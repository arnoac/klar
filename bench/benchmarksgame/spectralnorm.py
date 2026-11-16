"""spectral-norm, single-threaded, plain Python (Benchmarks Game algorithm)."""
import sys
from math import sqrt


def a(i, j):
    return 1.0 / ((i + j) * (i + j + 1) // 2 + i + 1)


def mul_av(v):
    n = len(v)
    return [sum(a(i, j) * v[j] for j in range(n)) for i in range(n)]


def mul_atv(v):
    n = len(v)
    return [sum(a(j, i) * v[j] for j in range(n)) for i in range(n)]


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    u = [1.0] * n
    for _ in range(10):
        v = mul_atv(mul_av(u))
        u = mul_atv(mul_av(v))
    vbv = sum(x * y for x, y in zip(u, v))
    vv = sum(x * x for x in v)
    print(f"{sqrt(vbv / vv):.9f}")


main()
