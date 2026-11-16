"""Security defaults: hostile requests against real servers in every backend, compile-time checks, crypto limits,
`klar audit` against a stand-in vulnerability database."""

import http.server as httpserver
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_web import NODE, ROOT, build_node, free_port, klar, wait_for, write  # noqa: E402

CC = shutil.which("tcc") or shutil.which("gcc") or shutil.which("cc") or shutil.which("clang")

SERVER = '''use std.web { web_get, web_post, web_serve, web_port }
use std.http { HttpRequest, HttpResponse, http_text }

fn main() uses io {
  let routes = [
    web_get("/", fn(req) => http_text(200, "home")),
    web_post("/echo", fn(req) => http_text(200, "got {len(req.body)}")),
    web_get("/inject", fn(req) => HttpResponse(200, ["x-evil": "a\\r\\nset-cookie: pwned=1", "x-ok": "fine"], "body")),
    web_get("/badname", fn(req) => HttpResponse(200, ["bad name": "x"], "body")),
  ]
  match web_serve(web_port(8080), routes) { Ok(_) => {}, Err(e) => print("server error: {e}") }
}
'''


def raw(port, data, timeout=5):
    """Send raw bytes, return everything the server answers (b"" if it just closes)."""
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        s.sendall(data)
        out = b""
        while True:
            try:
                chunk = s.recv(65536)
            except (ConnectionResetError, socket.timeout):
                break
            if not chunk:
                break
            out += chunk
        return out
    finally:
        s.close()


def servers(path):
    """(name, argv) for each backend that can run a server here."""
    out = [("interp", [sys.executable, "-m", "klar", "run", path])]
    if CC:
        exe = os.path.join(tempfile.mkdtemp(), "srv")
        b = klar("build", path, "-o", exe)
        assert b.returncode == 0, b.stderr
        out.append(("native", [exe]))
    if NODE:
        mjs, b = build_node(path)
        assert b.returncode == 0, b.stderr
        out.append(("node", [NODE, mjs]))
    return out


class HostileRequests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = write(SERVER)
        cls.backends = servers(cls.path)

    def run_server(self, argv, env=None):
        port = free_port()
        e = dict(os.environ, PORT=str(port), PYTHONPATH=ROOT, KLAR_MAX_BODY="1000", **(env or {}))
        proc = subprocess.Popen(argv, env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (proc.kill(), proc.wait(), proc.stdout.close(), proc.stderr.close()))
        wait_for(f"http://127.0.0.1:{port}/")
        return port, proc

    def test_bad_requests_are_refused_and_the_server_keeps_serving(self):
        for name, argv in self.backends:
            with self.subTest(backend=name):
                port, proc = self.run_server(argv)
                # a negative or malformed Content-Length
                r = raw(port, b"POST /echo HTTP/1.1\r\nHost: x\r\nContent-Length: -5\r\n\r\nabc")
                self.assertRegex(r, rb"^HTTP/1\.[01] 400")
                r = raw(port, b"POST /echo HTTP/1.1\r\nHost: x\r\nContent-Length: 12abc\r\n\r\nabc")
                self.assertRegex(r, rb"^HTTP/1\.[01] 400")
                # a body over the limit (KLAR_MAX_BODY=1000 here)
                r = raw(port, b"POST /echo HTTP/1.1\r\nHost: x\r\nContent-Length: 5000\r\n\r\n" + b"a" * 5000)
                self.assertRegex(r, rb"^HTTP/1\.[01] 413")
                # a body within it
                r = raw(port, b"POST /echo HTTP/1.1\r\nHost: x\r\nContent-Length: 3\r\nConnection: close\r\n\r\nabc")
                self.assertIn(b"got 3", r)
                # chunked bodies are not accepted (no request smuggling through a proxy)
                r = raw(port, b"POST /echo HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n0\r\n\r\n")
                self.assertRegex(r, rb"^HTTP/1\.[01] (400|501)")
                # a request line split oddly, and a malformed percent escape in the path
                raw(port, b"GET\r\nHost: a b\r\n\r\n")
                r = raw(port, b"GET /%E0%A4%A HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
                self.assertRegex(r, rb"^HTTP/1\.[01] (400|404)")
                # oversized headers
                r = raw(port, b"GET / HTTP/1.1\r\nHost: x\r\nX-Big: " + b"a" * 100000 + b"\r\n\r\n")
                self.assertRegex(r, rb"^(HTTP/1\.[01] 4\d\d|)$|^HTTP/1\.[01] 4")
                # still alive
                self.assertEqual(wait_for(f"http://127.0.0.1:{port}/"), "home")
                self.assertIsNone(proc.poll())

    def test_header_injection_is_dropped(self):
        for name, argv in self.backends:
            with self.subTest(backend=name):
                port, proc = self.run_server(argv)
                r = raw(port, b"GET /inject HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
                head = r.split(b"\r\n\r\n")[0].lower()
                self.assertNotIn(b"pwned", head)
                self.assertIn(b"x-ok: fine", head)
                self.assertTrue(r.endswith(b"body"))
                r = raw(port, b"GET /badname HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
                self.assertNotIn(b"bad name", r.lower())
                self.assertTrue(r.endswith(b"body"))
                self.assertIsNone(proc.poll())

    def test_responses_carry_security_headers(self):
        for name, argv in self.backends:
            with self.subTest(backend=name):
                port, _ = self.run_server(argv)
                r = raw(port, b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n").lower()
                self.assertIn(b"x-content-type-options: nosniff", r)
                self.assertIn(b"x-frame-options: sameorigin", r)
                # a form posted from another site is refused
                r = raw(port, b"POST /echo HTTP/1.1\r\nHost: x\r\nSec-Fetch-Site: cross-site\r\nContent-Length: 1\r\n"
                              b"Connection: close\r\n\r\na")
                self.assertRegex(r, rb"^HTTP/1\.[01] 403")


class CompileTimeChecks(unittest.TestCase):
    def check(self, body):
        src = ("use std.sqlite { Database, SqlValue, SqlInt, SqlText, sqlite_open, sqlite_query, sqlite_exec }\n"
               "fn f(db: Database, id: int, name: str) -> int uses io {\n" + body + "\n  return 0\n}\n"
               "fn main() uses io { print(1) }\n")
        return klar("check", write(src))

    def test_sql_built_from_values_is_an_error(self):
        for body in ['  let r = sqlite_query(db, "select * from t where id = {id}", [])',
                     '  let r = sqlite_exec(db, "delete from t where name = \'" + name + "\'", [])',
                     '  let r = sqlite_query(db, "select * from t where id = " + str(id), [])']:
            with self.subTest(body=body):
                r = self.check(body)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("E313", r.stdout + r.stderr)

    def test_parameters_and_constant_sql_are_fine(self):
        for body in ['  let r = sqlite_query(db, "select * from t where id = ? and name = ?", [SqlInt(id), SqlText(name)])',
                     '  let r = sqlite_exec(db, "create table t (id integer, " + "name text)", [])',
                     '  let q = "select 1"\n  let r = sqlite_query(db, q, [])']:
            with self.subTest(body=body):
                r = self.check(body)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_explain(self):
        r = klar("explain", "E313")
        self.assertEqual(r.returncode, 0)
        self.assertIn("SQL injection", r.stdout)


class CryptoLimits(unittest.TestCase):
    def test_short_tokens_are_refused_everywhere(self):
        path = write('use std.crypto { crypto_token }\nfn main() uses io {\n  print(crypto_token(8))\n}\n')
        runs = {"interp": [sys.executable, "-m", "klar", "run", path]}
        if CC:
            exe = os.path.join(tempfile.mkdtemp(), "t")
            self.assertEqual(klar("build", path, "-o", exe).returncode, 0)
            runs["native"] = [exe]
        if NODE:
            mjs, b = build_node(path)
            self.assertEqual(b.returncode, 0, b.stderr)
            runs["node"] = [NODE, mjs]
        for name, argv in runs.items():
            with self.subTest(backend=name):
                r = subprocess.run(argv, capture_output=True, text=True, timeout=60, env=dict(os.environ, PYTHONPATH=ROOT))
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("at least", r.stderr.replace("is the least", "at least"))
                self.assertEqual(r.stdout, "")


@unittest.skipUnless(CC, "no C compiler")
class NativeMemorySafety(unittest.TestCase):
    def test_huge_sizes_stop_with_an_error_instead_of_overflowing(self):
        for expr in ['len(range(0, n))', 'len(repeat("ab", n))', 'len(repeat("abcdefgh", n / 2))']:
            with self.subTest(expr=expr):
                path = write('fn main() uses io {\n  let n = unwrap(parse_int("4611686018427387904"))\n'
                             f'  print({expr})\n}}\n')
                exe = os.path.join(tempfile.mkdtemp(), "big")
                b = klar("build", path, "-o", exe)
                self.assertEqual(b.returncode, 0, b.stderr)
                r = subprocess.run([exe], capture_output=True, text=True, timeout=60)
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn("error[E407]: out of memory", r.stderr)


class OsvStandIn:
    """A tiny server answering like api.osv.dev and GitHub's advisory API."""

    def __init__(self, vulns, klar_advisories=()):
        self.seen = []
        outer = self

        class H(httpserver.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, obj):
                data = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                q = json.loads(self.rfile.read(int(self.headers["content-length"])))
                outer.seen += q["queries"]
                res = []
                for item in q["queries"]:
                    key = item.get("commit") or (item["package"]["ecosystem"], item["package"]["name"], item["version"])
                    res.append({"vulns": [{"id": v["id"]} for v in vulns.get(key, [])]} if key in vulns else {})
                self.reply({"results": res})

            def do_GET(self):
                if self.path.startswith("/advisories"):
                    return self.reply(list(klar_advisories))
                vid = self.path.rsplit("/", 1)[1]
                for vs in vulns.values():
                    for v in vs:
                        if v["id"] == vid:
                            return self.reply(v)
                self.send_response(404)
                self.end_headers()

        self.server = httpserver.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Audit(unittest.TestCase):
    def project(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "klar.toml"), "w") as f:
            f.write('[package]\nname = "app"\nversion = "0.1.0"\n\n[python]\njinja2 = "2.4.1"\n')
        with open(os.path.join(d, "klar.lock"), "w") as f:
            f.write('[[package]]\nname = "textkit"\ngit = "https://example.com/textkit"\ncommit = "' + "ab" * 20 + '"\n\n'
                    '[[python]]\nname = "jinja2"\nversion = "2.4.1"\nhashes = ["sha256:00"]\n')
        with open(os.path.join(d, "package-lock.json"), "w") as f:
            json.dump({"lockfileVersion": 3, "packages": {
                "": {"name": "app"},
                "node_modules/lodash": {"version": "4.17.20", "integrity": "sha512-x"},
                "node_modules/a/node_modules/minimist": {"version": "1.2.0", "integrity": "sha512-y"},
                "node_modules/clean": {"version": "1.0.0"},
            }}, f)
        return d

    def audit(self, d, osv, *extra):
        env = dict(os.environ, PYTHONPATH=ROOT, KLAR_OSV_URL=osv.url, KLAR_ADVISORIES_URL=osv.url + "/advisories")
        return subprocess.run([sys.executable, "-m", "klar", "audit", *extra], cwd=d, env=env, capture_output=True,
                              text=True, timeout=60)

    def test_finds_vulnerable_packages_in_every_ecosystem(self):
        lodash = {"id": "GHSA-35jh-r3h4-6jhm", "aliases": ["CVE-2021-23337"], "summary": "Command injection in lodash",
                  "database_specific": {"severity": "HIGH"},
                  "affected": [{"package": {"name": "lodash", "ecosystem": "npm"},
                                "ranges": [{"type": "SEMVER", "events": [{"introduced": "0"}, {"fixed": "4.17.21"}]}]}]}
        jinja = {"id": "PYSEC-2019-217", "summary": "Sandbox escape", "affected": [
            {"package": {"name": "jinja2", "ecosystem": "PyPI"}, "ranges": [{"type": "ECOSYSTEM", "events": [
                {"introduced": "0"}, {"fixed": "2.10.1"}]}]}]}
        osv = OsvStandIn({("npm", "lodash", "4.17.20"): [lodash], ("PyPI", "jinja2", "2.4.1"): [jinja]},
                         [{"ghsa_id": "GHSA-klar-0001", "summary": "Old Klar bug", "severity": "medium",
                           "vulnerabilities": [{"vulnerable_version_range": "< 0.0.1", "patched_versions": "0.0.1"}]}])
        self.addCleanup(osv.close)
        d = self.project()
        r = self.audit(d, osv)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("lodash 4.17.20 (npm): GHSA-35jh-r3h4-6jhm / CVE-2021-23337", r.stdout)
        self.assertIn("klar add --python jinja2==2.10.1", r.stdout)
        self.assertIn("upgrade the npm package that depends on it", r.stdout)
        self.assertIn("npm package clean 1.0.0 has no integrity hash", r.stdout)
        self.assertNotIn("Old Klar bug", r.stdout)                # 0.0.1 and later are not affected
        kinds = sorted(q.get("package", {}).get("ecosystem", "commit") for q in osv.seen)
        self.assertEqual(kinds, ["PyPI", "commit", "npm", "npm", "npm"])
        j = json.loads(self.audit(d, osv, "--json").stdout)
        self.assertEqual(len(j["findings"]), 2)
        self.assertEqual(j["findings"][0]["severity"], "high")

    def test_clean_project_and_klar_advisories(self):
        osv = OsvStandIn({}, [{"ghsa_id": "GHSA-klar-0002", "summary": "std.web flaw", "severity": "high",
                               "html_url": "https://github.com/arnoac/klar/security/advisories/GHSA-klar-0002",
                               "vulnerabilities": [{"vulnerable_version_range": "< 999.0.0", "patched_versions": "999.0.0"}]}])
        self.addCleanup(osv.close)
        d = self.project()
        r = self.audit(d, osv)
        self.assertEqual(r.returncode, 1)
        self.assertIn("std.web flaw", r.stdout)
        self.assertIn("upgrade Klar", r.stdout)

    def test_unreachable_database_is_not_all_clear(self):
        d = self.project()
        env = dict(os.environ, PYTHONPATH=ROOT, KLAR_OSV_URL="http://127.0.0.1:9", KLAR_ADVISORIES_URL="http://127.0.0.1:9")
        r = subprocess.run([sys.executable, "-m", "klar", "audit"], cwd=d, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertIn("did not complete", r.stderr)


if __name__ == "__main__":
    unittest.main()
