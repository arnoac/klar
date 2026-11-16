#!/bin/sh
# Builds the website into site/_site (or $1): the pages (site/build.klar), then the playground's compiler,
# which is Klar itself (packed from this checkout) on Pyodide (downloaded once and checked against its hash).
set -eu
cd "$(dirname "$0")/.."
OUT="${1:-site/_site}"

PYODIDE_VERSION=0.28.3
PYODIDE_SHA256=46fd28bfacd5b78333d73f171412cc992a2cab2332b067e4d2181fd255cca730
CACHE="${KLAR_SITE_CACHE:-site/.cache}"

python3 -m klar run site/build.klar "$OUT"

# The compiler, as committed: only what checking, compiling to JavaScript, testing and formatting need.
git archive --format=zip -o "$OUT/playground/klar.zip" HEAD -- klar ':!klar/runtime/wasm' ':!klar/tools' \
  ':!klar/runtime/*.h' ':!klar/runtime/klar_node.js' ':!klar/runtime/klar_wasm.js' ':!klar/SPEC.md'

# Pyodide's core files (CPython compiled to WebAssembly). No other Pyodide package is used.
mkdir -p "$CACHE"
TARBALL="$CACHE/pyodide-core-$PYODIDE_VERSION.tar.bz2"
if [ ! -f "$TARBALL" ]; then
  curl -fsSL -o "$TARBALL.part" "https://github.com/pyodide/pyodide/releases/download/$PYODIDE_VERSION/pyodide-core-$PYODIDE_VERSION.tar.bz2"
  mv "$TARBALL.part" "$TARBALL"
fi
echo "$PYODIDE_SHA256  $TARBALL" | sha256sum -c --quiet - || { echo "pyodide download does not match its hash" >&2; rm -f "$TARBALL"; exit 1; }
mkdir -p "$OUT/playground/pyodide"
tar -xjf "$TARBALL" -C "$OUT/playground/pyodide" --strip-components=1 \
  pyodide/pyodide.mjs pyodide/pyodide.asm.js pyodide/pyodide.asm.wasm pyodide/python_stdlib.zip pyodide/pyodide-lock.json
echo "site ready in $OUT"
