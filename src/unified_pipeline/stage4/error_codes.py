"""The error strings stage 4 writes onto an entry, defined once.

`extraction_error` and `llm_recovery_error` are read by more than the module
that writes them: the quality score and the doctor count entries by them
(`quality_score.stage4_group_failures`, #1174). They import this leaf rather
than `extraction`, which pulls in the LLM client and botocore (about 225 ms at
import) that a scorer run does not otherwise load.

Imports nothing, so any stage-4 module and the scorer can import it without a
cycle.
"""

# Stable error-code strings for extraction_error / llm_recovery_error fields.
# A caller can branch on these programmatically; str(exception) is for the
# log line only (via logger.exception, which records the full traceback),
# never for a field another stage or the frontend reads.
LLM_RESPONSE_INVALID = "llm_response_invalid"
LLM_TIMEOUT = "llm_timeout"
LLM_PROVIDER_ERROR = "llm_provider_error"

#: The one `extraction_error` that is not a failed LLM call. The call for the
#: entry's taxonomy group succeeded; its reply simply held no item for this
#: entry. Every other value means the whole group's call failed.
NO_MATCHING_EXTRACTION = "No matching extraction in LLM response"
