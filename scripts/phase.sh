#!/usr/bin/env bash
# Scrum master tool for moving between phases. Needs the GitHub CLI logged in as a repo admin.
#
#   scripts/phase.sh finish 1   Open the release pull request release/phase-1 -> main (phase_1 with main merged in)
#   scripts/phase.sh start 2    Create phase_2 from main, protect it, and make it the default branch
#
# See docs/CI_CD.md.
set -euo pipefail

REPO="${REPO:-tatsatshah05/NatHacks2026}"
phase_name() {
  case "$1" in
    1) echo "Data Foundation" ;;
    2) echo "Vision and Routing" ;;
    3) echo "Web App and Alerts" ;;
    4) echo "Production and Launch" ;;
  esac
}

usage() { echo "Usage: $0 finish|start <phase number 1-4>" >&2; exit 1; }
[[ $# -eq 2 && "$2" =~ ^[1-4]$ ]] || usage
action="$1"; n="$2"; branch="phase_$n"; name="$(phase_name "$n")"

protect() {
  gh api -X PUT "repos/$REPO/branches/$1/protection" --input - >/dev/null <<JSON
{
  "required_status_checks": {"strict": true, "checks": [{"context": "CI passed"}]},
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true,
    "required_approving_review_count": 1
  },
  "restrictions": null,
  "required_linear_history": false,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
JSON
}

case "$action" in
  finish)
    # The repository merges by squash only, so a phase branch never contains main as an
    # ancestor and a direct phase_N -> main pull request conflicts on every shared file.
    # Release through a branch that is phase_N with main merged in; its tree is phase_N's.
    release="release/phase-$n"
    git fetch -q origin main "$branch"
    git switch -q -c "$release" "origin/$branch" 2>/dev/null || { git switch -q "$release" && git reset -q --hard "origin/$branch"; }
    if ! git merge --no-edit origin/main >/dev/null 2>&1; then
      # Conflicts are always in files both sides changed; the phase branch has the newer version.
      git diff --name-only --diff-filter=U | xargs -r git checkout --ours --
      git diff --name-only --diff-filter=U | xargs -r git add --
      git commit -q --no-edit
    fi
    git push -q -u origin "$release"
    gh pr create -R "$REPO" --base main --head "$release" \
      --title "chore: Release Phase $n ($name) into main" \
      --body "Releases Phase $n ($name) into main after its checkpoint passed. This branch is phase_$n with main merged in; its tree is identical to phase_$n. Merging deploys to production. Next: \`scripts/phase.sh start $((n + 1))\`."
    ;;
  start)
    if gh api "repos/$REPO/branches/$branch" >/dev/null 2>&1; then
      echo "$branch already exists."
    else
      sha="$(gh api "repos/$REPO/git/ref/heads/main" --jq .object.sha)"
      gh api "repos/$REPO/git/refs" -f ref="refs/heads/$branch" -f sha="$sha" >/dev/null
      echo "Created $branch from main ($sha)."
    fi
    protect "$branch"
    echo "Protected $branch: CI passed + approval from a code owner (see .github/CODEOWNERS)."
    gh api -X PATCH "repos/$REPO" -f default_branch="$branch" >/dev/null
    echo "Default branch is now $branch, so new pull requests target it automatically."
    open_prs="$(gh pr list -R "$REPO" --state open --json number,title,baseRefName \
      --jq ".[] | select(.title | test(\"^OP-${n}[0-9]{2}:\")) | select(.baseRefName != \"$branch\") | \"#\\(.number) \\(.title)\"")"
    if [[ -n "$open_prs" ]]; then
      echo "These Phase $n pull requests still target another branch. Change their base to $branch:"
      echo "$open_prs"
    fi
    echo "Tell the team: Phase $n ($name) has started. Branch from $branch and open pull requests into $branch."
    ;;
  *) usage ;;
esac
