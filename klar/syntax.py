"""Lexer and parser for Klar. Produces a tree of `Node` objects."""

from .errors import KlarError

KEYWORDS = {
    "fn", "let", "var", "if", "then", "else", "while", "for", "in", "return",
    "break", "continue", "match", "struct", "enum", "uses", "true", "false",
    "and", "or", "not", "assert", "test", "with", "use", "extern", "type", "trait", "impl",
}

SYMBOLS = [
    "->", "=>", "==", "!=", "<=", ">=",
    "(", ")", "[", "]", "{", "}", ",", ":", ".", "=", "<", ">",
    "+", "-", "*", "/", "%", "?", ";",
]


class Token:
    __slots__ = ("kind", "value", "line", "col", "end_line")

    def __init__(self, kind, value, line, col, end_line=None):
        self.kind = kind      # 'ident', 'int', 'float', 'str', 'kw', 'sym', 'eof'
        self.value = value
        self.line = line
        self.col = col
        self.end_line = end_line or line

    def __repr__(self):
        return f"Token({self.kind},{self.value!r},{self.line}:{self.col})"


class Node:
    """Generic AST node: `kind` plus keyword fields. `ty` is filled by the checker."""

    def __init__(self, kind, line, col, **fields):
        self.kind = kind
        self.line = line
        self.col = col
        self.ty = None
        self.__dict__.update(fields)

    def __repr__(self):
        fields = {k: v for k, v in self.__dict__.items() if k not in ("kind", "line", "col", "ty")}
        return f"{self.kind}{fields}"


# ---------------------------------------------------------------- lexer

ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "r": "\r", "0": "\0"}


def _string_end(src, j, triple, raw):
    """Index of the closing quote(s) of a string whose body starts at j, or -1."""
    n = len(src)
    close = '"""' if triple else '"'
    while True:
        if j >= n or (not triple and src[j] == "\n"):
            return -1
        c = src[j]
        if c == "\\":
            j += 2
            continue
        if src.startswith(close, j):
            return j
        if not raw and c == "{":
            if src.startswith("{{", j):
                j += 2
                continue
            k = _interp_end(src, j + 1, triple)
            if k == -1:
                return -1
            j = k + 1
            continue
        j += 1


def _interp_end(src, j, multiline):
    """Index of the `}` closing an interpolation whose code starts at j (skipping nested strings), or -1."""
    n = len(src)
    depth = 1
    while j < n:
        c = src[j]
        if c == "\n" and not multiline:
            return -1
        if c == '"' or (c == "r" and src.startswith('r"', j) and (j == 0 or not (src[j - 1].isalnum() or src[j - 1] == "_"))):
            raw = c == "r"
            start = j + (1 if raw else 0)
            tr = src.startswith('"""', start)
            q = 3 if tr else 1
            e = _string_end(src, start + q, tr, raw)
            if e == -1:
                return -1
            j = e + q
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return -1


def lex_string(src, i, line, col, file):
    # Lex "...", r"...", triple-quoted and raw triple-quoted strings starting at src[i].
    # Returns (token, i, line, col).
    # r-prefixed strings are raw: braces are literal (no interpolation); the escapes \\n \\" etc. still work and any
    # other backslash is kept, so regular expressions read naturally: r"\\d+".
    # Triple-quoted strings may span lines; a newline right after the opening quotes is dropped and the
    # common indentation of the lines is removed.
    start_line, start_col = line, col
    raw = src[i] == "r"
    if raw:
        i += 1; col += 1
    triple = src.startswith('"""', i)
    q = 3 if triple else 1
    i += q; col += q
    # find the end, honouring escapes and nested strings inside {interpolations}
    j = _string_end(src, i, triple, raw)
    if j == -1:
        raise KlarError("E102", "unterminated string literal (or an unclosed { inside it)", start_line, start_col,
                        'close the string on the same line, or use """...""" for text spanning lines; '
                        'write {{ for a literal {', file)
    body = src[i:j]
    end = j + q
    # position after the literal
    nl = src.count("\n", i - q - (1 if raw else 0), end)
    if nl:
        new_line = line + nl
        new_col = end - src.rfind("\n", 0, end)
    else:
        new_line = line
        new_col = col + (end - i)
    if triple:
        if body.startswith("\n"):
            body = body[1:]
            line += 1
            col = 1
        import textwrap
        body = textwrap.dedent(body)
        if body.endswith("\n") is False and "\n" in body:
            # a closing """ on its own line leaves trailing indentation; drop a whitespace-only last line
            last = body.rsplit("\n", 1)
            if last[1].strip() == "":
                body = last[0] + "\n"
    parts = []
    buf = []
    k = 0
    cl, cc = line, col
    while k < len(body):
        ch = body[k]
        if ch == "\\":
            nxt = body[k + 1] if k + 1 < len(body) else ""
            if nxt not in ESCAPES and raw and nxt:
                buf.append("\\"); k += 1; cc += 1        # raw strings keep other backslashes: r"\d+"
                continue
            if nxt not in ESCAPES:
                raise KlarError("E101", f"unknown escape sequence \\{nxt}", cl, cc,
                                'valid escapes: \\n \\t \\r \\" \\\\ \\0', file)
            buf.append(ESCAPES[nxt]); k += 2; cc += 2
            continue
        if ch == "\n":
            buf.append(ch); k += 1; cl += 1; cc = 1
            continue
        if raw or ch not in "{}":
            buf.append(ch); k += 1; cc += 1
            continue
        if ch == "{":
            if body.startswith("{{", k):
                buf.append("{"); k += 2; cc += 2
                continue
            m = _interp_end(body, k + 1, True)
            if m == -1:
                raise KlarError("E104", "unclosed { in string", cl, cc,
                                'close it with }, or write {{ for a literal { (a raw string r"..." has no interpolation)', file)
            if buf:
                parts.append("".join(buf)); buf = []
            parts.append((body[k + 1:m], cl, cc + 1))
            cc += m - k + 1
            k = m + 1
            continue
        # ch == "}"
        if body.startswith("}}", k):
            buf.append("}"); k += 2; cc += 2
            continue
        raise KlarError("E104", "single } in string", cl, cc, 'use }} for a literal }, or a raw string r"..."', file)
    if buf or not parts:
        parts.append("".join(buf))
    return Token("str", parts, start_line, start_col, end_line=new_line), end, new_line, new_col

def lex(src, file=None, line_offset=0, col_offset=0):
    toks = []
    i, line, col = 0, 1 + line_offset, 1 + col_offset
    n = len(src)

    def err(code, msg, hint=None):
        raise KlarError(code, msg, line, col, hint, file)

    while i < n:
        c = src[i]
        if c == "\n":
            i += 1; line += 1; col = 1
            continue
        if c in " \t\r":
            i += 1; col += 1
            continue
        if src.startswith("//", i):
            while i < n and src[i] != "\n":
                i += 1
            continue
        if src.startswith("/*", i):
            err("E101", "block comments are not supported", "use // line comments")
        start_col = col
        if c.isdigit():
            j = i
            while j < n and (src[j].isdigit() or src[j] == "_"):
                j += 1
            is_float = False
            if j + 1 < n and src[j] == "." and src[j + 1].isdigit():
                is_float = True
                j += 1
                while j < n and (src[j].isdigit() or src[j] == "_"):
                    j += 1
            if j < n and src[j] in "eE":
                k = j + 1
                if k < n and src[k] in "+-":
                    k += 1
                if k < n and src[k].isdigit():
                    is_float = True
                    j = k
                    while j < n and src[j].isdigit():
                        j += 1
            text = src[i:j].replace("_", "")
            toks.append(Token("float" if is_float else "int", float(text) if is_float else int(text), line, start_col))
            col += j - i; i = j
            continue
        if c == "r" and src.startswith('r"', i):
            tok, i, line, col = lex_string(src, i, line, col, file)
            toks.append(tok)
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            word = src[i:j]
            toks.append(Token("kw" if word in KEYWORDS else "ident", word, line, start_col))
            col += j - i; i = j
            continue
        if c == '"' or (c == "r" and src.startswith('r"', i)):
            tok, i, line, col = lex_string(src, i, line, col, file)
            toks.append(tok)
            continue
        if src[i:i + 2] in ("+=", "-=", "*=", "/=", "%=", "++", "--"):
            err("E101", f"`{src[i:i + 2]}` is not a Klar operator",
                "write the assignment out in full, e.g. x = x + 1 (only `var` bindings can be reassigned)")
        for s in SYMBOLS:
            if src.startswith(s, i):
                toks.append(Token("sym", s, line, start_col))
                i += len(s); col += len(s)
                break
        else:
            hint = None
            if c == "|" or c == "&":
                hint = "use the keywords `and`, `or`, `not` for boolean logic"
            elif c == "!":
                hint = "use `not x` for negation and `!=` for inequality"
            elif c == "'":
                hint = "strings use double quotes"
            err("E101", f"unexpected character {c!r}", hint)
    toks.append(Token("eof", None, line, col))
    return toks


# ---------------------------------------------------------------- parser

MAX_TUPLE = 6


def tuple_name(n):
    """Tuples are built-in generic structs: (int, str) is Tuple2[int, str] with fields named 0 and 1."""
    return f"Tuple{n}"


def is_tuple_name(name):
    return len(name) == 6 and name.startswith("Tuple") and name[5].isdigit() and 2 <= int(name[5]) <= MAX_TUPLE


def is_upper(name):
    return name[0].isupper()


class Parser:
    def __init__(self, src, file=None):
        self.file = file
        self.toks = lex(src, file)
        self.pos = 0

    # -- helpers
    @property
    def tok(self):
        return self.toks[self.pos]

    def prev(self):
        return self.toks[self.pos - 1]

    def at(self, kind, value=None):
        t = self.tok
        return t.kind == kind and (value is None or t.value == value)

    def at_sym(self, v):
        return self.at("sym", v)

    def at_kw(self, v):
        return self.at("kw", v)

    def advance(self):
        t = self.tok
        self.pos += 1
        return t

    def error(self, msg, hint=None, tok=None, code="E103"):
        t = tok or self.tok
        raise KlarError(code, msg, t.line, t.col, hint, self.file)

    def describe(self, t):
        if t.kind == "eof":
            return "end of file"
        if t.kind == "str":
            return "string literal"
        return f"`{t.value}`"

    def expect_sym(self, v, hint=None):
        if not self.at_sym(v):
            self.error(f"expected `{v}` but found {self.describe(self.tok)}", hint)
        return self.advance()

    def expect_kw(self, v, hint=None):
        if not self.at_kw(v):
            self.error(f"expected `{v}` but found {self.describe(self.tok)}", hint)
        return self.advance()

    def expect_ident(self, what="a name", upper=None):
        t = self.tok
        if t.kind != "ident":
            hint = None
            if t.kind == "kw":
                hint = f"`{t.value}` is a reserved keyword and cannot be used as {what}"
            self.error(f"expected {what} but found {self.describe(t)}", hint)
        if upper is True and not is_upper(t.value):
            self.error(f"{what} must start with an uppercase letter: `{t.value}`",
                       f"rename it to `{t.value[0].upper() + t.value[1:]}` (types, structs and variants are Capitalized)")
        if upper is False and is_upper(t.value):
            self.error(f"{what} must start with a lowercase letter: `{t.value}`",
                       f"rename it to `{t.value[0].lower() + t.value[1:]}` (values and functions are lowercase)")
        return self.advance()

    def skip_semis(self):
        while self.at_sym(";"):
            self.advance()

    # -- program
    def parse_program(self):
        decls = []
        while not self.at("eof"):
            self.skip_semis()
            if self.at("eof"):
                break
            t = self.tok
            if self.at_kw("fn"):
                decls.append(self.parse_fn_decl())
            elif self.at_kw("struct"):
                decls.append(self.parse_struct())
            elif self.at_kw("enum"):
                decls.append(self.parse_enum())
            elif self.at_kw("test"):
                decls.append(self.parse_test())
            elif self.at_kw("use"):
                decls.append(self.parse_use())
            elif self.at_kw("extern"):
                decls.append(self.parse_extern())
            elif self.at_kw("trait"):
                decls.append(self.parse_trait())
            elif self.at_kw("impl"):
                decls.append(self.parse_impl())
            else:
                self.error(f"expected a top-level declaration (use, fn, struct, enum, trait, impl, extern, test) but found {self.describe(t)}",
                           "statements must live inside a function; put program logic in `fn main() uses io { ... }`")
        return Node("Program", 1, 1, decls=decls)

    def parse_type_params(self, bounds=None):
        """`[A, B]`; where bounds are allowed (functions, impls), `[T: Show + Eq, U]` fills `bounds`."""
        params = []
        if self.at_sym("["):
            self.advance()
            while True:
                params.append(self.expect_ident("a type parameter", upper=True).value)
                if self.at_sym(":"):
                    if bounds is None:
                        self.error("only functions and impls can put trait bounds on type parameters")
                    self.advance()
                    while True:
                        bounds.setdefault(params[-1], []).append(self.expect_ident("a trait name", upper=True).value)
                        if self.at_sym("+"):
                            self.advance(); continue
                        break
                if self.at_sym(","):
                    self.advance(); continue
                break
            self.expect_sym("]")
        return params

    def parse_trait(self):
        t = self.expect_kw("trait")
        name = self.expect_ident("a trait name", upper=True).value
        self.expect_sym("{", "trait form: trait Show { fn show(self: Self) -> str }")
        methods = []
        self.skip_semis()
        while not self.at_sym("}"):
            if not self.at_kw("fn"):
                self.error(f"expected `fn` in trait {name} but found {self.describe(self.tok)}",
                           "a trait lists method signatures: fn name(self: Self, ...) -> T")
            methods.append(self.parse_fn_decl(has_body=False, what="trait methods"))
            self.skip_semis()
        self.expect_sym("}")
        return Node("Trait", t.line, t.col, name=name, methods=methods)

    def parse_impl(self):
        t = self.expect_kw("impl")
        bounds = {}
        tparams = self.parse_type_params(bounds)
        trait = self.expect_ident("a trait name", upper=True).value
        self.expect_kw("for", f"impl form: impl {trait} for Point {{ fn ... }}")
        ty = self.parse_type()
        self.expect_sym("{")
        methods = []
        self.skip_semis()
        while not self.at_sym("}"):
            if not self.at_kw("fn"):
                self.error(f"expected `fn` in impl {trait} but found {self.describe(self.tok)}")
            methods.append(self.parse_fn_decl())
            self.skip_semis()
        self.expect_sym("}")
        return Node("Impl", t.line, t.col, trait=trait, ty=ty, tparams=tparams, bounds=bounds, methods=methods)

    def parse_use(self):
        t = self.expect_kw("use")
        path = [self.expect_ident("a module name", upper=False).value]
        while self.at_sym("."):
            self.advance()
            path.append(self.expect_ident("a module name", upper=False).value)
        self.expect_sym("{", "import form: use std.json { Json, parse }  (list every name you use)")
        names = []
        while not self.at_sym("}"):
            nt = self.expect_ident("an imported name")
            names.append((nt.value, nt))
            if self.at_sym(","):
                self.advance()
            elif not self.at_sym("}"):
                self.error(f"expected `,` or `}}` in import list but found {self.describe(self.tok)}")
        self.expect_sym("}")
        if not names:
            self.error("empty import list", "name what you use: use geometry { Point, area }", t)
        return Node("Use", t.line, t.col, path=path, names=names)

    def parse_extern(self):
        t = self.expect_kw("extern")
        if self.at_kw("type"):
            self.advance()
            name = self.expect_ident("an extern type name", upper=True).value
            return Node("ExternType", t.line, t.col, name=name)
        if not self.at("str") or len(self.tok.value) != 1 or not isinstance(self.tok.value[0], str):
            self.error("expected the Python target as a plain string",
                       'extern form: extern "module.function" fn name(x: int) -> int uses io')
        target = self.advance().value[0]
        if "." not in target and not target.startswith("klar:"):
            self.error(f"extern target `{target}` must be `module.attribute`", 'for example: extern "math.hypot" fn hypot(x: float, y: float) -> float', t)
        d = self.parse_fn_decl(has_body=False)
        if d.tparams and not target.startswith("klar:"):
            self.error("extern functions cannot be generic", tok=t)
        d.kind = "Extern"
        d.target = target
        return d

    def parse_fn_decl(self, has_body=True, what="extern functions"):
        t = self.expect_kw("fn")
        name = self.expect_ident("a function name", upper=False).value
        bounds = {}
        tparams = self.parse_type_params(bounds)
        self.expect_sym("(")
        params = []
        while not self.at_sym(")"):
            pt = self.expect_ident("a parameter name", upper=False)
            self.expect_sym(":", "every function parameter needs a type, e.g. `x: int`")
            params.append((pt.value, self.parse_type()))
            if self.at_sym(","):
                self.advance()
            elif not self.at_sym(")"):
                self.error(f"expected `,` or `)` in parameter list but found {self.describe(self.tok)}")
        self.expect_sym(")")
        ret = Node("TName", t.line, t.col, name="unit", args=[])
        if self.at_sym("->"):
            self.advance()
            ret = self.parse_type()
        effects = []
        if self.at_kw("uses"):
            self.advance()
            while True:
                e = self.expect_ident("an effect name", upper=False)
                if e.value != "io":
                    self.error(f"unknown effect `{e.value}`", "the only effect is `io`", e)
                effects.append(e.value)
                if self.at_sym(","):
                    self.advance(); continue
                break
        if not has_body:
            if self.at_sym("{"):
                self.error(f"{what} have no body", tok=self.tok)
            return Node("Fn", t.line, t.col, name=name, tparams=tparams, params=params, ret=ret, effects=effects, body=None,
                        bounds=bounds)
        if not self.at_sym("{"):
            self.error(f"expected `{{` to start the body of `{name}` but found {self.describe(self.tok)}",
                       "function signature form: fn name(a: int) -> int uses io { ... }")
        body = self.parse_block()
        return Node("Fn", t.line, t.col, name=name, tparams=tparams, params=params, ret=ret, effects=effects, body=body,
                    bounds=bounds)

    def parse_struct(self):
        t = self.expect_kw("struct")
        name = self.expect_ident("a struct name", upper=True).value
        tparams = self.parse_type_params()
        self.expect_sym("{")
        fields = []
        while not self.at_sym("}"):
            f = self.expect_ident("a field name", upper=False).value
            self.expect_sym(":", "struct fields need a type, e.g. `x: int`")
            fields.append((f, self.parse_type()))
            if self.at_sym(","):
                self.advance()
            elif not self.at_sym("}"):
                self.error(f"expected `,` or `}}` but found {self.describe(self.tok)}", "separate fields with commas")
        self.expect_sym("}")
        return Node("Struct", t.line, t.col, name=name, fields=fields, tparams=tparams)

    def parse_enum(self):
        t = self.expect_kw("enum")
        name = self.expect_ident("an enum name", upper=True).value
        tparams = self.parse_type_params()
        self.expect_sym("{")
        variants = []
        while not self.at_sym("}"):
            vt = self.expect_ident("a variant name", upper=True)
            fields = []
            if self.at_sym("("):
                self.advance()
                while not self.at_sym(")"):
                    f = self.expect_ident("a field name", upper=False).value
                    self.expect_sym(":", "variant fields need a name and type, e.g. `Circle(radius: float)`")
                    fields.append((f, self.parse_type()))
                    if self.at_sym(","):
                        self.advance()
                self.expect_sym(")")
            variants.append(Node("Variant", vt.line, vt.col, name=vt.value, fields=fields))
            if self.at_sym(","):
                self.advance()
            elif not self.at_sym("}"):
                self.error(f"expected `,` or `}}` but found {self.describe(self.tok)}", "separate variants with commas")
        self.expect_sym("}")
        return Node("Enum", t.line, t.col, name=name, variants=variants, tparams=tparams)

    def parse_test(self):
        t = self.expect_kw("test")
        if not self.at("str") or len(self.tok.value) != 1 or not isinstance(self.tok.value[0], str):
            self.error("expected a plain string name after `test`", 'example: test "adds numbers" { assert add(1, 2) == 3 }')
        name = self.advance().value[0]
        body = self.parse_block()
        return Node("Test", t.line, t.col, name=name, body=body)

    # -- types
    def parse_type(self):
        t = self.tok
        if self.at_kw("fn"):
            self.advance()
            self.expect_sym("(")
            params = []
            while not self.at_sym(")"):
                params.append(self.parse_type())
                if self.at_sym(","):
                    self.advance()
            self.expect_sym(")")
            self.expect_sym("->", "function types always state their result: fn(int) -> int")
            ret = self.parse_type()
            io = False
            if self.at_kw("uses"):
                self.advance()
                e = self.expect_ident("an effect name", upper=False)
                if e.value != "io":
                    self.error(f"unknown effect `{e.value}`", "the only effect is `io`", e)
                io = True
            return Node("TFn", t.line, t.col, params=params, ret=ret, io=io)
        if self.at_sym("("):
            self.advance()
            inner = self.parse_type()
            if self.at_sym(","):
                items = [inner]
                while self.at_sym(","):
                    self.advance()
                    if self.at_sym(")"):
                        break
                    items.append(self.parse_type())
                self.expect_sym(")")
                self.check_arity(len(items), t)
                return Node("TName", t.line, t.col, name=tuple_name(len(items)), args=items)
            self.expect_sym(")")
            return inner
        name = self.expect_ident("a type").value
        args = []
        if self.at_sym("["):
            self.advance()
            while True:
                args.append(self.parse_type())
                if self.at_sym(","):
                    self.advance(); continue
                break
            self.expect_sym("]")
        if self.at_sym("<"):
            self.error("generic arguments use square brackets", f"write {name}[...] instead of {name}<...>")
        return Node("TName", t.line, t.col, name=name, args=args)

    # -- statements
    def parse_block(self):
        t = self.expect_sym("{")
        stmts = []
        while not self.at_sym("}"):
            if self.at("eof"):
                self.error("unexpected end of file: a `{` was never closed", f"add `}}` to close the block opened at line {t.line}")
            self.skip_semis()
            if self.at_sym("}"):
                break
            st = self.parse_stmt()
            if st.kind == "Destructure":
                stmts.extend(st.stmts)
            else:
                stmts.append(st)
            self.skip_semis()
        self.expect_sym("}")
        return Node("Block", t.line, t.col, stmts=stmts)

    def check_arity(self, n, tok):
        if n > MAX_TUPLE:
            self.error(f"a tuple has at most {MAX_TUPLE} values, this one has {n}", "define a struct with named fields instead",
                       tok=tok)

    def parse_names_tuple(self, what):
        """`(a, b, _)` in `let (a, b) = ...` and `for (k, v) in ...`: the names, in order."""
        t = self.expect_sym("(")
        names = []
        while not self.at_sym(")"):
            names.append(self.expect_ident(f"a name to bind in {what}", upper=False))
            if self.at_sym(","):
                self.advance()
            elif not self.at_sym(")"):
                self.error(f"expected `,` or `)` but found {self.describe(self.tok)}",
                           f"{what} takes plain names: let (a, b) = pair")
        self.expect_sym(")")
        if len(names) < 2:
            self.error(f"{what} needs at least two names", "for one value write `let a = ...`", tok=t)
        self.check_arity(len(names), t)
        return names

    def tuple_assign_ahead(self):
        """Is this `(name, name, ...) =`?"""
        i = self.pos + 1
        while True:
            if self.toks[i].kind != "ident":
                return False
            i += 1
            tk = self.toks[i]
            if tk.kind == "sym" and tk.value == ",":
                i += 1
                continue
            if tk.kind == "sym" and tk.value == ")":
                nx = self.toks[i + 1]
                return nx.kind == "sym" and nx.value == "="
            return False

    def destructure(self, names, tmp, at, mutable=False):
        """`let (a, b) = tmp` as plain lets of tmp.0, tmp.1 (a `_` skips that value)."""
        out = []
        for i, nt in enumerate(names):
            if nt.value == "_":
                continue
            field = Node("Field", nt.line, nt.col, target=Node("Name", nt.line, nt.col, name=tmp), name=str(i))
            out.append(Node("Let", nt.line, nt.col, name=nt.value, mutable=mutable, ann=None, value=field, from_tuple=True))
        return out

    def parse_stmt(self):
        t = self.tok
        if self.at_kw("let") or self.at_kw("var"):
            mutable = self.advance().value == "var"
            if self.at_sym("("):
                names = self.parse_names_tuple("`let (...)`")
                ann = None
                if self.at_sym(":"):
                    self.advance()
                    ann = self.parse_type()
                self.expect_sym("=", "destructuring form: let (a, b) = pair")
                value = self.parse_expr()
                tmp = f"tuple__{t.line}_{t.col}"
                first = Node("Let", t.line, t.col, name=tmp, mutable=False, ann=ann, value=value, unpack=len(names))
                return Node("Destructure", t.line, t.col, stmts=[first] + self.destructure(names, tmp, t, mutable))
            name = self.expect_ident("a variable name", upper=False).value
            ann = None
            if self.at_sym(":"):
                self.advance()
                ann = self.parse_type()
            self.expect_sym("=", "bindings must be initialised: let x = 1")
            value = self.parse_expr()
            return Node("Let", t.line, t.col, name=name, mutable=mutable, ann=ann, value=value)
        if self.at_kw("if"):
            return self.parse_if_stmt()
        if self.at_kw("while"):
            self.advance()
            cond = self.parse_expr()
            body = self.parse_block()
            return Node("While", t.line, t.col, cond=cond, body=body)
        if self.at_kw("for"):
            self.advance()
            names = None
            if self.at_sym("("):
                names = self.parse_names_tuple("`for (...)`")
                var = f"tuple__{t.line}_{t.col}"
            else:
                var = self.expect_ident("a loop variable", upper=False).value
            self.expect_kw("in", "loop form: for x in xs { ... } or for i in range(0, n) { ... }")
            it = self.parse_expr()
            body = self.parse_block()
            if names:
                body.stmts = self.destructure(names, var, t) + body.stmts
            return Node("For", t.line, t.col, var=var, iter=it, body=body, unpack=len(names) if names else None)
        if self.at_kw("return"):
            self.advance()
            value = None
            if not self.at_sym("}") and not self.at_sym(";") and not self.at_sym(",") and self.tok.line == t.line:
                value = self.parse_expr()
            return Node("Return", t.line, t.col, value=value)
        if self.at_kw("break") or self.at_kw("continue"):
            self.advance()
            return Node("Break" if t.value == "break" else "Continue", t.line, t.col)
        if self.at_kw("assert"):
            self.advance()
            cond = self.parse_expr()
            msg = None
            if self.at_sym(","):
                self.advance()
                msg = self.parse_expr()
            return Node("Assert", t.line, t.col, cond=cond, msg=msg)
        if self.at_sym("(") and self.tuple_assign_ahead():
            # `(a, b) = expr` assigns existing vars from a tuple: a hidden let, then plain assignments
            names = self.parse_names_tuple("`(a, b) = ...`")
            self.advance()
            value = self.parse_expr()
            tmp = f"tuple__{t.line}_{t.col}"
            first = Node("Let", t.line, t.col, name=tmp, mutable=False, ann=None, value=value, unpack=len(names))
            stmts = [first]
            for i, nt in enumerate(names):
                if nt.value != "_":
                    field = Node("Field", nt.line, nt.col, target=Node("Name", nt.line, nt.col, name=tmp), name=str(i))
                    stmts.append(Node("Assign", nt.line, nt.col, name=nt.value, value=field))
            return Node("Destructure", t.line, t.col, stmts=stmts)
        if self.at("ident") and self.toks[self.pos + 1].kind == "sym" and self.toks[self.pos + 1].value == "=":
            name = self.advance().value
            self.advance()
            value = self.parse_expr()
            return Node("Assign", t.line, t.col, name=name, value=value)
        e = self.parse_expr()
        if self.at_sym("="):
            self.error("only plain variables can be assigned",
                       "values are immutable; build a new value instead, e.g. p = p with (x: 1) or xs = set_at(xs, i, v)")
        return Node("ExprStmt", t.line, t.col, expr=e)

    def parse_if_stmt(self):
        t = self.expect_kw("if")
        cond = self.parse_expr()
        if self.at_kw("then"):
            # expression-form if used as statement
            self.advance()
            a = self.parse_expr()
            self.expect_kw("else", "an `if ... then` expression always needs `else`")
            b = self.parse_expr()
            return Node("ExprStmt", t.line, t.col, expr=Node("IfExpr", t.line, t.col, cond=cond, then=a, els=b))
        if not self.at_sym("{"):
            self.error(f"expected `{{` after if-condition but found {self.describe(self.tok)}",
                       "statement form: if c { ... } else { ... }; expression form: if c then a else b")
        then = self.parse_block()
        els = None
        if self.at_kw("else"):
            self.advance()
            if self.at_kw("if"):
                inner = self.parse_if_stmt()
                els = Node("Block", inner.line, inner.col, stmts=[inner])
            else:
                els = self.parse_block()
        return Node("If", t.line, t.col, cond=cond, then=then, els=els)

    # -- expressions
    def parse_expr(self):
        return self.parse_or()

    def parse_or(self):
        left = self.parse_and()
        while self.at_kw("or"):
            t = self.advance()
            left = Node("Binary", t.line, t.col, op="or", left=left, right=self.parse_and())
        return left

    def parse_and(self):
        left = self.parse_not()
        while self.at_kw("and"):
            t = self.advance()
            left = Node("Binary", t.line, t.col, op="and", left=left, right=self.parse_not())
        return left

    def parse_not(self):
        if self.at_kw("not"):
            t = self.advance()
            return Node("Unary", t.line, t.col, op="not", operand=self.parse_not())
        return self.parse_cmp()

    def parse_cmp(self):
        left = self.parse_add()
        if self.tok.kind == "sym" and self.tok.value in ("==", "!=", "<", "<=", ">", ">="):
            t = self.advance()
            right = self.parse_add()
            left = Node("Binary", t.line, t.col, op=t.value, left=left, right=right)
            if self.tok.kind == "sym" and self.tok.value in ("==", "!=", "<", "<=", ">", ">="):
                self.error("comparisons cannot be chained", "write `a < b and b < c` instead of `a < b < c`")
        return left

    def parse_add(self):
        left = self.parse_mul()
        while self.tok.kind == "sym" and self.tok.value in ("+", "-"):
            t = self.advance()
            left = Node("Binary", t.line, t.col, op=t.value, left=left, right=self.parse_mul())
        return left

    def parse_mul(self):
        left = self.parse_unary()
        while self.tok.kind == "sym" and self.tok.value in ("*", "/", "%"):
            t = self.advance()
            left = Node("Binary", t.line, t.col, op=t.value, left=left, right=self.parse_unary())
        return left

    def parse_unary(self):
        if self.at_sym("-"):
            t = self.advance()
            return Node("Unary", t.line, t.col, op="-", operand=self.parse_unary())
        return self.parse_postfix()

    def parse_postfix(self):
        e = self.parse_primary()
        while True:
            t = self.tok
            same_line = t.line == self.prev().end_line
            if self.at_sym("("):
                if not same_line and self.tuple_assign_ahead():
                    break                     # the next statement is `(a, b) = ...`
                if not same_line:
                    self.error("`(` at the start of a line cannot continue the previous expression",
                               "put the `(` on the same line as what it applies to; Klar does not allow a statement to begin with `(`",
                               code="E105")
                args, named = self.parse_args()
                e = Node("Call", e.line, e.col, callee=e, args=args, named=named)
            elif self.at_sym("["):
                if not same_line:
                    self.error("`[` at the start of a line cannot continue the previous expression",
                               "put the `[` on the same line as what it applies to; Klar does not allow a statement to begin with `[`",
                               code="E105")
                self.advance()
                idx = self.parse_expr()
                self.expect_sym("]")
                e = Node("Index", t.line, t.col, target=e, index=idx)
            elif self.at_sym("."):
                self.advance()
                if self.at("int"):
                    n = self.advance()
                    e = Node("Field", n.line, n.col, target=e, name=str(n.value))
                    continue
                if self.at("float"):        # t.0.1 lexes as t . 0.1
                    n = self.advance()
                    raw = repr(n.value)
                    if not all(part.isdigit() for part in raw.split(".")):
                        self.error("expected a tuple position like .0 or .1", tok=n)
                    for part in raw.split("."):
                        e = Node("Field", n.line, n.col, target=e, name=part)
                    continue
                name = self.expect_ident("a field or function name after `.`").value
                if self.at_sym("(") and self.tok.line == self.prev().line:
                    args, named = self.parse_args()
                    if named:
                        self.error("method-style calls take positional arguments only", tok=t)
                    e = Node("MethodCall", t.line, t.col, target=e, name=name, args=args)
                else:
                    e = Node("Field", t.line, t.col, target=e, name=name)
            elif self.at_sym("?"):
                self.advance()
                e = Node("Try", t.line, t.col, value=e)
            elif self.at_kw("with"):
                self.advance()
                self.expect_sym("(", "update form: p with (x: 1, y: 2)")
                updates = []
                while not self.at_sym(")"):
                    f = self.expect_ident("a field name", upper=False)
                    self.expect_sym(":")
                    updates.append((f.value, self.parse_expr(), f))
                    if self.at_sym(","):
                        self.advance()
                self.expect_sym(")")
                e = Node("With", t.line, t.col, target=e, updates=updates)
            else:
                break
        return e

    def parse_args(self):
        self.expect_sym("(")
        args, named = [], []
        while not self.at_sym(")"):
            if self.at("ident") and self.toks[self.pos + 1].kind == "sym" and self.toks[self.pos + 1].value == ":":
                nt = self.advance()
                self.advance()
                named.append((nt.value, self.parse_expr(), nt))
            else:
                if named:
                    self.error("positional argument after named argument", "use all-positional or all-named arguments")
                args.append(self.parse_expr())
            if self.at_sym(","):
                self.advance()
            elif not self.at_sym(")"):
                self.error(f"expected `,` or `)` in argument list but found {self.describe(self.tok)}")
        self.expect_sym(")")
        if args and named:
            self.error("cannot mix positional and named arguments", "use all-positional or all-named arguments")
        return args, named

    def parse_primary(self):
        t = self.tok
        if t.kind == "int":
            self.advance(); return Node("Int", t.line, t.col, value=t.value)
        if t.kind == "float":
            self.advance(); return Node("Float", t.line, t.col, value=t.value)
        if t.kind == "str":
            self.advance()
            return self.build_string(t)
        if self.at_kw("true") or self.at_kw("false"):
            self.advance(); return Node("Bool", t.line, t.col, value=t.value == "true")
        if t.kind == "ident":
            self.advance(); return Node("Name", t.line, t.col, name=t.value)
        if self.at_sym("("):
            self.advance()
            if self.at_sym(")"):
                self.advance()
                return Node("Unit", t.line, t.col)
            e = self.parse_expr()
            if self.at_sym(","):
                items = [e]
                while self.at_sym(","):
                    self.advance()
                    if self.at_sym(")"):
                        break
                    items.append(self.parse_expr())
                self.expect_sym(")")
                self.check_arity(len(items), t)
                return Node("Call", t.line, t.col, callee=Node("Name", t.line, t.col, name=tuple_name(len(items))),
                            args=items, named=[], tuple_lit=True)
            self.expect_sym(")")
            return e
        if self.at_sym("["):
            return self.parse_list_or_map()
        if self.at_kw("if"):
            self.advance()
            cond = self.parse_expr()
            self.expect_kw("then", "an if-expression has the form: if cond then a else b")
            a = self.parse_expr()
            self.expect_kw("else", "an if-expression always needs `else`")
            b = self.parse_expr()
            return Node("IfExpr", t.line, t.col, cond=cond, then=a, els=b)
        if self.at_kw("match"):
            return self.parse_match()
        if self.at_kw("fn"):
            return self.parse_lambda()
        if self.at_sym("{"):
            self.error("unexpected `{` in expression position",
                       "maps are written [\"k\": v], structs as Point(x: 1, y: 2), blocks only follow if/while/for/fn/=>")
        self.error(f"expected an expression but found {self.describe(t)}")

    def build_string(self, t):
        parts = []
        for p in t.value:
            if isinstance(p, str):
                parts.append(p)
            else:
                text, line, col = p
                if not text.strip():
                    raise KlarError("E104", "empty {} in string", line, col, "use {{}} for literal braces", self.file)
                sub = Parser.__new__(Parser)
                sub.file = self.file
                sub.toks = lex(text, self.file, line - 1, col - 1)
                sub.pos = 0
                try:
                    e = sub.parse_expr()
                except KlarError as ex:
                    raise KlarError("E104", f"could not parse interpolation {{{text}}}: {ex.message}", line, col,
                                    "only a single expression is allowed inside {}; use {{ and }} for literal braces", self.file)
                if not sub.at("eof"):
                    raise KlarError("E104", f"could not parse interpolation {{{text}}}", line, col,
                                    "only a single expression is allowed inside {}", self.file)
                parts.append(e)
        if all(isinstance(p, str) for p in parts):
            return Node("Str", t.line, t.col, value="".join(parts))
        return Node("Interp", t.line, t.col, parts=parts)

    def parse_list_or_map(self):
        t = self.expect_sym("[")
        if self.at_sym(":"):
            self.advance()
            self.expect_sym("]", "the empty map is written [:]")
            return Node("MapLit", t.line, t.col, entries=[])
        if self.at_sym("]"):
            self.advance()
            return Node("ListLit", t.line, t.col, items=[])
        first = self.parse_expr()
        if self.at_sym(":"):
            self.advance()
            entries = [(first, self.parse_expr())]
            while self.at_sym(","):
                self.advance()
                if self.at_sym("]"):
                    break
                k = self.parse_expr()
                self.expect_sym(":", "every map entry needs `key: value`")
                entries.append((k, self.parse_expr()))
            self.expect_sym("]")
            return Node("MapLit", t.line, t.col, entries=entries)
        items = [first]
        while self.at_sym(","):
            self.advance()
            if self.at_sym("]"):
                break
            items.append(self.parse_expr())
        self.expect_sym("]", "separate list items with commas")
        return Node("ListLit", t.line, t.col, items=items)

    def parse_match(self):
        t = self.expect_kw("match")
        scrut = self.parse_expr()
        self.expect_sym("{", "match form: match value { Pattern => result, ... }")
        arms = []
        while not self.at_sym("}"):
            if self.at("eof"):
                self.error("unexpected end of file inside match")
            pt = self.tok
            pat = self.parse_pattern()
            self.expect_sym("=>", "each match arm is `Pattern => expression` or `Pattern => { statements }`")
            if self.at_sym("{"):
                body = self.parse_block()
            elif self.at_kw("return") or self.at_kw("break") or self.at_kw("continue") or (
                    self.at("ident") and self.toks[self.pos + 1].kind == "sym" and self.toks[self.pos + 1].value == "="):
                # `Pattern => return x`, `=> break`, `=> total = total + 1` are statements: treat as a one-line block
                st = self.tok
                one = self.parse_stmt()
                body = Node("Block", st.line, st.col, stmts=one.stmts if one.kind == "Destructure" else [one])
            else:
                body = self.parse_expr()
            arms.append(Node("Arm", pt.line, pt.col, pattern=pat, body=body))
            if self.at_sym(","):
                self.advance()
        self.expect_sym("}")
        return Node("Match", t.line, t.col, scrutinee=scrut, arms=arms)

    def parse_pattern(self):
        t = self.tok
        if t.kind == "ident" and t.value == "_":
            self.advance()
            return Node("PWild", t.line, t.col)
        if t.kind in ("int", "float"):
            self.advance()
            return Node("PLit", t.line, t.col, value=t.value)
        if self.at_sym("-") and self.toks[self.pos + 1].kind in ("int", "float"):
            self.advance()
            v = self.advance().value
            return Node("PLit", t.line, t.col, value=-v)
        if t.kind == "str":
            if len(t.value) != 1 or not isinstance(t.value[0], str):
                self.error("string patterns cannot contain interpolation")
            self.advance()
            return Node("PLit", t.line, t.col, value=t.value[0])
        if self.at_kw("true") or self.at_kw("false"):
            self.advance()
            return Node("PLit", t.line, t.col, value=t.value == "true")
        if self.at_sym("("):
            self.advance()
            subs = [self.parse_pattern()]
            while self.at_sym(","):
                self.advance()
                if self.at_sym(")"):
                    break
                subs.append(self.parse_pattern())
            self.expect_sym(")")
            if len(subs) < 2:
                self.error("a tuple pattern needs at least two parts: (a, b)", tok=t)
            self.check_arity(len(subs), t)
            return Node("PTuple", t.line, t.col, name=tuple_name(len(subs)), subs=subs)
        if t.kind == "ident":
            self.advance()
            if is_upper(t.value):
                subs = []
                if self.at_sym("("):
                    self.advance()
                    while not self.at_sym(")"):
                        subs.append(self.parse_pattern())
                        if self.at_sym(","):
                            self.advance()
                    self.expect_sym(")")
                return Node("PVariant", t.line, t.col, name=t.value, subs=subs)
            return Node("PBind", t.line, t.col, name=t.value)
        self.error(f"expected a pattern but found {self.describe(t)}",
                   "patterns: _, literals, a lowercase name to bind, Variant(p1, p2) or a tuple (p1, p2)")

    def parse_lambda(self):
        t = self.expect_kw("fn")
        if self.at("ident"):
            self.error("named functions must be declared at the top level",
                       "inside a function use an anonymous function: let f = fn(x: int) => x + 1")
        self.expect_sym("(")
        params = []
        while not self.at_sym(")"):
            p = self.expect_ident("a parameter name", upper=False).value
            ann = None
            if self.at_sym(":"):
                self.advance()
                ann = self.parse_type()
            params.append((p, ann))
            if self.at_sym(","):
                self.advance()
        self.expect_sym(")")
        ret = None
        if self.at_sym("->"):
            self.advance()
            ret = self.parse_type()
        if self.at_sym("=>"):
            self.advance()
            if not self.at_sym("{"):   # `fn(x) => { ... }` is the block form, as in JavaScript
                body = self.parse_expr()
                return Node("Lambda", t.line, t.col, params=params, ret=ret, body=body, block=False)
        if self.at_sym("{"):
            body = self.parse_block()
            return Node("Lambda", t.line, t.col, params=params, ret=ret, body=body, block=True)
        self.error("expected `=> expr` or `{ ... }` for the anonymous function body")


def parse(src, file=None):
    return Parser(src, file).parse_program()
