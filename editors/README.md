# Klar in your editor

`klarc lsp` is a language server (LSP over stdin/stdout). Any editor with LSP support gets:

* **diagnostics while you type**: the same errors as `klar check`, with their codes and hints;
* **quick fixes** taken from the hints: "did you mean `area`?", missing `use` imports, missing `uses io`;
* **hover**: the type of any variable, field or expression name; signatures and doc comments of functions;
* **go to definition**, **find references** and **rename** across every file of the program
  (standard library names can be looked at but not renamed);
* **completion**: names in scope, struct fields and functions that take the value after `x.`, module paths
  after `use std.` and the names a module exports inside `use std.json { ... }`;
* **signature help** inside calls, **document outline**, and **format document** (`klar fmt`).

Unsaved buffers are used in place of the files on disk, so an edit in one module shows up at once in the files
that import it. Analysis takes a few milliseconds to about 50 ms for the largest example app.

In every snippet below, replace `/path/to/klar` with the folder that holds `klarc`.

## VS Code

The extension in [`vscode/`](vscode) adds syntax highlighting and starts the language server.

```sh
cd editors/vscode
npm install
npx @vscode/vsce package          # makes klar-0.10.0.vsix
code --install-extension klar-0.10.0.vsix
```

Then set `"klar.command": ["/path/to/klar/klarc", "lsp"]` in your settings (the default, `["klarc", "lsp"]`,
works when `klarc` is on your PATH).

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
positions, and needs no settings.
