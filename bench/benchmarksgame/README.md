# Klar on the Computer Language Benchmarks Game

The [Computer Language Benchmarks Game](https://benchmarksgame-team.pages.debian.net/benchmarksgame/) is the
best-known public site that compares programming languages on the same set of small programs. This folder
ports nine of its ten programs to Klar and runs them next to C, Go and Python on the same machine.

```sh
python3 bench/benchmarksgame/run.py          # scaled sizes, all languages (~10 min)
python3 bench/benchmarksgame/run.py --full   # plus the official sizes for C, Go and Klar (~60 min)
```

`run.py` builds everything, checks that all languages print byte-identical output, and writes
[RESULTS.md](RESULTS.md) (wall time and peak memory).

## How to read the numbers

* **Same algorithm, one core.** Each C, Go and Python file is a plain, single-threaded implementation of the
  official algorithm, compiled with `gcc -O2` like `klar build --release`. The top entries on the
  Benchmarks Game site use all cores and hand-written SIMD; Klar has neither yet, so comparing against those
  would measure threads, not code generation.
* **Official sizes vs. scaled sizes.** The official sizes (nbody 50M steps, spectral-norm 5,500,
  fannkuch-redux 12, binary-trees 21, mandelbrot 16,000, fasta 25M, and fasta 25M / 25M / 5M output as the input
  of k-nucleotide / reverse-complement / regex-redux) take minutes in Python, so the main table
  uses smaller sizes for all four languages, and `--full` adds the official sizes for the compiled ones.
* **mandelbrot output.** The official program writes a binary PBM image. Klar cannot write raw bytes (strings
  are UTF-8 text and `print` adds a newline), so every language here prints the PBM header plus a checksum of
  the identical bitmap bytes. The work per pixel is unchanged.
* **Garbage collector.** `klar build` uses the Boehm GC when `libgc-dev` is installed and otherwise never frees
  memory. This machine has the GC library but not its headers, so `run.py` compiles Klar's generated C with
  a 10-line `gc/gc.h` declaring the four functions the runtime uses. That is the same binary `klar build
  --release` produces on a machine with `libgc-dev`.

## Results in short

Klar at the official sizes, single-threaded; ratios are slower-than unless marked. Python numbers are from the
scaled sizes. Exact numbers are in [RESULTS.md](RESULTS.md).

| Program | What it stresses | Klar vs C | Klar vs Go | Klar vs Python | Klar on Node vs C |
|---|---|---|---|---|---|
| mandelbrot | float loops | 1.0x | 1.0x | 37x faster | 1.3x |
| fasta | string building, output | 1.2x | 1.1x | 9x faster | 3.1x |
| regex-redux | regular expressions (PCRE2) | 1.4x | 13x faster | 4x faster | 2.3x |
| binary-trees | allocation + GC | 2.0x | 1.4x | 2.9x faster | 3.9x |
| spectral-norm | float loops over arrays | 2.0x | 2.0x | 54x faster | 6.8x |
| fannkuch-redux | in-place array permutation | 2.9x | 2.7x | 5x faster | 4.3x |
| nbody | small arrays updated every step | 4.6x | 2.6x | 17x faster | 4.8x |
| k-nucleotide | hash map of string keys | 8.1x | 2.8x | 2.3x faster | 16x |
| reverse-complement | rewriting ~250 MB of text | 13.5x | 4.8x | 1.4x slower | 65x |

**What closed the gaps.** Klar values never change, so loops that update a list, map or string would copy it
every time. Instead, a `var` that is the only holder of its value is updated in place, and any other use of
the var hands out the value as it is (see `klar/ownership.py`; values stay immutable as far as any program can
tell). This covers `xs = set_at(xs, i, v)` and `xs = push(xs, v)` (nbody and fannkuch-redux: 23-29x C down to
3-5x), `m = put(m, k, v)` (k-nucleotide: 26x to 8x), and `s = s + t` (reverse-complement: 40x to 13.5x,
memory 6.4 GB to 0.6 GB, and fasta: 1.8x to 1.2x). The same rule runs in the interpreter, so `klar run` no
longer turns these loops quadratic. One-character ASCII strings (from `s[i]`, `slice`, `chars`) are shared
constants and cost no allocation. Regular expressions use PCRE2 with its JIT, as the C program does.

**What is left:**

* Every `xs[i]` is bounds-checked and every integer `+`/`*` is overflow-checked (E408), and list reads check
  a version; hoisting those checks out of simple `for i in range(...)` loops would let gcc vectorise
  spectral-norm and nbody.
* k-nucleotide allocates a new string for every k-mer and hashes it byte by byte; the C program packs k-mers
  into integers. Caching string hashes, or slices that share the parent string's bytes, would help.
* reverse-complement looks up each character in a `map[str, str]`. Klar has no character codes or byte type,
  so there is nothing cheaper to write. On Node, building strings one character at a time is slow.
* `klar build` without `libgc-dev` silently produces a binary that never frees memory; it should warn.

## Compiler and runtime findings

Porting these programs found two real problems, both fixed on main: native regex was O(length x matches)
(PCRE2 re-validated the whole subject as UTF-8 on every match; regex-redux on 1 MB took 25 s, now 0.06 s), and
the interpreter copied lists on every `push`/`set_at` although SPEC promises O(1).

## Programs not ported yet

| Program | Status |
|---|---|
| pidigits | Needs arbitrary-precision integers. The interpreter has them, but compiled `int` is 64-bit (overflow is E408), and the interpreter is far too slow for this benchmark. Needs a compiled bignum type. |

## Web frameworks (TechEmpower) next

The [TechEmpower Framework Benchmarks](https://www.techempower.com/benchmarks/) are the public comparison for
web servers: plaintext, JSON serialisation, single and multiple database queries, "fortunes" (query + HTML
template) and updates, all under heavy concurrent load. Since v0.8 `std.http` and `std.json` compile natively
and since v0.9 servers handle requests concurrently, so the plaintext and JSON tests can be written today (the
todo API already serves about 7,700 requests/s natively). Before the database tests mean anything Klar needs
a PostgreSQL or MySQL driver, because TechEmpower's database tests use those, not SQLite, and a CPU-heavy
handler still holds up the others because Klar code runs one task at a time.
