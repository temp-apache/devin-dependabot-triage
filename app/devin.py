"""Devin API client: start a review session, wait for its structured output."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, cast

import httpx
from pydantic import ValidationError

from app.models import REVIEW_OUTPUT_SCHEMA, ReviewResult

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {"finished", "expired", "blocked"}

#: Devin Review states that mean the worker has stopped, successfully or not.
TERMINAL_REVIEW_STATUSES = {"completed", "errored", "cancelled", "skipped"}


class DevinError(RuntimeError):
    pass


class DevinClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.devin.ai",
        org_id: str = "",
    ) -> None:
        self._reviews_path = (
            f"/v3/organizations/{org_id}/pr-reviews" if org_id else "/v3/enterprise/pr-reviews"
        )
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def create_session(
        self, prompt: str, *, title: str, tags: list[str]
    ) -> dict[str, Any]:
        response = await self._client.post(
            "/v1/sessions",
            json={
                "prompt": prompt,
                "title": title,
                "tags": tags,
                "idempotent": True,
                "structured_output_schema": REVIEW_OUTPUT_SCHEMA,
            },
        )
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    async def get_session(self, session_id: str) -> dict[str, Any]:
        response = await self._client.get(f"/v1/sessions/{session_id}")
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    async def trigger_review(self, pr_url: str) -> str:
        """Ask for a Devin Review of ``pr_url`` and return its initial status.

        Devin Review does not run by itself on bot-authored pull requests, so a gate
        that waits for a verdict waits forever unless something requests one.
        """
        response = await self._client.post(self._reviews_path, json={"pr_url": pr_url})
        if response.is_error:
            # The status alone does not say whether the key lacks the scope, the org id
            # is wrong, or the repository is not connected. The body does.
            logger.error(
                "Devin Review request for %s failed: HTTP %s %s",
                pr_url,
                response.status_code,
                response.text[:500],
            )
        response.raise_for_status()
        return str(response.json().get("status", "pending"))

    async def review_status(self, pr_url: str) -> str:
        """Status of the latest review, or ``missing`` when none has been recorded."""
        response = await self._client.get(self._reviews_path, params={"pr_url": pr_url})
        if response.status_code == 404:
            return "missing"
        response.raise_for_status()
        return str(response.json().get("status", "missing"))

    async def wait_for_review(
        self,
        pr_url: str,
        *,
        interval_seconds: float,
        timeout_seconds: float,
    ) -> str:
        """Trigger a review and poll until the worker stops. Returns the final status."""
        status = await self.trigger_review(pr_url)
        deadline = time.monotonic() + timeout_seconds
        while status not in TERMINAL_REVIEW_STATUSES:
            if time.monotonic() >= deadline:
                logger.warning("Devin Review on %s still '%s', giving up", pr_url, status)
                return status
            await asyncio.sleep(interval_seconds)
            status = await self.review_status(pr_url)
            logger.info("Devin Review on %s is %s", pr_url, status)
        return status

    async def wait_for_result(
        self,
        session_id: str,
        *,
        interval_seconds: float,
        timeout_seconds: float,
    ) -> ReviewResult:
        """Poll until the session reaches a terminal state and yields valid output.

        ``idempotent`` sessions can be replayed, so this is safe to re-enter after a
        webhook redelivery.
        """
        deadline = time.monotonic() + timeout_seconds
        while True:
            session = await self.get_session(session_id)
            # status_enum is nullable and is absent while a session is starting up;
            # status always carries something human-readable.
            status = session.get("status_enum") or session.get("status") or "unknown"

            if status in TERMINAL_STATUSES:
                output = session.get("structured_output")
                if output is None:
                    raise DevinError(
                        f"session {session_id} ended as '{status}' with no structured output"
                    )
                try:
                    return ReviewResult.model_validate(output)
                except ValidationError as exc:
                    raise DevinError(
                        f"session {session_id} returned output not matching the schema"
                    ) from exc

            if time.monotonic() >= deadline:
                raise DevinError(
                    f"session {session_id} still '{status}' after {timeout_seconds:.0f}s"
                )

            logger.info("session %s is %s, waiting", session_id, status)
            await asyncio.sleep(interval_seconds)
