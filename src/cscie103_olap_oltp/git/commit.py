# src/cscie103_olap_oltp/git/commit.py
"""Stage and commit, as a task rather than a remembered command line.

    Tasks as Code   no repo operation lives only in someone's shell history

WHY THIS IS THIN, HAVING FIRST BEEN THICK.

A first version refused protected branches and accident-shaped filenames. Both
already exist -- `start:check` runs in the pre-commit hook, and the hygiene gate
refuses files that look like accidents -- so both would have been SECOND copies
of a rule, which is the drift R008 forbids. The hook layer is also the only one
that reliably enforces anything, since it fires no matter how the commit was
started.

Git's maintainer rejects the wider premise directly: preventing a mistaken
`git add` is not a worthy goal when `git reset` undoes it.

SO THIS FIXES EXACTLY ONE THING THE SHELL GETS WRONG.

`git add -A` exits 1 on git 2.52+ whenever an ignored directory exists on disk,
regardless of pathspec exclusions -- git still warns while traversing. This
repository creates `mutants/` on every mutation run and gitignores it, so the
bare command fails intermittently: only when there are untracked changes for it
to sweep up. The documented fix is two steps, neither of which walks an ignored
tree.
"""

from __future__ import annotations

import subprocess
import sys

__all__ = ["stage_commands", "untracked_files"]


def stage_commands(untracked: list[str]) -> list[tuple[str, ...]]:
    """The commands that stage everything, without a full tree walk.

    `git add -u` STAGES TRACKED CHANGES ONLY, so it never reaches `mutants/`.
    The untracked files are then named explicitly, from the one command that
    already respects .gitignore.

    THE `--` IS LOAD-BEARING: without it a file named `-n` is read as an
    option. Filenames come from the filesystem, so they are never trusted as
    syntax.
    """
    commands: list[tuple[str, ...]] = [("git", "add", "-u")]
    if untracked:
        commands.append(("git", "add", "--", *untracked))
    return commands


def untracked_files() -> list[str]:
    """Every untracked path git would add, respecting .gitignore.

    NUL-DELIMITED, because a newline is a legal character in a filename and
    line-splitting silently mangles it. Porcelain output is machine-readable
    only when it is read the machine-readable way.
    """
    completed = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return [path for path in completed.stdout.split("\0") if path]


def main() -> int:
    """Stage everything, then commit with the supplied message."""
    message = " ".join(sys.argv[1:]).strip()
    if not message:
        print("refusing: a commit needs a message", file=sys.stderr)
        return 1

    for command in stage_commands(untracked_files()):
        subprocess.run(list(command), check=True)  # noqa: S603

    # NOT --allow-empty. A commit recording nothing reports success having done
    # nothing, and git already refuses it with a message that says so.
    return subprocess.run(  # noqa: S603
        ["git", "commit", "-m", message],  # noqa: S607
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
