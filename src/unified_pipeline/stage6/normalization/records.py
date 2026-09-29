"""Correcting a stage-4 record that is not yet in canonical form (#398).

The value modules canonicalise a value and `fields.py` canonicalises a field's
shape;
this canonicalises the record itself, in the two ways the corpus shows it
arriving wrong:

- one record holds several. The #208 fusion class reaches the bibliography, so a
  single `formatted_citation` can carry several newline-separated citations that
  must be numbered separately.
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
_COMPLETED_STATUS_RE = re.compile(
    r'(?<!review )(?<!visit )\bcompleted?\b|\bclosed\b|\bexpired\b')
# A heading that names a current grant as well as a pending or completed one
# ("Current and Pending Support", "Past and Present") does not say which bucket
# one grant under it belongs in (#981).
_ACTIVE_HEADING_RE = re.compile(r'\b(?:current|active|ongoing|present)\b')
# A heading that files its grants as ended without the word "completed".
_PAST_HEADING_RE = re.compile(r'\b(?:past|previous(?:ly)?|prior)\b')

def split_fused_citation_entries(pubs: List[Dict]) -> List[Dict]:
    """Un-fuse publication entries whose stage-5d ``formatted_citation`` carries
    multiple newline-separated citations.

    The #208 fusion class reaches the bibliography too: when several source
    citations collapse into one entry, stage 5d formats the whole block into a
    single ``formatted_citation`` (newline-separated), and _fill_bibliography
    then renders ONE numbered item followed by unnumbered ``<w:br/>``
    continuation lines (the "no numbering on some pubs" symptom, HNFLBA S8).

    Splitting each non-blank line into its own entry lets the caller number them
    individually. A non-fused citation is a single line (verified: 100/101 of a
    real CV's formatted_citations have zero internal newlines), so it passes
    through untouched. Only stage-5d LLM citations with >=2 lines are split;
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
        lines = [ln.strip() for ln in normalized.split('\n') if ln.strip()]
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


def grant_status_rebucket_target(
    status: str, label: str = 'Status'
) -> Tuple[Optional[str], Optional[str]]:
    """Map a grant's extracted status string to the funding bucket it belongs
    in (#210). Returns (target_code, reclassification_note); (None, None)
    when the status doesn't force a move. `label` names where the text came
    from in the note ('Status', or 'Section heading' for
    `grant_heading_rebucket_target`).

    An explicit status beats date inference: "Under review" / "In review" /
    "Submitted" / "Awaiting sponsor decision" is Pending (M2C) no matter what
    dates say; "Not funded" is kept under Pending with a review comment
    rather than silently dropped.
    """
    status = (status or '').strip()
    if not status:
        return None, None
    lowered = status.lower()
    if _NOT_FUNDED_STATUS_RE.search(lowered):
        return 'M2C', (
            f"{label} is '{status}' — kept under Pending Funding rather than "
            "dropped; confirm whether to keep this entry on the CV"
        )
    if 'award' not in lowered and _PENDING_STATUS_RE.search(lowered):
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
