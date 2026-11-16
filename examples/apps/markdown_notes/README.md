# Markdown notes

A small browser app that uses two npm packages from Klar: [`marked`](https://www.npmjs.com/package/marked)
turns Markdown into HTML, and [`dompurify`](https://www.npmjs.com/package/dompurify) removes scripts from that
HTML, so a note containing `<script>` or `onerror=` is shown safely.

```
klar install                         # npm packages from klar.toml [npm], pinned by package-lock.json
klar dev app.klar                    # http://127.0.0.1:8000
klar build --target web app.klar     # app.html: one offline file with both packages embedded
klar build --target node export.klar && node export.mjs some.md > some.html
klar test app.klar
```

| File | |
|---|---|
| `klar.toml` | `[npm]` pins `marked` and `dompurify` to exact versions |
| `npm/markdown.klar` | the typed declarations this app uses, tightened by hand |
| `app.klar` | the app: model, update, view (the preview calls `render_markdown` from the pure `view`) |
| `export.klar` | the same `marked` binding on Node, as a command-line converter |
| `package.json`, `package-lock.json` | written by `klar install` and npm; the lock pins every package with its hash |

## How the bindings stay safe

`klar bind marked` generates every function as `uses io` returning `Result[T, str]`, because a `.d.ts` file
says neither whether a function has side effects nor whether it throws. `npm/markdown.klar` keeps just the
two functions the app needs and declares them pure and infallible, which is what the packages document.
Klar does not simply trust that: if either ever returned a Promise, threw, or returned null, the program
would stop with E407 at the call instead of carrying a wrong value into Klar code.
