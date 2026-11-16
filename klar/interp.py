"""Tree-walking evaluator for checked Klar programs."""

import math
import os
import re
import sys
import threading
from dataclasses import dataclass

from .errors import KlarError
from .ownership import self_update, str_append, LIST_UPDATES, MAP_UPDATES
from .syntax import Node, MAX_TUPLE, tuple_name, is_tuple_name
from .interop import Converter, Opaque, PyFn, InteropError, resolve_target
from .types import TCon, prune, resolve, substitute

# What parse_float accepts after trimming, in every backend.
FLOAT_TEXT = re.compile(r"[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?|[+-]?(?i:inf|infinity|nan)")


@dataclass(frozen=True)
class StructVal:
    name: str
    fields: tuple       # tuple of (name, value)

    def get(self, f):
        for k, v in self.fields:
            if k == f:
                return v
        raise KeyError(f)


@dataclass(frozen=True)
class EnumVal:
    variant: str
    values: tuple
    names: tuple = ()

    def __eq__(self, other):
        return isinstance(other, EnumVal) and self.variant == other.variant and self.values == other.values

    def __hash__(self):
        return hash((self.variant, self.values))


class Closure:
    def __init__(self, params, body, block, env, name="<anonymous fn>"):
        self.params = params
        self.body = body
        self.block = block
        self.env = env
        self.name = name


class FnRef:
    def __init__(self, name):
        self.name = name


class FrozenMap(dict):
    def __hash__(self):
        return hash(tuple(sorted(self.items(), key=repr)))


def _is_str(t):
    t = prune(t) if t is not None else None
    return isinstance(t, TCon) and t.name == "str"


class OwnedStr(list):
    """The pieces of a string held only by one var and built with `v = v + t` (see Interpreter.update_in_place);
    any read of the var joins them into an ordinary str first."""


class OwnedMap(dict):
    """A map held only by one var and updated in place (see Interpreter.update_in_place). It never leaves that
    var: any read other than m[k], len(m), get(m, k) and has(m, k) turns it into a FrozenMap first."""


NONE = EnumVal("None", ())


def some(v):
    return EnumVal("Some", (v,), ("value",))


def ok(v):
    return EnumVal("Ok", (v,), ("value",))


def errv(v):
    return EnumVal("Err", (v,), ("error",))


class ReturnSig(Exception):
    def __init__(self, value):
        self.value = value


class BreakSig(Exception):
    pass


class ContinueSig(Exception):
    pass


def fmt(v, nested=False):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isinf(v) or math.isnan(v):
            return {math.inf: "inf", -math.inf: "-inf"}.get(v, "nan")
        r = repr(v)
        return r
    if isinstance(v, str):
        if nested:
            return '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
        return v
    if v is None:
        return "()"
    if isinstance(v, tuple):
        return "[" + ", ".join(fmt(x, True) for x in v) + "]"
    if isinstance(v, dict):
        if not v:
            return "[:]"
        return "[" + ", ".join(f"{fmt(k, True)}: {fmt(x, True)}" for k, x in v.items()) + "]"
    if isinstance(v, StructVal):
        if is_tuple_name(v.name):
            return "(" + ", ".join(fmt(x, True) for _, x in v.fields) + ")"
        return v.name + "(" + ", ".join(f"{k}: {fmt(x, True)}" for k, x in v.fields) + ")"
    if isinstance(v, EnumVal):
        if not v.values:
            return v.variant
        if v.names and v.variant not in ("Some", "Ok", "Err"):
            return v.variant + "(" + ", ".join(f"{k}: {fmt(x, True)}" for k, x in zip(v.names, v.values)) + ")"
        return v.variant + "(" + ", ".join(fmt(x, True) for x in v.values) + ")"
    if isinstance(v, Opaque):
        return f"<{v.type_name}>"
    return "<fn>"


class Env:
    __slots__ = ("vars", "parent")

    def __init__(self, parent=None):
        self.vars = {}
        self.parent = parent

    def get(self, name):
        e = self
        while e is not None:
            if name in e.vars:
                return e.vars[name]
            e = e.parent
        raise KeyError(name)

    def set(self, name, value):
        e = self
        while e is not None:
            if name in e.vars:
                e.vars[name] = value
                return
            e = e.parent
        raise KeyError(name)


class _PerTask(threading.local):
    """Interpreter state that belongs to one task (thread): the call trace and generic type arguments."""
    def __init__(self):
        self.stack = []
        self.tenv = {}


class Interpreter:
    stack = property(lambda self: self._tl.stack, lambda self, v: setattr(self._tl, "stack", v))
    tenv = property(lambda self: self._tl.tenv, lambda self, v: setattr(self._tl, "tenv", v))

    def __init__(self, prog, file=None, argv=(), stdout=None, stdin=None):
        self._tl = _PerTask()
        from . import tasks
        self.rt = tasks.Runtime()
        tasks.RUNTIME = self.rt
        self.file = file
        self.argv = list(argv)
        self.out = stdout or sys.stdout
        self.inp = stdin or sys.stdin
        self.fns = {}
        self.structs = {}
        self.variants = {}
        self.tests = []
        self.externs = {}
        self.extern_cache = {}
        root = getattr(prog, "project_root", None)
        if root and os.path.isdir(os.path.join(root, ".klar", "python")) \
                and os.path.join(root, ".klar", "python") not in sys.path:
            sys.path.insert(0, os.path.join(root, ".klar", "python"))   # packages from klar.toml [python]
        prog_entry = getattr(prog, "entry", file)
        self.conv = Converter(self, getattr(prog, "checker", None))
        self.stack = []
        checker = getattr(prog, "checker", None)
        self.trait_methods = {n: s.trait for n, s in checker.fns.items() if s.trait} if checker else {}
        self.tenv = {}      # type parameter -> concrete type, for the generic function being run (json_decode needs it)
        for d in prog.decls:
            if d.kind == "Fn":
                self.fns[d.name] = d
            elif d.kind == "Struct":
                self.structs[d.name] = [f for f, _ in d.fields]
            elif d.kind == "Enum":
                for v in d.variants:
                    self.variants[v.name] = [f for f, _ in v.fields]
            elif d.kind == "Extern":
                self.externs[d.name] = d
            elif d.kind == "Test" and d.module == prog_entry:
                self.tests.append(d)
        self.variants.update({"Some": ["value"], "None": [], "Ok": ["value"], "Err": ["error"]})
        for n in range(2, MAX_TUPLE + 1):
            self.structs[tuple_name(n)] = [str(i) for i in range(n)]

    def rt_err(self, code, msg, node, hint=None):
        # self.stack holds (label, line the frame was called from); each frame is shown at the line it is on:
        # the innermost at the error, every caller at its call into the next frame.
        frames = self.stack[-11:]
        trace = []
        for k in range(len(frames) - 1, max(len(frames) - 11, -1), -1):
            label, _ = frames[k]
            at = node.line if k == len(frames) - 1 else frames[k + 1][1]
            trace.append(f"{label} ({self.file or '<input>'}:{at})" if at else label)
        raise KlarError(code, msg, node.line, node.col, hint, self.file, trace=trace)

    # ---------------------------------------------------------- entry points
    def run_main(self):
        with self.rt.cond:
            return self.call_fn("main", [], None)

    def run_tests(self):
        with self.rt.cond:
            return self._run_tests()

    def _run_tests(self):
        results = []
        for t in self.tests:
            try:
                self.stack = [(f"test \"{t.name}\"", None)]
                self.exec_block(t.body, Env())
                results.append((t.name, None))
            except KlarError as e:
                results.append((t.name, e))
            except RecursionError:
                results.append((t.name, KlarError("E406", "stack overflow (recursion too deep)", t.line, t.col, None, self.file)))
        return results

    # ---------------------------------------------------------- calls
    def call_fn(self, name, args, node, tenv=None):
        d = self.fns[name]
        env = Env()
        for (pname, _), a in zip(d.params, args):
            env.vars[pname] = a
        self.stack.append((name, node.line if node else None))
        saved_tenv = self.tenv
        inst = getattr(node, "inst", None) if d.tparams else None
        if tenv is not None:
            self.tenv = tenv
        else:
            self.tenv = {tp: self.concrete_type(t) for tp, t in zip(d.tparams, inst)} if inst else {}
        try:
            self.exec_block(d.body, env)
            return None
        except ReturnSig as r:
            return r.value
        except RecursionError:
            raise KlarError("E406", f"stack overflow in `{name}` (recursion too deep)", d.line, d.col,
                            "check the recursion's base case, or rewrite it as a loop", self.file)
        finally:
            self.stack.pop()
            self.tenv = saved_tenv

    def concrete_type(self, t):
        """t with the running generic function's type parameters replaced by the types it was called with."""
        t = resolve(t)
        return substitute(t, self.tenv) if self.tenv else t

    def runtime_head(self, v):
        """The type name an impl is chosen by, read from a value."""
        if isinstance(v, bool):
            return "bool"
        if isinstance(v, int):
            return "int"
        if isinstance(v, float):
            return "float"
        if isinstance(v, str):
            return "str"
        if isinstance(v, tuple):
            return "list"
        if isinstance(v, dict):
            return "map"
        if v is None:
            return "unit"
        if isinstance(v, StructVal):
            return v.name
        if isinstance(v, EnumVal):
            if v.variant in ("Some", "None"):
                return "Option"
            if v.variant in ("Ok", "Err"):
                return "Result"
            return self.conv.checker.variants[v.variant].enum
        if isinstance(v, Opaque):
            return v.type_name
        return "fn"

    def call_trait(self, method, args, node):
        """A trait method runs the impl for the type of its first argument."""
        from .checker import match_type
        c = self.conv.checker
        sig = c.fns[method]
        info = c.impls.get((sig.trait, self.runtime_head(args[0])))
        if info is None:
            self.rt_err("E312", f"no impl of {sig.trait} for this {self.runtime_head(args[0])} value", node or sig.node)
        tenv = {}
        inst = getattr(node, "inst", None)
        if inst:
            binding = {}
            if match_type(info.ty, self.concrete_type(inst[0]), info.tparams, binding):
                tenv = binding
        return self.call_fn(info.fn_names[method], args, node, tenv)

    def call_value(self, f, args, node):
        if isinstance(f, FnRef):
            if f.name in self.trait_methods:
                return self.call_trait(f.name, args, node)
            if f.name in self.fns:
                return self.call_fn(f.name, args, node)
            if f.name in self.externs:
                return self.call_extern(f.name, args, node)
            return self.call_builtin(f.name, args, node)
        if isinstance(f, PyFn):
            return self.call_python(f.fn, args, f.ty.params, f.ty.ret, node, "a Python function value")
        env = Env(f.env)
        for (pname, _), a in zip(f.params, args):
            env.vars[pname] = a
        saved_tenv = self.tenv
        self.tenv = getattr(f, "tenv", {})
        try:
            if f.block:
                try:
                    self.exec_block(f.body, env)
                    return None
                except ReturnSig as r:
                    return r.value
            return self.eval(f.body, env)
        finally:
            self.tenv = saved_tenv

    # ---------------------------------------------------------- statements
    def exec_block(self, block, env):
        local = Env(env)
        for s in block.stmts:
            self.exec(s, local)

    def exec(self, s, env):
        k = s.kind
        if k == "Let":
            env.vars[s.name] = self.eval(s.value, env)
        elif k == "Assign":
            steps = self_update(s, self.fns, LIST_UPDATES) or self_update(s, self.fns, MAP_UPDATES)
            parts = str_append(s) if steps is None and _is_str(getattr(s.value, "ty", None)) else None
            if steps is not None:
                self.update_in_place(s.name, steps, env)
            elif parts is not None:
                vals = [self.eval(x, env) for x in parts]
                cur = env.get(s.name)
                if type(cur) is not OwnedStr:
                    cur = OwnedStr([cur])
                cur.extend(vals)
                env.set(s.name, cur)
            else:
                env.set(s.name, self.eval(s.value, env))
        elif k == "ExprStmt":
            self.eval(s.expr, env)
        elif k == "If":
            if self.eval(s.cond, env):
                self.exec_block(s.then, env)
            elif s.els is not None:
                self.exec_block(s.els, env)
        elif k == "While":
            while self.eval(s.cond, env):
                try:
                    self.exec_block(s.body, env)
                except BreakSig:
                    break
                except ContinueSig:
                    continue
        elif k == "For":
            for item in self.eval(s.iter, env):
                local = Env(env)
                local.vars[s.var] = item
                try:
                    self.exec_block(s.body, local)
                except BreakSig:
                    break
                except ContinueSig:
                    continue
        elif k == "Return":
            raise ReturnSig(None if s.value is None else self.eval(s.value, env))
        elif k == "Break":
            raise BreakSig()
        elif k == "Continue":
            raise ContinueSig()
        elif k == "Assert":
            if not self.eval(s.cond, env):
                msg = "assertion failed"
                if s.msg is not None:
                    msg += ": " + self.eval(s.msg, env)
                detail = self.explain_assert(s.cond, env)
                self.rt_err("E404", msg, s, detail)

    def explain_assert(self, cond, env):
        if cond.kind == "Binary" and cond.op in ("==", "!=", "<", "<=", ">", ">="):
            try:
                l = self.eval(cond.left, env)
                r = self.eval(cond.right, env)
                return f"left side = {fmt(l, True)}, right side = {fmt(r, True)}"
            except Exception:
                return None
        return None

    # ---------------------------------------------------------- expressions
    def eval(self, e, env):
        k = e.kind
        if k in ("Int", "Float", "Str", "Bool"):
            return e.value
        if k == "Unit":
            return None
        if k == "Name":
            try:
                v = env.get(e.name)
            except KeyError:
                pass
            else:
                tv = type(v)
                if tv is list or tv is OwnedMap or tv is OwnedStr:  # an owned var (see update_in_place): snapshot
                    v = tuple(v) if tv is list else FrozenMap(v) if tv is OwnedMap else "".join(v)
                    env.set(e.name, v)
                return v
            if e.name in self.variants:
                return EnumVal(e.name, ())
            return FnRef(e.name)
        if k == "Interp":
            return "".join(p if isinstance(p, str) else fmt(self.eval(p, env)) for p in e.parts)
        if k == "ListLit":
            return tuple(self.eval(x, env) for x in e.items)
        if k == "MapLit":
            return FrozenMap((self.eval(a, env), self.eval(b, env)) for a, b in e.entries)
        if k == "Unary":
            v = self.eval(e.operand, env)
            return (not v) if e.op == "not" else -v
        if k == "Binary":
            return self.eval_binary(e, env)
        if k == "IfExpr":
            return self.eval(e.then, env) if self.eval(e.cond, env) else self.eval(e.els, env)
        if k == "Call":
            return self.eval_call(e, env)
        if k == "MethodCall":
            return self.eval(e.desugared, env)
        if k == "Field":
            return self.eval(e.target, env).get(e.name)
        if k == "Index":
            t = self.raw_read(e.target, env)
            i = self.eval(e.index, env)
            if isinstance(t, dict):
                if i not in t:
                    self.rt_err("E405", f"map has no key {fmt(i, True)}", e, "use get(m, key) which returns an Option, or check has(m, key) first")
                return t[i]
            if not (0 <= i < len(t)):
                self.rt_err("E401", f"index {i} is out of bounds for length {len(t)}", e,
                            "use get(xs, i) which returns an Option, or check i < len(xs) first")
            return t[i]
        if k == "Try":
            v = self.eval(e.value, env)
            if v.variant in ("Some", "Ok"):
                return v.values[0]
            wrap = getattr(e, "wrap", None)
            if wrap is not None:
                v = errv(EnumVal(wrap, (v.values[0],), tuple(self.variants[wrap])))
            raise ReturnSig(v)
        if k == "With":
            s = self.eval(e.target, env)
            upd = {f: self.eval(val, env) for f, val, _ in e.updates}
            return StructVal(s.name, tuple((f, upd.get(f, v)) for f, v in s.fields))
        if k == "Match":
            return self.eval_match(e, env)
        if k == "Lambda":
            c = Closure(e.params, e.body, e.block, env)
            c.tenv = self.tenv
            return c
        raise RuntimeError(f"cannot evaluate {k}")

    def eval_binary(self, e, env):
        op = e.op
        if op == "and":
            return self.eval(e.left, env) and self.eval(e.right, env)
        if op == "or":
            return self.eval(e.left, env) or self.eval(e.right, env)
        a = self.eval(e.left, env)
        b = self.eval(e.right, env)
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            if isinstance(a, int):
                if b == 0:
                    self.rt_err("E402", "integer division by zero", e)
                q = abs(a) // abs(b)
                return q if (a >= 0) == (b >= 0) else -q
            if b == 0:
                return math.inf if a > 0 else -math.inf if a < 0 else math.nan
            return a / b
        if op == "%":
            if b == 0:
                self.rt_err("E402", "remainder by zero", e)
            r = abs(a) % abs(b)
            return r if a >= 0 else -r
        if op == "==":
            return a == b
        if op == "!=":
            return a != b
        if op == "<":
            return a < b
        if op == "<=":
            return a <= b
        if op == ">":
            return a > b
        if op == ">=":
            return a >= b
        raise RuntimeError(op)

    def raw_read(self, e, env):
        """Evaluate e, but read an owned list var without snapshotting it (for xs[i] and len(xs))."""
        if e.kind == "Name":
            try:
                v = env.get(e.name)
            except KeyError:
                pass
            else:
                if type(v) is not OwnedStr:
                    return v
        return self.eval(e, env)

    def update_in_place(self, name, steps, env):
        """`xs = set_at(xs, i, v)` / `xs = push(xs, v)` on a var. The var then holds a Python list that only it
        references, so later updates are O(1), as in compiled code; any other read of the var turns it back
        into an immutable tuple first (eval of Name), so no other value can see a write."""
        vals = [[self.eval(a, env) for a in call.args[1:]] for _, call in steps]
        cur = env.get(name)
        if steps[0][0] == "put":
            if type(cur) is not OwnedMap:
                cur = OwnedMap(cur)
            for k, x in vals:
                cur[k] = x
            env.set(name, cur)
            return
        if type(cur) is not list:
            cur = list(cur)
        for (op, call), args in zip(steps, vals):
            if op == "push":
                cur.append(args[0])
            elif op == "pop":
                if not cur:
                    self.rt_err("E401", "pop on an empty list", call)
                cur.pop()
            else:
                i, x = args
                if not (0 <= i < len(cur)):
                    self.rt_err("E401", f"set_at index {i} is out of bounds for length {len(cur)}", call)
                cur[i] = x
        env.set(name, cur)

    def eval_call(self, e, env):
        tk = getattr(e, "target_kind", "value")
        if (tk == "fn" and not e.named and e.args and e.callee.kind == "Name" and e.callee.name in ("len", "get", "has")
                and e.callee.name not in self.fns):
            first = self.raw_read(e.args[0], env)
            if e.callee.name == "len":
                return len(first)
            rest = [self.eval(a, env) for a in e.args[1:]]
            return (self.b_get if e.callee.name == "get" else self.b_has)(e, first, *rest)
        if e.named:
            # reorder to declaration order
            if tk == "struct":
                order = self.structs[e.callee.name]
            elif tk == "variant":
                order = self.variants[e.callee.name]
            elif tk == "trait":
                order = [p for p, _ in self.conv.checker.fns[e.callee.name].params]
            else:
                decl = self.fns.get(e.callee.name) or self.externs[e.callee.name]
                order = [p for p, _ in decl.params]
            named = {n: v for n, v, _ in e.named}
            args = [self.eval(named[n], env) for n in order]
        else:
            args = [self.eval(a, env) for a in e.args]
        if tk == "struct":
            name = e.callee.name
            return StructVal(name, tuple(zip(self.structs[name], args)))
        if tk == "variant":
            name = e.callee.name
            return EnumVal(name, tuple(args), tuple(self.variants[name]))
        if tk == "extern":
            return self.call_extern(e.callee.name, args, e)
        if tk == "trait":
            return self.call_trait(e.callee.name, args, e)
        if tk == "fn":
            name = e.callee.name
            if name in self.fns:
                return self.call_fn(name, args, e)
            return self.call_builtin(name, args, e)
        f = self.eval(e.callee, env)
        return self.call_value(f, args, e)

    def eval_match(self, e, env):
        v = self.eval(e.scrutinee, env)
        for arm in e.arms:
            binds = {}
            if self.match_pat(arm.pattern, v, binds):
                local = Env(env)
                local.vars.update(binds)
                if arm.body.kind == "Block":
                    self.exec_block(arm.body, local)
                    return None
                return self.eval(arm.body, local)
        self.rt_err("E306", "no match arm matched", e)

    def match_pat(self, p, v, binds):
        k = p.kind
        if k == "PWild":
            return True
        if k == "PBind":
            binds[p.name] = v
            return True
        if k == "PLit":
            return type(v) is type(p.value) and v == p.value
        if k == "PTuple":
            return all(self.match_pat(sp, sv, binds) for sp, (_, sv) in zip(p.subs, v.fields))
        if k == "PVariant":
            if not isinstance(v, EnumVal) or v.variant != p.name:
                return False
            return all(self.match_pat(sp, sv, binds) for sp, sv in zip(p.subs, v.values))
        return False

    # ---------------------------------------------------------- python interop
    def call_extern(self, name, args, node):
        d = self.externs[name]
        if d.target.startswith("klar:"):
            return self.call_intrinsic(d, args, node or d)
        if d.target.startswith("wasm:"):
            return self.call_wasm_module(d, args, node or d)
        if d.target.startswith("js:"):
            self.rt_err("E407", f"extern `{name}` calls JavaScript (\"{d.target}\"), which only exists in compiled programs", node or d,
                        "build it with `klar build --target node` (or `--target web` for the browser)")
        fn = self.extern_cache.get(name)
        if fn is None:
            try:
                fn = resolve_target(d.target, d.module)
            except (ImportError, AttributeError) as ex:
                self.rt_err("E407", f"extern `{name}`: cannot load Python target \"{d.target}\": {ex}", node or d,
                            "the target is `module.attribute`; a module file next to the .klar file is found automatically")
            self.extern_cache[name] = fn
        self.stack.append((f"extern {name} -> python {d.target}", node.line if node else None))
        try:
            with self.rt.released():      # other tasks run while this one waits on io
                return self.call_python(fn, args, d.ptypes, d.rtype, node or d, f"extern `{name}`")
        finally:
            self.stack.pop()

    def call_wasm_module(self, d, args, node):
        """`extern "wasm:module.fn"` runs module.klar as WebAssembly in compiled web/node apps; here it is
        interpreted like any Klar code, so `klar run` and `klar test` give the same results."""
        hit = self.extern_cache.get(d.name)
        if hit is None:
            from . import tasks
            from .wasm import parse_target, load_module, check_extern
            path, fn = parse_target(d.target, d.module)
            if not os.path.exists(path):
                self.rt_err("E407", f"extern `{d.name}`: WebAssembly module {path} does not exist", node,
                            'the target is "wasm:module.function", where module.klar sits next to this file')
            prog, errors = load_module(path)
            if errors:
                raise errors[0]
            check_extern(d, self.conv.checker, prog, fn, path)
            saved = tasks.RUNTIME
            sub = Interpreter(prog, path, self.argv, self.out, self.inp)
            sub.rt = tasks.RUNTIME = saved          # one runtime (and one lock) for both
            hit = self.extern_cache[d.name] = (sub, fn)
        sub, fn = hit
        return sub.call_fn(fn, list(args), None)

    def call_intrinsic(self, d, args, node):
        """`extern "klar:..."` functions are implemented by each backend on its own value representation."""
        t = d.target
        if t == "klar:ui.start":
            self.rt_err("E407", "std.ui apps run in a web browser", node,
                        "build with `klar build --target web app.klar` and open app.html; test update and view with `klar test`")
        if t == "klar:ui.load":
            return NONE
        if t.startswith("klar:sql."):
            return self.call_sql(t, args, node)
        if t.startswith("klar:json."):
            return self.call_json(t, d, args, node)
        if t.startswith(("klar:task.", "klar:chan.")):
            return self.call_task(t, args, node)
        from .stdpy import weblib
        fn = getattr(weblib, t.split(".", 1)[1], None)
        if fn is None:
            self.rt_err("E407", f"unknown intrinsic \"{t}\"", node)
        return self.call_python(fn, args, d.ptypes, d.rtype, node, f"extern `{d.name}`")

    def call_task(self, t, args, node):
        rt = self.rt
        cid = lambda v: dict(v.fields)["id"]
        if t == "klar:task.all":
            return rt.task_all(self, list(args[0]), node)
        if t == "klar:task.group":
            rt.task_group(self, args[0], node, lambda gid: StructVal("TaskGroup", (("id", gid),)))
            return None
        if t == "klar:task.spawn":
            rt.task_spawn(self, cid(args[0]), args[1], node)
            return None
        if t == "klar:chan.new":
            return StructVal("Chan", (("id", rt.chan_new()),))
        if t == "klar:chan.send":
            rt.chan_send(self, cid(args[0]), args[1], node)
            return None
        if t == "klar:chan.recv":
            got, v = rt.chan_recv(self, cid(args[0]), node)
            return some(v) if got else NONE
        if t == "klar:chan.close":
            rt.chan_close(self, cid(args[0]), node)
            return None
        self.rt_err("E407", f"unknown intrinsic \"{t}\"", node)

    def call_sql(self, target, args, node):
        from .jsonconv import JsonConv
        from .sqlconv import SqlConv, SqlReadError
        conv = SqlConv(self.conv.checker, JsonConv(self.conv.checker))
        inst = getattr(node, "inst", None)
        ty = self.concrete_type(inst[0]) if inst else None
        if target == "klar:sql.value":
            return conv.value(args[0], ty)
        try:
            return ok(conv.read(args[0], ty))
        except SqlReadError as ex:
            return errv(str(ex))

    def call_json(self, target, d, args, node):
        from .jsonconv import JsonConv, JsonTypeError
        from .stdpy import jsonlib
        import json as pyjson
        conv = JsonConv(self.conv.checker)
        inst = getattr(node, "inst", None)
        ty = self.concrete_type(inst[0]) if inst else None
        op = target.split(".", 1)[1]
        if op in ("encode", "pretty", "of"):
            try:
                j = conv.encode(args[0], ty)
            except JsonTypeError as ex:
                self.rt_err("E407", f"json_{op}: {ex}", node)
            if op == "of":
                return conv._py_to_json_enum(j)
            from .jsonconv import dumps
            return dumps(j, "  " if op == "pretty" else "")
        if ty is None or not conv.concrete(ty):
            self.rt_err("E407", f"json_{op} needs to know which type to read, and it is not known here", node,
                        "call it where the type is fixed, e.g. `let r: Result[Todo, str] = json_decode(text)`")
        if op == "into":
            raw = jsonlib._from_klar(conv._json_to_py_tuple(args[0]))
        else:
            try:
                raw = pyjson.loads(args[0], parse_constant=jsonlib._no_constant)
            except ValueError as ex:
                return errv(f"invalid JSON: {ex}")
        try:
            return ok(conv.decode(raw, ty))
        except JsonTypeError as ex:
            return errv(str(ex))

    def call_python(self, fn, args, ptypes, rtype, node, what):
        pargs = [self.conv.to_py(a, t) for a, t in zip(args, ptypes)]
        rt = prune(rtype)
        returns_result = isinstance(rt, TCon) and rt.name == "Result"
        try:
            raw = fn(*pargs)
        except KlarError:
            raise
        except Exception as ex:
            msg = f"{type(ex).__name__}: {ex}"
            if returns_result and isinstance(prune(rt.args[1]), TCon) and prune(rt.args[1]).name == "str":
                return errv(msg)
            self.rt_err("E407", f"{what} raised {msg}", node,
                        "declare the return type as Result[T, str] to receive Python exceptions as Err values")
        try:
            return self.conv.from_py(raw, rtype)
        except InteropError as ex:
            self.rt_err("E407", f"{what} returned a value that does not match its declared type: {ex}", node,
                        "fix the extern's declared return type or the Python function")

    # ---------------------------------------------------------- builtins
    def call_builtin(self, name, a, node):
        f = getattr(self, "b_" + name, None)
        if f is None:
            raise RuntimeError(f"missing builtin {name}")
        return f(node, *a)

    def b_print(self, n, v):
        self.out.write(fmt(v) + "\n")

    def b_eprint(self, n, v):
        self.out.flush()
        sys.stderr.write(fmt(v) + "\n")
        sys.stderr.flush()

    def b_read_all(self, n):
        return self.inp.read()

    def b_read_line(self, n):
        line = self.inp.readline()
        if line == "":
            return NONE
        return some(line.rstrip("\n"))

    def b_read_file(self, n, path):
        try:
            with open(path, encoding="utf-8") as f:
                return ok(f.read())
        except OSError as ex:
            return errv(str(ex))

    def b_write_file(self, n, path, content):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return ok(None)
        except OSError as ex:
            return errv(str(ex))

    def b_args(self, n):
        return tuple(self.argv)

    def b_str(self, n, v):
        return fmt(v)

    def b_panic(self, n, msg):
        self.rt_err("E403", f"panic: {msg}", n)

    def b_len(self, n, xs):
        return len(xs)

    def b_push(self, n, xs, x):
        return xs + (x,)

    def b_set_at(self, n, xs, i, x):
        if not (0 <= i < len(xs)):
            self.rt_err("E401", f"set_at index {i} is out of bounds for length {len(xs)}", n)
        return xs[:i] + (x,) + xs[i + 1:]

    def b_pop(self, n, xs):
        if not xs:
            self.rt_err("E401", "pop on an empty list", n)
        return xs[:-1]

    def b_last(self, n, xs):
        return some(xs[-1]) if xs else NONE

    def b_ord(self, n, s):
        if len(s) != 1:
            self.rt_err("E401", f"ord needs exactly one character, got {len(s)}", n)
        return ord(s)

    def b_chr(self, n, code):
        if not (0 <= code <= 0x10FFFF) or 0xD800 <= code <= 0xDFFF:
            self.rt_err("E401", f"chr: {code} is not a Unicode code point", n)
        return chr(code)

    def b_get(self, n, c, k):
        if isinstance(c, dict):
            return some(c[k]) if k in c else NONE
        return some(c[k]) if 0 <= k < len(c) else NONE

    def b_slice(self, n, xs, a, b):
        a = max(0, min(a, len(xs)))
        b = max(a, min(b, len(xs)))
        return xs[a:b]

    def b_contains(self, n, xs, x):
        return x in xs

    def b_reverse(self, n, xs):
        return xs[::-1]

    def b_range(self, n, a, b):
        return tuple(range(a, b))

    def b_map(self, n, xs, f):
        return tuple(self.call_value(f, [x], n) for x in xs)

    def b_filter(self, n, xs, f):
        return tuple(x for x in xs if self.call_value(f, [x], n))

    def b_fold(self, n, xs, init, f):
        acc = init
        for x in xs:
            acc = self.call_value(f, [acc, x], n)
        return acc

    def b_any(self, n, xs, f):
        return any(self.call_value(f, [x], n) for x in xs)

    def b_all(self, n, xs, f):
        return all(self.call_value(f, [x], n) for x in xs)

    def b_find(self, n, xs, f):
        for x in xs:
            if self.call_value(f, [x], n):
                return some(x)
        return NONE

    def b_index_of(self, n, xs, x):
        for i, y in enumerate(xs):
            if y == x:
                return some(i)
        return NONE

    def b_sort(self, n, xs):
        return tuple(sorted(xs))

    def b_sort_by(self, n, xs, f):
        return tuple(sorted(xs, key=lambda x: self.call_value(f, [x], n)))

    def b_sum(self, n, xs):
        return sum(xs, 0.0 if xs and isinstance(xs[0], float) else 0)

    def b_join(self, n, xs, sep):
        return sep.join(xs)

    def b_split(self, n, s, sep):
        if sep == "":
            return tuple(s)
        return tuple(s.split(sep))

    def b_chars(self, n, s):
        return tuple(s)

    def b_trim(self, n, s):
        return s.strip()

    def b_upper(self, n, s):
        return s.upper()

    def b_lower(self, n, s):
        return s.lower()

    def b_starts_with(self, n, s, p):
        return s.startswith(p)

    def b_ends_with(self, n, s, p):
        return s.endswith(p)

    def b_replace(self, n, s, a, b):
        return s.replace(a, b)

    def b_repeat(self, n, s, k):
        return s * max(k, 0)

    def b_parse_int(self, n, s):
        s = s.strip()
        body = s[1:] if s[:1] in "+-" else s
        if body.isdigit() and body.isascii():
            return some(int(s))
        return NONE

    def b_parse_float(self, n, s):
        t = s.strip()
        if not FLOAT_TEXT.fullmatch(t):   # same rule in every backend: no `1_0`, no hex
            return NONE
        return some(float(t))

    def b_to_float(self, n, x):
        return float(x)

    def b_floor(self, n, x):
        return math.floor(x)

    def b_round(self, n, x):
        return int(math.floor(x + 0.5))

    def b_sqrt(self, n, x):
        if x < 0:
            return math.nan
        return math.sqrt(x)

    def b_abs(self, n, x):
        return abs(x)

    def b_min(self, n, a, b):
        return min(a, b)

    def b_max(self, n, a, b):
        return max(a, b)

    def b_keys(self, n, m):
        return tuple(m.keys())

    def b_entries(self, n, m):
        return tuple(StructVal("Tuple2", (("0", k), ("1", v))) for k, v in m.items())

    def b_values(self, n, m):
        return tuple(m.values())

    def b_has(self, n, m, k):
        return k in m

    def b_put(self, n, m, k, v):
        r = FrozenMap(m)
        r[k] = v
        return r

    def b_remove(self, n, m, k):
        r = FrozenMap(m)
        r.pop(k, None)
        return r

    def b_unwrap(self, n, o):
        if o.variant in ("Some", "Ok"):
            return o.values[0]
        if o.variant == "Err":
            self.rt_err("E403", f"unwrap() called on Err({fmt(o.values[0], True)})", n,
                        "handle the error with match or `?` instead of unwrap")
        self.rt_err("E403", "unwrap() called on None", n, "use unwrap_or(o, default) or match")

    def b_unwrap_or(self, n, o, d):
        return o.values[0] if o.variant in ("Some", "Ok") else d

    def b_ok_or(self, n, o, error):
        return ok(o.values[0]) if o.variant == "Some" else errv(error)

    def b_is_some(self, n, o):
        return o.variant == "Some"

    def b_is_none(self, n, o):
        return o.variant == "None"

    def b_is_ok(self, n, r):
        return r.variant == "Ok"

    def b_is_err(self, n, r):
        return r.variant == "Err"
