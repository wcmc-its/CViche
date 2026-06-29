"""Regression guard for issue #198: block-coherence corrector.

When the segmenter flattens a body-styled sub-header (e.g. "Postdoctoral Fellows
(direct mentor)") into a stray ``T`` row, Stage 3b classifies each mentee row in
isolation and attributes a THIRD PARTY's role (a mentee's "Current position")
to the profile SUBJECT, coding it C/D instead of N3.

The corrector NOTICES two structural smells -- a contiguous person-record run
split across >=2 taxonomy families (sibling-incoherence), and a T-coded header
with stranded person-record children (orphaned-header) -- then punts the flagged
block to an LLM judge. A SCOPE GUARD keeps the blast radius tiny: a judge verdict
is applied to a row ONLY when it reattributes a subject-self family to a
third-party (N) family. The header row is context, never recoded.

These tests mirror the module's ``_demo`` self-check with person-agnostic-ish
synthetic entries and lambda stub judges -- no DB, no FastAPI app, no Bedrock.
Run with:

    python3 -m pytest src/unified_pipeline/tests/test_block_coherence_corrector.py -p no:cacheprovider
"""

import sys
from pathlib import Path

# Make the repo's ``src`` importable regardless of cwd / rootdir.
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.core.validators.block_coherence_corrector import (  # noqa: E402
    apply_block_coherence_corrections,
)


def _e(idx, code, text, hier="PROFESSIONAL ACTIVITIES"):
    """Build a minimal classified entry (matches the module's ``_demo`` helper)."""
    return {"element_idx_start": idx, "taxonomy_code": code, "text": text, "hierarchy": [hier]}


# --- detection smells --------------------------------------------------------

def test_incoherent_person_block_is_flagged():
    """A contiguous person-record run split across N/C/D families -> flagged."""
    incoherent = [
        _e(10, "N3B", "Brian Kadera, MD\t2011 - 2013\tCurrent position: Assistant Professor, UCLA"),
        _e(11, "C",   "Razmik Ghukasayan, MD\t2019 - present\tCurrent position: Senior Resident, UCLA"),
        _e(12, "D2",  "Stephanie Kim, MD\t2017 - 2019\tCurrent position: Surgeon, Kaiser Permanente"),
    ]
    _, stats = apply_block_coherence_corrections(incoherent)

    assert stats["blocks_flagged"] >= 1, "should flag the incoherent person-record block"
    assert any(f["reason"] == "sibling-incoherence" for f in stats["flagged"])
    # detection only: with apply=False nothing is mutated.
    assert stats["corrections_made"] == 0
    assert [e["taxonomy_code"] for e in incoherent] == ["N3B", "C", "D2"]


def test_homogeneous_block_is_not_flagged():
    """A uniform D1 appointment block has no family disagreement -> never flagged."""
    coherent = [
        _e(20, "D1", "Assistant Professor of Surgery\t2009 - 2015\tUCLA"),
        _e(21, "D1", "Associate Professor of Surgery\t2015 - 2018\tUCLA"),
        _e(22, "D1", "Professor of Surgery\t2018 - present\tUCLA"),
    ]
    _, stats = apply_block_coherence_corrections(coherent)

    assert not any(f["reason"] == "sibling-incoherence" for f in stats["flagged"]), \
        "a homogeneous D block must not be flagged for incoherence"


def test_orphaned_t_header_is_flagged():
    """A T-coded sub-header with a stranded person-record child -> flagged."""
    orphaned = [
        _e(30, "T", "Postdoctoral Fellows (direct mentor)"),
        _e(31, "C", "Amanda Labora, MD\t2021 - present\tCurrent position: Senior Resident, UCLA"),
    ]
    _, stats = apply_block_coherence_corrections(orphaned)

    assert any(f["reason"] == "orphaned-header" for f in stats["flagged"]), \
        "a T-coded header with stranded children must be flagged"


# --- apply path (stub judges; no real LLM) -----------------------------------

def test_header_row_is_never_recoded():
    """Apply reattributes the stranded child but leaves the T header untouched."""
    orphaned = [
        _e(30, "T", "Postdoctoral Fellows (direct mentor)"),
        _e(31, "C", "Amanda Labora, MD\t2021 - present\tCurrent position: Senior Resident, UCLA"),
    ]
    judge = lambda _p: '{"coherent": true, "verdict": "third_party", "codes": ["N3A"], "confidence": 0.9, "evidence": "x"}'
    _, stats = apply_block_coherence_corrections(orphaned, llm=judge, apply=True)

    assert orphaned[0]["taxonomy_code"] == "T", "the header row must NOT be recoded"
    assert orphaned[1]["taxonomy_code"] == "N3A", "the stranded child must be reattributed to N3A"
    assert stats["corrections_made"] == 1


def test_apply_reattributes_self_family_rows_to_n():
    """Stub judge codes the block as mentees: C->N3A and D2->N3B apply, N3B unchanged."""
    incoherent = [
        _e(10, "N3B", "Brian Kadera, MD\t2011 - 2013\tCurrent position: Assistant Professor, UCLA"),
        _e(11, "C",   "Razmik Ghukasayan, MD\t2019 - present\tCurrent position: Senior Resident, UCLA"),
        _e(12, "D2",  "Stephanie Kim, MD\t2017 - 2019\tCurrent position: Surgeon, Kaiser Permanente"),
    ]
    judge = lambda _p: ('{"coherent": true, "verdict": "third_party", '
                        '"codes": ["N3B","N3A","N3B"], "confidence": 0.95, '
                        '"evidence": "named trainees under a mentoring header"}')
    _, stats = apply_block_coherence_corrections(incoherent, llm=judge, apply=True)

    # C->N3A and D2->N3B are self-family -> N reattributions; the already-N3B row is a no-op.
    assert stats["corrections_made"] == 2
    assert [e["taxonomy_code"] for e in incoherent] == ["N3B", "N3A", "N3B"]
    # the two mutated rows carry an audit trail of their original code.
    assert incoherent[1]["block_coherence_correction"]["original_code"] == "C"
    assert incoherent[2]["block_coherence_correction"]["original_code"] == "D2"
    assert "block_coherence_correction" not in incoherent[0]


def test_low_confidence_leave_it_does_not_mutate():
    """A non-coherent / low-confidence verdict applies nothing."""
    incoherent = [
        _e(10, "N3B", "Brian Kadera, MD\t2011 - 2013\tCurrent position: Assistant Professor, UCLA"),
        _e(11, "C",   "Razmik Ghukasayan, MD\t2019 - present\tCurrent position: Senior Resident, UCLA"),
        _e(12, "D2",  "Stephanie Kim, MD\t2017 - 2019\tCurrent position: Surgeon, Kaiser Permanente"),
    ]
    judge = lambda _p: '{"coherent": false, "verdict": "mixed", "codes": [], "confidence": 0.2, "evidence": "unclear"}'
    _, stats = apply_block_coherence_corrections(incoherent, llm=judge, apply=True)

    assert stats["corrections_made"] == 0
    assert [e["taxonomy_code"] for e in incoherent] == ["N3B", "C", "D2"]


def test_scope_guard_non_n_verdict_never_mutates():
    """SCOPE GUARD: a confident, coherent verdict whose codes are non-N (here D1)
    is accepted by repair_block (``applied`` True) yet mutates NO row, because the
    guard fires only on old_family != N and new_family == N."""
    incoherent = [
        _e(10, "N3B", "Brian Kadera, MD\t2011 - 2013\tCurrent position: Assistant Professor, UCLA"),
        _e(11, "C",   "Razmik Ghukasayan, MD\t2019 - present\tCurrent position: Senior Resident, UCLA"),
        _e(12, "D2",  "Stephanie Kim, MD\t2017 - 2019\tCurrent position: Surgeon, Kaiser Permanente"),
    ]
    judge = lambda _p: ('{"coherent": true, "verdict": "subject", '
                        '"codes": ["D1","D1","D1"], "confidence": 0.95, '
                        '"evidence": "re-subcoding subject\'s own positions"}')
    _, stats = apply_block_coherence_corrections(incoherent, llm=judge, apply=True)

    # The judge's verdict cleared repair_block's gate (coherent + confident + valid codes)...
    assert stats["flagged"][0]["verdict"]["applied"] is True
    # ...but the scope guard blocked every write: no row moves to a non-N family.
    assert stats["corrections_made"] == 0
    assert [e["taxonomy_code"] for e in incoherent] == ["N3B", "C", "D2"]
    assert all("block_coherence_correction" not in e for e in incoherent)
