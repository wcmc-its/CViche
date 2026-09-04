"""A leaked stage-3b taxonomy code, stripped off rendered text.

Its own module because its input is neither a CV value nor a Word marker: it
is a pipeline-internal label that escaped stage 3b into a bullet. It changes
when the taxonomy changes, and nothing else in ``normalization`` does.
"""
import re


# A leading 3b taxonomy code (M2B, D1, S6, N3A …) that leaked into a rendered
# bullet — code letter + 1-2 digits + optional trailing letter, bracketed at the
# very start and followed by whitespace. Seen verbatim in output on the WCM-
# template CVs (issue #251): "• [M2B] Project title: …", "• [D1] Visiting Prof…".
_TAXONOMY_CODE_PREFIX = re.compile(r"^\s*\[[A-Z]\d{1,2}[A-Z]?\]\s+")


def _strip_taxonomy_code(text: str) -> str:
    """Drop a leading bracketed taxonomy code from bullet text before render.

    Shape-match, not an allowlist of the 42 real codes, and the shape also
    matches a leading grant mechanism such as "[R01] ". Measured before
    keeping it that way, and regenerable with
    `scripts/measure_normalization_claims.py --only taxonomy`: across the
    farm's 412 stage-3b/4/5/5b/5c/5d artifacts (1,326,667 string values),
    exactly 2 values open with a bracketed single token at all, and both
    are the same token -- shape `Aaaaaa`, a capitalised six-letter word,
    which this pattern does not match. So 0 false positives, and 0 true
    positives too, since a leaked code is a per-run artifact rather than
    something the stored stage outputs carry. The script reports that token
    by shape and not verbatim, on purpose; the literal is pinned in
    `test_stage6_taxonomy_code_strip.py` instead. An allowlist buys nothing at 0 measured collisions and
    would need editing every time the taxonomy gains a code, so the shape
    stays; switch to the TAXONOMY_TO_SECTION key set if that measurement
    ever comes back non-zero. The cost of the shape is pinned by
    `test_stage6_taxonomy_code_strip.py::
    test_a_leading_grant_mechanism_is_stripped_by_the_shape_match`.
    """
    return _TAXONOMY_CODE_PREFIX.sub("", text) if text else text
