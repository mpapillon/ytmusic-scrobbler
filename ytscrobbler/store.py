from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, final

from ytscrobbler.models import Scrobble, Track


@final
class DataStore:
    _path: Path
    _data: dict[str, Any]

    def __init__(self, path: Path):
        self._path = path
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {}
        with open(self._path, "r") as f:
            return json.load(f)

    def save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False))
        tmp.replace(self._path)

    @property
    def last_success_at(self) -> int | None:
        return self._data.get("last_success_at")

    @last_success_at.setter
    def last_success_at(self, value: int):
        self._data["last_success_at"] = value

    @property
    def _scrobbles(self) -> dict[str, dict[str, Any]]:
        return self._data.setdefault("scrobbles", {})

    def get_scrobbles(self) -> list[Scrobble]:
        return [Scrobble(s) for s in self._scrobbles.values()]

    def find_scrobble(self, video_id: str) -> Scrobble | None:
        return Scrobble(s) if (s := self._scrobbles.get(video_id)) is not None else None

    def insert_scrobble(
        self,
        track: Track,
        array_position: int,
        max_array_position: int | None,
    ):
        s = Scrobble()
        s.track = track
        s.array_position = array_position
        s.max_array_position = max_array_position
        s.scrobbled_at = datetime.now().isoformat()

        self._scrobbles[s.video_id] = s.json

    def update_scrobble_position(
        self, video_id: str, array_position: int, max_array_position: int | None
    ):
        if (s := self.find_scrobble(video_id)) is None:
            return

        s.array_position = array_position
        s.max_array_position = max_array_position
        s.scrobbled_at = datetime.now().isoformat()

    def delete_scrobbles(self, video_ids: list[str]):
        for video_id in video_ids:
            self._scrobbles.pop(video_id, None)
