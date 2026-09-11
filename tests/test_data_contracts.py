# tests/test_data_contracts.py
"""The published contracts are valid ODCS, and derived from the models.

    Data Contract as Code   an open standard, not a shape invented here
    Contracts as Data       a catalog reads it without importing our code

WHY THESE ASSERTIONS AND NOT MORE. Validity is the reference implementation's
job, asserted by calling it. Re-checking individual ODCS fields here would
reimplement the specification in test form, which is the duplication this
repository refuses. What is worth asserting is what pyodcs cannot know: that the
document was DERIVED from the pandera models rather than written by hand.
"""

import pyodcs

from cscie103_olap_oltp.contracts.publish import (
    API_VERSION,
    MODELS,
    _serialise,
    generate,
    matches_models,
)


def _rendered(name: str) -> str:
    return _serialise(generate(name))


def test_every_published_contract_is_valid_odcs() -> None:
    """VALIDATED BY THE STANDARD'S OWN IMPLEMENTATION, so "valid" means what
    the specification says rather than what this project assumed."""
    for name in MODELS:
        report = pyodcs.parse_and_validate(_rendered(name))
        assert pyodcs.is_valid(report), report


def test_every_published_contract_is_current() -> None:
    """THE DRIFT GATE. Generating is only an improvement if the committed copy
    still agrees; otherwise it is a hand-maintained file with a generated
    header."""
    for name in MODELS:
        assert matches_models(name), f"{name} contract is stale"


def test_the_contract_carries_every_table() -> None:
    """A CONTRACT MISSING A TABLE IS WORSE THAN NONE: a consumer reads it and
    concludes the missing entity does not exist."""
    for name, (_, tables) in MODELS.items():
        assert {entry["name"] for entry in generate(name)["schema"]} == set(tables)


def test_the_contract_distinguishes_a_key_from_a_column() -> None:
    """UNIQUENESS IS WHAT A CONSUMER BINDS TO. Without it the document
    describes shape and says nothing about identity."""
    schema = {entry["name"]: entry for entry in generate("oltp")["schema"]}
    customer = {p["name"]: p for p in schema["customer"]["properties"]}
    assert customer["customer_id"]["unique"] is True


def test_nullability_is_translated_not_dropped() -> None:
    """`required` IS THE INVERSE OF `nullable` -- the one vocabulary difference
    between the two standards, and the easiest thing to lose in translation."""
    schema = {entry["name"]: entry for entry in generate("olap")["schema"]}
    product = {p["name"]: p for p in schema["dim_product"]["properties"]}

    # valid_to is open-ended for a current version, so it alone is optional.
    assert product["valid_to"]["required"] is False
    assert product["product_name"]["required"] is True


def test_the_api_version_is_pinned() -> None:
    """A FLOATING apiVersion WOULD CHANGE MEANING FOR EVERY CONSUMER without
    one reviewable edit."""
    assert API_VERSION == "v3.1.0"
    for name in MODELS:
        assert generate(name)["apiVersion"] == API_VERSION
