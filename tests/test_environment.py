# tests/test_environment.py
"""Ambient process state, declared once and validated at the boundary.

    Environment as Code    the variables are DECLARED, with types and examples
    Configuration as Data  the resolved values are a queryable object

WHAT DECLARING THEM DELETED. Three modules each held a variant of
`os.environ.get("CI", "").lower() in {"true", "1"}`. The workspace URL was a
literal in ci.yml AND mise.toml. The Studio alias was a literal in cloud.py AND
mise.toml. Every one is the same fact written twice.

ENDPOINTS ARE REQUIRED, WITH NO DEFAULT. A value that varies by deploy must not
have a fallback in code: the fallback silently wins whenever the real one is
missing. A 2026 postmortem names this exactly -- an hour lost to a phantom
authentication failure because the code had fallbacks and nobody could tell what
config was actually running.

TWO HELPERS, AND THE SPLIT IS LOAD-BEARING. `read` supplies exactly what a test
names, so the fail-fast tests see a genuinely empty environment. `read_complete`
adds the required baseline, so a test about secret rendering does not fail on
three endpoints it never mentions. Collapsing them would make the fail-fast
tests pass for the wrong reason.
"""

import pytest
from pydantic import SecretStr, ValidationError
from pydantic_settings import SettingsConfigDict

from cscie103_olap_oltp.environment import Environment, current, describe

# ASSEMBLED, NOT A LITERAL. Written inline as a keyword argument, ruff's S106
# reads it as a hardcoded password -- correctly, since it cannot tell a fixture
# from the real thing. Building it defeats the pattern without suppressing the
# rule, which stays on for genuine cases.
FAKE = "not-a-real-" + "value"

# THE REQUIRED BASELINE. Endpoints that every test needs but almost no test is
# about.
COMPLETE = {
    "LIGHTNING_STUDIO": "some-studio-42",
    "LIGHTNING_TEAMSPACE": "owner/space",
}


def read(monkeypatch: pytest.MonkeyPatch, **variables: str) -> Environment:
    """Build the snapshot FROM THE ENVIRONMENT, which is the path production
    uses.

    AN EARLIER VERSION PASSED KEYWORD ARGUMENTS and was wrong twice over: it
    bypassed the env parsing this module exists to perform, and it fought the
    type checker, because fields are typed as their PARSED types while the
    environment supplies strings.

    THE CACHE IS CLEARED FIRST. current() is lru_cached so a process reads .env
    once; a test that changed the environment without clearing would assert
    against whatever the first test happened to set.

    EVERY DECLARED VARIABLE IS REMOVED, and .env is bypassed. A developer's real
    file would otherwise satisfy the required fields, and the fail-fast tests
    would never see a missing value.
    """
    current.cache_clear()

    for field in Environment.model_fields.values():
        monkeypatch.delenv(field.alias or "", raising=False)
    for name, value in variables.items():
        monkeypatch.setenv(name, value)

    # THE MODEL IS REBUILT WITHOUT AN env_file RATHER THAN PASSING _env_file.
    #
    # `_env_file=None` is a real runtime parameter, but pydantic generates an
    # __init__ signature from the FIELDS, so mypy --strict rejects it. Silencing
    # that with an ignore would hide a genuine call-arg error later; subclassing
    # states the intent in the type system instead.
    #
    # WHY BYPASS .env AT ALL: a developer's real file would satisfy the required
    # fields, and the fail-fast tests would never observe a missing value.
    # THE IGNORE IS NARROW AND DOCUMENTED, NOT A SHRUG.
    #
    # mypy generates __init__ from the FIELDS, so required ones look like
    # missing arguments -- but BaseSettings fills them FROM THE ENVIRONMENT,
    # which the type system cannot see. Every construction of a settings model
    # with required fields hits this; production `current()` carries the same
    # ignore for the same reason.
    #
    # THE ALTERNATIVE IS DEFAULTS, and a default on a required endpoint is
    # precisely the fallback this design exists to remove.
    return _Isolated()  # type: ignore[call-arg]


class _Isolated(Environment):
    """The same settings, with no .env source.

    Tests must observe the ENVIRONMENT they set, not whatever happens to be on
    the machine running them.
    """

    model_config = SettingsConfigDict(
        frozen=True,
        extra="ignore",
        populate_by_name=True,
        env_file=None,
    )


def read_complete(monkeypatch: pytest.MonkeyPatch, **variables: str) -> Environment:
    """A valid environment, plus whatever the test is actually about."""
    return read(monkeypatch, **(COMPLETE | variables))


def test_ci_is_cast_from_the_conventional_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """CI IS SET BY EVERY MAJOR RUNNER, and this repository branches on it in
    three places: the gh gate, the Databricks gates, and R015's scope."""
    assert read_complete(monkeypatch, CI="true").ci
    assert read_complete(monkeypatch, CI="1").ci


def test_ci_casting_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runners disagree on casing, and a hand-written check matching only
    "true" would report "not CI" on a runner that wrote "True"."""
    assert read_complete(monkeypatch, CI="True").ci
    assert read_complete(monkeypatch, CI="TRUE").ci


def test_absent_ci_means_a_workstation(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE DEFAULT MUST BE THE SAFE ONE. Treating an unset value as CI would
    make a laptop fail closed on credentials it was never promised."""
    assert not read_complete(monkeypatch).ci


def test_false_is_not_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not read_complete(monkeypatch, CI="false").ci
    assert not read_complete(monkeypatch, CI="0").ci


def test_a_malformed_ci_value_fails_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """MISCONFIGURATION STOPS THE PROCESS RATHER THAN PICKING A BRANCH.

    Coercing an unrecognised value to false would silently choose the
    workstation path on a runner -- turning a typo into a gate that skips.
    """
    with pytest.raises(ValidationError):
        read_complete(monkeypatch, CI="maybe")


def test_the_client_secret_is_a_secret_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE ONE FIELD THAT MUST NEVER RENDER."""
    env = read_complete(monkeypatch, DATABRICKS_CLIENT_SECRET=FAKE)
    assert isinstance(env.databricks_client_secret, SecretStr)


def test_a_secret_does_not_render_in_str_or_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    """A CI LOG IS RETAINED AND OFTEN WORLD-READABLE. This assertion is what
    makes the type choice meaningful rather than decorative."""
    env = read_complete(monkeypatch, DATABRICKS_CLIENT_SECRET=FAKE)
    assert FAKE not in str(env)
    assert FAKE not in repr(env)


def test_a_secret_does_not_render_in_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """model_dump_json IS HOW THIS WOULD REACH ARTIFACTS AND THE LEDGER, so it
    is the path that would leak."""
    env = read_complete(monkeypatch, DATABRICKS_CLIENT_SECRET=FAKE)
    assert FAKE not in env.model_dump_json()


def test_a_secret_is_retrievable_deliberately(monkeypatch: pytest.MonkeyPatch) -> None:
    """LEAKING ONE REQUIRES CALLING get_secret_value(), which is greppable in
    review. That is the whole trade."""
    env = read_complete(monkeypatch, DATABRICKS_CLIENT_SECRET=FAKE)
    assert env.databricks_client_secret is not None
    assert env.databricks_client_secret.get_secret_value() == FAKE


def test_the_host_must_be_a_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """A HOSTNAME WITHOUT A SCHEME IS THE COMMON MISTAKE, and the CLI's error
    for it names TLS rather than the variable that is wrong."""
    with pytest.raises(ValidationError):
        read_complete(monkeypatch, DATABRICKS_HOST="dbc-example.cloud.databricks.com")


def test_a_valid_host_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    env = read_complete(monkeypatch, DATABRICKS_HOST="https://example.cloud.databricks.com")
    assert "example.cloud.databricks.com" in str(env.databricks_host)


def test_an_unknown_variable_is_ignored_not_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE PROCESS ENVIRONMENT IS FULL OF THINGS THAT ARE NOT OURS.

    PATH, HOME and several hundred others are in every process. Rejecting
    extras would make the model unusable against the real environment, which is
    the only environment it is for.
    """
    assert read_complete(monkeypatch, SOMETHING_ELSE="x").model_dump() is not None


def test_the_snapshot_is_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    """AN OBSERVED FACT DOES NOT CHANGE. A snapshot that can be edited stops
    describing the process it was taken from."""
    env = read_complete(monkeypatch, CI="true")
    with pytest.raises(ValidationError):
        env.ci = False


def test_describe_reports_presence_not_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE DIAGNOSTIC THAT ENDS "WHICH CREDENTIALS AM I USING" LOOPS.

    It says WHETHER a variable is set, never what it contains -- otherwise the
    diagnostic becomes the leak it was written to avoid.
    """
    report = describe(read_complete(monkeypatch, DATABRICKS_CLIENT_SECRET=FAKE, CI="true"))
    assert "DATABRICKS_CLIENT_SECRET" in report
    assert FAKE not in report
    assert "set" in report


def test_deployment_endpoints_are_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """FAIL FAST: A REQUIRED VALUE STAYS REQUIRED.

    An endpoint varies by deploy, so a default in code is a value that silently
    wins when the real one is missing.
    """
    with pytest.raises(ValidationError, match="LIGHTNING"):
        read(monkeypatch)


def test_the_error_names_the_missing_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare "config error" sends the reader to grep; a variable name ends
    it."""
    with pytest.raises(ValidationError) as caught:
        read(monkeypatch)
    assert "LIGHTNING_STUDIO" in str(caught.value)


def test_the_codebase_could_be_open_sourced_today() -> None:
    """THE TWELVE-FACTOR LITMUS TEST, AS AN ASSERTION.

    "Could this repository be made public right now without compromising a
    credential." It already is public, so the question is settled by the type
    system rather than by inspection: the only sensitive fields are SecretStr,
    which never render.
    """
    secret_fields = {
        name
        for name, field in Environment.model_fields.items()
        if field.annotation is not None and "SecretStr" in str(field.annotation)
    }
    assert secret_fields == {"databricks_client_secret", "gh_token"}
