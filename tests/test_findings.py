"""Fixes for what tripped up AI agents in the first evaluation (eval/RESULTS.md). Each program runs in the
interpreter, on Node and natively, and must give the same output everywhere."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_web import NODE, ROOT, build_node, klar, write  # noqa: E402

CC = shutil.which("tcc") or shutil.which("gcc") or shutil.which("cc") or shutil.which("clang")


def run_everywhere(test, src, stdin=""):
    """Runs `src` in every available backend; returns (stdout, stderr) after checking they all agree."""
    path = write(src)
    r = klar("run", path, stdin=stdin)
    test.assertEqual(r.returncode, 0, r.stdout + r.stderr)
    results = {"interpreter": (r.stdout, r.stderr)}
    if NODE:
        mjs, b = build_node(path)
        test.assertEqual(b.returncode, 0, b.stderr)
        n = subprocess.run([NODE, mjs], input=stdin, capture_output=True, text=True)
        results["node"] = (n.stdout, n.stderr)
    if CC:
        exe = os.path.join(tempfile.mkdtemp(), "prog")
        b = klar("build", path, "-o", exe)
        test.assertEqual(b.returncode, 0, b.stdout + b.stderr)
        c = subprocess.run([exe], input=stdin, capture_output=True, text=True)
        results["native"] = (c.stdout, c.stderr)
    first = results["interpreter"]
    for name, got in results.items():
        test.assertEqual(got, first, f"{name} differs from the interpreter")
    return first


class BreakInValueMatch(unittest.TestCase):
    SRC = '''
fn main() uses io {
  var total = 0
  while true {
    let line = match read_line() {
      Some(l) => l,
      None => break,
    }
    let v = match parse_int(line) {
      Some(x) => x,
      None => { continue }
    }
    total = total + v
  }
  for i in range(0, 9) {
    let w = match i {
      3 => break,
      _ => i * 10,
    }
    print(w)
  }
  print("total {total}")
}
'''

    def test_break_and_continue_end_value_arms(self):
        out, _ = run_everywhere(self, self.SRC, stdin="1\nx\n2\n3\n")
        self.assertEqual(out, "0\n10\n20\ntotal 6\n")

    def test_falling_through_arm_is_named(self):
        r = klar("check", write('fn main() uses io {\n  let x = match read_line() {\n    Some(l) => l,\n'
                                '    None => { print("none") }\n  }\n  print(x)\n}\n'))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("match arm at line 4", r.stdout + r.stderr)
        self.assertIn("return, break or continue", r.stdout + r.stderr)


class ArrowBlockLambda(unittest.TestCase):
    def test_fn_arrow_block(self):
        src = '''
fn main() uses io {
  let words = ["a", "bb", "ccc"]
  let lines = map(words, fn(w) => {
    let n = len(w)
    if n > 2 {
      return "{w}!"
    }
    return "{w}:{n}"
  })
  print(join(lines, " "))
}
'''
        out, _ = run_everywhere(self, src)
        self.assertEqual(out, "a:1 bb:2 ccc!\n")


class StdinStderr(unittest.TestCase):
    def test_read_all_and_eprint(self):
        src = '''
fn main() uses io {
  let first = read_line()
  let rest = read_all()
  print("first: {first}")
  eprint("left: {len(rest)}")
  print("[{rest}]")
  eprint(7)
  print(read_all() == "")
}
'''
        out, err = run_everywhere(self, src, stdin="a\nbé\nc")
        self.assertEqual(out, 'first: Some("a")\n[bé\nc]\ntrue\n')
        self.assertEqual(err, "left: 4\n7\n")

    def test_eprint_is_io(self):
        r = klar("check", write('fn f() {\n  eprint("x")\n}\nfn main() uses io {\n  f()\n}\n'))
        self.assertIn("E308", r.stdout + r.stderr)


class Numbers(unittest.TestCase):
    def test_json_int_refuses_inexact_numbers(self):
        src = '''
use std.json { json_parse, json_int, json_at }
fn main() uses io {
  let j = unwrap(json_parse("[9007199254740993, 9007199254740991, -3, 2.5, 1e400]"))
  for i in range(0, 5) {
    print(json_int(unwrap(json_at(j, i))))
  }
  print(is_err(json_parse("[NaN]")))
}
'''
        out, _ = run_everywhere(self, src)
        self.assertEqual(out.split("\n")[:6], ["None", "Some(9007199254740991)", "Some(-3)", "None", "None", "true"])

    def test_parse_float_same_everywhere(self):
        src = '''
fn main() uses io {
  for s in [" 1.5 ", "+1", "1_0", "5.", ".5", ".", "0x10", "1e3", "1e", "-INF", "infx", "1.2.3"] {
    print("{s}={parse_float(s)}")
  }
  for s in [" 7", "+1", "1_0", "-0", "0x10"] {
    print("{s}={parse_int(s)}")
  }
}
'''
        out, _ = run_everywhere(self, src)
        self.assertIn("1_0=None", out)
        self.assertIn("0x10=None", out)
        self.assertIn(" 1.5 =Some(1.5)", out)
        self.assertIn("-INF=Some(-inf)", out)


if __name__ == "__main__":
    unittest.main()
