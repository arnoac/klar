"""Structured diagnostics for Klar.

Every diagnostic has a stable code (e.g. E201), a one-line message, an exact
source location, and a concrete hint describing how to fix it. Diagnostics can
be rendered for humans or emitted as JSON for agents.
"""


# Stable catalog: code -> (title, longer explanation used by `klar explain`).
CATALOG = {
    # Lexing / parsing (E1xx)
    "E101": ("unexpected character", "The source contains a character that is not part of Klar's syntax."),
    "E102": ("unterminated string", "A string literal was opened with \" but never closed on the same line."),
    "E103": ("unexpected token", "The parser expected a different token here. The message lists what was expected."),
    "E104": ("invalid interpolation", "A {expr} inside a string literal could not be parsed. Use {{ and }} for literal braces."),
    "E105": ("call or index on a new line", "In Klar, `(` and `[` that start a call or index must be on the same line as the expression before them."),
    # Names / declarations (E2xx)
    "E201": ("unknown name", "A variable, function, type or variant was used but never declared."),
    "E202": ("duplicate declaration", "Two declarations share a name. Klar forbids shadowing and overloading, so every name in scope is unique."),
    "E203": ("assignment to immutable binding", "Only bindings declared with `var` can be reassigned. `let` bindings are constant."),
    "E204": ("unknown field", "The struct has no field with this name."),
    "E205": ("wrong argument count", "A function, struct or variant was called with the wrong number of arguments."),
    "E206": ("bad named arguments", "Named arguments must match the declared parameter names exactly, once each, and cannot be mixed with positional ones."),
    # Types (E3xx)
    "E301": ("type mismatch", "An expression has a different type from the one required. Klar never converts types implicitly."),
    "E302": ("operator type error", "This operator is not defined for these operand types."),
    "E303": ("not callable", "Only functions, struct names and enum variants can be called."),
    "E304": ("cannot infer type", "The type of an empty literal or unused generic could not be determined. Add a type annotation."),
    "E305": ("missing return", "A function with a non-unit return type can reach its end without returning a value."),
    "E306": ("non-exhaustive match", "A match does not cover every possible value. Add the missing cases or a `_` arm."),
    "E307": ("invalid `?`", "The `?` operator works only on Option or Result values inside a function returning the same kind."),
    "E308": ("missing effect", "A function performs IO (directly or by calling an IO function) but its signature lacks `uses io`."),
    "E309": ("break/continue outside loop", "`break` and `continue` may only appear inside `while` or `for` loops."),
    "E312": ("trait not implemented", "A value is used where its type must implement a trait (a bound like `T: Show`, or a trait method), but there is no `impl Show for ThatType { ... }`, or a type parameter lacks the bound. Add the impl, or add the bound to the generic function's type parameter."),
    "E313": ("SQL built from values", "The `sql` argument of a database call is assembled with interpolation or `+` from values. A value containing a quote would then change the query (SQL injection). Put `?` where each value goes and pass the values in the params list, e.g. sqlite_query(db, \"select * from users where name = ?\", [SqlText(name)]). A table or column name that varies cannot be a parameter: choose it from a fixed list of known names in your code."),
    "E311": ("captured var", "An anonymous function refers to a `var` declared outside it. Functions capture values, so copy the current value into a `let` first."),
    "E310": ("bad pattern", "This pattern cannot match a value of the scrutinee's type."),
    # Runtime (E4xx)
    "E401": ("index out of bounds", "A list or string was indexed outside 0..len-1."),
    "E402": ("division by zero", "An integer division or remainder had a zero divisor."),
    "E403": ("panic", "The program called panic() or unwrap() on an empty value."),
    "E404": ("assertion failed", "An `assert` condition evaluated to false."),
    "E405": ("missing map key", "A map was indexed with a key it does not contain. Use get(m, k) for a safe lookup."),
    "E406": ("stack overflow", "Recursion went too deep."),
    "E407": ("io error", "A file or system operation failed, or a Python extern misbehaved."),
    "E408": ("integer overflow", "Compiled programs use 64-bit integers; this operation overflowed. The interpreter has unlimited integers."),
    "E409": ("deadlock", "Every task is waiting (on a channel, or on other tasks) and none can go on. Look for a value that is never sent, a channel that is never closed before a loop reads it to the end, or two tasks waiting on each other."),
    "E501": ("not supported by the compiler", "This feature works with `klar run` but cannot be compiled yet."),
    "E502": ("no WebAssembly compiler", "WebAssembly builds need clang with its wasm32 target and the wasm-ld linker (LLVM lld). Install clang and lld, or point KLAR_WASM_CC at a clang that has them."),
    "E503": ("internal compiler error", "The C generated for a WebAssembly build did not compile. This is a bug in Klar; please report it with the program."),
    "E601": ("package error", "klar.toml, klar.lock or a dependency is missing or invalid. Dependencies are listed in klar.toml and fetched with `klar install`; `klar add` edits klar.toml for you."),
}


class KlarError(Exception):
    def __init__(self, code, message, line=0, col=0, hint=None, file=None, trace=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.line = line
        self.col = col
        self.hint = hint
        self.file = file
        self.trace = trace or []

    @property
    def phase(self):
        return {"1": "parse", "2": "check", "3": "check", "4": "runtime", "5": "compile"}[self.code[1]]

    def to_dict(self):
        d = {
            "code": self.code,
            "phase": self.phase,
            "title": CATALOG.get(self.code, ("", ""))[0],
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "col": self.col,
            "hint": self.hint,
        }
        if self.trace:
            d["trace"] = self.trace
        return d

    def render(self, source=None):
        loc = f"{self.file or '<input>'}:{self.line}:{self.col}"
        out = [f"error[{self.code}] {loc}: {self.message}"]
        if source and self.line > 0:
            lines = source.splitlines()
            if self.line <= len(lines):
                text = lines[self.line - 1]
                out.append(f"  {self.line:>4} | {text}")
                out.append("       | " + " " * max(self.col - 1, 0) + "^")
        if self.hint:
            out.append(f"  hint: {self.hint}")
        for fr in self.trace:
            out.append(f"  at {fr}")
        return "\n".join(out)


def to_json(errors):
    import json
    return json.dumps({"ok": not errors, "errors": [e.to_dict() for e in errors]}, indent=2)
