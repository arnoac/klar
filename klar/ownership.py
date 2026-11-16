"""Ownership of list variables, shared by the C and JavaScript backends.

A `var` holding a list gets a hidden flag that is true while the list (and its buffer) is known to be
referenced only by that variable: right after it was built by a list literal or range(), or after a
`v = set_at(v, ...)` that copied it. While the flag is set, `v = set_at(v, i, x)` and `v = push(v, x)`
write straight into the buffer, which is what makes array code run at C speed. Reading `v[i]` or `len(v)`
keeps the flag; any other use of `v` (passing it on, storing it, iterating it) clears the flag first, so no
other value can ever observe the in-place writes. Values stay immutable as far as any program can tell.
"""


def _builtin_call(e, fns):
    if e.kind == "MethodCall":
        e = getattr(e, "desugared", e)
    if e.kind != "Call" or getattr(e, "target_kind", "") != "fn" or e.named:
        return None
    if e.callee.kind != "Name" or e.callee.name not in ("set_at", "push") or e.callee.name in fns:
        return None
    return e if e.args else None


def self_update(s, fns):
    """For `v = set_at(v, i, x)`, `v = push(v, x)` or a nest of them such as `v = set_at(set_at(v, i, a), j, b)`,
    return the steps [(builtin name, call node), ...] innermost first, else None. The backends evaluate every
    index and value of every step first (they all read the old `v`, as the nested calls would) and then apply the
    writes in order."""
    steps = []
    e = s.value
    while True:
        call = _builtin_call(e, fns)
        if call is None:
            return None
        steps.append((call.callee.name, call))
        e = call.args[0]
        if e.kind == "Name":
            if e.name != s.name:
                return None
            steps.reverse()
            return steps


def is_fresh(e, fns):
    """A list expression whose result nothing else can reference."""
    if e.kind == "ListLit":
        return True
    return (e.kind == "Call" and getattr(e, "target_kind", "") == "fn" and e.callee.kind == "Name"
            and e.callee.name == "range" and "range" not in fns)
