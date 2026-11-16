"""Language setups: how a workspace is prepared, built and run for each language under test.

Each language gives the agent the same task text plus a short footer that says which file to write and which
commands exist. Building is part of grading: a program that does not pass the language's own static check
(`klar check` for Klar, `tsc` in strict mode for TypeScript) scores zero for that round, as it would in CI.
"""

import json
import os
import shutil
import subprocess
import sys

HARNESS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HARNESS))
WRAPPERS = os.path.join(HARNESS, "wrappers")

KLAR_FOOTER = """
## Language and tools

Write the program in **Klar**, in the file `main.klar` in this folder (you may add more `.klar` modules next
to it). Klar is a new language: you will not have seen it before. Everything you need to learn it is in this
folder:

* `toolchain/klar/SPEC.md`: the complete language reference (read this first).
* `toolchain/klar/std/*.klar`: the standard library sources, with every signature.
* `toolchain/README.md`, `toolchain/DESIGN.md` and `toolchain/examples/`: overview, design notes and example
  programs.

Commands (run them from this folder, with `bin` on your `PATH`):

* `klar check main.klar` type-checks (`--json` for machine-readable errors); `klar explain E301` explains a code.
* `klar run main.klar` runs the program. This is exactly how it will be graded.
* `klar test main.klar` runs `test` blocks.

Only the standard library is available (no packages). Python externs are not allowed.
"""

KLAR_WEB_FOOTER = """
## Language and tools

Write the app in **Klar**, in the file `main.klar` in this folder (you may add more `.klar` modules next to
it), as a browser app using `std.ui` (see "Web apps" in the reference). Klar is a new language: you will not
have seen it before. Everything you need to learn it is in this folder:

* `toolchain/klar/SPEC.md`: the complete language reference (read this first).
* `toolchain/klar/std/*.klar`: the standard library sources, with every signature.
* `toolchain/README.md`, `toolchain/DESIGN.md` and `toolchain/examples/`: overview, design notes and example
  programs (`examples/apps/` has browser apps).

Commands (run them from this folder, with `bin` on your `PATH`):

* `klar check main.klar` type-checks (`--json` for machine-readable errors); `klar explain E301` explains a code.
* `klar build --target web main.klar` makes `main.html`, a self-contained page. The tests open `main.html`
  (served over HTTP) in a browser. This is exactly how it will be graded.
* `klar test main.klar` runs `test` blocks.

Only the standard library is available (no packages). A headless Chromium with Playwright is installed for
Node.js (`require("playwright")`, with `NODE_PATH=$(npm root -g)` set) if you want to try the page in a browser.
"""

TS_WEB_FOOTER = """
## Language and tools

Write the app in **TypeScript**, in the file `main.ts` in this folder (you may add more `.ts` modules next to
it; import them with a `.js` extension). `index.html` loads the compiled `dist/main.js` as a module and has an
empty `<div id="app">`; the tests open `index.html` (served over HTTP) in a browser. Do not change
`index.html` or `tsconfig.json` (strict mode); use the DOM directly, since no npm packages are available and
nothing can be installed.

Commands (run them from this folder, with `bin` on your `PATH`):

* `tsc -p .` type-checks and compiles `main.ts` (and what it imports) to `dist/`. Grading runs `tsc -p .` and
  then opens `index.html`; a type error fails the build. `package.json` makes `.js` files ES modules, so a test
  script that uses `require` must end in `.cjs`.

A headless Chromium with Playwright is installed for Node.js (`require("playwright")`, with
`NODE_PATH=$(npm root -g)` set) if you want to try the page in a browser.
"""

TS_FOOTER = """
## Language and tools

Write the program in **TypeScript**, in the file `main.ts` in this folder (you may add more `.ts` modules
next to it; import them with a `.js` extension, as Node ES modules require). `tsconfig.json` is set up in
strict mode and must not be changed. Node.js 22 type definitions are installed; no other npm packages are
available and nothing can be installed.

Commands (run them from this folder, with `bin` on your `PATH`):

* `tsc -p .` type-checks and compiles to `dist/`.
* `node dist/main.js` runs the program. Grading runs `tsc -p .` and then `node dist/main.js`; a type error fails
  the build.

Only Node.js built-in modules are available (`node:fs`, `node:http`, `node:sqlite`, ...).
"""

TSCONFIG = {
    "compilerOptions": {
        "target": "ES2022",
        "module": "NodeNext",
        "moduleResolution": "NodeNext",
        "strict": True,
        "noEmitOnError": True,
        "outDir": "dist",
        "rootDir": ".",
        "types": ["node"],
        "skipLibCheck": True,
    },
    "include": ["main.ts"],   # imports are followed; test scripts elsewhere in the folder are not compiled
    "exclude": ["dist", "node_modules", "bin"],
}


def find_node_types():
    """Locate an installed @types/node to copy into TypeScript workspaces."""
    candidates = [os.environ.get("KLAREVAL_NODE_TYPES", "")]
    try:
        root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, timeout=20).stdout.strip()
        candidates.append(os.path.join(root, "@types", "node"))
    except (OSError, subprocess.SubprocessError):
        pass
    candidates += ["/opt/node-tools/node_modules/@types/node", "/usr/lib/node_modules/@types/node"]
    for c in candidates:
        if c and os.path.isfile(os.path.join(c, "package.json")):
            return c
    return None


def _install_wrapper(ws, name, real):
    """bin/NAME logs every call (arguments, exit status, error codes) to .eval/tools.jsonl, then behaves like NAME."""
    os.makedirs(os.path.join(ws, "bin"), exist_ok=True)
    path = os.path.join(ws, "bin", name)
    with open(path, "w") as f:
        log_dir = os.path.join(os.path.abspath(ws), ".eval")
        f.write(f"#!/bin/sh\nKLAREVAL_LOG_DIR={log_dir} exec {sys.executable} {os.path.join(WRAPPERS, 'logtool.py')} {name} {real} \"$@\"\n")
    os.chmod(path, 0o755)


class Klar:
    name = "klar"
    entry = "main.klar"
    footer = KLAR_FOOTER
    web_footer = KLAR_WEB_FOOTER
    web_page = "main.html"

    def prepare(self, ws, docs="repo"):
        tc = os.path.join(ws, "toolchain")
        os.makedirs(tc)
        shutil.copy(os.path.join(REPO, "klarc"), os.path.join(tc, "klarc"))
        os.chmod(os.path.join(tc, "klarc"), 0o755)
        shutil.copytree(os.path.join(REPO, "klar"), os.path.join(tc, "klar"),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if docs == "repo":
            for f in ("README.md", "DESIGN.md"):
                shutil.copy(os.path.join(REPO, f), os.path.join(tc, f))
            shutil.copytree(os.path.join(REPO, "examples"), os.path.join(tc, "examples"),
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.db"))
        _install_wrapper(ws, "klar", os.path.join(tc, "klarc"))

    def build(self, ws):
        p = subprocess.run(["sh", os.path.join(ws, "toolchain", "klarc"), "check", "--json", self.entry],
                           cwd=ws, capture_output=True, text=True, timeout=120)
        return p.returncode == 0, (p.stdout + p.stderr)

    def command(self, ws):
        return ["sh", os.path.join(ws, "toolchain", "klarc"), "run", self.entry]

    def build_web(self, ws):
        out = os.path.join(ws, self.web_page)
        if os.path.exists(out):
            os.remove(out)
        p = subprocess.run(["sh", os.path.join(ws, "toolchain", "klarc"), "build", "--target", "web", self.entry,
                            "-o", out], cwd=ws, capture_output=True, text=True, timeout=300)
        return p.returncode == 0 and os.path.isfile(out), (p.stdout + p.stderr)


class TypeScript:
    name = "ts"
    entry = "main.ts"
    footer = TS_FOOTER
    web_footer = TS_WEB_FOOTER
    web_page = "index.html"
    INDEX = ('<!doctype html>\n<html>\n<head>\n  <meta charset="utf-8">\n  <title>App</title>\n</head>\n<body>\n'
             '  <div id="app"></div>\n  <script type="module" src="dist/main.js"></script>\n</body>\n</html>\n')

    def prepare(self, ws, docs="repo"):
        with open(os.path.join(ws, "tsconfig.json"), "w") as f:
            json.dump(TSCONFIG, f, indent=2)
        with open(os.path.join(ws, "package.json"), "w") as f:
            json.dump({"name": "solution", "private": True, "type": "module"}, f, indent=2)
        types = find_node_types()
        if types is None:
            raise SystemExit("@types/node not found; set KLAREVAL_NODE_TYPES to its folder")
        shutil.copytree(types, os.path.join(ws, "node_modules", "@types", "node"))
        _install_wrapper(ws, "tsc", shutil.which("tsc") or "tsc")

    def build(self, ws):
        # The grader always compiles with the original settings, whatever the agent left in tsconfig.json.
        with open(os.path.join(ws, "tsconfig.json"), "w") as f:
            json.dump(TSCONFIG, f, indent=2)
        shutil.rmtree(os.path.join(ws, "dist"), ignore_errors=True)
        p = subprocess.run([shutil.which("tsc") or "tsc", "-p", "."], cwd=ws, capture_output=True, text=True,
                           timeout=300)
        ok = p.returncode == 0 and os.path.isfile(os.path.join(ws, "dist", "main.js"))
        return ok, (p.stdout + p.stderr)

    def command(self, ws):
        return ["node", "--no-warnings", os.path.join(ws, "dist", "main.js")]

    def build_web(self, ws):
        with open(os.path.join(ws, "index.html"), "w") as f:   # graded with the original page
            f.write(self.INDEX)
        return self.build(ws)


LANGS = {"klar": Klar(), "ts": TypeScript()}
