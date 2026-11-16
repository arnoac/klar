# Klar roadmap

Goal: a general-purpose language that an AI agent can learn from one page and use to build anything,
while keeping the agent-friendly core (one way to do things, immutable values, checked effects,
structured errors).

## Done

| Stage | What it adds | What it unlocks |
|---|---|---|
| v0.1 core | types, structs, enums, exhaustive match, Option/Result, effects, tests, coded errors | algorithms, data processing, CLI programs on stdin/stdout |
| 1. Modules and generics | `use` with explicit import lists, generic structs/enums, `uses io` on function types | multi-file programs, reusable libraries, callbacks and handlers |
| 2. Python interop | `extern` functions and types with type-directed conversion | any Python library: data science, ML, GUIs, games, cloud SDKs |
| 3. Standard library | fs, json, http (client and server), sqlite, time, process, math, random, regex; raw and multi-line strings | web APIs and sites, database apps, scripts and automation, CLI tools |
| 4. Example apps | todo REST API (SQLite + JSON + HTML), notes CLI, Python interop demo, all tested end to end | proof the pieces work together |
| 5. Native compiler | `klar build`: Klar to C, tcc for ~40 ms dev builds, gcc/clang -O2 for `--release`; monomorphised generics, O(1) amortised `push`/`put`, int64 overflow checks | CPU-bound programs at C/Go speed, single-file executables with no Python needed |
| 6. The web | JavaScript backend (Node and browser; io functions become async), `std.html` typed HTML with a default theme, `std.ui` model/update/view apps, `std.web` routing, forms, cookies, files; `js:` externs | full-stack web apps in one language, tested without a browser |
| 7. Faster lists, web polish (v0.5) | in-place `set_at`/`push` for lists only one `var` holds (nbody and fannkuch-redux went from 28x to under 3x C), automatic JSON for every type, `klar dev` (rebuild on save, browser reload, errors in the page), live updates (`web_events`/`web_publish`, `ui_listen`) | array-heavy code at near-C speed; full-stack apps with shared types and no hand-written JSON |
| 8. Traits and formatter (v0.6) | `trait`/`impl` with bounds (`fn f[T: Show + Shape]`), resolved at compile time in C and JavaScript; `klar fmt` (one canonical layout, token-verified) | shared behaviour across types without classes; agent diffs that show only real changes |
| 9. Packages (v0.7) | `klar.toml` with path and git dependencies (flattened, conflicts reported), `klar.lock` pinning commits, pinned Python packages for externs, `klar new/add/install/update`; a dependency's private functions never clash with your names | reusing Klar code across projects, reproducible builds, Python libraries without a global install |
| 10. Native standard library (v0.8) | every std module in `klar build` executables: files, processes, JSON (automatic too), regex (PCRE2), SQLite, HTTP client (HTTPS, proxies) and server with live updates; a program links only the libraries it uses | single-binary web servers and tools with no Python or Node; the todo API serves ~7,700 req/s natively vs ~4,800 on Node and ~280 in the interpreter |
| 11. Concurrency (v0.9) | `std.task`: `task_all`, task groups with `task_spawn`, channels; tasks never share variables, and if every task is waiting the program stops with E409 instead of hanging; the same programs give the same output in the interpreter, native builds (threads) and Node (promises); servers handle requests concurrently | parallel downloads and API calls, worker pools, pipelines, servers whose slow requests don't block fast ones |
| 12. Language server (v0.10) | `klar lsp`: diagnostics as you type with quick fixes from the hints, hover types and doc comments, go to definition, references and rename across files, completion (scope, fields, `x.` functions, imports), signature help, outline, formatting; a VS Code extension with highlighting, Neovim and Helix setup | Klar in any editor, with the same errors the compiler and agents see |
| 13. A real app (v0.11) | Klar Board (`examples/apps/board`): accounts, shared boards, live updates, a concurrent report tool, about 1,400 lines tested in all three backends and in the browser; `std.crypto` (hashes, tokens, passwords); fixes it called for: library names no longer leak between modules, one clear error for value-ending match blocks, a `klar fmt` indent fix, `web_clear_cookie`, `ui_unlisten` | real multi-user web apps with logins; evidence for what to build next |
| 14. Layout (v0.12) | `std.layout`: containers, rows, stacks, grids, sidebars and centring, tuned with typed options (`gap(Sm)`, `cols_at(Tablet, 3)`, `col_span`, `max_width(Narrow)`, `hide_from(Tablet)`); responsive from the phone up, a typo is a compile error, and the same HTML on every backend; Klar Board dropped its hand-written layout CSS | responsive pages and apps without writing CSS; the base the component kit builds on |
| 15. Components (v0.13) | `std.kit`: buttons with typed variants and sizes, fields with hints and problems, select, checkbox, switch, radio group, badges, alerts, toasts, dialogs, tabs, menus, panels, empty states, stats, avatars, breadcrumbs, progress, spinners and skeletons; a slim neutral theme (light and dark) restyled by one typed `kit_theme`; roles, labels, focus and keyboard handling built in; state stays in the app model; `on_key`; type parameters usable in local annotations | modern-looking apps with no CSS; Klar Board and the todo app use it |
| 16. Tables, combobox, drag and drop (v0.14) | `data_table` (sortable, paged, `aria-sort`), `combobox` (filter as you type, arrow keys, `aria-activedescendant`), dialogs that keep Tab inside and give focus back, `on_drag`/`on_drop`/`on_drag_end` with a drop highlight, `on_blur`, `on_press`; Klar Board cards move by dragging | the components most data apps need, with keyboard and screen reader support |
| 17. npm packages | typed `js:` externs for npm packages: constructors, methods, properties, callbacks both ways; null only as `None`, exceptions only as `Err`, Promises only with `uses io` (else E407); `[npm]` in klar.toml with exact versions, `npm ci` against package-lock.json, install scripts off; web builds embed the packages' ES modules (still one offline .html); `klar bind` generates declarations from `.d.ts` | the npm ecosystem from Klar without giving up its guarantees; `examples/apps/markdown_notes` (marked + DOMPurify) |
| 18. WebAssembly | `klar build --target wasm` and `extern "wasm:module.fn"` in web and Node apps: the C backend through clang's wasm32 target, with Klar's own small libc and a per-call heap; values are copied across as typed arrays, strings or JSON; runtime errors keep their codes; `klar run` interprets the module | heavy computation in the browser 1.1-5.7x faster than the JavaScript backend, on a par with hand-written JavaScript for float and allocation-heavy code |
| 19. Full-stack glue (v0.15) | typed calls (`std.api`: declare `Api[In, Out]` once, answer it with `api_serve`, call it with `api_call` or `api_fetch`), typed SQL rows (`sqlite_rows`/`sqlite_first` fill structs by column name and tuples by position, `sql(value)` for parameters), tuples with `let (a, b) = t` and tuple patterns, `?` that wraps an error into the function's error enum, `ok_or`, `entries` | full-stack apps with no routes, URLs or JSON and row conversion code; Klar Board lost 12% of its code (its server 35%) |
| 20. Production | `klar build --release` of a server and its browser apps: one executable with the apps and `public/` built in (gzip, ETags), `/healthz`, a request log, clean shutdown on SIGTERM, keep-alive, fair request order under load; SQLite in WAL mode with reused statements and `sqlite_migrate`; `klar build --static` for static hosts, `--docker` for containers; [DEPLOY.md](DEPLOY.md) | shipping an app is copying one file; Klar Board serves its page 5.7x faster than a hand-written Node server and its API at 70% of Node's rate ([bench/deploy](bench/deploy/README.md)) |

## Next

1. **More components.** Date picker, multi-select, a command palette, keyed list diffing for long tables, and
   touch drag and drop (HTML drag and drop does not fire on phones; the board keeps its arrow buttons there).
2. **More real apps** written in Klar end to end, to find what hurts at scale.
3. **Faster compiled code and builds.**
   * user C externs (`extern "c:..."`) for native libraries beyond the standard ones;
   * a precompiled runtime object so tcc doesn't re-parse the runtime on every build;
   * faster maps and strings (the `words` benchmark is about 2 times behind Go) and unchecked arithmetic
     where the checker can prove no overflow (recursive `fib` is about 2 times behind C);
   * WebAssembly: a garbage collector for long allocation-heavy calls (today each call gets a fresh heap, up
     to 4 GB), and closing the 1.7-1.9x gap to hand-written JavaScript on list-heavy kernels (bench/wasm).
4. **More for the web.** Keyed list diffing for long lists, websockets for two-way messages, and keeping the
   app's state across `klar dev` reloads.
5. **Security** (see [SECURITY.md](SECURITY.md)): an opt-in for packages that must run build scripts,
   rate limiting and login lockout helpers in `std.web`, a stricter per-app Content-Security-Policy, and
   detecting edits inside `.klar/deps`.
6. **GUI and games.** Browser apps work today through `std.ui` (and a canvas command is a small addition);
   desktop toolkits through Python `extern`.

## Known limits today

* Servers handle requests concurrently in every backend, but Klar code runs one task at a time, so a
  CPU-heavy handler still holds up the others (io waits do not). A runtime error in a handler answers that
  request with 500 in every backend (a stack overflow still ends a native server). Servers bind to 127.0.0.1
  unless HOST is set.
* The interpreter is about 20 to 100 times slower than Python; use `klar build` for speed. (`push` and `set_at`
  on a list `var` are O(1) there too.)
* Python externs run only in the interpreter, `js:` externs only in JavaScript builds.
* npm: `klar bind` cannot express unions of different types, intersections, `any`, tuples, rest parameters
  or built-ins like `Date` and `Uint8Array` (it lists what it skipped), and it marks every function
  `uses io` returning Result until you tighten it. Web builds need packages that ship ES modules (CommonJS-only
  packages work with `--target node`).
* No package registry yet: dependencies are git URLs or folders. Types and enum variants must be unique
  across all packages (functions you do not import are renamed automatically).
* Native tasks are OS threads (cheap while waiting, but thousands at once cost real memory); channels are
  unbounded, so a fast producer can outrun a slow consumer.
* No pattern guards and no destructuring of structs in patterns (tuples can be unpacked).
