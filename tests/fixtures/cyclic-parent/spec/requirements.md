# Requirements

## Verification methods
- **http** — requests to the running service; external services stubbed.
- **unit** — one module in isolation, no I/O.

## R-0001 One
Parent: R-0002
Statement: The first half of a loop.
Criteria:
- AC1: a -> b [http]

## R-0002 Two
Parent: R-0001
Statement: The second half of a loop.
Criteria:
- AC1: b -> a [http]
