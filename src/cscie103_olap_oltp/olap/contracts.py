# src/cscie103_olap_oltp/olap/contracts.py
"""The OLAP star: dimensions and one fact, as executable contracts.

    Schema as Code           the shape is declared, not discovered
    Transformation as Code   the denormalisation is reviewable
    Partitioning as Code     the physical key is chosen for a reason

WHY THIS DELIBERATELY BREAKS WHAT oltp/contracts.py KEEPS.

The OLTP model is in third normal form because it is WRITTEN TO. This model is
READ FROM, so it collapses the joins 3NF creates. `dim_product` carries
`category_name` directly -- precisely the transitive dependency normalisation
removes.

SNOWFLAKING IT BACK OUT WOULD BE THE MISTAKE, not the fix. In a columnar
warehouse, compression absorbs the redundancy while the extra join slows every
query and costs a reader one more table to understand. The exception is a
dimension of millions of rows with a deep attribute hierarchy, which this is not.

SURROGATE KEYS CARRY HISTORY; BUSINESS KEYS CANNOT. A natural key has no way to
distinguish the "before" and "after" of the same entity, so each version gets an
integer key of its own and the business key rides along as an attribute. Without
that, an SCD2 load can expire the row it just inserted, because the only handle
it has matches both.

THE UNKNOWN MEMBER IS WHY KEYS START AT -1. The rule is a reserved "Unknown" row
in every dimension and NEVER a null foreign key in a fact: a null breaks inner
joins silently and quietly drops rows from every total. Reserving -1 keeps an
unresolved reference visible and countable.
"""

from __future__ import annotations

from typing import ClassVar

import pandas as pd
import pandera.pandas as pa
from pandera.typing import Series

__all__ = [
    "TABLES",
    "UNKNOWN_KEY",
    "DimCustomer",
    "DimDate",
    "DimProduct",
    "FactOrderLine",
]

# THE RESERVED SURROGATE KEY for the Unknown member of every dimension.
# Named once so the reason travels with the value.
UNKNOWN_KEY = -1

# yyyymmdd BOUNDS. The lower bound rejects a bare ordinal or a stray small
# integer; the upper keeps a typo'd year from passing as a date.
_MIN_DATE_KEY = 19000101
_MAX_DATE_KEY = 99991231


class _Star(pa.DataFrameModel):
    """Defaults every table in the star inherits.

    strict REFUSES AN UNDECLARED COLUMN, so an upstream addition announces
    itself rather than arriving unnoticed. coerce parses rather than merely
    checking, which keeps a cast out of every caller.
    """

    class Config:
        strict = True
        coerce = True


class DimProduct(_Star):
    """What was sold, versioned. SCD TYPE 2: one row per product per version."""

    product_key: Series[int] = pa.Field(ge=UNKNOWN_KEY, unique=True)

    # THE BUSINESS KEY, KEPT AS AN ATTRIBUTE AND DELIBERATELY NOT UNIQUE.
    # Several rows share it -- that is what "versioned" means.
    product_id: Series[int] = pa.Field(ge=UNKNOWN_KEY)

    product_name: Series[str] = pa.Field(nullable=False)

    # DENORMALISED FROM `category`. See the module docstring.
    category_name: Series[str] = pa.Field(nullable=False)

    list_price: Series[float] = pa.Field(ge=0)

    valid_from: Series[pd.Timestamp] = pa.Field(nullable=False)

    # NULL MEANS "STILL TRUE", which is why this column alone is nullable.
    valid_to: Series[pd.Timestamp] = pa.Field(nullable=True)

    is_current: Series[bool] = pa.Field(nullable=False)


class DimCustomer(_Star):
    """Who bought, versioned. SCD Type 2, same shape as DimProduct."""

    customer_key: Series[int] = pa.Field(ge=UNKNOWN_KEY, unique=True)
    customer_id: Series[int] = pa.Field(ge=UNKNOWN_KEY)
    full_name: Series[str] = pa.Field(nullable=False)

    # A DOMAIN, NEVER AN ADDRESS -- carried through from the OLTP contract.
    # Denormalisation copies attributes, so a personal field here would be
    # duplicated across every version rather than held once.
    email_domain: Series[str] = pa.Field(nullable=False)

    valid_from: Series[pd.Timestamp] = pa.Field(nullable=False)
    valid_to: Series[pd.Timestamp] = pa.Field(nullable=True)
    is_current: Series[bool] = pa.Field(nullable=False)


class DimDate(_Star):
    """A conformed calendar. NOT VERSIONED -- a date does not change.

    PRE-CALCULATED ATTRIBUTES, so no query ever derives a quarter from a
    timestamp at runtime, and every report agrees on what a quarter is.
    """

    # AN INTEGER yyyymmdd KEY, CHOSEN FOR PARTITION PRUNING rather than
    # tidiness: a range predicate over it lets the engine skip whole partitions
    # without reading them.
    date_key: Series[int] = pa.Field(ge=_MIN_DATE_KEY, le=_MAX_DATE_KEY, unique=True)

    calendar_date: Series[pd.Timestamp] = pa.Field(nullable=False)
    year: Series[int] = pa.Field(ge=1900, le=9999)
    quarter: Series[int] = pa.Field(ge=1, le=4)
    month: Series[int] = pa.Field(ge=1, le=12)
    day_of_month: Series[int] = pa.Field(ge=1, le=31)
    is_weekend: Series[bool] = pa.Field(nullable=False)


class FactOrderLine(_Star):
    """One row per order line. THE GRAIN, STATED AND ENFORCED.

    KEYS AND MEASURES ONLY. Descriptive attributes belong in dimensions;
    carrying a product name here would mean rewriting facts whenever a name
    changed, which is the write amplification the star accepts once and should
    not accept twice.
    """

    # THE DEGENERATE DIMENSION: the order identifier has no attributes of its
    # own, so it lives on the fact rather than in a dimension holding one column.
    order_id: Series[int] = pa.Field(gt=0)
    line_number: Series[int] = pa.Field(gt=0)

    # FOREIGN KEYS, NEVER NULL. UNKNOWN_KEY is the documented alternative.
    customer_key: Series[int] = pa.Field(ge=UNKNOWN_KEY, nullable=False)
    product_key: Series[int] = pa.Field(ge=UNKNOWN_KEY, nullable=False)
    date_key: Series[int] = pa.Field(ge=_MIN_DATE_KEY, le=_MAX_DATE_KEY, nullable=False)

    # ADDITIVE MEASURES. Each sums meaningfully across every dimension, which is
    # what makes this fact table safe to aggregate without qualification.
    quantity: Series[int] = pa.Field(gt=0)
    unit_price: Series[float] = pa.Field(ge=0)
    line_revenue: Series[float] = pa.Field(ge=0)

    class Config:
        strict = True
        coerce = True
        # THE GRAIN AS A CONSTRAINT. ClassVar because a mutable class attribute
        # is shared by every subclass, and a Config that appended to it would
        # silently change the grain of anything inheriting.
        unique: ClassVar[list[str]] = ["order_id", "line_number"]


# THE STAR AS DATA, so a loader or a report iterates rather than repeating names.
TABLES: dict[str, type[pa.DataFrameModel]] = {
    "dim_customer": DimCustomer,
    "dim_date": DimDate,
    "dim_product": DimProduct,
    "fact_order_line": FactOrderLine,
}
