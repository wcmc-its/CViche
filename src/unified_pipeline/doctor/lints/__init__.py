"""One module per domain a lint inspects.

    enrichment.py     stage-5 citation enrichment and the cap-25 owner gate
    extraction.py     what stages 3b/4 pulled out, and what became of it
    render.py         defects visible in the rendered WCM document
    runtime.py        whether the run itself completed, rather than what it made
    segmentation.py   how stages 1a/2 cut the source into a hierarchy

Every lint in `run_doctor.KNOWN_LINTS` now lives in one of these five; what
stays in `run_doctor.py` is orchestration, artifact discovery and the report.
"""
