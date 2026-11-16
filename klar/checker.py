"""Static checker: names, types, effects, exhaustiveness, definite return."""

from .errors import KlarError
from .syntax import parse, Node, MAX_TUPLE, tuple_name, is_tuple_name
from .prelude import PRELUDE, OVERLOADED
from .types import (TCon, TFn, TVar, INT, FLOAT, STR, BOOL, UNIT, NEVER, PRIMS, GENERIC_ARITY,
                    list_of, option_of, prune, resolve, show, has_vars, unify, substitute)


class FnSig:
    def __init__(self, name, tparams, params, ret, effects, builtin=False, node=None):
        self.name = name
        self.tparams = tparams          # list of names
        self.params = params            # list of (name, type)
        self.ret = ret
        self.effects = effects
        self.builtin = builtin
        self.node = node
        self.bounds = {}                # type parameter -> list of trait names
        self.trait = None               # for a trait method: the trait's name

    def instantiate(self, node=None):
        mapping = {tp: TVar() for tp in self.tparams}
        if node is not None:
            node.inst = [mapping[tp] for tp in self.tparams]   # used by the compiler to monomorphise
        params = [(n, substitute(t, mapping)) for n, t in self.params]
        return params, substitute(self.ret, mapping)


class ImplInfo:
    def __init__(self, node, trait, head, tparams, bounds, fn_names):
        self.node = node
        self.trait = trait
        self.head = head
        self.tparams = tparams
        self.bounds = bounds
        self.fn_names = fn_names        # method -> name of the generated function
        self.ty = None                  # the implementing type, with the impl's type parameters in it


def match_type(pattern, t, tparams, binding):
    """Bind the impl's type parameters so that `pattern` equals `t`; False if they cannot match."""
    pattern, t = prune(pattern), prune(t)
    if isinstance(pattern, TCon) and not pattern.args and pattern.name in tparams:
        if pattern.name in binding:
            return show(binding[pattern.name]) == show(t)
        binding[pattern.name] = t
        return True
    if isinstance(pattern, TCon):
        return (isinstance(t, TCon) and t.name == pattern.name and len(t.args) == len(pattern.args)
                and all(match_type(a, b, tparams, binding) for a, b in zip(pattern.args, t.args)))
    if isinstance(pattern, TFn):
        return (isinstance(t, TFn) and len(t.params) == len(pattern.params)
                and all(match_type(a, b, tparams, binding) for a, b in zip(pattern.params, t.params))
                and match_type(pattern.ret, t.ret, tparams, binding))
    return False


def type_head(t):
    t = prune(t)
    return "fn" if isinstance(t, TFn) else t.name if isinstance(t, TCon) else None


class VariantInfo:
    def __init__(self, enum, name, fields, tparams=()):
        self.enum = enum        # enum name
        self.name = name
        self.fields = fields    # list of (name, type)
        self.tparams = list(tparams)


BUILTIN_VARIANTS = {
    "Some": VariantInfo("Option", "Some", [("value", TCon("T"))], ["T"]),
    "None": VariantInfo("Option", "None", [], ["T"]),
    "Ok": VariantInfo("Result", "Ok", [("value", TCon("T"))], ["T", "E"]),
    "Err": VariantInfo("Result", "Err", [("error", TCon("E"))], ["T", "E"]),
}


class FnCtx:
    def __init__(self, ret, io, is_lambda=False, name=""):
        self.ret = ret
        self.io = io
        self.is_lambda = is_lambda
        self.name = name
        self.loop_depth = 0
        self.lets = []
        self.used_io = False


class Checker:
    def __init__(self, file=None):
        self.file = file
        self.errors = []
        self.json_reads = []    # json_decode / json_into calls: their type argument must be known in the end
        self.traits = {}        # trait name -> Trait decl
        self.impls = {}         # (trait, type head) -> ImplInfo
        self.obligations = []   # (type, trait, node, bounds in scope, module): checked once all types are known
        self.cur_bounds = {}
        self.fns = {}
        self.structs = {}       # name -> list of (field, type)
        self.enums = {}         # name -> list of variant names
        self.variants = dict(BUILTIN_VARIANTS)
        self.tparams_of = {}    # generic struct/enum name -> list of type parameter names
        self.extern_types = set()
        self.duplicates = set()
        self.def_module = {}    # top-level name -> module path that defines it
        self.module_name = {}   # module path -> dotted name used in `use`
        self.visible_in = {}    # module path -> set of imported names
        self.cur_module = None
        self.scopes = []
        self.lambda_base = None   # scope depth where the innermost anonymous function starts
        self.ctx = None
        # for the language server: (module, node, name, kind, info) for every name the checker resolves, where
        # kind is "decl" (info = (type, defining node)), "local"/"fn"/"type"/"variant" (a use) or "field"
        self.refs = []
        self.decl_nodes = {}    # top-level type, variant or trait name -> its declaration node
        self._load_prelude()

    # ---------------------------------------------------------- utilities
    def err(self, code, msg, node, hint=None):
        raise KlarError(code, msg, node.line, node.col, hint, self.file)

    def _load_prelude(self):
        for n in range(2, MAX_TUPLE + 1):          # (A, B) is the built-in struct Tuple2[A, B] with fields 0 and 1
            tps = "ABCDEF"[:n]
            self.structs[tuple_name(n)] = [(str(i), TCon(tp)) for i, tp in enumerate(tps)]
            self.tparams_of[tuple_name(n)] = list(tps)
        prog = parse(PRELUDE, "<prelude>")
        for d in prog.decls:
            tps = set(d.tparams)
            params = [(n, self.to_type(t, tps)) for n, t in d.params]
            self.fns[d.name] = FnSig(d.name, d.tparams, params, self.to_type(d.ret, tps), d.effects, builtin=True)

    def to_type(self, tn, tparams=frozenset()):
        if tn.kind == "TFn":
            return TFn([self.to_type(p, tparams) for p in tn.params], self.to_type(tn.ret, tparams), tn.io)
        name = tn.name
        if name in tparams:
            if tn.args:
                self.err("E301", f"type parameter {name} cannot take arguments", tn)
            return TCon(name)
        if name in PRIMS:
            if tn.args:
                self.err("E301", f"`{name}` does not take type arguments", tn)
            return PRIMS[name]
        if name in GENERIC_ARITY:
            if len(tn.args) != GENERIC_ARITY[name]:
                example = {"list": "list[int]", "map": "map[str, int]", "Option": "Option[int]", "Result": "Result[int, str]"}[name]
                self.err("E301", f"`{name}` needs {GENERIC_ARITY[name]} type argument(s)", tn, f"for example: {example}")
            return TCon(name, [self.to_type(a, tparams) for a in tn.args])
        if name in self.structs or name in self.enums or name in self.extern_types:
            self.check_vis(name, tn)
            self.note(tn, name, "type")
            tps = self.tparams_of.get(name, [])
            if len(tn.args) != len(tps):
                if not tps:
                    self.err("E301", f"`{name}` does not take type arguments", tn)
                self.err("E301", f"`{name}` needs {len(tps)} type argument(s): {name}[{', '.join(tps)}]", tn)
            return TCon(name, [self.to_type(a, tparams) for a in tn.args])
        hint = None
        lower = {"Int": "int", "String": "str", "string": "str", "Float": "float", "Bool": "bool", "boolean": "bool",
                 "List": "list", "Map": "map", "Dict": "map", "dict": "map", "Vec": "list", "i32": "int", "i64": "int",
                 "f64": "float", "void": "unit", "None": "unit", "option": "Option", "result": "Result"}
        if name in lower:
            hint = f"did you mean `{lower[name]}`?"
        else:
            cands = list(PRIMS) + list(GENERIC_ARITY) + list(self.structs) + list(self.enums)
            hint = self.suggest(name, cands)
        self.err("E201", f"unknown type `{name}`", tn, hint)

    def fn_visible(self, name):
        mod = self.def_module.get(name)
        return mod is None or mod == self.cur_module or name in self.visible_in.get(self.cur_module, ())

    def obligate(self, sig, node):
        """Calling a function with bounded type parameters (or a trait method) obliges its type arguments."""
        if not sig.bounds:
            return
        for tp, inst in zip(sig.tparams, node.inst):
            for trait in sig.bounds.get(tp, ()):
                self.obligations.append((inst, trait, node, self.cur_bounds, self.cur_module, self.file))

    def satisfies(self, t, trait, bounds, node, depth=0):
        """None if t implements trait, else the error message."""
        t = prune(t)
        if isinstance(t, TVar):
            return f"cannot tell which type should implement {trait} here"
        if isinstance(t, TCon) and not t.args and t.name in bounds:
            if trait in bounds[t.name]:
                return None
            return f"type parameter `{t.name}` must implement {trait}; declare it as `{t.name}: {trait}`"
        info = self.impls.get((trait, type_head(t)))
        binding = {}
        if info is None or info.ty is None or not match_type(info.ty, t, info.tparams, binding):
            return f"`{show(t)}` does not implement {trait}"
        if depth > 50:
            return None
        for tp, traits in info.bounds.items():
            for tr in traits:
                why = self.satisfies(binding.get(tp, TVar()), tr, bounds, node, depth + 1)
                if why:
                    return f"`{show(t)}` implements {trait} only when {tp} implements {tr}: {why}"
        return None

    def impl_call(self, trait, method, t):
        """The generated function implementing `method` for the concrete type t, and its type arguments."""
        t = resolve(t)
        info = self.impls[(trait, type_head(t))]
        binding = {}
        match_type(info.ty, t, info.tparams, binding)
        return info.fn_names[method], [binding[tp] for tp in info.tparams]

    def check_vis(self, name, node):
        """Names defined in another module must be imported with `use` before they are used."""
        mod = self.def_module.get(name)
        if mod is None or name in self.duplicates or mod == self.cur_module or name in self.visible_in.get(self.cur_module, ()):
            return
        mname = self.module_name.get(mod, mod)
        sig = self.fns.get(name)
        if sig is not None and sig.trait:
            self.err("E201", f"`{name}` is a method of trait {sig.trait} from module `{mname}`, which is not imported here", node,
                     f"add `use {mname} {{ {sig.trait} }}` at the top of this file (importing a trait imports its methods)")
        self.err("E201", f"`{name}` is defined in module `{mname}` but not imported here", node,
                 f"add `use {mname} {{ {name} }}` at the top of this file")

    def define(self, name, d):
        """Register a top-level name; names are unique across the whole program."""
        if name in self.def_module and self.def_module[name] != d.module:
            self.duplicates.add(name)
            other = self.module_name.get(self.def_module[name], self.def_module[name])
            self.err("E202", f"`{name}` is already defined in module `{other}`", d,
                     "every top-level name is unique across the whole program (so a search finds exactly one "
                     "definition); rename one of them, e.g. with a module prefix")
        self.def_module[name] = d.module

    def struct_fields(self, t):
        """Field list of struct type `t` with its type arguments substituted."""
        fields = self.structs[t.name]
        tps = self.tparams_of.get(t.name, [])
        if not tps:
            return fields
        mapping = dict(zip(tps, t.args))
        return [(f, substitute(ft, mapping)) for f, ft in fields]

    @staticmethod
    def suggest(name, candidates):
        import difflib
        m = difflib.get_close_matches(name, candidates, n=1, cutoff=0.6)
        return f"did you mean `{m[0]}`?" if m else None

    def expect(self, actual, expected, node, what="expression"):
        if not unify(actual, expected):
            a, e = show(actual), show(expected)
            hint = None
            if {a, e} == {"int", "float"}:
                hint = "Klar never mixes int and float; convert with to_float(n), floor(x) or round(x)"
            elif a == f"list[{e}]":
                hint = "this is a whole list; join two lists with `xs + ys` (push adds one element)"
            elif e == "str" and a != "str":
                hint = "convert with str(value) or use interpolation: \"{value}\""
            elif a == "unit" and e != "unit":
                hint = "this produces no value; did you forget to return something?"
            elif a.startswith("Option[") and not e.startswith("Option["):
                hint = "unpack the Option with match, unwrap_or(o, default), or the `?` operator"
            elif a.startswith("Result[") and not e.startswith("Result["):
                hint = "unpack the Result with match or the `?` operator"
            self.err("E301", f"type mismatch: {what} has type `{a}` but `{e}` is expected", node, hint)

    # ---------------------------------------------------------- scopes
    def lookup(self, name):
        for s in reversed(self.scopes):
            if name in s:
                return s[name]
        return None

    def note(self, node, name, kind, info=None):
        self.refs.append((self.cur_module, node, name, kind, info))

    def declare(self, name, ty, mutable, node):
        if self.lookup(name) is not None:
            self.err("E202", f"`{name}` is already defined in this function", node,
                     f"Klar forbids shadowing; choose a new name like `{name}2` or `new_{name}`, or declare it with `var` and reassign")
        if name in self.fns and not self.fns[name].builtin and self.fn_visible(name):
            self.err("E202", f"`{name}` is already the name of a function", node, "choose a different variable name")
        self.scopes[-1][name] = (ty, mutable, node)
        self.note(node, name, "decl", (ty, node))

    # ---------------------------------------------------------- program
    def check_program(self, prog):
        modules = getattr(prog, "modules", {})       # path -> dotted name
        self.module_name.update(modules)
        for d in prog.decls:
            if not hasattr(d, "module"):
                d.module = self.file
        self.cur_module = None

        def guard(d, fn):
            self.file = d.module
            self.cur_module = d.module
            try:
                fn(d)
            except KlarError as e:
                self.errors.append(e)

        # pass 0: traits, and impl methods turned into ordinary generic functions
        generated = []
        for d in prog.decls:
            if d.kind == "Trait":
                self.traits.setdefault(d.name, d)
                for m in d.methods:
                    m.module = d.module
            elif d.kind == "Impl":
                head = d.ty.name if d.ty.kind == "TName" else "fn"
                names = {}
                for m in d.methods:
                    fname = f"{m.name}__{d.trait}__{head}"
                    while any(g.name == fname for g in generated):     # a duplicate impl is reported once, below
                        fname += "_"
                    names[m.name] = fname
                    f = Node("Fn", m.line, m.col, name=fname, tparams=list(d.tparams) + list(m.tparams), params=m.params,
                             ret=m.ret, effects=m.effects, body=m.body, bounds=dict(d.bounds))
                    f.module = d.module
                    f.impl_of = (d.trait, m.name, d)
                    f.own_tparams = m.tparams
                    generated.append(f)
                d.info = ImplInfo(d, d.trait, head, list(d.tparams), dict(d.bounds), names)
        prog.decls.extend(generated)

        # pass 1: collect type names
        def collect(d):
            if d.kind == "Trait":
                self.decl_nodes[d.name] = d
                if d.name in PRIMS or d.name in GENERIC_ARITY or self.traits.get(d.name) is not d:
                    self.err("E202", f"trait `{d.name}` is defined twice", d, "every trait name must be unique")
                self.define(d.name, d)
            if d.kind in ("Struct", "Enum", "ExternType"):
                if is_tuple_name(d.name):
                    self.err("E202", f"`{d.name}` is the built-in name of tuples like (int, str)", d, "choose another name")
                if d.name in PRIMS or d.name in GENERIC_ARITY or (d.name in self.def_module and self.def_module[d.name] == d.module
                                                                   and (d.name in self.structs or d.name in self.enums or d.name in self.extern_types)):
                    self.err("E202", f"type `{d.name}` is defined twice", d, "every type name must be unique")
                self.define(d.name, d)
                self.decl_nodes[d.name] = d
                if d.kind == "Struct":
                    self.structs[d.name] = []
                    self.tparams_of[d.name] = list(d.tparams)
                elif d.kind == "Enum":
                    self.enums[d.name] = []
                    self.tparams_of[d.name] = list(d.tparams)
                else:
                    self.extern_types.add(d.name)
        for d in prog.decls:
            guard(d, collect)

        # pass 1b: imports (need enum variant names, so collect variants' names first)
        for d in prog.decls:
            if d.kind == "Enum":
                for v in d.variants:
                    self.def_module.setdefault(v.name, d.module)
        mod_names = {}
        for d in prog.decls:
            if d.kind in ("Struct", "Enum", "ExternType", "Fn", "Extern"):
                mod_names.setdefault(d.module, set()).add(d.name)
            if d.kind == "Enum":
                mod_names.setdefault(d.module, set()).update(v.name for v in d.variants)
            if d.kind == "Trait":
                mod_names.setdefault(d.module, set()).add(d.name)

        def check_use(d):
            vis = self.visible_in.setdefault(d.module, set())
            target = d.target_module
            available = mod_names.get(target, set())
            for name, tok in d.names:
                if name not in available:
                    self.err("E201", f"module `{'.'.join(d.path)}` has no `{name}`", tok,
                             self.suggest(name, sorted(available)) or ("it defines: " + ", ".join(sorted(available))))
                vis.add(name)
                for ed in prog.decls:
                    if ed.kind == "Enum" and ed.name == name and ed.module == target:
                        vis.update(v.name for v in ed.variants)
                    if ed.kind == "Trait" and ed.name == name and ed.module == target:
                        vis.update(m.name for m in ed.methods)     # importing a trait imports its methods
        for d in prog.decls:
            if d.kind == "Use":
                guard(d, check_use)

        # pass 2: resolve fields, variants, function signatures
        tests = set()

        def signature(d):
            if d.kind == "Struct":
                seen = set()
                tps = set(d.tparams)
                fields = []
                for fname, ft in d.fields:
                    if fname in seen:
                        self.err("E202", f"field `{fname}` declared twice in struct {d.name}", d)
                    seen.add(fname)
                    fields.append((fname, self.to_type(ft, tps)))
                self.structs[d.name] = fields
            elif d.kind == "Enum":
                tps = set(d.tparams)
                for v in d.variants:
                    if v.name in self.variants or v.name in self.structs or v.name in self.enums:
                        other = self.variants.get(v.name)
                        where = ""
                        if other is not None and other.enum != d.name:
                            mod = self.def_module.get(other.enum)
                            where = f" by enum {other.enum}" + (f" in module `{self.module_name.get(mod, mod)}`" if mod else "")
                        self.err("E202", f"variant name `{v.name}` is already used{where}", v,
                                 "variant names are global and must be unique across all enums and structs; rename this one")
                    self.variants[v.name] = VariantInfo(d.name, v.name, [(f, self.to_type(t, tps)) for f, t in v.fields],
                                                        d.tparams)
                    self.enums.setdefault(d.name, []).append(v.name)
                    v.module = d.module
                    self.decl_nodes[v.name] = v
            elif d.kind == "Trait":
                for m in d.methods:
                    if m.name in self.fns:
                        self.duplicates.add(m.name)
                        self.err("E202", f"method `{m.name}` of trait {d.name} clashes with the function `{m.name}`", m,
                                 "trait methods are called like functions, so their names must be unique too")
                    if m.tparams:
                        self.err("E301", f"trait methods cannot have type parameters (`{m.name}` in {d.name})", m)
                    if not m.params or m.params[0][1].kind != "TName" or m.params[0][1].name != "Self" or m.params[0][1].args:
                        self.err("E301", f"the first parameter of `{m.name}` must have type `Self`", m,
                                 f"fn {m.name}(self: Self, ...): the value whose type decides which impl runs")
                    self.def_module[m.name] = d.module
                    params = [(n, self.to_type(t, {"Self"})) for n, t in m.params]
                    sig = FnSig(m.name, ["Self"], params, self.to_type(m.ret, {"Self"}), m.effects, node=m)
                    sig.trait = d.name
                    sig.bounds = {"Self": [d.name]}
                    self.fns[m.name] = sig
            elif d.kind in ("Fn", "Extern"):
                if d.name in self.fns:
                    b = self.fns[d.name].builtin
                    self.duplicates.add(d.name)
                    self.err("E202", f"function `{d.name}` is already defined" + (" as a built-in" if b else ""), d,
                             "Klar has no overloading; choose a different name")
                self.define(d.name, d)
                tps = set(d.tparams)
                for tp in d.tparams:
                    if (tp in self.structs or tp in self.enums) and (self.def_module.get(tp) == d.module
                                                                      or tp in self.visible_in.get(d.module, ())):
                        self.err("E202", f"type parameter `{tp}` clashes with a type of the same name", d)
                names = [p for p, _ in d.params]
                for p in names:
                    if names.count(p) > 1:
                        self.err("E202", f"parameter `{p}` appears twice", d)
                params = [(n, self.to_type(t, tps)) for n, t in d.params]
                sig = FnSig(d.name, d.tparams, params, self.to_type(d.ret, tps), d.effects, node=d)
                for tp, traits in (getattr(d, "bounds", None) or {}).items():
                    for tr in traits:
                        if tr not in self.traits:
                            self.err("E201", f"unknown trait `{tr}` in the bound on {tp}", d,
                                     self.suggest(tr, list(self.traits)))
                        self.check_vis(tr, d)
                sig.bounds = dict(getattr(d, "bounds", None) or {})
                self.fns[d.name] = sig
                if d.kind == "Extern":
                    d.ptypes = [t for _, t in params]
                    d.rtype = sig.ret
            elif d.kind == "Test":
                if (d.module, d.name) in tests:
                    self.err("E202", f"test \"{d.name}\" is defined twice", d)
                tests.add((d.module, d.name))
        for d in prog.decls:
            guard(d, signature)

        # pass 2b: impls must match their trait
        def check_impl(d):
            if d.trait not in self.traits:
                self.err("E201", f"unknown trait `{d.trait}`", d, self.suggest(d.trait, list(self.traits)))
            self.check_vis(d.trait, d)
            tr = self.traits[d.trait]
            info = d.info
            if d.ty.kind == "TFn":
                self.err("E301", "impls are for named types (structs, enums, int, str, list, ...), not function types", d)
            if (d.trait, info.head) in self.impls:
                self.err("E202", f"{d.trait} is already implemented for `{info.head}`", d,
                         "a type implements a trait once")
            tps = set(d.tparams)
            info.ty = self.to_type(d.ty, tps)
            used = set()
            match_type(info.ty, info.ty, tps, {})

            def names_in(t):
                t = prune(t)
                if isinstance(t, TCon):
                    if not t.args and t.name in tps:
                        used.add(t.name)
                    for a in t.args:
                        names_in(a)
                elif isinstance(t, TFn):
                    for a in t.params + [t.ret]:
                        names_in(a)
            names_in(info.ty)
            for tp in d.tparams:
                if tp not in used:
                    self.err("E301", f"type parameter `{tp}` of this impl does not appear in `{show(info.ty)}`", d)
            for tp, traits in d.bounds.items():
                for t2 in traits:
                    if t2 not in self.traits:
                        self.err("E201", f"unknown trait `{t2}`", d)
            wanted = {m.name: m for m in tr.methods}
            for m in d.methods:
                if m.name not in wanted:
                    self.err("E201", f"`{m.name}` is not a method of trait {d.trait}", m,
                             "the trait has: " + ", ".join(sorted(wanted)))
                if m.tparams:
                    self.err("E301", f"methods in an impl cannot have their own type parameters", m)
                tsig = self.fns[m.name] if m.name in self.fns and self.fns[m.name].trait == d.trait else None
                isig = self.fns.get(info.fn_names[m.name])
                if tsig is None or isig is None:
                    continue
                sub = {"Self": info.ty}
                want = [substitute(t, sub) for _, t in tsig.params] + [substitute(tsig.ret, sub)]
                got = [t for _, t in isig.params] + [isig.ret]
                if len(want) != len(got) or any(show(a) != show(b) for a, b in zip(want, got)) \
                        or sorted(tsig.effects) != sorted(isig.effects):
                    fmt = lambda ps, r, eff: f"({', '.join(show(t) for t in ps)}) -> {show(r)}" + (" uses io" if eff else "")
                    self.err("E301", f"`{m.name}` in impl {d.trait} for {show(info.ty)} has the wrong signature", m,
                             f"it must be fn {m.name}{fmt(want[:-1], want[-1], tsig.effects)}")
            for name in wanted:
                if name not in {m.name for m in d.methods}:
                    self.err("E312", f"impl {d.trait} for {show(info.ty)} is missing `{name}`", d)
            self.impls[(d.trait, info.head)] = info
        for d in prog.decls:
            if d.kind == "Impl":
                guard(d, check_impl)

        # pass 3: bodies (tests only for the entry module)
        def body(d):
            if d.kind == "Fn":
                if self.fns.get(d.name) is not None and self.fns[d.name].node is d:
                    self.check_fn(d)
            elif d.kind == "Test":
                self.check_body(d.body, [], UNIT, True, f"test \"{d.name}\"")
        for d in prog.decls:
            guard(d, body)
        for t, trait, node, bounds, module, file in self.obligations:
            self.file, self.cur_module = file, module
            why = self.satisfies(t, trait, bounds, node)
            if why:
                try:
                    hint = None
                    if why.startswith("`") and "does not implement" in why:
                        tn = type_head(t)
                        hint = f"add `impl {trait} for {tn} {{ ... }}`"
                    self.err("E312", why, node, hint)
                except KlarError as e:
                    self.errors.append(e)
        for call, name in self.json_reads:
            if has_vars(call.inst[0]):
                self.err("E304", f"cannot tell which type `{name}` should read (got `{show(call.inst[0])}`)", call,
                         f"say it where the result is stored, e.g. let r: Result[Todo, str] = {name}(text)")
        return self.errors

    def check_fn(self, d):
        sig = self.fns[d.name]
        ret = sig.ret
        self.cur_bounds = {tp: sig.bounds.get(tp, []) for tp in sig.tparams}
        try:
            self.check_body(d.body, sig.params, ret, "io" in sig.effects,
                            d.impl_of[1] if hasattr(d, "impl_of") else d.name, d)
        finally:
            self.cur_bounds = {}
        if prune(ret) is not UNIT and not (isinstance(prune(ret), TCon) and prune(ret).name == "unit"):
            if not self.always_exits(d.body):
                self.err("E305", f"function `{d.name}` may reach its end without returning a `{show(ret)}`",
                         d, "add a `return` statement on every path (e.g. at the end of the function)")

    def check_body(self, body, params, ret, io, name, decl=None):
        outer_scopes, outer_ctx = self.scopes, self.ctx
        self.scopes = [{}]
        self.ctx = FnCtx(ret, io, name=name)
        for pname, pty in params:
            self.scopes[0][pname] = (pty, False, decl)
            if decl is not None:
                self.note(decl, pname, "decl", (pty, decl))
        try:
            self.check_block(body, new_scope=False)
            for let in self.ctx.lets:
                if has_vars(let.ty):
                    self.err("E304", f"cannot infer the full type of `{let.name}` (got `{show(let.ty)}`)", let,
                             f"annotate it, e.g. let {let.name}: list[int] = []")
        finally:
            self.scopes, self.ctx = outer_scopes, outer_ctx

    # ---------------------------------------------------------- control-flow analysis
    def always_exits(self, node):
        k = node.kind
        if k == "Block":
            return any(self.always_exits(s) for s in node.stmts)
        if k in ("Return", "Break", "Continue"):
            return True   # leaves this block (a loop's break/continue is checked by the loop itself)
        if k == "If":
            return node.els is not None and self.always_exits(node.then) and self.always_exits(node.els)
        if k == "ExprStmt":
            e = node.expr
            if e.ty is not None and isinstance(prune(e.ty), TCon) and prune(e.ty).name == "never":
                return True
            if e.kind == "Match":
                return all(self.always_exits(a.body) if a.body.kind == "Block" else
                           (a.body.ty is not None and show(a.body.ty) == "never") for a in e.arms)
            return False
        if k == "While":
            return node.cond.kind == "Bool" and node.cond.value and not self.has_break(node.body)
        return False

    def has_break(self, node):
        if isinstance(node, Node):
            if node.kind == "Break":
                return True
            if node.kind in ("While", "For", "Lambda"):
                return False
            return any(self.has_break(v) for v in node.__dict__.values())
        if isinstance(node, (list, tuple)):
            return any(self.has_break(x) for x in node)
        return False

    # ---------------------------------------------------------- statements
    def check_block(self, block, new_scope=True):
        if new_scope:
            self.scopes.append({})
        try:
            for s in block.stmts:
                try:
                    self.check_stmt(s)
                except KlarError as err:
                    # record and keep going so one run reports every independent error
                    self.errors.append(err)
                    if s.kind == "Let" and self.lookup(s.name) is None:
                        self.scopes[-1][s.name] = (TVar(), s.mutable, s)
                        if getattr(s, "unpack", None):
                            s.unpack_failed = True
        finally:
            if new_scope:
                self.scopes.pop()

    def check_stmt(self, s):
        k = s.kind
        if k == "Let":
            if getattr(s, "from_tuple", False):
                entry = self.lookup(s.value.target.name)
                if entry is not None and getattr(entry[2], "unpack_failed", False):
                    self.declare(s.name, TVar(), s.mutable, s)     # the unpack itself was already reported
                    return
            if s.ann is not None:
                ty = self.to_type(s.ann, set(self.cur_bounds))
                self.expect(self.infer(s.value, ty), ty, s.value, f"the value of `{s.name}`")
            else:
                ty = self.infer(s.value)
                if show(ty) == "unit":
                    arm = getattr(s.value, "fallthrough_arm", None) if s.value.kind == "Match" else None
                    if arm is not None:
                        self.err("E301", f"`{s.name}` would hold no value: the match arm at line {arm.line} is a "
                                 "`{ }` block that can end without one", arm,
                                 "a block arm gives no value; write the value without braces (`Some(x) => x`), or "
                                 "end every path of the block with return, break or continue")
                    self.err("E301", f"`{s.name}` would hold no value (the expression has type unit)", s.value)
            s.ty = ty
            if getattr(s, "unpack", None):
                self.check_unpack(ty, s.unpack, s.value, "let")
            if s.name == "_":
                return  # `let _ = expr` evaluates and discards
            self.declare(s.name, ty, s.mutable, s)
            self.ctx.lets.append(s)
        elif k == "Assign":
            entry = self.lookup(s.name)
            if entry is None:
                if s.name in self.fns:
                    self.err("E203", f"cannot assign to function `{s.name}`", s)
                self.err("E201", f"unknown variable `{s.name}`", s,
                         f"declare it first with `var {s.name} = ...`")
            ty, mutable = entry[0], entry[1]
            self.note(s, s.name, "local", entry)
            if not mutable:
                self.err("E203", f"cannot assign to `{s.name}` because it was declared with `let`", s,
                         f"declare it with `var {s.name} = ...` if it needs to change")
            self.expect(self.infer(s.value, ty), ty, s.value, f"the new value of `{s.name}`")
        elif k == "If":
            self.expect(self.infer(s.cond, BOOL), BOOL, s.cond, "the if-condition")
            self.check_block(s.then)
            if s.els is not None:
                self.check_block(s.els)
        elif k == "While":
            self.expect(self.infer(s.cond, BOOL), BOOL, s.cond, "the while-condition")
            self.ctx.loop_depth += 1
            self.check_block(s.body)
            self.ctx.loop_depth -= 1
        elif k == "For":
            it = prune(self.infer(s.iter))
            if isinstance(it, TCon) and it.name == "list":
                elem = it.args[0]
            elif isinstance(it, TVar):
                elem = TVar()
                unify(it, list_of(elem))
            else:
                hint = None
                if isinstance(it, TCon) and it.name == "map":
                    hint = "iterate over keys(m), values(m) or entries(m) (pairs: for (k, v) in entries(m))"
                elif isinstance(it, TCon) and it.name == "str":
                    hint = "iterate over chars(s)"
                elif it is INT or show(it) == "int":
                    hint = "iterate over range(0, n)"
                self.err("E301", f"`for` needs a list but got `{show(it)}`", s.iter, hint)
            if getattr(s, "unpack", None):
                self.check_unpack(elem, s.unpack, s.iter, "for")
            self.scopes.append({})
            self.declare(s.var, elem, False, s)
            self.ctx.loop_depth += 1
            try:
                self.check_block(s.body)
            finally:
                self.ctx.loop_depth -= 1
                self.scopes.pop()
        elif k == "Return":
            ret = self.ctx.ret
            if s.value is None:
                if show(ret) != "unit":
                    self.err("E301", f"`return` without a value in a function returning `{show(ret)}`", s)
            else:
                self.expect(self.infer(s.value, ret), ret, s.value, "the returned value")
        elif k in ("Break", "Continue"):
            if self.ctx.loop_depth == 0:
                self.err("E309", f"`{k.lower()}` outside of a loop", s)
        elif k == "Assert":
            self.expect(self.infer(s.cond, BOOL), BOOL, s.cond, "the assert condition")
            if s.msg is not None:
                self.expect(self.infer(s.msg, STR), STR, s.msg, "the assert message")
        elif k == "ExprStmt":
            t = self.infer(s.expr)
            e = s.expr
            pt = prune(t)
            if getattr(s, "arm_tail", False) and show(pt) not in ("unit", "never") and not isinstance(pt, TVar) \
                    and e.kind not in ("Lambda",):
                s.arm_value = True
                self.err("E301", f"a `{{ ... }}` match arm does not give the match a value (this line has type `{show(pt)}`)", e,
                         "end the block with `return` to return from the function (e.g. `return " + ("Ok(...)`" if show(pt).startswith("Result[") else "value`")
                         + "), or write the arm without braces: `Pattern => expression`")
            if e.kind in ("Name", "Int", "Float", "Str", "Bool", "Binary", "Field", "Index", "ListLit", "MapLit"):
                hint = None
                if e.kind == "Binary" and e.op == "==":
                    hint = "use `=` to assign, `==` compares"
                self.err("E301", "this expression's value is unused", e, hint or "bind it with `let` or remove it")
            pt = prune(t)
            if e.kind in ("Call", "MethodCall") and show(pt) not in ("unit", "never") and not isinstance(pt, TVar):
                self.err("E301", f"the result of this call (type `{show(pt)}`) is ignored", e,
                         "values are immutable, so calls like push(xs, x) return a NEW value; write `xs = push(xs, x)`, "
                         "or `let _ = ...` to discard it on purpose")
        else:
            self.err("E103", f"unexpected statement {k}", s)

    # ---------------------------------------------------------- expressions
    def infer(self, e, expected=None):
        t = self._infer(e, expected)
        e.ty = t
        return t

    def _infer(self, e, expected):
        k = e.kind
        if k == "Int":
            return INT
        if k == "Float":
            return FLOAT
        if k == "Str":
            return STR
        if k == "Bool":
            return BOOL
        if k == "Unit":
            return UNIT
        if k == "Interp":
            for p in e.parts:
                if isinstance(p, Node):
                    pt = prune(self.infer(p))
                    if isinstance(pt, TFn):
                        self.err("E301", "cannot interpolate a function", p)
            return STR
        if k == "Name":
            return self.infer_name(e)
        if k == "ListLit":
            if expected is not None and isinstance(prune(expected), TCon) and prune(expected).name == "list":
                elem = prune(expected).args[0]
            else:
                elem = TVar()
            for it in e.items:
                self.expect(self.infer(it, elem), elem, it, "this list element")
            return list_of(elem)
        if k == "MapLit":
            pe = prune(expected) if expected is not None else None
            if isinstance(pe, TCon) and pe.name == "map":
                kt, vt = pe.args
            else:
                kt, vt = TVar(), TVar()
            for key, val in e.entries:
                self.expect(self.infer(key, kt), kt, key, "this map key")
                self.expect(self.infer(val, vt), vt, val, "this map value")
            self.check_key_type(kt, e)
            return TCon("map", [kt, vt])
        if k == "Unary":
            if e.op == "not":
                self.expect(self.infer(e.operand, BOOL), BOOL, e.operand, "the operand of `not`")
                return BOOL
            t = prune(self.infer(e.operand, expected))
            if show(t) not in ("int", "float"):
                self.err("E302", f"unary `-` needs int or float, got `{show(t)}`", e)
            return t
        if k == "Binary":
            return self.infer_binary(e)
        if k == "IfExpr":
            self.expect(self.infer(e.cond, BOOL), BOOL, e.cond, "the if-condition")
            a = self.infer(e.then, expected)
            b = self.infer(e.els, a)
            self.expect(b, a, e.els, "the else-branch")
            return a if show(a) != "never" else b
        if k == "Call":
            return self.infer_call(e, expected)
        if k == "MethodCall":
            # uniform function call syntax: x.f(a) == f(x, a)
            call = Node("Call", e.line, e.col, callee=Node("Name", e.line, e.col, name=e.name),
                        args=[e.target] + e.args, named=[])
            tgt = prune(self.infer(e.target))
            if isinstance(tgt, TCon) and tgt.name in self.structs and any(f == e.name for f, _ in self.structs[tgt.name]):
                ft = prune(dict(self.struct_fields(tgt))[e.name])
                if isinstance(ft, TFn):
                    self.err("E303", f"`{e.name}` is a field holding a function, not a function name", e,
                             f"call it as (x.{e.name})(...) or bind it first: let f = x.{e.name}")
                self.err("E303", f"`{e.name}` is a field, not a function", e,
                         f"to call a function stored in a field, bind it first: let f = x.{e.name}")
            if e.name not in self.fns and self.lookup(e.name) is None:
                self.err("E201", f"unknown function `{e.name}` (used as x.{e.name}(...))", e,
                         self.suggest(e.name, list(self.fns)) or "x.f(a) is shorthand for f(x, a); f must be a function")
            e.desugared = call
            return self.infer(call, expected)
        if k == "Field":
            t = prune(self.infer(e.target))
            if isinstance(t, TCon) and t.name in self.structs:
                for f, ft in self.struct_fields(t):
                    if f == e.name:
                        self.note(e, f, "field", (t.name, ft))
                        return ft
                if is_tuple_name(t.name):
                    self.err("E204", f"`{show(t)}` has no value .{e.name}", e,
                             f"its values are .0 to .{len(t.args) - 1}, or unpack it: let ({', '.join('abcdef'[:len(t.args)])}) = t")
                self.err("E204", f"struct `{t.name}` has no field `{e.name}`", e,
                         self.suggest(e.name, [f for f, _ in self.structs[t.name]]) or
                         "fields: " + ", ".join(f for f, _ in self.structs[t.name]))
            hint = None
            if e.name.isdigit():
                self.err("E204", f"`.{e.name}` reads a tuple value, but this has type `{show(t)}`", e)
            if e.name in self.fns:
                hint = f"to call the function, add parentheses: .{e.name}()"
            elif e.name in ("length", "size", "count"):
                hint = "use len(x)"
            self.err("E204", f"type `{show(t)}` has no field `{e.name}`", e, hint)
        if k == "Index":
            t = prune(self.infer(e.target))
            if isinstance(t, TCon) and t.name == "list":
                self.expect(self.infer(e.index, INT), INT, e.index, "the list index")
                return t.args[0]
            if isinstance(t, TCon) and t.name == "map":
                self.expect(self.infer(e.index, t.args[0]), t.args[0], e.index, "the map key")
                return t.args[1]
            if show(t) == "str":
                self.expect(self.infer(e.index, INT), INT, e.index, "the string index")
                return STR
            self.err("E302", f"cannot index into `{show(t)}`", e)
        if k == "Try":
            t = prune(self.infer(e.value))
            ret = prune(self.ctx.ret)
            if self.ctx.is_lambda:
                self.err("E307", "`?` cannot be used inside an anonymous function", e)
            if isinstance(t, TCon) and t.name == "Option":
                if not (isinstance(ret, TCon) and ret.name == "Option"):
                    self.err("E307", f"`?` on an Option requires the function to return an Option, but it returns `{show(ret)}`",
                             e, "use match or unwrap_or(...) instead")
                return t.args[0]
            if isinstance(t, TCon) and t.name == "Result":
                if not (isinstance(ret, TCon) and ret.name == "Result"):
                    self.err("E307", f"`?` on a Result requires the function to return a Result, but it returns `{show(ret)}`",
                             e, "use match instead, or change the return type to Result[...]")
                if not unify(t.args[1], ret.args[1]):
                    e.wrap = self.error_wrapper(t.args[1], ret.args[1], e)
                return t.args[0]
            self.err("E307", f"`?` needs an Option or Result, got `{show(t)}`", e)
        if k == "With":
            t = prune(self.infer(e.target, expected))
            if not (isinstance(t, TCon) and t.name in self.structs):
                self.err("E302", f"`with` needs a struct value, got `{show(t)}`", e)
            fields = dict(self.struct_fields(t))
            for fname, val, ft in e.updates:
                if fname not in fields:
                    self.err("E204", f"struct `{t.name}` has no field `{fname}`", ft, self.suggest(fname, list(fields)))
                self.note(ft, fname, "field", (t.name, fields[fname]))
                self.expect(self.infer(val, fields[fname]), fields[fname], val, f"field `{fname}`")
            return t
        if k == "Match":
            return self.infer_match(e, expected)
        if k == "Lambda":
            return self.infer_lambda(e, expected)
        self.err("E103", f"unexpected expression {k}", e)

    def check_unpack(self, t, n, node, form):
        """`let (a, b) = v` and `for (a, b) in xs` need tuples of exactly n values."""
        want = TCon(tuple_name(n), [TVar() for _ in range(n)])
        if unify(t, want):
            return
        names = ", ".join("abcdef"[:n])
        what = "the value" if form == "let" else "each element"
        pt = prune(t)
        hint = None
        if isinstance(pt, TCon) and is_tuple_name(pt.name):
            hint = f"it has {len(pt.args)} values; name each one (use _ to skip one)"
        elif isinstance(pt, TCon) and pt.name in self.structs:
            hint = "structs are not unpacked; bind the value and read its fields"
        elif form == "for" and isinstance(pt, TCon) and pt.name == "str":
            hint = "a map's entries are pairs: for (k, v) in entries(m)"
        self.err("E301", f"`{form} ({names})` unpacks a tuple of {n} values, but {what} has type `{show(t)}`", node, hint)

    def error_wrapper(self, err_t, ret_err, node):
        """`?` turns an Err(e) into the function's error type when that is an enum with exactly one variant
        holding just a value of e's type: in a function returning Result[T, ApiError], `db_call()?` with an
        Err(str) returns Err(Internal(message)). Returns that variant's name, or reports E307."""
        et, rt = prune(err_t), prune(ret_err)
        fits = []
        if isinstance(rt, TCon) and rt.name in self.enums:
            mapping = dict(zip(self.tparams_of.get(rt.name, []), rt.args))
            for vn in self.enums[rt.name]:
                fields = self.variants[vn].fields
                if len(fields) == 1 and show(resolve(substitute(fields[0][1], mapping))) == show(resolve(et)):
                    fits.append(vn)
        if len(fits) == 1:
            self.check_vis(fits[0], node)
            return fits[0]
        es, rs = show(et), show(rt)
        if len(fits) > 1:
            hint = (f"`?` can only wrap the error when exactly one variant of {rs} holds a `{es}`, but "
                    f"{', '.join(fits)} all do; convert it yourself with match")
        elif isinstance(rt, TCon) and rt.name in self.enums:
            hint = (f"add a variant that holds it, e.g. `Internal(message: {es})`, to enum {rs}: `?` then wraps the "
                    f"error in it; or convert it yourself with match")
        else:
            hint = (f"make the function return Result[..., {es}], or convert the error with match; `?` wraps errors "
                    f"automatically only into an enum with one variant holding a `{es}`")
        self.err("E307", f"error type `{es}` does not match the function's error type `{rs}`", node, hint)

    def check_key_type(self, kt, node):
        kt = prune(kt)
        if isinstance(kt, TVar):
            return
        if show(kt) not in ("int", "str", "bool"):
            self.err("E301", f"map keys must be int, str or bool, not `{show(kt)}`", node)

    def infer_name(self, e):
        entry = self.lookup(e.name)
        if entry is not None:
            if entry[1] and self.lambda_base is not None:
                for i in range(len(self.scopes) - 1, -1, -1):
                    if e.name in self.scopes[i]:
                        if i < self.lambda_base:
                            self.err("E311", f"anonymous function uses `{e.name}`, a `var` from outside it", e,
                                     f"functions capture values, not variables: copy it first with "
                                     f"`let {e.name}_now = {e.name}` and use that inside the function")
                        break
            self.note(e, e.name, "local", entry)
            return entry[0]
        if e.name in self.variants:
            self.check_vis(e.name, e)
            self.note(e, e.name, "variant")
            v = self.variants[e.name]
            if v.fields:
                self.err("E205", f"variant `{e.name}` needs arguments: {e.name}({', '.join(f for f, _ in v.fields)})", e)
            mapping = {tp: TVar() for tp in v.tparams}
            return TCon(v.enum, [mapping[tp] for tp in v.tparams])
        if e.name in self.fns:
            sig = self.fns[e.name]
            if sig.builtin and e.name in OVERLOADED:
                self.err("E303", f"built-in `{e.name}` cannot be used as a value", e,
                         f"wrap it: fn(x) => {e.name}(x)")
            self.check_vis(e.name, e)
            self.note(e, e.name, "fn")
            params, ret = sig.instantiate(e)
            self.obligate(sig, e)
            if sig.trait:
                e.trait_ref = (sig.trait, e.name)
            return TFn([t for _, t in params], ret, "io" in sig.effects)
        if e.name in self.structs:
            self.err("E205", f"struct `{e.name}` must be constructed with arguments: {e.name}(field: value, ...)", e)
        hint = None
        if e.name in ("null", "nil", "None", "undefined"):
            hint = "Klar has no null; use Option: Some(x) / None"
        elif e.name in ("True", "False"):
            hint = f"booleans are lowercase: {e.name.lower()}"
        elif e.name in ("self", "this"):
            hint = "Klar has no methods; pass the value as a normal parameter"
        else:
            names = [n for s in self.scopes for n in s] + list(self.fns) + list(self.variants)
            hint = self.suggest(e.name, names)
        self.err("E201", f"unknown name `{e.name}`", e, hint)

    def infer_binary(self, e):
        op = e.op
        if op in ("and", "or"):
            self.expect(self.infer(e.left, BOOL), BOOL, e.left, f"the left side of `{op}`")
            self.expect(self.infer(e.right, BOOL), BOOL, e.right, f"the right side of `{op}`")
            return BOOL
        lt = self.infer(e.left)
        rt = self.infer(e.right, lt)
        if not unify(lt, rt):
            ls, rs = show(lt), show(rt)
            hint = None
            if {ls, rs} == {"int", "float"}:
                hint = "convert explicitly with to_float(n) or floor(x)/round(x)"
            elif "str" in (ls, rs) and op == "+":
                hint = "build strings with interpolation: \"text {value}\" or convert with str(value)"
            self.err("E302", f"`{op}` needs both sides to have the same type, got `{ls}` and `{rs}`", e, hint)
        t = prune(lt)
        s = show(t)
        if op in ("==", "!="):
            if isinstance(t, TFn):
                self.err("E302", "functions cannot be compared", e)
            return BOOL
        if op in ("<", "<=", ">", ">="):
            if s not in ("int", "float", "str") and not isinstance(t, TVar):
                self.err("E302", f"`{op}` is only defined for int, float and str, not `{s}`", e)
            return BOOL
        if op == "+":
            if s in ("int", "float", "str") or s.startswith("list[") or isinstance(t, TVar):
                return t
            self.err("E302", f"`+` is not defined for `{s}`", e)
        if op in ("-", "*", "/"):
            if s in ("int", "float") or isinstance(t, TVar):
                return t
            self.err("E302", f"`{op}` is only defined for int and float, not `{s}`", e,
                     "use repeat(s, n) to repeat a string" if s == "str" and op == "*" else None)
        if op == "%":
            if s == "int" or isinstance(t, TVar):
                return t
            self.err("E302", f"`%` is only defined for int, not `{s}`", e)
        self.err("E302", f"unknown operator `{op}`", e)

    def bind_args(self, e, params, what, struct=None):
        """Return list of argument nodes ordered to match params."""
        if e.named:
            names = [p for p, _ in params]
            by_name = {}
            for n, val, tok in e.named:
                if n not in names:
                    self.err("E206", f"{what} has no parameter named `{n}`", tok,
                             self.suggest(n, names) or ("parameters: " + ", ".join(names)))
                if n in by_name:
                    self.err("E206", f"argument `{n}` given twice", tok)
                by_name[n] = val
                if struct is not None:
                    self.note(tok, n, "field", (struct, None))
            missing = [n for n in names if n not in by_name]
            if missing:
                self.err("E205", f"{what} is missing argument(s): {', '.join(missing)}", e)
            return [by_name[n] for n in names]
        if len(e.args) != len(params):
            sig = ", ".join(f"{p}: {show(t)}" for p, t in params)
            self.err("E205", f"{what} takes {len(params)} argument(s) but {len(e.args)} were given", e,
                     f"expected ({sig})")
        return list(e.args)

    def check_sql_text(self, a, fn_name):
        """SQL handed to a parameter named `sql` must not be assembled from values (SQL injection): values go in
        `?` parameters. Interpolating a string, or `+` with a non-literal, in the argument itself is an error;
        interpolated numbers and bools are allowed (they cannot change the query: `pragma user_version = {n}`)."""
        def text_part(x):            # a number or bool cannot change the query; a string can
            t = prune(getattr(x, "ty", None)) if getattr(x, "ty", None) is not None else None
            return not (isinstance(t, TCon) and t.name in ("int", "float", "bool"))

        def built(x):
            if x.kind == "Interp":
                return any(isinstance(p, Node) and text_part(p) for p in x.parts)
            if x.kind == "Binary" and x.op == "+":
                return built(x.left) or built(x.right) or x.left.kind != "Str" or x.right.kind != "Str"
            return False
        if built(a):
            self.err("E313", f"the SQL given to `{fn_name}` is built from values, which allows SQL injection", a,
                     "write the query as one string with `?` where values go and pass the values in the params "
                     "list: sqlite_query(db, \"select * from t where id = ?\", [SqlInt(id)]); a table or column "
                     "name that varies must be picked from a fixed list of names")

    def infer_call(self, e, expected):
        callee = e.callee
        if callee.kind == "Name" and self.lookup(callee.name) is None:
            name = callee.name
            if name in self.structs:
                self.check_vis(name, callee)
                self.note(callee, name, "type")
                result = TCon(name, [TVar() for _ in self.tparams_of.get(name, [])])
                if expected is not None:
                    unify(result, expected)
                fields = self.struct_fields(result)
                args = self.bind_args(e, fields, f"struct `{name}`", name)
                tup = is_tuple_name(name)
                for (fname, ft), a in zip(fields, args):
                    self.expect(self.infer(a, ft), ft, a, f"tuple value .{fname}" if tup else f"field `{fname}`")
                e.target_kind = "struct"
                return result
            if name in self.variants:
                self.check_vis(name, callee)
                self.note(callee, name, "variant")
                v = self.variants[name]
                mapping = {tp: TVar() for tp in v.tparams}
                fields = [(f, substitute(t, mapping)) for f, t in v.fields]
                if not fields:
                    self.err("E205", f"variant `{name}` takes no arguments; write just `{name}`", e)
                args = self.bind_args(e, fields, f"variant `{name}`")
                result = TCon(v.enum, [mapping[tp] for tp in v.tparams])
                if expected is not None:
                    unify(result, expected)
                for (fname, ft), a in zip(fields, args):
                    self.expect(self.infer(a, ft), ft, a, f"field `{fname}` of {name}")
                e.target_kind = "variant"
                return result
            if name in self.fns:
                sig = self.fns[name]
                self.check_vis(name, callee)
                self.note(callee, name, "fn")
                e.target_kind = "extern" if sig.node is not None and sig.node.kind == "Extern" else "fn"
                if "io" in sig.effects:
                    self.ctx.used_io = True
                if "io" in sig.effects and not self.ctx.io:
                    if self.ctx.is_lambda:
                        self.err("E308", f"this anonymous function performs io (`{name}` uses io) where a pure function is expected", e,
                                 "a function value may only do io when its type says so, e.g. a parameter typed "
                                 "`fn(Request) -> Response uses io`; for map/filter/fold use a for loop instead")
                    self.err("E308", f"`{name}` performs io, so `{self.ctx.name}` must be declared with `uses io`", e,
                             f"add `uses io` to the signature: fn {self.ctx.name}(...) -> ... uses io {{")
                if sig.builtin and name in OVERLOADED:
                    return self.infer_overloaded(e, name, expected)
                params, ret = sig.instantiate(e)
                self.obligate(sig, e)
                if sig.trait:
                    e.target_kind = "trait"
                    e.trait_ref = (sig.trait, name)
                if sig.node is not None and sig.node.kind == "Extern" and sig.node.target in ("klar:json.decode", "klar:json.into",
                                                                                          "klar:sql.read"):
                    self.json_reads.append((e, name))
                args = self.bind_args(e, params, f"`{name}`")
                if expected is not None and not isinstance(prune(expected), TVar):
                    # helps infer generic results, ignore failure (reported later)
                    snapshot = resolve(ret)
                    if not has_vars(snapshot) or True:
                        pass
                for (pname, pt), a in zip(params, args):
                    self.expect(self.infer(a, pt), pt, a, f"argument `{pname}` of `{name}`")
                    if pname == "sql":
                        self.check_sql_text(a, name)
                if name in ("put", "has", "remove"):
                    self.check_key_type(prune(params[0][1]).args[0] if isinstance(prune(params[0][1]), TCon) else TVar(), e)
                return ret
            hint = self.suggest(name, list(self.fns) + list(self.structs) + list(self.variants))
            if name in ("println", "console", "printf", "puts", "echo", "log"):
                hint = "use print(value)"
            elif name in ("length", "size", "count"):
                hint = "use len(x)"
            elif name in ("append",):
                hint = "use push(xs, x), which returns a new list"
            elif name in ("int", "float") :
                hint = "use parse_int(s) / parse_float(s) for strings, to_float(n), floor(x) or round(x) for numbers"
            self.err("E201", f"unknown function `{name}`", callee, hint)
        ct = prune(self.infer(callee))
        if not isinstance(ct, TFn):
            self.err("E303", f"cannot call a value of type `{show(ct)}`", e)
        if e.named:
            self.err("E206", "named arguments are not allowed when calling a function value", e)
        if ct.io:
            self.ctx.used_io = True
            if not self.ctx.io:
                self.err("E308", f"calling this function value performs io (its type is `{show(ct)}`), "
                         f"so `{self.ctx.name}` must be declared with `uses io`", e)
        if len(e.args) != len(ct.params):
            self.err("E205", f"function takes {len(ct.params)} argument(s) but {len(e.args)} were given", e)
        for pt, a in zip(ct.params, e.args):
            self.expect(self.infer(a, pt), pt, a, "the argument")
        e.target_kind = "value"
        return ct.ret

    def infer_overloaded(self, e, name, expected):
        if e.named:
            self.err("E206", f"built-in `{name}` takes positional arguments only", e)
        arity = {"print": 1, "eprint": 1, "str": 1, "len": 1, "get": 2, "slice": 3, "contains": 2, "reverse": 1, "sort": 1,
                 "sum": 1, "abs": 1, "min": 2, "max": 2, "unwrap": 1, "unwrap_or": 2}[name]
        if len(e.args) != arity:
            self.err("E205", f"`{name}` takes {arity} argument(s) but {len(e.args)} were given", e)
        a0 = e.args[0]
        t0 = prune(self.infer(a0))
        s0 = show(t0)
        is_list = isinstance(t0, TCon) and t0.name == "list"
        is_map = isinstance(t0, TCon) and t0.name == "map"

        def bad(desc):
            self.err("E301", f"`{name}` expects {desc}, got `{s0}`", a0)

        if name in ("print", "eprint", "str"):
            if isinstance(t0, TFn):
                bad("a printable value")
            return STR if name == "str" else UNIT
        if name == "len":
            if is_list or is_map or s0 == "str":
                return INT
            bad("a list, map or str")
        if name == "get":
            if is_list:
                self.expect(self.infer(e.args[1], INT), INT, e.args[1], "the index")
                return option_of(t0.args[0])
            if is_map:
                self.expect(self.infer(e.args[1], t0.args[0]), t0.args[0], e.args[1], "the key")
                return option_of(t0.args[1])
            bad("a list or map")
        if name == "slice":
            if is_list or s0 == "str":
                for a in e.args[1:]:
                    self.expect(self.infer(a, INT), INT, a, "the slice bound")
                return t0
            bad("a list or str")
        if name == "contains":
            if is_list:
                self.expect(self.infer(e.args[1], t0.args[0]), t0.args[0], e.args[1], "the searched element")
                return BOOL
            if s0 == "str":
                self.expect(self.infer(e.args[1], STR), STR, e.args[1], "the searched substring")
                return BOOL
            if is_map:
                self.err("E301", "use has(m, key) to test map membership", e)
            bad("a list or str")
        if name == "reverse":
            if is_list or s0 == "str":
                return t0
            bad("a list or str")
        if name == "sort":
            if is_list and show(t0.args[0]) in ("int", "float", "str"):
                return t0
            if is_list:
                self.err("E301", f"sort needs list[int], list[float] or list[str], got `{s0}`", a0,
                         "use sort_by(xs, fn(x) => key) for other element types")
            bad("a list")
        if name == "sum":
            if is_list and show(t0.args[0]) in ("int", "float"):
                return t0.args[0]
            bad("list[int] or list[float]")
        if name == "abs":
            if s0 in ("int", "float"):
                return t0
            bad("an int or float")
        if name in ("min", "max"):
            if s0 not in ("int", "float", "str"):
                bad("int, float or str")
            self.expect(self.infer(e.args[1], t0), t0, e.args[1], f"the second argument of `{name}`")
            return t0
        if name in ("unwrap", "unwrap_or"):
            if isinstance(t0, TCon) and t0.name in ("Option", "Result"):
                inner = t0.args[0]
                if name == "unwrap_or":
                    self.expect(self.infer(e.args[1], inner), inner, e.args[1], "the default")
                return inner
            if isinstance(t0, TVar):
                inner = TVar()
                unify(t0, option_of(inner))
                if name == "unwrap_or":
                    self.expect(self.infer(e.args[1], inner), inner, e.args[1], "the default")
                return inner
            bad("an Option or Result")
        self.err("E201", f"unknown built-in {name}", e)

    def infer_lambda(self, e, expected):
        pe = prune(expected) if expected is not None else None
        exp_params = pe.params if isinstance(pe, TFn) and len(pe.params) == len(e.params) else None
        ptypes = []
        for i, (pname, ann) in enumerate(e.params):
            if ann is not None:
                pt = self.to_type(ann, set(self.cur_bounds))
                if exp_params:
                    unify(pt, exp_params[i])
            else:
                pt = exp_params[i] if exp_params else TVar()
            ptypes.append(pt)
        ret = self.to_type(e.ret, set(self.cur_bounds)) if e.ret is not None else (pe.ret if isinstance(pe, TFn) else TVar())
        outer_ctx = self.ctx
        # A lambda may do io if the type it is checked against allows it; a lambda with no expected type
        # (e.g. `let f = fn(...) => ...`) may do io when the enclosing function can, and its type records it.
        if isinstance(pe, TFn):
            may_io = pe.io
        else:
            may_io = outer_ctx.io
        self.ctx = FnCtx(ret, may_io, is_lambda=True, name="<anonymous fn>")
        outer_base = self.lambda_base
        self.lambda_base = len(self.scopes)
        self.ctx.lets = outer_ctx.lets
        self.scopes.append({})
        try:
            for (pname, _), pt in zip(e.params, ptypes):
                self.declare(pname, pt, False, e)
            if e.block:
                self.check_block(e.body)
                if show(ret) != "unit" and not self.always_exits(e.body):
                    if isinstance(prune(ret), TVar):
                        unify(ret, UNIT)
                    else:
                        self.err("E305", "anonymous function may end without returning a value", e,
                                 "use `return` in the block, or the short form fn(x) => expr")
            else:
                bt = self.infer(e.body, ret)
                self.expect(bt, ret, e.body, "the anonymous function's result")
            used_io = self.ctx.used_io
        finally:
            self.scopes.pop()
            self.ctx = outer_ctx
            self.lambda_base = outer_base
        return TFn(ptypes, ret, used_io)

    # ---------------------------------------------------------- match
    def infer_match(self, e, expected):
        st = self.infer(e.scrutinee)
        if getattr(e, "let_else", None) is not None:
            pt = prune(st)
            name = pt.name if isinstance(pt, TCon) else ""
            if name not in ("Option", "Result"):
                self.err("E301", f"`let ... else` needs an Option or a Result, not `{show(st)}`", e.scrutinee,
                         "write `let x = value` (there is nothing to unwrap)")
                return TVar()
            e.arms[0].pattern.name = "Some" if name == "Option" else "Ok"
        if not e.arms:
            self.err("E306", "match has no arms", e)
        result = None
        falls_through = False   # a block arm that can end without return/break makes the match a statement
        for arm in e.arms:
            self.scopes.append({})
            try:
                self.check_pattern(arm.pattern, st)
                if arm.body.kind == "Block":
                    tail = arm.body.stmts[-1] if arm.body.stmts else None
                    if tail is not None and tail.kind == "ExprStmt":
                        tail.arm_tail = True
                    self.check_block(arm.body)
                    if not self.always_exits(arm.body) and not getattr(tail, "arm_value", False):
                        if not falls_through:
                            e.fallthrough_arm = arm
                        falls_through = True
                else:
                    bt = self.infer(arm.body, result or expected)
                    if result is None or show(result) == "never":
                        result = bt
                    else:
                        self.expect(bt, result, arm.body, "this match arm")
            finally:
                self.scopes.pop()
        witness = self.exhaustive([[a.pattern] for a in e.arms], [st])
        if witness is not None:
            missing = witness[0]
            self.err("E306", f"match is not exhaustive: missing case {missing}", e,
                     f"add an arm `{missing} => ...` or a catch-all `_ => ...`")
        if getattr(e, "let_else", None) is not None and falls_through:
            self.err("E301", "the `else` block of `let ... else` can end without leaving", e.let_else,
                     "it runs when there is no value to bind: end every path with return, break or continue")
            return result
        if falls_through:
            return UNIT
        return result if result is not None else NEVER

    def check_pattern(self, p, t):
        k = p.kind
        if k == "PWild":
            return
        if k == "PBind":
            self.declare(p.name, t, False, p)
            return
        if k == "PLit":
            v = p.value
            lt = BOOL if isinstance(v, bool) else INT if isinstance(v, int) else FLOAT if isinstance(v, float) else STR
            if not unify(t, lt):
                self.err("E310", f"literal pattern of type `{show(lt)}` cannot match a `{show(t)}`", p)
            return
        if k == "PTuple":
            n = len(p.subs)
            vt = TCon(p.name, [TVar() for _ in range(n)])
            if not unify(t, vt):
                pt = prune(t)
                hint = f"the value has {len(pt.args)} parts" if isinstance(pt, TCon) and is_tuple_name(pt.name) else None
                self.err("E310", f"a tuple pattern of {n} parts cannot match a `{show(t)}`", p, hint)
            for sub, at in zip(p.subs, prune(vt).args):
                self.check_pattern(sub, at)
            return
        if k == "PVariant":
            if p.name not in self.variants:
                hint = self.suggest(p.name, list(self.variants))
                if p.name in self.structs:
                    hint = "struct values cannot be destructured in patterns; bind with a lowercase name and use fields"
                self.err("E201", f"unknown variant `{p.name}`", p, hint)
            self.check_vis(p.name, p)
            self.note(p, p.name, "variant")
            v = self.variants[p.name]
            mapping = {tp: TVar() for tp in v.tparams}
            vt = TCon(v.enum, [mapping[tp] for tp in v.tparams])
            if not unify(t, vt):
                self.err("E310", f"variant `{p.name}` belongs to `{v.enum}` but the value has type `{show(t)}`", p)
            if len(p.subs) != len(v.fields):
                self.err("E205", f"pattern `{p.name}` needs {len(v.fields)} sub-pattern(s) but has {len(p.subs)}", p,
                         f"write {p.name}({', '.join(f for f, _ in v.fields)})" if v.fields else f"write just {p.name}")
            for sub, (fname, ft) in zip(p.subs, v.fields):
                self.check_pattern(sub, substitute(ft, mapping))
            return

    def constructors(self, t):
        t = prune(t)
        if not isinstance(t, TCon):
            return None
        if t.name == "bool":
            return [("true", []), ("false", [])]
        if t.name == "Option":
            return [("Some", [t.args[0]]), ("None", [])]
        if t.name == "Result":
            return [("Ok", [t.args[0]]), ("Err", [t.args[1]])]
        if is_tuple_name(t.name):
            return [(t.name, list(t.args))]
        if t.name in self.enums:
            mapping = dict(zip(self.tparams_of.get(t.name, []), t.args))
            return [(vn, [substitute(ft, mapping) for _, ft in self.variants[vn].fields]) for vn in self.enums[t.name]]
        return None

    def exhaustive(self, rows, types):
        """Maranget-style usefulness check. Returns None if `rows` cover every value
        of `types`, else a witness: one pattern string per type that no row matches."""
        if not types:
            return None if rows else []
        if not rows:
            return ["_"] * len(types)
        ctors = self.constructors(types[0])
        irrefutable = lambda p: p.kind in ("PWild", "PBind")
        if ctors is None or all(irrefutable(r[0]) for r in rows):
            default = [r[1:] for r in rows if irrefutable(r[0])]
            miss = self.exhaustive(default, types[1:])
            return None if miss is None else ["_"] + miss
        for cname, ftypes in ctors:
            spec = []
            for r in rows:
                p = r[0]
                if irrefutable(p):
                    spec.append([Node("PWild", 0, 0)] * len(ftypes) + r[1:])
                elif p.kind in ("PVariant", "PTuple") and p.name == cname and len(p.subs) == len(ftypes):
                    spec.append(list(p.subs) + r[1:])
                elif p.kind == "PLit" and isinstance(p.value, bool) and str(p.value).lower() == cname:
                    spec.append(r[1:])
            miss = self.exhaustive(spec, ftypes + types[1:])
            if miss is not None:
                n = len(ftypes)
                head = f"{cname}({', '.join(miss[:n])})" if n else cname
                if is_tuple_name(cname):
                    head = f"({', '.join(miss[:n])})"
                return [head] + miss[n:]
        return None


def check_source(src, file=None, overlay=None):
    """Parse the entry file and its modules, then check everything. Returns (program, errors)."""
    from .loader import load_program
    try:
        prog = load_program(src, file, overlay)
    except KlarError as e:
        return None, [e]
    c = Checker(file)
    errors = c.check_program(prog)
    errors.sort(key=lambda x: (x.file != file, x.file or "", x.line, x.col))
    prog.checker = c
    return prog, errors
