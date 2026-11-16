import { readFileSync } from "node:fs";

type Node =
  | { k: "num"; v: number }
  | { k: "cell"; c: number }
  | { k: "neg"; e: Node }
  | { k: "bin"; op: string; l: Node; r: Node }
  | { k: "fn"; f: string; cells: number[] };

type Value = number | string; // string = error code

const NROWS = 99;
const cellIndex = (col: number, row: number): number => col * NROWS + (row - 1);
const cellName = (i: number): string =>
  String.fromCharCode(65 + Math.floor(i / NROWS)) + String((i % NROWS) + 1);

function parseCellName(s: string): number | null {
  const m = /^([A-Z])([1-9][0-9]?)$/.exec(s);
  if (!m) return null;
  return cellIndex(m[1]!.charCodeAt(0) - 65, Number(m[2]));
}

type Tok = { t: "int" | "cell" | "func" | "sym"; s: string };

class ParseError extends Error {}

function tokenize(src: string): Tok[] {
  const toks: Tok[] = [];
  let i = 0;
  while (i < src.length) {
    const ch = src[i]!;
    if (ch === " " || ch === "\t") {
      i++;
      continue;
    }
    if ("+-*/():".includes(ch)) {
      toks.push({ t: "sym", s: ch });
      i++;
      continue;
    }
    if (/[A-Za-z0-9]/.test(ch)) {
      let j = i;
      while (j < src.length && /[A-Za-z0-9]/.test(src[j]!)) j++;
      const w = src.slice(i, j);
      i = j;
      if (/^[0-9]+$/.test(w)) toks.push({ t: "int", s: w });
      else if (w === "SUM" || w === "MIN" || w === "MAX") toks.push({ t: "func", s: w });
      else if (parseCellName(w) !== null) toks.push({ t: "cell", s: w });
      else throw new ParseError();
      continue;
    }
    throw new ParseError();
  }
  return toks;
}

function parseFormula(src: string, deps: number[]): Node {
  const toks = tokenize(src);
  let p = 0;
  const peek = (): Tok | undefined => toks[p];
  const isSym = (s: string): boolean => {
    const t = toks[p];
    return t !== undefined && t.t === "sym" && t.s === s;
  };
  const expectSym = (s: string): void => {
    if (!isSym(s)) throw new ParseError();
    p++;
  };
  const expectCell = (): number => {
    const t = toks[p];
    if (!t || t.t !== "cell") throw new ParseError();
    p++;
    const c = parseCellName(t.s)!;
    return c;
  };

  function expr(): Node {
    let l = term();
    while (isSym("+") || isSym("-")) {
      const op = toks[p++]!.s;
      l = { k: "bin", op, l, r: term() };
    }
    return l;
  }
  function term(): Node {
    let l = unary();
    while (isSym("*") || isSym("/")) {
      const op = toks[p++]!.s;
      l = { k: "bin", op, l, r: unary() };
    }
    return l;
  }
  function unary(): Node {
    if (isSym("-")) {
      p++;
      return { k: "neg", e: unary() };
    }
    return primary();
  }
  function primary(): Node {
    const t = peek();
    if (!t) throw new ParseError();
    if (t.t === "int") {
      p++;
      return { k: "num", v: Number(t.s) };
    }
    if (t.t === "cell") {
      p++;
      const c = parseCellName(t.s)!;
      deps.push(c);
      return { k: "cell", c };
    }
    if (t.t === "func") {
      p++;
      expectSym("(");
      const a = expectCell();
      expectSym(":");
      const b = expectCell();
      expectSym(")");
      const c1 = Math.floor(a / NROWS), r1 = a % NROWS;
      const c2 = Math.floor(b / NROWS), r2 = b % NROWS;
      const cells: number[] = [];
      for (let c = Math.min(c1, c2); c <= Math.max(c1, c2); c++)
        for (let r = Math.min(r1, r2); r <= Math.max(r1, r2); r++) cells.push(c * NROWS + r);
      for (const c of cells) deps.push(c);
      return { k: "fn", f: t.s, cells };
    }
    if (isSym("(")) {
      p++;
      const e = expr();
      expectSym(")");
      return e;
    }
    throw new ParseError();
  }

  const e = expr();
  if (p !== toks.length) throw new ParseError();
  return e;
}

// ---- read input ----
const input = readFileSync(0, "utf8");
const content = new Map<number, string>();
for (const rawLine of input.split("\n")) {
  const line = rawLine.replace(/\r$/, "");
  if (line.trim() === "") continue;
  const ci = line.indexOf(":");
  if (ci < 0) continue;
  const name = line.slice(0, ci).trim();
  const idx = parseCellName(name);
  if (idx === null) continue;
  content.set(idx, line.slice(ci + 1).trim());
}

const N = 26 * NROWS;
const defined: boolean[] = new Array(N).fill(false);
const ast: (Node | null)[] = new Array(N).fill(null);
const deps: number[][] = Array.from({ length: N }, () => []);
const values: (Value | undefined)[] = new Array(N).fill(undefined);

for (const [idx, c] of content) {
  defined[idx] = true;
  if (c.startsWith("=")) {
    const d: number[] = [];
    try {
      ast[idx] = parseFormula(c.slice(1), d);
      deps[idx] = d.filter((x) => content.has(x));
    } catch (e) {
      if (!(e instanceof ParseError)) throw e;
      values[idx] = "#PARSE";
    }
  } else if (/^-?[0-9]+$/.test(c)) {
    values[idx] = Number(c);
  } else {
    values[idx] = "#PARSE";
  }
}

// ---- Tarjan SCC (iterative); SCCs emerge in reverse topological order (deps first) ----
const index: number[] = new Array(N).fill(-1);
const low: number[] = new Array(N).fill(0);
const onStack: boolean[] = new Array(N).fill(false);
const stack: number[] = [];
const order: number[][] = [];
let counter = 0;

for (let s = 0; s < N; s++) {
  if (!defined[s] || index[s] !== -1) continue;
  const work: [number, number][] = [[s, 0]];
  index[s] = low[s] = counter++;
  stack.push(s);
  onStack[s] = true;
  while (work.length) {
    const top = work[work.length - 1]!;
    const v = top[0];
    const ds = deps[v]!;
    if (top[1] < ds.length) {
      const w = ds[top[1]++]!;
      if (index[w] === -1) {
        index[w] = low[w] = counter++;
        stack.push(w);
        onStack[w] = true;
        work.push([w, 0]);
      } else if (onStack[w]) {
        low[v] = Math.min(low[v]!, index[w]!);
      }
    } else {
      work.pop();
      if (work.length) {
        const u = work[work.length - 1]![0];
        low[u] = Math.min(low[u]!, low[v]!);
      }
      if (low[v] === index[v]) {
        const comp: number[] = [];
        let w: number;
        do {
          w = stack.pop()!;
          onStack[w] = false;
          comp.push(w);
        } while (w !== v);
        order.push(comp);
      }
    }
  }
}

function valueOf(c: number): Value {
  if (!defined[c]) return 0;
  return values[c]!;
}

function evalNode(n: Node): Value {
  switch (n.k) {
    case "num":
      return n.v;
    case "cell":
      return valueOf(n.c);
    case "neg": {
      const v = evalNode(n.e);
      return typeof v === "string" ? v : -v;
    }
    case "bin": {
      const a = evalNode(n.l);
      if (typeof a === "string") return a;
      const b = evalNode(n.r);
      if (typeof b === "string") return b;
      switch (n.op) {
        case "+":
          return a + b;
        case "-":
          return a - b;
        case "*":
          return a * b;
        default:
          if (b === 0) return "#DIV0";
          return Math.trunc(a / b);
      }
    }
    case "fn": {
      let acc = n.f === "SUM" ? 0 : n.f === "MIN" ? Infinity : -Infinity;
      for (const c of n.cells) {
        const v = valueOf(c);
        if (typeof v === "string") return v;
        if (n.f === "SUM") acc += v;
        else if (n.f === "MIN") acc = Math.min(acc, v);
        else acc = Math.max(acc, v);
      }
      return acc;
    }
  }
}

for (const comp of order) {
  const v = comp[0]!;
  const cyclic = comp.length > 1 || deps[v]!.includes(v);
  if (cyclic) {
    for (const c of comp) values[c] = "#CYCLE";
    continue;
  }
  if (values[v] !== undefined) continue;
  values[v] = evalNode(ast[v]!);
}

const out: string[] = [];
for (let i = 0; i < N; i++) {
  if (!defined[i]) continue;
  const v = values[i]!;
  out.push(`${cellName(i)} = ${typeof v === "number" ? String(v === 0 ? 0 : v) : v}`);
}
process.stdout.write(out.length ? out.join("\n") + "\n" : "");
