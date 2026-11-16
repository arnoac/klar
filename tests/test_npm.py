"""npm packages from Klar: typed "js:" externs on Node and in the browser, [npm] in klar.toml, and `klar bind`.

The packages here are small fakes written into a temporary node_modules, so no network is needed. The
markdown_notes example is built against its real packages when they have been installed (`klar install`).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")
sys.path.insert(0, ROOT)


def klar(*args, cwd=None):
    env = dict(os.environ, PYTHONPATH=ROOT)
    return subprocess.run([sys.executable, "-m", "klar", *args], capture_output=True, text=True, env=env, cwd=cwd or ROOT)


def put(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def project():
    """A temporary project with three fake npm packages:
    greet   (one ES module file, a class, a getter, a function that can throw, an async function, callbacks)
    multi   (ES modules importing each other and another package; a comment that looks like an import;
             a "browser" export condition)
    oldcjs  (CommonJS only)"""
    d = tempfile.mkdtemp()
    put(d, "node_modules/greet/package.json", json.dumps({"name": "greet", "version": "1.0.0", "type": "module",
                                                           "exports": {".": {"types": "./index.d.ts", "default": "./index.js"}}}))
    put(d, "node_modules/greet/index.js", """
export function hello(name) { return "hello " + name; }
export function maybe(x) { return x > 0 ? x * 2 : null; }
export function boom(x) { if (x < 0) throw new RangeError("negative: " + x); return x; }
export async function later(x) { return x + 1; }
export function sneaky() { return Promise.resolve(1); }
export function apply(f, xs) { return xs.map(f); }
export function opts(o) { return Object.keys(o).sort().join(",") + ":" + (o.loud ? "LOUD" : "quiet"); }
export function adder(n) { return (x) => x + n; }
export const version = "1.0.0";
export class Counter {
  constructor(start) { this.count = start; }
  add(n) { this.count += n; return this; }
  get doubled() { return this.count * 2; }
}
""")
    put(d, "node_modules/greet/index.d.ts", """
/** Says hello. */
export declare function hello(name: string): string;
export declare function maybe(x: number): number | null;
export declare function later(x: number): Promise<number>;
export interface Options { loud?: boolean; name: string }
export declare function opts(o: Options): string;
export declare function apply(f: (x: number) => number, xs: number[]): number[];
export declare function anything(x: any): void;
export declare const version: string;
export declare class Counter {
  constructor(start: number);
  count: number;
  add(n: number): Counter;
  private secret;
}
""")
    put(d, "node_modules/multi/package.json", json.dumps({"name": "multi", "version": "2.0.0", "type": "module",
                                                           "exports": {".": {"browser": "./browser.js", "default": "./node.js"}}}))
    put(d, "node_modules/multi/node.js", 'export { shout } from "./lib/shout.js";\nexport const where = "node";\n')
    put(d, "node_modules/multi/browser.js", """// import { nope } from "./does-not-exist.js";
/* export * from "./also-missing.js" */
export { shout } from "./lib/shout.js";
import { hello } from "greet";
export const where = "browser";
export const greeting = hello("web");
""")
    put(d, "node_modules/multi/lib/shout.js", 'import { up } from "./up.js";\nexport function shout(s) { return up(s) + "!"; }\n')
    put(d, "node_modules/multi/lib/up.js", "export const up = s => s.toUpperCase();\n")
    put(d, "node_modules/oldcjs/package.json", json.dumps({"name": "oldcjs", "version": "1.0.0", "main": "index.js"}))
    put(d, "node_modules/oldcjs/index.js", "module.exports.twice = x => x * 2;\n")
    return d


GREET = """
extern "js:greet.hello" fn hello(name: str) -> str
extern "js:greet.maybe" fn maybe(x: int) -> Option[int]
extern "js:greet.maybe" fn maybe_bad(x: int) -> int
extern "js:greet.boom" fn boom(x: int) -> Result[int, str]
extern "js:greet.boom" fn boom_unchecked(x: int) -> int
extern "js:greet.later" fn later(x: int) -> int uses io
extern "js:greet.sneaky" fn sneaky() -> int
extern "js:greet.apply" fn apply(f: fn(int) -> int, xs: list[int]) -> list[int]
extern "js:greet.adder" fn adder(n: int) -> fn(int) -> int
struct Opts { loud: Option[bool], name: str }
extern "js:greet.opts" fn opts(o: Opts) -> str
extern "js:get greet.version" fn version() -> str
extern type Counter
extern "js:new greet.Counter" fn counter(start: int) -> Counter
extern "js:.add" fn add(c: Counter, n: int) -> Counter
extern "js:get .count" fn count(c: Counter) -> int
extern "js:get .doubled" fn doubled(c: Counter) -> int
extern "js:set .count" fn set_count(c: Counter, n: int) uses io
"""


@unittest.skipUnless(NODE, "needs node")
class NodeTarget(unittest.TestCase):
    def run_prog(self, d, body, name="main.klar"):
        put(d, name, GREET + "fn main() uses io {\n" + body + "\n}\n")
        out = os.path.join(d, "out.mjs")
        b = klar("build", "--target", "node", os.path.join(d, name), "-o", out)
        self.assertEqual(b.returncode, 0, b.stderr)
        return subprocess.run([NODE, out], capture_output=True, text=True)

    def test_calls_convert_by_type(self):
        d = project()
        r = self.run_prog(d, """
  print(hello("klar"))
  print(maybe(4))
  print(maybe(-1))
  print(boom(3))
  print(boom(-2))
  print(later(41))
  print(apply(fn(x) => x * 10, [1, 2]))
  let plus2 = adder(2)
  print(plus2(5))
  print(opts(Opts(None, "a")))
  print(opts(Opts(Some(true), "a")))
  print(version())
  let c = add(add(counter(1), 2), 3)
  print(count(c))
  print(doubled(c))
  set_count(c, 10)
  print(count(c))""")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split("\n"), ["hello klar", "Some(8)", "None", "Ok(3)", 'Err("RangeError: negative: -2")',
                                                "42", "[10, 20]", "7", "name:quiet", "loud,name:LOUD", "1.0.0",
                                                "6", "12", "10", ""])
        self.assertIn('from "greet"', open(os.path.join(d, "out.mjs")).read())   # node finds node_modules itself

    def test_null_is_not_a_value(self):
        r = self.run_prog(project(), "  print(maybe_bad(-1))")
        self.assertEqual(r.returncode, 1)
        self.assertIn("E407", r.stderr)
        self.assertIn("returned null", r.stderr)
        self.assertIn("Option", r.stderr)

    def test_exception_without_result_stops_with_e407(self):
        r = self.run_prog(project(), "  print(boom_unchecked(-1))")
        self.assertIn("E407", r.stderr)
        self.assertIn("RangeError: negative: -1", r.stderr)
        self.assertIn("Result[T, str]", r.stderr)

    def test_promise_needs_uses_io(self):
        r = self.run_prog(project(), "  print(sneaky())")
        self.assertIn("E407", r.stderr)
        self.assertIn("uses io", r.stderr)

    def test_output_elsewhere_imports_the_file(self):
        d = project()
        put(d, "main.klar", GREET + 'fn main() uses io { print(hello("x")) }\n')
        out = os.path.join(tempfile.mkdtemp(), "o.mjs")
        b = klar("build", "--target", "node", os.path.join(d, "main.klar"), "-o", out)
        self.assertEqual(b.returncode, 0, b.stderr)
        self.assertIn("file://", open(out).read())
        self.assertEqual(subprocess.run([NODE, out], capture_output=True, text=True).stdout, "hello x\n")

    def test_missing_package_is_e601(self):
        d = tempfile.mkdtemp()
        put(d, "main.klar", 'extern "js:nothere.f" fn f() -> int\nfn main() uses io { print(f()) }\n')
        b = klar("build", "--target", "node", os.path.join(d, "main.klar"), "-o", os.path.join(d, "o.mjs"))
        self.assertEqual(b.returncode, 1)
        self.assertIn("E601", b.stderr)
        self.assertIn("klar add --npm nothere", b.stderr)

    def test_member_target_needs_an_object(self):
        d = tempfile.mkdtemp()
        put(d, "main.klar", 'extern "js:.size" fn size() -> int\nfn main() uses io { print(size()) }\n')
        b = klar("build", "--target", "node", os.path.join(d, "main.klar"), "-o", os.path.join(d, "o.mjs"))
        self.assertIn("E501", b.stderr)


class Bundling(unittest.TestCase):
    def test_module_graph_is_embedded_once_each(self):
        from klar import npm
        d = project()
        b = npm.WebBundler()
        key = b.name(npm.resolve_module("multi", d, npm.WEB_CONDITIONS))
        self.assertEqual(key, "@klar-npm/0")
        self.assertEqual(len(b.urls), 4)          # browser.js, shout.js, up.js, greet/index.js; not the commented ones
        import urllib.parse
        main = urllib.parse.unquote(b.urls[key].split(",", 1)[1])
        self.assertIn('// import { nope } from "./does-not-exist.js"', main)
        self.assertIn('from "@klar-npm/', main)
        self.assertNotIn('"greet"', main)
        self.assertIn('"importmap"', b.head())

    def test_conditions(self):
        from klar import npm
        d = project()
        self.assertTrue(npm.resolve_module("multi", d, npm.NODE_CONDITIONS).endswith("node.js"))
        self.assertTrue(npm.resolve_module("multi", d, npm.WEB_CONDITIONS).endswith("browser.js"))

    def test_commonjs_is_refused_in_the_browser(self):
        from klar import npm
        d = project()
        with self.assertRaises(npm.NpmError) as cm:
            npm.WebBundler().name(npm.resolve_module("oldcjs", d, npm.WEB_CONDITIONS))
        self.assertIn("CommonJS", cm.exception.msg)

    def test_target_forms(self):
        from klar import npm
        self.assertEqual(npm.parse_target("new marked.Marked"), ("new", "marked.Marked", None))
        self.assertEqual(npm.parse_target(".parse"), ("call", None, ["parse"]))
        self.assertEqual(npm.parse_target("get .length"), ("get", None, ["length"]))
        d = project()
        put(d, "node_modules/highlight.js/package.json", '{"name": "highlight.js", "main": "x.js"}')
        self.assertEqual(npm.split_module("highlight.js.default.highlight", d), ("highlight.js", ["default", "highlight"]))
        self.assertEqual(npm.split_module("node:crypto.randomUUID", d), ("node:crypto", ["randomUUID"]))
        self.assertEqual(npm.split_module("greet.Counter", d), ("greet", ["Counter"]))

    @unittest.skipUnless(NODE, "needs node")
    def test_web_page_runs_in_a_browser(self):
        try:
            import playwright  # noqa: F401
        except ImportError:
            pass
        d = project()
        put(d, "app.klar", """use std.ui { Next, ui_run }
use std.html { Html, text }
extern "js:multi.shout" fn shout(s: str) -> str
extern "js:get multi.where" fn where_am_i() -> str
extern "js:get multi.greeting" fn greeting() -> str
fn view(m: int) -> Html[int] { return text(shout("hi") + " " + where_am_i() + " " + greeting()) }
fn update(m: int, msg: int) -> Next[int, int] { return Next(m, []) }
fn main() uses io { ui_run(Next(0, []), update, view) }
""")
        out = os.path.join(d, "app.html")
        b = klar("build", "--target", "web", os.path.join(d, "app.klar"), "-o", out)
        self.assertEqual(b.returncode, 0, b.stderr)
        page = open(out).read()
        self.assertLess(page.index('type="importmap"'), page.index('type="module"'))
        script = """const { chromium } = require('playwright');
(async () => { const b = await chromium.launch(); const p = await b.newPage();
await p.goto('file://' + process.argv[1]); await p.waitForTimeout(300);
console.log(await p.innerText('#app')); await b.close(); })();"""
        r = subprocess.run([NODE, "-e", script, out], capture_output=True, text=True)
        if "Cannot find module 'playwright'" in r.stderr:
            self.skipTest("playwright not installed")
        self.assertEqual(r.stdout.strip(), "HI! browser hello web")


class Manifest(unittest.TestCase):
    def test_npm_versions_must_be_exact(self):
        from klar import pkg
        d = tempfile.mkdtemp()
        put(d, "klar.toml", '[package]\nname = "x"\n[npm]\nmarked = "^18.0.0"\n')
        with self.assertRaises(pkg.PkgError) as cm:
            pkg.Manifest(d)
        self.assertIn("exact version", str(cm.exception))
        put(d, "klar.toml", '[package]\nname = "x"\n[npm]\nmarked = "18.2.0"\n"@scope/pkg" = "1.0.0-beta.1"\n')
        self.assertEqual(pkg.Manifest(d).npm, {"marked": "18.2.0", "@scope/pkg": "1.0.0-beta.1"})


def typescript_dir():
    from klar import npm
    return npm._typescript(tempfile.mkdtemp())


@unittest.skipUnless(NODE and typescript_dir(), "needs node and the typescript package")
class Bind(unittest.TestCase):
    def test_bind_writes_checked_declarations(self):
        d = project()
        put(d, "klar.toml", '[package]\nname = "x"\n')
        r = klar("bind", "greet", cwd=d)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("use npm.greet", r.stderr)
        src = open(os.path.join(d, "npm", "greet.klar")).read()
        self.assertIn('// Says hello.\nextern "js:greet.hello" fn hello(name: str) -> Result[str, str] uses io', src)
        self.assertIn('fn maybe(x: float) -> Result[Option[float], str] uses io', src)
        self.assertIn('fn later(x: float) -> Result[float, str] uses io', src)
        self.assertIn('struct GreetOptions {\n  loud: Option[bool],\n  name: str,\n}', src)
        self.assertIn('fn apply(f: fn(float) -> float, xs: list[float])', src)
        self.assertIn('extern "js:new greet.Counter" fn new_counter(start: int) -> Result[GreetCounter, str] uses io', src)
        self.assertIn('extern "js:.add" fn counter_add(counter: GreetCounter, n: int) -> Result[GreetCounter, str] uses io', src)
        self.assertIn('extern "js:get .count" fn counter_count(counter: GreetCounter) -> float', src)
        self.assertNotIn("secret", src)
        self.assertIn("//   anything: `any` says nothing about the value", src)
        # and the bindings work
        put(d, "main.klar", 'use npm.greet { hello, new_counter, counter_add, counter_count }\n'
                            'fn main() uses io {\n  print(hello("bound"))\n'
                            '  match new_counter(1) { Ok(c) => print(counter_count(unwrap_or(counter_add(c, 2), c))), Err(e) => print(e) }\n}\n')
        out = os.path.join(d, "o.mjs")
        b = klar("build", "--target", "node", os.path.join(d, "main.klar"), "-o", out)
        self.assertEqual(b.returncode, 0, b.stderr)
        self.assertEqual(subprocess.run([NODE, out], capture_output=True, text=True).stdout, 'Ok("hello bound")\n3.0\n')


EXAMPLE = os.path.join(ROOT, "examples", "apps", "markdown_notes")


class Example(unittest.TestCase):
    def test_app_tests_pass(self):
        r = klar("test", os.path.join(EXAMPLE, "app.klar"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    @unittest.skipUnless(os.path.isdir(os.path.join(EXAMPLE, "node_modules", "marked")), "run `klar install` in the example first")
    def test_export_script_uses_marked(self):
        md = put(tempfile.mkdtemp(), "n.md", "# Title\n\n*hi*\n")
        out = os.path.join(tempfile.mkdtemp(), "e.mjs")
        b = klar("build", "--target", "node", os.path.join(EXAMPLE, "export.klar"), "-o", out)
        self.assertEqual(b.returncode, 0, b.stderr)
        r = subprocess.run([NODE, out, md], capture_output=True, text=True)
        self.assertIn("<h1>Title</h1>", r.stdout)
        self.assertIn("<em>hi</em>", r.stdout)


if __name__ == "__main__":
    unittest.main()
