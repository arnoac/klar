"""Tests for the general-purpose features: modules, generics, effects on function types, Python interop,
the standard library, strings, and the example applications."""

import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from klar.checker import check_source          # noqa: E402
from klar.interp import Interpreter            # noqa: E402
from klar.errors import KlarError              # noqa: E402

sys.setrecursionlimit(20000)
APPS = os.path.join(ROOT, "examples", "apps")


def run_file(path, stdin="", argv=()):
    with open(path, encoding="utf-8") as f:
        src = f.read()
    prog, errors = check_source(src, path)
    if errors:
        raise errors[0]
    out = io.StringIO()
    Interpreter(prog, path, argv, stdout=out, stdin=io.StringIO(stdin)).run_main()
    return out.getvalue()


def run_src(src, files=None):
    """Write `src` (and extra module files) into a temp dir and run it."""
    d = tempfile.mkdtemp()
    for name, content in (files or {}).items():
        p = os.path.join(d, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(content)
    main = os.path.join(d, "main.klar")
    with open(main, "w") as f:
        f.write(src)
    return run_file(main)


def check_codes(src, files=None):
    d = tempfile.mkdtemp()
    for name, content in (files or {}).items():
        p = os.path.join(d, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(content)
    main = os.path.join(d, "main.klar")
    with open(main, "w") as f:
        f.write(src)
    _, errors = check_source(src, main)
    return [e.code for e in errors]


class ModulesTest(unittest.TestCase):
    LIB = {"util/text.klar": "fn shout(s: str) -> str { return upper(s) + suffix() }\n"
                             "fn suffix() -> str { return \"!\" }\n"
                             "enum Mood { Happy, Sad }\n"
                             "test \"ignored when imported\" { assert false }\n"}

    def test_use_imports_listed_names(self):
        src = 'use util.text { shout, Mood }\nfn main() uses io {\n print(shout("hi"))\n let m: Mood = Happy\n print(m)\n}\n'
        self.assertEqual(run_src(src, self.LIB), "HI!\nHappy\n")

    def test_unimported_name_is_an_error(self):
        src = 'use util.text { shout }\nfn main() uses io { print(suffix()) }\n'
        self.assertEqual(check_codes(src, self.LIB), ["E201"])

    def test_missing_module(self):
        self.assertEqual(check_codes('use nope.mod { x }\nfn main() uses io {}\n'), ["E201"])

    def test_unknown_imported_name(self):
        self.assertEqual(check_codes('use util.text { shoutt }\nfn main() uses io {}\n', self.LIB), ["E201"])

    def test_names_are_unique_program_wide(self):
        src = 'use util.text { shout }\nfn suffix() -> str { return "?" }\nfn main() uses io {}\n'
        self.assertEqual(check_codes(src, self.LIB), ["E202"])


class GenericsTest(unittest.TestCase):
    def test_generic_struct_and_enum(self):
        src = ("struct Box[T] { item: T }\n"
               "enum Tree[T] { Leaf, Node(left: Tree[T], value: T, right: Tree[T]) }\n"
               "fn size[T](t: Tree[T]) -> int { return match t { Leaf => 0, Node(l, _, r) => size(l) + 1 + size(r) } }\n"
               "fn main() uses io {\n let b = Box(item: \"x\")\n print(b.item)\n"
               " let t = Node(Leaf, 1, Node(Leaf, 2, Leaf))\n print(size(t))\n let bb = b with (item: \"y\")\n print(bb)\n}\n")
        self.assertEqual(run_src(src), 'x\n2\nBox(item: "y")\n')

    def test_generic_type_argument_count(self):
        self.assertEqual(check_codes("struct Box[T] { item: T }\nfn f(b: Box) {}\n"), ["E301"])

    def test_generic_field_type_is_checked(self):
        src = "struct Box[T] { item: T }\nfn f(b: Box[int]) -> str { return b.item }\n"
        self.assertEqual(check_codes(src), ["E301"])


class EffectsTest(unittest.TestCase):
    def test_io_function_values(self):
        src = ("fn twice(f: fn(str) -> unit uses io) uses io {\n f(\"a\")\n f(\"b\")\n}\n"
               "fn main() uses io {\n twice(fn(s) => print(s))\n let p = fn(s: str) => print(\"[{s}]\")\n twice(p)\n}\n")
        self.assertEqual(run_src(src), "a\nb\n[a]\n[b]\n")

    def test_io_function_cannot_fill_pure_parameter(self):
        src = "fn apply(f: fn(int) -> int, x: int) -> int { return f(x) }\nfn noisy(x: int) -> int uses io {\n print(x)\n return x\n}\n" \
              "fn main() uses io { print(apply(noisy, 1)) }\n"
        self.assertEqual(check_codes(src), ["E301"])


class StringsTest(unittest.TestCase):
    def test_raw_and_nested_and_multiline(self):
        src = ('fn main() uses io {\n'
               ' print(r"{\\"a\\": 1}")\n'
               ' let m = ["k": "v"]\n'
               ' print("got {m["k"]} and {upper("x")}")\n'
               ' let name = "Klar"\n'
               ' let page = """\n    <h1>{name}</h1>\n      <p>hi</p>\n    """\n'
               ' print(page)\n}\n')
        self.assertEqual(run_src(src), '{"a": 1}\ngot v and X\n<h1>Klar</h1>\n  <p>hi</p>\n\n')


class InteropTest(unittest.TestCase):
    PY = {"helper.py": "def add(a, b): return a + b\n"
                       "def fail(): raise ValueError('nope')\n"
                       "def point(): return {'x': 1, 'y': 2}\n"
                       "def wrong(): return 'not an int'\n"
                       "def call(f, x): return f(x) * 10\n"
                       "def shape(): return ('Circle', 2.0)\n"}

    def test_typed_calls(self):
        src = ('extern "helper.add" fn add(a: int, b: int) -> int\n'
               'extern "helper.fail" fn fail() -> Result[int, str]\n'
               'extern "helper.point" fn point() -> P\n'
               'extern "helper.call" fn call(f: fn(int) -> int, x: int) -> int\n'
               'extern "helper.shape" fn shape() -> Shape\n'
               'struct P { x: int, y: int }\nenum Shape { Circle(r: float), Dot }\n'
               'fn main() uses io {\n print(add(2, 3))\n print(fail())\n print(point().y)\n'
               ' print(call(fn(n) => n + 1, 4))\n print(shape())\n}\n')
        self.assertEqual(run_src(src, self.PY), '5\nErr("ValueError: nope")\n2\n50\nCircle(r: 2.0)\n')

    def test_wrong_python_type_is_reported(self):
        src = 'extern "helper.wrong" fn wrong() -> int\nfn main() uses io { print(wrong()) }\n'
        with self.assertRaises(KlarError) as cm:
            run_src(src, self.PY)
        self.assertEqual(cm.exception.code, "E407")
        self.assertIn("expected int", cm.exception.message)

    def test_exception_without_result_is_runtime_error(self):
        src = 'extern "helper.fail" fn fail() -> int\nfn main() uses io { print(fail()) }\n'
        with self.assertRaises(KlarError) as cm:
            run_src(src, self.PY)
        self.assertEqual(cm.exception.code, "E407")


class StdlibTest(unittest.TestCase):
    def test_json_round_trip(self):
        src = ('use std.json { Json, JObj, JNum, json_parse, json_stringify, json_get, json_int }\n'
               'fn main() uses io {\n let j = unwrap(json_parse(r"{\\"a\\": [1, 2.5, null, true]}"))\n'
               ' print(json_stringify(j))\n print(json_parse("[1,"))\n'
               ' print(json_int(JNum(3.0)))\n}\n')
        out = run_src(src).splitlines()
        self.assertEqual(out[0], '{"a":[1,2.5,null,true]}')
        self.assertTrue(out[1].startswith("Err("))
        self.assertEqual(out[2], "Some(3)")

    def test_fs(self):
        d = tempfile.mkdtemp()
        src = ('use std.fs { fs_write, fs_read, fs_list, fs_join, fs_exists }\n'
               f'fn main() uses io {{\n let p = fs_join(["{d}", "a.txt"])\n let _ = fs_write(p, "hey")\n'
               f' print(fs_read(p))\n print(fs_list("{d}"))\n print(fs_exists(p))\n print(is_err(fs_read("{d}/missing")))\n}}\n')
        self.assertEqual(run_src(src), 'Ok("hey")\nOk(["a.txt"])\ntrue\ntrue\n')

    def test_sqlite(self):
        src = ('use std.sqlite { sqlite_open, sqlite_exec, sqlite_query, SqlValue, SqlInt, SqlText, sql_int }\n'
               'fn main() uses io {\n let db = unwrap(sqlite_open(":memory:"))\n'
               ' let _ = unwrap(sqlite_exec(db, "create table t (n integer)", []))\n'
               ' let _ = unwrap(sqlite_exec(db, "insert into t values (?), (?)", [SqlInt(2), SqlInt(5)]))\n'
               ' let rows = unwrap(sqlite_query(db, "select sum(n) as s from t", []))\n'
               ' print(sql_int(rows[0]["s"]))\n print(is_err(sqlite_exec(db, "bogus", [])))\n}\n')
        self.assertEqual(run_src(src), "Some(7)\ntrue\n")

    def test_regex_time_math_process_random(self):
        src = ('use std.regex { regex_find_all, regex_replace }\nuse std.time { time_format }\n'
               'use std.math { math_pow }\nuse std.process { process_run }\nuse std.random { random_seed, random_int }\n'
               'fn main() uses io {\n print(regex_find_all("[a-z]+", "ab 12 cd"))\n print(regex_replace("\\\\d", "a1b2", "#"))\n'
               ' print(time_format(86400.0, "%Y-%m-%d"))\n print(math_pow(2.0, 10.0))\n'
               ' print(trim(unwrap(process_run(["echo", "ok"])).stdout))\n random_seed(7)\n let n = random_int(1, 6)\n'
               ' print(n >= 1 and n <= 6)\n}\n')
        self.assertEqual(run_src(src), '["ab", "cd"]\na#b#\n1970-01-02\n1024.0\nok\ntrue\n')


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class AppsTest(unittest.TestCase):
    def test_py_interop_example(self):
        out = run_file(os.path.join(APPS, "py_interop", "main.klar"))
        self.assertIn("median 3.0", out)
        self.assertIn("the: 3", out)

    def test_notes_cli(self):
        notes = os.path.join(tempfile.mkdtemp(), "notes.json")
        env = dict(os.environ, NOTES_FILE=notes, PYTHONPATH=ROOT)
        main = os.path.join(APPS, "notes_cli", "main.klar")

        def cli(*args):
            return subprocess.run([sys.executable, "-m", "klar", "run", main, *args], env=env, cwd=ROOT,
                                  capture_output=True, text=True)
        self.assertIn("added note 1", cli("add", "buy", "milk").stdout)
        cli("add", "call", "Bob")
        self.assertIn("2. call Bob", cli("list").stdout)
        self.assertIn("buy milk", cli("search", "MILK").stdout)
        r = cli("remove", "9")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no note number 9", r.stdout)
        self.assertEqual(len(json.loads(open(notes).read())), 2)

    def test_todo_api(self):
        port = free_port()
        env = dict(os.environ, PORT=str(port), PYTHONPATH=ROOT)
        main = os.path.join(APPS, "todo_api", "main.klar")
        proc = subprocess.Popen([sys.executable, "-m", "klar", "run", main], env=env, cwd=ROOT,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        base = f"http://127.0.0.1:{port}"

        def call(method, path, body=None):
            req = urllib.request.Request(base + path, data=body.encode() if body else None, method=method)
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    return r.status, r.read().decode()
            except urllib.error.HTTPError as e:
                return e.code, e.read().decode()
        try:
            for _ in range(50):
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                    break
                except OSError:
                    time.sleep(0.1)
            self.assertEqual(call("GET", "/todos"), (200, "[]"))
            status, body = call("POST", "/todos", '{"title": "write tests"}')
            self.assertEqual(status, 201)
            self.assertEqual(json.loads(body), {"id": 1, "title": "write tests", "done": False})
            self.assertEqual(call("POST", "/todos/1/done")[0], 200)
            self.assertEqual(json.loads(call("GET", "/todos")[1])[0]["done"], True)
            self.assertEqual(call("POST", "/todos", "not json")[0], 400)
            self.assertEqual(call("DELETE", "/todos/42")[0], 404)
            status, page = call("GET", "/")
            self.assertEqual(status, 200)
            self.assertIn("<h1>Todos (1)</h1>", page)
        finally:
            proc.kill()
            proc.communicate()


if __name__ == "__main__":
    unittest.main()
