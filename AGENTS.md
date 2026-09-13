# Working in this repository

A webhook receiver that hands Dependabot pull requests to a Devin session and acts on
the decision. Roughly 400 lines. Read `README.md` first for what it is for.

## What this repository is demonstrating

The point is not the plumbing. It is that a dependency bump can satisfy every declared
version constraint and still be the wrong thing to merge, because the reason for the
constraint is written in a comment next to it. Any change that makes the review shallower
— trusting advisory metadata, deciding by update type, skipping the test run — removes
the reason this exists.

So: **`app/review_prompt.py` is the important file.** Treat it as product code, not as a
string. Do not soften step 3 (read the comments on constraints) or step 5 (run the tests
rather than reasoning about whether a major bump is safe).

## Conventions

- Python 3.12, type hints everywhere, `mypy --strict` clean.
- `ruff check` and `ruff format`, line length 95.
- `httpx` for HTTP, async throughout. No `requests`.
- Pydantic for anything crossing a boundary: settings, the Devin output schema.
- No `Any`, no `getattr`/`setattr` to dodge a type. If a shape is unclear, read the API
  docs and write the type.

Run all three before committing:

```bash
.venv/bin/ruff check . && .venv/bin/mypy app scripts && .venv/bin/python -m pytest -q
```

## Invariants — do not break these

**Verify the signature before parsing the body.** `verify_signature` in `app/github.py`
uses `hmac.compare_digest`. Never compare with `==`, never parse JSON first.

**The webhook endpoint must return within ten seconds.** GitHub's limit. All real work
goes in a `BackgroundTasks` task. Do not await a Devin session inside the request
handler.

**Sessions are created with `idempotent: true`.** GitHub redelivers webhooks. Without
this a redelivery means two reviews and possibly two merges.

**The merge gate is three conditions, all required:** decision is `approve_and_merge`,
confidence at or above `MIN_CONFIDENCE`, Devin Review verdict is `passed`. It is
expressed twice on purpose — in the prompt for `MERGE_ACTOR=devin`, in
`apply_decision` for `MERGE_ACTOR=service`. Change one, change the other, and update
`tests/test_decision.py`.

**Failing closed means commenting, never merging.** Every path that does not merge must
still leave a comment saying which condition stopped it. Silence looks like a crash.

**`REVIEW_OUTPUT_SCHEMA` and `ReviewResult` must agree.** `test_schema_matches_the_model`
enforces it. If you add a field, add it to both.

## Testing

Tests do not touch the network. GitHub is stubbed with `respx`; the Devin client is
injected. Keep it that way — a test that needs a key is a test nobody runs.

Cover a new decision path in `tests/test_decision.py` with its GitHub calls asserted:
the interesting assertion is usually that `merge` was *not* called.

## Things that will waste your time

- Dependabot **version updates** on a fork cannot be enabled through the API. There is no
  endpoint. `scripts/setup_repo.py` does the other four and prints the manual step.
- Superset's `.github/dependabot.yml` points `pip` at `/`, which never reads
  `requirements/development.txt`. Without a `/requirements` entry the `pytest` PR never
  opens.
- Devin Review only runs in repositories connected to the Devin organisation. In an
  unconnected repository the verdict is `absent` and, with the default settings, nothing
  ever merges. That is correct behaviour, not a bug.
- Superset runs 55 workflows per PR. Keep Actions disabled on the fork.

## Out of scope

This service never modifies the target repository's source. It reviews, comments,
approves, merges. Anything that writes code belongs in the Devin session, not here.
