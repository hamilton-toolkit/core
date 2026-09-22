# Data Model — Definition Side (PoC)

Deliberately minimal; see §7 for what is intentionally absent. Reflects D-014
(the model is one requirement tree), D-019 (every acceptance criterion names
its verification method; `Interface:` is retired) and D-020 (every test is
reviewed, and the review is recorded in its tag).

Companion to `concept.md`. Covers the **definition** side only (the left leg of
the V). Verification-side artifacts are out of scope here. In the PoC the
verification side is deliberately thin: `@covers R-nnnn/ACn` tags in the
tests under each method's paths, each carrying the review suffix `hamilton
review` wrote when its reviewer passed the test (§2.4). The falsification
ledger described in earlier drafts was removed, and `.hamilton/verified` is
retired — see `concept.md` §5.3.

---

## 1. Terminology

| Term | Meaning |
|---|---|
| **Actor** | External entity interacting with the system. Defines the system boundary. A flat supporting list — referenced, never referencing. |
| **Requirement** | What shall be true, plus its acceptance criteria. The requirements form one `Parent` tree, which is the whole model. |
| **Verification method** | How an acceptance criterion is proven: what a test observes, what is real and what is stubbed. Defined once per project; named by every AC. Not an entity with an id. |
| **Region** | What counts as "the test" for a `@covers` tag: the file's preamble (every line above its first tag block) plus the tag's own section (its tag block down to the next tag block, or the end of the file). A layout rule, not a parser. |
| **Supporting file** | A file in `spec/` beside the model — a rule set, legal or marketing text, a visual reference — that a `Statement` or AC incorporates by naming it as `spec/<file>`. Content, not verification: what must hold about it is stated by the AC. |
| **Review suffix** | `#<obligation>.<test>` after a `@covers` tag: the record that a reviewer passed this test for this AC, as both stood then. Written only by the reviewer in `hamilton build`. |

There is no **Component** and no **Module** entity (D-014), and no
**Interface** (D-019). The tree holds requirements and criteria; the
architecture lives in the code.

---

## 2. Entities

### Principle: the specification never duplicates the code

The specification holds what code cannot express — intent, decomposition,
acceptance criteria. Anything the repository already states authoritatively
(exact signatures, file locations, call graphs) is **never** copied into the
model. Duplicated facts drift, and they force specification edits for changes
that are pure refactoring.

### 2.1 Actor — `A-nnnn`

| Field | Req. | Type | Notes |
|---|---|---|---|
| `id` | yes | `A-nnnn` | |
| `Name` | yes | text | |
| `Description` | yes | prose | One sentence: the external role and what it needs from the system |

No outbound references. Actors are referenced, never referencing. `spec/actors.md`
is a flat list — no tree.

### 2.2 Requirement — `R-nnnn`

| Field | Req. | Type | Notes |
|---|---|---|---|
| `id` | yes | `R-nnnn` | |
| `Parent` | no | `R-nnnn` | Absent = **root requirement** — see the root rule below |
| `Actor` | on roots | `A-nnnn` | Required on a root; meaningless elsewhere |
| `Statement` | yes | prose | One sentence, one behaviour, under 20 words (D-006) |
| `Criteria` | yes | list, ≥1 | `AC<n>: <observable condition> -> <expected outcome> [<method>, ...]` — the marker names ≥1 method from §2.3 |

`Component` (D-014) and `Interface` (D-019) are **retired fields**: recognised
and skipped by the extractor so an older `requirements.md` still parses. They
are not stored and not flagged.

**The `Parent` tree is the whole model, and it is mandatory (D-007, D-014).**
A requirement with no `Parent` is a *root* — a system-level goal, what an actor
wants from the whole system — and **must** name the `Actor:` whose goal it is.
Everything else names a `Parent`. Specification proceeds top-down: the root
layer is ratified before it is decomposed.

The gate fails `orphan-requirement` for a requirement with neither a `Parent`
nor an `Actor`, and `dangling-ref` for a `Parent` or `Actor` that names no
declared entity; see §5.

### 2.3 Verification method — `## Verification methods`

Not an entity, not an id. A `## Verification methods` section at the top of
`spec/requirements.md`, above the first requirement, defines each method once:

| Part | Syntax | Notes |
|---|---|---|
| name | `**name**` | lowercase, `[a-z][a-z0-9-]*`; what AC markers reference |
| description | prose after `—` | what a test observes, what is real, what is stubbed |

- **Every AC names ≥1 method** in a trailing marker, `[browser]` or
  `[unit, http]`. More than one is rare; it usually means two ACs.
- **The marker is part of the AC.** It, and the method's definition, are part
  of the obligation a test's review covers (§2.4), so changing an AC's method —
  or redefining the method — leaves its tests `unreviewed`.
- **`manual` is reserved.** It needs no definition and no test; `hamilton verify`
  lists it as not machine-verified.
- **The method is spec, the tool is not.** Which framework runs a method's tests
  and where they live is a build-phase decision: `paths.<method>` in
  `.hamilton/config`. A `@covers` tag counts for an AC only under the paths of
  one of its methods, and a multi-method AC needs a tag under each.
- **Why the human owns it.** Left to the agent, the verification level becomes
  whatever is cheapest to test: design ACs checked by comparing hex values, a
  wizard checked by unit-testing its state module while the page wiring it up
  was broken. The engineer ratifies each method with its AC.

**D-019 — verification method per AC; `Interface:` retired.** Supersedes the
`Interface:` part of D-014 and the rule that the verification level derives
from tree position. The level was never enforced, so a gate could be green over
a broken product, and `Interface:` lines in practice restated the Statement or
the ACs. The tree is now requirements and ACs only; each AC records the method
that proves it (concept §4.4, §5.1).

### 2.4 Review suffix — on the `@covers` tag

Not an entity, not an id: a suffix on a tag in a test file,

```
// @covers R-0005/AC2 #3f9a2c.81d0e4
```

two 6-hex-digit prefixes of SHA-256, each taken over whitespace-normalised
text:

| Half | Covers | A change means |
|---|---|---|
| obligation | the AC id, its requirement's `Statement`, the AC text with its marker, the definition of each of the AC's methods whose paths hold the test file (sorted by name), and the content of each supporting file the `Statement` or AC references (sorted by path; only when there are any) | the test is judged against the new wording, and rewritten only if it no longer proves it |
| test | the tag's region (§1), with every suffix stripped from its tag lines | the test only needs another review |

- **Each tag has its own.** In a stacked tag block the test half is the same
  for every tag; the obligation half differs per AC.
- **Every counting tag needs one.** A tag counts when it names an existing AC
  and lies under the paths of one of the AC's methods. Another reviewed tag for
  the same AC does not excuse an unreviewed one. `wrong-method` and
  `orphan-tag` tags are not reviewed.
- **Only the reviewer in `hamilton build` writes it**, when it passes the test; the
  write gate refuses a file edit that introduces or changes one. A green
  `hamilton verify` writes nothing.
- **Cross-file code is in no region.** A shared helper in another file can
  change without invalidating any review (concept §5.3).

**D-020 — reviewed tests.** Supersedes `.hamilton/verified` and the `stale`
rule. The hash covered the AC line only, so redefining a method or rewording a
Statement re-flagged nothing, and `stale` cleared itself on the next green run
without anyone looking at a test. The judgement "this test proves this AC" is
now made per test, by a reviewer Hamilton runs with enforced inputs, recorded
in the tag, and invalidated when either the AC side or the test side changes
(concept §5.3).

### Phase note

Everything under `spec/` belongs to Step 1, so the phase gate stays a simple
directory rule with no field-level exceptions.

---

## 3. Identity

**Flat, monotonic, never reused.** `R-0042`, `A-0001`.

- Allocated by tooling from a persistent counter, never by an agent.
- The counter is the sole source of new IDs, so deleting an entity can never
  cause reuse. **No tombstones and no status field are needed for this
  invariant.**
- Hierarchy is carried by `Parent`, never by the number.

### Dotted paths are a derived view

The readable form — `10.5.3` — is **computed** by the tooling for display and
never stored or referenced:

```
10.5.3  (R-0042)  Reject expired tokens
```

Because nothing references the dotted form, recomputing it after any tree change
is free and harmless. This gives the readability of hierarchical numbering with
none of the renumbering hazard.

Siblings are ordered by id. A `Parent` that does not resolve, or that would
close a cycle, is treated as a root for path computation, so a malformed tree
still renders instead of failing the view.

---

## 4. File layout

```
spec/
  actors.md         # flat list of A-nnnn
  requirements.md   # the one Parent tree -- the whole model
  vision.md         # purpose, users, non-goals -- prose, not entities (D-009)
  <supporting files> # content criteria incorporate by `spec/<file>`
```

A supporting file is read only through the criteria that reference it: its
content is part of their obligation, so an edit to it leaves their tests
`unreviewed`, and the reviewer is shown it in full. A criterion that
references none hashes as if references did not exist.

Review suffixes live in the test files, on the tags. `.hamilton/verified`, the
per-AC hash file of earlier versions, is retired (D-020): nothing reads or
writes it, and `hamilton verify` prints a notice while it still exists.

`vision.md` holds prose rather than entities and is not read by `hamilton verify`.
It is phase-gated like the rest of `spec/`. A pre-D-014 project may still have
`spec/components.md` / `spec/modules.md` on disk; they are simply unread.

**PoC note.** ID-allocation tooling is out of scope for the PoC: IDs are
written by hand and `hamilton init` does not create a `.hamilton/counters`
file. The persistent-counter mechanism described in §3 is the intended end
state. There is no separate `duplicate-id` rule: a repeated `## R-nnnn` is
reported as `malformed`, which guards R-id uniqueness in the meantime.

### 4.1 Block syntax

Entities are `##` blocks. Fields are `Key: value` lines. Lists are `-` items. A
blank line ends a field; the next `##` ends the block. Text inside a fenced code
block is ignored, so the worked example each template ships is not parsed.

**`spec/actors.md`**
```markdown
## A-0001
Name: End User
Description: Uses the application through the web interface.
```

**`spec/requirements.md`** — the methods, a root and a child
```markdown
## Verification methods
- **http** — requests to the running service; external services stubbed.
- **unit** — one module in isolation, no I/O.

## R-0001 Requests are authenticated
Actor: A-0001
Statement: Every inbound request is authenticated before it is routed.
Criteria:
- AC1: no token -> 401 [http]
- AC2: valid token -> the request reaches its handler [http]

## R-0042 Reject expired tokens
Parent: R-0001
Statement: The auth middleware rejects a request whose token exp claim is in the past.
Criteria:
- AC1: expired token -> 401, no user data in response body [http]
- AC2: token inside the 30s clock-skew window -> accepted [unit]
```

---

## 5. Invariants (validator)

This is the target invariant set. A row marked **[check]** is enforced by
`hamilton verify` today; the rest are the roadmap for it.

Structural:
1. IDs unique across the model and well-formed for their type. **[check]** for
   `R-nnnn` (well-formed, and no id declared twice); `A-nnnn` well-formedness
   is assumed by the actor reader.
2. Every reference resolves to an existing entity of the correct type.
   **[check]** for a requirement's `Parent` and `Actor` (rule `dangling-ref`).
3. The requirement `Parent` graph is acyclic. **[check]** — `cyclic-parent`.
4. Every allocated number is ≤ the stored counter for its type. *(roadmap)*

Content:
5. Every requirement has ≥1 acceptance criterion. **[check]** — `malformed`.
6. Every requirement has a `Statement`. **[check]** — `malformed`.
7. Criterion labels are unique within their requirement. **[check]** — `malformed`.

Requirement tree (D-007, D-014):
8. A requirement with no `Parent` and no `Actor` — failure `orphan-requirement`.
   **[check]**
9. `Parent` and `Actor` resolve to a declared entity — failure `dangling-ref`;
   the `Parent` chain is acyclic — failure `cyclic-parent`. **[check]**

Verification methods (D-019):
10. Every AC ends in a method marker — failure `no-method`. **[check]**
11. Every method in a marker is defined in `## Verification methods`, or is
    `manual` — failure `unknown-method`. **[check]**
12. Every method in use has `paths.<method>` in `.hamilton/config` — failure
    `no-method-paths`. **[check]**
13. An AC tagged only outside its methods' paths — failure `wrong-method`;
    a method with no tag under its paths — failure `uncovered`. **[check]**
14. `.hamilton/config` sets no retired `test_paths` — failure `retired-config`.
    **[check]**

Reviews (D-020):
15. Every counting tag carries a current review suffix — failure `unreviewed`,
    one per tag, naming which half changed. **[check]**

Supporting files:
16. Every `spec/<file>` a `Statement` or AC names exists — failure
    `missing-reference`. **[check]**

Advisory (warn, do not fail):
17. A `Statement` over 20 words — warning `long-statement`. **[check]**
18. An `Actor` `Description` that is more than one sentence — warning
    `long-description`. **[check]**
19. A root requirement whose ACs are all `unit` — warning `root-unit-only`.
    **[check]**
20. A parent's children do not add up to it — **not checkable**; absence is
    invisible (concept §5.5). Mitigated by reading `hamilton show` upward.

*(Gone with D-014: `component-tree`, `module-marker`, `tree-consistency`,
`unmarked-module`, and the `malformed` "no Component" manifestations. Gone with
D-019: the `no-interface` warning. Gone with D-020: `stale`.)*

---

## 6. Derived views

Computed, never stored:

- **Dotted path** per requirement.
- **Outline** — the requirement tree with dotted paths, statements and a
  rolled-up coverage mark.
- **Coverage** per AC, method-aware: a tag counts only under the paths of one of
  the AC's methods. An AC is `uncovered` while a method lacks a counting tag,
  `unreviewed` while any counting tag lacks a current review, else `covered`.
- **Review state** per tag: reviewed, or which half of its suffix no longer
  matches.
- **Reverse links** — a requirement's children; an actor's requirements.

In the PoC these are surfaced by the read-only command `hamilton show`: with no
id the outline, with `<ID>` one entity — `R-nnnn` or `A-nnnn` — and what
refers to it. It recomputes from `spec/` on every call
and writes nothing. There is no `hamilton graph`: a tree needs no tool to read.

---

## 7. Deliberately out of scope for the PoC

| Omitted | Why |
|---|---|
| Separate component model and module model | The code is the architecture and the physical model (D-014, D-019). A parallel model is deferred until a system large enough to need one. |
| `Status` field | Its only structural job was never-reuse; the counter covers that (§3) |
| Cross-cutting secondary links (`Also satisfies`) | Needed eventually; not needed to prove the method |
| Rationale / priority / owner fields | Additive later without migration |
| Rich test↔AC linkage | Verification side. The PoC keeps only the minimum: `@covers` tags resolved against the requirements, each with its review suffix (§2.4) |
| Proof of test quality (mutation testing) | Per-test review is in (D-020); it is a judgement, not a proof. Mutation testing (`mutation_command`) is its planned mechanical complement |
