# Klar for Visual Studio Code

[Klar](https://github.com/arnoac/klar) is a statically typed language designed to be easy for people and AI agents
to write. This extension brings its tooling into VS Code:

* errors as you type, each with a one-click fix when Klar knows the exact repair (a misspelled name, a missing
  import, a missing `uses io`, `let` that should be `var`, the missing arms of a `match`);
* types on hover and inline after every inferred variable; signatures and doc comments;
* completion that knows what fits where, and adds the `use` line for names from modules you have not imported;
* go to definition (the standard library included), find references, rename across files, workspace symbols;
* format on save, signature help, document outline;
* Run, Debug and Run tests links in the editor;
* a debugger: breakpoints, stepping, the call stack, variables and watch expressions, and a stop at the line of a
  runtime error with every variable still there.

## Requirements

Klar itself: `pipx install` from a clone of <https://github.com/arnoac/klar> puts `klar` on your PATH. Otherwise set
`klar.command` to how Klar starts, e.g. `["/path/to/klar/klarc"]` or `["python3", "-m", "klar"]`.

## Debugging

Press F5 in a `.klar` file, or use the Debug link above `fn main`. A launch configuration takes `program`, `args`,
`cwd` and `stopOnEntry`:

```json
{ "type": "klar", "request": "launch", "name": "Debug server", "program": "${workspaceFolder}/server.klar" }
```
