"""Hidden acceptance tests for the tickets task: the old behaviour stays, the bug is fixed, the waitlist and
`capacity` work. `model()` is an independent Python version used for the generated test."""

import os
import random
import re

from checks import Fail, expect_eq

TIMEOUT = 60
EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "starter", "shared", "examples")


def check(prog, text, want):
    r = prog.run(text)
    got = [l for l in r.out.replace("\r\n", "\n").split("\n") if l != ""]
    if got != want:
        for i, (g, w) in enumerate(zip(got, want)):
            if g != w:
                raise Fail(f"line {i + 1} of output: expected {w!r}, got {g!r}")
        raise Fail(f"expected {len(want)} output lines, got {len(got)}: {r.out[-300:]!r}")
    expect_eq(r.code, 0, "exit status")


def money(c):
    return f"{c // 100}.{c % 100:02d}"


def model(text):
    events, order, out = {}, [], []

    def count(t):
        return int(t) if re.fullmatch(r"[0-9]+", t) else None

    def sell(e, person, q):
        e["sold"] += q
        e["revenue"] += q * e["price"]
        e["held"][person] = e["held"].get(person, 0) + q
        e["paid"][person] = e["paid"].get(person, 0) + q * e["price"]
        return f"ok: {person} bought {q} for {e['id']} ({money(q * e['price'])})"

    def fill(e):
        keep, lines = [], []
        for person, q in e["wait"]:
            if e["sold"] + q <= e["cap"]:
                lines.append(sell(e, person, q))
            else:
                keep.append((person, q))
        e["wait"] = keep
        return lines

    for line in text.split("\n"):
        w = line.split()
        if not w or w[0].startswith("#"):
            continue
        cmd, a = w[0], w[1:]
        usage = {"event": "event ID CAPACITY PRICE", "price": "price ID PRICE", "buy": "buy ID PERSON QTY",
                 "refund": "refund ID PERSON", "report": "report", "capacity": "capacity ID N"}
        arity = {"event": 3, "price": 2, "buy": 3, "refund": 2, "report": 0, "capacity": 2}
        if cmd not in arity:
            out.append(f"error: unknown command: {cmd}")
            continue
        if len(a) != arity[cmd]:
            out.append(f"error: usage: {usage[cmd]}")
            continue
        if cmd == "event":
            if a[0] in events:
                out.append(f"error: event exists: {a[0]}")
            elif count(a[1]) is None:
                out.append(f"error: bad number: {a[1]}")
            elif count(a[2]) is None:
                out.append(f"error: bad number: {a[2]}")
            else:
                events[a[0]] = {"id": a[0], "cap": count(a[1]), "price": count(a[2]), "sold": 0, "revenue": 0,
                                "held": {}, "paid": {}, "wait": []}
                order.append(a[0])
                out.append(f"ok: event {a[0]}")
            continue
        if cmd == "report":
            if not order:
                out.append("no events")
            for i in order:
                e = events[i]
                line = f"{i}: {e['sold']}/{e['cap']} sold, revenue {money(e['revenue'])}"
                out.append(line + (f", waiting {len(e['wait'])}" if e["wait"] else ""))
            continue
        e = events.get(a[0])
        if e is None:
            out.append(f"error: no such event: {a[0]}")
            continue
        if cmd == "price":
            p = count(a[1])
            if p is None:
                out.append(f"error: bad number: {a[1]}")
            else:
                e["price"] = p
                out.append(f"ok: price {a[0]} {money(p)}")
        elif cmd == "buy":
            person, q = a[1], count(a[2])
            if not q:
                out.append(f"error: bad number: {a[2]}")
            elif e["sold"] + q > e["cap"]:
                if any(p == person for p, _ in e["wait"]):
                    out.append(f"error: already waiting: {person}")
                else:
                    e["wait"].append((person, q))
                    out.append(f"waitlist: {person} wants {q} for {a[0]} (position {len(e['wait'])})")
            else:
                out.append(sell(e, person, q))
        elif cmd == "refund":
            person = a[1]
            held = e["held"].get(person, 0)
            if held == 0:
                if any(p == person for p, _ in e["wait"]):
                    e["wait"] = [x for x in e["wait"] if x[0] != person]
                    out.append(f"ok: left waitlist: {person}")
                else:
                    out.append(f"error: nothing to refund: {person}")
            else:
                amount = e["paid"].pop(person)
                del e["held"][person]
                e["sold"] -= held
                e["revenue"] -= amount
                out.append(f"ok: refunded {held} to {person} ({money(amount)})")
                out.extend(fill(e))
        elif cmd == "capacity":
            n = count(a[1])
            if n is None:
                out.append(f"error: bad number: {a[1]}")
            elif n < e["sold"]:
                out.append(f"error: capacity below sold: {a[0]}")
            else:
                e["cap"] = n
                out.append(f"ok: capacity {a[0]} {n}")
                out.extend(fill(e))
    return out


def test_existing_errors_unchanged(prog):
    with open(os.path.join(EXAMPLES, "errors.in")) as f, open(os.path.join(EXAMPLES, "errors.out")) as g:
        check(prog, f.read(), g.read().split("\n")[:-1])


def test_basic_flow_without_price_change(prog):
    check(prog, "event a 5 300\nbuy a x 2\nbuy a y 3\nrefund a x\nreport\nreport extra\n",
          ["ok: event a", "ok: x bought 2 for a (6.00)", "ok: y bought 3 for a (9.00)", "ok: refunded 2 to x (6.00)",
           "a: 3/5 sold, revenue 9.00", "error: usage: report"])


def test_no_events_report(prog):
    check(prog, "report\n# comment\n\n", ["no events"])


def test_refund_pays_what_was_paid(prog):
    check(prog, "event gala 10 1000\nbuy gala ann 2\nprice gala 1500\nrefund gala ann\nreport\n",
          ["ok: event gala", "ok: ann bought 2 for gala (20.00)", "ok: price gala 15.00",
           "ok: refunded 2 to ann (20.00)", "gala: 0/10 sold, revenue 0.00"])


def test_refund_after_several_prices(prog):
    check(prog, "event g 10 100\nbuy g ann 1\nprice g 250\nbuy g ann 2\nprice g 5\nbuy g bob 1\nrefund g ann\n"
                "report\n",
          ["ok: event g", "ok: ann bought 1 for g (1.00)", "ok: price g 2.50", "ok: ann bought 2 for g (5.00)",
           "ok: price g 0.05", "ok: bob bought 1 for g (0.05)", "ok: refunded 3 to ann (6.00)",
           "g: 1/10 sold, revenue 0.05"])


def test_waitlist_instead_of_sold_out(prog):
    check(prog, "event gala 3 1000\nbuy gala ann 2\nbuy gala bob 2\nbuy gala cat 5\nreport\n",
          ["ok: event gala", "ok: ann bought 2 for gala (20.00)", "waitlist: bob wants 2 for gala (position 1)",
           "waitlist: cat wants 5 for gala (position 2)", "gala: 2/3 sold, revenue 20.00, waiting 2"])


def test_wait_only_once(prog):
    check(prog, "event e 1 100\nbuy e a 1\nbuy e b 1\nbuy e b 1\nbuy e b 3\nbuy e c 1\n",
          ["ok: event e", "ok: a bought 1 for e (1.00)", "waitlist: b wants 1 for e (position 1)",
           "error: already waiting: b", "error: already waiting: b", "waitlist: c wants 1 for e (position 2)"])


def test_refund_fills_waitlist_in_order_at_current_price(prog):
    check(prog, "event g 4 1000\nbuy g ann 4\nbuy g bob 3\nbuy g cat 1\nbuy g dan 1\nprice g 2000\nrefund g ann\n"
                "report\n",
          ["ok: event g", "ok: ann bought 4 for g (40.00)", "waitlist: bob wants 3 for g (position 1)",
           "waitlist: cat wants 1 for g (position 2)", "waitlist: dan wants 1 for g (position 3)",
           "ok: price g 20.00", "ok: refunded 4 to ann (40.00)", "ok: bob bought 3 for g (60.00)",
           "ok: cat bought 1 for g (20.00)", "g: 4/4 sold, revenue 80.00, waiting 1"])


def test_too_big_requests_stay_and_later_ones_fill(prog):
    check(prog, "event g 3 100\nbuy g a 3\nbuy g big 3\nbuy g s 1\nrefund g a\nreport\n",
          ["ok: event g", "ok: a bought 3 for g (3.00)", "waitlist: big wants 3 for g (position 1)",
           "waitlist: s wants 1 for g (position 2)", "ok: refunded 3 to a (3.00)", "ok: big bought 3 for g (3.00)",
           "g: 3/3 sold, revenue 3.00, waiting 1"])


def test_skips_request_that_does_not_fit(prog):
    check(prog, "event g 4 100\nbuy g a 2\nbuy g b 2\nbuy g big 4\nbuy g s 1\nrefund g a\nreport\n",
          ["ok: event g", "ok: a bought 2 for g (2.00)", "ok: b bought 2 for g (2.00)",
           "waitlist: big wants 4 for g (position 1)", "waitlist: s wants 1 for g (position 2)",
           "ok: refunded 2 to a (2.00)", "ok: s bought 1 for g (1.00)", "g: 3/4 sold, revenue 3.00, waiting 1"])


def test_leave_waitlist(prog):
    check(prog, "event g 1 100\nbuy g a 1\nbuy g b 1\nbuy g c 1\nrefund g b\nrefund g b\nrefund g a\nreport\n",
          ["ok: event g", "ok: a bought 1 for g (1.00)", "waitlist: b wants 1 for g (position 1)",
           "waitlist: c wants 1 for g (position 2)", "ok: left waitlist: b", "error: nothing to refund: b",
           "ok: refunded 1 to a (1.00)", "ok: c bought 1 for g (1.00)", "g: 1/1 sold, revenue 1.00"])


def test_waitlisted_holder_refund_refunds_tickets(prog):
    check(prog, "event g 2 100\nbuy g a 1\nbuy g a 5\nrefund g a\nreport\n",
          ["ok: event g", "ok: a bought 1 for g (1.00)", "waitlist: a wants 5 for g (position 1)",
           "ok: refunded 1 to a (1.00)", "g: 0/2 sold, revenue 0.00, waiting 1"])


def test_capacity_command(prog):
    check(prog, "event g 2 500\nbuy g a 2\nbuy g b 2\nbuy g c 1\ncapacity g 1\ncapacity g 4\ncapacity g 4\n"
                "capacity g\ncapacity x 3\ncapacity g many\nreport\n",
          ["ok: event g", "ok: a bought 2 for g (10.00)", "waitlist: b wants 2 for g (position 1)",
           "waitlist: c wants 1 for g (position 2)", "error: capacity below sold: g", "ok: capacity g 4",
           "ok: b bought 2 for g (10.00)", "ok: capacity g 4", "error: usage: capacity ID N",
           "error: no such event: x", "error: bad number: many", "g: 4/4 sold, revenue 20.00, waiting 1"])


def test_capacity_down_to_sold(prog):
    check(prog, "event g 9 100\nbuy g a 3\ncapacity g 3\nbuy g b 1\nreport\n",
          ["ok: event g", "ok: a bought 3 for g (3.00)", "ok: capacity g 3", "waitlist: b wants 1 for g (position 1)",
           "g: 3/3 sold, revenue 3.00, waiting 1"])


def test_waitlists_are_per_event(prog):
    check(prog, "event x 1 100\nevent y 1 100\nbuy x a 1\nbuy y a 1\nbuy x b 1\nbuy y b 1\nrefund x a\nreport\n",
          ["ok: event x", "ok: event y", "ok: a bought 1 for x (1.00)", "ok: a bought 1 for y (1.00)",
           "waitlist: b wants 1 for x (position 1)", "waitlist: b wants 1 for y (position 1)",
           "ok: refunded 1 to a (1.00)", "ok: b bought 1 for x (1.00)", "x: 1/1 sold, revenue 1.00",
           "y: 1/1 sold, revenue 1.00, waiting 1"])


def test_filled_buyer_refund_pays_fill_price(prog):
    check(prog, "event g 1 100\nbuy g a 1\nbuy g b 1\nprice g 700\nrefund g a\nprice g 1\nrefund g b\nreport\n",
          ["ok: event g", "ok: a bought 1 for g (1.00)", "waitlist: b wants 1 for g (position 1)",
           "ok: price g 7.00", "ok: refunded 1 to a (1.00)", "ok: b bought 1 for g (7.00)", "ok: price g 0.01",
           "ok: refunded 1 to b (7.00)", "g: 0/1 sold, revenue 0.00"])


def test_generated_sessions(prog):
    rng = random.Random(142)
    for _ in range(8):
        lines = [f"event e{i} {rng.randrange(1, 8)} {rng.randrange(0, 2000)}" for i in range(3)]
        for _ in range(60):
            e = f"e{rng.randrange(3)}"
            p = rng.choice("abcdefg")
            k = rng.random()
            if k < 0.45:
                lines.append(f"buy {e} {p} {rng.randrange(1, 5)}")
            elif k < 0.7:
                lines.append(f"refund {e} {p}")
            elif k < 0.8:
                lines.append(f"price {e} {rng.randrange(0, 3000)}")
            elif k < 0.9:
                lines.append(f"capacity {e} {rng.randrange(0, 10)}")
            else:
                lines.append("report")
        lines.append("report")
        text = "\n".join(lines) + "\n"
        try:
            check(prog, text, model(text))
        except Fail as err:
            raise Fail(f"{err}\n  input:\n{text}")
