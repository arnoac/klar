"""WebAssembly: `klar build --target wasm` and `extern "wasm:module.fn"` must behave like the other backends."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from klar.wasm import find_wasm_cc     # noqa: E402

EXAMPLES = os.path.join(ROOT, "examples")
PROGRAMS = os.path.join(ROOT, "tests", "programs")
HAVE = bool(shutil.which("node")) and find_wasm_cc() is not None


def klar(*args, stdin=None, cwd=None):
    env = dict(os.environ, PYTHONPATH=ROOT)
    return subprocess.run([sys.executable, "-m", "klar", *args], capture_output=True, text=True, input=stdin,
                          env=env, cwd=cwd or ROOT)


def write(files):
    d = tempfile.mkdtemp()
    for name, src in files.items():
        with open(os.path.join(d, name), "w") as f:
            f.write(src)
    return d


def build_wasm(path):
    out = os.path.join(tempfile.mkdtemp(), "prog")
    r = klar("build", "--target", "wasm", path, "-o", out)
    return out + ".mjs", r


def node(js, stdin=None, args=()):
    return subprocess.run(["node", js, *args], capture_output=True, text=True, input=stdin)


@unittest.skipUnless(HAVE, "needs node and clang with the wasm32 target + wasm-ld")
class WasmPrograms(unittest.TestCase):
    def check_same(self, path, stdin=None):
        interp = klar("run", path, stdin=stdin)
        js, b = build_wasm(path)
        self.assertEqual(b.returncode, 0, b.stderr)
        r = node(js, stdin)
        self.assertEqual(r.stdout, interp.stdout)
        self.assertEqual(r.returncode, interp.returncode, r.stderr)
        return r

    def test_examples(self):
        for f in sorted(os.listdir(EXAMPLES)):
            if f.endswith(".klar"):
                with self.subTest(example=f):
                    inp = os.path.join(EXAMPLES, f[:-5] + ".input")
                    stdin = open(inp).read() if os.path.exists(inp) else None
                    self.check_same(os.path.join(EXAMPLES, f), stdin)

    def test_feature_programs(self):
        for f in ("features.klar", "traits.klar", "owned_lists.klar", "owned_maps.klar", "json_auto.klar", "web_features.klar"):
            with self.subTest(program=f):
                self.check_same(os.path.join(PROGRAMS, f))

    def test_runtime_errors_and_args(self):
        d = write({"main.klar": """
fn deep(n: int) -> int {
  if n == 0 { return 0 }
  return 1 + deep(n - 1)
}
fn main() uses io {
  let a = args()
  print(a)
  print(9223372036854775807 - 1)
  if a[0] == "deep" { print(deep(100000000)) }
  if a[0] == "ovf" { print(9223372036854775807 + len(a)) }
  print([1, 2][len(a) + 5])
}
"""})
        js, b = build_wasm(os.path.join(d, "main.klar"))
        self.assertEqual(b.returncode, 0, b.stderr)
        r = node(js, args=["x", "y"])
        self.assertEqual(r.stdout, '["x", "y"]\n9223372036854775806\n')
        self.assertIn("error[E401]", r.stderr)
        self.assertIn("index 7 is out of bounds for length 2", r.stderr)
        self.assertEqual(r.returncode, 1)
        r = node(js, args=["ovf"])
        self.assertIn("error[E408]", r.stderr)
        self.assertEqual(r.returncode, 1)
        r = node(js, args=["deep"])
        self.assertIn("error[E406]", r.stderr)
        self.assertEqual(r.returncode, 1)

    def test_os_features_are_refused_at_build_time(self):
        for src, what in (('use std.fs { fs_read }\nfn main() uses io {\n  print(fs_read("x"))\n}\n', "fs_read"),
                          ('fn main() uses io {\n  print(read_file("x"))\n}\n', "read_file")):
            with self.subTest(what=what):
                d = write({"main.klar": src})
                _, b = build_wasm(os.path.join(d, "main.klar"))
                self.assertEqual(b.returncode, 1)
                self.assertIn("E501", b.stderr)
                self.assertIn(f"`{what}` cannot run in WebAssembly", b.stderr)


LIB = """
struct Stats { inside: int, mean: float, label: str }
enum Shape { Dot, Box(w: int, h: int) }

fn escape(cr: float, ci: float, limit: int) -> int {
  var zr = 0.0
  var zi = 0.0
  var i = 0
  while i < limit and zr * zr + zi * zi <= 4.0 {
    let t = zr * zr - zi * zi + cr
    zi = 2.0 * zr * zi + ci
    zr = t
    i = i + 1
  }
  return i
}
fn render(w: int, h: int, limit: int) -> list[int] {
  var out: list[int] = []
  for y in range(0, h) {
    for x in range(0, w) {
      out = push(out, escape(-2.2 + 3.0 * to_float(x) / to_float(w), -1.2 + 2.4 * to_float(y) / to_float(h), limit))
    }
  }
  return out
}
fn stats(counts: list[int], limit: int) -> Stats {
  var inside = 0
  var total = 0
  for c in counts {
    if c == limit { inside = inside + 1 }
    total = total + c
  }
  return Stats(inside: inside, mean: to_float(total) / to_float(len(counts)), label: "n={len(counts)}")
}
fn scale(xs: list[float], k: float) -> list[float] { return map(xs, fn(x) => x * k) }
fn shout(s: str) -> str { return s + "!" }
fn area(s: Shape) -> Option[int] {
  return match s {
    Dot => None,
    Box(w, h) => Some(w * h),
  }
}
fn big(n: int) -> int { return n * 1000000000000 }
fn is_even(n: int) -> bool { return n % 2 == 0 }
fn fail_at(i: int) -> int {
  let xs = [1, 2, 3]
  return xs[i]
}
fn hello(name: str) uses io { print("hello {name} from wasm") }
"""

APP = """
struct Stats { inside: int, mean: float, label: str }
enum Shape { Dot, Box(w: int, h: int) }

extern "wasm:lib.render" fn render(w: int, h: int, limit: int) -> list[int]
extern "wasm:lib.stats" fn stats(counts: list[int], limit: int) -> Stats
extern "wasm:lib.scale" fn scale(xs: list[float], k: float) -> list[float]
extern "wasm:lib.shout" fn shout(s: str) -> str
extern "wasm:lib.area" fn area(s: Shape) -> Option[int]
extern "wasm:lib.big" fn big(n: int) -> int
extern "wasm:lib.is_even" fn is_even(n: int) -> bool
extern "wasm:lib.fail_at" fn fail_at(i: int) -> int
extern "wasm:lib.hello" fn hello(name: str) uses io

fn main() uses io {
  print("before")
  let c = render(40, 20, 50)
  print(len(c))
  print(stats(c, 50))
  print(scale([1.5, 2.0], 2.0))
  print(shout("héllo wörld"))
  print(area(Box(3, 4)))
  print(area(Dot))
  print(big(123456789))
  print(is_even(4))
  hello("Klar")
  print(fail_at(1))
  print(fail_at(7))
}
"""
EXPECTED = """before
800
Stats(inside: 188, mean: 15.29, label: "n=800")
[3.0, 4.0]
héllo wörld!
Some(12)
None
123456789000000000000
true
hello Klar from wasm
2
"""


@unittest.skipUnless(HAVE, "needs node and clang with the wasm32 target + wasm-ld")
class WasmExterns(unittest.TestCase):
    def test_node_app_calls_wasm_module(self):
        d = write({"lib.klar": LIB, "app.klar": APP})
        out = os.path.join(d, "app.mjs")
        b = klar("build", "--target", "node", os.path.join(d, "app.klar"), "-o", out)
        self.assertEqual(b.returncode, 0, b.stderr)
        r = node(out)
        # big() overflows 64 bits in compiled code (E408) and is exact in the interpreter; check it separately
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("error[E408]", r.stderr)
        self.assertEqual(r.stdout, EXPECTED.split("123456789000000000000")[0])

    def test_same_results_in_interpreter_and_compiled(self):
        app = APP.replace("print(big(123456789))", "print(big(1234))")
        d = write({"lib.klar": LIB, "app.klar": app})
        out = os.path.join(d, "app.mjs")
        b = klar("build", "--target", "node", os.path.join(d, "app.klar"), "-o", out)
        self.assertEqual(b.returncode, 0, b.stderr)
        r = node(out)
        i = klar("run", os.path.join(d, "app.klar"))
        want = EXPECTED.replace("123456789000000000000", "1234000000000000")
        self.assertEqual(r.stdout, want)
        self.assertEqual(i.stdout, want)
        self.assertIn("error[E401]", r.stderr)
        self.assertIn("index 7 is out of bounds for length 3 (at lib.klar:", r.stderr)
        self.assertIn("error[E401]", i.stderr)
        self.assertEqual((r.returncode, i.returncode), (1, 1))

    def test_signature_mismatch_is_a_build_error_with_the_fix(self):
        d = write({"lib.klar": LIB, "app.klar": 'extern "wasm:lib.shout" fn shout(s: str) -> int\n'
                                                 'fn main() uses io {\n  print(shout("a"))\n}\n'})
        b = klar("build", "--target", "web", os.path.join(d, "app.klar"), "-o", os.path.join(d, "app.html"))
        self.assertEqual(b.returncode, 1)
        self.assertIn("E301", b.stderr)
        self.assertIn('declare it as: extern "wasm:lib.shout" fn shout(s: str) -> str', b.stderr)

    def test_web_build_embeds_the_module(self):
        d = write({"lib.klar": LIB, "app.klar": APP})
        out = os.path.join(d, "app.html")
        b = klar("build", "--target", "web", os.path.join(d, "app.klar"), "-o", out)
        self.assertEqual(b.returncode, 0, b.stderr)
        html = open(out).read()
        self.assertIn("$wasm_load($wasm_b64(", html)
        self.assertNotIn("node:", html.split("$wasm_load")[0][-200:])


if __name__ == "__main__":
    unittest.main()
