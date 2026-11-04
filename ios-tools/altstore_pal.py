#!/usr/bin/env python3
# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Publishes a notarized app version to AltStore PAL.

Usage:
  altstore_pal.py --key-id ABC123 --issuer-id 69a6... --app 6824947028 \\
      --version 157.8092.0 --out altstore/pal

Once Apple has notarized the version, App Store Connect generates its
alternative distribution package (ADP). This:

1. looks up the package's ID in App Store Connect,
2. has AltStore process the package, and downloads it from AltStore,
3. uploads its files to the GitHub release `pal-<build>`, from where the
   Pages workflow places them at `--out`/<build>/ on GitHub Pages, and
4. writes `--out`/source.json, the AltStore PAL source, with the app's
   metadata from the AltStore Classic source and its `marketplaceID`.

AltStore PAL reads the package through the URL of its `manifest.json`, and
the files it lists next to it. The packages stay out of git: each is about
300 MB. GitHub Pages refuses files over 100 MB, which the script checks
first.
"""

import argparse
import datetime
import io
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import tempfile
import zipfile

from asc_provision import AppStoreConnect

REPO = 'tomayac/chromium-ios-built-in-ai'

ALTSTORE_API = 'https://api.altstore.io'
PAGES = 'https://tomayac.github.io/chromium-ios-built-in-ai'
GITHUB_FILE_LIMIT = 100 * 1000 * 1000
# AltStore's API sits behind Cloudflare, which turns away Python's default
# User-Agent (error 1010).
USER_AGENT = 'chromium-ios-built-in-ai release script'


def altstore(method, path, body=None):
  request = urllib.request.Request(
      ALTSTORE_API + path, method=method,
      data=json.dumps(body).encode() if body is not None else None,
      headers={'Content-Type': 'application/json', 'User-Agent': USER_AGENT})
  with urllib.request.urlopen(request) as response:
    text = response.read()
    return json.loads(text) if text else {}


def package_id(asc, app, version_string):
  versions = asc.get(f'/apps/{app}/appStoreVersions?limit=50')['data']
  version = next((v for v in versions
                  if v['attributes']['versionString'] == version_string), None)
  if not version:
    sys.exit(f'No app version {version_string}.')
  state = version['attributes']['appVersionState']
  package = asc.get(f'/appStoreVersions/{version["id"]}'
                    '/alternativeDistributionPackage')['data']
  if not package:
    sys.exit(f'Version {version_string} is {state}, and has no alternative '
             'distribution package yet. Apple generates it once notarization '
             'passes.')
  build = asc.get(f'/appStoreVersions/{version["id"]}/build')['data']
  return package['id'], build['attributes']['version']


def download_package(adp_id, timeout=1800):
  # AltStore processes packages on its own once Apple notifies it; asking
  # saves waiting for that.
  altstore('POST', '/adps', {'adpID': adp_id})
  started = time.time()
  while time.time() - started < timeout:
    status = altstore('GET', f'/adps/{adp_id}')
    if status.get('downloadURL'):
      print('Downloading the package from AltStore.')
      download = urllib.request.Request(status['downloadURL'],
                                        headers={'User-Agent': USER_AGENT})
      with urllib.request.urlopen(download) as response:
        return response.read()
    print(f'  AltStore: {status.get("status", "processing")}', flush=True)
    time.sleep(30)
  sys.exit(f'AltStore did not finish processing {adp_id} in {timeout} s.')


def unpack(data, directory):
  shutil.rmtree(directory, ignore_errors=True)
  os.makedirs(directory)
  with zipfile.ZipFile(io.BytesIO(data)) as archive:
    archive.extractall(directory)
  # Packages may come wrapped in one folder.
  entries = os.listdir(directory)
  if 'manifest.json' not in entries and len(entries) == 1:
    inner = os.path.join(directory, entries[0])
    for name in os.listdir(inner):
      shutil.move(os.path.join(inner, name), directory)
    os.rmdir(inner)
  if not os.path.exists(os.path.join(directory, 'manifest.json')):
    sys.exit(f'The package in {directory} has no manifest.json; look at its '
             'layout before publishing it.')
  too_big = []
  for root, _, files in os.walk(directory):
    for name in files:
      path = os.path.join(root, name)
      if os.path.getsize(path) > GITHUB_FILE_LIMIT:
        too_big.append(f'{os.path.relpath(path, directory)} '
                       f'({os.path.getsize(path) // 1000000} MB)')
  if too_big:
    sys.exit('Over GitHub\'s 100 MB file limit, so GitHub Pages cannot serve '
             'them:\n  ' + '\n  '.join(too_big))


def variant_size(directory):
  """The size of the largest variant, which AltStore shows as the app's."""
  variants = os.path.join(directory, 'variant')
  if not os.path.isdir(variants):
    variants = directory
  sizes = [os.path.getsize(os.path.join(root, name))
           for root, _, files in os.walk(variants) for name in files]
  return max(sizes) if sizes else 0


def release_tag(build):
  return f'pal-{build}'


def upload(directory, version, build):
  """Attaches the package's files to the GitHub release `pal-<build>`.

  Release assets have flat names, so the Pages workflow puts the variants
  back where `manifest.json` names them.
  """
  files = [os.path.join(root, name)
           for root, _, names in os.walk(directory) for name in names]
  tag = release_tag(build)
  exists = subprocess.run(['gh', 'release', 'view', tag, '--repo', REPO],
                          capture_output=True).returncode == 0
  if not exists:
    subprocess.run(['gh', 'release', 'create', tag, '--repo', REPO,
                    '--title', f'AltStore PAL package {version} ({build})',
                    '--notes', 'The alternative distribution package that '
                    'GitHub Pages serves to AltStore PAL.',
                    '--prerelease', '--latest=false'], check=True)
  subprocess.run(['gh', 'release', 'upload', tag, '--repo', REPO, '--clobber',
                  *files], check=True)


def write_source(out, classic_source, app, bundle_id, version, build, url,
                 size, notes):
  with open(classic_source) as f:
    source = json.load(f)
  classic = source['apps'][0]
  pal = {k: v for k, v in classic.items() if k != 'versions'}
  pal.update({
      'bundleIdentifier': bundle_id,
      'marketplaceID': app,
      'versions': [{
          'version': version,
          'buildVersion': build,
          'date': datetime.date.today().isoformat(),
          'localizedDescription': notes,
          'downloadURL': url,
          'size': size,
      }],
  })
  # Notarized apps get every capability of their App ID.
  pal['appPermissions'] = dict(classic.get('appPermissions', {}))
  pal['appPermissions']['entitlements'] = sorted({
      *pal['appPermissions'].get('entitlements', []),
      'com.apple.developer.kernel.extended-virtual-addressing',
      'com.apple.developer.kernel.increased-memory-limit',
  })
  source['apps'] = [pal]
  source['name'] = source['name'] + ' (AltStore PAL)'
  path = os.path.join(out, 'source.json')
  with open(path, 'w') as f:
    json.dump(source, f, indent=2, ensure_ascii=False)
    f.write('\n')
  print(f'Wrote {path}.')


def main():
  parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
  parser.add_argument('--key-id', required=True)
  parser.add_argument('--issuer-id', required=True)
  parser.add_argument('--key', help='AuthKey_<id>.p8, by default in '
                      '~/.appstoreconnect/private_keys')
  parser.add_argument('--app', required=True, help="the app's Apple ID")
  parser.add_argument('--bundle-id',
                      default='com.tomayac.chromium.built-in-ai-browser.ios.dev')
  parser.add_argument('--version', required=True,
                      help='the notarized app version, such as 157.8092.0')
  parser.add_argument('--out', default='altstore/pal')
  parser.add_argument('--classic-source', default='altstore/source.json')
  parser.add_argument('--notes', default='')
  args = parser.parse_args()
  key = args.key or os.path.expanduser(
      f'~/.appstoreconnect/private_keys/AuthKey_{args.key_id}.p8')
  asc = AppStoreConnect(args.key_id, args.issuer_id, key)

  adp_id, build = package_id(asc, args.app, args.version)
  print(f'Package {adp_id} for {args.version} ({build}).')
  with tempfile.TemporaryDirectory() as work:
    directory = os.path.join(work, build)
    unpack(download_package(adp_id), directory)
    upload(directory, args.version, build)
    size = variant_size(directory)
  # `--out` is relative to the repository root, and the Pages workflow puts the
  # package at the same path on GitHub Pages.
  url = f'{PAGES}/{os.path.normpath(os.path.join(args.out, build))}/manifest.json'
  write_source(args.out, args.classic_source, args.app, args.bundle_id,
               args.version, build, url, size, args.notes)
  print(f'Commit {args.out}/source.json; once it is pushed, the Pages workflow '
        f'publishes the package at {url}.')


if __name__ == '__main__':
  main()
