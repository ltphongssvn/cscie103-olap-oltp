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

import sys

import pandas as pd

from cscie103_olap_oltp.catalog import execute
from cscie103_olap_oltp.oltp.contracts import TABLES
from cscie103_olap_oltp.oltp.seed import PRICE_CHANGE_AT, changed, rows

__all__ = ["insert_statement", "main"]

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


def main() -> int:
    match sys.argv[1:]:
        case ["--initial", target]:
            batch, at = rows(), INITIAL_AT
        case ["--change", target]:
            batch, at = changed(), PRICE_CHANGE_AT
        case other:
            raise SystemExit(f"usage: --initial|--change <catalog.schema>  (got {other})")

    for name, frame in batch.items():
        # THE CONTRACT RUNS FIRST. A row the model refuses must never reach a
        # table, where it becomes a quarantined metric instead of an error.
        TABLES[name].validate(frame)
        execute(insert_statement(name, frame, target, at))
        print(f"loaded  {len(frame):>3} rows -> {target}.{name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
