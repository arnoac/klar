"""`klar dev`: build, serve and reload on every save.

    klar dev app.klar                  a std.ui browser app: served at http://127.0.0.1:8000
    klar dev server.klar               a backend (std.web / std.http): run on Node, restarted on save
    klar dev server.klar app.klar      both: app.klar is built to app.html next to it, for the server to serve

Every .klar file in the entry files' folders is watched. After a save everything is rebuilt; open pages
reload by themselves (a small script is added to every HTML page that passes through the dev server). When a
build fails, every page shows the errors instead, and reloads again once they are fixed. The backend gets its
port in the PORT environment variable (std.web's web_port reads it) and KLAR_DEV=1, and runs in its own folder.
"""

import http.client
import http.server
import os
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

from .checker import check_source
from .errors import KlarError
from .loader import _is_std

RELOAD_JS = """<script>(() => {
  const es = new EventSource("/__klar/events");
  es.addEventListener("reload", () => location.reload());
  es.onerror = () => { es.close(); const retry = () => fetch("/__klar/ping").then(() => location.reload(), () => setTimeout(retry, 500)); setTimeout(retry, 500); };
})();</script>"""

ERROR_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Build failed</title><link rel="icon" href="/__klar/icon.svg">
<style>body{{margin:0;font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;background:#1c1917;color:#fafaf9}}
h1{{font:600 16px system-ui,sans-serif;margin:0;padding:16px 20px;background:#991b1b}}
pre{{margin:0;padding:20px;white-space:pre-wrap}}</style></head>
<body><h1>klar dev: the build failed. Fix the errors below and save; this page reloads by itself.</h1>
<pre>{errors}</pre>{script}</body></html>
"""


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def html_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


ICON_LINK = '<link rel="icon" href="/__klar/icon.svg">'
ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime", "klar_icon.svg")


def inject(html):
    """Adds the reload script, and the Klar icon as the tab icon when the page sets none of its own."""
    if 'rel="icon"' not in html and "rel='icon'" not in html:
        h = html.find("</head>")
        if h >= 0:
            html = html[:h] + ICON_LINK + html[h:]
    i = html.rfind("</body>")
    return html[:i] + RELOAD_JS + html[i:] if i >= 0 else html + RELOAD_JS


class Entry:
    def __init__(self, path, tmp):
        self.path = os.path.abspath(path)
        self.stem = os.path.splitext(os.path.basename(path))[0]
        self.kind = None            # "web" or "node", known after the first successful check
        self.out = None
        self.tmp = tmp

    def build(self):
        """Returns None on success, else the rendered errors."""
        from .cli import WEB_PAGE, check_main_signature, load
        from .jscompiler import compile_to_js
        try:
            src = load(self.path)
        except OSError as ex:
            return f"cannot read {self.path}: {ex}"
        prog, errors = check_source(src, self.path)
        if not errors:
            e = check_main_signature(prog, self.path)
            if e:
                errors = [e]
        js = None
        if not errors:
            # A browser app imports std.ui itself; a server may reach it only through a library (std.api).
            is_ui = any(d.kind == "Use" and not _is_std(d.module) and d.path == ["std", "ui"] for d in prog.decls)
            self.kind = "web" if is_ui else "node"
            try:
                out = os.path.join(os.path.dirname(self.path) if self.kind == "web" else self.tmp, self.stem + ".x")
                js = compile_to_js(prog, self.path, self.kind, out)
            except KlarError as e:
                errors = [e]
        if errors:
            out = []
            for e in errors:
                esrc = src
                if e.file and os.path.exists(e.file):
                    esrc = load(e.file)
                out.append(e.render(esrc))
            return "\n".join(out)
        if self.kind == "web":
            self.out = os.path.join(os.path.dirname(self.path), self.stem + ".html")
            text = WEB_PAGE.format(title=self.stem, head=getattr(js, "head", ""), js=js.replace("</script", "<\\/script"))
        else:
            self.out = os.path.join(self.tmp, self.stem + ".mjs")
            text = js
        with open(self.out + ".tmp", "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(self.out + ".tmp", self.out)
        return None


class Dev:
    def __init__(self, files, port):
        self.tmp = tempfile.mkdtemp(prefix="klardev")
        self.entries = [Entry(f, self.tmp) for f in files]
        self.port = port
        self.errors = None
        self.child = None
        self.child_port = None
        self.clients = []
        self.lock = threading.Lock()
        self.dirs = sorted({os.path.dirname(e.path) for e in self.entries})

    # ---------------------------------------------------------------- building and the backend process
    def watched(self):
        files = {}
        for d in self.dirs:
            for root, dirs, names in os.walk(d):
                dirs[:] = [x for x in dirs if not x.startswith(".") and x not in ("node_modules", "__pycache__")]
                for n in names:
                    if n.endswith(".klar"):
                        p = os.path.join(root, n)
                        try:
                            files[p] = os.stat(p).st_mtime_ns
                        except OSError:
                            pass
        return files

    def rebuild(self):
        t0 = time.perf_counter()
        errs = [x for x in (e.build() for e in self.entries) if x]
        ms = 1000 * (time.perf_counter() - t0)
        with self.lock:
            self.errors = "\n".join(errs) if errs else None
        if errs:
            print(self.errors, file=sys.stderr)
            print(f"klar dev: build failed ({ms:.0f} ms); waiting for a fix", file=sys.stderr, flush=True)
            self.stop_child()
        else:
            print(f"klar dev: built {', '.join(os.path.basename(e.out) for e in self.entries)} in {ms:.0f} ms",
                  file=sys.stderr, flush=True)
            self.restart_child()
        self.notify()

    def backend(self):
        return next((e for e in self.entries if e.kind == "node"), None)

    def stop_child(self):
        c = self.child
        self.child = None
        if c and c.poll() is None:
            c.terminate()
            try:
                c.wait(3)
            except subprocess.TimeoutExpired:
                c.kill()

    def restart_child(self):
        self.stop_child()
        b = self.backend()
        if b is None:
            return
        self.child_port = free_port()
        env = dict(os.environ, PORT=str(self.child_port), KLAR_DEV="1", HOST="127.0.0.1")
        # The backend runs in its own folder, so relative paths in it (a database, files it serves) work the same
        # from wherever `klar dev` was started.
        self.child = subprocess.Popen(["node", b.out], env=env, cwd=os.path.dirname(b.path))

    def notify(self):
        with self.lock:
            for q in self.clients:
                q.put("reload")

    def watch(self):
        seen = self.watched()
        while True:
            time.sleep(0.25)
            now = self.watched()
            if now != seen:
                seen = now
                self.rebuild()

    # ---------------------------------------------------------------- the HTTP front
    def serve(self):
        dev = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def send(self, status, ctype, body, extra=()):
                data = body.encode("utf-8") if isinstance(body, str) else body
                self.send_response(status)
                self.send_header("content-type", ctype)
                self.send_header("content-length", str(len(data)))
                self.send_header("cache-control", "no-store")
                for k, v in extra:
                    self.send_header(k, v)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)

            def events(self):
                q = queue.Queue()
                with dev.lock:
                    dev.clients.append(q)
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("cache-control", "no-store")
                self.send_header("connection", "close")
                self.end_headers()
                try:
                    self.wfile.write(b": connected\n\n")
                    self.wfile.flush()
                    while True:
                        try:
                            ev = q.get(timeout=15)
                            self.wfile.write(f"event: {ev}\ndata: \n\n".encode())
                        except queue.Empty:
                            self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                except OSError:
                    pass
                finally:
                    with dev.lock:
                        dev.clients.remove(q)
                    self.close_connection = True

            def handle_any(self):
                # Only answer requests addressed to this machine by name: a page on another site that points a
                # hostname of its own at 127.0.0.1 (DNS rebinding) must not be able to read the app or its files.
                host = (self.headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()
                allowed = {"localhost", "127.0.0.1", "::1", (os.environ.get("HOST") or "127.0.0.1").lower()}
                if host not in allowed and not host.endswith(".localhost") and os.environ.get("HOST") != "0.0.0.0":
                    return self.send(403, "text/plain", "klar dev: unexpected Host header\n")
                path = self.path.split("?", 1)[0]
                if path == "/__klar/events":
                    return self.events()
                if path == "/__klar/ping":
                    return self.send(200, "text/plain", "ok")
                if path == "/__klar/icon.svg" and os.path.exists(ICON_PATH):
                    with open(ICON_PATH, "rb") as f:
                        return self.send(200, "image/svg+xml", f.read())
                with dev.lock:
                    errors = dev.errors
                if errors is not None:
                    page = ERROR_PAGE.format(errors=html_escape(errors), script=RELOAD_JS)
                    return self.send(500, "text/html; charset=utf-8", page)
                if dev.backend() is not None:
                    return self.proxy()
                return self.static(path)

            def static(self, path):
                from .stdpy.weblib import mime_type
                web = [e for e in dev.entries if e.kind == "web"]
                if not web:
                    return self.send(404, "text/plain", "klar dev: nothing to serve (give a std.ui app or a server)\n")
                root = os.path.dirname(web[0].path)
                if path == "/":
                    target = web[0].out
                else:
                    target = os.path.normpath(os.path.join(root, path.lstrip("/")))
                    if not target.startswith(root + os.sep) and target != root:
                        return self.send(403, "text/plain", "forbidden\n")
                    if any(part.startswith(".") for part in os.path.relpath(target, root).split(os.sep)):
                        return self.send(404, "text/plain", "not found\n")       # .env, .git, .klar
                if not os.path.isfile(target):
                    return self.send(404, "text/plain", "not found\n")
                with open(target, "rb") as f:
                    data = f.read()
                ctype = mime_type(target)
                if ctype.startswith("text/html"):
                    data = inject(data.decode("utf-8", "replace")).encode("utf-8")
                return self.send(200, ctype, data)

            def proxy(self):
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length) if length else None
                headers = {k: v for k, v in self.headers.items() if k.lower() not in ("accept-encoding", "connection")}
                deadline = time.time() + 10
                while True:
                    conn = http.client.HTTPConnection("127.0.0.1", dev.child_port, timeout=60)
                    try:
                        conn.request(self.command, self.path, body=body, headers=headers)
                        res = conn.getresponse()
                        break
                    except (ConnectionRefusedError, ConnectionResetError, OSError):
                        conn.close()
                        if time.time() > deadline or dev.child is None or dev.child.poll() is not None:
                            msg = "the backend is not running; see the terminal for its output"
                            page = ERROR_PAGE.format(errors=html_escape(msg), script=RELOAD_JS)
                            return self.send(502, "text/html; charset=utf-8", page)
                        time.sleep(0.05)
                ctype = res.getheader("content-type") or ""
                passthrough = [(k, v) for k, v in res.getheaders()
                               if k.lower() not in ("content-length", "transfer-encoding", "connection", "content-type")]
                if ctype.startswith("text/event-stream"):
                    self.send_response(res.status)
                    for k, v in passthrough:
                        self.send_header(k, v)
                    self.send_header("content-type", ctype)
                    self.send_header("connection", "close")
                    self.end_headers()
                    try:
                        while True:
                            chunk = res.read1(65536)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            self.wfile.flush()
                    except OSError:
                        pass
                    conn.close()
                    self.close_connection = True
                    return
                data = res.read()
                conn.close()
                if ctype.startswith("text/html"):
                    data = inject(data.decode("utf-8", "replace")).encode("utf-8")
                return self.send(res.status, ctype or "application/octet-stream", data, passthrough)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = handle_any

        server = http.server.ThreadingHTTPServer((os.environ.get("HOST") or "127.0.0.1", self.port), H)
        server.daemon_threads = True
        return server


def cmd_dev(files, port):
    if not files:
        print("usage: klar dev FILE... [--port N]", file=sys.stderr)
        return 2
    for f in files:
        if not os.path.isfile(f):
            print(f"klar dev: no such file {f}", file=sys.stderr)
            return 2
    dev = Dev(files, port)
    try:
        server = dev.serve()
    except OSError as ex:
        print(f"klar dev: cannot listen on port {port}: {ex}", file=sys.stderr)
        return 1
    dev.rebuild()
    if len([e for e in dev.entries if e.kind == "node"]) > 1:
        print("klar dev: only one backend can run; give one server file and any number of browser apps", file=sys.stderr)
        dev.stop_child()
        return 2
    if dev.backend() is not None and shutil.which("node") is None:
        print("klar dev: the backend needs Node.js (node was not found on PATH)", file=sys.stderr)
        return 1
    def stop(*_):
        raise KeyboardInterrupt                 # SIGTERM stops the backend too, like Ctrl-C
    import signal
    signal.signal(signal.SIGTERM, stop)
    threading.Thread(target=dev.watch, daemon=True).start()
    print(f"klar dev: http://127.0.0.1:{port}  (watching {len(dev.watched())} .klar files; Ctrl-C to stop)",
          file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        dev.stop_child()
        shutil.rmtree(dev.tmp, ignore_errors=True)
    return 0
