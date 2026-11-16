// The Klar Board endpoints that bench/deploy/load.go exercises, hand-written for Node.js with node:http and
// node:sqlite and no packages: the "comparable small Node server". Same schema, queries, session scheme
// (random token in an HttpOnly cookie, SHA-256 in the database), PBKDF2 password hashes and security headers.
//
//   PORT=8080 BOARD_DB=board.db APP_DIR=. node board_node.mjs
import http from "node:http";
import crypto from "node:crypto";
import { readFile } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";

const db = new DatabaseSync(process.env.BOARD_DB || "board.db");
db.exec("pragma foreign_keys = on");
db.exec("pragma journal_mode = wal");
db.exec("pragma busy_timeout = 5000");
db.exec(`create table if not exists users (id integer primary key, name text not null unique, password text not null);
create table if not exists sessions (token_hash text primary key, user_id integer not null references users(id), created real not null);
create table if not exists boards (id integer primary key, name text not null, owner_id integer not null references users(id));
create table if not exists members (board_id integer not null references boards(id), user_id integer not null references users(id), primary key (board_id, user_id));
create table if not exists cards (id integer primary key, board_id integer not null references boards(id), title text not null, notes text not null default '', col text not null, position integer not null, assignee integer references users(id));
create table if not exists events (id integer primary key, board_id integer not null references boards(id), user text not null, text text not null, at real not null);
create index if not exists cards_by_board on cards (board_id, col, position);
create index if not exists events_by_board on events (board_id, id);`);

const q = s => db.prepare(s);
const userByName = q("select id, name, password from users where name = ?");
const userAdd = q("insert into users (name, password) values (?, ?)");
const sessionAdd = q("insert into sessions (token_hash, user_id, created) values (?, ?, ?)");
const sessionUser = q("select u.id, u.name from sessions s join users u on u.id = s.user_id where s.token_hash = ?");
const boardAdd = q("insert into boards (name, owner_id) values (?, ?)");
const boardFind = q("select id, name, owner_id from boards where id = ?");
const memberAdd = q("insert or ignore into members (board_id, user_id) values (?, ?)");
const isMember = q("select 1 from members where board_id = ? and user_id = ?");
const membersOf = q("select u.id, u.name from users u join members m on m.user_id = u.id where m.board_id = ? order by u.name");
const cardsOf = q("select id, board_id, title, notes, col as column, position, assignee from cards where board_id = ? order by col, position");
const cardAdd = q("insert into cards (board_id, title, col, position) values (?, ?, ?, ?)");
const eventAdd = q("insert into events (board_id, user, text, at) values (?, ?, ?, ?)");
const eventsOf = q("select id, board_id, user, text, at from events where board_id = ? order by id desc limit ?");

const sha256 = s => crypto.createHash("sha256").update(s).digest("hex");
const pbkdf2 = (pw, salt) => new Promise((ok, no) =>
  crypto.pbkdf2(pw, salt, 600000, 32, "sha256", (e, k) => e ? no(e) : ok(k.toString("hex"))));
const titles = { Todo: "To do", Doing: "Doing", Done: "Done" };

class Refused extends Error { constructor(status, msg) { super(msg); this.status = status; } }

function signedIn(req) {
  const m = /(?:^|;\s*)board_session=([^;]*)/.exec(req.headers.cookie || "");
  const u = m && sessionUser.get(sha256(decodeURIComponent(m[1])));
  if (!u) throw new Refused(401, "not signed in");
  return { id: Number(u.id), name: u.name };
}
function access(req, boardId) {
  const user = signedIn(req);
  const board = boardFind.get(boardId);
  if (!board || !isMember.get(boardId, user.id)) throw new Refused(404, "not found");
  return { user, board };
}
const page = board => ({ board, members: membersOf.all(board.id), cards: cardsOf.all(board.id), events: eventsOf.all(board.id, 30) });

const calls = {
  async sign_up(req, c, res) {
    const name = String(c.name).trim().toLowerCase();
    if (userByName.get(name)) throw new Refused(409, "that name is taken");
    const salt = crypto.randomBytes(16).toString("hex");
    const hash = `pbkdf2_sha256$600000$${salt}$${await pbkdf2(c.password, salt)}`;
    const id = Number(userAdd.run(name, hash).lastInsertRowid);
    const token = crypto.randomBytes(32).toString("hex");
    sessionAdd.run(sha256(token), id, Date.now() / 1000);
    res.setHeader("set-cookie", `board_session=${token}; Path=/; HttpOnly; SameSite=Lax`);
    return { id, name };
  },
  create_board(req, name) {
    const user = signedIn(req);
    const id = Number(boardAdd.run(name, user.id).lastInsertRowid);
    memberAdd.run(id, user.id);
    eventAdd.run(id, user.name, "created the board", Date.now() / 1000);
    return { id, name, owner_id: user.id };
  },
  board(req, id) { return page(access(req, id).board); },
  add_card(req, c) {
    const a = access(req, c.board_id);
    const position = cardsOf.all(a.board.id).filter(x => x.column === c.column).length;
    const id = Number(cardAdd.run(a.board.id, c.title, c.column, position).lastInsertRowid);
    eventAdd.run(a.board.id, a.user.name, `added “${c.title}” to ${titles[c.column]}`, Date.now() / 1000);
    JSON.stringify(page(a.board));       // Klar Board sends the new page to everyone watching the board
    return { id, board_id: a.board.id, title: c.title, notes: "", column: c.column, position, assignee: null };
  },
};

const secure = {
  "x-content-type-options": "nosniff", "x-frame-options": "SAMEORIGIN", "referrer-policy": "strict-origin-when-cross-origin",
  "content-security-policy": "base-uri 'self'; object-src 'none'; frame-ancestors 'self'",
};
const appDir = process.env.APP_DIR || ".";

http.createServer(async (req, res) => {
  for (const k in secure) res.setHeader(k, secure[k]);
  const path = req.url.split("?")[0];
  try {
    if (req.method === "GET" && (path === "/" || path === "/login" || path.startsWith("/boards/"))) {
      const html = await readFile(appDir + "/app.html");
      res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
      return res.end(html);
    }
    if (req.method === "GET" && path === "/healthz") return res.end("ok\n");
    const call = req.method === "POST" && path.startsWith("/api/") && calls[path.slice(5)];
    if (!call) { res.writeHead(404); return res.end("not found\n"); }
    let body = "";
    for await (const chunk of req) body += chunk;
    const out = await call(req, JSON.parse(body || "null"), res);
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify(out, (k, v) => typeof v === "bigint" ? Number(v) : v));
  } catch (e) {
    res.writeHead(e.status || 500, { "content-type": "application/json" });
    res.end(JSON.stringify(e.message));
    if (!e.status) console.error(e);
  }
}).listen(Number(process.env.PORT || 8080), process.env.HOST || "127.0.0.1");
