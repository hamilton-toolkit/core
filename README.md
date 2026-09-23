# Hamilton

> Forget vibe coding, start engineering.

Hamilton is a small framework for building software **with** an AI agent
without taking its word for when the work is done. You write down what the
software must do — as specific, testable acceptance criteria — *before* it is
built. The agent then implements it and writes the tests. One command,
`hamilton verify`, verifies that every criterion has a test that names it, that
the suite passes, and that a reviewer Hamilton runs itself has judged each test
to prove its criterion — and judged it again after either one changed.
Specification, code and tests live in one repository and land in one review.

## Why

Vibe coding optimises for the first working version and leaves you to discover
later what it actually does. That is fine until the software matters, and then
the missing half of the work — *deciding what it should do, and proving it
does* — is the expensive half.

Hamilton borrows the discipline of the **V-model** from systems engineering:
verification intent is fixed *before* implementation, by someone not yet
anchored by the code, and every requirement is paired with the criteria that
say when it is met. It is deliberately a scale-appropriate slice — one engineer
to a small team, not a defence contractor — but the core move is the same: the
target is written down first, and "done" is checked against it rather than
asserted by whoever (or whatever) wrote the code.

It is named after **Margaret Hamilton**, who ran software engineering for the
Apollo flight computer and coined the term "software engineering" to make the
point that it deserved to be taken as seriously as any other kind.

### The loop

1. **You specify.** In `spec/` you write the vision, the actors, and a tree of
   requirements. Each requirement is one sentence and carries acceptance
   criteria — observable conditions, their expected outcomes, and how each is
   verified (in a real browser, over HTTP, as a unit, ...).
2. **The agent implements.** It writes the tests first, then the code. Each
   test carries a one-line comment naming the single criterion it covers —
   `@covers R-0001/AC1`.
3. **A reviewer judges the tests.** A separate agent session, with no
   tools and no sight of the implementation, checks each criterion's tests
   against it — all of them together, since between them they prove it. When
   it passes them, each tag gets a review suffix —
   `@covers R-0001/AC1 #3f9a2c.81d0e4`; tests it rejects are rewritten.
4. **`hamilton verify` verifies.** It runs your suite and stays red until every
   criterion has a passing, reviewed, tagged test of the kind its verification
   method names. Reword a criterion or edit its test and it goes red again
   until the test is reviewed against the current wording.

The acceptance criteria are yours. The rigorous tests that bind to them are the
agent's. `hamilton verify` is what keeps the two honest.

## The two phases

Work happens in one of two phases, and the point of each is what it stops you
(or the agent) from doing.

**Spec phase** — you decide what the software should do. The agent can edit
`spec/` and nothing else. It cannot start writing code against a target you
have not finished setting.

**Build phase** — the agent writes the code and the tests. It cannot edit
`spec/`, `.claude/`, or `AGENTS.md`, and of `.hamilton/` only `config` — and
there only `test_command` / `paths.<method>` / `run.<method>`, because picking the test
framework and layout is a build-time call. It cannot quietly change a requirement to
match what it built, or rewrite its own rules.

**`hamilton design`** (spec) and **`hamilton build`** (build) each write the
phase to `.hamilton/phase` and print a status banner. `design` then runs a
session — a conversation about the spec. `build` does not: it runs a loop
Hamilton drives itself (below). Either way the phase is fixed while it runs:
it exports `HAMILTON_SESSION`, and both refuse to start when that is already
set, so an agent can't relaunch itself into the other phase. Writes to
read-only paths are refused as fast feedback.

None of this is unbypassable — unset the variable, edit the phase file by hand,
or run an agent directly and you are outside it. It stops drift, not a
determined operator. `hamilton verify` run in CI, which ignores the phase
entirely, is the gate that enforces the outcome for real.

### How a spec session runs

`hamilton design` and `hamilton reverse` drive the agent turn by turn rather
than handing you an agent terminal. Three things follow from Hamilton being
able to see what is happening:

- **Questions are Hamilton's.** The agent asks through a tool; Hamilton renders
  the choices as a cursor list — arrows or Tab to move, Enter to commit, or
  pick *Type my own answer* to write something else. Moving the highlight sends
  nothing, so a mis-pick costs a keystroke rather than the session. Piped or
  non-interactive input falls back to a numbered list, and `NO_COLOR` is
  honoured.
- **Typing is not one line.** Alt+Enter (or Ctrl+J) opens a new line, Enter
  sends — a requirement or a correction is usually a paragraph. All four arrow
  keys move the cursor, and pasting a multi-line block pastes it rather than
  submitting at the first line break. Once sent, your text is reprinted as
  plain text, so copying it from the terminal gives it back exactly as typed.
- **You can see when it is thinking.** An `Engineering…` indicator runs while
  the agent works, with elapsed time once it passes a couple of seconds, and
  gets out of the way whenever it needs to show you something or ask. Under it,
  each subagent the agent runs — a test writer, say — is a live row: what it
  does, for how long, and its latest tool. When it finishes, one `✓` line stays
  behind.
- **You can see what it costs.** After each turn a dim line gives the tokens
  it used and the session's total so far — `12.3k tokens · 84.0k this
  session` — and the session ends with its total, counted the way a build
  run counts them.
- **Finishing a piece of work is not the end of the session.** When the agent
  gives its closing summary, Hamilton shows you the next step — another change,
  a change to requirements you pick from the tree, a decomposition or a
  completeness pass — and the work carries on in the same conversation, so
  nothing already read or ratified is thrown away. *Finish this session* is on that menu, and on every question the
  agent asks, so a session opened by mistake can be left at the first prompt.
- **An interrupted session can be resumed.** Every turn writes
  `.hamilton/session`; the next launch in the same phase offers to pick up
  where you left off — which is what you want when a session dies mid-spec.
  A session you ended with *Finish this session* is complete and is not
  offered again.

### How a build run differs

`hamilton build` is not a session. What comes next in build phase follows from
`hamilton verify`, not from an agent's judgement, so Hamilton runs the loop and
calls an agent only for the parts that need one:

- **Hamilton decides, the agents work.** The gate's findings are the work
  list. A planner scaffolds each new surface as a contract — a route that
  answers `501`, a function that raises — so the tests fail first. One agent
  per criterion writes its tests against it, in a file of their own; it may
  read the code. The reviewer may not: it sees only the spec and the
  criterion's tests, judged together, and that is where the independence
  lives. Then the implementation is written against the tests that passed,
  and the loop starts again at the gate.
- **No migration.** Tests already tagged in shared files stay valid and are
  reviewed where they are. When a run revises a criterion whose tests share
  a file with others, the writer moves them into a file of their own as part
  of that revision.
- **A changed criterion is judged before it is rewritten.** Reword a
  criterion, its Statement or a file it references and its tests are
  reviewed against the new wording first; only the ones the reviewer rejects
  go to a writer. A review is one call without tools; a rewrite is a whole
  agent session.
- **The review converges.** The first review lists what a test covers and
  what is wrong with it. Only what keeps the tests from proving the
  criterion as written is a comment, and only a comment sends them back;
  what would merely make them better is advice, shown when you unfold the
  review. After a revision the reviewer only settles the comments — each
  resolved or not, each covered point still covered or not — and raises
  nothing new. A lost point reopens; a resolved comment becomes a covered
  point. The list only shrinks.
- **A criterion's tests stay short.** More than 200 lines — counting the
  preamble of each file they are in — and Hamilton sends them back to the
  writer unread, without a reviewer: every later step would read them again.
  Tests reviewed before the cap and over it are written again the next time
  a run has work to do.
- **Each method has a command that runs one criterion's tests**
  (`run.<method>`). When one is missing and a run has work to do, the
  planner sets it first, so no step has to work out the project's runners.
- **Each kind of work runs on its own model.** Writing and reviewing tests —
  many small, tightly briefed tasks — run on a mid-tier model; planning and
  coding on the agent's default. `model.<step>` in `.hamilton/config`
  (`plan`, `tests`, `review`, `code`, `clarify`) overrides either. Test
  writers, coders and the planner think at medium effort and reviewers not at all;
  `effort.<step>` overrides it. Each finished task reports its tokens beside
  its time.
- **You are asked one kind of question.** When a criterion cannot be settled
  from its wording, or no test by its method could satisfy it, the run asks
  you. You answer, and Hamilton drafts the change to the spec the way
  `hamilton design` would — the criterion reworded, its method changed (to
  `manual`, say), new criteria added beside it — and shows it before and
  after. You write it, have it changed, or go back; only on your approval
  does it go into the spec, and the tests follow it. A method the spec does
  not define yet is left to `hamilton design`. Otherwise the run stops only
  when a criterion's tests are still rejected after three rounds or a step
  fails. Piped or in CI, it reports the stop and exits non-zero instead of
  asking.
- **The full suite runs once, at the end.** Each writer runs only its own
  criterion's tests, and the coding step only those of the criteria it
  implements — or the failing ones — with `hamilton verify R-nnnn/ACn`.
  `hamilton verify` runs the whole suite when nothing else is left, and sends
  a failure back to coding with its failures and the criteria whose tests
  they name. After that fix, Hamilton re-runs only those criteria's tests; the
  suite runs again once they pass. The screen shows only a spinner and the
  outcome; the suite's own output goes to a log named as it starts —
  `tail -f` it in another terminal to watch.
- **Every run ends with where its time and tokens went:**
  `Time 31m12s · checking 9m40s (3×, 1 with the suite) · planning 1m05s ·
  writing tests 8m30s · reviewing 6m10s · coding 5m47s · waiting for you 20s`,
  then `Tokens 2.1M · writing tests 1.4M · coding 420k · …` — what each kind
  of work read fresh and wrote, subagents included. Your own time at a
  question is booked apart, and each gate check shows how long it took.
- **Re-running is the resume.** `.hamilton/build` keeps only what re-running
  could not work out for itself — what you chose to skip, how many rewrites
  each criterion has had, and what the reviewer said about the criteria whose
  tests have not passed yet. Everything else comes back out of `hamilton verify`.

Hamilton runs on Claude, through the Claude Agent SDK, which installs with it.
The model is reached behind a single adapter: the session driver, the phase
gate and the question flow know nothing about which model is answering, so
supporting another one is a new adapter rather than a rewrite. Today there is
one.

It is early: the session does not narrow the agent's tool surface beyond the
phase gate.

## Quick start

### 1. Check out the framework

Get this repository onto your machine (`git clone`, or you may already have it)
and `cd` into it. This checkout *is* the distribution — `hamilton-core` is not
published anywhere.

Requires Python 3.11+ and the Claude Agent SDK, which comes in as a dependency.

Run its own test suite before you trust a checkout, especially one you have
been editing:

```
$ python -m pytest -q          # expects 490 passing
```

### 2. Link it into a separate test project

`hamilton-core` is not on PyPI (the name `hamilton` there belongs to an
unrelated project), so you install it from the checkout. To exercise it on a
real project, install it from your working copy into that project's virtualenv
— exactly what a published release would look like, just pointed at a local
path.

```
$ cd /path/to/your-test-project
$ python -m venv .venv
$ . .venv/bin/activate

# editable install: your edits to the framework take effect with no reinstall
$ pip install -e /path/to/hamilton

#   — or — a fixed snapshot, exactly like installing a published release
$ pip install /path/to/hamilton

# the test project needs its own test runner in the same venv
$ pip install pytest

$ hamilton --help
```

`hamilton` is now on the venv's `PATH`. Keep the venv activated so that a
`test_command` of `python -m pytest -q` in `.hamilton/config` resolves to this
venv's pytest.

### 3. Scaffold the project

```
$ cd /path/to/your-test-project
$ git init -q
$ hamilton init
hamilton init: scaffolded /path/to/your-test-project

Run `hamilton design` to write the specification with an AI agent's help.
Prefer to write it by hand? Start from the files in spec/ -- each explains
what it should contain.
```

`init` writes `spec/` (`vision.md`, `actors.md`, `requirements.md` — each with
a fenced example to replace), `.hamilton/` (`phase`, `config`), `AGENTS.md`
with `CLAUDE.md` pointing at it, and `.claude/settings.json` (the phase-guard
hook). From then on they are your project's files. The workflows the agent
follows — the `hamilton` skill — are not copied in: each session loads them
from the installed Hamilton.

### 4. Specify, build, check

```
$ hamilton design      # spec phase: draft the vision and the requirements with the agent
$ hamilton build       # build phase: Hamilton drives — plan, write the tagged tests, review them, implement, until the gate is green
$ hamilton verify      # the verification gate — run it yourself, and in CI
```

`hamilton verify` exits `0` when every criterion has a passing, reviewed,
tagged test; `1` on any finding; `2` if it cannot run at all. It never writes
anything. Wire the same command into your pipeline — that CI run, outside the
agent, is the real gate.

### Following along without an agent

Every step `hamilton design` / `hamilton build` drive can be done by hand to
see the mechanism — except the review, which only `hamilton build` runs. With
the tests already written, that is all it has left to do:

```
$ printf spec  > .hamilton/phase      # (what `hamilton design` does)
#   ... edit spec/requirements.md and spec/actors.md ...
$ printf build > .hamilton/phase      # (what `hamilton build` does)
#   ... write tests/ with @covers tags, then the implementation ...
$ hamilton build       # a reviewer session per criterion; needs Claude credentials
$ hamilton verify
R-0001 Initials of a name
- ✓ AC1 `initials "ada lovelace"` -> prints "AL" [cli]
  * test_prints_the_initials (tests/cli/test_initials.py:4-7)
- ✓ AC2 runs of whitespace between words count as one separator [unit]
  * test_whitespace_runs (tests/unit/test_initials.py:1-2)

Suite ✓ passed (1s)
hamilton verify: 1 requirement(s), 2 acceptance criteria
hamilton verify: ok
```

Each criterion lists the tests that verify it: where each lies,
`file:first-last`, after its name as the test framework writes it — the
title of a `it('…')`, or the function's name. While the suite runs, the same working indicator as in `hamilton build`
shows how long it has been going.

The review suffixes the reviewer writes into the tags are the record that
each test was judged against its criterion. Commit them with the tests; the
merge-request diff shows every suffix next to the test change it certifies.

### Starting from an existing codebase

If the code already exists, run `hamilton reverse` instead of `hamilton design`
for the first spec session:

```
$ hamilton init
$ hamilton reverse     # spec phase: the agent surveys the code and its git
                       # history, confirms with you what the system is and who
                       # its actors are, then derives the requirement tree
                       # module by module — you ratify each piece
$ hamilton verify      # red on `uncovered` — expected; the derived spec has
                       # no tests bound to it yet
$ hamilton build       # binds your existing tests to the derived criteria
                       # (and writes AC-level tests where the unit tests are
                       # too fine-grained), has every tagged test reviewed,
                       # then gets the gate green
```

The derived spec is deliberately **thinner than the code** — it records intent
and the load-bearing decisions and leaves the rest to the implementing agent, so
`hamilton verify` still runs against the same one-tree model as a greenfield
project. `hamilton reverse` refuses once `spec/requirements.md` has real
requirements; extend an existing spec with `hamilton design`.

## Commands

| Command | Phase | What it does |
|---|---|---|
| `hamilton init [path]` | — | Scaffold a project: `spec/`, `.hamilton/`, `AGENTS.md` / `CLAUDE.md`, `.claude/`. Refuses if `.hamilton/` already exists. |
| `hamilton design` | sets **spec** | Write the phase, print the status banner, run a spec-phase session with a kickoff to draft the vision / requirements through the review protocol. Offers to resume an unfinished spec session. |
| `hamilton build` | sets **build** | Get the gate green, as a loop Hamilton drives rather than a session an agent drives: `hamilton verify` is the work list; a planner scaffolds each new surface as a contract; a writer per criterion writes its tests against it, in a file of their own (and may read the code); a reviewer that sees only the spec and the criterion's tests, judged together, lists what they cover and what is wrong; revisions are re-reviewed against that list only, until it is settled; then the implementation is written against the tests. Each step names itself and shows what is running. The one question it asks is a clarification: a criterion the spec cannot settle, answered by you and written into the spec on your approval. Without a terminal it reports and exits non-zero. |
| `hamilton reverse` | sets **spec** | Brownfield: like `hamilton design`, but the kickoff has the agent derive a first spec from the existing code and its git history, module by module. Refuses if `spec/requirements.md` already has requirements. |
| `hamilton verify [R-nnnn[/ACn]] [--no-suite] [--json] [--suite-output]` | ignores phase | The verification gate: run `test_command`, check every AC has a passing `@covers` test under the paths of its verification method and that every such tag carries a current review suffix, validate the requirement tree, list `manual` criteria. It reads as the spec: each requirement, then each criterion with its mark — `✓` fine, `✗` no usable test, `?` not reviewed, `○` verified by a person — and under it each test that verifies it, by name and `file:lines`, then whether the suite passed, then anything about no one criterion. The suite's own output is kept out of the way: it goes, as it runs, into a temp file that is named on a failure (and deleted when green); `--suite-output` streams it instead, for CI logs. `--json` carries every finding in full. `hamilton verify R-nnnn/ACn` (or `R-nnnn`, for all of a requirement's criteria) shows those criteria's status and runs only their tests, each method's files by its `run.<method>` command, instead of the suite — how an agent in `hamilton build` runs the tests it works on, without working out the project's runners itself. A failure is put down to the criterion whose file it names. `--no-suite` runs no tests at all — the spec, the tags and the reviews only, in a second — which is what a spec session checks. Writes nothing to the project. This is the gate — run it in CI. |
| `hamilton status` | read-only | Print the project snapshot a session shows as its banner: phase, requirement and coverage counts, and the last three `spec/` changes. |
| `hamilton show [ID] [--json]` | read-only | Without an id, the whole requirement tree with a dotted path computed at render time and a per-requirement coverage mark. On a terminal it is interactive: ↑/↓ move, ←/→ fold or unfold, Enter opens the selected requirement in full; without one it is printed. With an id, print one entity in full and what refers to it. `R-nnnn`: path by title, `Actor:`, statement, criteria with their method, coverage status and the file holding each `@covers` tag, child requirements. `A-nnnn`: description and the requirements that name it. |

`hamilton verify`, `hamilton show` and `hamilton status` write nothing.
`verify` and `show` take `--json` for tooling.

### When `hamilton verify` fails

| Rule | What it means / what to do |
|---|---|
| `no-test-command` | `.hamilton/config` has no `test_command` (or a blank one). Set it — e.g. `test_command=python -m pytest -q`. |
| `tests-failed` | `test_command` ran and did not exit 0. Run it yourself to see why, and fix the code or the test. |
| `retired-config` | `.hamilton/config` still sets `test_paths`. Split it into one `paths.<method>=<dirs>` line per verification method and delete it. |
| `no-method` | A criterion has no `[method]` marker. Add one naming a method from `## Verification methods` (in spec phase). |
| `unknown-method` | A marker names a method `## Verification methods` does not define. Fix the marker or define the method (in spec phase). |
| `no-method-paths` | A method is used but `.hamilton/config` has no `paths.<method>`. Set it to the directories holding that method's tests. |
| `uncovered` | A criterion's method has no `@covers R-nnnn/ACn` tag in any file under that method's paths — with several methods, each needs one. Add a test of that kind and tag it. `manual` criteria need no test. |
| `wrong-method` | A criterion is tagged, but only under another method's paths — the test proves it the wrong way. Write a test by the criterion's method, under its paths. |
| `orphan-tag` | A `@covers` tag names a requirement or criterion that `spec/requirements.md` does not declare. Fix the tag, or add the criterion (in spec phase). |
| `orphan-requirement` | A requirement with no `Parent:` does not name an `Actor:`. Add the `Actor:`, or give it a `Parent:`. |
| `dangling-ref` | A `Parent:` or `Actor:` value names an entity that isn't declared. Fix the reference, or add the entity. |
| `cyclic-parent` | Following `Parent:` links from some requirement loops back on itself. Re-point one `Parent:`. |
| `unreviewed` | A counting tag has no review suffix, or its criterion (with its `Statement:`, method definition and the spec files it references) or its test changed since the review — the message says which. Run `hamilton build`: it has the test reviewed — against the criterion's current wording, if that changed — and rewritten only if the reviewer rejects it. Never write a suffix by hand. |
| `copied-suffix` | Two `@covers` tags carry the same review suffix. A suffix is written to one test, so the other is a copy — a duplicated test file, a debug extract. Delete the copy, or its tag. |
| `missing-reference` | A `Statement:` or criterion names a `spec/<file>` that does not exist. Add the file or correct the path (in spec phase). |
| `malformed` | A requirement is missing its `Statement`, has no criteria, repeats an id, or has a line that doesn't parse — or the file has no real requirements at all. The message names the line. |

It also prints **advisory warnings** — never fail the run, never change the
exit code: `long-statement` (a `Statement:` over 20 words — it is several
requirements welded together), `long-description` (an actor `Description:` over
one sentence), `root-unit-only` (a root requirement whose criteria are all
`unit`, so nothing verifies the actor's goal end to end). And a **notice** if `.hamilton/config` sets `mutation_command`: that
key is reserved for future mutation testing and is not implemented — unset it.

## The spec files

`hamilton init` writes three files under `spec/`. Each opens with a fenced
example that `hamilton verify` ignores; you replace it with your own content
below the fence. `spec/` is writable in spec phase, read-only in build.

### `spec/vision.md`

Prose, not a model — `hamilton verify` never reads it. Three sections:
**Purpose** (one or two sentences on what the software is for and for whom),
**Users** (who uses it and what they need), **Non-goals** (plausible features
that are deliberately out of scope, each with why). The non-goals are the
load-bearing part: they are what lets a reviewer, and a future agent, tell a
requested change from an unrequested feature. On a brand-new project
`hamilton design` offers to draft this with you first.

### `spec/actors.md`

A flat list of the external entities that interact with the system — they
define its boundary. One `## A-nnnn` block each, with a `Name:` and a
one-sentence `Description:`.

```markdown
## A-0001
Name: CLI user
Description: Runs the initials tool from the command line.
```

### `spec/requirements.md`

The whole model: one `Parent:` tree.

- A requirement with **no `Parent:`** is a **root** — a system-level goal — and
  must name the `Actor:` whose goal it is. Ratify the root layer first;
  specification is top-down.
- Every other requirement names its `Parent:`.

Every requirement is a `## R-nnnn Short title` block with a one-sentence
`Statement:` (under 20 words, one behaviour — detail belongs in the criteria)
and at least one `- ACn:` line, written as `<observable condition> -> <expected
outcome> [method]` where that shape fits.

The file opens with a `## Verification methods` section that defines, once,
how this project proves a criterion: what a test observes, what is real and
what is stubbed. Every criterion ends in a marker naming one of them. The agent
proposes a method per criterion; you ratify it like the criterion itself.
`manual` is reserved — a person judges it, and `hamilton verify` lists it
without enforcing it.

```markdown
## Verification methods
- **cli** — the installed command run as a subprocess; nothing stubbed.
- **unit** — one function in isolation, no I/O.

## R-0001 Initials of a name
Actor: A-0001
Statement: initials(name) returns the capitalised first letter of each whitespace-separated word.
Criteria:
- AC1: `initials "ada lovelace"` -> prints "AL" [cli]
- AC2: runs of whitespace between words count as one separator [unit]
```

Where each method's tests live is a build-phase choice, one line per method in
`.hamilton/config`:

```
test_command=python -m pytest -q
paths.cli=tests/cli
paths.unit=tests/unit
```

A tag counts only under the paths of the criterion's method. A test that covers
one of its criteria:

```python
# tests/unit/test_initials.py
def test_whitespace_runs():    # @covers R-0001/AC2
    assert initials("ada   lovelace") == "AL"
```

A shared field rule — a format, an enum, a validation rule — goes once in a
`## Domain vocabulary` section at the top of the file and is referenced by name
from the criteria that need it, never restated inside a `Statement:`.

### Supporting files

Content too long or too literal for a criterion — a pricing model with its
constants, a legal text, the site's copy, a visual reference — goes in a file
of its own in `spec/`, and the `Statement:` or criterion that incorporates it
names it by path:

```markdown
Statement: The completed configuration is priced per spec/price_model.md.
Criteria:
- AC1: any valid configuration -> the total follows the calculation path in spec/price_model.md [unit]
```

The criterion still says what must hold; the file only supplies the content.
That content is part of the criterion: the reviewer is shown it in full, and
editing the file sends every test of a referencing criterion back to review
— and to a rewrite where it no longer holds — so the next `hamilton build`
builds the change. A path to a file
that does not exist fails `missing-reference`. Like the rest of `spec/`, these
files are written in spec phase — `hamilton design` treats an edit to one as a
change of its own and names the requirements it affects.

## What this does not do

It has every test **reviewed** — it does not **prove** your tests are any
good. The reviewer is a model judging each criterion's tests against five checks (every
clause asserted, the right starting point, can fail, expectations from the spec
not the code, the whole set where the criterion says "every"); it can be wrong,
and nothing yet shows mechanically that a test would catch a regression.
Mutation testing is the planned complement.

Related limits, stated plainly:

- **Code in other files is outside a review.** A test's review covers its own
  section and the top of its file. A shared helper in another file can change
  without sending any test back to review.
- **A review suffix can be forged from a shell.** The guard blocks the agent's
  file-editing tools from adding or changing one, not a shell write. Every
  suffix change shows in the merge-request diff.
- **The phase hook is defeatable from a shell.** It covers file-editing tools
  only, so it stops drift inside a cooperating session, not deliberate
  circumvention. CI — `hamilton verify` run outside the agent — is the real gate.
- **Only requirements and criteria can *fail* the gate.** `spec/actors.md` is
  read for the view commands, for `dangling-ref` on an `Actor:` link, and for
  `long-description` warnings, but nothing in it turns a build red by itself.
- **IDs are hand-written.** A repeated `R-nnnn` is caught by `hamilton verify`
  (`malformed`), not prevented as you type.
- **Hamilton runs on Claude, and only Claude.** The model sits behind one
  adapter and nothing above it is Claude-specific, so a second model is an
  adapter away — but that adapter does not exist yet.
- **The agent's tool surface is not narrowed.** The phase gate governs what can
  be written, not what can be run or read.

## Upgrading Hamilton

Install the newer Hamilton; there is nothing to run in the project. Every
session loads the workflows from the installed package, so the next `hamilton
design` or `hamilton build` uses them. What `hamilton init` wrote —
`AGENTS.md`, `CLAUDE.md`, `.claude/settings.json` — is yours and stays as it
is.

**A project created before the workflows moved into the package** still has
`.claude/skills/hamilton/SKILL.md` (and perhaps `.claude/commands/spec.md` and
`build.md`). Nothing reads them any more; `hamilton verify` prints a notice
until you delete them.

**A project created before verification methods** goes red after upgrading,
on purpose: `retired-config` for its `test_paths` and `no-method` for every
criterion. There is no automatic migration. In a `hamilton design` session, add
the `## Verification methods` section and a marker to each criterion; then, in
`hamilton build`, the agent replaces `test_paths` with `paths.<method>` keys
and moves or writes tests to match. Any `Interface:` lines are now ignored —
delete them at your leisure.

**A project created before test review** goes red after upgrading, on
purpose: every tagged test is `unreviewed`. There is no automatic migration.
The first `hamilton build` runs a full review — one model call per test — and
rewrites the tests the reviewer rejects; that review is the quality audit of
your existing suite. `.hamilton/verified` is no longer used: `hamilton verify`
prints a notice until you delete it.

**A project whose criteria name supporting files** (`spec/<file>`) sees
those criteria go `unreviewed` once after upgrading: the files are now part of
what their tests were reviewed against. The next `hamilton build` reviews
them again, and rewrites only those the reviewer rejects. Criteria that reference no file are unaffected. A file named
without its `spec/` prefix is not a reference — add the prefix in a `hamilton
design` session.

**A project created before sessions moved in-process** still has an
`agent_command` line in `.hamilton/config`. Nothing reads it any more —
delete it at your leisure.

## The rationale

The design rationale is in `docs/` — `concept.md` (the V-model / MBSE slice)
and `data-model.md` (the spec format in full). You do not need either to use
the tool.
