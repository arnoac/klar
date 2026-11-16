import { readFileSync } from "node:fs";

type Obj = Record<string, unknown>;

function isObj(v: unknown): v is Obj {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function get(o: Obj, k: string): unknown {
  return Object.prototype.hasOwnProperty.call(o, k) ? o[k] : undefined;
}

function nonEmptyStr(v: unknown): v is string {
  return typeof v === "string" && v.length > 0;
}

function fail(): never {
  process.stdout.write(JSON.stringify({ error: "invalid input" }) + "\n");
  process.exit(1);
}

let data: unknown;
try {
  data = JSON.parse(readFileSync(0, "utf8"));
} catch {
  fail();
}
if (!isObj(data)) fail();
const orders = get(data, "orders");
if (!Array.isArray(orders)) fail();

const COUPONS = new Set(["SAVE10", "FLAT500"]);

interface Item {
  sku: string;
  qty: number;
  price: number;
}

const validIds = new Set<string>();
const invalid: { id: string | null; reason: string }[] = [];
const customers = new Map<string, { orders: number; total_cents: number }>();
const skuQty = new Map<string, number>();
let valid = 0;

function validate(o: Obj): { reason: string } | { customer: string; items: Item[]; coupon: string | null } {
  const id = get(o, "id");
  if (!nonEmptyStr(id)) return { reason: "missing id" };
  if (validIds.has(id)) return { reason: "duplicate id" };
  const customer = get(o, "customer");
  if (!nonEmptyStr(customer)) return { reason: "missing customer" };
  const items = get(o, "items");
  if (!Array.isArray(items) || items.length === 0) return { reason: "no items" };
  const parsed: Item[] = [];
  for (const it of items) {
    if (!isObj(it)) return { reason: "bad item" };
    const sku = get(it, "sku");
    const qty = get(it, "qty");
    const price = get(it, "price_cents");
    if (!nonEmptyStr(sku)) return { reason: "bad item" };
    if (typeof qty !== "number" || !Number.isInteger(qty) || qty < 1) return { reason: "bad item" };
    if (typeof price !== "number" || !Number.isInteger(price) || price < 0) return { reason: "bad item" };
    parsed.push({ sku, qty, price });
  }
  const coupon = get(o, "coupon");
  if (coupon !== undefined && coupon !== null) {
    if (typeof coupon !== "string" || !COUPONS.has(coupon)) return { reason: "unknown coupon" };
    return { customer, items: parsed, coupon };
  }
  return { customer, items: parsed, coupon: null };
}

for (const entry of orders) {
  const o: Obj = isObj(entry) ? entry : {};
  const r = validate(o);
  if ("reason" in r) {
    const id = get(o, "id");
    invalid.push({ id: nonEmptyStr(id) ? id : null, reason: r.reason });
    continue;
  }
  const id = get(o, "id") as string;
  validIds.add(id);
  valid++;
  let subtotal = 0;
  for (const it of r.items) {
    subtotal += it.qty * it.price;
    skuQty.set(it.sku, (skuQty.get(it.sku) ?? 0) + it.qty);
  }
  let total = subtotal;
  if (r.coupon === "SAVE10") total = subtotal - Math.floor(subtotal / 10);
  else if (r.coupon === "FLAT500") total = Math.max(0, subtotal - 500);
  const c = customers.get(r.customer) ?? { orders: 0, total_cents: 0 };
  c.orders++;
  c.total_cents += total;
  customers.set(r.customer, c);
}

let topSku: string | null = null;
let topQty = -1;
for (const [sku, q] of skuQty) {
  if (
    q > topQty ||
    (q === topQty && topSku !== null && Buffer.compare(Buffer.from(sku, "utf8"), Buffer.from(topSku, "utf8")) < 0)
  ) {
    topSku = sku;
    topQty = q;
  }
}

const custObj: Record<string, { orders: number; total_cents: number }> = Object.create(null);
for (const [k, v] of customers) custObj[k] = v;

process.stdout.write(
  JSON.stringify({ valid, invalid, customers: custObj, top_sku: topSku }) + "\n",
);
process.exitCode = 0;
