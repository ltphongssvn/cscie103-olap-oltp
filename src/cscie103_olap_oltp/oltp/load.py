# src/cscie103_olap_oltp/oltp/load.py
"""Write the seed into the OLTP tables. Idempotent per load.

    Data Pipeline as Code   the load is a task, not a notebook someone ran once

VALIDATED BEFORE IT IS WRITTEN. The contracts refuse bad rows here rather than
letting a pipeline expectation quarantine them later, where the failure is a
metric instead of a message.

TWO LOADS, BECAUSE SCD2 NEEDS A BEFORE AND AN AFTER. `--initial` writes the
world before the price change; `--change` writes the same product at its new
price with a later timestamp. One load can only ever insert, which exercises
nothing the framework is there to do.

updated_at IS SUPPLIED HERE, NOT DEFAULTED IN THE TABLE. AUTO CDC sequences by
it and requires a monotonically increasing value per key; letting the database
stamp current_timestamp() would make the ordering depend on when the loader ran
rather than on when the change happened.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence

import pandas as pd

from cscie103_olap_oltp.catalog import execute
from cscie103_olap_oltp.oltp.contracts import TABLES
from cscie103_olap_oltp.oltp.seed import PRICE_CHANGE_AT, changed, rows

__all__ = [
    "LOAD_TIMES",
    "batch_for",
    "insert_statement",
    "load_batch",
    "main",
    "parser",
    "run",
]

# THE FIRST LOAD'S TIMESTAMP. Before the change, so the change genuinely
# supersedes it in the sequence AUTO CDC orders by.
INITIAL_AT = pd.Timestamp("2026-01-01")


def _literal(value: object) -> str:
    """One SQL literal.

    STRINGS ARE ESCAPED BY DOUBLING THE QUOTE, which is the SQL standard and
    the only escaping this needs: every value here comes from a committed
    fixture that the contracts have already validated, not from input.
    """
    if isinstance(value, pd.Timestamp):
        return f"TIMESTAMP '{value.isoformat(sep=' ')}'"
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return str(value)


def insert_statement(table: str, frame: pd.DataFrame, target: str, at: pd.Timestamp) -> str:
    """One multi-row INSERT, with the sequencing column appended."""
    columns = [*frame.columns, "updated_at"]
    values = [
        "(" + ", ".join(_literal(value) for value in [*row, at]) + ")"
        for row in frame.itertuples(index=False, name=None)
    ]

    return f"INSERT INTO {target}.{table} ({', '.join(columns)}) VALUES\n" + ",\n".join(values)


# WHEN EACH LOAD WRITES. The batch itself is chosen by `batch_for` below.
#
# NOT A DICT OF FUNCTIONS, HAVING BEEN ONE. Holding `rows` in a dict captures
# the object at import time, so patching the module attribute never reaches it
# -- the same trap as a mutable default argument, and it made a test appear to
# pass while silently loading the real seed.
#
# NOT globals()[name] EITHER, WHICH WAS THE NEXT ATTEMPT. Looking the function
# up by string restored the seam and hid the import from ruff, which promptly
# deleted it as unused. Indirection that defeats static analysis trades one
# silent failure for another.
DESCRIPTION = "Write the seed into the OLTP tables."

LOAD_TIMES = {
    "initial": INITIAL_AT,
    "change": PRICE_CHANGE_AT,
}


def batch_for(mode: str) -> dict[str, pd.DataFrame]:
    """The frames one load writes.

    A FUNCTION, SO THE CALL HAPPENS AT CALL TIME. `rows` and `changed` are
    resolved when this runs rather than when the module is imported, which is
    what makes them substitutable in a test -- and they stay ordinary imports,
    so the linter and the type checker both still see them.
    """
    return rows() if mode == "initial" else changed()


def parser() -> argparse.ArgumentParser:
    """The command line, declared rather than matched by hand.

    ARGPARSE RATHER THAN `match sys.argv[1:]`, AND THE TRADE IS RECORDED HERE.

    The hand-rolled version owned a usage string and a literal per mode, and
    mutation testing showed the cost: deleting the entire `--change` case
    survived, because nothing exercised the load that makes SCD2 mean anything.
    `choices` generates the usage FROM the modes, so the list exists once.

    THE HELP PROSE IS MINIMAL ON PURPOSE. Every help string is a literal a
    mutant can corrupt, and pinning help text word for word would freeze prose
    that ought to stay editable -- so the surface is kept small rather than
    asserted. Excluding string mutants wholesale is the documented alternative
    and is rejected here for the reason its own maintainers give: it reports a
    score for everything except the part that was not checked.
    """
    argument_parser = argparse.ArgumentParser(description=DESCRIPTION)
    argument_parser.add_argument("mode", choices=sorted(LOAD_TIMES))
    argument_parser.add_argument("target")
    return argument_parser


def load_batch(
    batch: dict[str, pd.DataFrame],
    target: str,
    at: pd.Timestamp,
    run: Callable[[str], None],
) -> int:
    """Validate every frame, then write it. Returns the rows written.

    `run` IS A PARAMETER, NOT A PATCHED MODULE ATTRIBUTE. An earlier test
    patched three globals to exercise this -- the executor, the seed and the
    contract -- and the documented guidance is that needing several patches is
    a signal to make the dependency explicit instead. Passing the executor in
    leaves exactly one thing a test has to arrange.
    """
    written = 0

    for name, frame in batch.items():
        # THE CONTRACT RUNS FIRST. A row the model refuses must never reach a
        # table, where it becomes a quarantined metric instead of an error.
        TABLES[name].validate(frame)
        run(insert_statement(name, frame, target, at))
        print(f"loaded  {len(frame):>3} rows -> {target}.{name}")
        written += len(frame)

    return written


def run(mode: str, target: str, execute_statement: Callable[[str], None]) -> int:
    """One load, start to finish. THE COMMAND CORE.

    EVERYTHING main DID EXCEPT PARSING AND WIRING. Selecting the batch, reading
    the timestamp and driving the loader are decisions that can be wrong, so
    they live where a test can reach them without a command line.
    """
    return load_batch(batch_for(mode), target, LOAD_TIMES[mode], execute_statement)


def main(argv: Sequence[str] | None = None) -> int:
    """THE COMPOSITION ROOT: parse, bind the real executor, return.

    DELIBERATELY TOO THIN TO TEST, which is the Humble Object pattern. An
    earlier version chose the batch and the timestamp here, and mutation
    testing left twelve uncovered mutants in it -- because every decision it
    made needed a command line to reach. Moving those into `run` leaves an
    adapter with nothing left to get wrong.
    """
    arguments = parser().parse_args(argv)
    run(arguments.mode, arguments.target, execute)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
