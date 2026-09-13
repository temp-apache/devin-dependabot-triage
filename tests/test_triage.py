from __future__ import annotations

from typing import Any

import pytest

from app.config import Settings
from app.models import Decision, ReviewResult


def settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "github_token": "t",
        "github_webhook_secret": "s",
        "devin_api_key": "k",
        "target_repo": "acme/superset",
    }
    base.update(overrides)
    return Settings(**base)


PULL_REQUEST = {
    "number": 7,
    "title": "chore(deps): bump pytest from 7.4.4 to 9.0.3",
    "html_url": "https://github.com/acme/superset/pull/7",
}


class FakeDevin:
    def __init__(self) -> None:
        self.prompt = ""

    async def create_session(self, prompt: str, **_: Any) -> dict[str, Any]:
        self.prompt = prompt
        return {"session_id": "s-1", "url": "https://app.devin.ai/sessions/s-1"}

    async def wait_for_result(self, session_id: str, **_: Any) -> ReviewResult:
        return ReviewResult(
            decision=Decision.APPROVE_AND_MERGE, confidence=0.99, summary="fine"
        )


class FakeGitHub:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def devin_review_status(self, pull_number: int) -> str:
        return "passed"

    async def comment(self, pull_number: int, *, body: str) -> None:
        self.calls.append("comment")

    async def create_review(self, pull_number: int, *, body: str, event: str) -> None:
        self.calls.append("review")

    async def merge(self, pull_number: int, *, title: str) -> None:
        self.calls.append("merge")

    async def is_merged(self, pull_number: int) -> bool:
        return True


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeDevin, FakeGitHub]:
    from app.main import app

    devin, github = FakeDevin(), FakeGitHub()
    monkeypatch.setattr(app.state, "devin", devin, raising=False)
    monkeypatch.setattr(app.state, "github", github, raising=False)
    return devin, github


async def test_a_dry_run_forbids_the_session_from_acting(
    wired: tuple[FakeDevin, FakeGitHub],
) -> None:
    """DRY_RUN must gag Devin too, not only this service."""
    from app.main import triage

    devin, github = wired
    await triage(PULL_REQUEST, settings(dry_run=True, merge_actor="devin"))

    assert "Do not approve, merge, or comment" in devin.prompt
    assert github.calls == []


async def test_devin_is_told_to_merge_when_not_a_dry_run(
    wired: tuple[FakeDevin, FakeGitHub],
) -> None:
    from app.main import triage

    devin, github = wired
    await triage(PULL_REQUEST, settings(merge_actor="devin"))

    assert "then merge it" in devin.prompt
    assert github.calls == []  # the session acts, not the service


async def test_the_service_merges_when_it_owns_the_decision(
    wired: tuple[FakeDevin, FakeGitHub],
) -> None:
    from app.main import triage

    _, github = wired
    await triage(PULL_REQUEST, settings(merge_actor="service"))

    assert github.calls == ["review", "merge"]
