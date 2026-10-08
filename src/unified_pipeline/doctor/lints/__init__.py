"""One module per domain a lint inspects.

    classification.py whether stage 3b's codes agree with their own headings
    enrichment.py     stage-5 citation enrichment and the cap-25 owner gate
    contact.py        office contact values vs the rendered Personal Data rows
    extraction.py     what stages 3b/4 pulled out, and what became of it
    formatting.py     what the stage-5 formatters wrote over stage 4's records
    render.py         defects visible in the rendered WCM document
    protected_data.py protected personal data reaching the rendered document
    runtime.py        whether the run itself completed, rather than what it made
    segmentation.py   how stages 1a/1b/2 cut the source into a hierarchy
    summary.py        claims the stage-4.5 research summary makes that the CV lacks

Every lint in `run_doctor.KNOWN_LINTS` now lives in one of these ten; what
stays in `run_doctor.py` is orchestration, artifact discovery and the report.
"""
