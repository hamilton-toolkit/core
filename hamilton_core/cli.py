"""`hamilton` command dispatch."""

from __future__ import annotations

import argparse
import sys

from hamilton_core import check as _check
from hamilton_core import guard as _guard
from hamilton_core import init as _init
from hamilton_core import review as _review
from hamilton_core import show as _show
from hamilton_core import status as _status
from hamilton_core import tree as _tree
from hamilton_core import upgrade as _upgrade
from hamilton_core.session.modes import MODES


def _session():
    """Imported on demand: it pulls in the agent SDK, which the read-only
    commands (`check`, `tree`, `show`, `status`, `guard`) have no use for."""
    from hamilton_core.session import loop
    return loop


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="hamilton", description="Hamilton PoC")
    sub = parser.add_subparsers(dest="cmd")

    p_check = sub.add_parser("check", help="run the verification gate")
    p_check.add_argument("ac", nargs="?", default=None,
                         help="only this criterion's status, R-nnnn/ACn "
                              "(the suite is not run)")
    p_check.add_argument("--json", action="store_true",
                         help="machine-readable output for hooks")
    p_check.add_argument("--suite-output", action="store_true",
                         help="stream the test suite's own output as it runs")
    p_init = sub.add_parser("init", help="scaffold a project")
    p_init.add_argument("path", nargs="?", default=None,
                        help="target directory (default: current directory)")
    sub.add_parser("guard", help="phase-gate PreToolUse hook backend")
    p_review = sub.add_parser("review", help="have each unreviewed test judged "
                              "against its criterion; a pass writes its suffix")
    p_review.add_argument("ac", nargs="?", default=None,
                          help="only this criterion: R-nnnn/ACn")
    p_review.add_argument("--json", action="store_true", help="machine-readable output")
    sub.add_parser("build", help="get the gate green: check, plan, write the "
                   "tests, have them reviewed, implement -- a loop Hamilton "
                   "drives, asking only where it cannot proceed")
    for mode in MODES.values():
        sub.add_parser(mode.name, help=mode.help)

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
        return _check.main(as_json=args.json, suite_output=args.suite_output,
                           only=args.ac)
    if args.cmd == "init":
        return _init.main(args.path)
    if args.cmd == "guard":
        return _guard.main()
    if args.cmd == "review":
        return _review.main(args.ac, as_json=args.json)
    if args.cmd == "build":
        from hamilton_core import build as _build
        return _build.main()
    if args.cmd in MODES:
        return _session().main(MODES[args.cmd])
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
