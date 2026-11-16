#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
/* open-addressing hash map from string to count, insertion-ordered via a key array */
#define CAP 16384
static char *keys[CAP]; static int64_t vals[CAP]; static int64_t order[CAP]; static int norder;
static uint64_t h(const char *s) { uint64_t x = 1469598103934665603ULL; while (*s) { x ^= (unsigned char)*s++; x *= 1099511628211ULL; } return x; }
int main(void) {
  int64_t seed = 42; char buf[32];
  for (int i = 0; i < 2000000; i++) {
    seed = (seed * 1103515245 + 12345) % 2147483648LL;
    snprintf(buf, sizeof buf, "w%lld", (long long)(seed % 5000));
    uint64_t j = h(buf) & (CAP - 1);
    while (keys[j] && strcmp(keys[j], buf)) j = (j + 1) & (CAP - 1);
    if (!keys[j]) { keys[j] = strdup(buf); order[norder++] = j; }
    vals[j]++;
  }
  const char *best = ""; int64_t bn = 0;
  for (int i = 0; i < norder; i++) if (vals[order[i]] > bn) { bn = vals[order[i]]; best = keys[order[i]]; }
  printf("%d %s %lld\n", norder, best, (long long)bn);
  return 0;
}
