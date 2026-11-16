"""Helpers for hidden acceptance tests: run a built program as a black box.

A task's hidden tests live in `tasks/NAME/hidden/tests.py` as functions named `test_*` that take a `Program`
and raise `Fail` (or AssertionError) with a short, readable message. The same tests grade every language.
"""

import http.client
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time


class Fail(Exception):
    pass


def expect(cond, message):
    if not cond:
        raise Fail(message)


def expect_eq(got, want, what="output"):
    if got != want:
        raise Fail(f"{what}: expected {short(want)}, got {short(got)}")


def short(value, limit=600):
    text = value if isinstance(value, str) else json.dumps(value)
    return repr(text) if len(text) <= limit else repr(text[:limit]) + f"... ({len(text)} chars)"


class Run:
    def __init__(self, code, out, err):
        self.code, self.out, self.err = code, out, err


class Program:
    def __init__(self, command, cwd, timeout=60):
        self.command, self.cwd, self.timeout = command, cwd, timeout

    def run(self, stdin="", args=(), env=None):
        full_env = dict(os.environ, **(env or {}))
        try:
            p = subprocess.run(self.command + list(args), input=stdin, cwd=self.cwd, env=full_env,
                               capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            raise Fail(f"timed out after {self.timeout}s")
        return Run(p.returncode, p.stdout, p.stderr)

    def server(self, data_dir=None):
        return Server(self, data_dir)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Response:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    def json(self):
        try:
            return json.loads(self.body)
        except ValueError:
            raise Fail(f"response is not JSON (status {self.status}): {short(self.body, 200)}")

    def content_type(self):
        return self.headers.get("content-type", "")


class Server:
    """Starts the program as a server on a free port with PORT and DATA_DIR set; stops it on exit."""

    def __init__(self, program, data_dir=None):
        self.program = program
        self.own_dir = data_dir is None
        self.data_dir = data_dir or tempfile.mkdtemp(prefix="klareval-data-")
        self.proc = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        if self.own_dir:
            shutil.rmtree(self.data_dir, ignore_errors=True)

    def start(self):
        self.port = free_port()
        env = dict(os.environ, PORT=str(self.port), DATA_DIR=self.data_dir)
        self.log = tempfile.TemporaryFile(mode="w+")
        self.proc = subprocess.Popen(self.program.command, cwd=self.program.cwd, env=env,
                                     stdout=self.log, stderr=subprocess.STDOUT, text=True)
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.proc.poll() is not None:
                self.log.seek(0)
                raise Fail(f"server exited with status {self.proc.returncode}: {short(self.log.read(), 400)}")
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.5).close()
                return
            except OSError:
                time.sleep(0.1)
        raise Fail("server did not start listening on PORT within 30s")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()

    def restart(self):
        self.stop()
        self.start()

    def request(self, method, path, body=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        headers = {}
        data = None
        if raw is not None:
            data = raw.encode()
            headers["Content-Type"] = "application/json"
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        try:
            conn.request(method, path, body=data, headers=headers)
            r = conn.getresponse()
            text = r.read().decode("utf-8", "replace")
        except (OSError, http.client.HTTPException) as e:
            raise Fail(f"{method} {path}: request failed ({e.__class__.__name__}: {e})")
        finally:
            conn.close()
        return Response(r.status, {k.lower(): v for k, v in r.getheaders()}, text)
