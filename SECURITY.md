# Security

Klar aims to make the secure way the default way: web defaults that block common attacks, compile errors for
the most common injection mistake, pinned and verified dependencies, and a command that tells you when
something you depend on has a known vulnerability. This page says how to report a problem, how fixes reach
you, and what Klar does and does not protect against today.

## Reporting a vulnerability

Please report security problems privately, not in a public issue:
**[Report a vulnerability](https://github.com/arnoac/klar/security/advisories/new)** (GitHub's private
vulnerability reporting, under the repository's Security tab).

Include what is affected (the interpreter, native builds, the JavaScript backends, a std module, the package
manager, `klar dev`), a small program or request that shows the problem, and what an attacker gains. You will
get an answer within a few days. Once a fix is ready we publish it together with a GitHub security advisory
and credit you, unless you prefer not to be named. Please give us a reasonable time to fix the problem before
you disclose it publicly.

Only the latest version on `main` is supported; fixes are not backported.

## How fixes reach you

1. A fix lands on `main` and a **security advisory** is published on this repository, naming the affected
   versions of Klar.
2. **`klar audit`** reads those advisories, so every project that runs it learns about the problem: it
   checks the Klar you are running (`klar --version`) and all of your dependencies.
3. You upgrade Klar (`git pull` in your Klar checkout) and run `klar audit` again.

The same command covers your dependencies. `klar audit` looks up every package in `klar.lock` (Python
packages, git dependencies by commit) and `package-lock.json` (every npm package, including the ones your
packages pull in) in the [OSV](https://osv.dev) database, which collects advisories from GitHub, PyPI, npm
and others. For each problem it shows the severity, the advisory and the command that fixes it, for example
`klar add --python jinja2==2.11.3`.

Run it in CI so a new advisory fails your build the day it is published:

```sh
klar audit          # exit 0: nothing known; 1: vulnerabilities found; 2: the check could not complete
klar audit --json   # the same as a machine-readable report
```

Exit status 2 (for example, no network) is not "all clear": treat it as a failure.

## What Klar does by default

**Web apps (`std.web`, `std.html`)**

- Text in pages is escaped. Event-handler attributes (`onclick=...`) are never rendered from attribute
  lists, and `href`/`src`-like attributes holding `javascript:`, `vbscript:` or non-image `data:` URLs render
  as `about:invalid`. `raw_html` is the one way around this; never give it user input.
- A `POST`, `PUT`, `PATCH` or `DELETE` sent by a page on another site gets 403 (cross-site request forgery).
  Routes that other sites may call (public APIs, webhooks) opt out with `web_cross_origin(route)` and must
  not rely on cookies to identify the caller.
- Responses carry `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`,
  `Referrer-Policy: strict-origin-when-cross-origin` and a `Content-Security-Policy` limiting `base-uri`,
  `object-src` and `frame-ancestors`, unless the handler sets its own.
- Cookies are `HttpOnly` and `SameSite=Lax`, and `Secure` whenever the site is served beyond localhost.
- `web_redirect` only redirects to paths on the same site; `web_redirect_external` is the explicit way out.
- `web_static` and `web_safe_path` refuse `..`, absolute paths, backslashes and hidden files (`.env`, `.git`).
- Servers in every backend listen on 127.0.0.1 unless `HOST` says otherwise, refuse request bodies over
  `KLAR_MAX_BODY` bytes (default 10 MiB), malformed `Content-Length`, chunked request bodies and oversized
  headers, give clients 30 seconds to send a request, and drop response headers that contain line breaks.
- `klar dev` only answers requests addressed to localhost and never serves hidden files.

**Databases (`std.sqlite`)**: values go in `?` parameters. Passing SQL built from strings (interpolation or
`+`) to a parameter named `sql` is compile error E313.

**Passwords and tokens (`std.crypto`)**: `crypto_password_hash` uses PBKDF2-HMAC-SHA-256 with 600,000
iterations and a random salt; `crypto_password_verify` compares in constant time and still accepts hashes made
with fewer iterations (`crypto_password_needs_rehash` tells you to re-hash them at the next login).
`crypto_token` reads the operating system's secure random source and needs at least 16 bytes. `std.random`
is not for secrets.

**Native builds**: list and string indexes are bounds-checked, integer overflow and division by zero stop the
program, and every allocation is checked, so size mistakes stop with an error instead of corrupting memory.

**Dependencies**

- Git dependencies are pinned to a commit in `klar.lock`; a checkout at another commit is refused.
- Python packages are pinned to exact versions with the sha256 of every file in `klar.lock` (including the
  packages they pull in), installed with hash checking, and only as prebuilt wheels, so no package code runs
  during installation.
- npm packages are pinned to exact versions; `package-lock.json` holds the integrity hash of every package
  and `npm ci` refuses a changed one. Install scripts never run.
- Commit `klar.lock` and `package-lock.json`.

## Known limits

Klar is young and has not had an independent security review. Some things it does not do yet:

- Python externs and npm packages run with your program's full permissions. A malicious or compromised
  package can do anything your program can; pinning and hashes stop silent changes, not a bad release you
  choose to install. Read what you add.
- Git dependencies are verified by commit id only; files changed by hand inside `.klar/deps` are not detected.
- `std.regex` patterns are matched by backtracking engines; a pathological pattern on untrusted input can be
  slow. Keep patterns simple when the input comes from users.
- There are no rate limits, no login lockout and no HTTPS server: put a reverse proxy in front of a public
  server for TLS, and add `Strict-Transport-Security` there.
- Content-Security-Policy is deliberately loose (Klar's own pages use inline styles and scripts); tighten it
  per app if you can.
- `raw_html`, `web_redirect_external` and `web_cross_origin` turn protections off on purpose.

If you find a gap that is not on this list, please report it as described above.
