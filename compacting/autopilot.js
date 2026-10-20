// Copyright 2026 Google LLC
// SPDX-License-Identifier: Apache-2.0

// Drives the session compacting demo of web-ai-demos like a user would: fills
// the session with messages in several languages, compacts it, and asks one
// more question, then reports what happened. Only runs with `?auto`.

import { isRestoredRun } from '../fresh.js';

if (new URLSearchParams(location.search).has('auto') && !isRestoredRun()) {
  const $ = (selector) => document.querySelector(selector);
  const status = () => $('#status').textContent;
  const banner = document.createElement('div');
  banner.style.cssText =
    'position:sticky;top:0;z-index:9;padding:8px;font:600 15px system-ui;' +
    'background:#1a73e8;color:white';
  banner.textContent = 'Autopilot: waiting for the model…';
  document.body.prepend(banner);
  const steps = [];
  const started = performance.now();
  const note = (text) => {
    steps.push({ at: Math.round(performance.now() - started), text });
    banner.textContent = `Autopilot: ${text}`;
  };

  const until = async (condition, ms = 600000) => {
    const deadline = Date.now() + ms;
    while (!condition()) {
      if (Date.now() > deadline) {
        throw new Error(`timed out; status: ${status()}`);
      }
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  };
  const idle = () => !$('#send-btn').disabled;

  const send = async (text) => {
    await until(idle);
    const before = document.querySelectorAll('.msg.assistant').length;
    $('#user-input').value = text;
    $('#send-btn').click();
    await until(
      () => document.querySelectorAll('.msg.assistant').length > before,
    );
    await until(idle);
    const answers = document.querySelectorAll('.msg.assistant span:last-child');
    return answers[answers.length - 1].textContent;
  };

  const messages = [
    'Explain in three sentences how photosynthesis works.',
    'Write a short Python function that reverses a string. Put it in a code block.',
    'Erkläre in zwei Sätzen, warum der Himmel blau ist.',
    'Donne-moi trois conseils pour bien dormir.',
    'What was the first topic I asked you about? One word.',
  ];

  (async () => {
    try {
      await until(() => status() === 'Ready.');
      note('ready');
      for (const [i, message] of messages.entries()) {
        const answer = await send(message);
        note(`message ${i + 1}: ${answer.slice(0, 60).replace(/\s+/g, ' ')}`);
      }
      const before = $('#token-label').textContent;
      $('#compact-btn').click();
      await until(() => /compacted|failed/i.test(status()) && idle());
      note(
        `compacted: ${status()}; before ${before}; now ${$('#token-label').textContent}`,
      );
      const after = await send(
        'Which topics have we covered so far? List them briefly.',
      );
      note(`after compaction: ${after.slice(0, 120).replace(/\s+/g, ' ')}`);
      note('done');
      document.title = 'Compacting autopilot: done';
    } catch (error) {
      note(`failed: ${error.message}`);
      document.title = 'Compacting autopilot: failed';
    }
    const errors = [...document.querySelectorAll('.msg')]
      .map((m) => m.textContent)
      .filter((t) => /error/i.test(t));
    const log = $('#compact-log').textContent.replace(/\s+/g, ' ');
    fetch('/results', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        env: navigator.userAgent,
        rows: steps.map((s) => ({ check: `${s.at} ms`, detail: s.text })),
        errors,
        compactLog: log,
      }),
    }).catch(() => {});
  })();
}
