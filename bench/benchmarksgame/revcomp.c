/* reverse-complement, single-threaded, plain C (Benchmarks Game algorithm). Reads fasta output on stdin. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static char comp[256];
static char *seq; static size_t len, cap;
static void flush(const char *header) {
  if (!header[0]) return;
  fputs(header, stdout);
  char line[61]; int n = 0;
  for (size_t i = len; i-- > 0;) {
    line[n++] = comp[(unsigned char)seq[i]];
    if (n == 60) { line[n++] = '\n'; fwrite(line, 1, n, stdout); n = 0; }
  }
  if (n) { line[n++] = '\n'; fwrite(line, 1, n, stdout); }
  len = 0;
}
int main(void) {
  const char *from = "ACBDGHKMNSRUTWVYacbdghkmnsrutwvy", *to = "TGVHCDMKNSYAAWBRTGVHCDMKNSYAAWBR";
  for (int i = 0; from[i]; i++) comp[(unsigned char)from[i]] = to[i];
  char header[4096] = "", line[4096];
  cap = 1 << 20; seq = malloc(cap);
  while (fgets(line, sizeof line, stdin)) {
    if (line[0] == '>') { flush(header); strcpy(header, line); continue; }
    size_t n = strcspn(line, "\n");
    if (len + n > cap) { while (len + n > cap) cap *= 2; seq = realloc(seq, cap); }
    memcpy(seq + len, line, n); len += n;
  }
  flush(header);
  return 0;
}
