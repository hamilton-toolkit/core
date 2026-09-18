# Requirements

## Verification methods
- **http** — requests to the running service; external services stubbed.
- **unit** — one module in isolation, no I/O.

An example, fenced so `hamilton check` skips it:

```markdown
## R-9999
Statement: ignored, this block is inside a fence.
- AC1: ignored -> ignored
```

## R-0001
Actor: A-0001
Statement: The token validator rejects a request whose exp claim is in the past.
Criteria:
- AC1: expired token -> 401 and no user data in the response body [http]
- AC2: token inside the 30s clock-skew window -> accepted [http]
