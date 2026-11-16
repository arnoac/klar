import { readFileSync } from "node:fs";

type Expr =
  | { k: "num"; v: number }
  | { k: "cell"; c: string }
  | { k: "neg"; e: Expr }
  | { k: "bin"; op: string; l: Expr; r: Expr }
  | { k: "func"; f: string; cells: string[] };

type Value = number | string; // string = error code

class ParseError extends Error {}

const CELL_RE = /^[A-Z][1-9][0-9]?$/;

function colOf(c: string): number {
  return c.charCodeAt(0) - 65;
}
function rowOf(c: string): number {
  return parseInt(c.slice(1), 10);
}

type Tok = { t: "num" | "cell" | "func" | "sym"; s: string };

function tokenize(src: string): Tok[] {
  const toks: Tok[] = [];
  let i = 0;
  while (i < src.length) {
    const ch = src[i]!;
    if (/\s/.test(ch)) {
      i++;
      continue;
    }
    if (/[0-9]/.test(ch)) {
      let j = i;
      while (j < src.length && /[0-9]/.test(src[j]!)) j++;
      toks.push({ t: "num", s: src.slice(i, j) });
      i = j;
      continue;
    }
    if (/[A-Za-z]/.test(ch)) {
      let j = i;
      while (j < src.length && /[A-Za-z0-9]/.test(src[j]!)) j++;
      const w = src.slice(i, j);
      if (CELL_RE.test(w)) toks.push({ t: "cell", s: w });
      else if (w === "SUM" || w === "MIN" || w === "MAX") toks.push({ t: "func", s: w });
      else throw new ParseError();
      i = j;
      continue;
    }
    if ("+-*/():".includes(ch)) {
      toks.push({ t: "sym", s: ch });
      i++;
      continue;
    }
    throw new ParseError();
  }
  return toks;
}

function rangeCells(a: string, b: string): string[] {
  const c1 = Math.min(colOf(a), colOf(b));
  const c2 = Math.max(colOf(a), colOf(b));
  const r1 = Math.min(rowOf(a), rowOf(b));
  const r2 = Math.max(rowOf(a), rowOf(b));
  const out: string[] = [];
  for (let c = c1; c <= c2; c++)
    for (let r = r1; r <= r2; r++) out.push(String.fromCharCode(65 + c) + r);
  return out;
}

function parseFormula(src: string): Expr {
  const toks = tokenize(src);
  let p = 0;
  const peek = (): Tok | undefined => toks[p];
  const isSym = (s: string): boolean => {
    const t = toks[p];
    return t !== undefined && t.t === "sym" && t.s === s;
  };
  const expect = (s: string): void => {
    if (!isSym(s)) throw new ParseError();
    p++;
  };
  const expectCell = (): string => {
    const t = toks[p];
    if (!t || t.t !== "cell") throw new ParseError();
    p++;
    return t.s;
  };
  function expr(): Expr {
    let l = term();
    while (isSym("+") || isSym("-")) {
      const op = toks[p++]!.s;
      l = { k: "bin", op, l, r: term() };
    }
    return l;
  }
  function term(): Expr {
    let l = unary();
    while (isSym("*") || isSym("/")) {
      const op = toks[p++]!.s;
      l = { k: "bin", op, l, r: unary() };
    }
    return l;
  }
  function unary(): Expr {
    if (isSym("-")) {
      p++;
      return { k: "neg", e: unary() };
    }
    return primary();
  }
  function primary(): Expr {
    const t = peek();
    if (!t) throw new ParseError();
    if (t.t === "num") {
      p++;
      return { k: "num", v: parseInt(t.s, 10) };
    }
    if (t.t === "cell") {
      p++;
      return { k: "cell", c: t.s };
    }
    if (t.t === "func") {
      p++;
      expect("(");
      const a = expectCell();
      expect(":");
      const b = expectCell();
      expect(")");
      return { k: "func", f: t.s, cells: rangeCells(a, b) };
    }
    if (isSym("(")) {
      p++;
      const e = expr();
      expect(")");
      return e;
    }
    throw new ParseError();
  }
  const e = expr();
  if (p !== toks.length) throw new ParseError();
  return e;
}

function collectRefs(e: Expr, out: Set<string>): void {
  switch (e.k) {
    case "num":
      return;
    case "cell":
      out.add(e.c);
      return;
    case "neg":
      collectRefs(e.e, out);
      return;
    case "bin":
      collectRefs(e.l, out);
      collectRefs(e.r, out);
      return;
    case "func":
      for (const c of e.cells) out.add(c);
      return;
  }
}

function main(): void {
  const input = readFileSync(0, "utf8");
  const raw = new Map<string, string>();
  for (const line0 of input.split("\n")) {
    const line = line0.replace(/\r$/, "");
    if (line.trim() === "") continue;
    const idx = line.indexOf(":");
    if (idx < 0) continue;
    const cell = line.slice(0, idx).trim();
    const content = line.slice(idx + 1).trim();
    raw.set(cell, content);
  }

  type Cell = { kind: "val"; v: Value } | { kind: "f"; e: Expr; deps: string[] };
  const cells = new Map<string, Cell>();
  for (const [name, content] of raw) {
    if (!content.startsWith("=")) {
      if (/^-?[0-9]+$/.test(content)) {
        const v = parseInt(content, 10);
        cells.set(name, { kind: "val", v: v === 0 ? 0 : v });
      } else cells.set(name, { kind: "val", v: "#PARSE" });
      continue;
    }
    try {
      const e = parseFormula(content.slice(1));
      const s = new Set<string>();
      collectRefs(e, s);
      cells.set(name, { kind: "f", e, deps: [...s].filter((d) => raw.has(d)) });
    } catch {
      cells.set(name, { kind: "val", v: "#PARSE" });
    }
  }

  // Tarjan SCC (iterative)
  const names = [...cells.keys()];
  const index = new Map<string, number>();
  const low = new Map<string, number>();
  const onStack = new Set<string>();
  const stack: string[] = [];
  const inCycle = new Set<string>();
  const order: string[] = []; // dependencies before dependents
  let counter = 0;
  const depsOf = (n: string): string[] => {
    const c = cells.get(n);
    return c && c.kind === "f" ? c.deps : [];
  };
  for (const root of names) {
    if (index.has(root)) continue;
    const work: { n: string; i: number }[] = [{ n: root, i: 0 }];
    index.set(root, counter);
    low.set(root, counter);
    counter++;
    stack.push(root);
    onStack.add(root);
    while (work.length > 0) {
      const fr = work[work.length - 1]!;
      const ds = depsOf(fr.n);
      if (fr.i < ds.length) {
        const w = ds[fr.i++]!;
        if (!index.has(w)) {
          index.set(w, counter);
          low.set(w, counter);
          counter++;
          stack.push(w);
          onStack.add(w);
          work.push({ n: w, i: 0 });
        } else if (onStack.has(w)) {
          low.set(fr.n, Math.min(low.get(fr.n)!, index.get(w)!));
        }
      } else {
        work.pop();
        if (low.get(fr.n) === index.get(fr.n)) {
          const comp: string[] = [];
          let x: string;
          do {
            x = stack.pop()!;
            onStack.delete(x);
            comp.push(x);
          } while (x !== fr.n);
          if (comp.length > 1 || ds.includes(fr.n)) for (const c of comp) inCycle.add(c);
          for (const c of comp) order.push(c);
        }
        if (work.length > 0) {
          const parent = work[work.length - 1]!;
          low.set(parent.n, Math.min(low.get(parent.n)!, low.get(fr.n)!));
        }
      }
    }
  }

  const values = new Map<string, Value>();
  const get = (c: string): Value => {
    const v = values.get(c);
    return v === undefined ? 0 : v;
  };
  function ev(e: Expr): Value {
    switch (e.k) {
      case "num":
        return e.v;
      case "cell":
        return get(e.c);
      case "neg": {
        const v = ev(e.e);
        return typeof v === "string" ? v : v === 0 ? 0 : -v;
      }
      case "bin": {
        const l = ev(e.l);
        if (typeof l === "string") return l;
        const r = ev(e.r);
        if (typeof r === "string") return r;
        switch (e.op) {
          case "+":
            return l + r;
          case "-":
            return l - r;
          case "*": {
            const m = l * r;
            return m === 0 ? 0 : m;
          }
          default: {
            if (r === 0) return "#DIV0";
            const q = Math.trunc(l / r);
            return q === 0 ? 0 : q;
          }
        }
      }
      case "func": {
        let acc: number | null = null;
        for (const c of e.cells) {
          const v = get(c);
          if (typeof v === "string") return v;
          if (acc === null) acc = v;
          else if (e.f === "SUM") acc += v;
          else if (e.f === "MIN") acc = Math.min(acc, v);
          else acc = Math.max(acc, v);
        }
        return acc ?? 0;
      }
    }
  }

  for (const n of order) {
    const c = cells.get(n)!;
    if (inCycle.has(n)) values.set(n, "#CYCLE");
    else if (c.kind === "val") values.set(n, c.v);
    else values.set(n, ev(c.e));
  }

  names.sort((a, b) => colOf(a) - colOf(b) || rowOf(a) - rowOf(b));
  const out = names.map((n) => `${n} = ${get(n)}`);
  process.stdout.write(out.length ? out.join("\n") + "\n" : "");
}

main();
