/* mandelbrot, single-threaded, plain C (Benchmarks Game algorithm); prints a checksum of the PBM bytes. */
#include <stdio.h>
#include <stdlib.h>
int main(int argc, char **argv) {
  int w = argc > 1 ? atoi(argv[1]) : 200, h = w, bits = 0, nbits = 0;
  long hash = 0;
  for (int y = 0; y < h; y++) {
    double ci = 2.0 * y / h - 1.0;
    for (int x = 0; x < w; x++) {
      double cr = 2.0 * x / w - 1.5, zr = 0, zi = 0, tr = 0, ti = 0;
      for (int i = 0; i < 50 && tr + ti <= 4.0; i++) { zi = 2.0 * zr * zi + ci; zr = tr - ti + cr; tr = zr * zr; ti = zi * zi; }
      bits = bits * 2 + (tr + ti <= 4.0);
      if (++nbits == 8 || x == w - 1) {
        while (nbits < 8) { bits *= 2; nbits++; }
        hash = (hash * 31 + bits) % 1000000007;
        bits = nbits = 0;
      }
    }
  }
  printf("P4\n%d %d\n%ld\n", w, h, hash);
  return 0;
}
