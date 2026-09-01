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
`_matches_word_start` -- a PREFIX match anchored at a word's start only,
with no closing bound -- against `EXTRAMURAL_ROLE_KEYWORDS`, instead of
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

Round 3: a differential probe against the pre-#658 behavior found round 2
still regressed two groups. `\\b` sits between a word character and a
non-word character, so a DIGIT counts as part of the word and cannot open
one: "2010-2012Director, ..." (6 occurrences, 1 UID) read as an
organization. And a word-start anchor cannot reach a keyword that is a
compound's SUFFIX: "subcommittee" (50 occurrences, 5 unique lines, 14 UIDs)
likewise read as an organization. Fix: the left boundary becomes the
letters-only lookbehind `(?<![A-Za-z])`, so a digit or punctuation is a
legitimate word start and only a preceding LETTER means the keyword is
buried mid-word; and 'subcommittee' is listed in EXTRAMURAL_ROLE_KEYWORDS
as its own entry. Neither change reintroduces the inflection drops above or
the "onboarding" false positive. Four residual lines are accepted and
documented in `test_present_glued_role_titles_stay_unmatched_accepted`.

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


def test_subcommittee_lines_are_role_lines():
    """A word-START anchor cannot reach "committee" inside "subcommittee"
    (the keyword is the compound's SUFFIX), so round 2 stopped classifying
    genuine subcommittee role lines as roles -- 50 line occurrences over 14
    corpus UIDs that the pre-#658 substring code had classified as roles,
    and that a short line would instead have set as `current_org`
    (service.py, `_parse_extramural_leadership_lines` else branch). Round 3
    lists 'subcommittee' in EXTRAMURAL_ROLE_KEYWORDS as its own entry
    rather than loosening the anchor, which would re-admit arbitrary
    mid-word collisions (#658 round 3)."""
    assert _matches_word_start(
        "aha hospital accreditation stroke certification subcommittee 2018-present",
        _ROLE_KEYWORDS) is True
    assert _matches_word_start(
        "internal department of pediatrics subcommittee for clinical research",
        _ROLE_KEYWORDS) is True
    assert _is_known_org_line(
        "lcme self study subcommittee: academic and learning environments",
        _ROLE_KEYWORDS) is False


def test_date_glued_role_title_is_a_role_line():
    """Stage 4 emits a date concatenated onto the role title with no
    separator when the source docx had them in adjacent cells
    ("2010-2012Director, Cardiac Prep and Recovery Unit"). `\\b` sits
    between a word character and a non-word character, so it counts the
    digits as part of the word and refuses to open one before "director" --
    round 2 read these as organization lines. The letters-only lookbehind
    `(?<![A-Za-z])` treats a digit (and any punctuation) as a legitimate
    word start; only a preceding LETTER means the keyword is buried
    mid-word. 6 occurrences on 1 corpus UID (#658 round 3)."""
    assert _matches_word_start(
        "2010-2012director, cardiac prep and recovery unit", _ROLE_KEYWORDS) is True
    assert _matches_word_start(
        "2011-2012director, baltimore va cath lab", _ROLE_KEYWORDS) is True
    assert _is_known_org_line(
        "2010-2012director, cardiac prep and recovery unit", _ROLE_KEYWORDS) is False


# --- Documented exclusions: lines this matcher deliberately does NOT match ---

def test_onboarding_is_not_a_board_role_hit():
    """The false positive #658 was filed for: "board" sitting mid-word
    inside "onboarding" counted as a role keyword under the pre-#658
    substring code (13 line occurrences in the corpus). "board" is
    preceded by "n", a letter, so neither the whole-word bound nor round
    3's letters-only lookbehind opens a word there (#658)."""
    assert _matches_word_start("onboarding", _ROLE_KEYWORDS) is False


def test_present_glued_role_titles_stay_unmatched_accepted():
    """The four lines below are the complete residual of the round-3 fix:
    each glues the keyword onto the trailing date word "present", so the
    character before the keyword is "t" -- a LETTER -- and the letters-only
    lookbehind correctly refuses to open a word there. Accepted, not fixed:

    * "2008-presentdirector, cath lab peripheral interventions"
    * "2012-presentdirector, university of maryland cardiac cath lab"
    * "2014-presentreviewer, catheterization and cardiovascular interventions"
    * "2016-presentdirector, carroll hospital center cath lab"

    All four are stage-4 concatenation artifacts on a single corpus CV
    (web08), and none of them reaches
    `_parse_extramural_leadership_lines` (0 of the flip lines satisfy that
    call site's Q-code / no-extracted-fields / >3-lines guard), so no
    corpus output changes either way. The only fix would be to strip
    trailing date words before matching, which is date-parsing
    responsibility that does not belong in a keyword matcher and would
    match "present" inside unrelated words in turn (#658 round 3)."""
    for line in (
        "2008-presentdirector, cath lab peripheral interventions",
        "2012-presentdirector, university of maryland cardiac cath lab",
        "2014-presentreviewer, catheterization and cardiovascular interventions",
        "2016-presentdirector, carroll hospital center cath lab",
    ):
        assert _matches_word_start(line, _ROLE_KEYWORDS) is False, line


def test_ementorship_mid_word_is_not_restored():
    """'eMentorship' contains 'mentor' mid-word, not at the word's start --
    "ementorship" has no `\\b` boundary immediately before "mentor". The
    pre-#658 plain-substring code matched this by accident; neither #658's
    whole-word bounding nor this round's word-start bounding restores it.
    Treated as an accepted mis-file, not a regression (#658 round 2 PR
    body): a word-start rule cannot recognize a keyword buried mid-word
    without also re-admitting arbitrary substring collisions."""
    assert _matches_word_start("ementorship", _ROLE_KEYWORDS) is False
