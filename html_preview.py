"""Local static HTML previews with confined paths and no directory listings."""

from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import mimetypes
from pathlib import Path
import shutil
import stat
import tempfile
import threading
from urllib.parse import quote, unquote, urlsplit
import webbrowser

from developer_tools import _project_file, _project_root


def is_html(block):
    return (block.get("language", "").lower() in {"html", "htm", "html5"}
            or Path(block.get("filename") or "").suffix.lower() in {".html", ".htm"})


class _PreviewHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, root, **kwargs):
        self.root = root
        super().__init__(*args, **kwargs)

    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.serve(False)

    def do_HEAD(self):
        self.serve(True)

    def serve(self, head):
        authority = f"127.0.0.1:{self.server.server_port}"
        if self.headers.get("Host") != authority or self.headers.get("Origin", "http://" + authority) != "http://" + authority:
            self.send_error(403)
            return
        try:
            path = unquote(urlsplit(self.path).path, errors="strict").lstrip("/") or "index.html"
            target, _ = _project_file(self.root, path)
            if target.is_dir():
                target, _ = _project_file(self.root, path.rstrip("/") + "/index.html")
                if not urlsplit(self.path).path.endswith("/"):
                    self.send_response(302)
                    self.send_header("Location", quote(urlsplit(self.path).path, safe="/%") + "/")
                    self.end_headers()
                    return
            metadata = target.stat()
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("Not a regular file")
            with target.open("rb") as source:
                self.send_response(200)
                content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
                if content_type.startswith("text/"):
                    content_type += "; charset=utf-8"
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(metadata.st_size))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                if not head:
                    shutil.copyfileobj(source, self.wfile)
        except (BrokenPipeError, ConnectionResetError):
            return
        except (OSError, ValueError, RuntimeError, UnicodeError):
            self.send_error(404, "Preview file not found")


class PreviewServer:
    def __init__(self, root):
        self.root = _project_root(root)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_PreviewHandler, root=self.root))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        self.thread.start()

    def url(self, relative):
        target, _ = _project_file(self.root, relative)
        if not target.is_file() or target.suffix.lower() not in {".html", ".htm"}:
            raise ValueError("Select an HTML file to preview.")
        return f"http://127.0.0.1:{self.server.server_port}/" + quote(str(relative).replace("\\", "/"), safe="/")

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)


class HtmlPreviews:
    def __init__(self):
        self.temporary = None
        self.generated = None
        self.project = None

    @staticmethod
    def open_url(url):
        if not webbrowser.open(url, new=2):
            raise RuntimeError(f"Could not open your browser. Open this preview URL manually: {url}")
        return url

    def preview_block(self, block):
        if not block.get("complete") or not is_html(block):
            raise ValueError("Choose a complete HTML code block to preview.")
        if self.temporary is None:
            self.temporary = tempfile.TemporaryDirectory(prefix="aiassist-preview-")
        root = Path(self.temporary.name)
        (root / "index.html").write_text(block["code"], encoding="utf-8")
        if self.generated is None:
            self.generated = PreviewServer(root)
        return self.open_url(self.generated.url("index.html"))

    def preview_file(self, root, relative):
        project = _project_root(root)
        target, _ = _project_file(project, relative)
        if not target.is_file() or target.suffix.lower() not in {".html", ".htm"}:
            raise ValueError("Select an HTML file to preview.")
        if self.project is None or self.project.root != project:
            if self.project:
                self.project.close()
            self.project = PreviewServer(project)
        return self.open_url(self.project.url(relative))

    def close(self):
        for server in (self.project, self.generated):
            if server:
                server.close()
        self.project = self.generated = None
        if self.temporary:
            self.temporary.cleanup()
            self.temporary = None
