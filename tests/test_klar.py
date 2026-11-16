"""Test suite for the Klar reference implementation. Run: python3 -m unittest discover tests"""

import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from klar.checker import check_source          # noqa: E402
from klar.interp import Interpreter            # noqa: E402
from klar.errors import KlarError, CATALOG     # noqa: E402

EXAMPLES = os.path.join(ROOT, "examples")
sys.setrecursionlimit(20000)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def run(src, stdin=""):
    prog, errors = check_source(src, "<test>")
    if errors:
        raise errors[0]
    out = io.StringIO()
    Interpreter(prog, "<test>", stdout=out, stdin=io.StringIO(stdin)).run_main()
    return out.getvalue()


def errors_of(src):
    _, errors = check_source(src, "<test>")
    return [(e.code, e.line) for e in errors]


def main_wrap(body):
    return "fn main() uses io {\n" + body + "\n}\n"


class ExamplesTest(unittest.TestCase):
    def test_examples_produce_expected_output(self):
        names = sorted(f[:-5] for f in os.listdir(EXAMPLES) if f.endswith(".klar"))
        self.assertGreaterEqual(len(names), 7)
        for name in names:
            with self.subTest(example=name):
                src = read(os.path.join(EXAMPLES, name + ".klar"))
                stdin_path = os.path.join(EXAMPLES, name + ".input")
                stdin = read(stdin_path) if os.path.exists(stdin_path) else ""
                expected = read(os.path.join(EXAMPLES, name + ".expected"))
                self.assertEqual(run(src, stdin), expected)

    def test_example_test_blocks_pass(self):
        for f in sorted(os.listdir(EXAMPLES)):
            if not f.endswith(".klar"):
                continue
            with self.subTest(example=f):
                prog, errors = check_source(read(os.path.join(EXAMPLES, f)), f)
                self.assertEqual(errors, [])
                results = Interpreter(prog, f).run_tests()
                failures = [(n, e.render()) for n, e in results if e is not None]
                self.assertEqual(failures, [])


class SemanticsTest(unittest.TestCase):
    def test_int_division_truncates_toward_zero(self):
        self.assertEqual(run(main_wrap('print("{7 / 2} {-7 / 2} {7 % 3} {-7 % 3}")')), "3 -3 1 -1\n")

    def test_float_arithmetic(self):
        self.assertEqual(run(main_wrap("print(1.5 * 2.0)")), "3.0\n")

    def test_values_are_immutable(self):
        src = main_wrap("let xs = [1, 2]\nlet ys = push(xs, 3)\nprint(xs)\nprint(ys)")
        self.assertEqual(run(src), "[1, 2]\n[1, 2, 3]\n")

    def test_list_var_updates_are_in_place_but_invisible(self):
        # xs = push(xs, v) / set_at on a var is O(1) in the interpreter too; snapshots taken before keep their values
        src = main_wrap("""var xs: list[int] = []
for i in range(0, 100000) { xs = push(xs, i) }
let snap = xs
xs = set_at(xs, 0, 7)
xs = push(xs, len(xs))
var ys = xs
ys = set_at(set_at(ys, 1, ys[0]), 2, 9)
let f = fn() => len(snap)
print("{len(xs)} {xs[0]} {xs[100000]} {snap[0]} {f()} {ys[1]} {ys[2]} {xs[1]} {xs[2]}")""")
        self.assertEqual(run(src), "100001 7 100000 0 100000 7 9 1 2\n")

    def test_var_reassignment_and_loops(self):
        src = main_wrap("var n = 0\nwhile true {\n n = n + 1\n if n == 5 { break }\n}\nprint(n)")
        self.assertEqual(run(src), "5\n")

    def test_continue(self):
        src = main_wrap("var s = 0\nfor i in range(0, 10) {\n if i % 2 == 0 { continue }\n s = s + i\n}\nprint(s)")
        self.assertEqual(run(src), "25\n")

    def test_closures_capture(self):
        src = "fn adder(n: int) -> fn(int) -> int {\n return fn(x) => x + n\n}\n" + \
              main_wrap("let add5 = adder(5)\nprint(add5(10))")
        self.assertEqual(run(src), "15\n")

    def test_generics(self):
        src = "fn pair_up[T](a: T, b: T) -> list[T] { return [a, b] }\n" + \
              main_wrap('print(pair_up(1, 2))\nprint(pair_up("a", "b"))')
        self.assertEqual(run(src), '[1, 2]\n["a", "b"]\n')

    def test_named_arguments_any_order(self):
        src = "fn sub(a: int, b: int) -> int { return a - b }\n" + main_wrap("print(sub(b: 1, a: 10))")
        self.assertEqual(run(src), "9\n")

    def test_method_call_syntax(self):
        self.assertEqual(run(main_wrap('print("a,b,c".split(",").reverse().join("-"))')), "c-b-a\n")

    def test_question_mark_on_option(self):
        src = ("fn second(xs: list[int]) -> Option[int] {\n let v = get(xs, 1)?\n return Some(v * 10)\n}\n" +
               main_wrap("print(second([1, 2]))\nprint(second([1]))"))
        self.assertEqual(run(src), "Some(20)\nNone\n")

    def test_string_interpolation_escapes(self):
        self.assertEqual(run(main_wrap('let x = 2\nprint("{{x}} = {x * 2}")')), "{x} = 4\n")

    def test_maps(self):
        src = main_wrap('let m = put(["a": 1], "b", 2)\nprint(m)\nprint(get(m, "z"))\nprint(has(m, "a"))\nprint(keys(m))')
        self.assertEqual(run(src), '["a": 1, "b": 2]\nNone\ntrue\n["a", "b"]\n')

    def test_structural_equality(self):
        src = "struct P { x: int }\n" + main_wrap("print(P(x: 1) == P(x: 1))\nprint([Some(1)] == [Some(2)])")
        self.assertEqual(run(src), "true\nfalse\n")

    def test_match_with_guards_via_if_expression(self):
        src = main_wrap('let n = 5\nlet s = match n { 0 => "zero", k => if k > 3 then "big" else "small" }\nprint(s)')
        self.assertEqual(run(src), "big\n")

    def test_lambda_block_body(self):
        src = main_wrap("let f = fn(x: int) -> int {\n if x > 0 { return x }\n return 0 - x\n}\nprint(map([-2, 3], f))")
        self.assertEqual(run(src), "[2, 3]\n")

    def test_multiline_nested_lists(self):
        src = main_wrap("let grid = [\n  [1, 2],\n  [3, 4],\n]\nprint(grid[1][0])")
        self.assertEqual(run(src), "3\n")

    def test_stdin(self):
        src = main_wrap("var total = 0\nvar line = read_line()\nwhile is_some(line) {\n"
                        " total = total + unwrap_or(parse_int(unwrap(line)), 0)\n line = read_line()\n}\nprint(total)")
        self.assertEqual(run(src, "1\n2\nx\n3\n"), "6\n")


class CheckErrorsTest(unittest.TestCase):
    """Each program must produce exactly the listed (code, line) diagnostics."""

    CASES = {
        "unknown name": (main_wrap("print(y)"), [("E201", 2)]),
        "unknown function": (main_wrap("print(lenght([1]))"), [("E201", 2)]),
        "unknown type": ("fn f(x: String) -> int { return 1 }", [("E201", 1)]),
        "shadowing": (main_wrap("let a = 1\nlet a = 2"), [("E202", 3)]),
        "duplicate fn": ("fn f() {}\nfn f() {}", [("E202", 2)]),
        "builtin redefined": ("fn len(x: int) -> int { return x }", [("E202", 1)]),
        "assign to let": (main_wrap("let a = 1\na = 2"), [("E203", 3)]),
        "unknown field": ("struct P { x: int }\n" + main_wrap("let p = P(x: 1)\nprint(p.y)"), [("E204", 4)]),
        "arg count": ("fn f(a: int) -> int { return a }\n" + main_wrap("print(f(1, 2))"), [("E205", 3)]),
        "bad named arg": ("fn f(a: int) -> int { return a }\n" + main_wrap("print(f(b: 1))"), [("E206", 3)]),
        "type mismatch": ("fn f(a: int) -> int { return a }\n" + main_wrap('print(f("x"))'), [("E301", 3)]),
        "int float mix": (main_wrap("let x = 1 + 2.0"), [("E302", 2)]),
        "string plus int": (main_wrap('let s = "a" + 1'), [("E302", 2)]),
        "call non function": (main_wrap("let x = 1\nlet y = x(2)"), [("E303", 3)]),
        "cannot infer": (main_wrap("let xs = []"), [("E304", 2)]),
        "missing return": ("fn f(a: int) -> int {\n if a > 0 { return 1 }\n}", [("E305", 1)]),
        "non exhaustive": ("enum C { R, G, B }\nfn f(c: C) -> int {\n return match c { R => 1, G => 2 }\n}", [("E306", 3)]),
        "non exhaustive int": ("fn f(n: int) -> int {\n return match n { 0 => 1 }\n}", [("E306", 2)]),
        "bad try": ("fn f(o: Option[int]) -> int {\n return o?\n}", [("E307", 2)]),
        "missing io": ("fn f() {\n print(1)\n}", [("E308", 2)]),
        "io in lambda passed to pure param": ("fn f() uses io {\n let ys = map([1], fn(x) => print(x))\n}", [("E308", 2)]),
        "io lambda outside io fn": ("fn f() {\n let g = fn(x: int) => print(x)\n}", [("E308", 2)]),
        "calling io fn value": ("fn f(g: fn(int) -> unit uses io) {\n g(1)\n}", [("E308", 2)]),
        "break outside loop": ("fn f() {\n break\n}", [("E309", 2)]),
        "bad pattern": ("fn f(n: int) -> int {\n return match n { Some(x) => 1, _ => 0 }\n}", [("E310", 2)]),
        "ignored result": (main_wrap("let xs = [1]\npush(xs, 2)"), [("E301", 3)]),
        "trait not implemented": ("trait Show { fn show(self: Self) -> str }\nstruct P { x: int }\n" + main_wrap("print(show(P(1)))"), [("E312", 4)]),
        "missing bound": ("trait Show { fn show(self: Self) -> str }\nstruct P { x: int }\nfn f[T](x: T) -> str { return show(x) }", [("E312", 3)]),
        "impl signature": ("trait Show { fn show(self: Self) -> str }\nstruct P { x: int }\nimpl Show for P { fn show(self: P) -> int { return 1 } }", [("E301", 3)]),
        "impl missing method": ("trait Show { fn show(self: Self) -> str }\nstruct P { x: int }\nimpl Show for P { }", [("E312", 3)]),
        "impl twice": ("trait Show { fn show(self: Self) -> str }\nstruct P { x: int }\nimpl Show for P { fn show(self: P) -> str { return \"\" } }\n"
                       "impl Show for P { fn show(self: P) -> str { return \"\" } }", [("E202", 4)]),
        "unknown trait": ("fn f[T: Shw](x: T) -> int { return 1 }", [("E201", 1)]),
        "trait method needs Self": ("trait T { fn m(x: int) -> int }", [("E301", 1)]),
        "ambiguous error wrap": ("enum E { A(msg: str), B(msg: str) }\nfn g() -> Result[int, str] { return Ok(1) }\n"
                                 "fn f() -> Result[int, E] {\n let x = g()?\n return Ok(x)\n}", [("E307", 4)]),
        "unpack wrong count": (main_wrap("let (a, b) = (1, 2, 3)"), [("E301", 2)]),
        "unpack non tuple": (main_wrap("let (a, b) = 1"), [("E301", 2)]),
        "tuple value out of range": (main_wrap("let t = (1, 2)\nprint(t.2)"), [("E204", 3)]),
        "tuple name reserved": ("struct Tuple2 { a: int }", [("E202", 1)]),
        "non exhaustive tuple": ("fn f(t: (int, bool)) -> int {\n return match t { (_, false) => 1 }\n}", [("E306", 2)]),
        "untyped sql rows": ("use std.sqlite { Database, sqlite_rows }\nfn f(db: Database) uses io {\n let r = sqlite_rows(db, \"select 1\", [])\n}",
                             [("E304", 3)]),
        "multiple independent errors": (main_wrap("print(a)\nprint(b)\nlet c: int = \"s\""),
                                        [("E201", 2), ("E201", 3), ("E301", 4)]),
    }

    def test_cases(self):
        for name, (src, expected) in self.CASES.items():
            with self.subTest(case=name):
                self.assertEqual(errors_of(src), expected)

    def test_every_error_has_hint_or_catalog_entry(self):
        for name, (src, _) in self.CASES.items():
            _, errs = check_source(src, "<t>")
            for e in errs:
                self.assertIn(e.code, CATALOG)
                self.assertTrue(e.message)


class LetElseTest(unittest.TestCase):
    def test_unwraps_or_leaves(self):
        src = ('fn f(s: str) -> str {\n  let n = parse_int(s) else { return "none" }\n  return str(n + 1)\n}\n'
               + main_wrap('print(f("4"))\nprint(f("x"))'))
        self.assertEqual(run(src), "5\nnone\n")
        pair = ('fn g(m: map[str, (int, str)]) -> str {\n  let (n, s) = get(m, "a") else { return "none" }\n'
                '  return "{n}{s}"\n}\n' + main_wrap('print(g(["a": (1, "x")]))\nprint(g([:]))'))
        self.assertEqual(run(pair), "1x\nnone\n")

    def test_else_must_leave_and_value_must_unwrap(self):
        self.assertEqual(errors_of(main_wrap('let n = parse_int("1") else { print("no") }\nprint(n)')), [("E301", 2)])
        self.assertEqual(errors_of(main_wrap('let n = 3 else { return }\nprint(n)')), [("E301", 2)])


class ParseErrorsTest(unittest.TestCase):
    CASES = {
        "unterminated string": ('fn main() uses io { print("hi) }', "E102"),
        "compound assignment": (main_wrap("var x = 1\nx += 1"), "E101"),
        "boolean symbols": (main_wrap("let b = true && false"), "E101"),
        "chained comparison": (main_wrap("let b = 1 < 2 < 3"), "E103"),
        "statement at top level": ("let x = 1", "E103"),
        "uppercase function": ("fn Main() {}", "E103"),
        "lowercase type": ("struct point { x: int }", "E103"),
        "angle generics": ("fn f(x: list<int>) {}", "E103"),
        "unclosed block": ("fn main() {\n let x = 1\n", "E103"),
        "bad interpolation": (main_wrap('print("{1 +}")'), "E104"),
        "missing param type": ("fn f(x) {}", "E103"),
        "call on new line": (main_wrap("let f = 1\n(2)"), "E105"),
    }

    def test_cases(self):
        for name, (src, code) in self.CASES.items():
            with self.subTest(case=name):
                _, errs = check_source(src, "<t>")
                self.assertEqual([e.code for e in errs], [code])
                self.assertIsNotNone(errs[0].hint, f"{name} should carry a fix hint")


class RuntimeErrorsTest(unittest.TestCase):
    CASES = {
        "index out of bounds": (main_wrap("let xs = [1]\nprint(xs[3])"), "E401", 3),
        "division by zero": (main_wrap("let z = 0\nprint(1 / z)"), "E402", 3),
        "panic": (main_wrap('panic("boom")'), "E403", 2),
        "unwrap none": (main_wrap("let o: Option[int] = None\nprint(unwrap(o))"), "E403", 3),
        "missing key": (main_wrap('let m = ["a": 1]\nprint(m["b"])'), "E405", 3),
        "stack overflow": ("fn f(n: int) -> int { return f(n + 1) }\n" + main_wrap("print(f(0))"), "E406", 1),
    }

    def test_cases(self):
        for name, (src, code, line) in self.CASES.items():
            with self.subTest(case=name):
                with self.assertRaises(KlarError) as cm:
                    run(src)
                self.assertEqual((cm.exception.code, cm.exception.line), (code, line))

    def test_assert_failure_reports_both_sides(self):
        prog, errs = check_source('test "t" {\n let x = 2\n assert x + 1 == 4\n}', "<t>")
        self.assertEqual(errs, [])
        [(name, err)] = Interpreter(prog, "<t>").run_tests()
        self.assertEqual(err.code, "E404")
        self.assertIn("left side = 3, right side = 4", err.hint)


class CliTest(unittest.TestCase):
    def test_json_output(self):
        import json
        import subprocess
        bad = os.path.join(ROOT, "tests", "_tmp_bad.klar")
        with open(bad, "w") as f:
            f.write(main_wrap("print(nope)"))
        try:
            r = subprocess.run([sys.executable, "-m", "klar", "check", "--json", bad],
                               capture_output=True, text=True, cwd=ROOT)
            data = json.loads(r.stdout)
            self.assertFalse(data["ok"])
            self.assertEqual(data["errors"][0]["code"], "E201")
            self.assertEqual(data["errors"][0]["line"], 2)
            self.assertEqual(r.returncode, 1)
        finally:
            os.remove(bad)

    def test_fmt_layout(self):
        from klar.fmt import format_source
        ugly = ("fn   add( a:int,b : int )->int{\nreturn a+b*-1\n}\n\n\n"
                "fn main( ) uses io{\n    let xs=[1,2,  3]\n  let m : map[str,int]=[:]\n"
                "      print( add(1 ,-2) )   // why\n  let f=fn (x)=>x*2\n  print(f(xs [0]))\n}")
        self.assertEqual(format_source(ugly),
                         "fn add(a: int, b: int) -> int {\n  return a + b * -1\n}\n\n"
                         "fn main() uses io {\n  let xs = [1, 2, 3]\n  let m: map[str, int] = [:]\n"
                         "  print(add(1, -2))   // why\n  let f = fn(x) => x * 2\n  print(f(xs[0]))\n}\n")

    def test_fmt_is_stable_on_every_klar_file(self):
        from klar.fmt import format_source, klar_files
        dirs = [os.path.join(ROOT, d) for d in ("examples", os.path.join("tests", "programs"), os.path.join("klar", "std"))]
        for path in klar_files(dirs):
            with self.subTest(file=os.path.relpath(path, ROOT)):
                src = read(path)
                self.assertEqual(format_source(src), src)      # the repository is formatted, and fmt is idempotent

    def test_fmt_check_exit_code(self):
        import subprocess
        import tempfile
        d = tempfile.mkdtemp()
        p = os.path.join(d, "a.klar")
        with open(p, "w") as f:
            f.write("fn main() uses io {\nprint(1)\n}\n")
        r = subprocess.run([sys.executable, "-m", "klar", "fmt", "--check", d], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual((r.returncode, r.stdout.strip()), (1, p))
        subprocess.run([sys.executable, "-m", "klar", "fmt", p], capture_output=True, cwd=ROOT)
        self.assertEqual(read(p), "fn main() uses io {\n  print(1)\n}\n")

    def test_spec_is_small(self):
        spec = read(os.path.join(ROOT, "klar", "SPEC.md"))
        # The whole language reference must comfortably fit in an agent's context.
        self.assertLess(len(spec.split()), 4000)


if __name__ == "__main__":
    unittest.main()
