#!/usr/bin/env python3
"""Launcher for the VisLang remote reducer. The executor itself is
`vislang/remote/executor.py`.

This file exists so the command run over ssh stays a stable path
(`python <repo>/vislang_exec.py --plan …`) regardless of where the package
moves. `remote/reduce.py` builds that command and `remote/hosts.py` probes for
this file, so a remote checkout that is a few commits behind still works.
"""
import sys

from vislang.remote.executor import main

if __name__ == "__main__":
    sys.exit(main())
