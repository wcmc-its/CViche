"""Stage 6 submodules, split by separation of concerns (#398).

`stage_6_word_template.py` remains the entry point and the public import
surface; these packages hold the implementation it calls into.

    formatting/     applying visual formatting to python-docx objects
    parsing/        reading structure out of raw entry text        (planned)
    normalization/  canonicalising extracted values                (planned)
    sorting/        ordering records within a section              (planned)

Planned packages are listed so the intended boundaries are visible, not to
reserve empty directories -- each is created by the PR that populates it.
"""
