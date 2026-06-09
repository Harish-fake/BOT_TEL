"""Minimal startup - verify imports succeed before running full bot."""
import os, sys, http.server, threading, time

# Quick smoke test of all module-level imports
from database import db
from scheduler import scheduler_manager
from handlers.start import start, help_command, about
from handlers.upload import upload_start, receive_zip, cancel, WAITING_FOR_ZIP
from handlers.browse import select_project_callback, get_browse_conversation_handler
from handlers.accounts import accounts_list, delete_account_callback, confirm_delete_account, cancel_del_account, get_add_account_handler
from handlers.github import get_github_handler
from handlers.status import status_command, pushnow_command, projects_command
from handlers.settings import get_schedule_handler
from handlers.admin import admin_users, admin_projects, admin_stats, admin_logs, admin_broadcast

# Imports succeeded - start health server
port = int(os.environ.get("PORT", 8080))

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"GOOD")
    def log_message(self, *a): pass

server = http.server.HTTPServer(("0.0.0.0", port), H)
print(f"OK health on {port}")
server.serve_forever()
