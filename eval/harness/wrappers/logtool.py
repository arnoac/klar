"""Run a tool and record the call in .eval/tools.jsonl of the current workspace.

Usage: logtool.py NAME REAL_PATH ARGS...

The record holds the arguments, the exit status and every error code printed (Klar `E301`, TypeScript
`TS2322`), so a report can count how often the agent checked its work and which errors it ran into.
"""

import json
import os
import re
import subprocess
import sys
import time

CODES = re.compile(r"\b(E\d{3}|TS\d{4})\b")


def main():
    name, real, args = sys.argv[1], sys.argv[2], sys.argv[3:]
    cmd = ["sh", real] + args if real.endswith("klarc") else [real] + args
    start = time.time()
    if args[:1] in (["run"], ["dev"]):
        # Programs may be long-running servers: record the call, then hand over without capturing output.
        record({"time": round(start, 3), "tool": name, "args": args, "exit": None, "codes": []})
        os.execvp(cmd[0], cmd)
    p = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(p.stdout)
    sys.stderr.write(p.stderr)
    out = p.stdout + p.stderr
    record({
        "time": round(start, 3), "tool": name, "args": args, "exit": p.returncode,
        "seconds": round(time.time() - start, 3), "codes": CODES.findall(out), "output": out[-4000:],
    })
    sys.exit(p.returncode)


def record(entry):
    log_dir = os.environ.get("KLAREVAL_LOG_DIR") or os.path.join(os.getcwd(), ".eval")
    try:
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "tools.jsonl"), "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


if __name__ == "__main__":
    main()
