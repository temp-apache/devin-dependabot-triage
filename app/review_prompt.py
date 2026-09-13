"""The review prompt.

This is the most important file in the project. Everything else is plumbing; this is
where the difference between "a bot with a semver rule" and "a teammate" is expressed.

The instruction that matters is step 3: read the *comments* on a version constraint, not
just the constraint. A pin that permits an upgrade is not the same as a pin that endorses
it, and that distinction is only ever written in prose.
"""

from __future__ import annotations

from textwrap import dedent

from app.models import Decision

_CORE = """\
You are reviewing an automated dependency-bump pull request as the repository's
maintainer. Decide whether it should be merged. Work from the repository, not from
general knowledge about the packages involved.

Pull request: {pr_url}
Title: {pr_title}
Repository: {repo}

Steps:

1. Read the diff. Establish which package moves, from which version to which, and
   whether the change is confined to lockfiles or also edits a manifest.

2. If the pull request cites a security advisory, confirm it is real and applies here:
   that the pinned version is in the affected range and the proposed version is at or
   above the first fixed release. An advisory with no fixed release cannot be resolved
   by a bump, however severe it is. Most dependency bumps cite none, and that is not by
   itself a reason to withhold approval — routine upgrades are the normal case.

3. Check the repository's own constraints, AND THE COMMENTS ON THEM. Look in
   pyproject.toml, requirements/*.in, requirements/*.txt, package.json (including the
   "overrides" and "resolutions" blocks), and any constraint files. Two distinct
   questions:
     a. Does the constraint permit the new version at all? If an override pins a
        transitive dependency below the bump, the change will be undone on the next
        install and merging it accomplishes nothing.
     b. Does the comment on that constraint tell you why it exists? A cap that someone
        wrote "not yet validated" next to is not the same as a cap that happens to
        allow the version. Treat the comment as an instruction from a colleague.

4. Assess blast radius. Is the package used in shipped product code, in the build, or
   only in tests and tooling? Grep for direct imports rather than assuming.

5. If the change could plausibly break something, run it. Install the proposed version
   and run the affected tests, or the narrowest subset that would reveal a break. Do not
   reason about whether a major version bump is safe when you can find out. If you run
   tests, quote real output in your evidence.

Decide:

- "{approve}" if the repository's own constraints permit the bump to stick and you have
  either verified nothing breaks or established that nothing could. Where an advisory is
  cited, it must also apply and be resolved by the bump.
- "{decline}" if the bump cannot take effect, breaks something, or fails to resolve the
  advisory it cites. Say precisely what, with evidence.
- "{escalate}" if the right answer depends on a judgement the maintainers have to make
  (a deliberate trade-off, a pin with a reason you cannot verify, a failure you cannot
  attribute).

Set "confidence" to your genuine confidence in the decision. Low confidence on an
approval is itself a reason to escalate instead.

Populate "evidence" with concrete, checkable items: file:line references, the exact
constraint text, command output. Not restatements of the advisory.
"""

_DEVIN_MERGES = """\

Acting on your decision:

Devin Review verdict on this PR: {review_status}
The pull request was opened at {opened_at}.
The triage service received the webhook at {received_at}.

If, and only if, ALL of the following hold, approve the pull request with your summary
as the review body and then merge it:
  - your decision is "{approve}"
  - your confidence is at least {min_confidence}
{review_gate}
End the review body with a line reading "Reached <duration> after the pull request
opened, <duration> after this service saw it.", measured from the two timestamps
above to the moment you post it. The second number is the one that is comparable
across runs: it excludes however long the pull request sat before anything picked
it up.

Set "merged_by_devin" to true if you merged, false otherwise.

If any condition fails, do not approve and do not merge. Post your findings as a regular
pull request comment instead, and say plainly which condition stopped you.
{escalation}"""

_ESCALATION = """
When you do not merge, end the comment with this line exactly, which stands in for a
notification that is not wired up here:

> **Escalation:** this would notify `{channel}` in Slack for human review _(mocked — no
> Slack connection in this demo)_.
"""

_SERVICE_MERGES = """\

Do not approve, merge, or comment on the pull request. Return your decision as
structured output only; the calling service acts on it.
"""


def build_prompt(
    *,
    repo: str,
    pr_url: str,
    pr_title: str,
    merge_actor: str,
    min_confidence: float,
    review_status: str,
    escalation_channel: str = "",
    opened_at: str = "",
    received_at: str = "",
) -> str:
    body = _CORE.format(
        pr_url=pr_url,
        pr_title=pr_title,
        repo=repo,
        approve=Decision.APPROVE_AND_MERGE.value,
        decline=Decision.DECLINE.value,
        escalate=Decision.ESCALATE.value,
    )
    if merge_actor == "devin":
        # "not_required" means the operator turned the review gate off; treating it as a
        # failed gate would block every merge while looking like a verdict.
        gate_off = review_status == "not_required"
        review_gate = "" if gate_off else '  - the Devin Review verdict above is "passed"\n'
        body += _DEVIN_MERGES.format(
            review_gate=review_gate,
            review_status=(
                "not required for this repository, so it is not a condition below"
                if gate_off
                else review_status
            ),
            opened_at=opened_at,
            received_at=received_at or "unknown",
            approve=Decision.APPROVE_AND_MERGE.value,
            min_confidence=min_confidence,
            escalation=(
                _ESCALATION.format(channel=escalation_channel) if escalation_channel else ""
            ),
        )
    else:
        body += _SERVICE_MERGES
    return dedent(body)
