# tests/test_repo_snapshot.py
"""The snapshot describes THIS repository, not a hand-written fixture.

A policy suite can be immaculate and enforce nothing. policies/repo/repo.rego is
tested against a fixture; these tests check the OTHER half -- that the object fed
to OPA is derived from the files on disk, so the policy judges reality.

WHY THE SNAPSHOT IS ITS OWN MODULE RATHER THAN A SCRIPT. Reading mise.toml and
ci.yml is parsing with failure modes: a renamed key produces None, and None
flowing into a Rego input silently becomes a passing rule. The functions below
are unit-testable in isolation; a script would only be testable end to end.
"""

from pathlib import Path

import pytest

from cscie103_olap_oltp.policy.snapshot import (
    REPO_ROOT,
    build_snapshot,
    lefthook_facts,
    mise_facts,
    pytest_facts,
    python_facts,
    workflow_facts,
)


def test_repo_root_is_the_repository() -> None:
    """REPO_ROOT resolves to the directory holding pyproject.toml.

    Derived from __file__ rather than from the current working directory: a git
    hook runs from wherever the user happened to be, and a snapshot that depends
    on cwd reads a different repository depending on who invoked it.
    """
    assert (REPO_ROOT / "pyproject.toml").is_file()
    assert (REPO_ROOT / "flake.nix").is_file()


def test_mise_facts_report_no_tools_block() -> None:
    """R001's input is measured, not asserted."""
    facts = mise_facts(REPO_ROOT / "mise.toml")
    assert facts["has_tools_block"] is False


def test_mise_facts_detect_the_devshell_shell() -> None:
    """R002's input reflects task_config.shell as actually written."""
    facts = mise_facts(REPO_ROOT / "mise.toml")
    assert facts["task_shell_enters_devshell"] is True


def test_mise_facts_enumerate_every_task() -> None:
    """Tasks are discovered, not listed.

    A hand-maintained list would go stale the moment a task is added, and the
    failure is silent: R003 and R004 would simply stop examining the new task.
    """
    facts = mise_facts(REPO_ROOT / "mise.toml")
    names = {task["name"] for task in facts["tasks"]}
    assert {"lint", "types", "test", "check", "setup"} <= names


def test_mise_facts_mark_multiline_tasks() -> None:
    """R004 needs to know which task bodies contain more than one command."""
    facts = mise_facts(REPO_ROOT / "mise.toml")
    by_name = {task["name"]: task for task in facts["tasks"]}
    assert by_name["types"]["is_multiline"] is True
    assert by_name["lint"]["is_multiline"] is False


def test_workflow_facts_collect_action_refs() -> None:
    """R006 checks every `uses:` in the workflow, so every one must be found."""
    facts = workflow_facts(REPO_ROOT / ".github" / "workflows" / "ci.yml")
    assert facts["has_permissions"] is True
    assert len(facts["uses"]) >= 3
    for step in facts["uses"]:
        assert "@" in step["ref"]


def test_lefthook_facts_find_both_hooks_and_the_guard() -> None:
    """R009, R010 and R011 are fed from the committed hook config."""
    facts = lefthook_facts(REPO_ROOT / "lefthook.yml")
    assert facts["has_pre_commit"] is True
    assert facts["has_pre_push"] is True
    assert facts["pre_commit_guards_branch"] is True


def test_python_facts_read_both_declarations() -> None:
    """R012 compares two files; reading only one would make it vacuous."""
    facts = python_facts(REPO_ROOT)
    assert facts["dotfile_version"] == "3.12.3"
    assert facts["pyproject_version"] == "3.12.3"


def test_pytest_facts_read_addopts_and_xfail() -> None:
    """R013 and R014 are fed from pyproject.toml, not from pytest's runtime."""
    facts = pytest_facts(REPO_ROOT / "pyproject.toml")
    assert "--strict-markers" in facts["addopts"]
    assert facts["xfail_strict"] is True


def test_snapshot_has_every_section_the_policy_reads() -> None:
    """A missing section is a silent pass, so its absence is a failure here.

    Rego resolves an unknown path to undefined, and `not input.mise.x` is TRUE
    when the whole `mise` section is missing -- so a typo in a section name
    turns a rule into a permanent denial or a permanent pass depending on its
    polarity. Neither is a gate.
    """
    snapshot = build_snapshot(REPO_ROOT)
    assert set(snapshot) == {"mise", "workflow", "lefthook", "python", "pytest"}


def test_missing_file_raises_rather_than_returning_empty() -> None:
    """Absence is an error, never an empty dict.

    Returning {} for a missing file is the fail-open shape this project keeps
    removing: the policy would receive a section with no fields and report
    whatever its rules say about nothing.
    """
    with pytest.raises(FileNotFoundError):
        mise_facts(Path("/nonexistent/mise.toml"))
