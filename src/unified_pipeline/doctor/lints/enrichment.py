"""Lints for the two gates that decide whether a run is deliverable at all (#493).

One responsibility: whether the run has an owner and whether its citations are
the enriched ones. Both findings are about what stage 5/4 attached to a record
rather than how the record was cut or rendered, and neither reads the output
document.

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

from unified_pipeline.quality_score import cv_owner_name_missing

from ..shared import _finding


# --------------------------------------------------------------------------
# Stage-5 PubMed enrichment: citations that fell back to CV-extracted fields.


def lint_enrichment_failures(stage5e: dict) -> list[dict]:
    """Publications whose stage-5 PubMed enrichment ended in a *_failed status
    (lookup_failed, pmcid_conversion_failed, doi_found_but_fetch_failed):
    their citations degrade to CV-extracted fields. Non-failure outcomes
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
