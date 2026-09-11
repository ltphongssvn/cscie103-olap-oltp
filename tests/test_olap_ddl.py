# tests/test_olap_ddl.py
"""The pipeline's dimension schemas are DERIVED from the contracts.

    Schema as Code      one definition, two consumers
    Migration as Code   the DDL is generated, never typed

THE DEBT THIS CLOSES, RECORDED WHEN IT WAS INCURRED. pipelines/star.py declares
an inline schema per dimension, because an IDENTITY surrogate key requires a
specified schema. That left the pandera contract and the pipeline describing one
table in two places with nothing checking agreement -- the exact drift this
repository gates everywhere else.

A SINGLE SOURCE OF TRUTH MEANS EVERY DATA ELEMENT IS STORED EXACTLY ONCE, and
downstream artifacts are generated from it rather than written beside it.

WHY A GENERATED FILE AND NOT AN IMPORT. The pipeline cannot import the package:
`dlt` exists only in a pipeline runtime, and the error-code registry's package
walk already proved that boundary has to be physical. So the contracts render
JSON, the JSON is committed and gated, and the pipeline reads it at runtime.

THE INVARIANTS ARE THE DOMAIN'S, NOT THE FRAMEWORK'S. A dimension row is a
VERSION of a business entity, so its schema must carry three things or it is not
a dimension: a surrogate key identifying the version, the business key
identifying the entity, and the interval saying when the version was true.
"""

import json

import pytest

from cscie103_olap_oltp.olap.ddl import (
    SCD2_COLUMNS,
    SCHEMAS_PATH,
    generate,
    matches_models,
    streaming_table_schema,
)


def test_a_dimension_declares_a_generated_surrogate_key() -> None:
    """THE DOMAIN INVARIANT: a dimension row identifies a VERSION.

    Without a generated key the fact can only join on the business key, which
    stops identifying one row the moment history exists -- so the join fans out
    and every measure is multiplied.
    """
    assert "product_key BIGINT GENERATED ALWAYS AS IDENTITY" in streaming_table_schema(
        "dim_product"
    )


def test_a_dimension_declares_the_validity_interval() -> None:
    """REQUIRED BY THE DOMAIN AND BY THE FRAMEWORK.

    Databricks requires __START_AT and __END_AT in a specified SCD2 target
    schema, typed like the sequencing column. Omitting them deletes the columns
    the as-of join needs -- the failure that produced this module.
    """
    schema = streaming_table_schema("dim_product")

    for column in SCD2_COLUMNS:
        assert f"{column} TIMESTAMP" in schema


def test_a_dimension_carries_every_contract_column() -> None:
    """THE CONTRACT IS THE SOURCE. A column added to the pandera model appears
    here without anyone editing the pipeline."""
    from cscie103_olap_oltp.olap.contracts import DimProduct

    schema = streaming_table_schema("dim_product")

    # valid_from/valid_to/is_current are the CONTRACT's vocabulary for what
    # Lakeflow supplies as __START_AT/__END_AT, so the framework owns them.
    for column in set(DimProduct.to_schema().columns) - {
        "valid_from",
        "valid_to",
        "is_current",
    }:
        assert column in schema


def test_a_fact_has_no_declared_schema() -> None:
    """A FACT IS NOT A DIMENSION. It is the result of a query rather than a
    table AUTO CDC maintains, so it needs no declared schema and must not get
    one -- declaring it would freeze a shape the query already determines."""
    with pytest.raises(KeyError):
        streaming_table_schema("fact_order_line")


def test_only_versioned_dimensions_are_generated() -> None:
    """THE SELECTION IS DECLARED, AND THIS TEST FOUND TWO WEAKER VERSIONS.

    A `dim_` prefix swept in dim_date -- a conformed calendar, which has no
    history because a date does not change. Sniffing for validity columns was
    right but inferred the capability from its symptoms. Inheriting Versioned
    states it.
    """
    rendered = generate()

    assert set(rendered) == {"dim_customer", "dim_product"}
    assert "dim_date" not in rendered, "a calendar has no history to version"
    assert "fact_order_line" not in rendered, "a fact is a query result"


def test_the_committed_file_matches_the_models() -> None:
    """THE DRIFT GATE, WHICH IS THE WHOLE POINT. Editing a contract without
    regenerating fails here rather than in a pipeline update."""
    assert SCHEMAS_PATH.is_file(), "run `mise run contracts:generate`"
    assert matches_models(), "schemas are stale; run `mise run contracts:generate`"


def test_the_committed_file_is_parseable() -> None:
    """THE PIPELINE PARSES THIS AT RUNTIME, where a malformed file is a failed
    update rather than a failed test."""
    assert json.loads(SCHEMAS_PATH.read_text(encoding="utf-8"))
