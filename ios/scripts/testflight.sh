#!/bin/bash
# Archive a Release build and upload it to App Store Connect for TestFlight.
# Uses the Apple ID signed in to Xcode (Settings → Accounts). The build number is the UTC time, so every upload is new.
set -euo pipefail
cd "$(dirname "$0")/.."
BUILD=$(date -u +%Y%m%d%H%M)
ARCHIVE=build/Agents-$BUILD.xcarchive
LOG=build/testflight-$BUILD.log
mkdir -p build
xcodegen generate >/dev/null
echo "Archiving build ${BUILD}…"
if ! xcodebuild -project Agents.xcodeproj -scheme Agents -configuration Release -destination 'generic/platform=iOS' \
    -archivePath "$ARCHIVE" -allowProvisioningUpdates CURRENT_PROJECT_VERSION="$BUILD" archive >"$LOG" 2>&1; then
  grep -E 'error:' "$LOG" || tail -20 "$LOG"
  exit 1
fi
echo "Uploading…"
if ! xcodebuild -exportArchive -archivePath "$ARCHIVE" -exportOptionsPlist scripts/ExportOptions.plist -exportPath build/export \
    -allowProvisioningUpdates >>"$LOG" 2>&1; then
  grep -A3 -E 'error' "$LOG" | tail -12
  exit 1
fi
echo "Uploaded build ${BUILD}. It shows in App Store Connect → TestFlight once Apple finishes processing (usually 5–15 minutes)."
