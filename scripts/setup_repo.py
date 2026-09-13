#!/usr/bin/env python3
"""Configure the target repository. Idempotent; safe to re-run.

Does the four things GitHub exposes over REST:

  1. Dependabot alerts          PUT /repos/{repo}/vulnerability-alerts
  2. Dependabot security updates PUT /repos/{repo}/automated-security-fixes
  3. Actions off                 PUT /repos/{repo}/actions/permissions
  4. The webhook                 POST /repos/{repo}/hooks

It cannot do the fifth: **Dependabot version updates on a fork**. There is no endpoint
for it anywhere in GitHub's REST surface, and forks do not inherit it from a committed
.github/dependabot.yml. Enable it by hand at
  Settings -> Advanced Security -> Dependabot version updates -> Enable

Usage:
  python scripts/setup_repo.py --repo owner/name --webhook-url https://smee.io/abc123
"""

from __future__ import annotations

import argparse
import os
import sys

import httpx

API = "https://api.github.com"
HEADERS_VERSION = "2022-11-28"


def client(token: str) -> httpx.Client:
    return httpx.Client(
        base_url=API,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": HEADERS_VERSION,
        },
        timeout=30.0,
    )


def enable_dependabot_alerts(gh: httpx.Client, repo: str) -> None:
    gh.put(f"/repos/{repo}/vulnerability-alerts").raise_for_status()
    print("  dependabot alerts: enabled")


def enable_security_updates(gh: httpx.Client, repo: str) -> None:
    gh.put(f"/repos/{repo}/automated-security-fixes").raise_for_status()
    print("  dependabot security updates: enabled")


def disable_actions(gh: httpx.Client, repo: str) -> None:
    """Superset carries 55 workflows; every PR would drag the full matrix along."""
    gh.put(f"/repos/{repo}/actions/permissions", json={"enabled": False}).raise_for_status()
    print("  actions: disabled")


def ensure_webhook(gh: httpx.Client, repo: str, url: str, secret: str) -> None:
    existing = gh.get(f"/repos/{repo}/hooks")
    existing.raise_for_status()
    for hook in existing.json():
        if hook.get("config", {}).get("url") == url:
            print(f"  webhook: already present (id {hook['id']})")
            return

    response = gh.post(
        f"/repos/{repo}/hooks",
        json={
            "name": "web",
            "active": True,
            "events": ["pull_request", "pull_request_review"],
            "config": {
                "url": url,
                "content_type": "json",
                "secret": secret,
                "insecure_ssl": "0",
            },
        },
    )
    response.raise_for_status()
    print(f"  webhook: created (id {response.json()['id']})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--webhook-url", required=True)
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
    if not token or not secret:
        print("GITHUB_TOKEN and GITHUB_WEBHOOK_SECRET must be set", file=sys.stderr)
        return 1

    print(f"configuring {args.repo}")
    with client(token) as gh:
        enable_dependabot_alerts(gh, args.repo)
        enable_security_updates(gh, args.repo)
        disable_actions(gh, args.repo)
        ensure_webhook(gh, args.repo, args.webhook_url, secret)

    print(
        "\nremaining manual step:\n"
        f"  https://github.com/{args.repo}/settings/security_analysis\n"
        "  -> Dependabot version updates -> Enable\n"
        "  (no REST endpoint exists for this on a fork)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
