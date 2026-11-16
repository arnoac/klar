#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
int main(void) {
  int64_t n = 0, cap = 4; int64_t *xs = malloc(cap * 8);
  for (int64_t i = 0; i < 5000000; i++) { if (n == cap) { cap *= 2; xs = realloc(xs, cap * 8); } xs[n++] = i; }
  int64_t *ev = malloc(n * 8), ne = 0;
  for (int64_t i = 0; i < n; i++) if (xs[i] % 2 == 0) ev[ne++] = xs[i];
  int64_t *sq = malloc(ne * 8);
  for (int64_t i = 0; i < ne; i++) sq[i] = ev[i] % 1000 * 3;
  int64_t acc = 0;
  for (int64_t i = 0; i < ne; i++) acc += sq[i];
  printf("%lld\n", (long long)acc);
  return 0;
}
