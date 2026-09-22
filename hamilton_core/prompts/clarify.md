An acceptance criterion in a Hamilton specification could not settle what its
tests must prove. The engineer who owns the specification has answered the
question it raised. Draft the amendment to the requirement that says what
they meant -- and nothing more. The engineer will see it and approve it
before anything is written.

## The requirement

$rid "$title"
Statement: $statement

Its criteria:

```
$criteria
```

## The criterion in question: $acid

## The question it raised

$question

## The engineer's answer

$answer

## The verification methods the specification defines

$methods
- `$manual` -- verified by a person, not by an automated test.

$earlier## What the amendment may do

- **Reword $acid** so a reader who never sees this question can no longer be
  unsure: name a closed set instead of "etc.", state a boundary and which
  side of it counts.
- **Change $acid's method** to another one above -- `$manual` when the
  answer says a person has to verify it.
- **Add new criteria** beside it, when the answer asks for behaviour the
  requirement does not have a criterion for yet.

Each criterion is one line: `<condition> -> <outcome> [method]`, with a
method from the list above. Change nothing the answer does not touch, and do
not touch any other criterion.

If the answer needs a method the specification does not define yet, do not
invent one: answer `NEW METHOD NEEDED: <what it would have to observe>` and
nothing else. A new method is design work, done in `hamilton design`.

## Answer

The criterion first, with its id, then one `- NEW:` line per added criterion
-- Hamilton numbers them -- and nothing else:

```
- $acid: <condition> -> <outcome> [method]
- NEW: <condition> -> <outcome> [method]
```
