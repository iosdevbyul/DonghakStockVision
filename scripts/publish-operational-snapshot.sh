#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo 'Usage: publish-operational-snapshot.sh SOURCE_ABSOLUTE_PATH DESTINATION_ABSOLUTE_PATH [EXPECTED_TICKERS]' >&2
  exit 2
}

[[ $# -eq 2 || $# -eq 3 ]] || usage
source_db=$1
snapshot=$2
expected_tickers=${3-}

[[ "$source_db" = /* && "$snapshot" = /* ]] || usage
[[ "$snapshot" == *.snapshot.sqlite3 ]] || { echo 'Destination must end in .snapshot.sqlite3' >&2; exit 2; }
[[ -f "$source_db" && ! -L "$source_db" ]] || { echo 'Source must be a regular file' >&2; exit 2; }
[[ ! -e "$snapshot" && ! -L "$snapshot" ]] || { echo 'Snapshot destination already exists' >&2; exit 2; }
if [[ -n "$expected_tickers" && ! "$expected_tickers" =~ ^[1-9][0-9]*$ ]]; then
  echo 'Expected ticker count must be a positive integer' >&2
  exit 2
fi
command -v dsv >/dev/null || { echo 'dsv CLI unavailable' >&2; exit 2; }
command -v dsv-operational-verify >/dev/null || { echo 'dsv-operational-verify CLI unavailable' >&2; exit 2; }
command -v python3 >/dev/null || { echo 'python3 unavailable' >&2; exit 2; }

# Established SQLite backup uses a consistent read-only source connection.
backup_json=$(dsv market-storage backup --source "$source_db" --destination "$snapshot")
sha=$(printf '%s\n' "$backup_json" | python3 -c '
import json, sys
value = json.load(sys.stdin)
assert value["action"] == "backup"
sha = value["sha256"]
assert isinstance(sha, str) and len(sha) == 64
assert all(c in "0123456789abcdef" for c in sha)
print(sha)
')

args=(--snapshot "$snapshot" --sha256 "$sha")
if [[ -n "$expected_tickers" ]]; then
  args+=(--expected-tickers "$expected_tickers")
fi
dsv-operational-verify "${args[@]}"
