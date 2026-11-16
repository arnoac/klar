/* binary-trees, single-threaded, plain C with malloc/free (Benchmarks Game algorithm). */
#include <stdio.h>
#include <stdlib.h>
typedef struct Node { struct Node *l, *r; } Node;
static Node *bottom_up(int d) {
  Node *n = malloc(sizeof(Node));
  if (d > 0) { n->l = bottom_up(d - 1); n->r = bottom_up(d - 1); } else n->l = n->r = NULL;
  return n;
}
static long check(Node *n) { return n->l ? 1 + check(n->l) + check(n->r) : 1; }
static void del(Node *n) { if (n->l) { del(n->l); del(n->r); } free(n); }
int main(int argc, char **argv) {
  int n = argc > 1 ? atoi(argv[1]) : 10, min = 4, max = min + 2 > n ? min + 2 : n;
  Node *s = bottom_up(max + 1);
  printf("stretch tree of depth %d\t check: %ld\n", max + 1, check(s));
  del(s);
  Node *ll = bottom_up(max);
  for (int d = min; d <= max; d += 2) {
    long it = 1L << (max - d + min), tot = 0;
    for (long i = 0; i < it; i++) { Node *t = bottom_up(d); tot += check(t); del(t); }
    printf("%ld\t trees of depth %d\t check: %ld\n", it, d, tot);
  }
  printf("long lived tree of depth %d\t check: %ld\n", max, check(ll));
  return 0;
}
