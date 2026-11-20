"""model_review: a model checks each source record against the output document.

The deterministic lints catch the defect classes they were written for, and a
verified autopsy keeps finding the long tail they miss: on two fresh batches
(KFUTTD, BRAQDE) the doctor caught 36% of the verified defects. This lint
sends the source CV, as stage 2 parsed it, one section-sized chunk at a time,
with the whole output document, and lists each record the model says is lost,
changed, misplaced, duplicated, run together, cut in two, or shows protected
personal data.

Measured before it shipped (PRECISION.md, MR-V2): on 16 labelled runs the
prompt had never seen, the doctor plus this lint caught 66% of the verified
defects against the doctor's 43%, and 81% of its findings were real (76%
exactly as worded). So it is INFO and review-copy only: each finding is a
comment on the output text it quotes, or a review note, and costs no score.

The model call is injected (`call`, `llm_client.call_llm`'s signature), so this
module does no I/O itself, and run_doctor passes one only when its caller
switched the review on: the offline sweeps never pay for it.
"""

import hashlib
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, NamedTuple

from unified_pipeline.core.batch_pool import map_in_order
from unified_pipeline.doctor.shared import STATUS_SKIPPED, _finding
from unified_pipeline.log_redaction import redacted_error_label

if TYPE_CHECKING:  # the model call and its usage come in from run_doctor, which imports the client
    from unified_pipeline.llm_client import LlmUsage

logger = logging.getLogger(__name__)

#: The lint key. `lint_model_review` writes it as a literal too: the registry
#: contract test reads each lint's key from its body (test_run_doctor_contract.py).
MODEL_REVIEW_LINT = "model_review"
#: The llm_config.yaml stage key that picks the model (Haiku 5.5).
MODEL_REVIEW_STAGE = "doctor_model_review"

# ponytail: chunk sizes and the call ceiling are the prototype's, measured on
# 32 runs (median 17 chunks, max 44). A CV past MAX_CHUNKS is checked only up
# to it, and says so; raise it if real CVs reach it.
#: Source characters in one chunk, and entries, whichever is reached first.
CHUNK_MAX_CHARS = 8000
CHUNK_MAX_ENTRIES = 45
#: Model calls one run may make.
MAX_CHUNKS = 80
#: Calls in flight after the first, which writes the prompt cache alone.
CALL_WORKERS = 4
#: Characters of the model's reason kept in a finding.
MAX_REASON_CHARS = 300

#: What the model may call a defect, and how a reviewer reads it.
KIND_LABELS = {
    "record_lost": "May be missing from the document",
    "field_lost": "A detail from your CV may be missing here",
    "wrong_value": "May not match your CV",
    "misplaced": "May be in the wrong section",
    "duplicated": "May appear twice",
    "merged": "May be two entries run together",
    "split": "May be one entry cut in two",
    "protected_data": "Personal data that may need removing",
}
#: The model's word for an output quote it could not find.
ABSENT = "ABSENT"
#: Opens the finding that says how much of the CV went unchecked: coverage,
#: not a defect, so the review copy leaves it out.
NOT_CHECKED_PREFIX = "model review: "
#: A source element tag, "[E12]" in the chunk, "E12" in a reply.
_TAG_RE = re.compile(r"^\s*E?(\d+(?:\.\d+)?)\s*$", re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

REVIEW_PROMPT = """\
You review one automated conversion of a faculty member's CV into the Weill Cornell Medicine (WCM) CV template. The OUTPUT DOCUMENT below is what the faculty member receives. Each user message gives you one chunk of the SOURCE CV as it was parsed: one line per source element, "[E<n>] text", where E<n> is the element's index tag (E followed by a number; numbers inside the text, such as a publication's list number, are not indices); "##" marks a heading; "(heading path: ...)" names the headings the following lines sit under; " ⏎ " joins lines of one element.

Your job: for EVERY record in the chunk, find where it landed in the output document and report each defect a reader of the output would be misled or shortchanged by. A record is one appointment, degree, training period, certification, licence, honor, membership, committee role, grant, mentee, course, talk, publication, abstract, patent, or clinical or service activity. Work record by record and do not sample. Before reporting wrong_value, misplaced or record_lost, make sure the output entry you compared was made from THIS source record: the CV may hold a similar record elsewhere (another section, another term or year) that explains what you see. Table rows print their cells in column order, empty cells included, so read a value under its column header. Headings are not records, but a heading's text can carry facts (an employer, a date range) that apply to the lines under it.

Kinds of defect:
- record_lost: the record appears nowhere in the output. Search the WHOLE document, including any Appendix, before saying so. If its only trace is struck-through text, it is lost.
- field_lost: the record is present but a fact the source states for it is missing from its output entry: a date or year, a role or title, an institution or organization, a location, an amount, a funder, a grant number, a co-investigator, mentor or mentee name, a status (pending, funded, completed), a course or talk title.
- wrong_value: the output states a fact about the record that differs from the source: different years or dates, an open range ("2018-", "-present") closed or a closed one made open, a different role, rank or title, a wrong institution, amount or funding status, or a citation that now names a different paper, year, volume or pages.
- misplaced: the record sits under an output section of the wrong kind for what it is (a grant under honors, an invited talk as a journal article, a committee role as an employment position, a hospital appointment as an academic appointment).
- duplicated: the same record appears as two separate entries in the output.
- merged: two separate source records are fused into one output entry, or text of one record is glued onto another.
- split: one source record is cut into two or more output entries.
- protected_data: the output shows personal data the pipeline must withhold: date or place of birth, age, gender, ethnicity, religion, marital status, spouse, children or family, an emergency contact, home address or home phone, social security, passport, driver's licence, institutional or tax ID number, visa or immigration status, veteran or disability status, health information, blood type, salary or honorarium, or a DEA number. The template's own office phone, cell phone and work email rows, and a licence number in the Licensure table, are expected.

By design, NOT defects (never report these):
- Citations are reformatted and enriched from PubMed: author lists rewritten, "et al." added or removed, the owner's name marked, journal names abbreviated or expanded, PMID or DOI added. Report a citation only if it now names a different paper, or its year, volume, issue or pages contradict the source.
- Dates are reformatted ("2016-2018" may print as "07/2016-06/2018" or "2016 - 2018"). Only a change of meaning is a defect.
- Empty template slots print "N/A". Template instructions, hints such as "(mm/yyyy-mm/yyyy)" and template example text stay in the document.
- When the CV states the same fact twice (for example under two headings), the output keeps one copy, in the template-correct section.
- Material the template has no section for goes to an Appendix, written as in the CV. A record found there is not lost (it can still be misplaced if the template has a proper section for it).
- Supplementary descriptive text under a record is kept as struck-through text beneath the record, unless a whole record survives only as struck-through text.
- Ordering within a section, spacing, fonts and blank lines.
- An elected fellowship is listed as an honor; a membership-grade fellowship (FACS, FACC kind) stays with memberships or certifications.
- Accepted or in-press papers go to the in-press list unless they have since been published.
- Conference proceedings papers listed under peer-reviewed articles print under abstracts. A talk the CV says a student or postdoc gave prints among abstracts.
- A grant where the owner is mentor or sponsor stays in the grants section. A named chair or distinguished professorship listed under honors stays with appointments.
- Lists of staff the owner supervised go to the Appendix whole; trainees go to the mentoring section.
- The owner's own work contact details are expected.
- Funding whose end date has passed, judged against the date the output was prepared (often years after the CV was written), goes under Past (Completed) Funding; current and pending funding go under the current lists.
- A publication's bibliography sub-section (original research, reviews and editorials, case reports, and so on) follows the publication type PubMed gives the paper, when PubMed has it.
- Credentials: a licence to practise, a state controlled-substance registration, the DEA and the NPI go to Licensure; specialty board certifications and registry certifications (ASCP kind) go to Board Certification; licensing exams (USMLE, COMLEX, NBME, FLEX), the ECFMG certificate, an early board-exam step, life-support courses (BLS, ACLS, PALS, NALS), permits, procedure credentials and certificates of completion go to Other Educational Experiences.
- One activity the CV lists with several separate terms or years may print as one row per term. That is not a split, and nothing is lost.

Severity:
- high: a record lost; protected data shown; any defect on 20 or more records; or the output states a false fact about the owner that a reader would believe (wrong date, role, amount, funder, authorship, funding status).
- medium: a record in the wrong section; a field lost or a value wrong on 2 to 19 records; spurious or duplicate rows on 20 or more records.
- low: one record's lost field, wrong value or unsupported claim; fewer than 20 duplicate rows; formatting only.
When one defect affects several records the same way, report it once and list every affected index.

Confidence: "high" when you quoted both sides and are sure; "medium" when likely; "low" when you suspect it but could not confirm. Leave out anything you would only guess at.

Answer with JSON only, no prose:
{"findings": [{"idx": ["E<n>", ...], "kind": "record_lost|field_lost|wrong_value|misplaced|duplicated|merged|split|protected_data", "severity": "high|medium|low", "confidence": "high|medium|low", "source_quote": "at most 120 characters", "output_quote": "at most 120 characters, or ABSENT", "output_block": "O123 or null", "why": "one sentence"}]}
Use only E<n> index tags that appear in this chunk. If the chunk has no defect, answer {"findings": []}.
"""
#: The template's fingerprint, before any CV text goes in (CODING_STANDARDS §5.12).
REVIEW_PROMPT_SHA256 = hashlib.sha256(REVIEW_PROMPT.encode("utf-8")).hexdigest()
_OUTPUT_HEADER = "OUTPUT DOCUMENT (numbered blocks; table rows as [O<n>.<row>]):\n\n"


class SourceChunk(NamedTuple):
    """One call's worth of source: its top-level heading, its lines, and the
    element indices (truncated, as the labels read them) a reply may name."""
    top: str
    lines: list[str]
    idxs: frozenset[int]


class ChunkResult(NamedTuple):
    """What one call produced: its findings, or why it produced none."""
    findings: list[dict]
    error: str | None = None


def _fmt_idx(value: float) -> str:
    return str(int(value)) if float(value) == int(value) else str(value)


def _entry_line(entry: Mapping) -> str:
    """One source element as the model reads it: "[E12] ## HEADING" or "[E13] text"."""
    text = " ⏎ ".join(line.strip() for line in str(entry.get("text") or "").splitlines() if line.strip())
    heading = "## " if entry.get("element_type") == "header" else ""
    return f"[E{_fmt_idx(float(entry['element_idx_start']))}] {heading}{text}"


def review_chunks(stage_2: Mapping) -> list[SourceChunk]:
    """The stage-2 entries as section-sized chunks: one top-level heading per
    chunk, cut at CHUNK_MAX_CHARS or CHUNK_MAX_ENTRIES. A line naming the
    heading path goes before an entry whose path differs from the last one's."""
    chunks: list[SourceChunk] = []
    top, lines, idxs, chars, path = None, [], set(), 0, None
    for entry in stage_2.get("entries") or []:
        if entry.get("element_type") == "break" or not str(entry.get("text") or "").strip():
            continue
        entry_path = [h for h in entry.get("hierarchy") or [] if h] or ["(no heading)"]
        line = _entry_line(entry)
        if top != entry_path[0] or chars + len(line) > CHUNK_MAX_CHARS or len(idxs) >= CHUNK_MAX_ENTRIES:
            if lines:
                chunks.append(SourceChunk(top, lines, frozenset(idxs)))
            top, lines, idxs, chars, path = entry_path[0], [], set(), 0, None
        if entry_path != path and len(entry_path) > 1 and entry.get("element_type") != "header":
            lines.append(f"(heading path: {' > '.join(entry_path)})")
            path = entry_path
        lines.append(line)
        idxs.add(int(float(entry["element_idx_start"])))
        chars += len(line)
    if lines:
        chunks.append(SourceChunk(top, lines, frozenset(idxs)))
    return chunks


def parse_reply(text: str) -> list[dict] | None:
    """The reply's findings list, or None when no candidate parses as one.
    `strict=False`: a CV's tab layout reaches the quotes as raw tabs."""
    candidates = [text.strip(), *(m.group(1) for m in _FENCE_RE.finditer(text)),
                  text[text.find("{"): text.rfind("}") + 1]]
    for candidate in candidates:
        try:
            obj = json.loads(candidate, strict=False)
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("findings"), list):
            return [f for f in obj["findings"] if isinstance(f, dict)]
    return None


def _claim_idxs(claim: Mapping, valid: frozenset[int]) -> list[float]:
    """The claim's element indices that name an element of its own chunk."""
    raw = claim.get("idx")
    out = []
    for value in raw if isinstance(raw, list) else [raw]:
        match = _TAG_RE.match(str(value))
        if match and int(float(match.group(1))) in valid:
            out.append(float(match.group(1)))
    return out


def _quote(value: object) -> str:
    text = " ".join(str(value or "").split())
    return "" if text.upper() == ABSENT else text


def claim_finding(claim: Mapping, chunk: SourceChunk) -> dict | None:
    """One model claim as an INFO finding, or None when it names no element of
    its chunk or no known kind. The message is the reviewer's text; the
    evidence is the output quote (where a comment can sit), the source quote,
    then "entry <n>" per element, which doctor_vs_autopsy.py scores by."""
    kind = claim.get("kind")
    idxs = _claim_idxs(claim, chunk.idxs)
    if kind not in KIND_LABELS or not idxs:
        return None
    reason = " ".join(str(claim.get("why") or "").split())[:MAX_REASON_CHARS]
    quotes = [q for q in (_quote(claim.get("output_quote")), _quote(claim.get("source_quote"))) if q]
    entries = [f"entry {_fmt_idx(i)}" for i in idxs]
    return _finding(MODEL_REVIEW_LINT, "INFO", f"{KIND_LABELS[kind]}: {reason}", quotes + entries)


def _review_chunk(call: Callable[..., dict], system: str, chunk: SourceChunk,
                  position: int, total: int, usage: LlmUsage) -> ChunkResult:
    """One call: the chunk's findings, or the reason it has none. Never
    raises, so one failed call leaves the rest of the run's chunks running."""
    user = (f'SOURCE CHUNK {position} of {total}. Top-level source section: "{chunk.top}".\n\n'
            + "\n".join(chunk.lines)
            + "\n\nCheck every record above against the OUTPUT DOCUMENT. Answer with JSON only.")
    try:
        result = call(MODEL_REVIEW_STAGE, [{"role": "system", "content": system},
                                           {"role": "user", "content": user}])
    except Exception as e:  # recorded as this chunk's error; the finding below names it
        logger.debug("model_review: chunk %d failed", position, exc_info=True)
        return ChunkResult([], redacted_error_label(e))
    usage.add(result)
    claims = parse_reply(str(result.get("content") or ""))
    if claims is None:
        return ChunkResult([], "unparseable reply")
    return ChunkResult([f for f in (claim_finding(c, chunk) for c in claims) if f is not None])


def _not_checked(failed: Sequence[str], total: int, reason: str) -> dict:
    """The INFO finding that says how much of the CV the review did not cover."""
    labels = sorted(set(failed))
    return _finding(MODEL_REVIEW_LINT, "INFO",
                    f"{NOT_CHECKED_PREFIX}{len(failed)} of {total} source chunks not checked ({reason})",
                    labels)


def lint_model_review(stage_2: Mapping, review_text: str,
                      call: Callable[..., dict] | None, usage: LlmUsage | None) -> list[dict]:
    """Every defect the model names, one INFO finding each, plus one saying
    which chunks went unchecked. Skipped when ``call`` is None (the review is
    off) or the output document has no text. The first call runs alone, so
    the calls after it read the output document from the prompt cache; when
    it raises, the review stops there: the same fault (an outage, a model the
    role may not invoke) would fail every call."""
    if call is None:
        return [_finding("model_review", "INFO", "skipped: model review off",
                         status=STATUS_SKIPPED, reason="off")]
    if not review_text.strip():
        return [_finding("model_review", "INFO", "skipped: the output document has no text",
                         status=STATUS_SKIPPED, reason="stage_6_docx")]
    chunks = review_chunks(stage_2)
    if not chunks:
        return []
    system = REVIEW_PROMPT + "\n" + _OUTPUT_HEADER + review_text
    checked = chunks[:MAX_CHUNKS]
    first = _review_chunk(call, system, checked[0], 1, len(checked), usage)
    if first.error is not None and first.error != "unparseable reply":
        return [_finding("model_review", "INFO", f"skipped: the review call failed ({first.error})",
                         status=STATUS_SKIPPED, reason="model call")]
    rest = map_in_order(_review_chunk, [(call, system, chunk, i + 2, len(checked), usage)
                                        for i, chunk in enumerate(checked[1:])],
                        workers=CALL_WORKERS)
    results = [first, *rest]
    findings = [f for r in results for f in r.findings]
    failed = [r.error for r in results if r.error is not None]
    if failed:
        findings.append(_not_checked(failed, len(chunks), "the call failed or its reply did not parse"))
    if len(chunks) > MAX_CHUNKS:
        findings.append(_not_checked(["over the per-run ceiling"] * (len(chunks) - MAX_CHUNKS),
                                     len(chunks), f"only the first {MAX_CHUNKS} are reviewed"))
    return findings
