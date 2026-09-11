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

from pathlib import Path

import pandas as pd
import pytest

from cscie103_olap_oltp.oltp.contracts import TABLES
from cscie103_olap_oltp.oltp.seed import (
    CHANGED_PRODUCT,
    EMPTY_CATEGORY,
    PRICE_AFTER,
    PRICE_BEFORE,
    PRICE_CHANGE_AT,
    STOCKED_CATEGORIES,
    changed,
    rows,
)


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


def test_every_product_points_at_a_category_that_exists() -> None:
    """REFERENTIAL INTEGRITY WITHIN THE FIXTURE.

    A mutant changed a category_id unnoticed, because the tests asserted that
    SOME category was unused rather than which. A product whose foreign key
    matches nothing yields "Unknown" in the dimension and looks like a correct
    denormalisation.
    """
    seeded = rows()
    categories = set(seeded["category"]["category_id"])
    used = set(seeded["product"]["category_id"])

    assert used <= categories, "a product references a category that does not exist"
    assert set(STOCKED_CATEGORIES) == used
    assert EMPTY_CATEGORY in categories and EMPTY_CATEGORY not in used


def test_the_change_load_selects_the_changed_product_not_the_others() -> None:
    """`==` FILTERS IN; A MUTANT MADE IT `!=`, WHICH FILTERS OUT.

    The feed would then carry every UNCHANGED product at the new price and omit
    the one that actually changed -- versioning the wrong rows and recording a
    price change on products nobody touched.
    """
    after = changed()["product"]

    assert list(after["product_id"]) == [CHANGED_PRODUCT]


def test_only_one_order_line_dangles_and_it_is_deliberate() -> None:
    """THE UNKNOWN-MEMBER CASE MUST BE THE ONLY MISS.

    If another line failed to resolve, the Unknown test would pass for the
    wrong reason -- counting a fixture mistake as the case it meant to prove.
    """
    seeded = rows()
    dangling = set(seeded["order_line"]["product_id"]) - set(seeded["product"]["product_id"])

    assert len(dangling) == 1, "exactly one deliberate dangling reference"
    assert CHANGED_PRODUCT in set(seeded["order_line"]["product_id"])


def test_the_change_carries_a_real_price_and_a_different_one() -> None:
    """THE NEW PRICE IS THE ENTIRE POINT, AND IT WAS UNASSERTED.

    A mutant set it to None: the feed would carry a null price, the dimension
    would version to it, and every historical total would be wrong while the
    pipeline reported COMPLETED. Asserting "a change happened" is not enough --
    the changed value has to be a usable number.
    """
    after = changed()["product"]
    price = after.iloc[0]["list_price"]

    assert price == PRICE_AFTER
    assert price != PRICE_BEFORE, "a change that changes nothing versions nothing"
    assert price > 0, "a null or zero price would silently zero every line total"


def test_the_order_lines_charge_the_price_in_force_at_the_time() -> None:
    """THE FIXTURE MUST AGREE WITH ITSELF, or the as-of join is tested against
    data that was never consistent."""
    lines = rows()["order_line"]
    charged = set(lines["unit_price"])

    assert PRICE_BEFORE in charged, "the February order pays the old price"
    assert PRICE_AFTER in charged, "the April order pays the new price"


def test_the_change_load_satisfies_the_contract_too() -> None:
    """EVERY TRANSFORMATION'S OUTPUT IS A BOUNDARY, AND THIS ONE WAS UNCHECKED.

    The contract test validated rows() and never changed(), so mutants turning
    `reset_index(drop=True)` into drop=False or drop=None survived. With
    drop=False pandas keeps the old positions as an extra `index` column, and
    strict mode would reject it -- at load time, against a real table, rather
    than here where it costs nothing.

    THE RULE IS "VALIDATE AFTER EACH TRANSFORMATION", not "validate the input".
    """
    for name, frame in changed().items():
        TABLES[name].validate(frame)
        assert "index" not in frame.columns, "reset_index leaked the old positions"
        assert list(frame.index) == list(range(len(frame)))


def test_a_missing_seed_file_fails_loudly(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """AN ABSENT SEED MUST NOT PRODUCE EMPTY FRAMES.

    An empty load runs to COMPLETED having done nothing -- the vacuous success
    this repository keeps removing. The file is the input; its absence is a
    failure, not a default.
    """
    from cscie103_olap_oltp.oltp import seed

    monkeypatch.setattr(seed, "SEED_PATH", tmp_path / "absent.yaml")

    with pytest.raises(SystemExit) as caught:
        seed.rows()

    assert "seed data missing" in str(caught.value)


def test_the_date_columns_arrive_as_timestamps() -> None:
    """YAML HAS NO TIMESTAMP THIS PROJECT RELIES ON, so they are written as
    strings and converted here. Left as strings the contract rejects them, and
    the as-of join would have nothing to compare."""
    orders = rows()["order"]

    assert str(orders["ordered_at"].dtype) == "datetime64[ns]"
    assert (orders["ordered_at"] < PRICE_CHANGE_AT).any()
    assert (orders["ordered_at"] > PRICE_CHANGE_AT).any()


def test_only_the_declared_columns_are_converted() -> None:
    """CONVERTING BY SUFFIX WOULD BE A GUESS. A column ending in _at that
    legitimately held text would be silently parsed; the list is explicit so
    that choice is visible."""
    from cscie103_olap_oltp.oltp.seed import TIMESTAMP_COLUMNS

    assert {"registered_at", "ordered_at"} == TIMESTAMP_COLUMNS
    assert str(rows()["customer"]["email_domain"].dtype) != "datetime64[ns]"
