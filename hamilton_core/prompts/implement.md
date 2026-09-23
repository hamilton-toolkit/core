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

Run them with `hamilton verify <criterion>` (`R-nnnn/ACn`, or `R-nnnn` for
all of a requirement's): it runs that criterion's tests and nothing else,
and shows its failures and its status. If it says a `run.<method>` is not
set, set it in `.hamilton/config` first -- a command that runs the test files
given to it as arguments, starting whatever they need -- and run it again. Do **not** run the full suite
(`$command`) while you work: it runs every criterion's tests and takes
minutes. Hamilton runs it once, at the end, and brings you back if anything
else broke.

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
4. If a review suffix is copied (a `copied-suffix` finding), delete the copy
   -- the scratch file or extract that is not the criterion's own test -- or
   its tag.

## What you may change, and what you may not

- **The implementation** is yours: it is the repair surface.
- **A test** may be changed only when it misreads its criterion, and only with
  that criterion's text as the justification. A correct failing test is a bug
  in the implementation — never edit a test to make it pass. Editing a test
  sends it back to the reviewer, which is Hamilton's business, not yours.
- **Never write, edit or copy a `#…` review suffix.** The guard refuses it.
- **Scratch files stay out of the test paths** -- a debug script, an
  extract of a test. A file there that holds a `@covers` tag counts as a
  test of that criterion. Put them in a git-ignored directory, and delete
  them before you finish. Never copy a test file.
- **An acceptance criterion is immutable here**, and `spec/` cannot be written
  in build phase at all. If a criterion turns out to be wrong or impossible,
  stop and say so plainly in your answer rather than working around it — the
  engineer takes it back to `hamilton design`.

Finish with a short summary: what you implemented, what you changed to get the
suite green, and anything you could not do and why.
