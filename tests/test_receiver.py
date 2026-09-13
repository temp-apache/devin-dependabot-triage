from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.github import verify_signature
from app.models import REVIEW_OUTPUT_SCHEMA, ReviewResult

SECRET = "test-secret"


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    for key, value in {
        "GITHUB_TOKEN": "t",
        "GITHUB_WEBHOOK_SECRET": SECRET,
        "DEVIN_API_KEY": "k",
        "TARGET_REPO": "acme/superset",
    }.items():
        monkeypatch.setenv(key, value)

    from app.config import get_settings
    from app.main import app

    get_settings.cache_clear()
    return TestClient(app)


def payload(*, author: str = "dependabot[bot]", action: str = "opened") -> bytes:
    return json.dumps(
        {
            "action": action,
            "sender": {"login": "a-person"},
            "repository": {"full_name": "acme/superset"},
            "pull_request": {
                "user": {"login": author},
                "number": 1,
                "title": "chore(deps): bump js-yaml",
                "html_url": "https://github.com/acme/superset/pull/1",
            },
        }
    ).encode()


def test_rejects_a_bad_signature(client: TestClient) -> None:
    body = payload()
    response = client.post(
        "/github/webhook",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": "sha256=wrong"},
    )
    assert response.status_code == 401


def test_rejects_a_missing_signature(client: TestClient) -> None:
    response = client.post(
        "/github/webhook", content=payload(), headers={"X-GitHub-Event": "pull_request"}
    )
    assert response.status_code == 401


def test_ignores_a_human_pull_request(client: TestClient) -> None:
    body = payload(author="a-person")
    response = client.post(
        "/github/webhook",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": sign(body)},
    )
    assert response.status_code == 204


def test_ignores_a_synchronize_pull_request_action(client: TestClient) -> None:
    body = payload(action="synchronize")
    response = client.post(
        "/github/webhook",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": sign(body)},
    )
    assert response.status_code == 204


def test_accepts_a_dependabot_pull_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []

    async def fake_triage(
        pull_request: dict[str, Any], settings: Settings, *_: object
    ) -> None:
        seen.append(pull_request["number"])

    monkeypatch.setattr("app.main.triage", fake_triage)

    body = payload()
    response = client.post(
        "/github/webhook",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": sign(body)},
    )
    assert response.status_code == 202
    assert seen == [1]


def test_accepts_a_reopened_dependabot_pull_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []

    async def fake_triage(
        pull_request: dict[str, Any], settings: Settings, *_: object
    ) -> None:
        seen.append(pull_request["number"])

    monkeypatch.setattr("app.main.triage", fake_triage)

    body = payload(action="reopened")
    response = client.post(
        "/github/webhook",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": sign(body)},
    )
    assert response.status_code == 202
    assert seen == [1]


def test_signature_check_is_exact() -> None:
    body = b"{}"
    good = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert verify_signature(secret=SECRET, body=body, header=good)
    assert not verify_signature(secret=SECRET, body=body, header=good[:-1] + "0")
    assert not verify_signature(secret=SECRET, body=body, header=None)
    assert not verify_signature(secret=SECRET, body=b"{ }", header=good)


def test_schema_matches_the_model() -> None:
    required = set(REVIEW_OUTPUT_SCHEMA["required"])
    assert required <= set(ReviewResult.model_fields)
    assert set(REVIEW_OUTPUT_SCHEMA["properties"]) == set(ReviewResult.model_fields)
