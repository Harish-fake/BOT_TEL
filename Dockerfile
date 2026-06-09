FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p storage/projects storage/temp storage/logs database

CMD python -c "
import os, sys
sys.stdout = sys.stderr
os.environ.pop('DATABASE_URL', None)
print('Starting diagnostic...')
print(f'CWD: {os.getcwd()}')
print(f'Files in CWD: {os.listdir(\".\")}')
print('1. importing database...')
from database import db
print(f'   db._pg={db._pg}')
print('2. importing scheduler...')
from scheduler import scheduler_manager
print('   scheduler OK')
print('3. importing handlers...')
from handlers.start import start, help_command, about; print('   start OK')
from handlers.upload import upload_start, receive_zip, cancel, WAITING_FOR_ZIP; print('   upload OK')
from handlers.browse import select_project_callback as _, get_browse_conversation_handler; print('   browse OK')
from handlers.accounts import accounts_list, delete_account_callback, confirm_delete_account, cancel_del_account, get_add_account_handler; print('   accounts OK')
from handlers.github import get_github_handler; print('   github OK')
from handlers.status import status_command, pushnow_command, projects_command; print('   status OK')
from handlers.settings import get_schedule_handler; print('   settings OK')
from handlers.admin import admin_users, admin_projects, admin_stats, admin_logs, admin_broadcast; print('   admin OK')
print('ALL IMPORTS SUCCESSFUL')
print('Starting health server...')
import http.server
port = int(os.environ.get('PORT', 8080))
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(s):
        s.send_response(200)
        s.send_header('Content-Type', 'text/plain')
        s.end_headers()
        s.wfile.write(b'OK')
    def log_message(s, *a): pass
http.server.HTTPServer(('0.0.0.0', port), H).serve_forever()
" 2>&1
