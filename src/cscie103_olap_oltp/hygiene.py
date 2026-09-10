# src/cscie103_olap_oltp/hygiene.py
"""Refuse tracked files that look like accidents.

THIS REPOSITORY ALREADY COMMITTED ONE. A shell heredoc misfired and produced a
zero-byte file literally named `....`, which `git add -A` staged and committed
before anyone noticed. It was caught by reading the commit output -- luck, not a
gate.

WHY NOT JUST .gitignore. An ignore rule stops a file being ADDED by accident. It
does nothing about a file already tracked, and `git add -f` overrides it
silently. This checks what the INDEX actually holds, which is the only thing
that ships.

WHY -z ON ls-files. Without it a path containing a newline splits into two bogus
entries, and a hygiene check that misreads its own input is worse than none.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from cscie103_olap_oltp.git.env import scrubbed_env
from cscie103_olap_oltp.policy.snapshot import REPO_ROOT

# EXTENSIONS THAT ARE NEVER SOURCE.
#
# Data does not belong in the history: a parquet or csv in git makes every clone
# carry it forever, and an export of an executed notebook can carry PII that
# matches no ignore rule at all.
DATA_SUFFIXES = frozenset({".parquet", ".csv", ".tsv", ".ndjson", ".avro", ".orc"})

# BACKUPS AND RESCUE COPIES. A committed backup is a second copy of a fact, and
# two copies drift. The sibling project deployed a stray
# `assignment_01_spark_api.py_bak` to a Databricks workspace.
BACKUP_SUFFIXES = frozenset({".bak", ".orig", ".rej", ".swp", ".swo", ".tmp"})

# EDITOR AND OS DROPPINGS.
DROPPING_NAMES = frozenset({".DS_Store", "Thumbs.db", ".directory"})

# EMITTED EVIDENCE. .artifacts/ holds observations, not intent. Committing it
# would make every gate run a diff, and the repository would record its own
# observations as though they were source.
EMITTED_PREFIXES = (".artifacts/",)


def reasons_for(path: str) -> tuple[str, ...]:
    """Every rule this path breaks, not just the first.

    ALL OF THEM, DELIBERATELY. Reporting one reason means fixing it and
    discovering the next -- the same treadmill the gate runner exists to remove,
    reproduced one level down.
    """
    name = Path(path).name
    suffix = Path(path).suffix
    reasons: list[str] = []

    # A NAME THAT IS ONLY DOTS IS A SHELL MISHAP. `cat > .... << EOF` with a
    # mistyped path produces exactly this, and it is invisible in most listings.
    if name and set(name) == {"."}:
        reasons.append(f"{path}: a name consisting only of dots is a shell mishap")

    # A FILENAME THAT LOOKS LIKE A FLAG. Any later command globbing the
    # directory passes it as an OPTION rather than an argument.
    if name.startswith("-"):
        reasons.append(f"{path}: a leading dash makes this parse as a command-line flag")

    if name in DROPPING_NAMES:
        reasons.append(f"{path}: an editor or OS dropping, not source")

    if name.endswith("~"):
        reasons.append(f"{path}: an editor backup file")

    if suffix in BACKUP_SUFFIXES or name.endswith("_bak"):
        reasons.append(f"{path}: a backup or rescue copy; two copies of one fact drift")

    if suffix in DATA_SUFFIXES:
        reasons.append(f"{path}: data belongs in storage, not in the source history")

    if any(path.startswith(prefix) for prefix in EMITTED_PREFIXES):
        reasons.append(f"{path}: emitted evidence is observation, not intent")

    return tuple(reasons)


def is_accident(path: str) -> bool:
    return bool(reasons_for(path))


def tracked_files(root: Path) -> tuple[str, ...]:
    """Every path in the index.

    THE INDEX, NOT THE WORKING TREE. An untracked accident is harmless; a
    tracked one ships.
    """
    result = subprocess.run(
        ["git", "ls-files", "-z"],  # noqa: S607
        capture_output=True,
        check=False,
        cwd=root,
        env=scrubbed_env(),
    )
    if result.returncode != 0:
        print(result.stderr.decode(errors="replace").strip(), file=sys.stderr)
        raise SystemExit("git ls-files failed")

    return tuple(entry.decode("utf-8") for entry in result.stdout.split(b"\0") if entry)


def main() -> int:
    files = tracked_files(REPO_ROOT)

    # FAIL CLOSED ON AN EMPTY INDEX. Zero tracked files is not a clean
    # repository; it means the listing failed to produce anything, and reporting
    # success there is the vacuous-pass shape this project keeps removing.
    if not files:
        print("no tracked files reported; refusing to report success", file=sys.stderr)
        return 1

    findings = [reason for path in files for reason in reasons_for(path)]

    if findings:
        for reason in findings:
            print(reason, file=sys.stderr)
        print(f"\n{len(findings)} hygiene violations in tracked files", file=sys.stderr)
        return 1

    print(f"{len(files)} tracked files, no accidents")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
