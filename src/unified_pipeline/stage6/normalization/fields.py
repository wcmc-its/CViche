"""Coercing a stage-4 field of unpredictable shape to plain cell text (#398).

The sibling of the value modules (`authors.py`, `institutions.py`,
`content.py`, `rendering.py`, `taxonomy.py`), and separate from them because
the input is not text yet. Stage 4 stores raw LLM JSON and the extraction call runs with
`response_format={"type": "json_object"}` and no schema, so a field the renderer
expects to be a string arrives as a dict or a list of record dicts instead. The
key vocabulary is unbounded -- two corpus reproductions of the same address
disagreed (`office_address` vs `business_address`).

That is not a cosmetic problem. `cell.text = <dict>` raises deep inside
python-docx and aborts the entire document: two of 96 runs produced no
deliverable at all (#442), and a dict phone dropped every number on another
(#450). So every function here is total -- a shape it does not recognise is
stringified or joined, never skipped -- and a plain string is returned
untouched, so the CVs that never had the problem render byte-identically.

These stay in one file because they change for one reason: a new key or a new
shape observed in a real CV. The proper fix is a schema layer between stage 4
and stage 6; until that exists this is where the absence is absorbed.
"""

def _committee_cell_text(value) -> str:
    """Coerce a possibly-structured committee field to plain cell text.

    Stage 4 can emit a committee field as a dict or a list of record dicts for a
    multi-record entry (#208/#248 fusion), not just a string. Writing a non-str
    into a Word cell (``cell.text = <dict>``) raises deep in python-docx and
    aborts the whole document (#256). Never let that happen: pull the name-like
    value from a dict, join a list, and stringify anything else."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("committee_name") or value.get("committee")
                   or value.get("activity") or value.get("name")
                   or value.get("title") or "")
    if isinstance(value, list):
        return "; ".join(t for t in (_committee_cell_text(v) for v in value) if t)
    return str(value)


# Keys observed in structured stage-4 `address` values on the 2026-07-25 corpus:
# home_address, office_address, business_address. There is no convention — the
# LLM picks one — so match on all of them.
_HOME_ADDRESS_KEYS = ('home_address', 'home')
_OFFICE_ADDRESS_KEYS = ('business_address', 'office_address', 'work_address',
                        'business', 'office')


def _labels_its_own_address_slots(value) -> bool:
    """True when a dict address names its own home/office halves.

    Distinguishes ``{"home_address": ..., "office_address": ...}``, which knows
    which cell each half belongs in, from ``{"street": ..., "city": ...}``,
    which is one address in parts and must be routed by the entry's own text."""
    return isinstance(value, dict) and any(
        k in value for k in _HOME_ADDRESS_KEYS + _OFFICE_ADDRESS_KEYS)


def _address_cell_text(value, slot: str) -> str:
    """Coerce a stage-4 ``address`` field to plain text for one slot.

    ``slot`` is ``'home'`` or ``'office'``.

    Stage 4 stores raw LLM JSON, and ``coerce_field_value_types`` deliberately
    leaves dicts intact, so ``address`` reaches stage 6 as a dict on the CVs
    whose contact block is a two-column Home/Office table. ``.replace()`` on
    that dict raised AttributeError and aborted the entire document — two of 96
    runs produced no deliverable at all (#442).

    A string is returned untouched, so the CVs that never had this problem
    render byte-identically.

    Nothing is ever dropped for being an unfamiliar shape. The extraction call
    runs with ``response_format={"type": "json_object"}`` and no schema, so the
    key vocabulary is unbounded — the two corpus reproductions already disagreed
    (``office_address`` vs ``business_address``). A dict that names no slot is
    joined whole; a dict that names only one has its remaining keys joined into
    the office cell; a value nested under a slot key is recursed into rather
    than skipped. Silent loss is the failure mode this file keeps being bitten
    by, so the fallbacks favour rendering something over rendering nothing."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "; ".join(t for t in (_address_cell_text(v, slot) for v in value) if t)
    if not isinstance(value, dict):
        return str(value)

    def _join(items):
        return "; ".join(t for t in (_address_cell_text(v, slot).strip()
                                     for v in items) if t)

    if not _labels_its_own_address_slots(value):
        # One address split into parts. The caller routes it by entry text.
        return _join(value.values())

    own = _HOME_ADDRESS_KEYS if slot == 'home' else _OFFICE_ADDRESS_KEYS
    other = _OFFICE_ADDRESS_KEYS if slot == 'home' else _HOME_ADDRESS_KEYS
    for key in own:
        text = _address_cell_text(value.get(key), slot).strip()
        if text:
            return text
    if slot == 'home':
        return ""
    # Office is the catch-all: keys the home slot will never claim are joined
    # here rather than silently dropped from a partly-labelled dict.
    return _join(v for k, v in value.items() if k not in other)


# Phone is the same shape as address above: stage 4 can store a dict of
# slot -> number, and the renderer needs plain text for one slot (#450).

_CELL_PHONE_KEYS = ('cell', 'mobile', 'cell_phone', 'mobile_phone',
                    'mobile_phone_primary', 'personal_phone')


_OFFICE_PHONE_KEYS = ('office', 'work', 'business', 'office_phone',
                      'work_phone', 'phone_office', 'business_phone')


_HOME_PHONE_KEYS = ('home', 'home_phone', 'residence')


_ALL_PHONE_SLOT_KEYS = _CELL_PHONE_KEYS + _OFFICE_PHONE_KEYS + _HOME_PHONE_KEYS


def _labels_its_own_phone_slots(value) -> bool:
    """True when a dict phone names its own cell/office/home halves.

    Deliberately ANY key, not all: the real ``{"cell", "office", "fax"}`` from
    web147 is a slot map carrying one key we have no slot for, and requiring
    every key to be recognized would send it down the join path instead, which
    concatenates the fax number into whichever row asked first. A dict that
    names even one slot is routed by its own labels; keys outside
    ``_ALL_PHONE_SLOT_KEYS`` are dropped -- see ``_phone_cell_text``."""
    return isinstance(value, dict) and any(
        k in value for k in _ALL_PHONE_SLOT_KEYS)


def _phone_cell_text(value, slot: Literal['cell', 'office', 'home']) -> str:
    """Coerce a stage-4 ``phone`` field to plain text for one slot.

    Same defect family as the address field (#442): stage 4 stores raw LLM JSON
    and ``coerce_field_value_types`` leaves dicts intact, so a two-column
    contact block arrives here as ``{"cell": ..., "office": ..., "fax": ...}``.
    Phone never crashed the way address did -- ``_set_cell_text`` stringifies --
    so instead of losing the document it either rendered the dict's repr into a
    Word cell or, on web147, dropped all three numbers because the entry text
    said "Home" and the home slot has no template row (#450).

    Strings pass through untouched, so CVs that never had this render
    identically. A dict that names no slot is joined rather than dropped.

    A slot-labelled dict drops any key outside ``_ALL_PHONE_SLOT_KEYS``, and
    that is the intended render, not an oversight: the WCM template has exactly
    two phone rows, Office telephone and Cell phone. ``fax`` -- the one non-slot
    key observed in the corpus -- has nowhere to go, and joining it into the
    office row would print a fax number as the office telephone. Anything new
    stage 4 invents (``note``, ``pager``) is dropped the same way for the same
    reason. Recovering one of them means adding a template row first; widening
    the match here only moves the number into the wrong row."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "; ".join(t for t in (_phone_cell_text(v, slot) for v in value) if t)
    if not isinstance(value, dict):
        return str(value)
    if not _labels_its_own_phone_slots(value):
        # Recurse rather than str(): a nested value would otherwise render its
        # Python repr into a Word cell.
        return "; ".join(
            t for t in (_phone_cell_text(v, slot).strip() for v in value.values()) if t)
    keys = {'cell': _CELL_PHONE_KEYS, 'office': _OFFICE_PHONE_KEYS,
            'home': _HOME_PHONE_KEYS}[slot]
    for key in keys:
        text = _phone_cell_text(value.get(key), slot).strip()
        if text:
            return text
    return ""
