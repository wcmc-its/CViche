"""The on-disk institution lookup cache and its key scheme (#523).

Moved verbatim out of stage_5b_institution_enrichment.py. The key folds in the
CV owner's disambiguation context (#582) -- see _institution_cache_key.

INSTITUTION_CACHE is REASSIGNED (``global`` + rebind) by
load_institution_cache(), so other modules must read it as an attribute of this
module (``cache.INSTITUTION_CACHE``), never via
``from ...cache import INSTITUTION_CACHE`` -- a from-import freezes the
pre-load binding and silently splits state (the #496 lesson).
"""

import hashlib
import json
from pathlib import Path
from typing import Dict, Optional

# Paths, anchored to src/unified_pipeline/ (this module moved one directory
# down in the #523 split; the cache files did not).
CACHE_FILE = Path(__file__).parent.parent / "config" / "institution_cache.json"
OLD_CACHE_FILE = Path(__file__).parent.parent / "config" / "ror_cache.json"

# Global cache for institution lookups
INSTITUTION_CACHE: Dict[str, Optional[Dict]] = {}


def load_institution_cache():
    """Load institution cache from disk, migrating from old ror_cache.json if needed."""
    global INSTITUTION_CACHE

    # Try new cache file first
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                INSTITUTION_CACHE = json.load(f)
            return
        except Exception as e:
            print(f"Warning: Could not load institution cache: {e}")
            INSTITUTION_CACHE = {}
            return

    # Migrate from old ror_cache.json if it exists
    if OLD_CACHE_FILE.exists():
        try:
            with open(OLD_CACHE_FILE, 'r', encoding='utf-8') as f:
                INSTITUTION_CACHE = json.load(f)
            # Save under new name immediately
            save_institution_cache()
            print(f"Migrated cache: {OLD_CACHE_FILE.name} → {CACHE_FILE.name} ({len(INSTITUTION_CACHE)} entries)")
        except Exception as e:
            print(f"Warning: Could not migrate old cache: {e}")
            INSTITUTION_CACHE = {}
        return

    INSTITUTION_CACHE = {}


def save_institution_cache():
    """Save institution cache to disk."""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(INSTITUTION_CACHE, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Warning: Could not save institution cache: {e}")


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
    return f"{institution_name.lower().strip()}|{_owner_context_hash(owner_context)}"
