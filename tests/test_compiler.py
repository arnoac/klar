"""Compiled programs must behave exactly like the interpreter (output, errors, exit codes)."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(ROOT, "examples")
PROGRAMS = os.path.join(ROOT, "tests", "programs")
HAVE_CC = any(shutil.which(c) for c in ("tcc", "cc", "gcc", "clang"))
HAVE_OPT_CC = any(shutil.which(c) for c in ("cc", "gcc", "clang"))


def klar(*args, stdin=None, cwd=None):
    env = dict(os.environ, PYTHONPATH=ROOT)
    return subprocess.run([sys.executable, "-m", "klar", *args], capture_output=True, text=True,
                          input=stdin, env=env, cwd=cwd or ROOT)


def build(path, release=False):
    d = tempfile.mkdtemp()
    exe = os.path.join(d, "prog")
    r = klar("build", path, "-o", exe, *(["--release"] if release else []))
    return exe, r


def run_exe(exe, stdin=None, args=()):
    return subprocess.run([exe, *args], capture_output=True, text=True, input=stdin)


def write(src):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "main.klar")
    with open(p, "w") as f:
        f.write(src)
    return p


@unittest.skipUnless(HAVE_CC, "no C compiler available")
class CompiledMatchesInterpreter(unittest.TestCase):
    def check_same(self, path, stdin=None, release=False):
        interp = klar("run", path, stdin=stdin)
        exe, b = build(path, release)
        self.assertEqual(b.returncode, 0, b.stderr)
        comp = run_exe(exe, stdin)
        self.assertEqual(comp.stdout, interp.stdout)
        self.assertEqual(comp.returncode, interp.returncode)

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

    def test_feature_program(self):
        self.check_same(os.path.join(PROGRAMS, "features.klar"))

    @unittest.skipUnless(HAVE_OPT_CC, "no optimising C compiler")
    def test_feature_program_release(self):
        self.check_same(os.path.join(PROGRAMS, "features.klar"), release=True)

    def test_tuples_and_error_wrapping(self):
        self.check_same(os.path.join(PROGRAMS, "glue.klar"))

    def test_traits(self):
        for release in ((False, True) if HAVE_OPT_CC else (False,)):
            with self.subTest(release=release):
                self.check_same(os.path.join(PROGRAMS, "traits.klar"), release=release)

    def test_lists_updated_in_place_stay_immutable(self):
        path = os.path.join(PROGRAMS, "owned_lists.klar")
        for release in ((False, True) if HAVE_OPT_CC else (False,)):
            with self.subTest(release=release):
                self.check_same(path, release=release)
                exe, _ = build(path, release)
                self.assertIn("error[E401]", run_exe(exe).stderr)

    def test_runtime_errors_have_same_codes(self):
        cases = {
            "E401": "let xs = [1]\nprint(xs[3])",
            "E402": "let z = 0\nprint(1 / z)",
            "E403": 'panic("boom")',
            "E404": "assert 1 == 2",
            "E405": 'let m = ["a": 1]\nprint(m["b"])',
        }
        for code, body in cases.items():
            with self.subTest(code=code):
                p = write("fn main() uses io {\n print(\"before\")\n" + body + "\n}\n")
                interp = klar("run", p)
                exe, b = build(p)
                self.assertEqual(b.returncode, 0, b.stderr)
                comp = run_exe(exe)
                self.assertEqual(comp.returncode, 1)
                self.assertEqual(comp.stdout, "before\n")
                self.assertIn(f"error[{code}]", comp.stderr)
                self.assertIn(f"error[{code}]", interp.stderr)

    def test_overflow_is_reported(self):
        p = write("fn main() uses io {\n var x = 1\n for i in range(0, 70) { x = x * 2 }\n print(x)\n}\n")
        exe, _ = build(p)
        comp = run_exe(exe)
        self.assertEqual(comp.returncode, 1)
        self.assertIn("error[E408]", comp.stderr)

    def test_deep_recursion(self):
        p = write("fn depth(n: int) -> int {\n if n == 0 { return 0 }\n return 1 + depth(n - 1)\n}\n"
                  "fn main() uses io { print(depth(1000000)) }\n")
        exe, _ = build(p)
        self.assertEqual(run_exe(exe).stdout, "1000000\n")

    def test_args_and_files(self):
        d = tempfile.mkdtemp()
        target = os.path.join(d, "out.txt")
        p = write('fn main() uses io {\n let a = args()\n print(a)\n'
                  ' print(write_file(a[0], "data"))\n print(read_file(a[0]))\n print(is_err(read_file(a[0] + "x")))\n}\n')
        exe, _ = build(p)
        self.assertEqual(run_exe(exe, args=[target]).stdout, f'["{target}"]\nOk(())\nOk("data")\ntrue\n')

    def test_extern_is_rejected_clearly(self):
        p = write('extern "math.hypot" fn hypot(x: float, y: float) -> float\nfn main() uses io { print(hypot(3.0, 4.0)) }\n')
        exe, b = build(p)
        self.assertNotEqual(b.returncode, 0)
        self.assertIn("E501", b.stderr)


if __name__ == "__main__":
    unittest.main()
