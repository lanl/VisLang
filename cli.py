#!/usr/bin/env python3
"""`sieve` — the terminal front-end onto VisLang.

Same engine as the MCP server (`mcp_server.py`); both call cli_core. Every
subcommand prints the same formatted report the MCP tools return, and exits
non-zero when that report signals a failure / hold so the CLI composes in
shell scripts and CI.

    sieve connect gpu-server                      # open an ssh session (password dialog)
    sieve inspect ssh://darwin/path/to/data/     # explore a source's schema
    sieve estimate spec.py                        # static-check + cost, nothing read
    sieve execute spec.py [--confirm]             # run the reduction / transfer
    sieve render-cost heptane_302x302x302.raw     # per-file render payload estimate
    sieve submit-adapter FILE adapter.py          # verify+freeze a reader you wrote
    sieve submit-binding FILE binding.json        # verify+freeze an HDF5 binding
"""
import argparse
import sys

from cli_core import (do_inspect, do_estimate, do_execute,
                      do_estimate_render_cost, do_submit_adapter,
                      do_submit_binding, do_connect, do_disconnect)

# A report is "bad" (non-zero exit) when it starts with one of these markers or
# holds the run. The reports are prose for humans/LLMs; this is the thin machine
# signal a shell needs. `NEEDS CONFIRM`/`NEEDS ALLOCATION`/`NEEDS SESSION` are
# holds, not success.
_BAD_PREFIXES = ("ERROR", "NEEDS_ADAPTER", "NEEDS_SESSION", "ADAPTER REJECTED",
                 "BINDING REJECTED", "BINDING error", "UNREACHABLE",
                 "NOT CONNECTED")
_BAD_STATUSES = ("Status: FAILED", "Status: BUILD FAILED",
                 "Status: NEEDS CONFIRM", "Status: NEEDS ALLOCATION",
                 "Status: NEEDS SESSION")


def _emit(report):
    """Print a report and return the process exit code it implies."""
    print(report)
    bad = (report.startswith(_BAD_PREFIXES)
           or any(report.startswith(s) for s in _BAD_STATUSES))
    return 1 if bad else 0


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def build_parser():
    p = argparse.ArgumentParser(
        prog="sieve",
        description="Read, inspect, narrow, and reduce scientific data with the "
                    "VisLang DSL — the terminal twin of the MCP server.")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="<command>")

    pc = sub.add_parser("connect", help="open an ssh session for a remote host "
                                        "(prompts in a dialog, not here)")
    pc.add_argument("host", help="host, ssh alias, or any remote URI")
    pc.add_argument("--timeout", type=int, default=120,
                    help="seconds to wait for the password dialog (default 120)")

    pd = sub.add_parser("disconnect", help="close the session for a host")
    pd.add_argument("host", help="host, ssh alias, or any remote URI")

    pi = sub.add_parser("inspect", help="read a source's schema (metadata only)")
    pi.add_argument("uri", help="file, folder (=timeseries), or ssh://host/path")
    pi.add_argument("--positions", help='"x,y,z" names of the coordinate variables')

    pe = sub.add_parser("estimate", help="static-check a spec + estimate cost; "
                                         "materializes nothing")
    pe.add_argument("spec", nargs="?", default="spec.py", help="spec file (default spec.py)")

    px = sub.add_parser("execute", help="run a spec's reduction / render / transfer")
    px.add_argument("spec", nargs="?", default="spec.py", help="spec file (default spec.py)")
    px.add_argument("--confirm", action="store_true",
                    help="commit an over-budget run the user has approved")

    pr = sub.add_parser("render-cost", help="per-file render payload + read cost")
    pr.add_argument("filepath")

    pa = sub.add_parser("submit-adapter", help="verify+freeze a reader module you wrote")
    pa.add_argument("filepath", help="the real file the adapter must read")
    pa.add_argument("module", help="path to the adapter .py module")

    pb = sub.add_parser("submit-binding", help="verify+freeze an HDF5 semantic binding")
    pb.add_argument("filepath", help="the HDF5 file the binding describes")
    pb.add_argument("binding", help="path to the binding .json")

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "connect":
            report = do_connect(args.host, timeout=args.timeout)
        elif args.cmd == "disconnect":
            report = do_disconnect(args.host)
        elif args.cmd == "inspect":
            report = do_inspect(args.uri, args.positions)
        elif args.cmd == "estimate":
            report = do_estimate(args.spec)
        elif args.cmd == "execute":
            report = do_execute(args.spec, confirm=args.confirm)
        elif args.cmd == "render-cost":
            report = do_estimate_render_cost(args.filepath)
        elif args.cmd == "submit-adapter":
            report = do_submit_adapter(args.filepath, _read(args.module))
        elif args.cmd == "submit-binding":
            report = do_submit_binding(args.filepath, _read(args.binding))
        else:                                   # unreachable: argparse requires cmd
            build_parser().error(f"unknown command {args.cmd!r}")
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return _emit(report)


if __name__ == "__main__":
    sys.exit(main())
