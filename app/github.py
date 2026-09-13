"""The GitHub side: read the Devin Review verdict, act on a decision."""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import timedelta

import httpx

from app.models import Decision, ReviewResult

logger = logging.getLogger(__name__)

API_VERSION = "2022-11-28"

#: Devin Review identifies itself in the login of the account that posts its verdict.
DEVIN_REVIEW_LOGIN_MARKER = "devin"


def verify_signature(*, secret: str, body: bytes, header: str | None) -> bool:
    """Constant-time check of GitHub's ``X-Hub-Signature-256`` header."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


class GitHubClient:
    def __init__(self, token: str, repo: str) -> None:
        self.repo = repo
        self._client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
            },
            timeout=30.0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def devin_review_status(self, pull_number: int) -> str:
        """Best-effort read of the Devin Review verdict.

        Returns ``passed``, ``changes_requested``, or ``absent``. Devin Review surfaces
        as a pull request review from a Devin-owned account; a ``COMMENTED`` review with
        no requested changes is treated as passing.

        Anything that stops the verdict being read — an unreachable pull request, a
        token without access — reports ``absent``, which fails the merge gate closed.
        """
        response = await self._client.get(
            f"/repos/{self.repo}/pulls/{pull_number}/reviews",
            params={"per_page": 100},
        )
        if response.is_error:
            logger.warning(
                "could not read reviews on %s#%s: HTTP %s",
                self.repo,
                pull_number,
                response.status_code,
            )
            return "absent"
        reviews = [
            review
            for review in response.json()
            if DEVIN_REVIEW_LOGIN_MARKER in (review.get("user") or {}).get("login", "").lower()
        ]
        if not reviews:
            return "absent"
        latest = reviews[-1]["state"].upper()
        if latest in {"APPROVED", "COMMENTED"}:
            return "passed"
        return "changes_requested"

    async def create_review(self, pull_number: int, *, body: str, event: str) -> None:
        response = await self._client.post(
            f"/repos/{self.repo}/pulls/{pull_number}/reviews",
            json={"body": body, "event": event},
        )
        response.raise_for_status()

    async def comment(self, pull_number: int, *, body: str) -> None:
        response = await self._client.post(
            f"/repos/{self.repo}/issues/{pull_number}/comments",
            json={"body": body},
        )
        response.raise_for_status()

    async def merge(self, pull_number: int, *, title: str) -> None:
        response = await self._client.put(
            f"/repos/{self.repo}/pulls/{pull_number}/merge",
            json={"commit_title": title, "merge_method": "squash"},
        )
        response.raise_for_status()

    async def is_merged(self, pull_number: int) -> bool:
        response = await self._client.get(f"/repos/{self.repo}/pulls/{pull_number}")
        response.raise_for_status()
        return bool(response.json().get("merged"))


def render_comment(
    result: ReviewResult,
    session_url: str,
    elapsed: timedelta | None = None,
    handled_in: timedelta | None = None,
) -> str:
    lines = [f"**{result.decision.value}** (confidence {result.confidence:.2f})", ""]
    lines.append(result.summary)
    if result.evidence:
        lines += ["", "<details><summary>Evidence</summary>", ""]
        lines += [f"- {item}" for item in result.evidence]
        lines += ["", "</details>"]
    if elapsed is not None:
        timing = f"Reached {format_elapsed(elapsed)} after the pull request opened"
        if handled_in is not None:
            timing += f", {format_elapsed(handled_in)} after this service saw it"
        lines += ["", f"{timing}."]
    elif handled_in is not None:
        lines += ["", f"Reached {format_elapsed(handled_in)} after this service saw it."]
    lines += ["", f"[Written by Devin]({session_url})"]
    return "\n".join(lines)


def format_elapsed(elapsed: timedelta) -> str:
    seconds = max(int(elapsed.total_seconds()), 0)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


async def apply_decision(
    github: GitHubClient,
    *,
    pull_number: int,
    result: ReviewResult,
    session_url: str,
    min_confidence: float,
    require_devin_review: bool,
    dry_run: bool,
    elapsed: timedelta | None = None,
    handled_in: timedelta | None = None,
) -> str:
    """Execute a decision. Used when the service, not Devin, owns the merge."""
    body = render_comment(result, session_url, elapsed, handled_in)

    if dry_run:
        logger.info("dry run, would post on #%s:\n%s", pull_number, body)
        return "dry_run"

    if result.decision is not Decision.APPROVE_AND_MERGE:
        await github.comment(pull_number, body=body)
        return "commented"

    if result.confidence < min_confidence:
        held = f"Not merging: confidence below the {min_confidence:.2f} threshold."
        await github.comment(pull_number, body=f"{body}\n\n{held}")
        return "held_low_confidence"

    if require_devin_review:
        status = await github.devin_review_status(pull_number)
        if status != "passed":
            await github.comment(
                pull_number,
                body=f"{body}\n\nNot merging: Devin Review verdict is `{status}`.",
            )
            return "held_no_review"

    await github.create_review(pull_number, body=body, event="APPROVE")
    await github.merge(pull_number, title=f"Merge Dependabot PR #{pull_number}")
    return "merged"
