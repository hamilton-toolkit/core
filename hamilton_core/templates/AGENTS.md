# Hamilton project

Specification-gated development with enforced verification. This file states the
rules for any coding agent working here; the `hamilton` skill / prompt carries
the workflows.

- Behaviour changes require a specification change first.
- Specification is top-down: ratify the root layer of actor goals before
  decomposing them into child requirements.
- If a write to `spec/` is denied, stop and report. Never work around it.
- `spec/requirements.md` is the whole model: one `Parent:` tree. A root names an
  `Actor:`; every other requirement names a `Parent:`. A requirement with
  children is a subsystem boundary and carries an `Interface:` line.
- Every test carries `@covers R-nnnn/ACn`, and every AC has a passing test that
  carries its tag.
- `.hamilton/` is read-only in build phase, except `.hamilton/config`: choosing
  the test framework and layout (`test_command`, `test_paths`) is a build-time
  call and yours to make. Do not touch any other key there.
- `hamilton check` passes before a merge request opens.
- A correct failing test is never edited to pass.
- An existing codebase gets its first spec with `hamilton reverse`: derive
  intent and the load-bearing decisions from the code and its history — do not
  transcribe the implementation. Expect the gate red on `uncovered` until a
  build phase binds tests to the derived criteria.
