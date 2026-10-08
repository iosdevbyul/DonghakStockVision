#!/bin/bash
set -euo pipefail

SCHEME="${1:-$(basename "$PWD")}" 
DESTINATION="${DESTINATION:-platform=iOS Simulator,name=iPhone 16}"
CONFIGURATION="${CONFIGURATION:-Debug}"

echo "[test] scheme: $SCHEME"
echo "[test] destination: $DESTINATION"
echo "[test] configuration: $CONFIGURATION"

xcodebuild \
  -scheme "$SCHEME" \
  -destination "$DESTINATION" \
  -configuration "$CONFIGURATION" \
  CODE_SIGNING_ALLOWED=NO \
  test
