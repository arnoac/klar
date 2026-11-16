"""Production builds (DEPLOY.md): one executable with the browser app built in, --static, --docker, and how the
server behaves in production (health check, gzip and ETags, keep-alive, request log, clean shutdown) in the
native and Node backends; sqlite_migrate and SQLite's production defaults."""

import gzip
import http.client
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_web import EXAMPLES, NODE, ROOT, free_port, klar, wait_for, write  # noqa: E402

BOARD = os.path.join(EXAMPLES, "apps", "board")
CC = shutil.which("gcc") or shutil.which("cc") or shutil.which("clang")


def get(port, path, headers=None, conn=None):
    c = conn or http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path, headers=headers or {})
    r = c.getresponse()
    out = r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    if conn is None:
        c.close()
    return out


class Board:
    """Klar Board built for production and started on a free port in an empty folder."""

    def __init__(self, test, target):
        self.dir = tempfile.mkdtemp()
        test.addCleanup(shutil.rmtree, self.dir, True)
        if target == "native":
            out = os.path.join(self.dir, "board")
            b = klar("build", "--release", BOARD, "-o", out)
            cmd = [out]
        else:
            out = os.path.join(self.dir, "board.mjs")
            b = klar("build", "--target", "node", BOARD, "-o", out)
            cmd = [NODE, out]
        test.assertEqual(b.returncode, 0, b.stderr)
        self.build_output = b.stderr
        self.port = free_port()
        self.err = open(os.path.join(self.dir, "stderr.txt"), "w+")
        self.proc = subprocess.Popen(cmd, cwd=self.dir, stdout=subprocess.DEVNULL, stderr=self.err,
                                     env=dict(os.environ, PORT=str(self.port), KLAR_SHUTDOWN_SECONDS="5"))
        test.addCleanup(self.stop)
        wait_for(f"http://127.0.0.1:{self.port}/healthz")

    def stop(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        self.err.close()

    def log(self):
        self.err.flush()
        with open(self.err.name) as f:
            return f.read()


class ProductionServer:
    target = "native"

    def setUp(self):
        if self.target == "native" and not CC:
            self.skipTest("no C compiler")
        if self.target == "node" and not NODE:
            self.skipTest("no node")
        self.app = Board(self, self.target)

    def test_serves_the_built_in_app_gzipped_with_an_etag(self):
        port = self.app.port
        status, h, body = get(port, "/")              # no app.html on disk: it comes from inside the program
        self.assertEqual(status, 200)
        self.assertIn(b'<div id="app">', body)
        self.assertNotIn("content-encoding", h)
        self.assertEqual(h["cache-control"], "no-cache")
        status, hz, zbody = get(port, "/boards/3", {"accept-encoding": "gzip, br"})
        self.assertEqual((status, hz["content-encoding"]), (200, "gzip"))
        self.assertEqual(gzip.decompress(zbody), body)
        self.assertLess(len(zbody), len(body) / 2)
        status, h304, empty = get(port, "/", {"if-none-match": h["etag"]})
        self.assertEqual((status, empty), (304, b""))
        self.assertEqual(h304["etag"], h["etag"])
        self.assertIn("x-content-type-options", h)      # still a std.web response with its security headers

    def test_health_check_and_keep_alive(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=10)
        for _ in range(3):                              # three requests on one connection
            status, h, body = get(self.app.port, "/healthz", conn=conn)
            self.assertEqual((status, body), (200, b"ok\n"))
            self.assertEqual(h.get("cache-control"), "no-store")
        conn.close()

    def test_logs_requests_without_query_strings(self):
        get(self.app.port, "/healthz?token=secret")
        time.sleep(0.2)
        log = self.app.log()
        self.assertRegex(log, r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ GET /healthz 200 [\d.]+ms")
        self.assertNotIn("secret", log)

    def test_sigterm_stops_cleanly(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=10)
        get(self.app.port, "/healthz", conn=conn)       # an idle keep-alive connection must not hold it up
        t0 = time.time()
        self.app.proc.send_signal(signal.SIGTERM)
        self.assertEqual(self.app.proc.wait(10), 0)
        self.assertLess(time.time() - t0, 4)
        self.assertIn("SIGTERM received", self.app.log())
        conn.close()


class NativeProductionServer(ProductionServer, unittest.TestCase):
    target = "native"

    def test_build_says_what_it_built_in(self):
        self.assertIn("1 file(s) built in", self.app.build_output)
        self.assertIn("app.html", self.app.build_output)


class NodeProductionServer(ProductionServer, unittest.TestCase):
    target = "node"


SITE = """use std.web { web_get, web_serve, web_port, web_static }
use std.http { http_html }

fn main() uses io {
  let routes = [
    web_get("/", fn(req) => http_html(200, "<a href=\\"/about\\">about</a> <a href=\\"/search?q=x\\">s</a> <img src=\\"/public/logo.svg\\">")),
    web_get("/about", fn(req) => http_html(200, "<a href=\\"/\\">home</a> <a href=\\"https://example.com/\\">out</a>")),
    web_get("/public/*", web_static("public")),
  ]
  match web_serve(web_port(8080), routes) { Ok(_) => {}, Err(e) => print(e) }
}
"""


class Static(unittest.TestCase):
    def setUp(self):
        if not CC:
            self.skipTest("no C compiler")

    def test_a_std_web_site_becomes_files(self):
        src = write(SITE, "server.klar")
        d = os.path.dirname(src)
        os.makedirs(os.path.join(d, "public"))
        with open(os.path.join(d, "public", "logo.svg"), "w") as f:
            f.write("<svg/>")
        out = os.path.join(d, "dist")
        r = klar("build", "--static", d, "-o", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        files = sorted(os.path.relpath(os.path.join(p, n), out) for p, _, ns in os.walk(out) for n in ns)
        self.assertEqual(files, [".klar-static", "about/index.html", "index.html", "public/logo.svg"])
        self.assertIn("query string", r.stderr)                     # /search?q=x is listed as skipped
        r = klar("build", "--static", d, "-o", out)                  # rebuilding its own folder is fine
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_refuses_a_folder_it_did_not_make(self):
        src = write(SITE, "server.klar")
        mine = os.path.join(os.path.dirname(src), "mine")
        os.makedirs(mine)
        with open(os.path.join(mine, "notes.txt"), "w") as f:
            f.write("keep me")
        r = klar("build", "--static", src, "-o", mine)
        self.assertEqual(r.returncode, 2)
        self.assertTrue(os.path.exists(os.path.join(mine, "notes.txt")))

    def test_a_browser_app_becomes_index_and_404(self):
        out = tempfile.mkdtemp()
        r = klar("build", "--static", os.path.join(EXAMPLES, "apps", "showcase", "app.klar"), "-o",
                 os.path.join(out, "site"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(os.listdir(os.path.join(out, "site"))), [".klar-static", "404.html", "index.html"])


class Docker(unittest.TestCase):
    def test_writes_a_dockerfile_once(self):
        d = tempfile.mkdtemp()
        for f in ("server.klar", "app.klar", "shared.klar", "store.klar", "auth.klar"):
            shutil.copy(os.path.join(BOARD, f), d)
        r = klar("build", "--docker", d)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(d, "Dockerfile")) as f:
            text = f.read()
        self.assertIn("klarc build --release server.klar app.klar -o /app", text)
        self.assertRegex(text, r"ARG KLAR_REF=([0-9a-f]{40}|v[0-9.]+)\n")
        self.assertIn("USER app", text)
        self.assertIn("ENV HOST=0.0.0.0 PORT=8080", text)
        with open(os.path.join(d, "Dockerfile"), "w") as f:
            f.write("# mine\n")
        r = klar("build", "--docker", d)
        self.assertIn("kept the existing", r.stderr)
        with open(os.path.join(d, "Dockerfile")) as f:
            self.assertEqual(f.read(), "# mine\n")


MIGRATE = """use std.sqlite { sqlite_open, sqlite_migrate, sqlite_first }
use std.process { env_get }

fn main() uses io {
  let db = sqlite_open(unwrap_or(env_get("DB"), ":memory:")).unwrap()
  let v1 = ["create table t (id integer primary key)", "insert into t (id) values (1)"]
  print(sqlite_migrate(db, v1))
  print(sqlite_migrate(db, v1))
  print(sqlite_migrate(db, v1 + ["alter table t add column name text not null default 'x'", "insert into nowhere values (1)"]))
  let n: Option[int] = sqlite_first(db, "pragma user_version", []).unwrap()
  print(n)
  print(sqlite_migrate(db, ["create table t (id integer primary key)"]))
  let mode: Option[str] = sqlite_first(db, "pragma journal_mode", []).unwrap()
  print(mode)
}
"""

MIGRATE_OUT = """Ok(2)
Ok(0)
Err("migration 4: OperationalError: no such table: nowhere")
Some(3)
Err("the database has run 3 migrations but this program knows 1: is the program older than the database?")
Some("wal")
"""


class Sqlite(unittest.TestCase):
    def run_in(self, how):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        src = write(MIGRATE)
        env = dict(os.environ, DB=os.path.join(d, "x.db"), PYTHONPATH=ROOT)
        if how == "interp":
            return subprocess.run([sys.executable, "-m", "klar", "run", src], capture_output=True, text=True, env=env)
        if how == "node":
            out = os.path.join(d, "m.mjs")
            self.assertEqual(klar("build", "--target", "node", src, "-o", out).returncode, 0)
            return subprocess.run([NODE, out], capture_output=True, text=True, env=env)
        out = os.path.join(d, "m")
        self.assertEqual(klar("build", src, "-o", out).returncode, 0)
        return subprocess.run([out], capture_output=True, text=True, env=env)

    def test_migrate_and_wal_in_every_backend(self):
        for how in ("interp", "node", "native"):
            if how == "node" and not NODE or how == "native" and not CC:
                continue
            with self.subTest(how):
                r = self.run_in(how)
                self.assertEqual(r.stdout, MIGRATE_OUT, r.stderr)


FLOATS = """use std.json { json_encode }

fn main() uses io {
  for x in [0.1, 0.3, 1794799355.48275, 1.0 / 3.0, 5e-324, 1e22, 123456.789, 2.5e-8, 0.1 + 0.2] {
    print(x)
  }
  print(json_floats())
}
fn json_floats() -> str {
  return json_encode([0.1, 1e21, 1.0 / 3.0, 1794799355.48275])
}
"""


class Floats(unittest.TestCase):
    def test_native_prints_floats_like_the_interpreter(self):
        if not CC:
            self.skipTest("no C compiler")
        src = write(FLOATS)
        want = klar("run", src)
        self.assertEqual(want.returncode, 0, want.stderr)
        exe = os.path.join(os.path.dirname(src), "f")
        self.assertEqual(klar("build", src, "-o", exe).returncode, 0)
        got = subprocess.run([exe], capture_output=True, text=True)
        self.assertEqual(got.stdout, want.stdout)
        self.assertIn("0.30000000000000004", got.stdout)
        self.assertIn("5e-324", got.stdout)


if __name__ == "__main__":
    unittest.main()
