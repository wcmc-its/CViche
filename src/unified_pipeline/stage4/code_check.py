"""Membership check on the taxonomy code stage 4 receives from stage 3b (#651).

A code outside `core/taxonomy_v7.json` used to pick `DEFAULT_SCHEMA` in
`get_field_schema` with no record, and stage 6 then swept the entry into the
Appendix indistinguishably from a legitimately unrouted one. This is
CODING_STANDARDS.md 5.10's recourse ladder for that predicate: the entry is
quarantined -- re-coded to `QUARANTINE_CODE` (the Appendix's own code) with the
failed predicate named on the entry and the original code kept -- never
silently defaulted. There is no retry rung: the code came from an earlier
stage, and re-asking stage 3b is not this stage's call.
"""

import logging
from collections import Counter
from typing import Any

from unified_pipeline.core.retired_taxonomy_codes import live_taxonomy_code
from unified_pipeline.stage3b.io import canonical_taxonomy_codes

logger = logging.getLogger(__name__)

QUARANTINE_CODE = "T"
# Codes outside taxonomy_v7.json that stage 6 still renders (the postdoc
# training children, POSTDOC_TRAINING_CODES) and that 3b's reasoning corrector
# can emit. Accepted, not quarantined, so a corrector-set C1-C3 keeps rendering
# where it does today; reconciling the lists is #383.
RENDERED_NON_V7_CODES = frozenset({"C1", "C2", "C3"})
INVALID_CODE_REASON = "invalid_taxonomy_code"


def quarantine_invalid_taxonomy_codes(
    entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Return (entries, {original code repr: count}) with every entry whose
    `taxonomy_code` is not a canonical code re-coded to `QUARANTINE_CODE`.

    An entry with a canonical code is passed through as the same object, so
    the valid path is unchanged. A quarantined entry is a copy carrying
    `original_taxonomy_code` and `taxonomy_code_quarantine_reason`; the caller
    owns the stats. Logs one WARNING per distinct offending code.
    """
    valid = canonical_taxonomy_codes() | RENDERED_NON_V7_CODES
    out: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    for entry in entries:
        code = entry.get("taxonomy_code")
        if isinstance(code, str) and code in valid:
            out.append(entry)
            continue
        live = live_taxonomy_code(code)
        if live != code and live in valid:
            # Stage-3b output stored before the code was retired (#291), e.g.
            # a step-4 retry of an older run: re-coded, not quarantined.
            out.append({**entry, "taxonomy_code": live, "taxonomy_code_original": code})
            continue
        rejected[repr(code)] += 1
        out.append({
            **entry,
            "taxonomy_code": QUARANTINE_CODE,
            "original_taxonomy_code": code,
            "taxonomy_code_quarantine_reason": INVALID_CODE_REASON,
        })
    for code_repr, count in rejected.items():
        logger.warning(
            "Stage 4: %d entr%s carried taxonomy code %s, which is not in "
            "taxonomy_v7.json or rendered by stage 6; quarantined as %s (%s)",
            count, "y" if count == 1 else "ies", code_repr,
            QUARANTINE_CODE, INVALID_CODE_REASON,
        )
    return out, dict(rejected)
