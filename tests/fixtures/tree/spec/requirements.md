# Requirements

## R-0001 Authentication
Actor: A-0001
Interface: Every inbound HTTP request carries a bearer token; the reply is 200 or 401.
Statement: The system authenticates every inbound request before routing it.
Criteria:
- AC1: request with no credential -> 401

## R-0007 Sessions
Parent: R-0001
Interface: A session id issued at login and presented on later requests.
Statement: An authenticated caller is represented by a session over time.
Criteria:
- AC1: a session id is opaque and not guessable

## R-0042 Reject expired tokens
Parent: R-0007
Statement: The auth middleware rejects a request whose token exp claim is in the past.
Criteria:
- AC1: expired token -> 401 and no user data in the response body
- AC2: token inside the 30s clock-skew window -> accepted
