Write the implementation for one step of a Hamilton build run. The tests are
already written and a reviewer has passed them against the specification; your
job is to make them true, and to leave the project's own suite green.

## What the gate reports

$findings

$failures

## The criteria now under test

$criteria

## The tests to run while you work

$tests

Run only these, with the project's runner for each file (`phpunit <file>`,
`npx playwright test <file>`, `node --test <file>` …). Do **not** run the
full suite (`$command`) while you work: it runs every criterion's tests and
takes minutes. Hamilton runs it once, at the end, and brings you back if
anything else broke. `hamilton verify R-nnnn/ACn` shows one criterion's status
-- tagged, reviewed -- in a second, without running any tests.

## What to do

1. Read the tests that cover these criteria and the code they exercise, then
   implement the behaviour the criteria describe. Implement exactly what they
   ask for — code no criterion asks for is deleted, not kept.
2. Run the tests listed above until they pass. When this step is here
   because the suite failed, run the failing tests its output names, and fix
   them.
3. If a tagged test points at a criterion that no longer exists (an
   `orphan-tag` finding), delete the test and whatever only it needed, or
   retarget the tag if the behaviour moved to another requirement.

## What you may change, and what you may not

- **The implementation** is yours: it is the repair surface.
- **A test** may be changed only when it misreads its criterion, and only with
  that criterion's text as the justification. A correct failing test is a bug
  in the implementation — never edit a test to make it pass. Editing a test
  sends it back to the reviewer, which is Hamilton's business, not yours.
- **Never write, edit or copy a `#…` review suffix.** The guard refuses it.
- **An acceptance criterion is immutable here**, and `spec/` cannot be written
  in build phase at all. If a criterion turns out to be wrong or impossible,
  stop and say so plainly in your answer rather than working around it — the
  engineer takes it back to `hamilton design`.

Finish with a short summary: what you implemented, what you changed to get the
suite green, and anything you could not do and why.
