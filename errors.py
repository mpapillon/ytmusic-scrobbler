from enum import Enum


class FailureType(Enum):
    def __new__(cls, label: str, exit_code: int):
        obj = object.__new__(cls)
        obj._value_ = label
        return obj

    def __init__(self, label: str, exit_code: int):
        self.exit_code = exit_code

    AUTH = ("AUTH", 77)        # EX_NOPERM
    NETWORK = ("NETWORK", 75)  # EX_TEMPFAIL
    TEMPORARY = ("TEMPORARY", 75)  # EX_TEMPFAIL
    LASTFM = ("LASTFM", 75)    # EX_TEMPFAIL
    UNKNOWN = ("UNKNOWN", 70)  # EX_SOFTWARE


class ScrobblerError(Exception):
    """Base class for all errors raised by this project."""

    failure_type: FailureType = FailureType.UNKNOWN
    retriable: bool = True

    def __init__(self, message: str = "", *, retriable: bool | None = None):
        super().__init__(message)
        if retriable is not None:
            self.retriable = retriable


class YouTubeAuthError(ScrobblerError):
    """YouTube Music credentials are missing, expired, or invalid."""

    failure_type: FailureType = FailureType.AUTH
    retriable: bool = False


class YouTubeFetchError(ScrobblerError):
    """Failed to fetch or parse the YouTube Music history page."""

    failure_type: FailureType = FailureType.NETWORK


class LastFmError(ScrobblerError):
    """The Last.fm API returned an error."""

    failure_type: FailureType = FailureType.LASTFM


class ConfigError(ScrobblerError):
    """Missing or invalid configuration (env vars, cookie, etc.)."""

    failure_type: FailureType = FailureType.UNKNOWN
