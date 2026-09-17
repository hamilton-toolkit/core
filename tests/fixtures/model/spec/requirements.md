# Requirements

## Verification methods
- **http** — requests to the running service; external services stubbed.
- **unit** — one module in isolation, no I/O.

An example, fenced so the view commands ignore it:

```markdown
## R-9999 Should Not Appear
Statement: ignored, this block is inside a fence.
- AC1: ignored -> ignored
```

## R-0100 Run the shop
Actor: A-0001
Statement: An operator runs the shop through the web UI.
Criteria:
- AC1: the shop is reachable -> the home page renders [http]

## R-0001 Create a customer
Parent: R-0100
Statement: A customer is created from a valid payload.
Criteria:
- AC1: valid payload -> 201 and a customer id [http]

## R-0004 Reject an invalid name
Parent: R-0001
Statement: Customer creation rejects an invalid name.
Criteria:
- AC1: empty name -> 422 [unit]
- AC2: name over 200 characters -> 422 [unit]

## R-0007 Order references a customer
Parent: R-0100
Statement: An order references an existing customer.
Criteria:
- AC1: unknown customer id -> 404 [http]
