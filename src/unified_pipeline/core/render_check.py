"""Render-presence primitives shared between the offline run doctor
(unified_pipeline.run_doctor) and stage 6's own #221 recovery pass
(stage_6_word_template). Both decide whether an entry's text surfaced in the
rendered output by breaking it into fragments; keeping that one splitter here
stops the two copies drifting apart (they were verbatim-duplicated with no
enforced sync)."""

from typing import List


def entry_fragments(text) -> List[str]:
    """An entry's fragments: per line, per '|' cell, and per tab cell."""
    return [frag for line in str(text or "").split("\n")
            for cell in line.split("|") for frag in cell.split("\t")]
