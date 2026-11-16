"""Compile a checked Klar program to Python source code, which `klar run` and `klar test` execute.

Each Klar function becomes one Python function, each Klar local a Python local, and each loop a Python loop,
so a program runs at close to the speed of the same program written in Python (a tree-walking evaluator was
20 to 100 times slower). The generated code keeps the evaluator's semantics exactly: the same value
representation (see interp.py), the same in-place updates of owned vars (see ownership.py), and the same runtime
checks and error messages, which are raised through Interpreter.rt_err with the node of the failing expression.

Expressions that need statements (match, `?`, an if expression with a match inside, ...) are hoisted into
statements before the expression that uses them, spilling earlier operands to temporaries so that evaluation
stays left to right (the same scheme as the JavaScript backend).
"""

import math
import re

from .ownership import self_update, str_append, LIST_UPDATES, MAP_UPDATES
from .types import TCon, TFn, prune, resolve

TRIVIAL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|-?[0-9][0-9.e+-]*|None|True|False|'[^'\\]*'")
ARITH = {"+": 1, "-": 1, "*": 2}


def ident(name):
    """A Python identifier for a Klar name. Klar accepts any Unicode letters and digits, Python fewer, and Python
    folds some letters together (NFKC), so names that are not plain ASCII are spelled out by code point."""
    if name.isascii() and (name.isalnum() or "_" in name) and name.replace("_", "a").isalnum():
        return "v_" + name
    return "w_" + "".join(c if c.isascii() and c.isalnum() else f"_{ord(c):x}_" for c in name)


def kind(t):
    t = prune(t) if t is not None else None
    if isinstance(t, TCon):
        return t.name
    if isinstance(t, TFn):
        return "fn"
    return None


def trivial(code):
    return TRIVIAL.fullmatch(code) is not None


def tup(codes):
    return "(" + ", ".join(codes) + ("," if len(codes) == 1 else "") + ")"


def type_names(t, out):
    t = prune(t)
    if isinstance(t, TCon):
        out.add(t.name)
        for a in t.args:
            type_names(a, out)
    elif isinstance(t, TFn):
        for p in t.params:
            type_names(p, out)
        type_names(t.ret, out)
    return out


def name_uses(node, out, seen):
    """Every Name read anywhere inside node (used to find what an anonymous function captures)."""
    if id(node) in seen:
        return
    seen.add(id(node))
    if node.kind == "Name":
        out.add(node.name)
    for k, v in node.__dict__.items():
        if k in ("ty", "inst"):
            continue
        _uses_in(v, out, seen)


def _uses_in(v, out, seen):
    if hasattr(v, "kind") and hasattr(v, "line") and hasattr(v, "__dict__"):
        name_uses(v, out, seen)
    elif isinstance(v, (list, tuple)):
        for x in v:
            _uses_in(x, out, seen)


def owned_vars(body, fns):
    """name -> "list" | "map" | "str" for each var of this function body that is ever updated in place
    (not looking into anonymous functions, which have their own vars)."""
    out = {}

    def walk(n):
        if isinstance(n, (list, tuple)):
            for x in n:
                walk(x)
            return
        if not (hasattr(n, "kind") and hasattr(n, "__dict__")):
            return
        if n.kind == "Lambda":
            return
        if n.kind == "Assign":
            steps = self_update(n, fns, LIST_UPDATES)
            if steps is not None:
                out[n.name] = "list"
            elif self_update(n, fns, MAP_UPDATES) is not None:
                out[n.name] = "map"
            elif kind(getattr(n.value, "ty", None)) == "str" and str_append(n) is not None:
                out[n.name] = "str"
        for k, v in n.__dict__.items():
            if k not in ("ty", "inst"):
                walk(v)
    walk(body)
    return out


class Ctx:
    """One Python function being generated (a Klar function, an anonymous function or a test)."""
    def __init__(self, owned):
        self.owned = owned


class PyGen:
    def __init__(self, ip, filename):
        self.ip = ip
        self.fns = ip.fns
        self.filename = filename
        self.consts = {}            # name -> value, put into the generated module's globals
        self.const_ids = {}
        self.out = []               # (text, klar line, pure) for the function being generated
        self.lambda_labels = {}     # Python name of an anonymous function -> "<fn at file:line>"
        self.module = None
        self.kline = 0
        self.scopes = []
        self.ctx = None
        self.n = 0
        self.pynames = {}
        self.tparam_names = set()
        for d in ip.fns.values():
            self.tparam_names.update(d.tparams or ())
        for i, name in enumerate(ip.fns):
            self.pynames[name] = f"f{i}_{ident(name)[2:]}"

    # ------------------------------------------------------------------ output
    def tmp(self, prefix="_t"):
        self.n += 1
        return f"{prefix}{self.n}"

    def emit(self, text, pure=False):
        self.out.append((text, self.kline, pure))

    def emit_block(self, head, lines, pure=False):
        self.out.append((head, self.kline, pure))
        self.out.extend(indent(lines or [("pass", self.kline, True)]))

    def const(self, value, prefix="_c"):
        key = id(value)
        name = self.const_ids.get(key)
        if name is None:
            name = self.const_ids[key] = self.tmp(prefix)
            self.consts[name] = value
        return name

    def node(self, n):
        return self.const(n, "_n")

    def named_const(self, name, value):
        self.consts[name] = value
        return name

    # ------------------------------------------------------------------ scopes
    def declare(self, name):
        self.scopes[-1].add(name)

    def is_local(self, name):
        return any(name in s for s in self.scopes)

    @staticmethod
    def var(name):
        return ident(name)

    def params(self, params):
        out = []
        for i, (p, _) in enumerate(params):
            if p == "_":
                out.append(f"_u{i}")
            else:
                out.append(self.var(p))
                self.declare(p)
        return out

    # ------------------------------------------------------------------ program
    def reachable(self, tests):
        """Names of the functions main and the tests can reach (big std modules are mostly unused)."""
        checker = getattr(self.ip.conv, "checker", None)
        impls = {}
        for info in (checker.impls.values() if checker else ()):
            for method, fname in info.fn_names.items():
                impls.setdefault(method, []).append(fname)
        seen, todo = set(), ["main"]
        uses = set()
        for t in tests:
            name_uses(t.body, uses, set())
        todo += list(uses)
        while todo:
            name = todo.pop()
            if name in seen:
                continue
            seen.add(name)
            todo += impls.get(name, ())
            d = self.fns.get(name)
            if d is not None and d.body is not None:
                uses = set()
                name_uses(d.body, uses, set())
                todo += [u for u in uses if u not in seen]
        return seen

    def program(self, tests, roots=None):
        """Python source for the functions main and the tests use (every function if roots == "all") and for the tests;
        returns (source, Klar line per Python line, Klar file per Python line, test function names)."""
        lines, files = [], []
        wanted = self.reachable(tests) if roots is None else None
        for name, d in self.fns.items():
            if d.body is not None and (wanted is None or name in wanted):
                self.module = getattr(d, "module", None) or self.ip.file
                part = self.function(d)
                lines += part
                files += [self.module] * len(part)
        test_names = []
        for t in tests:
            pyname = self.tmp("_test")
            test_names.append(pyname)
            self.module = getattr(t, "module", None) or self.ip.file
            part = self.test(t, pyname)
            lines += part
            files += [self.module] * len(part)
        source = "\n".join(text for text, _, _ in lines) + "\n"
        return source, [k for _, k, _ in lines], files, test_names

    def function(self, d):
        self.ctx = Ctx(owned_vars(d.body, self.fns))
        self.scopes = [set()]
        self.out = []
        self.kline = d.line
        ps = self.params(d.params)
        body = self.block(d.body)
        self.kline = d.line
        head = f"def {self.pynames[d.name]}({', '.join(ps + ['*', '_tenv=_E'])}):"
        return [(head, d.line, False)] + indent(body or [("pass", d.line, True)])

    def test(self, t, pyname):
        self.ctx = Ctx(owned_vars(t.body, self.fns))
        self.scopes = [set()]
        self.out = []
        self.kline = t.line
        body = self.block(t.body)
        return [(f"def {pyname}():", t.line, False), ("    _tenv = _E", t.line, True)] + indent(body)

    # ------------------------------------------------------------------ statements
    def block(self, b, extra=()):
        saved = self.out
        self.out = list(extra)
        self.scopes.append(set())
        for s in b.stmts:
            self.stmt(s)
        self.scopes.pop()
        lines, self.out = self.out, saved
        return lines

    def sub(self, fn, *a):
        """Run fn with a fresh output list; returns (lines, result)."""
        saved = self.out
        self.out = []
        r = fn(*a)
        lines, self.out = self.out, saved
        return lines, r

    def stmt(self, s):
        self.kline = s.line
        k = s.kind
        if k == "Let":
            code = self.ex(s.value)
            self.declare(s.name)
            self.emit(f"{self.var(s.name)} = {code}")
        elif k == "Assign":
            self.assign(s)
        elif k == "ExprStmt":
            code = self.ex(s.expr)
            if not trivial(code):
                self.emit(code)
        elif k == "If":
            self.if_chain(s)
        elif k == "While":
            pre, cond = self.sub(self.ex, s.cond)
            body = self.block(s.body)
            if pre:
                self.emit_block("while True:", pre + [(f"if not {cond}: break", s.line, False)] + body)
            else:
                self.emit_block(f"while {cond}:", body)
        elif k == "For":
            it = s.iter
            if (it.kind == "Call" and getattr(it, "target_kind", "") == "fn" and it.callee.kind == "Name"
                    and it.callee.name == "range" and "range" not in self.fns and not it.named and len(it.args) == 2):
                a, b = self.seq(it.args)
                head = f"range({a}, {b})"
            else:
                head = self.ex(it)
            self.scopes.append({s.var})
            body = self.block(s.body)
            self.scopes.pop()
            self.kline = s.line
            self.emit_block(f"for {self.var(s.var)} in {head}:", body)
        elif k == "Return":
            if s.value is None:
                self.emit("return")
            else:
                self.emit(f"return {self.ex(s.value)}")
        elif k == "Break":
            self.emit("break")
        elif k == "Continue":
            self.emit("continue")
        elif k == "Assert":
            self.assertion(s)
        elif k == "Destructure":
            for x in s.stmts:
                self.stmt(x)
        else:
            raise RuntimeError(f"cannot run statement {k}")

    def if_chain(self, s):
        cond = self.ex(s.cond)
        self.emit_block(f"if {cond}:", self.block(s.then))
        els = s.els
        while els is not None:
            if len(els.stmts) == 1 and els.stmts[0].kind == "If":
                inner = els.stmts[0]
                self.kline = inner.line
                pre, c = self.sub(self.ex, inner.cond)
                if not pre:     # `else if` becomes elif (a long chain would nest too deep for Python)
                    self.emit_block(f"elif {c}:", self.block(inner.then))
                    els = inner.els
                    continue
            self.emit_block("else:", self.block(els))
            return

    def assign(self, s):
        v = self.var(s.name)
        steps = self_update(s, self.fns, LIST_UPDATES) or self_update(s, self.fns, MAP_UPDATES)
        parts = str_append(s) if steps is None and kind(getattr(s.value, "ty", None)) == "str" else None
        if steps is not None:
            # every index and value is evaluated first (they all read the old value), then the writes happen
            codes = self.seq([a for _, call in steps for a in call.args[1:]])
            codes = [self.spill(c) for c in codes]
            it = iter(codes)
            if steps[0][0] == "put":
                self.emit(f"if type({v}) is not OwnedMap: {v} = OwnedMap({v})")
                for _ in steps:
                    key, x = next(it), next(it)
                    self.emit(f"{v}[{key}] = {x}")
                return
            self.emit(f"if type({v}) is not list: {v} = list({v})")
            for op, call in steps:
                args = [next(it) for _ in call.args[1:]]
                self.kline = s.line
                if op == "push":
                    self.emit(f"{v}.append({args[0]})")
                elif op == "set_at":
                    i, x = args
                    self.emit(f"if not 0 <= {i} < len({v}): _set_oob({i}, {v}, {self.node(call)})")
                    self.emit(f"{v}[{i}] = {x}")
                elif op == "pop":
                    self.emit(f"if not {v}: _pop_empty({self.node(call)})")
                    self.emit(f"{v}.pop()")
                else:
                    raise RuntimeError(f"cannot update a list in place with {op}")
            return
        if parts is not None:
            codes = [self.spill(c) for c in self.seq(parts)]
            self.emit(f"if type({v}) is not OwnedStr: {v} = OwnedStr(({v},))")
            if len(codes) == 1:
                self.emit(f"{v}.append({codes[0]})")
            else:
                self.emit(f"{v}.extend({tup(codes)})")
            return
        self.emit(f"{v} = {self.ex(s.value)}")

    def spill(self, code):
        if trivial(code):
            return code
        t = self.tmp()
        self.emit(f"{t} = {code}")
        return t

    def assertion(self, s):
        c = s.cond
        detail = "None"
        if c.kind == "Binary" and c.op in ("==", "!=", "<", "<=", ">", ">="):
            a, b = [self.spill(x) for x in self.seq([c.left, c.right])]
            cond = f"({a} {c.op} {b})"
            detail = f"_assert_detail({a}, {b})"
        else:
            cond = self.ex(c)
        msg = "'assertion failed'"
        lines = []
        if s.msg is not None:
            lines, m = self.sub(self.ex, s.msg)
            msg = f"'assertion failed: ' + {m}"
        self.kline = s.line
        lines.append((f"_err('E404', {msg}, {self.node(s)}, {detail})", s.line, False))
        self.emit_block(f"if not {cond}:", lines)

    # ------------------------------------------------------------------ expressions
    def ex(self, e):
        m = getattr(self, "e_" + e.kind, None)
        if m is None:
            raise RuntimeError(f"cannot evaluate {e.kind}")
        return m(e)

    def seq(self, nodes, first=None):
        """Compile several operands, keeping left-to-right evaluation when a later one hoists statements.
        `first`, if given, compiles the first node instead of ex (used for raw reads)."""
        codes = []
        for i, n in enumerate(nodes):
            mark = len(self.out)
            code = first(n) if (i == 0 and first is not None) else self.ex(n)
            if any(not p for _, _, p in self.out[mark:]):
                spills = []
                for j, cj in enumerate(codes):
                    if not trivial(cj):
                        t = self.tmp()
                        spills.append((f"{t} = {cj}", self.kline, False))
                        codes[j] = t
                self.out[mark:mark] = spills
            codes.append(code)
        return codes

    def e_Int(self, e):
        return str(e.value) if e.value >= 0 else f"({e.value})"

    def e_Float(self, e):
        v = e.value
        if math.isinf(v) or math.isnan(v):
            return self.const(v)
        return repr(v) if v >= 0 else f"({v!r})"

    def e_Str(self, e):
        return repr(e.value)

    def e_Bool(self, e):
        return "True" if e.value else "False"

    def e_Unit(self, e):
        return "None"

    def read(self, name, raw=False):
        v = self.var(name)
        k = self.ctx.owned.get(name)
        if k is None or (raw and k != "str"):
            return v
        if k == "list":
            return f"(({v} := tuple({v})) if type({v}) is list else {v})"
        if k == "map":
            return f"(({v} := FrozenMap({v})) if type({v}) is OwnedMap else {v})"
        return f"(({v} := ''.join({v})) if type({v}) is OwnedStr else {v})"

    def raw(self, e):
        """e, but an owned list or map var is read without snapshotting it (for xs[i], len(xs), get and has)."""
        if e.kind == "Name" and self.is_local(e.name):
            return self.read(e.name, raw=True)
        return self.ex(e)

    def e_Name(self, e):
        name = e.name
        if self.is_local(name):
            return self.read(name)
        ip = self.ip
        if name in ip.variants:
            return self.named_const(f"_V_{ident(name)[2:]}", ip.make_enum(name, ()))
        return self.fn_value(name, e)

    def fn_value(self, name, e):
        ip = self.ip
        if name in ip.trait_methods:
            return self.const(ip.trait_value(name, e))
        if name in self.fns:
            return self.pynames[name]
        if name in ip.externs:
            return self.const(ip.extern_value(name, e))
        return self.const(ip.builtin_value(name, e))

    def e_Interp(self, e):
        exprs = [p for p in e.parts if not isinstance(p, str)]
        codes = iter(self.seq(exprs))
        out = []
        for p in e.parts:
            if isinstance(p, str):
                out.append(repr(p))
                continue
            c = next(codes)
            k = kind(getattr(p, "ty", None))
            out.append(c if k == "str" else f"str({c})" if k == "int" else f"fmt({c})")
        if not out:
            return "''"
        if len(out) == 1:
            return out[0]
        return f"''.join({tup(out)})"

    def e_ListLit(self, e):
        return tup(self.seq(e.items))

    def e_MapLit(self, e):
        codes = self.seq([x for kv in e.entries for x in kv])
        pairs = [f"({codes[i]}, {codes[i + 1]})" for i in range(0, len(codes), 2)]
        return f"FrozenMap({tup(pairs)})" if pairs else "FrozenMap()"

    def e_Unary(self, e):
        v = self.ex(e.operand)
        return f"(not {v})" if e.op == "not" else f"(-{v})"

    def e_Binary(self, e):
        op = e.op
        if op in ("and", "or"):
            # a chain a and b and c is written flat, so long chains don't nest parentheses too deep for Python
            chain = [e.right]
            left = e.left
            while left.kind == "Binary" and left.op == op:
                chain.append(left.right)
                left = left.left
            chain.append(left)
            chain.reverse()
            code = self.ex(chain[0])
            for r in chain[1:]:
                pre, rc = self.sub(self.ex, r)
                if not pre:
                    code = f"{code} {op} {rc}"
                    continue
                t = self.tmp()
                self.emit(f"{t} = ({code})")
                self.emit_block(f"if {'' if op == 'and' else 'not '}{t}:", pre + [(f"{t} = {rc}", self.kline, False)])
                code = t
            return f"({code})"
        if op in ARITH:
            # flatten left-nested + - * chains for the same reason
            terms = [(op, e.right)]
            left = e.left
            while left.kind == "Binary" and left.op in ARITH and ARITH[left.op] == ARITH[op]:
                terms.append((left.op, left.right))
                left = left.left
            terms.reverse()
            codes = self.seq([left] + [t for _, t in terms])
            out = codes[0]
            for (o, _), c in zip(terms, codes[1:]):
                out += f" {o} {c}"
            return f"({out})"
        a, b = self.seq([e.left, e.right])
        if op == "/":
            k = kind(getattr(e.left, "ty", None))
            if k == "float":
                if trivial(a):
                    t = b if trivial(b) else self.tmp()
                    test = b if t == b else f"({t} := {b})"
                    return f"({a} / {t} if {test} else _fdiv({a}, {t}))"
                return f"_fdiv({a}, {b})"
            if k == "int":
                if e.right.kind == "Int" and e.right.value > 0 and trivial(a):
                    return f"({a} // {b} if {a} >= 0 else -(-{a} // {b}))"
                return f"_idiv({a}, {b}, {self.node(e)})"
            return f"_div({a}, {b}, {self.node(e)})"
        if op == "%":
            if (kind(getattr(e.left, "ty", None)) == "int" and e.right.kind == "Int" and e.right.value > 0
                    and trivial(a)):
                return f"({a} % {b} if {a} >= 0 else -(-{a} % {b}))"
            return f"_rem({a}, {b}, {self.node(e)})"
        return f"({a} {op} {b})"

    def e_IfExpr(self, e):
        c = self.ex(e.cond)
        pa, a = self.sub(self.ex, e.then)
        pb, b = self.sub(self.ex, e.els)
        if not pa and not pb:
            return f"({a} if {c} else {b})"
        t = self.tmp()
        self.emit_block(f"if {c}:", pa + [(f"{t} = {a}", self.kline, False)])
        self.emit_block("else:", pb + [(f"{t} = {b}", self.kline, False)])
        return t

    def e_MethodCall(self, e):
        return self.ex(e.desugared)

    def e_Field(self, e):
        code = self.ex(e.target)
        t = prune(getattr(e.target, "ty", None))
        fields = self.ip.structs.get(t.name) if isinstance(t, TCon) else None
        if fields is not None and e.name in fields:
            return f"{code}.fields[{fields.index(e.name)}][1]"
        return f"{code}.get({e.name!r})"

    def e_Index(self, e):
        t, i = self.seq([e.target, e.index], first=self.raw)
        k = kind(getattr(e.target, "ty", None))
        n = self.node(e)
        both = trivial(t) and trivial(i)
        if k == "map":
            return f"({t}[{i}] if {i} in {t} else _nokey({i}, {n}))" if both else f"_mindex({t}, {i}, {n})"
        if k in ("list", "str"):
            return f"({t}[{i}] if 0 <= {i} < len({t}) else _oob({i}, {t}, {n}))" if both else f"_lindex({t}, {i}, {n})"
        return f"_index({t}, {i}, {n})"

    def e_Try(self, e):
        t = self.spill_always(self.ex(e.value))
        wrap = getattr(e, "wrap", None)
        fail = t
        if wrap is not None:
            names = tuple(self.ip.variants[wrap])
            fail = f"errv(EnumVal({wrap!r}, ({t}.values[0],), {names!r}))"
        self.emit(f"if {t}.variant != 'Some' and {t}.variant != 'Ok': return {fail}")
        return f"{t}.values[0]"

    def spill_always(self, code):
        t = self.tmp()
        self.emit(f"{t} = {code}")
        return t

    def e_With(self, e):
        codes = self.seq([e.target] + [v for _, v, _ in e.updates])
        upd = ", ".join(f"{f!r}: {c}" for (f, _, _), c in zip(e.updates, codes[1:]))
        return f"_with({codes[0]}, {{{upd}}})"

    def e_Match(self, e):
        m = self.spill_always(self.ex(e.scrutinee))
        r = self.tmp()
        if any(arm.body.kind == "Block" for arm in e.arms):
            self.emit(f"{r} = None")
        word = "if"
        sk = kind(getattr(e.scrutinee, "ty", None))
        for arm in e.arms:
            conds, binds = [], []
            self.pattern(arm.pattern, m, sk, conds, binds)
            self.scopes.append(set())
            lines = []
            for name, code in binds:
                self.declare(name)
                lines.append((f"{self.var(name)} = {code}", arm.line, True))
            if arm.body.kind == "Block":
                lines += self.block(arm.body)
            else:
                pre, code = self.sub(self.ex, arm.body)
                lines += pre + [(f"{r} = {code}", arm.line, False)]
            self.scopes.pop()
            self.kline = arm.line
            if not conds:
                self.emit_block("else:" if word == "elif" else "if True:", lines)
                return r
            self.emit_block(f"{word} {' and '.join(conds)}:", lines)
            word = "elif"
        self.kline = e.line
        self.emit_block("else:", [(f"_err('E306', 'no match arm matched', {self.node(e)})", e.line, False)])
        return r

    def pattern(self, p, code, k, conds, binds):
        pk = p.kind
        if pk == "PWild":
            return
        if pk == "PBind":
            binds.append((p.name, code))
            return
        if pk == "PLit":
            v = p.value
            if isinstance(v, bool):
                conds.append(f"{code} is {v}")
            elif isinstance(v, str) and k == "str":
                conds.append(f"{code} == {v!r}")
            elif type(v) is int and k == "int":
                conds.append(f"{code} == {v!r}")
            else:
                conds.append(f"(type({code}) is {type(v).__name__} and {code} == {self.const(v) if isinstance(v, float) else repr(v)})")
            return
        if pk == "PTuple":
            for i, sp in enumerate(p.subs):
                self.pattern(sp, f"{code}.fields[{i}][1]", None, conds, binds)
            return
        if pk == "PVariant":
            conds.append(f"{code}.variant == {p.name!r}")
            for i, sp in enumerate(p.subs):
                self.pattern(sp, f"{code}.values[{i}]", None, conds, binds)
            return
        conds.append("False")

    def e_Lambda(self, e):
        uses = set()
        name_uses(e.body, uses, set())
        caps = sorted(n for n in uses if self.is_local(n))
        name = self.tmp("_l")
        self.lambda_labels[name] = f"<fn at {self.module}:{e.line}>"
        saved_ctx, saved_line = self.ctx, self.kline
        self.ctx = Ctx(owned_vars(e.body, self.fns) if e.block else {})
        self.scopes.append(set())
        ps = self.params(e.params)
        saved_out = self.out
        self.out = []
        if e.block:
            body = self.block(e.body)
        else:
            code = self.ex(e.body)
            body = self.out + [(f"return {code}", e.line, False)]
        self.out = saved_out
        self.scopes.pop()
        self.ctx, self.kline = saved_ctx, saved_line
        kws = [f"{self.var(c)}={self.var(c)}" for c in caps] + ["_tenv=_tenv"]
        self.emit_block(f"def {name}({', '.join(ps + ['*'] + kws)}):", body, pure=True)
        return name

    # ------------------------------------------------------------------ calls
    def e_Call(self, e):
        ip = self.ip
        tk = getattr(e, "target_kind", "value")
        callee = e.callee
        name = callee.name if callee.kind == "Name" else None
        if tk == "fn" and not e.named and e.args and name in ("len", "get", "has") and name not in self.fns:
            codes = self.seq(e.args, first=self.raw)
            if name == "len":
                return f"len({codes[0]})"
            if name == "has" and trivial(codes[0]):
                return f"({codes[1]} in {codes[0]})"
            return f"_{name}({', '.join(codes)})"
        if e.named:
            if tk == "struct":
                order = ip.structs[name]
            elif tk == "variant":
                order = ip.variants[name]
            elif tk == "trait":
                order = [p for p, _ in ip.conv.checker.fns[name].params]
            else:
                decl = self.fns.get(name) or ip.externs[name]
                order = [p for p, _ in decl.params]
            named = {n: v for n, v, _ in e.named}
            arg_nodes = [named[n] for n in order]
        else:
            arg_nodes = list(e.args)
        if tk == "value":
            codes = self.seq(arg_nodes + [callee])     # the arguments are evaluated before the function
            f = codes.pop()
            if not trivial(f):
                codes = [self.spill(c) for c in codes]
            return f"{f}({', '.join(codes)})"
        codes = self.seq(arg_nodes)
        if tk == "struct":
            pairs = [f"({f!r}, {c})" for f, c in zip(ip.structs[name], codes)]
            return f"StructVal({name!r}, {tup(pairs)})"
        if tk == "variant":
            return f"EnumVal({name!r}, {tup(codes)}, {tuple(ip.variants[name])!r})"
        if tk == "extern":
            return f"_ext({name!r}, {tup(codes)}, {self.node(e)}, _tenv)"
        if tk == "trait":
            # dispatch on the first argument, then call the impl directly (no Python-level *args, so deep
            # recursion through trait methods doesn't use the C stack)
            codes = [self.spill(c) for c in codes]
            t = self.spill_always(f"_trait_fn({name!r}, {self.node(e)}, _tenv, {codes[0]})")
            return f"{t}[0]({', '.join(codes + [f'_tenv={t}[1]'])})"
        if name in self.fns:
            d = self.fns[name]
            inst = getattr(e, "inst", None)
            if d.tparams and inst:
                ts = [resolve(t) for t in inst]
                names = set()
                for t in ts:
                    type_names(t, names)
                if names & self.tparam_names:
                    tenv = f"_mk_tenv({self.const(tuple(d.tparams))}, {self.const(tuple(inst))}, _tenv)"
                else:
                    tenv = self.const(dict(zip(d.tparams, ts)))
                codes = codes + [f"_tenv={tenv}"]
            return f"{self.pynames[name]}({', '.join(codes)})"
        return self.builtin(name, codes, e)

    def builtin(self, name, codes, e):
        a = codes
        if name == "push" and len(a) == 2:
            return f"({a[0]} + ({a[1]},))"
        if name == "sqrt" and getattr(self.ip, "b_sqrt", None) is not None:
            x = a[0] if trivial(a[0]) else self.tmp()
            test = x if x == a[0] else f"({x} := {a[0]})"
            return f"(_sqrt({x}) if {test} >= 0 else _nan)"
        if name == "to_float":
            return f"float({a[0]})"
        if name in ("abs", "min", "max"):
            return f"{name}({', '.join(a)})"
        if name == "str":
            return f"fmt({a[0]})"
        if name == "range":
            return f"tuple(range({a[0]}, {a[1]}))"
        if name in ("is_some", "is_ok"):
            return f"({a[0]}.variant == {('Some' if name == 'is_some' else 'Ok')!r})"
        if name == "unwrap_or" and trivial(a[0]):
            return f"({a[0]}.values[0] if {a[0]}.variant == 'Some' or {a[0]}.variant == 'Ok' else {a[1]})"
        f = getattr(self.ip, "b_" + name, None)
        if f is None:
            return f"_missing({name!r})"
        fn = self.named_const(f"_b_{name}", f)
        return f"{fn}({', '.join([self.node(e)] + a)})"


def indent(lines):
    return [("    " + t, k, p) for t, k, p in lines]


def namespace(ip):
    """The globals the generated code runs with: the value types and small runtime helpers bound to ip."""
    from .interp import StructVal, EnumVal, FrozenMap, OwnedMap, OwnedStr, NONE, some, ok, errv, fmt
    err = ip.rt_err
    inf, nan = math.inf, math.nan

    def _idiv(a, b, node):
        if b == 0:
            err("E402", "integer division by zero", node)
        q = abs(a) // abs(b)
        return q if (a >= 0) == (b >= 0) else -q

    def _fdiv(a, b):
        if b == 0:
            return inf if a > 0 else -inf if a < 0 else nan
        return a / b

    def _div(a, b, node):
        return _idiv(a, b, node) if isinstance(a, int) else _fdiv(a, b)

    def _rem(a, b, node):
        if b == 0:
            err("E402", "remainder by zero", node)
        r = abs(a) % abs(b)
        return r if a >= 0 else -r

    def _oob(i, t, node):
        err("E401", f"index {i} is out of bounds for length {len(t)}", node,
            "use get(xs, i) which returns an Option, or check i < len(xs) first")

    def _nokey(k, node):
        err("E405", f"map has no key {fmt(k, True)}", node,
            "use get(m, key) which returns an Option, or check has(m, key) first")

    def _lindex(t, i, node):
        if 0 <= i < len(t):
            return t[i]
        _oob(i, t, node)

    def _mindex(t, k, node):
        if k in t:
            return t[k]
        _nokey(k, node)

    def _index(t, i, node):
        return _mindex(t, i, node) if isinstance(t, dict) else _lindex(t, i, node)

    def _set_oob(i, cur, node):
        err("E401", f"set_at index {i} is out of bounds for length {len(cur)}", node)

    def _pop_empty(node):
        err("E401", "pop on an empty list", node)

    def _get(c, k):
        if isinstance(c, dict):
            return some(c[k]) if k in c else NONE
        return some(c[k]) if 0 <= k < len(c) else NONE

    def _has(m, k):
        return k in m

    def _with(s, upd):
        return StructVal(s.name, tuple((f, upd[f] if f in upd else v) for f, v in s.fields))

    def _ext(name, args, node, tenv):
        ip.tenv = tenv
        return ip.call_extern(name, list(args), node)

    def _trait_fn(name, node, tenv, first):
        ip.tenv = tenv
        fn, tenv = ip.trait_target(name, first, node)
        return ip.py_fns[fn], tenv

    def _mk_tenv(tparams, inst, tenv):
        ip.tenv = tenv
        return {tp: ip.concrete_type(t) for tp, t in zip(tparams, inst)}

    def _assert_detail(a, b):
        return f"left side = {fmt(a, True)}, right side = {fmt(b, True)}"

    def _missing(name):
        raise RuntimeError(f"missing builtin {name}")

    ns = {k: v for k, v in locals().items() if k.startswith("_") and callable(v)}
    ns.update(StructVal=StructVal, EnumVal=EnumVal, FrozenMap=FrozenMap, OwnedMap=OwnedMap, OwnedStr=OwnedStr,
              NONE=NONE, some=some, ok=ok, errv=errv, fmt=fmt, _err=err, _E={}, _sqrt=math.sqrt, _nan=nan, __builtins__=__builtins__)
    return ns
