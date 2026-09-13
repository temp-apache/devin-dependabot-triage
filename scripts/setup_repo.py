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

Standard library only, so it runs under any Python 3.9+ with nothing installed.

Usage:
  GITHUB_TOKEN=... GITHUB_WEBHOOK_SECRET=... \
    python3 scripts/setup_repo.py --repo owner/name --webhook-url https://smee.io/abc123
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

API = "https://api.github.com"
HEADERS_VERSION = "2022-11-28"


def call(token: str, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    request = urllib.request.Request(
        f"{API}{path}",
        method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": HEADERS_VERSION,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
    return json.loads(body) if body else None


def enable_dependabot_alerts(token: str, repo: str) -> None:
    call(token, "PUT", f"/repos/{repo}/vulnerability-alerts")
    print("  dependabot alerts: enabled")


def enable_security_updates(token: str, repo: str) -> None:
    call(token, "PUT", f"/repos/{repo}/automated-security-fixes")
    print("  dependabot security updates: enabled")


def disable_actions(token: str, repo: str) -> None:
    """Superset carries 55 workflows; every PR would drag the full matrix along."""
    call(token, "PUT", f"/repos/{repo}/actions/permissions", {"enabled": False})
    print("  actions: disabled")


def ensure_webhook(token: str, repo: str, url: str, secret: str) -> None:
    for hook in call(token, "GET", f"/repos/{repo}/hooks") or []:
        if hook.get("config", {}).get("url") == url:
            print(f"  webhook: already present (id {hook['id']})")
            return

    created = call(
        token,
        "POST",
        f"/repos/{repo}/hooks",
        {
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
    print(f"  webhook: created (id {created['id']})")


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
    try:
        enable_dependabot_alerts(token, args.repo)
        enable_security_updates(token, args.repo)
        disable_actions(token, args.repo)
        ensure_webhook(token, args.repo, args.webhook_url, secret)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        print(f"\nHTTP {exc.code} from {exc.url}\n{detail}", file=sys.stderr)
        return 1

    print(
        "\nremaining manual step:\n"
        f"  https://github.com/{args.repo}/settings/security_analysis\n"
        "  -> Dependabot version updates -> Enable\n"
        "  (no REST endpoint exists for this on a fork)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
