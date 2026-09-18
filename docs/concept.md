# Model-Based V-Process for Agent-Based Coding

---

## 1. Purpose

Two goals, in order of importance:

1. **Reliability of output.** Verification tied to explicit, pre-stated intent, so that "done" is a machine-checkable property rather than an agent's self-assessment.
2. **Guidance of construction.** A structured model that bounds each agent task, survives context loss, and tells the agent where changes belong.

Non-goal: replacing human architectural judgment. The process concentrates human effort on decomposition and intent, and delegates realization.

---

## 2. Two complementary frameworks

| | Governs | Contributes |
|---|---|---|
| **MBSE** | Representation: what the design artifacts are and how they relate | Recursive decomposition |
| **V-Model** | Timing and level: when verification intent is fixed, at what granularity | R↔AC pairing, verification levels, traceability |

They are near-orthogonal. MBSE is weak on verification discipline; the V-model is weak on how decomposition is actually performed. Each covers the other's gap.

Hamilton takes a **scale-appropriate** slice of MBSE (D-014): recursive
decomposition, but not the logical/physical model split. At the
target scale — single engineer to small team — a separate component model and a
separate module model maintained in parallel with the requirements cost more
attention than they return. The requirement tree carries the decomposition; the
code is the physical model.

---

## 3. The model — one tree

The model is a single tree: `spec/requirements.md` (D-014). There is no separate
component file and no separate module file.

Recursive decomposition of the system from the outside in:

- **Root:** a system-level goal, as seen by an actor. It names the `Actor:`
  whose goal it is.
- **Interior nodes:** the goal broken into smaller requirements, each of which
  its children add up to.
- **Leaves:** behaviour small enough to implement in a single agent session,
  with at least one externally observable acceptance criterion.

Depth is **variable**. Three levels is the common case, not a rule. A trivial
feature may be root → leaf.

Every requirement — root, interior or leaf — carries a `Statement` and
acceptance criteria, and every criterion names how it is verified (§4.4, §5.1).
The tree holds requirements and criteria only; the architecture lives in the
code (§3.1).

### 3.1 The physical model is the code

The real modules/packages/files are the physical model; there is no parallel
map from a logical component to a path. An agent locates code the ordinary way
(search, reading the code). A separate
physical model is deferred until a system large enough to need one.

### 3.2 The refactoring rule

> Changing the code without changing the requirement tree is **refactoring**: no
> requirement is touched, and all existing tests must pass unchanged.
>
> Changing behaviour requires changing the **tree first**.

This is the primary defense against spec/code drift.

---

## 4. Requirements and acceptance criteria

### 4.1 The R↔AC pair

Every requirement **R** is paired with one or more acceptance criteria **AC**. This pairing is the core of the concept.

- **R** states *what shall be true*. Authored during decomposition.
- **AC** states *how we will know*. Authored **with R, before any implementation**.
- **Test code** is written **after** implementation, against the now-known API, and traces to a specific AC.

### 4.2 Why this resolves the TDD tension

The V-model does not require test *code* before implementation. It requires *verification intent* to be fixed before implementation. Separating AC from test code gives:

- the real V-model benefit: the target is defined by someone not yet anchored by the implementation
- practical feasibility: no need to write assertions against an API that does not exist yet
- an independent oracle for the test author, which is the single biggest lever on agent-written test quality

### 4.3 Diagnostic signal

Inability to state an AC before implementation is **a symptom, not an accepted limitation**:

- at leaf/unit level, it is sometimes legitimate (edge cases genuinely emerge from the code)
- at integration or system level, it means **the requirement above is underspecified**

Treat the second case as a defect in the tree and fix it there.

### 4.4 Requirement format

The implemented format (`data-model.md` §2.2, §4.1) is authoritative:

```
## Verification methods
- **<name>** — <what is real, what is stubbed>

## R-nnnn <Short title>
Parent:    <R-nnnn>        # omit only on a root requirement
Actor:     <A-nnnn>        # required on a root, meaningless elsewhere
Statement: <one sentence, one behaviour, < 20 words>
Criteria:
- AC1: <observable condition> -> <expected outcome> [<method>]
- AC2: ...
```

**Every AC records its verification method** (D-019), as a trailing marker
naming a method defined once, at the top of the file. This reverses an earlier
rule that the level was derived from tree position and recorded nowhere: in
practice nothing enforced the derived level, so an agent picked the cheapest
test everywhere and the gate went green over a broken product. The method is
spec, ratified with the AC; the tool that runs it is not (§5.1).

No `Status` field — the never-reuse invariant is carried by the id counter, not
a status lifecycle (`data-model.md` §3), and there are no tombstones.
`Component:` (D-014) and `Interface:` (D-019) are **retired fields**: a file
that still carries them parses, the lines are ignored.

The `Parent` tree is the whole model: requirements and their criteria. Roots
are actor-level goals and must name an `Actor:`; everything else names a
`Parent:` (D-007, D-014, `data-model.md` §2.2).

### 4.5 Granularity heuristic

A leaf requirement is correctly sized when:
- an agent can implement it in a single session, and
- it has at least one externally observable acceptance criterion.

If writing the requirement takes longer than writing the code it describes, the granularity is wrong.

---

## 5. Verification

### 5.1 Levels are chosen, not derived

Each project defines its **verification methods** once, in `## Verification
methods` at the top of `spec/requirements.md`, and every AC names the method
that proves it (D-019). A method's definition says what a test observes, what is
real and what is stubbed — for example:

| Method | Observes | Stubbed |
|---|---|---|
| `browser` | the running product in a real browser | external services |
| `http` | requests to the running backend, and the calls external services received | external services |
| `unit` | one module in isolation | all I/O |
| `manual` | judged by a person — reserved, never enforced | — |

The agent proposes a method for every AC by what the AC observes, never by what
is cheapest to test; the engineer refines it. Tree position is a hint, not the
rule: a root's criteria are usually observed through the actor's channel, a
leaf's are often `unit`, but a leaf whose outcome the actor sees is verified
where the actor sees it.

`hamilton check` enforces the choice: a test counts for an AC only under the
directories configured for its method (`paths.<method>`, §5.6). A `manual` AC
needs no test and is listed as not machine-verified. Changing a method changes
what the AC's tests owe, so they go `unreviewed` like a reworded AC's (§5.3).

### 5.2 Test authoring: fresh subagent

Tests are written by a **fresh agent session** that receives:
- the requirement and its acceptance criteria
- the definition of each AC's verification method
- for `unit`, the public signature of the unit under test; for any other method,
  a running instance of the system

and **not** the implementation body.

Rationale: an agent that just wrote the implementation derives expected behavior from the code, including its bugs. The result is tautological tests that pass forever and catch nothing.

This rule is an instruction: the subagent shares the repository. Every test it
writes is then judged by a reviewer whose inputs *are* enforced (§5.3).

### 5.3 Test quality is reviewed, not proven

Whether a test proves its AC is a judgement. Left to whoever wrote the tag, it
is made once and never recorded or revisited. Hamilton instead has it made by a
**reviewer it runs itself**, records it per test, and invalidates it when
either side changes (D-020).

**The reviewer.** `hamilton review` hands each unreviewed test to a fresh agent
session with no tools. Hamilton builds the prompt: the requirement's id, title
and Statement, the AC with its method marker, the definition of that method,
and the test's text with its file path. No implementation, no file access.
Unlike the writer rule (§5.2), this boundary is enforced — the session has
nothing to read with. It judges each AC against five checks:

1. **Clause coverage** — every clause of the expected outcome has an assertion.
2. **Starting point** — the test starts from the AC's condition, not from a
   lower layer's already-prepared inputs.
3. **Can fail** — the assertion would fail if the behaviour were missing: no
   tautologies, no assertions so loose that anything passes.
4. **Independent expectation** — expected values come from the spec or the AC,
   not from the implementation's own constants or files.
5. **Scope** — "any" / "every" in the AC means the test covers the set, not a
   sample.

It also checks that the test verifies by the declared method. It answers
**pass**, **reject** with reasons (the test is rewritten, §7.2), or
**unclear**: the AC's own text cannot settle whether a test proves it. That is
an ambiguous AC — a spec defect, and a hard stop (§7.4).

**The record.** A pass writes a review suffix into the tag, `@covers
R-0005/AC2 #3f9a2c.81d0e4`: two 6-hex-digit SHA-256 prefixes. The first covers
the **obligation** — the AC id, its requirement's Statement, the AC text with
its marker, and the definitions of the methods the test counts toward. The
second covers the **test** — its *region*: the file's preamble (everything
above the first tag) plus its own section (from its tags to the next tagged
test or the end of the file), whitespace-normalised. Change either and the tag
is `unreviewed`; `hamilton check` fails and says which half changed, because
an AC change means the test probably needs rewriting and a test change only
needs another review. Nothing else writes a suffix — in particular not a green
`hamilton check`, which writes nothing — so a review cannot clear itself.

**Why this is not the removed falsification record.** An earlier draft put a
hand-run falsification protocol here — mutate the implementation, confirm the
named test goes red, record the mutant. It was removed: an adversarial pass
showed a fabricated record cost almost nothing to produce and satisfied the
checker completely. A suffix is just as cheap to compute. Two things differ:
the judgement is made by a Hamilton-run agent with restricted inputs, not
self-reported by the agent that wrote the code; and forging a suffix is a
deliberate bypass of an enforced rule — the write gate refuses any file edit
that introduces or changes one (§7.1), and every suffix change shows in the
merge-request diff next to the test change it certifies — not a cheap way to
satisfy the checker. A shell write gets around the gate, as it does every
guard rule; CI and the diff are the backstop (§8.1).

**Known blind spot.** Code in another file is in no region. A weakened shared
helper — a `support.js` every browser test uses — invalidates no review, and
the reviewer judges such helpers by their names alone.

**Still not proven.** A review is a judgement, not a proof: nothing yet shows
mechanically that a test can fail. The planned complement is per-language
mutation testing driven by a `mutation_command` in `.hamilton/config` — empty
by default; when set, surviving mutants fail the gate. Per-language by nature
(`mutmut`, `Stryker`, `cargo-mutants`), so it is configuration rather than a
built-in. Not yet implemented: a `mutation_command` that is set today prints a
`hamilton check` notice every run and is not executed — config that looks
active and does nothing is exactly the failure the falsification record had.

### 5.4 Coverage policy

**Gate — AC coverage, enforced.**
- every AC maps to ≥1 test that names it (`@covers R-nnnn/ACn`) under the
  paths of each of its methods; `manual` ACs are listed instead
- every such tag carries a current review suffix (§5.3)
- `hamilton check` runs `test_command` and requires exit 0

Both are checkable at commit time — coverage from the `@covers` tags, the pass
from actually running the suite. Neither needs the API to have been known when
the AC was authored.

**Diagnostic — code coverage, no percentage target.**
The value is in reading *uncovered* regions, not the number. Every uncovered region is triaged into exactly one of:
- a missing acceptance criterion → spec hole, add the AC
- code nobody asked for → delete it

The second bucket is the point: it is the mechanical check against agent gold-plating. A percentage target would be counterproductive, pressuring tests for code that should be removed instead.

**Cap:** a bounded number of tests per requirement, forcing prioritization over assertion-count padding.

### 5.5 System verification is the root's own criteria

A root requirement carries acceptance criteria like any other, and it is
verified **through them**: at least one of them names an actor-facing method,
so a test exercises the running product the way the actor reaches it. Green
children do not verify their parent — the parts can each pass while the product
that wires them together is broken. `hamilton check` warns (`root-unit-only`)
on a root whose criteria are all `unit`.

There is **no separate acceptance mechanism** — no `accept` command, no stored
sign-off (a recorded self-report is fakeable the way the falsification ledger
was, §5.3). The final judgement is the user's, and it is not recorded.

Rejection at the system level is always a defect *upstream of the code*:

1. a criterion is missing — the spec did not say it;
2. a criterion is wrong — the spec said the wrong thing;
3. a criterion is untested in practice — a vacuous test passed it, and its
   review (§5.3).

Fix the spec or the test first; patching the code alone fixes the symptom and
guarantees recurrence.

**Known limitation.** A green check proves the specification is satisfied, not
that it is complete. A missing requirement produces no signal — absence is
invisible — and emergent properties (latency, concurrency, cross-boundary error
propagation) are covered only where a criterion names them. The cheap
mitigation is a completeness read-through: render `hamilton tree` after the
root layer is ratified and read it upward, asking of each parent: *do these
children add up to this?* That reading checks the spec; it does not verify the
product.

### 5.6 Test tiers and the local gate

Each method's tests live under its own directories, `paths.<method>` in
`.hamilton/config` — e.g. `paths.unit=tests/unit`, `paths.browser=tests/browser`.
Which tool runs them and where they live are build-phase choices. `test_command`
runs the fast tests. Heavier ones (a `browser` suite, say) may live under their
method's paths but outside `test_command`. Consequence: their `@covers` tags
satisfy the coverage gate, but the tests themselves run only in CI (§8). The
local gate stays fast enough to run on every change, which is the only way it
keeps being run.

---

## 6. Ownership

| Artifact | Owner | Agent role |
|---|---|---|
| `spec/vision.md` — purpose, users, non-goals | Human | Proposes; human ratifies |
| Initial requirements | User | — (greenfield); under `hamilton reverse`, agent proposes from the existing code, human ratifies (D-018) |
| The requirement tree — shape, statements, AC | Human | Proposes; human ratifies |
| Verification methods — their definitions and each AC's method | Human | Proposes; human ratifies |
| Internal file layout, data structures, algorithms | Claude Code | Owns |
| Framework and component-library choice — one-way doors | Human | Proposes; agent may advise |
| Utility-library and test-framework choice — two-way doors | Claude Code | Owns |
| Test implementation | Claude Code (fresh session, per 5.2) | Owns |
| Test review | Hamilton (reviewer session, per 5.3) | Rewrites what the reviewer rejects |

A technology choice is an architecture decision — record it in a decision note,
not as a requirement: no observable acceptance criterion can be written for "use Angular"
(D-008). The exception is an externally *mandated* technology: that is a genuine
constraint requirement, with an ugly-but-honest AC over the dependency manifest.

### 6.1 Propose–ratify pattern

Claude proposes the decomposition, the criteria and their methods; the human
reviews and ratifies. Generation is expensive, ratification is cheap. Applies uniformly to
every part of the tree.

---

## 7. Workflow

Scope: single engineer to small team. Specification lives **in the same repository as the code**. Separate spec/code repositories are an enterprise concern and out of scope.

**One merge request contains the specification change, the implementation and the tests.**

### 7.1 Ratification collapses into a phase gate

At this scale there is no separate spec MR, and the engineer is the spec author — self-approval would be ceremony without a check. Ratification therefore moves from *merge time* to the **Step 1 → Step 2 boundary**, and the boundary is the **scope of an agent session**:

| Phase | Writable | Read-only |
|---|---|---|
| Step 1 — Specification (`spec`) | `spec/` | source, tests, `.hamilton/`, `.claude/`, `AGENTS.md` |
| Step 2 — Implementation (`build`) | source, tests, `.hamilton/config` | `spec/`, the rest of `.hamilton/`, `.claude/`, `AGENTS.md` / `CLAUDE.md` |

`.hamilton/`, `.claude/` and the agent-instruction files are read-only in
`build` because they hold the tool's state, its hook/skill config, and the
rules the agent is meant to follow — an agent must not relax what constrains it
(Hamilton writes `.hamilton/phase` itself, as a subprocess, not through a
hooked tool). The one exception is `.hamilton/config`: which test
framework runs and where each method's tests live (`test_command`,
`paths.<method>`) are build-time decisions, so the file is writable in `build`. A path hook cannot
lock individual lines, so the whole file is writable there — and visible in the
config diff a reviewer sees.

There are **two enforcement layers**. They are not one uniform guarantee.

**1. The session launch.** The engineer runs `hamilton design` (Step 1) or
`hamilton build` (Step 2); each sets `.hamilton/phase`, prints a status banner
(phase, requirement / coverage counts, the last three `spec/` changes), then
drives the agent session with a one-line kickoff so it starts working
immediately — `build` needs no manual "build now". When the session ends,
Hamilton prints a short footer and the phase persists in `.hamilton/phase` for
the next launch. Self-switching is closed by an inherited marker:
`HAMILTON_SESSION` is exported before the agent starts, every child process
inherits it, and `design` / `build` refuse to run when it is set — so an agent
that shells out to `hamilton build` from inside a `design` session is blocked.
This scopes *which phase a session is in*; it does **not**, by itself, stop the
agent writing to `spec/` during a build session — it only fixes what phase that
session is.

**2. The write gate.** Hamilton refuses the file-editing tools (`Write` /
`Edit` / `MultiEdit` / `NotebookEdit`) on the read-only paths for the current
phase — this is what actually stops an in-session edit to `spec/` (in build) or
to source (in spec). One policy (`hamilton guard`'s `decide`) backs both the
in-process permission callback the session installs and the `.claude/`
`PreToolUse` hook, so the two cannot disagree; in a Hamilton session both run.
The hook is what still covers a `claude` session started outside Hamilton. The
same gate, in either phase, refuses a file edit that introduces or changes a
review suffix (§5.3) — only `hamilton review` writes one, and it writes files
itself, not through a hooked tool.

**Neither layer is an unbypassable boundary.** An operator who unsets
`HAMILTON_SESSION`, edits `.hamilton/phase` by hand, `chmod`s the paths back,
or starts the agent directly is outside the gate. This prevents *drift* — a
cooperating agent staying in its lane across a long session — not a determined
one. The authoritative gate is `hamilton check` run in CI, outside the agent's
reach (§8).

### 7.2 The three steps

**Step 1 — Specification**
Engineer updates the requirement tree: shape, statements, acceptance criteria,
and the verification method of each criterion. Claude may propose; the engineer
decides. No code is written.

**Step 2 — Implementation**
- *2a:* A **fresh subagent** writes tests from the AC and its method — the public signature for `unit`, a running instance otherwise — without the implementation body (see 5.2). An AC whose wording changed gets its test rewritten the same way.
- *2b:* `hamilton review` judges every unreviewed test (§5.3). A reject goes back to a fresh writer with the reviewer's reasons, then to review again — at most three rounds per test, then a hard stop. An `unclear` is a hard stop.
- *2c:* The newly reviewed tests run *before* implementing. One that is already green for a new or changed obligation is named in the summary: the behaviour exists already, or the test cannot fail.
- *2d:* Claude implements against the ratified requirements.

**Step 3 — Verification**
- `hamilton check` runs `test_command`; Claude repairs failures within the mutability rule below. A test it edits is `unreviewed` again and goes back through *2b*.
- The AC coverage gate (§5.4) is the exit condition and the precondition for opening the MR.

### 7.3 Mutability rule during Step 3

Without an explicit rule, agents repair red suites by weakening assertions, deleting failing tests, or loosening acceptance criteria.

| Artifact | During Step 3 |
|---|---|
| Implementation | Freely mutable — the intended repair surface |
| Tests | Mutable **only** when the test misreads its AC; the change must be justified against the AC text, and the edited test is reviewed again |
| Acceptance criteria | **Immutable.** Changing one means the spec was wrong → hard stop |

> A test that is correct and failing is a bug in the implementation. It is never edited to pass.

The review suffix makes this rule visible: an edited test loses its review, so
an edit to make it pass shows up in `hamilton check` and in the next review.

### 7.4 Loop-back edges

A hard stop is a **full stop**: Claude reports and yields to the engineer. It never continues on a deviated specification.

- **Step 2 → Step 1 (hard stop).** A ratified requirement or verification method proves wrong during implementation. Attempted spec write is denied; the agent stops. The engineer re-enters Step 1. Because both phases live in one branch and one MR, this costs a mode switch — no approval cycle.
- **Step 2 → Step 1 (hard stop), from the reviewer.** The reviewer answers `unclear` — the AC is ambiguous — or rejects a test three rounds running. The agent stops with the reviewer's question or last reasons.
- **Step 3 → Step 2.** Test failure caused by the implementation. Claude decides and proceeds.
- **Step 3 → Step 1.** Test failure revealing that the specification is wrong. **Only the engineer may take this edge.**

To avoid idling on a hard stop, the agent records the deviation and may continue with other already-ratified requirements; accumulated spec corrections are cleared in one Step 1 pass.

---

## 8. Enforcement

Enforced from day one; advisory processes degrade as soon as a session gets long.

### 8.1 Two tiers

- **Fast feedback** — the `PreToolUse` phase hook, inside an agent session. It
  keeps a cooperating agent on-process. It covers only the file-editing tools
  and is defeatable from a shell (§7.1); it is not the guarantee.
- **Authoritative** — `hamilton check` run in CI, outside the agent's reach, on
  every change. This is the tier that actually gates.

The framework ships **no CI config** — a pipeline is host-specific (D-005). A
project wires `hamilton check` into its own pipeline; the two lines that needs
are in the README. The authoritative tier must not be left implicit just
because it is unscaffolded.

### 8.2 Machine-checkable invariants

Non-exhaustive; the full target set is `data-model.md` §5.

- every requirement has ≥1 AC
- every AC names a defined verification method
- every AC has ≥1 traced test under its method's paths, and every test names an existing AC
- every counting tag carries a current review suffix
- the test suite runs and passes
- no orphan requirements; references resolve; `Parent` graphs are acyclic
- IDs unique, never reused (a counter, not a status lifecycle — no tombstones)

**Anything a script can check is never left to agent judgment.**

Periodic reconciliation: an agent diffs actual code behavior against the spec and **reports** discrepancies rather than fixing them.

---

## 9. Constraints for implementation

- **Language-agnostic.** The process must not assume a stack. The coding agent selects test framework and tooling.
- **Greenfield start, or brownfield adoption (D-018).** A new project writes its spec first. An existing codebase is adopted with `hamilton reverse`, which *derives* a first spec from the code and its git history — capturing intent and the load-bearing decisions, deliberately under-specified relative to the implementation, module by module. Ownership is unchanged (§6.1): the agent proposes, the engineer ratifies. The first `hamilton build` after it binds the existing tests to the derived criteria.
- **One agent, behind a seam.** Hamilton drives the session itself — `hamilton design` / `hamilton build` / `hamilton reverse` run the agent in process rather than handing over the terminal, which is what lets Hamilton own the question flow (so a mis-picked option can be taken back), end the session when the phase's work is done, and checkpoint every turn so an interrupted session resumes. That control is only purchasable by speaking a specific agent's protocol, so the earlier agent-agnostic launcher (`agent_command`, any CLI as a child process) was **retired**: it could set the phase but could see nothing inside the session. The dependency is contained rather than diffused — a single `AgentAdapter` (today `claude_agent_sdk`) is the only thing that knows which model is answering; the session driver, the write gate and the question flow are written against Hamilton's own event vocabulary. A second model is a second adapter. None exists yet, and the honest statement of today's position is: Hamilton runs on Claude.
- **Team collaboration via Git / GitLab.**
- **Enforced gating** rather than advisory.
