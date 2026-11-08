"""
Integration tests for ImprovedProcess.execute(): the full flow through a temp
JSON DataStore, with network calls (YouTube Music history fetch, Last.fm
scrobble) mocked out.
"""

import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("LAST_FM_API", "dummy")
os.environ.setdefault("LAST_FM_API_SECRET", "dummy")
os.environ.setdefault("LASTFM_SESSION", "dummy")

from ytscrobbler import DATA_JSON_PATH, scrobble_utils, scrobbler
from ytscrobbler.errors import FailureType
from ytscrobbler.store import DataStore


def api_song(
    video_id: str,
    title: str,
    artist: str,
    album: str,
    video_type: str = "MUSIC_VIDEO_TYPE_ATV",
) -> dict:
    """Song in the raw format returned by ytmusicapi get_history()."""
    return {
        "videoId": video_id,
        "title": title,
        "artists": [{"name": artist}],
        "album": {"name": album},
        "videoType": video_type,
        "played": "Today",
    }


SONG_1 = api_song("vid-1", "Song1", "Art1", "Alb1")
SONG_2 = api_song("vid-2", "Song2", "Art2", "Alb2")
SONG_3 = api_song("vid-3", "Song3", "Art3", "Alb3")


class ExecuteIntegrationTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self._orig_cwd = os.getcwd()
        os.chdir(self.tmpdir.name)  # DataStore lives at './data.json'

        self.history = []
        self.scrobbled = []

        self.ytmusic = Mock()
        self.ytmusic.get_history.side_effect = lambda: [
            dict(song) for song in self.history
        ]

        def fake_scrobble_song(inner_self, song, session, timestamp):
            self.scrobbled.append((song.title, timestamp))
            return True

        patcher_scrobble = patch.object(
            scrobble_utils.SmartScrobbler, "scrobble_song", fake_scrobble_song
        )

        self.addCleanup(patcher_scrobble.stop)
        patcher_scrobble.start()

        self.addCleanup(os.chdir, self._orig_cwd)
        self.addCleanup(self.tmpdir.cleanup)

    def new_process(self, dry_run=False, to_datetime=None):
        store = DataStore(Path(DATA_JSON_PATH))
        return scrobbler.ImprovedProcess(
            store,
            self.ytmusic,
            to_datetime=to_datetime or datetime.now(),
            dry_run=dry_run,
        )

    def stored(self, process):
        """Scrobbles held by the process' store, as (title, array_position)."""
        return sorted(
            (s.track.title, s.array_position or 0)
            for s in process._store.get_scrobbles()
        )

    def stored_titles(self, process):
        return [title for title, _ in self.stored(process)]

    def test_initial_run_is_calibration_only(self):
        self.history[:] = [SONG_1, SONG_2]
        process = self.new_process()

        process.execute()

        self.assertEqual(self.scrobbled, [])
        self.assertEqual(self.stored(process), [("Song1", 1), ("Song2", 2)])
        # The run anchor is persisted to disk, not just kept in memory.
        self.assertIsNotNone(DataStore(Path(DATA_JSON_PATH)).last_success_at)

    def test_nominal_run_scrobbles_new_song_only(self):
        self.history[:] = [SONG_1, SONG_2]
        process = self.new_process()
        process.execute()  # calibration
        self.scrobbled.clear()

        self.history.insert(0, SONG_3)
        process.execute()

        self.assertEqual([title for title, _ in self.scrobbled], ["Song3"])

    def test_no_new_songs_scrobbles_nothing(self):
        self.history[:] = [SONG_1, SONG_2]
        process = self.new_process()
        process.execute()  # calibration
        self.scrobbled.clear()

        process.execute()  # same songs, nothing changed

        self.assertEqual(self.scrobbled, [])

    def test_replayed_song_after_removal_is_scrobbled_as_new(self):
        """Regression test: a song dropped from today's list and later replayed the
        same day must be detected as a new play, not silently ignored because of a
        stale in-memory position from before the cleanup step."""
        self.history[:] = [SONG_1, SONG_2]
        process = self.new_process()
        process.execute()  # calibration

        # Song2 drops out of today's history (e.g. transient scraping gap)
        self.history[:] = [SONG_1]
        process.execute()
        self.assertEqual(self.stored_titles(process), ["Song1"])

        # Song2 is played again today
        self.history.insert(0, SONG_2)
        self.scrobbled.clear()
        process.execute()

        self.assertEqual([title for title, _ in self.scrobbled], ["Song2"])

    def test_non_music_history_entries_are_ignored(self):
        """Podcasts/non-music entries and ' - Topic' channels must never be
        recorded or scrobbled."""
        podcast = {
            "videoId": "vid-podcast",
            "title": "Some Podcast Episode",
            "artists": [{"name": "Podcaster"}],
            "played": "Today",
        }
        topic_channel = api_song(
            "vid-topic", "Topic Song", "Some Artist - Topic", "Album"
        )
        self.history[:] = [podcast, topic_channel, SONG_1]
        process = self.new_process()

        process.execute()

        self.assertEqual(self.scrobbled, [])
        self.assertEqual(self.stored(process), [("Song1", 1)])

    def test_gap_crossing_a_day_boundary_is_treated_as_calibration(self):
        """Regression test: once last_success_at falls on a previous calendar day,
        there's no same-day anchor left to place guessed timestamps against. So the
        run must re-calibrate instead of guessing, regardless of whether the gap
        was one day or several."""
        self.history[:] = [SONG_1, SONG_2]
        process = self.new_process()
        process.execute()  # calibration on "day 1"

        process._store.last_success_at = int(time.time()) - 3 * 86400
        process._store.save()

        # "Today" is now a different day - a completely different set of songs,
        # none of which match the stale rows from before the gap.
        self.history[:] = [SONG_3]
        process.execute()

        self.assertEqual(self.scrobbled, [])
        self.assertEqual(self.stored_titles(process), ["Song3"])

    def test_same_day_gap_with_wiped_db_still_scrobbles_normally(self):
        """Regression test: a run where nothing has been played yet today exits early
        without cleaning up yesterday's stale rows. The next run - the one that
        actually has today's songs - wipes those stale rows too, emptying
        database_scrobbles, but last_success_at is still from earlier today (a real,
        recent anchor), so this must scrobble normally rather than mistake the wiped
        store for a fresh install and silently calibrate instead."""
        # Pin "now" to noon so the same-day assumption can't break at midnight.
        noon = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
        self.history[:] = [SONG_1, SONG_2]
        process = self.new_process(to_datetime=noon)
        process.execute()  # calibration on "day 1"

        # Next run: nothing played yet today, exits early before cleanup runs.
        self.history[:] = []
        process.execute()

        # Later the same day: a real play happens, in a today's-list that shares
        # nothing with the still-present "day 1" rows.
        self.history[:] = [SONG_3]
        process.execute()

        self.assertEqual([title for title, _ in self.scrobbled], ["Song3"])

    def test_same_day_gap_anchors_timestamp_to_last_success_not_midnight(self):
        """Regression test for the actual timestamp, not just whether a scrobble
        happens: with a same-day last_success_at, the guessed window must start
        there - not fall back to midnight, which would spread a short real gap
        across the whole elapsed day instead of the real, much shorter one."""
        # Anchor the run to a fixed noon: a wall-clock "two hours ago" falls on
        # *yesterday* between 00:00 and 02:00, which would legitimately put the
        # next run back into first-time calibration mode and test nothing.
        noon = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
        self.history[:] = [SONG_1]
        process = self.new_process(to_datetime=noon)
        process.execute()  # calibration on "day 1"

        two_hours_ago = int(noon.timestamp()) - 2 * 3600
        process._store.last_success_at = two_hours_ago
        process._store.save()

        # Store wiped by an intervening empty-today run, same as the reported bug -
        # but last_success_at is still a real, recent, same-day anchor.
        self.history[:] = []
        process.execute()

        self.history[:] = [SONG_3, SONG_2]  # two new plays today
        process.execute()

        self.assertEqual([title for title, _ in self.scrobbled], ["Song3", "Song2"])
        timestamps = [int(ts) for _, ts in self.scrobbled]
        # The older of the two lands exactly at last_success_at (window_start) - a
        # midnight fallback would place it several hours earlier than that instead.
        self.assertEqual(min(timestamps), two_hours_ago)
        self.assertGreater(max(timestamps), min(timestamps))

    def test_dry_run_never_writes_to_the_database(self):
        self.history[:] = [SONG_1, SONG_2]
        process = self.new_process(dry_run=True)
        data_path = Path(DATA_JSON_PATH)

        self.assertFalse(data_path.exists())
        process.execute()

        self.assertFalse(data_path.exists())
        self.assertEqual(process._store.get_scrobbles(), [])
        self.assertIsNone(process._store.last_success_at)
        self.assertEqual(self.scrobbled, [])

    def test_expired_auth_returning_none_history_is_auth_failure(self):
        """Regression test: with stale browser.json credentials, get_history()
        returns None without raising. The run must report an AUTH failure,
        scrobble nothing, and not update last_success_at."""
        self.ytmusic.get_history.side_effect = None
        self.ytmusic.get_history.return_value = None
        process = self.new_process()

        failure = process.execute()

        self.assertEqual(failure, FailureType.AUTH)
        self.assertEqual(self.scrobbled, [])
        self.assertIsNone(process._store.last_success_at)


if __name__ == "__main__":
    unittest.main()
