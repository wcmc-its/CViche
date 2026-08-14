"""Stage 3b artifact I/O (#522).

Moved verbatim from `stage_3b_entry_classifier.py`: loading the taxonomy, the
stage 2 entries and the stage 3a mappings, the normalisation applied to
unvalidated stage 3a LLM output at load time (#558), and the `_safe_float`
coercion that normalisation shares with the classification passes.

The split's one non-move edit lives here: `load_taxonomy` gained a `.parent`
hop because this file sits one directory deeper than the module the function
moved out of.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


def _safe_float(value, default: float) -> float:
    """Coerce an LLM-provided numeric (e.g. a confidence) to float.

    The classifier LLM call uses ``response_format={"type": "json_object"}`` with
    no schema, so a confidence field can come back as a stringified number like
    ``"0.65"``. Downstream code compares it with ``< 0.7`` (in reconnect_fragments
    here and in stage_6_word_template.py), which raises
    ``TypeError: '<' not supported between instances of 'str' and 'float'`` and
    fails the entire run. Coerce defensively; fall back to ``default`` on anything
    non-numeric.
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def load_taxonomy() -> Dict:
    """Load the taxonomy reference JSON."""
    # One .parent more than the original stage_3b_entry_classifier.py site:
    # this file lives in stage3b/, one directory below unified_pipeline/.
    taxonomy_path = Path(__file__).parent.parent / "core" / "taxonomy_v7.json"
    with open(taxonomy_path, 'r') as f:
        return json.load(f)


def load_stage_2_entries(path: Path) -> Tuple[List[Dict], List[Dict]]:
    """
    Load Stage 2 entries, filtering to content entries only.

    Returns:
        Tuple of (content_entries, all_entries)
        - content_entries: Entries to classify (excludes headers and empty breaks)
        - all_entries: All entries for reference
    """
    with open(path, 'r') as f:
        data = json.load(f)

    all_entries = data.get("entries", [])

    # Filter to content entries only
    # - Always skip headers (section structure, not content)
    # - Skip "break" entries ONLY if they have no meaningful text content
    #   (Many "break" entries are actually content the LLM missed extracting)
    content_entries = []
    for e in all_entries:
        element_type = e.get("element_type", "")
        text = e.get("text", "").strip()

        # Always skip headers
        if element_type == "header":
            continue

        # For "break" entries, only skip if they're actually empty/whitespace
        if element_type == "break":
            if not text or len(text) < 3:  # Skip empty or trivially short breaks
                continue
            # Otherwise, this "break" has real content - include it for classification

        content_entries.append(e)

    return content_entries, all_entries


def load_stage_3a_mappings(path: Path) -> Dict:
    """Load Stage 3a header taxonomy mappings.

    ``mappings`` is unvalidated LLM output -- stage 3a parses it with
    ``json.loads()`` from a ``json_object``-mode call with no schema (#558).
    Nine call sites downstream subscript ``o["code"]``/``o["confidence"]``
    with no guard; a malformed option previously raised uncaught (KeyError
    on a missing "code", ValueError on a stringified confidence, TypeError
    on a bare-string option) and lost the entire run. Normalise once, here,
    before any of those sites ever see it.
    """
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(
            f"stage 3a artifact at {path} is not a JSON object (got "
            f"{type(data).__name__}) -- cannot contain a 'mappings' key"
        )
    mappings = data.get("mappings", [])
    if not isinstance(mappings, list):
        logger.warning(
            "stage 3a artifact %s: 'mappings' is not a list (%s) -- treating "
            "as empty", path, type(mappings).__name__)
        mappings = []
        data["mappings"] = mappings
    _normalize_taxonomy_mappings(mappings)
    return data


def _normalize_taxonomy_mappings(nodes: List[Dict]) -> None:
    """Recursively drop malformed nodes/taxonomy_options entries and coerce
    confidence to float, in place. See load_stage_3a_mappings (#558)."""
    nodes[:] = [n for n in nodes if isinstance(n, dict)]
    for node in nodes:
        title = node.get("title", "?")

        options = node.get("taxonomy_options")
        if isinstance(options, list):
            kept = []
            for option in options:
                if not isinstance(option, dict) or not option.get("code"):
                    logger.warning(
                        "stage 3a mapping %r: dropping malformed taxonomy_options "
                        "entry (%r) -- not an object or missing/empty 'code'", title, option)
                    continue
                option["code"] = str(option["code"])
                confidence = _safe_float(option.get("confidence"), 0.5)
                # _safe_float only guarantees convertibility, not domain -- a
                # confidence outside [0, 1] (including NaN/inf, which compare
                # false against any bound) falls back to the same 0.5 default
                # as an unparseable value.
                option["confidence"] = confidence if 0.0 <= confidence <= 1.0 else 0.5
                kept.append(option)
            if len(kept) != len(options):
                logger.warning(
                    "stage 3a mapping %r: taxonomy_options dropped from %d to %d "
                    "entries after normalisation", title, len(options), len(kept))
            node["taxonomy_options"] = kept
        elif options is not None:
            logger.warning(
                "stage 3a mapping %r: 'taxonomy_options' is not a list (%s) -- "
                "treating as empty", title, type(options).__name__)
            node["taxonomy_options"] = []

        children = node.get("children")
        if isinstance(children, list):
            _normalize_taxonomy_mappings(children)
        elif children is not None:
            logger.warning(
                "stage 3a mapping %r: 'children' is not a list (%s) -- treating "
                "as empty", title, type(children).__name__)
            node["children"] = []
