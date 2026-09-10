# tests/test_sync.py
"""Cleanup reports what it did NOT remove, and why.

THE FAILURE THIS PREVENTS: a sync that pulls successfully, then aborts partway
through deleting branches, leaving the repository in a state nobody described.
The pipeline version did exactly that when a worktree held a merged branch --
git refused, the shell stopped, and the pull had already happened.

DRIVEN BY THE STATE MODEL, so every branch gets a decision rather than the loop
stopping at the first refusal. The plan is computed BEFORE anything is deleted,
which is what makes it printable and testable without touching a repository.
"""

from pathlib import Path

from cscie103_olap_oltp.git.env import git as _run_git
from cscie103_olap_oltp.git.state import Branch, RepositoryState, Worktree
from cscie103_olap_oltp.git.sync import (
    advance_integration_branch,
    is_linked_worktree,
    plan_cleanup,
)


def _git(*args: str, cwd: Path) -> None:
    """A git call that cannot be redirected by an inherited GIT_DIR.

    Routed through the project's own helper, which scrubs git's routing
    variables. A fixture that skipped this once committed to the real
    repository -- see tests/conftest.py.
    """
    result = _run_git(*args, cwd=cwd)
    assert result.returncode == 0, result.stderr


def _rev(root: Path, ref: str) -> str:
    return _run_git("rev-parse", ref, cwd=root).stdout.strip()


def _current_branch(root: Path) -> str:
    return _run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=root).stdout.strip()


# PATHS THAT CANNOT EXIST AND ARE NEVER OPENED. These populate fields the plan
# reads; nothing here touches a filesystem. Deliberately not under /tmp, because
# a path that only LOOKS like insecure temp usage teaches the next reader to
# suppress ruff's S108 rather than think about it.
HELD_ELSEWHERE = Path("/nonexistent/worktrees/feature-x")
MAIN_CHECKOUT = Path("/nonexistent/repo")


def _state(*branches: Branch) -> RepositoryState:
    main = Worktree(
        path=MAIN_CHECKOUT,
        branch="develop",
        is_main=True,
        is_dirty=False,
    )
    return RepositoryState(worktrees=(main,), branches=branches)


def _branch(**overrides: object) -> Branch:
    payload: dict[str, object] = {
        "name": "feature/x",
        "upstream_gone": True,
        "is_merged": True,
        "held_by": None,
    }
    payload.update(overrides)
    return Branch.model_validate(payload)


def test_plan_selects_only_deletable_branches() -> None:
    plan = plan_cleanup(_state(_branch(name="feature/done")))
    assert [branch.name for branch in plan.remove] == ["feature/done"]


def test_plan_keeps_protected_branches_out_of_both_lists() -> None:
    """develop is neither removed nor reported as a problem.

    Listing it every run is noise, and noise is what makes people stop reading
    the output of a cleanup task.
    """
    plan = plan_cleanup(_state(_branch(name="develop")))
    assert plan.remove == ()
    assert plan.blocked == ()


def test_plan_reports_a_branch_held_by_a_worktree() -> None:
    """The case the pipeline version aborted on is now REPORTED, not fatal."""
    plan = plan_cleanup(_state(_branch(name="feature/held", held_by=HELD_ELSEWHERE)))
    assert plan.remove == ()
    assert [branch.name for branch in plan.blocked] == ["feature/held"]


def test_plan_ignores_branches_whose_upstream_still_exists() -> None:
    """Work in progress is not a problem to report; it is simply not finished."""
    plan = plan_cleanup(_state(_branch(name="feature/wip", upstream_gone=False)))
    assert plan.remove == ()
    assert plan.blocked == ()


def test_plan_reports_unmerged_work_whose_upstream_vanished() -> None:
    """A deleted remote branch with unmerged local commits is a real anomaly.

    Silently leaving it behind is how work is lost without anyone noticing.
    """
    plan = plan_cleanup(_state(_branch(name="feature/orphan", is_merged=False)))
    assert plan.remove == ()
    assert [branch.name for branch in plan.blocked] == ["feature/orphan"]


def test_plan_is_empty_for_an_empty_repository() -> None:
    plan = plan_cleanup(_state())
    assert plan.remove == ()
    assert plan.blocked == ()


def test_every_blocked_branch_can_explain_itself() -> None:
    """A report saying "3 branches skipped" is not actionable."""
    plan = plan_cleanup(
        _state(
            _branch(name="feature/held", held_by=HELD_ELSEWHERE),
            _branch(name="feature/orphan", is_merged=False),
        )
    )
    for branch in plan.blocked:
        assert branch.blocked_because


def test_a_linked_worktree_is_recognised(tmp_path: Path) -> None:
    """THE DISTINCTION sync DID NOT MAKE, AND IT COST A CHECKOUT.

    `sync` switched to develop unconditionally. Run from a linked worktree that
    is exactly wrong: develop becomes checked out THERE, the main worktree stops
    holding it, and the branch being cleaned up is the one now occupied -- so
    the delete fails after the pull has already succeeded. Observed exactly
    that, in this repository.

    GIT'S OWN ANSWER IS A COMPARISON, NOT A HEURISTIC: inside a linked worktree
    $GIT_DIR points at a private directory while $GIT_COMMON_DIR points back at
    the main repository. Differing paths mean linked.
    """
    main = tmp_path / "main"
    main.mkdir()
    _git("init", "-q", "-b", "main", cwd=main)
    _git("config", "user.email", "test@example.invalid", cwd=main)
    _git("config", "user.name", "Test", cwd=main)
    _git("commit", "-q", "--allow-empty", "-m", "initial", cwd=main)

    linked = tmp_path / "linked"
    _git("worktree", "add", "-q", "-b", "feature/z", str(linked), cwd=main)

    assert not is_linked_worktree(main)
    assert is_linked_worktree(linked)


def test_the_integration_branch_is_advanced_without_being_checked_out(
    tmp_path: Path,
) -> None:
    """FAST-FORWARD THE REF, DO NOT OCCUPY IT.

    In a bare-parent layout develop has no home worktree by design: the main
    worktree is the stable reference and every branch lives in a linked one.
    Advancing the ref directly is what canonical cleanup tools do for bare
    repositories, and it leaves develop free for whoever needs it next.
    """
    origin = tmp_path / "origin"
    origin.mkdir()
    _git("init", "-q", "-b", "develop", cwd=origin)
    _git("config", "user.email", "test@example.invalid", cwd=origin)
    _git("config", "user.name", "Test", cwd=origin)
    _git("commit", "-q", "--allow-empty", "-m", "one", cwd=origin)

    clone = tmp_path / "clone"
    _git("clone", "-q", str(origin), str(clone), cwd=tmp_path)
    _git("switch", "-q", "-c", "feature/work", cwd=clone)

    _git("commit", "-q", "--allow-empty", "-m", "two", cwd=origin)
    advance_integration_branch(clone)

    # develop moved, and feature/work is still what is checked out.
    assert _rev(clone, "develop") == _rev(origin, "develop")
    assert _current_branch(clone) == "feature/work"
