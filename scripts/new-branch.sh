#!/bin/bash
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "Usage: $0 <branch-name>"
  echo "Example: $0 feat/workout-report"
  exit 1
fi

BRANCH="$1"
CURRENT="$(git branch --show-current)"

if [ -n "$(git status --porcelain)" ]; then
  echo "Working tree has uncommitted changes. Commit or stash them first."
  exit 1
fi

git fetch --prune origin

git switch main
git pull --ff-only origin main

if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
  echo "Local branch already exists: $BRANCH"
  exit 1
fi

git switch -c "$BRANCH"

echo "Created and switched to: $BRANCH"
