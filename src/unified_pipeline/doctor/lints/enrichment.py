"""Lints for the two gates that decide whether a run is deliverable at all (#493).

One responsibility: whether the run has an owner and whether its citations are
the enriched ones. Both findings are about what stage 5/4 attached to a record
rather than how the record was cut or rendered, and neither reads the output
document.

`lint_pubmed_title_truncated` and `lint_enrichment_pubtype_mismatch` (EBYSBC
E19) read the same stage-5 artifact for an accepted PubMed record whose text
is wrong: a title cut short, or a correction or retraction notice accepted in
place of the paper. Stage 6 renders the PubMed title as a tracked replacement
of the CV's own, so either one reaches the document once changes are accepted.

`lint_owner_contact_missing` is the quality score's cap-25 HARD-FAIL gate --
without a usable `cv_owner` name the document cannot be delivered under
anyone's name, so it is not a degraded run but a blocked one. It reuses the
scorer's own predicate (`quality_score.cv_owner_name_missing`) so the doctor
and the score can never disagree about whether the gate fired.

`lint_enrichment_failures` is the softer half of the same question: a
publication whose stage-5 PubMed lookup ended in a `*_failed` status still
renders, but from CV-extracted fields rather than the canonical citation
(#222).

`_OWNER_GATE` and `_OWNER_CAP`, the two message fragments the owner finding
shares between its absent-artifact and empty-name branches, are used by nothing
else in `run_doctor.py`, so they move here and stop being module-global. No
name needed to move to `doctor.shared`: nothing in this domain is used by
another. Bodies are unmodified; `run_doctor` re-exports every name it exported
before.
"""

import re

from unified_pipeline.quality_score import cv_owner_name_missing
from unified_pipeline.stage_5_pubmed_enrichment import NOTICE_PUBTYPES

from ..shared import _finding


# --------------------------------------------------------------------------
# Stage-5 PubMed enrichment: citations that fell back to CV-extracted fields.


def lint_enrichment_failures(stage5e: dict) -> list[dict]:
    """Publications whose stage-5 PubMed enrichment ended in a *_failed status
    (lookup_failed, pmcid_conversion_failed, doi_found_but_fetch_failed,
    title_check_failed): their citations degrade to CV-extracted fields. Non-failure outcomes
    (enriched, no_identifier, doi_not_in_pubmed) are expected vocabulary."""
    failed = [e for e in stage5e.get("entries", [])
              if str(e.get("enrichment_status") or "").endswith("_failed")]
    if not failed:
        return []
    counts: dict[str, int] = {}
    for e in failed:
        status = str(e.get("enrichment_status"))
        counts[status] = counts.get(status, 0) + 1
    breakdown = ", ".join(f"{s}: {n}" for s, n in sorted(counts.items()))
    return [_finding(
        "enrichment_failures", "WARN",
        f"{len(failed)} publication(s) failed PubMed enrichment ({breakdown}) "
        f"— citations degrade to CV-extracted fields (#222)",
        [str(e.get("text", ""))[:100] for e in failed[:3]])]


# --------------------------------------------------------------------------
# Stage-5 PubMed enrichment: an accepted record whose text is wrong (EBYSBC E19).


#: The status stage 5 gives a record it accepted from PubMed. Stage 6 renders
#: only these as a tracked replacement of the CV's citation
#: (`stage6/sections/bibliography.py`), so only these can put a wrong PubMed
#: title into the document.
ACCEPTED_ENRICHMENT_STATUS = "enriched"

#: A PubMed ArticleTitle ends in one of these. Before #1358, stage 5 read the
#: title with `.text`, which stops at the first inline element (an italic
#: gene or organism name, a superscript), so a cut title ends mid-phrase:
#: an invented example is "Sediment transport in ".
#: Measured over the 63-run farm (2026-10-04): of 1,432 accepted titles, the
#: 22 that end in no character of this set are all cut, and each renders cut
#: in the base docx.
TITLE_TERMINAL_CHARS = frozenset(".?!)]\"'\u201d\u2019")

#: Notice records about a paper rather than the paper. Stage 5's
#: `NOTICE_PUBTYPES` (#1358) plus "Retraction of Publication", the PubMed
#: publication type of a retraction notice, which stage 5's set does not
#: name. "Comment" is not here: all 24
#: Comment-typed records accepted on the farm are the CV's own paper (title
#: overlap 0.86 or more), and "Retracted Publication" is the paper itself.
DOCTOR_NOTICE_PUBTYPES = NOTICE_PUBTYPES | {"Retraction of Publication"}

#: A PubMed title that opens like a notice ("Correction: <the paper's title>").
#: Applied with `re.match`, which anchors at the start, so no `^`.
_NOTICE_TITLE_RE = re.compile(
    r"\s*(?:author\s+)?(?:correction|erratum|corrigendum|retraction"
    r"|expression of concern)\b", re.IGNORECASE)

#: The CV itself lists the notice: its entry text or title names a correction,
#: erratum or retraction, so citing the notice is what the CV asked for.
_CV_NAMES_NOTICE_RE = re.compile(
    r"\b(?:correction|erratum|errata|corrigendum|retraction|retracted"
    r"|expression of concern)\b", re.IGNORECASE)


def _accepted_pubmed_records(stage5e: dict) -> list[tuple[dict, dict]]:
    """(entry, enrichment_data) for every record stage 5 accepted from PubMed."""
    accepted = []
    for e in stage5e.get("entries", []):
        data = e.get("enrichment_data")
        if e.get("enrichment_status") == ACCEPTED_ENRICHMENT_STATUS and isinstance(data, dict):
            accepted.append((e, data))
    return accepted


def _cv_title(entry: dict) -> str:
    fields = entry.get("extracted_fields")
    return str((fields or {}).get("title") or "") if isinstance(fields, dict) else ""


def _entry_label(entry: dict) -> str:
    return f"entry {entry.get('element_idx_start')} ({entry.get('taxonomy_code') or '?'})"


def lint_pubmed_title_truncated(stage5e: dict) -> list[dict]:
    """An accepted PubMed title that ends without terminal punctuation: it
    was cut at inline markup, and stage 6 renders the cut title in place of
    the CV's full one (QNZADH-02, AKPQEB-01). WARN, one per citation.

    An empty title is not reported: stage 6 then falls back to the CV's
    title (`stage6/normalization/publication.py`), so nothing cut renders.
    A PubMed title shorter than the CV's is not reported either: on the farm
    the two such titles that end in a full stop are the paper's published
    title, which differs from the one the CV gives."""
    findings = []
    for entry, data in _accepted_pubmed_records(stage5e):
        title = str(data.get("pubmed_title") or "").rstrip()
        if not title or title[-1] in TITLE_TERMINAL_CHARS:
            continue
        findings.append(_finding(
            "pubmed_title_truncated", "WARN",
            f"{_entry_label(entry)}: the PubMed title stage 5 accepted ends "
            f"mid-phrase, so the citation renders it cut (EBYSBC E19, #1358)",
            [f"PubMed title: {len(title)} chars, ends {title[-30:]!r}; "
             f"the CV's title: {len(_cv_title(entry))} chars"]))
    return findings


def lint_enrichment_pubtype_mismatch(stage5e: dict) -> list[dict]:
    """An accepted PubMed record that is a notice about a paper (an erratum,
    a retraction, an expression of concern) where the CV lists the paper:
    the citation then renders the notice's title, authors and pages
    (QNZADH-01). WARN, one per citation. A CV entry that itself names a
    correction or retraction is the notice by the CV's own choice."""
    findings = []
    for entry, data in _accepted_pubmed_records(stage5e):
        pubtypes = [str(p) for p in data.get("publication_types") or []]
        notice_types = sorted(DOCTOR_NOTICE_PUBTYPES.intersection(pubtypes))
        notice_title = bool(_NOTICE_TITLE_RE.match(str(data.get("pubmed_title") or "")))
        if not notice_types and not notice_title:
            continue
        cv_side = f"{entry.get('text') or ''} {_cv_title(entry)}"
        if _CV_NAMES_NOTICE_RE.search(cv_side):
            continue
        why = ", ".join(notice_types) or "a title that opens as a notice"
        findings.append(_finding(
            "enrichment_pubtype_mismatch", "WARN",
            f"{_entry_label(entry)}: stage 5 accepted a PubMed notice ({why}) "
            f"for a paper the CV lists, so the citation renders the notice "
            f"(EBYSBC E19, #1358)",
            [f"publication_types: {', '.join(pubtypes) or 'none'}; "
             f"enrichment_source: {entry.get('enrichment_source') or 'unknown'}"]))
    return findings


# --------------------------------------------------------------------------
# The cap-25 HARD-FAIL gate: the run has no deliverable owner.


_OWNER_GATE = "HARD-FAIL gate 'CV owner name / contact populated'"
_OWNER_CAP = "the quality score is capped at 25 (RED, do not deliver)"


def lint_owner_contact_missing(stage4: dict | None, uid: str,
                               unreadable: str | None = None) -> list[dict]:
    """The quality score's cap-25 hard-fail gate: the document cannot be
    delivered under anyone's name. The predicate is the scorer's own
    (quality_score.cv_owner_name_missing), applied to the stage-4 artifact the
    doctor already loads — the same ``*_fields.json`` the scorer reads.

    Accepts ``stage4=None`` rather than being skipped by ``_ready`` because
    score_cv_owner caps at 25 for an ABSENT ``*_fields.json`` too ("no
    fields.json found"); the call site decides when that case is a real run
    rather than a wrong uid.

    The evidence names which fields are populated but never their values: a
    partly-extracted owner (LLM found a surname but no given name) fires this
    gate, and the doctor report is mirrored to S3 and served by the admin
    viewer."""
    if stage4 is None:
        cause = (f"the stage-4 *_fields.json will not parse ({unreadable})"
                 if unreadable else
                 "there is no stage-4 *_fields.json for this document")
        return [_finding("owner_contact_missing", "ERROR",
                         f"{_OWNER_GATE}: {cause} — {_OWNER_CAP}")]
    if not cv_owner_name_missing(stage4):
        return []
    cv_owner = stage4.get("cv_owner", {}) or {}
    fields = ("full_name", "first_name", "last_name")
    populated = [f for f in fields if str(cv_owner.get(f) or "").strip()]
    evidence = ["cv_owner name fields populated: " + (", ".join(populated)
                                                      or "none")]
    if str(cv_owner.get("last_name") or "").strip().lower() == uid.lower():
        evidence.append(
            "last_name is the document uid — stage 4 fell back to the file "
            "stem, so the rendered document carries the uid as the owner name")
    return [_finding(
        "owner_contact_missing", "ERROR",
        f"{_OWNER_GATE}: the cv_owner block has no usable name — {_OWNER_CAP}",
        evidence)]
