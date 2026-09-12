# tests/test_lineage_query.py
"""The lineage QUERY is built correctly, without a warehouse.

    Lineage as Data   the graph is queryable facts, not a drawing

WHY THIS IS SEPARATE FROM test_lineage.py. Those tests assert the real graph and
are marked `spark`, so they are deselected from the default run and CI cannot
execute them at all -- Free Edition refuses the service principal SQL access.
olap/lineage.py therefore scored 0/14: the module was exercised nowhere a normal
run reaches.

THE QUERY IS THE PART THAT CAN BE WRONG WITHOUT A WAREHOUSE. Drop the DISTINCT
and every hourly run repeats an edge; drop the NULL filter and a literal INSERT
becomes a phantom parent; interpolate the table name and the whole point of
parameter markers is gone.
"""

from collections.abc import Sequence

import pytest

from cscie103_olap_oltp.olap import lineage


def captured(
    monkeypatch: pytest.MonkeyPatch,
    rows: Sequence[dict[str, str]] = (),
) -> dict[str, object]:
    """Record the SQL and parameters instead of reaching the warehouse."""
    seen: dict[str, object] = {}

    def fake_query(sql: str, **params: object) -> tuple[dict[str, str], ...]:
        seen["sql"] = sql
        seen["params"] = params
        return tuple(rows)

    monkeypatch.setattr(lineage, "query", fake_query)
    monkeypatch.setattr(lineage, "olap_schema", lambda: "cat.olap")
    return seen


def test_the_target_is_a_parameter_not_an_interpolated_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A TABLE NAME CROSSING INTO SQL AS TEXT IS THE INJECTION THIS AVOIDS.

    The marker is what keeps the value a value. Interpolating it would work
    identically on every input that happens to be a plain identifier, which is
    why the defect survives review.
    """
    seen = captured(monkeypatch)
    lineage.upstream_of("dim_product")

    assert ":target" in str(seen["sql"])
    assert seen["params"] == {"target": "cat.olap.dim_product"}
    assert "dim_product" not in str(seen["sql"]), "the table name must not be inlined"


def test_the_query_asks_for_distinct_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE RAW TABLE IS AN EVENT LOG: one row per read or write. Without
    DISTINCT an hourly pipeline returns the same edge hundreds of times and
    says nothing more than it did once."""
    seen = captured(monkeypatch)
    lineage.upstream_of("dim_product")

    assert "SELECT DISTINCT" in str(seen["sql"])


def test_writes_with_no_source_are_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    """source_table_full_name IS NULL FOR AN INSERT OF LITERAL VALUES, which is
    a write rather than an edge. Including those rows invents a parent named
    None and the graph stops being true."""
    seen = captured(monkeypatch)
    lineage.upstream_of("dim_product")

    assert "source_table_full_name IS NOT NULL" in str(seen["sql"])


def test_the_platform_lineage_table_is_the_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """NOTHING HERE DECLARES THE GRAPH. Reading Unity Catalog's own capture is
    the whole argument: a second description agrees with the pipeline until the
    day it does not."""
    seen = captured(monkeypatch)
    lineage.upstream_of("dim_product")

    assert lineage.LINEAGE_TABLE in str(seen["sql"])
    assert lineage.LINEAGE_TABLE == "system.access.table_lineage"


def test_an_explicit_schema_overrides_the_resolved_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE OLTP TABLES LIVE IN A DIFFERENT SCHEMA, so the cycle check has to ask
    about a schema other than the OLAP one."""
    seen = captured(monkeypatch)
    lineage.upstream_of("product", schema="cat.oltp")

    assert seen["params"] == {"target": "cat.oltp.product"}


def test_the_sources_are_returned_as_a_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """A SET, BECAUSE THE QUESTION IS MEMBERSHIP. Returning rows would leak the
    warehouse's shape into every caller."""
    seen = captured(
        monkeypatch, rows=[{"source": "cat.oltp.product"}, {"source": "cat.oltp.category"}]
    )
    assert seen is not None

    assert lineage.upstream_of("dim_product") == {"cat.oltp.product", "cat.oltp.category"}


def test_no_recorded_lineage_is_an_empty_set_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """AN EMPTY RESULT MEANS THE PIPELINE HAS NOT RUN OR THE EDGE WAS NOT
    CAPTURED. Both are worth failing on in the caller, which is why this
    reports the fact rather than inventing a fallback."""
    captured(monkeypatch, rows=[])

    assert lineage.upstream_of("dim_product") == set()


def test_the_named_tables_are_this_project_s_own() -> None:
    """A QUERY OVER EVERYTHING WOULD DROWN IN THE SIBLING PROJECT, and the
    assertion is about OUR graph."""
    assert lineage.OLAP_TABLES == ("dim_customer", "dim_product", "fact_order_line")
    assert set(lineage.OLTP_TABLES).isdisjoint(lineage.OLAP_TABLES)


def test_the_query_names_every_clause_it_depends_on() -> None:
    """ONE ASSERTION FOR ONE LITERAL, WHICH THE HOISTING MADE POSSIBLE.

    Inline, the SQL was three adjacent fragments and each was separately
    mutable: mutants corrupted the WHERE clause and the NULL filter while the
    fragment each test asserted stayed intact.
    """
    sql = lineage.UPSTREAM_QUERY

    assert sql.startswith("SELECT DISTINCT source_table_full_name AS source")
    assert f" FROM {lineage.LINEAGE_TABLE} " in sql + " "
    assert " WHERE target_table_full_name = :target" in sql
    assert sql.endswith(" AND source_table_full_name IS NOT NULL")
