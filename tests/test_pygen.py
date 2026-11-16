"""`klar run` compiles programs to Python code (klar/pygen.py): semantics the generated code must keep."""

import io
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from klar.checker import check_source          # noqa: E402
from klar.interp import Interpreter            # noqa: E402
from klar.errors import KlarError              # noqa: E402

sys.setrecursionlimit(20000)       # as the klar command does, for the checker on the long chains below


def run(src, argv=()):
    prog, errors = check_source(src, "<test>")
    if errors:
        raise errors[0]
    out = io.StringIO()
    Interpreter(prog, "<test>", list(argv), stdout=out).run_main()
    return out.getvalue()


class GeneratedCodeTest(unittest.TestCase):
    def test_closures_capture_values_per_iteration(self):
        src = """fn main() uses io {
  var fs: list[fn(int) -> int] = []
  for i in range(0, 3) {
    let k = i * 10
    fs = push(fs, fn(x: int) => x + i + k)
  }
  print(map(fs, fn(f) => f(1)))
}"""
        self.assertEqual(run(src), "[1, 12, 23]\n")

    def test_evaluation_order_with_hoisted_expressions(self):
        src = """fn say(s: str, v: int) -> int uses io { print(s)
  return v }
fn pick(o: Option[int]) -> int uses io {
  return say("a", 1) + match o { Some(v) => say("b", v), None => say("c", 0) } + say("d", 100)
}
fn main() uses io {
  print(pick(Some(10)))
  let t = false and match Some(1) { Some(v) => say("never", v) > 0, None => true }
  print(t)
}"""
        self.assertEqual(run(src), "a\nb\nd\n111\nfalse\n")

    def test_try_in_loop_condition(self):
        src = """fn first_big(xs: list[str]) -> Result[int, str] {
  var i = 0
  while parse_int(xs[i]).ok_or("bad {xs[i]}")? < 10 { i = i + 1 }
  return Ok(i)
}
fn main() uses io {
  print(first_big(["1", "2", "30"]))
  print(first_big(["1", "x"]))
}"""
        self.assertEqual(run(src), "Ok(2)\nErr(\"bad x\")\n")

    def test_owned_vars_are_snapshotted_when_shared(self):
        src = """fn main() uses io {
  var xs = [1]
  xs = push(xs, 2)
  let ys = xs
  xs = push(xs, 3)
  xs = set_at(xs, 0, 9)
  var m = ["a": 1]
  m = put(m, "b", 2)
  let m2 = m
  m = put(m, "a", 5)
  var s = "x"
  s = s + "y"
  let s2 = s
  s = s + "z"
  print("{xs} {ys} {m} {m2} {s} {s2} {len(xs)} {xs[2]}")
}"""
        self.assertEqual(run(src), '[9, 2, 3] [1, 2] ["a": 5, "b": 2] ["a": 1, "b": 2] xyz xy 3 3\n')

    def test_unicode_names_do_not_collide(self):
        src = """fn main() uses io {
  let ﬁ = 1
  let fi = 2
  let größe = 3
  print("{ﬁ} {fi} {größe}")
}"""
        self.assertEqual(run(src), "1 2 3\n")

    def test_long_chains_compile(self):
        terms = " + ".join(f'"{i}"' for i in range(400))
        cond = " or ".join(f"n == {i}" for i in range(400))
        elifs = " else ".join(f"if n == {i} {{ print({i}) }}" for i in range(300))
        src = f"fn main() uses io {{\n let n = 299\n print(len({terms}))\n print({cond})\n {elifs}\n}}"
        self.assertEqual(run(src), "1090\ntrue\n299\n")

    def test_error_trace_through_anonymous_function(self):
        src = """fn get_it(xs: list[int], i: int) -> int {
  return xs[i]
}
fn outer(xs: list[int]) -> int {
  let ys = map(xs, fn(x) => get_it(xs, x))
  return ys[0]
}
fn main() uses io { print(outer([1, 5, 0])) }"""
        with self.assertRaises(KlarError) as cm:
            run(src)
        e = cm.exception
        self.assertEqual((e.code, e.line), ("E401", 2))
        self.assertEqual(e.trace, ["get_it (<test>:2)", "outer (<test>:5)", "main (<test>:8)"])


    def test_error_in_another_module_names_that_file(self):
        import subprocess
        import tempfile
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "lib.klar"), "w") as f:
            f.write("fn pick(xs: list[int], i: int) -> int { return xs[i] }\n")
        with open(os.path.join(d, "main.klar"), "w") as f:
            f.write("use lib { pick }\nfn main() uses io {\n  print(pick([1], 3))\n}\n")
        r = subprocess.run([sys.executable, "-m", "klar", "run", "main.klar"], cwd=d, capture_output=True, text=True,
                           env=dict(os.environ, PYTHONPATH=ROOT))
        self.assertIn("error[E401] lib.klar:1:50", r.stderr)
        self.assertIn("return xs[i]", r.stderr)
        self.assertIn("at pick (lib.klar:1)\n  at main (main.klar:3)", r.stderr)


class RecursionTest(unittest.TestCase):
    def test_deep_recursion(self):
        src = """trait Count {
  fn count(self: Self, n: int) -> int
}
struct C { k: int }
impl Count for C {
  fn count(self: C, n: int) -> int {
    if n == 0 { return 0 }
    return 1 + self.count(n - 1)
  }
}
fn depth(n: int) -> int {
  if n == 0 { return 0 }
  return 1 + depth(n - 1)
}
fn viamap(n: int) -> int {
  if n == 0 { return 0 }
  return map([n - 1], fn(x) => viamap(x))[0] + 1
}
fn main() uses io {
  print(depth(200000))
  print(C(k: 1).count(100000))
  print(viamap(50000))
}"""
        self.assertEqual(run(src), "200000\n100000\n50000\n")

    def test_runaway_recursion_is_E406(self):
        src = "fn f(n: int) -> int { return f(n + 1) }\nfn main() uses io { print(f(0)) }"
        with self.assertRaises(KlarError) as cm:
            run(src)
        self.assertEqual((cm.exception.code, cm.exception.message), ("E406", "stack overflow in `f` (recursion too deep)"))


class SpeedTest(unittest.TestCase):
    def test_loops_run_at_python_speed(self):
        # 1,000,000 iterations that index a list: about 0.2 s here (the tree-walking evaluator took 2.5 s)
        src = """fn main() uses io {
  let xs = range(0, 1000)
  var total = 0
  for i in range(0, 1000000) { total = total + xs[i % 1000] }
  print(total)
}"""
        t = time.perf_counter()
        self.assertEqual(run(src), "499500000\n")
        self.assertLess(time.perf_counter() - t, 2.0)


if __name__ == "__main__":
    unittest.main()
