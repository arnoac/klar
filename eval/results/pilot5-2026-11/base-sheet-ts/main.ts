import { readFileSync } from "node:fs";

type Node =
  | { k: "num"; v: number }
  | { k: "cell"; name: string }
  | { k: "func"; f: string; cells: string[] }
  | { k: "neg"; e: Node }
  | { k: "bin"; op: string; l: Node; r: Node };

type Value = number | string; // string = error code

const CELL_RE = /^[A-Z]([1-9][0-9]?)$/;

function colOf(name: string): number {
  return name.charCodeAt(0) - 65;
}
function rowOf(name: string): number {
  return parseInt(name.slice(1), 10);
}

class ParseError extends Error {}

function tokenize(s: string): string[] {
  const toks: string[] = [];
  let i = 0;
  while (i < s.length) {
    const c = s[i];
    if (/\s/.test(c)) {
      i++;
    } else if (/[A-Za-z]/.test(c)) {
      let j = i;
      while (j < s.length && /[A-Za-z0-9]/.test(s[j])) j++;
      toks.push(s.slice(i, j));
      i = j;
    } else if (/[0-9]/.test(c)) {
      let j = i;
      while (j < s.length && /[0-9]/.test(s[j])) j++;
      toks.push(s.slice(i, j));
      i = j;
    } else if ("+-*/():".includes(c)) {
      toks.push(c);
      i++;
    } else {
      throw new ParseError();
    }
  }
  return toks;
}

function expandRange(a: string, b: string): string[] {
  const c1 = Math.min(colOf(a), colOf(b));
  const c2 = Math.max(colOf(a), colOf(b));
  const r1 = Math.min(rowOf(a), rowOf(b));
  const r2 = Math.max(rowOf(a), rowOf(b));
  const out: string[] = [];
  for (let c = c1; c <= c2; c++) {
    for (let r = r1; r <= r2; r++) out.push(String.fromCharCode(65 + c) + r);
  }
  return out;
}

function parseFormula(src: string): Node {
  const toks = tokenize(src);
  let p = 0;
  const peek = (): string | undefined => toks[p];
  const expect = (t: string): void => {
    if (toks[p] !== t) throw new ParseError();
    p++;
  };
  const cell = (): string => {
    const t = toks[p];
    if (t === undefined || !CELL_RE.test(t)) throw new ParseError();
    p++;
    return t;
  };

  function expr(): Node {
    let l = term();
    while (peek() === "+" || peek() === "-") {
      const op = toks[p++];
      l = { k: "bin", op, l, r: term() };
    }
    return l;
  }
  function term(): Node {
    let l = unary();
    while (peek() === "*" || peek() === "/") {
      const op = toks[p++];
      l = { k: "bin", op, l, r: unary() };
    }
    return l;
  }
  function unary(): Node {
    if (peek() === "-") {
      p++;
      return { k: "neg", e: unary() };
    }
    return primary();
  }
  function primary(): Node {
    const t = peek();
    if (t === undefined) throw new ParseError();
    if (/^[0-9]+$/.test(t)) {
      p++;
      return { k: "num", v: parseInt(t, 10) };
    }
    if (t === "(") {
      p++;
      const e = expr();
      expect(")");
      return e;
    }
    if (t === "SUM" || t === "MIN" || t === "MAX") {
      p++;
      expect("(");
      const a = cell();
      expect(":");
      const b = cell();
      expect(")");
      return { k: "func", f: t, cells: expandRange(a, b) };
    }
    if (CELL_RE.test(t)) {
      p++;
      return { k: "cell", name: t };
    }
    throw new ParseError();
  }

  const e = expr();
  if (p !== toks.length) throw new ParseError();
  return e;
}

function collectRefs(n: Node, out: Set<string>): void {
  switch (n.k) {
    case "num":
      return;
    case "cell":
      out.add(n.name);
      return;
    case "func":
      for (const c of n.cells) out.add(c);
      return;
    case "neg":
      collectRefs(n.e, out);
      return;
    case "bin":
      collectRefs(n.l, out);
      collectRefs(n.r, out);
      return;
  }
}

type Cell = { ast: Node | null; literal: Value | null; refs: string[] };

function main(): void {
  const input = readFileSync(0, "utf8");
  const raw = new Map<string, string>();
  for (const line0 of input.split("\n")) {
    const line = line0.replace(/\r$/, "");
    if (line.trim() === "") continue;
    const idx = line.indexOf(":");
    if (idx < 0) continue;
    const name = line.slice(0, idx).trim();
    const content = line.slice(idx + 1).trim();
    raw.set(name, content);
  }

  const cells = new Map<string, Cell>();
  for (const [name, content] of raw) {
    if (!content.startsWith("=")) {
      if (/^-?[0-9]+$/.test(content)) {
        const v = parseInt(content, 10);
        cells.set(name, { ast: null, literal: v === 0 ? 0 : v, refs: [] });
      } else {
        cells.set(name, { ast: null, literal: "#PARSE", refs: [] });
      }
      continue;
    }
    try {
      const ast = parseFormula(content.slice(1));
      const refs = new Set<string>();
      collectRefs(ast, refs);
      cells.set(name, {
        ast,
        literal: null,
        refs: [...refs].filter((r) => raw.has(r)),
      });
    } catch (e) {
      if (!(e instanceof ParseError)) throw e;
      cells.set(name, { ast: null, literal: "#PARSE", refs: [] });
    }
  }

  // Iterative Tarjan SCC. Emits SCCs in reverse topological order (dependencies first).
  const index = new Map<string, number>();
  const low = new Map<string, number>();
  const onStack = new Set<string>();
  const stack: string[] = [];
  const order: string[][] = [];
  let counter = 0;
  for (const start of cells.keys()) {
    if (index.has(start)) continue;
    const work: { v: string; i: number }[] = [{ v: start, i: 0 }];
    index.set(start, counter);
    low.set(start, counter);
    counter++;
    stack.push(start);
    onStack.add(start);
    while (work.length > 0) {
      const top = work[work.length - 1];
      const refs = cells.get(top.v)!.refs;
      if (top.i < refs.length) {
        const w = refs[top.i++];
        if (!index.has(w)) {
          index.set(w, counter);
          low.set(w, counter);
          counter++;
          stack.push(w);
          onStack.add(w);
          work.push({ v: w, i: 0 });
        } else if (onStack.has(w)) {
          low.set(top.v, Math.min(low.get(top.v)!, index.get(w)!));
        }
      } else {
        work.pop();
        if (low.get(top.v) === index.get(top.v)) {
          const scc: string[] = [];
          let w: string;
          do {
            w = stack.pop()!;
            onStack.delete(w);
            scc.push(w);
          } while (w !== top.v);
          order.push(scc);
        }
        if (work.length > 0) {
          const parent = work[work.length - 1].v;
          low.set(parent, Math.min(low.get(parent)!, low.get(top.v)!));
        }
      }
    }
  }

  const values = new Map<string, Value>();
  const get = (name: string): Value => {
    const v = values.get(name);
    return v === undefined ? 0 : v;
  };

  function evalNode(n: Node): Value {
    switch (n.k) {
      case "num":
        return n.v;
      case "cell":
        return get(n.name);
      case "neg": {
        const v = evalNode(n.e);
        if (typeof v === "string") return v;
        return v === 0 ? 0 : -v;
      }
      case "func": {
        let acc: number | null = null;
        for (const c of n.cells) {
          const v = get(c);
          if (typeof v === "string") return v;
          if (acc === null) acc = v;
          else if (n.f === "SUM") acc += v;
          else if (n.f === "MIN") acc = Math.min(acc, v);
          else acc = Math.max(acc, v);
        }
        return acc ?? 0;
      }
      case "bin": {
        const l = evalNode(n.l);
        if (typeof l === "string") return l;
        const r = evalNode(n.r);
        if (typeof r === "string") return r;
        let res: number;
        switch (n.op) {
          case "+":
            res = l + r;
            break;
          case "-":
            res = l - r;
            break;
          case "*":
            res = l * r;
            break;
          default:
            if (r === 0) return "#DIV0";
            res = Math.trunc(l / r);
        }
        return res === 0 ? 0 : res;
      }
    }
  }

  for (const scc of order) {
    const cyclic =
      scc.length > 1 || cells.get(scc[0])!.refs.includes(scc[0]);
    for (const name of scc) {
      const c = cells.get(name)!;
      if (cyclic) values.set(name, "#CYCLE");
      else if (c.literal !== null) values.set(name, c.literal);
      else values.set(name, evalNode(c.ast!));
    }
  }

  const names = [...cells.keys()].sort((a, b) => {
    const d = colOf(a) - colOf(b);
    return d !== 0 ? d : rowOf(a) - rowOf(b);
  });
  const out = names.map((n) => `${n} = ${values.get(n)}`).join("\n");
  process.stdout.write(out.length > 0 ? out + "\n" : "");
}

main();
