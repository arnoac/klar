# Klar in one page

For agents that already know TypeScript, Rust or Python: this page is enough to write Klar. It lists what
differs; everything else works the way you expect. `SPEC.md` is the full reference (search it when unsure),
`klar doc std.NAME` prints a library module's signatures, and `klar check` explains every error with a fix.

```klar
use std.process { process_exit }               // std functions must be imported by name
use parser { Expr, parse }                     // parser.klar next to this file; list every name you use

struct Item { name: str, cents: int, note: Option[str] }       // no classes; fields separated by commas
enum Shape { Circle(r: float), Rect(w: float, h: float), Empty } // variants are global names

fn tax_rate() -> int { return 21 }             // no globals or constants: use a zero-argument function

fn area(s: Shape) -> float {                   // parameter and return types always written
  return match s {                             // `return` is required; there is no implicit last value
    Circle(r) => 3.14159 * r * r,              // match must be exhaustive; `_` matches anything
    Rect(w, h) => w * h,
    Empty => 0.0,
  }
}

fn parse_cents(s: str) -> Result[int, str] {   // no exceptions: Result / Option, and `?` to propagate
  let n = parse_int(s) else { return Err("bad number: {s}") }  // unwrap Some/Ok, or leave
  if n < 0 { return Err("negative") }
  return Ok(n * 100)
}

fn main() uses io {                            // any function doing io (or calling one) says `uses io`
  var total = 0                                // `var` is reassignable, `let` is not
  var items: list[Item] = []                   // annotate empty [] and [:]
  while true {
    let raw = read_line() else { break }       // Option[str]; None at end of input
    let line = trim(raw)
    if line == "" { continue }
    match parse_cents(line) {                  // match when you need the error too
      Ok(c) => {
        total = total + c                      // no += or ++
        items = push(items, Item(name: line, cents: c, note: None))   // values are immutable: reassign
      }
      Err(e) => eprint("error: {e}"),          // "{expr}" interpolates; "{{" is a literal brace
    }
  }
  for it in sort_by(items, fn(i) => i.cents) { print("{it.name} {it.cents}") }
  print("total {total / 100}.{if total % 100 < 10 then "0" else ""}{total % 100}")
  if len(items) == 0 { process_exit(1) }       // exit status (else 0, or 1 after a runtime error)
}

test "parses cents" {                          // `klar test` runs these
  assert parse_cents("12") == Ok(1200)
}
```

## Rules the checker enforces

* **No null.** `Option[T]` is `Some(x)` / `None`; `Result[T, E]` is `Ok(x)` / `Err(e)`. `o?` / `r?` return early
  with None / the Err; `let x = o else { ... }` unwraps or runs a block that must return, break or continue.
  `unwrap_or(o, d)`; `get(xs, i)`, `get(m, k)` and `last(xs)` give Options; `xs[i]` and `m[k]` stop the
  program when missing.
* A `{ }` match arm gives no value: inside it, `return`/`break`/`continue` (an arm can also be one statement).
* **Immutable values.** `push`, `pop`, `set_at`, `put`, `remove`, `p with (x: 1)` return new values; reassign
  a `var`: `xs = push(xs, x)`, `m = put(m, k, v)`. This is O(1) when only that variable holds the value.
  Ignoring a call's non-unit result is an error.
* **No shadowing**: a name cannot be declared again while in scope (sibling blocks may reuse it), and a
  local cannot take the name of a top-level function or a built-in (`len`, `map`, `get`, `sum`, ...). Every
  top-level name is unique in the whole program.
* **`int` and `float` never mix**: `to_float(n)`, `floor(x)`, `round(x)`. `int` is arbitrary precision;
  `/` on ints truncates. Strings join only with `+` or interpolation; `str(x)` converts anything.
* **Operators**: `and`, `or`, `not` (no `&&`/`||`/`!`); `if c then a else b` is the expression form.
  Conditions must be `bool`.
* **Anonymous functions** `fn(x) => x * 2` or `fn(x: int) -> int { return x }` capture `let`s and
  parameters, not `var`s (copy first: `let n = count`). They cannot do io unless the expected type says so,
  so loop with `for`, not `map`, to print.
* `x.f(a)` is `f(x, a)`: `line.split(",").map(fn(w) => trim(w))`.
* Tuples: `(1, "a")`, `t.0`, `let (a, b) = t` (also with `else`), `(a, b) = t` for existing vars,
  `for (k, v) in entries(m)`. Enums may be recursive: `enum Expr { Num(n: int), Add(l: Expr, r: Expr) }`.
* Calls are all positional or all named. Structs/variants: `Item(name: "x", cents: 1, note: None)` or
  positional. No struct patterns; enum patterns are positional: `Rect(w, _)`.
* Raw strings `r"\d+"` and multi-line `"""..."""` (`r"""..."""` raw: use it for CSS or JSON text).

## Built-ins (no import)

io (`uses io`): `print(v)` `eprint(v)` `read_line() -> Option[str]` `read_all() -> str`
`read_file(p) -> Result[str, str]` `write_file(p, s) -> Result[unit, str]` `args() -> list[str]`.
Lists: `len push pop last set_at get slice contains reverse range(a, b) map filter fold(xs, init, fn(acc, x))
any all find index_of sort sort_by(xs, key) sum join(xs, sep)`. Strings: `len chars split trim upper lower
starts_with ends_with contains replace repeat ord chr slice reverse parse_int parse_float`; `s[i]` is a
1-char str. Maps (insertion-ordered): `len get has put remove keys values entries`. Numbers: `to_float floor
round sqrt abs min max`. Options/Results: `unwrap unwrap_or is_some is_none is_ok is_err ok_or`.
`str(v)`, `panic(msg)`. Exit status: 1 after a runtime error, `process_exit(n)` from `std.process`.

## Library (`klar doc std.NAME` for signatures)

`std.json` (`json_encode(v)` / `json_decode(text) -> Result[T, str]` for any type: structs are objects,
`Option` is null; `Json` enum and `json_parse` for untyped data) · `std.regex` · `std.fs` · `std.process` ·
`std.time` · `std.math` · `std.random` · `std.crypto` · `std.http` and `std.web` (servers) · `std.sqlite` ·
`std.api` (typed client/server calls) · `std.html`, `std.ui` (browser apps), `std.layout`, `std.kit` · `std.task`.

**Browser apps** (`std.ui`, model / update / view; built with `klar build --target web app.klar`):

```klar
use std.ui { Next, ui_run, ui_save, ui_load }
use std.html { Html, div, form, button, input, ul, li, text, id, value, input_type, on_submit, on_input }

struct Model { draft: str, items: list[str] }
enum Msg { Typed(text: str), Add }

fn update(m: Model, msg: Msg) -> Next[Model, Msg] {
  return match msg {
    Typed(t) => Next(m with (draft: t), []),
    Add => {
      let items = push(m.items, trim(m.draft))
      return Next(Model(draft: "", items: items), [ui_save("items", join(items, "\n"))])
    }
  }
}

fn view(m: Model) -> Html[Msg] {               // elements: tag(attrs, children); text is escaped
  return div([], [
    form([on_submit(Add)], [                   // Enter in an input and the submit button both send Add
      input([id("new"), value(m.draft), on_input(fn(t) => Typed(t))]),
      button([id("add"), input_type("submit")], [text("Add")]),
    ]),
    ul([id("list")], map(m.items, fn(s) => li([], [text(s)]))),
  ])
}

fn main() uses io {                            // load saved data here; save it from update
  let saved = match ui_load("items") { Some(t) => filter(split(t, "\n"), fn(s) => s != ""), None => [] }
  ui_run(Next(Model(draft: "", items: saved), []), update, view)
}
```

The page re-renders right after `update` returns. `value(v)` sets the input's text on every render. Events:
`on_click(msg)`, `on_input(fn(str) -> Msg)`, `on_submit(msg)` (no page reload), `on_change`, `on_check`,
`on_key(fn(key) -> Option[Msg])`. Attributes: `id class value placeholder input_type checked disabled attr(name, v)`.
