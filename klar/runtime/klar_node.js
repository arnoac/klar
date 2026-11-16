// Klar runtime: Node.js target (servers, command-line tools).

function $write(fd, s) {
  const buf = Buffer.from(s, "utf8");
  let off = 0;
  while (off < buf.length) {
    try { off += $fs.writeSync(fd, buf, off, buf.length - off); }
    catch (e) { if (e.code !== "EAGAIN") throw e; }
  }
}
function $flush() {
  if ($out.length === 0) return;
  const s = $out.join("\n") + "\n";
  $out = []; $out_len = 0;
  $write(1, s);
}

let $in_buf = Buffer.alloc(0), $in_eof = false;
function $read_line() {
  for (;;) {
    const nl = $in_buf.indexOf(10);
    if (nl >= 0) {
      const line = $in_buf.subarray(0, nl).toString("utf8");
      $in_buf = $in_buf.subarray(nl + 1);
      return $Some(line);
    }
    if ($in_eof) {
      if ($in_buf.length === 0) return $None;
      const line = $in_buf.toString("utf8");
      $in_buf = Buffer.alloc(0);
      return $Some(line);
    }
    $flush();
    const chunk = Buffer.alloc(65536);
    let n = 0;
    try { n = $fs.readSync(0, chunk, 0, chunk.length, null); }
    catch (e) {
      if (e.code === "EAGAIN") { Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 5); continue; }
      if (e.code === "EOF") n = 0; else throw e;
    }
    if (n === 0) $in_eof = true; else $in_buf = Buffer.concat([$in_buf, chunk.subarray(0, n)]);
  }
}

const $ERRNO = { ENOENT: [2, "No such file or directory"], EACCES: [13, "Permission denied"], EISDIR: [21, "Is a directory"],
  ENOTDIR: [20, "Not a directory"], EEXIST: [17, "File exists"], ENOTEMPTY: [39, "Directory not empty"], EPERM: [1, "Operation not permitted"] };
function $oserr(e, path) {
  const known = $ERRNO[e.code];
  return known ? `[Errno ${known[0]}] ${known[1]}: '${path}'` : `${e.code || "Error"}: ${e.message}`;
}
const $PYERR = { ENOENT: "FileNotFoundError", EACCES: "PermissionError", EPERM: "PermissionError", EISDIR: "IsADirectoryError",
  ENOTDIR: "NotADirectoryError", EEXIST: "FileExistsError" };
function $pyerr(e, path) { return `${$PYERR[e.code] || "OSError"}: ${$oserr(e, path)}`; }
function $read_file(p) { try { return $Ok($fs.readFileSync(p, "utf8")); } catch (e) { return $Err($oserr(e, p)); } }
function $write_file(p, s) { try { $fs.writeFileSync(p, s); return $Ok(undefined); } catch (e) { return $Err($oserr(e, p)); } }
let $argv = [];
function $args() { return $L($argv.slice()); }

// ---------------------------------------------------------------- standard library (raw Klar values)
function $tryio(f, path) { try { return $Ok(f()); } catch (e) { return $Err($pyerr(e, path)); } }
function $http_response(status, headers, body) {
  return new $structs.HttpResponse(status, headers, body);
}
let $sqlite = null;
const $PBKDF2_ITERS = 600000;

Object.assign($std, {
  "klar.stdpy.fslib.read": p => $tryio(() => $fs.readFileSync(p, "utf8"), p),
  "klar.stdpy.fslib.write": (p, s) => $tryio(() => { $fs.writeFileSync(p, s); }, p),
  "klar.stdpy.fslib.append": (p, s) => $tryio(() => { $fs.appendFileSync(p, s); }, p),
  "klar.stdpy.fslib.exists": p => $fs.existsSync(p),
  "klar.stdpy.fslib.is_dir": p => { try { return $fs.statSync(p).isDirectory(); } catch { return false; } },
  "klar.stdpy.fslib.list_dir": p => $tryio(() => $L($fs.readdirSync(p).sort()), p),
  "klar.stdpy.fslib.mkdir": p => $tryio(() => { $fs.mkdirSync(p, { recursive: true }); }, p),
  "klar.stdpy.fslib.remove": p => $tryio(() => { $fs.unlinkSync(p); }, p),
  "klar.stdpy.fslib.join": parts => $arr(parts).reduce((acc, p) => p.startsWith("/") ? p : acc === "" || acc.endsWith("/") ? acc + p : acc + "/" + p, ""),

  "klar.stdpy.processlib.run": (cmd, input) => {
    const a = $arr(cmd);
    const r = $child.spawnSync(a[0], a.slice(1), { input, encoding: "utf8" });
    if (r.error) return $Err(`FileNotFoundError: [Errno 2] No such file or directory: '${a[0]}'`);
    return $Ok(new $structs.ProcessResult(r.status ?? 1, r.stdout, r.stderr));
  },
  "klar.stdpy.cryptolib.sha256": t => $crypto.createHash("sha256").update(t, "utf8").digest("hex"),
  "klar.stdpy.cryptolib.hmac_sha256": (k, t) => $crypto.createHmac("sha256", Buffer.from(k, "utf8")).update(t, "utf8").digest("hex"),
  "klar.stdpy.cryptolib.token": n => {
    if (!(n >= 16 && n <= 1048576)) throw new Error(`ValueError: crypto_token(${n}): use between 16 and 1048576 bytes (16 bytes = 128 bits is the least that cannot be guessed)`);
    return $crypto.randomBytes(Number(n)).toString("hex");
  },
  "klar.stdpy.cryptolib.password_hash": p => {
    const salt = $crypto.randomBytes(16).toString("hex");
    return `pbkdf2_sha256$${$PBKDF2_ITERS}$${salt}$${$crypto.pbkdf2Sync(Buffer.from(p, "utf8"), Buffer.from(salt, "utf8"), $PBKDF2_ITERS, 32, "sha256").toString("hex")}`;
  },
  "klar.stdpy.cryptolib.needs_rehash": stored => {
    const parts = stored.split("$");
    return !(parts.length === 4 && parts[0] === "pbkdf2_sha256" && /^[0-9]+$/.test(parts[1]) && Number(parts[1]) >= $PBKDF2_ITERS);
  },
  "klar.stdpy.cryptolib.password_verify": (p, stored) => {
    const parts = stored.split("$");
    if (parts.length !== 4 || parts[0] !== "pbkdf2_sha256" || !/^[0-9]+$/.test(parts[1])) return false;
    const n = Number(parts[1]);
    if (!(n > 0 && n <= 10000000)) return false;
    const got = $crypto.pbkdf2Sync(Buffer.from(p, "utf8"), Buffer.from(parts[2], "utf8"), n, 32, "sha256").toString("hex");
    return got.length === parts[3].length && $crypto.timingSafeEqual(Buffer.from(got), Buffer.from(parts[3]));
  },
  "klar.stdpy.cryptolib.equal": (a, b) => {
    const x = Buffer.from(a, "utf8"), y = Buffer.from(b, "utf8");
    return x.length === y.length && $crypto.timingSafeEqual(x, y);
  },
  "klar.stdpy.processlib.env_get": n => (n in process.env ? $Some(process.env[n]) : $None),
  "klar.stdpy.processlib.exit": c => { $flush(); process.exit(c); },

  "klar.stdpy.sqlitelib.open_db": async p => {
    try {
      if (!$sqlite) {
        const emit = process.emitWarning;
        process.emitWarning = () => {};
        $sqlite = await import("node:sqlite");
        process.emitWarning = emit;
      }
      return $Ok(new $sqlite.DatabaseSync(p));
    } catch (e) { return $Err(`OperationalError: ${e.message}`); }
  },
  "klar.stdpy.sqlitelib.execute": (db, sql, params) => {
    if ($sql_many(sql)) return $Err("ProgrammingError: You can only execute one statement at a time.");
    try { const st = db.prepare(sql), r = st.run(...$arr(params).map($sql_in)); return $Ok(/^\s*(select|with|pragma)\b/i.test(sql) ? 0 : Number(r.changes)); }
    catch (e) { return $Err($sql_err(e)); }
  },
  "klar.stdpy.sqlitelib.query": (db, sql, params) => {
    if ($sql_many(sql)) return $Err("ProgrammingError: You can only execute one statement at a time.");
    try {
      const st = db.prepare(sql);
      const types = st.columns ? Object.fromEntries(st.columns().map(c => [c.name, (c.type || "").toUpperCase()])) : {};
      const rows = st.all(...$arr(params).map($sql_in));
      return $Ok($L(rows.map(r => $M(Object.entries(r).map(([k, v]) => [k, $sql_out(v, types[k])])))));
    } catch (e) { return $Err($sql_err(e)); }
  },
  "klar.stdpy.sqlitelib.last_insert_id": db => Number(db.prepare("select last_insert_rowid() as id").get().id),
  "klar.stdpy.sqlitelib.close": db => { db.close(); },

  "klar.stdpy.httplib.request": async (method, url, headers, body) => {
    if (!/^https?:\/\//i.test(url)) return $Err(`ValueError: unknown url type: '${url}' (http_request only fetches http:// and https:// URLs)`);
    try {
      const r = await fetch(url, { method, headers: Object.fromEntries($entries(headers)), body: body === "" ? undefined : body });
      const hs = $M([...r.headers.entries()].map(([k, v]) => [k.toLowerCase(), v]));
      return $Ok($http_response(r.status, hs, await r.text()));
    } catch (e) { return $Err(`URLError: ${e.cause ? e.cause.message : e.message}`); }
  },
  "klar.stdpy.httplib.serve": (port, handler, max) => $serve(port, handler, max),

  "klar:web.publish": (topic, data) => $publish(topic, data),
  "klar:ui.load": () => $None,
  "klar:ui.start": () => $fail("E407", 0, 0, "std.ui apps run in a web browser", "build with `klar build --target web`"),
});
function $sql_in(v) { return v.t === "SqlNull" ? null : v.f[0]; }
function $sql_err(e) {
  const kind = (e.errcode & 0xff) === 19 ? "IntegrityError" : e.code === "ERR_SQLITE_ERROR" ? "OperationalError" : "Error";
  return `${kind}: ${e.message}`;
}
// more than one statement (Python's sqlite3 refuses those in execute)
function $sql_many(sql) {
  let q = null;
  for (let i = 0; i < sql.length; i++) {
    const c = sql[i];
    if (q) { if (c === q) q = null; continue; }
    if (c === "'" || c === '"' || c === "`") q = c;
    else if (c === "-" && sql[i + 1] === "-") { while (i < sql.length && sql[i] !== "\n") i++; }
    else if (c === ";" && /[^\s;]/.test(sql.slice(i + 1))) return true;
  }
  return false;
}
function $sql_out(v, type) {
  if (v === null) return new KE("SqlNull", []);
  if (typeof v === "bigint") return new KE("SqlInt", [Number(v)]);
  if (typeof v === "number") return Number.isInteger(v) && !/REAL|FLOA|DOUB/.test(type || "") ? new KE("SqlInt", [v]) : new KE("SqlFloat", [v]);
  if (v instanceof Uint8Array) return new KE("SqlText", [Buffer.from(v).toString("utf8")]);
  return new KE("SqlText", [String(v)]);
}
// Live updates: a response with the x-klar-stream header (web_events) stays open as a server-sent event stream
// on that topic, and web_publish writes to every stream of a topic.
const $topics = new Map();
function $publish(topic, data) {
  const subs = $topics.get(topic);
  if (!subs) return;
  const msg = String(data).split("\n").map(l => "data: " + l).join("\n") + "\n\n";
  for (const res of subs) res.write(msg);
}
function $stream(res, topic) {
  res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-store", "connection": "keep-alive" });
  res.write(": connected\n\n");
  let subs = $topics.get(topic);
  if (!subs) $topics.set(topic, subs = new Set());
  subs.add(res);
  const ping = setInterval(() => res.write(": ping\n\n"), 15000);
  res.on("close", () => { clearInterval(ping); subs.delete(res); });
}
// Request limits, the same in every backend: bodies up to KLAR_MAX_BODY bytes (default 10 MiB), 30 s to send a
// request, 1024 open connections.
function $max_body() { const n = Number(process.env.KLAR_MAX_BODY || 0); return n > 0 ? n : 10 * 1024 * 1024; }
// a header name or value that would end the header line early (response splitting)
function $bad_header(s, name) { return /[\r\n\0]/.test(s) || (name && !/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(s)); }
function $refuse(res, status) {
  res.writeHead(status, { "content-type": "text/plain; charset=utf-8", "content-length": "0", "connection": "close" });
  res.end();
}
// HTTP server: each request is handled as it arrives; handlers are async, so requests interleave at io calls.
function $serve(port, handler, max) {
  $flush();
  return new Promise(resolve => {
    let served = 0;
    const server = $http.createServer({ requestTimeout: 30000, headersTimeout: 30000, maxHeaderSize: 65536 }, (req, res) => {
      if ("transfer-encoding" in req.headers) return $refuse(res, 501);
      const limit = $max_body();
      if (Number(req.headers["content-length"] || 0) > limit) return $refuse(res, 413);
      const chunks = [];
      let size = 0;
      req.on("data", c => {
        size += c.length;
        if (size > limit) { req.destroy(); return; }
        chunks.push(c);
      });
      req.on("end", () => respond().catch(e => {
        $write(2, $render_error(e) + "\n");
        if (!res.headersSent) $refuse(res, 500); else res.destroy();
      }));
      const respond = async () => {
        const u = new URL(req.url, "http://x");
        const query = $M([...u.searchParams]);
        const headers = $M(Object.entries(req.headers).map(([k, v]) => [k, Array.isArray(v) ? v.join(", ") : v]));
        // the path as sent (still percent-encoded), as the other backends see it; std.web decodes parameters
        const kreq = new $structs.HttpRequest(req.method, u.pathname, query, headers,
          Buffer.concat(chunks).toString("utf8"), $M([]));
        let status = 500, hs = { "content-type": "text/plain; charset=utf-8" }, body = "internal error\n";
        try {
          const r = await handler(kreq);
          status = r.status; body = r.body;
          hs = Object.fromEntries($entries(r.headers));
          hs["content-type"] ??= "text/plain; charset=utf-8";
        } catch (e) {
          $flush();
          $write(2, $render_error(e) + "\n");
        }
        if ("x-klar-stream" in hs) {
          $stream(res, hs["x-klar-stream"]);
          $flush();
          served++;
          if (max > 0 && served >= max) server.close(() => resolve($Ok(undefined)));
          return;
        }
        let payload = Buffer.from(body, "utf8");
        if ("x-klar-file" in hs) {
          const p = hs["x-klar-file"];
          delete hs["x-klar-file"];
          try { payload = $fs.readFileSync(p); }
          catch { status = 404; payload = Buffer.from("not found\n"); hs = { "content-type": "text/plain; charset=utf-8" }; }
        }
        const out = {};
        for (const [k, v] of Object.entries(hs)) {
          if ($bad_header(k, true)) { $write(2, "klar: dropped a response header with an invalid name\n"); continue; }
          const lines = (k === "set-cookie" ? v.split("\n") : [v]).filter(line => {
            if (!$bad_header(line, false)) return true;
            $write(2, `klar: dropped response header \`${k}\`: its value contains a line break\n`);
            return false;
          });
          if (lines.length) out[k] = k === "set-cookie" ? lines : lines[0];
        }
        out["content-length"] = String(payload.length);
        res.writeHead(status, out);
        res.end(req.method === "HEAD" ? undefined : payload);
        $flush();
        served++;
        if (max > 0 && served >= max) server.close(() => resolve($Ok(undefined)));
      };
    });
    server.maxConnections = 1024;
    server.on("error", e => resolve($Err(`OSError: ${e.message}`)));
    server.listen(port, process.env.HOST || "127.0.0.1");
  });
}

// ---------------------------------------------------------------- entry point
// The program runs in a worker thread with a 1 GB stack, so deep recursion behaves like the interpreter.
async function $start(main) {
  if ($wt.isMainThread && !process.env.KLAR_NO_WORKER) {
    const w = new $wt.Worker(new URL(import.meta.url), {
      argv: process.argv.slice(2), resourceLimits: { stackSizeMb: 1024, maxOldGenerationSizeMb: 8192 },
      stdin: false, stdout: false, stderr: false,
    });
    w.on("exit", c => { process.exitCode = c; });
    w.on("error", e => { $write(2, $render_error(e) + "\n"); process.exitCode = 1; });
    return;
  }
  $argv = process.argv.slice(2);
  let finished = false;
  process.on("exit", () => {          // the event loop ran dry while tasks still wait: a deadlock
    if (finished || !$task.waiting) return;
    const [what, l, c] = $task.where;
    try { $flush(); } catch {}
    $write(2, $render_error(new KlarError("E409", l, c, `deadlock: every task is waiting (${what})`,
      "a value that is never sent, a channel that is never closed, or tasks waiting on each other")) + "\n");
    process.exitCode = 1;
  });
  try {
    await main();
    finished = true;
    $flush();
  } catch (e) {
    try { $flush(); } catch {}
    $write(2, $render_error(e) + "\n");
    process.exit(1);
  }
}
