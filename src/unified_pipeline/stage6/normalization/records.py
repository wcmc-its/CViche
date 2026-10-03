"""Correcting a stage-4 record that is not yet in canonical form (#398).

The value modules canonicalise a value and `fields.py` canonicalises a field's
shape;
this canonicalises the record itself, in the two ways the corpus shows it
arriving wrong:

- one record holds several. The #208 fusion class reaches the bibliography, so a
  single `formatted_citation` can carry several citations, joined by newlines or
  (#1237) by " | ", that must be numbered separately.
- the record is filed under a bucket its own fields contradict. A grant whose
  status reads "Under review" is Pending regardless of what its dates imply.

`grant_status_rebucket_target` is a routing decision -- status text in, the
taxonomy code the record belongs under out -- and routing is a concern the split
has named but not yet built (`_get_wcm_section_header` and `TAXONOMY_TO_SECTION`
are its other members and are still in `stage_6_word_template`). It sits here
because it corrects a record's own classification from that record's own field,
which is normalization; move it to `routing/` when that package is created
rather than building a package for one function now.
"""
import re
from typing import Dict, List, Optional, Tuple

# Status vocabularies for grant_status_rebucket_target (#210, #575). Kept as
# code, not config: the words change only when the function beside them does,
# and any rebucketing change needs a corpus render A/B regardless of where
# the vocabulary lives.
_NOT_FUNDED_STATUS_RE = re.compile(
    r'\b(?:(?:not|non)[\s-]?funded|unfunded|declined|rejected)\b')
_PENDING_STATUS_RE = re.compile(
    # 'awaiting' alone would also match post-award statuses that lack the
    # 'award' substring ("Awaiting contract execution", "Awaiting IRB
    # approval"), so it only counts when a decision is what is awaited.
    r'\b(?:under\s+review|in\s+review|submitted|pending'
    r'|awaiting\s+(?:sponsor\s+)?decision|applications?\s+awaiting'
    r'|under\s+consideration)\b'
)
# "Review completed" / "Site visit completed" name a step of the review process,
# not an ended award, so a "completed" that follows those words is no bucket (#982).
# Likewise "Enrollment completed" / "Closed to accrual" name a stage of a clinical
# trial that is still running; only its end date may move it to M2B (#291).
_COMPLETED_STATUS_RE = re.compile(
    r'(?<!review )(?<!visit )(?<!enrollment )(?<!enrolment )(?<!accrual )(?<!recruitment )'
    r'\bcompleted?\b'
    r'|\bclosed\b(?!\s+to\s+(?:accrual|enrollment|enrolment|recruitment|new\s+patients))'
    r'|\bexpired\b')
# A status that only says a section is empty: "PENDING - none" is a CV's "no
# pending grants" label that stage 2 left inside the next grant's lines and
# stage 4 read as that grant's status (EBYSBC E7: a funded, ended grant filed
# under Pending Funding with that label as its status). "None" or "N/A" alone
# says the same. It names no bucket, so the heading and dates decide.
_EMPTY_SECTION_STATUS_RE = re.compile(
    r'(?:[a-z][a-z /&]*?\s*[-–—:]\s*)?(?:none|n/?a)\.?')
# A heading that names a current grant as well as a pending or completed one
# ("Current and Pending Support", "Past and Present") does not say which bucket
# one grant under it belongs in (#981).
_ACTIVE_HEADING_RE = re.compile(r'\b(?:current|active|ongoing|present)\b')
# A heading that files its grants as ended without the word "completed".
_PAST_HEADING_RE = re.compile(r'\b(?:past|previous(?:ly)?|prior)\b')

# Stage 5d usually puts one citation per line when a block holds several, but in
# one corpus run (#1237) it joined ten conference presentations on one line with
# this joiner instead. Kept as code, not config: it is the one joiner observed,
# and a second joiner would need its own corpus check.
_CITATION_PIPE_JOINER = ' | '
_CITATION_YEAR_RE = re.compile(r'\b(?:19|20)\d{2}\b')


def _is_citation_shaped(segment: str) -> bool:
    """Whether a pipe-delimited segment reads as a whole citation: it carries a
    publication year and ends the way 5d ends a citation, with a period. Both
    must hold so that a title which merely contains a pipe ("Home | Archive") is
    not mistaken for two citations."""
    return segment.endswith('.') and bool(_CITATION_YEAR_RE.search(segment))


def _split_pipe_joined(line: str) -> list[str]:
    """Split a line on the 5d pipe joiner, but only when EVERY segment is
    citation-shaped (#1237). All-or-nothing: one segment that is not a citation
    means the pipe belongs to a single citation, and the line is returned
    whole."""
    if _CITATION_PIPE_JOINER not in line:
        return [line]
    segments = [seg.strip() for seg in line.split(_CITATION_PIPE_JOINER)]
    if all(_is_citation_shaped(seg) for seg in segments):
        return segments
    return [line]


def split_fused_citation_entries(pubs: List[Dict]) -> List[Dict]:
    """Un-fuse publication entries whose stage-5d ``formatted_citation`` carries
    multiple citations, newline-separated or (#1237) joined by " | ".

    The #208 fusion class reaches the bibliography too: when several source
    citations collapse into one entry, stage 5d formats the whole block into a
    single ``formatted_citation`` (newline-separated), and _fill_bibliography
    then renders ONE numbered item followed by unnumbered ``<w:br/>``
    continuation lines (the "no numbering on some pubs" symptom, HNFLBA S8).

    Splitting each non-blank line into its own entry lets the caller number them
    individually. A line is further split on " | " when every piece of it is
    citation-shaped (``_split_pipe_joined``); a citation whose title merely holds
    a pipe stays whole. A non-fused citation is a single line (verified: 100/101
    of a real CV's formatted_citations have zero internal newlines), so it
    passes through untouched. Only stage-5d LLM citations with >=2 lines are split;
    other bibliography shapes (parts-built citations) never carry newlines.
    Continuation lines (i>0) are distinct records, so per-entry provenance that
    belongs to the block as a whole (classification comment, enrichment
    track-change, original text) is kept on the first line only, not replayed
    on each.
    """
    out: List[Dict] = []
    for pub in pubs:
        fields = pub.get('extracted_fields') or {}
        fc = fields.get('formatted_citation')
        fc = fc if isinstance(fc, str) else ''
        # str.splitlines() also breaks on \x0b, \x0c, \x1c-\x1e, \x85, U+2028
        # and U+2029, so a stray control character inside one LLM-written
        # citation (#552's input class) silently became two numbered entries
        # (#742). Split on real line breaks only.
        normalized = fc.replace('\r\n', '\n').replace('\r', '\n')
        lines = [
            unit
            for ln in normalized.split('\n') if ln.strip()
            for unit in _split_pipe_joined(ln.strip())
        ]
        if fields.get('formatting_source') == 'stage_5d_llm' and len(lines) >= 2:
            for i, line in enumerate(lines):
                clone = dict(pub)
                clone['extracted_fields'] = {**fields, 'formatted_citation': line}
                if i > 0:
                    clone['enrichment_status'] = ''
                    clone['text'] = ''
                    clone.pop('classification_reasoning', None)
                out.append(clone)
        else:
            out.append(pub)
    return out


def grant_status_is_empty_section_label(status: str | None) -> bool:
    """Whether a grant's status is only a CV's empty-section label ("PENDING -
    none", "None", "N/A") rather than a status of this grant (EBYSBC E7). Such
    a value is neither a bucket for `grant_status_rebucket_target` nor a
    Status row in the rendered grant."""
    lowered = str(status or '').strip().lower()
    return bool(_EMPTY_SECTION_STATUS_RE.fullmatch(lowered))


def grant_status_rebucket_target(
    status: str, label: str = 'Status', *, awarded_and_ended: bool = False
) -> Tuple[Optional[str], Optional[str]]:
    """Map a grant's extracted status string to the funding bucket it belongs
    in (#210). Returns (target_code, reclassification_note); (None, None)
    when the status doesn't force a move. `label` names where the text came
    from in the note ('Status', or 'Section heading' for
    `grant_heading_rebucket_target`).

    An explicit status beats date inference: "Under review" / "In review" /
    "Submitted" / "Awaiting sponsor decision" is Pending (M2C) no matter what
    dates say; "Not funded" is kept under Pending with a review comment
    rather than silently dropped. An empty-section label ("PENDING - none")
    is no status at all (`grant_status_is_empty_section_label`).

    `awarded_and_ended` is the caller's word that the grant carries an awarded
    total and its project period closed before this year. Such a grant is not
    awaiting a decision, so a pending word names no bucket for it (EBYSBC E7).
    A not-funded word still does: it says the total was never awarded.
    """
    status = (status or '').strip()
    if not status or grant_status_is_empty_section_label(status):
        return None, None
    lowered = status.lower()
    if _NOT_FUNDED_STATUS_RE.search(lowered):
        return 'M2C', (
            f"{label} is '{status}' — kept under Pending Funding rather than "
            "dropped; confirm whether to keep this entry on the CV"
        )
    if 'award' not in lowered and _PENDING_STATUS_RE.search(lowered):
        if awarded_and_ended:
            return None, None
        return 'M2C', f"Reclassified to Pending (M2C): {label.lower()} is '{status}'"
    if _COMPLETED_STATUS_RE.search(lowered):
        return 'M2B', f"Reclassified to Completed (M2B): {label.lower()} is '{status}'"
    return None, None


def grant_heading_rebucket_target(
    hierarchy: list[str]
) -> tuple[str | None, str | None]:
    """The bucket a grant's own section heading puts it in, for a grant whose
    record carries no status, or a status the vocabulary does not recognise
    (#981, #982).

    Stage 4 emitted a `status` field for 3 of 1268 grant records before #982
    added it to the M2A/M2B/M2C schemas, and it stays absent on any entry that
    states none, so the heading the CV filed the grant under ("Pending
    applications", "NOT FUNDED") is the only status signal most grants have.
    `research_support.explicit_status_target` decides which wins when both name
    a bucket. Same vocabulary as
    `grant_status_rebucket_target`; (None, None) when the heading is silent or
    names more than one bucket ("Current and Pending Support").
    """
    heading = ' > '.join(hierarchy or [])
    lowered = heading.lower()
    if _ACTIVE_HEADING_RE.search(lowered) and (
        _NOT_FUNDED_STATUS_RE.search(lowered) or _PENDING_STATUS_RE.search(lowered)
        or _COMPLETED_STATUS_RE.search(lowered)
    ):
        return None, None
    if _PENDING_STATUS_RE.search(lowered) and _COMPLETED_STATUS_RE.search(lowered):
        return None, None
    return grant_status_rebucket_target(heading, label='Section heading')


def grant_heading_is_past(hierarchy: list[str]) -> bool:
    """Whether the heading a grant was filed under says its grants have ended
    ("Past Grant Support", "Previous Grants"), unless it also names a current
    bucket ("Past and Present"). Used to keep an end date of "ongoing" from
    outvoting the heading (#981)."""
    lowered = ' > '.join(hierarchy or []).lower()
    return bool(_PAST_HEADING_RE.search(lowered)) and not _ACTIVE_HEADING_RE.search(lowered)
