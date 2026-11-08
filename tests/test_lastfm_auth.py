"""Regression tests for the Last.fm request-token polling flow.

The 5-minute wait must survive `auth.getSession` error code 14 ("token not
authorized yet") and must not swallow real configuration errors such as
code 13 ("invalid method signature").
"""

import os
import sys
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("LAST_FM_API", "dummy")
os.environ.setdefault("LAST_FM_API_SECRET", "dummy")

from ytscrobbler import lastpy, scrobbler
from ytscrobbler.errors import LastFmError

NOT_AUTHORIZED = '<lfm status="failed"><error code="14">This token has not been authorized</error></lfm>'
INVALID_SIGNATURE = '<lfm status="failed"><error code="13">Invalid method signature supplied</error></lfm>'
SESSION_KEY = "0123456789abcdef0123456789abcdef"
SESSION_OK = (
    '<lfm status="ok"><session><name>tester</name>'
    f"<key>{SESSION_KEY}</key></session></lfm>"
)


class TestGetSession(unittest.TestCase):
    @patch("ytscrobbler.lastpy.requests.post")
    def test_returns_error_payload_instead_of_raising(self, post):
        """Regression: the caller needs the code 14 payload to decide to retry."""
        post.return_value = Mock(text=NOT_AUTHORIZED)

        self.assertEqual(lastpy.get_session("request-token"), NOT_AUTHORIZED)


class TestGetTokenPolling(unittest.TestCase):
    def setUp(self):
        self.process = scrobbler.ImprovedProcess(Mock(), Mock(), datetime.now())

    @patch("ytscrobbler.scrobbler.dotenv.set_key")
    @patch("ytscrobbler.scrobbler.time.sleep")
    @patch("ytscrobbler.scrobbler.lastpy.get_session")
    @patch("ytscrobbler.scrobbler.lastpy.get_token", return_value="request-token")
    def test_retries_until_token_is_authorized(
        self, _get_token, get_session, sleep, set_key
    ):
        get_session.side_effect = [NOT_AUTHORIZED, NOT_AUTHORIZED, SESSION_OK]

        key = self.process.get_token()

        self.assertEqual(key, SESSION_KEY)
        self.assertEqual(get_session.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        sleep.assert_called_with(5)
        set_key.assert_called_once_with(".env", "LASTFM_SESSION", SESSION_KEY)

    @patch("ytscrobbler.scrobbler.dotenv.set_key")
    @patch("ytscrobbler.scrobbler.time.sleep")
    @patch("ytscrobbler.scrobbler.lastpy.get_session", return_value=INVALID_SIGNATURE)
    @patch("ytscrobbler.scrobbler.lastpy.get_token", return_value="request-token")
    def test_invalid_signature_fails_fast(
        self, _get_token, _get_session, sleep, _set_key
    ):
        with self.assertRaisesRegex(LastFmError, "Invalid method signature supplied"):
            self.process.get_token()

        sleep.assert_not_called()

    @patch("ytscrobbler.scrobbler.dotenv.set_key")
    @patch("ytscrobbler.scrobbler.time.sleep")
    @patch("ytscrobbler.scrobbler.lastpy.get_session", return_value=NOT_AUTHORIZED)
    @patch("ytscrobbler.scrobbler.lastpy.get_token", return_value="request-token")
    def test_gives_up_after_five_minutes(
        self, _get_token, get_session, sleep, _set_key
    ):
        with self.assertRaisesRegex(LastFmError, "Timed out after 5 minutes"):
            self.process.get_token()

        # 5 minutes at one request every 5 seconds.
        self.assertEqual(get_session.call_count, 60)
        self.assertEqual(sleep.call_count, 60)


if __name__ == "__main__":
    unittest.main()
