import { readFileSync } from "node:fs";

type Node =
  | { k: "int"; v: number }
  | { k: "ref"; cell: string }
  | { k: "range"; f: string; a: string; b: string }
  | { k: "neg"; e: Node }
  | { k: "bin"; op: string; l: Node; r: Node };
type Val = number | string;

const CELL = /^[A-Z][1-9][0-9]?$/;

function parse(src: string): Node | null {
  const toks = src.match(/\s+|\d+|[A-Z][A-Z0-9]*|[+\-*/():]|./g) ?? [];
  const t = toks.filter(x => x.trim() !== "");
  let i = 0;
  const peek = () => t[i];
  const fail = (): never => { throw new Error("parse"); };
  const take = (want?: string) => {
    const x = t[i];
    if (x === undefined || (want !== undefined && x !== want)) fail();
    i++;
    return x;
  };
  const cell = () => { const x = take(); return CELL.test(x) ? x : fail(); };
  const expr = (): Node => {
    let n = term();
    while (peek() === "+" || peek() === "-") n = { k: "bin", op: take(), l: n, r: term() };
    return n;
  };
  const term = (): Node => {
    let n = unary();
    while (peek() === "*" || peek() === "/") n = { k: "bin", op: take(), l: n, r: unary() };
    return n;
  };
  const unary = (): Node => (peek() === "-" ? (take(), { k: "neg", e: unary() }) : primary());
  const primary = (): Node => {
    const x = peek();
    if (x === undefined) return fail();
    if (/^\d+$/.test(x)) { take(); return { k: "int", v: Number(x) }; }
    if (x === "SUM" || x === "MIN" || x === "MAX") {
      take(); take("("); const a = cell(); take(":"); const b = cell(); take(")");
      return { k: "range", f: x, a, b };
    }
    if (x === "(") { take(); const e = expr(); take(")"); return e; }
    return { k: "ref", cell: cell() };
  };
  try {
    const n = expr();
    return i === t.length ? n : null;
  } catch {
    return null;
  }
}

function rangeCells(a: string, b: string): string[] {
  const [c1, c2] = [a.charCodeAt(0), b.charCodeAt(0)].sort((x, y) => x - y);
  const [r1, r2] = [Number(a.slice(1)), Number(b.slice(1))].sort((x, y) => x - y);
  const out: string[] = [];
  for (let c = c1; c <= c2; c++) for (let r = r1; r <= r2; r++) out.push(String.fromCharCode(c) + r);
  return out;
}

function refs(n: Node): string[] {
  switch (n.k) {
    case "int": return [];
    case "ref": return [n.cell];
    case "range": return rangeCells(n.a, n.b);
    case "neg": return refs(n.e);
    case "bin": return [...refs(n.l), ...refs(n.r)];
  }
}

const defs = new Map<string, string>();
for (const line of readFileSync(0, "utf8").split("\n")) {
  if (line.trim() === "") continue;
  const at = line.indexOf(":");
  defs.set(line.slice(0, at).trim(), line.slice(at + 1).trim());
}
const parsed = new Map<string, Node | null>();
for (const [name, c] of defs) {
  parsed.set(name, c.startsWith("=") ? parse(c.slice(1)) : /^-?\d+$/.test(c) ? { k: "int", v: Number(c) } : null);
}
const deps = new Map<string, string[]>();
for (const [name, n] of parsed) deps.set(name, n ? refs(n) : []);

function inCycle(start: string): boolean {
  const seen = new Set<string>();
  const stack = [...deps.get(start)!];
  while (stack.length) {
    const n = stack.pop()!;
    if (n === start) return true;
    if (seen.has(n) || !deps.has(n)) continue;
    seen.add(n);
    stack.push(...deps.get(n)!);
  }
  return false;
}

const values = new Map<string, Val>();
for (const name of parsed.keys()) if (inCycle(name)) values.set(name, "#CYCLE");

function value(name: string): Val {
  if (!parsed.has(name)) return 0;
  if (!values.has(name)) {
    const n = parsed.get(name)!;
    values.set(name, n === null ? "#PARSE" : ev(n));
  }
  return values.get(name)!;
}

function ev(n: Node): Val {
  switch (n.k) {
    case "int": return n.v;
    case "ref": return value(n.cell);
    case "neg": { const v = ev(n.e); return typeof v === "string" ? v : -v; }
    case "range": {
      const vs: number[] = [];
      for (const c of rangeCells(n.a, n.b)) {
        const v = value(c);
        if (typeof v === "string") return v;
        vs.push(v);
      }
      return n.f === "SUM" ? vs.reduce((x, y) => x + y, 0) : n.f === "MIN" ? Math.min(...vs) : Math.max(...vs);
    }
    case "bin": {
      const a = ev(n.l);
      if (typeof a === "string") return a;
      const b = ev(n.r);
      if (typeof b === "string") return b;
      if (n.op === "+") return a + b;
      if (n.op === "-") return a - b;
      if (n.op === "*") return a * b;
      if (b === 0) return "#DIV0";
      return Math.trunc(a / b);
    }
  }
}

const names = [...parsed.keys()].sort((x, y) => x[0] === y[0] ? Number(x.slice(1)) - Number(y.slice(1)) : x < y ? -1 : 1);
for (const n of names) {
  const v = value(n);
  console.log(`${n} = ${typeof v === "number" ? (Object.is(v, -0) ? 0 : v) : v}`);
}
