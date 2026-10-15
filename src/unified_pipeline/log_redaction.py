"""The redaction policy for exception text in log lines (#639).

``str(exc)`` is not safe to log at WARNING or above. A provider error can
echo a response-body fragment, a request URL, or an ID, and an error raised
while handling a CV can quote the CV text itself (CODING_STANDARDS §4.7).
The policy:

- A WARNING-or-above log line, or an exception message that a log line or a
  stored record will carry, names the exception with
  ``redacted_error_label``: its class, plus a structured detail the caller
  has checked is safe (a botocore error code, a YAML line/column).
- The raw ``str(exc)`` goes to DEBUG only, on its own line.

Imports nothing: a caller extracts its own safe detail from the exception
types it knows about, so this module needs neither botocore nor yaml.
"""


def redacted_error_label(exc: BaseException, safe_detail: str = "") -> str:
    """The class name of ``exc``, with ``safe_detail`` in brackets when given:
    ``"ClientError[ThrottlingException]"``, ``"ParserError[line 3, column 5]"``,
    ``"ReadTimeoutError"``. Never includes ``str(exc)``.
    """
    label = type(exc).__name__
    if safe_detail:
        label = f"{label}[{safe_detail}]"
    return label
