"""app/services/review_loop_service.py (#1587): verdict checks and the
corrected-copy diff. The routes over them are in test_feedback_notification.py.
Every document here is synthetic."""
import io
import json
import zipfile

import pytest
from fastapi import HTTPException

from app.models import ReviewVerdict
from app.schemas import FeedbackVerdictSubmit, VerdictGroup
from app.services import review_loop_service as svc

_RUN = "RVW001"
_GROUPS = [VerdictGroup(lint="junk_or_header_row", shape=None, title="t", count=3),
           VerdictGroup(lint="stage6_render_warnings", shape="no_teaching_content", title="t", count=1)]


def _verdict(lint, shape=None, verdict=ReviewVerdict.FIXED):
    return FeedbackVerdictSubmit(lint=lint, shape=shape, verdict=verdict)


def test_check_verdicts_takes_each_groups_count_from_the_server():
    verdicts = [_verdict("junk_or_header_row"),
                _verdict("stage6_render_warnings", "no_teaching_content", ReviewVerdict.CANT_TELL)]
    counts = svc.check_verdicts(verdicts, _GROUPS)
    assert counts == {("junk_or_header_row", None): 3, ("stage6_render_warnings", "no_teaching_content"): 1}
    rows = svc.verdict_rows(7, _RUN, verdicts, counts)
    assert [(r.feedback_id, r.lint, r.shape, r.finding_count, r.verdict) for r in rows] == [
        (7, "junk_or_header_row", None, 3, "fixed"),
        (7, "stage6_render_warnings", "no_teaching_content", 1, "cant_tell")]


@pytest.mark.parametrize("verdicts", [
    [_verdict("dead_sections")],  # a lint the run does not show
    [_verdict("stage6_render_warnings")],  # a shown lint, but not under this shape
    [_verdict("junk_or_header_row"), _verdict("junk_or_header_row", verdict=ReviewVerdict.NOT_A_PROBLEM)],
])
def test_check_verdicts_refuses_an_unshown_or_repeated_group(verdicts):
    with pytest.raises(HTTPException) as e:
        svc.check_verdicts(verdicts, _GROUPS)
    assert e.value.status_code == 422


def test_check_verdicts_refuses_everything_when_the_run_shows_nothing():
    with pytest.raises(HTTPException):
        svc.check_verdicts([_verdict("junk_or_header_row")], [])


def _docx(*paragraphs):
    from docx import Document

    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture
def store(tmp_path, monkeypatch):
    from app.storage.local_storage import LocalRunStorage

    store = LocalRunStorage(str(tmp_path))
    monkeypatch.setattr(svc, "get_storage", lambda: store)
    return store


_ENTRIES = ("Fellowship in Quorvane studies, Northfield Institute, 2011-2013",
            "Brennic Foundation Award for Saltwick lattice modelling, 2015")


def test_record_corrected_docx_maps_changes_to_stage4_entries(db, store):
    store.put_file(_RUN, f"outputs/{_RUN}_wcm.docx", _docx(*_ENTRIES))
    stage4 = {"entries": [{"element_idx_start": 12, "taxonomy_code": "H", "text": _ENTRIES[1]}]}
    store.put_file(_RUN, f"outputs/{_RUN}_fields.json", json.dumps(stage4).encode())

    assert svc.record_corrected_docx(db, _RUN, _docx(_ENTRIES[0])) == 1

    report = json.loads(store.get_file(_RUN, svc.corrected_diff_key(_RUN)))
    assert [(c["change_type"], c["element_idx"]) for c in report["changes"]] == [("deleted", 12)]


def test_record_corrected_docx_diffs_without_entries_when_stage4_is_unreadable(db, store):
    store.put_file(_RUN, f"outputs/{_RUN}_wcm.docx", _docx(*_ENTRIES))
    store.put_file(_RUN, f"outputs/{_RUN}_fields.json", b"{not json")
    assert svc.record_corrected_docx(db, _RUN, _docx(_ENTRIES[0])) == 1
    report = json.loads(store.get_file(_RUN, svc.corrected_diff_key(_RUN)))
    assert report["changes"][0]["element_idx"] is None


def test_record_corrected_docx_stores_nothing_when_the_copy_cannot_be_read(db, store):
    """A zip that passes the magic check but whose document part is not XML."""
    store.put_file(_RUN, f"outputs/{_RUN}_wcm.docx", _docx(*_ENTRIES))
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(_docx("x"))) as src, zipfile.ZipFile(buf, "w") as dst:
        for item in src.infolist():
            data = b"<not xml" if item.filename == "word/document.xml" else src.read(item)
            dst.writestr(item, data)
    with pytest.raises(HTTPException) as e:
        svc.record_corrected_docx(db, _RUN, buf.getvalue())
    assert e.value.status_code == 400
    assert store.list_files(_RUN, svc.CORRECTED_PREFIX) == []


@pytest.mark.parametrize("changes, line", [(0, "0 changes recorded"), (1, "1 change recorded"),
                                           (7, "7 changes recorded")])
def test_changes_summary(changes, line):
    assert svc.changes_summary(changes) == line
