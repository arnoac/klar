"""std.task: tasks and channels behave the same in the interpreter, native builds and Node."""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_web import NODE, PROGRAMS, ROOT, build_node, free_port, klar, wait_for, write  # noqa: E402

CC = shutil.which("tcc") or shutil.which("gcc") or shutil.which("cc") or shutil.which("clang")


def backends(path):
    """{name: command} for every backend available here."""
    runs = {"interp": [sys.executable, "-m", "klar", "run", path]}
    if CC:
        exe = os.path.join(tempfile.mkdtemp(), "prog")
        b = klar("build", path, "-o", exe)
        assert b.returncode == 0, b.stderr
        runs["native"] = [exe]
    if NODE:
        mjs, b = build_node(path)
        assert b.returncode == 0, b.stderr
        runs["node"] = [NODE, mjs]
    return runs


def run(cmd, env=None):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                          env=dict(os.environ, PYTHONPATH=ROOT, **(env or {})))


class Tasks(unittest.TestCase):
    def test_same_output_everywhere(self):
        expected = None
        for name, cmd in backends(os.path.join(PROGRAMS, "tasks.klar")).items():
            with self.subTest(backend=name):
                r = run(cmd)
                self.assertEqual(r.returncode, 0, r.stderr)
                expected = expected or r.stdout
                self.assertEqual(r.stdout, expected)
        self.assertIn("ping 0\npong 0\nping 1\n", expected)

    def test_task_all_overlaps_waiting(self):
        p = write('''use std.task { task_all }
use std.time { time_sleep, time_now }
fn nap(n: int) -> int uses io {
  time_sleep(0.4)
  return n
}
fn main() uses io {
  let t0 = time_now()
  print(task_all([fn() => nap(1), fn() => nap(2), fn() => nap(3), fn() => nap(4)]))
  print(time_now() - t0 < 1.0)
}
''')
        for name, cmd in backends(p).items():
            with self.subTest(backend=name):
                r = run(cmd)
                self.assertEqual(r.stdout, "[1, 2, 3, 4]\ntrue\n", r.stderr)

    def test_deadlocks_and_errors_in_tasks(self):
        cases = {
            "E409": "let c: Chan[int] = chan_new()\n  task_group(fn(g) {\n    task_spawn(g, fn() => print(chan_recv(c)))\n  })",
            "E401": "print(task_all([fn() => 1, fn() => [1][3]]))",
            "E407": "let c: Chan[TaskGroup] = chan_new()\n  task_group(fn(g) => chan_send(c, g))\n"
                    "  task_spawn(unwrap(chan_recv(c)), fn() => print(1))",
        }
        for code, body in cases.items():
            p = write("use std.task { Chan, TaskGroup, task_all, task_group, task_spawn, chan_new, chan_send, chan_recv }\n"
                      "fn main() uses io {\n  " + body + "\n}\n")
            for name, cmd in backends(p).items():
                with self.subTest(code=code, backend=name):
                    r = run(cmd)
                    self.assertEqual(r.returncode, 1, r.stdout)
                    self.assertIn(f"error[{code}]", r.stderr)

    def test_server_handles_requests_concurrently(self):
        """A slow handler (waiting on io) does not hold up other requests."""
        p = write('''use std.http { HttpRequest, HttpResponse, http_serve, http_text }
use std.process { env_get }
use std.time { time_sleep }
fn handle(req: HttpRequest) -> HttpResponse uses io {
  if req.path == "/slow" {
    time_sleep(1.5)
  }
  return http_text(200, req.path)
}
fn main() uses io {
  let port = unwrap_or(parse_int(unwrap_or(env_get("PORT"), "8080")), 8080)
  print(http_serve(port, handle))
}
''')
        for name, cmd in backends(p).items():
            with self.subTest(backend=name):
                port = free_port()
                proc = subprocess.Popen(cmd, env=dict(os.environ, PYTHONPATH=ROOT, PORT=str(port)),
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.addCleanup(proc.kill)
                base = f"http://127.0.0.1:{port}"
                self.assertEqual(wait_for(base + "/fast"), "/fast")
                slow = threading.Thread(target=lambda: urllib.request.urlopen(base + "/slow", timeout=10).read())
                slow.start()
                time.sleep(0.2)
                t0 = time.time()
                self.assertEqual(urllib.request.urlopen(base + "/fast", timeout=10).read(), b"/fast")
                self.assertLess(time.time() - t0, 1.0)
                slow.join()
                proc.kill()


if __name__ == "__main__":
    unittest.main()
