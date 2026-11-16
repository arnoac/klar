import { readFileSync } from "node:fs";

type ErrCode = "#PARSE" | "#CYCLE" | "#DIV0";

class CellError extends Error {
  readonly code: ErrCode;
  constructor(code: ErrCode) {
    super(code);
    this.code = code;
  }
}

type Tok = { t: "int" | "cell" | "func" | "op"; s: string };

type Expr =
  | { k: "num"; v: bigint }
  | { k: "ref"; cell: string }
  | { k: "agg"; fn: "SUM" | "MIN" | "MAX"; cells: string[] }
  | { k: "neg"; e: Expr }
  | { k: "bin"; op: "+" | "-" | "*" | "/"; l: Expr; r: Expr };

type Cell =
  | { kind: "parse" }
  | { kind: "lit"; v: bigint }
  | { kind: "formula"; ast: Expr };

function tokenize(src: string): Tok[] {
  const toks: Tok[] = [];
  let i = 0;
  const isAlnum = (ch: string): boolean => /[A-Za-z0-9]/.test(ch);
  while (i < src.length) {
    const c = src[i] as string;
    if (c === " " || c === "\t" || c === "\r" || c === "\n") {
      i++;
      continue;
    }
    if ("+-*/():".includes(c)) {
      toks.push({ t: "op", s: c });
      i++;
      continue;
    }
    if (isAlnum(c)) {
      let j = i;
      while (j < src.length && isAlnum(src[j] as string)) j++;
      const w = src.slice(i, j);
      i = j;
      if (/^[0-9]+$/.test(w)) toks.push({ t: "int", s: w });
      else if (/^[A-Z][1-9][0-9]?$/.test(w)) toks.push({ t: "cell", s: w });
      else if (w === "SUM" || w === "MIN" || w === "MAX") toks.push({ t: "func", s: w });
      else throw new CellError("#PARSE");
      continue;
    }
    throw new CellError("#PARSE");
  }
  return toks;
}

function rangeCells(a: string, b: string): string[] {
  const colA = a.charCodeAt(0);
  const colB = b.charCodeAt(0);
  const rowA = parseInt(a.slice(1), 10);
  const rowB = parseInt(b.slice(1), 10);
  const c0 = Math.min(colA, colB);
  const c1 = Math.max(colA, colB);
  const r0 = Math.min(rowA, rowB);
  const r1 = Math.max(rowA, rowB);
  const out: string[] = [];
  for (let c = c0; c <= c1; c++) {
    for (let r = r0; r <= r1; r++) {
      out.push(String.fromCharCode(c) + String(r));
    }
  }
  return out;
}

class Parser {
  private pos = 0;
  constructor(private readonly toks: Tok[]) {}

  private peek(): Tok | undefined {
    return this.toks[this.pos];
  }

  private isOp(s: string): boolean {
    const t = this.peek();
    return t !== undefined && t.t === "op" && t.s === s;
  }

  private expectOp(s: string): void {
    if (!this.isOp(s)) throw new CellError("#PARSE");
    this.pos++;
  }

  parseAll(): Expr {
    const e = this.expr();
    if (this.pos !== this.toks.length) throw new CellError("#PARSE");
    return e;
  }

  private expr(): Expr {
    let l = this.term();
    while (this.isOp("+") || this.isOp("-")) {
      const op = (this.peek() as Tok).s as "+" | "-";
      this.pos++;
      const r = this.term();
      l = { k: "bin", op, l, r };
    }
    return l;
  }

  private term(): Expr {
    let l = this.unary();
    while (this.isOp("*") || this.isOp("/")) {
      const op = (this.peek() as Tok).s as "*" | "/";
      this.pos++;
      const r = this.unary();
      l = { k: "bin", op, l, r };
    }
    return l;
  }

  private unary(): Expr {
    if (this.isOp("-")) {
      this.pos++;
      return { k: "neg", e: this.unary() };
    }
    return this.primary();
  }

  private primary(): Expr {
    const t = this.peek();
    if (t === undefined) throw new CellError("#PARSE");
    if (t.t === "int") {
      this.pos++;
      return { k: "num", v: BigInt(t.s) };
    }
    if (t.t === "cell") {
      this.pos++;
      return { k: "ref", cell: t.s };
    }
    if (t.t === "func") {
      this.pos++;
      this.expectOp("(");
      const a = this.peek();
      if (a === undefined || a.t !== "cell") throw new CellError("#PARSE");
      this.pos++;
      this.expectOp(":");
      const b = this.peek();
      if (b === undefined || b.t !== "cell") throw new CellError("#PARSE");
      this.pos++;
      this.expectOp(")");
      return { k: "agg", fn: t.s as "SUM" | "MIN" | "MAX", cells: rangeCells(a.s, b.s) };
    }
    if (this.isOp("(")) {
      this.pos++;
      const e = this.expr();
      this.expectOp(")");
      return e;
    }
    throw new CellError("#PARSE");
  }
}

function parseContent(content: string): Cell {
  if (content.startsWith("=")) {
    try {
      const ast = new Parser(tokenize(content.slice(1))).parseAll();
      return { kind: "formula", ast };
    } catch (e) {
      if (e instanceof CellError) return { kind: "parse" };
      throw e;
    }
  }
  if (/^-?[0-9]+$/.test(content)) return { kind: "lit", v: BigInt(content) };
  return { kind: "parse" };
}

function refsOf(e: Expr, out: string[]): void {
  switch (e.k) {
    case "num":
      break;
    case "ref":
      out.push(e.cell);
      break;
    case "agg":
      for (const c of e.cells) out.push(c);
      break;
    case "neg":
      refsOf(e.e, out);
      break;
    case "bin":
      refsOf(e.l, out);
      refsOf(e.r, out);
      break;
  }
}

// ---- main ----

const input = readFileSync(0, "utf8");
const cells = new Map<string, Cell>();
for (const raw of input.split("\n")) {
  const line = raw.trim();
  if (line === "") continue;
  const idx = line.indexOf(":");
  if (idx < 0) continue;
  const name = line.slice(0, idx).trim();
  const content = line.slice(idx + 1).trim();
  cells.set(name, parseContent(content));
}

// Dependency graph over formula cells only (other cells cannot be on a cycle).
const edges = new Map<string, string[]>();
const selfLoop = new Set<string>();
for (const [name, cell] of cells) {
  if (cell.kind !== "formula") continue;
  const refs: string[] = [];
  refsOf(cell.ast, refs);
  const out: string[] = [];
  for (const r of refs) {
    const target = cells.get(r);
    if (target === undefined || target.kind !== "formula") continue;
    if (r === name) selfLoop.add(name);
    out.push(r);
  }
  edges.set(name, out);
}

// Tarjan's SCC: a formula cell is on a cycle iff its component has more than
// one member or it references itself.
const index = new Map<string, number>();
const low = new Map<string, number>();
const onStack = new Set<string>();
const sccStack: string[] = [];
const onCycle = new Set<string>();
let counter = 0;

function strongConnect(v: string): void {
  index.set(v, counter);
  low.set(v, counter);
  counter++;
  sccStack.push(v);
  onStack.add(v);
  for (const w of edges.get(v) ?? []) {
    if (!index.has(w)) {
      strongConnect(w);
      low.set(v, Math.min(low.get(v) as number, low.get(w) as number));
    } else if (onStack.has(w)) {
      low.set(v, Math.min(low.get(v) as number, index.get(w) as number));
    }
  }
  if (low.get(v) === index.get(v)) {
    const comp: string[] = [];
    let w: string;
    do {
      w = sccStack.pop() as string;
      onStack.delete(w);
      comp.push(w);
    } while (w !== v);
    if (comp.length > 1 || selfLoop.has(v)) {
      for (const x of comp) onCycle.add(x);
    }
  }
}

for (const [name, cell] of cells) {
  if (cell.kind === "formula" && !index.has(name)) strongConnect(name);
}

// Values of non-cycle formula cells, computed in dependency order (iterative
// post-order DFS, so long reference chains do not overflow the call stack).
const memo = new Map<string, bigint | ErrCode>();

function lookup(name: string): bigint {
  if (onCycle.has(name)) throw new CellError("#CYCLE");
  const cell = cells.get(name);
  if (cell === undefined) return 0n;
  if (cell.kind === "parse") throw new CellError("#PARSE");
  if (cell.kind === "lit") return cell.v;
  const m = memo.get(name);
  if (m === undefined) throw new Error("internal: formula value not computed: " + name);
  if (typeof m === "string") throw new CellError(m);
  return m;
}

function evalExpr(e: Expr): bigint {
  switch (e.k) {
    case "num":
      return e.v;
    case "ref":
      return lookup(e.cell);
    case "agg": {
      if (e.fn === "SUM") {
        let acc = 0n;
        for (const c of e.cells) acc += lookup(c);
        return acc;
      }
      let acc: bigint | undefined;
      for (const c of e.cells) {
        const v = lookup(c);
        if (acc === undefined) acc = v;
        else if (e.fn === "MIN") {
          if (v < acc) acc = v;
        } else if (v > acc) acc = v;
      }
      return acc ?? 0n;
    }
    case "neg":
      return -evalExpr(e.e);
    case "bin": {
      const a = evalExpr(e.l);
      const b = evalExpr(e.r);
      switch (e.op) {
        case "+":
          return a + b;
        case "-":
          return a - b;
        case "*":
          return a * b;
        case "/":
          if (b === 0n) throw new CellError("#DIV0");
          return a / b;
      }
    }
  }
}

const done = new Set<string>();
for (const start of cells.keys()) {
  const startCell = cells.get(start) as Cell;
  if (startCell.kind !== "formula" || onCycle.has(start) || done.has(start)) continue;
  done.add(start);
  const stack: { n: string; deps: string[]; i: number }[] = [
    { n: start, deps: (edges.get(start) ?? []).filter((d) => !onCycle.has(d)), i: 0 },
  ];
  while (stack.length > 0) {
    const top = stack[stack.length - 1] as { n: string; deps: string[]; i: number };
    if (top.i < top.deps.length) {
      const d = top.deps[top.i++] as string;
      if (!done.has(d)) {
        done.add(d);
        stack.push({ n: d, deps: (edges.get(d) ?? []).filter((x) => !onCycle.has(x)), i: 0 });
      }
    } else {
      stack.pop();
      const cell = cells.get(top.n) as { kind: "formula"; ast: Expr };
      try {
        memo.set(top.n, evalExpr(cell.ast));
      } catch (e) {
        if (e instanceof CellError) memo.set(top.n, e.code);
        else throw e;
      }
    }
  }
}

function cmpCells(a: string, b: string): number {
  const ca = a.charCodeAt(0);
  const cb = b.charCodeAt(0);
  if (ca !== cb) return ca - cb;
  return parseInt(a.slice(1), 10) - parseInt(b.slice(1), 10);
}

const names = [...cells.keys()].sort(cmpCells);
const lines: string[] = [];
for (const name of names) {
  let text: string;
  try {
    text = lookup(name).toString();
  } catch (e) {
    if (e instanceof CellError) text = e.code;
    else throw e;
  }
  lines.push(`${name} = ${text}`);
}
if (lines.length > 0) process.stdout.write(lines.join("\n") + "\n");
