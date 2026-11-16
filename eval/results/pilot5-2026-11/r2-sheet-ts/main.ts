import { readFileSync } from "node:fs";

type Node =
  | { k: "num"; v: number }
  | { k: "cell"; id: number }
  | { k: "neg"; e: Node }
  | { k: "bin"; op: string; l: Node; r: Node }
  | { k: "fn"; name: string; cells: number[] };

type Value = number | string; // string = error

class ParseError extends Error {}

function cellId(name: string): number | null {
  const m = /^([A-Z])([1-9][0-9]?)$/.exec(name);
  if (!m) return null;
  return (m[1].charCodeAt(0) - 65) * 100 + Number(m[2]);
}

function cellName(id: number): string {
  return String.fromCharCode(65 + Math.floor(id / 100)) + String(id % 100);
}

type Tok = { t: "int"; v: number } | { t: "cell"; id: number } | { t: "fn"; name: string } | { t: "p"; c: string };

function tokenize(s: string): Tok[] {
  const toks: Tok[] = [];
  let i = 0;
  while (i < s.length) {
    const c = s[i];
    if (c === " " || c === "\t") {
      i++;
      continue;
    }
    if (c >= "0" && c <= "9") {
      let j = i;
      while (j < s.length && s[j] >= "0" && s[j] <= "9") j++;
      toks.push({ t: "int", v: Number(s.slice(i, j)) });
      i = j;
      continue;
    }
    if (/[A-Za-z]/.test(c)) {
      let j = i;
      while (j < s.length && /[A-Za-z]/.test(s[j])) j++;
      while (j < s.length && s[j] >= "0" && s[j] <= "9") j++;
      const w = s.slice(i, j);
      if (w === "SUM" || w === "MIN" || w === "MAX") toks.push({ t: "fn", name: w });
      else {
        const id = cellId(w);
        if (id === null) throw new ParseError();
        toks.push({ t: "cell", id });
      }
      i = j;
      continue;
    }
    if ("+-*/():".includes(c)) {
      toks.push({ t: "p", c });
      i++;
      continue;
    }
    throw new ParseError();
  }
  return toks;
}

function parseFormula(s: string): Node {
  const toks = tokenize(s);
  let pos = 0;
  const peekP = (c: string): boolean => {
    const t = toks[pos];
    return t !== undefined && t.t === "p" && t.c === c;
  };
  const expectP = (c: string): void => {
    if (!peekP(c)) throw new ParseError();
    pos++;
  };
  const expectCell = (): number => {
    const t = toks[pos];
    if (t === undefined || t.t !== "cell") throw new ParseError();
    pos++;
    return t.id;
  };
  function expr(): Node {
    let l = term();
    while (peekP("+") || peekP("-")) {
      const op = (toks[pos] as { t: "p"; c: string }).c;
      pos++;
      l = { k: "bin", op, l, r: term() };
    }
    return l;
  }
  function term(): Node {
    let l = unary();
    while (peekP("*") || peekP("/")) {
      const op = (toks[pos] as { t: "p"; c: string }).c;
      pos++;
      l = { k: "bin", op, l, r: unary() };
    }
    return l;
  }
  function unary(): Node {
    if (peekP("-")) {
      pos++;
      return { k: "neg", e: unary() };
    }
    return primary();
  }
  function primary(): Node {
    const t = toks[pos];
    if (t === undefined) throw new ParseError();
    if (t.t === "int") {
      pos++;
      return { k: "num", v: t.v };
    }
    if (t.t === "cell") {
      pos++;
      return { k: "cell", id: t.id };
    }
    if (t.t === "fn") {
      pos++;
      expectP("(");
      const a = expectCell();
      expectP(":");
      const b = expectCell();
      expectP(")");
      const c1 = Math.min(Math.floor(a / 100), Math.floor(b / 100));
      const c2 = Math.max(Math.floor(a / 100), Math.floor(b / 100));
      const r1 = Math.min(a % 100, b % 100);
      const r2 = Math.max(a % 100, b % 100);
      const cells: number[] = [];
      for (let c = c1; c <= c2; c++) for (let r = r1; r <= r2; r++) cells.push(c * 100 + r);
      return { k: "fn", name: t.name, cells };
    }
    if (t.t === "p" && t.c === "(") {
      pos++;
      const e = expr();
      expectP(")");
      return e;
    }
    throw new ParseError();
  }
  const e = expr();
  if (pos !== toks.length) throw new ParseError();
  return e;
}

function collectRefs(n: Node, out: number[]): void {
  switch (n.k) {
    case "num":
      return;
    case "cell":
      out.push(n.id);
      return;
    case "neg":
      collectRefs(n.e, out);
      return;
    case "bin":
      collectRefs(n.l, out);
      collectRefs(n.r, out);
      return;
    case "fn":
      for (const c of n.cells) out.push(c);
      return;
  }
}

function main(): void {
  const input = readFileSync(0, "utf8");
  const contents = new Map<number, string>();
  for (const raw of input.split("\n")) {
    const line = raw.replace(/\r$/, "");
    if (line.trim() === "") continue;
    const idx = line.indexOf(":");
    if (idx < 0) continue;
    const id = cellId(line.slice(0, idx).trim());
    if (id === null) continue;
    contents.set(id, line.slice(idx + 1).trim());
  }

  // Parse
  const kind = new Map<number, { k: "val"; v: Value } | { k: "f"; ast: Node }>();
  for (const [id, c] of contents) {
    if (c.startsWith("=")) {
      try {
        kind.set(id, { k: "f", ast: parseFormula(c.slice(1)) });
      } catch (e) {
        if (!(e instanceof ParseError)) throw e;
        kind.set(id, { k: "val", v: "#PARSE" });
      }
    } else if (/^-?[0-9]+$/.test(c)) {
      const v = Number(c);
      kind.set(id, { k: "val", v: v === 0 ? 0 : v });
    } else {
      kind.set(id, { k: "val", v: "#PARSE" });
    }
  }

  // Graph edges (only to defined formula cells matter for cycles)
  const ids = [...kind.keys()];
  const adj = new Map<number, number[]>();
  for (const id of ids) {
    const k = kind.get(id)!;
    const refs: number[] = [];
    if (k.k === "f") collectRefs(k.ast, refs);
    adj.set(
      id,
      [...new Set(refs)].filter((r) => kind.has(r)),
    );
  }

  // Iterative Tarjan SCC
  const index = new Map<number, number>();
  const low = new Map<number, number>();
  const onStack = new Set<number>();
  const stack: number[] = [];
  const order: number[] = []; // dependencies first
  const inCycle = new Set<number>();
  let counter = 0;
  for (const start of ids) {
    if (index.has(start)) continue;
    const work: { v: number; i: number }[] = [{ v: start, i: 0 }];
    index.set(start, counter);
    low.set(start, counter);
    counter++;
    stack.push(start);
    onStack.add(start);
    while (work.length > 0) {
      const top = work[work.length - 1];
      const nb = adj.get(top.v)!;
      if (top.i < nb.length) {
        const w = nb[top.i++];
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
        if (work.length > 0) {
          const p = work[work.length - 1].v;
          low.set(p, Math.min(low.get(p)!, low.get(top.v)!));
        }
        if (low.get(top.v) === index.get(top.v)) {
          const comp: number[] = [];
          let w: number;
          do {
            w = stack.pop()!;
            onStack.delete(w);
            comp.push(w);
          } while (w !== top.v);
          if (comp.length > 1 || adj.get(top.v)!.includes(top.v)) for (const c of comp) inCycle.add(c);
          for (const c of comp) order.push(c);
        }
      }
    }
  }

  // Evaluate in dependency order
  const values = new Map<number, Value>();
  const get = (id: number): Value => {
    const v = values.get(id);
    return v === undefined ? 0 : v;
  };
  const evalNode = (n: Node): Value => {
    switch (n.k) {
      case "num":
        return n.v;
      case "cell":
        return get(n.id);
      case "neg": {
        const v = evalNode(n.e);
        if (typeof v === "string") return v;
        return v === 0 ? 0 : -v;
      }
      case "bin": {
        const l = evalNode(n.l);
        if (typeof l === "string") return l;
        const r = evalNode(n.r);
        if (typeof r === "string") return r;
        switch (n.op) {
          case "+":
            return l + r;
          case "-":
            return l - r;
          case "*":
            return l * r;
          default: {
            if (r === 0) return "#DIV0";
            const q = Math.trunc(l / r);
            return q === 0 ? 0 : q;
          }
        }
      }
      case "fn": {
        let acc = 0;
        let first = true;
        for (const c of n.cells) {
          const v = get(c);
          if (typeof v === "string") return v;
          if (first) {
            acc = v;
            first = false;
          } else if (n.name === "SUM") acc += v;
          else if (n.name === "MIN") acc = Math.min(acc, v);
          else acc = Math.max(acc, v);
        }
        return acc;
      }
    }
  };
  for (const id of order) {
    if (inCycle.has(id)) {
      values.set(id, "#CYCLE");
      continue;
    }
    const k = kind.get(id)!;
    if (k.k === "val") values.set(id, k.v);
    else {
      const v = evalNode(k.ast);
      values.set(id, typeof v === "number" && v === 0 ? 0 : v);
    }
  }

  const out: string[] = [];
  for (const id of [...ids].sort((a, b) => a - b)) out.push(`${cellName(id)} = ${String(values.get(id))}`);
  process.stdout.write(out.length ? out.join("\n") + "\n" : "");
}

main();
