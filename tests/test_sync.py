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

from cscie103_olap_oltp.git.state import Branch, RepositoryState, Worktree
from cscie103_olap_oltp.git.sync import plan_cleanup

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
