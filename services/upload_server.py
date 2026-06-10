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
        expired = [t for t, s in sessions.items() if now - s.get("created_at", 0) > SESSION_TTL]
        for t in expired:
            del sessions[t]
    threading.Timer(300, _cleanup).start()


_cleanup()


UPLOAD_PAGE = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Upload Project</title>
<style>
  *{box-sizing:border-box}
  body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:600px;margin:40px auto;padding:0 20px;color:#333}
  h1{font-size:1.5rem} .note{color:#666;font-size:0.9rem}
  input[type=file]{display:block;margin:1rem 0}
  button{padding:10px 24px;background:#007bff;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:1rem}
  button:hover{background:#0056b3}
  .error{color:#dc3545;font-weight:bold;margin:1rem 0}
</style></head>
<body>
<h1>Upload Project</h1>
<p>Select a ZIP file to upload.</p>
<form method="post" enctype="multipart/form-data" action="UPLOAD_PATH">
  <input type="file" name="file" accept=".zip" required>
  <button type="submit">Upload</button>
</form>
<p class="note">Maximum file size: 2 GB</p>
</body></html>"""

SUCCESS_PAGE = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Upload Complete</title>
<style>
  *{box-sizing:border-box}
  body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:600px;margin:40px auto;padding:0 20px;color:#333;text-align:center}
  h1{font-size:1.5rem;color:#28a745} .note{color:#666;font-size:0.9rem}
</style></head>
<body>
<h1>Upload Complete!</h1>
<p>The bot is now processing your project. Check Telegram for updates.</p>
<p class="note">You can close this page.</p>
</body></html>"""


class UploadHTTPHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        try:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path in ("/health", "/"):
                self._send_text("OK")
                return
            if parsed.path.startswith("/upload/"):
                token = parsed.path[len("/upload/"):]
                sess = get_session(token)
                if sess is None:
                    self._send_text("Invalid or expired upload link.", 404)
                    return
                html = UPLOAD_PAGE.replace("UPLOAD_PATH", f"/upload/{token}")
                self._send_html(html)
                return
            self._send_text("Not found", 404)
        except Exception as e:
            logger.exception(f"GET {self.path} error")
            self._send_error(str(e))

    def do_POST(self) -> None:
        try:
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
        except Exception as e:
            logger.exception(f"POST {self.path} error")
            self._send_error(str(e))

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

        m = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', content_type)
        if not m:
            self._send_text("Could not parse boundary", 400)
            return
        boundary = m.group(1) or m.group(2)
        boundary_bytes = f"--{boundary}".encode()

        os.makedirs(UPLOAD_DIR, exist_ok=True)

        filename = None
        dest = None
        file_started = False
        header_buf = b""
        boundary_marker = b"--" + boundary_bytes[2:]

        remaining = length
        buf = b""

        while remaining > 0:
            chunk_size = min(remaining, 65536)
            chunk = self.rfile.read(chunk_size)
            if not chunk:
                break
            remaining -= len(chunk)
            buf += chunk

        parts = buf.split(boundary_bytes)
        for part in parts:
            if not part or part in (b"\r\n", b"--\r\n", b"--", b"\r\n--\r\n"):
                continue
            header_end = part.find(b"\r\n\r\n")
            if header_end == -1:
                continue
            headers_raw = part[:header_end].decode("utf-8", errors="replace")
            content = part[header_end + 4:]
            if content.endswith(b"\r\n"):
                content = content[:-2]

            field_name = None
            field_filename = None
            for h in headers_raw.split("\r\n"):
                h_lower = h.lower()
                if h_lower.startswith("content-disposition:"):
                    for seg in h.split(";"):
                        seg = seg.strip()
                        if seg.startswith('name="'):
                            field_name = seg[6:-1]
                        elif seg.startswith('filename="'):
                            field_filename = seg[10:-1]

            if field_name == "file" and field_filename:
                filename = field_filename
                if not filename.lower().endswith(".zip"):
                    self._send_text("Only .zip files are accepted.", 400)
                    return
                safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", filename)
                dest = os.path.join(UPLOAD_DIR, f"{token}_{safe_name}")
                with open(dest, "wb") as f:
                    f.write(content)
                logger.info(f"Uploaded {filename} ({len(content)} bytes) token={token}")
                break

        if not filename or not dest:
            self._send_text("No file uploaded. Select a ZIP file.", 400)
            return

        update_session(token, status="uploaded", file_path=dest)
        self._send_html(SUCCESS_PAGE)

        threading.Thread(
            target=_trigger_processing,
            args=(sess["telegram_id"], dest, filename),
            daemon=True,
        ).start()

    def _send_text(self, text: str, status: int = 200) -> None:
        try:
            body = text.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass

    def _send_html(self, html: str, status: int = 200) -> None:
        try:
            body = html.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass

    def _send_error(self, message: str) -> None:
        try:
            body = f"Error: {message}".encode()
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass

    def log_message(self, fmt: str, *args) -> None:
        logger.debug(f"WEB: {fmt % args}")


def start_upload_server(port: int) -> None:
    server = http.server.HTTPServer(("0.0.0.0", port), UploadHTTPHandler)
    server.timeout = 120
    logger.info(f"Upload server listening on port {port}")
    server.serve_forever()
