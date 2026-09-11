# tests/test_star_integration.py
"""The star, asserted against the real warehouse.

    Tests as Code        the demonstration is executable, not a transcript
    Assertions as Code   the domain's invariants are checked where data lives

WHY THESE ARE NOT UNIT TESTS. Every invariant here is a property of what the
PIPELINE produced. The unit tests assert what the seed and contracts say; only a
query against the warehouse can say whether AUTO CDC versioned correctly and
whether the as-of join attached the right version.

MARKED integration, SO THE DEFAULT RUN STAYS HERMETIC. The hook-friendly suite
must not need credentials; these run where credentials exist.

THE INVARIANTS, IN THE DOMAIN'S WORDS RATHER THAN THE FRAMEWORK'S:

    a changed entity has more than one version, an unchanged one exactly one
    versions of one entity have DIFFERENT surrogate keys
    validity intervals abut exactly -- no gap, no overlap
    exactly one version is open per entity
    a fact joins to the version that was true when the event happened
    an unresolvable reference lands on the Unknown member, not on a null

THE FIFTH IS THE ONE THAT FAILS SILENTLY. Joining on `is_current` instead of the
interval passes every other assertion here and restates all history.
"""

from itertools import pairwise

import pytest

from cscie103_olap_oltp.olap.warehouse import UNKNOWN_KEY, query

# BOTH MARKERS, AND THE SECOND IS A TIER LIMITATION RATHER THAN A PREFERENCE.
#
# CI RUNS AS A SERVICE PRINCIPAL WITHOUT databricks-sql-access, so the Statement
# Execution API refuses it outright:
#
#     This API is disabled for users without the databricks-sql-access
#     entitlement.
#
# Free Edition cannot grant entitlements to a service principal, so this is not
# a permission to fix -- it is the tier. `spark` is the registered marker for
# "needs a session CI does not have", and the integration gate excludes it.
#
# THE ASSERTIONS ARE NOT WEAKENED TO SUIT THE RUNNER. Deleting them, or reducing
# them to something CI can check, would trade a real proof for a green tick. They
# run wherever a user identity exists, which today is a developer machine.
pytestmark = [pytest.mark.integration, pytest.mark.spark]


def test_a_changed_entity_has_two_versions_and_an_unchanged_one_has_one() -> None:
    counts = {
        row["product_id"]: row["versions"]
        for row in query(
            "SELECT product_id, count(*) AS versions FROM {olap}.dim_product GROUP BY product_id"
        )
    }

    assert counts["100"] == "2", "the changed product must be versioned"
    assert counts["200"] == "1", "an unchanged product must not gain a version"


def test_versions_of_one_entity_have_distinct_surrogate_keys() -> None:
    """THE DEFINING PROPERTY OF A SURROGATE KEY. A key shared by two versions
    identifies the ENTITY, which the business key already does."""
    keys = [
        row["product_key"]
        for row in query("SELECT product_key FROM {olap}.dim_product WHERE product_id = 100")
    ]

    assert len(keys) == len(set(keys)), "two versions share a key"


def test_exactly_one_version_is_open_per_entity() -> None:
    """TWO OPEN VERSIONS DOUBLE EVERY CURRENT-STATE JOIN, and nothing errors."""
    open_rows = query(
        "SELECT product_id, count(*) AS open FROM {olap}.dim_product "
        "WHERE __END_AT IS NULL GROUP BY product_id"
    )

    assert all(row["open"] == "1" for row in open_rows), open_rows


def test_validity_intervals_abut_without_gap_or_overlap() -> None:
    """A GAP LOSES A FACT; AN OVERLAP COUNTS IT TWICE."""
    rows = query(
        "SELECT __START_AT, __END_AT FROM {olap}.dim_product "
        "WHERE product_id = 100 ORDER BY __START_AT"
    )

    for earlier, later in pairwise(rows):
        assert earlier["__END_AT"] == later["__START_AT"], (earlier, later)


def test_a_fact_joins_to_the_version_true_when_the_order_was_placed() -> None:
    """THE POINT OF THE WHOLE MODEL.

    The February order must carry the February price and the April order the
    April one. Joining on is_current gives both the current price, and every
    historical total silently restates.
    """
    priced = {
        row["order_id"]: row["unit_price"]
        for row in query(
            "SELECT order_id, unit_price FROM {olap}.fact_order_line "
            "WHERE line_number = 1 AND product_key <> -1"
        )
    }

    assert priced["1000"] == "9.99", "a February order must not carry the April price"
    assert priced["1001"] == "11.99"


def test_an_unresolvable_reference_lands_on_the_unknown_member() -> None:
    """NEVER A NULL FOREIGN KEY. A null drops the row from every inner join and
    from every total, without reporting anything."""
    unresolved = query(
        "SELECT count(*) AS rows FROM {olap}.fact_order_line WHERE product_key = :unknown",
        unknown=UNKNOWN_KEY,
    )

    assert unresolved[0]["rows"] == "1"


def test_no_fact_row_has_a_null_dimension_key() -> None:
    nulls = query(
        "SELECT count(*) AS rows FROM {olap}.fact_order_line "
        "WHERE product_key IS NULL OR customer_key IS NULL"
    )

    assert nulls[0]["rows"] == "0"
