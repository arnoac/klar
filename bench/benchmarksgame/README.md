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

Klar v0.14 at the official sizes, single-threaded; ratios are slower-than. Python numbers are from the scaled
sizes. Exact numbers are in [RESULTS.md](RESULTS.md).

| Program | What it stresses | Klar vs C | Klar vs Go | Klar vs Python | Klar on Node vs C |
|---|---|---|---|---|---|
| mandelbrot | float loops | 1.1x | 1.0x | 37x faster | 1.5x |
| spectral-norm | float loops over arrays | 1.7x | 1.8x | 57x faster | 6.9x |
| fasta | string building, output | 1.8x | 1.8x | 6.4x faster | 3.4x |
| binary-trees | allocation + GC | 2.1x | 1.5x | 2.7x faster | 4.4x |
| fannkuch-redux | in-place array permutation | 3.0x | 2.8x | 5x faster | 4.6x |
| nbody | small arrays updated every step | 4.8x | 2.9x | 14x faster | 4.7x |
| regex-redux | regular expressions (PCRE2) | 8.3x | 2.2x faster | 1.5x slower | 2.4x |
| k-nucleotide | hash map of string keys | 26x | 9.1x | 1.3x slower | 41x |
| reverse-complement | byte-level rewrite of ~250 MB | 40x | 15x | 8x slower | 45x |

**Where Klar is at C/Go speed:** numeric loops (mandelbrot, spectral-norm), output-heavy string code
(fasta), allocation-heavy recursive data (binary-trees, within 1.5x of Go) and, since v0.5, array code that
updates lists in place (fannkuch-redux, nbody: 3-5x C, down from 23-29x). The rest of that gap is bounds
checks, overflow checks and the version check on each list read, which keep gcc from vectorising.

**Where Klar is far behind, and why** (the three programs added last):

* **k-nucleotide (26x C, 9x Go).** `counts = put(counts, key, n + 1)` never updates in place: every `put`
  appends a new version of the key to the map's log, allocates a new map value, and compacts the log every
  few updates. Each k-mer is also a freshly allocated `slice` of the sequence, hashed twice (`get`, then
  `put`). Lists got in-place updates in v0.5; maps have not.
* **reverse-complement (40x C, 6.4 GB of memory).** Klar has no byte buffer, so the program turns each
  sequence into a list of one-character strings with `chars()`, looks every character up in a
  `map[str, str]`, and joins lines back together. 125 million small strings is where the memory goes.
* **regex-redux (8x C, but 2x faster than Go).** Matching is already PCRE2, as in C. The difference is that
  the C program uses PCRE2's JIT and Klar doesn't, and `regex_find_all` builds a list of matched strings
  where only the count is needed. Before the fix described below it was quadratic.

## Compiler and runtime findings

Porting these programs found two real problems, both fixed on main:

1. **Native regex was O(length x matches).** PCRE2 checks that the whole subject is valid UTF-8 on every
   `pcre2_match` call unless told not to, and `regex_find_all`/`regex_replace` call it once per match.
   regex-redux on a 1 MB input took 25 s; it now checks once per scan and takes 0.3 s.
2. **The interpreter copied lists on every `push`/`set_at`**, although SPEC promises O(1). It now uses the
   same ownership rule as the compiled backends (an agent evaluation found a program going from 8 s to 91 s
   because of it).

Next improvements, in order of payoff:

1. **In-place `put` for maps held by one `var`**, the same ownership rule lists use. This is most of
   k-nucleotide's gap, and maps are everywhere in real programs.
2. **A byte buffer or string builder type** (and a way to write raw bytes, which mandelbrot's PBM output
   needs) for reverse-complement, fasta and any text processing.
3. **PCRE2 JIT** for patterns used on long strings, and a `regex_count` that doesn't build the match list.
4. **Hoist bounds, version and overflow checks out of simple `for i in range(...)` loops**, which would let
   gcc vectorise spectral-norm and nbody.
5. **Warn when building without a GC.** On a machine without `libgc-dev`, `klar build` silently produces a
   binary that never frees memory; binary-trees at the official size would need many GB.

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
