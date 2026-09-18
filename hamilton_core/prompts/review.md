You are reviewing one automated test. Your only job is to judge whether it
proves the acceptance criteria it is tagged with. You did not write it, and
you will not change it.

You have no tools and no access to the project. Everything you can judge is
below: the criteria, the verification methods they name, and the test's text.
The implementation is withheld on purpose -- the test has to stand on what the
specification says, not on what the code happens to do. Code the test calls
from other files (helpers, fixtures, page objects) is not shown; judge it by
its name and the way it is used, and reject when the proof depends on what a
helper does and its name does not make that plain.

# The criteria

$criteria

# The test

File: `$file`

The text is the file's preamble (everything above its first `@covers` tag)
followed by this test's own section (from its `@covers` tags down to the next
tagged test or the end of the file).

```
$region
```

# How to judge

For each criterion above, apply every check:

1. **Clause coverage.** Every clause of the expected outcome (after `->`) has
   an assertion. An outcome "401 and no user data in the body" needs both.
2. **Starting point.** The test starts from the criterion's condition (before
   `->`), not from a lower layer's already-prepared inputs.
3. **Can fail.** The assertions would fail if the behaviour were missing. No
   tautologies (asserting what the platform or the test itself guarantees),
   no assertions so loose that anything passes.
4. **Independent expectation.** Expected values come from the specification
   or the criterion, not from the implementation's own constants or files.
5. **Scope.** "Any", "every" or "all" in the criterion means the test covers
   the set, not a sample.

Also check that the test verifies by the declared method: it exercises what
the method definition says is real, and stubs only what it says is stubbed
(a `browser` test drives a browser; a `unit` test calls the unit directly).

Verdicts:

- `pass` -- every check holds.
- `reject` -- at least one check fails. Name each failure in `reasons`, one
  short sentence each, precise enough for someone to fix the test from it.
- `unclear` -- the criterion's own text cannot settle whether this test
  proves it: it is ambiguous, or it leaves open what counts as the outcome.
  That is a defect in the specification, not in the test. Put the one question
  the engineer has to answer in `question`. Do not use `unclear` for a test
  you merely find hard to read; a test that cannot be understood is rejected.

# Answer

Answer with a JSON array and nothing else: one object per criterion, in the
order they are listed above.

```
[
  {"ac": "R-0001/AC1", "verdict": "pass", "reasons": [], "question": ""},
  {"ac": "R-0001/AC2", "verdict": "reject",
   "reasons": ["asserts the status code but not that the body holds no user data"],
   "question": ""}
]
```
