# Get started

Klar runs anywhere Python 3.10 or newer does. This page takes you from nothing to a program, a test, a native
executable and a web app in about five minutes. To try the language without installing anything, use the
[playground](playground.html).

## Install

You need Python 3.10+ and git. Klar has no Python dependencies of its own.

```sh
git clone https://github.com/arnoac/klar && cd klar
pipx install .             # gives you the `klar` command
```

No pipx? Link the launcher instead, with no install at all:

```sh
ln -s "$PWD/klarc" ~/.local/bin/klar
klar --version
```

`klar build` also needs a C compiler for native executables (tcc for quick builds, gcc or clang for
`--release`), and JavaScript builds need Node.js 22+ to run on a server. Everything else works without them.

## Your first program

```sh
klar new hello && cd hello
klar run                   # Hello, world!
```

`klar new` made a folder with a `klar.toml` and a `main.klar`. Open `main.klar` and replace it with this:

```klar
fn greeting(name: str) -> str {
  return if name == "" then "Hello, stranger!" else "Hello, {name}!"
}

fn main() uses io {
  for name in ["Ada", "", "Grace"] {
    print(greeting(name))
  }
}

test "greets by name" {
  assert greeting("Ada") == "Hello, Ada!"
  assert greeting("") == "Hello, stranger!"
}
```

Then run it, and run its test:

```sh
klar run                   # three greetings
klar test                  # PASS  greets by name
```

A few things to notice. Every function says its parameter and return types. `main` says `uses io` because it
prints: a function that does input or output must say so, so any signature without it is free of side effects.
Strings take expressions in braces. Tests live next to the code they test.

## When something is wrong

Change the test to `assert greeting(1) == "Hello, 1!"` and run `klar check`:

```text
error[E301] main.klar:12:19: type mismatch: argument `name` of `greeting` has type `int` but `str` is expected
    12 |   assert greeting(1) == "Hello, 1!"
       |                   ^
  hint: convert with str(value) or use interpolation: "{value}"
```

Every error has a stable code (`klar explain E301` says more about it), an exact line and column, and usually
a hint with the fix. `klar check --json` prints the same as JSON, which is what editors and agents read. All
independent errors are reported in one run.

## Build an executable

```sh
klar build && ./hello      # a native executable, through C
klar build --release       # optimised with gcc or clang -O2
```

The interpreter behind `klar run` is meant for quick runs and tests, and it is slow. Anything that computes a
lot should be built: compiled Klar runs within 1 to 3 times of C and Go on the usual benchmarks.

## A web app

```sh
klar new shop --web && cd shop
klar dev                   # open http://127.0.0.1:8000
```

`klar dev` rebuilds and reloads the page every time you save. A Klar web app is a model, the messages that
change it, a pure `update` function and a pure `view` function that returns typed HTML. Because `update` and
`view` are plain functions, `test` blocks cover them without a browser. `klar build` turns the app into one
self-contained `shop.html`.

The [tutorial](tutorial.html) builds a full-stack app step by step: a typed API, a SQLite database and a
browser front end, all in Klar.

## Add a dependency

```sh
klar add ../geometry                                 # a local Klar package
klar add https://github.com/someone/textkit@v1.2.0   # a Klar package from git, pinned
klar add py:requests==2.32.3                         # a Python library, for extern functions
klar add npm:marked                                  # an npm package, for JavaScript builds
```

Versions are always pinned in `klar.lock`, downloads are hash-checked, and install scripts never run.
`klar audit` checks every dependency against published vulnerability advisories.

## Editors

`klar lsp` is a language server: errors as you type with quick fixes, hover types, go to definition, rename
and completion. The VS Code extension and the setup for Neovim and Helix are in
[editors/README.md](https://github.com/arnoac/klar/blob/main/editors/README.md).

## Where to go next

* The [language reference](reference.html) is the whole language on one page.
* The [standard library](std/index.html) lists every module and function, generated from the source.
* Working with an AI agent? Give it [llms.txt](llms.txt): the language on one page. [llms-full.txt](llms-full.txt) adds the complete reference and every library signature.
