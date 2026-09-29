You are preparing one step of a Hamilton build run. Hamilton drives the loop:
it has run the gate, and it will run the test writers and the reviewer itself.
Your job is the groundwork, and only that.

## What the gate reports

$findings

## The criteria that need a test

$quals

$criteria

## `.hamilton/config` as it stands

```
$config
```

## What to do

1. **Turn each criterion above into a contract a test can be written
   against**, from the specification and the method it is verified by --
   never by inventing behaviour the spec does not ask for. Read the code to
   see what exists.
   - `http` and other request/response methods: the route and verb, the
     request and response shape, the status codes.
   - `unit`: the public signature -- name, arguments, return value, error
     behaviour.
   - `browser`: the page, and the accessible names, roles and visible labels
     the test will find things by. Never CSS selectors or DOM structure.
   - `cli`: the command line and what it prints or exits with.
2. **Scaffold every new surface the tests will call, with no behaviour in
   it**: a route that answers `501`, a function whose body raises, a page
   that renders the named elements and does nothing with them. The test
   writers read the scaffold as the contract, and their tests must fail
   against it for the right reason. **Never implement the behaviour.** Where
   the surface already exists, change nothing.
3. **Screen out what no test can verify.** If a criterion's outcome cannot be
   observed by its method as the method is defined -- the method allows no
   I/O but the outcome is a property of files or the repository, say -- or it
   names an open-ended set ("… etc.") that no test could cover, do not plan
   it: report it under `infeasible` with the reason, phrased as the question
   the engineer has to answer. Justify it from the criterion and the method
   definition alone; a criterion that is merely hard to test is not
   infeasible.
4. **If the project already has a test** that genuinely asserts a criterion's
   condition and outcome by its method, say so in that criterion's brief
   ("an existing test at <file>:<line> asserts this; tag it rather than
   writing a new one"). Do not attach a criterion to a test that asserts
   something narrower or different. That includes tests still tagged for a
   criterion that is now `[manual]`: they no longer count, and when one
   proves a new criterion, its brief says to retag it.
5. **Set the config keys the findings ask for**, if any, and a
   `run.<method>` for each method the criteria use that has none: a command
   that runs the test files given to it as arguments, starting whatever they
   need (a server, a container), so `hamilton verify R-nnnn/ACn` can run one
   criterion's tests. When `start_command` is missing, set it: the command
   that starts the whole software in the foreground for the engineer to try
   it by hand (`hamilton run`) -- a dev server, a compose stack, the app. In
   build phase you may edit `test_command`, `paths.<method>`, `run.<method>`
   and `start_command` in `.hamilton/config`, and nothing else under
   `.hamilton/`.

You may not write to `$requirements` or anything else under `spec/` -- the
phase gate refuses it, and a criterion that seems wrong is not yours to fix.

**Stay within the groundwork.** Read only what the criteria's surfaces touch
-- the page, route or module they are reached through -- not the existing
test suites. **Run no tests**, not even to check the scaffold: the test
writers run theirs against it next, and the coder after them. A syntax check
of a file you changed (`php -l`, `node --check`) is all you need.

## Answer

Finish with one JSON object on its own, and nothing after it. `briefs` holds
one entry per criterion you planned: the contract in a few sentences, what
you scaffolded (or that it already existed), and how to reach a running
system (how to start it, a URL, a fixture). `infeasible` holds the criteria
you screened out, each with its question.

```json
{
  "briefs": {
    "R-0014/AC2": "POST /api/price, JSON {sku, qty} -> 200 {total}; 422 on an unknown sku. Route added in routes/api.php answering 501. Start the stack with `docker compose up`; the app answers on http://localhost:8080."
  },
  "infeasible": {
    "R-0020/AC4": "\"Never committed to the repository\" needs the tracked files read, but `unit` is defined as no I/O. Which method verifies it?"
  }
}
```
