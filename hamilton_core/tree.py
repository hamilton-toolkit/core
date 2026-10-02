"""The project's working tree as git sees it, without touching git's state.

`snapshot` records every file git would track -- untracked ones included,
ignored ones not -- as a tree object, through an index of its own: the
engineer's index, stash and history are left exactly as they were. Two
snapshots tell whether, and how, a piece of work changed the project.

Outside a git repository there is nothing to record: `snapshot` is None.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile


def _git(root: str, *args, env=None) -> str | None:
    try:
        p = subprocess.run(["git", "-C", root, *args], capture_output=True,
                           text=True, timeout=120, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def snapshot(root: str) -> str | None:
    """The id of a tree object holding the working tree as it is now."""
    index = _git(root, "rev-parse", "--git-path", "index")
    if index is None:
        return None
    with tempfile.TemporaryDirectory(prefix="hamilton-tree-") as tmp:
        own = os.path.join(tmp, "index")
        real = os.path.join(root, index)
        if os.path.isfile(real):
            shutil.copyfile(real, own)      # its stat cache spares rehashing
        env = {**os.environ, "GIT_INDEX_FILE": own}
        if _git(root, "add", "-A", env=env) is None:
            return None
        return _git(root, "write-tree", env=env)


def diff(root: str, before: str, after: str) -> str:
    """What changed from one snapshot to the other, as a unified diff."""
    return _git(root, "diff", "--no-color", before, after) or ""
