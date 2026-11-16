<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo/logo-dark.svg">
    <img src="assets/logo/logo-light.svg" alt="Klar" height="56">
  </picture>
</h1>

A statically typed, general-purpose programming language designed to be easy for AI agents to learn, read,
write and repair. The complete language reference is [klar/SPEC.md](klar/SPEC.md) (about 4,000 words); the
reasoning behind each design choice is in [DESIGN.md](DESIGN.md), and what comes next is in
[ROADMAP.md](ROADMAP.md).

Whether agents really make fewer mistakes in Klar is measured in [eval/](eval/README.md): the same tasks with
hidden tests, given to an AI agent in Klar and in TypeScript. The first pilot's results, including where Klar
loses, are in [eval/RESULTS.md](eval/RESULTS.md).

Klar has modules, generics, checked side effects, a standard library (files, JSON, HTTP client and
server, SQLite, time, processes, math, random, regex, tasks and channels, crypto) and typed interop with any Python library.

Klar is built for the web as well: HTML pages are typed, auto-escaped values (`std.html`) laid out with typed, responsive containers and grids (`std.layout`) and built from accessible components (`std.kit`), browser apps use
model / update / view (`std.ui`), and backends are routes over plain functions (`std.web`). The same code
renders on the server and in the browser, and it all ships with a theme that looks good by default.
`klar build --target web` makes a self-contained page; `--target node` makes a Node.js server. JSON is
derived from your types (`json_encode`, `json_decode`, `web_json`, `web_body`), servers push live updates
to browsers (`web_publish`, `ui_listen`), and `klar dev` rebuilds and reloads the browser on every save.
Heavy computation in a web app can run as WebAssembly: `extern "wasm:fractal.render" fn render(...)` compiles
`fractal.klar` to a module embedded in the page, 1.1-5.7x faster than the JavaScript backend in Chromium and on a
par with hand-written JavaScript on half the kernels measured ([bench/wasm](bench/wasm/README.md)).

`klar build` compiles a program to a native executable through C: a dev build takes about 40 ms (tcc), and
`--release` (gcc -O2) produces code that runs within 1-3x of C and Go on the Computer Language Benchmarks Game
(and beats Go on two of six). Numbers against C, Go and Python are in [bench/RESULTS.md](bench/RESULTS.md) and
[bench/benchmarksgame](bench/benchmarksgame/README.md).

```klar
enum Shape {
  Circle(radius: float),
  Rect(width: float, height: float),
}

fn area(s: Shape) -> float {
  return match s {
    Circle(r) => 3.14159 * r * r,
    Rect(w, h) => w * h,
  }
}

fn main() uses io {
  let shapes = [Circle(1.0), Rect(width: 2.0, height: 3.0)]
  for s in shapes {
    print("area: {area(s)}")
  }
}

test "rectangle area" {
  assert area(Rect(2.0, 3.0)) == 6.0
}
```

## Getting started

You need Python 3.10+ and git. Install the `klar` command, make a project and run it:

```sh
git clone https://github.com/arnoac/klar && cd klar
pipx install .              # or: ln -s "$PWD/klarc" ~/.local/bin/klar   (no install at all)

klar new hello && cd hello
klar run                    # Hello, world!
klar test                   # runs the test in main.klar
klar build && ./hello       # a native executable
```

For a browser app, `klar new shop --web`, then `klar dev` in that folder and open http://127.0.0.1:8000: the page
reloads every time you save. `klar build` turns it into one self-contained `shop.html`.

Every command works on the project you are in, so you rarely name a file. `klar help` lists them all and
`klar help COMMAND` shows the options with examples:

```
klar new NAME [--web]       create a project               klar add SOURCE        add a dependency
klar run [FILE] [ARGS]      check and run                  klar remove NAME       remove one
klar dev [FILE...]          serve, reload on save          klar install           fetch the pinned dependencies
klar test [FILE]            run test blocks                klar update [NAME]     move git dependencies forward
klar check [FILE]           type-check (--json)            klar spec              the language reference
klar fmt [PATH]             format in place                klar explain E306      explain an error code
klar build [FILE]           compile                        klar lsp               language server for editors
```

`klar add` takes one source and says what it is: `npm:marked`, `py:requests==2.32.3`,
`https://github.com/someone/textkit@v1.2.0` or `../geometry`. Versions are always pinned and npm install
scripts never run. A mistyped command answers with the right one (`klar compile` says `klar build`).

`klar build` also needs a C compiler (tcc for dev builds, falling back to cc; gcc or clang for `--release`); the
Boehm GC (`libgc-dev`) is used when installed. JavaScript builds need Node.js 22+ to run on a server (any
modern browser for web pages).

```sh
klar run examples/shapes.klar           # any file runs directly
klar build --release prog.klar          # gcc/clang -O2: fast code
klar build --target node server.klar    # Node.js module: node server.mjs
klar build --target wasm prog.klar      # WebAssembly (clang + lld): node prog.mjs
klar check --json some_file.klar        # machine-readable diagnostics
echo "2 * (3 + 4)" | klar run examples/calc.klar

PORT=8080 klar run examples/apps/todo_api      # JSON API + HTML page on SQLite (a folder runs its main.klar)
klar run examples/apps/notes_cli add "buy milk"  # CLI tool storing JSON
klar run examples/apps/py_interop              # calling Python libraries
klar run examples/apps/guestbook               # server-rendered site with forms and cookies
```

The largest example is **Klar Board** (`examples/apps/board`): a team task board with accounts, shared boards
and live updates, about 1,400 lines of Klar tested in every backend and in the browser. Its README lists what
building it taught us about the language.

```sh
klar dev examples/apps/board               # open http://127.0.0.1:8000 (serves server.klar and app.klar)
```

The full-stack example (`examples/apps/todo_web`) has a browser app and a JSON backend, both in Klar:

```sh
klar dev examples/apps/todo_web            # open http://127.0.0.1:8000
klar test examples/apps/todo_web/app.klar               # the UI logic, tested without a browser
```

Both sides share their types and calls (`api.klar`): the server answers `add_todo()` with `api_serve`, the
browser calls it with `api_call`, and the checker holds both ends to the same types. There are no URLs or JSON
code to write, rows come out of SQLite as typed records (`sqlite_rows`), and every open tab stays in sync
through live updates. To build it yourself instead of `klar dev`:

```sh
klar build examples/apps/todo_web/app.klar                 # a std.ui app, so: app.html
klar build --target node examples/apps/todo_web/server.klar -o todo.mjs
APP_DIR=examples/apps/todo_web node todo.mjs              # then open http://127.0.0.1:8080
```

npm packages work in JavaScript builds with typed declarations, and never hand Klar code a null or an
exception (see SPEC "JavaScript interop and npm" and `examples/apps/markdown_notes`):

```
klar add npm:typescript                # once per project: lets klar read packages' .d.ts types
klar add npm:marked                    # pins the exact version in klar.toml [npm] and writes npm/marked.klar
```

```klar
extern "js:marked.parse" fn markdown_to_html(markdown: str) -> str
```

For editors, `klar lsp` is a language server: errors as you type with quick fixes, hover types, go to
definition, rename and completion. Setup for VS Code, Neovim and Helix is in [editors/README.md](editors/README.md).

## Security

Web apps are safe by default: escaped HTML, no event-handler or `javascript:` attributes, cross-site form
posts refused, security headers and Secure, HttpOnly cookies, on-site redirects, request size limits. SQL
built from strings is a compile error (E313). Dependencies are pinned and hash-checked, and no install script
ever runs. `klar audit` checks every Python, npm and git dependency, and Klar itself, against published
vulnerability advisories; run it in CI. [SECURITY.md](SECURITY.md) covers reporting a vulnerability, how fixes
reach you, and the known limits.

## Layout

| Path | Contents |
|---|---|
| `klar/syntax.py` | lexer and parser |
| `klar/checker.py` | type checker: inference, generics, effects, exhaustiveness, definite return |
| `klar/interp.py` | tree-walking interpreter and built-ins |
| `klar/prelude.py` | built-in function signatures, written in Klar |
| `klar/errors.py` | diagnostic codes, rendering and JSON output |
| `klar/loader.py` | module loading for `use` |
| `klar/interop.py` | Python interop for `extern` |
| `klar/compiler.py` | Klar to C translation for `klar build` |
| `klar/jscompiler.py` | Klar to JavaScript translation for `--target node` and `--target web` |
| `klar/wasm.py` | WebAssembly builds: `--target wasm` and `extern "wasm:module.fn"` (C backend + clang wasm32) |
| `klar/ownership.py` | which list variables can be updated in place (shared by both compilers) |
| `klar/jsonconv.py` | automatic JSON for any type (interpreter; `klar_std.js` has the same rules) |
| `klar/dev.py` | `klar dev`: rebuild on save, reload the browser, run the backend |
| `klar/tasks.py` | tasks and channels in the interpreter (`std.task`) |
| `klar/lsp.py` | `klar lsp`: the language server (diagnostics, hover, definitions, rename, completion) |
| `klar/pkg.py` | packages: `klar.toml`, `klar.lock`, `klar new/add/install/update`, npm packages via `[npm]` |
| `klar/npm.py` | npm packages in JavaScript builds: resolving, Node imports, embedding ES modules in web pages, `klar bind` |
| `klar/tools/dts2klar.mjs` | `klar bind`: TypeScript declarations to Klar externs (uses the TypeScript compiler) |
| `klar/fmt.py` | `klar fmt`: one canonical layout, checked to keep the exact tokens |
| `klar/runtime/*.js` | JavaScript runtime: values, standard library on Node, browser UI runtime |
| `klar/runtime/klar_rt.h` | C runtime: lists, maps, strings, checked arithmetic, errors |
| `klar/runtime/klar_std.h` | the standard library in C (fs, process, JSON, regex, SQLite, HTTP client and server, tasks) |
| `klar/runtime/wasm/`, `klar_wasm.js` | WebAssembly: the libc subset and allocator the C runtime needs, and the JavaScript host |
| `klar/std/`, `klar/stdpy/` | standard library (Klar modules and their Python implementations) |
| `klar/SPEC.md` | the complete language reference |
| `examples/` | 7 small programs with expected output |
| `examples/apps/` | component showcase (`showcase`), Klar Board (multi-user task board with logins and live updates, ~1,400 lines), full-stack todo app, Game of Life on WebAssembly (`life`), server-rendered guestbook, todo JSON API, notes CLI, Python interop demo, Markdown notes (npm packages `marked` and `dompurify` in the browser and on Node) |
| `editors/` | VS Code extension (highlighting + language server) and Neovim / Helix setup |
| `bench/` | benchmarks against C, Go and Python (`python3 bench/run.py`), the Benchmarks Game programs (`bench/benchmarksgame`), and WebAssembly vs JavaScript in the browser (`bench/wasm`) |
| `tests/` | 172 tests: `python3 -m unittest discover tests` |

## License

Klar is released under the [MIT License](LICENSE).
