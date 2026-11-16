import { createServer, IncomingMessage, ServerResponse } from "node:http";
import { readFileSync, writeFileSync, renameSync, existsSync } from "node:fs";
import { join } from "node:path";

interface Note { id: number; title: string; body: string; tags: string[] }
interface Db { next: number; notes: Note[] }

const file = join(process.env.DATA_DIR ?? ".", "notes.json");
const db: Db = existsSync(file) ? JSON.parse(readFileSync(file, "utf8")) : { next: 1, notes: [] };
function save(): void {
  writeFileSync(file + ".tmp", JSON.stringify(db));
  renameSync(file + ".tmp", file);
}

class BadRequest { constructor(public message: string) {} }
const TAG = /^[a-z0-9-]{1,20}$/;

function title(v: unknown): string {
  if (typeof v !== "string") throw new BadRequest("title must be a string");
  const t = v.trim();
  if (t === "" || t.length > 100) throw new BadRequest("title must be 1 to 100 characters");
  return t;
}
function body(v: unknown): string {
  if (typeof v !== "string") throw new BadRequest("body must be a string");
  return v;
}
function tags(v: unknown): string[] {
  if (!Array.isArray(v)) throw new BadRequest("tags must be an array");
  const out: string[] = [];
  for (const t of v) {
    if (typeof t !== "string" || !TAG.test(t)) throw new BadRequest("invalid tag");
    if (!out.includes(t)) out.push(t);
  }
  return out;
}

function send(res: ServerResponse, status: number, value?: unknown): void {
  if (value === undefined) { res.writeHead(status); res.end(); return; }
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(value));
}
const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

async function readBody(req: IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  for await (const c of req) chunks.push(c as Buffer);
  let v: unknown;
  try { v = JSON.parse(Buffer.concat(chunks).toString("utf8")); } catch { throw new BadRequest("invalid JSON"); }
  if (typeof v !== "object" || v === null || Array.isArray(v)) throw new BadRequest("body must be an object");
  return v as Record<string, unknown>;
}

async function handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
  const url = new URL(req.url ?? "/", "http://localhost");
  const path = url.pathname;
  const method = req.method ?? "GET";
  if (path === "/" && method === "GET") {
    const items = db.notes.map(n => `<li>${esc(n.title)}</li>`).join("");
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end(`<!doctype html><title>Notes</title><ul>${items}</ul>`);
    return;
  }
  if (path === "/notes" && method === "GET") {
    const tag = url.searchParams.get("tag");
    const q = url.searchParams.get("q")?.toLowerCase();
    send(res, 200, db.notes.filter(n => (tag === null || n.tags.includes(tag)) &&
      (q === undefined || n.title.toLowerCase().includes(q) || n.body.toLowerCase().includes(q))));
    return;
  }
  if (path === "/notes" && method === "POST") {
    const b = await readBody(req);
    const note: Note = { id: db.next, title: title(b.title), body: b.body === undefined ? "" : body(b.body),
      tags: b.tags === undefined ? [] : tags(b.tags) };
    db.next++;
    db.notes.push(note);
    save();
    send(res, 201, note);
    return;
  }
  const m = /^\/notes\/(\d+)$/.exec(path);
  const note = m ? db.notes.find(n => n.id === Number(m[1])) : undefined;
  if (!m || !["GET", "PATCH", "DELETE"].includes(method)) { send(res, 404, { error: "not found" }); return; }
  if (!note) { send(res, 404, { error: "note not found" }); return; }
  if (method === "GET") { send(res, 200, note); return; }
  if (method === "DELETE") {
    db.notes = db.notes.filter(n => n !== note);
    save();
    send(res, 204);
    return;
  }
  const b = await readBody(req);
  const next: Note = { ...note };
  if (b.title !== undefined) next.title = title(b.title);
  if (b.body !== undefined) next.body = body(b.body);
  if (b.tags !== undefined) next.tags = tags(b.tags);
  Object.assign(note, next);
  save();
  send(res, 200, note);
}

createServer((req, res) => {
  handle(req, res).catch(e => {
    if (e instanceof BadRequest) send(res, 400, { error: e.message });
    else send(res, 500, { error: "internal error" });
  });
}).listen(Number(process.env.PORT ?? 8000), "127.0.0.1");
