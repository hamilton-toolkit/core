# Requirements

This file is the whole model. Each requirement is a `## R-nnnn Short title`
block with a `Statement:` line and at least one `- AC<n>: ...` criterion.
The title after the id is what tools and reviewers
show instead of a bare `R-nnnn`; keep it a few words. Write each criterion as
`<observable condition> -> <expected outcome>` when that shape fits.

- **`Statement:` is one sentence, under 20 words, one behaviour.** Detail goes
  into the acceptance criteria, where `hamilton check` can act on it.
- **`Parent:` makes the tree.** A requirement with no `Parent:` is a *root* — a
  system-level goal — and must name the `Actor:` whose goal it is. Everything
  else has a `Parent:`. Write and ratify the root layer first.
- **The interior of the tree is the architecture.** A requirement with children
  is a subsystem boundary: give it an `Interface:` line naming what crosses that
  boundary. That line is the integration-test surface. It is
  fine to leave it blank while the subsystem is still being decomposed —
  `hamilton check` only advises.
- **`Actor:` is required on roots, and only meaningful there.**

`hamilton show R-nnnn` prints one requirement with its full path, statement,
criteria and coverage; `hamilton tree` prints the whole outline — read it upward
to check each parent's children add up to it.

The worked example below sits in a fenced code block, so `hamilton check`
ignores it. Write your own requirements **below** the fence, not inside it. A
freshly initialised project has zero real requirements and `hamilton check`
will say so until you add one.

```markdown
## R-0001 Requests are authenticated
Actor: A-0001
Interface: HTTP bearer-token header on every inbound request.
Statement: Every inbound request is authenticated before it is routed.
Criteria:
- AC1: no token -> 401
- AC2: valid token -> the request reaches its handler

## R-0002 Reject expired tokens
Parent: R-0001
Statement: The validator rejects a request whose exp claim is in the past.
Criteria:
- AC1: expired token -> 401 and no user data in the response body
- AC2: token inside the 30s clock-skew window -> accepted
```
