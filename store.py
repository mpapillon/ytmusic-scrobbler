import sqlite3
from dataclasses import dataclass
from typing import final


@dataclass
class Scrobble:
    id: int
    track_name: str = ''
    artist_name: str = ''
    album_name: str = ''
    scrobbled_at: str | None = None
    array_position: int | None = None
    max_array_position: int | None = None


@final
class Store:
    def __init__(self):
        self.conn = sqlite3.connect('./data.db')

    def migrate(self):
        cursor = self.conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS scrobbles (
                id INTEGER PRIMARY KEY,
                track_name TEXT,
                artist_name TEXT,
                album_name TEXT,
                scrobbled_at TEXT DEFAULT CURRENT_TIMESTAMP,
                array_position INTEGER,
                max_array_position INTEGER
            )
        ''')

        # Add new columns if they don't exist (for backward compatibility)
        try:
            cursor.execute('ALTER TABLE scrobbles ADD COLUMN max_array_position INTEGER')
        except sqlite3.OperationalError:
            pass  # Column already exists

        # Single-row table tracking the last successful (non-dry-run) run, used to
        # bound the fake-timestamp distribution window to the real elapsed gap
        # since that run instead of a fixed guess.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS run_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_success_at INTEGER
            )
        ''')
        cursor.execute('INSERT OR IGNORE INTO run_state (id, last_success_at) VALUES (1, NULL)')

        self.conn.commit()
        cursor.close()

    def get_last_success_at(self) -> int | None:
        cursor = self.conn.cursor()
        try:
            result = cursor.execute('SELECT last_success_at FROM run_state WHERE id = 1').fetchone()
            return result[0] if result else None
        finally:
            cursor.close()

    def update_last_success_at(self, timestamp: int):
        cursor = self.conn.cursor()
        try:
            cursor.execute('UPDATE run_state SET last_success_at = ? WHERE id = 1', (timestamp,))
            self.conn.commit()
        finally:
            cursor.close()

    def get_scrobbles(self) -> list[Scrobble]:
        cursor = self.conn.cursor()
        try:
            rows = cursor.execute('''
                SELECT id, track_name, artist_name, album_name, scrobbled_at,
                       array_position, max_array_position
                FROM scrobbles
            ''').fetchall()
            return [Scrobble(*row) for row in rows]
        finally:
            cursor.close()

    def find_scrobble(self, track_name: str, artist_name: str, album_name: str) -> Scrobble | None:
        cursor = self.conn.cursor()
        try:
            result = cursor.execute('''
                SELECT id, track_name, artist_name, album_name, scrobbled_at,
                       array_position, max_array_position
                FROM scrobbles
                WHERE track_name = ? AND artist_name = ? AND album_name = ?
            ''', (track_name, artist_name, album_name)).fetchone()
            return Scrobble(*result) if result else None
        finally:
            cursor.close()

    def insert_scrobble(self, track_name: str, artist_name: str, album_name: str, array_position: int, max_array_position: int | None):
        cursor = self.conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO scrobbles (track_name, artist_name, album_name, array_position, max_array_position)
                VALUES (?, ?, ?, ?, ?)
            ''', (track_name, artist_name, album_name, array_position, max_array_position))
            self.conn.commit()
            return cursor.lastrowid
        finally:
            cursor.close()

    def update_scrobble_position(self, id: int, array_position: int, max_array_position: int | None):
        cursor = self.conn.cursor()
        try:
            cursor.execute('''
                UPDATE scrobbles
                SET array_position = ?, max_array_position = ?, scrobbled_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (array_position, max_array_position, id))
            self.conn.commit()
        finally:
            cursor.close()

    def delete_scrobbles(self, ids: list[int]):
        if not ids:
            return
        cursor = self.conn.cursor()
        try:
            cursor.execute('DELETE FROM scrobbles WHERE id IN ({})'.format(','.join('?' for _ in ids)), ids)
            self.conn.commit()
        finally:
            cursor.close()
