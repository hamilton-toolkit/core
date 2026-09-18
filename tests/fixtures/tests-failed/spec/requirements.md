# Requirements

## Verification methods
- **http** — requests to the running service; external services stubbed.
- **unit** — one module in isolation, no I/O.

## R-0001
Actor: A-0001
Statement: The validator accepts a well-formed token.
Criteria:
- AC1: valid token -> accepted [http]
