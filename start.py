"""Minimal startup - catches errors during bot.main() and shows them on /health"""
import os, sys, http.server, traceback, threading

error = None

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        body = error.encode() if error else b"GOOD"
        self.wfile.write(body)
    def log_message(self, *a): pass

# Start health server BEFORE running bot
port = int(os.environ.get("PORT", 8080))
server = http.server.HTTPServer(("0.0.0.0", port), H)
t = threading.Thread(target=server.serve_forever, daemon=True)
t.start()

try:
    # Now try to run bot.main()
    import bot
    bot.main()
except Exception as e:
    error = f"BOT ERROR: {e}\n{traceback.format_exc()}"
    # Keep health server running so we can read the error
    import time
    while True:
        time.sleep(10)
