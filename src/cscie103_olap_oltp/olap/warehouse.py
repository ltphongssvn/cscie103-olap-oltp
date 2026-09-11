# src/cscie103_olap_oltp/olap/warehouse.py
"""Read the star, for tests that must assert against real data.

    Tests as Code   the demonstration is executable rather than a transcript

WHY A MODULE AND NOT A QUERY IN EACH TEST. The invariants that matter here were
first checked by hand, in a shell, once. That proves a moment rather than a
property -- and a transcript cannot fail when a regression arrives.

RESULTS AS DICTS, NOT POSITIONAL ROWS. The Statement Execution API returns a
data_array of strings with the column names in a separate manifest; indexing by
position makes an assertion silently shift when a SELECT list is edited.

VALUES STAY STRINGS. The API sends them that way, and coercing here would put a
second type system beside the contracts -- one that disagrees the first time a
DECIMAL appears. Tests compare to the string the warehouse actually returned.

THE SCHEMA IS SUBSTITUTED, NOT HARDCODED. Development mode renames schemas, so a
literal works in production and silently reads nothing in dev.
"""

from __future__ import annotations

import json
from typing import Any

from cscie103_olap_oltp.catalog import _cli, warehouse_id
from cscie103_olap_oltp.olap.contracts import UNKNOWN_KEY

__all__ = ["UNKNOWN_KEY", "olap_schema", "query"]


def olap_schema() -> str:
    """The effective OLAP schema for this deployment, ASKED OF THE BUNDLE.

    NOT RECONSTRUCTED FROM AN IDENTITY. A first version composed the dev prefix
    from a `databricks_username` setting -- a field that does not exist, invented
    because the shape of the answer was obvious and the source was not. Even had
    it existed, that would be a second implementation of the CLI's naming rule,
    disagreeing the first time the rule changed.

    THE BUNDLE ALREADY RESOLVED THIS. `bundle validate --output json` reports the
    resolved resources for the target, including the schema the pipeline
    publishes into -- which is by construction the one this deploy created.
    """
    resolved = _cli("bundle", "validate", "-t", "dev", "--output", "json")
    schema: str = resolved["resources"]["schemas"]["olap"]["name"]
    catalog: str = resolved["resources"]["schemas"]["olap"]["catalog_name"]

    return f"{catalog}.{schema}"


def query(statement: str, **parameters: Any) -> list[dict[str, Any]]:
    """Run one SELECT and return rows keyed by column name.

    VALUES GO IN AS PARAMETER MARKERS, NEVER AS FORMATTED TEXT. The Statement
    Execution API supports named markers (`:name`), and Databricks is explicit
    that f-strings do not protect against injection while markers separate the
    provided values from the structure of the statement.

    THE FIX BELONGS HERE RATHER THAN AT THE ONE CALL SITE THAT TRIPPED THE
    LINTER. Removing a single interpolation leaves the next one to be written;
    giving the boundary a parameter API removes the reason to write one.

    THE SCHEMA IS STILL SUBSTITUTED, because it is an IDENTIFIER rather than a
    value -- markers cannot name objects outside an IDENTIFIER clause. It comes
    from the resolved bundle, never from input.
    """
    response = _cli(
        "api",
        "post",
        "/api/2.0/sql/statements",
        "--json",
        json.dumps(
            {
                "warehouse_id": warehouse_id(),
                "statement": statement.format(olap=olap_schema()),
                "parameters": [
                    {"name": name, "value": str(value)} for name, value in parameters.items()
                ],
                "wait_timeout": "50s",
            }
        ),
    )

    state = response.get("status", {}).get("state")
    if state != "SUCCEEDED":
        raise RuntimeError(f"query did not succeed: {response.get('status')}")

    columns = [column["name"] for column in response["manifest"]["schema"]["columns"]]
    rows = response.get("result", {}).get("data_array", [])

    return [dict(zip(columns, row, strict=True)) for row in rows]
