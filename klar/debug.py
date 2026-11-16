"""`klar debug`: breakpoints, stepping and variables for programs run by the interpreter.

Two front ends share one engine:

    klar debug --dap                        the Debug Adapter Protocol over stdin/stdout, for editors
    klar debug main.klar --break 12         run, and at each hit of a breakpoint print where the program is,
    klar debug main.klar --break lib.klar:8 --json    the call stack and every variable (as JSON lines)

With no --break, `klar debug FILE` runs the program and, if it stops with an error, prints the variables of
every Klar frame at that moment: what an agent would otherwise add print() calls for.

The interpreter compiles each Klar function to a Python function. The engine follows the generated code with
sys.settrace, so programs run at full speed when they are not being debugged: a statement starts on a new
generated line, and the interpreter's line map says which Klar file and line that is.
"""

import json
import os
import sys
import threading

from .errors import KlarError

MAX_HITS = 20          # per breakpoint location, in the command-line mode
MAX_CHILDREN = 500     # list items or map entries shown in one expansion


# ------------------------------------------------------------------ values
def plain(v):
    """A value as Klar code sees it: owned buffers (in-place lists, maps and strings) turned into plain values."""
    from . import interp
    if isinstance(v, getattr(interp, "OwnedStr", ())):
        return "".join(v)
    if isinstance(v, getattr(interp, "OwnedMap", ())):
        return interp.FrozenMap(v)
    if type(v) is list:
        return tuple(v)
    return v


def show(v):
    from . import interp
    try:
        return interp.fmt(plain(v), True)
    except Exception:
        return repr(v)


def children(v):
    """(name, value) pairs inside a value, or [] for a scalar."""
    from . import interp
    v = plain(v)
    if isinstance(v, interp.StructVal):
        return list(v.fields)
    if isinstance(v, interp.EnumVal):
        names = v.names or tuple(str(i) for i in range(len(v.values)))
        return list(zip(names, v.values))
    if isinstance(v, tuple):
        return [(str(i), x) for i, x in enumerate(v[:MAX_CHILDREN])]
    if isinstance(v, dict):
        return [(show(k), x) for k, x in list(v.items())[:MAX_CHILDREN]]
    return []


def type_name(v):
    from . import interp
    v = plain(v)
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, interp.StructVal):
        return v.name if not v.name.startswith("Tuple") else "tuple"
    if isinstance(v, interp.EnumVal):
        return v.variant
    if isinstance(v, tuple):
        return f"list ({len(v)})"
    if isinstance(v, dict):
        return f"map ({len(v)})"
    return ""


# ------------------------------------------------------------------ the engine
class Frame:
    __slots__ = ("id", "py", "fn", "file", "line", "col")

    def __init__(self, fid, py, fn, file, line):
        self.id, self.py, self.fn, self.file, self.line, self.col = fid, py, fn, file, line, 1

    def locals(self):
        out = []
        for k, v in self.py.f_locals.items():
            if k.startswith("v_") and not k.startswith("v__"):
                out.append((k[2:], v))
        return out


class Engine:
    """Runs one program under sys.settrace and stops where asked. `on_stop(reason, frames, error)` is called on
    the program's thread; it returns when the program may go on (after resume() from another thread, or
    straight away in the command-line mode)."""

    def __init__(self, path, argv=(), stdout=None, on_stop=None, on_exit=None):
        self.path = os.path.abspath(path)
        self.argv = list(argv)
        self.stdout = stdout
        self.on_stop = on_stop
        self.on_exit = on_exit
        self.breakpoints = {}            # abs file -> set of lines
        self.mode = "run"                # run | step_in | step_over | step_out | pause
        self.depth0 = 0
        self.last = {}                   # id(python frame) -> last Klar (file, line)
        self.paused = threading.Event()
        self.go = threading.Condition()
        self.resumed = False
        self.stopping = False
        self.interp = None
        self.gen_file = None
        self.frames = []

    # --- the generated code
    def klar_pos(self, f):
        """(file, line) of the Klar statement a generated frame is on."""
        return self.klar_pos_at(f, f.f_lineno)

    def klar_pos_at(self, f, lineno):
        lm = getattr(self.interp, "line_map", None)
        i = (lineno or 0) - 1
        if lm is None or not (0 <= i < len(lm)):
            return None
        p = lm[i]
        if p is None:
            return None
        if isinstance(p, tuple):
            file, line = p[0], p[1]
        else:
            fm = getattr(self.interp, "file_map", None)
            file, line = (fm[i] if fm is not None and i < len(fm) else None), p
        if not line:
            return None
        return os.path.abspath(file) if file else self.path, line

    def is_gen(self, f):
        return f.f_code.co_filename == self.gen_file

    def fn_name(self, f):
        named = getattr(self.interp, "named_code", {}) or {}
        return named.get(f.f_code) or "fn"

    def stack(self, f):
        out = []
        while f is not None:
            if self.is_gen(f):
                p = self.klar_pos(f) or self.last.get(id(f))
                if p is not None:
                    out.append(Frame(len(out), f, self.fn_name(f), p[0], p[1]))
            f = f.f_back
        return out

    # --- tracing
    def trace(self, frame, event, arg):
        if self.stopping:
            raise SystemExit
        if not self.is_gen(frame):
            return None
        return self.local

    def local(self, frame, event, arg):
        if self.stopping:
            raise SystemExit
        if event == "line":
            p = self.klar_pos(frame)
            if p is None or self.last.get(id(frame)) == p:
                return self.local
            self.last[id(frame)] = p
            reason = None
            if p[1] in self.breakpoints.get(p[0], ()):
                reason = "breakpoint"
            elif self.mode == "step_in" or self.mode == "pause":
                reason = "step" if self.mode == "step_in" else "pause"
            elif self.mode in ("step_over", "step_out"):
                d = self.depth(frame)
                if (self.mode == "step_over" and d <= self.depth0) or (self.mode == "step_out" and d < self.depth0):
                    reason = "step"
            if reason:
                self.stop(reason, frame, None)
        elif event == "return":
            self.last.pop(id(frame), None)
        return self.local

    def depth(self, frame):
        n = 0
        while frame is not None:
            if self.is_gen(frame):
                n += 1
            frame = frame.f_back
        return n

    def stop(self, reason, frame, error):
        self.frames = self.stack(frame)
        self.mode = "run"
        if self.on_stop is None:
            return
        with self.go:
            self.resumed = False
        self.paused.set()
        self.on_stop(reason, self.frames, error)
        with self.go:
            while not self.resumed and not self.stopping:
                self.go.wait()
        self.paused.clear()
        if self.stopping:
            raise SystemExit

    def resume(self, mode="run", frame=None):
        """Let a stopped program go on: run, or step (step_in, step_over, step_out) from `frame`."""
        self.mode = mode
        self.depth0 = self.depth(frame.py) if frame is not None else (self.depth(self.frames[0].py) if self.frames else 0)
        self.frames = []
        with self.go:
            self.resumed = True
            self.go.notify_all()

    def pause(self):
        self.mode = "pause"

    def terminate(self):
        self.stopping = True
        with self.go:
            self.go.notify_all()

    # --- running
    def load(self):
        """Check the program. Returns a list of KlarErrors (empty when it can run)."""
        from .checker import check_source
        from .cli import check_main_signature
        with open(self.path, encoding="utf-8") as f:
            src = f.read()
        self.src = src
        prog, errors = check_source(src, self.path)
        if not errors:
            e = check_main_signature(prog, self.path)
            errors = [e] if e else []
        self.prog = prog
        return errors

    def run(self):
        """Run main() under the tracer. Returns (exit code, the KlarError that stopped it or None)."""
        from .interp import Interpreter
        self.interp = Interpreter(self.prog, file=self.path, argv=self.argv, stdout=self.stdout)
        self.gen_file = getattr(self.interp, "gen_file", None)
        code, error = 0, None
        threading.settrace(self.trace)
        sys.settrace(self.trace)
        try:
            self.interp.run_main()
        except KlarError as e:
            code, error = 1, e
            # stop after the fact, in the innermost Klar frame the error left: its variables are all still there
            tb, inner = e.__traceback__, None
            while tb is not None:
                if self.is_gen(tb.tb_frame):
                    inner = tb.tb_frame
                    self.last[id(inner)] = self.klar_pos_at(inner, tb.tb_lineno) or self.last.get(id(inner))
                tb = tb.tb_next
            sys.settrace(None)
            if inner is not None and not self.stopping:
                try:
                    self.stop("exception", inner, e)
                except SystemExit:
                    pass
        except SystemExit:
            code = 130
        finally:
            sys.settrace(None)
            threading.settrace(None)
        if self.on_exit:
            self.on_exit(code, error)
        return code, error


def supported():
    """Whether this interpreter can be debugged (it compiles Klar functions to Python with a line map)."""
    import importlib.util
    return importlib.util.find_spec("klar.pygen") is not None


# ------------------------------------------------------------------ command line: klar debug FILE [--break ...]
def parse_breaks(specs, entry):
    """`12`, `lib.klar:8` or `src/lib.klar:8,9` -> {abs file: {lines}}."""
    out = {}
    base = os.path.dirname(os.path.abspath(entry))
    for spec in specs:
        for part in spec.split(","):
            part = part.strip()
            if not part:
                continue
            file, _, line = part.rpartition(":")
            if not line.isdigit():
                raise ValueError(f"`{part}` is not LINE or FILE:LINE")
            path = os.path.abspath(entry) if not file else (
                os.path.abspath(file) if os.path.exists(file) else os.path.join(base, file))
            out.setdefault(os.path.abspath(path), set()).add(int(line))
    return out


def describe(frames, error=None, rel=None):
    rel = rel or (lambda p: p)
    out = {"stack": [{"fn": f.fn, "file": rel(f.file), "line": f.line} for f in frames], "frames": []}
    for f in frames:
        out["frames"].append({"fn": f.fn, "at": f"{rel(f.file)}:{f.line}",
                              "variables": {n: show(v) for n, v in f.locals()}})
    if error is not None:
        out["error"] = error.to_dict()
    return out


def cmd_debug(path, breaks, as_json, argv):
    if not supported():
        print("klar debug: this version of the interpreter cannot be debugged yet", file=sys.stderr)
        return 2
    base = os.getcwd()

    def rel(p):
        r = os.path.relpath(p, base)
        return p if r.startswith("..") else r

    hits = {}

    def on_stop(reason, frames, error):
        if not frames:
            return
        top = frames[0]
        key = (top.file, top.line)
        if reason == "breakpoint":
            hits[key] = hits.get(key, 0) + 1
            if hits[key] > MAX_HITS:
                if hits[key] == MAX_HITS + 1:
                    print(f"[break] {rel(top.file)}:{top.line}: hit {MAX_HITS} times; not shown again",
                          file=sys.stderr)
                return
        elif reason != "exception":
            return
        if as_json:
            d = describe(frames if reason == "exception" else frames[:1], error, rel)
            d = {"event": "break" if reason == "breakpoint" else "error", "at": f"{rel(top.file)}:{top.line}",
                 "hit": hits.get(key, 1), **d}
            sys.stderr.write(json.dumps(d) + "\n")
        else:
            what = "break" if reason == "breakpoint" else "error"
            lines = [f"[{what}] {rel(top.file)}:{top.line} in {top.fn}" +
                     (f" (hit {hits[key]})" if hits.get(key, 0) > 1 else "")]
            for i, f in enumerate(frames if reason == "exception" else frames[:1]):
                if i:
                    lines.append(f"  called from {f.fn} at {rel(f.file)}:{f.line}")
                for n, v in f.locals():
                    lines.append(f"    {n} = {show(v)}")
            if reason == "breakpoint" and len(frames) > 1:
                lines.append("  stack: " + " <- ".join(f"{f.fn} ({rel(f.file)}:{f.line})" for f in frames))
            sys.stderr.write("\n".join(lines) + "\n")
        sys.stderr.flush()

    eng = Engine(path, argv, on_stop=None)
    errors = eng.load()
    if errors:
        from .cli import report
        report(errors, eng.src, as_json)
        return 1
    try:
        eng.breakpoints = parse_breaks(breaks, path)
    except ValueError as ex:
        print(f"klar debug: {ex}", file=sys.stderr)
        return 2
    eng.on_stop = on_stop
    code, error = eng.run()
    sys.stdout.flush()
    if error is not None:
        sys.stderr.write(error.render(_source_of(error, eng)) + "\n")
    return code


def _source_of(error, eng):
    f = error.file or eng.path
    try:
        with open(f, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


# ------------------------------------------------------------------ the Debug Adapter Protocol
class Adapter:
    """`klar debug --dap`: one debug session over stdin/stdout."""

    def __init__(self, inp, out):
        self.inp, self.out = inp, out
        self.lock = threading.Lock()
        self.seq = 0
        self.engine = None
        self.pending_breaks = {}          # abs file -> list of requested lines
        self.refs = {}                    # variablesReference -> value or ("frame", Frame)
        self.stop_on_entry = False
        self.thread = None
        self.done = False

    # --- transport (the same framing as LSP)
    def send(self, msg):
        with self.lock:
            self.seq += 1
            msg["seq"] = self.seq
            body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
            self.out.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
            self.out.flush()

    def event(self, name, body=None):
        self.send({"type": "event", "event": name, "body": body or {}})

    def respond(self, req, body=None, error=None):
        msg = {"type": "response", "request_seq": req["seq"], "command": req["command"], "success": error is None}
        if error is not None:
            msg["message"] = error
            msg["body"] = {"error": {"id": 1, "format": error}}
        elif body is not None:
            msg["body"] = body
        self.send(msg)

    def read(self):
        headers = {}
        while True:
            line = self.inp.readline()
            if not line:
                return None
            line = line.decode("ascii", "replace").strip()
            if not line:
                break
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
        n = int(headers.get("content-length", 0))
        return json.loads(self.inp.read(n).decode("utf-8")) if n else {}

    def serve(self):
        while not self.done:
            msg = self.read()
            if msg is None:
                break
            if msg.get("type") != "request":
                continue
            try:
                getattr(self, "on_" + msg["command"], self.unknown)(msg, msg.get("arguments") or {})
            except Exception as e:      # never take the editor's session down
                self.respond(msg, error=f"{type(e).__name__}: {e}")
        if self.engine is not None:
            self.engine.terminate()
        return 0

    def unknown(self, req, args):
        self.respond(req, error=f"`{req['command']}` is not supported")

    # --- requests
    def on_initialize(self, req, args):
        self.lines_start_at1 = args.get("linesStartAt1", True)
        self.respond(req, {"supportsConfigurationDoneRequest": True, "supportsTerminateRequest": True,
                           "supportsEvaluateForHovers": True, "supportsSingleThreadExecutionRequests": False,
                           "exceptionBreakpointFilters": []})
        self.event("initialized")

    def line_in(self, n):
        return n if self.lines_start_at1 else n + 1

    def line_out(self, n):
        return n if self.lines_start_at1 else n - 1

    def on_launch(self, req, args):
        program = args.get("program")
        if not program:
            return self.respond(req, error="launch needs `program`: the .klar file to run")
        if args.get("cwd"):
            os.chdir(args["cwd"])
        if not supported():
            return self.respond(req, error="this version of the Klar interpreter cannot be debugged yet")
        adapter = self

        class Out:
            def write(self, s):
                if s:
                    adapter.event("output", {"category": "stdout", "output": s})
                return len(s)

            def flush(self):
                pass

        self.engine = Engine(program, args.get("args") or [], stdout=Out(), on_stop=self.stopped,
                             on_exit=self.exited)
        errors = self.engine.load()
        if errors:
            for e in errors:
                self.event("output", {"category": "stderr", "output": e.render(_source_of(e, self.engine)) + "\n"})
            self.respond(req, error=f"{program} has {len(errors)} error(s); see the Debug Console")
            self.event("terminated")
            return
        self.stop_on_entry = bool(args.get("stopOnEntry"))
        for f, lines in self.pending_breaks.items():
            self.engine.breakpoints[f] = set(lines)
        self.respond(req)

    def on_setBreakpoints(self, req, args):
        path = os.path.abspath(args.get("source", {}).get("path", ""))
        lines = [self.line_in(b["line"]) for b in args.get("breakpoints", [])]
        self.pending_breaks[path] = lines
        if self.engine is not None:
            self.engine.breakpoints[path] = set(lines)
        self.respond(req, {"breakpoints": [{"verified": True, "line": self.line_out(n)} for n in lines]})

    def on_setExceptionBreakpoints(self, req, args):
        self.respond(req, {"breakpoints": []})

    def on_configurationDone(self, req, args):
        self.respond(req)
        if self.engine is None or self.thread is not None:
            return
        if self.stop_on_entry:
            self.engine.mode = "step_in"
        self.thread = threading.Thread(target=self.engine.run, daemon=True)
        self.thread.start()

    def on_threads(self, req, args):
        self.respond(req, {"threads": [{"id": 1, "name": "main"}]})

    def on_stackTrace(self, req, args):
        frames = self.engine.frames if self.engine else []
        start, levels = args.get("startFrame", 0), args.get("levels") or len(frames)
        out = []
        for f in frames[start:start + levels]:
            out.append({"id": f.id + 1, "name": f.fn, "line": self.line_out(f.line), "column": 1,
                        "source": {"name": os.path.basename(f.file), "path": f.file}})
        self.respond(req, {"stackFrames": out, "totalFrames": len(frames)})

    def ref(self, value):
        n = len(self.refs) + 1
        self.refs[n] = value
        return n

    def on_scopes(self, req, args):
        frames = self.engine.frames if self.engine else []
        fid = args.get("frameId", 1) - 1
        if not (0 <= fid < len(frames)):
            return self.respond(req, {"scopes": []})
        r = self.ref(("frame", frames[fid]))
        self.respond(req, {"scopes": [{"name": "Locals", "variablesReference": r, "expensive": False}]})

    def variable(self, name, v):
        kids = children(v)
        return {"name": name, "value": show(v), "type": type_name(v),
                "variablesReference": self.ref(v) if kids else 0}

    def on_variables(self, req, args):
        target = self.refs.get(args.get("variablesReference"))
        if target is None:
            return self.respond(req, {"variables": []})
        if isinstance(target, tuple) and len(target) == 2 and target[0] == "frame" and isinstance(target[1], Frame):
            items = target[1].locals()
        else:
            items = children(target)
        self.respond(req, {"variables": [self.variable(n, v) for n, v in items]})

    def on_evaluate(self, req, args):
        """Names and paths of fields, elements and map keys: `card.title`, `cards.0.id`, `t.1`."""
        expr = (args.get("expression") or "").strip()
        frames = self.engine.frames if self.engine else []
        fid = (args.get("frameId") or 1) - 1
        if not (0 <= fid < len(frames)):
            return self.respond(req, error="the program is not stopped")
        parts = expr.split(".")
        scope = dict(frames[fid].locals())
        if parts[0] not in scope:
            return self.respond(req, error=f"no variable `{parts[0]}` here")
        v = scope[parts[0]]
        for p in parts[1:]:
            kids = dict(children(v))
            if p not in kids:
                return self.respond(req, error=f"`{p}` is not in {show(v)[:60]}")
            v = kids[p]
        var = self.variable(expr, v)
        self.respond(req, {"result": var["value"], "type": var["type"], "variablesReference": var["variablesReference"]})

    def resume(self, req, mode):
        frames = self.engine.frames if self.engine else []
        self.refs.clear()
        self.respond(req, {"allThreadsContinued": True} if mode == "run" else None)
        if self.engine:
            self.engine.resume(mode, frames[0] if frames else None)

    def on_continue(self, req, args):
        self.resume(req, "run")

    def on_next(self, req, args):
        self.resume(req, "step_over")

    def on_stepIn(self, req, args):
        self.resume(req, "step_in")

    def on_stepOut(self, req, args):
        self.resume(req, "step_out")

    def on_pause(self, req, args):
        if self.engine:
            self.engine.pause()
        self.respond(req)

    def on_terminate(self, req, args):
        if self.engine:
            self.engine.terminate()
        self.respond(req)

    def on_disconnect(self, req, args):
        if self.engine:
            self.engine.terminate()
        self.respond(req)
        self.done = True

    # --- engine callbacks (on the program's thread)
    def stopped(self, reason, frames, error):
        body = {"reason": reason, "threadId": 1, "allThreadsStopped": True}
        if error is not None:
            body["description"] = f"error[{error.code}]"
            body["text"] = error.message + (f"\nhint: {error.hint}" if error.hint else "")
        self.event("stopped", body)

    def exited(self, code, error):
        if error is not None:
            self.event("output", {"category": "stderr", "output": error.render(_source_of(error, self.engine)) + "\n"})
        self.event("exited", {"exitCode": code})
        self.event("terminated")


def cmd_dap():
    out = sys.stdout.buffer
    sys.stdout = sys.stderr          # stdout carries the protocol: stray prints from Python code go to stderr
    return Adapter(sys.stdin.buffer, out).serve()
