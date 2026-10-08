#!/bin/bash
set -euo pipefail

BRANCH="$(git branch --show-current)"

if [ -z "$BRANCH" ]; then
  echo "Could not determine current branch."
  exit 1
fi

if [ "$BRANCH" = "main" ] || [ "$BRANCH" = "dev" ]; then
  echo "Refusing to create a PR from protected branch: $BRANCH"
  exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "Working tree has uncommitted changes. Commit them first."
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI (gh) is required."
  exit 1
fi

git push -u origin "$BRANCH"

if gh pr view "$BRANCH" >/dev/null 2>&1; then
  echo "PR already exists for $BRANCH"
  gh pr view "$BRANCH" --web
else
  gh pr create --fill --base main --head "$BRANCH"
fi
