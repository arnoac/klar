// Klar JavaScript runtime (shared by the Node and browser targets).
// Value representation:
//   int, float -> number (ints are checked to stay within +-2^53)   bool -> boolean   unit -> undefined
//   str -> string        list -> KL (append-only buffer + length)    map -> KM (persistent, insertion ordered)
//   struct -> an instance of a generated class    enum -> KE {t: variant name, f: fields}    fn -> function

class KlarError extends Error {
  constructor(code, line, col, msg, hint) {
    super(msg);
    this.code = code; this.line = line; this.col = col; this.hint = hint || null;
  }
}
let $file = "<input>";
function $fail(code, l, c, msg, hint) { throw new KlarError(code, l, c, msg, hint); }
function $render_error(e) {
  if (e instanceof KlarError) {
    return `error[${e.code}] ${$file}:${e.line}:${e.col}: ${e.message}` + (e.hint ? `\n  hint: ${e.hint}` : "");
  }
  if (e instanceof RangeError && /call stack/i.test(e.message)) {
    return "error[E406] stack overflow (recursion too deep)";
  }
  return "error[E407] " + (e && e.stack ? e.stack : String(e));
}

// ---------------------------------------------------------------- numbers
// Ints are 64-bit like in native builds. Values within +-2^53 are plain numbers (the fast path); larger ones
// are BigInts. Every int is kept in that canonical form, so === and Map keys work for both.
const $MAXI = 9007199254740991;
const $I64_MAX = 9223372036854775807n, $I64_MIN = -9223372036854775808n;
const $OVF = "integer overflow: the result does not fit in 64 bits";
function $big(x, l, c) {
  if (x > $I64_MAX || x < $I64_MIN) $fail("E408", l, c, $OVF, "compiled Klar ints are 64-bit; the interpreter (`klar run`) has unlimited integers");
  return (x <= 9007199254740991n && x >= -9007199254740991n) ? Number(x) : x;
}
function $add(a, b, l, c) {
  if (typeof a === "number" && typeof b === "number") { const r = a + b; if (r <= $MAXI && r >= -$MAXI) return r; }
  return $big(BigInt(a) + BigInt(b), l, c);
}
function $sub(a, b, l, c) {
  if (typeof a === "number" && typeof b === "number") { const r = a - b; if (r <= $MAXI && r >= -$MAXI) return r; }
  return $big(BigInt(a) - BigInt(b), l, c);
}
function $mul(a, b, l, c) {
  if (typeof a === "number" && typeof b === "number") { const r = a * b; if (r <= $MAXI && r >= -$MAXI) return r === 0 ? 0 : r; }
  return $big(BigInt(a) * BigInt(b), l, c);
}
function $div(a, b, l, c) {
  if (b === 0) $fail("E402", l, c, "integer division by zero");
  if (typeof a === "number" && typeof b === "number") { const r = (a - a % b) / b; return r === 0 ? 0 : r; }
  return $big(BigInt(a) / BigInt(b), l, c);
}
function $rem(a, b, l, c) {
  if (b === 0) $fail("E402", l, c, "remainder by zero");
  if (typeof a === "number" && typeof b === "number") { const r = a % b; return r === 0 ? 0 : r; }
  return $big(BigInt(a) % BigInt(b), l, c);
}
function $neg(a, l, c) { return typeof a === "number" ? (a === 0 ? 0 : -a) : $big(-a, l, c); }
function $iabs(a, l, c) { return typeof a === "number" ? Math.abs(a) : $big(a < 0n ? -a : a, l, c); }
function $num2int(x) { return Number.isSafeInteger(x) ? x : (Number.isFinite(x) ? $big(BigInt(x), 0, 0) : x); }
function $to_float(a) { return typeof a === "number" ? a : Number(a); }
function $int_lit(v) { return v; }
function $fmt_float(x) {
  if (x !== x) return "nan";
  if (x === Infinity) return "inf";
  if (x === -Infinity) return "-inf";
  if (x === 0) return Object.is(x, -0) ? "-0.0" : "0.0";
  let [m, ex] = x.toExponential().split("e");
  const exp = +ex;
  const neg = m[0] === "-";
  if (neg) m = m.slice(1);
  const digits = m.replace(".", "");
  let s;
  if (exp >= -4 && exp < 16) {
    if (exp >= 0) {
      s = digits.length <= exp + 1 ? digits + "0".repeat(exp + 1 - digits.length) + ".0"
        : digits.slice(0, exp + 1) + "." + digits.slice(exp + 1);
    } else {
      s = "0." + "0".repeat(-exp - 1) + digits;
    }
  } else {
    s = digits[0] + (digits.length > 1 ? "." + digits.slice(1) : "") + "e" + (exp < 0 ? "-" : "+")
      + String(Math.abs(exp)).padStart(2, "0");
  }
  return neg ? "-" + s : s;
}
function $floor(x) { return $num2int(Math.floor(x)); }
function $round(x) { return $num2int(Math.floor(x + 0.5)); }

// ---------------------------------------------------------------- strings (indexed by code point)
const $ASTRAL = /[\uD800-\uDFFF]/;
let $cs_s = "", $cs_a = null;
function $cps(s) {                      // code points of s, or null when every code unit is one code point
  if (s === $cs_s) return $cs_a;
  const a = s.length > 0 && $ASTRAL.test(s) ? Array.from(s) : null;
  $cs_s = s; $cs_a = a;
  return a;
}
function $slen(s) { const a = $cps(s); return a ? a.length : s.length; }
function $sidx(s, i, l, c) {
  const a = $cps(s);
  const n = a ? a.length : s.length;
  if (i < 0 || i >= n) $fail("E401", l, c, `index ${i} is out of bounds for length ${n}`,
    "check the index against len(...) first, or use get(xs, i) which returns an Option");
  return a ? a[i] : s[i];
}
function $sslice(s, a, b) {
  const arr = $cps(s);
  const n = arr ? arr.length : s.length;
  a = Math.max(0, Math.min(a, n)); b = Math.max(a, Math.min(b, n));
  return arr ? arr.slice(a, b).join("") : s.slice(a, b);
}
function $ord(s, l, c) {
  const a = Array.from(s);
  if (a.length !== 1) $fail("E401", l, c, `ord needs exactly one character, got ${a.length}`);
  return a[0].codePointAt(0);
}
function $chr(n, l, c) {
  const k = Number(n);
  if (!(k >= 0 && k <= 0x10FFFF) || (k >= 0xD800 && k <= 0xDFFF)) $fail("E401", l, c, `chr: ${n} is not a Unicode code point`);
  return String.fromCodePoint(k);
}
function $chars(s) { return $L(Array.from(s)); }
function $split(s, sep) { return $L(sep === "" ? Array.from(s) : s.split(sep)); }
function $replace(s, a, b) { return s.replaceAll(a, () => b); }
function $repeat(s, k) { return k > 0 ? s.repeat(k) : ""; }
function $trim(s) { return s.replace(/^[\s\x1c-\x1f\x85]+|[\s\x1c-\x1f\x85]+$/g, ""); }
function $str_reverse(s) { return Array.from(s).reverse().join(""); }
function $cmp(a, b) { return a < b ? -1 : a > b ? 1 : 0; }
function $parse_int(s) {
  const t = $trim(s);
  if (!/^[+-]?[0-9]+$/.test(t)) return $None;
  const v = Number(t);
  if (Number.isSafeInteger(v)) return $Some(v === 0 ? 0 : v);
  const big = BigInt(t);
  return big > $I64_MAX || big < $I64_MIN ? $None : $Some(big);
}
function $parse_float(s) {
  const t = $trim(s).toLowerCase();
  if (/^[+-]?(inf|infinity)$/.test(t)) return $Some(t[0] === "-" ? -Infinity : Infinity);
  if (/^[+-]?nan$/.test(t)) return $Some(NaN);
  if (!/^[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)(e[+-]?[0-9]+)?$/.test(t)) return $None;
  return $Some(Number(t));
}

// ---------------------------------------------------------------- lists
// A list is a view (buffer, length). Buffers only grow at the end, so pushing onto the newest view of a buffer
// appends in place (amortised O(1)) while older views keep seeing their own prefix.
class KL { constructor(b, n, v) { this.b = b; this.n = n; this.v = v | 0; } }
// set_at works in place on the newest version of a buffer: the array records (version, index, old value)
// in its undo log ($log) and bumps its version ($v). A list whose version is older than its array's is stale
// and rebuilds a private copy from the log the first time it is read ($d does the check), so older values
// never change and `xs = set_at(xs, i, x)` is O(1).
function $d(l) { return l.v === (l.b.$v | 0) ? l.b : $lsync(l); }
function $lsync(l) {
  const b = l.b, d = b.slice(0, l.n), log = b.$log;
  for (let k = log.length - 3; k >= 0 && log[k] >= l.v; k -= 3) if (log[k + 1] < l.n) d[log[k + 1]] = log[k + 2];
  l.b = d; l.v = 0;
  return d;
}
function $L(arr) { return new KL(arr, arr.length, 0); }
function $arr(l) { const d = $d(l); return d.length === l.n ? d : d.slice(0, l.n); }
function $push(l, x) {
  const b = $d(l);
  if (b.length === l.n) { b.push(x); return new KL(b, l.n + 1, l.v); }
  const nb = b.slice(0, l.n); nb.push(x);
  return new KL(nb, nb.length, 0);
}
// A view one shorter shares the buffer: a later push onto it copies, so `xs = pop(xs)` on an owned var
// is done in place by the compiler instead.
function $pop(l, line, col) {
  if (l.n === 0) $fail("E401", line, col, "pop on an empty list");
  return new KL(l.b, l.n - 1, l.v);
}
function $last(l) { return l.n === 0 ? $None : $Some($d(l)[l.n - 1]); }
function $concat(a, b) {
  const da = $d(a), db = $d(b), n = b.n;
  if (da.length === a.n) {
    for (let i = 0; i < n; i++) da.push(db[i]);
    return new KL(da, a.n + n, a.v);
  }
  const nb = da.slice(0, a.n);
  for (let i = 0; i < n; i++) nb.push(db[i]);
  return $L(nb);
}
function $lget(l, i, line, col) {
  if (i < 0 || i >= l.n) $fail("E401", line, col, `index ${i} is out of bounds for length ${l.n}`,
    "check the index against len(...) first, or use get(xs, i) which returns an Option");
  return $d(l)[i];
}
// For an owned list variable (see ownership.py): copy into a fresh array that only the variable will see.
function $set_at_fresh(l, i, x, line, col) {
  if (i < 0 || i >= l.n) $fail("E401", line, col, `set_at index ${i} is out of bounds for length ${l.n}`);
  const nb = $d(l).slice(0, l.n); nb[i] = x;
  return new KL(nb, l.n, 0);
}
function $set_at(l, i, x, line, col) {
  if (i < 0 || i >= l.n) $fail("E401", line, col, `set_at index ${i} is out of bounds for length ${l.n}`);
  const d = $d(l);
  let log = d.$log;
  if (log === undefined) log = d.$log = [];
  if (log.length < 3 * (d.length + 16)) {
    const v = d.$v | 0;
    log.push(v, i, d[i]);
    d.$v = v + 1;
    d[i] = x;
    return new KL(d, l.n, v + 1);
  }
  const nb = d.slice(0, l.n); nb[i] = x;
  return $L(nb);
}
function $lslice(l, a, b) {
  a = Math.max(0, Math.min(a, l.n)); b = Math.max(a, Math.min(b, l.n));
  return $L($d(l).slice(a, b));
}
function $range(a, b) { const r = []; for (let i = a; i < b; i++) r.push(i); return $L(r); }
function $lreverse(l) { return $L($d(l).slice(0, l.n).reverse()); }
function $sort(l) {
  const a = $d(l).slice(0, l.n);
  if (a.length && a.every(x => typeof x === "number")) a.sort((x, y) => x - y); else a.sort($cmp);
  return $L(a);
}
function $sort_by(l, f) {
  const keys = new Array(l.n);
  for (let i = 0; i < l.n; i++) keys[i] = f($d(l)[i]);
  const idx = keys.map((_, i) => i);
  if (keys.length && keys.every(x => typeof x === "number")) idx.sort((x, y) => keys[x] - keys[y]);
  else idx.sort((x, y) => $cmp(keys[x], keys[y]));
  const d = $d(l);
  return $L(idx.map(i => d[i]));
}
function $sum_int(l, line, col) { let s = 0; const d = $d(l); for (let i = 0; i < l.n; i++) s = $add(s, d[i], line, col); return s; }
function $sum_float(l) { let s = 0; const d = $d(l); for (let i = 0; i < l.n; i++) s += d[i]; return s; }
function $join(l, sep) { return $arr(l).join(sep); }

// ---------------------------------------------------------------- maps
// Persistent maps with O(1) updates of the newest version (Baker's trick): the newest version owns a JS Map;
// an older version stores the one-key difference to a newer one and is "rerooted" (the differences are
// flipped) when it is used again. Entries are [key, value] under a primitive lookup key.
class KM { constructor(d, size) { this.d = d; this.size = size; this.dk = undefined; this.dv = undefined; this.next = null; } }
function $key(k) {
  const t = typeof k;
  return (t === "string" || t === "number" || t === "boolean" || t === "bigint") ? k : "\u0000" + $keystr(k);
}
function $keystr(v) {
  if (v instanceof KL) return "[" + $arr(v).map($keystr).join(",") + "]";
  if (v instanceof KE) return v.t + "(" + v.f.map($keystr).join(",") + ")";
  if (v instanceof KM) return "{" + [...$reroot(v).values()].map(e => $keystr(e[0]) + ":" + $keystr(e[1])).join(",") + "}";
  if (v !== null && typeof v === "object") return v.constructor.name + "(" + Object.values(v).map($keystr).join(",") + ")";
  return typeof v === "string" ? JSON.stringify(v) : String(v);
}
function $reroot(m) {
  if (m.d) return m.d;
  const path = [];
  let x = m;
  while (!x.d) { path.push(x); x = x.next; }
  for (let i = path.length - 1; i >= 0; i--) {
    const v = path[i];
    const d = x.d, k2 = v.dk, cur = d.get(k2);
    if (v.dv === undefined) d.delete(k2); else d.set(k2, v.dv);
    x.d = null; x.dk = k2; x.dv = cur; x.next = v;
    v.d = d; v.dk = undefined; v.dv = undefined; v.next = null;
    x = v;
  }
  return m.d;
}
function $M(entries) {
  const d = new Map();
  for (const [k, v] of entries) d.set($key(k), [k, v]);
  return new KM(d, d.size);
}
function $mput(m, k, v) {
  const d = $reroot(m), k2 = $key(k), prev = d.get(k2);
  d.set(k2, [k, v]);
  const n = new KM(d, prev === undefined ? m.size + 1 : m.size);
  m.d = null; m.dk = k2; m.dv = prev; m.next = n;
  return n;
}
// `v = put(v, k, x)` while v is owned (see ownership.py): v is the newest version and nothing else refers to it,
// so its Map is updated in place.
function $mput_owned(m, k, v) {
  const k2 = $key(k), e = m.d.get(k2);
  if (e === undefined) { m.d.set(k2, [k, v]); m.size++; } else e[1] = v;
}
function $mremove(m, k) {
  const d = $reroot(m), k2 = $key(k);
  if (!d.has(k2)) return m;
  const nd = new Map(d); nd.delete(k2);
  return new KM(nd, nd.size);
}
function $mfind(m, k) { return $reroot(m).get($key(k)); }
function $mindex(m, k, l, c) {
  const e = $mfind(m, k);
  if (e === undefined) $fail("E405", l, c, `map has no key ${typeof k === "string" ? $quote(k) : $keystr(k)}`,
    "use get(m, key) which returns an Option, or check has(m, key) first");
  return e[1];
}
function $mget(m, k) { const e = $mfind(m, k); return e === undefined ? $None : $Some(e[1]); }
function $mhas(m, k) { return $mfind(m, k) !== undefined; }
function $entries(m) { return [...$reroot(m).values()]; }
function $mkeys(m) { return $L($entries(m).map(e => e[0])); }
function $mvalues(m) { return $L($entries(m).map(e => e[1])); }
function $mentries(m) { return $L($entries(m).map(e => new $structs.Tuple2(e[0], e[1]))); }

// ---------------------------------------------------------------- enums, Option, Result
class KE { constructor(t, f) { this.t = t; this.f = f; } }
const $None = new KE("None", []);
function $Some(x) { return new KE("Some", [x]); }
function $Ok(x) { return new KE("Ok", [x]); }
function $Err(x) { return new KE("Err", [x]); }
function $ok_or(o, e) { return o.t === "Some" ? $Ok(o.f[0]) : $Err(e); }
function $unwrap(o, l, c, show_err) {
  if (o.t === "Some" || o.t === "Ok") return o.f[0];
  if (o.t === "Err") $fail("E403", l, c, `unwrap() called on Err(${show_err(o.f[0])})`, "handle the error with match or `?` instead of unwrap");
  $fail("E403", l, c, "unwrap() called on None", "use unwrap_or(o, default) or match");
}
const $structs = {};

// ---------------------------------------------------------------- equality (structural)
function $eq(a, b) {
  if (a === b) return true;
  if (typeof a !== "object" || a === null || typeof b !== "object" || b === null) return false;
  if (a instanceof KL) {
    if (!(b instanceof KL) || a.n !== b.n) return false;
    const da = $d(a), db = $d(b);
    for (let i = 0; i < a.n; i++) if (!$eq(da[i], db[i])) return false;
    return true;
  }
  if (a instanceof KE) {
    if (!(b instanceof KE) || a.t !== b.t || a.f.length !== b.f.length) return false;
    for (let i = 0; i < a.f.length; i++) if (!$eq(a.f[i], b.f[i])) return false;
    return true;
  }
  if (a instanceof KM) {
    if (!(b instanceof KM) || a.size !== b.size) return false;
    for (const [k, v] of $entries(a)) {
      const e = $mfind(b, k);
      if (e === undefined || !$eq(v, e[1])) return false;
    }
    return true;
  }
  if (a.constructor !== b.constructor || !$structs[a.constructor.$name]) return false;
  for (const k of Object.keys(a)) if (!$eq(a[k], b[k])) return false;
  return true;
}
function $contains(l, x) { for (let i = 0; i < l.n; i++) if ($eq($d(l)[i], x)) return true; return false; }
function $index_of(l, x) { for (let i = 0; i < l.n; i++) if ($eq($d(l)[i], x)) return $Some(i); return $None; }

// ---------------------------------------------------------------- display (type-directed by descriptors)
// Descriptor kinds: i int, f float, b bool, u unit, s str, fn, x extern, l list (e), m map (k, v),
// st struct (n name, fs [[field, desc]]), tu tuple (like st), en enum (vs {variant: [[label or "", desc]]}).
function $quote(s) { return '"' + s.replaceAll("\\", "\\\\").replaceAll('"', '\\"').replaceAll("\n", "\\n") + '"'; }
function $show(v, t, nested) {
  switch (t.k) {
    case "i": return String(v);
    case "f": return $fmt_float(v);
    case "b": return v ? "true" : "false";
    case "u": return "()";
    case "s": return nested ? $quote(v) : v;
    case "fn": return "<fn>";
    case "x": return "<" + t.n + ">";
    case "l": {
      const out = [];
      const d = $d(v);
      for (let i = 0; i < v.n; i++) out.push($show(d[i], t.e, true));
      return "[" + out.join(", ") + "]";
    }
    case "m": {
      if (v.size === 0) return "[:]";
      return "[" + $entries(v).map(e => $show(e[0], t.kk, true) + ": " + $show(e[1], t.v, true)).join(", ") + "]";
    }
    case "tu":
      return "(" + t.fs.map(([name, ft]) => $show(v[name], ft, true)).join(", ") + ")";
    case "st":
      return t.n + "(" + t.fs.map(([name, ft]) => name + ": " + $show(v[name], ft, true)).join(", ") + ")";
    case "en": {
      const fs = t.vs[v.t];
      if (fs.length === 0) return v.t;
      return v.t + "(" + fs.map(([label, ft], i) => (label ? label + ": " : "") + $show(v.f[i], ft, true)).join(", ") + ")";
    }
  }
  return "<value>";
}

// ---------------------------------------------------------------- JavaScript interop (extern "js:...")
// Values are converted by the declared Klar type, like Python interop. Nothing JavaScript-shaped leaks into
// Klar: null/undefined arrive only as None (an Option), a thrown error only as Err (a Result), a Promise only
// through a `uses io` extern, and a value of the wrong shape stops the program with E407.
function $to_js(v, t) {
  switch (t.k) {
    case "l": return $arr(v).map(x => $to_js(x, t.e));
    case "m": { const o = {}; for (const [k, x] of $entries(v)) o[k] = $to_js(x, t.v); return o; }
    case "st": {
      const o = {};
      for (const [name, ft] of t.fs) { const x = $to_js(v[name], ft); if (x !== undefined) o[name] = x; }
      return o;
    }
    case "tu": return t.fs.map(([name, ft]) => $to_js(v[name], ft));
    case "en":
      if (t.n === "Option") return v.t === "Some" ? $to_js(v.f[0], t.vs.Some[0][1]) : undefined;
      return v.f.length ? { tag: v.t, values: v.f.map((x, i) => $to_js(x, t.vs[v.t][i][1])) } : v.t;
    case "fn": {
      if (!t.ps) return v;
      const what = "a Klar function called from JavaScript";
      if (t.io) return (...xs) => v(...t.ps.map((p, i) => $from_js(xs[i], p, what))).then(r => $to_js(r, t.r));
      return (...xs) => $to_js(v(...t.ps.map((p, i) => $from_js(xs[i], p, what))), t.r);
    }
    default: return v;
  }
}
const $js_kinds = { i: "int", f: "float", b: "bool", s: "str", l: "list", m: "map", st: "struct", en: "enum", fn: "function", x: "extern type" };
function $js_show(v) {
  if (v === undefined) return "undefined";
  if (typeof v === "function") return "a function";
  if (typeof v === "bigint") return String(v);
  try { const j = JSON.stringify(v); return j === undefined ? String(v) : (j.length > 80 ? j.slice(0, 77) + "..." : j); }
  catch (_) { return String(v); }
}
function $js_err(e) { return e instanceof Error ? (e.name && e.name !== "Error" ? e.name + ": " : "") + e.message : $js_show(e); }
function $js_sync(v, what, l, c) {
  if (v !== null && (typeof v === "object" || typeof v === "function") && typeof v.then === "function")
    $fail("E407", l, c, `${what} returned a Promise, so it does something asynchronous: declare it with \`uses io\``);
  return v;
}
function $from_js(v, t, what, l = 0, c = 0) {
  const bad = () => {
    const want = t.k === "st" || t.k === "en" || t.k === "x" ? `${$js_kinds[t.k]} ${t.n}` : $js_kinds[t.k] || t.k;
    const hint = v === null || v === undefined ? "; if it can be missing, declare it as Option[...]" : "";
    throw new KlarError("E407", l, c, `${what} returned ${$js_show(v)}, which is not ${/^[aeiou]/.test(want) ? "an" : "a"} ${want}${hint}`);
  };
  switch (t.k) {
    case "i": if (typeof v === "bigint") return $big(v, l, c); if (!Number.isInteger(v)) bad(); return $num2int(v);
    case "f": if (typeof v !== "number") bad(); return v;
    case "b": if (typeof v !== "boolean") bad(); return v;
    case "s": if (typeof v !== "string") bad(); return v;
    case "u": return undefined;
    case "l": if (!Array.isArray(v)) { if (v == null || typeof v[Symbol.iterator] !== "function" || typeof v === "string") bad(); v = [...v]; }
      return $L(v.map(x => $from_js(x, t.e, what, l, c)));
    case "m": {
      if (v instanceof Map) return $M([...v.entries()].map(([k, x]) => [k, $from_js(x, t.v, what, l, c)]));
      if (typeof v !== "object" || v === null) bad();
      return $M(Object.entries(v).map(([k, x]) => [k, $from_js(x, t.v, what, l, c)]));
    }
    case "st": {
      if (typeof v !== "object" || v === null) bad();
      const C = $structs[t.n];
      return new C(...t.fs.map(([name, ft]) => $from_js(v[name], ft, `${what} (field \`${name}\`)`, l, c)));
    }
    case "tu": {
      if (!Array.isArray(v) || v.length !== t.fs.length) bad();
      return new $structs[t.n](...t.fs.map(([name, ft], i) => $from_js(v[i], ft, what, l, c)));
    }
    case "en":
      if (t.n === "Option") return v === null || v === undefined ? $None : $Some($from_js(v, t.vs.Some[0][1], what, l, c));
      if (t.n === "Result") return $Ok($from_js(v, t.vs.Ok[0][1], what, l, c));
      if (typeof v === "string" && t.vs[v] && t.vs[v].length === 0) return new KE(v, []);
      if (v && t.vs[v.tag]) return new KE(v.tag, v.values.map((x, i) => $from_js(x, t.vs[v.tag][i][1], what, l, c)));
      bad();
    case "x": if (v === null || v === undefined) bad(); return v;
    case "fn": {
      if (typeof v !== "function") bad();
      if (!t.ps) return v;
      const call = (...xs) => v(...xs.map((x, i) => $to_js(x, t.ps[i])));
      if (t.io) return async (...xs) => $from_js(await call(...xs), t.r, what, l, c);
      return (...xs) => $from_js($js_sync(call(...xs), what, l, c), t.r, what, l, c);
    }
  }
  return v;
}

// ---------------------------------------------------------------- output
let $out = [], $out_len = 0;
function $print(s) {
  $out.push(s); $out_len += s.length;
  if ($out_len > 1 << 16) $flush();
}

// ---------------------------------------------------------------- built-ins taking functions
function $lgetopt(l, i) { return i >= 0 && i < l.n ? $Some($d(l)[i]) : $None; }
function $lmap(l, f) { const r = new Array(l.n); for (let i = 0; i < l.n; i++) r[i] = f($d(l)[i]); return new KL(r, l.n, 0); }
function $lfilter(l, f) { const r = []; for (let i = 0; i < l.n; i++) if (f($d(l)[i])) r.push($d(l)[i]); return $L(r); }
function $lany(l, f) { for (let i = 0; i < l.n; i++) if (f($d(l)[i])) return true; return false; }
function $lall(l, f) { for (let i = 0; i < l.n; i++) if (!f($d(l)[i])) return false; return true; }
function $lfind(l, f) { for (let i = 0; i < l.n; i++) if (f($d(l)[i])) return $Some($d(l)[i]); return $None; }
function $lfold(l, acc, f) { for (let i = 0; i < l.n; i++) acc = f(acc, $d(l)[i]); return acc; }
function $min(a, b) { return b < a ? b : a; }
function $max(a, b) { return b > a ? b : a; }
function $unwrap_or(o, d) { return o.t === "Some" || o.t === "Ok" ? o.f[0] : d; }
function $frem(a, b, l, c) { if (b === 0) $fail("E402", l, c, "remainder by zero"); return a % b; }
