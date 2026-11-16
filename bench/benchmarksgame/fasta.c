/* fasta, single-threaded, plain C (Benchmarks Game algorithm). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static const char *ALU = "GGCCGGGCGCGGTGGCTCACGCCTGTAATCCCAGCACTTTGGGAGGCCGAGGCGGGCGGATCACCTGAGGTCAGGAGTTCGAGACCAGCCTGGCCAACATGGTGAAACCCCGTCTCTACTAAAAATACAAAAATTAGCCGGGCGTGGTGGCGCGCGCCTGTAATCCCAGCTACTCGGGAGGCTGAGGCAGGAGAATCGCTTGAACCCGGGAGGCGGAGGTTGCAGTGAGCCGAGATCGCGCCACTGCACTCCAGCCTGGGCGACAGAGCGAGACTCCGTCTCAAAAA";
static long seed = 42;
static double rnd(void) { seed = (seed * 3877 + 29573) % 139968; return (double)seed / 139968.0; }
static void repeat(const char *hdr, const char *s, int n) {
  int k = strlen(s), pos = 0;
  char line[61];
  puts(hdr);
  while (n > 0) {
    int m = n < 60 ? n : 60;
    for (int i = 0; i < m; i++) line[i] = s[(pos + i) % k];
    line[m] = 0; puts(line);
    pos = (pos + m) % k; n -= m;
  }
}
static void randomf(const char *hdr, const char *chars, const double *p, int cnt, int n) {
  double cum[16], acc = 0;
  char line[61];
  for (int i = 0; i < cnt; i++) { acc += p[i]; cum[i] = acc; }
  puts(hdr);
  while (n > 0) {
    int m = n < 60 ? n : 60;
    for (int i = 0; i < m; i++) {
      double r = rnd(); int j = 0;
      while (j < cnt - 1 && cum[j] <= r) j++;
      line[i] = chars[j];
    }
    line[m] = 0; puts(line); n -= m;
  }
}
int main(int argc, char **argv) {
  int n = argc > 1 ? atoi(argv[1]) : 1000;
  static const double iub[] = {0.27, 0.12, 0.12, 0.27, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02};
  static const double hs[] = {0.3029549426680, 0.1979883004921, 0.1975473066391, 0.3015094502008};
  repeat(">ONE Homo sapiens alu", ALU, n * 2);
  randomf(">TWO IUB ambiguity codes", "acgtBDHKMNRSVWY", iub, 15, n * 3);
  randomf(">THREE Homo sapiens frequency", "acgt", hs, 4, n * 5);
  return 0;
}
