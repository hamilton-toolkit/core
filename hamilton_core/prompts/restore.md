You just implemented a step of a Hamilton build. A reviewer found that it
changed the presentation of what already existed -- how the software looks
-- where no criterion asked for it. The presentation is the engineer's, set
by the design guide and judged by trying the software; a coding step leaves
it as it was.

## What the reviewer flagged

$flagged

## The criteria the step implemented

$criteria

## What to do

For each file above, undo the presentation change the reviewer names, and
keep every behaviour change: the criteria's tests must still pass. Run them
with `hamilton verify <criterion>` (never the full suite, `$command`).

If a criterion does need a flagged change, keep it, and say which criterion
and why. Do not change anything else.

Finish with one JSON object on its own, and nothing after it: the files where
you kept a flagged change, each with the reason.

```json
{"kept": {"src/views/signup.html": "R-0004/AC2 names the label \"Create account\"."}}
```

`{"kept": {}}` when you restored everything.
