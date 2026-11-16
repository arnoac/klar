# Klar's website

The documentation site and the browser playground, published with GitHub Pages by
`.github/workflows/pages.yml`. The site is a Klar program: `build.klar` renders every page with `std.html`,
`std.layout` and `std.kit`.

```sh
site/build.sh                      # builds into site/_site (needs git, curl and Python 3.10+)
cd site/_site && python3 -m http.server 8000
```

| Path | What it is |
|---|---|
| `build.klar` | the generator: landing page, guides, references, playground page, `llms.txt`, `site.css` |
| `md.klar`, `highlight.klar` | Markdown (the subset the docs use) to `std.html`, and Klar syntax highlighting |
| `stdref.klar` | the standard library reference, read from `klar/std/*.klar` and `klar/prelude.py` |
| `content/` | the guides (`start.md`, `tutorial.md`); `<!-- include FILE#section -->` pulls in tested code |
| `tutorial/` | the tutorial's app, tested by `tests/test_site.py` |
| `examples/` | the landing page's code: a sample and a mistake (its errors come from the real checker) |
| `playground/` | the playground: `playground.js` (editor and runner), `compiler-worker.js`, `examples/` |

The language reference page is `klar/SPEC.md` itself, and `llms.txt` is that reference plus every library
signature, so nothing is written twice.

## The playground

The playground runs the real Klar toolchain in the browser: CPython compiled to WebAssembly
([Pyodide](https://pyodide.org), core files only, version and SHA-256 pinned in `build.sh`) loads the `klar`
package from this commit (`playground/klar.zip`, made with `git archive`). It checks, compiles to JavaScript,
formats and runs tests in a Web Worker; nothing is sent to a server.

Programs run in an `<iframe sandbox="allow-scripts">` with an opaque origin and a Content-Security-Policy
that allows no network requests. Console programs run in a worker inside that frame, so an endless loop is
stopped after 10 seconds without freezing the page; browser apps run in the frame itself. Tests run in the
interpreter inside the compiler's worker, which is restarted after 20 seconds.

Limits: the first visit downloads about 6 MB (compressed; cached afterwards) before the compiler is ready; one file at a time;
no files, processes, databases, servers, npm packages or Python externs; `read_line` reads nothing.
