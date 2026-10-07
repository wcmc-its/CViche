"""The on-disk institution lookup cache and its key scheme (#523).

Moved verbatim out of stage_5b_institution_enrichment.py. The key folds in the
CV owner's disambiguation context (#582) -- see _institution_cache_key.

INSTITUTION_CACHE is REASSIGNED (``global`` + rebind) by
load_institution_cache(), so other modules must read it as an attribute of this
module (``cache.INSTITUTION_CACHE``), never via
``from ...cache import INSTITUTION_CACHE`` -- a from-import freezes the
pre-load binding and silently splits state (the #496 lesson).

The disk cache is for the CLI only (run_full_pipeline.py, one process at a
time): load/modify/save has no locking. The web drivers run stage 5b with
persist_cache=False -- several concurrent runs per backend process and one
per queue-worker pod, on a root-owned, non-writable config/ -- so they never
load or save this file and never touch INSTITUTION_CACHE; each run uses its
own empty dict instead (#1238). Making config/ writable for them would trade
a failed save for lost updates and torn writes across pods. A shared cache
for the web path would need a transactional store.
"""

import hashlib
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Paths, anchored to src/unified_pipeline/ (this module moved one directory
# down in the #523 split; the cache files did not).
CACHE_FILE = Path(__file__).parent.parent / "config" / "institution_cache.json"
OLD_CACHE_FILE = Path(__file__).parent.parent / "config" / "ror_cache.json"

# Global cache for institution lookups
INSTITUTION_CACHE: dict[str, dict | None] = {}


def load_institution_cache():
    """Load institution cache from disk, migrating from old ror_cache.json if needed."""
    global INSTITUTION_CACHE

    # Try new cache file first
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Could not load institution cache: %s", e)
            INSTITUTION_CACHE = {}
            return
        if not isinstance(loaded, dict):
            logger.warning("Institution cache at %s is not a JSON object; ignoring", CACHE_FILE)
            INSTITUTION_CACHE = {}
            return
        INSTITUTION_CACHE = loaded
        return

    # Migrate from old ror_cache.json if it exists
    if OLD_CACHE_FILE.exists():
        try:
            with open(OLD_CACHE_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Could not migrate old cache: %s", e)
            INSTITUTION_CACHE = {}
            return
        if not isinstance(loaded, dict):
            logger.warning("Old cache at %s is not a JSON object; ignoring", OLD_CACHE_FILE)
            INSTITUTION_CACHE = {}
            return
        INSTITUTION_CACHE = loaded
        # Save under new name immediately
        save_institution_cache()
        logger.info("Migrated cache: %s -> %s (%d entries)",
                     OLD_CACHE_FILE.name, CACHE_FILE.name, len(INSTITUTION_CACHE))
        return

    INSTITUTION_CACHE = {}


def save_institution_cache():
    """Save institution cache to disk atomically (write to a temp file, then rename)."""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = CACHE_FILE.with_suffix(f"{CACHE_FILE.suffix}.tmp")
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(INSTITUTION_CACHE, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, CACHE_FILE)
    except OSError as e:
        logger.warning("Could not save institution cache: %s", e)


def store_for_run(persist: bool) -> dict[str, dict | None]:
    """The dict one stage-5b run reads and writes: the disk cache, freshly
    loaded, on the CLI (persist=True); an empty dict of its own on the web
    path (#1238), which never touches the disk or INSTITUTION_CACHE."""
    if not persist:
        return {}
    load_institution_cache()
    return INSTITUTION_CACHE


def save_if_persisted(persist: bool) -> None:
    """Write INSTITUTION_CACHE back to disk on the CLI; nothing on the web path."""
    if persist:
        save_institution_cache()


def lookup(store: dict[str, dict | None], cache_key: str, raw_key: str | None = None):
    """Look up an institution in ``store`` by its cache key, falling back to a
    secondary raw_key only when cache_key has no entry at all. ``store`` is
    INSTITUTION_CACHE on the CLI and a per-run dict on the web path (#1238).

    Returns (found, value): `found` distinguishes "no entry under either
    key" from a legitimately cached negative result (value=None means
    "looked up, no match"). A plain `cache.get(a) or cache.get(b)` gets this
    wrong -- a None cached under `a` would fall through to `b`'s (possibly
    stale) value instead of respecting the negative result.
    """
    if cache_key in store:
        return True, store[cache_key]
    if raw_key is not None and raw_key in store:
        return True, store[raw_key]
    return False, None


def set_cached(store: dict[str, dict | None], key: str, value: dict | None) -> None:
    store[key] = value


def _owner_context_hash(owner_context: str) -> str:
    """Deterministic namespace id for an owner-disambiguation context string.

    sha256[:16] (64 bits) rather than md5[:10] (40 bits) -- this is a cache
    namespace separator, not a security boundary, but 40 bits is cheap to
    collide by accident across a large CV corpus.
    """
    return hashlib.sha256(owner_context.encode('utf-8')).hexdigest()[:16]


def _institution_cache_key(institution_name: str, owner_context: str) -> str:
    """Cache key for an institution lookup, scoped to the CV owner's
    disambiguation context.

    INSTITUTION_CACHE used to be keyed on institution name alone, but the
    value is produced by a prompt that is deliberately conditioned on whose
    CV it is (INSTITUTION_SYSTEM_PROMPT rule 3, e.g. "OU College of Medicine"
    resolving differently for an Oklahoma owner vs. an Ohio owner). A
    name-only key let the first owner to resolve an ambiguous name decide it
    for every owner afterwards, persisted to disk (#582). Folding a hash of
    the owner context into the key means two owners with different contexts
    simply never collide; two owners with the same (or no) context still
    share the cache entry, so the common unambiguous case is unaffected.

    Existing name-only keys in institution_cache.json stop matching under
    this key shape, which is the intended migration: rather than a purge
    script, each institution just gets one fresh, correctly-scoped LLM
    lookup the next time its CV is processed.
    """
    return f"{institution_name.casefold().strip()}|{_owner_context_hash(owner_context)}"
