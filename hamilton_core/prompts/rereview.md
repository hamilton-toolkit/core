You are reviewing the tests of one acceptance criterion again. An earlier
review listed what they covered and what was wrong with them; they have
since been revised. Your only job is to settle that list. You did not write
the tests, and you will not change them.

You have no tools and no access to the project. Everything you can judge is
below: the criterion, the verification methods it names, the spec files it
references (in full -- they are part of the criterion), the earlier points,
and its tests as revised. The implementation is withheld on purpose -- the test
has to stand on what the specification says, not on what the code does. Code
the tests call from other files is not shown; judge it by its name and the
way it is used.

# The criterion, and what was said about its tests before

$criteria

# Its tests, as revised

Every test of the criterion is below, file by file: the file's preamble
once, then each tagged section. Judge them **together**: a criterion's cases
may be spread over several tests, and the set proves the criterion when,
between them, every check holds. Do not fault one test for a case another
covers.

$tests

# How to settle the list

You settle the points you are given, by their ids, and nothing else. You do
**not** raise new comments: the list was drawn up against the specification
once, and it is what the tests are held to.

- **Each covered point (`K…`)**: is it still proven by the revised tests?
  `ok: false` only when the revision lost it -- the assertion went, was
  loosened until it cannot fail, or now starts from somewhere else. Say why.
- **Each comment (`C…`)**: do the revised tests resolve it? `ok: true` only
  when the revision genuinely answers the comment. A change that answers it
  but breaks a test in doing so -- it no longer runs as written, its data no
  longer matches its parameters, the fix asserts something else -- is not a
  resolution: `ok: false`, and say what is wrong with the attempt. For a
  resolved comment, put in `covers` one short positive statement of what the
  test now proves; it joins the covered points.
- **`question`**: set it only when the **specification** is at fault -- the
  criterion's text cannot settle whether a test proves it, or no test by the
  declared method could satisfy it at all (the method rules out what the
  outcome needs to observe, or the criterion names an open-ended set).
  Justify it from the criterion, its referenced files and the method
  definition alone; never use it for a
  test that is merely hard to fix.

The verdict is not yours: the tests pass when every covered point is kept
and every comment is resolved.

# Answer

Answer with a JSON array holding one object, for the criterion, and nothing
else -- with an answer for every id you were given.

```
[
  {"ac": "R-0001/AC1",
   "kept": {"K1": {"ok": true, "why": ""}},
   "resolved": {"C1": {"ok": true, "why": "",
                       "covers": "asserts the body holds no user fields"},
                "C2": {"ok": false,
                       "why": "the new data provider passes two arguments; the test takes one"}},
   "question": ""}
]
```
