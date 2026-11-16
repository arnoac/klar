"""Benchmark `klar run` (Klar compiled to Python code at load time) against Python 3 running the same programs.

Usage: python3 bench/interp.py [--before PATH]      (writes bench/INTERP.md)
--before PATH also times `klar run` from another checkout of Klar at PATH (an older version), as a column.
Every program's output is checked to be identical to Python's. Wall-clock time, median of 3 runs
(1 run for the older version), including startup: parsing, type checking and code generation.
"""

import hashlib
import os
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GAME = os.path.join(HERE, "benchmarksgame")
TIMEOUT = 300

# (program, directory, size argument or None, stdin from `fasta N` or None)
PROGRAMS = [
    ("fib", HERE, None, None),
    ("loop", HERE, None, None),
    ("mandel", HERE, None, None),
    ("lists", HERE, None, None),
    ("words", HERE, None, None),
    ("nbody", GAME, 100_000, None),
    ("spectralnorm", GAME, 300, None),
    ("fannkuchredux", GAME, 9, None),
    ("binarytrees", GAME, 15, None),
    ("mandelbrot", GAME, 1_000, None),
    ("fasta", GAME, 250_000, None),
    ("knucleotide", GAME, None, 250_000),
    ("revcomp", GAME, None, 250_000),
    ("regexredux", GAME, None, 100_000),
]


def run(cmd, cwd, root, stdin_path, repeat):
    times, digest = [], None
    for _ in range(repeat):
        with open(stdin_path or os.devnull, "rb") as stdin:
            t = time.perf_counter()
            try:
                r = subprocess.run(cmd, cwd=cwd, stdin=stdin, capture_output=True, timeout=TIMEOUT,
                                   env=dict(os.environ, PYTHONPATH=root))
            except subprocess.TimeoutExpired:
                return None, None
            times.append(time.perf_counter() - t)
        if r.returncode != 0:
            raise RuntimeError(f"{cmd} failed: {r.stderr[:2000]}")
        digest = hashlib.md5(r.stdout).hexdigest()
    return statistics.median(times), digest


def fmt(t):
    if t is None:
        return f"> {TIMEOUT} s"
    return f"{t * 1000:.0f} ms" if t < 1 else f"{t:.2f} s"


def main():
    before = sys.argv[sys.argv.index("--before") + 1] if "--before" in sys.argv else None
    tmp = tempfile.mkdtemp(prefix="klarinterp")
    rows = []
    for name, cwd, n, fasta_n in PROGRAMS:
        stdin_path = None
        if fasta_n:
            stdin_path = os.path.join(tmp, f"fasta{fasta_n}.txt")
            if not os.path.exists(stdin_path):
                with open(stdin_path, "wb") as f:
                    subprocess.run([sys.executable, "fasta.py", str(fasta_n)], cwd=GAME, stdout=f, check=True)
        args = [str(n)] if n else []
        py, py_md5 = run([sys.executable, f"{name}.py"] + args, cwd, ROOT, stdin_path, 3)
        klar_cmd = [sys.executable, "-m", "klar", "run", f"{name}.klar"] + args
        now, now_md5 = run(klar_cmd, cwd, ROOT, stdin_path, 3)
        if now_md5 != py_md5:
            raise RuntimeError(f"{name}: klar run and Python print different output")
        old = run(klar_cmd, cwd, before, stdin_path, 1)[0] if before else None
        rows.append((name, n or fasta_n, py, now, old))
        print(name, fmt(py), fmt(now), fmt(old) if before else "", flush=True)
    v = sys.version.split()[0]
    lines = ["# `klar run` speed", "",
             f"Machine: {os.cpu_count()} CPUs, Linux. Python {v}. `klar run` compiles the checked program to Python "
             "code and runs that (klar/pygen.py). Wall-clock time including startup (parsing, type checking, code "
             "generation), median of 3 runs. Every program's output is checked to equal the Python version's. "
             "Programs and sizes: bench/*.klar (as in RESULTS.md) and bench/benchmarksgame at the sizes shown; "
             "knucleotide, revcomp and regexredux read the output of `fasta N`.", ""]
    head = "| Program | size (N) | Python 3 | klar run |"
    sep = "|---|---|---|---|"
    if before:
        head += " klar run before (tree-walking evaluator, 1 run) | speed-up |"
        sep += "---|---|"
    lines += [head + " klar run / Python |", sep + "---|"]
    for name, n, py, now, old in rows:
        cells = [name, f"{n:,}" if n else "as in source", fmt(py), fmt(now)]
        if before:
            cells += [fmt(old), f"{old / now:.0f}x" if old and now else f"> {TIMEOUT / now:.0f}x" if now else "n/a"]
        cells.append(f"{now / py:.1f}x" if now and py else "n/a")
        lines.append("| " + " | ".join(cells) + " |")
    out = os.path.join(HERE, "INTERP.md")
    with open(out, "w") as f:
        f.write("\n".join(lines) + "\n")
    print("wrote", out)


if __name__ == "__main__":
    main()
