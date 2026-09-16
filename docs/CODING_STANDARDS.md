# CViche coding standards

The code-level companion to `DEV_WORKFLOW.md`, which covers process (branching, merging, corpus batches). This covers the code itself: module boundaries, where things live, function responsibility, state, error handling, and what a test has to prove.

Every rule here is earned by something that actually went wrong in this repository. Where a rule has an incident behind it, the incident is named — a rule nobody can trace to a real failure is a rule nobody follows.

## How to read this

Rules are marked **[gate]**, **[ratchet]**, **[gate — check pending]**, or **[judgement]**.

- **[gate]** — mechanically checkable and binary: a reviewer may reject on it without discussion, and the check either passes or it doesn't.
- **[ratchet]** — mechanically checkable, but the count isn't already zero, so pass/fail would either block on old debt or hide new debt. Enforced as *non-increasing* against a checked-in baseline (§3.2a; §9's ratcheted rows) instead: a rising count blocks, a falling or flat one doesn't. Same obligation as **[gate]**, different shape of check.
- **[gate — check pending]** — worded and agreed as a **[gate]** or **[ratchet]**, but the machinery its *Check:* names (a script, a CI job, a recorded field) doesn't exist in the repo yet. §9 marks the row by the best current evidence — ✓, ~, or ✗ — the same as any hand-verified row; the marker signals only that no *standing* check keeps that evidence current, not that the invariant is false. §5.2 applies here too: reporting a flat ✗ for an invariant that's actually true would itself be the failure this document exists to catch. Retag to **[gate]**/**[ratchet]** in the same PR that ships the check (§6.6).
- **[judgement]** — a question the author answers in the PR description. Not a veto; an obligation to have thought about it. Where the shape genuinely doesn't admit a script, a **[gate]** or **[ratchet]** names a human comparison instead and says why a script can't do it (§5.6's request-multiplier clause is the current example) — that's still binding, unlike **[judgement]**, only the mechanism isn't automated.

A **[gate]** violation that's deliberate, reviewed, and worth keeping is marked at the site: `# standards-waiver: <rule> — <reason> (#issue)`, greppable per rule number so it can't silently drift onto a different violation as the surrounding code changes. `scripts/check_standards.py` reports it separately from an unwaived hit — a row with only waived hits reads `~`, never `✓` (§5.2's "never report success when a step failed" applies to this table too) — and the waived count is itself ratcheted: it may fall, never rise, same baseline file and `--update` contract as any other ratcheted row, including the same hand-edit escape hatch — a maintainer can raise it by editing the baseline file directly, a separate and visibly deliberate act, the same as §3.2a's. The comment at the violation site is the one place a waiver is recorded; a central allowlist would be a second definition of the same fact (§1.5) that can drift once the line it protects moves or is deleted. A waiver cannot be used for the categories the standing exception below names — cross-tenant data exposure, silent content loss into a delivered document, a gate silently proving less than it claims, or credential exposure; §4.2, §4.3, and §7.5 are the rules currently in that set.

Nothing here is retroactive. Existing violations are listed so the direction is unambiguous, not so anyone is expected to fix them on sight. New and touched code meets the standard.

### Closing the loop: incident to class, and the standing exception

Earning every rule from something that already broke is a strength — it's why nothing here is a rule nobody can trace — but taken as the *only* route to a rule, it guarantees this document can only ever describe last year's failures. Three corrections keep that from being the whole story:

**A rule names the class, not the instance.** §3.4's own `*Why:*` shows the gap: #454's shipped fix (`_cleared_tables`) "guards one of the ways two fillers can collide" — a patch scoped to the instance, by its own admission. When an incident earns a new rule or a new `*Why:*` line, the postmortem answers one more question before it's done: *which existing rule should have caught this, and why didn't it?* If the answer is "no rule could have," that is itself often the strongest evidence for the rule being added.

**A standing exception to the traceability principle.** For a category where the cost of being wrong is severe enough — cross-tenant data exposure, silent content loss into a delivered document, a gate silently proving less than it claims, or credential exposure — a rule may exist *before* an incident earns it, provided it says so plainly: *"preventive — no incident yet"*, plus the cost of the incident it forestalls. §4.2/§4.3 were earned the traceable way, by two real leaks; several rules below (§4.7, §5.8, §5.11, §6.7, §7.5) are not, and each says which case it is rather than dressing a hunch up as history.

**A regression guard on a currently-true invariant.** Neither of the above — nothing has broken, and there's no hypothesized future cost being forestalled either. The rule exists because something is true today and stays true only if nobody reintroduces the failure mode by accident. §5.6's original half (no stage wraps its own retry loop) is this shape: its `*Why:*` says "true today," checked directly — not "here's what it would cost if it stopped." §5.6's own second half, added this round, is the opposite: an unmet-today check on the request multiplier, bundled into the same rule number because it's the same subject, not because it's the same shape.

### Two questions, two authorities

Minimalism and readability pull against each other on real diffs — the shortest version of a thing and the clearest version of it are often not the same. Rather than re-argue that on every PR, they are separated by which question is being asked:

| Question | Decided by |
|---|---|
| **Should this code exist at all?** New helper, new abstraction, new dependency, new file, new config mechanism. | Minimalism. Default is no. Does the standard library or an existing dependency already do it; can the requirement be dropped; would deleting something serve better than adding. |
| **Given that it exists, how should it read?** Naming, structure, compactness, where it lives, what tests it carries. | This document. Explicit over compact, one responsibility, boundaries in §1, tests per §6. |

So "it's fewer lines" is a reason to *not add* something, and never a reason to write an existing thing more densely. Conversely §3.6's push toward explicit code is not a licence to add an abstraction, a wrapper, or a file — §3.8 and the table above both close that door.

The practical test for anything new: **it earns its place by removing more than it adds.** Report the net line delta in the PR when introducing a module, layer or interface.

### What a PR description must contain

Every item below is already required by a rule; this list adds no obligation. It exists so an author states the answers instead of reconstructing the question list from eight sections. Skip any line that doesn't apply.

- Touched or added a function over ~100 lines → its single responsibility, stated (§3.2)
- Over ~200 → why it should not be split (§3.2)
- New module, layer, interface, or dependency → net line delta (above; §3.8)
- Ran `check_function_size.py --update` → the justification (§3.2a)
- Which of §6.3's three gates this change's kind requires, and where the evidence is
- Touches a thinly-covered corpus area → say so, and which hole (§6.5)
- Behaviour depends on `datetime.now()` or another ambient input → the determinism policy (§7.4)
- New `ponytail:` shortcut → its ceiling and upgrade path (§3.9)
- Concurrency-relevant → what per-run state exists and why another run cannot reach it (§4.1)
- Adds or touches a dict read across a boundary, or a literal used to classify, threshold, or compare → the typed-record / named-constant call, and why (§8.1, §8.2, §8.3)

### When a rule retires

"Earns its place by removing more than it adds" applies to rules, not just code. A **[gate]** or **[ratchet]** green in CI for two consecutive quarters collapses to one line in a **Settled** list at the end of this document: rule statement, check, incident number. Its `*Why:*` prose is deleted — the incident number is the pointer, and git remembers the rest. A settled rule is still enforced: the check still runs; only the reading cost retires. If it ever goes red, the rule returns in full, with the regression appended to its `*Why:*`. A rule whose check is a review procedure rather than a CI job doesn't qualify — nothing runs on it automatically to prove two quarters clean.

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
*Why:* the WCM code vocabulary is currently defined in at least **five** places, not three, and at least two disagree outright. `core/valid_taxonomy_codes.py` lists no `E`/`F`/`L`/`P` child codes and puts `F` and `L` in `SECTIONS_WITHOUT_SUBSECTIONS`; `cv_parser/cv_taxonomy_wcm.py` defines `F1`, `L1`, `L2`, `L3`; `stage_4_field_extractor.py`'s `FIELD_SCHEMAS` and `stage_6_word_template.py`'s `TAXONOMY_TO_SECTION`/`RENDER_ROUTED_CODES` are a third, fourth and fifth, the last two containing codes neither other list has (`S0`, `S5`, `S9`, `Q4`, `N3A`/`N3B`, bare `C`). `core/valid_taxonomy_codes.py`'s definition gates `has_subsections_router` at `taxonomy_mapper_v2.py:3084` (moved from the `:4095` previously cited here — the file has shrunk since), which short-circuits any entry routed to a parent it believes has no subsections. But that router, and every enum-validated code path in `taxonomy_mapper_v2.py`, is **unreachable from either live driver** — `run_full_pipeline.py` and `orchestrator.py` both import stage 3a from `stage_3a_header_taxonomy_mapper.py`, which never imports `taxonomy_mapper_v2` at all; the only importers of the validated path are two standalone dev/eval scripts and its own tests, the same orphan-driver shape already on record for `core/cv_pipeline.py` (§2.2). Separately, the stage→output-path map is restated in four places and the stage ordering in three.
*Check:* the literal appears in one module; everything else imports it. `grep -cE "'F1'|\"F1\""` across the tree should concentrate, not spread (Appendix).
*Also:* the same principle covers process state, not just vocabulary — the filesystem is the record of what's done; a separate completion ledger is a second definition of that fact and drifts from disk the same way a duplicated code list drifts from itself.

**1.6 A dependency that may exist is pinned, license-checked, and reproducible from a lock. [gate — check pending] — preventive, no incident yet.**
The "Two questions, two authorities" table above decides *whether* a dependency exists. Once it does: every direct dependency is pinned exactly, in exactly the place a consumer installs from (§1.5's one-definition discipline applied to the dependency list), and every *transitive* dependency resolves to the same version on every install.
*Why:* the direct-pin half already holds — `requirements.txt`, `web_interface/backend/requirements.txt`, and `requirements-dev.txt` are all exact-pinned (`==X.Y.Z`, no ranges), and the versions the first two share are verified identical, not just claimed to be. But no lock file exists (`poetry.lock`, `uv.lock`, `Pipfile.lock` — none present), so a transitive dependency's version isn't pinned by anything and can drift silently between two installs of the same `requirements.txt`. That's an unpinned behaviour change that arrives with no PR, no diff, and no author — which defeats §6.3's premise that every kind of change has a gate that can see it. Nothing in CI checks a dependency's license either.
*Check:* CI installs from a lock file (or equivalent resolved-and-pinned manifest) rather than an unlocked `requirements.txt`, and a license scan runs over the resolved set. Not implemented yet — no lock file exists to check against; see §9.

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

**2.1 Routes do not touch the database. [ratchet]**
`api/` modules call `services/`. No `db.query(` in a route handler.
*Why:* 34 of 72 non-test `db.query(` calls sit in `api/`, 21 of them in `admin_routes.py`, while a `services/` package already exists. This is the whole of the value a repository layer would have delivered, without adding one.
*Check:* `grep -rc 'db\.query(' web_interface/backend/app/api/` trends to 0.

**2.2 One driver. [gate — check pending]**
There is one place that knows the stage list, the stage order, and the artifact paths. New entry points are not created.
*Why:* there are three today — `run_full_pipeline.py` and `pipeline/orchestrator.py` independently restate the same twelve-stage pipeline and share 46 verbatim-identical lines, and have already drifted on error policy, cancellation, render flags and cost accounting; `core/cv_pipeline.py` is a third whose only importers are two test files — and the repository's one end-to-end test drives *it*, so it exercises neither real driver. Current line counts are §9's row 2.2, not restated here — a driver's size drifts every commit and the rule doesn't need it to hold. §1.5 already counts 4 restatements of the stage→output-path map and 3 of the stage order; `orchestrator.py` dispatches off `step_registry.py`'s `STEP_REGISTRY` rather than re-deriving order itself, which makes it the closest thing to a single source today — but `run_full_pipeline.py` and `cv_pipeline.py` each still encode their own, independently.
*Check:* the count of independent stage-order/artifact-path encodings (§1.5's mechanism, applied to this one vocabulary item) trends to 1, converging on `STEP_REGISTRY` since it's already live and imported by four files. Not implemented yet — no script counts this today; see §9.

**2.3 A stage owns its artifacts and nothing else. [judgement]**
A stage reads named inputs and writes named outputs. It does not reach into another stage's directory, and it does not resolve its input by globbing.
*Why:* the CLI resolves stage 6's input with `glob(f"*{uid}*")` then `candidates[0]` — a substring match that can select a different CV's artifact.

## 3. Function size, responsibility, and clarity

Mahender's guidance is explicit and this section follows it: **separation of concerns, not line count.** Size is not the rule. Size is the trigger for asking whether the rule is met.

**3.1 One reason to change. [judgement]**
A function does one thing at one level of abstraction. If describing it needs the word "and", that is the seam.

**3.2 Size thresholds are review triggers, not limits. [judgement]**
Over ~100 lines, the PR description says what the single responsibility is. Over ~200, it says why it should not be split. Neither is a veto — `_score_header_candidate` at 445 lines may be a flat scoring table that is genuinely clearer whole.
*For calibration, not restated here because it drifts every commit:* `check_function_size.py --report` names today's count of 200+-line functions and the worst offenders; §9's own row tracks the excess-lines total (5,759 as of this measurement).

**3.2a The debt does not grow. [ratchet]**
Everything else in this section is judgement, which does not shrink an existing 963-line function and does not stop a new one. This rule is the enforcement: total *excess* lines over the 200-line threshold is checked in at `scripts/function-size-baseline.json` and may fall but never rise.

```bash
python3 scripts/check_function_size.py            # CI gate
python3 scripts/check_function_size.py --report   # the worst offenders
python3 scripts/check_function_size.py --update   # lock in an improvement
```

Excess, not count, because count is perverse: splitting one 963-line function into five 200-line ones would fail a count-based gate. Excess falls whenever a function is genuinely decomposed and rises only when new oversized code appears. Its own blind spot, named rather than hidden: forty new 195-line functions add zero excess. The threshold catches size, not the fragmentation of one responsibility across many just-under-the-line functions — §3.1's "one reason to change" is judgement precisely because no line-count gate can stand in for it.

*Baseline at adoption:* **6,572 excess lines across 40 functions**, measured on `origin/dev` @ `4e62bbd`.
*Escape hatch, scoped to holding or lowering the baseline:* if a function genuinely should stay whole, say so in the PR — and if the PR as a whole doesn't raise total excess (something else in it shrank), run `--update` to lock the new total in. `--update` refuses to write a baseline higher than the one on disk ("the ratchet only turns one way," in the script's own words) — that is the gate working, not a bug in the hatch. So a PR that only adds excess has no scripted unlock: offset it in the same PR, or a maintainer edits `scripts/function-size-baseline.json` by hand, a separate and visibly deliberate act rather than a silent one.
*CI:* stdlib only, no dependencies, so it runs as its own `function-size` job (`.github/workflows/ci.yml`) in a few seconds.

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
*One rule number, two check shapes:* metaprogramming proper (metaclasses, `eval`/`exec`, dynamic class creation) is at zero and stays a true **[gate]** — a single instance fails it. Dynamic attribute access is a **[ratchet]** underneath the same number (§9's own row marks it separately) — the header tag names the stricter of the two.

**3.8 Clarity does not license verbosity. [judgement]**
Rewriting for readability is right when it does not cost lines. When clarity and size genuinely conflict, leave the compact form and add one comment saying what it produces — do not expand it into boilerplate, and do not add an abstraction to make it "cleaner".
*Why this rule exists:* 3.6 and "do not increase code size" pull against each other, and without a tie-break the predictable outcome is a bigger codebase justified as clarity. A two-dimensional comprehension like `[[str(item.get(h, "")) for h in headers] for item in value]` (`api/steps.py:392`) is a standard table build; expanding it costs four lines and improves nothing. Depth is the signal, not nesting per se — 31 nested comprehensions exist and most are of that shape.
*Corollary for abstractions:* the same test — §1's "earns its place by removing more than it adds" — applies to a new interface, wrapper, or base class, and §1 already covers when we add those.

**3.9 A deliberate shortcut is labelled with its ceiling. [judgement]**
Where the simple implementation is knowingly not the general one, say so at the site, name the limit, and name what to do if the limit is ever reached. The established prefix in this codebase is `ponytail:` — roughly 10 occurrences today, in `ed_group_lookup.py`, `session_idle.py`, `login_throttle.py` and `stage_6_word_template.py`:

```python
# ponytail: fixed window; expire only on the first hit. Orphan key ...
# ponytail: pure renames ONLY -- old code and target must mean the same thing.
```

It marks intent, not ignorance: the reader can tell a considered trade-off from an oversight, which is the difference between leaving it alone and "fixing" it into something more complex. Distinct from `TODO`, which means unfinished — a labelled shortcut may be finished and correct at its stated scale, permanently.

*Measured, not just asserted:* the sharpest example is `login_throttle.py`'s `_allow_local` — its own site comment and #414 both name the measurement behind the shortcut and the three changes that would reopen it, not just the shortcut itself.

*Why it matters for review:* an unlabelled global lock, `O(n²)` scan or naive heuristic reads as a defect and invites an unnecessary rewrite. A labelled one reads as a decision with a stated upgrade path.

## 4. State and concurrency

Runs are genuinely concurrent: `api/runs.py:256` schedules each run through `run_in_threadpool`, and `CVICHE_MAX_CONCURRENT_RUNS` is `"3"` in both the dev and prod overlays, so up to three full pipelines share one interpreter and one copy of every module-level object. Prod additionally runs 2–4 pods.

**4.1 No module-level mutable state that is written after import. [gate]**
Module-level bindings are constants. Per-run state is passed explicitly or lives on a per-run object.
*Why:* `INSTITUTION_CACHE` (`stage_5b:40`), `_group_cache` (`ed_group_lookup:39`) and `_storage` (`storage/factory:18`) are all written at runtime and shared across concurrent runs.
*Also:* freezing a never-mutated table (`MappingProxyType`) is defence in depth and welcome, but it doesn't answer this rule by itself — the check is still whether any per-run state can be reached by another run, not whether an already-immutable one is wrapped.
*Check:* an AST scan for module-level mutable bindings that are subscript-stored, `AugAssign`ed, or mutated by method call anywhere in the tree.

**4.2 No process-global mutation for per-run purposes. [gate]**
No `sys.stdout` swapping, no `os.chdir`, no shared output directory. Anything written per run carries the run id in its path.
*Why:* two live cross-tenant leaks. `core/prompt_logger.py:37` writes verbatim CV text to one process-wide directory with **no run id in the filename**; `orchestrator.py:866` then uploads by `st_mtime` into `put_file(self.run_id, …)`, so one faculty member's CV text can be filed under another's run and served from an authenticated endpoint. And `orchestrator.py:900` swaps process-global `sys.stdout` per stage from a worker thread, so during an overlap run A's output — grant titles, the owner's name — is written as `Log` rows against run B and streamed to B's viewer. Fixing *whose* run a prompt log lands under (§9) is a different question from *whether it should persist at all* — #593 tracks the open retention/encryption/access-control policy question for verbatim CV text in `PROMPT_LOG_DIR`; this rule doesn't answer that, only that the directory is scoped correctly once the answer is yes.

**4.2a An endpoint that serves a run's artifacts checks ownership before it touches storage. [gate — check pending]**
Not a concurrency rule — §4's preamble frames concurrent runs sharing state; this is authorization, sitting here because it's §4.2/§4.3's failure class delivered through the front door instead of through shared state. Every route returning run data — status, artifacts, JSON, input, prompt logs, quality, feedback — resolves access through `check_run_access(run_id, current_user, db)` (or is explicitly `require_admin`-gated with no per-owner claim) before it touches storage. Knowing a run id is not authorization to read its output.
*Why:* this is a regression guard, not a preventive rule — the invariant holds today, at all 15 REST routes and the one websocket route that return single-run data (`runs.py`, `steps.py`, `feedback_routes.py`, `websocket.py`), with `check_run_access` (`services/run_service.py:398`) as the one shared choke point. It's here so the next route added to any of those files can't quietly skip the call. No incident has shipped from this gap; §4.2's two leaks are the adjacent failure this rule exists to keep from happening a third way.
*Check:* route discovery is free — `app.routes` lists every endpoint, including the one `APIWebSocketRoute` a naive `isinstance(route, APIRoute)` filter would silently drop. The ownership assertion is not free: `check_run_access` is called as a plain function inside each handler body, never wired through `Depends(...)`, so FastAPI's dependency-table introspection sees nothing — a test needs either AST inspection of each handler for the call, or a mock-and-`TestClient` pass per route. Not implemented yet — neither exists; see §9.

**4.3 A cache key contains everything the cached value depends on. [judgement]**
*Why:* `INSTITUTION_CACHE` is keyed on institution name alone but populated by an **owner-conditioned** prompt. Faculty B inherits faculty A's disambiguation as B's city and state, in the delivered document, and it is persisted to disk.
*Check:* no general mechanical form — a per-cache-site review: enumerate the inputs the cached value depends on, confirm the key names all of them.

**4.4 Own your region; return side effects rather than accumulating them. [judgement]**
A section renderer receives its region and its entries, and returns what it produced plus anything it could not place. It does not append to shared instance lists.
*Why:* this is §3.4's rule stated as state rather than as lookup, and together they are what make section bleed structurally impossible instead of defensively guarded. §1.3 (peers don't import peers) is this rule's structural half and *is* a gate; the state half — no appends to shared instance lists — is AST-checkable in principle (the same shape as §4.1's scan) but nobody's built it, so this stays a review question with §1.3 as its mechanical floor.

**4.5 Lazy singletons are locked. [gate — check pending]**
*Why:* `llm_client.py:176-180` uses a double-checked lock and documents the exact boto3 hazard — `llm_client.py` has exactly 2 lazy singletons (the OpenAI and Bedrock clients), both locked by the same `_client_init_lock`. `storage/factory.py:18` constructs the same kind of client unlocked. §9's row previously said "4 locked, 1 unlocked"; a fresh sweep for the same shape (module-level `None` sentinel, populated by a function under `global`, re-checked inside a lock) found 6 locked and 5 unlocked repo-wide, not 4 and 1 — including one not previously documented: `core/async_rate_limiter.py`'s `get_rate_limiter_sync()` shares the module-level `_rate_limiter` global with the locked async getter but has no lock of its own around its check-and-init. Real hazard, currently zero blast radius — the module has no importers anywhere in the repo and no test references it (one commit total, the initial one), the same orphan shape already on record for `core/cv_pipeline.py` (§2.2). Filed as #653 rather than left as a doc footnote.
*Check:* an AST scan — module-level `Name = None`, then a function with `global <name>` containing `if <name> is None:` that reassigns it, flagged unless that block is nested inside a `with` whose context resolves to a `threading.Lock`/`RLock`/`asyncio.Lock` instantiation guarding a second, identical check. Not implemented yet — the pattern is confirmed detectable (grep alone isn't reliable enough; the check needs the AST shape) but no script exists; see §9.

*(§4.6 retired — folded into this rule.)*

**4.7 A log line or artifact does not carry PII beyond what it is scoped to serve. [judgement] — preventive, no incident yet.**
No log statement, debug artifact, or diagnostic dump writes verbatim CV text, `home_address`, `phone`, or another faculty member's identifying detail, unless that content is the log's entire stated purpose — and even then, scoped per §4.2.
*Why:* every PII incident this document already has is adjacent to this gap, not squarely inside an existing rule. §4.2's two leaks were about *whose run* a prompt log lands under, not whether the log should carry verbatim CV text at all — that's the open policy question #593 tracks. §8.1's `home_address`/`phone` incidents (#442, #450) were about a renderer crashing or silently dropping the field, not about where else that data can end up once it's read into memory. Nothing today says a stage or a stray debug print can't dump PII to a shared log, a temp file, or stdout — only that *if* it does, §4.2 says the write must be scoped per-run.

## 5. Failure, retries, and run provenance

**5.1 The error policy belongs to the driver, not to the stage. [gate]**
A stage raises. The driver decides whether that ends the run.
*Why:* the same twelve stages currently have opposite semantics depending on caller — the CLI wraps each in `except Exception` and continues with a printed warning (11 sites), while the orchestrator raises and fails the run. A clean CLI run is therefore not evidence the web path works, and neither behaviour is written down anywhere a stage author would see it.
*Check:* `test_run_full_pipeline_exit_status.py::test_a_mid_pipeline_crash_also_exits_non_zero` pins the CLI half (a mid-pipeline stage exception is recorded and the run continues to completion); `test_error_policy_driver_contract.py::test_a_stage_exception_fails_the_run_and_stops_the_pipeline` pins the orchestrator half (the same kind of exception propagates, marks the run failed, and stops the remaining stages). Together they're the regression guard for #646 -- proof the two policies stay as documented, not that they're unified into one (§9's row 5.1, still open).

**5.2 Never report success when a step failed. [gate]**
*Why:* #448 existed because we did.
*Check:* exit status and run status are derived from the same value that recorded the failure, not set independently.

**5.3 Degradation is visible in the artifact. [judgement]**
A fallback path records that it was taken. Silent fallback is indistinguishable from success and defeats every downstream gate.
*Why:* both stage-6 `call_llm` sites are wrapped in `try/except` with silent fallbacks — `_classify_geographic_scope` returns `'National'` and the segment reroute is skipped. Under the corpus gate, which stubs the LLM, those paths are always taken and therefore never tested.
*Check:* no general mechanical form — a per-fallback review. §6.8 is the narrower, buildable version of this for the two fallbacks already named: a test that drives each `except` branch and asserts the marker is written.

**5.4 No bare swallow. [ratchet]**
`except Exception: pass` requires a comment stating what is expected and why continuing is correct.
*Why:* `run_full_pipeline.py`'s stage-5b cost read was wrapped in exactly this — a missing file, malformed JSON, or a permissions error reported the same `cost: 0.0` as a stage that genuinely cost nothing, so `total_cost` was silently short and the run still claimed success (#489, fixed by logging the exception instead of swallowing it).

**5.5 Tools and gates fail closed. [gate]**
A validator, gate or script exits non-zero when it could not do its job. "Nothing to compare" is a failure, not a pass.
*Why:* `render_gate.py` (then `gate_render.py`) used to never clear its output directory and never call `sys.exit`. Re-rendering into a reused directory after a code change that crashed every render left the previous arm's files in place — a run where **all 100 CVs failed** reported `compared 100 / identical 100 / CHANGED 0 / PASS`, exit 0. Fixed in #589 (closing #584): the script now clears its output directory before every run and exits non-zero on any comparison failure or crash.

**5.6 Retries live in exactly one stated layer: the LLM client, and nowhere else. [gate]**
A stage never wraps its own retry loop around a `call_llm` call; `_call_with_retry` (`llm_client.py:230-295`) is the one place a transient LLM fault gets a second attempt.
*Why:* true today — checked all 9 `call_llm`-calling stage modules for `for attempt in`/`retry_count=`/`@retry`/`_call_with_retry`, zero hits — but worth stating because the failure mode is cheap to introduce and expensive to notice: a stage-level retry stacked on the client's own would compound silently. It already compounds once, underneath this layer and outside this file's control: both providers' SDKs retry on their own defaults (OpenAI's client `max_retries=2`, botocore's `max_attempts=3`), so one logical `call_llm()` call can cost up to 4×3=12 raw requests today, and up to 24 when a Bedrock JSON-repair cycle also fires (`llm_client.py:727-765`) — a multiplier nothing here currently documents or caps.
*Check:* `grep -rn 'for attempt in\|retry_count=\|@retry\|_call_with_retry' src/unified_pipeline/stage_*.py` returns nothing.
*Also:* the compounded ceiling itself — up to 12 raw requests per logical call today, 24 with a Bedrock JSON-repair cycle — is stated once, next to the code that produces it, not only in this paragraph. Neither `_call_with_retry`'s docstring nor the JSON-repair branch (`llm_client.py:727-765`) currently names the number; a provider SDK's own `max_retries`/`max_attempts` default changing is a real change to this ceiling and should be visible at the call site, not just here.
*Check, this half:* `llm_client.py` states the current worst case as a comment or constant next to `_call_with_retry`, and it agrees with the number in this paragraph. Unmet today — no such comment exists yet. Too fiddly a shape (a product across two SDK defaults and one conditional repair branch) for an AST scan to gate on; a human re-reading both sites is the check.

**5.7 A retry is recorded, not silent. [gate — check pending]**
An attempt count belongs in the metrics dict a stage returns, not only in a log line.
*Why:* `_call_with_retry`'s own docstring says it plainly — "the wait a caller actually experienced is not currently reported anywhere" (`llm_client.py:238-247`). Every retry today is a `logger.warning` per attempt and nothing else. A pipeline that starts succeeding only on attempt 3 is the leading indicator of a provider degrading or a prompt drifting out of schema, and there is currently no artifact-level signal that it's happening. The one place attempts genuinely *are* persisted is the unrelated, currently-disabled run-level retry (`auto_retry.py`'s `run.attempt_count`) — call-level attempts have no equivalent, and if that flag is ever turned on, its up-to-3 relaunches each independently carry the up-to-12/24 multiplier from §5.6, compounding further with nothing recording either layer.
*Check:* `call_llm()`'s return includes an attempt count wherever `_call_with_retry` fired more than once. Not implemented yet — no such field exists today; see §9.

**5.8 Artifact writes are atomic. [judgement] — preventive, no incident yet.**
Write to a temp path in the same directory, then rename over the final name, for any multi-step write — never open the final path directly and write into it incrementally.
*Why:* every stage across both drivers writes its output the same way — `open(output_path, 'w')` then `json.dump`, or `Document.save(output_path)` — straight to the final path. Zero temp+rename sites exist anywhere in the pipeline (checked all twelve stages plus the stage6 package). A crash or `SIGKILL` mid-write leaves a truncated-but-present file readable under the final name, and the one place that checks a prerequisite before trusting it — `run_full_pipeline.py:188`'s `prereq_path.exists()` — checks exactly that: existence, not completeness. No CV has yet been traced to a truncated-write failure; this is the standing exception above, not the traceable route — the failure mode is a stage silently consuming a half-written JSON file and either crashing opaquely or, worse, parsing a partial structure as if it were whole.

*(§5.9 retired — folded into §1.5.)*

**5.10 A check that can fail has a stated recourse: retry-once-named → quarantine → raise. "Accept anyway" is not on the ladder. [gate]**
Content that is structurally valid but wrong — a hallucinated taxonomy code, a 5,000-character span with no line breaks where segmented entries were expected — gets the same discipline as a thrown exception: something decides what happens, and that decision is visible.
*Why:* two live, systemic gaps share this shape. No consumption site on the live driver path (`stage_3a` → `stage_3b` → `stage_4` → `stage_6`) validates an LLM-returned taxonomy code against any canonical list before using it — the one validating mechanism in the repo, `taxonomy_mapper_v2.py`'s enum-constrained schema, is unreachable from either driver (§1.5). An invalid or hallucinated code either gets a generic default field schema at stage 4 or is silently swept into the Appendix at stage 6 alongside every legitimately-unrouted code, with no distinction and no warning — generalizing what this doc already documents for `N2` specifically (§9) to the fate of *any* invalid code, by construction. Separately, a purpose-built check for exactly the "one unbroken span, no line breaks" segmentation failure already exists — `segmentation/entry_validator.py`'s oversplit/undersplit LLM judgment, with a confidence threshold and auto-repair — but it is orphaned, imported only by a module neither live driver calls. Both gaps are the same failure: a check exists or nearly exists, and nothing wires it to a disposition.
The ladder, in order: retry once, with the re-prompt naming the specific defect ("previous output contained no line breaks; emit one entry per line") — not retry-until-plausible, which selects for passing the predicate rather than for being correct, since a predicate is a necessary condition, never a sufficiency test; then quarantine, for which this pipeline already has an architecturally correct sink — the Appendix — provided the quarantined entry carries the predicate that failed, not a silent drop into the same bucket as everything else; then raise, if neither applies. Each boundary that receives text or a classification from outside states its own predicates, and the thresholds live next to the code they guard, not in this document, each with a `ponytail:`-style comment naming its calibration and ceiling (e.g. "bound guessed from the 100-CV corpus; revisit if extraction quality changes") — per §3.9.
*Check:* a plausibility-predicate failure appears in the returned metrics dict (§7.1's pattern), and a quarantined entry carries the failed predicate's name in the artifact next to it.
The taxonomy-code half is tracked at #651.

**5.11 Determinism policy is stated per stage: temperature, retries, and what the corpus gate's real-LLM arm would claim. [judgement] — preventive, no incident yet, tracked at #649.**
*Why:* a prompt or model-parameter change is a behaviour change under §6.3's table, not a doc tweak — nothing currently says so, so a classification prompt can be reworded today and described in the PR as a comment/doc change. Separately, `render_gate.py`'s LLM stub (`call_llm` monkeypatched to raise, so every render takes stage 6's fallback branch) is honestly documented as a mechanism — `docs/guides/render-doctor-gates.md` states plainly that calls are neutralised for determinism — but not as a scope limit: nowhere does the gate's documentation say the success path of an LLM call is structurally untested by it. A relocation gate's "identical corpus render" claim is only coherent if the LLM arm is pinned or stubbed the same way both times; this doc doesn't currently say which, for any stage.

**5.12 A stage artifact records the prompt template hash, the call params, and the model id the call actually used. [gate — check pending]**
Every LLM-calling stage's returned metrics include three fields: `prompt_template_hash` (the system prompt, user template, and any tool/response-schema definition, hashed *before* CV content is interpolated in — it identifies the template and call shape, not the per-call, per-CV text), `params` (temperature, max_tokens, schema version), and `model_id`.
*Why:* `"gpt-5.1"` is a repeated default-parameter literal in `taxonomy_mapper_v2.py` that disagreed with the model `llm_config.yaml` actually resolved (§8.2, #377) — a 96-CV batch was stamped with a model it did not use, in both the CLI banner (#444) and the stage artifacts themselves (#459). Separately, §5.11's own rule has no way to fire without this: a prompt or parameter change is a behaviour change under §6.3's table, but nothing in a corpus run today makes one *visible*, so there is no artifact-level basis for the A/B §6.3 asks for. `prompt_template_hash` is exactly that basis — hashed pre-interpolation so it fingerprints the template, not the CV content flowing through it, which keeps this rule clear of §4.7's PII scope. This is the checkable half of §5.11's determinism policy; where prompts live and how they're diffed is a separate, unscoped design question this rule doesn't answer.
*Check:* each LLM-calling stage's returned metrics dict includes `prompt_template_hash`, `params`, and `model_id`. `model_id` is read from the provider's response where one is returned — both OpenAI's and Bedrock's responses carry a model identifier that can be more specific than the request — and falls back to the request/config value only where no response-side field exists. Not implemented yet — this is a target, not a measured state; see §9.

## 6. Testing expectations

**6.1 Tests assert on behaviour, not on structure. [judgement]**
Assert on rendered output, returned values, and artifact contents — not on which module a function lives in or how many helpers it has.
*Why:* this is precisely what makes a relocation PR safe to sign off. Tests that encode structure make refactoring expensive and prove nothing about correctness.
*Check:* not mechanically checkable in general — a reviewer's read of what a new or touched test actually asserts on.

**6.2 A test must fail if the thing it covers is deleted. [gate]**
"The suite passes" is not a standard. The standard is that the suite would notice.
*Why:* measured by ablation over the 100-CV corpus, **31 of 44 stage-6 renderers can be deleted whole with all 437 pipeline tests green** — including publications, grants, teaching, service and memberships. Deleting `_add_track_change_deletion` removes 1,926 tracked deletions across 68 of 100 CVs and every gate still passes. That ablation is against a 437-test collection that has since grown to 714 (§9) and hasn't been re-run — the *ratio* isn't current, only the failure mode it demonstrates is.
*Check:* the cheapest mutation test there is — delete the function, run the test, confirm red, restore.

**6.3 Match the gate to the kind of change. [judgement]**

| Change | Gate | Written |
|---|---|---|
| Pure relocation | identical corpus render **and** identical XML fingerprint, two arms, same day | after |
| Behaviour change | tests asserting the new behaviour, plus render attribution with zero unexplained deltas | **before** |
| God-function decomposition | tests pinning current behaviour | **before** |

*Why:* the #398 section-split stack sat 44 commits behind `dev`, about to merge on a relocation gate alone — no fingerprint or behaviour check — which would have silently reverted 25 methods' worth of shipped stage-6 fixes (#570).

A relocation gate cannot sign off a behaviour change; it is defined as proving nothing changed. Conversely, unit tests alone do not discharge a relocation — per 6.2, they largely cannot see one.

A prompt or model-parameter change is a **behaviour change** by this table, not a relocation and not a doc tweak — see §5.11.

A reviewer's classification call by construction — not mechanically checkable. The author's half of it (which gate, and where the evidence is) belongs in the PR description; see "What a PR description must contain" above.

**6.4 Cross-process contracts get contract tests. [gate]**
If another process consumes your output, pin its shape.
*Why:* the precedent already exists and works — `scripts/doctor_one.py` and `scripts/score_one.py` both have tests covering "stdout is exactly one TSV line, diagnostics go to stderr, failure exits non-zero." Copy that shape.

**6.5 Corpus coverage is disclosed, not assumed. [judgement]**
The corpus has holes: section E has zero entries, `M4C` has one entry in one CV, and 86 of 100 CVs have no table-sourced entry. A change touching a thinly-covered area says so, because a green gate there means little.

**6.6 A gate cited as an acceptance criterion exists in the repo before anything depends on it. [judgement]**
If a plan, wave, or PR description names a script as a pass/fail gate, that script is committed under `scripts/` — not a prototype on one contributor's machine that the plan assumes into existence.
*Why:* #584 found that waves 3–5's own acceptance criteria — "the render gate" and "the doctor gate" — did not exist anywhere in the repo: `git log --all -- '*gate_render*' '*doctor_gate*'` returned nothing. It has since been fixed (`scripts/render_gate.py`, `scripts/doctor_gate.py` and their `_compare` counterparts, landed in #589 and wired into CI's `pipeline-tests` job), so this rule generalizes a resolved incident rather than naming a still-open gap — the same way §5.5 cites an already-fixed bug as its own *Why*. It is still distinct from what §5.5 and §6.3 already cover: §5.5 is a gate that runs and reports a false pass, §6.3 is using the wrong kind of an existing gate. Neither catches a gate that a plan relies on before it has been written at all.
Naming an owner was also part of #584's own definition of "done," and never actually happened — no assignee, no target date, in the repo or the issue. That half has no positive incident behind it, so it isn't stated as a rule here.
This applies to this document's own rules too, one level down: a `[gate]` here citing a check that isn't in the repo is the same gap as a plan citing a script that doesn't exist. Every rule currently in that state is marked **[gate — check pending]** rather than left as a plain `[gate]` naming machinery nobody can grep for — `grep -c '^\*\*[0-9].*\[gate — check pending\]' docs/CODING_STANDARDS.md` is the live count of rule headers carrying it (not this sentence's own uses of the term), so it's not a number restated here to drift out of sync with the file.

**6.7 A thinly-covered code gets a synthetic fixture before code touching it merges. [judgement] — preventive, no incident yet, tracked at #650.**
§6.5 requires disclosing a corpus hole; this is what closes one.
*Why:* real faculty CVs cluster around the middle of the distribution, so the corpus's own documented holes (§6.5: section E empty, `M4C` at n=1, 86 of 100 CVs with no table-sourced entry) aren't closed by waiting for more real CVs — a bigger corpus of the same shape has the same holes. A dozen constructed CVs — an empty section, a single-entry section, a maximal section, duplicate entries, a degenerate date, a non-ASCII name, an entry that legitimately matches two codes — cover boundary conditions a green gate over the real corpus currently says nothing about. Disclosure tells a reviewer the gate means little there; a fixture is what makes it mean something again.

**6.8 Every recourse path is driven by a test, the same as every thrown exception. [gate — check pending]**
Every `except` branch that isn't a bare re-raise, and every plausibility predicate's failure arm (§5.10), has a test that feeds it the input that triggers it and asserts the recourse actually fires — not just that the pipeline completes.
*Why:* this is §5.3's own gap, generalized and confirmed still open. Neither stage-6 LLM fallback it names — `_classify_geographic_scope`'s `except: return 'National'` or `_reclassify_entry_segments`'s `except: return None` — has a test that drives its `except` branch and asserts anything about the fallback's own behaviour; the one test that does trip `_reclassify_entry_segments`'s exception path (`test_stage6_unrendered_recovery.py`) does so as a side effect of testing dedup, and asserts nothing about the reclassify outcome. Under `render_gate.py`, whose LLM stub makes every call raise, this isn't a coverage gap so much as an inversion: the fallback path is the *only* path a corpus render gate run ever exercises, so a green gate is silently certifying the degraded pipeline, never the intended one. #640 already tracks the same class scoped to `llm_client.py`'s own exception paths; this extends it to stage 6's, tracked separately at #652 with the exact two functions and file:line. A fixture proving a predicate's failure arm actually fires — the 5,000-character no-line-break paragraph, the hallucinated taxonomy code — is worth more than the rule describing it, because it's the only thing that would have caught either gap directly instead of by inspection.
*Check:* an AST/coverage cross-reference between `except` blocks (and §5.10's predicate-failure arms) and which lines a test suite actually exercises. Real engineering, not a one-liner. Not implemented yet — no script does this cross-reference; see §9.

**6.10 Coverage is measured against the code a run actually executes, and reported rather than gated. [gate — check pending]**
Coverage is a reported number, not a merge condition. Every measurement states the command that produced it and which tree it covers, and the figure that carries weight is coverage of modules reachable from a live driver (`run_full_pipeline.py` or `web_interface/backend/app/pipeline/orchestrator.py`), not the raw repository total. No minimum percentage is enforced and no build fails on coverage.
This sits next to three rules it is often confused with. §6.2 asks whether the suite would notice a function's deletion; §6.8 asks whether every recourse arm is driven by a test; §6.9 asks whether a *reviewer* can check a function's case list without reconstructing it. All three are per-function. This one is the project-level counterpart: what the number is, what it is measured over, and what it is allowed to do to a build.
*Why:* on #644 mrj4001 asked for exactly this and said why — *"this makes it difficult for reviewers to consistently determine whether regression suites cover all meaningful error and decision paths [...] an objective project-level measure instead of relying on individual judgment about test coverage."* Seven review threads on that PR had already been spent enumerating cases by hand. That is the incident behind the first half of the rule.
The second half — measure against *reachable* code — is its own incident, from building the first. Measured on `dev` at 2026-08-31, `src/unified_pipeline` is 27.1% covered counting statements alone, which the command's own total line reports as 26% because coverage.py's headline blends statements and branches (25.5% before rounding). Quote which of the two a figure is; they are not interchangeable and the gap widens as branch coverage lags. Either way the repository-wide number is misleading in a way that would have set the wrong target: 13,707 of the 23,293 uncovered statements sit in 87 modules that **no live driver reaches at all**, and a further 4,346 statements sit in modules that are partly tested but equally unreachable. Coverage of the code a corpus run actually executes is 55.7% of statements (52.5% on the blended headline). A percentage target set against the raw figure would have commissioned tests for an abandoned parser generation, superseded segmentation strategies and one-off operational scripts — `docs/LLM_MODELS.md` already lists those same families as "never reached on a live run" — while saying nothing about the live path. Reachability analysis has to be `sys.path`-aware to get this right: `segmentation/chunked_chat_hierarchy_extractor.py:30` does a `sys.path.insert` and then imports by bare module name, and a package-qualified-only resolver silently drops that edge, misclassifying `signature_based_segmentation.py` — which `segmentation/README.md` marks as the production path — and the whole `core/validators/*` family as dead. That is the same `sys.path` problem tracked at #702.
"Reported, not gated" is also deliberate rather than provisional. The 100-CV corpus run is the project's real acceptance evidence, and a coverage threshold that can fail a build is a second gate able to stop it for a reason unrelated to output correctness. §5.5 makes tools fail closed; a coverage gate would fail *closed on the wrong thing*.
Coverage counts executed lines, not asserted behaviour, so a rising number is not on its own evidence of a stronger suite — #643 shipped an integration test that passed while the code path it existed to cover was completely broken, because the asserted string reached the prompt by an unrelated fallback route. §6.2 is the rule that catches that; this one does not replace it.
*Check:* `pytest --cov=src --cov-branch --cov-report=term-missing`, with `pytest-cov` pinned in `web_interface/backend/requirements-dev.txt` and `.coveragerc` omitting `*/tests/*` — without that omission the suite's own files enter the denominator and the same run reports 35% instead of 26%, which is the first way this number can be quoted wrongly. `--cov-report=term-missing` names the uncovered lines and branches directly, which is the "clearly identifying any uncovered lines/branches" half of the ask. Two pieces are not built: nothing computes the reachable-module set as a standing check (it was done by hand for the figures above), and no CI job runs coverage even non-failingly. `--cov=src` also covers the pipeline only — the backend's 56 files under `web_interface/backend/app/` are outside it, and that suite cannot run without database credentials. The 80% target on reachable code, and the per-module worklist, are tracked at #704.

## 7. Contracts and configuration

**7.1 Nothing parses another process's stdout. [ratchet]**
Progress is a callback. Metrics are a returned dict. `print()` is for humans.
*Why:* `print()` is currently a wire protocol on two live consumers — `orchestrator.py:129-138` regexes stage output into the progress bar, and `run_corpus_batch.sh` greps four literals into `summary.tsv`. No test guards either, so rewording a print silently blanks the progress bar and the batch metrics. The precedent for doing this properly already exists: the `cancel_check` callback threaded into stages 2 and 4.
*Check:* two §9 rows, both ratcheted by `scripts/check_standards.py` — the `re.compile(` count inside `PROGRESS_PATTERNS`, and every `print()` outside `tests/` and `scripts/` (ruff `T201`, scoped by `ruff.toml`'s per-file ignores).

**7.2 One configuration source per consumer. [gate — check pending]**
Do not add a mechanism; use the one that exists, or delete one first.
*Why:* nine distinct mechanisms exist today: (1) root `config.yaml`, dead — **no Python reader** anywhere in the tree, yet the backend Dockerfile still copies it in; (2) `auth_config.yaml` + `config_loader.py`'s `get_config()`, also reached from the pipeline core via §1.4's `sys.path` backdoor, plus a DB-backed layer (`SystemConfig`) on top of the same file; (3) `llm_config.yaml` + `unified_pipeline/config.py`'s own, structurally separate loader; (4) `app/config/pipeline_config.py`, a hardcoded class, confirmed dead (its one importer is itself unimported); (5) `core/llm_config.py`, a second differently-named "llm config" module, zero importers, name-collides with (3); (6) the hardcoded `known_institutions` dict now at `stage6/resolution/institution.py:39-40` still claiming to mirror `config.yaml` (moved from the stale `stage_6_word_template.py:2507` this doc used to cite — see §9); (7) the `PRICING` dict in `unified_pipeline/config.py`; (8) `Settings.pricing` in `web_interface/backend/app/schemas.py`, a second hand-maintained pricing table in different units, itself dead but still there to trip over — plus two documentation copies, making model pricing defined four times total; (9) scattered direct `os.environ.get()`/`os.getenv()` reads outside both loaders, each hardcoding its own default inline.
*Check:* the mechanism inventory above (nine, enumerated with file:line by category) is recounted at each review; a ninth-plus-one mechanism or an eleventh pricing copy fails the count. Trends to 2 (one backend, one pipeline-core). Not implemented yet — no script counts this, the inventory above is hand-kept; see §9.

**7.3 A produced field is rendered or explicitly declared unrendered — and a stage validates the shape of what it consumes, the same as what it produces. [gate — check pending]**
*Why:* stage-6 section renderers are fixed-slot `fields.get(...)` enumerations, so a stage-4 field the renderer does not name is dropped with no warning — `grant_number` being the worked example (fixed since as a one-off `.get()` addition, not a registry — see §9). The consuming half is the same gap stated the other way round: `stage_3b_entry_classifier.py` subscripts stage 3a's raw LLM JSON directly, with no key or type check, which is why a malformed or missing `code` field there was a `KeyError` that could abort a run, not a caught, recorded defect (#558, closed). #567 is the open, better-scoped umbrella tracking this across `stage6/` and the doctor artifact dicts — extend that issue rather than filing a new one; this rule is the standard the fix in #567 is aimed at meeting, and §8.1's typed-record rule is the shape it should take.
*Check:* a registry of the keys stage 4 can emit, and a test asserting each is either rendered or listed in an explicit `DELIBERATELY_UNRENDERED` set. For the consuming half: each inter-stage artifact has a declared shape (even a lightweight one — required keys, allowed taxonomy codes per §5.10), and the consumer raises on a genuine surprise rather than subscripting blind. Neither half exists yet — no registry (`grep -rl DELIBERATELY_UNRENDERED\|FIELD_REGISTRY` returns nothing) and no declared per-artifact shape; see §9.

**7.4 Time is an input, not an ambient fact. [judgement]**
*Why:* `datetime.now()` in stage 6 does more than stamp a date. In `stage6/sections/education.py:78` it decides whether a degree renders as in-progress, and in `stage6/sections/research_support.py:71` it reclassifies grants with past end dates from M2A into M2B — so **the current date moves content between sections**. That makes output non-reproducible and makes any cross-midnight A/B comparison meaningless. Neither boundary has a test pinned at the date it names — the in-progress cutoff, the M2A/M2B end-date flip — ideally one straddling December 31, so the reclassification is asserted directly instead of inferred from a render that happened not to cross midnight.

**7.5 Secrets come from the environment or a secret manager, never from a file in the repo. [gate — check pending] — preventive, no incident yet.**
A config file may name which environment variable or secret-manager key holds a value; it does not hold the value itself.
*Why:* §7.2 already documents nine distinct configuration mechanisms and zero rules about what belongs in them. Nothing today says a credential can't land in one of those files verbatim, and the mechanism count alone means there are nine places it could.
*Check:* a secret-scanning tool (gitleaks or trufflehog) runs an incremental scan over each push's diff range in CI, plus a full-history scan on a schedule and once, as a baseline, when this is first wired up — full history on every push is unnecessary once a baseline scan has run. An existing tool doing this beats a hand-rolled pass over nine mechanisms, the same "does an existing tool already do it" question §1's minimalism table asks of everything else. Not wired into CI yet; see §9. If a secret is ever found already committed, the response is rotation, not deletion — history is visible to anyone with clone access, and removing the line is the common mistake that leaves the credential live.

**7.6 An artifact's shape is versioned, and a consumer states what it reads. [gate — check pending] — preventive, no incident yet.**
A change to an artifact's JSON shape bumps its schema version. A stage that reads another stage's artifact declares the versions it accepts and fails closed (§5.5) on one it doesn't — it does not read absent keys as absent data.
*Why:* old runs' artifacts persist in S3 under whatever shape they were written with, and stage readers are fixed-slot `.get(...)` enumerations, so an old-shape artifact is indistinguishable from a sparse one. That's §7.3's silent-drop failure relocated to the inter-stage boundary, where no renderer registry will catch it. §5.12 already stamps a schema version into a call's `params`; this rule is what that stamp obligates — the version exists to be read by something, and nothing reads it yet.
*Check:* each artifact reader declares accepted versions; a test feeds it a well-formed artifact one version older than accepted and asserts a hard failure, not a partial read. Not implemented yet — no reader declares a version today; see §9.

**7.7 The schema changes only by migration, and a migration downgrades or says why it can't. [gate — check pending]**
Every model change lands with its Alembic migration in the same PR. Every migration implements `downgrade()`, or carries a comment stating why it can't and what rollback means instead. A merged migration is never edited — a correction is a new migration.
*Why:* both halves of this rule are earned, not preventive. `6291772` ("merge divergent Alembic heads blocking dev rollout") is the incident: two migrations branched off the same parent and merged to `dev` independently, producing two heads; `alembic upgrade head` then refused with "Multiple head revisions," which crashed the DB-migration init container — every dev rollout from 2026-06-26 until the fix **silently fell back to the last-healthy image**, so merged work looked live and wasn't. And `faf90b7` is the "never edited" half's incident: an already-merged migration was edited in place (not superseded by a new one) to add explicit varchar lengths, because prod had already drifted from it via a manual schema patch — the migration file and deployed reality had desynchronized, the same "second definition drifts" failure as §1.5, in DDL. `web_interface/backend/alembic/versions/` has 13 of 14 files with a real `downgrade()` today; the 14th is a legitimate no-op merge revision (the one `6291772` produced), not a violation.
*Check:* CI runs `alembic upgrade head` then `alembic downgrade -1` against a scratch DB for the newest migration on every PR touching `alembic/versions/`; a migration that can't downgrade must carry the irreversibility comment for the check to pass instead. Not implemented yet — no such CI job exists; see §9.

**7.8 A log line has a purpose, and its level states it. [judgement] — preventive, no incident yet.**
§7.1 says what a log line can't be (a wire protocol); §4.7 says what it can't contain. This says what one is *for*: **ERROR** means a person should act; **WARNING** means degradation someone will need to find later — and per §5.3 the artifact, not the log, is the durable record of that; **INFO** marks run lifecycle; **DEBUG** is free. Anything WARNING or above carries the run id — in a process running three concurrent pipelines, a log line that can't be attributed to a run is noise at best and misattribution at worst.
*Why:* no incident named. The nearest miss is §4.2's — `Log` rows written against the wrong run — fixed as a state bug; the fix only stays fixed if run-attribution is a stated obligation rather than an accident of the current code. Level choice isn't mechanically checkable, which is why this is `[judgement]` rather than a fourth marker; the run-id half is closer to checkable and could split out as its own narrow gate later if it proves worth enforcing on its own.

**7.9 The runtime version is defined once, by the image tag. [gate]**
`FROM python:X.Y-slim` in `web_interface/backend/Dockerfile` is the definition. Every other place that states the version — the `setup-python` pins in `ci.yml` and `deps-audit.yml`, the prerequisites bullet in a README — restates that number and must agree with it. Today that number is **3.14**. Moving it is a deliberate PR that changes the tag first and everything that echoes it in the same commit.
*Why:* §1.5's one-definition failure, in prose. The version has moved twice — `af2cef4` (`python:3.11-slim` → `python:3.13-slim`) and `8bc3750` (→ `python:3.14-slim`) — and both commits correctly carried the Dockerfile, both workflow pins and `requirements.txt` together, so the executable copies never drifted. The five prose copies did: `README.md`, `docs/PIPELINE_README.md`, `docs/TECHNICAL_README.md`, `web_interface/README.md` and `web_interface/TECHNICAL_README.md` were still claiming 3.9, 3.10 or 3.11 against a 3.14 image more than five weeks after the last bump. The cost isn't a broken build — nothing executable read them — it's that a contributor who reads a prerequisites bullet argues for the wrong runtime, which is the incident (2026-08-21): a working session spent disputing the project's Python version on the strength of a stale README, against a `dev` tree that is uniformly 3.14 in every place a machine looks.
*Check:* `scripts/check_standards.py` reads the version out of the backend Dockerfile and flags every `python-version:` in `.github/workflows/` and every "Python X.Y" in a Markdown file that disagrees with it. 0 today. The scan is deliberately literal: prose that has to name a superseded version for history writes the bare number, without the word "Python" in front of it — there is no waiver comment for Markdown, since §3.7's `# standards-waiver:` form is a heading here.

## 8. Types and literals

Where §3 is about function-level shape, this is about the shape of the data and the constants that pass through it. §8.1, §8.2, and the first half of §8.3 are **[judgement]** — per "How to read this," a question answered in the PR description, never a veto. None of the three is a count-triggered check: a `.get()` count or a repetition count is at most where a reviewer's eye lands first, not the reason to refactor. The actual question is whether the data has a known and stable structure (§8.1, and §8.3's first half) or whether the repeated value names a meaningful shared concept (§8.2) — and even then, only in code the PR is already changing: none of the three asks for an existing dict or literal in untouched code to be converted on sight. Say so in the PR instead.

**8.1 A dict crossing a function boundary is a typed record, not a bag of `.get()` calls. [judgement]**
Data with a known and stable structure that crosses a function or stage boundary and gets read by more than one caller is a dataclass, `TypedDict`, or Pydantic model — not a raw `dict` re-interpreted ad hoc by each reader.
*Why:* three corpus-observed defects share this exact root cause. `_fill_personal_data` assumed `home_address` was a string and called `.replace()` on it; 2 of 96 runs in one batch produced **no output document at all** (#442). The same function's `phone` field arrived as a dict of three numbers, and none of them rendered, with nothing logged (#450). `split_fused_citation_entries` called `.splitlines()` on `formatted_citation` before checking its type, which aborts the run on any non-string value (#554). None of the three would have been caught by a type annotation alone — they all arrive as `Any` off `json.load` — but a typed record forces the read site to declare what it expects, instead of discovering the mismatch at whichever method the wrong type happens to reach first. The flip side holds too: `Haystack` is already a `NamedTuple`, and all three call sites unpack it positionally anyway, one of them silently discarding `.tokens` (#280) — a typed record only pays off if callers use the names.
Not every dict needs this: one that stays inside a single function, or is read once, is fine as-is — #554's own filer explicitly declined a dataclass fix for that specific defect in favor of a boundary-normalizing function, and that was the right call for a single read site. Nor is an existing dict converted just because its structure is known: the conversion belongs in a PR that is already changing that data structure, and only where the stronger typing gives a clear benefit there — a drive-by conversion of code the PR does not otherwise touch is not what this rule asks for. The signal is a known and stable structure being read at a boundary, not a count of how many times it's read: `_create_grant_table`, the function CLAUDE.md names as the stage-6 field-drop example, has exactly one `fields` shape on every call — title / agency / role / dates — and its 28 `.get(` reads are where that shape shows, not the reason by themselves to fix it. #567 tracks this class across `stage6/` (zero `TypedDict`/`@dataclass` exist there today) and the eight review comments it collects, on top of #442/#450/#554, are what earns this its own rule rather than a line in §3.6.

**8.2 A magic number or repeated string is a named constant. [judgement]**
An inline literal used for classification, a threshold, or a comparison — not a one-off display value — gets a name that says what it means. A value earns a name when it represents a meaningful shared concept — a configuration, protocol, or domain value, or a classification threshold, that more than one place has to agree on. Appearing more than once is where you notice it, not the test: a repeated literal is not extracted for being repeated, and a string that merely appears twice as display text does not qualify.
*Why:* three independent misclassifications in stage 6 trace to exactly this, all three still live on `origin/dev` (#573). `_fill_licensure` treats any 10–11 digit string as an NPI and any text containing the substring `'DEA'` as a DEA number — including an entry whose text merely mentions "Dean" — so a state medical licence number can render as a federal identifier on a physician's CV (`stage_6_word_template.py:5901-5993`). `_fill_service` builds a 14-entry keyword list and then reads `journal_keywords[:10]`, an off-by-one slice that silently drops the one word "neurology," so identically-shaped Neurology and Oncology reviewer entries route to different sections (`stage6/sections/service.py:122`). And a postdoctoral-code set `{'C', 'C1', 'C2'}`, duplicated in two places, is missing `C3` in both, so Fellowship Training entries fall through to the Appendix. Naming each — `NPI_DIGIT_PATTERN`, `JOURNAL_SPECIALTY_KEYWORDS`, `POSTDOC_CODES` — doesn't fix the bug by itself, but it is what would have made the wrong value visible at the definition site instead of a corpus batch away from it.
A second, narrower incident: `"gpt-5.1"` is a repeated default-parameter literal across 5+ function signatures in `taxonomy_mapper_v2.py` (#377), and it disagrees with the model actually resolved from `llm_config.yaml` — every run in a 96-CV batch was stamped with a model it did not use, in both the CLI banner (#444) and the stage artifacts themselves (#459). Closer to §1.5's territory than a classic magic number, but the fix is the same shape: one named source of truth instead of a literal repeated on faith.

Taxonomy codes are this rule's canonical instance in this codebase. A comparison or routing decision on a code — `code.startswith('M2')`, `taxonomy_code in ('A', 'S0')`, a dict keyed on `'Q1'`..`'Q4D'` — reads through a module-level named constant whose comment says what the group means. The code list itself is defined once, in `src/unified_pipeline/core/taxonomy_v7.json` (loaded by `stage_3a_header_taxonomy_mapper.py:29` in both drivers; `taxonomy_reference.md` is its human companion), and §1.5's baseline already tracks the cost of not doing this: the taxonomy is defined 5 separate times in this codebase and at least two disagree, tracked separately at #681 — a constant here groups codes for one decision, it does not redefine the taxonomy. §5.10 separately tracks that no taxonomy-code validation exists on the live driver path. Done right twice already: `stage_5b_institution_enrichment.py`'s `INSTITUTION_CODES` (line 44) and `stage4/coercion.py`'s four `*_TAXONOMY_*` constants (PR #643).

**8.3 Modern typing on Python 3.14, and type checking passes for the files `mypy.ini` lists. [gate]**
Two halves under one number, the same shape as §3.7: item 2 below is a true **[gate]** — the check already runs in CI and either passes or it doesn't, within its configured scope; item 1 is **[judgement]**. The header tag names the stricter of the two.

*Item 1 — [judgement].* Write new and touched code in modern Python 3.14 typing/syntax — builtin generics, `X | None`, and the rest of what "3.14" implies for syntax (§7.9, merged from PR #679, owns the runtime-version rule itself — this half is only about the syntax that version enables). Prefer a `TypedDict`, dataclass, or Pydantic model over `dict[str, Any]` where the data has a known and stable structure, and avoid `dict[str, Any]` specifically where a more precise type can be written instead. Like §8.1 and §8.2 this is applied as judgement in the PR description — no `.get()` count, line count, or grep triggers it.
This does not restate §8.1 — §8.1 already owns the boundary-crossing-dict rule, with four traced incidents (`_fill_personal_data`'s untyped `home_address` and `phone`, #442/#450; `split_fused_citation_entries`'s untyped `.splitlines()` target, #554; `Haystack`'s fields unpacked positionally instead of by name, #280). This half is the general instruction those incidents are a special case of: reach for a typed record instead of an untyped dict at any boundary, not only at the ones already caught, and prefer that shape over `dict[str, Any]` from the first draft rather than after a corpus batch finds the gap. It applies to the code being changed — §8.1's limit holds here too: an existing dict elsewhere is not converted because its shape is known.
*Why:* preventive — no traced incident of its own. Added at the reviewer's direct request (PR #677 review, mrj4001, 2026-08-24); the cost it forestalls is the same class §8.1's four incidents came from — type drift and `Any` leakage into code nothing catches until a corpus batch does.
*Check:* the syntax half only — `ruff check` with the rule set in `ruff.toml` (pre-3.10 typing syntax `UP*`/`RUF013`; missing annotations `ANN*`/`RUF012`), summed and ratcheted by `scripts/check_standards.py` as two §9 rows: the counts may fall, never rise, and go to a hard zero once the repo-wide syntax sweep lands. The typed-record half stays where §8.1 and §8.2 are: stated in the PR description, not scripted.

*Item 2 — [gate], scoped.* Type checking passes with the project's configured checker for exactly the files `mypy.ini`'s `files =` line lists — today one module, `src/unified_pipeline/segmentation_regression.py` — and new code on that list does not introduce an avoidable `Any` escape. This is not a repo-wide type check and must not be read as one: the gate covers whatever is on the list, and widens only as the list does.
*Why:* the reviewer asked for this plainly, and CI already has the machinery: a `type-check` job (`.github/workflows/ci.yml`) runs `mypy --config-file mypy.ini` against a list that is, today, one file out of roughly 303 non-test `.py` files under `src/` and `web_interface/`. `ignore_missing_imports` and `follow_imports = silent` are also set, so the listed file is verified against its own annotations, not against the shape of anything it imports. Stating the rule as "type checking must pass" without naming that scope would claim far more than the CI job actually proves — the same overclaiming §5.2 ("never report success when a step failed") exists to catch, applied here to a rule's own wording rather than a script's output.
*Check:* `mypy --config-file mypy.ini` from the repo root — the exact command the `type-check` CI job runs. Passes today: the `type-check` job succeeded on `origin/dev` @ `360790f` (2026-09-01) — https://github.com/wcmc-its/CViche/actions/runs/33523076709/job/99906932102. Growing the scope is `mypy.ini`'s `files =` list; this rule gates whatever is on that list, not the codebase at large, until the list grows.

## 9. Where the code stands against this today

This is a target state, in two tables now instead of one. **Mechanically verified** is regenerated by `scripts/check_standards.py` — `--update` rewrites it in place, and the bare command is a CI gate that fails if it's gone stale. Thirteen rows are honest enough to reduce to a single script-checked count; the trade is that a script can't originate the file:line narrative the old hand-written version carried (which module, which line, "reaching 43 modules") — run `scripts/check_standards.py --report` for that detail. Seven of the thirteen (2.1, 3.7's dynamic-attribute row, 5.4, both 7.1 rows, both 8.3 rows) are `[ratchet]` — enforced against `scripts/standards-baseline.json` the same way §3.2a ratchets oversized-function debt: the target column reads "falling," not "0" (§3.2a's own refuse-to-raise contract applies here too). Three of those (7.1's `print()` row and both 8.3 rows) are sums over `ruff check --statistics` with the rule set in `ruff.toml`, not AST walks; ruff missing from `PATH` fails the whole check (§5.5), it does not read as zero. Both §3.7 rows also accept the waiver mechanism from "How to read this" — a `# standards-waiver: 3.7` comment reads as **~**, not **✓**. **Requires judgment** stays hand-narrated: architectural reads (how many drivers exist), incident narratives (a fallback recorded, a gate now fixed), and calls a syntax scan can't make. Together the two tables are every **[gate]**, **[ratchet]**, and **[gate — check pending]** rule, measured against `origin/dev` @ `5a0035a` (2026-08-14) — the original baseline was `4e62bbd`, 144 commits earlier, and a meaningful fraction had drifted in that gap. `check_standards.py` now warns when that sha falls more than 50 commits behind `HEAD` and fails past 200, per §5.10's own recourse ladder — the hand-narrated table is the half that can't re-verify itself by regenerating. Ten PRs are open at time of writing (#600, #620, #623–625, #641–644, #647); none are merged, so this reflects `dev` as it stands, not as it will once they land. Nothing here is a work item by itself — it is the honest baseline the rules are written against.

### Mechanically verified

<!-- check_standards:auto:begin -->

| Rule | Target | Today | |
|---|---|---|---|
| 1.2 pure layers import no `docx` | 0 | 0 | ✓ |
| 1.3 peers do not import peers | 0 | 0 | ✓ |
| 1.4 core does not import the web backend | 0 | 1 | ✗ |
| 2.1 no `db.query(` in `api/` | falling | 30 | ratchet |
| 3.x oversized-function debt (excess lines) | falling | 2419 | ratchet |
| 3.7 no metaprogramming | 0 | 0 (1 waived) | ~ |
| 3.7 dynamic attribute access (non-literal) | falling | 6 | ratchet |
| 5.4 bare swallows (`except Exception: pass`) | falling | 3 | ratchet |
| 7.1 stdout-parsing regexes (`PROGRESS_PATTERNS`) | falling | 4 | ratchet |
| 7.1 print() in library code (T201) | falling | 929 | ratchet |
| 7.9 restated Python version != the build image | 0 | 0 | ✓ |
| 8.3 typing syntax (UP*, RUF013) | falling | 233 | ratchet |
| 8.3 missing annotations (ANN*, RUF012) | falling | 556 | ratchet |

<!-- check_standards:auto:end -->


















### Requires judgment

| Rule | Target | Today | |
|---|---|---|---|
| 1.1 dependency direction declared | every package | **30** total `__init__.py` on `dev`, of which **6** state an actual import-direction rule (`stage3b/`, `stage4/`, `stage5b/`, `stage6/`, `stage6/sections/`, `services/`) — the rest state contents or purpose, not direction, and 12 are empty. Was 3 of 26 at `5a0035a`: each of the three package splits shipped one | ✗ |
| 1.5 one definition per vocabulary | 1 each | taxonomy defined 5× **and at least two disagree**; stage→path map 4×; stage order 3× | ✗ |
| 1.6 dependency pinned, license-checked, one lock | pinned + locked + scanned | direct pins hold (3 files, exact, verified identical where shared); no lock file exists (transitive versions unpinned); no license scan in CI — the new `deps-audit.yml` runs `pip-audit` over all 3 files, but that is a vulnerability scan, `--no-deps`, so neither the lock half nor the license half of the *Check:* is met | ~ |
| 2.2 one driver | 1 | 3 — `run_full_pipeline.py` 1,174 lines, `orchestrator.py` 1,399, `core/cv_pipeline.py` 1,602 (still imported only by 2 test files, `test_cv_pipeline.py` and `test_llm_client.py`) | ✗ |
| 2.3 stage owns its artifacts | no globbing | CLI still resolves stage 6 input by `glob(f"*{uid}*")[0]`, now 5 such sites in `run_full_pipeline.py` | ✗ |
| 3.4 receive your scope | 0 doc-wide searches | re-counted this round: **60** doc-wide text lookups across **27** `_fill_*` writers in `stage6/sections/`, plus 4 more search-resolving functions still in the monolith. The 23 classes `stage6/sections/__init__.py` lists reconcile against 27 writers because 3 modules hold more than one | ✗ |
| 4.1 no module state written after import | 0 | the 3 named examples still present, 2 at new addresses after the splits (`INSTITUTION_CACHE` → `stage5b/cache.py:33`, `_group_cache` → `ed_group_lookup.py:112`, `_storage` → `storage/factory.py:18`); the count is now reproducible from §4.1's own *Check:* — **30** sites across 17 files, or 25 across 16 excluding an orphaned analysis script | ✗ |
| 4.2 no process-global per-run mutation | 0 | **0 — fixed.** `orchestrator.py`'s `_RoutedStdout` router (installed once at import, dispatches per calling thread id) replaces the per-stage `sys.stdout` swap, closing #581; `prompt_logger.py` now scopes every write under `PROMPT_LOG_DIR/<run_id>`, closing #580 (PRs #585, #586) | ✓ |
| 4.2a ownership checked on artifact-read endpoints | 0 gaps | holds at 15 of 16 run-scoped routes (14 via `check_run_access`, 1 websocket via the same call); the 16th (`/admin/run/{run_id}/score`) is intentionally `require_admin`-only, not a gap. Hand-verified, not yet a standing test | ✓ |
| 4.3 complete cache keys | 0 | **0 — fixed.** `_institution_cache_key()` folds a hash of the owner context into the cache key, closing #582 (PR #585); now at `stage5b/cache.py:122` after the stage-5b split | ✓ |
| 4.4 own your region | per-section | shared `_overflow_entries`, `_appendix_pending`, `_cleared_tables` still declared and written only in `stage_6_word_template.py`, but `_cleared_tables` is now read cross-file from `stage6/sections/service.py:790`. Sections are mixins, so all 23 reach all three by construction; no section module mutates them today | ✗ |
| 4.5 lazy singletons locked | all | 6 locked (`llm/openai.py:14` and `llm/bedrock.py:50`, both under `llm/retry.py:137`'s shared lock; `async_rate_limiter.py`, `session_idle.py`, `saml_replay.py`, `login_throttle.py`), **7** unlocked (`storage/factory.py:18`, `stage4/schemas.py:609`, `taxonomy_mapper_v2.py` ×2, `config.py:450`, `consent.py:19-20`, and `async_rate_limiter.py`'s own `get_rate_limiter_sync()` sharing state with its locked sibling — orphaned module, #653). Revised upward twice now (4/1 → 6/5 → 6/7), the last two both pre-existing and simply missed — build the check rather than hand-count a fourth time | ~ |
| 5.1 error policy owned by the driver | 1 policy | 2 opposite policies — CLI catches at 11 sites, orchestrator raises, now pinned by a regression test on each side (#647, merged, §5.1); the policies themselves are still not unified | ✗ |
| 5.2 never report success on failure | enforced | met, with a regression test (`test_run_full_pipeline_exit_status.py`) | ✓ |
| 5.3 degradation visible in the artifact | all paths | 2 silent LLM fallbacks in stage 6, both still present (`stage_6_word_template.py:1098`, `:2034`); a third, in stage 3b, was made visible this round — a rejected taxonomy code now records `classification_source: llm_invalid_code` plus an `invalid_code_entries` stat (c6402bf) | ✗ |
| 5.5 tools fail closed | all | **0 — fixed.** `render_gate.py` clears its output directory and exits non-zero on any comparison failure; `render_gate_compare.py` refuses PASS on any mismatch (PR #589, closing #584) | ✓ |
| 5.6 retries live in one stated layer | 0 stage-level retries | 0 — checked all **12** `call_llm`-calling stage modules (was 9 before the stage3b/stage4/stage5b splits), no hits. (The nested SDK/botocore retry underneath `llm/retry.py` is real but is §5.6's own *Why*, not a stage-level violation.) | ✓ |
| 5.7 a retry is recorded | attempts in the returned metrics | not recorded anywhere — `_call_with_retry`'s own docstring still says so, now at `llm/retry.py:156` after the #496 split | ✗ |
| 5.10 a check that can fail has a stated recourse | ladder enforced | **1 of 2 now has one.** Stage 3b validates LLM-returned codes against `taxonomy_v7.json` and records the rejection (`classification_source: llm_invalid_code`, `invalid_code_entries`; c6402bf) — which meets the *Check:* at the producing site. Stages 3a/4/6 still consume codes unvalidated (#651 open), and `entry_validator.py` is still orphaned (0 Python importers) | ~ |
| 5.12 stage artifact records prompt template hash, params, model id | all LLM-calling stages | not implemented — no stage records `prompt_template_hash` or `params` anywhere. 2 of 12 LLM-calling stages (3a, 3b) do record a `model` under `meta`, but it is the request/config value, not the response-side model id the *Check:* asks for | ✗ |
| 6.1 tests assert behaviour not structure | all | still the norm for behaviour tests, but **212 of 1315** collected tests are now import-surface pins asserting which module a name lives in (`test_stage{3b,4,5b,6}_import_surface.py`, `test_run_doctor_contract.py`), added deliberately as split-safety contracts and re-derived from the AST on each run. Readable either as §6.4-shaped cross-file contracts or as the structure-coupling §6.1 warns about; marked partial rather than clean until that call is made explicitly | ~ |
| 6.2 a test fails if its subject is deleted | all | the 31-of-44 ablation wasn't re-run for this refresh either (it needs 44 delete-run-restore cycles over the corpus); pytest collection under `src/unified_pipeline/tests/` is now **1315** tests, up from 714 at `5a0035a` and 437 before that, so the ratio still needs re-measuring rather than assuming it improved — and "44 renderers" is itself a stale denominator now that `stage6/sections/` is 24 files / 39 classes | ✗ |
| 6.3 gate matches the kind of change | 3 gates | `render_gate_compare.py` now adds a C14N fingerprint of `document.xml` alongside the paragraph-text check — the doc's stated gap ("no fingerprint arm") is closed, though not confirmed across all three gate kinds in the table above | ~ |
| 6.4 contract tests on cross-process output | all | met for `doctor_one`, `score_one`; absent for the stage `print()` wire | ~ |
| 6.5 corpus coverage disclosed | practised | **now practised** — 6 merged PRs since 2026-08-26 carry an explicit §6.5 disclosure section, and 5 of 5 pipeline-touching PRs since 2026-09-01 do; the four earlier pipeline PRs (#641–#644) predate the habit | ~ |
| 6.8 every recourse path is tested | all | 0 of 2 named stage-6 fallback paths (`_classify_geographic_scope`, `_reclassify_entry_segments`) have a test driving the `except` branch and asserting the fallback's own behaviour; tracked at #652, still open. Definitions remain in the monolith (`stage_6_word_template.py:1000`, `:1939`); the call sites moved to `stage6/sections/presentations.py:89` and `service.py:395` | ✗ |
| 6.10 coverage measured over reachable code, reported not gated | measured + reported | `pytest-cov` now pinned; measured 2026-08-31 — 55.7% over the 102 modules reachable from a live driver (13,903 stmts, 6,163 uncovered); 27% raw over all of `src/unified_pipeline`. No CI job runs it and no standing check computes the reachable set; backend not measured | ~ |
| 7.2 one config source per consumer | 2 | 9 mechanisms; root `config.yaml` still has 0 readers; the "should match config.yaml" comment moved to `stage6/resolution/institution.py:39` | ✗ |
| 7.3 produced field rendered or declared | registry exists | no registry (`grep -rl DELIBERATELY_UNRENDERED\|FIELD_REGISTRY` — 0 hits). The worked example no longer holds as stated: `grant_number` is now read and rendered into Award Source (`stage6/sections/research_support.py:353-360`) — fixed as a one-off `.get()` addition, not via a registry, so the next unnamed field drops exactly as silently | ✗ |
| 7.5 secrets never committed to a config file | 0 findings | no scanner wired into CI yet — not verified either way | ✗ |
| 7.6 artifact shape versioned, consumer states what it reads | all readers | not implemented for stage artifacts — no stage-artifact reader declares an accepted version, and stage 3b now stamps a `classification_rules_version` (`stage3b/classify.py:446`) that nothing reads. The pattern is done correctly exactly once, outside this rule's scope: `auth.py:306` rejects any session cookie not stamped v2 | ✗ |
| 7.7 schema changes only by migration, downgrades or says why not | all migrations | 13 of 14 migration files have a real `downgrade()`; the 14th is a legitimate no-op merge revision. No CI job runs upgrade/downgrade; one past violation of "never edited" predates this rule (`faf90b7`) | ~ |
| 8.3 (item 2) type-check passes for the files `mypy.ini` lists | scope grows from 1 file | passes today — `type-check` job succeeded on `origin/dev` @ `7edf10a`, run 33773180953 (2026-09-03). Scope is `mypy.ini`'s one listed file, `src/unified_pipeline/segmentation_regression.py`, of **323** non-test `.py` files under `src/` and `web_interface/` — about 0.3% of the tree | ~ |

Item 1 of §8.3 has two rows in the mechanically-verified table, but only for its syntax half — pre-3.10 typing syntax and missing annotations, the shapes ruff can count. Its typed-record half, and §8.1 and §8.2 whole, have no row here: this table (per its own scope note above) covers `[gate]`/`[ratchet]`/`[gate — check pending]` rules; a plain `[judgement]` rule is tracked in its PR description, not measured here.

Of 42 rows across both tables: **10 met, 7 partial, 20 not met**, plus 8 rows that are `[ratchet]` rather than pass/fail — 2.1, 3.2a, 3.7's dynamic-attribute row, 5.4, both 7.1 rows and both 8.3 rows, all falling or holding since their baselines. None of this is the underlying pipeline getting safer; it's the measurement catching up to what was already true or already decided.

**Reading this honestly, again.** If only three things are done next, they should be:

1. **§6.2** — until a test fails when its subject is deleted, every other gate is being verified by a suite that demonstrably cannot see whole missing features. The suite has nearly doubled again in the intervening 154 commits (714 → 1315, 26 new test files), but none of that was renderer-ablation coverage, so the ratio remains unmeasured rather than improved.
2. **§5.3** — both silent stage-6 LLM fallbacks are still live, and still structurally untested: the corpus gate stubs the LLM, so the fallback path is the only path the gate ever actually exercises. Stage 3b's equivalent was made visible this round (c6402bf) — that is the shape these two still need.
3. **§7.3** — no registry exists yet. `grant_number` itself is fixed, but it was fixed the same way it broke — another one-off line in `_create_grant_table` — so the next stage-4 field this section doesn't name will drop exactly as silently as that one did.

Rows 1.2 and 1.3 remain green, and are worth repeating: the stage-6 split delivered the two boundaries that make section bleed structurally harder, which is the part of that work most worth repeating elsewhere.

## Appendix: the checks, as commands

The seven checks that used to live here as hand-run `grep` commands (1.2, 1.3, 1.4, 2.1, 5.4, both 3.7 rows) are now `scripts/check_standards.py` — run `--report` for the same file:line detail these commands used to print. They moved because grep on `getattr(...)` can't reliably tell a literal attribute name from a variable one, and grep on `db.query(`/`except Exception` gained nothing by staying textual once the AST scan existed for its neighbors; the rules themselves (§1.2, §1.4, §3.7 — all **[gate]** — and §5.4, now **[ratchet]**) and their *Why:* haven't moved, only the reproduction command has.

One Appendix-style command remains, because check_standards.py doesn't reach it (§9's build note above explains why: it needs a curated target list, not just a syntax shape) but it's still a fair one-liner once that list is a single well-known literal:

```bash
# 1.5  a shared-vocabulary literal should concentrate in one module, not spread
grep -cE "'F1'|\"F1\"" -r src/unified_pipeline/ web_interface/backend/app/
```

Single-quote-only undercounted — 15 single-quoted sites against 22 double-quoted ones, so the un-widened version was catching the *minority* shape. `-c -r` also prints one count per matching file, not a running total; read it as a file list, or sum the second column. A shortlist either way: an f-string or a variable holding `"F1"` won't match, the same honesty the paragraph above already gives `getattr(...)`.

§7.2 (config mechanisms) doesn't get one even here: the row's own claim is that *nine* mechanisms exist, so a one-liner would first need that list curated and named — at which point it stops being a quick command and starts being the finding itself.

Note for anyone reproducing these, or writing a new one: `grep` on the dev machines is ugrep, whose recursive mode **silently skips dot-directories**. For a complete sweep use `find . -path ./.git -prune -o -type f -print | xargs grep -l …` — or, for anything with a real AST shape, add it to `check_standards.py` instead of writing a ninth grep incantation.
