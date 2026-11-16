"""mandelbrot, single-threaded, plain Python (Benchmarks Game algorithm); prints a checksum of the PBM bytes."""
import sys


def main():
    w = h = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    hsh = bits = nbits = 0
    for y in range(h):
        ci = 2.0 * y / h - 1.0
        for x in range(w):
            cr = 2.0 * x / w - 1.5
            zr = zi = tr = ti = 0.0
            i = 0
            while i < 50 and tr + ti <= 4.0:
                zi = 2.0 * zr * zi + ci
                zr = tr - ti + cr
                tr = zr * zr
                ti = zi * zi
                i += 1
            bits = bits * 2 + (1 if tr + ti <= 4.0 else 0)
            nbits += 1
            if nbits == 8 or x == w - 1:
                bits <<= 8 - nbits
                hsh = (hsh * 31 + bits) % 1000000007
                bits = nbits = 0
    print(f"P4\n{w} {h}\n{hsh}")


main()
