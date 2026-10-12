"""What the run doctor cannot see, in words a CV owner reads (#1588).

`doctor/COVERAGE.md` is the coverage contract: a matrix of defect type by
level, each cell either covered by named lints with measured recall or
marked BLIND. Its "Not checked" section restates each BLIND cell as one
plain-language sentence, and `blind_spots()` reads those sentences so the
review copy's "not checked" box (#1589) can print them. The file is the one
definition; nothing here repeats it.

Kept to the standard library: the backend imports it without python-docx.
"""
import functools
import re
from pathlib import Path
from typing import NamedTuple

COVERAGE_PATH = Path(__file__).with_name("COVERAGE.md")
NOT_CHECKED_HEADING = "## Not checked"

#: One bullet of the section: "- `<type> / <level>`: <sentence>".
_BULLET_RE = re.compile(r"^- `(?P<type>[a-z ]+) / (?P<level>[a-z]+)`: (?P<sentence>\S.*\.)$")


class BlindSpot(NamedTuple):
    type: str        # the matrix row: missing, invented, wrong value, ...
    level: str       # the matrix column: section, entry, record, field
    sentence: str    # what a reader of the CV is told the doctor did not check


@functools.cache
def blind_spots(path: Path = COVERAGE_PATH) -> tuple[BlindSpot, ...]:
    """The "Not checked" bullets of the coverage contract, in file order.
    Raises ValueError when the section is missing, empty, or holds a line
    that is not a bullet of the expected shape: a box that silently showed
    nothing would claim the doctor checks everything."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if NOT_CHECKED_HEADING not in lines:
        raise ValueError(f"{path.name}: no '{NOT_CHECKED_HEADING}' section")
    spots = []
    for line in lines[lines.index(NOT_CHECKED_HEADING) + 1:]:
        if line.startswith("## "):
            break
        if not line.strip() or not line.startswith("- "):
            continue
        match = _BULLET_RE.match(line)
        if match is None:
            raise ValueError(f"{path.name}: not a blind-spot bullet: {line[:80]!r}")
        spots.append(BlindSpot(match["type"], match["level"], match["sentence"]))
    if not spots:
        raise ValueError(f"{path.name}: '{NOT_CHECKED_HEADING}' lists no blind spot")
    return tuple(spots)
