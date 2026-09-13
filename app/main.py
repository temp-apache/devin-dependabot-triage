"""Webhook receiver.

GitHub allows a webhook ten seconds to respond; a review takes minutes. So the endpoint
acknowledges immediately and does the work in a background task.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, Request, Response, status

from app.config import Settings, get_settings
from app.devin import DevinClient, DevinError
from app.github import (
    GitHubClient,
    apply_decision,
    format_elapsed,
    verify_signature,
)
from app.review_prompt import build_prompt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.devin = DevinClient(
        settings.devin_api_key, settings.devin_api_base, settings.devin_org_id
    )
    app.state.github = GitHubClient(settings.github_token, settings.target_repo)
    yield
    await app.state.devin.aclose()
    await app.state.github.aclose()


app = FastAPI(title="devin-dependabot-triage", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


#: Reopening is the one manual way to ask for another pass over the same pull request.
TRIAGE_ACTIONS = frozenset({"opened", "reopened"})


def _is_bot_dependency_pr(payload: dict[str, Any], settings: Settings) -> bool:
    if payload.get("action") not in TRIAGE_ACTIONS:
        return False
    # The author, not the sender: a reopen is sent by whoever clicked the button.
    author = ((payload.get("pull_request") or {}).get("user") or {}).get("login", "")
    if author not in settings.bot_senders:
        logger.info("ignoring pull request from %r", author)
        return False
    repo = (payload.get("repository") or {}).get("full_name")
    if repo != settings.target_repo:
        logger.info("ignoring pull request in %r", repo)
        return False
    return True


@app.post("/github/webhook")
async def webhook(
    request: Request,
    background: BackgroundTasks,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str | None = Header(default=None),
) -> Response:
    settings = get_settings()
    raw = await request.body()

    if not verify_signature(
        secret=settings.github_webhook_secret, body=raw, header=x_hub_signature_256
    ):
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    if x_github_event == "ping":
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    if x_github_event != "pull_request":
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    payload = json.loads(raw)
    if not _is_bot_dependency_pr(payload, settings):
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    background.add_task(
        _triage_guarded, payload["pull_request"], settings, datetime.now(timezone.utc)
    )
    return Response(status_code=status.HTTP_202_ACCEPTED)


async def _triage_guarded(
    pull_request: dict[str, Any], settings: Settings, received_at: datetime
) -> None:
    """A background task that raises is logged nowhere useful, so catch it here."""
    try:
        await triage(pull_request, settings, received_at)
    except Exception:
        logger.exception("triage failed for PR #%s", pull_request.get("number"))


def _opened_at(pull_request: dict[str, Any]) -> datetime | None:
    raw = pull_request.get("created_at")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


async def _devin_review_verdict(
    devin: DevinClient,
    github: GitHubClient,
    pr_url: str,
    number: int,
    settings: Settings,
) -> str:
    """The Devin Review verdict, requesting a review first if the PR has none.

    Devin Review runs on human pull requests, not bot ones, so a gate that only reads
    an existing verdict blocks every Dependabot PR forever. Requesting one turns the
    gate into a real check rather than a switch that has to be turned off.
    """
    verdict = await github.devin_review_status(number)
    if verdict != "absent" or not settings.trigger_devin_review:
        return verdict

    logger.info("no Devin Review on #%s, requesting one", number)
    try:
        status = await devin.wait_for_review(
            pr_url,
            interval_seconds=settings.review_poll_interval_seconds,
            timeout_seconds=settings.review_timeout_seconds,
        )
    except httpx.HTTPError:
        logger.exception("could not request a Devin Review for #%s", number)
        return "absent"

    if status != "completed":
        logger.warning("Devin Review on #%s ended as '%s'", number, status)
        return "absent"
    return await github.devin_review_status(number)


async def triage(
    pull_request: dict[str, Any],
    settings: Settings,
    received_at: datetime | None = None,
) -> None:
    number = pull_request["number"]
    pr_url = pull_request["html_url"]
    opened_at = _opened_at(pull_request)
    received_at = received_at or datetime.now(timezone.utc)
    devin: DevinClient = app.state.devin
    github: GitHubClient = app.state.github

    review_status = (
        await _devin_review_verdict(devin, github, pr_url, number, settings)
        if settings.require_devin_review
        else "not_required"
    )

    # A dry run must also stop the *session* from acting, not just this service, so the
    # prompt gets the variant that forbids touching the pull request.
    merge_actor = "service" if settings.dry_run else settings.merge_actor

    prompt = build_prompt(
        repo=settings.target_repo,
        pr_url=pr_url,
        pr_title=pull_request["title"],
        merge_actor=merge_actor,
        min_confidence=settings.min_confidence,
        review_status=review_status,
        escalation_channel=settings.escalation_channel,
        opened_at=opened_at.isoformat() if opened_at else "unknown",
        received_at=received_at.isoformat(),
    )

    session = await devin.create_session(
        prompt,
        title=f"Triage {settings.target_repo}#{number}",
        tags=["dependabot-triage"],
    )
    logger.info("PR #%s -> session %s", number, session["url"])

    try:
        result = await devin.wait_for_result(
            session["session_id"],
            interval_seconds=settings.poll_interval_seconds,
            timeout_seconds=settings.poll_timeout_seconds,
        )
    except DevinError:
        logger.exception("review failed for PR #%s", number)
        if not settings.dry_run:
            await github.comment(
                number,
                body=f"Automated triage did not complete. Session: {session['url']}",
            )
        return

    now = datetime.now(timezone.utc)
    elapsed = now - opened_at if opened_at is not None else None
    handled_in = now - received_at

    if merge_actor == "devin":
        logger.info(
            "PR #%s: %s (confidence %.2f), merged_by_devin=%s, %s since it opened, "
            "%s since the webhook arrived",
            number,
            result.decision.value,
            result.confidence,
            result.merged_by_devin,
            format_elapsed(elapsed) if elapsed else "unknown time",
            format_elapsed(handled_in),
        )
        if result.merged_by_devin and not await github.is_merged(number):
            logger.warning("PR #%s reported merged but is still open", number)
        return

    outcome = await apply_decision(
        github,
        pull_number=number,
        result=result,
        session_url=session["url"],
        min_confidence=settings.min_confidence,
        require_devin_review=settings.require_devin_review,
        dry_run=settings.dry_run,
        escalation_channel=settings.escalation_channel,
        elapsed=elapsed,
        handled_in=handled_in,
    )
    logger.info(
        "PR #%s: %s -> %s, %s since it opened, %s since the webhook arrived",
        number,
        result.decision.value,
        outcome,
        format_elapsed(elapsed) if elapsed else "unknown time",
        format_elapsed(handled_in),
    )
