#!/usr/bin/env python3
"""
Standalone YouTube Music Last.fm Scrobbler
- No external API dependencies (direct HTML page scraping)
- Multilingual date detection (50+ languages)
- Smart timestamp distribution (logarithmic, bounded to the real elapsed time since the last successful run)
- Better position tracking and re-reproduction detection
- Robust error handling and categorization
"""
import argparse
import os
import sys
import time
import webbrowser
import xml.etree.ElementTree as ET
from typing import final

from dotenv import find_dotenv, load_dotenv, set_key

import lastpy
from date_detection import (
    get_detected_languages,
    get_unknown_date_values,
    is_today_song,
)
from errors import ConfigError, FailureType, LastFmError
from scrobble_utils import (
    PositionTracker,
    SmartScrobbler,
    compute_scrobble_window,
    log_error,
    log_info,
    log_warning,
    start_of_day,
)
from store import Store

# Import our new modules
from ytmusic_fetcher import get_ytmusic_history_from_cookie

load_dotenv(find_dotenv(usecwd=True))


@final
class ImprovedProcess:
    def __init__(self, store: Store, cookie: str, dry_run: bool = False):
        self.cookie = cookie
        self.dry_run = dry_run
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

    def handle_authentication_error(self, error: Exception) -> None:
        """Log an authentication failure with guidance for the user"""
        log_error(f"YouTube Music authentication failed: {error}")
        log_error("Your YouTube Music cookie appears to be expired or invalid. Please update it:")
        print("1. Go to https://music.youtube.com and sign in", file=sys.stderr)
        print("2. Copy the new cookie from Developer Tools", file=sys.stderr)
        print("3. Run this script again", file=sys.stderr)

    def execute(self) -> FailureType | None:
        """Run the full fetch/filter/scrobble flow. Returns None on success, or the
        FailureType that ended the run early."""
        if self.dry_run:
            log_info("Dry run mode: no songs will actually be scrobbled to Last.fm.")

        last_success_at = self.store.get_last_success_at()

        if not self.session:
            try:
                self.session = self.get_token()
            except Exception as e:
                failure_type = self.scrobbler.categorize_error(e)
                log_error(f"Failed to authenticate with Last.fm: {e} ({failure_type.value})")
                return failure_type

        log_info("Fetching YouTube Music history...")
        try:
            history = get_ytmusic_history_from_cookie(self.cookie)
        except Exception as error:
            failure_type = self.scrobbler.categorize_error(error)

            if failure_type == FailureType.AUTH:
                self.handle_authentication_error(error)
            else:
                log_error(f"Failed to fetch history: {error} ({failure_type.value})")
            return failure_type

        log_info(f"Retrieved {len(history)} songs from history")

        print()
        log_info("Filtering songs played today...")
        today_songs = [song for song in history if is_today_song(song.get('playedAt'))]

        # Log unknown date values for future expansion
        unknown_values = get_unknown_date_values(history)
        if unknown_values:
            log_warning(f"Unknown date formats detected: {', '.join(unknown_values)} "
                        f"(please report these to the developer)")

        # Log detected languages
        detected_languages = get_detected_languages(history)
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
                    if (today_song['title'] == db_song.track_name and
                        today_song['artist'] == db_song.artist_name and
                        today_song['album'] == db_song.album_name):
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
                    self.store.delete_scrobbles(scrobbles_to_delete)

        now = int(time.time())
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

        # Bound the fake-timestamp distribution window to the real elapsed time since
        # the last successful run, never going before the start of today - so a short
        # cron interval keeps the window tight instead of getting stretched into a
        # misleadingly wide spread. (Gaps wide enough to lose that same-day anchor are
        # calibration runs, per is_first_time above, and never reach this window.)
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
                if should_scrobble:
                    # Calculate smart timestamp
                    timestamp = self.scrobbler.calculate_timestamp(
                        scrobble_position,
                        total_to_scrobble,
                        window_start,
                        window_end
                    )

                    action = "NEW" if reason == "new_song" else "RE-SCROBBLE"

                    if self.dry_run:
                        songs_scrobbled += 1
                        log_info(f"[DRY RUN] Would scrobble ({action}): \"{song['title']}\" by {song['artist']}")
                        scrobble_position += 1
                    else:
                        # Scrobble the song
                        success = self.scrobbler.scrobble_song(song, self.session, timestamp)

                        if success:
                            songs_scrobbled += 1
                            log_info(f"{action}: \"{song['title']}\" by {song['artist']}")
                            scrobble_position += 1
                        else:
                            log_info(f"FAILED: \"{song['title']}\" by {song['artist']} (Last.fm rejected)")

                if self.dry_run:
                    # Skip database writes so a dry run has no side effects
                    continue

                # Update/insert in database
                existing_scrobble = self.store.find_scrobble(song['title'], song['artist'], song['album'])

                if existing_scrobble:
                    # Update existing song
                    new_max = max(existing_scrobble.max_array_position or position, position)
                    self.store.update_scrobble_position(existing_scrobble.id, position, new_max)
                else:
                    self.store.insert_scrobble(song['title'], song['artist'], song['album'], position, position)
            except Exception as error:
                failure_type = self.scrobbler.categorize_error(error)
                log_error(f'Failed to process "{song["title"]}" by {song["artist"]}: {error} ({failure_type.value})')

                # Continue processing other songs unless it's an auth error
                if failure_type == FailureType.AUTH:
                    had_fatal_error = True
                    break

        # Only mark this as a successful run if it wasn't cut short by a fatal
        # error - otherwise the next run's window would wrongly start from "now"
        # instead of correctly spanning the gap this run failed to cover.
        if not self.dry_run and not had_fatal_error:
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

def get_cookie(args_cookie: str | None) -> str:
    """Get YouTube Music cookie from args or environment"""
    env_cookie = os.environ.get('YTMUSIC_COOKIE')
    cookie = args_cookie or env_cookie

    if not cookie:
        print("YouTube Music cookie required.", file=sys.stderr)
        print(
            "Please set your YouTube Music cookie from your browser with `--set-cookie` argument "
            "or `YTMUSIC_COOKIE` environment variable.",
            file=sys.stderr
        )
        print("To get your cookie:", file=sys.stderr)
        print("1. Go to https://music.youtube.com in your browser", file=sys.stderr)
        print("2. Open Developer Tools (F12)", file=sys.stderr)
        print("3. Go to Network tab", file=sys.stderr)
        print("4. Refresh the page", file=sys.stderr)
        print("5. Find any request to music.youtube.com", file=sys.stderr)
        print("6. Copy the entire 'Cookie' header value", file=sys.stderr)
        print("The cookie should contain '__Secure-3PAPISID=' among other values.", file=sys.stderr)
        raise ConfigError("YouTube Music cookie is required")

    if args_cookie and args_cookie != env_cookie:
        set_key('.env', 'YTMUSIC_COOKIE', cookie)
        log_info("Cookie saved to .env file")

    return cookie

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="Standalone YouTube Music Last.fm Scrobbler")
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help="Fetch and process history without actually scrobbling to Last.fm or updating the local database"
    )
    parser.add_argument(
        '-c', '--set-cookie',
        help="Save the specified cookie for authentication"
    )
    args = parser.parse_args()

    try:
        cookie = get_cookie(args.set_cookie)
        store = Store()
        store.migrate()
        process = ImprovedProcess(store, cookie, dry_run=args.dry_run)
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
