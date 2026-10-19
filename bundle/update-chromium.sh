#!/bin/sh
# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
#
# Builds the polyfill bundle and writes it to where Chromium for iOS injects
# it from.
#
#   WEB_AI_DEMOS=~/src/web-ai-demos ./update-chromium.sh ~/chromium/src

set -eu
SRC=${1:?usage: $0 <chromium/src>}
OUT="$SRC/ios/chrome/browser/built_in_ai/model/resources/built_in_ai_polyfills.js"
cd "$(dirname "$0")"
npm run --silent build >/dev/null
{
  cat <<'HEADER'
// Copyright 2026 The Chromium Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

// GENERATED FILE, do not edit. Built from the Apache-2.0 licensed
// prompt-api-polyfill and built-in-ai-task-apis-polyfills in
// https://github.com/GoogleChromeLabs/web-ai-demos by `update-chromium.sh` in
// https://github.com/tomayac/chromium-ios-built-in-ai.

HEADER
  cat dist/built-in-ai-native.js
} >"$OUT"
echo "Wrote $OUT ($(wc -c <"$OUT" | tr -d ' ') bytes)."
