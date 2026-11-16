"""Runs checked Klar programs (`klar run`, `klar test`): the program is compiled to Python code by pygen.py, and
this module holds the value representation, the runtime support and the built-in functions."""

import math
import os
import re
import sys
import threading

from .errors import KlarError
from .syntax import MAX_TUPLE, tuple_name, is_tuple_name
from .interop import Converter, Opaque, InteropError, resolve_target
from .types import TCon, prune, resolve, substitute

# What parse_float accepts after trimming, in every backend.
FLOAT_TEXT = re.compile(r"[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?|[+-]?(?i:inf|infinity|nan)")


class StructVal:
    """A struct or tuple value. Immutable by convention: nothing assigns to a value after it is built."""
    __slots__ = ("name", "fields")

    def __init__(self, name, fields):
        self.name = name
        self.fields = fields        # tuple of (name, value)

    def get(self, f):
        for k, v in self.fields:
            if k == f:
                return v
        raise KeyError(f)

    def __eq__(self, other):
        return type(other) is StructVal and self.name == other.name and self.fields == other.fields

    def __hash__(self):
        return hash((self.name, self.fields))

    def __repr__(self):
        return f"StructVal(name={self.name!r}, fields={self.fields!r})"


class EnumVal:
    """An enum value (including Option and Result). Immutable by convention, like StructVal."""
    __slots__ = ("variant", "values", "names")

    def __init__(self, variant, values, names=()):
        self.variant = variant
        self.values = values
        self.names = names

    def __eq__(self, other):
        return type(other) is EnumVal and self.variant == other.variant and self.values == other.values

    def __hash__(self):
        return hash((self.variant, self.values))

    def __repr__(self):
        return f"EnumVal(variant={self.variant!r}, values={self.values!r}, names={self.names!r})"


class FrozenMap(dict):
    def __hash__(self):
        return hash(tuple(sorted(self.items(), key=repr)))


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


# How deep Klar calls may nest under `klar run`. Each Klar call is one Python call. From Python 3.12 on, calls
# between Python functions don't use the C stack (frames live on the heap, about 0.3 KB each), so only memory
# bounds the depth; older versions run the program on a thread with a large C stack instead.
RECURSION_LIMIT = 1_000_000 if sys.version_info >= (3, 11) else 250_000     # 3.10 puts every call on the C stack
BIG_STACK = 1024 * 1024 * 1024


def deep_call(fn):
    """fn() with the recursion limit raised to RECURSION_LIMIT (on a big-stack thread before Python 3.12)."""
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, RECURSION_LIMIT))
    try:
        if sys.version_info >= (3, 12):
            return fn()
        result = {}

        def run():
            try:
                result["value"] = fn()
            except BaseException as ex:      # handed to the calling thread below
                result["error"] = ex
        size = threading.stack_size()
        threading.stack_size(BIG_STACK)
        try:
            t = threading.Thread(target=run, daemon=True)
            t.start()
        finally:
            threading.stack_size(size)
        t.join()
        if "error" in result:
            raise result["error"]
        return result.get("value")
    finally:
        sys.setrecursionlimit(old)


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
        self.compile()

    def compile(self, everything=False):
        """Turn the functions main and the tests can reach, and the tests, into Python code (see pygen.py)."""
        from .pygen import PyGen, namespace
        self.gen_file = f"<klar {self.file or 'program'} {id(self):x}>"
        gen = PyGen(self, self.gen_file)
        source, self.line_map, self.file_map, test_names = gen.program(self.tests, "all" if everything else None)
        ns = namespace(self)
        ns.update(gen.consts)
        exec(compile(source, self.gen_file, "exec"), ns)
        self.py_fns = {name: ns[py] for name, py in gen.pynames.items() if py in ns}
        self.named_code = {f.__code__: name for name, f in self.py_fns.items()}
        self.test_fns = [ns[t] for t in test_names]
        self.py_source = source
        # a label for every generated code object (Klar functions, anonymous functions, tests), for debuggers
        self.code_labels = {}
        pending = [(f.__code__, name) for name, f in self.py_fns.items()]
        pending += [(f.__code__, f"test \"{t.name}\"") for t, f in zip(self.tests, self.test_fns)]
        while pending:
            code, label = pending.pop()
            self.code_labels[code] = label
            pending += [(c, gen.lambda_labels.get(c.co_name, c.co_name)) for c in code.co_consts if hasattr(c, "co_name")]

    # ---------------------------------------------------------- errors and stack traces
    def rt_err(self, code, msg, node, hint=None):
        frame = next(self.klar_frames(), (None, None))[0]
        file = self.show_file(self.file_map[frame.f_lineno - 1]) if frame is not None else self.file
        if node is not None:
            line, col = node.line, node.col
        else:
            line, col = (self.line_map[frame.f_lineno - 1] if frame is not None else 0), 0
        raise KlarError(code, msg, line, col, hint, file, trace=self.trace(line))

    def show_file(self, module):
        """How a module's path is shown in errors: as given on the command line for the entry file, else
        relative to the current directory."""
        if module is None or module == self.file:
            return self.file
        if self.file and os.path.abspath(module) == os.path.abspath(self.file):
            return self.file
        rel = os.path.relpath(module)
        return module if rel.startswith("..") else rel

    def klar_frames(self):
        """(frame, Klar function name or None) for each running frame of generated code, innermost first."""
        f = sys._getframe(1)
        fname, named = self.gen_file, self.named_code
        while f is not None:
            if f.f_code.co_filename == fname:
                yield f, named.get(f.f_code)
            f = f.f_back

    def trace(self, line):
        """Call trace, innermost first: each frame at the line it is on (the innermost at the error, every
        caller at its call into the next frame). self.stack holds the frames that are not Klar functions:
        tests, tasks and Python externs, as (label, line it was called from)."""
        out = []
        cur = line
        where = None
        extra = self.stack
        for label, called in reversed([e for e in extra if e[0].startswith("extern ")]):
            out.append((label, cur, None))
            cur = None
        for f, name in self.klar_frames():
            if cur is None:
                cur = self.line_map[f.f_lineno - 1]
            if where is None:
                where = self.file_map[f.f_lineno - 1]
            if name is not None:
                out.append((name, cur, where))
                cur = where = None
            if len(out) > 10:
                break
        for label, called in reversed([e for e in extra if not e[0].startswith("extern ")]):
            out.append((label, cur, where))
            cur, where = called, None
        return [f"{label} ({self.show_file(w) or '<input>'}:{at})" if at else label for label, at, w in out[:10]]

    def overflow(self, ex, fallback):
        """The KlarError for a RecursionError: it names the innermost Klar function that was running."""
        tb, last = ex.__traceback__, None
        while tb is not None:
            name = self.named_code.get(tb.tb_frame.f_code)
            if name is not None:
                last = name
            tb = tb.tb_next
        if last is None:
            return fallback
        d = self.fns[last]
        return KlarError("E406", f"stack overflow in `{last}` (recursion too deep)", d.line, d.col,
                         "check the recursion's base case, or rewrite it as a loop", self.file)

    # ---------------------------------------------------------- entry points
    def run_main(self):
        return deep_call(self._run_main)

    def _run_main(self):
        with self.rt.cond:
            try:
                return self.call_fn("main", [], None)
            except RecursionError as ex:
                d = self.fns["main"]
                raise self.overflow(ex, KlarError("E406", "stack overflow (recursion too deep)", d.line, d.col,
                                                  None, self.file)) from None

    def run_tests(self):
        return deep_call(self._run_tests_locked)

    def _run_tests_locked(self):
        with self.rt.cond:
            return self._run_tests()

    def _run_tests(self):
        results = []
        for t, fn in zip(self.tests, self.test_fns):
            try:
                self.stack = [(f"test \"{t.name}\"", None)]
                fn()
                results.append((t.name, None))
            except KlarError as e:
                results.append((t.name, e))
            except RecursionError as ex:
                results.append((t.name, self.overflow(ex, KlarError("E406", "stack overflow (recursion too deep)",
                                                                    t.line, t.col, None, self.file))))
        return results

    # ---------------------------------------------------------- calls
    def call_fn(self, name, args, node, tenv=None):
        d = self.fns[name]
        fn = self.py_fns.get(name)
        if fn is None:          # not reachable from main (e.g. a WebAssembly module's export): compile everything
            self.compile(everything=True)
            fn = self.py_fns[name]
        if tenv is None:
            inst = getattr(node, "inst", None) if d.tparams else None
            tenv = {tp: self.concrete_type(t) for tp, t in zip(d.tparams, inst)} if inst else {}
        return fn(*args, _tenv=tenv)

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

    def trait_target(self, method, first, node):
        """A trait method runs the impl for the type of its first argument: (generated function name, its tenv)."""
        from .checker import match_type
        c = self.conv.checker
        sig = c.fns[method]
        info = c.impls.get((sig.trait, self.runtime_head(first)))
        if info is None:
            self.rt_err("E312", f"no impl of {sig.trait} for this {self.runtime_head(first)} value", node or sig.node)
        tenv = {}
        inst = getattr(node, "inst", None)
        if inst:
            binding = {}
            if match_type(info.ty, self.concrete_type(inst[0]), info.tparams, binding):
                tenv = binding
        return info.fn_names[method], tenv

    def call_trait(self, method, args, node):
        name, tenv = self.trait_target(method, args[0], node)
        return self.call_fn(name, args, node, tenv)

    def call_value(self, f, args, node):
        """Call a Klar function value: every one (named, anonymous, built-in or from Python) is a Python callable."""
        return f(*args)

    # function values for names that are not plain Klar functions
    def make_enum(self, name, values):
        return EnumVal(name, values)

    def trait_value(self, name, node):
        return lambda *a: self.call_trait(name, list(a), node)

    def extern_value(self, name, node):
        return lambda *a: self.call_extern(name, list(a), node)

    def builtin_value(self, name, node):
        return lambda *a: self.call_builtin(name, a, node)

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
        return tuple([f(x) for x in xs])

    def b_filter(self, n, xs, f):
        return tuple([x for x in xs if f(x)])

    def b_fold(self, n, xs, init, f):
        acc = init
        for x in xs:
            acc = f(acc, x)
        return acc

    def b_any(self, n, xs, f):
        for x in xs:
            if f(x):
                return True
        return False

    def b_all(self, n, xs, f):
        for x in xs:
            if not f(x):
                return False
        return True

    def b_find(self, n, xs, f):
        for x in xs:
            if f(x):
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
        keys = [f(x) for x in xs]      # a Python loop, so deep recursion in f doesn't use the C stack
        return tuple(xs[i] for i in sorted(range(len(xs)), key=keys.__getitem__))

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
