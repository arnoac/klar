// Runs a web task's browser tests: node browser.mjs ROOT PAGE TESTS.mjs
// Serves ROOT over HTTP (so localStorage works), opens PAGE in a fresh headless Chromium context for each test
// exported by TESTS.mjs, and prints {"test name": {"ok": bool, "message": str}} as JSON.
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const [root, page, testsFile] = process.argv.slice(2);
const require = createRequire(import.meta.url);
let playwright;
try { playwright = require("playwright"); }
catch { playwright = require(path.join(require("child_process").execSync("npm root -g").toString().trim(), "playwright")); }

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
  ".wasm": "application/wasm", ".json": "application/json" };
const server = http.createServer((req, res) => {
  const p = path.join(root, decodeURIComponent(new URL(req.url, "http://x").pathname));
  if (!p.startsWith(root) || !fs.existsSync(p) || fs.statSync(p).isDirectory()) { res.writeHead(404); res.end(); return; }
  res.writeHead(200, { "content-type": TYPES[path.extname(p)] ?? "application/octet-stream" });
  fs.createReadStream(p).pipe(res);
});
await new Promise(r => server.listen(0, "127.0.0.1", r));
const url = `http://127.0.0.1:${server.address().port}/${page}`;

const { tests } = await import(pathToFileURL(testsFile).href);
const browser = await playwright.chromium.launch();
const results = {};
for (const [name, fn] of Object.entries(tests)) {
  const context = await browser.newContext();
  const pg = await context.newPage();
  const errors = [];
  pg.on("pageerror", e => errors.push(String(e)));
  pg.setDefaultTimeout(4000);
  try {
    await pg.goto(url);
    await fn(pg, { url, errors });
    results[name] = { ok: true };
  } catch (e) {
    const msg = String(e && e.message || e).split("\n").filter(l => l.trim() && !l.includes("Call log")).slice(0, 3).join(" ");
    results[name] = { ok: false, message: msg + (errors.length ? ` (page errors: ${errors.slice(0, 2).join("; ")})` : "") };
  }
  await context.close();
}
await browser.close();
server.close();
console.log(JSON.stringify(results));
