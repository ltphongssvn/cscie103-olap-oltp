# tests/test_gitenv.py
"""Git calls cannot be redirected by an inherited GIT_DIR.

THE PROBLEM, IN GIT'S OWN WORDS: environment variables such as GIT_DIR and
GIT_WORK_TREE are exported so that git commands run by a hook can locate the
repository -- and a hook needing to invoke git elsewhere "should clear these
environment variables so they do not interfere".

They OVERRIDE both `-C` and `cwd`. So any git call made from inside a hook
operates on the hook's repository regardless of where it was pointed.

WHY THIS REPOSITORY IS EXPOSED. Its gates run under pre-commit and pre-push and
will run from linked worktrees. Today a hook's repository and the project root
coincide, so raw subprocess works BY COINCIDENCE. From a worktree they differ,
and a gate would read a different index entirely -- passing while inspecting the
wrong repository.

THE TESTS BELOW DEMONSTRATE THAT RATHER THAN ASSERTING IT: they set GIT_DIR to a
real second repository and show raw subprocess following it while git() does not.
"""

import os
import subprocess
from pathlib import Path

import pytest

from cscie103_olap_oltp.git.env import git, routing_overrides_present, scrubbed_env


def _init_repo(path: Path) -> Path:
    """A real repository, because the behaviour under test is git's own.

    A mocked subprocess would assert what I believe git does. This observes it.
    """
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    return path


def test_scrubbed_env_removes_git_dir(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GIT_DIR", "/somewhere/else/.git")
    assert "GIT_DIR" not in scrubbed_env()


def test_scrubbed_env_removes_git_work_tree(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GIT_WORK_TREE", "/somewhere/else")
    assert "GIT_WORK_TREE" not in scrubbed_env()


def test_scrubbed_env_keeps_unrelated_variables(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Over-scrubbing would break PATH and the toolchain with it.

    The variables removed are git's OWN enumeration from
    `git rev-parse --local-env-vars`, not a hardcoded list -- a literal list is a
    second copy of something git owns, and it goes stale silently.
    """
    monkeypatch.setenv("GIT_DIR", "/somewhere/else/.git")
    environment = scrubbed_env()
    assert "PATH" in environment
    assert environment["PATH"] == os.environ["PATH"]


def test_raw_subprocess_is_hijacked_by_git_dir(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """DEMONSTRATE THE VULNERABILITY, do not merely claim it.

    Without this test the next person has no evidence the protection is needed,
    and a "simplification" that drops it would pass every other test here.
    """
    other = _init_repo(tmp_path / "other")
    here = _init_repo(tmp_path / "here")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))

    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
        cwd=here,
    )
    assert Path(result.stdout.strip()).name == "other"


def test_git_resists_the_hijack(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The same call through git() resolves from cwd, as asked."""
    other = _init_repo(tmp_path / "other")
    here = _init_repo(tmp_path / "here")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))

    result = git("rev-parse", "--show-toplevel", cwd=here)
    assert Path(result.stdout.strip()).name == "here"


def test_routing_overrides_are_reported_for_diagnostics(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A hijack that is silently corrected is still worth being able to see.

    Four CI failures in the sibling project were hard to diagnose precisely
    because nothing could answer "which variables are in play right now".
    """
    monkeypatch.setenv("GIT_DIR", "/somewhere/else/.git")
    assert routing_overrides_present()["GIT_DIR"] == "/somewhere/else/.git"


def test_no_overrides_reports_empty(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Outside a hook nothing is set, and the diagnostic should say so."""
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    assert routing_overrides_present() == {}


def test_a_poisoned_git_dir_cannot_retarget_a_fixture_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE REGRESSION THAT COST A COMMIT TO THE REAL REPOSITORY.

    A fixture ran `git commit` with cwd pointed at a scratch repository and the
    commit landed on the actual feature branch, because GIT_DIR outranks cwd and
    git exports it while a hook runs -- and the pre-push hook runs this suite.

    RED BEFORE GREEN, DELIBERATELY: the first half proves the poison WORKS, so a
    passing second half means the scrub defeated something real rather than
    nothing at all.
    """
    victim = tmp_path / "victim"
    victim.mkdir()
    subprocess.run(["git", "init", "-q", victim], check=True, capture_output=True)

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    subprocess.run(["git", "init", "-q", scratch], check=True, capture_output=True)

    # monkeypatch, NOT os.environ. A direct assignment with a trailing `del`
    # leaks GIT_DIR into every later test whenever an assertion below raises --
    # reintroducing the exact hazard this test exists to prevent. monkeypatch
    # reverses the change even on failure.
    monkeypatch.setenv("GIT_DIR", str(victim / ".git"))

    # THE POISON WORKS: git reports the victim even though cwd is the scratch.
    hijacked = subprocess.run(
        ["git", "rev-parse", "--absolute-git-dir"],
        cwd=scratch,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        check=True,
    )
    assert str(victim) in hijacked.stdout

    # THE SCRUB DEFEATS IT: the same call resolves from cwd.
    resolved = subprocess.run(
        ["git", "rev-parse", "--absolute-git-dir"],
        cwd=scratch,
        # THE SCRUBBED ENVIRONMENT AS IT IS, not one with GIT_DIR blanked. An
        # earlier draft set it to "" and would have passed even if scrubbed_env()
        # did nothing -- asserting git's tolerance of an empty value rather than
        # our removal of a real one.
        env=scrubbed_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert str(victim) not in resolved.stdout


def test_the_conftest_guard_removes_routing_variables() -> None:
    """THE ENFORCEMENT, ASSERTED RATHER THAN ASSUMED.

    tests/conftest.py strips these for every test. If that regressed, fixtures
    would silently depend on being run outside a hook -- which is exactly the
    condition under which the original damage went unnoticed.
    """
    assert "GIT_DIR" not in os.environ
    assert "GIT_WORK_TREE" not in os.environ
    assert "GIT_INDEX_FILE" not in os.environ
