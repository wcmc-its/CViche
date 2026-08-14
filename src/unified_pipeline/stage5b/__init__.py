"""Stage 5b submodules, split by separation of concerns (#523).

`stage_5b_institution_enrichment.py` remains the entry point and the public
import surface; these modules hold the implementation it calls into.

    normalize.py  institution-name normalization and location formatting
    cache.py      the on-disk lookup cache, its key scheme, and its mutable state
    lookup.py     LLM prompt construction and the batched lookup call

Dependencies run one way and must keep doing so: nothing here may import
`stage_5b_institution_enrichment` -- that module imports these, so a back-edge
is an import cycle and fails at load, not at run time.
"""
