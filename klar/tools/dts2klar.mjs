// klar bind: write Klar extern declarations for an npm package from its TypeScript declarations (.d.ts).
//
//   node dts2klar.mjs TYPESCRIPT_PATH PROJECT_DIR MODULE KLAR_PREFIX
//
// Prints the Klar module on stdout. Uses the TypeScript compiler's type checker, so re-exports, overloads,
// namespaces and generics are resolved the way an editor resolves them. Whatever does not map onto Klar's
// types (any, unknown, intersections, mixed unions, rest parameters, ...) is listed in a comment instead.

import { createRequire } from "node:module";
import path from "node:path";
import fs from "node:fs";

const [tsPath, projectDir, moduleName, prefixArg] = process.argv.slice(2);
const require = createRequire(import.meta.url);
const ts = require(tsPath);

const KEYWORDS = new Set(("fn let var if then else while for in return break continue match struct enum uses true false " +
  "and or not assert test with use extern type trait impl").split(" "));
const RESERVED_TYPES = new Set(["Option", "Result", "Some", "None", "Ok", "Err", "Html", "Attr", "Json", "Next", "Cmd"]);
const INT_NAMES = /^(size|count|length|len|index|idx|n|limit|max|min|depth|width|height|port|offset|start|end|level|times|retries|ms|timeout|seconds|minutes|hours|days|year|month|day|indent|precision|digits|radix|base)$/i;

const snake = s => s.replace(/([a-z0-9])([A-Z])/g, "$1_$2").replace(/([A-Z]+)([A-Z][a-z])/g, "$1_$2")
  .replace(/[^A-Za-z0-9_]/g, "_").replace(/_+/g, "_").replace(/^_|_$/g, "").toLowerCase();
const pascal = s => s.split(/[^A-Za-z0-9]+/).filter(Boolean).map(w => w[0].toUpperCase() + w.slice(1)).join("");
const ident = s => { let x = snake(s) || "x"; if (/^[0-9]/.test(x)) x = "n" + x; return KEYWORDS.has(x) ? x + "_" : x; };
const isFieldName = s => /^[a-z][A-Za-z0-9_]*$/.test(s) && !KEYWORDS.has(s);

// ---------------------------------------------------------------- find the .d.ts
const host = ts.createCompilerHost({});
const resolved = ts.resolveModuleName(moduleName, path.join(projectDir, "index.ts"),
  { moduleResolution: ts.ModuleResolutionKind.Bundler, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 }, host);
let entry = resolved.resolvedModule && resolved.resolvedModule.resolvedFileName;
if (!entry || !/\.d\.[cm]?ts$/.test(entry)) {
  const pkgName = moduleName.startsWith("@") ? moduleName.split("/").slice(0, 2).join("/") : moduleName.split("/")[0];
  const typesName = "@types/" + pkgName.replace(/^@/, "").replace("/", "__");
  const r2 = ts.resolveModuleName(typesName, path.join(projectDir, "index.ts"),
    { moduleResolution: ts.ModuleResolutionKind.Bundler, module: ts.ModuleKind.ESNext }, host);
  entry = r2.resolvedModule && r2.resolvedModule.resolvedFileName;
  if (!entry) {
    console.error(`no TypeScript declarations found for \`${moduleName}\`; ` +
      `it ships no .d.ts and there is no ${typesName}. Try: klar add --npm ${typesName}, then klar bind again`);
    process.exit(3);
  }
}
const program = ts.createProgram([entry], { allowJs: false, skipLibCheck: true, target: ts.ScriptTarget.ES2022,
  module: ts.ModuleKind.ESNext, moduleResolution: ts.ModuleResolutionKind.Bundler, strict: true });
const checker = program.getTypeChecker();
const sf = program.getSourceFile(entry);
let modSym = checker.getSymbolAtLocation(sf);
if (!modSym) {   // `declare module "x" { ... }` or a global script
  for (const s of program.getSourceFiles()) for (const st of s.statements)
    if (ts.isModuleDeclaration(st) && st.name.text === moduleName) modSym = checker.getSymbolAtLocation(st.name);
}
if (!modSym) { console.error(`${entry} declares no module exports`); process.exit(3); }

// ---------------------------------------------------------------- naming
const prefix = prefixArg || pascal(moduleName.replace(/^@/, ""));
const typeNames = new Map();          // TS symbol -> Klar type name
const usedTypeNames = new Set();
function klarTypeName(sym) {
  if (typeNames.has(sym)) return typeNames.get(sym);
  let base = pascal(sym.getName()) || "Value";
  if (!base.startsWith(prefix)) base = prefix + base;
  let name = base, i = 2;
  while (usedTypeNames.has(name) || RESERVED_TYPES.has(name)) name = base + i++;
  usedTypeNames.add(name);
  typeNames.set(sym, name);
  return name;
}
const usedFnNames = new Set();
function fnName(want) {
  let name = ident(want), i = 2;
  while (usedFnNames.has(name)) name = ident(want) + "_" + i++;
  usedFnNames.add(name);
  return name;
}

// ---------------------------------------------------------------- types
const F = ts.TypeFlags;
const decls = [];        // emitted Klar type declarations, in order
const pending = [];      // named object types still to emit
const skipped = [];      // [name, reason]
const done = new Set();

class Unmappable extends Error {}
const fail = why => { throw new Unmappable(why); };

function typeArgs(t) { return checker.getTypeArguments ? (t.target ? checker.getTypeArguments(t) : []) : t.typeArguments || []; }
function symName(t) { const s = t.aliasSymbol || t.getSymbol(); return s ? s.getName() : ""; }

// mode: "param" | "ret" | "field"; hint: a parameter or field name (for int vs float)
function mapType(t, mode, hint, asyncOut) {
  if (t.isTypeParameter && t.isTypeParameter()) {
    const c = checker.getBaseConstraintOfType(t);
    if (!c || c === t || c.flags & F.Unknown) fail(`generic type parameter ${checker.typeToString(t)}`);
    return mapType(c, mode, hint, asyncOut);
  }
  if (t.flags & (F.Any | F.Unknown)) fail(`\`${checker.typeToString(t)}\` says nothing about the value`);
  if (t.flags & (F.String | F.StringLiteral | F.TemplateLiteral)) return "str";
  if (t.flags & (F.Number | F.NumberLiteral)) return mode !== "ret" && hint && INT_NAMES.test(snake(hint).split("_").pop()) ? "int" : "float";
  if (t.flags & (F.Boolean | F.BooleanLiteral)) return "bool";
  if (t.flags & (F.BigInt | F.BigIntLiteral)) return "int";
  if (t.flags & (F.Void | F.Undefined)) { if (mode === "ret") return "unit"; fail("a value that is always undefined"); }
  if (t.flags & F.Null) fail("a value that is always null");
  if (t.flags & F.Never) return "unit";
  if (t.isUnion()) {
    const parts = t.types.filter(x => !(x.flags & (F.Null | F.Undefined | F.Void)));
    const optional = parts.length < t.types.length;
    let inner;
    if (parts.every(x => x.flags & (F.StringLiteral | F.String | F.TemplateLiteral))) inner = "str";
    else if (parts.every(x => x.flags & (F.NumberLiteral | F.Number))) inner = mapType(parts[0], mode, hint, asyncOut);
    else if (parts.every(x => x.flags & (F.BooleanLiteral | F.Boolean))) inner = "bool";
    else if (parts.length === 1) inner = mapType(parts[0], mode, hint, asyncOut);
    else if (mode === "ret" && parts.length === 2 && parts.some(x => /^Promise(Like)?$/.test(symName(x)))) {
      // `T | Promise<T>` (marked.parse): awaiting gives T either way
      const p = parts.find(x => /^Promise(Like)?$/.test(symName(x))), o = parts.find(x => x !== p);
      const a = mapType(p, mode, hint, asyncOut), b = mapType(o, mode, hint, {});
      if (a !== b) fail(`the union \`${checker.typeToString(t)}\``);
      inner = a;
    } else if (mode === "param") {
      // an argument may be any one of the accepted types: Klar passes the first one it can express
      const ok = [];
      for (const x of parts) { try { ok.push(mapType(x, mode, hint, asyncOut)); } catch (e) { if (!(e instanceof Unmappable)) throw e; } }
      if (!ok.length) fail(`the union \`${checker.typeToString(t)}\``);
      inner = ok.includes("str") ? "str" : ok[0];
    } else fail(`the union \`${checker.typeToString(t)}\``);
    if (!optional) return inner;
    if (mode === "ret" && inner === "unit") return "unit";
    return `Option[${inner}]`;
  }
  if (t.isIntersection()) fail(`the intersection \`${checker.typeToString(t)}\``);
  const name = symName(t);
  if (name === "Promise" || name === "PromiseLike") {
    if (mode !== "ret") fail("a Promise as an argument");
    asyncOut.async = true;
    const [inner] = typeArgs(t);
    return inner ? mapType(inner, "ret", hint, asyncOut) : "unit";
  }
  if (checker.isArrayType(t) || checker.isTupleType && checker.isTupleType(t) && false || name === "ReadonlyArray" || name === "Array") {
    const [e] = typeArgs(t);
    return `list[${mapType(e, mode === "ret" ? "ret-inner" : mode, hint, asyncOut)}]`;
  }
  if (checker.isTupleType && checker.isTupleType(t)) fail("a tuple");
  if (name === "Record" && t.aliasTypeArguments) {
    const [k, v] = t.aliasTypeArguments;
    if (!(k.flags & F.String)) fail("a Record with non-string keys");
    return `map[str, ${mapType(v, mode === "ret" ? "ret-inner" : mode, hint, asyncOut)}]`;
  }
  if (name === "Map" || name === "ReadonlyMap") {
    const [k, v] = typeArgs(t);
    if (!(k.flags & F.String)) fail("a Map with non-string keys");
    return `map[str, ${mapType(v, mode === "ret" ? "ret-inner" : mode, hint, asyncOut)}]`;
  }
  if (t.flags & F.Object) {
    const calls = t.getCallSignatures();
    const props = t.getProperties();
    if (calls.length && !props.length) return fnType(calls[calls.length - 1]);
    const strIndex = t.getStringIndexType && t.getStringIndexType();
    if (strIndex && !props.length) return `map[str, ${mapType(strIndex, mode === "ret" ? "ret-inner" : mode, hint, asyncOut)}]`;
    const sym = t.aliasSymbol || t.getSymbol();
    if (!sym || sym.getName() === "__type" || sym.getName() === "__object") {
      if (t.aliasSymbol) return namedType(t.aliasSymbol, t);
      fail(`an inline object type \`${checker.typeToString(t)}\``);
    }
    if (isBuiltinGlobal(sym)) fail(`the built-in \`${sym.getName()}\``);
    return namedType(sym, t);
  }
  fail(`\`${checker.typeToString(t)}\``);
}

function isBuiltinGlobal(sym) {
  const d = sym.declarations && sym.declarations[0];
  if (!d) return true;
  const file = d.getSourceFile().fileName;
  return /[\\/]typescript[\\/]lib[\\/]lib\./.test(file) || /[\\/]@types[\\/]node[\\/]/.test(file);
}

function fnType(sig) {
  const out = { async: false };
  const ps = sig.getParameters().map(p => {
    const d = p.valueDeclaration;
    if (d && d.dotDotDotToken) fail("a callback with a rest parameter");
    // a callback's parameters are values JavaScript hands to Klar: they come back like results
    return mapType(checker.getTypeOfSymbolAtLocation(p, d), "ret-inner", p.getName(), out);
  });
  const r = mapType(sig.getReturnType(), "ret", "", out);
  return `fn(${ps.join(", ")})${r === "unit" && !out.async ? "" : ` -> ${r}`}`.replace(/^fn\(\)$/, "fn() -> unit") +
    (out.async ? " uses io" : "");
}

// A named object type becomes a struct when it is plain data with Klar-friendly field names, else an opaque
// extern type with methods and property getters.
function namedType(sym, t) {
  const name = klarTypeName(sym);
  if (!done.has(sym)) { done.add(sym); pending.push([sym, t, name]); }
  return name;
}

function emitNamed(sym, t, name) {
  const props = t.getProperties();
  const isClass = sym.flags & ts.SymbolFlags.Class;
  const data = !isClass && props.length > 0 && props.every(p => {
    const pt = checker.getTypeOfSymbol(p);
    return isFieldName(p.getName()) && !(p.flags & ts.SymbolFlags.Method) && !pt.getCallSignatures().length;
  });
  if (data) {
    const fields = [];
    for (const p of props) {
      const optional = !!(p.flags & ts.SymbolFlags.Optional);
      try {
        let ft = mapType(checker.getNonNullableType(checker.getTypeOfSymbol(p)), "field", p.getName(), {});
        if (optional && !ft.startsWith("Option[")) ft = `Option[${ft}]`;
        fields.push(`  ${p.getName()}: ${ft},`);
      } catch (e) {
        if (!(e instanceof Unmappable)) throw e;
        if (!optional) { emitOpaque(sym, t, name, `field \`${p.getName()}\` is ${e.message}`); return; }
        fields.push(`  // ${p.getName()}: left out (${e.message})`);
      }
    }
    decls.push(`// ${sym.getName()}: plain data, converted field by field\nstruct ${name} {\n${fields.join("\n")}\n}`);
    return;
  }
  emitOpaque(sym, t, name);
}

const members = [];      // extern declarations for methods and properties of opaque types
function emitOpaque(sym, t, name, why) {
  decls.push(`// ${sym.getName()}: a JavaScript object Klar holds without looking inside${why ? ` (${why})` : ""}\nextern type ${name}`);
  const base = snake(name.startsWith(prefix) && name !== prefix ? name.slice(prefix.length) : name) || snake(name);
  const self = ident(base) === "self" ? "obj" : ident(base);
  for (const p of t.getProperties()) {
    const pname = p.getName();
    if (pname.startsWith("_") || pname.startsWith("#") || /^__@/.test(pname)) continue;
    const d = p.valueDeclaration || (p.declarations && p.declarations[0]);
    if (d && d.modifiers && d.modifiers.some(m => m.kind === ts.SyntaxKind.PrivateKeyword || m.kind === ts.SyntaxKind.ProtectedKeyword)) continue;
    const pt = checker.getTypeOfSymbol(p);
    const calls = pt.getCallSignatures();
    if (p.flags & ts.SymbolFlags.Method || (calls.length && !pt.getProperties().length)) {
      const sig = pickSignature(calls, `${name}.${pname}`, [self + ": " + name]);
      if (sig) members.push(signatureDecl(`js:.${pname}`, fnName(`${base}_${pname}`), sig, [`${self}: ${name}`]));
    } else {
      try {
        const ft = mapType(pt, "ret", pname, {});
        members.push(`extern "js:get .${pname}" fn ${fnName(`${base}_${pname}`)}(${self}: ${name}) -> ${ft}`);
      } catch (e) {
        if (!(e instanceof Unmappable)) throw e;
        skipped.push([`${name}.${pname}`, e.message]);
      }
    }
  }
}

// ---------------------------------------------------------------- functions
function mapSignature(sig, leading) {
  const out = { async: false };
  const params = [...leading];
  const used = new Set(leading.map(p => p.split(":")[0]));
  for (const p of sig.getParameters()) {
    const d = p.valueDeclaration;
    if (d && d.dotDotDotToken) fail("a rest parameter (...args)");
    if (d && ts.isParameter(d) && d.name && d.name.kind === ts.SyntaxKind.Identifier && d.name.text === "this") continue;
    let pt = checker.getTypeOfSymbolAtLocation(p, d);
    const optional = !!(d && (d.questionToken || d.initializer));
    let mt = mapType(optional ? checker.getNonNullableType(pt) : pt, "param", p.getName(), out);
    if (optional && !mt.startsWith("Option[")) mt = `Option[${mt}]`;
    let pn = ident(p.getName());
    while (used.has(pn)) pn += "_";
    used.add(pn);
    params.push(`${pn}: ${mt}`);
  }
  const ret = mapType(sig.getReturnType(), "ret", "", out);
  return { params, ret, async: out.async };
}

function pickSignature(sigs, what, leading) {
  const reasons = [];
  for (const sig of sigs) {
    try { mapSignature(sig, leading); return sig; }
    catch (e) { if (!(e instanceof Unmappable)) throw e; reasons.push(e.message); }
  }
  skipped.push([what, reasons[reasons.length - 1] || "no call signature"]);
  return null;
}

// Every function is declared `uses io` and returns Result: a .d.ts says neither whether a function has side
// effects nor whether it throws. Where you know better, edit the declaration (see the header).
function signatureDecl(target, name, sig, leading) {
  const m = mapSignature(sig, leading);
  const ret = m.ret === "unit" ? "Result[unit, str]" : `Result[${m.ret}, str]`;
  const doc = docOf(sig);
  return `${doc}extern "${target}" fn ${name}(${m.params.join(", ")}) -> ${ret} uses io`;
}

function docOf(sig) {
  const d = sig.getDocumentationComment && ts.displayPartsToString(sig.getDocumentationComment(checker));
  if (!d) return "";
  const first = d.split(/\n\s*\n/)[0].replace(/\s+/g, " ").trim();
  return first ? `// ${first.length > 110 ? first.slice(0, 107) + "..." : first}\n` : "";
}

// ---------------------------------------------------------------- exports
const fns = [];
const target = (exportName) => `js:${moduleName}.${exportName}`;
for (let sym of checker.getExportsOfModule(modSym)) {
  const exportName = sym.getName();
  if (sym.flags & ts.SymbolFlags.Alias) sym = checker.getAliasedSymbol(sym);
  const nice = exportName === "default" ? moduleName.replace(/^@[^/]+\//, "") : exportName;
  if (sym.flags & ts.SymbolFlags.Class) {
    const inst = checker.getDeclaredTypeOfSymbol(sym);
    const tname = namedType(sym, inst);
    const ctorType = checker.getTypeOfSymbol(sym);
    const sig = pickSignature(ctorType.getConstructSignatures(), `new ${exportName}`, []);
    if (sig) {
      const m = mapSignature(sig, []);
      fns.push(`${docOf(sig)}extern "js:new ${moduleName}.${exportName}" fn ${fnName("new_" + snake(nice))}(${m.params.join(", ")}) -> Result[${tname}, str] uses io`);
    }
    continue;
  }
  if (sym.flags & (ts.SymbolFlags.Interface | ts.SymbolFlags.TypeAlias) && !(sym.flags & ts.SymbolFlags.Value)) {
    try { mapType(checker.getDeclaredTypeOfSymbol(sym), "field", "", {}); }
    catch (e) { if (!(e instanceof Unmappable)) throw e; skipped.push([`type ${exportName}`, e.message]); }
    continue;
  }
  if (!(sym.flags & ts.SymbolFlags.Value)) continue;
  const t = checker.getTypeOfSymbol(sym);
  const calls = t.getCallSignatures();
  if (calls.length) {
    const sig = pickSignature(calls, exportName, []);
    if (sig) fns.push(signatureDecl(target(exportName), fnName(nice), sig, []));
    // functions that also carry functions as properties (marked.parse, marked.use, ...)
    for (const p of t.getProperties()) {
      const pc = checker.getTypeOfSymbol(p).getCallSignatures();
      if (!pc.length || p.getName().startsWith("_")) continue;
      const s2 = pickSignature(pc, `${exportName}.${p.getName()}`, []);
      if (s2) fns.push(signatureDecl(target(`${exportName}.${p.getName()}`), fnName(`${nice}_${p.getName()}`), s2, []));
    }
    continue;
  }
  // a namespace object: export its function members
  const props = t.getProperties();
  if (t.flags & F.Object && props.length && props.some(p => checker.getTypeOfSymbol(p).getCallSignatures().length)
      && !(sym.flags & ts.SymbolFlags.Variable && symName(t) && !isBuiltinGlobal(t.getSymbol()) && t.getSymbol().flags & ts.SymbolFlags.Class)) {
    let any = false;
    for (const p of props) {
      const pc = checker.getTypeOfSymbol(p).getCallSignatures();
      if (!pc.length) continue;
      const s2 = pickSignature(pc, `${exportName}.${p.getName()}`, []);
      if (s2) { fns.push(signatureDecl(target(`${exportName}.${p.getName()}`), fnName(`${exportName === "default" ? "" : nice + "_"}${p.getName()}`), s2, [])); any = true; }
    }
    if (any) continue;
  }
  try {
    const vt = mapType(t, "ret", exportName, {});
    fns.push(`extern "js:get ${moduleName}.${exportName}" fn ${fnName(nice)}() -> ${vt}`);
  } catch (e) { if (!(e instanceof Unmappable)) throw e; skipped.push([exportName, e.message]); }
}
while (pending.length) emitNamed(...pending.shift());

// ---------------------------------------------------------------- output
let version = "";
try {
  const pj = path.join(projectDir, "node_modules", ...moduleName.split("/").slice(0, moduleName.startsWith("@") ? 2 : 1), "package.json");
  version = JSON.parse(fs.readFileSync(pj, "utf8")).version || "";
} catch (_) {}
const out = [];
out.push(`// Klar bindings for the npm package \`${moduleName}\`${version ? ` ${version}` : ""}, written by \`klar bind ${moduleName}\``);
out.push(`// from ${path.relative(projectDir, entry)}. This file is yours: edit it, and commit it.`);
out.push("//");
out.push("// A .d.ts does not say whether a function has side effects or whether it throws, so every function here");
out.push("// starts out as `uses io` returning Result[T, str]: errors arrive as Err, never as exceptions. Where you know");
out.push("// better, tighten a declaration by hand: drop `uses io` from a pure function, or the Result from one that");
out.push("// cannot fail. Klar keeps checking at run time (a Promise from a function not marked `uses io`, a thrown");
out.push("// error without a Result, or null where no Option was declared stops the program with E407).");
out.push("");
if (decls.length) out.push(...decls.flatMap(d => [d, ""]));
if (fns.length) out.push(...fns, "");
if (members.length) out.push(...members, "");
if (skipped.length) {
  out.push("// Not bound (Klar has no type for them yet; declare what you need by hand):");
  for (const [n, why] of skipped) out.push(`//   ${n}: ${why.replace(/\s+/g, " ").slice(0, 120)}`);
}
process.stdout.write(out.join("\n").replace(/\n{3,}/g, "\n\n").trimEnd() + "\n");
