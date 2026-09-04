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

    A shape match, not an allowlist of the 42 real codes, so a bracketed
    non-code such as "[R01]" is stripped too -- pinned by
    `test_stage6_taxonomy_code_strip.py`. `measure_normalization_claims.py
    --only taxonomy` says what that costs on the farm's 412 artifacts:

        values opening with a [token]    : 2
        those tokens, by shape           : {'Aaaaaa': 1}
        matching _TAXONOMY_CODE_PREFIX   : 0

    Switch to the TAXONOMY_TO_SECTION key set if the last line goes non-zero.
    """
    return _TAXONOMY_CODE_PREFIX.sub("", text) if text else text
