"""`klar doc`: the standard library as signatures, for people and agents who should not read 120 KB of sources.

Each module becomes its first comment line, then one line per type and function: the signature without the
body, followed by the first sentence of the comment above it. Functions that differ only in name (the HTML
elements, layout helpers) share one line. `klar doc` prints every module (this is `klar/STD.md`);
`klar doc std.ui` prints one; `klar doc NAME` prints the declarations whose name contains NAME, with their
full comments.
"""

import os
import re

STD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "std")
DECL = re.compile(r"^(fn|extern|struct|enum|trait|impl)\b")
EXTERN = re.compile(r'^extern\s+"[^"]*"\s+')


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def modules():
    return sorted(f[:-5] for f in os.listdir(STD) if f.endswith(".klar"))


def _first_sentence(lines):
    text = " ".join(l.strip() for l in lines if l.strip())
    m = re.search(r"(?<!e\.g\.)(?<!i\.e\.)(?<=[.!?])\s+(?=[A-Z`(])", text)
    s = text[:m.start()] if m else text
    return s if len(s) <= 200 else s[:197].rsplit(" ", 1)[0] + " ..."


def _decls(name):
    """Yield (kind, name, signature, comment_lines) for each top-level declaration of a std module."""
    src = _read(os.path.join(STD, name + ".klar")).split("\n")
    comment, i = [], 0
    while i < len(src):
        line = src[i]
        if line.startswith("//"):
            comment.append(line[2:].strip())
            i += 1
            continue
        if not DECL.match(line):
            comment = []          # a comment documents only the declaration right below it
            i += 1
            continue
        kind = DECL.match(line).group(1)
        if kind == "impl":
            comment, i = [], i + 1
            continue
        text = line
        if kind == "extern":
            text = EXTERN.sub("", text)
            kind = "type" if text.startswith("type ") else "fn"
        elif kind == "fn":
            while "{" not in text and i + 1 < len(src):
                i += 1
                text += " " + src[i].strip()
            text = text[:text.index("{")].rstrip()
            text = re.sub(r"\(\s+", "(", re.sub(r",\s*\)", ")", text))
        elif kind in ("struct", "enum", "trait"):
            if "}" not in line:
                body = []
                while i + 1 < len(src):
                    i += 1
                    s = src[i].split("//")[0].strip()
                    if s == "}":
                        break
                    if s:
                        body.append(s.rstrip(","))
                text = line.rstrip(" {") + " { " + ", ".join(body) + " }"
        text = re.sub(r"\s+", " ", text).strip()
        m = re.match(r"(?:fn|struct|enum|trait|type)\s+([A-Za-z_][A-Za-z0-9_]*)", text)
        yield kind, m.group(1) if m else "", text, comment
        comment = []
        i += 1


def _spec():
    return _read(os.path.join(os.path.dirname(STD), "SPEC.md"))


def _helper(name, decl_name, code, spec):
    """A function the module itself calls and the reference never names is an internal helper (the same rule
    as the website's library pages)."""
    word = r"(?<![A-Za-z0-9_])" + re.escape(decl_name) + r"(?![A-Za-z0-9_])"
    return len(re.findall(word + r"\s*\(", code)) > 1 and not re.search(word, spec)


def module_doc(name, spec=None, intro=False):
    spec = _spec() if spec is None else spec
    src = _read(os.path.join(STD, name + ".klar")).split("\n")
    code = "\n".join(l.split("//")[0] for l in src)
    title = src[0][2:].strip() if src and src[0].startswith("//") else ""
    title = re.sub(r"^std\.\w+:\s*", "", title)
    out = ["## std.{}{}".format(name, ": " + title if title else "")]
    if intro:            # the module's opening comment: how the pieces fit, with examples
        body = []
        for line in src[1:]:
            if not line.startswith("//"):
                break
            body.append(line[3:] if line.startswith("// ") else line[2:])
        if "".join(body).strip():
            out += ["", "\n".join(body).strip("\n"), ""]
    groups = {}          # signature shape -> index in out
    for kind, decl_name, sig, comment in _decls(name):
        note = _first_sentence(comment)
        if kind == "fn" and _helper(name, decl_name, code, spec):
            continue
        if kind == "fn" and decl_name:
            shape = sig.replace("fn " + decl_name, "fn _", 1)
            key = (shape, note if not note.lower().startswith(decl_name) else "")
            if key in groups:
                idx, names = groups[key]
                names.append(decl_name)
                out[idx] = (", ".join(names), shape, note)
                continue
            groups[key] = (len(out), [decl_name])
            out.append((decl_name, shape, note))
            continue
        out.append(sig + ("   // " + note if note else ""))
    lines = []
    for item in out:
        if isinstance(item, tuple):
            names, shape, note = item
            sig = shape.replace("fn _", "fn " + names, 1)
            item = sig + ("   // " + note if note else "")
        lines.append(item)
    return "\n".join(lines)


HEADER = """# Klar standard library: every signature

Generated by `klar doc` from `klar/std/*.klar`; do not edit. Import with `use std.NAME { ... }`. A line lists
several names when they share one signature. `klar doc NAME` shows the full comment for any name; the sources
have the details and examples.
"""


def all_docs():
    spec = _spec()
    return HEADER + "\n" + "\n\n".join(module_doc(m, spec) for m in modules()) + "\n"


def lookup(term):
    """Declarations whose name contains term, with their full comment, across every std module."""
    out = []
    for m in modules():
        for kind, decl_name, sig, comment in _decls(m):
            if term in decl_name:
                block = ["std.{}: {}".format(m, sig)] + ["  " + c for c in comment if c]
                out.append("\n".join(block))
    return "\n\n".join(out)


def doc(target=None):
    if not target:
        return all_docs()
    name = target[4:] if target.startswith("std.") else target
    if name in modules():
        return module_doc(name, intro=True) + "\n"
    found = lookup(name)
    return found + "\n" if found else None
