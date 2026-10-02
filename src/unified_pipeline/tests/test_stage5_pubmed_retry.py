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


def test_id_converter_summed_retry_after_waits_are_capped(monkeypatch):
    # Each 25 s wait is under the 60 s cap alone; the third would push the sum to 75 s.
    enricher, session, sleeps = _make(
        monkeypatch,
        [FakeResponse(429, headers={'Retry-After': '25'})] * stage5.IDCONV_MAX_ATTEMPTS)
    assert enricher._convert_pmcids_to_pmids(['PMC1234567']) == {}
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
    assert 'in_press_note' not in result


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
    enricher, _, _ = _make(monkeypatch, _found())
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
    # A PMID on the other entry costs one efetch before the title search.
    pmid_lookup = [FakeResponse(200, content=_published_xml())]
    for other, superseded, extra in (
            (_published_entry(title=INPRESS_TITLE, pmid=PMID), True, pmid_lookup),
            (_published_entry(title=INPRESS_TITLE, year='2019'), False, []),
            # web228: a shorter title nested in the in-press one, same year.
            (_published_entry(title='Statin adherence in older adults', year='2025'), False, []),
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
