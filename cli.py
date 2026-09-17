#!/usr/bin/env python3
"""Launcher for the `sieve` terminal CLI. The CLI itself is
`vislang/server/cli.py`.

This file exists so the `sieve` shell script can keep invoking a stable path at
the repo root, including through a symlink on your PATH.
"""
import sys

from vislang.server.cli import main

if __name__ == "__main__":
    sys.exit(main())
