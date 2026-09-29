"""`hamilton run` -- start the whole software for the engineer to try it.

Runs `start_command` from `.hamilton/config` in the project root, in the
foreground, with the terminal handed over: its output is the engineer's to
read, and Ctrl+C stops it. `hamilton build` sets the key when it is missing;
the engineer may set it too. It ignores the phase and writes nothing -- it is
what the engineer keeps running in a second terminal while `hamilton
validate` takes their findings.

Exit: the command's own exit code, 2 when the key is unset or this is not a
project, 130 if interrupted.
"""

from __future__ import annotations

import os
import subprocess
import sys

from hamilton_core import verify as _verify


def main() -> int:
    root = os.getcwd()
    try:
        command = _verify.start_command(_verify.read_config(root))
    except _verify.UsageError as exc:
        print(f"hamilton run: {exc}", file=sys.stderr)
        return 2
    if not command:
        print(f"hamilton run: {_verify.CONFIG_REL} has no "
              f"{_verify.START_KEY} -- `hamilton build` sets it, or set it "
              f"yourself: the command that starts the whole software, e.g. "
              f"`{_verify.START_KEY}=docker compose up`.", file=sys.stderr)
        return 2
    print(f"hamilton run: {command}  (Ctrl+C stops it)", file=sys.stderr, flush=True)
    proc = subprocess.Popen(command, shell=True, cwd=root)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        # The child had the Ctrl+C too, being in the same process group:
        # give it the time to shut down that it asks for.
        try:
            return proc.wait() or 130
        except KeyboardInterrupt:
            proc.kill()
            return 130
