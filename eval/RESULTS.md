# Results

## Fifth pilot, November 2026: making Klar cheaper for agents

**Question.** Pilot 4 left Klar correct but more expensive than TypeScript. Where does the extra cost go, and
can changes to Klar remove it?

**Setup.** One model (the strong one), tasks `tickets`, `splitter` and `sheet`. First a baseline of the
unchanged Klar and a fresh TypeScript run, side by side, with every agent asked to list the files it read.
Then the changes below, three more Klar trials of each task, and a second TypeScript trial of each so that
both languages are measured in the same session. Records and programs are in
[results/pilot5-2026-11](results/pilot5-2026-11) (`base-*` before, `r1-` to `r3-` after; `r1` ran before
`let ... else`).

**Where the cost was.** The baseline Klar agents read `SPEC.md` in full (about 7,000 tokens), and the browser
app agent also read `std/ui.klar`, `std/html.klar` and four example apps. The TypeScript agents read only the
task. Klar code itself was 10 to 40% longer. The extra tokens matched what was read almost exactly.

**What changed in Klar.**

* **`klar/QUICK.md`**: the language on one page (about 1,300 words), written for agents that already know
  TypeScript or Rust: one annotated program, the rules the checker enforces, every built-in, the library
  modules and a browser app. The eval footer now points at it first, and at `SPEC.md` for lookups. On the
  website, `llms.txt` is now this page and the old full file is `llms-full.txt`.
* **`klar doc [std.NAME | NAME]`**: library signatures with a one-line summary each, generated from the
  sources (all of std: 30 KB instead of 120 KB of sources; `klar doc std.ui` is about 600 tokens).
* **`let x = maybe else { return ... }`**: unwraps `Some`/`Ok` or runs a block that must leave. It replaces
  the four-line `match` that was the most common pattern in agents' Klar, and works with `let (a, b) = ...`.

| task | TypeScript (2 trials) | Klar before | Klar after (3 trials) |
|---|---|---|---|
| tickets | 65,950 tokens, 40 s | 80,048 tokens, 43 s | 70,395 tokens, 42 s |
| splitter | 64,650 tokens, 40 s | 94,155 tokens, 71 s | 75,959 tokens, 56 s |
| sheet | 67,200 tokens, 64 s | 85,756 tokens, 104 s | 78,395 tokens, 107 s |
| **all three** | **197,800 tokens, 144 s** | **260,000 (+31%), 218 s (1.52x)** | **224,800 (+14%), 205 s (1.43x)** |

Means per trial. Every trial in both languages passed every hidden test on the first try (9 Klar trials after
the change, 3 before, 6 TypeScript).

### What this says

* **The token gap more than halved, from +31% to +14%.** No agent after the change read `SPEC.md` in full; they
  read `QUICK.md`, ran `klar doc` for the one or two modules they used, and grepped `SPEC.md` for a detail at
  most. On `tickets` Klar is now within 7% of TypeScript's tokens and level on time.
* **Klar is not yet cheaper than TypeScript**, and these numbers say why: TypeScript costs the agent nothing to
  learn, so Klar starts about 2,000 tokens behind (QUICK.md), and its programs are still 10 to 20% longer.
  Beating TypeScript needs Klar programs shorter than TypeScript programs, not only cheaper docs.
* **`sheet` time did not move** (about 105 s against 64 s). Those agents spent their time designing and
  testing a graph algorithm; with the docs cost gone, what remains is writing a longer program and one
  compile-and-fix round (2 of the 3 sheet trials after the change hit a checker error, each fixed in one step).
* **Compiler errors after the change**: `last(xs)` returning an Option (E301), a local named like a top-level
  function (E202), and `let (a, b) = x else` (E103, since supported). QUICK.md now states the first two.
* One `splitter` agent still read an example app in full (82,000 tokens against 73,000 for the other two).
  The footer no longer lists the examples folder.

### Next

Programs shorter than TypeScript are the remaining lever. Candidates, each a change to how Klar looks and so
Arno's call: `+=` (SPEC says "no += or ++"), an implicit value for the last expression of a block, and
string/list helpers that remove loops the agents wrote by hand (`pad_left`, `count`, `group_by`).

## Fourth pilot, November 2026: Klar after the fixes and the faster interpreter

**Setup.** After fixing the gaps the earlier pilots found and after `klar run` became 10 to 110 times faster
(it now compiles programs to Python code), 8 new Klar trials on `tickets`, `splitter` and `sheet` (twice),
with each model. TypeScript was not rerun: nothing on that side changed, so the TypeScript trials of pilots 1
to 3 are the baseline. Records and programs are in [results/pilot4-2026-11](results/pilot4-2026-11).

| task | model | first try | checks (failed) | compiler errors | agent time | agent tokens |
|---|---|---|---|---|---|---|
| tickets | strong | pass | 1 (0) | - | 48 s | 76,756 |
| splitter | strong | pass | 2 (0) | - | 60 s | 90,384 |
| sheet (1) | strong | pass | 1 (0) | - | 102 s | 84,879 |
| sheet (2) | strong | pass | 2 (1) | E311 | 91 s | 84,133 |
| tickets | small | pass | 2 (0) | - | 83 s | 91,430 |
| splitter | small | pass | 2 (1) | E104 | 100 s | 102,937 |
| sheet (1) | small | pass | 3 (0) | - | 313 s | 113,631 |
| sheet (2) | small | pass | 2 (0) | - | 152 s | 107,240 |

Totals for the same mix of tasks (one `tickets`, one `splitter`, two `sheet`), against the TypeScript
medians for each task:

| model | Klar time vs TypeScript, before → now | Klar tokens vs TypeScript, before → now |
|---|---|---|
| strong (pilot 2 → 4) | 3.8x → **1.4x** | +36% → **+26%** |
| small (pilot 3 → 4) | 3.0x → **2.1x** | +52% → **+35%** |

### What this says

* **The cost gap narrowed a lot, mostly on `sheet`.** The strong model's `sheet` trials went from 116 to
  545 s down to 91 and 102 s: no agent had to redesign for speed or work around recursion depth. On `tickets`
  and `splitter` the strong model is now within 1.15x to 1.35x of TypeScript's time.
* **Klar still costs more**, chiefly in tokens: reading SPEC and the std sources is a fixed price of roughly
  15,000 to 20,000 tokens per task that TypeScript does not pay.
* **Correctness unchanged:** 8 of 8 passed every hidden test first try, as before.
* **The small model's slowest trial was its own algorithm**: a reachability search per cell instead of one
  strongly-connected-components pass, which took minutes on large sheets in any language.

### New friction, and what was fixed right away

* An E104 inside a `"""..."""` string of CSS did not mention raw strings. **Fixed:** the hint now says to use
  `r"..."` or `r"""..."""` for text full of braces.
* `klar fmt --check` listed the starter file without saying why, and the starter itself was not formatted.
  **Fixed:** `--check` now prints the diff (to standard error), and every eval source is formatted.
* Not fixed (no room under SPEC's word cap; the sources document them): `ui_start` for a custom stylesheet,
  whether passing a `var` list to a function copies it, and whether `on_submit` stops the browser's own
  submit (it does).

## Third pilot, November 2026: a smaller model

**Setup.** The second pilot's tasks and method with a smaller, cheaper model from the same family as the
subject (recorded as `small-agent`), to test the idea that a weaker model gains more from Klar's checker.
`tickets` and `sheet` twice per language, `splitter` once. 10 trials. Raw records and programs are in
[results/pilot3-2026-11](results/pilot3-2026-11).

| task | language | first try | first-try tests | rounds to pass | checks (failed) | compiler errors | agent time | agent tokens |
|---|---|---|---|---|---|---|---|---|
| tickets (1) | Klar | pass | 17/17 | 1 | 2 (0) | - | 72 s | 88,075 |
| tickets (2) | Klar | **fail** | 16/17 | 2 | 3 (0) | - | 141 s | 99,382 |
| tickets (1) | TypeScript | pass | 17/17 | 1 | 1 (0) | - | 57 s | 73,471 |
| tickets (2) | TypeScript | pass | 17/17 | 1 | 3 (0) | - | 58 s | 72,958 |
| splitter | Klar | pass | 14/14 | 1 | 2 (0) | - | 110 s | 113,222 |
| splitter | TypeScript | pass | 14/14 | 1 | 2 (0) | - | 56 s | 71,599 |
| sheet (1) | Klar | pass | 22/22 | 1 | 3 (0) | - | 371 s | 127,729 |
| sheet (2) | Klar | pass | 22/22 | 1 | 3 (0) | - | 340 s | 133,401 |
| sheet (1) | TypeScript | pass | 22/22 | 1 | 2 (0) | - | 99 s | 82,744 |
| sheet (2) | TypeScript | pass | 22/22 | 1 | 3 (0) | - | 95 s | 80,867 |

Medians: Klar 141 s and 113,222 tokens, TypeScript 58 s and 73,471 tokens (times and tokens include fix
rounds).

### What this says

* **The smaller model is also at the ceiling.** 9 of 10 trials passed first try, and none of the 24 checks in
  either language failed: the smaller model did not write a single program the compiler rejected. So the
  "weaker models need the checker more" idea gets no support at this task size either.
* **The one failure was the task's fault, not the language's.** The ticket said "If PERSON is already on the
  waitlist, print `error: already waiting`" under the waitlist bullet; this agent applied it to every buy,
  while the hidden tests (and the other 7 agents given this task in pilots 2 and 3) applied it only to a buy
  that does not fit. The agent fixed it in one round from the feedback. `TASK.md` now says which reading is
  meant.
* **Klar's cost gap is larger with the smaller model: about 2.4x the time and 54% more tokens.** `sheet` is the
  worst case (3.6x the time): both Klar agents first wrote a correct but slow design, found it took 20 to 120
  seconds on large inputs in the interpreter, and rewrote it.
* **Harness fixes from this run:** the feedback file cut each failing test's message at 500 characters, which
  cut the test input mid-line; it now keeps 4,000. Tool calls were logged for every trial this time.

### New Klar friction

Repeats of pilot 2 (asynchronous `std.ui` redraws, `std.ui` signatures and `on_key`'s key string only in the
sources, no top-level constants, `klar` vs `klarc`, interpreter speed) plus:

1. **No `ord`/character code and no list `pop`.** Digit and column values went through `index_of` over
   `chars("0123456789")`; an explicit stack needed `set_at` and a separate top index. **Fixed:** `ord`, `chr`,
   `pop` (O(1) on a `var`, like `push`) and `last`.
2. **No assignment to several existing variables from a tuple** (`(a, b) = f()`), so agents used small structs.
   **Fixed:** `(a, b) = f()` assigns existing `var`s.
3. **The E403 stack trace did not point where the agent expected** for a failed `unwrap`; it had to read
   the reported line to tell which `unwrap` failed. **Fixed:** each frame of a trace now shows its own line
   (it showed the line its caller called it from).
4. **Unclear whether passing a large map to a function copies it**; one agent suspected that as the cause of a
   slow program and could not confirm it.

## Second pilot, November 2026: harder tasks

**Setup.** Same agent and method as the first pilot below, after fixing findings 1 to 5 of that pilot. Two new
tasks: `tickets` (change an existing program: fix a refund bug, add a waitlist and a command, keep all other
output identical) and `splitter` (a browser app with storage, tested in headless Chromium through element
ids). Two trials per language on each, plus two more Klar trials of `sheet` to see whether the fixes changed
its cost. 10 trials. Raw records and programs are in [results/pilot2-2026-11](results/pilot2-2026-11).

| task | language | first try | first-try tests | checks (failed) | compiler errors | agent time | agent tokens |
|---|---|---|---|---|---|---|---|
| tickets (1) | Klar | pass | 17/17 | 1 (0) | - | 52 s | 79,649 |
| tickets (2) | Klar | pass | 17/17 | 2 (1) | E301 | 57 s | 80,754 |
| tickets (1) | TypeScript | pass | 17/17 | not logged | - | 36 s | 65,734 |
| tickets (2) | TypeScript | pass | 17/17 | not logged | - | 35 s | 65,643 |
| splitter (1) | Klar | pass | 14/14 | 2 (0) | - | 106 s | 100,854 |
| splitter (2) | Klar | pass | 14/14 | 2 (0) | - | 86 s | 101,485 |
| splitter (1) | TypeScript | pass | 14/14 | 2 (0) | - | 61 s | 67,545 |
| splitter (2) | TypeScript | pass | 14/14 | 1 (0) | - | 43 s | 65,547 |
| sheet (3) | Klar | pass | 22/22 | 1 (0) | - | 116 s | 84,952 |
| sheet (4) | Klar | pass | 22/22 | 3 (0) | - | 545 s | 96,037 |

On `tickets` and `splitter` only: Klar median 72 s and 90,800 tokens, TypeScript 40 s and 65,700 tokens.

### What this says

* **Still no difference in correctness.** All 10 trials passed every hidden test on the first try, including
  the browser app and the change to existing code. These tasks do not separate the languages for this model
  either, so the claim "fewer errors in Klar" remains untested rather than confirmed.
* **Klar still costs more: about 1.8x the time and 38% more tokens** on the new tasks. Most of the extra
  tokens are reading SPEC and std sources; the browser app cost the most, because `std.ui` behaviour had to
  be read from the runtime (see below).
* **The pilot-1 fixes worked where they applied.** No agent hit list copying, `None => break`, stdin or exit
  status problems. `sheet` in Klar ran 116 s and 545 s (225 s and 406 s before). The slow trial spent its time
  on its own choice to write a second implementation in Python and compare 150 random sheets, and then on
  interpreter speed for an extreme input it made up (1.9M cell references, 53 s); the hidden tests never
  come close to that.
* **One compiler error in 10 trials**, and it was Klar's: `push([text], lines)` gave E301 with a hint to use
  `str(value)`, when the fix is `[text] + lines`.
* **Measurement gap.** The two TypeScript `tickets` trials compiled (their notes say so and `dist/` was
  built) but the wrapper logged no calls, so their check counts are missing. One Klar agent's first command
  also failed with `klarc: command not found` after `export PATH=...`. The cause is not known; the next run
  should log through a mechanism that does not depend on the agent's PATH.

### What tripped the agents up in Klar

None of these came up for TypeScript, apart from the TASK wording point in 7.

1. **`std.ui` redraws one frame late, and nothing says so.** The page re-renders on `requestAnimationFrame`,
   so a test (or an agent's own Playwright script) that reads the DOM right after a click sees the old
   state, or half of the new one. Both `splitter` agents lost time on it; our own hidden tests had to poll.
   SPEC should say it, and the runtime could render synchronously after an event handler. **Fixed:** the page
   re-renders as soon as `update` returns, and SPEC says so.
2. **`std.ui` details only in the sources**: whether `value(...)` sets the live input value on every redraw
   (it does), that `on_key` exists (SPEC lists the name only), the `ui_load` in `main` + `ui_save` in `update`
   recipe for storage, and that `std.ui` alone is enough without `std.kit` or `std.layout`. **Fixed:** in SPEC
   and the `std.ui` / `std.html` sources.
3. **Recursion depth is not stated.** A recursive function fails with E406 somewhere between depth 1,000 and
   3,000 in the interpreter. Both `sheet` agents tested for it and rewrote their searches to be iterative.
4. **Interpreter speed is still not documented**: about 2.5 microseconds per loop iteration that indexes a
   list. Agents could not tell whether compiling the program was allowed.
5. **The name rule in SPEC does not match the checker.** SPEC says a name is declared once per function,
   inner blocks included; the checker accepts `let w` in two sibling blocks and a match binding that reuses a
   name from elsewhere. One agent was left unsure which is right. **Fixed:** SPEC states the checker's rule (no
   redeclaring a name while it is in scope; separate blocks may reuse it).
6. **No top-level constants**, and SPEC does not say to use zero-argument functions instead. **Fixed** in SPEC.
7. **Smaller**: the E301 hint for `push(list, list)` (above); TASK says `klarc` while SPEC says `klar`; when
   a function ending in a `match` whose arms all return counts as returning (E305) is not spelled out.
   **Fixed:** the hint now points at `xs + ys`, the eval workspace command is `klar`, and SPEC states the E305
   rule.

TypeScript agents' only notes were about the workspace: `tsconfig.json` compiles every `.ts` file, so test
scripts had to be `.cjs`, and `"type": "module"` blocks `require`.

### What the next run needs

* **A smaller or cheaper model as the subject.** A strong model at the ceiling on every task cannot show a
  difference in errors; the compiler's help should matter more where first drafts are wrong. The harness
  already records the model name; the subject just has to be configured.
* Fix findings 1 to 3 above and the E301 hint, then rerun `splitter`.
* Fix the tool logging gap before counting checks again.

## Pilot, November 2026

**Setup.** One general-purpose coding agent (a current frontier model, recorded as `pilot-agent`), run with
`prepare` + `record` (see [README.md](README.md)): a fresh agent per trial, no memory between trials, the same
instructions in both languages, Klar docs as in `--docs repo`. Four tasks; one trial per task and language,
plus a second trial of each language on `sheet`, the hardest task. 10 trials in all. Each agent was also asked
to list what confused or slowed it down. Raw records, grades and the programs the agents wrote are in
[results/pilot-2026-11](results/pilot-2026-11).

| task | language | first try | first-try tests | checks (failed) | compiler errors | agent time | agent tokens |
|---|---|---|---|---|---|---|---|
| ledger | Klar | pass | 21/21 | 3 (1) | E301 | 54 s | 81,377 |
| ledger | TypeScript | pass | 21/21 | 1 (0) | - | 37 s | 64,304 |
| orders | Klar | pass | 20/20 | 1 (0) | - | 69 s | 84,051 |
| orders | TypeScript | pass | 20/20 | 1 (0) | - | 34 s | 63,823 |
| notes_api | Klar | pass | 18/18 | 2 (0) | - | 105 s | 101,891 |
| notes_api | TypeScript | pass | 18/18 | 1 (0) | - | 48 s | 66,122 |
| sheet (1) | Klar | pass | 22/22 | 3 (0) | - | 225 s | 92,670 |
| sheet (2) | Klar | pass | 22/22 | 6 (1) | E311 ×2 | 406 s | 105,529 |
| sheet (1) | TypeScript | pass | 22/22 | 1 (0) | - | 67 s | 67,863 |
| sheet (2) | TypeScript | pass | 22/22 | 1 (0) | - | 60 s | 67,227 |

| language | trials | first-try pass | median agent time | median agent tokens | failed checks |
|---|---|---|---|---|---|
| Klar | 5 | 5/5 | 105 s | 92,670 | 2 of 15 |
| TypeScript | 5 | 5/5 | 48 s | 66,122 | 0 of 5 |

### What this says

* **No difference in correctness, and Klar was more expensive.** Every trial in both languages passed every
  hidden test on the first try, so this pilot cannot show that Klar leads to fewer bugs. It does show the
  cost of a new language: the Klar agents took about twice as long (5x on `sheet`) and used about 40% more
  tokens, mostly reading the reference and std sources, and they hit 3 compiler errors where the TypeScript
  agents hit none.
* **That is a real result about the claim, not only about the pilot.** The bugs Klar is designed to prevent
  (null dereferences, unhandled exceptions, missed cases) did not occur in TypeScript: given a precise spec,
  a strong model writes careful strict TypeScript (BigInt for money, `never`-returning helpers, iterative
  graph searches) without help. Klar's advantage, if there is one, has to show up somewhere else: weaker or
  cheaper models, vaguer specs, larger programs, changes to existing code, or when the first draft is wrong
  and the agent has to repair it.
* **The tasks are too easy for this model.** All tests passed, so the scores are at the ceiling and only cost
  differs. The next run needs tasks with a meaningful failure rate (see below).
* **Klar's compiler feedback worked when it was hit.** Both E311 errors were fixed in one step from the hint.
  The E301 was not (see finding 1): the agent changed its approach instead.

### What tripped the agents up in Klar

From the agents' own notes and the tool logs, most important first. None of these came up for TypeScript.

1. **The interpreter is slow, and its lists are not what SPEC says.** SPEC §5 says `xs = push(xs, v)` and
   `set_at` are O(1) on a `var`; in `klar run` (the interpreter) each call copies the list
   (`klar/interp.py`, `b_push` / `b_set_at`). One `sheet` agent's program went from 8 s to 91 s because of
   it and the agent had to profile the interpreter to find out; the other rewrote its design after a 31 s
   test. Both `sheet` trials spent most of their extra time on speed, not correctness. Fix: in-place updates
   in the interpreter too, or at least say in SPEC that the O(1) promise is for compiled code and give the
   interpreter's rough speed.
2. **`None => break` cannot end a value match.** `let line = match read_line() { Some(l) => l, None => break }`
   fails with E301 "`line` would hold no value (the expression has type unit)", although SPEC §6 shows
   `None => break` as an arm and says an always-returning block arm does not affect the type. `break` and
   `continue` (bare or in a block) should be treated like `return`. Hit by the ledger agent; the reference
   solution hit it too. Reading every line of input is the most common loop there is, so this matters.
   **Fixed:** `break` and `continue` arms now end value matches in every backend, and an E301 caused by a
   `{ }` arm names that arm and says how to fix it.
3. **No way to read all of standard input, and no stderr.** Agents loop over `read_line()` and join the
   lines. There is no `print` to standard error at all. Wanted: `read_all() -> str` (or `read_stdin`) and
   `eprint`. **Fixed:** built-ins `read_all()` and `eprint(value)`.
4. **Exit codes are hard to find.** SPEC §7 has no built-in for the exit status; agents found `process_exit`
   in `std.process` (and one wondered whether it counts as a forbidden Python extern). **Fixed:** SPEC §7 now
   says how the exit status is set.
5. **JSON numbers are floats.** `std.json` reads every number as a float, so whole numbers above 2^53 lose
   precision and a literal like `1e400` makes `json_parse` fail the whole document. `json_int` floors NaN or
   infinity without saying what happens. The typed `json_decode` reads into `int` fields, so this mostly bites
   code that has to validate untyped JSON (the `orders` task). SPEC says only "Numbers are floats".
   **Fixed:** `json_int` returns None for numbers it cannot hold exactly (from 2^53 up, infinity), the
   interpreter rejects `NaN` like the other backends, and SPEC says to decode into an `int` field for exact
   integers. `Json` itself still stores floats; an exact integer variant would break existing matches.
6. **Unwritten behaviour that agents had to read the Python sources for**: whether `read_line` strips `\r`,
   which characters `trim` removes, what `parse_int` accepts (it follows Python's `int()`, so `"+1"`, `" 1"`
   and `"1_0"` parse), whether `sort` on strings is byte or code-point order, whether the HTTP server
   URL-decodes query values and drops empty ones, and which Content-Type a response gets by default.
   **Fixed in part:** SPEC now states what `read_line`, `parse_int`/`parse_float` and string ordering do, and
   `parse_float` accepts the same text in every backend (it took `1_0` in the interpreter and Node and hex
   in native code).
7. **`null` and absent are the same in typed JSON.** `web_body` into a struct with `Option` fields cannot tell
   `{"body": null}` from `{}`, so APIs that must reject `null` have to fall back to the `Json` enum. (Found while
   writing the reference solution; the pilot agent used SQLite and the `Json` enum from the start.)
8. **Smaller friction**: the docs call the tool `klar` while the command is `klarc`; no tuples, so a parser
   returns a helper struct (`calc.klar` shows the pattern); E311 for capturing a `var` in a lambda is easy to
   hit when passing closures to `map`/`filter`, though its hint is good; `fn(req) => { ... }` gives E103 with
   a hint saying blocks may follow `=>` (they may in match arms, not in anonymous functions; the fix is
   `fn(req: T) -> R { ... }`); `std.web` answers unknown paths and wrong methods in plain text with no hook
   to change it, so a JSON API wraps `web_handle`.

### What a full run should change

* **Harder tasks**, so first-try scores fall below 100%: longer specs with interacting rules, a browser app
  (`std.ui` against TypeScript and the DOM), and tasks that modify an existing program. Keep `sheet`; drop or
  extend `orders`, the easiest.
* **More models**, including smaller ones, where the compiler's help should matter more.
* **5+ trials per cell** and a significance test on first-try pass rate and on rounds to pass.
* **Run Klar programs compiled** (`klar build --target node` or native) as a second Klar setting, so
  interpreter speed stops dominating the Klar agent's time.
* Re-run after fixing findings 1 to 4; they account for most of the extra time.
