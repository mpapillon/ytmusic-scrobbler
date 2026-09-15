#!/usr/bin/env python3
"""
YouTube Music Last.fm Scrobbler
- YouTube Music history via ytmusicapi (browser.json auth, see README)
- Multilingual date detection (50+ languages)
- Smart timestamp distribution (logarithmic, bounded to the real elapsed time since the last successful run)
- Better position tracking and re-reproduction detection
- Robust error handling and categorization
"""
import argparse
import json
import os
import sys
import time
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import final

from dotenv import find_dotenv, load_dotenv, set_key
from ytmusicapi import YTMusic, setup
from ytmusicapi.exceptions import YTMusicUserError

import lastpy
from date_detection import (
    get_detected_languages,
    get_unknown_date_values,
    is_today_song,
)
from errors import ConfigError, FailureType, LastFmError
from scrobble_utils import (
    HistorySong,
    PositionTracker,
    SmartScrobbler,
    compute_scrobble_window,
    log_error,
    log_info,
    log_warning,
    start_of_day,
)
from store import Store

load_dotenv(find_dotenv(usecwd=True))

BROWSER_JSON_PATH = "browser.json"
MAX_BROWSER_SETUP_ATTEMPTS = 3


def _format_ts(ts: int | None) -> str:
    if ts is None:
        return "never"
    local = datetime.fromtimestamp(ts, tz=datetime.now().astimezone().tzinfo)
    return local.strftime("%Y-%m-%d %H:%M:%S")


def _parse_local_iso(value: str) -> datetime:
    """Parse an ISO 8601 date/datetime as local time (tz info dropped). Raises ConfigError on bad format."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ConfigError(f"Invalid ISO 8601 datetime: {value!r}") from None
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


@final
class ImprovedProcess:
    def __init__(self, store: Store, ytmusic: YTMusic, to_datetime: datetime, dry_run: bool = False,
                 anchor_override: int | None = None):
        self.ytmusic = ytmusic
        self.to_datetime = to_datetime
        self.dry_run = dry_run
        self.anchor_override = anchor_override
        self.api_key = os.environ.get('LAST_FM_API')
        self.api_secret = os.environ.get('LAST_FM_API_SECRET')
        if not self.api_key or not self.api_secret:
            raise ConfigError("Missing LAST_FM_API or LAST_FM_API_SECRET environment variables")

        try:
            self.session = os.environ['LASTFM_SESSION']
        except KeyError:
            self.session = None

        # Initialize smart scrobbler
        self.scrobbler = SmartScrobbler(self.api_key, self.api_secret)
        self.position_tracker = PositionTracker()
        self.store = store

    def get_token(self) -> str:
        auth_token = lastpy.get_token()
        auth_url = f"https://www.last.fm/api/auth/?api_key={self.api_key}&token={auth_token}"

        poll_interval_seconds = 5
        timeout_minutes = 5
        max_attempts = timeout_minutes * 60 // poll_interval_seconds

        webbrowser.open(auth_url)
        print(
            "\nLast.fm authorization required. Open this URL and approve access:\n"
            f"\n    {auth_url}\n\n"
            f"Waiting up to {timeout_minutes} minutes for approval...\n"
        )

        for _ in range(max_attempts):
            xml_response = lastpy.get_session(auth_token)
            root = ET.fromstring(xml_response)
            if (session_key := root.find('session/key')) is not None and session_key.text:
                set_key('.env', 'LASTFM_SESSION', session_key.text)
                log_info("Last.fm authorization successful.")
                return session_key.text

            error = root.find("error")
            if error is not None and error.attrib.get("code") == "14":
                # 14 : This token has not been authorized
                time.sleep(poll_interval_seconds)
                continue

            message = error.text.strip() if error is not None and error.text else xml_response
            raise LastFmError(f"Last.fm rejected the authorization token: {message}")

        raise LastFmError(
            f"Timed out after {timeout_minutes} minutes waiting for Last.fm authorization. "
            f"Open {auth_url} and approve access, then rerun the script."
        )

    def handle_authentication_error(self, error: Exception | None = None) -> None:
        """Log an authentication failure with guidance for refreshing credentials"""
        if error is not None:
            log_error(f"YouTube Music authentication failed: {error}")
        else:
            log_error("YouTube Music authentication failed: the history request returned no data.")
        log_error("Your YouTube Music credentials appear to be expired or invalid.")
        log_error("Run this script with --login and paste the request headers of a signed-in")
        log_error("music.youtube.com 'browse' request when prompted.")

    def execute(self) -> FailureType | None:
        """Run the full fetch/filter/scrobble flow. Returns None on success, or the
        FailureType that ended the run early."""
        if self.dry_run:
            log_info("Dry run mode: no songs will actually be scrobbled to Last.fm.\n")

        last_success_at = self.anchor_override if self.anchor_override is not None \
            else self.store.get_last_success_at()
        if self.anchor_override is not None and self.dry_run:
            log_info(f"[DRY RUN] Using last_success_at override: {_format_ts(self.anchor_override)}")

        if not self.session:
            try:
                self.session = self.get_token()
            except Exception as e:
                failure_type = self.scrobbler.categorize_error(e)
                log_error(f"Failed to authenticate with Last.fm: {e} ({failure_type.value})")
                return failure_type

        log_info("Fetching YouTube Music history...")
        try:
            raw_history = self.ytmusic.get_history()
        except Exception as error:
            failure_type = self.scrobbler.categorize_error(error)

            if failure_type == FailureType.AUTH:
                self.handle_authentication_error(error)
            else:
                log_error(f"Failed to fetch history: {error} ({failure_type.value})")
            return failure_type

        if raw_history is None:
            # Expired browser.json credentials: the endpoint answers with a
            # sign-in page instead of history, so get_history() returns None
            # without raising. Treat it as an auth failure.
            self.handle_authentication_error()
            return FailureType.AUTH

        history = [s for s in map(HistorySong.from_api_item, raw_history) if s is not None]
        log_info(f"Retrieved {len(history)} songs from history")

        print()
        log_info("Filtering songs played today...")
        today_songs = [song for song in history if is_today_song(song.played)]

        # Log unknown date values for future expansion
        played_values = [song.played for song in history]
        unknown_values = get_unknown_date_values(played_values)
        if unknown_values:
            log_warning(f"Unknown date formats detected: {', '.join(unknown_values)} "
                        f"(please report these to the developer)")

        # Log detected languages
        detected_languages = get_detected_languages(played_values)
        if detected_languages:
            log_info(f"Detected languages in today's songs: {', '.join(detected_languages)}")

        log_info(f"Found {len(today_songs)} songs played today")

        if len(today_songs) == 0:
            log_info("Nothing to scrobble.")
            return None

        print()

        database_scrobbles = self.store.get_scrobbles()

        # Clean up database: remove songs not in today's history
        if database_scrobbles:
            scrobbles_to_delete: list[int] = []
            for db_song in database_scrobbles:
                found = False
                for today_song in today_songs:
                    if (today_song.title == db_song.track_name and
                        today_song.artist == db_song.artist_name and
                        today_song.album == db_song.album_name):
                        found = True
                        break

                if not found:
                    scrobbles_to_delete.append(db_song.id)

            if scrobbles_to_delete:
                # Drop these from the in-memory list too, not just the DB table -
                # otherwise a song that legitimately replays later in the day still
                # matches its stale in-memory entry and gets wrongly treated as
                # "already known" instead of a new play.
                database_scrobbles = [s for s in database_scrobbles if s.id not in scrobbles_to_delete]

                if self.dry_run:
                    log_info(f"[DRY RUN] Would remove {len(scrobbles_to_delete)} songs no longer in today's history")
                else:
                    log_info(f"Removing {len(scrobbles_to_delete)} songs no longer in today's history")
                    with self.store.transaction():
                        self.store.delete_scrobbles(scrobbles_to_delete)

        now = int(self.to_datetime.timestamp())
        is_first_time = last_success_at is None or last_success_at < start_of_day(now)

        songs_to_process = self.position_tracker.detect_songs_to_scrobble(
            today_songs, database_scrobbles, is_first_time
        )

        # Count how many will actually be scrobbled
        songs_to_scrobble = [s for s in songs_to_process if s['should_scrobble']]
        total_to_scrobble = len(songs_to_scrobble)

        if is_first_time:
            log_info(f"Calibration run: recording {len(today_songs)} songs as a baseline, nothing scrobbled. "
                     f"Future runs will scrobble new plays going forward.")

        log_info(f"Processing {len(songs_to_process)} songs ({total_to_scrobble} will be scrobbled)")

        window_start, window_end = compute_scrobble_window(last_success_at, now)

        songs_scrobbled = 0
        scrobble_position = 0
        had_fatal_error = False

        for item in songs_to_process:
            song = item['song']
            position = item['position']
            should_scrobble = item['should_scrobble']
            reason = item['reason']

            try:
                if self.dry_run:
                    if should_scrobble:
                        action = "NEW" if reason == "new_song" else "RE-SCROBBLE"
                        songs_scrobbled += 1
                        log_info(f"[DRY RUN] Would scrobble ({action}): \"{song.title}\" by {song.artist}")
                        scrobble_position += 1
                    continue

                with self.store.transaction():
                    existing_scrobble = self.store.find_scrobble(
                        song.title, song.artist, song.album
                    )

                    if existing_scrobble:
                        new_max = max(existing_scrobble.max_array_position or position, position)
                        self.store.update_scrobble_position(existing_scrobble.id, position, new_max)
                    else:
                        self.store.insert_scrobble(
                            song.title, song.artist, song.album, position, position
                        )

                    if should_scrobble:
                        timestamp = self.scrobbler.calculate_timestamp(
                            scrobble_position,
                            total_to_scrobble,
                            window_start,
                            window_end
                        )
                        action = "NEW" if reason == "new_song" else "RE-SCROBBLE"

                        success = self.scrobbler.scrobble_song(song, self.session, timestamp)

                        if success:
                            songs_scrobbled += 1
                            log_info(f"{action}: \"{song.title}\" by {song.artist}")
                            scrobble_position += 1
                        else:
                            log_info(f"FAILED: \"{song.title}\" by {song.artist} (Last.fm rejected)")

            except Exception as error:
                failure_type = self.scrobbler.categorize_error(error)
                log_error(f'Failed to process "{song.title}" by {song.artist}: {error} ({failure_type.value})')

                # Continue processing other songs unless it's an auth error
                if failure_type == FailureType.AUTH:
                    had_fatal_error = True
                    break

        if not self.dry_run and not had_fatal_error:
            with self.store.transaction():
                self.store.update_last_success_at(now)

        print()
        if self.dry_run:
            log_info(f"Run complete: {len(today_songs)} today, {songs_scrobbled} would be scrobbled")
        else:
            log_info(f"Run complete: {len(today_songs)} today, {songs_scrobbled} scrobbled, "
                     f"{len(songs_to_process)} processed")

        if had_fatal_error:
            return FailureType.AUTH
        return None

def _check_browser_json(filepath: str) -> None:
    with open(filepath, encoding='utf-8') as f:
        headers = json.load(f)
    if 'SAPISIDHASH' not in headers.get('authorization', ''):
        raise YTMusicUserError(
            "browser.json has no valid 'authorization' header (SAPISIDHASH). "
            "Copy the request headers of a 'browse' request while signed in, "
            "making sure they include 'authorization'."
        )


def update_browser_json(filepath: str | None = None) -> None:
    if filepath is None:
        filepath = BROWSER_JSON_PATH
    log_info("Refresh YouTube Music credentials. To copy the request headers:")
    log_info("1. Go to https://music.youtube.com and sign in")
    log_info("2. Open Developer Tools (F12) -> Network tab, refresh the page")
    log_info("3. Select any 'browse' request to music.youtube.com")
    log_info("4. In 'Request Headers', select all and copy (must include 'cookie',")
    log_info("   'authorization' and 'x-goog-authuser')\n")

    for attempt in range(1, MAX_BROWSER_SETUP_ATTEMPTS + 1):
        try:
            setup(filepath=filepath)
            _check_browser_json(filepath)
        except (OSError, ValueError, YTMusicUserError) as e:
            remaining = MAX_BROWSER_SETUP_ATTEMPTS - attempt
            log_error(str(e))
            if remaining <= 0:
                raise ConfigError(f"Could not update {filepath} after {MAX_BROWSER_SETUP_ATTEMPTS} attempts")
            log_error(f"{remaining} attempt(s) left.")
            continue

        log_info(f"YouTube Music credentials saved to {filepath}")
        return


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="Standalone YouTube Music Last.fm Scrobbler")
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help="Fetch and process history without actually scrobbling to Last.fm or updating the local database"
    )
    parser.add_argument(
        '--to-datetime',
        metavar="<ISO 8601>",
        help="Scrobble to the specified datetime (ISO 8601 format)"
    )
    parser.add_argument(
        '-l', '--login',
        action='store_true',
        help="Interactively paste YouTube Music request headers to refresh credentials (nothing is scrobbled)"
    )
    parser.add_argument(
        '--set-last-success',
        metavar="<ISO 8601>",
        help="Manually set run_state.last_success_at before the run (manual catch-up); rejected if in the future"
    )
    args = parser.parse_args()

    if args.login:
        try:
            update_browser_json()
        except ConfigError as e:
            log_error(str(e))
            return 78
        return 0

    to_datetime = datetime.now()

    try:
        if args.to_datetime:
            parsed = _parse_local_iso(args.to_datetime)
            if parsed > to_datetime:
                log_error("to_datetime must be in the past")
                return 78
            to_datetime = parsed
            log_info(f"Scrobbling to time: {to_datetime}")

        store = Store()
        store.migrate()

        anchor_override: int | None = None
        if args.set_last_success:
            anchor = _parse_local_iso(args.set_last_success)
            if anchor > to_datetime:
                log_error("--set-last-success must be in the past")
                return 78
            anchor_override = int(anchor.timestamp())
            old = store.get_last_success_at()
            if args.dry_run:
                log_info(f"[DRY RUN] Would set last_success_at: {_format_ts(old)} -> {_format_ts(anchor_override)} "
                         f"(not written, using in memory for this run)")
            else:
                with store.transaction():
                    store.update_last_success_at(anchor_override)
                log_info(f"last_success_at manually set: {_format_ts(old)} -> {_format_ts(anchor_override)}")

        if not os.path.isfile(BROWSER_JSON_PATH):
            raise ConfigError(
                f"YouTube Music credentials not found: {BROWSER_JSON_PATH} does not exist. "
                "Run this script with --login to create it."
            )
        try:
            ytmusic = YTMusic(BROWSER_JSON_PATH)
        except (OSError, ValueError, YTMusicUserError) as e:
            raise ConfigError(
                f"Could not load YouTube Music credentials from {BROWSER_JSON_PATH}: {e}. "
                "Run this script with --login to refresh them."
            ) from e
        process = ImprovedProcess(store, ytmusic, to_datetime, dry_run=args.dry_run,
                                  anchor_override=anchor_override)
        failure = process.execute()
        if failure is None:
            return 0
        return failure.exit_code
    except ConfigError as e:
        log_error(str(e))
        return 78
    except KeyboardInterrupt:
        log_warning("Interrupted by user")
        return 130
    except Exception as e:
        log_error(f"Unexpected error: {e}")
        return 70


if __name__ == '__main__':
    sys.exit(main())
