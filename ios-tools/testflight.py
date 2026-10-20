#!/usr/bin/env python3
# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Uploads a Chromium for iOS build to TestFlight and opens it to testers.

No Xcode UI involved. Given an App Store Connect API key and a device build of
Chromium (any signing), this:

1. creates an Apple Distribution certificate in the keychain of
   `asc_provision.py`, and an App Store profile for the App ID,
2. re-signs a copy of the app for the App Store, with the next free build
   number, the privacy manifest, and the export compliance answer,
3. validates and uploads it with `altool`,
4. waits until App Store Connect has processed it, and
5. with `--release`, fills in the beta app information from `testflight.json`,
   adds the build to the external group with the public link, and submits it
   to Beta App Review.

The app record has to exist first: the API cannot create apps. Create it once
at https://appstoreconnect.apple.com/apps with the App ID's bundle ID.

Usage:
  testflight.py --key-id ABC123 --issuer-id 69a6... --app out/dir/Chromium.app \\
      [--bundle-id com.example.browser] [--notes "What to test"] [--release]
"""

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time

import asc_provision as asc_lib
from asc_provision import AppStoreConnect, run

HERE = os.path.dirname(os.path.abspath(__file__))
PRIVACY_MANIFEST = os.path.join(HERE, 'PrivacyInfo.xcprivacy')
# Contact details for Beta App Review and the beta app information. Personal,
# so kept out of git.
CONFIG = os.path.join(HERE, 'testflight.json')
GROUP_NAME = 'Public'
# Entitlements of the profile that do not belong in the signature.
PROFILE_ONLY = {'keychain-access-groups'}


def profile_entitlements(path):
  decoded = run('security', 'cms', '-D', '-i', path)
  entitlements = plistlib.loads(decoded)['Entitlements']
  return {k: v for k, v in entitlements.items() if k not in PROFILE_ONLY}


def find_app(asc, bundle_identifier):
  listing = asc.get(f'/apps?filter[bundleId]={bundle_identifier}')
  apps = [a for a in listing['data']
          if a['attributes']['bundleId'] == bundle_identifier]
  if not apps:
    sys.exit(f'No app record for {bundle_identifier}. Create it once at '
             'https://appstoreconnect.apple.com/apps (New App, iOS, bundle '
             f'ID {bundle_identifier}), then run this again.')
  return apps[0]['id']


def next_build_number(asc, app, short_version):
  """`<major>.<build>.<n>`, with `n` one above every upload so far."""
  prefix = '.'.join(short_version.split('.')[:2]) + '.'
  highest = 0
  path = f'/builds?filter[app]={app}&limit=200&fields[builds]=version'
  while path:
    listing = asc.get(path)
    for build in listing['data']:
      version = build['attributes']['version']
      if version.startswith(prefix) and version[len(prefix):].isdigit():
        highest = max(highest, int(version[len(prefix):]))
    path = listing.get('links', {}).get('next', '').replace(asc_lib.API, '')
  return f'{prefix}{highest + 1}'


def replace_strings(value, old, new):
  if value == old:
    return new
  if isinstance(value, dict):
    return {k: replace_strings(v, old, new) for k, v in value.items()}
  if isinstance(value, list):
    return [replace_strings(v, old, new) for v in value]
  return value


def package(app_path, identity, profile, bundle_identifier, build_number,
            out_dir):
  """Re-signs a copy of the app for the App Store and zips it as an .ipa."""
  work = tempfile.mkdtemp()
  try:
    copy = os.path.join(work, 'Payload', 'Chromium.app')
    os.makedirs(os.path.dirname(copy))
    run('ditto', app_path, copy)

    info_path = os.path.join(copy, 'Info.plist')
    with open(info_path, 'rb') as f:
      raw = f.read()
    info = plistlib.loads(raw)
    # The bundle ID only lives in Info.plist, as the app's identifier and its
    # URL type, so the App Store build can have one of its own.
    info = replace_strings(info, info['CFBundleIdentifier'], bundle_identifier)
    info['CFBundleVersion'] = build_number
    # Chromium uses standard encryption only (TLS through BoringSSL and the
    # system), which is exempt from export documentation.
    info['ITSAppUsesNonExemptEncryption'] = False
    with open(info_path, 'wb') as f:
      plistlib.dump(info, f, fmt=plistlib.FMT_BINARY
                    if raw.startswith(b'bplist') else plistlib.FMT_XML)
    # App Store Connect requires a version on every framework, which Lottie's
    # Info.plist leaves out.
    frameworks = os.path.join(copy, 'Frameworks')
    for name in os.listdir(frameworks) if os.path.isdir(frameworks) else []:
      plist_path = os.path.join(frameworks, name, 'Info.plist')
      if not os.path.exists(plist_path):
        continue
      with open(plist_path, 'rb') as f:
        plist_raw = f.read()
      plist = plistlib.loads(plist_raw)
      if 'CFBundleShortVersionString' not in plist:
        plist['CFBundleShortVersionString'] = plist.get('CFBundleVersion', '1.0')
        with open(plist_path, 'wb') as f:
          plistlib.dump(plist, f, fmt=plistlib.FMT_BINARY
                        if plist_raw.startswith(b'bplist') else plistlib.FMT_XML)
    # Pseudo-locales for testing translations, which App Store Connect does not
    # recognize.
    for pseudo in ('ar_XB.lproj', 'en_XA.lproj'):
      shutil.rmtree(os.path.join(copy, pseudo), ignore_errors=True)
    shutil.copy(PRIVACY_MANIFEST, os.path.join(copy, 'PrivacyInfo.xcprivacy'))
    shutil.copy(profile, os.path.join(copy, 'embedded.mobileprovision'))

    entitlements = os.path.join(work, 'entitlements.plist')
    with open(entitlements, 'wb') as f:
      plistlib.dump(profile_entitlements(profile), f)

    # Inside out: nested code first, the app last.
    nested = []
    for root, dirs, files in os.walk(copy, topdown=False):
      for name in dirs + files:
        if name.endswith(('.framework', '.dylib', '.appex')):
          nested.append(os.path.join(root, name))
    for path in nested:
      run('codesign', '--force', '--sign', identity, '--timestamp=none', path)
    run('codesign', '--force', '--sign', identity, '--timestamp=none',
        '--entitlements', entitlements, copy)
    run('codesign', '--verify', '--deep', '--strict', copy)

    ipa = os.path.join(out_dir, f'Chromium-{build_number}.ipa')
    if os.path.exists(ipa):
      os.remove(ipa)
    subprocess.run(['zip', '-qry', ipa, 'Payload'], cwd=work, check=True)
    return ipa
  finally:
    shutil.rmtree(work)


def altool(action, ipa, key_id, issuer_id):
  result = subprocess.run(
      ['xcrun', 'altool', action, '-f', ipa, '-t', 'ios', '--apiKey', key_id,
       '--apiIssuer', issuer_id, '--output-format', 'json'],
      capture_output=True, text=True)
  output = result.stdout + result.stderr
  if result.returncode:
    sys.exit(f'altool {action} failed:\n{output}')
  return output


def upload_problems(asc, app, build_number):
  """Apple's errors for the upload of `build_number`, once it failed."""
  for upload in asc.get(f'/apps/{app}/buildUploads?limit=50')['data']:
    attributes = upload['attributes']
    if (attributes['cfBundleVersion'] == build_number
        and attributes['state']['state'] == 'FAILED'):
      return [f'{e["code"]}: {e["description"]}'
              for e in attributes['state']['errors']] or ['FAILED']
  return []


def wait_for_build(asc, app, build_number, timeout=3600):
  started = time.time()
  state = 'not there yet'
  while time.time() - started < timeout:
    # A rejected upload never turns into a build; the upload says why.
    problems = upload_problems(asc, app, build_number)
    if problems:
      sys.exit(f'App Store Connect rejected {build_number}:\n' +
               '\n'.join(problems))
    listing = asc.get(f'/builds?filter[app]={app}'
                      f'&filter[version]={build_number}')
    if listing['data']:
      build = listing['data'][0]
      state = build['attributes']['processingState']
      if state == 'VALID':
        return build['id']
      if state in ('FAILED', 'INVALID'):
        sys.exit(f'Build {build_number} is {state}; Apple emails the reasons.')
    print(f'  {int(time.time() - started)} s: {state}', flush=True)
    time.sleep(30)
  sys.exit(f'Build {build_number} still {state} after {timeout} s.')


def first(asc, path):
  data = asc.get(path)['data']
  return data[0] if data else None


def release(asc, app, build, config, notes):
  """Opens `build` to external testers through the public link."""
  locale = 'en-US'
  localization = first(
      asc, f'/apps/{app}/betaAppLocalizations?filter[locale]={locale}')
  attributes = {
      'description': config['description'],
      'feedbackEmail': config['feedbackEmail'],
      'privacyPolicyUrl': config['privacyPolicyUrl'],
      'marketingUrl': config.get('marketingUrl'),
  }
  if localization:
    asc.request('PATCH', f'/betaAppLocalizations/{localization["id"]}', {
        'data': {'type': 'betaAppLocalizations', 'id': localization['id'],
                 'attributes': attributes}})
  else:
    asc.post('/betaAppLocalizations', {
        'data': {'type': 'betaAppLocalizations',
                 'attributes': {'locale': locale, **attributes},
                 'relationships': {'app': {'data': {'type': 'apps',
                                                    'id': app}}}}})

  review = config['review']
  asc.request('PATCH', f'/betaAppReviewDetails/{app}', {
      'data': {'type': 'betaAppReviewDetails', 'id': app, 'attributes': {
          'contactFirstName': review['firstName'],
          'contactLastName': review['lastName'],
          'contactEmail': review['email'],
          'contactPhone': review['phone'],
          'demoAccountRequired': False,
          'notes': review.get('notes', ''),
      }}})

  if notes:
    existing = first(asc, f'/builds/{build}/betaBuildLocalizations')
    if existing:
      asc.request('PATCH', f'/betaBuildLocalizations/{existing["id"]}', {
          'data': {'type': 'betaBuildLocalizations', 'id': existing['id'],
                   'attributes': {'whatsNew': notes}}})
    else:
      asc.post('/betaBuildLocalizations', {
          'data': {'type': 'betaBuildLocalizations',
                   'attributes': {'locale': locale, 'whatsNew': notes},
                   'relationships': {'build': {'data': {'type': 'builds',
                                                        'id': build}}}}})

  group = next((g for g in asc.get(f'/apps/{app}/betaGroups')['data']
                if g['attributes']['name'] == GROUP_NAME), None)
  if not group:
    group = asc.post('/betaGroups', {
        'data': {'type': 'betaGroups',
                 'attributes': {'name': GROUP_NAME, 'publicLinkEnabled': True,
                                'publicLinkLimitEnabled': False},
                 'relationships': {'app': {'data': {'type': 'apps',
                                                    'id': app}}}}})['data']
  asc.post(f'/betaGroups/{group["id"]}/relationships/builds',
           {'data': [{'type': 'builds', 'id': build}]})

  status = asc.get(f'/builds/{build}/betaAppReviewSubmission')
  if not status.get('data'):
    asc.post('/betaAppReviewSubmissions', {
        'data': {'type': 'betaAppReviewSubmissions',
                 'relationships': {'build': {'data': {'type': 'builds',
                                                      'id': build}}}}})
    print('Submitted to Beta App Review.')
  link = asc.get(f'/betaGroups/{group["id"]}')['data']['attributes'].get(
      'publicLink')
  print(f'Public link, live once the review passes: {link}')


def main():
  parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
  parser.add_argument('--key-id', required=True)
  parser.add_argument('--issuer-id', required=True)
  parser.add_argument('--key', help='AuthKey_<id>.p8, by default in '
                      '~/.appstoreconnect/private_keys, where altool looks')
  parser.add_argument('--app', required=True, help='a device build of Chromium')
  parser.add_argument('--bundle-id',
                      help="the App Store app's bundle ID, by default the build's")
  parser.add_argument('--out', default='.', help='where the .ipa goes')
  parser.add_argument('--notes', default='', help='what to test in this build')
  parser.add_argument('--release', action='store_true',
                      help='open the build to external testers')
  parser.add_argument('--skip-upload', metavar='BUILD_NUMBER',
                      help='release an uploaded build instead of a new one')
  args = parser.parse_args()
  key = args.key or os.path.expanduser(
      f'~/.appstoreconnect/private_keys/AuthKey_{args.key_id}.p8')
  with open(os.path.join(args.app, 'Info.plist'), 'rb') as f:
    info = plistlib.load(f)
  bundle_identifier = args.bundle_id or info['CFBundleIdentifier']

  asc = AppStoreConnect(args.key_id, args.issuer_id, key)
  config = None
  if args.release:
    if not os.path.exists(CONFIG):
      sys.exit(f'--release needs {CONFIG}; see testflight.example.json.')
    with open(CONFIG) as f:
      config = json.load(f)

  if args.skip_upload:
    app = find_app(asc, bundle_identifier)
    build_number = args.skip_upload
  else:
    password = asc_lib.ensure_keychain()
    identity = asc_lib.ensure_certificate(asc, password, 'DISTRIBUTION')
    certificate = asc_lib.certificate_id(asc, identity, 'DISTRIBUTION')
    bundle = asc_lib.ensure_bundle_id(asc, bundle_identifier,
                                      'Chromium Built-in AI')
    profile = asc_lib.create_profile(asc, f'{bundle_identifier} App Store',
                                     bundle, certificate, None, 'IOS_APP_STORE')
    app = find_app(asc, bundle_identifier)
    short_version = info['CFBundleShortVersionString']
    build_number = next_build_number(asc, app, short_version)
    print(f'Packaging {short_version} ({build_number}).')
    ipa = package(args.app, identity, profile, bundle_identifier, build_number,
                  args.out)
    print(f'Validating {ipa}.')
    altool('--validate-app', ipa, args.key_id, args.issuer_id)
    print('Uploading.')
    altool('--upload-app', ipa, args.key_id, args.issuer_id)

  print(f'Waiting for App Store Connect to process {build_number}.')
  build = wait_for_build(asc, app, build_number)
  print(f'Build {build_number} is ready for internal testers.')
  if args.release:
    release(asc, app, build, config, args.notes)


if __name__ == '__main__':
  main()
