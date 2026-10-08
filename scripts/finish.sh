#!/bin/bash
set -euo pipefail

SCHEME="${1:-$(basename "$PWD")}" 
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

BRANCH="$(git branch --show-current)"

if [ "$BRANCH" = "main" ] || [ "$BRANCH" = "dev" ]; then
  echo "Refusing to finish work directly on protected branch: $BRANCH"
  exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "Working tree has uncommitted changes. Commit them first."
  exit 1
fi

"$SCRIPT_DIR/test.sh" "$SCHEME"
"$SCRIPT_DIR/push-pr.sh"
