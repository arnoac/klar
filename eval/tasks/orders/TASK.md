# Task: orders

Write a command-line program that reads a JSON document of shop orders from standard input, validates each
order, and prints a JSON summary on standard output.

## Input

```json
{
  "orders": [
    {"id": "A1", "customer": "ann", "items": [{"sku": "pen", "qty": 2, "price_cents": 150}], "coupon": "SAVE10"},
    {"id": "A2", "customer": "bob", "items": [{"sku": "ink", "qty": 1, "price_cents": 900}]}
  ]
}
```

Each order has:

* `id`: a non-empty string, unique among valid orders.
* `customer`: a non-empty string.
* `items`: a non-empty array of items. Each item has `sku` (non-empty string), `qty` (whole number, at least
  1) and `price_cents` (whole number, at least 0).
* `coupon` (optional): a string, or `null` (same as absent).

Other keys anywhere are ignored. An entry of `orders` that is not an object counts as an order with no
fields.

## Validation

Check each order in input order. An order is invalid for the **first** reason that applies:

1. `"missing id"`: `id` is absent or is not a non-empty string.
2. `"duplicate id"`: an earlier **valid** order has the same `id`.
3. `"missing customer"`: `customer` is absent or is not a non-empty string.
4. `"no items"`: `items` is absent, not an array, or empty.
5. `"bad item"`: some item is not an object, or has a `sku`, `qty` or `price_cents` that breaks the rules above
   (a `qty` of `2.5` or `"2"` is bad).
6. `"unknown coupon"`: `coupon` is present, not `null`, and not one of the coupons below (a coupon that is not a
   string is unknown too).

Invalid orders are skipped and do not count anywhere else.

## Totals

An order's subtotal is the sum of `qty * price_cents` over its items. Coupons:

* `"SAVE10"`: 10% off the subtotal, rounded down to a whole cent of discount (subtotal 1999 -> discount 199,
  total 1800).
* `"FLAT500"`: 500 cents off, but the total never goes below 0.

## Output

Print exactly one JSON object (whitespace and key order do not matter):

```json
{
  "valid": 2,
  "invalid": [{"id": "A3", "reason": "bad item"}],
  "customers": {"ann": {"orders": 1, "total_cents": 270}, "bob": {"orders": 1, "total_cents": 900}},
  "top_sku": "pen"
}
```

* `valid`: number of valid orders.
* `invalid`: one entry per invalid order, in input order. `id` is the order's `id` if it is a non-empty
  string, otherwise `null`.
* `customers`: for each customer with at least one valid order, the number of valid orders and the sum of their
  totals (after coupons).
* `top_sku`: over valid orders, the sku with the largest total `qty`; on a tie, the one that sorts first
  (byte order); `null` when there are no valid orders.

Exit status 0.

If standard input is not valid JSON, or is not an object with an `orders` array, print exactly
`{"error": "invalid input"}` and exit with status 1.
