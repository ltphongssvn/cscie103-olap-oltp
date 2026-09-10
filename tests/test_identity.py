# tests/test_identity.py
"""The identities CI and the bundle depend on, provisioned as code.

WHY A SCRIPT AND NOT THE BUNDLE OR TERRAFORM
The industry split is settled: Terraform owns platform infrastructure and
IDENTITY; bundles own the deployable workload. On a paid tier the service
principal would live in Terraform. It cannot here -- the Terraform provider
reaches account identity through an account-level provider aimed at the ACCOUNT
host, and Free Edition documents no access to the account console or
account-level APIs.

CONVERGENCE, NOT ONE-SHOT, AND THAT IS THE INTERESTING PART.

The catalog grants cannot be applied on a fresh workspace, because the catalog
does not exist until the bundle deploys it. That is an ORDERING dependency, not
a circular one, and it resolves in two passes:

    identity  -> group, service principal, workspace assignment, membership
    deploy    -> catalog, schemas, volume
    identity  -> catalog grants, now reconcilable

The first implementation printed SKIP for the ungranted catalog and exited 0.
That is the fail-open shape this project removes everywhere else: it would claim
identity coverage while CI held no catalog privileges at all. Bootstrap must be
idempotent AND observable -- it has to be able to say it has not finished.

So reconciliation reports a three-valued outcome, matching the verdict contract
already used for policy: converged, not converged, or failed.
"""

import inspect
from typing import Any

import pytest

from cscie103_olap_oltp import identity
from cscie103_olap_oltp.environment import current
from cscie103_olap_oltp.identity import (
    CATALOG_GRANTS,
    GROUPS,
    SERVICE_PRINCIPALS,
    WORKSPACE_ENTITLEMENTS,
    Convergence,
    missing_from,
)


def credentials_are_promised() -> bool:
    """Same rule as every other live gate: a runner promised credentials, a
    workstation never did."""
    return current().ci


def test_the_service_principal_is_this_projects_own() -> None:
    """ISOLATION APPLIES TO IDENTITY, NOT ONLY TO THE CATALOG.

    Databricks guidance is one service principal per distinct external workload:
    it preserves audit-log attribution and lets one workload be revoked without
    affecting others. Reusing the sibling's would work immediately and destroy
    both properties.
    """
    assert SERVICE_PRINCIPALS == ["cscie103-olap-oltp-ci"]
    assert "cscie103-ci" not in SERVICE_PRINCIPALS


def test_the_group_is_purpose_named() -> None:
    """NOT `account users`, which exists and would work.

    It means every account user, and UC best practice is least privilege through
    purpose-named groups.
    """
    assert GROUPS == ["cscie103_olap_oltp_readers"]


def test_no_workspace_local_group_is_used() -> None:
    """`users` and `admins` are WORKSPACE-LOCAL and cannot hold UC privileges.

    The API reports the attempt as PRINCIPAL_DOES_NOT_EXIST, which reads like a
    missing group and is actually a category error. Separately, those system
    groups lose assignable entitlements for all workspaces on 2026-09-14.
    """
    assert "users" not in GROUPS
    assert "admins" not in GROUPS


def test_the_entitlement_is_minimal() -> None:
    """workspace-access ONLY.

    Not allow-cluster-create, not databricks-sql-access. CI validates and
    deploys bundles; it does not need compute. An entitlement granted just in
    case is one nobody removes later.
    """
    assert WORKSPACE_ENTITLEMENTS == ["workspace-access"]


def test_grants_target_the_group_not_the_principal() -> None:
    """UC guidance is to grant to groups and avoid direct grants to principals.

    A second CI identity then inherits access by joining the group rather than
    accumulating its own grant.
    """
    for wanted in CATALOG_GRANTS.values():
        for principal in wanted:
            assert principal in GROUPS


def test_grants_are_read_only() -> None:
    """USE_CATALOG to reach inside, BROWSE to list without reading data.

    Deliberately NOT ALL_PRIVILEGES and NOT CREATE_SCHEMA: this identity
    validates and deploys, and the bundle's own resources carry the rest.
    """
    for wanted in CATALOG_GRANTS.values():
        for privileges in wanted.values():
            assert set(privileges) <= {"USE_CATALOG", "BROWSE"}


def test_grants_target_only_this_projects_catalog() -> None:
    """The sibling's catalog is not this project's to grant on."""
    assert set(CATALOG_GRANTS) == {"cscie103_olap_oltp"}


def test_missing_from_reports_what_is_absent() -> None:
    """The reconciliation primitive: wanted minus present."""
    assert missing_from({"a": "1"}, ["a", "b"]) == ["b"]


def test_missing_from_is_empty_when_all_present() -> None:
    """Idempotence in one assertion: a second run creates nothing."""
    assert missing_from({"a": "1", "b": "2"}, ["a", "b"]) == []


def test_convergence_is_three_valued() -> None:
    """converged / pending / failed.

    `pending` is what the first implementation lacked: it printed SKIP and
    exited 0, so a run that had granted nothing reported success.
    """
    assert set(Convergence) == {
        Convergence.CONVERGED,
        Convergence.PENDING,
        Convergence.FAILED,
    }


def test_pending_does_not_exit_zero() -> None:
    """THE ASSERTION THAT CLOSES THE FAIL-OPEN.

    A bootstrap that has not finished must not look like one that has. Exit
    codes are distinct so a caller can tell "run me again after deploying" from
    "something is broken".
    """
    assert identity.exit_code(Convergence.CONVERGED) == 0
    assert identity.exit_code(Convergence.PENDING) != 0
    assert identity.exit_code(Convergence.FAILED) != 0
    assert identity.exit_code(Convergence.PENDING) != identity.exit_code(Convergence.FAILED)


def test_pending_explains_what_to_do_next() -> None:
    """ "Not converged" is not actionable; the ordering is.

    The catalog is created by the bundle, so the remedy is a deploy followed by
    a second run -- and the message has to say so, or the next person reads a
    missing catalog as a broken script.
    """
    assert "deploy" in identity.PENDING_REMEDY
    assert "again" in identity.PENDING_REMEDY


def test_the_module_never_creates_a_secret() -> None:
    """THE ASSERTION THAT KEEPS A CREDENTIAL OUT OF SCROLLBACK.

    Databricks returns an OAuth client secret exactly once, in the response
    body. The sibling project generated one with a bare API call, it rendered to
    the terminal, and it had to be revoked -- it was in scrollback, in shell
    history, and transmitted.
    """
    assert "credentials/secrets" not in inspect.getsource(identity)


@pytest.mark.integration
def test_the_account_scim_route_is_reachable() -> None:
    """The workspace-domain route this whole module depends on.

    Free Edition blocks the account console; this endpoint is the documented
    exception, and if it ever stops working the module cannot function at all.
    """
    try:
        principals: dict[str, Any] = identity.existing(identity.SPS_ENDPOINT)
    except RuntimeError as error:
        if credentials_are_promised():
            pytest.fail(f"CI must supply Databricks credentials to this gate.\n{error}")
        pytest.skip(str(error))

    assert isinstance(principals, dict)


@pytest.mark.integration
def test_this_projects_service_principal_exists() -> None:
    """Provisioned, not assumed.

    If this fails, CI has no identity to authenticate as and the three live
    gates cannot run on a runner at all.
    """
    try:
        principals = identity.existing(identity.SPS_ENDPOINT)
    except RuntimeError as error:
        if credentials_are_promised():
            pytest.fail(f"CI must supply Databricks credentials to this gate.\n{error}")
        pytest.skip(str(error))

    for name in SERVICE_PRINCIPALS:
        assert name in principals
