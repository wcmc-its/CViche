#!/usr/bin/env python3
"""
Stage 5: PubMed Enrichment

Enriches Stage 4 field extraction output with authoritative PubMed data.

Strategy:
1. For entries with PMID: Direct efetch lookup
2. For entries with PMCID only: Convert to PMID via ID converter, then efetch
3. For entries with DOI only: Search PubMed by DOI, then efetch

What gets enriched:
- Full author list (in Vancouver format)
- Official journal name
- Volume, issue, pages (if missing)
- Publication type (Journal Article, Review, etc.)
- MeSH terms
- Citation counts (when available)

Author: Scholar Signals CV Pipeline
Date: 2025-11-29
"""

import copy
import os
import sys
import json
import logging
import re
import time
import unicodedata
import requests
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, NamedTuple
from datetime import datetime

from unified_pipeline.core.pubmed_xml import ID_TYPE_DOI, ID_TYPE_PMC, own_article_id

logger = logging.getLogger(__name__)


# Configuration
NCBI_API_KEY = os.getenv('NCBI_API_KEY', os.getenv('PUBMED_API_KEY', ''))
PUBMED_CONTACT_EMAIL = os.getenv('PUBMED_CONTACT_EMAIL', os.getenv('NCBI_CONTACT_EMAIL', ''))
RATE_LIMIT_DELAY = 0.1 if NCBI_API_KEY else 0.34  # 10/s with key, 3/s without

# Retry policy for transient NCBI API failures (429 / 5xx / connection errors)
MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 1.0  # 1s, 2s, ... doubling; Retry-After honored when larger

# idconv (#1219): a 429 there outlasted the 3-attempt, ~3 s window in 2 of 8
# batch CVs and failed every PMCID in the CV. Re-probes showed it throttles
# intermittently (429, 200, 429, 200 at 15 s spacing). 6 attempts back off
# 1+2+4+8+16 = 31 s; the total cap bounds that sum plus any Retry-After, so a
# server asking for longer than the cap fails fast instead of stalling stage 5.
IDCONV_MAX_ATTEMPTS = 6
IDCONV_RETRY_TOTAL_WAIT_CAP_SECONDS = 60.0

# Output directory
OUTPUT_DIR = Path(__file__).parent / "outputs" / "stage_5_enrichment"

# NCBI API endpoints
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ELINK_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"
ID_CONVERTER_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"

# Minimum share of the shorter title's words that the PubMed record's title
# must share with the source title before the record replaces the citation
# (#1043). Over 105 local CVs, every wrong-paper substitution scored below 0.4
# (swapped adjacent PMIDs top out at 0.33); the lowest same-paper matches
# (retitled on publication) score 0.45-0.57.
MIN_TITLE_WORD_OVERLAP = 0.4

# Statuses the PMCID path leaves on an entry it could not enrich, which the
# bare-digits-as-PMID retry (#1219) may still resolve.
PMCID_PATH_FAILURES = ('pmcid_conversion_failed', 'title_check_failed')

# Words shorter than this carry no signal ("of", "in", "a"); 3 keeps short
# content words such as "DNA" and "HIV". Preventive, no incident: a stopword
# proxy, not a measured value.
_MIN_TITLE_WORD_LEN = 3
# Surnames as short as "Li" and "Wu" are names.
_MIN_SURNAME_WORD_LEN = 2


# A CV entry the author called "in press" or "accepted" that has no
# identifier is looked up by title. The 2026-10-01 probe (63 in-press entries
# stuck in S7, `3b-adjudication-2026-10-01/pubmed_probe.py`) found 29 at
# overlap >= 0.8, all of type Journal Article; 3 weaker hits were left alone.
# A title search can return any paper, so this floor is twice the ID paths'.
MIN_TITLE_SEARCH_OVERLAP = 0.8
# Fewer words than this and a title search matches too much; the probe used 4.
MIN_TITLE_SEARCH_WORDS = 4
TITLE_SEARCH_MAX_HITS = 3
PREPRINT_PUBTYPE = 'Preprint'
# A title search can also return a notice about the paper, whose title is the
# paper's own with "Correction:" in front (QNZADH, dev-242: an "accepted"
# article was cited as its Published Erratum). None of these is ever what a CV
# lists as in press.
NOTICE_PUBTYPES = frozenset({'Published Erratum', 'Retraction Notice', 'Expression of Concern'})
TITLE_SEARCH_EXCLUDED_PUBTYPES = NOTICE_PUBTYPES | {PREPRINT_PUBTYPE}
# A Comment can be either. Of the three Comment-typed title-search matches in
# the 63-run EBYSBC farm (2026-10-02), two were the CV's own commentary, each
# its search's only hit; the third was a "Discussion of:" piece about the
# paper (QSWXKR), listed ahead of the paper itself at the same overlap. So a
# Comment is not skipped, but ranks below a non-Comment hit that matches at
# least as well.
COMMENT_PUBTYPE = 'Comment'
# A paper in press is published within a couple of years of the year the CV
# gives it. Of the 87 corpus matches (2026-10-02) with a CV year, 64 were 0-2
# years later; the one at 4 years was a different paper by the same group
# with a near-identical title.
IN_PRESS_YEAR_WINDOW = range(-1, 3)

# "accepted" counts only in a status shape: followed by punctuation, a date,
# "for publication" or "author manuscript". Every corpus "accepted" in an S
# entry has one of those shapes (2026-10-02, 60 CVs), while PubMed's 1,599
# titles with the word mostly use it as a verb ("accepted by", "accepted as a
# standard"), and "accepted for presentation" / "Accepted abstract" are
# conference items. "in press" likewise needs punctuation, a year, PMID,
# doi or "in" after it (every corpus shape, "In press PMID: ...", "[In Press
# in the 2022 Proceedings"); PubMed titles hold "in Press Releases" (PMID
# 27978540), "in press conference", "in press-fit", "in Press Ganey".
IN_PRESS_PATTERN = re.compile(
    r'\b(in[\s-]press(?=\s*(?:[^\w\s-]|\d|$|pmid\b|doi\b|in\b))'
    r'|accepted(?=\s*(?:[^\w\s]|\d|$)|\s+for\s+publication|\s+author\s+manuscript)'
    r'|(?:e-?pub|online)\s+ahead\s+of\s+print)\b', re.I)
# An entry that also says it is still under review is not in press (4 of the
# 63 probe entries said both).
NOT_YET_ACCEPTED_PATTERN = re.compile(
    r'\b(submitted|under\s+review|in\s+review|in\s+revision|under\s+revision|in\s+prep(?:aration)?)\b', re.I)

# The S code an in-press entry moves to once PubMed shows it published, by
# PubMed PublicationType; first match wins, otherwise S1. Case Reports comes
# first because "case report and review of the literature" carries both.
PUBTYPE_TAXONOMY_CODES = (('Case Reports', 'S6'), ('Review', 'S2'),
                          ('Systematic Review', 'S2'), ('Editorial', 'S2'))
PUBLISHED_DEFAULT_CODE = 'S1'
IN_REVIEW_CODE = 'S7'
# An in-press entry PubMed did not find still is not "In review" (#1166):
# taxonomy_v7 promotes a paper once accepted. Without a PubMed type to go by,
# a chapter ("In: ... eds") goes to S4 and anything else to S1. Prompt text
# alone did not move these: the 2026-09-30 3b A/B left 23 in S7 on web200 and
# web46 after rule 30 named "in press" explicitly.
UNMATCHED_CHAPTER_CODE = 'S4'
UNMATCHED_DEFAULT_CODE = 'S1'
# "In;" and "(eds Greenwood, ...)" / "(ed Venables)" are both corpus shapes
# (web200).
CHAPTER_PATTERN = re.compile(r'\bIn[:;]\s*\S|\(eds?\b|\beds?\.(?=\s|,|$)|\beditors?\b|\bchapter\b', re.I)
# An in-press entry whose PubMed match another entry already lists as
# published is a stale duplicate (web200: the same 2006 paper listed both as
# published and as "in press" in another journal). Compared only against these
# codes, so a same-titled conference abstract (S8) is not mistaken for it.
PUBLISHED_ARTICLE_CODES = frozenset({'S1', 'S2', 'S6'})
# Only an article can be "in press" in the sense PubMed can answer. A
# conference abstract marked "(Accepted)" (S8) was replaced live by the later
# journal paper of the same title (SDEBQJ, dev-239, 2026-10-02); a PubMed
# journal article is no replacement for a chapter or a book either.
IN_PRESS_ELIGIBLE_CODES = PUBLISHED_ARTICLE_CODES | {IN_REVIEW_CODE}
# Only an ID-less entry, or one whose DOI PubMed did not know, is searched by
# title: a CV identifier that resolved to another paper stays rejected.
_TITLE_SEARCHABLE_STATUSES = frozenset({'no_identifier', 'doi_not_in_pubmed'})


def in_press_phrase(text: str, title: str = '') -> str | None:
    """The phrase that marks the entry as in press, lowercased, or None when
    there is none or the entry also says it is still under review.

    A phrase inside the title ("Socially accepted norms") is not a status, so
    the text must hold more matches than the title does. Counting, not
    deleting the title from the text, because stage 4's title rarely equals
    the CV's spelling of it exactly (case, punctuation, truncation). Status
    phrases trail the citation, so the last match is the one reported."""
    text = text or ''
    matches = IN_PRESS_PATTERN.findall(text)
    if (len(matches) <= len(IN_PRESS_PATTERN.findall(title or ''))
            or NOT_YET_ACCEPTED_PATTERN.search(text)):
        return None
    return re.sub(r'\s+', ' ', matches[-1].lower())


def listed_in_press(entry: dict) -> str | None:
    """The phrase by which the CV lists `entry` as in press or accepted, or
    None: only an article code can be, and only a titled entry, since without
    a title a status cannot be told from a title that says "(in press)"
    (letters, corrigenda) or ends "accepted."."""
    if entry.get('taxonomy_code') not in IN_PRESS_ELIGIBLE_CODES:
        return None
    fields = entry.get('extracted_fields') or {}
    title = fields.get('title') or fields.get('chapter_title') or ''
    return in_press_phrase(entry.get('text') or '', title) if title else None


def _folded_words(text: str, min_len: int) -> set[str]:
    unquoted = re.sub(r"['\u2019]", '', text or '')
    folded = unicodedata.normalize('NFKD', unquoted).encode('ascii', 'ignore').decode()
    return {w for w in re.findall(r'[a-z0-9]+', folded.lower()) if len(w) >= min_len}


def _title_words(title: str) -> set[str]:
    """Accent-folded lowercase words, so 'geneticas' matches 'genéticas'.
    Apostrophes are dropped first so "Men’s" and "Men's" both give 'mens'."""
    return _folded_words(title, _MIN_TITLE_WORD_LEN)


def shares_an_author(cv_authors: str | list[str] | None, pubmed_authors: list[str]) -> bool:
    """True when any PubMed surname ("Smith JA" -> "smith") appears in the
    CV's author text, or the CV names no authors to compare. Two-letter
    surnames such as Li and Wu count."""
    if isinstance(cv_authors, list):
        cv_authors = ' '.join(map(str, cv_authors))
    cv_words = _folded_words(str(cv_authors or ''), 2)
    if not cv_words:
        return True
    return any(_folded_words(a.rsplit(' ', 1)[0], 2) & cv_words for a in pubmed_authors)


def pubmed_list_drops_the_owner(cv_authors: str | list[str] | None, target_name: str | None,
                                pubmed_authors: list[str]) -> bool:
    """Whether PubMed's author list is the CV's list with the owner cut out:
    the CV's list names the owner (stage 4's `target_name`), PubMed's does
    not, and every surname PubMed gives is in the CV's list (OIYKZE, dev-242:
    the CV names 20+ authors with the owner sixteenth; the DOI's PubMed record
    holds the first ten). A PubMed list with a name the CV lacks is another
    spelling or another record, not a cut, and stays. Initials ("JA", "B.")
    are not the owner's name, so all-uppercase words of `target_name` are
    dropped. A list-typed `cv_authors` is read through its str(), which
    keeps every name's words."""
    name = ' '.join(w for w in str(target_name or '').split() if not w.isupper())
    owner = _folded_words(name, _MIN_SURNAME_WORD_LEN)
    cv_words = _folded_words(str(cv_authors or ''), _MIN_SURNAME_WORD_LEN)
    surnames = [s for s in (_folded_words(a.rsplit(' ', 1)[0], _MIN_SURNAME_WORD_LEN)
                            for a in pubmed_authors) if s]
    return bool(owner & cv_words and surnames
                and all(s <= cv_words for s in surnames)
                and not any(owner & s for s in surnames))


def plausible_publication_year(cv_year: str | int | None, pubmed_year: int | None) -> bool:
    """False only when both years are known and PubMed's falls outside
    IN_PRESS_YEAR_WINDOW of the CV's."""
    match = re.fullmatch(r'\s*((?:19|20)\d\d)\s*', str(cv_year))
    if not match or not pubmed_year:
        return True
    return pubmed_year - int(match.group(1)) in IN_PRESS_YEAR_WINDOW


def _same_title(a: str, b: str) -> bool:
    """Overlap against the longer title, so one title nested in the other
    does not count as the same paper."""
    words_a, words_b = _title_words(a), _title_words(b)
    if not words_a or not words_b:
        return False
    return len(words_a & words_b) / max(len(words_a), len(words_b)) >= MIN_TITLE_SEARCH_OVERLAP


def published_taxonomy_code(publication_types: list[str]) -> str:
    """The S code for a published paper of these PubMed publication types."""
    for pubtype, code in PUBTYPE_TAXONOMY_CODES:
        if pubtype in publication_types:
            return code
    return PUBLISHED_DEFAULT_CODE


def title_word_overlap(source_title: str, pubmed_titles: list[str]) -> float | None:
    """Best overlap (shared words / shorter title's words) between the source
    title and any of the record's titles, or None when either side has no
    words to compare. PubMed brackets a translated ArticleTitle and keeps the
    original in VernacularTitle, so a non-English source title is compared
    against both."""
    source = _title_words(source_title)
    if not source:
        return None
    scores = [
        len(source & words) / min(len(source), len(words))
        for words in map(_title_words, pubmed_titles) if words
    ]
    return max(scores, default=None)


def _sanitize_error(error: Any) -> str:
    """
    Redact NCBI API keys from error text before it reaches logs.

    Exception messages from requests embed the full request URL, which
    includes api_key as a query parameter.
    """
    return re.sub(r'api_key=[^&\s]+', 'api_key=***', str(error))


class _Acceptance(NamedTuple):
    """One accepted record, kept until every lookup path has run so that an
    entry sharing its PMID with a better-matching entry can be restored to
    `before`, its state before the record was merged in."""
    entry: dict
    before: dict
    pmid: str
    pubmed_title: str
    source: str
    overlap: float | None


class PubMedEnricher:
    """
    Enriches CV publication entries with PubMed data.

    Supports lookup by:
    - PMID (direct)
    - PMCID (via ID converter)
    - DOI (via esearch)
    """

    def __init__(self, api_key: str | None = None, verbose: bool = True):
        self.api_key = api_key or NCBI_API_KEY
        self.verbose = verbose
        self.session = requests.Session()
        user_agent = 'Scholar-Signals-CV-Pipeline/1.0'
        if PUBMED_CONTACT_EMAIL:
            user_agent += f' (mailto:{PUBMED_CONTACT_EMAIL})'
        self.session.headers.update({'User-Agent': user_agent})

        # Stats tracking
        self.stats = {
            'total_publications': 0,
            'with_pmid': 0,
            'with_pmcid_only': 0,
            'with_doi_only': 0,
            'no_identifier': 0,
            'enriched': 0,
            'pmid_lookups': 0,
            'pmcid_conversions': 0,
            'doi_searches': 0,
            'failed_lookups': 0,
            'title_mismatches': 0,
            'api_errors': 0,
            'title_searches': 0,
            'in_press_resolved': 0,
            'in_press_duplicates': 0,
            'in_press_promoted': 0,
        }

        # (operation, status/exception) classes already logged at ERROR this run
        self._logged_failure_classes = set()

        # This document's accepted records, for _release_weaker_shared_pmids
        self._acceptances: list[_Acceptance] = []

    def enrich_stage4_output(self, stage4_path: str) -> dict[str, Any]:
        """
        Main entry point: Enrich a Stage 4 output file.

        Args:
            stage4_path: Path to Stage 4 JSON output

        Returns:
            Enriched output dict
        """
        # Load Stage 4 output
        with open(stage4_path, 'r') as f:
            stage4_data = json.load(f)

        document_uid = stage4_data.get('document_uid', 'unknown')
        entries = stage4_data.get('entries', [])
        cv_owner = stage4_data.get('cv_owner')  # Pass through from Stage 4
        self._acceptances = []

        if self.verbose:
            logger.info(f"\n{'='*60}")
            logger.info(f"Stage 5: PubMed Enrichment - {document_uid}")
            logger.info(f"{'='*60}")

        # Identify publication entries (S1-S9)
        pub_entries = [e for e in entries if e.get('taxonomy_code', '').startswith('S')
                       and e.get('taxonomy_code') not in ('S0',)]

        self.stats['total_publications'] = len(pub_entries)

        if self.verbose:
            logger.info(f"\nFound {len(pub_entries)} publication entries to enrich")

        # Categorize by available identifiers
        by_pmid = []
        by_pmcid = []
        by_doi = []
        no_id = []

        for entry in pub_entries:
            fields = entry.get('extracted_fields', {})
            pmid = self._clean_pmid(fields.get('pmid'))
            pmcid = self._clean_pmcid(fields.get('pmcid'))
            doi = self._clean_doi(fields.get('doi'))

            if pmid:
                by_pmid.append((entry, pmid))
                self.stats['with_pmid'] += 1
            elif pmcid:
                by_pmcid.append((entry, pmcid))
                self.stats['with_pmcid_only'] += 1
            elif doi:
                by_doi.append((entry, doi))
                self.stats['with_doi_only'] += 1
            else:
                no_id.append(entry)
                self.stats['no_identifier'] += 1

        if self.verbose:
            logger.info(f"  - {len(by_pmid)} with PMID (direct lookup)")
            logger.info(f"  - {len(by_pmcid)} with PMCID only (needs conversion)")
            logger.info(f"  - {len(by_doi)} with DOI only (needs search)")
            logger.info(f"  - {len(no_id)} without identifiers (skip)")

        # Process each category
        enriched_entries = []
        # [N/M] is the parsed progress-bar contract (orchestrator PROGRESS_PATTERNS);
        # counted in publications so the three lookup paths share one bar.
        progress_total = len(by_pmid) + len(by_pmcid) + len(by_doi)

        # 1. Direct PMID lookups (batch)
        if by_pmid:
            if self.verbose:
                logger.info(f"\n📚 Fetching {len(by_pmid)} records by PMID...")
            enriched_entries.extend(self._enrich_by_pmid(by_pmid))
            if self.verbose:
                logger.info("[%d/%d] publications looked up", len(by_pmid), progress_total)

        # 2. PMCID conversions then lookup
        if by_pmcid:
            if self.verbose:
                logger.info(f"\n🔄 Converting {len(by_pmcid)} PMCIDs to PMIDs...")
            enriched_entries.extend(self._enrich_by_pmcid(by_pmcid))
            if self.verbose:
                logger.info("[%d/%d] publications looked up", len(by_pmid) + len(by_pmcid), progress_total)

        # 3. DOI searches then lookup
        if by_doi:
            if self.verbose:
                logger.info(f"\n🔍 Searching PubMed for {len(by_doi)} DOIs...")
            enriched_entries.extend(
                self._enrich_by_doi(by_doi, progress_base=len(by_pmid) + len(by_pmcid), progress_total=progress_total)
            )

        # 4. Entries without identifiers (pass through unchanged)
        for entry in no_id:
            entry['enrichment_status'] = 'no_identifier'
            enriched_entries.append(entry)

        # 4b. One PMID accepted for two entries: only the paper it names keeps it.
        # Before step 5, whose duplicate check reads the PMIDs entries hold.
        self._release_weaker_shared_pmids()

        # 5. "In press" entries: title search, then record what PubMed shows
        self._resolve_in_press(pub_entries)

        # Build output
        output = {
            'document_uid': document_uid,
            'stage': '5',
            'stage_name': 'PubMed Enrichment',
            'source_stage': '4',
            'cv_owner': cv_owner,  # Pass through from Stage 4
            'enrichment_timestamp': datetime.now().isoformat(),
            'stats': self.stats.copy(),
            'entries': entries  # All entries (publications updated in place)
        }

        if self.verbose:
            logger.info(f"\n{'='*60}")
            logger.info(f"Enrichment Summary")
            logger.info(f"{'='*60}")
            logger.info(f"  Total publications: {self.stats['total_publications']}")
            logger.info(f"  Successfully enriched: {self.stats['enriched']}")
            logger.info(f"  Failed lookups: {self.stats['failed_lookups']}")
            logger.info(f"  No identifier: {self.stats['no_identifier']}")

        return output

    def _clean_pmid(self, pmid: Any) -> str | None:
        """Extract clean PMID number."""
        if not pmid:
            return None
        pmid_str = str(pmid).strip()
        # Extract digits only
        match = re.search(r'(\d{7,8})', pmid_str)
        return match.group(1) if match else None

    def _clean_pmcid(self, pmcid: Any) -> str | None:
        """Extract clean PMCID (with PMC prefix)."""
        if not pmcid:
            return None
        pmcid_str = str(pmcid).strip().upper()
        # Ensure PMC prefix
        match = re.search(r'(PMC\d+)', pmcid_str)
        if match:
            return match.group(1)
        # Just digits - add prefix
        match = re.search(r'(\d{6,8})', pmcid_str)
        if match:
            return f"PMC{match.group(1)}"
        return None

    def _clean_doi(self, doi: Any) -> str | None:
        """Extract clean DOI."""
        if not doi:
            return None
        doi_str = str(doi).strip()
        # Extract DOI pattern
        match = re.search(r'(10\.\d{4,}/[^\s]+)', doi_str)
        return match.group(1).rstrip('.,;') if match else None

    def _enrich_by_pmid(
        self, entries_with_pmid: list[tuple[dict, str]], source: str = 'pmid'
    ) -> list[dict]:
        """
        Enrich entries by direct PMID lookup.

        Batches requests for efficiency (up to 200 per request). Entries that
        share a PMID are each checked against the one record (#1219).
        """
        results = []
        pmids = list(dict.fromkeys(pmid for _, pmid in entries_with_pmid))

        # Fetch in batches
        batch_size = 200
        all_records = {}

        for i in range(0, len(pmids), batch_size):
            batch = pmids[i:i + batch_size]
            records = self._fetch_pubmed_batch(batch)
            all_records.update(records)
            self.stats['pmid_lookups'] += len(batch)

            if i + batch_size < len(pmids):
                time.sleep(RATE_LIMIT_DELAY)

        # Merge enrichment data into entries
        for entry, pmid in entries_with_pmid:
            if pmid in all_records:
                self._accept_record(entry, all_records[pmid], source)
            else:
                entry['enrichment_status'] = 'lookup_failed'
                self.stats['failed_lookups'] += 1
            results.append(entry)

        return results

    def _enrich_by_pmcid(self, entries_with_pmcid: list[tuple[dict, str]]) -> list[dict]:
        """
        Enrich entries by PMCID → PMID conversion, then lookup.
        """
        results = []

        # Convert PMCIDs to PMIDs using ID converter
        pmcids = [pmcid for _, pmcid in entries_with_pmcid]
        pmcid_to_pmid = self._convert_pmcids_to_pmids(pmcids)
        self.stats['pmcid_conversions'] += len(pmcids)

        # Now fetch by PMID (ensure strings)
        pmids_to_fetch = [str(pmid) for pmid in pmcid_to_pmid.values() if pmid]

        if pmids_to_fetch:
            records = self._fetch_pubmed_batch(pmids_to_fetch)
        else:
            records = {}

        # Merge results
        for entry, pmcid in entries_with_pmcid:
            pmid = pmcid_to_pmid.get(pmcid)
            pmid_str = str(pmid) if pmid else None
            if pmid_str and pmid_str in records:
                if self._accept_record(entry, records[pmid_str], 'pmcid_conversion'):
                    # Also store the PMID we discovered
                    if 'extracted_fields' in entry:
                        entry['extracted_fields']['pmid'] = pmid_str
            else:
                entry['enrichment_status'] = 'pmcid_conversion_failed'
                self.stats['failed_lookups'] += 1
            results.append(entry)

        self._retry_bare_pmcids_as_pmids(results)
        return results

    def _bare_digits_pmid(self, entry: dict) -> str | None:
        """The PMID-length digit run of a "PMCID" value that has no PMC prefix
        (#1219: a CV labelled 8-digit PMIDs as PMCIDs). "PMCID-123..." and
        "PMCID: 123..." are labels, not prefixes; only "PMC" directly
        followed by a digit marks a real PMCID."""
        raw = str((entry.get('extracted_fields') or {}).get('pmcid') or '')
        if re.search(r'PMC\d', raw.upper()):
            return None
        match = re.search(r'(?<!\d)\d{7,8}(?!\d)', raw)
        return match.group(0) if match else None

    @staticmethod
    def _failure_counts(status: str | None) -> tuple[int, int]:
        """(failed_lookups, title_mismatches) an entry with this status adds."""
        failed = status in PMCID_PATH_FAILURES or status == 'lookup_failed'
        return (1 if failed else 0, 1 if status == 'title_check_failed' else 0)

    def _shift_failure_counts(self, status: str | None, sign: int) -> None:
        failed, mismatched = self._failure_counts(status)
        self.stats['failed_lookups'] += sign * failed
        self.stats['title_mismatches'] += sign * mismatched

    def _retry_bare_pmcids_as_pmids(self, entries: list[dict]) -> None:
        """Second chance for entries the PMCID path failed or title-rejected
        whose "PMCID" has no PMC prefix: look the digits up as a PMID. The
        title check guards the result. An entry the retry does not enrich
        keeps the status and rejection record it had, so an idconv outage is
        never recorded as a title rejection."""
        retry = [(e, self._bare_digits_pmid(e)) for e in entries
                 if e.get('enrichment_status') in PMCID_PATH_FAILURES]
        retry = [(e, pmid) for e, pmid in retry if pmid]
        prior = {id(e): (e['enrichment_status'], e.pop('enrichment_rejected', None))
                 for e, _ in retry}
        for entry, _ in retry:
            self._shift_failure_counts(prior[id(entry)][0], -1)
        self._enrich_by_pmid(retry, source='pmcid_as_pmid')
        for entry, _ in retry:
            status, rejected = prior[id(entry)]
            if entry['enrichment_status'] == 'enriched':
                continue
            self._shift_failure_counts(entry['enrichment_status'], -1)
            self._shift_failure_counts(status, 1)
            entry['enrichment_status'] = status
            entry.pop('enrichment_rejected', None)
            if rejected:
                entry['enrichment_rejected'] = rejected

    def _enrich_by_doi(
        self, entries_with_doi: list[tuple[dict, str]], progress_base: int = 0, progress_total: int | None = None,
    ) -> list[dict]:
        """
        Enrich entries by DOI search in PubMed, then lookup.

        Outcome classes are distinct (#222): 'doi_not_in_pubmed' means esearch
        succeeded with an empty idlist (a normal-vocabulary outcome), while an
        API failure (non-2xx after retries, connection error) is recorded as
        'doi_lookup_failed' so failure lints can surface it.
        """
        results = []

        for entry, doi in entries_with_doi:
            self.stats['doi_searches'] += 1
            search_failed = False
            try:
                pmid = self._search_pmid_by_doi(doi)
            except Exception as e:
                pmid = None
                search_failed = True
                self.stats['api_errors'] += 1
                self._log_api_failure('doi_search', e)
                if self.verbose:
                    # No logger.exception/exc_info: _sanitize_error() redacts the NCBI api_key here; a traceback would leak it unredacted.
                    # Info, not warning/error: _log_api_failure() above already emits the one deduplicated ERROR record per failure class per run; logging this per-citation line at warning/error would reintroduce the flood that dedup exists to prevent (see also _fetch_pubmed_batch and _convert_pmcids_to_pmids below).
                    logger.info(f"    ⚠️ DOI search error for {doi}: {_sanitize_error(e)}")

            if pmid:
                records = self._fetch_pubmed_batch([pmid])
                if pmid in records:
                    if self._accept_record(entry, records[pmid], 'doi_search'):
                        # Store discovered PMID
                        if 'extracted_fields' in entry:
                            entry['extracted_fields']['pmid'] = pmid
                else:
                    entry['enrichment_status'] = 'doi_found_but_fetch_failed'
                    self.stats['failed_lookups'] += 1
            elif search_failed:
                entry['enrichment_status'] = 'doi_lookup_failed'
                self.stats['failed_lookups'] += 1
            else:
                entry['enrichment_status'] = 'doi_not_in_pubmed'
                self.stats['failed_lookups'] += 1

            results.append(entry)
            if self.verbose:
                logger.info(
                    "[%d/%d] publications looked up",
                    progress_base + len(results), progress_total or len(entries_with_doi),
                )
            time.sleep(RATE_LIMIT_DELAY)

        return results

    def _resolve_in_press(self, pub_entries: list[dict]) -> None:
        """An entry the CV calls in press is searched by title when it had no
        usable identifier; once matched by any path it takes PubMed's year,
        leaves S7 for its published code, and carries `in_press_note`, the
        Word comment stage 6 attaches to the tracked change. One PubMed did
        not match still leaves S7, by its own text (#1166)."""
        for entry in pub_entries:
            phrase = listed_in_press(entry)
            if not phrase:
                continue
            fields = entry.get('extracted_fields') or {}
            title = fields.get('title') or fields.get('chapter_title') or ''
            if entry.get('enrichment_status') in _TITLE_SEARCHABLE_STATUSES:
                self._enrich_by_title(entry, title)
            if entry.get('enrichment_status') != 'enriched':
                self._promote_unmatched_in_press(entry)
                continue
            if self._lists_as_published(entry, pub_entries):
                self._record_in_press_duplicate(entry, phrase)
            else:
                self._record_in_press_resolution(entry, phrase)

    def _enrich_by_title(self, entry: dict, title: str) -> None:
        """Accept the best of the top title-search hits when its title
        overlap clears MIN_TITLE_SEARCH_OVERLAP, it shares an author with the
        CV, and its year is plausible for the CV's. A miss leaves the entry as it was: a miss is not evidence the
        paper is unpublished (non-indexed journals, chapters, retitling)."""
        if len(_title_words(title)) < MIN_TITLE_SEARCH_WORDS:
            return
        self.stats['title_searches'] += 1
        try:
            pmids = self._search_pmids_by_title(title)
        except (requests.RequestException, ValueError) as e:
            self.stats['api_errors'] += 1
            self._log_api_failure('title_search', e)
            return
        finally:
            time.sleep(RATE_LIMIT_DELAY)
        records = self._fetch_pubmed_batch(pmids) if pmids else {}
        # A preprint is the paper's earlier version, never what "in press"
        # names (QZWBKQ, dev-239: an "Appl Environ Microbiol, in press" entry
        # matched its bioRxiv record, PubMed type Preprint); an erratum is a
        # notice about the paper (QNZADH, dev-242).
        scored = [(title_word_overlap(title, [r['title'], r['vernacular_title']]) or 0,
                   COMMENT_PUBTYPE not in r['publication_types'], r)
                  for r in records.values()
                  if not TITLE_SEARCH_EXCLUDED_PUBTYPES.intersection(r['publication_types'])]
        if not scored:
            return
        overlap, _, best = max(scored, key=lambda hit: hit[:2])
        fields = entry.get('extracted_fields') or {}
        if (overlap >= MIN_TITLE_SEARCH_OVERLAP
                and shares_an_author(fields.get('authors'), best['authors'])
                and plausible_publication_year(fields.get('year'), best['year'])):
            self._accept_record(entry, best, 'title_search')

    def _search_pmids_by_title(self, title: str) -> list[str]:
        params = {
            'db': 'pubmed',
            'term': f'{title}[ti]',
            'retmode': 'json',
            'retmax': TITLE_SEARCH_MAX_HITS,
            **self._identity_params()
        }
        if self.api_key:
            params['api_key'] = self.api_key
        response = self._get_with_retry(ESEARCH_URL, params)
        return response.json().get('esearchresult', {}).get('idlist', [])

    def _record_in_press_resolution(self, entry: dict, phrase: str) -> None:
        enrichment = entry.get('enrichment_data') or {}
        pmid, year = enrichment.get('pubmed_pmid'), enrichment.get('pubmed_year')
        fields = entry.setdefault('extracted_fields', {})
        if year and str(fields.get('year')) != str(year):
            fields['year'] = year
            entry.setdefault('enriched_fields', []).append('year')
        if entry.get('taxonomy_code') == IN_REVIEW_CODE:
            code = published_taxonomy_code(enrichment.get('publication_types') or [])
            entry['taxonomy_code'] = code
            if entry.get('classification_reasoning'):
                entry['classification_reasoning'] += (
                    f" Moved from {IN_REVIEW_CODE} to {code}: PubMed PMID {pmid} shows it published.")
        published = f", published {year}" if year else ""
        entry['in_press_note'] = (f"Found in PubMed as PMID {pmid}{published}; "
                                  f"the CV listed it as {phrase}.")
        self.stats['in_press_resolved'] += 1

    def _lists_as_published(self, entry: dict, pub_entries: list[dict]) -> bool:
        """Whether another published-article entry of this CV is the paper
        `entry` was matched to: the same PMID, or a title clearing
        MIN_TITLE_SEARCH_OVERLAP measured against the LONGER title, with the
        same year. Both guards are corpus incidents (2026-10-02): 7 of 8
        title-only candidates were same-group papers in another year, and
        web228's 2018 Sci Rep meta-analysis has a short title nested inside
        the in-press paper's, which a shorter-title overlap scores as a match."""
        enrichment = entry.get('enrichment_data') or {}
        pmid, year = enrichment.get('pubmed_pmid'), enrichment.get('pubmed_year')
        for other in pub_entries:
            if other is entry or other.get('taxonomy_code') not in PUBLISHED_ARTICLE_CODES:
                continue
            fields = other.get('extracted_fields') or {}
            if pmid and str(fields.get('pmid')) == str(pmid):
                return True
            if (year and str(fields.get('year')) == str(year)
                    and _same_title(fields.get('title') or '', enrichment.get('pubmed_title') or '')):
                return True
        return False

    def _promote_unmatched_in_press(self, entry: dict) -> None:
        """An S7 entry the CV calls in press that PubMed did not match (a
        non-indexed journal, a chapter, a retitled paper) moves to S4 or S1.
        Stage 4 extracted it with S7's schema, whose venue key is
        `target_journal`; the citation renderer reads `journal`."""
        if entry.get('taxonomy_code') != IN_REVIEW_CODE:
            return
        is_chapter = bool(CHAPTER_PATTERN.search(entry.get('text') or ''))
        code = UNMATCHED_CHAPTER_CODE if is_chapter else UNMATCHED_DEFAULT_CODE
        entry['taxonomy_code'] = code
        fields = entry.setdefault('extracted_fields', {})
        if not is_chapter and not fields.get('journal') and fields.get('target_journal'):
            fields['journal'] = fields['target_journal']
        if entry.get('classification_reasoning'):
            entry['classification_reasoning'] += (
                f" Moved from {IN_REVIEW_CODE} to {code}: the CV lists it as accepted or in press.")
        self.stats['in_press_promoted'] += 1

    def _record_in_press_duplicate(self, entry: dict, phrase: str) -> None:
        """Stage 6 renders this entry as a tracked deletion, not a replacement:
        replacing it would print the paper twice. Its code and year stay."""
        pmid = (entry.get('enrichment_data') or {}).get('pubmed_pmid')
        entry['in_press_superseded'] = True
        entry['in_press_note'] = (f"Already listed as published (PMID {pmid}); "
                                  f"the CV also listed it as {phrase}.")
        self.stats['in_press_duplicates'] += 1

    def _identity_params(self) -> dict[str, str]:
        """NCBI identification params; email omitted when not configured."""
        params = {'tool': 'scholar_signals_cv_pipeline'}
        if PUBMED_CONTACT_EMAIL:
            params['email'] = PUBMED_CONTACT_EMAIL
        return params

    def _log_api_failure(self, operation: str, error: Exception) -> None:
        """
        Log an API failure at ERROR level, once per failure class per run.

        A bad API key fails every call identically (e.g. HTTP 400
        {"error":"API key invalid"}); per-citation logging would flood the log
        while burying the one response body that explains the failure.
        """
        response = getattr(error, 'response', None)
        status = getattr(response, 'status_code', None)
        failure_class = (operation, status if status is not None else type(error).__name__)
        if failure_class in self._logged_failure_classes:
            return
        self._logged_failure_classes.add(failure_class)

        message = f"PubMed {operation} failed: {_sanitize_error(error)}"
        body = str(getattr(response, 'text', '') or '').strip()
        if body:
            message += f" | response body: {_sanitize_error(body[:500])}"
        logger.error(message)

    @staticmethod
    def _retry_delay(attempt: int, retry_after: str | None) -> float:
        """Exponential backoff for this attempt, raised to a numeric Retry-After."""
        delay = BACKOFF_BASE_SECONDS * (2 ** attempt)
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                pass  # HTTP-date Retry-After: deliberately ignored, backoff applies
        return delay

    def _get_with_retry(
        self,
        url: str,
        params: dict[str, Any],
        max_attempts: int = MAX_ATTEMPTS,
        total_wait_cap: float | None = None,
    ) -> requests.Response:
        """
        HTTP GET with retry on transient failures.

        Retries 429s, 5xx responses, and connection/timeout errors up to
        max_attempts total attempts with exponential backoff (1s, 2s, ...),
        honoring a Retry-After header when larger. When total_wait_cap is set,
        a retry whose delay would push the summed waits past it is not taken
        and the last error raises. Other HTTP errors (e.g. 404) raise
        immediately, exactly as before.
        """
        last_error = None
        waited = 0.0
        for attempt in range(max_attempts):
            retry_after = None
            try:
                response = self.session.get(url, params=params, timeout=30)
                if response.status_code == 429 or response.status_code >= 500:
                    retry_after = response.headers.get('Retry-After')
                    last_error = requests.HTTPError(
                        f"{response.status_code} transient error for url: {response.url}",
                        response=response
                    )
                else:
                    response.raise_for_status()  # non-transient 4xx raises here
                    return response
            except (requests.ConnectionError, requests.Timeout) as e:
                last_error = e

            if attempt < max_attempts - 1:
                delay = self._retry_delay(attempt, retry_after)
                if total_wait_cap is not None and waited + delay > total_wait_cap:
                    break
                waited += delay
                if self.verbose:
                    logger.warning(f"    ⏳ Transient API error ({_sanitize_error(last_error)}); "
                                   f"retry {attempt + 1}/{max_attempts - 1} in {delay:g}s")
                time.sleep(delay)

        raise last_error

    def _fetch_pubmed_batch(self, pmids: list[str]) -> dict[str, dict]:
        """
        Fetch multiple PubMed records via efetch.
        """
        if not pmids:
            return {}

        try:
            params = {
                'db': 'pubmed',
                'id': ','.join(pmids),
                'retmode': 'xml',
                'rettype': 'abstract',
                **self._identity_params()
            }
            if self.api_key:
                params['api_key'] = self.api_key

            response = self._get_with_retry(EFETCH_URL, params)

            root = ET.fromstring(response.content)

            results = {}
            for article in root.findall('.//PubmedArticle'):
                pmid, record = self._parse_pubmed_article(article)
                if pmid:
                    results[pmid] = record

            if self.verbose:
                logger.info(f"    → Fetched {len(results)}/{len(pmids)} records")

            return results

        except Exception as e:
            self.stats['api_errors'] += 1
            self._log_api_failure('efetch', e)
            if self.verbose:
                # Info, not error: see the note at _enrich_by_doi's DOI search error above.
                logger.info(f"    ❌ API error: {_sanitize_error(e)}")
            return {}

    def _parse_pubmed_article(self, article: ET.Element) -> tuple[str | None, dict | None]:
        """
        Parse a single PubmedArticle XML element.
        """
        try:
            pmid_elem = article.find('.//PMID')
            if pmid_elem is None or not pmid_elem.text:
                return None, None
            pmid = pmid_elem.text.strip()

            # Title. itertext(), not .text: .text stops at the first inline
            # element, so "Control of <i>Gene</i> ..." was stored as
            # "Control of " (QNZADH, AKPQEB, dev-242).
            title_elem = article.find('.//ArticleTitle')
            title = ''.join(title_elem.itertext()) if title_elem is not None else ''
            vernacular_elem = article.find('.//VernacularTitle')
            vernacular_title = ''.join(vernacular_elem.itertext()) if vernacular_elem is not None else ''

            # Journal
            journal_elem = article.find('.//Journal/Title')
            journal = journal_elem.text if journal_elem is not None and journal_elem.text else ''

            # Journal abbreviation (ISO)
            journal_iso_elem = article.find('.//ISOAbbreviation')
            journal_iso = journal_iso_elem.text if journal_iso_elem is not None else ''

            # Volume, issue, pages
            volume = self._get_text(article, './/Volume')
            issue = self._get_text(article, './/Issue')
            pages = self._get_text(article, './/MedlinePgn')

            # Year
            year = None
            year_elem = article.find('.//PubDate/Year')
            if year_elem is not None and year_elem.text:
                try:
                    year = int(year_elem.text)
                except ValueError:
                    pass

            # DOI and PMCID
            doi = own_article_id(article, ID_TYPE_DOI)
            pmcid = own_article_id(article, ID_TYPE_PMC)

            # Authors in Vancouver format
            authors = []
            for author_elem in article.findall('.//Author'):
                last_name = self._get_text(author_elem, 'LastName')
                initials = self._get_text(author_elem, 'Initials')
                if last_name:
                    if initials:
                        authors.append(f"{last_name} {initials}")
                    else:
                        authors.append(last_name)

            # Publication types
            pub_types = []
            for pt_elem in article.findall('.//PublicationType'):
                if pt_elem.text:
                    pub_types.append(pt_elem.text)

            # MeSH terms
            mesh_terms = []
            for mesh_elem in article.findall('.//MeshHeading/DescriptorName'):
                if mesh_elem.text:
                    mesh_terms.append(mesh_elem.text)

            record = {
                'pmid': pmid,
                'title': title,
                'vernacular_title': vernacular_title,
                'journal': journal,
                'journal_iso': journal_iso,
                'volume': volume,
                'issue': issue,
                'pages': pages,
                'year': year,
                'doi': doi,
                'pmcid': pmcid,
                'authors': authors,
                'authors_vancouver': ', '.join(authors),
                'publication_types': pub_types,
                'mesh_terms': mesh_terms
            }

            return pmid, record

        except Exception as e:
            if self.verbose:
                logger.warning(f"    ⚠️ Parse error: {e}")
            return None, None

    def _get_text(self, elem: ET.Element, path: str) -> str:
        """Safely get text from XML element."""
        found = elem.find(path)
        return found.text.strip() if found is not None and found.text else ''

    def _convert_pmcids_to_pmids(self, pmcids: list[str]) -> dict[str, str]:
        """
        Convert PMCIDs to PMIDs using NCBI ID converter.
        """
        if not pmcids:
            return {}

        try:
            params = {
                'ids': ','.join(pmcids),
                'format': 'json',
                **self._identity_params()
            }

            response = self._get_with_retry(
                ID_CONVERTER_URL, params,
                max_attempts=IDCONV_MAX_ATTEMPTS,
                total_wait_cap=IDCONV_RETRY_TOTAL_WAIT_CAP_SECONDS,
            )

            data = response.json()

            result = {}
            for record in data.get('records', []):
                pmcid = record.get('pmcid', '')
                pmid = record.get('pmid', '')
                if pmcid and pmid:
                    result[pmcid] = pmid

            if self.verbose:
                logger.info(f"    → Converted {len(result)}/{len(pmcids)} PMCIDs to PMIDs")

            return result

        except Exception as e:
            self.stats['api_errors'] += 1
            self._log_api_failure('pmcid_conversion', e)
            if self.verbose:
                # Info, not error: see the note at _enrich_by_doi's DOI search error above.
                logger.info(f"    ❌ ID conversion error: {_sanitize_error(e)}")
            return {}

    def _search_pmid_by_doi(self, doi: str) -> str | None:
        """
        Search PubMed for a DOI to get the PMID.

        Returns None only when esearch succeeds with an empty idlist (DOI
        genuinely not in PubMed). API failures propagate to the caller so
        they are not conflated with a legitimate no-match (#222).
        """
        params = {
            'db': 'pubmed',
            # Quoted: unquoted, PubMed re-tags the text after '/' as
            # [Publisher ID] and returns unrelated PMIDs (#1219).
            'term': f'"{doi.replace(chr(34), "")}"[doi]',
            'retmode': 'json',
            **self._identity_params()
        }
        if self.api_key:
            params['api_key'] = self.api_key

        response = self._get_with_retry(ESEARCH_URL, params)

        data = response.json()
        id_list = data.get('esearchresult', {}).get('idlist', [])

        if id_list:
            return id_list[0]  # First match
        return None

    def _accept_record(self, entry: dict, pubmed_record: dict, source: str) -> bool:
        """Merge the record into the entry unless its title names a different
        paper (#1043): a wrong PMID/PMCID/DOI in the CV, or two entries'
        PMIDs swapped, otherwise replaces the citation with an unrelated one.
        A rejected entry keeps its CV-extracted fields; the rejected record is
        kept under `enrichment_rejected` for audit."""
        fields = entry.get('extracted_fields') or {}
        source_title = fields.get('title') or fields.get('chapter_title') or ''
        overlap = title_word_overlap(
            source_title,
            [pubmed_record.get('title') or '', pubmed_record.get('vernacular_title') or ''],
        )
        if overlap is not None and overlap < MIN_TITLE_WORD_OVERLAP:
            self._reject_record(entry, source, pubmed_record.get('pmid'),
                                pubmed_record.get('title'), overlap)
            return False
        self._acceptances.append(_Acceptance(
            entry, copy.deepcopy(entry), pubmed_record.get('pmid'),
            pubmed_record.get('title'), source, overlap))
        self._merge_pubmed_data(entry, pubmed_record)
        entry['enrichment_status'] = 'enriched'
        entry['enrichment_source'] = source
        self.stats['enriched'] += 1
        return True

    def _reject_record(self, entry: dict, source: str, pmid: str | None,
                       pubmed_title: str | None, overlap: float,
                       shared_pmid_with: int | None = None) -> None:
        """Leave the entry on its CV-extracted fields, with the record it
        refused kept under `enrichment_rejected` for audit. A refusal because
        another entry matches the record better names that entry's
        element_idx_start."""
        entry['enrichment_status'] = 'title_check_failed'
        entry['enrichment_rejected'] = {
            'source': source,
            'pubmed_pmid': pmid,
            'pubmed_title': pubmed_title,
            'title_word_overlap': round(overlap, 2),
        }
        if shared_pmid_with is not None:
            entry['enrichment_rejected']['shared_pmid_with'] = shared_pmid_with
        self.stats['title_mismatches'] += 1
        self.stats['failed_lookups'] += 1

    def _release_weaker_shared_pmids(self) -> None:
        """When one PMID was accepted for several entries, the entry whose
        title matches it best keeps it, and so does any other whose title
        clears MIN_TITLE_SEARCH_OVERLAP (the same paper listed twice). Any
        other holder is a different paper that cleared the ID paths' looser
        MIN_TITLE_WORD_OVERLAP on shared topic words, and is put back as it
        was before the record was merged (AQCLHS, dev-242: a CV gave two
        consecutive papers one DOI, and the second, at 0.43, was replaced by
        a copy of the first). Order-independent, so it runs once every ID
        path is done. An untitled entry has no overlap to compare and is
        left alone. An entry the CV lists as in press can keep the PMID but
        is never released: the in-press step marks it superseded when a
        published entry holds the PMID (a paper retitled after acceptance),
        and a release would promote it to a second copy of that paper."""
        holders: dict[str, list[_Acceptance]] = {}
        for accepted in self._acceptances:
            if accepted.overlap is not None:
                holders.setdefault(accepted.pmid, []).append(accepted)
        for group in holders.values():
            best = max(group, key=lambda accepted: accepted.overlap)
            for accepted in group:
                if (accepted.overlap < min(best.overlap, MIN_TITLE_SEARCH_OVERLAP)
                        and not listed_in_press(accepted.before)):
                    self._restore_and_reject(accepted, best)

    def _restore_and_reject(self, accepted: _Acceptance, keeper: _Acceptance) -> None:
        entry = accepted.entry
        entry.clear()
        entry.update(accepted.before)
        self.stats['enriched'] -= 1
        self._reject_record(entry, accepted.source, accepted.pmid, accepted.pubmed_title,
                            accepted.overlap,
                            shared_pmid_with=keeper.entry.get('element_idx_start'))

    def _merge_pubmed_data(self, entry: dict, pubmed_record: dict):
        """
        Merge PubMed data into the entry's extracted_fields.

        Strategy:
        - Add missing fields from PubMed
        - Store PubMed's version in 'pubmed_' prefixed fields for comparison
        - Track what was enriched
        """
        fields = entry.get('extracted_fields', {})

        # Create enrichment_data section
        entry['enrichment_data'] = {
            'pubmed_pmid': pubmed_record.get('pmid'),
            'pubmed_title': pubmed_record.get('title'),
            'pubmed_journal': pubmed_record.get('journal'),
            'pubmed_journal_iso': pubmed_record.get('journal_iso'),
            'pubmed_authors': pubmed_record.get('authors_vancouver'),
            'pubmed_volume': pubmed_record.get('volume'),
            'pubmed_issue': pubmed_record.get('issue'),
            'pubmed_pages': pubmed_record.get('pages'),
            'pubmed_year': pubmed_record.get('year'),
            'pubmed_doi': pubmed_record.get('doi'),
            'pubmed_pmcid': pubmed_record.get('pmcid'),
            'publication_types': pubmed_record.get('publication_types', []),
            'mesh_terms': pubmed_record.get('mesh_terms', [])
        }

        # Stage 6 renders pubmed_authors in place of the CV's list, so a
        # PubMed list that cut the owner out is withheld and the CV's list
        # renders; PubMed's is kept beside it for audit.
        if pubmed_list_drops_the_owner(fields.get('authors'), fields.get('target_name'),
                                       pubmed_record.get('authors') or []):
            enrichment = entry['enrichment_data']
            enrichment['pubmed_authors_without_owner'] = enrichment['pubmed_authors']
            enrichment['pubmed_authors'] = None

        # Fill in missing identifier fields
        if not fields.get('pmid') and pubmed_record.get('pmid'):
            fields['pmid'] = pubmed_record['pmid']
            entry.setdefault('enriched_fields', []).append('pmid')

        if not fields.get('pmcid') and pubmed_record.get('pmcid'):
            fields['pmcid'] = pubmed_record['pmcid']
            entry.setdefault('enriched_fields', []).append('pmcid')

        if not fields.get('doi') and pubmed_record.get('doi'):
            fields['doi'] = pubmed_record['doi']
            entry.setdefault('enriched_fields', []).append('doi')

        # Fill in missing citation fields
        if not fields.get('volume') and pubmed_record.get('volume'):
            fields['volume'] = pubmed_record['volume']
            entry.setdefault('enriched_fields', []).append('volume')

        if not fields.get('issue') and pubmed_record.get('issue'):
            fields['issue'] = pubmed_record['issue']
            entry.setdefault('enriched_fields', []).append('issue')

        if not fields.get('pages') and pubmed_record.get('pages'):
            fields['pages'] = pubmed_record['pages']
            entry.setdefault('enriched_fields', []).append('pages')


def run_stage5(stage4_path: str, output_path: str | None = None, verbose: bool = True) -> dict[str, Any]:
    """
    Run Stage 5 enrichment on a Stage 4 output file.

    Args:
        stage4_path: Path to Stage 4 JSON file
        output_path: Optional output path (default: outputs/stage_5_enrichment/)
        verbose: Print progress

    Returns:
        Enriched output dict, with 'output_path' set to the file written
    """
    enricher = PubMedEnricher(verbose=verbose)
    result = enricher.enrich_stage4_output(stage4_path)

    # Save output
    if output_path is None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        doc_uid = result.get('document_uid', 'unknown')
        output_path = OUTPUT_DIR / f"{doc_uid}_enriched.json"

    with open(output_path, 'w') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    if verbose:
        logger.info(f"\n✅ Enriched output saved to: {output_path}")

    # Additive, and after the write so the on-disk artifact is unchanged: the
    # caller reads the path this stage actually used instead of rebuilding it.
    result['output_path'] = str(output_path)
    return result


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='Stage 5: PubMed Enrichment')
    parser.add_argument('input', help='Stage 4 output file or document UID')
    parser.add_argument('--output', '-o', help='Output file path')
    parser.add_argument('--quiet', '-q', action='store_true', help='Suppress progress output')

    args = parser.parse_args()

    # Resolve input path
    input_path = args.input
    if not os.path.exists(input_path):
        # Try looking in stage 4 outputs
        stage4_dir = Path(__file__).parent / "outputs" / "stage_4_field_extraction"
        candidates = list(stage4_dir.glob(f"*{input_path}*_fields.json"))
        if candidates:
            input_path = str(candidates[0])
        else:
            logger.error(f"Error: Could not find input file: {args.input}")
            sys.exit(1)

    result = run_stage5(input_path, args.output, verbose=not args.quiet)

    # Print summary stats
    stats = result.get('stats', {})
    logger.info(f"\nEnrichment complete:")
    logger.info(f"  {stats.get('enriched', 0)}/{stats.get('total_publications', 0)} publications enriched")


if __name__ == '__main__':
    main()
