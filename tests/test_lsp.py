"""`klar lsp`: drive the language server the way an editor does and check each answer."""

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SHAPES = '''// Geometry helpers.
struct Point { x: float, y: float }

enum Shape {
  Circle(center: Point, radius: float),
  Square(corner: Point, side: float),
}

// The area of a shape.
fn area(s: Shape) -> float {
  return match s {
    Circle(c, r) => 3.14159 * r * r,
    Square(c, side) => side * side,
  }
}
'''

MAIN = '''use shapes { Point, Shape, Circle, area }

fn describe(s: Shape) -> str {
  let a = area(s)
  return "area {a}"
}

fn main() uses io {
  let p = Point(x: 1.0, y: 2.0)
  print(describe(Circle(p, 2.0)))
  print(p.x)
}
'''


class Client:
    def __init__(self, cwd):
        self.proc = subprocess.Popen([sys.executable, "-m", "klar", "lsp"], cwd=cwd, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     env=dict(os.environ, PYTHONPATH=ROOT))
        self.next_id = 0
        self.responses = {}
        self.notes = queue.Queue()
        self.cond = threading.Condition()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        out = self.proc.stdout
        while True:
            headers = {}
            while True:
                line = out.readline()
                if not line:
                    return
                line = line.decode().strip()
                if not line:
                    break
                k, _, v = line.partition(":")
                headers[k.lower()] = v.strip()
            msg = json.loads(out.read(int(headers["content-length"])))
            if "id" in msg and "method" not in msg:
                with self.cond:
                    self.responses[msg["id"]] = msg
                    self.cond.notify_all()
            else:
                self.notes.put(msg)

    def send(self, msg):
        body = json.dumps(msg).encode()
        self.proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
        self.proc.stdin.flush()

    def request(self, method, params):
        self.next_id += 1
        i = self.next_id
        self.send({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
        with self.cond:
            self.cond.wait_for(lambda: i in self.responses, timeout=30)
        msg = self.responses.pop(i)
        if "error" in msg:
            raise RuntimeError(msg["error"]["message"])
        return msg["result"]

    def notify(self, method, params):
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def diagnostics(self, uri, timeout=30):
        while True:
            n = self.notes.get(timeout=timeout)
            if n.get("method") == "textDocument/publishDiagnostics" and n["params"]["uri"] == uri:
                return n["params"]["diagnostics"]

    def close(self):
        try:
            self.request("shutdown", None)
            self.notify("exit", None)
            self.proc.wait(timeout=10)
        finally:
            self.proc.kill()
            for f in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                f.close()


def uri(path):
    return "file://" + path


def pos(text, needle, nth=0, delta=0):
    """LSP position of the nth occurrence of needle in text, plus delta characters."""
    i = -1
    for _ in range(nth + 1):
        i = text.index(needle, i + 1)
    i += delta
    line = text.count("\n", 0, i)
    return {"line": line, "character": i - (text.rfind("\n", 0, i) + 1)}


class LanguageServer(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.shapes = os.path.join(self.dir, "shapes.klar")
        self.main = os.path.join(self.dir, "main.klar")
        with open(self.shapes, "w") as f:
            f.write(SHAPES)
        with open(self.main, "w") as f:
            f.write(MAIN)
        self.c = Client(self.dir)
        self.addCleanup(self.c.close)
        init = self.c.request("initialize", {"processId": None, "rootUri": uri(self.dir), "capabilities": {}})
        self.assertTrue(init["capabilities"]["hoverProvider"])
        self.c.notify("initialized", {})

    def open(self, path, text, version=1):
        self.c.notify("textDocument/didOpen", {"textDocument": {"uri": uri(path), "languageId": "klar",
                                                                "version": version, "text": text}})
        return self.c.diagnostics(uri(path))

    def change(self, path, text, version):
        self.c.notify("textDocument/didChange", {"textDocument": {"uri": uri(path), "version": version},
                                                 "contentChanges": [{"text": text}]})
        return self.c.diagnostics(uri(path))

    def at(self, method, path, position, **extra):
        return self.c.request(method, {"textDocument": {"uri": uri(path)}, "position": position, **extra})

    def complete(self, path, position):
        r = self.at("textDocument/completion", path, position)
        return r["items"] if isinstance(r, dict) else r

    def test_diagnostics_follow_edits(self):
        self.assertEqual(self.open(self.main, MAIN), [])
        bad = MAIN.replace("area(s)", "aera(s)")
        [d] = self.change(self.main, bad, 2)
        self.assertEqual(d["code"], "E201")
        self.assertEqual(d["range"], {"start": pos(bad, "aera"), "end": pos(bad, "aera", delta=4)})
        self.assertIn("did you mean `area`?", d["message"])
        # the hint becomes a quick fix
        [fix] = self.c.request("textDocument/codeAction", {"textDocument": {"uri": uri(self.main)}, "range": d["range"],
                                                           "context": {"diagnostics": [d]}})
        self.assertEqual(fix["edit"]["changes"][uri(self.main)][0]["newText"], "area")
        self.assertEqual(self.change(self.main, MAIN, 3), [])

    def test_hover_definition_and_references(self):
        self.open(self.main, MAIN)
        h = self.at("textDocument/hover", self.main, pos(MAIN, "area(s)"))["contents"]["value"]
        self.assertIn("fn area(s: Shape) -> float", h)
        self.assertIn("The area of a shape.", h)            # doc comment from the other file
        self.assertIn("from shapes.klar", h)
        h = self.at("textDocument/hover", self.main, pos(MAIN, "a}"))["contents"]["value"]
        self.assertIn("let a: float", h)
        h = self.at("textDocument/hover", self.main, pos(MAIN, "p.x", delta=2))["contents"]["value"]
        self.assertIn("x: float", h)
        self.assertIn("field of struct Point", h)
        h = self.at("textDocument/hover", self.main, pos(MAIN, "print"))["contents"]["value"]
        self.assertIn("fn print[T](value: T) uses io", h)
        # go to definition, across files and to locals
        d = self.at("textDocument/definition", self.main, pos(MAIN, "area(s)"))
        self.assertEqual(d["uri"], uri(self.shapes))
        self.assertEqual(d["range"]["start"], pos(SHAPES, "area(s"))
        d = self.at("textDocument/definition", self.main, pos(MAIN, "Circle(p"))
        self.assertEqual(d["range"]["start"], pos(SHAPES, "Circle("))
        d = self.at("textDocument/definition", self.main, pos(MAIN, "p, 2.0"))
        self.assertEqual(d, {"uri": uri(self.main), "range": {"start": pos(MAIN, "p ="), "end": pos(MAIN, "p =", delta=1)}})
        d = self.at("textDocument/definition", self.main, pos(MAIN, "p.x", delta=2))
        self.assertEqual((d["uri"], d["range"]["start"]), (uri(self.shapes), pos(SHAPES, "x:")))
        # references to `p`: the declaration and two uses
        refs = self.at("textDocument/references", self.main, pos(MAIN, "p ="), context={"includeDeclaration": True})
        self.assertEqual(len(refs), 3)
        # references to `Point` span both files
        refs = self.at("textDocument/references", self.main, pos(MAIN, "Point("), context={"includeDeclaration": True})
        self.assertEqual({r["uri"] for r in refs}, {uri(self.main), uri(self.shapes)})

    def test_rename_across_files(self):
        self.open(self.main, MAIN)
        edit = self.at("textDocument/rename", self.main, pos(MAIN, "area(s)"), newName="surface")
        self.assertEqual(len(edit["changes"][uri(self.main)]), 2)     # the import and the call
        self.assertEqual(len(edit["changes"][uri(self.shapes)]), 1)
        with self.assertRaises(RuntimeError):
            self.at("textDocument/rename", self.main, pos(MAIN, "print"), newName="show")
        self.assertIsNone(self.at("textDocument/prepareRename", self.main, pos(MAIN, "print")))

    def test_completion(self):
        text = MAIN.replace("  print(p.x)\n", "  print(p.)\n  let q = de\n")
        self.open(self.main, MAIN)
        self.change(self.main, text, 2)
        labels = [i["label"] for i in self.complete(self.main, pos(text, "p.)", delta=2))]
        self.assertEqual(labels[:2], ["x", "y"])
        labels = [i["label"] for i in self.complete(self.main, pos(text, "de\n", delta=2))]
        self.assertIn("describe", labels)
        self.assertIn("p", labels)
        self.assertIn("area", labels)
        self.assertNotIn("json_parse", labels)                       # not imported
        u = "use std.\n"
        self.change(self.main, u + MAIN, 3)
        labels = [i["label"] for i in self.complete(self.main, pos(u, "\n"))]
        self.assertIn("json", labels)
        self.assertIn("task", labels)
        u = "use std.json { Json, js\n"
        self.change(self.main, u + MAIN, 4)
        labels = [i["label"] for i in self.complete(self.main, pos(u, "\n"))]
        self.assertIn("json_parse", labels)

    def test_signature_help(self):
        text = MAIN.replace("print(describe(Circle(p, 2.0)))", "print(describe(Circle(p, ")
        self.open(self.main, MAIN)
        self.change(self.main, text, 2)
        sig = self.at("textDocument/signatureHelp", self.main, pos(text, "Circle(p, ", delta=10))
        self.assertEqual(sig["signatures"][0]["label"], "Circle(center: Point, radius: float)")
        self.assertEqual(sig["activeParameter"], 1)

    def test_symbols_and_formatting(self):
        self.open(self.shapes, SHAPES)
        syms = self.c.request("textDocument/documentSymbol", {"textDocument": {"uri": uri(self.shapes)}})
        self.assertEqual([s["name"] for s in syms], ["Point", "Shape", "area"])
        self.assertEqual([s["name"] for s in syms[1]["children"]], ["Circle", "Square"])
        messy = "fn  f( x:int )->int{\n  return x+1\n}\n"
        self.change(self.shapes, messy, 2)
        [edit] = self.c.request("textDocument/formatting", {"textDocument": {"uri": uri(self.shapes)}, "options": {}})
        self.assertEqual(edit["newText"], "fn f(x: int) -> int {\n  return x + 1\n}\n")

    def test_missing_import_and_unsaved_modules(self):
        text = MAIN.replace("use shapes { Point, Shape, Circle, area }", "use shapes { Point, Shape, Circle }")
        [d] = self.open(self.main, text)
        self.assertEqual(d["code"], "E201")
        [fix] = self.c.request("textDocument/codeAction", {"textDocument": {"uri": uri(self.main)}, "range": d["range"],
                                                           "context": {"diagnostics": [d]}})
        self.assertEqual(fix["edit"]["changes"][uri(self.main)][0]["newText"], "use shapes { Point, Shape, Circle, area }")
        # an unsaved edit in shapes.klar is seen by main.klar
        self.open(self.shapes, SHAPES.replace("fn area(", "fn area2("))
        self.c.notify("textDocument/didOpen", {"textDocument": {"uri": uri(self.main), "languageId": "klar",
                                                                "version": 5, "text": MAIN}})
        diags = self.c.diagnostics(uri(self.main))
        while not diags:
            diags = self.c.diagnostics(uri(self.main))
        self.assertIn("area", diags[0]["message"])

    def test_errors_in_closed_modules_show_on_the_import(self):
        with open(self.shapes, "w") as f:
            f.write(SHAPES.replace("side * side", "side * \"x\""))
        [d] = self.open(self.main, MAIN)
        self.assertIn("in shapes.klar:", d["message"])
        self.assertEqual(d["range"]["start"]["line"], 0)

    def fixes_for(self, path, text, diag):
        return self.c.request("textDocument/codeAction", {"textDocument": {"uri": uri(path)}, "range": diag["range"],
                                                          "context": {"diagnostics": [diag]}})

    def test_quick_fixes_for_common_mistakes(self):
        text = (MAIN.replace("  print(p.x)\n", "  let n = 1\n  n = 2\n  let j = json_parse(\"1\")\n")
                .replace("fn describe(s: Shape) -> str {\n  let a = area(s)\n  return \"area {a}\"\n}",
                         "fn describe(s: Shape) -> str {\n  return match s {\n    Circle(c, r) => \"circle\"\n  }\n}"))
        diags = self.open(self.main, text)
        by_code = {d["code"]: d for d in diags}
        [fix] = self.fixes_for(self.main, text, by_code["E306"])
        self.assertEqual(fix["title"], "Add the missing arm")
        new = [e["newText"] for e in fix["edit"]["changes"][uri(self.main)]]
        self.assertEqual(new, [",", '    Square(_, _) => panic("not handled yet"),\n'])
        [fix] = self.fixes_for(self.main, text, by_code["E203"])
        self.assertEqual(fix["title"], "Declare `n` with `var`")
        self.assertEqual(fix["edit"]["changes"][uri(self.main)][0]["range"]["start"], pos(text, "let n"))

    def test_missing_std_import_is_a_quick_fix_and_a_completion(self):
        text = MAIN.replace("  print(p.x)\n", "  let j = json_parse(\"1\")\n")
        [d] = self.open(self.main, text)
        [fix] = self.fixes_for(self.main, text, d)
        self.assertEqual(fix["title"], "Add `use std.json { json_parse }`")
        self.assertEqual(fix["edit"]["changes"][uri(self.main)][0]["newText"], "use std.json { json_parse }\n")
        typed = MAIN.replace("  print(p.x)\n", "  let j = json_pa\n")
        self.change(self.main, typed, 2)
        [item] = [i for i in self.complete(self.main, pos(typed, "json_pa", delta=7)) if i["label"] == "json_parse"]
        self.assertEqual(item["additionalTextEdits"][0]["newText"], "use std.json { json_parse }\n")

    def test_completion_knows_the_expected_type(self):
        app = ('use std.html { Html, Attr, button, text }\nuse std.kit { variant, Outline }\n\nenum Msg { Go }\n\n'
               'fn view() -> Html[Msg] {\n  return button([variant(Outline), ], [text("x")])\n}\n')
        path = os.path.join(self.dir, "app.klar")
        self.open(path, app)
        items = self.complete(path, pos(app, "), ]", delta=3))
        size = [i for i in items if i["label"] == "size"]
        self.assertTrue(size and size[0]["sortText"].startswith("0"))           # std.kit option, not imported yet
        self.assertEqual(size[0]["additionalTextEdits"][0]["newText"], "use std.kit { variant, Outline, size }")
        typed = app.replace("variant(Outline)", "variant()")
        self.change(path, typed, 2)
        first = sorted(self.complete(path, pos(typed, "variant()", delta=8)), key=lambda i: i["sortText"])[:6]
        self.assertEqual({i["label"] for i in first}, {"Primary", "Secondary", "Outline", "Ghost", "Destructive", "Link"})

    def test_match_arm_completion(self):
        text = MAIN.replace("fn describe(s: Shape) -> str {\n  let a = area(s)\n  return \"area {a}\"\n}",
                            "fn describe(s: Shape) -> str {\n  return match s {\n    Circle(c, r) => \"c\",\n    \n  }\n}")
        self.open(self.main, text)
        items = sorted(self.complete(self.main, pos(text, "    \n", delta=4)), key=lambda i: i["sortText"])
        self.assertEqual([i["insertText"] for i in items[:2]], ["Square(corner, side) => ", "Circle(center, radius) => "])

    def test_hovers_for_tuples_and_externs(self):
        text = ('extern "js:marked.parse" fn md(text: str) -> str\n\nfn pair() -> (int, str) {\n  return (1, "a")\n}\n\n'
                'fn main() uses io {\n  let t = pair()\n  print(t.1)\n  print(md("x"))\n}\n')
        self.open(self.main, text)
        h = self.at("textDocument/hover", self.main, pos(text, "t.1", delta=2))["contents"]["value"]
        self.assertIn("1: str", h)
        self.assertIn("element 1 of a tuple", h)
        h = self.at("textDocument/hover", self.main, pos(text, "md(\"x"))["contents"]["value"]
        self.assertIn('extern "js:marked.parse" fn md(text: str) -> str', h)
        self.assertIn("JavaScript function (npm package)", h)
        hints = self.c.request("textDocument/inlayHint", {"textDocument": {"uri": uri(self.main)},
                                                          "range": {"start": pos(text, "fn main"), "end": pos(text, "}\n", nth=1)}})
        self.assertEqual([h["label"] for h in hints], [": (int, str)"])
        lenses = self.c.request("textDocument/codeLens", {"textDocument": {"uri": uri(self.main)}})
        self.assertEqual([x["command"]["title"] for x in lenses], ["Run", "Debug"])
        syms = self.c.request("workspace/symbol", {"query": "area"})
        self.assertIn("area", [s["name"] for s in syms])


class CheckForAgents(unittest.TestCase):
    """The same information without an editor: `klar check --json` fixes, `--fix` and `--at`."""

    def run_klar(self, *args, cwd):
        return subprocess.run([sys.executable, "-m", "klar", *args], cwd=cwd, capture_output=True, text=True,
                              env=dict(os.environ, PYTHONPATH=ROOT))

    def test_json_fixes_fix_and_at(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        with open(os.path.join(d, "shapes.klar"), "w") as f:
            f.write(SHAPES)
        bad = MAIN.replace("area(s)", "aera(s)").replace("  print(p.x)\n", "  print(p.x)\n  let ok = True\n")
        with open(os.path.join(d, "main.klar"), "w") as f:
            f.write(bad)
        r = self.run_klar("check", "main.klar", "--json", cwd=d)
        errs = json.loads(r.stdout)["errors"]
        self.assertEqual(errs[0]["fixes"][0]["edits"][0]["text"], "area")
        self.assertEqual(errs[0]["fixes"][0]["edits"][0]["col"], bad.splitlines()[3].index("aera") + 1)
        r = self.run_klar("check", "--fix", "main.klar", cwd=d)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(os.path.join(d, "main.klar")) as f:
            self.assertEqual(f.read(), MAIN.replace("  print(p.x)\n", "  print(p.x)\n  let ok = true\n"))
        r = self.run_klar("check", "main.klar", "--at", "4:12", "--json", cwd=d)
        info = json.loads(r.stdout)
        self.assertEqual(info["signature"], "fn area(s: Shape) -> float")
        self.assertEqual(info["doc"], "The area of a shape.")
        self.assertEqual((os.path.basename(info["definition"]["file"]), info["definition"]["line"]), ("shapes.klar", 10))


if __name__ == "__main__":
    unittest.main()
