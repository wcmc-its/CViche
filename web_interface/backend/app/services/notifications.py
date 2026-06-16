"""Best-effort outbound notifications for terminal run events.

Posts one Microsoft Teams message per run when it reaches a terminal status
(complete or failed). Fully decoupled from run execution: a missing webhook
URL is a silent no-op, and any delivery failure is logged and swallowed so it
can never affect run status or surface to the user.

Config (read via app.config_loader.get_config, never hardcoded):
  - notifications.CVICHE_TEAMS_WEBHOOK_URL -- the incoming-webhook URL. If
    empty/unset, notifications are disabled.
  - auth.CVICHE_ALLOWED_ORIGINS -- reused to derive the run-link base URL (its
    first origin), consistent with how the CORS layer resolves the app origin.
"""

import logging

import requests

from app.config_loader import get_config

logger = logging.getLogger(__name__)

# Themed accent for the Teams MessageCard, keyed off terminal status.
_STATUS_THEME = {
    "complete": "2EB67D",  # green
    "failed": "E01E5A",    # red
    "error": "E01E5A",     # red
}
_DEFAULT_THEME = "808080"

# POST timeout (seconds). Kept short so a slow/unreachable webhook never stalls
# the orchestrator's executor thread for long; delivery is best-effort anyway.
_POST_TIMEOUT = 10


def _run_link_base() -> str:
    """Derive the app base URL for run links from the configured origins.

    Reuses CVICHE_ALLOWED_ORIGINS (the deploy buildspec writes the real prod
    origin there; see main._resolve_allowed_origins) rather than introducing a
    second base-url knob. Returns "" if nothing usable is configured, in which
    case the payload omits the action button but still sends.
    """
    raw, source = get_config("auth", "CVICHE_ALLOWED_ORIGINS", default="")
    if source == "default" or not raw:
        return ""
    first = raw.split(",")[0].strip()
    return first.rstrip("/")


def _webhook_url() -> str:
    """Return the configured Teams webhook URL, or "" if not configured."""
    url, _ = get_config("notifications", "CVICHE_TEAMS_WEBHOOK_URL", default="")
    return (url or "").strip()


def build_teams_payload(run, score=None) -> dict:
    """Build a Teams MessageCard for a terminal run.

    Args:
        run: the Run ORM object (id, filename, status, total_cost,
            total_duration_seconds).
        score: the cached quality-score dict ({"totalScore": int, "band": str})
            or None when unavailable (e.g. on failure).

    Returns a JSON-serializable MessageCard dict.
    """
    status = getattr(run, "status", None) or "unknown"
    run_id = getattr(run, "id", None) or "unknown"
    filename = getattr(run, "filename", None) or "unknown"

    if score:
        total = score.get("totalScore")
        band = score.get("band") or "n/a"
        score_text = f"{total} ({band})" if total is not None else "n/a"
    else:
        score_text = "n/a"

    total_cost = getattr(run, "total_cost", None)
    cost_text = f"${total_cost:.4f}" if isinstance(total_cost, (int, float)) else "n/a"

    duration = getattr(run, "total_duration_seconds", None)
    duration_text = f"{duration}s" if isinstance(duration, int) else "n/a"

    theme = _STATUS_THEME.get(status, _DEFAULT_THEME)

    facts = [
        {"name": "Run ID", "value": str(run_id)},
        {"name": "File", "value": str(filename)},
        {"name": "Status", "value": str(status)},
        {"name": "Quality score", "value": score_text},
        {"name": "Total cost", "value": cost_text},
        {"name": "Duration", "value": duration_text},
    ]

    payload = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "themeColor": theme,
        "summary": f"CViche run {run_id} {status}",
        "title": f"CViche run {run_id} {status}",
        "sections": [
            {
                "activityTitle": str(filename),
                "facts": facts,
                "markdown": False,
            }
        ],
    }

    base = _run_link_base()
    if base:
        payload["potentialAction"] = [
            {
                "@type": "OpenUri",
                "name": "Open run",
                "targets": [{"os": "default", "uri": f"{base}/run/{run_id}"}],
            }
        ]

    return payload


def notify_run_terminal(run, score=None) -> None:
    """Best-effort: POST a Teams notification for a terminal run.

    No-ops silently when the webhook URL is not configured. Catches and logs
    every exception so it can never raise or affect run status.
    """
    try:
        url = _webhook_url()
        if not url:
            return

        payload = build_teams_payload(run, score)
        resp = requests.post(url, json=payload, timeout=_POST_TIMEOUT)
        # Surface non-2xx as a warning, but never raise.
        if resp.status_code >= 400:
            logger.warning(
                "Teams notification for run %s returned HTTP %s",
                getattr(run, "id", "unknown"),
                resp.status_code,
            )
    except Exception as e:  # noqa: BLE001 -- best-effort, must never raise
        logger.warning(
            "Teams notification failed for run %s: %s",
            getattr(run, "id", "unknown"),
            e,
        )
