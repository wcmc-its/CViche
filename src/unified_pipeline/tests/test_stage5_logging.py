"""#780 review follow-up: stage_5_pubmed_enrichment.py's 29 print() calls were
converted to the module's project logger. This file pins that conversion:

- zero print() calls survive, checked structurally (AST, not substring) so a
  print() inside a comment or docstring can't satisfy it and can't break it;
- narration goes out at INFO, an unrecoverable per-lookup failure follows the
  established stage-6 pattern (see test_stage5b_print_to_logger.py) of a
  caplog assertion on level + message text;
- the module's own NEVER-exc_info rule (stage_5_pubmed_enrichment.py:58-65,
  _sanitize_error) is checked directly on every converted call that could
  plausibly grow an exc_info=True by mistake -- the pre-existing
  _log_api_failure() ERROR record, the converted "Transient API error"
  WARNING, the converted "Parse error" WARNING, and the converted __main__
  "Could not find input file" ERROR -- plus a raised requests-style message
  with an api_key=... query param redacted before it reaches the record;
- the existing `if self.verbose:` gates are unchanged -- verbose=False must
  still suppress the INFO/WARNING narration exactly as it suppressed the old
  prints, on the no-network path AND on a mocked failure path (a guard that
  only fires under real errors is exactly the kind that a no-network smoke
  test misses).

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage5_logging.py -p no:cacheprovider

Self-contained: no DB, no network (session.get is faked), no real LLM call.
"""

import ast
import json
import logging
import sys
from pathlib import Path

import pytest
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
    the reviewer's severity map) with its message unchanged, and must never
    carry exc_info (this module's never-exc_info rule, #780 review).

    Mutants that kill this:
    1. promote/demote the logger.warning() call to logger.info(). Verified:
       edited stage_5_pubmed_enrichment.py:438 to logger.info(...), reran --
       failing test id --
       test_stage5_logging.py::test_transient_retry_logs_at_warning_with_exact_message
       (no WARNING record found).
    2. add exc_info=True to that same call. Verified: edited
       stage_5_pubmed_enrichment.py:438 to
       logger.warning(..., exc_info=True), reran -- failing test id --
       test_stage5_logging.py::test_transient_retry_logs_at_warning_with_exact_message
       (assert matches[0].exc_info is None fails: exc_info is populated).
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
    assert matches[0].exc_info is None


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
    assert all(r.exc_info is None for r in caplog.records)


# ------------------------------------------ (b) more converted exc_info sites

def test_parse_error_logs_warning_no_exc_info(caplog):
    """The "Parse error" line (converted from print, stays WARNING per the
    severity map) must carry the exception text but never exc_info -- same
    never-exc_info rule as the other converted sites.

    Mutant that kills this: add exc_info=True to
    stage_5_pubmed_enrichment.py:575's logger.warning() call. Verified:
    made that edit, reran -- failing test id --
    test_stage5_logging.py::test_parse_error_logs_warning_no_exc_info
    (assert warn_records[0].exc_info is None fails: exc_info is populated).
    """
    enricher = PubMedEnricher(verbose=True)

    class _MalformedArticle:
        """Stands in for a malformed PubmedArticle element: any .find() call
        raises, driving _parse_pubmed_article() into its outer except."""

        def find(self, path):
            raise ValueError('malformed article XML')

    with caplog.at_level(logging.WARNING, logger=stage5.__name__):
        pmid, record = enricher._parse_pubmed_article(_MalformedArticle())

    assert pmid is None
    assert record is None
    warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warn_records) == 1
    assert warn_records[0].getMessage() == '    ⚠️ Parse error: malformed article XML'
    assert warn_records[0].exc_info is None


def test_main_missing_input_logs_error_no_exc_info(monkeypatch, caplog):
    """The __main__ "Could not find input file" line (converted from print,
    ERROR per the severity map) must carry the input path but never
    exc_info.

    Mutant that kills this: add exc_info=True to
    stage_5_pubmed_enrichment.py:755's logger.error() call. Verified: made
    that edit, reran -- failing test id --
    test_stage5_logging.py::test_main_missing_input_logs_error_no_exc_info
    (assert error_records[0].exc_info is None fails: exc_info is populated).
    """
    missing_input = 'definitely-missing-uid-does-not-exist-780'
    monkeypatch.setattr(sys, 'argv', ['stage_5_pubmed_enrichment.py', missing_input])

    with caplog.at_level(logging.ERROR, logger=stage5.__name__):
        with pytest.raises(SystemExit) as exc:
            stage5.main()

    assert exc.value.code == 1
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(error_records) == 1
    assert error_records[0].getMessage() == f'Error: Could not find input file: {missing_input}'
    assert error_records[0].exc_info is None


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


def test_quiet_mode_suppresses_warning_and_info_on_mocked_failure_path(monkeypatch, caplog):
    """The no-network narration path above only exercises verbose=False
    where nothing ever fails, so it cannot catch a guard that's missing only
    on the FAILURE branch. This drives a real (mocked) failure -- three
    exhausted 429s -- with verbose=False and checks that neither the
    per-retry WARNING nor the per-citation "API error" INFO fires; the
    unconditional _log_api_failure() ERROR record is unaffected by verbose
    and is not asserted against here.

    Mutants that kill this:
    1. drop the `if self.verbose:` guard at
       stage_5_pubmed_enrichment.py:437 (before the "Transient API error"
       warning), making it unconditional. Verified: de-indented that
       logger.warning() call out from under the if, reran -- failing test
       id --
       test_stage5_logging.py::test_quiet_mode_suppresses_warning_and_info_on_mocked_failure_path
       (a WARNING record was emitted with verbose=False).
    2. drop the `if self.verbose:` guard at
       stage_5_pubmed_enrichment.py:480 (before the "API error" info),
       making it unconditional. Verified: de-indented that logger.info()
       call out from under the if, reran -- failing test id --
       test_stage5_logging.py::test_quiet_mode_suppresses_warning_and_info_on_mocked_failure_path
       (an INFO record containing '❌ API error' was emitted with
       verbose=False).
    """
    monkeypatch.setattr(stage5.time, 'sleep', lambda s: None)
    enricher = PubMedEnricher(verbose=False)
    enricher.session = _FakeSession([
        _FakeResponse(429, 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed'),
    ] * 3)

    with caplog.at_level(logging.INFO, logger=stage5.__name__):
        records = enricher._fetch_pubmed_batch(['12345678'])

    assert records == {}
    assert not any(r.levelno == logging.WARNING for r in caplog.records)
    assert not any('❌ API error' in r.getMessage() for r in caplog.records)


def test_doi_lookups_log_progress_bar_lines(monkeypatch, caplog, progress_patterns):
    """Stage 5's slow path is one PubMed search per DOI; each finished lookup
    logs ``[done/total]`` so the web bar moves. The count continues from the
    PMID/PMCID entries already done (progress_base). The web driver runs
    verbose=True; --quiet keeps suppressing it like the other narration."""
    enricher = PubMedEnricher(verbose=True)
    monkeypatch.setattr(enricher, "_search_pmid_by_doi", lambda doi: None)
    monkeypatch.setattr(stage5.time, "sleep", lambda s: None)
    entries = [({"extracted_fields": {}}, f"10.1/{i}") for i in range(2)]

    with caplog.at_level(logging.INFO, logger=stage5.__name__):
        enricher._enrich_by_doi(entries, progress_base=3, progress_total=5)

    seen = []
    for r in caplog.records:
        match = next((m for p in progress_patterns if (m := p.search(r.getMessage()))), None)
        if match:
            seen.append((int(match.group(1)), int(match.group(2))))
    assert seen == [(4, 5), (5, 5)]
