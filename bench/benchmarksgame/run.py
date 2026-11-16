"""Run Klar on Computer Language Benchmarks Game programs against C, Go and Python.

Usage:
  python3 bench/benchmarksgame/run.py            scaled sizes, all four languages (median of 3; Python once)
  python3 bench/benchmarksgame/run.py --full     also the official Benchmarks Game sizes, compiled languages only
Writes RESULTS.md next to this file. Every program's output is checked to be identical across languages.

All reference programs are single-threaded, plain implementations of the official algorithms, built with
the same optimisation level Klar uses (gcc -O2), because Klar has no threads or SIMD yet. The fastest entries
on the Benchmarks Game site use all cores and hand-written SIMD, so they are not a like-for-like comparison.
"""

import hashlib
import os
import signal
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT = tempfile.mkdtemp(prefix="klarbg")
ENV = dict(os.environ, GOCACHE=os.path.join(OUT, "gocache"))

# (program, scaled size for all languages, official Benchmarks Game size)
PROGRAMS = [
    ("nbody", 5_000_000, 50_000_000),
    ("spectralnorm", 2_000, 5_500),
    ("fannkuchredux", 10, 12),
    ("binarytrees", 18, 21),
    ("mandelbrot", 4_000, 16_000),
    ("fasta", 2_500_000, 25_000_000),
    # these read the fasta program's output on stdin; the size is fasta's N for that input
    ("knucleotide", 2_500_000, 25_000_000),
    ("revcomp", 2_500_000, 25_000_000),
    ("regexredux", 100_000, 5_000_000),
]
STDIN_PROGRAMS = {"knucleotide", "revcomp", "regexredux"}
LIBS = ["-lpcre2-8", "-lpthread"]   # regexredux uses PCRE2, like `klar build` does for std.regex
LIBGC = "/usr/lib/x86_64-linux-gnu/libgc.so.1"
MEMTIME = os.path.join(OUT, "memtime")
OFFICIAL_TIMEOUT = 300


def build_klar(src, exe):
    """`klar build --release`. If the Boehm GC library is present but its headers (libgc-dev) are not, compile
    the generated C exactly as `klar build --release` would with libgc-dev, using a minimal gc.h shim."""
    sys.path.insert(0, ROOT)
    from klar.cli import has_gc
    if has_gc() or not os.path.exists(LIBGC):
        subprocess.run([sys.executable, "-S", "-m", "klar", "build", "--release", src, "-o", exe],
                       check=True, env=dict(ENV, PYTHONPATH=ROOT), capture_output=True)
        return "gc" if has_gc() else "no GC"
    subprocess.run([sys.executable, "-S", "-m", "klar", "build", "--emit-c", src, "-o", exe],
                   check=True, env=dict(ENV, PYTHONPATH=ROOT), capture_output=True)
    subprocess.run(["gcc", "-O2", "-std=gnu11", "-w", "-DKLAR_GC", "-I", os.path.join(HERE, "gc"), "-o", exe,
                    exe + ".c", "-lm", LIBGC] + LIBS, check=True)
    return "gc"


def run(cmd, timeout, stdin_path=None):
    """Wall time, peak RSS (MB) and an md5 of stdout for one run, or (None, None, None) on timeout."""
    with tempfile.TemporaryFile() as out, open(stdin_path or os.devnull, "rb") as stdin:
        t = time.perf_counter()
        p = subprocess.Popen([MEMTIME] + cmd, stdin=stdin, stdout=out, stderr=subprocess.PIPE, cwd=HERE,
                             start_new_session=True)
        try:
            _, err = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.communicate()
            return None, None, None
        elapsed = time.perf_counter() - t
        if p.returncode != 0:
            raise RuntimeError(f"{cmd} failed: {err[:2000]}")
        rss_kb = int(err.decode().rsplit("MAXRSS_KB", 1)[1].split()[0])
        out.seek(0)
        return elapsed, rss_kb / 1024, hashlib.md5(out.read()).hexdigest()


def fmt(t):
    if t is None:
        return f"> {OFFICIAL_TIMEOUT} s"
    return f"{t * 1000:.0f} ms" if t < 1 else f"{t:.2f} s"


def table(rows, labels, title):
    lines = [f"### {title}", "", "| Program | size (N) | " + " | ".join(labels) + " | Klar / C | Klar / Go |",
             "|---|---|" + "---|" * (len(labels) + 2)]
    for name, n, res in rows:
        cells = []
        for l in labels:
            t, mem = res.get(l, (None, None))
            cells.append("n/a" if l not in res else f"{fmt(t)} ({mem:.0f} MB)" if t is not None else fmt(None))
        def ratio(other):
            k, o = res.get("Klar --release", (None,))[0], res.get(other, (None,))[0]
            return f"{k / o:.1f}x" if k and o else "n/a"
        lines.append(f"| {name} | {n:,} | " + " | ".join(cells) + f" | {ratio('C (gcc -O2)')} | {ratio('Go')} |")
    return lines


def main():
    full = "--full" in sys.argv
    subprocess.run(["gcc", "-O2", "-o", MEMTIME, "memtime.c"], check=True, cwd=HERE)
    exes = {}
    gc_mode = None
    for name, _, _ in PROGRAMS:
        e = lambda tag: os.path.join(OUT, f"{name}_{tag}")
        subprocess.run(["gcc", "-O2", "-o", e("c"), f"{name}.c", "-lm"] + LIBS, check=True, cwd=HERE)
        subprocess.run(["go", "build", "-o", e("go"), f"{name}.go"], check=True, cwd=HERE, env=ENV)
        gc_mode = build_klar(os.path.join(HERE, f"{name}.klar"), e("klar"))
        subprocess.run([sys.executable, "-S", "-m", "klar", "build", "--target", "node", f"{name}.klar", "-o",
                        e("klar") + ".mjs"], check=True, cwd=HERE, env=dict(ENV, PYTHONPATH=ROOT), capture_output=True)
        exes[name] = {"Klar --release": [e("klar")], "Klar on Node": ["node", e("klar") + ".mjs"],
                      "C (gcc -O2)": [e("c")], "Go": [e("go")], "Python 3": [sys.executable, f"{name}.py"]}

    def fasta_input(n):
        path = os.path.join(OUT, f"fasta_{n}.txt")
        if not os.path.exists(path):
            with open(path, "wb") as f:
                subprocess.run([os.path.join(OUT, "fasta_c"), str(n)], stdout=f, check=True)
        return path

    def measure(name, n, langs, repeat, timeout):
        res, digests = {}, {}
        stdin_path = fasta_input(n) if name in STDIN_PROGRAMS else None
        args = [] if stdin_path else [str(n)]
        for lang in langs:
            samples = []
            for _ in range(1 if lang == "Python 3" else repeat):
                t, mem, digest = run(exes[name][lang] + args, timeout, stdin_path)
                if t is None:
                    break
                samples.append((t, mem))
                digests[lang] = digest
            res[lang] = (statistics.median(s[0] for s in samples), max(s[1] for s in samples)) if samples else (None, None)
            print(f"{name} {n} {lang}: {fmt(res[lang][0])}", file=sys.stderr)
        if len(set(digests.values())) > 1:
            raise RuntimeError(f"{name} {n}: outputs differ: {digests}")
        return res

    langs = ["Klar --release", "Klar on Node", "C (gcc -O2)", "Go", "Python 3"]
    scaled = [(name, n, measure(name, n, langs, 3, 300)) for name, n, _ in PROGRAMS]
    official = []
    if full:
        official = [(name, n, measure(name, n, langs[:4], 1, OFFICIAL_TIMEOUT)) for name, _, n in PROGRAMS]

    go = subprocess.run(["go", "version"], capture_output=True, text=True).stdout.split()[2]
    gcc = subprocess.run(["gcc", "--version"], capture_output=True, text=True).stdout.splitlines()[0]
    lines = ["# Klar on the Computer Language Benchmarks Game", "",
             f"Machine: {os.cpu_count()} CPUs, Linux. {gcc}; Go {go}; Python {sys.version.split()[0]}. "
             f"Klar built with `klar build --release` (gcc -O2, Boehm GC: {gc_mode}) and with `klar build --target node` "
             f"(Node {subprocess.run(['node', '--version'], capture_output=True, text=True).stdout.strip()}).",
             "Wall-clock time (median of 3, Python run once) and peak memory. Every language's output is checked to be "
             "byte-identical. All programs are single-threaded; see README.md for what that means. "
             "knucleotide, revcomp and regexredux read the output of `fasta N` on stdin.", ""]
    lines += table(scaled, langs, "Scaled sizes (all languages)")
    if official:
        lines += [""] + table(official, langs[:4], "Official Benchmarks Game sizes (compiled languages, 1 run)")
    with open(os.path.join(HERE, "RESULTS.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
