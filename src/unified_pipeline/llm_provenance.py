"""Where the artifacts record that a call was served by the content-filter
fallback model (#1174).

A Sonnet-5-family call that ends content_filtered is retried once on a
fallback model (`llm.bedrock.CONTENT_FILTER_FALLBACK_MODEL`). The call
succeeds, so nothing in the run reads as a failure; these keys are the
write-only record that lets the doctor and the quality score tell such a call
from an ordinary one. The writers (`llm.bedrock`, stage 4, stage 4.5) and the
readers (`quality_score`, the doctor) all import the names from here.

A leaf: imports nothing, so the scorer can read the names without loading the
LLM client and botocore.
"""

#: Set on a `call_llm` result when the fallback served the call; the value is
#: the model id that answered. Absent on every other result.
FALLBACK_SERVED_KEY = "served_by_fallback_model"

#: On a stage-4 entry whose taxonomy group's extraction call the fallback
#: served: the model id that answered.
STAGE4_ENTRY_FALLBACK_KEY = "llm_fallback_model"

#: Top level of the stage-4.5 artifact: one `{"call": ..., "model": ...}` per
#: fallback-served call. Absent when none was.
STAGE4_5_FALLBACK_CALLS_KEY = "llm_fallback_calls"

#: Top level of the stage-4.5 artifact: one `{"call", "exception_type",
#: "stop_reason", "message"}` per call that raised on every model tried, so
#: stage 4.5 went on without its answer (#1174). Absent when every call answered.
STAGE4_5_CALL_FAILURES_KEY = "llm_call_failures"

#: The stage-4.5 calls, as `call` values in the records above.
STAGE4_5_CALL_M1_SCORE = "m1_relevance_score"
STAGE4_5_CALL_SUMMARY = "summary_generation"
