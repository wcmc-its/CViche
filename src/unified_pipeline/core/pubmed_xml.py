"""Reading an efetch ``PubmedArticle``'s own identifiers (#1042).

A ``PubmedArticle`` carries its own IDs at ``PubmedData/ArticleIdList``, but
``PubmedData/ReferenceList`` nests one more ``ArticleIdList`` per cited
reference. A ``.//ArticleId`` descent therefore sees every reference's DOI and
PMCID too. Both efetch parsers read IDs through ``own_article_id`` so neither
can pick up a cited reference's value.
"""

import xml.etree.ElementTree as ET

# Relative to the PubmedArticle element. Anchored so ReferenceList is excluded.
OWN_ARTICLE_ID_PATH = './PubmedData/ArticleIdList/ArticleId'

# ArticleId IdType values the parsers read.
ID_TYPE_DOI = 'doi'
ID_TYPE_PMC = 'pmc'


def own_article_id(pubmed_article: ET.Element, id_type: str) -> str:
    """Return the article's own ``ArticleId`` of ``id_type`` (``ID_TYPE_DOI``, ``ID_TYPE_PMC``), or ''."""
    for id_elem in pubmed_article.findall(OWN_ARTICLE_ID_PATH):
        if id_elem.get('IdType') == id_type and id_elem.text:
            return id_elem.text.strip()
    return ''
