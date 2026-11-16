# Tutorial: a full-stack app

In this tutorial you build **Bookmarks**, a small web app where you save links. It has a browser front end, a
server, a SQLite database and tests, and it is all Klar: about 220 lines in four files, tests included. Along the way you meet
the parts of Klar that matter most for web apps: types shared by both ends, typed calls instead of URLs and
JSON, rows that come out of the database as records, and views that are plain, testable functions.

You need Klar installed (see [Get started](start.html)). The finished app is in the repository under
[site/tutorial](https://github.com/arnoac/klar/tree/main/site/tutorial); every code block on this page is
read from those files when the site is built, and their tests run in Klar's test suite.

Make a folder with four empty files:

```sh
mkdir bookmarks && cd bookmarks
touch api.klar store.klar server.klar app.klar
```

## 1. The contract: api.klar

Start with what the browser and the server say to each other. Both sides import this file, so if either
side sends or expects the wrong thing, the checker tells you before anything runs.

<!-- include site/tutorial/api.klar -->

A `struct` is a record with named, typed fields. `Api[In, Out]` is a typed call: `add_bookmark` takes a
`NewBookmark` and answers with a `Bookmark`. Behind it is an ordinary POST to `/api/add_bookmark` with JSON,
but you never write either: the JSON is derived from the types.

`check_bookmark` holds the rules. It returns a `Result`: `Ok` with the cleaned-up bookmark, or `Err` with a
message for a person. Klar has no exceptions and no null; a function that can fail says so in its type, and
the caller has to deal with both cases. Because the browser and the server both import it, the form can give
instant feedback with the same rule the server enforces.

The `test` block runs with `klar test api.klar`. It checks the rules, including that a `javascript:` link is
turned away.

## 2. Storage: store.klar

<!-- include site/tutorial/store.klar -->

`?` after a call that returns a `Result` means: if it failed, return that error from this function now;
otherwise give me the value. That is how errors travel in Klar, visibly but without ceremony.

`sqlite_rows` turns each row into a `Bookmark` by matching column names to field names, and the type it
fills comes from the function's return type. Queries take their values as parameters (`sql(b.title)`);
building SQL by gluing strings together is a compile error (E313), so SQL injection is ruled out by the
language, not by care.

Notice `uses io` on every function here. Functions that touch the outside world must say so, and a function
that does not say so cannot call them. You can tell from a signature alone what may have side effects.

The test uses an in-memory database: `klar test store.klar`.

## 3. The server: server.klar

<!-- include site/tutorial/server.klar -->

`api_serve` connects a typed call to a function. The checker knows that `add_bookmark` carries a
`NewBookmark`, so in `fn(req, b) => save(db, b)` it knows the type of `b` without you writing it.

`save` checks the bookmark with the shared rule. `Refused(400, problem)` answers the browser with status 400
and the message, which arrives on the other side as a typed error. A database failure (`?` on
`store_add`) becomes an internal error: it is logged on the server and answered as a plain 500, so no
details leak.

There is no list of URLs, no request parsing and no JSON code. Security headers, request size limits and
refusal of cross-site posts are on by default.

## 4. The browser app: app.klar

A Klar browser app is four things: a model (the state), messages (what can happen), `update` (how a message
changes the model) and `view` (what the model looks like). First the model and the messages:

<!-- include site/tutorial/app.klar#model -->

`init` is the starting state plus the commands to run right away: here, one call to load the saved
bookmarks. When the reply arrives, it comes back as the message `Loaded(reply)`.

Next, `update`. It is a pure function: it takes the model and a message, and returns the new model and any
commands to run.

<!-- include site/tutorial/app.klar#update -->

`match` must cover every case, so if you add a message and forget to handle it, the checker says which one
(E306). `m with (title: t)` is a copy of the model with one field changed: values in Klar never change in
place, which makes `update` easy to test and reason about.

Then the view: typed HTML built from `std.html` elements, `std.layout` containers and `std.kit` components.

<!-- include site/tutorial/app.klar#view -->

Text is always escaped, so a bookmark titled `<script>` shows as text. Links with `javascript:` addresses
are neutralised as well. `field` gives each input a label, and `icon_button` requires a label for screen
readers; accessibility is part of the components' signatures.

## 5. Test it

Because `update` and `view` are plain functions, the tests need no browser:

<!-- include site/tutorial/app.klar#tests -->

```sh
klar test                   # every test block in the folder
```

## 6. Run it

```sh
klar dev server.klar app.klar      # open http://127.0.0.1:8000
```

`klar dev` builds `app.klar` into a page, runs the server, and rebuilds and reloads the browser whenever you
save. Try saving a bookmark without a title, or with an `ftp://` address: the form refuses it at once, and
the server would refuse it too.

To ship it, build the two halves:

```sh
klar build app.klar                                    # app.html: the whole front end in one file
klar build --target node server.klar -o server.mjs     # the server, for Node.js 22+
node server.mjs                                        # http://127.0.0.1:8080
```

Or build the server as a native executable with `klar build server.klar`: one binary with SQLite built in.

## Where to go next

* Add live updates so every open tab sees new bookmarks: `web_events` and `web_publish` on the server,
  `ui_listen` in the app. The [todo example](https://github.com/arnoac/klar/tree/main/examples/apps/todo_web)
  does exactly that.
* Read the [language reference](reference.html) from start to end: it is about 4,000 words.
* Browse [std.kit](std/kit.html) for dialogs, tabs, tables, menus and toasts.
