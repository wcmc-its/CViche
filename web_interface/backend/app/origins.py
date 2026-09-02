"""The allowed-origin allowlist, and the one predicate that answers it.

Lifted out of main.py so it has more than one consumer: the CSRF middleware
there and the WebSocket endpoint in app/api/websocket.py both have to answer
"is this browser origin allowed to talk to us?", and a WebSocket upgrade is
not covered by CORS or by CSRFMiddleware (it is a GET, and the browser sends
no preflight). Importing main.py from a router would be an import cycle, so
the allowlist lives here and main.py imports it back (CODING_STANDARDS.md 2.1
-- a shared rule belongs below both of its callers, not inside one of them).

The helpers themselves are unchanged from main.py; only ``origin_permitted``
is new.
"""
import logging
from urllib.parse import urlsplit

from app.config_loader import get_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Allowed origins (env-configurable, comma-separated)
# ---------------------------------------------------------------------------
_LOCALHOST_ORIGINS = (
    "http://localhost:3000,http://localhost:3001,http://localhost:5173,"
    "http://127.0.0.1:3000,http://127.0.0.1:3001,http://127.0.0.1:5173"
)


def _resolve_allowed_origins() -> list[str]:
    """Resolve allowed origins. Precedence: CVICHE_ALLOWED_ORIGINS env var,
    then auth_config.yaml (where the deploy buildspec writes it, under `auth`),
    then localhost dev defaults.

    The deploy buildspec writes CVICHE_ALLOWED_ORIGINS into auth_config.yaml
    rather than as an env var, so an env-only lookup silently fell back to the
    localhost defaults in production -- which then rejected same-origin POST
    requests from the real prod origin via the CSRF middleware below. Reading
    the YAML as a fallback makes the configured value take effect.
    """
    raw, source = get_config("auth", "CVICHE_ALLOWED_ORIGINS", default=_LOCALHOST_ORIGINS)
    if source == "default":
        raw = _LOCALHOST_ORIGINS
    return [o.strip() for o in raw.split(",") if o.strip()]


_allowed_origins = _resolve_allowed_origins()


def _origin_key(origin: str) -> tuple[str, str, int] | None:
    """Parse an Origin value into a comparable (scheme, host, port) triple.

    Default ports are normalized so "https://host" == "https://host:443".
    Returns None when unparseable (no scheme/host, bad port), which callers
    must treat as not-allowed.
    """
    try:
        parts = urlsplit(origin.strip())
        host, port = parts.hostname, parts.port
    except ValueError:
        return None
    if not parts.scheme or not host:
        return None
    if port is None:
        port = {"http": 80, "https": 443}.get(parts.scheme, 0)
    # urlsplit/.hostname already lowercase these, but normalize explicitly so a
    # non-normalized configured origin (e.g. "HTTPS://Host") still compares equal.
    return (parts.scheme.lower(), host.lower(), port)


_allowed_origin_keys = frozenset(
    key for key in (_origin_key(o) for o in _allowed_origins) if key is not None
)


def origin_permitted(origin: str | None) -> bool:
    """Is this ``Origin`` header value allowed to reach us?

    A *missing* Origin is permitted, exactly as CSRFMiddleware permits it: the
    header is sent by browsers on cross-origin requests, so its absence means a
    non-browser client (curl, a test client, a server-to-server call), which the
    session cookie -- not this allowlist -- is what actually guards. Refusing
    those would break every non-browser consumer without closing anything a
    browser can exploit, since a browser cannot omit the header on the requests
    this defends against.

    A *present but unparseable* Origin is not permitted: _origin_key returns
    None for it and None is never in the allowlist.
    """
    if not origin:
        return True
    return _origin_key(origin) in _allowed_origin_keys
