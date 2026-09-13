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
    app.state.devin = DevinClient(settings.devin_api_key, settings.devin_api_base)
    app.state.github = GitHubClient(settings.github_token, settings.target_repo)
    yield
    await app.state.devin.aclose()
    await app.state.github.aclose()


app = FastAPI(title="devin-dependabot-triage", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


def _is_bot_dependency_pr(payload: dict[str, Any], settings: Settings) -> bool:
    if payload.get("action") != "opened":
        return False
    sender = (payload.get("sender") or {}).get("login", "")
    if sender not in settings.bot_senders:
        logger.info("ignoring pull request from %r", sender)
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

    background.add_task(_triage_guarded, payload["pull_request"], settings)
    return Response(status_code=status.HTTP_202_ACCEPTED)


async def _triage_guarded(pull_request: dict[str, Any], settings: Settings) -> None:
    """A background task that raises is logged nowhere useful, so catch it here."""
    try:
        await triage(pull_request, settings)
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


async def triage(pull_request: dict[str, Any], settings: Settings) -> None:
    number = pull_request["number"]
    opened_at = _opened_at(pull_request)
    devin: DevinClient = app.state.devin
    github: GitHubClient = app.state.github

    review_status = (
        await github.devin_review_status(number)
        if settings.require_devin_review
        else "not_required"
    )

    # A dry run must also stop the *session* from acting, not just this service, so the
    # prompt gets the variant that forbids touching the pull request.
    merge_actor = "service" if settings.dry_run else settings.merge_actor

    prompt = build_prompt(
        repo=settings.target_repo,
        pr_url=pull_request["html_url"],
        pr_title=pull_request["title"],
        merge_actor=merge_actor,
        min_confidence=settings.min_confidence,
        review_status=review_status,
        opened_at=opened_at.isoformat() if opened_at else "unknown",
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

    elapsed = datetime.now(timezone.utc) - opened_at if opened_at is not None else None

    if merge_actor == "devin":
        logger.info(
            "PR #%s: %s (confidence %.2f), merged_by_devin=%s, %s since it opened",
            number,
            result.decision.value,
            result.confidence,
            result.merged_by_devin,
            format_elapsed(elapsed) if elapsed else "unknown time",
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
        elapsed=elapsed,
    )
    logger.info(
        "PR #%s: %s -> %s in %s",
        number,
        result.decision.value,
        outcome,
        format_elapsed(elapsed) if elapsed else "unknown time",
    )
