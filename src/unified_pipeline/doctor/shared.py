"""Primitives every lint needs, regardless of which stage it inspects (#493).

One responsibility: the vocabulary of a finding, and the text normalisation the
lints agree on. `_finding` is used by all sixteen; the rest are the matching
helpers that more than one domain needs, so they cannot live inside any single
lint module without one domain importing another.

Nothing here imports `run_doctor`. That is what keeps the dependency a straight
line -- run_doctor and every lint module depend on this, and it depends on
neither.
"""
import re
from typing import Dict, List, NamedTuple, Optional, Tuple

from unified_pipeline.core.render_check import entry_fragments
from unified_pipeline.segmentation_regression import _norm, _squash


# Lint 5: a squashed text piece shorter than this matches by accident; a
# longer fragment is matched by its leading window, so a reformatted tail
# (5d trims trailing publisher details) doesn't hide a rendered line.
RENDER_PIECE_MIN_CHARS = 15


RENDER_PIECE_WINDOW = 40


# Lints 3/5 fallback: stages 4-6 re-render most entries from extracted fields
# (5c teaching / 5d citation formatters), so no verbatim piece survives; an
# entry counts as rendered when its whole text — or any single fragment of it
# (stage 6 renders the extracted title/institution fields and drops long
# narratives) — has at least this many distinctive tokens and this share of
# them appear in the output.
RENDER_TOKEN_MIN_COUNT = 3


RENDER_TOKEN_OVERLAP = 0.7


_RENDER_TOKEN_RE = re.compile(r"[a-z]{5,}")


# Separator joined between output lines in the containment haystack so a
# verbatim piece can't match across two unrelated lines (a control char that
# never occurs in real CV text).
_LINE_SENTINEL = "\x00"


def _long_word_tokens(text) -> set:
    """5+-letter token set for one string (lints 5/8 render-overlap checks)."""
    return set(_RENDER_TOKEN_RE.findall(_norm(text)))


# WCM output section headers come letter-prefixed ("T. APPENDIX") or as plain
# uppercase paragraphs ("RESEARCH", "MENTORING") — stage 6 emits both forms.
_SECTION_HEADER_RE = re.compile(r"^[A-Z]\.\s+\S")


def _output_section_header(text: str) -> Optional[str]:
    """Normalized section name when a paragraph is a WCM output section
    header (either form above), else None."""
    stripped = str(text or "").strip()
    if _SECTION_HEADER_RE.match(stripped):
        return _norm(re.sub(r"^[A-Z]\.\s+", "", stripped))
    if (3 <= len(stripped) <= 60 and stripped[0].isalpha()
            and stripped == stripped.upper()
            and not any(ch.isdigit() for ch in stripped)):
        return _norm(stripped)
    return None


def _finding(lint: str, severity: str, message: str,
             evidence: Optional[List[str]] = None) -> Dict:
    return {"lint": lint, "severity": severity, "message": message,
            "evidence": evidence or []}


def _magnitude_severity(observed: float, threshold: float) -> str:
    """WARN when this run is in the corpus's worst quartile, else INFO.

    Severity that encodes presence cannot rank anything: `echo_paragraphs=20`
    and `echo_paragraphs=1` are the same lint and used to be the same WARN."""
    return "WARN" if observed >= threshold else "INFO"


class Haystack(NamedTuple):
    text: str    # squashed containment haystack, _LINE_SENTINEL-joined
    tokens: set  # distinctive long-word token set

def _haystacks(blocks: List[Tuple[str, str]]) -> Haystack:
    """Containment haystack + distinctive-token set for the output blocks.
    Read the result via its named fields (``h.text`` / ``h.tokens``), not
    positional unpacking."""
    pieces: List[str] = []
    tokens: set = set()
    for _, text in blocks:
        for line in str(text).split("\n"):
            if line.strip():
                pieces.append(_squash(line))
                tokens.update(_long_word_tokens(line))
    return Haystack(_LINE_SENTINEL.join(pieces), tokens)


def _entry_pieces(text) -> List[str]:
    """Squashed fragments of an entry long enough to be looked up in the
    output haystack."""
    pieces = []
    for frag in entry_fragments(text):
        squashed = _squash(frag)
        if len(squashed) >= RENDER_PIECE_MIN_CHARS:
            pieces.append(squashed[:RENDER_PIECE_WINDOW])
    return pieces
