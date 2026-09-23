Write the tests for one acceptance criterion of a Hamilton project, $qual.

You are given the criterion and the contract it is reached through. You may
read the code -- the scaffold, and whatever already exists -- to write a test
that actually runs against it. But the test proves **the criterion**, not the
code: every expected value comes from the specification, never from the
implementation's own constants, and a reviewer who sees only the spec and
your test will hold you to that.

## The criterion

$criterion

## The contract

$brief

## Where its tests go

Under: $paths

**In a file of their own**, holding this criterion's tests and nothing else,
with `$name` in its name, following the project's naming convention for test
files (a PHPUnit class `R0020AC3Test.php`, a Node `r-0020-ac3.test.js`, …).
If the criterion's tests live in a file shared with other criteria today,
move them into that file as part of this change, and take them out of the
shared file -- leaving the other criteria's tests there exactly as they were.
Split the criterion's cases over as many tests in that file as reads well:
the reviewer judges them together.

## The tests of this criterion the reviewer rejected, and why

$reasons

## What the test must do

A separate reviewer will see only the specification above and the test you
write — no implementation, no other files. It passes the test only when all
of these hold:

1. **Clause coverage** — every clause of the expected outcome has an
   assertion.
2. **Starting point** — the test starts from the criterion's condition, not
   from a lower layer's already-prepared inputs.
3. **Can fail** — the assertion would fail if the behaviour were missing. No
   tautologies, no assertion so loose that anything passes.
4. **Independent expectation** — expected values come from the criterion
   and the spec files it references (read them: they are part of it), not
   from the implementation's own constants or files.
5. **Scope** — "any" or "every" in the criterion means the test covers the
   set, not one sample.
6. **The declared method** — a `browser` criterion is proved by driving a
   browser, an `http` one over HTTP, a `unit` one against the unit's public
   surface.

Also:

- Each test carries the comment `@covers $qual` on its own line directly
  above it — the tag text is matched, not the comment syntax. One criterion per tag.
  Put it in a **line comment** (`// @covers $qual`, `# @covers $qual`), never
  inside a docblock: some frameworks (PHPUnit) read `@covers` in a docblock as
  their own annotation. Above a docblock, the tag goes on the line before `/**`.
- **The file must still parse and the test must still run** after your
  change. Run it and read the output: a syntax error, a data provider whose
  values do not match the test's parameters, or a test the runner skips is
  not a failing test, it is a broken one.
- **Never write, edit or copy a `#…` review suffix.** Only the reviewer writes
  one, and the guard refuses it.
- The reviewer sees the file's preamble (everything above the first `@covers`)
  and your test's own section. It cannot open other files, and judges helpers
  by their names — so name them for what they do.
- **Small and plain.** Prove the criterion as written with the fewest,
  plainest tests that do it: one per case it names. No generic discovery,
  crawling or comparison frameworks, no guarding against markup or content
  the specification does not name. A long test is not a thorough one -- it is
  one the reviewer has more to find in. **At most $budget lines** for the
  criterion, counting the preamble of each file its tests are in: longer is
  sent back unread.
- Write only test files. Do not touch the implementation, and do not make
  the test pass by changing what it tests.
- **Run it -- and only it -- with `hamilton verify $qual`.** It runs this
  criterion's test files and nothing else, and shows the failures. If it
  says a `run.<method>` is not set, set it in `.hamilton/config` -- a command
  that runs the test files given to it as arguments, starting whatever they
  need -- and run it again. **Never run the full suite** (`$command`): it
  runs every criterion's tests and takes minutes; Hamilton runs it once, at
  the end. For behaviour that does not exist yet your test must fail --
  against the scaffold, for the reason the criterion names. A test for new
  behaviour that passes before anything is implemented proves nothing: fix
  it.

Finish with one line saying what you wrote and where, and whether it failed
or passed when you ran it.
