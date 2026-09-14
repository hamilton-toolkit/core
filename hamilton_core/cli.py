"""`hamilton` command dispatch."""

from __future__ import annotations

import argparse
import sys

from hamilton_core import check as _check
from hamilton_core import guard as _guard
from hamilton_core import init as _init
from hamilton_core import launch as _launch
from hamilton_core import show as _show
from hamilton_core import status as _status
from hamilton_core import tree as _tree
from hamilton_core import upgrade as _upgrade


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="hamilton", description="Hamilton PoC")
    sub = parser.add_subparsers(dest="cmd")

    p_check = sub.add_parser("check", help="run the verification gate")
    p_check.add_argument("--json", action="store_true",
                         help="machine-readable output for hooks")
    p_init = sub.add_parser("init", help="scaffold a project")
    p_init.add_argument("path", nargs="?", default=None,
                        help="target directory (default: current directory)")
    sub.add_parser("guard", help="phase-gate PreToolUse hook backend")
    sub.add_parser("design", help="set phase to spec, then launch the agent (agent_command)")
    sub.add_parser("build", help="set phase to build, then launch the agent (agent_command)")
    sub.add_parser("reverse", help="brownfield: set phase to spec, then launch "
                   "the agent to derive a first spec from an existing codebase")

    sub.add_parser("status", help="print a read-only project snapshot (phase, "
                   "counts, coverage, recent spec changes)")

    p_tree = sub.add_parser("tree", help="print the requirement tree (dotted paths, coverage)")
    p_tree.add_argument("--json", action="store_true", help="machine-readable output")

    p_show = sub.add_parser("show", help="print one entity (R or A) and what refers to it")
    p_show.add_argument("id", nargs="?", help="entity id: R-nnnn or A-nnnn")
    p_show.add_argument("--json", action="store_true", help="machine-readable output")

    p_upgrade = sub.add_parser("upgrade", help="bring framework-managed scaffold files up to date")
    p_upgrade.add_argument("path", nargs="?", default=None,
                           help="target directory (default: current directory)")

    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.cmd == "check":
        return _check.main(as_json=args.json)
    if args.cmd == "init":
        return _init.main(args.path)
    if args.cmd == "guard":
        return _guard.main()
    if args.cmd == "design":
        return _launch.main("spec")
    if args.cmd == "build":
        return _launch.main("build")
    if args.cmd == "reverse":
        return _launch.main("spec", verb="reverse", kickoff_key="reverse")
    if args.cmd == "status":
        return _status.main()
    if args.cmd == "tree":
        return _tree.main(as_json=args.json)
    if args.cmd == "show":
        return _show.main(args.id, as_json=args.json)
    if args.cmd == "upgrade":
        return _upgrade.main(args.path)
    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
