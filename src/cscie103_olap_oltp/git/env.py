# src/cscie103_olap_oltp/git/env.py
"""A git environment that cannot be redirected by an inherited GIT_DIR.

THE PROBLEM, IN GIT'S OWN WORDS
"Environment variables, such as GIT_DIR, GIT_WORK_TREE, etc., are exported so
that Git commands run by the hook can correctly locate the repository. If your
hook needs to invoke Git commands in a foreign repository or in a different
working tree of the same repository, then it should clear these environment
variables so they do not interfere with Git operations at the foreign location."

Those variables OVERRIDE both `-C` and `cwd`. Any git call made from inside a
hook therefore operates on the hook's repository regardless of where it was
pointed.

WHY THIS REPOSITORY IS EXPOSED. Its gates run under pre-commit and pre-push, and
`worktree:add` will create linked worktrees. Today a hook's repository and the
project root coincide, so a raw subprocess call works BY COINCIDENCE. From a
worktree they differ, and a gate would read a different index entirely --
passing while inspecting the wrong repository. In the sibling project the same
inheritance let a test fixture's `git commit` write into the real repository,
and the stray commit was pushed.

NAMESPACED AS cscie103_olap_oltp.git, WHICH IS SAFE, AND A FLAT scripts/git/
WOULD NOT BE. A top-level directory named `git` on sys.path shadows the `git`
module GitPython installs -- the documented failure where a file named like a
real module is imported instead of it. Inside an installed package there is no
such collision, which is one of the things the src layout buys.

WHY `git rev-parse --local-env-vars` RATHER THAN A HARDCODED LIST
It is git's own enumeration of the repository-routing variables, so it stays
correct when git adds one. A literal list is a second copy of something git
already owns, and it goes stale silently -- the same argument this repository
makes about ci.yml restating the gate list.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _local_env_var_names() -> list[str] | None:
    """Git's own enumeration of the routing variables, or None if it cannot say.

    NO noqa NEEDED: the argument list is entirely literal, so S603 does not
    fire. The asymmetry with git() below is ruff distinguishing a fixed command
    from a computed one, and suppressing both would hide that distinction.
    """
    listing = subprocess.run(
        ["git", "rev-parse", "--local-env-vars"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return listing.stdout.split() if listing.returncode == 0 else None


def scrubbed_env() -> dict[str, str]:
    """A copy of the environment with git's repository-routing variables removed.

    FAILS CLOSED ON THE FALLBACK. If git cannot enumerate the variables, every
    GIT_ name is removed rather than a guessed subset: over-removing makes git
    resolve from cwd, which is exactly what the caller asked for. Under-removing
    leaves the hijack in place.
    """
    environment = dict(os.environ)
    names = _local_env_var_names()

    if names is None:
        return {key: value for key, value in environment.items() if not key.startswith("GIT_")}

    for name in names:
        environment.pop(name, None)

    return environment


def routing_overrides_present() -> dict[str, str]:
    """Which routing variables are set right now, for diagnostics.

    A hijack that is silently corrected is still worth being able to see. Four
    CI failures in the sibling project were hard to diagnose precisely because
    nothing could answer "which variables are in play right now".
    """
    names = _local_env_var_names()
    if names is None:
        names = [key for key in os.environ if key.startswith("GIT_")]
    return {name: os.environ[name] for name in names if name in os.environ}


def git(*args: str, cwd: str | Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run git with a scrubbed environment, resolving the repository from cwd.

    check=False DELIBERATELY. Callers inspect returncode and stderr to decide
    what a failure MEANS -- "branch does not exist" and "not a repository" call
    for different responses, and an exception collapses both into a traceback.

    S603 IS SUPPRESSED NARROWLY: `*args` makes the list computed, which is what
    ruff is flagging. Every call site in this project passes git subcommands and
    refs as separate literal arguments, there is no shell, and nothing is
    interpolated from user input.
    """
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
        env=scrubbed_env(),
    )
