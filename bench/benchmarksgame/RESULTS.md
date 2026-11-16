# Klar on the Computer Language Benchmarks Game

Machine: 4 CPUs, Linux. gcc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0; Go go1.24.7; Python 3.13.18. Klar built with `klar build --release` (gcc -O2, Boehm GC: gc) and with `klar build --target node` (Node v22.23.4).
Wall-clock time (median of 3, Python run once) and peak memory. Every language's output is checked to be byte-identical. All programs are single-threaded; see README.md for what that means. knucleotide, revcomp and regexredux read the output of `fasta N` on stdin.

### Scaled sizes (all languages)

| Program | size (N) | Klar --release | Klar on Node | C (gcc -O2) | Go | Python 3 | Klar / C | Klar / Go |
|---|---|---|---|---|---|---|---|---|
| nbody | 5,000,000 | 991 ms (3 MB) | 1.23 s (81 MB) | 244 ms (2 MB) | 409 ms (2 MB) | 16.99 s (12 MB) | 4.1x | 2.4x |
| spectralnorm | 2,000 | 407 ms (3 MB) | 1.58 s (82 MB) | 188 ms (2 MB) | 214 ms (2 MB) | 22.17 s (12 MB) | 2.2x | 1.9x |
| fannkuchredux | 10 | 658 ms (2 MB) | 1.02 s (82 MB) | 211 ms (2 MB) | 239 ms (2 MB) | 3.57 s (11 MB) | 3.1x | 2.8x |
| binarytrees | 18 | 2.04 s (56 MB) | 3.96 s (760 MB) | 1.14 s (34 MB) | 2.11 s (44 MB) | 5.85 s (43 MB) | 1.8x | 1.0x |
| mandelbrot | 4,000 | 1.02 s (2 MB) | 1.36 s (80 MB) | 939 ms (2 MB) | 964 ms (2 MB) | 37.98 s (11 MB) | 1.1x | 1.1x |
| fasta | 2,500,000 | 467 ms (2 MB) | 1.27 s (106 MB) | 330 ms (1 MB) | 400 ms (2 MB) | 4.32 s (108 MB) | 1.4x | 1.2x |
| knucleotide | 2,500,000 | 5.37 s (86 MB) | 10.79 s (173 MB) | 712 ms (29 MB) | 2.24 s (59 MB) | 12.24 s (63 MB) | 7.6x | 2.4x |
| revcomp | 2,500,000 | 695 ms (83 MB) | 3.58 s (178 MB) | 56 ms (13 MB) | 200 ms (33 MB) | 491 ms (65 MB) | 12.4x | 3.5x |
| regexredux | 100,000 | 56 ms (13 MB) | 235 ms (89 MB) | 40 ms (9 MB) | 624 ms (9 MB) | 225 ms (21 MB) | 1.4x | 0.1x |

### Official Benchmarks Game sizes (compiled languages, 1 run)

| Program | size (N) | Klar --release | Klar on Node | C (gcc -O2) | Go | Klar / C | Klar / Go |
|---|---|---|---|---|---|---|---|
| nbody | 50,000,000 | 10.55 s (2 MB) | 11.08 s (80 MB) | 2.32 s (2 MB) | 4.01 s (2 MB) | 4.6x | 2.6x |
| spectralnorm | 5,500 | 2.99 s (4 MB) | 9.98 s (82 MB) | 1.47 s (2 MB) | 1.51 s (2 MB) | 2.0x | 2.0x |
| fannkuchredux | 12 | 91.08 s (2 MB) | 138.62 s (89 MB) | 31.93 s (2 MB) | 33.56 s (2 MB) | 2.9x | 2.7x |
| binarytrees | 21 | 24.67 s (282 MB) | 48.42 s (2790 MB) | 12.51 s (258 MB) | 17.23 s (262 MB) | 2.0x | 1.4x |
| mandelbrot | 16,000 | 15.13 s (2 MB) | 19.75 s (82 MB) | 15.01 s (2 MB) | 15.28 s (2 MB) | 1.0x | 1.0x |
| fasta | 25,000,000 | 4.12 s (2 MB) | 10.48 s (106 MB) | 3.42 s (1 MB) | 3.80 s (2 MB) | 1.2x | 1.1x |
| knucleotide | 25,000,000 | 53.01 s (792 MB) | 103.45 s (601 MB) | 6.54 s (137 MB) | 19.00 s (497 MB) | 8.1x | 2.8x |
| revcomp | 25,000,000 | 7.11 s (603 MB) | 34.09 s (669 MB) | 526 ms (121 MB) | 1.48 s (289 MB) | 13.5x | 4.8x |
| regexredux | 5,000,000 | 2.66 s (370 MB) | 4.26 s (821 MB) | 1.84 s (315 MB) | 33.99 s (275 MB) | 1.4x | 0.1x |
