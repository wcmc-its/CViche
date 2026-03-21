"""Maps Python exceptions to the CViche error taxonomy."""


def classify_error(exception: Exception) -> str:
    """Map an exception to a structured error type.

    Returns one of: llm_timeout, token_limit, parse_error,
    invalid_response, api_error, file_error, unknown.
    """
    name = type(exception).__name__
    msg = str(exception).lower()

    if 'timeout' in name.lower() or 'timeout' in msg:
        return 'llm_timeout'
    if 'token' in msg and ('limit' in msg or 'exceed' in msg or 'maximum' in msg):
        return 'token_limit'
    if any(x in msg for x in ['json', 'parse', 'decode', 'unexpected']):
        return 'parse_error'
    if 'invalid' in msg and 'response' in msg:
        return 'invalid_response'
    if any(x in name.lower() for x in ['api', 'http', 'connection', 'request']):
        return 'api_error'
    if any(x in name.lower() for x in ['file', 'io', 'permission', 'notfound']):
        return 'file_error'
    return 'unknown'
