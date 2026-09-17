# Hamilton

> Forget vibe coding, start engineering.

Hamilton is a small framework for building software **with** an AI agent
without taking its word for when the work is done. You write down what the
software must do — as specific, testable acceptance criteria — *before* it is
built. The agent then implements it and writes the tests. One command,
`hamilton check`, verifies that every criterion has a test that names it, that
the suite passes, and that no criterion has been quietly reworded since it last
passed. Specification, code and tests live in one repository and land in one
review.

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
   criteria — observable conditions and their expected outcomes.
2. **The agent implements.** It writes the code and the tests. Each test
   carries a one-line comment naming the single criterion it covers —
   `@covers R-0001/AC1`.
3. **`hamilton check` verifies.** It runs your suite and stays red until every
   criterion has a passing tagged test. Reword a criterion and it goes red
   again until the test and code are reconciled with the new wording.

The acceptance criteria are yours. The rigorous tests that bind to them are the
agent's. `hamilton check` is what keeps the two honest.

## The two phases

Work happens in one of two phases, and the point of each is what it stops you
(or the agent) from doing.

**Spec phase** — you decide what the software should do. The agent can edit
`spec/` and nothing else. It cannot start writing code against a target you
have not finished setting.

**Build phase** — the agent writes the code and the tests. It cannot edit
`spec/`, `.claude/`, or `AGENTS.md`, and of `.hamilton/` only `config` — and
there only `test_command` / `test_paths`, because picking the test framework
and layout is a build-time call. It cannot quietly change a requirement to
match what it built, or rewrite its own rules.

You start a session in a phase with **`hamilton design`** (spec) or
**`hamilton build`** (build). Each writes the phase to `.hamilton/phase`,
prints a status banner, then runs the session with a one-line kickoff so it
starts working straight away. The phase is fixed for that whole session: it
exports `HAMILTON_SESSION`, and `design` / `build` refuse to run when that is
already set, so an agent can't relaunch itself into the other phase. Writes to
read-only paths are refused during the session as fast feedback.

None of this is unbypassable — unset the variable, edit the phase file by hand,
or run an agent directly and you are outside it. It stops drift, not a
determined operator. `hamilton check` run in CI, which ignores the phase
entirely, is the gate that enforces the outcome for real.

### How a session runs

Hamilton drives the session turn by turn rather than handing you an agent
terminal. Three things follow from Hamilton being able to see what is
happening:

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
  gets out of the way whenever it needs to show you something or ask.
- **Finishing a piece of work is not the end of the session.** When the agent
  gives its closing summary, Hamilton shows you the next step — another change,
  a decomposition, a completeness pass, or your own instruction — and the work
  carries on in the same conversation, so nothing already read or ratified is
  thrown away. *Finish this session* is on that menu, and on every question the
  agent asks, so a session opened by mistake can be left at the first prompt.
- **An interrupted session can be resumed.** Every turn writes
  `.hamilton/session`; the next launch in the same phase offers to pick up
  where you left off — which is what you want when a session dies mid-spec.

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
$ python -m pytest -q          # expects 266 passing
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
with `CLAUDE.md` pointing at it, and `.claude/` (the phase-guard hook settings
and the `hamilton` skill). `AGENTS.md` and `.claude/` belong to the framework
and stay as written.

### 4. Specify, build, check

```
$ hamilton design      # spec phase: draft the vision and the requirements with the agent
$ hamilton build       # build phase: the agent writes code + tagged tests, gets the gate green
$ hamilton check       # the verification gate — run it yourself, and in CI
```

`hamilton check` exits `0` when every criterion has a passing tagged test and
nothing is stale; `1` on any finding; `2` if it cannot run at all. Wire the
same command into your pipeline — that CI run, outside the agent, is the real
gate.

### Following along without an agent

Every step `hamilton design` / `hamilton build` drive can be done by hand to
see the mechanism:

```
$ printf spec  > .hamilton/phase      # (what `hamilton design` does)
#   ... edit spec/requirements.md and spec/actors.md ...
$ printf build > .hamilton/phase      # (what `hamilton build` does)
#   ... write the implementation and tests/ with @covers tags ...
$ hamilton check
hamilton check: running test_command: python -m pytest -q
2 passed in 0.01s
hamilton check: 1 requirement(s), 2 acceptance criteria
hamilton check: ok
```

A green run writes `.hamilton/verified` — the hash of each criterion's text at
that passing run. Commit it; that is what a later `stale` finding compares
against.

### Starting from an existing codebase

If the code already exists, run `hamilton reverse` instead of `hamilton design`
for the first spec session:

```
$ hamilton init
$ hamilton reverse     # spec phase: the agent surveys the code and its git
                       # history, confirms with you what the system is and who
                       # its actors are, then derives the requirement tree
                       # module by module — you ratify each piece
$ hamilton check       # red on `uncovered` — expected; the derived spec has
                       # no tests bound to it yet
$ hamilton build       # the agent binds your existing tests to the derived
                       # criteria (and writes AC-level tests where the unit
                       # tests are too fine-grained), then gets the gate green
```

The derived spec is deliberately **thinner than the code** — it records intent
and the load-bearing decisions and leaves the rest to the implementing agent, so
`hamilton check` still runs against the same one-tree model as a greenfield
project. `hamilton reverse` refuses once `spec/requirements.md` has real
requirements; extend an existing spec with `hamilton design`.

## Commands

| Command | Phase | What it does |
|---|---|---|
| `hamilton init [path]` | — | Scaffold a project: `spec/`, `.hamilton/`, `AGENTS.md` / `CLAUDE.md`, `.claude/`. Refuses if `.hamilton/` already exists. |
| `hamilton design` | sets **spec** | Write the phase, print the status banner, run a spec-phase session with a kickoff to draft the vision / requirements through the review protocol. Offers to resume an unfinished spec session. |
| `hamilton build` | sets **build** | Write the phase, print the banner, run a build-phase session with a kickoff to propagate the latest spec change and get `hamilton check` green. Offers to resume an unfinished build session. |
| `hamilton reverse` | sets **spec** | Brownfield: like `hamilton design`, but the kickoff has the agent derive a first spec from the existing code and its git history, module by module. Refuses if `spec/requirements.md` already has requirements. |
| `hamilton check [--json]` | ignores phase | The verification gate: run `test_command`, check every AC has a passing `@covers` test under `test_paths`, flag reworded criteria as `stale`, validate the requirement tree. Writes `.hamilton/verified` on a clean run. This is the gate — run it in CI. |
| `hamilton status` | read-only | Print the project snapshot a session shows as its banner: phase, requirement and coverage counts, and the last three `spec/` changes. |
| `hamilton tree [--json]` | read-only | Print the whole requirement tree with a dotted path computed at render time, an `i` / `!` marker for whether each interior node carries an `Interface:` yet, and a per-requirement coverage mark. |
| `hamilton show <ID> [--json]` | read-only | Print one entity in full and what refers to it. `R-nnnn`: path by title, statement, criteria with coverage status and the file holding each `@covers` tag, `Interface:` / `Actor:`, child requirements. `A-nnnn`: description and the requirements that name it. |
| `hamilton upgrade [path]` | — | Bring the framework-managed files up to date after installing a newer Hamilton — `AGENTS.md`, `CLAUDE.md`, the `.claude/` tree. Prints a diff, then overwrites. Never touches `spec/`, `.hamilton/phase`, `.hamilton/config`, `.hamilton/verified`. |
| `hamilton guard` | — | Internal: the `PreToolUse` hook backend that blocks read-only-path edits during a session. Not run by hand. |

`hamilton tree`, `hamilton show` and `hamilton status` write nothing and never
touch the gate. All three, plus `hamilton check`, take `--json` for tooling.

### When `hamilton check` fails

| Rule | What it means / what to do |
|---|---|
| `no-test-command` | `.hamilton/config` has no `test_command` (or a blank one). Set it — e.g. `test_command=python -m pytest -q`. |
| `tests-failed` | `test_command` ran and did not exit 0. Run it yourself to see why, and fix the code or the test. |
| `uncovered` | An acceptance criterion has no `@covers R-nnnn/ACn` tag in any file under `test_paths`. Add the tag to the test that checks that criterion. |
| `orphan-tag` | A `@covers` tag names a requirement or criterion that `spec/requirements.md` does not declare. Fix the tag, or add the criterion (in spec phase). |
| `orphan-requirement` | A requirement with no `Parent:` does not name an `Actor:`. Add the `Actor:`, or give it a `Parent:`. |
| `dangling-ref` | A `Parent:` or `Actor:` value names an entity that isn't declared. Fix the reference, or add the entity. |
| `cyclic-parent` | Following `Parent:` links from some requirement loops back on itself. Re-point one `Parent:`. |
| `stale` | A criterion was reworded since the last green check. Re-read it, confirm the tagged test still fits, and run `hamilton check` again — it clears once the run is otherwise clean. |
| `malformed` | A requirement is missing its `Statement`, has no criteria, repeats an id, or has a line that doesn't parse — or the file has no real requirements at all. The message names the line. |

It also prints **advisory warnings** — never fail the run, never change the
exit code: `long-statement` (a `Statement:` over 20 words — it is several
requirements welded together), `long-description` (an actor `Description:` over
one sentence), `no-interface` (an interior requirement with no `Interface:`
line yet). And a **notice** if `.hamilton/config` sets `mutation_command`: that
key is reserved for future mutation testing and is not implemented — unset it.

## The spec files

`hamilton init` writes three files under `spec/`. Each opens with a fenced
example that `hamilton check` ignores; you replace it with your own content
below the fence. `spec/` is writable in spec phase, read-only in build.

### `spec/vision.md`

Prose, not a model — `hamilton check` never reads it. Three sections:
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
- A requirement **with children** is a subsystem boundary and carries an
  `Interface:` line — one sentence naming what crosses that boundary. The
  interior of the tree *is* the architecture.
- A **leaf** needs neither: just a `Statement:` and criteria.

Every requirement is a `## R-nnnn Short title` block with a one-sentence
`Statement:` (under 20 words, one behaviour — detail belongs in the criteria)
and at least one `- ACn:` line, written as `<observable condition> -> <expected
outcome>` where that shape fits.

```markdown
## R-0001 Initials of a name
Actor: A-0001
Statement: initials(name) returns the capitalised first letter of each whitespace-separated word.
Criteria:
- AC1: "ada lovelace" -> "AL"
- AC2: runs of whitespace between words count as one separator
```

A test that covers one of its criteria:

```python
def test_two_words():          # @covers R-0001/AC1
    assert initials("ada lovelace") == "AL"
```

A shared field rule — a format, an enum, a validation rule — goes once in a
`## Domain vocabulary` section at the top of the file and is referenced by name
from the criteria that need it, never restated inside a `Statement:`.

## What this does not do

It runs your tests and checks that each criterion has one — it does **not**
check that your tests are any good. A test that asserts nothing, or the wrong
thing, passes the gate as long as it runs. `hamilton check` proves your
criteria are written down, tied to tests, and passing; it does not prove those
tests would catch a regression.

Related limits, stated plainly:

- **`stale` clears by re-running.** It proves the suite was re-executed against
  the reworded criterion, not that a person reconciled the test with it.
- **The phase hook is defeatable from a shell.** It covers file-editing tools
  only, so it stops drift inside a cooperating session, not deliberate
  circumvention. CI — `hamilton check` run outside the agent — is the real gate.
- **Only requirements and criteria can *fail* the gate.** `spec/actors.md` is
  read for the view commands, for `dangling-ref` on an `Actor:` link, and for
  `long-description` warnings, but nothing in it turns a build red by itself.
- **IDs are hand-written.** A repeated `R-nnnn` is caught by `hamilton check`
  (`malformed`), not prevented as you type.
- **Hamilton runs on Claude, and only Claude.** The model sits behind one
  adapter and nothing above it is Claude-specific, so a second model is an
  adapter away — but that adapter does not exist yet.
- **The agent's tool surface is not narrowed.** The phase gate governs what can
  be written, not what can be run or read.

## Upgrading the scaffold

After installing a newer Hamilton, `hamilton upgrade` brings the
framework-managed files up to date — `AGENTS.md`, `CLAUDE.md` (a pointer at
it), and the `.claude/` tree (the phase-guard hook settings and the `hamilton`
skill). It never touches your own work: `spec/` (including `spec/vision.md`),
`.hamilton/phase`, `.hamilton/config`, `.hamilton/verified`.

```
$ hamilton upgrade
```

It prints a diff of everything it changes, then overwrites those files with the
current templates, recreates any you deleted, and deletes any it has since
retired. They belong to the framework, so it does not ask before replacing them
— keep local changes out of them.

**A project created before sessions moved in-process** still has an
`agent_command` line in `.hamilton/config` (that file is yours, so `upgrade`
won't touch it). Nothing reads it any more — delete it at your leisure.

## The rationale

The design rationale is in `docs/` — `concept.md` (the V-model / MBSE slice)
and `data-model.md` (the spec format in full). You do not need either to use
the tool.
