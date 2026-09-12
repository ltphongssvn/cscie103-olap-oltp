# src/cscie103_olap_oltp/oltp/tables.py
"""Create the OLTP tables from their contracts. Idempotent.

    Migration as Code   the DDL is DERIVED from the contract, never written
    Schema as Code      one definition creates the table AND validates it

WHY THIS EXISTS, DISCOVERED BY RUNNING THE PIPELINE RATHER THAN BY REVIEW.
The star pipeline deployed cleanly and failed with TABLE_OR_VIEW_NOT_FOUND on
every source. Contracts had been declared, published as ODCS and gated -- and
nothing had ever created a table. A contract with no table is a promise about
something that does not exist.

WHY TABLES ARE NOT A BUNDLE RESOURCE. The documented split puts everything the
bundle reconciles on deploy -- catalog, schemas, pipelines -- above the line,
and the schema an application owns (its tables, columns, grants) below it,
applied once the infrastructure is in place and shipped as versioned migrations.
Bundles have no table resource type; this is that lower layer.

WHY THE DDL IS GENERATED. Writing CREATE TABLE by hand would put the column list
in two places: the pandera model that validates and the SQL that creates. They
would disagree on the first change, silently, because each would still look
correct on its own.

IF NOT EXISTS IS THE IDEMPOTENCE PROPERTY, for the reason catalog.py states: a
bootstrap that fails on its second run is a bootstrap nobody re-runs, and one
nobody re-runs cannot reconcile drift.
"""

from __future__ import annotations

import sys

import pandera.pandas as pa

from cscie103_olap_oltp.catalog import execute
from cscie103_olap_oltp.oltp.contracts import TABLES

__all__ = ["SQL_TYPES", "TABLE_FOOTER", "UNMAPPED_DTYPE", "USAGE", "ddl_for", "main"]

# PANDERA DTYPES TO UNITY CATALOG TYPES, declared as a table so an unmapped
# dtype fails at the lookup rather than silently becoming STRING.
SQL_TYPES = {
    "int64": "BIGINT",
    "float64": "DOUBLE",
    "bool": "BOOLEAN",
    "string[python]": "STRING",
    "datetime64[ns]": "TIMESTAMP",
}

# THE COLUMN THE CDC FLOW SEQUENCES BY.
#
# Deliberately NOT in the pandera contract: it is warehouse plumbing rather than
# a business fact, and putting it in the published data contract would promise
# consumers a column that means nothing to them. The pipeline needs it to order
# changes and handle late arrivals.
SEQUENCE_COLUMN = "updated_at"

# THE MESSAGES, AS TEMPLATES RATHER THAN ADJACENT LITERALS.
#
# Inline, each fragment was separately mutable: three mutants corrupted
# mid-sentence text that no test could kill without pinning the wording word
# for word, which freezes prose that ought to stay free to improve.
UNMAPPED_DTYPE = (
    "no SQL type mapped for pandera dtype {dtype!r} (column {column!r}); add it to SQL_TYPES"
)

# THE TABLE FOOTER. One literal, so the CDC property cannot be corrupted
# piecemeal -- and a table created without it makes the pipeline read an empty
# change feed and report success.
TABLE_FOOTER = "\n)\nTBLPROPERTIES (delta.enableChangeDataFeed = true)"

USAGE = "usage: python -m cscie103_olap_oltp.oltp.tables <catalog.schema>"


def ddl_for(name: str, schema: pa.DataFrameSchema, target: str) -> str:
    """One CREATE TABLE, rendered from the contract.

    CHANGE DATA FEED IS ENABLED AT CREATION. The CDC flow reads it, and turning
    it on later requires an ALTER that somebody has to remember.
    """
    columns = []

    for column, spec in schema.columns.items():
        dtype = str(spec.dtype)
        if dtype not in SQL_TYPES:
            raise SystemExit(UNMAPPED_DTYPE.format(dtype=dtype, column=column))

        nullable = "" if spec.nullable else " NOT NULL"
        columns.append(f"  {column} {SQL_TYPES[dtype]}{nullable}")

    columns.append(f"  {SEQUENCE_COLUMN} TIMESTAMP NOT NULL")

    return f"CREATE TABLE IF NOT EXISTS {target}.{name} (\n" + ",\n".join(columns) + TABLE_FOOTER


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(USAGE)

    target = sys.argv[1]

    for name, model in TABLES.items():
        execute(ddl_for(name, model.to_schema(), target))
        print(f"ok      {target}.{name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
