"""Hidden acceptance tests for the ledger task."""

from checks import Fail, expect_eq

TIMEOUT = 60


def check(prog, stdin, want_lines, want_code):
    r = prog.run(stdin)
    got = r.out.replace("\r\n", "\n").rstrip("\n").split("\n")
    want = want_lines
    if got != want:
        for i, (g, w) in enumerate(zip(got, want)):
            if g != w:
                raise Fail(f"line {i + 1} of output: expected {w!r}, got {g!r}")
        raise Fail(f"expected {len(want)} output lines, got {len(got)}; full output {r.out[:400]!r}")
    expect_eq(r.code, want_code, "exit status")


def test_example_from_task(prog):
    check(prog, "open alice\nopen bob\ndeposit alice 100\ntransfer alice bob 30.5\nwithdraw bob 40\n# comment\n"
                "close bob\npay bob 1\n",
          ["line 5: insufficient funds: bob", "line 7: balance not zero: bob", "line 8: unknown command: pay",
           "--- balances ---", "alice 69.50", "bob 30.50", "errors: 3"], 1)


def test_no_errors_exit_zero(prog):
    check(prog, "open a\ndeposit a 1.25\n", ["--- balances ---", "a 1.25", "errors: 0"], 0)


def test_empty_input(prog):
    check(prog, "", ["--- balances ---", "errors: 0"], 0)


def test_cents_are_exact(prog):
    lines = ["open x"] + ["deposit x 0.1"] * 10 + ["deposit x 0.2", "withdraw x 0.3"]
    check(prog, "\n".join(lines) + "\n", ["--- balances ---", "x 0.90", "errors: 0"], 0)


def test_large_balances_exact(prog):
    stdin = "open big\ndeposit big 999999999999999.99\ndeposit big 999999999999999.99\ndeposit big 0.03\n" \
            "withdraw big 0.02\n"
    check(prog, stdin, ["--- balances ---", "big 1999999999999999.99", "errors: 0"], 0)


def test_sorted_byte_order(prog):
    check(prog, "open bob\nopen Alice\nopen alice\nopen _x\nopen Z9\n",
          ["--- balances ---", "Alice 0.00", "Z9 0.00", "_x 0.00", "alice 0.00", "bob 0.00", "errors: 0"], 0)


def test_whitespace_tabs_and_comments(prog):
    check(prog, "  open   a  \n\n\t# note\n\topen\tb\ndeposit a\t\t7\n   \n#deposit a 5\n",
          ["--- balances ---", "a 7.00", "b 0.00", "errors: 0"], 0)


def test_line_numbers_count_blank_and_comment_lines(prog):
    check(prog, "\n# c\n\nfoo\n", ["line 4: unknown command: foo", "--- balances ---", "errors: 1"], 1)


def test_command_words_case_sensitive(prog):
    check(prog, "Open a\nopen a\nDEPOSIT a 1\n",
          ["line 1: unknown command: Open", "line 3: unknown command: DEPOSIT", "--- balances ---", "a 0.00",
           "errors: 2"], 1)


def test_wrong_argument_counts(prog):
    check(prog, "open\nopen a b\ndeposit a\nwithdraw a 1 2\ntransfer a b\nclose\n",
          ["line 1: wrong number of arguments: open", "line 2: wrong number of arguments: open",
           "line 3: wrong number of arguments: deposit", "line 4: wrong number of arguments: withdraw",
           "line 5: wrong number of arguments: transfer", "line 6: wrong number of arguments: close",
           "--- balances ---", "errors: 6"], 1)


def test_invalid_names(prog):
    check(prog, "open bad-name\nopen " + "a" * 21 + "\nopen " + "b" * 20 + "\nopen é\n",
          ["line 1: invalid name: bad-name", "line 2: invalid name: " + "a" * 21, "line 4: invalid name: é",
           "--- balances ---", "b" * 20 + " 0.00", "errors: 3"], 1)


def test_invalid_amounts(prog):
    bad = ["0", "0.00", "-5", "1.234", ".5", "5.", "1e3", "abc", "+5", "1,00", "1234567890123456"]
    stdin = "open a\n" + "".join(f"deposit a {b}\n" for b in bad) + "deposit a 0.01\n"
    want = [f"line {i + 2}: invalid amount: {b}" for i, b in enumerate(bad)]
    check(prog, stdin, want + ["--- balances ---", "a 0.01", f"errors: {len(bad)}"], 1)


def test_amount_checked_before_account(prog):
    check(prog, "deposit ghost x\ndeposit ghost 5\n",
          ["line 1: invalid amount: x", "line 2: no such account: ghost", "--- balances ---", "errors: 2"], 1)


def test_unknown_accounts_left_to_right(prog):
    check(prog, "open a\ntransfer p q 1\ntransfer a q 1\ntransfer q a 1\nwithdraw z 1\nclose z\n",
          ["line 2: no such account: p", "line 3: no such account: q", "line 4: no such account: q",
           "line 5: no such account: z", "line 6: no such account: z", "--- balances ---", "a 0.00",
           "errors: 5"], 1)


def test_open_existing(prog):
    check(prog, "open a\ndeposit a 3\nopen a\n",
          ["line 3: account exists: a", "--- balances ---", "a 3.00", "errors: 1"], 1)


def test_same_account_transfer(prog):
    check(prog, "open a\ndeposit a 5\ntransfer a a 1\ntransfer a a 100\n",
          ["line 3: same account: a", "line 4: same account: a", "--- balances ---", "a 5.00", "errors: 2"], 1)


def test_insufficient_funds_exact_boundary(prog):
    check(prog, "open a\nopen b\ndeposit a 10\nwithdraw a 10.01\ntransfer a b 10.00\ntransfer a b 0.01\n",
          ["line 4: insufficient funds: a", "line 6: insufficient funds: a", "--- balances ---", "a 0.00",
           "b 10.00", "errors: 2"], 1)


def test_close_and_reopen(prog):
    check(prog, "open a\ndeposit a 2\nwithdraw a 2\nclose a\ndeposit a 1\nopen a\nclose a\nclose a\n",
          ["line 5: no such account: a", "line 8: no such account: a", "--- balances ---", "errors: 2"], 1)


def test_reopened_account_starts_at_zero(prog):
    check(prog, "open a\nopen b\ndeposit a 5\ntransfer a b 5\nclose a\nopen a\ndeposit a 0.5\n",
          ["--- balances ---", "a 0.50", "b 5.00", "errors: 0"], 0)


def test_many_operations(prog):
    lines = ["open acc"]
    for i in range(1, 301):
        lines.append(f"deposit acc {i}.{i % 100:02d}")
    total = sum(i * 100 + i % 100 for i in range(1, 301))
    lines.append("withdraw acc 1")
    total -= 100
    check(prog, "\n".join(lines) + "\n", ["--- balances ---", f"acc {total // 100}.{total % 100:02d}", "errors: 0"], 0)


def test_crlf_line_endings(prog):
    check(prog, "open a\r\ndeposit a 4.5\r\n", ["--- balances ---", "a 4.50", "errors: 0"], 0)
