"""Hidden acceptance tests for the orders task."""

import json

from checks import Fail, expect_eq

TIMEOUT = 60


def run_json(prog, doc, raw=None):
    r = prog.run(raw if raw is not None else json.dumps(doc))
    try:
        out = json.loads(r.out)
    except ValueError:
        raise Fail(f"output is not one JSON value: {r.out[:300]!r}")
    return r, out


def summary(prog, orders):
    r, out = run_json(prog, {"orders": orders})
    expect_eq(r.code, 0, "exit status")
    if not isinstance(out, dict):
        raise Fail(f"output is not a JSON object: {out!r}")
    return out


def item(sku="pen", qty=1, price=100):
    return {"sku": sku, "qty": qty, "price_cents": price}


def order(id="A1", customer="ann", items=None, **extra):
    o = {"id": id, "customer": customer, "items": items if items is not None else [item()]}
    o.update(extra)
    return o


def test_example_shape(prog):
    out = summary(prog, [
        order("A1", "ann", [item("pen", 2, 150)], coupon="SAVE10"),
        order("A2", "bob", [item("ink", 1, 900)]),
        order("A3", "ann", [item("pen", 0, 150)]),
    ])
    expect_eq(out, {"valid": 2, "invalid": [{"id": "A3", "reason": "bad item"}],
                    "customers": {"ann": {"orders": 1, "total_cents": 270},
                                  "bob": {"orders": 1, "total_cents": 900}},
                    "top_sku": "pen"})


def test_empty_orders(prog):
    expect_eq(summary(prog, []), {"valid": 0, "invalid": [], "customers": {}, "top_sku": None})


def test_save10_rounds_discount_down(prog):
    out = summary(prog, [order("A", "c", [item("x", 1, 1999)], coupon="SAVE10")])
    expect_eq(out["customers"], {"c": {"orders": 1, "total_cents": 1800}}, "customers")


def test_save10_small_amounts(prog):
    out = summary(prog, [order("A", "c", [item("x", 1, 9)], coupon="SAVE10"),
                         order("B", "c", [item("x", 3, 7)], coupon="SAVE10")])
    expect_eq(out["customers"], {"c": {"orders": 2, "total_cents": 9 + 19}}, "customers")


def test_flat500_never_negative(prog):
    out = summary(prog, [order("A", "c", [item("x", 2, 100)], coupon="FLAT500"),
                         order("B", "d", [item("x", 1, 700)], coupon="FLAT500")])
    expect_eq(out["customers"], {"c": {"orders": 1, "total_cents": 0}, "d": {"orders": 1, "total_cents": 200}},
              "customers")


def test_null_coupon_is_absent(prog):
    out = summary(prog, [order("A", "c", [item("x", 1, 100)], coupon=None)])
    expect_eq(out["valid"], 1, "valid")
    expect_eq(out["customers"]["c"]["total_cents"], 100, "total")


def test_unknown_coupons(prog):
    out = summary(prog, [order("A", coupon="save10"), order("B", coupon=10), order("C", coupon=""),
                         order("D", coupon=["SAVE10"])])
    expect_eq(out["invalid"], [{"id": k, "reason": "unknown coupon"} for k in "ABCD"], "invalid")
    expect_eq(out["valid"], 0, "valid")


def test_missing_id_variants(prog):
    no_id = {"customer": "c", "items": [item()]}
    out = summary(prog, [no_id, order(id=""), order(id=7), order(id=None)])
    expect_eq(out["invalid"], [{"id": None, "reason": "missing id"}] * 4, "invalid")


def test_duplicate_only_against_valid(prog):
    out = summary(prog, [order("A", items=[]), order("A"), order("A"), order("B")])
    expect_eq(out["invalid"], [{"id": "A", "reason": "no items"}, {"id": "A", "reason": "duplicate id"}],
              "invalid")
    expect_eq(out["valid"], 2, "valid")


def test_reason_order(prog):
    # several problems at once: the first in the list wins
    out = summary(prog, [
        {"id": "A", "items": [], "coupon": "BAD"},
        {"id": "B", "customer": "", "items": [{"sku": ""}]},
        {"id": "C", "customer": "c", "items": [{"sku": "", "qty": 1, "price_cents": 1}], "coupon": "BAD"},
        {"id": "D", "customer": "c", "items": "pen"},
        {"id": "E", "customer": "c"},
    ])
    expect_eq(out["invalid"], [{"id": "A", "reason": "missing customer"}, {"id": "B", "reason": "missing customer"},
                               {"id": "C", "reason": "bad item"}, {"id": "D", "reason": "no items"},
                               {"id": "E", "reason": "no items"}], "invalid")


def test_bad_items(prog):
    bad = [
        [item(qty=2.5)], [item(qty="2")], [item(qty=0)], [item(qty=-1)], [item(price=-1)], [item(price=1.5)],
        [{"qty": 1, "price_cents": 1}], [item(sku=3)], ["pen"], [item(), None], [{"sku": "x", "qty": 1}],
        [item(qty=True)],
    ]
    out = summary(prog, [order(f"O{i}", items=items) for i, items in enumerate(bad)])
    expect_eq(out["invalid"], [{"id": f"O{i}", "reason": "bad item"} for i in range(len(bad))], "invalid")


def test_zero_price_is_fine(prog):
    out = summary(prog, [order("A", "c", [item("free", 3, 0)])])
    expect_eq(out, {"valid": 1, "invalid": [], "customers": {"c": {"orders": 1, "total_cents": 0}},
                    "top_sku": "free"})


def test_extra_keys_ignored(prog):
    o = order("A", "c", [dict(item("x", 1, 5), color="red")], note={"a": 1})
    out = summary(prog, [o])
    expect_eq(out["valid"], 1, "valid")


def test_top_sku_by_quantity_with_tie(prog):
    out = summary(prog, [order("A", "c", [item("b", 3, 1), item("a", 1, 1)]),
                         order("B", "c", [item("a", 2, 1), item("c", 2, 1)]),
                         order("X", "c", [item("z", 100, 1)], coupon="NOPE")])
    expect_eq(out["top_sku"], "a", "top_sku")


def test_customers_aggregate(prog):
    orders = [order(f"N{i}", "ann" if i % 3 else "bob", [item("s", i + 1, 250)]) for i in range(30)]
    out = summary(prog, orders)
    ann = [i for i in range(30) if i % 3]
    bob = [i for i in range(30) if not i % 3]
    expect_eq(out["customers"], {"ann": {"orders": len(ann), "total_cents": sum((i + 1) * 250 for i in ann)},
                                 "bob": {"orders": len(bob), "total_cents": sum((i + 1) * 250 for i in bob)}},
              "customers")


def test_large_totals(prog):
    out = summary(prog, [order("A", "c", [item("x", 1000000, 99999999)])])
    expect_eq(out["customers"]["c"]["total_cents"], 1000000 * 99999999, "total")


def test_unicode_strings(prog):
    out = summary(prog, [order("Ω1", "zoë", [item("café", 1, 10)])])
    expect_eq(out, {"valid": 1, "invalid": [], "customers": {"zoë": {"orders": 1, "total_cents": 10}},
                    "top_sku": "café"})


def test_invalid_json(prog):
    r, out = run_json(prog, None, raw="{\"orders\": [")
    expect_eq(out, {"error": "invalid input"})
    expect_eq(r.code, 1, "exit status")


def test_not_an_orders_object(prog):
    for doc in ([], {"orders": {}}, {"order": []}, "x", 3):
        r, out = run_json(prog, doc)
        expect_eq(out, {"error": "invalid input"}, f"output for {json.dumps(doc)}")
        expect_eq(r.code, 1, f"exit status for {json.dumps(doc)}")


def test_order_not_an_object(prog):
    out = summary(prog, ["A1", 5, None, order("B")])
    expect_eq(out["invalid"], [{"id": None, "reason": "missing id"}] * 3, "invalid")
    expect_eq(out["valid"], 1, "valid")
