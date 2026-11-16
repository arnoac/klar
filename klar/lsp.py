"""`klar lsp`: a Language Server Protocol server over stdin/stdout.

It reuses the checker, so an editor shows exactly the diagnostics `klar check` prints (with their codes and hints),
plus hover types, go-to-definition, references, rename, completion, signature help, document symbols, formatting
and quick fixes taken from the hints. Every open buffer is analysed as the entry file, with the other open buffers
standing in for their files on disk.
"""

import json
import os
import queue
import re
import sys
import threading
import urllib.parse

from . import fixes, loader
from .checker import check_source
from .errors import CATALOG, KlarError
from .prelude import PRELUDE
from .syntax import Node, parse, is_tuple_name
from .types import TCon, TFn, prune, show

KEYWORDS = ["fn", "let", "var", "if", "else", "then", "while", "for", "in", "return", "break", "continue", "match",
            "struct", "enum", "use", "test", "assert", "extern", "type", "trait", "impl", "uses", "io", "and", "or",
            "not", "true", "false", "with"]
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# LSP enums
SEV_ERROR = 1
K_FUNCTION, K_FIELD, K_VARIABLE, K_STRUCT, K_ENUM_MEMBER, K_KEYWORD, K_MODULE, K_ENUM, K_INTERFACE = 3, 5, 6, 22, 20, 14, 9, 13, 8
S_FUNCTION, S_STRUCT, S_ENUM, S_FIELD, S_ENUM_MEMBER, S_INTERFACE, S_METHOD = 12, 23, 10, 8, 22, 11, 6


def uri_to_path(uri):
    p = urllib.parse.urlparse(uri)
    return os.path.abspath(urllib.parse.unquote(p.path))


def path_to_uri(path):
    return "file://" + urllib.parse.quote(os.path.abspath(path))


class Text:
    """Source text with conversions between Klar positions (1-based line, 1-based code point column), offsets,
    and LSP positions (0-based line, UTF-16 column)."""

    def __init__(self, src):
        self.src = src
        self.lines = src.split("\n")
        self.starts = [0]
        for ln in self.lines[:-1]:
            self.starts.append(self.starts[-1] + len(ln) + 1)

    def offset(self, line, col):
        """Offset of a Klar (line, col)."""
        line = min(max(line, 1), len(self.lines))
        return self.starts[line - 1] + max(col - 1, 0)

    def lsp(self, off):
        """LSP position of an offset."""
        off = min(max(off, 0), len(self.src))
        i = max(0, _bisect(self.starts, off))
        prefix = self.src[self.starts[i]:off]
        return {"line": i, "character": len(prefix.encode("utf-16-le")) // 2}

    def from_lsp(self, pos):
        line = min(max(pos["line"], 0), len(self.lines) - 1)
        text = self.lines[line]
        units, col = 0, 0
        while col < len(text) and units < pos["character"]:
            units += 2 if ord(text[col]) > 0xFFFF else 1
            col += 1
        return self.starts[line] + col

    def range(self, a, b):
        return {"start": self.lsp(a), "end": self.lsp(b)}

    def word_at(self, off):
        """(start, end) of the identifier touching offset, or None."""
        s = e = off
        while s > 0 and (self.src[s - 1].isalnum() or self.src[s - 1] == "_"):
            s -= 1
        while e < len(self.src) and (self.src[e].isalnum() or self.src[e] == "_"):
            e += 1
        return (s, e) if s < e else None

    def find_name(self, name, line, col, limit=4000):
        """Offset of identifier `name` at or after (line, col)."""
        start = self.offset(line, col)
        m = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])").search(self.src, start, start + limit)
        return m.start() if m else None


def _bisect(starts, off):
    lo, hi = 0, len(starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if starts[mid] <= off:
            lo = mid
        else:
            hi = mid - 1
    return lo


class Span:
    __slots__ = ("start", "end", "name", "kind", "info", "target")

    def __init__(self, start, end, name, kind, info, target):
        self.start, self.end, self.name, self.kind, self.info, self.target = start, end, name, kind, info, target


def doc_comment(text, line):
    """The `//` comment lines directly above a declaration on `line` (1-based)."""
    out = []
    i = line - 2
    while i >= 0 and text.lines[i].strip().startswith("//"):
        out.append(text.lines[i].strip()[2:].strip())
        i -= 1
    return "\n".join(reversed(out))


def short_path(module):
    if not module:
        return ""
    if module.startswith(loader.STD_DIR + os.sep):
        return "std." + os.path.splitext(os.path.relpath(module, loader.STD_DIR))[0].replace(os.sep, ".")
    return os.path.basename(module)


class Analysis:
    """One check of a buffer: diagnostics plus an index of every name the checker resolved."""

    def __init__(self, server, path, src):
        self.server = server
        self.path = path
        self.texts = {}
        self.spans = {}           # module path -> list of Span (sorted)
        self.prog, self.errors = check_source(src, path, server.overlay())
        self.texts[path] = Text(src)
        self.checker = getattr(self.prog, "checker", None) if self.prog is not None else None
        if self.checker is not None:
            self._index()

    def text(self, module):
        if module not in self.texts:
            src = self.server.overlay().get(module)
            if src is None:
                try:
                    with open(module, encoding="utf-8") as f:
                        src = f.read()
                except OSError:
                    src = ""
            self.texts[module] = Text(src)
        return self.texts[module]

    def _decl_target(self, node, name, module):
        """(module, start, end) where `name` is declared by `node`."""
        if node is None or module is None:
            return None
        t = self.text(module)
        off = t.find_name(name, node.line, node.col)
        return (module, off, off + len(name)) if off is not None else None

    def target_of(self, name, kind, info, module):
        c = self.checker
        if kind == "decl":
            return self._decl_target(info[1], name, module)
        if kind == "local":
            return self._decl_target(info[2] if len(info) > 2 else None, name, module)
        if kind == "fn":
            sig = c.fns.get(name)
            if sig is None or sig.node is None:
                return None
            return self._decl_target(sig.node, name, getattr(sig.node, "module", None))
        if kind in ("type", "variant"):
            d = c.decl_nodes.get(name)
            return self._decl_target(d, name, getattr(d, "module", None)) if d is not None else None
        if kind == "field":
            d = c.decl_nodes.get(info[0])
            return self._decl_target(d, name, getattr(d, "module", None)) if d is not None else None
        return None

    def _index(self):
        seen = set()
        for module, node, name, kind, info in self.checker.refs:
            if module is None or not hasattr(node, "line"):
                continue
            t = self.text(module)
            off = t.find_name(name, node.line, node.col)
            if off is None:
                continue
            key = (module, off, kind == "decl")
            if key in seen:
                continue
            seen.add(key)
            target = self.target_of(name, kind, info, module)
            self.spans.setdefault(module, []).append(Span(off, off + len(name), name, kind, info, target))
        # declarations of top-level names point at themselves
        for d in self.prog.decls:
            module = getattr(d, "module", None)
            if module is None:
                continue
            names = []
            if d.kind in ("Fn", "Extern", "Struct", "Enum", "Trait", "ExternType"):
                names.append((d, d.name, {"Fn": "fn", "Extern": "fn", "Trait": "trait"}.get(d.kind, "type")))
            if d.kind == "Enum":
                names += [(v, v.name, "variant") for v in d.variants]
            if d.kind == "Struct":
                t = self.text(module)
                for fname, ft in d.fields:
                    off = t.find_name(fname, d.line, d.col + len("struct "))
                    if off is not None:
                        self.spans.setdefault(module, []).append(
                            Span(off, off + len(fname), fname, "field", (d.name, None), (module, off, off + len(fname))))
            if d.kind == "Trait":
                names += [(m, m.name, "fn") for m in d.methods]
            if d.kind == "Use":
                c = self.checker
                t = self.text(module)
                target = getattr(d, "target_module", None)
                off = t.find_name(d.path[0], d.line, d.col + 3)
                if off is not None and target:
                    end = off + len(".".join(d.path))
                    self.spans.setdefault(module, []).append(
                        Span(off, end, ".".join(d.path), "module", target, (os.path.abspath(target), 0, 0)))
                for n, _ in d.names:
                    kind = "fn" if n in c.fns else "variant" if n in c.variants else \
                        "trait" if n in c.traits else "type"
                    off = t.find_name(n, d.line, d.col + 4)
                    tgt = self.target_of(n, kind if kind != "trait" else "type", None, module)
                    if off is not None and (module, off, False) not in seen:
                        seen.add((module, off, False))
                        self.spans.setdefault(module, []).append(Span(off, off + len(n), n, kind, None, tgt))
            for node, name, kind in names:
                tgt = self._decl_target(node, name, module)
                if tgt and (module, tgt[1], False) not in seen:
                    seen.add((module, tgt[1], False))
                    self.spans.setdefault(module, []).append(Span(tgt[1], tgt[2], name, kind, None, tgt))
        for lst in self.spans.values():
            lst.sort(key=lambda s: (s.start, s.end))

    def span_at(self, module, off):
        for s in self.spans.get(module, ()):
            if s.start <= off <= s.end:
                return s
        return None

    # ----------------------------------------------------------------- hover text
    def fn_signature(self, name):
        sig = self.checker.fns.get(name)
        if sig is None:
            return None
        tps = ""
        if sig.tparams and not sig.trait:
            parts = [tp + (": " + " + ".join(sig.bounds[tp]) if sig.bounds.get(tp) else "") for tp in sig.tparams]
            tps = "[" + ", ".join(parts) + "]"
        params = ", ".join(f"{n}: {show(t)}" for n, t in sig.params)
        ret = "" if show(sig.ret) == "unit" else f" -> {show(sig.ret)}"
        io = " uses io" if "io" in sig.effects else ""
        return f"fn {name}{tps}({params}){ret}{io}"

    def hover_parts(self, span):
        """(code, where, doc) describing a name: a declaration in Klar syntax, where it comes from, its docs."""
        c = self.checker
        name, kind, info = span.name, span.kind, span.info
        doc = ""
        where = ""
        if kind in ("decl", "local"):
            ty = info[0]
            node = info[2] if kind == "local" and len(info) > 2 else (info[1] if kind == "decl" else None)
            mutable = getattr(node, "mutable", False) if node is not None and node.kind == "Let" else (
                kind == "local" and info[1])
            code = f"{'var' if mutable else 'let'} {name}: {show(ty)}"
        elif kind in ("fn", "trait"):
            if kind == "trait":
                d = c.decl_nodes.get(name)
                code = f"trait {name}"
                if d is not None:
                    code += " {\n" + "".join(f"  {self.fn_signature(m.name)}\n" for m in d.methods if self.fn_signature(m.name)) + "}"
            else:
                code = self.fn_signature(name) or name
                sig = c.fns.get(name)
                if sig is not None and sig.trait:
                    where = f"method of trait {sig.trait}"
                if sig is not None and sig.builtin:
                    where = "built-in"
                node = getattr(sig, "node", None)
                if node is not None and node.kind == "Extern" and \
                        not (getattr(node, "module", None) or "").startswith(loader.STD_DIR + os.sep):
                    tgt = getattr(node, "target", "") or ""
                    code = f'extern "{tgt}" {code}'
                    where = ("JavaScript function" + (" (npm package)" if not tgt.startswith(("js:globalThis", "js:node:"))
                                                      else "") if tgt.startswith("js:") else
                             "WebAssembly function" if tgt.startswith("wasm:") else
                             "built into Klar" if tgt.startswith("klar:") else "Python function")
        elif kind == "type":
            if name in c.structs:
                tps = c.tparams_of.get(name)
                code = f"struct {name}{'[' + ', '.join(tps) + ']' if tps else ''} {{\n" + \
                       "".join(f"  {f}: {show(t)},\n" for f, t in c.structs[name]) + "}"
            elif name in c.enums:
                tps = c.tparams_of.get(name)
                lines = []
                for v in c.enums[name]:
                    vi = c.variants[v]
                    lines.append(f"  {v}" + (f"({', '.join(f'{f}: {show(t)}' for f, t in vi.fields)})" if vi.fields else "") + ",\n")
                code = f"enum {name}{'[' + ', '.join(tps) + ']' if tps else ''} {{\n" + "".join(lines) + "}"
            else:
                code = f"extern type {name}"
        elif kind == "variant":
            vi = c.variants.get(name)
            if vi is None:
                return None
            code = name + (f"({', '.join(f'{f}: {show(t)}' for f, t in vi.fields)})" if vi.fields else "")
            where = f"variant of enum {vi.enum}"
        elif kind == "module":
            return None
        elif kind == "field":
            struct = info[0]
            ft = dict(c.structs.get(struct, [])).get(name) if info[1] is None else info[1]
            code = f"{name}: {show(ft) if ft is not None else '?'}"
            where = f"element {name} of a tuple" if is_tuple_name(struct) else f"field of struct {struct}"
        else:
            return None
        tgt = span.target
        if tgt is not None and kind not in ("decl", "local"):
            tline = self.text(tgt[0]).lsp(tgt[1])["line"] + 1
            doc = doc_comment(self.text(tgt[0]), tline)
            mod = short_path(tgt[0])
            if mod and tgt[0] != self.path:
                where = (where + ", " if where else "") + f"from {mod}"
        elif kind == "fn" and c.fns.get(name) is not None and c.fns[name].builtin:
            doc = PRELUDE_DOCS.get(name, "")
        return code, where, doc

    def hover(self, span):
        if span.kind == "module":
            text = self.text(os.path.abspath(span.info))
            first = []
            for line in text.lines:
                if not line.startswith("//"):
                    break
                first.append(line[2:].strip())
            out = f"```klar\nuse {span.name}\n```\n*{short_path(os.path.abspath(span.info))}*"
            return out + ("\n\n" + "\n".join(first) if first else "")
        parts = self.hover_parts(span)
        if parts is None:
            return None
        code, where, doc = parts
        out = f"```klar\n{code}\n```"
        if where:
            out += f"\n*{where}*"
        if doc:
            out += "\n\n" + doc
        return out

    def type_before(self, off):
        """Type of the expression that ends right before `off` (for `x.` completion): the name there, or None."""
        t = self.texts[self.path]
        i = off
        while i > 0 and t.src[i - 1] in " \t":
            i -= 1
        s = self.span_at(self.path, i)
        if s is None or s.end != i:
            return None
        if s.kind in ("decl", "local"):
            return prune(s.info[0])
        if s.kind == "field":
            ft = s.info[1] if s.info[1] is not None else dict(self.checker.structs.get(s.info[0], [])).get(s.name)
            return prune(ft) if ft is not None else None
        return None


def _prelude_docs():
    docs, comment = {}, []
    for line in PRELUDE.splitlines():
        st = line.strip()
        if st.startswith("// ----"):
            comment = [st[7:].strip()]
        m = re.match(r"fn (\w+)", st)
        if m:
            docs[m.group(1)] = comment[0] if comment else ""
    return docs


PRELUDE_DOCS = _prelude_docs()


class Server:
    def __init__(self, inp, out):
        self.inp, self.out = inp, out
        self.docs = {}            # path -> source text
        self.versions = {}
        self.analyses = {}        # path -> latest Analysis
        self.good = {}            # path -> latest Analysis that has an index (code that parsed)
        self.dirty = set()
        self.shutdown = False
        self.out_lock = threading.Lock()

    def overlay(self):
        return dict(self.docs)

    # ----------------------------------------------------------------- transport
    def send(self, msg):
        body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        with self.out_lock:
            self.out.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
            self.out.flush()

    def read_message(self):
        headers = {}
        while True:
            line = self.inp.readline()
            if not line:
                return None
            line = line.decode("ascii", "replace").strip()
            if not line:
                break
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
        n = int(headers.get("content-length", 0))
        return json.loads(self.inp.read(n).decode("utf-8")) if n else {}

    def serve(self):
        q = queue.Queue()

        def reader():
            while True:
                try:
                    m = self.read_message()
                except (ValueError, OSError):
                    m = None
                q.put(m)
                if m is None:
                    return
        threading.Thread(target=reader, daemon=True).start()
        while True:
            try:
                msg = q.get(timeout=0.15 if self.dirty else None)
            except queue.Empty:
                self.flush()
                continue
            if msg is None:
                return 0 if self.shutdown else 1
            if self.handle(msg) == "exit":
                return 0 if self.shutdown else 1

    def handle(self, msg):
        method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method == "exit":
            return "exit"
        if method is None:
            return None         # a response to something we sent
        try:
            result = self.dispatch(method, params)
        except KlarError as e:
            result = None
            if mid is not None:
                self.send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": e.message}})
                return None
        except Exception as e:   # never take the editor's session down
            if mid is not None:
                self.send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"{type(e).__name__}: {e}"}})
            return None
        if mid is not None:
            self.send({"jsonrpc": "2.0", "id": mid, "result": result})
        return None

    # ----------------------------------------------------------------- analysis
    def analyse(self, path):
        a = Analysis(self, path, self.docs[path])
        self.analyses[path] = a
        if a.checker is not None:
            self.good[path] = a
        self.dirty.discard(path)
        return a

    def current(self, path):
        """Latest analysis with an index, refreshing a changed buffer first."""
        if path in self.dirty or path not in self.analyses:
            self.analyse(path)
            self.publish(path)
        return self.good.get(path)

    def flush(self):
        for path in list(self.dirty):
            if path in self.docs:
                self.analyse(path)
                self.publish(path)
        self.dirty.clear()

    def publish(self, path):
        """Diagnostics for the buffer itself; an error inside a module it imports (that is not open in the
        editor, which would report it itself) is shown on the `use` line that brings that module in."""
        a = self.analyses[path]
        diags = []
        t = a.texts[path]
        for e in a.errors:
            f = os.path.abspath(e.file) if e.file else path
            if f == path:
                diags.append(self.diagnostic(a, path, e))
            elif f not in self.docs:
                line = self.use_line(t, path, f)
                d = self.diagnostic(a, path, KlarError(e.code, f"in {short_path(f)}:{e.line}:{e.col}: {e.message}",
                                                         line, 1, e.hint))
                d["range"]["end"] = t.lsp(t.starts[line - 1] + len(t.lines[line - 1]))
                diags.append(d)
        self.send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
                   "params": {"uri": path_to_uri(path), "diagnostics": diags,
                              **({"version": self.versions[path]} if self.versions.get(path) is not None else {})}})

    def use_line(self, t, path, module):
        first = None
        for i, line in enumerate(t.lines):
            m = re.match(r"\s*use\s+([\w.]+)", line)
            if m:
                first = first or i + 1
                try:
                    if os.path.abspath(loader.resolve_module(m.group(1).split("."), path)) == module:
                        return i + 1
                except Exception:
                    pass
        return first or 1

    def diagnostic(self, a, f, e):
        t = a.text(f) if f != a.path else a.texts[a.path]
        start = t.offset(e.line, e.col) if e.line > 0 else 0
        w = t.word_at(start)
        end = w[1] if w and w[0] == start else min(start + 1, len(t.src))
        msg = e.message + (f"\nhint: {e.hint}" if e.hint else "")
        return {"range": t.range(start, end), "severity": SEV_ERROR, "code": e.code, "source": "klar",
                "message": msg, "data": {"hint": e.hint, "line": e.line, "col": e.col, "text": e.message}}

    # ----------------------------------------------------------------- requests
    def dispatch(self, method, p):
        if method == "initialize":
            return {"capabilities": {
                "positionEncoding": "utf-16",
                "textDocumentSync": {"openClose": True, "change": 1, "save": {"includeText": False}},
                "hoverProvider": True, "definitionProvider": True, "referencesProvider": True,
                "documentHighlightProvider": True, "documentSymbolProvider": True,
                "documentFormattingProvider": True, "renameProvider": {"prepareProvider": True},
                "completionProvider": {"triggerCharacters": [".", "{", " "]},
                "signatureHelpProvider": {"triggerCharacters": ["(", ","]},
                "codeActionProvider": {"codeActionKinds": ["quickfix"]},
                "inlayHintProvider": True, "codeLensProvider": {"resolveProvider": False},
                "workspaceSymbolProvider": True,
            }, "serverInfo": {"name": "klar", "version": "0.10"}}
        if method == "initialized":
            return None
        if method == "shutdown":
            self.shutdown = True
            return None
        if method == "textDocument/didOpen":
            d = p["textDocument"]
            path = uri_to_path(d["uri"])
            self.docs[path] = d["text"]
            self.versions[path] = d.get("version")
            self.analyse(path)
            self.publish(path)
            self.recheck_others(path)
            return None
        if method == "textDocument/didChange":
            path = uri_to_path(p["textDocument"]["uri"])
            for ch in p["contentChanges"]:
                if "range" in ch:
                    t = Text(self.docs.get(path, ""))
                    a, b = t.from_lsp(ch["range"]["start"]), t.from_lsp(ch["range"]["end"])
                    self.docs[path] = t.src[:a] + ch["text"] + t.src[b:]
                else:
                    self.docs[path] = ch["text"]
            self.versions[path] = p["textDocument"].get("version")
            self.dirty.add(path)
            return None
        if method == "textDocument/didSave":
            path = uri_to_path(p["textDocument"]["uri"])
            self.recheck_others(path)
            return None
        if method == "textDocument/didClose":
            path = uri_to_path(p["textDocument"]["uri"])
            for m in (self.docs, self.versions, self.analyses, self.good):
                m.pop(path, None)
            self.dirty.discard(path)
            self.send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
                       "params": {"uri": path_to_uri(path), "diagnostics": []}})
            return None
        handler = {
            "textDocument/hover": self.on_hover,
            "textDocument/definition": self.on_definition,
            "textDocument/references": self.on_references,
            "textDocument/documentHighlight": self.on_highlight,
            "textDocument/documentSymbol": self.on_symbols,
            "textDocument/formatting": self.on_format,
            "textDocument/completion": self.on_completion,
            "textDocument/signatureHelp": self.on_signature,
            "textDocument/prepareRename": self.on_prepare_rename,
            "textDocument/rename": self.on_rename,
            "textDocument/codeAction": self.on_code_action,
            "textDocument/inlayHint": self.on_inlay_hints,
            "textDocument/codeLens": self.on_code_lens,
        }.get(method)
        if method == "workspace/symbol":
            return self.on_workspace_symbols(p)
        if handler is None:
            if method.startswith("$/"):
                return None
            raise KlarError("E000", f"unsupported method {method}")
        path = uri_to_path(p["textDocument"]["uri"])
        if path not in self.docs:
            try:
                with open(path, encoding="utf-8") as f:
                    self.docs[path] = f.read()
            except OSError:
                return None
        return handler(path, p)

    def recheck_others(self, path):
        """Another buffer may import the one that changed: recheck the open buffers in the background pass."""
        for other in self.docs:
            if other != path:
                self.dirty.add(other)

    def position(self, path, p):
        a = self.current(path)
        t = Text(self.docs[path])
        return a, t, t.from_lsp(p["position"])

    def location(self, a, target):
        module, s, e = target
        t = a.text(module) if module != a.path else a.texts[a.path]
        return {"uri": path_to_uri(module), "range": t.range(s, e)}

    def on_hover(self, path, p):
        a, t, off = self.position(path, p)
        if a is None:
            return None
        s = a.span_at(path, off)
        if s is None:
            return None
        text = a.hover(s)
        if text is None:
            return None
        return {"contents": {"kind": "markdown", "value": text}, "range": t.range(s.start, s.end)}

    def on_definition(self, path, p):
        a, _, off = self.position(path, p)
        if a is None:
            return None
        s = a.span_at(path, off)
        if s is None or s.target is None:
            return None
        return self.location(a, s.target)

    def references(self, a, target):
        out = []
        for module, spans in a.spans.items():
            for s in spans:
                if s.target == target:
                    out.append((module, s))
        return out

    def on_references(self, path, p):
        a, _, off = self.position(path, p)
        if a is None:
            return None
        s = a.span_at(path, off)
        if s is None or s.target is None:
            return []
        include_decl = (p.get("context") or {}).get("includeDeclaration", True)
        return [self.location(a, (m, r.start, r.end)) for m, r in self.references(a, s.target)
                if include_decl or (m, r.start, r.end) != s.target]

    def on_highlight(self, path, p):
        a, t, off = self.position(path, p)
        if a is None:
            return None
        s = a.span_at(path, off)
        if s is None or s.target is None:
            return []
        return [{"range": t.range(r.start, r.end), "kind": 3 if r.kind == "decl" else 2}
                for m, r in self.references(a, s.target) if m == path]

    def on_prepare_rename(self, path, p):
        a, t, off = self.position(path, p)
        if a is None:
            return None
        s = a.span_at(path, off)
        if s is None or s.target is None or self.read_only(s.target[0]):
            return None
        return {"range": t.range(s.start, s.end), "placeholder": s.name}

    def read_only(self, module):
        return module.startswith(loader.STD_DIR + os.sep) or (os.sep + ".klar" + os.sep) in module

    def on_rename(self, path, p):
        a, _, off = self.position(path, p)
        new = p["newName"]
        if a is None:
            return None
        s = a.span_at(path, off)
        if s is None or s.target is None:
            raise KlarError("E000", "nothing to rename here")
        if self.read_only(s.target[0]):
            raise KlarError("E000", f"`{s.name}` is defined in the standard library or a dependency")
        if not IDENT.fullmatch(new) or new in KEYWORDS:
            raise KlarError("E000", f"`{new}` is not a valid name")
        if new[0].isupper() != s.name[0].isupper():
            raise KlarError("E000", "types and variants start with a capital letter, everything else does not")
        changes = {}
        for m, r in self.references(a, s.target):
            t = a.text(m) if m != a.path else a.texts[a.path]
            changes.setdefault(path_to_uri(m), []).append({"range": t.range(r.start, r.end), "newText": new})
        return {"changes": changes}

    def on_symbols(self, path, p):
        t = Text(self.docs[path])
        try:
            prog = parse(self.docs[path], path)
        except KlarError:
            a = self.good.get(path)
            return [] if a is None else self._symbols_from(a.prog.decls, t, path)
        return self._symbols_from(prog.decls, t, path)

    def _symbols_from(self, decls, t, path):
        out = []

        def sym(name, kind, node, children=(), detail=None):
            off = t.find_name(name, node.line, node.col)
            if off is None:
                return None
            start = t.offset(node.line, node.col)
            r = t.range(start, off + len(name))
            d = {"name": name, "kind": kind, "range": r, "selectionRange": t.range(off, off + len(name)),
                 "children": [c for c in children if c]}
            if detail:
                d["detail"] = detail
            return d
        for d in decls:
            if getattr(d, "module", path) not in (path, None):
                continue
            if d.kind in ("Fn", "Extern"):
                out.append(sym(d.name, S_FUNCTION, d))
            elif d.kind == "Struct":
                out.append(sym(d.name, S_STRUCT, d, [sym(f, S_FIELD, d) for f, _ in d.fields]))
            elif d.kind == "Enum":
                out.append(sym(d.name, S_ENUM, d, [sym(v.name, S_ENUM_MEMBER, v) for v in d.variants]))
            elif d.kind == "Trait":
                out.append(sym(d.name, S_INTERFACE, d, [sym(m.name, S_METHOD, m) for m in d.methods]))
            elif d.kind == "Test":
                start = t.offset(d.line, d.col)
                r = t.range(start, start + 4)
                out.append({"name": f'test "{d.name}"', "kind": S_METHOD, "range": r, "selectionRange": r, "children": []})
        return [s for s in out if s]

    def on_format(self, path, p):
        from .fmt import format_source
        src = self.docs[path]
        try:
            new = format_source(src)
        except KlarError:
            return None         # code that does not parse is left alone; the diagnostics say why
        if new == src:
            return []
        t = Text(src)
        return [{"range": t.range(0, len(src)), "newText": new}]

    # ----------------------------------------------------------------- completion
    def on_completion(self, path, p):
        a, t, off = self.position(path, p)
        line_start = t.starts[t.lsp(off)["line"]]
        before = t.src[line_start:off]
        m = re.match(r"\s*use\s+([\w.]*)$", before)
        if m:
            return self.complete_module_path(path, m.group(1))
        m = re.match(r"\s*use\s+([\w.]+)\s*\{[^}]*$", before)
        if m:
            return self.complete_module_names(a, path, m.group(1))
        if a is None:
            return [{"label": k, "kind": K_KEYWORD} for k in KEYWORDS]
        m = re.search(r"\.(\w*)$", before)
        if m and not re.search(r"\d\.\w*$", before):
            return self.complete_after_dot(a, path, off - len(m.group(1)) - 1)
        prefix = re.search(r"\w*$", before).group(0)
        items = self.complete_names(a, path, off)
        arms = self.complete_match_arms(a, t, off - len(prefix))
        if arms is not None:
            return {"isIncomplete": False, "items": arms + items}
        want = self.expected_type(a, t, off - len(prefix))
        if want is not None:
            self.rank_for(a, path, items, want)
        if len(prefix) >= 2 or want is not None:
            items += self.complete_imports(a, path, t, prefix, want)
        return {"isIncomplete": len(prefix) < 2, "items": items}

    # what goes here: the parameter type of the enclosing call (inside a list literal: its element type)
    def expected_type(self, a, t, off):
        src = t.src
        depth, commas, i, in_list = 0, 0, off - 1, False
        named = re.search(r"(\w+)\s*:\s*$", src[max(0, off - 80):off])
        while i >= 0 and off - i < 3000:
            ch = src[i]
            if ch in ")]}":
                depth += 1
            elif ch in "([{":
                if depth == 0:
                    if ch == "[" and not in_list:
                        in_list = True
                        commas = 0
                        i -= 1
                        continue
                    if ch != "(":
                        return None
                    break
                depth -= 1
            elif ch == "," and depth == 0:
                commas += 1
            i -= 1
        else:
            return None
        w = t.word_at(i)
        if w is None or w[1] != i:
            return None
        name = src[w[0]:w[1]]
        c = a.checker
        if name in c.fns:
            params = c.fns[name].params
            if w[0] > 0 and src[w[0] - 1] == ".":
                commas += 1
        elif name in c.structs:
            params = c.structs[name]
        elif name in c.variants:
            params = c.variants[name].fields
        else:
            return None
        ty = None
        if named and not in_list:
            ty = dict(params).get(named.group(1))
        elif commas < len(params):
            ty = params[commas][1]
        ty = prune(ty) if ty is not None else None
        if in_list:
            if not (isinstance(ty, TCon) and ty.name == "list" and ty.args):
                return None
            ty = prune(ty.args[0])
        return ty if isinstance(ty, TCon) else None

    def rank_for(self, a, path, items, want):
        """Put what fits the expected type first: its variants, and functions that return it."""
        c = a.checker
        for it in items:
            label = it["label"]
            fits = False
            if it["kind"] == K_ENUM_MEMBER and label in c.variants:
                fits = c.variants[label].enum == want.name
            elif it["kind"] == K_FUNCTION and label in c.fns:
                r = prune(c.fns[label].ret)
                fits = isinstance(r, TCon) and r.name == want.name
            elif it["kind"] == K_VARIABLE:
                fits = it.get("detail", "").split("[")[0] == want.name
            if fits:
                it["sortText"] = "0" + label
                it["preselect"] = True

    def complete_imports(self, a, path, t, prefix, want):
        """Names from modules this file does not import yet (the standard library and the project's own files),
        with the `use` added when one is picked. With an expected type, only names that fit it."""
        c = a.checker
        items = []
        try:
            exports = fixes.all_exports(path)
        except Exception:
            return items
        src = Text(self.docs[path])
        for name, (mod, kind, node) in exports.items():
            if prefix and not name.lower().startswith(prefix.lower()):
                continue
            if name in c.fns or name in c.structs or name in c.enums or name in c.variants or name in c.extern_types:
                if self.visible(a, path, name) or c.def_module.get(name) is None:
                    continue
            if want is not None:
                if kind == "Variant":
                    if node.name != want.name:
                        continue
                elif kind in ("Fn", "Extern"):
                    r = node.ret
                    if not (r is not None and r.kind == "TName" and r.name == want.name):
                        continue
                else:
                    continue
            elif "__" in name:
                continue
            use = f"use {mod} {{ {name} }}"
            e = fixes.import_edit(fixes.Src(self.docs[path]), use)
            if e is None:
                continue
            edit = {"range": src.range(src.offset(e["line"], e["col"]), src.offset(e["end_line"], e["end_col"])),
                    "newText": e["text"]}
            kindc = {"Fn": K_FUNCTION, "Extern": K_FUNCTION, "Struct": K_STRUCT, "Enum": K_ENUM,
                     "Variant": K_ENUM_MEMBER}.get(kind, K_INTERFACE)
            detail = mod if kind != "Variant" else f"{node.name} from {mod}"
            if kind in ("Fn", "Extern"):
                detail = self.decl_line(node) + "  (" + mod + ")"
            items.append({"label": name, "kind": kindc, "detail": detail,
                          "labelDetails": {"description": mod},
                          "sortText": ("0" if want is not None else "4") + name,
                          "additionalTextEdits": [edit]})
            if len(items) >= 200:
                break
        return items

    def decl_line(self, node):
        """`fn name(...) -> T` as written, from the module's source."""
        try:
            text = self.text_of(node.module) if getattr(node, "module", None) else None
        except Exception:
            text = None
        if text is None:
            return f"fn {node.name}(...)"
        line = text.split("\n")[node.line - 1].strip()
        return line[:line.index("{")].strip() if "{" in line else line

    def text_of(self, module):
        if module in self.docs:
            return self.docs[module]
        with open(module, encoding="utf-8") as f:
            return f.read()

    def complete_match_arms(self, a, t, off):
        """At the start of a match arm: the variants of the matched enum, the ones not covered yet first."""
        src = t.src
        line_start = src.rfind("\n", 0, off) + 1
        if src[line_start:off].strip():
            return None
        depth, i = 0, off - 1
        while i >= 0 and off - i < 4000:
            ch = src[i]
            if ch == "}":
                depth += 1
            elif ch == "{":
                if depth == 0:
                    break
                depth -= 1
            i -= 1
        else:
            return None
        head = src[src.rfind("\n", 0, i) + 1:i]
        m = re.search(r"\bmatch\s+(\w+)\s*$", head)
        if not m:
            return None
        sp = a.span_at(self.path_of(a), i - len(head) + head.rindex(m.group(1)))
        if sp is None or sp.kind not in ("decl", "local"):
            return None
        ty = prune(sp.info[0])
        c = a.checker
        if not isinstance(ty, TCon) or ty.name not in c.enums:
            return None
        body = src[i:off]
        out = []
        for v in c.enums[ty.name]:
            vi = c.variants[v]
            used = re.search(r"(?<![\w.])" + re.escape(v) + r"\b", body) is not None
            pat = v + ("(" + ", ".join(f for f, _ in vi.fields) + ")" if vi.fields else "")
            out.append({"label": pat + " =>", "kind": K_ENUM_MEMBER, "detail": ty.name, "filterText": v,
                        "insertText": pat + " => ", "sortText": ("!1" if used else "!0") + v})
        return out

    def path_of(self, a):
        return a.path

    def complete_module_path(self, path, typed):
        parts = typed.split(".")
        items = []
        if len(parts) == 1:
            items.append({"label": "std", "kind": K_MODULE})
            base = os.path.dirname(path)
        elif parts[0] == "std":
            base = os.path.join(loader.STD_DIR, *parts[1:-1])
        else:
            base = os.path.join(os.path.dirname(path), *parts[:-1])
        try:
            for f in sorted(os.listdir(base)):
                full = os.path.join(base, f)
                if f.endswith(".klar") and full != path:
                    items.append({"label": f[:-5], "kind": K_MODULE})
                elif os.path.isdir(full) and not f.startswith(".") and f != "__pycache__":
                    items.append({"label": f, "kind": K_MODULE})
        except OSError:
            pass
        return items

    def complete_module_names(self, a, path, dotted):
        try:
            target = loader.resolve_module(dotted.split("."), path)
            with open(target, encoding="utf-8") as f:
                mod = parse(f.read(), target)
        except (OSError, KlarError, Exception):
            return []
        items = []
        for d in mod.decls:
            if d.kind in ("Fn", "Extern"):
                items.append({"label": d.name, "kind": K_FUNCTION})
            elif d.kind == "Struct":
                items.append({"label": d.name, "kind": K_STRUCT})
            elif d.kind == "Enum":
                items.append({"label": d.name, "kind": K_ENUM})
                items += [{"label": v.name, "kind": K_ENUM_MEMBER, "detail": d.name} for v in d.variants]
            elif d.kind in ("Trait", "ExternType"):
                items.append({"label": d.name, "kind": K_INTERFACE if d.kind == "Trait" else K_STRUCT})
        return items

    def visible(self, a, path, name):
        c = a.checker
        mod = c.def_module.get(name)
        return mod is None or os.path.abspath(mod) == path or name in c.visible_in.get(path, ())

    def fn_item(self, a, name):
        sig = a.checker.fns[name]
        return {"label": name, "kind": K_FUNCTION, "detail": a.fn_signature(name),
                "documentation": PRELUDE_DOCS.get(name, "") if sig.builtin else ""}

    def complete_after_dot(self, a, path, dot):
        ty = a.type_before(dot)
        items = []
        c = a.checker
        if ty is None:
            return items
        if isinstance(ty, TCon) and ty.name in c.structs:
            for f, ft in c.struct_fields(ty):
                items.append({"label": f, "kind": K_FIELD, "detail": show(ft), "sortText": "0" + f})
        head = ty.name if isinstance(ty, TCon) else "fn" if isinstance(ty, TFn) else None
        for name, sig in c.fns.items():
            if not sig.params or not self.visible(a, path, name) or "__" in name:
                continue
            p0 = prune(sig.params[0][1])
            fits = (isinstance(p0, TCon) and (p0.name == head or p0.name in sig.tparams)) or \
                   (isinstance(p0, TFn) and head == "fn")
            if fits:
                it = self.fn_item(a, name)
                it["sortText"] = ("1" if isinstance(p0, TCon) and p0.name == head else "2") + name
                items.append(it)
        return items

    def complete_names(self, a, path, off):
        c = a.checker
        items, seen = [], set()

        def add(it):
            if it["label"] not in seen:
                seen.add(it["label"])
                items.append(it)
        # locals declared earlier in the same top-level declaration
        decl_range = None
        t = a.texts[path]
        for d in a.prog.decls:
            if getattr(d, "module", None) == path:
                s = t.offset(d.line, d.col)
                if s <= off:
                    decl_range = s
        for s in a.spans.get(path, ()):
            if s.kind == "decl" and decl_range is not None and decl_range <= s.start < off:
                add({"label": s.name, "kind": K_VARIABLE, "detail": show(s.info[0]), "sortText": "0" + s.name})
        for name, sig in c.fns.items():
            if self.visible(a, path, name) and "__" not in name:
                it = self.fn_item(a, name)
                it["sortText"] = ("2" if sig.builtin else "1") + name
                add(it)
        for name in list(c.structs) + list(c.enums) + list(c.extern_types):
            if self.visible(a, path, name):
                add({"label": name, "kind": K_STRUCT if name in c.structs else K_ENUM, "sortText": "1" + name})
        for name, vi in c.variants.items():
            if self.visible(a, path, name):
                add({"label": name, "kind": K_ENUM_MEMBER, "detail": vi.enum, "sortText": "1" + name})
        for k in KEYWORDS:
            add({"label": k, "kind": K_KEYWORD, "sortText": "3" + k})
        return items

    def on_signature(self, path, p):
        a, t, off = self.position(path, p)
        if a is None:
            return None
        depth, commas, i = 0, 0, off - 1
        src = t.src
        while i >= 0 and off - i < 2000:
            ch = src[i]
            if ch in ")]}":
                depth += 1
            elif ch in "([{":
                if depth == 0:
                    if ch != "(":
                        return None
                    break
                depth -= 1
            elif ch == "," and depth == 0:
                commas += 1
            elif ch == "\n" and depth == 0 and src[max(i - 1, 0)] in "{;":
                return None
            i -= 1
        else:
            return None
        w = t.word_at(i)
        if w is None or w[1] != i:
            return None
        name = src[w[0]:w[1]]
        c = a.checker
        if name in c.fns:
            sig = c.fns[name]
            label = a.fn_signature(name)
            params = [f"{n}: {show(ty)}" for n, ty in sig.params]
            if w[0] > 0 and src[w[0] - 1] == ".":      # x.f(a): the first parameter is x
                commas += 1
        elif name in c.structs:
            params = [f"{f}: {show(ty)}" for f, ty in c.structs[name]]
            label = f"{name}({', '.join(params)})"
        elif name in c.variants and c.variants[name].fields:
            params = [f"{f}: {show(ty)}" for f, ty in c.variants[name].fields]
            label = f"{name}({', '.join(params)})"
        else:
            return None
        return {"signatures": [{"label": label, "parameters": [{"label": x} for x in params]}],
                "activeSignature": 0, "activeParameter": min(commas, max(len(params) - 1, 0))}

    # ----------------------------------------------------------------- quick fixes
    def on_code_action(self, path, p):
        """The fixes `klar check --json` lists for each diagnostic (klar/fixes.py), as quick fixes."""
        t = Text(self.docs[path])
        actions = []
        for d in (p.get("context") or {}).get("diagnostics", []):
            data = d.get("data") or {}
            if d.get("source") != "klar" or "text" not in data:
                continue
            e = KlarError(d.get("code") or "E000", data["text"], data.get("line", 0), data.get("col", 0), data.get("hint"))
            if re.match(r"in \S+:\d+:\d+: ", e.message):
                continue            # an error inside an imported module, shown on its `use` line
            for f in fixes.fixes(e, self.docs[path], path, self.overlay()):
                edits = [{"range": t.range(t.offset(x["line"], x["col"]), t.offset(x["end_line"], x["end_col"])),
                          "newText": x["text"]} for x in f["edits"]]
                actions.append({"title": f["title"], "kind": "quickfix", "diagnostics": [d],
                                "isPreferred": bool(f.get("preferred")),
                                "edit": {"changes": {path_to_uri(path): edits}}})
        return actions

    # ----------------------------------------------------------------- inlay hints, lenses, workspace symbols
    def on_inlay_hints(self, path, p):
        """`: type` after every `let`/`var`/pattern name the checker inferred."""
        a, t = self.current(path), Text(self.docs[path])
        if a is None:
            return []
        lo, hi = t.from_lsp(p["range"]["start"]), t.from_lsp(p["range"]["end"])
        src = a.texts[path].src
        if src != t.src:
            return []
        out = []
        for s in a.spans.get(path, ()):
            if s.kind != "decl" or not (lo <= s.start <= hi) or s.name == "_":
                continue
            node = s.info[1] if len(s.info) > 1 else None
            if node is not None and getattr(node, "kind", "") == "Let" and getattr(node, "ann", None) is not None:
                continue
            j = s.end
            while j < len(src) and src[j] in " \t":
                j += 1
            if j < len(src) and src[j] == ":":
                continue            # annotated (a parameter or `let x: T`)
            ty = show(s.info[0])
            if ty in ("_", "never"):
                continue
            out.append({"position": t.lsp(s.end), "label": f": {ty}", "kind": 1, "paddingLeft": False})
        return out

    def on_code_lens(self, path, p):
        """Run (and Debug) above `fn main`, and above each `test`."""
        t = Text(self.docs[path])
        try:
            prog = parse(self.docs[path], path)
        except KlarError:
            return []
        uri = path_to_uri(path)
        out = []
        for d in prog.decls:
            r = t.range(t.offset(d.line, d.col), t.offset(d.line, d.col))
            if d.kind == "Fn" and d.name == "main" and not d.params:
                out.append({"range": r, "command": {"title": "Run", "command": "klar.run", "arguments": [uri]}})
                out.append({"range": r, "command": {"title": "Debug", "command": "klar.debug", "arguments": [uri]}})
            elif d.kind == "Test":
                out.append({"range": r, "command": {"title": "Run tests", "command": "klar.test", "arguments": [uri]}})
                break
        return out

    def on_workspace_symbols(self, p):
        q = (p.get("query") or "").lower()
        seen, out = set(), []
        roots = list(self.docs) or [os.path.join(os.getcwd(), "main.klar")]
        for dotted, f in fixes.module_files(roots[0]) + [(None, d) for d in self.docs]:
            if f in seen or (dotted or "").startswith("std.") and not q:
                continue
            seen.add(f)
            for name, (mod, kind, node) in fixes._exports_of(f, dotted or "").items():
                if q and q not in name.lower():
                    continue
                if kind == "Variant":
                    node = next((v for v in node.variants if v.name == name), node)
                t = self.docs.get(f)
                line = max(node.line - 1, 0)
                out.append({"name": name, "kind": {"Fn": S_FUNCTION, "Extern": S_FUNCTION, "Struct": S_STRUCT,
                                                   "Enum": S_ENUM, "Variant": S_ENUM_MEMBER}.get(kind, S_INTERFACE),
                            "containerName": dotted or short_path(f),
                            "location": {"uri": path_to_uri(f), "range": {"start": {"line": line, "character": max(node.col - 1, 0)},
                                                                         "end": {"line": line, "character": max(node.col - 1, 0)}}}})
                if len(out) >= 500:
                    return out
        return out


def describe_at(path, src, line, col):
    """What the editor shows for the name at (line, col): for `klar check FILE --at LINE:COL`."""
    path = os.path.abspath(path)
    server = Server(None, None)
    server.docs[path] = src
    a = Analysis(server, path, src)
    if a.checker is None:
        e = a.errors[0] if a.errors else None
        return {"error": e.render(src) if e else "the file could not be checked"}
    t = a.texts[path]
    off = t.offset(line, col)
    sp = a.span_at(path, off)
    if sp is None:
        return {"error": f"no name at {path}:{line}:{col}"}
    out = {"name": sp.name, "kind": {"decl": "variable", "local": "variable"}.get(sp.kind, sp.kind)}
    if sp.kind == "module":
        out["signature"] = f"use {sp.name}"
        out["where"] = short_path(os.path.abspath(sp.info))
    else:
        parts = a.hover_parts(sp)
        if parts is None:
            return {"error": f"nothing known about `{sp.name}`"}
        out["signature"], out["where"], out["doc"] = parts
    if sp.target is not None:
        tt = a.text(sp.target[0])
        p = tt.lsp(sp.target[1])
        out["definition"] = {"file": sp.target[0], "line": p["line"] + 1,
                             "col": sp.target[1] - tt.starts[p["line"]] + 1}
        refs = []
        for m, r in server.references(a, sp.target):
            rt = a.text(m)
            rp = rt.lsp(r.start)
            refs.append({"file": m, "line": rp["line"] + 1, "col": r.start - rt.starts[rp["line"]] + 1})
        out["references"] = refs
    return {k: v for k, v in out.items() if v not in ("", None)}


def cmd_lsp():
    server = Server(sys.stdin.buffer, sys.stdout.buffer)
    return server.serve()
