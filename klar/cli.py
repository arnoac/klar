"""Command line interface. `klar help` lists the commands; HELP below is that text."""

import os
import sys

from . import __version__
from .checker import check_source
from .errors import KlarError, CATALOG, to_json

# Heavier modules (argparse, json, subprocess, the interpreter) are imported only by the commands that
# need them, so `klar build` starts as fast as possible.

HERE = os.path.dirname(os.path.abspath(__file__))


def load(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def report(errors, src, as_json):
    if as_json:
        print(to_json(errors))
    else:
        for e in errors:
            esrc = src
            if e.file and os.path.exists(e.file):
                esrc = load(e.file)
            print(e.render(esrc), file=sys.stderr)
        if errors:
            print(f"{len(errors)} error(s)", file=sys.stderr)


def check_main_signature(prog, path):
    for d in prog.decls:
        if d.kind == "Fn" and d.name == "main":
            if d.params or not (d.ret.kind == "TName" and d.ret.name == "unit"):
                return KlarError("E301", "`main` must take no parameters and return unit", d.line, d.col,
                                 "use args() to read command line arguments", path)
            return None
    return KlarError("E201", "no `main` function to run", 1, 1, "add: fn main() uses io { ... }", path)


def cmd_check(path, as_json):
    src = load(path)
    _, errors = check_source(src, path)
    report(errors, src, as_json)
    if as_json and not errors:
        pass
    elif not errors:
        print("ok")
    return 1 if errors else 0


def cmd_run(path, as_json, argv):
    src = load(path)
    prog, errors = check_source(src, path)
    if not errors:
        e = check_main_signature(prog, path)
        if e:
            errors = [e]
    if errors:
        report(errors, src, as_json)
        return 1
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))
    from .interp import Interpreter
    interp = Interpreter(prog, path, argv)
    try:
        interp.run_main()
    except KlarError as e:
        sys.stdout.flush()
        report([e], src, as_json)
        return 1
    return 0


def cmd_test(path, as_json):
    src = load(path)
    prog, errors = check_source(src, path)
    if errors:
        report(errors, src, as_json)
        return 1
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))
    from .interp import Interpreter
    results = Interpreter(prog, path).run_tests()
    failed = [(n, e) for n, e in results if e is not None]
    if as_json:
        import json
        print(json.dumps({
            "ok": not failed,
            "passed": len(results) - len(failed),
            "failed": len(failed),
            "tests": [{"name": n, "ok": e is None, "error": e.to_dict() if e else None} for n, e in results],
        }, indent=2))
    else:
        for n, e in results:
            print(("PASS  " if e is None else "FAIL  ") + n)
            if e is not None:
                print("      " + e.render(src).replace("\n", "\n      "))
        print(f"{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


def which(name):
    # a tiny shutil.which (importing shutil alone costs ~10 ms of startup)
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = os.path.join(d, name)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def find_cc(release):
    if not release and which("tcc"):
        return ["tcc", "-w"]
    for cc in ("cc", "gcc", "clang"):
        if which(cc):
            return [cc, "-O2", "-std=gnu11", "-w"] if release else [cc, "-O0", "-std=gnu11", "-w"]
    return None


def has_gc():
    return any(os.path.exists(p) for p in ("/usr/include/gc.h", "/usr/local/include/gc.h", "/opt/homebrew/include/gc.h"))


def cmd_build(path, out, release, emit_c, as_json, quiet=False):
    import time
    from .compiler import compile_to_c
    t0 = time.perf_counter()
    src = load(path)
    prog, errors = check_source(src, path)
    if not errors:
        e = check_main_signature(prog, path)
        if e:
            errors = [e]
    if not errors:
        try:
            info = {}
            c_src = compile_to_c(prog, path, info)
        except KlarError as e:
            errors = [e]
    if errors:
        report(errors, src, as_json)
        return 1
    t1 = time.perf_counter()
    out = out or os.path.splitext(os.path.basename(path))[0]
    c_path = out + ".c"
    with open(c_path, "w", encoding="utf-8") as f:
        f.write(c_src)
    if emit_c:
        print(f"wrote {c_path}")
        return 0
    cc = find_cc(release)
    if cc is None:
        print("error: no C compiler found (install tcc, gcc or clang)", file=sys.stderr)
        return 1
    gc = ["-DKLAR_GC"] if has_gc() else []
    extra = os.environ.get("KLAR_CFLAGS", "").split()      # e.g. sanitizers in CI: -fsanitize=address,undefined
    cmd = cc + gc + extra + ["-o", out, c_path, "-lm"] + info.get("libs", []) + (["-lgc"] if gc else [])
    status = os.spawnvp(os.P_WAIT, cmd[0], cmd)
    t2 = time.perf_counter()
    if status != 0:
        print(f"internal compiler error: the generated C ({c_path}) did not compile. Please report this with the program.",
              file=sys.stderr)
        return 2
    os.remove(c_path)
    if not quiet:
        mode = "release" if release else "debug"
        print(f"built {out} ({mode}, {cc[0]}): klar {1000 * (t1 - t0):.0f} ms + C {1000 * (t2 - t1):.0f} ms",
              file=sys.stderr)
    return 0


WEB_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
</head>
<body>
<div id="app"></div>
{head}<script type="module">
{js}
</script>
</body>
</html>
"""


def cmd_build_js(path, out, target, as_json, quiet=False):
    import time
    from .jscompiler import compile_to_js
    t0 = time.perf_counter()
    src = load(path)
    prog, errors = check_source(src, path)
    if not errors:
        e = check_main_signature(prog, path)
        if e:
            errors = [e]
    stem = os.path.splitext(os.path.basename(path))[0]
    out = out or stem + (".mjs" if target == "node" else ".html")
    if not errors:
        try:
            js = compile_to_js(prog, path, target, out)
        except KlarError as e:
            errors = [e]
    if errors:
        report(errors, src, as_json)
        return 1
    with open(out, "w", encoding="utf-8") as f:
        if target == "node":
            f.write("#!/usr/bin/env node\n" + js)
        else:
            f.write(WEB_PAGE.format(title=stem, head=getattr(js, "head", ""), js=js.replace("</script", "<\\/script")))
    if target == "node":
        os.chmod(out, 0o755)
    if not quiet:
        how = f"run it with: node {out}" if target == "node" else f"open {out} in a browser, or serve it"
        print(f"built {out} ({target}) in {1000 * (time.perf_counter() - t0):.0f} ms; {how}", file=sys.stderr)
    return 0


def cmd_build_wasm(path, out, release, emit_c, as_json, quiet=False):
    import time
    from .compiler import compile_to_c
    from .wasm import c_to_wasm, runner_js
    t0 = time.perf_counter()
    src = load(path)
    prog, errors = check_source(src, path)
    if not errors:
        e = check_main_signature(prog, path)
        if e:
            errors = [e]
    wasm = None
    if not errors:
        try:
            c_src = compile_to_c(prog, path, None, "wasm")
            if emit_c:
                c_path = (out or os.path.splitext(os.path.basename(path))[0]) + ".c"
                with open(c_path, "w", encoding="utf-8") as f:
                    f.write(c_src)
                print(f"wrote {c_path}")
                return 0
            t1 = time.perf_counter()
            wasm = c_to_wasm(c_src, release=True, where=path)
        except KlarError as e:
            errors = [e]
    if errors:
        report(errors, src, as_json)
        return 1
    stem = out[:-4] if out and out.endswith(".mjs") else out or os.path.splitext(os.path.basename(path))[0]
    wasm_path, js_path = stem + ".wasm", stem + ".mjs"
    with open(wasm_path, "wb") as f:
        f.write(wasm)
    with open(js_path, "w", encoding="utf-8") as f:
        f.write("#!/usr/bin/env node\n" + runner_js(os.path.basename(wasm_path)))
    os.chmod(js_path, 0o755)
    if not quiet:
        print(f"built {wasm_path} ({len(wasm) // 1024 + 1} KB) + {js_path}: klar {1000 * (t1 - t0):.0f} ms + clang "
              f"{1000 * (time.perf_counter() - t1):.0f} ms; run it with: node {js_path}", file=sys.stderr)
    return 0


def cmd_explain(code):
    code = code.upper()
    if code not in CATALOG:
        print(f"unknown error code {code}. Known codes: {', '.join(sorted(CATALOG))}")
        return 1
    title, text = CATALOG[code]
    print(f"{code}: {title}\n\n{text}")
    return 0


HELP = """Klar: a typed language that is easy for people and AI agents to write.

Start
  klar new NAME             create a project (--web: a browser app)
  klar run [FILE] [ARGS]    check and run a program
  klar dev [FILE...]        serve a web app or server; rebuild and reload on every save

Check and ship
  klar test [FILE]          run `test` blocks
  klar check [FILE]         type-check without running
  klar fmt [PATH...]        format .klar files in place (--check: only report)
  klar build [FILE]         compile: native executable, or a web page for std.ui apps

Packages
  klar add SOURCE           add a dependency: npm:NAME, py:NAME==VERSION, a git URL or ./folder
  klar remove NAME          remove a dependency
  klar install              fetch everything klar.toml and klar.lock name
  klar update [NAME...]     move git dependencies to the newest commit they allow
  klar bind NPM_PACKAGE     write typed declarations for an npm package
  klar audit                check dependencies and Klar for known vulnerabilities

Learn
  klar spec                 print the complete language reference
  klar explain CODE         explain an error code, e.g. klar explain E306
  klar help COMMAND         options and examples for one command
  klar lsp                  language server for editors

FILE defaults to main.klar of the project you are in (a folder works too: klar run examples/apps/todo_api).
With no FILE, `klar test`, `klar check` and `klar fmt` cover every .klar file in the project.
Commands that report errors take --json for machine-readable output.
"""

EXAMPLES = {
    "new": "klar new hello            a console program\n  klar new shop --web       a browser app (std.ui)",
    "run": "klar run                  main.klar of this project\n  klar run tool.klar a b     arguments reach args()\n"
           "  klar run add milk          main.klar with arguments `add milk`",
    "dev": "klar dev                  main.klar, app.klar and server.klar of this project\n"
           "  klar dev server.klar app.klar --port 3000",
    "test": "klar test                 every file in the project\n  klar test bank.klar --json",
    "check": "klar check                every file in the project\n  klar check main.klar --json",
    "fmt": "klar fmt                  the whole project\n  klar fmt --check src/      list unformatted files, change nothing",
    "build": "klar build                main.klar: an executable named after the package (a .html page for std.ui apps)\n"
             "  klar build --release      optimised (gcc/clang -O2)\n"
             "  klar build --target node server.klar   a Node.js module: node server.mjs\n"
             "  klar build --target web app.klar -o public/index.html\n"
             "  klar build --target wasm prog.klar  prog.wasm + prog.mjs loader",
    "add": "klar add npm:marked                       npm, latest version, pinned exactly\n"
           "  klar add npm:marked@18.2.0\n"
           "  klar add py:requests==2.32.3\n"
           "  klar add https://github.com/someone/textkit@v1.2.0\n"
           "  klar add ../geometry                      a local package\n"
           "  klar add text https://github.com/someone/textkit   choose the name `use` refers to",
    "remove": "klar remove marked textkit",
    "install": "klar install              after cloning a project",
    "update": "klar update                all git dependencies\n  klar update textkit",
    "bind": "klar bind marked          writes npm/marked.klar",
    "explain": "klar explain E306",
    "audit": "klar audit\n  klar audit --json",
}

DESCRIPTIONS = {
    "run": "Check a program, then run its main() in the interpreter (use `klar build` for speed).",
    "check": "Type-check without running. Reports every error with a code, a hint and a fix.",
    "test": "Run every `test` block. With no FILE, every .klar file in the project that has tests.",
    "build": "Compile a program. The target is chosen for you: a std.ui app becomes a self-contained web page,\n"
             "anything else a native executable (via C). --target picks one explicitly.",
    "fmt": "Rewrite .klar files in the one canonical layout. Only whitespace changes; the tokens are verified.",
    "dev": "Serve browser apps and run a backend, rebuilding and reloading the browser on every save.",
    "new": "Create a project folder with klar.toml and main.klar (a test included).",
    "add": "Add a dependency to klar.toml, pin it and install it. Versions are always pinned exactly and npm\n"
           "install scripts never run. Adding an npm package also writes typed declarations when it can.",
    "remove": "Remove dependencies from klar.toml (any kind: Klar, Python or npm) and reinstall.",
    "install": "Fetch every dependency klar.toml and klar.lock name, at the pinned versions.",
    "update": "Move git dependencies to the newest commit their tag or branch allows, and re-pin them.",
    "audit": "Check every dependency (git, Python, npm) and this Klar itself against known vulnerabilities.\n"
             "Exit status: 0 clean, 1 findings, 2 the check could not complete.",
    "bind": "Write Klar extern declarations for an installed npm package from its TypeScript types.",
    "lsp": "Run the language server over stdin/stdout (diagnostics, hover, go to definition, rename, completion).",
    "spec": "Print the complete language reference (about 4,000 words). Read it once before writing Klar.",
    "explain": "Explain an error code in detail.",
}

# What people (and agents) type out of habit, and the Klar command for it.
GUESSES = {
    "compile": "build", "make": "build", "init": "new", "create": "new", "start": "dev", "serve": "dev",
    "watch": "dev", "format": "fmt", "lint": "check", "typecheck": "check", "vet": "check", "doc": "spec",
    "docs": "spec", "reference": "spec", "i": "install", "get": "add", "upgrade": "update", "rm": "remove",
    "uninstall": "remove", "exec": "run", "version": "--version",
}


def say(msg, hint=None):
    print(msg, file=sys.stderr)
    if hint:
        print("  hint: " + hint, file=sys.stderr)


def project_dir(start=None):
    """The folder of the project around `start` (default: here): the nearest klar.toml, else the folder itself."""
    from .pkg import find_root
    start = os.path.abspath(start or os.getcwd())
    return find_root(start) or (start if os.path.isdir(start) else os.path.dirname(start))


def mains_in(folder):
    try:
        names = sorted(f for f in os.listdir(folder) if f.endswith(".klar"))
    except OSError:
        return []
    import re
    out = []
    for f in names:
        try:
            if re.search(r"(?m)^fn main\(", load(os.path.join(folder, f))):
                out.append(f)
        except OSError:
            pass
    return out


def entry(arg, cmd):
    """The file a command works on: FILE as given, FOLDER/main.klar, or main.klar of the project. None after
    printing what to type instead."""
    if arg is not None and os.path.isfile(arg):
        return arg
    if arg is not None and not os.path.isdir(arg):
        import difflib
        folder = os.path.dirname(arg) or "."
        near = difflib.get_close_matches(os.path.basename(arg), [f for f in os.listdir(folder) if f.endswith(".klar")]
                                         if os.path.isdir(folder) else [], 1)
        say(f"klar {cmd}: no such file {arg}", f"klar {cmd} {os.path.join(folder, near[0]) if folder != '.' else near[0]}"
            if near else None)
        return None
    folder = arg if arg is not None else project_dir()
    main = os.path.join(folder, "main.klar")
    if os.path.isfile(main):
        return os.path.relpath(main) if arg is None else main
    found = mains_in(folder)
    where = "here" if arg is None else f"in {arg}"
    if found:
        say(f"klar {cmd}: no main.klar {where}", "name the file: " +
            "  |  ".join(f"klar {cmd} {os.path.relpath(os.path.join(folder, f))}" for f in found[:4]))
    else:
        say(f"klar {cmd}: no main.klar {where}", "start a project with `klar new NAME`, or name a .klar file")
    return None


def project_files(paths, cmd):
    """Files for check/test: the given files and folders, or every .klar file in the project."""
    from .fmt import klar_files
    if not paths:
        paths = [project_dir()]
        files = [os.path.relpath(f) for f in klar_files(paths)]
    else:
        files = list(klar_files(paths))
    for f in files:
        if not os.path.isfile(f):
            entry(f, cmd)                                   # prints the hint
            return None
    if not files:
        say(f"klar {cmd}: no .klar files here", "start a project with `klar new NAME`")
        return None
    return files


def cmd_check_all(files, as_json):
    if len(files) == 1:
        return cmd_check(files[0], as_json)
    all_errors = []
    for f in files:
        src = load(f)
        _, errors = check_source(src, f)
        for e in errors:
            e.file = e.file or f
        if not as_json:
            report(errors, src, False)
        all_errors += errors
    if as_json:
        print(to_json(all_errors))
    elif not all_errors:
        print(f"ok ({len(files)} files)")
    return 1 if all_errors else 0


def cmd_test_all(files, as_json, explicit):
    import re
    if not explicit:
        files = [f for f in files if re.search(r'(?m)^\s*test\s+"', load(f))]
        if not files:
            say("klar test: no `test` blocks in this project", 'add one: test "adds" { assert 1 + 1 == 2 }')
            return 1
    if len(files) == 1:
        return cmd_test(files[0], as_json)
    import io
    import json
    import contextlib
    status, passed, failed, tests = 0, 0, 0, []
    for f in files:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            st = cmd_test(f, True)
        status = status or st
        try:
            r = json.loads(buf.getvalue())
        except ValueError:
            r = {"ok": False, "passed": 0, "failed": 1, "tests": [], "errors": []}
        if "tests" not in r:                                # did not check: a list of errors
            r = {"passed": 0, "failed": 1, "tests": [{"name": "(check)", "ok": False, "error": e}
                                                     for e in r.get("errors", [])]}
        passed += r["passed"]
        failed += r["failed"]
        for t in r["tests"]:
            tests.append(dict(t, file=f))
        if not as_json:
            if r["tests"] and all(t["ok"] for t in r["tests"]):
                print(f"PASS  {f} ({r['passed']} tests)")
            else:
                cmd_test(f, False)                          # show the details for files that fail
    if as_json:
        print(json.dumps({"ok": failed == 0, "passed": passed, "failed": failed, "tests": tests}, indent=2))
    else:
        print(f"{passed} passed, {failed} failed ({len(files)} files)")
    return 1 if failed or status else 0


def is_ui_app(path):
    """Whether a program's main() starts a std.ui app (so `klar build` should make a web page)."""
    src = load(path)
    if "ui_" not in src:
        return False
    prog, errors = check_source(src, path)
    if errors:
        return False
    from .syntax import Node
    fns = {d.name: d for d in prog.decls if d.kind == "Fn"}
    starts = {d.name for d in prog.decls if d.kind == "Extern" and d.target == "klar:ui.start"}
    seen, todo = set(), ["main"]
    while todo:
        name = todo.pop()
        if name in starts:
            return True
        if name in seen or name not in fns:
            continue
        seen.add(name)
        stack = [fns[name].body]
        while stack:
            n = stack.pop()
            if isinstance(n, Node):
                if n.kind == "Name":
                    todo.append(n.name)
                stack.extend(v for k, v in n.__dict__.items() if k not in ("ty", "inst"))
            elif isinstance(n, (list, tuple)):
                stack.extend(n)
    return False


def package_output(path, target):
    """`klar build` of a project's main.klar is named after the package: shop, shop.html, shop.mjs."""
    folder = os.path.dirname(path)
    if not os.path.isfile(os.path.join(folder, "klar.toml")):
        return None
    from .pkg import Manifest, PkgError
    try:
        name = Manifest(os.path.abspath(folder)).name
    except PkgError:
        return None
    return os.path.join(folder, name + {"web": ".html", "node": ".mjs"}.get(target, ""))


def unknown_command(word):
    import difflib
    if word.endswith(".klar"):
        say(f"klar: `{word}` is a file, not a command", f"klar run {word}")
        return 2
    to = GUESSES.get(word) or next(iter(difflib.get_close_matches(word, list(COMMANDS), 1, 0.6)), None)
    say(f"klar: unknown command `{word}`", (f"klar {to}" if to else None))
    if not to:
        print("Run `klar help` to see the commands.", file=sys.stderr)
    return 2


COMMANDS = ["new", "run", "dev", "test", "check", "fmt", "build", "add", "remove", "install", "update", "bind",
            "audit", "spec", "explain", "help", "lsp"]


def build_main(rest):
    """`klar build`, parsed by hand: building must start fast, and argparse alone costs milliseconds."""
    opts = {"out": None, "release": False, "emit_c": False, "json": False, "file": None, "target": None}
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in ("-o", "--output") and i + 1 < len(rest):
            opts["out"] = rest[i + 1]; i += 2; continue
        if a == "--target" and i + 1 < len(rest):
            opts["target"] = rest[i + 1]; i += 2; continue
        if a.startswith("--target="):
            opts["target"] = a.split("=", 1)[1]
        elif a == "--release":
            opts["release"] = True
        elif a == "--emit-c":
            opts["emit_c"] = True
        elif a == "--json":
            opts["json"] = True
        elif opts["file"] is None and not a.startswith("-"):
            opts["file"] = a
        else:
            say(f"klar build: unexpected argument {a!r}", "klar help build")
            return 2
        i += 1
    path = entry(opts["file"], "build")
    if path is None:
        return 2
    target = opts["target"]
    if target is None:
        target = "web" if is_ui_app(path) else "native"
    if opts["out"] is None and os.path.basename(path) == "main.klar" and target != "wasm":
        opts["out"] = package_output(path, target)
    if target in ("node", "web"):
        return cmd_build_js(path, opts["out"], target, opts["json"])
    if target == "wasm":
        return cmd_build_wasm(path, opts["out"], opts["release"], opts["emit_c"], opts["json"])
    if target != "native":
        say(f"klar build: unknown target {target!r}", "choose one of: native (default), node, web, wasm")
        return 2
    return cmd_build(path, opts["out"], opts["release"], opts["emit_c"], opts["json"])


def parser():
    import argparse

    class Parser(argparse.ArgumentParser):
        def error(self, message):
            say(f"{self.prog}: {message}", f"klar help {self.prog.split()[-1]}" if " " in self.prog else "klar help")
            sys.exit(2)

    p = Parser(prog="klar", add_help=False)
    sub = p.add_subparsers(dest="cmd", parser_class=Parser)

    def cmd(name, summary):
        ex = EXAMPLES.get(name)
        return sub.add_parser(name, help=summary, description=DESCRIPTIONS.get(name, summary),
                              epilog=("examples:\n  " + ex) if ex else None,
                              formatter_class=argparse.RawDescriptionHelpFormatter)

    r = cmd("run", "check and run a program's main()")
    r.add_argument("file", nargs="?", help="a .klar file or a folder with main.klar (default: this project's)")
    r.add_argument("--json", action="store_true", help="errors as JSON")
    r.add_argument("args", nargs=argparse.REMAINDER, help="arguments for the program (see args())")
    c = cmd("check", "type-check without running")
    c.add_argument("files", nargs="*", help="files or folders (default: the whole project)")
    c.add_argument("--json", action="store_true", help="errors as JSON")
    t = cmd("test", "run all `test` blocks")
    t.add_argument("files", nargs="*", help="files or folders (default: the whole project)")
    t.add_argument("--json", action="store_true", help="results as JSON")
    b = cmd("build", "compile a program")
    b.add_argument("file", nargs="?", help="a .klar file or a folder with main.klar (default: this project's)")
    b.add_argument("-o", "--output", help="where to write (default: the file name with the target's extension)")
    b.add_argument("--target", choices=["native", "node", "web", "wasm"],
                   help="native executable, Node.js module (servers, tools), web page (browser apps) or "
                        "WebAssembly (.wasm + .mjs loader, for browsers and Node); default: web for std.ui apps, "
                        "else native")
    b.add_argument("--release", action="store_true", help="optimise with gcc/clang -O2 (default: fastest compile)")
    b.add_argument("--emit-c", action="store_true", help="write the generated C file and stop")
    b.add_argument("--json", action="store_true", help="errors as JSON")
    fm = cmd("fmt", "format .klar files in place")
    fm.add_argument("paths", nargs="*", help="files or folders (default: the whole project)")
    fm.add_argument("--check", action="store_true", help="change nothing; list unformatted files and exit 1")
    dv = cmd("dev", "serve and reload on every save")
    dv.add_argument("files", nargs="*", help="a std.ui app, a server, or both (default: main.klar, app.klar and "
                                            "server.klar of this project)")
    dv.add_argument("--port", type=int, default=int(os.environ.get("KLAR_DEV_PORT") or 8000))
    nw = cmd("new", "create a project")
    nw.add_argument("name")
    nw.add_argument("--web", action="store_true", help="a browser app (std.ui) instead of a console program")
    ad = cmd("add", "add a dependency")
    ad.add_argument("source", nargs="*", metavar="[NAME] SOURCE",
                    help="npm:NAME[@VERSION], py:NAME==VERSION, a git URL[@TAG] or a folder; NAME first to choose "
                         "the name `use` refers to")
    ad.add_argument("--path", help=argparse.SUPPRESS)
    ad.add_argument("--git", help=argparse.SUPPRESS)
    ad.add_argument("--rev", help="pin a git dependency to this commit")
    ad.add_argument("--tag", help="pin a git dependency to this tag")
    ad.add_argument("--branch", help="follow a branch (klar.lock still pins its commit)")
    ad.add_argument("--python", help=argparse.SUPPRESS)
    ad.add_argument("--npm", help=argparse.SUPPRESS)
    rm = cmd("remove", "remove a dependency")
    rm.add_argument("names", nargs="+")
    cmd("install", "fetch the dependencies")
    up = cmd("update", "update git dependencies")
    up.add_argument("names", nargs="*", help="only these dependencies (default: all)")
    bd = cmd("bind", "typed declarations for an npm package")
    bd.add_argument("module", help="the npm package (or package/subpath), e.g. marked")
    bd.add_argument("-o", "--output", help="where to write (default: npm/NAME.klar in the project)")
    bd.add_argument("--prefix", help="prefix for generated type names (default: the package name in PascalCase)")
    au = cmd("audit", "check for known vulnerabilities")
    au.add_argument("--json", action="store_true", help="machine-readable report")
    cmd("lsp", "language server")
    cmd("spec", "the language reference")
    x = cmd("explain", "explain an error code")
    x.add_argument("code")
    return p, sub


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help") and len(argv) == 1:
        print(HELP, end="")
        return 0
    if argv[0] in ("--version", "-V", "version"):
        from . import __version__
        print(f"klar {__version__}")
        return 0
    if argv[0] == "help":
        topic = argv[1]
        if topic.upper().startswith("E") and topic[1:].isdigit():
            return cmd_explain(topic)
        if topic not in COMMANDS:
            return unknown_command(topic)
        argv = [topic, "--help"]
    if argv[0] not in COMMANDS:
        return unknown_command(argv[0])
    if argv[0] == "build" and "-h" not in argv and "--help" not in argv:
        return build_main(argv[1:])
    if argv[0] == "install" and len(argv) > 1 and not argv[1].startswith("-"):
        say("klar install takes no names: it fetches what klar.toml lists",
            f"to add a dependency: klar add npm:{argv[1]}  or  klar add py:{argv[1]}==VERSION")
        return 2
    p, _ = parser()
    a = p.parse_args(argv)
    if a.cmd == "run":
        args = a.args
        if a.file is not None and not a.file.endswith(".klar") and not os.path.isdir(a.file):
            args, a.file = [a.file] + args, None             # `klar run add milk`: arguments for main.klar
        path = entry(a.file, "run")
        return 2 if path is None else cmd_run(path, a.json, args)
    if a.cmd == "check":
        files = project_files(a.files, "check")
        return 2 if files is None else cmd_check_all(files, a.json)
    if a.cmd == "test":
        files = project_files(a.files, "test")
        return 2 if files is None else cmd_test_all(files, a.json, bool(a.files))
    if a.cmd == "build":
        return build_main(argv[1:])
    if a.cmd == "audit":
        from .audit import cmd_audit
        return cmd_audit(a.json)
    if a.cmd == "lsp":
        from .lsp import cmd_lsp
        return cmd_lsp()
    if a.cmd == "fmt":
        from .fmt import cmd_fmt
        return cmd_fmt(a.paths or [project_dir()], a.check)
    if a.cmd == "dev":
        from .dev import cmd_dev
        files = a.files
        if not files or (len(files) == 1 and os.path.isdir(files[0])):
            folder = files[0] if files else project_dir()
            files = [os.path.relpath(os.path.join(folder, f)) for f in ("main.klar", "server.klar", "app.klar")
                     if os.path.isfile(os.path.join(folder, f))]
            if not files:
                say("klar dev: no main.klar, app.klar or server.klar here",
                    "name the files: klar dev server.klar app.klar  (or start one: klar new NAME --web)")
                return 2
        return cmd_dev(files, a.port)
    if a.cmd == "bind":
        from . import npm
        return npm.cmd_bind(a.module, a.output, a.prefix)
    if a.cmd in ("new", "add", "remove", "install", "update"):
        from . import pkg
        if a.cmd == "new":
            return pkg.cmd_new(a.name, a.web)
        if a.cmd == "add":
            return add_main(a, pkg)
        if a.cmd == "remove":
            return pkg.cmd_remove(a.names)
        if a.cmd == "install":
            return pkg.cmd_install()
        return pkg.cmd_install(set(a.names) if a.names else "all")
    if a.cmd == "spec":
        print(load(os.path.join(HERE, "SPEC.md")))
        return 0
    if a.cmd == "explain":
        return cmd_explain(a.code)
    return 2


def add_main(a, pkg):
    """`klar add SOURCE`, `klar add NAME SOURCE`, or the long forms with --path/--git/--python/--npm."""
    src = a.source
    if a.path or a.git or a.python or a.npm:
        if len(src) > 1:
            say("klar add: give one NAME with --path/--git", "klar help add")
            return 2
        return pkg.cmd_add(src[0] if src else None, a.path, a.git, a.rev, a.tag, a.branch, a.python, a.npm)
    if not src or len(src) > 2:
        say("klar add: say what to add", "klar add npm:marked  |  klar add py:requests==2.32.3  |  "
            "klar add https://github.com/OWNER/REPO@v1.0.0  |  klar add ../folder")
        return 2
    try:
        kw = pkg.parse_source(src[-1], src[0] if len(src) == 2 else None)
    except pkg.PkgError as ex:
        return pkg._report(ex)
    if len(src) == 2 and ("npm" in kw or "python" in kw):
        say("klar add: npm and Python packages keep their own name", f"klar add {src[1]}")
        return 2
    for k in ("rev", "tag", "branch"):
        if getattr(a, k):
            kw[k] = getattr(a, k)
    return pkg.cmd_add(kw.pop("name", None), kw.get("path"), kw.get("git"), kw.get("rev"), kw.get("tag"),
                       kw.get("branch"), kw.get("python"), kw.get("npm"))


def entry_point():
    """The `klar` command installed by pip/pipx."""
    sys.exit(main())


if __name__ == "__main__":
    sys.exit(main())
