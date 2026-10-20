#!/bin/sh
# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
#
# Turns a sideload build of Chromium (ios_chrome_sideload_entitlements = true)
# into an .ipa for AltStore and SideStore, and adds it to the AltStore source.
#
#   ios-tools/make-altstore-release.sh <Chromium.app> <out dir> [release notes]
#
# Writes <out dir>/Chromium-<version>-<build>.ipa and updates
# <out dir>/source.json, which lists the release under the download URL that
# `RELEASE_URL_BASE` names, a GitHub release by default.
#
# The .ipa is signed ad hoc: AltStore re-signs it with the tester's Apple ID,
# and reads the entitlements it asks for from this signature. Nothing of the
# development signing stays in it, neither the certificate nor the
# provisioning profile, which lists the developer's devices.

set -eu
APP=${1:?usage: $0 <Chromium.app> <out dir> [release notes]}
OUT=${2:?usage: $0 <Chromium.app> <out dir> [release notes]}
NOTES=${3:-}
REPO=tomayac/chromium-ios-built-in-ai
PAGES=https://tomayac.github.io/chromium-ios-built-in-ai/altstore

plist() { /usr/libexec/PlistBuddy -c "Print :$1" "$2"; }

VERSION=$(plist CFBundleShortVersionString "$APP/Info.plist")
BUILD=$(plist CFBundleVersion "$APP/Info.plist")
TAG="v$BUILD"
RELEASE_URL_BASE=${RELEASE_URL_BASE:-https://github.com/$REPO/releases/download/$TAG}
IPA_NAME="Chromium-$BUILD.ipa"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/Payload" "$OUT"
ditto "$APP" "$WORK/Payload/Chromium.app"
APP_COPY="$WORK/Payload/Chromium.app"

# What the build was signed with decides what AltStore asks for, minus what
# identifies the developer's team or only makes sense for development.
codesign -d --entitlements :- "$APP" 2>/dev/null >"$WORK/signed.plist"
python3 - "$WORK/signed.plist" "$WORK/entitlements.plist" <<'EOF'
import plistlib, sys
entitlements = plistlib.load(open(sys.argv[1], 'rb'))
for key in ('application-identifier', 'com.apple.developer.team-identifier',
            'keychain-access-groups', 'get-task-allow'):
    entitlements.pop(key, None)
plistlib.dump(entitlements, open(sys.argv[2], 'wb'))
print('Entitlements:', ', '.join(sorted(entitlements)) or 'none')
EOF

rm -f "$APP_COPY/embedded.mobileprovision"
# Inside out: nested code first, the app last.
find "$APP_COPY" -depth \( -name '*.framework' -o -name '*.dylib' -o -name '*.appex' \) \
  -print | while read -r code; do
  codesign --force --sign - --timestamp=none "$code" 2>/dev/null
done
codesign --force --sign - --timestamp=none \
  --entitlements "$WORK/entitlements.plist" "$APP_COPY" 2>/dev/null
codesign --verify --deep --strict "$APP_COPY"

(cd "$WORK" && zip -qry "$IPA_NAME" Payload)
mv "$WORK/$IPA_NAME" "$OUT/"
SIZE=$(stat -f %z "$OUT/$IPA_NAME")
echo "Wrote $OUT/$IPA_NAME ($SIZE bytes)."

python3 - "$APP_COPY/Info.plist" "$WORK/entitlements.plist" "$OUT/source.json" \
  "$VERSION" "$BUILD" "$RELEASE_URL_BASE/$IPA_NAME" "$SIZE" "$PAGES" "$NOTES" <<'EOF'
import datetime, json, os, plistlib, sys
(info_path, entitlements_path, source_path, version, build, url, size, pages,
 notes) = sys.argv[1:]
info = plistlib.load(open(info_path, 'rb'))
# The usage descriptions in Info.plist are resource IDs, which the English
# InfoPlist.strings turns into the text iOS shows.
strings_path = os.path.join(os.path.dirname(info_path), 'en.lproj',
                            'InfoPlist.strings')
strings = (plistlib.load(open(strings_path, 'rb'))
           if os.path.exists(strings_path) else {})
entitlements = sorted(plistlib.load(open(entitlements_path, 'rb')))
privacy = {k: strings.get(k, v) for k, v in sorted(info.items())
           if k.startswith('NS') and k.endswith('UsageDescription')}
bundle_id = info['CFBundleIdentifier']

if os.path.exists(source_path):
    source = json.load(open(source_path))
else:
    source = {
        'name': 'Chromium with Built-in AI',
        'subtitle': 'The built-in AI APIs on iOS, on device',
        'description': (
            'Chromium for iOS with the built-in AI APIs (Prompt, Summarizer, '
            'Writer, Rewriter, Translator, and Language Detector) backed by '
            'Apple Foundation Models, or by Gemma 4 on devices without Apple '
            'Intelligence.'),
        'iconURL': f'{pages}/icon.png',
        'website': 'https://github.com/tomayac/chromium-ios-built-in-ai',
        'tintColor': '#1A73E8',
        'apps': [],
        'news': [],
    }
apps = [a for a in source['apps'] if a['bundleIdentifier'] == bundle_id]
if apps:
    app = apps[0]
else:
    app = {
        'name': 'Chromium AI',
        'bundleIdentifier': bundle_id,
        'developerName': 'Thomas Steiner',
        'subtitle': 'The built-in AI APIs on every page',
        'localizedDescription': (
            'An experimental build of Chromium for iOS where every web page can '
            'use LanguageModel (the Prompt API), Summarizer, Writer, Rewriter, '
            'Translator, and LanguageDetector, with inference on the device.\n\n'
            'On devices with Apple Intelligence the APIs use Apple Foundation '
            'Models. Elsewhere they use Gemma 4 E2B, downloaded once on first '
            'use (2 to 2.6 GB) and shared by all sites.\n\n'
            'This is a personal experiment, and not an official Google or '
            'Chromium product.'),
        'iconURL': f'{pages}/icon.png',
        'tintColor': '#1A73E8',
        'category': 'developer',
        'screenshots': [f'{pages}/screenshot-{n}.png' for n in (1, 2)],
        'versions': [],
    }
    source['apps'].append(app)
app['appPermissions'] = {'entitlements': entitlements, 'privacy': privacy}
app['versions'] = [v for v in app['versions']
                   if (v['version'], v['buildVersion']) != (version, build)]
app['versions'].insert(0, {
    'version': version,
    'buildVersion': build,
    'date': datetime.date.today().isoformat(),
    'localizedDescription': notes,
    'downloadURL': url,
    'size': int(size),
    'minOSVersion': info.get('MinimumOSVersion', '18.0'),
})
with open(source_path, 'w') as f:
    json.dump(source, f, indent=2, ensure_ascii=False)
    f.write('\n')
print(f'Updated {source_path}: {app["name"]} {version} ({build}), '
      f'{len(entitlements)} entitlements, {len(privacy)} privacy keys.')
EOF
echo "Tag for the GitHub release: $TAG"
