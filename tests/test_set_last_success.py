"""Tests for --set-last-success: manual anchor override for catch-up runs.

Covers the main() wiring (DB write, dry-run restraint, validation exit codes)
and the execute() consumption of the override (skips calibration-only mode).
"""
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('LAST_FM_API', 'dummy')
os.environ.setdefault('LAST_FM_API_SECRET', 'dummy')
os.environ.setdefault('LASTFM_SESSION', 'dummy')

import scrobble_utils
import scrobbler
from scrobble_utils import start_of_day
from store import Store


class TmpCwdTestCase(unittest.TestCase):
    """Real Store writes './data.db' - keep every test in its own tmpdir."""

    def setUp(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        self._orig_cwd = os.getcwd()
        self.addCleanup(os.chdir, self._orig_cwd)
        os.chdir(tmpdir.name)
        self.tmpdir = tmpdir


class TestMainSetLastSuccess(TmpCwdTestCase):
    """main() with --set-last-success, ImprovedProcess stubbed out."""

    def setUp(self):
        super().setUp()
        self.browser_path = os.path.join(self.tmpdir.name, 'browser.json')
        open(self.browser_path, 'w').close()

    def run_main(self, *extra_argv):
        mock_process = Mock()
        mock_process.execute.return_value = None
        with patch.object(scrobbler, 'BROWSER_JSON_PATH', self.browser_path), \
                patch.object(scrobbler, 'YTMusic'), \
                patch.object(scrobbler, 'ImprovedProcess', return_value=mock_process) as mock_cls, \
                patch.object(sys, 'argv', ['scrobbler.py', *extra_argv]):
            exit_code = scrobbler.main()
        return exit_code, mock_cls

    def read_anchor(self):
        store = Store()
        try:
            return store.get_last_success_at()
        finally:
            store.conn.close()

    def test_valid_value_is_written_and_passed_to_run(self):
        anchor = datetime.now().replace(microsecond=0) - timedelta(hours=5)
        expected = int(anchor.timestamp())

        exit_code, mock_cls = self.run_main('--set-last-success', anchor.isoformat())

        self.assertEqual(exit_code, 0)
        self.assertEqual(self.read_anchor(), expected)
        self.assertEqual(mock_cls.call_args.kwargs['anchor_override'], expected)

    def test_date_only_value_is_accepted(self):
        midnight = start_of_day(int(time.time()))
        local_midnight = datetime.fromtimestamp(midnight, tz=datetime.now().astimezone().tzinfo)

        exit_code, _ = self.run_main('--set-last-success', local_midnight.date().isoformat())

        self.assertEqual(exit_code, 0)
        self.assertEqual(self.read_anchor(), midnight)

    def test_future_value_is_rejected_without_writing(self):
        future = (datetime.now() + timedelta(days=1)).isoformat()

        exit_code, mock_cls = self.run_main('--set-last-success', future)

        self.assertEqual(exit_code, 78)
        self.assertIsNone(self.read_anchor())
        mock_cls.assert_not_called()

    def test_invalid_format_exits_78(self):
        exit_code, mock_cls = self.run_main('--set-last-success', 'not-a-date')

        self.assertEqual(exit_code, 78)
        mock_cls.assert_not_called()

    def test_invalid_to_datetime_format_exits_78(self):
        """--to-datetime used to die as 'Unexpected error' (70) on bad input."""
        exit_code, mock_cls = self.run_main('--to-datetime', 'not-a-date')

        self.assertEqual(exit_code, 78)
        mock_cls.assert_not_called()

    def test_dry_run_does_not_write_but_overrides_in_memory(self):
        anchor = datetime.now().replace(microsecond=0) - timedelta(hours=5)
        expected = int(anchor.timestamp())

        exit_code, mock_cls = self.run_main('--dry-run', '--set-last-success', anchor.isoformat())

        self.assertEqual(exit_code, 0)
        self.assertIsNone(self.read_anchor())
        self.assertEqual(mock_cls.call_args.kwargs['anchor_override'], expected)
        self.assertTrue(mock_cls.call_args.kwargs['dry_run'])

    def test_without_flag_anchor_is_untouched(self):
        exit_code, mock_cls = self.run_main()

        self.assertEqual(exit_code, 0)
        self.assertIsNone(self.read_anchor())
        self.assertIsNone(mock_cls.call_args.kwargs['anchor_override'])


class TestExecuteAnchorOverride(TmpCwdTestCase):
    """execute() must treat a same-day override as a real anchor: no
    calibration mode, backlog scrobbled, window starting at the override."""

    def setUp(self):
        super().setUp()
        self.history = []
        self.scrobbled = []
        self.ytmusic = Mock()
        self.ytmusic.get_history.side_effect = lambda: [dict(song) for song in self.history]

        patcher1 = patch.object(scrobbler, 'is_today_song', side_effect=lambda x: x == 'Today')

        def fake_scrobble_song(inner_self, song, session, timestamp):
            self.scrobbled.append((song.title, int(timestamp)))
            return True

        patcher2 = patch.object(scrobble_utils.SmartScrobbler, 'scrobble_song', fake_scrobble_song)
        self.addCleanup(patcher1.stop)
        self.addCleanup(patcher2.stop)
        patcher1.start()
        patcher2.start()

    def new_process(self, anchor_override=None, dry_run=False):
        store = Store()
        store.migrate()
        return scrobbler.ImprovedProcess(
            store, self.ytmusic, to_datetime=datetime.now(),
            dry_run=dry_run, anchor_override=anchor_override,
        )

    def test_override_skips_calibration_and_scrobbles_the_backlog(self):
        """Empty DB (never ran, or ran last night): the normal first run would
        only calibrate. With an override anchored in the past, the backlog
        must actually scrobble."""
        self.history[:] = [{'title': 'Song1', 'artists': [{'name': 'Art1'}],
                            'album': {'name': 'Alb1'}, 'videoType': 'MUSIC_VIDEO_TYPE_ATV', 'played': 'Today'},
                           {'title': 'Song2', 'artists': [{'name': 'Art2'}],
                            'album': {'name': 'Alb2'}, 'videoType': 'MUSIC_VIDEO_TYPE_ATV', 'played': 'Today'}]
        anchor = start_of_day(int(time.time()))
        process = self.new_process(anchor_override=anchor)

        result = process.execute()

        self.assertIsNone(result)
        self.assertEqual(sorted(t for t, _ in self.scrobbled), ['Song1', 'Song2'])
        # The oldest scrobble lands exactly at the override - proof the window
        # started there instead of falling back to calibration/midnight guess.
        self.assertEqual(min(ts for _, ts in self.scrobbled), anchor)
        # A completed run re-anchors to now, as usual.
        last_success_at = process.store.conn.execute(
            'SELECT last_success_at FROM run_state').fetchone()[0]
        self.assertGreater(last_success_at, anchor)

    def test_dry_run_with_override_writes_nothing(self):
        self.history[:] = [{'title': 'Song1', 'artists': [{'name': 'Art1'}],
                            'album': {'name': 'Alb1'}, 'videoType': 'MUSIC_VIDEO_TYPE_ATV', 'played': 'Today'}]
        process = self.new_process(anchor_override=start_of_day(int(time.time())), dry_run=True)

        process.execute()

        self.assertEqual(self.scrobbled, [])
        rows = process.store.conn.execute('SELECT COUNT(*) FROM scrobbles').fetchone()[0]
        state = process.store.conn.execute('SELECT last_success_at FROM run_state').fetchone()[0]
        self.assertEqual(rows, 0)
        self.assertIsNone(state)


if __name__ == '__main__':
    unittest.main()
