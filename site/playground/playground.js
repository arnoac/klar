// The Klar playground: an editor, the compiler (compiler-worker.js, the real Klar toolchain on Pyodide) and a
// runner for the compiled program.
//
// Programs run in a sandboxed iframe (sandbox="allow-scripts", no allow-same-origin), so they get an opaque
// origin: no cookies, storage or DOM of this site. The frame's Content-Security-Policy allows no network
// requests at all. Console programs run in a Web Worker inside that frame, so an endless loop never freezes
// the page and Stop (or the time limit) ends it by removing the frame. Browser apps (std.ui) run in the frame
// itself, where you can see and use them.

import { highlight } from "./highlight.js";

const $ = id => document.getElementById(id);
const editor = $("pg-code");
const hl = $("pg-hl");
const consoleEl = $("pg-console");
const previewWrap = $("pg-preview-wrap");
const status = $("pg-status");
const runBtn = $("pg-run"), testBtn = $("pg-test"), fmtBtn = $("pg-fmt"), shareBtn = $("pg-share");
const picker = $("pg-example");

const RUN_LIMIT_MS = 10000;      // console programs
const TEST_LIMIT_MS = 20000;     // tests run in the interpreter, inside the compiler's worker

// ---------------------------------------------------------------- editor

function paint() {
  // a trailing newline needs a character after it, or the last empty line has no height
  hl.innerHTML = highlight(editor.value) + "\n ";
}
editor.addEventListener("input", paint);
editor.addEventListener("scroll", () => {
  hl.parentElement.scrollTop = editor.scrollTop;
  hl.parentElement.scrollLeft = editor.scrollLeft;
});
editor.addEventListener("keydown", e => {
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
    e.preventDefault();
    run();
  } else if (e.key === "Tab" && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
    e.preventDefault();
    document.execCommand("insertText", false, "  ");
  } else if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
    const start = editor.selectionStart;
    const lineStart = editor.value.lastIndexOf("\n", start - 1) + 1;
    const indent = editor.value.slice(lineStart, start).match(/^ */)[0];
    const opens = /[{(\[]\s*$/.test(editor.value.slice(lineStart, start));
    e.preventDefault();
    document.execCommand("insertText", false, "\n" + indent + (opens ? "  " : ""));
  }
});

function setCode(src) {
  editor.value = src;
  paint();
  editor.scrollTop = 0;
}

function jumpTo(line, col) {
  const lines = editor.value.split("\n");
  let pos = 0;
  for (let i = 0; i < line - 1 && i < lines.length; i++) pos += lines[i].length + 1;
  pos += Math.max(0, col - 1);
  editor.focus();
  editor.setSelectionRange(pos, pos);
  const lineHeight = parseFloat(getComputedStyle(editor).lineHeight) || 20;
  editor.scrollTop = Math.max(0, (line - 4) * lineHeight);
}

// ---------------------------------------------------------------- console

function clearConsole() { consoleEl.textContent = ""; }
function say(text, cls) {
  const span = document.createElement("span");
  if (cls) span.className = cls;
  span.textContent = text + "\n";
  consoleEl.appendChild(span);
  consoleEl.scrollTop = consoleEl.scrollHeight;
}
function showErrors(result) {
  if (result.notice) {
    say(result.notice, "err");
    return;
  }
  if (result.crash) {
    say("The compiler stopped unexpectedly: " + result.crash, "err");
    return;
  }
  for (const e of result.errors) {
    const block = document.createElement("span");
    block.className = "err";
    const jump = document.createElement("button");
    jump.className = "jump";
    jump.textContent = `line ${e.line}:${e.col}`;
    jump.addEventListener("click", () => jumpTo(e.line, e.col));
    block.append(e.rendered + "\n", "  ", jump, "\n\n");
    consoleEl.appendChild(block);
  }
  say(`${result.errors.length} error(s). Every code is explained in the language reference, section 8.`, "dim");
}

// ---------------------------------------------------------------- compiler

let worker, nextId = 1;
const pending = new Map();
let compilerReady = false;

function startCompiler() {
  compilerReady = false;
  setBusy(true);
  worker = new Worker(new URL("./compiler-worker.js", import.meta.url), { type: "module" });
  worker.onmessage = e => {
    const m = e.data;
    if (m.status) { status.textContent = m.status; return; }
    if (m.ready) {
      compilerReady = true;
      status.textContent = "Ready: the Klar compiler is running in your browser";
      setBusy(false);
      return;
    }
    if (m.failed) {
      status.textContent = "The compiler could not load: " + m.failed;
      return;
    }
    const p = pending.get(m.id);
    if (p) { pending.delete(m.id); p(m); }
  };
}

function ask(cmd, src, limit) {
  return new Promise(resolve => {
    const id = nextId++;
    let timer = null;
    if (limit) {
      timer = setTimeout(() => {
        pending.delete(id);
        worker.terminate();
        startCompiler();
        resolve({ timedOut: true });
      }, limit);
    }
    pending.set(id, m => { clearTimeout(timer); resolve(m); });
    worker.postMessage({ id, cmd, src });
  });
}

function setBusy(busy) {
  for (const b of [runBtn, testBtn, fmtBtn]) b.disabled = busy || !compilerReady;
}

// ---------------------------------------------------------------- runner

let frame = null, runTimer = null;

function stopProgram(note) {
  clearTimeout(runTimer);
  if (frame) { frame.remove(); frame = null; }
  if (note) say(note, "dim");
  runBtn.textContent = "Run";
}

// Text placed inside an inline <script>: a JSON string that cannot close the script element.
const scriptString = s => JSON.stringify(s).replace(/</g, "\\u003c").replace(/[\u2028\u2029]/g, c => "\\u" + c.charCodeAt(0).toString(16));

const FRAME_CSP = "default-src 'none'; script-src 'unsafe-inline' blob:; worker-src blob:; style-src 'unsafe-inline'; img-src data: blob:; font-src data:";

// Console output of the program, passed up to this page.
const HOOK_FRAME = `<script>
(() => {
  const send = (kind, args) => parent.postMessage({ kind, text: args.map(a => typeof a === "string" ? a : String(a)).join(" ") }, "*");
  console.log = console.info = (...a) => send("out", a);
  console.warn = console.error = (...a) => send("err", a);
  addEventListener("error", e => send("err", [e.message]));
  addEventListener("unhandledrejection", e => send("err", [String(e.reason)]));
})();
<\/script>`;

const HOOK_WORKER = `const $send = (kind, args) => postMessage({ kind, text: args.map(a => typeof a === "string" ? a : String(a)).join(" ") });
console.log = console.info = (...a) => $send("out", a);
console.warn = console.error = (...a) => $send("err", a);
`;

function consoleRunner(js) {
  return `<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${FRAME_CSP}">
<script>
const w = new Worker(URL.createObjectURL(new Blob([${scriptString(HOOK_WORKER + js)}], { type: "text/javascript" })));
w.onmessage = e => parent.postMessage(e.data, "*");
w.onerror = e => { e.preventDefault(); parent.postMessage({ kind: "err", text: e.message || "the program failed to start" }, "*"); parent.postMessage({ kind: "done" }, "*"); };
<\/script></head><body></body></html>`;
}

function appRunner(page) {
  const head = `<meta http-equiv="Content-Security-Policy" content="${FRAME_CSP}">\n${HOOK_FRAME}\n`;
  return page.replace(/<head>/i, "<head>\n" + head);
}

function launch(doc, ui) {
  stopProgram();
  frame = document.createElement("iframe");
  frame.setAttribute("sandbox", "allow-scripts");
  frame.setAttribute("title", "Your program");
  frame.setAttribute("referrerpolicy", "no-referrer");
  frame.srcdoc = doc;
  previewWrap.hidden = !ui;
  if (ui) {
    previewWrap.appendChild(frame);
  } else {
    frame.style.display = "none";
    document.body.appendChild(frame);
    runBtn.textContent = "Stop";
    runTimer = setTimeout(() => stopProgram(`Stopped after ${RUN_LIMIT_MS / 1000} s. Is there an endless loop?`), RUN_LIMIT_MS);
  }
}

addEventListener("message", e => {
  if (!frame || e.source !== frame.contentWindow) return;
  const m = e.data || {};
  if (m.kind === "out") say(String(m.text));
  else if (m.kind === "err") say(String(m.text), "err");
  else if (m.kind === "done") {
    clearTimeout(runTimer);
    runBtn.textContent = "Run";
    const ms = Math.round(performance.now() - startedAt);
    say(`Finished in ${ms} ms.`, "dim");
  }
});

// ---------------------------------------------------------------- actions

let startedAt = 0;

async function run() {
  if (runBtn.textContent === "Stop") { stopProgram("Stopped."); return; }
  if (!compilerReady) return;
  clearConsole();
  setBusy(true);
  status.textContent = "Compiling...";
  const t0 = performance.now();
  const r = await ask("build", editor.value);
  setBusy(false);
  status.textContent = `Compiled in ${Math.round(performance.now() - t0)} ms`;
  if (!r.ok) {
    stopProgram();
    previewWrap.hidden = true;
    showErrors(r);
    return;
  }
  startedAt = performance.now();
  if (r.ui) {
    say("Your app is running above. Messages it prints appear here.", "dim");
    launch(appRunner(r.page), true);
  } else {
    launch(consoleRunner(r.js), false);
  }
}

async function test() {
  if (!compilerReady) return;
  clearConsole();
  setBusy(true);
  status.textContent = "Running tests in the interpreter...";
  const r = await ask("test", editor.value, TEST_LIMIT_MS);
  setBusy(compilerReady ? false : true);
  if (r.timedOut) {
    status.textContent = "Restarting the compiler...";
    say(`The tests did not finish within ${TEST_LIMIT_MS / 1000} s, so they were stopped. Is there an endless loop?`, "err");
    return;
  }
  status.textContent = "Tests finished";
  if (!r.ok) { showErrors(r); return; }
  if (r.output) say(r.output.replace(/\n$/, ""));
  if (r.tests.length === 0) {
    say('No test blocks. Add one: test "adds" { assert 1 + 1 == 2 }', "dim");
    return;
  }
  for (const t of r.tests) {
    say((t.ok ? "PASS  " : "FAIL  ") + t.name, t.ok ? "ok" : "err");
    if (t.error) say("      " + t.error.replace(/\n/g, "\n      "), "err");
  }
  const failed = r.tests.filter(t => !t.ok).length;
  say(`${r.tests.length - failed} passed, ${failed} failed`, failed ? "err" : "ok");
}

async function format() {
  if (!compilerReady) return;
  setBusy(true);
  const r = await ask("fmt", editor.value);
  setBusy(false);
  if (r.ok) {
    if (r.src !== editor.value) {
      editor.select();
      document.execCommand("insertText", false, r.src);   // keeps undo working
      paint();
    }
    status.textContent = "Formatted";
  } else {
    clearConsole();
    showErrors(r);
  }
}

// Share: the code goes in the URL's #fragment, which browsers never send to a server.
const toBase64 = s => btoa(String.fromCharCode(...new TextEncoder().encode(s))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const fromBase64 = s => new TextDecoder().decode(Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/")), c => c.charCodeAt(0)));

async function share() {
  const url = location.href.split("#")[0] + "#code=" + toBase64(editor.value);
  history.replaceState(null, "", url);
  try {
    await navigator.clipboard.writeText(url);
    status.textContent = "Link copied";
  } catch {
    status.textContent = "Link is in the address bar";
  }
}

runBtn.addEventListener("click", run);
testBtn.addEventListener("click", test);
fmtBtn.addEventListener("click", format);
shareBtn.addEventListener("click", share);

// ---------------------------------------------------------------- start

let examples = [];
picker.addEventListener("change", () => {
  const ex = examples.find(x => x.name === picker.value);
  if (ex) {
    setCode(ex.code);
    history.replaceState(null, "", location.href.split("#")[0] + "#example=" + ex.name);
    stopProgram();
    previewWrap.hidden = true;
    clearConsole();
  }
});

async function init() {
  startCompiler();
  try {
    examples = await (await fetch(new URL("./examples.json", import.meta.url))).json();
  } catch {
    examples = [];
  }
  const hash = new URLSearchParams(location.hash.slice(1));
  let code = null;
  if (hash.get("code")) {
    try { code = fromBase64(hash.get("code")); } catch { code = null; }
  }
  const ex = examples.find(x => x.name === hash.get("example")) || examples[0];
  if (code !== null) {
    picker.value = "";
    setCode(code);
    say("Code from a shared link. Read it before you run it: it runs in a sandbox, but it is someone else's code.", "dim");
  } else if (ex) {
    picker.value = ex.name;
    setCode(ex.code);
  }
}

init();
