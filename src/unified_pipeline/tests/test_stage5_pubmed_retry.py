"""Retry/backoff, identity, secret-redaction, and failure-classification
guards for Stage 5 PubMed enrichment (#222).

NCBI E-utilities return 429 when the shared rate limit is hit; previously a
single 429 aborted the whole efetch batch (catch-all -> {}). These tests pin
the new behavior: transient failures (429 / 5xx / connection errors) retry up
to 3 total attempts (idconv: IDCONV_MAX_ATTEMPTS) with exponential backoff (Retry-After honored when
larger), non-transient HTTP errors still fail immediately, exhausted retries
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
from unified_pipeline.stage_5_pubmed_enrichment import (  # noqa: E402
    MIN_TITLE_WORD_OVERLAP,
    PubMedEnricher,
    _sanitize_error,
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
    """Queue of responses; an Exception instance in the queue is raised."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []  # (url, params) per get()

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _make(monkeypatch, responses, api_key='', email=''):
    """Enricher with mocked session + recorded (never real) sleeps.

    Module-level env lookups are monkeypatched to controlled dummy values so
    tests never depend on (or expose) a real NCBI_API_KEY in the environment.
    """
    monkeypatch.setattr(stage5, 'NCBI_API_KEY', api_key)
    monkeypatch.setattr(stage5, 'PUBMED_CONTACT_EMAIL', email)
    sleeps = []
    monkeypatch.setattr(stage5.time, 'sleep', lambda s: sleeps.append(s))
    enricher = PubMedEnricher(verbose=True)
    session = FakeSession(responses)
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

def test_three_429s_gives_up_gracefully(monkeypatch, caplog):
    enricher, session, sleeps = _make(monkeypatch, [FakeResponse(429)] * 3)
    with caplog.at_level(logging.INFO, logger=stage5.__name__):
        records = enricher._fetch_pubmed_batch([PMID])
    assert records == {}  # same degraded shape as before
    assert len(session.calls) == 3
    assert sleeps == [1.0, 2.0]
    assert enricher.stats['api_errors'] == 1
    assert any('❌ API error' in r.getMessage() for r in caplog.records)


def test_id_converter_gives_up_gracefully(monkeypatch, caplog):
    enricher, session, sleeps = _make(
        monkeypatch, [FakeResponse(429)] * stage5.IDCONV_MAX_ATTEMPTS)
    with caplog.at_level(logging.INFO, logger=stage5.__name__):
        result = enricher._convert_pmcids_to_pmids(['PMC1234567'])
    assert result == {}
    assert len(session.calls) == stage5.IDCONV_MAX_ATTEMPTS
    assert sleeps == [1.0, 2.0, 4.0, 8.0, 16.0]
    assert sum(sleeps) <= stage5.IDCONV_RETRY_TOTAL_WAIT_CAP_SECONDS
    assert enricher.stats['api_errors'] == 1
    assert any('❌ ID conversion error' in r.getMessage() for r in caplog.records)
    assert all(r.exc_info is None for r in caplog.records)


def test_id_converter_429_outlasting_the_old_window_now_converts(monkeypatch):
    ok = FakeResponse(200, json_data={'records': [{'pmcid': 'PMC1234567', 'pmid': '7654321'}]})
    enricher, session, sleeps = _make(monkeypatch, [FakeResponse(429)] * 3 + [ok])
    assert enricher._convert_pmcids_to_pmids(['PMC1234567']) == {'PMC1234567': '7654321'}
    assert sleeps == [1.0, 2.0, 4.0]
    assert enricher.stats['api_errors'] == 0


def test_id_converter_honours_retry_after(monkeypatch):
    ok = FakeResponse(200, json_data={'records': []})
    enricher, _, sleeps = _make(
        monkeypatch, [FakeResponse(429, headers={'Retry-After': '20'}), ok])
    enricher._convert_pmcids_to_pmids(['PMC1234567'])
    assert sleeps == [20.0]


def test_id_converter_retry_after_beyond_the_cap_fails_fast(monkeypatch):
    too_long = str(int(stage5.IDCONV_RETRY_TOTAL_WAIT_CAP_SECONDS) + 1)
    enricher, session, sleeps = _make(
        monkeypatch, [FakeResponse(429, headers={'Retry-After': too_long})])
    assert enricher._convert_pmcids_to_pmids(['PMC1234567']) == {}
    assert sleeps == [] and len(session.calls) == 1
    assert enricher.stats['api_errors'] == 1


def test_doi_search_retries_5xx_then_succeeds(monkeypatch):
    enricher, _, sleeps = _make(monkeypatch, [
        FakeResponse(500),
        FakeResponse(200, json_data=ESEARCH_JSON),
    ])
    assert enricher._search_pmid_by_doi('10.1000/test.123') == PMID
    assert sleeps == [1.0]


# --------------------------------------------------------- (c) 404 = no retry

def test_404_fails_immediately_no_retry(monkeypatch):
    enricher, session, sleeps = _make(monkeypatch, [FakeResponse(404)])
    records = enricher._fetch_pubmed_batch([PMID])
    assert records == {}
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
    source = Path(stage5.__file__).read_text()
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
    enricher, session, _ = _make(monkeypatch, [FakeResponse(429)] * 3)
    results = enricher._enrich_by_doi([(_doi_entry(), DOI)])
    assert results[0]['enrichment_status'] == 'doi_lookup_failed'
    assert len(session.calls) == 3  # retries still exhausted first


def test_esearch_connection_error_becomes_doi_lookup_failed(monkeypatch):
    enricher, _, _ = _make(monkeypatch, [requests.ConnectionError('reset')] * 3)
    results = enricher._enrich_by_doi([(_doi_entry(), DOI)])
    assert results[0]['enrichment_status'] == 'doi_lookup_failed'


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
        + [requests.ConnectionError('reset')] * 3,
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
    enricher, _, _ = _make(monkeypatch, [FakeResponse(429)] * stage5.IDCONV_MAX_ATTEMPTS)
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
    assert result['enrichment_status'] == 'pmcid_conversion_failed'
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
    assert result['enrichment_status'] == 'pmcid_conversion_failed'
    assert len(session.calls) == 1


def test_bare_digit_pmcid_missing_as_pmid_keeps_its_original_failure(monkeypatch):
    enricher, _, _ = _make(monkeypatch, [
        _idconv_error_response(),
        FakeResponse(200, content=b'<PubmedArticleSet/>'),
    ])
    entry = _titled('A test article', pmcid=BARE_PMID)
    [result] = enricher._enrich_by_pmcid([(entry, f'PMC{BARE_PMID}')])
    assert result['enrichment_status'] == 'pmcid_conversion_failed'
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


def test_prefixed_pmcid_is_never_tried_as_a_pmid(monkeypatch):
    enricher, session, _ = _make(monkeypatch, [_idconv_error_response()])
    entry = _titled('A test article', pmcid=f'PMC{BARE_PMID}')
    [result] = enricher._enrich_by_pmcid([(entry, f'PMC{BARE_PMID}')])
    assert result['enrichment_status'] == 'pmcid_conversion_failed'
    assert len(session.calls) == 1


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
