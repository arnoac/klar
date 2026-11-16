"""Klar Board (examples/apps/board): the larger example app, tested end to end.

The API is driven through every backend (interpreter, Node, native) with two users, and the browser app is
clicked through in Chromium with two signed-in users watching the same board.
"""

import http.cookiejar
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_web import EXAMPLES, NODE, ROOT, build_node, free_port, klar  # noqa: E402

APP = os.path.join(EXAMPLES, "apps", "board")
CC = shutil.which("tcc") or shutil.which("gcc") or shutil.which("cc") or shutil.which("clang")


class User:
    """A browser-like client: keeps its session cookie."""

    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"content-type": "application/json"})
        try:
            r = self.opener.open(req, timeout=20)
            return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")


def start(test, backend, env):
    d = tempfile.mkdtemp()
    test.addCleanup(shutil.rmtree, d, True)
    server = os.path.join(APP, "server.klar")
    if backend == "interp":
        cmd = [sys.executable, "-m", "klar", "run", server]
    elif backend == "node":
        mjs, b = build_node(server)
        test.assertEqual(b.returncode, 0, b.stderr)
        cmd = [NODE, mjs]
    else:
        exe = os.path.join(d, "board")
        b = klar("build", server, "-o", exe)
        test.assertEqual(b.returncode, 0, b.stderr)
        cmd = [exe]
    port = free_port()
    proc = subprocess.Popen(cmd, env=dict(os.environ, PYTHONPATH=ROOT, PORT=str(port), BOARD_DB=os.path.join(d, "b.db"),
                                          **env), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    test.addCleanup(proc.kill)
    base = f"http://127.0.0.1:{port}"
    end = time.time() + 30
    while True:
        try:
            urllib.request.urlopen(base + "/api/me", timeout=2)
            break
        except urllib.error.HTTPError:
            break               # any HTTP answer (401 here) means the server is up
        except OSError:
            if time.time() > end or proc.poll() is not None:
                raise AssertionError(f"{backend} server did not start")
            time.sleep(0.1)
    return base


def backends():
    out = ["interp"]
    if NODE:
        out.append("node")
    if CC:
        out.append("native")
    return out


class BoardApp(unittest.TestCase):
    def test_board_rules_and_ui_logic(self):
        for f in ("shared.klar", "app.klar"):
            with self.subTest(file=f):
                r = klar("test", os.path.join(APP, f))
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn(" 0 failed", r.stdout)

    def test_api_in_every_backend(self):
        for backend in backends():
            with self.subTest(backend=backend):
                self.check_api(start(self, backend, {}))

    def check_api(self, base):
        ada, bob, nobody = User(base), User(base), User(base)
        self.assertEqual(nobody.call("GET", "/api/boards")[0], 401)
        self.assertEqual(ada.call("POST", "/api/signup", {"name": "Ada", "password": "short"}),
                         (400, {"error": "passwords are at least 8 characters"}))
        self.assertEqual(ada.call("POST", "/api/signup", {"name": "Ada", "password": "correct horse"}),
                         (201, {"id": 1, "name": "ada"}))
        self.assertEqual(bob.call("POST", "/api/signup", {"name": "ada", "password": "correct horse"})[0], 409)
        self.assertEqual(bob.call("POST", "/api/signup", {"name": "bob", "password": "battery staple"})[0], 201)
        self.assertEqual(ada.call("GET", "/api/me"), (200, {"id": 1, "name": "ada"}))

        status, board = ada.call("POST", "/api/boards", {"name": "  Launch  "})
        self.assertEqual((status, board), (201, {"id": 1, "name": "Launch", "owner_id": 1}))
        for title in ("Write docs", "Fix login", "Ship"):
            self.assertEqual(ada.call("POST", "/api/boards/1/cards", {"title": title, "column": "Todo"})[0], 201)
        self.assertEqual(ada.call("POST", "/api/boards/1/cards", {"title": " ", "column": "Todo"}),
                         (400, {"error": "a title is required"}))
        self.assertEqual(ada.call("POST", "/api/boards/1/cards", {"title": "x", "column": "Later"})[0], 400)

        # bob cannot see the board (404, as if it did not exist) until ada adds him
        self.assertEqual(bob.call("GET", "/api/boards/1")[0], 404)
        self.assertEqual(bob.call("PUT", "/api/cards/1/move", {"column": "Done", "position": 0})[0], 404)
        self.assertEqual(ada.call("POST", "/api/boards/1/members", {"name": "carol"}), (404, {"error": "nobody is called carol"}))
        self.assertEqual(ada.call("POST", "/api/boards/1/members", {"name": "Bob"})[0], 200)
        self.assertEqual(bob.call("GET", "/api/boards")[1], [board])

        status, page = bob.call("PUT", "/api/cards/2/move", {"column": "Doing", "position": 0})
        self.assertEqual(status, 200)
        layout = sorted((c["column"], c["position"], c["title"]) for c in page["cards"])
        self.assertEqual(layout, [("Doing", 0, "Fix login"), ("Todo", 0, "Write docs"), ("Todo", 1, "Ship")])
        status, page = bob.call("PUT", "/api/cards/3/move", {"column": "Todo", "position": 0})
        self.assertEqual([c["title"] for c in sorted(page["cards"], key=lambda c: (c["column"], c["position"]))],
                         ["Fix login", "Ship", "Write docs"])

        status, card = bob.call("PUT", "/api/cards/2", {"title": "Fix login on Safari", "notes": " iOS only ", "assignee": 2})
        self.assertEqual((status, card["notes"], card["assignee"]), (200, "iOS only", 2))
        self.assertEqual(bob.call("PUT", "/api/cards/2", {"title": "x", "notes": "", "assignee": 99}),
                         (400, {"error": "only board members can be assigned"}))
        status, page = ada.call("DELETE", "/api/cards/1")
        self.assertEqual([c["title"] for c in page["cards"]], ["Fix login on Safari", "Ship"])
        self.assertEqual([e["text"] for e in page["events"]][:3],
                         ["deleted “Write docs”", "edited “Fix login on Safari”", "reordered “Ship”"])

        # sessions: wrong password, case-insensitive names, logging out ends the session
        self.assertEqual(nobody.call("POST", "/api/login", {"name": "ada", "password": "wrong horse"})[0], 401)
        self.assertEqual(nobody.call("POST", "/api/login", {"name": "ADA", "password": "correct horse"})[0], 200)
        self.assertEqual(ada.call("POST", "/api/logout")[0], 200)
        self.assertEqual(ada.call("GET", "/api/me")[0], 401)
        self.assertEqual(nobody.call("GET", "/api/me")[1]["name"], "ada")

    def test_live_updates_reach_members_only(self):
        base = start(self, "node" if NODE else "interp", {})
        ada, bob = User(base), User(base)
        ada.call("POST", "/api/signup", {"name": "ada", "password": "correct horse"})
        bob.call("POST", "/api/signup", {"name": "bob", "password": "battery staple"})
        ada.call("POST", "/api/boards", {"name": "Launch"})
        self.assertEqual(bob.call("GET", "/api/boards/1/live")[0], 404)
        ada.call("POST", "/api/boards/1/members", {"name": "bob"})
        stream = bob.opener.open(base + "/api/boards/1/live", timeout=10)
        self.assertEqual(stream.readline(), b": connected\n")
        ada.call("POST", "/api/boards/1/cards", {"title": "pushed", "column": "Done"})
        self.assertEqual(stream.readline(), b"\n")
        line = stream.readline()
        self.assertTrue(line.startswith(b"data: "), line)
        page = json.loads(line[6:])
        self.assertEqual([(c["title"], c["column"]) for c in page["cards"]], [("pushed", "Done")])
        stream.close()

    def test_report_fetches_boards_concurrently(self):
        base = start(self, "node" if NODE else "interp", {})
        ada = User(base)
        ada.call("POST", "/api/signup", {"name": "ada", "password": "correct horse"})
        for name in ("Alpha", "Beta", "Gamma"):
            ada.call("POST", "/api/boards", {"name": name})
        ada.call("POST", "/api/boards/2/cards", {"title": "one", "column": "Doing"})
        r = subprocess.run([sys.executable, "-m", "klar", "run", os.path.join(APP, "report.klar"), base, "ada"],
                           input="correct horse\n", capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, PYTHONPATH=ROOT))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [
            "3 boards for ada", "Alpha: 0 to do, 0 doing, 0 done", "Beta: 0 to do, 1 doing, 0 done",
            "Gamma: 0 to do, 0 doing, 0 done"])

    @unittest.skipUnless(NODE, "node is not installed")
    def test_two_people_in_the_browser(self):
        if subprocess.run([NODE, "-e", "require('playwright')"], capture_output=True).returncode != 0:
            self.skipTest("playwright is not installed")
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        b = klar("build", "--target", "web", os.path.join(APP, "app.klar"), "-o", os.path.join(d, "app.html"))
        self.assertEqual(b.returncode, 0, b.stderr)
        base = start(self, "native" if CC else "node", {"APP_DIR": d})
        script = os.path.join(d, "drive.js")
        with open(script, "w") as f:
            f.write(BROWSER_SCRIPT)
        r = subprocess.run([NODE, script, base], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines(), [
            "redirected to login", "names are 2 to 30 characters", "signed up", "cards 4", "moved 2", "bob sees board",
            "ada sees bob's edit", "ada sees bob's move", "dragged", "search 1", "after logout login", "errors 0"])


BROWSER_SCRIPT = r"""
const { chromium } = require("playwright");
const base = process.argv[2];
(async () => {
  const browser = await chromium.launch();
  const errors = [];
  const watch = p => {
    p.on("pageerror", e => errors.push(String(e)));
    p.on("console", m => { if (m.type() === "error" && !m.text().includes("401")) errors.push(m.text()); });
  };
  const page = await (await browser.newContext()).newPage();
  watch(page);
  await page.goto(base + "/");
  await page.waitForURL("**/login");
  console.log("redirected to login");
  await page.click("text=New here? Create an account");
  await page.fill("#name", "x");
  await page.fill("#password", "hunter22hunter");
  await page.click("button[type=submit]");
  console.log((await page.textContent(".status .alert-title")).trim());
  await page.fill("#name", "ada");
  await page.click("button[type=submit]");
  await page.waitForSelector("text=Your boards");
  console.log("signed up");
  await page.fill("#new-board", "Launch plan");
  await page.click("text=Create board");
  await page.waitForSelector("h1:has-text('Launch plan')");
  for (const t of ["Write the announcement", "Fix login on Safari", "Record the demo"]) {
    await page.fill("[aria-label='new card in To do']", t);
    await page.press("[aria-label='new card in To do']", "Enter");
    await page.waitForSelector(`.kcard:has-text('${t}')`);
  }
  await page.fill("[aria-label='new card in Doing']", "Benchmarks page");
  await page.press("[aria-label='new card in Doing']", "Enter");
  await page.waitForSelector(".kcard:has-text('Benchmarks page')");
  console.log("cards", await page.locator(".kcard").count());
  await page.locator(".kcard", { hasText: "Fix login on Safari" }).locator("[aria-label='move right']").click();
  await page.waitForSelector("[data-column='Doing'] .kcard:has-text('Fix login on Safari')");
  console.log("moved", await page.locator("[data-column='Doing'] .kcard").count());

  const bob = await (await browser.newContext()).newPage();
  watch(bob);
  await bob.goto(base + "/login");
  await bob.click("text=New here? Create an account");
  await bob.fill("#name", "bob");
  await bob.fill("#password", "battery staple");
  await bob.click("button[type=submit]");
  await bob.waitForSelector("text=Your boards");
  await page.fill("[aria-label='member name']", "bob");
  await page.press("[aria-label='member name']", "Enter");
  await page.waitForSelector(".card:has(h3:has-text('Members')) li:has-text('bob')");
  await bob.reload();
  await bob.click("text=Launch plan");
  await bob.waitForSelector(".kcard:has-text('Record the demo')");
  console.log("bob sees board");
  await bob.click("button.title:has-text('Write the announcement')");
  await bob.fill("#edit-notes", "Blog post + mailing list");
  await bob.selectOption("#edit-assignee", { label: "bob" });
  await bob.click("[role=dialog] button:text-is('Save')");
  await page.waitForSelector(".kcard:has-text('@bob')", { timeout: 5000 });
  console.log("ada sees bob's edit");
  await bob.locator(".kcard", { hasText: "Record the demo" }).locator("[aria-label='move right']").click();
  await page.waitForSelector("[data-column='Doing'] .kcard:has-text('Record the demo')", { timeout: 5000 });
  console.log("ada sees bob's move");
  // drag "Benchmarks page" from Doing onto the first card of To do
  await page.locator(".kcard", { hasText: "Benchmarks page" }).dragTo(page.locator("[data-column='To do'] .kcard").first());
  await bob.waitForSelector("[data-column='To do'] .kcard:first-child:has-text('Benchmarks page')", { timeout: 5000 });
  console.log("dragged");
  await page.fill("#search", "safari");
  await page.waitForFunction(() => document.querySelectorAll(".kcard").length === 1, null, { timeout: 5000 });
  console.log("search", await page.locator(".kcard").count());
  await page.click("text=Log out");
  await page.waitForURL("**/login");
  await page.goto(base + "/boards/1");
  await page.waitForURL("**/login");
  console.log("after logout login");
  console.log("errors", errors.length, ...errors.slice(0, 3));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
"""

if __name__ == "__main__":
    unittest.main()
