"""Локальный HTTP-стенд, один временный заказ. python server.py [port]."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys

model = import_module("04_concurrent_version_conflict_test")
root = Path(__file__).resolve().parent
static_files = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("build/app.js", "text/javascript"),
    "/form.css": ("form.css", "text/css; charset=utf-8"),
    "/assets/inter-latin.woff2": ("assets/inter-latin.woff2", "font/woff2"),
    "/assets/inter-cyrillic.woff2": ("assets/inter-cyrillic.woff2", "font/woff2"),
}


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, value, mime="application/json"):
        data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/order":
            self.reply(*model.get_order(self.server.db, 1))
        elif self.path in static_files:
            file, mime = static_files[self.path]
            self.reply(200, (root / file).read_bytes(), mime)
        else:
            self.reply(404, {})

    def do_PUT(self):
        origin = f"http://127.0.0.1:{self.server.server_port}"
        if self.path != "/api/order":
            return self.reply(404, {})
        if self.headers.get("Host") != origin[7:] or self.headers.get("Origin") != origin:
            return self.reply(403, {})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if self.headers.get_content_type() != "application/json" or not 0 < size <= 8192:
                raise ValueError()
            obj = json.loads(self.rfile.read(size))
            if not isinstance(obj, dict) or type(obj.get("version")) is not int or not isinstance(obj.get("comment"), str) or len(obj["comment"]) > 1000:
                raise ValueError()
        except (ValueError, UnicodeError):
            return self.reply(400, {})
        self.reply(*model.update_order_with_version(self.server.db, 1, obj["version"], obj["comment"]))


if __name__ == "__main__":
    if not (root / "build/app.js").is_file():
        sys.exit("Сначала выполните npm ci и npm run build в папке forms.")
    with TemporaryDirectory() as directory, ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv)>1 else 8765), Handler) as server:
        server.db = Path(directory) / "orders.sqlite"
        model.setup_db(server.db)
        print(f"http://127.0.0.1:{server.server_port}", flush=True)
        server.serve_forever()
