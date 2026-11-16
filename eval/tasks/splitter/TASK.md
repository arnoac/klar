# Task: splitter

Build a browser app that splits shared expenses between friends. It runs entirely in the page (no server):
people type who paid how much for what, and the app shows who owes whom.

Tests drive the page in a real browser and find elements by their `id` (and the `remove` class), so use
exactly the ids below. Layout and styling are up to you.

## Elements

| Element | id | Notes |
|---|---|---|
| text input for the person who paid | `name` | |
| text input for the amount | `amount` | dollars, such as `12.50` |
| text input for a description | `desc` | may be left empty |
| button that adds the expense | `add` | pressing Enter in any of the three inputs does the same |
| list of expenses | `expenses` | one `li` per expense, oldest first |
| list of balances | `balances` | one `li` per person |
| total | `total` | |
| error message | `error` | present only while there is an error |

## Adding an expense

When the user clicks `add` (or presses Enter in an input):

* The name is the `name` input with surrounding spaces removed; it must not be empty.
* The amount must be one or more digits, optionally followed by `.` and one or two digits, and greater than
  zero (`5`, `5.5`, `0.01`; not `0`, `-1`, `1.234`, `abc`, or empty). Surrounding spaces are ignored.
* If either is invalid, show an element with id `error` holding a short message (any non-empty text) and add
  nothing; all inputs keep what was typed.
* Otherwise add the expense, clear the `amount` and `desc` inputs (keep `name`, so one person can add several
  expenses quickly) and remove the `error` element if it was shown.

Each expense is an `li` in `expenses` whose text starts with `NAME paid AMOUNT for DESC`, or
`NAME paid AMOUNT` when the description is empty after trimming, with AMOUNT written with two decimals
(`Ann paid 12.50 for pizza`). The `li` also contains a button with class `remove` that deletes that expense.
Names are case-sensitive.

## Totals and balances

* `total` shows `Total: T`, the sum of all expenses with two decimals (`Total: 0.00` when there are none).
* Everyone who appears in at least one expense shares the total equally. A person's share is the total
  divided by the number of people, rounded to the nearest cent (half a cent rounds up). Their balance is what
  they paid minus their share.
* `balances` has one `li` per person, sorted by name (by code point, so uppercase first), with the text
  `NAME: BALANCE`. BALANCE has two decimals and a sign: `+12.50`, `-3.33`, or `0.00` without a sign.

Example: Ann pays 30.00 and Bob pays 10.00. Total 40.00, share 20.00: `Ann: +10.00`, `Bob: -10.00`.

## Keeping the data

Expenses survive reloading the page (store them in the browser, for example in `localStorage`). A reload
shows the same expenses, totals and balances; the inputs start empty.
