# Klar on the Computer Language Benchmarks Game

The [Computer Language Benchmarks Game](https://benchmarksgame-team.pages.debian.net/benchmarksgame/) is the
best-known public site that compares programming languages on the same set of small programs. This folder
ports six of its ten programs to Klar and runs them next to C, Go and Python on the same machine.

```sh
python3 bench/benchmarksgame/run.py          # scaled sizes, all four languages (~5 min)
python3 bench/benchmarksgame/run.py --full   # plus the official sizes for C, Go and Klar (~35 min)
```

`run.py` builds everything, checks that all languages print byte-identical output, and writes
[RESULTS.md](RESULTS.md) (wall time and peak memory).

## How to read the numbers

* **Same algorithm, one core.** Each C, Go and Python file is a plain, single-threaded implementation of the
  official algorithm, compiled with `gcc -O2` like `klar build --release`. The top entries on the
  Benchmarks Game site use all cores and hand-written SIMD; Klar has neither yet, so comparing against those
  would measure threads, not code generation.
* **Official sizes vs. scaled sizes.** The official sizes (nbody 50M steps, spectral-norm 5,500,
  fannkuch-redux 12, binary-trees 21, mandelbrot 16,000, fasta 25M) take minutes in Python, so the main table
  uses smaller sizes for all four languages, and `--full` adds the official sizes for the compiled ones.
* **mandelbrot output.** The official program writes a binary PBM image. Klar cannot write raw bytes (strings
  are UTF-8 text and `print` adds a newline), so every language here prints the PBM header plus a checksum of
  the identical bitmap bytes. The work per pixel is unchanged.
* **Garbage collector.** `klar build` uses the Boehm GC when `libgc-dev` is installed and otherwise never frees
  memory. This machine has the GC library but not its headers, so `run.py` compiles Klar's generated C with
  a 10-line `gc/gc.h` declaring the four functions the runtime uses. That is the same binary `klar build
  --release` produces on a machine with `libgc-dev`.

## Results in short

Scaled sizes, Klar v0.5. At the official sizes the picture is the same (1.0-4.0x C, 1.0-3.0x Go; nbody is
4.0x C there). Ratios are slower-than unless marked. Exact numbers are in [RESULTS.md](RESULTS.md).

| Program | What it stresses | Klar vs C | Klar vs Go | Klar vs Python | Klar on Node vs C |
|---|---|---|---|---|---|
| mandelbrot | float loops | 1.0x | 0.9x (faster) | 39x faster | 1.4x |
| binary-trees | allocation + GC | 1.7x | 0.9x (faster) | 3.3x faster | 4.1x |
| fasta | string building, output | 2.0x | 1.8x | 6.3x faster | 4.1x |
| spectral-norm | float loops over arrays | 2.2x | 1.9x | 54x faster | 7.9x |
| nbody | small arrays updated every step | 2.6x (was 28x) | 2.0x (was 16x) | 19x faster | 3.6x |
| fannkuch-redux | in-place array permutation | 2.7x (was 29x) | 2.9x | 7x faster (was 1.5x slower) | 4.1x |

**What changed in v0.5: lists update in place.** `xs = set_at(xs, i, v)` and `xs = push(xs, v)` on a list held
only by that `var` now write straight into the list's buffer, so nbody and fannkuch-redux went from 23-29x C to
about 2.7x. The compiler tracks a hidden "owned" flag per list `var` (see `klar/ownership.py`): a list literal,
`range()` or the copy made by the first `set_at` is owned; reading `xs[i]` or `len(xs)` keeps it owned; any other
use (passing it on, storing it, iterating it) clears the flag first, so no other value can ever see a write. A
`set_at` on a list that is not owned still works in place on the newest version of its buffer and keeps an undo
log for older versions. Values stay immutable as far as any program can tell (tests/programs/owned_lists.klar).
On Node, list reads are also inlined at each site, which made nbody 7x faster there.

**Where the remaining 2-3x goes:** every `xs[i]` is bounds-checked, every integer `+`/`*` is overflow-checked
(E408), and the version check on each read keeps gcc from vectorising the inner loops. fasta has no byte
buffer or string builder.

## Compiler and runtime findings

No compiler bug blocked any program: all six compiled on the first try and match C's output exactly. Next
improvements, in order of payoff:

1. **Hoist bounds, version and overflow checks out of simple `for i in range(...)` loops**, which would let gcc
   vectorise spectral-norm and nbody.
2. **Atomic allocation for numeric buffers.** Lists of `int`/`float` could use `GC_MALLOC_ATOMIC`, so the GC
   doesn't scan them.
3. **A string builder or byte buffer** for fasta and reverse-complement.
4. **Warn when building without a GC.** On a machine without `libgc-dev`, `klar build` silently produces a
   binary that never frees memory; binary-trees at the official size would need many GB.

## Programs not ported yet

| Program | Status |
|---|---|
| pidigits | Needs arbitrary-precision integers. The interpreter has them, but compiled `int` is 64-bit (overflow is E408), and the interpreter is far too slow for this benchmark. Needs a compiled bignum type. |
| regex-redux | Needs regular expressions. `std.regex` is Python-backed and interpreter-only, so `klar build` rejects it (E501). |
| k-nucleotide | Expressible today (`read_line`, `map[str, int]`): reads fasta output from stdin and counts substrings in a hash map. A good next port; it would mainly measure Klar's maps and string slicing. |
| reverse-complement | Expressible but would be slow: it rewrites ~250 MB of input, which needs a mutable byte buffer that Klar doesn't have. |

## Web frameworks (TechEmpower) later

The [TechEmpower Framework Benchmarks](https://www.techempower.com/benchmarks/) are the public comparison for
web servers: plaintext, JSON serialisation, single and multiple database queries, "fortunes" (query + HTML
template) and updates, all under heavy concurrent load. Klar can already serve these endpoints
(`std.http` + `std.json` + `std.sqlite`, see `examples/apps/todo_api`), but only in the interpreter, on top of
Python's `http.server`, so a TechEmpower run today would measure Python. It becomes meaningful once:

1. `std.http` and `std.json` compile natively (today they are E501 in `klar build`), on an event loop or
   thread pool, since TechEmpower runs hundreds to thousands of concurrent connections;
2. Klar has concurrency (on the ROADMAP);
3. there is a PostgreSQL or MySQL driver, because TechEmpower's database tests use those, not SQLite.

The plaintext and JSON tests need only step 1 and would be the natural first entries.
