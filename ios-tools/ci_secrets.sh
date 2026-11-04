#!/bin/sh
# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
#
# Sets the repository secrets the release workflow needs:
#
#   ASC_API_KEY             the App Store Connect API key
#   SIGNING_P12             the Apple Distribution identity of the signing
#   SIGNING_P12_PASSWORD    keychain of `asc_provision.py`, and its password
#   TESTFLIGHT_JSON         `ios-tools/testflight.json`
#
#   ios-tools/ci_secrets.sh [key id]
#
# Only the identity leaves the keychain, in a temporary directory that this
# deletes again. Run it again after `asc_provision.py` creates a new
# distribution certificate.

set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
KEY_ID=${1:-B78MTCSY2V}
KEYCHAIN="$HOME/Library/Keychains/chromium-signing.keychain-db"
OPENSSL=/opt/homebrew/bin/openssl
REPO=tomayac/chromium-ios-built-in-ai

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
chmod 700 "$WORK"

security unlock-keychain -p "$(cat "$HERE/.secrets/keychain-password")" \
  "$KEYCHAIN"
EXPORT_PASSWORD=$("$OPENSSL" rand -hex 16)
security export -k "$KEYCHAIN" -t identities -f pkcs12 -P "$EXPORT_PASSWORD" \
  -o "$WORK/all.p12"
"$OPENSSL" pkcs12 -legacy -in "$WORK/all.p12" -passin "pass:$EXPORT_PASSWORD" \
  -nodes -out "$WORK/all.pem" 2>/dev/null

# The keychain also holds the development identity, which stays here.
python3 - "$WORK" <<'EOF'
import re, sys
work = sys.argv[1]
bags = re.findall(r'Bag Attributes.*?-----END [A-Z ]+-----\n',
                  open(f'{work}/all.pem').read(), re.S)
def key_id(bag):
  return re.search(r'localKeyID: ([0-9A-F ]+)', bag).group(1).strip()
certificate = next(b for b in bags
                   if 'CERTIFICATE' in b and 'Apple Distribution' in b)
key = next(b for b in bags
           if 'PRIVATE KEY' in b and key_id(b) == key_id(certificate))
open(f'{work}/distribution.pem', 'w').write(certificate + key)
EOF

P12_PASSWORD=$("$OPENSSL" rand -hex 16)
"$OPENSSL" pkcs12 -export -legacy -in "$WORK/distribution.pem" \
  -out "$WORK/distribution.p12" -passout "pass:$P12_PASSWORD"

base64 -i "$WORK/distribution.p12" | gh secret set SIGNING_P12 --repo "$REPO"
printf %s "$P12_PASSWORD" | gh secret set SIGNING_P12_PASSWORD --repo "$REPO"
gh secret set ASC_API_KEY --repo "$REPO" \
  <"$HOME/.appstoreconnect/private_keys/AuthKey_$KEY_ID.p8"
gh secret set TESTFLIGHT_JSON --repo "$REPO" <"$HERE/testflight.json"
echo "Set the release secrets of $REPO."
