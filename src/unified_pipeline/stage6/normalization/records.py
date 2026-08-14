"""Correcting a stage-4 record that is not yet in canonical form (#398).

`text.py` canonicalises a value and `fields.py` canonicalises a field's shape;
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
_NOT_FUNDED_STATUS_RE = re.compile(r'not\s+funded|unfunded|declined|rejected')
_PENDING_STATUS_RE = re.compile(
    r'under\s+review|in\s+review|submitted|pending|awaiting|under\s+consideration'
)
_COMPLETED_STATUS_RE = re.compile(r'\bcompleted?\b|\bclosed\b|\bexpired\b')

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
        lines = [ln.strip() for ln in fc.splitlines() if ln.strip()]
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


def grant_status_rebucket_target(status: str) -> Tuple[Optional[str], Optional[str]]:
    """Map a grant's extracted status string to the funding bucket it belongs
    in (#210). Returns (target_code, reclassification_note); (None, None)
    when the status doesn't force a move.

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
            f"Status is '{status}' — kept under Pending Funding rather than "
            "dropped; confirm whether to keep this entry on the CV"
        )
    if 'award' not in lowered and _PENDING_STATUS_RE.search(lowered):
        return 'M2C', f"Reclassified to Pending (M2C): status is '{status}'"
    if _COMPLETED_STATUS_RE.search(lowered):
        return 'M2B', f"Reclassified to Completed (M2B): status is '{status}'"
    return None, None
