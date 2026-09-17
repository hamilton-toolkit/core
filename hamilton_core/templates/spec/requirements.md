# Requirements

This file is the whole model. It opens with a `## Verification methods`
section; after it, each requirement is a `## R-nnnn Short title` block with a
`Statement:` line and at least one `- AC<n>: ...` criterion. The title after
the id is what tools and reviewers show instead of a bare `R-nnnn`; keep it a
few words. Write each criterion as `<observable condition> -> <expected
outcome> [method]` when that shape fits.

- **`## Verification methods` defines how this project proves a criterion.**
  One bullet per method, `- **name** — description`, above the first
  requirement. The description says what is real and what is stubbed. The
  tool that runs it (Playwright, pytest, ...) is not part of it. `manual` is
  reserved: a person judges it, and `hamilton check` lists it but never
  enforces it.
- **Every criterion ends in its method marker**, e.g. `[browser]`. Two methods
  (`[unit, http]`) are allowed but rare; usually that is two criteria.
- **`Statement:` is one sentence, under 20 words, one behaviour.** Detail goes
  into the acceptance criteria, where `hamilton check` can act on it.
- **`Parent:` makes the tree.** A requirement with no `Parent:` is a *root* — a
  system-level goal — and must name the `Actor:` whose goal it is. Everything
  else has a `Parent:`. Write and ratify the root layer first.
- **`Actor:` is required on roots, and only meaningful there.** A root needs at
  least one criterion verified the way the actor reaches the system, not only
  `unit`.

`hamilton show R-nnnn` prints one requirement with its full path, statement,
criteria, methods and coverage; `hamilton tree` prints the whole outline — read
it upward to check each parent's children add up to it.

The worked example below sits in a fenced code block, so `hamilton check`
ignores it. Write your own methods and requirements **below** the fence, not
inside it. A freshly initialised project has zero real requirements and
`hamilton check` will say so until you add one.

```markdown
## Verification methods
- **http** — requests to the running service; external services stubbed and the calls they received asserted.
- **unit** — one module in isolation, no I/O.

## R-0001 Requests are authenticated
Actor: A-0001
Statement: Every inbound request is authenticated before it is routed.
Criteria:
- AC1: no token -> 401 [http]
- AC2: valid token -> the request reaches its handler [http]

## R-0002 Reject expired tokens
Parent: R-0001
Statement: The validator rejects a request whose exp claim is in the past.
Criteria:
- AC1: expired token -> 401 and no user data in the response body [http]
- AC2: token inside the 30s clock-skew window -> accepted [unit]
```
