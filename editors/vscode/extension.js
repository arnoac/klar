// Starts `klar lsp` for .klar files; everything else (diagnostics, hover, rename, formatting...) comes from it.
const vscode = require("vscode");
const { LanguageClient } = require("vscode-languageclient/node");

let client;

function activate(context) {
  const [command, ...args] = vscode.workspace.getConfiguration("klar").get("command");
  client = new LanguageClient(
    "klar", "Klar",
    { command, args },
    { documentSelector: [{ scheme: "file", language: "klar" }] },
  );
  client.start().catch((err) => {
    vscode.window.showErrorMessage(
      `Could not start the Klar language server (${command} ${args.join(" ")}): ${err.message}. ` +
      "Set klar.command to the klar command (or the path of klarc in your clone).");
  });
  context.subscriptions.push({ dispose: () => client && client.stop() });
}

function deactivate() {
  return client ? client.stop() : undefined;
}

module.exports = { activate, deactivate };
