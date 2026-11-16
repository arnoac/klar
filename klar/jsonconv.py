"""Automatic JSON for any Klar type (interpreter side; klar_std.js has the same rules for compiled programs).

  int, float, str, bool    number, number, string, true/false
  unit                     null
  list[T]                  array
  map[str, V]              object; a map with other key types is an array of [key, value] pairs
  Option[T]                null or the value; a struct field of Option type may be missing
  Result[T, E]             {"ok": value} or {"err": error}
  struct                   object with one key per field, in declaration order; unknown keys are ignored
  enum                     "Variant" for a variant without fields, else {"type": "Variant", field: value, ...}
  Json                     itself
"""

import math

from .types import TCon, TFn, prune, substitute

INT_MIN, INT_MAX = -2 ** 63, 2 ** 63 - 1


class JsonTypeError(Exception):
    pass


def js_number(f):
    """A float printed the way JavaScript prints numbers, so both runtimes write identical JSON."""
    if f != f or f in (float("inf"), float("-inf")):
        return "null"
    if f == 0:
        return "0"
    from decimal import Decimal
    sign, digits, exp = Decimal(repr(f)).as_tuple()
    ds = "".join(map(str, digits)).rstrip("0") or "0"
    exp += len("".join(map(str, digits))) - len(ds)
    k = len(ds)
    n = exp + k                      # the decimal point sits after n digits
    neg = "-" if sign else ""
    if k <= n <= 21:
        return neg + ds + "0" * (n - k)
    if 0 < n <= 21:
        return neg + ds[:n] + "." + ds[n:]
    if -6 < n <= 0:
        return neg + "0." + "0" * (-n) + ds
    e = n - 1
    es = ("+" if e > 0 else "-") + str(abs(e))
    return neg + (ds if k == 1 else ds[0] + "." + ds[1:]) + "e" + es


def dumps(x, indent="", cur=""):
    """JSON text for a Python JSON value, byte-identical to the JavaScript runtime's $json_write."""
    import json
    if x is None:
        return "null"
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float):
        return js_number(x)
    if isinstance(x, str):
        return json.dumps(x, ensure_ascii=False)
    if isinstance(x, list):
        items, open_, close = [dumps(i, indent, cur + indent) for i in x], "[", "]"
    else:
        sep = ": " if indent else ":"
        items = [json.dumps(k, ensure_ascii=False) + sep + dumps(i, indent, cur + indent) for k, i in x.items()]
        open_, close = "{", "}"
    if not items:
        return open_ + close
    if not indent:
        return open_ + ",".join(items) + close
    return open_ + "\n" + cur + indent + (",\n" + cur + indent).join(items) + "\n" + cur + close


def _kind(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "a bool"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return js_number(v)
    if isinstance(v, str):
        return "a string"
    if isinstance(v, list):
        return "an array"
    return "an object"


def _has_params(t, known):
    t = prune(t)
    if isinstance(t, TFn):
        return True
    if not isinstance(t, TCon):
        return True
    if not t.args and t.name not in known:
        return True
    return any(_has_params(a, known) for a in t.args)


class JsonConv:
    def __init__(self, checker):
        self.c = checker

    def concrete(self, t):
        """True if t names only real types (no type parameters left)."""
        from .checker import PRIMS
        c = self.c
        known = set(c.structs) | set(c.enums) | set(PRIMS) | set(c.extern_types) | {"list", "map", "Option", "Result"}
        return not _has_params(t, known)

    # ---------------------------------------------------------------- Klar value -> Python JSON value
    def encode(self, v, t):
        from .interp import StructVal, EnumVal
        from .stdpy import jsonlib
        t = prune(t) if t is not None else None
        n = t.name if isinstance(t, TCon) else None
        if n == "Json":
            return jsonlib._from_klar(self._json_to_py_tuple(v))
        if isinstance(v, bool) or isinstance(v, str) or v is None:
            return v
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            if math.isnan(v) or math.isinf(v):
                return None
            return v
        if isinstance(v, tuple):
            et = t.args[0] if n == "list" else None
            return [self.encode(x, et) for x in v]
        if isinstance(v, dict):
            kt = prune(t.args[0]) if n == "map" else None
            vt = t.args[1] if n == "map" else None
            if (kt is not None and isinstance(kt, TCon) and kt.name == "str") or (kt is None and all(isinstance(k, str) for k in v)):
                return {k: self.encode(x, vt) for k, x in v.items()}
            return [[self.encode(k, kt), self.encode(x, vt)] for k, x in v.items()]
        if isinstance(v, StructVal):
            ftypes = dict(self.c.struct_fields(t)) if n in self.c.structs and self.concrete(t) else {}
            return {k: self.encode(x, ftypes.get(k)) for k, x in v.fields}
        if isinstance(v, EnumVal):
            if v.variant == "None":
                return None
            if v.variant == "Some":
                return self.encode(v.values[0], t.args[0] if n == "Option" else None)
            if v.variant in ("Ok", "Err"):
                at = (t.args[0] if v.variant == "Ok" else t.args[1]) if n == "Result" else None
                return {v.variant.lower(): self.encode(v.values[0], at)}
            if v.variant in ("JNull", "JBool", "JNum", "JStr", "JArr", "JObj") and v.variant in self.c.variants \
                    and self.c.variants[v.variant].enum == "Json":
                return self.encode(v, TCon("Json"))
            if not v.values:
                return v.variant
            info = self.c.variants[v.variant]
            mapping = dict(zip(self.c.tparams_of.get(info.enum, []), t.args)) if n == info.enum else None
            out = {"type": v.variant}
            for (f, ft), x in zip(info.fields, v.values):
                out[f] = self.encode(x, substitute(ft, mapping) if mapping is not None else None)
            return out
        raise JsonTypeError(f"a {type(v).__name__} value cannot be turned into JSON")

    def _json_to_py_tuple(self, v):
        """The Json enum as jsonlib's tuple form."""
        if v.variant == "JNull":
            return "JNull"
        x = v.values[0]
        if v.variant == "JArr":
            return ("JArr", [self._json_to_py_tuple(i) for i in x])
        if v.variant == "JObj":
            return ("JObj", {k: self._json_to_py_tuple(i) for k, i in x.items()})
        return (v.variant, x)

    # ---------------------------------------------------------------- Python JSON value -> Klar value
    def decode(self, j, t, path="$"):
        from .interp import StructVal, EnumVal, FrozenMap, NONE, some, ok, errv
        t = prune(t)
        if not isinstance(t, TCon):
            raise JsonTypeError(f"cannot decode into a function type")
        n = t.name

        def bad(want):
            raise JsonTypeError(f"expected {want} at {path}, found {_kind(j)}")

        if n == "int":
            if isinstance(j, bool) or not isinstance(j, (int, float)):
                bad("a whole number")
            if isinstance(j, float):
                if not j.is_integer():
                    bad("a whole number")
                j = int(j)
            if j < INT_MIN or j > INT_MAX:
                raise JsonTypeError(f"number at {path} does not fit in a 64-bit int")
            return j
        if n == "float":
            if isinstance(j, bool) or not isinstance(j, (int, float)):
                bad("a number")
            return float(j)
        if n == "str":
            if not isinstance(j, str):
                bad("a string")
            return j
        if n == "bool":
            if not isinstance(j, bool):
                bad("true or false")
            return j
        if n == "unit":
            if j is not None:
                bad("null")
            return None
        if n == "Json":
            return self._py_to_json_enum(j)
        if n == "list":
            if not isinstance(j, list):
                bad("an array")
            return tuple(self.decode(x, t.args[0], f"{path}[{i}]") for i, x in enumerate(j))
        if n == "map":
            kt = prune(t.args[0])
            if isinstance(kt, TCon) and kt.name == "str":
                if not isinstance(j, dict):
                    bad("an object")
                return FrozenMap((k, self.decode(x, t.args[1], f"{path}.{k}")) for k, x in j.items())
            if not isinstance(j, list):
                bad("an array of [key, value] pairs")
            out = FrozenMap()
            for i, pair in enumerate(j):
                if not isinstance(pair, list) or len(pair) != 2:
                    raise JsonTypeError(f"expected a [key, value] pair at {path}[{i}], found {_kind(pair)}")
                out[self.decode(pair[0], kt, f"{path}[{i}][0]")] = self.decode(pair[1], t.args[1], f"{path}[{i}][1]")
            return out
        if n == "Option":
            return NONE if j is None else some(self.decode(j, t.args[0], path))
        if n == "Result":
            if isinstance(j, dict) and len(j) == 1 and ("ok" in j or "err" in j):
                if "ok" in j:
                    return ok(self.decode(j["ok"], t.args[0], f"{path}.ok"))
                return errv(self.decode(j["err"], t.args[1], f"{path}.err"))
            bad('{"ok": ...} or {"err": ...}')
        c = self.c
        if n in c.structs:
            if not isinstance(j, dict):
                bad(f"an object ({n})")
            vals = []
            for f, ft in c.struct_fields(t):
                if f in j:
                    vals.append((f, self.decode(j[f], ft, f"{path}.{f}")))
                elif isinstance(prune(ft), TCon) and prune(ft).name == "Option":
                    vals.append((f, NONE))
                else:
                    raise JsonTypeError(f"missing field `{f}` at {path} ({n})")
            return StructVal(n, tuple(vals))
        if n in c.enums:
            if isinstance(j, str):
                tag, obj = j, None
            elif isinstance(j, dict) and isinstance(j.get("type"), str):
                tag, obj = j["type"], j
            else:
                bad(f"a {n} variant name or an object with a \"type\" key")
            if tag not in c.enums[n]:
                raise JsonTypeError(f"`{tag}` at {path} is not a variant of {n}")
            info = c.variants[tag]
            mapping = dict(zip(c.tparams_of.get(n, []), t.args))
            if info.fields and obj is None:
                raise JsonTypeError(f"variant {tag} at {path} has fields, so it must be an object with a \"type\" key")
            vals = []
            for f, ft in info.fields:
                ft = substitute(ft, mapping)
                if f in obj:
                    vals.append(self.decode(obj[f], ft, f"{path}.{f}"))
                elif isinstance(prune(ft), TCon) and prune(ft).name == "Option":
                    vals.append(NONE)
                else:
                    raise JsonTypeError(f"missing field `{f}` at {path} ({tag})")
            return EnumVal(tag, tuple(vals), tuple(f for f, _ in info.fields))
        raise JsonTypeError(f"values of type {n} cannot be read from JSON")

    def _py_to_json_enum(self, j):
        from .interp import EnumVal, FrozenMap
        if j is None:
            return EnumVal("JNull", ())
        if isinstance(j, bool):
            return EnumVal("JBool", (j,), ("value",))
        if isinstance(j, (int, float)):
            return EnumVal("JNum", (float(j),), ("value",))
        if isinstance(j, str):
            return EnumVal("JStr", (j,), ("value",))
        if isinstance(j, list):
            return EnumVal("JArr", (tuple(self._py_to_json_enum(x) for x in j),), ("items",))
        return EnumVal("JObj", (FrozenMap((k, self._py_to_json_enum(x)) for k, x in j.items()),), ("fields",))
