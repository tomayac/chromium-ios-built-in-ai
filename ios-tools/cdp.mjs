#!/usr/bin/env node
// Copyright 2026 Google LLC
// SPDX-License-Identifier: Apache-2.0

// Drives the Blink build of Chromium for iOS over the DevTools protocol, which
// it serves with `--remote-debugging-port`. In the simulator that port is on
// the Mac's localhost.
//
//   ios-tools/cdp.mjs [--port 9333] [--wait <seconds>] [--screenshot <png>]
//       [--eval <expression>] [<url>]
//
// Loads `url` in the first page, waits until `--eval` returns something other
// than `undefined` (or `--wait` seconds pass), prints that, and saves a
// screenshot if asked.

import { writeFileSync } from 'node:fs';
import { parseArgs } from 'node:util';

const { values, positionals } = parseArgs({
  allowPositionals: true,
  options: {
    port: { type: 'string', default: '9333' },
    wait: { type: 'string', default: '60' },
    eval: { type: 'string' },
    screenshot: { type: 'string' },
  },
});

const base = `http://127.0.0.1:${values.port}`;
const targets = await (await fetch(`${base}/json`)).json();
const page = targets.find((target) => target.type === 'page');
if (!page) {
  throw new Error('No page target.');
}

const socket = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.onopen = resolve;
  socket.onerror = reject;
});
let nextId = 1;
const pending = new Map();
socket.onmessage = ({ data }) => {
  const message = JSON.parse(data);
  if (message.id && pending.has(message.id)) {
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    message.error
      ? reject(new Error(message.error.message))
      : resolve(message.result);
  } else if (message.method === 'Runtime.exceptionThrown') {
    console.error(
      'Exception:',
      message.params.exceptionDetails.exception?.description,
    );
  } else if (message.method === 'Runtime.consoleAPICalled') {
    console.error(
      `console.${message.params.type}:`,
      message.params.args.map((arg) => arg.value ?? arg.description).join(' '),
    );
  }
};
const send = (method, params = {}) =>
  new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });

await send('Runtime.enable');
if (positionals[0]) {
  await send('Page.navigate', { url: positionals[0] });
}

// Many short evaluations: a long one returns nothing when the page reloads.
const deadline = Date.now() + Number(values.wait) * 1000;
let result;
if (values.eval) {
  while (Date.now() < deadline) {
    try {
      const { result: value } = await send('Runtime.evaluate', {
        expression: values.eval,
        awaitPromise: true,
        returnByValue: true,
      });
      if (value.type !== 'undefined') {
        result = value.value;
        break;
      }
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  console.log(
    typeof result === 'string' ? result : JSON.stringify(result, null, 2),
  );
} else {
  await new Promise((resolve) =>
    setTimeout(resolve, Number(values.wait) * 1000),
  );
}

if (values.screenshot) {
  const { data } = await send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(values.screenshot, Buffer.from(data, 'base64'));
}
socket.close();
