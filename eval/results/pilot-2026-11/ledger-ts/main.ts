import { readFileSync } from "node:fs";

const ARITY: Record<string, number> = {
  open: 1,
  deposit: 2,
  withdraw: 2,
  transfer: 3,
  close: 1,
};

const NAME_RE = /^[A-Za-z0-9_]{1,20}$/;
const AMOUNT_RE = /^([0-9]{1,15})(?:\.([0-9]{1,2}))?$/;

function parseAmount(tok: string): bigint | null {
  const m = AMOUNT_RE.exec(tok);
  if (!m) return null;
  const whole = BigInt(m[1]);
  const frac = m[2] === undefined ? 0n : BigInt(m[2].padEnd(2, "0"));
  const cents = whole * 100n + frac;
  return cents > 0n ? cents : null;
}

function formatCents(c: bigint): string {
  const neg = c < 0n;
  const a = neg ? -c : c;
  const whole = a / 100n;
  const frac = (a % 100n).toString().padStart(2, "0");
  return `${neg ? "-" : ""}${whole.toString()}.${frac}`;
}

function main(): void {
  let input: string;
  try {
    input = readFileSync(0, "utf8");
  } catch {
    input = "";
  }
  const lines = input.split("\n");
  if (lines.length > 0 && lines[lines.length - 1] === "") lines.pop();

  const accounts = new Map<string, bigint>();
  const out: string[] = [];
  let errors = 0;

  const fail = (n: number, msg: string): void => {
    out.push(`line ${n}: ${msg}`);
    errors++;
  };

  for (let i = 0; i < lines.length; i++) {
    const lineNo = i + 1;
    const trimmed = lines[i].trim();
    if (trimmed === "" || trimmed.startsWith("#")) continue;
    const words = trimmed.split(/[ \t]+/);
    const cmd = words[0];
    const args = words.slice(1);

    const arity = Object.prototype.hasOwnProperty.call(ARITY, cmd) ? ARITY[cmd] : undefined;
    if (arity === undefined) {
      fail(lineNo, `unknown command: ${cmd}`);
      continue;
    }
    if (args.length !== arity) {
      fail(lineNo, `wrong number of arguments: ${cmd}`);
      continue;
    }

    switch (cmd) {
      case "open": {
        const name = args[0];
        if (!NAME_RE.test(name)) {
          fail(lineNo, `invalid name: ${name}`);
          break;
        }
        if (accounts.has(name)) {
          fail(lineNo, `account exists: ${name}`);
          break;
        }
        accounts.set(name, 0n);
        break;
      }
      case "deposit":
      case "withdraw": {
        const [name, tok] = args;
        const amt = parseAmount(tok);
        if (amt === null) {
          fail(lineNo, `invalid amount: ${tok}`);
          break;
        }
        const bal = accounts.get(name);
        if (bal === undefined) {
          fail(lineNo, `no such account: ${name}`);
          break;
        }
        if (cmd === "deposit") {
          accounts.set(name, bal + amt);
        } else {
          if (amt > bal) {
            fail(lineNo, `insufficient funds: ${name}`);
            break;
          }
          accounts.set(name, bal - amt);
        }
        break;
      }
      case "transfer": {
        const [from, to, tok] = args;
        const amt = parseAmount(tok);
        if (amt === null) {
          fail(lineNo, `invalid amount: ${tok}`);
          break;
        }
        const fb = accounts.get(from);
        if (fb === undefined) {
          fail(lineNo, `no such account: ${from}`);
          break;
        }
        const tb = accounts.get(to);
        if (tb === undefined) {
          fail(lineNo, `no such account: ${to}`);
          break;
        }
        if (from === to) {
          fail(lineNo, `same account: ${from}`);
          break;
        }
        if (amt > fb) {
          fail(lineNo, `insufficient funds: ${from}`);
          break;
        }
        accounts.set(from, fb - amt);
        accounts.set(to, tb + amt);
        break;
      }
      case "close": {
        const name = args[0];
        const bal = accounts.get(name);
        if (bal === undefined) {
          fail(lineNo, `no such account: ${name}`);
          break;
        }
        if (bal !== 0n) {
          fail(lineNo, `balance not zero: ${name}`);
          break;
        }
        accounts.delete(name);
        break;
      }
    }
  }

  out.push("--- balances ---");
  const names = [...accounts.keys()].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
  for (const n of names) {
    out.push(`${n} ${formatCents(accounts.get(n) as bigint)}`);
  }
  out.push(`errors: ${errors}`);
  process.stdout.write(out.join("\n") + "\n");
  process.exitCode = errors > 0 ? 1 : 0;
}

main();
