"""Bounded keyword matching in section Q classification (#658, round 2).

`_is_q2_journal_reviewer`, `_split_q2_lines`, and `_is_known_org_line` used
plain substring containment (`kw in text_lower`) against REVIEWER_PATTERNS,
BOARD_KEYWORDS, and a `role_keywords` list -- so a keyword embedded inside
an unrelated longer word (e.g. "council" inside "councillorship", "chair"
inside "chairman") counted as a match. `_matches_bounded` (service.py)
replaces those four call sites with a `\\b...\\b`-anchored regex (whole
word/phrase only), the same technique #573 already used to stop the
licensure "Dean"/"DEA" collision.

Round 2: whole-word bounding also stopped matching `EXTRAMURAL_ROLE_KEYWORDS`'
(the promoted-to-constant former local `role_keywords`) own INFLECTED and
DERIVED forms -- "member" no longer matched "members", "chair" no longer
matched "chairman"/"chaired", "director" no longer matched "directors"/
"directorship", "mentor" no longer matched "mentoring"/"mentorship",
"council" no longer matched "councilor", "leader" no longer matched
"leaders"/"leadership". A differential probe over every entry line in the
corpus's stage-5d artifacts found dozens of real leadership-table role
lines flip role -> organization because of this ("Program Chairman,
Florida Neurosurgical Society Annual Meeting", "Association of Directors
of Medical Student Education...", "Developing Leaders in Pediatric
Graduate Medical Education").

Fix: `_is_known_org_line`'s veto and the sibling `is_role` check inside
`_parse_extramural_leadership_lines` (service.py :~653) both now use
`_matches_word_start` -- a `\\b`-anchored PREFIX match (start of a word
only, no closing `\\b`) -- against `EXTRAMURAL_ROLE_KEYWORDS`, instead of
`_matches_bounded`'s whole-word match. An explicit stem list (hand-spelling
every inflected form) was tried first and rejected: the corpus's real
inflected/derived forms turned out far more varied than the handful the
issue named (also directors, directorship, mentorship, leaders, boards,
reviewers, advisory...), and a hand list kept missing new ones on each
pass. Word-start anchoring generalizes correctly instead, because English
inflection/derivation overwhelmingly adds a SUFFIX. It also does not
regress relative to the currently-shipped whole-word match: every
whole-word match is trivially also a word-start match (confirmed by the
round-2 differential probe: 0 True-to-False flips against round-1's
behavior). The sibling `is_role` check previously used bare substring while
`_is_known_org_line`'s veto used `_matches_bounded` -- the two could each
fire for different reasons on the same line; both now call
`_matches_word_start` with the same `EXTRAMURAL_ROLE_KEYWORDS`, so they
cannot disagree.

Each fixture below is picked so the readings under test disagree. Controls
alongside them prove a genuine hit still matches -- each fix narrows or
corrects what counts, neither stops matching outright.

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
    EXTRAMURAL_ROLE_KEYWORDS,
    _is_known_org_line,
    _is_q2_journal_reviewer,
    _matches_word_start,
    _split_q2_lines,
)

# The real production constant -- imported, not hand-copied, so a future
# edit to EXTRAMURAL_ROLE_KEYWORDS can't silently drift from what these
# tests exercise.
_ROLE_KEYWORDS = EXTRAMURAL_ROLE_KEYWORDS


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
# 3. `_is_known_org_line`: role_keywords veto, `_matches_word_start`


def test_board_member_is_still_vetoed_as_a_role_line():
    """Control: a genuine whole-word role_keywords hit ('member') still
    vetoes -- same case #624 pinned in test_stage6_classification_literals.py,
    reasserted here against the word-start matcher."""
    assert _is_known_org_line("board member", _ROLE_KEYWORDS) is False


def test_program_chairman_is_still_a_role_line_not_an_organization():
    """'chairman' is the inflected form of the 'chair' role_keywords entry
    -- "Program Chairman, ..." is a role, not an organization. #658's
    whole-word `\\b...\\b` bounding stopped matching "chairman" entirely
    (it is not the WHOLE word "chair"), so this real corpus line flipped
    from a role line to an organization line, fixed this round by matching
    only the word's START (#658 round 2)."""
    assert _is_known_org_line(
        "program chairman, florida neurosurgical society annual meeting",
        _ROLE_KEYWORDS) is False


def test_members_is_still_a_role_line_not_an_organization():
    """'members' (plural of 'member') was the single largest flip category
    in the corpus probe (#658 round 2). No other role_keywords whole word
    is present, so the pre-round-2 matcher's veto never fires and
    `_GENERIC_ORG_TERMS`' 'society' wins the line by default."""
    assert _is_known_org_line("members of the society", _ROLE_KEYWORDS) is False


def test_chaired_is_still_a_role_line_not_an_organization():
    """'chaired' is the past-tense form of 'chair' (#658 round 2)."""
    assert _is_known_org_line(
        "chaired the academy symposium", _ROLE_KEYWORDS) is False


def test_directors_and_directorship_are_still_role_lines():
    """Two more inflected/derived forms of 'director' the corpus probe
    found flipped, beyond the issue's own named list -- 'directors'
    (plural, "Association of Directors of Medical Student Education...")
    and 'directorship' (derived noun, "medical directorship of
    rheumatology...") (#658 round 2)."""
    assert _is_known_org_line(
        "association of directors of medical student education in psychiatry",
        _ROLE_KEYWORDS) is False
    assert _is_known_org_line(
        "part-time medical directorship of rheumatology services",
        _ROLE_KEYWORDS) is False


def test_mentoring_councilor_leaders_are_still_role_lines():
    """The remaining inflected/derived forms the corpus probe found
    flipped: 'mentoring' (from 'mentor'), 'councilor' (from 'council'),
    'leaders' (from 'leader') (#658 round 2)."""
    assert _is_known_org_line("mentoring society outreach", _ROLE_KEYWORDS) is False
    assert _is_known_org_line("councilor of the institute", _ROLE_KEYWORDS) is False
    assert _is_known_org_line(
        "developing leaders in pediatric graduate medical education institute",
        _ROLE_KEYWORDS) is False


def test_matches_word_start_does_not_regress_a_whole_word_hit():
    """Every whole-word `_matches_bounded` hit is trivially also a
    `_matches_word_start` hit -- switching matchers only adds matches, never
    removes one (#658 round 2)."""
    for line in ("board member", "committee chair", "advisor to the dean"):
        assert _matches_word_start(line, _ROLE_KEYWORDS) is True, line


def test_subcommittee_is_not_treated_as_a_committee_role_hit():
    """'committee' does not start the word "subcommittee" -- word-start
    anchoring does not match it, matching the currently-shipped whole-word
    behavior (which also does not match it). Not a regression this round
    introduces: the differential probe found `_matches_bounded` already
    missed this line before this round's change (#658 round 2)."""
    assert _matches_word_start("subcommittee", _ROLE_KEYWORDS) is False


def test_ementorship_mid_word_is_not_restored():
    """'eMentorship' contains 'mentor' mid-word, not at the word's start --
    "ementorship" has no `\\b` boundary immediately before "mentor". The
    pre-#658 plain-substring code matched this by accident; neither #658's
    whole-word bounding nor this round's word-start bounding restores it.
    Treated as an accepted mis-file, not a regression (#658 round 2 PR
    body): a word-start rule cannot recognize a keyword buried mid-word
    without also re-admitting arbitrary substring collisions."""
    assert _matches_word_start("ementorship", _ROLE_KEYWORDS) is False
