# Klar Board

A shared task board for a team, written entirely in Klar: about 1,200 lines across six files. It is the
largest Klar program so far, built to find out what hurts when a Klar codebase grows past a toy.

* **Accounts**: sign up and log in. Passwords are stored as salted PBKDF2 hashes. Sessions are random tokens
  in HttpOnly cookies, and the database keeps only their SHA-256.
* **Boards** have members. Anyone can add another user by name. Boards you are not a member of answer 404,
  the same as boards that do not exist.
* **Cards** sit in three columns (To do, Doing, Done). You can add, edit (title, notes, assignee), drag or
  move between and within columns, delete, and search them.
* **Live**: everyone looking at a board sees every change at once, along with an activity feed.
* **A report tool** signs in over HTTP and summarises every board, fetching them concurrently.

| File | What it is |
|---|---|
| `shared.klar` | types, rules and typed calls used by both the server and the browser: where a moved card lands, validation, search |
| `store.klar` | SQLite: versioned migrations, queries, moving cards in a transaction |
| `auth.klar` | sign up, log in, sessions |
| `server.klar` | answers the typed calls, checks access, sends live updates |
| `app.klar` | the browser app: login, your boards, the board itself, built from `std.layout` and `std.kit` |
| `report.klar` | a command-line report using `std.task` and the HTTP client |

```sh
klar dev examples/apps/board/server.klar examples/apps/board/app.klar      # http://127.0.0.1:8000
klar test examples/apps/board/shared.klar                                  # board rules
klar test examples/apps/board/app.klar                                     # UI logic, no browser needed
klar build examples/apps/board/server.klar -o board                        # one native binary for the backend
echo "$PASSWORD" | klar run examples/apps/board/report.klar http://127.0.0.1:8080 ada
```

`tests/test_board.py` runs the whole API against the interpreter, Node and native backends with two users. It
also clicks through the app in Chromium with two people signed in to the same board.

## What building it taught us

The server API worked on its first run, and the browser app needed no logic fixes. The problems were
elsewhere, and each one below is now fixed and has a regression test in `tests/test_lessons.py`:

1. **No password hashing or secure randomness.** A real login system was impossible. Added `std.crypto`:
   SHA-256, HMAC, secure tokens, and PBKDF2 password hashes that every backend can verify.
2. **Short `std.html` names leaked across the program.** Importing `card` or `p` in the browser app broke an
   unrelated `card` helper in `shared.klar`. A library name now only counts in the modules that import it.
3. **A Rust habit gave two confusing errors.** Ending a `{ ... }` match arm with a value (`Ok(x)` with no
   `return`) produced "unit expected" and "result ignored". It is now one error that says to write `return`.
4. **`klar fmt` mis-indented** a `match` whose subject spanned two lines.
5. **Logging out needed `web_clear_cookie`**, and switching boards needed `ui_unlisten` to close the old live
   stream. `ui_start` is now documented for adding the app's own CSS.

## Removing the glue

A second pass added what the first one found awkward, and the board was ported to it:

* **Typed calls** (`std.api`). Each server function is declared once in `shared.klar`, for example
  `fn add_card_call() -> Api[NewCard, Card]`. The server answers it with `api_serve`, the browser calls it with
  `api_call`, and the report tool with `api_fetch`. Both ends are checked against the same types, so there are
  no routes, URLs, status checks or JSON reading left in the app. Errors are an `ApiError`:
  `Refused(status, message)` for the user, `Internal(message)` for the logs.
* **Typed SQL rows.** `sqlite_rows(db, query, params)` fills structs by column name and tuples by position,
  the way `json_decode` does; `sql(value)` turns any value into a parameter. The `row_*` converters are gone.
* **Tuples** replaced the `Account` struct (`Option[(User, str)]`), and **`?` wraps errors**: in a function
  returning `Result[T, ApiError]`, a `str` error becomes `Internal(e)` by itself. With `ok_or`, the nested
  `Result[Result[Access, HttpResponse], str]` access check became a flat function of `?` lines.

Code lines (not counting blank lines and comments), before and after:

| File | Before | After |
|---|---|---|
| `server.klar` | 200 | 131 |
| `store.klar` | 176 | 138 |
| `auth.klar` | 53 | 45 |
| `report.klar` | 55 | 45 |
| `app.klar` | 489 | 468 |
| `shared.klar` (now also declares the 12 calls) | 150 | 161 |
| **Total** | **1,123** | **988** (12% less) |

The todo app (`examples/apps/todo_web`) went from 277 to 227 lines, 18% less.

Still awkward, and on the roadmap:

* **Drag and drop** now works with the mouse (`on_drag`/`on_drop`). Phones still use the arrow buttons, because HTML
  drag and drop does not fire on touch screens.
