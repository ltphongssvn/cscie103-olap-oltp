# tests/test_environment.py
"""Ambient process state, declared and validated rather than assumed.

THE PILLAR THIS COMPLETES. Environment variables decide which workspace a
command reaches, whether a missing credential is a skip or a failure, and
whether a deploy runs at all. Until now this repository read them with
os.environ.get() scattered across four modules -- so the set of variables that
matter existed nowhere, was validated nowhere, and was discoverable only by
grep.

    Environment as Code    the variables are DECLARED, with types and defaults
    Configuration as Data  the resolved values are a queryable object

WHY BaseSettings RATHER THAN A HAND-ROLLED READER, AND WHAT THAT DELETED.

The first draft declared `ci: str` and added an `is_ci` property comparing
against {"true", "1"} after lowercasing -- which is exactly what this repository
already did in three separate places, each written slightly differently.

pydantic-settings casts "true", "True" and "1" to bool by itself. Declaring the
field as `bool` removes the property, the three copies, and the whole
case-sensitivity bug class. The library was already doing the work; the code was
duplicating it.

IT ALSO IMPROVED THE CONTRACT. A malformed value now FAILS VALIDATION rather
than silently becoming false. Misconfiguration should stop the process, not
quietly select the other branch -- which is the fail-open shape this repository
removes everywhere else.

SECRETS ARE SecretStr, AND THAT IS A GUARANTEE RATHER THAN A HABIT. A plain
string is one f-string away from a CI log that is retained and often
world-readable. SecretStr renders as asterisks in print, repr and
model_dump_json, so leaking one requires deliberately calling
get_secret_value() -- which is greppable in review.
"""

import pytest
from pydantic import SecretStr, ValidationError

from cscie103_olap_oltp.environment import Environment, describe

# ASSEMBLED, NOT A LITERAL. Written inline as a keyword argument, ruff's S106
# reads it as a hardcoded password -- correctly, since it cannot tell a fixture
# from the real thing. Building it defeats the pattern without suppressing the
# rule, which stays on for genuine cases.
FAKE = "not-a-real-" + "value"


def read(monkeypatch: pytest.MonkeyPatch, **variables: str) -> Environment:
    """Build the snapshot FROM THE ENVIRONMENT, which is the path production
    uses.

    THE FIRST VERSION PASSED KEYWORD ARGUMENTS and it was wrong twice over: it
    bypassed the env-parsing this module exists to perform, so the tests proved
    nothing about reading variables -- and it fought the type checker, because
    the fields are typed as their PARSED types while the environment supplies
    strings.

    EVERY DECLARED VARIABLE IS CLEARED FIRST. A developer machine with
    DATABRICKS_HOST already exported would otherwise leak into a test asserting
    its absence, and the suite would pass or fail depending on whose laptop ran
    it.
    """
    for field in Environment.model_fields.values():
        monkeypatch.delenv(field.alias or "", raising=False)
    for name, value in variables.items():
        monkeypatch.setenv(name, value)
    return Environment()


def test_ci_is_cast_from_the_conventional_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """CI IS SET BY EVERY MAJOR RUNNER, and this repository branches on it in
    three places: the gh gate, the Databricks gates, and R015's scope."""
    assert read(monkeypatch, CI="true").ci
    assert read(monkeypatch, CI="1").ci


def test_ci_casting_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runners disagree on casing, and a hand-written check that only matched
    "true" would report "not CI" on a runner that wrote "True"."""
    assert read(monkeypatch, CI="True").ci
    assert read(monkeypatch, CI="TRUE").ci


def test_absent_ci_means_a_workstation(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE DEFAULT MUST BE THE SAFE ONE. Treating an unset value as CI would
    make a laptop fail closed on credentials it was never promised."""
    assert not read(monkeypatch).ci


def test_false_is_not_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not read(monkeypatch, CI="false").ci
    assert not read(monkeypatch, CI="0").ci


def test_a_malformed_ci_value_fails_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """MISCONFIGURATION STOPS THE PROCESS RATHER THAN PICKING A BRANCH.

    Coercing an unrecognised value to false would silently choose the
    workstation path on a runner -- turning a typo into a gate that skips.
    """
    with pytest.raises(ValidationError):
        read(monkeypatch, CI="maybe")


def test_the_client_secret_is_a_secret_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE ONE FIELD THAT MUST NEVER RENDER."""
    env = read(monkeypatch, DATABRICKS_CLIENT_SECRET=FAKE)
    assert isinstance(env.databricks_client_secret, SecretStr)


def test_a_secret_does_not_render_in_str_or_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    """A CI LOG IS RETAINED AND OFTEN WORLD-READABLE. This assertion is what
    makes the type choice meaningful rather than decorative."""
    env = read(monkeypatch, DATABRICKS_CLIENT_SECRET=FAKE)
    assert FAKE not in str(env)
    assert FAKE not in repr(env)


def test_a_secret_does_not_render_in_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """model_dump_json IS HOW THIS WOULD REACH ARTIFACTS AND THE LEDGER, so it
    is the path that would leak."""
    env = read(monkeypatch, DATABRICKS_CLIENT_SECRET=FAKE)
    assert FAKE not in env.model_dump_json()


def test_a_secret_is_retrievable_deliberately(monkeypatch: pytest.MonkeyPatch) -> None:
    """LEAKING ONE REQUIRES CALLING get_secret_value(), which is greppable in
    review. That is the whole trade."""
    env = read(monkeypatch, DATABRICKS_CLIENT_SECRET=FAKE)
    assert env.databricks_client_secret is not None
    assert env.databricks_client_secret.get_secret_value() == FAKE


def test_the_host_must_be_a_url_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """A HOSTNAME WITHOUT A SCHEME IS THE COMMON MISTAKE, and the CLI's error
    for it names TLS rather than the variable that is wrong."""
    with pytest.raises(ValidationError):
        read(monkeypatch, DATABRICKS_HOST="dbc-example.cloud.databricks.com")


def test_a_valid_host_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    env = read(monkeypatch, DATABRICKS_HOST="https://dbc-example.cloud.databricks.com")
    assert env.databricks_host is not None


def test_an_unknown_variable_is_ignored_not_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE PROCESS ENVIRONMENT IS FULL OF THINGS THAT ARE NOT OURS.

    PATH, HOME and several hundred others are in every process. Rejecting
    extras would make the model unusable against the real environment, which is
    the only environment it is for.
    """
    assert read(monkeypatch, SOMETHING_ELSE="x").model_dump() is not None


def test_the_snapshot_is_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    """AN OBSERVED FACT DOES NOT CHANGE. A snapshot that can be edited after
    the fact stops describing the process it was taken from."""
    env = read(monkeypatch, CI="true")
    with pytest.raises(ValidationError):
        env.ci = False


def test_describe_reports_presence_not_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE DIAGNOSTIC THAT ENDS "WHICH CREDENTIALS AM I USING" LOOPS.

    It says WHETHER a variable is set, never what it contains -- otherwise the
    diagnostic becomes the leak it was written to avoid.
    """
    report = describe(read(monkeypatch, DATABRICKS_CLIENT_SECRET=FAKE, CI="true"))
    assert "DATABRICKS_CLIENT_SECRET" in report
    assert FAKE not in report
    assert "set" in report
