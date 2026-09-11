"""Presentation cleanup: markers a value must not carry into a Word run.

Rendering concerns, not domain ones. Neither function decides what a value
*is*; both remove markers that would render wrongly on the page -- stage
5c's markdown, and the separators the readers use internally to flatten a
table row. They change when what the writer emits changes, not when a CV
spelling does, which is why they no longer sit next to the author and
institution rules.
"""
import re


def _strip_markdown_for_word(text: str, preserve_newlines: bool = False) -> str:
    r"""Strip the SUPPORTED markdown subset from stage-5c text for a Word run.

    The name says "markdown"; the contract is narrower on purpose. The input
    is not arbitrary markdown, it is the small set of markers stage 5c emits
    -- `**bold**`, a "- " list prefix and the `Notes:` marker after it -- and
    everything outside that set is passed through as written, the safe
    direction: an unrecognised construct then renders as literal characters
    instead of having content cut out of it. Lines are stripped, blank lines
    dropped, and the rest rejoined with "; " or with newlines.

    The whole subset, and every construct deliberately left alone, is pinned
    as a contract by
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

    The upstream routing contract is asserted rather than described here:
    ``test_stage6_cell_separator_contract.py`` enumerates the six call
    sites and what each has already decided, and pins what happens when a
    genuine table row reaches this function anyway -- it is flattened into
    one "cell — cell — cell" line, with no warning.

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
