# Hamilton project

Specification-gated development with enforced verification. This file states the
rules for any coding agent working here; Hamilton's commands (`hamilton design`,
`hamilton build`) carry the workflows and brief each agent they start.

- Behaviour changes require a specification change first.
- Specification is top-down: ratify the root layer of actor goals before
  decomposing them into child requirements.
- If a write to `spec/` is denied, stop and report. Never work around it.
- `spec/requirements.md` is the whole model: one `Parent:` tree. A root names an
  `Actor:`; every other requirement names a `Parent:`. Every acceptance
  criterion ends in its verification method, e.g. `[browser]`, defined once
  in `## Verification methods` at the top of the file.
- Every test carries `@covers R-nnnn/ACn`, and every AC has a passing test
  **under the paths of its verification method** that carries its tag. `manual`
  criteria are the exception: a person verifies them.
- Every such tag carries a review suffix, `@covers R-nnnn/ACn #xxxxxx.yyyyyy`,
  which only the reviewer in `hamilton build` writes, when it passes the test. A
  change to the AC or to the test clears it. Never write or edit a suffix.
- `.hamilton/` is read-only in build phase, except `.hamilton/config`: choosing
  the test framework, the layout and how the software starts
  (`test_command`, `paths.<method>`, `run.<method>`, `start_command`) is a
  build-time call and yours to make. Do not touch any other key there.
- `hamilton verify` passes before a merge request opens.
- A correct failing test is never edited to pass.
- An existing codebase gets its first spec with `hamilton reverse`: derive
  intent and the load-bearing decisions from the code and its history — do not
  transcribe the implementation. Expect the gate red on `uncovered` until a
  build phase binds tests to the derived criteria.
