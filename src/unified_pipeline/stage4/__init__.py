"""Stage 4 submodules, split by separation of concerns (#498).

`stage_4_field_extractor.py` remains the entry point and the public import
surface; these modules hold the implementation it calls into.

    schemas.py     per-code field schemas, descriptions, taxonomy labels
    extraction.py  LLM prompts, batch dispatch, and the recovery pass
    coercion.py    value coercion and normalisation of extracted fields
    owner_name.py  CV owner name extraction and owner-location inference
    error_codes.py the error strings written onto an entry; imports nothing,
                   so the scorer can read them without loading `extraction`

Dependencies run one way and must keep doing so. `extraction` imports from
`schemas`, `coercion`, `owner_name` and `error_codes`; those four import
nothing from each other. Nothing here may import `stage_4_field_extractor` -- that module imports
these, so a back-edge is an import cycle and fails at load, not at extraction.
"""
