"""npm packages for the JavaScript targets.

A program names an npm package in an extern target: `extern "js:marked.parse" fn md(src: str) -> str`.
This module finds the installed package (node_modules, found by walking up from the program), picks the file
to load the way Node and bundlers do (package.json "exports" with conditions, then "module"/"main"), and

  - for `--target node` emits an import specifier that works from where the output is written;
  - for `--target web` bundles the package's ES modules into the page as nested `data:` URL modules, so the
    built .html stays one self-contained file that works offline. Identical files get identical URLs, so the
    browser evaluates each module once.

Packages are declared in klar.toml `[npm]` and installed with `klar install` (see pkg.py).
"""

import json
import os
import re
import urllib.parse

NODE_CONDITIONS = ("node", "import", "module", "default")
WEB_CONDITIONS = ("browser", "import", "module", "default")


class NpmError(Exception):
    def __init__(self, msg, hint=None):
        super().__init__(msg)
        self.msg = msg
        self.hint = hint


# ---------------------------------------------------------------------------------------------- targets

def parse_target(spec):
    """Split the text after "js:" into (op, module, path).

    op is "call", "new", "get" or "set"; module is None for a member of the first argument (".name") or a
    global ("globalThis.X"); path is the attribute path as a list of names.
        "marked.parse"            -> ("call", "marked", ["parse"])
        "new marked.Marked"       -> ("new", "marked", ["Marked"])
        ".parse"                  -> ("call", None, ["parse"])        method of the first argument
        "get .length"             -> ("get", None, ["length"])
        "globalThis.Math.hypot"   -> ("call", None, ["globalThis", "Math", "hypot"])
    The module part is resolved later (it may itself contain dots, like "highlight.js")."""
    op = "call"
    for word in ("new", "get", "set"):
        if spec.startswith(word + " "):
            op, spec = word, spec[len(word) + 1:].strip()
    if spec.startswith("."):
        return op, None, spec[1:].split(".")
    if spec.startswith("globalThis."):
        return op, None, spec.split(".")
    return op, spec, None


def package_name(module):
    """The npm package part of a module specifier: "marked", "@scope/pkg", "date-fns" (of "date-fns/locale")."""
    parts = module.split("/")
    return "/".join(parts[:2]) if module.startswith("@") else parts[0]


def is_bare(module):
    return not (module.startswith(("node:", ".", "/", "data:", "http:", "https:", "file:")))


def find_package(name, start_dir):
    """The folder of an installed npm package, looking in node_modules from start_dir upwards."""
    d = os.path.abspath(start_dir)
    while True:
        cand = os.path.join(d, "node_modules", *name.split("/"))
        if os.path.isfile(os.path.join(cand, "package.json")):
            return cand
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def split_module(spec, start_dir):
    """Split "MODULE.attr.path" into (module, [attr...]). The module may contain dots ("highlight.js") or a
    subpath ("date-fns/locale"), so the longest prefix that is an installed package (or a node: module, or an
    existing local file) wins."""
    if spec.startswith("node:"):
        mod, _, rest = spec.partition(".")
        return mod, rest.split(".") if rest else []
    cuts = [i for i, ch in enumerate(spec) if ch == "."]
    for i in reversed(cuts):
        mod = spec[:i]
        if not mod:
            continue
        if mod.startswith((".", "/")):
            if os.path.isfile(os.path.join(start_dir, mod)):
                return mod, spec[i + 1:].split(".")
            continue
        if is_bare(mod) and find_package(package_name(mod), start_dir):
            return mod, spec[i + 1:].split(".")
    mod, _, attr = spec.rpartition(".")
    if not mod:
        return spec, []
    if mod.startswith(".") and not mod.startswith(("./", "../")):
        mod, attr = spec, ""
    return mod, attr.split(".") if attr else []


# ---------------------------------------------------------------------------------------------- resolution

def _pick(entry, conditions):
    """Resolve a package.json "exports" value with the given conditions."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, list):
        for e in entry:
            r = _pick(e, conditions)
            if r:
                return r
        return None
    if isinstance(entry, dict):
        for k, v in entry.items():
            if k in conditions:
                r = _pick(v, conditions)
                if r:
                    return r
    return None


def _exports_target(exports, sub, conditions):
    if isinstance(exports, (str, list)) or (isinstance(exports, dict) and not any(k.startswith(".") for k in exports)):
        return _pick(exports, conditions) if sub == "." else None
    if sub in exports:
        return _pick(exports[sub], conditions)
    for key, val in exports.items():                       # "./*": "./dist/*.js"
        if "*" in key:
            pre, _, post = key.partition("*")
            if sub.startswith(pre) and sub.endswith(post) and len(sub) >= len(pre) + len(post):
                star = sub[len(pre):len(sub) - len(post)]
                t = _pick(val, conditions)
                if t:
                    return t.replace("*", star)
    return None


def _file(path):
    for cand in (path, path + ".mjs", path + ".js", os.path.join(path, "index.mjs"), os.path.join(path, "index.js")):
        if os.path.isfile(cand):
            return cand
    return None


def resolve_module(module, from_dir, conditions):
    """The file a module specifier loads, as Node or a bundler would find it."""
    if module.startswith((".", "/")):
        f = _file(os.path.normpath(os.path.join(from_dir, module)))
        if not f:
            raise NpmError(f"cannot find the JavaScript file `{module}` (from {from_dir})")
        return f
    name = package_name(module)
    pdir = find_package(name, from_dir)
    if not pdir:
        raise NpmError(f"npm package `{name}` is not installed",
                       f"add it to klar.toml with `klar add --npm {name}` (or run `klar install` if it is listed)")
    with open(os.path.join(pdir, "package.json"), encoding="utf-8") as f:
        meta = json.load(f)
    sub = "." + module[len(name):]
    target = None
    if "exports" in meta:
        target = _exports_target(meta["exports"], sub, conditions)
        if not target:
            raise NpmError(f"npm package `{name}` does not export `{sub}` for {conditions[0]}")
    elif sub != ".":
        target = sub
    else:
        browser = meta.get("browser") if "browser" in conditions else None
        target = (browser if isinstance(browser, str) and browser.endswith(".mjs") else None) \
            or meta.get("module") or meta.get("main") or "index.js"
    f = _file(os.path.normpath(os.path.join(pdir, target)))
    if not f:
        raise NpmError(f"npm package `{name}` names `{target}`, which does not exist in {pdir}")
    return f


# ---------------------------------------------------------------------------------------------- node target

def node_specifier(module, src_dir, out_dir):
    """How the built .mjs should import `module`. A package that Node would find from the output folder keeps
    its bare name; otherwise (for example `klar dev`, which builds into a temporary folder) the absolute file."""
    if module.startswith("node:"):
        return module
    if not is_bare(module):
        if module.startswith((".", "/")):
            path = _file(os.path.normpath(os.path.join(src_dir, module))) or os.path.join(src_dir, module)
            rel = os.path.relpath(path, out_dir).replace(os.sep, "/")
            return rel if rel.startswith(".") else "./" + rel
        return module
    name = package_name(module)
    pdir = find_package(name, src_dir)
    if not pdir:
        raise NpmError(f"npm package `{name}` is not installed",
                       f"add it to klar.toml with `klar add --npm {name}` (or run `klar install` if it is listed)")
    if find_package(name, out_dir) == pdir:
        return module
    return "file://" + urllib.parse.quote(resolve_module(module, src_dir, NODE_CONDITIONS))


# ---------------------------------------------------------------------------------------------- web target

IMPORT_RE = re.compile(
    r"""(?P<head>(?<![\w$.])import\s*(?:[\w$*{}\s,]+?\s*from\s*)?|(?<![\w$.])export\s*(?:\*(?:\s*as\s+[\w$]+)?|\{[^}]*\})\s*from\s*|(?<![\w$.])import\s*\(\s*)(?P<q>["'])(?P<spec>[^"'\n]+)(?P=q)""")
CJS_RE = re.compile(r"\bmodule\.exports\b|\bexports\.[\w$]+\s*=|\brequire\s*\(")
ESM_RE = re.compile(r"(?:^|[;\n}])\s*(?:import|export)\b")


def _skipped_spans(src):
    """Start/end offsets of comments, strings, template literals and regex literals in JavaScript source, so that
    import-looking text inside them is left alone. A light scanner, not a parser: enough for published code."""
    spans, i, n = [], 0, len(src)
    prev = ""                                   # last significant character, to tell a regex from a division
    while i < n:
        ch = src[i]
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            j = n if j < 0 else j
            spans.append((i, j)); i = j; continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            spans.append((i, j)); i = j; continue
        if ch in "'\"`":
            j = i + 1
            while j < n and src[j] != ch:
                if src[j] == "\\":
                    j += 1
                elif ch != "`" and src[j] == "\n":
                    break
                j += 1
            spans.append((i, j + 1)); i = j + 1; prev = ch; continue
        if ch == "/" and (prev == "" or prev in "(,=:[!&|?{};+-*%<>~^"):
            j, in_class = i + 1, False
            while j < n and src[j] != "\n":
                c = src[j]
                if c == "\\":
                    j += 2; continue
                if c == "[":
                    in_class = True
                elif c == "]":
                    in_class = False
                elif c == "/" and not in_class:
                    break
                j += 1
            spans.append((i, j + 1)); i = j + 1; prev = "/"; continue
        if not ch.isspace():
            prev = ch if not (ch.isalnum() or ch in "_$") else "a"
        i += 1
    return spans


def _inside(pos, spans):
    import bisect
    k = bisect.bisect_right(spans, (pos, float("inf"))) - 1
    return k >= 0 and spans[k][0] <= pos < spans[k][1]


class WebBundler:
    """Embeds an ES module graph in the page. Every module file becomes one `data:` URL under a short bare name
    ("@klar-npm/3") in an import map, and every import inside it is rewritten to such a name. Modules are thus
    embedded once each (no nesting), evaluated once each, and the page stays a single file that works offline."""

    def __init__(self):
        self.names = {}         # real path -> bare name
        self.urls = {}          # bare name -> data URL

    def name(self, path):
        path = os.path.realpath(path)
        if path in self.names:
            return self.names[path]
        key = f"@klar-npm/{len(self.names)}"
        self.names[path] = key           # set before recursing: import cycles are fine with an import map
        with open(path, encoding="utf-8") as f:
            src = f.read()
        if not ESM_RE.search(src) and CJS_RE.search(src):
            raise NpmError(f"{path} is a CommonJS module (require/module.exports); the browser loads only "
                           "ES modules", "pick the package's ESM build with a subpath (for example "
                           "\"js:pkg/dist/pkg.mjs.fn\"), or use it from a `--target node` backend")
        d = os.path.dirname(path)
        spans = _skipped_spans(src)

        def repl(m):
            if _inside(m.start(), spans):
                return m.group(0)
            spec = m.group("spec")
            if spec.startswith("node:"):
                raise NpmError(f"{path} imports `{spec}`, a Node built-in that the browser does not have",
                               "use this package from a `--target node` backend instead")
            if spec.startswith(("http:", "https:", "data:")):
                return m.group(0)
            return m.group("head") + json.dumps(self.name(resolve_module(spec, d, WEB_CONDITIONS)))

        out = IMPORT_RE.sub(repl, src)
        out = re.sub(r"^//# sourceMappingURL=.*$", "", out, flags=re.M)
        self.urls[key] = "data:text/javascript;charset=utf-8," + urllib.parse.quote(out, safe=SAFE)
        return key

    def head(self):
        """The <script> tags that must come before the program's module script."""
        if not self.urls:
            return ""
        imap = json.dumps({"imports": self.urls}, separators=(",", ":")).replace("</", "<\\/")
        return PROCESS_SHIM + f'<script type="importmap">{imap}</script>\n'


# Characters left as is in data: URLs (everything printable except %, # and quotes, which JSON would escape).
SAFE = " !$&'()*+,-./:;=?@[]^_`{|}~" + "".join(chr(c) for c in range(0x30, 0x3a)) + \
       "".join(chr(c) for c in range(0x41, 0x5b)) + "".join(chr(c) for c in range(0x61, 0x7b)) + "<>\\"

PROCESS_SHIM = ('<script>globalThis.process ??= { env: { NODE_ENV: "production" } };</script>\n')


# ---------------------------------------------------------------------------------------------- klar bind

def _typescript(root):
    import shutil
    import subprocess
    local = os.path.join(root, "node_modules", "typescript")
    if os.path.isfile(os.path.join(local, "package.json")):
        return local
    npm = shutil.which("npm")
    if npm:
        r = subprocess.run([npm, "root", "-g"], capture_output=True, text=True)
        g = os.path.join(r.stdout.strip(), "typescript")
        if r.returncode == 0 and os.path.isfile(os.path.join(g, "package.json")):
            return g
    return None


def module_file_name(module):
    return re.sub(r"[^a-z0-9]+", "_", module.lower().lstrip("@")).strip("_") or "pkg"


def cmd_bind(module, out=None, prefix=None):
    """`klar bind MODULE`: write npm/MODULE.klar with extern declarations generated from the package's .d.ts."""
    import shutil
    import subprocess
    import sys
    from . import pkg
    root = pkg.find_root(os.getcwd()) or os.getcwd()
    if not find_package(package_name(module), root):
        print(f"klar bind: npm package `{package_name(module)}` is not installed here", file=sys.stderr)
        print(f"  hint: klar add --npm {package_name(module)}", file=sys.stderr)
        return 1
    node = shutil.which("node")
    tsdir = _typescript(root)
    if not node or not tsdir:
        print("klar bind: needs Node.js and the `typescript` package to read .d.ts files", file=sys.stderr)
        print("  hint: klar add --npm typescript", file=sys.stderr)
        return 1
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "dts2klar.mjs")
    r = subprocess.run([node, script, tsdir, root, module, prefix or ""], capture_output=True, text=True)
    if r.returncode != 0:
        print("klar bind: " + (r.stderr.strip() or "the generator failed"), file=sys.stderr)
        return 1
    out = out or os.path.join(root, "npm", module_file_name(module) + ".klar")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(r.stdout)
    from .cli import check_source
    _, errors = check_source(r.stdout, out)
    rel = os.path.relpath(out, root)
    use = ".".join(os.path.splitext(rel)[0].split(os.sep))
    n = r.stdout.count("\nextern \"")
    if errors:
        for e in errors:
            print(e.render(r.stdout), file=sys.stderr)
        print(f"klar bind: wrote {rel}, but it does not check; please report this with the package name", file=sys.stderr)
        return 1
    print(f"wrote {rel} ({n} declarations). Use it with: use {use} {{ ... }}", file=sys.stderr)
    return 0
