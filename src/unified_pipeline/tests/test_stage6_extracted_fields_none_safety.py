"""Regression guard for issue #659 (five of ten sites): entry.get('extracted_fields', {})
crashes on an explicit None.

`dict.get(key, default)` only substitutes `default` when the key is absent --
an entry that carries `extracted_fields: None` explicitly still gets `None`
back, and the very next `.get()` call on it raises `AttributeError: 'NoneType'
object has no attribute 'get'`. `licensure.py` and `postdoc_training.py`
already use the defended form `entry.get('extracted_fields') or {}`; this
fixes the same idiom at:

    mentoring.py:90   (the N3B "treat as current" override loop)
    mentoring.py:161  (Current Mentees table-fill loop)
    mentoring.py:182  (Past Mentees table-fill loop)
    other_education.py:85
    patents.py:80

research_support.py (4 sites) and formatting/values.py (1 site) are the
remaining five of the ten sites #659 names; they land in sibling PRs (#659
closes only once all three land -- see PR body).

Each test drives the real `_fill_*` section-writer entrypoint on a minimal
document -- the same pattern as test_stage6_classification_literals.py --
with an entry whose `extracted_fields` is explicitly `None`, and asserts the
call completes without raising. Fixtures are fictional.

    python3 -m pytest src/unified_pipeline/tests/test_stage6_extracted_fields_none_safety.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402

from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


def _new_generator() -> WCMTemplateGenerator:
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document()
    return gen


def test_mentoring_none_extracted_fields_does_not_raise():
    """A past-mentee entry with extracted_fields=None used to crash the
    'treat as current' override loop at mentoring.py:90, before any section
    heading is even looked up -- reached on dev regardless of template
    content."""
    gen = _new_generator()
    gen.doc.add_paragraph("MENTORING")
    gen.doc.add_paragraph("Current Mentees:")
    gen.doc.add_paragraph("Past Mentees:")

    entries_by_code = {
        'N3B': [{'taxonomy_code': 'N3B', 'text': 'A past mentee with no fields',
                 'extracted_fields': None}],
    }

    gen._fill_mentoring(entries_by_code)  # must not raise AttributeError


def test_other_education_none_extracted_fields_does_not_raise():
    """A B2 entry with extracted_fields=None used to crash the fill loop at
    other_education.py:85."""
    gen = _new_generator()
    gen.doc.add_paragraph("OTHER EDUCATIONAL")
    gen.doc.add_table(rows=1, cols=3)

    entries = [{'taxonomy_code': 'B2', 'text': 'Some training program',
                'extracted_fields': None}]

    gen._fill_other_education(entries)  # must not raise AttributeError


def test_patents_none_extracted_fields_does_not_raise():
    """An M2D entry with extracted_fields=None used to crash the fill loop
    at patents.py:80."""
    gen = _new_generator()
    gen.doc.add_paragraph("Patents & Inventions")

    entries = [{'taxonomy_code': 'M2D', 'text': 'A patent with no fields',
                'extracted_fields': None}]

    gen._fill_patents(entries)  # must not raise AttributeError
