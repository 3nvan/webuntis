# WebUntis Timetable Comparison CLI

Python CLI tool to fetch your WebUntis timetable, compare it against a saved baseline, and show changes.

## Quick Start

```bash
pip install -r requirements.txt
python3 main.py fetch
python3 main.py save
```

## Commands

| Command | Purpose |
|---------|---------|
| `fetch` | Fetch from WebUntis & compare to baseline |
| `save`  | Save last fetched timetable as baseline |
| `show`  | Display last fetched timetable |
| `config`| Test connection / interactive setup |

### Date Options

```bash
python3 main.py fetch                  # Today (auto-skips weekends)
python3 main.py fetch --days 7         # Next N days
python3 main.py fetch --date YYYY-MM-DD  # Specific day
python3 main.py fetch --start YYYY-MM-DD --end YYYY-MM-DD  # Range
```

### Filters

```bash
python3 main.py fetch --subject Math
python3 main.py fetch --teacher "Smith"
python3 main.py fetch --room A101
```

### Overrides

```bash
python3 main.py fetch --username john --password secret
python3 main.py fetch --server other.webuntis.com
python3 main.py fetch --no-save          # Don't save fetched data
```

### Multi-User

```bash
python3 main.py fetch --user Benny
python3 main.py save --user Benny
```

## Detected Changes

| Icon | Type | What It Means |
|------|------|---------------|
| ❌ | Cancellation | Lesson removed |
| 🔄 | Room Change | Different room |
| ⏱️ | Time Change | Start/end time shifted |
| 👨‍🏫 | Teacher Change | Different/substitute/absent teacher |
| 📚 | Subject Change | Subject changed |
| ✨ | New Class | New lesson added |
| 🎉 | Holiday/Free Day | No lessons on a date |

## Setup

### Dependencies
```
webuntis python-dotenv tabulate colorama
```

### `.env` (copy from `.env.example`)
```env
WEBUNTIS_SERVER=schuldorf.webuntis.com
WEBUNTIS_SCHOOL=schuldorf
WEBUNTIS_USER=your_username
WEBUNTIS_PASSWORD=your_password

# Optional: skip Saturdays and Sundays
SKIP_WEEKENDS=true

# Or specify exact days (0=Mon, 6=Sun)
# IGNORE_DAYS=5,6

# Multi-user (optional)
# WEBUNTIS_USER_BENNY=benny_username
# WEBUNTIS_PASSWORD_BENNY=benny_password
```

### Config options

| Variable | Description |
|----------|-------------|
| `WEBUNTIS_SERVER` | WebUntis server (e.g. `schuldorf.webuntis.com`) |
| `WEBUNTIS_SCHOOL` | School name (e.g. `schuldorf`) |
| `WEBUNTIS_USER` | Default username |
| `WEBUNTIS_PASSWORD` | Default password |
| `SKIP_WEEKENDS` | Skip Sat/Sun when using `--days` |
| `IGNORE_DAYS` | Comma-separated weekday numbers to skip |

## Project Structure

```
webuntis/
├── .env               # Your credentials (keep secret)
├── .env.example       # Template
├── main.py            # Single-file application
├── requirements.txt   # Dependencies
├── README.md          # This file
└── timetables/        # Stored timetable data
    ├── baseline_evadee.json
    └── current_evadee.json
```

## Troubleshooting

```bash
python3 main.py config             # Test connection
python3 main.py config --setup     # Create .env interactively
python3 main.py --help             # General help
python3 main.py fetch --help       # Command-specific help
```

**No baseline?** `python3 main.py fetch && python3 main.py save`
