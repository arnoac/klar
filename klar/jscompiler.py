"""Klar -> JavaScript compiler (targets: Node.js and the browser).

Same plan as the C backend: generic functions are monomorphised on demand (so printing knows ints from floats),
and the output is one self-contained ES module with the runtime inlined. Functions that `uses io` compile to
async functions and every call to them is awaited; pure functions stay synchronous. That is how servers and
browser apps can wait for the network without callbacks appearing in Klar code.

Expressions that need statements (match, `?`, if-expressions with such parts) are hoisted: `ex()` returns a JS
expression and may first append statements to the current block. `seq()` keeps left-to-right evaluation order
when a later operand hoists statements.
"""

import json
import os
import re

from .errors import KlarError
from .ownership import is_fresh, self_update
from .syntax import is_tuple_name
from .types import TCon, TFn, TVar, INT, prune, resolve, substitute, show

RT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime")
MAX_SAFE = 2 ** 53 - 1
TRIVIAL = re.compile(r"^(?:[A-Za-z_$][\w$]*|-?\d+(?:\.\d+)?(?:e[+-]?\d+)?|\"(?:[^\"\\]|\\.)*\"|true|false|undefined)$")
NODE_ONLY = ("klar.stdpy.fslib.", "klar.stdpy.processlib.", "klar.stdpy.sqlitelib.", "klar.stdpy.cryptolib.", "klar.stdpy.httplib.serve", "klar:web.publish")


class Ctx:
    def __init__(self, is_async):
        self.is_async = is_async
        self.locals = {}
        self.owned = set()      # list vars with an ownership flag o_<name> (see ownership.py)


class JSCompiler:
    def __init__(self, prog, file, target="node", out_path=None):
        self.prog = prog
        self.file = file or "<input>"
        self.target = target
        self.out_path = out_path
        self.js_modules = {}        # module specifier -> alias
        self.bundler = None
        self.c = prog.checker
        self.fns = {d.name: d for d in prog.decls if d.kind == "Fn"}
        self.externs = {d.name: d for d in prog.decls if d.kind == "Extern"}
        self.instances = {}
        self.work = []
        self.defs = []
        self.consts = []
        self.desc_decls = []
        self.desc_fills = []
        self.helpers = {}
        self.imports = []
        self.ntmp = 0
        self.sub = {}
        self.ctx = None
        self.out = None
        self.wasm = {}           # WebAssembly module path -> (JS variable, compiled export kinds)

    # ------------------------------------------------------------------ errors and types
    def unsupported(self, node, what, hint=None):
        raise KlarError("E501", f"{what} cannot be compiled to JavaScript", node.line, node.col,
                        hint or "run this program with `klar run` (the interpreter) instead", self.file)

    def T(self, t):
        if t is None:
            return INT
        return self._default_vars(resolve(substitute(prune(t), self.sub)))

    def _default_vars(self, t):
        t = prune(t)
        if isinstance(t, TVar):
            return INT
        if isinstance(t, TFn):
            return TFn([self._default_vars(p) for p in t.params], self._default_vars(t.ret), t.io)
        return TCon(t.name, [self._default_vars(a) for a in t.args])

    def _subst(self, t, sub):
        return self._default_vars(resolve(substitute(prune(t), sub)))

    def kind(self, t):
        t = prune(t)
        if isinstance(t, TFn):
            return "fn"
        n = t.name
        if n in ("int", "float", "bool", "unit", "never", "str", "list", "map"):
            return n
        if n in ("Option", "Result") or n in self.c.enums:
            return "enum"
        if n in self.c.structs:
            return "struct"
        if n in self.c.extern_types:
            return "extern"
        return "int"

    def tmp(self):
        self.ntmp += 1
        return f"t{self.ntmp}"

    # ------------------------------------------------------------------ driver
    def compile(self):
        if "main" not in self.fns:
            raise KlarError("E201", "no `main` function to compile", 1, 1, "add: fn main() uses io { ... }", self.file)
        main = self.request_fn("main", [])
        while self.work:
            name, targs, jsname = self.work.pop()
            self.gen_fn(self.fns[name], targs, jsname)
        files = ["klar_rt.js", "klar_std.js", "klar_node.js" if self.target == "node" else "klar_web.js"]
        if self.wasm:
            files.append("klar_wasm.js")
        parts = []
        if self.target == "node":
            parts.append('import $fs from "node:fs"; import $path from "node:path"; import $http from "node:http";\n'
                         'import $child from "node:child_process"; import * as $wt from "node:worker_threads"; import $crypto from "node:crypto";')
        parts += self.imports
        for f in files:
            with open(os.path.join(RT_DIR, f), encoding="utf-8") as fh:
                parts.append(fh.read())
        parts.append("// ---- generated ----")
        parts.append(f"$file = {json.dumps(self.file)};")
        for name, d in self.c.structs.items():
            pass
        parts += self.struct_classes()
        parts += self.consts
        parts += self.desc_decls
        parts += self.desc_fills
        parts += self.defs
        main_call = f"await {main}()" if self.fn_is_io("main") else f"{main}()"
        parts.append(f"$start(async () => {{ {main_call}; }});")
        return JSOutput("\n".join(parts) + "\n", self.bundler.head() if self.bundler else "")

    def struct_classes(self):
        out = []
        for name in self.c.structs:
            fields = [f for f, _ in self.c.struct_fields(TCon(name, [TVar() for _ in self.c.tparams_of.get(name, [])]))]
            params = ", ".join(f"a{i}" for i in range(len(fields)))
            body = " ".join(f"this[{json.dumps(f)}] = a{i};" for i, f in enumerate(fields))
            out.append(f"class S_{name} {{ constructor({params}) {{ {body} }} }}\nS_{name}.$name = {json.dumps(name)}; "
                       f"$structs[{json.dumps(name)}] = S_{name};")
        return out

    def fn_is_io(self, name):
        return "io" in self.c.fns[name].effects

    def trait_target(self, e):
        """The impl function (and its type arguments) that a trait method call runs, for this instance."""
        trait, method = e.trait_ref
        name, targs = self.c.impl_call(trait, method, self.T(e.inst[0]))
        return name, [self.T(t) for t in targs]

    def request_fn(self, name, targs):
        key = name + "|" + ",".join(show(t) for t in targs)
        if key not in self.instances:
            jsname = f"f_{name}" if not targs else f"f_{name}${len(self.instances)}"
            self.instances[key] = jsname
            self.work.append((name, targs, jsname))
        return self.instances[key]

    def gen_fn(self, d, targs, jsname):
        self.sub = dict(zip(d.tparams, targs))
        sig = self.c.fns[d.name]
        is_async = "io" in sig.effects
        self.ctx = Ctx(is_async)
        params = []
        for p, t in sig.params:
            self.ctx.locals[p] = self.T(t)
            params.append(f"v_{p}")
        lines = []
        self.block_into(d.body, lines)
        body = "\n".join("  " + l for l in lines)
        self.defs.append(f"{'async ' if is_async else ''}function {jsname}({', '.join(params)}) {{\n{body}\n}}")

    # ------------------------------------------------------------------ statements
    def block_into(self, b, lines):
        saved = self.out
        self.out = lines
        for s in b.stmts:
            self.stmt(s)
        self.out = saved

    def block(self, b):
        lines = []
        self.block_into(b, lines)
        return lines

    def emit(self, line):
        self.out.append(line)

    def emit_block(self, head, lines, tail="}"):
        self.out.append(head)
        self.out.extend("  " + l for l in lines)
        self.out.append(tail)

    def stmt(self, s):
        k = s.kind
        if k == "Let":
            code = self.ex(s.value)
            if s.name == "_":
                self.emit(f"{code};")
            else:
                t = self.T(s.ty)
                self.ctx.locals[s.name] = t
                self.emit(f"{'let' if s.mutable else 'const'} v_{s.name} = {code};")
                if s.mutable and self.kind(t) == "list":
                    self.ctx.owned.add(s.name)
                    self.emit(f"let o_{s.name} = {'true' if is_fresh(s.value, self.fns) else 'false'};")
        elif k == "Assign":
            steps = self_update(s, self.fns) if s.name in self.ctx.owned else None
            if steps is not None:
                self.owned_update(s, steps)
                return
            self.emit(f"v_{s.name} = {self.ex(s.value)};")
            if s.name in self.ctx.owned:
                self.emit(f"o_{s.name} = {'true' if is_fresh(s.value, self.fns) else 'false'};")
        elif k == "ExprStmt":
            code = self.ex(s.expr)
            if not TRIVIAL.match(code):
                self.emit(f"{code};")
        elif k == "If":
            cond = self.ex(s.cond)
            then = self.block(s.then)
            if s.els is None:
                self.emit_block(f"if ({cond}) {{", then)
            else:
                els = self.block(s.els) if s.els.kind == "Block" else self.sub_stmts(s.els)
                self.emit_block(f"if ({cond}) {{", then, "} else {")
                self.out.extend("  " + l for l in els)
                self.emit("}")
        elif k == "While":
            pre = []
            saved = self.out
            self.out = pre
            cond = self.ex(s.cond)
            self.out = saved
            body = self.block(s.body)
            if pre:
                self.emit_block("while (true) {", pre + [f"if (!({cond})) break;"] + body)
            else:
                self.emit_block(f"while ({cond}) {{", body)
        elif k == "For":
            it = s.iter
            elem = self.T(prune(self.T(it.ty)).args[0])
            self.ctx.locals[s.var] = elem
            if it.kind == "Call" and it.callee.kind == "Name" and it.callee.name == "range" \
                    and getattr(it, "target_kind", "") == "fn" and "range" not in self.fns and not it.named:
                a, b = self.seq([it.args[0], it.args[1]])
                end = self.tmp()
                self.emit(f"const {end} = {b};")
                self.emit_block(f"for (let v_{s.var} = {a}; v_{s.var} < {end}; v_{s.var}++) {{", self.block(s.body))
            else:
                l, i = self.tmp(), self.tmp()
                self.emit(f"const {l} = {self.ex(it)};")
                self.emit_block(f"for (let {i} = 0; {i} < {l}.n; {i}++) {{",
                                [f"const v_{s.var} = $d({l})[{i}];"] + self.block(s.body))
        elif k == "Return":
            if s.value is None:
                self.emit("return;")
            else:
                self.emit(f"return {self.ex(s.value)};")
        elif k == "Break":
            self.emit("break;")
        elif k == "Continue":
            self.emit("continue;")
        elif k == "Assert":
            c = s.cond
            hint = "null"
            if c.kind == "Binary" and c.op in ("==", "!=", "<", "<=", ">", ">="):
                lt = self.T(c.left.ty)
                a, b = self.seq([c.left, c.right])
                ta, tb = self.tmp(), self.tmp()
                self.emit(f"const {ta} = {a}, {tb} = {b};")
                cond = self.binary_op(c.op, ta, tb, lt, c)
                hint = f"\"left side = \" + {self.show(ta, lt, True)} + \", right side = \" + {self.show(tb, lt, True)}"
            else:
                cond = self.ex(c)
            msg = '"assertion failed"'
            if s.msg is not None:
                pre = []
                saved = self.out
                self.out = pre
                m = self.ex(s.msg)
                self.out = saved
                self.emit_block(f"if (!({cond})) {{", pre + [f'$fail("E404", {s.line}, {s.col}, "assertion failed: " + {m}, {hint});'])
                return
            self.emit(f'if (!({cond})) $fail("E404", {s.line}, {s.col}, {msg}, {hint});')
        else:
            self.unsupported(s, f"statement {k}")

    def sub_stmts(self, node):
        lines = []
        saved = self.out
        self.out = lines
        self.stmt(node)
        self.out = saved
        return lines

    # ------------------------------------------------------------------ expressions
    def ex(self, e):
        m = getattr(self, "e_" + e.kind, None)
        if m is None:
            self.unsupported(e, f"expression {e.kind}")
        return m(e)

    def seq(self, nodes):
        """Compile several operands, keeping left-to-right evaluation when a later one hoists statements."""
        codes = []
        for n in nodes:
            mark = len(self.out)
            code = self.ex(n)
            if len(self.out) > mark:
                spills = []
                for j, cj in enumerate(codes):
                    if not TRIVIAL.match(cj):
                        t = self.tmp()
                        spills.append(f"const {t} = {cj};")
                        codes[j] = t
                self.out[mark:mark] = spills
            codes.append(code)
        return codes

    def sub_expr(self, e):
        """Compile e into its own statement list; returns (statements, code)."""
        lines = []
        saved = self.out
        self.out = lines
        code = self.ex(e)
        self.out = saved
        return lines, code

    def e_Int(self, e):
        if not (-(2 ** 63) <= e.value < 2 ** 63):
            raise KlarError("E408", f"integer literal {e.value} does not fit in 64 bits", e.line, e.col,
                            "compiled Klar ints are 64-bit; use `klar run` for unlimited integers", self.file)
        return str(e.value) if abs(e.value) <= MAX_SAFE else f"{e.value}n"

    def e_Float(self, e):
        r = repr(e.value)
        if r == "inf":
            return "Infinity"
        if r == "nan":
            return "NaN"
        return f"({r})" if r.startswith("-") else r

    def e_Bool(self, e):
        return "true" if e.value else "false"

    def e_Unit(self, e):
        return "undefined"

    def e_Str(self, e):
        return json.dumps(e.value)

    def e_Interp(self, e):
        exprs = [p for p in e.parts if not isinstance(p, str)]
        codes = iter(self.seq(exprs))
        out = []
        for p in e.parts:
            if isinstance(p, str):
                if p:
                    out.append(json.dumps(p))
            else:
                out.append(self.to_str(next(codes), self.T(p.ty)))
        if not out:
            return '""'
        if len(out) == 1:
            return out[0] if out[0].startswith('"') else f"(\"\" + {out[0]})"
        return "(" + ("" if out[0].startswith('"') else '"" + ') + " + ".join(out) + ")"

    def owned_update(self, s, steps):
        """`v = set_at(v, i, x)` / `v = push(v, x)` (or a nest of them) on an owned list var: evaluate every index
        and value first, then write straight into the array while the flag is set."""
        v, o = f"v_{s.name}", f"o_{s.name}"
        vals = []
        for name, call in steps:
            if name == "set_at":
                i, x = self.tmp(), self.tmp()
                ci, cx = self.seq([call.args[1], call.args[2]])
                self.emit(f"const {i} = {ci};")
                self.emit(f"const {x} = {cx};")
                vals.append((name, call, i, x))
            else:
                x = self.tmp()
                self.emit(f"const {x} = {self.ex(call.args[1])};")
                vals.append((name, call, None, x))
        for name, call, i, x in vals:
            L = f"{call.line}, {call.col}"
            if name == "set_at":
                self.emit(f"if ({o}) {{ if ({i} < 0 || {i} >= {v}.n) $set_at({v}, {i}, {x}, {L}); {v}.b[{i}] = {x}; }}")
                self.emit(f"else {{ {v} = $set_at_fresh({v}, {i}, {x}, {L}); {o} = true; }}")
            else:
                self.emit(f"if ({o}) {{ {v}.b.push({x}); {v}.n++; }} else {v} = $push({v}, {x});")

    def e_Name(self, e):
        name = e.name
        if name in self.ctx.locals:
            if name in self.ctx.owned and not getattr(e, "safe_read", False):
                return f"(o_{name} = false, v_{name})"
            return f"v_{name}"
        if name in self.c.variants:
            return self.nullary(name)
        if name in self.fns:
            targs = [self.T(x) for x in getattr(e, "inst", [])]
            return self.request_fn(name, targs)
        if getattr(e, "trait_ref", None):
            return self.request_fn(*self.trait_target(e))
        if name in self.externs:
            return self.extern_fn(self.externs[name], e)
        return self.builtin_value(name, self.T(e.ty), e)

    def nullary(self, name):
        if name == "None":
            return "$None"
        key = ("nullary", name)
        if key not in self.helpers:
            self.helpers[key] = f"N_{name}"
            self.consts.append(f"const N_{name} = new KE({json.dumps(name)}, []);")
        return self.helpers[key]

    def builtin_value(self, name, t, e):
        if not isinstance(t, TFn):
            self.unsupported(e, f"`{name}` as a value")
        ps = [f"a{i}" for i in range(len(t.params))]
        saved, self.out = self.out, []
        body = self.builtin(name, ps, list(t.params), t.ret, e)
        self.out = saved
        return f"(({', '.join(ps)}) => {body})"

    def e_ListLit(self, e):
        return "$L([" + ", ".join(self.seq(e.items)) + "])"

    def e_MapLit(self, e):
        flat = self.seq([x for kv in e.entries for x in kv])
        pairs = [f"[{flat[i]}, {flat[i + 1]}]" for i in range(0, len(flat), 2)]
        return "$M([" + ", ".join(pairs) + "])"

    def e_Unary(self, e):
        x = self.ex(e.operand)
        if e.op == "not":
            return f"(!{x})"
        if self.kind(self.T(e.ty)) == "int":
            return f"$neg({x}, {e.line}, {e.col})"
        return f"(-{x})"

    def e_Binary(self, e):
        op = e.op
        if op in ("and", "or"):
            a = self.ex(e.left)
            pre, b = self.sub_expr(e.right)
            if not pre:
                return f"({a} {'&&' if op == 'and' else '||'} {b})"
            t = self.tmp()
            self.emit(f"let {t} = {a};")
            self.emit_block(f"if ({'' if op == 'and' else '!'}{t}) {{", pre + [f"{t} = {b};"])
            return t
        lt = self.T(e.left.ty)
        a, b = self.seq([e.left, e.right])
        return self.binary_op(op, a, b, lt, e)

    def binary_op(self, op, a, b, lt, e):
        k = self.kind(lt)
        if op in ("==", "!="):
            eq = self.eq(a, b, lt)
            return eq if op == "==" else f"(!{eq})"
        if op in ("<", "<=", ">", ">="):
            return f"({a} {op} {b})"
        if k == "float":
            if op == "%":
                return f"$frem({a}, {b}, {e.line}, {e.col})"
            return f"({a} {op} {b})"
        if k == "str":
            return f"({a} + {b})"
        if k == "list":
            return f"$concat({a}, {b})"
        fn = {"+": "$add", "-": "$sub", "*": "$mul", "/": "$div", "%": "$rem"}[op]
        return f"{fn}({a}, {b}, {e.line}, {e.col})"

    def e_IfExpr(self, e):
        cond = self.ex(e.cond)
        pa, a = self.sub_expr(e.then)
        pb, b = self.sub_expr(e.els)
        if not pa and not pb:
            return f"({cond} ? {a} : {b})"
        t = self.tmp()
        self.emit(f"let {t};")
        self.emit_block(f"if ({cond}) {{", pa + [f"{t} = {a};"], "} else {")
        self.out.extend("  " + l for l in pb + [f"{t} = {b};"])
        self.emit("}")
        return t

    def e_MethodCall(self, e):
        return self.ex(e.desugared)

    def e_Field(self, e):
        return f"{self.ex(e.target)}[{json.dumps(e.name)}]" if not e.name.isidentifier() else f"{self.ex(e.target)}.{e.name}"

    def e_Index(self, e):
        if e.target.kind == "Name":
            e.target.safe_read = True
        t = self.T(e.target.ty)
        k = self.kind(t)
        tgt, idx = self.seq([e.target, e.index])
        L = f"{e.line}, {e.col}"
        if k == "list":
            if TRIVIAL.match(tgt) and TRIVIAL.match(idx):
                # Inline at each site so the engine's property caches stay monomorphic (a shared $lget is
                # megamorphic in any real program and runs several times slower).
                return (f"(({idx} < 0 || {idx} >= {tgt}.n) ? $lget({tgt}, {idx}, {L}) : "
                        f"{tgt}.v === ({tgt}.b.$v | 0) ? {tgt}.b[{idx}] : $lsync({tgt})[{idx}])")
            return f"$lget({tgt}, {idx}, {L})"
        if k == "str":
            return f"$sidx({tgt}, {idx}, {L})"
        return f"$mindex({tgt}, {idx}, {L})"

    def e_Try(self, e):
        t = self.tmp()
        self.emit(f"const {t} = {self.ex(e.value)};")
        wrap = getattr(e, "wrap", None)
        fail = f"$Err(new KE({json.dumps(wrap)}, [{t}.f[0]]))" if wrap else t
        self.emit(f'if ({t}.t === "None" || {t}.t === "Err") return {fail};')
        return f"{t}.f[0]"

    def e_With(self, e):
        t = self.T(e.ty)
        fields = [f for f, _ in self.c.struct_fields(t)]
        codes = self.seq([e.target] + [v for _, v, _ in e.updates])
        o = self.tmp()
        self.emit(f"const {o} = {codes[0]};")
        upd = {f: c for (f, _, _), c in zip(e.updates, codes[1:])}
        args = [upd.get(f, f"{o}[{json.dumps(f)}]") for f in fields]
        return f"new S_{t.name}({', '.join(args)})"

    def e_Lambda(self, e):
        t = self.T(e.ty)
        outer = self.ctx
        self.ctx = Ctx(t.io)
        self.ctx.locals = dict(outer.locals)
        for (p, _), pt in zip(e.params, t.params):
            self.ctx.locals[p] = pt
        params = ", ".join(f"v_{p}" for p, _ in e.params)
        if e.block:
            body = self.block(e.body)
            code = "{\n" + "\n".join("  " + l for l in body) + "\n}"
        else:
            pre, c = self.sub_expr(e.body)
            if pre:
                code = "{\n" + "\n".join("  " + l for l in pre + [f"return {c};"]) + "\n}"
            else:
                code = f"({c})"
        self.ctx = outer
        return f"({'async ' if t.io else ''}({params}) => {code})"

    def ordered_args(self, e, names):
        if e.named:
            by = {n: v for n, v, _ in e.named}
            return [by[n] for n in names]
        return list(e.args)

    def e_Call(self, e):
        tk = getattr(e, "target_kind", "value")
        if tk == "struct":
            t = self.T(e.ty)
            fields = self.c.struct_fields(t)
            codes = self.seq(self.ordered_args(e, [f for f, _ in fields]))
            return f"new S_{t.name}({', '.join(codes)})"
        if tk == "variant":
            name = e.callee.name
            info = self.c.variants[name]
            codes = self.seq(self.ordered_args(e, [f for f, _ in info.fields]))
            if name in ("Some", "Ok", "Err"):
                return f"${name}({codes[0]})"
            return f"new KE({json.dumps(name)}, [{', '.join(codes)}])"
        if tk == "extern":
            d = self.externs[e.callee.name]
            if d.target.startswith("klar:json."):
                (code,) = self.seq(self.ordered_args(e, [p for p, _ in self.c.fns[d.name].params]))
                op = d.target.split(".", 1)[1]
                return f"$json.{op}({code}, {self.desc(self.T(e.inst[0]))}, {e.line}, {e.col})"
            if d.target.startswith("klar:sql."):
                (code,) = self.seq(self.ordered_args(e, [p for p, _ in self.c.fns[d.name].params]))
                op = d.target.split(".", 1)[1]
                return f"$sql.{op}({code}, {self.desc(self.T(e.inst[0]))})"
            if d.target.startswith(("klar:task.", "klar:chan.")):
                codes = self.seq(self.ordered_args(e, [p for p, _ in self.c.fns[d.name].params]))
                op = d.target.split(".", 1)[1]
                op = "new_" if op == "new" else op
                return f"(await $task.{op}({', '.join(codes + [str(e.line), str(e.col)])}))"
            fn = self.extern_fn(d, e)
            codes = self.seq(self.ordered_args(e, [p for p, _ in self.c.fns[d.name].params]))
            if d.target.startswith("js:"):
                codes += [str(e.line), str(e.col)]
            call = f"{fn}({', '.join(codes)})"
            return f"(await {call})" if "io" in self.c.fns[d.name].effects else call
        if tk == "trait":
            sig = self.c.fns[e.callee.name]
            name, targs = self.trait_target(e)
            inst = self.request_fn(name, targs)
            call = f"{inst}({', '.join(self.seq(self.ordered_args(e, [p for p, _ in sig.params])))})"
            return f"(await {call})" if "io" in sig.effects else call
        if tk == "fn":
            name = e.callee.name
            sig = self.c.fns[name]
            args = self.ordered_args(e, [p for p, _ in sig.params])
            if name in self.fns:
                targs = [self.T(x) for x in getattr(e, "inst", [])]
                inst = self.request_fn(name, targs)
                call = f"{inst}({', '.join(self.seq(args))})"
                return f"(await {call})" if "io" in sig.effects else call
            if name == "len" and args and args[0].kind == "Name":
                args[0].safe_read = True
            codes = self.seq(args)
            return self.builtin(name, codes, [self.T(a.ty) for a in args], self.T(e.ty), e)
        ct = self.T(e.callee.ty)
        codes = self.seq([e.callee] + list(e.args))
        call = f"{codes[0]}({', '.join(codes[1:])})"
        return f"(await {call})" if ct.io else call

    # ------------------------------------------------------------------ externs
    def extern_fn(self, d, node):
        key = ("extern", d.name)
        if key in self.helpers:
            return self.helpers[key]
        target = d.target
        sig = self.c.fns[d.name]
        name = f"x_{d.name}"
        if target.startswith("js:"):
            self.helpers[key] = name
            self.js_extern(d, sig, name, node)
            return name
        if target.startswith("wasm:"):
            return self.wasm_extern(d, node, key, name, sig)
        if self.target == "web" and target.startswith(NODE_ONLY):
            self.unsupported(node, f"`{d.name}` (it needs a server: files, processes and databases are not available in "
                                   f"the browser)", "use it in a backend built with `--target node`, and call that backend "
                                                     "from the browser with ui_get / ui_send")
        if not (target.startswith("klar.stdpy.") or target.startswith("klar:")):
            self.unsupported(node, f"extern `{d.name}` (Python interop)",
                             'Python libraries need `klar run`; for JavaScript libraries declare the extern with a '
                             '"js:module.function" target')
        self.helpers[key] = f"$std[{json.dumps(target)}]"
        return self.helpers[key]

    def js_module(self, module, node):
        """Alias of an imported JavaScript module (npm package, node: module or local .js/.mjs file)."""
        if module in self.js_modules:
            return self.js_modules[module]
        from . import npm
        src_dir = os.path.dirname(os.path.abspath(self.file)) if self.file != "<input>" else os.getcwd()
        try:
            if self.target == "web":
                if module.startswith("node:"):
                    self.unsupported(node, f"`{module}` (a Node built-in; the browser does not have it)",
                                     "use it in a backend built with `--target node`")
                if module.startswith(("http:", "https:", "data:")):
                    spec = module
                else:
                    self.bundler = self.bundler or npm.WebBundler()
                    spec = self.bundler.name(npm.resolve_module(module, src_dir, npm.WEB_CONDITIONS))
            else:
                out_dir = os.path.dirname(os.path.abspath(self.out_path)) if self.out_path else src_dir
                spec = npm.node_specifier(module, src_dir, out_dir)
        except npm.NpmError as ex:
            raise KlarError("E601", ex.msg, node.line, node.col, ex.hint, self.file)
        alias = f"$js{len(self.js_modules)}"
        self.js_modules[module] = alias
        self.imports.append(f"import * as {alias} from {json.dumps(spec)};")
        return alias

    def js_extern(self, d, sig, name, node):
        """Wrapper for `extern "js:..."`: converts arguments by their Klar types, calls, converts the result back
        (null/undefined only as Option, a thrown error only as Err), and enforces the declared effect."""
        from . import npm
        op, module, path = npm.parse_target(d.target[3:])
        n = len(sig.params)
        ps = [f"a{i}" for i in range(n)]
        conv = [f"$to_js({p}, {self.desc(self._subst(t, {}))})" for p, (_, t) in zip(ps, sig.params)]
        member = lambda base, names: base + "".join(f".{x}" if x.isidentifier() else f"[{json.dumps(x)}]" for x in names)
        form = f'"js:{op + " " if op != "call" else ""}.name"'
        if module is None and path and path[0] != "globalThis":
            if n == 0:
                self.unsupported(node, f"`{d.name}`: a member target needs the object as the first parameter",
                                 f"extern {form} fn {d.name}(obj: SomeType, ...) -> ...")
            ref, conv = member(conv[0], path), conv[1:]
        elif module is None:
            ref = member("", path)[1:]
        else:
            src_dir = os.path.dirname(os.path.abspath(self.file)) if self.file != "<input>" else os.getcwd()
            mod, attrs = npm.split_module(module, src_dir)
            ref = member(self.js_module(mod, node), attrs)
        if op == "get":
            if conv:
                self.unsupported(node, f"`{d.name}`: a `get` target takes only the object", f'extern "js:get .size" fn size(obj: T) -> int')
            raw = ref
        elif op == "set":
            if len(conv) != 1:
                self.unsupported(node, f"`{d.name}`: a `set` target takes the object and the new value",
                                 f'extern "js:set .value" fn set_value(obj: T, v: str) uses io')
            raw = f"({ref} = {conv[0]}, undefined)"
        else:
            raw = f"{'new ' if op == 'new' else ''}{ref}({', '.join(conv)})"
        is_io = "io" in sig.effects
        rt = self._subst(sig.ret, {})
        what = json.dumps(f"extern `{d.name}`")
        where = "$l, $c"
        if is_io:
            got = f"await {raw}"
        else:
            got = f"$js_sync({raw}, {what}, {where})"
        if isinstance(rt, TCon) and rt.name == "Result":
            inner = self.desc(rt.args[0])
            body = (f"let r; try {{ r = {got}; }} catch (e) {{ if (e instanceof KlarError) throw e; return $Err($js_err(e)); }} "
                    f"return $Ok($from_js(r, {inner}, {what}, {where}));")
        else:
            body = (f"let r; try {{ r = {got}; }} catch (e) {{ if (e instanceof KlarError) throw e; "
                    f"$fail(\"E407\", {where}, {what} + \" threw \" + $js_err(e) + \" (declare the result as Result[T, str] to handle it)\"); }} "
                    f"return $from_js(r, {self.desc(rt)}, {what}, {where});")
        self.defs.append(f"{'async ' if is_io else ''}function {name}({', '.join(ps + ['$l = 0', '$c = 0'])}) {{ {body} }}")

    def wasm_extern(self, d, node, key, name, sig):
        """extern "wasm:module.function": compile module.klar to WebAssembly once, call the function through it."""
        from .compiler import compile_to_c
        from .wasm import parse_target, load_module, check_extern, c_to_wasm, b64
        path, fn = parse_target(d.target, d.module)
        if path not in self.wasm:
            if not os.path.exists(path):
                raise KlarError("E201", f"extern `{d.name}`: WebAssembly module {path} does not exist", d.line, d.col,
                                'the target is "wasm:module.function", where module.klar sits next to this file', d.module)
            prog, errors = load_module(path)
            if errors:
                raise errors[0]
            fns = []
            for x in self.externs.values():      # every function this app calls in that module: one .wasm
                if x.target.startswith("wasm:") and parse_target(x.target, x.module)[0] == path:
                    check_extern(x, self.c, prog, parse_target(x.target, x.module)[1], path)
                    fns.append(parse_target(x.target, x.module)[1])
            info = {}
            c_src = compile_to_c(prog, path, info, "wasm", sorted(set(fns)))
            var = f"$wasm{len(self.wasm)}"
            self.consts.append(f"const {var} = await $wasm_load($wasm_b64(\"{b64(c_to_wasm(c_src, where=path))}\"));")
            self.wasm[path] = (var, info["exports"])
        var, exports = self.wasm[path]
        kinds, rkind = exports[fn]
        ptypes = [self._subst(t, {}) for _, t in sig.params]
        rt = self._subst(sig.ret, {})
        ps = [f"a{i}" for i in range(len(ptypes))]
        descs = ", ".join(self.desc(t) if k == "json" else "null" for t, k in zip(ptypes, kinds))
        rdesc = self.desc(rt) if rkind == "json" else "null"
        self.helpers[key] = name
        self.defs.append(f"function {name}({', '.join(ps)}) {{ return $wasm_call({var}, {json.dumps(fn)}, "
                         f"{json.dumps(kinds)}, [{descs}], {json.dumps(rkind)}, {rdesc}, [{', '.join(ps)}]); }}")
        return name

    # ------------------------------------------------------------------ match
    def e_Match(self, e):
        st = self.T(e.scrutinee.ty)
        m, r = self.tmp(), self.tmp()
        self.emit(f"const {m} = {self.ex(e.scrutinee)};")
        self.emit(f"let {r};")
        first = True
        for arm in e.arms:
            conds, binds = [], []
            self.pattern(arm.pattern, m, st, conds, binds)
            cond = " && ".join(conds) if conds else "true"
            lines = list(binds)
            saved = self.out
            self.out = lines
            if arm.body.kind == "Block":
                self.block_into(arm.body, lines)
            else:
                code = self.ex(arm.body)
                lines.append(f"{r} = {code};")
            self.out = saved
            head = ("if" if first else "} else if") + f" ({cond}) {{"
            first = False
            self.out.append(head)
            self.out.extend("  " + l for l in lines)
        self.out.append(f'}} else $fail("E306", {e.line}, {e.col}, "no match arm matched");')
        return r

    def pattern(self, p, code, t, conds, binds):
        k = p.kind
        if k == "PWild":
            return
        if k == "PBind":
            self.ctx.locals[p.name] = t
            binds.append(f"const v_{p.name} = {code};")
            return
        if k == "PLit":
            v = p.value
            if isinstance(v, bool):
                conds.append(f"{code} === {'true' if v else 'false'}")
            elif isinstance(v, str):
                conds.append(f"{code} === {json.dumps(v)}")
            else:
                conds.append(f"{code} === {repr(v)}")
            return
        if k == "PTuple":
            for i, (sp, at) in enumerate(zip(p.subs, t.args)):
                self.pattern(sp, f"{code}[\"{i}\"]", self.T(at), conds, binds)
            return
        info = self.c.variants[p.name]
        conds.append(f"{code}.t === {json.dumps(p.name)}")
        mapping = dict(zip(info.tparams, t.args))
        for i, (sp, (_, ft)) in enumerate(zip(p.subs, info.fields)):
            self.pattern(sp, f"{code}.f[{i}]", self.T(substitute(ft, mapping)), conds, binds)

    # ------------------------------------------------------------------ type-directed helpers
    def eq(self, a, b, t):
        if self.kind(t) in ("int", "float", "bool", "str", "unit", "fn", "extern"):
            return f"({a} === {b})"
        return f"$eq({a}, {b})"

    def desc(self, t):
        """Name of a runtime type descriptor for t (used by display and JavaScript interop)."""
        t = self.T(t)
        key = ("desc", show(t))
        if key in self.helpers:
            return self.helpers[key]
        k = self.kind(t)
        simple = {"int": "i", "float": "f", "bool": "b", "unit": "u", "never": "u", "str": "s"}
        name = f"D{len(self.helpers)}"
        self.helpers[key] = name
        if k in simple:
            self.desc_decls.append(f"const {name} = {{k: \"{simple[k]}\"}};")
        elif k == "extern":
            self.desc_decls.append(f"const {name} = {{k: \"x\", n: {json.dumps(t.name)}}};")
        elif k == "fn":
            t = prune(t)
            self.desc_decls.append(f"const {name} = {{k: \"fn\", io: {'true' if t.io else 'false'}}};")
            self.desc_fills.append(f"{name}.ps = [{', '.join(self.desc(p) for p in t.params)}]; {name}.r = {self.desc(t.ret)};")
        elif k == "list":
            self.desc_decls.append(f"const {name} = {{k: \"l\"}};")
            self.desc_fills.append(f"{name}.e = {self.desc(t.args[0])};")
        elif k == "map":
            self.desc_decls.append(f"const {name} = {{k: \"m\"}};")
            self.desc_fills.append(f"{name}.kk = {self.desc(t.args[0])}; {name}.v = {self.desc(t.args[1])};")
        elif k == "struct":
            kind = "tu" if is_tuple_name(t.name) else "st"
            self.desc_decls.append(f"const {name} = {{k: \"{kind}\", n: {json.dumps(t.name)}}};")
            fs = ", ".join(f"[{json.dumps(f)}, {self.desc(ft)}]" for f, ft in self.c.struct_fields(t))
            self.desc_fills.append(f"{name}.fs = [{fs}];")
        else:
            self.desc_decls.append(f"const {name} = {{k: \"en\", n: {json.dumps(t.name)}}};")
            vs = []
            for vname, ftypes in self.variant_types(t):
                labels = [f for f, _ in self.c.variants[vname].fields]
                items = ", ".join(f"[{json.dumps('' if vname in ('Some', 'Ok', 'Err') else lab)}, {self.desc(ft)}]"
                                  for lab, ft in zip(labels, ftypes))
                vs.append(f"{json.dumps(vname)}: [{items}]")
            self.desc_fills.append(f"{name}.vs = {{{', '.join(vs)}}};")
        return name

    def variant_types(self, t):
        if t.name == "Option":
            return [("Some", [t.args[0]]), ("None", [])]
        if t.name == "Result":
            return [("Ok", [t.args[0]]), ("Err", [t.args[1]])]
        mapping = dict(zip(self.c.tparams_of.get(t.name, []), t.args))
        return [(vn, [self.T(substitute(ft, mapping)) for _, ft in self.c.variants[vn].fields]) for vn in self.c.enums[t.name]]

    def show(self, code, t, nested):
        k = self.kind(t)
        if k == "str":
            return f"$quote({code})" if nested else code
        if k == "int":
            return f"String({code})"
        if k == "float":
            return f"$fmt_float({code})"
        if k == "bool":
            return f"({code} ? \"true\" : \"false\")"
        return f"$show({code}, {self.desc(t)}, {'true' if nested else 'false'})"

    def to_str(self, code, t):
        return self.show(code, t, False)

    # ------------------------------------------------------------------ built-ins
    def builtin(self, name, a, ts, rt, e):
        L = f"{e.line}, {e.col}"
        k0 = self.kind(ts[0]) if ts else None
        if name == "print":
            return f"$print({self.to_str(a[0], ts[0])})"
        if name == "str":
            return self.to_str(a[0], ts[0])
        if name == "panic":
            return f"$fail(\"E403\", {L}, \"panic: \" + {a[0]})"
        if name == "read_line":
            return "$read_line()"
        if name == "read_file":
            return f"$read_file({a[0]})"
        if name == "write_file":
            return f"$write_file({a[0]}, {a[1]})"
        if name == "args":
            return "$args()"
        if name == "len":
            if k0 == "str":
                return f"$slen({a[0]})"
            if k0 == "map":
                return f"{a[0]}.size"
            return f"{a[0]}.n"
        if name == "get":
            return f"$lgetopt({a[0]}, {a[1]})" if k0 == "list" else f"$mget({a[0]}, {a[1]})"
        if name == "slice":
            return f"$sslice({a[0]}, {a[1]}, {a[2]})" if k0 == "str" else f"$lslice({a[0]}, {a[1]}, {a[2]})"
        if name == "contains":
            return f"{a[0]}.includes({a[1]})" if k0 == "str" else f"$contains({a[0]}, {a[1]})"
        if name == "reverse":
            return f"$str_reverse({a[0]})" if k0 == "str" else f"$lreverse({a[0]})"
        if name == "sum":
            return f"$sum_float({a[0]})" if self.kind(ts[0].args[0]) == "float" else f"$sum_int({a[0]}, {L})"
        if name == "set_at":
            return f"$set_at({a[0]}, {a[1]}, {a[2]}, {L})"
        if name == "unwrap":
            t = prune(ts[0])
            show_err = f"(x => {self.show('x', t.args[1], True)})" if t.name == "Result" else "null"
            return f"$unwrap({a[0]}, {L}, {show_err})"
        if name == "ok_or":
            return f"$ok_or({a[0]}, {a[1]})"
        if name in ("is_some", "is_none", "is_ok", "is_err"):
            tag = {"is_some": "Some", "is_none": "None", "is_ok": "Ok", "is_err": "Err"}[name]
            return f"({a[0]}.t === \"{tag}\")"
        if name == "to_float":
            return f"$to_float({a[0]})"
        if name == "abs":
            return f"Math.abs({a[0]})" if k0 == "float" else f"$iabs({a[0]}, {L})"
        if name in ("sort", "sort_by"):
            kt = ts[0].args[0] if name == "sort" else prune(ts[1]).ret
            if self.kind(kt) not in ("int", "float", "str", "bool"):
                self.unsupported(e, f"sorting by `{show(kt)}` keys")
        simple = {
            "push": "$push", "index_of": "$index_of", "range": "$range", "map": "$lmap", "filter": "$lfilter",
            "any": "$lany", "all": "$lall", "find": "$lfind", "fold": "$lfold", "sort": "$sort", "sort_by": "$sort_by",
            "join": "$join", "split": "$split", "chars": "$chars", "trim": "$trim", "replace": "$replace",
            "repeat": "$repeat", "parse_int": "$parse_int", "parse_float": "$parse_float", "floor": "$floor",
            "round": "$round", "sqrt": "Math.sqrt", "keys": "$mkeys", "values": "$mvalues", "entries": "$mentries", "has": "$mhas",
            "put": "$mput", "remove": "$mremove", "unwrap_or": "$unwrap_or", "min": "$min", "max": "$max",
        }
        if name in simple:
            return f"{simple[name]}({', '.join(a)})"
        methods = {"upper": "toUpperCase", "lower": "toLowerCase"}
        if name in methods:
            return f"{a[0]}.{methods[name]}()"
        if name == "starts_with":
            return f"{a[0]}.startsWith({a[1]})"
        if name == "ends_with":
            return f"{a[0]}.endsWith({a[1]})"
        self.unsupported(e, f"built-in `{name}`")


class JSOutput(str):
    """The compiled program; `head` holds HTML (an import map of embedded npm modules) that a web page must
    include before the program's script."""
    def __new__(cls, text, head=""):
        o = super().__new__(cls, text)
        o.head = head
        return o


def compile_to_js(prog, file, target="node", out_path=None):
    return JSCompiler(prog, file, target, out_path).compile()
