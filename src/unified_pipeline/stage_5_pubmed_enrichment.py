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

import os
import sys
import json
import logging
import re
import time
import requests
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


# Configuration
NCBI_API_KEY = os.getenv('NCBI_API_KEY', os.getenv('PUBMED_API_KEY', ''))
PUBMED_CONTACT_EMAIL = os.getenv('PUBMED_CONTACT_EMAIL', os.getenv('NCBI_CONTACT_EMAIL', ''))
RATE_LIMIT_DELAY = 0.1 if NCBI_API_KEY else 0.34  # 10/s with key, 3/s without

# Retry policy for transient NCBI API failures (429 / 5xx / connection errors)
MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 1.0  # 1s, 2s, ... doubling; Retry-After honored when larger

# Output directory
OUTPUT_DIR = Path(__file__).parent / "outputs" / "stage_5_enrichment"

# NCBI API endpoints
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ELINK_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"
ID_CONVERTER_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"


def _sanitize_error(error: Any) -> str:
    """
    Redact NCBI API keys from error text before it reaches logs.

    Exception messages from requests embed the full request URL, which
    includes api_key as a query parameter.
    """
    return re.sub(r'api_key=[^&\s]+', 'api_key=***', str(error))


class PubMedEnricher:
    """
    Enriches CV publication entries with PubMed data.

    Supports lookup by:
    - PMID (direct)
    - PMCID (via ID converter)
    - DOI (via esearch)
    """

    def __init__(self, api_key: str = None, verbose: bool = True):
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
            'api_errors': 0
        }

        # (operation, status/exception) classes already logged at ERROR this run
        self._logged_failure_classes = set()

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

        if self.verbose:
            print(f"\n{'='*60}")
            print(f"Stage 5: PubMed Enrichment - {document_uid}")
            print(f"{'='*60}")

        # Identify publication entries (S1-S9)
        pub_entries = [e for e in entries if e.get('taxonomy_code', '').startswith('S')
                       and e.get('taxonomy_code') not in ('S0',)]

        self.stats['total_publications'] = len(pub_entries)

        if self.verbose:
            print(f"\nFound {len(pub_entries)} publication entries to enrich")

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
            print(f"  - {len(by_pmid)} with PMID (direct lookup)")
            print(f"  - {len(by_pmcid)} with PMCID only (needs conversion)")
            print(f"  - {len(by_doi)} with DOI only (needs search)")
            print(f"  - {len(no_id)} without identifiers (skip)")

        # Process each category
        enriched_entries = []

        # 1. Direct PMID lookups (batch)
        if by_pmid:
            if self.verbose:
                print(f"\n📚 Fetching {len(by_pmid)} records by PMID...")
            enriched_entries.extend(self._enrich_by_pmid(by_pmid))

        # 2. PMCID conversions then lookup
        if by_pmcid:
            if self.verbose:
                print(f"\n🔄 Converting {len(by_pmcid)} PMCIDs to PMIDs...")
            enriched_entries.extend(self._enrich_by_pmcid(by_pmcid))

        # 3. DOI searches then lookup
        if by_doi:
            if self.verbose:
                print(f"\n🔍 Searching PubMed for {len(by_doi)} DOIs...")
            enriched_entries.extend(self._enrich_by_doi(by_doi))

        # 4. Entries without identifiers (pass through unchanged)
        for entry in no_id:
            entry['enrichment_status'] = 'no_identifier'
            enriched_entries.append(entry)

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
            print(f"\n{'='*60}")
            print(f"Enrichment Summary")
            print(f"{'='*60}")
            print(f"  Total publications: {self.stats['total_publications']}")
            print(f"  Successfully enriched: {self.stats['enriched']}")
            print(f"  Failed lookups: {self.stats['failed_lookups']}")
            print(f"  No identifier: {self.stats['no_identifier']}")

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

    def _enrich_by_pmid(self, entries_with_pmid: list[tuple[dict, str]]) -> list[dict]:
        """
        Enrich entries by direct PMID lookup.

        Batches requests for efficiency (up to 200 per request).
        """
        results = []
        pmids = [pmid for _, pmid in entries_with_pmid]
        entry_map = {pmid: entry for entry, pmid in entries_with_pmid}

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
        for pmid, entry in entry_map.items():
            if pmid in all_records:
                self._merge_pubmed_data(entry, all_records[pmid])
                entry['enrichment_status'] = 'enriched'
                entry['enrichment_source'] = 'pmid'
                self.stats['enriched'] += 1
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
                self._merge_pubmed_data(entry, records[pmid_str])
                # Also store the PMID we discovered
                if 'extracted_fields' in entry:
                    entry['extracted_fields']['pmid'] = pmid_str
                entry['enrichment_status'] = 'enriched'
                entry['enrichment_source'] = 'pmcid_conversion'
                self.stats['enriched'] += 1
            else:
                entry['enrichment_status'] = 'pmcid_conversion_failed'
                self.stats['failed_lookups'] += 1
            results.append(entry)

        return results

    def _enrich_by_doi(self, entries_with_doi: list[tuple[dict, str]]) -> list[dict]:
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
                    print(f"    ⚠️ DOI search error for {doi}: {_sanitize_error(e)}")

            if pmid:
                records = self._fetch_pubmed_batch([pmid])
                if pmid in records:
                    self._merge_pubmed_data(entry, records[pmid])
                    # Store discovered PMID
                    if 'extracted_fields' in entry:
                        entry['extracted_fields']['pmid'] = pmid
                    entry['enrichment_status'] = 'enriched'
                    entry['enrichment_source'] = 'doi_search'
                    self.stats['enriched'] += 1
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
            time.sleep(RATE_LIMIT_DELAY)

        return results

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

    def _get_with_retry(self, url: str, params: dict[str, Any]) -> requests.Response:
        """
        HTTP GET with retry on transient failures.

        Retries 429s, 5xx responses, and connection/timeout errors up to
        MAX_ATTEMPTS total attempts with exponential backoff (1s, 2s, ...),
        honoring a Retry-After header when larger. Other HTTP errors
        (e.g. 404) raise immediately, exactly as before.
        """
        last_error = None
        for attempt in range(MAX_ATTEMPTS):
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

            if attempt < MAX_ATTEMPTS - 1:
                delay = BACKOFF_BASE_SECONDS * (2 ** attempt)
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        pass
                if self.verbose:
                    print(f"    ⏳ Transient API error ({_sanitize_error(last_error)}); "
                          f"retry {attempt + 1}/{MAX_ATTEMPTS - 1} in {delay:g}s")
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
                print(f"    → Fetched {len(results)}/{len(pmids)} records")

            return results

        except Exception as e:
            self.stats['api_errors'] += 1
            self._log_api_failure('efetch', e)
            if self.verbose:
                print(f"    ❌ API error: {_sanitize_error(e)}")
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

            # Title
            title_elem = article.find('.//ArticleTitle')
            title = title_elem.text if title_elem is not None and title_elem.text else ''

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
            doi = ''
            pmcid = ''
            for id_elem in article.findall('.//ArticleId'):
                id_type = id_elem.get('IdType')
                if id_type == 'doi' and id_elem.text:
                    doi = id_elem.text.strip()
                elif id_type == 'pmc' and id_elem.text:
                    pmcid = id_elem.text.strip()

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
                print(f"    ⚠️ Parse error: {e}")
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

            response = self._get_with_retry(ID_CONVERTER_URL, params)

            data = response.json()

            result = {}
            for record in data.get('records', []):
                pmcid = record.get('pmcid', '')
                pmid = record.get('pmid', '')
                if pmcid and pmid:
                    result[pmcid] = pmid

            if self.verbose:
                print(f"    → Converted {len(result)}/{len(pmcids)} PMCIDs to PMIDs")

            return result

        except Exception as e:
            self.stats['api_errors'] += 1
            self._log_api_failure('pmcid_conversion', e)
            if self.verbose:
                print(f"    ❌ ID conversion error: {_sanitize_error(e)}")
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
            'term': f'{doi}[doi]',
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


def run_stage5(stage4_path: str, output_path: str = None, verbose: bool = True) -> dict[str, Any]:
    """
    Run Stage 5 enrichment on a Stage 4 output file.

    Args:
        stage4_path: Path to Stage 4 JSON file
        output_path: Optional output path (default: outputs/stage_5_enrichment/)
        verbose: Print progress

    Returns:
        Enriched output dict
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
        print(f"\n✅ Enriched output saved to: {output_path}")

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
            print(f"Error: Could not find input file: {args.input}")
            sys.exit(1)

    result = run_stage5(input_path, args.output, verbose=not args.quiet)

    # Print summary stats
    stats = result.get('stats', {})
    print(f"\nEnrichment complete:")
    print(f"  {stats.get('enriched', 0)}/{stats.get('total_publications', 0)} publications enriched")


if __name__ == '__main__':
    main()
