// Klar runtime: standard-library functions shared by the Node and browser targets (raw Klar values).

function $json_to_klar(v) {
  if (v === null) return new KE("JNull", []);
  if (typeof v === "boolean") return new KE("JBool", [v]);
  if (typeof v === "number") return new KE("JNum", [v]);
  if (typeof v === "string") return new KE("JStr", [v]);
  if (Array.isArray(v)) return new KE("JArr", [$L(v.map($json_to_klar))]);
  return new KE("JObj", [$M(Object.entries(v).map(([k, x]) => [k, $json_to_klar(x)]))]);
}
function $json_from_klar(v) {
  switch (v.t) {
    case "JNull": return null;
    case "JArr": return $arr(v.f[0]).map($json_from_klar);
    case "JObj": { const o = {}; for (const [k, x] of $entries(v.f[0])) o[k] = $json_from_klar(x); return o; }
    default: return v.f[0];
  }
}
// ---------------------------------------------------------------- automatic JSON (same rules as klar/jsonconv.py)
// Encoding builds plain JS values with objects as $JO (an ordered entry list, so keys keep their order) and
// big ints as BigInt; $json_write prints them exactly like the interpreter does.
class $JO { constructor(e) { this.e = e; } }
class $JsonErr extends Error {}
function $json_enc(v, t) {
  switch (t.k) {
    case "i": case "s": case "b": return v;
    case "f": return Number.isFinite(v) ? v : null;
    case "u": return null;
    case "l": return $arr(v).map(x => $json_enc(x, t.e));
    case "m":
      if (t.kk.k === "s") return new $JO($entries(v).map(([k, x]) => [k, $json_enc(x, t.v)]));
      return $entries(v).map(([k, x]) => [$json_enc(k, t.kk), $json_enc(x, t.v)]);
    case "st": return new $JO(t.fs.map(([name, ft]) => [name, $json_enc(v[name], ft)]));
    case "en": {
      if (t.n === "Json") return $json_from_klar_o(v);
      if (t.n === "Option") return v.t === "None" ? null : $json_enc(v.f[0], t.vs.Some[0][1]);
      if (t.n === "Result") return new $JO([[v.t === "Ok" ? "ok" : "err", $json_enc(v.f[0], t.vs[v.t][0][1])]]);
      if (!v.f.length) return v.t;
      return new $JO([["type", v.t], ...t.vs[v.t].map(([label, ft], i) => [label, $json_enc(v.f[i], ft)])]);
    }
    default: throw new $JsonErr(`a ${t.k === "fn" ? "function" : t.n || t.k} value cannot be turned into JSON`);
  }
}
function $json_from_klar_o(v) {
  switch (v.t) {
    case "JNull": return null;
    case "JArr": return $arr(v.f[0]).map($json_from_klar_o);
    case "JObj": return new $JO($entries(v.f[0]).map(([k, x]) => [k, $json_from_klar_o(x)]));
    default: return v.f[0];
  }
}
function $json_num(x) {
  if (typeof x === "bigint") return x.toString();
  if (!Number.isFinite(x)) return "null";
  return String(x);
}
function $json_write(x, ind, cur) {
  if (x === null || x === undefined) return "null";
  if (typeof x === "number" || typeof x === "bigint") return $json_num(x);
  if (typeof x === "string" || typeof x === "boolean") return JSON.stringify(x);
  const items = Array.isArray(x) ? x.map(i => $json_write(i, ind, cur + ind))
    : x.e.map(([k, i]) => JSON.stringify(k) + (ind ? ": " : ":") + $json_write(i, ind, cur + ind));
  const [open, close] = Array.isArray(x) ? ["[", "]"] : ["{", "}"];
  if (!items.length) return open + close;
  if (!ind) return open + items.join(",") + close;
  return open + "\n" + cur + ind + items.join(",\n" + cur + ind) + "\n" + cur + close;
}
function $json_parse_raw(s) {
  return JSON.parse(s, function (k, v, c) {
    return typeof v === "number" && c && !Number.isSafeInteger(v) && /^-?\d+$/.test(c.source) ? BigInt(c.source) : v;
  });
}
function $json_kind(x) {
  if (x === null) return "null";
  if (typeof x === "boolean") return "a bool";
  if (typeof x === "number" || typeof x === "bigint") return $json_num(x);
  if (typeof x === "string") return "a string";
  return Array.isArray(x) ? "an array" : "an object";
}
const $isobj = x => typeof x === "object" && x !== null && !Array.isArray(x);
const $has = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
function $json_dec(x, t, path) {
  const bad = want => { throw new $JsonErr(`expected ${want} at ${path}, found ${$json_kind(x)}`); };
  switch (t.k) {
    case "i":
      if (typeof x === "bigint") {
        if (x > $I64_MAX || x < $I64_MIN) throw new $JsonErr(`number at ${path} does not fit in a 64-bit int`);
        return $big(x, 0, 0);
      }
      if (typeof x !== "number" || !Number.isInteger(x)) bad("a whole number");
      return $num2int(x);
    case "f": if (typeof x === "bigint") return Number(x); if (typeof x !== "number") bad("a number"); return x;
    case "s": if (typeof x !== "string") bad("a string"); return x;
    case "b": if (typeof x !== "boolean") bad("true or false"); return x;
    case "u": if (x !== null) bad("null"); return undefined;
    case "l": if (!Array.isArray(x)) bad("an array"); return $L(x.map((i, n) => $json_dec(i, t.e, `${path}[${n}]`)));
    case "m":
      if (t.kk.k === "s") {
        if (!$isobj(x)) bad("an object");
        return $M(Object.entries(x).map(([k, i]) => [k, $json_dec(i, t.v, `${path}.${k}`)]));
      }
      if (!Array.isArray(x)) bad("an array of [key, value] pairs");
      return $M(x.map((p, n) => {
        if (!Array.isArray(p) || p.length !== 2)
          throw new $JsonErr(`expected a [key, value] pair at ${path}[${n}], found ${$json_kind(p)}`);
        return [$json_dec(p[0], t.kk, `${path}[${n}][0]`), $json_dec(p[1], t.v, `${path}[${n}][1]`)];
      }));
    case "st": {
      if (!$isobj(x)) bad(`an object (${t.n})`);
      const C = $structs[t.n];
      return new C(...t.fs.map(([name, ft]) => {
        if ($has(x, name)) return $json_dec(x[name], ft, `${path}.${name}`);
        if (ft.k === "en" && ft.n === "Option") return $None;
        throw new $JsonErr(`missing field \`${name}\` at ${path} (${t.n})`);
      }));
    }
    case "en": {
      if (t.n === "Json") return $json_to_klar_big(x);
      if (t.n === "Option") return x === null ? $None : $Some($json_dec(x, t.vs.Some[0][1], path));
      if (t.n === "Result") {
        if ($isobj(x) && Object.keys(x).length === 1 && ($has(x, "ok") || $has(x, "err")))
          return $has(x, "ok") ? $Ok($json_dec(x.ok, t.vs.Ok[0][1], `${path}.ok`)) : $Err($json_dec(x.err, t.vs.Err[0][1], `${path}.err`));
        bad('{"ok": ...} or {"err": ...}');
      }
      let tag, obj = null;
      if (typeof x === "string") tag = x;
      else if ($isobj(x) && typeof x.type === "string") { tag = x.type; obj = x; }
      else bad(`a ${t.n} variant name or an object with a "type" key`);
      if (!$has(t.vs, tag)) throw new $JsonErr(`\`${tag}\` at ${path} is not a variant of ${t.n}`);
      const fs = t.vs[tag];
      if (!fs.length) return new KE(tag, []);
      if (obj === null) throw new $JsonErr(`variant ${tag} at ${path} has fields, so it must be an object with a "type" key`);
      return new KE(tag, fs.map(([label, ft]) => {
        if ($has(obj, label)) return $json_dec(obj[label], ft, `${path}.${label}`);
        if (ft.k === "en" && ft.n === "Option") return $None;
        throw new $JsonErr(`missing field \`${label}\` at ${path} (${tag})`);
      }));
    }
    default: throw new $JsonErr(`values of type ${t.n || t.k} cannot be read from JSON`);
  }
}
function $json_to_klar_big(v) {
  if (typeof v === "bigint") return new KE("JNum", [Number(v)]);
  if (v === null) return new KE("JNull", []);
  if (typeof v === "boolean") return new KE("JBool", [v]);
  if (typeof v === "number") return new KE("JNum", [v]);
  if (typeof v === "string") return new KE("JStr", [v]);
  if (Array.isArray(v)) return new KE("JArr", [$L(v.map($json_to_klar_big))]);
  return new KE("JObj", [$M(Object.entries(v).map(([k, x]) => [k, $json_to_klar_big(x)]))]);
}
function $json_fail(op, e, line, col) {
  if (e instanceof $JsonErr) $fail("E407", line, col, `json_${op}: ${e.message}`);
  throw e;
}
const $json = {
  encode: (v, t, l, c) => { try { return $json_write($json_enc(v, t), "", ""); } catch (e) { $json_fail("encode", e, l, c); } },
  pretty: (v, t, l, c) => { try { return $json_write($json_enc(v, t), "  ", ""); } catch (e) { $json_fail("encode_pretty", e, l, c); } },
  of: (v, t, l, c) => { try { return $json_to_klar_big($json_parse_raw($json_write($json_enc(v, t), "", ""))); } catch (e) { $json_fail("of", e, l, c); } },
  decode: (s, t) => {
    let x;
    try { x = $json_parse_raw(s); } catch (e) { return $Err("invalid JSON: " + e.message); }
    try { return $Ok($json_dec(x, t, "$")); } catch (e) { if (e instanceof $JsonErr) return $Err(e.message); throw e; }
  },
  into: (j, t) => {
    try { return $Ok($json_dec($json_parse_raw($json_write($json_from_klar_o(j), "", "")), t, "$")); }
    catch (e) { if (e instanceof $JsonErr) return $Err(e.message); throw e; }
  },
};
let $rng_state = (Date.now() ^ (Math.random() * 0x100000000)) >>> 0;
function $rng() {                       // mulberry32
  let t = ($rng_state = ($rng_state + 0x6D2B79F5) >>> 0);
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}
// Python regex syntax -> JavaScript: named groups, and Unicode \w \d (JavaScript's are ASCII-only even with /u)
function $py_regex(p) {
  p = p.replace(/\(\?P</g, "(?<").replace(/\(\?P=(\w+)\)/g, "\\k<$1>");
  const W = "\\p{L}\\p{N}\\p{M}_", D = "\\p{Nd}";
  let out = "", cls = false;
  for (let i = 0; i < p.length; i++) {
    const c = p[i];
    if (c === "\\" && i + 1 < p.length) {
      const n = p[++i];
      if (n === "w") out += cls ? W : `[${W}]`;
      else if (n === "W") out += cls ? "\\W" : `[^${W}]`;
      else if (n === "d") out += cls ? D : `[${D}]`;
      else if (n === "D") out += cls ? "\\D" : `[^${D}]`;
      else out += c + n;
      continue;
    }
    if (c === "[" && !cls) cls = true;
    else if (c === "]" && cls) cls = false;
    out += c;
  }
  return out;
}
function $re_split(p, s) {
  const out = []; let last = 0;
  for (const m of s.matchAll(new RegExp($py_regex(p), "gu"))) {
    out.push(s.slice(last, m.index), ...m.slice(1).map(g => g ?? ""));
    last = m.index + m[0].length;
  }
  out.push(s.slice(last));
  return $L(out);
}
function $py_repl(r) { return r.replace(/\$/g, "$$$$").replace(/\\(\d+)/g, "$$$1").replace(/\\g<(\w+)>/g, "$$<$1>"); }
function $strftime(d, pat) {
  const p2 = n => String(n).padStart(2, "0");
  const days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  const months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const wd = (d.getUTCDay() + 6) % 7;
  return pat.replace(/%([a-zA-Z%])/g, (_, c) => ({
    Y: String(d.getUTCFullYear()), m: p2(d.getUTCMonth() + 1), d: p2(d.getUTCDate()), H: p2(d.getUTCHours()),
    M: p2(d.getUTCMinutes()), S: p2(d.getUTCSeconds()), y: p2(d.getUTCFullYear() % 100), A: days[wd], a: days[wd].slice(0, 3),
    B: months[d.getUTCMonth()], b: months[d.getUTCMonth()].slice(0, 3), "%": "%",
    f: String(d.getUTCMilliseconds() * 1000).padStart(6, "0"),
  })[c] ?? "%" + c);
}
function $iso(ts) {
  const d = new Date(ts * 1000);
  const ms = Math.round((ts - Math.floor(ts)) * 1e6);
  const base = d.toISOString().slice(0, 19);
  return base + (ms ? "." + String(ms).padStart(6, "0") : "") + "+00:00";
}
const $std = {
  "klar.stdpy.jsonlib.parse": s => { try { return $Ok($json_to_klar(JSON.parse(s))); } catch (e) { return $Err("JSONDecodeError: " + e.message); } },
  "klar.stdpy.jsonlib.stringify": v => JSON.stringify($json_from_klar(v)),
  "klar.stdpy.jsonlib.pretty": v => JSON.stringify($json_from_klar(v), null, 2),

  "klar.stdpy.mathlib.pi": () => Math.PI,
  "klar.stdpy.mathlib.sin": Math.sin, "klar.stdpy.mathlib.cos": Math.cos, "klar.stdpy.mathlib.tan": Math.tan,
  "klar.stdpy.mathlib.atan2": Math.atan2, "klar.stdpy.mathlib.exp": Math.exp, "klar.stdpy.mathlib.log": Math.log,
  "klar.stdpy.mathlib.log10": Math.log10, "klar.stdpy.mathlib.pow": Math.pow,

  "klar.stdpy.randomlib.seed": n => { $rng_state = n >>> 0; },
  "klar.stdpy.randomlib.int_between": (lo, hi) => lo + Math.floor($rng() * (hi - lo + 1)),
  "klar.stdpy.randomlib.unit_float": () => $rng(),

  "klar.stdpy.regexlib.matches": (p, s) => new RegExp($py_regex(p), "u").test(s),
  "klar.stdpy.regexlib.find_all": (p, s) => $L([...s.matchAll(new RegExp($py_regex(p), "gu"))].map(m => m[0])),
  "klar.stdpy.regexlib.groups": (p, s) => { const m = new RegExp($py_regex(p), "u").exec(s); return m ? $Some($L(m.slice(1).map(g => g ?? ""))) : $None; },
  "klar.stdpy.regexlib.replace": (p, s, r) => s.replace(new RegExp($py_regex(p), "gu"), $py_repl(r)),
  "klar.stdpy.regexlib.split": $re_split,

  "klar.stdpy.timelib.now": () => Date.now() / 1000,
  "klar.stdpy.timelib.millis": () => Math.floor(performance.now()),
  "klar.stdpy.timelib.sleep": s => { $flush(); return new Promise(r => setTimeout(r, Math.max(0, s) * 1000)); },
  "klar.stdpy.timelib.format_utc": (ts, pat) => $strftime(new Date(ts * 1000), pat),
  "klar.stdpy.timelib.iso_utc": $iso,

  "klar:web.form_decode": s => $M([...new URLSearchParams(s)]),
  "klar:web.url_encode": s => encodeURIComponent(s).replace(/[!'()*]/g, c => "%" + c.charCodeAt(0).toString(16).toUpperCase()),
  "klar:web.url_decode": s => { try { return decodeURIComponent(s.replaceAll("+", " ")); } catch { return s; } },
  "klar:web.mime_type": p => $mime(p),
};

const $MIME = { html: "text/html", htm: "text/html", css: "text/css", js: "text/javascript", mjs: "text/javascript",
  json: "application/json", txt: "text/plain", md: "text/markdown", svg: "image/svg+xml", png: "image/png", jpg: "image/jpeg",
  jpeg: "image/jpeg", gif: "image/gif", webp: "image/webp", ico: "image/vnd.microsoft.icon", woff: "font/woff",
  woff2: "font/woff2", pdf: "application/pdf", wasm: "application/wasm", xml: "application/xml", csv: "text/csv" };
function $mime(p) {
  const t = $MIME[(p.split(".").pop() || "").toLowerCase()] || "application/octet-stream";
  return t.startsWith("text/") || t === "application/json" || t === "image/svg+xml" ? t + "; charset=utf-8" : t;
}

// ---------------------------------------------------------------- std.task: tasks are promises, channels are queues
const $task = {
  ids: 0, chans: new Map(), groups: new Map(), waiting: 0, where: null,
  async all(jobs, l, c) { return $L(await Promise.all($arr(jobs).map(f => Promise.resolve().then(() => f())))); },
  async wait(p, what, l, c) {
    this.waiting++; this.where = [what, l, c];
    try { return await p; } finally { this.waiting--; }
  },
  async group(body, l, c) {
    const id = ++this.ids, g = { pending: new Set(), open: true };
    this.groups.set(id, g);
    try {
      await body(new $structs.TaskGroup(id));
      while (g.pending.size) await this.wait(Promise.all([...g.pending]), "task_group", l, c);
    } finally { g.open = false; }
  },
  async spawn(gv, job, l, c) {
    const g = this.groups.get(gv.id);
    if (!g || !g.open) $fail("E407", l, c, "task_spawn on a group that has already finished",
      "spawn tasks inside the task_group body (or inside tasks of that group)");
    const p = Promise.resolve().then(() => job());
    g.pending.add(p);
    p.then(() => g.pending.delete(p), () => {});
  },
  async new_(l, c) { const id = ++this.ids; this.chans.set(id, { q: [], closed: false, waiters: [] }); return new $structs.Chan(id); },
  chan(cv, l, c) { const ch = this.chans.get(cv.id); if (!ch) $fail("E407", l, c, "not a channel made by chan_new"); return ch; },
  async send(cv, v, l, c) {
    const ch = this.chan(cv, l, c);
    if (ch.closed) $fail("E407", l, c, "chan_send on a closed channel", "close a channel only when nothing will send on it again");
    if (ch.waiters.length) ch.waiters.shift()($Some(v)); else ch.q.push(v);
  },
  async recv(cv, l, c) {
    const ch = this.chan(cv, l, c);
    if (ch.q.length) return $Some(ch.q.shift());
    if (ch.closed) return $None;
    return this.wait(new Promise(r => ch.waiters.push(r)), "chan_recv", l, c);
  },
  async close(cv, l, c) {
    const ch = this.chan(cv, l, c);
    ch.closed = true;
    for (const w of ch.waiters.splice(0)) w($None);
  },
};
