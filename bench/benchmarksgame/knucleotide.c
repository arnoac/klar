/* k-nucleotide, single-threaded, plain C (Benchmarks Game algorithm). Reads fasta output on stdin.
 * k-mers are packed 2 bits per base into a 64-bit key and counted in an open-addressing hash table. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef struct { uint64_t key; int64_t n; } Slot;
typedef struct { Slot *s; uint64_t cap, used; } Table;
static int code(char c) { return c == 'A' ? 0 : c == 'C' ? 1 : c == 'G' ? 2 : 3; }
static void tinit(Table *t, uint64_t cap) { t->cap = cap; t->used = 0; t->s = calloc(cap, sizeof(Slot)); }
static Slot *tfind(Table *t, uint64_t key) {   /* key + 1 so that 0 marks an empty slot */
  uint64_t h = (key + 1) * 0x9E3779B97F4A7C15ULL, i = h & (t->cap - 1);
  while (t->s[i].key && t->s[i].key != key + 1) i = (i + 1) & (t->cap - 1);
  return &t->s[i];
}
static void tinc(Table *t, uint64_t key) {
  if (t->used * 2 >= t->cap) {
    Table n; tinit(&n, t->cap * 2);
    for (uint64_t i = 0; i < t->cap; i++) if (t->s[i].key) { Slot *d = tfind(&n, t->s[i].key - 1); *d = t->s[i]; n.used++; }
    free(t->s); *t = n;
  }
  Slot *s = tfind(t, key);
  if (!s->key) { s->key = key + 1; t->used++; }
  s->n++;
}
static Table count(const char *seq, long len, int k) {
  Table t; tinit(&t, 1024);
  uint64_t mask = k == 32 ? ~0ULL : (1ULL << (2 * k)) - 1, key = 0;
  for (long i = 0; i < len; i++) {
    key = ((key << 2) | code(seq[i])) & mask;
    if (i >= k - 1) tinc(&t, key);
  }
  return t;
}
static void decode(uint64_t key, int k, char *out) {
  for (int i = k - 1; i >= 0; i--) { out[i] = "ACGT"[key & 3]; key >>= 2; }
  out[k] = 0;
}
typedef struct { char s[4]; int64_t n; } Freq;
static int cmp(const void *a, const void *b) {
  const Freq *x = a, *y = b;
  if (x->n != y->n) return x->n < y->n ? 1 : -1;
  return strcmp(x->s, y->s);
}
int main(void) {
  size_t cap = 1 << 20, len = 0; char *seq = malloc(cap), line[4096]; int inside = 0;
  while (fgets(line, sizeof line, stdin)) {
    if (inside) {
      if (line[0] == '>') break;
      for (char *p = line; *p && *p != '\n'; p++) {
        if (len == cap) seq = realloc(seq, cap *= 2);
        seq[len++] = *p >= 'a' ? *p - 32 : *p;
      }
    } else if (strncmp(line, ">THREE", 6) == 0) inside = 1;
  }
  for (int k = 1; k <= 2; k++) {
    Table t = count(seq, len, k); Freq f[16]; int n = 0;
    for (uint64_t i = 0; i < t.cap; i++) if (t.s[i].key) { decode(t.s[i].key - 1, k, f[n].s); f[n++].n = t.s[i].n; }
    qsort(f, n, sizeof(Freq), cmp);
    for (int i = 0; i < n; i++) printf("%s %.3f\n", f[i].s, 100.0 * f[i].n / (len - k + 1));
    printf("\n");
  }
  const char *q[] = {"GGT", "GGTA", "GGTATT", "GGTATTTTAATT", "GGTATTTTAATTTATAGT"};
  for (int j = 0; j < 5; j++) {
    int k = strlen(q[j]); uint64_t key = 0;
    for (int i = 0; i < k; i++) key = (key << 2) | code(q[j][i]);
    Table t = count(seq, len, k); Slot *s = tfind(&t, key);
    printf("%lld\t%s\n", (long long)(s->key ? s->n : 0), q[j]);
    free(t.s);
  }
  return 0;
}
