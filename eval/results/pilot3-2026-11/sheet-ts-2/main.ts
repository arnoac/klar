// Spreadsheet evaluator: reads "CELL: CONTENT" lines from stdin and prints the value of every defined cell.

type Expr =
  | { t: "num"; v: bigint }
  | { t: "ref"; col: number; row: number }
  | { t: "range"; fn: "SUM" | "MIN" | "MAX"; c1: number; r1: number; c2: number; r2: number }
  | { t: "neg"; e: Expr }
  | { t: "bin"; op: "+" | "-" | "*" | "/"; l: Expr; r: Expr };

type Cell =
  | { kind: "lit"; v: bigint }
  | { kind: "err"; code: string }
  | { kind: "formula"; expr: Expr };

// A cell value is either a BigInt or an error code string such as "#DIV0".
// Errors are thrown as strings during evaluation, so the first error met wins.
type Value = bigint | string;

class ParseError extends Error {}

type Tok =
  | { k: "int"; s: string }
  | { k: "cell"; col: number; row: number }
  | { k: "func"; name: "SUM" | "MIN" | "MAX" }
  | { k: "op"; c: string };

function tokenize(s: string): Tok[] {
  const out: Tok[] = [];
  let i = 0;
  while (i < s.length) {
    const ch = s[i]!;
    if (/\s/.test(ch)) {
      i++;
      continue;
    }
    if (ch >= "0" && ch <= "9") {
      let j = i;
      while (j < s.length && s[j]! >= "0" && s[j]! <= "9") j++;
      out.push({ k: "int", s: s.slice(i, j) });
      i = j;
      continue;
    }
    if (/[A-Za-z]/.test(ch)) {
      let j = i;
      while (j < s.length && /[A-Za-z0-9]/.test(s[j]!)) j++;
      const w = s.slice(i, j);
      i = j;
      const m = /^([A-Z])([1-9][0-9]?)$/.exec(w);
      if (m) {
        out.push({ k: "cell", col: m[1]!.charCodeAt(0) - 65, row: Number(m[2]) });
      } else if (w === "SUM" || w === "MIN" || w === "MAX") {
        out.push({ k: "func", name: w });
      } else {
        throw new ParseError("bad word");
      }
      continue;
    }
    if ("+-*/():".includes(ch)) {
      out.push({ k: "op", c: ch });
      i++;
      continue;
    }
    throw new ParseError("bad char");
  }
  return out;
}

class Parser {
  private pos = 0;
  constructor(private readonly toks: Tok[]) {}

  atEnd(): boolean {
    return this.pos >= this.toks.length;
  }

  private peekOp(...ops: string[]): string | undefined {
    const t = this.toks[this.pos];
    if (t && t.k === "op" && ops.includes(t.c)) return t.c;
    return undefined;
  }

  private expectOp(c: string): void {
    if (this.peekOp(c) === undefined) throw new ParseError("expected " + c);
    this.pos++;
  }

  private cellTok(): { col: number; row: number } {
    const t = this.toks[this.pos];
    if (t && t.k === "cell") {
      this.pos++;
      return { col: t.col, row: t.row };
    }
    throw new ParseError("expected cell");
  }

  parseExpr(): Expr {
    let l = this.parseTerm();
    let op = this.peekOp("+", "-");
    while (op !== undefined) {
      this.pos++;
      const r = this.parseTerm();
      l = { t: "bin", op: op as "+" | "-", l, r };
      op = this.peekOp("+", "-");
    }
    return l;
  }

  private parseTerm(): Expr {
    let l = this.parseUnary();
    let op = this.peekOp("*", "/");
    while (op !== undefined) {
      this.pos++;
      const r = this.parseUnary();
      l = { t: "bin", op: op as "*" | "/", l, r };
      op = this.peekOp("*", "/");
    }
    return l;
  }

  private parseUnary(): Expr {
    if (this.peekOp("-") !== undefined) {
      this.pos++;
      return { t: "neg", e: this.parseUnary() };
    }
    return this.parsePrimary();
  }

  private parsePrimary(): Expr {
    const t = this.toks[this.pos];
    if (!t) throw new ParseError("unexpected end");
    if (t.k === "int") {
      this.pos++;
      return { t: "num", v: BigInt(t.s) };
    }
    if (t.k === "cell") {
      this.pos++;
      return { t: "ref", col: t.col, row: t.row };
    }
    if (t.k === "func") {
      this.pos++;
      this.expectOp("(");
      const a = this.cellTok();
      this.expectOp(":");
      const b = this.cellTok();
      this.expectOp(")");
      return {
        t: "range",
        fn: t.name,
        c1: Math.min(a.col, b.col),
        c2: Math.max(a.col, b.col),
        r1: Math.min(a.row, b.row),
        r2: Math.max(a.row, b.row),
      };
    }
    if (this.peekOp("(") !== undefined) {
      this.pos++;
      const e = this.parseExpr();
      this.expectOp(")");
      return e;
    }
    throw new ParseError("unexpected token");
  }
}

// Parses the text after '='. Throws ParseError if the text does not match the grammar exactly.
function parseFormula(s: string): Expr {
  const p = new Parser(tokenize(s));
  const e = p.parseExpr();
  if (!p.atEnd()) throw new ParseError("trailing tokens");
  return e;
}

const keyOf = (col: number, row: number): number => col * 100 + row;

function collectRefs(e: Expr, out: number[]): void {
  switch (e.t) {
    case "num":
      return;
    case "ref":
      out.push(keyOf(e.col, e.row));
      return;
    case "range":
      for (let c = e.c1; c <= e.c2; c++) for (let r = e.r1; r <= e.r2; r++) out.push(keyOf(c, r));
      return;
    case "neg":
      collectRefs(e.e, out);
      return;
    case "bin":
      collectRefs(e.l, out);
      collectRefs(e.r, out);
      return;
  }
}

async function readStdin(): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks).toString("utf8");
}

function main(input: string): string[] {
  // Last definition of a cell wins; keys sort by column then row.
  const contents = new Map<number, string>();
  for (const raw of input.split("\n")) {
    const line = raw.trim();
    if (line === "") continue;
    const colon = line.indexOf(":");
    if (colon < 0) continue;
    const name = line.slice(0, colon).trim();
    const m = /^([A-Z])([1-9][0-9]?)$/.exec(name);
    if (!m) continue;
    contents.set(keyOf(m[1]!.charCodeAt(0) - 65, Number(m[2])), line.slice(colon + 1).trim());
  }

  const cells = new Map<number, Cell>();
  for (const [key, content] of contents) {
    if (/^-?[0-9]+$/.test(content)) {
      cells.set(key, { kind: "lit", v: BigInt(content) });
    } else if (content.startsWith("=")) {
      try {
        cells.set(key, { kind: "formula", expr: parseFormula(content.slice(1)) });
      } catch (e) {
        if (!(e instanceof ParseError)) throw e;
        cells.set(key, { kind: "err", code: "#PARSE" });
      }
    } else {
      cells.set(key, { kind: "err", code: "#PARSE" });
    }
  }

  // Dependency graph between formula cells (edges to non-formula cells cannot close a cycle).
  const adj = new Map<number, number[]>();
  for (const [key, cell] of cells) {
    if (cell.kind !== "formula") continue;
    const refs: number[] = [];
    collectRefs(cell.expr, refs);
    adj.set(
      key,
      refs.filter((k) => cells.get(k)?.kind === "formula"),
    );
  }

  // Tarjan's SCC algorithm. Components are emitted dependencies-first.
  const index = new Map<number, number>();
  const low = new Map<number, number>();
  const onStack = new Set<number>();
  const stack: number[] = [];
  const comps: number[][] = [];
  let counter = 0;
  const strong = (v: number): void => {
    index.set(v, counter);
    low.set(v, counter);
    counter++;
    stack.push(v);
    onStack.add(v);
    for (const w of adj.get(v) ?? []) {
      if (!index.has(w)) {
        strong(w);
        low.set(v, Math.min(low.get(v)!, low.get(w)!));
      } else if (onStack.has(w)) {
        low.set(v, Math.min(low.get(v)!, index.get(w)!));
      }
    }
    if (low.get(v) === index.get(v)) {
      const comp: number[] = [];
      let w: number;
      do {
        w = stack.pop()!;
        onStack.delete(w);
        comp.push(w);
      } while (w !== v);
      comps.push(comp);
    }
  };
  for (const key of adj.keys()) if (!index.has(key)) strong(key);

  const values = new Map<number, Value>();
  for (const [key, cell] of cells) {
    if (cell.kind === "lit") values.set(key, cell.v);
    else if (cell.kind === "err") values.set(key, cell.code);
  }

  const fetch = (key: number): bigint => {
    const v = values.get(key);
    if (v === undefined) return 0n;
    if (typeof v === "string") throw v;
    return v;
  };

  const evalExpr = (e: Expr): bigint => {
    switch (e.t) {
      case "num":
        return e.v;
      case "ref":
        return fetch(keyOf(e.col, e.row));
      case "range": {
        let acc: bigint | undefined;
        for (let c = e.c1; c <= e.c2; c++) {
          for (let r = e.r1; r <= e.r2; r++) {
            const x = fetch(keyOf(c, r));
            if (acc === undefined) acc = x;
            else if (e.fn === "SUM") acc += x;
            else if (e.fn === "MIN") acc = x < acc ? x : acc;
            else acc = x > acc ? x : acc;
          }
        }
        return acc!;
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
            if (b === 0n) throw "#DIV0";
            return a / b;
        }
      }
    }
  };

  for (const comp of comps) {
    const selfLoop = comp.length === 1 && (adj.get(comp[0]!) ?? []).includes(comp[0]!);
    if (comp.length > 1 || selfLoop) {
      for (const key of comp) values.set(key, "#CYCLE");
      continue;
    }
    const key = comp[0]!;
    const cell = cells.get(key)!;
    if (cell.kind !== "formula") continue;
    try {
      values.set(key, evalExpr(cell.expr));
    } catch (err) {
      if (typeof err !== "string") throw err;
      values.set(key, err);
    }
  }

  const lines: string[] = [];
  for (const key of [...cells.keys()].sort((a, b) => a - b)) {
    const name = String.fromCharCode(65 + Math.floor(key / 100)) + String(key % 100);
    const v = values.get(key)!;
    lines.push(`${name} = ${typeof v === "string" ? v : v.toString()}`);
  }
  return lines;
}

const input = await readStdin();
const out = main(input);
if (out.length > 0) process.stdout.write(out.join("\n") + "\n");
process.exitCode = 0;
