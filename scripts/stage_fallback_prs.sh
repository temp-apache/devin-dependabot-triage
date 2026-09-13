#!/usr/bin/env bash
# Pre-stage the two demo pull requests by hand.
#
# Dependabot's scheduler is not something you control to the minute. These are the same
# two diffs it would produce, on Dependabot-style branch names, so if nothing has opened
# by the time you present, you close and reopen one of these and the identical webhook
# path runs.
#
# Usage:  ./scripts/stage_fallback_prs.sh /path/to/superset-checkout

set -euo pipefail

REPO_DIR="${1:?usage: $0 /path/to/superset-checkout}"
cd "$REPO_DIR"

BASE="$(git symbolic-ref --short HEAD)"

stage() {
  local branch="$1" title="$2" body="$3"
  shift 3
  git checkout -q "$BASE"
  git checkout -q -b "$branch"
  "$@"
  git commit -qam "$title"
  git push -q -u origin "$branch"
  gh pr create --base "$BASE" --head "$branch" --title "$title" --body "$body"
  git checkout -q "$BASE"
}

bump_js_yaml() {
  # HIGH, CVE-2026-84375. Patch bump, and the overrides block in package.json
  # already admits it -- the case that should be approved and merged.
  python3 - <<'PY'
import re, pathlib
p = pathlib.Path("superset-frontend/package-lock.json")
text = p.read_text()
text = text.replace('"js-yaml": "4.3.1"', '"js-yaml": "4.3.2"')
text = re.sub(r'("node_modules/[^"]*js-yaml",?\s*\{[^}]*?"version": ")4\.3\.1(")', r'\g<1>4.3.2\g<2>', text)
p.write_text(text)
PY
}

bump_pytest() {
  # MODERATE, CVE-2025-71176. Only fixed in 9.0.3 -- two majors up from the pin, but
  # still inside the pyproject.toml `pytest<10.0.0` cap. The case that should not be
  # merged on a rule.
  sed -i 's/^pytest==7\.4\.4$/pytest==9.0.3/' requirements/development.txt
}

stage "dependabot/npm_and_yarn/superset-frontend/js-yaml-4.3.2" \
  "chore(deps): bump js-yaml from 4.3.1 to 4.3.2 in /superset-frontend" \
  "Bumps js-yaml from 4.3.1 to 4.3.2. Fixes GHSA-2883-xcg3-v3hh (CVE-2026-84375, high)." \
  bump_js_yaml

stage "dependabot/pip/requirements/pytest-9.0.3" \
  "chore(deps): bump pytest from 7.4.4 to 9.0.3 in /requirements" \
  "Bumps pytest from 7.4.4 to 9.0.3. Fixes GHSA-6w46-j5rx-g56g (CVE-2025-71176, moderate)." \
  bump_pytest

echo "staged both pull requests against $BASE"
