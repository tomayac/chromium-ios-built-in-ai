#!/usr/bin/env python3
# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0
"""Serves the test page and records the results it posts.

  serve.py [--port 8000] [--results results.json]

Each POST to /results is written to the results file, so a script can open
the page with `?autorun` in a simulator or on a device and wait for the file.
"""

import argparse
import functools
import http.server
import json
import os


class Handler(http.server.SimpleHTTPRequestHandler):
  results_path = 'results.json'

  def do_POST(self):
    if self.path != '/results':
      self.send_error(404)
      return
    body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
    results = json.loads(body)
    with open(self.results_path, 'w') as f:
      json.dump(results, f, indent=2)
    self.send_response(204)
    self.end_headers()
    for row in results.get('rows', []):
      print(f"{row['check']}: {row['detail']}", flush=True)

  def end_headers(self):
    self.send_header('Cache-Control', 'no-store')
    super().end_headers()


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('--port', type=int, default=8000)
  parser.add_argument('--results', default='results.json')
  args = parser.parse_args()
  Handler.results_path = os.path.abspath(args.results)
  handler = functools.partial(
      Handler, directory=os.path.dirname(os.path.abspath(__file__)))
  http.server.ThreadingHTTPServer(('127.0.0.1', args.port), handler).serve_forever()


if __name__ == '__main__':
  main()
