# Deploying a Klar app

A Klar web app goes to production as **one executable**: `klar build --release` compiles the server to native
code and builds the browser app and any `public/` or `static/` folder into it. Copy that one file to a server
and run it. A site with no server becomes a folder of plain files instead (`klar build --static`).

```sh
klar build --release                 # in a project with server.klar and app.klar: one executable, named after it
klar build --static                  # a folder (dist/) for any static host
klar build --docker                  # a Dockerfile that builds and runs the executable
```

`klar build` picks the files the way `klar dev` does: `main.klar`, `server.klar` and `app.klar` of the project
you are in, or of the folder you name (`klar build --release examples/apps/board`). Name them to choose:
`klar build --release server.klar admin.klar shop.klar`. One of them is the server (it does not `use std.ui`);
the others are browser apps.

## What the executable does

* **Serves the files built into it** before looking on disk. A browser app `app.klar` is built in as
  `app.html`, next to where `klar dev` writes it, so the server's own `web_file(dir + "/app.html")` finds it.
  Files in `public/` and `static/` keep their paths (`web_get("/assets/*", web_static("public"))` works
  unchanged). Built-in files are sent gzipped to browsers that accept it and carry an `ETag`, so a reload
  costs a `304 Not Modified`. Anything else is read from disk as before.
* **Answers `GET /healthz`** with `200 ok` (unless the app has a route of its own there), for load balancers,
  container platforms and uptime checks.
* **Logs one line per request** to stderr: time, method, path (never the query string, which may hold
  tokens), status and duration. `KLAR_LOG=off` silences it. What the program prints goes to stdout.
* **Stops cleanly.** On `SIGTERM` (what `docker stop`, systemd and every platform send) or `Ctrl-C` it stops
  accepting connections, closes live-update streams so browsers reconnect elsewhere, lets the requests in
  flight finish (up to `KLAR_SHUTDOWN_SECONDS`, default 10) and returns from `web_serve`, so code after it
  runs. A second signal exits at once.
* **Keeps connections open** between requests (HTTP/1.1 keep-alive) and closes idle ones after 5 seconds.
* **Uses SQLite safely under load**: every database opens in write-ahead-log mode (readers never wait for the
  writer) and waits up to 5 seconds for a lock instead of failing. Prepared statements are reused.

The security defaults stay on: the server listens on 127.0.0.1 unless `HOST` says otherwise, request bodies
are limited, security headers and Secure cookies are set (see [SECURITY.md](SECURITY.md)).

Install the Boehm garbage collector where you build (`apt install libgc-dev`, `brew install bdw-gc`): without
it a server's memory grows with every request, and `klar build` warns about it.

## Configuration

Everything is set through environment variables, so the same executable runs anywhere.

| Variable | Default | What it does |
|---|---|---|
| `PORT` | the app's `web_port(fallback)` | port to listen on; most platforms set it for you |
| `HOST` | `127.0.0.1` | address to listen on; `0.0.0.0` to accept connections from other machines |
| `KLAR_LOG` | on | `off` stops the request log |
| `KLAR_SHUTDOWN_SECONDS` | `10` | how long a stopping server waits for requests in flight |
| `KLAR_MAX_BODY` | `10485760` | largest request body accepted, in bytes |

Read your own settings with `env_get` (`std.process`), as Klar Board does for its database path:
`unwrap_or(env_get("BOARD_DB"), "board.db")`. Keep secrets (API keys) in environment variables, never in
the source.

## The database

A relative database path is relative to the folder the executable runs in, so choose that folder on purpose:
the container image runs in `/data`, a systemd service in its `WorkingDirectory`. Back it up while the app
runs with `sqlite3 board.db ".backup board-$(date +%F).db"`; the `-wal` and `-shm` files next to it are part of
the database. Keep it on a local disk: SQLite's locking does not work on network file systems.

Change the tables with `sqlite_migrate` when the program starts. Each step runs once, in order, in a
transaction, and a database that has run more steps than the program knows (an older version deployed by
mistake) is refused instead of being damaged:

```klar
fn open_store(path: str) -> Result[Database, str] uses io {
  let db = sqlite_open(path)?
  let _ = sqlite_migrate(db, [
    "create table todos (id integer primary key, title text not null)",
    "alter table todos add column done integer not null default 0",     // added in version 2: always at the end
  ])?
  return Ok(db)
}
```

## Behind a reverse proxy, with TLS

The executable speaks plain HTTP. Put it behind a proxy that holds the certificate. Caddy gets and renews
certificates by itself:

```
# /etc/caddy/Caddyfile
board.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

With nginx, turn off buffering so live updates (server-sent events) arrive at once:

```nginx
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Host $host;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_read_timeout 1h;
}
```

Pass the original `Host` header through (both examples do): the server uses it to refuse cross-site form
posts and to mark cookies `Secure` once the site is not on localhost.

## Where to run it

**A Linux server (VPS) with systemd.** Build on a machine with the same distribution, or in the container
below, then:

```ini
# /etc/systemd/system/board.service
[Unit]
Description=Klar Board
After=network.target

[Service]
ExecStart=/opt/board/board
WorkingDirectory=/var/lib/board
StateDirectory=board
DynamicUser=yes
Environment=PORT=8080
Restart=on-failure
NoNewPrivileges=yes
ProtectSystem=strict

[Install]
WantedBy=multi-user.target
```

`systemctl enable --now board`, and Caddy in front for TLS. `journalctl -u board` shows the request log.

**Any container platform** (Fly.io, Railway, Render, Koyeb, a Kubernetes cluster, your own Docker host).
`klar build --docker` writes a `Dockerfile` (it never replaces one you have): it compiles the app with the
Klar version you have, pinned to its commit, and runs the executable as an unprivileged user with
`HOST=0.0.0.0`, port 8080 and the working directory `/data` declared as a volume. The image is about 130 MB
on disk (32 MB to download), mostly Ubuntu's base layer.

```sh
docker build -t board .
docker run -p 8080:8080 -v board-data:/data board
```

Give the platform a persistent volume mounted at `/data` for the SQLite file, point its health check at
`/healthz`, and let it terminate TLS. Platforms that scale to zero or replace disks on every deploy (Cloud
Run, Heroku-style dynos) lose a SQLite file: use them only for apps without one.

**Node.js hosting.** `klar build --target node` (with the same files) makes one `.mjs` file with the browser
app and public files built in, the same health check, log and clean shutdown: `node board.mjs`. It needs
Node.js 22 or newer and no `npm install` unless the app uses npm packages.

**Static hosts** (GitHub Pages, Netlify, Cloudflare Pages, S3 and the like) for sites with no server.
`klar build --static` writes `dist/`:

* For browser apps alone (`std.ui`), the app becomes `index.html`, and also `404.html` so that hosts which
  serve that page for unknown paths still open the app on its own routes. `public/` and `static/` are copied
  to the top of the folder.
* For a site made with `std.web`, it runs the server, fetches `/` and every page and file linked from there,
  and saves them (`/about` as `about/index.html`). Forms that post and pages behind a query string need a
  server, so they are listed as skipped.

Upload the folder, or point the host at it. `klar build --static` refuses to write into a folder it did not
make, and rebuilds its own from scratch.

## Checklist

1. `klar test` and `klar audit` pass.
2. `klar build --release` on Linux with `libgc-dev` installed (or the Dockerfile).
3. `HOST=0.0.0.0` only when nothing but the proxy or the platform can reach the port.
4. The database on a persistent local disk, with a backup schedule.
5. TLS at the proxy or the platform, with the `Host` header passed through.
6. The platform's health check on `/healthz`.
