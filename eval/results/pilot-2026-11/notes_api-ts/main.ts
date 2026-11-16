import * as http from "node:http";
import * as fs from "node:fs";
import * as path from "node:path";

interface Note {
  id: number;
  title: string;
  body: string;
  tags: string[];
}

interface Store {
  nextId: number;
  notes: Note[];
}

const PORT = Number(process.env.PORT ?? "8000");
const DATA_DIR = process.env.DATA_DIR ?? ".";
const DATA_FILE = path.join(DATA_DIR, "notes.json");

function loadStore(): Store {
  try {
    const raw = fs.readFileSync(DATA_FILE, "utf8");
    const parsed = JSON.parse(raw) as Store;
    if (typeof parsed.nextId === "number" && Array.isArray(parsed.notes)) return parsed;
  } catch {
    // fall through to empty store
  }
  return { nextId: 1, notes: [] };
}

const store: Store = loadStore();

function saveStore(): void {
  const tmp = DATA_FILE + ".tmp";
  const fd = fs.openSync(tmp, "w");
  try {
    fs.writeSync(fd, JSON.stringify(store));
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
  fs.renameSync(tmp, DATA_FILE);
}

class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

const TAG_RE = /^[a-z0-9-]{1,20}$/;

function validateTitle(v: unknown): string {
  if (typeof v !== "string") throw new HttpError(400, "title must be a string");
  const t = v.trim();
  if (t.length === 0) throw new HttpError(400, "title is required");
  if ([...t].length > 100) throw new HttpError(400, "title is too long");
  return t;
}

function validateBody(v: unknown): string {
  if (typeof v !== "string") throw new HttpError(400, "body must be a string");
  return v;
}

function validateTags(v: unknown): string[] {
  if (!Array.isArray(v)) throw new HttpError(400, "tags must be an array of strings");
  const out: string[] = [];
  for (const t of v) {
    if (typeof t !== "string") throw new HttpError(400, "tags must be an array of strings");
    if (!TAG_RE.test(t)) throw new HttpError(400, `invalid tag: ${t}`);
    if (!out.includes(t)) out.push(t);
  }
  return out;
}

function parseJsonObject(raw: string): Record<string, unknown> {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    throw new HttpError(400, "invalid JSON");
  }
  if (typeof data !== "object" || data === null || Array.isArray(data)) {
    throw new HttpError(400, "body must be a JSON object");
  }
  return data as Record<string, unknown>;
}

function has(obj: Record<string, unknown>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(obj, key);
}

function asciiLower(s: string): string {
  return s.replace(/[A-Z]/g, (c) => String.fromCharCode(c.charCodeAt(0) + 32));
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function sendJson(res: http.ServerResponse, status: number, data: unknown): void {
  const body = JSON.stringify(data);
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(body),
  });
  res.end(body);
}

function readBody(req: http.IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

function findNote(idStr: string): Note {
  if (!/^\d+$/.test(idStr)) throw new HttpError(404, "note not found");
  const id = Number(idStr);
  const note = store.notes.find((n) => n.id === id);
  if (!note) throw new HttpError(404, "note not found");
  return note;
}

async function handle(req: http.IncomingMessage, res: http.ServerResponse): Promise<void> {
  const url = new URL(req.url ?? "/", "http://localhost");
  const p = url.pathname;
  const method = req.method ?? "GET";

  if (p === "/" && method === "GET") {
    const items = store.notes.map((n) => `<li>${escapeHtml(n.title)}</li>`).join("\n");
    const html = `<!doctype html>\n<html><head><meta charset="utf-8"><title>Notes</title></head>\n<body><h1>Notes</h1>\n<ul>\n${items}\n</ul>\n</body></html>\n`;
    res.writeHead(200, {
      "Content-Type": "text/html; charset=utf-8",
      "Content-Length": Buffer.byteLength(html),
    });
    res.end(html);
    return;
  }

  if (p === "/notes") {
    if (method === "GET") {
      const tag = url.searchParams.get("tag");
      const q = url.searchParams.get("q");
      let notes = store.notes;
      if (tag !== null) notes = notes.filter((n) => n.tags.includes(tag));
      if (q !== null) {
        const ql = asciiLower(q);
        notes = notes.filter(
          (n) => asciiLower(n.title).includes(ql) || asciiLower(n.body).includes(ql),
        );
      }
      sendJson(res, 200, notes);
      return;
    }
    if (method === "POST") {
      const obj = parseJsonObject(await readBody(req));
      if (!has(obj, "title")) throw new HttpError(400, "title is required");
      const title = validateTitle(obj.title);
      const body = has(obj, "body") ? validateBody(obj.body) : "";
      const tags = has(obj, "tags") ? validateTags(obj.tags) : [];
      const note: Note = { id: store.nextId, title, body, tags };
      store.nextId += 1;
      store.notes.push(note);
      saveStore();
      sendJson(res, 201, note);
      return;
    }
    throw new HttpError(404, "not found");
  }

  const m = /^\/notes\/([^/]+)$/.exec(p);
  if (m) {
    const idStr = m[1]!;
    if (method === "GET") {
      sendJson(res, 200, findNote(idStr));
      return;
    }
    if (method === "PATCH") {
      const raw = await readBody(req);
      const note = findNote(idStr);
      const obj = parseJsonObject(raw);
      const title = has(obj, "title") ? validateTitle(obj.title) : note.title;
      const body = has(obj, "body") ? validateBody(obj.body) : note.body;
      const tags = has(obj, "tags") ? validateTags(obj.tags) : note.tags;
      note.title = title;
      note.body = body;
      note.tags = tags;
      saveStore();
      sendJson(res, 200, note);
      return;
    }
    if (method === "DELETE") {
      const note = findNote(idStr);
      store.notes = store.notes.filter((n) => n !== note);
      saveStore();
      res.writeHead(204);
      res.end();
      return;
    }
  }

  throw new HttpError(404, "not found");
}

const server = http.createServer((req, res) => {
  handle(req, res).catch((err: unknown) => {
    if (err instanceof HttpError) {
      sendJson(res, err.status, { error: err.message });
    } else {
      sendJson(res, 500, { error: "internal error" });
    }
  });
});

server.listen(PORT, "127.0.0.1");

function shutdown(): void {
  server.close();
  process.exit(0);
}
process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);
