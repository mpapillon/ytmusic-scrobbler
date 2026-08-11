"""
Integration tests for ImprovedProcess.execute(): the full flow through a temp SQLite
DB, with network calls (YouTube Music history fetch, Last.fm scrobble) mocked out.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('LAST_FM_API', 'dummy')
os.environ.setdefault('LAST_FM_API_SECRET', 'dummy')
os.environ.setdefault('YTMUSIC_COOKIE', 'dummy')
os.environ.setdefault('LASTFM_SESSION', 'dummy')

import start_standalone
import scrobble_utils

SONG_1 = {'title': 'Song1', 'artist': 'Art1', 'album': 'Alb1', 'playedAt': 'Today'}
SONG_2 = {'title': 'Song2', 'artist': 'Art2', 'album': 'Alb2', 'playedAt': 'Today'}
SONG_3 = {'title': 'Song3', 'artist': 'Art3', 'album': 'Alb3', 'playedAt': 'Today'}


class ExecuteIntegrationTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self._orig_cwd = os.getcwd()
        os.chdir(self.tmpdir.name)  # ImprovedProcess hardcodes './data.db'

        self.history = []
        self.scrobbled = []

        patcher_history = patch.object(
            start_standalone, 'get_ytmusic_history_from_cookie', side_effect=lambda cookie: list(self.history)
        )
        patcher_is_today = patch.object(start_standalone, 'is_today_song', side_effect=lambda x: x == 'Today')

        def fake_scrobble_song(inner_self, song, session, timestamp):
            self.scrobbled.append((song['title'], timestamp))
            return True

        patcher_scrobble = patch.object(scrobble_utils.SmartScrobbler, 'scrobble_song', fake_scrobble_song)

        self.addCleanup(patcher_history.stop)
        self.addCleanup(patcher_is_today.stop)
        self.addCleanup(patcher_scrobble.stop)
        patcher_history.start()
        patcher_is_today.start()
        patcher_scrobble.start()

        self.addCleanup(os.chdir, self._orig_cwd)
        self.addCleanup(self.tmpdir.cleanup)

    def new_process(self, dry_run=False):
        return start_standalone.ImprovedProcess(dry_run=dry_run)

    def test_initial_run_is_calibration_only(self):
        self.history[:] = [SONG_1, SONG_2]
        process = self.new_process()

        process.execute()

        self.assertEqual(self.scrobbled, [])
        rows = process.conn.execute('SELECT track_name, array_position FROM scrobbles').fetchall()
        self.assertEqual(sorted(rows), [('Song1', 1), ('Song2', 2)])
        last_success_at = process.conn.execute('SELECT last_success_at FROM run_state').fetchone()[0]
        self.assertIsNotNone(last_success_at)

    def test_nominal_run_scrobbles_new_song_only(self):
        self.history[:] = [SONG_1, SONG_2]
        process = self.new_process()
        process.execute()  # calibration
        self.scrobbled.clear()

        self.history.insert(0, SONG_3)
        process.execute()

        self.assertEqual([title for title, _ in self.scrobbled], ['Song3'])

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
        rows = process.conn.execute('SELECT track_name FROM scrobbles').fetchall()
        self.assertEqual([r[0] for r in rows], ['Song1'])

        # Song2 is played again today
        self.history.insert(0, SONG_2)
        self.scrobbled.clear()
        process.execute()

        self.assertEqual([title for title, _ in self.scrobbled], ['Song2'])

    def test_multi_day_gap_recovery_is_treated_as_calibration(self):
        """Regression test: after a gap long enough that every previously tracked
        row is stale (e.g. days of a broken cookie), the recovery run must
        re-calibrate instead of scrobbling today's already-elapsed songs with
        guessed timestamps."""
        self.history[:] = [SONG_1, SONG_2]
        process = self.new_process()
        process.execute()  # calibration on "day 1"

        process.conn.execute("UPDATE run_state SET last_success_at = strftime('%s','now','-3 days')")
        process.conn.commit()

        # "Today" is now a different day - a completely different set of songs,
        # none of which match the stale rows from before the gap.
        self.history[:] = [SONG_3]
        process.execute()

        self.assertEqual(self.scrobbled, [])
        rows = process.conn.execute('SELECT track_name FROM scrobbles').fetchall()
        self.assertEqual([r[0] for r in rows], ['Song3'])

    def test_dry_run_never_writes_to_the_database(self):
        self.history[:] = [SONG_1, SONG_2]
        process = self.new_process(dry_run=True)

        before = process.conn.execute('SELECT * FROM scrobbles').fetchall()
        before_state = process.conn.execute('SELECT * FROM run_state').fetchall()
        process.execute()
        after = process.conn.execute('SELECT * FROM scrobbles').fetchall()
        after_state = process.conn.execute('SELECT * FROM run_state').fetchall()

        self.assertEqual(before, after)
        self.assertEqual(before_state, after_state)
        self.assertEqual(self.scrobbled, [])  # scrobble_song must never actually be called


if __name__ == '__main__':
    unittest.main()
