// ---------------------------------------------------------------- WebAssembly host (klar_wasm.js)
// Runs Klar code compiled to WebAssembly (klar/runtime/wasm/klar_wasm.h) in browsers and Node. The module imports
// a few host functions from "klar" (output, float formatting and parsing, math, clocks) and exports klar_reset,
// klar_str_*/klar_list_* helpers and one function per exported Klar function. Every call starts from an empty
// heap: arguments are written into linear memory after klar_reset, the result is copied out before the next call.
class $WasmExit extends Error { constructor(code) { super("exit " + code); this.code = code; } }
const $wasm_enc = new TextEncoder(), $wasm_dec = new TextDecoder();
const $wasm_node = typeof process !== "undefined" && process.versions && process.versions.node;
const $wasm_fs = $wasm_node && process.getBuiltinModule ? process.getBuiltinModule("fs") : null;
function $wasm_fmt_float(x, conv, p, alt) {
  const c = String.fromCharCode(conv).toLowerCase(), up = conv < 97;
  let s;
  const ex = t => t.replace(/e([+-])(\d)$/, "e$10$2");
  if (Number.isNaN(x)) s = "nan";
  else if (!Number.isFinite(x)) s = "inf";
  else if (c === "e") s = ex(x.toExponential(Math.min(p, 100)));
  else if (c === "f") s = x >= 1e21 ? BigInt(x).toString() + (p ? "." + "0".repeat(p) : "") : x.toFixed(Math.min(p, 100));
  else {
    const P = p === 0 ? 1 : Math.min(p, 100);
    const X = x === 0 ? 0 : Number(x.toExponential(P - 1).split("e")[1]);
    s = P > X && X >= -4 ? x.toFixed(P - 1 - X) : ex(x.toExponential(P - 1));
    if (!alt && s.includes(".")) s = s.replace(/\.?0+(?=e|$)/, "");
  }
  if (alt && !s.includes(".") && /^\d+$/.test(s)) s += ".";
  return up ? s.toUpperCase() : s;
}
const $wasm_num = /^[ \t\n\r\f\v]*[+-]?(?:inf(?:inity)?|nan|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)/i;
async function $wasm_load(bytes, opts) {
  const w = { err: "", part: "", out: [], echo: !!(opts && opts.echo), mem: null, ex: null, sp: null };
  const u8 = () => new Uint8Array(w.mem.buffer);
  const text = (p, n) => $wasm_dec.decode(u8().subarray(p, p + n));
  const line = (fd, s) => {
    // inside a JavaScript-backend app: through its own print, so output stays in order
    if (fd === 1 && !w.echo && typeof $print === "function") {
      const ls = (w.part + s).split("\n");
      w.part = ls.pop();
      for (const l of ls) $print(l);
      return;
    }
    // browsers: complete lines to the console; Node: straight to the file descriptor
    if ($wasm_fs) { $wasm_fs.writeSync(fd, s); return; }
    const buf = fd === 1 ? w.out : (w.errbuf = w.errbuf || []);
    buf.push(s);
    const all = buf.join("");
    const lines = all.split("\n");
    buf.length = 0;
    if (lines[lines.length - 1]) buf.push(lines[lines.length - 1]);
    for (const l of lines.slice(0, -1)) (fd === 1 ? console.log : console.error)(l);
  };
  const imports = { klar: {
    write(fd, p, n) {
      const s = text(p, n);
      if (fd === 2) { w.err += s; if (w.echo) line(2, s); } else line(fd, s);
    },
    read(fd, p, cap) {
      if (!$wasm_fs) return 0;
      try { return $wasm_fs.readSync(fd, u8(), p, cap, null); } catch (e) { return 0; }
    },
    exit(code) { throw new $WasmExit(code); },
    fmt_float(x, conv, prec, out, cap) {
      const b = $wasm_enc.encode($wasm_fmt_float(x, conv & 0xff, prec, conv & 0x100));
      const n = Math.min(b.length, cap - 1);
      u8().set(b.subarray(0, n), out);
      return n;
    },
    strtod(p, n, used) {
      const m = $wasm_num.exec(text(p, n));
      new Int32Array(w.mem.buffer, used, 1)[0] = m ? m[0].length : 0;
      if (!m) return 0;
      const t = m[0].trim().toLowerCase().replace(/^\+/, "");
      if (/inf/.test(t)) return t[0] === "-" ? -Infinity : Infinity;
      if (/nan/.test(t)) return NaN;
      return Number(t);
    },
    arg(i, out, cap) {
      const argv = w.echo && $wasm_node ? process.argv.slice(2) : [];
      if (i >= argv.length) return -1;
      const b = $wasm_enc.encode(argv[i]);
      if (out && b.length < cap) u8().set(b, out);
      return b.length;
    },
    now: () => Date.now() / 1000,
    clock_ms: () => (typeof performance !== "undefined" ? performance.now() : Date.now()),
    seed: () => (Math.random() * 4294967296) >>> 0,
    sin: Math.sin, cos: Math.cos, tan: Math.tan, asin: Math.asin, acos: Math.acos, atan: Math.atan, atan2: Math.atan2,
    exp: Math.exp, log: Math.log, log10: Math.log10, log2: Math.log2, pow: Math.pow, hypot: Math.hypot,
    fmod: (a, b) => a % b,
  } };
  const { instance: inst } = await WebAssembly.instantiate(bytes, imports);
  w.ex = inst.exports; w.mem = inst.exports.memory; w.sp = inst.exports.__stack_pointer;
  w.ex.klar_init();
  return w;
}
function $wasm_flush(w) {
  if (w.part) { $print(w.part); w.part = ""; }
  if (w.out.length) { console.log(w.out.join("")); w.out.length = 0; }
}
// Run one exported function; a Klar runtime error inside it comes back as the same KlarError the JavaScript
// backend throws (code, line, column of the WebAssembly module's source).
function $wasm_run(w, f) {
  const sp = w.sp.value;
  w.err = "";
  try { return f(); }
  catch (e) {
    w.sp.value = sp;
    const mk = typeof KlarError !== "undefined" ? (c, l, k, m) => new KlarError(c, l, k, m) : (c, l, k, m) => new Error(`error[${c}] ${m}`);
    if (e instanceof $WasmExit) {
      const m = /error\[(E\d+)\] ([^\n]*?):(\d+):(\d+): ([\s\S]*?)\n?$/.exec(w.err);
      if (m) throw mk(m[1], +m[3], +m[4], `${m[5]} (at ${m[2].split("/").pop()}:${m[3]}:${m[4]}, in WebAssembly)`);
      throw mk("E407", 0, 0, w.err.trim() || `WebAssembly module exited with status ${e.code}`);
    }
    if (e instanceof RangeError || (e instanceof WebAssembly.RuntimeError && /out of bounds|call stack/.test(e.message)))
      throw mk("E406", 0, 0, `stack overflow (recursion too deep) or invalid memory access in WebAssembly (${e.message})`);
    throw e;
  }
}
const $wasm_i64 = x => (x >= -9007199254740991n && x <= 9007199254740991n ? Number(x) : x);
// Values in: JavaScript-backend Klar values -> wasm arguments. kinds: i64 bool unit f64 str ints floats json.
function $wasm_arg(w, v, kind, d) {
  const ex = w.ex;
  switch (kind) {
    case "i64": return BigInt(v);
    case "bool": return v ? 1n : 0n;
    case "unit": return 0n;
    case "f64": return v;
    case "ints": case "floats": {
      const a = $arr(v), n = a.length, l = ex.klar_list_new(n), p = ex.klar_list_data(l);
      if (kind === "floats") new Float64Array(w.mem.buffer, p, n).set(a);
      else { const t = new BigInt64Array(w.mem.buffer, p, n); for (let i = 0; i < n; i++) t[i] = BigInt(a[i]); }
      return l;
    }
    default: {
      const b = $wasm_enc.encode(kind === "json" ? $json_write($json_enc(v, d), "", "") : v);
      const s = ex.klar_str_new(b.length);
      new Uint8Array(w.mem.buffer, ex.klar_str_data(s), b.length).set(b);
      return s;
    }
  }
}
function $wasm_ret(w, r, kind, d) {
  const ex = w.ex;
  switch (kind) {
    case "i64": return $wasm_i64(r);
    case "bool": return r !== 0n;
    case "unit": return undefined;
    case "f64": return r;
    case "ints": case "floats": {
      const n = ex.klar_list_len(r), p = ex.klar_list_data(r);
      if (kind === "floats") return $L(Array.from(new Float64Array(w.mem.buffer, p, n)));
      const t = new BigInt64Array(w.mem.buffer, p, n), a = new Array(n);
      for (let i = 0; i < n; i++) a[i] = $wasm_i64(t[i]);
      return $L(a);
    }
    default: {
      const s = $wasm_dec.decode(new Uint8Array(w.mem.buffer, ex.klar_str_data(r), ex.klar_str_len(r)));
      if (kind === "str") return s;
      return $json_dec($json_parse_raw(s), d, "$");
    }
  }
}
function $wasm_call(w, name, kinds, descs, rkind, rdesc, args) {
  return $wasm_run(w, () => {
    w.ex.klar_reset();
    const xs = args.map((v, i) => $wasm_arg(w, v, kinds[i], descs[i]));
    const r = $wasm_ret(w, w.ex[name](...xs), rkind, rdesc);
    $wasm_flush(w);
    return r;
  });
}
function $wasm_b64(s) {
  const b = atob(s), u = new Uint8Array(b.length);
  for (let i = 0; i < b.length; i++) u[i] = b.charCodeAt(i);
  return u;
}
