"""`klar fmt`: one canonical layout, so agents' diffs only show real changes.

The formatter keeps your line breaks and comments and decides everything else: two-space indentation from
bracket depth, one space around operators and after commas and colons, none inside brackets or before
`(` / `[` of a call, index or type, at most one blank line, none just inside a block, one newline at the
end of the file. The result is checked to lex to exactly the same tokens as the input, so formatting can
never change what a program means.

    klar fmt file.klar dir/      rewrite files in place (directories: every .klar file inside)
    klar fmt --check ...         change nothing; list files that are not formatted and exit 1
"""

import os
import sys

from .errors import KlarError
from .syntax import KEYWORDS, SYMBOLS, lex, lex_string

OPEN, CLOSE = "([{", ")]}"
CONT_START = {"+", "-", "*", "/", "%", "==", "!=", "<", ">", "<=", ">=", "=>", "->", ".", "?"}
CONT_END = {"+", "-", "*", "/", "%", "==", "!=", "<", ">", "<=", ">=", "=>", "->", "=", "."}
# keywords after which `-` is a sign and `(` gets a space
KW_OPERAND_FOLLOWS = KEYWORDS - {"true", "false"}


def scan(src):
    """Raw tokens: (kind, text) with kinds word, num, str, sym, comment, nl. Strings keep their source text."""
    out = []
    i, n, line, col = 0, len(src), 1, 1
    while i < n:
        c = src[i]
        if c == "\n":
            out.append(("nl", "\n")); i += 1; line += 1; col = 1
            continue
        if c in " \t\r":
            i += 1; col += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j == -1 else j
            k = i
            while k > 0 and src[k - 1] in " \t":
                k -= 1
            gap = max(i - k, 1)                           # a trailing comment keeps its distance (alignment)
            out.append(("comment", src[i:j].rstrip(), gap))
            col += j - i; i = j
            continue
        if c == '"' or src.startswith('r"', i):
            _, j, line2, col2 = lex_string(src, i, line, col, None)
            out.append(("str", src[i:j]))
            i, line, col = j, line2, col2
            continue
        if c.isdigit():
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_."):
                if src[j] == "." and not (j + 1 < n and src[j + 1].isdigit()):
                    break
                if src[j] in "eE" and j + 1 < n and src[j + 1] in "+-":
                    j += 1
                j += 1
            out.append(("num", src[i:j]))
            col += j - i; i = j
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            out.append(("word", src[i:j]))
            col += j - i; i = j
            continue
        for s in SYMBOLS:
            if src.startswith(s, i):
                out.append(("sym", s))
                i += len(s); col += len(s)
                break
        else:
            raise KlarError("E101", f"unexpected character {c!r}", line, col)
    return out


def _is_operand_end(tok):
    kind, text = tok
    if kind in ("num", "str"):
        return True
    if kind == "word":
        return text not in KW_OPERAND_FOLLOWS
    return text in (")", "]", "}", "?")


def _space(prev, prevprev, tok):
    """Whether one space goes between prev and tok on the same line."""
    pk, pt = prev
    k, t = tok
    if k == "comment":
        return True                                       # the gap is added by the caller
    if t in (",", ")", "]", ".", "?", ";") and k == "sym":
        return False
    if t == ":" and k == "sym":
        return False
    if pk == "sym" and pt in ("(", "[", "."):
        return False
    if pk == "sym" and pt == ":" and t == "]":            # [:]
        return False
    if pk == "sym" and pt == "{" and t == "}":            # {}
        return False
    if t in ("(", "[") and k == "sym":
        if pk == "word" and pt not in KW_OPERAND_FOLLOWS:
            return False                                  # call, index, generic type
        if pk == "word" and pt in ("fn", "impl"):
            return False                                  # fn(x) => ..., fn(int) -> str, impl[T: Show]
        if pk == "sym" and pt in (")", "]", "?"):
            return False                                  # f(a)(b), xs[0][1]
        if pk == "str":
            return False
        return True
    if pk == "sym" and pt == "-" and (prevprev is None or not _is_operand_end(prevprev)):
        return False                                      # unary minus: -x, f(-1), return -n
    return True


def format_source(src):
    toks = scan(src)
    lines, cur = [], []
    for tok in toks:
        if tok[0] == "nl":
            lines.append(cur); cur = []
        else:
            cur.append(tok)
    lines.append(cur)

    # Indentation: a line inside brackets is indented one step more than the line that opened the innermost
    # bracket; a line starting with closing brackets lines up with the line that opened them.
    out = []
    stack = []                                            # indent of the line each open bracket is on
    prev_last = None                                      # last code token of the previous line
    for line in lines:
        if not line:
            out.append(None)                              # a blank line, settled below
            continue
        lead = 0
        for tok in line:
            if tok[0] == "sym" and tok[1] in CLOSE:
                lead += 1
            else:
                break
        if lead and len(stack) >= lead:
            indent = stack[-lead]
        else:
            indent = stack[-1] + 1 if stack else 0
            first = line[0]
            if (first[0] == "sym" and first[1] in CONT_START) or (first[0] == "word" and first[1] in ("and", "or", "then", "else")) \
                    or (prev_last is not None and prev_last[0] == "sym" and prev_last[1] in CONT_END) \
                    or (prev_last is not None and prev_last[0] == "word" and prev_last[1] in ("and", "or", "not", "then", "else")):
                indent += 1                               # the expression continues from the line above
        code = [t for t in line if t[0] != "comment"]
        prev_last_next = code[-1] if code else prev_last
        parts = []
        prev = prevprev = None
        base = indent        # indent for brackets opened on this line; drops back once it closes an outer one
        for tok in line:
            if prev is not None and tok[0] == "comment":
                parts.append(" " * tok[2])
            elif prev is not None and _space(prev, prevprev, tok):
                parts.append(" ")
            parts.append(tok[1])
            prevprev, prev = prev, tok
            if tok[0] == "sym":
                if tok[1] in OPEN:
                    stack.append(base)
                elif tok[1] in CLOSE and stack:
                    base = min(base, stack.pop())
        prev_last = prev_last_next
        text = "".join(parts)
        if "\n" in text:                                  # a multi-line string: its inside is left alone
            first, rest = text.split("\n", 1)
            out.append("  " * indent + first + "\n" + rest)
        else:
            out.append("  " * indent + text)

    # blank lines: at most one in a row, none at the start or end, none just inside { }
    result = []
    for i, l in enumerate(out):
        if l is None:
            if not result or result[-1] is None or result[-1].rstrip().endswith(("{", "(", "[")):
                continue
            nxt = next((x for x in out[i + 1:] if x is not None), None)
            if nxt is None or nxt.lstrip().startswith(("}", ")", "]")):
                continue
        result.append(l)
    while result and result[-1] is None:
        result.pop()
    text = "\n".join("" if l is None else l for l in result) + "\n"

    def strip_pos(v):
        if isinstance(v, (list, tuple)):
            return [strip_pos(x) for x in v if not isinstance(x, int)]
        return v

    def sig(s):
        return [(t.kind, strip_pos(t.value)) for t in lex(s)]
    if sig(text) != sig(src):
        raise KlarError("E101", "klar fmt would change the meaning of this file (a formatter bug); it was left as is", 1, 1)
    return text


def klar_files(paths):
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__"))
                for f in sorted(files):
                    if f.endswith(".klar"):
                        yield os.path.join(root, f)
        else:
            yield p


def cmd_fmt(paths, check):
    if not paths:
        print("usage: klar fmt FILE_OR_DIR... [--check]", file=sys.stderr)
        return 2
    bad, changed, failed = [], [], 0
    for path in klar_files(paths):
        try:
            with open(path, encoding="utf-8") as f:
                src = f.read()
            new = format_source(src)
        except OSError as ex:
            print(f"klar fmt: cannot read {path}: {ex}", file=sys.stderr)
            failed += 1
            continue
        except KlarError as ex:
            ex.file = path
            print(ex.render(src), file=sys.stderr)
            failed += 1
            continue
        if new != src:
            if check:
                bad.append(path)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(new)
                changed.append(path)
    if check:
        for p in bad:
            print(p)
        return 1 if bad or failed else 0
    for p in changed:
        print(f"formatted {p}", file=sys.stderr)
    return 1 if failed else 0
