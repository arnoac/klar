"""Debugging: `klar debug` (command line and the Debug Adapter Protocol) and Klar stack traces from JavaScript builds."""

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import queue
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")

LIB = '''// helpers
fn pick(xs: list[int], i: int) -> int {
  return xs[i]
}

fn deeper(xs: list[int]) -> int {
  let n = len(xs)
  return pick(xs, n + 2)
}
'''

MAIN = '''use lib { deeper }

struct Card { title: str, votes: int }

fn total(cards: list[Card]) -> int {
  var sum = 0
  for c in cards {
    sum = sum + c.votes
  }
  return sum
}

fn main() uses io {
  let cards = [Card(title: "a", votes: 2), Card(title: "b", votes: 3)]
  print(total(cards))
  let xs = [1, 2, 3]
  print(deeper(xs))
}
'''


def klar(*args, cwd):
    return subprocess.run([sys.executable, "-m", "klar", *args], cwd=cwd, capture_output=True, text=True,
                          env=dict(os.environ, PYTHONPATH=ROOT), timeout=120)


class Project(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        for name, text in (("lib.klar", LIB), ("main.klar", MAIN)):
            with open(os.path.join(self.dir, name), "w") as f:
                f.write(text)


@unittest.skipUnless(NODE, "needs node")
class JavaScriptTraces(Project):
    def test_runtime_errors_show_klar_frames_and_files(self):
        r = klar("build", "--target", "node", "main.klar", "-o", "main.mjs", cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([NODE, "main.mjs"], cwd=self.dir, capture_output=True, text=True)
        self.assertEqual(r.stdout, "5\n")
        self.assertIn("error[E401] lib.klar:3:12: index 5 is out of bounds for length 3", r.stderr)
        self.assertIn("  at pick (lib.klar:3)\n  at deeper (lib.klar:8)\n  at main (main.klar:17)", r.stderr)
        # with Node's own source map support the trace is the same
        r = subprocess.run([NODE, "--enable-source-maps", "main.mjs"], cwd=self.dir, capture_output=True, text=True)
        self.assertIn("  at pick (lib.klar:3)\n  at deeper (lib.klar:8)\n  at main (main.klar:17)", r.stderr)

    def test_source_map_points_at_klar_lines(self):
        klar("build", "--target", "node", "main.klar", "-o", "main.mjs", cwd=self.dir)
        with open(os.path.join(self.dir, "main.mjs")) as f:
            text = f.read()
        smap = json.loads(base64.b64decode(re.search(r"sourceMappingURL=data:application/json;base64,(\S+)", text).group(1)))
        self.assertEqual(smap["sources"], ["main.klar", "lib.klar"])
        check = ('import { SourceMap } from "node:module"; import fs from "node:fs";'
                 'const t = fs.readFileSync("main.mjs", "utf8").split("\\n");'
                 'const sm = new SourceMap(JSON.parse(process.argv[1]));'
                 'const i = t.findIndex(l => l.includes("sum = $add("));'
                 'const e = sm.findEntry(i, t[i].search(/\\S/)); console.log(e.originalSource, e.originalLine + 1);')
        r = subprocess.run([NODE, "--input-type=module", "-e", check, json.dumps(smap)], cwd=self.dir,
                           capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "main.klar 8", r.stderr)


def debuggable():
    sys.path.insert(0, ROOT)
    from klar.debug import supported
    return supported()


class Dap:
    """A minimal Debug Adapter Protocol client, as an editor drives `klar debug --dap`."""

    def __init__(self, cwd):
        self.proc = subprocess.Popen([sys.executable, "-m", "klar", "debug", "--dap"], cwd=cwd, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ, PYTHONPATH=ROOT))
        self.seq = 0
        self.msgs = queue.Queue()
        self.pending = []
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        out = self.proc.stdout
        while True:
            headers = {}
            while True:
                line = out.readline()
                if not line:
                    self.msgs.put(None)
                    return
                line = line.decode().strip()
                if not line:
                    break
                k, _, v = line.partition(":")
                headers[k.lower()] = v.strip()
            self.msgs.put(json.loads(out.read(int(headers["content-length"]))))

    def next(self, pred, timeout=60):
        for i, m in enumerate(self.pending):
            if pred(m):
                return self.pending.pop(i)
        while True:
            m = self.msgs.get(timeout=timeout)
            if m is None:
                raise AssertionError("the adapter exited: " + self.proc.stderr.read().decode())
            if pred(m):
                return m
            self.pending.append(m)

    def request(self, command, args=None):
        self.seq += 1
        seq = self.seq
        body = json.dumps({"seq": seq, "type": "request", "command": command, "arguments": args or {}}).encode()
        self.proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
        self.proc.stdin.flush()
        r = self.next(lambda m: m.get("type") == "response" and m.get("request_seq") == seq)
        return r

    def event(self, name):
        return self.next(lambda m: m.get("type") == "event" and m.get("event") == name)

    def close(self):
        try:
            self.request("disconnect")
        except Exception:
            pass
        self.proc.kill()
        self.proc.wait()
        for f in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            f.close()


@unittest.skipUnless(debuggable(), "the interpreter cannot be debugged")
class DebugAdapter(Project):
    def start(self, breaks, **launch):
        d = Dap(self.dir)
        self.addCleanup(d.close)
        self.assertTrue(d.request("initialize", {"adapterID": "klar", "linesStartAt1": True})["success"])
        d.event("initialized")
        r = d.request("launch", {"program": os.path.join(self.dir, "main.klar"), **launch})
        self.assertTrue(r["success"], r)
        for file, lines in breaks.items():
            r = d.request("setBreakpoints", {"source": {"path": os.path.join(self.dir, file)},
                                             "breakpoints": [{"line": n} for n in lines]})
            self.assertTrue(all(b["verified"] for b in r["body"]["breakpoints"]))
        d.request("configurationDone")
        return d

    def stopped(self, d):
        ev = d.event("stopped")
        st = d.request("stackTrace", {"threadId": 1})["body"]["stackFrames"]
        return ev["body"], st

    def variables(self, d, frame_id):
        [scope] = d.request("scopes", {"frameId": frame_id})["body"]["scopes"]
        vs = d.request("variables", {"variablesReference": scope["variablesReference"]})["body"]["variables"]
        return {v["name"]: v for v in vs}

    def test_breakpoint_variables_and_stepping(self):
        d = self.start({"main.klar": [8]})
        ev, st = self.stopped(d)
        self.assertEqual(ev["reason"], "breakpoint")
        self.assertEqual([(f["name"], os.path.basename(f["source"]["path"]), f["line"]) for f in st],
                         [("total", "main.klar", 8), ("main", "main.klar", 15)])
        vs = self.variables(d, st[0]["id"])
        self.assertEqual(vs["sum"]["value"], "0")
        self.assertEqual(vs["c"]["value"], 'Card(title: "a", votes: 2)')
        fields = d.request("variables", {"variablesReference": vs["c"]["variablesReference"]})["body"]["variables"]
        self.assertEqual([(f["name"], f["value"]) for f in fields], [("title", '"a"'), ("votes", "2")])
        r = d.request("evaluate", {"expression": "c.votes", "frameId": st[0]["id"]})
        self.assertEqual(r["body"]["result"], "2")
        # the next pass through the loop
        d.request("continue", {"threadId": 1})
        ev, st = self.stopped(d)
        self.assertEqual(self.variables(d, st[0]["id"])["sum"]["value"], "2")
        # step out of total() back into main, then over the print
        d.request("setBreakpoints", {"source": {"path": os.path.join(self.dir, "main.klar")}, "breakpoints": []})
        d.request("stepOut", {"threadId": 1})
        ev, st = self.stopped(d)
        self.assertEqual((ev["reason"], st[0]["name"], st[0]["line"]), ("step", "main", 15))
        d.request("next", {"threadId": 1})
        ev, st = self.stopped(d)
        self.assertEqual((st[0]["name"], st[0]["line"]), ("main", 16))
        self.assertEqual(d.event("output")["body"]["output"], "5\n")       # the print ran
        d.request("stepIn", {"threadId": 1})
        ev, st = self.stopped(d)
        self.assertEqual((st[0]["name"], st[0]["line"]), ("main", 17))
        d.request("stepIn", {"threadId": 1})
        ev, st = self.stopped(d)
        self.assertEqual((st[0]["name"], os.path.basename(st[0]["source"]["path"]), st[0]["line"]),
                         ("deeper", "lib.klar", 7))

    def test_stops_where_a_runtime_error_happens(self):
        d = self.start({})
        ev, st = self.stopped(d)
        self.assertEqual(ev["reason"], "exception")
        self.assertIn("index 5 is out of bounds", ev["text"])
        self.assertEqual([(f["name"], f["line"]) for f in st], [("pick", 3), ("deeper", 8), ("main", 17)])
        self.assertEqual(self.variables(d, st[0]["id"])["i"]["value"], "5")
        self.assertEqual(self.variables(d, st[1]["id"])["n"]["value"], "3")
        d.request("continue", {"threadId": 1})
        self.assertEqual(d.event("exited")["body"]["exitCode"], 1)


@unittest.skipUnless(debuggable(), "the interpreter cannot be debugged")
class DebugCommand(Project):
    def test_break_prints_stack_and_variables(self):
        r = klar("debug", "main.klar", "--break", "8", cwd=self.dir)
        self.assertEqual(r.stdout, "5\n")
        self.assertIn("[break] main.klar:8 in total\n    cards = [", r.stderr)
        self.assertIn("    sum = 2\n", r.stderr)
        self.assertIn("stack: total (main.klar:8) <- main (main.klar:15)", r.stderr)

    def test_json_and_errors(self):
        r = klar("debug", "main.klar", "--break", "lib.klar:7", "--json", cwd=self.dir)
        stops = [json.loads(l) for l in r.stderr.splitlines() if l.startswith("{")]
        self.assertEqual([s["event"] for s in stops], ["break", "error"])
        self.assertEqual(stops[0]["frames"][0]["variables"], {"xs": "[1, 2, 3]"})
        self.assertEqual([f["at"] for f in stops[1]["frames"]], ["lib.klar:3", "lib.klar:8", "main.klar:17"])
        self.assertEqual(stops[1]["frames"][1]["variables"]["n"], "3")
        self.assertEqual(stops[1]["error"]["code"], "E401")
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
