import { readFileSync } from "node:fs";

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const nonEmpty = (v: unknown): v is string => typeof v === "string" && v.length > 0;
const wholeAtLeast = (v: unknown, min: number): v is number => typeof v === "number" && Number.isInteger(v) && v >= min;

function fail(): never {
  console.log(JSON.stringify({ error: "invalid input" }));
  process.exit(1);
}

let doc: unknown;
try { doc = JSON.parse(readFileSync(0, "utf8")); } catch { fail(); }
if (!isObj(doc) || !Array.isArray(doc.orders)) fail();

const seen = new Set<string>();
const invalid: { id: string | null; reason: string }[] = [];
const customers: Record<string, { orders: number; total_cents: number }> = {};
const qty = new Map<string, number>();
let valid = 0;

function reason(o: Obj): string | null {
  if (!nonEmpty(o.id)) return "missing id";
  if (seen.has(o.id)) return "duplicate id";
  if (!nonEmpty(o.customer)) return "missing customer";
  if (!Array.isArray(o.items) || o.items.length === 0) return "no items";
  for (const it of o.items) {
    if (!isObj(it) || !nonEmpty(it.sku) || !wholeAtLeast(it.qty, 1) || !wholeAtLeast(it.price_cents, 0)) return "bad item";
  }
  if (o.coupon !== undefined && o.coupon !== null && o.coupon !== "SAVE10" && o.coupon !== "FLAT500") return "unknown coupon";
  return null;
}

for (const raw of (doc as { orders: unknown[] }).orders) {
  const o: Obj = isObj(raw) ? raw : {};
  const why = reason(o);
  if (why !== null) {
    invalid.push({ id: nonEmpty(o.id) ? o.id : null, reason: why });
    continue;
  }
  const items = o.items as { sku: string; qty: number; price_cents: number }[];
  let sub = 0;
  for (const it of items) {
    sub += it.qty * it.price_cents;
    qty.set(it.sku, (qty.get(it.sku) ?? 0) + it.qty);
  }
  const total = o.coupon === "SAVE10" ? sub - Math.floor(sub / 10) : o.coupon === "FLAT500" ? Math.max(0, sub - 500) : sub;
  seen.add(o.id as string);
  valid++;
  const c = (customers[o.customer as string] ??= { orders: 0, total_cents: 0 });
  c.orders++;
  c.total_cents += total;
}

let top: string | null = null;
for (const [sku, n] of qty) {
  if (top === null || n > qty.get(top)! || (n === qty.get(top)! && sku < top)) top = sku;
}
console.log(JSON.stringify({ valid, invalid, customers, top_sku: top }));
