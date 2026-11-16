# Klar on the Computer Language Benchmarks Game

Machine: 4 CPUs, Linux. gcc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0; Go go1.24.7; Python 3.13.18. Klar built with `klar build --release` (gcc -O2, Boehm GC: gc) and with `klar build --target node` (Node v22.23.4).
Wall-clock time (median of 3, Python run once) and peak memory. Every language's output is checked to be byte-identical. All programs are single-threaded; see README.md for what that means.

### Scaled sizes (all languages)

| Program | size | Klar --release | Klar on Node | C (gcc -O2) | Go | Python 3 | Klar / C | Klar / Go |
|---|---|---|---|---|---|---|---|---|
| nbody | 5,000,000 | 1.22 s (3 MB) | 1.57 s (80 MB) | 280 ms (2 MB) | 488 ms (2 MB) | 21.14 s (11 MB) | 4.4x | 2.5x |
| spectralnorm | 2,000 | 493 ms (3 MB) | 2.09 s (81 MB) | 391 ms (2 MB) | 320 ms (2 MB) | 26.94 s (11 MB) | 1.3x | 1.5x |
| fannkuchredux | 10 | 756 ms (2 MB) | 1.19 s (81 MB) | 297 ms (2 MB) | 315 ms (2 MB) | 5.08 s (11 MB) | 2.5x | 2.4x |
| binarytrees | 18 | 2.27 s (56 MB) | 5.06 s (648 MB) | 1.42 s (34 MB) | 2.58 s (45 MB) | 7.03 s (43 MB) | 1.6x | 0.9x |
| mandelbrot | 4,000 | 1.16 s (2 MB) | 1.68 s (80 MB) | 1.16 s (2 MB) | 1.19 s (2 MB) | 46.00 s (11 MB) | 1.0x | 1.0x |
| fasta | 2,500,000 | 897 ms (2 MB) | 1.55 s (90 MB) | 417 ms (1 MB) | 466 ms (2 MB) | 5.31 s (108 MB) | 2.1x | 1.9x |

### Official Benchmarks Game sizes (compiled languages, 1 run)

| Program | size | Klar --release | Klar on Node | C (gcc -O2) | Go | Klar / C | Klar / Go |
|---|---|---|---|---|---|---|---|
| nbody | 50,000,000 | 12.36 s (2 MB) | 13.31 s (78 MB) | 3.11 s (2 MB) | 4.60 s (2 MB) | 4.0x | 2.7x |
| spectralnorm | 5,500 | 3.82 s (4 MB) | 14.03 s (82 MB) | 1.90 s (2 MB) | 1.97 s (2 MB) | 2.0x | 1.9x |
| fannkuchredux | 12 | 117.94 s (2 MB) | 179.03 s (90 MB) | 39.69 s (2 MB) | 39.80 s (2 MB) | 3.0x | 3.0x |
| binarytrees | 21 | 27.40 s (282 MB) | 62.51 s (2805 MB) | 13.47 s (258 MB) | 22.73 s (282 MB) | 2.0x | 1.2x |
| mandelbrot | 16,000 | 19.03 s (2 MB) | 26.88 s (81 MB) | 19.57 s (2 MB) | 18.93 s (2 MB) | 1.0x | 1.0x |
| fasta | 25,000,000 | 8.01 s (2 MB) | 14.29 s (106 MB) | 3.71 s (1 MB) | 4.16 s (2 MB) | 2.2x | 1.9x |
