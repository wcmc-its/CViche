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
import json
import logging
import os
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime
from functools import partial
from http import HTTPStatus
from itertools import combinations
from pathlib import Path
from typing import Any, NamedTuple, TypeVar

import requests

from unified_pipeline.core.credentials import CREDENTIALS
from unified_pipeline.core.pubmed_xml import ID_TYPE_DOI, ID_TYPE_PMC, own_article_id
from unified_pipeline.log_redaction import redacted_error_label
from unified_pipeline.stage4.coercion import is_verbatim_author

logger = logging.getLogger(__name__)


# Configuration
NCBI_API_KEY = os.getenv('NCBI_API_KEY', os.getenv('PUBMED_API_KEY', ''))
PUBMED_CONTACT_EMAIL = os.getenv('PUBMED_CONTACT_EMAIL', os.getenv('NCBI_CONTACT_EMAIL', ''))
RATE_LIMIT_DELAY = 0.1 if NCBI_API_KEY else 0.34  # 10/s with key, 3/s without

# Retry policy for transient NCBI API failures: 429 / 5xx / connection
# errors, and a 200 whose body won't parse or carries an esearch ERROR.
# idconv (#1219): a 429 there outlasted the old 3-attempt, ~3 s window in 2 of
# 8 batch CVs and failed every PMCID in the CV. Re-probes showed it throttles
# intermittently (429, 200, 429, 200 at 15 s spacing). #1387 made that policy
# the default for every NCBI call (efetch and esearch kept the ~3 s window).
# 6 attempts back off 1+2+4+8+16 = 31 s; the total cap bounds that sum plus
# any Retry-After, so a server asking for longer than the cap fails fast
# instead of stalling stage 5. A call that succeeds takes no longer.
MAX_ATTEMPTS = 6
BACKOFF_BASE_SECONDS = 1.0  # 1s, 2s, ... doubling; Retry-After honored when larger
RETRY_TOTAL_WAIT_CAP_SECONDS = 60.0

# esearch can answer 200 with esearchresult.ERROR set when its backend fails;
# read as an empty idlist, that was recorded as 'doi_not_in_pubmed' (#1387).
ESEARCH_ERROR_KEY = 'ERROR'

# Output directory
OUTPUT_DIR = Path(__file__).parent / "outputs" / "stage_5_enrichment"

# NCBI API endpoints
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ELINK_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"
# The legacy www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/ host 301-redirects
# here (checked 2026-10-02 and 2026-10-07), so every attempt paid for the
# redirect (#1219).
ID_CONVERTER_URL = "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"

# NCBI's documented per-request ceiling for both idconv and efetch-by-id. A
# CV's PMCIDs went to idconv in one GET before #1219, so a CV citing more
# than 200 sent an over-limit request.
NCBI_MAX_IDS_PER_REQUEST = 200

# idconv throttles intermittently: CHXRBM's request re-sent 15 s apart
# answered 429, 200, 429, 200 (#1219). A PMCID whose lookup still failed
# after the retry budget is asked for once more at the end of stage 5, at
# least this long after the failure.
PMCID_RERUN_PAUSE_SECONDS = 15.0

# An efetch request that fails after its retries is split in half and each
# half fetched, down to this many ids (#1387): one PMID that breaks a batch,
# or a response NCBI cuts off, then costs a few entries instead of 200.
EFETCH_SPLIT_FLOOR = 25

# PMID- and DOI-path entries an NCBI error (not a no-match) left unenriched
# are looked up once more at the end of stage 5, after this pause (#1387).
SECOND_PASS_PAUSE_SECONDS = 30.0

# Minimum share of the shorter title's words that the PubMed record's title
# must share with the source title before the record replaces the citation
# (#1043). Over 105 local CVs, every wrong-paper substitution scored below 0.4
# (swapped adjacent PMIDs top out at 0.33); the lowest same-paper matches
# (retitled on publication) score 0.45-0.57.
MIN_TITLE_WORD_OVERLAP = 0.4

# PMCID-path outcomes (#1219), split so that an outage is not read as a
# missing paper. Not found: idconv answered, with no PMID for the PMCID (not
# in PMC, or not indexed in PubMed); expected vocabulary, like
# 'doi_not_in_pubmed'. Lookup failed: the idconv request, or the efetch of the
# PMID it gave, failed; a *_failed status the doctor's enrichment_failures
# lint counts, and the one the end-of-stage re-run retries.
PMCID_NOT_FOUND = 'pmcid_not_found'
PMCID_LOOKUP_FAILED = 'pmcid_lookup_failed'
TITLE_CHECK_FAILED = 'title_check_failed'

# Statuses the PMCID path leaves on an entry it could not enrich, which the
# bare-digits-as-PMID retry (#1219) may still resolve.
PMCID_PATH_FAILURES = (PMCID_NOT_FOUND, PMCID_LOOKUP_FAILED, TITLE_CHECK_FAILED)

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
# An in-press entry PubMed did not match, dated at least this many years before
# the run, is stale and gets a Word comment asking for the final citation. A
# partner reviewer (Houston Methodist) found CV
# entries listed as in press that never were; over the 199-CV stage-5 farm
# (2026-10-15) all 42 matches were "in press" / "accepted" / "Epub ahead of
# print" from 2023 or earlier with no final citation (#1871).
STALE_IN_PRESS_YEARS = 2

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
# PubMed often indexes an author's reply under the letter it answers, so the
# PMID a CV gives for its reply resolves to the letter, by someone else
# (VYNARH, dev-246: three "(Reply to Editor)" entries rendered the letter
# writers in place of the owner, #1438). The CV text marks a reply in these
# shapes; a bare "response" is not one ("immune response", "Dose-Response").
REPLY_CITATION_PATTERN = re.compile(
    r"\b(?:in\s+)?repl(?:y|ies)\s+to\b"
    r"|\bresponse\s+to\s+(?:the\s+|a\s+)?"
    r"(?:letters?|editors?|editorials?|comments?|commentary|correspondence)\b"
    r"|\bauthors?['\u2019]?s?\s+(?:reply|response)\b"
    r"|\(\s*(?:reply|response)\s*\)", re.I)
# A record whose own title says reply or response is the reply itself, which
# PubMed titles "Reply", "Response to ...", "The authors reply" and so on.
REPLY_RECORD_TITLE_PATTERN = re.compile(r'\b(?:repl(?:y|ies)|response|rebuttal)\b', re.I)
# The status of a reply whose record is the item it replies to. Not a
# *_failed status: the CV's own citation is the right one to render.
REPLY_TARGET_STATUS = 'reply_resolved_to_replied_item'
# Only an ID-less entry, or one whose DOI PubMed did not know, is searched by
# title: a CV identifier that resolved to another paper stays rejected.
_TITLE_SEARCHABLE_STATUSES = frozenset({'no_identifier', 'doi_not_in_pubmed'})
# The journal-article codes searched by title whether or not the CV calls the
# entry in press (#1598). Measured offline over 7 YUYVIG runs (2026-10-08):
# 43% of S1/S2/S6 entries without an identifier matched, and 96-99% of the
# hand-checked matches were the right paper. S4/S8/S9 matched 3-8%, and about
# half of those were wrong: an abstract, talk or chapter matched to the same
# group's later article of near-identical title, which every guard passes.
TITLE_SEARCH_CODES = PUBLISHED_ARTICLE_CODES
# The `enrichment_source` of a record found by title search, not by an
# identifier the CV gave.
TITLE_SEARCH_SOURCE = 'title_search'
# A PubMed title search misses papers PubMed indexes when the CV's title
# differs by a word or a symbol ("Ca2+" for "Calcium", a dropped subtitle).
# Crossref's bibliographic search tolerates that, so a miss asks Crossref for
# the paper's DOI and looks that DOI up in PubMed, under the same guards as a
# PubMed title hit (#1872). Replayed on 200 S1/S2/S6 entries the PubMed title
# search had missed (2026-10-15 stage-5 farm, 46 CVs), it accepted 133; 128
# hand-checked as the right paper, 4 wrong (two author replies, an abstract
# matched to its later paper, a position-statement summary) and 1 unsure.
CROSSREF_WORKS_URL = 'https://api.crossref.org/works'
CROSSREF_TITLE_SOURCE = 'crossref_title_search'
# A conference abstract, chapter or reference entry Crossref returns for an
# article's title is another item (FVQANK: a JCEM article matched a book
# chapter of the same name).
CROSSREF_ARTICLE_TYPE = 'journal-article'
# Crossref lookups stop once this many seconds of a run have gone to them, so
# a CV with a thousand unmatched articles cannot push stage 5 past the
# orchestrator's 1800 s stage timeout. A lookup took about 1.1 s in the
# 200-entry replay (2026-10-15), so the budget covers ~270 entries: every CV
# but the largest (p90 104 unmatched; BCTOGR ~1,500).
CROSSREF_BUDGET_SECONDS = 300.0
# An article Crossref knows but PubMed does not index is a real publication
# that stage 5 cannot enrich: a non-MEDLINE journal, most often. The entry
# keeps its status and gains this key, Crossref's record of it, so a check
# for unverified citations can tell it from one nobody can find (#1870). Its
# `source` says how it was matched: the CV's own DOI, or a title search.
# Stage 6 fills its issue and last page into 5d's citation when the
# citation's volume and first page are its own (#1872). On the 2026-10-15
# stage-5 farm: 164 of 192 `doi_not_in_pubmed` DOIs matched (40 of 40 sampled the right item), and 59
# of the 803 entries with no record, identifier, volume or pages matched by
# title (52 right, 4 wrong, 3 unsure: each wrong one names another venue).
# The title path's other 324 of those 803 found a PubMed record instead.
CROSSREF_MATCH_KEY = 'crossref_match'
CROSSREF_MATCH_BY_CV_DOI = 'cv_doi'
CROSSREF_MATCH_BY_TITLE = 'title_search'
# An article line with a year and a journal but no title is a stub
# ("Rodd et al. (2004a) Alcoholism: Clinical and Experimental Research JIF:
# 2.716 cited 58 times"): nothing can be looked up and the citation is
# unusable as printed. It carries this Word comment, which stage 6 attaches
# like `in_press_note` (#1870). On the 2026-10-15 stage-5 farm, 127 entries in
# 8 CVs (LFFKVV 117); a titleless line with no journal is a header or a split
# row ("1993", "~ Published"), not a stub, and is left alone.
CITATION_NOTE_KEY = 'citation_note'
INCOMPLETE_CITATION_NOTE = ("This citation has no article title, so CViche could not look it up in "
                            "PubMed or Crossref. Add the title, volume and pages.")
# Crossref's bibliographic search is built for whole reference strings: on
# a title alone it can rank another paper of similar wording above the right
# one (MNZ7IA's West J Emerg Med 2010 case report never reached the top 3).
# The query adds the CV's first authors and year (#1870).
CROSSREF_QUERY_AUTHORS = 3
# The sources that found a record by title, with no identifier from the CV.
TITLE_SEARCH_SOURCES = frozenset({TITLE_SEARCH_SOURCE, CROSSREF_TITLE_SOURCE})
# A doi.org link in an entry's text. The DOI ends at whitespace or a quote: a
# Word HYPERLINK field code puts the link in quotes before its display text.
DOI_URL_PATTERN = re.compile(r'https?://(?:dx\.)?doi\.org/(10\.\d{4,}/[^\s"]+)', re.I)


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


def _folded_tokens(text: str) -> list[str]:
    unquoted = re.sub(r"['\u2019]", '', text or '')
    folded = unicodedata.normalize('NFKD', unquoted).encode('ascii', 'ignore').decode()
    return re.findall(r'[A-Za-z0-9]+', folded)


def _folded_words(text: str, min_len: int) -> set[str]:
    return {w.lower() for w in _folded_tokens(text) if len(w) >= min_len}


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


_AUTHOR_SEPARATOR = re.compile(r'\s*(?:[,;&]|\band\b)\s*')
_ET_AL = re.compile(r'^et\.?\s+al\.?$', re.IGNORECASE)
# A CV author written "Surname J.A." or "J. A. Surname", for PubMed's "Surname JA",
# or "Karl J. Surname" in a CV that writes given names first.
_SURNAME_FIRST = re.compile(r"^(\S*[a-z]\S*(?: \S*[a-z]\S*)*)((?: [A-Z]{1,3})+)$")
_INITIALS_FIRST = re.compile(r"^((?:[A-Z]{1,3} )+)(\S*[a-z]\S*(?: \S*[a-z]\S*)*)$")
_NAME_FIRST = re.compile(r"^([A-Z])[a-z]+((?: [A-Z])*) (\S*[a-z]\S*)$")
_GENERATION = re.compile(r"\b(?:II|III|IV)\b")  # "Doe JA III": not initials
# Words that end a name rather than name an author, stops dropped and case
# kept ("MA" is a degree, "Ma" a surname): a suffix ("Jr"), a degree or
# credential ("PhD", "M.D."), an editor mark ("Eds").
_NOT_A_NAME_WORDS = frozenset({'Jr', 'Sr', 'II', 'III', 'IV', 'Ed', 'Eds', 'Editor', 'Editors'}
                              | {w for c in CREDENTIALS for w in c.replace('.', '').split()})


def _cv_author_names(cv_authors: str | list[str] | None) -> list[str]:
    """The CV's authors, one string each; "et al." is not one. A string list
    is split at commas, semicolons, "&" and "and"; a piece with no lowercase
    letter is a "Last, F." list's initials, not an author."""
    if isinstance(cv_authors, list):
        names = [str(a).strip() for a in cv_authors]
        return [a for a in names if a and not _ET_AL.match(a)]
    pieces = _AUTHOR_SEPARATOR.split(str(cv_authors or ''))
    return [p for p in pieces if re.search('[a-z]', p) and not _ET_AL.match(p)]


def _within_one_edit(a: str, b: str) -> bool:
    """Whether `a` becomes `b` by at most one inserted, deleted or changed letter."""
    i = next((k for k, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), min(len(a), len(b)))
    return b[i + 1:] in (a[i + 1:], a[i:]) or a[i + 1:] == b[i:]


def _initials(given: list[str]) -> str:
    """Given names as lowercase initials; a capital word of up to three
    letters ("JA") is initials already."""
    return ''.join(w if w.isupper() and len(w) <= 3 else w[0] for w in given).lower()


def _same_author(pubmed_name: str, cv_name: str, given_first: tuple[bool, ...] = (False, True)) -> bool:
    """Whether the CV's `cv_name` is PubMed's "Surname Initials" author: the
    CV's words split into PubMed's surname ("Vanderberg" for "van der Berg";
    one letter off, a CV misspelling, past three letters) and given names,
    after it or (`given_first`) before it, whose initials start PubMed's or
    PubMed's start them: "Vann L" is "Vann LH", "Vann LJ" is not."""
    surname, _, initials = pubmed_name.rpartition(' ')
    pubmed_surname, pubmed_initials = ''.join(_folded_tokens(surname)).lower(), initials.lower()
    words = _folded_tokens(cv_name)
    readings = [(words[k:], words[:k]) if first else (words[:k], words[k:])
                for k in range(1, len(words)) for first in given_first]
    return any((cv_surname == pubmed_surname
                or len(pubmed_surname) > 3 and _within_one_edit(cv_surname, pubmed_surname))
               and (cv_initials.startswith(pubmed_initials) or pubmed_initials.startswith(cv_initials))
               for cv_surname, cv_initials in ((''.join(s).lower(), _initials(g)) for s, g in readings))


def _pubmed_style(cv_name: str, given_first: bool) -> str:
    """`cv_name` as PubMed writes an author, "Surname JA", when it reads
    cleanly as a surname and initials, or as given names then a surname in
    a CV that writes them first; otherwise as the CV writes it, as is a
    group stage 4 keeps as written ("HIV Neuroimaging Consortium", AOJEQD e1050)."""
    name = ' '.join(cv_name.replace('.', ' ').split())
    if _GENERATION.search(name) or is_verbatim_author(name):
        return cv_name.strip()
    if match := _SURNAME_FIRST.match(name):
        return f"{match[1]} {match[2].replace(' ', '')}"
    if match := _INITIALS_FIRST.match(name):
        return f"{match[2]} {match[1].replace(' ', '')}"
    if given_first and (match := _NAME_FIRST.match(name)):
        return f"{match[3]} {match[1]}{match[2].replace(' ', '')}"
    return cv_name.strip()


def _pubmed_positions(pubmed_authors: list[str], cv_names: list[str]) -> list[int] | None:
    """Each PubMed author's place in the CV's list, in PubMed's order, or None
    when one of them is not there."""
    positions: list[int] = []
    for pubmed_name in pubmed_authors:
        start = positions[-1] + 1 if positions else 0
        place = next((k for k in range(start, len(cv_names)) if _same_author(pubmed_name, cv_names[k])), None)
        if place is None:
            return None
        positions.append(place)
    return positions


# A word no listed credential names but shaped like one: a capital after a
# lowercase letter ("MBChB", "MSc(Med)", "PhDr") unless it opens like a
# two-part surname ("McDonald", "deLuca", "'tHooft"), a word opening a
# parenthesis ("(Hons)"),
# or four or more capitals ("BCPS", "FRCPC").
_DEGREE_SHAPED_WORD = re.compile(r"\(.*|[A-Z]{4,}|(?!\S?[a-z]+[A-Z][a-z]{2,})\S*[a-z]\S*[A-Z]\S*")


def _is_not_a_name_word(word: str) -> bool:
    """Whether `word` ends a name rather than names an author. A hyphenated
    word is degree-shaped only when every part is ("Curran-Everett" and
    "O'Neil-Ray" are surnames, as is each part)."""
    return all(part in _NOT_A_NAME_WORDS or _DEGREE_SHAPED_WORD.fullmatch(part) for part in word.split('-'))


def _restorable(cv_name: str, cv_text: str | None) -> bool:
    """Whether a CV name PubMed lacks can render: it is no parenthesised note
    (GCFEBE e717's "(Equal 1st author contributorship)"), is not only a
    suffix, degree or editor mark cut from a name (RXYBVF e561's "Jr",
    M7NDD0 e27's "MD, PhD"; any one-word piece, "MBChB" too, since a CV
    author has a surname and initials or is a group of several words), and
    the CV's line writes it, words in order
    (not "Team H5P", stage 4's reading of "HVTN 505 Protocol Team")."""
    words = ' '.join(_folded_tokens(cv_name)).lower()
    pieces = cv_name.replace('.', '').replace(',', ' ').split()
    return (not cv_name.startswith('(')
            and len(pieces) > 1 and not all(map(_is_not_a_name_word, pieces))
            and f' {words} ' in f" {' '.join(_folded_tokens(cv_text)).lower()} ")


def cv_byline_over_shorter_pubmed_list(cv_authors: str | list[str] | None,
                                       pubmed_authors: list[str] | None, cv_text: str | None) -> str | None:
    """The CV's byline, with each of PubMed's authors in PubMed's form and
    the CV's other names after `_pubmed_style`, when PubMed's list is the
    CV's with names cut (#1661), else None. Every PubMed author must be one
    of the CV's, in PubMed's order, the CV must name someone more, and each
    of those more must be `_restorable` and none of PubMed's. The cut names
    are MEDLINE's cap (KRSFSI e468: pre-1996, ten authors, the owner twelfth), a group credit
    PubMed keeps as no author (AOJEQD: "...; <name> Study Team"), and
    co-authors PubMed's record lacks (UAURFU e790: 34 CV names, 21 PubMed).
    A "Last, F." string, whose pieces are not one author each, is None."""
    cv_names, pubmed_authors = _cv_author_names(cv_authors), pubmed_authors or []
    if isinstance(cv_authors, str) and len(_AUTHOR_SEPARATOR.split(cv_authors)) != len(cv_names):
        return None
    positions = _pubmed_positions(pubmed_authors, cv_names)
    if not positions:
        return None
    pubmed_at = dict(zip(positions, pubmed_authors, strict=True))
    more = [name for k, name in enumerate(cv_names) if k not in pubmed_at]
    if (not more or not all(_restorable(name, cv_text) for name in more)
            or any(_same_author(p, name) for p in pubmed_at.values() for name in more)):
        return None
    given_first = all(_same_author(p, cv_names[k], (True,)) for k, p in pubmed_at.items())
    return ', '.join(pubmed_at.get(k) or _pubmed_style(name, given_first) for k, name in enumerate(cv_names))


def cites_a_reply(text: str, title: str = '') -> bool:
    """Whether the CV's citation text calls the entry a reply. A marker inside
    the title is the title's, so the text must hold more matches than the
    title does (the counting `in_press_phrase` uses)."""
    return (len(REPLY_CITATION_PATTERN.findall(text or ''))
            > len(REPLY_CITATION_PATTERN.findall(title or '')))


def record_is_the_replied_item(entry: dict, pubmed_record: dict) -> bool:
    """Whether `pubmed_record` is the item a CV reply answers rather than the
    reply: the CV calls the entry a reply, the record's title does not, and
    the record names none of the CV's authors (#1438). A record naming a CV
    author may be the reply indexed with its letter, and stays."""
    fields = entry.get('extracted_fields') or {}
    title = fields.get('title') or fields.get('chapter_title') or ''
    record_titles = f"{pubmed_record.get('title') or ''} {pubmed_record.get('vernacular_title') or ''}"
    return (cites_a_reply(entry.get('text') or '', title)
            and not REPLY_RECORD_TITLE_PATTERN.search(record_titles)
            and not shares_an_author(fields.get('authors'), pubmed_record.get('authors') or []))


def plausible_publication_year(cv_year: str | int | None, pubmed_year: int | None) -> bool:
    """False only when both years are known and PubMed's falls outside
    IN_PRESS_YEAR_WINDOW of the CV's."""
    match = re.fullmatch(r'\s*((?:19|20)\d\d)\s*', str(cv_year))
    if not match or not pubmed_year:
        return True
    return pubmed_year - int(match.group(1)) in IN_PRESS_YEAR_WINDOW


def stale_in_press_note(phrase: str, cv_year: str | int | None, run_year: int) -> str | None:
    """The Word comment for an in-press entry PubMed did not match, when the
    CV's year is STALE_IN_PRESS_YEARS or more before `run_year`; None for a
    recent or undated one, whose paper may simply not be indexed yet. Worded
    as "could not match", never "not published": some of these quote a PMID
    the title check refused (WBZIDP, BMAMWE)."""
    match = re.fullmatch(r'\s*((?:19|20)\d\d)\s*', str(cv_year))
    if not match or run_year - int(match.group(1)) < STALE_IN_PRESS_YEARS:
        return None
    return (f"The CV lists this as {phrase} ({match.group(1)}), but CViche could not "
            f"match it to a PubMed record. If it was published, replace it with the "
            f"final citation (journal, volume, issue, pages); if not, remove it.")


def _same_title(a: str, b: str) -> bool:
    """Overlap against the longer title, so one title nested in the other
    does not count as the same paper."""
    words_a, words_b = _title_words(a), _title_words(b)
    if not words_a or not words_b:
        return False
    return len(words_a & words_b) / max(len(words_a), len(words_b)) >= MIN_TITLE_SEARCH_OVERLAP


def volume_number(volume: str | int | None) -> int | None:
    """The volume's leading number ("19 Suppl 1" -> 19), or None when it
    has none to compare."""
    match = re.match(r'\s*(\d+)', str(volume or ''))
    return int(match.group(1)) if match else None


# An ID-path record whose year and volume both differ from the CV's names
# another paper unless the titles also agree against the LONGER one at the
# title-search floor (#1696). UAURFU e694 (dev-264): a CV's 16-word 2021
# vol. 20 title shared 6 words with an 8-word 2020 vol. 19 record by the same
# group, which scored 0.75 against the shorter title and replaced the paper.
# Replayed over 3,445 ID-path acceptances in 122 stage-5 artifacts, it is
# the only one refused: of the 29 whose year and volume both differ, 17 were
# already under MIN_TITLE_WORD_OVERLAP and 11 are the same title (the CV gave
# the issue as the volume). 415 differ in the year alone, mostly online versus
# print, so a year alone does not refuse. The title search has its own floor
# and year window and is not checked this way.
def citation_contradicts_record(fields: dict, pubmed_record: dict) -> bool:
    """Whether the CV's year and volume are both known and both differ from
    the record's: two facts of the citation that say it is another paper. An
    unknown on either side contradicts nothing."""
    cv_year = cv_publication_year(fields.get('year'))
    cv_volume = volume_number(fields.get('volume'))
    record_volume = volume_number(pubmed_record.get('volume'))
    return (cv_year is not None and bool(pubmed_record.get('year'))
            and cv_year != pubmed_record.get('year')
            and cv_volume is not None and record_volume is not None
            and cv_volume != record_volume)


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


class NCBIResponseError(ValueError):
    """A 200 response whose body reports a server-side failure. A ValueError,
    like a body that won't parse, so _get_with_retry retries both."""


_Parsed = TypeVar('_Parsed')


class _Attempt(NamedTuple):
    """One GET's outcome: the parsed body, or the transient error to retry
    on (with the server's Retry-After, when it sent one)."""
    value: Any = None
    error: Exception | None = None
    retry_after: str | None = None


def _parse_xml(response: requests.Response) -> ET.Element:
    return ET.fromstring(response.content)


def _parse_json(response: requests.Response) -> dict[str, Any]:
    return response.json()


def _parse_esearch_idlist(response: requests.Response) -> list[str]:
    """The esearch idlist; an ERROR in the result raises NCBIResponseError so
    it is retried, and never read as a no-match."""
    result = response.json().get('esearchresult', {})
    if ESEARCH_ERROR_KEY in result:
        raise NCBIResponseError(f"esearch error: {result[ESEARCH_ERROR_KEY]}")
    return result.get('idlist', [])


def _parse_crossref_items(response: requests.Response) -> list[dict[str, Any]]:
    return (response.json().get('message') or {}).get('items') or []


def _parse_crossref_work(response: requests.Response) -> dict[str, Any]:
    return response.json().get('message') or {}


def crossref_query(title: str, fields: dict) -> str:
    """The bibliographic query for an entry: its title, then its first
    CROSSREF_QUERY_AUTHORS authors as the CV writes them, then its year."""
    authors = _cv_author_names(fields.get('authors'))[:CROSSREF_QUERY_AUTHORS]
    year = cv_publication_year(fields.get('year'))
    return ' '.join([title, *authors, *([str(year)] if year else [])])


def crossref_record(item: dict[str, Any]) -> dict[str, Any]:
    """A Crossref work in the shape the title guards read from a PubMed
    record: authors as "Surname Initials", the year it was issued, and the
    title with its subtitle joined on (Crossref keeps them apart)."""
    authors = []
    for author in item.get('author') or []:
        surname = author.get('family') or author.get('name') or ''
        initials = ''.join(w[0] for w in (author.get('given') or '').replace('-', ' ').split())
        if surname:
            authors.append(f'{surname} {initials}'.strip())
    titles = item.get('title') or []
    issued = ((item.get('issued') or {}).get('date-parts') or [[None]])[0]
    return {
        'doi': item.get('DOI'),
        'type': item.get('type'),
        'title': ' '.join(titles + (item.get('subtitle') or [])),
        'vernacular_title': titles[0] if titles else None,
        'journal': next(iter(item.get('container-title') or []), None),
        'year': issued[0] if issued else None,
        'volume': item.get('volume'),
        'issue': item.get('issue'),
        'pages': item.get('page'),
        'authors': authors,
    }


class _PmcidConversion(NamedTuple):
    """idconv's answer for a set of PMCIDs: the PMID of each PMCID it mapped,
    and the PMCIDs whose request failed (so their absence from `pmids` is an
    outage, not a not-found)."""
    pmids: dict[str, str]
    failed: frozenset[str]


def _chunks(ids: list[str], size: int) -> list[list[str]]:
    return [ids[i:i + size] for i in range(0, len(ids), size)]


class _Acceptance(NamedTuple):
    """One accepted record, kept until every lookup path has run so that an
    entry sharing its PMID with a better-matching entry can be restored to
    `before`, its state before the record was merged in."""
    entry: dict
    before: dict
    pmid: str
    pubmed_title: str
    pubmed_year: int | None
    source: str
    overlap: float | None


def cv_publication_year(cv_year: str | int | None) -> int | None:
    """The first four-digit year the CV gives, or None."""
    match = re.search(r'(?:19|20)\d\d', str(cv_year or ''))
    return int(match.group()) if match else None


def cv_year_is(cv_year: str | int | None, pubmed_year: int | None) -> bool:
    """Whether the CV gives exactly PubMed's year for the paper."""
    return bool(pubmed_year) and cv_publication_year(cv_year) == pubmed_year


def cv_title(fields: dict) -> str:
    """The title an entry is matched to PubMed by."""
    return fields.get('title') or fields.get('chapter_title') or ''


def _cv_fields(accepted: _Acceptance) -> dict:
    """An accepted record's entry as the CV gave it, before the merge."""
    return accepted.before.get('extracted_fields') or {}


def _match_strength(accepted: _Acceptance) -> tuple[float, bool, bool, bool]:
    """How strongly an accepted record names its entry's paper, compared
    between entries holding the same PMID: title overlap first; then a
    published listing over one the CV calls in press (the in-press step marks
    that one superseded); then the CV giving PubMed's own year; then an
    identifier the CV gave over a title search. The year decides between two
    exact-title holders: in the #1598 measurement two such holders were
    hand-checked as a different paper (BLBVPD e848, BCTOGR e2519), and each
    was the one whose CV year was not PubMed's."""
    fields = _cv_fields(accepted)
    return (accepted.overlap or 0.0,
            not listed_in_press(accepted.before),
            cv_year_is(fields.get('year'), accepted.pubmed_year),
            accepted.source not in TITLE_SEARCH_SOURCES)


class PubMedEnricher:
    """
    Enriches CV publication entries with PubMed data.

    Supports lookup by:
    - PMID (direct)
    - PMCID (via ID converter)
    - DOI (via esearch)
    """

    # Circuit breaker: until NCBI answers one request it is down, and the
    # efetch split and the second pass would only add retry waits that can
    # push the stage past the orchestrator's stage timeout (#1387). Set on the
    # instance by the first answered request.
    _ncbi_answered = False

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
            'reply_targets_rejected': 0,
            'api_errors': 0,
            'title_searches': 0,
            'crossref_searches': 0,
            'crossref_skipped': 0,
            'crossref_doi_lookups': 0,
            'crossref_matches': 0,
            'incomplete_citations': 0,
            'in_press_resolved': 0,
            'in_press_duplicates': 0,
            'in_press_promoted': 0,
            'in_press_stale': 0,
            'second_pass_recovered': 0,
            'second_pass_failed': 0,
        }

        # (operation, status/exception) classes already logged at ERROR this run
        self._logged_failure_classes = set()

        # This document's accepted records, for _release_weaker_shared_pmids
        self._acceptances: list[_Acceptance] = []

        # PMIDs whose efetch failed, and DOI-path entries an API error left
        # unenriched: the second pass's work (_retry_api_failures).
        self._efetch_failed: set[str] = set()
        self._doi_api_failed: list[tuple[dict, str]] = []

        # Seconds this run has spent on Crossref lookups (CROSSREF_BUDGET_SECONDS).
        self._crossref_seconds = 0.0

    def enrich_stage4_output(self, stage4_path: str) -> dict[str, Any]:
        """
        Main entry point: Enrich a Stage 4 output file.

        Args:
            stage4_path: Path to Stage 4 JSON output

        Returns:
            Enriched output dict
        """
        # Load Stage 4 output
        with open(stage4_path, 'r', encoding='utf-8') as f:
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

        # A `doi:` token copied from the neighbouring citation yields to the
        # entry's own doi.org link before any DOI is looked up.
        self._prefer_url_dois(pub_entries)

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
            logger.info(f"  - {len(no_id)} without identifiers (articles searched by title)")

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
        pmid_api_failed = [(entry, pmid) for entry, pmid in by_pmid if pmid in self._efetch_failed]

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

        # 4a. PMCIDs whose lookup failed get one more try, now that the DOI
        # path has given idconv's throttle time to clear. Before 4b, so a
        # record the re-run accepts is checked for a shared PMID too.
        self._rerun_failed_pmcid_lookups(by_pmcid)

        # 4a'. The same for PMID and DOI entries an NCBI error left
        # unenriched. Before 4b, so a DOI the re-run finds absent from PubMed
        # is title-searched.
        self._retry_api_failures(pmid_api_failed, progress_total)

        # 4b. Articles still without a record: guarded title search (#1598).
        # Before 4c, so a title match that shares a PMID is weighed there too.
        self._enrich_articles_by_title(pub_entries)

        # 4b'. A DOI PubMed does not index, asked of Crossref (#1872).
        self._match_doi_misses_in_crossref(pub_entries)

        # 4c. One PMID accepted for two entries: only the paper it names keeps it.
        # Before step 5, whose duplicate check reads the PMIDs entries hold.
        self._release_weaker_shared_pmids()

        # 5. "In press" entries: title search, then record what PubMed shows
        self._resolve_in_press(pub_entries)

        # 6. Article stubs with no title to look up (#1870).
        self._note_incomplete_citations(pub_entries)

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

    def _prefer_url_dois(self, pub_entries: list[dict]) -> None:
        """An entry whose `doi` is the DOI of the entry before or after it,
        while its text links one other DOI through doi.org, takes the linked
        DOI (#1219, RVTAQT 93): the CV's `doi:` token was copied from the
        neighbouring citation and the link is the paper's own. Stage 4 sees
        one entry at a time, so this is the first point where the neighbour's
        DOI is visible, and it runs before the DOI is looked up. The replaced
        value is kept as `doi_replaced`."""
        dois = [(self._clean_doi((e.get('extracted_fields') or {}).get('doi')) or '').lower()
                for e in pub_entries]
        for i, entry in enumerate(pub_entries):
            neighbours = {dois[j] for j in (i - 1, i + 1) if 0 <= j < len(dois)}
            if not dois[i] or dois[i] not in neighbours:
                continue
            linked = {self._clean_doi(m).lower(): self._clean_doi(m)
                      for m in DOI_URL_PATTERN.findall(entry.get('text') or '')}
            if len(linked) != 1 or dois[i] in linked:
                continue
            fields = entry['extracted_fields']
            entry['doi_replaced'] = fields['doi']
            [fields['doi']] = linked.values()

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
        all_records = self._fetch_pubmed_records(pmids)
        self.stats['pmid_lookups'] += len(pmids)

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
        conversion = self._convert_pmcids_to_pmids(pmcids)
        self.stats['pmcid_conversions'] += len(pmcids)
        records = self._fetch_pubmed_records(list(dict.fromkeys(conversion.pmids.values())))

        # Merge results
        for entry, pmcid in entries_with_pmcid:
            pmid = conversion.pmids.get(pmcid)
            if pmid and pmid in records:
                if self._accept_record(entry, records[pmid], 'pmcid_conversion'):
                    # Also store the PMID we discovered
                    if 'extracted_fields' in entry:
                        entry['extracted_fields']['pmid'] = pmid
            else:
                entry['enrichment_status'] = self._pmcid_failure_status(pmcid, conversion)
                self.stats['failed_lookups'] += 1
            results.append(entry)

        self._retry_bare_pmcids_as_pmids(results)
        return results

    @staticmethod
    def _pmcid_failure_status(pmcid: str, conversion: _PmcidConversion) -> str:
        """Not found only when idconv answered for this PMCID and gave no
        PMID. A failed idconv request, or a PMID whose record efetch did not
        return, is a lookup failure."""
        if pmcid in conversion.failed or pmcid in conversion.pmids:
            return PMCID_LOOKUP_FAILED
        return PMCID_NOT_FOUND

    def _rerun_failed_pmcid_lookups(self, entries_with_pmcid: list[tuple[dict, str]]) -> None:
        """Run the PMCID path once more for the entries whose lookup failed
        (#1219: one idconv 429 that outlasted the retries cost CHXRBM all 40
        of its PMCID citations). The failure counts the first pass added
        come off before the re-run adds its own."""
        rerun = [(entry, pmcid) for entry, pmcid in entries_with_pmcid
                 if entry.get('enrichment_status') == PMCID_LOOKUP_FAILED]
        if not rerun:
            return
        if self.verbose:
            logger.info(f"\n🔄 Re-running {len(rerun)} failed PMCID lookups "
                        f"in {PMCID_RERUN_PAUSE_SECONDS:g}s...")
        time.sleep(PMCID_RERUN_PAUSE_SECONDS)
        for _ in rerun:
            self._shift_failure_counts(PMCID_LOOKUP_FAILED, -1)
        self._enrich_by_pmcid(rerun)

    def _retry_api_failures(self, pmid_failed: list[tuple[dict, str]], progress_total: int) -> None:
        """Look up once more, after a pause, the PMID- and DOI-path entries an
        NCBI error left unenriched (#1387). A PMID NCBI answered without a
        record is not retried. The re-run's outcome replaces the first
        pass's, whose failure counts come off first."""
        doi_failed, self._doi_api_failed = self._doi_api_failed, []
        retry = pmid_failed + doi_failed
        if not retry or not self._ncbi_answered:
            return
        if self.verbose:
            logger.info(f"\n🔄 Re-running {len(retry)} lookups an NCBI error failed "
                        f"in {SECOND_PASS_PAUSE_SECONDS:g}s...")
        time.sleep(SECOND_PASS_PAUSE_SECONDS)
        self.stats['failed_lookups'] -= len(retry)
        self._enrich_by_pmid(pmid_failed)
        self._enrich_by_doi(doi_failed, progress_total - len(doi_failed), progress_total)
        recovered = sum(entry['enrichment_status'] == 'enriched' for entry, _ in retry)
        self.stats['second_pass_recovered'] = recovered
        self.stats['second_pass_failed'] = len(retry) - recovered

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
        return (1 if failed else 0, 1 if status == TITLE_CHECK_FAILED else 0)

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
        API failure (non-2xx after retries, connection error, a body that
        will not parse or carries an esearch ERROR) is recorded as
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
                    logger.info("    ⚠️ DOI search error for %s: %s", doi, redacted_error_label(e))
                    logger.debug("DOI search error detail for %s: %s", doi, _sanitize_error(e))

            if pmid:
                records = self._fetch_pubmed_batch([pmid])
                if records is None:
                    self._doi_api_failed.append((entry, doi))
                if pmid in (records or {}):
                    if self._accept_record(entry, records[pmid], 'doi_search'):
                        # Store discovered PMID
                        if 'extracted_fields' in entry:
                            entry['extracted_fields']['pmid'] = pmid
                else:
                    entry['enrichment_status'] = 'doi_found_but_fetch_failed'
                    self.stats['failed_lookups'] += 1
            elif search_failed:
                self._doi_api_failed.append((entry, doi))
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
                note = stale_in_press_note(phrase, fields.get('year'), datetime.now().year)
                if note:
                    entry['in_press_note'] = note
                    self.stats['in_press_stale'] += 1
                continue
            if self._lists_as_published(entry, pub_entries):
                self._record_in_press_duplicate(entry, phrase)
            else:
                self._record_in_press_resolution(entry, phrase)

    def _enrich_articles_by_title(self, pub_entries: list[dict]) -> None:
        """Search PubMed by title for every TITLE_SEARCH_CODES entry with no
        usable identifier (#1598), under `_enrich_by_title`'s guards. An entry
        the CV lists as in press is left to `_resolve_in_press`, which
        searches it as before."""
        candidates = [entry for entry in pub_entries
                      if entry.get('taxonomy_code') in TITLE_SEARCH_CODES
                      and entry.get('enrichment_status') in _TITLE_SEARCHABLE_STATUSES
                      and not listed_in_press(entry)]
        if self.verbose and candidates:
            logger.info(f"\n🔍 Searching PubMed by title for {len(candidates)} articles...")
        for searched, entry in enumerate(candidates, 1):
            fields = entry.get('extracted_fields') or {}
            self._enrich_by_title(entry, cv_title(fields))
            if self.verbose:
                logger.info("[%d/%d] publications searched by title", searched, len(candidates))

    def _enrich_by_title(self, entry: dict, title: str) -> None:
        """Accept the best of the top title-search hits that passes every
        guard (`_title_hit_passes`). Hits rank by title overlap, then a
        non-Comment over a Comment, then PubMed's order. A miss leaves the
        entry as it was: a miss is not evidence the paper is unpublished
        (non-indexed journals, chapters, retitling)."""
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
        records = self._fetch_pubmed_batch(pmids) or {}
        # A preprint is the paper's earlier version, never what "in press"
        # names (QZWBKQ, dev-239: an "Appl Environ Microbiol, in press" entry
        # matched its bioRxiv record, PubMed type Preprint); an erratum is a
        # notice about the paper (QNZADH, dev-242).
        scored = [(title_word_overlap(title, [r['title'], r['vernacular_title']]) or 0,
                   COMMENT_PUBTYPE not in r['publication_types'], r)
                  for r in records.values()
                  if not TITLE_SEARCH_EXCLUDED_PUBTYPES.intersection(r['publication_types'])]
        # Each hit is guarded on its own (#1619): two hits can tie on overlap,
        # and the first of them failing a guard said nothing about the other.
        # A stable descending sort keeps PubMed's order among equal ranks, so
        # the first hit tried is the one `max()` picked before.
        for overlap, _, record in sorted(scored, key=lambda hit: hit[:2], reverse=True):
            if self._title_hit_passes(entry, overlap, record):
                self._accept_record(entry, record, TITLE_SEARCH_SOURCE)
                return
        self._enrich_by_crossref_doi(entry, title)

    def _enrich_by_crossref_doi(self, entry: dict, title: str) -> None:
        """Ask Crossref for the DOI of the journal article `title` names and
        accept the PubMed record of that DOI under the title search's guards
        (#1872). The PubMed record, not Crossref's, decides: Crossref only
        finds the DOI. When the DOI is not in PubMed, Crossref's record is kept
        under CROSSREF_MATCH_KEY instead, under the same guards. A Crossref
        failure, or a spent budget, leaves the entry as it was."""
        self._within_crossref_budget(partial(self._look_up_crossref_doi, entry, title))

    def _match_doi_misses_in_crossref(self, pub_entries: list[dict]) -> None:
        """Ask Crossref for the work of every article DOI PubMed did not know
        (`doi_not_in_pubmed`) and the title search did not resolve, and keep
        its record under CROSSREF_MATCH_KEY (#1872)."""
        for entry in pub_entries:
            if (entry.get('taxonomy_code') in TITLE_SEARCH_CODES
                    and entry.get('enrichment_status') == 'doi_not_in_pubmed'
                    and CROSSREF_MATCH_KEY not in entry):
                self._within_crossref_budget(partial(self._look_up_crossref_work, entry))

    def _within_crossref_budget(self, lookup: Callable[[], None]) -> None:
        """Run one Crossref lookup unless this run has spent
        CROSSREF_BUDGET_SECONDS on them; its time counts against the budget."""
        if self._crossref_seconds >= CROSSREF_BUDGET_SECONDS:
            self.stats['crossref_skipped'] += 1
            return
        started = time.monotonic()
        try:
            lookup()
        finally:
            self._crossref_seconds += time.monotonic() - started

    def _look_up_crossref_work(self, entry: dict) -> None:
        """Keep Crossref's record of the entry's own DOI when its title is the
        CV's under the identifier paths' guard: MIN_TITLE_WORD_OVERLAP, and no
        year-and-volume contradiction (#1696). A DOI Crossref does not know
        (404) is not an error."""
        fields = entry.get('extracted_fields') or {}
        doi = self._clean_doi(fields.get('doi'))
        self.stats['crossref_doi_lookups'] += 1
        try:
            item = self._get_with_retry(f'{CROSSREF_WORKS_URL}/{doi}', {}, _parse_crossref_work)
        except requests.HTTPError as e:
            if e.response is None or e.response.status_code != HTTPStatus.NOT_FOUND:
                self.stats['api_errors'] += 1
                self._log_api_failure('crossref_work', e)
            return
        except (requests.RequestException, ValueError) as e:
            self.stats['api_errors'] += 1
            self._log_api_failure('crossref_work', e)
            return
        finally:
            time.sleep(RATE_LIMIT_DELAY)
        record = crossref_record(item)
        overlap = title_word_overlap(cv_title(fields), [record['title'], record['vernacular_title'] or ''])
        if not record['doi'] or overlap is not None and (overlap < MIN_TITLE_WORD_OVERLAP
                                    or self._contradicted_on_an_id_path(fields, record, CROSSREF_MATCH_BY_CV_DOI)):
            return
        self._keep_crossref_match(entry, record, CROSSREF_MATCH_BY_CV_DOI)

    def _keep_crossref_match(self, entry: dict, record: dict, source: str) -> None:
        entry[CROSSREF_MATCH_KEY] = {'source': source,
                                     **{k: v for k, v in record.items() if k != 'vernacular_title'}}
        self.stats['crossref_matches'] += 1

    def _look_up_crossref_doi(self, entry: dict, title: str) -> None:
        self.stats['crossref_searches'] += 1
        params = {'query.bibliographic': crossref_query(title, entry.get('extracted_fields') or {}),
                  'rows': TITLE_SEARCH_MAX_HITS,
                  'select': 'DOI,type,title,subtitle,author,issued,container-title,volume,issue,page'}
        try:
            items = self._get_with_retry(CROSSREF_WORKS_URL, params, _parse_crossref_items)
        except (requests.RequestException, ValueError) as e:
            self.stats['api_errors'] += 1
            self._log_api_failure('crossref_search', e)
            return
        finally:
            time.sleep(RATE_LIMIT_DELAY)
        unindexed = None  # the first guarded hit PubMed does not index
        for item in items:
            crossref = crossref_record(item)
            crossref_overlap = title_word_overlap(title, [crossref['title'], crossref['vernacular_title'] or '']) or 0
            if (item.get('type') != CROSSREF_ARTICLE_TYPE or not item.get('DOI')
                    or crossref_overlap < MIN_TITLE_SEARCH_OVERLAP):
                continue
            try:
                pmid = self._search_pmid_by_doi(item['DOI'])
            except (requests.RequestException, ValueError) as e:
                self.stats['api_errors'] += 1
                self._log_api_failure('doi_search', e)
                return
            finally:
                time.sleep(RATE_LIMIT_DELAY)
            if not pmid:
                if unindexed is None and self._title_hit_passes(entry, crossref_overlap, crossref):
                    unindexed = crossref
                continue
            record = (self._fetch_pubmed_batch([pmid]) or {}).get(pmid)
            if record is None or TITLE_SEARCH_EXCLUDED_PUBTYPES.intersection(record['publication_types']):
                continue
            overlap = title_word_overlap(title, [record['title'], record['vernacular_title']]) or 0
            if self._title_hit_passes(entry, overlap, record):
                self._accept_record(entry, record, CROSSREF_TITLE_SOURCE)
                return
        if unindexed is not None:
            self._keep_crossref_match(entry, unindexed, CROSSREF_MATCH_BY_TITLE)

    @staticmethod
    def _title_hit_passes(entry: dict, overlap: float, record: dict) -> bool:
        """Whether a title-search hit may be accepted: its title overlap
        clears MIN_TITLE_SEARCH_OVERLAP, it shares an author with the CV, and its
        year is plausible for the CV's. `_accept_record`'s reply check
        (#1438) cannot refuse such a hit: a record sharing a CV author is
        never taken for the item a reply answers."""
        fields = entry.get('extracted_fields') or {}
        return (overlap >= MIN_TITLE_SEARCH_OVERLAP
                and shares_an_author(fields.get('authors'), record['authors'])
                and plausible_publication_year(fields.get('year'), record['year']))

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
        return self._get_with_retry(ESEARCH_URL, params, _parse_esearch_idlist)

    def _note_incomplete_citations(self, pub_entries: list[dict]) -> None:
        """Give INCOMPLETE_CITATION_NOTE to every unenriched article with a
        year and a journal but no title, unless Crossref matched it or it
        already carries an in-press note."""
        for entry in pub_entries:
            fields = entry.get('extracted_fields') or {}
            if (entry.get('taxonomy_code') in PUBLISHED_ARTICLE_CODES
                    and entry.get('enrichment_status') != 'enriched'
                    and not cv_title(fields) and fields.get('year') and fields.get('journal')
                    and CROSSREF_MATCH_KEY not in entry and not entry.get('in_press_note')):
                entry[CITATION_NOTE_KEY] = INCOMPLETE_CITATION_NOTE
                self.stats['incomplete_citations'] += 1

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
        parse: Callable[[requests.Response], _Parsed],
    ) -> _Parsed:
        """
        HTTP GET, parsed by `parse`, with retry on transient failures.

        Retries 429s, 5xx responses, connection/timeout errors, and a 200
        whose body `parse` rejects (ET.ParseError / ValueError, which covers
        a truncated body and NCBIResponseError) up to MAX_ATTEMPTS total
        attempts with exponential backoff (1s, 2s, ...), honoring a
        Retry-After header when larger. A retry whose delay would push the
        summed waits past RETRY_TOTAL_WAIT_CAP_SECONDS is not taken and the
        last error raises. Other HTTP errors (e.g. 404) raise immediately.
        """
        last_error: Exception | None = None
        waited = 0.0
        for attempt in range(MAX_ATTEMPTS):
            outcome = self._attempt_get(url, params, parse)
            if outcome.error is None:
                # A Crossref reply says nothing about whether NCBI is up.
                if not url.startswith(CROSSREF_WORKS_URL):
                    self._ncbi_answered = True
                return outcome.value
            last_error = outcome.error
            if attempt == MAX_ATTEMPTS - 1:
                break
            delay = self._retry_delay(attempt, outcome.retry_after)
            if waited + delay > RETRY_TOTAL_WAIT_CAP_SECONDS:
                break
            waited += delay
            if self.verbose:
                logger.warning(f"    ⏳ Transient API error ({_sanitize_error(last_error)}); "
                               f"retry {attempt + 1}/{MAX_ATTEMPTS - 1} in {delay:g}s")
            time.sleep(delay)

        raise last_error

    def _attempt_get(
        self,
        url: str,
        params: dict[str, Any],
        parse: Callable[[requests.Response], _Parsed],
    ) -> _Attempt:
        """One GET. A transient failure comes back as `error`; a
        non-transient HTTP error (e.g. 404) raises."""
        try:
            response = self.session.get(url, params=params, timeout=30)
        except (requests.ConnectionError, requests.Timeout) as e:
            return _Attempt(error=e)
        if response.status_code == 429 or response.status_code >= 500:
            return _Attempt(
                error=requests.HTTPError(
                    f"{response.status_code} transient error for url: {response.url}",
                    response=response,
                ),
                retry_after=response.headers.get('Retry-After'),
            )
        response.raise_for_status()  # non-transient 4xx raises here
        try:
            return _Attempt(value=parse(response))
        except (ET.ParseError, ValueError) as e:  # truncated / error body on a 200
            return _Attempt(error=e)

    def _fetch_pubmed_records(self, pmids: list[str]) -> dict[str, dict]:
        """efetch in requests of at most NCBI_MAX_IDS_PER_REQUEST ids."""
        records: dict[str, dict] = {}
        for i, chunk in enumerate(_chunks(pmids, NCBI_MAX_IDS_PER_REQUEST)):
            if i:
                time.sleep(RATE_LIMIT_DELAY)
            records.update(self._fetch_or_split(chunk))
        return records

    def _fetch_or_split(self, pmids: list[str]) -> dict[str, dict]:
        """efetch `pmids`; a failed request is split in half and each half
        fetched, down to EFETCH_SPLIT_FLOOR ids. PMIDs no request fetched go
        into _efetch_failed."""
        records = self._fetch_pubmed_batch(pmids)
        if records is not None:
            return records
        if len(pmids) <= EFETCH_SPLIT_FLOOR or not self._ncbi_answered:
            self._efetch_failed.update(pmids)
            return {}
        half = len(pmids) // 2
        return {**self._fetch_or_split(pmids[:half]), **self._fetch_or_split(pmids[half:])}

    def _fetch_pubmed_batch(self, pmids: list[str]) -> dict[str, dict] | None:
        """
        Fetch multiple PubMed records via efetch. None when the request
        failed, so an API error is told apart from records NCBI lacks.
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

            root = self._get_with_retry(EFETCH_URL, params, _parse_xml)

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
                logger.info("    ❌ API error: %s", redacted_error_label(e))
                logger.debug("API error detail: %s", _sanitize_error(e))
            return None

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
                logger.warning("    ⚠️ Parse error: %s", redacted_error_label(e))
                logger.debug("    ⚠️ Parse error: %s", e)
            return None, None

    def _get_text(self, elem: ET.Element, path: str) -> str:
        """Safely get text from XML element."""
        found = elem.find(path)
        return found.text.strip() if found is not None and found.text else ''

    def _convert_pmcids_to_pmids(self, pmcids: list[str]) -> _PmcidConversion:
        """
        Convert PMCIDs to PMIDs using the NCBI ID converter, in requests of
        at most NCBI_MAX_IDS_PER_REQUEST ids. A request that fails marks
        only its own PMCIDs failed.
        """
        unique = list(dict.fromkeys(pmcids))
        pmids: dict[str, str] = {}
        failed: set[str] = set()
        for i, chunk in enumerate(_chunks(unique, NCBI_MAX_IDS_PER_REQUEST)):
            if i:
                time.sleep(RATE_LIMIT_DELAY)
            try:
                pmids.update(self._convert_pmcid_chunk(chunk))
            except Exception as e:
                failed.update(chunk)
                self.stats['api_errors'] += 1
                self._log_api_failure('pmcid_conversion', e)
                if self.verbose:
                    # Info, not error: see the note at _enrich_by_doi's DOI search error above.
                    logger.info("    ❌ ID conversion error: %s", redacted_error_label(e))
                    logger.debug("ID conversion error detail: %s", _sanitize_error(e))
        if self.verbose and unique:
            logger.info(f"    → Converted {len(pmids)}/{len(unique)} PMCIDs to PMIDs")
        return _PmcidConversion(pmids, frozenset(failed))

    def _convert_pmcid_chunk(self, pmcids: list[str]) -> dict[str, str]:
        """One idconv request: PMCID -> PMID for each record that has both.
        idconv answers the PMID as a JSON number, so it is stringified."""
        params = {
            'ids': ','.join(pmcids),
            'format': 'json',
            **self._identity_params()
        }
        data = self._get_with_retry(ID_CONVERTER_URL, params, _parse_json)
        return {record['pmcid']: str(record['pmid'])
                for record in data.get('records', [])
                if record.get('pmcid') and record.get('pmid')}

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

        id_list = self._get_with_retry(ESEARCH_URL, params, _parse_esearch_idlist)

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
        contradicted = self._contradicted_on_an_id_path(fields, pubmed_record, source)
        if overlap is not None and (overlap < MIN_TITLE_WORD_OVERLAP or contradicted):
            self._reject_record(entry, source, pubmed_record.get('pmid'),
                                pubmed_record.get('title'), overlap)
            if contradicted:
                entry['enrichment_rejected']['year_and_volume_differ'] = True
            return False
        if record_is_the_replied_item(entry, pubmed_record):
            self._reject_replied_item(entry, source, pubmed_record)
            return False
        self._acceptances.append(_Acceptance(
            entry, copy.deepcopy(entry), pubmed_record.get('pmid'),
            pubmed_record.get('title'), pubmed_record.get('year'), source, overlap))
        self._merge_pubmed_data(entry, pubmed_record)
        entry['enrichment_status'] = 'enriched'
        entry['enrichment_source'] = source
        self.stats['enriched'] += 1
        return True

    @staticmethod
    def _contradicted_on_an_id_path(fields: dict, pubmed_record: dict, source: str) -> bool:
        """Whether an ID path's record is another paper by the CV's own
        citation (#1696): its year and volume both differ from the CV's, and
        its title does not match the CV's against the longer of the two
        (`_same_title`), so a short record title that is a subset of the
        CV's topic words is not enough."""
        if source in TITLE_SEARCH_SOURCES or not citation_contradicts_record(fields, pubmed_record):
            return False
        source_title = cv_title(fields)
        return not any(_same_title(source_title, title or '')
                       for title in (pubmed_record.get('title'),
                                     pubmed_record.get('vernacular_title')))

    def _reject_record(self, entry: dict, source: str, pmid: str | None,
                       pubmed_title: str | None, overlap: float,
                       shared_pmid_with: int | None = None) -> None:
        """Leave the entry on its CV-extracted fields, with the record it
        refused kept under `enrichment_rejected` for audit. A refusal because
        another entry matches the record better names that entry's
        element_idx_start."""
        entry['enrichment_status'] = TITLE_CHECK_FAILED
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

    def _reject_replied_item(self, entry: dict, source: str, pubmed_record: dict) -> None:
        """Leave a CV reply on its own fields when its record is the item it
        replies to (#1438), with that record kept under `enrichment_rejected`
        for audit."""
        entry['enrichment_status'] = REPLY_TARGET_STATUS
        entry['enrichment_rejected'] = {
            'source': source,
            'pubmed_pmid': pubmed_record.get('pmid'),
            'pubmed_title': pubmed_record.get('title'),
            'pubmed_authors': pubmed_record.get('authors_vancouver'),
        }
        self.stats['reply_targets_rejected'] += 1

    def _release_weaker_shared_pmids(self) -> None:
        """One PMID ends on at most one citation of a document (#1598). When it
        was accepted for several, the holder with the strongest
        `_match_strength` keeps it and every other holder is put back as it
        was before the record was merged. Two shapes: a different paper that
        cleared the ID paths' looser MIN_TITLE_WORD_OVERLAP on shared topic
        words (AQCLHS, dev-242: a CV gave two consecutive papers one DOI, and
        the second, at 0.43, was replaced by a copy of the first), and an item
        a title search matched to a paper another entry already holds (the
        #1598 measurement: 71 PMIDs on two or more entries of 7 runs, mostly
        an abstract and its paper, or one paper listed twice). When the
        strongest holders tie, they keep the PMID if they are one citation
        (`_one_citation`): one paper listed twice, which stage 6 dedup
        collapses (stage6/dedup.py). Tied holders that are different
        citations (title or year differs) are all released, since nothing
        tells which one the record names.
        Order-independent, so it runs once every ID path and the article
        title search are done. An untitled entry has no overlap to compare
        and is left alone. An entry the CV lists as in press can keep the
        PMID but is never released: the in-press step marks it superseded
        when a published entry holds the PMID (a paper retitled after
        acceptance), and a release would promote it to a second copy of that
        paper."""
        holders: dict[str, list[_Acceptance]] = {}
        for accepted in self._acceptances:
            if accepted.overlap is not None:
                holders.setdefault(accepted.pmid, []).append(accepted)
        for group in holders.values():
            ranked = sorted(group, key=_match_strength, reverse=True)
            strongest = [accepted for accepted in ranked
                         if _match_strength(accepted) == _match_strength(ranked[0])]
            kept = len(strongest) if self._one_citation(strongest) else 0
            for accepted in ranked[kept:]:
                if listed_in_press(accepted.before):
                    continue
                stronger = ranked[1] if accepted is ranked[0] else ranked[0]
                self._restore_and_reject(accepted, stronger)

    def _one_citation(self, holders: list[_Acceptance]) -> bool:
        """Whether every pair of these holders is one citation as the CV gave
        it: an identifier in common, or the same title (`_same_title`) and
        the same year. In the #1598 measurement all 11 tied holder pairs on
        7 YUYVIG runs were this shape (ECXGAT e182/e287 and SQMWHM e84/e86
        give one DOI or PMID twice)."""
        return all(self._cv_identifiers(a) & self._cv_identifiers(b)
                   or (_same_title(cv_title(_cv_fields(a)), cv_title(_cv_fields(b)))
                       and cv_publication_year(_cv_fields(a).get('year'))
                       == cv_publication_year(_cv_fields(b).get('year')))
                   for a, b in combinations(holders, 2))

    def _cv_identifiers(self, accepted: _Acceptance) -> set[tuple[str, str]]:
        """The identifiers the CV gave for an accepted record's entry."""
        fields = _cv_fields(accepted)
        ids = {('pmid', self._clean_pmid(fields.get('pmid'))),
               ('pmcid', self._clean_pmcid(fields.get('pmcid'))),
               ('doi', (self._clean_doi(fields.get('doi')) or '').lower())}
        return {(kind, value) for kind, value in ids if value}

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
        # A PubMed list that is the CV's byline with names cut renders as the
        # CV's byline, in PubMed's form (#1661).
        elif cv_byline := cv_byline_over_shorter_pubmed_list(
                fields.get('authors'), pubmed_record.get('authors'), entry.get('text')):
            enrichment = entry['enrichment_data']
            enrichment['pubmed_authors_shorter'] = enrichment['pubmed_authors']
            enrichment['pubmed_authors'] = cv_byline

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
        # A CV line with no year takes PubMed's (#1219, RVTAQT 70): a yearless
        # article renders without one and sorts last among the newest-first.
        # One the CV calls in press is left to `_resolve_in_press`, which
        # sets the year only on a paper not already listed as published.
        if (not fields.get('year') and pubmed_record.get('year')
                and not listed_in_press(entry)):
            fields['year'] = str(pubmed_record['year'])
            entry.setdefault('enriched_fields', []).append('year')

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

    with open(output_path, 'w', encoding='utf-8') as f:
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
