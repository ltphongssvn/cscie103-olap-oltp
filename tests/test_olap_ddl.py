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
import sys
from pathlib import Path

import pytest

from cscie103_olap_oltp.olap.ddl import (
    SCD2_COLUMNS,
    SCHEMAS_PATH,
    UNMAPPED_DTYPE_FIX,
    UnmappedDtypeError,
    _columns,
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


def test_an_unmapped_dtype_fails_loudly_and_classified(tmp_path: Path) -> None:
    """SystemExit(None) EXITS WITH STATUS ZERO, WHICH IS WHY THIS IS AN ERROR.

    The raise was `SystemExit(<message>)`. A mutant blanking the message
    survived, and that mutant is not cosmetic: SystemExit(None) terminates
    successfully, so the generator would emit a schema missing a column and the
    gate would report success.

    A CLASSIFIED ERROR CARRIES WHAT SystemExit CANNOT -- a code to aggregate
    on, a remediation, and the facts needed to fix it.
    """
    import pandas as pd
    import pandera.pandas as pa

    unmapped = pa.DataFrameSchema({"thing": pa.Column(pd.CategoricalDtype())})

    with pytest.raises(UnmappedDtypeError) as caught:
        _columns(unmapped)

    assert caught.value.code == "ERR_UNMAPPED_DTYPE"
    assert caught.value.context["column"] == "thing"
    # THE REMEDIATION AS THE ERROR CARRIES IT, not as the module declares it.
    # Two mutants survived while only UNMAPPED_DTYPE_FIX itself was asserted:
    # the constant was intact and the error could still have been given a
    # different, corrupted one.
    assert caught.value.remediation == UNMAPPED_DTYPE_FIX
    assert str(caught.value).endswith(f"fix: {UNMAPPED_DTYPE_FIX}")


def test_a_missing_file_does_not_report_a_match(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """THE DRIFT GATE MUST FAIL CLOSED ON AN ABSENT ARTIFACT.

    `is_file() and content == generated` became `or` under mutation, so a
    repository with no generated schemas reported "matches" -- a gate passing
    because there was nothing to check.
    """
    from cscie103_olap_oltp.olap import ddl

    monkeypatch.setattr(ddl, "SCHEMAS_PATH", Path("/nonexistent/schemas.json"))

    assert ddl.matches_models() is False


def test_the_cli_regenerates_and_then_reports_a_match(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    """main() HAD NO TEST -- THIRTY-SIX MUTANTS MARKED "no coverage".

    This is the entry point `mise run contracts:generate` and the streaming
    tables gate both call, so its behaviour was entirely unverified: nothing
    proved --generate writes a file or that --check agrees with it afterwards.
    """
    from cscie103_olap_oltp.olap import ddl

    target = tmp_path / "schemas.json"
    monkeypatch.setattr(ddl, "SCHEMAS_PATH", target)

    monkeypatch.setattr(sys, "argv", ["ddl", "--generate"])
    assert ddl.main() == 0
    assert target.is_file()

    monkeypatch.setattr(sys, "argv", ["ddl", "--check"])
    assert ddl.main() == 0
    assert capsys.readouterr().out.endswith("schemas.json matches the dimension contracts\n")


def test_the_cli_fails_when_the_committed_file_is_stale(  # type: ignore[no-untyped-def]
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A STALE ARTIFACT IS THE ONE EVENT THIS GATE EXISTS TO CATCH.

    The pipeline reads this file at runtime, so a drift that returns 0 here
    becomes a failed pipeline update -- the slowest place to learn a column was
    renamed.
    """
    from cscie103_olap_oltp.olap import ddl

    target = tmp_path / "schemas.json"
    target.write_text('{"dim_product": "stale"}\n')
    monkeypatch.setattr(ddl, "SCHEMAS_PATH", target)
    monkeypatch.setattr(sys, "argv", ["ddl", "--check"])

    assert ddl.main() == 1
    # THE FILE'S NAME, RENDERED. A mutant formatting with None survived: the
    # gate would report that "None no longer matches", naming no file.
    err = capsys.readouterr().err
    assert err.startswith("schemas.json no longer matches")
    assert "contracts:generate" in err


def test_the_cli_refuses_an_unknown_argument(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """AN UNRECOGNISED FLAG MUST NOT SILENTLY DO NOTHING."""
    from cscie103_olap_oltp.olap import ddl

    monkeypatch.setattr(sys, "argv", ["ddl", "--wat"])

    with pytest.raises(SystemExit):
        ddl.main()


def test_the_unmapped_dtype_error_names_the_dtype_it_could_not_map() -> None:
    """THE DTYPE IS THE ONE FACT THAT MAKES THE ERROR ACTIONABLE.

    Mutants blanking or dropping it survived: the test checked the column and
    the table. Without the dtype, "add it to SQL_TYPES" cannot be followed --
    the reader knows where to edit but not what to add.
    """
    import pandas as pd
    import pandera.pandas as pa

    with pytest.raises(UnmappedDtypeError) as caught:
        _columns(pa.DataFrameSchema({"thing": pa.Column(pd.CategoricalDtype())}))

    assert caught.value.context["dtype"] == "category"

    # THE RENDERED MESSAGE, NOT JUST THE FIELD. Mutants blanking the message or
    # formatting it with None survived while only the context was asserted --
    # the structured facts were right and the sentence a human reads said
    # "dtype None", or nothing at all.
    assert str(caught.value).startswith("[ERR_UNMAPPED_DTYPE] no SQL type mapped")
    assert "'category'" in str(caught.value)


def test_a_path_inside_the_repository_is_shown_relative() -> None:
    """THE SHORT FORM IS THE POINT OF THE HELPER.

    A mutant returning str(None) survived because the fallback test only proved
    it did not raise. "None" is not a path, and a tool reporting where it wrote
    a file must report where it wrote the file.
    """
    from cscie103_olap_oltp.olap.ddl import _display
    from cscie103_olap_oltp.policy.snapshot import REPO_ROOT

    assert _display(REPO_ROOT / "contracts" / "x.json") == "contracts/x.json"


def test_a_path_outside_the_repository_is_shown_in_full(tmp_path: Path) -> None:
    """THE FALLBACK MUST STILL IDENTIFY THE FILE.

    tmp_path RATHER THAN A LITERAL /tmp, which the linter flags: a fixed
    temporary path is predictable and therefore hijackable, and the fixture
    gives a unique directory per test at no cost.
    """
    from cscie103_olap_oltp.olap.ddl import _display

    outside = tmp_path / "x.json"

    assert _display(outside) == str(outside)


def test_generating_creates_the_directory_recursively(tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    """parents=True IS LOAD-BEARING ON A FRESH CHECKOUT.

    contracts/ may not exist, and a mutant weakening this survived because
    every other test wrote into a directory that already existed -- the one
    condition that matters was never tried.
    """
    from cscie103_olap_oltp.olap import ddl

    nested = tmp_path / "contracts" / "generated" / "schemas.json"
    monkeypatch.setattr(ddl, "SCHEMAS_PATH", nested)
    monkeypatch.setattr(sys, "argv", ["ddl", "--generate"])

    assert ddl.main() == 0
    assert nested.is_file()
    assert str(nested) in capsys.readouterr().out


def test_the_committed_file_is_sorted_and_indented() -> None:
    """THE SERIALISED FORM IS A COMMITTED ARTIFACT, so its shape is a contract.

    Mutants dropping sort_keys or the indent survived. Without sorting, the key
    order follows dict insertion and an unrelated edit produces a spurious diff
    -- a drift gate that cries wolf is one people learn to re-run until green.
    """
    from cscie103_olap_oltp.olap.ddl import _serialise

    rendered = _serialise({"b": "two", "a": "one"})

    assert rendered == '{\n  "a": "one",\n  "b": "two"\n}\n'


def test_each_message_carries_the_fact_its_reader_needs() -> None:
    """ONE ASSERTION PER TEMPLATE, WHICH THE HOISTING MADE POSSIBLE.

    Inline, each message was several adjacent literals and every fragment was
    separately mutable -- ten mutants corrupted mid-sentence text that no test
    could kill without pinning the wording word for word.
    """
    from cscie103_olap_oltp.olap.ddl import (
        FRESH_SCHEMAS,
        STALE_SCHEMAS,
        UNMAPPED_DTYPE_FIX,
        USAGE,
    )

    assert "contracts:generate" in STALE_SCHEMAS, "a failure must name its own fix"
    assert "{name}" in STALE_SCHEMAS and "{name}" in FRESH_SCHEMAS
    assert "SQL_TYPES" in UNMAPPED_DTYPE_FIX
    assert "STRING" in UNMAPPED_DTYPE_FIX, "the silent-default consequence is the point"
    assert "--generate" in USAGE and "--check" in USAGE


def test_an_unknown_argument_exits_non_zero_with_a_message(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """SystemExit(None) EXITS WITH STATUS ZERO.

    A mutant blanking the usage message survived, and it is not cosmetic: an
    unrecognised flag would terminate successfully, so `contracts:generate`
    would report done having written nothing.
    """
    from cscie103_olap_oltp.olap import ddl

    monkeypatch.setattr(sys, "argv", ["ddl", "--wat"])

    with pytest.raises(SystemExit) as caught:
        ddl.main()

    assert caught.value.code is not None, "SystemExit(None) exits successfully"
    # THE OFFENDING ARGUMENT, RENDERED. Formatted with None the message says
    # "(got None)", which tells the reader nothing about what they typed.
    assert str(caught.value.code) == "usage: --generate | --check  (got ['--wat'])"


def test_the_message_names_the_dtype_it_actually_found() -> None:
    """A mutant formatting with None survived: the error would read "dtype
    None" while the structured field stayed correct -- so the sentence a human
    reads and the fact a query reads would disagree."""
    import pandas as pd
    import pandera.pandas as pa

    with pytest.raises(UnmappedDtypeError) as caught:
        _columns(pa.DataFrameSchema({"x": pa.Column(pd.CategoricalDtype())}))

    assert "dtype 'category'" in str(caught.value)
    assert "None" not in str(caught.value)


def test_the_suffix_alone_distinguishes_surrogate_from_natural_keys() -> None:
    """`_id` IS THE SOURCE'S KEY, `_key` IS THE WAREHOUSE'S, AND THAT IS THE
    WHOLE RULE.

    An earlier version also required the column's stem to appear in the table
    name -- a substring test joined by `and`, which mutation flipped to `or`.
    Under `or`, any column whose name occurred in the table name became an
    IDENTITY column, and a generated column the loader cannot write leaves a
    dimension whose key nothing populates.
    """
    schema = streaming_table_schema("dim_product")

    assert "product_key BIGINT GENERATED ALWAYS AS IDENTITY" in schema
    assert "product_id BIGINT" in schema
    assert "product_id BIGINT GENERATED" not in schema, "a natural key is not generated"
    assert schema.count("GENERATED ALWAYS AS IDENTITY") == 1, "exactly one surrogate key"
