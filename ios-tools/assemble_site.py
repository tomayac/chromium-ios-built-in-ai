#!/usr/bin/env python3
# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Assembles the GitHub Pages site, for the Pages workflow.

The site is the test pages of `bundle/test/` at its root, the pages of
`site/`, and `altstore/` with its sources. The AltStore PAL package that
`altstore/pal/source.json` names comes from its GitHub release, `pal-<build>`
(see `altstore_pal.py`), and goes where its `manifest.json` expects it.

Usage:
  assemble_site.py <out dir>
"""

import json
import os
import shutil
import subprocess
import sys
import urllib.parse

TOOLS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TOOLS)


def copy(source, target, skip=()):
  shutil.copytree(os.path.join(REPO, source), target, dirs_exist_ok=True,
                  ignore=shutil.ignore_patterns(*skip))


def pal_package(out):
  """Downloads the AltStore PAL package of `altstore/pal/source.json`."""
  source = os.path.join(REPO, 'altstore', 'pal', 'source.json')
  if not os.path.exists(source):
    return
  with open(source) as f:
    version = json.load(f)['apps'][0]['versions'][0]
  build = version['buildVersion']
  # The manifest's URL path below the site, such as altstore/pal/<build>.
  path = urllib.parse.urlparse(version['downloadURL']).path
  directory = os.path.join(out, *path.split('/')[2:-1])
  os.makedirs(directory, exist_ok=True)
  subprocess.run(['gh', 'release', 'download', f'pal-{build}', '--dir',
                  directory, '--clobber'], cwd=REPO, check=True)
  with open(os.path.join(directory, 'manifest.json')) as f:
    manifest = json.load(f)
  # Release assets are flat, the manifest names the variants by path.
  for variant in manifest['variants']:
    target = os.path.join(directory, variant['assetPath'])
    os.makedirs(os.path.dirname(target), exist_ok=True)
    os.replace(os.path.join(directory, os.path.basename(target)), target)
  print(f'Placed the AltStore PAL package {build} in '
        f'{os.path.relpath(directory, out)}.')


def main():
  out = os.path.abspath(sys.argv[1])
  shutil.rmtree(out, ignore_errors=True)
  copy('bundle/test', out, skip=('node_modules', 'serve.py'))
  copy('site', out)
  copy('altstore', os.path.join(out, 'altstore'), skip=('*.ipa',))
  pal_package(out)
  # Serves the files as they are, without Jekyll.
  open(os.path.join(out, '.nojekyll'), 'w').close()


if __name__ == '__main__':
  main()
