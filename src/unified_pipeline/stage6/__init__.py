"""Stage 6 submodules, split by separation of concerns (#398).

`stage_6_word_template.py` remains the entry point and the public import
surface; these packages hold the implementation it calls into.

    formatting/     applying visual formatting, and rendering a value as text
    parsing/        reading structure out of raw entry text
    normalization/  canonicalising extracted values
    resolution/     deciding what an incomplete or ambiguous record should say
    sorting/        ordering records within a section              (planned)

`resolution/` is a sixth layer, added because the original five did not cover
inference. The other four all assume the answer is already on the record --
parsing reads it, normalization canonicalises it, formatting renders it.
Resolution handles the case where the source CV never said it.

Planned packages are listed so the intended boundaries are visible, not to
reserve empty directories -- each is created by the PR that populates it.
"""
