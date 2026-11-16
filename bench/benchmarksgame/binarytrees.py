"""binary-trees, single-threaded, plain Python (Benchmarks Game algorithm)."""
import sys


def bottom_up(d):
    return (bottom_up(d - 1), bottom_up(d - 1)) if d > 0 else (None, None)


def check(t):
    l, r = t
    return 1 if l is None else 1 + check(l) + check(r)


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    min_d = 4
    max_d = max(min_d + 2, n)
    print(f"stretch tree of depth {max_d + 1}\t check: {check(bottom_up(max_d + 1))}")
    ll = bottom_up(max_d)
    for d in range(min_d, max_d + 1, 2):
        it = 1 << (max_d - d + min_d)
        tot = sum(check(bottom_up(d)) for _ in range(it))
        print(f"{it}\t trees of depth {d}\t check: {tot}")
    print(f"long lived tree of depth {max_d}\t check: {check(ll)}")


main()
