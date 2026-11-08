import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import ytmusicapi
from ytmusicapi import YTMusic
from ytmusicapi.exceptions import YTMusicUserError

from ytscrobbler import BROWSER_JSON_PATH, DATA_JSON_PATH, MAX_BROWSER_SETUP_ATTEMPTS
from ytscrobbler.errors import ConfigError
from ytscrobbler.scrobble_utils import (
    format_ts,
    log_error,
    log_info,
    log_warning,
)
from ytscrobbler.scrobbler import ImprovedProcess
from ytscrobbler.store import DataStore


def _parse_local_iso(value: str) -> datetime:
    """Parse an ISO 8601 date/datetime as local time (tz info dropped). Raises ConfigError on bad format."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ConfigError(f"Invalid ISO 8601 datetime: {value!r}") from None
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def _check_browser_json(filepath: str) -> None:
    with open(filepath, encoding="utf-8") as f:
        headers = json.load(f)
    if "SAPISIDHASH" not in headers.get("authorization", ""):
        raise YTMusicUserError(
            "browser.json has no valid 'authorization' header (SAPISIDHASH). "
            "Copy the request headers of a 'browse' request while signed in, "
            "making sure they include 'authorization'."
        )


def _update_browser_json(filepath: str | None = None) -> None:
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
            ytmusicapi.setup(filepath=filepath)
            _check_browser_json(filepath)
        except (OSError, ValueError, YTMusicUserError) as e:
            remaining = MAX_BROWSER_SETUP_ATTEMPTS - attempt
            log_error(str(e))
            if remaining <= 0:
                raise ConfigError(
                    f"Could not update {filepath} after {MAX_BROWSER_SETUP_ATTEMPTS} attempts"
                )
            log_error(f"{remaining} attempt(s) left.")
            continue

        log_info(f"YouTube Music credentials saved to {filepath}")
        return


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Standalone YouTube Music Last.fm Scrobbler"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and process history without actually scrobbling to Last.fm or updating the local database",
    )
    parser.add_argument(
        "--to-datetime",
        metavar="<ISO 8601>",
        help="Scrobble to the specified datetime (ISO 8601 format)",
    )
    parser.add_argument(
        "-l",
        "--login",
        action="store_true",
        help="Interactively paste YouTube Music request headers to refresh credentials (nothing is scrobbled)",
    )
    parser.add_argument(
        "--set-last-success",
        metavar="<ISO 8601>",
        help="Manually set run_state.last_success_at before the run (manual catch-up). Rejected if in the future",
    )
    args = parser.parse_args()

    if args.login:
        try:
            _update_browser_json()
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

        store = DataStore(Path(DATA_JSON_PATH))

        anchor_override: int | None = None
        if args.set_last_success:
            anchor = _parse_local_iso(args.set_last_success)
            if anchor > to_datetime:
                log_error("--set-last-success must be in the past")
                return 78
            anchor_override = int(anchor.timestamp())
            old = store.last_success_at
            if args.dry_run:
                log_info(
                    f"[DRY RUN] Would set last_success_at: {format_ts(old)} -> {format_ts(anchor_override)} "
                    f"(not written, using in memory for this run)"
                )
            else:
                store.last_success_at = anchor_override
                store.save()
                log_info(
                    f"last_success_at manually set: {format_ts(old)} -> {format_ts(anchor_override)}"
                )

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
        process = ImprovedProcess(
            store,
            ytmusic,
            to_datetime,
            dry_run=args.dry_run,
            anchor_override=anchor_override,
        )
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


if __name__ == "__main__":
    sys.exit(main())
