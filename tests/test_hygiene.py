# tests/test_hygiene.py
"""Refuse tracked files that look like accidents.

THIS REPOSITORY ALREADY COMMITTED ONE. A shell heredoc misfired and produced a
zero-byte file literally named `....`, which was staged by `git add -A` and
committed before anyone noticed. It was caught by reading the commit output, not
by any gate -- which is exactly the kind of luck a gate exists to replace.

WHAT COUNTS AS AN ACCIDENT
  editor and OS droppings      .DS_Store, *.swp, *~
  backup and rescue copies     *.bak, *.orig, *.rej, *_bak
  shell mishaps                names that are only dots, or start with a dash
  data that belongs elsewhere  *.parquet, *.csv, *.ndjson
  emitted evidence             anything under .artifacts/

WHY NOT JUST .gitignore. An ignore rule stops a file being ADDED by accident; it
does nothing about a file already tracked, and `git add -f` overrides it
silently. This checks what the index actually holds.
"""

import pytest

from cscie103_olap_oltp.hygiene import is_accident, reasons_for


def test_dot_only_names_are_refused() -> None:
    """The exact file this repository committed.

    `cat > .... << EOF` with a mistyped path produces it, and `git add -A`
    stages it without comment.
    """
    assert is_accident("....")
    assert is_accident("..")
    assert is_accident("src/...")


def test_os_droppings_are_refused() -> None:
    assert is_accident(".DS_Store")
    assert is_accident("docs/.DS_Store")


def test_editor_files_are_refused() -> None:
    assert is_accident("notes.txt~")
    assert is_accident(".mise.toml.swp")


def test_backup_copies_are_refused() -> None:
    """A committed backup is a second copy of a fact, which is what drifts.

    The sibling project deployed a stray `assignment_01_spark_api.py_bak`.
    """
    assert is_accident("mise.toml.bak")
    assert is_accident("notebooks/job.py_bak")
    assert is_accident("merge.orig")
    assert is_accident("merge.rej")


def test_data_files_are_refused() -> None:
    """Data does not belong in the source history.

    A parquet or csv in git makes every clone carry it forever, and an export of
    an executed notebook can carry PII that matches no ignore rule.
    """
    assert is_accident("seeds/customers.parquet")
    assert is_accident("seeds/orders.csv")


def test_emitted_evidence_is_refused() -> None:
    """.artifacts/ holds observations, not intent.

    Committing it would make every gate run a diff, and the repository would
    record its own observations as though they were source.
    """
    assert is_accident(".artifacts/verdicts/20260101T000000Z.json")


def test_ordinary_source_files_are_allowed() -> None:
    """A hygiene check that refuses correct work is one people disable."""
    for path in (
        "src/cscie103_olap_oltp/gates.py",
        "tests/test_gates.py",
        "mise.toml",
        "flake.nix",
        ".python-version",
        ".github/workflows/ci.yml",
        "contracts/ruleset-develop-and-main.json",
        "policies/repo/repo.rego",
        "README.md",
        "uv.lock",
    ):
        assert not is_accident(path), path


def test_reasons_name_the_rule_not_just_the_file() -> None:
    """ "Refused: ...." is not actionable; the reason is what tells you what to do."""
    reasons = reasons_for("....")
    assert reasons
    assert all(reason for reason in reasons)


def test_reasons_are_empty_for_a_clean_file() -> None:
    assert reasons_for("src/cscie103_olap_oltp/gates.py") == ()


def test_a_file_can_break_more_than_one_rule() -> None:
    """Reporting only the first reason means fixing one and discovering another."""
    reasons = reasons_for(".artifacts/dump.parquet")
    assert len(reasons) >= 2


@pytest.mark.parametrize("name", ["-rf", "--force"])
def test_leading_dash_names_are_refused(name: str) -> None:
    """A filename that looks like a flag is a shell mishap waiting to happen.

    Any later command globbing the directory passes it as an OPTION rather than
    an argument.
    """
    assert is_accident(name)
