#!/usr/bin/env python3
"""klareval: does an AI agent build working programs with fewer mistakes in Klar than in TypeScript?

  klareval.py prepare --task ledger --lang klar --out WS     make a workspace for one attempt
  klareval.py grade   --task ledger --lang klar --workspace WS [--feedback] [--json OUT]
  klareval.py trial   --task ledger --lang klar --agent-cmd CMD [--model M] [--rounds 3] --out DIR
  klareval.py record  --task ledger --lang klar --trial DIR [--model M]   grade DIR/workspace as the next round
  klareval.py report  RESULTS_DIR...                          summary table in Markdown

`trial` drives any agent that can be started from a shell command. `prepare` + `record` are for agents driven
some other way (an IDE agent, a chat session, a person): prepare DIR/workspace, let the agent work, then
`record` grades it, writes FEEDBACK.md for the next round and keeps DIR/trial.json up to date.

A trial prepares a workspace, runs the agent, grades the result with the hidden tests, and while tests fail
(up to --rounds rounds in total) gives the agent the same kind of feedback in every language: the names of
the failing tests and a one-line reason each. See eval/README.md for the method.
"""

import argparse
import collections
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import traceback

HARNESS = os.path.dirname(os.path.abspath(__file__))
EVAL = os.path.dirname(HARNESS)
sys.path.insert(0, HARNESS)

from checks import Fail, Program  # noqa: E402
from langs import LANGS  # noqa: E402

TASKS = os.path.join(EVAL, "tasks")

FIRST_PROMPT = """You are being evaluated on writing a working program.

Read TASK.md in this folder and write the program it describes. The section "Language and tools" at the end
of TASK.md says which language and file to use and how to check and run your work. Work only inside this
folder: do not read or write files outside it. Test your program yourself before you finish; hidden tests
will be run against it afterwards. When you are done, stop and say DONE.
"""

FIX_PROMPT = """Your program was graded with the hidden tests and some failed. FEEDBACK.md in this folder lists
them. Fix the program (same files, same rules as in TASK.md), test it again, and say DONE when finished.
"""


def task_names():
    return sorted(d for d in os.listdir(TASKS) if os.path.isfile(os.path.join(TASKS, d, "TASK.md")))


def load_tests(task):
    path = os.path.join(TASKS, task, "hidden", "tests.py")
    spec = importlib.util.spec_from_file_location(f"hidden_{task}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return [(n, getattr(mod, n)) for n in dir(mod) if n.startswith("test_") and callable(getattr(mod, n))], mod


def prepare(task, lang, ws, docs="repo"):
    if os.path.exists(ws) and os.listdir(ws):
        raise SystemExit(f"{ws} exists and is not empty")
    os.makedirs(ws, exist_ok=True)
    _, mod = load_tests(task)
    web = getattr(mod, "KIND", "cli") == "web"
    with open(os.path.join(TASKS, task, "TASK.md")) as f:
        text = f.read().rstrip() + "\n" + (LANGS[lang].web_footer if web else LANGS[lang].footer)
    with open(os.path.join(ws, "TASK.md"), "w") as f:
        f.write(text)
    with open(os.path.join(ws, "PROMPT.md"), "w") as f:
        f.write(FIRST_PROMPT)
    LANGS[lang].prepare(ws, docs)
    if web and lang == "ts":
        with open(os.path.join(ws, "index.html"), "w") as f:
            f.write(LANGS[lang].INDEX)
    # tasks that change an existing program ship it in starter/LANG (code) and starter/shared (data)
    for part in (lang, "shared"):
        src = os.path.join(TASKS, task, "starter", part)
        if os.path.isdir(src):
            shutil.copytree(src, ws, dirs_exist_ok=True)
    os.makedirs(os.path.join(ws, ".eval"), exist_ok=True)


def grade(task, lang, ws):
    language = LANGS[lang]
    started = time.time()
    tests, mod = load_tests(task)
    result = {"task": task, "lang": lang, "build_ok": False, "build_log": "", "tests": [],
              "passed": 0, "total": len(tests)}
    if not os.path.isfile(os.path.join(ws, language.entry)):
        result["build_log"] = f"{language.entry} is missing"
        return finish(result, started)
    web = getattr(mod, "KIND", "cli") == "web"
    ok, log = language.build_web(ws) if web else language.build(ws)
    result["build_ok"], result["build_log"] = ok, log[-6000:]
    if not ok:
        result["tests"] = [{"name": n, "ok": False, "message": "build failed"} for n, _ in tests]
        return finish(result, started)
    timeout = getattr(mod, "TIMEOUT", 60)
    prog = Program(language.command(ws), ws, timeout=timeout, page=language.web_page if web else None)
    for name, fn in tests:
        t0 = time.time()
        try:
            fn(prog)
            entry = {"name": name, "ok": True}
        except (Fail, AssertionError) as e:
            entry = {"name": name, "ok": False, "message": str(e) or e.__class__.__name__}
        except Exception as e:  # a harness-side error caused by odd program output
            entry = {"name": name, "ok": False,
                     "message": f"{e.__class__.__name__}: {e}", "trace": traceback.format_exc()[-1500:]}
        entry["seconds"] = round(time.time() - t0, 2)
        result["tests"].append(entry)
    result["passed"] = sum(1 for t in result["tests"] if t["ok"])
    return finish(result, started)


def finish(result, started):
    result["seconds"] = round(time.time() - started, 2)
    result["all_pass"] = result["build_ok"] and result["passed"] == result["total"]
    return result


def feedback_text(result):
    """The feedback an agent gets between rounds: identical in shape for every language."""
    lines = ["# Hidden test results", ""]
    if not result["build_ok"]:
        lines += ["The build failed, so no test could run. Build output:", "", "```",
                  result["build_log"][-3000:].strip(), "```"]
        return "\n".join(lines) + "\n"
    lines.append(f"{result['passed']} of {result['total']} tests passed. Failing tests:")
    lines.append("")
    for t in result["tests"]:
        if not t["ok"]:
            name = t["name"][len("test_"):].replace("_", " ")
            lines.append(f"* {name}: {t['message'][:500]}")
    return "\n".join(lines) + "\n"


def tool_stats(ws):
    path = os.path.join(ws, ".eval", "tools.jsonl")
    calls = []
    if os.path.isfile(path):
        with open(path) as f:
            calls = [json.loads(line) for line in f if line.strip()]
    checks = [c for c in calls if c["tool"] == "tsc" or (c["args"][:1] in (["check"], ["test"]))]
    codes = collections.Counter(code for c in calls for code in c.get("codes", []))
    return {
        "tool_calls": len(calls),
        "check_calls": len(checks),
        "failed_checks": sum(1 for c in checks if c["exit"] not in (0, None)),
        "run_calls": sum(1 for c in calls if c["args"][:1] == ["run"]),
        "error_codes": dict(codes.most_common()),
    }


def run_agent(cmd_template, ws, prompt_file, model, round_no, log_path, timeout):
    cmd = cmd_template.format(workspace=shlex.quote(ws), prompt_file=shlex.quote(prompt_file),
                              model=shlex.quote(model), round=round_no)
    env = dict(os.environ, PATH=os.path.join(ws, "bin") + os.pathsep + os.environ.get("PATH", ""),
               KLAREVAL_MODEL=model, KLAREVAL_WORKSPACE=ws)
    started = time.time()
    with open(log_path, "a") as log:
        log.write(f"\n===== round {round_no}: {cmd}\n")
        log.flush()
        try:
            p = subprocess.run(cmd, shell=True, cwd=ws, env=env, stdout=log, stderr=subprocess.STDOUT,
                               timeout=timeout)
            code = p.returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
    return {"exit": code, "seconds": round(time.time() - started, 1)}


def trial(args):
    os.makedirs(args.out, exist_ok=True)
    ws = os.path.abspath(os.path.join(args.out, "workspace"))
    prepare(args.task, args.lang, ws, args.docs)
    record = {"task": args.task, "lang": args.lang, "model": args.model, "docs": args.docs,
              "max_rounds": args.rounds, "rounds": []}
    prompt = os.path.join(ws, "PROMPT.md")
    for r in range(1, args.rounds + 1):
        agent = run_agent(args.agent_cmd, ws, prompt, args.model, r, os.path.join(args.out, "agent.log"),
                          args.agent_timeout)
        result = grade(args.task, args.lang, ws)
        record["rounds"].append({"round": r, "agent": agent, "build_ok": result["build_ok"],
                                 "passed": result["passed"], "total": result["total"],
                                 "failing": [t["name"] for t in result["tests"] if not t["ok"]]})
        with open(os.path.join(args.out, f"grade-{r}.json"), "w") as f:
            json.dump(result, f, indent=2)
        if result["all_pass"]:
            break
        with open(os.path.join(ws, "FEEDBACK.md"), "w") as f:
            f.write(feedback_text(result))
        with open(prompt, "w") as f:
            f.write(FIX_PROMPT)
    summarize(record, ws)
    with open(os.path.join(args.out, "trial.json"), "w") as f:
        json.dump(record, f, indent=2)
    print(json.dumps({k: v for k, v in record.items() if k != "rounds"}, indent=2))


def record_round(args):
    ws = os.path.abspath(os.path.join(args.trial, "workspace"))
    path = os.path.join(args.trial, "trial.json")
    if os.path.isfile(path):
        with open(path) as f:
            record = json.load(f)
    else:
        record = {"task": args.task, "lang": args.lang, "model": args.model, "docs": args.docs,
                  "max_rounds": args.rounds, "rounds": []}
    r = len(record["rounds"]) + 1
    result = grade(args.task, args.lang, ws)
    record["rounds"].append({"round": r, "agent": {"exit": None, "seconds": args.seconds, "tokens": args.tokens,
                                       "tool_uses": args.tool_uses},
                             "build_ok": result["build_ok"], "passed": result["passed"],
                             "total": result["total"],
                             "failing": [t["name"] for t in result["tests"] if not t["ok"]]})
    with open(os.path.join(args.trial, f"grade-{r}.json"), "w") as f:
        json.dump(result, f, indent=2)
    if not result["all_pass"]:
        with open(os.path.join(ws, "FEEDBACK.md"), "w") as f:
            f.write(feedback_text(result))
        with open(os.path.join(ws, "PROMPT.md"), "w") as f:
            f.write(FIX_PROMPT)
    summarize(record, ws)
    with open(path, "w") as f:
        json.dump(record, f, indent=2)
    print(f"round {r}: " + ("build failed" if not result["build_ok"] else f"{result['passed']}/{result['total']} passed"))


def summarize(record, ws):
    rounds = record["rounds"]
    record["first_try_pass"] = bool(rounds) and rounds[0]["build_ok"] and rounds[0]["passed"] == rounds[0]["total"]
    record["first_try_score"] = round(rounds[0]["passed"] / rounds[0]["total"], 3) if rounds else 0
    passing = [r["round"] for r in rounds if r["build_ok"] and r["passed"] == r["total"]]
    record["rounds_to_pass"] = passing[0] if passing else None
    record["final_score"] = round(rounds[-1]["passed"] / rounds[-1]["total"], 3) if rounds else 0
    record["tools"] = tool_stats(ws)


def agent_total(trial, key):
    vals = [r.get("agent", {}).get(key) for r in trial["rounds"]]
    return None if not vals or any(v is None for v in vals) else sum(vals)


def fmt_total(trial, key, fmt):
    v = agent_total(trial, key)
    return "-" if v is None else fmt.format(v)


def median(trials, key, fmt):
    vals = sorted(v for v in (agent_total(t, key) for t in trials) if v is not None)
    if not vals:
        return "-"
    mid = len(vals) // 2
    return fmt.format(vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2)


def report(dirs):
    trials = []
    for d in dirs:
        for root, _, files in os.walk(d):
            if "trial.json" in files:
                with open(os.path.join(root, "trial.json")) as f:
                    trials.append(json.load(f))
    if not trials:
        print("no trial.json found")
        return
    rows = ["| task | language | model | first try | first-try tests | rounds to pass | final tests | checks (failed) | error codes | agent time | agent tokens |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for t in sorted(trials, key=lambda t: (t["task"], t["lang"], t.get("model", ""))):
        first = t["rounds"][0] if t["rounds"] else {"passed": 0, "total": 0}
        last = t["rounds"][-1] if t["rounds"] else {"passed": 0, "total": 0}
        tools = t.get("tools", {})
        codes = ", ".join(f"{k}x{v}" for k, v in list(tools.get("error_codes", {}).items())[:4]) or "-"
        rows.append(f"| {t['task']} | {t['lang']} | {t.get('model', '')} | {'pass' if t.get('first_try_pass') else 'fail'} "
                    f"| {first['passed']}/{first['total']} | {t.get('rounds_to_pass') or '-'} "
                    f"| {last['passed']}/{last['total']} | {tools.get('check_calls', 0)} ({tools.get('failed_checks', 0)}) | {codes} "
                    f"| {fmt_total(t, 'seconds', '{:.0f} s')} | {fmt_total(t, 'tokens', '{:,}')} |")
    by_lang = collections.defaultdict(list)
    for t in trials:
        by_lang[t["lang"]].append(t)
    rows += ["", "| language | trials | first-try pass rate | mean first-try tests | pass within all rounds | median agent time | median agent tokens |",
             "|---|---|---|---|---|---|---|"]
    for lang, ts in sorted(by_lang.items()):
        n = len(ts)
        rows.append(f"| {lang} | {n} | {sum(t.get('first_try_pass', False) for t in ts)}/{n} "
                    f"| {sum(t.get('first_try_score', 0) for t in ts) / n:.0%} "
                    f"| {sum(1 for t in ts if t.get('rounds_to_pass'))}/{n} "
                    f"| {median(ts, 'seconds', '{:.0f} s')} | {median(ts, 'tokens', '{:,.0f}')} |")
    print("\n".join(rows))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--task", required=True, choices=task_names())
        p.add_argument("--lang", required=True, choices=sorted(LANGS))

    p = sub.add_parser("prepare")
    common(p)
    p.add_argument("--out", required=True)
    p.add_argument("--docs", default="repo", choices=["repo", "spec"],
                   help="repo: SPEC, std sources, README, DESIGN and examples; spec: SPEC and std sources only")
    p = sub.add_parser("grade")
    common(p)
    p.add_argument("--workspace", required=True)
    p.add_argument("--json", help="write the full result here")
    p.add_argument("--feedback", action="store_true", help="also write FEEDBACK.md into the workspace")
    p = sub.add_parser("trial")
    common(p)
    p.add_argument("--agent-cmd", required=True,
                   help="shell command that runs the agent once; {workspace}, {prompt_file}, {model}, {round} "
                        "are filled in; it runs inside the workspace with bin/ first on PATH")
    p.add_argument("--model", default=os.environ.get("KLAREVAL_MODEL", "unspecified"))
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--agent-timeout", type=int, default=1800)
    p.add_argument("--docs", default="repo", choices=["repo", "spec"])
    p.add_argument("--out", required=True)
    p = sub.add_parser("record")
    common(p)
    p.add_argument("--trial", required=True, help="trial folder holding workspace/")
    p.add_argument("--model", default=os.environ.get("KLAREVAL_MODEL", "unspecified"))
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--docs", default="repo", choices=["repo", "spec"])
    p.add_argument("--seconds", type=float, default=None, help="agent time for this round, if known")
    p.add_argument("--tokens", type=int, default=None, help="tokens the agent used this round, if known")
    p.add_argument("--tool-uses", type=int, default=None, help="tool calls the agent made this round, if known")
    p = sub.add_parser("report")
    p.add_argument("dirs", nargs="+")
    a = ap.parse_args()

    if a.cmd == "prepare":
        prepare(a.task, a.lang, os.path.abspath(a.out), a.docs)
        print(os.path.abspath(a.out))
    elif a.cmd == "grade":
        ws = os.path.abspath(a.workspace)
        result = grade(a.task, a.lang, ws)
        if a.json:
            with open(a.json, "w") as f:
                json.dump(result, f, indent=2)
        if a.feedback:
            with open(os.path.join(ws, "FEEDBACK.md"), "w") as f:
                f.write(feedback_text(result))
        status = "build failed" if not result["build_ok"] else f"{result['passed']}/{result['total']} passed"
        print(f"{a.task} [{a.lang}]: {status} in {result['seconds']}s")
        if not result["build_ok"]:
            print(result["build_log"][-3000:])
        for t in result["tests"]:
            if not t["ok"]:
                print(f"  FAIL {t['name']}: {t['message'][:300]}")
        sys.exit(0 if result["all_pass"] else 1)
    elif a.cmd == "trial":
        trial(a)
    elif a.cmd == "record":
        record_round(a)
    elif a.cmd == "report":
        report(a.dirs)


if __name__ == "__main__":
    main()
