"""Runtime configuration, read once from the environment."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    github_token: str = Field(min_length=1)
    github_webhook_secret: str = Field(min_length=1)
    devin_api_key: str = Field(min_length=1)

    target_repo: str = Field(
        description="owner/name of the repository whose Dependabot PRs are triaged",
    )
    devin_api_base: str = "https://api.devin.ai"

    bot_senders: tuple[str, ...] = ("dependabot[bot]",)

    merge_actor: str = Field(
        default="devin",
        description="'devin' lets the session approve and merge; 'service' does it here",
    )
    require_devin_review: bool = True
    min_confidence: float = 0.8
    dry_run: bool = False

    devin_org_id: str = Field(
        default="",
        description="org-<uuid>; when empty the enterprise-scoped review route is used",
    )
    trigger_devin_review: bool = Field(
        default=True,
        description="request a Devin Review when a bot PR has none, rather than fail the gate",
    )
    review_poll_interval_seconds: float = 10.0
    review_timeout_seconds: float = 600.0

    poll_interval_seconds: float = 15.0
    poll_timeout_seconds: float = 2700.0

    @property
    def owner(self) -> str:
        return self.target_repo.split("/", 1)[0]

    @property
    def name(self) -> str:
        return self.target_repo.split("/", 1)[1]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
