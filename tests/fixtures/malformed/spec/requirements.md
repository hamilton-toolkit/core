# Requirements

## R-0001
Actor: A-0001
Statement: A well-formed requirement apart from one stray line.
this line is neither a field nor a criterion
Criteria:
- AC1: input -> output

## R-0002
Actor: A-0001
Statement: This requirement has no acceptance criteria.

## R-0003
Actor: A-0001
Criteria:
- AC1: this requirement has no Statement -> still flagged

## R-0004
Actor: A-0001
Statement: This requirement defines AC1 twice.
Criteria:
- AC1: first definition -> wins visually
- AC1: second definition -> would silently win
