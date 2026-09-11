# tests/test_oltp_contracts.py
"""The OLTP model, in third normal form, as executable contracts.

    Schema as Code         the table's shape is declared, not discovered
    Data Contract as Code  producer and consumer agree in a reviewable file
    Data Quality as Code   the agreement is CHECKED, and refuses bad rows

WHY PANDERA AND NOT MORE PYDANTIC. Pydantic validates ONE OBJECT AT A TIME --
it is the record boundary, and it is already used for verdicts, settings and
ledger entries. A table is a different boundary: uniqueness of a key, a foreign
key that resolves, a grain that holds across millions of rows. Pandera is built
for that, validates the same schema across pandas and PySpark, and this project
already declares it as a dependency. Writing row loops to check a primary key
would be the custom code this repository refuses.

WHY 3NF HERE AND A STAR LATER, WHICH IS THE WHOLE POINT OF THE PROJECT.
The OLTP side is normalised because it is written to: one fact in one place, so
an update cannot leave two copies disagreeing. `category` is its own table
rather than a column on `product` precisely to remove the transitive dependency
product -> category -> category_name.

The OLAP side will DENORMALISE exactly that, on purpose, because it is read
from. Both are correct; they optimise for different verbs, and having the two
models side by side with contracts on each is what makes the difference
demonstrable rather than asserted.
"""

import json

import pandas as pd
import pytest
from pandera.errors import SchemaError, SchemaErrors

from cscie103_olap_oltp.oltp.contracts import (
    Category,
    Customer,
    Order,
    OrderLine,
    Product,
)

# BOTH FORMS, BECAUSE PANDERA RAISES EITHER AND THEY ARE NOT RELATED.
#
# A single failing check raises SchemaError; column-level strictness collects
# every problem first and raises SchemaErrors (plural). Catching only the
# singular passed eight tests and failed the ninth for a reason that looked like
# the contract was wrong when it had worked exactly as intended.
#
# COLLECTING IS THE BETTER BEHAVIOUR and worth keeping: one run reports every
# offending column rather than the first.
SchemaFailure = (SchemaError, SchemaErrors)


def _customers() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "customer_id": [1, 2],
            "full_name": ["Ada Lovelace", "Alan Turing"],
            "email_domain": ["example.test", "example.test"],
            "registered_at": pd.to_datetime(["2026-01-01", "2026-01-02"]),
        }
    )


def _categories() -> pd.DataFrame:
    return pd.DataFrame({"category_id": [10, 20], "category_name": ["Tools", "Books"]})


def _products() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "product_id": [100, 200],
            "product_name": ["Hammer", "SICP"],
            "category_id": [10, 20],
            "list_price": [9.99, 55.00],
        }
    )


def _orders() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": [1000, 1001],
            "customer_id": [1, 2],
            "ordered_at": pd.to_datetime(["2026-02-01", "2026-02-02"]),
            "status": ["placed", "shipped"],
        }
    )


def _order_lines() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": [1000, 1000, 1001],
            "line_number": [1, 2, 1],
            "product_id": [100, 200, 100],
            "quantity": [2, 1, 3],
            "unit_price": [9.99, 55.00, 9.99],
        }
    )


def test_the_compliant_fixtures_validate() -> None:
    """THE BASELINE. Every negative test below is one of these with a single
    field changed, so a failure names the rule rather than the fixture."""
    Customer.validate(_customers())
    Category.validate(_categories())
    Product.validate(_products())
    Order.validate(_orders())
    OrderLine.validate(_order_lines())


def test_a_duplicate_primary_key_is_refused() -> None:
    """THE DEFINING PROPERTY OF A KEY, AND THE ONE A TYPE CANNOT EXPRESS.

    `customer_id: int` is satisfied by a column of identical values. Uniqueness
    is a property of the COLUMN, which is exactly why this belongs to a table
    validator rather than to pydantic.
    """
    frame = _customers()
    frame.loc[1, "customer_id"] = 1

    with pytest.raises(SchemaFailure):
        Customer.validate(frame)


def test_a_null_in_a_required_field_is_refused() -> None:
    """PANDERA TREATS EVERY COLUMN AS NON-NULLABLE UNLESS TOLD OTHERWISE, which
    is the safe default: a nullable key is a key that does not identify."""
    frame = _customers()
    frame.loc[0, "full_name"] = None

    with pytest.raises(SchemaFailure):
        Customer.validate(frame)


def test_an_unexpected_column_is_refused() -> None:
    """strict = True REJECTS COLUMNS NOT IN THE SCHEMA.

    An extra column is how an upstream change arrives silently. Refusing it
    turns a schema drift into a failed load rather than a surprise in a
    downstream join six months later.
    """
    frame = _customers()
    frame["loyalty_tier"] = "gold"

    with pytest.raises(SchemaFailure):
        Customer.validate(frame)


def test_a_negative_quantity_is_refused() -> None:
    """A DOMAIN RULE, NOT A TYPE RULE. int accepts -3 happily; the business
    does not."""
    frame = _order_lines()
    frame.loc[0, "quantity"] = -3

    with pytest.raises(SchemaFailure):
        OrderLine.validate(frame)


def test_a_negative_price_is_refused() -> None:
    frame = _products()
    frame.loc[0, "list_price"] = -1.0

    with pytest.raises(SchemaFailure):
        Product.validate(frame)


def test_an_unknown_order_status_is_refused() -> None:
    """THE STATUS SET IS CLOSED. A typo'd status silently creates a new
    category that every downstream aggregate then reports separately."""
    frame = _orders()
    frame.loc[0, "status"] = "plcaed"

    with pytest.raises(SchemaFailure):
        Order.validate(frame)


def test_the_order_line_grain_is_one_row_per_order_and_line() -> None:
    """A COMPOSITE KEY IS STILL A KEY.

    order_id alone is not unique here by design -- an order has many lines --
    so the grain is the PAIR, and asserting it is what keeps a later fact table
    from double-counting revenue.
    """
    frame = _order_lines()
    frame.loc[2, ["order_id", "line_number"]] = [1000, 1]

    with pytest.raises(SchemaFailure):
        OrderLine.validate(frame)


def test_no_contract_carries_personal_data() -> None:
    """PRIVACY AS CODE, AT THE MODEL RATHER THAN IN A SCANNER.

    The PII gate reads files; this reads the CONTRACT. A schema that declares an
    `email` column invites a pipeline to carry one, and the cheapest place to
    refuse personal data is before a column for it exists.
    """
    forbidden = {"email", "phone", "address", "ssn", "date_of_birth"}

    for model in (Customer, Category, Product, Order, OrderLine):
        columns = set(model.to_schema().columns)
        assert not (columns & forbidden), f"{model.__name__} declares personal data"


def test_the_published_contract_covers_every_table() -> None:
    """A CONTRACT MISSING A TABLE IS WORSE THAN NONE: a consumer reads it,
    finds four entities, and concludes the fifth does not exist."""
    from cscie103_olap_oltp.oltp.contracts import TABLES
    from cscie103_olap_oltp.oltp.publish import generate

    assert set(generate()["tables"]) == set(TABLES)


def test_the_published_contract_carries_the_checks() -> None:
    """THE REASON to_json() REPLACED to_json_schema().

    The first version published the JSON-Schema projection, which renders the
    columnar shape and DROPS every check -- so `unique` and `gt=0` vanished and
    a consumer saw none of the rules the producer enforces.

    Asserting the checks are present is what keeps the lossy form from quietly
    coming back.
    """
    from cscie103_olap_oltp.oltp.publish import generate

    customer = generate()["tables"]["customer"]
    assert customer["columns"]["customer_id"]["unique"] is True
    assert customer["columns"]["customer_id"]["greater_than"] == 0
    assert customer["strict"] is True


def test_the_published_contract_round_trips() -> None:
    """THE PROPERTY THAT MAKES IT A CONTRACT RATHER THAN A REPORT.

    pandera can load this document back into a working schema, so the published
    file IS the validator -- not a description of one that may have drifted.
    """
    import pandera.pandas as pa

    from cscie103_olap_oltp.oltp.publish import generate

    restored = pa.DataFrameSchema.from_json(json.dumps(generate()["tables"]["customer"]))
    restored.validate(_customers())
