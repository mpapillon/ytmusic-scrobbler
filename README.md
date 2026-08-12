# YOUTUBE MUSIC LAST.FM SCROBBLER

The YouTube Music Last.fm Scrobbler is a Python application that fetches your YouTube Music listening history from the last 24 hours and scrobbles it to Last.fm.

---

## 🚀 Quick Start

### Prerequisites

1. Install Python 3.8+ and dependencies:
   ```bash
   # Using conda (recommended)
   conda env create -f environment.yml
   conda activate ytmusic-scrobbler
   
   # OR using pip
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

On first run, you'll be prompted to:
1. **Authenticate with Last.fm** (browser will open automatically)  
2. **Provide your YouTube Music cookie** (detailed instructions provided)

**To get your YouTube Music cookie:**
1. Go to [https://music.youtube.com](https://music.youtube.com) in your browser
2. Open Developer Tools (F12) → Network tab  
3. Refresh the page and find any `music.youtube.com` request
4. Copy the complete `Cookie` header value
5. Paste when prompted (or save to `.env` as `YTMUSIC_COOKIE`)

---

## ✨ Features

### 🌟 Standalone Version (`start_standalone.py`)

- **No API dependencies** - Direct HTML scraping eliminates API rate limits
- **Multilingual support** - Detects "Today" in 50+ languages (English, Spanish, Chinese, Russian, Arabic, etc.)
- **Smart timestamp distribution** - Logarithmic spread across the time since your last successful run, clamped to the current day. First run ever only calibrates position tracking (nothing is scrobbled).
- **Better duplicate detection** - Tracks re-reproductions and position changes
- **Robust error handling** - Categorizes and handles different error types
- **Enhanced logging** - Better visibility into processing and language detection

**⚠️ Considerations:**
- Requires copying cookie from browser (but provides detailed instructions)
- Cookie needs periodic refresh (browser will notify when needed)

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

1. **Fetches YouTube Music history page** directly via HTTP
2. **Extracts embedded JSON data** from HTML using regex parsing
3. **Detects today's songs** using multilingual date detection (50+ languages)
4. **First run ever**: records today's songs as a position-tracking baseline, scrobbles nothing
5. **Later runs**: smart position tracking identifies new songs and re-reproductions
6. **Calculates timestamps**: logarithmic spread across the time since your last successful run, clamped to today
7. **Scrobbles to Last.fm** with proper error handling and retry logic
8. **Updates database** with enhanced tracking information

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

# Required for standalone version only  
YTMUSIC_COOKIE=your_complete_browser_cookie
```

### Files Used
| File      | Description              |
|-----------|--------------------------|
| `.env`    | API keys and tokens      |
| `data.db` | SQLite tracking database |

---

## 🐛 Troubleshooting

**❌ "Cookie is missing __Secure-3PAPISID"**
- Ensure you copied the complete cookie from Developer Tools
- Make sure you're logged into YouTube Music in the browser

**❌ "Authentication failed"**  
- Your cookie may have expired - get a fresh one from browser
- Cookies typically last several hours to days

**❌ "No songs played today"**
- Check your YouTube Music language - multilingual detection should work
- Report unknown date formats to help improve detection

---

## 📋 Deployment

1. Run locally first to complete Last.fm OAuth
2. Copy `.env` file to server (includes `LASTFM_SESSION`)
3. Set up cron job at any interval you like - timing adapts to the real gap between runs:
   ```bash
   # e.g. every 15 minutes
   */15 * * * * /path/to/python /path/to/start_standalone.py
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
