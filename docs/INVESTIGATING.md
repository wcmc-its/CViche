# Investigating CViche accuracy problems

Failure modes that have cost real time on this project more than once. Read before
starting an accuracy investigation, and add to it when something new bites.

Each entry is here because it has already produced a wrong conclusion, a wasted
session, or a shipped regression — not because it is theoretically possible.

## Do not compare run outputs by naive string diff

Stages 5c and 5d reformat teaching entries and citations, and rendered lists
renumber. The same publication legitimately appears as
`jane a doe, richard roe, mei-lin chan...` in one run and
`Doe JA, Roe R, Chan M...` in another.

A naive diff during the C0ZGFW post-mortem reported **151 records lost**. Every one
was a reformatting or renumbering artefact. Zero were real.

Neutralise before comparing: strip leading list numbering and bullet markers,
normalise whitespace and case, and expect author-name and citation-format churn.
Prefer distinctive-token matching over whole-string equality.

## Do not trust a single roll

Stage 3b classification varies between runs on identical input. Three runs of the
same file produced **278, 268 and 324** entries.

Any classification finding, and any before/after comparison of a classification
change, needs at least two rolls. A single run can manufacture both a false win and
a false regression.

## Do not treat an unchanged doctor verdict as "no regression"

`run_doctor` has no duplication lint. A change that added **122 duplicate rendered
lines** left the verdict identical at 6 WARN / 1 INFO.

The quality score is not a backstop either: its "duplicate-entry ratio" dimension
measures duplicate *entries at stage 3b*, not duplicate *rendered output*, and moved
0.079 -> 0.082 across the same change. The nearest real signal, `echo_paragraphs`,
sat on a dimension already floored at 0/10 — so a genuine regression was invisible
inside an already-failing metric.

Until those lints exist, "the metrics didn't move" is not evidence.

## Do not validate a rule with its own definition

A corpus gate that re-implements the predicate under test will confirm it. PR #419
originally reported "0 drops with an uncovered line" as a gate result; the check
computed uncovered lines only for entries that had already passed the coverage test,
so the answer was 0 by construction.

A validation is only informative where the check and the code under test *can*
disagree. Prefer checking against an independent source — the source `.docx`, a
different similarity measure, a manual read of a sample — over a mirror of your own
logic.

Related: "deterministic" is not a synonym for "correct." A deterministic rule that
misfires misfires every time, reproducibly.

## Do not change table indices in `extract_docx_structure`

The reader assigns tables a **string** idx (`table_N`) while paragraphs get ints.
Stage 2 depends on that shape via `startswith` checks and sort keys.

Fix table-index problems at the consumer boundary, never at the source. This has
bitten before.

## Do not tune dedup thresholds to fix duplication or loss

The dedup thresholds are load-bearing across every CV. Both the C0ZGFW content loss
and the duplication that replaced it originated in stage 2 emitting an entry that
should not have existed — dedup was choosing between two representations of the same
content.

**When a fix has to decide which of two representations to keep, the upstream
duplication is the bug.** Adjusting the arbitration is motion without progress.

## Corpus gating: collapse to distinct CVs first

The S3 corpus is duplicate-heavy — **99 runs are 21 distinct CVs**. Quoting "13 of 99
runs" overstates coverage; quote distinct CVs. A corpus rate bounds a bug class, it
does not measure prevalence in the wild.

```bash
aws s3 sync s3://wcm-cviche-storage/cviche/runs/ <dir> \
    --exclude '*' --include '*/outputs/*_fields.json'   # or *_entries.json
```

Read-only via IAM user `reciter`. Collapse by content signature or CV owner before
counting.

## Probe before declaring something unavailable

Assertions that S3, a database, a pod or a tool is inaccessible have been wrong here.
One `aws s3 ls` refuted a claim that blocked two issues. An untested constraint is a
guess; run the one-line probe.

## Check the deployed image before blaming code

The running pod can lag the branch you are reading. Confirm the deployed image tag
before attributing behaviour to current source, and re-ground symbol and line
references via `git show origin/dev:<path>` when the working tree is behind.

## Issue titles are not diagnoses

Issue #208 read "stop fusing multi-row tables in stage 2." Nothing fused — the reader
returned every table with full row data and the splitter produced correct per-row
entries. The real defects were an index collision and a subset-comparison bug, two
stages apart from where the title pointed.

Re-derive the mechanism from artifacts before trusting a title, and correct the title
when you find it wrong.
