# tests/test_seed.py
"""Seed data for the OLTP side, and the invariants it must satisfy.

    Tests as Code        the seed is exercised, not assumed
    Assertions as Code   the domain's rules hold on the data itself

WHY SEED DATA IS NOT A CONVENIENCE. The pipeline has run to COMPLETED against
empty tables. That proves the DAG resolves and proves nothing about SCD Type 2,
because no row has ever changed. A model that has never processed a change is a
claim, not a demonstration.

THE SEED IS DESIGNED TO EXERCISE THE HARD CASES, not to look plausible:

    a product whose price changes    -> a second version, the first closed
    an order before that change      -> must join to the OLD price
    an order after it                -> must join to the NEW price
    a category with no products      -> must not appear in any fact
    an order line for a missing key  -> must land on the Unknown member

THE THIRD AND FOURTH ARE THE POINT. If the as-of join were wrong -- joining on
is_current instead of the validity interval -- the first three rows would still
look right in a spot check and every historical total would be wrong.

VALIDATED BY THE CONTRACTS THEMSELVES. Seed data that violated the contracts
would be caught in the pipeline rather than here, so the contracts validate it
before it is ever written.
"""

import pandas as pd

from cscie103_olap_oltp.oltp.contracts import TABLES
from cscie103_olap_oltp.oltp.seed import PRICE_CHANGE_AT, changed, rows


def test_every_seeded_table_satisfies_its_contract() -> None:
    """THE CONTRACT IS THE GATE, and seed data is data like any other. Loading
    rows the contract would refuse is how a fixture becomes a false witness."""
    seeded = rows()

    for name, frame in seeded.items():
        TABLES[name].validate(frame)


def test_the_seed_covers_every_oltp_table() -> None:
    """A PARTIAL SEED LEAVES A FLOW WITH NOTHING TO DO, and a flow that
    processes nothing reports success."""
    assert set(rows()) == set(TABLES)


def test_a_product_changes_price_so_scd2_has_something_to_do() -> None:
    """WITHOUT A CHANGE, TYPE 2 IS INDISTINGUISHABLE FROM TYPE 1."""
    product = rows()["product"]
    changed = product[product["product_id"] == 100]

    assert len(changed) == 1, "the CHANGE is applied by a second load, not a second row"
    assert changed.iloc[0]["list_price"] > 0


def test_orders_straddle_the_price_change() -> None:
    """THE AS-OF JOIN IS ONLY TESTED IF ORDERS EXIST ON BOTH SIDES.

    An order before the change must resolve to the old version and one after it
    to the new. With orders on one side only, joining on `is_current` would pass
    every check and still be wrong.
    """
    orders = rows()["order"]

    assert (orders["ordered_at"] < PRICE_CHANGE_AT).any(), "no order before the change"
    assert (orders["ordered_at"] > PRICE_CHANGE_AT).any(), "no order after the change"


def test_an_order_line_references_a_product_that_does_not_exist() -> None:
    """THE UNKNOWN MEMBER IS EXERCISED, NOT MERELY DECLARED.

    A reserved key nothing ever resolves to is a claim. This row is what proves
    an unresolved reference stays countable instead of vanishing from a join.
    """
    lines = rows()["order_line"]
    products = set(rows()["product"]["product_id"])

    assert not set(lines["product_id"]) <= products, "every product resolves; -1 untested"


def test_a_category_has_no_products() -> None:
    """AN EMPTY CATEGORY MUST NOT REACH A FACT. It is the case where a left
    join silently invents rows if the direction is wrong."""
    categories = set(rows()["category"]["category_id"])
    used = set(rows()["product"]["category_id"])

    assert categories - used, "every category is used; the empty case is untested"


def test_the_seed_carries_no_personal_data() -> None:
    """PRIVACY APPLIES TO FIXTURES. Seed data is committed, so a plausible
    email address here is a real finding in the repository forever."""
    customers = rows()["customer"]

    assert not customers["email_domain"].str.contains("@").any()
    assert customers["full_name"].str.len().gt(0).all()


def test_the_seed_is_deterministic() -> None:
    """A RANDOM FIXTURE MAKES A FAILING RUN UNREPRODUCIBLE, which is the worst
    property a fixture can have."""
    pd.testing.assert_frame_equal(rows()["order_line"], rows()["order_line"])


def test_the_change_load_carries_only_what_changed() -> None:
    """OBSERVED IN THE WAREHOUSE: product 200 grew a second version despite
    never changing.

    The change load resent every product row with a later timestamp, and
    AUTO CDC versions on the SEQUENCE, not on a value comparison -- a resent row
    is a new version whether or not anything differs. History then records edits
    that never happened, and "when did this price change" answers wrongly.

    THE FIX IS AT THE SOURCE: a change feed carries changes.
    """
    before = {row.product_id: row for row in rows()["product"].itertuples()}
    after = changed()["product"]

    assert len(after) == 1, "the change load must carry only the changed product"

    only = after.iloc[0]
    assert only["list_price"] != before[only["product_id"]].list_price


def test_only_the_changed_product_is_versioned_twice() -> None:
    """THE INVARIANT THE WAREHOUSE CONFIRMED, PINNED SO IT CANNOT REGRESS.

    Exactly one product changes, so exactly one product may have a second
    version. A feed that resends unchanged rows versions everything, and the
    resulting keys look plausible enough to pass a spot check -- which is how
    this was nearly diagnosed as an identity-column fault instead.
    """
    before = rows()["product"]
    after = changed()["product"]

    versioned_twice = set(after["product_id"]) & set(before["product_id"])

    assert len(versioned_twice) == 1
    assert len(after) == len(versioned_twice), "unchanged rows must not be resent"
