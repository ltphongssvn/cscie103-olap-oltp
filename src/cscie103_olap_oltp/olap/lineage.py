# src/cscie103_olap_oltp/olap/lineage.py
"""Read the lineage Unity Catalog already recorded.

    Lineage as Data   the graph is queryable facts, not a drawing

NOTHING HERE DECLARES THE GRAPH, AND THAT IS THE POINT. Unity Catalog captures
lineage automatically from Spark execution plans for every pipeline update, so
the edges already exist. Writing them down a second time would produce a
description that agrees with the pipeline until the day it does not -- and the
day it stops agreeing is exactly the day someone needs it.

A DIAGRAM CANNOT FAIL. That is the whole argument for deriving this: the
question is operational -- "if oltp.product changes, what breaks" -- and an
answer nobody checks is trusted precisely because it looks authoritative.

RECORDS ARE EMITTED ONLY WHEN LINEAGE CAN BE INFERRED, so an empty result means
either the pipeline has not run or the edge was not captured. Both are worth
failing on, and neither is worth papering over with a hardcoded fallback.
"""

from __future__ import annotations

from cscie103_olap_oltp.olap.warehouse import olap_schema, query

__all__ = [
    "LINEAGE_TABLE",
    "OLAP_TABLES",
    "OLTP_TABLES",
    "UPSTREAM_QUERY",
    "upstream_of",
]

# THE SYSTEM TABLE, NOT THE REST API. Both expose the same capture; SQL is what
# this project already speaks, and the API returns only DIRECT parents and
# children rather than a traversable graph.
LINEAGE_TABLE = "system.access.table_lineage"

# THE TABLES THIS PROJECT PUBLISHES. Named here because the assertion is about
# OUR graph; a query over everything would drown in the sibling project.
OLAP_TABLES = ("dim_customer", "dim_product", "fact_order_line")

# THE SOURCE TABLES, NAMED SO THE CYCLE CHECK HAS SOMETHING TO ASK ABOUT.
# A warehouse feeding a source is the edge that turns a batch into a loop.
OLTP_TABLES = ("category", "customer", "order", "order_line", "product")

# THE QUERY, AS ONE VALUE RATHER THAN THREE ADJACENT LITERALS.
#
# Written inline it was three separately mutable fragments, and mutants
# corrupted the WHERE and the NULL filter without any test noticing -- each
# clause looked intact in the fragment the test happened to assert.
#
# DISTINCT, BECAUSE THE RAW TABLE IS AN EVENT LOG: one row per read or write,
# so an hourly pipeline repeats the same edge hundreds of times.
#
# IS NOT NULL, BECAUSE A WRITE WITH NO SOURCE IS NOT AN EDGE -- an INSERT of
# literal values records a target and no parent.
UPSTREAM_QUERY = (
    # S608 SUPPRESSED NARROWLY: the only interpolation is LINEAGE_TABLE, a
    # module constant naming a platform system table. The one value that varies
    # -- the target -- is a PARAMETER MARKER, which is the whole point.
    "SELECT DISTINCT source_table_full_name AS source"  # noqa: S608
    f" FROM {LINEAGE_TABLE}"
    " WHERE target_table_full_name = :target"
    " AND source_table_full_name IS NOT NULL"
)


def upstream_of(table: str, schema: str | None = None) -> set[str]:
    """The tables that fed `table`, as recorded by the platform.

    DISTINCT, BECAUSE THE RAW TABLE IS AN EVENT LOG. It holds one row per read
    or write, so a pipeline run every hour would otherwise return the same edge
    hundreds of times and say nothing more than it did once.

    source_table_full_name IS NULL FOR WRITES WITH NO SOURCE -- an INSERT of
    literal values, for instance -- and those rows are not edges.
    """
    rows = query(UPSTREAM_QUERY, target=f"{schema or olap_schema()}.{table}")

    return {row["source"] for row in rows}
