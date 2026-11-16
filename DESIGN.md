# Why Klar is built for AI agents

Klar ("clear" in Dutch and German) starts from the parts of Rust, Python, Go and TypeScript that
language models already write fluently, and removes the features that cause them to write code that looks
right but is wrong. Each decision below targets a specific, recurring agent failure.

## 1. The whole language fits in one context window

`klar spec` prints the complete reference: about 2,000 words. An agent that has never seen Klar can read
it once and know every rule, keyword and built-in. There is nothing else to learn: no standard library
tour, no ecosystem conventions, no "advanced" features hiding behind the basics. A test in the suite keeps
the spec under 4,000 words so it stays that way.

## 2. One obvious way to do each thing

Agents trained on many languages blend their idioms. Klar removes the choice so the blend has nowhere to
go: one loop over collections (`for x in list`), one conditional statement, one conditional expression
(`if c then a else b`), one way to handle absence (`Option`), one way to handle failure (`Result` and `?`),
no exceptions, no null, no classes, no inheritance, no operator overloading, no macros. Shared behaviour is a `trait` with explicit impls.

Familiar spellings were kept wherever they are unambiguous (`fn`, `let`, `match`, `struct`, `enum`,
`list[int]`), so a model's prior knowledge mostly transfers. Where other languages disagree (`&&` vs
`and`, `+=`, `null`), Klar picks one and the compiler names the replacement when the other form appears.

## 3. Local reasoning: no hidden state, no action at a distance

* **All values are immutable.** There is no aliasing bug where changing `b` silently changes `a`.
  "Updating" means building a new value and reassigning a `var`, which is visible at the call site.
* **No shadowing.** A name means one thing for the whole function, so an agent editing line 80 doesn't
  need to know whether line 12 rebinds it.
* **Effects in signatures.** `uses io` marks every function that can do input or output, and the checker
  enforces it transitively. An agent can tell from the signature alone whether a function is safe to call
  in a test or to reorder.
* **Explicit types at function boundaries.** Parameters and return types are always written; local types
  are inferred. Reading one function's signature tells you how to call it.
* **Method-style calls are pure sugar.** `x.f(a)` is exactly `f(x, a)`, so there is no dispatch to
  look up.

## 4. A grammar that is hard to get wrong

* No significant indentation (whitespace errors are invisible in diffs and easy to break with edits).
  Blocks use braces.
* No semicolons required, and only one line-sensitivity rule (a call's `(` stays on the same line), which
  is enforced with its own error code instead of silently changing meaning.
* Capitalisation is meaningful and checked: `Point` is a type or variant, `point` is a value. This makes
  patterns unambiguous (`Circle(r)` matches a variant, `r` binds a value) without extra syntax.
* No precedence traps: comparisons don't chain, boolean operators are words, and there are no bitwise
  operators to confuse with logical ones.
* Maps use `[k: v]` and structs use `Point(x: 1)`, so `{` only ever opens a block. There is no
  "is this an object literal or a block?" ambiguity.

## 5. Mistakes are caught before running, and every error says how to fix it

The checker catches, before execution: unknown names (with "did you mean"), type mismatches, int/float
mixing, missing returns, non-exhaustive `match` (with the missing case spelled out, e.g.
`Some(Op(_))`), missing effect annotations, misuse of `?`, and ignored results of pure calls (the
classic `push(xs, 1)` that does nothing).

Every diagnostic has:

* a **stable code** (`E306`) an agent can key repair strategies on,
* an **exact location** (`line:col`),
* a **hint** written as the concrete fix ("add `uses io` to the signature: ..."),
* a **JSON form** (`--json`) so tools don't parse human prose,
* and the checker reports **all independent errors at once**, so one edit/compile round can fix many.

Habits from other languages get targeted hints: `x += 1`, `&&`, `null`, `String`, `List<int>`,
`xs.length`, `append`, `self`, and so on.

Runtime failures (index out of bounds, unwrap on `None`, failed `assert`) carry the same structure plus a
call trace, and failed assertions show both sides of the comparison.

## 6. Tests live next to the code

`test "name" { assert ... }` blocks sit in the same file and run with `klar test`, with JSON output. An
agent can write a function and its test in one edit and verify both in one command.

## 7. General purpose without a big language

Klar reaches real applications through three small mechanisms instead of a large standard library:

* **Modules with explicit import lists.** `use std.json { Json, json_parse }` names every imported item,
  and every top-level name is unique across the whole program. An agent never has to resolve which
  `parse` a call means: a text search finds the one definition.
* **Generics on structs, enums and functions**, inferred at use sites, so libraries can define containers
  like `Tree[T]` without casts.
* **Typed Python interop.** `extern "module.attr" fn name(...) -> T` puts a checked Klar signature on any
  Python function. Values are converted by the declared type, exceptions become `Err` values when the
  signature returns `Result[T, str]`, and a mismatch is a clear runtime error rather than silent
  corruption. This gives Klar the whole Python ecosystem (web, data, ML, GUI toolkits, games) on day one,
  while Klar code stays statically checked. The standard library itself is written this way: thin Klar
  modules over Python implementations.

Effects extend to function values: a handler typed `fn(HttpRequest) -> HttpResponse uses io` may do io; a
callback passed to `map` may not. Agents can still read a signature and know what it can touch.

## 8. Lessons from writing Klar with an agent

While building the example apps, the most frequent mistake (made three times by the agent writing them)
was putting a string literal inside an interpolation, such as `"{row["id"]}"`. Version 0.1 rejected this.
Version 0.2 accepts it, because a rule an agent keeps tripping over is a defect in the language, not in
the agent. Raw strings `r"..."` (for JSON) and multi-line strings (for HTML and SQL) were added for the
same reason. Likewise, a `match` arm that is a block ending in `return` no longer turns the whole match into
a statement.

## 9. Compiling: fast to build, fast to run

`klar build` translates the checked program to one self-contained C file and hands it to a C compiler:
**tcc** by default (it compiles C in milliseconds, so the edit-build-run loop stays instant) and
**gcc/clang -O2** with `--release` (mature optimisers, so the output runs at roughly native C speed).
Emitting C instead of writing a native backend gets both properties immediately and runs everywhere a C
compiler exists. The design choices that make the generated code fast:

* **Static types everywhere.** Every expression's type is known after checking, so ints and floats are
  plain C `int64_t`/`double` locals and arithmetic compiles to single instructions (plus an overflow check).
* **Monomorphisation.** Generic functions are compiled once per concrete type combination, so `==`,
  printing and sorting are direct calls with no runtime type tests.
* **Uniform 64-bit slots** for values inside lists, maps, structs and closures, so containers are simple
  arrays and generic code needs no boxing of numbers.
* **Immutable values with in-place fast paths.** `xs = push(xs, x)` and `m = put(m, k, v)` are O(1)
  amortised: lists and maps share an append-only buffer, and only the newest version appends in place, so
  older versions stay unchanged and the language keeps its "values never change" rule.
* **Closures capture values**, never variables (rule E311), so a closure is a function pointer plus a
  small array of copied values: no hidden shared mutable state for the compiler to preserve.
* **Garbage collection** uses the Boehm collector when installed (it is optional; without it, memory is
  never freed, which is fine for short-lived tools).

The interpreter remains the reference implementation and the only one that supports `extern`; a test
suite runs every example and a feature-coverage program through both and requires identical output and
error codes.

## 10. The web: one language for the page and the server

Agents write a large share of their code for the web, and the web is where they make the most invisible
mistakes: unescaped user input, client and server disagreeing on a shape, state mutated in an event handler
that something else still reads. Klar's web support is built from the same principles as the rest of the
language rather than bolted on.

* **HTML is a typed value, not a string.** `div([class("card")], [text(name)])` is checked like any other
  call, a misspelt element is a compile error with a "did you mean", and text is escaped when rendered.
  Injection needs the explicitly named `raw_html`. One shape (attributes list, children list) covers every
  element, so there is nothing to remember per tag. HTML-shaped syntax (JSX) was considered and rejected: it
  adds a second grammar with its own escaping rules inside the first.
* **The same view code runs on the server and in the browser.** `html_render` turns a view into a string for
  server-rendered pages; `std.ui` turns the same values into live DOM. Data types, JSON helpers and
  validation functions are shared by the backend and the frontend because both are Klar.
* **Frontends are model / update / view.** All state is one immutable model; every event is a message;
  `update` is a pure function returning the new model and a list of commands (HTTP, timers, storage,
  navigation). This is the architecture where Klar's immutability is a feature instead of a cost, and it
  makes the whole UI testable with `test` blocks: no browser, no mocks.
* **Backends are plain functions.** A handler is `fn(HttpRequest) -> HttpResponse uses io`; routes are a
  list of values; routing, 404/405, forms, cookies, redirects and static files are a few small functions.
  `web_handle(routes, web_request(...))` tests the whole server without a socket.
* **Effects decide what is asynchronous.** Compiled to JavaScript, every `uses io` function becomes `async`
  and every call to one is awaited; pure functions stay synchronous. Klar code never mentions promises or
  callbacks, yet a Node server handles requests concurrently and the browser never blocks.
* **Good-looking by default.** `html_page` and `ui_run` include a small theme (typography, forms, buttons,
  cards, layout helpers, dark mode) so an agent's first page already looks finished; CSS variables change it.
* **Standard-library names do not leak.** `std.html` uses plain element names (`header`, `text`, `input`).
  A standard-library name only counts in a module that imports it, so these never collide with user code.

`klar build --target web` writes one self-contained HTML file (it opens straight from disk);
`--target node` writes one ES module with the standard library (files, SQLite, HTTP server and client)
implemented on Node. Native, Node and interpreter runs of the same program are checked to give identical
output, including 64-bit integer semantics (JavaScript numbers fall back to BigInt above 2^53).

## 11. Immutable values, mutable speed (v0.5)

Immutability makes programs easy to reason about, but `xs = set_at(xs, i, v)` that copies the list turns an
O(n) array loop into O(n²), and the first benchmarks showed it (nbody 28x slower than C). Klar keeps the
value semantics and removes the cost in two layers:

* **The compiler tracks ownership of list variables.** A `var` holding a list has a hidden flag that is set
  while nothing else can see that list: after a list literal, `range()`, or the private copy the first
  `set_at` makes. While it is set, `set_at` and `push` on that variable write into the buffer. Reading
  `xs[i]` or `len(xs)` keeps the flag; every other use clears it first, so the value handed out is never
  written again. The rule is local to one function and needs no annotations, so it never changes what a
  program means, only how fast it runs.
* **The runtime makes the remaining cases cheap.** A `set_at` on a shared list updates the newest version of
  its buffer in place and logs the old value; an older version rebuilds itself from the log the first time it
  is read. This is the classic persistent-array trick, bounded so memory stays O(n).

An agent never has to think about it: the idiomatic loop is the fast loop.

**JSON from types, not by hand.** Hand-written conversion between structs and JSON is where client and
server drift apart. `json_encode` / `json_decode` are derived from the type at each call site (monomorphised
in compiled code, from the call's type arguments in the interpreter), so a struct shared by both sides is
the protocol. Decoding errors say where (`$.items[2].id`) because that is what an agent needs to fix the
caller. The mapping is the obvious one a JavaScript or Python developer would write, and every runtime
prints byte-identical JSON so tests written against one hold on the others.

**The inner loop is save, look.** `klar dev` rebuilds in tens of milliseconds, reloads open pages, and shows
compile errors in the page itself, so an agent driving a browser sees the same diagnostics as in the terminal.

## 12. Traits and one layout (v0.6)

**Traits are explicit and nominal.** A type has a trait's functions only if an `impl Trait for Type` block
says so, and there is one impl per trait and type in the whole program, so "which code runs for `show(x)`?"
always has a single answer an agent can find with a text search. A generic function must list the traits it
uses (`fn f[T: Show]`), so its signature says everything a caller has to provide; a missing impl is E312 at
the call, with a hint naming the impl to write. There is no inheritance, no default methods and no implicit
conversion. Trait functions are ordinary global names, so `show(x)` and `x.show()` both work, and importing
the trait imports them.

**Dispatch is free in compiled code.** Generics are monomorphised, so each call resolves to one impl at
compile time in C and JavaScript; only the interpreter looks at the value at run time.

**`klar fmt` keeps your line breaks and decides the rest.** Indentation comes from bracket depth, spacing
from fixed rules, and blank lines are capped at one. Keeping line breaks means the formatter never invents
a layout an agent did not write, and every run checks that the output lexes to exactly the same tokens, so
it cannot change what a program means. When every agent runs it, diffs show only real changes.

## 13. Packages without surprises (v0.7)

A dependency is a folder or a git repository with a `klar.toml`; there is no registry to trust or to be down.
`use textkit.case { ... }` names the dependency first, so a reader knows where every import comes from.
`klar.lock` pins each git dependency to a commit and loading checks the checkout against it, so a program
never silently runs code other than what was reviewed. Dependencies are flattened: if two packages ask for
the same name from different places, `klar install` stops and says which two, instead of loading both.
Python packages for externs are pinned to exact versions and installed into the project (`.klar/python`),
not into the machine. Klar's rule that every top-level name is unique program-wide would make libraries
collide on helper names, so a library function that nobody outside the library imports is renamed
internally when it clashes; what you import is still exactly one definition.

## 14. One program, one binary (v0.8)

Every standard module now compiles natively, so a Klar web server or tool can ship as one executable with no
Python or Node behind it. The C versions are written to give the same results as the interpreter and Node,
down to error texts and JSON number formatting, and tests run the same programs in all three and compare the
output. Automatic JSON uses small type descriptors the compiler generates per type, so one C encoder and decoder
serve every struct and enum. Regex uses PCRE2, which reads Python's syntax; SQLite and OpenSSL are linked only
when a program uses them. Raw strings keep unknown escapes (`r"\d+"`), so regular expressions read the same in
every language an agent knows.

## 15. Concurrency without races (v0.9)

Tasks are for waiting on many things at once: requests, files, processes, timers. `task_all` runs a list of
jobs and returns their results in order; `task_group` waits for every task spawned into it, so no task outlives
the code that started it; channels carry values between tasks. Closures capture values, not variables, so tasks
cannot share mutable state and there is nothing to race on. Only one task runs Klar code at a time (a baton
passed at io calls, like JavaScript's event loop), which keeps every backend deterministic enough that the same
test program prints the same lines in the interpreter, native builds and Node. When every task is waiting and
none can go on, the runtime stops with E409 and the line that waited, rather than hanging, because an agent
cannot debug a program that never returns. CPU parallelism is left for later: it needs the same model with
per-task heaps, not a different API.

## 16. The editor sees what the compiler sees (v0.10)

The language server is the checker with a recorder attached: while it checks, it notes every name it resolves,
with the type it found and where that name is declared. Hover, go to definition, references, rename and
completion are all lookups in that record, so they cannot disagree with `klar check`, and there is no second
type checker to keep in step. Because top-level names are unique program-wide, rename across files is a plain
list of edits with no shadowing to reason about. The hints the checker already writes for agents ("did you
mean", "add `use ...`", "add `uses io`") double as quick fixes. When a buffer stops parsing mid-edit, the last
good analysis keeps hover and completion working.

## 17. Build something real, then fix the language (v0.11)

Klar Board, a multi-user task board, was written to find what small examples hide. The type system and the
error messages held up: the server API worked on its first run, and the shared move rule meant the browser and
server always agreed. The real problems were missing pieces (password hashing, clearing a cookie, closing a
live stream) and one rule that did not scale: short `std.html` names, once imported anywhere, took that name
from the whole program. That rule is now per module for library names, which keeps "one definition per name"
for your own code without letting a library's vocabulary leak into it. A Rust habit (ending a block with a
value) now produces one error that names the fix, because agents will keep writing it. The rest of the
friction is listed in the app's README and drives the roadmap.

## 18. Layout as typed options (v0.12)

Layout is where generated UI code goes wrong most quietly: a misspelled CSS class does nothing and nobody is
told. `std.layout` keeps the familiar vocabulary (container, row, stack, grid) but every option is a function
over an enum, so `gap(Small)` fails to compile where `class("gap-small")` would fail silently. Options are
ordinary attributes, so they combine with classes and events and need no new syntax. Responsive options
(`cols_at(Tablet, 3)`) set CSS variables that the theme reads at each screen width, which keeps the stylesheet
fixed and small while allowing any column count, and the variables do not inherit, so a grid nested in a grid
starts fresh. Screens mean "this wide and up", so the natural order is phone first.

## 19. Components that hold no state (v0.13)

`std.kit` follows the same rule as the rest of std.ui: the model is the only state. A dialog is open because
the view passes `true`, a tab is selected because the view passes its id, and toasts are a list in the model.
That keeps every interaction testable with plain `test` blocks and leaves nothing to sync. Accessibility is put
in the signatures, so it cannot be forgotten: an icon button takes a label, a field takes a label and wires
`for`, `aria-describedby` and `aria-invalid` itself. The look is deliberately plain (neutral colours, thin
borders, one radius) and lives entirely in CSS variables, so `kit_theme(default_theme() with (accent: ...))`
restyles every component at once, and `kit_theme` picks the text colour on the accent for contrast, so a theme
is one colour, not a matched pair.

## What is not built yet

See [ROADMAP.md](ROADMAP.md).
