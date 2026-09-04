"""Presentation cleanup: markers a value must not carry into a Word run.

Rendering concerns, not domain ones. Neither function decides what a value
*is*; both remove markers that would render wrongly on the page -- stage
5c's markdown, and the separators the readers use internally to flatten a
table row. They change when what the writer emits changes, not when a CV
spelling does, which is why they no longer sit next to the author and
institution rules.

Split out of the former ``text.py``. Names keep their leading underscore for
now. Renaming and relocating in one change would make a failure impossible
to attribute to either.
"""
import re


def _strip_markdown_for_word(text: str, preserve_newlines: bool = False) -> str:
    r"""Strip the SUPPORTED markdown subset from stage-5c text for a Word run.

    The name says "markdown"; the contract is narrower on purpose. The input
    is not arbitrary markdown, it is the small set of markers stage 5c
    emits, and everything outside that set is passed through as written --
    the safe direction, since an unrecognised construct then renders as
    literal characters instead of having content cut out of it.

    Stripped:
    - `**bold**` markers, anywhere in a line ("**Course:** X" -> "Course: X")
    - a `- ` list prefix at the start of a line (after that line is
      stripped, so an indented sub-bullet is flattened to the same level,
      not preserved as one), and stage 5c's `Notes:` structural marker
      immediately after it
    - leading/trailing whitespace on every line, and empty lines
    Lines are then rejoined with "; " -- or with newlines when
    `preserve_newlines` is set.

    Deliberately NOT handled, and passed through unchanged:
    - links, `[text](url)`
    - emphasis, `*text*` and `_text_`
    - inline code, `` `x` ``, and fenced code blocks
    - escapes: `\*\*not bold\*\*` keeps its backslashes and its asterisks
    - ATX headers, `# Header`. The previous docstring claimed these were
      stripped; the implementation has never touched them.
    - ordered list markers, `1. item`
    - a bullet written `-item`, with no space after the dash

    Pinned as a contract by
    `src/unified_pipeline/tests/test_stage6_markdown_subset_contract.py`.

    Args:
        preserve_newlines: If True, join lines with newlines instead of
            semicolons. Use for teaching entries where each line becomes
            a separate bullet (main entry + notes).
    """
    if not text:
        return ''

    # Remove bold markers
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)

    # Handle sub-bullets - strip the dash prefix
    lines = text.split('\n')
    result_parts = []
    for line in lines:
        line = line.strip()
        if line.startswith('- '):
            # Sub-bullet, strip the prefix and any "Notes: " structural marker from Stage 5c
            line = line[2:].strip()
            if line.startswith('Notes: '):
                line = line[7:]
            elif line.startswith('Notes:'):
                line = line[6:].strip()
            result_parts.append(line)
        elif line:
            result_parts.append(line)

    # Join with appropriate separator
    if preserve_newlines:
        return '\n'.join(result_parts)
    elif len(result_parts) > 1:
        # Multiple lines - join with semicolon for compactness
        result = '; '.join(result_parts)
    else:
        result = result_parts[0] if result_parts else ''

    return result


def _clean_inline_tabs(text: str) -> str:
    """Render the pipeline's internal cell separators readably.

    Two separators are artifacts of how the readers flatten a source CV, and
    neither belongs in a rendered Word document:

    - " | " joins the cells of a table row (``docx_structure_extractor``). Those
      cells are columns, not a label/value pair, so they rejoin with " — ".
      A row whose cells are all empty is a blank template row carrying no
      information; it collapses to "" so callers can drop it.
    - "\\t" joins the "Label\\tValue" pairs of the WCM template's tables. A raw
      tab renders ragged against Word's default tab stops, so the first becomes
      ": " (label: value) and any further tabs become " — ".

    UPSTREAM CONTRACT -- what each caller has already decided before it gets
    here. It is not one contract but two, and neither is "a row can never
    arrive". The six call sites, each re-read against its own callers:

    - ``stage_6_word_template.py:1137`` ``_insert_bulleted_entry`` -- a
      shared bullet helper with eight callers in three sections, so the
      contract is the section's, not the helper's. ``teaching.py`` (six of
      the eight) renders every entry as a bullet because the WCM template
      has no teaching table to route to. ``clinical_practice.py:487`` (via
      ``_insert_multiline_as_bullets``) and ``passthrough.py:279`` are the
      other kind: an explicit fallback taken only when the section's table
      was not found in the template or failed its header validation.
    - ``stage_6_word_template.py:2084`` ``_insert_reconsidered_segment`` --
      two callers. ``:1925`` re-routes an LLM-reclassified appendix
      segment. ``:2320`` is ``_recover_unrendered_records``, which passes
      multi-cell rows here DELIBERATELY: it skips a multi-column row only
      when the row carries no digit and its cells are majority
      column-label words (``_is_column_header_row``), and keeps "a dateless
      multi-cell row of real content (committee membership: 'Member |
      Committee on X | Organization')". That row is flattened here on
      purpose, because the alternative is leaving the record unrendered.
    - ``stage_6_word_template.py:2386`` ``_unconsumed_personal_data_batch``
      -- A entries no Personal Data slot consumed. Its PII scan reads the
      RAW text, precisely because this call destroys the fragment boundaries
      that scan splits on.
    - ``sections/appendix.py:70`` -- the appendix, which by construction
      holds only entries no section renderer claimed.
    - ``sections/mentoring.py:203`` ``_insert_mentoring_summaries`` -- three
      feeds, screened differently. ``n3a_summaries``/``n3b_summaries`` are
      what ``_is_mentee_record()`` rejected, every mentee record having gone
      to ``_create_mentee_table_with_spacing`` instead (``:112-115``). The
      third feed, ``n4_entries`` (``:194``), is NOT screened by
      ``_is_mentee_record`` and never passes through that partition: N4
      mentoring outcomes have no table anywhere in the WCM template, so
      there is nothing to screen them against.
    - ``sections/researcher_profiles.py:62`` -- S0 identifier lines (ORCID,
      Google Scholar). The template has neither a heading nor a table for
      them, so every S0 entry is a bullet.

    So the residual-text framing holds only for the table-backed sections
    (clinical practice, passthrough, the mentee tables). For teaching, N4,
    S0 and the appendix there is no table in the template to route to, and a
    row-shaped record reaches this function by construction rather than
    through a routing failure. Either way nothing is enforced by a type or
    an assertion: a genuine table row is flattened into one
    "cell — cell — cell" line, with no warning. That cost is pinned rather
    than left to be discovered in a delivered document --
    ``test_cell_separators.py`` covers the residual-text path and
    ``test_stage6_cell_separator_contract.py`` covers what the flattening
    fallback actually produces when a row does reach it.

    ponytail: the name says "tabs" but it now handles both separators. Kept as-is
    so this change does not collide with the three bullet call sites that #254
    also edits; rename to _clean_cell_separators once that has landed.
    """
    if not text:
        return text
    if "|" in text:
        text = " — ".join(c.strip() for c in text.split("|") if c.strip())
    if "\t" not in text:
        return text
    parts = [p.strip() for p in text.split("\t") if p.strip()]
    if len(parts) <= 1:
        return text.replace("\t", " ").strip()
    return parts[0] + ": " + " — ".join(parts[1:])
