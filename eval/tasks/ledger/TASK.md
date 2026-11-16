# Task: ledger

Write a command-line program that reads banking commands from standard input, one per line, applies them in
order, and prints a report on standard output.

## Input

Each line holds one command. Words are separated by one or more spaces or tabs; leading and trailing
whitespace is ignored. Blank lines and lines whose first non-space character is `#` are ignored (but still
count for line numbers).

| Command | Meaning |
|---|---|
| `open NAME` | Create an account with balance 0. |
| `deposit NAME AMOUNT` | Add AMOUNT to the account. |
| `withdraw NAME AMOUNT` | Remove AMOUNT from the account. |
| `transfer FROM TO AMOUNT` | Move AMOUNT from account FROM to account TO. |
| `close NAME` | Remove the account. Only allowed when its balance is exactly 0. |

Command words are case-sensitive (`Open` is an unknown command).

* `NAME` is 1 to 20 characters, each an ASCII letter, digit or `_`. Names are case-sensitive.
* `AMOUNT` is one or more ASCII digits, optionally followed by `.` and one or two digits
  (`5`, `5.5`, `5.50`, `0.01`). It must be greater than zero. Amounts have at most 15 digits before the
  point. Balances can therefore become very large, and every balance must be **exact** to the cent.

## Errors

A line that cannot be applied is skipped (it changes nothing) and produces one error line:

```
line N: MESSAGE
```

where `N` is the 1-based line number in the input. Check in this order and report only the first problem:

1. Unknown command word: `unknown command: WORD`
2. Wrong number of arguments for the command: `wrong number of arguments: COMMAND`
3. For `open` only, a NAME that breaks the naming rule: `invalid name: NAME`
4. An AMOUNT that breaks the amount rule (including zero): `invalid amount: TOKEN`
5. An account named in the command does not exist, checked left to right
   (`open` excepted): `no such account: NAME`
6. `open` of a name that already exists: `account exists: NAME`
7. `transfer` where FROM and TO are the same account: `same account: NAME`
8. `withdraw` or `transfer` of more than the balance: `insufficient funds: NAME` (the account paying)
9. `close` of an account whose balance is not 0: `balance not zero: NAME`

A closed account no longer exists; its name may be opened again, starting from 0.

Error lines are printed as they happen, before the report.

## Report

After the last input line, print:

```
--- balances ---
NAME BALANCE
...
errors: K
```

with one line per open account, sorted by name in byte order (uppercase before lowercase), the balance
written with exactly two decimals (`0.00`, `12.50`, `1000000000000000.01`), and `K` the number of error
lines printed. The program exits with status 0 if there were no errors, otherwise with status 1.

## Example

Input:

```
open alice
open bob
deposit alice 100
transfer alice bob 30.5
withdraw bob 40
# comment
close bob
pay bob 1
```

Output (exit status 1):

```
line 5: insufficient funds: bob
line 7: balance not zero: bob
line 8: unknown command: pay
--- balances ---
alice 69.50
bob 30.50
errors: 3
```
