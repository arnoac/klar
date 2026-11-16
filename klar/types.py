"""Type representation and unification."""

import itertools

_ids = itertools.count(1)


class TCon:
    __slots__ = ("name", "args")

    def __init__(self, name, args=()):
        self.name = name
        self.args = list(args)

    def __repr__(self):
        return show(self)


class TFn:
    __slots__ = ("params", "ret", "io")

    def __init__(self, params, ret, io=False):
        self.params = list(params)
        self.ret = ret
        self.io = io

    def __repr__(self):
        return show(self)


class TVar:
    __slots__ = ("id", "ref")

    def __init__(self):
        self.id = next(_ids)
        self.ref = None

    def __repr__(self):
        return show(self)


INT = TCon("int")
FLOAT = TCon("float")
STR = TCon("str")
BOOL = TCon("bool")
UNIT = TCon("unit")
NEVER = TCon("never")

PRIMS = {"int": INT, "float": FLOAT, "str": STR, "bool": BOOL, "unit": UNIT, "never": NEVER}
GENERIC_ARITY = {"list": 1, "map": 2, "Option": 1, "Result": 2}


def list_of(t):
    return TCon("list", [t])


def option_of(t):
    return TCon("Option", [t])


def prune(t):
    while isinstance(t, TVar) and t.ref is not None:
        t = t.ref
    return t


def resolve(t):
    t = prune(t)
    if isinstance(t, TCon):
        return TCon(t.name, [resolve(a) for a in t.args])
    if isinstance(t, TFn):
        return TFn([resolve(p) for p in t.params], resolve(t.ret), t.io)
    return t


def show(t):
    t = prune(t)
    if isinstance(t, TVar):
        return "_"
    if isinstance(t, TFn):
        return "fn(" + ", ".join(show(p) for p in t.params) + ") -> " + show(t.ret) + (" uses io" if t.io else "")
    if t.args:
        if t.name.startswith("Tuple") and len(t.name) == 6 and t.name[5].isdigit():
            return "(" + ", ".join(show(a) for a in t.args) + ")"
        return t.name + "[" + ", ".join(show(a) for a in t.args) + "]"
    return t.name


def has_vars(t):
    t = prune(t)
    if isinstance(t, TVar):
        return True
    if isinstance(t, TCon):
        return any(has_vars(a) for a in t.args)
    return any(has_vars(p) for p in t.params) or has_vars(t.ret)


def occurs(v, t):
    t = prune(t)
    if t is v:
        return True
    if isinstance(t, TCon):
        return any(occurs(v, a) for a in t.args)
    if isinstance(t, TFn):
        return any(occurs(v, p) for p in t.params) or occurs(v, t.ret)
    return False


def unify(a, b):
    """Return True if a and b can be made equal (binding variables), else False."""
    a, b = prune(a), prune(b)
    if a is b:
        return True
    if isinstance(a, TCon) and a.name == "never":
        return True
    if isinstance(b, TCon) and b.name == "never":
        return True
    if isinstance(a, TVar):
        if occurs(a, b):
            return False
        a.ref = b
        return True
    if isinstance(b, TVar):
        return unify(b, a)
    if isinstance(a, TCon) and isinstance(b, TCon):
        if a.name != b.name or len(a.args) != len(b.args):
            return False
        return all(unify(x, y) for x, y in zip(a.args, b.args))
    if isinstance(a, TFn) and isinstance(b, TFn):
        if len(a.params) != len(b.params):
            return False
        if a.io and not b.io:
            return False   # an io function cannot stand in for a pure one (the reverse is fine)
        return all(unify(x, y) for x, y in zip(a.params, b.params)) and unify(a.ret, b.ret)
    return False


def substitute(t, mapping):
    t = prune(t)
    if isinstance(t, TCon):
        if not t.args and t.name in mapping:
            return mapping[t.name]
        return TCon(t.name, [substitute(a, mapping) for a in t.args])
    if isinstance(t, TFn):
        return TFn([substitute(p, mapping) for p in t.params], substitute(t.ret, mapping), t.io)
    return t
