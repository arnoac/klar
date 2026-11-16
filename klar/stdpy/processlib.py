import os
import subprocess
import sys


def run(cmd, stdin):
    r = subprocess.run(cmd, input=stdin, capture_output=True, text=True)
    return {"code": r.returncode, "stdout": r.stdout, "stderr": r.stderr}


def env_get(name):
    return os.environ.get(name)


def exit(code):
    sys.stdout.flush()
    sys.exit(code)
