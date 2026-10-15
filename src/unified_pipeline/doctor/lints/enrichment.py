"""Lints for the two gates that decide whether a run is deliverable at all (#493).

One responsibility: whether the run has an owner and whether its citations are
the enriched ones. Both findings are about what stage 5/4 attached to a record
rather than how the record was cut or rendered, and neither reads the output
document.

`lint_pubmed_title_truncated` and `lint_enrichment_pubtype_mismatch` (EBYSBC
E19) read the same stage-5 artifact for an accepted PubMed record whose text
is wrong: a title cut short, or a correction or retraction notice accepted in
place of the paper. Stage 6 renders the PubMed title as a tracked replacement
of the CV's own, so either one reaches the document once changes are accepted.

`lint_owner_contact_missing` is the quality score's cap-25 HARD-FAIL gate --
without a usable `cv_owner` name the document cannot be delivered under
anyone's name, so it is not a degraded run but a blocked one. It reuses the
scorer's own predicate (`quality_score.cv_owner_name_missing`) so the doctor
and the score can never disagree about whether the gate fired.

`lint_enrichment_failures` is the softer half of the same question: a
publication whose stage-5 PubMed lookup ended in a `*_failed` status still
renders, but from CV-extracted fields rather than the canonical citation
(#222).

`_OWNER_GATE` and `_OWNER_CAP`, the two message fragments the owner finding
shares between its absent-artifact and empty-name branches, are used by nothing
else in `run_doctor.py`, so they move here and stop being module-global. No
name needed to move to `doctor.shared`: nothing in this domain is used by
another. Bodies are unmodified; `run_doctor` re-exports every name it exported
before.
"""

import re
import unicodedata
from collections.abc import Mapping
from difflib import SequenceMatcher
from typing import NamedTuple

from unified_pipeline.quality_score import cv_owner_name_missing
from unified_pipeline.stage6.normalization.institutions import words_beyond_location
from unified_pipeline.stage_5_pubmed_enrichment import NOTICE_PUBTYPES

from ..shared import TABLE_ROW_JOINER, _finding

# --------------------------------------------------------------------------
# Stage-5 PubMed enrichment: citations that fell back to CV-extracted fields.


#: The key stage 5 sets on a rejection when the PMID was shared with another
#: entry, whose element index it holds: that entry kept the PMID as the
#: stronger match, or, when equally strong holders were different citations,
#: was released too (#1598). Either way stage 5 put the CV's own citation
#: back (`_restore_and_reject`), which is right (RCBKFG JJUQDF 93, the fix
#: for EBYSBC AQCLHS-02).
SHARED_PMID_REJECTION_KEY = "shared_pmid_with"


def _restored_from_shared_pmid(entry: dict) -> bool:
    rejected = entry.get("enrichment_rejected")
    return isinstance(rejected, dict) and rejected.get(SHARED_PMID_REJECTION_KEY) is not None


def lint_enrichment_failures(stage5e: dict) -> list[dict]:
    """Publications whose stage-5 PubMed enrichment ended in a *_failed status
    (lookup_failed, pmcid_lookup_failed, doi_found_but_fetch_failed,
    title_check_failed; pmcid_conversion_failed in stage-5 output written before
    #1219 split it): their citations degrade to CV-extracted fields. Non-failure
    outcomes (enriched, no_identifier, doi_not_in_pubmed, pmcid_not_found, and
    reply_resolved_to_replied_item, a CV reply whose PMID is the letter it
    answers, #1438) are expected vocabulary, and so is a title check stage 5
    failed on purpose because the PMID was shared with another entry
    (`SHARED_PMID_REJECTION_KEY`): one that kept it, or, when equally strong
    holders were different citations, one that was released too (#1598)."""
    failed = [e for e in stage5e.get("entries", [])
              if str(e.get("enrichment_status") or "").endswith("_failed")
              and not _restored_from_shared_pmid(e)]
    if not failed:
        return []
    counts: dict[str, int] = {}
    for e in failed:
        status = str(e.get("enrichment_status"))
        counts[status] = counts.get(status, 0) + 1
    breakdown = ", ".join(f"{s}: {n}" for s, n in sorted(counts.items()))
    return [_finding(
        "enrichment_failures", "WARN",
        f"{len(failed)} publication(s) failed PubMed enrichment ({breakdown}) "
        f"— citations degrade to CV-extracted fields (#222)",
        [str(e.get("text", ""))[:100] for e in failed[:3]])]


# --------------------------------------------------------------------------
# Stage-5 PubMed enrichment: an accepted record whose text is wrong (EBYSBC E19).


#: The status stage 5 gives a record it accepted from PubMed. Stage 6 renders
#: only these as a tracked replacement of the CV's citation
#: (`stage6/sections/bibliography.py`), so only these can put a wrong PubMed
#: title into the document.
ACCEPTED_ENRICHMENT_STATUS = "enriched"

#: A PubMed ArticleTitle ends in one of these. Before #1358, stage 5 read the
#: title with `.text`, which stops at the first inline element (an italic
#: gene or organism name, a superscript), so a cut title ends mid-phrase:
#: an invented example is "Sediment transport in ".
#: Measured over the 63-run farm (2026-10-04): of 1,432 accepted titles, the
#: 22 that end in no character of this set are all cut, and each renders cut
#: in the base docx.
TITLE_TERMINAL_CHARS = frozenset(".?!)]\"'\u201d\u2019")

#: Notice records about a paper rather than the paper. Stage 5's
#: `NOTICE_PUBTYPES` (#1358) plus "Retraction of Publication", the PubMed
#: publication type of a retraction notice, which stage 5's set does not
#: name. "Comment" is not here: all 24
#: Comment-typed records accepted on the farm are the CV's own paper (title
#: overlap 0.86 or more), and "Retracted Publication" is the paper itself.
DOCTOR_NOTICE_PUBTYPES = NOTICE_PUBTYPES | {"Retraction of Publication"}

#: A PubMed title that opens like a notice ("Correction: <the paper's title>").
#: Applied with `re.match`, which anchors at the start, so no `^`.
_NOTICE_TITLE_RE = re.compile(
    r"\s*(?:author\s+)?(?:correction|erratum|corrigendum|retraction"
    r"|expression of concern)\b", re.IGNORECASE)

#: The CV itself lists the notice: its entry text or title names a correction,
#: erratum or retraction, so citing the notice is what the CV asked for.
_CV_NAMES_NOTICE_RE = re.compile(
    r"\b(?:correction|erratum|errata|corrigendum|retraction|retracted"
    r"|expression of concern)\b", re.IGNORECASE)


def _accepted_pubmed_records(stage5e: dict) -> list[tuple[dict, dict]]:
    """(entry, enrichment_data) for every record stage 5 accepted from PubMed."""
    accepted = []
    for e in stage5e.get("entries", []):
        data = e.get("enrichment_data")
        if e.get("enrichment_status") == ACCEPTED_ENRICHMENT_STATUS and isinstance(data, dict):
            accepted.append((e, data))
    return accepted


def _cv_title(entry: dict) -> str:
    fields = entry.get("extracted_fields")
    return str((fields or {}).get("title") or "") if isinstance(fields, dict) else ""


def _entry_label(entry: dict) -> str:
    return f"entry {entry.get('element_idx_start')} ({entry.get('taxonomy_code') or '?'})"


def lint_pubmed_title_truncated(stage5e: dict) -> list[dict]:
    """An accepted PubMed title that ends without terminal punctuation: it
    was cut at inline markup, and stage 6 renders the cut title in place of
    the CV's full one (QNZADH-02, AKPQEB-01). WARN, one per citation.

    An empty title is not reported: stage 6 then falls back to the CV's
    title (`stage6/normalization/publication.py`), so nothing cut renders.
    A PubMed title shorter than the CV's is not reported either: on the farm
    the two such titles that end in a full stop are the paper's published
    title, which differs from the one the CV gives."""
    findings = []
    for entry, data in _accepted_pubmed_records(stage5e):
        title = str(data.get("pubmed_title") or "").rstrip()
        if not title or title[-1] in TITLE_TERMINAL_CHARS:
            continue
        findings.append(_finding(
            "pubmed_title_truncated", "WARN",
            f"{_entry_label(entry)}: the PubMed title stage 5 accepted ends "
            f"mid-phrase, so the citation renders it cut (EBYSBC E19, #1358)",
            [f"PubMed title: {len(title)} chars, ends {title[-30:]!r}; "
             f"the CV's title: {len(_cv_title(entry))} chars"]))
    return findings


def lint_enrichment_pubtype_mismatch(stage5e: dict) -> list[dict]:
    """An accepted PubMed record that is a notice about a paper (an erratum,
    a retraction, an expression of concern) where the CV lists the paper:
    the citation then renders the notice's title, authors and pages
    (QNZADH-01). WARN, one per citation. A CV entry that itself names a
    correction or retraction is the notice by the CV's own choice."""
    findings = []
    for entry, data in _accepted_pubmed_records(stage5e):
        pubtypes = [str(p) for p in data.get("publication_types") or []]
        notice_types = sorted(DOCTOR_NOTICE_PUBTYPES.intersection(pubtypes))
        notice_title = bool(_NOTICE_TITLE_RE.match(str(data.get("pubmed_title") or "")))
        if not notice_types and not notice_title:
            continue
        cv_side = f"{entry.get('text') or ''} {_cv_title(entry)}"
        if _CV_NAMES_NOTICE_RE.search(cv_side):
            continue
        why = ", ".join(notice_types) or "a title that opens as a notice"
        findings.append(_finding(
            "enrichment_pubtype_mismatch", "WARN",
            f"{_entry_label(entry)}: stage 5 accepted a PubMed notice ({why}) "
            f"for a paper the CV lists, so the citation renders the notice "
            f"(EBYSBC E19, #1358)",
            [f"publication_types: {', '.join(pubtypes) or 'none'}; "
             f"enrichment_source: {entry.get('enrichment_source') or 'unknown'}"]))
    return findings


# --------------------------------------------------------------------------
# The cap-25 HARD-FAIL gate: the run has no deliverable owner.


_OWNER_GATE = "HARD-FAIL gate 'CV owner name / contact populated'"
_OWNER_CAP = "the quality score is capped at 25 (RED, do not deliver)"


def lint_owner_contact_missing(stage4: dict | None, uid: str,
                               unreadable: str | None = None) -> list[dict]:
    """The quality score's cap-25 hard-fail gate: the document cannot be
    delivered under anyone's name. The predicate is the scorer's own
    (quality_score.cv_owner_name_missing), applied to the stage-4 artifact the
    doctor already loads — the same ``*_fields.json`` the scorer reads.

    Accepts ``stage4=None`` rather than being skipped by ``_ready`` because
    score_cv_owner caps at 25 for an ABSENT ``*_fields.json`` too ("no
    fields.json found"); the call site decides when that case is a real run
    rather than a wrong uid.

    The evidence names which fields are populated but never their values: a
    partly-extracted owner (LLM found a surname but no given name) fires this
    gate, and the doctor report is mirrored to S3 and served by the admin
    viewer."""
    if stage4 is None:
        cause = (f"the stage-4 *_fields.json will not parse ({unreadable})"
                 if unreadable else
                 "there is no stage-4 *_fields.json for this document")
        return [_finding("owner_contact_missing", "ERROR",
                         f"{_OWNER_GATE}: {cause} — {_OWNER_CAP}")]
    if not cv_owner_name_missing(stage4):
        return []
    cv_owner = stage4.get("cv_owner", {}) or {}
    fields = ("full_name", "first_name", "last_name")
    populated = [f for f in fields if str(cv_owner.get(f) or "").strip()]
    evidence = ["cv_owner name fields populated: " + (", ".join(populated)
                                                      or "none")]
    if str(cv_owner.get("last_name") or "").strip().lower() == uid.lower():
        evidence.append(
            "last_name is the document uid — stage 4 fell back to the file "
            "stem, so the rendered document carries the uid as the owner name")
    return [_finding(
        "owner_contact_missing", "ERROR",
        f"{_OWNER_GATE}: the cv_owner block has no usable name — {_OWNER_CAP}",
        evidence)]


# --------------------------------------------------------------------------
# Stage-5b institution enrichment: the name it gives against the CV's own.
#
# Stage 5b returns a `cleaned_name` for each institution, meant to be the CV's
# institution without its location, and stage 6 renders it in place of stage
# 4's `institution` (`stage6/normalization/institutions.py`). Its model also
# drops a department, a platform, a parent institution or one of two
# hospitals (#1257). Stage 6 puts the qualifier back only when `cleaned_name`
# is a substring of the raw value (#1516); this lint reads what the document
# shows either way.

#: A name a model writes when it found none. Rendered, it reads as the
#: institution's name (UYQRUN 600: eight B2 rows "Unknown").
PLACEHOLDER_INSTITUTION_NAMES = frozenset({
    "unknown", "n/a", "na", "none", "not specified", "unspecified",
    "not applicable", "not available", "not provided", "null", "tbd"})
#: Words a name keeps or loses without naming anything.
_NAME_FILLER_WORDS = frozenset({"of", "the", "at", "and", "in", "for", "de", "la"})
#: How alike a dropped word and a word of the cleaned name must be for the
#: drop to be a spelling the model corrected ("Calrian" for "Clarian",
#: "Univeritat" for "Universitat", "John" for "Johns"), not a word lost.
SPELLING_VARIANT_RATIO = 0.8
#: The words that say what kind of place an institution is. One the CV wrote
#: that the lookup's name swaps for another ("Albert Einstein School of
#: Medicine" for its official "... College of Medicine") is a renaming, not
#: a qualifier lost.
INSTITUTION_KIND_WORDS = frozenset({
    "school", "college", "university", "institute", "hospital", "center", "centre"})
#: The most words a city has ("Research Triangle Park").
_MAX_PLACE_WORDS = 3
#: The separators between the parts of an institution string, as stage 6
#: splits them (`stage6/normalization/institutions.py`).
_PART_SEPARATOR_RE = re.compile(r"\s*(?:[,;/()]|\s[-\u2013\u2014]\s)\s*")
_STATE_CODE_RE = re.compile(r"^[A-Z]{2}\.?$")
#: A street address: a number, up to three capitalised words, and a street
#: word ("10627 Diebold Rd.", "3820 S. Western Ave.").
_STREET_ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+(?:[NSEW]\.?\s+)?(?:[A-Z][\w'.-]*\s+){1,3}"
    r"(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Ln|Lane|Way|Pkwy|Parkway"
    r"|Pl|Place|Ct|Court|Hwy|Highway)\b\.?")
INSTITUTION_EVIDENCE_LIMIT = 3
INSTITUTION_CLEANED_NAME_SEVERITY = "WARN"
_FOUR_DIGIT_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
#: A month and two-digit year, as stage 6 prints a date cell ("07/21").
_MONTH_YEAR_RE = re.compile(r"\b\d{1,2}/(\d{2})\b")
_WORD_RE = re.compile(r"[^\W_]+")


def _name_words(text: str) -> list[str]:
    """Lower-case, accent-folded words of two or more characters, with
    apostrophes closed up, so "Children’s" and "Children's" are one word."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    folded = re.sub(r"['’]", "", folded).lower()
    return [word for word in _WORD_RE.findall(folded) if len(word) > 1]


class _CleanedName(NamedTuple):
    """A stage-5b entry with a name from the lookup, read once (§8.1)."""
    element_idx: object
    code: str
    text: str
    #: Stage 4's `institution`, "" when it has none.
    raw: str
    #: The lookup's `cleaned_name`, else its `official_name`.
    name: str
    enrichment: Mapping[str, object]


def _cleaned_names(stage5b: dict) -> list[_CleanedName]:
    names = []
    for entry in stage5b.get("entries", []):
        enrichment = entry.get("institution_enrichment")
        if not isinstance(enrichment, Mapping):
            continue
        name = enrichment.get("cleaned_name") or enrichment.get("official_name")
        if not isinstance(name, str) or not name.strip():
            continue
        fields = entry.get("extracted_fields")
        raw = fields.get("institution") if isinstance(fields, Mapping) else None
        names.append(_CleanedName(
            entry.get("element_idx_start"), str(entry.get("taxonomy_code") or ""),
            str(entry.get("text") or ""), raw if isinstance(raw, str) else "",
            name.strip(), enrichment))
    return names


class _Row(NamedTuple):
    """One rendered line: a body paragraph, or a table row's cells."""
    text: str
    cells: tuple[str, ...]
    words: frozenset[str]
    years: frozenset[str]


def _row_years(text: str) -> frozenset[str]:
    """The years a rendered line prints, a "07/21" read as 1921 or 2021."""
    short = _MONTH_YEAR_RE.findall(text)
    return frozenset(_FOUR_DIGIT_YEAR_RE.findall(text)
                     + [century + yy for yy in short for century in ("19", "20")])


def _rendered_rows(table_rows: list[list[list[str]]],
                   blocks: list[tuple[str, str]] | None) -> list[_Row]:
    rows = []
    for table in table_rows:
        for row in table:
            cells = tuple(dict.fromkeys(cell.strip() for cell in row if cell.strip()))
            if cells:
                text = TABLE_ROW_JOINER.join(cells)
                rows.append(_Row(text, cells, frozenset(_name_words(text)), _row_years(text)))
    for kind, text in blocks or ():
        line = str(text or "").strip()
        if kind == "p" and line:
            rows.append(_Row(line, (line,), frozenset(_name_words(line)), _row_years(line)))
    return rows


def _entry_rows(rows: list[_Row], core: set[str], name: _CleanedName) -> list[_Row]:
    """The rows that show all of `core`, the likeliest to be this entry's
    first: most words shared with the CV's institution, then a year shared
    with the entry's text, then most words shared with that text."""
    years = set(_FOUR_DIGIT_YEAR_RE.findall(name.text))
    raw, text = set(_name_words(name.raw)), set(_name_words(name.text))
    return sorted((row for row in rows if core <= row.words),
                  key=lambda row: (-len(row.words & raw), not row.years & years,
                                   -len(row.words & text)))


def _abbreviates(word: str, name_words: list[str]) -> bool:
    """Whether `word` is a shortening of one of `name_words` ("dept" for
    "department", "univ", "lab"), an acronym of a run of them starting at
    any word ("hsc" for "Health Science Center", "bcm"), or a spelling of
    one the lookup corrected."""
    for i, full in enumerate(name_words):
        if len(word) < len(full) and word[0] == full[0]:
            rest = iter(full[1:])
            if all(letter in rest for letter in word[1:]):
                return True
        if "".join(w[0] for w in name_words[i:i + len(word)]) == word:
            return True
        if SequenceMatcher(None, word, full).ratio() >= SPELLING_VARIANT_RATIO:
            return True
    return False


def _renamed_kind(word: str, name_words: list[str], raw_words: list[str]) -> bool:
    """Whether `word` is a kind word the lookup's name replaced with another."""
    return word in INSTITUTION_KIND_WORDS and any(
        other in INSTITUTION_KIND_WORDS and other not in raw_words for other in name_words)


def _source_places(name: _CleanedName) -> set[str]:
    """The words of the CV's institution that name a place: a short run of
    capitalised words just before a state code, or before a part that names
    only 5b's state or country ("Palo Alto, CA", "Tiberias, Israel")."""
    parts = [part.strip() for part in _PART_SEPARATOR_RE.split(name.raw)]
    places = set()
    for part, after in zip(parts, parts[1:], strict=False):
        words = part.split()
        if not 0 < len(words) <= _MAX_PLACE_WORDS or not all(w[0].isupper() for w in words):
            continue
        region = after.split()
        if region and (all(_STATE_CODE_RE.match(w) for w in region)
                       or not words_beyond_location(after, name.name, name.enrichment)):
            places.update(_name_words(part))
    return places


def _likeliest_rows(name: _CleanedName, rows: list[_Row]) -> list[_Row]:
    """The rows that may be this entry's: those showing every word of the
    lookup's name -- and, since one school can head many rows, of those the
    ones sharing the most years with the entry's text. Empty when the name
    has no word to look for or renders in no row."""
    core = set(_name_words(name.name)) - _NAME_FILLER_WORDS
    if not core:
        return []
    shown = _entry_rows(rows, core, name)
    years = set(_FOUR_DIGIT_YEAR_RE.findall(name.text))
    most = max((len(row.years & years) for row in shown), default=0)
    return [row for row in shown if len(row.years & years) == most]


def _address_row(name: _CleanedName, rows: list[_Row]) -> tuple[str, _Row] | None:
    """The street address the CV's institution carries and the lookup's
    name drops, with the entry's row that prints it anyway: stage 6 puts a
    dropped part back when it names something besides 5b's location, and a
    street does (SXPHOG 56, "..., 10627 Diebold Rd., Fort Wayne, IN")."""
    match = _STREET_ADDRESS_RE.search(name.raw)
    if match is None or match.group().lower() in name.name.lower():
        return None
    address = " ".join(match.group().split()).lower()
    printed = [row for row in _likeliest_rows(name, rows)
               if address in " ".join(row.text.split()).lower()]
    return (match.group(), printed[0]) if printed else None


def _qualifier_lost(name: _CleanedName, rows: list[_Row]) -> tuple[list[str], _Row] | None:
    """The words of the CV's institution that the lookup's name drops, that
    are not 5b's location or a street address, and that no row showing the
    name prints, with that row; None when nothing is lost or the name
    renders in no row."""
    if not name.raw.strip():
        return None
    name_words = _name_words(name.name)
    official = _name_words(str(name.enrichment.get("official_name") or ""))
    beyond = {word for token in words_beyond_location(name.raw, name.name, name.enrichment)
              for word in _name_words(token)}
    raw_words = _name_words(name.raw)
    # A street address is a place, and dropping it is right.
    beyond -= {word for street in _STREET_ADDRESS_RE.findall(name.raw)
               for word in _name_words(street)}
    dropped = [word for word in dict.fromkeys(raw_words)
               if word in beyond and word not in _NAME_FILLER_WORDS
               and not word.isdigit() and word not in name_words
               and not _abbreviates(word, name_words + official)
               and not _renamed_kind(word, name_words, raw_words)]
    if not dropped:
        return None
    shown = _likeliest_rows(name, rows)
    if not shown:
        return None
    # Every row that might be this entry's must lack the word.
    lost = [word for word in dropped if not any(word in row.words for row in shown)]
    return (lost, shown[0]) if lost else None


def _placeholder_row(name: _CleanedName, rows: list[_Row]) -> _Row | None:
    """The row of this entry that prints the lookup's placeholder name as a
    cell or a part of one ("Unknown, Washington, DC"), when the CV's own
    institution is not that placeholder."""
    placeholder = name.name.lower()
    if placeholder not in PLACEHOLDER_INSTITUTION_NAMES or name.raw.strip().lower() == placeholder:
        return None
    shown = [row for row in rows if any(
        part.lower() == placeholder
        for cell in row.cells for part in _PART_SEPARATOR_RE.split(cell))]
    own = set(_name_words(name.text)) - set(_name_words(placeholder))
    shown = _entry_rows([row for row in shown if row.words & own], set(), name)
    return shown[0] if shown else None


def lint_institution_cleaned_name(stage5b: dict, table_rows: list[list[list[str]]],
                                  blocks: list[tuple[str, str]] | None = None) -> list[dict]:
    """Institution names stage 5b's lookup gave that lose the CV's own text
    in the document (#1257). One finding per entry, in two shapes:
      - qualifier_dropped: the name drops words of stage 4's `institution`
        (a department, a platform, a parent institution, a second hospital)
        that are not 5b's location, and no row showing the name prints them.
        A shortening, acronym or corrected spelling of the name's own words,
        or a kind word renamed ("School" for "College"), is not a loss.
      - source_place_replaced: the same, where every lost word is a city the
        CV's institution names before its state or country: the row prints
        5b's place instead ("Palo Alto, CA" rendered "Stanford, CA").
      - placeholder_name: the name is a placeholder ("Unknown") and a row
        prints it as a cell.
      - address_in_cell: the CV's institution carries a street address the
        name drops, and the entry's row prints it anyway."""
    rows = _rendered_rows(table_rows, blocks)
    findings = []
    for name in _cleaned_names(stage5b):
        label = f"entry {name.element_idx} ({name.code})"
        placeholder = _placeholder_row(name, rows)
        if placeholder is not None:
            findings.append(_finding(
                "institution_cleaned_name", INSTITUTION_CLEANED_NAME_SEVERITY,
                f"{label}: placeholder_name: stage 5b named the institution "
                f"'{name.name}', and a row prints it",
                [f"source institution: {name.raw!r}",
                 f"rendered: {placeholder.text!r}"][:INSTITUTION_EVIDENCE_LIMIT]))
            continue
        address = _address_row(name, rows)
        if address is not None:
            street, row = address
            findings.append(_finding(
                "institution_cleaned_name", INSTITUTION_CLEANED_NAME_SEVERITY,
                f"{label}: address_in_cell: the row prints the street address "
                f"{street!r} in the institution stage 5b named {name.name!r}",
                [f"source institution: {name.raw!r}",
                 f"rendered: {row.text!r}"][:INSTITUTION_EVIDENCE_LIMIT]))
            continue
        lost = _qualifier_lost(name, rows)
        if lost is None:
            continue
        words, row = lost
        shape = ("source_place_replaced" if set(words) <= _source_places(name)
                 else "qualifier_dropped")
        findings.append(_finding(
            "institution_cleaned_name", INSTITUTION_CLEANED_NAME_SEVERITY,
            f"{label}: {shape}: stage 5b's name drops "
            f"{', '.join(repr(w) for w in words)} from the CV's institution, "
            f"and the row lacks it",
            [f"source institution: {name.raw!r}", f"stage 5b name: {name.name!r}",
             f"rendered: {row.text!r}"][:INSTITUTION_EVIDENCE_LIMIT]))
    return findings
