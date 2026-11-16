"""Tasks and channels for the interpreter (std.task).

Every task is a Python thread, but only the thread holding the runtime's lock (the baton) runs Klar code; a
task gives the baton up while it waits for a channel, for other tasks, or inside an io extern (a file, a
request, a sleep). So tasks overlap their io, and Klar code never runs in two threads at once, exactly like
the native runtime (klar_std.h) and the JavaScript one. If every task is waiting on another, the program
stops with E409 instead of hanging.
"""

import collections
import threading

from .errors import KlarError

STACK_SIZE = 256 * 1024 * 1024
RUNTIME = None      # the running interpreter's Runtime (std.http's server takes the baton from here)


class Runtime:
    def __init__(self):
        self.cond = threading.Condition(threading.RLock())
        self.live = 1              # tasks that have not finished, including main
        self.waiting = []          # the conditions that blocked tasks are waiting for
        self.failed = None         # the first runtime error in any task
        self.chans = {}
        self.groups = {}
        self.ids = 0

    def new_id(self):
        self.ids += 1
        return self.ids

    # the baton -------------------------------------------------------------------------------------------
    def released(self):
        """Context manager: give the baton up around blocking io (only if this thread holds it)."""
        rt = self

        class Released:
            def __enter__(self):
                self.state = rt.cond._release_save() if rt.cond._is_owned() else None

            def __exit__(self, *exc):
                if self.state is not None:
                    rt.cond._acquire_restore(self.state)
        return Released()

    def wait_until(self, pred, interp, node, what):
        while not pred():
            if self.failed is not None:
                raise self.failed
            # a woken task still counts as waiting until it runs, so only call it a deadlock when no waiter can go on
            if len(self.waiting) + 1 >= self.live and not any(p() for p in self.waiting):
                interp.rt_err("E409", f"deadlock: every task is waiting ({what})", node,
                              "a value that is never sent, a channel that is never closed, or tasks waiting on each other")
            self.waiting.append(pred)
            try:
                self.cond.wait()
            finally:
                self.waiting.remove(pred)
        if self.failed is not None:
            raise self.failed

    def start(self, interp, fn, args, node, on_done):
        """Start a task running fn(*args); on_done(result) runs with the baton held."""
        self.live += 1
        parent_stack = list(interp.stack[-5:])

        def run():
            with self.cond:
                interp.stack = parent_stack + [("task", node.line if node else None)]
                interp.tenv = {}
                result = None
                try:
                    result = interp.call_value(fn, args, node)
                except KlarError as e:
                    if self.failed is None:
                        self.failed = e
                except RecursionError:
                    if self.failed is None:
                        self.failed = KlarError("E406", "stack overflow in a task (recursion too deep)", node.line,
                                                node.col, None, interp.file)
                finally:
                    self.live -= 1
                    on_done(result)
                    self.cond.notify_all()
        old = threading.stack_size()
        threading.stack_size(STACK_SIZE)
        try:
            threading.Thread(target=run, daemon=True).start()
        finally:
            threading.stack_size(old)

    # std.task --------------------------------------------------------------------------------------------
    def task_all(self, interp, jobs, node):
        results = [None] * len(jobs)
        done = [0]

        def finish(i):
            def f(r):
                results[i] = r
                done[0] += 1
            return f
        for i, job in enumerate(jobs):
            self.start(interp, job, [], node, finish(i))
        self.wait_until(lambda: done[0] == len(jobs), interp, node, "task_all")
        return tuple(results)

    def task_group(self, interp, body, node, make_group):
        gid = self.new_id()
        self.groups[gid] = {"active": 0, "open": True}
        try:
            interp.call_value(body, [make_group(gid)], node)
            g = self.groups[gid]
            self.wait_until(lambda: g["active"] == 0, interp, node, "task_group")
        finally:
            self.groups[gid]["open"] = False

    def task_spawn(self, interp, gid, job, node):
        g = self.groups.get(gid)
        if g is None or not g["open"]:
            interp.rt_err("E407", "task_spawn on a group that has already finished", node,
                          "spawn tasks inside the task_group body (or inside tasks of that group)")
        g["active"] += 1

        def done(_):
            g["active"] -= 1
        self.start(interp, job, [], node, done)

    def chan_new(self):
        cid = self.new_id()
        self.chans[cid] = {"q": collections.deque(), "closed": False}
        return cid

    def chan(self, interp, cid, node):
        c = self.chans.get(cid)
        if c is None:
            interp.rt_err("E407", "not a channel made by chan_new", node)
        return c

    def chan_send(self, interp, cid, value, node):
        c = self.chan(interp, cid, node)
        if c["closed"]:
            interp.rt_err("E407", "chan_send on a closed channel", node, "close a channel only when nothing will send on it again")
        c["q"].append(value)
        self.cond.notify_all()

    def chan_recv(self, interp, cid, node):
        c = self.chan(interp, cid, node)
        self.wait_until(lambda: c["q"] or c["closed"], interp, node, "chan_recv")
        return (True, c["q"].popleft()) if c["q"] else (False, None)

    def chan_close(self, interp, cid, node):
        self.chan(interp, cid, node)["closed"] = True
        self.cond.notify_all()
