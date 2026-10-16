"""Retry/backoff, identity, secret-redaction, and failure-classification
guards for Stage 5 PubMed enrichment (#222).

NCBI E-utilities return 429 when the shared rate limit is hit; previously a
single 429 aborted the whole efetch batch (catch-all -> {}). These tests pin
the new behavior: transient failures (429 / 5xx / connection errors, and a
200 whose body won't parse or carries an esearch ERROR, #1387) retry up to
MAX_ATTEMPTS total attempts with exponential backoff (Retry-After honored when
larger, summed waits capped at RETRY_TOTAL_WAIT_CAP_SECONDS), non-transient HTTP errors still fail immediately, exhausted retries
degrade exactly as before, api_key never leaks into logged errors, and the
fake support@example.com identity is gone (email sent only when configured).

They also pin DOI-path outcome classification: an empty esearch idlist stays
'doi_not_in_pubmed', while an API failure becomes 'doi_lookup_failed' (a
*_failed status the run_doctor enrichment_failures lint can see) with the
error body logged at ERROR once per failure class per run — the invalid-key
incident masked by the old catch-all (every esearch 400ed, 28 DOIs silently
classified 'doi_not_in_pubmed').

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage5_pubmed_retry.py -p no:cacheprovider

Self-contained: requests layer fully mocked, sleeps monkeypatched, no network.
"""

import json
import logging
import sys
from pathlib import Path

import pytest
import requests

# Ensure the repo's ``src`` directory is importable regardless of cwd/rootdir.
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import unified_pipeline.stage_5_pubmed_enrichment as stage5  # noqa: E402
from unified_pipeline.core.credentials import CREDENTIALS  # noqa: E402
from unified_pipeline.stage_5_pubmed_enrichment import (  # noqa: E402
    MIN_TITLE_WORD_OVERLAP,
    PubMedEnricher,
    _sanitize_error,
    in_press_phrase,
    plausible_publication_year,
    shares_an_author,
    title_word_overlap,
)

PMID = '12345678'

PUBMED_XML = f"""<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>{PMID}</PMID>
      <Article>
        <ArticleTitle>A Test Article</ArticleTitle>
        <Journal><Title>Test Journal</Title></Journal>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>
""".encode()

ESEARCH_JSON = {'esearchresult': {'idlist': [PMID]}}


class FakeResponse:
    def __init__(self, status_code=200, content=b'', headers=None, json_data=None,
                 url='https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed'):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self.url = url
        self._json = json_data

    def json(self):
        return self._json

    @property
    def text(self):
        return self.content.decode('utf-8', 'replace')

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(
                f'{self.status_code} Error for url: {self.url}', response=self)


class FakeSession:
    """Queue of responses; an Exception instance in the queue is raised.
    Crossref requests take theirs from `crossref`, and find nothing once it
    is empty (no search hits; a 404 for a DOI), so a test of a PubMed miss
    need not queue Crossref's answer."""

    def __init__(self, responses, crossref=()):
        self.responses = list(responses)
        self.crossref = list(crossref)
        self.calls = []  # (url, params) per get()

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if url == stage5.CROSSREF_WORKS_URL:
            queue = self.crossref or [_crossref_items()]
        elif url.startswith(stage5.CROSSREF_WORKS_URL):
            queue = self.crossref or [FakeResponse(404, url=url)]
        else:
            queue = self.responses
        result = queue.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _crossref_items(*items):
    return FakeResponse(200, json_data={'message': {'items': list(items)}},
                        url=stage5.CROSSREF_WORKS_URL)


def _make(monkeypatch, responses, api_key='', email='', crossref=()):
    """Enricher with mocked session + recorded (never real) sleeps.

    Module-level env lookups are monkeypatched to controlled dummy values so
    tests never depend on (or expose) a real NCBI_API_KEY in the environment.
    """
    monkeypatch.setattr(stage5, 'NCBI_API_KEY', api_key)
    monkeypatch.setattr(stage5, 'PUBMED_CONTACT_EMAIL', email)
    sleeps = []
    monkeypatch.setattr(stage5.time, 'sleep', lambda s: sleeps.append(s))
    enricher = PubMedEnricher(verbose=True)
    session = FakeSession(responses, crossref)
    enricher.session = session
    return enricher, session, sleeps


# ------------------------------------------------------ (a) retry then succeed

def test_429_then_200_succeeds_after_retry(monkeypatch):
    enricher, session, sleeps = _make(monkeypatch, [
        FakeResponse(429),
        FakeResponse(200, content=PUBMED_XML),
    ])
    records = enricher._fetch_pubmed_batch([PMID])
    assert PMID in records
    assert records[PMID]['title'] == 'A Test Article'
    assert len(session.calls) == 2
    assert sleeps == [1.0]  # exponential backoff, first step
    assert enricher.stats['api_errors'] == 0


def test_retry_after_header_honored_when_larger(monkeypatch):
    enricher, _, sleeps = _make(monkeypatch, [
        FakeResponse(429, headers={'Retry-After': '7'}),
        FakeResponse(200, content=PUBMED_XML),
    ])
    enricher._fetch_pubmed_batch([PMID])
    assert sleeps == [7.0]


def test_connection_error_then_200_retries(monkeypatch):
    enricher, session, sleeps = _make(monkeypatch, [
        requests.ConnectionError('connection reset'),
        FakeResponse(200, content=PUBMED_XML),
    ])
    records = enricher._fetch_pubmed_batch([PMID])
    assert PMID in records
    assert sleeps == [1.0]


# --------------------------------------------------- (b) exhausted -> graceful

def test_exhausted_429s_give_up_gracefully(monkeypatch, caplog):
    enricher, session, sleeps = _make(monkeypatch, [FakeResponse(429)] * stage5.MAX_ATTEMPTS)
    with caplog.at_level(logging.INFO, logger=stage5.__name__):
        records = enricher._fetch_pubmed_batch([PMID])
    assert records is None  # an API error, not "no records" (#1387)
    assert len(session.calls) == stage5.MAX_ATTEMPTS
    assert sleeps == [1.0, 2.0, 4.0, 8.0, 16.0]
    assert enricher.stats['api_errors'] == 1
    assert any('❌ API error' in r.getMessage() for r in caplog.records)


def test_id_converter_gives_up_gracefully(monkeypatch, caplog):
    enricher, session, sleeps = _make(
        monkeypatch, [FakeResponse(429)] * stage5.MAX_ATTEMPTS)
    with caplog.at_level(logging.INFO, logger=stage5.__name__):
        result = enricher._convert_pmcids_to_pmids(['PMC1234567'])
    assert result.pmids == {}
    assert result.failed == {'PMC1234567'}
    assert len(session.calls) == stage5.MAX_ATTEMPTS
    assert sleeps == [1.0, 2.0, 4.0, 8.0, 16.0]
    assert sum(sleeps) <= stage5.RETRY_TOTAL_WAIT_CAP_SECONDS
    assert enricher.stats['api_errors'] == 1
    assert any('❌ ID conversion error' in r.getMessage() for r in caplog.records)
    assert all(r.exc_info is None for r in caplog.records)


def test_id_converter_429_outlasting_the_old_window_now_converts(monkeypatch):
    ok = FakeResponse(200, json_data={'records': [{'pmcid': 'PMC1234567', 'pmid': '7654321'}]})
    enricher, session, sleeps = _make(monkeypatch, [FakeResponse(429)] * 3 + [ok])
    assert enricher._convert_pmcids_to_pmids(['PMC1234567']).pmids == {'PMC1234567': '7654321'}
    assert sleeps == [1.0, 2.0, 4.0]
    assert enricher.stats['api_errors'] == 0


def test_id_converter_honours_retry_after(monkeypatch):
    ok = FakeResponse(200, json_data={'records': []})
    enricher, _, sleeps = _make(
        monkeypatch, [FakeResponse(429, headers={'Retry-After': '20'}), ok])
    enricher._convert_pmcids_to_pmids(['PMC1234567'])
    assert sleeps == [20.0]


def test_id_converter_retry_after_beyond_the_cap_fails_fast(monkeypatch):
    too_long = str(int(stage5.RETRY_TOTAL_WAIT_CAP_SECONDS) + 1)
    enricher, session, sleeps = _make(
        monkeypatch, [FakeResponse(429, headers={'Retry-After': too_long})])
    assert enricher._convert_pmcids_to_pmids(['PMC1234567']).pmids == {}
    assert sleeps == [] and len(session.calls) == 1
    assert enricher.stats['api_errors'] == 1


def test_id_converter_summed_retry_after_waits_are_capped(monkeypatch):
    # Each 25 s wait is under the 60 s cap alone; the third would push the sum to 75 s.
    enricher, session, sleeps = _make(
        monkeypatch,
        [FakeResponse(429, headers={'Retry-After': '25'})] * stage5.MAX_ATTEMPTS)
    assert enricher._convert_pmcids_to_pmids(['PMC1234567']).pmids == {}
    assert sleeps == [25.0, 25.0]
    assert len(session.calls) == 3
    assert enricher.stats['api_errors'] == 1


def test_doi_search_retries_5xx_then_succeeds(monkeypatch):
    enricher, _, sleeps = _make(monkeypatch, [
        FakeResponse(500),
        FakeResponse(200, json_data=ESEARCH_JSON),
    ])
    assert enricher._search_pmid_by_doi('10.1000/test.123') == PMID
    assert sleeps == [1.0]


# ------------------------- (b2) a 200 that won't parse is transient (#1387)

TRUNCATED_XML = PUBMED_XML[:len(PUBMED_XML) // 2]


class BadJSONResponse(FakeResponse):
    """A 200 whose body is not JSON (truncated / HTML error page)."""

    def json(self):
        raise json.JSONDecodeError('Expecting value', '', 0)


def _esearch_error_json():
    return {'esearchresult': {'ERROR': 'Search Backend failed: synthetic test error'}}


def test_efetch_malformed_xml_then_valid_enriches_the_entry(monkeypatch):
    enricher, session, sleeps = _make(monkeypatch, [
        FakeResponse(200, content=TRUNCATED_XML),
        FakeResponse(200, content=PUBMED_XML),
    ])
    [result] = enricher._enrich_by_pmid([(_titled('A test article.', pmid=PMID), PMID)])
    assert result['enrichment_status'] == 'enriched'
    assert len(session.calls) == 2
    assert sleeps == [1.0]  # one backoff sleep
    assert enricher.stats['api_errors'] == 0
    assert enricher.stats['failed_lookups'] == 0


def test_efetch_malformed_xml_every_attempt_gives_up_and_logs_once(monkeypatch, caplog):
    enricher, session, _ = _make(
        monkeypatch, [FakeResponse(200, content=TRUNCATED_XML)] * stage5.MAX_ATTEMPTS)
    with caplog.at_level(logging.ERROR, logger=stage5.__name__):
        [result] = enricher._enrich_by_pmid([(_titled('A test article.', pmid=PMID), PMID)])
    assert result['enrichment_status'] == 'lookup_failed'
    assert len(session.calls) == stage5.MAX_ATTEMPTS
    assert enricher.stats['api_errors'] == 1
    assert len(caplog.records) == 1
    assert 'efetch' in caplog.records[0].getMessage()


def test_esearch_error_then_idlist_enriches_not_doi_not_in_pubmed(monkeypatch):
    enricher, session, sleeps = _make(monkeypatch, [
        FakeResponse(200, json_data=_esearch_error_json()),
        FakeResponse(200, json_data=ESEARCH_JSON),
        FakeResponse(200, content=PUBMED_XML),
    ])
    [result] = enricher._enrich_by_doi([(_doi_entry(), DOI)])
    assert result['enrichment_status'] == 'enriched'
    assert result['extracted_fields']['pmid'] == PMID
    assert sleeps[0] == 1.0  # the esearch backoff (later sleeps are rate-limit pauses)
    assert enricher.stats['failed_lookups'] == 0


def test_esearch_error_every_attempt_is_a_lookup_failure_not_a_no_match(monkeypatch):
    enricher, session, _ = _make(
        monkeypatch, [FakeResponse(200, json_data=_esearch_error_json())] * stage5.MAX_ATTEMPTS)
    [result] = enricher._enrich_by_doi([(_doi_entry(), DOI)])
    assert result['enrichment_status'] == 'doi_lookup_failed'
    assert len(session.calls) == stage5.MAX_ATTEMPTS
    assert enricher.stats['api_errors'] == 1


def test_esearch_malformed_json_then_idlist_finds_the_pmid(monkeypatch):
    enricher, session, sleeps = _make(monkeypatch, [
        BadJSONResponse(200),
        FakeResponse(200, json_data=ESEARCH_JSON),
    ])
    assert enricher._search_pmid_by_doi(DOI) == PMID
    assert sleeps == [1.0]


def test_title_search_error_is_retried(monkeypatch):
    enricher, session, sleeps = _make(monkeypatch, [
        FakeResponse(200, json_data=_esearch_error_json()),
        FakeResponse(200, json_data=ESEARCH_JSON),
    ])
    assert enricher._search_pmids_by_title('A synthetic title for a retry test') == [PMID]
    assert sleeps == [1.0]


def test_id_converter_malformed_json_then_valid_converts(monkeypatch):
    ok = FakeResponse(200, json_data={'records': [{'pmcid': 'PMC1234567', 'pmid': '7654321'}]})
    enricher, _, sleeps = _make(monkeypatch, [BadJSONResponse(200), ok])
    assert enricher._convert_pmcids_to_pmids(['PMC1234567']).pmids == {'PMC1234567': '7654321'}
    assert sleeps == [1.0]
    assert enricher.stats['api_errors'] == 0


def test_efetch_429s_outlasting_the_old_window_now_fetch(monkeypatch):
    enricher, session, sleeps = _make(
        monkeypatch, [FakeResponse(429)] * 3 + [FakeResponse(200, content=PUBMED_XML)])
    assert PMID in enricher._fetch_pubmed_batch([PMID])
    assert sleeps == [1.0, 2.0, 4.0]


def test_efetch_total_wait_stays_within_the_cap_on_long_retry_after(monkeypatch):
    enricher, session, sleeps = _make(
        monkeypatch,
        [FakeResponse(429, headers={'Retry-After': '120'})] * stage5.MAX_ATTEMPTS)
    assert enricher._fetch_pubmed_batch([PMID]) is None
    assert sum(sleeps) <= stage5.RETRY_TOTAL_WAIT_CAP_SECONDS
    assert sleeps == [] and len(session.calls) == 1


def test_no_sleep_after_the_last_attempt_even_under_a_loose_cap(monkeypatch):
    monkeypatch.setattr(stage5, 'RETRY_TOTAL_WAIT_CAP_SECONDS', 10_000.0)
    enricher, session, sleeps = _make(monkeypatch, [FakeResponse(429)] * stage5.MAX_ATTEMPTS)
    assert enricher._fetch_pubmed_batch([PMID]) is None
    assert len(sleeps) == stage5.MAX_ATTEMPTS - 1  # nothing to wait for after the last try


# --------------------------------------------------------- (c) 404 = no retry

def test_404_fails_immediately_no_retry(monkeypatch):
    enricher, session, sleeps = _make(monkeypatch, [FakeResponse(404)])
    records = enricher._fetch_pubmed_batch([PMID])
    assert records is None
    assert len(session.calls) == 1  # no retry on non-transient 4xx
    assert sleeps == []
    assert enricher.stats['api_errors'] == 1


# ------------------------------------------------------- (d) contact identity

def test_email_param_present_when_configured(monkeypatch):
    enricher, session, _ = _make(
        monkeypatch, [FakeResponse(200, content=PUBMED_XML)],
        email='curator@med.cornell.edu')
    enricher._fetch_pubmed_batch([PMID])
    _, params = session.calls[0]
    assert params['email'] == 'curator@med.cornell.edu'
    assert params['tool'] == 'scholar_signals_cv_pipeline'


def test_email_param_omitted_when_unset(monkeypatch):
    enricher, session, _ = _make(monkeypatch, [FakeResponse(200, content=PUBMED_XML)])
    enricher._fetch_pubmed_batch([PMID])
    _, params = session.calls[0]
    assert 'email' not in params  # anonymous beats a fake identity
    assert params['tool'] == 'scholar_signals_cv_pipeline'


def test_placeholder_email_gone_from_source():
    source = Path(stage5.__file__).read_text(encoding='utf-8')
    assert 'support@example.com' not in source


# ------------------------------------------------------------- (e) api_key

def test_api_key_sent_when_configured(monkeypatch):
    enricher, session, _ = _make(
        monkeypatch, [FakeResponse(200, content=PUBMED_XML)],
        api_key='dummy-test-key')
    enricher._fetch_pubmed_batch([PMID])
    _, params = session.calls[0]
    assert params['api_key'] == 'dummy-test-key'


def test_api_key_omitted_when_unset(monkeypatch):
    enricher, session, _ = _make(monkeypatch, [FakeResponse(200, content=PUBMED_XML)])
    enricher._fetch_pubmed_batch([PMID])
    _, params = session.calls[0]
    assert 'api_key' not in params


# ------------------------------------------------------------- (f) redaction

def test_sanitizer_redacts_api_key():
    msg = ('429 Client Error for url: https://eutils.ncbi.nlm.nih.gov/entrez/'
           'eutils/efetch.fcgi?db=pubmed&id=1&api_key=dummy-test-key-123&retmode=xml')
    cleaned = _sanitize_error(msg)
    assert 'dummy-test-key-123' not in cleaned
    assert 'api_key=***' in cleaned
    assert 'db=pubmed' in cleaned  # rest of the message preserved


def test_logged_error_is_redacted_end_to_end(monkeypatch, caplog):
    leaky_url = ('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi'
                 '?db=pubmed&api_key=dummy-test-key&retmode=xml')
    enricher, _, _ = _make(
        monkeypatch, [FakeResponse(404, url=leaky_url)], api_key='dummy-test-key')
    with caplog.at_level(logging.INFO, logger=stage5.__name__):
        enricher._fetch_pubmed_batch([PMID])
    assert not any('dummy-test-key' in r.getMessage() for r in caplog.records)
    assert any('api_key=***' in r.getMessage() for r in caplog.records)


# ------------------------------------ (g) DOI-path outcome classification

DOI = '10.1000/test.123'
ESEARCH_EMPTY_JSON = {'esearchresult': {'idlist': []}}
API_KEY_INVALID_BODY = b'{"error":"API key invalid"}'


def _doi_entry():
    return {'extracted_fields': {'doi': DOI}}


def test_empty_idlist_stays_doi_not_in_pubmed(monkeypatch, caplog):
    enricher, _, _ = _make(monkeypatch, [FakeResponse(200, json_data=ESEARCH_EMPTY_JSON)])
    with caplog.at_level(logging.ERROR, logger=stage5.__name__):
        results = enricher._enrich_by_doi([(_doi_entry(), DOI)])
    assert results[0]['enrichment_status'] == 'doi_not_in_pubmed'
    assert enricher.stats['api_errors'] == 0
    assert caplog.records == []  # a legitimate no-match is not an API failure


def test_esearch_400_becomes_doi_lookup_failed(monkeypatch, caplog):
    enricher, session, _ = _make(
        monkeypatch, [FakeResponse(400, content=API_KEY_INVALID_BODY)],
        api_key='dummy-test-key')
    with caplog.at_level(logging.ERROR, logger=stage5.__name__):
        results = enricher._enrich_by_doi([(_doi_entry(), DOI)])
    assert results[0]['enrichment_status'] == 'doi_lookup_failed'
    assert enricher.stats['failed_lookups'] == 1
    assert enricher.stats['api_errors'] == 1
    assert len(session.calls) == 1  # 400 is non-transient: no retry
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.ERROR
    assert 'API key invalid' in caplog.records[0].getMessage()


def test_esearch_exhausted_429s_become_doi_lookup_failed(monkeypatch):
    enricher, session, _ = _make(monkeypatch, [FakeResponse(429)] * stage5.MAX_ATTEMPTS)
    results = enricher._enrich_by_doi([(_doi_entry(), DOI)])
    assert results[0]['enrichment_status'] == 'doi_lookup_failed'
    assert len(session.calls) == stage5.MAX_ATTEMPTS  # retries still exhausted first
    assert session.responses == []


def test_esearch_connection_error_becomes_doi_lookup_failed(monkeypatch):
    enricher, session, _ = _make(
        monkeypatch, [requests.ConnectionError('reset')] * stage5.MAX_ATTEMPTS)
    results = enricher._enrich_by_doi([(_doi_entry(), DOI)])
    assert results[0]['enrichment_status'] == 'doi_lookup_failed'
    assert session.responses == []  # gave up on the error, not an exhausted fake


def test_doi_path_success_still_enriches(monkeypatch):
    enricher, _, _ = _make(monkeypatch, [
        FakeResponse(200, json_data=ESEARCH_JSON),
        FakeResponse(200, content=PUBMED_XML),
    ])
    results = enricher._enrich_by_doi([(_doi_entry(), DOI)])
    assert results[0]['enrichment_status'] == 'enriched'
    assert results[0]['enrichment_source'] == 'doi_search'
    assert results[0]['extracted_fields']['pmid'] == PMID


def test_new_status_matches_doctor_failed_vocabulary():
    # run_doctor lint 9 matches enrichment_status.endswith('_failed')
    assert 'doi_lookup_failed'.endswith('_failed')
    assert not 'doi_not_in_pubmed'.endswith('_failed')
    assert 'title_check_failed'.endswith('_failed')  # #1043 rejection surfaces too


# --------------------------- (h) ERROR body once per failure class per run

def test_api_error_body_logged_once_per_class(monkeypatch, caplog):
    enricher, _, _ = _make(
        monkeypatch, [FakeResponse(400, content=API_KEY_INVALID_BODY)] * 2,
        api_key='dummy-test-key')
    entries = [(_doi_entry(), DOI), (_doi_entry(), DOI)]
    with caplog.at_level(logging.INFO, logger=stage5.__name__):
        results = enricher._enrich_by_doi(entries)
    assert [r['enrichment_status'] for r in results] == ['doi_lookup_failed'] * 2
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    info_records = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(error_records) == 1  # bad key 400s every call; body logged once
    # per-citation verbose diagnostics now go through the logger at INFO
    assert sum('⚠️ DOI search error' in r.getMessage() for r in info_records) == 2
    assert all(r.exc_info is None for r in caplog.records)


def test_distinct_failure_classes_each_logged(monkeypatch, caplog):
    enricher, _, _ = _make(
        monkeypatch,
        [FakeResponse(400, content=API_KEY_INVALID_BODY)]
        + [requests.ConnectionError('reset')] * stage5.MAX_ATTEMPTS,
        api_key='dummy-test-key')
    entries = [(_doi_entry(), DOI), (_doi_entry(), DOI)]
    with caplog.at_level(logging.ERROR, logger=stage5.__name__):
        enricher._enrich_by_doi(entries)
    assert len(caplog.records) == 2


def test_efetch_failure_logged_at_error_with_redaction(monkeypatch, caplog):
    leaky_url = ('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi'
                 '?db=pubmed&api_key=dummy-test-key&retmode=xml')
    enricher, _, _ = _make(
        monkeypatch,
        [FakeResponse(400, content=API_KEY_INVALID_BODY, url=leaky_url)],
        api_key='dummy-test-key')
    with caplog.at_level(logging.ERROR, logger=stage5.__name__):
        enricher._fetch_pubmed_batch([PMID])
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert 'dummy-test-key' not in msg
    assert 'api_key=***' in msg
    assert 'API key invalid' in msg


def test_pmcid_conversion_failure_logged_at_error(monkeypatch, caplog):
    enricher, _, _ = _make(monkeypatch, [FakeResponse(429)] * stage5.MAX_ATTEMPTS)
    with caplog.at_level(logging.ERROR, logger=stage5.__name__):
        enricher._convert_pmcids_to_pmids(['PMC1234567'])
    assert len(caplog.records) == 1
    assert 'pmcid_conversion' in caplog.records[0].getMessage()


# ------------------------------------- own ArticleIdList, never ReferenceList (#1042)

REFERENCE_LIST_XML = (
    Path(__file__).resolve().parent / 'fixtures' / 'efetch_with_reference_list.xml'
).read_bytes()


def test_efetch_reads_own_ids_not_last_reference(monkeypatch):
    enricher, _, _ = _make(monkeypatch, [FakeResponse(200, content=REFERENCE_LIST_XML)])
    record = enricher._fetch_pubmed_batch(['11111111', '22222222'])['11111111']
    assert record['doi'] == '10.1000/own.1'
    assert record['pmcid'] == 'PMC1111111'


def test_efetch_article_without_own_ids_gets_empty_not_reference_ids(monkeypatch):
    enricher, _, _ = _make(monkeypatch, [FakeResponse(200, content=REFERENCE_LIST_XML)])
    record = enricher._fetch_pubmed_batch(['11111111', '22222222'])['22222222']
    assert record['doi'] == ''
    assert record['pmcid'] == ''


# ------------------------- (i) title gate: a record naming another paper (#1043)

UNRELATED_TITLE = 'Lunar tides and migratory patterns of coastal herons'
PMCID = 'PMC7654321'


def _article_xml(title, vernacular=None):
    vern = f'<VernacularTitle>{vernacular}</VernacularTitle>' if vernacular else ''
    return f"""<?xml version="1.0"?>
<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>{PMID}</PMID>
<Article><ArticleTitle>{title}</ArticleTitle>{vern}<Journal><Title>J</Title></Journal></Article>
</MedlineCitation></PubmedArticle></PubmedArticleSet>""".encode()


def _titled(title, **ids):
    return {'extracted_fields': {'title': title, **ids}}


def test_pmid_path_rejects_a_record_for_another_paper(monkeypatch):
    enricher, _, _ = _make(monkeypatch, [FakeResponse(200, content=PUBMED_XML)])
    entry = _titled(UNRELATED_TITLE, pmid=PMID)
    [result] = enricher._enrich_by_pmid([(entry, PMID)])
    assert result['enrichment_status'] == 'title_check_failed'
    assert 'enrichment_data' not in result  # stage 6 renders the CV's own citation
    assert result['enrichment_rejected'] == {
        'source': 'pmid', 'pubmed_pmid': PMID,
        'pubmed_title': 'A Test Article', 'title_word_overlap': 0.0}
    assert enricher.stats['title_mismatches'] == 1
    assert enricher.stats['failed_lookups'] == 1
    assert enricher.stats['enriched'] == 0


def test_pmid_path_accepts_the_same_paper(monkeypatch):
    enricher, _, _ = _make(monkeypatch, [FakeResponse(200, content=PUBMED_XML)])
    [result] = enricher._enrich_by_pmid([(_titled('A test article.', pmid=PMID), PMID)])
    assert result['enrichment_status'] == 'enriched'
    assert result['enrichment_source'] == 'pmid'
    assert enricher.stats['title_mismatches'] == 0
    assert enricher.stats['pmid_lookups'] == 1


def test_pmcid_path_rejection_does_not_store_the_discovered_pmid(monkeypatch):
    enricher, _, _ = _make(monkeypatch, [
        FakeResponse(200, json_data={'records': [{'pmcid': PMCID, 'pmid': PMID}]}),
        FakeResponse(200, content=PUBMED_XML),
    ])
    [result] = enricher._enrich_by_pmcid([(_titled(UNRELATED_TITLE, pmcid=PMCID), PMCID)])
    assert result['enrichment_status'] == 'title_check_failed'
    assert result['enrichment_rejected']['source'] == 'pmcid_conversion'
    assert 'pmid' not in result['extracted_fields']


def test_doi_path_rejection_does_not_store_the_discovered_pmid(monkeypatch):
    enricher, _, _ = _make(monkeypatch, [
        FakeResponse(200, json_data=ESEARCH_JSON),
        FakeResponse(200, content=PUBMED_XML),
    ])
    [result] = enricher._enrich_by_doi([(_titled(UNRELATED_TITLE, doi=DOI), DOI)])
    assert result['enrichment_status'] == 'title_check_failed'
    assert result['enrichment_rejected']['source'] == 'doi_search'
    assert 'pmid' not in result['extracted_fields']


def test_translated_title_matches_through_vernacular_title(monkeypatch):
    # PubMed brackets the English translation and keeps the original,
    # unaccented, in VernacularTitle; the CV cites the original with accents.
    xml = _article_xml('[Seasonal patterns of heron migration on the coast].',
                       vernacular='Patrones estacionales de la migracion de garzas en la costa')
    enricher, _, _ = _make(monkeypatch, [FakeResponse(200, content=xml)])
    entry = _titled('Patrones estacionales de la migración de garzas en la costa', pmid=PMID)
    [result] = enricher._enrich_by_pmid([(entry, PMID)])
    assert result['enrichment_status'] == 'enriched'


def test_untitled_source_skips_the_gate(monkeypatch):
    enricher, _, _ = _make(monkeypatch, [FakeResponse(200, content=PUBMED_XML)])
    [result] = enricher._enrich_by_pmid([({'extracted_fields': {'pmid': PMID}}, PMID)])
    assert result['enrichment_status'] == 'enriched'


def test_chapter_title_is_compared_when_there_is_no_title(monkeypatch):
    enricher, _, _ = _make(monkeypatch, [FakeResponse(200, content=PUBMED_XML)])
    entry = {'extracted_fields': {'chapter_title': UNRELATED_TITLE, 'pmid': PMID}}
    [result] = enricher._enrich_by_pmid([(entry, PMID)])
    assert result['enrichment_status'] == 'title_check_failed'


def test_overlap_at_the_floor_is_accepted_and_just_below_is_not():
    # 2 of the shorter title's 5 words shared = 0.4, the floor itself.
    assert title_word_overlap('alpha bravo charlie delta echo',
                              ['alpha bravo xray yankee zulu kilo']) == MIN_TITLE_WORD_OVERLAP
    assert title_word_overlap('alpha bravo charlie delta echo foxtrot',
                              ['alpha bravo xray yankee zulu kilo']) < MIN_TITLE_WORD_OVERLAP


def test_overlap_ignores_short_words_case_and_accents():
    assert title_word_overlap('Él y la Garza', ['EL Y LA GARZA']) == 1.0  # only 'garza' counts
    assert title_word_overlap('of in a', ['Herons']) is None
    assert title_word_overlap('Women\u2019s health', ["Women's health"]) == 1.0


def test_accent_folding_matches_unaccented_vernacular():
    assert title_word_overlap('Garzas migración', ['Garzas migracion']) == 1.0


def test_record_exactly_at_the_floor_is_enriched(monkeypatch):
    xml = _article_xml('alpha bravo xray yankee zulu')
    enricher, _, _ = _make(monkeypatch, [FakeResponse(200, content=xml)])
    entry = _titled('alpha bravo charlie delta echo', pmid=PMID)  # 2 of 5 = 0.4
    [result] = enricher._enrich_by_pmid([(entry, PMID)])
    assert result['enrichment_status'] == 'enriched'


def test_rejection_records_the_rounded_overlap(monkeypatch):
    xml = _article_xml('Heron Article Reviewed')
    enricher, _, _ = _make(monkeypatch, [FakeResponse(200, content=xml)])
    entry = _titled('Heron tides and coastal lunar cycles', pmid=PMID)  # 1 of 3
    [result] = enricher._enrich_by_pmid([(entry, PMID)])
    assert result['enrichment_rejected']['title_word_overlap'] == 0.33


def test_three_letter_words_count():
    # 'DNA' is signal: a 4-letter floor would score these two titles 0.
    assert title_word_overlap('DNA repair', ['DNA damage']) == 0.5


# ------------- (i-b) title gate: same-topic record of another year and volume (#1696)

# The UAURFU e694 shape, synthetic: the record's short title is a subset of the
# CV's topic words (5 of its 7 words shared, 0.71 against the shorter title),
# but only 5 of the CV's 15, and the year and volume both differ.
LONG_CV_TITLE = ('Views of coastal birders and park rangers about digital survey tools '
                 'for heron nesting in the estuary')
SHORT_RECORD_TITLE = 'Digital survey tool for identification of heron nesting.'


def _dated_article_xml(title, year, volume, vernacular=None):
    vern = f'<VernacularTitle>{vernacular}</VernacularTitle>' if vernacular else ''
    return f"""<?xml version="1.0"?>
<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>{PMID}</PMID>
<Article><ArticleTitle>{title}</ArticleTitle>{vern}<Journal><Title>J</Title>
<JournalIssue><Volume>{volume}</Volume><PubDate><Year>{year}</Year></PubDate></JournalIssue>
</Journal></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>""".encode()


def _record(title=SHORT_RECORD_TITLE, year=2020, volume='19', vernacular_title=None):
    return {'pmid': PMID, 'title': title, 'vernacular_title': vernacular_title,
            'year': year, 'volume': volume, 'authors': [], 'authors_vancouver': ''}


def _accepts(entry_fields, record, source='pmid'):
    entry = {'extracted_fields': entry_fields}
    return PubMedEnricher(verbose=False)._accept_record(entry, record, source), entry


def test_pmcid_record_of_another_year_and_volume_is_refused(monkeypatch):
    assert 0.7 < title_word_overlap(LONG_CV_TITLE, [SHORT_RECORD_TITLE]) < 0.8
    enricher, _, _ = _make(monkeypatch, [
        FakeResponse(200, json_data={'records': [{'pmcid': PMCID, 'pmid': PMID}]}),
        FakeResponse(200, content=_dated_article_xml(SHORT_RECORD_TITLE, 2020, '19')),
    ])
    entry = _titled(LONG_CV_TITLE, pmcid=PMCID, year='2021', volume='20')
    [result] = enricher._enrich_by_pmcid([(entry, PMCID)])
    assert result['enrichment_status'] == 'title_check_failed'
    assert 'enrichment_data' not in result  # the CV's own citation renders
    assert result['enrichment_rejected'] == {
        'source': 'pmcid_conversion', 'pubmed_pmid': PMID, 'pubmed_title': SHORT_RECORD_TITLE,
        'title_word_overlap': 0.71, 'year_and_volume_differ': True}
    assert 'pmid' not in result['extracted_fields']


@pytest.mark.parametrize('cv_fields, record', [
    # online-versus-print: the year is off by one, the volume is the record's
    ({'year': '2021', 'volume': '19'}, _record()),
    ({'year': '2020', 'volume': '20'}, _record()),        # the year agrees
    ({'year': '2021'}, _record()),                         # no CV volume
    ({'volume': '20'}, _record()),                         # no CV year
    ({'year': 'n.d.', 'volume': '20'}, _record()),         # no year to read
    ({'year': '2021', 'volume': '20'}, _record(year=None)),
    ({'year': '2021', 'volume': '20'}, _record(volume='')),
    ({'year': '2021', 'volume': '20'}, _record(volume='Suppl 2')),
    ({'year': '2021', 'volume': '19'}, _record(volume='19 Suppl 1')),
], ids=['volume-agrees', 'year-agrees', 'no-cv-volume', 'no-cv-year', 'unreadable-cv-year',
        'no-record-year', 'no-record-volume', 'record-volume-has-no-number', 'supplement-volume'])
def test_record_is_accepted_unless_year_and_volume_both_contradict(cv_fields, record):
    accepted, entry = _accepts({'title': LONG_CV_TITLE, **cv_fields}, record)
    assert accepted, entry.get('enrichment_rejected')
    assert entry['enrichment_status'] == 'enriched'


@pytest.mark.parametrize('source', ['pmid', 'pmcid_conversion', 'doi_search', 'pmcid_as_pmid'])
def test_every_id_path_refuses_the_contradicted_record(source):
    accepted, entry = _accepts({'title': LONG_CV_TITLE, 'year': '2021', 'volume': '20'},
                               _record(), source)
    assert not accepted
    assert entry['enrichment_rejected']['year_and_volume_differ'] is True


@pytest.mark.parametrize('source', sorted(stage5.TITLE_SEARCH_SOURCES))
def test_title_search_is_left_to_its_own_guards(source):
    accepted, _ = _accepts({'title': LONG_CV_TITLE, 'year': '2021', 'volume': '20'},
                           _record(), source)
    assert accepted


def test_same_title_with_a_wrong_cv_year_and_volume_is_kept():
    # A CV that gives the issue number as the volume and the online year (the
    # 11 same-title year-and-volume disagreements in the corpus replay).
    accepted, _ = _accepts({'title': SHORT_RECORD_TITLE, 'year': '2021', 'volume': '4'},
                           _record())
    assert accepted


def test_same_title_through_the_vernacular_title_is_kept():
    record = _record(title='[A different translated title].', vernacular_title=LONG_CV_TITLE)
    accepted, _ = _accepts({'title': LONG_CV_TITLE, 'year': '2021', 'volume': '20'}, record)
    assert accepted


def test_untitled_entry_still_skips_the_gate():
    accepted, _ = _accepts({'year': '2021', 'volume': '20'}, _record())
    assert accepted


@pytest.mark.parametrize('volume, number', [
    ('19 Suppl 1', 19), (' 7', 7), (42, 42), ('Suppl 2', None), ('', None), (None, None)])
def test_volume_number_is_the_leading_number(volume, number):
    assert stage5.volume_number(volume) == number


# ------------------------------------------------ #1219 items 2-4

BARE_PMID = PMID  # 8 digits, written under a "PMCID" label with no PMC prefix


def _idconv_error_response():
    return FakeResponse(200, json_data={'records': [{'requested-id': BARE_PMID, 'status': 'error'}]})


def test_bare_digit_pmcid_is_tried_as_a_pmid(monkeypatch):
    enricher, session, _ = _make(monkeypatch, [
        _idconv_error_response(),
        FakeResponse(200, content=PUBMED_XML),
    ])
    entry = _titled('A test article', pmcid=BARE_PMID)
    [result] = enricher._enrich_by_pmcid([(entry, f'PMC{BARE_PMID}')])
    assert result['enrichment_status'] == 'enriched'
    assert result['enrichment_source'] == 'pmcid_as_pmid'
    assert session.calls[1][1]['id'] == BARE_PMID
    assert enricher.stats['failed_lookups'] == 0


def test_bare_digit_pmcid_retry_keeps_the_title_guard(monkeypatch):
    enricher, _, _ = _make(monkeypatch, [
        _idconv_error_response(),
        FakeResponse(200, content=PUBMED_XML),
    ])
    entry = _titled(UNRELATED_TITLE, pmcid=BARE_PMID)
    [result] = enricher._enrich_by_pmcid([(entry, f'PMC{BARE_PMID}')])
    assert result['enrichment_status'] == 'pmcid_not_found'
    assert 'enrichment_rejected' not in result
    assert enricher.stats['title_mismatches'] == 0
    assert enricher.stats['failed_lookups'] == 1


@pytest.mark.parametrize('label', [f'PMCID-{BARE_PMID}', f'PMCID: {BARE_PMID}', f'PMCID {BARE_PMID}'])
def test_labelled_digit_pmcid_is_tried_as_a_pmid(monkeypatch, label):
    enricher, session, _ = _make(monkeypatch, [
        _idconv_error_response(),
        FakeResponse(200, content=PUBMED_XML),
    ])
    entry = _titled('A test article', pmcid=label)
    [result] = enricher._enrich_by_pmcid([(entry, f'PMC{BARE_PMID}')])
    assert result['enrichment_status'] == 'enriched'
    assert session.calls[1][1]['id'] == BARE_PMID


def test_nine_digit_pmcid_value_is_not_truncated_to_a_pmid(monkeypatch):
    enricher, session, _ = _make(monkeypatch, [_idconv_error_response()])
    entry = _titled('A test article', pmcid='123456789')
    [result] = enricher._enrich_by_pmcid([(entry, 'PMC123456789')])
    assert result['enrichment_status'] == 'pmcid_not_found'
    assert len(session.calls) == 1


def test_bare_digit_pmcid_missing_as_pmid_keeps_its_original_failure(monkeypatch):
    enricher, _, _ = _make(monkeypatch, [
        _idconv_error_response(),
        FakeResponse(200, content=b'<PubmedArticleSet/>'),
    ])
    entry = _titled('A test article', pmcid=BARE_PMID)
    [result] = enricher._enrich_by_pmcid([(entry, f'PMC{BARE_PMID}')])
    assert result['enrichment_status'] == 'pmcid_not_found'
    assert enricher.stats['failed_lookups'] == 1


def test_bare_digit_pmcid_title_rejected_via_idconv_is_retried_as_pmid(monkeypatch):
    other = _article_xml('Something Entirely Different Here').replace(
        f'<PMID>{PMID}</PMID>'.encode(), b'<PMID>11112222</PMID>')
    enricher, _, _ = _make(monkeypatch, [
        FakeResponse(200, json_data={'records': [{'pmcid': f'PMC{BARE_PMID}', 'pmid': '11112222'}]}),
        FakeResponse(200, content=other),
        FakeResponse(200, content=PUBMED_XML),
    ])
    entry = _titled('A test article', pmcid=BARE_PMID)
    [result] = enricher._enrich_by_pmcid([(entry, f'PMC{BARE_PMID}')])
    assert result['enrichment_status'] == 'enriched'
    assert result['enrichment_source'] == 'pmcid_as_pmid'
    assert 'enrichment_rejected' not in result
    assert enricher.stats['title_mismatches'] == 0
    assert enricher.stats['failed_lookups'] == 0


def test_title_rejected_via_idconv_and_again_as_pmid_keeps_the_idconv_rejection(monkeypatch):
    other = _article_xml('Something Entirely Different Here').replace(
        f'<PMID>{PMID}</PMID>'.encode(), b'<PMID>11112222</PMID>')
    enricher, _, _ = _make(monkeypatch, [
        FakeResponse(200, json_data={'records': [{'pmcid': f'PMC{BARE_PMID}', 'pmid': '11112222'}]}),
        FakeResponse(200, content=other),
        FakeResponse(200, content=PUBMED_XML),
    ])
    entry = _titled(UNRELATED_TITLE, pmcid=BARE_PMID)
    [result] = enricher._enrich_by_pmcid([(entry, f'PMC{BARE_PMID}')])
    assert result['enrichment_status'] == 'title_check_failed'
    assert result['enrichment_rejected']['source'] == 'pmcid_conversion'
    assert result['enrichment_rejected']['pubmed_pmid'] == '11112222'
    assert enricher.stats['title_mismatches'] == 1
    assert enricher.stats['failed_lookups'] == 1


def test_prefixed_pmcid_is_never_tried_as_a_pmid(monkeypatch):
    enricher, session, _ = _make(monkeypatch, [_idconv_error_response()])
    entry = _titled('A test article', pmcid=f'PMC{BARE_PMID}')
    [result] = enricher._enrich_by_pmcid([(entry, f'PMC{BARE_PMID}')])
    assert result['enrichment_status'] == 'pmcid_not_found'
    assert len(session.calls) == 1


# ------------------------------- #1219 item 1: idconv URL, chunks, status split, re-run

def _idconv_outage():
    """A 429 asking for longer than the retry cap: fails after one call."""
    return FakeResponse(
        429, headers={'Retry-After': str(int(stage5.RETRY_TOTAL_WAIT_CAP_SECONDS) + 1)})


def _idconv_found(pmid=PMID):
    # idconv answers the PMID as a JSON number.
    return FakeResponse(200, json_data={'records': [{'pmcid': PMCID, 'pmid': int(pmid)}]})


def _idconv_not_found():
    return FakeResponse(200, json_data={'records': [
        {'pmcid': PMCID, 'requested-id': PMCID, 'status': 'error',
         'errmsg': 'Identifier not found in PMC'}]})


def _pmcid_entry():
    return {'element_idx_start': 10, 'taxonomy_code': 'S1', 'text': 'Garcia M. A test article.',
            'extracted_fields': {'title': 'A test article', 'pmcid': PMCID}}


def test_id_converter_uses_the_current_url(monkeypatch):
    enricher, session, _ = _make(monkeypatch, [_idconv_found()])
    enricher._convert_pmcids_to_pmids([PMCID])
    assert session.calls[0][0] == 'https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/'


def test_numeric_pmid_from_id_converter_enriches(monkeypatch):
    enricher, session, _ = _make(monkeypatch, [_idconv_found(), FakeResponse(200, content=PUBMED_XML)])
    [result] = enricher._enrich_by_pmcid([(_titled('A test article', pmcid=PMCID), PMCID)])
    assert result['enrichment_status'] == 'enriched'
    assert result['extracted_fields']['pmid'] == PMID
    assert session.calls[1][1]['id'] == PMID


def test_id_converter_sends_at_most_200_ids_per_request(monkeypatch):
    pmcids = [f'PMC{n}' for n in range(1000001, 1000202)]  # 201
    enricher, session, sleeps = _make(monkeypatch, [
        FakeResponse(200, json_data={'records': []}),
        FakeResponse(200, json_data={'records': [{'pmcid': pmcids[-1], 'pmid': 7654321}]}),
    ])
    result = enricher._convert_pmcids_to_pmids(pmcids + pmcids[:5])  # duplicates sent once
    assert [len(params['ids'].split(',')) for _, params in session.calls] == [200, 1]
    assert result.pmids == {pmcids[-1]: '7654321'}
    assert sleeps == [stage5.RATE_LIMIT_DELAY]


def test_a_failed_idconv_chunk_fails_only_its_own_pmcids(monkeypatch):
    pmcids = [f'PMC{n}' for n in range(1000001, 1000202)]
    enricher, _, _ = _make(monkeypatch, [
        FakeResponse(200, json_data={'records': [{'pmcid': pmcids[0], 'pmid': 7654321}]}),
        _idconv_outage(),
    ])
    result = enricher._convert_pmcids_to_pmids(pmcids)
    assert result.pmids == {pmcids[0]: '7654321'}
    assert result.failed == {pmcids[-1]}
    assert enricher.stats['api_errors'] == 1


def test_efetch_sends_at_most_200_ids_per_request(monkeypatch):
    pmids = [str(n) for n in range(10000001, 10000202)]
    enricher, session, _ = _make(monkeypatch, [FakeResponse(200, content=b'<PubmedArticleSet/>')] * 2)
    enricher._fetch_pubmed_records(pmids)
    assert [len(params['id'].split(',')) for _, params in session.calls] == [200, 1]


@pytest.mark.parametrize('responses, status', [
    ([_idconv_not_found()], 'pmcid_not_found'),
    ([_idconv_outage()], 'pmcid_lookup_failed'),
    ([_idconv_found(), FakeResponse(200, content=b'<PubmedArticleSet/>')], 'pmcid_lookup_failed'),
])
def test_pmcid_not_found_is_told_apart_from_a_failed_lookup(monkeypatch, responses, status):
    enricher, _, _ = _make(monkeypatch, responses)
    [result] = enricher._enrich_by_pmcid([(_titled('A test article', pmcid=PMCID), PMCID)])
    assert result['enrichment_status'] == status
    assert enricher.stats['failed_lookups'] == 1


def test_only_a_failed_lookup_matches_the_doctor_failed_vocabulary():
    assert stage5.PMCID_LOOKUP_FAILED.endswith('_failed')
    assert not stage5.PMCID_NOT_FOUND.endswith('_failed')


def test_failed_pmcid_lookup_is_rerun_at_the_end_of_the_stage(tmp_path, monkeypatch):
    results, session, enricher = _run_entries(tmp_path, monkeypatch, [_pmcid_entry()], [
        _idconv_outage(), _idconv_found(), FakeResponse(200, content=PUBMED_XML)])
    [result] = results
    assert result['enrichment_status'] == 'enriched'
    assert result['enrichment_source'] == 'pmcid_conversion'
    assert [url for url, _ in session.calls] == [stage5.ID_CONVERTER_URL] * 2 + [stage5.EFETCH_URL]
    assert (enricher.stats['enriched'], enricher.stats['failed_lookups']) == (1, 0)


def test_rerun_waits_before_asking_idconv_again(tmp_path, monkeypatch):
    enricher, _, sleeps = _make(monkeypatch, [
        _idconv_outage(), _idconv_found(), FakeResponse(200, content=PUBMED_XML)])
    path = tmp_path / 's4.json'
    path.write_text(json.dumps({'document_uid': 'x', 'entries': [_pmcid_entry()]}))
    enricher.enrich_stage4_output(str(path))
    assert sleeps == [stage5.PMCID_RERUN_PAUSE_SECONDS]


def test_failed_pmcid_lookup_is_rerun_only_once(tmp_path, monkeypatch):
    results, session, enricher = _run_entries(
        tmp_path, monkeypatch, [_pmcid_entry()], [_idconv_outage(), _idconv_outage()])
    assert results[0]['enrichment_status'] == 'pmcid_lookup_failed'
    assert len(session.calls) == 2
    assert enricher.stats['failed_lookups'] == 1


def test_pmcid_not_found_is_not_rerun(tmp_path, monkeypatch):
    results, session, enricher = _run_entries(
        tmp_path, monkeypatch, [_pmcid_entry()], [_idconv_not_found()])
    assert results[0]['enrichment_status'] == 'pmcid_not_found'
    assert len(session.calls) == 1
    assert enricher.stats['failed_lookups'] == 1


def test_doi_search_term_is_quoted(monkeypatch):
    enricher, session, _ = _make(monkeypatch, [FakeResponse(200, json_data=ESEARCH_JSON)])
    enricher._search_pmid_by_doi('10.1000/abc.123')
    assert session.calls[0][1]['term'] == '"10.1000/abc.123"[doi]'


def test_doi_with_a_double_quote_cannot_break_the_term(monkeypatch):
    enricher, session, _ = _make(monkeypatch, [FakeResponse(200, json_data=ESEARCH_JSON)])
    enricher._search_pmid_by_doi('10.1000/ab"c')
    assert session.calls[0][1]['term'] == '"10.1000/abc"[doi]'


def test_two_entries_sharing_a_pmid_are_both_resolved(monkeypatch):
    enricher, session, _ = _make(monkeypatch, [FakeResponse(200, content=PUBMED_XML)])
    wrong = _titled(UNRELATED_TITLE, pmid=PMID)
    right = _titled('A test article', pmid=PMID)
    results = enricher._enrich_by_pmid([(wrong, PMID), (right, PMID)])
    assert [r['enrichment_status'] for r in results] == ['title_check_failed', 'enriched']
    assert session.calls[0][1]['id'] == PMID  # fetched once, not twice
    assert enricher.stats['enriched'] == 1


# ------------------------------------------- "in press" title search (2026-10-02)

INPRESS_TITLE = 'Statin adherence after myocardial infarction in older adults'


def _published_xml(title=INPRESS_TITLE, surname='Garcia', pubtypes=('Journal Article',)):
    types = ''.join(f'<PublicationType>{t}</PublicationType>' for t in pubtypes)
    return f"""<?xml version="1.0"?>
<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>{PMID}</PMID>
<Article><Journal><JournalIssue><Volume>12</Volume><PubDate><Year>2025</Year></PubDate>
</JournalIssue><Title>Heart Journal</Title></Journal><ArticleTitle>{title}</ArticleTitle>
<Pagination><MedlinePgn>100-9</MedlinePgn></Pagination>
<AuthorList><Author><LastName>{surname}</LastName><Initials>M</Initials></Author></AuthorList>
<PublicationTypeList>{types}</PublicationTypeList></Article>
</MedlineCitation></PubmedArticle></PubmedArticleSet>""".encode()


def _inpress_entry(text=None, code='S7', authors='Garcia M, Doe J'):
    return {
        'taxonomy_code': code,
        'classification_reasoning': 'Not yet published.',
        'text': text or f'Garcia M, Doe J. {INPRESS_TITLE}. Heart Journal. In press.',
        'extracted_fields': {'title': INPRESS_TITLE, 'authors': authors, 'year': 'in press'},
    }


def _run_stage5(tmp_path, monkeypatch, entry, responses):
    enricher, session, _ = _make(monkeypatch, responses)
    path = tmp_path / 's4.json'
    path.write_text(json.dumps({'document_uid': 'x', 'entries': [entry]}))
    [result] = enricher.enrich_stage4_output(str(path))['entries']
    return result, session, enricher


def _found(xml=None):
    return [FakeResponse(200, json_data=ESEARCH_JSON),
            FakeResponse(200, content=xml or _published_xml())]


def test_in_press_phrase_matches_status_not_presentations_or_submissions():
    assert in_press_phrase('Doe J. A study. JAMA. In press.') == 'in press'
    assert in_press_phrase('Doe J. A study. JAMA 2025 (Accepted for publication)') == 'accepted'
    assert in_press_phrase('Doe J. A study. Epub ahead of print.') == 'epub ahead of print'
    assert in_press_phrase('Doe J. A study. Accepted for presentation, AHA 2024.') is None
    assert in_press_phrase('Doe J. A study. Accepted as a poster.') is None
    assert in_press_phrase('Doe J. A study. Submitted; in press pending revision.') is None
    assert in_press_phrase('Doe J. A study. Circulation. 2024;1:1.') is None


def test_in_press_s7_entry_is_found_by_title_recoded_and_annotated(tmp_path, monkeypatch):
    result, session, enricher = _run_stage5(tmp_path, monkeypatch, _inpress_entry(), _found())
    assert session.calls[0][1]['term'] == f'{INPRESS_TITLE}[ti]'
    assert result['enrichment_status'] == 'enriched'
    assert result['enrichment_source'] == 'title_search'
    assert result['taxonomy_code'] == 'S1'
    assert result['extracted_fields']['pmid'] == PMID
    assert result['extracted_fields']['year'] == 2025
    assert 'year' in result['enriched_fields']
    assert result['in_press_note'] == (
        f'Found in PubMed as PMID {PMID}, published 2025; the CV listed it as in press.')
    assert 'Moved from S7 to S1' in result['classification_reasoning']
    assert enricher.stats['in_press_resolved'] == 1


def test_published_review_and_case_report_take_their_own_codes(tmp_path, monkeypatch):
    for pubtypes, code in [(('Review',), 'S2'), (('Case Reports', 'Review'), 'S6')]:
        result, _, _ = _run_stage5(tmp_path, monkeypatch, _inpress_entry(),
                                   _found(_published_xml(pubtypes=pubtypes)))
        assert result['taxonomy_code'] == code


def test_weak_title_match_is_not_accepted(tmp_path, monkeypatch):
    xml = _published_xml(title='Statin adherence in children with familial hypercholesterolemia')
    result, _, enricher = _run_stage5(tmp_path, monkeypatch, _inpress_entry(), _found(xml))
    assert 'enrichment_data' not in result
    assert result['taxonomy_code'] == 'S1'  # #1166 text fallback, not the match
    assert result['enrichment_status'] == 'no_identifier'
    assert 'in_press_note' not in result
    assert enricher.stats['title_searches'] == 1


def test_title_match_with_no_shared_author_is_rejected(tmp_path, monkeypatch):
    result, _, _ = _run_stage5(tmp_path, monkeypatch, _inpress_entry(),
                               _found(_published_xml(surname='Okonkwo')))
    assert 'enrichment_data' not in result
    assert result['taxonomy_code'] == 'S1'  # #1166 text fallback, not the match
    assert 'in_press_note' not in result


def test_two_letter_surnames_count_as_shared_authors():
    assert shares_an_author('Li X, Doe J', ['Li X'])
    assert not shares_an_author('Doe J', ['Li X'])
    assert shares_an_author('', ['Li X'])


def test_accepted_inside_the_title_is_not_a_status(tmp_path, monkeypatch):
    entry = _inpress_entry(text='Garcia M. Socially accepted norms of statin use. JAMA. 2024;1:1.')
    entry['extracted_fields']['title'] = 'Socially accepted norms of statin use'
    result, session, _ = _run_stage5(tmp_path, monkeypatch, entry, [])
    assert session.calls == []
    assert 'in_press_note' not in result


def test_title_search_failure_leaves_the_entry_unenriched(tmp_path, monkeypatch):
    result, _, enricher = _run_stage5(tmp_path, monkeypatch, _inpress_entry(),
                                      [FakeResponse(400)])
    assert result['enrichment_status'] == 'no_identifier'
    assert 'enrichment_data' not in result
    assert result['taxonomy_code'] == 'S1'  # #1166 text fallback, not the match
    assert enricher.stats['api_errors'] == 1


def test_title_match_published_years_after_the_cv_year_is_another_paper(tmp_path, monkeypatch):
    entry = _inpress_entry()
    entry['extracted_fields']['year'] = '2021'  # PubMed says 2025
    result, _, _ = _run_stage5(tmp_path, monkeypatch, entry, _found())
    assert 'enrichment_data' not in result
    assert result['taxonomy_code'] == 'S1'  # #1166 text fallback, not the match
    assert 'PMID' not in result.get('in_press_note', '')  # the refused record is not cited


def test_publication_year_window():
    assert plausible_publication_year('2023', 2025)
    assert plausible_publication_year(2025, 2024)
    assert not plausible_publication_year('2021', 2025)
    assert plausible_publication_year('in press', 2025)
    assert plausible_publication_year(None, 2025)


PRESS_RELEASES = ('Sumner P, Vivian-Griffiths S, Boivin J. Exaggerations and caveats in '
                  'press releases and health-related science news. PLoS One. 2016;11(12):e0168217.')


def test_in_press_releases_is_a_title_idiom_not_a_status():
    # PMID 27978540: stage 4's title absent or spelled differently must not matter.
    assert in_press_phrase(PRESS_RELEASES) is None
    assert in_press_phrase(PRESS_RELEASES, 'Exaggerations and Caveats in Press Releases') is None


def test_phrase_in_a_differently_spelled_title_needs_a_second_match():
    text = 'Garcia M. SOCIALLY ACCEPTED NORMS of statin use. JAMA. 2024;1:1.'
    assert in_press_phrase(text, 'Socially accepted norms of statin use') is None
    assert in_press_phrase(text + ' Accepted.', 'Socially accepted norms of statin use') == 'accepted'


def test_accepted_abstract_is_a_conference_abstract():
    assert in_press_phrase('Doe J. Hard metal lung disease. Accepted abstract, ATS 2024.') is None


def test_status_before_a_word_still_counts():
    # Corpus shapes: the status is followed by an identifier or a venue, not punctuation.
    assert in_press_phrase('Doe J. A sling trial. J Urol. In press PMID: 26820550') == 'in press'
    assert in_press_phrase('Doe J. Learning. [In Press in the 2022 Proceedings of X]') == 'in press'


def test_pubmed_title_idioms_are_not_statuses():
    # From the 2,496 PubMed titles holding a trigger phrase (2026-10-02 sweep).
    for text in ('Constructing the image of China in press conference interpreting. J X. 2020;1:1.',
                 'Bone stress in press-fit femoral knee implants. J Biomech. 2021;1:1.',
                 'Unmasking disparities in Press Ganey surveys. J Y. 2022;1:1.',
                 'Microwave ablation can be accepted as a standard treatment. J Z. 2019;1:1.',
                 'Recommendations made and accepted by a stewardship program. J Z. 2019;1:1.'):
        assert in_press_phrase(text) is None, text


def test_accepted_status_shapes_still_count():
    for text, phrase in (('Doe J. A study. JAMA. Accepted.', 'accepted'),
                         ('Doe J. A study. JAMA (accepted 9/2021)', 'accepted'),
                         ('Doe J. A study. Accepted for publication in J Am Coll Surg.', 'accepted'),
                         ('Doe J. A study. Mov Disord. 2022 Accepted Author Manuscript.', 'accepted'),
                         ('Doe J. A study. Pancreas. [Epub ahead of print].', 'epub ahead of print')):
        assert in_press_phrase(text) == phrase, text


def test_untitled_entry_is_not_treated_as_in_press(tmp_path, monkeypatch):
    # Without a title, a letter titled 'Re: ... Eur Urol. In press.' cannot be
    # told from a status, and there is nothing to search by anyway.
    # The entry has a PMID, so the ID path enriches it; only the in-press
    # note, year and recode are withheld.
    entry = _inpress_entry(text='Doe J. Re: Smith A. A trial. Eur Urol. In press. PMID: 12345678')
    entry['extracted_fields'].update(title='', pmid=PMID)
    result, _, _ = _run_stage5(tmp_path, monkeypatch, entry,
                               [FakeResponse(200, content=_published_xml())])
    assert result['enrichment_status'] == 'enriched'
    assert result['taxonomy_code'] == 'S7'
    assert 'in_press_note' not in result


def _published_entry(**fields):
    return {'taxonomy_code': 'S1', 'text': 'Garcia M. Published version.',
            'extracted_fields': {'authors': 'Garcia M', **fields}}


def test_in_press_entry_already_listed_as_published_is_marked_superseded(tmp_path, monkeypatch):
    # web200: the same paper listed both as published and as "in press".
    # The published entry's own title search (#1598) finds nothing here, so
    # the duplicate is told by title and year.
    enricher, _, _ = _make(monkeypatch, _NO_HITS + _found())
    path = tmp_path / 's4.json'
    published = _published_entry(title=INPRESS_TITLE, year='2025')
    path.write_text(json.dumps({'document_uid': 'x', 'entries': [_inpress_entry(), published]}))
    result = enricher.enrich_stage4_output(str(path))['entries'][0]
    assert result['in_press_superseded'] is True
    assert result['in_press_note'] == (
        f'Already listed as published (PMID {PMID}); the CV also listed it as in press.')
    assert result['taxonomy_code'] == 'S7'  # left alone: stage 6 deletes it
    assert enricher.stats['in_press_duplicates'] == 1
    assert enricher.stats['in_press_resolved'] == 0


def test_same_pmid_elsewhere_is_a_duplicate_but_similar_title_other_year_is_not(tmp_path, monkeypatch):
    # A PMID on the other entry costs one efetch before the title search; an
    # S1 entry without one is title-searched first (#1598), finding nothing.
    pmid_lookup = [FakeResponse(200, content=_published_xml())]
    for other, superseded, extra in (
            (_published_entry(title=INPRESS_TITLE, pmid=PMID), True, pmid_lookup),
            (_published_entry(title=INPRESS_TITLE, year='2019'), False, _NO_HITS),
            # web228: a shorter title nested in the in-press one, same year.
            (_published_entry(title='Statin adherence in older adults', year='2025'), False, _NO_HITS),
            (dict(_published_entry(title=INPRESS_TITLE, year='2025'), taxonomy_code='S8'), False, [])):
        enricher, _, _ = _make(monkeypatch, extra + _found())
        path = tmp_path / 's4.json'
        path.write_text(json.dumps({'document_uid': 'x', 'entries': [_inpress_entry(), other]}))
        result = enricher.enrich_stage4_output(str(path))['entries'][0]
        assert result.get('in_press_superseded', False) is superseded, other


# ------------------------- #1166: in press, but PubMed found nothing

def _unmatched(text, code='S7', **fields):
    entry = {'taxonomy_code': code, 'classification_reasoning': 'Not yet published.', 'text': text,
             'extracted_fields': {'title': INPRESS_TITLE, 'authors': 'Garcia M', **fields}}
    return entry


_NO_HITS = [FakeResponse(200, json_data={'esearchresult': {'idlist': []}})]


def test_unmatched_in_press_article_leaves_s7_for_s1_with_its_journal(tmp_path, monkeypatch):
    entry = _unmatched(f'Garcia M. {INPRESS_TITLE}. Heart Journal. In press.', target_journal='Heart Journal')
    result, _, enricher = _run_stage5(tmp_path, monkeypatch, entry, _NO_HITS)
    assert result['taxonomy_code'] == 'S1'
    assert result['extracted_fields']['journal'] == 'Heart Journal'
    assert 'Moved from S7 to S1' in result['classification_reasoning']
    assert 'in_press_note' not in result  # nothing was changed in the faculty's text
    assert enricher.stats['in_press_promoted'] == 1


def test_stale_unmatched_in_press_entry_carries_a_note(tmp_path, monkeypatch):
    """#1871: an in-press entry years old that PubMed never matched gets a Word
    comment; it is still promoted (#1166)."""
    entry = _unmatched(f'Garcia M. {INPRESS_TITLE}. Heart Journal. 2001, in press.',
                       target_journal='Heart Journal', year='2001')
    result, _, enricher = _run_stage5(tmp_path, monkeypatch, entry, _NO_HITS)
    assert result['taxonomy_code'] == 'S1'
    assert result['in_press_note'].startswith('The CV lists this as in press (2001)')
    assert enricher.stats['in_press_stale'] == 1


def test_stale_in_press_note_only_past_the_window():
    assert stage5.stale_in_press_note('in press', '2024', 2026)
    assert stage5.stale_in_press_note('accepted', 2010, 2026)
    assert stage5.stale_in_press_note('in press', '2025', 2026) is None
    assert stage5.stale_in_press_note('in press', '2026', 2026) is None
    assert stage5.stale_in_press_note('in press', None, 2026) is None
    assert stage5.stale_in_press_note('in press', '2019-2020', 2026) is None


def test_unmatched_in_press_chapter_goes_to_s4():
    for text in (f'Garcia M. {INPRESS_TITLE}. In: Doe J, ed. Cardiology. Springer. In press.',
                 f'Garcia M. {INPRESS_TITLE}. Doe J, Roe K (eds.) Cardiology. In press.',
                 f'Garcia M. {INPRESS_TITLE}. Chapter 4 in Cardiology. Accepted.',
                 # web200's shapes: "In;" and an unclosed "(eds Name" / "(ed Name)".
                 'Wessely S. Fatigue. In; "Neurological Rehabilitation". (eds Greenwood, Barnes). In press.',
                 'Wessely S. A UK perspective. In;Current Topics (ed Venables). OUP, in press'):
        assert stage5.CHAPTER_PATTERN.search(text), text
    assert not stage5.CHAPTER_PATTERN.search(f'Garcia M. {INPRESS_TITLE}. Heart Journal. In press.')


def test_unmatched_chapter_is_recoded_s4_without_a_journal(tmp_path, monkeypatch):
    entry = _unmatched(f'Garcia M. {INPRESS_TITLE}. In: Doe J, ed. Cardiology. In press.',
                       target_journal='Cardiology')
    result, _, _ = _run_stage5(tmp_path, monkeypatch, entry, _NO_HITS)
    assert result['taxonomy_code'] == 'S4'
    assert 'journal' not in result['extracted_fields']


def test_fallback_leaves_submitted_and_non_s7_entries_alone(tmp_path, monkeypatch):
    submitted = _unmatched(f'Garcia M. {INPRESS_TITLE}. Submitted; in press pending revision.')
    result, session, _ = _run_stage5(tmp_path, monkeypatch, submitted, [])
    assert session.calls == [] and result['taxonomy_code'] == 'S7'
    chapter_s3 = _unmatched(f'Garcia M. {INPRESS_TITLE}. Springer. In press.', code='S3')
    result, _, _ = _run_stage5(tmp_path, monkeypatch, chapter_s3, _NO_HITS)
    assert result['taxonomy_code'] == 'S3'


def test_accepted_conference_abstract_is_not_replaced_by_the_journal_paper(tmp_path, monkeypatch):
    # SDEBQJ (dev-239): "Organization for Human Brain Mapping ... (Accepted)." on
    # an S8 abstract was title-searched and overwritten by the later article.
    entry = _inpress_entry(text=f'Garcia M. {INPRESS_TITLE}. OHBM, Vancouver. (Accepted).', code='S8')
    result, session, _ = _run_stage5(tmp_path, monkeypatch, entry, _found())
    assert session.calls == []
    assert result['taxonomy_code'] == 'S8'
    assert 'in_press_note' not in result and 'enrichment_data' not in result


def test_title_search_ignores_a_preprint_record(tmp_path, monkeypatch):
    # QZWBKQ (dev-239): "App Environ Microbiology, in press" matched the bioRxiv record.
    xml = _published_xml(pubtypes=('Preprint', 'Journal Article'))
    result, _, _ = _run_stage5(tmp_path, monkeypatch, _inpress_entry(), _found(xml))
    assert 'enrichment_data' not in result and 'in_press_note' not in result


# ------------------- EBYSBC E19: wrong text written into accepted citations

PAPER_PMID, NOTICE_PMID = '23456789', '34567890'


def _article(pmid, title, pubtypes=('Journal Article',), authors=('Garcia M',), year=2025):
    """One PubmedArticle; `title` is raw XML, so it may hold inline markup."""
    names = ''.join(f'<Author><LastName>{a.rsplit(" ", 1)[0]}</LastName>'
                    f'<Initials>{a.rsplit(" ", 1)[1]}</Initials></Author>' for a in authors)
    types = ''.join(f'<PublicationType>{t}</PublicationType>' for t in pubtypes)
    return (f'<PubmedArticle><MedlineCitation><PMID>{pmid}</PMID><Article>'
            f'<Journal><JournalIssue><PubDate><Year>{year}</Year></PubDate></JournalIssue>'
            f'<Title>Heart Journal</Title></Journal><ArticleTitle>{title}</ArticleTitle>'
            f'<AuthorList>{names}</AuthorList><PublicationTypeList>{types}</PublicationTypeList>'
            f'</Article></MedlineCitation></PubmedArticle>')


def _article_set(*articles):
    return FakeResponse(200, content=f'<PubmedArticleSet>{"".join(articles)}</PubmedArticleSet>'.encode())


def _hits(*pmids):
    return FakeResponse(200, json_data={'esearchresult': {'idlist': list(pmids)}})


def _run_entries(tmp_path, monkeypatch, entries, responses):
    enricher, session, _ = _make(monkeypatch, responses)
    path = tmp_path / 's4.json'
    path.write_text(json.dumps({'document_uid': 'x', 'entries': entries}))
    return enricher.enrich_stage4_output(str(path))['entries'], session, enricher


def test_article_title_keeps_the_text_inside_inline_markup(monkeypatch):
    # QNZADH, AKPQEB (dev-242): `.text` stopped at <i>, so the stored title
    # was "Control of " and stage 6 printed it in place of the CV's.
    title = 'Control of <i>Fooella</i> growth by Ca<sup>2+</sup> in <b>vitro</b>'
    enricher, _, _ = _make(monkeypatch, [_article_set(_article(PMID, title))])
    record = enricher._fetch_pubmed_batch([PMID])[PMID]
    assert record['title'] == 'Control of Fooella growth by Ca2+ in vitro'


@pytest.mark.parametrize('notice', ['Published Erratum', 'Retraction Notice', 'Expression of Concern'])
def test_title_search_takes_the_paper_not_a_notice_about_it(tmp_path, monkeypatch, notice):
    # QNZADH (dev-242): an "accepted" article was matched to its erratum,
    # whose title is the paper's with "Correction:" in front.
    notice_record = _article(NOTICE_PMID, f'Correction: {INPRESS_TITLE}', pubtypes=(notice,))
    paper = _article(PAPER_PMID, INPRESS_TITLE)
    [result], _, _ = _run_entries(tmp_path, monkeypatch, [_inpress_entry()], [
        _hits(NOTICE_PMID, PAPER_PMID), _article_set(notice_record, paper)])
    assert result['enrichment_status'] == 'enriched'
    assert result['enrichment_data']['pubmed_pmid'] == PAPER_PMID


def test_title_search_with_only_an_erratum_leaves_the_entry_unmatched(tmp_path, monkeypatch):
    notice_record = _article(NOTICE_PMID, f'Correction: {INPRESS_TITLE}', pubtypes=('Published Erratum',))
    [result], _, _ = _run_entries(tmp_path, monkeypatch, [_inpress_entry()], [
        _hits(NOTICE_PMID), _article_set(notice_record)])
    assert 'enrichment_data' not in result and 'in_press_note' not in result


def test_title_search_still_matches_an_editorial_typed_comment(tmp_path, monkeypatch):
    # An editorial is typed Comment; two of the three Comment-typed
    # title-search matches in the EBYSBC farm were the CV's own commentary.
    editorial = _article(PAPER_PMID, INPRESS_TITLE, pubtypes=('Editorial', 'Comment'))
    [result], _, _ = _run_entries(tmp_path, monkeypatch, [_inpress_entry()], [
        _hits(PAPER_PMID), _article_set(editorial)])
    assert result['enrichment_data']['pubmed_pmid'] == PAPER_PMID


@pytest.mark.parametrize('comment_first', [True, False])
def test_title_search_takes_the_paper_over_an_equal_comment_on_it(tmp_path, monkeypatch,
                                                                  comment_first):
    # QSWXKR (dev-242): a "Discussion of:" piece, typed Comment, came ahead of
    # the paper in the hit list at the same overlap and was taken for it.
    comment = (NOTICE_PMID, _article(NOTICE_PMID, f'Discussion of: {INPRESS_TITLE}',
                                     pubtypes=('Journal Article', 'Comment')))
    paper = (PAPER_PMID, _article(PAPER_PMID, INPRESS_TITLE))
    hits = [comment, paper] if comment_first else [paper, comment]
    [result], _, _ = _run_entries(tmp_path, monkeypatch, [_inpress_entry()], [
        _hits(*(pmid for pmid, _ in hits)), _article_set(*(xml for _, xml in hits))])
    assert result['enrichment_data']['pubmed_pmid'] == PAPER_PMID


def test_title_search_keeps_a_comment_that_matches_better_than_the_other_hits(tmp_path, monkeypatch):
    # The rank is by overlap first: a weaker non-Comment hit (6 of 7 words)
    # does not displace a Comment that is the full title.
    comment = _article(NOTICE_PMID, INPRESS_TITLE, pubtypes=('Journal Article', 'Comment'))
    weaker = _article(PAPER_PMID, 'Statin adherence after myocardial infarction in younger adults')
    [result], _, _ = _run_entries(tmp_path, monkeypatch, [_inpress_entry()], [
        _hits(PAPER_PMID, NOTICE_PMID), _article_set(weaker, comment)])
    assert result['enrichment_data']['pubmed_pmid'] == NOTICE_PMID


# One DOI printed on two papers (AQCLHS, dev-242): the PubMed record is the
# first paper's, and the second paper's title shares 2 of its 5 words (0.4).
SHARED_TITLE = 'Heron migration along the northern coast in spring'
OTHER_PAPER_TITLE = 'Heron migration counts in a southern wetland'


def _paper_entry(title, idx, doi=DOI):
    return {'element_idx_start': idx, 'taxonomy_code': 'S1', 'text': f'Garcia M. {title}.',
            'extracted_fields': {'title': title, 'authors': 'Garcia M', 'doi': doi}}


def _doi_found():
    return [_hits(PAPER_PMID), _article_set(_article(PAPER_PMID, SHARED_TITLE))]


@pytest.mark.parametrize('order', [(0, 1), (1, 0)])
def test_shared_pmid_stays_with_the_paper_it_names(tmp_path, monkeypatch, order):
    pair = [_paper_entry(SHARED_TITLE, 10), _paper_entry(OTHER_PAPER_TITLE, 11)]
    entries = [pair[i] for i in order]
    results, _, enricher = _run_entries(tmp_path, monkeypatch, entries, _doi_found() * 2)
    by_idx = {r['element_idx_start']: r for r in results}
    assert by_idx[10]['enrichment_status'] == 'enriched'
    other = by_idx[11]
    assert other['enrichment_status'] == 'title_check_failed'
    assert other['enrichment_rejected'] == {
        'source': 'doi_search', 'pubmed_pmid': PAPER_PMID, 'pubmed_title': SHARED_TITLE,
        'title_word_overlap': 0.4, 'shared_pmid_with': 10}
    # Back on its own CV fields: nothing PubMed merged in survives.
    assert other['extracted_fields'] == _paper_entry(OTHER_PAPER_TITLE, 11)['extracted_fields']
    assert not {'enrichment_data', 'enrichment_source', 'enriched_fields'} & other.keys()
    assert (enricher.stats['enriched'], enricher.stats['title_mismatches'],
            enricher.stats['failed_lookups']) == (1, 1, 1)


def test_one_pmcid_cited_for_two_papers_stays_with_the_paper_it_names(tmp_path, monkeypatch):
    # #1219's CHXRBM case: one PMCID given for two papers that share topic words.
    entries = [dict(_paper_entry(t, i), extracted_fields={'title': t, 'pmcid': PMCID})
               for t, i in ((OTHER_PAPER_TITLE, 11), (SHARED_TITLE, 10))]
    results, _, _ = _run_entries(tmp_path, monkeypatch, entries, [
        FakeResponse(200, json_data={'records': [{'pmcid': PMCID, 'pmid': PAPER_PMID}]}),
        _article_set(_article(PAPER_PMID, SHARED_TITLE))])
    assert [r['enrichment_status'] for r in results] == ['title_check_failed', 'enriched']
    assert 'pmid' not in results[0]['extracted_fields']


@pytest.mark.parametrize('second_title', [
    'Heron migration along northern beaches',  # 4 of 5 words: 0.8
    'Heron migration along wetlands',  # 3 of 4: 0.75
])
def test_second_holder_of_a_shared_pmid_is_released_even_as_the_same_paper(tmp_path, monkeypatch,
                                                                         second_title):
    # #1598: a weaker holder that is not tied is released. A paper listed twice (once retitled
    # slightly) used to keep it on both from MIN_TITLE_SEARCH_OVERLAP up.
    entries = [_paper_entry(SHARED_TITLE, 10), _paper_entry(second_title, 11)]
    results, _, _ = _run_entries(tmp_path, monkeypatch, entries, _doi_found() * 2)
    assert [r['enrichment_status'] for r in results] == ['enriched', 'title_check_failed']
    assert results[1]['enrichment_rejected']['shared_pmid_with'] == 10


def test_untitled_entry_sharing_a_pmid_is_left_alone(tmp_path, monkeypatch):
    untitled = _paper_entry('', 11)
    results, _, _ = _run_entries(tmp_path, monkeypatch, [_paper_entry(SHARED_TITLE, 10), untitled],
                                 _doi_found() * 2)
    assert [r['enrichment_status'] for r in results] == ['enriched', 'enriched']


def test_a_pmid_held_in_another_document_is_not_shared(tmp_path, monkeypatch):
    # One enricher, two documents: the first document's better match must not
    # release the second document's entry (and must not be edited by it).
    enricher, _, _ = _make(monkeypatch, _doi_found() * 2)
    outputs = []
    for title in (SHARED_TITLE, OTHER_PAPER_TITLE):
        path = tmp_path / 's4.json'
        path.write_text(json.dumps({'document_uid': 'x', 'entries': [_paper_entry(title, 10)]}))
        outputs.append(enricher.enrich_stage4_output(str(path))['entries'][0])
    assert [e['enrichment_status'] for e in outputs] == ['enriched', 'enriched']


# An in-press listing whose DOI names the paper the CV also lists as
# published, under the title it was accepted with (4 of 7 words: 0.57).
RETITLED_IN_PRESS = 'Statin adherence and outcomes after infarction among elderly patients'


def _in_press_with_doi(title, idx=20):
    return {'element_idx_start': idx, 'taxonomy_code': 'S7',
            'text': f'Garcia M, Doe J. {title}. Heart Journal. In press.',
            'extracted_fields': {'title': title, 'authors': 'Garcia M, Doe J',
                                 'year': 'in press', 'doi': DOI}}


def test_weaker_in_press_holder_of_a_shared_pmid_is_superseded_not_released(tmp_path, monkeypatch):
    # Releasing it would promote it from S7 and print the paper twice.
    published = dict(_published_entry(title=INPRESS_TITLE, pmid=PMID), element_idx_start=10)
    responses = [FakeResponse(200, content=_published_xml())] + _found()
    results, _, _ = _run_entries(tmp_path, monkeypatch,
                                 [published, _in_press_with_doi(RETITLED_IN_PRESS)], responses)
    in_press = results[1]
    assert [r['enrichment_status'] for r in results] == ['enriched', 'enriched']
    assert (in_press['taxonomy_code'], in_press['in_press_superseded']) == ('S7', True)


def test_in_press_holder_of_a_shared_pmid_still_counts_as_its_best_match(tmp_path, monkeypatch):
    # A published entry with a weaker title (3 of 6 words) is released to the
    # in-press entry that names the paper, which then resolves as published.
    published = {'element_idx_start': 10, 'taxonomy_code': 'S1', 'text': 'Garcia M. Other paper.',
                 'extracted_fields': {'title': 'Statin adherence in younger adults with diabetes',
                                      'authors': 'Garcia M', 'year': '2025', 'doi': DOI}}
    results, _, _ = _run_entries(tmp_path, monkeypatch,
                                 [published, _in_press_with_doi(INPRESS_TITLE)], _found() * 2)
    released, in_press = results
    assert released['enrichment_status'] == 'title_check_failed'
    assert released['enrichment_rejected']['shared_pmid_with'] == 20
    assert in_press['enrichment_status'] == 'enriched'
    assert (in_press['taxonomy_code'], in_press.get('in_press_superseded')) == ('S1', None)


def test_equal_weak_matches_of_one_citation_both_keep_a_shared_pmid():
    # Two holders at the same strength giving one DOI and title: the same
    # citation listed twice keeps the PMID on both, for stage 6 dedup (#1598).
    enricher = PubMedEnricher(verbose=False)
    record = {'pmid': PAPER_PMID, 'title': SHARED_TITLE, 'vernacular_title': '', 'authors': []}
    entries = [_paper_entry(OTHER_PAPER_TITLE, i) for i in (10, 11)]
    for entry in entries:
        assert enricher._accept_record(entry, record, 'doi_search')
    enricher._release_weaker_shared_pmids()
    assert [e['enrichment_status'] for e in entries] == ['enriched', 'enriched']
    assert enricher.stats['enriched'] == 2


# ------------- #1598: every article without an identifier is searched by title

def _article_entry(idx, code='S1', title=SHARED_TITLE, year='2025', **ids):
    return {'element_idx_start': idx, 'taxonomy_code': code, 'text': f'Garcia M. {title}. {year}.',
            'extracted_fields': {'title': title, 'authors': 'Garcia M', 'year': year, **ids}}


@pytest.mark.parametrize('code', ['S1', 'S2', 'S6'])
def test_article_without_an_identifier_is_found_by_title(tmp_path, monkeypatch, code):
    [result], session, enricher = _run_entries(tmp_path, monkeypatch, [_article_entry(10, code)],
                                               _doi_found())
    assert session.calls[0][1]['term'] == f'{SHARED_TITLE}[ti]'
    assert (result['enrichment_status'], result['enrichment_source']) == ('enriched', 'title_search')
    assert result['extracted_fields']['pmid'] == PAPER_PMID
    assert result['taxonomy_code'] == code
    assert 'in_press_note' not in result
    assert (enricher.stats['title_searches'], enricher.stats['enriched']) == (1, 1)


def test_article_with_only_a_chapter_title_is_searched_by_it(tmp_path, monkeypatch):
    entry = _article_entry(10)
    entry['extracted_fields']['chapter_title'] = entry['extracted_fields'].pop('title')
    [result], session, _ = _run_entries(tmp_path, monkeypatch, [entry], _doi_found())
    assert session.calls[0][1]['term'] == f'{SHARED_TITLE}[ti]'
    assert result['enrichment_status'] == 'enriched'


@pytest.mark.parametrize('code', ['S4', 'S5', 'S8', 'S9'])
def test_non_article_publication_is_not_searched_by_title(tmp_path, monkeypatch, code):
    # S4/S8/S9 matches were about half wrong in the #1598 measurement: an
    # abstract or chapter matched to the same group's later article.
    [result], session, _ = _run_entries(tmp_path, monkeypatch, [_article_entry(10, code)], [])
    assert session.calls == []
    assert result['enrichment_status'] == 'no_identifier'


def test_article_whose_doi_pubmed_lacks_is_searched_by_title(tmp_path, monkeypatch):
    entry = _article_entry(10, doi=DOI)
    [result], session, _ = _run_entries(tmp_path, monkeypatch, [entry],
                                        [_hits()] + _doi_found())
    assert session.calls[1][1]['term'] == f'{SHARED_TITLE}[ti]'
    assert (result['enrichment_status'], result['enrichment_source']) == ('enriched', 'title_search')


def test_article_title_search_keeps_the_author_guard(tmp_path, monkeypatch):
    found = [_hits(PAPER_PMID), _article_set(_article(PAPER_PMID, SHARED_TITLE, authors=('Okonkwo A',)))]
    [result], _, _ = _run_entries(tmp_path, monkeypatch, [_article_entry(10)], found)
    assert result['enrichment_status'] == 'no_identifier'
    assert 'enrichment_data' not in result


# #1619: the guards ran on the single best hit only, so a tied hit that
# passes them was never tried.
SIBLING_PMID = '45678901'
_GUARD_FAILURES = {'author': {'authors': ('Okonkwo A',)}, 'year': {'year': 2011}}


@pytest.mark.parametrize('guard', sorted(_GUARD_FAILURES))
def test_tied_title_hit_passing_the_guards_is_taken_when_the_first_fails(tmp_path, monkeypatch,
                                                                         guard):
    refused = _article(SIBLING_PMID, SHARED_TITLE, **_GUARD_FAILURES[guard])
    found = [_hits(SIBLING_PMID, PAPER_PMID),
             _article_set(refused, _article(PAPER_PMID, SHARED_TITLE))]
    [result], _, _ = _run_entries(tmp_path, monkeypatch, [_article_entry(10)], found)
    assert (result['enrichment_status'], result['enrichment_source']) == ('enriched', 'title_search')
    assert result['enrichment_data']['pubmed_pmid'] == PAPER_PMID


def test_tied_title_hits_both_passing_keep_pubmeds_order(tmp_path, monkeypatch):
    found = [_hits(SIBLING_PMID, PAPER_PMID),
             _article_set(_article(SIBLING_PMID, SHARED_TITLE), _article(PAPER_PMID, SHARED_TITLE))]
    [result], _, _ = _run_entries(tmp_path, monkeypatch, [_article_entry(10)], found)
    assert result['enrichment_data']['pubmed_pmid'] == SIBLING_PMID


def test_weaker_title_hit_passing_the_guards_is_taken_when_the_best_fails(tmp_path, monkeypatch):
    # 6 of the title's 7 counted words: 0.86, above MIN_TITLE_SEARCH_OVERLAP.
    weaker_title = SHARED_TITLE.replace('spring', 'autumn')
    refused = _article(SIBLING_PMID, SHARED_TITLE, authors=('Okonkwo A',))
    found = [_hits(SIBLING_PMID, PAPER_PMID),
             _article_set(refused, _article(PAPER_PMID, weaker_title))]
    [result], _, _ = _run_entries(tmp_path, monkeypatch, [_article_entry(10)], found)
    assert result['enrichment_data']['pubmed_pmid'] == PAPER_PMID


def test_title_hit_below_the_overlap_floor_is_not_taken_when_the_best_fails(tmp_path, monkeypatch):
    refused = _article(SIBLING_PMID, SHARED_TITLE, authors=('Okonkwo A',))
    found = [_hits(SIBLING_PMID, PAPER_PMID),
             _article_set(refused, _article(PAPER_PMID, OTHER_PAPER_TITLE))]
    [result], _, _ = _run_entries(tmp_path, monkeypatch, [_article_entry(10)], found)
    assert result['enrichment_status'] == 'no_identifier'
    assert 'enrichment_data' not in result


def test_in_press_article_is_searched_once_by_the_in_press_step(tmp_path, monkeypatch):
    entry = _inpress_entry(code='S1')
    result, session, enricher = _run_stage5(tmp_path, monkeypatch, entry, _found())
    assert len(session.calls) == 2  # one esearch, one efetch
    assert enricher.stats['title_searches'] == 1
    assert result['in_press_note'].startswith(f'Found in PubMed as PMID {PMID}')


def test_in_press_article_is_left_to_the_in_press_step(tmp_path, monkeypatch):
    # A published entry whose DOI names the paper under its pre-acceptance
    # title (0.57) keeps it: the in-press listing, matched by title after the
    # shared-PMID release as before #1598, is the stale duplicate.
    published = _paper_entry(RETITLED_IN_PRESS, 10)
    in_press = dict(_inpress_entry(code='S1'), element_idx_start=20)
    responses = [_hits(PMID), FakeResponse(200, content=_published_xml())] + _found()
    results, _, _ = _run_entries(tmp_path, monkeypatch, [published, in_press], responses)
    kept, superseded = results
    assert kept['enrichment_status'] == 'enriched'
    assert superseded['in_press_superseded'] is True


def test_title_match_of_a_paper_the_cv_cites_by_doi_is_released(tmp_path, monkeypatch):
    # An abstract or duplicate listing found by title gives way to the entry
    # whose CV identifier names the paper, at the same overlap and year.
    entries = [_article_entry(11), _paper_entry(SHARED_TITLE, 10)]
    entries[1]['extracted_fields']['year'] = '2025'
    results, _, enricher = _run_entries(tmp_path, monkeypatch, entries, _doi_found() * 2)
    titled, cited = results
    assert cited['enrichment_status'] == 'enriched'
    assert titled['enrichment_status'] == 'title_check_failed'
    assert titled['enrichment_rejected']['shared_pmid_with'] == 10
    assert titled['extracted_fields'] == _article_entry(11)['extracted_fields']
    assert enricher.stats['enriched'] == 1


def test_shared_pmid_stays_with_the_entry_giving_pubmeds_year(tmp_path, monkeypatch):
    # BLBVPD e848 (#1598): an exact-title holder a year off PubMed's was a
    # different paper; the holder giving PubMed's year was the paper.
    entries = [_article_entry(10, year='2024'), _article_entry(11, year='2025')]
    results, _, _ = _run_entries(tmp_path, monkeypatch, entries, _doi_found() * 2)
    assert [r['enrichment_status'] for r in results] == ['title_check_failed', 'enriched']
    assert results[0]['enrichment_rejected']['shared_pmid_with'] == 11


@pytest.mark.parametrize('years, status', [
    (('2025', '2025'), 'enriched'),  # one paper listed twice (YUYVIG, 11 groups)
    (('2023', '2024'), 'title_check_failed'),  # two citations, neither PubMed's year
])
def test_equal_title_matches_keep_a_shared_pmid_only_as_one_citation(tmp_path, monkeypatch,
                                                                     years, status):
    entries = [_article_entry(10, year=years[0]), _article_entry(11, year=years[1])]
    results, _, enricher = _run_entries(tmp_path, monkeypatch, entries, _doi_found() * 2)
    assert [r['enrichment_status'] for r in results] == [status] * 2
    assert enricher.stats['enriched'] == (2 if status == 'enriched' else 0)
    if status != 'enriched':
        assert [r['enrichment_rejected']['shared_pmid_with'] for r in results] == [11, 10]


NESTED_TITLE = 'Heron migration along the coast'  # overlap 1.0, not _same_title


@pytest.mark.parametrize('dois, status', [
    ((DOI, DOI.upper()), 'enriched'),  # ECXGAT e182/e287: one DOI cited twice
    ((None, None), 'title_check_failed'),
])
def test_tied_holders_with_different_titles_keep_a_pmid_only_by_one_identifier(
        tmp_path, monkeypatch, dois, status):
    entries = [dict(_article_entry(10), extracted_fields={
                   **_article_entry(10)['extracted_fields'], 'doi': dois[0]}),
               dict(_article_entry(11, title=NESTED_TITLE), extracted_fields={
                   **_article_entry(11, title=NESTED_TITLE)['extracted_fields'], 'doi': dois[1]})]
    results, _, _ = _run_entries(tmp_path, monkeypatch, entries, _doi_found() * 2)
    assert [r['enrichment_status'] for r in results] == [status] * 2


def _held(**fields):
    return stage5._Acceptance({}, {'extracted_fields': fields}, PAPER_PMID, SHARED_TITLE,
                              2025, 'doi_search', 1.0)


@pytest.mark.parametrize('pair, one', [
    (({'title': SHARED_TITLE, 'year': '2025'}, {'title': SHARED_TITLE, 'year': '2025'}), True),
    (({'title': SHARED_TITLE}, {'title': SHARED_TITLE}), True),
    (({'title': SHARED_TITLE, 'year': '2025'}, {'title': SHARED_TITLE, 'year': '2024'}), False),
    (({'title': SHARED_TITLE}, {'title': NESTED_TITLE}), False),
    (({'title': SHARED_TITLE}, {'chapter_title': SHARED_TITLE}), True),
    (({'title': SHARED_TITLE, 'doi': DOI}, {'title': NESTED_TITLE, 'doi': DOI.upper()}), True),
    (({'title': SHARED_TITLE, 'doi': DOI}, {'title': NESTED_TITLE, 'doi': '10.1000/x.9'}), False),
    (({'title': SHARED_TITLE, 'pmid': 'PMID: 23456789'}, {'title': NESTED_TITLE, 'pmid': '23456789'}),
     True),
    (({'title': SHARED_TITLE, 'pmcid': 'PMC123456'}, {'title': NESTED_TITLE, 'pmcid': '123456'}), True),
])
def test_one_citation_is_one_identifier_or_one_title_and_year(pair, one):
    assert PubMedEnricher(verbose=False)._one_citation([_held(**f) for f in pair]) is one


def test_one_citation_holds_for_every_pair_of_tied_holders():
    same, other = {'title': SHARED_TITLE, 'year': '2025'}, {'title': NESTED_TITLE, 'year': '2025'}
    enricher = PubMedEnricher(verbose=False)
    assert enricher._one_citation([_held(**same)])
    assert not enricher._one_citation([_held(**same), _held(**same), _held(**other)])


@pytest.mark.parametrize('in_press_year', ['in press', '2025'])
def test_published_holder_keeps_a_pmid_tied_with_an_in_press_one(tmp_path, monkeypatch, in_press_year):
    # Two citations (no identifier in common, different years), so the tie is
    # not kept as one: the published entry, matched by title search with a
    # year that is not PubMed's, ties the in-press DOI holder on overlap, and
    # the in-press rank keeps it, ahead of the year even when the in-press
    # entry gives PubMed's year ('2025'). Releasing it would leave the
    # in-press entry to be printed as the published paper.
    published = _paper_entry(INPRESS_TITLE, 10, doi=None)
    published['extracted_fields']['year'] = '2023'
    in_press = _in_press_with_doi(INPRESS_TITLE)
    in_press['extracted_fields']['year'] = in_press_year
    responses = [_hits(PMID), FakeResponse(200, content=_published_xml())] * 2
    results, _, _ = _run_entries(tmp_path, monkeypatch, [published, in_press], responses)
    kept, in_press = results
    assert kept['enrichment_status'] == 'enriched'
    assert kept['enrichment_data']['pubmed_pmid'] == PMID
    assert kept['enrichment_source'] == stage5.TITLE_SEARCH_SOURCE
    assert (in_press['taxonomy_code'], in_press['in_press_superseded']) == ('S7', True)


def test_cv_year_is_pubmeds_year():
    assert stage5.cv_year_is('2025', 2025) and stage5.cv_year_is(2025, 2025)
    assert stage5.cv_year_is('2024-2025', 2024)
    assert not stage5.cv_year_is('2024', 2025)
    assert not stage5.cv_year_is('in press', 2025)
    assert not stage5.cv_year_is(None, 2025) and not stage5.cv_year_is('2025', None)
    assert not stage5.cv_year_is(None, None)
    # A page range or volume ahead of the year is not read as one.
    assert stage5.cv_publication_year('1123-9, 2025') == 2025


# OIYKZE (dev-242): PubMed's list for the owner's paper held the first ten of
# 20+ names, and the owner, sixteenth in the CV, rendered nowhere.
CV_AUTHORS = 'Abel A, Brandt B, Cole C, Quill JA, Wren D'


def test_pubmed_list_cut_before_the_owner_is_detected():
    assert stage5.pubmed_list_drops_the_owner(CV_AUTHORS, 'Quill JA', ['Abel A', 'Brandt B', 'Cole C'])
    # "Last, F" CV lists and a list-typed authors field.
    assert stage5.pubmed_list_drops_the_owner(
        ['Abel, A', 'Brandt, B', 'Quill, J. A.'], 'Quill, J. A.', ['Abel A', 'Brandt B'])


def test_pubmed_list_naming_the_owner_or_another_name_is_kept():
    assert not stage5.pubmed_list_drops_the_owner(CV_AUTHORS, 'Quill JA', ['Abel A', 'Quill J'])
    # A name the CV lacks: another spelling or another record, not a cut.
    assert not stage5.pubmed_list_drops_the_owner(CV_AUTHORS, 'Quill JA', ['Abel A', 'Quille J'])
    assert not stage5.pubmed_list_drops_the_owner(CV_AUTHORS, '', ['Abel A'])
    # A CV list that does not name the owner either has nothing to restore.
    assert not stage5.pubmed_list_drops_the_owner('Abel A, Brandt B', 'Quill JA', ['Abel A'])
    assert not stage5.pubmed_list_drops_the_owner(CV_AUTHORS, 'Quill JA', [])


def test_owner_initials_are_not_read_as_a_surname():
    # "JA" is Quill's initials, not the coauthor surnamed Ja.
    assert stage5.pubmed_list_drops_the_owner('Ja K, Quill JA', 'Quill JA', ['Ja K'])


def _owner_entry():
    return {'taxonomy_code': 'S1', 'text': f'{CV_AUTHORS}. {INPRESS_TITLE}.',
            'extracted_fields': {'title': INPRESS_TITLE, 'authors': CV_AUTHORS,
                                 'target_name': 'Quill JA', 'pmid': PMID}}


def test_cv_author_list_renders_when_pubmed_cut_the_owner(monkeypatch):
    xml = _article_set(_article(PMID, INPRESS_TITLE, authors=('Abel A', 'Brandt B', 'Cole C')))
    enricher, _, _ = _make(monkeypatch, [xml])
    [result] = enricher._enrich_by_pmid([(_owner_entry(), PMID)])
    data = result['enrichment_data']
    assert data['pubmed_authors'] is None  # stage 6 falls back to the CV's list
    assert data['pubmed_authors_without_owner'] == 'Abel A, Brandt B, Cole C'


def test_pubmed_author_list_naming_the_owner_still_renders(monkeypatch):
    # As long as the CV's list: a shorter one renders the CV's (#1661).
    pubmed = ('Abel A', 'Brandt B', 'Cole C', 'Quill J', 'Wren D')
    xml = _article_set(_article(PMID, INPRESS_TITLE, authors=pubmed))
    enricher, _, _ = _make(monkeypatch, [xml])
    [result] = enricher._enrich_by_pmid([(_owner_entry(), PMID)])
    assert result['enrichment_data']['pubmed_authors'] == ', '.join(pubmed)
    assert 'pubmed_authors_without_owner' not in result['enrichment_data']


# #1661 (KRSFSI e468): MEDLINE indexed at most 10 authors through 1995, so
# PubMed's list cut a longer CV byline before the owner; a group credit or
# co-authors PubMed's record lacks cut it the same way.
CAPPED_CV = [f'{s} {i}' for s, i in (
    ('Abel', 'A'), ('Brandt', 'B'), ('Cole', 'C'), ('Dane', 'D'), ('Eno', 'E'), ('Frey', 'F'),
    ('Gale', 'G'), ('Hume', 'H'), ('Ives', 'I'), ('Jory', 'J'), ('Kemp', 'K'), ('Quill', 'JA'),
    ('Wren', 'D'))]
CAPPED_CV_TEXT = ', '.join(CAPPED_CV)
# PubMed's first ten: one surname spelled differently from the CV's.
CAPPED_PUBMED = CAPPED_CV[:7] + ['Hulme H'] + CAPPED_CV[8:10]
CAPPED_MERGED = ', '.join(CAPPED_PUBMED + CAPPED_CV[10:])
# Given names first, as initials.
SWAPPED_CV = [f'{a.split()[1]}. {a.split()[0]}' for a in CAPPED_CV]


def _restored(cv, pubmed, line=None):
    """Stage 5's CV byline over `pubmed`, for a CV line that writes `cv` as given."""
    line = (cv if isinstance(cv, str) else ', '.join(cv)) if line is None else line
    return stage5.cv_byline_over_shorter_pubmed_list(cv, pubmed, line)


def test_capped_list_takes_the_cv_names_after_pubmeds():
    assert _restored(CAPPED_CV_TEXT, CAPPED_PUBMED) == CAPPED_MERGED
    assert _restored(CAPPED_CV, CAPPED_PUBMED) == CAPPED_MERGED
    for sep in (' and ', '; ', ' & '):
        text = ', '.join(CAPPED_CV[:-1]) + sep + CAPPED_CV[-1]
        assert _restored(text, CAPPED_PUBMED) == CAPPED_MERGED
    # Blank and "et al." list items are not authors; a padded one is trimmed.
    padded = CAPPED_CV[:10] + ['et al.'] + CAPPED_CV[10:-1] + [f' {CAPPED_CV[-1]} ', '', '  ']
    assert _restored(padded, CAPPED_PUBMED) == CAPPED_MERGED


def test_capped_list_matches_names_written_differently():
    # Given names first, or full; a surname one letter off; "van der Berg"
    # written as one word; initials one of which starts the other's.
    assert _restored(SWAPPED_CV, CAPPED_PUBMED) == CAPPED_MERGED
    for first in ('Ann Bo Abel', 'Abel A.B.', 'ABEL A'):
        cv = [first] + CAPPED_CV[1:]
        assert _restored(cv, CAPPED_PUBMED) == CAPPED_MERGED
    pubmed = ['Abel AB'] + CAPPED_PUBMED[1:]
    assert _restored(CAPPED_CV, pubmed) == ', '.join(pubmed + CAPPED_CV[10:])
    for spelling in ('Hulm H', 'Hlme H', 'Hulmme H', 'Hulne H'):
        cv = CAPPED_CV[:7] + [spelling] + CAPPED_CV[8:]
        assert _restored(cv, CAPPED_PUBMED) == CAPPED_MERGED
    cv = CAPPED_CV[:9] + ['Jorry J'] + CAPPED_CV[10:]
    assert _restored(cv, CAPPED_PUBMED) == CAPPED_MERGED
    # A given name gives its initial; a capital word of up to three letters is initials.
    pubmed = CAPPED_PUBMED[:9] + ['Jory JH']
    for spelling in ('Jory Jo', 'JORY JOHN', 'Jory J. H.', 'Jory JH'):
        cv = CAPPED_CV[:9] + [spelling] + CAPPED_CV[10:]
        assert _restored(cv, pubmed) == ', '.join(pubmed + CAPPED_CV[10:])
    pubmed = CAPPED_PUBMED[:9] + ['van der Berg J']
    for spelling in ('Vanderberg J', 'Van der Berg J.'):
        cv = CAPPED_CV[:9] + [spelling] + CAPPED_CV[10:]
        assert _restored(cv, pubmed) == ', '.join(pubmed + CAPPED_CV[10:])


@pytest.mark.parametrize('cv_ten, name, styled', [
    (CAPPED_CV[:10], 'K. Kemp', 'Kemp K'), (CAPPED_CV[:10], 'J. A. Quill', 'Quill JA'),
    (CAPPED_CV[:10], 'JA Quill', 'Quill JA'), (CAPPED_CV[:10], 'Quill J.A.', 'Quill JA'),
    (CAPPED_CV[:10], 'van der Berg J.', 'van der Berg J'),
    (CAPPED_CV[:10], 'J. van der Berg', 'van der Berg J'),
    # Full given names reorder only in a CV that writes them first.
    (CAPPED_CV[:10], 'Kemp Karl', 'Kemp Karl'), (CAPPED_CV[:10], 'Karl Kemp', 'Karl Kemp'),
    (SWAPPED_CV[:10], 'Karl Kemp', 'Kemp K'), (SWAPPED_CV[:10], 'Karl J. Kemp', 'Kemp KJ'),
    (SWAPPED_CV[:1] + CAPPED_CV[1:10], 'Karl Kemp', 'Karl Kemp'),
    (SWAPPED_CV[:10], 'Juan Garcia Lopez', 'Juan Garcia Lopez'),
    (SWAPPED_CV[:10], 'Jean-Paul Kemp', 'Jean-Paul Kemp'),
    (CAPPED_CV[:10], 'Wren D ABCDEF', 'Wren D ABCDEF'),
    # A generational suffix is not initials: the name stays as the CV wrote it.
    (CAPPED_CV[:10], 'Quill JA III', 'Quill JA III'), (CAPPED_CV[:10], 'J. A. Quill II', 'J. A. Quill II'),
    (SWAPPED_CV[:10], ' The ABC Study Group ', 'The ABC Study Group'),
    # A group stage 4 keeps as written is not read as initials and a surname.
    (CAPPED_CV[:10], 'HIV Neuroimaging Consortium', 'HIV Neuroimaging Consortium')])
def test_cv_tail_takes_pubmeds_name_format(cv_ten, name, styled):
    assert _restored(cv_ten + [name], CAPPED_CV[:10]) == ', '.join(
        CAPPED_CV[:10] + [styled])


def test_cv_tail_in_a_string_is_trimmed():
    text = ', '.join(CAPPED_CV[:10]) + ', The ABC Study Group '
    assert _restored(text, CAPPED_CV[:10]) == ', '.join(
        CAPPED_CV[:10] + ['The ABC Study Group'])


def test_capped_list_tells_namesakes_apart_by_place():
    # PubMed's first and tenth share a surname: each matches the CV's name in
    # its own place.
    lee = ['Lee A'] + CAPPED_CV[1:9] + ['Lee J', 'Kemp K', 'Wren D']
    assert _restored(lee, lee[:10]) == ', '.join(lee)


# PubMed's ninth and tenth are namesakes; the CV omits the ninth, so its
# "Vann LJ" sits in PubMed's "Vann LH" place.
VANN_PUBMED = CAPPED_CV[:8] + ['Vann LH', 'Vann LJ']
VANN_CV = CAPPED_CV[:8] + ['Vann LJ', 'Vann LM', 'Kemp K', 'Quill JA']
# A 1997 record: the first 24 of the CV's 30 authors, then its last, which
# the CV writes "Kim S"; the CV's 25th, "Kim SH", is another author.
_THIRTY = [f'{s}{t} A' for s in ('Bal', 'Cor', 'Dun', 'Fes', 'Gil') for t in 'bdkmt'][:24]
KIM_CV = _THIRTY + ['Kim SH', 'Mott A', 'Nash A', 'Orr A', 'Pratt A', 'Kim S']
KIM_PUBMED = _THIRTY + ['Kim SJ']


@pytest.mark.parametrize('cv, pubmed, rendered', [
    # MEDLINE's 1996-1999 cap: the first 24 and the last author.
    (KIM_CV, KIM_PUBMED, KIM_CV[:-1] + ['Kim SJ']),
    # UAURFU e790: co-authors PubMed's record lacks, anywhere in the CV's list.
    (CAPPED_CV, CAPPED_CV[:3] + CAPPED_CV[5:9] + CAPPED_CV[11:], CAPPED_CV),
    # PubMed's list one longer than MEDLINE's pre-1996 ten, or one shorter.
    (CAPPED_CV, CAPPED_CV[:11], CAPPED_CV),
    (CAPPED_CV, CAPPED_PUBMED[:9], CAPPED_PUBMED[:9] + CAPPED_CV[9:]),
    # AOJEQD: a group credit PubMed keeps as no author, here naming the owner.
    (CAPPED_CV[:10] + ['ABC Investigators (Quill JA)'], CAPPED_CV[:10],
     CAPPED_CV[:10] + ['ABC Investigators (Quill JA)']),
    # The name PubMed lacks is the CV line's first.
    (['Hart L'] + CAPPED_CV[:10], CAPPED_CV[:10], ['Hart L'] + CAPPED_CV[:10]),
    # A name with a word that is also a credential or editor mark is a name.
    (CAPPED_CV[:10] + ['Ed Wren', 'Do T'], CAPPED_CV[:10], CAPPED_CV[:10] + ['Ed Wren', 'Do T']),
])
def test_shorter_pubmed_list_renders_the_cv_byline(cv, pubmed, rendered):
    assert _restored(cv, pubmed) == ', '.join(rendered)


@pytest.mark.parametrize('cv, pubmed', [
    (CAPPED_CV, None),
    (CAPPED_CV, []),
    (CAPPED_CV[:10] + ['', '  ', 'et al.'], CAPPED_PUBMED),  # nobody after PubMed's ten
    (', '.join(CAPPED_CV[:10]) + ', et al.', CAPPED_PUBMED),
    # A "Last, F." string's pieces are not one author each.
    (', '.join(f"{a.split()[0]}, {a.split()[1]}." for a in CAPPED_CV), CAPPED_PUBMED),
    (CAPPED_CV, CAPPED_PUBMED[:9] + ['Jory Z']),   # another initial
    (CAPPED_CV, CAPPED_PUBMED[:9] + ['Jory ZJ']),
    (CAPPED_CV[:9] + ['Jory JB'] + CAPPED_CV[10:], CAPPED_PUBMED[:9] + ['Jory JA']),
    (CAPPED_CV[:9] + ['Jory JAB'] + CAPPED_CV[10:], CAPPED_PUBMED[:9] + ['Jory JAH']),
    (CAPPED_CV_TEXT + ', MD', CAPPED_PUBMED),      # a piece that is no author
    (CAPPED_CV, CAPPED_PUBMED[:9] + ['Jorrey J']),  # two letters off
    (CAPPED_CV, CAPPED_PUBMED[:9] + ['Jo J']),
    (CAPPED_CV, CAPPED_PUBMED[:9] + ['Jry J']),     # one off, but three letters
    (CAPPED_CV, CAPPED_PUBMED[:9] + ['Jory']),     # no initials
    (CAPPED_CV[:9] + ['Jory'] + CAPPED_CV[10:], CAPPED_PUBMED),
    # PubMed's order is not the CV's, or one CV name stands for two of PubMed's.
    (CAPPED_CV, [CAPPED_CV[1], CAPPED_CV[0]]),
    (['Lee J', 'Kemp K'], ['Lee J', 'Lee J']),
    # A name the CV adds is one of PubMed's, however written.
    (CAPPED_CV[:10] + ['Hume H.', 'Wren D'], CAPPED_PUBMED),
    (CAPPED_CV[:10] + ['Kemp K', 'Abel A.'], CAPPED_PUBMED),
    (CAPPED_CV[:10] + ['Jane Jory'], CAPPED_PUBMED),
    (VANN_CV, VANN_PUBMED),
])
def test_pubmed_list_not_cut_from_the_cv_byline_is_none(cv, pubmed):
    assert _restored(cv, pubmed) is None


# GCFEBE e717: a CV note; RXYBVF e561: a suffix stage 4 cut from its name;
# M7NDD0 e27: degrees after each name; an editor mark.
@pytest.mark.parametrize('piece', [
    '(Equal 1st author contributorship)', 'Jr', 'jr.', 'Sr.', 'II', 'III', 'IV',
    'PhD', 'Ph.D.', 'M.D.', 'MD', 'MSc', 'MPH', 'MD PhD', 'Editors', 'Editor', 'Eds.', 'ed',
    # one word is no author, listed degree or not; several listed ones aren't either
    'MBChB', 'FRCSEd', 'PhD(c)', 'MSc(Med)', 'Hons', 'Ng', 'MD, PhD', 'Ed Eds', 'Sr Jr', 'II III', 'IV Editors', 'Editor Eds',
    # unlisted degrees by shape: mixed case, parenthesised, four or more capitals
    'MBChB PhD', 'PharmD BCPS', 'PhD ABPP', 'MD (Hons)', 'MD (Hons', 'MD MSc(Med)', 'MBChB MD',
    'MD DPhil', 'MPhil MD', 'PhDr MD', 'MD MScMed', 'MD FAAOS', 'MD FACMGG', 'MD MSc-PhD'])
def test_cv_piece_that_is_no_name_is_none(piece):
    assert _restored(CAPPED_CV[:10] + [piece], CAPPED_CV[:10]) is None


@pytest.mark.parametrize('credential', CREDENTIALS)
def test_every_listed_credential_is_no_name(credential):
    assert _restored(CAPPED_CV[:10] + [credential], CAPPED_CV[:10]) is None


def test_degrees_after_names_in_a_cv_string_are_none():
    # A ", PhD" after a name splits off as a piece with a lowercase letter.
    text = ', '.join(f'{name}, PhD' for name in CAPPED_CV[:4])
    assert _restored(text, CAPPED_CV[:4]) is None
    text = ', '.join(CAPPED_CV[:3]) + ', PhD, ' + CAPPED_CV[3]
    assert _restored(text, CAPPED_CV[:4]) is None
    assert _restored(text, CAPPED_CV[:3]) is None


def test_cv_name_the_cv_line_does_not_write_is_none():
    # AOJEQD e1004: stage 4 read "HVTN 505 Protocol Team" as "Team H5P".
    cv = CAPPED_CV[:10] + ['Team H5P']
    line = ', '.join(CAPPED_CV[:10]) + ', HVTN 505 Protocol Team'
    assert _restored(cv, CAPPED_CV[:10], line) is None
    # The words must be the line's, in its order.
    assert _restored(cv, CAPPED_CV[:10], ', '.join(CAPPED_CV[:10]) + ', H5P Team') is None
    assert _restored(cv, CAPPED_CV[:10], '') is None
    # Whole words: "Kemp K" is not written in "Sakemp K" or "Kemp Ka".
    for written in ('Sakemp K', 'Kemp Ka'):
        line = ', '.join(CAPPED_CV[:10] + [written])
        assert _restored(CAPPED_CV[:11], CAPPED_CV[:10], line) is None
    # One name that cannot render keeps PubMed's list, whatever the others.
    assert _restored(CAPPED_CV[:11] + ['Jr'], CAPPED_CV[:10]) is None
    # A name only starting like a suffix renders, written in another case, as
    # does a group named from a number.
    line = ', '.join(CAPPED_CV[:10]) + ', SRINIVASAN R, 5199 Study Team'
    more = ['Srinivasan R', '5199 Study Team']
    assert _restored(CAPPED_CV[:10] + more, CAPPED_CV[:10], line) == ', '.join(CAPPED_CV[:10] + more)


@pytest.mark.parametrize('name', ['Ma JD', 'Ma D', 'Deng D', 'Bao MS', 'Do MD', 'Ed Wren', 'Jr Quill', 'Lu Lu',
                                  'McDonald JD', 'DeLuca MS', 'LeBlanc MA', 'MacKay DM', 'Quill,J',
                                  'LEE JD', 'LI JA', 'DiMaggio MD', 'deLuca MD', 'McDonald-Smith MD', "'tHooft MD",
                                  'KEMPson MD', 'Curran-Everett D', 'Farr-Jones MA', 'Al-Khatib MS',
                                  'Ben-Ami D', "O'Neil-Ray JD", 'Garcia-LOPEZ MD', 'LOPEZ-Garcia MD'])
def test_a_surname_spelt_like_a_credential_still_renders(name):
    # The credential words are compared as written: "Ma" is a surname, "MA" a degree.
    assert _restored(CAPPED_CV[:10] + [name], CAPPED_CV[:10]) == ', '.join(CAPPED_CV[:10] + [name])


def _capped_entry(authors=CAPPED_CV_TEXT, target_name='Quill JA'):
    return {'taxonomy_code': 'S1', 'text': f'{authors}. {INPRESS_TITLE}.',
            'extracted_fields': {'title': INPRESS_TITLE, 'authors': authors,
                                 'target_name': target_name, 'pmid': PMID}}


@pytest.mark.parametrize('year', [1988, 2012])
def test_shorter_pubmed_list_renders_with_the_cv_names(monkeypatch, year):
    xml = _article_set(_article(PMID, INPRESS_TITLE, authors=CAPPED_PUBMED, year=year))
    enricher, _, _ = _make(monkeypatch, [xml])
    [result] = enricher._enrich_by_pmid([(_capped_entry(), PMID)])
    data = result['enrichment_data']
    # PubMed's ten, then the CV's names PubMed lacks, the owner among them.
    assert data['pubmed_authors'] == CAPPED_MERGED
    assert data['pubmed_authors_shorter'] == ', '.join(CAPPED_PUBMED)


def test_owner_drop_guard_wins_over_the_shorter_list(monkeypatch):
    # PubMed's ten are all in the CV's byline and the owner is past them: the
    # owner-drop guard renders the CV's own list, as on dev.
    xml = _article_set(_article(PMID, INPRESS_TITLE, authors=CAPPED_CV[:10], year=1988))
    enricher, _, _ = _make(monkeypatch, [xml])
    [result] = enricher._enrich_by_pmid([(_capped_entry(), PMID)])
    data = result['enrichment_data']
    assert data['pubmed_authors'] is None
    assert data['pubmed_authors_without_owner'] == ', '.join(CAPPED_CV[:10])
    assert 'pubmed_authors_shorter' not in data


# A CV byline that does not hold PubMed's list in PubMed's order leaves
# stage 5 as it was before #1661: PubMed's list renders unless it dropped the
# owner. The owner, "Owen", is past PubMed's ten in every CV but F1's.
_TEN = CAPPED_CV[:10]
_MISALIGNED = {
    'F1 owner first': ['Owen O'] + _TEN[:9] + ['Kemp K', 'Wren D'],
    'F2 ninth and tenth swapped': _TEN[:8] + [_TEN[9], _TEN[8], 'Owen O'],
    'F3 PubMed tenth omitted': _TEN[:9] + ['Owen O', 'Kemp K', 'Wren D'],
    'F4 coauthor omitted, namesakes': _TEN[:2] + _TEN[3:9] + ['Lee J', 'Lee J', 'Owen O'],
}


@pytest.mark.parametrize('case', sorted(_MISALIGNED))
@pytest.mark.parametrize('owner', ['Owen O', 'Abel A', None])
def test_misaligned_cv_byline_leaves_stage5_as_before(monkeypatch, case, owner):
    cv = _MISALIGNED[case]
    pubmed = _TEN[:9] + ['Lee J'] if case.startswith('F4') else _TEN
    record = {'pmid': PMID, 'year': 1988, 'authors': pubmed, 'authors_vancouver': ', '.join(pubmed)}
    enricher, _, _ = _make(monkeypatch, [])
    entry = _capped_entry(', '.join(cv), owner)
    enricher._merge_pubmed_data(entry, record)
    data = entry['enrichment_data']
    # The guard drops PubMed's list only where every PubMed surname is in
    # the CV and the owner is not among them.
    dropped = owner == 'Owen O' and case.startswith('F2')
    assert data['pubmed_authors'] == (None if dropped else ', '.join(pubmed))
    assert data.get('pubmed_authors_without_owner') == (', '.join(pubmed) if dropped else None)
    assert 'pubmed_authors_shorter' not in data


@pytest.mark.parametrize('cv', [VANN_CV, ', '.join(VANN_CV)])
def test_namesakes_leave_stage5_as_before(monkeypatch, cv):
    # The verifier's probe: the CV's "Vann LM" was dropped. PubMed's list,
    # which names the owner, renders, as on dev.
    enricher, _, _ = _make(monkeypatch, [])
    entry = _capped_entry(cv, 'Abel A')
    enricher._merge_pubmed_data(entry, {'pmid': PMID, 'year': 1988, 'authors': VANN_PUBMED,
                                        'authors_vancouver': ', '.join(VANN_PUBMED)})
    assert entry['enrichment_data']['pubmed_authors'] == ', '.join(VANN_PUBMED)
    assert 'pubmed_authors_shorter' not in entry['enrichment_data']


def test_cv_line_not_stage4_byline_decides_what_renders(monkeypatch):
    # AOJEQD e1004: stage 4's byline holds "Team H5P", which the CV's own
    # line does not write, so PubMed's list renders as before.
    enricher, _, _ = _make(monkeypatch, [])
    entry = _capped_entry(', '.join(CAPPED_CV[:10] + ['Team H5P']), 'Abel A')
    entry['text'] = ', '.join(CAPPED_CV[:10]) + f', HVTN 505 Protocol Team. {INPRESS_TITLE}.'
    enricher._merge_pubmed_data(entry, {'pmid': PMID, 'year': 1988, 'authors': CAPPED_CV[:10],
                                        'authors_vancouver': ', '.join(CAPPED_CV[:10])})
    assert entry['enrichment_data']['pubmed_authors'] == ', '.join(CAPPED_CV[:10])
    assert 'pubmed_authors_shorter' not in entry['enrichment_data']


def test_pubmed_list_as_long_as_the_cv_byline_still_renders(monkeypatch):
    xml = _article_set(_article(PMID, INPRESS_TITLE, authors=CAPPED_PUBMED, year=1988))
    enricher, _, _ = _make(monkeypatch, [xml])
    [result] = enricher._enrich_by_pmid([(_capped_entry(', '.join(CAPPED_CV[:10])), PMID)])
    assert result['enrichment_data']['pubmed_authors'] == ', '.join(CAPPED_PUBMED)
    assert 'pubmed_authors_shorter' not in result['enrichment_data']


# #1438 (VYNARH, dev-246): PubMed indexes an author's reply under the letter
# it answers, so the CV's "(Reply to Editor)" PMID resolved to the letter and
# the letter's writer rendered in place of the owner.
REPLY_TITLE = 'Tidal heron foraging in estuarine marshes'
REPLY_CV_AUTHORS = 'Quill JA, Wren D'


def _reply_entry(note='(Reply to Editor)', authors=REPLY_CV_AUTHORS):
    return {'taxonomy_code': 'S2',
            'text': f'{authors}. {REPLY_TITLE}. Heart Journal. 2025;7:101 {note}. PMID: {PMID}.',
            'extracted_fields': {'title': REPLY_TITLE, 'authors': authors,
                                 'target_name': 'Quill JA', 'pmid': PMID}}


def _letter_response(title=REPLY_TITLE, authors=('Abel A',)):
    return _article_set(_article(PMID, title, pubtypes=('Comment', 'Letter'), authors=authors))


@pytest.mark.parametrize('note', [
    '(Reply to Editor)', '(Response to Letter to Editor by A. Abel)', '(In reply to Abel)',
    "(Authors' response)", '(reply)'])
def test_reply_whose_pmid_is_the_letter_keeps_the_cv_citation(monkeypatch, note):
    enricher, _, _ = _make(monkeypatch, [_letter_response()])
    [result] = enricher._enrich_by_pmid([(_reply_entry(note), PMID)])
    assert result['enrichment_status'] == stage5.REPLY_TARGET_STATUS
    assert 'enrichment_data' not in result  # stage 6 renders the CV's own citation
    assert result['enrichment_rejected'] == {
        'source': 'pmid', 'pubmed_pmid': PMID, 'pubmed_title': REPLY_TITLE,
        'pubmed_authors': 'Abel A'}
    assert enricher.stats['reply_targets_rejected'] == 1
    assert enricher.stats['enriched'] == 0
    assert enricher.stats['failed_lookups'] == 0


def test_reply_record_titled_as_a_reply_is_accepted(monkeypatch):
    enricher, _, _ = _make(monkeypatch, [_letter_response(title=f'Reply: {REPLY_TITLE}')])
    [result] = enricher._enrich_by_pmid([(_reply_entry(), PMID)])
    assert result['enrichment_status'] == 'enriched'


def test_reply_record_naming_a_cv_author_is_accepted(monkeypatch):
    # A record with the CV's author may be the reply indexed with its letter.
    enricher, _, _ = _make(monkeypatch, [_letter_response(authors=('Abel A', 'Quill JA'))])
    [result] = enricher._enrich_by_pmid([(_reply_entry(), PMID)])
    assert result['enrichment_status'] == 'enriched'


def test_entry_not_called_a_reply_is_accepted(monkeypatch):
    enricher, _, _ = _make(monkeypatch, [_letter_response()])
    [result] = enricher._enrich_by_pmid([(_reply_entry(note='(Letter)'), PMID)])
    assert result['enrichment_status'] == 'enriched'


def test_reply_doi_path_does_not_store_the_letters_pmid(monkeypatch):
    enricher, _, _ = _make(monkeypatch, [FakeResponse(200, json_data=ESEARCH_JSON), _letter_response()])
    entry = _reply_entry()
    del entry['extracted_fields']['pmid']
    [result] = enricher._enrich_by_doi([(entry, DOI)])
    assert result['enrichment_status'] == stage5.REPLY_TARGET_STATUS
    assert result['enrichment_rejected']['source'] == 'doi_search'
    assert 'pmid' not in result['extracted_fields']


def test_reply_marker_inside_the_title_is_not_a_reply():
    title = 'Response to the editor as a teaching device'
    assert not stage5.cites_a_reply(f'Quill JA. {title}. J Ed. 2020.', title)
    assert stage5.cites_a_reply(f'Quill JA. {title}. J Ed. 2020 (Reply to Editor).', title)
    # "response" alone is a common title word, not a reply.
    assert not stage5.cites_a_reply('Quill JA. Immune response to vaccination. 2020.')
    assert not stage5.cites_a_reply('Quill JA. Dose-Response in cognition. 2020.')


# ------------------- #1219: PubMed's year for a yearless CV line; a copied doi:

def _yearless_entry(year=None):
    return {'taxonomy_code': 'S1', 'extracted_fields': {'title': 'Whimbrel counts', 'year': year}}


@pytest.mark.parametrize('cv_year', [None, ''])
def test_yearless_cv_article_takes_pubmeds_year(monkeypatch, cv_year):
    enricher, _, _ = _make(monkeypatch, [])
    entry = _yearless_entry(cv_year)
    enricher._merge_pubmed_data(entry, {'pmid': PMID, 'year': 2024})
    assert entry['extracted_fields']['year'] == '2024'
    assert 'year' in entry['enriched_fields']


@pytest.mark.parametrize('cv_year, record_year, kept', [
    ('2019', 2024, '2019'),  # the CV's own year is never overwritten
    (None, None, None),      # PubMed gave no year either
])
def test_cv_year_or_missing_pubmed_year_leaves_year_alone(monkeypatch, cv_year, record_year, kept):
    enricher, _, _ = _make(monkeypatch, [])
    entry = _yearless_entry(cv_year)
    enricher._merge_pubmed_data(entry, {'pmid': PMID, 'year': record_year})
    assert entry['extracted_fields']['year'] == kept
    assert 'year' not in entry.get('enriched_fields', [])


def test_yearless_in_press_article_is_left_to_the_in_press_step(monkeypatch):
    # An in-press listing PubMed also shows as an already-listed paper is
    # rendered as a deletion with its year as the CV gave it.
    enricher, _, _ = _make(monkeypatch, [])
    entry = _inpress_entry(code='S1')
    entry['extracted_fields']['year'] = None
    enricher._merge_pubmed_data(entry, {'pmid': PMID, 'year': 2024})
    assert entry['extracted_fields']['year'] is None
    assert 'year' not in entry.get('enriched_fields', [])


def test_yearless_article_matched_by_pmid_renders_pubmeds_year(monkeypatch):
    xml = _article_set(_article(PMID, 'Whimbrel counts', year=2023))
    enricher, _, _ = _make(monkeypatch, [xml])
    entry = _yearless_entry()
    [result] = enricher._enrich_by_pmid([(entry, PMID)])
    assert result['enrichment_status'] == 'enriched'
    assert result['extracted_fields']['year'] == '2023'


PLOVER, CURLEW, DUNLIN = '10.5555/plover.1', '10.5555/curlew.2', '10.5555/dunlin.3'


def _linked(doi, url_doi, link='HYPERLINK "https://doi.org/{0}" https://doi.org/{0}.'):
    return {'taxonomy_code': 'S3',
            'text': f'Godwit survey. doi: {doi}. ' + (link.format(url_doi) if url_doi else ''),
            'extracted_fields': {'doi': doi}}


def _plain(doi):
    return {'taxonomy_code': 'S3', 'text': f'Godwit survey. doi: {doi}.',
            'extracted_fields': {'doi': doi}}


def _dois(entries):
    return [e.get('extracted_fields', {}).get('doi') for e in entries]


@pytest.mark.parametrize('order', ['copied from the entry before', 'copied from the entry after'])
def test_doi_copied_from_a_neighbour_yields_to_the_entrys_own_link(monkeypatch, order):
    enricher, _, _ = _make(monkeypatch, [])
    entries = [_plain(PLOVER), _linked(PLOVER, CURLEW), _plain(DUNLIN)]
    if order.endswith('after'):
        entries = [_plain(DUNLIN), _linked(PLOVER, CURLEW), _plain(PLOVER)]
    enricher._prefer_url_dois(entries)
    assert entries[1]['extracted_fields']['doi'] == CURLEW  # no quote, no final period
    assert entries[1]['doi_replaced'] == PLOVER
    assert 'doi_replaced' not in entries[0] and 'doi_replaced' not in entries[2]
    assert _dois(entries)[0::2] == ([PLOVER, DUNLIN] if order.endswith('before') else [DUNLIN, PLOVER])


@pytest.mark.parametrize('link', [
    'http://dx.doi.org/{0}', 'https://DOI.org/{0}', 'https://doi.org/{0};'])
def test_link_forms_and_doi_case(monkeypatch, link):
    enricher, _, _ = _make(monkeypatch, [])
    entries = [_plain(PLOVER.upper()), _linked(PLOVER, CURLEW, link)]
    enricher._prefer_url_dois(entries)
    assert _dois(entries) == [PLOVER.upper(), CURLEW]


@pytest.mark.parametrize('case, entries', [
    ('link is the doi field', [_plain(PLOVER), _linked(PLOVER, PLOVER.upper())]),
    ('doi field shared with no neighbour', [_plain(DUNLIN), _linked(PLOVER, CURLEW)]),
    ('shared only with an entry two away',
     [_plain(PLOVER), _plain(DUNLIN), _linked(PLOVER, CURLEW)]),
    ('only the last entry, not a neighbour of the first, shares it',
     [_linked(PLOVER, CURLEW), _plain(DUNLIN), _plain(PLOVER)]),
    ('no link', [_plain(PLOVER), _linked(PLOVER, None)]),
    ('no text', [_plain(PLOVER), {'taxonomy_code': 'S3', 'text': None,
                                  'extracted_fields': {'doi': PLOVER}}]),
    ('no extracted fields', [{'taxonomy_code': 'S3', 'text': 'https://doi.org/' + CURLEW},
                             {'taxonomy_code': 'S3', 'text': 'https://doi.org/' + CURLEW}]),
    ('two different links',
     [_plain(PLOVER), _linked(PLOVER, CURLEW, 'https://doi.org/{0} https://doi.org/' + DUNLIN)]),
    ('no doi field', [{'taxonomy_code': 'S3', 'text': 'https://doi.org/' + CURLEW,
                       'extracted_fields': {}},
                      {'taxonomy_code': 'S3', 'text': 'https://doi.org/' + CURLEW,
                       'extracted_fields': {}}]),
])
def test_doi_field_is_kept_unless_copied_from_a_neighbour_with_one_other_link(monkeypatch, case, entries):
    enricher, _, _ = _make(monkeypatch, [])
    before = _dois(entries)
    enricher._prefer_url_dois(entries)
    assert _dois(entries) == before, case
    assert not any('doi_replaced' in e for e in entries), case


def test_stage5_looks_up_the_linked_doi_not_the_copied_one(tmp_path, monkeypatch):
    # The wire: the correction runs before the DOI path searches PubMed.
    empty = FakeResponse(200, json_data=ESEARCH_EMPTY_JSON)
    entries, session, _ = _run_entries(tmp_path, monkeypatch,
                                       [_plain(PLOVER), _linked(PLOVER, CURLEW)], [empty, empty])
    terms = [params.get('term') for _, params in session.calls]
    assert terms == [f'"{PLOVER}"[doi]', f'"{CURLEW}"[doi]']
    assert entries[1]['doi_replaced'] == PLOVER


# --------- #1387 steps 3-4: split a failed efetch batch; second pass for API errors

@pytest.fixture
def ncbi_up(monkeypatch):
    """NCBI has answered an earlier request this run, so the split and the
    second pass are not held back by the circuit breaker."""
    monkeypatch.setattr(stage5.PubMedEnricher, '_ncbi_answered', True)


def test_an_answered_request_closes_the_circuit_breaker(monkeypatch):
    enricher, _, _ = _make(monkeypatch, [FakeResponse(200, content=PUBMED_XML)])
    assert enricher._ncbi_answered is False
    enricher._fetch_pubmed_batch([PMID])
    assert enricher._ncbi_answered is True


def test_with_ncbi_down_a_failed_batch_is_not_split_and_no_second_pass_runs(tmp_path, monkeypatch):
    """An outage: no request has been answered, so stage 5 makes the requests
    it made before #1387 and no more."""
    pmids = _pmids(50)
    enricher, session, _ = _make(monkeypatch, [FakeResponse(404)])
    assert enricher._fetch_pubmed_records(pmids) == {}
    assert _ids_sent(session) == [pmids]
    [result], session, enricher = _run_entries(tmp_path, monkeypatch, [_pmid_entry()], [FakeResponse(404)])
    assert result['enrichment_status'] == 'lookup_failed'
    assert len(session.calls) == 1
    assert enricher.stats['second_pass_failed'] == 0

def _pmids(n, start=20000001):
    return [str(i) for i in range(start, start + n)]


def _ids_sent(session):
    return [params['id'].split(',') for _, params in session.calls]


def test_a_failed_efetch_batch_is_split_and_each_half_fetched(monkeypatch, ncbi_up):
    pmids = _pmids(50)
    enricher, session, _ = _make(monkeypatch, [
        FakeResponse(404),
        _article_set(_article(pmids[0], 'First half')),
        _article_set(_article(pmids[49], 'Second half')),
    ])
    records = enricher._fetch_pubmed_records(pmids)
    assert _ids_sent(session) == [pmids, pmids[:25], pmids[25:]]
    assert set(records) == {pmids[0], pmids[49]}
    assert enricher._efetch_failed == set()


@pytest.mark.parametrize('size, calls', [(25, 1), (26, 3)])
def test_a_failed_batch_is_split_only_above_the_floor(monkeypatch, ncbi_up, size, calls):
    pmids = _pmids(size)
    enricher, session, _ = _make(monkeypatch, [FakeResponse(404)] * calls)
    assert enricher._fetch_pubmed_records(pmids) == {}
    assert len(session.calls) == calls
    assert enricher._efetch_failed == set(pmids)


def _pmid_entry():
    return {'taxonomy_code': 'S1', 'extracted_fields': {'title': 'A Test Article', 'pmid': PMID}}


def _doi_article_entry():
    return {'taxonomy_code': 'S1', 'extracted_fields': {'title': 'A Test Article', 'doi': DOI}}


def test_pmid_lookup_an_efetch_error_failed_is_recovered_by_the_second_pass(tmp_path, monkeypatch, ncbi_up):
    [result], session, enricher = _run_entries(
        tmp_path, monkeypatch, [_pmid_entry()], [FakeResponse(404), FakeResponse(200, content=PUBMED_XML)])
    assert result['enrichment_status'] == 'enriched'
    assert len(session.calls) == 2
    assert enricher.stats['failed_lookups'] == 0
    assert (enricher.stats['second_pass_recovered'], enricher.stats['second_pass_failed']) == (1, 0)


def test_second_pass_waits_out_the_pause_first(monkeypatch, ncbi_up, caplog):
    enricher, _, sleeps = _make(monkeypatch, [FakeResponse(200, content=PUBMED_XML)])
    with caplog.at_level(logging.INFO, logger=stage5.__name__):
        enricher._retry_api_failures([(_pmid_entry(), PMID)], progress_total=1)
    assert sleeps == [stage5.SECOND_PASS_PAUSE_SECONDS]
    assert 'Re-running 1 lookups an NCBI error failed in 30s' in caplog.text


@pytest.mark.parametrize('entry, responses, status', [
    (_pmid_entry, [], 'lookup_failed'),
    (_doi_article_entry, [FakeResponse(200, json_data=ESEARCH_JSON)], 'doi_found_but_fetch_failed'),
])
def test_a_pmid_ncbi_answered_without_a_record_is_not_retried(tmp_path, monkeypatch, entry, responses, status):
    no_record = FakeResponse(200, content=b'<PubmedArticleSet/>')
    [result], session, enricher = _run_entries(tmp_path, monkeypatch, [entry()], responses + [no_record])
    assert result['enrichment_status'] == status
    assert len(session.calls) == len(responses) + 1
    assert enricher.stats['failed_lookups'] == 1
    assert (enricher.stats['second_pass_recovered'], enricher.stats['second_pass_failed']) == (0, 0)


@pytest.mark.parametrize('entry, status', [
    (_pmid_entry, 'lookup_failed'),
    (_doi_article_entry, 'doi_lookup_failed'),
])
def test_a_lookup_the_second_pass_also_fails_keeps_its_status(tmp_path, monkeypatch, ncbi_up, entry, status):
    [result], session, enricher = _run_entries(
        tmp_path, monkeypatch, [entry()], [FakeResponse(404), FakeResponse(404)])
    assert result['enrichment_status'] == status
    assert len(session.calls) == 2  # one second pass, not a loop
    assert enricher.stats['failed_lookups'] == 1
    assert (enricher.stats['second_pass_recovered'], enricher.stats['second_pass_failed']) == (0, 1)


def test_a_quiet_second_pass_logs_nothing(monkeypatch, ncbi_up, caplog):
    enricher, _, _ = _make(monkeypatch, [FakeResponse(200, content=PUBMED_XML)])
    enricher.verbose = False
    with caplog.at_level(logging.INFO, logger=stage5.__name__):
        enricher._retry_api_failures([(_pmid_entry(), PMID)], progress_total=1)
    assert caplog.records == []


@pytest.mark.parametrize('first_pass', [
    [FakeResponse(404)],                                                     # esearch error
    [FakeResponse(200, json_data=ESEARCH_JSON), FakeResponse(404)],          # efetch error
])
def test_doi_lookup_an_api_error_failed_is_recovered_by_the_second_pass(tmp_path, monkeypatch, ncbi_up, first_pass):
    [result], session, enricher = _run_entries(
        tmp_path, monkeypatch, [_doi_article_entry()],
        first_pass + [FakeResponse(200, json_data=ESEARCH_JSON), FakeResponse(200, content=PUBMED_XML)])
    assert result['enrichment_status'] == 'enriched'
    assert result['extracted_fields']['pmid'] == PMID
    assert enricher.stats['failed_lookups'] == 0
    assert enricher.stats['second_pass_recovered'] == 1
    # The second pass searches the entry's DOI again, not its PMID.
    assert [params['term'] for _, params in session.calls if 'term' in params][-1] == f'"{DOI}"[doi]'


def test_second_pass_progress_ends_the_bar_at_its_total(tmp_path, monkeypatch, ncbi_up, caplog):
    with caplog.at_level(logging.INFO, logger=stage5.__name__):
        _run_entries(tmp_path, monkeypatch, [_pmid_entry(), _doi_article_entry()], [
            FakeResponse(200, content=PUBMED_XML), FakeResponse(404),
            FakeResponse(200, json_data=ESEARCH_JSON), FakeResponse(200, content=PUBMED_XML)])
    bars = [r.getMessage() for r in caplog.records if 'publications looked up' in r.getMessage()]
    assert bars == ['[1/2] publications looked up', '[2/2] publications looked up',
                    '[2/2] publications looked up']


def test_title_search_survives_an_efetch_error(tmp_path, monkeypatch):
    [result], session, _ = _run_entries(tmp_path, monkeypatch, [_article_entry(10)],
                                        [_hits(PAPER_PMID), FakeResponse(404)])
    assert result['enrichment_status'] == 'no_identifier'
    assert len([url for url, _ in session.calls if url != stage5.CROSSREF_WORKS_URL]) == 2


# ------------------------------- #1872: Crossref finds the DOI of a PubMed title miss

CROSSREF_DOI = '10.1000/heron.2025.1'


def _crossref_hit(title=SHARED_TITLE, kind='journal-article', doi=CROSSREF_DOI):
    return {'DOI': doi, 'type': kind, 'title': [title]}


def _run_crossref(tmp_path, monkeypatch, crossref, responses):
    """One S1 entry whose PubMed title search finds nothing; `responses`
    answer what follows Crossref."""
    enricher, session, _ = _make(monkeypatch, [_hits(), *responses], crossref=crossref)
    path = tmp_path / 's4.json'
    path.write_text(json.dumps({'document_uid': 'x', 'entries': [_article_entry(10)]}))
    [result] = enricher.enrich_stage4_output(str(path))['entries']
    return result, session, enricher


def test_pubmed_title_miss_takes_the_pubmed_record_of_crossrefs_doi(tmp_path, monkeypatch):
    result, session, enricher = _run_crossref(
        tmp_path, monkeypatch, [_crossref_items(_crossref_hit())],
        [_hits(PAPER_PMID), _article_set(_article(PAPER_PMID, SHARED_TITLE))])
    assert session.calls[1] == (stage5.CROSSREF_WORKS_URL, {
        'query.bibliographic': f'{SHARED_TITLE} Garcia M 2025', 'rows': 3,
        'select': 'DOI,type,title,subtitle,author,issued,container-title,volume,issue,page'})
    assert session.calls[2][1]['term'] == f'"{CROSSREF_DOI}"[doi]'
    assert (result['enrichment_status'], result['enrichment_source']) == ('enriched', 'crossref_title_search')
    assert result['extracted_fields']['pmid'] == PAPER_PMID
    assert enricher.stats['crossref_searches'] == 1


def test_crossref_hit_that_is_not_a_journal_article_is_not_looked_up(tmp_path, monkeypatch):
    # FVQANK: a JCEM article's title also names a book chapter.
    result, session, _ = _run_crossref(
        tmp_path, monkeypatch, [_crossref_items(_crossref_hit(kind='book-chapter'))], [])
    assert len(session.calls) == 2
    assert result['enrichment_status'] == 'no_identifier'


def test_crossref_hit_with_another_title_is_not_looked_up(tmp_path, monkeypatch):
    _, session, _ = _run_crossref(
        tmp_path, monkeypatch, [_crossref_items(_crossref_hit(title=OTHER_PAPER_TITLE))], [])
    assert len(session.calls) == 2


def test_pubmed_record_of_crossrefs_doi_keeps_the_author_guard(tmp_path, monkeypatch):
    stranger = _article(PAPER_PMID, SHARED_TITLE, authors=('Okonkwo A',))
    result, _, _ = _run_crossref(tmp_path, monkeypatch, [_crossref_items(_crossref_hit())],
                                 [_hits(PAPER_PMID), _article_set(stranger)])
    assert result['enrichment_status'] == 'no_identifier'
    assert 'enrichment_data' not in result


def test_crossref_429_is_retried_and_a_reply_does_not_mark_ncbi_answering(monkeypatch):
    enricher, session, sleeps = _make(monkeypatch, [_hits()], crossref=[
        FakeResponse(429, url=stage5.CROSSREF_WORKS_URL), _crossref_items()])
    entry = _article_entry(10)
    enricher._enrich_by_crossref_doi(entry, SHARED_TITLE)
    assert [url for url, _ in session.calls] == [stage5.CROSSREF_WORKS_URL] * 2
    assert enricher._ncbi_answered is False
    assert enricher.stats['api_errors'] == 0


def test_crossref_failure_leaves_the_entry_unenriched(tmp_path, monkeypatch):
    result, _, enricher = _run_crossref(
        tmp_path, monkeypatch, [FakeResponse(400, url=stage5.CROSSREF_WORKS_URL)], [])
    assert result['enrichment_status'] == 'no_identifier'
    assert enricher.stats['api_errors'] == 1


def test_pubmed_record_of_crossrefs_doi_is_not_taken_when_it_is_an_erratum(tmp_path, monkeypatch):
    erratum = _article(PAPER_PMID, SHARED_TITLE, pubtypes=('Published Erratum',))
    result, _, _ = _run_crossref(tmp_path, monkeypatch, [_crossref_items(_crossref_hit())],
                                 [_hits(PAPER_PMID), _article_set(erratum)])
    assert 'enrichment_data' not in result


@pytest.mark.parametrize('source, cv_identifier', [
    ('pmid', True), ('title_search', False), ('crossref_title_search', False)])
def test_a_title_found_record_ranks_below_an_identifier_the_cv_gave(source, cv_identifier):
    entry = _article_entry(10)
    accepted = stage5._Acceptance(entry, entry, PAPER_PMID, SHARED_TITLE, 2025, source, 1.0)
    assert stage5._match_strength(accepted)[-1] is cv_identifier


def test_crossref_is_skipped_once_the_runs_budget_is_spent(monkeypatch):
    enricher, session, _ = _make(monkeypatch, [])
    enricher._crossref_seconds = stage5.CROSSREF_BUDGET_SECONDS
    enricher._enrich_by_crossref_doi(_article_entry(10), SHARED_TITLE)
    assert session.calls == []
    assert enricher.stats['crossref_skipped'] == 1


def test_crossref_lookups_count_against_the_budget(monkeypatch):
    enricher, _, _ = _make(monkeypatch, [])
    clock = iter([100.0, 102.5])
    monkeypatch.setattr(stage5.time, 'monotonic', lambda: next(clock))
    enricher._enrich_by_crossref_doi(_article_entry(10), SHARED_TITLE)
    assert enricher._crossref_seconds == 2.5


# --------------- #1872: Crossref's record of an article PubMed does not index

def _crossref_work(title=SHARED_TITLE, issued=2025, volume='12', family='Garcia'):
    return {'DOI': DOI, 'type': 'journal-article', 'title': [title],
            'container-title': ['Journal of Coastal Birds'], 'volume': volume, 'issue': '3',
            'page': '100-109', 'issued': {'date-parts': [[issued, 5]]},
            'author': [{'family': family, 'given': 'Maria'}]}


def _work_reply(work):
    return FakeResponse(200, json_data={'message': work}, url=f'{stage5.CROSSREF_WORKS_URL}/{DOI}')


def test_cv_doi_pubmed_does_not_index_keeps_crossrefs_record(tmp_path, monkeypatch):
    enricher, session, _ = _make(monkeypatch, [_hits(), _hits()],
                                 crossref=[_crossref_items(), _work_reply(_crossref_work())])
    path = tmp_path / 's4.json'
    path.write_text(json.dumps({'document_uid': 'x', 'entries': [_paper_entry(SHARED_TITLE, 10)]}))
    [result] = enricher.enrich_stage4_output(str(path))['entries']
    assert session.calls[-1][0] == f'{stage5.CROSSREF_WORKS_URL}/{DOI}'
    assert result['enrichment_status'] == 'doi_not_in_pubmed'
    assert 'enrichment_data' not in result
    assert result[stage5.CROSSREF_MATCH_KEY] == {
        'source': 'cv_doi', 'doi': DOI, 'type': 'journal-article', 'title': SHARED_TITLE,
        'journal': 'Journal of Coastal Birds', 'year': 2025, 'volume': '12', 'issue': '3',
        'pages': '100-109', 'authors': ['Garcia M']}
    assert enricher.stats['crossref_matches'] == 1


def _doi_miss_match(tmp_path, monkeypatch, work_reply, fields=None):
    """Crossref's match for one S1 entry whose DOI PubMed does not know and
    whose title neither PubMed nor Crossref's search finds; `work_reply`
    answers the lookup of the entry's own DOI."""
    enricher, _, _ = _make(monkeypatch, [_hits(), _hits()], crossref=[_crossref_items(), work_reply])
    entry = _paper_entry(SHARED_TITLE, 10)
    entry['extracted_fields'].update(fields or {})
    path = tmp_path / 's4.json'
    path.write_text(json.dumps({'document_uid': 'x', 'entries': [entry]}))
    [result] = enricher.enrich_stage4_output(str(path))['entries']
    return result.get(stage5.CROSSREF_MATCH_KEY), enricher


def test_cv_doi_whose_crossref_title_is_another_paper_keeps_no_match(tmp_path, monkeypatch):
    match, _ = _doi_miss_match(tmp_path, monkeypatch, _work_reply(_crossref_work(title='Soil carbon in alpine meadows')))
    assert match is None


def test_cv_doi_whose_year_and_volume_contradict_crossref_keeps_no_match(tmp_path, monkeypatch):
    # The #1696 UAURFU shape: 0.71 against the shorter title, another paper.
    work = _crossref_work(title=SHORT_RECORD_TITLE, issued=2020, volume='19')
    match, _ = _doi_miss_match(tmp_path, monkeypatch, _work_reply(work),
                               {'title': LONG_CV_TITLE, 'year': '2021', 'volume': '20'})
    assert match is None


def test_cv_doi_crossref_does_not_know_is_not_an_error(tmp_path, monkeypatch):
    match, enricher = _doi_miss_match(
        tmp_path, monkeypatch, FakeResponse(404, url=f'{stage5.CROSSREF_WORKS_URL}/{DOI}'))
    assert match is None
    assert enricher.stats['api_errors'] == 0


def test_cv_doi_lookup_failure_is_counted(tmp_path, monkeypatch):
    match, enricher = _doi_miss_match(
        tmp_path, monkeypatch, FakeResponse(400, url=f'{stage5.CROSSREF_WORKS_URL}/{DOI}'))
    assert match is None
    assert enricher.stats['api_errors'] == 1


def test_cv_doi_lookup_is_skipped_once_the_budget_is_spent(monkeypatch):
    enricher, session, _ = _make(monkeypatch, [])
    enricher._crossref_seconds = stage5.CROSSREF_BUDGET_SECONDS
    entry = _paper_entry(SHARED_TITLE, 10)
    entry['enrichment_status'] = 'doi_not_in_pubmed'
    enricher._match_doi_misses_in_crossref([entry])
    assert session.calls == []
    assert enricher.stats['crossref_skipped'] == 1


def test_title_search_hit_pubmed_does_not_index_keeps_crossrefs_record(tmp_path, monkeypatch):
    work = _crossref_work()
    result, _, enricher = _run_crossref(tmp_path, monkeypatch, [_crossref_items(work)], [_hits()])
    assert result['enrichment_status'] == 'no_identifier'
    assert result[stage5.CROSSREF_MATCH_KEY]['source'] == 'title_search'
    assert result[stage5.CROSSREF_MATCH_KEY]['volume'] == '12'
    assert enricher.stats['crossref_matches'] == 1


def test_unindexed_title_search_hit_keeps_the_author_guard(tmp_path, monkeypatch):
    work = _crossref_work(family='Okonkwo')
    result, _, _ = _run_crossref(tmp_path, monkeypatch, [_crossref_items(work)], [_hits()])
    assert stage5.CROSSREF_MATCH_KEY not in result


def test_crossref_record_joins_the_subtitle_and_reads_initials():
    record = stage5.crossref_record({'DOI': DOI, 'title': ['Herons'], 'subtitle': ['a coastal survey'],
                                     'author': [{'family': 'Garcia', 'given': 'Maria-Luz'},
                                                {'name': 'Coastal Bird Group'}]})
    assert record['title'] == 'Herons a coastal survey'
    assert record['authors'] == ['Garcia ML', 'Coastal Bird Group']
    assert record['year'] is None


def test_doi_matched_by_the_title_search_is_not_looked_up_again(tmp_path, monkeypatch):
    enricher, session, _ = _make(monkeypatch, [_hits(), _hits(), _hits()],
                                 crossref=[_crossref_items(_crossref_work())])
    path = tmp_path / 's4.json'
    path.write_text(json.dumps({'document_uid': 'x', 'entries': [_paper_entry(SHARED_TITLE, 10)]}))
    [result] = enricher.enrich_stage4_output(str(path))['entries']
    assert result[stage5.CROSSREF_MATCH_KEY]['source'] == 'title_search'
    assert f'{stage5.CROSSREF_WORKS_URL}/{DOI}' not in [url for url, _ in session.calls]


def test_an_empty_crossref_work_is_no_match(tmp_path, monkeypatch):
    match, _ = _doi_miss_match(tmp_path, monkeypatch, _work_reply({}))
    assert match is None


def test_a_crossref_work_reply_does_not_mark_ncbi_answering(monkeypatch):
    enricher, _, _ = _make(monkeypatch, [], crossref=[_work_reply(_crossref_work())])
    entry = _paper_entry(SHARED_TITLE, 10)
    enricher._look_up_crossref_work(entry)
    assert entry[stage5.CROSSREF_MATCH_KEY]['doi'] == DOI
    assert enricher._ncbi_answered is False


# ------------------- #1870: a titleless article stub gets an incomplete-citation note

def _stub(code='S1', **fields):
    return {'taxonomy_code': code, 'enrichment_status': 'no_identifier',
            'text': 'Rodd et al. (2004a) Alcoholism: Clinical and Experimental Research JIF: 2.716',
            'extracted_fields': {'authors': 'Rodd et al.', 'year': '2004',
                                 'journal': 'Alcoholism: Clinical and Experimental Research', **fields}}


@pytest.mark.parametrize('code', ['S1', 'S2', 'S6'])
def test_titleless_article_stub_gets_the_incomplete_citation_note(code):
    enricher = PubMedEnricher(verbose=False)
    entry = _stub(code)
    enricher._note_incomplete_citations([entry])
    assert entry[stage5.CITATION_NOTE_KEY] == stage5.INCOMPLETE_CITATION_NOTE
    assert enricher.stats['incomplete_citations'] == 1


@pytest.mark.parametrize('entry', [
    _stub(title='A titled paper'),                                   # has a title
    _stub(year=None),                                                # "1993"-style rows need both
    _stub(journal=None),                                             # a header or split row
    _stub(code='S8'),                                                # not a journal article
    {**_stub(), 'enrichment_status': 'enriched'},
    {**_stub(), stage5.CROSSREF_MATCH_KEY: {'doi': DOI}},
    {**_stub(), 'in_press_note': 'The CV lists this as in press.'},
], ids=['titled', 'no-year', 'no-journal', 'S8', 'enriched', 'crossref-matched', 'in-press-noted'])
def test_only_an_unmatched_titleless_article_with_year_and_journal_is_noted(entry):
    PubMedEnricher(verbose=False)._note_incomplete_citations([entry])
    assert stage5.CITATION_NOTE_KEY not in entry


def test_stage5_run_notes_a_stub(tmp_path, monkeypatch):
    [result], session, _ = _run_entries(tmp_path, monkeypatch, [_stub()], [])
    assert session.calls == []
    assert result[stage5.CITATION_NOTE_KEY] == stage5.INCOMPLETE_CITATION_NOTE


@pytest.mark.parametrize('fields, query', [
    ({'authors': 'Waseem M, Perry C, Bomann JS, et al.', 'year': '2010'},
     'Cholinergic crisis Waseem M Perry C Bomann JS 2010'),
    ({'authors': ['Li AW', 'Weed JG', 'Stamey CR', 'Subtil A'], 'year': 'Dec 2017'},
     'Cholinergic crisis Li AW Weed JG Stamey CR 2017'),
    ({'authors': 'Waseem M', 'year': 'in press'}, 'Cholinergic crisis Waseem M'),
    ({}, 'Cholinergic crisis'),
], ids=['string-list-capped', 'list-and-month', 'no-year', 'nothing-but-title'])
def test_crossref_query_adds_the_first_authors_and_year(fields, query):
    # #1870: on its title alone, MNZ7IA's West J Emerg Med case report never
    # reached Crossref's top three.
    assert stage5.crossref_query('Cholinergic crisis', fields) == query
