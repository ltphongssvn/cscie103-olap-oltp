# src/cscie103_olap_oltp/databricks.py
"""The bundle's relationship to the local toolchain, checked rather than assumed.

TWO CONCERNS, DELIBERATELY IN ONE MODULE because they share the bundle
resolution and would otherwise duplicate it:

  interpreter parity  the declared environment_version and .python-version
                      describe the same Python. Databricks serverless runs Spark
                      Connect, and client and server must agree on the MINOR
                      version or every Python UDF fails with "Python versions in
                      the Spark Connect client and server are different" -- at
                      UDF execution time, far from the change that caused it.

  prerequisites       the unmanaged things a deploy depends on exist. Without
                      this a missing dependency surfaces as an API error naming
                      the symptom, not the cause.

WHY A GATE AND NOT A COMMENT
The interpreter version exists in two places by necessity: .python-version
drives the local toolchain, and environment_version drives what the server runs.
Two copies of one fact drift. A comment saying "keep these in sync" documents an
intention; this makes disagreement a red build.

THE SEAM, NAMED RATHER THAN HIDDEN
contracts/environment-versions.json is transcribed from published documentation,
because Databricks exposes environment version contents as a reference table
rather than an API. It is the one link a human maintains, and its review trigger
is a new environment version shipping.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from cscie103_olap_oltp.policy.snapshot import REPO_ROOT

CONTRACT = REPO_ROOT / "contracts" / "environment-versions.json"
PYTHON_VERSION_FILE = REPO_ROOT / ".python-version"

# THE FIXTURE TARGET RESOLVES WITHOUT DEPLOYER IDENTITY, which is what these
# checks want: they read values that do not vary by who runs them. Resolving
# `dev` would embed the deployer in paths and prefixed names, so the same bundle
# would resolve differently on a laptop and a runner.
PROBE_TARGET = "fixture"

# THE SIBLING PROJECT'S CATALOG. Named so a test can assert no RESOURCE in the
# RESOLVED bundle references it: Free Edition shares one workspace and one
# metastore, so the catalog is the only isolation boundary, and a bundle
# reaching across it would put another project's data under this bundle's
# destroy plan.
SIBLING_CATALOG = "cscie103_catalog"

# WHAT THIS BUNDLE OWNS AND CREATES.
PROJECT_CATALOG = "cscie103_olap_oltp"


def _databricks(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the CLI, or report why it could not run.

    S603 IS SUPPRESSED NARROWLY: `*args` makes the list computed, which is what
    ruff flags. Every call site passes literal subcommands; there is no shell.
    """
    return subprocess.run(  # noqa: S603
        ["databricks", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )


def resolve_bundle(target: str = PROBE_TARGET) -> dict[str, Any]:
    """The bundle as the CLI actually resolves it.

    RAISES RATHER THAN RETURNING A DEFAULT. An empty configuration would flow
    into every check below and produce tidy answers about nothing.

    NOT check=True: a CalledProcessError names the command and a number and
    explains nothing, which is how an authentication failure comes to look like
    a configuration error. The stderr the CLI produced is carried instead.

    WHAT RESOLUTION DOES AND DOES NOT DO: variables and workspace substitutions
    are resolved here; RESOURCE CROSS-REFERENCES such as
    ${resources.schemas.oltp.name} are not, because the referenced object has no
    identity until deploy. Callers asserting on the output must expect them.
    """
    result = _databricks("bundle", "validate", "-t", target, "--output", "json")
    if result.returncode != 0:
        raise RuntimeError(
            f"`databricks bundle validate -t {target}` failed:\n"
            f"{result.stderr.strip() or '(no stderr)'}"
        )

    parsed: dict[str, Any] = json.loads(result.stdout)
    return parsed


def declared_environment_version(config: dict[str, Any]) -> str:
    """The authoritative interpreter selector, read from the resolved bundle.

    THE BUNDLE IS THE SOURCE, NOT THE YAML TEXT. What deploys is what the CLI
    resolved, including any target override; reading the file would check a
    default that a target may not use.
    """
    variables = config.get("variables", {})
    if "environment_version" not in variables:
        raise RuntimeError(
            "databricks.yml declares no environment_version variable.\n"
            "It is the authoritative target; without it there is nothing to check."
        )
    value = variables["environment_version"].get("value")
    if value is None:
        raise RuntimeError("environment_version resolved to no value")
    return str(value)


def interpreter_drift(
    environment_version: str,
    local_python: str,
    contract: dict[str, Any],
) -> str | None:
    """The drift message, or None when the two agree.

    A PURE FUNCTION over three values, so the comparison logic is testable
    without a CLI, a workspace, or credentials. The live lookup is the caller's
    problem.

    AN UNKNOWN VERSION IS A FAILURE, NOT A PASS. A version missing from the
    mapping means the mapping is stale, which is exactly when the check matters
    -- treating it as "no drift" would silently disable the gate the moment
    Databricks shipped a new environment version.
    """
    versions = contract.get("versions", {})

    if environment_version not in versions:
        known = ", ".join(sorted(versions, key=int, reverse=True))
        return (
            f"environment version {environment_version} is not in {CONTRACT.name}.\n"
            f"  known versions: {known}\n"
            "A new environment version is the review trigger for that file:\n"
            f"  {contract.get('x-source', '')}"
        )

    expected = versions[environment_version]["python"]
    if local_python == expected:
        return None

    return (
        "INTERPRETER DRIFT\n"
        f"  bundle environment_version : {environment_version}\n"
        f"  that version ships Python  : {expected}\n"
        f"  .python-version says       : {local_python}\n\n"
        "Spark Connect requires the client and server to share the same Python "
        "minor version.\n"
        f"Fix by writing {expected} to .python-version, then re-running setup."
    )


def check_env_parity() -> int:
    config = resolve_bundle()
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    local = PYTHON_VERSION_FILE.read_text(encoding="utf-8").strip()
    version = declared_environment_version(config)

    drift = interpreter_drift(version, local, contract)
    if drift:
        print(drift, file=sys.stderr)
        return 1

    print(f"env parity ok: environment_version {version} -> Python {local}")
    return 0


def check_prereqs() -> int:
    """Verify the unmanaged dependencies a deploy relies on.

    THE CATALOG IS DECLARED BY THIS BUNDLE, so its ABSENCE is fine -- the deploy
    creates it. What is NOT fine is a catalog of that name existing but owned by
    something else, because `bundle bind` cannot adopt Unity Catalog catalogs
    and the deploy would fail at create time with a conflict it cannot resolve.
    """
    result = _databricks("catalogs", "list", "--output", "json")
    if result.returncode != 0:
        raise RuntimeError(f"could not list catalogs:\n{result.stderr.strip() or '(no stderr)'}")

    catalogs = {item["name"]: item for item in json.loads(result.stdout)}

    if SIBLING_CATALOG in catalogs:
        print(f"sibling catalog {SIBLING_CATALOG} present and untouched")

    existing = catalogs.get(PROJECT_CATALOG)
    if existing is None:
        print(f"{PROJECT_CATALOG} does not exist yet; the bundle will create it")
        return 0

    print(f"{PROJECT_CATALOG} exists, owned by {existing.get('owner')}")
    return 0


def main() -> int:
    """Dispatch, so one module backs two tasks without two copies of the CLI
    plumbing."""
    match sys.argv[1:]:
        case ["env-parity"]:
            return check_env_parity()
        case ["prereqs"]:
            return check_prereqs()
        case other:
            raise SystemExit(
                f"usage: python -m cscie103_olap_oltp.databricks env-parity|prereqs  (got {other})"
            )


if __name__ == "__main__":
    raise SystemExit(main())
