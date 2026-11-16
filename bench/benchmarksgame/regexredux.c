/* regex-redux, single-threaded, plain C with PCRE2 (Benchmarks Game algorithm). Reads fasta output on stdin. */
#define PCRE2_CODE_UNIT_WIDTH 8
#include <pcre2.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef struct { char *p; size_t len, cap; } Buf;
static void add(Buf *b, const char *s, size_t n) {
  if (b->len + n > b->cap) { while (b->len + n > b->cap) b->cap = b->cap ? b->cap * 2 : 1024; b->p = realloc(b->p, b->cap); }
  memcpy(b->p + b->len, s, n); b->len += n;
}
static pcre2_code *compile(const char *pat) {
  int ec; PCRE2_SIZE off;
  pcre2_code *re = pcre2_compile((PCRE2_SPTR)pat, PCRE2_ZERO_TERMINATED, 0, &ec, &off, NULL);
  pcre2_jit_compile(re, PCRE2_JIT_COMPLETE);
  return re;
}
static Buf replace(const char *pat, Buf in, const char *rep) {
  pcre2_code *re = compile(pat); pcre2_match_data *md = pcre2_match_data_create_from_pattern(re, NULL);
  Buf out = {0}; size_t pos = 0, rl = strlen(rep);
  while (pos <= in.len && pcre2_match(re, (PCRE2_SPTR)in.p, in.len, pos, 0, md, NULL) > 0) {
    PCRE2_SIZE *o = pcre2_get_ovector_pointer(md);
    add(&out, in.p + pos, o[0] - pos); add(&out, rep, rl); pos = o[1] > o[0] ? o[1] : o[1] + 1;
  }
  if (pos < in.len) add(&out, in.p + pos, in.len - pos);
  pcre2_match_data_free(md); pcre2_code_free(re);
  return out;
}
static long count(const char *pat, Buf in) {
  pcre2_code *re = compile(pat); pcre2_match_data *md = pcre2_match_data_create_from_pattern(re, NULL);
  long n = 0; size_t pos = 0;
  while (pos <= in.len && pcre2_match(re, (PCRE2_SPTR)in.p, in.len, pos, 0, md, NULL) > 0) {
    PCRE2_SIZE *o = pcre2_get_ovector_pointer(md); n++; pos = o[1] > o[0] ? o[1] : o[1] + 1;
  }
  pcre2_match_data_free(md); pcre2_code_free(re);
  return n;
}
int main(void) {
  Buf in = {0}; char chunk[65536]; size_t r;
  while ((r = fread(chunk, 1, sizeof chunk, stdin)) > 0) add(&in, chunk, r);
  Buf seq = replace(">.*\n|\n", in, "");
  const char *v[] = {"agggtaaa|tttaccct", "[cgt]gggtaaa|tttaccc[acg]", "a[act]ggtaaa|tttacc[agt]t",
    "ag[act]gtaaa|tttac[agt]ct", "agg[act]taaa|ttta[agt]cct", "aggg[acg]aaa|ttt[cgt]ccct",
    "agggt[cgt]aa|tt[acg]accct", "agggta[cgt]a|t[acg]taccct", "agggtaa[cgt]|[acg]ttaccct"};
  for (int i = 0; i < 9; i++) printf("%s %ld\n", v[i], count(v[i], seq));
  const char *s[][2] = {{"tHa[Nt]", "<4>"}, {"aND|caN|Ha[DS]|WaS", "<3>"}, {"a[NSt]|BY", "<2>"},
    {"<[^>]*>", "|"}, {"\\|[^|][^|]*\\|", "-"}};
  Buf out = seq;
  for (int i = 0; i < 5; i++) out = replace(s[i][0], out, s[i][1]);
  printf("\n%zu\n%zu\n%zu\n", in.len, seq.len, out.len);
  return 0;
}
