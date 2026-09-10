# tests/test_artifact_schema.py
"""The shape of an artifact is a published contract, not an implementation detail.

    Schema as Code    the contract is a committed file, reviewed like any other
    Evidence as Data  what execution emits conforms to it, provably

WHY A REPOSITORY THAT ALREADY HAS TYPED MODELS STILL NEEDS THIS. Pydantic
validates on the way IN, inside one process. A consumer reading
.artifacts/verdicts/*.json six months from now has no models -- it has JSON. If
a field is renamed, that reader breaks and nothing here notices, because every
producer test still passes.

PYDANTIC CANNOT DO THE CHECKING. It generates JSON Schema and does nothing to
validate either a schema or an instance against one, so `jsonschema` is the
tool that makes the contract load-bearing rather than decorative.

THE COMMITTED BYTES ARE CANONICAL, AND REGENERATION IS DELIBERATE.
The obvious gate -- regenerate and compare -- has a documented failure: a
pydantic point release can subtly change generated output, so the gate goes red
for an upgrade that broke nothing, and drift returns through the back door once
people learn to regenerate on any failure.

The published schema is therefore a committed artifact that changes only when
someone runs `mise run schema:generate`. The comparison gate is still kept,
because this repository pins pydantic exactly in uv.lock -- so a difference here
means a MODEL changed, which is the thing worth being told about.
"""

import json

import pytest
from jsonschema import Draft202012Validator

from cscie103_olap_oltp.contracts.schema import (
    SCHEMA_PATH,
    committed_schema,
    generate_schema,
    schema_matches_models,
)
from cscie103_olap_oltp.contracts.verdict import Decision, Verdict, Violation


def _passing() -> Decision:
    verdict = Verdict(
        contract="repository/v1",
        outcome="pass",
        reason_code="POLICY_SATISFIED",
        policy_revision="0" * 64,
        violations=(),
        evidence=(),
    )
    return Decision(verdict=verdict, action="publish", reason_code="POLICY_SATISFIED")


def _failing() -> Decision:
    verdict = Verdict(
        contract="repository/v1",
        outcome="fail",
        reason_code="POLICY_VIOLATIONS",
        policy_revision="0" * 64,
        violations=(Violation(id="R001", reason_code="X", message="m"),),
        evidence=(),
    )
    return Decision(verdict=verdict, action="halt", reason_code="POLICY_VIOLATIONS")


def test_the_committed_schema_is_valid_json_schema() -> None:
    """A SCHEMA THAT IS NOT ITSELF VALID VALIDATES NOTHING.

    check_schema tests the schema against its metaschema, which is the
    difference between a contract and a JSON file with plausible keys.
    """
    Draft202012Validator.check_schema(committed_schema())


def test_the_schema_declares_its_dialect() -> None:
    """WITHOUT $schema A VALIDATOR GUESSES THE DIALECT, and dialects disagree
    about what an unknown keyword means -- silently, permissively."""
    assert committed_schema()["$schema"].startswith("https://json-schema.org/draft/2020-12")


def test_a_passing_decision_validates_against_the_committed_schema() -> None:
    """THE LOAD-BEARING ASSERTION.

    Validating against the COMMITTED bytes rather than freshly generated ones
    is what proves a consumer holding this file can read what we emit.
    """
    Draft202012Validator(committed_schema()).validate(json.loads(_passing().model_dump_json()))


def test_a_failing_decision_validates_too() -> None:
    """BOTH OUTCOMES. A verdict carrying violations has a different shape, and
    it is the one a consumer most needs to read correctly."""
    Draft202012Validator(committed_schema()).validate(json.loads(_failing().model_dump_json()))


def test_a_missing_required_field_is_rejected() -> None:
    """THE SCHEMA MUST REFUSE SOMETHING, or it is decoration. A field that
    stopped being emitted is the consumer's real failure mode."""
    payload = json.loads(_passing().model_dump_json())
    del payload["verdict"]

    with pytest.raises(Exception, match="verdict"):
        Draft202012Validator(committed_schema()).validate(payload)


def test_an_unknown_outcome_is_rejected() -> None:
    """THREE-VALUED MEANS THREE VALUES. A fourth would flow silently through
    every consumer that switches on this field."""
    payload = json.loads(_passing().model_dump_json())
    payload["verdict"]["outcome"] = "probably"

    with pytest.raises(Exception, match=r"probably|enum|not one of"):
        Draft202012Validator(committed_schema()).validate(payload)


def test_the_committed_schema_still_matches_the_models() -> None:
    """DRIFT, REPORTED RATHER THAN SILENTLY TOLERATED.

    Safe as an exact comparison ONLY because uv.lock pins pydantic: without
    that pin this would fail on a point release that broke nothing, and people
    would learn to regenerate reflexively -- which is how drift returns.
    """
    assert schema_matches_models()


def test_the_schema_is_published_beside_the_other_contracts() -> None:
    """A CONTRACT NOBODY CAN FIND IS NOT PUBLISHED. It sits in contracts/ with
    the ruleset and the environment mapping."""
    assert SCHEMA_PATH.is_file()
    assert SCHEMA_PATH.parent.name == "contracts"


def test_generation_is_deterministic() -> None:
    """TWO RUNS, ONE ANSWER. A generator that reorders keys would make every
    regeneration a diff and the drift gate meaningless."""
    assert generate_schema() == generate_schema()
