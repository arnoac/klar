# Changelog

## 0.11.0

* Debugger (breakpoints, stepping, variables, watch expressions, stop on runtime errors) through `klar debug --dap`.
* Quick fixes for missing imports from the standard library, `uses io`, `let` to `var`, missing `match` arms.
* Completion by expected type (attribute functions in `[...]`, enum variants as arguments, match arms) and
  completion of names from modules not imported yet, adding the `use`.
* Inlay type hints, workspace symbols, Run / Debug / Run tests code lenses, format on save by default.
* `klar.command` is now the base command (`["klar"]`); the extension adds `lsp` and `debug --dap`.
* Uses vscode-languageclient 11 (VS Code 1.91 or newer).

## 0.10.0

* First version: highlighting and the language server (`klar lsp`).
