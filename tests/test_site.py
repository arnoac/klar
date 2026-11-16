"""The website (site/): its Klar code and tests, the tutorial app, the playground examples, and a full build of the
pages with every internal link checked. The playground's compiler (Pyodide) is not downloaded here."""

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def klar(*args):
    env = dict(os.environ, PYTHONPATH=ROOT)
    return subprocess.run([sys.executable, "-m", "klar", *args], capture_output=True, text=True, cwd=ROOT, env=env,
                          timeout=300)


class Site(unittest.TestCase):
    def test_site_code_and_tutorial_pass_their_tests(self):
        for f in ["site/md.klar", "site/highlight.klar", "site/stdref.klar", "site/tutorial/api.klar",
                  "site/tutorial/store.klar", "site/tutorial/app.klar"]:
            r = klar("test", f)
            self.assertEqual(r.returncode, 0, f + "\n" + r.stdout + r.stderr)
        r = klar("check", "site/tutorial/server.klar")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_playground_examples_check(self):
        files = sorted(glob.glob(os.path.join(ROOT, "site/playground/examples/*.klar")))
        self.assertGreater(len(files), 4)
        for f in files:
            r = klar("check", f)
            if f.endswith("mistakes.klar"):
                # this one is meant to show the checker's errors
                self.assertIn("E306", r.stdout + r.stderr)
                self.assertEqual(r.returncode, 1)
            else:
                self.assertEqual(r.returncode, 0, f + "\n" + r.stdout + r.stderr)
        r = klar("run", "site/examples/sample.klar")
        self.assertEqual(r.stdout, "area: 3.14159\narea: 6.0\n")

    def test_build_makes_every_page_and_links_resolve(self):
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, True)
        r = klar("run", "site/build.klar", out)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        mods = [os.path.basename(f)[:-5] for f in glob.glob(os.path.join(ROOT, "klar/std/*.klar"))]
        for page in ["index.html", "start.html", "tutorial.html", "reference.html", "playground.html", "404.html",
                     "std/index.html", "std/builtins.html", "site.css", "llms.txt", "playground/examples.json"] \
                + [f"std/{m}.html" for m in mods]:
            self.assertTrue(os.path.exists(os.path.join(out, page)), page)

        # every relative link and #anchor on the site leads somewhere
        for path in glob.glob(os.path.join(out, "**/*.html"), recursive=True):
            if path.endswith("404.html"):
                continue
            html = open(path, encoding="utf-8").read()
            ids = set(re.findall(r'id="([^"]+)"', html))
            for url in re.findall(r'(?:href|src)="([^"]+)"', html):
                if url.startswith(("http://", "https://")):
                    continue
                if url.startswith("#"):
                    self.assertIn(url[1:], ids, f"{path}: {url}")
                    continue
                target = os.path.normpath(os.path.join(os.path.dirname(path), url.split("#")[0]))
                if "/playground/" in target and target.endswith((".mjs", "klar.zip")):
                    continue
                self.assertTrue(os.path.exists(target), f"{path}: {url}")

        # the guides show the tutorial's real code, and the landing page the checker's real answer
        tutorial = open(os.path.join(out, "tutorial.html"), encoding="utf-8").read()
        self.assertNotIn("<!-- include", tutorial)
        self.assertIn("check_bookmark", tutorial)
        index = open(os.path.join(out, "index.html"), encoding="utf-8").read()
        self.assertIn("error[E306] mistake.klar:4:10", index)

        # llms.txt holds the whole reference and every module
        llms = open(os.path.join(out, "llms.txt"), encoding="utf-8").read()
        self.assertTrue(llms.startswith("# Klar\n"))
        self.assertEqual(llms.count("\n# "), 0)
        self.assertIn("## 10. Coming from another language".replace("## ", "### "), llms)
        for m in mods:
            self.assertIn(f"### std.{m}\n", llms)
        self.assertIn("fn fs_read(path: str) -> Result[str, str] uses io", llms)

        examples = json.load(open(os.path.join(out, "playground/examples.json"), encoding="utf-8"))
        self.assertTrue(all(e["code"] and e["title"] for e in examples))


if __name__ == "__main__":
    unittest.main()
