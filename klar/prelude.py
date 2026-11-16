"""Signatures of Klar's built-in functions, written in Klar itself.

Functions listed in OVERLOADED are typed by special rules in the checker
(they accept several container types); their signature here documents the
common case.
"""

PRELUDE = r'''
// ---- io (require `uses io`)
fn print[T](value: T) uses io {}
fn read_line() -> Option[str] uses io {}
fn read_file(path: str) -> Result[str, str] uses io {}
fn write_file(path: str, content: str) -> Result[unit, str] uses io {}
fn args() -> list[str] uses io {}

// ---- general
fn str[T](value: T) -> str {}
fn panic(message: str) -> never {}

// ---- lists (all return new lists; values are immutable)
fn len[T](xs: list[T]) -> int {}
fn push[T](xs: list[T], x: T) -> list[T] {}
fn set_at[T](xs: list[T], index: int, x: T) -> list[T] {}
fn get[T](xs: list[T], index: int) -> Option[T] {}
fn slice[T](xs: list[T], start: int, end: int) -> list[T] {}
fn contains[T](xs: list[T], x: T) -> bool {}
fn reverse[T](xs: list[T]) -> list[T] {}
fn range(start: int, end: int) -> list[int] {}
fn map[T, U](xs: list[T], f: fn(T) -> U) -> list[U] {}
fn filter[T](xs: list[T], f: fn(T) -> bool) -> list[T] {}
fn fold[T, A](xs: list[T], init: A, f: fn(A, T) -> A) -> A {}
fn any[T](xs: list[T], f: fn(T) -> bool) -> bool {}
fn all[T](xs: list[T], f: fn(T) -> bool) -> bool {}
fn find[T](xs: list[T], f: fn(T) -> bool) -> Option[T] {}
fn index_of[T](xs: list[T], x: T) -> Option[int] {}
fn sort[T](xs: list[T]) -> list[T] {}
fn sort_by[T, K](xs: list[T], key: fn(T) -> K) -> list[T] {}
fn sum(xs: list[int]) -> int {}
fn join(xs: list[str], sep: str) -> str {}

// ---- strings
fn split(s: str, sep: str) -> list[str] {}
fn chars(s: str) -> list[str] {}
fn trim(s: str) -> str {}
fn upper(s: str) -> str {}
fn lower(s: str) -> str {}
fn starts_with(s: str, prefix: str) -> bool {}
fn ends_with(s: str, suffix: str) -> bool {}
fn replace(s: str, old: str, new: str) -> str {}
fn repeat(s: str, times: int) -> str {}
fn parse_int(s: str) -> Option[int] {}
fn parse_float(s: str) -> Option[float] {}

// ---- numbers
fn to_float(n: int) -> float {}
fn floor(x: float) -> int {}
fn round(x: float) -> int {}
fn sqrt(x: float) -> float {}
fn abs(x: int) -> int {}
fn min(a: int, b: int) -> int {}
fn max(a: int, b: int) -> int {}

// ---- maps (all return new maps)
fn keys[K, V](m: map[K, V]) -> list[K] {}
fn values[K, V](m: map[K, V]) -> list[V] {}
fn entries[K, V](m: map[K, V]) -> list[(K, V)] {}
fn has[K, V](m: map[K, V], key: K) -> bool {}
fn put[K, V](m: map[K, V], key: K, value: V) -> map[K, V] {}
fn remove[K, V](m: map[K, V], key: K) -> map[K, V] {}

// ---- Option / Result
fn unwrap[T](o: Option[T]) -> T {}
fn unwrap_or[T](o: Option[T], default: T) -> T {}
fn ok_or[T, E](o: Option[T], error: E) -> Result[T, E] {}
fn is_some[T](o: Option[T]) -> bool {}
fn is_none[T](o: Option[T]) -> bool {}
fn is_ok[T, E](r: Result[T, E]) -> bool {}
fn is_err[T, E](r: Result[T, E]) -> bool {}
'''

# Built-ins whose argument types are checked by dedicated rules.
OVERLOADED = {
    "print", "str", "len", "get", "slice", "contains", "reverse", "sort", "sum",
    "abs", "min", "max", "unwrap", "unwrap_or",
}
