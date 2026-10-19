#!/bin/sh
# Build, install and launch Agents on the first paired physical iPhone.
set -e
cd "$(dirname "$0")/.."
DEVICE=$(xcrun devicectl list devices 2>/dev/null | awk '/physical/ && /available/ {for (i = 1; i <= NF; i++) if ($i ~ /^[0-9A-F]{8}-[0-9A-F]{16}$/) {print $i; exit}}')
[ -n "$DEVICE" ] || { echo "No available iPhone (connected, unlocked, Developer Mode on)."; exit 1; }
xcodegen generate >/dev/null
xcodebuild -project Agents.xcodeproj -scheme Agents -destination "id=$DEVICE" -derivedDataPath build/DerivedData -allowProvisioningUpdates build | grep -E 'error:|BUILD (SUCCEEDED|FAILED)'
xcrun devicectl device install app --device "$DEVICE" build/DerivedData/Build/Products/Debug-iphoneos/Agents.app
xcrun devicectl device process launch --device "$DEVICE" com.j4cku.agents
