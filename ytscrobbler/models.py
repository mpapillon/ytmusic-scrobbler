from dataclasses import dataclass
from typing import Any, Literal, NotRequired, TypedDict

from ytmusicapi.type_alias import JsonDict


@dataclass(frozen=True, slots=True)
class Artist:
    json: JsonDict

    @property
    def id(self) -> str:
        return self.json.get("id") or ""

    @property
    def name(self) -> str:
        return self.json.get("name") or ""


@dataclass(frozen=True, slots=True)
class Album:
    json: JsonDict

    @property
    def id(self) -> str:
        return self.json.get("id") or ""

    @property
    def name(self) -> str:
        return self.json.get("name") or ""


@dataclass(frozen=True, slots=True)
class Track:
    json: JsonDict

    @property
    def video_id(self) -> str:
        return self.json.get("videoId") or ""

    @property
    def title(self) -> str:
        return self.json.get("title") or ""

    @property
    def artists(self) -> list[Artist]:
        artists: list[JsonDict] = self.json.get("artists") or []
        return [Artist(a) for a in artists]

    @property
    def artist(self) -> str:
        # deprecated, should be unsued
        return self.artists[0].name

    @property
    def album(self) -> Album:
        return Album(self.json.get("album") or {})

    @property
    def played(self) -> str:
        return self.json.get("played") or ""

    @property
    def video_type(self) -> str:
        return self.json.get("videoType") or ""

    @property
    def is_regular_track(self) -> bool:
        return self.video_type.startswith("MUSIC_VIDEO_TYPE_") and not any(
            a.name.endswith(" - Topic") for a in self.artists
        )

    @property
    def was_played_today(self) -> bool:
        return self.played.lower() == "today"


class SongToScrobble(TypedDict):
    song: Track
    position: int
    reason: Literal["calibration", "new_song", "reproduction", "position_update"]
    should_scrobble: bool
    previous_position: NotRequired[int | None]


@dataclass
class Scrobble:
    json: dict[str, Any]

    def __init__(self, json: dict[str, Any] | None = None) -> None:
        self.json = {} if json is None else json

    @property
    def track(self) -> Track:
        track: dict[str, Any] = self.json.get("track", {})
        return Track(track)

    @track.setter
    def track(self, value: Track):
        self.json["track"] = value.json

    @property
    def video_id(self) -> str:
        return self.track.video_id

    @property
    def max_array_position(self) -> int | None:
        return self.json.get("max_array_position")

    @max_array_position.setter
    def max_array_position(self, value: int | None):
        self.json["max_array_position"] = value

    @property
    def array_position(self) -> int | None:
        return self.json.get("array_position")

    @array_position.setter
    def array_position(self, value: int):
        self.json["array_position"] = value

    @property
    def scrobbled_at(self) -> str | None:
        return self.json.get("scrobbled_at")

    @scrobbled_at.setter
    def scrobbled_at(self, value: str):
        self.json["scrobbled_at"] = value
