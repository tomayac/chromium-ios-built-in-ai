#!/usr/bin/env python3
# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Puts the app's own name into the texts iOS shows for its permissions.

Usage:
  rebrand_strings.py <App.app> [name]

The permission prompts (camera, microphone, location, and the like) come from
`InfoPlist.strings` in every language, where Chromium's strings name the
product "Chromium". This replaces that name with `name`, by default the app's
`CFBundleDisplayName`, in every language the app has.
"""

import glob
import os
import plistlib
import sys

PRODUCT = 'Chromium'


def rebrand(app, name):
  changed = 0
  for path in glob.glob(os.path.join(app, '*.lproj', 'InfoPlist.strings')):
    with open(path, 'rb') as f:
      raw = f.read()
    strings = plistlib.loads(raw)
    rebranded = {k: v.replace(PRODUCT, name) if isinstance(v, str) else v
                 for k, v in strings.items()}
    if rebranded != strings:
      with open(path, 'wb') as f:
        plistlib.dump(rebranded, f, fmt=plistlib.FMT_BINARY
                      if raw.startswith(b'bplist') else plistlib.FMT_XML)
      changed += 1
  return changed


def rebrand_app(app, name=None):
  if name is None:
    with open(os.path.join(app, 'Info.plist'), 'rb') as f:
      name = plistlib.load(f)['CFBundleDisplayName']
  if name == PRODUCT:
    return
  print(f'Named the app "{name}" in the permission texts of '
        f'{rebrand(app, name)} languages.')


def main():
  rebrand_app(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)


if __name__ == '__main__':
  main()
