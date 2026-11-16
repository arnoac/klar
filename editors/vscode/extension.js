// The Klar extension: everything about the code (errors, fixes, hover, completion, rename, formatting...) comes
// from `klar lsp`, and debugging from `klar debug --dap`. This file only starts them and adds the run commands.
const vscode = require("vscode");
const { LanguageClient } = require("vscode-languageclient/node");

let client;

// The configured command, without the old default's trailing "lsp" (settings written for version 0.10).
function klarCommand() {
  const cmd = [...(vscode.workspace.getConfiguration("klar").get("command") || ["klar"])];
  if (cmd.length > 1 && cmd[cmd.length - 1] === "lsp") cmd.pop();
  return cmd.length ? cmd : ["klar"];
}

function startClient() {
  const [command, ...args] = klarCommand();
  client = new LanguageClient(
    "klar", "Klar",
    { command, args: [...args, "lsp"] },
    { documentSelector: [{ scheme: "file", language: "klar" }, { scheme: "untitled", language: "klar" }] },
  );
  return client.start().catch((err) => {
    vscode.window.showErrorMessage(
      `Could not start the Klar language server (${[command, ...args].join(" ")} lsp): ${err.message}. ` +
      "Install Klar so `klar` is on your PATH, or set klar.command to the path of klarc in your clone.");
  });
}

function fileOf(arg) {
  if (typeof arg === "string") return vscode.Uri.parse(arg).fsPath;
  if (arg instanceof vscode.Uri) return arg.fsPath;
  const ed = vscode.window.activeTextEditor;
  return ed && ed.document.languageId === "klar" ? ed.document.uri.fsPath : undefined;
}

// Run `klar <verb> FILE` in a terminal named after the file.
async function inTerminal(verb, arg) {
  const file = fileOf(arg);
  if (!file) return vscode.window.showWarningMessage("Open a .klar file first.");
  const doc = vscode.workspace.textDocuments.find((d) => d.uri.fsPath === file);
  if (doc && doc.isDirty) await doc.save();
  const quote = (s) => (/^[\w@%+=:,./-]+$/.test(s) ? s : `'${s.replace(/'/g, "'\\''")}'`);
  const term = vscode.window.createTerminal({ name: `klar ${verb}`, cwd: require("path").dirname(file) });
  term.show(true);
  term.sendText([...klarCommand(), verb, file].map(quote).join(" "));
}

function debugFile(arg) {
  const file = fileOf(arg);
  if (!file) return vscode.window.showWarningMessage("Open a .klar file first.");
  const folder = vscode.workspace.getWorkspaceFolder(vscode.Uri.file(file));
  return vscode.debug.startDebugging(folder, {
    type: "klar", request: "launch", name: "Debug " + require("path").basename(file), program: file,
  });
}

const debugConfigs = {
  // F5 with no launch.json: debug the open file
  resolveDebugConfiguration(folder, config) {
    if (!config.type && !config.request && !config.name) {
      const file = fileOf();
      if (!file) return vscode.window.showWarningMessage("Open a .klar file to debug it.").then(() => undefined);
      return { type: "klar", request: "launch", name: "Debug Klar file", program: file };
    }
    if (!config.program) config.program = fileOf();
    return config;
  },
};

const debugAdapters = {
  createDebugAdapterDescriptor() {
    const [command, ...args] = klarCommand();
    return new vscode.DebugAdapterExecutable(command, [...args, "debug", "--dap"]);
  },
};

function activate(context) {
  startClient();
  context.subscriptions.push(
    vscode.commands.registerCommand("klar.run", (arg) => inTerminal("run", arg)),
    vscode.commands.registerCommand("klar.test", (arg) => inTerminal("test", arg)),
    vscode.commands.registerCommand("klar.debug", debugFile),
    vscode.commands.registerCommand("klar.restart", async () => {
      if (client) await client.stop();
      await startClient();
    }),
    vscode.debug.registerDebugConfigurationProvider("klar", debugConfigs),
    vscode.debug.registerDebugAdapterDescriptorFactory("klar", debugAdapters),
    vscode.workspace.onDidChangeConfiguration(async (e) => {
      if (e.affectsConfiguration("klar.command")) {
        if (client) await client.stop();
        await startClient();
      }
    }),
    { dispose: () => client && client.stop() },
  );
}

function deactivate() {
  return client ? client.stop() : undefined;
}

module.exports = { activate, deactivate };
