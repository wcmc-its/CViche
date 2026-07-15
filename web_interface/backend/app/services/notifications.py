"""Best-effort outbound notifications for run lifecycle events.

Posts one Microsoft Teams message when a run reaches a terminal status
(complete or failed). Fully decoupled from run execution: a missing webhook URL
is a silent no-op, and any delivery failure is logged and swallowed so it can
never affect run status or surface to the user.

Cards are Adaptive Cards wrapped in the {"type": "message", "attachments": [...]}
envelope that the Teams *Workflows* incoming webhook expects. (The older Office
365 connector took MessageCards but Microsoft retired it; Workflows is the
supported replacement.) Each card carries a plain-text ``fallbackText`` and the
message a ``summary`` so Teams surfaces that don't render Adaptive Cards (mobile,
activity feed, previews) show a useful line rather than "cards.unsupported".

Config (read via app.config_loader.get_config, never hardcoded):
  - notifications.CVICHE_TEAMS_WEBHOOK_URL -- the Workflows webhook URL. If
    empty/unset, notifications are disabled. Treat as a secret.
  - auth.CVICHE_ALLOWED_ORIGINS -- reused to derive the run-link base URL (its
    first origin), consistent with how the CORS layer resolves the app origin.
"""

import logging

import requests

from app.config_loader import get_config

logger = logging.getLogger(__name__)

# Adaptive Card title color, keyed off status. Values are the Adaptive Card
# color enum (rendered by Teams), not hex.
_STATUS_COLOR = {
    "complete": "good",       # green
    "failed": "attention",    # red
    "error": "attention",     # red
}
_DEFAULT_COLOR = "default"

# POST timeout (seconds). Kept short so a slow/unreachable webhook never stalls
# the orchestrator's executor thread for long; delivery is best-effort anyway.
_POST_TIMEOUT = 10


def _run_link_base() -> str:
    """Derive the app base URL for run links from the configured origins.

    Reuses CVICHE_ALLOWED_ORIGINS (the deploy buildspec writes the real prod
    origin there; see main._resolve_allowed_origins) rather than introducing a
    second base-url knob. Returns "" if nothing usable is configured, in which
    case the card omits the action button but still sends.
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


def _action_buttons(run_id) -> list:
    """The "Open run" Adaptive Card action list, or [] when no app origin is set.

    Shared by the started and terminal cards so both link the same way.
    """
    base = _run_link_base()
    if not base:
        return []
    return [
        {
            "type": "Action.OpenUrl",
            "title": "Open run",
            "url": f"{base}/run/{run_id}",
        }
    ]


def _adaptive_card(title, color, facts, run_id, summary) -> dict:
    """Wrap a colored title + fact list in the Teams message/adaptive-card envelope.

    ``summary`` is a plain-text one-liner used two ways for surfaces that do NOT
    render Adaptive Cards (Teams mobile, the activity feed, channel-list
    previews): the card's ``fallbackText`` (shown in place of the card) and the
    message ``summary`` (the notification/toast text). Without it those surfaces
    show Microsoft's "Card - access it on go.skype.com/cards.unsupported"
    placeholder instead of anything useful.

    Args:
        title: the bold heading line.
        color: an Adaptive Card color enum ("good"/"attention"/"accent"/...).
        facts: list of {"name", "value"} dicts (rendered as an Adaptive FactSet).
        run_id: used to build the optional "Open run" button link.
    """
    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.5",
        "fallbackText": summary,
        "body": [
            {
                "type": "TextBlock",
                "text": title,
                "weight": "Bolder",
                "size": "Medium",
                "color": color,
                "wrap": True,
            },
            {
                "type": "FactSet",
                "facts": [
                    {"title": f["name"], "value": f["value"]} for f in facts
                ],
            },
        ],
    }

    actions = _action_buttons(run_id)
    if actions:
        card["actions"] = actions

    return {
        "type": "message",
        "summary": summary,
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": card,
            }
        ],
    }


def _doctor_text(doctor):
    """One-line summary of the run-doctor report, or None to omit the line.

    Counts substantive findings only (ERROR + WARN; INFO covers skipped-lint
    notices and informational counts), naming the most severe finding's lint
    when there is one.
    """
    if not isinstance(doctor, dict):
        return None
    try:
        counts = doctor.get("counts") or {}
        total = int(counts.get("ERROR") or 0) + int(counts.get("WARN") or 0)
        if not total:
            return "0 findings"
        top = next(
            (f.get("lint") for severity in ("ERROR", "WARN")
             for f in doctor.get("findings") or []
             if f.get("severity") == severity and f.get("lint")),
            None,
        )
        return f"{total} findings (top: {top})" if top else f"{total} findings"
    except Exception:
        # A malformed report must cost only its own line, never the card.
        return None


def build_teams_payload(run, score=None, submitter=None, doctor=None) -> dict:
    """Build the Teams card for a terminal run (complete or failed).

    Args:
        run: the Run ORM object (id, filename, status, total_cost,
            total_duration_seconds).
        score: the cached quality-score dict ({"totalScore": int, "band": str})
            or None when unavailable (e.g. on failure).
        submitter: display name/email of who submitted the run, or None to omit.
        doctor: the run-doctor report dict (run_doctor payload) or None to omit
            the Doctor line (doctor disabled, failed, or a failed run).
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

    facts = [
        {"name": "Run ID", "value": str(run_id)},
        {"name": "File", "value": str(filename)},
    ]
    if submitter:
        facts.append({"name": "Submitted by", "value": str(submitter)})
    facts += [
        {"name": "Status", "value": str(status)},
        {"name": "Quality score", "value": score_text},
        {"name": "Total cost", "value": cost_text},
        {"name": "Duration", "value": duration_text},
    ]

    doctor_text = _doctor_text(doctor)
    if doctor_text:
        facts.append({"name": "Doctor", "value": doctor_text})

    color = _STATUS_COLOR.get(status, _DEFAULT_COLOR)
    # One-line summary for the non-card surfaces (mobile / activity feed /
    # preview): mirror the useful facts a reader would otherwise have to open
    # the card to see.
    extras = []
    if score:
        extras.append(f"score {score_text}")
    if doctor_text:
        extras.append(doctor_text)
    summary = f"CViche run {run_id} {status}"
    if extras:
        summary += " — " + ", ".join(extras)
    return _adaptive_card(f"CViche run {run_id} {status}", color, facts, run_id, summary)


def _post(payload, run_id) -> None:
    """Best-effort: POST a prepared card to the Teams webhook.

    No-ops silently when the webhook URL is not configured. Catches and logs
    every exception (and any non-2xx response) so it can never raise or affect
    run status.
    """
    try:
        url = _webhook_url()
        if not url:
            return

        resp = requests.post(url, json=payload, timeout=_POST_TIMEOUT)
        # Surface non-2xx as a warning, but never raise.
        if resp.status_code >= 400:
            logger.warning(
                "Teams notification for run %s returned HTTP %s",
                run_id,
                resp.status_code,
            )
    except Exception as e:  # noqa: BLE001 -- best-effort, must never raise
        logger.warning("Teams notification failed for run %s: %s", run_id, e)


def notify_run_terminal(run, score=None, submitter=None, doctor=None) -> None:
    """Best-effort: POST a Teams notification for a terminal run.

    No-ops silently when the webhook URL is not configured. Catches and logs
    every exception so it can never raise or affect run status.
    """
    _post(build_teams_payload(run, score, submitter, doctor), getattr(run, "id", "unknown"))
