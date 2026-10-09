"""app/services/review_loop_service.py (#1587): the corrected-copy diff. The
route over it is in test_feedback_notification.py. Every document here is
synthetic."""
import io
import json
import zipfile

import pytest
from fastapi import HTTPException

from app.services import review_loop_service as svc

_RUN = "RVW001"


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
