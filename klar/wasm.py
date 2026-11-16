"""WebAssembly builds: Klar -> C (compiler.py, target "wasm") -> clang --target=wasm32 -> .wasm.

Needs only clang with its WebAssembly target and the wasm-ld linker (LLVM's lld); no C library, wasi-sdk or
Emscripten: the runtime brings the little libc it uses (runtime/wasm/klar_wasm.h) and the JavaScript host
(runtime/klar_wasm.js) supplies output, float formatting, math and clocks.

Two ways in:
  klar build --target wasm prog.klar      -> prog.wasm + prog.mjs (node prog.mjs, or import it in a page)
  extern "wasm:mod.fn" fn f(...) -> ...   in a --target web / node app: mod.klar is compiled to WebAssembly and
                                          f calls it (see jscompiler.py)
"""

import base64
import os
import subprocess
import tempfile

from .errors import KlarError

RT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime")
WASM_INCLUDE = os.path.join(RT_DIR, "wasm", "include")
CANDIDATES = ["clang", "clang-20", "clang-19", "clang-18", "clang-17", "clang-16", "clang-15",
              "/opt/homebrew/opt/llvm/bin/clang", "/usr/local/opt/llvm/bin/clang"]
FLAGS = ["--target=wasm32", "-std=gnu11", "-w", "-ffreestanding", "-nostdlib", "-nostdlibinc", "-isystem", WASM_INCLUDE,
         "-mbulk-memory", "-mnontrapping-fptoint", "-msign-ext", "-mmutable-globals",
         "-Wl,--no-entry", "-Wl,--stack-first", "-Wl,-z,stack-size=8388608", "-Wl,--export=__stack_pointer",
         "-Wl,--strip-all"]
HINT = ("install clang and lld (Debian/Ubuntu: apt install clang lld; Fedora: dnf install clang lld; "
        "macOS: brew install llvm lld, since Apple's clang has no WebAssembly target), or set KLAR_WASM_CC")
_found = {}


def find_wasm_cc():
    """A clang that can compile and link wasm32, or None."""
    if "cc" in _found:
        return _found["cc"]
    env = os.environ.get("KLAR_WASM_CC")
    cands = [env] if env else CANDIDATES
    found = None
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "t.c")
        with open(src, "w") as f:
            f.write('__attribute__((export_name("f"))) int f(int x) { return x + 1; }\n')
        for cc in cands:
            try:
                r = subprocess.run([cc, "--target=wasm32", "-nostdlib", "-Wl,--no-entry", "-o", os.path.join(d, "t.wasm"), src],
                                   capture_output=True, timeout=60)
            except (OSError, subprocess.TimeoutExpired):
                continue
            if r.returncode == 0:
                found = cc
                break
    _found["cc"] = found
    return found


def c_to_wasm(c_src, release=True, where="<input>"):
    """Compile generated C to WebAssembly bytes."""
    cc = find_wasm_cc()
    if cc is None:
        raise KlarError("E502", "no C compiler with a WebAssembly target was found", 1, 1, HINT, where)
    with tempfile.TemporaryDirectory() as d:
        c_path, out = os.path.join(d, "m.c"), os.path.join(d, "m.wasm")
        with open(c_path, "w", encoding="utf-8") as f:
            f.write(c_src)
        r = subprocess.run([cc, "-O3" if release else "-O1"] + FLAGS + ["-o", out, c_path], capture_output=True, text=True)
        if r.returncode != 0:
            keep = os.path.abspath("klar_wasm_failed.c")
            with open(keep, "w", encoding="utf-8") as f:
                f.write(c_src)
            raise KlarError("E503", "internal compiler error: the generated C did not compile to WebAssembly", 1, 1,
                            f"please report this with the program; the C file is {keep}\n{r.stderr[:2000]}", where)
        with open(out, "rb") as f:
            return f.read()


def host_js():
    with open(os.path.join(RT_DIR, "klar_wasm.js"), encoding="utf-8") as f:
        return f.read()


RUNNER = """
const $here = typeof import.meta !== "undefined" ? import.meta.url : "";
const $bytes = $wasm_fs ? $wasm_fs.readFileSync(new URL(%(name)s, $here))
  : new Uint8Array(await (await fetch(new URL(%(name)s, $here))).arrayBuffer());
const $w = await $wasm_load($bytes, { echo: true });
let $status = 0;
try { $w.ex.klar_main(); }
catch (e) {
  if (e instanceof $WasmExit) $status = e.code;
  else if (e instanceof RangeError || e instanceof WebAssembly.RuntimeError) {
    const msg = "error[E406] stack overflow (recursion too deep) or invalid memory access\\n";
    if ($wasm_fs) $wasm_fs.writeSync(2, msg); else console.error(msg.trim());
    $status = 1;
  } else throw e;
}
$wasm_flush($w);
if ($wasm_fs) process.exitCode = $status;
export const status = $status;
"""


def runner_js(wasm_name):
    import json
    return host_js() + RUNNER % {"name": json.dumps(wasm_name)}


def b64(data):
    return base64.b64encode(data).decode("ascii")


# ------------------------------------------------------------------ extern "wasm:module.function"
def parse_target(target, declared_in):
    """("…/module.klar", "function") for an extern target "wasm:module.function", relative to the declaring file."""
    spec = target[len("wasm:"):]
    mod, _, fn = spec.rpartition(".")
    base = os.path.dirname(os.path.abspath(declared_in)) if declared_in else os.getcwd()
    return os.path.normpath(os.path.join(base, mod + ".klar")), fn


def load_module(path):
    """Check a WebAssembly module's source: (prog, errors)."""
    from .checker import check_source
    with open(path, encoding="utf-8") as f:
        return check_source(f.read(), path)


def check_extern(d, app_checker, mod_prog, fn, path):
    """The extern's declared signature must be the module function's (names of types, effects)."""
    from .types import show
    mc = mod_prog.checker
    rel = os.path.basename(path)
    if fn not in mc.fns or mc.fns[fn].builtin or fn not in {x.name for x in mod_prog.decls if x.kind == "Fn"}:
        names = sorted(x.name for x in mod_prog.decls if x.kind == "Fn" and getattr(x, "module", path) == path)
        raise KlarError("E201", f"extern `{d.name}`: {rel} has no function `{fn}`", d.line, d.col,
                        f"functions in {rel}: {', '.join(names) or 'none'}", d.module)
    ms, es = mc.fns[fn], app_checker.fns[d.name]
    want = f"fn({', '.join(show(t) for _, t in ms.params)}) -> {show(ms.ret)}"
    have = f"fn({', '.join(show(t) for _, t in es.params)}) -> {show(es.ret)}"
    if want != have or ("io" in ms.effects) != ("io" in es.effects):
        io = " uses io" if "io" in ms.effects else ""
        params = ", ".join(f"{p}: {show(t)}" for p, t in ms.params)
        raise KlarError("E301", f"extern `{d.name}` is declared as {have}{' uses io' if 'io' in es.effects else ''}, "
                        f"but `{fn}` in {rel} is {want}{io}", d.line, d.col,
                        f'declare it as: extern "{d.target}" fn {d.name}({params}) -> {show(ms.ret)}{io}', d.module)
