#!/bin/sh
# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
#
# Benchmarks Gemma on a device, once per configuration: relaunches Chromium
# with developer launch arguments, opens the benchmark page, and waits for the
# stats line the browser writes after the answer.
#
#   ios-tools/bench-device.sh <udid> <bundle id> <config>...
#
# Each config is a backend, optionally with a CPU thread count: `auto`,
# `gpu`, `cpu`, `cpu:2`, `cpu:6`.

set -eu
UDID=${1:?usage: $0 <udid> <bundle id> <config>...}
BUNDLE_ID=${2:?usage: $0 <udid> <bundle id> <config>...}
shift 2
URL=${BENCH_URL:-chromiums://tomayac.github.io/chromium-ios-built-in-ai/bench.html}
STATS=Library/Caches/BuiltInAI/stats.jsonl
WORK=$(mktemp -d)

stats_lines() {
  rm -f "$WORK/stats.jsonl"
  xcrun devicectl device copy from --device "$UDID" --domain-type appDataContainer \
    --domain-identifier "$BUNDLE_ID" --source "$STATS" --destination "$WORK/stats.jsonl" \
    >/dev/null 2>&1 || true
  [ -f "$WORK/stats.jsonl" ] && wc -l <"$WORK/stats.jsonl" | tr -d ' ' || echo 0
}

for config in "$@"; do
  backend=${config%%:*}
  threads=0
  [ "$backend" != "$config" ] && threads=${config#*:}
  before=$(stats_lines)
  echo "== $config"
  # The engine is loaded once per process, so every configuration gets a
  # fresh one.
  xcrun devicectl device process launch --device "$UDID" --terminate-existing \
    --payload-url "$URL" "$BUNDLE_ID" -- \
    -BuiltInAIGemmaBackend "$backend" -BuiltInAIGemmaThreads "$threads" \
    -BuiltInAIKeepAwake YES >/dev/null
  deadline=$(($(date +%s) + 900))
  while [ "$(stats_lines)" -le "$before" ]; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
      echo "   no stats after 15 minutes (crashed or killed?)"
      break
    fi
    sleep 10
  done
  tail -1 "$WORK/stats.jsonl" 2>/dev/null | sed 's/^/   /'
done
