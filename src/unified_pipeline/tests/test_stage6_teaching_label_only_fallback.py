"""Regression guard for #574.

`_insert_teaching_entry`'s last fallback (stage 5c produced no formatted_text
and stage 4 extracted no course_title) filters an entry's raw text down to
non-label lines and bullets each survivor. When every line is a bare column
label ("Title"/"Institution"/"Dates"/"Role") the survivor list is empty and the
branch emitted zero bullets -- silently dropping the entry -- even though
institution and role are sitting in extracted_fields, unread.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_teaching_label_only_fallback.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402

from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


def _generator():
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    return gen


def _label_only_entry():
    # Every line is a column header from the source table; formatted_text and
    # course_title are both absent, so this drives the field-fallback branch.
    return {
        "taxonomy_code": "K1",
        "text": "Title\nInstitution\nDates\nRole",
        "extracted_fields": {
            "institution": "Weill Cornell Medicine",
            "role": "Course Director",
        },
    }


def test_label_only_entry_falls_back_to_institution_and_role():
    gen = _generator()
    gen._fill_teaching({"K1": [_label_only_entry()]})

    rendered = [p for p in gen.doc.paragraphs if "Weill Cornell Medicine" in p.text]
    assert len(rendered) == 1
    assert "Course Director" in rendered[0].text


def test_label_only_entry_with_no_fields_at_all_still_drops_silently():
    # No institution or role either -- there is genuinely nothing to render, so
    # the entry is still dropped. This just pins that the warning path, not a
    # crash, is what happens when the fallback truly has nothing.
    gen = _generator()
    entry = _label_only_entry()
    entry["extracted_fields"] = {}

    gen._fill_teaching({"K1": [entry]})

    assert not any("Weill Cornell Medicine" in p.text or "Course Director" in p.text
                    for p in gen.doc.paragraphs)


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
