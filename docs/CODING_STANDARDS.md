# CViche coding standards

The code-level companion to `DEV_WORKFLOW.md`, which covers process (branching, merging, corpus batches). This covers the code itself: module boundaries, where things live, function responsibility, state, error handling, and what a test has to prove.

Every rule here is earned by something that actually went wrong in this repository. Where a rule has an incident behind it, the incident is named — a rule nobody can trace to a real failure is a rule nobody follows.

## How to read this

Rules are marked **[gate]** or **[judgement]**.

- **[gate]** — mechanically checkable, and a reviewer may reject on it without discussion. Each states its check.
- **[judgement]** — a question the author answers in the PR description. Not a veto; an obligation to have thought about it.

Nothing here is retroactive. Existing violations are listed so the direction is unambiguous, not so anyone is expected to fix them on sight. New and touched code meets the standard.

### Two questions, two authorities

Minimalism and readability pull against each other on real diffs — the shortest version of a thing and the clearest version of it are often not the same. Rather than re-argue that on every PR, they are separated by which question is being asked:

| Question | Decided by |
|---|---|
| **Should this code exist at all?** New helper, new abstraction, new dependency, new file, new config mechanism. | Minimalism. Default is no. Does the standard library or an existing dependency already do it; can the requirement be dropped; would deleting something serve better than adding. |
| **Given that it exists, how should it read?** Naming, structure, compactness, where it lives, what tests it carries. | This document. Explicit over compact, one responsibility, boundaries in §1, tests per §6. |

So "it's fewer lines" is a reason to *not add* something, and never a reason to write an existing thing more densely. Conversely §3.6's push toward explicit code is not a licence to add an abstraction, a wrapper, or a file — §3.8 and the table above both close that door.

The practical test for anything new: **it earns its place by removing more than it adds.** Report the net line delta in the PR when introducing a module, layer or interface.

## 1. Module boundaries

**1.1 Dependencies run one way, and the direction is written down. [gate]**
Every package `__init__.py` states what it contains and which packages it may import. The precedent is `stage6/__init__.py`: *"Dependencies run one way and must keep doing so… Nothing here may import `stage_6_word_template` — a back-edge is an import cycle and fails at load, not at render."*
*Check:* an import test per package. A cycle already fails at load, which is the cheapest possible enforcement.

**1.2 Pure layers import no I/O library. [gate]**
Parsing, normalization, sorting and resolution take data and return data. They do not import `docx`, `boto3`, `requests`, or a DB session.
*Why:* this is what makes them testable without a fixture and reviewable without context. It already holds — `stage6/{parsing,normalization,resolution,sorting}` is 1,326 lines with zero `docx` imports.
*Check:* `grep -rl 'import docx\|from docx' <pure package>/` returns nothing.

**1.3 Peers do not import peers. [gate]**
Modules at the same level of a package — most importantly `stage6/sections/*` — import shared helpers, never each other.
*Why:* this is the structural half of the anti-bleed rule (§4.4). A section that cannot reach another section cannot corrupt it.
*Check:* an import test asserting no `sections.*` module imports another.

**1.4 The pipeline core does not import the web backend. [gate]**
`src/unified_pipeline/` is a library. It knows nothing about FastAPI, the DB, or `web_interface/`.
*Why:* `llm_client.py:50-61` injects `sys.path` so the pipeline core can import `app.config_loader` **from the web backend**, at import time, for 43 downstream modules. That arrow is backwards, and it means the CLI cannot run without the web app's config layout.
*Check:* `grep -rn 'web_interface\|from app\.' src/unified_pipeline/` returns nothing.

**1.5 A shared vocabulary has exactly one definition. [gate]**
Taxonomy codes, section letters, stage names, stage ordering, artifact paths — each defined once and imported.
*Why:* the WCM code vocabulary is currently defined in at least three places that **disagree**. `core/valid_taxonomy_codes.py` lists no `E`/`F`/`L`/`P` child codes and puts `F` and `L` in `SECTIONS_WITHOUT_SUBSECTIONS`; `cv_parser/cv_taxonomy_wcm.py` defines `F1`, `L1`, `L2`, `L3`; `stage_6_word_template.py` renders them. The first is live at `taxonomy_mapper_v2.py:4095`, which short-circuits any entry routed to a parent it believes has no subsections. Separately, the stage→output-path map is restated in four places and the stage ordering in three.
*Check:* the literal appears in one module; everything else imports it. `grep -c "'F1'"` across the tree should concentrate, not spread.

## 2. Where things live

```
src/unified_pipeline/          the pipeline library — no web, no DB, no FastAPI
  stage_<n>_*.py               one stage each: takes artifacts in, writes artifacts out
  stage6/                      a stage large enough to be a package
    formatting/ parsing/ normalization/ resolution/ sorting/   pure, no docx
    sections/                  one module per WCM section; peers never import peers
  core/                        shared pipeline logic used by 2+ stages
  cv_parser/ segmentation/     document reading and structural segmentation
web_interface/backend/app/
  api/                         HTTP only: parse request, call a service, shape response
  services/                    business operations; owns all DB access
  pipeline/                    the run driver, progress, cancellation
  storage/                     S3 and filesystem adapters
scripts/                       operator tools; stdout is a contract (§7.1)
docs/                          committed working agreements
```

**2.1 Routes do not touch the database. [gate]**
`api/` modules call `services/`. No `db.query(` in a route handler.
*Why:* 34 of 72 non-test `db.query(` calls sit in `api/`, 21 of them in `admin_routes.py`, while a `services/` package already exists. This is the whole of the value a repository layer would have delivered, without adding one.
*Check:* `grep -rc 'db\.query(' web_interface/backend/app/api/` trends to 0.

**2.2 One driver. [gate]**
There is one place that knows the stage list, the stage order, and the artifact paths. New entry points are not created.
*Why:* there are three today. `run_full_pipeline.py` (1,131 lines) and `pipeline/orchestrator.py` (1,321) independently restate the same twelve-stage pipeline and share 46 verbatim-identical lines; they have already drifted on error policy, cancellation, render flags and cost accounting. `core/cv_pipeline.py` (2,179 lines) is a third whose only importers are two test files — and the repository's one end-to-end test drives *it*, so it exercises neither real driver.

**2.3 A stage owns its artifacts and nothing else. [judgement]**
A stage reads named inputs and writes named outputs. It does not reach into another stage's directory, and it does not resolve its input by globbing.
*Why:* the CLI resolves stage 6's input with `glob(f"*{uid}*")` then `candidates[0]` — a substring match that can select a different CV's artifact.

## 3. Function size, responsibility, and clarity

Mahender's guidance is explicit and this section follows it: **separation of concerns, not line count.** Size is not the rule. Size is the trigger for asking whether the rule is met.

**3.1 One reason to change. [judgement]**
A function does one thing at one level of abstraction. If describing it needs the word "and", that is the seam.

**3.2 Size thresholds are review triggers, not limits. [judgement]**
Over ~100 lines, the PR description says what the single responsibility is. Over ~200, it says why it should not be split. Neither is a veto — `_score_header_candidate` at 445 lines may be a flat scoring table that is genuinely clearer whole.
*Current state, for calibration:* 1,926 non-test functions; 40 are 200+ lines and total 14,572. The largest are `classify_entries_batch` (963), `main` (900), `get_data_quality` (747), `map_cv_sections_v2` (664).

**3.2a The debt does not grow. [gate]**
Everything else in this section is judgement, which does not shrink an existing 963-line function and does not stop a new one. This rule is the enforcement: total *excess* lines over the 200-line threshold is checked in at `scripts/function-size-baseline.json` and may fall but never rise.

```bash
python3 scripts/check_function_size.py            # CI gate
python3 scripts/check_function_size.py --report   # the worst offenders
python3 scripts/check_function_size.py --update   # lock in an improvement
```

Excess, not count, because count is perverse: splitting one 963-line function into five 200-line ones would fail a count-based gate. Excess falls whenever a function is genuinely decomposed and rises only when new oversized code appears.

*Baseline at adoption:* **6,572 excess lines across 40 functions**, measured on `origin/dev` @ `4e62bbd`.
*Escape hatch, scoped to holding or lowering the baseline:* if a function genuinely should stay whole, say so in the PR — and if the PR as a whole doesn't raise total excess (something else in it shrank), run `--update` to lock the new total in. `--update` refuses to write a baseline higher than the one on disk ("the ratchet only turns one way," in the script's own words) — that is the gate working, not a bug in the hatch. So a PR that only adds excess has no scripted unlock: offset it in the same PR, or a maintainer edits `scripts/function-size-baseline.json` by hand, a separate and visibly deliberate act rather than a silent one.
*CI:* stdlib only, no dependencies, so it runs as its own job in a few seconds.

```yaml
  function-size:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.14" }
      - run: python3 scripts/check_function_size.py
      - run: python3 scripts/test_check_function_size.py   # the gate's own self-test
```

**3.3 Splitting a file does not decompose its functions, and must not be described as if it does. [judgement]**
*Why:* across the stage-6 split, `stage_6_word_template.py` went 9,530 → 3,113 lines plus a package, and every function of 200+ lines kept its exact size — `generate` 306, `_fill_personal_data` 296, `_fill_clinical_practice` 280, before and after. These are two separate kinds of work with two different acceptance mechanisms (§6.3).

**3.4 Receive your scope; do not search for it. [gate]**
A function that operates on part of a document takes that part as an argument. It does not take the whole document and locate its target by text search.
*Why:* 26 section fillers resolve their write target by fuzzy search across the whole document — 59 such lookups. That is how a Q4A anchor search landed on the table Q1 had just filled and **wiped 39 entries across 6 corpus CVs** (#454). The shipped fix added a shared `_cleared_tables` set, which guards one of the ways two fillers can collide.

**3.5 Helpers are defined at module level. [judgement]**
Nested function definitions inside a long function hide the seam and cannot be tested. Hoist them (#407).

**3.6 Explicit over clever. [judgement]**
Prefer readable Python to compact Python. If a construct needs a second read to work out what it produces, write it out.

**3.7 No metaprogramming or dynamic attribute access. [gate]**
No metaclasses, no `eval`/`exec`, no `type(name, bases, dict)`, no `__getattr__`/`__setattr__` overrides, and no `getattr`/`setattr`/`hasattr` whose attribute name is a variable rather than a literal. No passing `__dict__` around as a payload.
*Why:* these defeat grep, defeat the type checker, and turn a rename into a runtime failure in a pipeline whose failures are silent content loss. `taxonomy_mapper_v2.py:3102` passes `validation_guidance.__dict__` into a prompt payload, which silently couples the prompt to attribute names.
*This is cheap to hold:* the codebase has zero metaclasses, zero `eval`/`exec`, and zero dynamic class creation — plus one deliberate, commented `__getattr__` override (`orchestrator.py:176`, added to fix §4.2's `sys.stdout` leak; see §9). The rule is about keeping the rest of it that way, not a cleanup campaign — dynamic attribute access is the one sub-check that needs an AST scan to count precisely (§9's Appendix), so treat any grep-derived figure here as a shortlist, not a total.

**3.8 Clarity does not license verbosity. [judgement]**
Rewriting for readability is right when it does not cost lines. When clarity and size genuinely conflict, leave the compact form and add one comment saying what it produces — do not expand it into boilerplate, and do not add an abstraction to make it "cleaner".
*Why this rule exists:* 3.6 and "do not increase code size" pull against each other, and without a tie-break the predictable outcome is a bigger codebase justified as clarity. A two-dimensional comprehension like `[[str(item.get(h, "")) for h in headers] for item in value]` (`api/steps.py:392`) is a standard table build; expanding it costs four lines and improves nothing. Depth is the signal, not nesting per se — 31 nested comprehensions exist and most are of that shape.
*Corollary for abstractions:* a new interface, wrapper or base class earns its place by removing more code than it adds. If it does not, it is a layer, and §1 already covers when we add those.

**3.9 A deliberate shortcut is labelled with its ceiling. [judgement]**
Where the simple implementation is knowingly not the general one, say so at the site, name the limit, and name what to do if the limit is ever reached. The established prefix in this codebase is `ponytail:` — roughly 10 occurrences today, in `ed_group_lookup.py`, `session_idle.py`, `login_throttle.py` and `stage_6_word_template.py`:

```python
# ponytail: fixed window; expire only on the first hit. Orphan key ...
# ponytail: pure renames ONLY -- old code and target must mean the same thing.
```

It marks intent, not ignorance: the reader can tell a considered trade-off from an oversight, which is the difference between leaving it alone and "fixing" it into something more complex. Distinct from `TODO`, which means unfinished — a labelled shortcut may be finished and correct at its stated scale, permanently.

*Measured, not just asserted:* `login_throttle.py`'s label is the sharpest of the four — #414 is the actual measurement behind it (200 concurrent POSTs through the real ASGI transport report 1 distinct thread ident inside the handler; the unlocked shortcut holds because `login` is an `async def` with no `await` and the container runs single-worker uvicorn). The same issue reproduces the failure with real OS threads — 18 of 200 trials over-admit once that precondition breaks — which is what turns "ponytail: fixed window" from a hopeful comment into a checked one, and names the exact three changes (sync handler, `--workers > 1`, a background task) that would reopen it.

*Why it matters for review:* an unlabelled global lock, `O(n²)` scan or naive heuristic reads as a defect and invites an unnecessary rewrite. A labelled one reads as a decision with a stated upgrade path.

## 4. State and concurrency

Runs are genuinely concurrent: `api/runs.py:256` schedules each run through `run_in_threadpool`, and `CVICHE_MAX_CONCURRENT_RUNS` is `"3"` in both the dev and prod overlays, so up to three full pipelines share one interpreter and one copy of every module-level object. Prod additionally runs 2–4 pods.

**4.1 No module-level mutable state that is written after import. [gate]**
Module-level bindings are constants. Per-run state is passed explicitly or lives on a per-run object.
*Why:* `INSTITUTION_CACHE` (`stage_5b:40`), `_group_cache` (`ed_group_lookup:39`) and `_storage` (`storage/factory:18`) are all written at runtime and shared across concurrent runs.
*Check:* an AST scan for module-level mutable bindings that are subscript-stored, `AugAssign`ed, or mutated by method call anywhere in the tree.

**4.2 No process-global mutation for per-run purposes. [gate]**
No `sys.stdout` swapping, no `os.chdir`, no shared output directory. Anything written per run carries the run id in its path.
*Why:* two live cross-tenant leaks. `core/prompt_logger.py:37` writes verbatim CV text to one process-wide directory with **no run id in the filename**; `orchestrator.py:866` then uploads by `st_mtime` into `put_file(self.run_id, …)`, so one faculty member's CV text can be filed under another's run and served from an authenticated endpoint. And `orchestrator.py:900` swaps process-global `sys.stdout` per stage from a worker thread, so during an overlap run A's output — grant titles, the owner's name — is written as `Log` rows against run B and streamed to B's viewer.

**4.3 A cache key contains everything the cached value depends on. [gate]**
*Why:* `INSTITUTION_CACHE` is keyed on institution name alone but populated by an **owner-conditioned** prompt. Faculty B inherits faculty A's disambiguation as B's city and state, in the delivered document, and it is persisted to disk.

**4.4 Own your region; return side effects rather than accumulating them. [gate]**
A section renderer receives its region and its entries, and returns what it produced plus anything it could not place. It does not append to shared instance lists.
*Why:* this is §3.4's rule stated as state rather than as lookup, and together they are what make section bleed structurally impossible instead of defensively guarded.

**4.5 Lazy singletons are locked. [gate]**
*Why:* `llm_client.py:176-180` uses a double-checked lock and documents the exact boto3 hazard. `storage/factory.py:18` constructs the same kind of client unlocked, entered by 8 threads from `admin_routes.py:336`.

**4.6 Freezing a constant is not encapsulation. [judgement]**
Converting a never-mutated table to `MappingProxyType` is defence in depth and is welcome, but it does not answer a concurrency review. Answer it by naming what per-run state exists and showing it cannot be reached by another run.

## 5. Error handling

**5.1 The error policy belongs to the driver, not to the stage. [gate]**
A stage raises. The driver decides whether that ends the run.
*Why:* the same twelve stages currently have opposite semantics depending on caller — the CLI wraps each in `except Exception` and continues with a printed warning (11 sites), while the orchestrator raises and fails the run. A clean CLI run is therefore not evidence the web path works, and neither behaviour is written down anywhere a stage author would see it.

**5.2 Never report success when a step failed. [gate]**
*Why:* #448 existed because we did.
*Check:* exit status and run status are derived from the same value that recorded the failure, not set independently.

**5.3 Degradation is visible in the artifact. [gate]**
A fallback path records that it was taken. Silent fallback is indistinguishable from success and defeats every downstream gate.
*Why:* both stage-6 `call_llm` sites are wrapped in `try/except` with silent fallbacks — `_classify_geographic_scope` returns `'National'` and the segment reroute is skipped. Under the corpus gate, which stubs the LLM, those paths are always taken and therefore never tested.

**5.4 No bare swallow. [gate]**
`except Exception: pass` requires a comment stating what is expected and why continuing is correct.
*Why:* `run_full_pipeline.py`'s stage-5b cost read was wrapped in exactly this — a missing file, malformed JSON, or a permissions error reported the same `cost: 0.0` as a stage that genuinely cost nothing, so `total_cost` was silently short and the run still claimed success (#489, fixed by logging the exception instead of swallowing it).

**5.5 Tools and gates fail closed. [gate]**
A validator, gate or script exits non-zero when it could not do its job. "Nothing to compare" is a failure, not a pass.
*Why:* `render_gate.py` (then `gate_render.py`) used to never clear its output directory and never call `sys.exit`. Re-rendering into a reused directory after a code change that crashed every render left the previous arm's files in place — a run where **all 100 CVs failed** reported `compared 100 / identical 100 / CHANGED 0 / PASS`, exit 0. Fixed in #589 (closing #584): the script now clears its output directory before every run and exits non-zero on any comparison failure or crash.

## 6. Testing expectations

**6.1 Tests assert on behaviour, not on structure. [gate]**
Assert on rendered output, returned values, and artifact contents — not on which module a function lives in or how many helpers it has.
*Why:* this is precisely what makes a relocation PR safe to sign off. Tests that encode structure make refactoring expensive and prove nothing about correctness.

**6.2 A test must fail if the thing it covers is deleted. [gate]**
"The suite passes" is not a standard. The standard is that the suite would notice.
*Why:* measured by ablation over the 100-CV corpus, **31 of 44 stage-6 renderers can be deleted whole with all 437 pipeline tests green** — including publications, grants, teaching, service and memberships. Deleting `_add_track_change_deletion` removes 1,926 tracked deletions across 68 of 100 CVs and every gate still passes.
*Check:* the cheapest mutation test there is — delete the function, run the test, confirm red, restore.

**6.3 Match the gate to the kind of change. [gate]**

| Change | Gate | Written |
|---|---|---|
| Pure relocation | identical corpus render **and** identical XML fingerprint, two arms, same day | after |
| Behaviour change | tests asserting the new behaviour, plus render attribution with zero unexplained deltas | **before** |
| God-function decomposition | tests pinning current behaviour | **before** |

*Why:* the #398 section-split stack sat 44 commits behind `dev`, about to merge on a relocation gate alone — no fingerprint or behaviour check — which would have silently reverted 25 methods' worth of shipped stage-6 fixes (#570).

A relocation gate cannot sign off a behaviour change; it is defined as proving nothing changed. Conversely, unit tests alone do not discharge a relocation — per 6.2, they largely cannot see one.

**6.4 Cross-process contracts get contract tests. [gate]**
If another process consumes your output, pin its shape.
*Why:* the precedent already exists and works — `scripts/doctor_one.py` and `scripts/score_one.py` both have tests covering "stdout is exactly one TSV line, diagnostics go to stderr, failure exits non-zero." Copy that shape.

**6.5 Corpus coverage is disclosed, not assumed. [judgement]**
The corpus has holes: section E has zero entries, `M4C` has one entry in one CV, and 86 of 100 CVs have no table-sourced entry. A change touching a thinly-covered area says so, because a green gate there means little.

**6.6 A gate cited as an acceptance criterion exists in the repo before anything depends on it. [judgement]**
If a plan, wave, or PR description names a script as a pass/fail gate, that script is committed under `scripts/` — not a prototype on one contributor's machine that the plan assumes into existence.
*Why:* #584 found that waves 3–5's own acceptance criteria — "the render gate" and "the doctor gate" — did not exist anywhere in the repo: `git log --all -- '*gate_render*' '*doctor_gate*'` returned nothing. It has since been fixed (`scripts/render_gate.py`, `scripts/doctor_gate.py` and their `_compare` counterparts, landed in #589 and wired into CI's `pipeline-tests` job), so this rule generalizes a resolved incident rather than naming a still-open gap — the same way §5.5 cites an already-fixed bug as its own *Why*. It is still distinct from what §5.5 and §6.3 already cover: §5.5 is a gate that runs and reports a false pass, §6.3 is using the wrong kind of an existing gate. Neither catches a gate that a plan relies on before it has been written at all.
Naming an owner was also part of #584's own definition of "done," and never actually happened — no assignee, no target date, in the repo or the issue. That half has no positive incident behind it, so it isn't stated as a rule here.

## 7. Contracts and configuration

**7.1 Nothing parses another process's stdout. [gate]**
Progress is a callback. Metrics are a returned dict. `print()` is for humans.
*Why:* `print()` is currently a wire protocol on two live consumers — `orchestrator.py:129-138` regexes stage output into the progress bar, and `run_corpus_batch.sh` greps four literals into `summary.tsv`. No test guards either, so rewording a print silently blanks the progress bar and the batch metrics. The precedent for doing this properly already exists: the `cancel_check` callback threaded into stages 2 and 4.

**7.2 One configuration source per consumer. [gate]**
Do not add a mechanism; use the one that exists, or delete one first.
*Why:* nine distinct mechanisms exist today. Root `config.yaml` has **no Python reader** — every `config.yaml` hit in the tree is `auth_config.yaml` or `llm_config.yaml` — yet the backend Dockerfile still copies it from the repo root, and `stage_6_word_template.py:2507` carries a comment claiming a hardcoded dict below it "should match config.yaml". `app/config/pipeline_config.py` and `core/llm_config.py` have zero importers. Model pricing is defined four times.

**7.3 A produced field is rendered or explicitly declared unrendered. [gate]**
*Why:* stage-6 section renderers are fixed-slot `fields.get(...)` enumerations, so a stage-4 field the renderer does not name is dropped with no warning — `grant_number` being the worked example.
*Check:* a registry of the keys stage 4 can emit, and a test asserting each is either rendered or listed in an explicit `DELIBERATELY_UNRENDERED` set.

**7.4 Time is an input, not an ambient fact. [judgement]**
*Why:* `datetime.now()` in stage 6 does more than stamp a date. At `:2236` it decides whether a degree renders as in-progress, and at `:4363` it reclassifies grants with past end dates from M2A into M2B — so **the current date moves content between sections**. That makes output non-reproducible and makes any cross-midnight A/B comparison meaningless.

## 8. Types and literals

Where §3 is about function-level shape, this is about the shape of the data and the constants that pass through it. Both rules below are **[judgement]** — the incidents behind them are real, but the mechanical check for either (three-or-more `.get()` reads on one variable; a literal used more than once) is too noisy against the current codebase to gate unconditionally without ratchet infrastructure like §3.2a's. Say so in the PR instead.

**8.1 A dict crossing a function boundary is a typed record, not a bag of `.get()` calls. [judgement]**
Data that crosses a function or stage boundary and gets read by more than one caller is a dataclass, `TypedDict`, or Pydantic model — not a raw `dict` re-interpreted ad hoc by each reader.
*Why:* three corpus-observed defects share this exact root cause. `_fill_personal_data` assumed `home_address` was a string and called `.replace()` on it; 2 of 96 runs in one batch produced **no output document at all** (#442). The same function's `phone` field arrived as a dict of three numbers, and none of them rendered, with nothing logged (#450). `split_fused_citation_entries` called `.splitlines()` on `formatted_citation` before checking its type, which aborts the run on any non-string value (#554). None of the three would have been caught by a type annotation alone — they all arrive as `Any` off `json.load` — but a typed record forces the read site to declare what it expects, instead of discovering the mismatch at whichever method the wrong type happens to reach first. The flip side holds too: `Haystack` is already a `NamedTuple`, and all three call sites unpack it positionally anyway, one of them silently discarding `.tokens` (#280) — a typed record only pays off if callers use the names.
Not every dict needs this: one that stays inside a single function, or is read once, is fine as-is — #554's own filer explicitly declined a dataclass fix for that specific defect in favor of a boundary-normalizing function, and that was the right call for a single read site. The signal is repetition at the boundary: `_create_grant_table`, the function CLAUDE.md names as the stage-6 field-drop example, reads its `fields` dict via `.get(` 28 times. #567 tracks this class across `stage6/` (zero `TypedDict`/`@dataclass` exist there today) and the eight review comments it collects, on top of #442/#450/#554, are what earns this its own rule rather than a line in §3.6.

**8.2 A magic number or repeated string is a named constant. [judgement]**
An inline literal used for classification, a threshold, or a comparison — not a one-off display value — gets a name that says what it means.
*Why:* three independent misclassifications in stage 6 trace to exactly this, all three still live on `origin/dev` (#573). `_fill_licensure` treats any 10–11 digit string as an NPI and any text containing the substring `'DEA'` as a DEA number — including an entry whose text merely mentions "Dean" — so a state medical licence number can render as a federal identifier on a physician's CV (`stage_6_word_template.py:5901-5993`). `_fill_service` builds a 14-entry keyword list and then reads `journal_keywords[:10]`, an off-by-one slice that silently drops the one word "neurology," so identically-shaped Neurology and Oncology reviewer entries route to different sections (`stage6/sections/service.py:122`). And a postdoctoral-code set `{'C', 'C1', 'C2'}`, duplicated in two places, is missing `C3` in both, so Fellowship Training entries fall through to the Appendix. Naming each — `NPI_DIGIT_PATTERN`, `JOURNAL_SPECIALTY_KEYWORDS`, `POSTDOC_CODES` — doesn't fix the bug by itself, but it is what would have made the wrong value visible at the definition site instead of a corpus batch away from it.
A second, narrower incident: `"gpt-5.1"` is a repeated default-parameter literal across 5+ function signatures in `taxonomy_mapper_v2.py` (#377), and it disagrees with the model actually resolved from `llm_config.yaml` — every run in a 96-CV batch was stamped with a model it did not use, in both the CLI banner (#444) and the stage artifacts themselves (#459). Closer to §1.5's territory than a classic magic number, but the fix is the same shape: one named source of truth instead of a literal repeated on faith.

## 9. Where the code stands against this today

This is a target state. The table below is every **[gate]** rule, re-measured against `origin/dev` @ `5a0035a` (2026-08-14) — the original baseline was `4e62bbd`, 144 commits earlier, and a meaningful fraction of the table had drifted in that gap. Ten PRs are open at time of writing (#600, #620, #623–625, #641–644, #647); none are merged, so this reflects `dev` as it stands, not as it will once they land. Nothing here is a work item by itself — it is the honest baseline the rules are written against.

| Rule | Target | Today | |
|---|---|---|---|
| 1.1 dependency direction declared | every package | 26 total `__init__.py` on `dev`, but only **3** state an actual import-direction rule (`stage6/__init__.py`, `stage6/sections/__init__.py`, `services/__init__.py`) — the rest state contents or purpose, not direction | ✗ |
| 1.2 pure layers import no `docx` | 0 | 0 | ✓ |
| 1.3 peers do not import peers | 0 | 0 | ✓ |
| 1.4 core does not import the web backend | 0 | 1 — `llm_client.py:62`, reaching 43 modules | ✗ |
| 1.5 one definition per vocabulary | 1 each | taxonomy defined 3× **and they disagree**; stage→path map 4×; stage order 3× | ✗ |
| 2.1 no `db.query(` in `api/` | 0 | 34 (21 in `admin_routes.py`) | ✗ |
| 2.2 one driver | 1 | 3 — `run_full_pipeline.py` 1,142 lines, `orchestrator.py` 1,398, `core/cv_pipeline.py` 1,602 (still imported only by its own 2 test files) | ✗ |
| 2.3 stage owns its artifacts | no globbing | CLI still resolves stage 6 input by `glob(f"*{uid}*")[0]`, now 5 such sites in `run_full_pipeline.py` | ✗ |
| 3.4 receive your scope | 0 doc-wide searches | 59 fuzzy lookups across 26 section fillers (not re-counted; `stage6/sections/__init__.py` now lists 23 classes, worth reconciling) | ✗ |
| 3.x oversized-function debt | falling | **5,759** excess lines, 37 functions ≥200 — falling from the 6,572/40 baseline, though the checked-in `function-size-baseline.json` still reads 6,572 pending a `--update` | ratchet |
| 3.7 no metaprogramming | 0 | 0 metaclasses, 0 `eval`/`exec`, 0 dynamic class creation — but **1 real `__getattr__` override**, `orchestrator.py:176`'s `_RoutedStdout`, added while fixing 4.2 below (commented, deliberate, and still a violation) | ✗ |
| 3.7 no dynamic attribute access | 0 | not reliably re-measurable with the Appendix command — see its note below; last trustworthy figure (9) is from the `4e62bbd` baseline | ✗ |
| 4.1 no module state written after import | 0 | at least the 3 named examples still present (`INSTITUTION_CACHE`, `_group_cache`, `_storage`); the original total of 6 wasn't independently reproducible from a stated enumeration | ✗ |
| 4.2 no process-global per-run mutation | 0 | **0 — fixed.** `orchestrator.py`'s `_RoutedStdout` router (installed once at import, dispatches per calling thread id) replaces the per-stage `sys.stdout` swap, closing #581; `prompt_logger.py` now scopes every write under `PROMPT_LOG_DIR/<run_id>`, closing #580 (PRs #585, #586) | ✓ |
| 4.3 complete cache keys | 0 | **0 — fixed.** `_institution_cache_key()` folds a hash of the owner context into the cache key, closing #582 (PR #585) | ✓ |
| 4.4 own your region | per-section | shared `_overflow_entries`, `_appendix_pending`, `_cleared_tables` still present, same file | ✗ |
| 4.5 lazy singletons locked | all | 4 locked (the documented example now at `llm_client.py:181`), 1 unlocked (`storage/factory.py:18`) | ~ |
| 5.1 error policy owned by the driver | 1 policy | 2 opposite policies — CLI catches at 11 sites, orchestrator raises. #647 (open) adds a regression test *pinning* this divergence; it does not unify the policy | ✗ |
| 5.2 never report success on failure | enforced | met, with a regression test (`test_run_full_pipeline_exit_status.py`) | ✓ |
| 5.3 degradation visible in the artifact | all paths | 2 silent LLM fallbacks in stage 6, both still present | ✗ |
| 5.4 no bare swallow | 0 | 6 literal `except Exception: pass` sites — the original figure of 48 wasn't reproducible from the stated command; even the broader `except …: pass/continue` reading comes to 41 | ✗ |
| 5.5 tools fail closed | all | **0 — fixed.** `render_gate.py` clears its output directory and exits non-zero on any comparison failure; `render_gate_compare.py` refuses PASS on any mismatch (PR #589, closing #584) | ✓ |
| 6.1 tests assert behaviour not structure | all | largely met — this is why relocation PRs work at all | ✓ |
| 6.2 a test fails if its subject is deleted | all | the 31-of-44 ablation wasn't re-run for this refresh (it's expensive to redo); pytest collection under `src/unified_pipeline/tests/` is now 714 tests, up from 437, so the ratio specifically needs re-measuring, not assumed improved | ✗ |
| 6.3 gate matches the kind of change | 3 gates | `render_gate_compare.py` now adds a C14N fingerprint of `document.xml` alongside the paragraph-text check — the doc's stated gap ("no fingerprint arm") is closed, though not confirmed across all three gate kinds in the table above | ~ |
| 6.4 contract tests on cross-process output | all | met for `doctor_one`, `score_one`; absent for the stage `print()` wire | ~ |
| 6.5 corpus coverage disclosed | practised | not practised | ✗ |
| 7.1 nothing parses another process's stdout | 0 | 2 consumers — `PROGRESS_PATTERNS` is **4** regexes, not 6; 4 batch-script literals, now at `run_corpus_batch.sh:142-145` | ✗ |
| 7.2 one config source per consumer | 2 | 9 mechanisms; root `config.yaml` still has 0 readers; the "should match config.yaml" comment moved to `stage6/resolution/institution.py:39` | ✗ |
| 7.3 produced field rendered or declared | registry exists | no registry (`grep -rl DELIBERATELY_UNRENDERED\|FIELD_REGISTRY` — 0 hits). The worked example no longer holds as stated: `grant_number` is now read and rendered into Award Source (`stage6/sections/research_support.py:346-351`) — fixed as a one-off `.get()` addition, not via a registry, so the next unnamed field drops exactly as silently | ✗ |
| 7.4 time is an input | 0 ambient | 7 `datetime.now()` in stage 6 (exact match); the 2 that move content between sections are now at `stage6/sections/education.py:78` and `stage6/sections/research_support.py:71`, relocated from `stage_6_word_template.py:2236`/`:4363` by the split | ✗ |

Of 31 rows: **7 met, 3 partial, 20 not met**, plus the oversized-function debt, which is a ratchet rather than a pass/fail — falling since baseline, from 6,572 to 5,759 excess lines.

**What changed since `4e62bbd`.** Two rows flipped to ✓, and they were the doc's own stated top priority: §4.2 and §4.3 (PRs #585/#586, closing #580/#581/#582). §5.5 also flipped to ✓ (PR #589, closing #584), which closed §6.3's fingerprint-arm gap as a side effect. One row flipped the other way: fixing §4.2's `sys.stdout` swap introduced `orchestrator.py`'s `_RoutedStdout.__getattr__` — a real §3.7 violation, deliberate and commented, but the metaprogramming row is no longer a clean ✓. None of this came from the ten open PRs; all of it is already merged to `dev`.

**Reading this honestly, again.** If only three things are done next, they should be:

1. **§6.2** — until a test fails when its subject is deleted, every other gate is being verified by a suite that demonstrably cannot see whole missing features. Nothing in the intervening 144 commits touched renderer test coverage, so there's no reason to expect this has improved even though it wasn't re-measured directly.
2. **§5.3** — both silent stage-6 LLM fallbacks are still live, and still structurally untested: the corpus gate stubs the LLM, so the fallback path is the only path the gate ever actually exercises.
3. **§7.3** — no registry exists yet. `grant_number` itself is fixed, but it was fixed the same way it broke — another one-off line in `_create_grant_table` — so the next stage-4 field this section doesn't name will drop exactly as silently as that one did.

Rows 1.2 and 1.3 remain green, and are worth repeating: the stage-6 split delivered the two boundaries that make section bleed structurally harder, which is the part of that work most worth repeating elsewhere.

## Appendix: the checks, as commands

The gates above that are greppable today, for use in review or in CI:

```bash
# 1.2  pure layers import no docx
grep -rl 'import docx\|from docx' src/unified_pipeline/stage6/{parsing,normalization,resolution,sorting}/

# 1.3  sections do not import each other                            (clean today)
grep -rn 'stage6\.sections\|from \.\.sections' src/unified_pipeline/stage6/sections/ | grep -v __init__

# 1.4  pipeline core does not import the web backend        (1 violation: llm_client.py:62)
grep -rnE '^[[:space:]]*(from|import)[[:space:]]+(app|web_interface)\b' src/unified_pipeline/

# 2.1  routes do not query the database                                  (34 today)
grep -rh 'db\.query(' web_interface/backend/app/api/*.py | wc -l

# 5.4  bare swallows                                                      (6 literal today)
grep -rn -A1 'except Exception' src/ web_interface/ | grep -B1 'pass$'

# 3.7  metaprogramming                                (0 today except one commented __getattr__)
grep -rnE 'metaclass=|[^_]\beval\(|[^_]\bexec\(|__getattr__|__setattr__' src/ web_interface/

# 3.7  dynamic attribute access                                (stale — see footnote and §9's row)
grep -rnE '\b(get|set|has|del)attr\([^,]+,[^"'"'"']' src/ web_interface/
grep -rn '__dict__' src/ web_interface/
```

§3.7's `getattr` form needs AST to be exact — grep cannot reliably tell a literal attribute name from a variable one, so treat the command above as a shortlist to read, not a count. It also doesn't exclude a literal string argument (`getattr(x, 'foo')` still matches), which is why a 2026-08-14 re-run returned 177 raw hits, 49 with tests excluded — both numbers overcount for the same reason the original "9" undercounted precision. The precise scan is the same AST walk `check_function_size.py` already performs; say the word and it grows a `--clarity` mode rather than becoming a second script.

Note for anyone reproducing these: `grep` on the dev machines is ugrep, whose recursive mode **silently skips dot-directories**. For a complete sweep use `find . -path ./.git -prune -o -type f -print | xargs grep -l …`.
