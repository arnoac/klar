// Box office: reads commands from standard input, one per line, and prints what happened.
//
//   event ID CAPACITY PRICE     create an event (price in cents)
//   price ID PRICE              change the price of future purchases
//   buy ID PERSON QTY           buy QTY tickets
//   refund ID PERSON            give back all of PERSON's tickets for the event
//   capacity ID N               change the capacity of the event
//   report                      sold tickets and revenue per event
import { readFileSync } from "node:fs";

interface Event {
  id: string;
  capacity: number;
  price: number;
  sold: number;
  revenue: number;
  holders: Map<string, Holding>; // person -> tickets held and amount paid
  waitlist: Wait[];
}

interface Holding {
  qty: number;
  paid: number;
}

interface Wait {
  person: string;
  qty: number;
}

const events = new Map<string, Event>();

function money(cents: number): string {
  return `${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, "0")}`;
}

function count(text: string): number | null {
  return /^[0-9]+$/.test(text) ? Number(text) : null;
}

function find(id: string): Event | string {
  return events.get(id) ?? `error: no such event: ${id}`;
}

function purchase(e: Event, person: string, q: number): string {
  const cost = q * e.price;
  e.sold += q;
  e.revenue += cost;
  const h = e.holders.get(person) ?? { qty: 0, paid: 0 };
  h.qty += q;
  h.paid += cost;
  e.holders.set(person, h);
  return `ok: ${person} bought ${q} for ${e.id} (${money(cost)})`;
}

// Fill waitlisted requests that now fit, front to back; the rest keep their places.
function fillWaitlist(e: Event): string[] {
  const out: string[] = [];
  const remaining: Wait[] = [];
  for (const w of e.waitlist) {
    if (e.sold + w.qty <= e.capacity) out.push(purchase(e, w.person, w.qty));
    else remaining.push(w);
  }
  e.waitlist = remaining;
  return out;
}

function run(words: string[]): string {
  const [cmd, ...args] = words;
  switch (cmd) {
    case "event": {
      if (args.length !== 3) return "error: usage: event ID CAPACITY PRICE";
      const [id, cap, price] = args;
      if (events.has(id)) return `error: event exists: ${id}`;
      const c = count(cap), p = count(price);
      if (c === null) return `error: bad number: ${cap}`;
      if (p === null) return `error: bad number: ${price}`;
      events.set(id, { id, capacity: c, price: p, sold: 0, revenue: 0, holders: new Map(), waitlist: [] });
      return `ok: event ${id}`;
    }
    case "price": {
      if (args.length !== 2) return "error: usage: price ID PRICE";
      const e = find(args[0]);
      if (typeof e === "string") return e;
      const p = count(args[1]);
      if (p === null) return `error: bad number: ${args[1]}`;
      e.price = p;
      return `ok: price ${e.id} ${money(p)}`;
    }
    case "buy": {
      if (args.length !== 3) return "error: usage: buy ID PERSON QTY";
      const e = find(args[0]);
      if (typeof e === "string") return e;
      const person = args[1];
      const q = count(args[2]);
      if (q === null || q === 0) return `error: bad number: ${args[2]}`;
      if (e.sold + q > e.capacity) {
        if (e.waitlist.some(w => w.person === person)) return `error: already waiting: ${person}`;
        e.waitlist.push({ person, qty: q });
        return `waitlist: ${person} wants ${q} for ${e.id} (position ${e.waitlist.length})`;
      }
      return purchase(e, person, q);
    }
    case "refund": {
      if (args.length !== 2) return "error: usage: refund ID PERSON";
      const e = find(args[0]);
      if (typeof e === "string") return e;
      const person = args[1];
      const h = e.holders.get(person);
      if (h === undefined || h.qty === 0) {
        const i = e.waitlist.findIndex(w => w.person === person);
        if (i < 0) return `error: nothing to refund: ${person}`;
        e.waitlist.splice(i, 1);
        return `ok: left waitlist: ${person}`;
      }
      e.sold -= h.qty;
      e.revenue -= h.paid;
      e.holders.delete(person);
      return [`ok: refunded ${h.qty} to ${person} (${money(h.paid)})`, ...fillWaitlist(e)].join("\n");
    }
    case "capacity": {
      if (args.length !== 2) return "error: usage: capacity ID N";
      const e = find(args[0]);
      if (typeof e === "string") return e;
      const n = count(args[1]);
      if (n === null) return `error: bad number: ${args[1]}`;
      if (n < e.sold) return `error: capacity below sold: ${e.id}`;
      e.capacity = n;
      return [`ok: capacity ${e.id} ${n}`, ...fillWaitlist(e)].join("\n");
    }
    case "report": {
      if (args.length !== 0) return "error: usage: report";
      const lines = [...events.values()].map(e =>
        `${e.id}: ${e.sold}/${e.capacity} sold, revenue ${money(e.revenue)}` +
        (e.waitlist.length > 0 ? `, waiting ${e.waitlist.length}` : ""));
      return lines.length ? lines.join("\n") : "no events";
    }
    default:
      return `error: unknown command: ${cmd}`;
  }
}

for (const line of readFileSync(0, "utf8").split("\n")) {
  const words = line.trim().split(/\s+/).filter(w => w !== "");
  if (words.length === 0 || words[0].startsWith("#")) continue;
  console.log(run(words));
}
