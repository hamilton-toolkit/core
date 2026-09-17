# Agent instructions

Instructions for any AI agent working on this repository.

Claude Code reads `CLAUDE.md`, not this file. Create a local `CLAUDE.md`
containing `@AGENTS.md` (or symlink it); it is gitignored on purpose.

## Code architecture

- **One source of truth.** Every rule, decision or fact lives in exactly one
  place. Duplicated *knowledge* is a bug. Code that only looks similar but can
  change for different reasons is not duplication; do not merge it.
- **Simple and readable.** Prefer the plain solution over the clever one. Code
  is read far more often than it is written.
- **Short, expressive names.** A function, class or variable name says what it
  is or does, so the reader does not need its body or a comment to know.
- **Vendor code stays behind its adapter.** Hamilton's session logic and UX
  depend on its own interfaces, never on a specific AI provider's SDK.
- **Tests may repeat themselves** when that makes each test readable on its own.

## Git

- **Gitflow.** Work follows the gitflow branching model.
- **Commit messages** are concise: one short line saying what changed. No
  attribution trailers such as `Co-Authored-By`.

## GitHub issues

- **Concise.** Say what is needed, not everything that is known.
- **Abstract.** An issue may be picked up long after it was written, when the
  code has changed. Leave out file names, functions and config keys unless
  they are the subject of the issue (a bug in a specific place, or an
  interface the work has to build on).
- **Structure.** A short introduction, then bold headings with bullet lists:
  - Features: introduction, **Current State**, **Expected State**
  - Bugs: introduction, **Steps**, **Actual Result**, **Expected Result**
