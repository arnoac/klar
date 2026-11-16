"""What an agent reads first: QUICK.md (its programs must compile and run) and `klar doc` (library signatures)."""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from klar import stddoc  # noqa: E402


def klar(*args, cwd=None, input=None):
    env = dict(os.environ, PYTHONPATH=ROOT)
    return subprocess.run([sys.executable, "-m", "klar", *args], capture_output=True, text=True, cwd=cwd, env=env,
                          input=input, timeout=120)


class Quick(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        with open(os.path.join(ROOT, "klar", "QUICK.md"), encoding="utf-8") as f:
            self.text = f.read()

    def test_is_one_page(self):
        # The point of QUICK.md is to cost an agent far less than SPEC.md; keep it a fraction of the reference.
        self.assertLess(len(self.text.split()), 1400)

    def test_examples_work(self):
        blocks = re.findall(r"```klar\n(.*?)```", self.text, re.S)
        self.assertEqual(len(blocks), 2)
        # The first program imports a module of its own to show `use`; give it one.
        with open(os.path.join(self.tmp, "parser.klar"), "w") as f:
            f.write("enum Expr { Lit(n: int) }\nfn parse(s: str) -> Option[Expr] { return None }\n")
        console = os.path.join(self.tmp, "console.klar")
        with open(console, "w") as f:
            f.write(blocks[0])
        r = klar("run", console, input="5\nx\n7\n")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(r.stdout, "5 500\n7 700\ntotal 12.00\n")
        self.assertIn("error: bad number: x", r.stderr)
        self.assertEqual(klar("test", console).returncode, 0)
        app = os.path.join(self.tmp, "app.klar")
        with open(app, "w") as f:
            f.write(blocks[1])
        r = klar("build", "--target", "web", app, "-o", os.path.join(self.tmp, "app.html"))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

    def test_names_in_it_exist(self):
        # Every library function QUICK.md names in backticks must be a real one.
        names = set(re.findall(r"`((?:json|ui|process)_[a-z_]+)", self.text))
        self.assertTrue(names)
        for n in names:
            self.assertIsNotNone(stddoc.doc(n), n)


class Doc(unittest.TestCase):
    def test_every_module_listed_and_much_shorter_than_sources(self):
        text = klar("doc").stdout
        src = sum(os.path.getsize(os.path.join(stddoc.STD, m + ".klar")) for m in stddoc.modules())
        for m in stddoc.modules():
            self.assertIn(f"## std.{m}", text)
        self.assertLess(len(text), src / 3)

    def test_one_module_has_its_introduction_and_signatures(self):
        text = klar("doc", "std.ui").stdout
        self.assertIn("Keeping data across reloads", text)
        self.assertIn("fn ui_save[Msg](key: str, data: str) -> Cmd[Msg]   // Store text in the browser", text)
        self.assertIn("fn fs_read(path: str) -> Result[str, str] uses io", klar("doc", "fs").stdout)

    def test_same_signatures_share_a_line_and_helpers_are_hidden(self):
        html = stddoc.module_doc("html")
        self.assertRegex(html, r"fn div, span, p, a, .*\[M\]\(attrs: list\[Attr\[M\]\], children: list\[Html\[M\]\]\)")
        self.assertNotIn("html_parts", html)        # called inside std.html only, never named in SPEC

    def test_lookup_by_name(self):
        r = klar("doc", "json_decode")
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.startswith("std.json: fn json_decode[T](text: str) -> Result[T, str]"))
        r = klar("doc", "no_such_thing")
        self.assertEqual(r.returncode, 1)
        self.assertIn("std.ui", r.stderr)


if __name__ == "__main__":
    unittest.main()
