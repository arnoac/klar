"""The standard library in native builds: same results as the interpreter and Node, and real servers."""

import http.server as httpserver
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_web import (EXAMPLES, NODE, PROGRAMS, FullStackApps, build_node, free_port, http, klar,  # noqa: E402
                      run_node, wait_for, write)

CC = shutil.which("tcc") or shutil.which("gcc") or shutil.which("cc") or shutil.which("clang")


def build_native(path):
    exe = os.path.join(tempfile.mkdtemp(), "prog")
    b = klar("build", path, "-o", exe)
    return exe, b


def run(cmd, args=()):
    return subprocess.run([*cmd, *args], capture_output=True, text=True, timeout=60)


@unittest.skipUnless(CC, "no C compiler")
class NativeStd(unittest.TestCase):
    def outputs(self, path, with_dir=False):
        """stdout of the interpreter, the native build and (if installed) Node, with temp dirs normalised."""
        exe, b = build_native(path)
        self.assertEqual(b.returncode, 0, b.stderr)
        runs = {"interp": [sys.executable, "-m", "klar", "run", path], "native": [exe]}
        if NODE:
            mjs, bn = build_node(path)
            self.assertEqual(bn.returncode, 0, bn.stderr)
            runs["node"] = [NODE, mjs]
        out = {}
        for name, cmd in runs.items():
            d = tempfile.mkdtemp()
            r = subprocess.run([*cmd, *([d] if with_dir else [])], capture_output=True, text=True, timeout=60,
                               env=dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            self.assertEqual(r.returncode, 0, f"{name}: {r.stderr}")
            out[name] = r.stdout.replace(d, "DIR")
        return out

    def assert_all_same(self, out):
        for name, text in out.items():
            self.assertEqual(text, out["interp"], f"{name} differs from the interpreter")

    def test_std_modules(self):
        out = self.outputs(os.path.join(PROGRAMS, "std_native.klar"), with_dir=True)
        self.assert_all_same(out)
        self.assertIn('Err("IntegrityError: UNIQUE constraint failed: t.name")', out["native"])

    def test_automatic_json_and_web_logic(self):
        for f in ("json_auto.klar", "web_features.klar", "web_security.klar"):
            with self.subTest(program=f):
                self.assert_all_same(self.outputs(os.path.join(PROGRAMS, f)))

    def test_json_errors_and_bad_regex_have_codes(self):
        p = write('use std.regex { regex_matches }\nfn main() uses io {\n  print(regex_matches("(", "x"))\n}\n')
        exe, b = build_native(p)
        self.assertEqual(b.returncode, 0, b.stderr)
        r = run([exe])
        self.assertEqual(r.returncode, 1)
        self.assertIn("error[E407]", r.stderr)
        self.assertIn(":3:", r.stderr)

    def test_http_client(self):
        class H(httpserver.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/old":
                    self.send_response(302)
                    self.send_header("Location", "/new?x=1")
                    self.end_headers()
                    return
                body = f"{self.path} {self.headers.get('X-Test', '-')}".encode()
                self.send_response(404 if self.path == "/missing" else 200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                n = int(self.headers["Content-Length"])
                body = self.rfile.read(n)
                self.send_response(201)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for part in (b"got ", body):
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(part), part))
                self.wfile.write(b"0\r\n\r\n")

            def log_message(self, *a):
                pass

        H.protocol_version = "HTTP/1.1"
        srv = httpserver.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        p = write(f'''use std.http {{ HttpResponse, http_get, http_request }}
fn show(r: Result[HttpResponse, str]) uses io {{
  match r {{
    Ok(resp) => print("{{resp.status}} {{resp.body}} {{get(resp.headers, "content-type")}}"),
    Err(e) => print("error"),
  }}
}}
fn main() uses io {{
  show(http_request("GET", "{base}/hello", ["X-Test": "yes"], ""))
  show(http_get("{base}/old"))
  show(http_get("{base}/missing"))
  show(http_request("POST", "{base}/p", ["content-type": "text/plain"], "data é"))
  show(http_get("http://127.0.0.1:1/nothing"))
}}
''')
        exe, b = build_native(p)
        self.assertEqual(b.returncode, 0, b.stderr)
        r = run([exe])
        self.assertEqual(r.stdout.splitlines(), [
            '200 /hello yes Some("text/plain")', '200 /new?x=1 - Some("text/plain")', '404 /missing - Some("text/plain")',
            "201 got data é None", "error"])

    def test_todo_api_server(self):
        exe, b = build_native(os.path.join(EXAMPLES, "apps", "todo_api", "main.klar"))
        self.assertEqual(b.returncode, 0, b.stderr)
        port = free_port()
        d = tempfile.mkdtemp()
        proc = subprocess.Popen([exe], env=dict(os.environ, PORT=str(port)), cwd=d,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.kill)
        base = f"http://127.0.0.1:{port}"
        self.assertEqual(wait_for(base + "/todos"), "[]")
        status, body, _ = http("POST", base + "/todos", '{"title": "native"}')
        self.assertEqual((status, json.loads(body)), (201, {"id": 1, "title": "native", "done": False}))
        self.assertEqual(http("POST", base + "/todos", "not json")[0], 400)
        self.assertEqual(http("DELETE", base + "/todos/42")[0], 404)
        self.assertIn("<h1>Todos (1)</h1>", http("GET", base + "/")[1])

    def test_live_updates(self):
        exe, b = build_native(os.path.join(EXAMPLES, "apps", "todo_web", "server.klar"))
        self.assertEqual(b.returncode, 0, b.stderr)
        port = free_port()
        d = tempfile.mkdtemp()
        proc = subprocess.Popen([exe], env=dict(os.environ, PORT=str(port), TODO_DB=os.path.join(d, "t.db")),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.kill)
        base = f"http://127.0.0.1:{port}"
        self.assertEqual(wait_for(base + "/api/todos"), "[]")
        stream = urllib.request.urlopen(base + "/api/live", timeout=10)
        self.assertEqual(stream.readline(), b": connected\n")
        http("POST", base + "/api/todos", '{"title": "pushed"}')
        lines = [stream.readline() for _ in range(2)]
        self.assertEqual(lines[1], b'data: [{"id":1,"title":"pushed","done":false}]\n')
        stream.close()

    def test_python_externs_still_need_the_interpreter(self):
        p = write('extern "math.hypot" fn hypot(x: float, y: float) -> float\nfn main() uses io { print(hypot(3.0, 4.0)) }\n')
        _, b = build_native(p)
        self.assertNotEqual(b.returncode, 0)
        self.assertIn("E501", b.stderr)


@unittest.skipUnless(CC and NODE, "needs a C compiler and node")
class NativeFullStackApps(FullStackApps):
    """The full-stack tests of test_web.py, with the backend compiled natively instead of for Node."""

    def start(self, path, env):
        exe, b = build_native(path)
        self.assertEqual(b.returncode, 0, b.stderr)
        proc = subprocess.Popen([exe], env=dict(os.environ, **env), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(proc.kill)
        return proc


del FullStackApps   # run once, in test_web.py

if __name__ == "__main__":
    unittest.main()
