# src/cscie103_olap_oltp/environment.py
"""Ambient process state, declared and validated rather than assumed.

THE PROBLEM THIS REPLACES. Environment variables decide which workspace a
command reaches, whether a missing credential is a skip or a failure, and
whether a deploy runs at all -- and this repository read them with
os.environ.get() in four modules. The set of variables that matter existed
nowhere, was validated nowhere, and was discoverable only by grep.

    Environment as Code    the variables are DECLARED, with types and defaults
    Configuration as Data  the resolved values are a queryable object

WHAT DECLARING THEM DELETED. Three modules each contained a variant of
`os.environ.get("CI", "").lower() in {"true", "1"}`, written slightly
differently each time. pydantic-settings casts "true", "True" and "1" to bool
itself, so declaring `ci: bool` removes all three copies and the entire
case-sensitivity bug class along with them.

MISCONFIGURATION FAILS VALIDATION rather than selecting a branch. An
unrecognised value for a boolean raises instead of coercing to false -- because
coercing would silently choose the workstation path on a runner, turning a typo
into a gate that skips.

SECRETS ARE SecretStr, WHICH IS A GUARANTEE RATHER THAN A HABIT. A plain string
is one f-string away from a CI log that is retained and often world-readable.
SecretStr renders as asterisks in print, repr and model_dump_json, so leaking
one requires deliberately calling get_secret_value() -- greppable in review.
"""

from __future__ import annotations

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Environment", "current", "describe"]


class Environment(BaseSettings):
    """The variables this platform actually depends on.

    EXTRAS ARE IGNORED, NOT REJECTED. PATH, HOME and several hundred others are
    present in every process; refusing them would make the model unusable
    against the real environment, which is the only environment it is for.

    FROZEN, BECAUSE AN OBSERVED FACT DOES NOT CHANGE. A snapshot that can be
    edited after the fact stops describing the process it was taken from.
    """

    # SettingsConfigDict, NOT ConfigDict. BaseSettings declares the narrower
    # type, and mypy --strict catches the substitution -- which would otherwise
    # silently drop the settings-specific options this relies on.
    model_config = SettingsConfigDict(
        frozen=True,
        extra="ignore",
        populate_by_name=True,
    )

    # THE SIGNAL EVERY MAJOR RUNNER SETS, and the one this repository branches
    # on in three places: whether missing credentials are a skip or a failure,
    # and whether R015 applies to this checkout.
    ci: bool = Field(default=False, alias="CI")

    # NOT SECRET, DELIBERATELY. A workspace URL names a location, and hiding it
    # would imply a sensitivity it does not have while making the diagnostic
    # useless.
    #
    # TYPED AS A URL so a bare hostname -- the common mistake -- fails here,
    # naming the variable, rather than in the CLI, naming TLS.
    databricks_host: AnyHttpUrl | None = Field(default=None, alias="DATABRICKS_HOST")

    # THE OAUTH CLIENT ID IS A USERNAME, not a credential. It belongs in the
    # repository and in workflow files; see docs/adr/0001-ci-authentication.md.
    databricks_client_id: str | None = Field(default=None, alias="DATABRICKS_CLIENT_ID")

    # THE ONLY SECRET. Bounded at 90 days by policy, and never rendered by this
    # type.
    databricks_client_secret: SecretStr | None = Field(
        default=None, alias="DATABRICKS_CLIENT_SECRET"
    )

    # gh READS THIS ON A RUNNER and a keyring on a laptop. Its absence is why
    # the ruleset gate once failed with exit 4, which reads like a missing
    # ruleset and is not.
    gh_token: SecretStr | None = Field(default=None, alias="GH_TOKEN")


def current() -> Environment:
    """The live environment, read once.

    A FUNCTION RATHER THAN A MODULE-LEVEL SINGLETON. Reading at import time
    freezes whatever the environment was when the first module happened to be
    imported, which makes tests order-dependent and monkeypatching useless.
    """
    return Environment()


def describe(env: Environment | None = None) -> str:
    """Report which variables are set, and never what they contain.

    THE DIAGNOSTIC THAT ENDS "WHICH CREDENTIALS AM I USING" LOOPS -- the same
    role `databricks auth describe` plays, for the variables we own.

    PRESENCE, NOT VALUES, AND THAT IS THE WHOLE DESIGN. A diagnostic that prints
    a secret is the leak it was written to prevent, and it would be pasted into
    an issue by someone trying to be helpful.
    """
    env = env or current()
    lines = ["environment:"]

    for name, field in Environment.model_fields.items():
        variable = field.alias or name
        value = getattr(env, name)

        if isinstance(value, SecretStr):
            state = "set (hidden)"
        elif value is None:
            state = "not set"
        elif isinstance(value, bool):
            state = str(value).lower()
        else:
            state = str(value)

        lines.append(f"  {variable:<26} {state}")

    return "\n".join(lines)


def main() -> int:
    """Print the diagnostic.

    ALWAYS EXITS ZERO. This reports state; it does not judge it. A missing
    variable is a finding for the gate that needs it, and duplicating that
    judgement here would put the same rule in two places.
    """
    print(describe())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
