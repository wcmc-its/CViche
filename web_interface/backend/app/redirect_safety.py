"""Open-redirect (CWE-601) safe-target validation for post-auth redirects."""

DEFAULT_SAFE_PATH = "/"


def safe_relative_path(target: str | None, default: str = DEFAULT_SAFE_PATH) -> str:
    """Return *target* only if it is a safe same-site relative path, else *default*.

    Accepts ONLY a single-leading-slash absolute path on this site
    (e.g. "/dashboard", "/runs/ABC123?tab=log"). Rejects anything that could
    redirect off-site or smuggle a scheme:
      - empty / None
      - protocol-relative "//evil.com" or "/\\evil.com" (browser treats // as host)
      - any scheme "https://evil", "http:/evil", "javascript:alert(1)", "data:..."
      - backslashes anywhere ("\\\\evil", "/\\evil") -- browsers fold \\ to /
      - values not starting with a single "/"
    On rejection returns *default* ("/").
    """
    if not target or not isinstance(target, str):
        return default
    # Any backslash is disqualifying: browsers treat \ as / in the authority
    # component, so "/\evil.com" / "\\evil.com" would resolve off-site.
    if "\\" in target:
        return default
    # A scheme is present if there's a colon (covers "javascript:", "data:",
    # "http:/evil", "https://evil"). Also reject control chars / whitespace that
    # could break URL parsing or smuggle a header.
    if ":" in target:
        return default
    if any(c in target for c in ("\r", "\n", "\t", " ")):
        return default
    # Must be an absolute same-site path: exactly one leading slash.
    if not target.startswith("/"):
        return default
    # Reject protocol-relative "//host" (and "///host").
    if target.startswith("//"):
        return default
    return target
