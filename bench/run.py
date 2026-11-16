"""Benchmark Klar against Python, Go and C: compile time and run time.

Usage: python3 bench/run.py            (writes bench/RESULTS.md)
Each timing is the median of several runs, wall clock, on the same machine.
"""

import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BENCHES = ["fib", "loop", "mandel", "lists", "words"]
OUT = tempfile.mkdtemp(prefix="klarbench")
ENV = dict(os.environ, PYTHONPATH=ROOT, GOCACHE=os.path.join(OUT, "gocache"))
TIMEOUT = 120


def timed(cmd, repeat=3, timeout=TIMEOUT, stdin=None):
    times, out = [], None
    for _ in range(repeat):
        t = time.perf_counter()
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, env=ENV, timeout=timeout, cwd=HERE)
        except subprocess.TimeoutExpired:
            return None, None
        times.append(time.perf_counter() - t)
        if r.returncode != 0:
            raise RuntimeError(f"{cmd} failed: {r.stderr[:2000]}")
        out = r.stdout
    return statistics.median(times), out


def fmt(t):
    if t is None:
        return f"> {TIMEOUT} s"
    return f"{t * 1000:.0f} ms" if t < 1 else f"{t:.2f} s"


def main():
    have_tcc = shutil.which("tcc") is not None
    rows_compile, rows_run = [], []
    # warm the Go build cache once (the standard library is compiled on first use)
    warm = os.path.join(OUT, "warm")
    subprocess.run(["go", "build", "-o", warm, "fib.go"], env=ENV, cwd=HERE, capture_output=True)
    for b in BENCHES:
        exe = lambda tag: os.path.join(OUT, f"{b}_{tag}")
        compile_t = {}
        klarc = os.path.join(ROOT, "klarc")
        compile_t["Klar (tcc)"] = timed([klarc, "build", f"{b}.klar", "-o", exe("kdev")], repeat=5)[0] if have_tcc else None
        compile_t["Klar --release (gcc -O2)"] = timed([klarc, "build", "--release", f"{b}.klar", "-o", exe("krel")])[0]
        compile_t["Go (warm cache)"] = timed(["go", "build", "-o", exe("go"), f"{b}.go"], repeat=5)[0]
        if b == BENCHES[0]:
            cold = dict(ENV, GOCACHE=tempfile.mkdtemp(prefix="gocold"))
            t = time.perf_counter()
            subprocess.run(["go", "build", "-o", exe("gocold"), f"{b}.go"], env=cold, cwd=HERE, capture_output=True)
            compile_t["Go (cold cache)"] = time.perf_counter() - t
        compile_t["C (gcc -O2)"] = timed(["gcc", "-O2", "-o", exe("c"), f"{b}.c"])[0]
        if have_tcc:
            compile_t["C (tcc)"] = timed(["tcc", "-o", exe("ctcc"), f"{b}.c"], repeat=5)[0]
        rows_compile.append((b, compile_t))

        run_t, outs = {}, {}
        for label, cmd, rep in [
            ("Klar compiled (gcc -O2)", [exe("krel")], 3),
            ("Klar compiled (tcc)", [exe("kdev")], 3),
            ("C (gcc -O2)", [exe("c")], 3),
            ("Go", [exe("go")], 3),
            ("Python 3", [sys.executable, f"{b}.py"], 1),
            ("Klar interpreter", [sys.executable, "-m", "klar", "run", f"{b}.klar"], 1),
        ]:
            if label == "Klar compiled (tcc)" and not have_tcc:
                continue
            t, o = timed(cmd, repeat=rep, timeout=TIMEOUT if rep == 1 else 60)
            run_t[label] = t
            if o is not None:
                outs[label] = " ".join(o.split())
        distinct = set(outs.values())
        if len(distinct) != 1:
            raise RuntimeError(f"{b}: outputs differ: {outs}")
        rows_run.append((b, run_t))
        print(b, {k: fmt(v) for k, v in run_t.items()}, file=sys.stderr)

    lines = ["# Klar benchmarks", "",
             f"Machine: {os.cpu_count()} CPUs, Linux. Go {subprocess.run(['go', 'version'], capture_output=True, text=True).stdout.split()[2]}, "
             f"{subprocess.run(['gcc', '--version'], capture_output=True, text=True).stdout.splitlines()[0]}, Python {sys.version.split()[0]}.",
             "Wall-clock medians. Compile time includes everything from source file to executable "
             "(for Klar: starting Python, parsing, type checking, generating C and running the C compiler). "
             "Go's cold-cache build (first build after installing Go or clearing its cache) is measured once.",
             "", "## Run time", ""]
    labels = list(rows_run[0][1].keys())
    lines.append("| Benchmark | " + " | ".join(labels) + " |")
    lines.append("|---|" + "---|" * len(labels))
    for b, r in rows_run:
        lines.append(f"| {b} | " + " | ".join(fmt(r.get(l)) for l in labels) + " |")
    lines += ["", "## Compile time", ""]
    labels = []
    for _, r in rows_compile:
        labels += [l for l in r if l not in labels]
    lines.append("| Benchmark | " + " | ".join(labels) + " |")
    lines.append("|---|" + "---|" * len(labels))
    for b, r in rows_compile:
        lines.append(f"| {b} | " + " | ".join(fmt(r[l]) if l in r else "n/a" for l in labels) + " |")
    lines += ["", "Programs: `fib` = recursive fib(32); `loop` = 100M iterations of integer arithmetic; "
              "`mandel` = 1000x1000 Mandelbrot, 100 iterations; `lists` = push 5M ints, filter, map, fold; "
              "`words` = count 2M words from a 5,000-word vocabulary in a map."]
    with open(os.path.join(HERE, "RESULTS.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
