"""Post-render repairs: fix in the finished WCM document what a Run Doctor
lint can locate and the fix for which is unambiguous (#1389, Phase A).

    protected_data.py  remove protected personal data the render still carries

A repair runs at the end of stage 6 (`stage_6_word_template.run_stage6`,
behind its `repair_protected_data` flag), so both drivers get it and the
document a driver uploads is the repaired one. It re-runs the lint it acts
on and reports what is left; it never claims a fix the lint does not see.

Dependencies run one way: this package may import `unified_pipeline.doctor`
(the lint vocabulary it acts on) and `unified_pipeline.stage6` (the policy
those lints share), never `stage_6_word_template`, `run_doctor`,
`quality_score` or anything under `web_interface/`. `run_stage6` imports it
lazily, at call time, so the edge from `stage_6_word_template` is not a
load-time cycle through `doctor/lints/extraction.py`, which imports it.
"""
