# `klar run` speed

Machine: 4 CPUs, Linux. Python 3.13.18. `klar run` compiles the checked program to Python code and runs that (klar/pygen.py). Wall-clock time including startup (parsing, type checking, code generation), median of 3 runs. Every program's output is checked to equal the Python version's. Programs and sizes: bench/*.klar (as in RESULTS.md) and bench/benchmarksgame at the sizes shown; knucleotide, revcomp and regexredux read the output of `fasta N`.

| Program | size (N) | Python 3 | klar run | klar run before (tree-walking evaluator, 1 run) | speed-up | klar run / Python |
|---|---|---|---|---|---|---|
| fib | as in source | 198 ms | 270 ms | 25.11 s | 93x | 1.4x |
| loop | as in source | 7.23 s | 9.61 s | 227.94 s | 24x | 1.3x |
| mandel | as in source | 1.83 s | 2.57 s | 280.39 s | 109x | 1.4x |
| lists | as in source | 718 ms | 946 ms | 22.53 s | 24x | 1.3x |
| words | as in source | 683 ms | 1.34 s | 17.10 s | 13x | 2.0x |
| nbody | 100,000 | 289 ms | 1.20 s | 40.71 s | 34x | 4.1x |
| spectralnorm | 300 | 512 ms | 829 ms | 35.31 s | 43x | 1.6x |
| fannkuchredux | 9 | 297 ms | 1.50 s | 55.10 s | 37x | 5.0x |
| binarytrees | 15 | 443 ms | 2.85 s | 94.42 s | 33x | 6.4x |
| mandelbrot | 1,000 | 2.16 s | 2.17 s | 227.90 s | 105x | 1.0x |
| fasta | 250,000 | 393 ms | 1.58 s | 45.20 s | 29x | 4.0x |
| knucleotide | 250,000 | 1.16 s | 4.54 s | 68.58 s | 15x | 3.9x |
| revcomp | 250,000 | 44 ms | 1.53 s | 15.62 s | 10x | 34.8x |
| regexredux | 100,000 | 217 ms | 218 ms | 224 ms | 1x | 1.0x |
