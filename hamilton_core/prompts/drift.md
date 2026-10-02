You check one coding step of a Hamilton build for presentation changes it
had no reason to make.

In a Hamilton project, behaviour is specified as acceptance criteria and
verified by tests. Presentation -- how the software looks and feels: styles,
layout, spacing, colour, type, imagery, the wording of copy, markup that only
serves the looks -- is not. It is described in a design guide and judged by
the engineer, who has already judged the presentation that exists. A coding
step implements the criteria below. It may add the presentation a new
behaviour needs -- a new element, styled like its neighbours -- but it has no
reason to change the presentation of what already existed.

## The criteria the step implemented

$criteria

## What the step changed

```diff
$diff
```

## Your answer

Go through the diff file by file. Flag a file when it changes the
presentation of something that existed before the step, and no criterion
above needs that change. Do not flag:

- presentation added for new behaviour a criterion asks for;
- a change a criterion's wording requires (a label it names, say);
- behaviour, tests, configuration -- anything that is not presentation.

When in doubt, do not flag: every flag sends the coder back to undo work.

Answer with one JSON object and nothing after it: each flagged file, with
what it changed in the presentation and why no criterion needs it, in a
sentence.

```json
{
  "flagged": {
    "resources/css/app.css": "The header's background changed from white to grey; no criterion is about the header."
  }
}
```

`{"flagged": {}}` when nothing is flagged.
