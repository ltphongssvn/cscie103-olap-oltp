# src/cscie103_olap_oltp/oltp/seed.py
"""Seed rows for the OLTP side, designed to exercise the hard cases.

    Tests as Code   the fixture is validated by the same contracts as real data

WHY THIS EXISTS. The pipeline ran to COMPLETED against empty tables, which
proves the DAG resolves and nothing about Slowly Changing Dimension Type 2: no
row had ever changed. A model that has never processed a change is a claim.

DESIGNED FOR THE CASES THAT FAIL SILENTLY, not for plausibility:

    a price that changes            a second version, with the first closed
    orders on BOTH sides of it      the as-of join, which is the whole point
    an order line with no product   the Unknown member, exercised not declared
    a category with no products     the empty side of a left join

THE SECOND IS WHAT SEPARATES A CORRECT AS-OF JOIN FROM A WRONG ONE. Joining on
`is_current` instead of the validity interval attaches today's price to every
historical sale -- and with orders on one side only, that mistake passes.

TWO LOADS, NOT ONE. `rows()` is the first state and `changed()` is the second.
SCD2 needs a BEFORE and an AFTER; a single load can only ever insert.

VALIDATED BY THE PRODUCTION CONTRACTS. Seed data that violates them would be
caught in a pipeline run rather than here, so the contracts check it first.
"""

from __future__ import annotations

import pandas as pd

__all__ = ["PRICE_CHANGE_AT", "changed", "rows"]

# THE INSTANT THE CATALOGUE PRICE CHANGES. Named because three fixtures and two
# tests depend on being on the correct side of it, and a literal repeated five
# times is a bug waiting for someone to edit four of them.
PRICE_CHANGE_AT = pd.Timestamp("2026-03-01")

_BEFORE = pd.Timestamp("2026-02-01")
_AFTER = pd.Timestamp("2026-04-01")

# THE UNRESOLVABLE REFERENCE. An order line pointing at a product that does not
# exist, which is how the Unknown member gets exercised rather than merely
# reserved.
_MISSING_PRODUCT = 999


def rows() -> dict[str, pd.DataFrame]:
    """The first load: the world before the price change."""
    return {
        "customer": pd.DataFrame(
            {
                "customer_id": [1, 2],
                "full_name": ["Ada Lovelace", "Alan Turing"],
                # A DOMAIN, NEVER AN ADDRESS -- committed fixtures are forever.
                "email_domain": ["example.test", "example.test"],
                "registered_at": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            }
        ),
        "category": pd.DataFrame(
            {
                # 30 IS DELIBERATELY UNUSED: the empty side of a left join.
                "category_id": [10, 20, 30],
                "category_name": ["Tools", "Books", "Unstocked"],
            }
        ),
        "product": pd.DataFrame(
            {
                "product_id": [100, 200],
                "product_name": ["Hammer", "SICP"],
                "category_id": [10, 20],
                "list_price": [9.99, 55.00],
            }
        ),
        "order": pd.DataFrame(
            {
                "order_id": [1000, 1001],
                "customer_id": [1, 2],
                # ONE ON EACH SIDE OF THE CHANGE.
                "ordered_at": [_BEFORE, _AFTER],
                "status": ["delivered", "shipped"],
            }
        ),
        "order_line": pd.DataFrame(
            {
                "order_id": [1000, 1000, 1001, 1001],
                "line_number": [1, 2, 1, 2],
                "product_id": [100, 200, 100, _MISSING_PRODUCT],
                "quantity": [2, 1, 3, 1],
                # THE PRICE ACTUALLY CHARGED, which is what the fact must
                # reproduce and what an is_current join would overwrite.
                "unit_price": [9.99, 55.00, 11.99, 1.00],
            }
        ),
    }


def changed() -> dict[str, pd.DataFrame]:
    """The second load: the same world after the price change.

    ONLY WHAT CHANGED. AUTO CDC sequences by updated_at and applies the
    difference; resending unchanged rows would still be correct but would say
    nothing about whether change detection works.
    """
    # ONLY THE CHANGED ROW, AND THE WAREHOUSE SHOWED WHY.
    #
    # A first version resent every product with a later timestamp. AUTO CDC
    # versions on the SEQUENCE rather than by comparing values, so product 200
    # -- untouched -- gained a second version identical to its first. History
    # then records an edit that never happened, and "when did this price change"
    # answers with the load time instead.
    #
    # A CHANGE FEED CARRIES CHANGES. That is what the name means, and sending
    # the full catalogue is a SNAPSHOT, which is a different API
    # (AUTO CDC FROM SNAPSHOT) that diffs for you.
    first = rows()["product"]
    after = first[first["product_id"] == 100].copy()
    after["list_price"] = 11.99

    return {"product": after.reset_index(drop=True)}
