# src/cscie103_olap_oltp/git/state.py
"""The repository's branch and worktree state, as a validated snapshot.

WHY A STATE MODEL AND NOT A PIPELINE
The obvious `sync` deletes merged branches by piping `git for-each-ref` through
awk. It works until a worktree holds one of those branches, at which point git
refuses -- correctly -- and the task aborts mid-way, leaving the sync half done
after the pull had already succeeded.

The bug is not the missing filter. It is that cleanup has no model of what it is
cleaning: it knows "upstream is gone" and nothing else, so every other fact
about a branch is invisible.

WHAT THE MODEL MAKES EXPLICIT
  is_protected     develop and main are never deleted, whatever their state
  held_by          the linked worktree holding it, if any -- one branch, one
                   worktree, and git enforces that
  upstream_gone    the remote branch was deleted, usually by a merge
  is_merged        its commits are in develop

Deletability is then a property of the state rather than a guess, and the reason
a branch is NOT deletable is answerable instead of inferred from an error.

WHY --porcelain -z EVERYWHERE
Human-readable git output is explicitly not a stable interface, and porcelain
does not quote paths -- so a path containing a newline corrupts a line-based
parser. The docs recommend combining --porcelain with -z for that reason.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from cscie103_olap_oltp.git.env import scrubbed_env

PROTECTED_BRANCHES = frozenset({"develop", "main"})
INTEGRATION_BRANCH = "develop"


class Worktree(BaseModel):
    """A checkout: the main clone or a linked worktree."""

    model_config = ConfigDict(frozen=True)

    path: Path
    branch: str | None
    is_main: bool
    is_dirty: bool

    @property
    def is_linked(self) -> bool:
        """The canonical predicate.

        A linked worktree cannot check out a branch another worktree already
        holds, and the integration branch is normally held by the main clone.
        """
        return not self.is_main


class Branch(BaseModel):
    """A local branch and everything that decides whether it may be removed."""

    model_config = ConfigDict(frozen=True)

    name: str
    upstream_gone: bool
    is_merged: bool
    held_by: Path | None

    @property
    def is_protected(self) -> bool:
        return self.name in PROTECTED_BRANCHES

    @property
    def is_deletable(self) -> bool:
        """EVERY condition, not just the one that happened to fail last."""
        return (
            not self.is_protected and self.upstream_gone and self.is_merged and self.held_by is None
        )

    @property
    def blocked_because(self) -> str | None:
        """Why this branch is not deletable, in the words a person needs.

        An error message from git says a delete failed. This says which
        condition holds and what to do about it.

        PROTECTION IS REPORTED FIRST. Telling someone `main` is "not merged into
        develop" is technically true and actively misleading about why it was
        skipped.
        """
        if self.is_protected:
            return "protected branch"
        if not self.upstream_gone:
            return "upstream still exists; it has not been merged and deleted"
        if not self.is_merged:
            return f"not merged into {INTEGRATION_BRANCH}"
        if self.held_by is not None:
            return (
                f"checked out in a worktree at {self.held_by}. "
                "Remove the worktree first: mise run worktree:remove <slug>"
            )
        return None


class RepositoryState(BaseModel):
    """Everything cleanup needs to decide, gathered once."""

    model_config = ConfigDict(frozen=True)

    worktrees: tuple[Worktree, ...]
    branches: tuple[Branch, ...]

    @property
    def deletable(self) -> tuple[Branch, ...]:
        return tuple(branch for branch in self.branches if branch.is_deletable)

    @property
    def blocked(self) -> tuple[Branch, ...]:
        """Branches whose upstream is gone but which cannot be removed yet.

        REPORTED RATHER THAN SILENTLY SKIPPED: a cleanup that quietly leaves
        something behind is the failure signal for this whole class of bug.

        Protected branches are excluded because reporting `develop` as blocked
        on every run is noise that trains people to stop reading the output.
        """
        return tuple(
            branch
            for branch in self.branches
            if branch.upstream_gone and not branch.is_deletable and not branch.is_protected
        )


def _git(*args: str, cwd: Path | None = None) -> str:
    """Text git output, or raise. S603 is suppressed for the computed list."""
    result = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
        env=scrubbed_env(),
    )
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _git_bytes(*args: str, cwd: Path | None = None) -> bytes:
    """Raw git output, for -z formats where the separator is a NUL byte."""
    result = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        capture_output=True,
        check=False,
        cwd=cwd,
        env=scrubbed_env(),
    )
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed")
    return result.stdout


def _worktrees(root: Path) -> tuple[Worktree, ...]:
    """Every checkout, main first.

    THE FIRST RECORD IS THE MAIN WORKTREE. git documents that ordering, and it
    is what distinguishes the clone from its linked worktrees without parsing
    paths or guessing from the directory name.
    """
    raw = _git_bytes("worktree", "list", "--porcelain", "-z", cwd=root)

    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for token in raw.split(b"\0"):
        if token == b"":
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = token.decode("utf-8").partition(" ")
        current[key] = value
    if current:
        records.append(current)

    worktrees: list[Worktree] = []
    for index, record in enumerate(records):
        path = Path(record["worktree"])
        status = _git_bytes("status", "--porcelain", "-z", cwd=path)
        worktrees.append(
            Worktree(
                path=path,
                branch=record.get("branch", "").removeprefix("refs/heads/") or None,
                is_main=index == 0,
                is_dirty=bool(status.strip(b"\0")),
            )
        )
    return tuple(worktrees)


def gather(root: Path) -> RepositoryState:
    """Read the repository's state once, from plumbing only.

    for-each-ref, NOT `git branch --merged`.

    `git branch` is porcelain and prefixes its output: "* " for the current
    branch and "+ " for one held by a LINKED WORKTREE. A parser stripping only
    "* " reads "+ feature/x" as unmerged -- silently wrong for exactly the
    branches this model exists to reason about. for-each-ref is plumbing, takes
    --merged as a filter, and emits bare names.
    """
    worktrees = _worktrees(root)
    holders = {w.branch: w.path for w in worktrees if w.branch}

    merged = {
        name
        for name in _git(
            "for-each-ref",
            "--format=%(refname:short)",
            "--merged",
            INTEGRATION_BRANCH,
            "refs/heads",
            cwd=root,
        ).split()
        if name
    }

    branches: list[Branch] = []
    listing = _git(
        "for-each-ref",
        "--format=%(refname:short)%00%(upstream:track)",
        "refs/heads",
        cwd=root,
    )
    for line in listing.splitlines():
        if not line:
            continue
        name, _, track = line.partition("\0")
        branches.append(
            Branch(
                name=name,
                upstream_gone=track == "[gone]",
                is_merged=name in merged,
                held_by=holders.get(name),
            )
        )

    return RepositoryState(worktrees=worktrees, branches=tuple(branches))
