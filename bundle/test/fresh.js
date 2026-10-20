// Copyright 2026 Google LLC
// SPDX-License-Identifier: Apache-2.0

// Whether this page load is a restored tab of an earlier test run. Automated
// runs open test pages with `?v=<seconds since the epoch>`; a browser that
// restores its tabs on launch loads them again with that old value, and they
// must not run their tests again then. Pages opened without `v` always run.
export function isRestoredRun(maxAgeSeconds = 45) {
  const v = Number(new URLSearchParams(location.search).get('v'));
  return Boolean(v) && Date.now() / 1000 - v > maxAgeSeconds;
}

// Stops a test page that is a restored tab, saying so on the page.
export function stopIfRestored() {
  if (!isRestoredRun()) {
    return false;
  }
  const note = document.createElement('p');
  note.textContent = 'Not run: this tab was restored from an earlier test run.';
  document.body.prepend(note);
  document.title += ' (restored, not run)';
  return true;
}
