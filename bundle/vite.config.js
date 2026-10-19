/**
 * Copyright 2026 Google LLC
 * SPDX-License-Identifier: Apache-2.0
 */

import { defineConfig } from 'vite';
import { dirname, resolve } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
// A checkout of https://github.com/GoogleChromeLabs/web-ai-demos with the
// polyfills' `native` backend. Defaults to one next to this repository.
const webAIDemos = resolve(
  process.env.WEB_AI_DEMOS || resolve(__dirname, '../../web-ai-demos'),
);
const polyfill = resolve(webAIDemos, 'prompt-api-polyfill');
const taskPolyfills = resolve(webAIDemos, 'built-in-ai-task-apis-polyfills');

export default defineConfig({
  resolve: {
    alias: [
      {
        find: /^prompt-api-polyfill\/(.*)$/,
        replacement: `${polyfill}/$1`,
      },
      {
        find: /^prompt-api-polyfill$/,
        replacement: `${polyfill}/prompt-api-polyfill.js`,
      },
      {
        find: /^built-in-ai-task-apis-polyfills\/(.*)$/,
        replacement: `${taskPolyfills}/$1`,
      },
    ],
  },
  plugins: [
    {
      name: 'native-backend-only',
      enforce: 'pre',
      resolveId(source, importer) {
        if (
          source === './backends-registry.js' &&
          importer?.startsWith(polyfill)
        ) {
          return resolve(__dirname, 'src/native-registry.js');
        }
        return null;
      },
    },
  ],
  build: {
    target: 'safari16',
    minify: true,
    outDir: 'dist',
    emptyOutDir: true,
    lib: {
      entry: resolve(__dirname, 'src/entry.js'),
      formats: ['iife'],
      name: 'BuiltInAINativeBundle',
      fileName: () => 'built-in-ai-native.js',
    },
    rollupOptions: {
      output: {
        // The built-in AI APIs exist in secure contexts only.
        banner: 'if (globalThis.isSecureContext) {',
        footer: '}',
      },
    },
  },
});
