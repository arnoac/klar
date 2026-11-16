# Klar Language Reference (v0.14)

Klar is a small, statically typed language designed so that an AI agent can learn it from this page, write
correct code first time, and fix mistakes from the compiler's structured feedback. Files end in `.klar`. This page is the complete language.

## 1. Program structure

A file contains only top-level declarations: `use`, `fn`, `struct`, `enum`, `trait`, `impl`, `extern`, `test`.
Order does not matter. Programs start at `fn main() uses io { ... }`.

```klar
fn main() uses io {
  print("Hello, Klar!")
}
```

Comments: `// to end of line`. There are no block comments. Statements end at the end of a line; `;` is
an optional separator. Whitespace and indentation are not significant, with one rule: the `(` of a call and
the `[` of an index must be on the same line as the expression before them.

## 2. Names

| Kind | Case | Examples |
|---|---|---|
| variables, parameters, functions, fields | lowercase first letter | `total`, `parse_line`, `x` |
| types, structs, enums, variants, type parameters | Uppercase first letter | `Point`, `Shape`, `Circle`, `T` |

There is **no shadowing**: a name declared in a function cannot be declared again anywhere in that
function (including inner blocks). There is **no overloading**: every function name is unique, and user
functions cannot reuse built-in names. Variant names are global and must be unique across all enums.

Every top-level name (function, type, variant) is unique across the **whole program**, including imported
modules, so a search finds exactly one definition. Standard library names carry a module prefix
(`json_parse`, `fs_read`), except `std.html` elements (`div`, `text`); a std name only counts in the modules
that import it, so your own `text` is fine elsewhere.

Keywords: `fn let var if then else while for in return break continue match struct enum uses true false and or not assert test with use extern type trait impl`.

## 3. Types

| Type | Literals / constructors |
|---|---|
| `int` (arbitrary precision) | `0`, `42`, `1_000_000` |
| `float` (64-bit) | `3.14`, `2.0`, `1e-9`, `6.02e23` (a digit is required on both sides of `.`) |
| `bool` | `true`, `false` |
| `str` (UTF-8, immutable) | `"text"`, `"total: {x + 1}"` |
| `unit` (no value) | `()` |
| `list[T]` | `[1, 2, 3]`, `[]` |
| `map[K, V]` (K is int, str or bool; insertion-ordered) | `["a": 1, "b": 2]`, `[:]` |
| `Option[T]` | `Some(x)`, `None` |
| `Result[T, E]` | `Ok(x)`, `Err(e)` |
| `fn(A, B) -> R` | `fn(a: int, b: int) => a + b` |
| user `struct` / `enum` | `Point(x: 1, y: 2)`, `Circle(radius: 1.0)` |

There is **no null**, no implicit conversion, and no subtyping. `int` and `float` never mix:
`1 + 2.0` is an error; write `to_float(1) + 2.0`. Strings only join with strings: use interpolation.

**All values are immutable.** `push`, `put`, `set_at` and `with` return a new value; to "update", reassign a
`var`: `xs = push(xs, 4)`. Equality (`==`) is structural for every type except functions.

String escapes: `\n \t \r \" \\ \0`. Interpolation: `{expr}` inside a string inserts `str(expr)` and may
contain any expression, including other strings: `"{m["key"]} {upper("x")}"`. Write `{{` and `}}` for
literal braces.

* **Raw strings** `r"..."` have no interpolation and keep unknown escapes: `r"{\"id\": 1}"`, `r"\d+"`.
* **Multi-line strings** `"""..."""` may span lines and interpolate. The first newline and common indentation
  are removed.
  `r"""..."""` is the raw multi-line form.

## 4. Declarations

```klar
struct Point { x: float, y: float }

enum Shape {
  Circle(center: Point, radius: float),
  Rect(corner: Point, width: float, height: float),
  Empty,
}

fn area(s: Shape) -> float {          // parameter and return types are always written
  return match s {
    Circle(_, r) => 3.14159 * r * r,
    Rect(_, w, h) => w * h,
    Empty => 0.0,
  }
}

fn first[T](xs: list[T]) -> Option[T] {   // generic function: type parameters in [ ]
  return get(xs, 0)
}

fn log(msg: str) uses io {             // no `->` means the return type is unit
  print("[log] {msg}")
}

test "area of a rectangle" {
  assert area(Rect(corner: Point(x: 0.0, y: 0.0), width: 2.0, height: 3.0)) == 6.0
}
```

* Functions are declared only at top level. Inside functions use anonymous functions.
* Structs and enums may be generic: `struct Pair[A, B] { first: A, second: B }`,
  `enum Tree[T] { Leaf, Node(left: Tree[T], value: T, right: Tree[T]) }`. Type arguments are inferred at
  construction (`Pair(first: 1, second: "a")` is a `Pair[int, str]`) and written in types (`Tree[int]`).
* Fields are separated by commas; a trailing comma is allowed.
* `test "name" { ... }` blocks are run by `klar test file.klar`. They may use io.

### Traits

A trait names functions that several types provide. Each type opts in with an explicit `impl`:

```
trait Shape { fn area(self: Self) -> float }
impl Shape for Rect { fn area(self: Rect) -> float { return self.w * self.h } }
impl[T: Show] Show for list[T] { fn show(self: list[T]) -> str { ... } }
fn total[T: Shape](xs: list[T]) -> float { ... }     // bounds: T: Shape + Show
```

* The first parameter of every trait function is `self: Self`. Call it like any function: `area(r)` or `r.area()`.
* A generic function may call trait functions on `T` only if it lists the bound; callers must pass a type
  with an impl (E312). One impl per trait and type, anywhere in the program.
* Importing a trait (`use shapes { Shape }`) imports its functions.

### Effects

A function that performs input/output must say `uses io` after its return type. This includes calling any
function that `uses io`. The checker enforces this (E308), so a signature without `uses io` is guaranteed
to have no side effects.

Function types carry the effect too: `fn(Request) -> Response uses io` may do io and is callable only from
io code. A pure function may be passed where an io one is expected, not the reverse. An anonymous function may
do io only if its expected type allows it, or (bound with `let`) if the enclosing function `uses io`; so
`map(xs, fn(x) => print(x))` is an error: use `for`. To make a function returning a function io, parenthesise:
`fn make() -> (fn(int) -> int) uses io`.

### Modules

Each file is a module. `use` imports named items from another file; every name you use from another
module must be listed (an enum brings its variants along):

```klar
use geometry.shapes { Shape, Circle, area }   // loads geometry/shapes.klar next to this file
use std.json { Json, json_parse }             // standard library module
```

There is no `pub`: everything top-level can be imported. Tests in imported modules are not run.

### Packages

A package is a folder with `klar.toml` (`klar new NAME` makes one). Dependencies are other packages:

```toml
[dependencies]
geometry = { path = "../geometry" }
textkit = { git = "https://github.com/someone/textkit", tag = "v1.2.0" }   # or rev = / branch =
[python]
requests = "2.32.3"                                                        # for extern functions
[npm]
marked = "18.2.0"
```

`use textkit { shout }` loads the dependency's `lib.klar`; `use textkit.case { ... }` its `case.klar`.
`klar add NAME --path DIR | --git URL [--tag T]` (or `klar add --python NAME==VERSION`) edits `klar.toml` and
installs; `klar install` fetches into `.klar/`; `klar.lock` pins each dependency by commit or hash (commit it);
`klar update` moves pins forward. Unimported dependency functions never clash with yours;
types and variants must be unique. Package errors are E601.

### Python interop

`extern` gives any Python function a checked Klar signature. The target is `"module.attribute"`; a `.py`
file next to the `.klar` file is found automatically. `extern type` declares an opaque Python object type.

```klar
extern "statistics.median" fn median(xs: list[float]) -> float
extern "mylib.load_model" fn load_model(path: str) -> Result[Model, str] uses io
extern type Model
```

Values convert by type: scalars as is, `unit`/`None` = None, list = list, map = dict, struct = dict (or
object with those attributes), variant = `("Variant", field1, ...)` or `"Variant"`, function = callable,
extern type = the object. With a `Result[T, str]` result a Python exception becomes `Err("Type: message")`,
else E407 (also for a value of the wrong type). Declare `uses io` for side effects: Klar trusts the signature.

### JavaScript interop and npm

JavaScript builds call `"js:..."` targets: `"js:node:crypto.randomUUID"`, `"js:globalThis.Math.hypot"`, npm
packages (`"js:marked.parse"`, `"js:new pkg.Class"`), first-argument members (`"js:.add"`, `"js:get .size"`,
`set`). Values convert by type; null arrives only as `None`, a throw only as `Err` (if declared
`Result[T, str]`), a Promise only with `uses io`, else E407. `klar add --npm marked` pins packages in
`[npm]`; `klar bind marked` writes `npm/marked.klar` from `.d.ts` types. `"wasm:mod.f"` runs `f` from
`mod.klar` as WebAssembly (values copied).

## 5. Statements

```
let name = expr                 // immutable binding
let name: Type = expr           // with annotation (needed for empty [] / [:] when not inferable)
var count = 0                   // mutable binding
count = count + 1               // reassignment (only `var`; no += or ++)
let _ = expr                    // evaluate and discard on purpose

if cond { ... } else if cond { ... } else { ... }
while cond { ... }
for x in list_expr { ... }       // iterates a list; use range(0, n), keys(m), chars(s)
break
continue
return expr                      // `return` alone in unit functions
assert cond                      // fails the test/program with both sides shown
assert cond, "message"
expr                             // only calls (and match) may stand alone as statements
```

Conditions must be `bool` (no truthiness). A call whose non-unit result is ignored is an error (E301):
this catches `push(xs, 1)` written instead of `xs = push(xs, 1)`.

A function with a non-unit return type must `return` on every path (E305).

**Lists in loops.** Values never change, but `xs = set_at(xs, i, v)` and `xs = push(xs, v)` on a `var` cost
O(1): while only that variable holds the list, compiled code writes in place. Reading `xs[i]` and `len(xs)`
keeps that; passing, storing or iterating `xs` hands out a snapshot. Keep a hot loop's arrays in local
`var`s (not struct fields) and array code runs within 2-3x of C.

## 6. Expressions

Precedence, lowest to highest:

1. `or`
2. `and`
3. `not`
4. `==  !=  <  <=  >  >=` (do not chain: `a < b < c` is an error)
5. `+  -`
6. `*  /  %`
7. unary `-`
8. postfix: call `f(x)`, index `xs[i]`, field `p.x`, method-style call `x.f(y)`, `?`, `with`

* `+` works on int, float, str and lists (concatenation). `-  *  /` on int and float. `%` on int.
* Integer `/` truncates toward zero (`-7 / 2 == -3`), `%` takes the left side's sign; dividing by integer
  zero is a runtime error. Float division follows IEEE.
* `<  <=  >  >=` work on int, float and str.
* `and`, `or` short-circuit; `not` negates.

**If-expression:** `if cond then a else b` (both branches required, same type).

**Match** (an expression; must be exhaustive, E306):

```klar
let label = match shape {
  Circle(_, r) => "circle {r}",
  Rect(_, w, h) => if w == h then "square" else "rect",
  Empty => "empty",
}
match result {
  Ok(v) => { print("got {v}") }        // a block arm gives no value (its last line is not a result)
  Err(e) => { print("failed: {e}") }
}
return match route {
  "GET /" => Ok(home()),
  "POST /items" => {                   // a block arm that always returns doesn't affect the type
    if bad { return Err("bad") }
    return Ok(created())
  }
  _ => Ok(not_found()),
}
match read_line() {
  Some(l) => count = count + 1,        // `return`, `break`, `continue` and assignments work as arm bodies
  None => break,
}
```

Patterns: `_`, a lowercase name (binds the value), literals (`0`, `-1`, `"s"`, `true`), and variants with
positional sub-patterns `Some(Circle(_, r))`, tried top to bottom. No guards (use `if` in the arm) and no
struct destructuring.

**Calls:** arguments are all positional `f(1, 2)` or all named in any order `f(b: 2, a: 1)`. Structs and
variants are constructed the same way: `Point(x: 1.0, y: 2.0)` or `Point(1.0, 2.0)`.

**Method-style calls:** `x.f(a, b)` means exactly `f(x, a, b)`. Use it for chaining:
`text.split(" ").map(fn(w) => upper(w)).join("-")`.

**Struct update:** `p with (x: 5.0)` returns a copy of `p` with the given fields replaced.

**Indexing:** `xs[i]` (list, int index), `s[i]` (str, returns a 1-char str), `m[k]` (map). Out-of-range or
missing keys are runtime errors; use `get(xs, i)` / `get(m, k)` for an `Option`.

**`?` operator:** `o?` unwraps `Some(v)` or returns `None` (the function must return an Option); `r?` unwraps
`Ok(v)` or returns the `Err(e)` (the function returns a Result with the same error type). Not allowed in
anonymous functions.

**Anonymous functions:**

```klar
let double = fn(x: int) => x * 2                  // expression body
let inc = fn(x) => x + 1                          // parameter types inferred where possible
let clamp = fn(x: int) -> int {                   // block body uses return
  if x < 0 { return 0 }
  return x
}
```

They capture the *values* of `let` bindings and parameters from the enclosing scope. Using a `var`
from outside is an error (E311): copy it first with `let now = x`. Built-in functions with several overloads (marked * in
section 7) cannot be passed as values directly; wrap them: `fn(x) => str(x)`.

## 7. Built-in functions

IO (require `uses io`):
`print(value)` (any value, adds newline) · `read_line() -> Option[str]` (None at end of input) ·
`read_file(path: str) -> Result[str, str]` · `write_file(path: str, content: str) -> Result[unit, str]` ·
`args() -> list[str]`

General: `str(value) -> str`\* · `panic(message: str) -> never`

Lists: `len(xs)`\* · `push(xs, x)` · `set_at(xs, i, x)` · `get(xs, i) -> Option`\* ·
`slice(xs, start, end)`\* (end exclusive, clamped) · `contains(xs, x)`\* · `reverse(xs)`\* ·
`range(start, end) -> list[int]` (end exclusive) · `map(xs, f)` · `filter(xs, f)` · `fold(xs, init, f)`
(f takes `(acc, x)`) · `any(xs, f)` · `all(xs, f)` · `find(xs, f) -> Option` · `index_of(xs, x) -> Option[int]` ·
`sort(xs)`\* (int, float or str elements) · `sort_by(xs, key_fn)` (stable) · `sum(xs)`\* · `join(xs: list[str], sep)`

Strings: `len(s)`\* · `chars(s) -> list[str]` · `split(s, sep) -> list[str]` · `trim(s)` · `upper(s)` ·
`lower(s)` · `starts_with(s, p)` · `ends_with(s, p)` · `contains(s, sub)`\* · `replace(s, old, new)` ·
`repeat(s, n)` · `slice(s, start, end)`\* · `reverse(s)`\* · `parse_int(s) -> Option[int]` ·
`parse_float(s) -> Option[float]`

Numbers: `to_float(n: int) -> float` · `floor(x) -> int` · `round(x) -> int` (half up) · `sqrt(x)` ·
`abs(x)`\* · `min(a, b)`\* · `max(a, b)`\*

Maps: `len(m)`\* · `get(m, k) -> Option`\* · `has(m, k) -> bool` · `put(m, k, v)` · `remove(m, k)` ·
`keys(m) -> list` · `values(m) -> list`

Option/Result: `unwrap(o)`\* (runtime error on None/Err) · `unwrap_or(o, default)`\* · `is_some(o)` ·
`is_none(o)` · `is_ok(r)` · `is_err(r)`

### Standard library

Import with `use std.NAME { ... }`. Full signatures are in each module's source (`klar/std/NAME.klar`).

| Module | Contents |
|---|---|
| `std.fs` | `fs_read`, `fs_write`, `fs_append`, `fs_exists`, `fs_is_dir`, `fs_list`, `fs_mkdir`, `fs_remove`, `fs_join` |
| `std.json` | `json_encode(value)`, `json_encode_pretty`, `json_decode(text) -> Result[T, str]` for any type (see Web apps); `enum Json { JNull, JBool, JNum, JStr, JArr, JObj }`, `json_of(value) -> Json`, `json_into(json) -> Result[T, str]`, `json_parse`, `json_stringify`, `json_pretty`, `json_get`, `json_at`, `json_str`, `json_num`, `json_int`, `json_bool`, `json_list` |
| `std.http` | `HttpRequest`, `HttpResponse`, `http_get`, `http_request`, `http_post_json`, `http_serve(port, handler)`, `http_text`, `http_html`, `http_json` |
| `std.sqlite` | `Database`, `enum SqlValue { SqlNull, SqlInt, SqlFloat, SqlText }`, `sqlite_open/exec/query/last_id/close`, `sql_int/text/float` |
| `std.crypto` | `crypto_sha256`, `crypto_hmac`, `crypto_token(bytes)` (secure random hex), `crypto_password_hash`, `crypto_password_verify`, `crypto_equal` (servers only) |
| `std.time` | `time_now`, `time_millis`, `time_sleep`, `time_format`, `time_iso` |
| `std.process` | `ProcessResult`, `process_run`, `process_run_input`, `env_get`, `process_exit` |
| `std.math` | `math_pi/sin/cos/tan/atan2/exp/ln/log10/pow` |
| `std.random` | `random_seed`, `random_int`, `random_float`, `random_pick`, `random_shuffle` |
| `std.regex` | `regex_matches`, `regex_find_all`, `regex_groups`, `regex_replace`, `regex_split` |
| `std.html` | `Html[M]`, `Attr[M]`; every common HTML element by its tag name (`div`, `ul`, `input`, ...), `el(tag, ...)`, `text`, `raw_html`, `nothing`, `card`; attributes `class/class_if/id/href/src/style/value/placeholder/input_type/checked/disabled/attr/flag`; events `on_click/on_submit/on_input/on_change/on_check/on_blur/on_press`, `on_key(fn(key) -> Option[Msg])`, drag and drop `on_drag/on_drop/on_drag_end(msg)`; `html_render/html_page/html_escape/html_theme_css` |
| `std.layout` | `container/row/stack/grid/spread/center/box/spacer`, `sidebar(attrs, side, main)`; options `gap/pad` (`Zero/Xs/Sm/Md/Lg/Xl`), `cols/cols_at/fit_cols/col_span(_at)/full_row`, `align/justify` (`Start/Center/End/Stretch/Between`), `max_width` (`Narrow/Readable/Wide/Full/Rem(n)`), `centered/grow/row_at/hide_from/show_from`; screens `Phone/Tablet/Laptop/Desktop` |
| `std.kit` | components `icon_button`, `menu/menu_item/menu_danger/menu_link`, `field/field_note(label, control, Hint(t)/Problem(t))`, `select_box/checkbox/toggle/radio_group` (`Choice`), `badge/alert/alert_dismissable/toasts` (`Tone`: `Neutral/Info/Success/Warning/Danger`), `dialog(open, title, text, close, body, actions)`, `tabs(attrs, current, [Tab(id, label, body)], pick)`, `data_table(attrs, [TableColumn(id, label, cell, ByText(f)/ByNumber(f)/Unsorted)], rows, state, page_size, change)`, `combobox(label, state, choices, change, pick)`, `panel/empty_state/stat/avatar/breadcrumbs/progress_bar/spinner/skeleton/kbd/separator`; options `variant` (`Primary/Secondary/Outline/Ghost/Destructive/Link`), `size(Small/Large)`, `busy/tooltip`; `kit_theme(default_theme() with (accent: "#2563eb", corners: Round, scheme: Dark))` |
| `std.web` | `Route`, `web_get/post/put/patch/delete(pattern, handler)`, `web_serve(port, routes)`, `web_port(fallback)`, `web_handle`, `web_request`, `web_body(req) -> Result[T, str]`, `web_json(status, value)`, `web_form/json_body/cookies/page/redirect/set_cookie/clear_cookie/file/static`, `web_events(topic)`, `web_publish(topic, text)` |
| `std.task` | `task_all(jobs)` (concurrent, results in order); `task_group(fn(g) { task_spawn(g, job) })` waits for its tasks; `Chan[T]`: `chan_new`, `chan_send`, `chan_recv -> Option[T]` (None once closed and empty), `chan_close`. One task runs Klar code at a time; others wait on io |
| `std.ui` | `Next[M, Msg]`, `Cmd[Msg]`, `ui_run`, `ui_run_routed`, `ui_start` (adds CSS), `ui_get/ui_send/ui_after/ui_save/ui_load/ui_go/ui_focus`, `ui_listen(url, to_msg)`, `ui_unlisten(url)` |

Operations that can fail return `Result[T, str]`.

### Web apps

**Pages are values.** `std.html` element functions take attributes and children:
`div([class("card")], [h2([], [text("Hi {name}")])])`. Text is escaped (except `raw_html`).
`Html[M]` is parameterised by the message type its events produce; server pages use `Html[unit]`.
`html_page(title, css, body)` makes a full document with the built-in theme (light and dark);
restyle it with `kit_theme(...)` or CSS variables (`--accent`, `--radius`, `--font`).
**Layout:** options are attributes; phone first, then wider screens:
`grid([cols(1), cols_at(Tablet, 3), gap(Sm)], tiles)`, `container([max_width(Narrow)], ...)`.

**Backends.** A handler is `fn(HttpRequest) -> HttpResponse uses io`. Routes match paths with `:name`
parameters and a final `*`, read through `req.params["name"]`:

```klar
let routes = [web_get("/", home), web_get("/items/:id", show_item), web_post("/items", create),
              web_get("/assets/*", web_static("public"))]
web_serve(8080, routes)   // 404 / 405 automatically; HOST=0.0.0.0 to listen beyond localhost
```

`web_page(title, html)` answers with a themed page; `web_form(req)`, `web_body(req)` and
`web_cookies(req)` read requests; `web_redirect`, `web_set_cookie`, `web_clear_cookie`, `web_json` and `web_file` answer.
Test handlers by calling `web_handle(routes, web_request("GET", "/items/3", ""))`. Listen on
`web_port(8080)`, which reads PORT (set by `klar dev` and hosting platforms).

**Safe by default:** cross-site POST/PUT/PATCH/DELETE get 403 unless `web_cross_origin(route)`; `web_redirect`
stays on-site.

**JSON is automatic.** `json_encode(value)` and `json_decode(text)` work for every type; the type to read
comes from where the result goes. Share one module of types between the frontend and the backend:

```klar
struct Todo { id: int, title: str, done: bool, note: Option[str] }
let r: Result[Todo, str] = json_decode(text)    // Err("expected a whole number at $.id, found a string")
web_json(201, todo)                             // {"id":1,"title":"Buy milk","done":false,"note":null}
let body: Result[NewTodo, str] = web_body(req)  // the request body, read as any type
```

Structs are objects (unknown keys ignored; a missing `Option` field is `None`), `map[str, V]` is an object
(other keys: `[key, value]` pairs), `Option` is `null` or the value, `Result` is `{"ok": ...}`/`{"err": ...}`,
enums are `"Variant"` or `{"type": "Variant", field: ...}`. Reading needs
a known type (else E304). `json_of` / `json_into` work with `Json` values.

**Live updates.** A route answering `web_events("todos")` keeps the connection open (server-sent events);
`web_publish("todos", text)` sends `text` to every browser on it. In the browser, the command
`ui_listen("/live", fn(text) => Synced(text))` turns each message into a `Msg`, and reconnects by itself.

**Frontends** follow the model / update / view pattern and run in the browser:

```klar
struct Model { count: int }
enum Msg { Inc, Loaded(reply: Result[HttpResponse, str]) }
fn update(m: Model, msg: Msg) -> Next[Model, Msg] {
  return match msg {
    Inc => Next(Model(m.count + 1), []),
    Loaded(_) => Next(m, []),
  }
}
fn view(m: Model) -> Html[Msg] {
  return button([on_click(Inc)], [text("clicked {m.count} times")])
}
fn main() uses io { ui_run(Next(Model(0), [ui_get("/api/start", fn(r) => Loaded(r))]), update, view) }
```

`update` and `view` are pure, so `test` blocks cover them without a browser (`html_render` renders a view).
Side effects are commands in `Next`: HTTP (`ui_get`, `ui_send`), timers (`ui_after`),
storage (`ui_save`; read back with `ui_load` in `main`), navigation (`ui_go`), focus and live updates
(`ui_listen`). `ui_run_routed` adds an `on_url: fn(str) -> Msg` called on start-up, link clicks and
back/forward.

**Develop with `klar dev`.** `klar dev server.klar app.klar` serves the backend (on Node) and the app at
http://127.0.0.1:8000, rebuilding and reloading on save.

## 8. Diagnostics

Every error has a stable code, an exact `line:col`, a message, and usually a `hint` with the fix. The
checker reports all independent errors in one run. `klar check --json file.klar` emits:

```json
{"ok": false, "errors": [{"code": "E201", "phase": "check", "title": "unknown name",
  "message": "unknown function `lenght`", "file": "a.klar", "line": 3, "col": 11,
  "hint": "did you mean `len`?"}]}
```

| Code | Meaning | Code | Meaning |
|---|---|---|---|
| E101 | unexpected character | E301 | type mismatch |
| E102 | unterminated string | E302 | operator type error |
| E103 | unexpected token | E303 | not callable |
| E104 | bad interpolation | E304 | cannot infer type (annotate) |
| E105 | call/index on new line | E305 | missing return |
| E201 | unknown name | E306 | non-exhaustive match |
| E202 | duplicate name / shadowing | E307 | invalid `?` |
| E203 | assign to `let` | E308 | missing `uses io` |
| E204 | unknown field | E309 | break/continue outside loop |
| E205 | wrong argument count | E310 | pattern does not fit type |
| E311 | anonymous fn uses an outer `var` | E408 | integer overflow (compiled code) |
| E501 | not supported by this `klar build` target | E312 | trait not implemented |
| E601 | klar.toml or a dependency is missing or invalid | E409 | deadlock: every task waiting |
| E206 | bad named arguments | E401–E407 | runtime: index, div by zero, panic, assert, missing key, stack overflow, io/interop |
| E313 | SQL built from strings | | |

`klar explain E306` explains any code.

## 9. Tooling

```
klar run file.klar [args...]     check, then run main() in the interpreter
klar build file.klar [-o exe]    native executable
klar build --release file.klar   gcc/clang -O2 (fastest code)
klar build --target node f.klar  Node.js module: node f.mjs
klar build --target web f.klar   self-contained web page: f.html
klar build --target wasm f.klar  WebAssembly: f.wasm, f.mjs
klar dev server.klar app.klar    serve a backend and/or app, reloading on save (--port N)
klar check file.klar [--json]    type-check only
klar test file.klar [--json]     run every `test` block
klar fmt file.klar dir/          format in place (--check: list unformatted files)
klar new|add|install|update      packages (see Packages)
klar audit                       check dependencies for known vulnerabilities
klar lsp                         language server for editors (see editors/README.md)
klar spec                        print this reference
klar explain CODE                explain an error code
```

Compiled programs match the interpreter except: `int` is 64-bit and
overflow is E408, and natively `upper`/`lower`/`trim` change only ASCII. Native and Node builds support every
std module (native regex and sqlite need the PCRE2 and SQLite libraries). The browser has no files, processes,
databases or servers (E501), and `print` goes to the console. Python externs are interpreter-only, `js:` ones
JavaScript-only.

## 10. Coming from another language

| You might write | In Klar |
|---|---|
| `x += 1`, `i++` | `x = x + 1` (x must be `var`) |
| `&&`, `\|\|`, `!x` | `and`, `or`, `not x` |
| `null`, `nil` | `Option[T]`: `Some(x)` / `None` |
| `throw` / `try` / `except` | return `Result[T, E]`; propagate with `?` |
| `xs.append(4)` | `xs = push(xs, 4)` |
| `for i in 0..n` | `for i in range(0, n)` |
| `for k, v in m` | `for k in keys(m) { let v = m[k] }` |
| `"a" + 1`, f-strings | `"a{1}"` |
| `from y import x` | `use y { x }` |
| `class` with methods | `struct` + functions, called as `p.area()` |
| `interface`, Rust `trait` | `trait` + `impl Trait for Type` |
| `switch`; Rust `Some(x) => { ...; x }` | `match`; inside `{ }` arms write `return x` |
| `p.x = 5` | `p = p with (x: 5)` (p must be `var`) |
