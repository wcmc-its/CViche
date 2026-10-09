"""run_doctor internals, split by what each lint inspects (#493).

`run_doctor.py` remains the entry point, the artifact discovery layer and the
public import surface; these modules hold the lint rules it calls.

    shared.py      the finding vocabulary and shared text matching
    lints/         one module per domain the lints inspect
    PRECISION.md   each lint's last measured precision and recall (#819)
    COVERAGE.md    the coverage contract: defect type x level (#1588)
    mutation_harness.py / MUTATIONS.md
                   one defect injected into a synthetic run per mutation,
                   the catch rate per COVERAGE.md cell (#1588)
    precision.py   reads PRECISION.md's per-lint table for doctor.tsv, the
                   Teams card and #813's remediation gate
    docx_diff.py   a delivered docx vs the reviewer's corrected copy, typed
                   per change and written as doctor_vs_autopsy.py labels
                   (#1587); scripts/docx_review_diff.py is its CLI
"""
