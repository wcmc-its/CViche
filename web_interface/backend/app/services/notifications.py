"""Best-effort outbound notifications for run lifecycle events.

Posts a Microsoft Teams message when a run starts processing, again when it
reaches a terminal status (complete, failed, or cancelled), and again when a
user submits feedback on a run. Fully decoupled from run execution and from
the feedback API: a missing webhook URL is a silent no-op, payload building
and delivery never raise back into the caller, and delivery itself happens on
a dedicated single-worker thread (see _DELIVERY below) so a slow or
unreachable webhook can never block the run/feedback path that triggered it.

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

Delivery model (#782 review round):
  Each notify_* call builds its payload synchronously on the caller's thread
  (cheap, in-memory, and still inside the never-raise boundary -- see
  notify_run_started et al.), then hands it to _DELIVERY, a module-level
  single-worker ThreadPoolExecutor, and returns immediately. _deliver() (run
  on that worker) retries a transient failure with a fixed bounded backoff.
  flush() blocks until everything queued so far has been attempted; tests use
  it to make delivery synchronous for assertions, and main.py's lifespan
  shutdown calls it so a pod stop doesn't drop a terminal card mid-flight.
"""

import concurrent.futures
import functools
import logging
import re
import time
from dataclasses import dataclass
from typing import NamedTuple, TypedDict
from urllib.parse import quote, urlparse

import requests

from app.config_loader import get_config
from app.models import Run

logger = logging.getLogger(__name__)

# Adaptive Card title color, keyed off status. Values are the Adaptive Card
# color enum (rendered by Teams), not hex.
_STATUS_COLOR = {
    "complete": "good",       # green
    "failed": "attention",    # red
    "cancelled": "attention", # red
    "error": "attention",     # red
    "started": "accent",      # blue -- a run just started processing
}
_DEFAULT_COLOR = "default"

# POST timeout (seconds) per attempt. Kept short so one attempt never sits on
# the delivery worker for long; _deliver bounds the total worst case anyway
# (see _RETRY_ATTEMPTS / _RETRY_BACKOFF_SECONDS below).
_POST_TIMEOUT = 10

# Max characters kept in any single card fact/title built from a run- or
# user-supplied string (filename, run id, submitter). Named per CODING
# STANDARDS §8.2 rather than an inline literal in _card_text's call sites.
_FACT_MAX_CHARS = 200

# C0 and C1 control characters -- stripped from card text so a pathological
# filename or run id can't inject formatting/control bytes into a rendered
# Adaptive Card.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")

# --- delivery: single background worker + bounded retry ---------------------
#
# ponytail: in-process single worker + bounded retry; survives neither a pod
# restart nor a crash mid-queue -- upgrade path is a transactional outbox once
# a job queue exists (none does: no taskiq/celery; the Valkey broker is
# pub/sub). Worst-case in-flight delivery is 3 * _POST_TIMEOUT + 3s ~= 33s.
_DELIVERY = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="teams-notify")

# Used only from the _DELIVERY worker thread (single worker => no lock needed
# on the Session itself; see _deliver). Long-lived so retries and repeated
# notifications reuse connections instead of paying a new TLS handshake each
# time (#782 review point 7).
_SESSION = requests.Session()

# Adaptive Card schema version on every card -- the one every card has
# shipped with and Teams renders. A client that can't render it shows the
# card's fallbackText instead (see _adaptive_card), so dropping to 1.4 would
# buy nothing (#311).
ADAPTIVE_CARD_VERSION = "1.5"

_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = (1.0, 2.0)


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
    wrong, warned about) on its own first use. validate_configuration() also
    calls this, so the warning (if any) fires once at startup rather than
    only lazily on a run's first notification.

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


def validate_configuration() -> dict[str, bool]:
    """Resolve and validate the Teams webhook config once, eagerly.

    Called from main.py's lifespan at startup (rather than only lazily on a
    run's first notification) so a missing or malformed
    CVICHE_TEAMS_WEBHOOK_URL is visible in the boot log and on /readyz instead
    of only surfacing the first time a run finishes. Notifications stay
    best-effort either way -- this never raises and never affects readiness.
    """
    raw, _ = get_config("notifications", "CVICHE_TEAMS_WEBHOOK_URL", default="")
    raw = (raw or "").strip()
    configured = bool(raw)
    valid = bool(_validate_webhook_url(raw)) if configured else False
    return {"configured": configured, "valid": valid}


def _card_text(value: object, limit: int) -> str:
    """Sanitise a run- or user-supplied string for a Teams card.

    Strips C0/C1 control characters and truncates to `limit` characters
    (with a trailing ellipsis) so a pathological filename, run id, or
    submitter name can't inject control bytes or blow up a card's size.
    """
    text = _CONTROL_CHARS.sub("", str(value))
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _action_buttons(run_id: str) -> list[dict[str, str]]:
    """The "Open run" Adaptive Card action list, or [] when no app origin is set.

    Shared by the started and terminal cards so both link the same way.
    `run_id` must be the RAW (un-truncated) id -- see _adaptive_card.
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


def _adaptive_card(
    title: str, color: str, facts: list[dict[str, str]], run_id: str, summary: str,
) -> dict:
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
        run_id: the RAW run id, used only to build the optional "Open run"
            button link (via urllib.parse.quote). Deliberately NOT the
            _card_text-truncated id used elsewhere on the card: truncating it
            here would build a link to a nonexistent, truncated run id
            (#782 review, D8 point 4) -- percent-encoding already neutralises
            anything a raw id could inject into the URL, so no length bound
            is needed for this use.
        summary: plain-text fallback/summary one-liner (see above).
    """
    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": ADAPTIVE_CARD_VERSION,
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


# --- notification DTOs -------------------------------------------------------
#
# Immutable facts extracted from the ORM (or ORM-like) objects the run/
# feedback services pass in. from_run/from_feedback are the *only* places
# that reach into those objects with getattr -- everything below them (the
# build_*_payload functions) takes a typed DTO, not an ORM object, keeping the
# notification adapter decoupled from the persistence layer (#782 review,
# r3968159100 / point 1).


@dataclass(frozen=True, slots=True)
class RunFacts:
    """Notification-relevant facts about a Run, decoupled from the ORM model."""

    id: str
    filename: str
    status: str
    total_cost: float | None
    total_duration_seconds: int | None

    @classmethod
    def from_run(cls, run: object) -> RunFacts:
        return cls(
            id=str(getattr(run, "id", None) or "unknown"),
            filename=str(getattr(run, "filename", None) or "unknown"),
            status=str(getattr(run, "status", None) or "unknown"),
            total_cost=getattr(run, "total_cost", None),
            total_duration_seconds=getattr(run, "total_duration_seconds", None),
        )


@dataclass(frozen=True, slots=True)
class FeedbackFacts:
    """Notification-relevant facts about a Feedback row, decoupled from the ORM model."""

    reviewer_role: str | None
    overall_usefulness: int | None
    likelihood_to_recommend: int | None
    overall_accuracy: int | None

    @classmethod
    def from_feedback(cls, feedback: object) -> FeedbackFacts:
        return cls(
            reviewer_role=getattr(feedback, "reviewer_role", None),
            overall_usefulness=getattr(feedback, "overall_usefulness", None),
            likelihood_to_recommend=getattr(feedback, "likelihood_to_recommend", None),
            overall_accuracy=getattr(feedback, "overall_accuracy", None),
        )


class ScoreSummary(TypedDict, total=False):
    """The cached quality-score dict, as read by build_teams_payload."""

    totalScore: float
    band: str
    # quality_score.score_run's evidence inventory (#745). Absent on a cache
    # written before #724 added it -- read as "unknown", never as incomplete.
    data_complete: bool
    missing_evidence: list[str]


class DoctorFinding(TypedDict, total=False):
    """One run-doctor finding, as read by _doctor_text."""

    lint: str
    severity: str
    message: str
    evidence: str


class DoctorSummary(TypedDict, total=False):
    """The run-doctor report payload, as read by _doctor_text."""

    counts: dict[str, int]
    findings: list[DoctorFinding]


def build_started_payload(facts: RunFacts, submitter: str | None = None) -> dict:
    """Build the Teams card for a run that just started processing.

    Leaner than the terminal card: at start there is no score, cost, or
    duration yet, so it shows only the run id, file, submitter, and status.

    submitter: display name/email of who submitted the run, or None to omit.
    """
    run_id = _card_text(facts.id, _FACT_MAX_CHARS)
    filename = _card_text(facts.filename, _FACT_MAX_CHARS)

    card_facts = [
        {"name": "Run ID", "value": run_id},
        {"name": "File", "value": filename},
    ]
    if submitter:
        card_facts.append({"name": "Submitted by", "value": _card_text(submitter, _FACT_MAX_CHARS)})
    card_facts.append({"name": "Status", "value": "started"})

    return _adaptive_card(
        f"CViche run {run_id} started", _STATUS_COLOR["started"], card_facts, facts.id,
        f"CViche run {run_id} started",
    )


def _doctor_text(doctor_report: DoctorSummary | None) -> str | None:
    """One-line summary of the run-doctor report, or None to omit the line.

    Counts substantive findings only (ERROR + WARN; INFO covers skipped-lint
    notices and informational counts), naming the most severe finding's lint
    when there is one. `lint` is a doctor-module code literal by convention
    (src/unified_pipeline/doctor/shared.py._finding), not enforced here, so
    it is run through _card_text before going on the card -- like any other
    string this module doesn't fully control the shape of (#782 review,
    r3968154302 / D8 point 3).
    """
    if not isinstance(doctor_report, dict):
        return None
    try:
        counts = doctor_report.get("counts") or {}
        total = int(counts.get("ERROR") or 0) + int(counts.get("WARN") or 0)
        if not total:
            return "0 findings"
        top = next(
            (f.get("lint") for severity in ("ERROR", "WARN")
             for f in doctor_report.get("findings") or []
             if f.get("severity") == severity and f.get("lint")),
            None,
        )
        if top:
            top = _card_text(str(top), _FACT_MAX_CHARS)
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


def _score_text(score: ScoreSummary | None) -> str:
    """The card's Quality score value: "87 (GREEN (ship))", or "n/a".

    A score computed with a scored artifact missing or unreadable says so
    (#745), so a reader does not take it for a measured result. Only the
    count goes on the card, never the missing_evidence strings: an
    "ambiguous" entry names the matched files, and those are CV filenames.
    """
    if not score or score.get("totalScore") is None:
        return "n/a"
    text = f"{score.get('totalScore')} ({score.get('band') or 'n/a'})"
    if score.get("data_complete") is False:
        missing = score.get("missing_evidence")
        count = len(missing) if isinstance(missing, list) else 0
        text += f" — incomplete: {count} file(s) missing or unreadable" if count else " — incomplete"
    return text


def build_teams_payload(
    facts: RunFacts,
    score: ScoreSummary | None = None,
    submitter: str | None = None,
    doctor_report: DoctorSummary | None = None,
) -> dict:
    """Build the Teams card for a terminal run (complete, failed, or cancelled).

    Args:
        facts: the run's notification-relevant facts (see RunFacts).
        score: the cached quality-score dict ({"totalScore": int, "band": str})
            or None when unavailable (e.g. on failure).
        submitter: display name/email of who submitted the run, or None to omit.
        doctor_report: the run-doctor report dict (run_doctor payload) or None to omit
            the Doctor line (doctor disabled, failed, or a failed run).
    """
    status = facts.status
    run_id = _card_text(facts.id, _FACT_MAX_CHARS)
    filename = _card_text(facts.filename, _FACT_MAX_CHARS)

    score_text = _score_text(score)

    total_cost = facts.total_cost
    cost_text = f"${total_cost:.4f}" if isinstance(total_cost, (int, float)) else "n/a"

    duration = facts.total_duration_seconds
    duration_text = f"{duration}s" if isinstance(duration, int) else "n/a"

    card_facts = [
        {"name": "Run ID", "value": run_id},
        {"name": "File", "value": filename},
    ]
    if submitter:
        card_facts.append({"name": "Submitted by", "value": _card_text(submitter, _FACT_MAX_CHARS)})
    # These four are deliberately NOT run through _card_text: unlike run id,
    # filename, submitter and (on the feedback card) reviewer_role, none is
    # user-, LLM- or filename-derived. `status` is written only as a code
    # literal (upload/runs/run_service/orchestrator), `band` is one of the
    # three strings quality_score.band_for returns, and cost/duration are
    # numbers guarded by isinstance above. Same for the Doctor line below: it
    # uses a finding's `lint` id (sanitised in _doctor_text) and an int()
    # count, never its `message`/`evidence`, which do quote CV text.
    card_facts += [
        {"name": "Status", "value": str(status)},
        {"name": "Quality score", "value": score_text},
        {"name": "Total cost", "value": cost_text},
        {"name": "Duration", "value": duration_text},
    ]

    doctor_text = _doctor_text(doctor_report)
    if doctor_text:
        card_facts.append({"name": "Doctor", "value": doctor_text})

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
    return _adaptive_card(f"CViche run {run_id} {status}", color, card_facts, facts.id, summary)


def build_feedback_payload(
    feedback: FeedbackFacts, run: RunFacts, submitter: str | None = None
) -> dict:
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
        feedback: the feedback's notification-relevant facts (see FeedbackFacts).
        run: the run's notification-relevant facts (see RunFacts).
        submitter: display name/email of who submitted the feedback, or None
            to omit.
    """
    run_id = _card_text(run.id, _FACT_MAX_CHARS)
    filename = _card_text(run.filename, _FACT_MAX_CHARS)
    usefulness = feedback.overall_usefulness
    recommend = feedback.likelihood_to_recommend

    card_facts = [
        {"name": "Run ID", "value": run_id},
        {"name": "File", "value": filename},
    ]
    if submitter:
        card_facts.append({"name": "Submitted by", "value": _card_text(submitter, _FACT_MAX_CHARS)})
    card_facts += [
        {"name": "Reviewer role",
         "value": _card_text(feedback.reviewer_role or "n/a", _FACT_MAX_CHARS)},
        {"name": "Overall usefulness", "value": f"{usefulness}/5" if usefulness is not None else "n/a"},
        {"name": "Likelihood to recommend", "value": f"{recommend}/5" if recommend is not None else "n/a"},
    ]
    accuracy = feedback.overall_accuracy
    if accuracy is not None:
        card_facts.append({"name": "Overall accuracy", "value": f"{accuracy}/10"})

    # A low recommend score is the signal worth a red card; a high one is a
    # green nod. 3 (neutral) falls through to the default color.
    if recommend is not None and recommend <= 2:
        color = "attention"
    elif recommend is not None and recommend >= 4:
        color = "good"
    else:
        color = _DEFAULT_COLOR

    summary = f"CViche feedback on run {run_id}: usefulness {usefulness or 'n/a'}/5, recommend {recommend or 'n/a'}/5"
    return _adaptive_card(f"CViche feedback on run {run_id}", color, card_facts, run.id, summary)


# --- delivery -----------------------------------------------------------------


class _Attempt(NamedTuple):
    """Outcome of one _do_post attempt -- internal to the retry loop in _deliver."""

    success: bool
    retryable: bool
    detail: str


def _do_post(url: str, payload: dict, session: requests.Session = _SESSION) -> _Attempt:
    """One HTTP attempt against an already-resolved, already-validated URL.

    Never itself raises requests.RequestException (caught and classified
    below); a non-request error (e.g. a payload requests can't serialise) is
    left to propagate so the caller can tell an unexpected/non-transient
    failure apart from a network one.

    `detail` never includes str(exception): a requests.RequestException's
    message can embed the full request URL, including the webhook's secret
    path and query string (#782 review, r3968176452 point 6) -- only the
    exception's type name, plus an HTTP status code when one is available,
    goes into `detail`.

    `session` defaults to the module's long-lived _SESSION; a test passes its
    own instead of monkeypatching the global (#310).
    """
    try:
        resp = session.post(url, json=payload, timeout=_POST_TIMEOUT)
    except requests.RequestException as e:
        detail = type(e).__name__
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status is not None:
            detail = f"{detail} (HTTP {status})"
        return _Attempt(success=False, retryable=True, detail=detail)
    if resp.status_code >= 400:
        retryable = resp.status_code == 429 or resp.status_code >= 500
        return _Attempt(success=False, retryable=retryable, detail=f"HTTP {resp.status_code}")
    return _Attempt(success=True, retryable=False, detail=f"HTTP {resp.status_code}")


def _deliver(url: str, payload: dict, run_id: str) -> None:
    """Runs on the _DELIVERY worker thread: retry a transient failure.

    Takes the already-resolved, already-validated webhook URL from the
    caller's thread (each notify_*) instead of calling _webhook_url() here.
    _webhook_url() reads live, mutable config (env var or auth_config.yaml)
    -- calling it on the worker thread raced the config a caller resolved
    moments earlier against whatever the worker actually saw once its task
    ran, which could be a different value if config changed in between (a
    real race, not just a test artifact: confirmed by
    test_webhook_cache_revalidates_on_each_distinct_value failing under
    reordering/repetition before this fix). Resolving once, on the caller's
    thread, and passing the result through makes one notify_* call see one
    config snapshot end to end.

    Retries a request-layer exception or an HTTP 429/5xx up to
    _RETRY_ATTEMPTS times, sleeping _RETRY_BACKOFF_SECONDS between attempts.
    Any other 4xx is not retried (repeating a client error just repeats it) --
    logged once. Exhausting all attempts logs once, summarising. Success logs
    at debug. Never raises: this is the executor's target function, so an
    uncaught exception here would only surface in an unfetched Future, never
    anywhere a human sees it.
    """
    last_detail = ""
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            result = _do_post(url, payload)
        except Exception:  # noqa: BLE001 -- best-effort, must never raise
            # Unexpected and not transient (e.g. a payload TypeError) --
            # retrying would just fail the same way again.
            logger.exception(
                "Teams notification failed for run %s with an unexpected error", run_id
            )
            return

        if result.success:
            logger.debug(
                "Teams notification delivered for run %s (attempt %d/%d)",
                run_id, attempt, _RETRY_ATTEMPTS,
            )
            return

        last_detail = result.detail
        if not result.retryable:
            logger.warning(
                "Teams notification for run %s failed (not retried): %s",
                run_id, last_detail,
            )
            return

        if attempt < _RETRY_ATTEMPTS:
            time.sleep(_RETRY_BACKOFF_SECONDS[attempt - 1])

    logger.warning(
        "Teams notification for run %s failed after %d attempts: %s",
        run_id, _RETRY_ATTEMPTS, last_detail,
    )


def _enqueue(url: str, payload: dict, run_id: str) -> None:
    """Submit a delivery to the background worker."""
    _DELIVERY.submit(_deliver, url, payload, run_id)


def flush(timeout: float = 15.0) -> None:
    """Block until every delivery queued before this call has been attempted.

    _DELIVERY has exactly one worker, so its internal task queue is FIFO: a
    no-op task submitted now can only run after every delivery already
    queued has been attempted, so waiting on that no-op's Future is
    equivalent to waiting on the whole backlog -- without this module
    keeping its own list of pending Futures (module-level mutable state
    written after import, exactly what CODING STANDARDS §4.1 flags and
    _validate_webhook_url's docstring says this module avoids elsewhere).

    Best-effort: tests call this after notify_*() to make delivery
    synchronous for assertions; main.py's lifespan shutdown calls it too, so
    a pod stop doesn't drop a terminal card that was still in flight. Never
    raises; logs (does not raise) if `timeout` elapses first.
    """
    try:
        _DELIVERY.submit(lambda: None).result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        logger.warning(
            "Teams notification flush timed out after %ss waiting for the "
            "delivery queue to drain",
            timeout,
        )


# --- public entry points -------------------------------------------------------
#
# Each of these is the never-raise boundary for its notification: converting
# the caller's object(s) to a typed DTO, building the payload, resolving/
# validating the webhook URL, and enqueueing the delivery all happen inside
# the try -- a malformed input can make DTO conversion or the builder raise,
# and _DELIVERY.submit() can itself raise RuntimeError once interpreter
# shutdown has begun (#782 review follow-up, point 5) -- and a notification
# must never affect run status, feedback submission, or surface to the user
# (#782 review, r3968255070 point 5 / point 1). The webhook URL is resolved
# here, on the caller's thread, and passed to _enqueue/_deliver explicitly --
# not re-resolved on the worker thread -- so one notify_* call sees one
# config snapshot rather than racing a config change against whenever the
# worker gets to the task (#782 review follow-up, r3968142554).


def notify_run_started(run: object, submitter: str | None = None) -> None:
    """Best-effort: queue a Teams notification for a run that just started."""
    run_id = "unknown"
    try:
        run_id = getattr(run, "id", None) or "unknown"
        payload = build_started_payload(RunFacts.from_run(run), submitter)
        url = _webhook_url()
        if not url:
            return
        _enqueue(url, payload, run_id)
    except Exception:
        logger.exception("Teams notification payload build failed for run %s", run_id)
        return


def notify_run_terminal(
    run: object,
    score: ScoreSummary | None = None,
    submitter: str | None = None,
    doctor_report: DoctorSummary | None = None,
) -> None:
    """Best-effort: queue a Teams notification for a terminal run.

    No-ops (with a warning, no post) when run.status is not one of
    Run.TERMINAL_RUN_STATUSES -- this function's name and contract are for
    terminal runs only (#782 review, r3968176452 point 5).
    """
    run_id = "unknown"
    try:
        run_id = getattr(run, "id", None) or "unknown"
        facts = RunFacts.from_run(run)
        if facts.status not in Run.TERMINAL_RUN_STATUSES:
            logger.warning(
                "notify_run_terminal called for run %s with non-terminal status %r; skipping",
                run_id, facts.status,
            )
            return
        payload = build_teams_payload(facts, score, submitter, doctor_report)
        url = _webhook_url()
        if not url:
            return
        _enqueue(url, payload, run_id)
    except Exception:
        logger.exception("Teams notification payload build failed for run %s", run_id)
        return


def notify_feedback_submitted(feedback: object, run: object, submitter: str | None = None) -> None:
    """Best-effort: queue a Teams notification when a user submits run feedback."""
    run_id = "unknown"
    try:
        run_id = getattr(run, "id", None) or "unknown"
        payload = build_feedback_payload(
            FeedbackFacts.from_feedback(feedback), RunFacts.from_run(run), submitter
        )
        url = _webhook_url()
        if not url:
            return
        _enqueue(url, payload, run_id)
    except Exception:
        logger.exception("Teams notification payload build failed for run %s", run_id)
        return
