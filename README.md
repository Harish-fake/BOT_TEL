# GitSync Bot

A Telegram bot that uploads your project files to GitHub in scheduled batches.

Upload a ZIP → Bot extracts and analyzes it → Link a GitHub repo → Bot pushes 4 files per batch on your chosen schedule (every 1 min, 1 hour, 4 hours, daily, etc.).

## Features

- **📤 Upload & Analyze** — Send a ZIP file; the bot counts files, folders, LOC, and detects technologies (Python, Java, JS, React, etc.)
- **🔗 GitHub Integration** — Link multiple GitHub accounts, assign per project
- **⏰ Scheduled Sync** — Push files in batches of 4 on interval (1m/1h/4h/6h/12h) or daily/weekly cron
- **📊 Progress Tracking** — `/status` shows progress bar, next push time, sync history
- **📁 File Browser** — Browse, delete, rename, and create files directly in Telegram
- **⏸ Pause/Resume** — Control sync per project
- **🔒 Private & Secure** — Per-user data isolation, encrypted tokens, path traversal protection
- **📱 Interactive UI** — Inline buttons throughout for a guided experience

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome & quick start |
| `/upload` | Send a project ZIP file |
| `/github` | Link a project to a GitHub repo |
| `/projects` | List your projects with progress |
| `/status` | Sync progress, next push, history |
| `/pushnow` | Push next batch immediately |
| `/pushall` | Push ALL remaining files at once |
| `/schedule` | Set sync frequency |
| `/pause` | Pause auto-sync |
| `/resume` | Resume auto-sync |
| `/batchsize` | Change files per batch (1–50) |
| `/addaccount` | Add a GitHub account |
| `/accounts` | List GitHub accounts |
| `/menu` | Interactive menu |
| `/help` | Full command guide |

## Quick Start

```bash
git clone <repo-url> gitsync-bot
cd gitsync-bot
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env: set BOT_TOKEN, ADMIN_IDS
python bot.py
```

Then in Telegram:
1. `/start` → 4-step guide
2. `/addaccount` → paste your GitHub PAT
3. `/upload` → send your project ZIP
4. `/github` → link to a repo → auto-sync begins

## Deployment

### Render (free)

1. Push this repo to GitHub
2. [Create a free PostgreSQL database](https://dashboard.render.com/new/database) — gives you `DATABASE_URL`
3. [Create a Worker service](https://dashboard.render.com/new/worker) → connect repo → set `BOT_TOKEN` and `ADMIN_IDS`
4. The bot auto-configures via `render.yaml`

### Docker

```bash
docker compose up -d
```

## Project Structure

```
gitsync-bot/
├── bot.py                  # Entry point, handler registration, scheduler
├── config.py               # Environment configuration
├── database.py             # SQLite/PostgreSQL schema & CRUD
├── scheduler.py            # APScheduler with SQLAlchemyJobStore
├── github_manager.py       # GitHub init & push operations
├── project_manager.py      # Business logic layer
├── analyzer.py             # Project analysis (file count, LOC, tech detection)
├── handlers/
│   ├── start.py            # /start, /help, /about
│   ├── upload.py           # ZIP upload & extraction
│   ├── browse.py           # File browser with path traversal protection
│   ├── accounts.py         # GitHub account management
│   ├── github.py           # GitHub linking flow
│   ├── status.py           # Progress, push, projects list
│   ├── settings.py         # Schedule & batch size config
│   └── admin.py            # Admin commands (/users, /stats, /logs)
├── services/
│   ├── git_service.py      # Git commit & push operations
│   ├── file_service.py     # File system operations
│   ├── file_tracker.py     # SHA-256 file tracking & batch selection
│   ├── zip_service.py      # ZIP validation & extraction
│   ├── encryption_service.py # Fernet token encryption
│   ├── report_service.py   # Formatted Telegram messages
│   └── schedule_service.py # Cron expression parsing
├── storage/                # Runtime data (ephemeral — use PostgreSQL on Render)
├── database/               # SQLite fallback location
├── Dockerfile
├── docker-compose.yml
├── render.yaml
└── requirements.txt
```

## Architecture

- **python-telegram-bot v22** — async polling, ConversationHandler for multi-step flows
- **APScheduler** — interval & cron triggers, SQLAlchemyJobStore for persistence
- **SQLite** (local) / **PostgreSQL** (production) — auto-detected via `DATABASE_URL`
- **GitPython** — `repo.index.add()`, `commit()`, `push()` with auth URL swapping
- **cryptography (Fernet)** — token encryption key derived from `ENCRYPTION_KEY` or `BOT_TOKEN`
- **httpx** — async streaming downloads for large ZIP files

Each push commits 4 files (configurable) with message format:
```
Uploaded via <github_username> — 2026-06-09 04:20 PM IST
```

## Security

- GitHub tokens encrypted with Fernet before storage
- ZIP extraction guards against path traversal (`os.path.commonpath` check)
- File operations validate project ownership before allowing browse/delete/rename
- Admin commands restricted to `ADMIN_IDS`
- No hardcoded secrets — all via environment variables
- `.dockerignore` prevents secrets from entering images

## License

MIT
