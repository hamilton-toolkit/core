Bring the presentation of a Hamilton project in line with its design guide.
The behaviour is specified, built and verified; how the software looks and
feels is not -- it is described in `$guide`, and the engineer judges the
result by trying the software. Your job is that presentation, and only that.

## The design guide

```markdown
$text
```

$changes

## The reference files that changed

$refs

Read each one that is listed: a mockup or screenshot says what the words do
not.

## What to do

1. Find where the software's presentation lives -- templates, styles,
   components, theme files -- and realise **what changed** in the guide there,
   as well as you can. Where the guide is silent, change nothing.
2. **Presentation only.** Change no behaviour: no route, no handler, no rule,
   no data. Create no page, screen or component the software does not already
   have -- what it offers is the specification's business, not the guide's.
3. **Keep what the tests find things by**: accessible names, roles, visible
   labels and the text the specification names. Restyle an element; do not
   rename, remove or restructure it away.
4. **Run no tests**, and not the suite: Hamilton runs it once, at the end,
   and brings the coder back if anything broke.
5. **Never write, edit or copy a `#…` review suffix**, and leave test files
   alone.

You may not write to `spec/` -- the phase gate refuses it.

Finish with a short summary: what you changed, where, and anything in the
guide you could not realise and why.
