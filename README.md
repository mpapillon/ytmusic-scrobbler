# YOUTUBE MUSIC LAST.FM SCROBBLER

The YouTube Music Last.fm Scrobbler is a Python application that fetches your YouTube Music listening history from the last 24 hours and scrobbles it to Last.fm.

---

## 🚀 Quick Start

### Prerequisites

1. Install Python 3.8+ and dependencies:
   ```bash
   # using pip (a venv is recommended)
   pip install -r requirements.txt
   ```

2. Get your Last.fm API credentials from [Last.fm API](https://www.last.fm/api/account/create)

3. Create a `.env` file:
   ```bash
   LAST_FM_API=your_lastfm_api_key
   LAST_FM_API_SECRET=your_lastfm_api_secret
   ```

### Run

```bash
python start_standalone.py
```

On first run you need to:
1. **Create your YouTube Music credentials** once, with the interactive login flow:
   ```bash
   python start_standalone.py --login
   ```
2. **Authenticate with Last.fm** - on the next run, the script opens your browser once and saves `LASTFM_SESSION` to `.env`

**To get your YouTube Music request headers** (pasted when `--login` prompts you):
1. Open a **private/incognito window**, go to [https://music.youtube.com](https://music.youtube.com) and sign in there
2. Open Developer Tools (F12) → Network tab  
3. Refresh the page and select any `browse` request to `music.youtube.com`
4. Copy the complete **Request Headers** (they must include `cookie`, `authorization` and `x-goog-authuser`)
5. Paste into the terminal, press Enter then Ctrl-D — this creates `browser.json`

> **Tip:** sign in in a dedicated private window rather than your daily browser
> profile. Your regular sessions keep rotating their token cookies (`*PSIDTS`),
> which expires the copied headers faster; a private window nobody else uses
> stays untouched, so the credentials last noticeably longer.

---

## ✨ Features

### 🌟 Standalone Version (`start_standalone.py`)

- **ytmusicapi-based fetching** - History comes from `ytmusicapi.get_history()` (the same API the web player uses), so no fragile HTML parsing
- **Multilingual support** - Detects "Today" in 50+ languages (English, Spanish, Chinese, Russian, Arabic, etc.)
- **Smart timestamp distribution** - Logarithmic spread across the time since your last successful run, clamped to the current day. First run ever only calibrates position tracking (nothing is scrobbled).
- **Better duplicate detection** - Tracks re-reproductions and position changes
- **Robust error handling** - Categorizes and handles different error types
- **Enhanced logging** - Better visibility into processing and language detection

**⚠️ Considerations:**
- Credentials are Google session cookies, valid only while your browser session lives
- Periodically refresh them with `python start_standalone.py --login` (the script tells you when they've expired)
- Note: ytmusicapi's long-lived OAuth flow is currently broken upstream ([sigma67/ytmusicapi#813](https://github.com/sigma67/ytmusicapi/issues/813)), hence browser-header auth for now

---

## 🗄️ Database Schema

SQLite is used to track scrobbled songs and prevent duplicates:

```sql
CREATE TABLE scrobbles (
    id INTEGER PRIMARY KEY,
    track_name TEXT,
    artist_name TEXT,
    album_name TEXT,
    scrobbled_at TEXT DEFAULT CURRENT_TIMESTAMP,
    array_position INTEGER,
    max_array_position INTEGER           -- Tracks highest position
)

CREATE TABLE run_state (                 -- Single row, last successful run
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_success_at INTEGER
)
```

---

## 📝 How It Works

1. **Fetches your play history** via `ytmusicapi.get_history()`
2. **Detects today's songs** using multilingual date detection on the history shelf labels (50+ languages)
3. **First run ever**: records today's songs as a position-tracking baseline, scrobbles nothing
4. **Later runs**: smart position tracking identifies new songs and re-reproductions
5. **Calculates timestamps**: logarithmic spread across the time since your last successful run, clamped to today
6. **Scrobbles to Last.fm** with proper error handling and retry logic
7. **Updates database** with enhanced tracking information

---

## 🌍 Multilingual Support

The scrobbler automatically detects "Today" in these language families:

- **Latin**: English, Spanish, Portuguese, Italian, French, German, Dutch, etc.
- **Cyrillic**: Russian, Ukrainian, Bulgarian, Serbian, etc.
- **Arabic**: Arabic, Persian, Urdu
- **CJK**: Chinese (Simplified/Traditional), Japanese, Korean
- **Indic**: Hindi, Bengali, Tamil, Telugu, etc.
- **Southeast Asian**: Thai, Vietnamese, Indonesian, etc.
- **Others**: Hebrew, Georgian, Armenian, etc.

---

## 🔧 Configuration

### Environment Variables (.env)
```bash
LAST_FM_API=your_lastfm_api_key
LAST_FM_API_SECRET=your_lastfm_api_secret

# Added automatically after first run
LASTFM_SESSION=your_session_token
```

### Files Used
| File            | Description                                        |
|-----------------|----------------------------------------------------|
| `.env`          | API keys and tokens                                |
| `browser.json`  | YouTube Music credentials (created by `--login`, keep private) |
| `data.db`       | SQLite tracking database                             |

---

## 🐛 Troubleshooting

**❌ "YouTube Music credentials not found: browser.json does not exist"**
- Run `python start_standalone.py --login` to create it (see Quick Start)

**❌ "The following entries are missing in your headers: cookie, x-goog-authuser"**
- You copied headers from the wrong request - pick a `browse` request while signed in

**❌ "authentication failed" / history request returned no data**
- Your `browser.json` credentials have expired - refresh them with `--login`
- Sessions typically last days to weeks while you stay logged in in the browser

**❌ "No songs played today"**
- Check your YouTube Music language - multilingual detection should work
- Report unknown date formats to help improve detection

---

## 📋 Deployment

1. Run locally first to complete Last.fm OAuth and create `browser.json`
2. Copy `.env` **and `browser.json`** to the server (both contain secrets - keep them private)
3. Set up cron job at any interval you like - timing adapts to the real gap between runs.
   `browser.json` and `data.db` are resolved relative to the working directory, so `cd` into the project first:
   ```bash
   # e.g. every 15 minutes
   */15 * * * * cd /path/to/ytmusic-scrobbler && /path/to/python start_standalone.py
   ```
4. Test with `--dry-run` first to preview what a run would do without side effects

---

## 🤝 Contributing

Contributions are welcome!

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Commit changes: `git commit -m 'Add amazing feature'`
4. Push to branch: `git push origin feature/amazing-feature`
5. Open a Pull Request

---

## 📄 License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for more information.

---

## 🎵 Enjoy Your Scrobbles!

Scrobble your Youtube Music listening history with last.fm: reliability, multilingual support, and smart smart timestamp handling. 🎶
