"""Command line interface: klar run|check|test|build|dev|fmt|new|add|install|update|spec|explain."""

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


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "build" and "-h" not in argv and "--help" not in argv:
        # fast path without argparse
        rest = argv[1:]
        opts = {"out": None, "release": False, "emit_c": False, "json": False, "file": None, "target": "native"}
        i = 0
        while i < len(rest):
            a = rest[i]
            if a in ("-o", "--output") and i + 1 < len(rest):
                opts["out"] = rest[i + 1]; i += 2; continue
            if a == "--target" and i + 1 < len(rest):
                opts["target"] = rest[i + 1]; i += 2; continue
            if a == "--release":
                opts["release"] = True
            elif a == "--emit-c":
                opts["emit_c"] = True
            elif a == "--json":
                opts["json"] = True
            elif opts["file"] is None and not a.startswith("-"):
                opts["file"] = a
            else:
                print(f"klar build: unexpected argument {a!r}", file=sys.stderr)
                return 2
            i += 1
        if opts["file"] is None:
            print("usage: klar build FILE [--target native|node|web|wasm] [-o OUT] [--release] [--emit-c] [--json]", file=sys.stderr)
            return 2
        if opts["target"] in ("node", "web"):
            return cmd_build_js(opts["file"], opts["out"], opts["target"], opts["json"])
        if opts["target"] == "wasm":
            return cmd_build_wasm(opts["file"], opts["out"], opts["release"], opts["emit_c"], opts["json"])
        if opts["target"] != "native":
            print(f"klar build: unknown target {opts['target']!r} (choose native, node, web or wasm)", file=sys.stderr)
            return 2
        return cmd_build(opts["file"], opts["out"], opts["release"], opts["emit_c"], opts["json"])
    import argparse
    p = argparse.ArgumentParser(prog="klar", description="The Klar language: run, check and test .klar files.")
    p.add_argument("--version", action="version", version=f"klar {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="check and run a program's main()")
    r.add_argument("file")
    r.add_argument("--json", action="store_true", help="emit errors as JSON")
    r.add_argument("args", nargs=argparse.REMAINDER, help="arguments passed to the program (see args())")
    c = sub.add_parser("check", help="type-check without running")
    c.add_argument("file")
    c.add_argument("--json", action="store_true")
    t = sub.add_parser("test", help="run all `test` blocks")
    t.add_argument("file")
    t.add_argument("--json", action="store_true")
    b = sub.add_parser("build", help="compile to a native executable (via C)")
    b.add_argument("file")
    b.add_argument("-o", "--output", help="executable path (default: file name without .klar)")
    b.add_argument("--release", action="store_true", help="optimise with gcc/clang -O2 (default: fastest compile, tcc if installed)")
    b.add_argument("--emit-c", action="store_true", help="write the generated C file and stop")
    b.add_argument("--json", action="store_true")
    b.add_argument("--target", default="native", choices=["native", "node", "web", "wasm"],
                   help="native executable (default), Node.js module (servers, tools), web page (browser apps) "
                        "or WebAssembly (.wasm + .mjs loader, for browsers and Node)")
    fm = sub.add_parser("fmt", help="format .klar files in place (one canonical layout)")
    fm.add_argument("paths", nargs="+", help="files or directories")
    fm.add_argument("--check", action="store_true", help="change nothing; list unformatted files and exit 1")
    dv = sub.add_parser("dev", help="serve a web app or backend and rebuild + reload the browser on every save")
    dv.add_argument("files", nargs="+", help="a std.ui app, a server, or both")
    dv.add_argument("--port", type=int, default=int(os.environ.get("KLAR_DEV_PORT") or 8000))
    nw = sub.add_parser("new", help="create a package folder with klar.toml and main.klar")
    nw.add_argument("name")
    ad = sub.add_parser("add", help="add a dependency to klar.toml and install it")
    ad.add_argument("name", nargs="?", help="the name `use` will refer to")
    ad.add_argument("--path", help="a local package folder")
    ad.add_argument("--git", help="a git repository URL")
    ad.add_argument("--rev", help="a commit")
    ad.add_argument("--tag", help="a tag, e.g. v1.2.0")
    ad.add_argument("--branch", help="a branch (the lock file still pins its commit)")
    ad.add_argument("--python", metavar="NAME==VERSION", help="a pinned Python package for extern functions")
    ad.add_argument("--npm", metavar="NAME[@VERSION]", help="a pinned npm package for \"js:\" extern functions")
    bd = sub.add_parser("bind", help="write Klar extern declarations for an npm package from its TypeScript types")
    bd.add_argument("module", help="the npm package (or package/subpath), e.g. marked")
    bd.add_argument("-o", "--output", help="where to write (default: npm/NAME.klar in the project)")
    bd.add_argument("--prefix", help="prefix for generated type names (default: the package name in PascalCase)")
    sub.add_parser("install", help="fetch the dependencies klar.toml and klar.lock name")
    up = sub.add_parser("update", help="move git dependencies to the newest commit their tag/branch allows")
    up.add_argument("names", nargs="*", help="only these dependencies (default: all)")
    au = sub.add_parser("audit", help="check dependencies and Klar itself against known vulnerabilities")
    au.add_argument("--json", action="store_true", help="machine-readable report")
    sub.add_parser("lsp", help="run the language server (LSP over stdin/stdout) for editors")
    sub.add_parser("spec", help="print the complete language reference")
    x = sub.add_parser("explain", help="explain an error code, e.g. klar explain E306")
    x.add_argument("code")
    a = p.parse_args(argv)
    if a.cmd == "run":
        return cmd_run(a.file, a.json, a.args)
    if a.cmd == "build":
        if a.target == "wasm":
            return cmd_build_wasm(a.file, a.output, a.release, a.emit_c, a.json)
        if a.target != "native":
            return cmd_build_js(a.file, a.output, a.target, a.json)
        return cmd_build(a.file, a.output, a.release, a.emit_c, a.json)
    if a.cmd == "check":
        return cmd_check(a.file, a.json)
    if a.cmd == "audit":
        from .audit import cmd_audit
        return cmd_audit(a.json)
    if a.cmd == "lsp":
        from .lsp import cmd_lsp
        return cmd_lsp()
    if a.cmd == "fmt":
        from .fmt import cmd_fmt
        return cmd_fmt(a.paths, a.check)
    if a.cmd == "dev":
        from .dev import cmd_dev
        return cmd_dev(a.files, a.port)
    if a.cmd == "test":
        return cmd_test(a.file, a.json)
    if a.cmd == "bind":
        from . import npm
        return npm.cmd_bind(a.module, a.output, a.prefix)
    if a.cmd in ("new", "add", "install", "update"):
        from . import pkg
        if a.cmd == "new":
            return pkg.cmd_new(a.name)
        if a.cmd == "add":
            return pkg.cmd_add(a.name, a.path, a.git, a.rev, a.tag, a.branch, a.python, a.npm)
        if a.cmd == "install":
            return pkg.cmd_install()
        return pkg.cmd_install(set(a.names) if a.names else "all")
    if a.cmd == "spec":
        print(load(os.path.join(HERE, "SPEC.md")))
        return 0
    if a.cmd == "explain":
        return cmd_explain(a.code)


if __name__ == "__main__":
    sys.exit(main())
