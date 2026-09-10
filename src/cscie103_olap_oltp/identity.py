# src/cscie103_olap_oltp/identity.py
"""Provision the account identities CI and the bundle depend on. Idempotent.

The group the bundle grants to, and the service principal CI authenticates as.
Both are identity, so they share one mechanism rather than two.

WHY THIS IS NOT TERRAFORM
The industry split is settled: Terraform owns platform infrastructure that does
not change run-to-run -- workspaces, account-level UC objects, and IDENTITY.
Bundles own the deployable workload. On a paid tier the service principal would
live in Terraform.

It cannot here. The Terraform provider reaches account identity through an
account-level provider aimed at the ACCOUNT host, and Free Edition documents no
access to the account console or account-level APIs. The endpoint this module
uses is a WORKSPACE-domain route that workspace admins may call -- reachable on
this tier, probed rather than assumed. That places it in the third layer of the
standard governance pattern: Terraform for the platform foundation, bundles for
Databricks-native resources, imperative automation for the gaps in between.

CONVERGENCE, NOT ONE-SHOT.

Catalog grants cannot be applied on a fresh workspace, because the catalog does
not exist until the bundle deploys it. That is an ORDERING dependency, not a
circular one, and it resolves in two passes:

    identity  -> group, service principal, workspace assignment, membership
    deploy    -> catalog, schemas, volume
    identity  -> catalog grants, now reconcilable

THE FIRST VERSION PRINTED SKIP AND EXITED 0, which is the fail-open shape this
project removes everywhere else: it would claim identity coverage while CI held
no catalog privileges at all. Bootstrap must be idempotent AND observable -- it
has to be able to say it has not finished. So reconciliation returns a
three-valued outcome, matching the verdict contract used for policy.

MIGRATION TRIGGER: if this account moves to a paid tier, delete this module,
move identity into Terraform, and replace the CI client secret with an OIDC
federation policy. See docs/adr/0001-ci-authentication.md.
"""

from __future__ import annotations

import json
import subprocess
import sys
from enum import StrEnum
from typing import Any

GROUPS_ENDPOINT = "/api/2.0/account/scim/v2/Groups"
SPS_ENDPOINT = "/api/2.0/account/scim/v2/ServicePrincipals"
WORKSPACE_SPS_ENDPOINT = "/api/2.0/preview/scim/v2/ServicePrincipals"


class Convergence(StrEnum):
    """How far reconciliation got.

    PENDING IS THE VALUE THAT MATTERS. Without it, "created the identities but
    granted nothing" and "everything is in place" are the same exit code, and a
    bootstrap that has not finished looks like one that has.
    """

    CONVERGED = "converged"
    PENDING = "pending"
    FAILED = "failed"


# WHAT TO DO NEXT, NOT JUST THAT SOMETHING IS MISSING. "Not converged" is not
# actionable; the ordering is. Without this the next person reads a missing
# catalog as a broken script rather than an unfinished sequence.
PENDING_REMEDY = (
    "The catalog is created by the bundle, so grants cannot be applied yet.\n"
    "  1. mise run bundle:deploy\n"
    "  2. run this again -- it is idempotent and will grant what is now grantable"
)

# DISTINCT EXIT CODES so a caller can tell "run me again after deploying" from
# "something is broken". Collapsing them would make the bootstrap task unable to
# decide whether to continue.
EXIT_CODES = {Convergence.CONVERGED: 0, Convergence.PENDING: 2, Convergence.FAILED: 1}


def exit_code(state: Convergence) -> int:
    return EXIT_CODES[state]


# A PURPOSE-NAMED GROUP RATHER THAN `account users`, which exists and would work
# but means every account user. UC best practice is least privilege through
# purpose-named groups.
#
# NOT `users` OR `admins`: those are WORKSPACE-LOCAL and cannot hold UC
# privileges at all. The API reports the attempt as PRINCIPAL_DOES_NOT_EXIST,
# which reads like a missing group but is a category error. Separately, those
# system groups lose assignable entitlements for all workspaces on 2026-09-14.
GROUPS = ["cscie103_olap_oltp_readers"]

# THIS PROJECT'S OWN SERVICE PRINCIPAL, NOT THE SIBLING'S. Databricks guidance is
# a dedicated service principal per distinct external workload: it preserves
# audit-log attribution and lets access be revoked for one workload without
# affecting others.
#
# WHY A SERVICE PRINCIPAL AT ALL: CI cannot use OAuth user-to-machine, which is
# browser-based and interactive. The alternative is a personal access token --
# a long-lived bearer credential tied to a human, worse on every axis.
SERVICE_PRINCIPALS = ["cscie103-olap-oltp-ci"]

# MINIMUM ENTITLEMENT, DELIBERATELY. Not allow-cluster-create, not
# databricks-sql-access. CI validates and deploys bundles; it does not need
# compute. An entitlement granted just in case is one nobody removes later.
WORKSPACE_ENTITLEMENTS = ["workspace-access"]

# Group membership rather than direct grants: UC guidance is to use groups and
# avoid granting individual principals, so a second CI identity joins the group
# instead of accumulating its own privileges.
GROUP_MEMBERSHIPS = {"cscie103_olap_oltp_readers": ["cscie103-olap-oltp-ci"]}

# USE_CATALOG is required to reach anything inside; BROWSE allows listing objects
# without reading data, which is what the prereqs and bundle-validate gates need.
# Deliberately NOT ALL_PRIVILEGES and NOT CREATE_SCHEMA.
CATALOG_GRANTS: dict[str, dict[str, list[str]]] = {
    "cscie103_olap_oltp": {"cscie103_olap_oltp_readers": ["USE_CATALOG", "BROWSE"]},
}


def _cli(*args: str) -> Any:
    """Call the pinned Databricks CLI and parse its JSON response.

    NO PROFILE HANDLING, DELIBERATELY. The CLI resolves credentials in a
    documented order -- environment variables, then the .databrickscfg DEFAULT
    profile -- stopping at the first success. CI has DATABRICKS_CLIENT_ID and
    _SECRET and stops at step one; a laptop has neither and falls through to
    DEFAULT. Identical code runs in both, with no "which environment am I in"
    branch to get wrong.

    S603 IS SUPPRESSED NARROWLY: `*args` makes the list computed, which is what
    ruff flags. Every call site passes literal subcommands; there is no shell.
    """
    result = subprocess.run(  # noqa: S603
        ["databricks", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"databricks {' '.join(args)} failed:\n{result.stderr.strip() or '(no stderr)'}"
        )
    return json.loads(result.stdout) if result.stdout.strip() else {}


def existing(endpoint: str) -> dict[str, str]:
    """displayName -> id for everything currently at this endpoint."""
    payload = _cli("api", "get", endpoint)
    return {
        item["displayName"]: item["id"]
        for item in payload.get("Resources", [])
        if "displayName" in item and "id" in item
    }


def missing_from(present: dict[str, str], wanted: list[str]) -> list[str]:
    """What still needs creating.

    A PURE FUNCTION, so idempotence is testable without an account: a second run
    over the same state returns an empty list, which is the whole property.
    """
    return [name for name in wanted if name not in present]


def _reconcile(endpoint: str, schema: str, wanted: list[str], label: str) -> None:
    present = existing(endpoint)
    for name in wanted:
        if name in present:
            print(f"ok      {label} {name} (id {present[name]})")
            continue
        _cli(
            "api",
            "post",
            endpoint,
            "--json",
            json.dumps({"schemas": [schema], "displayName": name}),
        )
        print(f"created {label} {name}")


def _reconcile_workspace_assignment() -> None:
    """Assign the account service principal to the workspace so it can log in.

    AN ACCOUNT PRINCIPAL CAN EXIST AND STILL AUTHENTICATE NOWHERE. It must also
    be assigned to the workspace with an entitlement, and it holds no UC
    privileges until it joins a group that does.
    """
    present = existing(WORKSPACE_SPS_ENDPOINT)
    detail = _cli("api", "get", SPS_ENDPOINT)

    app_ids = {
        item["displayName"]: item["applicationId"]
        for item in detail.get("Resources", [])
        if "displayName" in item and "applicationId" in item
    }

    for name in SERVICE_PRINCIPALS:
        if name in present:
            print(f"ok      workspace assignment {name}")
            continue
        if name not in app_ids:
            print(f"SKIP    workspace assignment {name}: not an account principal")
            continue
        # POST THE applicationId, NOT THE SCIM id. The workspace endpoint links
        # to the account identity by application id; posting the SCIM id creates
        # a SECOND, unlinked principal -- worth avoiding rather than debugging.
        _cli(
            "api",
            "post",
            WORKSPACE_SPS_ENDPOINT,
            "--json",
            json.dumps(
                {
                    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServicePrincipal"],
                    "applicationId": app_ids[name],
                    "displayName": name,
                    "entitlements": [{"value": e} for e in WORKSPACE_ENTITLEMENTS],
                }
            ),
        )
        print(f"created workspace assignment {name}")


def _reconcile_group_memberships() -> None:
    groups = existing(GROUPS_ENDPOINT)
    principals = existing(SPS_ENDPOINT)

    for group, members in GROUP_MEMBERSHIPS.items():
        if group not in groups:
            print(f"SKIP    membership {group}: group does not exist")
            continue
        current = _cli("api", "get", f"{GROUPS_ENDPOINT}/{groups[group]}")
        held = {member.get("display") for member in current.get("members", [])}

        for member in members:
            if member in held:
                print(f"ok      membership {group} <- {member}")
                continue
            if member not in principals:
                print(f"SKIP    membership {group} <- {member}: principal missing")
                continue
            _cli(
                "api",
                "patch",
                f"{GROUPS_ENDPOINT}/{groups[group]}",
                "--json",
                json.dumps(
                    {
                        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                        "Operations": [
                            {
                                "op": "add",
                                "path": "members",
                                "value": [{"value": principals[member]}],
                            }
                        ],
                    }
                ),
            )
            print(f"added   membership {group} <- {member}")


def _reconcile_catalog_grants() -> Convergence:
    """Grant on catalogs that exist. REPORTS PENDING when one does not.

    The catalog is created by the bundle, so on a fresh workspace this cannot
    complete -- and saying so is the point. Returning CONVERGED here would mean
    CI holds no catalog privileges while the bootstrap reports success.
    """
    state = Convergence.CONVERGED

    for catalog, wanted in CATALOG_GRANTS.items():
        endpoint = f"/api/2.1/unity-catalog/permissions/catalog/{catalog}"
        try:
            current = _cli("api", "get", endpoint)
        except RuntimeError:
            print(f"pending grants {catalog}: catalog does not exist yet")
            state = Convergence.PENDING
            continue

        held = {
            assignment["principal"]: set(assignment.get("privileges", []))
            for assignment in current.get("privilege_assignments", [])
        }
        for principal, privileges in wanted.items():
            absent = sorted(set(privileges) - held.get(principal, set()))
            if not absent:
                print(f"ok      grant {catalog} -> {principal}")
                continue
            _cli(
                "api",
                "patch",
                endpoint,
                "--json",
                json.dumps({"changes": [{"principal": principal, "add": absent}]}),
            )
            print(f"granted {catalog} -> {principal}: {', '.join(absent)}")

    return state


def reconcile() -> Convergence:
    """Bring identity to the desired state, reporting how far it got."""
    try:
        _reconcile(
            GROUPS_ENDPOINT,
            "urn:ietf:params:scim:schemas:core:2.0:Group",
            GROUPS,
            "group",
        )
        _reconcile(
            SPS_ENDPOINT,
            "urn:ietf:params:scim:schemas:core:2.0:ServicePrincipal",
            SERVICE_PRINCIPALS,
            "service principal",
        )
        _reconcile_workspace_assignment()
        _reconcile_group_memberships()
        return _reconcile_catalog_grants()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return Convergence.FAILED


def main() -> int:
    state = reconcile()

    if state is Convergence.PENDING:
        print(f"\nnot converged.\n{PENDING_REMEDY}", file=sys.stderr)
    elif state is Convergence.FAILED:
        print("\nreconciliation failed; see the error above.", file=sys.stderr)
    else:
        print("\nidentity converged")

    return exit_code(state)


if __name__ == "__main__":
    raise SystemExit(main())


# --- OAuth secrets are DELIBERATELY not created here --------------------------
#
# Databricks returns an OAuth client secret exactly ONCE, in the response body.
# Anything that prints it puts it in terminal scrollback, in shell history, and
# in any log capturing that session. That happened in the sibling project: a
# secret was generated with a bare API call, rendered to the terminal, and had to
# be revoked and reissued immediately.
#
# The fix is not "remember to be careful". A secret must never exist anywhere a
# human or a log can see it, so it is piped from the API straight into the secret
# store -- never through a variable, a file, or a screen. That is a one-time,
# supervised act with a destination, not something a reconciliation module should
# perform: automating it here would mean this module either prints a secret or
# silently rotates a live credential.
#
# tests/test_identity.py asserts this module's source contains no such call, so
# the omission is enforced rather than remembered.
#
# WHAT IS AND IS NOT SECRET
#   applicationId  NOT secret. It is the OAuth client ID and belongs in the repo
#                  and in workflow files, exactly like a username.
#   client secret  Secret. Exists only inside GitHub Actions secrets.
