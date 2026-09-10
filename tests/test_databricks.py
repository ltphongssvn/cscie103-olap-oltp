# tests/test_databricks.py
"""The bundle and the local toolchain agree, and the prerequisites exist.

TWO GATES LIVE HERE, AND THEY FAIL FOR DIFFERENT REASONS.

  env parity   .python-version and the bundle's environment_version describe the
               same interpreter. Databricks serverless runs Spark Connect, and
               client and server must share a Python MINOR version or every
               Python UDF dies with "Python versions in the Spark Connect client
               and server are different" -- at UDF execution time, far from the
               change that caused it.

  prereqs      the unmanaged things the bundle depends on are actually there.
               Without this, a missing dependency surfaces as a deploy failure
               with an API error naming the symptom rather than the cause.

HOW THE ISOLATION ASSERTION FOUND ITS INSTRUMENT, IN THREE WRONG STEPS.

First it searched the raw YAML for the sibling catalog's name, stripping lines
starting with `#`. That failed against a correct file: a comment is not always a
line beginning with a hash, and a `>-` block can contain anything.

Then it searched the RESOLVED bundle, on the reasoning that resolution strips
comments. It does -- but a `comment:` FIELD is a deployed value, not prose, and
the catalog's description named the sibling to explain the isolation.

Then it asked structurally, comparing every catalog_name against the CATALOG
RESOURCE REFERENCE -- and that was right until the catalog stopped being a
bundle resource at all (databricks/cli#4513; see src/.../catalog.py). The
reference resolved away, and the assertion described a design that no longer
existed.

The version below asks the question that survives all three: whatever the
mechanism, does every object land in THIS project's catalog and not the
sibling's. That is the property; the reference was only ever one way to get it.
"""

import json
import os
from typing import Any

import pytest

from cscie103_olap_oltp.databricks import (
    PROJECT_CATALOG,
    SIBLING_CATALOG,
    interpreter_drift,
    resolve_bundle,
)
from cscie103_olap_oltp.policy.snapshot import REPO_ROOT

CONTRACT_PATH = REPO_ROOT / "contracts" / "environment-versions.json"


def credentials_are_promised() -> bool:
    """Whether this environment undertook to supply Databricks credentials.

    Same rule as the gh gate: a runner promises them and their absence is a
    defect; a workstation never did, so a skip there is honest rather than a
    green job that verified nothing.
    """
    return os.environ.get("CI", "").lower() in {"true", "1"}


def _contract() -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    return parsed


def _resolved_or_skip() -> dict[str, Any]:
    """The resolved bundle, or an honest outcome about why not."""
    try:
        return resolve_bundle("fixture")
    except RuntimeError as error:
        if credentials_are_promised():
            pytest.fail(f"CI must supply Databricks credentials to this gate.\n{error}")
        pytest.skip(str(error))


def test_agreeing_versions_report_no_drift() -> None:
    assert interpreter_drift("5", "3.12.3", _contract()) is None


def test_disagreeing_versions_report_drift() -> None:
    """The failure this gate exists to move earlier in time."""
    drift = interpreter_drift("5", "3.11.9", _contract())
    assert drift is not None
    assert "3.12.3" in drift
    assert "3.11.9" in drift


def test_an_unknown_environment_version_is_an_error() -> None:
    """A version missing from the mapping means the mapping is stale.

    Treating it as "no drift" would silently disable the check the moment
    Databricks ships a new environment version -- precisely when it matters.
    """
    drift = interpreter_drift("99", "3.12.3", _contract())
    assert drift is not None
    assert "99" in drift


def test_the_contract_names_its_source_and_review_trigger() -> None:
    """A transcribed file with no provenance is a guess with a timestamp.

    The review trigger is an EVENT, not a date: a calendar review either fires
    when nothing changed or misses a version that shipped the week after.
    """
    contract = _contract()
    assert "databricks.com" in contract["x-source"]
    assert contract["x-review-trigger"]
    assert contract["x-verified-on"]


def test_local_interpreter_matches_the_contract() -> None:
    """The real check, offline: .python-version against the mapping.

    Needs no CLI and no workspace, so it runs in the hermetic suite and on a
    plane.
    """
    local = (REPO_ROOT / ".python-version").read_text(encoding="utf-8").strip()
    assert interpreter_drift("5", local, _contract()) is None


@pytest.mark.integration
def test_the_declared_environment_version_is_in_the_contract() -> None:
    """Resolved, not read from the YAML.

    What deploys is what the CLI resolved, including any target override;
    reading the file would check a default a target may not use.
    """
    config = _resolved_or_skip()
    declared = str(config["variables"]["environment_version"]["value"])
    assert declared in _contract()["versions"]


@pytest.mark.integration
def test_the_bundle_resolves_without_deployer_identity() -> None:
    """The fixture target is what makes a stable snapshot possible.

    dev mode prefixes names with the deployer and deploys to a user-scoped path,
    so the same bundle resolves differently for a laptop and a service
    principal. Nothing identity-bearing may appear in the fixture output.
    """
    config = _resolved_or_skip()
    serialised = json.dumps(config)

    assert config["bundle"]["name"] == "cscie103-olap-oltp"
    assert "${workspace.current_user" not in serialised
    assert "${var." not in serialised


@pytest.mark.integration
def test_the_bundle_declares_no_catalog_resource() -> None:
    """THE CATALOG IS NOT THE BUNDLE'S TO CREATE ON THIS TIER.

    Declaring one fails with "Metastore storage root URL does not exist" --
    databricks/cli#4513, closed as not planned. It is created by
    src/cscie103_olap_oltp/catalog.py instead, and if a catalogs block ever
    reappears here every deploy will retry a create that cannot succeed.
    """
    assert "catalogs" not in _resolved_or_skip()["resources"]


@pytest.mark.integration
def test_every_resource_lands_in_this_projects_catalog() -> None:
    """ISOLATION, ASKED AS A PROPERTY RATHER THAN A MECHANISM.

    catalog_name is the field that decides where an object lives. Whether it
    arrives through a variable or a resource reference is an implementation
    detail that has already changed once; that every object lands in THIS
    catalog and never the sibling's is the thing that must stay true.
    """
    resources = _resolved_or_skip()["resources"]

    bindings = [
        resource["catalog_name"]
        for kind in ("schemas", "volumes")
        for resource in resources.get(kind, {}).values()
    ]

    assert bindings, "no schemas or volumes declared; the assertion would be vacuous"
    for binding in bindings:
        assert binding == PROJECT_CATALOG
        assert binding != SIBLING_CATALOG


@pytest.mark.integration
def test_schema_names_are_bare_in_the_resolved_bundle() -> None:
    """The CLI adds its own prefix in development mode.

    Interpolating schema_prefix into a resource name too produces
    dev_user_dev_user_oltp -- a real bug the sibling project shipped and fixed.
    The fixture target sets an empty prefix, so a bare name here proves the
    resource is not double-prefixing.
    """
    schemas = _resolved_or_skip()["resources"]["schemas"]
    assert schemas["oltp"]["name"] == "oltp"
    assert schemas["olap"]["name"] == "olap"


@pytest.mark.integration
def test_the_volume_references_the_schema_resource() -> None:
    """A resource reference declares an ordering; a variable reference does not.

    The schema IS still a bundle resource, so this dependency remains real --
    naming the same string via ${var.oltp_schema} would let the deploy race the
    schema's creation. The reference is unresolved at validate time, which is
    exactly what proves it is a resource reference.
    """
    volume = _resolved_or_skip()["resources"]["volumes"]["raw"]
    assert volume["schema_name"] == "${resources.schemas.oltp.name}"
