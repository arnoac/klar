# Klar on WebAssembly

Klar compiles to WebAssembly through its C backend: the same generated C as `klar build`, compiled by clang's
`wasm32` target. Nothing else is needed (no Emscripten, no wasi-sdk): the runtime carries the small part of libc it
uses (`klar/runtime/wasm/klar_wasm.h`) and the JavaScript host (`klar/runtime/klar_wasm.js`) supplies output,
float formatting, math and clocks. It runs in every browser and in Node.

```sh
klar build --target wasm prog.klar     # prog.wasm + prog.mjs; run with: node prog.mjs
```

The more useful form is inside a web app. Page updates go through the JavaScript backend (the DOM is only
reachable from JavaScript), and heavy, pure computation goes to WebAssembly:

```klar
// app.klar, built with: klar build --target web app.klar
extern "wasm:fractal.render" fn render(w: int, h: int, limit: int) -> list[int]
```

`fractal.klar` (next to `app.klar`) is ordinary Klar. The web build compiles it to WebAssembly, embeds the module
in `app.html`, and `render` calls it. The extern's signature must match the function's (E301, with the exact
declaration as the fix). `klar run` and `klar test` interpret `fractal.klar` instead, so tests need no compiler.

**What crosses the boundary.** Arguments and results are copied: ints, floats and bools directly, strings as UTF-8,
`list[int]` and `list[float]` as raw arrays, anything else (structs, enums, maps, nested lists) as JSON through the
same type-directed encoding as `json_encode`. Functions and extern types cannot cross (E501). A runtime error in
WebAssembly (index out of bounds, overflow, panic) arrives as the same Klar error, with the module's line and column.

**Memory.** Klar has no global variables, so when an exported function has returned and its result is copied out,
nothing it allocated is reachable. Every call therefore starts with an empty heap: allocation is a pointer bump and
freeing costs nothing. The limit is that one call can allocate at most 4 GB in total; a long-running,
allocation-heavy program (binarytrees at depth 21, say) needs the native build.

**Not available in WebAssembly** (refused at build time with E501): files, processes, sockets and HTTP, SQLite,
regex, tasks and channels, `time_sleep`, `read_file`/`write_file`. `std.math`, `std.random`, `std.json`,
`std.crypto`, `time_now`/`time_millis`/formatting and the web helpers work. `read_line` reads stdin under Node.

`examples/apps/life` is a small app built this way: a Game of Life page whose simulation runs as WebAssembly,
with a switch to run the same Klar code as JavaScript instead. In headless Chromium, 100 generations of a 160 x 60
grid take about 22 ms per click as WebAssembly and about 165 ms as JavaScript.

## Results

`node bench/wasm/run.mjs` runs six kernels three ways in Node and in headless Chromium, checks that all results
are identical, and writes [RESULTS.md](RESULTS.md). Summary of the browser run (warm, best of 4):

| Kernel | Klar -> JS | Klar -> wasm | hand-written JS |
|---|---|---|---|
| nbody 1M steps | 768 ms | 235 ms | 124 ms |
| spectralnorm 1000 | 250 ms | 149 ms | 85 ms |
| fannkuch 10 | 1842 ms | 521 ms | 310 ms |
| mandelbrot 2000 | 355 ms | 317 ms | 311 ms |
| binarytrees 16 | 1433 ms | 252 ms | 220 ms |
| smooth 1M floats x 10 (data in and out) | 214 ms | 81 ms | 80 ms |

* WebAssembly is 1.1-5.7x faster than Klar's JavaScript backend on every kernel in the browser (in Node it is
  1.4-4.6x faster on five of six; nbody is 1.1x slower there).
* It matches hand-written JavaScript on mandelbrot, binarytrees and smooth, and is 1.7-1.9x slower on nbody,
  spectralnorm and fannkuch. The gap is Klar's own semantics, not WebAssembly: every int operation is checked for
  64-bit overflow, every index is bounds-checked, and lists are immutable values whose `set_at` updates in place
  only when nothing else holds the list (nbody and fannkuch keep a second reference, so they log undo entries).
  Native builds pay the same costs (see ../benchmarksgame).
* **Cold starts.** Browsers first run WebAssembly with a quick baseline compiler and switch to the optimizing one for
  later calls, not in the middle of a running call (V8 has no on-stack replacement for WebAssembly). A single
  long first call is therefore slower: fannkuch's first run took 1372 ms against 521 ms warm. Apps that call a
  function repeatedly (renders, simulations, per-keystroke work) get the warm numbers after the first call.
