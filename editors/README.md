# Klar in your editor

`klar lsp` is a language server (LSP over stdin/stdout) and `klar debug --dap` a debug adapter (the Debug Adapter
Protocol). Any editor that speaks them gets:

* **errors while you type**: the same errors as `klar check`, with their codes and hints;
* **quick fixes** that apply the fix exactly: a misspelled name (`did you mean area?`), a missing import (also for
  names from the standard library you have not imported yet), `uses io` missing from a function, `let` that needs
  to be `var`, the missing arms of a `match`, `True` for `true`;
* **hover**: the type of any variable, field, tuple element or expression name; signatures and doc comments of
  functions, with the JavaScript, WebAssembly or Python target of an `extern`;
* **types inline** (inlay hints) after every `let`, `var` and pattern name the checker inferred;
* **go to definition**, **find references** and **rename** across every file of the program, the standard library
  included (it can be read, not renamed); **workspace symbols** search every module of the project;
* **completion** that knows what fits: inside `button([...])` the attribute functions (`variant`, `size`, `tooltip`
  from `std.kit`...) come first, inside `variant(` the variants of `Variant`, and at the start of a `match` arm the
  cases not covered yet. Names from modules you have not imported are offered too, and picking one adds the `use`.
  Struct fields and functions after `x.`, module paths after `use std.`, and names inside `use std.json { ... }`;
* **signature help** inside calls, **document outline**, **format document** (`klar fmt`; on save in VS Code);
* **Run / Debug** links above `fn main` and **Run tests** above the first `test`;
* a **debugger** for programs run by the interpreter: breakpoints, step over, into and out, pause, the call stack,
  variables (structs, enums, lists and maps open up) and watch expressions like `card.title` or `cards.0`. When a
  program stops with an error, the debugger stops there with every frame's variables intact.

Unsaved buffers are used in place of the files on disk, so an edit in one module shows up at once in the files
that import it. Analysis takes a few milliseconds to about 50 ms for the largest example app.

## Without an editor (agents, scripts, CI)

The same information is one command away, with no server to run:

```sh
klar check --json               # every error with code, position, hint and exact fixes ("fixes": [{edits}])
klar check --fix                # apply the fixes Klar is sure of, then report what is left
klar check main.klar --at 12:7  # what hover shows: the type or signature, docs, definition, references
klar debug main.klar --break 12 # run; at each pass through line 12 print the stack and every variable
klar debug main.klar            # run; on an error print the variables of each frame it went through
```

Add `--json` to `--at` and `debug` for JSON (one object per stop, on stderr, for `debug`).

Programs built for JavaScript (`klar build --target node|web`) report runtime errors with Klar files, lines and
function names (`at pick (lib.klar:3)`), in Node, in the browser console and in the error box `std.ui` shows. They
also carry a source map, so browser devtools and `node --enable-source-maps` show Klar lines.

In every snippet below, use `klar` if it is on your PATH (see the main README), or the full path to `klarc` in your
clone.

## VS Code

Install the extension from a file (no marketplace needed):

```sh
cd editors/vscode
npm ci                          # vscode-languageclient, pinned in package-lock.json
npx @vscode/vsce package        # makes klar-0.11.0.vsix
code --install-extension klar-0.11.0.vsix
```

If `klar` is not on your PATH, set `"klar.command": ["/path/to/klar/klarc"]` in your settings (or
`["python3", "-m", "klar"]`). Press F5 in a `.klar` file to debug it, or add a launch configuration:

```json
{ "type": "klar", "request": "launch", "name": "Debug server", "program": "${workspaceFolder}/server.klar",
  "args": [], "stopOnEntry": false }
```

### Publishing to the marketplace

The extension is ready to publish; these steps need the owner's accounts, so nothing is published from here:

1. Create a publisher at <https://marketplace.visualstudio.com/manage> (sign in with a Microsoft account). Its ID
   must match `"publisher"` in `package.json` (`klar-lang`); change one or the other.
2. Upload `klar-0.11.0.vsix` on that page ("New extension" > "Visual Studio Code"). No token is needed for a manual
   upload. To publish from the command line instead, create an Azure DevOps personal access token with the
   Marketplace (Manage) scope and run `npx @vscode/vsce login klar-lang`, then `npx @vscode/vsce publish`.
3. Optionally also publish to Open VSX (used by VSCodium, Cursor and others): create an account at
   <https://open-vsx.org>, then `npx ovsx create-namespace klar-lang -p TOKEN` and `npx ovsx publish klar-0.11.0.vsix -p TOKEN`.

Each new version needs a higher `"version"` in `package.json`.

## Neovim (0.11+)

```lua
vim.filetype.add({ extension = { klar = "klar" } })
vim.lsp.config("klar", {
  cmd = { "/path/to/klar/klarc", "lsp" },
  filetypes = { "klar" },
  root_markers = { "klar.toml", ".git" },
})
vim.lsp.enable("klar")
```

For debugging, nvim-dap can start the adapter:

```lua
local dap = require("dap")
dap.adapters.klar = { type = "executable", command = "/path/to/klar/klarc", args = { "debug", "--dap" } }
dap.configurations.klar = { { type = "klar", request = "launch", name = "Debug file", program = "${file}" } }
```

## Helix (`~/.config/helix/languages.toml`)

```toml
[[language]]
name = "klar"
scope = "source.klar"
file-types = ["klar"]
comment-token = "//"
indent = { tab-width = 2, unit = "  " }
language-servers = ["klar"]
auto-format = true

[language-server.klar]
command = "/path/to/klar/klarc"
args = ["lsp"]
```

## Other editors

Start `/path/to/klar/klarc lsp` for files ending in `.klar`. The server speaks LSP 3.17 over stdio, uses UTF-16
positions, and needs no settings. The debug adapter is `/path/to/klar/klarc debug --dap`, with a `launch`
request taking `program`, `args`, `cwd` and `stopOnEntry`.
