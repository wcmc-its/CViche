"""Record-level dedup across the shapes text similarity cannot pair (#666, EBYSBC E28/E10).

`dedup.deduplicate_entries` compares raw texts inside one taxonomy code, so
four kinds of copy reach the page twice:

- one grant coded M2A in one section and M2B in another (AKPQEB-04);
- one award listed under two headings, one name a word run of the other
  (EOSAFF-06);
- one fellowship or leadership role listed once under a university and once
  under its affiliated hospital (EQGGRB-07);
- an undated header line repeating a dated appointment (RVROVQ-05, OTBUCZ-03);
- one paper coded S1 in one bibliography list and S2 or S6 in another, under
  one PMID, DOI or 5d citation (#446, a partner reviewer's returned CV).

Each rule here reads `extracted_fields`, never the text, and drops only when
the two records agree on everything that tells two records of the code apart:
the name, the year (and the month when both state one), the rank words and
the institution. Every drop is appended to the run's dedup decisions, so the
sidecar and the doctor's `dedup_drops` lint see it.

Runs after `dedup.deduplicate_entries` on what it kept. Nothing here may import
`stage_6_word_template`.
"""
import re
from collections.abc import Callable
from types import MappingProxyType

from .dedup import (
    _YEAR_RE,
    _citation_fullness,
    _citation_identity_keys,
    _citations_conflict,
    _decision_fields,
    _entry_signature_words,
    _first_author,
    _is_subsequence,
    _is_word_run,
    _ordered_words,
    _part_numbers,
    _rank_qualifiers,
    _singular,
)
from .fan_out import FANNED_OUT_FROM
from .normalization.publication import ResolvedPublication, resolve_publication

# The grant codes, one record whatever its status: grant_status_corrector moves
# a copy with an amount to M2B and leaves a copy without one in M2A (AKPQEB-04).
GRANT_FAMILY_CODES = ('M2A', 'M2B', 'M2C')
HONOR_CODE = 'H'
TRAINING_CODE = 'C'
LEADERSHIP_CODE = 'L3'
APPOINTMENT_CODE = 'D1'
# The journal-article lists: peer-reviewed, reviews and editorials, case
# reports. A paper belongs in one of them, so one PMID or DOI in two is a copy
# (#446). Stage 5's `PUBLISHED_ARTICLE_CODES` names the same three; stage 6
# keeps its own copy so it does not import stage 5, and a test pins the two
# equal. In this order, so an equal pair keeps its peer-reviewed copy.
PUBLISHED_ARTICLE_CODES = ('S1', 'S2', 'S6')
# The `dedup._citation_identity_keys` kinds that pair two article lists: the
# PMID, the DOI, and the whole 5d citation, which a stage-2 split's two halves
# share when 3b codes them apart (VGHNZD-07: the head S2, the tail S1).
# Title + journal + year is left out: it is the weakest kind even inside one
# list, and nothing on the farm needs it across lists.
_CROSS_LIST_KEY_KINDS = frozenset({'pmid', 'doi', 'citation'})
# The sidecar decision's metric for a drop made here, followed by the rule's
# name. The doctor's dedup_drops lint reads it: these drops match on fields,
# so the text coverage it tests for a text-similarity drop says nothing.
RECORD_RULE_METRIC_PREFIX = 'record='
# The dated rows that can hold an undated appointment row's titles, with the
# field that names each: an appointment, or an institutional leadership role.
_TITLE_HOLDERS = (('D1', 'title'), ('O', 'leadership_role'))

# A shorter award name inside a longer one is the same award only when it is a
# name of its own, not a generic phrase ("Teaching Award" sits inside many).
# EOSAFF-06's shorter name has 8 words.
HONOR_MIN_NAME_WORDS = 4
# A training specialty inside another ("Pediatric Critical Care" in "...
# Medicine") must be more than one word: "Surgery" sits inside every surgical
# specialty.
TRAINING_MIN_SPECIALTY_WORDS = 2
# The longest bare acronym read as an institution of unknown expansion ("NGCH").
INSTITUTION_ACRONYM_MAX_CHARS = 8

# Words every institution name shares; two names agreeing only on these are
# not shown to be one institution or a parent and its affiliate.
_GENERIC_INSTITUTION_WORDS = frozenset({
    'university', 'univ', 'hospital', 'hospitals', 'college', 'school', 'medical',
    'medicine', 'center', 'centre', 'health', 'healthcare', 'institute', 'department',
    'dept', 'division', 'children', 'childrens', 'clinic', 'clinics', 'system',
    'science', 'sciences', 'campus', 'program', 'faculty', 'graduate', 'national',
    'state', 'city', 'county', 'general', 'memorial', 'regional', 'community'})

# Abbreviations a header line and an appointment table write differently.
_TITLE_ABBREVIATIONS = MappingProxyType({
    'prof': 'professor', 'assoc': 'associate', 'asst': 'assistant',
    'clin': 'clinical', 'univ': 'university', 'dept': 'department'})
# The training level a record's type names: an intern year and a residency of
# one programme agree on specialty, years and institution and are still two
# records (#666, WWRSAL 31: stage 4 split one row naming both years into an
# intern record and a resident record, and the intern one was dropped). A type
# naming no level ("Postdoctoral Research Associate") says nothing.
_TRAINING_LEVELS = MappingProxyType({
    'intern': 'intern', 'internship': 'intern', 'resident': 'resident',
    'residency': 'resident', 'fellow': 'fellow', 'fellowship': 'fellow'})
_OPEN_END_WORDS = frozenset({'present', 'current', 'now', 'ongoing'})
_TITLE_PIECE_SEPARATOR = ';'


def _field(entry: dict, key: str) -> str:
    value = (entry.get('extracted_fields') or {}).get(key)
    return value.strip() if isinstance(value, str) else ''


def _year(value: str) -> str | None:
    match = _YEAR_RE.search(value or '')
    return match.group(0) if match else None


def _month(value: str) -> str | None:
    """The month of an ISO-shaped "YYYY-MM[-DD]" date, None otherwise."""
    match = re.match(r'(?:19|20)\d{2}-(\d{2})', value or '')
    return match.group(1) if match else None


def _same_date(first: str, second: str) -> bool:
    """Both dates name the same year, and the same month when both name one."""
    year = _year(first)
    if year is None or year != _year(second):
        return False
    months = (_month(first), _month(second))
    return None in months or months[0] == months[1]


def _norm_words(text: str) -> list[str]:
    """`_ordered_words` with abbreviations spelled out and plural s dropped
    (`dedup._singular`: "Health Sciences Center" reads as "Health Science
    Center", OTBUCZ-03)."""
    return [_singular(_TITLE_ABBREVIATIONS.get(word, word)) for word in _ordered_words(text)]


def _filled_count(entry: dict) -> int:
    fields = entry.get('extracted_fields') or {}
    return sum(1 for value in fields.values() if value not in (None, '', [], {}))


def _fuller_first(first: dict, second: dict) -> tuple[dict, dict]:
    """(kept, dropped): the copy with more filled fields, then the longer text."""
    def weight(entry: dict) -> tuple[int, int]:
        return _filled_count(entry), len(entry.get('text') or '')
    return (first, second) if weight(first) >= weight(second) else (second, first)


def _compatible(first: str, second: str) -> bool:
    """Empty on either side, or one value's words are the other's in order."""
    a, b = _norm_words(first), _norm_words(second)
    return not a or not b or _is_subsequence(a, b) or _is_subsequence(b, a)


def _same_grant(first: dict, second: dict) -> dict | None:
    """The copy to drop when two grant-family records are one grant: the same
    title, start year, role, agency and grant number."""
    title = _norm_words(_field(first, 'title'))
    if not title or title != _norm_words(_field(second, 'title')):
        return None
    if not _same_date(_field(first, 'start_date'), _field(second, 'start_date')):
        return None
    ends = (_field(first, 'end_date'), _field(second, 'end_date'))
    if all(ends) and not _same_date(*ends):
        return None
    numbers = [re.sub(r'[^a-z0-9]', '', _field(e, 'grant_number').lower()) for e in (first, second)]
    if all(numbers) and numbers[0] != numbers[1]:
        return None
    if not (_compatible(_field(first, 'pi_role'), _field(second, 'pi_role'))
            and _compatible(_field(first, 'agency'), _field(second, 'agency'))):
        return None
    return _fuller_first(first, second)[1]


def _lists_disagree(kind: str, first: tuple[dict, ResolvedPublication],
                    second: tuple[dict, ResolvedPublication]) -> bool:
    """True when two copies in two article lists that share a PMID or DOI name
    another first author or another part number: a titled commentary under
    the paper's DOI ("Editorial: <its title>" by another author), or a Part I
    and Part II that share one DOI. Inside one list `dedup._citations_conflict`
    refuses the same pairs, but reads the first author's name words
    order-blind and one letter apart, since a CV's own spelling or name order
    is not a second paper (RXYBVF); across lists a lost paper costs more than
    a duplicate, so the stricter first-word reading stays (#446 review). A
    shared whole 5d citation already prints both."""
    if kind == 'citation':
        return False
    (_first_entry, first_pub), (_second_entry, second_pub) = first, second
    authors = _first_author(first_pub), _first_author(second_pub)
    return ((all(authors) and authors[0] != authors[1])
            or _part_numbers(first_pub.title) != _part_numbers(second_pub.title))


def _same_paper(first: dict, second: dict) -> dict | None:
    """The less full copy (`dedup._citation_fullness`) when two article
    records share a PMID, a DOI or a whole 5d citation and
    `dedup._citations_conflict` finds nothing apart: the bibliography-group
    rule of `dedup._drop_citation_copies`, asked across codes. An equal pair
    drops `second`.

    Its exemptions are that rule's too: a fanned-out record (#983) and an
    in-press listing stage 5 superseded, which stage 6 renders as a tracked
    deletion of the published copy it names (BMAMWE), are never dropped.
    Both copies must name a title: a title-less line in another list is a
    note about the paper, not a second listing of it (web172: "Editorial in
    Radiology: Artificial Intelligence DOI: ..." under the paper's own DOI,
    which stage 4 also wrote onto the paper)."""
    if any(entry.get(FANNED_OUT_FROM) or entry.get('in_press_superseded') is True
           for entry in (first, second)):
        return None
    pubs = resolve_publication(first), resolve_publication(second)
    if not (pubs[0].title and pubs[1].title):
        return None
    copies = (first, pubs[0]), (second, pubs[1])
    first_keys, second_keys = ({key for key in _citation_identity_keys(*copy)
                                if key[0] in _CROSS_LIST_KEY_KINDS} for copy in copies)
    if all(_citations_conflict(kind, *copies) or _lists_disagree(kind, *copies)
           for kind, _value in first_keys & second_keys):
        return None
    return first if _citation_fullness(first, pubs[0]) < _citation_fullness(second, pubs[1]) else second


def _same_honor(first: dict, second: dict) -> dict | None:
    """The shorter-named copy when one award name is a run of the other's
    words, both name the same year, and no rank word or part number differs."""
    (short, dropped), (long, kept) = sorted(
        ((_ordered_words(_field(e, 'award_name')), e) for e in (first, second)),
        key=lambda pair: len(pair[0]))
    if len(short) < HONOR_MIN_NAME_WORDS or not _is_word_run(short, long):
        return None
    if not _same_date(_field(dropped, 'date'), _field(kept, 'date')):
        return None
    keys = ('award_name',)
    if (_rank_qualifiers(dropped.get('extracted_fields') or {}, keys)
            != _rank_qualifiers(kept.get('extracted_fields') or {}, keys)):
        return None
    if _part_numbers(_field(dropped, 'award_name')) != _part_numbers(_field(kept, 'award_name')):
        return None
    if not _compatible(_field(dropped, 'granting_body'), _field(kept, 'granting_body')):
        return None
    return dropped


def _distinctive_words(institution: str) -> set[str]:
    return {word for word in _norm_words(institution) if word not in _GENERIC_INSTITUTION_WORDS}


def _is_acronym(institution: str) -> bool:
    return bool(re.fullmatch(rf'[A-Z]{{2,{INSTITUTION_ACRONYM_MAX_CHARS}}}', institution))


def _affiliated(first: dict, second: dict) -> bool:
    """One institution, or a parent and its affiliate: the names share a word
    that is not generic ("Acme University", "Gadget Children's Hospital at
    Acme"), or one is a bare acronym the record does not expand."""
    names = (_field(first, 'institution'), _field(second, 'institution'))
    if not all(names):
        return False
    if any(_is_acronym(name) for name in names):
        return True
    return bool(_distinctive_words(names[0]) & _distinctive_words(names[1]))


def _same_span(first: dict, second: dict) -> bool:
    """Both records state a start and an end, and they name the same years."""
    return all(_same_date(_field(first, key), _field(second, key))
               for key in ('start_date', 'end_date'))


def _training_levels(entry: dict) -> set[str]:
    """The `_TRAINING_LEVELS` the entry's training type names, a plural as
    its singular (`_norm_words`: "Residents" names the resident level)."""
    return {_TRAINING_LEVELS[word] for word in _norm_words(_field(entry, 'training_type'))
            if word in _TRAINING_LEVELS}


def _levels_cover(kept: dict, dropped: dict) -> bool:
    """True when the kept copy's training type names every level the dropped
    copy's names, or names none (no evidence either way)."""
    kept_levels = _training_levels(kept)
    return not kept_levels or _training_levels(dropped) <= kept_levels


def _same_training(first: dict, second: dict) -> dict | None:
    """The copy with the shorter specialty when one specialty is a run of the
    other's words, the years are the same, the institutions affiliated and
    the other copy's type names every level this one's names. With equal
    specialties the less full copy goes, or the fuller one when only the
    less full one names every level: a row naming an internship and a
    residency holds a resident copy, and a resident row does not hold that
    row."""
    (short, dropped), (long, kept) = sorted(
        ((_norm_words(_field(e, 'specialty')), e) for e in (first, second)),
        key=lambda pair: len(pair[0]))
    if len(short) < TRAINING_MIN_SPECIALTY_WORDS or not _is_word_run(short, long):
        return None
    if not (_same_span(first, second) and _affiliated(first, second)):
        return None
    if len(short) < len(long):
        return dropped if _levels_cover(kept, dropped) else None
    kept, dropped = _fuller_first(first, second)
    if _levels_cover(kept, dropped):
        return dropped
    return kept if _levels_cover(dropped, kept) else None


def _same_leadership(first: dict, second: dict) -> dict | None:
    """The fuller copy's twin when the role and the unit are the same, the
    years are the same and the institutions affiliated."""
    for key in ('leadership_role', 'unit_program'):
        words = _norm_words(_field(first, key))
        if not words or words != _norm_words(_field(second, key)):
            return None
    if not (_same_span(first, second) and _affiliated(first, second)):
        return None
    return _fuller_first(first, second)[1]


def _is_undated(entry: dict) -> bool:
    return not _field(entry, 'start_date') and _YEAR_RE.search(entry.get('text') or '') is None


def _is_open_ended(entry: dict) -> bool:
    end = _field(entry, 'end_date')
    return not end or end.lower() in _OPEN_END_WORDS


def _piece_inside(piece: str, institution: str, row: dict, name_key: str) -> bool:
    """`piece` (one title) is the dated `row`'s, its words in order in the
    row's name and institution and no rank word apart, and `institution` is
    empty or the row's, in order."""
    words = _norm_words(piece)
    row_name, row_institution = _field(row, name_key), _field(row, 'institution')
    return (bool(words) and _is_subsequence(words, _norm_words(f'{row_name} {row_institution}'))
            and _rank_qualifiers({'title': piece}, ('title',))
            == _rank_qualifiers({'title': row_name}, ('title',))
            and (not institution
                 or _is_subsequence(_norm_words(institution), _norm_words(row_institution))))


def _undated_inside(entry: dict, dated: list[tuple[dict, str]]) -> dict | None:
    """The first dated row that holds every title of an undated appointment
    row, or None. `dated` pairs each row with its name field. An undated row
    states a current title, so only a row still open (no end, or "present")
    holds it: a past "Associate Professor" row does not say the owner holds
    that rank now."""
    pieces = [p.strip() for p in _field(entry, 'title').split(_TITLE_PIECE_SEPARATOR) if p.strip()]
    if not pieces or not _is_undated(entry):
        return None
    institution = _field(entry, 'institution')
    rows = [(row, key) for row, key in dated if _is_open_ended(row)]
    holders = [next((row for row, key in rows if _piece_inside(piece, institution, row, key)), None)
               for piece in pieces]
    return None if any(holder is None for holder in holders) else holders[0]


def _decision(dropped: dict, kept: dict, code: str, kept_code: str, rule: str) -> dict:
    """A sidecar decision shaped like `deduplicate_entries`'s, naming the rule."""
    dropped_sig, kept_sig = _entry_signature_words(dropped), _entry_signature_words(kept)
    union = dropped_sig | kept_sig
    smaller = min(len(dropped_sig), len(kept_sig))
    shared = len(dropped_sig & kept_sig)
    return {
        "metric": f"{RECORD_RULE_METRIC_PREFIX}{rule}",
        "jaccard": round(shared / len(union), 2) if union else 0,
        "containment": round(shared / smaller, 2) if smaller else 0,
        "title_containment": 0.0,
        "dropped_text": (dropped.get('text') or '')[:500],
        "kept_text": (kept.get('text') or '')[:500],
        "dropped_fields": _decision_fields(dropped, code),
        "kept_fields": _decision_fields(kept, kept_code),
        "code": code,
        "kept_code": kept_code,
    }


def _pairwise_drops(members: list[tuple[str, dict]], same: Callable[[dict, dict], dict | None],
                    rule: str, cross_code_only: bool) -> list[tuple[str, dict, dict, str]]:
    """(code, dropped, kept, kept_code) for each pair `same` judges one
    record. An entry already dropped neither drops nor vouches again."""
    gone: set[int] = set()
    drops = []
    for i, (code_i, first) in enumerate(members):
        for code_j, second in members[i + 1:]:
            if id(first) in gone:
                break
            if id(second) in gone or (cross_code_only and code_i == code_j):
                continue
            dropped = same(first, second)
            if dropped is None:
                continue
            kept, kept_code = (second, code_j) if dropped is first else (first, code_i)
            gone.add(id(dropped))
            drops.append((code_i if dropped is first else code_j, dropped, kept, kept_code))
    return drops


def _undated_drops(entries_by_code: dict[str, list[dict]]) -> list[tuple[str, dict, dict, str]]:
    """An undated appointment row held by dated appointment or leadership
    rows: a CV banner listing current titles (QNZADH-07) repeats both."""
    dated = [(row, key, code) for code, key in _TITLE_HOLDERS
             for row in entries_by_code.get(code, []) if not _is_undated(row)]
    code_of = {id(row): code for row, _key, code in dated}
    drops = []
    for entry in entries_by_code.get(APPOINTMENT_CODE, []):
        holder = _undated_inside(entry, [(row, key) for row, key, _code in dated])
        if holder is not None:
            drops.append((APPOINTMENT_CODE, entry, holder, code_of[id(holder)]))
    return drops


_PAIR_RULES = (
    (GRANT_FAMILY_CODES, _same_grant, 'grant_family', True),
    ((HONOR_CODE,), _same_honor, 'honor_name', False),
    ((TRAINING_CODE,), _same_training, 'training_affiliate', False),
    ((LEADERSHIP_CODE,), _same_leadership, 'leadership_affiliate', False),
    (PUBLISHED_ARTICLE_CODES, _same_paper, 'paper_identifier', True),
)


def deduplicate_record_groups(entries_by_code: dict[str, list[dict]],
                              decisions: list[dict],
                              dropped_ids: set[int]) -> int:
    """Drop the record-level copies `deduplicate_entries` cannot pair, in
    place, appending one decision per drop and each dropped entry's `id()` to
    `dropped_ids`. Returns how many were dropped."""
    drops = []
    for codes, same, rule, cross_code_only in _PAIR_RULES:
        members = [(code, entry) for code in codes for entry in entries_by_code.get(code, [])]
        drops += [drop + (rule,) for drop in _pairwise_drops(members, same, rule, cross_code_only)]
    drops += [drop + ('undated_appointment',) for drop in _undated_drops(entries_by_code)]
    for code, dropped, kept, kept_code, rule in drops:
        entries_by_code[code] = [e for e in entries_by_code[code] if e is not dropped]
        dropped_ids.add(id(dropped))
        decisions.append(_decision(dropped, kept, code, kept_code, rule))
    return len(drops)
