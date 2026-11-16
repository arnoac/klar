"""Hidden acceptance tests for the sheet task. `model()` is an independent Python solution used for the
generated tests; the hand-written tests state their expected output directly."""

import random
import re

from checks import Fail, expect_eq

TIMEOUT = 60

CELL = re.compile(r"[A-Z](?:[1-9][0-9]?)$")


class ParseError(Exception):
    pass


def tokenize(s):
    toks, i = [], 0
    while i < len(s):
        c = s[i]
        if c == " ":
            i += 1
        elif c.isdigit():
            j = i
            while j < len(s) and s[j].isdigit():
                j += 1
            toks.append(("int", int(s[i:j])))
            i = j
        elif "A" <= c <= "Z":
            j = i
            while j < len(s) and ("A" <= s[j] <= "Z" or s[j].isdigit()):
                j += 1
            word = s[i:j]
            if word in ("SUM", "MIN", "MAX"):
                toks.append(("fn", word))
            elif CELL.match(word):
                toks.append(("cell", word))
            else:
                raise ParseError(word)
            i = j
        elif c in "+-*/():":
            toks.append((c, c))
            i += 1
        else:
            raise ParseError(c)
    return toks


def parse(src):
    toks = tokenize(src)
    pos = [0]

    def peek():
        return toks[pos[0]][0] if pos[0] < len(toks) else None

    def take(kind):
        if peek() != kind:
            raise ParseError(kind)
        pos[0] += 1
        return toks[pos[0] - 1][1]

    def expr():
        node = term()
        while peek() in ("+", "-"):
            op = take(peek())
            node = ("bin", op, node, term())
        return node

    def term():
        node = unary()
        while peek() in ("*", "/"):
            op = take(peek())
            node = ("bin", op, node, unary())
        return node

    def unary():
        if peek() == "-":
            take("-")
            return ("neg", unary())
        return primary()

    def primary():
        k = peek()
        if k == "int":
            return ("int", take("int"))
        if k == "cell":
            return ("ref", take("cell"))
        if k == "fn":
            f = take("fn")
            take("(")
            a = take("cell")
            take(":")
            b = take("cell")
            take(")")
            return ("range", f, a, b)
        take("(")
        e = expr()
        take(")")
        return e

    node = expr()
    if pos[0] != len(toks):
        raise ParseError("trailing")
    return node


def range_cells(a, b):
    c1, c2 = sorted([a[0], b[0]])
    r1, r2 = sorted([int(a[1:]), int(b[1:])])
    return [f"{chr(c)}{r}" for c in range(ord(c1), ord(c2) + 1) for r in range(r1, r2 + 1)]


def refs(node):
    kind = node[0]
    if kind == "ref":
        return [node[1]]
    if kind == "range":
        return range_cells(node[2], node[3])
    if kind == "bin":
        return refs(node[2]) + refs(node[3])
    if kind == "neg":
        return refs(node[1])
    return []


def model(text):
    cells = {}
    for line in text.split("\n"):
        if line.strip() == "":
            continue
        name, content = line.split(":", 1)
        cells[name.strip()] = content.strip()
    parsed = {}
    for name, content in cells.items():
        if content.startswith("="):
            try:
                parsed[name] = parse(content[1:])
            except ParseError:
                parsed[name] = "#PARSE"
        elif re.fullmatch(r"-?[0-9]+", content):
            parsed[name] = ("int", int(content))
        else:
            parsed[name] = "#PARSE"
    deps = {n: (refs(p) if isinstance(p, tuple) else []) for n, p in parsed.items()}

    def reaches_self(start):
        seen, stack = set(), list(deps[start])
        while stack:
            n = stack.pop()
            if n == start:
                return True
            if n in seen or n not in deps:
                continue
            seen.add(n)
            stack.extend(deps[n])
        return False

    values = {n: "#CYCLE" for n in parsed if reaches_self(n)}

    def value(name):
        if name not in parsed:
            return 0
        if name not in values:
            p = parsed[name]
            values[name] = p if isinstance(p, str) else ev(p)
        return values[name]

    def ev(node):
        kind = node[0]
        if kind == "int":
            return node[1]
        if kind == "ref":
            return value(node[1])
        if kind == "neg":
            v = ev(node[1])
            return v if isinstance(v, str) else -v
        if kind == "range":
            vals = []
            for c in range_cells(node[2], node[3]):
                v = value(c)
                if isinstance(v, str):
                    return v
                vals.append(v)
            return {"SUM": sum, "MIN": min, "MAX": max}[node[1]](vals)
        a = ev(node[2])
        if isinstance(a, str):
            return a
        b = ev(node[3])
        if isinstance(b, str):
            return b
        op = node[1]
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if b == 0:
            return "#DIV0"
        q = abs(a) // abs(b)
        return q if (a >= 0) == (b >= 0) else -q

    order = sorted(parsed, key=lambda n: (n[0], int(n[1:])))
    return [f"{n} = {value(n)}" for n in order]


def check(prog, text, want=None):
    if want is None:
        want = model(text)
    r = prog.run(text)
    got = [l for l in r.out.replace("\r\n", "\n").split("\n") if l != ""]
    if got != want:
        for i, (g, w) in enumerate(zip(got, want)):
            if g != w:
                raise Fail(f"line {i + 1} of output: expected {w!r}, got {g!r}")
        raise Fail(f"expected {len(want)} output lines, got {len(got)}: {r.out[:300]!r}")
    expect_eq(r.code, 0, "exit status")


EXAMPLE = """A1: 10
A2: =A1 * 2 + B5
B1: =SUM(A1:A3)
B2: =A1 / (A3 - 3)
A3: 3
C1: =C2 + 1
C2: =C1
C3: =C1 + 1
D1: = MAX(A1:B1)
D2: =A1 +
"""


def test_example_from_task(prog):
    check(prog, EXAMPLE, ["A1 = 10", "A2 = 20", "A3 = 3", "B1 = 33", "B2 = #DIV0", "C1 = #CYCLE", "C2 = #CYCLE",
                          "C3 = #CYCLE", "D1 = 33", "D2 = #PARSE"])


def test_empty_input(prog):
    check(prog, "", [])


def test_sort_order_rows_numeric(prog):
    check(prog, "B1: 1\nA10: 2\nA2: 3\nZ99: 4\nA1: 5\n", ["A1 = 5", "A2 = 3", "A10 = 2", "B1 = 1", "Z99 = 4"])


def test_last_definition_wins(prog):
    check(prog, "A1: 1\nB1: =A1\nA1: 2\n", ["A1 = 2", "B1 = 2"])


def test_integer_literals(prog):
    check(prog, "A1: 007\nA2: -7\nA3: 1.5\nA4: abc\nA5: +3\nA6:\nA7: - 4\nA8:   12  \n",
          ["A1 = 7", "A2 = -7", "A3 = #PARSE", "A4 = #PARSE", "A5 = #PARSE", "A6 = #PARSE", "A7 = #PARSE",
           "A8 = 12"])


def test_precedence_and_unary(prog):
    check(prog, "A1: =2+3*4\nA2: =(2+3)*4\nA3: =10-4-3\nA4: =-2*-3\nA5: =--5\nA6: =100/10/5\nA7: =2*(3+(4-1))*2\n",
          ["A1 = 14", "A2 = 20", "A3 = 3", "A4 = 6", "A5 = 5", "A6 = 2", "A7 = 24"])


def test_division_truncates_toward_zero(prog):
    check(prog, "A1: =-7/2\nA2: =7/-2\nA3: =-7/-2\nA4: =7/2\nA5: =1/3\nA6: =-1/3\n",
          ["A1 = -3", "A2 = -3", "A3 = 3", "A4 = 3", "A5 = 0", "A6 = 0"])


def test_spaces_between_tokens(prog):
    check(prog, "A1: =  1 +2*  ( 3 )\nA2: = SUM ( A1 : A1 )\nA3:   =A1   \n", ["A1 = 7", "A2 = 7", "A3 = 7"])


def test_parse_errors(prog):
    bad = ["=", "=1 +", "=(1", "=1)", "=1 2", "=a1", "=A0", "=A100", "=AA1", "=sum(A1:A2)", "=SUM(A1)",
           "=SUM(A1:A2", "=AVG(A1:A2)", "=1 % 2", "=1.5", "=A01", "=SUM(1:2)", "=*2", "=A1:A2"]
    text = "".join(f"B{i + 1}: {b}\n" for i, b in enumerate(bad))
    check(prog, text, [f"B{i + 1} = #PARSE" for i in range(len(bad))])


def test_empty_cells_are_zero(prog):
    check(prog, "A1: =B7 + 5\nA2: =MIN(C1:C3)\nA3: =MAX(D1:D2) - 1\nA4: =10 / Z9\n",
          ["A1 = 5", "A2 = 0", "A3 = -1", "A4 = #DIV0"])


def test_ranges_any_corner_order(prog):
    check(prog, "A1: 1\nA2: 2\nB1: 3\nB2: 4\nC1: =SUM(B2:A1)\nC2: =SUM(A2:B1)\nC3: =MIN(B1:A2)\nC4: =MAX(A2:A1)\n",
          ["A1 = 1", "A2 = 2", "B1 = 3", "B2 = 4", "C1 = 10", "C2 = 10", "C3 = 1", "C4 = 2"])


def test_min_max_with_negatives_and_empties(prog):
    check(prog, "A1: -5\nA3: -2\nB1: =MIN(A1:A3)\nB2: =MAX(A1:A3)\nB3: =MAX(A1:A1)\n",
          ["A1 = -5", "A3 = -2", "B1 = -5", "B2 = 0", "B3 = -5"])


def test_self_reference(prog):
    check(prog, "A1: =A1\nA2: =SUM(A1:A3)\nB1: =1 + B1 * 0\n", ["A1 = #CYCLE", "A2 = #CYCLE", "B1 = #CYCLE"])


def test_cycle_beats_other_errors(prog):
    check(prog, "A1: =1/0 + B1\nB1: =A1\nC1: =1/0 + A1\nD1: =A1 + 1/0\n",
          ["A1 = #CYCLE", "B1 = #CYCLE", "C1 = #DIV0", "D1 = #CYCLE"])


def test_cycle_through_range(prog):
    check(prog, "A1: =SUM(A2:A4)\nA3: =A1 * 2\nB1: =A3\nA5: =SUM(A1:A1)\n",
          ["A1 = #CYCLE", "A3 = #CYCLE", "A5 = #CYCLE", "B1 = #CYCLE"])


def test_dependents_of_cycle_are_not_in_cycle_but_error(prog):
    check(prog, "A1: =A2\nA2: =A1\nB1: =5\nB2: =B1 + A1\nB3: =B2 * 0\n",
          ["A1 = #CYCLE", "A2 = #CYCLE", "B1 = 5", "B2 = #CYCLE", "B3 = #CYCLE"])


def test_first_error_left_to_right(prog):
    check(prog, "A1: x\nA2: =1/0\nB1: =A2 + A1\nB2: =A1 + A2\nB3: =SUM(A1:A2)\nB4: =-A2\nB5: =(A1)*(A2)\n",
          ["A1 = #PARSE", "A2 = #DIV0", "B1 = #DIV0", "B2 = #PARSE", "B3 = #PARSE", "B4 = #DIV0",
           "B5 = #PARSE"])


def test_range_order_column_major(prog):
    check(prog, "A2: =1/0\nB1: bad\nC1: =SUM(A1:B2)\nC2: =SUM(B1:A2)\n",
          ["A2 = #DIV0", "B1 = #PARSE", "C1 = #DIV0", "C2 = #DIV0"])


def test_chain_of_references(prog):
    lines = ["A1: 1"] + [f"A{i}: =A{i - 1} + {i}" for i in range(2, 100)]
    random.Random(4).shuffle(lines)
    check(prog, "\n".join(lines) + "\n")


def test_deep_chain_across_columns(prog):
    names = [f"{chr(65 + c)}{r}" for c in range(26) for r in range(1, 21)]
    lines = [f"{names[0]}: 3"] + [f"{names[i]}: ={names[i - 1]} - 1" for i in range(1, len(names))]
    random.Random(5).shuffle(lines)
    check(prog, "\n".join(lines) + "\n")


def gen_sheet(seed, n):
    rng = random.Random(seed)
    names = [f"{chr(65 + rng.randrange(6))}{rng.randrange(1, 13)}" for _ in range(n)]

    def gen_expr(depth):
        k = rng.random()
        if depth > 2 or k < 0.3:
            return str(rng.randrange(0, 20))
        if k < 0.55:
            return rng.choice(names)
        if k < 0.65:
            f = rng.choice(["SUM", "MIN", "MAX"])
            return f"{f}({rng.choice(names)}:{rng.choice(names)})"
        if k < 0.72:
            return f"-{gen_expr(depth + 1)}"
        if k < 0.8:
            return f"({gen_expr(depth + 1)})"
        return f"{gen_expr(depth + 1)} {rng.choice('+-*/')} {gen_expr(depth + 1)}"

    lines = []
    for name in names:
        r = rng.random()
        if r < 0.3:
            lines.append(f"{name}: {rng.randrange(-50, 50)}")
        elif r < 0.33:
            lines.append(f"{name}: =({gen_expr(1)}")
        else:
            lines.append(f"{name}: ={gen_expr(0)}")
    return "\n".join(lines) + "\n"


def small_values(text):
    """True when every value stays within the 48-bit promise of the task (checked on the final values; the
    generator keeps expressions shallow so intermediates stay close)."""
    for line in model(text):
        v = line.split(" = ")[1]
        if not v.startswith("#") and abs(int(v)) >= 2 ** 40:
            return False
    return True


def generated(first_seed, count, n):
    seed, out = first_seed, []
    while len(out) < count:
        text = gen_sheet(seed, n)
        if small_values(text):
            out.append(text)
        seed += 1
    return out


def check_all(prog, texts):
    for text in texts:
        try:
            check(prog, text)
        except Fail as e:
            raise Fail(f"{e}\n  input:\n{text}")


def test_generated_small(prog):
    check_all(prog, generated(0, 20, 8))


def test_generated_large(prog):
    check_all(prog, generated(100, 5, 60))
