#!/usr/bin/env python3
# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Replaces an app version's App Store screenshots through the API.

Usage:
  upload_screenshots.py --key-id ABC123 --issuer-id 69a6... --app 6824947028 \\
      APP_IPHONE_65=iphone.png APP_IPAD_PRO_3GEN_129=ipad.png

Each argument pairs a screenshot display type with a PNG of the size that
type takes, such as 1242x2688 for APP_IPHONE_65 and 2064x2752 for
APP_IPAD_PRO_3GEN_129. The screenshots go to the en-US localization of the
version that is being prepared.
"""

import argparse
import hashlib
import os
import urllib.request

from asc_provision import AppStoreConnect


def editable_localization(asc, app, locale='en-US'):
  versions = asc.get(f'/apps/{app}/appStoreVersions')['data']
  editable = [v for v in versions if v['attributes']['appVersionState'] in (
      'PREPARE_FOR_SUBMISSION', 'DEVELOPER_REJECTED', 'REJECTED',
      'METADATA_REJECTED', 'INVALID_BINARY')]
  if not editable:
    raise SystemExit('No app version is open for changes.')
  localizations = asc.get(
      f'/appStoreVersions/{editable[0]["id"]}/appStoreVersionLocalizations')
  return next(l['id'] for l in localizations['data']
              if l['attributes']['locale'] == locale)


def screenshot_set(asc, localization, display_type):
  sets = asc.get(f'/appStoreVersionLocalizations/{localization}'
                 '/appScreenshotSets')['data']
  for existing in sets:
    if existing['attributes']['screenshotDisplayType'] == display_type:
      # Replaces what is there.
      for shot in asc.get(f'/appScreenshotSets/{existing["id"]}'
                           '/appScreenshots')['data']:
        asc.request('DELETE', f'/appScreenshots/{shot["id"]}')
      return existing['id']
  return asc.post('/appScreenshotSets', {
      'data': {'type': 'appScreenshotSets',
               'attributes': {'screenshotDisplayType': display_type},
               'relationships': {'appStoreVersionLocalization': {
                   'data': {'type': 'appStoreVersionLocalizations',
                            'id': localization}}}}})['data']['id']


def upload(asc, set_id, path):
  with open(path, 'rb') as f:
    data = f.read()
  shot = asc.post('/appScreenshots', {
      'data': {'type': 'appScreenshots',
               'attributes': {'fileName': os.path.basename(path),
                              'fileSize': len(data)},
               'relationships': {'appScreenshotSet': {
                   'data': {'type': 'appScreenshotSets', 'id': set_id}}}}})
  shot = shot['data']
  for operation in shot['attributes']['uploadOperations']:
    part = data[operation['offset']:operation['offset'] + operation['length']]
    request = urllib.request.Request(
        operation['url'], data=part, method=operation['method'],
        headers={h['name']: h['value'] for h in operation['requestHeaders']})
    urllib.request.urlopen(request).read()
  asc.request('PATCH', f'/appScreenshots/{shot["id"]}', {
      'data': {'type': 'appScreenshots', 'id': shot['id'],
               'attributes': {'uploaded': True,
                             'sourceFileChecksum': hashlib.md5(data).hexdigest()}}})
  print(f'Uploaded {path}.')


def main():
  parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
  parser.add_argument('--key-id', required=True)
  parser.add_argument('--issuer-id', required=True)
  parser.add_argument('--key', help='AuthKey_<id>.p8, by default in '
                      '~/.appstoreconnect/private_keys')
  parser.add_argument('--app', required=True, help="the app's Apple ID")
  parser.add_argument('screenshots', nargs='+', metavar='TYPE=PATH')
  args = parser.parse_args()
  key = args.key or os.path.expanduser(
      f'~/.appstoreconnect/private_keys/AuthKey_{args.key_id}.p8')
  asc = AppStoreConnect(args.key_id, args.issuer_id, key)
  localization = editable_localization(asc, args.app)
  for item in args.screenshots:
    display_type, path = item.split('=', 1)
    upload(asc, screenshot_set(asc, localization, display_type), path)


if __name__ == '__main__':
  main()
