#!/usr/bin/env python3
"""Video Studio launcher.

The application now lives in the `vs_app` package:
  presentation  vs_app/web/video_studio_app.html (served with the per-launch token)
  API           vs_app/main.py + vs_app/api/ (routes, schemas, input policy)
  orchestration vs_app/orchestration/ (jobs, pipeline, flows, resource locks)
  execution     vs_app/execution/ (WSL bridge, tool adapters, parsers, media tools)
  storage       vs_app/storage/ (SQLite jobs + replayable events, event bus)
  core          vs_app/core/ (pure recovery and security rules, no I/O)

Security boundary: loopback bind only, per-launch bearer token, device allowlist,
output sandbox, no shell=True, exact pidfile cancellation.
"""

import argparse
import sys

from vs_app import settings
from vs_app.main import run


def main(argv=None):
    parser = argparse.ArgumentParser(description='Video Studio loopback backend (FastAPI)')
    parser.add_argument('--port', type=int, default=settings.SETTINGS.port)
    parser.add_argument('--distro', default=settings.SETTINGS.wsl_distro)
    parser.add_argument('--open-browser', action='store_true')
    parser.add_argument('--version', action='version', version=settings.APP_VERSION)
    args = parser.parse_args(argv)
    return run(args.port, args.distro, args.open_browser)


if __name__ == '__main__':
    sys.exit(main())
