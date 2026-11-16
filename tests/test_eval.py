"""The agent evaluation in eval/ stays sound: every task's hidden tests pass on its reference solutions, in
Klar and (when tsc and Node type definitions are installed) in TypeScript, and fail on an empty program."""

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "eval", "harness"))

import klareval  # noqa: E402
from langs import find_node_types  # noqa: E402

HAVE_TS = shutil.which("tsc") is not None and shutil.which("node") is not None and find_node_types() is not None


def have_browser():
    if shutil.which("node") is None:
        return False
    import subprocess
    probe = "try { require('playwright') } catch { require(require('path').join(require('child_process')" \
            ".execSync('npm root -g').toString().trim(), 'playwright')) }"
    return subprocess.run(["node", "-e", probe], capture_output=True).returncode == 0


HAVE_BROWSER = have_browser()


def runnable_tasks():
    """Every task, except browser tasks when there is no headless browser to test them in."""
    out = []
    for task in klareval.task_names():
        _, mod = klareval.load_tests(task)
        if getattr(mod, "KIND", "cli") != "web" or HAVE_BROWSER:
            out.append(task)
    return out


class EvalTasks(unittest.TestCase):
    def grade_reference(self, task, lang):
        language = klareval.LANGS[lang]
        with tempfile.TemporaryDirectory() as d:
            ws = os.path.join(d, "ws")
            klareval.prepare(task, lang, ws, docs="spec")
            src = os.path.join(klareval.TASKS, task, "reference", lang)
            for f in os.listdir(src):
                shutil.copy(os.path.join(src, f), ws)
            self.assertTrue(os.path.isfile(os.path.join(ws, language.entry)))
            result = klareval.grade(task, lang, ws)
        failing = [f"{t['name']}: {t.get('message', '')[:200]}" for t in result["tests"] if not t["ok"]]
        self.assertTrue(result["build_ok"], result["build_log"][-1500:])
        self.assertEqual(failing, [])
        self.assertGreater(result["total"], 10)

    def test_klar_references_pass(self):
        for task in runnable_tasks():
            with self.subTest(task=task):
                self.grade_reference(task, "klar")

    @unittest.skipUnless(HAVE_TS, "needs tsc, node and @types/node")
    def test_typescript_references_pass(self):
        for task in runnable_tasks():
            with self.subTest(task=task):
                self.grade_reference(task, "ts")

    def test_missing_program_fails_and_feedback_names_it(self):
        with tempfile.TemporaryDirectory() as d:
            ws = os.path.join(d, "ws")
            klareval.prepare("ledger", "klar", ws, docs="spec")
            with open(os.path.join(ws, "main.klar"), "w") as f:
                f.write('fn main() uses io {\n  print("hi")\n}\n')
            result = klareval.grade("ledger", "klar", ws)
            self.assertTrue(result["build_ok"])
            self.assertEqual(result["passed"], 0)
            self.assertIn("example from task", klareval.feedback_text(result))
            with open(os.path.join(ws, "main.klar"), "w") as f:
                f.write("fn main() uses io {\n  print(nope)\n}\n")
            result = klareval.grade("ledger", "klar", ws)
            self.assertFalse(result["build_ok"])
            self.assertIn("E201", klareval.feedback_text(result))


if __name__ == "__main__":
    unittest.main()
