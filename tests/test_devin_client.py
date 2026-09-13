from __future__ import annotations

import httpx
import pytest
import respx

from app.devin import DevinClient, DevinError

SESSION = "devin-1"
URL = f"https://api.devin.ai/v1/sessions/{SESSION}"


@pytest.fixture
async def devin() -> DevinClient:
    return DevinClient("key")


@respx.mock
async def test_a_starting_session_reports_its_plain_status(devin: DevinClient) -> None:
    """status_enum is null until a session gets going, and a null reads badly in logs."""
    respx.get(URL).mock(httpx.Response(200, json={"status": "running", "status_enum": None}))
    with pytest.raises(DevinError, match="still 'running'"):
        await devin.wait_for_result(SESSION, interval_seconds=0, timeout_seconds=0)


@respx.mock
async def test_a_finished_session_yields_its_structured_output(
    devin: DevinClient,
) -> None:
    respx.get(URL).mock(
        httpx.Response(
            200,
            json={
                "status": "finished",
                "status_enum": "finished",
                "structured_output": {
                    "decision": "decline",
                    "confidence": 0.9,
                    "summary": "Two majors ahead of the validated version.",
                    "evidence": ["requirements/development.txt:41"],
                },
            },
        )
    )
    result = await devin.wait_for_result(SESSION, interval_seconds=0, timeout_seconds=10)
    assert result.decision.value == "decline"


@respx.mock
async def test_a_finished_session_without_output_is_an_error(
    devin: DevinClient,
) -> None:
    respx.get(URL).mock(httpx.Response(200, json={"status_enum": "finished"}))
    with pytest.raises(DevinError, match="no structured output"):
        await devin.wait_for_result(SESSION, interval_seconds=0, timeout_seconds=10)
