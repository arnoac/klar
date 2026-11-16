/* Klar standard library for compiled programs (std.fs, math, time, random, process, regex, json, sqlite, http,
 * web). Included after klar_rt.h when a program uses a standard module. Each function behaves like the
 * interpreter's Python version (klar/stdpy) and the JavaScript one (klar_std.js, klar_node.js): same results,
 * same error texts where the program can see them.
 *
 * The compiler defines KT_<Variant> for every enum variant, and K_STD_REGEX / K_STD_SQLITE / K_STD_NET /
 * K_STD_TLS for the parts that need a library (PCRE2, SQLite, sockets, OpenSSL), so a program links only what
 * it uses. kx_line/kx_col hold the position of the current std call for runtime errors.
 */
#include <time.h>
#include <dirent.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/wait.h>
#include <fcntl.h>
#include <poll.h>
#include <strings.h>

static int kx_line, kx_col;

static const char *kx_errname(int e) {
    switch (e) {
    case ENOENT: return "FileNotFoundError";
    case EACCES: case EPERM: return "PermissionError";
    case EEXIST: return "FileExistsError";
    case EISDIR: return "IsADirectoryError";
    case ENOTDIR: return "NotADirectoryError";
    default: return "OSError";
    }
}
static KEnum *kx_oserr(KStr *path) {
    char t[1024];
    snprintf(t, sizeof t, "%s: [Errno %d] %s: '%s'", kx_errname(errno), errno, strerror(errno), path->data);
    return k_err(k_p2s(k_cstr(t)));
}
static KStr *kx_fmt(const char *fmt, ...) {
    char t[2048];
    va_list ap; va_start(ap, fmt); vsnprintf(t, sizeof t, fmt, ap); va_end(ap);
    return k_cstr(t);
}
static inline KStr *kx_list_str(KList *l, int64_t i) { return K_S2P(KStr *, k_list_data(l)[i]); }
static KList *kx_list_empty(void) { return k_list_with_cap(0, 4); }

/* ------------------------------------------------------------------ std.task
 * Tasks are threads, but only the thread holding kt_mu (the baton) runs Klar code. A task gives the baton up
 * while it waits on a channel or on other tasks, and around blocking io (KX_IO_BEGIN/END), so tasks overlap
 * their io while the runtime's lists and maps are never touched by two threads at once. Same model as the
 * interpreter (klar/tasks.py). If every task is waiting, the program stops with E409.
 */
#ifdef K_STD_TASKS
static pthread_mutex_t kt_mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t kt_cv = PTHREAD_COND_INITIALIZER;
static int kt_on;
static int64_t kt_live = 1, kt_blocked;
typedef int (*KTPred)(void *);
typedef struct KTWaiter { KTPred pred; void *arg; struct KTWaiter *next; } KTWaiter;
static KTWaiter *kt_waiters;    /* a woken task still counts as waiting until it runs */
#define KX_IO_BEGIN do { if (kt_on) pthread_mutex_unlock(&kt_mu); } while (0)
#define KX_IO_END do { if (kt_on) pthread_mutex_lock(&kt_mu); } while (0)
#if defined(__GLIBC__) && !defined(KLAR_GC)
#include <malloc.h>
#endif
static void kt_enable(void) {
    if (kt_on) return;
    kt_on = 1;
#ifdef KLAR_GC
    /* every collection stops every task's thread, so start with room to spare instead of collecting while tiny */
    if (GC_get_heap_size() < ((size_t)64 << 20)) GC_expand_hp(((size_t)64 << 20) - GC_get_heap_size());
#elif defined(__GLIBC__)
    mallopt(M_ARENA_MAX, 1);    /* one task runs at a time, so per-thread malloc arenas only cost memory */
#endif
    pthread_mutex_lock(&kt_mu);
}
static void kt_wait(KTPred pred, void *arg, const char *what, int line, int col) {
    while (!pred(arg)) {
        if (kt_blocked + 1 >= kt_live) {
            int stuck = 1;
            for (KTWaiter *w = kt_waiters; w; w = w->next) if (w->pred(w->arg)) { stuck = 0; break; }
            if (stuck) k_fail("E409", line, col, "deadlock: every task is waiting (%s)", what);
        }
        KTWaiter me = { pred, arg, kt_waiters };
        kt_waiters = &me; kt_blocked++;
        pthread_cond_wait(&kt_cv, &kt_mu);
        KTWaiter **pp = &kt_waiters;
        while (*pp != &me) pp = &(*pp)->next;
        *pp = me.next; kt_blocked--;
    }
}
typedef struct { KClo *job; KS *slot; int64_t *counter; } KTStart;
static void *kt_main(void *p) {
    KTStart *s = (KTStart *)p;
    stack_t ss, off; ss.ss_sp = malloc(65536); ss.ss_size = 65536; ss.ss_flags = 0; sigaltstack(&ss, NULL);
    pthread_mutex_lock(&kt_mu);
    KS r = K_CALL0(s->job);
    if (s->slot) *s->slot = r;
    if (s->counter) (*s->counter)--;
    kt_live--;
    pthread_cond_broadcast(&kt_cv);
    pthread_mutex_unlock(&kt_mu);
    off.ss_sp = NULL; off.ss_size = 0; off.ss_flags = SS_DISABLE; sigaltstack(&off, NULL);
    free(ss.ss_sp);
    return NULL;
}
static void kt_thread(void *(*fn)(void *), void *arg) {
    pthread_attr_t a; pthread_attr_init(&a);
    pthread_attr_setstacksize(&a, (size_t)256 << 20);
    pthread_attr_setdetachstate(&a, PTHREAD_CREATE_DETACHED);
    pthread_t t;
    if (pthread_create(&t, &a, fn, arg) != 0) k_fail("E407", kx_line, kx_col, "cannot start a task: %s", strerror(errno));
    pthread_attr_destroy(&a);
}
static void kt_spawn(KClo *job, KS *slot, int64_t *counter) {
    kt_enable();
    KTStart *s = (KTStart *)K_ALLOC(sizeof(KTStart));
    s->job = job; s->slot = slot; s->counter = counter;
    kt_live++;
    if (counter) (*counter)++;
    kt_thread(kt_main, s);
}
static int kt_zero(void *p) { return *(int64_t *)p == 0; }
static KList *kx_task_all(KList *jobs) {
    int line = kx_line, col = kx_col;
    int64_t n = jobs->len, pending = 0;
    KS *res = (KS *)K_ALLOC(sizeof(KS) * (n ? n : 1));
    for (int64_t i = 0; i < n; i++) kt_spawn(K_S2P(KClo *, k_list_data(jobs)[i]), &res[i], &pending);
    kt_wait(kt_zero, &pending, "task_all", line, col);
    KList *r = k_list_with_cap(0, n + 1);
    for (int64_t i = 0; i < n; i++) r = k_list_push(r, res[i]);
    return r;
}
typedef struct { int64_t active; int open; } KTGroup;
static KTGroup **kt_groups; static int64_t kt_ngroups;
static int64_t kx_task_group(KClo *body) {
    int line = kx_line, col = kx_col;
    kt_enable();
    KTGroup *g = (KTGroup *)K_ALLOC(sizeof(KTGroup));
    g->active = 0; g->open = 1;
    kt_groups = (KTGroup **)K_REALLOC(kt_groups, sizeof(KTGroup *) * (kt_ngroups + 1));
    kt_groups[kt_ngroups++] = g;
    K_CALL1(body, k_p2s(k_struct_new(1, (KS)(kt_ngroups - 1))));
    kt_wait(kt_zero, &g->active, "task_group", line, col);
    g->open = 0;
    return 0;
}
static int64_t kx_task_spawn(KS *gv, KClo *job) {
    int64_t id = (int64_t)gv[0];
    KTGroup *g = id >= 0 && id < kt_ngroups ? kt_groups[id] : NULL;
    if (!g || !g->open) k_fail("E407", kx_line, kx_col, "task_spawn on a group that has already finished");
    kt_spawn(job, NULL, &g->active);
    return 0;
}
typedef struct { KS *q; int64_t head, tail, cap; int closed; } KTChan;
static KTChan **kt_chans; static int64_t kt_nchans;
static KS *kx_chan_new(void) {
    KTChan *c = (KTChan *)K_ALLOC(sizeof(KTChan));
    c->cap = 8; c->head = c->tail = 0; c->closed = 0;
    c->q = (KS *)K_ALLOC(sizeof(KS) * c->cap);
    kt_chans = (KTChan **)K_REALLOC(kt_chans, sizeof(KTChan *) * (kt_nchans + 1));
    kt_chans[kt_nchans++] = c;
    return k_struct_new(1, (KS)(kt_nchans - 1));
}
static KTChan *kt_chan(KS *cv) {
    int64_t id = (int64_t)cv[0];
    if (id < 0 || id >= kt_nchans) k_fail("E407", kx_line, kx_col, "not a channel made by chan_new");
    return kt_chans[id];
}
static int64_t kx_chan_send(KS *cv, KS v) {
    KTChan *c = kt_chan(cv);
    if (c->closed) k_fail("E407", kx_line, kx_col, "chan_send on a closed channel");
    if (c->tail == c->cap) {
        int64_t n = c->tail - c->head;
        if (c->head > c->cap / 2) memmove(c->q, c->q + c->head, sizeof(KS) * n);
        else { c->cap *= 2; c->q = (KS *)K_REALLOC(c->q, sizeof(KS) * c->cap); memmove(c->q, c->q + c->head, sizeof(KS) * n); }
        c->head = 0; c->tail = n;
    }
    c->q[c->tail++] = v;
    if (kt_on) pthread_cond_broadcast(&kt_cv);
    return 0;
}
static int kt_ready(void *p) { KTChan *c = (KTChan *)p; return c->head < c->tail || c->closed; }
static KEnum *kx_chan_recv(KS *cv) {
    int line = kx_line, col = kx_col;
    KTChan *c = kt_chan(cv);
    if (!kt_ready(c)) {
        if (!kt_on) k_fail("E409", line, col, "deadlock: every task is waiting (chan_recv)");
        kt_wait(kt_ready, c, "chan_recv", line, col);
    }
    if (c->head < c->tail) return k_some(c->q[c->head++]);
    return K_NONE;
}
static int64_t kx_chan_close(KS *cv) {
    kt_chan(cv)->closed = 1;
    if (kt_on) pthread_cond_broadcast(&kt_cv);
    return 0;
}
#else
#define KX_IO_BEGIN do {} while (0)
#define KX_IO_END do {} while (0)
#endif

/* ------------------------------------------------------------------ std.fs */
static KEnum *kx_fs_read(KStr *path) {
    FILE *f = fopen(path->data, "rb");
    if (!f) return kx_oserr(path);
    struct stat st;
    if (fstat(fileno(f), &st) == 0 && S_ISDIR(st.st_mode)) { fclose(f); errno = EISDIR; return kx_oserr(path); }
    KSB b; ksb_init(&b);
    char buf[65536]; size_t n;
    while ((n = fread(buf, 1, sizeof buf, f)) > 0) ksb_add(&b, buf, n);
    fclose(f);
    return k_ok(k_p2s(ksb_done(&b)));
}
static KEnum *kx_fs_put(KStr *path, KStr *content, const char *mode) {
    struct stat st;
    if (stat(path->data, &st) == 0 && S_ISDIR(st.st_mode)) { errno = EISDIR; return kx_oserr(path); }
    FILE *f = fopen(path->data, mode);
    if (!f) return kx_oserr(path);
    fwrite(content->data, 1, content->len, f);
    fclose(f);
    return k_ok(0);
}
static KEnum *kx_fs_write(KStr *path, KStr *content) { return kx_fs_put(path, content, "wb"); }
static KEnum *kx_fs_append(KStr *path, KStr *content) { return kx_fs_put(path, content, "ab"); }
static int64_t kx_fs_exists(KStr *path) { struct stat st; return stat(path->data, &st) == 0; }
static int64_t kx_fs_is_dir(KStr *path) { struct stat st; return stat(path->data, &st) == 0 && S_ISDIR(st.st_mode); }
static int kx_strp_cmp(const void *a, const void *b) {
    KStr *x = *(KStr **)a, *y = *(KStr **)b;
    return (int)k_str_cmp(x, y);
}
static KEnum *kx_fs_list_dir(KStr *path) {
    DIR *d = opendir(path->data);
    if (!d) return kx_oserr(path);
    int64_t n = 0, cap = 16;
    KStr **names = (KStr **)K_ALLOC(sizeof(KStr *) * cap);
    struct dirent *e;
    while ((e = readdir(d))) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        if (n == cap) { cap *= 2; names = (KStr **)K_REALLOC(names, sizeof(KStr *) * cap); }
        names[n++] = k_cstr(e->d_name);
    }
    closedir(d);
    qsort(names, n, sizeof(KStr *), kx_strp_cmp);
    KList *r = k_list_with_cap(0, n + 1);
    for (int64_t i = 0; i < n; i++) r = k_list_push(r, k_p2s(names[i]));
    return k_ok(k_p2s(r));
}
static KEnum *kx_fs_mkdir(KStr *path) {
    char *p = strdup(path->data);
    for (char *s = p + 1; *s; s++) {
        if (*s != '/') continue;
        *s = 0;
        if (mkdir(p, 0777) != 0 && errno != EEXIST) { free(p); return kx_oserr(path); }
        *s = '/';
    }
    free(p);
    if (mkdir(path->data, 0777) != 0) {
        if (errno == EEXIST && kx_fs_is_dir(path)) return k_ok(0);
        return kx_oserr(path);
    }
    return k_ok(0);
}
static KEnum *kx_fs_remove(KStr *path) {
    if (kx_fs_is_dir(path)) { errno = EISDIR; return kx_oserr(path); }
    if (unlink(path->data) != 0) return kx_oserr(path);
    return k_ok(0);
}
static KStr *kx_fs_join(KList *parts) {
    KSB b; ksb_init(&b);
    for (int64_t i = 0; i < parts->len; i++) {
        KStr *s = kx_list_str(parts, i);
        if (s->len && s->data[0] == '/') b.len = 0;
        else if (b.len && b.p[b.len - 1] != '/') ksb_add(&b, "/", 1);
        ksb_str(&b, s);
    }
    return ksb_done(&b);
}

/* ------------------------------------------------------------------ std.math */
static double kx_math_pi(void) { return 3.141592653589793; }
static double kx_math_sin(double x) { return sin(x); }
static double kx_math_cos(double x) { return cos(x); }
static double kx_math_tan(double x) { return tan(x); }
static double kx_math_atan2(double y, double x) { return atan2(y, x); }
static double kx_math_exp(double x) { return exp(x); }
static double kx_math_log(double x) { return log(x); }
static double kx_math_log10(double x) { return log10(x); }
static double kx_math_pow(double a, double b) { return pow(a, b); }

/* ------------------------------------------------------------------ std.time (UTC) */
static double kx_time_now(void) { struct timeval tv; gettimeofday(&tv, NULL); return tv.tv_sec + tv.tv_usec / 1e6; }
static int64_t kx_time_millis(void) {
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}
static int64_t kx_time_sleep(double s) {
    fflush(stdout);
    if (s <= 0) return 0;
    struct timespec ts; ts.tv_sec = (time_t)s; ts.tv_nsec = (long)((s - (double)ts.tv_sec) * 1e9);
    KX_IO_BEGIN;
    while (nanosleep(&ts, &ts) != 0 && errno == EINTR) {}
    KX_IO_END;
    return 0;
}
static void kx_utc(double t, struct tm *tm, int64_t *micros) {
    int64_t us = (int64_t)llround(t * 1e6);
    int64_t sec = us >= 0 ? us / 1000000 : -((-us + 999999) / 1000000);
    *micros = us - sec * 1000000;
    time_t tt = (time_t)sec;
    gmtime_r(&tt, tm);
}
static const char *kx_days[] = {"Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"};
static const char *kx_months[] = {"January", "February", "March", "April", "May", "June", "July", "August",
                                  "September", "October", "November", "December"};
static KStr *kx_time_format_utc(double t, KStr *pat) {
    struct tm tm; int64_t us;
    kx_utc(t, &tm, &us);
    KSB b; ksb_init(&b);
    char x[32];
    for (int64_t i = 0; i < pat->len; i++) {
        char c = pat->data[i];
        if (c != '%' || i + 1 >= pat->len) { ksb_add(&b, &c, 1); continue; }
        char d = pat->data[++i];
        switch (d) {
        case 'Y': snprintf(x, sizeof x, "%d", tm.tm_year + 1900); break;
        case 'm': snprintf(x, sizeof x, "%02d", tm.tm_mon + 1); break;
        case 'd': snprintf(x, sizeof x, "%02d", tm.tm_mday); break;
        case 'H': snprintf(x, sizeof x, "%02d", tm.tm_hour); break;
        case 'M': snprintf(x, sizeof x, "%02d", tm.tm_min); break;
        case 'S': snprintf(x, sizeof x, "%02d", tm.tm_sec); break;
        case 'y': snprintf(x, sizeof x, "%02d", (tm.tm_year + 1900) % 100); break;
        case 'A': snprintf(x, sizeof x, "%s", kx_days[tm.tm_wday]); break;
        case 'a': snprintf(x, sizeof x, "%.3s", kx_days[tm.tm_wday]); break;
        case 'B': snprintf(x, sizeof x, "%s", kx_months[tm.tm_mon]); break;
        case 'b': snprintf(x, sizeof x, "%.3s", kx_months[tm.tm_mon]); break;
        case 'f': snprintf(x, sizeof x, "%06lld", (long long)us); break;
        case '%': strcpy(x, "%"); break;
        default: snprintf(x, sizeof x, "%%%c", d);
        }
        ksb_c(&b, x);
    }
    return ksb_done(&b);
}
static KStr *kx_time_iso_utc(double t) {
    struct tm tm; int64_t us;
    kx_utc(t, &tm, &us);
    char x[64];
    int n = snprintf(x, sizeof x, "%04d-%02d-%02dT%02d:%02d:%02d", tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday,
                     tm.tm_hour, tm.tm_min, tm.tm_sec);
    if (us) n += snprintf(x + n, sizeof x - n, ".%06lld", (long long)us);
    snprintf(x + n, sizeof x - n, "+00:00");
    return k_cstr(x);
}

/* ------------------------------------------------------------------ std.random (mulberry32, as on JavaScript) */
static uint32_t kx_rng_state;
static int kx_rng_ready;
static double kx_rng(void) {
    if (!kx_rng_ready) { kx_rng_state = (uint32_t)time(NULL) ^ ((uint32_t)getpid() << 16) ^ (uint32_t)clock(); kx_rng_ready = 1; }
    uint32_t t = (kx_rng_state += 0x6D2B79F5u);
    t = (t ^ (t >> 15)) * (t | 1);
    t ^= t + (t ^ (t >> 7)) * (t | 61);
    return (double)(t ^ (t >> 14)) / 4294967296.0;
}
static int64_t kx_random_seed(int64_t n) { kx_rng_state = (uint32_t)n; kx_rng_ready = 1; return 0; }
static int64_t kx_random_int_between(int64_t lo, int64_t hi) { return lo + (int64_t)floor(kx_rng() * (double)(hi - lo + 1)); }
static double kx_random_unit_float(void) { return kx_rng(); }

/* ------------------------------------------------------------------ std.crypto (SHA-256, HMAC, PBKDF2) */
typedef struct { uint32_t h[8]; uint8_t buf[64]; uint64_t n; } KSha;
static const uint32_t ksha_k[64] = {
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,
    0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,
    0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,
    0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,
    0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,
    0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
#define KSHA_ROR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))
static void ksha_block(KSha *s, const uint8_t *p) {
    uint32_t w[64], a, b, c, d, e, f, g, h;
    for (int i = 0; i < 16; i++) w[i] = (uint32_t)p[4*i] << 24 | (uint32_t)p[4*i+1] << 16 | (uint32_t)p[4*i+2] << 8 | p[4*i+3];
    for (int i = 16; i < 64; i++) {
        uint32_t s0 = KSHA_ROR(w[i-15], 7) ^ KSHA_ROR(w[i-15], 18) ^ (w[i-15] >> 3);
        uint32_t s1 = KSHA_ROR(w[i-2], 17) ^ KSHA_ROR(w[i-2], 19) ^ (w[i-2] >> 10);
        w[i] = w[i-16] + s0 + w[i-7] + s1;
    }
    a = s->h[0]; b = s->h[1]; c = s->h[2]; d = s->h[3]; e = s->h[4]; f = s->h[5]; g = s->h[6]; h = s->h[7];
    for (int i = 0; i < 64; i++) {
        uint32_t t1 = h + (KSHA_ROR(e, 6) ^ KSHA_ROR(e, 11) ^ KSHA_ROR(e, 25)) + ((e & f) ^ (~e & g)) + ksha_k[i] + w[i];
        uint32_t t2 = (KSHA_ROR(a, 2) ^ KSHA_ROR(a, 13) ^ KSHA_ROR(a, 22)) + ((a & b) ^ (a & c) ^ (b & c));
        h = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
    }
    s->h[0] += a; s->h[1] += b; s->h[2] += c; s->h[3] += d; s->h[4] += e; s->h[5] += f; s->h[6] += g; s->h[7] += h;
}
static void ksha_init(KSha *s) {
    static const uint32_t iv[8] = {0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    memcpy(s->h, iv, sizeof iv); s->n = 0;
}
static void ksha_add(KSha *s, const uint8_t *p, size_t n) {
    while (n > 0) {
        size_t used = s->n % 64, take = 64 - used < n ? 64 - used : n;
        memcpy(s->buf + used, p, take);
        s->n += take; p += take; n -= take;
        if (s->n % 64 == 0) ksha_block(s, s->buf);
    }
}
static void ksha_done(KSha *s, uint8_t out[32]) {
    uint64_t bits = s->n * 8;
    uint8_t pad = 0x80, zero = 0, len[8];
    ksha_add(s, &pad, 1);
    while (s->n % 64 != 56) ksha_add(s, &zero, 1);
    for (int i = 0; i < 8; i++) len[i] = (uint8_t)(bits >> (56 - 8 * i));
    ksha_add(s, len, 8);
    for (int i = 0; i < 8; i++) { out[4*i] = s->h[i] >> 24; out[4*i+1] = s->h[i] >> 16; out[4*i+2] = s->h[i] >> 8; out[4*i+3] = s->h[i]; }
}
static void khmac(const uint8_t *key, size_t kn, const uint8_t *msg, size_t mn, uint8_t out[32]) {
    uint8_t k[64] = {0}, ipad[64], opad[64], inner[32];
    if (kn > 64) { KSha s; ksha_init(&s); ksha_add(&s, key, kn); ksha_done(&s, k); } else memcpy(k, key, kn);
    for (int i = 0; i < 64; i++) { ipad[i] = k[i] ^ 0x36; opad[i] = k[i] ^ 0x5c; }
    KSha s; ksha_init(&s); ksha_add(&s, ipad, 64); ksha_add(&s, msg, mn); ksha_done(&s, inner);
    ksha_init(&s); ksha_add(&s, opad, 64); ksha_add(&s, inner, 32); ksha_done(&s, out);
}
static KStr *khex(const uint8_t *p, size_t n) {
    static const char d[] = "0123456789abcdef";
    KStr *s = k_str_alloc((int64_t)n * 2);
    for (size_t i = 0; i < n; i++) { s->data[2*i] = d[p[i] >> 4]; s->data[2*i+1] = d[p[i] & 15]; }
    return s;
}
static KStr *kx_crypto_sha256(KStr *t) {
    KSha s; uint8_t out[32];
    ksha_init(&s); ksha_add(&s, (const uint8_t *)t->data, (size_t)t->len); ksha_done(&s, out);
    return khex(out, 32);
}
static KStr *kx_crypto_hmac_sha256(KStr *key, KStr *t) {
    uint8_t out[32];
    khmac((const uint8_t *)key->data, (size_t)key->len, (const uint8_t *)t->data, (size_t)t->len, out);
    return khex(out, 32);
}
static void krandom_bytes(uint8_t *p, size_t n) {
    FILE *f = fopen("/dev/urandom", "rb");
    if (!f || fread(p, 1, n, f) != n) k_fail("E407", kx_line, kx_col, "no secure random source (/dev/urandom)");
    fclose(f);
}
static KStr *kx_crypto_token(int64_t n) {
    if (n < 16 || n > (1 << 20))
        k_fail("E407", kx_line, kx_col, "ValueError: crypto_token(%lld): use between 16 and 1048576 bytes (16 bytes = 128 bits is the least that cannot be guessed)", (long long)n);
    uint8_t *buf = (uint8_t *)K_ALLOC_ATOMIC(n + 1);
    krandom_bytes(buf, (size_t)n);
    return khex(buf, (size_t)n);
}
#define KX_PBKDF2_ITERS 600000
/* PBKDF2-HMAC-SHA-256 with one 32-byte block; the HMAC pads are hashed once and reused for every iteration */
static void kpbkdf2(const uint8_t *pw, size_t pn, const uint8_t *salt, size_t sn, int64_t iters, uint8_t out[32]) {
    uint8_t k[64] = {0}, pad[64], u[32];
    if (pn > 64) { KSha s; ksha_init(&s); ksha_add(&s, pw, pn); ksha_done(&s, k); } else memcpy(k, pw, pn);
    KSha in0, out0;
    for (int i = 0; i < 64; i++) pad[i] = k[i] ^ 0x36;
    ksha_init(&in0); ksha_add(&in0, pad, 64);
    for (int i = 0; i < 64; i++) pad[i] = k[i] ^ 0x5c;
    ksha_init(&out0); ksha_add(&out0, pad, 64);
    KSha s = in0; uint8_t one[4] = {0, 0, 0, 1};
    ksha_add(&s, salt, sn); ksha_add(&s, one, 4); ksha_done(&s, u);
    s = out0; ksha_add(&s, u, 32); ksha_done(&s, u);
    memcpy(out, u, 32);
    for (int64_t it = 1; it < iters; it++) {
        s = in0; ksha_add(&s, u, 32); ksha_done(&s, u);
        s = out0; ksha_add(&s, u, 32); ksha_done(&s, u);
        for (int i = 0; i < 32; i++) out[i] ^= u[i];
    }
}
static KStr *kx_crypto_password_hash(KStr *pw) {
    uint8_t salt_raw[16], dk[32];
    krandom_bytes(salt_raw, 16);
    KStr *salt = khex(salt_raw, 16);
    KX_IO_BEGIN;
    kpbkdf2((const uint8_t *)pw->data, (size_t)pw->len, (const uint8_t *)salt->data, 32, KX_PBKDF2_ITERS, dk);
    KX_IO_END;
    return kx_fmt("pbkdf2_sha256$%d$%s$%s", KX_PBKDF2_ITERS, salt->data, khex(dk, 32)->data);
}
static int64_t kx_crypto_needs_rehash(KStr *stored) {
    const char *p = stored->data;
    if (strncmp(p, "pbkdf2_sha256$", 14) != 0) return 1;
    long long n = 0;
    for (p += 14; *p >= '0' && *p <= '9' && n < 1000000000; p++) n = n * 10 + (*p - '0');
    return *p != '$' || n < KX_PBKDF2_ITERS;
}
static int64_t kx_crypto_equal(KStr *a, KStr *b) {
    if (a->len != b->len) return 0;
    unsigned char diff = 0;
    for (int64_t i = 0; i < a->len; i++) diff |= (unsigned char)(a->data[i] ^ b->data[i]);
    return diff == 0;
}
static int64_t kx_crypto_password_verify(KStr *pw, KStr *stored) {
    const char *p = stored->data;
    if (strncmp(p, "pbkdf2_sha256$", 14) != 0) return 0;
    p += 14;
    int64_t iters = 0;
    if (*p < '0' || *p > '9') return 0;
    while (*p >= '0' && *p <= '9') { iters = iters * 10 + (*p - '0'); if (iters > 10000000) return 0; p++; }
    if (*p != '$' || iters <= 0) return 0;
    const char *salt = ++p;
    const char *dollar = strchr(salt, '$');
    if (!dollar || strchr(dollar + 1, '$')) return 0;
    uint8_t dk[32];
    kpbkdf2((const uint8_t *)pw->data, (size_t)pw->len, (const uint8_t *)salt, (size_t)(dollar - salt), iters, dk);
    return kx_crypto_equal(khex(dk, 32), k_cstr(dollar + 1));
}

/* ------------------------------------------------------------------ std.process */
static KEnum *kx_process_env_get(KStr *name) {
    const char *v = getenv(name->data);
    return v ? k_some(k_p2s(k_cstr(v))) : K_NONE;
}
static int64_t kx_process_exit(int64_t code) { fflush(stdout); fflush(stderr); exit((int)code); }
static KEnum *kx_process_run(KList *cmd, KStr *input) {
    if (cmd->len == 0) return k_err(k_p2s(k_cstr("IndexError: list index out of range")));
    int in[2], out[2], err[2], st[2];
    if (pipe(in) || pipe(out) || pipe(err) || pipe(st)) return kx_oserr(kx_list_str(cmd, 0));
    fcntl(st[1], F_SETFD, FD_CLOEXEC);
    fflush(stdout); fflush(stderr);
    char **argv = (char **)K_ALLOC(sizeof(char *) * (cmd->len + 1));
    for (int64_t i = 0; i < cmd->len; i++) argv[i] = kx_list_str(cmd, i)->data;
    argv[cmd->len] = NULL;
    KStr *cmd0 = kx_list_str(cmd, 0);
    KX_IO_BEGIN;
    pid_t pid = fork();
    if (pid == 0) {
        dup2(in[0], 0); dup2(out[1], 1); dup2(err[1], 2);
        close(in[1]); close(out[0]); close(err[0]); close(st[0]);
        unsetenv("KLAR_STACK_SET");
        execvp(argv[0], argv);
        int e = errno;
        if (write(st[1], &e, sizeof e) < 0) {}
        _exit(127);
    }
    close(in[0]); close(out[1]); close(err[1]); close(st[1]);
    int e = 0;
    if (read(st[0], &e, sizeof e) == (ssize_t)sizeof e) {
        close(st[0]); close(in[1]); close(out[0]); close(err[0]);
        waitpid(pid, NULL, 0);
        KX_IO_END;
        errno = e;
        return kx_oserr(cmd0);
    }
    close(st[0]);
    KSB ob, eb; ksb_init(&ob); ksb_init(&eb);
    int64_t woff = 0;
    if (input->len == 0) { close(in[1]); in[1] = -1; }
    else fcntl(in[1], F_SETFL, O_NONBLOCK);
    int open_out = 1, open_err = 1;
    char buf[65536];
    while (open_out || open_err) {
        struct pollfd p[3]; int np = 0, io = -1, ie = -1, iw = -1;
        if (open_out) { io = np; p[np].fd = out[0]; p[np++].events = POLLIN; }
        if (open_err) { ie = np; p[np].fd = err[0]; p[np++].events = POLLIN; }
        if (in[1] >= 0) { iw = np; p[np].fd = in[1]; p[np++].events = POLLOUT; }
        if (poll(p, np, -1) < 0) { if (errno == EINTR) continue; break; }
        if (io >= 0 && p[io].revents) { ssize_t n = read(out[0], buf, sizeof buf); if (n > 0) ksb_add(&ob, buf, n); else open_out = 0; }
        if (ie >= 0 && p[ie].revents) { ssize_t n = read(err[0], buf, sizeof buf); if (n > 0) ksb_add(&eb, buf, n); else open_err = 0; }
        if (iw >= 0 && p[iw].revents) {
            ssize_t n = write(in[1], input->data + woff, input->len - woff);
            if (n > 0) woff += n;
            if (n < 0 && errno != EAGAIN) woff = input->len;
            if (woff >= input->len) { close(in[1]); in[1] = -1; }
        }
    }
    if (in[1] >= 0) close(in[1]);
    close(out[0]); close(err[0]);
    int status = 0;
    waitpid(pid, &status, 0);
    KX_IO_END;
    int64_t code = WIFEXITED(status) ? WEXITSTATUS(status) : -WTERMSIG(status);
    KS *r = k_struct_new(3, (KS)code, k_p2s(ksb_done(&ob)), k_p2s(ksb_done(&eb)));
    return k_ok(k_p2s(r));
}

/* ------------------------------------------------------------------ web helpers */
static int kx_hex(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}
static KStr *kx_unquote(const char *s, int64_t n, int plus) {
    KSB b; ksb_init(&b);
    for (int64_t i = 0; i < n; i++) {
        char c = s[i];
        if (c == '+' && plus) c = ' ';
        else if (c == '%' && i + 2 < n && kx_hex(s[i + 1]) >= 0 && kx_hex(s[i + 2]) >= 0) {
            c = (char)(kx_hex(s[i + 1]) * 16 + kx_hex(s[i + 2])); i += 2;
        }
        ksb_add(&b, &c, 1);
    }
    return ksb_done(&b);
}
static KStr *kx_web_url_decode(KStr *s) { return kx_unquote(s->data, s->len, 1); }
static KStr *kx_web_url_encode(KStr *s) {
    KSB b; ksb_init(&b);
    for (int64_t i = 0; i < s->len; i++) {
        unsigned char c = (unsigned char)s->data[i];
        if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '.' || c == '-' || c == '~')
            ksb_add(&b, (char *)&c, 1);
        else { char t[4]; snprintf(t, sizeof t, "%%%02X", c); ksb_add(&b, t, 3); }
    }
    return ksb_done(&b);
}
static KMap *kx_query_decode(const char *s, int64_t n, int keep_blank) {
    KMap *m = k_map_empty(1);
    int64_t i = 0;
    while (i <= n && n > 0) {
        int64_t j = i;
        while (j < n && s[j] != '&') j++;
        if (j > i) {
            int64_t eq = i;
            while (eq < j && s[eq] != '=') eq++;
            KStr *k = kx_unquote(s + i, eq - i, 1);
            KStr *v = eq < j ? kx_unquote(s + eq + 1, j - eq - 1, 1) : k_cstr("");
            if (v->len || keep_blank) m = k_map_put(m, k_p2s(k), k_p2s(v));
        }
        i = j + 1;
    }
    return m;
}
static KMap *kx_web_form_decode(KStr *s) { return kx_query_decode(s->data, s->len, 1); }
static KStr *kx_web_mime_type(KStr *path) {
    static const char *types[][2] = {
        {".html", "text/html"}, {".htm", "text/html"}, {".css", "text/css"}, {".js", "text/javascript"},
        {".mjs", "text/javascript"}, {".json", "application/json"}, {".txt", "text/plain"}, {".md", "text/markdown"},
        {".csv", "text/csv"}, {".xml", "application/xml"}, {".svg", "image/svg+xml"}, {".png", "image/png"},
        {".jpg", "image/jpeg"}, {".jpeg", "image/jpeg"}, {".gif", "image/gif"}, {".webp", "image/webp"},
        {".ico", "image/vnd.microsoft.icon"}, {".pdf", "application/pdf"}, {".wasm", "application/wasm"},
        {".woff", "font/woff"}, {".woff2", "font/woff2"}, {".mp3", "audio/mpeg"}, {".mp4", "video/mp4"},
        {".zip", "application/zip"},
    };
    const char *dot = strrchr(path->data, '.');
    const char *slash = strrchr(path->data, '/');
    const char *t = "application/octet-stream";
    if (dot && (!slash || dot > slash)) {
        char ext[16]; int n = 0;
        for (const char *p = dot; *p && n < 15; p++) ext[n++] = (char)((*p >= 'A' && *p <= 'Z') ? *p + 32 : *p);
        ext[n] = 0;
        for (size_t i = 0; i < sizeof types / sizeof types[0]; i++) if (!strcmp(ext, types[i][0])) { t = types[i][1]; break; }
    }
    if (!strncmp(t, "text/", 5) || !strcmp(t, "application/javascript") || !strcmp(t, "application/json") || !strcmp(t, "image/svg+xml"))
        return kx_fmt("%s; charset=utf-8", t);
    return k_cstr(t);
}

/* ------------------------------------------------------------------ JSON
 * Text is parsed into a small tree (KJ) first; it then becomes a Json value or, for json_decode, any type
 * described by a KD descriptor that the compiler generates. Numbers keep their source text so 64-bit ints
 * are read exactly. Output is byte-identical to the other runtimes (JavaScript number formatting).
 */
typedef struct KJ { int t; double num; const char *raw; int rawn; int isint; KStr *s; struct KJ **items; KStr **keys; int64_t n; } KJ;
enum { KJ_NULL, KJ_BOOL, KJ_NUM, KJ_STR, KJ_ARR, KJ_OBJ };
typedef struct { const char *p, *end; const char *err; } KJP;

static void kj_ws(KJP *s) { while (s->p < s->end && (*s->p == ' ' || *s->p == '\t' || *s->p == '\n' || *s->p == '\r')) s->p++; }
static KJ *kj_new(int t) { KJ *j = (KJ *)K_ALLOC(sizeof(KJ)); memset(j, 0, sizeof *j); j->t = t; return j; }
static void kj_utf8(KSB *b, uint32_t c) {
    char t[4]; int n;
    if (c < 0x80) { t[0] = (char)c; n = 1; }
    else if (c < 0x800) { t[0] = (char)(0xC0 | (c >> 6)); t[1] = (char)(0x80 | (c & 63)); n = 2; }
    else if (c < 0x10000) { t[0] = (char)(0xE0 | (c >> 12)); t[1] = (char)(0x80 | ((c >> 6) & 63)); t[2] = (char)(0x80 | (c & 63)); n = 3; }
    else { t[0] = (char)(0xF0 | (c >> 18)); t[1] = (char)(0x80 | ((c >> 12) & 63)); t[2] = (char)(0x80 | ((c >> 6) & 63)); t[3] = (char)(0x80 | (c & 63)); n = 4; }
    ksb_add(b, t, n);
}
static int kj_hex4(KJP *s, uint32_t *out) {
    if (s->end - s->p < 4) return 0;
    uint32_t v = 0;
    for (int i = 0; i < 4; i++) { int h = kx_hex(s->p[i]); if (h < 0) return 0; v = v * 16 + (uint32_t)h; }
    s->p += 4; *out = v; return 1;
}
static KStr *kj_string(KJP *s) {
    s->p++;
    KSB b; ksb_init(&b);
    while (s->p < s->end && *s->p != '"') {
        unsigned char c = (unsigned char)*s->p;
        if (c < 0x20) { s->err = "Invalid control character in string"; return NULL; }
        if (c != '\\') { ksb_add(&b, s->p, 1); s->p++; continue; }
        s->p++;
        if (s->p >= s->end) break;
        char e = *s->p++;
        switch (e) {
        case '"': ksb_add(&b, "\"", 1); break;
        case '\\': ksb_add(&b, "\\", 1); break;
        case '/': ksb_add(&b, "/", 1); break;
        case 'b': ksb_add(&b, "\b", 1); break;
        case 'f': ksb_add(&b, "\f", 1); break;
        case 'n': ksb_add(&b, "\n", 1); break;
        case 'r': ksb_add(&b, "\r", 1); break;
        case 't': ksb_add(&b, "\t", 1); break;
        case 'u': {
            uint32_t u;
            if (!kj_hex4(s, &u)) { s->err = "Invalid \\uXXXX escape"; return NULL; }
            if (u >= 0xD800 && u < 0xDC00 && s->end - s->p >= 6 && s->p[0] == '\\' && s->p[1] == 'u') {
                const char *save = s->p; uint32_t lo;
                s->p += 2;
                if (kj_hex4(s, &lo) && lo >= 0xDC00 && lo < 0xE000) u = 0x10000 + ((u - 0xD800) << 10) + (lo - 0xDC00);
                else s->p = save;
            }
            kj_utf8(&b, u);
            break;
        }
        default: s->err = "Invalid \\escape"; return NULL;
        }
    }
    if (s->p >= s->end) { s->err = "Unterminated string"; return NULL; }
    s->p++;
    return ksb_done(&b);
}
static KJ *kj_value(KJP *s, int depth) {
    kj_ws(s);
    if (s->p >= s->end) { s->err = "Expecting value"; return NULL; }
    if (depth > 10000) { s->err = "Nesting too deep"; return NULL; }
    char c = *s->p;
    if (c == '{' || c == '[') {
        int obj = c == '{';
        KJ *j = kj_new(obj ? KJ_OBJ : KJ_ARR);
        int64_t cap = 4;
        j->items = (KJ **)K_ALLOC(sizeof(KJ *) * cap);
        if (obj) j->keys = (KStr **)K_ALLOC(sizeof(KStr *) * cap);
        s->p++;
        kj_ws(s);
        if (s->p < s->end && *s->p == (obj ? '}' : ']')) { s->p++; return j; }
        for (;;) {
            KStr *key = NULL;
            if (obj) {
                kj_ws(s);
                if (s->p >= s->end || *s->p != '"') { s->err = "Expecting property name enclosed in double quotes"; return NULL; }
                if (!(key = kj_string(s))) return NULL;
                kj_ws(s);
                if (s->p >= s->end || *s->p != ':') { s->err = "Expecting ':' delimiter"; return NULL; }
                s->p++;
            }
            KJ *v = kj_value(s, depth + 1);
            if (!v) return NULL;
            if (j->n == cap) {
                cap *= 2;
                j->items = (KJ **)K_REALLOC(j->items, sizeof(KJ *) * cap);
                if (obj) j->keys = (KStr **)K_REALLOC(j->keys, sizeof(KStr *) * cap);
            }
            if (obj) j->keys[j->n] = key;
            j->items[j->n++] = v;
            kj_ws(s);
            if (s->p < s->end && *s->p == ',') { s->p++; continue; }
            if (s->p < s->end && *s->p == (obj ? '}' : ']')) { s->p++; return j; }
            s->err = "Expecting ',' delimiter";
            return NULL;
        }
    }
    if (c == '"') { KStr *str = kj_string(s); if (!str) return NULL; KJ *j = kj_new(KJ_STR); j->s = str; return j; }
    if (s->end - s->p >= 4 && !strncmp(s->p, "null", 4)) { s->p += 4; return kj_new(KJ_NULL); }
    if (s->end - s->p >= 4 && !strncmp(s->p, "true", 4)) { s->p += 4; KJ *j = kj_new(KJ_BOOL); j->num = 1; return j; }
    if (s->end - s->p >= 5 && !strncmp(s->p, "false", 5)) { s->p += 5; return kj_new(KJ_BOOL); }
    const char *start = s->p;
    if (*s->p == '-') s->p++;
    if (s->p >= s->end || *s->p < '0' || *s->p > '9') { s->p = start; s->err = "Expecting value"; return NULL; }
    if (*s->p == '0') s->p++;
    else while (s->p < s->end && *s->p >= '0' && *s->p <= '9') s->p++;
    int isint = 1;
    if (s->p < s->end && *s->p == '.') {
        isint = 0; s->p++;
        if (s->p >= s->end || *s->p < '0' || *s->p > '9') { s->err = "Expecting value"; return NULL; }
        while (s->p < s->end && *s->p >= '0' && *s->p <= '9') s->p++;
    }
    if (s->p < s->end && (*s->p == 'e' || *s->p == 'E')) {
        isint = 0; s->p++;
        if (s->p < s->end && (*s->p == '+' || *s->p == '-')) s->p++;
        if (s->p >= s->end || *s->p < '0' || *s->p > '9') { s->err = "Expecting value"; return NULL; }
        while (s->p < s->end && *s->p >= '0' && *s->p <= '9') s->p++;
    }
    KJ *j = kj_new(KJ_NUM);
    j->raw = start; j->rawn = (int)(s->p - start); j->isint = isint;
    char tmp[512]; int n = j->rawn < 511 ? j->rawn : 511;
    memcpy(tmp, start, n); tmp[n] = 0;
    j->num = strtod(tmp, NULL);
    return j;
}
static KJ *kj_parse(KStr *text, KStr **err) {
    KJP s = {text->data, text->data + text->len, NULL};
    KJ *j = kj_value(&s, 0);
    if (j) { kj_ws(&s); if (s.p < s.end) { s.err = "Extra data"; j = NULL; } }
    if (!j) {
        int64_t line = 1, col = 1;
        for (const char *q = text->data; q < s.p; q++) { if (*q == '\n') { line++; col = 1; } else col++; }
        *err = kx_fmt("%s: line %lld column %lld (char %lld)", s.err, (long long)line, (long long)col, (long long)(s.p - text->data));
    }
    return j;
}

/* JavaScript's Number.prototype.toString for a double. */
static void kx_js_num(KSB *b, double f) {
    if (f != f || isinf(f)) { ksb_c(b, "null"); return; }
    if (f == 0) { ksb_c(b, "0"); return; }
    char tmp[64];
    k_shortest_e(tmp, sizeof tmp, f);
    char ds[32]; int k = 0, neg = 0; char *q = tmp;
    if (*q == '-') { neg = 1; q++; }
    for (; *q && *q != 'e'; q++) if (*q != '.') ds[k++] = *q;
    int e10 = atoi(q + 1);
    while (k > 1 && ds[k - 1] == '0') k--;
    ds[k] = 0;
    int n = e10 + 1;                       /* the decimal point sits after n digits */
    if (neg) ksb_c(b, "-");
    if (k <= n && n <= 21) { ksb_add(b, ds, k); for (int i = k; i < n; i++) ksb_c(b, "0"); }
    else if (0 < n && n <= 21) { ksb_add(b, ds, n); ksb_c(b, "."); ksb_add(b, ds + n, k - n); }
    else if (-6 < n && n <= 0) { ksb_c(b, "0."); for (int i = 0; i < -n; i++) ksb_c(b, "0"); ksb_add(b, ds, k); }
    else {
        int e = n - 1; char t[16];
        ksb_add(b, ds, 1);
        if (k > 1) { ksb_c(b, "."); ksb_add(b, ds + 1, k - 1); }
        snprintf(t, sizeof t, "e%c%d", e > 0 ? '+' : '-', e > 0 ? e : -e);
        ksb_c(b, t);
    }
}
static void kx_json_quote(KSB *b, KStr *s) {
    ksb_c(b, "\"");
    for (int64_t i = 0; i < s->len; i++) {
        unsigned char c = (unsigned char)s->data[i];
        switch (c) {
        case '"': ksb_c(b, "\\\""); break;
        case '\\': ksb_c(b, "\\\\"); break;
        case '\n': ksb_c(b, "\\n"); break;
        case '\r': ksb_c(b, "\\r"); break;
        case '\t': ksb_c(b, "\\t"); break;
        case '\b': ksb_c(b, "\\b"); break;
        case '\f': ksb_c(b, "\\f"); break;
        default:
            if (c < 0x20) { char t[8]; snprintf(t, sizeof t, "\\u%04x", c); ksb_c(b, t); }
            else ksb_add(b, (char *)&c, 1);
        }
    }
    ksb_c(b, "\"");
}
static void kx_indent(KSB *b, const char *ind, int depth) { ksb_c(b, "\n"); for (int i = 0; i < depth; i++) ksb_c(b, ind); }
/* writes a KJ tree */
static void kj_write(KSB *b, KJ *j, const char *ind, int depth) {
    switch (j->t) {
    case KJ_NULL: ksb_c(b, "null"); return;
    case KJ_BOOL: ksb_c(b, j->num ? "true" : "false"); return;
    case KJ_NUM: if (j->raw && j->isint) ksb_add(b, j->raw, j->rawn); else kx_js_num(b, j->num); return;
    case KJ_STR: kx_json_quote(b, j->s); return;
    default: {
        int obj = j->t == KJ_OBJ;
        ksb_c(b, obj ? "{" : "[");
        for (int64_t i = 0; i < j->n; i++) {
            if (i) ksb_c(b, ",");
            if (*ind) kx_indent(b, ind, depth + 1);
            if (obj) { kx_json_quote(b, j->keys[i]); ksb_c(b, *ind ? ": " : ":"); }
            kj_write(b, j->items[i], ind, depth + 1);
        }
        if (j->n && *ind) kx_indent(b, ind, depth);
        ksb_c(b, obj ? "}" : "]");
    }
    }
}

#ifdef KT_JNull
/* Json enum <-> KJ */
static KEnum *kj_to_json(KJ *j) {
    switch (j->t) {
    case KJ_NULL: return k_enum_new(KT_JNull, 0);
    case KJ_BOOL: return k_enum_new(KT_JBool, 1, (KS)(j->num != 0));
    case KJ_NUM: return k_enum_new(KT_JNum, 1, k_d2s(j->num));
    case KJ_STR: return k_enum_new(KT_JStr, 1, k_p2s(j->s));
    case KJ_ARR: {
        KList *l = k_list_with_cap(0, j->n + 1);
        for (int64_t i = 0; i < j->n; i++) l = k_list_push(l, k_p2s(kj_to_json(j->items[i])));
        return k_enum_new(KT_JArr, 1, k_p2s(l));
    }
    default: {
        KMap *m = k_map_empty(1);
        for (int64_t i = 0; i < j->n; i++) m = k_map_put(m, k_p2s(j->keys[i]), k_p2s(kj_to_json(j->items[i])));
        return k_enum_new(KT_JObj, 1, k_p2s(m));
    }
    }
}
static KJ *kj_from_json(KEnum *v) {
    KJ *j;
    switch (v->tag) {
    case KT_JNull: return kj_new(KJ_NULL);
    case KT_JBool: j = kj_new(KJ_BOOL); j->num = v->f[0] ? 1 : 0; return j;
    case KT_JNum: {
        j = kj_new(KJ_NUM); j->num = k_s2d(v->f[0]);
        return j;
    }
    case KT_JStr: j = kj_new(KJ_STR); j->s = K_S2P(KStr *, v->f[0]); return j;
    case KT_JArr: {
        KList *l = K_S2P(KList *, v->f[0]);
        j = kj_new(KJ_ARR); j->n = l->len;
        j->items = (KJ **)K_ALLOC(sizeof(KJ *) * (l->len + 1));
        for (int64_t i = 0; i < l->len; i++) j->items[i] = kj_from_json(K_S2P(KEnum *, k_list_data(l)[i]));
        return j;
    }
    default: {
        KMap *m = K_S2P(KMap *, v->f[0]);
        KList *ks = k_map_keys(m), *vs = k_map_values(m);
        j = kj_new(KJ_OBJ); j->n = ks->len;
        j->items = (KJ **)K_ALLOC(sizeof(KJ *) * (ks->len + 1));
        j->keys = (KStr **)K_ALLOC(sizeof(KStr *) * (ks->len + 1));
        for (int64_t i = 0; i < ks->len; i++) {
            j->keys[i] = K_S2P(KStr *, k_list_data(ks)[i]);
            j->items[i] = kj_from_json(K_S2P(KEnum *, k_list_data(vs)[i]));
        }
        return j;
    }
    }
}
static KEnum *kx_json_parse(KStr *text) {
    KStr *err = NULL;
    KJ *j = kj_parse(text, &err);
    if (!j) return k_err(k_p2s(kx_fmt("JSONDecodeError: %s", err->data)));
    return k_ok(k_p2s(kj_to_json(j)));
}
static KStr *kx_json_stringify(KEnum *v) { KSB b; ksb_init(&b); kj_write(&b, kj_from_json(v), "", 0); return ksb_done(&b); }
static KStr *kx_json_pretty(KEnum *v) { KSB b; ksb_init(&b); kj_write(&b, kj_from_json(v), "  ", 0); return ksb_done(&b); }
#endif

/* Type descriptors for automatic JSON (generated by the compiler). */
typedef struct KD KD;
typedef struct { const char *name; int64_t tag; int nf; const char **labels; KD **types; } KDV;
struct KD {
    int k;                 /* KD_INT ... */
    const char *name;      /* struct/enum name */
    KD *e, *kk, *v;        /* list element, map key, map value */
    int nf; const char **fnames; KD **ftypes;     /* struct fields */
    int nv; KDV *vs;       /* enum variants */
};
enum { KD_INT, KD_FLOAT, KD_BOOL, KD_UNIT, KD_STR, KD_LIST, KD_MAP, KD_STRUCT, KD_ENUM, KD_OTHER, KD_TUPLE };

typedef struct { int failed; KStr *msg; } KJErr;
static KJ *kj_num_i(int64_t v) {
    KJ *j = kj_new(KJ_NUM); char t[32]; int n = snprintf(t, sizeof t, "%lld", (long long)v);
    char *r = (char *)K_ALLOC_ATOMIC(n + 1); memcpy(r, t, n + 1);
    j->raw = r; j->rawn = n; j->isint = 1; j->num = (double)v;
    return j;
}
static KJ *kj_obj(int64_t cap) {
    KJ *j = kj_new(KJ_OBJ);
    j->items = (KJ **)K_ALLOC(sizeof(KJ *) * (cap + 1));
    j->keys = (KStr **)K_ALLOC(sizeof(KStr *) * (cap + 1));
    return j;
}
static KJ *kj_enc(KS v, KD *d, KJErr *er) {
    KJ *j;
    if (er->failed) return kj_new(KJ_NULL);
    switch (d->k) {
    case KD_INT: return kj_num_i((int64_t)v);
    case KD_FLOAT: { double f = k_s2d(v); if (f != f || isinf(f)) return kj_new(KJ_NULL); j = kj_new(KJ_NUM); j->num = f; return j; }
    case KD_BOOL: j = kj_new(KJ_BOOL); j->num = v ? 1 : 0; return j;
    case KD_UNIT: return kj_new(KJ_NULL);
    case KD_STR: j = kj_new(KJ_STR); j->s = K_S2P(KStr *, v); return j;
    case KD_LIST: {
        KList *l = K_S2P(KList *, v);
        j = kj_new(KJ_ARR); j->n = l->len;
        j->items = (KJ **)K_ALLOC(sizeof(KJ *) * (l->len + 1));
        for (int64_t i = 0; i < l->len; i++) j->items[i] = kj_enc(k_list_data(l)[i], d->e, er);
        return j;
    }
    case KD_MAP: {
        KMap *m = K_S2P(KMap *, v);
        KList *ks = k_map_keys(m), *vs = k_map_values(m);
        if (d->kk->k == KD_STR) {
            j = kj_obj(ks->len); j->n = ks->len;
            for (int64_t i = 0; i < ks->len; i++) {
                j->keys[i] = K_S2P(KStr *, k_list_data(ks)[i]);
                j->items[i] = kj_enc(k_list_data(vs)[i], d->v, er);
            }
            return j;
        }
        j = kj_new(KJ_ARR); j->n = ks->len;
        j->items = (KJ **)K_ALLOC(sizeof(KJ *) * (ks->len + 1));
        for (int64_t i = 0; i < ks->len; i++) {
            KJ *p = kj_new(KJ_ARR); p->n = 2;
            p->items = (KJ **)K_ALLOC(sizeof(KJ *) * 2);
            p->items[0] = kj_enc(k_list_data(ks)[i], d->kk, er);
            p->items[1] = kj_enc(k_list_data(vs)[i], d->v, er);
            j->items[i] = p;
        }
        return j;
    }
    case KD_TUPLE: {
        KS *s = K_S2P(KS *, v);
        j = kj_new(KJ_ARR); j->n = d->nf;
        j->items = (KJ **)K_ALLOC(sizeof(KJ *) * (d->nf + 1));
        for (int i = 0; i < d->nf; i++) j->items[i] = kj_enc(s[i], d->ftypes[i], er);
        return j;
    }
    case KD_STRUCT: {
        KS *s = K_S2P(KS *, v);
        j = kj_obj(d->nf); j->n = d->nf;
        for (int i = 0; i < d->nf; i++) { j->keys[i] = k_cstr(d->fnames[i]); j->items[i] = kj_enc(s[i], d->ftypes[i], er); }
        return j;
    }
    case KD_ENUM: {
        KEnum *e = K_S2P(KEnum *, v);
#ifdef KT_JNull
        if (!strcmp(d->name, "Json")) return kj_from_json(e);
#endif
        if (!strcmp(d->name, "Option")) return e->tag == K_TAG_NONE ? kj_new(KJ_NULL) : kj_enc(e->f[0], d->vs[0].types[0], er);
        if (!strcmp(d->name, "Result")) {
            int ok = e->tag == K_TAG_OK;
            j = kj_obj(1); j->n = 1;
            j->keys[0] = k_cstr(ok ? "ok" : "err");
            j->items[0] = kj_enc(e->f[0], d->vs[ok ? 0 : 1].types[0], er);
            return j;
        }
        KDV *var = NULL;
        for (int i = 0; i < d->nv; i++) if (d->vs[i].tag == e->tag) var = &d->vs[i];
        if (!var->nf) { j = kj_new(KJ_STR); j->s = k_cstr(var->name); return j; }
        j = kj_obj(var->nf + 1); j->n = var->nf + 1;
        j->keys[0] = k_cstr("type");
        j->items[0] = kj_new(KJ_STR); j->items[0]->s = k_cstr(var->name);
        for (int i = 0; i < var->nf; i++) { j->keys[i + 1] = k_cstr(var->labels[i]); j->items[i + 1] = kj_enc(e->f[i], var->types[i], er); }
        return j;
    }
    default:
        er->failed = 1;
        er->msg = kx_fmt("a %s value cannot be turned into JSON", d->name ? d->name : "function");
        return kj_new(KJ_NULL);
    }
}
static const char *kj_kind(KJ *j, char *buf, size_t n) {
    switch (j->t) {
    case KJ_NULL: return "null";
    case KJ_BOOL: return "a bool";
    case KJ_STR: return "a string";
    case KJ_ARR: return "an array";
    case KJ_OBJ: return "an object";
    default: {
        if (j->isint && j->raw) {
            char t[512]; int m = j->rawn < 511 ? j->rawn : 511; memcpy(t, j->raw, m); t[m] = 0;
            errno = 0;
            long long x = strtoll(t, NULL, 10);
            if (errno == 0) { snprintf(buf, n, "%lld", x); return buf; }
            snprintf(buf, n, "%s", t + (t[0] == '-' && t[1] == '0' && t[2] == 0));
            return buf;
        }
        KSB b; ksb_init(&b); kx_js_num(&b, j->num);
        int m = b.len < (int64_t)n - 1 ? (int)b.len : (int)n - 1;
        memcpy(buf, b.p, m); buf[m] = 0;
        return buf;
    }
    }
}
static KS kj_dec(KJ *j, KD *d, KSB *path, KJErr *er);
#define KJ_BAD(want) do { char kb[64]; er->failed = 1; er->msg = kx_fmt("expected %s at %.*s, found %s", want, (int)path->len, path->p, kj_kind(j, kb, sizeof kb)); return 0; } while (0)
static KS kj_dec_field(KJ *obj, const char *label, KD *ft, KSB *path, KJErr *er, const char *owner) {
    if (er->failed) return 0;
    for (int64_t i = obj->n - 1; i >= 0; i--) {
        if (!strcmp(obj->keys[i]->data, label) && (int64_t)strlen(label) == obj->keys[i]->len) {
            int64_t save = path->len;
            ksb_c(path, "."); ksb_c(path, label);
            KS r = kj_dec(obj->items[i], ft, path, er);
            path->len = save;
            return r;
        }
    }
    if (ft->k == KD_ENUM && !strcmp(ft->name, "Option")) return k_p2s(K_NONE);
    er->failed = 1;
    er->msg = kx_fmt("missing field `%s` at %.*s (%s)", label, (int)path->len, path->p, owner);
    return 0;
}
static KJ *kj_obj_get(KJ *obj, const char *key) {
    for (int64_t i = obj->n - 1; i >= 0; i--) if (!strcmp(obj->keys[i]->data, key)) return obj->items[i];
    return NULL;
}
static KS kj_dec(KJ *j, KD *d, KSB *path, KJErr *er) {
    if (er->failed) return 0;
    switch (d->k) {
    case KD_INT: {
        if (j->t != KJ_NUM) KJ_BAD("a whole number");
        if (j->isint && j->raw) {
            char t[512]; int m = j->rawn < 511 ? j->rawn : 511; memcpy(t, j->raw, m); t[m] = 0;
            errno = 0;
            long long x = strtoll(t, NULL, 10);
            if (errno == ERANGE) { er->failed = 1; er->msg = kx_fmt("number at %.*s does not fit in a 64-bit int", (int)path->len, path->p); return 0; }
            return (KS)(int64_t)x;
        }
        if (j->num != floor(j->num) || isinf(j->num)) KJ_BAD("a whole number");
        if (j->num < -9223372036854775808.0 || j->num >= 9223372036854775808.0) {
            er->failed = 1; er->msg = kx_fmt("number at %.*s does not fit in a 64-bit int", (int)path->len, path->p); return 0;
        }
        return (KS)(int64_t)j->num;
    }
    case KD_FLOAT: if (j->t != KJ_NUM) KJ_BAD("a number"); return k_d2s(j->num);
    case KD_STR: if (j->t != KJ_STR) KJ_BAD("a string"); return k_p2s(j->s);
    case KD_BOOL: if (j->t != KJ_BOOL) KJ_BAD("true or false"); return (KS)(j->num != 0);
    case KD_UNIT: if (j->t != KJ_NULL) KJ_BAD("null"); return 0;
    case KD_LIST: {
        if (j->t != KJ_ARR) KJ_BAD("an array");
        KList *l = k_list_with_cap(0, j->n + 1);
        for (int64_t i = 0; i < j->n; i++) {
            int64_t save = path->len; char t[32]; snprintf(t, sizeof t, "[%lld]", (long long)i); ksb_c(path, t);
            l = k_list_push(l, kj_dec(j->items[i], d->e, path, er));
            path->len = save;
        }
        return k_p2s(l);
    }
    case KD_MAP: {
        KMap *m = k_map_empty(d->kk->k == KD_STR ? 1 : 0);
        if (d->kk->k == KD_STR) {
            if (j->t != KJ_OBJ) KJ_BAD("an object");
            for (int64_t i = 0; i < j->n; i++) {
                int64_t save = path->len; ksb_c(path, "."); ksb_str(path, j->keys[i]);
                m = k_map_put(m, k_p2s(j->keys[i]), kj_dec(j->items[i], d->v, path, er));
                path->len = save;
            }
            return k_p2s(m);
        }
        if (j->t != KJ_ARR) KJ_BAD("an array of [key, value] pairs");
        for (int64_t i = 0; i < j->n; i++) {
            KJ *p = j->items[i];
            int64_t save = path->len; char t[48];
            if (p->t != KJ_ARR || p->n != 2) {
                char kb[64];
                snprintf(t, sizeof t, "[%lld]", (long long)i); ksb_c(path, t);
                er->failed = 1; er->msg = kx_fmt("expected a [key, value] pair at %.*s, found %s", (int)path->len, path->p, kj_kind(p, kb, sizeof kb));
                return 0;
            }
            snprintf(t, sizeof t, "[%lld][0]", (long long)i); ksb_c(path, t);
            KS k = kj_dec(p->items[0], d->kk, path, er);
            path->len = save;
            snprintf(t, sizeof t, "[%lld][1]", (long long)i); ksb_c(path, t);
            KS v = kj_dec(p->items[1], d->v, path, er);
            path->len = save;
            if (er->failed) return 0;
            m = k_map_put(m, k, v);
        }
        return k_p2s(m);
    }
    case KD_TUPLE: {
        if (j->t != KJ_ARR || j->n != d->nf) { char w[64]; snprintf(w, sizeof w, "an array of %d values", d->nf); KJ_BAD(w); }
        KS *s = (KS *)K_ALLOC(sizeof(KS) * d->nf);
        for (int i = 0; i < d->nf; i++) {
            int64_t save = path->len; char t[32]; snprintf(t, sizeof t, "[%d]", i); ksb_c(path, t);
            s[i] = kj_dec(j->items[i], d->ftypes[i], path, er);
            path->len = save;
        }
        return k_p2s(s);
    }
    case KD_STRUCT: {
        if (j->t != KJ_OBJ) { char w[300]; snprintf(w, sizeof w, "an object (%s)", d->name); KJ_BAD(w); }
        KS *s = (KS *)K_ALLOC(sizeof(KS) * (d->nf ? d->nf : 1));
        for (int i = 0; i < d->nf; i++) s[i] = kj_dec_field(j, d->fnames[i], d->ftypes[i], path, er, d->name);
        return k_p2s(s);
    }
    case KD_ENUM: {
#ifdef KT_JNull
        if (!strcmp(d->name, "Json")) return k_p2s(kj_to_json(j));
#endif
        if (!strcmp(d->name, "Option")) return k_p2s(j->t == KJ_NULL ? K_NONE : k_some(kj_dec(j, d->vs[0].types[0], path, er)));
        if (!strcmp(d->name, "Result")) {
            KJ *okv = j->t == KJ_OBJ && j->n == 1 ? kj_obj_get(j, "ok") : NULL;
            KJ *errv = j->t == KJ_OBJ && j->n == 1 ? kj_obj_get(j, "err") : NULL;
            int64_t save = path->len;
            if (okv) { ksb_c(path, ".ok"); KS r = kj_dec(okv, d->vs[0].types[0], path, er); path->len = save; return k_p2s(k_ok(r)); }
            if (errv) { ksb_c(path, ".err"); KS r = kj_dec(errv, d->vs[1].types[0], path, er); path->len = save; return k_p2s(k_err(r)); }
            KJ_BAD("{\"ok\": ...} or {\"err\": ...}");
        }
        KStr *tag = NULL; KJ *obj = NULL;
        if (j->t == KJ_STR) tag = j->s;
        else if (j->t == KJ_OBJ) { KJ *t = kj_obj_get(j, "type"); if (t && t->t == KJ_STR) { tag = t->s; obj = j; } }
        if (!tag) { char w[300]; snprintf(w, sizeof w, "a %s variant name or an object with a \"type\" key", d->name); KJ_BAD(w); }
        KDV *var = NULL;
        for (int i = 0; i < d->nv; i++) if (!strcmp(d->vs[i].name, tag->data)) var = &d->vs[i];
        if (!var) { er->failed = 1; er->msg = kx_fmt("`%s` at %.*s is not a variant of %s", tag->data, (int)path->len, path->p, d->name); return 0; }
        if (!var->nf) return k_p2s(k_enum_new(var->tag, 0));
        if (!obj) { er->failed = 1; er->msg = kx_fmt("variant %s at %.*s has fields, so it must be an object with a \"type\" key", var->name, (int)path->len, path->p); return 0; }
        KEnum *e = (KEnum *)K_ALLOC(sizeof(KEnum) + sizeof(KS) * var->nf);
        e->tag = var->tag;
        for (int i = 0; i < var->nf; i++) e->f[i] = kj_dec_field(obj, var->labels[i], var->types[i], path, er, var->name);
        return k_p2s(e);
    }
    default:
        er->failed = 1; er->msg = kx_fmt("values of type %s cannot be read from JSON", d->name ? d->name : "function");
        return 0;
    }
}
static void kx_json_fail(const char *op, KJErr *er) { k_fail("E407", kx_line, kx_col, "json_%s: %s", op, er->msg->data); }
static KStr *kx_json_encode_d(KS v, KD *d, const char *ind, const char *op) {
    KJErr er = {0, NULL};
    KJ *j = kj_enc(v, d, &er);
    if (er.failed) kx_json_fail(op, &er);
    KSB b; ksb_init(&b); kj_write(&b, j, ind, 0);
    return ksb_done(&b);
}
static KStr *kx_json_encode(KS v, KD *d) { return kx_json_encode_d(v, d, "", "encode"); }
static KStr *kx_json_encode_pretty(KS v, KD *d) { return kx_json_encode_d(v, d, "  ", "encode_pretty"); }
static KEnum *kx_json_decode_j(KJ *j, KD *d) {
    KJErr er = {0, NULL};
    KSB path; ksb_init(&path); ksb_c(&path, "$");
    KS r = kj_dec(j, d, &path, &er);
    if (er.failed) return k_err(k_p2s(er.msg));
    return k_ok(r);
}
static KEnum *kx_json_decode(KStr *text, KD *d) {
    KStr *err = NULL;
    KJ *j = kj_parse(text, &err);
    if (!j) return k_err(k_p2s(kx_fmt("invalid JSON: %s", err->data)));
    return kx_json_decode_j(j, d);
}
#ifdef KT_JNull
static KEnum *kx_json_of(KS v, KD *d) {
    KJErr er = {0, NULL};
    KJ *j = kj_enc(v, d, &er);
    if (er.failed) kx_json_fail("of", &er);
    return kj_to_json(j);
}
static KEnum *kx_json_into(KEnum *v, KD *d) { return kx_json_decode_j(kj_from_json(v), d); }
#endif

/* ------------------------------------------------------------------ std.regex (PCRE2, Python syntax) */
#ifdef K_STD_REGEX
#define PCRE2_CODE_UNIT_WIDTH 8
#include <pcre2.h>
static pcre2_code *kx_re(KStr *pat) {
    static KStr *last_pat; static pcre2_code *last;
    if (last && k_str_eq(last_pat, pat)) return last;
    int ec; PCRE2_SIZE off;
    pcre2_code *re = pcre2_compile((PCRE2_SPTR)pat->data, pat->len, PCRE2_UTF | PCRE2_UCP, &ec, &off, NULL);
    if (!re) {
        PCRE2_UCHAR msg[256]; pcre2_get_error_message(ec, msg, sizeof msg);
        k_fail("E407", kx_line, kx_col, "error: %s at position %d", (char *)msg, (int)off);
    }
    pcre2_jit_compile(re, PCRE2_JIT_COMPLETE);   /* machine code for the pattern where PCRE2 supports it; ignored otherwise */
    last = re; last_pat = pat;
    return re;
}
/* Calls f(match data) for every non-overlapping match, Python-style (empty matches included). */
typedef struct { KStr *s; pcre2_code *re; pcre2_match_data *md; PCRE2_SIZE start; int done; int last_empty_at; uint32_t utf_checked; } KReIt;
static void kx_re_it(KReIt *it, KStr *pat, KStr *s) {
    it->s = s; it->re = kx_re(pat); it->md = pcre2_match_data_create_from_pattern(it->re, NULL);
    it->start = 0; it->done = 0; it->last_empty_at = -1; it->utf_checked = 0;
}
static int kx_re_next(KReIt *it, PCRE2_SIZE **ov) {
    while (!it->done && it->start <= (PCRE2_SIZE)it->s->len) {
        uint32_t opts = 0;
        if ((int64_t)it->start == it->last_empty_at) opts = PCRE2_NOTEMPTY_ATSTART | PCRE2_ANCHORED;
        /* PCRE2 checks the whole subject is valid UTF-8 on every call unless told not to, which made finding
         * many matches O(length * matches). Check once per scan, on the first call. */
        int rc = pcre2_match(it->re, (PCRE2_SPTR)it->s->data, it->s->len, it->start, opts | it->utf_checked, it->md, NULL);
        it->utf_checked = PCRE2_NO_UTF_CHECK;
        if (rc < 0) {
            if (opts) {   /* no non-empty match here: move on one character */
                it->last_empty_at = -1;
                it->start++;
                while (it->start < (PCRE2_SIZE)it->s->len && (it->s->data[it->start] & 0xC0) == 0x80) it->start++;
                continue;
            }
            it->done = 1;
            return 0;
        }
        PCRE2_SIZE *o = pcre2_get_ovector_pointer(it->md);
        if (o[0] == o[1]) it->last_empty_at = (int)o[1];
        it->start = o[1];
        *ov = o;
        return rc;
    }
    return 0;
}
static int64_t kx_regex_matches(KStr *pat, KStr *s) {
    pcre2_code *re = kx_re(pat);
    pcre2_match_data *md = pcre2_match_data_create_from_pattern(re, NULL);
    int rc = pcre2_match(re, (PCRE2_SPTR)s->data, s->len, 0, 0, md, NULL);
    pcre2_match_data_free(md);
    return rc >= 0;
}
static KList *kx_regex_find_all(KStr *pat, KStr *s) {
    KReIt it; kx_re_it(&it, pat, s);
    KList *r = kx_list_empty(); PCRE2_SIZE *o;
    while (kx_re_next(&it, &o)) r = k_list_push(r, k_p2s(k_str_new(s->data + o[0], o[1] - o[0])));
    pcre2_match_data_free(it.md);
    return r;
}
static KStr *kx_group(KStr *s, PCRE2_SIZE *o, int g, int rc) {
    if (g >= rc || o[2 * g] == PCRE2_UNSET) return k_cstr("");
    return k_str_new(s->data + o[2 * g], o[2 * g + 1] - o[2 * g]);
}
static KEnum *kx_regex_groups(KStr *pat, KStr *s) {
    pcre2_code *re = kx_re(pat);
    pcre2_match_data *md = pcre2_match_data_create_from_pattern(re, NULL);
    int rc = pcre2_match(re, (PCRE2_SPTR)s->data, s->len, 0, 0, md, NULL);
    if (rc < 0) { pcre2_match_data_free(md); return K_NONE; }
    uint32_t ng; pcre2_pattern_info(re, PCRE2_INFO_CAPTURECOUNT, &ng);
    PCRE2_SIZE *o = pcre2_get_ovector_pointer(md);
    KList *r = kx_list_empty();
    for (uint32_t g = 1; g <= ng; g++) r = k_list_push(r, k_p2s(kx_group(s, o, (int)g, rc)));
    pcre2_match_data_free(md);
    return k_some(k_p2s(r));
}
static KStr *kx_regex_replace(KStr *pat, KStr *s, KStr *rep) {
    KReIt it; kx_re_it(&it, pat, s);
    KSB b; ksb_init(&b); PCRE2_SIZE *o; PCRE2_SIZE last = 0; int rc;
    while ((rc = kx_re_next(&it, &o))) {
        ksb_add(&b, s->data + last, o[0] - last);
        for (int64_t i = 0; i < rep->len; i++) {
            char c = rep->data[i];
            if (c != '\\' || i + 1 >= rep->len) { ksb_add(&b, &c, 1); continue; }
            char d = rep->data[++i];
            if (d >= '0' && d <= '9') {
                int g = d - '0';
                if (i + 1 < rep->len && rep->data[i + 1] >= '0' && rep->data[i + 1] <= '9') { g = g * 10 + (rep->data[++i] - '0'); }
                ksb_str(&b, kx_group(s, o, g, rc));
            } else if (d == 'g' && i + 1 < rep->len && rep->data[i + 1] == '<') {
                int64_t j = i + 2; while (j < rep->len && rep->data[j] != '>') j++;
                char name[128]; int n = (int)(j - i - 2) < 127 ? (int)(j - i - 2) : 127;
                memcpy(name, rep->data + i + 2, n); name[n] = 0;
                int g = (name[0] >= '0' && name[0] <= '9') ? atoi(name) : pcre2_substring_number_from_name(it.re, (PCRE2_SPTR)name);
                if (g >= 0) ksb_str(&b, kx_group(s, o, g, rc));
                i = j;
            } else if (d == 'n') ksb_c(&b, "\n");
            else if (d == 't') ksb_c(&b, "\t");
            else if (d == 'r') ksb_c(&b, "\r");
            else if (d == '\\') ksb_c(&b, "\\");
            else { ksb_add(&b, &c, 1); ksb_add(&b, &d, 1); }
        }
        last = o[1];
    }
    ksb_add(&b, s->data + last, s->len - last);
    pcre2_match_data_free(it.md);
    return ksb_done(&b);
}
static KList *kx_regex_split(KStr *pat, KStr *s) {
    KReIt it; kx_re_it(&it, pat, s);
    uint32_t ng; pcre2_pattern_info(it.re, PCRE2_INFO_CAPTURECOUNT, &ng);
    KList *r = kx_list_empty(); PCRE2_SIZE *o; PCRE2_SIZE last = 0; int rc;
    while ((rc = kx_re_next(&it, &o))) {
        r = k_list_push(r, k_p2s(k_str_new(s->data + last, o[0] - last)));
        for (uint32_t g = 1; g <= ng; g++) r = k_list_push(r, k_p2s(kx_group(s, o, (int)g, rc)));
        last = o[1];
    }
    r = k_list_push(r, k_p2s(k_str_new(s->data + last, s->len - last)));
    pcre2_match_data_free(it.md);
    return r;
}
#endif

/* ------------------------------------------------------------------ std.sqlite */
#ifdef K_STD_SQLITE
#include <sqlite3.h>
static KEnum *kx_sql_err(sqlite3 *db, int rc) {
    const char *kind = (rc & 0xff) == SQLITE_CONSTRAINT ? "IntegrityError" : "OperationalError";
    return k_err(k_p2s(kx_fmt("%s: %s", kind, db ? sqlite3_errmsg(db) : sqlite3_errstr(rc))));
}
static KEnum *kx_sqlite_open_db(KStr *path) {
    sqlite3 *db = NULL;
    int rc = sqlite3_open(path->data, &db);
    if (rc != SQLITE_OK) { KEnum *e = kx_sql_err(NULL, rc); e = k_err(k_p2s(k_cstr("OperationalError: unable to open database file"))); sqlite3_close(db); return e; }
    /* production defaults, the same in every backend: write-ahead logging (readers never wait for a writer, one
       sync per commit) and waiting up to 5 s for a lock held by another process instead of failing at once */
    sqlite3_busy_timeout(db, 5000);
    sqlite3_exec(db, "pragma journal_mode = wal", NULL, NULL, NULL);
    return k_ok(k_p2s(db));
}
/* Prepared statements are kept and reused (per database and SQL text), as Python's sqlite3 does: parsing the SQL
 * again for every query costs more than running most of them. */
#define KX_STMTS 64
typedef struct { sqlite3 *db; char *sql; int64_t len; sqlite3_stmt *st; uint64_t used; } KXStmt;
static KXStmt kx_stmts[KX_STMTS]; static uint64_t kx_stmt_clock;
static sqlite3_stmt *kx_stmt_take(sqlite3 *db, KStr *sql) {
    for (int i = 0; i < KX_STMTS; i++)
        if (kx_stmts[i].db == db && kx_stmts[i].len == sql->len && !memcmp(kx_stmts[i].sql, sql->data, sql->len)) {
            sqlite3_stmt *st = kx_stmts[i].st;
            free(kx_stmts[i].sql); memset(&kx_stmts[i], 0, sizeof kx_stmts[i]);
            return st;
        }
    return NULL;
}
static void kx_stmt_give(sqlite3 *db, KStr *sql, sqlite3_stmt *st) {
    sqlite3_reset(st); sqlite3_clear_bindings(st);
    int slot = 0;
    for (int i = 0; i < KX_STMTS; i++) {
        if (!kx_stmts[i].db) { slot = i; break; }
        if (kx_stmts[i].used < kx_stmts[slot].used) slot = i;
    }
    if (kx_stmts[slot].db) { sqlite3_finalize(kx_stmts[slot].st); free(kx_stmts[slot].sql); }
    char *copy = (char *)malloc(sql->len + 1);
    if (!copy) { sqlite3_finalize(st); memset(&kx_stmts[slot], 0, sizeof kx_stmts[slot]); return; }
    memcpy(copy, sql->data, sql->len);
    kx_stmts[slot] = (KXStmt){db, copy, sql->len, st, ++kx_stmt_clock};
}
static KEnum *kx_sql_prepare(sqlite3 *db, KStr *sql, KList *params, sqlite3_stmt **st) {
    const char *tail = NULL;
    if (!(*st = kx_stmt_take(db, sql))) {
        int rc = sqlite3_prepare_v2(db, sql->data, (int)sql->len, st, &tail);
        if (rc != SQLITE_OK) return kx_sql_err(db, rc);
        if (tail) { while (*tail == ' ' || *tail == '\n' || *tail == '\t' || *tail == '\r' || *tail == ';') tail++; }
        if (tail && *tail) { sqlite3_finalize(*st); return k_err(k_p2s(k_cstr("ProgrammingError: You can only execute one statement at a time."))); }
    }
    int want = sqlite3_bind_parameter_count(*st);
    if (want != params->len) {
        kx_stmt_give(db, sql, *st);
        return k_err(k_p2s(kx_fmt("ProgrammingError: Incorrect number of bindings supplied. The current statement uses %d, and there are %lld supplied.", want, (long long)params->len)));
    }
    for (int64_t i = 0; i < params->len; i++) {
        KEnum *v = K_S2P(KEnum *, k_list_data(params)[i]);
        int c = (int)i + 1;
        if (v->tag == KT_SqlInt) sqlite3_bind_int64(*st, c, (int64_t)v->f[0]);
        else if (v->tag == KT_SqlFloat) sqlite3_bind_double(*st, c, k_s2d(v->f[0]));
        else if (v->tag == KT_SqlText) { KStr *s = K_S2P(KStr *, v->f[0]); sqlite3_bind_text(*st, c, s->data, (int)s->len, SQLITE_TRANSIENT); }
        else sqlite3_bind_null(*st, c);
    }
    return NULL;
}
static KEnum *kx_sqlite_execute(void *dbp, KStr *sql, KList *params) {
    sqlite3 *db = (sqlite3 *)dbp; sqlite3_stmt *st;
    KEnum *e = kx_sql_prepare(db, sql, params, &st);
    if (e) return e;
    int rc;
    while ((rc = sqlite3_step(st)) == SQLITE_ROW) {}
    int ro = sqlite3_stmt_readonly(st);
    if (rc != SQLITE_DONE) { KEnum *err = kx_sql_err(db, rc); kx_stmt_give(db, sql, st); return err; }
    kx_stmt_give(db, sql, st);
    return k_ok((KS)(int64_t)(ro ? 0 : sqlite3_changes(db)));
}
static KEnum *kx_sqlite_query(void *dbp, KStr *sql, KList *params) {
    sqlite3 *db = (sqlite3 *)dbp; sqlite3_stmt *st;
    KEnum *e = kx_sql_prepare(db, sql, params, &st);
    if (e) return e;
    KList *rows = kx_list_empty();
    int rc, nc = sqlite3_column_count(st);
    while ((rc = sqlite3_step(st)) == SQLITE_ROW) {
        KMap *m = k_map_empty(1);
        for (int c = 0; c < nc; c++) {
            KEnum *v;
            switch (sqlite3_column_type(st, c)) {
            case SQLITE_INTEGER: v = k_enum_new(KT_SqlInt, 1, (KS)sqlite3_column_int64(st, c)); break;
            case SQLITE_FLOAT: v = k_enum_new(KT_SqlFloat, 1, k_d2s(sqlite3_column_double(st, c))); break;
            case SQLITE_NULL: v = k_enum_new(KT_SqlNull, 0); break;
            default: v = k_enum_new(KT_SqlText, 1, k_p2s(k_str_new((const char *)sqlite3_column_text(st, c), sqlite3_column_bytes(st, c))));
            }
            m = k_map_put(m, k_p2s(k_cstr(sqlite3_column_name(st, c))), k_p2s(v));
        }
        rows = k_list_push(rows, k_p2s(m));
    }
    if (rc != SQLITE_DONE) { KEnum *err = kx_sql_err(db, rc); kx_stmt_give(db, sql, st); return err; }
    kx_stmt_give(db, sql, st);
    return k_ok(k_p2s(rows));
}
static int64_t kx_sqlite_last_insert_id(void *db) { return sqlite3_last_insert_rowid((sqlite3 *)db); }
static int64_t kx_sqlite_close(void *db) {
    for (int i = 0; i < KX_STMTS; i++)
        if (kx_stmts[i].db == db) { sqlite3_finalize(kx_stmts[i].st); free(kx_stmts[i].sql); memset(&kx_stmts[i], 0, sizeof kx_stmts[i]); }
    sqlite3_close((sqlite3 *)db);
    return 0;
}

/* Typed rows: `sql(value)` and `sql_read(rows)` (same rules as klar/sqlconv.py). */
static int kx_sql_plain(KD *d) {
    if (d->k != KD_ENUM || !strcmp(d->name, "Option") || !strcmp(d->name, "Result") || !strcmp(d->name, "Json")
        || !strcmp(d->name, "SqlValue")) return 0;
    for (int i = 0; i < d->nv; i++) if (d->vs[i].nf) return 0;
    return 1;
}
static KEnum *kx_sql_value(KS v, KD *d) {
    switch (d->k) {
    case KD_INT: return k_enum_new(KT_SqlInt, 1, v);
    case KD_FLOAT: return k_enum_new(KT_SqlFloat, 1, v);
    case KD_STR: return k_enum_new(KT_SqlText, 1, v);
    case KD_BOOL: return k_enum_new(KT_SqlInt, 1, (KS)(int64_t)(v ? 1 : 0));
    case KD_UNIT: return k_enum_new(KT_SqlNull, 0);
    default: break;
    }
    if (d->k == KD_ENUM && !strcmp(d->name, "Option")) {
        KEnum *o = K_S2P(KEnum *, v);
        return o->tag == K_TAG_NONE ? k_enum_new(KT_SqlNull, 0) : kx_sql_value(o->f[0], d->vs[0].types[0]);
    }
    if (d->k == KD_ENUM && !strcmp(d->name, "SqlValue")) return K_S2P(KEnum *, v);
    if (kx_sql_plain(d)) {
        KEnum *e = K_S2P(KEnum *, v);
        for (int i = 0; i < d->nv; i++) if (d->vs[i].tag == e->tag) return k_enum_new(KT_SqlText, 1, k_p2s(k_cstr(d->vs[i].name)));
    }
    return k_enum_new(KT_SqlText, 1, k_p2s(kx_json_encode_d(v, d, "", "encode")));
}
static const char *kx_sql_kind(KEnum *v) {
    return v->tag == KT_SqlInt ? "a whole number" : v->tag == KT_SqlFloat ? "a decimal number" : v->tag == KT_SqlText ? "text" : "NULL";
}
#define KX_SQL_FAIL(...) do { er->failed = 1; er->msg = kx_fmt(__VA_ARGS__); return 0; } while (0)
#define KX_SQL_BAD(want) KX_SQL_FAIL("column `%s` holds %s, but %s needs %s", col, kx_sql_kind(v), target, want)
static KS kx_sql_col(KEnum *v, KD *d, const char *col, const char *target, KJErr *er) {
    if (er->failed) return 0;
    KS x = v->tag == KT_SqlNull ? 0 : v->f[0];  /* NULL carries no value */
    if (d->k == KD_ENUM && !strcmp(d->name, "Option")) {
        if (v->tag == KT_SqlNull) return k_p2s(K_NONE);
        KS r = kx_sql_col(v, d->vs[0].types[0], col, target, er);
        return er->failed ? 0 : k_p2s(k_some(r));
    }
    int raw = d->k == KD_ENUM && !strcmp(d->name, "SqlValue");
    if (v->tag == KT_SqlNull && d->k != KD_UNIT && !raw)
        KX_SQL_FAIL("column `%s` is NULL, but %s is not an Option; make it an Option, or select a value instead of NULL", col, target);
    switch (d->k) {
    case KD_INT: if (v->tag != KT_SqlInt) KX_SQL_BAD("a whole number"); return x;
    case KD_FLOAT:
        if (v->tag == KT_SqlInt) return k_d2s((double)(int64_t)x);
        if (v->tag != KT_SqlFloat) KX_SQL_BAD("a number");
        return x;
    case KD_STR: if (v->tag != KT_SqlText) KX_SQL_BAD("text"); return x;
    case KD_BOOL:
        if (v->tag != KT_SqlInt) KX_SQL_FAIL("column `%s` holds %s, but %s needs 0 or 1", col, kx_sql_kind(v), target);
        if ((int64_t)x != 0 && (int64_t)x != 1) KX_SQL_FAIL("column `%s` holds %lld, but %s needs 0 or 1", col, (long long)(int64_t)x, target);
        return (KS)(int64_t)x;
    case KD_UNIT: if (v->tag != KT_SqlNull) KX_SQL_BAD("NULL"); return 0;
    default: break;
    }
    if (raw) return k_p2s(v);
    if (kx_sql_plain(d)) {
        if (v->tag != KT_SqlText) { char w[300]; snprintf(w, sizeof w, "the name of a %s variant", d->name); KX_SQL_BAD(w); }
        KStr *t = K_S2P(KStr *, x);
        for (int i = 0; i < d->nv; i++)
            if ((int64_t)strlen(d->vs[i].name) == t->len && !memcmp(d->vs[i].name, t->data, t->len)) return k_p2s(k_enum_new(d->vs[i].tag, 0));
        KX_SQL_FAIL("column `%s` holds \"%s\", which is not a variant of %s", col, t->data, d->name);
    }
    if (v->tag != KT_SqlText) KX_SQL_BAD("JSON text");
    KStr *perr = NULL;
    KJ *j = kj_parse(K_S2P(KStr *, x), &perr);
    if (!j) KX_SQL_FAIL("column `%s` is not valid JSON, but %s is read from JSON text", col, target);
    KJErr je = {0, NULL};
    KSB path; ksb_init(&path); ksb_c(&path, "$");
    KS r = kj_dec(j, d, &path, &je);
    if (je.failed) KX_SQL_FAIL("column `%s`: %s", col, je.msg->data);
    return r;
}
static KEnum *kx_sql_read(KList *rows, KD *d) {
    KJErr er = {0, NULL};
    KList *out = k_list_with_cap(0, rows->len + 1);
    for (int64_t r = 0; r < rows->len && !er.failed; r++) {
        KMap *m = k_map_compact(K_S2P(KMap *, k_list_data(rows)[r]));
        int64_t n = m->len;
        KS val = 0;
        if (d->k == KD_TUPLE) {
            if (n != d->nf) { er.failed = 1; er.msg = kx_fmt("the query returns %lld column(s) but the tuple has %d values", (long long)n, d->nf); break; }
            KS *s = (KS *)K_ALLOC(sizeof(KS) * d->nf);
            for (int i = 0; i < d->nf; i++) {
                char target[32]; snprintf(target, sizeof target, "tuple value .%d", i);
                s[i] = kx_sql_col(K_S2P(KEnum *, m->d->vals[i]), d->ftypes[i], K_S2P(KStr *, m->d->keys[i])->data, target, &er);
            }
            val = k_p2s(s);
        } else if (d->k == KD_STRUCT) {
            KS *s = (KS *)K_ALLOC(sizeof(KS) * (d->nf ? d->nf : 1));
            for (int i = 0; i < d->nf && !er.failed; i++) {
                const char *f = d->fnames[i];
                int64_t at = -1;
                for (int64_t c = 0; c < n; c++) if (!strcmp(K_S2P(KStr *, m->d->keys[c])->data, f)) at = c;
                if (at < 0) {
                    er.failed = 1;
                    er.msg = kx_fmt("the query has no column `%s` for field `%s.%s`; select it, or name a column with `as %s`", f, d->name, f, f);
                    break;
                }
                KStr *target = kx_fmt("field `%s.%s`", d->name, f);
                s[i] = kx_sql_col(K_S2P(KEnum *, m->d->vals[at]), d->ftypes[i], f, target->data, &er);
            }
            val = k_p2s(s);
        } else {
            if (n != 1) {
                er.failed = 1;
                er.msg = kx_fmt("the query returns %lld columns; select exactly one to read single values, or read the rows into a struct or a tuple", (long long)n);
                break;
            }
            val = kx_sql_col(K_S2P(KEnum *, m->d->vals[0]), d, K_S2P(KStr *, m->d->keys[0])->data, "the result", &er);
        }
        out = k_list_push(out, val);
    }
    if (er.failed) return k_err(k_p2s(er.msg));
    return k_ok(k_p2s(out));
}
#endif

/* ------------------------------------------------------------------ std.http (client and server) */
#ifdef K_STD_NET
#include <setjmp.h>
#include <pthread.h>
#include <sys/socket.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>
#ifdef K_STD_TLS
#include <openssl/ssl.h>
#include <openssl/err.h>
#endif
typedef struct { int fd; void *ssl; } KConn;
#ifdef K_STD_TLS
static SSL_CTX *kx_ssl_ctx;
#endif
static ssize_t kx_conn_read(KConn *c, char *buf, size_t n) {
#ifdef K_STD_TLS
    if (c->ssl) return SSL_read((SSL *)c->ssl, buf, (int)n);
#endif
    return read(c->fd, buf, n);
}
static int kx_conn_write(KConn *c, const char *p, int64_t n) {
    while (n > 0) {
        ssize_t w;
#ifdef K_STD_TLS
        if (c->ssl) w = SSL_write((SSL *)c->ssl, p, (int)n); else
#endif
        w = send(c->fd, p, n, MSG_NOSIGNAL);
        if (w <= 0) { if (w < 0 && errno == EINTR) continue; return -1; }
        p += w; n -= w;
    }
    return 0;
}
static void kx_conn_close(KConn *c) {
#ifdef K_STD_TLS
    if (c->ssl) { SSL_shutdown((SSL *)c->ssl); SSL_free((SSL *)c->ssl); }
#endif
    close(c->fd);
}
static void kx_split_hostport(const char *hp, char *h, size_t hn, char *p, size_t pn, const char *defport) {
    const char *c = strrchr(hp, ':');
    if (c && !strchr(c, ']')) { snprintf(h, hn, "%.*s", (int)(c - hp), hp); snprintf(p, pn, "%s", c + 1); }
    else { snprintf(h, hn, "%s", hp); snprintf(p, pn, "%s", defport); }
}
static KEnum *kx_tcp(const char *h, const char *port, int *out) {
    struct addrinfo hints, *res;
    memset(&hints, 0, sizeof hints); hints.ai_socktype = SOCK_STREAM;
    int gr = getaddrinfo(h, port, &hints, &res);
    if (gr != 0) return k_err(k_p2s(kx_fmt("URLError: <urlopen error [Errno -2] %s>", gai_strerror(gr))));
    int fd = -1;
    for (struct addrinfo *a = res; a; a = a->ai_next) {
        fd = socket(a->ai_family, a->ai_socktype, a->ai_protocol);
        if (fd < 0) continue;
        struct timeval tv = {30, 0};
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
        if (connect(fd, a->ai_addr, a->ai_addrlen) == 0) break;
        close(fd); fd = -1;
    }
    freeaddrinfo(res);
    if (fd < 0) return k_err(k_p2s(kx_fmt("URLError: <urlopen error [Errno %d] %s>", errno, strerror(errno))));
    *out = fd;
    return NULL;
}
static void kx_base64(const char *in, size_t n, char *out) {
    static const char *t = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    size_t o = 0;
    for (size_t i = 0; i < n; i += 3) {
        uint32_t v = (uint32_t)(unsigned char)in[i] << 16;
        if (i + 1 < n) v |= (uint32_t)(unsigned char)in[i + 1] << 8;
        if (i + 2 < n) v |= (unsigned char)in[i + 2];
        out[o++] = t[(v >> 18) & 63]; out[o++] = t[(v >> 12) & 63];
        out[o++] = i + 1 < n ? t[(v >> 6) & 63] : '=';
        out[o++] = i + 2 < n ? t[v & 63] : '=';
    }
    out[o] = 0;
}
/* the proxy to use for host (HTTP_PROXY / HTTPS_PROXY / NO_PROXY, like Python's urllib), as host:port */
static int kx_proxy_for(int tls, const char *host, char *proxy, size_t pn, char *auth, size_t an) {
    const char *v = getenv(tls ? "https_proxy" : "http_proxy");
    if (!v || !*v) v = getenv(tls ? "HTTPS_PROXY" : "HTTP_PROXY");
    auth[0] = 0;
    if (!v || !*v) return 0;
    const char *np = getenv("no_proxy"); if (!np || !*np) np = getenv("NO_PROXY");
    if (np) {
        char list[2048]; snprintf(list, sizeof list, "%s", np);
        size_t hl = strlen(host);
        for (char *save, *tok = strtok_r(list, ", ", &save); tok; tok = strtok_r(NULL, ", ", &save)) {
            if (!strcmp(tok, "*")) return 0;
            char *colon = strchr(tok, ':'); if (colon && !strchr(colon + 1, ':')) *colon = 0;
            if (*tok == '.') tok++;
            size_t tl = strlen(tok);
            if (tl && hl >= tl && !strcasecmp(host + hl - tl, tok) && (hl == tl || host[hl - tl - 1] == '.')) return 0;
        }
    }
    const char *p = strstr(v, "://"); p = p ? p + 3 : v;
    const char *at = strrchr(p, '@');
    if (at) {
        char cred[400], b64[600];
        snprintf(cred, sizeof cred, "%.*s", (int)(at - p), p);
        KStr *dec = kx_unquote(cred, (int64_t)strlen(cred), 0);
        kx_base64(dec->data, dec->len, b64);
        snprintf(auth, an, "Proxy-Authorization: Basic %s\r\n", b64);
        p = at + 1;
    }
    snprintf(proxy, pn, "%s", p);
    char *slash = strchr(proxy, '/'); if (slash) *slash = 0;
    return 1;
}
static KStr *kx_lower(const char *p, int64_t n) {
    KStr *s = k_str_new(p, n);
    for (int64_t i = 0; i < n; i++) if (s->data[i] >= 'A' && s->data[i] <= 'Z') s->data[i] += 32;
    return s;
}
static const char *kx_find(const char *h, int64_t hn, const char *n) { return k_memmem(h, hn, n, (int64_t)strlen(n)); }

/* the client: HTTP/1.1, Connection: close, follows redirects like Python's urllib */
static KEnum *kx_http_once(KStr *method, KStr *url, KList *hk, KList *hv, KStr *body, KStr **location, KS **resp) {
    const char *u = url->data;
    int tls = 0;
    if (!strncmp(u, "http://", 7)) u += 7;
    else if (!strncmp(u, "https://", 8)) { u += 8; tls = 1; }
    else return k_err(k_p2s(kx_fmt("ValueError: unknown url type: '%s'", url->data)));
#ifndef K_STD_TLS
    if (tls) return k_err(k_p2s(k_cstr("URLError: https needs OpenSSL (install libssl-dev) when compiled")));
#endif
    const char *slash = strchr(u, '/');
    const char *hostend = slash ? slash : u + strlen(u);
    const char *q = strchr(u, '?');
    if (q && q < hostend) hostend = q;
    char host[256], port[16];
    int hl = (int)(hostend - u);
    if (hl <= 0 || hl > 255) return k_err(k_p2s(k_cstr("URLError: no host given")));
    memcpy(host, u, hl); host[hl] = 0;
    char *colon = strrchr(host, ':');
    if (colon && !strchr(colon, ']')) { snprintf(port, sizeof port, "%s", colon + 1); *colon = 0; }
    else snprintf(port, sizeof port, "%s", tls ? "443" : "80");
    char *h = host;
    if (h[0] == '[') { h++; char *e = strchr(h, ']'); if (e) *e = 0; }
    const char *path = (*hostend == '?') ? NULL : hostend;
    char proxy[512], pauth[512];
    int via_proxy = kx_proxy_for(tls, h, proxy, sizeof proxy, pauth, sizeof pauth);
    int fd;
    KEnum *ce;
    if (via_proxy) {
        char ph[256], pp[16];
        kx_split_hostport(proxy, ph, sizeof ph, pp, sizeof pp, "80");
        if ((ce = kx_tcp(ph, pp, &fd))) return ce;
        if (tls) {
            KSB cr; ksb_init(&cr);
            char t[700];
            snprintf(t, sizeof t, "CONNECT %s:%s HTTP/1.1\r\nHost: %s:%s\r\n%s\r\n", h, port, h, port, pauth);
            ksb_c(&cr, t);
            KConn pc = {fd, NULL};
            if (kx_conn_write(&pc, cr.p, cr.len) < 0) { close(fd); return k_err(k_p2s(k_cstr("URLError: proxy connection failed"))); }
            char rb[4096]; int rn = 0;
            while (rn < (int)sizeof rb - 1) {
                ssize_t n = read(fd, rb + rn, 1);
                if (n <= 0) break;
                rn += (int)n;
                if (rn >= 4 && !memcmp(rb + rn - 4, "\r\n\r\n", 4)) break;
            }
            rb[rn] = 0;
            if (rn < 12 || strncmp(rb + 9, "200", 3)) {
                close(fd);
                char *e = strstr(rb, "\r\n"); if (e) *e = 0;
                return k_err(k_p2s(kx_fmt("URLError: <urlopen error Tunnel connection failed: %s>", rn > 9 ? rb + 9 : "no reply")));
            }
        }
    } else if ((ce = kx_tcp(h, port, &fd))) return ce;
    KConn c = {fd, NULL};
#ifdef K_STD_TLS
    if (tls) {
        SSL *ssl = SSL_new(kx_ssl_ctx);
        SSL_set_fd(ssl, fd);
        SSL_set_tlsext_host_name(ssl, h);
        SSL_set1_host(ssl, h);
        if (SSL_connect(ssl) != 1) {
            char eb[256]; ERR_error_string_n(ERR_get_error(), eb, sizeof eb);
            SSL_free(ssl); close(fd);
            return k_err(k_p2s(kx_fmt("URLError: <urlopen error %s>", eb)));
        }
        c.ssl = ssl;
    }
#endif
    KSB rq; ksb_init(&rq);
    ksb_str(&rq, method); ksb_c(&rq, " ");
    if (via_proxy && !tls) { ksb_c(&rq, "http://"); ksb_c(&rq, host); if (colon) { ksb_c(&rq, ":"); ksb_c(&rq, port); } }
    if (!path || !*path) ksb_c(&rq, "/");
    if (path) ksb_c(&rq, path); else ksb_c(&rq, hostend);
    ksb_c(&rq, " HTTP/1.1\r\nHost: "); ksb_c(&rq, host);
    if (colon) { ksb_c(&rq, ":"); ksb_c(&rq, port); }
    ksb_c(&rq, "\r\nConnection: close\r\nAccept-Encoding: identity\r\n");
    if (via_proxy && !tls) ksb_c(&rq, pauth);
    int has_ua = 0, has_ct = 0;
    for (int64_t i = 0; i < hk->len; i++) {
        KStr *k = kx_list_str(hk, i), *v = kx_list_str(hv, i);
        KStr *lk = kx_lower(k->data, k->len);
        if (!strcmp(lk->data, "user-agent")) has_ua = 1;
        if (!strcmp(lk->data, "content-type")) has_ct = 1;
        ksb_str(&rq, k); ksb_c(&rq, ": "); ksb_str(&rq, v); ksb_c(&rq, "\r\n");
    }
    if (!has_ua) ksb_c(&rq, "User-Agent: klar\r\n");
    if (body->len) {
        if (!has_ct) ksb_c(&rq, "Content-Type: application/x-www-form-urlencoded\r\n");
        char t[64]; snprintf(t, sizeof t, "Content-Length: %lld\r\n", (long long)body->len); ksb_c(&rq, t);
    }
    ksb_c(&rq, "\r\n");
    ksb_str(&rq, body);
    if (kx_conn_write(&c, rq.p, rq.len) < 0) { kx_conn_close(&c); return k_err(k_p2s(kx_fmt("URLError: %s", strerror(errno)))); }
    KSB in; ksb_init(&in);
    char buf[65536]; ssize_t n;
    while ((n = kx_conn_read(&c, buf, sizeof buf)) > 0) ksb_add(&in, buf, n);
    kx_conn_close(&c);
    const char *hend = kx_find(in.p, in.len, "\r\n\r\n");
    if (!hend || in.len < 12) return k_err(k_p2s(k_cstr("RemoteDisconnected: Remote end closed connection without response")));
    int status = atoi(in.p + 9);
    KMap *hs = k_map_empty(1);
    const char *line = kx_find(in.p, hend - in.p + 2, "\r\n") + 2;
    int chunked = 0; int64_t clen = -1;
    while (line < hend) {
        const char *eol = kx_find(line, hend - line + 2, "\r\n");
        const char *col = memchr(line, ':', eol - line);
        if (col) {
            KStr *k = kx_lower(line, col - line);
            const char *v = col + 1; while (v < eol && *v == ' ') v++;
            KStr *val = k_str_new(v, eol - v);
            int64_t f = k_map_find(hs, k_p2s(k));
            if (f >= 0) val = kx_fmt("%s, %s", K_S2P(KStr *, hs->d->vals[f])->data, val->data);
            hs = k_map_put(hs, k_p2s(k), k_p2s(val));
            if (!strcmp(k->data, "transfer-encoding") && strstr(val->data, "chunked")) chunked = 1;
            if (!strcmp(k->data, "content-length")) clen = atoll(val->data);
            if (!strcmp(k->data, "location")) *location = val;
        }
        line = eol + 2;
    }
    const char *bp = hend + 4; int64_t bl = in.len - (bp - in.p);
    KSB out; ksb_init(&out);
    if (chunked) {
        const char *p = bp, *e = bp + bl;
        while (p < e) {
            int64_t sz = strtoll(p, NULL, 16);
            const char *nl = kx_find(p, e - p, "\r\n");
            if (!nl || sz <= 0) break;
            p = nl + 2;
            if (p + sz > e) sz = e - p;
            ksb_add(&out, p, sz);
            p += sz + 2;
        }
    } else ksb_add(&out, bp, clen >= 0 && clen < bl ? clen : bl);
    *resp = k_struct_new(3, (KS)(int64_t)status, k_p2s(hs), k_p2s(ksb_done(&out)));
    return NULL;
}
static KEnum *kx_http_request(KStr *method, KStr *url, KMap *headers, KStr *body) {
#ifdef K_STD_TLS
    if (!kx_ssl_ctx) {      /* set up once, while holding the baton */
        SSL_library_init(); SSL_load_error_strings();
        kx_ssl_ctx = SSL_CTX_new(TLS_client_method());
        SSL_CTX_set_default_verify_paths(kx_ssl_ctx);
        SSL_CTX_set_verify(kx_ssl_ctx, SSL_VERIFY_PEER, NULL);
    }
#endif
    KList *hk = k_map_keys(headers), *hv = k_map_values(headers);
    for (int hop = 0; hop < 10; hop++) {
        KStr *loc = NULL; KS *resp = NULL;
        KX_IO_BEGIN;
        KEnum *e = kx_http_once(method, url, hk, hv, body, &loc, &resp);
        KX_IO_END;
        if (e) return e;
        int64_t st = (int64_t)resp[0];
        if (!loc || !(st == 301 || st == 302 || st == 303 || st == 307 || st == 308)) return k_ok(k_p2s(resp));
        if (strstr(loc->data, "://")) url = loc;
        else {
            const char *u = url->data; const char *s = strstr(u, "://"); s = s ? strchr(s + 3, '/') : NULL;
            int64_t base = s ? s - u : url->len;
            url = loc->data[0] == '/' ? kx_fmt("%.*s%s", (int)base, u, loc->data) : kx_fmt("%.*s/%s", (int)base, u, loc->data);
        }
        if (st != 307 && st != 308) { method = k_cstr("GET"); body = k_cstr(""); }
    }
    return k_err(k_p2s(k_cstr("HTTPError: HTTP Error 310: too many redirects")));
}

/* the server: one handler at a time (like the interpreter), event streams kept open alongside */
typedef struct { int fd; KStr *topic; } KStream;
static KStream *kx_streams; static int kx_nstreams, kx_capstreams;
static int64_t kx_web_publish(KStr *topic, KStr *data) {
    KSB m; ksb_init(&m);
    const char *p = data->data, *e = p + data->len;
    for (;;) {
        const char *nl = memchr(p, '\n', e - p);
        ksb_c(&m, "data: "); ksb_add(&m, p, (nl ? nl : e) - p); ksb_c(&m, "\n");
        if (!nl) break;
        p = nl + 1;
    }
    ksb_c(&m, "\n");
    for (int i = 0; i < kx_nstreams; i++) {
        if (!k_str_eq(kx_streams[i].topic, topic)) continue;
        if (send(kx_streams[i].fd, m.p, m.len, MSG_NOSIGNAL | MSG_DONTWAIT) < 0) {
            close(kx_streams[i].fd); kx_streams[i] = kx_streams[--kx_nstreams]; i--;
        }
    }
    return 0;
}
static const char *kx_reason(int64_t s) {
    switch (s) {
    case 200: return "OK"; case 201: return "Created"; case 204: return "No Content"; case 301: return "Moved Permanently";
    case 302: return "Found"; case 303: return "See Other"; case 304: return "Not Modified"; case 307: return "Temporary Redirect";
    case 308: return "Permanent Redirect"; case 400: return "Bad Request"; case 401: return "Unauthorized"; case 403: return "Forbidden";
    case 404: return "Not Found"; case 405: return "Method Not Allowed"; case 409: return "Conflict"; case 413: return "Payload Too Large";
    case 422: return "Unprocessable Entity"; case 429: return "Too Many Requests"; case 500: return "Internal Server Error";
    case 503: return "Service Unavailable"; default: return "";
    }
}
/* request limits: headers up to 64 KiB, bodies up to KLAR_MAX_BODY bytes (default 10 MiB) */
#define KX_MAX_HEAD 65536
static int64_t kx_max_body(void) {
    const char *v = getenv("KLAR_MAX_BODY");
    long long n = v && *v ? atoll(v) : 0;
    return n > 0 ? (int64_t)n : 10 * 1024 * 1024;
}
static void kx_refuse(int fd, const char *status) {
    char t[160];
    int n = snprintf(t, sizeof t, "HTTP/1.0 %s\r\ncontent-type: text/plain; charset=utf-8\r\ncontent-length: 0\r\nconnection: close\r\n\r\n", status);
    KConn c = {fd, NULL};
    kx_conn_write(&c, t, n);
    shutdown(fd, SHUT_WR);
    close(fd);
}
/* a header name or value that would end the header line early (response splitting) */
static int kx_bad_header(KStr *s, int name) {
    for (int64_t i = 0; i < s->len; i++) {
        unsigned char c = (unsigned char)s->data[i];
        if (c == '\r' || c == '\n' || c == 0 || (name && (c <= ' ' || c == ':' || c >= 127))) return 1;
    }
    return name && s->len == 0;
}
/* Handlers that are running (each on its own connection thread, one at a time under the baton) and where to
 * jump back to if one hits a runtime error. Only touched while holding the baton. */
typedef struct { pthread_t thread; jmp_buf *back; } KXRecover;
static KXRecover kx_recover[1100]; static int kx_nrecover;
static void kx_fail_hook(void) {
    for (int i = kx_nrecover - 1; i >= 0; i--)
        if (pthread_equal(kx_recover[i].thread, pthread_self())) longjmp(*kx_recover[i].back, 1);
}
static void kx_recover_pop(void) {
    pthread_t me = pthread_self();
    for (int i = kx_nrecover - 1; i >= 0; i--)
        if (pthread_equal(kx_recover[i].thread, me)) { kx_recover[i] = kx_recover[--kx_nrecover]; return; }
}
/* Files built into the executable (`klar build` of a server with browser apps or a public/ folder): web_file and
 * web_static look here first. The table is filled in by the generated program; each file is also kept gzipped
 * when that is smaller, and answered with an ETag so browsers revalidate with a cheap 304. */
typedef struct { const char *path; const char *data; int64_t len; const char *gz; int64_t gzlen; const char *etag; } KXAsset;
static const KXAsset *kx_assets; static int kx_nassets;
static const KXAsset *kx_asset_find(KStr *path) {
    if (!kx_nassets) return NULL;
    char key[1024]; int n = 0;
    const char *p = path->data, *e = p + path->len;
    while (p < e) {                                /* "./app.html", ".//public/./a.css" -> "app.html", "public/a.css" */
        const char *s = memchr(p, '/', e - p); if (!s) s = e;
        int64_t l = s - p;
        if (l == 0 || (l == 1 && p[0] == '.')) { p = s + 1; continue; }
        if (l == 2 && p[0] == '.' && p[1] == '.') return NULL;
        if (n + l + 1 >= (int64_t)sizeof key) return NULL;
        if (n) key[n++] = '/';
        memcpy(key + n, p, l); n += (int)l;
        p = s + 1;
    }
    key[n] = 0;
    if (path->len && path->data[0] == '/') return NULL;      /* an absolute path is a file on disk */
    for (int i = 0; i < kx_nassets; i++) if (!strcmp(kx_assets[i].path, key)) return &kx_assets[i];
    return NULL;
}

/* Production behaviour of the server: KLAR_LOG=off silences the one-line-per-request log on stderr; SIGTERM or
 * SIGINT stops taking new connections, lets requests in flight finish (up to KLAR_SHUTDOWN_SECONDS, default 10)
 * and returns from http_serve; a second signal exits at once. Idle keep-alive connections close after 5 s. */
static volatile sig_atomic_t kx_stopping;
static int kx_stop_pipe[2] = {-1, -1};
static int kx_log_on = 1;
static void kx_on_signal(int sig) {
    if (kx_stopping) _exit(128 + sig);
    kx_stopping = sig;
    if (kx_stop_pipe[1] >= 0) { ssize_t w = write(kx_stop_pipe[1], "x", 1); (void)w; }
}
static void kx_log(KStr *method, KStr *path, int64_t status, struct timespec *t0) {
    if (!kx_log_on) return;
    struct timespec t1; clock_gettime(CLOCK_MONOTONIC, &t1);
    double ms = (t1.tv_sec - t0->tv_sec) * 1e3 + (t1.tv_nsec - t0->tv_nsec) / 1e6;
    time_t now = time(NULL); struct tm tm; gmtime_r(&now, &tm);
    char line[1200], when[32];
    strftime(when, sizeof when, "%Y-%m-%dT%H:%M:%SZ", &tm);
    int pl = path->len > 1000 ? 1000 : (int)path->len;
    int n = snprintf(line, sizeof line, "%s %.*s %.*s %lld %.1fms\n", when, (int)(method->len > 16 ? 16 : method->len),
                     method->data, pl, path->data, (long long)status, ms);
    for (int i = 0; i < n - 1; i++) if ((unsigned char)line[i] < ' ') line[i] = '?';   /* no forged log lines */
    ssize_t w = write(2, line, n); (void)w;
}
static int kx_header_has(KMap *hs, const char *name, const char *token) {
    int64_t f = k_map_find(hs, k_p2s(k_cstr(name)));
    if (f < 0) return 0;
    KStr *v = K_S2P(KStr *, hs->d->vals[f]);
    int64_t tl = (int64_t)strlen(token);
    for (int64_t i = 0; i + tl <= v->len; i++)
        if (!strncasecmp(v->data + i, token, tl)) return 1;
    return 0;
}
/* Wait until fd has data, a stop was asked for, or `ms` passed. 1: data. */
static int kx_wait_readable(int fd, int ms) {
    while (ms > 0) {
        if (kx_stopping) return 0;
        struct pollfd p = {fd, POLLIN, 0};
        int slice = ms < 250 ? ms : 250;
        int r = poll(&p, 1, slice);
        if (r > 0) return 1;
        if (r < 0 && errno != EINTR) return 0;
        ms -= slice;
    }
    return 0;
}
/* The baton is not fair: under load a thread that has just read a request can take it again and again ahead of
 * one that has waited, which left a few requests waiting seconds. Handing it over strictly in arrival order costs
 * a third of the throughput (the next in line is usually asleep). So, as Go's mutex does, a request that has
 * waited 50 ms draws a ticket, and while tickets are out every request draws one and they go in order. */
typedef struct KXTurn { int64_t ticket; pthread_cond_t cv; struct KXTurn *next; } KXTurn;
static int64_t kx_ticket_next, kx_ticket_now;
static KXTurn *kx_turns;                  /* ticket holders waiting for their number, lowest first */
static pthread_mutex_t kx_ticket_mu = PTHREAD_MUTEX_INITIALIZER;
static int64_t kx_ticket(int draw) {      /* the next ticket number, drawing it if `draw` */
    pthread_mutex_lock(&kx_ticket_mu);
    int64_t n = draw ? kx_ticket_next++ : kx_ticket_next;
    pthread_mutex_unlock(&kx_ticket_mu);
    return n;
}
static int kx_lock_within(int ms) {       /* take the baton if that takes at most `ms`; 1 if taken */
#ifdef __APPLE__
    for (int waited = 0; waited < ms * 5; waited++) {         /* macOS has no pthread_mutex_timedlock */
        if (pthread_mutex_trylock(&kt_mu) == 0) return 1;
        struct timespec nap = {0, 200000}; nanosleep(&nap, NULL);
    }
    return 0;
#else
    struct timespec until; clock_gettime(CLOCK_REALTIME, &until);
    until.tv_nsec += (long)ms * 1000000;
    if (until.tv_nsec >= 1000000000) { until.tv_sec++; until.tv_nsec -= 1000000000; }
    return pthread_mutex_timedlock(&kt_mu, &until) == 0;
#endif
}
static void kx_take_baton(void) {
    int64_t my;
    if (kx_lock_within(50)) {
        if (kx_ticket(0) == kx_ticket_now) return;     /* nobody has waited long */
        my = kx_ticket(1);                              /* get in line */
    } else {
        my = kx_ticket(1);
        pthread_mutex_lock(&kt_mu);
    }
    if (my != kx_ticket_now) {
        KXTurn t; t.ticket = my; pthread_cond_init(&t.cv, NULL);
        KXTurn **p = &kx_turns;
        while (*p && (*p)->ticket < my) p = &(*p)->next;
        t.next = *p; *p = &t;
        while (kx_ticket_now != my) pthread_cond_wait(&t.cv, &kt_mu);
        for (p = &kx_turns; *p != &t; p = &(*p)->next) {}
        *p = t.next;
        pthread_cond_destroy(&t.cv);
    }
    kx_ticket_now++;                      /* the next ticket may go whenever this request gives up the baton */
    if (kx_turns && kx_turns->ticket == kx_ticket_now) pthread_cond_signal(&kx_turns->cv);
}
/* One connection: requests are read and answered in turn (HTTP/1.1 keep-alive) until the client closes, sends
 * `connection: close`, stays idle for 5 s, or the server stops. Returns after handing an event stream over. */
static void kx_serve_conn(int fd, KClo *handler, int keep) {
    struct timeval tv = {30, 0};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
    KSB in; ksb_init(&in);
    char buf[65536]; ssize_t n;
    for (;;) {
        const char *hend = NULL;
        while (!(hend = kx_find(in.p, in.len, "\r\n\r\n"))) {
            if (in.len > KX_MAX_HEAD) { kx_refuse(fd, "431 Request Header Fields Too Large"); return; }
            if (in.len == 0 && !kx_wait_readable(fd, 5000)) { close(fd); return; }    /* idle between requests */
            n = read(fd, buf, sizeof buf);
            if (n <= 0) { close(fd); return; }
            ksb_add(&in, buf, n);
        }
        struct timespec t0; clock_gettime(CLOCK_MONOTONIC, &t0);
        int64_t hlen = hend - in.p + 4;
        if (hlen > KX_MAX_HEAD) { kx_refuse(fd, "431 Request Header Fields Too Large"); return; }
        const char *eol = kx_find(in.p, in.len, "\r\n");
        const char *sp1 = memchr(in.p, ' ', eol - in.p);
        const char *sp2 = sp1 ? memchr(sp1 + 1, ' ', eol - sp1 - 1) : NULL;
        if (!sp1 || sp1 == in.p) { kx_refuse(fd, "400 Bad Request"); return; }
        int http11 = sp2 && eol - sp2 - 1 == 8 && !memcmp(sp2 + 1, "HTTP/1.1", 8);
        if (!sp2) sp2 = eol;
        KStr *method = k_str_new(in.p, sp1 - in.p);
        const char *target = sp1 + 1; int64_t tl = sp2 - target;
        const char *qm = memchr(target, '?', tl);
        KStr *path = k_str_new(target, (qm ? qm : target + tl) - target);
        KMap *query = qm ? kx_query_decode(qm + 1, target + tl - qm - 1, 1) : k_map_empty(1);
        KMap *hs = k_map_empty(1);
        int64_t clen = 0;
        const char *line = eol + 2;
        while (line < hend) {
            const char *le = kx_find(line, hend - line + 2, "\r\n");
            const char *col = memchr(line, ':', le - line);
            if (col) {
                KStr *k = kx_lower(line, col - line);
                const char *v = col + 1; while (v < le && *v == ' ') v++;
                hs = k_map_put(hs, k_p2s(k), k_p2s(k_str_new(v, le - v)));
                if (!strcmp(k->data, "content-length")) {
                    const char *d = v; clen = 0;
                    if (d == le) clen = -1;
                    for (; d < le && clen >= 0; d++) {
                        if (*d < '0' || *d > '9' || clen > ((int64_t)1 << 50)) clen = -1;
                        else clen = clen * 10 + (*d - '0');
                    }
                    if (clen < 0) { kx_refuse(fd, "400 Bad Request"); return; }
                }
                if (!strcmp(k->data, "transfer-encoding")) { kx_refuse(fd, "501 Not Implemented"); return; }
            }
            line = le + 2;
        }
        if (clen > kx_max_body()) { kx_refuse(fd, "413 Payload Too Large"); return; }
        while (in.len - hlen < clen) {
            n = read(fd, buf, sizeof buf);
            if (n <= 0) { close(fd); return; }                  /* the body never arrived */
            ksb_add(&in, buf, n);
        }
        KStr *body = k_str_new(in.p + hlen, clen);
        /* what follows this request (a pipelined next one) stays in the buffer */
        int64_t used = hlen + clen;
        memmove(in.p, in.p + used, in.len - used); in.len -= used;
        int alive = keep && !kx_stopping && (http11 ? !kx_header_has(hs, "connection", "close")
                                                    : kx_header_has(hs, "connection", "keep-alive"));
        KS *req = k_struct_new(6, k_p2s(method), k_p2s(path), k_p2s(query), k_p2s(hs), k_p2s(body), k_p2s(k_map_empty(1)));
        kx_take_baton();                      /* the handler is Klar code: it runs holding the baton */
        KS *resp;
        jmp_buf back;
        if (kx_nrecover >= (int)(sizeof kx_recover / sizeof kx_recover[0])) {
            resp = K_S2P(KS *, K_CALL1(handler, k_p2s(req)));
        } else if (setjmp(back) == 0) {
            k_fail_hook = kx_fail_hook;
            kx_recover[kx_nrecover].thread = pthread_self(); kx_recover[kx_nrecover++].back = &back;
            resp = K_S2P(KS *, K_CALL1(handler, k_p2s(req)));
            kx_recover_pop();
        } else {
            /* a runtime error in the handler (already printed): this request gets 500, the server goes on */
            kx_recover_pop();
            resp = k_struct_new(3, (KS)(int64_t)500,
                                k_p2s(k_map_put(k_map_empty(1), k_p2s(k_cstr("content-type")), k_p2s(k_cstr("text/plain; charset=utf-8")))),
                                k_p2s(k_cstr("internal error\n")));
        }
        fflush(stdout);
        int64_t status = (int64_t)resp[0];
        KMap *rh = K_S2P(KMap *, resp[1]);
        KStr *payload = K_S2P(KStr *, resp[2]);
        KS stream = 0, file = 0;
        int64_t f;
        if ((f = k_map_find(rh, k_p2s(k_cstr("x-klar-stream")))) >= 0) stream = rh->d->vals[f];
        if ((f = k_map_find(rh, k_p2s(k_cstr("x-klar-file")))) >= 0) { file = rh->d->vals[f]; rh = k_map_remove(rh, k_p2s(k_cstr("x-klar-file"))); }
        if (stream) {
            const char *h = "HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\ncache-control: no-store\r\nconnection: close\r\n\r\n: connected\n\n";
            if (send(fd, h, strlen(h), MSG_NOSIGNAL) < 0) { close(fd); pthread_mutex_unlock(&kt_mu); return; }
            if (kx_nstreams == kx_capstreams) {
                kx_capstreams = kx_capstreams ? kx_capstreams * 2 : 8;
                kx_streams = (KStream *)realloc(kx_streams, sizeof(KStream) * kx_capstreams);
            }
            kx_streams[kx_nstreams].fd = fd; kx_streams[kx_nstreams++].topic = K_S2P(KStr *, stream);
            pthread_mutex_unlock(&kt_mu);
            kx_log(method, path, 200, &t0);
            return;
        }
        const char *data = payload->data; int64_t dlen = payload->len;
        const char *gz = NULL, *etag = NULL;
        if (file) {
            const KXAsset *a = kx_asset_find(K_S2P(KStr *, file));
            if (a) {
                data = a->data; dlen = a->len; etag = a->etag;
                if (a->gz && kx_header_has(hs, "accept-encoding", "gzip")) { gz = a->gz; dlen = a->gzlen; }
                if (kx_header_has(hs, "if-none-match", a->etag)) status = 304;
            } else {
                KEnum *r = kx_fs_read(K_S2P(KStr *, file));
                if (r->tag == K_TAG_OK) { payload = K_S2P(KStr *, r->f[0]); data = payload->data; dlen = payload->len; }
                else { status = 404; data = "not found\n"; dlen = 10; rh = k_map_put(k_map_empty(1), k_p2s(k_cstr("content-type")), k_p2s(k_cstr("text/plain; charset=utf-8"))); }
            }
        }
        KSB out; ksb_init(&out);
        char t[160];
        snprintf(t, sizeof t, "HTTP/1.1 %lld %s\r\n", (long long)status, kx_reason(status)); ksb_c(&out, t);
        if (k_map_find(rh, k_p2s(k_cstr("content-type"))) < 0) ksb_c(&out, "content-type: text/plain; charset=utf-8\r\n");
        KList *ks = k_map_keys(rh), *vs = k_map_values(rh);
        for (int64_t i = 0; i < ks->len; i++) {
            KStr *k = kx_list_str(ks, i), *v = kx_list_str(vs, i);
            if (kx_bad_header(k, 1)) { fprintf(stderr, "klar: dropped a response header with an invalid name\n"); continue; }
            if (!strcmp(k->data, "connection") || !strcmp(k->data, "content-length")) continue;   /* the server's own */
            const char *p = v->data, *e = p + v->len;
            for (;;) {
                const char *nl = strcmp(k->data, "set-cookie") ? NULL : memchr(p, '\n', e - p);
                KStr *one = k_str_new(p, (nl ? nl : e) - p);
                if (kx_bad_header(one, 0)) fprintf(stderr, "klar: dropped response header `%s`: its value contains a line break\n", k->data);
                else { ksb_str(&out, k); ksb_c(&out, ": "); ksb_str(&out, one); ksb_c(&out, "\r\n"); }
                if (!nl) break;
                p = nl + 1;
            }
        }
        if (etag) {
            snprintf(t, sizeof t, "etag: %s\r\nvary: accept-encoding\r\n", etag); ksb_c(&out, t);
            if (k_map_find(rh, k_p2s(k_cstr("cache-control"))) < 0) ksb_c(&out, "cache-control: no-cache\r\n");
            if (gz && status != 304) ksb_c(&out, "content-encoding: gzip\r\n");
        }
        if (status == 304 || status == 204) dlen = 0;
        snprintf(t, sizeof t, "content-length: %lld\r\nconnection: %s\r\n\r\n", (long long)dlen, alive ? "keep-alive" : "close");
        ksb_c(&out, t);
        int head = !strcmp(method->data, "HEAD");
        /* small bodies go out with the headers; big ones (files) are sent from where they are, without a copy */
        if (!head && dlen <= 16384) ksb_add(&out, gz ? gz : data, dlen);
        pthread_mutex_unlock(&kt_mu);
        KConn c = {fd, NULL};
        int bad = kx_conn_write(&c, out.p, out.len) < 0 || (!head && dlen > 16384 && kx_conn_write(&c, gz ? gz : data, dlen) < 0);
        kx_log(method, path, status, &t0);
        if (bad || !alive) break;
    }
    shutdown(fd, SHUT_WR);
    close(fd);
}
typedef struct { int fd; KClo *handler; int keep; } KSrvConn;
static int64_t kx_conn_active;
static void *kx_conn_main(void *p) {
    KSrvConn *a = (KSrvConn *)p;
    stack_t ss; ss.ss_sp = malloc(65536); ss.ss_size = 65536; ss.ss_flags = 0; sigaltstack(&ss, NULL);
    kx_serve_conn(a->fd, a->handler, a->keep);
    pthread_mutex_lock(&kt_mu);
    kx_conn_active--; kt_live--;
    pthread_cond_broadcast(&kt_cv);
    pthread_mutex_unlock(&kt_mu);
    return NULL;
}
static int kx_no_conns(void *p) { (void)p; return kx_conn_active == 0; }
/* Each connection is read and answered on its own thread; handlers run one at a time (they hold the baton),
 * so a slow client or a handler waiting on io does not hold up the others. */
static KEnum *kx_http_serve(int64_t port, KClo *handler, int64_t max) {
    signal(SIGPIPE, SIG_IGN);
    fflush(stdout);                       /* what main printed shows up in the logs now, not after a request */
    kt_enable();
    const char *lg = getenv("KLAR_LOG");
    kx_log_on = !(lg && (!strcmp(lg, "off") || !strcmp(lg, "0") || !strcmp(lg, "false")));
    const char *host = getenv("HOST");
    if (!host || !*host) host = "127.0.0.1";
    struct addrinfo hints, *res;
    memset(&hints, 0, sizeof hints); hints.ai_socktype = SOCK_STREAM; hints.ai_flags = AI_PASSIVE;
    char ps[16]; snprintf(ps, sizeof ps, "%lld", (long long)port);
    if (getaddrinfo(host, ps, &hints, &res) != 0) return k_err(k_p2s(kx_fmt("OSError: cannot resolve %s", host)));
    int fd = socket(res->ai_family, SOCK_STREAM, 0), one = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    if (bind(fd, res->ai_addr, res->ai_addrlen) != 0 || listen(fd, 511) != 0) {
        int e = errno; freeaddrinfo(res); close(fd); errno = e;
        return k_err(k_p2s(kx_fmt("OSError: [Errno %d] %s", e, strerror(e))));
    }
    freeaddrinfo(res);
    if (kx_stop_pipe[0] < 0 && pipe(kx_stop_pipe) == 0) {
        fcntl(kx_stop_pipe[0], F_SETFD, FD_CLOEXEC); fcntl(kx_stop_pipe[1], F_SETFD, FD_CLOEXEC);
        struct sigaction sa; memset(&sa, 0, sizeof sa); sa.sa_handler = kx_on_signal; sigemptyset(&sa.sa_mask);
        sigaction(SIGTERM, &sa, NULL); sigaction(SIGINT, &sa, NULL);
    }
    int64_t served = 0;
    while ((max <= 0 || served < max) && !kx_stopping) {
        struct pollfd p[2] = {{fd, POLLIN, 0}, {kx_stop_pipe[0], POLLIN, 0}};
        KX_IO_BEGIN;
        int r = poll(p, kx_stop_pipe[0] >= 0 ? 2 : 1, 15000);
        int c = r > 0 && (p[0].revents & POLLIN) ? accept(fd, NULL, NULL) : -1;
        KX_IO_END;
        if (kx_stopping) { if (c >= 0) close(c); break; }
        if (r == 0) {          /* keep event streams alive through proxies */
            for (int i = 0; i < kx_nstreams; i++)
                if (send(kx_streams[i].fd, ": ping\n\n", 8, MSG_NOSIGNAL | MSG_DONTWAIT) < 0) {
                    close(kx_streams[i].fd); kx_streams[i] = kx_streams[--kx_nstreams]; i--;
                }
            continue;
        }
        if (c < 0) continue;
        if (kx_conn_active >= 1024) { kx_refuse(c, "503 Service Unavailable"); continue; }   /* bounded threads */
        setsockopt(c, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
        KSrvConn *a = (KSrvConn *)K_ALLOC(sizeof(KSrvConn));
        /* tests serve a counted number of requests, one per connection; past 512 open connections new ones
           are answered once, so idle keep-alive clients cannot use up the threads */
        a->fd = c; a->handler = handler; a->keep = max <= 0 && kx_conn_active < 512;
        kx_conn_active++; kt_live++;
        kt_thread(kx_conn_main, a);
        served++;
    }
    close(fd);
    if (kx_stopping) {
        const char *sv = getenv("KLAR_SHUTDOWN_SECONDS");
        int secs = sv && *sv ? atoi(sv) : 10;
        if (kx_log_on) fprintf(stderr, "klar: %s received, finishing %lld open connection(s) (up to %d s)\n",
                               kx_stopping == SIGINT ? "SIGINT" : "SIGTERM", (long long)kx_conn_active, secs);
        for (int i = 0; i < kx_nstreams; i++) { shutdown(kx_streams[i].fd, SHUT_RDWR); close(kx_streams[i].fd); }
        kx_nstreams = 0;
        struct timespec until; clock_gettime(CLOCK_REALTIME, &until); until.tv_sec += secs;
        while (!kx_no_conns(NULL)) {
            if (pthread_cond_timedwait(&kt_cv, &kt_mu, &until) == ETIMEDOUT) break;
        }
        return k_ok(0);
    }
    while (!kx_no_conns(NULL)) pthread_cond_wait(&kt_cv, &kt_mu);    /* the last replies go out first */
    return k_ok(0);
}
#endif
