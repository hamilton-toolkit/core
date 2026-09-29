# Design guide

How this software should look and feel — its presentation. This is intent,
not a model: `hamilton verify` does not read it, no criterion references it,
and no test asserts it. The build agents realise it as well as they can, and
you judge the result by trying the software (`hamilton run`, `hamilton
validate`). What the software *does* — what an actor can do and observe —
belongs in `spec/requirements.md`, not here.

It is phase-gated like the rest of `spec/`: writable in spec phase, read-only
in build. Reference files that say more than words can — a mockup, a
screenshot, a logo, a palette — go under `spec/design/` and are named here by
path, e.g. `spec/design/home.png`. Leave a section out when you have nothing
to say about it.

## Intent

<One or two sentences: the impression the software should make, and on whom.>

## Look and feel

- <Palette, type, spacing, imagery, iconography.>

## Layout and responsiveness

- <How screens are arranged, and how they adapt to small and large screens.>

## Components and states

- <How recurring elements look: buttons, forms, messages, empty and loading
  states.>

## Tone of copy

- <How the software speaks to its users.>

## References

- <`spec/design/<file>` — what it shows, and what to take from it.>
