/* Klar runtime for compiled programs. Included verbatim at the top of every generated C file.
 *
 * Every Klar value fits in one 64-bit slot (KS): int, float (bit pattern), bool, or a pointer to an
 * immutable heap object (str, list, map, struct, enum, closure). Memory is managed by the Boehm GC
 * when KLAR_GC is defined; otherwise it is never freed (fine for short-lived programs).
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <stdarg.h>
#include <math.h>
#include <errno.h>
#include <signal.h>
#include <unistd.h>
#include <sys/resource.h>

/* Every allocation is checked: running out of memory stops the program with an error instead of handing
 * back NULL. Sizes are checked before they are computed (k_size), so they cannot wrap around. */
#define K_MAX_ALLOC ((int64_t)1 << 44)
__attribute__((noreturn, cold)) static void k_oom(int64_t n) {
    fflush(stdout);
    fprintf(stderr, "error[E407]: out of memory (asked for a value of size %lld)\n", (long long)n);
    exit(1);
}
static inline void *k_nn(void *p, int64_t n) { if (__builtin_expect(!p, 0)) k_oom(n); return p; }
/* n items of `each` bytes plus `extra`, refusing sizes that would overflow */
static inline int64_t k_size(int64_t n, int64_t each, int64_t extra) {
    if (n < 0 || n > (K_MAX_ALLOC - extra) / each) k_oom(n);
    return n * each + extra;
}
#ifdef KLAR_GC
#include <gc.h>
#define K_ALLOC(n) k_nn(GC_MALLOC(n), (int64_t)(n))
#define K_ALLOC_ATOMIC(n) k_nn(GC_MALLOC_ATOMIC(n), (int64_t)(n))
#define K_REALLOC(p, n) k_nn(GC_REALLOC(p, n), (int64_t)(n))
#else
#define K_ALLOC(n) k_nn(malloc(n), (int64_t)(n))
#define K_ALLOC_ATOMIC(n) k_nn(malloc(n), (int64_t)(n))
#define K_REALLOC(p, n) k_nn(realloc(p, n), (int64_t)(n))
#endif

typedef uint64_t KS;
typedef int64_t KUnit;

static inline KS k_i2s(int64_t x) { return (KS)x; }
static inline int64_t k_s2i(KS s) { return (int64_t)s; }
static inline KS k_d2s(double d) { KS s; memcpy(&s, &d, 8); return s; }
static inline double k_s2d(KS s) { double d; memcpy(&d, &s, 8); return d; }
static inline KS k_p2s(const void *p) { return (KS)(uintptr_t)p; }
#define K_S2P(T, s) ((T)(uintptr_t)(s))

/* ------------------------------------------------------------------ errors */
static const char *k_file = "<input>";
static int k_argc;
static char **k_argv;

/* Set by the HTTP server: a runtime error inside a request handler answers that request with 500 (the hook
 * jumps back to the server) instead of ending the whole server. Elsewhere it returns and the program exits. */
static void (*k_fail_hook)(void);
__attribute__((noreturn, cold)) static void k_fail(const char *code, int line, int col, const char *fmt, ...) {
    fflush(stdout);
    fprintf(stderr, "error[%s] %s:%d:%d: ", code, k_file, line, col);
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fprintf(stderr, "\n");
    if (k_fail_hook) k_fail_hook();
    exit(1);
}

static char k_altstack[65536];
static void k_segv(int sig) {
    (void)sig;
    static const char msg[] = "error[E406] stack overflow (recursion too deep) or invalid memory access\n";
    fflush(stdout);
    if (write(2, msg, sizeof msg - 1) < 0) {}
    _exit(1);
}

static void k_init(int argc, char **argv) {
    /* Re-exec once with a large stack so deep recursion works like in the interpreter. */
    struct rlimit rl;
    if (getrlimit(RLIMIT_STACK, &rl) == 0 && rl.rlim_cur != RLIM_INFINITY && rl.rlim_cur < (rlim_t)1 << 30
        && !getenv("KLAR_STACK_SET")) {
        rlim_t want = (rlim_t)1 << 30;
        if (rl.rlim_max != RLIM_INFINITY && rl.rlim_max < want) want = rl.rlim_max;
        if (want > rl.rlim_cur) {
            rl.rlim_cur = want;
            if (setrlimit(RLIMIT_STACK, &rl) == 0) {
                setenv("KLAR_STACK_SET", "1", 1);
                execv("/proc/self/exe", argv);
            }
        }
    }
    k_argc = argc;
    k_argv = argv;
#ifdef KLAR_GC
    GC_INIT();
#endif
    stack_t ss;
    ss.ss_sp = k_altstack;
    ss.ss_size = sizeof k_altstack;
    ss.ss_flags = 0;
    sigaltstack(&ss, NULL);
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = k_segv;
    sa.sa_flags = SA_ONSTACK;
    sigaction(SIGSEGV, &sa, NULL);
    static char outbuf[1 << 16];
    setvbuf(stdout, outbuf, _IOFBF, sizeof outbuf);
}

/* ------------------------------------------------------------------ checked integer arithmetic */
#if defined(__GNUC__) && !defined(__TINYC__)
#define K_ADD_OVF(a, b, r) __builtin_add_overflow(a, b, r)
#define K_SUB_OVF(a, b, r) __builtin_sub_overflow(a, b, r)
#ifdef KLAR_WASM
#define K_MUL_OVF(a, b, r) k_wasm_mul_ovf(a, b, r)   /* see klar_wasm.h */
#else
#define K_MUL_OVF(a, b, r) __builtin_mul_overflow(a, b, r)
#endif
#else
static inline int K_ADD_OVF(int64_t a, int64_t b, int64_t *r) {
    if ((b > 0 && a > INT64_MAX - b) || (b < 0 && a < INT64_MIN - b)) return 1;
    *r = a + b; return 0;
}
static inline int K_SUB_OVF(int64_t a, int64_t b, int64_t *r) {
    if ((b < 0 && a > INT64_MAX + b) || (b > 0 && a < INT64_MIN + b)) return 1;
    *r = a - b; return 0;
}
static inline int K_MUL_OVF(int64_t a, int64_t b, int64_t *r) {
    if (a == 0 || b == 0) { *r = 0; return 0; }
    if ((a == -1 && b == INT64_MIN) || (b == -1 && a == INT64_MIN)) return 1;
    if (a > 0 ? (b > 0 ? a > INT64_MAX / b : b < INT64_MIN / a)
              : (b > 0 ? a < INT64_MIN / b : a < INT64_MAX / b)) return 1;
    *r = a * b; return 0;
}
#endif

#define K_OVF_MSG "integer overflow: compiled Klar ints are 64-bit (the interpreter has no limit)"
static inline int64_t k_add(int64_t a, int64_t b, int l, int c) {
    int64_t r; if (K_ADD_OVF(a, b, &r)) k_fail("E408", l, c, K_OVF_MSG); return r;
}
static inline int64_t k_sub(int64_t a, int64_t b, int l, int c) {
    int64_t r; if (K_SUB_OVF(a, b, &r)) k_fail("E408", l, c, K_OVF_MSG); return r;
}
static inline int64_t k_mul(int64_t a, int64_t b, int l, int c) {
    int64_t r; if (K_MUL_OVF(a, b, &r)) k_fail("E408", l, c, K_OVF_MSG); return r;
}
static inline int64_t k_neg(int64_t a, int l, int c) {
    if (a == INT64_MIN) k_fail("E408", l, c, K_OVF_MSG); return -a;
}
static inline int64_t k_div(int64_t a, int64_t b, int l, int c) {
    if (b == 0) k_fail("E402", l, c, "integer division by zero");
    if (a == INT64_MIN && b == -1) k_fail("E408", l, c, K_OVF_MSG);
    return a / b;
}
static inline int64_t k_rem(int64_t a, int64_t b, int l, int c) {
    if (b == 0) k_fail("E402", l, c, "remainder by zero");
    if (b == -1) return 0;
    return a % b;
}

/* ------------------------------------------------------------------ strings */
typedef struct KStr { int64_t len; int64_t cps; char data[]; } KStr;   /* cps: code points, -1 = unknown */

static KStr *k_str_alloc(int64_t n) {
    KStr *s = (KStr *)K_ALLOC_ATOMIC(k_size(n, 1, sizeof(KStr) + 1));
    s->len = n; s->cps = -1; s->data[n] = 0;
    return s;
}
static KStr *k_str_new(const char *p, int64_t n) {
    KStr *s = k_str_alloc(n);
    memcpy(s->data, p, n);
    return s;
}
static KStr *k_cstr(const char *p) { return k_str_new(p, (int64_t)strlen(p)); }

static int64_t k_str_cps(KStr *s) {
    if (s->cps < 0) {
        int64_t n = 0;
        for (int64_t i = 0; i < s->len; i++) if (((unsigned char)s->data[i] & 0xC0) != 0x80) n++;
        s->cps = n;
    }
    return s->cps;
}
/* byte offset of code point index i (0 <= i <= cps) */
static int64_t k_str_off(KStr *s, int64_t i) {
    if (k_str_cps(s) == s->len) return i;
    int64_t n = 0, b = 0;
    for (; b < s->len; b++) {
        if (((unsigned char)s->data[b] & 0xC0) != 0x80) { if (n == i) return b; n++; }
    }
    return s->len;
}
static inline int k_str_eq(KStr *a, KStr *b) { return a == b || (a->len == b->len && memcmp(a->data, b->data, a->len) == 0); }
static int64_t k_str_cmp(KStr *a, KStr *b) {
    int64_t n = a->len < b->len ? a->len : b->len;
    int r = memcmp(a->data, b->data, n);
    if (r) return r;
    return a->len < b->len ? -1 : a->len > b->len;
}
static KStr *k_str_concat(KStr *a, KStr *b) {
    KStr *s = k_str_alloc(a->len + b->len);
    memcpy(s->data, a->data, a->len);
    memcpy(s->data + a->len, b->data, b->len);
    return s;
}

/* string builder */
typedef struct { char *p; int64_t len, cap; } KSB;
static inline void ksb_init(KSB *b) { b->cap = 32; b->len = 0; b->p = (char *)K_ALLOC_ATOMIC(b->cap); }
static void ksb_add(KSB *b, const char *s, int64_t n) {
    if (b->len + n > b->cap) {
        k_size(b->len + n, 2, 0);
        while (b->len + n > b->cap) b->cap *= 2;
        b->p = (char *)K_REALLOC(b->p, b->cap);
    }
    memcpy(b->p + b->len, s, n);
    b->len += n;
}
static inline void ksb_str(KSB *b, KStr *s) { ksb_add(b, s->data, s->len); }
static inline void ksb_c(KSB *b, const char *s) { ksb_add(b, s, (int64_t)strlen(s)); }
static KStr *ksb_done(KSB *b) { return k_str_new(b->p, b->len); }

static void ksb_int(KSB *b, int64_t v) { char t[32]; int n = snprintf(t, sizeof t, "%lld", (long long)v); ksb_add(b, t, n); }

/* Python-compatible repr() of a float: shortest round-trip digits. */
static void k_fmt_float(char *out, double d) {
    if (isnan(d)) { strcpy(out, "nan"); return; }
    if (isinf(d)) { strcpy(out, d > 0 ? "inf" : "-inf"); return; }
    if (d == 0) { strcpy(out, signbit(d) ? "-0.0" : "0.0"); return; }
    char tmp[64];
    int p;
    for (p = 1; p <= 17; p++) {
        snprintf(tmp, sizeof tmp, "%.*e", p - 1, d);
        if (strtod(tmp, NULL) == d) break;
    }
    char digits[32]; int nd = 0; int neg = 0; char *q = tmp;
    if (*q == '-') { neg = 1; q++; }
    for (; *q && *q != 'e'; q++) if (*q != '.') digits[nd++] = *q;
    digits[nd] = 0;
    int exp = atoi(q + 1);
    while (nd > 1 && digits[nd - 1] == '0') digits[--nd] = 0;
    char *o = out;
    if (neg) *o++ = '-';
    if (exp >= -4 && exp < 16) {
        if (exp >= 0) {
            for (int i = 0; i <= exp; i++) *o++ = i < nd ? digits[i] : '0';
            *o++ = '.';
            if (nd > exp + 1) { for (int i = exp + 1; i < nd; i++) *o++ = digits[i]; }
            else *o++ = '0';
        } else {
            *o++ = '0'; *o++ = '.';
            for (int i = 0; i < -exp - 1; i++) *o++ = '0';
            for (int i = 0; i < nd; i++) *o++ = digits[i];
        }
        *o = 0;
    } else {
        *o++ = digits[0];
        if (nd > 1) { *o++ = '.'; for (int i = 1; i < nd; i++) *o++ = digits[i]; }
        sprintf(o, "e%c%02d", exp < 0 ? '-' : '+', exp < 0 ? -exp : exp);
    }
}
static void ksb_float(KSB *b, double d) { char t[64]; k_fmt_float(t, d); ksb_c(b, t); }
static void ksb_quoted(KSB *b, KStr *s) {
    ksb_add(b, "\"", 1);
    for (int64_t i = 0; i < s->len; i++) {
        char c = s->data[i];
        if (c == '\\') ksb_add(b, "\\\\", 2);
        else if (c == '"') ksb_add(b, "\\\"", 2);
        else if (c == '\n') ksb_add(b, "\\n", 2);
        else ksb_add(b, &c, 1);
    }
    ksb_add(b, "\"", 1);
}

/* ------------------------------------------------------------------ lists
 * A list value is a view (buffer, len, version). Buffers only grow at the end, so pushing onto the newest
 * view of a buffer appends in place (amortised O(1)) while older views keep seeing their own prefix.
 * set_at also works in place on the newest version of a buffer: it records (version, index, old value) in
 * the buffer's undo log and bumps the buffer's version. A view whose version is older than its buffer's is
 * stale; the first time it is read it rebuilds a private copy from the undo log (KL_DATA does the check).
 * So `xs = set_at(xs, i, x)` is O(1) and every older value still reads exactly as it was.
 */
typedef struct { int64_t ver; int64_t index; KS old; } KUndo;
typedef struct { int64_t used, cap; KS *data; int64_t ver; KUndo *log; int64_t nlog, caplog; } KBuf;
typedef struct { KBuf *buf; int64_t len; int64_t ver; } KList;

static KList *k_list_with_cap(int64_t len, int64_t cap) {
    if (cap < 4) cap = 4;
    /* one allocation holds the list header, the buffer header and the first data block */
    KList *l = (KList *)K_ALLOC(k_size(cap, sizeof(KS), sizeof(KList) + sizeof(KBuf)));
    KBuf *b = (KBuf *)(l + 1);
    b->used = len; b->cap = cap; b->ver = 0; b->log = NULL; b->nlog = 0; b->caplog = 0;
    b->data = (KS *)(b + 1);
    l->buf = b; l->len = len; l->ver = 0;
    return l;
}
static void k_buf_grow(KBuf *b) {
    int64_t cap = b->cap * 2;
    k_size(cap, sizeof(KS), 0);
    if (b->data == (KS *)(b + 1)) {
        KS *d = (KS *)K_ALLOC(sizeof(KS) * cap);
        memcpy(d, b->data, sizeof(KS) * b->used);
        b->data = d;
    } else {
        b->data = (KS *)K_REALLOC(b->data, sizeof(KS) * cap);
    }
    b->cap = cap;
}
static KS *k_list_sync(KList *l) {
    /* l is stale: copy its prefix and undo every later set_at, newest first */
    KBuf *b = l->buf;
    KList *fresh = k_list_with_cap(l->len, l->len);
    KS *d = fresh->buf->data;
    memcpy(d, b->data, sizeof(KS) * l->len);
    for (int64_t k = b->nlog - 1; k >= 0 && b->log[k].ver >= l->ver; k--)
        if (b->log[k].index < l->len) d[b->log[k].index] = b->log[k].old;
    l->buf = fresh->buf; l->ver = 0;
    return d;
}
#define KL_DATA(l) (__builtin_expect((l)->ver == (l)->buf->ver, 1) ? (l)->buf->data : k_list_sync(l))
static KList *k_list_new(int64_t n, ...) {
    KList *l = k_list_with_cap(n, n);
    va_list ap; va_start(ap, n);
    for (int64_t i = 0; i < n; i++) l->buf->data[i] = va_arg(ap, KS);
    va_end(ap);
    return l;
}
static inline KS *k_list_data(KList *l) { return KL_DATA(l); }
static KList *k_list_push(KList *l, KS x) {
    KS *src = KL_DATA(l);
    KBuf *b = l->buf;
    if (l->len == b->used) {
        if (b->used == b->cap) k_buf_grow(b);
        b->data[b->used++] = x;
        KList *r = (KList *)K_ALLOC(sizeof(KList));
        r->buf = b; r->len = l->len + 1; r->ver = b->ver;
        return r;
    }
    KList *r = k_list_with_cap(l->len + 1, l->len * 2 + 1);
    memcpy(r->buf->data, src, sizeof(KS) * l->len);
    r->buf->data[l->len] = x;
    return r;
}
static inline KS k_list_get(KList *l, int64_t i, int line, int col) {
    if (i < 0 || i >= l->len) k_fail("E401", line, col, "index %lld is out of bounds for length %lld", (long long)i, (long long)l->len);
    return KL_DATA(l)[i];
}
static KList *k_list_concat(KList *a, KList *b) {
    if (b->len == 0) return a;
    KList *r = k_list_with_cap(a->len + b->len, a->len + b->len);
    memcpy(r->buf->data, KL_DATA(a), sizeof(KS) * a->len);
    memcpy(r->buf->data + a->len, KL_DATA(b), sizeof(KS) * b->len);
    return r;
}
static KList *k_list_set_at(KList *l, int64_t i, KS x, int line, int col) {
    if (i < 0 || i >= l->len) k_fail("E401", line, col, "set_at index %lld is out of bounds for length %lld", (long long)i, (long long)l->len);
    KS *d = KL_DATA(l);
    KBuf *b = l->buf;
    if (b->nlog < b->used + 16) {               /* in place; the log is bounded, so memory stays O(n) */
        if (b->nlog == b->caplog) {
            b->caplog = b->caplog ? b->caplog * 2 : 8;
            b->log = (KUndo *)K_REALLOC(b->log, sizeof(KUndo) * b->caplog);
        }
        b->log[b->nlog].ver = b->ver; b->log[b->nlog].index = i; b->log[b->nlog].old = d[i];
        b->nlog++;
        b->ver++;
        d[i] = x;
        KList *r = (KList *)K_ALLOC(sizeof(KList));
        r->buf = b; r->len = l->len; r->ver = b->ver;
        return r;
    }
    KList *r = k_list_with_cap(l->len, l->len);
    memcpy(r->buf->data, d, sizeof(KS) * l->len);
    r->buf->data[i] = x;
    return r;
}
/* For an owned list variable (see ownership.py): copy into a fresh buffer that only the variable will see. */
static KList *k_list_set_at_fresh(KList *l, int64_t i, KS x, int line, int col) {
    if (i < 0 || i >= l->len) k_fail("E401", line, col, "set_at index %lld is out of bounds for length %lld", (long long)i, (long long)l->len);
    KList *r = k_list_with_cap(l->len, l->len);
    memcpy(r->buf->data, KL_DATA(l), sizeof(KS) * l->len);
    r->buf->data[i] = x;
    return r;
}
static void k_list_push_owned(KList *l, KS x) {
    KBuf *b = l->buf;
    if (b->used == b->cap) k_buf_grow(b);
    b->data[b->used++] = x;
    l->len++;
}
static KList *k_list_slice(KList *l, int64_t a, int64_t b) {
    if (a < 0) a = 0; if (a > l->len) a = l->len;
    if (b < a) b = a; if (b > l->len) b = l->len;
    if (a == 0) { KS *d0 = KL_DATA(l); (void)d0; KList *r = (KList *)K_ALLOC(sizeof(KList)); r->buf = l->buf; r->len = b; r->ver = l->ver; return r; }
    KList *r = k_list_with_cap(b - a, b - a);
    memcpy(r->buf->data, KL_DATA(l) + a, sizeof(KS) * (b - a));
    return r;
}
static KList *k_list_reverse(KList *l) {
    KList *r = k_list_with_cap(l->len, l->len);
    for (int64_t i = 0; i < l->len; i++) r->buf->data[i] = KL_DATA(l)[l->len - 1 - i];
    return r;
}
static KList *k_range(int64_t a, int64_t b) {
    int64_t n = b > a ? b - a : 0;
    KList *r = k_list_with_cap(n, n);
    for (int64_t i = 0; i < n; i++) r->buf->data[i] = (KS)(a + i);
    return r;
}

/* sorting: kind 0 = int, 1 = float, 2 = str */
static int k_cmp_kind(int kind, KS a, KS b) {
    if (kind == 0) { int64_t x = (int64_t)a, y = (int64_t)b; return x < y ? -1 : x > y; }
    if (kind == 1) { double x = k_s2d(a), y = k_s2d(b); return x < y ? -1 : x > y; }
    int64_t c = k_str_cmp(K_S2P(KStr *, a), K_S2P(KStr *, b)); return c < 0 ? -1 : c > 0;
}
/* stable merge sort of vals by keys */
static void k_msort(KS *keys, KS *vals, KS *tk, KS *tv, int64_t n, int kind) {
    if (n < 2) return;
    int64_t m = n / 2;
    k_msort(keys, vals, tk, tv, m, kind);
    k_msort(keys + m, vals + m, tk, tv, n - m, kind);
    int64_t i = 0, j = m, k = 0;
    while (i < m && j < n) {
        if (k_cmp_kind(kind, keys[j], keys[i]) < 0) { tk[k] = keys[j]; tv[k++] = vals[j++]; }
        else { tk[k] = keys[i]; tv[k++] = vals[i++]; }
    }
    while (i < m) { tk[k] = keys[i]; tv[k++] = vals[i++]; }
    while (j < n) { tk[k] = keys[j]; tv[k++] = vals[j++]; }
    memcpy(keys, tk, sizeof(KS) * n);
    memcpy(vals, tv, sizeof(KS) * n);
}
static KList *k_sort_keys(KList *l, KS *keys, int kind) {
    int64_t n = l->len;
    KList *r = k_list_with_cap(n, n);
    memcpy(r->buf->data, KL_DATA(l), sizeof(KS) * n);
    KS *tk = (KS *)malloc(sizeof(KS) * (n + 1)), *tv = (KS *)malloc(sizeof(KS) * (n + 1));
    k_msort(keys, r->buf->data, tk, tv, n, kind);
    free(tk); free(tv);
    return r;
}
static KList *k_sort(KList *l, int kind) {
    KS *keys = (KS *)malloc(sizeof(KS) * (l->len + 1));
    memcpy(keys, KL_DATA(l), sizeof(KS) * l->len);
    KList *r = k_sort_keys(l, keys, kind);
    free(keys);
    return r;
}

/* ------------------------------------------------------------------ enums, structs, closures */
typedef struct KEnum { int64_t tag; KS f[]; } KEnum;
static KEnum *k_enum_new(int64_t tag, int n, ...) {
    KEnum *e = (KEnum *)K_ALLOC(sizeof(KEnum) + sizeof(KS) * n);
    e->tag = tag;
    va_list ap; va_start(ap, n);
    for (int i = 0; i < n; i++) e->f[i] = va_arg(ap, KS);
    va_end(ap);
    return e;
}
#define K_TAG_SOME 0
#define K_TAG_NONE 1
#define K_TAG_OK 2
#define K_TAG_ERR 3
static KEnum k_none_obj = {K_TAG_NONE};
#define K_NONE (&k_none_obj)
static inline KEnum *k_some(KS v) { KEnum *e = (KEnum *)K_ALLOC(sizeof(KEnum) + 8); e->tag = K_TAG_SOME; e->f[0] = v; return e; }
static inline KEnum *k_ok(KS v) { KEnum *e = (KEnum *)K_ALLOC(sizeof(KEnum) + 8); e->tag = K_TAG_OK; e->f[0] = v; return e; }
static inline KEnum *k_err(KS v) { KEnum *e = (KEnum *)K_ALLOC(sizeof(KEnum) + 8); e->tag = K_TAG_ERR; e->f[0] = v; return e; }

static KS *k_struct_new(int n, ...) {
    KS *s = (KS *)K_ALLOC(sizeof(KS) * (n ? n : 1));
    va_list ap; va_start(ap, n);
    for (int i = 0; i < n; i++) s[i] = va_arg(ap, KS);
    va_end(ap);
    return s;
}
static KS *k_struct_copy(KS *s, int n) {
    KS *r = (KS *)K_ALLOC(sizeof(KS) * (n ? n : 1));
    memcpy(r, s, sizeof(KS) * n);
    return r;
}

typedef struct KClo { void *fn; KS env[]; } KClo;
static KClo *k_clo_new(void *fn, int n, ...) {
    KClo *c = (KClo *)K_ALLOC(sizeof(KClo) + sizeof(KS) * n);
    c->fn = fn;
    va_list ap; va_start(ap, n);
    for (int i = 0; i < n; i++) c->env[i] = va_arg(ap, KS);
    va_end(ap);
    return c;
}
typedef KS (*KF0)(KClo *);
typedef KS (*KF1)(KClo *, KS);
typedef KS (*KF2)(KClo *, KS, KS);
typedef KS (*KF3)(KClo *, KS, KS, KS);
typedef KS (*KF4)(KClo *, KS, KS, KS, KS);
#define K_CALL0(c) (((KF0)(c)->fn)(c))
#define K_CALL1(c, a) (((KF1)(c)->fn)(c, a))
#define K_CALL2(c, a, b) (((KF2)(c)->fn)(c, a, b))
#define K_CALL3(c, a, b, d) (((KF3)(c)->fn)(c, a, b, d))
#define K_CALL4(c, a, b, d, e) (((KF4)(c)->fn)(c, a, b, d, e))

/* ------------------------------------------------------------------ maps
 * Insertion-ordered hash map with persistent (immutable) semantics and O(1) amortised updates.
 * Storage is an append-only log of entries shared by many map values ("views"). A view sees the first
 * `len` entries. Updating a key appends a new version linked to the previous one (prev/next), so older
 * views keep seeing their old value. Only the newest view of a log appends in place; any other view
 * copies first. The log is compacted when it holds many stale versions.
 * key kind: 0 = int/bool (slot compared directly), 1 = str.
 */
/* frozen: entries below this index may be visible to map values other than an owned var's (see k_map_put_owned) */
typedef struct { KS *keys, *vals; int64_t *prev, *next; int64_t used, cap; int64_t *index; int64_t icap; int kind; int64_t frozen;
                KS memo_key; int64_t *memo_index; int64_t memo_slot; int64_t memo_tag, last_tag; } KMapData;
/* an index slot holds (entry + 1) in its low 40 bits and the top 24 bits of the key's hash above them, so
 * probing past other keys rarely has to load and compare the key itself */
#define K_IX_ENTRY(v) (((v) & ((INT64_C(1) << 40) - 1)) - 1)
typedef struct { KMapData *d; int64_t len; int64_t count; } KMap;

static inline uint64_t k_hash(int kind, KS k) {
    if (kind == 1) {
        KStr *s = K_S2P(KStr *, k);
        uint64_t h = 1469598103934665603ULL;
        for (int64_t i = 0; i < s->len; i++) { h ^= (unsigned char)s->data[i]; h *= 1099511628211ULL; }
        return h;
    }
    uint64_t z = k + 0x9e3779b97f4a7c15ULL;
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
    return z ^ (z >> 31);
}
static inline int k_key_eq(int kind, KS a, KS b) {
    if (kind == 1) return k_str_eq(K_S2P(KStr *, a), K_S2P(KStr *, b));
    return a == b;
}
static KMapData *k_mapdata_new(int64_t cap, int kind) {
    if (cap < 4) cap = 4;
    KMapData *d = (KMapData *)K_ALLOC(sizeof(KMapData));
    d->keys = (KS *)K_ALLOC(sizeof(KS) * cap);
    d->vals = (KS *)K_ALLOC(sizeof(KS) * cap);
    d->prev = (int64_t *)K_ALLOC_ATOMIC(sizeof(int64_t) * cap);
    d->next = (int64_t *)K_ALLOC_ATOMIC(sizeof(int64_t) * cap);
    d->used = 0; d->cap = cap; d->kind = kind; d->frozen = 0; d->memo_index = NULL;
    d->icap = 8;
    while (d->icap < cap * 2) d->icap *= 2;
    d->index = (int64_t *)K_ALLOC_ATOMIC(sizeof(int64_t) * d->icap);
    memset(d->index, 0, sizeof(int64_t) * d->icap);
    return d;
}
/* index slot holding (latest entry + 1) for key, or the empty slot where it would go */
static int64_t k_map_slot(KMapData *d, KS key) {
    /* `put(m, k, f(get(m, k)))` looks the same key up twice in a row: remember the last slot until the index
     * is rebuilt (a key's slot never moves otherwise). Klar code runs one task at a time, so this is safe. */
    if (d->memo_index == d->index && d->memo_key == key) { d->last_tag = d->memo_tag; return d->memo_slot; }
    uint64_t full = k_hash(d->kind, key), m = d->icap - 1, h = full & m;
    int64_t tag = (int64_t)(full & ~((UINT64_C(1) << 40) - 1)), e;
    while ((e = d->index[h]) && !((e & ~((INT64_C(1) << 40) - 1)) == tag && k_key_eq(d->kind, d->keys[K_IX_ENTRY(e)], key)))
        h = (h + 1) & m;
    d->memo_key = key; d->memo_index = d->index; d->memo_slot = (int64_t)h; d->memo_tag = d->last_tag = tag;
    return (int64_t)h;
}
static void k_map_rehash(KMapData *d) {
    int64_t *old = d->index, oc = d->icap;
    d->icap *= 2;
    d->index = (int64_t *)K_ALLOC_ATOMIC(sizeof(int64_t) * d->icap);
    memset(d->index, 0, sizeof(int64_t) * d->icap);
    for (int64_t i = 0; i < oc; i++) if (old[i]) d->index[k_map_slot(d, d->keys[K_IX_ENTRY(old[i])])] = old[i];
}
static KMap *k_map_view(KMapData *d, int64_t count) {
    KMap *m = (KMap *)K_ALLOC(sizeof(KMap));
    m->d = d; m->len = d->used; m->count = count;
    return m;
}
static KMap *k_map_empty(int kind) { return k_map_view(k_mapdata_new(4, kind), 0); }
/* entry index of the version of `key` visible in m, or -1 */
static int64_t k_map_find(KMap *m, KS key) {
    KMapData *d = m->d;
    int64_t j = K_IX_ENTRY(d->index[k_map_slot(d, key)]);
    while (j >= m->len) j = d->prev[j];
    return j;
}
static inline int64_t k_map_count(KMap *m) { return m->count; }
/* append an entry to the newest view's log (m->len == d->used) */
static void k_map_append(KMapData *d, KS key, KS val, int64_t *count) {
    if (d->used == d->cap) {
        d->cap *= 2;
        d->keys = (KS *)K_REALLOC(d->keys, sizeof(KS) * d->cap);
        d->vals = (KS *)K_REALLOC(d->vals, sizeof(KS) * d->cap);
        d->prev = (int64_t *)K_REALLOC(d->prev, sizeof(int64_t) * d->cap);
        d->next = (int64_t *)K_REALLOC(d->next, sizeof(int64_t) * d->cap);
    }
    if ((d->used + 1) * 2 > d->icap) k_map_rehash(d);
    int64_t slot = k_map_slot(d, key), j = K_IX_ENTRY(d->index[slot]), n = d->used;
    d->keys[n] = key; d->vals[n] = val; d->prev[n] = j; d->next[n] = -1;
    if (j >= 0) d->next[j] = n; else (*count)++;
    d->index[slot] = (n + 1) | d->last_tag;
    d->used++;
}
/* a fresh log holding exactly the entries visible in m (optionally without one key) */
static KMap *k_map_compact_skip(KMap *m, int has_skip, KS skip) {
    KMapData *s = m->d;
    KMapData *d = k_mapdata_new(m->count + 4, s->kind);
    int64_t count = 0;
    for (int64_t j = 0; j < m->len; j++) {
        if (s->prev[j] != -1) continue;                 /* not the first version of its key */
        if (has_skip && k_key_eq(s->kind, s->keys[j], skip)) continue;
        int64_t v = j;
        while (s->next[v] != -1 && s->next[v] < m->len) v = s->next[v];
        k_map_append(d, s->keys[j], s->vals[v], &count);
    }
    return k_map_view(d, count);
}
static KMap *k_map_compact(KMap *m) {
    if (m->len == m->count) return m;      /* no stale versions: entries are exactly the visible ones */
    return k_map_compact_skip(m, 0, 0);
}
static KMap *k_map_put(KMap *m, KS key, KS val) {
    if (m->len != m->d->used) m = k_map_compact_skip(m, 0, 0);   /* not the newest view: copy */
    KMapData *d = m->d;
    int64_t count = m->count;
    k_map_append(d, key, val, &count);
    KMap *r = k_map_view(d, count);
    if (d->used > 64 && d->used > 4 * count) r = k_map_compact_skip(r, 0, 0);
    return r;
}
/* `v = put(v, k, x)` on a map var that may share its entries with other values. The result is a view only v
 * holds; everything visible to anyone else (at most m's entries, since only the newest view appends in
 * place) is frozen, so k_map_put_owned can then update v in place. */
static KMap *k_map_put_shared(KMap *m, KS key, KS val) {
    if (m->len == m->d->used && m->d->frozen < m->len) m->d->frozen = m->len;
    return k_map_put(m, key, val);
}
/* `v = put(v, k, x)` while v is owned: its KMap is referenced only by v. An existing key's newest entry is
 * overwritten when no other value can see it; otherwise this appends, without allocating a new map value. */
static void k_map_put_owned(KMap *m, KS key, KS val) {
    KMapData *d = m->d;
    if (m->len != d->used) { *m = *k_map_put_shared(m, key, val); return; }
    int64_t j = K_IX_ENTRY(d->index[k_map_slot(d, key)]);
    if (j >= d->frozen) { d->vals[j] = val; return; }
    int64_t count = m->count;
    k_map_append(d, key, val, &count);
    m->len = d->used; m->count = count;
    if (d->used > 64 && d->used > 4 * count) *m = *k_map_compact_skip(m, 0, 0);
}
static KMap *k_map_remove(KMap *m, KS key) {
    if (k_map_find(m, key) < 0) return m;
    return k_map_compact_skip(m, 1, key);
}
static KList *k_map_keys(KMap *m) {
    m = k_map_compact(m);
    KList *r = k_list_with_cap(m->len, m->len);
    memcpy(r->buf->data, m->d->keys, sizeof(KS) * m->len);
    return r;
}
static KList *k_map_values(KMap *m) {
    m = k_map_compact(m);
    KList *r = k_list_with_cap(m->len, m->len);
    memcpy(r->buf->data, m->d->vals, sizeof(KS) * m->len);
    return r;
}

static KList *k_map_entries(KMap *m) {
    m = k_map_compact(m);
    KList *r = k_list_with_cap(m->len, m->len);
    for (int64_t i = 0; i < m->len; i++) r->buf->data[i] = k_p2s(k_struct_new(2, m->d->keys[i], m->d->vals[i]));
    return r;
}

/* ------------------------------------------------------------------ string built-ins */
static KStr *k_str_index(KStr *s, int64_t i, int line, int col) {
    int64_t n = k_str_cps(s);
    if (i < 0 || i >= n) k_fail("E401", line, col, "index %lld is out of bounds for length %lld", (long long)i, (long long)n);
    int64_t a = k_str_off(s, i), b = k_str_off(s, i + 1);
    return k_str_new(s->data + a, b - a);
}
static KStr *k_str_slice(KStr *s, int64_t a, int64_t b) {
    int64_t n = k_str_cps(s);
    if (a < 0) a = 0; if (a > n) a = n;
    if (b < a) b = a; if (b > n) b = n;
    int64_t x = k_str_off(s, a), y = k_str_off(s, b);
    return k_str_new(s->data + x, y - x);
}
static KList *k_str_chars(KStr *s) {
    int64_t n = k_str_cps(s);
    KList *r = k_list_with_cap(n, n);
    int64_t start = 0, k = 0;
    for (int64_t i = 1; i <= s->len; i++) {
        if (i == s->len || ((unsigned char)s->data[i] & 0xC0) != 0x80) {
            r->buf->data[k++] = k_p2s(k_str_new(s->data + start, i - start));
            start = i;
        }
    }
    return r;
}
static KStr *k_str_reverse(KStr *s) {
    KList *cs = k_str_chars(s);
    KStr *r = k_str_alloc(s->len);
    int64_t o = 0;
    for (int64_t i = cs->len - 1; i >= 0; i--) {
        KStr *c = K_S2P(KStr *, KL_DATA(cs)[i]);
        memcpy(r->data + o, c->data, c->len); o += c->len;
    }
    return r;
}
static const char *k_memmem(const char *h, int64_t hn, const char *n, int64_t nn) {
    if (nn == 0) return h;
    for (int64_t i = 0; i + nn <= hn; i++) if (h[i] == n[0] && memcmp(h + i, n, nn) == 0) return h + i;
    return NULL;
}
static int k_str_contains(KStr *s, KStr *sub) { return k_memmem(s->data, s->len, sub->data, sub->len) != NULL; }
static int k_starts_with(KStr *s, KStr *p) { return p->len <= s->len && memcmp(s->data, p->data, p->len) == 0; }
static int k_ends_with(KStr *s, KStr *p) { return p->len <= s->len && memcmp(s->data + s->len - p->len, p->data, p->len) == 0; }
static KList *k_split(KStr *s, KStr *sep) {
    if (sep->len == 0) return k_str_chars(s);
    KList *r = k_list_with_cap(0, 4);
    const char *p = s->data, *end = s->data + s->len;
    for (;;) {
        const char *q = k_memmem(p, end - p, sep->data, sep->len);
        if (!q) { r = k_list_push(r, k_p2s(k_str_new(p, end - p))); break; }
        r = k_list_push(r, k_p2s(k_str_new(p, q - p)));
        p = q + sep->len;
    }
    return r;
}
static KStr *k_join(KList *xs, KStr *sep) {
    KSB b; ksb_init(&b);
    for (int64_t i = 0; i < xs->len; i++) {
        if (i) ksb_str(&b, sep);
        ksb_str(&b, K_S2P(KStr *, KL_DATA(xs)[i]));
    }
    return ksb_done(&b);
}
static int k_isspace(char c) { return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\v' || c == '\f'; }
static KStr *k_trim(KStr *s) {
    int64_t a = 0, b = s->len;
    while (a < b && k_isspace(s->data[a])) a++;
    while (b > a && k_isspace(s->data[b - 1])) b--;
    return k_str_new(s->data + a, b - a);
}
static KStr *k_upper(KStr *s) {
    KStr *r = k_str_new(s->data, s->len);
    for (int64_t i = 0; i < r->len; i++) if (r->data[i] >= 'a' && r->data[i] <= 'z') r->data[i] -= 32;
    return r;
}
static KStr *k_lower(KStr *s) {
    KStr *r = k_str_new(s->data, s->len);
    for (int64_t i = 0; i < r->len; i++) if (r->data[i] >= 'A' && r->data[i] <= 'Z') r->data[i] += 32;
    return r;
}
static KStr *k_replace(KStr *s, KStr *old, KStr *new_) {
    KSB b; ksb_init(&b);
    if (old->len == 0) {
        KList *cs = k_str_chars(s);
        ksb_str(&b, new_);
        for (int64_t i = 0; i < cs->len; i++) { ksb_str(&b, K_S2P(KStr *, KL_DATA(cs)[i])); ksb_str(&b, new_); }
        return ksb_done(&b);
    }
    const char *p = s->data, *end = s->data + s->len;
    for (;;) {
        const char *q = k_memmem(p, end - p, old->data, old->len);
        if (!q) { ksb_add(&b, p, end - p); break; }
        ksb_add(&b, p, q - p);
        ksb_str(&b, new_);
        p = q + old->len;
    }
    return ksb_done(&b);
}
static KStr *k_repeat(KStr *s, int64_t n) {
    if (n < 0) n = 0;
    if (s->len && n > K_MAX_ALLOC / s->len) k_oom(n);
    KStr *r = k_str_alloc(s->len * n);
    for (int64_t i = 0; i < n; i++) memcpy(r->data + i * s->len, s->data, s->len);
    return r;
}
static KEnum *k_parse_int(KStr *s) {
    KStr *t = k_trim(s);
    int64_t i = 0, neg = 0;
    if (t->len && (t->data[0] == '+' || t->data[0] == '-')) { neg = t->data[0] == '-'; i = 1; }
    if (i >= t->len) return K_NONE;
    int64_t v = 0;
    for (; i < t->len; i++) {
        char c = t->data[i];
        if (c < '0' || c > '9') return K_NONE;
        if (K_MUL_OVF(v, 10, &v) || K_ADD_OVF(v, neg ? -(c - '0') : (c - '0'), &v)) return K_NONE;
    }
    return k_some((KS)v);
}
#define K_DIGIT(c) ((c) >= '0' && (c) <= '9')
#define K_ALPHA(c) (((c) | 32) >= 'a' && ((c) | 32) <= 'z')
static KEnum *k_parse_float(KStr *s) {
    KStr *t = k_trim(s);
    if (t->len == 0) return K_NONE;
    /* the same text every backend accepts: [+-]digits[.digits][e[+-]digits], or inf / infinity / nan */
    const char *q = t->data; int digits = 0;
    if (*q == '+' || *q == '-') q++;
    if (!K_DIGIT(*q) && *q != '.' && !K_ALPHA(*q)) return K_NONE;
    if (K_ALPHA(*q)) {
        if (strcasecmp(q, "inf") && strcasecmp(q, "infinity") && strcasecmp(q, "nan")) return K_NONE;
    } else {
        while (K_DIGIT(*q)) { q++; digits++; }
        if (*q == '.') { q++; while (K_DIGIT(*q)) { q++; digits++; } }
        if (!digits) return K_NONE;
        if (*q == 'e' || *q == 'E') {
            q++;
            if (*q == '+' || *q == '-') q++;
            if (!K_DIGIT(*q)) return K_NONE;
            while (K_DIGIT(*q)) q++;
        }
        if (q != t->data + t->len) return K_NONE;
    }
    char *end;
    double d = strtod(t->data, &end);
    if (end != t->data + t->len) return K_NONE;
    return k_some(k_d2s(d));
}

/* ------------------------------------------------------------------ io */
static inline void k_print_str(KStr *s) { fwrite(s->data, 1, s->len, stdout); fputc('\n', stdout); }
static void k_eprint_str(KStr *s) {
    fflush(stdout);
    fwrite(s->data, 1, s->len, stderr); fputc('\n', stderr); fflush(stderr);
}
static KStr *k_read_all(void) {
    fflush(stdout);
    KSB b; ksb_init(&b);
    char buf[65536]; size_t n;
    while ((n = fread(buf, 1, sizeof buf, stdin)) > 0) ksb_add(&b, buf, n);
    return ksb_done(&b);
}
static KEnum *k_read_line(void) {
    fflush(stdout);
    KSB b; ksb_init(&b);
    int c, any = 0;
    while ((c = getchar()) != EOF) { any = 1; if (c == '\n') break; char ch = (char)c; ksb_add(&b, &ch, 1); }
    if (!any) return K_NONE;
    return k_some(k_p2s(ksb_done(&b)));
}
static KStr *k_errno_msg(KStr *path) {
    char t[512];
    snprintf(t, sizeof t, "[Errno %d] %s: '%s'", errno, strerror(errno), path->data);
    return k_cstr(t);
}
static KEnum *k_read_file(KStr *path) {
    FILE *f = fopen(path->data, "rb");
    if (!f) return k_err(k_p2s(k_errno_msg(path)));
    KSB b; ksb_init(&b);
    char buf[65536]; size_t n;
    while ((n = fread(buf, 1, sizeof buf, f)) > 0) ksb_add(&b, buf, n);
    fclose(f);
    return k_ok(k_p2s(ksb_done(&b)));
}
static KEnum *k_write_file(KStr *path, KStr *content) {
    FILE *f = fopen(path->data, "wb");
    if (!f) return k_err(k_p2s(k_errno_msg(path)));
    fwrite(content->data, 1, content->len, f);
    fclose(f);
    return k_ok(0);
}
static KList *k_args(void) {
    KList *r = k_list_with_cap(0, 4);
    for (int i = 1; i < k_argc; i++) r = k_list_push(r, k_p2s(k_cstr(k_argv[i])));
    return r;
}
static inline int64_t k_floor(double x) { return (int64_t)floor(x); }
static inline int64_t k_round(double x) { return (int64_t)floor(x + 0.5); }
static inline double k_sqrt(double x) { return x < 0 ? NAN : sqrt(x); }
