"""Where the artifacts record that a call was served by the content-filter
fallback model (#1174).

A Sonnet-5-family call that ends content_filtered is retried down an ordered
chain of fallback models (`llm.bedrock.CONTENT_FILTER_FALLBACK_MODELS`) until
one does not end content_filtered. The call succeeds, so nothing in the run reads as a failure; these keys are the
write-only record that lets the doctor tell such a call from an ordinary one;
the quality score does not cap on it (#1174). The writers (`llm.bedrock`,
stage 4, stage 4.5) and the reader (`quality_score.llm_fallback_served`, which
the doctor's lint calls) all import the names from here.

A leaf: imports nothing, so the scorer can read the names without loading the
LLM client and botocore.
"""

#: Set on a `call_llm` result when a fallback served the call; the value is
#: the id of the fallback model that answered, whichever link of the chain. Absent on every other result.
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

#: Suffix of a prompt log's response record (`core.prompt_logger`). Every
#: `call_llm` writes one, with FALLBACK_SERVED_KEY under its "response" when
#: a fallback served the call: the one record that covers every stage.
PROMPT_LOG_RESPONSE_SUFFIX = "_RESPONSE.json"

#: The stage-4.5 calls, as `call` values in the records above.
STAGE4_5_CALL_M1_SCORE = "m1_relevance_score"
STAGE4_5_CALL_SUMMARY = "summary_generation"
