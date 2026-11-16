"""The JavaScript backend (Node and browser), std.html, std.web and std.ui.

Programs compiled with --target node must behave exactly like the interpreter. The full-stack example is
driven end to end: the Node server is started and the browser app is clicked through in Chromium (when
Playwright is installed).
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(ROOT, "examples")
PROGRAMS = os.path.join(ROOT, "tests", "programs")
NODE = shutil.which("node")


def klar(*args, stdin=None, cwd=None):
    env = dict(os.environ, PYTHONPATH=ROOT)
    return subprocess.run([sys.executable, "-m", "klar", *args], capture_output=True, text=True,
                          input=stdin, env=env, cwd=cwd or ROOT)


def write(src, name="main.klar"):
    d = tempfile.mkdtemp()
    p = os.path.join(d, name)
    with open(p, "w") as f:
        f.write(src)
    return p


def build_node(path):
    out = os.path.join(tempfile.mkdtemp(), "prog.mjs")
    r = klar("build", "--target", "node", path, "-o", out)
    return out, r


def run_node(mjs, stdin=None, args=(), env=None):
    return subprocess.run([NODE, mjs, *args], capture_output=True, text=True, input=stdin,
                          env=dict(os.environ, **(env or {})))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_for(url, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                return r.read().decode()
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f"server at {url} did not start")


def wait_call(base, name, body="null", timeout=10):
    """POST a typed call (std.api) once the server is up; returns the reply body."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            req = urllib.request.Request(f"{base}/api/{name}", method="POST", data=body.encode())
            with urllib.request.urlopen(req, timeout=1) as r:
                return r.read().decode()
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f"server at {base} did not start")


def call(base, name, body="null"):
    """A typed call (std.api): POST /api/<name> with a JSON body; returns (status, decoded reply)."""
    status, text, _ = http("POST", f"{base}/api/{name}", body)
    return status, json.loads(text) if text else None


def http(method, url, body=None):
    req = urllib.request.Request(url, method=method, data=body.encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)


@unittest.skipUnless(NODE, "node is not installed")
class NodeMatchesInterpreter(unittest.TestCase):
    def check_same(self, path, stdin=None):
        interp = klar("run", path, stdin=stdin)
        mjs, b = build_node(path)
        self.assertEqual(b.returncode, 0, b.stderr)
        js = run_node(mjs, stdin)
        self.assertEqual(js.stdout, interp.stdout, js.stderr)
        self.assertEqual(js.returncode, interp.returncode)

    def test_examples(self):
        for f in sorted(os.listdir(EXAMPLES)):
            if f.endswith(".klar"):
                with self.subTest(example=f):
                    inp = os.path.join(EXAMPLES, f[:-5] + ".input")
                    stdin = None
                    if os.path.exists(inp):
                        with open(inp) as fh:
                            stdin = fh.read()
                    self.check_same(os.path.join(EXAMPLES, f), stdin)

    def test_feature_programs(self):
        for f in ("features.klar", "web_features.klar", "web_security.klar", "owned_lists.klar", "owned_maps.klar", "owned_strings.klar", "json_auto.klar", "traits.klar", "glue.klar"):
            with self.subTest(program=f):
                self.check_same(os.path.join(PROGRAMS, f))

    def test_runtime_errors_have_codes(self):
        cases = {
            "E401": "let xs = [1, 2]\n  print(xs[5])",
            "E402": "let z = len([])\n  print(10 / z)",
            "E403": "let o: Option[int] = None\n  print(unwrap(o))",
            "E404": "assert 1 + 1 == 3",
            "E405": 'let m = ["a": 1]\n  print(m["b"])',
        }
        for code, body in cases.items():
            with self.subTest(code=code):
                mjs, b = build_node(write("fn main() uses io {\n  " + body + "\n}\n"))
                self.assertEqual(b.returncode, 0, b.stderr)
                r = run_node(mjs)
                self.assertEqual(r.returncode, 1)
                self.assertIn(f"error[{code}]", r.stderr)

    def test_ints_are_64_bit(self):
        src = """fn main() uses io {
  var x = 1
  for i in range(0, 62) { x = x * 2 }
  print(x)
  print(x / 3 + 9007199254740993)
  var h = 7
  for i in range(0, 1000) { h = (h * 3000000019 + 12345) % 2147483647 }
  print(h)
  print(x * 4)
}
"""
        p = write(src)
        mjs, b = build_node(p)
        r = run_node(mjs)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[:2], ["4611686018427387904", str(4611686018427387904 // 3 + 9007199254740993)])
        self.assertEqual(lines[2], klar("run", p).stdout.splitlines()[2])
        self.assertIn("error[E408]", r.stderr)

    def test_deep_recursion(self):
        src = "fn depth(n: int) -> int {\n  if n == 0 { return 0 }\n  return 1 + depth(n - 1)\n}\n" \
              "fn main() uses io {\n  print(depth(1000000))\n}\n"
        mjs, b = build_node(write(src))
        r = run_node(mjs)
        self.assertEqual(r.stdout.strip(), "1000000", r.stderr)

    def test_args_files_and_stdin(self):
        src = """fn main() uses io {
  print(args())
  match write_file(args()[0], "line 1\\nline 2") { Ok(_) => print("written"), Err(e) => print(e) }
  print(read_file(args()[0]))
  print(read_file("/no/such/file"))
  var total = 0
  while true {
    match read_line() {
      Some(l) => { total = total + unwrap_or(parse_int(l), 0) }
      None => break,
    }
  }
  print(total)
}
"""
        p = write(src)
        target = os.path.join(tempfile.mkdtemp(), "f.txt")
        interp = subprocess.run([sys.executable, "-m", "klar", "run", p, target], capture_output=True, text=True,
                                input="1\n2\n39\n", env=dict(os.environ, PYTHONPATH=ROOT))
        mjs, b = build_node(p)
        js = run_node(mjs, "1\n2\n39\n", [target])
        self.assertEqual(js.stdout, interp.stdout)
        self.assertIn("42", js.stdout)

    def test_javascript_interop(self):
        src = """extern "js:node:path.basename" fn base_name(p: str) -> str
extern "js:globalThis.Math.hypot" fn hypot(a: float, b: float) -> float
struct P { x: int, tags: list[str] }
extern "js:globalThis.JSON.parse" fn from_json(s: str) -> Result[P, str]
fn main() uses io {
  print(base_name("/a/b/c.txt"))
  print(hypot(3.0, 4.0))
  print(from_json(r"{\\"x\\": 5, \\"tags\\": [\\"q\\"]}"))
  print(is_err(from_json("nope")))
}
"""
        p = write(src)
        mjs, b = build_node(p)
        self.assertEqual(b.returncode, 0, b.stderr)
        self.assertEqual(run_node(mjs).stdout, 'c.txt\n5.0\nOk(P(x: 5, tags: ["q"]))\ntrue\n')
        r = klar("run", p)
        self.assertIn("E407", r.stderr)

    def test_python_externs_are_rejected(self):
        p = write('extern "math.hypot" fn hypot(a: float, b: float) -> float\nfn main() uses io { print(hypot(3.0, 4.0)) }\n')
        _, b = build_node(p)
        self.assertEqual(b.returncode, 1)
        self.assertIn("E501", b.stderr)

    def test_server_only_modules_are_rejected_in_the_browser(self):
        p = write('use std.fs { fs_read }\nfn main() uses io { print(fs_read("x")) }\n')
        r = klar("build", "--target", "web", p, "-o", os.path.join(tempfile.mkdtemp(), "a.html"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("E501", r.stderr)
        self.assertIn("browser", r.stderr)


class StdNamesDoNotLeak(unittest.TestCase):
    def test_unimported_std_names_are_free_for_user_code(self):
        src = """use std.html { Html, p, html_render }
fn header(n: int) -> int { return n + 1 }
fn main() uses io {
  let text = "a local named like std.html's text()"
  let input = header(1)
  print(html_render(p([], [])))
  print(text)
  print(input)
}
"""
        r = klar("run", write(src))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "<p></p>\na local named like std.html's text()\n2\n")

    def test_imported_names_still_clash(self):
        src = 'use std.html { text }\nfn main() uses io {\n  let text = 1\n  print(text)\n}\n'
        r = klar("check", write(src))
        self.assertEqual(r.returncode, 1)
        self.assertIn("E202", r.stdout + r.stderr)

    def test_example_apps_pass_their_tests(self):
        for app in ("todo_web/app.klar", "guestbook/main.klar"):
            with self.subTest(app=app):
                r = klar("test", os.path.join(EXAMPLES, "apps", app))
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn("0 failed", r.stdout)

    def test_json_decode_needs_a_known_type(self):
        src = 'use std.json { json_decode }\nfn main() uses io {\n  print(json_decode("1"))\n}\n'
        r = klar("check", write(src))
        self.assertEqual(r.returncode, 1)
        self.assertIn("E304", r.stdout + r.stderr)
        self.assertIn("json_decode", r.stdout + r.stderr)

    def test_live_updates_in_the_interpreter(self):
        port = free_port()
        d = tempfile.mkdtemp()
        env = dict(os.environ, PYTHONPATH=ROOT, PORT=str(port), TODO_DB=os.path.join(d, "t.db"))
        proc = subprocess.Popen([sys.executable, "-m", "klar", "run", os.path.join(EXAMPLES, "apps", "todo_web", "server.klar")],
                                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.kill)
        base = f"http://127.0.0.1:{port}"
        self.assertEqual(wait_call(base, "list_todos"), "[]")
        stream = urllib.request.urlopen(base + "/api/live", timeout=10)
        self.assertEqual(stream.readline(), b": connected\n")
        call(base, "add_todo", '"pushed"')
        lines = [stream.readline() for _ in range(2)]
        self.assertEqual(lines[1], b'data: [{"id":1,"title":"pushed","done":false}]\n')
        stream.close()

    def test_ui_apps_explain_they_need_a_browser(self):
        r = klar("run", os.path.join(EXAMPLES, "apps", "todo_web", "app.klar"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("--target web", r.stderr)


@unittest.skipUnless(NODE, "node is not installed")
class FullStackApps(unittest.TestCase):
    def start(self, path, env):
        mjs, b = build_node(path)
        self.assertEqual(b.returncode, 0, b.stderr)
        proc = subprocess.Popen([NODE, mjs], env=dict(os.environ, **env), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(proc.kill)
        return proc

    def test_guestbook_server(self):
        port = free_port()
        d = tempfile.mkdtemp()
        self.start(os.path.join(EXAMPLES, "apps", "guestbook", "main.klar"),
                   {"PORT": str(port), "GUESTBOOK_DB": os.path.join(d, "gb.db")})
        base = f"http://127.0.0.1:{port}"
        self.assertIn("No entries yet", wait_for(base + "/"))
        req = urllib.request.Request(base + "/sign", method="POST", data=b"name=Ann+%3Cb%3E&message=Hi+%26+bye")
        opener = urllib.request.build_opener(NoRedirect())
        try:
            opener.open(req)
            self.fail("expected a redirect")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 303)
            self.assertIn("name=Ann%20%3Cb%3E", e.headers["set-cookie"])
        page = wait_for(base + "/")
        self.assertIn("Ann &lt;b&gt;", page)
        self.assertIn("Hi &amp; bye", page)
        self.assertEqual(http("GET", base + "/missing")[0], 404)
        self.assertEqual(http("DELETE", base + "/")[0], 405)

    def test_todo_app_end_to_end(self):
        port = free_port()
        d = tempfile.mkdtemp()
        app = os.path.join(d, "app.html")
        b = klar("build", "--target", "web", os.path.join(EXAMPLES, "apps", "todo_web", "app.klar"), "-o", app)
        self.assertEqual(b.returncode, 0, b.stderr)
        self.start(os.path.join(EXAMPLES, "apps", "todo_web", "server.klar"),
                   {"PORT": str(port), "APP_DIR": d, "TODO_DB": os.path.join(d, "t.db")})
        base = f"http://127.0.0.1:{port}"
        self.assertEqual(wait_call(base, "list_todos"), "[]")
        self.assertEqual(call(base, "add_todo", '"from the API"'), (200, {"id": 1, "title": "from the API", "done": False}))
        self.assertEqual(call(base, "add_todo", '"  "'), (400, {"error": "title is required"}))
        self.assertEqual(call(base, "add_todo", '{"title": "x"}')[0], 400)        # the body must be an str
        self.assertEqual(call(base, "set_done", '{"id": 1, "done": true}'), (200, None))
        self.assertEqual(call(base, "set_done", '{"id": 9, "done": true}'), (404, {"error": "no such todo"}))
        self.assertEqual(http("GET", base + "/api/list_todos")[0], 405)
        self.assertIn('<div id="app">', http("GET", base + "/")[1])
        if subprocess.run([NODE, "-e", "require('playwright')"], capture_output=True).returncode != 0:
            self.skipTest("playwright is not installed")
        script = os.path.join(d, "drive.js")
        with open(script, "w") as f:
            f.write(BROWSER_SCRIPT)
        r = subprocess.run([NODE, script, base + "/"], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines(), [
            "loaded 1", "added 3 2 items left", "toggled 1 item left", "filtered 2", "deleted 2", "other tab 2",
            "errors 0"])
        todos = call(base, "list_todos")[1]
        self.assertEqual([(t["title"], t["done"]) for t in todos], [("from the API", True), ("Buy <milk>", True)])


@unittest.skipUnless(NODE, "node is not installed")
class DevServer(unittest.TestCase):
    def test_klar_dev_rebuilds_reloads_and_shows_errors(self):
        d = tempfile.mkdtemp()
        app_dir = os.path.join(d, "todo")
        shutil.copytree(os.path.join(EXAMPLES, "apps", "todo_web"), app_dir,
                        ignore=shutil.ignore_patterns("*.db", "*.html"))
        port = free_port()
        proc = subprocess.Popen([sys.executable, "-m", "klar", "dev", "server.klar", "app.klar", "--port", str(port)],
                                cwd=app_dir, env=dict(os.environ, PYTHONPATH=ROOT),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (proc.terminate(), proc.wait(10)))     # SIGTERM also stops its backend
        base = f"http://127.0.0.1:{port}"
        page = wait_for(base + "/")
        self.assertIn('<div id="app">', page)
        self.assertIn("/__klar/events", page)                      # the reload script was added
        self.assertIn('<link rel="icon" href="/__klar/icon.svg">', page)   # and the Klar tab icon
        icon = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "klar", "runtime", "klar_icon.svg")
        if os.path.exists(icon):                                   # the icon file is optional
            self.assertIn("<svg", wait_for(base + "/__klar/icon.svg"))
        self.assertEqual(wait_call(base, "list_todos"), "[]")
        self.assertTrue(os.path.exists(os.path.join(app_dir, "todos.db")))   # the backend runs in its folder
        events = urllib.request.urlopen(base + "/__klar/events", timeout=10)
        self.assertEqual(events.readline(), b": connected\n")
        time.sleep(0.3)
        with open(os.path.join(app_dir, "api.klar"), "a") as f:
            f.write("\nfn broken( {\n")
        self.assertEqual([events.readline() for _ in range(2)][1], b"event: reload\n")
        status, body, _ = http("GET", base + "/")
        self.assertEqual(status, 500)
        self.assertIn("the build failed", body)
        self.assertIn("api.klar:14", body)
        with open(os.path.join(app_dir, "api.klar")) as f:
            fixed = f.read().replace("\nfn broken( {\n", "")
        with open(os.path.join(app_dir, "api.klar"), "w") as f:
            f.write(fixed)
        deadline = time.time() + 10
        while http("GET", base + "/")[0] != 200:
            self.assertLess(time.time(), deadline, "the dev server never recovered")
            time.sleep(0.1)
        self.assertEqual(wait_call(base, "list_todos"), "[]")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


BROWSER_SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage();
  const other = await b.newPage();          // a second tab: it must follow along through live updates
  await other.goto(process.argv[2]);
  const errors = [];
  p.on('pageerror', e => errors.push(String(e)));
  p.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await p.goto(process.argv[2]);
  const rows = () => p.locator('ul.list li').count();
  await p.waitForSelector('ul.list li');
  console.log('loaded', await rows());
  for (const t of ['Buy <milk>', 'Walk']) {
    const n = await rows();
    await p.fill('#new-todo', t);
    await p.press('#new-todo', 'Enter');
    await p.waitForFunction(n => document.querySelectorAll('ul.list li').length === n + 1, n);
  }
  console.log('added', await rows(), await p.textContent('.badge'));
  await p.locator('ul.list li').nth(1).locator('input[type=checkbox]').check();
  await p.waitForFunction(() => document.querySelector('.badge').textContent === '1 item left');
  console.log('toggled', await p.textContent('.badge'));
  await p.click('button:has-text("Done")');
  await p.waitForFunction(() => document.querySelectorAll('ul.list li').length === 2);
  console.log('filtered', await rows());
  await p.click('button:has-text("All")');
  await p.locator('ul.list li').nth(2).locator('button').click();
  await p.waitForFunction(() => document.querySelectorAll('ul.list li').length === 2);
  await p.waitForTimeout(300);
  console.log('deleted', await rows());
  await other.waitForFunction(() => document.querySelectorAll('ul.list li').length === 2 &&
    document.querySelector('.badge').textContent === '0 items left');
  console.log('other tab', await other.locator('ul.list li').count());
  console.log('errors', errors.length);
  if (errors.length) console.error(errors.join('\n'));
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
"""


if __name__ == "__main__":
    unittest.main()
