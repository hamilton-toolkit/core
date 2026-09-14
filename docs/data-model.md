# Data Model — Definition Side (PoC)

Deliberately minimal; see §7 for what is intentionally absent. Reflects D-014
— the model is one requirement tree.

Companion to `concept.md`. Covers the **definition** side only (the left leg of
the V). Verification-side artifacts are out of scope here. In the PoC the
verification side is deliberately thin: `@covers R-nnnn/ACn` tags in the code,
and `.hamilton/verified` (one hash per AC, written by `hamilton check` on a
passing run). The falsification ledger described in earlier drafts was removed
— see `concept.md` §5.3.

---

## 1. Terminology

| Term | Meaning |
|---|---|
| **Actor** | External entity interacting with the system. Defines the system boundary. A flat supporting list — referenced, never referencing. |
| **Requirement** | What shall be true, plus its acceptance criteria. The requirements form one `Parent` tree, which is the whole model. |
| **Interface** | A prose line on an *interior* requirement (one with children) naming what crosses the boundary that requirement owns. Not a separate entity. |

There is no **Component** and no **Module** entity (D-014). The interior of the
requirement tree is the architecture; the code is the physical model.

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
| `Interface` | on interior nodes | prose | Required once the requirement has children: one sentence naming what crosses its boundary. Advisory before then. |
| `Statement` | yes | prose | One sentence, one behaviour, under 20 words (D-006) |
| `Criteria` | yes | list, ≥1 | `AC<n>: <observable condition> -> <expected outcome>` |

`Component` is a **retired field** (D-014): recognised and skipped by the
extractor so a pre-D-014 `requirements.md` still parses. It is not stored and
not flagged.

**The `Parent` tree is the whole model, and it is mandatory (D-007, D-014).**
A requirement with no `Parent` is a *root* — a system-level goal, what an actor
wants from the whole system — and **must** name the `Actor:` whose goal it is.
Everything else names a `Parent`. Specification proceeds top-down: the root
layer is ratified before it is decomposed.

The tree is at once *problem* structure and *solution* structure:

- a **leaf** requirement is behaviour to implement and test directly (unit level);
- an **interior** requirement is a subsystem boundary — decomposing it *is* the
  architectural decision — and carries an `Interface:` line, which is the
  integration-test surface (concept §6.1).

The gate fails `orphan-requirement` for a requirement with neither a `Parent`
nor an `Actor`, and `dangling-ref` for a `Parent` or `Actor` that names no
declared entity; see §5. An interior node with no `Interface:` yet is the
advisory `no-interface` warning — expected while a subsystem is still being
decomposed.

### 2.3 Interface — the line on an interior requirement

Not an entity, not an id. A single `Interface:` field on a requirement that has
children.

- **Prose, not signature.** It names *what* crosses the boundary — the data, the
  calls, the protocol, a mandated technology — in intent. The concrete callable
  form lives in the code and is not duplicated here.
- **One sentence.** If it needs an "and" listing several unrelated things, the
  decomposition beneath the node is probably wrong.
- **Why the human owns it.** Interface errors are the dominant integration
  failure class (concept §6.1, the Apollo/DBTF lineage). The interface line is
  the one architectural artefact worth authoring by hand, and it belongs *on
  the boundary requirement*, not in a parallel file.
- **Hard-stop anchor.** A ratified interface proving wrong during build is a
  hard stop (concept §7.4); the deviation names the requirement whose
  `Interface:` moved.

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
```

`vision.md` holds prose rather than entities and is not read by `hamilton check`.
It is phase-gated like the rest of `spec/`. A pre-D-014 project may still have
`spec/components.md` / `spec/modules.md` on disk; they are simply unread, and
`hamilton upgrade` does not touch `spec/`.

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

**`spec/requirements.md`** — a root and a child
```markdown
## R-0001 Requests are authenticated
Actor: A-0001
Interface: HTTP bearer-token header on every inbound request; reply is 200 or 401.
Statement: Every inbound request is authenticated before it is routed.
Criteria:
- AC1: no token -> 401
- AC2: valid token -> the request reaches its handler

## R-0042 Reject expired tokens
Parent: R-0001
Statement: The auth middleware rejects a request whose token exp claim is in the past.
Criteria:
- AC1: expired token -> 401, no user data in response body
- AC2: token inside the 30s clock-skew window -> accepted
```

---

## 5. Invariants (validator)

This is the target invariant set. A row marked **[check]** is enforced by
`hamilton check` today; the rest are the roadmap for it.

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

Advisory (warn, do not fail):
10. A `Statement` over 20 words — warning `long-statement`. **[check]**
11. An `Actor` `Description` that is more than one sentence — warning
    `long-description`. **[check]**
12. An interior requirement (has children) with no `Interface:` line — warning
    `no-interface`. **[check]**
13. A parent's children do not add up to it — **not checkable**; absence is
    invisible (concept §5.5). Mitigated by reading `hamilton tree` upward.

*(Gone with D-014: `component-tree`, `module-marker`, `tree-consistency`,
`unmarked-module`, and the `malformed` "no Component" manifestations.)*

---

## 6. Derived views

Computed, never stored:

- **Dotted path** per requirement.
- **Outline** — the requirement tree with dotted paths, statements, a rolled-up
  coverage mark, and a marker on each interior node showing whether it carries
  an `Interface:` yet.
- **Verification level** per requirement, from its tree position: root → system,
  interior → integration, leaf → unit (concept §5.1).
- **Reverse links** — a requirement's children; an actor's requirements.

In the PoC these are surfaced by the read-only commands `hamilton tree` (the
outline) and `hamilton show <ID>` (one entity — `R-nnnn` or `A-nnnn` — and what
refers to it). Each recomputes from `spec/` on every call
and writes nothing. There is no `hamilton graph`: a tree needs no tool to read.

---

## 7. Deliberately out of scope for the PoC

| Omitted | Why |
|---|---|
| Separate component model and module model | The interior of the requirement tree is the architecture; the code is the physical model (D-014). A parallel model is deferred until a system large enough to need one. |
| `Status` field | Its only structural job was never-reuse; the counter covers that (§3) |
| Explicit verification level | Derivable from tree position (§6) |
| Cross-cutting secondary links (`Also satisfies`) | Needed eventually; not needed to prove the method |
| Rationale / priority / owner fields | Additive later without migration |
| Rich test↔AC linkage; test-quality / falsification checking | Verification side. The PoC keeps only the minimum: `@covers` tags resolved against the requirements, and `.hamilton/verified` for staleness |
