"""Rendered output must never leak the readers' internal cell separators.

The docx readers flatten a table row by joining cells with " | " and the WCM
template's label/value pairs with tabs. Both used to reach the Appendix verbatim
(run EH4XXA: 49 raw pipes in the output docx, 0 in the source). Strings below are
taken from that run.
"""

from unified_pipeline.stage_6_word_template import _clean_inline_tabs


def test_pipe_joined_cells_render_as_em_dashes():
    assert _clean_inline_tabs("Teaching | 10% | Yes") == "Teaching — 10% — Yes"
    assert (
        _clean_inline_tabs("M.D. | University of Wroclaw Poland | 7/1987 - 7/1994 | 1994")
        == "M.D. — University of Wroclaw Poland — 7/1987 - 7/1994 — 1994"
    )


def test_blank_template_rows_collapse_to_empty():
    # Non-empty as raw text, but carries no information -> callers drop it.
    for blank in ("|  |  |", "|  |", "|", " | | "):
        assert _clean_inline_tabs(blank) == ""


def test_empty_leading_cells_are_dropped_not_the_content():
    # "| May 2019" is an orphaned date: the cell is empty, the date is real.
    assert _clean_inline_tabs("| May 2019") == "May 2019"
    assert _clean_inline_tabs("Total | 100% |") == "Total — 100%"


def test_tab_label_value_behaviour_is_unchanged():
    # Pre-existing contract of _clean_inline_tabs: first tab -> ": ", rest -> " — ".
    assert _clean_inline_tabs("Label\tValue") == "Label: Value"
    assert _clean_inline_tabs("Label\tA\tB") == "Label: A — B"
    assert _clean_inline_tabs("\tSolo") == "Solo"
    assert _clean_inline_tabs("no separators") == "no separators"
    assert _clean_inline_tabs("") == ""


def test_real_content_is_never_dropped():
    # The "every cell is a known template label" heuristic would have eaten this
    # real percent-effort row; rendering (not classifying) keeps it safe.
    assert _clean_inline_tabs("Total | 100% |") != ""
    assert "326634" in _clean_inline_tabs("New York | 326634 | 11/07/2023 | 07/07/2025")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("all checks passed")
