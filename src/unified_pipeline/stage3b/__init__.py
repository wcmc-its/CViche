"""Stage 3b submodules, split by separation of concerns (#522).

`stage_3b_entry_classifier.py` remains the entry point and the public import
surface; these modules hold the implementation it calls into.

    context.py   hierarchy -> taxonomy suggestions (TaxonomyContext, mapping index)
    io.py        stage artifact loading + input normalisation
    prompt.py    the classification system prompt and its taxonomy reference
    classify.py  the LLM classification passes (batch loop + post-passes)
    header_pin.py  a confidently mapped section header beats a model answer in a named set of confusions (#312)

Dependencies run one way and must keep doing so: `classify` imports from
`context`, `io` and `prompt`; `header_pin` imports only `context`; nothing here may import
`stage_3b_entry_classifier` -- that module imports these, so a back-edge is an
import cycle and fails at load.

`classify.py` deliberately holds every `call_llm` call site of the stage except
the flag-gated block-coherence closure in `run_stage_3b`, so a test that stubs
the LLM patches exactly one module attribute (`stage3b.classify.call_llm`).
Splitting call sites across modules is how a patch silently stops covering one
of them (#496).
"""
