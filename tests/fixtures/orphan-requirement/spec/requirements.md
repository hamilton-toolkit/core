# Requirements

## Verification methods
- **http** — requests to the running service; external services stubbed.
- **unit** — one module in isolation, no I/O.

## R-0001 Rows are stored
Statement: A valid row is written to the store.
Criteria:
- AC1: valid row -> persisted [http]
