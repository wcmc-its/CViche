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
- A traceback above DEBUG (``logger.exception``, ``exc_info=True``) keeps its
  frames and each exception's class but not its message, its ``__notes__``,
  or a SyntaxError's source excerpt -- for the whole ``__cause__`` /
  ``__context__`` chain and every member of an exception group. Both logging
  configs (``run_full_pipeline.configure_cli_logging`` and the backend's
  ``app.logging_config``) install ``RedactingFormatter`` for this, so a call
  site needs no change. At DEBUG the traceback renders in full.

Imports only the stdlib: a caller extracts its own safe detail from the
exception types it knows about, so this module needs neither botocore nor yaml.
"""

import logging
import textwrap
import traceback
from types import TracebackType

# The separators traceback.TracebackException.format() prints between the
# links of a chain, copied because the stdlib keeps them private.
_CAUSE_SEPARATOR = (
    "\nThe above exception was the direct cause of the following exception:\n\n")
_CONTEXT_SEPARATOR = (
    "\nDuring handling of the above exception, another exception occurred:\n\n")


def redacted_error_label(exc: BaseException, safe_detail: str = "") -> str:
    """The class name of ``exc``, with ``safe_detail`` in brackets when given:
    ``"ClientError[ThrottlingException]"``, ``"ParserError[line 3, column 5]"``,
    ``"ReadTimeoutError"``. Never includes ``str(exc)``.
    """
    label = type(exc).__name__
    if safe_detail:
        label = f"{label}[{safe_detail}]"
    return label


def _format_one(te: traceback.TracebackException) -> str:
    """One exception's frames and class, then its group members indented."""
    text = ""
    if te.stack:
        text = "Traceback (most recent call last):\n" + "".join(te.stack.format())
    text += f"{te.exc_type_str}\n"
    for index, member in enumerate(te.exceptions or (), start=1):
        text += f"  +---- {index} ----\n" + textwrap.indent(_format_chain(member), "  | ")
    return text


def _format_chain(te: traceback.TracebackException) -> str:
    """The chain oldest-first, as traceback.format_exception orders it."""
    text = _format_one(te)
    while True:
        if te.__cause__ is not None:
            te, separator = te.__cause__, _CAUSE_SEPARATOR
        elif te.__context__ is not None and not te.__suppress_context__:
            te, separator = te.__context__, _CONTEXT_SEPARATOR
        else:
            return text
        text = _format_one(te) + separator + text


def format_exception_redacted(
        exc_info: tuple[type[BaseException], BaseException, TracebackType | None]) -> str:
    """``logging.Formatter.formatException(exc_info)`` without any exception's
    message: every frame (file, line, function, source line) and every class
    name in the chain survive."""
    return _format_chain(traceback.TracebackException(*exc_info)).rstrip("\n")


def exception_text(formatter: logging.Formatter, record: logging.LogRecord) -> str:
    """The traceback for ``record`` under the policy: full at DEBUG, redacted
    above it. Never reads or writes ``record.exc_text``."""
    if record.levelno <= logging.DEBUG:
        return formatter.formatException(record.exc_info)
    return format_exception_redacted(record.exc_info)


class RedactingFormatter(logging.Formatter):
    """A ``logging.Formatter`` whose traceback follows the policy above.

    ``logging.Formatter.format`` caches the traceback in ``record.exc_text``,
    and every handler after the first reuses it. This formatter never reads
    that cache (another formatter may have filled it with the full text) and
    puts the old value back afterwards, so it never hands its redacted text
    to a formatter that wants the full one.
    """

    def format(self, record: logging.LogRecord) -> str:
        if not record.exc_info:
            return super().format(record)
        cached = record.exc_text
        record.exc_text = exception_text(self, record)
        try:
            return super().format(record)
        finally:
            record.exc_text = cached
