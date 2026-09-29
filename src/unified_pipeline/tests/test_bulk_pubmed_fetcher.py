"""Guards for core/bulk_pubmed_fetcher.py's efetch parser.

#1042: a ``.//ArticleId`` descent also sees every cited reference's
ArticleIdList under ``PubmedData/ReferenceList``, so an article with no PMCID
of its own picked up its first reference's. The parser must read only the
article's own ``PubmedData/ArticleIdList``.

Self-contained: parses a synthetic fixture, no network, no DB.
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.core.bulk_pubmed_fetcher import BulkPubMedFetcher  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'efetch_with_reference_list.xml'


def _parse_all():
    fetcher = BulkPubMedFetcher(api_key='unused', verbose=False)
    root = ET.parse(FIXTURE).getroot()
    return dict(fetcher._parse_pubmed_article(a) for a in root.findall('PubmedArticle'))


def test_reads_own_ids_when_present():
    record = _parse_all()['11111111']
    assert record['doi'] == '10.1000/own.1'
    assert record['pmcid'] == 'PMC1111111'


def test_article_without_own_ids_gets_empty_not_first_reference_ids():
    record = _parse_all()['22222222']
    assert record['doi'] == ''
    assert record['pmcid'] == ''
