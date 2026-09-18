# Requirements

## Verification methods
- **http** — requests to the running service; external services stubbed.
- **unit** — one module in isolation, no I/O.

## R-0001 The system works
Actor: A-0001
Statement: The system does its job.
Criteria:
- AC1: request -> response [http]

## R-0002 A detail
Parent: R-0999
Statement: A detail of the job.
Criteria:
- AC1: edge case -> handled [http]
