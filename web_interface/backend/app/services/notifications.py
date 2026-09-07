"""Best-effort outbound notifications for run lifecycle events.

Posts a Microsoft Teams message when a run starts processing, again when it
reaches a terminal status (complete or failed), and again when a user submits
feedback on a run. Fully decoupled from run execution and from the feedback
API: a missing webhook URL is a silent no-op, and any delivery failure is
logged and swallowed so it can never affect run status, feedback submission,
or surface to the user.

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

import functools
import logging
import re
from urllib.parse import quote, urlparse

import requests

from app.config_loader import get_config

logger = logging.getLogger(__name__)

# Adaptive Card title color, keyed off status. Values are the Adaptive Card
# color enum (rendered by Teams), not hex.
_STATUS_COLOR = {
    "complete": "good",       # green
    "failed": "attention",    # red
    "error": "attention",     # red
    "started": "accent",      # blue -- a run just started processing
}
_DEFAULT_COLOR = "default"

# POST timeout (seconds). Kept short so a slow/unreachable webhook never stalls
# the orchestrator's executor thread for long; delivery is best-effort anyway.
_POST_TIMEOUT = 10

# Max characters kept in any single card fact/title built from a run- or
# user-supplied string (filename, run id, submitter). Named per CODING
# STANDARDS §8.2 rather than an inline literal in _card_text's call sites.
_FACT_MAX_CHARS = 200

# C0 and C1 control characters -- stripped from card text so a pathological
# filename or run id can't inject formatting/control bytes into a rendered
# Adaptive Card.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


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


@functools.lru_cache(maxsize=1)
def _validate_webhook_url(raw: str) -> str:
    """Return `raw` unchanged if it is a plausible https webhook URL, else "".

    Logs one warning per distinct invalid value rather than once per POST --
    memoized (via lru_cache) instead of a module-level "already warned" flag,
    so this doesn't add to CODING_STANDARDS §4.1's no-mutable-module-state
    debt, and a corrected config value still gets validated (and, if still
    wrong, warned about) on its own first use.

    The webhook URL is operator configuration (env var or auth_config.yaml),
    not user input, so this is defence in depth against a mis-set config --
    not a security boundary against an attacker-controlled value. A
    Microsoft-domain allowlist was considered and declined: Teams Workflows
    webhooks live on tenant-specific `*.logic.azure.com` hosts, so an
    allowlist would have to chase that rather than add real protection.
    """
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc:
        logger.warning(
            "CVICHE_TEAMS_WEBHOOK_URL is not a valid https URL; "
            "Teams notifications are disabled"
        )
        return ""
    return raw


def _webhook_url() -> str:
    """Return the configured Teams webhook URL, or "" if unset or invalid."""
    url, _ = get_config("notifications", "CVICHE_TEAMS_WEBHOOK_URL", default="")
    return _validate_webhook_url((url or "").strip())


def _card_text(value, limit: int) -> str:
    """Sanitise a run- or user-supplied string for a Teams card.

    Strips C0/C1 control characters and truncates to `limit` characters
    (with a trailing ellipsis) so a pathological filename, run id, or
    submitter name can't inject control bytes or blow up a card's size.
    """
    text = _CONTROL_CHARS.sub("", str(value))
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


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
            "url": f"{base}/run/{quote(str(run_id), safe='')}",
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
        run_id: used to build the optional "Open run" button link. Callers
            pass the already-sanitised (_card_text-truncated) run id here,
            not the raw one -- harmless for real run ids (UUIDs, well under
            _FACT_MAX_CHARS), but a run id over the limit would link to a
            truncated, nonexistent run rather than the real one.
        summary: plain-text fallback/summary one-liner (see above).
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


def build_started_payload(run, submitter=None) -> dict:
    """Build the Teams card for a run that just started processing.

    Leaner than the terminal card: at start there is no score, cost, or
    duration yet, so it shows only the run id, file, submitter, and status.

    submitter: display name/email of who submitted the run, or None to omit.
    """
    run_id = _card_text(getattr(run, "id", None) or "unknown", _FACT_MAX_CHARS)
    filename = _card_text(getattr(run, "filename", None) or "unknown", _FACT_MAX_CHARS)

    facts = [
        {"name": "Run ID", "value": run_id},
        {"name": "File", "value": filename},
    ]
    if submitter:
        facts.append({"name": "Submitted by", "value": _card_text(submitter, _FACT_MAX_CHARS)})
    facts.append({"name": "Status", "value": "started"})

    return _adaptive_card(
        f"CViche run {run_id} started", _STATUS_COLOR["started"], facts, run_id,
        f"CViche run {run_id} started",
    )


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
    except (TypeError, ValueError, AttributeError, OverflowError):
        # A malformed report must cost only its own line, never the card --
        # but it should still be visible, not a silent drop (CODING STANDARDS
        # §5.4). These are what a doctor dict with the wrong shape can
        # actually raise here: int() on a non-numeric count (TypeError/
        # ValueError) or a non-finite float count such as float("inf")
        # (OverflowError), .get() on a non-dict counts/finding value
        # (AttributeError), or a non-iterable findings value (TypeError).
        logger.warning("Doctor report summary failed to parse; omitting Doctor line", exc_info=True)
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
    run_id = _card_text(getattr(run, "id", None) or "unknown", _FACT_MAX_CHARS)
    filename = _card_text(getattr(run, "filename", None) or "unknown", _FACT_MAX_CHARS)

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
        {"name": "Run ID", "value": run_id},
        {"name": "File", "value": filename},
    ]
    if submitter:
        facts.append({"name": "Submitted by", "value": _card_text(submitter, _FACT_MAX_CHARS)})
    # These four are deliberately NOT run through _card_text: unlike run id,
    # filename, submitter and (on the feedback card) reviewer_role, none is
    # user-, LLM- or filename-derived. `status` is written only as a code
    # literal (upload/runs/run_service/orchestrator), `band` is one of the
    # three strings quality_score.band_for returns, and cost/duration are
    # numbers guarded by isinstance above. Same for the Doctor line below: it
    # uses a finding's `lint` id (a code literal) and an int() count, never
    # its `message`/`evidence`, which do quote CV text.
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


def build_feedback_payload(feedback, run, submitter=None) -> dict:
    """Build the Teams card for a user-submitted run feedback survey.

    Only ratings and category picks go on the card -- never a reviewer's
    free-text answer (biggest_issue, issue_locations). Those can name a
    specific person or quote CV content, and this posts to an external Teams
    webhook outside the app's access controls; open the run in-app to read
    them.

    ``reviewer_role`` is the one card fact here that is NOT a fixed pick: the
    survey's "other" option submits the reviewer's own typed text as
    reviewer_role (web_interface/frontend/src/components/FeedbackForm.tsx --
    `formData.reviewer_role_other.trim()`), and app.schemas.FeedbackSubmit
    types it as a bare ``str``. It is therefore run through _card_text like the
    other user-supplied facts.

    Args:
        feedback: the Feedback ORM object (reviewer_role, overall_usefulness,
            likelihood_to_recommend, overall_accuracy).
        run: the Run ORM object the feedback was submitted against.
        submitter: display name/email of who submitted the feedback, or None
            to omit.
    """
    run_id = _card_text(getattr(run, "id", None) or "unknown", _FACT_MAX_CHARS)
    filename = _card_text(getattr(run, "filename", None) or "unknown", _FACT_MAX_CHARS)
    usefulness = getattr(feedback, "overall_usefulness", None)
    recommend = getattr(feedback, "likelihood_to_recommend", None)

    facts = [
        {"name": "Run ID", "value": run_id},
        {"name": "File", "value": filename},
    ]
    if submitter:
        facts.append({"name": "Submitted by", "value": _card_text(submitter, _FACT_MAX_CHARS)})
    facts += [
        {"name": "Reviewer role",
         "value": _card_text(getattr(feedback, "reviewer_role", None) or "n/a", _FACT_MAX_CHARS)},
        {"name": "Overall usefulness", "value": f"{usefulness}/5" if usefulness is not None else "n/a"},
        {"name": "Likelihood to recommend", "value": f"{recommend}/5" if recommend is not None else "n/a"},
    ]
    accuracy = getattr(feedback, "overall_accuracy", None)
    if accuracy is not None:
        facts.append({"name": "Overall accuracy", "value": f"{accuracy}/10"})

    # A low recommend score is the signal worth a red card; a high one is a
    # green nod. 3 (neutral) falls through to the default color.
    if recommend is not None and recommend <= 2:
        color = "attention"
    elif recommend is not None and recommend >= 4:
        color = "good"
    else:
        color = _DEFAULT_COLOR

    summary = f"CViche feedback on run {run_id}: usefulness {usefulness or 'n/a'}/5, recommend {recommend or 'n/a'}/5"
    return _adaptive_card(f"CViche feedback on run {run_id}", color, facts, run_id, summary)


def _post(payload, run_id) -> None:
    """Best-effort: POST a prepared card to the Teams webhook.

    No-ops silently when the webhook URL is not configured. Catches and logs
    every exception (and any non-2xx response) so it can never raise or affect
    run status. Shared by the started, terminal, and feedback notifications.
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
    except requests.RequestException as e:
        # Network/HTTP-layer failure (timeout, connection refused, ...): the
        # expected best-effort failure mode, logged at WARNING without a
        # traceback.
        logger.warning("Teams notification failed for run %s: %s", run_id, e)
    except Exception:  # noqa: BLE001 -- best-effort, must never raise
        # Anything else (e.g. a bad payload TypeError) is unexpected -- still
        # swallowed per this function's contract, but with a traceback so it
        # doesn't vanish silently.
        logger.exception(
            "Teams notification failed for run %s with an unexpected error", run_id
        )


def notify_run_started(run, submitter=None) -> None:
    """Best-effort: POST a Teams notification when a run starts processing."""
    _post(build_started_payload(run, submitter), getattr(run, "id", "unknown"))


def notify_run_terminal(run, score=None, submitter=None, doctor=None) -> None:
    """Best-effort: POST a Teams notification for a terminal run.

    No-ops silently when the webhook URL is not configured. Catches and logs
    every exception so it can never raise or affect run status.
    """
    _post(build_teams_payload(run, score, submitter, doctor), getattr(run, "id", "unknown"))


def notify_feedback_submitted(feedback, run, submitter=None) -> None:
    """Best-effort: POST a Teams notification when a user submits run feedback.

    No-ops silently when the webhook URL is not configured. Catches and logs
    every exception so it can never raise or affect the feedback submission.
    """
    _post(build_feedback_payload(feedback, run, submitter), getattr(run, "id", "unknown"))
