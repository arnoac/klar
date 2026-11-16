"""Load-test Klar Board from its production build against a hand-written Node.js server.

    python3 bench/deploy/run.py [--seconds 10] [--conc 32]

Builds Klar Board three ways: `klar build --release` (one native executable with the app built in), `klar build
--target node` (one .mjs file), and board_node.mjs (the same endpoints written by hand for Node with node:http and
node:sqlite). Each server starts in an empty folder with a fresh database; load.go (needs Go) then signs up a user,
creates a board with 20 cards and runs each kind of request for --seconds. Prints a Markdown table.
"""

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
KLAR = [sys.executable, "-m", "klar"]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def rss_kb(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            return next(int(line.split()[1]) for line in f if line.startswith("VmRSS"))
    except (OSError, StopIteration):
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=10)
    ap.add_argument("--conc", type=int, default=32)
    a = ap.parse_args()
    tmp = tempfile.mkdtemp(prefix="klardeploy")
    env = dict(os.environ, PYTHONPATH=ROOT)
    load = os.path.join(tmp, "load")
    subprocess.run(["go", "build", "-o", load, os.path.join(HERE, "load.go")], check=True)
    board = os.path.join(ROOT, "examples", "apps", "board")
    exe, mjs = os.path.join(tmp, "board"), os.path.join(tmp, "board.mjs")
    subprocess.run(KLAR + ["build", "--release", board, "-o", exe], check=True, env=env)
    subprocess.run(KLAR + ["build", "--target", "node", board, "-o", mjs], check=True, env=env)
    page = os.path.join(tmp, "app")                 # the hand-written server reads app.html from disk
    os.makedirs(page)
    subprocess.run(KLAR + ["build", os.path.join(board, "app.klar"), "-o", os.path.join(page, "app.html")],
                   check=True, env=env)
    servers = [
        ("Klar, native executable", [exe], {}),
        ("Klar, --target node", ["node", mjs], {}),
        ("hand-written Node.js", ["node", os.path.join(HERE, "board_node.mjs")], {"APP_DIR": page}),
    ]
    kinds = [("health", True), ("page", True), ("page", False), ("read", True), ("write", True)]
    rows = {}
    for name, cmd, extra in servers:
        d = tempfile.mkdtemp(dir=tmp)
        port = free_port()
        proc = subprocess.Popen(cmd, cwd=d, env=dict(os.environ, PORT=str(port), KLAR_LOG="off", **extra),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t0 = time.time()
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1).read()
                break
            except OSError:
                time.sleep(0.01)
        start_ms = (time.time() - t0) * 1000
        idle = rss_kb(proc.pid)
        out = []
        for kind, gz in kinds:
            r = subprocess.run([load, "-base", f"http://127.0.0.1:{port}", "-kind", kind, "-c", str(a.conc),
                                "-d", f"{a.seconds}s", f"-gzip={str(gz).lower()}"], capture_output=True, text=True)
            print(name, r.stdout.strip() or r.stderr.strip(), file=sys.stderr)
            f = r.stdout.split()
            out.append(f"{float(f[2]):,.0f} ({f[8]} ms)" if r.returncode == 0 else "failed")
        rows[name] = out + [f"{start_ms:.0f} ms", f"{idle // 1024} / {rss_kb(proc.pid) // 1024} MB"]
        proc.terminate()
        proc.wait(15)
    print(f"\n{a.conc} connections, {a.seconds} s per row; requests per second (p99 latency).\n")
    heads = ["/healthz", "app page, gzip", "app page, no gzip", "board read", "add card", "start", "memory idle / after"]
    print("| | " + " | ".join(n for n, _, _ in servers) + " |")
    print("|---|" + "---:|" * len(servers))
    for i, h in enumerate(heads):
        print(f"| {h} | " + " | ".join(rows[n][i] for n, _, _ in servers) + " |")
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
