"""Machine-applicable fixes for Klar's diagnostics.

The checker's hints say how to fix an error in words ("did you mean `area`?", "add `use std.json { json_parse }`
at the top of this file"). This module turns the mechanical ones into exact text edits, so the editor's quick
fixes, `klar check --json` and `klar check --fix` all offer the same repairs:

    {"title": "Change to `area`", "preferred": true,
     "edits": [{"line": 4, "col": 11, "end_line": 4, "end_col": 15, "text": "area"}]}

Positions are 1-based lines and columns (code points), like every Klar diagnostic; `end_col` is exclusive.
A fix only edits the file its error is in.
"""

import os
import re

from . import loader
from .errors import KlarError
from .syntax import lex, parse

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
TODO_ARM = 'panic("not handled yet")'


class Src:
    def __init__(self, text):
        self.text = text
        self.lines = text.split("\n")
        self.starts = [0]
        for ln in self.lines[:-1]:
            self.starts.append(self.starts[-1] + len(ln) + 1)

    def offset(self, line, col):
        line = min(max(line, 1), len(self.lines))
        return min(self.starts[line - 1] + max(col - 1, 0), len(self.text))

    def pos(self, off):
        lo, hi = 0, len(self.starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.starts[mid] <= off:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1, off - self.starts[lo] + 1

    def edit(self, a, b, text):
        (l1, c1), (l2, c2) = self.pos(a), self.pos(b)
        return {"line": l1, "col": c1, "end_line": l2, "end_col": c2, "text": text}


def apply_edits(text, edits):
    """Apply non-overlapping edits (Klar positions) to text."""
    s = Src(text)
    spans = sorted(((s.offset(e["line"], e["col"]), s.offset(e["end_line"], e["end_col"]), e["text"]) for e in edits),
                   reverse=True)
    for a, b, t in spans:
        text = text[:a] + t + text[b:]
    return text


def _name_after(s, name, off, limit=400):
    m = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])").search(s.text, off, off + limit)
    return m.start() if m else None


def wrong_name(message):
    """The misspelled or missing name an error message is about."""
    for pat in (r"no field `(\w+)`", r"has no `(\w+)`", r"unknown (?:name|function|type|variant) `(\w+)`",
                r"`(\w+)` is defined in module", r"`(\w+)` is a method of trait"):
        m = re.search(pat, message)
        if m:
            return m.group(1)
    return None


# ------------------------------------------------------------------ edits shared with the language server
def import_edit(s, use):
    """Edit that adds `use mod { name }`: merged into an existing `use` of the same module, or a new line after
    the last `use` (or at the top)."""
    m = re.match(r"use ([\w.]+) \{ (\w+) \}", use)
    mod, name = (m.group(1), m.group(2)) if m else (None, None)
    last = -1
    for i, line in enumerate(s.lines):
        lm = re.match(r"(\s*use\s+([\w.]+)\s*\{)([^}]*)\}", line)
        if lm and mod and lm.group(2) == mod:
            names = [n.strip() for n in lm.group(3).split(",") if n.strip()]
            if name in names:
                return None
            start = s.starts[i]
            return s.edit(start, start + len(line), f"use {mod} {{ {', '.join(names + [name])} }}")
        if re.match(r"use\s", line):
            last = i
    if last >= 0:
        pos = s.starts[last + 1] if last + 1 < len(s.starts) else len(s.text)
        return s.edit(pos, pos, use + "\n")
    # after a leading comment block, if any
    i = 0
    while i < len(s.lines) and s.lines[i].startswith("//"):
        i += 1
    if i > 0:
        if i < len(s.lines) and not s.lines[i].strip() and i + 1 < len(s.starts):
            return s.edit(s.starts[i + 1], s.starts[i + 1], use + "\n\n")
        pos = s.starts[i] if i < len(s.starts) else len(s.text)
        return s.edit(pos, pos, "\n" + use + "\n")
    return s.edit(0, 0, use + "\n\n")


def uses_io_edit(s, off):
    """Insert ` uses io` before the `{` of the named function enclosing `off`."""
    src = s.text
    starts = [m.start() for m in re.finditer(r"(?m)^(?:\s*)fn\s+\w+", src) if m.start() <= off]
    if not starts:
        return None
    start = starts[-1]
    depth = 0
    for i in range(start, len(src)):
        if src[i] in "([":
            depth += 1
        elif src[i] in ")]":
            depth -= 1
        elif src[i] == "{" and depth == 0:
            head = src[start:i]
            if re.search(r"\buses\s+io\b", head):
                return None
            j = i
            while j > start and src[j - 1] in " \t":
                j -= 1
            return s.edit(j, j, " uses io")
    return None


def _tokens(text):
    try:
        return lex(text)
    except KlarError:
        return None


def _match_braces(s, line, col):
    """Offsets of the `{` and `}` of the match expression whose `match` keyword is at (line, col)."""
    toks = _tokens(s.text)
    if toks is None:
        return None
    k = next((i for i, t in enumerate(toks) if t.line == line and t.col == col and t.value == "match"), None)
    if k is None:
        k = next((i for i, t in enumerate(toks) if t.value == "match" and (t.line, t.col) >= (line, col)), None)
    if k is None:
        return None
    depth, opened = 0, None
    for i in range(k + 1, len(toks)):
        t = toks[i]
        if t.kind != "sym":
            continue
        if t.value in ("(", "["):
            depth += 1
        elif t.value in (")", "]"):
            depth -= 1
        elif t.value == "{":
            if opened is None and depth == 0:
                opened, depth = i, 0
                continue
            depth += 1
        elif t.value == "}":
            if opened is not None and depth == 0:
                return toks, opened, i
            depth -= 1
    return None


def add_arms_edits(s, line, col, arms):
    """Edits adding `arm => panic(...)` for each missing case to the match at (line, col)."""
    found = _match_braces(s, line, col)
    if found is None:
        return None
    toks, o, c = found
    close = toks[c]
    prev = toks[c - 1]
    close_off = s.offset(close.line, close.col)
    edits = []
    if close.line > prev.line:                          # arms on their own lines
        first = toks[o + 1] if o + 1 < c else None
        indent = re.match(r"\s*", s.lines[first.line - 1]).group(0) if first is not None and first.line > toks[o].line \
            else re.match(r"\s*", s.lines[close.line - 1]).group(0) + "  "
        if prev.value != "," and o + 1 < c:
            end = s.offset(prev.line, len(s.lines[prev.line - 1].rstrip()) + 1)
            if "//" not in s.lines[prev.line - 1]:
                edits.append(s.edit(end, end, ","))
        at = s.starts[close.line - 1]
        edits.append(s.edit(at, at, "".join(f"{indent}{a} => {TODO_ARM},\n" for a in arms)))
    else:                                               # a one-line match
        j = close_off
        while j > 0 and s.text[j - 1] == " ":
            j -= 1
        sep = "" if prev.value in (",", "{") else ","
        edits.append(s.edit(j, j, sep + "".join(f" {a} => {TODO_ARM}," for a in arms)[:-1]))
    return edits


# ------------------------------------------------------------------ module index (for missing imports)
_STD_CACHE = {}


def _exports_of(path, dotted):
    try:
        st = os.stat(path)
    except OSError:
        return {}
    key = (path, st.st_mtime_ns)
    if key in _STD_CACHE:
        return _STD_CACHE[key]
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            mod = parse(f.read(), path)
        for d in mod.decls:
            if d.kind in ("Fn", "Extern", "Struct", "Enum", "Trait", "ExternType"):
                out[d.name] = (dotted, d.kind, d)
            if d.kind == "Enum":
                for v in d.variants:
                    out[v.name] = (dotted, "Variant", d)
    except (KlarError, OSError, RecursionError):
        out = {}
    _STD_CACHE[key] = out
    return out


def module_files(importer):
    """(dotted module name, path) of every module `importer` could `use`: the standard library, then the
    .klar files beside it (and in folders below it, npm bindings included)."""
    out = []
    for root, dirs, files in os.walk(loader.STD_DIR):
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "_")))
        for f in sorted(files):
            if f.endswith(".klar"):
                rel = os.path.relpath(os.path.join(root, f), loader.STD_DIR)[:-5]
                out.append(("std." + rel.replace(os.sep, "."), os.path.join(root, f)))
    if importer:
        base = os.path.dirname(os.path.abspath(importer))
        for root, dirs, files in os.walk(base):
            depth = os.path.relpath(root, base).count(os.sep)
            dirs[:] = sorted(d for d in dirs if not d.startswith((".", "_")) and d not in ("node_modules", "std")
                             and depth < 2)
            for f in sorted(files):
                p = os.path.join(root, f)
                if f.endswith(".klar") and os.path.abspath(p) != os.path.abspath(importer):
                    rel = os.path.relpath(p, base)[:-5]
                    if all(IDENT.fullmatch(x) for x in rel.split(os.sep)):
                        out.append((rel.replace(os.sep, "."), p))
    return out


def exporters(name, importer):
    """Modules that declare `name`, as (dotted, kind, decl node)."""
    found = []
    for dotted, path in module_files(importer):
        e = _exports_of(path, dotted).get(name)
        if e is not None:
            found.append(e)
    return found


def all_exports(importer):
    """name -> (dotted, kind, decl node) for every importable name (first module wins)."""
    out = {}
    for dotted, path in module_files(importer):
        for n, e in _exports_of(path, dotted).items():
            out.setdefault(n, e)
    return out


# ------------------------------------------------------------------ fixes
def fixes(err, text, path=None, overlay=None):
    """Fixes for one KlarError in `text` (the source of the file it is in)."""
    s = Src(text)
    hint = err.hint or ""
    out = []
    if err.line <= 0:
        return out
    off = s.offset(err.line, err.col)
    name = wrong_name(err.message)

    m = re.search(r"did you mean `([^`]+)`\?", hint)
    if m and name and IDENT.fullmatch(m.group(1)):
        at = _name_after(s, name, off)
        if at is not None:
            out.append({"title": f"Change to `{m.group(1)}`", "preferred": True,
                        "edits": [s.edit(at, at + len(name), m.group(1))]})
    m = re.search(r"booleans are lowercase: (true|false)", hint)
    if m and name:
        at = _name_after(s, name, off)
        if at is not None:
            out.append({"title": f"Change to `{m.group(1)}`", "preferred": True,
                        "edits": [s.edit(at, at + len(name), m.group(1))]})

    m = re.search(r"add `(use [\w.]+ \{ \w+ \})` at the top of this file", hint)
    if m:
        e = import_edit(s, m.group(1))
        if e:
            out.append({"title": f"Add `{m.group(1)}`", "preferred": True, "edits": [e]})
    elif err.code == "E201" and name and re.search(r"unknown (name|function|type|variant)", err.message):
        cands = exporters(name, path)
        for dotted, kind, _ in cands[:3]:
            use = f"use {dotted} {{ {name} }}"
            e = import_edit(s, use)
            if e:
                out.append({"title": f"Add `{use}`", "preferred": len(cands) == 1 and not out, "edits": [e]})

    if "add `uses io` to the signature" in hint:
        e = uses_io_edit(s, off)
        if e:
            out.append({"title": "Add `uses io` to the function", "preferred": True, "edits": [e]})

    m = re.match(r"cannot assign to `(\w+)` because it was declared with `let`", err.message)
    if err.code == "E203" and m:
        decls = [x for x in re.finditer(r"\blet(\s+)" + re.escape(m.group(1)) + r"\b", text[:off])]
        if decls:
            d = decls[-1]
            out.append({"title": f"Declare `{m.group(1)}` with `var`", "preferred": True,
                        "edits": [s.edit(d.start(), d.start() + 3, "var")]})

    m = re.match(r"match is not exhaustive: missing case (.+)$", err.message)
    if err.code == "E306" and m:
        arms = missing_arms(err, text, path, overlay, m.group(1))
        edits = add_arms_edits(s, err.line, err.col, arms)
        if edits:
            what = "the missing arm" if len(arms) == 1 else f"{len(arms)} missing arms"
            out.append({"title": f"Add {what}", "preferred": True, "edits": edits})
    return out


def missing_arms(err, text, path, overlay, first):
    """Every case a match is missing: the checker names one at a time, so add it and check again."""
    arms = [first]
    if path is None:
        return arms
    from .checker import check_source
    s = Src(text)
    ov = dict(overlay or {})
    for _ in range(40):
        edits = add_arms_edits(s, err.line, err.col, arms)
        if not edits:
            break
        new = apply_edits(text, edits)
        ov[os.path.abspath(path)] = new
        try:
            _, errors = check_source(new, path, ov)
        except Exception:
            break
        nxt = [e for e in errors if e.code == "E306" and e.line == err.line and e.col == err.col
               and (e.file is None or os.path.abspath(e.file) == os.path.abspath(path))]
        m = re.match(r"match is not exhaustive: missing case (.+)$", nxt[0].message) if nxt else None
        if m is None or m.group(1) in arms:
            break
        arms.append(m.group(1))
    return arms


def fix_all(path, text, errors, overlay=None):
    """The source with every preferred fix applied (one per error, no overlapping edits). Returns
    (new text, titles of the fixes applied)."""
    edits, titles, taken = [], [], []
    s = Src(text)
    for e in errors:
        if e.file and os.path.abspath(e.file) != os.path.abspath(path):
            continue
        pref = [f for f in fixes(e, text, path, overlay) if f.get("preferred")]
        if not pref:
            continue
        f = pref[0]
        spans = [(s.offset(x["line"], x["col"]), s.offset(x["end_line"], x["end_col"])) for x in f["edits"]]
        if any(a < tb and ta < b or (a == b == ta == tb) for a, b in spans for ta, tb in taken):
            continue
        taken += spans
        edits += f["edits"]
        titles.append(f"{e.line}:{e.col} {f['title']}")
    return apply_edits(text, edits), titles
