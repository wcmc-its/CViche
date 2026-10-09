#!/usr/bin/env python3
"""Offline LLM audit of research-summary sentences against the source CV (#1592).

    audit <runs_dir> --out DIR [--uids-from LABELS_DIR] [--uid UID ...] --dry-run
    audit <runs_dir> --out DIR [--uids-from LABELS_DIR] [--uid UID ...]     PAID
    score <audit_dir> <labels_dir> [--json OUT]

Not part of the pipeline and not run per production run: an operator tool for
measuring a summary-claim checker before anyone trusts one. The doctor's
deterministic `summary_unsupported_claim` lint fired once in 37 YUYVIG runs, on
a false positive, and missed all 19 confirmed #1554 instances.

`runs_dir` holds one directory per run in the S3 run-artifact layout:
`<uid>/outputs/<uid>_research_summary.json` (stage 4.5) and
`<uid>/outputs/<uid>_entries.json` (stage 2). Each run whose summary the model
generated gets ONE call: the stage-2 text of the whole CV, which covers every
element the reader produced, and the summary split into sentences the way the
doctor lint splits them. The model returns a verdict per sentence.

`audit` writes, per run:
- `<out>/labels/<uid>.json`: the findings in the label schema
  scripts/doctor_vs_autopsy.py reads (ids, codes, counts; no CV text), plus an
  `audit` block with the model, tokens, cost and prompt template hash.
- `<out>/detail/<uid>.json`: each sentence, its verdict and the model's reason.
  This is CV text: PII, so `--out` is refused inside this repository. call_llm
  logs every prompt verbatim to PROMPT_LOG_DIR, which is read when the pipeline
  is imported; a paid run is refused unless that is set outside the repository
  too (`PROMPT_LOG_DIR=<out>/prompt_logs`).
A run whose label already records status "ok" or "not_generated" is skipped, so
re-running after a failure pays only for the runs that failed.

`--dry-run` makes no call. It prints each prompt exactly as it would be sent,
then a per-run table of estimated tokens and cost. Input tokens are the prompt's
characters over a characters-per-token ratio measured from the runs' own logged
stage-4.5 calls on the same model (`measure_chars_per_token`); the ratio is
printed with the call count it came from. Output tokens are a stated allowance
per sentence (`OUTPUT_TOKENS_PER_SENTENCE`), the one assumed number.

`score` compares the audit's labels with verified autopsy labels, per run, on
the runs both cover. A labelled run is positive when one of its findings lists
"#1554" in `issues`; an audited run is positive when the audit flagged a
sentence. The autopsy labels name no sentence, so a true-positive run is not
proof the flagged sentence is the confirmed one: the hand-check lists say
which runs and sentences to read against the verified report.

Fails closed (CODING_STANDARDS 5.5): a missing artifact, a run whose call or
reply failed, or nothing to score exits non-zero.
"""
import argparse
import hashlib
import json
import logging
import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.config import calculate_cost, get_stage_config  # noqa: E402
from unified_pipeline.core import prompt_logger  # noqa: E402
from unified_pipeline.doctor.lints.summary import (  # noqa: E402
    generated_summary,
    summary_sentences,
)
from unified_pipeline.llm_client import call_llm  # noqa: E402
from unified_pipeline.stage_4_5_research_summary import (
    GENERATION_METHOD_LLM,  # noqa: E402
)

logger = logging.getLogger("summary_claim_audit")

#: The call_llm stage name. It has no llm_config.yaml block, so it takes the
#: default model (Sonnet 5, docs/adr/0001-sonnet-5-default-model.md).
STAGE = "summary_claim_audit"
#: The logged pipeline calls the dry run calibrates characters-per-token on:
#: stage 4.5's prompts are, like this one, CV-derived prose sent as one user
#: message with no system prompt or tool schema.
CALIBRATION_STAGE = "stage_4_5"
MAX_OUTPUT_TOKENS = 4000
# ponytail: a guessed allowance -- one JSON object with a <=25-word reason is
# ~70 Sonnet-5 tokens at the 2.2 chars/token measured on stage 4.5. The paid
# run records the real completion tokens per run; replace this with that.
OUTPUT_TOKENS_PER_SENTENCE = 100

CLASS = "summary_unsupported_claim"
CLASS_REF = "#1554"
ISSUE = "#1554"
LABEL_STAGE = "4.5"
DEFAULT_BATCH = "summary_claim_audit"

VERDICT_SUPPORTED = "supported"
VERDICTS = (VERDICT_SUPPORTED, "unsupported", "partly")
KINDS = ("none", "grant_status", "funding", "funder", "role", "count", "award",
         "mentoring", "outcome", "other")
SEVERITY_NONE = "none"
SEVERITIES = ("high", "medium", "low")
#: The #1554 acceptance's target set: the HIGH and MED instances.
TARGET_SEVERITIES = frozenset({"high", "medium"})

STATUS_OK = "ok"
STATUS_NOT_GENERATED = "not_generated"
STATUS_CALL_FAILED = "call_failed"
STATUS_REPLY_UNPARSED = "reply_unparsed"
#: A label in either of these needs no new call.
DONE_STATUSES = frozenset({STATUS_OK, STATUS_NOT_GENERATED})

EXIT_INPUT_ERROR = 2
EXIT_RUN_FAILED = 1

PROMPT_TEMPLATE = """You are auditing a research summary paragraph that was generated from a faculty member's CV. For each numbered sentence, decide whether the CV TEXT below supports every factual claim in it.

Rules:
- Judge only against the CV TEXT. Use no outside knowledge.
- A sentence is "unsupported" when it states any fact the CV TEXT does not contain or that it contradicts, such as:
  - a grant, application or funding status ("under review", "pending", "current", "funded") the CV does not show for that work;
  - a funder, sponsor or agency the CV does not name for that work;
  - a role (principal investigator, co-investigator, director, lead) the CV does not give;
  - a count, duration, "multiple", "consecutive" or "numerous" the CV does not bear out, including an award the CV marks declined or withdrawn;
  - mentoring, teaching or training of others the CV does not list;
  - an outcome or impact (clinical translation, guidelines, policy, commercial use) the CV does not state.
- Describing research areas in general terms that the CV's publications, grants or positions cover is supported. Paraphrase and summary are fine.
- "partly": the sentence mixes supported and unsupported claims.
- The summary was written on {summary_date}. A grant or position is current only if the CV shows it ongoing on that date.

Severity of an unsupported or partly supported sentence:
- high: an invented or wrong grant, funding status, role, award or position -- a fact a reader would rely on;
- medium: another specific claim the CV lacks (a funder's name, mentoring, a count, a named outcome);
- low: vague embellishment or impact language.

Reply with JSON only, one object per sentence, in order:
{{"sentences": [{{"i": 1, "verdict": "supported" | "unsupported" | "partly", "kind": "{kinds}", "severity": "none" | "high" | "medium" | "low", "reason": "at most 25 words"}}]}}
Use kind "none" and severity "none" for a supported sentence.

CV TEXT:
<<<
{source}
>>>

SUMMARY SENTENCES:
{sentences}"""


class InputError(Exception):
    """An input this tool cannot audit or score; main() exits EXIT_INPUT_ERROR."""


class ReplyError(Exception):
    """A model reply that is not one valid verdict per sentence."""


@dataclass(frozen=True)
class RunInput:
    """What one run's audit call is built from."""
    uid: str
    generation_method: str
    summary_date: str
    sentences: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class Verdict:
    """The model's verdict on one summary sentence (1-based `index`)."""
    index: int
    verdict: str
    kind: str
    severity: str
    reason: str


@dataclass(frozen=True)
class Usage:
    """What one call was billed, as call_llm reports it."""
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost: float


def prompt_template_hash() -> str:
    """The template's fingerprint, before any CV text is put in (CODING_STANDARDS 5.12)."""
    return hashlib.sha256(PROMPT_TEMPLATE.encode("utf-8")).hexdigest()[:16]


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise InputError(f"cannot read {path}: {e}") from e


def source_text(stage_2: dict) -> str:
    """The CV as the reader produced it: every stage-2 entry's text, in the order
    stage 2 wrote them, which is document order. Not re-sorted: an index can be
    a sub-entry string ("21.1"), and stage 2 already orders by it."""
    entries = stage_2.get("entries")
    if not isinstance(entries, list):
        raise InputError("stage-2 artifact has no entries list")
    return "\n".join(text for e in entries if (text := str(e.get("text") or "").strip()))


def load_run(run_dir: Path, uid: str) -> RunInput:
    """One run's summary sentences and source text, from its stored artifacts."""
    outputs = run_dir / "outputs"
    stage_4_5 = _read_json(outputs / f"{uid}_research_summary.json")
    if not isinstance(stage_4_5, dict) or not isinstance(stage_4_5.get("research_summary"), dict):
        raise InputError(f"{uid}: stage-4.5 artifact has no research_summary object")
    summary = stage_4_5["research_summary"]
    stage_2 = _read_json(outputs / f"{uid}_entries.json")
    if not isinstance(stage_2, dict):
        raise InputError(f"{uid}: stage-2 artifact is not an object")
    return RunInput(
        uid=uid,
        generation_method=str(summary.get("generation_method") or ""),
        summary_date=str(summary.get("generation_timestamp") or "")[:10],
        sentences=tuple(summary_sentences(generated_summary(stage_4_5))),
        source=source_text(stage_2))


def build_prompt(run: RunInput) -> str:
    """The exact prompt text of one run's call."""
    numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(run.sentences, start=1))
    return PROMPT_TEMPLATE.format(summary_date=run.summary_date or "an unknown date",
                                  kinds='" | "'.join(KINDS), source=run.source,
                                  sentences=numbered)


def _verdict(item: object, expected_index: int) -> Verdict:
    if not isinstance(item, dict):
        raise ReplyError(f"sentence {expected_index}: not an object")
    verdict = Verdict(index=item.get("i"), verdict=item.get("verdict"), kind=item.get("kind"),
                      severity=item.get("severity"), reason=str(item.get("reason") or ""))
    if verdict.index != expected_index:
        raise ReplyError(f"expected sentence {expected_index}, got {verdict.index!r}")
    if verdict.verdict not in VERDICTS or verdict.kind not in KINDS:
        raise ReplyError(f"sentence {expected_index}: verdict {verdict.verdict!r} / kind {verdict.kind!r}")
    allowed = (SEVERITY_NONE,) if verdict.verdict == VERDICT_SUPPORTED else SEVERITIES
    if verdict.severity not in allowed:
        raise ReplyError(f"sentence {expected_index}: severity {verdict.severity!r} for {verdict.verdict!r}")
    return verdict


#: The model's reasoning, when it writes some before its answer.
_LEADING_THINK_RE = re.compile(r"\A\s*<think>.*?</think>", re.DOTALL)
_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def parse_reply(reply: str, sentence_count: int) -> list[Verdict]:
    """One Verdict per sentence from the model's JSON reply; ReplyError otherwise.
    A leading <think> block is dropped first: 2 of YUYVIG's 37 replies (OKRTPJ,
    QQGKXR) opened with one before a valid fenced JSON block (#1592)."""
    text = _LEADING_THINK_RE.sub("", reply, count=1).strip()
    fenced = _FENCED_JSON_RE.fullmatch(text)
    try:
        data = json.loads(fenced.group(1) if fenced else text)
    except ValueError as e:
        raise ReplyError(f"not JSON: {e}") from e
    items = data.get("sentences") if isinstance(data, dict) else None
    if not isinstance(items, list) or len(items) != sentence_count:
        raise ReplyError(f"expected {sentence_count} sentence verdicts, got "
                         f"{len(items) if isinstance(items, list) else type(items).__name__}")
    return [_verdict(item, i) for i, item in enumerate(items, start=1)]


def _audit_block(run: RunInput, status: str, usage: Usage | None, error: str = "") -> dict:
    return {"status": status, "error": error, "stage": STAGE,
            "model": usage.model if usage else get_stage_config(STAGE)["model"],
            "prompt_template_hash": prompt_template_hash(),
            "generation_method": run.generation_method, "sentences": len(run.sentences),
            "prompt_tokens": usage.prompt_tokens if usage else 0,
            "completion_tokens": usage.completion_tokens if usage else 0,
            "cost": usage.cost if usage else 0.0}


def label_record(run: RunInput, batch: str, verdicts: list[Verdict], audit: dict) -> dict:
    """The run's findings in the autopsy label schema: one per flagged sentence, no CV text."""
    flagged = [v for v in verdicts if v.verdict != VERDICT_SUPPORTED]
    findings = [{"id": f"{run.uid}-A{n:02d}", "class": CLASS, "class_ref": CLASS_REF,
                 "batch_class": None, "stage": LABEL_STAGE, "stages": [LABEL_STAGE],
                 "issues": [ISSUE], "severity": v.severity, "element_idx_start": None,
                 "records": None, "doctor_caught": None,
                 "sentence_index": v.index, "claim_kind": v.kind, "verdict": v.verdict}
                for n, v in enumerate(flagged, start=1)]
    return {"uid": run.uid, "batch": batch, "findings": findings, "doctor_review": [],
            "audit": audit}


def detail_record(run: RunInput, verdicts: list[Verdict], reply: str = "") -> dict:
    """Each sentence with its verdict and reason -- CV text, for the hand-check only."""
    by_index = {v.index: v for v in verdicts}
    return {"uid": run.uid, "pii": "CV-derived text; never commit or quote",
            "sentences": [{"i": i, "text": s,
                           **({"verdict": by_index[i].verdict, "kind": by_index[i].kind,
                               "severity": by_index[i].severity, "reason": by_index[i].reason}
                              if i in by_index else {})}
                          for i, s in enumerate(run.sentences, start=1)],
            "raw_reply": reply if not verdicts else ""}


#: call(prompt) -> call_llm's result dict (content, prompt_tokens, completion_tokens, cost, model).
LlmCall = Callable[[str], dict]


def audit_run(run: RunInput, batch: str, call: LlmCall) -> tuple[dict, dict]:
    """(label, detail) for one run: one call when the model wrote its summary, none otherwise."""
    if run.generation_method != GENERATION_METHOD_LLM or not run.sentences:
        return (label_record(run, batch, [], _audit_block(run, STATUS_NOT_GENERATED, None)),
                detail_record(run, []))
    try:
        result = call(build_prompt(run))
    except Exception as e:  # any call failure: logged with its traceback, recorded in the label, and main exits 1
        logger.exception("%s: audit call failed", run.uid)
        error = f"{type(e).__name__}: {e}"
        return (label_record(run, batch, [], _audit_block(run, STATUS_CALL_FAILED, None, error)),
                detail_record(run, []))
    usage = Usage(model=str(result.get("model") or ""), prompt_tokens=int(result.get("prompt_tokens") or 0),
                  completion_tokens=int(result.get("completion_tokens") or 0),
                  cost=float(result.get("cost") or 0.0))
    reply = str(result.get("content") or "")
    try:
        verdicts = parse_reply(reply, len(run.sentences))
    except ReplyError as e:
        return (label_record(run, batch, [], _audit_block(run, STATUS_REPLY_UNPARSED, usage, str(e))),
                detail_record(run, [], reply))
    return (label_record(run, batch, verdicts, _audit_block(run, STATUS_OK, usage)),
            detail_record(run, verdicts))


def bedrock_call(prompt: str) -> dict:
    """PAID: one call_llm call on the audit stage's configured model."""
    return call_llm(stage=STAGE, messages=[{"role": "user", "content": prompt}],
                    temperature=0, max_tokens=MAX_OUTPUT_TOKENS, enable_prompt_caching=False)


_LOG_ID_RE = re.compile(rf"_{CALIBRATION_STAGE}_([0-9a-f]{{12}})(_RESPONSE)?\.json$")


def _logged_calls(prompt_logs: Path) -> list[tuple[dict, dict]]:
    """(request, response) pairs of one run's logged calibration-stage calls."""
    requests, responses = {}, {}
    for path in prompt_logs.glob(f"*_{CALIBRATION_STAGE}_*.json"):
        match = _LOG_ID_RE.search(path.name)
        if not match:
            continue  # a longer purpose that starts the same, as stage_3b_block_coherence does stage_3b
        record = _read_json(path)
        target = responses if match.group(2) else requests
        target[match.group(1)] = record
    return [(requests[k], responses[k]) for k in requests if k in responses]


def measure_chars_per_token(run_dirs: Iterable[Path], model: str) -> tuple[float, int]:
    """(characters per input token, calls measured) over the runs' logged stage-4.5
    calls served by `model`; InputError when there are none to measure."""
    chars = tokens = calls = 0
    for run_dir in run_dirs:
        for request, response in _logged_calls(run_dir / "prompt_logs"):
            billed = response.get("response") or {}
            prompt_tokens = (billed.get("usage") or {}).get("prompt_tokens")
            if billed.get("model") != model or not prompt_tokens:
                continue
            # The content itself: the log's character_count is of the JSON-encoded
            # messages, escapes included, which is not what the estimate divides.
            chars += sum(len(str(m.get("content") or "")) for m in request.get("messages") or [])
            tokens += int(prompt_tokens)
            calls += 1
    if not calls:
        raise InputError(f"no logged {CALIBRATION_STAGE} call on {model} to measure "
                         "characters per token from")
    return chars / tokens, calls


def estimate(run: RunInput, chars_per_token: float, model: str) -> dict:
    """Estimated tokens and USD of one run's call; zero when no call is made."""
    if run.generation_method != GENERATION_METHOD_LLM or not run.sentences:
        return {"uid": run.uid, "sentences": 0, "prompt_chars": 0, "input_tokens": 0,
                "output_tokens": 0, "cost": 0.0}
    prompt_chars = len(build_prompt(run))
    input_tokens = round(prompt_chars / chars_per_token)
    output_tokens = OUTPUT_TOKENS_PER_SENTENCE * len(run.sentences)
    return {"uid": run.uid, "sentences": len(run.sentences), "prompt_chars": prompt_chars,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "cost": calculate_cost(input_tokens, output_tokens, model=model)}


def _run_uids(runs_dir: Path, uids: list[str], uids_from: Path | None) -> list[str]:
    if uids:
        return sorted(set(uids))
    if uids_from is not None:
        found = sorted(p.stem for p in uids_from.glob("*.json"))
    else:
        found = sorted(p.name for p in runs_dir.iterdir()
                       if (p / "outputs" / f"{p.name}_research_summary.json").is_file())
    if not found:
        raise InputError("no runs to audit")
    return found


def _check_out_dir(out: Path) -> None:
    """Refuse an output directory inside this repository: the detail files are CV text."""
    if out.resolve().is_relative_to(_REPO):
        raise InputError(f"--out {out} is inside the repository; detail files are PII")


def _check_prompt_log_dir() -> None:
    """Refuse a paid run whose verbatim prompt logs would land inside this repository."""
    if prompt_logger.PROMPT_LOG_DIR.resolve().is_relative_to(_REPO):
        raise InputError(f"PROMPT_LOG_DIR {prompt_logger.PROMPT_LOG_DIR} is inside the repository; "
                         "set it outside, e.g. PROMPT_LOG_DIR=<out>/prompt_logs")


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _already_done(out: Path, uid: str) -> bool:
    path = out / "labels" / f"{uid}.json"
    if not path.is_file():
        return False
    label = _read_json(path)
    return isinstance(label, dict) and (label.get("audit") or {}).get("status") in DONE_STATUSES


def print_dry_run(runs: list[RunInput], chars_per_token: float, calls: int, model: str) -> None:
    """Every prompt, then the per-run estimate table and its total."""
    for run in runs:
        if run.generation_method == GENERATION_METHOD_LLM and run.sentences:
            print(f"===== {run.uid} prompt =====\n{build_prompt(run)}\n")
    print(f"model {model}; {chars_per_token:.2f} chars/token measured over {calls} logged "
          f"{CALIBRATION_STAGE} calls; output allowance {OUTPUT_TOKENS_PER_SENTENCE} tokens/sentence")
    print(f"{'uid':<8} {'sent':>4} {'chars':>8} {'in_tok':>8} {'out_tok':>7} {'usd':>7}")
    rows = [estimate(run, chars_per_token, model) for run in runs]
    for r in rows:
        print(f"{r['uid']:<8} {r['sentences']:>4} {r['prompt_chars']:>8} {r['input_tokens']:>8} "
              f"{r['output_tokens']:>7} {r['cost']:>7.4f}")
    print(f"{'total':<8} {sum(r['sentences'] for r in rows):>4} {sum(r['prompt_chars'] for r in rows):>8} "
          f"{sum(r['input_tokens'] for r in rows):>8} {sum(r['output_tokens'] for r in rows):>7} "
          f"{sum(r['cost'] for r in rows):>7.4f}  ({sum(1 for r in rows if r['sentences'])} calls)")


def cmd_audit(args: argparse.Namespace, call: LlmCall | None = None) -> int:
    """`audit`: the dry-run table, or the paid calls and their label files."""
    _check_out_dir(args.out)
    uids = _run_uids(args.runs_dir, args.uid, args.uids_from)
    run_dirs = [args.runs_dir / uid for uid in uids]
    runs = [load_run(run_dir, uid) for run_dir, uid in zip(run_dirs, uids, strict=True)]
    model = get_stage_config(STAGE)["model"]
    if args.dry_run:
        chars_per_token, calls = measure_chars_per_token(run_dirs, model)
        print_dry_run(runs, chars_per_token, calls, model)
        return 0
    if call is None:
        _check_prompt_log_dir()
        call = bedrock_call
    failed, spent = [], 0.0
    for run in runs:
        if _already_done(args.out, run.uid):
            print(f"{run.uid}: already audited, skipped", file=sys.stderr)
            continue
        label, detail = audit_run(run, args.batch, call)
        _write_json(args.out / "labels" / f"{run.uid}.json", label)
        _write_json(args.out / "detail" / f"{run.uid}.json", detail)
        audit = label["audit"]
        spent += audit["cost"]
        if audit["status"] not in DONE_STATUSES:
            failed.append(run.uid)
        print(f"{run.uid}: {audit['status']} {len(label['findings'])} flagged of "
              f"{audit['sentences']} sentences, ${audit['cost']:.4f}")
    print(f"spent ${spent:.4f}; failed {failed}")
    return EXIT_RUN_FAILED if failed else 0


def _labels(directory: Path) -> dict[str, dict]:
    paths = sorted(directory.glob("*.json")) if directory.is_dir() else []
    if not paths:
        raise InputError(f"{directory}: no <uid>.json label files")
    labels = {}
    for path in paths:
        label = _read_json(path)
        if not isinstance(label, dict) or label.get("uid") != path.stem:
            raise InputError(f"{path}: uid does not match the file name")
        if not isinstance(label.get("findings"), list):
            raise InputError(f"{path}: no findings list")
        labels[path.stem] = label
    return labels


def _truth_instances(label: dict) -> list[dict]:
    return [f for f in label["findings"] if ISSUE in (f.get("issues") or [])]


def _ratio(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _confusion(flagged: dict[str, list[dict]], truth: dict[str, list[dict]],
               min_severities: frozenset[str] | None) -> dict:
    """Per-run confusion counts; `min_severities` keeps only audit findings of those severities."""
    def positive(uid: str) -> bool:
        return any(min_severities is None or f["severity"] in min_severities for f in flagged[uid])
    uids = sorted(truth)
    tp = [u for u in uids if positive(u) and truth[u]]
    fp = [u for u in uids if positive(u) and not truth[u]]
    fn = [u for u in uids if not positive(u) and truth[u]]
    target = [u for u in uids if any(f["severity"] in TARGET_SEVERITIES for f in truth[u])]
    return {"tp": tp, "fp": fp, "fn": fn, "tn": len(uids) - len(tp) - len(fp) - len(fn),
            "precision": _ratio(len(tp), len(tp) + len(fp)),
            "recall": _ratio(len(tp), len(tp) + len(fn)),
            "target_runs": target, "target_caught": [u for u in target if positive(u)]}


def score(audit_dir: Path, labels_dir: Path) -> dict:
    """The audit's per-run precision and recall on the #1554 instances of the labelled runs."""
    audited, labelled = _labels(audit_dir), _labels(labels_dir)
    unfinished = sorted(u for u, label in audited.items()
                        if (label.get("audit") or {}).get("status") not in DONE_STATUSES)
    if unfinished:
        raise InputError(f"audit has runs whose call or reply failed: {unfinished}")
    scored = sorted(set(audited) & set(labelled))
    if not scored:
        raise InputError("no run is both audited and labelled: nothing to score")
    truth = {u: _truth_instances(labelled[u]) for u in scored}
    flagged = {u: audited[u]["findings"] for u in scored}
    return {
        "runs": {"audited": len(audited), "labelled": len(labelled), "scored": len(scored),
                 "labelled_not_audited": sorted(set(labelled) - set(audited))},
        "instances": sum(len(v) for v in truth.values()),
        "instance_ids": sorted(f["id"] for v in truth.values() for f in v),
        "cost": sum((audited[u].get("audit") or {}).get("cost", 0.0) for u in scored),
        "any_severity": _confusion(flagged, truth, None),
        "high_or_medium": _confusion(flagged, truth, TARGET_SEVERITIES),
        "flagged_sentences": {u: [f["sentence_index"] for f in flagged[u]] for u in scored if flagged[u]},
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.0f}%"


def render_score(report: dict) -> str:
    """The human summary; --json carries the same numbers."""
    runs = report["runs"]
    lines = [f"scored {runs['scored']} runs (audited {runs['audited']}, labelled {runs['labelled']}); "
             f"{report['instances']} labelled {ISSUE} instances; audit cost ${report['cost']:.4f}"]
    for name in ("any_severity", "high_or_medium"):
        c = report[name]
        lines += [f"{name}: TP {len(c['tp'])} FP {len(c['fp'])} FN {len(c['fn'])} TN {c['tn']}; "
                  f"precision {_pct(c['precision'])} recall {_pct(c['recall'])}; "
                  f"HIGH/MED target runs caught {len(c['target_caught'])}/{len(c['target_runs'])}",
                  f"  hand-check FP {c['fp']}", f"  missed {c['fn']}"]
    lines.append("flagged sentence indices (read against verified/<UID>.md): "
                 + json.dumps(report["flagged_sentences"], sort_keys=True))
    return "\n".join(lines)


def cmd_score(args: argparse.Namespace) -> int:
    """`score`: print the report, and write it as JSON when asked."""
    report = score(args.audit_dir, args.labels_dir)
    print(render_score(report))
    if args.json:
        args.json.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit", help="audit stored runs (PAID unless --dry-run)")
    audit.add_argument("runs_dir", type=Path)
    audit.add_argument("--out", type=Path, required=True)
    audit.add_argument("--uid", action="append", default=[], help="audit only this run (repeatable)")
    audit.add_argument("--uids-from", type=Path, default=None,
                       help="audit the runs that have a <uid>.json label in this directory")
    audit.add_argument("--batch", default=DEFAULT_BATCH, help="the labels' batch field")
    audit.add_argument("--dry-run", action="store_true", help="print prompts and estimates; no call")
    scorer = sub.add_parser("score", help="score audit labels against autopsy labels")
    scorer.add_argument("audit_dir", type=Path, help="the audit's labels/ directory")
    scorer.add_argument("labels_dir", type=Path, help="verified autopsy labels")
    scorer.add_argument("--json", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None, call: LlmCall | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return cmd_audit(args, call) if args.command == "audit" else cmd_score(args)
    except InputError as e:
        print(f"summary_claim_audit: {e}", file=sys.stderr)
        return EXIT_INPUT_ERROR


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main())
