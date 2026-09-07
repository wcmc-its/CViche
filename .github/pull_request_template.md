## What changed

<!-- One paragraph: what behaves differently once this merges. -->

## What did not change

<!-- Code that moved, was renamed, or was reformatted without changing behaviour.
     Write "none" if none. This tells a reviewer where to spend attention, it
     does not ask them to skip anything. -->

## How it was verified

<!-- The command and its output. "Tests pass" is not verification. -->

Closes #

## Before requesting review

- [ ] Base branch is `dev`. A PR based on another feature branch blocks its whole
      chain and auto-closes no issue.
- [ ] One `Closes #N` per issue — a comma list closes only the first.
- [ ] CI green on this PR, not just locally.
- [ ] Every signature or import line I touched uses builtin generics and
      `X | None` — no `typing.List` / `Dict` / `Optional` / `Tuple` / `Set`.
- [ ] Every function I added has parameter and return annotations, with
      `object` (not `Any`) for accept-anything helpers.
- [ ] No `print()` added in library code (`src/unified_pipeline` outside
      `scripts/` and `tests/`).
- Say in the description, not as a tick: the `docs/CODING_STANDARDS.md` §8
  judgement calls — a typed record at a boundary (§8.1), a named constant for
  a classifying literal (§8.2) — and why, per "What a PR description must
  contain".
