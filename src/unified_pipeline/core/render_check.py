"""Render-presence primitives shared between the offline run doctor
(unified_pipeline.run_doctor) and stage 6's own #221 recovery pass
(stage_6_word_template). Both decide whether an entry's text surfaced in the
rendered output by breaking it into fragments; keeping that one splitter here
stops the two copies drifting apart (they were verbatim-duplicated with no
enforced sync)."""

def entry_fragments(text: str | None) -> list[str]:
    """An entry's fragments: per line, per '|' cell, and per tab cell.

    `str(text or "")` rather than a bare split: entry['text'] is absent on some
    stage-5 records and None on others, and every one of the ten call sites
    this replaces coerced defensively. The annotation states the contract;
    the coercion is what survives a record that breaks it.
    """
    return [frag for line in str(text or "").split("\n")
            for cell in line.split("|") for frag in cell.split("\t")]


def entry_lines(text: str | None) -> list[str]:
    """An entry's non-empty lines, stripped.

    Newline only -- deliberately NOT the same split as `entry_fragments`. This
    is the exact idiom that was copy-pasted ten times across stage 6's section
    renderers; it lives here so the next change to it happens once.

    Note what it does NOT see, because callers have been surprised by this:
    only 612 of the corpus's 36,933 entries contain a newline at all, while
    8,672 are multi-part once '|' and tab are counted. So for 8,060 entries
    (21.8%, spread across all 100 corpus CVs) this returns a single blob for
    content that actually carries 2-8 separated parts.

    That is preserved behaviour, not an endorsement: consolidating first makes
    switching a call site to `entry_fragments` a one-line change instead of a
    ten-line one, and each such switch is a rendering change that needs its own
    corpus gate. Do not "fix" this function to split on more separators --
    every current caller would change output at once.
    """
    return [line.strip() for line in str(text or "").split("\n") if line.strip()]
