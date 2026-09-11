"""#780 review follow-up: stage_5_pubmed_enrichment.py's 29 print() calls were
converted to the module's project logger. This file pins that conversion:

- zero print() calls survive, checked structurally (AST, not substring) so a
  print() inside a comment or docstring can't satisfy it and can't break it;
- narration goes out at INFO, an unrecoverable per-lookup failure follows the
  established stage-6 pattern (see test_stage5b_print_to_logger.py) of a
  caplog assertion on level + message text;
- the module's own NEVER-exc_info rule (stage_5_pubmed_enrichment.py:58-65,
  _sanitize_error) is checked directly: the ERROR record from a failed lookup
  carries exc_info=None, and a raised requests-style message with an
  api_key=... query param is redacted before it reaches the record;
- the existing `if self.verbose:` gates are unchanged -- verbose=False must
  still suppress the INFO narration exactly as it suppressed the old prints.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage5_logging.py -p no:cacheprovider

Self-contained: no DB, no network (session.get is faked), no real LLM call.
"""

import ast
import json
import logging
import sys
from pathlib import Path

import requests

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import unified_pipeline.stage_5_pubmed_enrichment as stage5  # noqa: E402
from unified_pipeline.stage_5_pubmed_enrichment import PubMedEnricher  # noqa: E402

_STAGE5_PATH = Path(stage5.__file__)


class _FakeResponse:
    """Minimal stand-in for requests.Response, matching the shape
    test_stage5_pubmed_retry.py's FakeResponse uses for the same module."""

    def __init__(self, status_code, url, content=b''):
        self.status_code = status_code
        self.url = url
        self.content = content
        self.headers = {}

    @property
    def text(self):
        return self.content.decode('utf-8', 'replace')

    def json(self):
        return {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(
                f'{self.status_code} Error for url: {self.url}', response=self)


class _FakeSession:
    """Queue of responses; an Exception instance in the queue is raised."""

    def __init__(self, responses):
        self.responses = list(responses)

    def get(self, url, params=None, timeout=None):
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _no_id_stage4_file(tmp_path):
    """A Stage 4 file with one publication entry and no identifiers, so
    enrich_stage4_output() takes the no_id passthrough branch and makes no
    network call at all -- only the verbose narration lines fire."""
    path = tmp_path / "stage4.json"
    path.write_text(json.dumps({
        'document_uid': 'test-uid',
        'entries': [{'taxonomy_code': 'S1', 'extracted_fields': {}}],
    }))
    return str(path)


# --------------------------------------------------------------- (a) AST gate

def test_stage5_makes_no_print_calls_at_all():
    """Structural, not substring: an AST walk, so the word `print(` inside a
    comment or docstring cannot satisfy it and cannot break it either.

    Mutant that kills this: put any one print() back into
    stage_5_pubmed_enrichment.py. Verified: reinserted a bare
    `print("mutant")` at module scope; failing test id --
    test_stage5_logging.py::test_stage5_makes_no_print_calls_at_all (assert
    [<lineno>] == []).
    """
    tree = ast.parse(_STAGE5_PATH.read_text())
    prints = [node.lineno for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == 'print']
    assert prints == [], f"print() calls left in stage_5_pubmed_enrichment.py at lines {prints}"


# ------------------------------------------------------------- (b) one INFO

def test_narration_logs_at_info_with_exact_message(tmp_path, caplog):
    """The verbose narration ("Found N publication entries to enrich") must
    land on the module logger at INFO with its message unchanged from the
    old print() text.

    Mutant that kills this: demote that one logger.info() call to
    logger.debug(). Verified: edited stage_5_pubmed_enrichment.py:135 to
    logger.debug(...), reran -- failing test id --
    test_stage5_logging.py::test_narration_logs_at_info_with_exact_message
    (no INFO record found).
    """
    enricher = PubMedEnricher(verbose=True)
    with caplog.at_level(logging.INFO, logger=stage5.__name__):
        enricher.enrich_stage4_output(_no_id_stage4_file(tmp_path))

    matches = [r for r in caplog.records
               if r.levelno == logging.INFO
               and r.getMessage() == '\nFound 1 publication entries to enrich']
    assert len(matches) == 1


# ---------------------------------------------------------- (b) one WARNING

def test_transient_retry_logs_at_warning_with_exact_message(monkeypatch, caplog):
    """The "Transient API error ... retry" line must stay at WARNING (per
    the reviewer's severity map) with its message unchanged.

    Mutant that kills this: promote/demote that one logger.warning() call to
    logger.info(). Verified: edited stage_5_pubmed_enrichment.py:438 to
    logger.info(...), reran -- failing test id --
    test_stage5_logging.py::test_transient_retry_logs_at_warning_with_exact_message
    (no WARNING record found).
    """
    monkeypatch.setattr(stage5.time, 'sleep', lambda s: None)
    enricher = PubMedEnricher(verbose=True)
    enricher.session = _FakeSession([
        _FakeResponse(429, 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed'),
        _FakeResponse(200, 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed',
                      content=b'<PubmedArticleSet></PubmedArticleSet>'),
    ])

    with caplog.at_level(logging.WARNING, logger=stage5.__name__):
        enricher._fetch_pubmed_batch(['12345678'])

    matches = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(matches) == 1
    assert matches[0].getMessage().startswith(
        '    ⏳ Transient API error (429 transient error for url:')
    assert 'retry 1/2 in 1s' in matches[0].getMessage()


# ------------------------------------------------------------- (b) one ERROR

def test_lookup_failure_logs_error_no_exc_info_and_redacts_api_key(monkeypatch, caplog):
    """A failed lookup must log exactly one ERROR record (from
    _log_api_failure(), the dedup gate), with exc_info left unset -- this
    module's #58-65 comment says never logger.exception/exc_info here,
    because _sanitize_error() only scrubs the message text, not a traceback
    -- and the api_key query param redacted out of the message.

    Mutant that kills this: change _log_api_failure()'s `logger.error(message)`
    (stage_5_pubmed_enrichment.py:402) to `logger.exception(message)`.
    Verified: made that edit, reran -- failing test id --
    test_stage5_logging.py::test_lookup_failure_logs_error_no_exc_info_and_redacts_api_key
    (assert record.exc_info is None fails: exc_info is populated).
    """
    leaky_url = ('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi'
                 '?db=pubmed&api_key=dummy-test-key&retmode=xml')
    enricher = PubMedEnricher(verbose=True)
    enricher.session = _FakeSession([_FakeResponse(404, leaky_url)])

    with caplog.at_level(logging.INFO, logger=stage5.__name__):
        enricher._fetch_pubmed_batch(['12345678'])

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(error_records) == 1
    record = error_records[0]
    assert record.exc_info is None
    message = record.getMessage()
    assert 'dummy-test-key' not in message
    assert 'api_key=***' in message


# ------------------------------------------------------- (c) verbose=False

def test_quiet_mode_emits_no_stage5_info_record(tmp_path, caplog):
    """verbose=False (the CLI's --quiet) must still suppress the narration
    INFO lines exactly as it suppressed the old prints -- the `if
    self.verbose:` gates are unchanged, not decorative.

    Mutant that kills this: drop the `if self.verbose:` guard
    (stage_5_pubmed_enrichment.py:134) around the "Found N publication
    entries" line, making it unconditional. Verified: made that edit
    (de-indented the logger.info call out from under the if), reran --
    failing test id --
    test_stage5_logging.py::test_quiet_mode_emits_no_stage5_info_record
    (an INFO record was emitted with verbose=False).
    """
    enricher = PubMedEnricher(verbose=False)
    with caplog.at_level(logging.INFO, logger=stage5.__name__):
        enricher.enrich_stage4_output(_no_id_stage4_file(tmp_path))

    assert not any(r.levelno == logging.INFO for r in caplog.records)
