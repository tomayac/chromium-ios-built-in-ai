#!/usr/bin/env python3
# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Catches the browser up with the newest Chrome for iOS, and releases it.

Each run first publishes the newest version that Apple has approved on
AltStore PAL, if AltStore PAL does not have it yet (see `altstore_pal.py`).
Then it asks Chromium Dash for the newest Chrome iOS Stable version. If that
is newer than the one in `released.json`, this:

1. checks out that Chrome release in a Chromium checkout of its own, a git
   worktree of the main one, and syncs its dependencies,
2. applies `patches/` onto it, and writes them back, so they keep up with
   Chrome's changes,
3. rebuilds the polyfill bundle from the newest web-ai-demos, and folds any
   change into the patch that adds the bundle,
4. builds the app with `release.gn`, unsigned, since Santa may not let
   `codesign` run on this Mac, and
5. pushes the new patches, attaches the app to a draft GitHub release, and
   starts the release workflow, which signs it on a GitHub-hosted Mac,
   releases it on TestFlight, submits it for App Store review, and publishes
   it for AltStore Classic (see `ci_release.py`).

Every stage can run again. If a patch does not apply, this stops with the
checkout in the middle of `git am`: resolve the conflict, run
`git am --continue`, and run this again with `--from patch`. Once App Review
has approved the version, the next run publishes it on AltStore PAL.

Usage:
  catch_up.py [--version 157.0.8103.37] [--force] [--no-release]
              [--from sync|patch|polyfills|build|upload]
"""

import argparse
import glob
import json
import os
import plistlib
import shutil
import subprocess
import sys
import urllib.request

TOOLS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TOOLS)

# The main Chromium checkout, whose git objects the release checkout shares,
# and where the release checkout goes.
MAIN_SRC = os.environ.get('CHROMIUM_SRC', '/Volumes/SanDisk/chromium/src')
RELEASE_ROOT = os.environ.get('RELEASE_ROOT', '/Volumes/SanDisk/chromium-release')
RELEASE_SRC = os.path.join(RELEASE_ROOT, 'src')
OUT_DIR = 'out/release'
CHROMIUM_URL = 'https://chromium.googlesource.com/chromium/src.git'
WEB_AI_DEMOS_URL = 'https://github.com/GoogleChromeLabs/web-ai-demos.git'
# The native backend of the Prompt API polyfill, which is not upstream yet.
NATIVE_BACKEND_URL = 'https://github.com/tomayac/web-ai-demos.git'
NATIVE_BACKEND_BRANCH = 'prompt-api-polyfill-native-backend'  # PR #558

UNSIGNED_ASSET = 'Chromium-unsigned.zip'  # What `ci_release.py` signs.

# App Store Connect: the API key, which stays in
# ~/.appstoreconnect/private_keys, and the app.
ASC_KEY_ID = 'B78MTCSY2V'
ASC_ISSUER_ID = '69a6de6f-b54e-47e3-e053-5b8c7c11a4d1'
APP_ID = '6824947028'
PAL_SOURCE = os.path.join(REPO, 'altstore', 'pal', 'source.json')

STAGES = ['sync', 'patch', 'polyfills', 'build', 'upload']

# The tools and the Xcode the builds use, and room for temporary files, which
# the internal disk lacks.
os.environ['PATH'] = os.pathsep.join([
    os.environ.get('DEPOT_TOOLS', os.path.expanduser('~/bin/depot_tools')),
    '/opt/homebrew/bin', os.environ['PATH']])
os.environ.setdefault('DEVELOPER_DIR',
                      '/Volumes/SanDisk/xcode/sdk/Xcode.app/Contents/Developer')
os.environ.setdefault('TMPDIR', '/Volumes/SanDisk/tmp')


def public_gitconfig():
  """Points git at the global config minus its URL rewrites.

  Some setups rewrite https://*.googlesource.com to a remote that needs a
  login, but Chromium and its dependencies are all public.
  """
  source = os.path.expanduser('~/.gitconfig')
  if not os.path.exists(source):
    return
  kept, skipping = [], False
  with open(source) as f:
    for line in f:
      if line.lstrip().startswith('['):
        skipping = line.lstrip().lower().startswith('[url ')
      if not skipping:
        kept.append(line)
  os.makedirs(RELEASE_ROOT, exist_ok=True)
  path = os.path.join(RELEASE_ROOT, 'gitconfig')
  with open(path, 'w') as f:
    f.writelines(kept)
  os.environ['GIT_CONFIG_GLOBAL'] = path


public_gitconfig()
BUNDLE_PATH = 'ios/chrome/browser/built_in_ai/model/resources/built_in_ai_polyfills.js'
DASH = ('https://chromiumdash.appspot.com/fetch_releases'
        '?channel=Stable&platform=iOS&num=1')


def run(*cmd, cwd=None, env=None, check=True, capture=False):
  print('+', ' '.join(cmd), flush=True)
  result = subprocess.run(cmd, cwd=cwd, env=env, check=False, text=True,
                          capture_output=capture)
  if check and result.returncode:
    if capture:
      sys.stderr.write(result.stdout + result.stderr)
    sys.exit(f'Failed ({result.returncode}): {" ".join(cmd)}')
  return result


def git(*args, cwd=RELEASE_SRC, check=True):
  return run('git', *args, cwd=cwd, check=check, capture=True)


def newest_chrome():
  with urllib.request.urlopen(DASH, timeout=30) as response:
    return json.load(response)[0]['version']


def version_key(version):
  return tuple(int(part) for part in version.split('.'))


def released():
  with open(os.path.join(TOOLS, 'released.json')) as f:
    return json.load(f)['chrome']


def branch_name(version):
  return f'built-in-ai-ios-{version}'


def patches():
  return sorted(glob.glob(os.path.join(REPO, 'patches', '*.patch')))


def am_in_progress():
  # A worktree keeps its `git am` state in the main checkout's git directory.
  path = git('rev-parse', '--git-path', 'rebase-apply').stdout.strip()
  return os.path.exists(os.path.join(RELEASE_SRC, path))


def git_dependencies(root):
  """The paths of the git repositories that gclient checked out under root."""
  revinfo = run('gclient', 'revinfo', cwd=root, capture=True).stdout
  paths = []
  for line in revinfo.splitlines():
    path = line.split(': ', 1)[0]
    # CIPD packages and Cloud Storage files have colons in their names.
    if path != 'src' and ':' not in path and \
        os.path.isdir(os.path.join(root, path, '.git')):
      paths.append(path)
  return paths


def seed_dependencies():
  """Clones the dependencies of a new release checkout from the main one.

  `--shared` borrows the main checkout's objects, so this takes seconds and no
  space, and `gclient sync` then only fetches what the release needs on top.
  """
  main_root = os.path.dirname(MAIN_SRC)
  for path in git_dependencies(main_root):
    source = os.path.join(main_root, path)
    target = os.path.join(RELEASE_ROOT, path)
    if os.path.exists(target):
      continue
    url = git('remote', 'get-url', 'origin', cwd=source).stdout.strip()
    run('git', 'clone', '--quiet', '--shared', '--no-checkout', source, target)
    git('remote', 'set-url', 'origin', url, cwd=target)


def share_objects():
  """Drops the objects that the main checkout's dependencies already have.

  Links each dependency of the release checkout to the same one in the main
  checkout, and repacks it without the objects it can borrow from there.
  """
  main_root = os.path.dirname(MAIN_SRC)
  for path in git_dependencies(RELEASE_ROOT):
    source = os.path.join(main_root, path, '.git', 'objects')
    alternates = os.path.join(RELEASE_ROOT, path, '.git', 'objects', 'info',
                              'alternates')
    if not os.path.isdir(source) or os.path.exists(alternates):
      continue
    with open(alternates, 'w') as f:
      f.write(source + '\n')
    git('repack', '-a', '-d', '-l', '-q', cwd=os.path.join(RELEASE_ROOT, path))


def sync(version):
  """Checks out the Chrome release in the release checkout, with its deps."""
  tag = f'refs/tags/{version}'
  # By URL, since the main checkout's remote may need a login.
  git('fetch', CHROMIUM_URL, f'+{tag}:{tag}', '--no-tags', cwd=MAIN_SRC)
  if not os.path.exists(RELEASE_SRC):
    os.makedirs(RELEASE_ROOT, exist_ok=True)
    # A worktree shares the main checkout's objects, which saves a 70 GB clone,
    # and leaves whatever the main checkout is doing alone.
    git('worktree', 'add', '--detach', RELEASE_SRC, tag, cwd=MAIN_SRC)
    shutil.copy(os.path.join(os.path.dirname(MAIN_SRC), '.gclient'), RELEASE_ROOT)
    seed_dependencies()
  if am_in_progress():
    sys.exit('The release checkout is in the middle of `git am`. Finish it with '
             '`git am --continue` or drop it with `git am --abort`, then run '
             'this again.')
  git('checkout', '-B', branch_name(version), tag)
  run('gclient', 'sync', '-D', '--force', '--reset', cwd=RELEASE_ROOT)
  share_objects()


def apply_patches(version):
  """Applies patches/ onto the release, and writes them back from it."""
  tag = f'refs/tags/{version}'
  applied = int(git('rev-list', '--count', f'{tag}..HEAD').stdout)
  if applied == 0:
    # `--keep-non-patch` keeps the "[iOS]" that starts each subject.
    result = run('git', 'am', '-3', '--keep-non-patch',
                 '--committer-date-is-author-date', *patches(),
                 cwd=RELEASE_SRC, check=False)
    if result.returncode:
      sys.exit(f'A patch did not apply onto Chrome {version}. In {RELEASE_SRC}, '
               'resolve the conflict, `git add` the files, run '
               '`git am --continue`, and then run this again with '
               '`--from patch`.')
  elif applied != len(patches()):
    sys.exit(f'{RELEASE_SRC} has {applied} commits on Chrome {version}, but there '
             f'are {len(patches())} patches. Check the branch, or start over '
             'with `--from sync`.')
  write_patches(version)


def write_patches(version):
  for path in patches():
    os.remove(path)
  git('format-patch', '--no-signature', '--zero-commit', '--no-numbered',
      '-o', os.path.join(REPO, 'patches'), f'refs/tags/{version}..HEAD')


def polyfills(version):
  """Rebuilds the polyfill bundle from the newest web-ai-demos.

  That is web-ai-demos `main`, with the native backend's commits on top.
  """
  demos = os.path.join(RELEASE_ROOT, 'web-ai-demos')
  if not os.path.exists(demos):
    # Without blobs until checkout: the history is enough to find the backend's
    # commits.
    run('git', 'clone', '--filter=blob:none', '--no-checkout', WEB_AI_DEMOS_URL,
        demos)
    git('remote', 'add', 'backend', NATIVE_BACKEND_URL, cwd=demos)
  git('fetch', 'origin', 'main', cwd=demos)
  git('checkout', '--force', '-B', 'release', 'origin/main', cwd=demos)
  landed = git('cat-file', '-e', 'origin/main:prompt-api-polyfill/backends/native.js',
               cwd=demos, check=False).returncode == 0
  if not landed:
    git('fetch', 'backend', NATIVE_BACKEND_BRANCH, cwd=demos)
    result = run('git', 'cherry-pick',
                 f'origin/main..backend/{NATIVE_BACKEND_BRANCH}', cwd=demos,
                 check=False)
    if result.returncode:
      sys.exit(f'The native backend does not apply onto web-ai-demos main. Update '
               f'{NATIVE_BACKEND_BRANCH} in {NATIVE_BACKEND_URL} to apply onto '
               'it, and run this again with `--from polyfills`.')
  bundle = os.path.join(REPO, 'bundle')
  run('npm', 'ci', '--silent', cwd=bundle)
  run(os.path.join(bundle, 'update-chromium.sh'), RELEASE_SRC,
      env={**os.environ, 'WEB_AI_DEMOS': demos})
  if not git('status', '--porcelain', BUNDLE_PATH).stdout.strip():
    print('The polyfill bundle is unchanged.')
    return
  # Folds the new bundle into the patch that added it, so the patches stay one
  # per change.
  owner = git('log', '-1', '--format=%H', 'HEAD', '--', BUNDLE_PATH).stdout.strip()
  git('commit', '--fixup', owner, BUNDLE_PATH)
  run('git', 'rebase', '--autosquash', '--committer-date-is-author-date',
      '--interactive', f'refs/tags/{version}', cwd=RELEASE_SRC,
      env={**os.environ, 'GIT_SEQUENCE_EDITOR': 'true'})
  write_patches(version)


def gn_args():
  with open(os.path.join(TOOLS, 'release.gn')) as f:
    args = f.read()
  with open(os.path.join(TOOLS, 'signing.json')) as f:
    signing = json.load(f)['gn_args']
  # The build signs nothing (see release.gn), so it only takes the bundle ID
  # from signing.json. With a signing identity, it would expect a provisioning
  # profile in the app.
  prefix = signing['ios_app_bundle_id_prefix']
  args += f'\n# From signing.json.\nios_app_bundle_id_prefix = "{prefix}"\n'
  return args


def build():
  run('python3', 'third_party/litert_lm/fetch.py', cwd=RELEASE_SRC)
  out = os.path.join(RELEASE_SRC, OUT_DIR)
  os.makedirs(out, exist_ok=True)
  with open(os.path.join(out, 'args.gn'), 'w') as f:
    f.write(gn_args())
  run('gn', 'gen', OUT_DIR, cwd=RELEASE_SRC)
  run('autoninja', '-C', OUT_DIR, 'chrome', cwd=RELEASE_SRC)


def app_path():
  return os.path.join(RELEASE_SRC, OUT_DIR, 'Chromium.app')


def write_entitlements():
  """Writes the entitlements of a sideload build, for `ci_release.py`.

  They come from the build's template, minus what signing for a team fills
  in, such as the application identifier.
  """
  template = os.path.join(RELEASE_SRC, 'ios', 'chrome', 'app', 'resources',
                          'Chrome+Sideload.entitlements')
  with open(template, 'rb') as f:
    entitlements = plistlib.load(f)
  entitlements = {key: value for key, value in entitlements.items()
                  if not (isinstance(value, str) and '$(' in value)}
  with open(os.path.join(RELEASE_SRC, OUT_DIR, 'release.entitlements'), 'wb') as f:
    plistlib.dump(entitlements, f)


def upload(version):
  """Hands the build to the release workflow."""
  with open(os.path.join(app_path(), 'Info.plist'), 'rb') as f:
    tag = 'v' + plistlib.load(f)['CFBundleVersion']
  write_entitlements()
  out = os.path.join(RELEASE_SRC, OUT_DIR)
  archive = os.path.join(out, UNSIGNED_ASSET)
  if os.path.exists(archive):
    os.remove(archive)
  # `-y` keeps the frameworks' symlinks.
  run('zip', '-qry', archive, 'Chromium.app', 'release.entitlements', cwd=out)

  # The workflow checks out the repository, so the patches go first.
  run('git', 'add', 'patches', cwd=REPO)
  if run('git', 'diff', '--cached', '--quiet', cwd=REPO, check=False).returncode:
    run('git', 'commit', '-m', f'Catch up the patches with Chrome {version}',
        cwd=REPO)
  run('git', 'push', cwd=REPO)

  title = f'Built-In AI Browser {tag[1:]}'
  if run('gh', 'release', 'view', tag, cwd=REPO, check=False,
         capture=True).returncode:
    run('gh', 'release', 'create', tag, archive, '--draft', '--title', title,
        '--notes', f'Catches up with Chrome {version}.', cwd=REPO)
  else:
    run('gh', 'release', 'upload', tag, archive, '--clobber', cwd=REPO)
  os.remove(archive)
  run('gh', 'workflow', 'run', 'release.yml', '-f', f'chrome={version}',
      '-f', f'tag={tag}', cwd=REPO)


def chrome_version(app_version):
  """The Chrome version of an app version: 157.8103.37 is 157.0.8103.37."""
  major, build, patch = app_version.split('.')
  return f'{major}.0.{build}.{patch}'


def publish_pal():
  """Publishes the newest approved version on AltStore PAL, if it is new."""
  sys.path.insert(0, TOOLS)
  from asc_provision import AppStoreConnect
  asc = AppStoreConnect(ASC_KEY_ID, ASC_ISSUER_ID, os.path.expanduser(
      f'~/.appstoreconnect/private_keys/AuthKey_{ASC_KEY_ID}.p8'))
  current = '0.0.0'
  if os.path.exists(PAL_SOURCE):
    with open(PAL_SOURCE) as f:
      current = json.load(f)['apps'][0]['versions'][0]['version']
  approved = [v['attributes']['versionString']
              for v in asc.get(f'/apps/{APP_ID}/appStoreVersions?limit=50')['data']
              if v['attributes']['appVersionState'] in
              ('READY_FOR_DISTRIBUTION', 'READY_FOR_SALE')]
  newer = [v for v in approved if version_key(v) > version_key(current)]
  if not newer:
    print(f'AltStore PAL has the newest approved version, {current}.')
    return
  version = max(newer, key=version_key)
  print(f'Publishing {version} on AltStore PAL, which has {current}.')
  run(os.path.join(TOOLS, 'altstore_pal.py'), '--key-id', ASC_KEY_ID,
      '--issuer-id', ASC_ISSUER_ID, '--app', APP_ID, '--version', version,
      '--notes', f'Catches up with Chrome {chrome_version(version)}.', cwd=REPO)
  run('git', 'add', PAL_SOURCE, cwd=REPO)
  run('git', 'commit', '-m', f'Publish {version} on AltStore PAL', cwd=REPO)
  # The push starts the Pages workflow, which publishes the package.
  run('git', 'push', cwd=REPO)


def main():
  parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
  parser.add_argument('--version', help='a Chrome version, by default the newest '
                      'Chrome iOS Stable')
  parser.add_argument('--force', action='store_true',
                      help='run even if this version is already released')
  parser.add_argument('--no-release', action='store_true',
                      help='stop after the build')
  parser.add_argument('--from', dest='start', choices=STAGES, default='sync',
                      help='skip the stages before this one')
  args = parser.parse_args()

  if not args.no_release:
    print('== AltStore PAL', flush=True)
    publish_pal()

  version = args.version or newest_chrome()
  last = released()
  print(f'Newest Chrome iOS Stable: {version}. Last released: {last}.')
  if version_key(version) <= version_key(last) and not args.force:
    print('Up to date.')
    return

  stages = STAGES[STAGES.index(args.start):]
  if args.no_release:
    stages = [s for s in stages if s != 'upload']
  for stage in stages:
    print(f'\n== {stage}', flush=True)
    {'sync': sync, 'patch': apply_patches, 'polyfills': polyfills,
     'build': lambda v: build(), 'upload': upload}[stage](version)
  if 'upload' in stages:
    print(f'\nStarted the release of Chrome {version}. Follow it with '
          '`gh run watch`.')
  else:
    print(f'\nBuilt {app_path()}, unsigned.')


if __name__ == '__main__':
  main()
