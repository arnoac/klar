# Klar Board in production, under load

Klar Board (`examples/apps/board`) run from its production build and load-tested against the same endpoints
written by hand for Node.js. Reproduce with `python3 bench/deploy/run.py` (needs gcc or clang, `libgc-dev`,
Node.js 22+ and Go for the load generator).

* **Klar, native executable**: `klar build --release examples/apps/board`, one 378 KB file with the browser
  app built in.
* **Klar, `--target node`**: the same Klar program built to one `.mjs` file, run by Node.
* **hand-written Node.js**: [board_node.mjs](board_node.mjs), the endpoints below with `node:http` and
  `node:sqlite` and no packages: the same schema, queries, session scheme, password hashing and security
  headers, serving `app.html` from disk the way a small Express app would (no compression).

[load.go](load.go) signs up a user, creates a board with 20 cards and sends one kind of request from 32
connections for 10 seconds, keeping connections open and accepting gzip as browsers do (except the
"no gzip" row). Each server starts with an empty database. The machine: 4 cores, Linux, Node 22, gcc -O2;
the load generator runs on the same machine.

| requests per second (p99 latency) | Klar, native executable | Klar, `--target node` | hand-written Node.js |
|---|---:|---:|---:|
| `GET /healthz` | 25,782 (5.7 ms) | 19,788 (4.7 ms) | 41,172 (2.5 ms) |
| `GET /`, the app page, gzip | 40,895 (4.8 ms) | 19,206 (4.2 ms) | 7,110 (7.7 ms) |
| `GET /`, no gzip | 28,764 (6.4 ms) | 14,199 (5.3 ms) | 6,957 (7.5 ms) |
| board read (`POST /api/board`: session, membership, 3 queries, 4 KB of JSON) | 3,248 (22.7 ms) | 1,739 (37.8 ms) | 4,612 (13.8 ms) |
| add card (`POST /api/add_card`: 2 inserts, reads the growing board) | 149 (418 ms) | 125 (834 ms) | 238 (362 ms) |
| start to first answer | 10 ms | 153 ms | 78 ms |
| memory idle / after the run | 4 / 77 MB | 74 / 152 MB | 55 / 174 MB |

What it says, honestly:

* **Pages and files**: the executable serves the app 4 to 6 times faster than the hand-written server, because
  the page is built in, sent gzipped (39 KB instead of 162 KB) and revalidated with an ETag. A Node app gets
  the same with a compression middleware and a static-file cache; Klar does it with no code.
* **API calls**: the hand-written Node server answers the board read 1.4 times faster and adds cards 1.6 times
  faster. Klar handlers run one at a time on the native server, and turning SQLite rows into typed records and
  back into JSON costs more than V8's hand-tuned `JSON.stringify` of plain objects. At one connection the two
  are level (about 2,200 reads per second each); the gap is throughput under concurrency.
* **The tiny endpoint**: Node's event loop answers `/healthz` 1.6 times faster than a thread per connection.
* **Start and memory**: the executable starts in 10 ms and idles at 4 MB, which matters for scale-to-zero
  hosting and small servers.
* `--target node` is the slowest of the three for API calls: the JavaScript the Klar compiler emits is not yet
  as tight as hand-written code. Prefer the native executable.

## Before this work

The same load generator against Klar Board as it was (native build, files read from disk, 5-second runs):

| | before | now |
|---|---:|---:|
| `GET /healthz` | 5,172 | 25,782 |
| `GET /`, the app page | 3,336 | 40,895 |
| board read | 1,312 | 3,248 |
| add card | 161 (p99 426 ms) | 149 (p99 418 ms) |

What changed: HTTP/1.1 keep-alive (every request used to open a connection and a thread), the app built in and
gzipped, SQLite in write-ahead-log mode with prepared statements reused, floats written to JSON in at most
three tries instead of up to seventeen, and requests that have waited 50 ms for their turn served in arrival
order (without that, a few writes waited 3 to 4 seconds under this load while the median was 100 ms).
