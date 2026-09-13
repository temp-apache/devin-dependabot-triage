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


class DevinError(RuntimeError):
    pass


class DevinClient:
    def __init__(self, api_key: str, base_url: str = "https://api.devin.ai") -> None:
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
