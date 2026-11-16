/* Klar runtime support for WebAssembly builds (`klar build --target wasm`, and `extern "wasm:..."` in web apps).
 *
 * Compiled with clang --target=wasm32 and no C library: this file provides the small part of libc that
 * klar_rt.h and klar_std.h use, and asks the JavaScript host (klar_wasm.js) for what the browser or Node already
 * does well: output, float formatting and parsing, transcendental math, clocks and random seeds. Every POSIX type
 * the shared runtime mentions is declared so it compiles unchanged; the compiler refuses the parts of the standard
 * library that need an operating system (files, processes, sockets, threads, SQLite, regex) with E501.
 *
 * Memory: a bump allocator over linear memory. Klar has no global variables, so once an exported function has
 * returned (and the host has copied its result out) nothing on the heap is reachable any more: every call starts
 * with an empty heap (klar_reset). Allocation is a pointer increment and freeing is free, but one call can only
 * use what it allocates in total (up to 4 GB); a long-running allocation-heavy computation needs the native build.
 */
#include <stdint.h>
#include <stddef.h>
#include <stdarg.h>
#include <limits.h>

#define KLAR_WASM 1
#define K_IMPORT(name) __attribute__((import_module("klar"), import_name(name)))
#define K_EXPORT(name) __attribute__((export_name(name)))

/* ------------------------------------------------------------------ host imports (klar_wasm.js) */
K_IMPORT("write") void kh_write(int fd, const char *p, int n);
K_IMPORT("read") int kh_read(int fd, char *p, int cap);
K_IMPORT("exit") __attribute__((noreturn)) void kh_exit(int code);
K_IMPORT("fmt_float") int kh_fmt_float(double x, int conv, int prec, char *out, int cap);
K_IMPORT("strtod") double kh_strtod(const char *p, int n, int *used);
K_IMPORT("now") double kh_now(void);
K_IMPORT("clock_ms") double kh_clock_ms(void);
K_IMPORT("seed") uint32_t kh_seed(void);
K_IMPORT("arg") int kh_arg(int i, char *out, int cap);      /* length of argument i (-1: none); copies it if it fits */
K_IMPORT("sin") double sin(double);
K_IMPORT("cos") double cos(double);
K_IMPORT("tan") double tan(double);
K_IMPORT("asin") double asin(double);
K_IMPORT("acos") double acos(double);
K_IMPORT("atan") double atan(double);
K_IMPORT("atan2") double atan2(double, double);
K_IMPORT("exp") double exp(double);
K_IMPORT("log") double log(double);
K_IMPORT("log10") double log10(double);
K_IMPORT("log2") double log2(double);
K_IMPORT("pow") double pow(double, double);
K_IMPORT("fmod") double fmod(double, double);
K_IMPORT("hypot") double hypot(double, double);

/* ------------------------------------------------------------------ types and constants */
typedef long ssize_t;
typedef int pid_t;
typedef long long time_t;
typedef long clock_t;
typedef unsigned long long rlim_t;
typedef unsigned int mode_t;
typedef struct KWFile FILE;
struct timeval { time_t tv_sec; long tv_usec; };
struct timespec { time_t tv_sec; long tv_nsec; };
struct tm { int tm_sec, tm_min, tm_hour, tm_mday, tm_mon, tm_year, tm_wday, tm_yday, tm_isdst; };
struct stat { mode_t st_mode; long long st_size; };
struct dirent { char d_name[256]; };
typedef struct KWDir DIR;
struct pollfd { int fd; short events, revents; };
struct rlimit { rlim_t rlim_cur, rlim_max; };
typedef struct { void *ss_sp; int ss_flags; size_t ss_size; } stack_t;
struct sigaction { void (*sa_handler)(int); int sa_flags; };
#define EOF (-1)
#define NULL ((void *)0)
#define RLIMIT_STACK 3
#define RLIM_INFINITY (~0ULL)
#define SIGSEGV 11
#define SA_ONSTACK 0x08000000
#define CLOCK_MONOTONIC 1
#define S_ISDIR(m) (((m) & 0170000) == 0040000)
#define POLLIN 1
#define POLLOUT 4
#define F_SETFD 2
#define F_SETFL 4
#define FD_CLOEXEC 1
#define O_NONBLOCK 04000
#define WIFEXITED(s) 1
#define WEXITSTATUS(s) (s)
#define WTERMSIG(s) 0
#define _IOFBF 0
enum { EPERM = 1, ENOENT = 2, EINTR = 4, EAGAIN = 11, EACCES = 13, EEXIST = 17, ENOTDIR = 20, EISDIR = 21,
       EINVAL = 22, ERANGE = 34, ENOSYS = 38 };
static int errno;
#define NAN (__builtin_nan(""))
#define INFINITY (__builtin_inf())
#define HUGE_VAL (__builtin_huge_val())
#define isnan(x) __builtin_isnan(x)
#define isinf(x) __builtin_isinf(x)
#define isfinite(x) __builtin_isfinite(x)
#define signbit(x) __builtin_signbit(x)
#define INT64_MAX __INT64_MAX__
#define INT64_MIN (-__INT64_MAX__ - 1)

static inline double sqrt(double x) { return __builtin_sqrt(x); }
static inline double fabs(double x) { return __builtin_fabs(x); }
static inline double floor(double x) { return __builtin_floor(x); }
static inline double ceil(double x) { return __builtin_ceil(x); }
static inline double trunc(double x) { return __builtin_trunc(x); }
static inline double round(double x) { return __builtin_round(x); }
static inline long long llround(double x) { return (long long)__builtin_round(x); }
static inline double fmin(double a, double b) { return __builtin_fmin(a, b); }
static inline double fmax(double a, double b) { return __builtin_fmax(a, b); }

/* ------------------------------------------------------------------ memory and strings */
/* built with -mbulk-memory: these become single memory.copy / memory.fill instructions */
void *memcpy(void *restrict d, const void *restrict s, size_t n) { __builtin_memcpy(d, s, n); return d; }
void *memmove(void *d, const void *s, size_t n) { __builtin_memmove(d, s, n); return d; }
void *memset(void *d, int c, size_t n) { __builtin_memset(d, c, n); return d; }
/* -ffreestanding stops clang from rewriting fwrite into fputc (and the like) inside this libc; these keep the
 * usual inlining of fixed-size copies in the runtime */
#define memcpy(d, s, n) __builtin_memcpy(d, s, n)
#define memmove(d, s, n) __builtin_memmove(d, s, n)
#define memset(d, c, n) __builtin_memset(d, c, n)
int memcmp(const void *x, const void *y, size_t n) {
    const unsigned char *a = (const unsigned char *)x, *b = (const unsigned char *)y;
    for (; n; n--, a++, b++) if (*a != *b) return *a - *b;
    return 0;
}
size_t strlen(const char *s) { const char *p = s; while (*p) p++; return (size_t)(p - s); }
int strcmp(const char *a, const char *b) {
    while (*a && *a == *b) { a++; b++; }
    return (unsigned char)*a - (unsigned char)*b;
}
int strncmp(const char *a, const char *b, size_t n) {
    for (; n; n--, a++, b++) { if (*a != *b) return (unsigned char)*a - (unsigned char)*b; if (!*a) return 0; }
    return 0;
}
int strcasecmp(const char *a, const char *b) {
    for (;; a++, b++) {
        int x = (unsigned char)*a, y = (unsigned char)*b;
        if (x >= 'A' && x <= 'Z') x += 32;
        if (y >= 'A' && y <= 'Z') y += 32;
        if (x != y || !x) return x - y;
    }
}
int strncasecmp(const char *a, const char *b, size_t n) {
    for (; n; n--, a++, b++) {
        int x = (unsigned char)*a, y = (unsigned char)*b;
        if (x >= 'A' && x <= 'Z') x += 32;
        if (y >= 'A' && y <= 'Z') y += 32;
        if (x != y || !x) return x - y;
    }
    return 0;
}
char *strcpy(char *d, const char *s) { char *r = d; while ((*d++ = *s++)) {} return r; }
char *strncpy(char *d, const char *s, size_t n) {
    size_t i = 0;
    for (; i < n && s[i]; i++) d[i] = s[i];
    for (; i < n; i++) d[i] = 0;
    return d;
}
char *strchr(const char *s, int c) {
    for (;; s++) { if (*s == (char)c) return (char *)s; if (!*s) return NULL; }
}
char *strrchr(const char *s, int c) {
    const char *r = NULL;
    for (;; s++) { if (*s == (char)c) r = s; if (!*s) return (char *)r; }
}
char *strstr(const char *h, const char *n) {
    size_t k = strlen(n);
    if (!k) return (char *)h;
    for (; *h; h++) if (*h == *n && !strncmp(h, n, k)) return (char *)h;
    return NULL;
}
const char *strerror(int e) {
    switch (e) {
    case ENOENT: return "No such file or directory";
    case EACCES: return "Permission denied";
    case EEXIST: return "File exists";
    case EISDIR: return "Is a directory";
    case ENOTDIR: return "Not a directory";
    default: return "Function not implemented";
    }
}

/* ------------------------------------------------------------------ heap: bump allocator, reset per call */
extern unsigned char __heap_base;
static uintptr_t kw_hp, kw_hend, kw_last;

static void kw_oom(size_t n) {
    static const char msg[] = "error[E406] out of memory: a WebAssembly call can allocate at most 4 GB in total "
                              "(build natively for long-running, allocation-heavy work)\n";
    kh_write(2, msg, sizeof msg - 1);
    (void)n;
    kh_exit(1);
}
K_EXPORT("klar_reset") void klar_reset(void) {
    kw_hp = ((uintptr_t)&__heap_base + 15) & ~(uintptr_t)15;
    kw_hend = __builtin_wasm_memory_size(0) * 65536u;
    kw_last = 0;
}
static void *kw_more(size_t n) {
    if (!kw_hp) klar_reset();
    uintptr_t p = kw_hp + 8;                 /* 8-byte size header; blocks are 8-aligned */
    uintptr_t end = p + ((n + 7) & ~(size_t)7);
    if (end < p) kw_oom(n);
    if (end > kw_hend) {
        size_t have = __builtin_wasm_memory_size(0);
        size_t need = (end - kw_hend + 65535) / 65536;
        size_t grow = need > have / 2 ? need : have / 2;   /* grow by half again to keep calls rare */
        if (have + grow > 65536) grow = 65536 - have;
        if (grow < need || __builtin_wasm_memory_grow(0, grow) == (size_t)-1) kw_oom(n);
        kw_hend = __builtin_wasm_memory_size(0) * 65536u;
    }
    *(uint64_t *)(p - 8) = n;
    kw_last = p;
    kw_hp = end;
    return (void *)p;
}
void *malloc(size_t n) { return kw_more(n ? n : 1); }
void *calloc(size_t a, size_t b) {
    size_t n = a * b;
    void *p = kw_more(n ? n : 1);       /* memory past the old top may hold an earlier call's data */
    memset(p, 0, n);
    return p;
}
void free(void *p) {
    if (p && (uintptr_t)p == kw_last) { kw_hp = kw_last - 8; kw_last = 0; }
}
void *realloc(void *p, size_t n) {
    if (!p) return malloc(n);
    size_t old = (size_t)*(uint64_t *)((uintptr_t)p - 8);
    if ((uintptr_t)p == kw_last) {            /* the newest block grows in place */
        kw_hp = kw_last - 8;
        void *q = kw_more(n);
        return q;                             /* same address: kw_more starts where the block started */
    }
    void *q = kw_more(n);
    memcpy(q, p, old < n ? old : n);
    return q;
}
char *strdup(const char *s) { size_t n = strlen(s) + 1; char *p = (char *)malloc(n); memcpy(p, s, n); return p; }
K_EXPORT("klar_alloc") void *klar_alloc(int n) { return malloc((size_t)n); }

/* ------------------------------------------------------------------ numbers */
/* 128-bit multiply, which clang calls for __builtin_mul_overflow on 64-bit ints (compiler-rt is not linked) */
__int128 __multi3(__int128 a, __int128 b) {
    uint64_t al = (uint64_t)a, ah = (uint64_t)((unsigned __int128)a >> 64);
    uint64_t bl = (uint64_t)b, bh = (uint64_t)((unsigned __int128)b >> 64);
    uint64_t a0 = al & 0xffffffffu, a1 = al >> 32, b0 = bl & 0xffffffffu, b1 = bl >> 32;
    uint64_t p00 = a0 * b0, p01 = a0 * b1, p10 = a1 * b0, p11 = a1 * b1;
    uint64_t mid = (p00 >> 32) + (p01 & 0xffffffffu) + (p10 & 0xffffffffu);
    uint64_t lo = (mid << 32) | (p00 & 0xffffffffu);
    uint64_t hi = p11 + (p01 >> 32) + (p10 >> 32) + (mid >> 32) + ah * bl + al * bh;
    return (__int128)(((unsigned __int128)hi << 64) | lo);
}
double strtod(const char *s, char **end) {
    int used = 0;
    double d = kh_strtod(s, (int)strlen(s), &used);
    if (end) *end = (char *)s + used;
    return d;
}
/* wasm32 has no overflow flag, so __builtin_mul_overflow on int64 becomes a call to __multi3. Factors that both
 * fit in 32 bits (nearly all of them) cannot overflow: multiply those inline. */
static inline int k_wasm_mul_ovf(int64_t a, int64_t b, int64_t *r) {
    if ((uint64_t)(a + 0x80000000LL) <= 0xffffffffULL && (uint64_t)(b + 0x80000000LL) <= 0xffffffffULL) { *r = a * b; return 0; }
    return __builtin_mul_overflow(a, b, r);
}
long long strtoll(const char *s, char **end, int base) {
    const char *p = s;
    while (*p == ' ' || (*p >= '\t' && *p <= '\r')) p++;
    int neg = 0;
    if (*p == '+' || *p == '-') neg = *p++ == '-';
    if (base == 0) base = 10;
    unsigned long long v = 0; int any = 0, over = 0;
    for (;; p++) {
        int c = *p, d;
        if (c >= '0' && c <= '9') d = c - '0';
        else if (c >= 'a' && c <= 'z') d = c - 'a' + 10;
        else if (c >= 'A' && c <= 'Z') d = c - 'A' + 10;
        else break;
        if (d >= base) break;
        any = 1;
        if (v > (ULLONG_MAX - d) / base) over = 1; else v = v * base + d;
    }
    if (end) *end = (char *)(any ? p : s);
    if (over || v > (unsigned long long)LLONG_MAX + neg) { errno = ERANGE; return neg ? LLONG_MIN : LLONG_MAX; }
    return neg ? (long long)(0 - v) : (long long)v;
}
long strtol(const char *s, char **end, int base) { return (long)strtoll(s, end, base); }
int atoi(const char *s) { return (int)strtoll(s, NULL, 10); }
int abs(int x) { return x < 0 ? -x : x; }
long long llabs(long long x) { return x < 0 ? -x : x; }

static void kw_swap(char *a, char *b, size_t n) { while (n--) { char t = *a; *a++ = *b; *b++ = t; } }
void qsort(void *base, size_t n, size_t size, int (*cmp)(const void *, const void *)) {
    char *b = (char *)base;              /* insertion sort for short runs, else heap sort: no recursion */
    if (n < 16) {
        for (size_t i = 1; i < n; i++)
            for (size_t j = i; j > 0 && cmp(b + (j - 1) * size, b + j * size) > 0; j--) kw_swap(b + (j - 1) * size, b + j * size, size);
        return;
    }
    for (size_t start = n / 2; start-- > 0;) {
        for (size_t r = start;;) {
            size_t c = 2 * r + 1;
            if (c >= n) break;
            if (c + 1 < n && cmp(b + c * size, b + (c + 1) * size) < 0) c++;
            if (cmp(b + r * size, b + c * size) >= 0) break;
            kw_swap(b + r * size, b + c * size, size); r = c;
        }
    }
    for (size_t end = n - 1; end > 0; end--) {
        kw_swap(b, b + end * size, size);
        for (size_t r = 0;;) {
            size_t c = 2 * r + 1;
            if (c >= end) break;
            if (c + 1 < end && cmp(b + c * size, b + (c + 1) * size) < 0) c++;
            if (cmp(b + r * size, b + c * size) >= 0) break;
            kw_swap(b + r * size, b + c * size, size); r = c;
        }
    }
}

/* ------------------------------------------------------------------ formatted output */
typedef struct { char *p; size_t n, cap; } KWOut;
static void kw_put(KWOut *o, const char *s, size_t n) {
    for (size_t i = 0; i < n; i++, o->n++) if (o->n + 1 < o->cap) o->p[o->n] = s[i];
}
static void kw_pad(KWOut *o, char c, int n) { while (n-- > 0) kw_put(o, &c, 1); }
int vsnprintf(char *buf, size_t cap, const char *fmt, va_list ap) {
    KWOut o = {buf, 0, cap};
    for (const char *f = fmt; *f; f++) {
        if (*f != '%') { kw_put(&o, f, 1); continue; }
        f++;
        int left = 0, zero = 0, plus = 0, space = 0, alt = 0, width = 0, prec = -1, lng = 0;
        for (;; f++) {
            if (*f == '-') left = 1; else if (*f == '0') zero = 1; else if (*f == '+') plus = 1;
            else if (*f == ' ') space = 1; else if (*f == '#') alt = 1; else break;
        }
        if (*f == '*') { width = va_arg(ap, int); if (width < 0) { left = 1; width = -width; } f++; }
        else while (*f >= '0' && *f <= '9') width = width * 10 + (*f++ - '0');
        if (*f == '.') {
            f++; prec = 0;
            if (*f == '*') { prec = va_arg(ap, int); f++; }
            else while (*f >= '0' && *f <= '9') prec = prec * 10 + (*f++ - '0');
        }
        while (*f == 'l' || *f == 'h' || *f == 'z' || *f == 'j' || *f == 't') { if (*f == 'l' || *f == 'z' || *f == 'j' || *f == 't') lng++; f++; }
        char tmp[400]; const char *s = tmp; int n = 0; const char *sign = "";
        switch (*f) {
        case 'd': case 'i': {
            long long v = lng >= 2 ? va_arg(ap, long long) : lng == 1 ? va_arg(ap, long) : va_arg(ap, int);
            unsigned long long u = v < 0 ? 0ULL - (unsigned long long)v : (unsigned long long)v;
            sign = v < 0 ? "-" : plus ? "+" : space ? " " : "";
            char r[24]; int k = 0;
            do { r[k++] = (char)('0' + u % 10); u /= 10; } while (u);
            while (k < prec) r[k++] = '0';
            while (k) tmp[n++] = r[--k];
            break;
        }
        case 'u': case 'x': case 'X': case 'o': case 'p': {
            unsigned long long u = *f == 'p' ? (uintptr_t)va_arg(ap, void *)
                                 : lng >= 2 ? va_arg(ap, unsigned long long) : lng == 1 ? va_arg(ap, unsigned long) : va_arg(ap, unsigned);
            int base = *f == 'u' ? 10 : *f == 'o' ? 8 : 16;
            const char *dg = *f == 'X' ? "0123456789ABCDEF" : "0123456789abcdef";
            char r[24]; int k = 0;
            do { r[k++] = dg[u % base]; u /= base; } while (u);
            while (k < prec) r[k++] = '0';
            if (*f == 'p' || (alt && base == 16)) { tmp[n++] = '0'; tmp[n++] = 'x'; }
            while (k) tmp[n++] = r[--k];
            break;
        }
        case 'c': tmp[0] = (char)va_arg(ap, int); n = 1; break;
        case 's': {
            s = va_arg(ap, const char *);
            if (!s) s = "(null)";
            n = 0;
            while ((prec < 0 || n < prec) && s[n]) n++;
            break;
        }
        case 'e': case 'E': case 'f': case 'F': case 'g': case 'G': {
            double d = va_arg(ap, double);
            if (signbit(d) && !isnan(d)) { sign = "-"; d = -d; } else sign = plus ? "+" : space ? " " : "";
            n = kh_fmt_float(d, *f | (alt ? 0x100 : 0), prec < 0 ? 6 : prec, tmp, (int)sizeof tmp);
            if (isnan(d) || isinf(d)) zero = 0;
            break;
        }
        case '%': tmp[0] = '%'; n = 1; break;
        default: tmp[0] = '%'; tmp[1] = *f; n = 2; if (!*f) f--; break;
        }
        int sl = (int)strlen(sign), padn = width - n - sl;
        if (!left && !zero) kw_pad(&o, ' ', padn);
        kw_put(&o, sign, sl);
        if (!left && zero) kw_pad(&o, '0', padn);
        kw_put(&o, s, n);
        if (left) kw_pad(&o, ' ', padn);
    }
    if (cap) o.p[o.n < cap ? o.n : cap - 1] = 0;
    return (int)o.n;
}
int snprintf(char *buf, size_t cap, const char *fmt, ...) {
    va_list ap; va_start(ap, fmt); int n = vsnprintf(buf, cap, fmt, ap); va_end(ap); return n;
}
int sprintf(char *buf, const char *fmt, ...) {
    va_list ap; va_start(ap, fmt); int n = vsnprintf(buf, 1 << 20, fmt, ap); va_end(ap); return n;
}

/* stdout is buffered here and flushed to the host; stderr goes straight through */
struct KWFile { int fd; };
static FILE kw_stdin = {0}, kw_stdout = {1}, kw_stderr = {2};
#define stdin (&kw_stdin)
#define stdout (&kw_stdout)
#define stderr (&kw_stderr)
static char kw_obuf[1 << 16];
static int kw_on;
int fflush(FILE *f) { (void)f; if (kw_on) { kh_write(1, kw_obuf, kw_on); kw_on = 0; } return 0; }
size_t fwrite(const void *p, size_t size, size_t n, FILE *f) {
    size_t len = size * n;
    if (f->fd != 1) { fflush(stdout); kh_write(f->fd, (const char *)p, (int)len); return n; }
    if (kw_on + len > sizeof kw_obuf) fflush(stdout);
    if (len >= sizeof kw_obuf) kh_write(1, (const char *)p, (int)len);
    else { memcpy(kw_obuf + kw_on, p, len); kw_on += (int)len; }
    return n;
}
int fputc(int c, FILE *f) { char ch = (char)c; fwrite(&ch, 1, 1, f); return c; }
int putchar(int c) { return fputc(c, stdout); }
int fputs(const char *s, FILE *f) { fwrite(s, 1, strlen(s), f); return 0; }
int puts(const char *s) { fputs(s, stdout); return putchar('\n'); }
int vfprintf(FILE *f, const char *fmt, va_list ap) {
    char t[4096];
    va_list a2; va_copy(a2, ap);
    int n = vsnprintf(t, sizeof t, fmt, a2);
    va_end(a2);
    if (n < (int)sizeof t) { fwrite(t, 1, n, f); return n; }
    char *big = (char *)malloc(n + 1);
    vsnprintf(big, n + 1, fmt, ap);
    fwrite(big, 1, n, f);
    return n;
}
int fprintf(FILE *f, const char *fmt, ...) { va_list ap; va_start(ap, fmt); int n = vfprintf(f, fmt, ap); va_end(ap); return n; }
int printf(const char *fmt, ...) { va_list ap; va_start(ap, fmt); int n = vfprintf(stdout, fmt, ap); va_end(ap); return n; }
static char kw_ibuf[4096];
static int kw_ipos, kw_ilen, kw_ieof;
int getchar(void) {
    fflush(stdout);
    if (kw_ipos == kw_ilen) {
        if (kw_ieof) return EOF;
        kw_ilen = kh_read(0, kw_ibuf, sizeof kw_ibuf); kw_ipos = 0;
        if (kw_ilen <= 0) { kw_ilen = 0; kw_ieof = 1; return EOF; }
    }
    return (unsigned char)kw_ibuf[kw_ipos++];
}
int setvbuf(FILE *f, char *b, int m, size_t n) { (void)f; (void)b; (void)m; (void)n; return 0; }
__attribute__((noreturn)) void exit(int code) { fflush(stdout); kh_exit(code); }
__attribute__((noreturn)) void _exit(int code) { kh_exit(code); }
__attribute__((noreturn)) void abort(void) { fflush(stdout); __builtin_trap(); }
char *getenv(const char *name) { (void)name; return NULL; }

/* command line arguments (Node: process.argv after the script; browsers: none) */
static char **kw_args(int *argc) {
    int n = 0;
    while (kh_arg(n, NULL, 0) >= 0) n++;
    char **v = (char **)malloc(sizeof(char *) * (n + 2));
    v[0] = "klar";
    for (int i = 0; i < n; i++) {
        int len = kh_arg(i, NULL, 0);
        v[i + 1] = (char *)malloc(len + 1);
        kh_arg(i, v[i + 1], len + 1);
        v[i + 1][len] = 0;
    }
    v[n + 1] = NULL;
    *argc = n + 1;
    return v;
}

/* ------------------------------------------------------------------ time */
time_t time(time_t *t) { time_t r = (time_t)kh_now(); if (t) *t = r; return r; }
clock_t clock(void) { return (clock_t)(kh_clock_ms() * 1000); }
pid_t getpid(void) { return 1; }
int gettimeofday(struct timeval *tv, void *tz) {
    (void)tz;
    double t = kh_now();
    tv->tv_sec = (time_t)t; tv->tv_usec = (long)((t - (double)tv->tv_sec) * 1e6);
    return 0;
}
int clock_gettime(int clk, struct timespec *ts) {
    (void)clk;
    double ms = kh_clock_ms();
    ts->tv_sec = (time_t)(ms / 1000); ts->tv_nsec = (long)((ms - (double)ts->tv_sec * 1000) * 1e6);
    return 0;
}
struct tm *gmtime_r(const time_t *tp, struct tm *tm) {
    long long t = *tp, days = t / 86400, rem = t % 86400;
    if (rem < 0) { rem += 86400; days--; }
    tm->tm_hour = (int)(rem / 3600); tm->tm_min = (int)(rem % 3600 / 60); tm->tm_sec = (int)(rem % 60);
    tm->tm_wday = (int)(((days % 7) + 11) % 7);          /* 1970-01-01 was a Thursday */
    long long z = days + 719468, era = (z >= 0 ? z : z - 146096) / 146097;   /* civil_from_days (H. Hinnant) */
    long long doe = z - era * 146097, yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    long long y = yoe + era * 400, doy = doe - (365 * yoe + yoe / 4 - yoe / 100), mp = (5 * doy + 2) / 153;
    long long d = doy - (153 * mp + 2) / 5 + 1, m = mp < 10 ? mp + 3 : mp - 9;
    tm->tm_year = (int)(y + (m <= 2) - 1900); tm->tm_mon = (int)(m - 1); tm->tm_mday = (int)d;
    tm->tm_yday = 0; tm->tm_isdst = 0;
    return tm;
}

/* ------------------------------------------------------------------ operating-system calls the compiler refuses
 * Declared so the shared runtime compiles; a program can never reach them (they fail like a missing file). */
static int kw_nosys(void) { errno = ENOSYS; return -1; }
FILE *fopen(const char *p, const char *m) { (void)p; (void)m; errno = ENOENT; return NULL; }
int fclose(FILE *f) { (void)f; return 0; }
size_t fread(void *p, size_t s, size_t n, FILE *f) { (void)p; (void)s; (void)n; (void)f; return 0; }
int fileno(FILE *f) { return f->fd; }
int stat(const char *p, struct stat *st) { (void)p; (void)st; return kw_nosys(); }
int fstat(int fd, struct stat *st) { (void)fd; (void)st; return kw_nosys(); }
int mkdir(const char *p, mode_t m) { (void)p; (void)m; return kw_nosys(); }
int unlink(const char *p) { (void)p; return kw_nosys(); }
DIR *opendir(const char *p) { (void)p; errno = ENOSYS; return NULL; }
struct dirent *readdir(DIR *d) { (void)d; return NULL; }
int closedir(DIR *d) { (void)d; return 0; }
int pipe(int fd[2]) { (void)fd; return kw_nosys(); }
int fcntl(int fd, int cmd, ...) { (void)fd; (void)cmd; return kw_nosys(); }
pid_t fork(void) { return kw_nosys(); }
int dup2(int a, int b) { (void)a; (void)b; return kw_nosys(); }
int close(int fd) { (void)fd; return 0; }
ssize_t read(int fd, void *p, size_t n) { (void)fd; (void)p; (void)n; return kw_nosys(); }
ssize_t write(int fd, const void *p, size_t n) { kh_write(fd, (const char *)p, (int)n); return (ssize_t)n; }
int execvp(const char *f, char *const argv[]) { (void)f; (void)argv; return kw_nosys(); }
int execv(const char *f, char *const argv[]) { (void)f; (void)argv; return kw_nosys(); }
pid_t waitpid(pid_t p, int *st, int o) { (void)p; (void)st; (void)o; return kw_nosys(); }
int poll(struct pollfd *p, unsigned long n, int t) { (void)p; (void)n; (void)t; return kw_nosys(); }
int nanosleep(const struct timespec *a, struct timespec *b) { (void)a; (void)b; return 0; }
int setenv(const char *n, const char *v, int o) { (void)n; (void)v; (void)o; return 0; }
int unsetenv(const char *n) { (void)n; return 0; }
int getrlimit(int r, struct rlimit *l) { (void)r; l->rlim_cur = l->rlim_max = RLIM_INFINITY; return 0; }
int setrlimit(int r, const struct rlimit *l) { (void)r; (void)l; return kw_nosys(); }
int sigaltstack(const stack_t *a, stack_t *b) { (void)a; (void)b; return 0; }
int sigaction(int s, const struct sigaction *a, struct sigaction *b) { (void)s; (void)a; (void)b; return 0; }
