# tests/test_worktree.py
"""Worktree naming and inventory parsing, verified before anything destructive.

THE PARSER IS WHAT THESE TESTS GUARD. `worktree remove` sits on top of it, so a
half-understood inventory is how the wrong directory gets deleted. It fails
closed on an unrecognised record rather than returning a partial list.

-z IS NOT OPTIONAL, AND ONE TEST PROVES WHY. `worktree list --porcelain` does
NOT quote paths, so a path containing a newline corrupts any line-based parser.
The git docs recommend combining --porcelain with -z for exactly this reason,
and a test with a newline in a path is the only way that claim stays true.
"""

import subprocess
from pathlib import Path

import pytest

from cscie103_olap_oltp.git.worktree import (
    parse_records,
    sibling_name,
    validate_slug,
    worktree_parent,
)


def _run(*args: str, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    _run("git", "init", "-q", "-b", "main", cwd=root)
    _run("git", "config", "user.email", "test@example.invalid", cwd=root)
    _run("git", "config", "user.name", "Test", cwd=root)
    (root / "README.md").write_text("x\n", encoding="utf-8")
    _run("git", "add", "README.md", cwd=root)
    _run("git", "commit", "-qm", "initial", cwd=root)
    _run("git", "branch", "develop", cwd=root)
    return root


def test_slug_must_be_kebab_case() -> None:
    """A slug becomes both a branch name and a directory name.

    Rejecting anything else keeps those two derivable from one string instead of
    needing a translation table.
    """
    for bad in ("Feature_X", "has space", "UPPER", "", "trailing/slash"):
        with pytest.raises(SystemExit, match="kebab-case"):
            validate_slug(bad)


def test_valid_slugs_are_accepted() -> None:
    for good in ("oltp-contracts", "star-schema", "scd2"):
        validate_slug(good)


def test_sibling_name_flattens_the_branch_slash() -> None:
    """SIBLING DIRECTORIES, NAMED AFTER THE BRANCH.

    Branch slashes do not translate to folder names, so feature/star-schema
    becomes <repo>-star-schema. Siblings avoid nested-.git problems and make
    active work visible from one `ls`.
    """
    assert sibling_name(Path("/nonexistent/cscie103-olap-oltp"), "star-schema") == Path(
        "/nonexistent/cscie103-olap-oltp-star-schema"
    )


def test_parse_records_finds_the_main_worktree(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    records = parse_records(root)
    assert records[0]["worktree"] == str(root)
    assert records[0]["branch"] == "refs/heads/main"


def test_parse_records_finds_a_linked_worktree(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    linked = tmp_path / "project-feature"
    _run("git", "worktree", "add", "-q", "-b", "feature/x", str(linked), cwd=root)

    records = parse_records(root)
    assert len(records) == 2
    assert records[1]["worktree"] == str(linked)
    assert records[1]["branch"] == "refs/heads/feature/x"


def test_parse_records_survives_a_newline_in_a_path(tmp_path: Path) -> None:
    """THE REASON -z IS MANDATORY, demonstrated rather than asserted.

    Without it this path splits into two bogus records and the inventory is
    silently wrong -- underneath a command that deletes directories.
    """
    root = _repo(tmp_path)
    linked = tmp_path / "project-with\nnewline"
    _run("git", "worktree", "add", "-q", "-b", "feature/nl", str(linked), cwd=root)

    records = parse_records(root)
    assert len(records) == 2
    assert "\n" in records[1]["worktree"]


def test_parse_records_reports_a_prunable_worktree(tmp_path: Path) -> None:
    """A worktree whose directory vanished is still registered.

    `list` must show it, because the fix is `git worktree prune` and nothing
    else will tell you that.
    """
    root = _repo(tmp_path)
    linked = tmp_path / "project-gone"
    _run("git", "worktree", "add", "-q", "-b", "feature/gone", str(linked), cwd=root)
    for item in sorted(linked.rglob("*"), reverse=True):
        item.unlink() if item.is_file() else item.rmdir()
    linked.rmdir()

    records = parse_records(root)
    assert any("prunable" in record for record in records)


def test_parse_records_rejects_an_unknown_attribute(tmp_path: Path) -> None:
    """FAIL CLOSED. A future git attribute refuses the whole inventory.

    Destructive verbs sit on this parser; acting on a partially understood list
    is worse than refusing to act at all.
    """
    root = _repo(tmp_path)
    with pytest.raises(SystemExit, match="unrecognised"):
        parse_records(root, _raw=b"worktree /a\0surprise value\0\0")


def test_parse_records_accepts_a_bare_repository(tmp_path: Path) -> None:
    """THE OLD TEST ASSERTED THE BUG, WHICH IS WHY IT SURVIVED SO LONG.

    It required parse_records to REFUSE a bare parent, encoding the claim that
    the sibling layout needs a parent checkout. Every worktree in this project
    hangs off a bare parent, so the refusal made `worktree:remove` unusable --
    and the test made the refusal look deliberate.

    A test that pins a limitation in place is worse than no test: it converts a
    fixable defect into a documented decision.
    """
    root = _repo(tmp_path)
    records = parse_records(
        root,
        _raw=b"worktree /x/project.git\0bare\0\0",
    )
    assert "bare" in records[0]


def test_a_bare_parent_is_accepted(tmp_path: Path) -> None:
    """THE LAYOUT THIS PROJECT ACTUALLY USES, AND THE PARSER REFUSED IT.

    `parse_records` rejected any inventory whose first record is bare, reasoning
    that the sibling layout composes paths from a parent checkout. That is half
    right: a bare repository has no working tree, but it still has a PATH, and
    `<repo>-<slug>` beside it is exactly the naming already in use here.

    The refusal cost `worktree:remove` entirely -- every worktree in this
    repository hangs off a bare parent, so the verb could never run. Tooling
    elsewhere reached the same conclusion and simply removed the block, skipping
    only the checks that need a working tree.
    """
    bare = tmp_path / "project.git"
    _run("git", "init", "-q", "--bare", "-b", "main", str(bare), cwd=tmp_path)

    records = parse_records(bare)
    assert records
    assert "bare" in records[0]


def test_the_sibling_base_comes_from_git_not_from_a_path_guess(tmp_path: Path) -> None:
    """THE LAYOUT IS ASKED OF GIT, NOT INFERRED FROM DIRECTORY NAMES.

    Stripping a ".git" suffix is exactly the path-name inference a 2026 bug
    report names as the defect: git records the layout explicitly, and reading
    it is what works for every arrangement rather than the ones whose names
    happen to follow a convention.
    """
    bare = tmp_path / "project.git"
    _run("git", "init", "-q", "--bare", "-b", "main", str(bare), cwd=tmp_path)

    assert worktree_parent(bare) == tmp_path


def test_a_normal_parent_is_unaffected(tmp_path: Path) -> None:
    """A FIX FOR THE BARE CASE MUST NOT BREAK THE ORDINARY ONE."""
    root = tmp_path / "project"
    root.mkdir()
    _run("git", "init", "-q", "-b", "main", cwd=root)

    assert worktree_parent(root) == tmp_path


def test_sibling_naming_is_unchanged(tmp_path: Path) -> None:
    """<repo>-<slug>, beside the parent, in both layouts."""
    assert sibling_name(Path("/x/project"), "star-schema") == Path("/x/project-star-schema")
