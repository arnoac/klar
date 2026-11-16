# Klar Board

A shared task board for a team, written entirely in Klar: about 1,400 lines across six files. It is the
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
| `shared.klar` | types and rules used by both the server and the browser: where a moved card lands, validation, search |
| `store.klar` | SQLite: versioned migrations, queries, moving cards in a transaction |
| `auth.klar` | sign up, log in, sessions |
| `server.klar` | the JSON API, access checks and live updates |
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

Still awkward, and on the roadmap:

* **Access checks.** A "user plus board, or the reply to send instead" helper returns
  `Result[Result[Access, HttpResponse], str]`. Each caller unpacks it with a `match` that returns early. It
  reads fine, but a small middleware helper in `std.web` would remove the nesting.
* **Converting database rows by hand** (`row_card`, `row_user`, ...) is the most repetitive code. Typed queries
  that fill a struct by column name, the way `json_decode` does, would remove it.
* **No tuples.** A function returning "a user and their password hash" needs a named struct (`Account`). That is
  clearer, but costs a declaration.
* **Drag and drop** now works with the mouse (`on_drag`/`on_drop`). Phones still use the arrow buttons, because HTML
  drag and drop does not fire on touch screens.
