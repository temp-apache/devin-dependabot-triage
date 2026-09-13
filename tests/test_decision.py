from __future__ import annotations

import httpx
import pytest
import respx

from app.github import GitHubClient, apply_decision, render_comment
from app.models import Decision, ReviewResult
from app.review_prompt import build_prompt

REPO = "acme/superset"
SESSION = "https://app.devin.ai/sessions/abc"


def result(decision: Decision, confidence: float = 0.95) -> ReviewResult:
    return ReviewResult(
        decision=decision,
        confidence=confidence,
        summary="Patch bump; overrides admit it.",
        evidence=["superset-frontend/package.json:431 pins ^4.3.1"],
    )


@pytest.fixture
def github() -> GitHubClient:
    return GitHubClient("token", REPO)


@respx.mock
async def test_approves_and_merges_when_every_gate_passes(github: GitHubClient) -> None:
    respx.get(f"https://api.github.com/repos/{REPO}/pulls/1/reviews").mock(
        httpx.Response(
            200, json=[{"user": {"login": "devin-ai-integration[bot]"}, "state": "APPROVED"}]
        )
    )
    review = respx.post(f"https://api.github.com/repos/{REPO}/pulls/1/reviews").mock(
        httpx.Response(200, json={})
    )
    merge = respx.put(f"https://api.github.com/repos/{REPO}/pulls/1/merge").mock(
        httpx.Response(200, json={})
    )

    outcome = await apply_decision(
        github,
        pull_number=1,
        result=result(Decision.APPROVE_AND_MERGE),
        session_url=SESSION,
        min_confidence=0.8,
        require_devin_review=True,
        dry_run=False,
    )

    assert outcome == "merged"
    assert review.called and merge.called


@respx.mock
async def test_holds_when_confidence_is_below_threshold(github: GitHubClient) -> None:
    comment = respx.post(f"https://api.github.com/repos/{REPO}/issues/1/comments").mock(
        httpx.Response(201, json={})
    )
    merge = respx.put(f"https://api.github.com/repos/{REPO}/pulls/1/merge")

    outcome = await apply_decision(
        github,
        pull_number=1,
        result=result(Decision.APPROVE_AND_MERGE, confidence=0.4),
        session_url=SESSION,
        min_confidence=0.8,
        require_devin_review=True,
        dry_run=False,
    )

    assert outcome == "held_low_confidence"
    assert comment.called and not merge.called


@respx.mock
async def test_holds_when_devin_review_has_not_run(github: GitHubClient) -> None:
    respx.get(f"https://api.github.com/repos/{REPO}/pulls/1/reviews").mock(
        httpx.Response(200, json=[])
    )
    comment = respx.post(f"https://api.github.com/repos/{REPO}/issues/1/comments").mock(
        httpx.Response(201, json={})
    )
    merge = respx.put(f"https://api.github.com/repos/{REPO}/pulls/1/merge")

    outcome = await apply_decision(
        github,
        pull_number=1,
        result=result(Decision.APPROVE_AND_MERGE),
        session_url=SESSION,
        min_confidence=0.8,
        require_devin_review=True,
        dry_run=False,
    )

    assert outcome == "held_no_review"
    assert comment.called and not merge.called


@respx.mock
async def test_a_decline_only_comments(github: GitHubClient) -> None:
    comment = respx.post(f"https://api.github.com/repos/{REPO}/issues/1/comments").mock(
        httpx.Response(201, json={})
    )
    merge = respx.put(f"https://api.github.com/repos/{REPO}/pulls/1/merge")

    outcome = await apply_decision(
        github,
        pull_number=1,
        result=result(Decision.DECLINE),
        session_url=SESSION,
        min_confidence=0.8,
        require_devin_review=True,
        dry_run=False,
    )

    assert outcome == "commented"
    assert comment.called and not merge.called


async def test_dry_run_touches_nothing(github: GitHubClient) -> None:
    outcome = await apply_decision(
        github,
        pull_number=1,
        result=result(Decision.APPROVE_AND_MERGE),
        session_url=SESSION,
        min_confidence=0.8,
        require_devin_review=True,
        dry_run=True,
    )
    assert outcome == "dry_run"


def test_comment_carries_evidence_and_session_link() -> None:
    body = render_comment(result(Decision.DECLINE), SESSION)
    assert "decline" in body
    assert "package.json:431" in body
    assert SESSION in body


def test_prompt_tells_devin_to_merge_only_when_gated() -> None:
    prompt = build_prompt(
        repo=REPO,
        pr_url="https://github.com/acme/superset/pull/1",
        pr_title="bump",
        merge_actor="devin",
        min_confidence=0.8,
        review_status="passed",
    )
    assert "Devin Review verdict on this PR: passed" in prompt
    assert "confidence is at least 0.8" in prompt


def test_prompt_forbids_acting_when_the_service_merges() -> None:
    prompt = build_prompt(
        repo=REPO,
        pr_url="https://github.com/acme/superset/pull/1",
        pr_title="bump",
        merge_actor="service",
        min_confidence=0.8,
        review_status="absent",
    )
    assert "Do not approve, merge, or comment" in prompt
