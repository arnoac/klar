"""Typed SQL rows (interpreter side; klar_std.js and klar_std.h follow the same rules for compiled programs).

  sql(value)       int -> SqlInt, float -> SqlFloat, str -> SqlText, bool -> SqlInt 0/1, unit and None -> SqlNull,
                   Some(x) -> sql(x), an enum whose variants have no fields -> SqlText(variant name),
                   anything else (lists, maps, structs, tuples, other enums) -> SqlText(JSON)
  sql_read(rows)   a struct reads its fields from the columns of the same name, a tuple reads the columns in order,
                   any other type reads the only column; each column converts back by the rules above
"""

from .types import TCon, prune, substitute
from .syntax import is_tuple_name


class SqlReadError(Exception):
    pass


def plain_enum(c, name):
    """An enum stored as its variant name: every variant is without fields."""
    return name in c.enums and name not in ("Json",) and all(not c.variants[v].fields for v in c.enums[name])


def sql_kind(v):
    if v.variant == "SqlInt":
        return "a whole number"
    if v.variant == "SqlFloat":
        return "a decimal number"
    if v.variant == "SqlText":
        return "text"
    return "NULL"


class SqlConv:
    def __init__(self, checker, json_conv):
        self.c = checker
        self.j = json_conv

    # ---------------------------------------------------------------- Klar value -> SqlValue
    def value(self, v, t):
        from .interp import EnumVal
        from .jsonconv import dumps
        t = prune(t)
        n = t.name if isinstance(t, TCon) else None
        sv = lambda tag, x: EnumVal(tag, (x,), ("value",))
        if n == "int":
            return sv("SqlInt", v)
        if n == "float":
            return sv("SqlFloat", v)
        if n == "str":
            return sv("SqlText", v)
        if n == "bool":
            return sv("SqlInt", 1 if v else 0)
        if n == "unit":
            return EnumVal("SqlNull", ())
        if n == "Option":
            return EnumVal("SqlNull", ()) if v.variant == "None" else self.value(v.values[0], t.args[0])
        if n == "SqlValue":
            return v
        if plain_enum(self.c, n):
            return sv("SqlText", v.variant)
        return sv("SqlText", dumps(self.j.encode(v, t)))

    # ---------------------------------------------------------------- rows -> Klar values
    def read(self, rows, t):
        from .interp import StructVal
        t = prune(t)
        n = t.name
        out = []
        for row in rows:
            cols = list(row.keys())
            if is_tuple_name(n):
                if len(cols) != len(t.args):
                    raise SqlReadError(f"the query returns {len(cols)} column(s) but the tuple has {len(t.args)} values")
                out.append(StructVal(n, tuple((str(i), self.column(row[col], at, col, f"tuple value .{i}"))
                                              for i, (col, at) in enumerate(zip(cols, t.args)))))
            elif n in self.c.structs:
                vals = []
                for f, ft in self.c.struct_fields(t):
                    if f not in row:
                        raise SqlReadError(f"the query has no column `{f}` for field `{n}.{f}`; select it, or name a "
                                           f"column with `as {f}`")
                    vals.append((f, self.column(row[f], ft, f, f"field `{n}.{f}`")))
                out.append(StructVal(n, tuple(vals)))
            else:
                if len(cols) != 1:
                    raise SqlReadError(f"the query returns {len(cols)} columns; select exactly one to read single values, "
                                       f"or read the rows into a struct or a tuple")
                out.append(self.column(row[cols[0]], t, cols[0], "the result"))
        return tuple(out)

    def column(self, v, t, col, target):
        from .interp import EnumVal, NONE, some
        from .jsonconv import JsonTypeError
        import json
        t = prune(t)
        n = t.name
        tag = v.variant
        x = v.values[0] if v.values else None

        def bad(want):
            raise SqlReadError(f"column `{col}` holds {sql_kind(v)}, but {target} needs {want}")

        if n == "Option":
            return NONE if tag == "SqlNull" else some(self.column(v, t.args[0], col, target))
        if tag == "SqlNull" and n not in ("unit", "SqlValue"):
            raise SqlReadError(f"column `{col}` is NULL, but {target} is not an Option; make it an Option, or select "
                               f"a value instead of NULL")
        if n == "int":
            if tag != "SqlInt":
                bad("a whole number")
            return x
        if n == "float":
            if tag == "SqlInt":
                return float(x)
            if tag != "SqlFloat":
                bad("a number")
            return x
        if n == "str":
            if tag != "SqlText":
                bad("text")
            return x
        if n == "bool":
            if tag != "SqlInt" or x not in (0, 1):
                raise SqlReadError(f"column `{col}` holds {self.show(v)}, but {target} needs 0 or 1")
            return x == 1
        if n == "unit":
            if tag != "SqlNull":
                bad("NULL")
            return None
        if n == "SqlValue":
            return v
        if plain_enum(self.c, n):
            if tag != "SqlText":
                bad(f"the name of a {n} variant")
            if x not in self.c.enums[n]:
                raise SqlReadError(f"column `{col}` holds \"{x}\", which is not a variant of {n}")
            return EnumVal(x, ())
        if tag != "SqlText":
            bad("JSON text")
        try:
            raw = json.loads(x)
        except ValueError:
            raise SqlReadError(f"column `{col}` is not valid JSON, but {target} is read from JSON text")
        try:
            return self.j.decode(raw, t)
        except JsonTypeError as ex:
            raise SqlReadError(f"column `{col}`: {ex}")

    @staticmethod
    def show(v):
        if v.variant == "SqlText":
            return "text"
        if v.variant == "SqlNull":
            return "NULL"
        return str(v.values[0]) if v.variant == "SqlInt" else "a decimal number"
