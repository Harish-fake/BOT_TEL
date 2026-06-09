import os, uuid, time, json, logging, http.server, urllib.parse, threading, re, asyncio
from typing import Optional, Callable, Awaitable

logger = logging.getLogger(__name__)

UPLOAD_DIR = os.path.join("storage", "temp")
MAX_UPLOAD_SIZE = 1024 * 1024 * 1024 * 2  # 2 GB
SESSION_TTL = 3600  # 1 hour

sessions: dict[str, dict] = {}
_lock = threading.Lock()


def create_session(telegram_id: int) -> str:
    token = uuid.uuid4().hex[:16]
    with _lock:
        sessions[token] = {
            "telegram_id": telegram_id,
            "created_at": time.time(),
            "status": "pending",
            "file_path": None,
        }
    return token


def get_session(token: str) -> Optional[dict]:
    with _lock:
        s = sessions.get(token)
        if s is None:
            return None
        if time.time() - s["created_at"] > SESSION_TTL:
            del sessions[token]
            return None
        return s


def update_session(token: str, **kw) -> None:
    with _lock:
        s = sessions.get(token)
        if s:
            s.update(kw)


_upload_processor: Optional[Callable[[int, str, str], Awaitable[None]]] = None


def set_upload_processor(fn: Callable[[int, str, str], Awaitable[None]]) -> None:
    global _upload_processor
    _upload_processor = fn


def _trigger_processing(telegram_id: int, file_path: str, filename: str) -> None:
    fn = _upload_processor
    if fn is None:
        logger.error("No upload processor registered, cannot process uploaded file")
        return
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(fn(telegram_id, file_path, filename))
        loop.close()
    except Exception as e:
        logger.exception(f"Upload processing failed: {e}")


def _cleanup() -> None:
    now = time.time()
    with _lock:
        expired = [t for t, s in sessions.items() if now - s["created_at"] > SESSION_TTL]
        for t in expired:
            del sessions[t]
    threading.Timer(300, _cleanup).start()


_cleanup()


def _parse_multipart(body: bytes, boundary: str) -> dict:
    """Parse multipart/form-data and return {field_name: (filename, content)}."""
    result = {}
    parts = body.split(f"--{boundary}".encode())
    for part in parts:
        if not part or part in (b"\r\n", b"--\r\n", b"--"):
            continue
        header_end = part.find(b"\r\n\r\n")
        if header_end == -1:
            continue
        headers_raw = part[:header_end].decode("utf-8", errors="replace")
        content = part[header_end + 4:]
        if content.endswith(b"\r\n"):
            content = content[:-2]
        name = None
        filename = None
        for h in headers_raw.split("\r\n"):
            h = h.lower()
            if h.startswith("content-disposition:"):
                for seg in h.split(";"):
                    seg = seg.strip()
                    if seg.startswith('name="'):
                        name = seg[6:-1]
                    elif seg.startswith('filename="'):
                        filename = seg[10:-1]
        if name:
            result[name] = (filename, content)
    return result


class UploadHTTPHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/health" or parsed.path == "/":
            self._send_text("OK")
            return
        if parsed.path.startswith("/upload/"):
            token = parsed.path[len("/upload/"):]
            sess = get_session(token)
            if sess is None:
                self._send_text("Invalid or expired upload link.", 404)
                return
            self._send_html(self._upload_page(token))
            return
        self._send_text("Not found", 404)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/upload/"):
            token = parsed.path[len("/upload/"):]
            sess = get_session(token)
            if sess is None:
                self._send_text("Invalid or expired upload link.", 404)
                return
            self._handle_upload(token, sess)
            return
        self._send_text("Not found", 404)

    def _handle_upload(self, token: str, sess: dict) -> None:
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._send_text("Expected multipart/form-data", 400)
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length == 0:
            self._send_text("No data received", 400)
            return
        if length > MAX_UPLOAD_SIZE:
            self._send_text("File too large (max 2 GB)", 413)
            return

        body = self.rfile.read(length)
        m = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', content_type)
        if not m:
            self._send_text("Could not parse boundary", 400)
            return
        boundary = m.group(1) or m.group(2)

        data = _parse_multipart(body, boundary)
        file_info = data.get("file")
        if not file_info or not file_info[0]:
            self._send_text("No file uploaded. Select a ZIP file.", 400)
            return

        filename, content = file_info
        if not filename.lower().endswith(".zip"):
            self._send_text("Only .zip files are accepted.", 400)
            return

        os.makedirs(UPLOAD_DIR, exist_ok=True)
        safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", filename)
        dest = os.path.join(UPLOAD_DIR, f"{token}_{safe_name}")
        with open(dest, "wb") as f:
            f.write(content)

        update_session(token, status="uploaded", file_path=dest)
        self._send_html(self._success_page(token))

        threading.Thread(
            target=_trigger_processing,
            args=(sess["telegram_id"], dest, filename),
            daemon=True,
        ).start()
        logger.info(f"Web upload complete: {filename} ({len(content)} bytes) token={token}")

    def _send_text(self, text: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(text)))
        self.end_headers()
        self.wfile.write(text.encode())

    def _send_html(self, html: str, status: int = 200) -> None:
        body = html.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        logger.debug(f"WEB: {fmt % args}")

    @staticmethod
    def _upload_page(token: str) -> str:
        return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Upload Project</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:600px;margin:40px auto;padding:0 20px;color:#333}}
  h1{{font-size:1.5rem}} .note{{color:#666;font-size:0.9rem}}
  input[type=file]{{display:block;margin:1rem 0}}
  button{{padding:10px 24px;background:#007bff;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:1rem}}
  button:hover{{background:#0056b3}}
</style></head>
<body>
<h1>📤 Upload Project</h1>
<p>Select a ZIP file to upload.</p>
<form method="post" enctype="multipart/form-data" action="/upload/{token}">
  <input type="file" name="file" accept=".zip" required>
  <button type="submit">Upload</button>
</form>
<p class="note">Maximum file size: 2 GB</p>
</body></html>"""

    @staticmethod
    def _success_page(token: str) -> str:
        return """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Upload Complete</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:600px;margin:40px auto;padding:0 20px;color:#333;text-align:center}}
  h1{{font-size:1.5rem;color:#28a745}} .note{{color:#666;font-size:0.9rem}}
</style></head>
<body>
<h1>✅ Upload Complete!</h1>
<p>The bot is now processing your project. Check Telegram for updates.</p>
<p class="note">You can close this page.</p>
</body></html>"""


def start_upload_server(port: int) -> None:
    server = http.server.HTTPServer(("0.0.0.0", port), UploadHTTPHandler)
    logger.info(f"Upload server listening on port {port}")
    server.serve_forever()
