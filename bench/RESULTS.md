# Klar benchmarks

Machine: 4 CPUs, Linux. Go go1.24.7, gcc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0, Python 3.13.18.
Wall-clock medians. Compile time includes everything from source file to executable (for Klar: starting Python, parsing, type checking, generating C and running the C compiler). Go's cold-cache build (first build after installing Go or clearing its cache) is measured once.

## Run time

| Benchmark | Klar compiled (gcc -O2) | Klar compiled (tcc) | C (gcc -O2) | Go | Python 3 | Klar interpreter |
|---|---|---|---|---|---|---|
| fib | 14 ms | 91 ms | 7 ms | 14 ms | 361 ms | 31.99 s |
| loop | 108 ms | 1.34 s | 104 ms | 117 ms | 10.71 s | > 120 s |
| mandel | 108 ms | 273 ms | 95 ms | 106 ms | 2.71 s | > 120 s |
| lists | 280 ms | 339 ms | 63 ms | 488 ms | 964 ms | > 120 s |
| words | 492 ms | 728 ms | 146 ms | 217 ms | 863 ms | 73.71 s |

## Compile time

| Benchmark | Klar (tcc) | Klar --release (gcc -O2) | Go (warm cache) | Go (cold cache) | C (gcc -O2) | C (tcc) |
|---|---|---|---|---|---|---|
| fib | 44 ms | 148 ms | 48 ms | 4.63 s | 92 ms | 6 ms |
| loop | 41 ms | 116 ms | 42 ms | n/a | 44 ms | 4 ms |
| mandel | 47 ms | 141 ms | 49 ms | n/a | 49 ms | 5 ms |
| lists | 39 ms | 178 ms | 45 ms | n/a | 75 ms | 6 ms |
| words | 50 ms | 241 ms | 43 ms | n/a | 83 ms | 6 ms |

Programs: `fib` = recursive fib(32); `loop` = 100M iterations of integer arithmetic; `mandel` = 1000x1000 Mandelbrot, 100 iterations; `lists` = push 5M ints, filter, map, fold; `words` = count 2M words from a 5,000-word vocabulary in a map.
