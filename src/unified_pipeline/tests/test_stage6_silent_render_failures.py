"""Regression guard for issue #547 (the silent-failure half): a mispositioned
patent table and a skipped postdoc section both used to report success.

`patents.py:157-164` (pre-fix line numbers) caught a repositioning failure
with a bare ``except (ValueError, IndexError): pass`` and still incremented
both counters -- so a table stranded at the end of the document, instead of
under its heading, was invisible three times over: nothing logged, the stats
claimed a normal render, and the content is present so a text-only render
gate sees no change either. `postdoc_training.py:309-317` (pre-fix line
numbers) returned silently -- twice -- when the POSTDOCTORAL/TRAINING
heading or its table could not be found in the template, dropping every
postdoc entry with no record of any kind.

Both now emit `logger.warning` naming the section and the entry count,
mirroring `service.py`'s `_fill_journal_reviewing` pattern (:691-698, added
by 993642bf). The fallback *behaviour* is unchanged in both cases -- these
tests pin the new diagnostic, not a content change, so no corpus A/B is
needed (issue text: "Behaviour-neutral for output content").

Judgement call (disclosed in the PR body): on a reposition failure, patents.py
now increments a new `tables_misplaced` counter instead of `tables_populated`,
so the latter keeps meaning "landed where it should have"; `entries_inserted`
still increments either way because the content is present in the document.

    python3 -m pytest src/unified_pipeline/tests/test_stage6_silent_render_failures.py -p no:cacheprovider
"""

import logging
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


def test_patents_reposition_failure_logs_warning(caplog):
    """Force the reposition's `body_elements.index(last_element)` to fail by
    making `body.insert` itself raise ValueError -- the same except clause
    the real failure (last_element no longer present in the body) goes
    through. The table still gets built (content not lost); only the
    reposition is forced to fail."""
    gen = _new_generator()
    gen.doc.add_paragraph("Patents & Inventions")

    def _raise_value_error(*_args, **_kwargs):
        raise ValueError("forced for test")

    gen.doc.element.body.insert = _raise_value_error

    entries = [{
        'taxonomy_code': 'M2D',
        'text': 'A real patent entry',
        'extracted_fields': {'title': 'Widget for Doing Things',
                             'patent_number': 'US1234567'},
    }]

    with caplog.at_level(logging.WARNING,
                          logger='unified_pipeline.stage6.sections.patents'):
        gen._fill_patents(entries)  # must not raise

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, "expected exactly one warning, got: %r" % (
        [r.message for r in warnings],)
    message = warnings[0].getMessage()
    assert 'Patents' in message
    assert '1' in message  # entry 1 of 1

    # Fallback behaviour unchanged: content still lands in the document.
    assert len(gen.doc.tables) == 1
    # The reposition failed, so it is counted separately from a normal fill.
    assert gen.stats.get('tables_misplaced') == 1
    assert gen.stats['tables_populated'] == 0
    assert gen.stats['entries_inserted'] == 1


def test_postdoc_missing_heading_logs_warning(caplog):
    """A template with neither "POSTDOCTORAL" nor "TRAINING" anywhere used to
    return silently, dropping every entry with no diagnostic (postdoc_training.py,
    first bare return)."""
    gen = _new_generator()
    gen.doc.add_paragraph("Some Unrelated Section")

    entries_by_code = {
        'C1': [{'taxonomy_code': 'C1', 'text': 'A postdoc research entry',
                'extracted_fields': {'institution': 'Some Hospital'}}],
    }

    with caplog.at_level(
            logging.WARNING,
            logger='unified_pipeline.stage6.sections.postdoc_training'):
        gen._fill_postdoc_training(entries_by_code)  # must not raise

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, "expected exactly one warning, got: %r" % (
        [r.message for r in warnings],)
    message = warnings[0].getMessage()
    assert 'Postdoctoral Training' in message
    assert '1' in message  # 1 entry not rendered
    assert len(gen.doc.tables) == 0


def test_postdoc_missing_table_logs_warning(caplog):
    """A template whose POSTDOCTORAL heading exists but has no table after
    it used to return silently (postdoc_training.py, second bare return)."""
    gen = _new_generator()
    gen.doc.add_paragraph("POSTDOCTORAL TRAINING")
    gen.doc.add_paragraph("No table follows this heading.")

    entries_by_code = {
        'C1': [{'taxonomy_code': 'C1', 'text': 'A postdoc research entry',
                'extracted_fields': {'institution': 'Some Hospital'}}],
    }

    with caplog.at_level(
            logging.WARNING,
            logger='unified_pipeline.stage6.sections.postdoc_training'):
        gen._fill_postdoc_training(entries_by_code)  # must not raise

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, "expected exactly one warning, got: %r" % (
        [r.message for r in warnings],)
    message = warnings[0].getMessage()
    assert 'Postdoctoral Training' in message
    assert '1' in message  # 1 entry not rendered
