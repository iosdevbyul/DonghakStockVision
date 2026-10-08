#!/bin/bash
set -euo pipefail

if [ -n "$(git status --porcelain)" ]; then
  echo "Working tree has uncommitted changes. Commit or stash them first."
  exit 1
fi

git fetch --prune origin
git switch main
git pull --ff-only origin main

CURRENT="$(git branch --show-current)"

if [ "$CURRENT" != "main" ]; then
  echo "Failed to switch to main."
  exit 1
fi

MERGED_BRANCHES=$(git branch --merged main \
  | sed 's/^..//' \
  | grep -Ev '^(main|dev)$' || true)

if [ -z "$MERGED_BRANCHES" ]; then
  echo "No merged local branches to delete."
  exit 0
fi

echo "Merged local branches:"
echo "$MERGED_BRANCHES"

while IFS= read -r branch; do
  [ -z "$branch" ] && continue
  git branch -d "$branch"
done <<< "$MERGED_BRANCHES"

echo "Local merged branch cleanup complete."
