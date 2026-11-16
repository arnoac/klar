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
| `GET /healthz` | 25,728 (5.7 ms) | 20,039 (4.5 ms) | 41,209 (2.6 ms) |
| `GET /`, the app page, gzip | 41,363 (4.9 ms) | 19,636 (3.8 ms) | 6,919 (7.9 ms) |
| `GET /`, no gzip | 28,359 (6.6 ms) | 15,024 (5.1 ms) | 7,202 (6.9 ms) |
| board read (`POST /api/board`: session, membership, 3 queries, 4 KB of JSON) | 5,286 (13.8 ms) | 1,680 (39.5 ms) | 4,758 (13.3 ms) |
| add card (`POST /api/add_card`: 2 inserts, reads the growing board) | 216 (280 ms) | 123 (829 ms) | 237 (379 ms) |
| start to first answer | 19 ms | 152 ms | 100 ms |
| memory idle / after the run | 4 / 79 MB | 75 / 151 MB | 56 / 172 MB |

What it says, honestly:

* **Pages and files**: the executable serves the app 4 to 6 times faster than the hand-written server, because
  the page is built in, sent gzipped (39 KB instead of 162 KB) and revalidated with an ETag. A Node app gets
  the same with a compression middleware and a static-file cache; Klar does it with no code.
* **API calls**: the executable now answers the board read 1.1 times faster than the hand-written server, and
  adds cards at 0.9 times its rate with a lower p99 (280 ms against 379 ms). Klar handlers run one at a time on
  the native server, like Node's single thread; each write reads the whole growing board twice and sends it as
  JSON, so the add-card rate falls as the board grows, for both servers.
* **The tiny endpoint**: Node's event loop answers `/healthz` 1.6 times faster than a thread per connection.
* **Start and memory**: the executable starts in 10 to 20 ms and idles at 4 MB, which matters for scale-to-zero
  hosting and small servers.
* `--target node` is the slowest of the three for API calls: the JavaScript the Klar compiler emits is not yet
  as tight as hand-written code. Prefer the native executable.

## Before this work

The same load generator against Klar Board as it was before the production work (native build, files read from
disk, 5-second runs), after it, and after the API work (10-second runs, as in the table above):

| | before | production build | API work |
|---|---:|---:|---:|
| `GET /healthz` | 5,172 | 25,782 | 25,728 |
| `GET /`, the app page | 3,336 | 40,895 | 41,363 |
| board read | 1,312 | 3,248 | 5,286 |
| add card | 161 (p99 426 ms) | 149 (p99 418 ms) | 216 (p99 280 ms) |

The production build: HTTP/1.1 keep-alive (every request used to open a connection and a thread), the app built
in and gzipped, SQLite in write-ahead-log mode with prepared statements reused, floats written to JSON in at most
three tries instead of up to seventeen, and requests that have waited 50 ms for their turn served in arrival
order (without that, a few writes waited 3 to 4 seconds under this load while the median was 100 ms).

The API work, found with callgrind on the board read: values are written to JSON straight from their type
descriptions instead of building a JSON tree first, strings are escaped in runs rather than a byte at a time,
integers are formatted without `printf`, rows from SQLite get column names made once per query and maps sized
for their columns, and reading rows into typed records finds each field's column once per query and only formats
error text when a conversion fails. Request limits and runtime checks are unchanged. SQLite keeps its default
`synchronous = FULL`, so a committed write survives a power cut; the hand-written server uses the same setting.
`/healthz` costs about 30 microseconds of work in Klar; the rest is handing each connection's thread its turn,
where Node's event loop is faster.
