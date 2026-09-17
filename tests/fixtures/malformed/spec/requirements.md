# Requirements

## Verification methods
- **http** — requests to the running service; external services stubbed.
- **unit** — one module in isolation, no I/O.

## R-0001
Actor: A-0001
Statement: A well-formed requirement apart from one stray line.
this line is neither a field nor a criterion
Criteria:
- AC1: input -> output [http]

## R-0002
Actor: A-0001
Statement: This requirement has no acceptance criteria.

## R-0003
Actor: A-0001
Criteria:
- AC1: this requirement has no Statement -> still flagged [http]

## R-0004
Actor: A-0001
Statement: This requirement defines AC1 twice.
Criteria:
- AC1: first definition -> wins visually [http]
- AC1: second definition -> would silently win [http]
