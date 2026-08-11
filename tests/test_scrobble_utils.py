"""Unit tests for scrobble_utils.py: position tracking, timestamp windowing/distribution."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrobble_utils import PositionTracker, ScrobbleTimestampCalculator, compute_scrobble_window

SONG_A = {'title': 'Song A', 'artist': 'Artist A', 'album': 'Album A'}
SONG_B = {'title': 'Song B', 'artist': 'Artist B', 'album': 'Album B'}
SONG_C = {'title': 'Song C', 'artist': 'Artist C', 'album': 'Album C'}


def db_entry(song, array_position):
    return {**song, 'array_position': array_position, 'max_array_position': array_position}


class TestDetectSongsToScrobbleCalibration(unittest.TestCase):
    """Initial case: the very first run ever (empty DB) only calibrates."""

    def test_calibration_run_scrobbles_nothing(self):
        today = [SONG_A, SONG_B, SONG_C]
        result = PositionTracker.detect_songs_to_scrobble(today, [], is_first_time=True)

        self.assertEqual(len(result), 3)
        for i, item in enumerate(result):
            self.assertEqual(item['reason'], 'calibration')
            self.assertFalse(item['should_scrobble'])
            self.assertEqual(item['position'], i + 1)


class TestDetectSongsToScrobbleNominal(unittest.TestCase):
    """Nominal case: detecting new plays / replays via title+artist+album matching."""

    def test_new_song_is_scrobbled(self):
        today = [SONG_A]
        result = PositionTracker.detect_songs_to_scrobble(today, [], is_first_time=False)

        self.assertEqual(result[0]['reason'], 'new_song')
        self.assertTrue(result[0]['should_scrobble'])

    def test_song_moved_to_better_position_is_reproduction(self):
        # SONG_B was previously seen at position 3, now it's back at position 1
        today = [SONG_B]
        database_songs = [db_entry(SONG_B, 3)]
        result = PositionTracker.detect_songs_to_scrobble(today, database_songs, is_first_time=False)

        self.assertEqual(result[0]['reason'], 'reproduction')
        self.assertTrue(result[0]['should_scrobble'])
        self.assertEqual(result[0]['previous_position'], 3)

    def test_song_same_or_worse_position_is_not_scrobbled(self):
        # SONG_B was at position 1, still at position 1 - no new play
        today = [SONG_A, SONG_B]
        database_songs = [db_entry(SONG_B, 2)]
        result = PositionTracker.detect_songs_to_scrobble(today, database_songs, is_first_time=False)

        b_item = next(r for r in result if r['song'] == SONG_B)
        self.assertEqual(b_item['reason'], 'position_update')
        self.assertFalse(b_item['should_scrobble'])

    def test_same_title_different_artist_is_not_a_match(self):
        same_title_other_artist = {'title': 'Song A', 'artist': 'Someone Else', 'album': 'Other Album'}
        today = [same_title_other_artist]
        database_songs = [db_entry(SONG_A, 1)]
        result = PositionTracker.detect_songs_to_scrobble(today, database_songs, is_first_time=False)

        # Must not be matched against SONG_A's stale entry - it's a different song
        self.assertEqual(result[0]['reason'], 'new_song')
        self.assertTrue(result[0]['should_scrobble'])

    def test_same_title_and_artist_different_album_is_not_a_match(self):
        same_title_artist_other_album = {'title': 'Song A', 'artist': 'Artist A', 'album': 'Live Version'}
        today = [same_title_artist_other_album]
        database_songs = [db_entry(SONG_A, 1)]
        result = PositionTracker.detect_songs_to_scrobble(today, database_songs, is_first_time=False)

        self.assertEqual(result[0]['reason'], 'new_song')
        self.assertTrue(result[0]['should_scrobble'])


class TestScrobbleTimestampCalculatorSingleSong(unittest.TestCase):
    def test_single_song_is_placed_30s_before_window_end(self):
        window_end = 1_700_000_000
        ts = ScrobbleTimestampCalculator.calculate_scrobble_timestamp(0, 1, window_end - 3600, window_end)
        self.assertEqual(ts, str(window_end - 30))


class TestScrobbleTimestampCalculatorFewHoursCatchUp(unittest.TestCase):
    """Few-hours catch-up case: a wide window, but still within the same day."""

    def test_distribution_spans_the_full_window(self):
        window_end = 1_700_000_000
        window_start = window_end - 5 * 3600  # 5h gap
        total = 5

        offsets = [
            window_end - int(ScrobbleTimestampCalculator.calculate_scrobble_timestamp(i, total, window_start, window_end))
            for i in range(total)
        ]

        # Most recent song (index 0) sticks near "now"
        self.assertEqual(offsets[0], 30)
        # Oldest song in the batch (last index) reaches exactly window_start
        self.assertEqual(offsets[-1], window_end - window_start)
        # Offsets strictly increase (older songs get pushed further back)
        self.assertEqual(offsets, sorted(offsets))
        # Nothing ever goes past window_start
        self.assertTrue(all(o <= window_end - window_start for o in offsets))


class TestScrobbleTimestampCalculatorEdgeCases(unittest.TestCase):
    def test_zero_span_window_places_everything_at_window_end(self):
        now = 1_700_000_000
        for i in range(3):
            ts = ScrobbleTimestampCalculator.calculate_scrobble_timestamp(i, 3, now, now)
            self.assertEqual(ts, str(now))

    def test_min_offset_guard_on_very_short_window(self):
        # 10s window - the usual 30s "minimum offset" must not exceed the span
        window_end = 1_700_000_000
        window_start = window_end - 10
        ts = ScrobbleTimestampCalculator.calculate_scrobble_timestamp(0, 2, window_start, window_end)
        self.assertEqual(ts, str(window_start))  # clamped to the span, not -30s past window_start

    def test_negative_span_from_clock_skew_does_not_crash(self):
        # last_success_at in the future relative to window_end (clock skew) -> window_start > window_end
        window_end = 1_700_000_000
        window_start = window_end + 3600
        ts = ScrobbleTimestampCalculator.calculate_scrobble_timestamp(0, 2, window_start, window_end)
        self.assertEqual(ts, str(window_end))  # span clamped to 0, no negative offset


class TestComputeScrobbleWindow(unittest.TestCase):
    def setUp(self):
        # Fixed "now": 2026-08-11 15:00:00 local time
        import datetime
        self.now_dt = datetime.datetime(2026, 8, 11, 15, 0, 0)
        self.now = int(self.now_dt.timestamp())
        self.start_of_today = int(self.now_dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())

    def test_no_prior_run_falls_back_to_start_of_today(self):
        window_start, window_end = compute_scrobble_window(None, self.now)
        self.assertEqual(window_start, self.start_of_today)
        self.assertEqual(window_end, self.now)

    def test_nominal_recent_run_uses_last_success(self):
        last_success_at = self.now - 900  # 15 minutes ago, same day
        window_start, _ = compute_scrobble_window(last_success_at, self.now)
        self.assertEqual(window_start, last_success_at)

    def test_few_hours_catch_up_uses_last_success(self):
        last_success_at = self.now - 5 * 3600  # 5h ago, still same day
        window_start, _ = compute_scrobble_window(last_success_at, self.now)
        self.assertEqual(window_start, last_success_at)

    def test_multi_day_catch_up_clamps_to_start_of_today(self):
        """Multi-day catch-up case: the gap must never reach back before today."""
        last_success_at = self.now - 3 * 86400  # 3 days ago
        window_start, _ = compute_scrobble_window(last_success_at, self.now)
        self.assertEqual(window_start, self.start_of_today)
        self.assertNotEqual(window_start, last_success_at)

    def test_clock_skew_future_last_success_is_not_clamped_down(self):
        # max() picks the later timestamp even if it's ahead of "now" - the
        # downstream timestamp calculator is what guards against a negative span
        last_success_at = self.now + 3600
        window_start, _ = compute_scrobble_window(last_success_at, self.now)
        self.assertEqual(window_start, last_success_at)


if __name__ == '__main__':
    unittest.main()
