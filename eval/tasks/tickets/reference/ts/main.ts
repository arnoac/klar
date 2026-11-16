// Box office: reads commands from standard input, one per line, and prints what happened.
//
//   event ID CAPACITY PRICE     create an event (price in cents)
//   price ID PRICE              change the price of future purchases
//   buy ID PERSON QTY           buy QTY tickets
//   refund ID PERSON            give back all of PERSON's tickets for the event
//   capacity ID N               change the capacity
//   report                      sold tickets and revenue per event
//
// A purchase that does not fit joins the event's waitlist; freed seats go to the waitlist in order.
import { readFileSync } from "node:fs";

interface Event {
  id: string;
  capacity: number;
  price: number;
  sold: number;
  revenue: number;
  holders: Map<string, number>; // person -> tickets held
  paid: Map<string, number>; // person -> cents paid for the tickets held
  waiting: { person: string; qty: number }[];
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

function sell(e: Event, person: string, q: number): string {
  e.sold += q;
  e.revenue += q * e.price;
  e.holders.set(person, (e.holders.get(person) ?? 0) + q);
  e.paid.set(person, (e.paid.get(person) ?? 0) + q * e.price);
  return `ok: ${person} bought ${q} for ${e.id} (${money(q * e.price)})`;
}

// Fills waitlisted requests that fit now, front to back.
function fill(e: Event): string[] {
  const out: string[] = [];
  e.waiting = e.waiting.filter(w => {
    if (e.sold + w.qty > e.capacity) return true;
    out.push(sell(e, w.person, w.qty));
    return false;
  });
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
      events.set(id, { id, capacity: c, price: p, sold: 0, revenue: 0, holders: new Map(), paid: new Map(), waiting: [] });
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
        if (e.waiting.some(w => w.person === person)) return `error: already waiting: ${person}`;
        e.waiting.push({ person, qty: q });
        return `waitlist: ${person} wants ${q} for ${e.id} (position ${e.waiting.length})`;
      }
      return sell(e, person, q);
    }
    case "refund": {
      if (args.length !== 2) return "error: usage: refund ID PERSON";
      const e = find(args[0]);
      if (typeof e === "string") return e;
      const person = args[1];
      const held = e.holders.get(person) ?? 0;
      if (held === 0) {
        const at = e.waiting.findIndex(w => w.person === person);
        if (at < 0) return `error: nothing to refund: ${person}`;
        e.waiting.splice(at, 1);
        return `ok: left waitlist: ${person}`;
      }
      const amount = e.paid.get(person) ?? 0;
      e.sold -= held;
      e.revenue -= amount;
      e.holders.delete(person);
      e.paid.delete(person);
      return [`ok: refunded ${held} to ${person} (${money(amount)})`, ...fill(e)].join("\n");
    }
    case "capacity": {
      if (args.length !== 2) return "error: usage: capacity ID N";
      const e = find(args[0]);
      if (typeof e === "string") return e;
      const n = count(args[1]);
      if (n === null) return `error: bad number: ${args[1]}`;
      if (n < e.sold) return `error: capacity below sold: ${e.id}`;
      e.capacity = n;
      return [`ok: capacity ${e.id} ${n}`, ...fill(e)].join("\n");
    }
    case "report": {
      if (args.length !== 0) return "error: usage: report";
      const lines = [...events.values()].map(e => `${e.id}: ${e.sold}/${e.capacity} sold, revenue ${money(e.revenue)}` +
        (e.waiting.length ? `, waiting ${e.waiting.length}` : ""));
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
