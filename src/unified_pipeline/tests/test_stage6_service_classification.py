"""Bounded keyword matching in section Q classification (#658).

`_is_q2_journal_reviewer`, `_split_q2_lines`, and `_is_known_org_line` used
plain substring containment (`kw in text_lower`) against REVIEWER_PATTERNS,
BOARD_KEYWORDS, and a `role_keywords` list -- so a keyword embedded inside
an unrelated longer word (e.g. "council" inside "councillorship", "chair"
inside "chairman") counted as a match. `_matches_bounded` (service.py)
replaces those four call sites with a `\\b`-anchored regex, the same
technique #573 already used to stop the licensure "Dean"/"DEA" collision.

Each fixture below is picked so the *un*bounded reading and the bounded
reading disagree: a keyword sits inside a longer word that is not itself
the concept the keyword names. Controls alongside them prove a genuine
whole-word/whole-phrase hit still matches -- the fix narrows what counts,
it does not stop matching outright.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_service_classification.py -p no:cacheprovider

Self-contained: no DB, no LLM calls, no docx object -- every function under
test here takes plain strings and returns a plain value.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.sections.service import (  # noqa: E402
    _is_known_org_line,
    _is_q2_journal_reviewer,
    _split_q2_lines,
)

# Mirrors the `role_keywords` list local to
# `_parse_extramural_leadership_lines` (service.py) -- passed explicitly
# because `_is_known_org_line` takes it as a parameter.
_ROLE_KEYWORDS = ['member', 'chair', 'reviewer', 'liaison', 'mentor', 'committee',
                  'board', 'council', 'advisor', 'director', 'leader', 'representative']


# ---------------------------------------------------------------------------
# 1. `_is_q2_journal_reviewer`: BOARD_KEYWORDS veto, REVIEWER_PATTERNS hit


def test_embedded_council_does_not_veto_a_journal_reroute():
    """'council' is a BOARD_KEYWORDS entry; "councillorship" merely contains
    it as a run of letters. Plain substring matching vetoed the reroute for
    any entry naming a councillorship, even a plainly journal one."""
    assert _is_q2_journal_reviewer(
        "manuscript reviewer, general councillorship", "reviewer", "", "") is True


def test_committee_still_vetoes_a_journal_reroute():
    """Control: a genuine whole-word BOARD_KEYWORDS hit still vetoes."""
    assert _is_q2_journal_reviewer(
        "manuscript reviewer, education committee", "reviewer", "", "") is False


def test_reviewer_for_still_triggers_the_reroute():
    """Control: a genuine whole-phrase REVIEWER_PATTERNS hit still counts,
    with no board signal present."""
    assert _is_q2_journal_reviewer(
        "reviewer for the annals of pediatrics", "member", "", "") is True


# ---------------------------------------------------------------------------
# 2. `_split_q2_lines`: same two constants, line-by-line


def test_split_q2_lines_embedded_council_line_is_not_forced_to_board():
    """Same fixture as above, driven through the line splitter: a
    councillorship line naming a REVIEWER_PATTERNS phrase and no genuine
    board term must land in journal_lines, not board_lines."""
    journal_lines, board_lines = _split_q2_lines(
        ["Manuscript reviewer, general councillorship"])
    assert journal_lines == ["Manuscript reviewer, general councillorship"]
    assert board_lines == []


def test_split_q2_lines_committee_line_still_lands_in_board():
    """Control: a genuine committee line still lands in board_lines."""
    journal_lines, board_lines = _split_q2_lines(["Member, Education Committee"])
    assert board_lines == ["Member, Education Committee"]
    assert journal_lines == []


# ---------------------------------------------------------------------------
# 3. `_is_known_org_line`: role_keywords veto


def test_embedded_chair_does_not_veto_an_organization_line():
    """'chair' is a role_keywords entry; "chairman" merely contains it as a
    run of letters. The line names a real organization ("... Institute for
    Policy Studies") and carries no genuine role vocabulary, so it must
    still classify as an organization line."""
    assert _is_known_org_line(
        "chairman institute for policy studies", _ROLE_KEYWORDS) is True


def test_board_member_is_still_vetoed_as_a_role_line():
    """Control: a genuine whole-word role_keywords hit ('member') still
    vetoes -- same case #624 pinned in test_stage6_classification_literals.py,
    reasserted here against the bounded matcher."""
    assert _is_known_org_line("board member", _ROLE_KEYWORDS) is False
