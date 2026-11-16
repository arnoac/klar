# Klar on the Computer Language Benchmarks Game

Machine: 4 CPUs, Linux. gcc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0; Go go1.24.7; Python 3.13.18. Klar built with `klar build --release` (gcc -O2, Boehm GC: gc) and with `klar build --target node` (Node v22.23.4).
Wall-clock time (median of 3, Python run once) and peak memory. Every language's output is checked to be byte-identical. All programs are single-threaded; see README.md for what that means. knucleotide, revcomp and regexredux read the output of `fasta N` on stdin.

### Scaled sizes (all languages)

| Program | size (N) | Klar --release | Klar on Node | C (gcc -O2) | Go | Python 3 | Klar / C | Klar / Go |
|---|---|---|---|---|---|---|---|---|
| nbody | 5,000,000 | 1.23 s (2 MB) | 1.25 s (80 MB) | 326 ms (2 MB) | 407 ms (2 MB) | 17.30 s (12 MB) | 3.8x | 3.0x |
| spectralnorm | 2,000 | 406 ms (3 MB) | 1.66 s (81 MB) | 223 ms (2 MB) | 175 ms (2 MB) | 23.34 s (12 MB) | 1.8x | 2.3x |
| fannkuchredux | 10 | 717 ms (2 MB) | 1.05 s (82 MB) | 223 ms (2 MB) | 230 ms (2 MB) | 3.60 s (11 MB) | 3.2x | 3.1x |
| binarytrees | 18 | 2.10 s (56 MB) | 3.96 s (744 MB) | 1.20 s (34 MB) | 2.10 s (45 MB) | 5.65 s (43 MB) | 1.7x | 1.0x |
| mandelbrot | 4,000 | 1.05 s (2 MB) | 1.46 s (81 MB) | 880 ms (2 MB) | 972 ms (2 MB) | 38.49 s (11 MB) | 1.2x | 1.1x |
| fasta | 2,500,000 | 683 ms (2 MB) | 1.36 s (90 MB) | 356 ms (1 MB) | 317 ms (2 MB) | 4.36 s (108 MB) | 1.9x | 2.2x |
| knucleotide | 2,500,000 | 16.79 s (270 MB) | 22.07 s (421 MB) | 718 ms (29 MB) | 2.35 s (59 MB) | 13.22 s (63 MB) | 23.4x | 7.2x |
| revcomp | 2,500,000 | 2.52 s (638 MB) | 2.01 s (364 MB) | 57 ms (13 MB) | 176 ms (41 MB) | 313 ms (65 MB) | 43.9x | 14.3x |
| regexredux | 100,000 | 351 ms (13 MB) | 189 ms (89 MB) | 48 ms (9 MB) | 657 ms (9 MB) | 240 ms (21 MB) | 7.3x | 0.5x |

### Official Benchmarks Game sizes (compiled languages, 1 run)

| Program | size (N) | Klar --release | Klar on Node | C (gcc -O2) | Go | Klar / C | Klar / Go |
|---|---|---|---|---|---|---|---|
| nbody | 50,000,000 | 10.99 s (2 MB) | 10.81 s (80 MB) | 2.28 s (2 MB) | 3.84 s (2 MB) | 4.8x | 2.9x |
| spectralnorm | 5,500 | 2.65 s (4 MB) | 10.92 s (82 MB) | 1.58 s (2 MB) | 1.50 s (2 MB) | 1.7x | 1.8x |
| fannkuchredux | 12 | 96.88 s (2 MB) | 148.42 s (91 MB) | 32.45 s (2 MB) | 34.88 s (2 MB) | 3.0x | 2.8x |
| binarytrees | 21 | 26.68 s (282 MB) | 57.07 s (2701 MB) | 12.84 s (257 MB) | 17.85 s (242 MB) | 2.1x | 1.5x |
| mandelbrot | 16,000 | 15.33 s (2 MB) | 21.05 s (82 MB) | 14.40 s (2 MB) | 15.00 s (2 MB) | 1.1x | 1.0x |
| fasta | 25,000,000 | 6.56 s (2 MB) | 12.25 s (106 MB) | 3.58 s (1 MB) | 3.71 s (2 MB) | 1.8x | 1.8x |
| knucleotide | 25,000,000 | 187.78 s (786 MB) | 296.12 s (869 MB) | 7.20 s (137 MB) | 20.72 s (498 MB) | 26.1x | 9.1x |
| revcomp | 25,000,000 | 22.81 s (6383 MB) | 25.61 s (2547 MB) | 567 ms (121 MB) | 1.55 s (289 MB) | 40.2x | 14.7x |
| regexredux | 5,000,000 | 14.23 s (334 MB) | 4.19 s (819 MB) | 1.72 s (315 MB) | 31.53 s (355 MB) | 8.3x | 0.5x |
