"""The command line: defaults, help, hints for wrong commands, `klar new`, one-argument `klar add`, `klar remove`."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CC = shutil.which("tcc") or shutil.which("cc") or shutil.which("gcc")


def klar(*args, cwd=None):
    env = dict(os.environ, PYTHONPATH=ROOT)
    return subprocess.run([sys.executable, "-m", "klar", *args], capture_output=True, text=True, cwd=cwd, env=env,
                          timeout=120)


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_help_lists_every_command(self):
        r = klar()
        self.assertEqual(r.returncode, 0)
        from klar.cli import COMMANDS
        for c in COMMANDS:
            self.assertIn(f"klar {c}", r.stdout)
        r = klar("help", "add")
        self.assertIn("klar add npm:marked", r.stdout)
        self.assertIn("E306", klar("help", "E306").stdout)
        self.assertRegex(klar("--version").stdout, r"^klar \d+\.\d+")

    def test_wrong_commands_say_what_to_type(self):
        for typed, hint in [("compile", "klar build"), ("init", "klar new"), ("serve", "klar dev"),
                            ("format", "klar fmt"), ("biuld", "klar build"), ("hello.klar", "klar run hello.klar"),
                            ("install", "klar add npm:marked")]:
            r = klar(*typed.split(), *(["marked"] if typed == "install" else []))
            self.assertEqual(r.returncode, 2, typed)
            self.assertIn(hint, r.stderr, typed)
        r = klar("run", "mian.klar", cwd=os.path.join(ROOT, "examples", "apps", "notes_cli"))
        self.assertIn("klar run main.klar", r.stderr)

    def test_new_project_works_without_naming_files(self):
        r = klar("new", "hello", cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        d = os.path.join(self.tmp, "hello")
        self.assertEqual(klar("run", cwd=d).stdout, "Hello, world!\n")
        self.assertIn("1 passed", klar("test", cwd=d).stdout)
        self.assertEqual(klar("check", cwd=d).stdout, "ok\n")
        self.assertEqual(klar("fmt", "--check", cwd=d).returncode, 0)
        # arguments without a file go to main.klar; a subfolder still finds the project
        with open(os.path.join(d, "main.klar"), "w") as f:
            f.write('fn main() uses io { print(join(args(), "+")) }\n')
        os.makedirs(os.path.join(d, "sub"))
        self.assertEqual(klar("run", "a", "b", cwd=os.path.join(d, "sub")).stdout, "a+b\n")
        self.assertEqual(klar("run", d, "x").stdout, "x\n")              # a folder means its main.klar

    def test_check_and_test_cover_the_whole_project(self):
        klar("new", "proj", cwd=self.tmp)
        d = os.path.join(self.tmp, "proj")
        with open(os.path.join(d, "util.klar"), "w") as f:
            f.write('fn double(x: int) -> int { return x * 2 }\ntest "double" { assert double(2) == 4 }\n')
        r = klar("test", cwd=d)
        self.assertIn("2 passed, 0 failed (2 files)", r.stdout, r.stderr)
        with open(os.path.join(d, "bad.klar"), "w") as f:
            f.write("fn f() -> int { return \"no\" }\n")
        r = klar("check", cwd=d)
        self.assertEqual(r.returncode, 1)
        self.assertIn("bad.klar", r.stderr)

    @unittest.skipUnless(CC, "no C compiler")
    def test_build_names_the_output_after_the_package(self):
        klar("new", "tool", cwd=self.tmp)
        d = os.path.join(self.tmp, "tool")
        r = klar("build", cwd=d)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(subprocess.run([os.path.join(d, "tool")], capture_output=True, text=True).stdout,
                         "Hello, world!\n")

    def test_web_project_builds_a_page_by_default(self):
        klar("new", "shop", "--web", cwd=self.tmp)
        d = os.path.join(self.tmp, "shop")
        self.assertIn("1 passed", klar("test", cwd=d).stdout)
        r = klar("build", cwd=d)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isfile(os.path.join(d, "shop.html")))

    def test_add_takes_one_source_and_remove_undoes_it(self):
        for name in ("geo", "app"):
            klar("new", name, cwd=self.tmp)
        with open(os.path.join(self.tmp, "geo", "lib.klar"), "w") as f:
            f.write("fn twice(x: int) -> int { return x * 2 }\n")
        app = os.path.join(self.tmp, "app")
        r = klar("add", "../geo", cwd=app)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(app, "main.klar"), "w") as f:
            f.write("use geo { twice }\nfn main() uses io { print(twice(21)) }\n")
        self.assertEqual(klar("run", cwd=app).stdout, "42\n")
        r = klar("add", "marked", cwd=app)
        self.assertEqual(r.returncode, 1)
        self.assertIn("klar add npm:marked", r.stderr)
        self.assertEqual(klar("remove", "geo", cwd=app).returncode, 0)
        with open(os.path.join(app, "klar.toml")) as f:
            self.assertNotIn("geo", f.read())

    def test_sources_mean_what_they_look_like(self):
        from klar.pkg import parse_source
        self.assertEqual(parse_source("npm:marked@18.2.0"), {"npm": "marked@18.2.0"})
        self.assertEqual(parse_source("py:requests==2.32.3"), {"python": "requests==2.32.3"})
        self.assertEqual(parse_source("github.com/someone/Text-Kit@v1.2.0"),
                         {"name": "text_kit", "git": "https://github.com/someone/Text-Kit", "tag": "v1.2.0"})
        self.assertEqual(parse_source("https://example.org/a/b.git@0123abcd", "bee"),
                         {"name": "bee", "git": "https://example.org/a/b.git", "rev": "0123abcd"})
        self.assertEqual(parse_source("git@github.com:someone/textkit.git"),
                         {"name": "textkit", "git": "git@github.com:someone/textkit.git"})


if __name__ == "__main__":
    unittest.main()
