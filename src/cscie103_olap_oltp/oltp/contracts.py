# src/cscie103_olap_oltp/oltp/contracts.py
"""The OLTP model, in third normal form, as executable contracts.

    Schema as Code         the table's shape is declared, not discovered
    Data Contract as Code  producer and consumer agree in a reviewable file
    Data Quality as Code   the agreement is CHECKED, and refuses bad rows

WHY PANDERA RATHER THAN MORE PYDANTIC. Pydantic is the RECORD boundary and this
project already uses it for verdicts, settings and ledger entries. A table is a
different boundary: uniqueness of a key, a grain that holds across every row, a
distribution. Pandera owns that, and validates one schema definition across
pandas and PySpark -- so these contracts move to Spark without being rewritten.
Hand-rolling a primary-key check would be the custom code this repository
refuses on principle.

WHY THIRD NORMAL FORM HERE AND A STAR LATER, WHICH IS THE PROJECT'S SUBJECT.
This side is WRITTEN TO, so every fact lives in exactly one place and an update
cannot leave two copies disagreeing. `category` is a table rather than a column
on `product` to remove the transitive dependency

    product -> category_id -> category_name

The OLAP side will collapse precisely that join back into a dimension, on
purpose, because it is READ FROM. Neither model is the correct one; they
optimise for different verbs, and holding both with contracts on each is what
makes the trade-off demonstrable instead of asserted.

NO PERSONAL DATA IS DECLARED, AND A TEST ENFORCES IT. The cheapest place to
refuse an email address is before a column exists to hold one -- `customer`
carries a domain, not an address, because the analytics this feeds needs the
domain and never the person.
"""

from __future__ import annotations

from typing import ClassVar

import pandas as pd
import pandera.pandas as pa
from pandera.typing import Series

__all__ = ["TABLES", "Category", "Customer", "Order", "OrderLine", "Product"]

# THE CLOSED SET OF ORDER STATES. Declared once, referenced by the contract, so
# a typo'd status is a validation failure rather than a new category that every
# downstream aggregate silently reports on its own row.
ORDER_STATUSES = ("placed", "shipped", "delivered", "cancelled")


class _Strict(pa.DataFrameModel):
    """Defaults every table in this model inherits.

    strict = True REJECTS COLUMNS THE SCHEMA DOES NOT DECLARE, which is how an
    upstream addition announces itself instead of arriving unnoticed and
    breaking a join months later.

    coerce = True PARSES RATHER THAN MERELY CHECKING. A CSV reader yields
    strings; refusing them would push a cast into every caller, which is the
    duplication this project removes elsewhere.
    """

    class Config:
        strict = True
        coerce = True


class Customer(_Strict):
    """Who placed an order. One row per customer."""

    customer_id: Series[int] = pa.Field(gt=0, unique=True)
    full_name: Series[str] = pa.Field(nullable=False)

    # A DOMAIN, NEVER AN ADDRESS. The analytics question is "which domains buy
    # what", and storing the local part would carry a person for no gain --
    # while making every artifact a PII finding.
    email_domain: Series[str] = pa.Field(nullable=False)

    registered_at: Series[pd.Timestamp] = pa.Field(nullable=False)


class Category(_Strict):
    """A product's category, SEPARATED TO REMOVE A TRANSITIVE DEPENDENCY.

    Held on `product` instead, renaming a category would mean updating every
    product row -- and a partial update leaves two names for one category.
    """

    category_id: Series[int] = pa.Field(gt=0, unique=True)
    category_name: Series[str] = pa.Field(nullable=False)


class Product(_Strict):
    """What was sold. One row per product."""

    product_id: Series[int] = pa.Field(gt=0, unique=True)
    product_name: Series[str] = pa.Field(nullable=False)
    category_id: Series[int] = pa.Field(gt=0)

    # THE CURRENT CATALOGUE PRICE, WHICH IS NOT WHAT WAS CHARGED. order_line
    # keeps its own unit_price precisely because this one changes; conflating
    # them is how historical revenue silently restates itself.
    list_price: Series[float] = pa.Field(gt=0)


class Order(_Strict):
    """A placed order. One row per order."""

    order_id: Series[int] = pa.Field(gt=0, unique=True)
    customer_id: Series[int] = pa.Field(gt=0)
    ordered_at: Series[pd.Timestamp] = pa.Field(nullable=False)
    status: Series[str] = pa.Field(isin=ORDER_STATUSES)


class OrderLine(_Strict):
    """One line of an order. THE GRAIN IS (order_id, line_number).

    order_id ALONE IS NOT UNIQUE HERE, by design -- an order has many lines.
    Asserting the composite key is what stops a later fact table from
    double-counting revenue, which is the classic failure when a fact is built
    at a grain nobody stated.
    """

    order_id: Series[int] = pa.Field(gt=0)
    line_number: Series[int] = pa.Field(gt=0)
    product_id: Series[int] = pa.Field(gt=0)
    quantity: Series[int] = pa.Field(gt=0)

    # THE PRICE ACTUALLY CHARGED, captured at the time of sale. See Product.
    unit_price: Series[float] = pa.Field(gt=0)

    class Config:
        strict = True
        coerce = True
        # THE COMPOSITE KEY, EXPRESSED WHERE PANDERA CAN ENFORCE IT.
        #
        # ClassVar because a mutable class attribute is shared by every
        # instance -- ruff's RUF012 flags it, and it is right to: an inherited
        # Config that appended to this list would silently change the key of
        # every table that inherits it.
        unique: ClassVar[list[str]] = ["order_id", "line_number"]


# THE MODEL AS DATA, so a loader, a migration or a report can iterate the tables
# rather than hardcoding five names in three places.
TABLES: dict[str, type[pa.DataFrameModel]] = {
    "customer": Customer,
    "category": Category,
    "product": Product,
    "order": Order,
    "order_line": OrderLine,
}
