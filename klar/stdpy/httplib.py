import json as _json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Requests are accepted on threads so that live-update streams (web_events) can stay open, but Klar handlers
# run one at a time under this lock: the interpreter is single-threaded.
_LOCK = threading.RLock()


def _baton():
    from .. import tasks
    return tasks.RUNTIME.cond if tasks.RUNTIME is not None else _LOCK
_TOPICS = {}        # topic -> set of queues, one per open event stream


def publish(topic, data):
    msg = "".join(f"data: {line}\n" for line in str(data).split("\n")) + "\n"
    for q in list(_TOPICS.get(topic, ())):
        q.put(msg)


MAX_HEAD = 65536           # request line plus headers
MAX_CONNECTIONS = 1024     # open connections at once; more are answered 503
TIMEOUT = 30               # seconds a client may take to send its request


def max_body():
    """The largest request body accepted (KLAR_MAX_BODY bytes, default 10 MiB); larger ones get 413."""
    try:
        n = int(os.environ.get("KLAR_MAX_BODY") or 0)
    except ValueError:
        n = 0
    return n if n > 0 else 10 * 1024 * 1024


def bad_header(text, name=False):
    """A header name or value that would end the header line early (response splitting) or is not a token."""
    if "\r" in text or "\n" in text or "\0" in text:
        return True
    return name and (not text or any(c <= " " or c == ":" or ord(c) >= 127 for c in text))


def request(method, url, headers, body):
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise ValueError(f"unknown url type: {url!r} (http_request only fetches http:// and https:// URLs)")
    for k, v in headers.items():
        if bad_header(k, True) or bad_header(v):
            raise ValueError(f"invalid header {k!r}: names are tokens and values cannot contain line breaks")
    data = body.encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method, headers=dict(headers))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return {"status": r.status, "headers": {k.lower(): v for k, v in r.headers.items()},
                    "body": r.read().decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "headers": {k.lower(): v for k, v in e.headers.items()},
                "body": e.read().decode("utf-8", "replace")}


def serve(port, handler, max_requests):
    class H(BaseHTTPRequestHandler):
        timeout = TIMEOUT

        def _refuse(self, status):
            self.send_response(status)
            self.send_header("content-type", "text/plain; charset=utf-8")
            self.send_header("content-length", "0")
            self.send_header("connection", "close")
            self.end_headers()
            self.close_connection = True

        def _handle(self):
            parsed = urllib.parse.urlsplit(self.path)
            if "Transfer-Encoding" in self.headers:
                return self._refuse(501)
            raw_length = (self.headers.get("Content-Length") or "0").strip()
            if not raw_length.isdigit() or not raw_length.isascii():
                return self._refuse(400)
            length = int(raw_length)
            if length > max_body():
                return self._refuse(413)
            body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
            req = {
                "method": self.command,
                "path": parsed.path,
                "query": {k: v[-1] for k, v in urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items()},
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": body,
                "params": {},
            }
            with _baton():
                try:
                    resp = handler(req)
                except Exception as e:  # a Klar runtime error inside the handler
                    msg = getattr(e, "render", None)
                    text = msg() if msg else f"{type(e).__name__}: {e}"
                    print(text, file=sys.stderr)
                    resp = {"status": 500, "headers": {"content-type": "text/plain"}, "body": "internal error\n"}
                sys.stdout.flush()
            headers = dict(resp["headers"])
            if "x-klar-stream" in headers:
                return self._stream(headers["x-klar-stream"])
            status = resp["status"]
            payload = resp["body"].encode("utf-8")
            path = headers.pop("x-klar-file", None)
            if path is not None:
                try:
                    with open(path, "rb") as f:
                        payload = f.read()
                except OSError:
                    status, payload = 404, b"not found\n"
                    headers = {"content-type": "text/plain; charset=utf-8"}
            self.send_response(status)
            headers.setdefault("content-type", "text/plain; charset=utf-8")
            for k, v in headers.items():
                if bad_header(k, True):
                    print("klar: dropped a response header with an invalid name", file=sys.stderr)
                    continue
                for line in v.split("\n") if k == "set-cookie" else [v]:
                    if bad_header(line):
                        print(f"klar: dropped response header `{k}`: its value contains a line break", file=sys.stderr)
                        continue
                    self.send_header(k, line)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _stream(self, topic):
            q = queue.Queue()
            _TOPICS.setdefault(topic, set()).add(q)
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("cache-control", "no-store")
            self.end_headers()
            self.close_connection = True
            try:
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
                while True:
                    try:
                        msg = q.get(timeout=15)
                    except queue.Empty:
                        msg = ": ping\n\n"
                    self.wfile.write(msg.encode("utf-8"))
                    self.wfile.flush()
            except OSError:
                pass
            finally:
                _TOPICS[topic].discard(q)

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = _handle

        def log_message(self, *args):
            pass

    class Server(ThreadingHTTPServer):
        active = 0
        guard = threading.Lock()

        def process_request(self, request, client_address):
            with self.guard:
                busy = self.active >= MAX_CONNECTIONS
                if not busy:
                    self.active += 1
            if busy:
                try:
                    request.sendall(b"HTTP/1.0 503 Service Unavailable\r\ncontent-length: 0\r\n\r\n")
                except OSError:
                    pass
                self.shutdown_request(request)
                return
            super().process_request(request, client_address)

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                with self.guard:
                    self.active -= 1

    server = Server((os.environ.get("HOST") or "127.0.0.1", port), H)
    server.daemon_threads = max_requests <= 0    # with a request limit, wait for the last replies before returning
    try:
        if max_requests > 0:
            for _ in range(max_requests):
                server.handle_request()
        else:
            server.serve_forever()
    finally:
        server.server_close()
