"""Text-normalisation helpers shared by the run doctor, the segmentation
regression harness and the render checks."""

import re
import unicodedata

# Substantive-line threshold: shorter lines ("2016", "PhD", bare bullets)
# match by accident and only add noise to the coverage metric.
SUBSTANTIVE_LINE_CHARS = 15


def _fold_marks(text: str) -> str:
    """Drop combining marks ('Müller' -> 'Muller', Greek tonos, Cyrillic
    breve) so an accented word tokenizes the same as its base letters (#541).
    NFKD then NFC: NFC recomposes Hangul jamo so a CJK string keeps its
    character count (CJK is excluded from render tokens, #722). Pure-ASCII input is
    returned untouched, byte-identical."""
    if text.isascii():
        return text
    decomposed = unicodedata.normalize("NFKD", text)
    kept = "".join(c for c in decomposed if not unicodedata.combining(c))
    return unicodedata.normalize("NFC", kept)


def norm(text: str) -> str:
    # Lowercase AFTER folding: NFKD can yield uppercase ("™" -> "TM").
    return _fold_marks(" ".join(str(text or "").split())).lower()


def squash(text: str) -> str:
    """Whitespace-FREE normalization for the coverage check: stage 2 joins
    text across in-paragraph line breaks with no whitespace at all
    ('Present position:' + break + 'Attending' -> 'position:Attending'),
    and tab-joined label/value lines re-emerge with tabs dropped. Comparing
    with whitespace removed on both sides is immune to all of that."""
    return re.sub(r"\s+", "", str(text or "")).lower()


def looks_like_record(line: str) -> bool:
    line = line.strip()
    return len(line) > 60 and (" | " in line or "\t" in line)
