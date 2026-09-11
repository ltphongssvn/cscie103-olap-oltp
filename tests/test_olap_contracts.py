# tests/test_olap_contracts.py
"""The OLAP star, and why it deliberately breaks the rules the OLTP side keeps.

    Schema as Code           dimensions and facts are declared, not discovered
    Transformation as Code   the shape change is a reviewable artifact
    Partitioning as Code     physical organisation is stated, not incidental

BOTH MODELS ARE CORRECT, AND THAT IS THE PROJECT'S WHOLE SUBJECT.

3NF is right for the OLTP side because it is WRITTEN TO: one fact in one place,
so an update cannot leave two rows disagreeing. It is poor for analytics, where
answering a question means many joins.

This side DENORMALISES exactly what 3NF removed. `dim_product` carries
`category_name` directly -- the transitive dependency third normal form exists
to eliminate. Snowflaking it back out is almost never worth it in a modern
columnar warehouse: compression handles the redundancy, while the extra join
slows queries and confuses readers.

SURROGATE KEYS ARE WHAT MAKE HISTORY POSSIBLE. A natural key cannot distinguish
the "before" and "after" versions of the same entity, so SCD Type 2 needs an
integer key that identifies the VERSION, with the business key kept alongside as
an attribute.

THE UNKNOWN MEMBER IS WHY KEYS ALLOW -1. The rule is to create an "Unknown" row
in each dimension and NEVER leave a null foreign key in a fact -- a null breaks
inner joins silently and drops rows from every total. Reserving -1 is what lets
an unresolved reference stay countable.
"""

import pandas as pd
import pytest
from pandera.errors import SchemaError, SchemaErrors

from cscie103_olap_oltp.olap.contracts import (
    UNKNOWN_KEY,
    DimCustomer,
    DimDate,
    DimProduct,
    FactOrderLine,
)

SchemaFailure = (SchemaError, SchemaErrors)


def _dim_product() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "product_key": [UNKNOWN_KEY, 1, 2, 3],
            "product_id": [-1, 100, 100, 200],
            "product_name": ["Unknown", "Hammer", "Hammer", "SICP"],
            # DENORMALISED ON PURPOSE. See the module docstring.
            "category_name": ["Unknown", "Tools", "Tools", "Books"],
            "list_price": [0.0, 9.99, 11.99, 55.00],
            "valid_from": pd.to_datetime(["1900-01-01", "2026-01-01", "2026-06-01", "2026-01-01"]),
            "valid_to": pd.Series([pd.NaT, pd.Timestamp("2026-06-01"), pd.NaT, pd.NaT]),
            "is_current": [True, False, True, True],
        }
    )


def _dim_customer() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "customer_key": [UNKNOWN_KEY, 1, 2],
            "customer_id": [-1, 1, 2],
            "full_name": ["Unknown", "Ada Lovelace", "Alan Turing"],
            "email_domain": ["unknown", "example.test", "example.test"],
            "valid_from": pd.to_datetime(["1900-01-01", "2026-01-01", "2026-01-01"]),
            "valid_to": pd.Series([pd.NaT, pd.NaT, pd.NaT]),
            "is_current": [True, True, True],
        }
    )


def _dim_date() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date_key": [20260201, 20260202],
            "calendar_date": pd.to_datetime(["2026-02-01", "2026-02-02"]),
            "year": [2026, 2026],
            "quarter": [1, 1],
            "month": [2, 2],
            "day_of_month": [1, 2],
            "is_weekend": [True, False],
        }
    )


def _fact() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": [1000, 1000, 1001],
            "line_number": [1, 2, 1],
            "customer_key": [1, 1, 2],
            "product_key": [1, 3, 1],
            "date_key": [20260201, 20260201, 20260202],
            "quantity": [2, 1, 3],
            "unit_price": [9.99, 55.00, 9.99],
            "line_revenue": [19.98, 55.00, 29.97],
        }
    )


def test_the_compliant_star_validates() -> None:
    DimProduct.validate(_dim_product())
    DimCustomer.validate(_dim_customer())
    DimDate.validate(_dim_date())
    FactOrderLine.validate(_fact())


def test_a_dimension_carries_denormalised_attributes() -> None:
    """THE TRADE, ASSERTED SO IT CANNOT BE QUIETLY UNDONE.

    Someone "tidying up" dim_product by extracting category_name into its own
    table would restore 3NF and remove the reason this model exists.
    """
    assert "category_name" in set(DimProduct.to_schema().columns)


def test_the_business_key_repeats_but_the_surrogate_key_does_not() -> None:
    """THE PROPERTY THAT MAKES SCD TYPE 2 POSSIBLE.

    product_id appears twice -- two versions of one product -- and that must be
    legal. product_key identifies the VERSION, so a duplicate there is a defect.
    """
    frame = _dim_product()
    assert frame["product_id"].duplicated().any()

    frame.loc[2, "product_key"] = 1
    with pytest.raises(SchemaFailure):
        DimProduct.validate(frame)


def test_the_unknown_member_is_a_legal_key() -> None:
    """NEVER A NULL FOREIGN KEY IN A FACT.

    A null silently breaks inner joins and drops rows from every total. The
    documented remedy is a reserved Unknown row, which is why the key range
    starts at -1 rather than 1.
    """
    assert UNKNOWN_KEY == -1
    DimProduct.validate(_dim_product())

    fact = _fact()
    fact.loc[0, "product_key"] = UNKNOWN_KEY
    FactOrderLine.validate(fact)


def test_a_key_below_the_unknown_member_is_refused() -> None:
    """-1 IS RESERVED, NOT A LICENCE FOR ARBITRARY NEGATIVES."""
    fact = _fact()
    fact.loc[0, "product_key"] = -2

    with pytest.raises(SchemaFailure):
        FactOrderLine.validate(fact)


def test_the_fact_grain_is_one_row_per_order_line() -> None:
    """DOCUMENTED AND ENFORCED. A duplicate at this grain double-counts revenue
    and nothing downstream notices -- the totals are simply wrong."""
    frame = _fact()
    frame.loc[2, ["order_id", "line_number"]] = [1000, 1]

    with pytest.raises(SchemaFailure):
        FactOrderLine.validate(frame)


def test_a_fact_carries_keys_and_measures_only() -> None:
    """A FACT TABLE IS NARROW BY DESIGN. Descriptive attributes live in
    dimensions; carrying product_name here would mean rewriting facts whenever
    a name changed."""
    columns = set(FactOrderLine.to_schema().columns)
    assert not (columns & {"product_name", "category_name", "full_name"})


def test_an_open_ended_version_has_no_end_date() -> None:
    """NULL valid_to MEANS "STILL TRUE", which is why that column alone is
    nullable."""
    frame = _dim_product()

    # VALIDATE FIRST, THEN ASSERT THE NULL. mypy narrows the result of pd.isna
    # to Never and calls everything after it unreachable -- so the order that
    # reads naturally hides the real assertion from the type checker.
    DimProduct.validate(frame)
    assert bool(pd.isna(frame.loc[2, "valid_to"]))


def test_a_negative_measure_is_refused() -> None:
    frame = _fact()
    frame.loc[0, "line_revenue"] = -1.0

    with pytest.raises(SchemaFailure):
        FactOrderLine.validate(frame)


def test_the_date_key_is_yyyymmdd() -> None:
    """AN INTEGER DATE KEY EXISTS FOR PARTITION PRUNING, not tidiness: the
    engine can skip whole partitions on a range predicate over it."""
    frame = _dim_date()
    assert frame["date_key"].tolist() == [20260201, 20260202]

    frame.loc[0, "date_key"] = 999
    with pytest.raises(SchemaFailure):
        DimDate.validate(frame)


def test_no_dimension_carries_personal_data() -> None:
    """PRIVACY SURVIVES THE SHAPE CHANGE.

    Denormalisation copies attributes, so a personal field on the OLTP side
    would now sit in many rows instead of one. The constraint holds on both
    sides or on neither.
    """
    forbidden = {"email", "phone", "address", "ssn", "date_of_birth"}

    for model in (DimCustomer, DimProduct, DimDate, FactOrderLine):
        assert not (set(model.to_schema().columns) & forbidden)
