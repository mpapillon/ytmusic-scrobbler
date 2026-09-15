"""Unit tests for update_browser_json(): the interactive browser.json refresh flow.

scrobbler.setup is patched with a writer that mimics ytmusicapi.setup's
file output, so the SAPISIDHASH validation step is exercised for real.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ytmusicapi.exceptions import YTMusicUserError

import scrobbler
from errors import ConfigError

VALID_HEADERS = {
    'cookie': 'SID=fake',
    'x-goog-authuser': '0',
    'authorization': 'SAPISIDHASH 1788593192_abcdef_u',
}
NO_AUTH_HEADERS = {'cookie': 'SID=fake', 'x-goog-authuser': '0'}


def setup_writer(*header_sets):
    """side_effect for setup(): writes each header set on successive calls."""
    def write(filepath):
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(header_sets[min(len(header_sets) - 1, write.calls)].copy(), f)
            write.calls += 1
    write.calls = 0
    return write


class TestUpdateBrowserJson(unittest.TestCase):
    def setUp(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        self.path = os.path.join(tmpdir.name, 'browser.json')

    def run_update(self, side_effect):
        with patch.object(scrobbler, 'setup', side_effect=side_effect):
            scrobbler.update_browser_json(self.path)

    def test_valid_headers_are_saved(self):
        self.run_update(setup_writer(VALID_HEADERS))
        with open(self.path, encoding='utf-8') as f:
            self.assertIn('SAPISIDHASH', json.load(f)['authorization'])

    def test_headers_without_authorization_are_rejected(self):
        """A file without SAPISIDHASH would load as OAuth JSON in YTMusic() -
        it must be treated as a failed attempt, not a successful update."""
        with patch.object(scrobbler, 'setup', side_effect=setup_writer(NO_AUTH_HEADERS)) as mock_setup, \
                self.assertRaises(ConfigError):
            scrobbler.update_browser_json(self.path)
        self.assertEqual(mock_setup.call_count, scrobbler.MAX_BROWSER_SETUP_ATTEMPTS)

    def test_bad_paste_then_valid_paste_succeeds(self):
        self.run_update(setup_writer(NO_AUTH_HEADERS, VALID_HEADERS))
        with open(self.path, encoding='utf-8') as f:
            self.assertIn('SAPISIDHASH', json.load(f)['authorization'])

    def test_parse_error_from_setup_is_retried(self):
        def flaky(filepath):
            if not flaky.retried:
                flaky.retried = True
                raise YTMusicUserError('Error parsing your input, please try again')
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(VALID_HEADERS, f)
        flaky.retried = False

        self.run_update(flaky)
        with open(self.path, encoding='utf-8') as f:
            self.assertIn('SAPISIDHASH', json.load(f)['authorization'])

    def test_keyboard_interrupt_propagates(self):
        with self.assertRaises(KeyboardInterrupt):
            self.run_update(KeyboardInterrupt)

    def test_login_flag_skips_scrobbling(self):
        """--login must exit after updating, never reaching the scrobble flow."""
        with patch.object(scrobbler, 'setup', side_effect=setup_writer(VALID_HEADERS)), \
             patch.object(scrobbler, 'BROWSER_JSON_PATH', self.path), \
             patch.object(sys, 'argv', ['scrobbler.py', '--login']), \
             patch.object(scrobbler, 'YTMusic') as mock_ytmusic, \
             patch.object(scrobbler, 'Store') as mock_store:
            exit_code = scrobbler.main()

        self.assertEqual(exit_code, 0)
        mock_ytmusic.assert_not_called()
        mock_store.assert_not_called()


class TestMainCredentialLoading(unittest.TestCase):
    """main() must fail with actionable guidance (78) instead of an opaque
    'Unexpected error' when browser.json is missing or unusable."""

    def setUp(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        self.path = os.path.join(tmpdir.name, 'browser.json')
        # main() now creates the Store (./data.db) before loading credentials
        orig_cwd = os.getcwd()
        self.addCleanup(os.chdir, orig_cwd)
        os.chdir(tmpdir.name)

    def run_main(self):
        with patch.object(scrobbler, 'BROWSER_JSON_PATH', self.path), \
             patch.object(sys, 'argv', ['scrobbler.py']):
            return scrobbler.main()

    def test_missing_file_prompts_login(self):
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(self.run_main(), 78)

    def test_invalid_json_prompts_login(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write('this is not json')
        self.assertEqual(self.run_main(), 78)

    def test_headers_without_authorization_prompts_login(self):
        """A file that would be misread as OAuth JSON must also be caught here
        rather than at scrobble time."""
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(NO_AUTH_HEADERS, f)
        self.assertEqual(self.run_main(), 78)


if __name__ == '__main__':
    unittest.main()
