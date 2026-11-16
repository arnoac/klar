import { readFileSync } from "node:fs";

const NAME = /^[A-Za-z0-9_]{1,20}$/;
const AMOUNT = /^(\d{1,15})(?:\.(\d{1,2}))?$/;
const ARITY: Record<string, number> = { open: 1, deposit: 2, withdraw: 2, transfer: 3, close: 1 };

function parseAmount(tok: string): bigint | null {
  const m = AMOUNT.exec(tok);
  if (!m) return null;
  const cents = BigInt(m[1]) * 100n + BigInt((m[2] ?? "").padEnd(2, "0") || "0");
  return cents > 0n ? cents : null;
}

function fmt(c: bigint): string {
  return `${c / 100n}.${(c % 100n).toString().padStart(2, "0")}`;
}

const accounts = new Map<string, bigint>();
let errors = 0;

function apply(words: string[]): string | null {
  const [cmd, ...a] = words;
  if (!(cmd in ARITY)) return `unknown command: ${cmd}`;
  if (a.length !== ARITY[cmd]) return `wrong number of arguments: ${cmd}`;
  if (cmd === "open") {
    if (!NAME.test(a[0])) return `invalid name: ${a[0]}`;
    if (accounts.has(a[0])) return `account exists: ${a[0]}`;
    accounts.set(a[0], 0n);
    return null;
  }
  let amount = 0n;
  if (cmd !== "close") {
    const p = parseAmount(a[a.length - 1]);
    if (p === null) return `invalid amount: ${a[a.length - 1]}`;
    amount = p;
  }
  const names = cmd === "transfer" ? a.slice(0, 2) : [a[0]];
  for (const n of names) if (!accounts.has(n)) return `no such account: ${n}`;
  const bal = accounts.get(a[0])!;
  switch (cmd) {
    case "deposit": accounts.set(a[0], bal + amount); return null;
    case "withdraw":
      if (amount > bal) return `insufficient funds: ${a[0]}`;
      accounts.set(a[0], bal - amount); return null;
    case "transfer":
      if (a[0] === a[1]) return `same account: ${a[0]}`;
      if (amount > bal) return `insufficient funds: ${a[0]}`;
      accounts.set(a[0], bal - amount);
      accounts.set(a[1], accounts.get(a[1])! + amount); return null;
    default:
      if (bal !== 0n) return `balance not zero: ${a[0]}`;
      accounts.delete(a[0]); return null;
  }
}

const lines = readFileSync(0, "utf8").split("\n");
if (lines.length && lines[lines.length - 1] === "") lines.pop();
lines.forEach((raw, i) => {
  const line = raw.trim();
  if (line === "" || line.startsWith("#")) return;
  const err = apply(line.split(/[ \t]+/));
  if (err !== null) { console.log(`line ${i + 1}: ${err}`); errors++; }
});
console.log("--- balances ---");
for (const n of [...accounts.keys()].sort()) console.log(`${n} ${fmt(accounts.get(n)!)}`);
console.log(`errors: ${errors}`);
process.exitCode = errors ? 1 : 0;
