# src/cscie103_olap_oltp/oltp/seed.py
"""Load the seed, and validate it on the way in.

    Tests as Code   the fixture is checked by the same contracts as real data

WHY THE DATA IS NOT IN THIS FILE, AND MUTATION TESTING IS WHY.

The rows were Python literals here. Mutation testing rewrote "Ada Lovelace" to
"XXAda LovelaceXX" and shifted a registration date by a day, and no test could
tell -- because there is nothing to assert about an arbitrary fixture value
except by restating it, which asserts nothing and freezes data that ought to be
free to change. Thirty-nine such mutants survived.

MOVING THE DATA OUT MAKES THE DISTINCTION STRUCTURAL rather than a config
exclusion. contracts/seed.yaml is data; this module is loading logic, and
loading logic has behaviour worth testing: the file exists, it parses, the
dates become timestamps, and every frame satisfies its contract.

THE FACTS THAT ARE NOT ARBITRARY ARE STILL NAMED BELOW, because tests and the
pipeline depend on them: which product changes, which category is empty, the
two prices. A name is what makes a reference assertable.

DESIGNED FOR THE CASES THAT FAIL SILENTLY:

    a price that changes            a second version, with the first closed
    orders on BOTH sides of it      the as-of join, which is the whole point
    an order line with no product   the Unknown member, exercised not declared
    a category with no products     the empty side of a left join
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import yaml

from cscie103_olap_oltp.oltp.contracts import TABLES
from cscie103_olap_oltp.policy.snapshot import REPO_ROOT

__all__ = [
    "CHANGED_PRODUCT",
    "EMPTY_CATEGORY",
    "PRICE_AFTER",
    "PRICE_BEFORE",
    "PRICE_CHANGE_AT",
    "SEED_PATH",
    "STOCKED_CATEGORIES",
    "UNCHANGED_PRODUCT",
    "changed",
    "rows",
]

SEED_PATH = REPO_ROOT / "contracts" / "seed.yaml"

# THE INSTANT THE CATALOGUE PRICE CHANGES. Named because several tests depend
# on being on the correct side of it, and a literal repeated is a bug waiting
# for someone to edit all but one.
PRICE_CHANGE_AT = pd.Timestamp("2026-03-01")

# THE TWO PRICES THE DEMONSTRATION TURNS ON, and the identifiers the tests use
# to say WHICH product versions and WHICH category stays empty. Everything else
# in the seed -- names, dates, quantities -- is arbitrary and lives in the YAML.
PRICE_BEFORE = 9.99
PRICE_AFTER = 11.99

CHANGED_PRODUCT = 100
UNCHANGED_PRODUCT = 200
STOCKED_CATEGORIES = (10, 20)
EMPTY_CATEGORY = 30

# COLUMNS THAT ARE DATES IN THE FILE AND TIMESTAMPS IN THE CONTRACT.
#
# YAML has no timestamp type this project wants to rely on, so they are written
# as strings and converted here. Named rather than sniffed: guessing by column
# suffix would convert anything ending in _at, including one that legitimately
# held text.
TIMESTAMP_COLUMNS = frozenset({"registered_at", "ordered_at"})


def _frames(document: dict[str, Any]) -> dict[str, pd.DataFrame]:
    """One validated DataFrame per table.

    VALIDATED HERE, NOT BY THE CALLER. The seed is the input to a load, and the
    documented practice is to check at every boundary data enters -- so a
    malformed edit to the YAML fails the same way bad production data would,
    with the contract naming the column.
    """
    frames: dict[str, pd.DataFrame] = {}

    for name, columns in document.items():
        frame = pd.DataFrame(columns)
        # A PLAIN SET INTERSECTION, NOT `Index & frozenset`. `&` on a pandas
        # Index is the LOGICAL operator, not set intersection, and pandas now
        # refuses it against a dtype-less sequence outright -- it used to
        # broadcast, which would have been worse.
        for column in TIMESTAMP_COLUMNS & set(frame.columns):
            frame[column] = pd.to_datetime(frame[column])

        frames[name] = TABLES[name].validate(frame)

    return frames


def _document() -> dict[str, Any]:
    """The parsed seed file. FAILS LOUDLY IF IT IS ABSENT.

    A missing seed would otherwise produce empty frames, and an empty load runs
    to COMPLETED having done nothing -- the vacuous success this repository
    keeps removing.
    """
    if not SEED_PATH.is_file():
        raise SystemExit(f"seed data missing: {SEED_PATH}")

    parsed: dict[str, Any] = yaml.safe_load(SEED_PATH.read_text())
    return parsed


def rows() -> dict[str, pd.DataFrame]:
    """The first load: the world before the price change."""
    return _frames(_document()["initial"])


def changed() -> dict[str, pd.DataFrame]:
    """The second load: only what actually changed.

    ONLY THE CHANGED ROW, AND THE WAREHOUSE SHOWED WHY. A first version resent
    every product with a later timestamp. AUTO CDC versions on the SEQUENCE
    rather than by comparing values, so an untouched product gained a second
    version identical to its first -- history recording an edit that never
    happened.
    """
    return _frames(_document()["change"])
