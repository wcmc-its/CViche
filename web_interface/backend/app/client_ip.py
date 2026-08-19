"""Resolve the real client IP from X-Forwarded-For, not uvicorn's leftmost pick.

The backend runs uvicorn with ``--proxy-headers --forwarded-allow-ips='*'``
(``web_interface/backend/docker-entrypoint.sh``), which is the trust
configuration ``docs/PRODUCTION_TLS.md`` relies on for ``X-Forwarded-Proto`` /
``X-Forwarded-For`` / ``X-Forwarded-Host`` to reach the app correctly. With
that config, uvicorn's ``ProxyHeadersMiddleware``
(``uvicorn/middleware/proxy_headers.py``, ``_TrustedHosts.get_trusted_client_address``)
unconditionally takes ``x_forwarded_for_hosts[0]`` -- the *leftmost* entry in
the header -- and sets it as ``request.client.host``.

The leftmost entry is exactly the one an untrusted client controls: a caller
can send an arbitrary ``X-Forwarded-For: 1.2.3.4`` and uvicorn will treat it
as the client address. AWS ALB does not overwrite an existing
``X-Forwarded-For`` header -- it *appends* the real client IP as the last
hop. So the trustworthy value is the last entry in the raw header, not the
first, and not ``request.client.host`` once uvicorn has already picked the
wrong one.

This module reads the raw header directly and takes its last entry instead.
It assumes the load-balancer contract documented in
``docs/PRODUCTION_TLS.md`` -- a single network path to the container via the
ALB, which appends rather than trusts the incoming header -- so that the
last hop is always the one the LB itself observed.
"""
import ipaddress

from fastapi import Request


def get_client_ip(request: Request) -> str | None:
    """Return the best-effort real client IP for `request`.

    Reads the raw ``X-Forwarded-For`` header -- combining every repeated
    header field, since a proxy chain may send it as several header entries
    rather than one comma-joined value -- and takes its last non-empty entry
    (the hop ALB appends, which a client cannot override). That trailing
    entry must also pass ``ipaddress.ip_address()`` validation; if it does
    not, the header is untrusted entirely and the function falls back to
    ``request.client.host`` rather than an earlier, attacker-forgeable
    entry -- the same fallback used when the header is absent or empty.
    """
    raw_values = request.headers.getlist("x-forwarded-for")
    for raw_value in reversed(raw_values):
        for entry in reversed(raw_value.split(",")):
            candidate = entry.strip()
            if not candidate:
                continue
            try:
                ipaddress.ip_address(candidate)
            except ValueError:
                return request.client.host if request.client else None
            return candidate
    return request.client.host if request.client else None
