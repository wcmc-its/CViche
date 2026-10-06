#!/usr/bin/env python3
"""
Bulk PubMed Record Fetcher with Caching

Efficiently fetches PubMed records in batches (up to 200 at a time).
Caches results in database to avoid re-fetching.
Stores both parsed JSON and raw XML for future re-parsing.

Author: Scholar Signals CV Pipeline
Date: 2025-11-09
"""

import os
import json
import time
import hashlib
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

from .pubmed_xml import ID_TYPE_DOI, ID_TYPE_PMC, own_article_id


class BulkPubMedFetcher:
    """
    Efficiently fetch PubMed records in batches with database caching.

    Features:
    - Batch fetching (up to 200 PMIDs per request)
    - Database caching to avoid re-fetching
    - Stores both parsed JSON and raw XML
    - Automatic cache refresh for stale records
    - Rate limiting compliance

    Usage:
        fetcher = BulkPubMedFetcher(api_key=PUBMED_API_KEY, db_connection=db)
        records = fetcher.fetch_records_bulk(['12345', '67890', ...])
    """

    def __init__(self, api_key: str | None = None, db_connection=None, verbose: bool = True):
        """
        Initialize the bulk fetcher.

        Args:
            api_key: NCBI E-utilities API key (optional, but recommended)
            db_connection: Database connection for caching
            verbose: Print progress messages
        """
        self.api_key = api_key or os.getenv('PUBMED_API_KEY', '')
        self.db = db_connection
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Scholar-Signals-CV-Pipeline/1.0'})

        # Configuration
        self.batch_size = 200  # PubMed efetch max
        self.rate_limit_delay = 0.1 if self.api_key else 0.34  # 10 req/s with key, 3 req/s without
        self.parser_version = 'v1.0'

        # E-utilities endpoints
        self.efetch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

    def fetch_records_bulk(self, pmids: list[str], force_refresh: bool = False) -> dict[str, dict]:
        """
        Fetch multiple PubMed records efficiently.

        Strategy:
        1. Check cache (database) first
        2. Filter out fresh cached records
        3. Batch-fetch missing/stale records from PubMed
        4. Store fetched records in cache
        5. Return combined results

        Args:
            pmids: List of PMIDs to fetch
            force_refresh: Ignore cache and re-fetch all

        Returns:
            Dict mapping PMID → PubMedRecord
        """
        if not pmids:
            return {}

        # Clean PMIDs (remove None, empty, duplicates)
        pmids = list(set([str(p).strip() for p in pmids if p]))

        if self.verbose:
            print(f"📚 Fetching PubMed records for {len(pmids)} PMIDs...")

        results = {}

        # Step 1: Check cache (unless force refresh)
        cached_pmids = set()
        if self.db and not force_refresh:
            cached_records = self._load_from_cache(pmids)

            # Filter out stale records
            for pmid, record in cached_records.items():
                if not self._should_refresh_cache(record):
                    results[pmid] = record
                    cached_pmids.add(pmid)

            if self.verbose and results:
                print(f"  ✓ Found {len(results)}/{len(pmids)} fresh records in cache")

        # Step 2: Fetch missing/stale from PubMed in batches
        missing_pmids = [p for p in pmids if p not in cached_pmids]

        if missing_pmids:
            if self.verbose:
                print(f"  ⬇ Fetching {len(missing_pmids)} records from PubMed...")

            for i in range(0, len(missing_pmids), self.batch_size):
                batch = missing_pmids[i:i + self.batch_size]
                batch_num = i // self.batch_size + 1
                total_batches = (len(missing_pmids) - 1) // self.batch_size + 1

                if self.verbose:
                    print(f"    Batch {batch_num}/{total_batches}: {len(batch)} PMIDs")

                batch_results = self._fetch_batch_from_pubmed(batch)
                results.update(batch_results)

                # Step 3: Cache immediately
                if self.db and batch_results:
                    self._save_to_cache(batch_results)

                # Rate limiting between batches
                if i + self.batch_size < len(missing_pmids):
                    time.sleep(self.rate_limit_delay)

        if self.verbose:
            print(f"  ✅ Total: {len(results)}/{len(pmids)} records retrieved")

        return results

    def _fetch_batch_from_pubmed(self, pmids: list[str]) -> dict[str, dict]:
        """
        Single batch fetch from PubMed efetch API.

        Uses comma-separated PMIDs: efetch.fcgi?id=12345,67890,11111

        Args:
            pmids: List of PMIDs (max 200)

        Returns:
            Dict mapping PMID → parsed record
        """
        if not pmids:
            return {}

        try:
            # Join PMIDs with commas (PubMed supports this)
            pmid_string = ",".join(pmids)

            params = {
                'db': 'pubmed',
                'id': pmid_string,
                'retmode': 'xml',
                'rettype': 'abstract',
                'tool': 'scholar_signals_cv_pipeline',
            }
            if self.api_key:
                params['api_key'] = self.api_key

            response = self.session.get(
                self.efetch_url,
                params=params,
                timeout=30  # Longer timeout for bulk requests
            )
            response.raise_for_status()

            # Parse XML response (contains multiple <PubmedArticle> elements)
            root = ET.fromstring(response.content)

            results = {}
            for article in root.findall('.//PubmedArticle'):
                pmid, record = self._parse_pubmed_article(article)
                if pmid:
                    results[pmid] = record

            if self.verbose:
                print(f"      → Fetched {len(results)}/{len(pmids)} successfully")

            return results

        except requests.exceptions.RequestException as e:
            print(f"      ❌ API request error: {e}")
            return {}
        except ET.ParseError as e:
            print(f"      ❌ XML parse error: {e}")
            return {}
        except Exception as e:
            print(f"      ❌ Unexpected error: {e}")
            return {}

    def _parse_pubmed_article(self, article: ET.Element) -> tuple[str | None, dict | None]:
        """
        Parse a single PubmedArticle XML element.

        Args:
            article: XML Element for a single PubmedArticle

        Returns:
            (pmid, record_dict) or (None, None) if parsing fails
        """
        try:
            # Extract PMID (required)
            pmid_elem = article.find('.//PMID')
            if pmid_elem is None or not pmid_elem.text:
                return None, None
            pmid = pmid_elem.text.strip()

            # Extract basic metadata
            title_elem = article.find('.//ArticleTitle')
            title = title_elem.text if title_elem is not None and title_elem.text else ''

            journal_elem = article.find('.//Journal/Title')
            journal = journal_elem.text if journal_elem is not None and journal_elem.text else ''

            # Volume, issue, pages
            volume_elem = article.find('.//Volume')
            volume = volume_elem.text if volume_elem is not None and volume_elem.text else ''

            issue_elem = article.find('.//Issue')
            issue = issue_elem.text if issue_elem is not None and issue_elem.text else ''

            pagination_elem = article.find('.//MedlinePgn')
            pagination = pagination_elem.text if pagination_elem is not None and pagination_elem.text else ''

            # Year
            year_elem = article.find('.//PubDate/Year')
            year = None
            if year_elem is not None and year_elem.text:
                try:
                    year = int(year_elem.text)
                except ValueError:
                    pass

            # DOI and PMCID: the article's own, never a cited reference's (#1042)
            doi = own_article_id(article, ID_TYPE_DOI)
            pmcid = own_article_id(article, ID_TYPE_PMC)

            # Abstract
            abstract_text = ''
            abstract_elem = article.find('.//AbstractText')
            if abstract_elem is not None and abstract_elem.text:
                abstract_text = abstract_elem.text

            # Authors
            authors = []
            for author_elem in article.findall('.//Author'):
                last_name = author_elem.find('LastName')
                fore_name = author_elem.find('ForeName')
                if last_name is not None and last_name.text:
                    name = last_name.text
                    if fore_name is not None and fore_name.text:
                        name = f"{last_name.text} {fore_name.text}"
                    authors.append(name)

            # Publication Types (CRITICAL for classification!)
            publication_types = []
            for pub_type_elem in article.findall('.//PublicationType'):
                if pub_type_elem.text:
                    publication_types.append(pub_type_elem.text)

            # MeSH Terms
            mesh_terms = []
            for mesh_elem in article.findall('.//MeshHeading/DescriptorName'):
                if mesh_elem.text:
                    mesh_terms.append(mesh_elem.text)

            # Publication Status
            statuses = []
            pub_status_elem = article.find('.//PublicationStatus')
            if pub_status_elem is not None and pub_status_elem.text:
                statuses.append(pub_status_elem.text)

            # Check for "Epub ahead of print"
            for date_elem in article.findall('.//PubMedPubDate'):
                if date_elem.get('PubStatus') == 'aheadofprint':
                    statuses.append('Epub ahead of print')
                    break

            # Build complete record
            record = {
                'pmid': pmid,
                'title': title,
                'journal_title': journal,
                'volume': volume,
                'issue': issue,
                'pagination': pagination,
                'pub_year': year,
                'doi': doi,
                'pmcid': pmcid,
                'abstract_text': abstract_text,
                'authors': authors,
                'publication_type_list': publication_types,
                'mesh_terms': mesh_terms,
                'publication_statuses': statuses,
                'fetched_at': datetime.now().isoformat(),
                'fetch_source': 'efetch',
                'parser_version': self.parser_version,
                'raw_xml': ET.tostring(article, encoding='unicode')  # Store original XML
            }

            return pmid, record

        except Exception as e:
            # Log but don't fail entire batch
            if self.verbose:
                print(f"      ⚠️  Parse error for article: {e}")
            return None, None

    def _load_from_cache(self, pmids: list[str]) -> dict[str, dict]:
        """
        Load cached PubMed records from database.

        Args:
            pmids: List of PMIDs to load

        Returns:
            Dict mapping PMID → cached record
        """
        if not pmids or not self.db:
            return {}

        try:
            # Build query with placeholders
            placeholders = ','.join(['?' for _ in pmids])
            query = f"""
                SELECT pmid, record_json, raw_xml, fetched_at, parser_version
                FROM pubmed_cache
                WHERE pmid IN ({placeholders})
            """

            rows = self.db.execute(query, pmids).fetchall()

            results = {}
            for row in rows:
                pmid = row[0]  # pmid
                record_json = row[1]  # record_json
                raw_xml = row[2]  # raw_xml
                fetched_at = row[3]  # fetched_at
                parser_version = row[4]  # parser_version

                # Parse JSON record
                record = json.loads(record_json)
                record['raw_xml'] = raw_xml  # Restore raw XML
                record['fetched_at'] = fetched_at
                record['parser_version'] = parser_version
                record['from_cache'] = True

                results[pmid] = record

            return results

        except Exception as e:
            print(f"  ⚠️  Cache load error: {e}")
            return {}

    def _save_to_cache(self, records: dict[str, dict]):
        """
        Save fetched PubMed records to database cache.

        Args:
            records: Dict mapping PMID → record
        """
        if not records or not self.db:
            return

        try:
            for pmid, record in records.items():
                # Extract fields for structured columns
                title = record.get('title', '')
                journal_title = record.get('journal_title', '')
                pub_year = record.get('pub_year')
                doi = record.get('doi', '')
                volume = record.get('volume', '')
                issue = record.get('issue', '')
                pagination = record.get('pagination', '')
                publication_types = json.dumps(record.get('publication_type_list', []))

                # Prepare full record JSON (without raw_xml to avoid duplication)
                record_copy = record.copy()
                raw_xml = record_copy.pop('raw_xml', '')
                record_json = json.dumps(record_copy, ensure_ascii=False)

                # Insert or replace
                self.db.execute("""
                    INSERT OR REPLACE INTO pubmed_cache
                    (pmid, title, journal_title, pub_year, doi, volume, issue, pagination,
                     publication_types, record_json, raw_xml, fetch_source, parser_version, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, [
                    pmid, title, journal_title, pub_year, doi, volume, issue, pagination,
                    publication_types, record_json, raw_xml,
                    record.get('fetch_source', 'efetch'),
                    record.get('parser_version', self.parser_version)
                ])

            self.db.commit()

            if self.verbose:
                print(f"      💾 Cached {len(records)} records")

        except Exception as e:
            print(f"  ⚠️  Cache save error: {e}")
            # Don't fail the entire operation if cache write fails

    def _should_refresh_cache(self, cached_record: dict) -> bool:
        """
        Decide if cached PubMed record needs refreshing.

        Refresh rules:
        - Records < 30 days old: trust cache
        - Records 30-180 days old with uncertain status: refresh
        - Records > 180 days old without DOI: refresh
        - Records > 365 days old: always refresh

        Args:
            cached_record: Cached record dict

        Returns:
            True if should refresh, False if cache is still good
        """
        try:
            fetched_str = cached_record.get('fetched_at', '')
            if isinstance(fetched_str, str):
                fetched_at = datetime.fromisoformat(fetched_str.replace('Z', '+00:00'))
            else:
                # Assume it's already a datetime
                fetched_at = fetched_str

            age_days = (datetime.now() - fetched_at).days

            # Fresh records: trust cache
            if age_days < 30:
                return False

            # Medium-age records: refresh if uncertain status
            if age_days < 180:
                statuses = cached_record.get('publication_statuses', [])
                if 'Epub ahead of print' in statuses:
                    return True  # Might be published now
                if not cached_record.get('volume'):
                    return True  # Might have volume now

            # Older records without DOI: refresh
            if age_days < 365:
                if not cached_record.get('doi'):
                    return True  # DOI might have been assigned

            # Very old records: always refresh
            if age_days >= 365:
                return True

            return False

        except Exception:
            # If we can't determine age, refresh to be safe
            return True


def get_cache_stats(db_connection) -> dict:
    """
    Get PubMed cache statistics.

    Args:
        db_connection: Database connection

    Returns:
        Dict with cache statistics
    """
    try:
        result = db_connection.execute("""
            SELECT * FROM pubmed_cache_stats
        """).fetchone()

        if result:
            return {
                'total_records': result[0],
                'fresh_30d': result[1],
                'medium_30_180d': result[2],
                'stale_180d': result[3],
                'oldest_record': result[4],
                'newest_record': result[5],
                'cache_hit_rate_30d': (result[1] / result[0] * 100) if result[0] > 0 else 0
            }
        return {}
    except Exception as e:
        print(f"Error getting cache stats: {e}")
        return {}
