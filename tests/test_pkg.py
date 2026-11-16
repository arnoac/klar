"""Packages: klar.toml, path and git dependencies, klar.lock, Python pins, name clashes between packages."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KLAR = [sys.executable, "-m", "klar"]


def klar(*args, cwd, env=None):
    e = dict(os.environ, PYTHONPATH=ROOT, **(env or {}))
    return subprocess.run([*KLAR, *args], cwd=cwd, capture_output=True, text=True, env=e)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=cwd, check=True,
                   capture_output=True)


TEXTKIT = '''use case { upper_first }

fn shout(s: str) -> str {
  return helper(upper_first(s)) + "!"
}

fn helper(s: str) -> str {
  return "<" + s + ">"
}
'''

CASE = '''fn upper_first(s: str) -> str {
  return upper(slice(s, 0, 1)) + slice(s, 1, len(s))
}
'''

GEOMETRY = '''use textkit { shout }

struct Vec2 { x: float, y: float }

fn helper(v: Vec2) -> float {
  return v.x * v.x + v.y * v.y
}

fn describe(v: Vec2) -> str {
  return shout("length² {helper(v)}")
}
'''

APP = '''use geometry { Vec2, describe }
use textkit { shout }
use textkit.case { upper_first }

fn helper() -> str {
  return "app helper"
}

fn main() uses io {
  print(describe(Vec2(x: 3.0, y: 4.0)))
  print(shout(upper_first("klar")))
  print(helper())
}
'''

EXPECTED = "<Length² 25.0>!\n<Klar>!\napp helper\n"


@unittest.skipUnless(shutil.which("git"), "git not installed")
class PackageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        t = self.textkit = os.path.join(self.tmp, "textkit")
        write(os.path.join(t, "klar.toml"), '[package]\nname = "textkit"\nversion = "1.0.0"\n')
        write(os.path.join(t, "lib.klar"), TEXTKIT)
        write(os.path.join(t, "case.klar"), CASE)
        git(t, "init", "-q")
        git(t, "add", "-A")
        git(t, "commit", "-qm", "v1")
        git(t, "tag", "v1.0.0")
        write(os.path.join(t, "lib.klar"), TEXTKIT.replace('"!"', '"!!"'))
        git(t, "commit", "-qam", "v1.1")
        git(t, "tag", "v1.1.0")
        g = self.geometry = os.path.join(self.tmp, "geometry")
        write(os.path.join(g, "klar.toml"), '[package]\nname = "geometry"\n\n[dependencies]\n'
                                            f'textkit = {{ git = "{t}", tag = "v1.0.0" }}\n')
        write(os.path.join(g, "lib.klar"), GEOMETRY)
        self.app = os.path.join(self.tmp, "app")
        r = klar("new", "app", cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        write(os.path.join(self.app, "main.klar"), APP)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, *args, ok=True, env=None):
        r = klar("add", *args, cwd=self.app, env=env)
        self.assertEqual(r.returncode == 0, ok, r.stderr)
        return r

    def run_app(self):
        return klar("run", "main.klar", cwd=self.app)

    def test_dependencies_lock_and_backends(self):
        self.add("geometry", "--path", "../geometry")
        self.add("textkit", "--git", self.textkit, "--tag", "v1.0.0")
        with open(os.path.join(self.app, "klar.toml")) as f:
            toml = f.read()
        self.assertIn('geometry = { path = "../geometry" }', toml)
        with open(os.path.join(self.app, "klar.lock")) as f:
            lock = f.read()
        self.assertIn('tag = "v1.0.0"', lock)
        self.assertIn("commit = ", lock)
        # three packages each define a private `helper`; each keeps its own
        r = self.run_app()
        self.assertEqual((r.returncode, r.stdout), (0, EXPECTED), r.stderr)
        out = os.path.join(self.tmp, "app.mjs")
        if shutil.which("node"):
            self.assertEqual(klar("build", "--target", "node", "main.klar", "-o", out, cwd=self.app).returncode, 0)
            self.assertEqual(subprocess.run(["node", out], capture_output=True, text=True).stdout, EXPECTED)
        exe = os.path.join(self.tmp, "app_exe")
        if klar("build", "main.klar", "-o", exe, cwd=self.app).returncode == 0:
            self.assertEqual(subprocess.run([exe], capture_output=True, text=True).stdout, EXPECTED)

        # a fresh checkout: nothing installed yet, so a clear error, then `klar install` restores the pinned commit
        shutil.rmtree(os.path.join(self.app, ".klar"))
        r = self.run_app()
        self.assertIn("E601", r.stderr)
        self.assertIn("klar install", r.stderr)
        self.assertEqual(klar("install", cwd=self.app).returncode, 0)
        self.assertEqual(self.run_app().stdout, EXPECTED)

        # a dependency checked out at another commit than the lock pins is reported
        git(os.path.join(self.app, ".klar", "deps", "textkit"), "checkout", "-q", "v1.1.0")
        r = self.run_app()
        self.assertIn("klar.lock pins", r.stderr)
        klar("install", cwd=self.app)

        # two packages asking for different versions: an error, and klar.toml stays as it was
        r = self.add("textkit", "--git", self.textkit, "--tag", "v1.1.0", ok=False)
        self.assertIn("two different places", r.stderr)
        with open(os.path.join(self.app, "klar.toml")) as f:
            self.assertEqual(f.read(), toml)
        self.assertEqual(self.run_app().stdout, EXPECTED)

    def test_branch_and_update(self):
        write(os.path.join(self.app, "main.klar"), 'use textkit { shout }\nfn main() uses io { print(shout("x")) }\n')
        self.add("textkit", "--git", self.textkit, "--branch", "master" if self._branch() == "master" else "main")
        self.assertEqual(self.run_app().stdout, "<X>!!\n")
        write(os.path.join(self.textkit, "lib.klar"), TEXTKIT.replace('"!"', '"?"'))
        git(self.textkit, "commit", "-qam", "v1.2")
        klar("install", cwd=self.app)
        self.assertEqual(self.run_app().stdout, "<X>!!\n")   # the lock holds it back
        self.assertEqual(klar("update", cwd=self.app).returncode, 0)
        self.assertEqual(self.run_app().stdout, "<X>?\n")

    def _branch(self):
        return subprocess.run(["git", "branch", "--show-current"], cwd=self.textkit, capture_output=True,
                              text=True).stdout.strip()

    def test_python_package(self):
        wheels = os.path.join(self.tmp, "wheels")
        os.makedirs(wheels)
        di = "tinycomma-1.0.0.dist-info"
        files = {"tinycomma.py": "def intcomma(n):\n    return f'{n:,}'\n",
                 f"{di}/METADATA": "Metadata-Version: 2.1\nName: tinycomma\nVersion: 1.0.0\n",
                 f"{di}/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"}
        files[f"{di}/RECORD"] = "".join(f"{k},,\n" for k in files) + f"{di}/RECORD,,\n"
        with zipfile.ZipFile(os.path.join(wheels, "tinycomma-1.0.0-py3-none-any.whl"), "w") as z:
            for k, c in files.items():
                z.writestr(k, c)
        self.add("--python", "tinycomma==1.0.0", env={"PIP_NO_INDEX": "1", "PIP_FIND_LINKS": wheels})
        write(os.path.join(self.app, "main.klar"),
              'extern "tinycomma.intcomma" fn intcomma(n: int) -> str\n\nfn main() uses io {\n  print(intcomma(1234567))\n}\n')
        self.assertEqual(self.run_app().stdout, "1,234,567\n")
        self.add("--python", "tinycomma", ok=False)

    def test_bad_manifest(self):
        write(os.path.join(self.app, "klar.toml"), '[package]\nname = "app"\n\n[dependencies]\nx = { }\n')
        r = self.run_app()
        self.assertIn("E601", r.stderr)
        self.assertIn("exactly one of `path` or `git`", r.stderr)


if __name__ == "__main__":
    unittest.main()
