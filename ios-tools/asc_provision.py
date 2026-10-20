#!/usr/bin/env python3
# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Provisions a Chromium for iOS device build through the App Store Connect API.

No Xcode UI involved. Given an App Store Connect API key, this:

1. creates an Apple Development certificate, with its private key in a keychain
   of its own (`chromium-signing.keychain-db`) that codesign can use without
   prompting,
2. registers the device,
3. creates an explicit App ID with the capabilities in `CAPABILITIES`,
4. creates a development provisioning profile for the three, and
5. writes `signing.json` with the GN args that make the build use them.

Every step reuses what already exists, so running it again is cheap.

Usage:
  asc_provision.py --key-id ABC123 --issuer-id 69a6... --key ~/AuthKey_ABC123.p8 \
      --udid 00008030-001661A83C12802E --prefix com.example.chromium
"""

import argparse
import base64
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

API = 'https://api.appstoreconnect.apple.com/v1'
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, 'signing.json')
SECRETS = os.path.join(HERE, '.secrets')
KEYCHAIN = os.path.expanduser(
    '~/Library/Keychains/chromium-signing.keychain-db')
WWDR_URL = 'https://www.apple.com/certificateauthority/AppleWWDRCAG3.cer'
PROFILES = os.path.expanduser('~/Library/MobileDevice/Provisioning Profiles')
OPENSSL = next((p for p in ('/opt/homebrew/bin/openssl', '/usr/bin/openssl')
                if os.path.exists(p)), 'openssl')

# App ID capabilities to enable. `Chrome+Minimal.entitlements` needs none. The
# API offers no memory capabilities (increased memory limit, extended virtual
# addressing); those are checkboxes on the App ID in the developer portal.
CAPABILITIES = []


def run(*args, **kwargs):
  return subprocess.run(args, check=True, capture_output=True, **kwargs).stdout


# --- App Store Connect API ---------------------------------------------------


def b64url(data):
  return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def der_to_raw(der):
  """Converts an ECDSA signature from DER to the r || s form JWS uses."""
  assert der[0] == 0x30
  index = 2 if der[1] < 0x80 else 2 + (der[1] & 0x7F)
  parts = []
  for _ in range(2):
    assert der[index] == 0x02
    length = der[index + 1]
    value = der[index + 2:index + 2 + length]
    parts.append(value.lstrip(b'\0').rjust(32, b'\0'))
    index += 2 + length
  return parts[0] + parts[1]


class AppStoreConnect:

  def __init__(self, key_id, issuer_id, key_path):
    self.key_id = key_id
    self.issuer_id = issuer_id
    self.key_path = key_path
    self._token = None
    self._expires = 0

  def token(self):
    now = int(time.time())
    if self._token and now < self._expires - 60:
      return self._token
    header = b64url(json.dumps(
        {'alg': 'ES256', 'kid': self.key_id, 'typ': 'JWT'}).encode())
    payload = b64url(json.dumps({
        'iss': self.issuer_id,
        'iat': now,
        'exp': now + 1200,
        'aud': 'appstoreconnect-v1',
    }).encode())
    signing_input = f'{header}.{payload}'.encode()
    der = run(OPENSSL, 'dgst', '-sha256', '-sign', self.key_path,
              input=signing_input)
    self._token = f'{header}.{payload}.{b64url(der_to_raw(der))}'
    self._expires = now + 1200
    return self._token

  def request(self, method, path, body=None):
    request = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            'Authorization': f'Bearer {self.token()}',
            'Content-Type': 'application/json',
        })
    try:
      with urllib.request.urlopen(request) as response:
        text = response.read()
        return json.loads(text) if text else {}
    except urllib.error.HTTPError as error:
      detail = error.read().decode(errors='replace')
      sys.exit(f'{method} {path}: HTTP {error.code}\n{detail}')

  def get(self, path):
    return self.request('GET', path)

  def post(self, path, body):
    return self.request('POST', path, body)


# --- Keychain ---------------------------------------------------------------


def keychain_password():
  os.makedirs(SECRETS, mode=0o700, exist_ok=True)
  path = os.path.join(SECRETS, 'keychain-password')
  if not os.path.exists(path):
    with open(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600), 'w') as f:
      f.write(secrets.token_urlsafe(24))
  with open(path) as f:
    return f.read().strip()


def ensure_keychain():
  password = keychain_password()
  if not os.path.exists(KEYCHAIN):
    run('security', 'create-keychain', '-p', password, KEYCHAIN)
    # Stays unlocked; it holds nothing but this signing identity.
    run('security', 'set-keychain-settings', KEYCHAIN)
  run('security', 'unlock-keychain', '-p', password, KEYCHAIN)
  # codesign only finds identities in keychains on the search list. Keep
  # every keychain that is already there.
  listed = [
      line.strip().strip('"') for line in run(
          'security', 'list-keychains', '-d', 'user').decode().splitlines()
      if line.strip()
  ]
  if KEYCHAIN not in listed:
    run('security', 'list-keychains', '-d', 'user', '-s', *listed, KEYCHAIN)
  with tempfile.TemporaryDirectory() as tmp:
    wwdr = os.path.join(tmp, 'wwdr.cer')
    urllib.request.urlretrieve(WWDR_URL, wwdr)
    subprocess.run(['security', 'import', wwdr, '-k', KEYCHAIN],
                   capture_output=True)
  return password


# Certificate types, and the name their identities start with.
IDENTITY_NAMES = {
    'DEVELOPMENT': 'Apple Development',
    'DISTRIBUTION': 'Apple Distribution',
}


def find_identity(certificate_type='DEVELOPMENT'):
  """The SHA-1 of a valid identity of `certificate_type` in the keychain."""
  output = run('security', 'find-identity', '-v', '-p', 'codesigning',
               KEYCHAIN).decode()
  for line in output.splitlines():
    parts = line.split()
    if len(parts) > 2 and f'"{IDENTITY_NAMES[certificate_type]}' in line:
      return parts[1]
  return None


def ensure_certificate(asc, password, certificate_type='DEVELOPMENT'):
  identity = find_identity(certificate_type)
  if identity:
    print(f'Signing identity {identity} already present.')
    return identity

  with tempfile.TemporaryDirectory() as tmp:
    key = os.path.join(tmp, 'key.pem')
    csr = os.path.join(tmp, 'request.csr')
    run(OPENSSL, 'req', '-new', '-newkey', 'rsa:2048', '-nodes', '-keyout',
        key, '-out', csr, '-subj',
        f'/CN=Chromium iOS {IDENTITY_NAMES[certificate_type]}')
    with open(csr) as f:
      csr_content = f.read()
    created = asc.post('/certificates', {
        'data': {
            'type': 'certificates',
            'attributes': {
                'certificateType': certificate_type,
                'csrContent': csr_content,
            },
        }
    })
    der = base64.b64decode(created['data']['attributes']['certificateContent'])
    cer = os.path.join(tmp, 'cert.cer')
    with open(cer, 'wb') as f:
      f.write(der)
    pem = os.path.join(tmp, 'cert.pem')
    run(OPENSSL, 'x509', '-inform', 'DER', '-in', cer, '-out', pem)
    p12 = os.path.join(tmp, 'identity.p12')
    p12_password = secrets.token_urlsafe(16)
    legacy = ['-legacy'] if 'homebrew' in OPENSSL else []
    run(OPENSSL, 'pkcs12', '-export', *legacy, '-inkey', key, '-in', pem,
        '-out', p12, '-passout', f'pass:{p12_password}')
    run('security', 'import', p12, '-k', KEYCHAIN, '-P', p12_password, '-T',
        '/usr/bin/codesign', '-T', '/usr/bin/security')
  # Lets codesign use the key without asking.
  run('security', 'set-key-partition-list', '-S', 'apple-tool:,apple:,codesign:',
      '-s', '-k', password, KEYCHAIN)
  identity = find_identity(certificate_type)
  if not identity:
    sys.exit('The new certificate did not show up as a valid identity.')
  print(f'Created signing identity {identity}.')
  return identity


def certificate_id(asc, identity, certificate_type='DEVELOPMENT'):
  """The App Store Connect id of the certificate behind `identity`."""
  pem = run('security', 'find-certificate', '-a', '-Z', '-p', KEYCHAIN).decode()
  # Match by serial number, which App Store Connect reports.
  serials = set()
  for block in pem.split('-----END CERTIFICATE-----'):
    if '-----BEGIN CERTIFICATE-----' not in block:
      continue
    if identity.upper() not in block.upper():
      continue
    certificate = block[block.index('-----BEGIN CERTIFICATE-----'):] + (
        '-----END CERTIFICATE-----\n')
    serial = run(OPENSSL, 'x509', '-noout', '-serial',
                 input=certificate.encode()).decode()
    serials.add(serial.strip().split('=')[1].upper().lstrip('0'))
  listing = asc.get(f'/certificates?filter[certificateType]={certificate_type}'
                    '&limit=200')
  for certificate in listing['data']:
    serial = certificate['attributes']['serialNumber'].upper().lstrip('0')
    if serial in serials:
      return certificate['id']
  sys.exit(f'No App Store Connect certificate matches identity {identity}.')


# --- Devices, App IDs, profiles ---------------------------------------------


def ensure_device(asc, udid, name):
  listing = asc.get(f'/devices?filter[udid]={udid}')
  if listing['data']:
    print(f'Device {udid} already registered.')
    return listing['data'][0]['id']
  created = asc.post('/devices', {
      'data': {
          'type': 'devices',
          'attributes': {
              'name': name,
              'udid': udid,
              'platform': 'IOS',
          },
      }
  })
  print(f'Registered device {udid}.')
  return created['data']['id']


def ensure_bundle_id(asc, identifier, name):
  listing = asc.get(f'/bundleIds?filter[identifier]={identifier}')
  matches = [b for b in listing['data']
             if b['attributes']['identifier'] == identifier]
  if matches:
    bundle = matches[0]
    print(f'App ID {identifier} already exists.')
  else:
    bundle = asc.post('/bundleIds', {
        'data': {
            'type': 'bundleIds',
            'attributes': {
                'identifier': identifier,
                'name': name,
                'platform': 'IOS',
            },
        }
    })['data']
    print(f'Created App ID {identifier}.')
  enabled = {
      c['attributes']['capabilityType']
      for c in asc.get(f'/bundleIds/{bundle["id"]}/bundleIdCapabilities')['data']
  }
  for capability in CAPABILITIES:
    if capability in enabled:
      continue
    asc.post('/bundleIdCapabilities', {
        'data': {
            'type': 'bundleIdCapabilities',
            'attributes': {'capabilityType': capability},
            'relationships': {
                'bundleId': {'data': {'type': 'bundleIds', 'id': bundle['id']}},
            },
        }
    })
    print(f'Enabled {capability}.')
  return bundle['id']


def create_profile(asc, name, bundle_id, certificate, device,
                   profile_type='IOS_APP_DEVELOPMENT'):
  # A profile is fixed to its certificates and devices, so a fresh one is
  # created each time and the one it replaces deleted.
  existing = asc.get(f'/profiles?filter[name]={urllib.request.quote(name)}')
  for profile in existing['data']:
    asc.request('DELETE', f'/profiles/{profile["id"]}')
  created = asc.post('/profiles', {
      'data': {
          'type': 'profiles',
          'attributes': {
              'name': name,
              'profileType': profile_type,
          },
          'relationships': {
              'bundleId': {'data': {'type': 'bundleIds', 'id': bundle_id}},
              'certificates': {
                  'data': [{'type': 'certificates', 'id': certificate}]
              },
              # App Store profiles list no devices.
              'devices': {
                  'data': [{'type': 'devices', 'id': device}] if device else []
              },
          },
      }
  })['data']
  content = base64.b64decode(created['attributes']['profileContent'])
  os.makedirs(PROFILES, exist_ok=True)
  path = os.path.join(PROFILES, f'{created["attributes"]["uuid"]}.mobileprovision')
  with open(path, 'wb') as f:
    f.write(content)
  print(f'Installed profile {path}.')
  return path


def main():
  parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
  parser.add_argument('--key-id', required=True)
  parser.add_argument('--issuer-id', required=True)
  parser.add_argument('--key', required=True, help='AuthKey_<id>.p8')
  parser.add_argument('--udid', required=True)
  parser.add_argument('--device-name', default='iPhone')
  parser.add_argument('--prefix', required=True,
                      help='ios_app_bundle_id_prefix, such as com.example.chromium')
  args = parser.parse_args()
  # How //ios/build/chrome_build.gni composes Chromium's bundle id.
  bundle_identifier = f'{args.prefix}.chrome.ios.dev'

  asc = AppStoreConnect(args.key_id, args.issuer_id,
                        os.path.expanduser(args.key))
  password = ensure_keychain()
  identity = ensure_certificate(asc, password)
  certificate = certificate_id(asc, identity)
  device = ensure_device(asc, args.udid, args.device_name)
  bundle = ensure_bundle_id(asc, bundle_identifier, 'Chromium Built-in AI')
  profile = create_profile(asc, 'Chromium Built-in AI Development', bundle,
                           certificate, device)

  state = {
      'identity': identity,
      'profile': profile,
      'bundle_id': bundle_identifier,
      'gn_args': {
          'ios_code_signing_identity': identity,
          'ios_code_signing_identity_description': '',
          'ios_app_bundle_id_prefix': args.prefix,
          'ios_mobileprovision_files': [profile],
          'ios_chrome_minimal_entitlements': True,
      },
  }
  with open(STATE, 'w') as f:
    json.dump(state, f, indent=2)
  print(f'Wrote {STATE}.')


if __name__ == '__main__':
  main()
