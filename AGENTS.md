# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python script that fetches YouTube Music listening history from the last 24 hours and scrobbles it to Last.fm. History is fetched with `ytmusicapi` using browser-header authentication (`browser.json`). Note: ytmusicapi's OAuth flow is currently broken upstream (sigma67/ytmusicapi#813), so browser.json headers are the supported auth method here. The application includes significant improvements in multilingual support, timestamp distribution, and error handling.

## Environment Setup

```bash
# pip-only setup
pip install -r requirements.txt
```

## Authentication Setup

1. **YouTube Music credentials (`browser.json`)**: Run the script with `--login` and paste your browser request headers when prompted (nothing is scrobbled, the file is created/refreshed):
   ```bash
   python start_standalone.py --login
   ```
   To get the headers:
   - Go to https://music.youtube.com in your browser (signed in)
   - Open Developer Tools (F12) → Network tab, refresh the page
   - Select any `browse` request to music.youtube.com
   - Copy the complete 'Request Headers' (must include `cookie`, `authorization` and `x-goog-authuser`)
   - Paste into the terminal, press Enter then Ctrl-D

   `browser.json` is gitignored - it contains your full Google session. When it expires, any run fails with exit code 78 and refresh instructions.

2. **Last.fm API Setup**: Create `.env` file with:
   ```
   LAST_FM_API=YOUR_LASTFM_API_KEY
   LAST_FM_API_SECRET=YOUR_LASTFM_API_SECRET
   ```

3. **First Run**: The script will open a browser for Last.fm OAuth and create `LASTFM_SESSION` in `.env` for subsequent runs

## Running the Application

```bash
python start_standalone.py
python start_standalone.py --dry-run  # preview, no Last.fm calls or DB writes
python start_standalone.py --login    # refresh browser.json credentials
```
Designed to run repeatedly via cron at any interval - timing adapts to the real gap between runs.

## Running Tests

```bash
python -m unittest discover -s tests -t .
```

## Code Architecture

- `start_standalone.py`: primary implementation (`ImprovedProcess.execute()`), plus `update_browser_json()` for the `--login` flow
- `scrobble_utils.py`: `HistorySong` (normalizes raw `ytmusicapi.get_history()` entries: first artist, album fallback to title, drops " - Topic" channels), `ScrobbleTimestampCalculator` (fake timestamps), `PositionTracker` (new/replay detection), `SmartScrobbler` (Last.fm API + error categorization)
- `date_detection.py`: multilingual "Today" detection (50+ languages), applied to the `played` shelf label strings
- `lastpy/`: custom Last.fm API client (`authorize`, `scrobble`)
- `ytmusic_fetcher.py`: legacy HTML-scraping fetcher, **no longer imported** (kept for reference only)

## Key Dependencies

- `ytmusicapi`: YouTube Music history (browser-header auth via `browser.json`)
- `lastpy`: Last.fm scrobbling (custom library, not in requirements)
- `python-dotenv`: Environment variable management
- `sqlite3`: Built-in SQLite support

## Database Schema (`data.db`)

```sql
CREATE TABLE scrobbles (
    id INTEGER PRIMARY KEY,
    track_name TEXT,
    artist_name TEXT,
    album_name TEXT,
    scrobbled_at TEXT DEFAULT CURRENT_TIMESTAMP,
    array_position INTEGER,
    max_array_position INTEGER
)

CREATE TABLE run_state (  -- single row, last successful (non-dry-run) run
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_success_at INTEGER
)
```

## Important Files

- `start_standalone.py`: entry point
- `.env`: API keys and session tokens
- `browser.json`: YouTube Music credentials (gitignored, created by `--login`)
- `data.db`: SQLite tracking + run state
- `environment.yml`, `requirements.txt`: dependencies

## Scrobbling Logic

The application processes YouTube Music history by:
1. Fetching history with `ytmusicapi.get_history()` and filtering "Today" tracks
2. Drops stale position-tracking rows no longer in today's list, from both the DB and the in-memory comparison.
3. Runs with no same-day `last_success_at` (never run before, or last success was on a previous calendar day) only calibrate position tracking - nothing is scrobbled, since there's no same-day anchor to place guessed timestamps against.
4. Later runs scrobble genuinely new or replayed songs, detected via position tracking.
5. Fake timestamps spread logarithmically across `[last successful run, now]`, clamped to the start of the current day.
6. Skips artists ending with "- Topic".
7. Uses track name as album name when album is missing.
