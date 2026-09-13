# Demo runbook: cold start on a fresh fork

Ordered so the clock only starts once everything downstream is listening. The elapsed
line in Devin's comment is measured from GitHub's own `created_at`, so a pull request
that opens before the receiver is up reads as an hour old no matter how fast the
triage was.

## 1. Fork

Delete the old fork, then fork `apache/superset` into `temp-apache`. Keep only the
default branch.

## 2. Commit the curated config first

Replace `.github/dependabot.yml` with [`dependabot.yml`](dependabot.yml) from this
directory and commit it to the fork's default branch. Superset's own config opens
thirty-odd pull requests on the first run; this opens three.

Do this **before** step 4. Dependabot reads the config that exists when version
updates are switched on.

## 3. Repository settings

```bash
export GITHUB_TOKEN=<pat with webhooks: write>
export GITHUB_WEBHOOK_SECRET=<same value as .env>
python3 scripts/setup_repo.py --repo temp-apache/superset --webhook-url <smee channel>
```

Alerts, security updates, Actions off, webhook. If the token lacks the Webhooks
permission the first three still apply and you add the webhook by hand at
Settings → Webhooks → Add webhook (content type `application/json`, the same secret,
**Pull requests** only).

Actions off matters: Superset runs 55 workflows per pull request otherwise.

## 4. Connect the fork to your Devin organisation

Devin Review is connected per repository and a re-fork is a new repository. Without it
every verdict reads `absent` and the merge gate holds everything.

## 5. Enable Dependabot version updates

Settings → Advanced Security → **Dependabot version updates** → Enable. This switch has
no REST endpoint and forks never inherit it from a committed config.

Enabling it triggers the first check, so have the receiver already running.

## 6. Receiver up, then trigger

```bash
docker compose up --build
```

Wait for `Connected https://smee.io/...`, then Insights → Dependency graph →
Dependabot → **Check for updates** on the entry you want.

## What the log should show

```
POST /github/webhook 202 Accepted
no Devin Review on #1, requesting one
Devin Review on https://github.com/... is completed
PR #1 -> session https://app.devin.ai/sessions/...
PR #1: decline (confidence 0.95), merged_by_devin=False, 2m 11s since it opened, 2m 4s since the webhook arrived
```

The second duration is the one to read out: it excludes however long the pull request
sat before anything picked it up.
