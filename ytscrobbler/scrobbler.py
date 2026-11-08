#!/usr/bin/env python3
"""
YouTube Music Last.fm Scrobbler
- YouTube Music history via ytmusicapi (browser.json auth, see README)
- Smart timestamp distribution (logarithmic, bounded to the real elapsed time since the last successful run)
- Better position tracking and re-reproduction detection
- Robust error handling and categorization
"""

import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import final

import dotenv
from ytmusicapi import YTMusic

from ytscrobbler import lastpy
from ytscrobbler.errors import ConfigError, FailureType, LastFmError
from ytscrobbler.models import Track
from ytscrobbler.scrobble_utils import (
    PositionTracker,
    SmartScrobbler,
    compute_scrobble_window,
    format_ts,
    log_error,
    log_info,
    start_of_day,
)
from ytscrobbler.store import DataStore


@final
class ImprovedProcess:
    def __init__(
        self,
        store: DataStore,
        ytmusic: YTMusic,
        to_datetime: datetime,
        dry_run: bool = False,
        anchor_override: int | None = None,
    ):
        self.ytmusic = ytmusic
        self.to_datetime = to_datetime
        self.dry_run = dry_run
        self.anchor_override = anchor_override
        self.api_key = os.environ.get("LAST_FM_API")
        self.api_secret = os.environ.get("LAST_FM_API_SECRET")
        if not self.api_key or not self.api_secret:
            raise ConfigError(
                "Missing LAST_FM_API or LAST_FM_API_SECRET environment variables"
            )

        try:
            self.session = os.environ["LASTFM_SESSION"]
        except KeyError:
            self.session = None

        # Initialize smart scrobbler
        self.scrobbler = SmartScrobbler(self.api_key, self.api_secret)
        self.position_tracker = PositionTracker()
        self._store = store

    def get_token(self) -> str:
        auth_token = lastpy.get_token()
        auth_url = (
            f"https://www.last.fm/api/auth/?api_key={self.api_key}&token={auth_token}"
        )

        poll_interval_seconds = 5
        timeout_minutes = 5
        max_attempts = timeout_minutes * 60 // poll_interval_seconds

        print(
            "\nLast.fm authorization required. Open this URL and approve access:\n"
            f"\n    {auth_url}\n\n"
            f"Waiting up to {timeout_minutes} minutes for approval...\n"
        )

        for _ in range(max_attempts):
            xml_response = lastpy.get_session(auth_token)
            root = ET.fromstring(xml_response)
            if (
                session_key := root.find("session/key")
            ) is not None and session_key.text:
                dotenv.set_key(".env", "LASTFM_SESSION", session_key.text)
                log_info("Last.fm authorization successful.")
                return session_key.text

            error = root.find("error")
            if error is not None and error.attrib.get("code") == "14":
                # 14 : This token has not been authorized
                time.sleep(poll_interval_seconds)
                continue

            message = (
                error.text.strip() if error is not None and error.text else xml_response
            )
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
            log_error(
                "YouTube Music authentication failed: the history request returned no data."
            )
        log_error("Your YouTube Music credentials appear to be expired or invalid.")
        log_error(
            "Run this script with --login and paste the request headers of a signed-in"
        )
        log_error("music.youtube.com 'browse' request when prompted.")

    def execute(self) -> FailureType | None:
        """Run the full fetch/filter/scrobble flow. Returns None on success, or the
        FailureType that ended the run early."""
        if self.dry_run:
            log_info("Dry run mode: no songs will actually be scrobbled to Last.fm.\n")

        last_success_at = (
            self.anchor_override
            if self.anchor_override is not None
            else self._store.last_success_at
        )
        if self.anchor_override is not None and self.dry_run:
            log_info(
                f"[DRY RUN] Using last_success_at override: {format_ts(self.anchor_override)}"
            )

        if not self.session:
            try:
                self.session = self.get_token()
            except Exception as e:
                failure_type = self.scrobbler.categorize_error(e)
                log_error(
                    f"Failed to authenticate with Last.fm: {e} ({failure_type.value})"
                )
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

        history = [Track(h) for h in raw_history]
        log_info(f"Retrieved {len(history)} songs from history")

        today_tracks = [
            track
            for track in history
            if track.was_played_today and track.is_regular_track
        ]
        log_info(f"Found {len(today_tracks)} songs played today")

        if len(today_tracks) == 0:
            log_info("Nothing to scrobble.")
            return None

        print()

        scrobbles = self._store.get_scrobbles()

        # Clean up database: remove songs not in today's history
        if scrobbles:
            scrobbles_to_delete: list[str] = []
            for scrobble in scrobbles:
                found = False
                for today_song in today_tracks:
                    if today_song.video_id == scrobble.video_id:
                        found = True
                        break

                if not found:
                    scrobbles_to_delete.append(scrobble.video_id)

            if scrobbles_to_delete:
                # Drop these from the in-memory list too, not just the DB table -
                # otherwise a song that legitimately replays later in the day still
                # matches its stale in-memory entry and gets wrongly treated as
                # "already known" instead of a new play.
                scrobbles = [
                    s for s in scrobbles if s.video_id not in scrobbles_to_delete
                ]

                if self.dry_run:
                    log_info(
                        f"[DRY RUN] Would remove {len(scrobbles_to_delete)} songs no longer in today's history"
                    )
                else:
                    log_info(
                        f"Removing {len(scrobbles_to_delete)} songs no longer in today's history"
                    )
                    self._store.delete_scrobbles(scrobbles_to_delete)
                    self._store.save()

        now = int(self.to_datetime.timestamp())
        is_first_time = last_success_at is None or last_success_at < start_of_day(now)

        songs_to_process = self.position_tracker.detect_songs_to_scrobble(
            today_tracks, scrobbles, is_first_time
        )

        # Count how many will actually be scrobbled
        songs_to_scrobble = [s for s in songs_to_process if s["should_scrobble"]]
        total_to_scrobble = len(songs_to_scrobble)

        if is_first_time:
            log_info(
                f"Calibration run: recording {len(today_tracks)} songs as a baseline, nothing scrobbled. "
                f"Future runs will scrobble new plays going forward."
            )

        log_info(
            f"Processing {len(songs_to_process)} songs ({total_to_scrobble} will be scrobbled)"
        )

        window_start, window_end = compute_scrobble_window(last_success_at, now)

        songs_scrobbled = 0
        scrobble_position = 0
        had_fatal_error = False

        for item in songs_to_process:
            song = item["song"]
            position = item["position"]
            should_scrobble = item["should_scrobble"]
            reason = item["reason"]

            try:
                if self.dry_run:
                    if should_scrobble:
                        action = "NEW" if reason == "new_song" else "RE-SCROBBLE"
                        songs_scrobbled += 1
                        log_info(
                            f'[DRY RUN] Would scrobble ({action}): "{song.title}" by {song.artist}'
                        )
                        scrobble_position += 1
                    continue

                existing_scrobble = self._store.find_scrobble(song.video_id)

                if existing_scrobble:
                    new_max = max(
                        existing_scrobble.max_array_position or position, position
                    )
                    self._store.update_scrobble_position(
                        existing_scrobble.video_id, position, new_max
                    )
                else:
                    self._store.insert_scrobble(song, position, position)

                if should_scrobble:
                    timestamp = self.scrobbler.calculate_timestamp(
                        scrobble_position,
                        total_to_scrobble,
                        window_start,
                        window_end,
                    )
                    action = "NEW" if reason == "new_song" else "RE-SCROBBLE"

                    success = self.scrobbler.scrobble_song(
                        song, self.session, timestamp
                    )

                    if success:
                        songs_scrobbled += 1
                        log_info(f'{action}: "{song.title}" by {song.artist}')
                        scrobble_position += 1
                    else:
                        log_info(
                            f'FAILED: "{song.title}" by {song.artist} (Last.fm rejected)'
                        )

                self._store.save()

            except Exception as error:
                failure_type = self.scrobbler.categorize_error(error)
                log_error(
                    f'Failed to process "{song.title}" by {song.artist}: {error} ({failure_type.value})'
                )

                # Continue processing other songs unless it's an auth error
                if failure_type == FailureType.AUTH:
                    had_fatal_error = True
                    break

        if not self.dry_run and not had_fatal_error:
            self._store.last_success_at = now
            self._store.save()

        print()
        if self.dry_run:
            log_info(
                f"Run complete: {len(today_tracks)} today, {songs_scrobbled} would be scrobbled"
            )
        else:
            log_info(
                f"Run complete: {len(today_tracks)} today, {songs_scrobbled} scrobbled, "
                f"{len(songs_to_process)} processed"
            )

        if had_fatal_error:
            return FailureType.AUTH
        return None
