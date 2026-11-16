# Klar

A statically typed, general-purpose programming language designed to be easy for AI agents to learn, read,
write and repair. The complete language reference is [klar/SPEC.md](klar/SPEC.md) (about 4,000 words); the
reasoning behind each design choice is in [DESIGN.md](DESIGN.md), and what comes next is in
[ROADMAP.md](ROADMAP.md).

Klar has modules, generics, checked side effects, a standard library (files, JSON, HTTP client and
server, SQLite, time, processes, math, random, regex, tasks and channels, crypto) and typed interop with any Python library.

Klar is built for the web as well: HTML pages are typed, auto-escaped values (`std.html`) laid out with typed, responsive containers and grids (`std.layout`) and built from accessible components (`std.kit`), browser apps use
model / update / view (`std.ui`), and backends are routes over plain functions (`std.web`). The same code
renders on the server and in the browser, and it all ships with a theme that looks good by default.
`klar build --target web` makes a self-contained page; `--target node` makes a Node.js server. JSON is
derived from your types (`json_encode`, `json_decode`, `web_json`, `web_body`), servers push live updates
to browsers (`web_publish`, `ui_listen`), and `klar dev` rebuilds and reloads the browser on every save.

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

## Running it

Requires Python 3.10+. `klar build` also needs a C compiler (tcc for dev builds, falling back to cc; gcc or
clang for `--release`); the Boehm GC (`libgc-dev`) is used when installed. JavaScript builds need Node.js 22+
to run on a server (any modern browser for `--target web`).

```sh
./klarc run examples/shapes.klar       # check and run main()
./klarc build examples/shapes.klar     # native executable via tcc (fast build)
./klarc build --release prog.klar      # via gcc/clang -O2 (fast code)
./klarc build --target node prog.klar  # Node.js module: node prog.mjs
./klarc build --target web app.klar    # browser app: open app.html
./klarc test examples/bank.klar        # run test blocks
./klarc check --json some_file.klar    # machine-readable diagnostics
./klarc fmt examples/                  # one canonical layout (--check to only report)
./klarc new my_app                     # a package: klar.toml + main.klar
./klarc add textkit --git URL --tag v1.0.0   # a dependency, pinned in klar.lock
./klarc spec                           # print the language reference
./klarc explain E306                   # explain an error code
echo "2 * (3 + 4)" | ./klarc run examples/calc.klar

PORT=8080 ./klarc run examples/apps/todo_api/main.klar     # JSON API + HTML page on SQLite
./klarc run examples/apps/notes_cli/main.klar add "buy milk"  # CLI tool storing JSON
./klarc run examples/apps/py_interop/main.klar             # calling Python libraries
./klarc run examples/apps/guestbook/main.klar              # server-rendered site with forms and cookies
```

The largest example is **Klar Board** (`examples/apps/board`): a team task board with accounts, shared boards
and live updates, about 1,400 lines of Klar tested in every backend and in the browser. Its README lists what
building it taught us about the language.

```sh
./klarc dev examples/apps/board/server.klar examples/apps/board/app.klar     # open http://127.0.0.1:8000
```

The full-stack example (`examples/apps/todo_web`) has a browser app and a JSON backend, both in Klar:

```sh
./klarc dev examples/apps/todo_web/server.klar examples/apps/todo_web/app.klar   # open http://127.0.0.1:8000
./klarc test examples/apps/todo_web/app.klar               # the UI logic, tested without a browser
```

Both sides share their types (`api.klar`), JSON is derived from them, and every open tab stays in sync
through live updates. To build it yourself instead of `klar dev`:

```sh
./klarc build --target web examples/apps/todo_web/app.klar -o examples/apps/todo_web/app.html
./klarc build --target node examples/apps/todo_web/server.klar -o todo.mjs
APP_DIR=examples/apps/todo_web node todo.mjs              # then open http://127.0.0.1:8080
```

(`./klarc` is a wrapper for `python3 -m klar`.)

npm packages work in JavaScript builds with typed declarations, and never hand Klar code a null or an
exception (see SPEC "JavaScript interop and npm" and `examples/apps/markdown_notes`):

```
./klarc add --npm marked                 # pins the exact version in klar.toml [npm]; install scripts never run
./klarc bind marked                      # npm/marked.klar: extern declarations generated from marked's .d.ts
```

```klar
extern "js:marked.parse" fn markdown_to_html(markdown: str) -> str
```

For editors, `./klarc lsp` is a language server: errors as you type with quick fixes, hover types, go to
definition, rename and completion. Setup for VS Code, Neovim and Helix is in [editors/README.md](editors/README.md).

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
| `klar/std/`, `klar/stdpy/` | standard library (Klar modules and their Python implementations) |
| `klar/SPEC.md` | the complete language reference |
| `examples/` | 7 small programs with expected output |
| `examples/apps/` | component showcase (`showcase`), Klar Board (multi-user task board with logins and live updates, ~1,400 lines), full-stack todo app, server-rendered guestbook, todo JSON API, notes CLI, Python interop demo, Markdown notes (npm packages `marked` and `dompurify` in the browser and on Node) |
| `editors/` | VS Code extension (highlighting + language server) and Neovim / Helix setup |
| `bench/` | benchmarks against C, Go and Python (`python3 bench/run.py`), and the Benchmarks Game programs (`bench/benchmarksgame`) |
| `tests/` | 137 tests: `python3 -m unittest discover tests` |
