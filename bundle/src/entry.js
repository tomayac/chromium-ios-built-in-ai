/**
 * Copyright 2026 Google LLC
 * SPDX-License-Identifier: Apache-2.0
 */

// The built-in AI APIs for a browser to inject into every frame, at document
// start, in the page's own world. `LanguageModel` talks to the browser's
// on-device models through the `native` backend, and the task APIs are built
// on `LanguageModel`. APIs the browser implements natively are left alone.

import './config.js';
import 'prompt-api-polyfill';
import 'built-in-ai-task-apis-polyfills/summarizer-api-polyfill.js';
import 'built-in-ai-task-apis-polyfills/writer-api-polyfill.js';
import 'built-in-ai-task-apis-polyfills/rewriter-api-polyfill.js';
import 'built-in-ai-task-apis-polyfills/language-detector-api-polyfill.js';
import 'built-in-ai-task-apis-polyfills/translator-api-polyfill.js';
