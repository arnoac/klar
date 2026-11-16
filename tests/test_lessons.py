"""Fixes that building Klar Board (examples/apps/board) called for: each problem the app ran into stays fixed."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_web import NODE, ROOT, build_node, klar  # noqa: E402

sys.path.insert(0, ROOT)
from klar.checker import check_source  # noqa: E402
from klar.fmt import format_source  # noqa: E402

CC = shutil.which("tcc") or shutil.which("gcc") or shutil.which("cc") or shutil.which("clang")

CRYPTO = '''use std.crypto { crypto_sha256, crypto_hmac, crypto_token, crypto_password_hash, crypto_password_verify, crypto_equal }
fn main() uses io {
  print(crypto_sha256(""))
  print(crypto_sha256("héllo wörld"))
  print(crypto_sha256(repeat("ab", 1000)))
  print(crypto_hmac("key", "The quick brown fox jumps over the lazy dog"))
  print(crypto_hmac(repeat("k", 100), "a long key is hashed first"))
  let t = crypto_token(16)
  print(len(t))
  print(t != crypto_token(16))
  let h = crypto_password_hash("s3cret pass")
  print(starts_with(h, "pbkdf2_sha256$100000$"))
  print(crypto_password_verify("s3cret pass", h))
  print(crypto_password_verify("s3cret pas", h))
  print(crypto_password_verify("x", "pbkdf2_sha256$abc$$"))
  print(crypto_equal("abc", "abc"))
  print(crypto_equal("abc", "abcd"))
  print(h)
}
'''

EXPECTED = """e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
a1003f7d04a4115711d0b48a2eaf1359ce565d2d2a6fd65098dfcffadeeef59f
{ab}
f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8
{longkey}
32
true
true
true
false
false
true
false
"""


def write(files):
    d = tempfile.mkdtemp()
    for name, src in files.items():
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(src)
    return d


def runners(path):
    out = {"interp": [sys.executable, "-m", "klar", "run", path]}
    if CC:
        exe = os.path.join(tempfile.mkdtemp(), "prog")
        b = klar("build", path, "-o", exe)
        assert b.returncode == 0, b.stderr
        out["native"] = [exe]
    if NODE:
        mjs, b = build_node(path)
        assert b.returncode == 0, b.stderr
        out["node"] = [NODE, mjs]
    return out


class Lessons(unittest.TestCase):
    def test_crypto_is_the_same_everywhere_and_hashes_are_portable(self):
        import hashlib
        import hmac
        expected = EXPECTED.format(ab=hashlib.sha256(("ab" * 1000).encode()).hexdigest(),
                                   longkey=hmac.new(b"k" * 100, b"a long key is hashed first", hashlib.sha256).hexdigest())
        path = os.path.join(write({"c.klar": CRYPTO}), "c.klar")
        hashes = {}
        env = dict(os.environ, PYTHONPATH=ROOT)
        for name, cmd in runners(path).items():
            with self.subTest(backend=name):
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
                self.assertEqual(r.returncode, 0, r.stderr)
                lines = r.stdout.splitlines()
                self.assertEqual("\n".join(lines[:-1]) + "\n", expected)
                hashes[name] = lines[-1]
        # a password hashed by one backend verifies in every other one
        for made_by, h in hashes.items():
            src = f'use std.crypto {{ crypto_password_verify }}\nfn main() uses io {{\n  print(crypto_password_verify("s3cret pass", "{h}"))\n}}\n'
            p = os.path.join(write({"v.klar": src}), "v.klar")
            for name, cmd in runners(p).items():
                with self.subTest(made_by=made_by, checked_by=name):
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
                    self.assertEqual(r.stdout, "true\n", r.stderr)

    def test_crypto_is_rejected_in_the_browser(self):
        p = os.path.join(write({"w.klar": 'use std.crypto { crypto_sha256 }\nfn main() uses io { print(crypto_sha256("x")) }\n'}), "w.klar")
        r = klar("build", "--target", "web", p, "-o", p + ".html")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("E501", r.stderr)

    def test_a_block_arm_ending_in_a_value_gets_one_clear_error(self):
        src = '''fn parse(s: str) -> Result[int, str] {
  return match parse_int(s) {
    Some(n) => {
      let doubled = n * 2
      Ok(doubled)
    }
    None => Err("not a number"),
  }
}
fn main() uses io { print(parse("2")) }
'''
        _, errors = check_source(src, "<test>")
        self.assertEqual([(e.code, e.line) for e in errors], [("E301", 5)])
        self.assertIn("does not give the match a value", errors[0].message)
        self.assertIn("return Ok(...)", errors[0].hint)

    def test_std_names_imported_in_one_module_stay_free_in_others(self):
        d = write({
            "helpers.klar": "fn card(n: int) -> int { return n * 2 }\nfn p(n: int) -> int { return n + 1 }\n"
                            "fn twice(n: int) -> int { return card(n) }\n",
            "main.klar": '''use std.html { Html, card, p, text, html_render }
use helpers { twice }
fn main() uses io {
  print(html_render(card([], [p([], [text("hi {twice(2)}")])])))
}
''',
            "both.klar": "use std.html { card }\nuse helpers { card }\nfn main() uses io { print(1) }\n",
        })
        r = klar("run", os.path.join(d, "main.klar"))
        self.assertEqual(r.stdout, '<div class="card"><p>hi 4</p></div>\n', r.stderr)
        # helpers.card is still callable from a module that imports it and not the std one
        with open(os.path.join(d, "other.klar"), "w") as f:
            f.write("use helpers { card, p }\nfn main() uses io { print(card(p(1))) }\n")
        self.assertEqual(klar("run", os.path.join(d, "other.klar")).stdout, "4\n")
        # one module importing both is a real clash
        r = klar("check", os.path.join(d, "both.klar"))
        self.assertNotEqual(r.returncode, 0)

    def test_fmt_closes_a_continued_line_back_to_its_statement(self):
        src = '''fn f(x: int) -> int {
  match g(x,
    x) {
    0 => { return 1 }
    _ => { return 2 }
  }
  return 0
}
fn g(a: int, b: int) -> int { return a + b }
'''
        self.assertEqual(format_source(src), src)
        ugly = src.replace("    0 =>", "        0 =>").replace("  }\n  return", "    }\n  return")
        self.assertEqual(format_source(ugly), src)

    @unittest.skipUnless(NODE, "node is not installed")
    def test_ui_unlisten_compiles_for_the_browser(self):
        src = '''use std.ui { Next, ui_run, ui_listen, ui_unlisten }
use std.html { Html, text }
enum Msg { Got(data: str) }
fn update(m: int, msg: Msg) -> Next[int, Msg] { return Next(m + 1, [ui_unlisten("/live")]) }
fn view(m: int) -> Html[Msg] { return text(str(m)) }
fn main() uses io { ui_run(Next(0, [ui_listen("/live", fn(d) => Got(d))]), update, view) }
'''
        p = os.path.join(write({"u.klar": src}), "u.klar")
        r = klar("build", "--target", "web", p, "-o", p + ".html")
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(p + ".html") as f:
            self.assertIn("CmdUnlisten", f.read())


if __name__ == "__main__":
    unittest.main()
