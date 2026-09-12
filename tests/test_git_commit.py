# tests/test_git_commit.py
"""Staging is a repository operation, so it is a task.

    Tasks as Code   no repo operation lives only in someone's shell history

WHAT THIS DELIBERATELY DOES NOT DO, HAVING FIRST TRIED TO.

A first version refused protected branches and accident-shaped filenames. Both
are SECOND COPIES of rules that already exist -- `start:check` in the
pre-commit hook, and the hygiene gate -- and the hook layer is the only one
that reliably enforces them anyway. Git's own maintainer rejects the premise
directly: preventing a mistaken `git add` is not a worthy goal when `git reset`
undoes it. A guard duplicated is a guard that drifts.

WHAT IT DOES DO IS FIX A REAL BUG. `git add -A` exits 1 on git 2.52+ whenever
an ignored directory exists on disk, regardless of pathspec exclusions -- and
`mutants/` is exactly such a directory, created by every mutation run. The
documented fix is two-step staging: `git add -u` for tracked changes, then the
untracked files that `ls-files --others --exclude-standard` reports.
"""

from cscie103_olap_oltp.git.commit import stage_commands


def test_tracked_changes_are_staged_without_a_full_tree_walk() -> None:
    """`git add -u` NEVER TRAVERSES IGNORED DIRECTORIES, which is the whole
    reason for the split."""
    first = stage_commands(untracked=[])[0]

    assert first == ("git", "add", "-u")
    assert "-A" not in first, "git add -A exits 1 when an ignored dir exists on disk"


def test_untracked_files_are_staged_by_name() -> None:
    """NAMED EXPLICITLY, so what enters the index is what ls-files reported
    rather than whatever a second tree walk happens to find."""
    commands = stage_commands(untracked=["tests/test_new.py", "docs/note.md"])

    assert commands[-1] == ("git", "add", "--", "tests/test_new.py", "docs/note.md")


def test_nothing_untracked_means_no_second_command() -> None:
    """`git add --` WITH NO PATHS IS AN ERROR, so the command is omitted rather
    than issued empty."""
    assert stage_commands(untracked=[]) == [("git", "add", "-u")]


def test_a_filename_that_looks_like_a_flag_is_still_a_path() -> None:
    """THE `--` SEPARATOR IS LOad-BEARING. Without it a file named `-n` is
    parsed as an option, and git does something nobody asked for."""
    commands = stage_commands(untracked=["-n"])

    assert commands[-1] == ("git", "add", "--", "-n")
