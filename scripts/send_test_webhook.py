#!/usr/bin/env python3
"""Send a correctly signed pull_request webhook at a locally running receiver.

Exercises the full path without GitHub. Pair it with DRY_RUN=true to check the wiring
before pointing anything real at it. Standard library only, so it runs on any Python
3.9+ without installing anything.

  python scripts/send_test_webhook.py --url http://localhost:8000/github/webhook
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000/github/webhook")
    parser.add_argument("--repo", default="temp-apache/superset")
    parser.add_argument("--number", type=int, default=1)
    parser.add_argument("--author", default="dependabot[bot]", help="pull request author")
    parser.add_argument("--action", default="opened", choices=["opened", "reopened"])
    parser.add_argument(
        "--title",
        default="chore(deps): bump react-window from 2.3.0 to 2.3.1 in /superset-frontend",
    )
    args = parser.parse_args()

    secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
    if not secret:
        print(
            "GITHUB_WEBHOOK_SECRET must be set (same value as the receiver)", file=sys.stderr
        )
        return 1

    payload = {
        "action": args.action,
        "sender": {"login": args.author},
        "repository": {"full_name": args.repo},
        "pull_request": {
            "user": {"login": args.author},
            "number": args.number,
            "title": args.title,
            "html_url": f"https://github.com/{args.repo}/pull/{args.number}",
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
    }
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    request = urllib.request.Request(
        args.url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "local-test",
            "X-Hub-Signature-256": signature,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            code = response.status
            print(f"{code} {response.read().decode()}")
    except urllib.error.HTTPError as error:
        code = error.code
        print(f"{code} {error.read().decode()}")
    except urllib.error.URLError as error:
        print(f"could not reach {args.url}: {error.reason}", file=sys.stderr)
        return 1

    return 0 if code in {202, 204} else 1


if __name__ == "__main__":
    raise SystemExit(main())
