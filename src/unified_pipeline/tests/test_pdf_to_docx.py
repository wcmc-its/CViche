"""Tests for core/pdf_to_docx.py and scripts/pdf_to_docx.py (#806).

No PDF writer is a dependency, so fixtures are built from hand-written PDF
bytes (`_make_pdf`): base-14 Helvetica / Helvetica-Bold, optional 1x1 image.
"""

import hashlib
import struct
import subprocess
import sys
from pathlib import Path

import pdfplumber
import pytest
from docx import Document

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.core.docx_structure_extractor import extract_unified_elements
from unified_pipeline.core.pdf_to_docx import (
    _is_bold,
    _Para,
    _Run,
    _write_docx,
    convert_pdf_to_docx,
)
from unified_pipeline.run_doctor import iter_header_candidates

_REPO = Path(__file__).resolve().parents[3]
_CLI = _REPO / "scripts" / "pdf_to_docx.py"

_LONG = ("Completed the doctoral programme and residency with a thesis on the "
         "long term outcomes of")
_LONG_TAIL = "surgical patients in the region."


_PDF_PAD = bytes.fromhex("28BF4E5E4E758A4164004E56FFFA01082E2E00B6D0683E802F0CA9FE6453697A")


def _rc4(key: bytes, data: bytes) -> bytes:
    box, j = list(range(256)), 0
    for i in range(256):
        j = (j + box[i] + key[i % len(key)]) % 256
        box[i], box[j] = box[j], box[i]
    out, i, j = bytearray(), 0, 0
    for byte in data:
        i = (i + 1) % 256
        j = (j + box[i]) % 256
        box[i], box[j] = box[j], box[i]
        out.append(byte ^ box[(box[i] + box[j]) % 256])
    return bytes(out)


def _encrypt_dict(user_password: str, obj_num: int) -> tuple[bytes, bytes]:
    """PDF standard security handler, revision 2 (RC4-40): an /Encrypt object
    body and the trailer entries that make pdfminer demand `user_password`."""
    pad = lambda pw: (pw.encode() + _PDF_PAD)[:32]  # noqa: E731
    owner = _rc4(hashlib.md5(pad("owner-pw")).digest()[:5], pad(user_password))
    perms, doc_id = -4, b"0123456789abcdef"
    key = hashlib.md5(pad(user_password) + owner + struct.pack("<i", perms) + doc_id).digest()[:5]
    user = _rc4(key, _PDF_PAD)
    body = (f"<< /Filter /Standard /V 1 /R 2 /P {perms} /O <{owner.hex()}> "
            f"/U <{user.hex()}> >>").encode()
    return body, f"/Encrypt {obj_num} 0 R /ID [<{doc_id.hex()}> <{doc_id.hex()}>]".encode()


def _make_pdf(pages, image_pages=(), user_password=None) -> bytes:
    """pages: list of lists of (bold, size, x, y, text). image_pages: 0-based
    indexes of pages that also carry a 1x1 image."""
    objs: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        4: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
        5: (b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
            b"/ColorSpace /DeviceGray /BitsPerComponent 8 /Length 1 >>\n"
            b"stream\n\x80\nendstream"),
    }
    kids = []
    for i, ops in enumerate(pages):
        page_id, content_id = 6 + 2 * i, 7 + 2 * i
        kids.append(f"{page_id} 0 R")
        body = "".join(
            f"BT /F{2 if bold else 1} {size} Tf {x} {y} Td ({text}) Tj ET\n"
            for bold, size, x, y, text in ops)
        if i in image_pages:
            body += "q 200 0 0 200 100 300 cm /Im0 Do Q\n"
        stream = body.encode("latin-1")
        objs[content_id] = (b"<< /Length %d >>\nstream\n" % len(stream)
                            + stream + b"\nendstream")
        objs[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {content_id} 0 R /Resources << /Font << /F1 3 0 R "
            f"/F2 4 0 R >> /XObject << /Im0 5 0 R >> >> >>").encode()
    trailer_extra = b""
    if user_password is not None:
        # xref subsections are contiguous, so the object takes the next number.
        objs[len(objs) + 2], trailer_extra = _encrypt_dict(user_password, len(objs) + 2)
    objs[2] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>".encode()
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += b"%d 0 obj\n" % num + objs[num] + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for num in sorted(objs):
        out += b"%010d 00000 n \n" % offsets[num]
    out += b"trailer\n<< /Size %d /Root 1 0 R %s >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs) + 1, trailer_extra, xref)
    return bytes(out)


def _convert(tmp_path, pages, image_pages=(), name="cv"):
    pdf, docx = tmp_path / f"{name}.pdf", tmp_path / f"{name}.docx"
    pdf.write_bytes(_make_pdf(pages, image_pages))
    report = convert_pdf_to_docx(pdf, docx)
    return report, Document(str(docx))


def _cv_page():
    """Header, a two-line wrapped paragraph, a gap, a column-aligned row."""
    return [
        (True, 12, 72, 700, "EDUCATION"),
        (False, 10, 72, 680, _LONG),
        (False, 10, 72, 668, _LONG_TAIL),
        (False, 10, 72, 630, "2019"),
        (False, 10, 260, 630, "Award"),
    ]


def test_bold_header_is_a_bold_run_with_size(tmp_path):
    _, doc = _convert(tmp_path, [_cv_page()])
    first = doc.paragraphs[0]
    assert first.text == "EDUCATION"
    assert all(r.bold for r in first.runs)
    assert first.runs[0].font.size.pt == 12
    assert not doc.paragraphs[1].runs[0].bold


def test_wrapped_lines_merge_into_one_paragraph(tmp_path):
    _, doc = _convert(tmp_path, [_cv_page()])
    assert doc.paragraphs[1].text == f"{_LONG} {_LONG_TAIL}"


def test_vertical_gap_emits_blank_paragraph(tmp_path):
    report, doc = _convert(tmp_path, [_cv_page()])
    assert [p.text for p in doc.paragraphs][2] == ""
    assert report.blank_paragraphs == 1
    assert report.paragraphs == 4


def test_column_gap_becomes_tab(tmp_path):
    _, doc = _convert(tmp_path, [_cv_page()])
    assert doc.paragraphs[3].text == "2019\tAward"


def test_normal_word_gap_stays_a_space(tmp_path):
    _, doc = _convert(tmp_path, [[(False, 10, 72, 700, "Assistant Professor")]])
    assert doc.paragraphs[0].text == "Assistant Professor"


def test_short_lines_do_not_merge(tmp_path):
    page = [(False, 10, 72, 700, _LONG), (False, 10, 72, 688, "Short entry"),
            (False, 10, 72, 676, "Another short entry")]
    _, doc = _convert(tmp_path, [page])
    assert [p.text for p in doc.paragraphs] == [f"{_LONG} Short entry", "Another short entry"]


def _furniture_pages():
    pages = [
        [(False, 9, 72, 765, "Confidential CV"), (False, 10, 72, 600, f"Body {n}"),
         (False, 9, 280, 25, f"Page {n} of 3")]
        for n in (1, 2, 3)]
    pages[0].append((False, 10, 72, 700, _LONG))  # sets the right margin
    return pages


def test_running_header_and_page_number_removed(tmp_path):
    report, doc = _convert(tmp_path, _furniture_pages())
    assert [p.text for p in doc.paragraphs if p.text] == [_LONG, "Body 1", "Body 2", "Body 3"]
    assert report.pages == 3


def test_repeated_body_line_is_kept(tmp_path):
    pages = [[(False, 10, 72, 400, "Present")] for _ in range(3)]
    _, doc = _convert(tmp_path, pages)
    assert " ".join(p.text for p in doc.paragraphs).split() == ["Present"] * 3


def test_single_page_is_never_treated_as_furniture(tmp_path):
    _, doc = _convert(tmp_path, [[(False, 9, 72, 765, "Only header")]])
    assert doc.paragraphs[0].text == "Only header"


def test_image_only_page_reported(tmp_path):
    pages = [[(False, 10, 72, 700, "This first page carries plenty of readable text.")], []]
    report, _ = _convert(tmp_path, pages, image_pages=(1,))
    assert report.image_only_pages == [2]
    assert report.chars > 0


def test_textless_page_without_image_is_not_image_only(tmp_path):
    pages = [[(False, 10, 72, 700, "This first page carries plenty of readable text.")], []]
    report, _ = _convert(tmp_path, pages)
    assert report.image_only_pages == []


def test_control_characters_do_not_break_the_docx(tmp_path):
    # pdfminer usually maps such glyphs to "(cid:N)"; a font ToUnicode map can
    # still yield a raw control char, which python-docx rejects.
    out = tmp_path / "c.docx"
    _write_docx([_Para(runs=[_Run("ab\x01cd\x0c", False, 10.0)])], out)
    assert Document(str(out)).paragraphs[0].text == "abcd"


def test_unreadable_pdf_raises(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf at all")
    with pytest.raises(Exception) as excinfo:
        convert_pdf_to_docx(bad, tmp_path / "bad.docx")
    assert "encrypted" not in str(excinfo.value)
    assert not (tmp_path / "bad.docx").exists()


def test_wire_reader_and_doctor_see_converted_file(tmp_path):
    """extract_unified_elements + iter_header_candidates on the converted docx:
    the bold ALL-CAPS header is a candidate and elements keep source order."""
    page = [
        (True, 12, 72, 720, "EDUCATION"),
        (False, 10, 72, 700, "Doctor of Medicine, Example University"),
        (False, 10, 72, 640, "2019"),
        (False, 10, 260, 640, "Award"),
        (True, 12, 72, 580, "PUBLICATIONS"),
        (False, 10, 72, 560, "First paper title"),
    ]
    pdf, docx = tmp_path / "w.pdf", tmp_path / "w.docx"
    pdf.write_bytes(_make_pdf([page]))
    convert_pdf_to_docx(pdf, docx)
    assert iter_header_candidates(str(docx)) == ["EDUCATION", "PUBLICATIONS"]
    texts = [e.get("text") for e in extract_unified_elements(str(docx))["elements"]]
    order = [t for t in texts if t]
    assert order.index("EDUCATION") < order.index("Doctor of Medicine, Example University") \
        < order.index("PUBLICATIONS") < order.index("First paper title")
    assert "" in texts  # blank paragraphs survive into the element stream


def _texts(doc):
    return [p.text for p in doc.paragraphs]


def _width(tmp_path, text, size=10):
    """Rendered width of `text` in base-14 Helvetica, measured by pdfplumber."""
    probe = tmp_path / "probe.pdf"
    probe.write_bytes(_make_pdf([[(False, size, 0, 700, text)]]))
    with pdfplumber.open(probe) as pdf:
        return max(w["x1"] for w in pdf.pages[0].extract_words())


def test_superscript_stays_on_its_line(tmp_path):
    page = [(False, 10, 72, 700, "Figure"), (False, 7, 104, 703, "1")]
    _, doc = _convert(tmp_path, [page])
    assert _texts(doc) == ["Figure 1"]


def test_mixed_sizes_on_one_baseline_are_one_line_of_two_runs(tmp_path):
    page = [(False, 14, 72, 684, "Big"), (False, 10, 100, 684, "small")]
    _, doc = _convert(tmp_path, [page])
    assert _texts(doc) == ["Big small"]
    assert [(r.text, r.font.size.pt) for r in doc.paragraphs[0].runs] == [("Big ", 14), ("small", 10)]


def test_runs_split_on_weight_and_merge_within_a_weight(tmp_path):
    page = [(True, 10, 72, 700, "Name:"), (False, 10, 110, 700, "value"),
            (False, 10, 140, 700, "more")]
    _, doc = _convert(tmp_path, [page])
    assert [(r.text, r.bold) for r in doc.paragraphs[0].runs] == [("Name: ", True), ("value more", False)]


def test_words_of_mixed_size_are_ordered_left_to_right(tmp_path):
    """Same baseline, tops 10pt apart, right-hand word drawn first: only a
    bottom-keyed line grouping keeps them on one line, and only a sort by x
    puts them in reading order."""
    page = [(False, 20, 110, 684, "Big"), (False, 10, 72, 684, "small")]
    _, doc = _convert(tmp_path, [page])
    assert _texts(doc) == ["small Big"]


def test_words_are_ordered_left_to_right(tmp_path):
    page = [(False, 10, 260, 630, "Award"), (False, 10, 72, 630, "2019")]
    _, doc = _convert(tmp_path, [page])
    assert _texts(doc) == ["2019\tAward"]


def test_bold_line_does_not_absorb_a_plain_continuation(tmp_path):
    page = [(True, 10, 72, 700, _LONG), (False, 10, 72, 688, "tail")]
    _, doc = _convert(tmp_path, [page])
    assert _texts(doc) == [_LONG, "tail"]


def test_mixed_weight_line_does_not_merge_with_an_all_bold_line(tmp_path):
    page = [(True, 10, 72, 700, "Lead:"), (False, 10, 104, 700, _LONG), (True, 10, 72, 688, "Tail")]
    _, doc = _convert(tmp_path, [page])
    assert _texts(doc) == [f"Lead: {_LONG}", "Tail"]


def test_tabbed_line_is_never_merged_with_its_neighbours(tmp_path):
    before = [(False, 10, 72, 700, _LONG), (False, 10, 72, 688, "2019"),
              (False, 10, 260, 688, "Award")]
    after = [(False, 10, 72, 700, "Label"), (False, 10, 200, 700, _LONG),
             (False, 10, 72, 688, "tail")]
    _, doc_before = _convert(tmp_path, [before], name="b")
    _, doc_after = _convert(tmp_path, [after], name="a")
    assert _texts(doc_before) == [_LONG, "2019\tAward"]
    assert _texts(doc_after) == [f"Label\t{_LONG}", "tail"]


def test_size_change_starts_a_new_paragraph(tmp_path):
    page = [(False, 10, 72, 700, _LONG), (False, 14, 72, 684, "Bigger tail")]
    _, doc = _convert(tmp_path, [page])
    assert _texts(doc) == [_LONG, "Bigger tail"]


def test_line_short_of_the_margin_is_not_a_wrapped_line(tmp_path):
    """The margin is measured from the leftmost line; P ends 36pt short of it,
    which is outside the slack (33pt) only if `left` is the real left edge."""
    right = _width(tmp_path, _LONG) + 72
    short = "short text"
    x = right - 36 - (_width(tmp_path, short) - 0)
    pages = [[(False, 10, x, 700, short), (False, 10, 72, 688, "next")],
             [(False, 10, 72, 700, _LONG)]]
    _, doc = _convert(tmp_path, pages)
    assert _texts(doc)[:2] == [short, "next"]


@pytest.mark.parametrize("fontname, bold", [
    ("ABCDEF+Arial-BoldMT", True), ("Foo-Black", True), ("FooHEAVY", True),
    ("ABCDEF+CMBX12", True), ("ABCDEF+CMSSBX10", True), ("CMBX10", True),
    ("NimbusRomNo9L-Medi", True), ("Foo-Semibold", True), ("Foo-DemiBold", True),
    ("Foo-Demi", True),
    ("Acmbx12", False), ("CMBXfoo", False), ("Arial-bxtra", False),
    ("HelveticaNeue-Medium", False), ("Arial-ItalicMT", False),
    ("TimesNewRomanPSMT", False), ("ABCDEF+CMSS10", False), ("CMR12", False),
    ("ABCDEF+CIDFont+F1", False)])
def test_bold_detection_by_font_name(fontname, bold):
    assert _is_bold(fontname) is bold


def test_furniture_needs_half_the_pages(tmp_path):
    pages = [[(False, 9, 72, 765, "Rare header" if n < 2 else "Other"),
              (False, 10, 72, 600, f"Body {n}")] for n in range(5)]
    _, doc = _convert(tmp_path, pages)
    assert _texts(doc).count("Rare header") == 2


def test_two_page_furniture_on_both_pages_is_removed(tmp_path):
    pages = [[(False, 9, 72, 765, "Both pages"), (False, 10, 72, 600, f"Body {n}")]
             for n in range(2)]
    _, doc = _convert(tmp_path, pages)
    assert "Both pages" not in " ".join(_texts(doc))


def test_furniture_matches_across_small_position_jitter(tmp_path):
    pages = [[(False, 9, 72, 765 - 0.4 * n, "Jittery header"), (False, 10, 72, 600, f"Body {n}")]
             for n in range(3)]
    _, doc = _convert(tmp_path, pages)
    assert "Jittery header" not in " ".join(_texts(doc))


def test_same_text_at_different_positions_is_not_furniture(tmp_path):
    pages = [[(False, 9, 72, 765 - 25 * n, "Moving title"), (False, 10, 72, 600, f"Body {n}")]
             for n in range(2)]
    _, doc = _convert(tmp_path, pages)
    assert " ".join(_texts(doc)).count("Moving title") == 2


def test_page_with_text_and_an_image_is_not_image_only(tmp_path):
    pages = [[(False, 10, 72, 700, "A page with real text and a logo image on it.")]]
    report, _ = _convert(tmp_path, pages, image_pages=(0,))
    assert report.image_only_pages == []


def test_page_break_is_not_a_vertical_gap(tmp_path):
    pages = [[(False, 10, 72, 750, "Top of page one")], [(False, 10, 72, 60, "Bottom of page two")]]
    report, _ = _convert(tmp_path, pages)
    assert report.blank_paragraphs == 0


@pytest.mark.parametrize("big_first", [True, False])
def test_gap_after_a_mixed_size_line_uses_its_largest_size_and_lowest_edge(tmp_path, big_first):
    """Gap 13.6pt: under the 14pt line's blank threshold (14) but over 10 (the
    smaller word's) and, measured from the smaller word's bottom, over 14.
    Either word order: the size is the max, not the first run's."""
    big, small = (False, 14, 72, 700, "Big"), (False, 10, 100, 700, "small")
    if not big_first:  # the small word now sits to the left
        big, small = (False, 14, 100, 700, "Big"), (False, 10, 72, 700, "small")
    page = [big, small, (False, 10, 72, 675.57, "next")]
    report, _ = _convert(tmp_path, [page])
    assert (report.paragraphs, report.blank_paragraphs) == (2, 0)


def test_gap_before_a_mixed_size_line_is_measured_from_its_highest_top(tmp_path):
    """Gap 8pt to the 14pt word's top; 11pt if measured to the 10pt word's."""
    page = [(False, 10, 72, 700, "plain"), (False, 14, 72, 678.83, "Big"),
            (False, 10, 100, 678.83, "small")]
    report, _ = _convert(tmp_path, [page])
    assert (report.paragraphs, report.blank_paragraphs) == (2, 0)


def _run_cli(*args):
    return subprocess.run([sys.executable, str(_CLI), *map(str, args)],
                          capture_output=True, text=True,
                          env={"PYTHONPATH": str(_SRC), "PATH": ""})


@pytest.mark.parametrize("text, breaks", [
    ("12. Second entry", True), ("3\\) Second entry", True), ("[4] Second entry", True),
    ("\\267 Second entry", True), ("- Second entry", True), ("\\261 Second entry", True),
    ("* Second entry", True),
    ("1.5 mg is the dose", False), ("-5 units of it", False), ("2019 was the year", False),
    ("1234. long number", False)])
def test_list_marker_line_starts_a_new_entry(tmp_path, text, breaks):
    page = [(False, 10, 72, 700, _LONG), (False, 10, 72, 688, text)]
    _, doc = _convert(tmp_path, [page])
    assert (len(doc.paragraphs) == 2) is breaks


def test_outdented_line_after_a_hanging_indent_is_a_new_entry(tmp_path):
    page = [(False, 10, 72, 700, _LONG), (False, 10, 90, 688, _LONG),
            (False, 10, 72, 676, "Next entry begins here")]
    _, doc = _convert(tmp_path, [page])
    assert _texts(doc) == [f"{_LONG} {_LONG}", "Next entry begins here"]


def _spaced_line(tmp_path, gaps, size=10):
    """A line of 'aa' words whose measured gaps are exactly `gaps`."""
    w = _width(tmp_path, "aa", size)
    xs = [72.0]
    for gap in gaps:
        xs.append(xs[-1] + w + gap)
    return [(False, size, x, 700, "aa") for x in xs]


def test_justified_line_with_uniformly_wide_gaps_has_no_tabs(tmp_path):
    _, doc = _convert(tmp_path, [_spaced_line(tmp_path, [20, 20, 20, 20])])
    assert _texts(doc) == ["aa aa aa aa aa"]


def test_justified_line_stays_mergeable(tmp_path):
    page = _spaced_line(tmp_path, [89, 89, 89, 89]) + [(False, 10, 72, 688, "tail")]
    _, doc = _convert(tmp_path, [page + [(False, 10, 72, 400, _LONG)]])
    assert _texts(doc)[0] == "aa aa aa aa aa tail"


def test_label_value_line_gets_a_tab_at_the_outlier_gap(tmp_path):
    _, doc = _convert(tmp_path, [_spaced_line(tmp_path, [4, 4, 4, 90])])
    assert _texts(doc) == ["aa aa aa aa\taa"]


def test_moderately_wider_gap_is_not_an_outlier(tmp_path):
    _, doc = _convert(tmp_path, [_spaced_line(tmp_path, [20, 20, 20, 30])])
    assert _texts(doc) == ["aa aa aa aa aa"]


def test_gap_over_twice_the_median_is_a_column_gap(tmp_path):
    _, doc = _convert(tmp_path, [_spaced_line(tmp_path, [20, 20, 20, 45])])
    assert _texts(doc) == ["aa aa aa aa\taa"]


def test_outlier_is_measured_against_the_median_not_the_smallest_gap(tmp_path):
    _, doc = _convert(tmp_path, [_spaced_line(tmp_path, [16, 50, 50, 50])])
    assert _texts(doc) == ["aa aa aa aa aa"]


def test_line_of_three_columns_keeps_both_tabs(tmp_path):
    _, doc = _convert(tmp_path, [_spaced_line(tmp_path, [40, 40])])
    assert _texts(doc) == ["aa\taa\taa"]


def test_tab_threshold_uses_the_size_of_the_word_before_the_gap(tmp_path):
    big = _width(tmp_path, "Big", 20)
    after_big = [(False, 20, 72, 700, "Big"), (False, 10, 72 + big + 20, 700, "small")]
    small = _width(tmp_path, "small", 10)
    before_big = [(False, 10, 72, 700, "small"), (False, 20, 72 + small + 20, 700, "Big")]
    _, doc_a = _convert(tmp_path, [after_big], name="a")
    _, doc_b = _convert(tmp_path, [before_big], name="b")
    assert _texts(doc_a) == ["Big small"]      # 20pt gap < 1.5 x 20pt
    assert _texts(doc_b) == ["small\tBig"]     # 20pt gap > 1.5 x 10pt


def test_password_protected_pdf_raises_a_clear_error(tmp_path):
    pdf = tmp_path / "locked.pdf"
    pdf.write_bytes(_make_pdf([_cv_page()], user_password="secret"))
    with pytest.raises(ValueError, match="encrypted/password-protected"):
        convert_pdf_to_docx(pdf, tmp_path / "locked.docx")


def test_cli_names_the_password_protected_cause(tmp_path):
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "locked.pdf").write_bytes(_make_pdf([_cv_page()], user_password="secret"))
    res = _run_cli(tmp_path / "in", tmp_path / "out")
    assert res.returncode == 1
    assert "conversion failed: locked.pdf (ValueError: encrypted/password-protected PDF)" in res.stderr
    assert res.stdout.strip() == "locked\t\t\t\t\t\terror"


def test_cli_one_tsv_line_per_file_and_continues_past_failure(tmp_path):
    src, out = tmp_path / "in", tmp_path / "out"
    src.mkdir()
    (src / "a.pdf").write_bytes(_make_pdf([_cv_page()]))
    (src / "b.pdf").write_bytes(b"garbage")
    (src / "c.pdf").write_bytes(_make_pdf([[(False, 10, 72, 700, "x" * 30)], []], image_pages=(1,)))
    (src / "notes.txt").write_text("ignored")
    res = _run_cli(src, out)
    rows = [line.split("\t") for line in res.stdout.splitlines()]
    assert res.returncode == 1
    assert [r[0] for r in rows] == ["a", "b", "c"]
    assert rows[0] == ["a", "1", "4", "1", "", str(len(_LONG) + len(_LONG_TAIL) + 1
                                                   + len("EDUCATION") + len("2019\tAward")), "ok"]
    assert rows[1] == ["b", "", "", "", "", "", "error"]
    assert rows[2][4] == "2" and rows[2][6] == "ok"
    assert all(len(r) == 7 for r in rows)
    assert "b.pdf" in res.stderr and "a.pdf" not in res.stderr
    assert (out / "a.docx").exists() and (out / "c.docx").exists()
    assert not (out / "b.docx").exists()


def test_cli_all_ok_exits_zero_for_single_file(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(_make_pdf([_cv_page()]))
    res = _run_cli(pdf, tmp_path / "out")
    assert res.returncode == 0 and res.stdout.count("\n") == 1
    assert res.stderr == ""
