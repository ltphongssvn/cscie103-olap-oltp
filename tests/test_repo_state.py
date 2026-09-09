# tests/test_repo_state.py
"""Deletability is a property of observed state, never a guess.

WHY A STATE MODEL AND NOT A PIPELINE. The obvious `sync` deletes merged branches
by piping `git for-each-ref` through awk. It works until a worktree holds one of
those branches, at which point git refuses -- correctly -- and the task aborts
mid-way, leaving the sync half done after the pull already succeeded.

The bug is not a missing filter. It is that cleanup has no model of what it is
cleaning: it knows "upstream is gone" and nothing else, so every other fact
about a branch is invisible, and the reason a delete failed can only be inferred
from an error message.

THESE TESTS BUILD REAL REPOSITORIES AND REAL WORKTREES. The behaviour under test
is git's own -- what `--merged` reports, what a linked worktree does to a branch
-- and a mock would assert what I believe git does rather than observing it.

THE FIXTURE PATH IS NOT UNDER /tmp. It is never touched on disk, but ruff's S108
does not know that, and writing a path that only LOOKS like insecure temp usage
teaches the next reader to suppress the rule. A path that is obviously synthetic
says the same thing without the argument.
"""

import subprocess
from pathlib import Path

from cscie103_olap_oltp.git.state import Branch, gather

# A PATH THAT CANNOT EXIST AND IS NEVER OPENED. Used only to populate held_by so
# the reason-string tests have something to find.
HELD_ELSEWHERE = Path("/nonexistent/worktrees/feature-x")


def _run(*args: str, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    """A repository with develop, main, and one commit.

    Configured LOCALLY rather than globally: a test that writes to the user's
    git config has side effects outside its own tmp_path.
    """
    root = tmp_path / "repo"
    root.mkdir()
    _run("git", "init", "-q", "-b", "main", cwd=root)
    _run("git", "config", "user.email", "test@example.invalid", cwd=root)
    _run("git", "config", "user.name", "Test", cwd=root)
    (root / "README.md").write_text("x\n", encoding="utf-8")
    _run("git", "add", "README.md", cwd=root)
    _run("git", "commit", "-qm", "initial", cwd=root)
    _run("git", "branch", "develop", cwd=root)
    return root


def _branch(**overrides: object) -> Branch:
    payload: dict[str, object] = {
        "name": "feature/x",
        "upstream_gone": True,
        "is_merged": True,
        "held_by": None,
    }
    payload.update(overrides)
    return Branch.model_validate(payload)


def test_protected_branches_are_never_deletable() -> None:
    """develop and main are excluded whatever their state.

    A merged, upstream-gone `develop` would otherwise satisfy every other
    condition -- and deleting the integration branch is unrecoverable locally.
    """
    for name in ("develop", "main"):
        assert not _branch(name=name).is_deletable


def test_branch_with_live_upstream_is_not_deletable() -> None:
    """An existing remote branch means the work has not been merged and cleaned."""
    assert not _branch(upstream_gone=False).is_deletable


def test_unmerged_branch_is_not_deletable() -> None:
    """Deleting unmerged work loses it; git refuses `-d` for the same reason."""
    assert not _branch(is_merged=False).is_deletable


def test_branch_held_by_a_worktree_is_not_deletable() -> None:
    """This is the case the pipeline version aborted on."""
    assert not _branch(held_by=HELD_ELSEWHERE).is_deletable


def test_fully_satisfied_branch_is_deletable() -> None:
    assert _branch().is_deletable


def test_blocked_because_names_the_worktree_path() -> None:
    """An error saying a delete failed is not actionable; this says what to do."""
    reason = _branch(held_by=HELD_ELSEWHERE).blocked_because
    assert reason is not None
    assert str(HELD_ELSEWHERE) in reason
    assert "worktree:remove" in reason


def test_blocked_because_is_none_when_deletable() -> None:
    assert _branch().blocked_because is None


def test_protection_is_reported_before_other_reasons() -> None:
    """Ordering matters: telling someone `main` is 'not merged' is misleading."""
    assert _branch(name="main", is_merged=False).blocked_because == "protected branch"


def test_gather_finds_the_main_worktree(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    state = gather(root)
    assert len(state.worktrees) == 1
    assert state.worktrees[0].is_main
    assert state.worktrees[0].branch == "main"


def test_gather_detects_a_linked_worktree_holding_a_branch(tmp_path: Path) -> None:
    """One branch, one worktree -- git enforces it, and cleanup must know."""
    root = _repo(tmp_path)
    _run("git", "worktree", "add", "-q", "-b", "feature/y", str(tmp_path / "wt"), cwd=root)

    state = gather(root)
    held = {branch.name: branch.held_by for branch in state.branches}
    assert held["feature/y"] is not None
    assert not state.worktrees[0].is_linked
    assert state.worktrees[1].is_linked


def test_gather_reports_merged_branches(tmp_path: Path) -> None:
    """`--merged develop` is asked of git rather than reimplemented."""
    root = _repo(tmp_path)
    _run("git", "branch", "feature/merged", cwd=root)

    state = gather(root)
    merged = {branch.name for branch in state.branches if branch.is_merged}
    assert "feature/merged" in merged


def test_gather_detects_a_dirty_worktree(tmp_path: Path) -> None:
    """A dirty worktree must never be removed; git refuses, and so should we."""
    root = _repo(tmp_path)
    (root / "README.md").write_text("changed\n", encoding="utf-8")

    state = gather(root)
    assert state.worktrees[0].is_dirty


def test_blocked_excludes_protected_branches(tmp_path: Path) -> None:
    """Reporting `develop` as blocked on every run is noise that trains people
    to stop reading the output."""
    root = _repo(tmp_path)
    state = gather(root)
    assert all(not branch.is_protected for branch in state.blocked)
