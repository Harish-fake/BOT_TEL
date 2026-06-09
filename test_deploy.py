import os, http.server

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *a): pass

port = int(os.environ.get("PORT", 8080))
http.server.HTTPServer(("0.0.0.0", port), H).serve_forever()
