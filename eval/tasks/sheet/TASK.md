# Task: sheet

Write a command-line program that evaluates a small spreadsheet read from standard input and prints the value
of every cell.

## Input

Each non-blank line defines one cell:

```
CELL: CONTENT
```

* `CELL` is a column letter `A`-`Z` followed by a row number `1`-`99` without leading zeros (`A1`, `C12`).
* `CONTENT` is everything after the first `:`, with surrounding spaces removed.
* If the same cell is defined more than once, the last definition wins.

Every line is well-formed. Cells that are not defined are **empty** and count as `0` wherever they are used,
but they are not printed.

## Content

* If `CONTENT` does not start with `=`, it must be an integer: an optional `-` followed by one or more digits
  (`42`, `-7`, `007`). Anything else (`1.5`, `abc`, `+3`, empty) makes the cell's value `#PARSE`.
* If it starts with `=`, the rest is a **formula** following this grammar (spaces are allowed between any two
  tokens):

```
expr    := term (("+" | "-") term)*
term    := unary (("*" | "/") unary)*
unary   := "-" unary | primary
primary := INTEGER | CELL | FUNC "(" CELL ":" CELL ")" | "(" expr ")"
FUNC    := "SUM" | "MIN" | "MAX"
```

`INTEGER` is one or more digits; cell names and function names are uppercase only. A formula that does not
match the grammar exactly (including trailing tokens, `=` alone, lowercase names, a cell like `A0` or `A100`)
makes the cell's value `#PARSE`.

## Evaluation

* Values are integers. `/` divides and truncates toward zero (`-7 / 2` is `-3`). All intermediate values fit
  in 48 bits.
* A range `X:Y` covers every cell in the rectangle with corners X and Y (in any order). Its cells are visited
  column by column (A before B), top to bottom within a column. `SUM` adds them, `MIN` and `MAX` take the
  smallest and largest; empty cells count as 0.

Errors:

* `#PARSE`: the cell's content is invalid (see above).
* `#CYCLE`: the cell is part of a reference cycle, meaning that by following references (including every cell
  of a range) from this cell you can come back to it. This holds no matter what else the formula contains.
* `#DIV0`: division by zero.
* A cell that uses a cell holding an error gets an error too. Operands are evaluated left to right (and range
  cells in the order above), and the **first** error met is the result: `=1/0 + B1` is `#DIV0` even if `B1`
  is `#PARSE`.

## Output

Print one line per defined cell, `CELL = VALUE`, sorted by column letter and then by row number (`A2` before
`A10` before `B1`). Exit status 0.

## Example

Input:

```
A1: 10
A2: =A1 * 2 + B5
B1: =SUM(A1:A3)
B2: =A1 / (A3 - 3)
A3: 3
C1: =C2 + 1
C2: =C1
C3: =C1 + 1
D1: = MAX(A1:B1)
D2: =A1 +
```

Output:

```
A1 = 10
A2 = 20
A3 = 3
B1 = 33
B2 = #DIV0
C1 = #CYCLE
C2 = #CYCLE
C3 = #CYCLE
D1 = 33
D2 = #PARSE
```
