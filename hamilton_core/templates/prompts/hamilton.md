---
name: hamilton
description: Use in a repository scaffolded by `hamilton init`. Covers the spec-phase review protocol for drafting requirement changes, implementing code against a ratified requirement, propagating a spec change into the code, getting `hamilton check` back to green, deriving a first spec from an existing codebase (`hamilton reverse`), and adopting an existing test suite against that derived spec. The model is one requirement tree; every acceptance criterion names how it is verified.
---

# Hamilton workflows

`spec/requirements.md` is the whole model — one `Parent:` tree of
requirements and their acceptance criteria. It opens with a `## Verification
methods` section, and every criterion ends in a marker naming one of those
methods, e.g. `[browser]`. `spec/actors.md` is a flat supporting list.
`spec/` is writable only in `spec` phase; code and tests only in `build` phase.
`hamilton check` runs the test suite (`test_command` in `.hamilton/config`),
checks that every acceptance criterion has a `@covers`-tagged test under the
paths of its method (`paths.<method>` in `.hamilton/config`), and flags any
criterion whose **AC text** — marker included — changed since the last green
run. It hashes AC text only, so a changed title or statement produces no
finding. Its findings carry `file:line` and a rule — treat them as the work
list. It also prints advisory **warnings** (`long-statement`,
`long-description`, `root-unit-only`) that never fail a run but flag spec prose
that needs attention — see **How to write a requirement**.

## Which workflow?

- The engineer wants to change what the software must do -> **Specify**.
- A ratified requirement needs code -> **Implement**.
- The engineer changed `spec/` (you were told, or `git diff spec/` shows it)
  -> **Propagate a change**.
- `hamilton check` is red and you need it green -> **Verify**.
- The repo has code, `spec/requirements.md` is empty, and you are in a
  `hamilton reverse` session -> **Reverse-engineer the spec from existing
  code**.
- First `build` after `hamilton reverse` — `.hamilton/verified` does not exist,
  most or all ACs are `uncovered`, and `git log -- spec` shows the spec only
  just landed -> **Adopt an existing test suite**.

`hamilton design`, `hamilton build` and `hamilton reverse` scope the session to
a phase and hand you a kickoff line so you start straight away — do not wait to
be told "go". In a **build** session, run `git diff spec/` and `hamilton check`
first: if the diff shows a spec change, that is **Propagate a change**;
otherwise, or once the diff is dealt with, a red gate is **Verify** — unless
this is the first build after `hamilton reverse` (see its trigger above), which
is **Adopt an existing test suite**. End with a summary (below); Hamilton
offers the engineer their next step from there.

## Asking, and ending

Hamilton drives this session rather than handing the engineer a raw terminal,
so two mechanics are not optional:

1. **Put every question to the engineer through the `ask_engineer` tool** — an
   approval in the review protocol, a decomposition choice, an open input
   boundary. Supply `choices` when the answer is a selection, and leave
   `choices` empty for an open question. Hamilton always adds "Type my own
   answer" and "Finish this session" rows itself, so never include a catch-all
   choice such as "Other" or "Something else", or a choice to exit or end the
   session. Hamilton renders it and lets the engineer correct a mis-pick before
   it reaches you, which is the whole point; a question asked as plain prose
   bypasses that and strands them.
2. **End the closing summary with the literal line `HAMILTON_SESSION_DONE`**,
   on its own, after everything else. That line marks the end of one
   *iteration*, not the session: Hamilton takes it as the cue to show the
   engineer what they can do next — another change, a decomposition, or
   finishing. Emit it whenever a workflow runs to its summary, and do **not**
   tell the engineer to exit or that the session is over; Hamilton offers that
   choice, and the session stays open so the next piece of work keeps
   everything you have already read and ratified. Never emit it at a hard stop,
   which is the engineer's decision to act on.

---

## Specify — spec phase

The engineer wants a change to what the software must do. You draft it into
`spec/`; the engineer reviews and approves it **one piece at a time**. `spec/`
is writable now; source and tests are not.

### Opening the session

`hamilton design` has already printed a status banner (phase, requirement and
coverage counts, the last three `spec/` changes). Do not repeat it. Open with
one or two lines: greet the engineer, and — reading `hamilton tree` if you need
the shape — say in a sentence where the spec stands (e.g. "12 requirements, 3
ACs still uncovered" or "the spec is empty").

**If `hamilton tree` shows no requirements and `spec/vision.md` is still the
scaffolded stub** (its `<…>` placeholders unfilled), this is a new project:
offer to draft the vision first — see **Drafting the vision** below. If the
engineer declines, write nothing to `spec/vision.md` and go straight to the one
question below, taking the answer as "initial spec".

**Otherwise**, ask **one** question: are they drafting the initial spec, or
modifying / extending existing requirements, and what is the change? Wait for the
answer before Phase 1.

### Drafting the vision (first run only)

`spec/vision.md` is prose — **Purpose** (one or two sentences), **Users**
(bullets), **Non-goals** (tempting out-of-scope features, each with why).
`hamilton check` never reads it; it is here so a reviewer, and a future agent,
can tell a requested change from an unrequested feature. The **Non-goals** are
the load-bearing part.

Draft it the same way you draft a requirement — the engineer gives a coarse
description, you write the clean version, you ask where it is unclear:

1. Ask, in one prompt, for a coarse description: what the software is for, who
   uses it, and what is deliberately out of scope. Plain prose back.
2. Draft a clean `spec/vision.md` in the template's three sections. Fill every
   placeholder — leave no `<…>`.
3. Where you had to guess or the description is silent — most often *who the real
   users are* versus buyers or stakeholders, and *what is out of scope* — ask one
   focused question at a time. Same rule as Phase 3: a specific question, never
   "is this ok?".
4. Show the full draft. Wait for confirmation or corrections; iterate. **Write
   `spec/vision.md` only after the engineer confirms.**

Keep it short: when the description is solid and nothing needs a question, one
round is enough. This is orientation, not a spec — do not expand it toward
requirements.

### From vision to requirements

Once `spec/vision.md` is written — or the engineer skipped it — tell the engineer
the vision is set and the next step is the requirements. Ask them to describe
what the system must do, starting with the actor-level goals (the root layer;
specification is top-down). Then enter **Phase 1** of the review protocol with
that as the initial spec. Do not re-ask whether this is an initial or an
extending change — the vision step already settled it.

**Verification methods before the first requirement.** On a new project, once
the actors are ratified and before you propose the first requirement, propose
the `## Verification methods` section (see **How to write a requirement**) in
one review turn: each method, what it observes, what it stubs. Write it on
approval. Every AC you propose afterwards names one of these methods.

### How to show a requirement

These rules apply every time you name or display a requirement — in the plan,
in review, in the summary, anywhere. Not just in review.

1. **Never a bare id.** Always `R-0242 "Reject expired tokens"`. This includes
   parent links, actor links and cross-references. The read-only view commands
   render this way for you and write nothing:
   - `hamilton show <ID>` — one entity, `R-nnnn` or `A-nnnn`: its fields, its
     coverage, and what refers to it (a requirement's children; an actor's
     requirements).
   - `hamilton tree` — the requirement outline with computed dotted paths and
     a coverage mark per requirement.
2. **Show the path, not the parent.** Walk the `Parent:` chain to the root and
   render it by title:
   `Authentication › Sessions › Reject expired tokens`.
   `Parent: R-0007` tells the reviewer nothing; the path tells them where they
   are. (If a chain has no single root — a malformed model — walk as far as
   the parents resolve and say so.)
3. **Show a tree fragment for context.** When you present a new or changed
   item, draw the parent, its existing children (the siblings), and the item,
   with the item marked — so the reviewer sees where it lands, not only what it
   says:
   ```
   Authentication › Sessions   (R-0007)
   ├─ R-0031 "Issue a session on login"
   ├─ R-0042 "Reject expired tokens"        ← changed
   └─ R-0058 "Revoke a session on logout"   ← new
   ```
4. **Statement and ACs together, always.** The statement is what the
   requirement means; the ACs are what `hamilton check` enforces. Show both for
   every item. Neither is skippable.
5. **Every AC with its method.** Show the marker as part of the AC. Justify the
   method in one line only when it is not obvious.

### How to write a requirement

**Specification proceeds top-down.** Propose and ratify the **root layer** —
the actor-level goals, what someone wants from the whole system — before
decomposing any of them. A root requirement has no `Parent:` and names the
`Actor:` whose goal it is. Everything else has a `Parent:` and is a child of
another requirement. `hamilton check` fails a root with no `Actor:`
(`orphan-requirement`) and a `Parent` or `Actor` that names nothing
(`dangling-ref`).

**Completeness review.** After the root layer is ratified, run `hamilton tree`
and read it **upward**: for each parent, ask *do these children add up to
this?* Nothing else performs this check — a missing child requirement produces
no finding, because absence is invisible.

**STATEMENT — one sentence, under 20 words, one behaviour.** If it needs an
"and", a semicolon, or a dash introducing more detail, it is two requirements.
Split it. The detail does not disappear — it moves into acceptance criteria,
where the gate can act on it. `hamilton check` emits a `long-statement`
*warning* (advisory, never a failure) for any Statement over 20 words; treat it
as a split you owe the engineer, not as noise.

**METHOD — every AC names how it is verified.** The method is spec: it says
what a test must observe and what it may stub, and the engineer ratifies it with
the AC. The tool that runs it (Playwright, pytest, ...) is not part of it — that
stays a build-phase choice. Methods are defined once, in `## Verification
methods` at the top of `spec/requirements.md`: one bullet `- **name** — what is
real and what is stubbed`, e.g.

```
## Verification methods
- **browser** — the running site in a real browser at 375px and 1280px; real backend; external services stubbed.
- **http** — requests to the running backend; external services stubbed and the calls they received asserted.
- **unit** — one module in isolation, no I/O.
```

Propose a method for every AC; the engineer refines it. Choose by what the AC
observes, never by what is cheapest to test:

- an outcome the actor observes -> through the actor's channel (e.g. `browser`
  for a visitor of a website, `http` for an API client);
- a calculation or rule without I/O -> `unit`;
- data crossing to an external system -> `http`, with the stub's received calls
  asserted;
- a subjective quality (looks, feel) -> first make it checkable, e.g. a
  screenshot compared against an approved reference kept in `spec/`; `manual`
  only as a last resort. `manual` is reserved: it needs no definition and no
  test, and `hamilton check` lists it as not machine-verified.
- **every root needs at least one AC with an actor-facing method.** A root
  whose ACs are all `unit` proves the parts, never the goal — `hamilton check`
  warns `root-unit-only`. If no actor-facing method fits, ask the engineer.
- Two methods on one AC (`[unit, http]`) are allowed, and each then needs its
  own test. It is rare: it usually means the AC is two ACs — prefer splitting.

**DESCRIPTION (actors) — one sentence.** The external role and what it needs
from the system. Same "and" test as a Statement. A
Description that runs to a second sentence gets a `long-description` warning.

**Shared field rules go in a `## Domain vocabulary` section**, not in
statements. Put it at the top of `spec/requirements.md`, next to `## Verification
methods` and above the first `## R-nnnn` — `hamilton check` reads only the
methods section there and ignores the rest as prose. Define a
format, an enum, or a validation rule once, and reference it by name from the
ACs that need it. Never restate a shared rule inside a Statement.

#### Worked example — split a welded statement

A real Statement, 43 words, five behaviours welded together:

```
## R-0050 Customer CSV import
Statement: The service accepts an uploaded CSV of up to 10,000 rows, validates
each row against the account schema, rejects the entire file if any single row
is malformed, stores the valid rows, and emails the uploader a summary within
five minutes of completion.
```

The "account schema" is a shared field rule — it moves to domain vocabulary,
referenced by name, not spelled out in any Statement:

```
## Domain vocabulary
- **account schema** — the required columns and per-column validation for one
  customer row. ACs reference it by name.
```

The rest splits into three requirements — `R-0050` keeps its id but is narrowed
to one behaviour, and two children are added under it. Each is one sentence,
one behaviour, with every number and outcome from the 43-word version pushed
down into ACs:

```
## R-0050 Import a customer CSV
Statement: An uploaded customer CSV is imported into the account store.
Criteria:
- AC1: file with up to 10,000 rows -> accepted for processing [http]
- AC2: file with more than 10,000 rows -> 413, nothing stored [http]
- AC3: every row valid against the account schema -> all rows stored, stored count returned [http]

## R-0051 Reject a CSV with any malformed row
Parent: R-0050
Statement: A customer CSV with any malformed row is rejected whole.
Criteria:
- AC1: one row fails the account schema -> 422, zero rows stored [http]
- AC2: the rejection names the first failing row number and column [unit]

## R-0052 Notify the uploader when an import finishes
Parent: R-0050
Statement: The uploader is emailed a summary when an import finishes.
Criteria:
- AC1: import finishes -> summary email to the uploader within five minutes [http]
- AC2: the email states rows accepted and rows rejected [http]
```

Nothing was lost. "10,000 rows", "reject the whole file", "five minutes" are
all still there — as ACs, where a test can bind to each one.

### Review protocol

Think first, then present. **Never propose changes as you generate them.**

**Phase 1 — plan silently.** Work out the complete set of changes the request
implies: new requirements, edited requirements, edited ACs, removed
requirements, new or changed methods (on an AC or in `## Verification
methods`), moved subtrees. Apply **How to write a
requirement** as you go — a behaviour that needs an "and" is two requirements,
count it as two. Write nothing yet.

**Phase 2 — state the size, then show the plan.** Open with one line: **how
many requirements this change touches** — new, substantively edited and
removed. If
that is **more than about six**, stop there: say so and propose splitting the
engineer's *request* into smaller pieces before going further. Do not show the
full plan or write anything until the request is cut down.

Otherwise, a numbered list, one line per change, each with title and path, no
detail:
```
Touches 4 requirements (2 new, 1 edited, 1 removed).

1. New     Authentication › Sessions › R-0058 "Revoke a session on logout"
2. Edit    Authentication › Sessions › R-0042 "Reject expired tokens" — AC2 reworded, AC3 added
3. Edit    Authentication › R-0007 "Sessions" — statement clarified
4. Remove  Authentication › Sessions › R-0031 "Remember me"
```
This lets the engineer see the shape and the size before spending attention.

**Phase 3 — one item at a time, in order.** For each item, show:

- **where it lands** — the tree fragment (rule 3), titles not ids
- **statement** — the full text; for an edit, *before* and *after*
- **acceptance criteria** — all of them, each with its method; for an edit,
  *before* and *after*
- **CONSEQUENCE** — what this makes true elsewhere: which ACs become
  `uncovered`, which passing tests go `stale` — changing an AC's method is a
  consequence just like rewording it, and its old test no longer counts. Name
  them specifically. State a
  shared dependency once, on the first item that has it — do not repeat it on
  every dependent item.
- **ASSUMPTIONS** — only the ones you made for *this* item that the engineer
  did not state. Omit the heading if there are none. Never dump assumptions
  for several items at once. If an assumption exists only because of the open
  question below, put it in the question, not both.
- **one question** — only if a real decision is still open: an input boundary
  the ACs do not settle, or a decomposition or method choice you had to guess.
  Ask about that specific case, never "is this ok?" — e.g. *"AC2 says
  whitespace runs count as one separator; what should `initials('  ada  ')`
  return?"* If the item settles what it needs to, ask nothing.

**For a removal**, show where it sits, its full statement and acceptance
criteria as they stand, and a CONSEQUENCE naming:

- its children — each needs a new parent or is removed too; if the request
  does not settle which, that is the one question
- any requirement whose statement or ACs refer to it
- the tagged tests for its ACs, which become `orphan-tag` in the next build

A removed requirement's id is **never reused**: a new requirement always takes
a fresh id. A reused id would silently bind the old tests to the new
requirement.

Then **wait.** The engineer replies with approval, a correction, or a question.
Do not move to the next item until this one is settled. When an item is
approved, write it to `spec/` — then move on. **Never write ahead of
approval.**

**Batch the trivial.** An item with no consequence and no open question — a
typo, a reworded title — is not worth a turn of its own. Collect these and
present them at the end as one confirmation, **one line each: what changed,
nothing restated**. If you find yourself explaining why it is harmless, it is
not trivial — give it a Phase 3 turn.

**Phase 4 — summary.** State what was written, what the engineer changed or
rejected during review, and the consolidated red list: which rules
`hamilton check` will now report and why, e.g. *"R-0016 (2 ACs) and R-0017
(4 ACs) become `uncovered`; work them in build phase."* Name requirements and
count their ACs — do not list every AC. Do not re-explain the per-item
CONSEQUENCE lines. If `.hamilton/verified` does not exist yet (no run has
passed), a reworded AC does **not** go `stale` — it stays `uncovered`; say
that, do not announce `stale`.

That closes the iteration. Do not tell the engineer to exit -- Hamilton asks
them what comes next, and may hand you another change to run the protocol on
from Phase 1.

Once the engineer starts a build session (`hamilton build`), that `stale` /
`uncovered` list is the **Propagate a change** work list.

---

## Reverse-engineer the spec from existing code — spec phase

`hamilton reverse` opens this workflow: an existing codebase has no spec yet and
you derive its first one. Spec phase — `spec/` is writable, source and tests are
not. You run it **once, end to end**, working module by module. If the session
is cut short, the engineer finishes with `hamilton design` (the tree is no
longer empty, so `hamilton reverse` will not re-open).

### The discipline — derive, do not transcribe

You are recovering the specification that the code is **one implementation of**,
not documenting the code. The code is far more detailed than the spec should
be: capture **intent and the load-bearing decisions**, and
leave out everything the repository already states authoritatively — signatures,
file layout, algorithms, data structures. Err toward **less**: a tree so
under-specified that a fresh agent could re-implement it a different but
acceptable way is *correct*; a tree that pins every branch and error string is
wrong. If a decision later turns out to matter, it gets added to the spec then —
that is the intended way the spec grows, not a failure of this pass.

The granularity test from *How to write a requirement* bites hard here: if
writing the requirement takes longer than reading the code it describes, it is
too fine.

### Phase A — survey (silently)

Read, and write nothing yet:

1. **The code.** Entry points, the module / package layout, build and dependency
   config, the public surface of each module.
2. **The prose.** `README`, anything under `docs/`, ADRs, a decision log, the
   changelog.
3. **The history.** `git log`, `git log --stat`, tags. Commit messages often
   state the *why* of a decision and show how a module was reshaped — that is
   design rationale you would otherwise have to reconstruct or ask about.

Produce a **module map**: each subsystem, a one-line purpose, and a one-line
guess at what crosses its boundary. Note too how the existing tests observe the
system (unit, HTTP, browser, ...) — the raw material for the verification
methods.

### Phase B — confirm the frame (interview)

Before proposing a single requirement, show the engineer:

- what you think the system is **for**, and **who** uses it;
- the **actor list** you would write (`## A-nnnn`, name, one-sentence
  description);
- the **module map** with your per-module purpose;
- the **`## Verification methods`** you would write — what each observes and
  stubs.

Ask them to correct it. A wrong mental model is cheapest to fix here, before any
requirement is built on it.

### Phase C — vision

Draft `spec/vision.md` from the survey and the engineer's corrections, using the
same propose / confirm / iterate loop as **Drafting the vision** above (write the
file only on confirmation). The **Non-goals** are the hard part: the code cannot
tell you what was *deliberately* excluded versus simply never built. Ask
directly — *"there is no retry logic anywhere — is that out of scope, or just
not built yet?"* — one focused question at a time.

### Phase D — the requirement tree, module by module

Run the **Specify review protocol** (Phases 1–4, one item at a time, wait for
approval, write on approval) and every rule in **How to show a requirement** and
**How to write a requirement**. Two brownfield specifics:

1. **Methods, then roots.** Write the ratified `## Verification methods`
   section first. Then propose and ratify the whole root layer of actor-level
   goals from the entry points before decomposing any of it (top-down, as
   always).
2. **Then one module at a time**, ratified before you move to the next. The
   ~6-requirements cap in Phase 2 applies **per module** — if a module needs
   more than that, propose a coarser cut first. For each module:
   - Propose the **interior requirement** for the module — what it is for, in
     intent and prose. The module's public surface is a hint; lift it to
     intent, do not paste the signature.
   - Decompose into **leaf requirements** — `Statement` + ACs — that capture the
     *observable, important* behaviour: the "if this changed silently it would
     be a bug" altitude. Not every branch, not every message string. Propose a
     method for each AC by what it observes (**How to write a requirement**),
     not by where the existing test happens to sit.
   - For each notable **design decision** you found — a choice of algorithm,
     protocol, wire or file format, ordering guarantee, a hard limit, an
     error-handling stance — say in the CONSEQUENCE / ASSUMPTIONS lines which
     bucket it is in:
     - **load-bearing intent** — record it as an AC;
     - **implementation choice** — leave it unspecified; the next agent may
       revisit it. Name it under ASSUMPTIONS so the engineer can pull it back
       into the spec if they disagree.
   - **Interview when the code is ambiguous about intent** — a behaviour that
     could be intentional or incidental, a limit that looks arbitrary, two
     modules whose responsibilities overlap. Phase 3's one-question rule
     applies: a specific question, never "is this right?".

### Phase E — completeness and over-specification review

1. Render `hamilton tree` and read it **upward**: for each parent, *do these
   children add up to this?* (the only check for a missing requirement,
   because absence is invisible).
2. **Brownfield pass — code with no requirement.** Is there significant code
   that no requirement now covers? Triage each into one bucket: a **missing
   requirement** (add it, through the protocol),
   or **code nobody would ask for** (note it for the engineer as a
   deletion candidate — do **not** delete it, you are in spec phase).
3. **Reverse pass — requirements finer than the code earns.** Anything you
   over-specified: coarsen it.

### Phase F — summary

State what was written: the vision, the actor count, and the tree shape (root
count and depth). Then, plainly:

> Almost every AC is now `uncovered` and `hamilton check` will be red. That is
> the expected state after `hamilton reverse`, not a failure. `.hamilton/verified`
> does not exist yet, so nothing is `stale`.

Next step for the engineer: `hamilton build`, which opens **Adopt an existing
test suite**.

---

## Implement

1. Read the ratified requirement in `spec/requirements.md`, its ancestors, and
   the definitions of its ACs' methods in `## Verification methods`. Implement
   against exactly those. Code that no AC asks for is deleted, not kept.
2. Author tests — see **Test authoring**. Each test carries a comment
   `@covers R-nnnn/ACn` naming the one AC it exercises, and exercises it by that
   AC's method, in a file under that method's `paths.<method>`.
3. Run `hamilton check`. Resolve every finding. If it reports
   `no-test-command` or `no-method-paths`, the framework and test layout are
   yours to choose: set `test_command` and the `paths.<method>` keys in
   `.hamilton/config` (the only file under `.hamilton/` you may edit in build
   phase, and only those keys) and re-run.
4. **Run the product.** For every AC with an actor-facing method, start the
   system and see the outcome the way the actor would. A green `hamilton check`
   alone is not done: it proves a tagged test passed, not that the product
   works.
5. If the ratified requirement or a method proves wrong -> **Hard stops**.

## Propagate a change

The engineer edited `spec/` during a design (spec-phase) session. Bring the code and tests
back in line — and only that.

1. `git diff spec/` — read what changed and why.
2. `hamilton check`.
3. Rework exactly what it names, nothing else:
   - `stale` — an AC was reworded or its method changed: re-check the
     implementation and the `@covers` test against the new wording. A new
     method needs a test by that method. A clean `hamilton check` records the
     new hash.
   - `uncovered` — an AC's method has no tagged test under its paths: add one
     that exercises the AC by that method.
   - `wrong-method` — the AC is tagged, but under another method's paths:
     write a test by the AC's method under its paths. Moving the tag is not
     enough.
   - `no-method-paths` — a method has no `paths.<method>` yet: choose where
     its tests live and set the key.
   - `orphan-tag` — a tag points at an AC or requirement that no longer
     exists. If the requirement was removed, delete the test and any code only
     it needed; retarget the tag only if the behaviour moved to another
     requirement.
4. Re-run `hamilton check` until it exits 0. Do not touch what it does not name.
5. **Summary.** List the files and requirements you touched and which ACs moved
   out of `stale` / `uncovered`. That closes the iteration; Hamilton asks the
   engineer what comes next.

## Verify

1. Run `hamilton check`. It runs the test suite and reports what is wrong.
2. Repair failures under the mutability rule:
   - **Implementation** — freely mutable; the repair surface.
   - **A test** — changed only when it misreads its AC, and justified against
     the AC text. A correct failing test is a bug in the implementation; never
     edit it to pass.
   - **An acceptance criterion** — immutable. Needing to change one is a hard
     stop (see below).
3. Re-run `hamilton check`. Exit 0.
4. **Summary.** Say what you changed to get to green. That closes the
   iteration; Hamilton asks the engineer what comes next.

## Adopt an existing test suite — build phase

The first `hamilton build` after `hamilton reverse`. The spec was just derived
from the code, so nearly every AC is `uncovered` and the gate is red — but the
code already works. Your job is to **bind** the derived criteria to tests, not
to change behaviour.

### Recognise it, and rule out the other two

`hamilton check` is red with `uncovered` on most or all ACs, `.hamilton/verified`
does not exist, and `git log -- spec` shows the spec only just landed. That is
**not Propagate a change** (there is no incremental `git diff spec/`) and **not
Verify** (the suite is unbound, not logically failing). If the suite is actually
failing on logic, that is a **Verify** problem and comes first.

### Steps

1. **Set `test_command` and the `paths.<method>` keys** in `.hamilton/config`
   if they are still unset — discover the project's existing runner and test
   layout, and map each directory to the method its tests actually use. These
   are the keys you may edit in build phase. Run the suite once as-is and
   confirm it is green before you start.
2. **For each `uncovered` AC**, in tree order:
   - If an existing test **genuinely asserts that AC's observable condition ->
     outcome by the AC's method** — not merely exercises the same area of code,
     and not a unit test standing in for a `browser` AC — add the
     `@covers R-nnnn/ACn` comment to it. One AC per tag. Do not attach a tag to
     a test that asserts something narrower or different just to clear the
     finding.
   - Otherwise **write a new AC-level test** via the **fresh-subagent rule**
     (see *Test authoring*) under that method's paths. It sits **alongside**
     the existing tests. Do not
     delete or rewrite them: they still run and still guard against regressions,
     they are simply not the AC binding.
   - Keep the count bounded — a few tests per requirement.
3. **Triage what will not bind:**
   - An AC you cannot write a feasible test for is a signal the AC is wrong or
     pitched too deep. **Hard stop** (see below): report it; the engineer
     re-enters `hamilton design`. Never weaken the AC to make it bind.
   - Code paths that no requirement covers: list them for the engineer — a
     missing requirement, or dead code. Do not act on it in build phase.
4. **Iterate `hamilton check` to green.** A clean run writes `.hamilton/verified`
   — the adoption is complete and from here it is the normal loop.
5. **Summary.** ACs bound to an existing test, ACs given a new test, ACs that
   hard-stopped back to spec, and any code with no covering requirement. Then
   that closes the iteration; Hamilton asks the engineer what comes next.

## Test authoring

- Tests are written by a **fresh subagent** given only the requirement text
  (`Statement` + `Criteria`), the definition of the AC's method, and:
  - for `unit`, the unit's public signature;
  - for any other method, a running instance of the system and how to reach
    it — **not** the source.

  Never the implementation body. An agent that just wrote the code writes tests
  that encode its own bugs.
- This is an instruction, not an enforced boundary: the subagent shares the
  repo. `hamilton check` confirms only that a tagged test exists and passes —
  it does not judge whether the test is any good. That judgement is the whole
  reason for the fresh-subagent rule.
- Every test carries its `@covers R-nnnn/ACn` tag, exercises the AC by its
  method, and sits under that method's `paths.<method>`. A `manual` AC gets no
  test.
- Keep the count bounded — a few tests per requirement, prioritised, not
  assertion padding.

## Hard stops

Concept.md 7.4. During **Implement** or **Adopt an existing test suite**, if a
ratified requirement or method proves wrong — including an AC that no
feasible test can bind by its method:

- Stop. Report the deviation to the engineer, specifically.
- Do **not** edit `spec/` — you are in `build` phase and the guard hook blocks
  it anyway.
- Do **not** work around it in the code.
- The engineer ends this session and starts a `hamilton design` session to fix
  the model, then a fresh `hamilton build`. You may continue on other
  already-ratified requirements in the meantime.

Needing to change an acceptance criterion — its method included — is the same
hard stop: ACs are immutable during `build`.
