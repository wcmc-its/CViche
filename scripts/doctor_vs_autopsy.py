#!/usr/bin/env python3
"""Score one doctor_gate.py findings JSON against verified autopsy labels (#819).

    python3 scripts/doctor_vs_autopsy.py <doctor_gate.json> <labels_dir> [--json OUT]
                                         [--source-fixed]

The doctor's per-lint precision used to be re-measured by hand on every batch
and recorded nowhere the lint could see (#819). This scores an arm's findings
against the defects an adversarial autopsy verified, so a lint change reports
before/after numbers instead of a fresh hand-check. The ledger it feeds is
src/unified_pipeline/doctor/PRECISION.md.

Per lint, and per message shape of `stage6_render_warnings` (one lint key that
re-emits about twenty unrelated stage-6 checks, so it is split by `_STAGE6_SHAPES`):

- hits: the arm's findings on labelled runs, status "ran" only (a skipped or
  unreadable lint is not a detection). Findings on a run with no label file
  are counted apart and never scored.
- located: hits whose message or evidence starts with an entry index
  (`_IDX_PREFIX_RE`). The other lints name text, not an entry, and cannot be
  matched by index. Only the indices a finding prints are read, and the doctor
  prints at most 3 evidence items per finding (the stage-6 re-emit in
  doctor/lints/render.py, `FIELD_EVIDENCE_MAX_VALUES` for offschema_fields).
  A finding about more entries is matched on the 3 it lists, so matched and
  caught undercount for it.
- matched: located hits sharing an element_idx_start with a verified finding
  of the same uid -- the same entry, not necessarily the same defect.
  matched / located is a LOWER bound on precision: the autopsy listed defects
  it chose to verify, not every true signal, so an unmatched hit is a
  hand-check, not a proven false positive.
- caught: the verified findings this lint matched (its recall contribution).
- judged TP/partial/FP: verdicts the batch verifier gave this lint's findings
  (labels' `doctor_review`), and how many of those (uid, lint) pairs still
  fire in this arm. The verdict precision TP / judged is the one hand-checked
  number; it describes the doctor the verifier saw, so "still firing" is what
  an arm can move.

Recall: the verified findings carrying an element_idx_start that at least one
hit matched, overall and by severity, batch, batch_class (a batch synthesis's
own class: EBYSBC's E1..E36, s7ab's s7ab-1..s7ab-21), class_ref (the
cross-batch class it cites; YUYVIG cites an issue, "#1403", or "NEW") and stage
(the pipeline stage the verifier blamed first, "3b"; #1586 ranks misses by it).
A null class or stage is grouped as "(none)".
A finding with no index (`element_idx_start: null`) is counted apart: no hit
can match it. The autopsy's own `doctor_caught` verdicts are tallied beside it.

--source-fixed adds one split of the UNCAUGHT indexed findings: those that no
longer reproduce at source. The scoreboard doctors stored runs, so a stage-3b
miscoding the pipeline has since fixed where it arises (a header pin, the
duplicate marking) is still in the stored run, has no lint because new runs no
longer do it, and would count as a doctor miss for ever. For each run with an
uncaught finding, this checkout's stage-3b post-processing is replayed over the
stored `stage_3a` / `stage_3b` artifacts the doctor report names
(`replay_stage3b`); a finding is source_fixed when, at one of its entry
indices, the taxonomy code changes or a stored duplicate mark is no longer
made. It says the entry is treated differently now, not that the new treatment
is the one the verifier wanted. Only the deterministic passes are replayed (no
LLM), over the codes the stored run ended with. A run whose artifacts are
missing or unreadable is counted as not replayed and its findings stay
unjudged. Without the flag the output is unchanged.

Label schema, one `<uid>.json` per run. Ids, codes and counts only -- no CV text:

    {"uid": "ABCDEF", "batch": "EBYSBC",
     "findings": [{"id": "ABCDEF-01", "class": "whole_record_lost",
                   "class_ref": "s7ab-4" | null, "batch_class": "E1" | null,
                   "stage": "3b" | null,  # optional
                   "severity": "high" | "medium" | "low",
                   "element_idx_start": 32 | [32, 64] | null, "records": 5 | null,
                   "doctor_caught": {"verdict": "yes" | "partial" | "no",
                                     "lints": ["offschema_fields"]} | null}],
     "doctor_review": [{"lint": "offschema_fields", "shape": null,
                        "severity": "WARN", "verdict": "TP" | "partial" | "FP",
                        "element_idx_start": null}]}

An index is normalised by truncation (`30.0` and a stage-2 sub-entry `474.1`
both read as their source element), the same way on both sides.

Fails closed (CODING_STANDARDS 5.5): an unreadable input, a doctor_gate run
that recorded failures, a malformed label, or no run that is both doctored and
labelled exits 2 -- "nothing to score" is not a score.
"""
import argparse
import contextlib
import json
import logging
import re
import sys
from collections import Counter
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import NamedTuple

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# The shapes are the run-time ledger's (doctor/precision.py), so this scorer
# and the review copy's precision gate (#1589) name a stage-6 message alike.
from unified_pipeline.doctor.precision import (  # noqa: E402
    STAGE6_LINT,
    stage6_shape,
)
from unified_pipeline.stage3b.classify import detect_duplicates  # noqa: E402
from unified_pipeline.stage3b.context import (  # noqa: E402
    build_mapping_index,
    get_taxonomy_context,
)
from unified_pipeline.stage3b.header_pin import (  # noqa: E402
    apply_header_pin,
    apply_non_seat_pins,
)
from unified_pipeline.stage3b.io import _normalize_taxonomy_mappings  # noqa: E402

STATUS_RAN = "ran"
FAILED_KEY = "_failed"  # doctor_gate.py's top-level failure map
LOUD_SEVERITIES = frozenset({"WARN", "ERROR"})
VERDICTS = ("TP", "partial", "FP")
NO_CLASS = "(none)"
CLASS_GROUPS = ("batch_class", "class_ref", "stage")
CLASS_TEXT_MIN = 3  # the text table lists a class with this many findings; --json lists all
EXIT_INPUT_ERROR = 2

ARTIFACTS_KEY = "artifacts"  # run_doctor's per-run map of artifact name -> path
STAGE_3A_ARTIFACT = "stage_3a"
STAGE_3B_ARTIFACT = "stage_3b"
DUPLICATE_MARK_KEYS = ("is_duplicate", "duplicate_note")  # what detect_duplicates writes
STAGE3B_LOGGER = "unified_pipeline.stage3b"

# An entry index at the START of a message or one evidence string -- the
# formats doctor/lints/*.py and the stage-6 sidecar write: "entry 128 (M2B):"
# (extraction/render lints), "entry 136: {...}" (offschema_fields evidence),
# "element_idx_start 43" (reroute) and "element_idx_start=12" (memberships
# header skip), "idx 30.0". Anchored at the start because the rest of a
# finding can quote CV text, where a number after "entry" is not an index.
_IDX_PREFIX_RE = re.compile(r"(?:element_idx_start[ =:]\s*|entry |idx )(\d+(?:\.\d+)?)\b")



class InputError(Exception):
    """An input this tool cannot score; main() turns it into exit 2."""


@dataclass(frozen=True)
class Hit:
    """One doctor finding, reduced to what is scored."""
    uid: str
    key: str
    severity: str
    idxs: frozenset[int]


@dataclass(frozen=True)
class Verified:
    """One verified autopsy finding."""
    uid: str
    id: str
    batch: str
    severity: str
    class_ref: str
    batch_class: str
    stage: str
    idxs: frozenset[int]
    autopsy_credit: str | None  # doctor_caught verdict: yes / partial / no / None


@dataclass(frozen=True)
class Review:
    """One verifier verdict on a doctor finding."""
    uid: str
    key: str
    verdict: str


@dataclass
class Labels:
    """Every label file's findings and verdicts, and the uids they cover."""
    uids: set[str] = field(default_factory=set)
    verified: list[Verified] = field(default_factory=list)
    reviews: list[Review] = field(default_factory=list)


class Replayed(NamedTuple):
    """One stored stage-3b entry, as stored and as this checkout's replay leaves it."""
    old_code: str | None
    new_code: str | None
    was_dup: bool
    is_dup: bool

    @property
    def source_fixed(self) -> bool:
        """Recoded, or stored as a duplicate and no longer marked one."""
        return self.old_code != self.new_code or (self.was_dup and not self.is_dup)


class RunArtifacts(NamedTuple):
    """The two stored artifacts of one run that the replay reads."""
    stage_3a: str | None
    stage_3b: str | None


@dataclass
class LintRow:
    """One lint key's scores; the hit lists are for the hand-check."""
    key: str
    hits: int = 0
    loud: int = 0
    located: int = 0
    matched: int = 0
    caught_ids: set[str] = field(default_factory=set)
    judged: dict[str, int] = field(default_factory=dict)
    judged_firing: dict[str, int] = field(default_factory=dict)
    unmatched_hits: list[tuple[str, list[int]]] = field(default_factory=list)
    unlocated_uids: list[str] = field(default_factory=list)


def lint_key(lint: str, shape: str | None) -> str:
    """The scoring key: the lint, or `stage6_render_warnings:<shape>`."""
    return f"{lint}:{shape}" if lint == STAGE6_LINT else lint


def as_idx_set(value: object) -> frozenset[int]:
    """A label's element_idx_start (number, list or null) as truncated ints."""
    values = value if isinstance(value, list) else [] if value is None else [value]
    try:
        return frozenset(int(float(v)) for v in values)
    except (TypeError, ValueError) as e:
        raise InputError(f"element_idx_start {value!r} is not numeric") from e


def finding_idxs(finding: dict) -> frozenset[int]:
    """Entry indices named at the start of a finding's message or evidence."""
    texts = [str(finding.get("message", ""))] + [str(e) for e in finding.get("evidence") or []]
    found = (_IDX_PREFIX_RE.match(t.strip()) for t in texts)
    return frozenset(int(float(m.group(1))) for m in found if m)


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise InputError(f"cannot read {path}: {e}") from e


def load_hits(doctor_json: Path) -> tuple[list[Hit], set[str]]:
    """Every scored finding of a doctor_gate.py output, and the uids it doctored."""
    reports = _read_json(doctor_json)
    if not isinstance(reports, dict):
        raise InputError(f"{doctor_json}: not a doctor_gate.py findings map")
    if reports.get(FAILED_KEY):
        raise InputError(f"{doctor_json}: doctor_gate.py recorded failures: "
                         f"{sorted(reports[FAILED_KEY])}")
    uids = {uid for uid in reports if uid != FAILED_KEY}
    try:
        hits = [_hit(uid, finding) for uid in sorted(uids)
                for finding in reports[uid].get("findings", [])
                if finding.get("status", STATUS_RAN) == STATUS_RAN]
    except (AttributeError, KeyError, TypeError) as e:
        raise InputError(f"{doctor_json}: malformed doctor report ({type(e).__name__}: {e})") from e
    return hits, uids


def _hit(uid: str, finding: dict) -> Hit:
    shape = stage6_shape(finding.get("message", "")) if finding["lint"] == STAGE6_LINT else None
    return Hit(uid, lint_key(finding["lint"], shape), finding.get("severity", ""),
               finding_idxs(finding))


def _label_findings(uid: str, label: dict) -> list[Verified]:
    batch = label.get("batch") or ""
    return [Verified(uid, f["id"], batch, f["severity"],
                     f.get("class_ref") or NO_CLASS, f.get("batch_class") or NO_CLASS,
                     f.get("stage") or NO_CLASS, as_idx_set(f["element_idx_start"]),
                     (f.get("doctor_caught") or {}).get("verdict"))
            for f in label["findings"]]


def _label_reviews(uid: str, label: dict) -> list[Review]:
    reviews = []
    for r in label.get("doctor_review") or []:
        if r["verdict"] not in VERDICTS:
            raise InputError(f"{uid}: doctor_review verdict {r['verdict']!r} not in {VERDICTS}")
        reviews.append(Review(uid, lint_key(r["lint"], r.get("shape")), r["verdict"]))
    return reviews


def load_labels(labels_dir: Path) -> Labels:
    """Every `<uid>.json` label in a directory; a malformed one is an InputError."""
    labels = Labels()
    paths = sorted(labels_dir.glob("*.json")) if labels_dir.is_dir() else []
    if not paths:
        raise InputError(f"{labels_dir}: no <uid>.json label files")
    for path in paths:
        label = _read_json(path)
        uid = label.get("uid") if isinstance(label, dict) else None
        if uid != path.stem:
            raise InputError(f"{path}: uid {uid!r} does not match the file name")
        try:
            labels.verified += _label_findings(uid, label)
            labels.reviews += _label_reviews(uid, label)
        except (KeyError, TypeError) as e:
            raise InputError(f"{path}: malformed label ({type(e).__name__}: {e})") from e
        labels.uids.add(uid)
    return labels


def load_artifacts(doctor_json: Path) -> dict[str, RunArtifacts]:
    """Each doctored run's stage 3a / 3b artifact paths; a run the doctor
    recorded none for is absent."""
    reports = _read_json(doctor_json)
    if not isinstance(reports, dict):
        raise InputError(f"{doctor_json}: not a doctor_gate.py findings map")
    found = {}
    for uid, report in reports.items():
        paths = report.get(ARTIFACTS_KEY) if isinstance(report, dict) and uid != FAILED_KEY else None
        if isinstance(paths, dict):
            found[uid] = RunArtifacts(paths.get(STAGE_3A_ARTIFACT), paths.get(STAGE_3B_ARTIFACT))
    return found


def _replay_pins(mappings: list[dict], entries: list[dict]) -> list[dict]:
    """The entries after this checkout's header and non-seat pins, in the stored
    order. Each hierarchy group is pinned as one classification call, as stage 3b
    groups them."""
    mappings = deepcopy(mappings)
    _normalize_taxonomy_mappings(mappings)
    index = build_mapping_index(mappings)
    groups: dict[tuple, list[int]] = {}
    for position, entry in enumerate(entries):
        groups.setdefault(tuple(entry.get("hierarchy") or ()), []).append(position)
    pinned = list(entries)
    for hierarchy, positions in groups.items():
        context = get_taxonomy_context(list(hierarchy), index)
        group, _ = apply_header_pin(deepcopy([entries[p] for p in positions]), context, batch_size=None)
        for position, entry in zip(positions, group, strict=True):
            pinned[position] = entry
    return apply_non_seat_pins(pinned)[0]


def _replay_duplicates(entries: list[dict]) -> list[dict]:
    """The entries as this checkout's duplicate marking leaves them, the stored marks removed first."""
    unmarked = [{k: v for k, v in deepcopy(entry).items() if k not in DUPLICATE_MARK_KEYS}
                for entry in entries]
    return detect_duplicates(unmarked)[0]


def replay_stage3b(stage_3a: dict, stage_3b: dict) -> dict[int, Replayed]:
    """This checkout's stage-3b post-processing over one stored run, by entry index.

    Pure: takes the two loaded artifacts, changes neither. The key is an
    entry's element_idx_start, truncated as `as_idx_set` does; where several
    entries share one (stage-2 sub-entries), the first that is source_fixed
    stands for the index, else the first. An entry with no index is left out:
    no finding can name it.
    """
    entries = stage_3b["entries"]
    pinned, marked = _replay_pins(stage_3a["mappings"], entries), _replay_duplicates(entries)
    by_idx: dict[int, Replayed] = {}
    for stored, recoded, remarked in zip(entries, pinned, marked, strict=True):
        replayed = Replayed(stored.get("taxonomy_code"), recoded.get("taxonomy_code"),
                            bool(stored.get("is_duplicate")), bool(remarked.get("is_duplicate")))
        for idx in as_idx_set(stored.get("element_idx_start")):
            if idx not in by_idx or (replayed.source_fixed and not by_idx[idx].source_fixed):
                by_idx[idx] = replayed
    return by_idx


@contextlib.contextmanager
def _stage3b_warnings_off() -> Iterator[None]:
    """The replayed stage-3b code logs heading titles; this report is ids, codes and counts."""
    log = logging.getLogger(STAGE3B_LOGGER)
    level = log.level
    log.setLevel(logging.ERROR)
    try:
        yield
    finally:
        log.setLevel(level)


def replay_run(artifacts: RunArtifacts | None) -> dict[int, Replayed]:
    """Load one run's stored stage 3a / 3b and replay them; InputError, naming
    why, when the run cannot be replayed."""
    if artifacts is None or not (artifacts.stage_3a and artifacts.stage_3b):
        raise InputError("the doctor report names no stage_3a / stage_3b artifact")
    stage_3a, stage_3b = _read_json(Path(artifacts.stage_3a)), _read_json(Path(artifacts.stage_3b))
    try:
        with _stage3b_warnings_off():
            return replay_stage3b(stage_3a, stage_3b)
    except (AttributeError, KeyError, OverflowError, TypeError) as e:
        raise InputError(f"malformed stage 3a / 3b artifact ({type(e).__name__}: {e})") from e


def _score_hit(row: LintRow, hit: Hit, by_uid: dict[str, list[Verified]]) -> None:
    row.hits += 1
    if hit.severity in LOUD_SEVERITIES:
        row.loud += 1
    if not hit.idxs:
        row.unlocated_uids.append(hit.uid)
        return
    row.located += 1
    caught = [v.id for v in by_uid.get(hit.uid, []) if v.idxs & hit.idxs]
    if caught:
        row.matched += 1
        row.caught_ids.update(caught)
    else:
        row.unmatched_hits.append((hit.uid, sorted(hit.idxs)))


def score_lints(hits: list[Hit], labels: Labels) -> dict[str, LintRow]:
    """Per lint key: hits, located, matched, caught and the verdict tallies."""
    by_uid: dict[str, list[Verified]] = {}
    for v in labels.verified:
        by_uid.setdefault(v.uid, []).append(v)
    rows: dict[str, LintRow] = {}
    for hit in hits:
        _score_hit(rows.setdefault(hit.key, LintRow(hit.key)), hit, by_uid)
    firing = {(h.uid, h.key) for h in hits}
    for review in labels.reviews:
        row = rows.setdefault(review.key, LintRow(review.key))
        row.judged[review.verdict] = row.judged.get(review.verdict, 0) + 1
        if (review.uid, review.key) in firing:
            row.judged_firing[review.verdict] = row.judged_firing.get(review.verdict, 0) + 1
    return dict(sorted(rows.items()))


def _tally(findings: list[Verified], caught: set[str], attr: str) -> dict[str, dict[str, int]]:
    totals: Counter[str] = Counter(getattr(v, attr) for v in findings)
    hit: Counter[str] = Counter(getattr(v, attr) for v in findings if v.id in caught)
    return {k: {"findings": n, "caught": hit[k]} for k, n in totals.most_common()}


def _caught_ids(rows: dict[str, LintRow]) -> set[str]:
    return {cid for row in rows.values() for cid in row.caught_ids}


def score_recall(rows: dict[str, LintRow], labels: Labels) -> dict:
    """Verified findings matched by at least one hit, by five groupings, and
    the autopsy's own credit (doctor_caught) for comparison."""
    indexed = [v for v in labels.verified if v.idxs]
    caught = _caught_ids(rows)
    credited = [v for v in labels.verified if v.autopsy_credit is not None]
    return {
        "overall": {"findings": len(indexed), "caught": sum(v.id in caught for v in indexed)},
        "no_idx": len(labels.verified) - len(indexed),
        "autopsy_credit": {"judged": len(credited),
                           **Counter(v.autopsy_credit for v in credited)},
        "by_severity": _tally(indexed, caught, "severity"),
        "by_batch": _tally(indexed, caught, "batch"),
        **{f"by_{group}": _tally(indexed, caught, group) for group in CLASS_GROUPS},
    }


def _replay_uncaught(uncaught: list[Verified],
                     artifacts: dict[str, RunArtifacts]) -> tuple[list[Verified], dict[str, str]]:
    """(the uncaught findings that are source_fixed, why each unreplayable run was not replayed)."""
    by_uid: dict[str, list[Verified]] = {}
    for v in uncaught:
        by_uid.setdefault(v.uid, []).append(v)
    fixed, not_replayed = [], {}
    for uid, findings in sorted(by_uid.items()):
        try:
            replayed = replay_run(artifacts.get(uid))
        except InputError as e:
            not_replayed[uid] = str(e)  # counted and reported; its findings stay unjudged
            continue
        fixed += [v for v in findings
                  if any(i in replayed and replayed[i].source_fixed for i in v.idxs)]
    return fixed, not_replayed


def score_source_fixed(rows: dict[str, LintRow], labels: Labels,
                       artifacts: dict[str, RunArtifacts]) -> dict:
    """Of the indexed findings no hit matched, those this checkout's stage-3b
    replay treats differently, overall and by the finding's stage."""
    caught = _caught_ids(rows)
    uncaught = [v for v in labels.verified if v.idxs and v.id not in caught]
    fixed, not_replayed = _replay_uncaught(uncaught, artifacts)
    stages, fixed_stages = Counter(v.stage for v in uncaught), Counter(v.stage for v in fixed)
    lost_stages = Counter(stage for _, stage in {(v.uid, v.stage) for v in uncaught if v.uid in not_replayed})
    return {
        "uncaught": len(uncaught), "source_fixed": len(fixed),
        "runs_not_replayed": len(not_replayed),
        "uncaught_not_replayed": sum(v.uid in not_replayed for v in uncaught),
        "not_replayed": not_replayed,
        "by_stage": {stage: {"uncaught": n, "source_fixed": fixed_stages[stage],
                             "runs_not_replayed": lost_stages[stage]}
                     for stage, n in stages.most_common()},
        "source_fixed_ids": sorted(v.id for v in fixed),
    }


def build_report(doctor_json: Path, labels_dir: Path, source_fixed: bool = False) -> dict:
    """Load both inputs and score them; InputError when there is nothing to score.
    `source_fixed` adds recall's source_fixed block (--source-fixed)."""
    hits, doctored = load_hits(doctor_json)
    labels = load_labels(labels_dir)
    scored = doctored & labels.uids
    if not scored:
        raise InputError("no run is both doctored and labelled: nothing to score")
    labels.verified = [v for v in labels.verified if v.uid in scored]
    labels.reviews = [r for r in labels.reviews if r.uid in scored]
    rows = score_lints([h for h in hits if h.uid in scored], labels)
    unscored = [h for h in hits if h.uid not in scored]
    recall = score_recall(rows, labels)
    if source_fixed:
        recall["source_fixed"] = score_source_fixed(rows, labels, load_artifacts(doctor_json))
    return {
        "doctor_json": str(doctor_json), "labels_dir": str(labels_dir),
        "runs": {"doctored": len(doctored), "labelled": len(labels.uids), "scored": len(scored),
                 "labelled_not_doctored": sorted(labels.uids - doctored)},
        "unscored_hits": {"hits": len(unscored), "uids": sorted({h.uid for h in unscored})},
        "lints": [{**asdict(row), "caught_ids": sorted(row.caught_ids)} for row in rows.values()],
        "recall": recall,
    }


def _pct(part: int, whole: int) -> str:
    return f"{part}/{whole} ({100 * part / whole:.0f}%)" if whole else f"{part}/0"


def _verdicts(counts: dict[str, int]) -> str:
    return "/".join(str(counts.get(v, 0)) for v in VERDICTS)


def _lint_lines(lints: list[dict]) -> list[str]:
    head = (f"{'lint':<52} {'hits':>5} {'warn+':>5} {'loc':>5} {'match':>5} {'unmat':>5} "
            f"{'unloc':>5} {'caught':>6}  {'judged TP/pa/FP':>15}  {'firing TP/pa/FP':>15}")
    lines = [head, "-" * len(head)]
    for r in lints:
        lines.append(
            f"{r['key']:<52} {r['hits']:>5} {r['loud']:>5} {r['located']:>5} {r['matched']:>5} "
            f"{r['located'] - r['matched']:>5} {r['hits'] - r['located']:>5} "
            f"{len(r['caught_ids']):>6}  {_verdicts(r['judged']):>15}  {_verdicts(r['judged_firing']):>15}")
    return lines


def _group_line(name: str, groups: dict[str, dict[str, int]]) -> str:
    shown = {k: v for k, v in groups.items() if v["findings"] >= CLASS_TEXT_MIN}
    rest = [v for k, v in groups.items() if k not in shown]
    parts = [", ".join(f"{k} {_pct(v['caught'], v['findings'])}" for k, v in shown.items())]
    if rest:
        parts.append(f"{len(rest)} smaller groups {sum(v['caught'] for v in rest)}/"
                     f"{sum(v['findings'] for v in rest)} (each in --json)")
    return f"  {name}: " + "; ".join(p for p in parts if p)


def _recall_lines(recall: dict) -> list[str]:
    overall, credit = recall["overall"], recall["autopsy_credit"]
    lines = [f"recall: {_pct(overall['caught'], overall['findings'])} verified findings matched "
             f"by >=1 hit; {recall['no_idx']} more carry no element_idx_start and cannot match",
             f"  autopsy's own credit (doctor_caught, the doctor it reviewed): yes {credit.get('yes', 0)}, "
             f"partial {credit.get('partial', 0)} of {credit['judged']} judged"]
    for group in ("by_severity", "by_batch", *(f"by_{g}" for g in CLASS_GROUPS)):
        lines.append(_group_line(group, recall[group]))
    if "source_fixed" in recall:
        lines.append(_source_fixed_line(recall["source_fixed"]))
    return lines


def _source_fixed_line(split: dict) -> str:
    return (f"  of {split['uncaught']} uncaught, {split['source_fixed']} no longer reproduce at source "
            f"on this checkout (stage-3b pins / duplicate marking); {split['runs_not_replayed']} runs "
            f"not replayed ({split['uncaught_not_replayed']} uncaught findings unjudged)")


def render_text(report: dict) -> str:
    """The human table; --json carries the same numbers plus the hit lists."""
    runs = report["runs"]
    lines = [f"scored {runs['scored']} runs (doctored {runs['doctored']}, labelled {runs['labelled']}); "
             f"{report['unscored_hits']['hits']} hits on unlabelled runs not scored "
             f"{report['unscored_hits']['uids']}",
             "loc = hits naming an entry index; match = loc sharing one with a verified finding "
             "(match/loc is a lower bound on precision); judged = verifier verdicts",
             ""]
    lines += _lint_lines(report["lints"]) + [""] + _recall_lines(report["recall"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("doctor_json", type=Path, help="doctor_gate.py findings JSON")
    parser.add_argument("labels_dir", type=Path, help="directory of <uid>.json labels")
    parser.add_argument("--json", type=Path, default=None, help="also write the report here")
    parser.add_argument("--source-fixed", action="store_true",
                        help="split the uncaught findings by whether this checkout's stage-3b "
                             "replay of the stored run still reproduces them")
    args = parser.parse_args(argv)
    try:
        report = build_report(args.doctor_json, args.labels_dir, source_fixed=args.source_fixed)
    except InputError as e:
        print(f"doctor_vs_autopsy: {e}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    print(render_text(report))
    if args.json:
        args.json.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
