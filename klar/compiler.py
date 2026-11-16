"""Klar -> C compiler.

Single pass over the checked program: generic functions are monomorphised on demand, every value is a
64-bit slot in containers (typed C locals elsewhere), closures are lifted to C functions taking slots,
and type-directed operations (==, str/print) get one generated helper per concrete type.
The generated C is self-contained (the runtime is inlined) and compiles with tcc, gcc or clang.
"""

import os

from .errors import KlarError
from .ownership import self_update, is_fresh
from .syntax import Node, is_tuple_name
from .types import TCon, TFn, TVar, INT, prune, resolve, substitute, show

RUNTIME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime", "klar_rt.h")
STD_RUNTIME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime", "klar_std.h")
WASM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime", "wasm")
# std modules a WebAssembly build can use (the rest need an operating system: files, processes, sockets, threads)
WASM_STD = ("klar.stdpy.mathlib.", "klar.stdpy.timelib.now", "klar.stdpy.timelib.millis", "klar.stdpy.timelib.format_utc",
            "klar.stdpy.timelib.iso_utc", "klar.stdpy.randomlib.", "klar.stdpy.jsonlib.", "klar.stdpy.cryptolib.", "klar.stdpy.fslib.join",
            "klar:web.form_decode", "klar:web.url_encode", "klar:web.url_decode", "klar:web.mime_type")

# Standard-library externs with a C implementation in klar_std.h: target -> (C function, feature).
# A feature names the library a program then links (see FEATURES); None needs only libc.
NATIVE = {}
for _mod, _names in {
    "fslib": "read write append exists is_dir list_dir mkdir remove join",
    "mathlib": "pi sin cos tan atan2 exp log log10 pow",
    "timelib": "now millis sleep format_utc iso_utc",
    "randomlib": "seed int_between unit_float",
    "processlib": "run env_get exit",
    "jsonlib": "parse stringify pretty",
    "cryptolib": "sha256 hmac_sha256 token password_hash password_verify needs_rehash equal",
}.items():
    for _n in _names.split():
        NATIVE[f"klar.stdpy.{_mod}.{_n}"] = (f"kx_{_mod[:-3]}_{_n}", None)
for _n in "matches find_all groups replace split".split():
    NATIVE[f"klar.stdpy.regexlib.{_n}"] = (f"kx_regex_{_n}", "regex")
for _n in "open_db execute query last_insert_id close".split():
    NATIVE[f"klar.stdpy.sqlitelib.{_n}"] = (f"kx_sqlite_{_n}", "sqlite")
NATIVE["klar.stdpy.httplib.request"] = ("kx_http_request", "http_client")
NATIVE["klar.stdpy.httplib.serve"] = ("kx_http_serve", "net")
for _n in "all group spawn".split():
    NATIVE[f"klar:task.{_n}"] = (f"kx_task_{_n}", "tasks")
for _n in "new send recv close".split():
    NATIVE[f"klar:chan.{_n}"] = (f"kx_chan_{_n}", "tasks")
NATIVE["klar:web.publish"] = ("kx_web_publish", "net")
for _n in "form_decode url_encode url_decode mime_type".split():
    NATIVE[f"klar:web.{_n}"] = (f"kx_web_{_n}", None)
JSON_OPS = {"encode": "kx_json_encode", "pretty": "kx_json_encode_pretty", "decode": "kx_json_decode",
            "of": "kx_json_of", "into": "kx_json_into"}

INCLUDE_DIRS = ["/usr/include", "/usr/local/include", "/opt/homebrew/include", "/usr/include/x86_64-linux-gnu",
                "/usr/include/aarch64-linux-gnu"]


def has_header(name):
    return any(os.path.exists(os.path.join(d, name)) for d in INCLUDE_DIRS)


# feature -> (C defines, libraries to link, required header, package hint)
FEATURES = {
    "regex": (["K_STD_REGEX"], ["-lpcre2-8"], "pcre2.h", "libpcre2-dev (Debian/Ubuntu) or pcre2 (Homebrew)"),
    "sqlite": (["K_STD_SQLITE"], ["-lsqlite3"], "sqlite3.h", "libsqlite3-dev (Debian/Ubuntu) or sqlite (Homebrew)"),
    "net": (["K_STD_NET", "K_STD_TASKS"], ["-lpthread"], None, None),
    "tasks": (["K_STD_TASKS"], ["-lpthread"], None, None),
}

PTR_KINDS = {"str": "KStr*", "list": "KList*", "map": "KMap*", "enum": "KEnum*", "struct": "KS*", "fn": "KClo*"}


def c_string_literal(b):
    out = []
    for ch in b:
        if ch == 0x22:
            out.append('\\"')
        elif ch == 0x5C:
            out.append("\\\\")
        elif 0x20 <= ch < 0x7F and ch != 0x3F:
            out.append(chr(ch))
        else:
            out.append("\\%03o" % ch)
    return '"' + "".join(out) + '"'


class FnCtx:
    def __init__(self, ret, is_lambda):
        self.ret = ret
        self.is_lambda = is_lambda
        self.locals = {}
        self.owned = set()      # list vars with an ownership flag o_<name>


class Compiler:
    def __init__(self, prog, file, target="native", exports=None):
        self.target = target     # "native" or "wasm"
        self.exports = exports   # wasm: names of functions JavaScript calls (None: a program with main)
        self.prog = prog
        self.file = file or "<input>"
        self.c = prog.checker
        self.fns = {d.name: d for d in prog.decls if d.kind == "Fn"}
        self.externs = {d.name for d in prog.decls if d.kind == "Extern"}
        self.extern_decls = {d.name: d for d in prog.decls if d.kind == "Extern"}
        self.features = set()    # parts of klar_std.h the program uses
        self.uses_std = False
        self.descs = []          # JSON type descriptor definitions
        self.tags = {"Some": 0, "None": 1, "Ok": 2, "Err": 3}
        for name in self.c.variants:
            if name not in self.tags:
                self.tags[name] = len(self.tags)
        self.instances = {}      # key -> C name
        self.work = []
        self.defs = []           # C function definitions
        self.protos = []
        self.statics = []
        self.strlits = {}
        self.helpers = {}        # (kind, type key) -> C name
        self.ntmp = 0
        self.nlam = 0
        self.sub = {}
        self.ctx = None

    # ------------------------------------------------------------------ errors
    def unsupported(self, node, what):
        raise KlarError("E501", f"{what} cannot be compiled yet", node.line, node.col,
                        "run this program with `klar run` (the interpreter) instead", node_file(node, self.file))

    # ------------------------------------------------------------------ types
    def T(self, t):
        """Concrete type in the current instantiation."""
        if t is None:
            return INT
        t = resolve(substitute(prune(t), self.sub))
        return self._default_vars(t)

    def _default_vars(self, t):
        t = prune(t)
        if isinstance(t, TVar):
            return INT        # unconstrained: no value of this type exists at run time
        if isinstance(t, TFn):
            return TFn([self._default_vars(p) for p in t.params], self._default_vars(t.ret), t.io)
        return TCon(t.name, [self._default_vars(a) for a in t.args])

    def kind(self, t):
        t = prune(t)
        if isinstance(t, TFn):
            return "fn"
        n = t.name
        if n in ("int", "bool", "unit", "never"):
            return "int"
        if n in ("float", "str", "list", "map"):
            return n
        if n in ("Option", "Result") or n in self.c.enums:
            return "enum"
        if n in self.c.structs:
            return "struct"
        if n in self.c.extern_types:
            return "extern"
        return "int"

    def ctype(self, t):
        k = self.kind(t)
        if k == "int":
            return "int64_t"
        if k == "float":
            return "double"
        if k == "extern":
            return "void*"
        return PTR_KINDS[k]

    def to_s(self, code, t):
        k = self.kind(t)
        if k == "int":
            return f"((KS)({code}))"
        if k == "float":
            return f"k_d2s({code})"
        return f"k_p2s({code})"

    def from_s(self, code, t):
        k = self.kind(t)
        if k == "int":
            return f"((int64_t)({code}))"
        if k == "float":
            return f"k_s2d({code})"
        return f"K_S2P({self.ctype(t)}, {code})"

    def is_never(self, t):
        t = prune(t)
        return isinstance(t, TCon) and t.name == "never"

    def coerce(self, code, from_t, to_t):
        if self.is_never(from_t) and not self.is_never(to_t):
            return f"({{ {code}; ({self.ctype(to_t)})0; }})"
        return code

    def tmp(self):
        self.ntmp += 1
        return f"_t{self.ntmp}"

    # ------------------------------------------------------------------ driver
    def compile(self):
        if self.target == "wasm":
            return self.compile_wasm()
        if "main" not in self.fns:
            raise KlarError("E201", "no `main` function to compile", 1, 1, "add: fn main() uses io { ... }", self.file)
        main = self.request_fn("main", [])
        while self.work:
            name, targs, cname = self.work.pop()
            self.gen_fn(self.fns[name], targs, cname)
        threads = self.features & {"net", "tasks", "http_client"}
        parts = ["#define GC_THREADS\n#include <pthread.h>\n"] if threads else []
        parts.append(open(RUNTIME, encoding="utf-8").read())
        if self.uses_std:
            parts.append("".join(f"#define KT_{v} {t}\n" for v, t in self.tags.items() if t > 3))
            parts += sorted({f"#define {d}" for f in self.features for d in self.feature_defines(f)})
            parts.append(open(STD_RUNTIME, encoding="utf-8").read())
        parts.append("\n/* ---- generated ---- */\n")
        parts += self.descs
        parts += self.statics
        parts += [p + ";" for p in self.protos]
        parts += self.defs
        parts.append(f"int main(int argc, char **argv) {{\n  k_file = {c_string_literal(self.file.encode())};\n"
                     f"  k_init(argc, argv);\n  {main}();\n  fflush(stdout);\n  return 0;\n}}\n")
        return "\n".join(parts)

    # ------------------------------------------------------------------ WebAssembly
    def wasm_kind(self, t):
        """How a value crosses the JavaScript <-> WebAssembly boundary: i64 / f64 / str / ints / floats / json."""
        k = self.kind(t)
        n = prune(t).name if isinstance(prune(t), TCon) else ""
        if k == "int":
            return "bool" if n == "bool" else "unit" if n in ("unit", "never") else "i64"
        if k == "float":
            return "f64"
        if k == "str":
            return "str"
        if k == "list":
            ek = self.kind(t.args[0])
            en = prune(t.args[0]).name if isinstance(prune(t.args[0]), TCon) else ""
            if ek == "int" and en == "int":
                return "ints"
            if ek == "float":
                return "floats"
        if k in ("fn", "extern"):
            return None
        return "json"

    def compile_wasm(self):
        main = None
        if self.exports is None:
            if "main" not in self.fns:
                raise KlarError("E201", "no `main` function to compile", 1, 1, "add: fn main() uses io { ... }", self.file)
            main = self.request_fn("main", [])
        wrappers = []
        self.export_sigs = {}
        for name in self.exports or []:
            d = self.fns.get(name)
            if d is None:
                raise KlarError("E201", f"`{name}` is not a function in {self.file}", 1, 1,
                                "a wasm extern names a top-level `fn` of that module", self.file)
            if d.tparams:
                raise KlarError("E501", f"generic function `{name}` cannot be called from JavaScript", d.line, d.col,
                                "export a non-generic wrapper, e.g. fn sum_ints(xs: list[int]) -> int { return sum(xs) }",
                                self.file)
            sig = self.c.fns[name]
            cname = self.request_fn(name, [])
            ps, args, kinds, pre = [], [], [], []
            for i, (p, t) in enumerate(sig.params):
                t = self.T(t)
                wk = self.wasm_kind(t)
                if wk is None:
                    raise KlarError("E501", f"`{name}` takes a `{show(t)}`, which cannot cross from JavaScript", d.line,
                                    d.col, "pass plain data (numbers, strings, lists, maps, structs, enums)", self.file)
                kinds.append(wk)
                if wk in ("i64", "bool", "unit"):
                    ps.append(f"int64_t a{i}")
                    args.append(f"a{i}")
                elif wk == "f64":
                    ps.append(f"double a{i}")
                    args.append(f"a{i}")
                elif wk in ("str", "ints", "floats"):
                    ps.append(f"{self.ctype(t)} a{i}")
                    args.append(f"a{i}")
                else:
                    self.uses_std = True
                    ps.append(f"KStr *a{i}")
                    pre.append(f"KEnum *d{i} = kx_json_decode(a{i}, &{self.desc(t)}); "
                               f"if (d{i}->tag != K_TAG_OK) k_fail(\"E407\", 0, 0, \"argument `{p}` of `{name}`: %s\", "
                               f"K_S2P(KStr *, d{i}->f[0])->data);")
                    args.append(self.from_s(f"d{i}->f[0]", t))
            rt = self.T(sig.ret)
            rk = self.wasm_kind(rt)
            if rk is None:
                raise KlarError("E501", f"`{name}` returns a `{show(rt)}`, which cannot cross to JavaScript", d.line, d.col,
                                "return plain data (numbers, strings, lists, maps, structs, enums)", self.file)
            call = f"{cname}({', '.join(args)})"
            if rk in ("i64", "bool", "unit"):
                ret, body = "int64_t", f"int64_t r = {call};"
            elif rk == "f64":
                ret, body = "double", f"double r = {call};"
            elif rk == "str":
                ret, body = "KStr *", f"KStr *r = {call};"
            elif rk in ("ints", "floats"):
                ret, body = "KList *", f"KList *r = {call}; k_list_data(r);"
            else:
                self.uses_std = True
                ret, body = "KStr *", f"KStr *r = kx_json_encode({self.to_s(call, rt)}, &{self.desc(rt)});"
            wrappers.append(f'K_EXPORT("{name}") {ret} kw_{name}({", ".join(ps) or "void"}) {{\n'
                            f'  {" ".join(pre)}\n  {body}\n  fflush(stdout);\n  return r;\n}}\n')
            self.export_sigs[name] = (kinds, rk)
        while self.work:
            fname, targs, cname = self.work.pop()
            self.gen_fn(self.fns[fname], targs, cname)
        parts = [open(os.path.join(WASM_DIR, "klar_wasm.h"), encoding="utf-8").read(),
                 open(RUNTIME, encoding="utf-8").read()]
        if self.uses_std:
            parts.append("".join(f"#define KT_{v} {t}\n" for v, t in self.tags.items() if t > 3))
            parts.append(open(STD_RUNTIME, encoding="utf-8").read())
        parts.append(WASM_GLUE)
        parts.append("\n/* ---- generated ---- */\n")
        parts += self.descs
        parts += self.statics
        parts += [p + ";" for p in self.protos]
        parts += self.defs
        parts += wrappers
        parts.append(f"K_EXPORT(\"klar_init\") void klar_init(void) {{ k_file = {c_string_literal(self.file.encode())}; }}\n")
        if main:
            parts.append(f'K_EXPORT("klar_main") int klar_main(void) {{\n  k_argv = kw_args(&k_argc);\n  {main}();\n'
                         f'  fflush(stdout);\n  return 0;\n}}\n')
        return "\n".join(parts)

    def feature_defines(self, f):
        if f == "http_client":
            return ["K_STD_NET", "K_STD_TASKS"] + (["K_STD_TLS"] if has_header("openssl/ssl.h") else [])
        return FEATURES[f][0]

    def libs(self):
        out = []
        for f in sorted(self.features):
            if f == "http_client":
                out += (["-lssl", "-lcrypto"] if has_header("openssl/ssl.h") else []) + ["-lpthread"]
            else:
                out += FEATURES[f][1]
        return out

    def native_extern(self, e, d):
        """C function for a standard-library extern, or E501 for anything else (Python / JavaScript interop)."""
        target = d.target
        if self.target == "wasm" and not target.startswith(WASM_STD) and target in NATIVE:
            raise KlarError("E501", f"`{d.name}` cannot run in WebAssembly (it needs an operating system: files, "
                            f"processes, sockets, threads, SQLite or regex)", e.line, e.col,
                            "keep WebAssembly modules to computation; do the io in the JavaScript part of the app "
                            "(`--target web` / `--target node`) and pass the data in", node_file(e, self.file))
        if target in NATIVE:
            cname, feature = NATIVE[target]
            if feature in FEATURES and FEATURES[feature][2] and not has_header(FEATURES[feature][2]):
                raise KlarError("E501", f"`{d.name}` needs the {feature} library to be compiled natively", e.line, e.col,
                                f"install {FEATURES[feature][3]}, or run with `klar run` / `--target node`",
                                node_file(e, self.file))
            self.uses_std = True
            if feature:
                self.features.add(feature)
            return cname
        if target.startswith("klar:ui."):
            self.unsupported(e, f"`{d.name}` (browser apps: build with --target web)")
        self.unsupported(e, f"extern function `{d.name}` (Python or JavaScript interop)")

    def desc(self, t):
        """Name of a static KD descriptor for type t, used by automatic JSON."""
        t = self.T(t)
        key = ("desc", show(t))
        if key in self.helpers:
            return self.helpers[key]
        name = f"KD_{len(self.helpers)}"
        self.helpers[key] = name
        self.descs.append(f"static KD {name};")
        k = self.kind(t)
        tn = prune(t).name if isinstance(prune(t), TCon) else ""
        q = lambda x: c_string_literal(x.encode())
        if tn in ("int", "float", "bool", "str", "unit", "never") or k in ("int", "float", "str"):
            kd = {"int": "KD_INT", "float": "KD_FLOAT", "bool": "KD_BOOL", "unit": "KD_UNIT", "never": "KD_UNIT",
                  "str": "KD_STR"}.get(tn, "KD_INT")
            init = f"{{{kd}}}"
        elif k == "list":
            init = f"{{KD_LIST, 0, &{self.desc(t.args[0])}}}"
        elif k == "map":
            init = f"{{KD_MAP, 0, 0, &{self.desc(t.args[0])}, &{self.desc(t.args[1])}}}"
        elif k == "struct":
            fields = self.c.struct_fields(t)
            fn = ", ".join(q(f) for f, _ in fields) or "0"
            ft = ", ".join(f"&{self.desc(ft)}" for _, ft in fields) or "0"
            self.descs.append(f"static const char *{name}_f[] = {{{fn}}}; static KD *{name}_t[] = {{{ft}}};")
            kd = "KD_TUPLE" if is_tuple_name(tn) else "KD_STRUCT"
            init = f"{{{kd}, {q(tn)}, 0, 0, 0, {len(fields)}, {name}_f, {name}_t}}"
        elif k == "enum":
            vs = []
            for i, (vname, ftypes) in enumerate(self.variant_types(t)):
                labels = [f for f, _ in self.c.variants[vname].fields]
                ln = ", ".join(q(x) for x in labels) or "0"
                lt = ", ".join(f"&{self.desc(ft)}" for ft in ftypes) or "0"
                self.descs.append(f"static const char *{name}_l{i}[] = {{{ln}}}; static KD *{name}_t{i}[] = {{{lt}}};")
                vs.append(f"{{{q(vname)}, {self.tags[vname]}, {len(ftypes)}, {name}_l{i}, {name}_t{i}}}")
            self.descs.append(f"static KDV {name}_v[] = {{{', '.join(vs)}}};")
            init = f"{{KD_ENUM, {q(tn)}, 0, 0, 0, 0, 0, 0, {len(vs)}, {name}_v}}"
        else:
            init = f"{{KD_OTHER, {q(tn) if tn else '0'}}}"
        self.descs.append(f"static KD {name} = {init};")
        return name

    def request_fn(self, name, targs):
        key = name + "|" + ",".join(show(t) for t in targs)
        if key not in self.instances:
            cname = f"kf_{name}" if not targs else f"kf_{name}__{len(self.instances)}"
            self.instances[key] = cname
            d = self.fns[name]
            sub = dict(zip(d.tparams, targs))
            sig = self.c.fns[name]
            ptypes = [self._subst_c(t, sub) for _, t in sig.params]
            rt = self._subst_c(sig.ret, sub)
            params = ", ".join(f"{self.ctype(t)} v_{p}" for (p, _), t in zip(sig.params, ptypes)) or "void"
            self.protos.append(f"static {self.ctype(rt)} {cname}({params})")
            self.work.append((name, targs, cname))
        return self.instances[key]

    def _subst_c(self, t, sub):
        return self._default_vars(resolve(substitute(prune(t), sub)))

    def gen_fn(self, d, targs, cname):
        self.sub = dict(zip(d.tparams, targs))
        sig = self.c.fns[d.name]
        rt = self.T(sig.ret)
        self.ctx = FnCtx(rt, False)
        lines = []
        params = []
        for p, t in sig.params:
            ct = self.T(t)
            self.ctx.locals[p] = ct
            params.append(f"{self.ctype(ct)} v_{p}")
        self.block(d.body, lines, new_scope=False)
        body = "\n".join("  " + l for l in lines)
        self.defs.append(f"static {self.ctype(rt)} {cname}({', '.join(params) or 'void'}) {{\n{body}\n"
                         f"  return ({self.ctype(rt)})0;\n}}\n")

    # ------------------------------------------------------------------ statements
    def block(self, b, out, new_scope=True):
        if new_scope:
            out.append("{")
        for s in b.stmts:
            self.stmt(s, out)
        if new_scope:
            out.append("}")

    def stmt(self, s, out):
        k = s.kind
        if k == "Let":
            t = self.T(s.ty)
            code = self.coerce(self.expr(s.value), self.T(s.value.ty), t)
            if s.name == "_":
                out.append(f"(void)({code});")
            else:
                self.ctx.locals[s.name] = t
                out.append(f"{self.ctype(t)} v_{s.name} = {code};")
                if s.mutable and self.kind(t) == "list":
                    self.ctx.owned.add(s.name)
                    out.append(f"int o_{s.name} = {1 if is_fresh(s.value, self.fns) else 0}; (void)o_{s.name};")
        elif k == "Assign":
            t = self.ctx.locals[s.name]
            upd = self_update(s, self.fns) if s.name in self.ctx.owned else None
            if upd is not None:
                self.owned_update(s, upd, t, out)
                return
            out.append(f"v_{s.name} = {self.coerce(self.expr(s.value), self.T(s.value.ty), t)};")
            if s.name in self.ctx.owned:
                out.append(f"o_{s.name} = {1 if is_fresh(s.value, self.fns) else 0};")
        elif k == "ExprStmt":
            out.append(f"(void)({self.expr(s.expr)});")
        elif k == "If":
            out.append(f"if ({self.expr(s.cond)}) {{")
            self.block(s.then, out, new_scope=False)
            out.append("}")
            if s.els is not None:
                out.append("else {")
                self.block(s.els, out, new_scope=False)
                out.append("}")
        elif k == "While":
            out.append(f"while ({self.expr(s.cond)}) {{")
            self.block(s.body, out, new_scope=False)
            out.append("}")
        elif k == "For":
            it = s.iter
            elem = self.T(prune(self.T(it.ty)).args[0])
            self.ctx.locals[s.var] = elem
            if it.kind == "Call" and it.callee.kind == "Name" and it.callee.name == "range" \
                    and getattr(it, "target_kind", "") == "fn" and "range" not in self.fns and not it.named:
                a, b = self.expr(it.args[0]), self.expr(it.args[1])
                end = self.tmp()
                out.append(f"{{ int64_t {end} = {b};")
                out.append(f"for (int64_t v_{s.var} = {a}; v_{s.var} < {end}; v_{s.var}++) {{")
                self.block(s.body, out, new_scope=False)
                out.append("}}")
            else:
                l, i = self.tmp(), self.tmp()
                out.append(f"{{ KList *{l} = {self.expr(it)};")
                out.append(f"for (int64_t {i} = 0; {i} < {l}->len; {i}++) {{")
                out.append(f"{self.ctype(elem)} v_{s.var} = {self.from_s(f'KL_DATA({l})[{i}]', elem)};")
                self.block(s.body, out, new_scope=False)
                out.append("}}")
        elif k == "Return":
            rt = self.ctx.ret
            if s.value is None:
                code = "0"
                vt = rt
            else:
                vt = self.T(s.value.ty)
                code = self.coerce(self.expr(s.value), vt, rt)
            if self.ctx.is_lambda:
                out.append(f"return {self.to_s(code, rt)};")
            else:
                out.append(f"return {code};")
        elif k == "Break":
            out.append("break;")
        elif k == "Continue":
            out.append("continue;")
        elif k == "Assert":
            cond = self.expr(s.cond)
            if s.msg is not None:
                out.append(f"if (!({cond})) {{ KStr *_am = {self.expr(s.msg)}; "
                           f"k_fail(\"E404\", {s.line}, {s.col}, \"assertion failed: %s\", _am->data); }}")
            else:
                out.append(f"if (!({cond})) k_fail(\"E404\", {s.line}, {s.col}, \"assertion failed\");")
        else:
            self.unsupported(s, f"statement {k}")

    def owned_update(self, s, steps, t, out):
        v, o = f"v_{s.name}", f"o_{s.name}"
        et = t.args[0]
        vals = []
        out.append("{")
        for name, call in steps:
            if name == "set_at":
                i, x = self.tmp(), self.tmp()
                out.append(f"int64_t {i} = {self.expr(call.args[1])}; KS {x} = {self.to_s(self.expr(call.args[2]), et)};")
                vals.append((name, call, i, x))
            else:
                x = self.tmp()
                out.append(f"KS {x} = {self.to_s(self.expr(call.args[1]), et)};")
                vals.append((name, call, None, x))
        for name, call, i, x in vals:
            L = f"{call.line}, {call.col}"
            if name == "set_at":
                out.append(f"if ({o}) {{ if ({i} < 0 || {i} >= {v}->len) k_fail(\"E401\", {L}, \"set_at index %lld is out of "
                           f"bounds for length %lld\", (long long){i}, (long long){v}->len); {v}->buf->data[{i}] = {x}; }}")
                out.append(f"else {{ {v} = k_list_set_at_fresh({v}, {i}, {x}, {L}); {o} = 1; }}")
            else:
                out.append(f"if ({o}) k_list_push_owned({v}, {x}); else {{ {v} = k_list_push({v}, {x}); }}")
        out.append("}")

    # ------------------------------------------------------------------ expressions
    def expr(self, e):
        k = e.kind
        m = getattr(self, "e_" + k, None)
        if m is None:
            self.unsupported(e, f"expression {k}")
        return m(e)

    def e_Int(self, e):
        v = e.value
        if not (-(2 ** 63) < v < 2 ** 63):
            raise KlarError("E408", f"integer literal {v} does not fit in 64 bits", e.line, e.col,
                            "compiled Klar ints are 64-bit; use `klar run` for unlimited integers", self.file)
        return f"INT64_C({v})"

    def e_Float(self, e):
        r = repr(e.value)
        if "inf" in r or "nan" in r:
            return "(1.0/0.0)" if r == "inf" else "NAN"
        return r if ("." in r or "e" in r) else r + ".0"

    def e_Bool(self, e):
        return "1" if e.value else "0"

    def e_Unit(self, e):
        return "0"

    def strlit(self, value):
        if value not in self.strlits:
            name = f"ks_{len(self.strlits)}"
            b = value.encode("utf-8")
            self.strlits[value] = name
            self.statics.append(f"static struct {{ int64_t len; int64_t cps; char data[{len(b) + 1}]; }} {name}_raw = "
                                f"{{{len(b)}, {len(value)}, {c_string_literal(b)}}};\n#define {name} ((KStr *)&{name}_raw)")
        return self.strlits[value]

    def e_Str(self, e):
        return self.strlit(e.value)

    def e_Interp(self, e):
        b = self.tmp()
        parts = [f"KSB {b}; ksb_init(&{b});"]
        for p in e.parts:
            if isinstance(p, str):
                if p:
                    parts.append(f"ksb_str(&{b}, {self.strlit(p)});")
            else:
                parts.append(self.show_into(f"&{b}", self.expr(p), self.T(p.ty), nested=False))
        parts.append(f"ksb_done(&{b});")
        return "({ " + " ".join(parts) + " })"

    def e_Name(self, e):
        name = e.name
        if name in self.ctx.locals:
            if name in self.ctx.owned and not getattr(e, "safe_read", False):
                return f"(o_{name} = 0, v_{name})"
            return f"v_{name}"
        if name in self.c.variants:
            return self.nullary(name)
        if name in self.fns:
            t = self.T(e.ty)
            targs = [self.T(x) for x in getattr(e, "inst", [])]
            return self.fn_value(name, targs, t)
        if getattr(e, "trait_ref", None):
            return self.fn_value(*self.trait_target(e), self.T(e.ty))
        if name in self.externs:
            self.unsupported(e, f"extern function `{name}` used as a value (wrap it: `fn(x) => {name}(x)`)")
        return self.builtin_value(name, self.T(e.ty), e)

    def nullary(self, name):
        cname = f"k_v_{name}"
        key = ("nullary", name)
        if key not in self.helpers:
            self.helpers[key] = cname
            self.statics.append(f"static KEnum {cname} = {{{self.tags[name]}}};")
        return f"(&{cname})"

    def fn_value(self, name, targs, t):
        inst = self.request_fn(name, targs)
        key = ("fnval", inst)
        if key not in self.helpers:
            tr = f"kt_{inst}"
            self.helpers[key] = tr
            ps = ", ".join(["KClo *self"] + [f"KS a{i}" for i in range(len(t.params))])
            args = ", ".join(self.from_s(f"a{i}", pt) for i, pt in enumerate(t.params))
            self.defs.append(f"static KS {tr}({ps}) {{ (void)self; return {self.to_s(f'{inst}({args})', t.ret)}; }}")
            self.statics.append(f"static KS {tr}({ps});\nstatic KClo kc_{inst} = {{(void *){tr}}};")
        return f"(&kc_{inst})"

    def builtin_value(self, name, t, e):
        if not isinstance(t, TFn):
            self.unsupported(e, f"`{name}` as a value")
        key = ("builtin_val", name, show(t))
        if key not in self.helpers:
            tr = f"kb_{len(self.helpers)}"
            self.helpers[key] = tr
            ps = ", ".join(["KClo *self"] + [f"KS a{i}" for i in range(len(t.params))])
            codes = [self.from_s(f"a{i}", pt) for i, pt in enumerate(t.params)]
            body = self.builtin(name, codes, list(t.params), t.ret, e)
            self.statics.append(f"static KS {tr}({ps});\nstatic KClo kcb_{tr} = {{(void *){tr}}};")
            self.defs.append(f"static KS {tr}({ps}) {{ (void)self; return {self.to_s(body, t.ret)}; }}")
        return f"(&kcb_{self.helpers[key]})"

    def e_ListLit(self, e):
        t = self.T(e.ty)
        et = t.args[0]
        items = [self.to_s(self.expr(x), et) for x in e.items]
        return f"k_list_new({len(items)}" + "".join(", " + i for i in items) + ")"

    def e_MapLit(self, e):
        t = self.T(e.ty)
        kt, vt = t.args
        kind = 1 if self.kind(kt) == "str" else 0
        code = f"k_map_empty({kind})"
        for k_, v_ in e.entries:
            code = f"k_map_put({code}, {self.to_s(self.expr(k_), kt)}, {self.to_s(self.expr(v_), vt)})"
        return code

    def e_Unary(self, e):
        x = self.expr(e.operand)
        if e.op == "not":
            return f"(!({x}))"
        if self.kind(self.T(e.ty)) == "float":
            return f"(-({x}))"
        return f"k_neg({x}, {e.line}, {e.col})"

    def e_Binary(self, e):
        op = e.op
        if op == "and":
            return f"({self.expr(e.left)} && {self.expr(e.right)})"
        if op == "or":
            return f"({self.expr(e.left)} || {self.expr(e.right)})"
        lt = self.T(e.left.ty)
        a, b = self.expr(e.left), self.expr(e.right)
        k = self.kind(lt)
        if op in ("==", "!="):
            eq = self.eq(a, b, lt)
            return eq if op == "==" else f"(!{eq})"
        if op in ("<", "<=", ">", ">="):
            if k == "str":
                return f"(k_str_cmp({a}, {b}) {op} 0)"
            return f"({a} {op} {b})"
        if k == "float":
            return f"({a} {op} {b})"
        if k == "str":
            return f"k_str_concat({a}, {b})"
        if k == "list":
            return f"k_list_concat({a}, {b})"
        fn = {"+": "k_add", "-": "k_sub", "*": "k_mul", "/": "k_div", "%": "k_rem"}[op]
        return f"{fn}({a}, {b}, {e.line}, {e.col})"

    def e_IfExpr(self, e):
        t = self.T(e.ty)
        a = self.coerce(self.expr(e.then), self.T(e.then.ty), t)
        b = self.coerce(self.expr(e.els), self.T(e.els.ty), t)
        return f"(({self.expr(e.cond)}) ? ({a}) : ({b}))"

    def e_MethodCall(self, e):
        return self.expr(e.desugared)

    def e_Field(self, e):
        t = self.T(e.target.ty)
        fields = self.c.struct_fields(t)
        idx = [f for f, _ in fields].index(e.name)
        return self.from_s(f"({self.expr(e.target)})[{idx}]", self.T(e.ty))

    def e_Index(self, e):
        if e.target.kind == "Name":
            e.target.safe_read = True
        t = self.T(e.target.ty)
        k = self.kind(t)
        tgt, idx = self.expr(e.target), self.expr(e.index)
        if k == "list":
            return self.from_s(f"k_list_get({tgt}, {idx}, {e.line}, {e.col})", self.T(e.ty))
        if k == "str":
            return f"k_str_index({tgt}, {idx}, {e.line}, {e.col})"
        kt, vt = t.args
        m, ix, key = self.tmp(), self.tmp(), self.tmp()
        if self.kind(kt) == "str":
            fail = f"k_fail(\"E405\", {e.line}, {e.col}, \"map has no key \\\"%s\\\"\", {key}->data)"
        else:
            fail = f"k_fail(\"E405\", {e.line}, {e.col}, \"map has no key %lld\", (long long){key})"
        return (f"({{ KMap *{m} = {tgt}; {self.ctype(kt)} {key} = {idx}; int64_t {ix} = k_map_find({m}, {self.to_s(key, kt)}); "
                f"if ({ix} < 0) {fail}; {self.from_s(f'{m}->d->vals[{ix}]', vt)}; }})")

    def e_Try(self, e):
        t = self.tmp()
        inner = self.T(e.ty)
        wrap = getattr(e, "wrap", None)
        fail = f"k_err((KS)k_enum_new({self.tags[wrap]}, 1, {t}->f[0]))" if wrap else t
        return (f"({{ KEnum *{t} = {self.expr(e.value)}; if ({t}->tag == K_TAG_NONE || {t}->tag == K_TAG_ERR) return {fail}; "
                f"{self.from_s(f'{t}->f[0]', inner)}; }})")

    def e_With(self, e):
        t = self.T(e.ty)
        fields = self.c.struct_fields(t)
        names = [f for f, _ in fields]
        s = self.tmp()
        parts = [f"KS *{s} = k_struct_copy({self.expr(e.target)}, {len(fields)});"]
        for fname, val, _ in e.updates:
            i = names.index(fname)
            parts.append(f"{s}[{i}] = {self.to_s(self.expr(val), fields[i][1])};")
        return "({ " + " ".join(parts) + f" {s}; }})"

    def e_Lambda(self, e):
        t = self.T(e.ty)
        used = set()
        bound = set(p for p, _ in e.params)
        walk_names(e.body, used, bound)
        free = sorted(n for n in used - bound if n in self.ctx.locals)
        self.nlam += 1
        cname = f"kl_{self.nlam}"
        outer = self.ctx
        self.ctx = FnCtx(t.ret, True)
        lines = []
        for i, n in enumerate(free):
            ft = outer.locals[n]
            self.ctx.locals[n] = ft
            lines.append(f"{self.ctype(ft)} v_{n} = {self.from_s(f'self->env[{i}]', ft)};")
        for i, ((p, _), pt) in enumerate(zip(e.params, t.params)):
            self.ctx.locals[p] = pt
            lines.append(f"{self.ctype(pt)} v_{p} = {self.from_s(f'a{i}', pt)};")
        if e.block:
            self.block(e.body, lines, new_scope=False)
            lines.append("return 0;")
        else:
            bt = self.T(e.body.ty)
            code = self.coerce(self.expr(e.body), bt, t.ret)
            lines.append(f"return {self.to_s(code, t.ret)};")
        self.ctx = outer
        ps = ", ".join(["KClo *self"] + [f"KS a{i}" for i in range(len(e.params))])
        body = "\n".join("  " + l for l in lines)
        self.protos.append(f"static KS {cname}({ps})")
        self.defs.append(f"static KS {cname}({ps}) {{\n  (void)self;\n{body}\n}}\n")
        env = "".join(", " + self.to_s(f"v_{n}", outer.locals[n]) for n in free)
        return f"k_clo_new((void *){cname}, {len(free)}{env})"

    def call_clo(self, clo_code, arg_slots):
        c = self.tmp()
        sig = ", ".join(["KClo *"] + ["KS"] * len(arg_slots))
        args = "".join(", " + a for a in arg_slots)
        return f"({{ KClo *{c} = {clo_code}; ((KS (*)({sig})){c}->fn)({c}{args}); }})"

    def ordered_args(self, e, names):
        if e.named:
            by = {n: v for n, v, _ in e.named}
            return [by[n] for n in names]
        return list(e.args)

    def trait_target(self, e):
        """The impl function (and its type arguments) that a trait method call runs, for this instance."""
        trait, method = e.trait_ref
        name, targs = self.c.impl_call(trait, method, self.T(e.inst[0]))
        return name, [self.T(t) for t in targs]

    def e_Call(self, e):
        tk = getattr(e, "target_kind", "value")
        if tk == "trait":
            name, targs = self.trait_target(e)
            inst = self.request_fn(name, targs)
            sig = self.c.fns[name]
            args = self.ordered_args(e, [p for p, _ in self.c.fns[e.callee.name].params])
            sub = dict(zip(sig.tparams, targs))
            codes = [self.coerce(self.expr(a), self.T(a.ty), self._subst_c(pt, sub)) for a, (_, pt) in zip(args, sig.params)]
            return f"{inst}({', '.join(codes)})"
        if tk == "struct":
            name = e.callee.name
            t = self.T(e.ty)
            fields = self.c.struct_fields(t)
            args = self.ordered_args(e, [f for f, _ in fields])
            vals = "".join(", " + self.to_s(self.expr(a), ft) for a, (_, ft) in zip(args, fields))
            return f"k_struct_new({len(fields)}{vals})"
        if tk == "variant":
            name = e.callee.name
            t = self.T(e.ty)
            info = self.c.variants[name]
            mapping = dict(zip(info.tparams, t.args))
            ftypes = [self.T(substitute(ft, mapping)) for _, ft in info.fields]
            args = self.ordered_args(e, [f for f, _ in info.fields])
            vals = [self.to_s(self.expr(a), ft) for a, ft in zip(args, ftypes)]
            if name == "Some":
                return f"k_some({vals[0]})"
            if name == "Ok":
                return f"k_ok({vals[0]})"
            if name == "Err":
                return f"k_err({vals[0]})"
            return f"k_enum_new({self.tags[name]}, {len(vals)}" + "".join(", " + v for v in vals) + ")"
        if tk == "extern":
            d = self.extern_decls[e.callee.name]
            sig = self.c.fns[d.name]
            args = self.ordered_args(e, [p for p, _ in sig.params])
            pos = f"kx_line = {e.line}, kx_col = {e.col}, "
            if d.target.startswith("klar:sql."):
                self.uses_std = True
                self.features.add("sqlite")
                desc = self.desc(self.T(e.inst[0]))
                if d.target == "klar:sql.value":
                    return f"({pos}kx_sql_value({self.to_s(self.expr(args[0]), self.T(args[0].ty))}, &{desc}))"
                return f"({pos}kx_sql_read({self.expr(args[0])}, &{desc}))"
            if d.target.startswith("klar:json."):
                op = d.target.split(".", 1)[1]
                if op not in JSON_OPS:
                    self.unsupported(e, f"`{d.name}`")
                self.uses_std = True
                vt = self.T(e.inst[0])
                desc = self.desc(vt)
                if op in ("encode", "pretty", "of"):
                    code = self.to_s(self.expr(args[0]), self.T(args[0].ty))
                else:
                    code = self.expr(args[0])
                return f"({pos}{JSON_OPS[op]}({code}, &{desc}))"
            cname = self.native_extern(e, d)
            codes = []
            for a, (_, pt) in zip(args, sig.params):
                code = self.expr(a)
                codes.append(self.to_s(code, self.T(a.ty)) if isinstance(prune(pt), TVar) else code)
            rt = self.T(e.ty)
            call = f"{cname}({', '.join(codes)})"
            if isinstance(prune(sig.ret), TVar):
                call = self.from_s(call, rt)
            if self.kind(rt) == "extern":
                return f"({pos}(void*){call})"
            return f"({pos}{call})"
        if tk == "fn":
            name = e.callee.name
            if name in self.fns:
                sig = self.c.fns[name]
                targs = [self.T(x) for x in getattr(e, "inst", [])]
                inst = self.request_fn(name, targs)
                args = self.ordered_args(e, [p for p, _ in sig.params])
                sub = dict(zip(sig.tparams, targs))
                codes = [self.coerce(self.expr(a), self.T(a.ty), self._subst_c(pt, sub))
                         for a, (_, pt) in zip(args, sig.params)]
                return f"{inst}({', '.join(codes)})"
            sig = self.c.fns[name]
            args = self.ordered_args(e, [p for p, _ in sig.params])
            if name == "len" and args and args[0].kind == "Name":
                args[0].safe_read = True
            return self.builtin(name, [self.expr(a) for a in args], [self.T(a.ty) for a in args], self.T(e.ty), e)
        ct = self.T(e.callee.ty)
        slots = [self.to_s(self.expr(a), pt) for a, pt in zip(e.args, ct.params)]
        return self.from_s(self.call_clo(self.expr(e.callee), slots), ct.ret)

    # ------------------------------------------------------------------ match
    def e_Match(self, e):
        st = self.T(e.scrutinee.ty)
        rt = self.T(e.ty) if e.ty is not None else INT
        m, r = self.tmp(), self.tmp()
        lines = [f"{self.ctype(st)} {m} = {self.expr(e.scrutinee)};", f"{self.ctype(rt)} {r} = ({self.ctype(rt)})0;"]
        first = True
        for arm in e.arms:
            conds, binds = [], []
            self.pattern(arm.pattern, m, st, conds, binds)
            cond = " && ".join(conds) if conds else "1"
            lines.append(("if" if first else "else if") + f" ({cond}) {{")
            first = False
            lines.extend(binds)
            if arm.body.kind == "Block":
                self.block(arm.body, lines, new_scope=False)
            else:
                bt = self.T(arm.body.ty)
                code = self.expr(arm.body)
                if self.is_never(bt) or self.is_never(rt):
                    lines.append(f"{code};")
                else:
                    lines.append(f"{r} = {code};")
            lines.append("}")
        lines.append(f"else k_fail(\"E306\", {e.line}, {e.col}, \"no match arm matched\");")
        return "({ " + "\n".join(lines) + f"\n{r}; }})"

    def pattern(self, p, code, t, conds, binds):
        k = p.kind
        if k == "PWild":
            return
        if k == "PBind":
            self.ctx.locals[p.name] = t
            binds.append(f"{self.ctype(t)} v_{p.name} = {code};")
            return
        if k == "PLit":
            v = p.value
            if isinstance(v, bool):
                conds.append(f"({code}) == {1 if v else 0}")
            elif isinstance(v, str):
                conds.append(f"k_str_eq({code}, {self.strlit(v)})")
            elif isinstance(v, float):
                conds.append(f"({code}) == {repr(v)}")
            else:
                conds.append(f"({code}) == INT64_C({v})")
            return
        if k == "PTuple":
            for i, (sub, at) in enumerate(zip(p.subs, t.args)):
                st = self.T(at)
                self.pattern(sub, self.from_s(f"({code})[{i}]", st), st, conds, binds)
            return
        info = self.c.variants[p.name]
        conds.append(f"({code})->tag == {self.tags[p.name]}")
        mapping = dict(zip(info.tparams, t.args))
        for i, (sub, (_, ft)) in enumerate(zip(p.subs, info.fields)):
            st = self.T(substitute(ft, mapping))
            self.pattern(sub, self.from_s(f"({code})->f[{i}]", st), st, conds, binds)

    # ------------------------------------------------------------------ type-directed helpers
    def eq(self, a, b, t):
        k = self.kind(t)
        if k in ("int", "float"):
            return f"(({a}) == ({b}))"
        if k == "str":
            return f"k_str_eq({a}, {b})"
        if k == "extern":
            return f"(({a}) == ({b}))"
        return f"{self.eq_helper(t)}({a}, {b})"

    def eq_helper(self, t):
        key = ("eq", show(t))
        if key in self.helpers:
            return self.helpers[key]
        name = f"k_eq_{len(self.helpers)}"
        self.helpers[key] = name
        ct = self.ctype(t)
        self.protos.append(f"static int {name}({ct} a, {ct} b)")
        k = self.kind(t)
        body = []
        if k == "list":
            et = t.args[0]
            body.append("if (a->len != b->len) return 0;")
            body.append("for (int64_t i = 0; i < a->len; i++) if (!"
                        + self.eq(self.from_s("KL_DATA(a)[i]", et), self.from_s("KL_DATA(b)[i]", et), et) + ") return 0;")
        elif k == "map":
            vt = t.args[1]
            body.append("if (a->count != b->count) return 0;")
            body.append("a = k_map_compact(a);")
            body.append("for (int64_t i = 0; i < a->len; i++) { int64_t j = k_map_find(b, a->d->keys[i]); if (j < 0) return 0;"
                        f" if (!{self.eq(self.from_s('a->d->vals[i]', vt), self.from_s('b->d->vals[j]', vt), vt)}) return 0; }}")
        elif k == "struct":
            for i, (_, ft) in enumerate(self.c.struct_fields(t)):
                ft = self.T(ft)
                body.append(f"if (!{self.eq(self.from_s(f'a[{i}]', ft), self.from_s(f'b[{i}]', ft), ft)}) return 0;")
        elif k == "enum":
            body.append("if (a == b) return 1;")
            body.append("if (a->tag != b->tag) return 0;")
            body.append("switch (a->tag) {")
            for vname, ftypes in self.variant_types(t):
                body.append(f"case {self.tags[vname]}:")
                for i, ft in enumerate(ftypes):
                    body.append(f"  if (!{self.eq(self.from_s(f'a->f[{i}]', ft), self.from_s(f'b->f[{i}]', ft), ft)}) return 0;")
                body.append("  return 1;")
            body.append("}")
        else:
            body.append("return a == b;")
        self.defs.append(f"static int {name}({ct} a, {ct} b) {{\n  " + "\n  ".join(body) + "\n  return 1;\n}\n")
        return name

    def variant_types(self, t):
        if t.name == "Option":
            return [("Some", [t.args[0]]), ("None", [])]
        if t.name == "Result":
            return [("Ok", [t.args[0]]), ("Err", [t.args[1]])]
        mapping = dict(zip(self.c.tparams_of.get(t.name, []), t.args))
        return [(vn, [self.T(substitute(ft, mapping)) for _, ft in self.c.variants[vn].fields]) for vn in self.c.enums[t.name]]

    def show_into(self, b, v, t, nested):
        """C statement appending the display form of value `v` (type t) to KSB* b."""
        k = self.kind(t)
        tn = prune(t).name if isinstance(prune(t), TCon) else ""
        if tn == "bool":
            return f"ksb_c({b}, ({v}) ? \"true\" : \"false\");"
        if tn in ("unit", "never"):
            return f"(void)({v}); ksb_c({b}, \"()\");"
        if k == "int":
            return f"ksb_int({b}, {v});"
        if k == "float":
            return f"ksb_float({b}, {v});"
        if k == "str":
            return f"ksb_quoted({b}, {v});" if nested else f"ksb_str({b}, {v});"
        if k == "fn":
            return f"(void)({v}); ksb_c({b}, \"<fn>\");"
        return f"{self.show_helper(t)}({b}, {v});"

    def show_helper(self, t):
        key = ("show", show(t))
        if key in self.helpers:
            return self.helpers[key]
        name = f"k_show_{len(self.helpers)}"
        self.helpers[key] = name
        ct = self.ctype(t)
        self.protos.append(f"static void {name}(KSB *b, {ct} v)")
        k = self.kind(t)
        body = []
        if k == "list":
            et = t.args[0]
            body.append("ksb_c(b, \"[\");")
            body.append("for (int64_t i = 0; i < v->len; i++) { if (i) ksb_c(b, \", \"); "
                        + self.show_into("b", self.from_s("KL_DATA(v)[i]", et), et, True) + " }")
            body.append("ksb_c(b, \"]\");")
        elif k == "map":
            kt, vt = t.args
            body.append("if (v->count == 0) { ksb_c(b, \"[:]\"); return; }")
            body.append("v = k_map_compact(v);")
            body.append("ksb_c(b, \"[\");")
            body.append("for (int64_t i = 0; i < v->len; i++) { if (i) ksb_c(b, \", \"); "
                        + self.show_into("b", self.from_s("v->d->keys[i]", kt), kt, True) + " ksb_c(b, \": \"); "
                        + self.show_into("b", self.from_s("v->d->vals[i]", vt), vt, True) + " }")
            body.append("ksb_c(b, \"]\");")
        elif k == "struct" and is_tuple_name(t.name):
            body.append("ksb_c(b, \"(\");")
            for i, (fname, ft) in enumerate(self.c.struct_fields(t)):
                ft = self.T(ft)
                body.append(("ksb_c(b, \", \"); " if i else "") + self.show_into("b", self.from_s(f"v[{i}]", ft), ft, True))
            body.append("ksb_c(b, \")\");")
        elif k == "struct":
            body.append(f"ksb_c(b, \"{t.name}(\");")
            for i, (fname, ft) in enumerate(self.c.struct_fields(t)):
                ft = self.T(ft)
                sep = ", " if i else ""
                body.append(f"ksb_c(b, \"{sep}{fname}: \"); " + self.show_into("b", self.from_s(f"v[{i}]", ft), ft, True))
            body.append("ksb_c(b, \")\");")
        elif k == "enum":
            body.append("switch (v->tag) {")
            for vname, ftypes in self.variant_types(t):
                body.append(f"case {self.tags[vname]}:")
                if not ftypes:
                    body.append(f"  ksb_c(b, \"{vname}\"); return;")
                    continue
                body.append(f"  ksb_c(b, \"{vname}(\");")
                fnames = [f for f, _ in self.c.variants[vname].fields]
                for i, ft in enumerate(ftypes):
                    sep = ", " if i else ""
                    label = "" if vname in ("Some", "Ok", "Err") else f"{fnames[i]}: "
                    body.append(f"  ksb_c(b, \"{sep}{label}\"); " + self.show_into("b", self.from_s(f"v->f[{i}]", ft), ft, True))
                body.append("  ksb_c(b, \")\"); return;")
            body.append("}")
        else:
            body.append("ksb_c(b, \"<value>\");")
        self.defs.append(f"static void {name}(KSB *b, {ct} v) {{\n  " + "\n  ".join(body) + "\n}\n")
        return name

    def to_str(self, code, t):
        if self.kind(t) == "str":
            return code
        b = self.tmp()
        return f"({{ KSB {b}; ksb_init(&{b}); {self.show_into('&' + b, code, t, False)} ksb_done(&{b}); }})"

    # ------------------------------------------------------------------ built-ins
    def builtin(self, name, a, ts, rt, e):
        """C expression for built-in `name` applied to argument codes `a` with types `ts`."""
        L = f"{e.line}, {e.col}"
        k0 = self.kind(ts[0]) if ts else None

        def elem(i=0):
            return ts[i].args[0]

        if name == "print":
            t = ts[0]
            if k0 == "str":
                return f"(k_print_str({a[0]}), (int64_t)0)"
            if k0 == "int" and t.name == "int":
                return f"(printf(\"%lld\\n\", (long long)({a[0]})), (int64_t)0)"
            return f"(k_print_str({self.to_str(a[0], t)}), (int64_t)0)"
        if name == "str":
            return self.to_str(a[0], ts[0])
        if name == "panic":
            return f"(k_fail(\"E403\", {L}, \"panic: %s\", ({a[0]})->data), (int64_t)0)"
        if name == "read_line":
            return "k_read_line()"
        if name in ("read_file", "write_file") and self.target == "wasm":
            raise KlarError("E501", f"`{name}` cannot run in WebAssembly (there is no file system)", e.line, e.col,
                            "read files in the JavaScript part of the app and pass their contents in", self.file)
        if name == "read_file":
            return f"k_read_file({a[0]})"
        if name == "write_file":
            return f"k_write_file({a[0]}, {a[1]})"
        if name == "args":
            return "k_args()"
        if name == "len":
            if k0 == "str":
                return f"k_str_cps({a[0]})"
            if k0 == "map":
                return f"(({a[0]})->count)"
            return f"(({a[0]})->len)"
        if name == "push":
            return f"k_list_push({a[0]}, {self.to_s(a[1], ts[1])})"
        if name == "set_at":
            return f"k_list_set_at({a[0]}, {a[1]}, {self.to_s(a[2], ts[2])}, {L})"
        if name == "get":
            t1 = self.tmp()
            if k0 == "list":
                i = self.tmp()
                return (f"({{ KList *{t1} = {a[0]}; int64_t {i} = {a[1]}; ({i} >= 0 && {i} < {t1}->len) ? "
                        f"k_some(KL_DATA({t1})[{i}]) : K_NONE; }})")
            ix = self.tmp()
            return (f"({{ KMap *{t1} = {a[0]}; int64_t {ix} = k_map_find({t1}, {self.to_s(a[1], ts[1])}); "
                    f"{ix} >= 0 ? k_some({t1}->d->vals[{ix}]) : K_NONE; }})")
        if name == "slice":
            if k0 == "str":
                return f"k_str_slice({a[0]}, {a[1]}, {a[2]})"
            return f"k_list_slice({a[0]}, {a[1]}, {a[2]})"
        if name == "contains":
            if k0 == "str":
                return f"k_str_contains({a[0]}, {a[1]})"
            et = elem()
            l, x, i, r = self.tmp(), self.tmp(), self.tmp(), self.tmp()
            eq = self.eq(self.from_s(f"KL_DATA({l})[{i}]", et), x, et)
            return (f"({{ KList *{l} = {a[0]}; {self.ctype(et)} {x} = {a[1]}; int64_t {r} = 0; "
                    f"for (int64_t {i} = 0; {i} < {l}->len; {i}++) if ({eq}) {{ {r} = 1; break; }} {r}; }})")
        if name == "index_of":
            et = elem()
            l, x, i, r = self.tmp(), self.tmp(), self.tmp(), self.tmp()
            eq = self.eq(self.from_s(f"KL_DATA({l})[{i}]", et), x, et)
            return (f"({{ KList *{l} = {a[0]}; {self.ctype(et)} {x} = {a[1]}; KEnum *{r} = K_NONE; "
                    f"for (int64_t {i} = 0; {i} < {l}->len; {i}++) if ({eq}) {{ {r} = k_some((KS){i}); break; }} {r}; }})")
        if name == "reverse":
            return f"k_str_reverse({a[0]})" if k0 == "str" else f"k_list_reverse({a[0]})"
        if name == "range":
            return f"k_range({a[0]}, {a[1]})"
        if name in ("map", "filter", "any", "all", "find"):
            l, f, i, r, x = self.tmp(), self.tmp(), self.tmp(), self.tmp(), self.tmp()
            call = f"((KS (*)(KClo *, KS)){f}->fn)({f}, {x})"
            head = f"KList *{l} = {a[0]}; KClo *{f} = {a[1]};"
            loop = f"for (int64_t {i} = 0; {i} < {l}->len; {i}++) {{ KS {x} = KL_DATA({l})[{i}];"
            if name == "map":
                return f"({{ {head} KList *{r} = k_list_with_cap({l}->len, {l}->len); {loop} KL_DATA({r})[{i}] = {call}; }} {r}; }})"
            if name == "filter":
                return (f"({{ {head} KList *{r} = k_list_with_cap(0, {l}->len); {loop} if ({call}) "
                        f"{{ KL_DATA({r})[{r}->len++] = {x}; {r}->buf->used = {r}->len; }} }} {r}; }})")
            if name == "any":
                return f"({{ {head} int64_t {r} = 0; {loop} if ({call}) {{ {r} = 1; break; }} }} {r}; }})"
            if name == "all":
                return f"({{ {head} int64_t {r} = 1; {loop} if (!{call}) {{ {r} = 0; break; }} }} {r}; }})"
            return f"({{ {head} KEnum *{r} = K_NONE; {loop} if ({call}) {{ {r} = k_some({x}); break; }} }} {r}; }})"
        if name == "fold":
            l, f, i, acc = self.tmp(), self.tmp(), self.tmp(), self.tmp()
            return (f"({{ KList *{l} = {a[0]}; KS {acc} = {self.to_s(a[1], ts[1])}; KClo *{f} = {a[2]}; "
                    f"for (int64_t {i} = 0; {i} < {l}->len; {i}++) {acc} = ((KS (*)(KClo *, KS, KS)){f}->fn)({f}, {acc}, KL_DATA({l})[{i}]); "
                    f"{self.from_s(acc, rt)}; }})")
        if name == "sort":
            return f"k_sort({a[0]}, {self.sort_kind(elem(), e)})"
        if name == "sort_by":
            kt = prune(ts[1]).ret
            l, f, i, keys = self.tmp(), self.tmp(), self.tmp(), self.tmp()
            return (f"({{ KList *{l} = {a[0]}; KClo *{f} = {a[1]}; KS *{keys} = (KS *)malloc(sizeof(KS) * ({l}->len + 1)); "
                    f"for (int64_t {i} = 0; {i} < {l}->len; {i}++) {keys}[{i}] = ((KS (*)(KClo *, KS)){f}->fn)({f}, KL_DATA({l})[{i}]); "
                    f"KList *_sr = k_sort_keys({l}, {keys}, {self.sort_kind(kt, e)}); free({keys}); _sr; }})")
        if name == "sum":
            et = elem()
            l, i, s = self.tmp(), self.tmp(), self.tmp()
            if self.kind(et) == "float":
                return (f"({{ KList *{l} = {a[0]}; double {s} = 0.0; for (int64_t {i} = 0; {i} < {l}->len; {i}++) "
                        f"{s} += k_s2d(KL_DATA({l})[{i}]); {s}; }})")
            return (f"({{ KList *{l} = {a[0]}; int64_t {s} = 0; for (int64_t {i} = 0; {i} < {l}->len; {i}++) "
                    f"{s} = k_add({s}, (int64_t)KL_DATA({l})[{i}], {L}); {s}; }})")
        if name == "join":
            return f"k_join({a[0]}, {a[1]})"
        simple = {"split": "k_split", "chars": "k_str_chars", "trim": "k_trim", "upper": "k_upper", "lower": "k_lower",
                  "starts_with": "k_starts_with", "ends_with": "k_ends_with", "replace": "k_replace", "repeat": "k_repeat",
                  "parse_int": "k_parse_int", "parse_float": "k_parse_float", "floor": "k_floor", "round": "k_round",
                  "sqrt": "k_sqrt", "keys": "k_map_keys", "values": "k_map_values", "entries": "k_map_entries"}
        if name in simple:
            return f"{simple[name]}({', '.join(a)})"
        if name == "to_float":
            return f"((double)({a[0]}))"
        if name == "abs":
            if k0 == "float":
                return f"fabs({a[0]})"
            x = self.tmp()
            return f"({{ int64_t {x} = {a[0]}; {x} < 0 ? k_neg({x}, {L}) : {x}; }})"
        if name in ("min", "max"):
            x, y = self.tmp(), self.tmp()
            ct = self.ctype(ts[0])
            op = "<" if name == "min" else ">"
            cmp = f"k_str_cmp({y}, {x}) {op} 0" if k0 == "str" else f"{y} {op} {x}"
            return f"({{ {ct} {x} = {a[0]}; {ct} {y} = {a[1]}; ({cmp}) ? {y} : {x}; }})"
        if name == "has":
            return f"(k_map_find({a[0]}, {self.to_s(a[1], ts[1])}) >= 0)"
        if name == "put":
            return f"k_map_put({a[0]}, {self.to_s(a[1], ts[1])}, {self.to_s(a[2], ts[2])})"
        if name == "remove":
            return f"k_map_remove({a[0]}, {self.to_s(a[1], ts[1])})"
        if name in ("unwrap", "unwrap_or"):
            o = self.tmp()
            if name == "unwrap_or":
                return f"({{ KEnum *{o} = {a[0]}; ({o}->tag == K_TAG_SOME || {o}->tag == K_TAG_OK) ? {self.from_s(f'{o}->f[0]', rt)} : ({a[1]}); }})"
            fail = (f"if ({o}->tag == K_TAG_NONE) k_fail(\"E403\", {L}, \"unwrap() called on None\"); "
                    f"if ({o}->tag == K_TAG_ERR) k_fail(\"E403\", {L}, \"unwrap() called on Err\");")
            return f"({{ KEnum *{o} = {a[0]}; {fail} {self.from_s(f'{o}->f[0]', rt)}; }})"
        if name == "ok_or":
            o = self.tmp()
            return f"({{ KEnum *{o} = {a[0]}; {o}->tag == K_TAG_SOME ? k_ok({o}->f[0]) : k_err({self.to_s(a[1], ts[1])}); }})"
        tagtest = {"is_some": "K_TAG_SOME", "is_none": "K_TAG_NONE", "is_ok": "K_TAG_OK", "is_err": "K_TAG_ERR"}
        if name in tagtest:
            return f"(({a[0]})->tag == {tagtest[name]})"
        self.unsupported(e, f"built-in `{name}`")

    def sort_kind(self, t, e):
        k = self.kind(t)
        if k == "float":
            return 1
        if k == "str":
            return 2
        if k == "int":
            return 0
        self.unsupported(e, f"sorting by `{show(t)}` keys")


def node_file(node, default):
    return default


def walk_names(node, used, bound):
    """Collect names referenced (used) and introduced (bound) inside an expression tree."""
    if isinstance(node, Node):
        k = node.kind
        if k == "Name":
            used.add(node.name)
        elif k == "Let":
            bound.add(node.name)
        elif k == "For":
            bound.add(node.var)
        elif k == "PBind":
            bound.add(node.name)
        elif k == "Lambda":
            for p, _ in node.params:
                bound.add(p)
        for key, v in node.__dict__.items():
            if key in ("ty", "inst"):
                continue
            walk_names(v, used, bound)
    elif isinstance(node, (list, tuple)):
        for x in node:
            walk_names(x, used, bound)


# Exported helpers the JavaScript side uses to build and read strings and number lists in linear memory.
WASM_GLUE = """
K_EXPORT("klar_str_new") KStr *klar_str_new(int n) { return k_str_alloc(n); }
K_EXPORT("klar_str_data") char *klar_str_data(KStr *s) { return s->data; }
K_EXPORT("klar_str_len") int klar_str_len(KStr *s) { return (int)s->len; }
K_EXPORT("klar_list_new") KList *klar_list_new(int n) { return k_list_with_cap(n, n); }
K_EXPORT("klar_list_data") KS *klar_list_data(KList *l) { return k_list_data(l); }
K_EXPORT("klar_list_len") int klar_list_len(KList *l) { return (int)l->len; }
"""


def compile_to_c(prog, file, info=None, target="native", exports=None):
    c = Compiler(prog, file, target, exports)
    out = c.compile()
    if info is not None:
        info["libs"] = c.libs()
        if target == "wasm":
            info["exports"] = c.export_sigs
    return out
