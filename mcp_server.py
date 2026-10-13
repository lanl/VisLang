#!/usr/bin/env python3
"""Launcher for the VisLang MCP server. The server itself is
`vislang/server/mcp_server.py`.

This file exists so `.mcp.json` can keep pointing at a stable path at the repo
root. Running it as a script puts the repo root on sys.path, so `vislang.*`
imports resolve without the package being installed.
"""
import sys

from vislang.server.mcp_server import mcp

if __name__ == "__main__":
    sys.exit(mcp.run())
