#!/bin/bash
set -euo pipefail

echo "== Repository =="
printf "Path:   %s\n" "$PWD"
printf "Branch: %s\n" "$(git branch --show-current)"
printf "Remote: %s\n" "$(git remote get-url origin 2>/dev/null || echo 'none')"

echo
echo "== Working Tree =="
git status --short

echo
echo "== Local Branches =="
git branch -vv

echo
echo "== Recent Commits =="
git log --oneline --decorate -5
