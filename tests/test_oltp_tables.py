# tests/test_oltp_tables.py
"""The OLTP tables are DERIVED from their contracts, never typed.

    Migration as Code   the DDL is generated, so it cannot disagree
    Schema as Code      one definition creates the table AND validates it

WHY THIS FILE EXISTS. oltp/tables.py scored 0/37 on mutation testing: nothing
ran it at all. It is the module that CREATES the source tables, and the reason
it was written is itself a caught failure -- the pipeline deployed cleanly and
died with TABLE_OR_VIEW_NOT_FOUND because contracts had been declared, gated and
published while nothing had ever created a table.

A generator with no tests is the same shape: it looks correct, and the first
sign of a defect is a pipeline failing against a real warehouse.
"""

import pandas as pd
import pandera.pandas as pa
import pytest

from cscie103_olap_oltp.oltp.contracts import TABLES
from cscie103_olap_oltp.oltp.tables import SEQUENCE_COLUMN, SQL_TYPES, ddl_for

TARGET = "cat.sch"


def rendered(name: str = "customer") -> str:
    return ddl_for(name, TABLES[name].to_schema(), TARGET)


def test_the_table_is_created_only_if_absent() -> None:
    """IF NOT EXISTS IS THE IDEMPOTENCE PROPERTY. Without it a second bootstrap
    fails on a table that is already correct, which trains people to run
    bootstrap once and never again -- and one nobody re-runs cannot reconcile
    drift."""
    assert rendered().startswith(f"CREATE TABLE IF NOT EXISTS {TARGET}.customer (")


def test_the_change_data_feed_is_enabled_at_creation() -> None:
    """THE CDC FLOW READS IT, and enabling it later needs an ALTER that somebody
    has to remember. A table created without it makes the pipeline read an
    empty change feed and report success."""
    assert "delta.enableChangeDataFeed = true" in rendered()


def test_every_contract_column_reaches_the_table() -> None:
    """THE CONTRACT IS THE SOURCE. A column added to the pandera model appears
    here without anyone editing SQL."""
    schema = TABLES["customer"].to_schema()
    sql = rendered()

    for column in schema.columns:
        assert f"  {column} " in sql, column


def test_the_sequencing_column_is_added_and_not_null() -> None:
    """AUTO CDC ORDERS CHANGES BY IT, so a nullable sequence column lets a row
    with no timestamp sort arbitrarily -- and history records an order that
    never happened.

    IT IS DELIBERATELY ABSENT FROM THE CONTRACT: warehouse plumbing rather than
    a business fact, so publishing it would promise consumers a meaningless
    column.
    """
    assert f"  {SEQUENCE_COLUMN} TIMESTAMP NOT NULL" in rendered()
    assert SEQUENCE_COLUMN not in TABLES["customer"].to_schema().columns


def test_a_required_column_is_declared_not_null() -> None:
    """NULLABILITY IS THE CONTRACT'S, NOT A DEFAULT. A required column created
    nullable accepts the rows the contract exists to refuse, and the table
    stops being a constraint."""
    sql = rendered()

    assert "  customer_id BIGINT NOT NULL" in sql


def test_a_nullable_column_is_not_declared_not_null() -> None:
    """THE OTHER DIRECTION MATTERS EQUALLY: NOT NULL on a nullable column
    rejects legitimate rows at load time, far from the contract that allowed
    them."""
    # `pd.Int64Dtype()` RENDERS AS "Int64", WHICH SQL_TYPES DOES NOT MAP.
    # pandas' nullable extension types are spelled with a capital; the mapping
    # declares the numpy spellings the contracts actually use. Reaching for the
    # nullable type here tested the error path by accident.
    schema = pa.DataFrameSchema({"maybe": pa.Column("int64", nullable=True)})
    sql = ddl_for("t", schema, TARGET)

    assert "  maybe BIGINT," in sql
    assert "maybe BIGINT NOT NULL" not in sql


def test_every_declared_dtype_maps_to_a_warehouse_type() -> None:
    """THE MAPPING IS A TABLE SO AN UNMAPPED DTYPE FAILS AT THE LOOKUP rather
    than silently becoming STRING -- which is how a BIGINT key turns into text
    and every join stops matching."""
    for model in TABLES.values():
        for spec in model.to_schema().columns.values():
            assert str(spec.dtype) in SQL_TYPES, spec.dtype


def test_an_unmapped_dtype_refuses_to_render() -> None:
    """FAIL LOUD, NOT DEFAULT. Guessing STRING would produce a table that looks
    created and holds the wrong type."""
    unmapped = pa.DataFrameSchema({"thing": pa.Column(pd.CategoricalDtype())})

    with pytest.raises(SystemExit) as caught:
        ddl_for("t", unmapped, TARGET)

    # THE COLUMN, NOT JUST THE DTYPE. A mutant formatting with None survived:
    # the reader would learn that some `category` column is unmapped without
    # learning which one, in a table that may have thirty.
    assert str(caught.value) == (
        "no SQL type mapped for pandera dtype 'category' (column 'thing'); add it to SQL_TYPES"
    )


def test_the_target_qualifies_every_table() -> None:
    """A BARE TABLE NAME RESOLVES AGAINST WHATEVER THE SESSION HAPPENS TO HOLD,
    which on a shared metastore may be another project's schema."""
    for name in TABLES:
        assert f"{TARGET}.{name} (" in ddl_for(name, TABLES[name].to_schema(), TARGET)


def test_the_cli_creates_every_contract_table(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    """main() HAD NO TEST -- SIXTEEN MUTANTS WITH NO COVERAGE.

    It is the whole bootstrap step: the reason this module exists is that
    contracts were gated and published while nothing had ever created a table.
    An entry point nobody runs in a test is the same gap one layer up.
    """
    import sys

    from cscie103_olap_oltp.oltp import tables

    issued: list[str] = []
    monkeypatch.setattr(tables, "execute", issued.append)
    monkeypatch.setattr(sys, "argv", ["tables", TARGET])

    assert tables.main() == 0
    assert len(issued) == len(TABLES), "every contract table must be created"

    out = capsys.readouterr().out
    for name in TABLES:
        assert f"ok      {TARGET}.{name}" in out
        assert any(f"{TARGET}.{name} (" in statement for statement in issued)


def test_the_cli_refuses_without_a_target(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A MISSING TARGET MUST NOT DEFAULT. Creating tables in whatever schema the
    session happens to hold is how one project writes into another's."""
    import sys

    from cscie103_olap_oltp.oltp import tables

    monkeypatch.setattr(sys, "argv", ["tables"])

    with pytest.raises(SystemExit) as caught:
        tables.main()

    assert "catalog.schema" in str(caught.value)


def test_each_message_carries_the_fact_its_reader_needs() -> None:
    """ONE ASSERTION PER TEMPLATE, WHICH THE HOISTING MADE POSSIBLE."""
    from cscie103_olap_oltp.oltp.tables import TABLE_FOOTER, UNMAPPED_DTYPE, USAGE

    assert "SQL_TYPES" in UNMAPPED_DTYPE
    assert "{dtype!r}" in UNMAPPED_DTYPE and "{column!r}" in UNMAPPED_DTYPE
    assert "delta.enableChangeDataFeed = true" in TABLE_FOOTER
    assert TABLE_FOOTER.startswith("\n)"), "the column list must be closed"
    assert "catalog.schema" in USAGE
