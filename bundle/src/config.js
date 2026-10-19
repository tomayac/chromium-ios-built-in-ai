/**
 * Copyright 2026 Google LLC
 * SPDX-License-Identifier: Apache-2.0
 */

// Runs before the polyfills, which pick their backend when they load. The
// browser that injects this bundle provides the bridge.
globalThis.NATIVE_CONFIG ??= { bridge: '__builtInAINativeBridge' };
