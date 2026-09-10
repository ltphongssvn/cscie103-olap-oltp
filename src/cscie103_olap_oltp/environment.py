# src/cscie103_olap_oltp/environment.py
"""Ambient process state, declared once and validated at the boundary.

THE PROBLEM THIS REPLACES. Environment variables decide which workspace a
command reaches, which Studio it syncs to, and whether a deploy runs at all --
and this repository read them with os.environ.get() in four modules while
hardcoding endpoints in three more. The set of variables that mattered existed
nowhere, was validated nowhere, and was discoverable only by grep.

    Environment as Code    the variables are DECLARED, with types and examples
    Configuration as Data  the resolved values are a queryable object

WHAT DECLARING THEM DELETED. Three modules each held a variant of
`os.environ.get("CI", "").lower() in {"true", "1"}`. The workspace URL was a
literal in ci.yml AND mise.toml. The Studio alias was a literal in cloud.py AND
mise.toml. Every one of those is the same fact written twice.

ENDPOINTS ARE REQUIRED, WITH NO DEFAULT, AND THAT IS DELIBERATE. A value that
varies by deploy must not have a fallback in code: the fallback silently wins
whenever the real one is missing. A 2026 postmortem names this exactly -- a team
lost an hour chasing a phantom authentication failure because their code had
fallbacks and nobody could tell what config was actually running. Missing
config stops the process, naming the variable.

THE TEMPLATE IS GENERATED, NOT MAINTAINED. The documented practice is to commit
a .env.example listing every variable, and the documented failure is that it
drifts the moment someone adds a key and forgets the template -- the number one
"works on my machine" bug, with a tool category built to police it. Policing a
second copy is not this repository's answer: `template()` derives the file from
the model, and a gate fails if the committed copy disagrees. One source, checked.

SECRETS ARE SecretStr, WHICH IS A GUARANTEE RATHER THAN A HABIT. A plain string
is one f-string away from a CI log that is retained and often world-readable.
SecretStr renders as asterisks in print, repr and model_dump_json, so leaking
one requires deliberately calling get_secret_value() -- greppable in review.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Environment", "current", "describe", "template"]


class Environment(BaseSettings):
    """The variables this platform depends on.

    READS .env FOR LOCAL DEVELOPMENT. Real environment variables still win, so
    a runner's injected values are never shadowed by a stray file. .env is
    gitignored; .env.example is the committed contract.

    EXTRAS ARE IGNORED, NOT REJECTED. PATH, HOME and several hundred others are
    present in every process; refusing them would make the model unusable
    against the real environment, which is the only environment it is for.

    FROZEN, BECAUSE AN OBSERVED FACT DOES NOT CHANGE. A snapshot that can be
    edited stops describing the process it was taken from.
    """

    model_config = SettingsConfigDict(
        frozen=True,
        extra="ignore",
        populate_by_name=True,
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # THE SIGNAL EVERY MAJOR RUNNER SETS, and the one this repository branches
    # on: whether missing credentials are a skip or a failure, and whether R015
    # applies to this checkout. pydantic casts "true", "True" and "1" itself.
    ci: bool = Field(
        default=False,
        alias="CI",
        description="Set by the runner. Decides whether missing credentials fail or skip.",
        examples=["true"],
    )

    # OPTIONAL, AND SETTING IT LOCALLY BREAKS AUTHENTICATION.
    #
    # Making this required and exporting it from .env broke every Databricks
    # gate at once, with the documented signature:
    #
    #     cannot configure default credentials ... Env: DATABRICKS_HOST
    #
    # The CLI resolves credentials in a documented order and REFUSES TO GUESS
    # when several attributes are present: a host with no matching credential
    # stops it falling back to the DEFAULT profile a laptop authenticates with.
    # databricks.yml already documents the same divergence for workspace.host;
    # this is that trap in its environment form.
    #
    # A LAPTOP RESOLVES THE HOST FROM ITS PROFILE. A runner has no profile, so
    # the workflow supplies host, client id, secret AND an explicit auth type.
    # Optional here is therefore accurate rather than lenient: the value really
    # is absent in one environment and present in the other.
    databricks_host: AnyHttpUrl | None = Field(
        default=None,
        alias="DATABRICKS_HOST",
        description="Workspace URL. CI only -- setting it locally overrides profile auth.",
        examples=["https://dbc-00000000-0000.cloud.databricks.com"],
    )

    # THE DISAMBIGUATOR, DECLARED SO IT IS VISIBLE RATHER THAN FOLKLORE.
    #
    # Documented purpose: when multiple authentication attributes are available
    # in the environment, use the type named here. CI sets oauth-m2m; a laptop
    # leaves it unset and the CLI uses its profile.
    databricks_auth_type: str | None = Field(
        default=None,
        alias="DATABRICKS_AUTH_TYPE",
        description="Forces one auth method when several are present. CI sets oauth-m2m.",
        examples=["oauth-m2m"],
    )

    lightning_studio: str = Field(
        alias="LIGHTNING_STUDIO",
        description="SSH host alias of the Lightning Studio. Per-person.",
        examples=["your-studio-name"],
    )

    lightning_teamspace: str = Field(
        alias="LIGHTNING_TEAMSPACE",
        description="owner/teamspace. The CLI cannot resolve it unaided.",
        examples=["owner/teamspace"],
    )

    # THE OAUTH CLIENT ID IS A USERNAME, not a credential. It belongs in the
    # repository and in workflow files; see docs/adr/0001-ci-authentication.md.
    databricks_client_id: str | None = Field(
        default=None,
        alias="DATABRICKS_CLIENT_ID",
        description="Service principal application id. NOT secret -- it is a username.",
        examples=["00000000-0000-0000-0000-000000000000"],
    )

    databricks_client_secret: SecretStr | None = Field(
        default=None,
        alias="DATABRICKS_CLIENT_SECRET",
        description="OAuth M2M secret. NEVER commit. CI supplies it from the secret store.",
        examples=[""],
    )

    gh_token: SecretStr | None = Field(
        default=None,
        alias="GH_TOKEN",
        description="Read by gh on a runner; a keyring supplies it on a laptop.",
        examples=[""],
    )


@lru_cache(maxsize=1)
def current() -> Environment:
    """The live environment, constructed once per process.

    A FUNCTION RATHER THAN A MODULE-LEVEL INSTANCE. Reading at import time
    freezes whatever the environment was when the first module happened to be
    imported -- which makes tests order-dependent, monkeypatching useless, and
    a missing required variable a crash during import rather than a clear error
    at the point of use.

    CACHED, BECAUSE EACH CALL OTHERWISE RE-READS .env FROM DISK. cloud.py alone
    calls this three times to build one script. The documented pattern is
    exactly this: construct once per process, and clear the cache in tests that
    need a different environment.

    TESTS MUST CALL current.cache_clear(). That is the cost of caching, and it
    is paid in one place -- the `read` helper in tests/test_environment.py --
    rather than by every caller.
    """
    return Environment()  # type: ignore[call-arg]


def template() -> str:
    """The .env.example contract, DERIVED from the model.

    GENERATED RATHER THAN MAINTAINED. A hand-written template drifts the moment
    someone adds a variable and forgets it, and the usual answer is a second
    tool that compares the two. This repository declares a fact once instead:
    the model is the source, the template is its rendering, and a gate fails if
    the committed copy disagrees.

    VALUES ARE EXAMPLES, NEVER REAL ONES. Secrets render empty, so copying this
    file to .env cannot smuggle a credential into a working tree.
    """
    lines = [
        "# .env.example",
        "# GENERATED FROM src/cscie103_olap_oltp/environment.py -- DO NOT EDIT.",
        "# Regenerate with `mise run env:template`.",
        "#",
        "# Copy to .env and fill in the required values. .env is gitignored;",
        "# this file is the committed contract and carries no real values.",
        "",
    ]

    for name, field in Environment.model_fields.items():
        variable = field.alias or name
        required = field.is_required()
        example = field.examples[0] if field.examples else ""

        lines.append(f"# {field.description}")
        lines.append(f"# required: {'yes' if required else 'no'}")
        lines.append(f"{variable}={example}")
        lines.append("")

    return "\n".join(lines)


def describe(env: Environment | None = None) -> str:
    """Report which variables are set, and never what they contain.

    THE DIAGNOSTIC THAT ENDS "WHICH CREDENTIALS AM I USING" LOOPS -- the same
    role `databricks auth describe` plays, for the variables we own. It also
    answers the standing complaint about twelve-factor config, that masked
    secrets leave you unable to see what is set.

    PRESENCE, NOT VALUES. A diagnostic that prints a secret is the leak it was
    written to prevent, and it is exactly the output someone pastes into an
    issue while asking for help.
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


TEMPLATE_PATH = Path(".env.example")


def check_template() -> int:
    """Refuse a committed contract that disagrees with the model.

    THE GATE THAT MAKES GENERATION SAFE. Deriving .env.example is only an
    improvement if the committed copy actually matches; otherwise it is a
    hand-maintained file with a misleading header. Regenerating and comparing
    turns drift into a red build rather than a surprise for the next person who
    copies it.
    """
    expected = template()

    if not TEMPLATE_PATH.is_file():
        print(f"{TEMPLATE_PATH} is missing; run `mise run env:template`", file=sys.stderr)
        return 1

    if TEMPLATE_PATH.read_text(encoding="utf-8") != expected:
        print(
            f"{TEMPLATE_PATH} no longer matches the settings model.\n"
            "A variable was added, renamed or re-described without regenerating.\n"
            "Fix: mise run env:template",
            file=sys.stderr,
        )
        return 1

    print(f"{TEMPLATE_PATH} matches the settings model")
    return 0


def main() -> int:
    """Print the diagnostic, or render the template.

    THE DIAGNOSTIC ALWAYS EXITS ZERO. It reports state; it does not judge it. A
    missing variable is a finding for the gate that needs it, and duplicating
    that judgement here would put the same rule in two places.
    """
    match sys.argv[1:]:
        case ["--template"]:
            TEMPLATE_PATH.write_text(template(), encoding="utf-8")
            print(f"wrote {TEMPLATE_PATH}")
            return 0
        case ["--check-template"]:
            return check_template()
        case []:
            print(describe())
            return 0
        case other:
            raise SystemExit(f"usage: --template | --check-template  (got {other})")


if __name__ == "__main__":
    raise SystemExit(main())
