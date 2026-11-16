# klareval: do agents make fewer mistakes in Klar?

Klar's central claim is not speed. It is that an AI agent can build a correct program in Klar with fewer
mistakes than in a mainstream language, because the language removes whole classes of bugs (null, exceptions,
unchecked side effects, non-exhaustive matches) and its compiler explains every error with a fix. This folder
is the test of that claim: a fixed set of programming tasks, hidden acceptance tests that grade every language
the same way, and a harness that runs an agent on each task in **Klar** and in **TypeScript** and records how
it went.

The model under test has almost certainly seen millions of lines of TypeScript and none of Klar. That is the
real situation Klar is in, so the test does not try to remove it: the Klar agent gets the language reference
and the repository's docs and examples, the TypeScript agent gets nothing but the task.

Results so far are in [RESULTS.md](RESULTS.md).

## What is measured

For each task, language and model, one *trial*:

1. A fresh workspace is prepared with `TASK.md` (the same task text for both languages, plus a short footer
   naming the file to write and the commands available) and the toolchain.
2. The agent works until it says it is done. It may check, compile and run its code as often as it likes.
   Every call to `klarc` or `tsc` goes through a logging wrapper (`bin/`), so the number of checks and every
   error code it hit (`E301`, `TS2345`) are recorded in `.eval/tools.jsonl`.
3. The program is built the way CI would build it (`klarc check`; `tsc -p .` with `strict` and
   `noEmitOnError`, using the original `tsconfig.json`) and the task's hidden tests run against it as a black
   box (standard input and output, exit status, or HTTP).
4. If tests fail, the agent gets `FEEDBACK.md`, which lists each failing test with a one-line reason, and
   another round. Up to 3 rounds by default.

Per trial the harness reports:

| Metric | Meaning |
|---|---|
| first try | all hidden tests pass after round 1 |
| first-try tests | hidden tests passed after round 1 |
| rounds to pass | the round in which all tests first passed |
| final tests | hidden tests passed after the last round |
| checks (failed) | type-check or compile calls the agent made, and how many reported errors |
| error codes | which compiler errors the agent ran into, most frequent first |

Agents are also asked to finish with a list of what confused or slowed them down. Those notes, and the
compiler errors they hit, are the most useful output for improving Klar.

## Tasks

| Task | Kind | Hidden tests | What it probes |
|---|---|---|---|
| [ledger](tasks/ledger/TASK.md) | command-line tool, text in and out | 21 | parsing, exact money arithmetic (amounts up to 10^15), error ordering, exit codes |
| [orders](tasks/orders/TASK.md) | JSON in, JSON out | 20 | validating untyped JSON (`null` versus absent, `2.5` versus `"2"`), aggregation |
| [notes_api](tasks/notes_api/TASK.md) | HTTP JSON API with an HTML page and persistence | 18 | routing, request validation, status codes, HTML escaping, data surviving a restart |
| [sheet](tasks/sheet/TASK.md) | spreadsheet evaluator | 22 | a parser, recursive evaluation, cycle detection, precise error rules; includes generated sheets checked against an independent model |
| [tickets](tasks/tickets/TASK.md) | change an existing program | 17 | reading someone else's code, a bug described only by its symptom, a feature ticket that must fit the existing conventions, no regressions |
| [splitter](tasks/splitter/TASK.md) | browser app | 14 | DOM events and forms, state, rounding, `localStorage`, text that must not become markup; tested in headless Chromium |

`tickets` ships the program to change in `starter/` (the same program in both languages, with the same bug).
`splitter`'s tests (`hidden/tests.mjs`) drive the page with Playwright and wait up to two seconds for each
expected state, because a page may render on the next animation frame (Klar's `std.ui` does).

Each task folder holds `TASK.md`, `hidden/tests.py` (never shown to the agent) and `reference/` solutions in
both languages. The reference solutions pass every test; they prove the tests are passable in both
languages and are checked by `tests/test_eval.py`.

Rules that keep the comparison fair:

* One task text for both languages, with no hints about language-specific traps.
* Hidden tests are black-box and identical across languages; feedback between rounds has the same shape.
* TypeScript runs in strict mode with Node.js type definitions and only built-in modules; Klar uses only its
  standard library. Neither side can install packages.
* A static check failure is a failed build in both languages.

## Running it

Requirements: Python 3.10+, Node.js 22+ with `tsc` on `PATH` and `@types/node` installed somewhere (found
through `npm root -g`, or set `KLAREVAL_NODE_TYPES`), and for browser tasks Playwright with Chromium for Node.js.

Any agent that can be started from a shell command can be tested. The command runs inside the workspace, with
`bin/` first on `PATH`; `{workspace}`, `{prompt_file}`, `{model}` and `{round}` are filled in. `PROMPT.md`
holds the instruction for the current round.

```sh
H=eval/harness/klareval.py
for task in ledger orders notes_api; do
  for lang in klar ts; do
    for n in 1 2 3 4 5; do
      python3 $H trial --task $task --lang $lang --model MODEL_ID \
        --agent-cmd 'your-agent --model {model} --prompt-file {prompt_file}' \
        --out results/MODEL_ID/$task-$lang-$n
    done
  done
done
python3 $H report results/MODEL_ID
```

For an agent that is driven by hand (an IDE or chat agent), prepare the workspace, let the agent work in it,
then record each round:

```sh
python3 $H prepare --task orders --lang klar --out results/run1/orders-klar/workspace
# ... the agent works in that folder, following PROMPT.md ...
python3 $H record --task orders --lang klar --trial results/run1/orders-klar --model MODEL_ID
# if tests failed, the agent reads FEEDBACK.md and tries again; record again
```

`grade` checks any workspace without recording a round; `--docs spec` gives the Klar agent only the language
reference and standard library sources instead of the repository's README, design notes and examples.

## What a full run needs

The pilot in [RESULTS.md](RESULTS.md) is 10 trials with one model. It shows the harness works and where Klar
hurts, but every trial passed, so it cannot separate the languages on correctness. A run that can needs:

* at least 5 trials per task, language and model (agents are not deterministic), so 30 trials per model for the
  current three tasks;
* two or more models, ideally of different sizes, since a weaker model may depend more on the compiler's help;
* more tasks, especially a browser app (Klar's `std.ui` against TypeScript with the DOM) and a task that
  changes an existing codebase rather than starting from nothing;
* the agent's token use and wall time per trial, which the agent command should print and the harness should
  record.

Each trial takes from a few minutes (command-line tasks) to about fifteen (the HTTP task in Klar, where the
agent reads the reference first).
