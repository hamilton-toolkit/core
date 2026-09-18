# Requirements

## Verification methods
- **http** — requests to the running service; external services stubbed.
- **unit** — one module in isolation, no I/O.

## R-0001 Session lifecycle
Actor: A-0001
Statement: When a caller authenticates the system issues a session, refreshes it on each request within the idle window, rejects any request whose token exp claim is in the past, and revokes the session on logout or after seven days.
Criteria:
- AC1: authenticated caller -> a session id is issued [http]
- AC2: request inside the idle window -> session refreshed [http]

## R-0002 Well within budget
Actor: A-0001
Statement: The token validator rejects a request whose exp claim is in the past.
Criteria:
- AC1: expired token -> 401 [http]
