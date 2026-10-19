/**
 * Copyright 2026 Google LLC
 * SPDX-License-Identifier: Apache-2.0
 */

// Stands in for the polyfill's registry of every backend, so the bundle
// carries the native backend and no SDKs or model runtimes.

import NativeBackend from 'prompt-api-polyfill/backends/native.js';

export const BACKENDS = [
  {
    config: 'NATIVE_CONFIG',
    path: './backends/native.js',
  },
];

export async function getBackendClass(path) {
  if (path === './backends/native.js') {
    return NativeBackend;
  }
  throw new Error(`Unknown backend path "${path}"`);
}
