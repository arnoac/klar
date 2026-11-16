"""Fixes for what slowed AI agents down in the second and third evaluation pilots (eval/RESULTS.md). Programs
run in the interpreter, on Node and natively, and must give the same output everywhere."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_findings import run_everywhere  # noqa: E402
from test_web import NODE, klar, write  # noqa: E402


class StackAndCharacterBuiltins(unittest.TestCase):
    def test_pop_last_ord_chr(self):
        out, _ = run_everywhere(self, '''
fn main() uses io {
  var st = [1, 2, 3]
  st = push(st, 4)
  st = pop(st)
  st = pop(st)
  print(st)
  let a = [5, 6, 7]
  let b = pop(a)
  let c = push(b, 9)
  var d = b
  d = push(d, 10)
  print("{a} {b} {c} {d} {last(c)} {last(pop([1]))}")
  print("{ord("A")} {ord("é")} {ord("😀")} {chr(97)}{chr(233)}{chr(128512)}")
  var s = 0
  var stack = [0]
  var i = 0
  while i < 100000 {
    stack = push(stack, i)
    i = i + 1
  }
  while len(stack) > 0 {
    s = s + stack[len(stack) - 1]
    stack = pop(stack)
  }
  print(s)
}
''')
        self.assertEqual(out, "[1, 2]\n[5, 6, 7] [5, 6] [5, 6, 9] [5, 6, 10] Some(9) None\n65 233 128512 aé😀\n4999950000\n")

    def test_errors(self):
        for src, msg in (('print(ord("ab"))', "exactly one character"), ('print(chr(-1))', "not a Unicode"),
                         ('let e: list[int] = []\n  print(pop(e))', "pop on an empty list")):
            r = klar("run", write("fn main() uses io {\n  " + src + "\n}\n"))
            self.assertIn("E401", r.stderr + r.stdout)
            self.assertIn(msg, r.stderr + r.stdout)


class TupleAssignment(unittest.TestCase):
    def test_assign_existing_vars(self):
        out, _ = run_everywhere(self, '''
fn divmod(a: int, b: int) -> (int, int) {
  return (a / b, a % b)
}
fn main() uses io {
  var q = 0
  var r = 0
  (q, r) = divmod(17, 5)
  print("{q} {r}")
  var x = 1
  var y = 2
  (x, y) = (y, x)
  print("{x} {y}")
  (x, _) = (9, 8)
  print(x)
}
''')
        self.assertEqual(out, "3 2\n2 1\n9\n")

    def test_let_cannot_be_assigned(self):
        r = klar("check", write("fn main() uses io {\n  let a = 1\n  var b = 2\n  (a, b) = (3, 4)\n  print(b)\n}\n"))
        self.assertIn("E203", r.stdout + r.stderr)


UI_APP = '''
use std.ui { Next, ui_run }
use std.html { Html, div, text, button, input, on_click, on_input, value, id }
struct Model { clicks: int, draft: str }
enum Msg { Click, Typed(text: str) }
fn update(m: Model, msg: Msg) -> Next[Model, Msg] {
  return match msg {
    Click => Next(Model(m.clicks + 1, ""), []),
    Typed(t) => Next(m with (draft: t), []),
  }
}
fn view(m: Model) -> Html[Msg] {
  return div([], [input([id("box"), value(m.draft), on_input(fn(t) => Typed(t))]),
                  button([id("go"), on_click(Click)], [text("{m.clicks} {m.draft}")])])
}
fn main() uses io {
  ui_run(Next(Model(0, ""), []), update, view)
}
'''

UI_SCRIPT = r"""
const { chromium } = require("playwright");
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto("file://" + process.argv[2]);
  const read = () => page.evaluate(() => document.getElementById("go").textContent + "|" + document.getElementById("box").value);
  await page.fill("#box", "hi");
  console.log(await read());
  await page.click("#go");
  console.log(await read());
  await page.click("#go");
  console.log(await read());
  await browser.close();
})();
"""


class UiRendersRightAway(unittest.TestCase):
    """A test reading the page straight after a click sees the new view, and value("") clears an input."""

    @unittest.skipUnless(NODE, "node is not installed")
    def test_click_then_read(self):
        if subprocess.run([NODE, "-e", "require('playwright')"], capture_output=True).returncode != 0:
            self.skipTest("playwright is not installed")
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        html = os.path.join(d, "app.html")
        b = klar("build", "--target", "web", write(UI_APP), "-o", html)
        self.assertEqual(b.returncode, 0, b.stdout + b.stderr)
        script = os.path.join(d, "drive.js")
        with open(script, "w") as f:
            f.write(UI_SCRIPT)
        r = subprocess.run([NODE, script, html], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["0 hi|hi", "1 |", "2 |"])


if __name__ == "__main__":
    unittest.main()
