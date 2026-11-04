#!/usr/bin/env python3
# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Signs and releases a build that `catch_up.py` made, on a GitHub-hosted Mac.

`catch_up.py` builds the app without signing it, since the Mac it runs on may
not allow `codesign`, and attaches it to a draft GitHub release. This takes it
from there:

1. sets up the signing keychain with the Apple Distribution identity of the
   `SIGNING_P12` secret, and the App Store Connect API key and
   `testflight.json` from secrets,
2. signs the app ad hoc with the entitlements of a sideload build,
3. releases it on TestFlight and submits it for App Store review
   (`testflight.py --release --app-store`),
4. packages it for AltStore Classic, publishes the GitHub release with the
   .ipa in place of the unsigned app, and
5. commits and pushes the AltStore source and `released.json`.

Usage (in the release workflow):
  ci_release.py --chrome 157.0.8103.37 --tag v157.8103.37
"""

import argparse
import base64
import json
import os
import plistlib
import subprocess
import sys
import tempfile

import asc_provision

TOOLS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TOOLS)

# App Store Connect: the API key's IDs and the TestFlight app.
ASC_KEY_ID = 'B78MTCSY2V'
ASC_ISSUER_ID = '69a6de6f-b54e-47e3-e053-5b8c7c11a4d1'
TESTFLIGHT_BUNDLE_ID = 'com.tomayac.chromium.built-in-ai-browser.ios.dev'
UNSIGNED_ASSET = 'Chromium-unsigned.zip'


def run(*cmd, cwd=None):
  print('+', ' '.join(cmd), flush=True)
  subprocess.run(cmd, cwd=cwd, check=True)


def notes(chrome):
  return f'Catches up with Chrome {chrome}.'


def secrets():
  """Puts the API key, testflight.json, and the signing identity in place."""
  keys = os.path.expanduser('~/.appstoreconnect/private_keys')
  os.makedirs(keys, exist_ok=True)
  with open(os.path.join(keys, f'AuthKey_{ASC_KEY_ID}.p8'), 'w') as f:
    f.write(os.environ['ASC_API_KEY'])
  with open(os.path.join(TOOLS, 'testflight.json'), 'w') as f:
    f.write(os.environ['TESTFLIGHT_JSON'])
  password = asc_provision.ensure_keychain()
  with tempfile.NamedTemporaryFile(suffix='.p12') as p12:
    p12.write(base64.b64decode(os.environ['SIGNING_P12']))
    p12.flush()
    run('security', 'import', p12.name, '-k', asc_provision.KEYCHAIN,
        '-P', os.environ['SIGNING_P12_PASSWORD'],
        '-T', '/usr/bin/codesign', '-T', '/usr/bin/security')
  # Lets codesign use the key without asking.
  run('security', 'set-key-partition-list', '-S', 'apple-tool:,apple:,codesign:',
      '-s', '-k', password, asc_provision.KEYCHAIN)
  if not asc_provision.find_identity('DISTRIBUTION'):
    sys.exit('SIGNING_P12 has no valid Apple Distribution identity.')


def unpack(tag, work):
  run('gh', 'release', 'download', tag, '--pattern', UNSIGNED_ASSET,
      '--dir', work, cwd=REPO)
  run('ditto', '-x', '-k', os.path.join(work, UNSIGNED_ASSET), work)
  return os.path.join(work, 'Chromium.app'), os.path.join(work, 'release.entitlements')


def sign_ad_hoc(app, entitlements):
  """Signs the app ad hoc, with the entitlements of a sideload build.

  `make-altstore-release.sh` reads the entitlements from this signature, and
  `testflight.py` re-signs the app with the App Store profile's own.
  """
  nested = []
  for root, dirs, _ in os.walk(app):
    nested += [os.path.join(root, d) for d in dirs
               if d.endswith(('.framework', '.appex'))]
  # Inside out: nested code first, the app last.
  for code in sorted(nested, key=len, reverse=True):
    run('codesign', '--force', '--sign', '-', '--timestamp=none', code)
  run('codesign', '--force', '--sign', '-', '--timestamp=none',
      '--entitlements', entitlements, app)
  run('codesign', '--verify', '--deep', '--strict', app)


def release(app, chrome, tag, work):
  run(os.path.join(TOOLS, 'testflight.py'), '--key-id', ASC_KEY_ID,
      '--issuer-id', ASC_ISSUER_ID, '--app', app,
      '--bundle-id', TESTFLIGHT_BUNDLE_ID, '--out', work,
      '--notes', notes(chrome), '--release', '--app-store')

  with open(os.path.join(app, 'Info.plist'), 'rb') as f:
    build = plistlib.load(f)['CFBundleVersion']
  altstore = os.path.join(REPO, 'altstore')
  run(os.path.join(TOOLS, 'make-altstore-release.sh'), app, altstore,
      notes(chrome))
  ipa = os.path.join(altstore, f'BuiltInAIBrowser-{build}.ipa')
  run('gh', 'release', 'upload', tag, ipa, '--clobber', cwd=REPO)
  run('gh', 'release', 'delete-asset', tag, UNSIGNED_ASSET, '--yes', cwd=REPO)
  run('gh', 'release', 'edit', tag, '--draft=false', '--latest', cwd=REPO)
  os.remove(ipa)

  with open(os.path.join(TOOLS, 'released.json'), 'w') as f:
    json.dump({'chrome': chrome}, f, indent=2)
    f.write('\n')
  run('git', 'add', 'altstore/source.json', 'ios-tools/released.json', cwd=REPO)
  run('git', 'commit', '-m', f'Release Chrome {chrome}', cwd=REPO)
  run('git', 'push', cwd=REPO)


def main():
  parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
  parser.add_argument('--chrome', required=True, help='the Chrome version')
  parser.add_argument('--tag', required=True,
                      help='the draft GitHub release with the unsigned app')
  args = parser.parse_args()
  secrets()
  with tempfile.TemporaryDirectory() as work:
    app, entitlements = unpack(args.tag, work)
    sign_ad_hoc(app, entitlements)
    release(app, args.chrome, args.tag, work)
  print(f'Released Chrome {args.chrome}. Once App Review has approved it, it goes '
        'out on the App Store, and `altstore_pal.py` publishes it on AltStore '
        'PAL.')


if __name__ == '__main__':
  main()
