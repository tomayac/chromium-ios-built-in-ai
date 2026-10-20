#!/bin/sh
# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
#
# Installs a simulator build of Chromium, opens the test page in it with
# `?autorun`, and prints the results the page reports.
#
#   ios-tools/run-simulator.sh <chromium/src>/out/ios-sim/Chromium.app
#
# Needs `axe` (brew install cameroncooke/axe/axe) to accept the "Open in
# Chromium?" prompt, and DEVELOPER_DIR pointing at Xcode if xcode-select does
# not.

set -eu
APP=${1:?usage: $0 <path to Chromium.app>}
BUNDLE_ID=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP/Info.plist")
DEVICE_NAME="BuiltInAI iPhone 17 Pro"
PORT=8000
HERE=$(cd "$(dirname "$0")" && pwd)
RESULTS=$(mktemp -d)/results.json

UDID=$(xcrun simctl list devices -j | python3 -c '
import json, sys
name = sys.argv[1]
for devices in json.load(sys.stdin)["devices"].values():
    for d in devices:
        if d["name"] == name and d["isAvailable"]:
            print(d["udid"]); break' "$DEVICE_NAME" | head -1)
if [ -z "$UDID" ]; then
  RUNTIME=$(xcrun simctl list runtimes -j | python3 -c '
import json, sys
ios = [r for r in json.load(sys.stdin)["runtimes"] if r["platform"] == "iOS" and r["isAvailable"]]
print(ios[-1]["identifier"])')
  UDID=$(xcrun simctl create "$DEVICE_NAME" \
    com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro "$RUNTIME")
fi
xcrun simctl boot "$UDID" 2>/dev/null || true
xcrun simctl bootstatus "$UDID" -b >/dev/null

xcrun simctl terminate "$UDID" "$BUNDLE_ID" 2>/dev/null || true
xcrun simctl install "$UDID" "$APP"
xcrun simctl spawn "$UDID" defaults write "$BUNDLE_ID" FirstRunForceDisabled -bool YES

# A server left over on the port would take the results.
if lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT is in use; stop what listens there first." >&2
  exit 1
fi
python3 "$HERE/../bundle/test/serve.py" --port "$PORT" --results "$RESULTS" \
  >/dev/null 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null' EXIT

xcrun simctl launch "$UDID" "$BUNDLE_ID" >/dev/null
sleep 6
# Exactly one URL: Chromium asserts when a second arrives before the first is
# handled.
xcrun simctl openurl "$UDID" "chromium://localhost:$PORT/index.html?autorun"
sleep 2
axe tap --label Open --udid "$UDID" >/dev/null 2>&1 || true
# Taps on the prompt do not always register. Return picks its default button,
# Open, and does nothing once the prompt is gone.
sleep 1
axe key 40 --udid "$UDID" >/dev/null 2>&1 || true

for _ in $(seq 1 100); do
  [ -s "$RESULTS" ] && break
  sleep 3
done
if [ ! -s "$RESULTS" ]; then
  echo "No results after 5 minutes." >&2
  exit 1
fi
python3 -c '
import json, sys
results = json.load(open(sys.argv[1]))
print(results["env"])
for row in results["rows"]:
    print("%s: %s" % (row["check"], row["detail"]))
sys.exit(any(r["check"].startswith("FAIL") for r in results["rows"]))' "$RESULTS"
