/* Minimal declarations for the system's Boehm GC (libgc.so.1); libgc-dev headers are not installed here. */
#include <stddef.h>
void *GC_malloc(size_t);
void *GC_malloc_atomic(size_t);
void *GC_realloc(void *, size_t);
void GC_init(void);
#define GC_MALLOC(n) GC_malloc(n)
#define GC_MALLOC_ATOMIC(n) GC_malloc_atomic(n)
#define GC_REALLOC(p, n) GC_realloc(p, n)
#define GC_INIT() GC_init()
