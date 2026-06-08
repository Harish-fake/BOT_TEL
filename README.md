# GitSync Telegram Bot

A production-ready Telegram bot that manages software projects, connects them to GitHub repositories, monitors file changes, and automatically pushes updates on a scheduled basis.

## Features

- **Project Upload** — Upload project ZIP files (up to 1GB) for analysis and management
- **Project Analysis** — Automatically detects file count, folder count, lines of code, and technologies used
- **File Management** — Browse, view, create, delete, and rename files directly via Telegram
- **Multiple GitHub Accounts** — Link multiple GitHub accounts per user, assign per project
- **Git Integration** — Initialize repos, create `.gitignore`, commit and push
- **Auto-Sync** — Daily, weekly, or custom cron scheduling via APScheduler
- **Manual Push** — Immediate commit and push with `/pushnow`
- **Status Reports** — View sync history, changed files, and commit hashes
- **Admin Panel** — View users, projects, stats, and logs
- **Security** — Token encryption, ZIP validation, path traversal protection
- **Persistent Logging** — Rotating log files

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message and command list |
| `/help` | Detailed help |
| `/about` | Bot information |
| `/upload` | Upload a project ZIP file |
| `/projects` | List your projects |
| `/status` | View project status and sync info |
| `/pushnow` | Immediately push changes to GitHub |
| `/github` | Link a project to a GitHub repository |
| `/schedule` | Set auto-sync schedule |
| `/accounts` | List linked GitHub accounts |
| `/addaccount` | Add a new GitHub account |
| `/menu` | Show interactive menu |
| `/users` | [Admin] List all users |
| `/stats` | [Admin] Bot statistics |
| `/logs` | [Admin] Download log file |

## Setup

### Prerequisites

- Python 3.12+
- A Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- A GitHub Personal Access Token

### Local Installation

```bash
# Clone the repository
git clone <repo-url> gitsync-bot
cd gitsync-bot

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your BOT_TOKEN and ADMIN_IDS

# Run
python bot.py
```

### Configuration

Create a `.env` file:

```env
BOT_TOKEN=your_telegram_bot_token
GITHUB_TOKEN=your_github_token
ADMIN_IDS=123456789,987654321
DATABASE_PATH=database/bot.db
LOG_LEVEL=INFO
```

### Docker Deployment

```bash
docker compose up -d
```

### Render Deployment

1. Push this repo to GitHub
2. Go to [Render Dashboard](https://dashboard.render.com)
3. Click **New +** → **Worker**
4. Connect your repository
5. Render will auto-detect the `render.yaml`
6. Set the `BOT_TOKEN` environment variable in Render dashboard
7. Deploy

## Project Structure

```
gitsync-bot/
├── bot.py                  # Entry point
├── config.py               # Configuration
├── database.py             # SQLite schema & CRUD
├── scheduler.py            # APScheduler wrapper
├── github_manager.py       # GitHub operations
├── project_manager.py      # Project CRUD
├── analyzer.py             # Project analysis
├── handlers/
│   ├── start.py            # /start, /help, /about
│   ├── upload.py           # /upload
│   ├── browse.py           # File browsing
│   ├── accounts.py         # /accounts, /addaccount
│   ├── github.py           # /github
│   ├── status.py           # /status, /pushnow, /projects
│   ├── settings.py         # /schedule
│   └── admin.py            # Admin commands
├── services/
│   ├── zip_service.py      # ZIP validation & extraction
│   ├── file_service.py     # File operations
│   ├── git_service.py      # Git operations
│   ├── report_service.py   # Report formatting
│   ├── schedule_service.py # Schedule utilities
│   └── encryption_service.py # Token encryption
├── storage/
│   ├── projects/           # Extracted projects
│   ├── temp/               # Temporary uploads
│   └── logs/               # Rotating logs
├── database/
│   └── bot.db              # SQLite database
├── Dockerfile
├── docker-compose.yml
├── render.yaml
├── requirements.txt
└── .env.example
```

## Database Schema

- **users** — Telegram user accounts
- **github_accounts** — Linked GitHub accounts per user
- **projects** — Uploaded projects with GitHub linkage
- **schedules** — Sync schedules per project
- **sync_logs** — Sync operation history
- **settings** — Key-value settings

## Security

- GitHub tokens are encrypted with AES (Fernet) before storage
- ZIP files are validated for size, entry count, and path traversal
- Rate limiting on file operations
- Admin-only commands require pre-configured user IDs
- No tokens exposed in logs or responses

## License

MIT
