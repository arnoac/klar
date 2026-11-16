// The playground's compiler: Klar's own checker, JavaScript backend, test runner and formatter, running in this
// Web Worker on Pyodide (CPython compiled to WebAssembly). It only turns source text into results; it never
// runs the program it compiles (playground.js does that, in a sandboxed frame).
//
// Messages in:  { id, cmd: "build" | "test" | "fmt", src }
// Messages out: { status } while loading, { ready: true, version } once loaded, then { id, ...result } per request.

import { loadPyodide } from "./pyodide/pyodide.mjs";

const SETUP = String.raw`
import contextlib, io, json, os, sys
sys.path.insert(0, "/klar-src")
os.makedirs("/play", exist_ok=True)
os.chdir("/play")

from klar import __version__, cli
from klar.checker import check_source
from klar.errors import KlarError
from klar.fmt import format_source
from klar.jscompiler import compile_to_js

PATH = "main.klar"

def _errors(errors, src):
    return [dict(e.to_dict(), rendered=e.render(src)) for e in errors]

def _check(src):
    with open(PATH, "w", encoding="utf-8") as f:
        f.write(src)
    prog, errors = check_source(src, PATH)
    return prog, errors

def klar_build(src):
    prog, errors = _check(src)
    if not errors:
        e = cli.check_main_signature(prog, PATH)
        if e:
            errors = [e]
    if errors:
        return json.dumps({"ok": False, "errors": _errors(errors, src)})
    ui = cli.is_ui_app(PATH)
    try:
        js = compile_to_js(prog, PATH, "web", "main.html")
    except KlarError as e:
        return json.dumps({"ok": False, "errors": _errors([e], src)})
    page = ""
    code = str(js)
    if ui:
        page = cli.WEB_PAGE.format(title="Klar app", head=getattr(js, "head", ""), js=code.replace("</script", "<\\/script"))
    elif getattr(js, "head", "") or code.startswith("import "):
        return json.dumps({"ok": False, "errors": [], "notice": "npm packages are not available in the playground."})
    else:
        # A classic worker script (module workers cannot start from a sandboxed frame), which says when main()
        # has finished.
        i = code.rfind("$start(")
        code = ("(async () => {\n" + code[:i] + "await $start(" + code[i + 7:]
                + "\n})().finally(() => postMessage({ kind: 'done' }));\n")
    return json.dumps({"ok": True, "ui": ui, "page": page, "js": "" if ui else code})

def klar_test(src):
    prog, errors = _check(src)
    if errors:
        return json.dumps({"ok": False, "errors": _errors(errors, src)})
    from klar.interp import Interpreter
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        results = Interpreter(prog, PATH).run_tests()
    tests = [{"name": n, "ok": e is None, "error": e.render(src) if e else None} for n, e in results]
    return json.dumps({"ok": True, "tests": tests, "output": out.getvalue()})

def klar_fmt(src):
    try:
        return json.dumps({"ok": True, "src": format_source(src)})
    except KlarError as e:
        return json.dumps({"ok": False, "errors": _errors([e], src)})
`;

async function start() {
  postMessage({ status: "Loading Python (WebAssembly)..." });
  const base = new URL("./", import.meta.url);
  const py = await loadPyodide({ indexURL: new URL("pyodide/", base).href });
  postMessage({ status: "Loading the Klar compiler..." });
  const zip = await fetch(new URL("klar.zip", base));
  if (!zip.ok) throw new Error(`klar.zip: ${zip.status}`);
  py.unpackArchive(await zip.arrayBuffer(), "zip", { extractDir: "/klar-src" });
  py.runPython(SETUP);
  return py;
}

const ready = start();
ready.then(
  py => postMessage({ ready: true, version: py.globals.get("__version__") }),
  e => postMessage({ failed: String(e && e.message || e) }),
);

onmessage = async event => {
  const { id, cmd, src } = event.data;
  try {
    const py = await ready;
    const fn = py.globals.get({ build: "klar_build", test: "klar_test", fmt: "klar_fmt" }[cmd]);
    const result = JSON.parse(fn(src));
    fn.destroy();
    postMessage({ id, ...result });
  } catch (e) {
    postMessage({ id, ok: false, crash: String(e && e.message || e) });
  }
};
