"""Minimal startup - verify imports succeed before running full bot."""
import os, sys, http.server, threading, time

from database import db
from scheduler import scheduler_manager

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
