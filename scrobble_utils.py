"""
Smart scrobbling utilities with improved timestamp distribution and error handling
Based on ytmusic-scrobbler-web worker implementation
"""
import sys
import time
import math
from enum import Enum
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import hashlib
import xml.etree.ElementTree as ET
import lastpy


def log_info(message: str) -> None:
    print(message)


def log_warning(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr)


def log_error(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)


class FailureType(Enum):
    AUTH = "AUTH"
    NETWORK = "NETWORK"
    TEMPORARY = "TEMPORARY"  # For 503, rate limits, and other temporary issues
    LASTFM = "LASTFM"
    UNKNOWN = "UNKNOWN"


def compute_scrobble_window(last_success_at: Optional[int], now: int) -> Tuple[int, int]:
    """
    Compute the [window_start, now] range fake timestamps get distributed across.

    window_start is the later of the last successful run and the start of the
    current calendar day (local time, derived from `now`) - so a short gap since
    the last run keeps the window tight, a long gap (outage recovery) widens it to
    cover the real elapsed time, and a multi-day gap still never reaches back past
    today (there's no reliable timestamp for anything YouTube Music files under
    "Yesterday" or older).
    """
    start_of_today = int(
        datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    )
    window_start = max(last_success_at, start_of_today) if last_success_at else start_of_today
    return window_start, now


class ScrobbleTimestampCalculator:
    """Smart timestamp calculator distributing scrobbles across a real time window"""

    @staticmethod
    def calculate_scrobble_timestamp(
        songs_scrobbled_so_far: int,
        total_songs_to_scrobble: int,
        window_start: int,
        window_end: int
    ) -> str:
        """
        Calculate timestamp for scrobbling, distributing songs logarithmically across
        [window_start, window_end] (recent songs cluster near window_end, older songs
        spread out toward window_start). The window itself is computed by the caller
        from the real elapsed time since the last successful run, bounded to the
        current calendar day.
        """
        # If only one song, place it 30 seconds before the end of the window
        if total_songs_to_scrobble == 1:
            return str(window_end - 30)

        span = max(window_end - window_start, 0)
        min_offset = min(30, span)  # guard: don't exceed the span on very short windows

        # Calculate position ratio (0 = most recent, 1 = oldest)
        position_ratio = songs_scrobbled_so_far / (total_songs_to_scrobble - 1)

        # Logarithmic scaling: most recent songs cluster near min_offset,
        # older songs spread out across the full window
        log_scale = math.log(1 + position_ratio * (math.e - 1))
        offset = min_offset + (span - min_offset) * log_scale

        return str(int(window_end - offset))


class ErrorCategorizer:
    """Categorize different types of errors for smart handling"""

    @staticmethod
    def categorize_error(error: Exception) -> FailureType:
        """Categorize error type based on error message"""
        error_message = str(error)

        # Authentication errors
        if any(keyword in error_message for keyword in [
            "401", "UNAUTHENTICATED", "authentication credential",
            "Headers.append", "invalid header value", "__Secure-3PAPISID"
        ]):
            return FailureType.AUTH

        # Temporary service errors (503, 502, 429, rate limits)
        if any(keyword in error_message for keyword in [
            "503", "Service Unavailable", "502", "Bad Gateway",
            "429", "Too Many Requests", "rate limit",
            "temporarily unavailable", "try again later"
        ]):
            return FailureType.TEMPORARY

        # Network/YouTube Music errors
        if any(keyword in error_message for keyword in [
            "Failed to fetch", "network", "timeout",
            "ECONNRESET", "ENOTFOUND", "ConnectionError"
        ]):
            return FailureType.NETWORK

        # Last.fm specific errors
        if any(keyword in error_message for keyword in [
            "audioscrobbler", "last.fm", "scrobble"
        ]):
            return FailureType.LASTFM

        return FailureType.UNKNOWN

    @staticmethod
    def should_deactivate_user(failure_type: FailureType, consecutive_failures: int) -> bool:
        """Determine if user should be deactivated based on failure type and count"""
        thresholds = {
            FailureType.AUTH: 3,      # Auth issues are persistent
            FailureType.NETWORK: 8,   # Network issues might be temporary
            FailureType.TEMPORARY: 15, # Temporary issues should rarely deactivate users
            FailureType.LASTFM: 5,    # Last.fm issues might be temporary
            FailureType.UNKNOWN: 7,   # Give more chances for unknown errors
        }

        return consecutive_failures >= thresholds.get(failure_type, 7)


class SmartScrobbler:
    """Enhanced scrobbler with smart features"""

    def __init__(self, last_fm_api_key: str, last_fm_api_secret: str):
        self.last_fm_api_key = last_fm_api_key
        self.last_fm_api_secret = last_fm_api_secret
        self.timestamp_calculator = ScrobbleTimestampCalculator()
        self.error_categorizer = ErrorCategorizer()

    def _sanitize_string(self, s: str) -> str:
        """Sanitize string for Last.fm API"""
        # Decode Unicode escape sequences
        import re
        s = re.sub(r'\\u([0-9A-Fa-f]{4})', lambda m: chr(int(m.group(1), 16)), s)

        # Replace specific Unicode characters
        replacements = {
            '\u2026': '...',  # ellipsis
            '\u2013': '-',    # en dash
            '\u2014': '-',    # em dash
            '\u2018': "'",    # left single quotation mark
            '\u2019': "'",    # right single quotation mark
            '\u201C': '"',    # left double quotation mark
            '\u201D': '"',    # right double quotation mark
        }

        for old, new in replacements.items():
            s = s.replace(old, new)

        # Remove control characters and invalid Unicode
        s = re.sub(r'[\u0000-\u001F\u007F\uFFFE\uFFFF]', '', s)

        return s

    def _hash_request(self, params: Dict[str, str]) -> str:
        """Create MD5 hash for Last.fm API request"""
        string = ""
        for key in sorted(params.keys()):
            string += key + params[key]
        string += self.last_fm_api_secret
        return hashlib.md5(string.encode('utf-8')).hexdigest()

    def scrobble_song(
        self,
        song: Dict[str, str],
        last_fm_session_key: str,
        timestamp: str
    ) -> bool:
        """
        Scrobble a single song to Last.fm

        Args:
            song: Dict with title, artist, album keys
            last_fm_session_key: User's Last.fm session key
            timestamp: Unix timestamp as string

        Returns:
            True if scrobble was successful, False otherwise
        """
        params = {
            'album': self._sanitize_string(song['album']),
            'api_key': self.last_fm_api_key,
            'method': 'track.scrobble',
            'timestamp': timestamp,
            'track': self._sanitize_string(song['title']),
            'artist': self._sanitize_string(song['artist']),
            'sk': last_fm_session_key,
        }

        # Create API signature
        api_sig = self._hash_request(params)

        # Use lastpy for scrobbling (assuming it's available)
        xml_response = lastpy.scrobble(
            params['track'],
            params['artist'],
            params['album'],
            last_fm_session_key,
            timestamp
        )

        # Parse XML response
        root = ET.fromstring(xml_response)
        scrobbles = root.find('scrobbles')

        if scrobbles is not None:
            accepted = scrobbles.get('accepted', '0')
            ignored = scrobbles.get('ignored', '0')

            scrobble_elements = scrobbles.findall('scrobble')
            for scrobble in scrobble_elements:
                track_elem = scrobble.find('track')
                artist_elem = scrobble.find('artist')
                ignored_message = scrobble.find('ignoredMessage')

                track_corrected = track_elem.get('corrected', '0') if track_elem is not None else '0'
                artist_corrected = artist_elem.get('corrected', '0') if artist_elem is not None else '0'

                if track_corrected != '0' or artist_corrected != '0':
                    log_warning(
                        f'Last.fm corrected "{song["title"]}" by {song["artist"]} -> '
                        f'"{track_elem.text}" by "{artist_elem.text}"'
                    )

                if ignored_message is not None and ignored_message.text:
                    log_warning(f'Last.fm ignored "{song["title"]}" by {song["artist"]}: {ignored_message.text}')

            # Return True if at least one scrobble was accepted (keeping original logic)
            return accepted != '0' or ignored == '0'

        log_warning(f'Unexpected Last.fm response for "{song["title"]}" by {song["artist"]}: no scrobbles element')
        return False

    def calculate_timestamp(
        self,
        position: int,
        total: int,
        window_start: int,
        window_end: int
    ) -> str:
        """Calculate timestamp for scrobbling at given position"""
        return self.timestamp_calculator.calculate_scrobble_timestamp(
            position, total, window_start, window_end
        )

    def categorize_error(self, error: Exception) -> FailureType:
        """Categorize an error for smart handling"""
        return self.error_categorizer.categorize_error(error)

    def should_deactivate_user(self, failure_type: FailureType, consecutive_failures: int) -> bool:
        """Check if user should be deactivated"""
        return self.error_categorizer.should_deactivate_user(failure_type, consecutive_failures)


class PositionTracker:
    """Track song positions for detecting re-reproductions"""

    def __init__(self):
        pass

    @staticmethod
    def detect_songs_to_scrobble(
        today_songs: List[Dict[str, str]],
        database_songs: List[Dict],
        is_first_time: bool = False
    ) -> List[Dict]:
        """
        Determine which songs should be scrobbled based on position tracking

        Args:
            today_songs: Songs from today's history (with position index)
            database_songs: Songs already in database with max_array_position
            is_first_time: Whether this is the very first run ever (empty DB) -
                calibrates position tracking without scrobbling anything, so future
                runs can detect genuinely new plays from a known baseline

        Returns:
            List of songs that should be scrobbled with their info
        """
        songs_to_scrobble = []

        if is_first_time:
            # First run ever: record today's songs as a baseline, scrobble nothing
            for i, song in enumerate(today_songs):
                songs_to_scrobble.append({
                    'song': song,
                    'position': i + 1,
                    'reason': 'calibration',
                    'should_scrobble': False
                })
        else:
            # Regular processing: check for new songs and re-reproductions
            for i, song in enumerate(today_songs):
                current_position = i + 1

                # Find matching song in database
                saved_song = None
                for db_song in database_songs:
                    if (db_song['title'] == song['title'] and
                        db_song['artist'] == song['artist'] and
                        db_song['album'] == song['album']):
                        saved_song = db_song
                        break

                if not saved_song:
                    # New song - scrobble it
                    songs_to_scrobble.append({
                        'song': song,
                        'position': current_position,
                        'reason': 'new_song',
                        'should_scrobble': True
                    })
                elif current_position < saved_song.get('array_position', float('inf')):
                    # Re-reproduction - song moved up in the list (better position than previous session)
                    songs_to_scrobble.append({
                        'song': song,
                        'position': current_position,
                        'reason': 'reproduction',
                        'should_scrobble': True,
                        'previous_position': saved_song.get('array_position')
                    })
                else:
                    # Song exists and hasn't moved up - just update position
                    songs_to_scrobble.append({
                        'song': song,
                        'position': current_position,
                        'reason': 'position_update',
                        'should_scrobble': False
                    })

        return songs_to_scrobble
