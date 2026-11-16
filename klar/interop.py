"""Python interop for `extern` functions.

Klar values cross into Python by value shape; Python values come back into Klar guided by the declared
Klar type, so an extern's signature is a checked contract:

  Klar                     Python
  int float str bool       int float str bool
  unit                     None
  list[T]                  list (any iterable on the way back)
  map[K, V]                dict
  Option[T]                value or None
  Result[T, str]           value; a raised exception becomes Err(message)
  struct                   dict of fields (objects with attributes are accepted on the way back)
  enum variant             ("Variant", field1, ...) tuple, or just "Variant" for a variant without fields
  fn(...) -> R             Python callable (both directions)
  extern type              the Python object itself, opaque to Klar
"""

import importlib
import os
import sys

from .types import TCon, TFn, prune


class Opaque:
    """A Python object held by Klar code (an `extern type` value)."""
    __slots__ = ("type_name", "obj")

    def __init__(self, type_name, obj):
        self.type_name = type_name
        self.obj = obj

    def __eq__(self, other):
        return isinstance(other, Opaque) and self.obj is other.obj

    def __hash__(self):
        return id(self.obj)


class PyFn:
    """A Python callable that Klar code can call, with the Klar type it was received as."""
    def __init__(self, fn, ty):
        self.fn = fn
        self.ty = ty


class InteropError(Exception):
    pass


def resolve_target(target, klar_file):
    mod_name, _, attr = target.rpartition(".")
    if klar_file:
        d = os.path.dirname(os.path.abspath(klar_file))
        if d not in sys.path:
            sys.path.insert(0, d)
    mod = importlib.import_module(mod_name)
    obj = mod
    for part in attr.split(":"):
        obj = getattr(obj, part)
    return obj


class Converter:
    def __init__(self, interp, checker):
        self.interp = interp
        self.checker = checker

    # ---- Klar -> Python
    def to_py(self, v, ty=None):
        from .interp import StructVal, EnumVal, Closure, FnRef, FrozenMap
        t = prune(ty) if ty is not None else None
        if isinstance(v, (bool, int, float, str)) or v is None:
            return v
        if isinstance(v, tuple):
            et = t.args[0] if isinstance(t, TCon) and t.name == "list" else None
            return [self.to_py(x, et) for x in v]
        if isinstance(v, dict):
            vt = t.args[1] if isinstance(t, TCon) and t.name == "map" else None
            return {self.to_py(k): self.to_py(x, vt) for k, x in v.items()}
        if isinstance(v, StructVal):
            return {k: self.to_py(x) for k, x in v.fields}
        if isinstance(v, EnumVal):
            if v.variant == "Some":
                return self.to_py(v.values[0], t.args[0] if isinstance(t, TCon) and t.args else None)
            if v.variant == "None":
                return None
            if not v.values:
                return v.variant
            return (v.variant,) + tuple(self.to_py(x) for x in v.values)
        if isinstance(v, Opaque):
            return v.obj
        if isinstance(v, PyFn):
            return v.fn
        if isinstance(v, (Closure, FnRef)):
            interp = self.interp
            fty = t if isinstance(t, TFn) else None

            def call_from_python(*args):
                kargs = [self.from_py(a, fty.params[i]) if fty else a for i, a in enumerate(args)]
                with interp.rt.cond:          # Klar code runs only while holding the task baton
                    return self.to_py(interp.call_value(v, kargs, None), fty.ret if fty else None)
            return call_from_python
        return v

    # ---- Python -> Klar, guided by the declared type
    def from_py(self, v, ty, where="result"):
        from .interp import StructVal, EnumVal, FrozenMap, NONE, some, ok, errv
        t = prune(ty)
        if isinstance(t, TFn):
            if not callable(v):
                raise InteropError(f"{where}: expected a callable for `{t}`, got {type(v).__name__}")
            return PyFn(v, t)
        if not isinstance(t, TCon):
            return v
        n = t.name
        if n == "int":
            if isinstance(v, bool) or not isinstance(v, int):
                if isinstance(v, float) and v.is_integer():
                    return int(v)
                raise InteropError(f"{where}: expected int, got {type(v).__name__} {v!r}")
            return v
        if n == "float":
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise InteropError(f"{where}: expected float, got {type(v).__name__} {v!r}")
            return float(v)
        if n == "str":
            if not isinstance(v, str):
                raise InteropError(f"{where}: expected str, got {type(v).__name__} {v!r}")
            return v
        if n == "bool":
            if not isinstance(v, bool):
                raise InteropError(f"{where}: expected bool, got {type(v).__name__} {v!r}")
            return v
        if n in ("unit", "never"):
            return None
        if n == "list":
            return tuple(self.from_py(x, t.args[0], where + "[i]") for x in v)
        if n == "map":
            return FrozenMap((self.from_py(k, t.args[0], where + " key"), self.from_py(x, t.args[1], where + " value"))
                             for k, x in dict(v).items())
        if n == "Option":
            return NONE if v is None else some(self.from_py(v, t.args[0], where))
        if n == "Result":
            if isinstance(v, tuple) and len(v) == 2 and v[0] in ("Ok", "Err"):
                return ok(self.from_py(v[1], t.args[0], where)) if v[0] == "Ok" else errv(self.from_py(v[1], t.args[1], where))
            return ok(self.from_py(v, t.args[0], where))
        c = self.checker
        if n in c.extern_types:
            if isinstance(v, Opaque):
                return v
            return Opaque(n, v)
        if n in c.structs:
            fields = c.struct_fields(t)
            vals = []
            for f, ft in fields:
                if isinstance(v, dict):
                    if f not in v:
                        raise InteropError(f"{where}: struct {n} needs field `{f}`, missing from Python dict")
                    raw = v[f]
                else:
                    if not hasattr(v, f):
                        raise InteropError(f"{where}: struct {n} needs field `{f}`, missing on {type(v).__name__}")
                    raw = getattr(v, f)
                vals.append((f, self.from_py(raw, ft, f"{where}.{f}")))
            return StructVal(n, tuple(vals))
        if n in c.enums:
            from .types import substitute
            tag, rest = (v, ()) if isinstance(v, str) else (v[0], tuple(v[1:]))
            if tag not in c.enums[n]:
                raise InteropError(f"{where}: `{tag}` is not a variant of {n}")
            info = c.variants[tag]
            mapping = dict(zip(c.tparams_of.get(n, []), t.args))
            if len(rest) != len(info.fields):
                raise InteropError(f"{where}: variant {tag} needs {len(info.fields)} value(s), got {len(rest)}")
            vals = tuple(self.from_py(x, substitute(ft, mapping), f"{where}.{tag}") for x, (_, ft) in zip(rest, info.fields))
            return EnumVal(tag, vals, tuple(f for f, _ in info.fields))
        return v
