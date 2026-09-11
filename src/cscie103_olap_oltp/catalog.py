# src/cscie103_olap_oltp/catalog.py
"""Create this project's Unity Catalog catalog. Idempotent.

WHY THIS IS NOT A BUNDLE RESOURCE, HAVING BEEN ONE.

Catalogs ARE a bundle resource type on the direct deployment engine, and this
project declared one -- correctly, since ours did not exist and `bundle bind`
cannot adopt an existing catalog (databricks/cli#4842). The deploy then failed:

    Metastore storage root URL does not exist. Default Storage is enabled in
    your account. (400 INVALID_STATE)

That is databricks/cli#4513, filed February 2026 against exactly this
combination -- Free Edition with no default location configured -- and CLOSED AS
NOT PLANNED. Waiting for a fix is not a plan.

THE CATALOG ITSELF IS CREATABLE. Serverless workspaces support
`CREATE CATALOG catalog_name` with NO location specified, resolving storage from
Default Storage. It is the REST POST /catalogs path the bundle uses that demands
a storage_root the metastore does not have.

So the catalog is created here instead -- still as code, still tested, still
reviewable, just through the API that works on this tier. That is the third
layer of the standard governance pattern: Terraform for the platform, bundles
for Databricks-native resources, imperative automation for the gaps declarative
configuration cannot reach. This gap is proven by a filed issue rather than
assumed.

MIGRATION TRIGGER: if the CLI issue is fixed, or this account gains a metastore
root storage location, move the catalog back into resources/unity_catalog.yml
and delete this module.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

# THIS PROJECT'S CATALOG. Free Edition shares one workspace and one metastore
# between projects, so the catalog is the only isolation boundary available.
CATALOG_NAME = "cscie103_olap_oltp"

# NAMED SO THE ISOLATION CAN BE ASSERTED, never written to.
SIBLING_CATALOG = "cscie103_catalog"

COMMENT = "OLTP-to-OLAP demonstration platform."

# THE ONLY CATALOGS THIS MODULE MAY NAME. A catalog name is an IDENTIFIER going
# into a SQL statement, and identifiers cannot be parameterised -- so the choice
# is an exact allow-list or a sanitiser that has to be right every time. With
# one legal value, the allow-list is trivially correct.
OWNED = frozenset({CATALOG_NAME})


def create_statement(name: str) -> str:
    """The SQL that creates the catalog, or a refusal.

    IF NOT EXISTS IS THE WHOLE IDEMPOTENCE PROPERTY. Bootstrap must converge on
    repeated runs; without it a second run fails on a catalog that is already
    correct, which trains people to run bootstrap once and never again -- and a
    bootstrap nobody re-runs cannot reconcile drift.

    NO MANAGED LOCATION, WHICH IS REQUIRED RATHER THAN OMITTED. Serverless
    workspaces resolve storage from Default Storage when no location is given.
    Supplying one would need an external location and a storage credential,
    neither of which exists on this tier.
    """
    if name not in OWNED:
        raise ValueError(f"{name!r} is not a catalog this project owns")
    return f"CREATE CATALOG IF NOT EXISTS {name} COMMENT '{COMMENT}'"


def _cli(*args: str) -> Any:
    """Call the pinned Databricks CLI and parse its JSON response.

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


def warehouse_id() -> str:
    """The SQL warehouse to execute against.

    DISCOVERED, NOT HARDCODED. A warehouse id is workspace-specific, so a
    literal would make this module work on exactly one account -- and Free
    Edition allows exactly one warehouse, so there is nothing to choose between.

    RAISES ON ABSENCE rather than returning a default: with no warehouse the
    statement cannot run at all, and a clear message beats an API error about an
    empty id.
    """
    warehouses = _cli("warehouses", "list", "--output", "json")
    if not warehouses:
        raise RuntimeError(
            "no SQL warehouse exists in this workspace.\n"
            "The catalog is created through the Statement Execution API, which "
            "needs one. Free Edition provisions a Serverless Starter Warehouse."
        )
    identifier: str = warehouses[0]["id"]
    return identifier


def execute(statement: str) -> None:
    """Run one SQL statement through the Statement Execution API.

    EXTRACTED SO TABLE CREATION CAN REUSE IT rather than repeating the warehouse
    lookup, the request shape and the state check. Those three details were
    already correct here; copying them into a second module would give the copy
    its own chance to be wrong.

    THE WAREHOUSE IS STARTED BY THIS CALL if it is stopped, which is normal on a
    tier whose single warehouse auto-stops. wait_timeout gives it room to come
    up rather than reporting a cold start as a failure.
    """
    response = _cli(
        "api",
        "post",
        "/api/2.0/sql/statements",
        "--json",
        json.dumps(
            {
                "warehouse_id": warehouse_id(),
                "statement": statement,
                "wait_timeout": "50s",
            }
        ),
    )

    state = response.get("status", {}).get("state")
    if state != "SUCCEEDED":
        raise RuntimeError(f"statement did not succeed: {response.get('status')}")


def existing_catalogs() -> set[str]:
    listing = _cli("catalogs", "list", "--output", "json")
    return {item["name"] for item in listing}


def reconcile() -> bool:
    """Create the catalog if absent. True when it exists afterwards.

    THE WAREHOUSE IS STARTED BY THIS CALL if it is stopped, which is expected on
    a tier where the single warehouse auto-stops. wait_timeout gives it room to
    come up rather than reporting a timeout as a failure.
    """
    if CATALOG_NAME in existing_catalogs():
        print(f"ok      catalog {CATALOG_NAME}")
        return True

    response = _cli(
        "api",
        "post",
        "/api/2.0/sql/statements",
        "--json",
        json.dumps(
            {
                "warehouse_id": warehouse_id(),
                "statement": create_statement(CATALOG_NAME),
                "wait_timeout": "50s",
            }
        ),
    )

    state = response.get("status", {}).get("state")
    if state != "SUCCEEDED":
        raise RuntimeError(f"catalog creation did not succeed: {response.get('status')}")

    print(f"created catalog {CATALOG_NAME}")
    return True


def main() -> int:
    try:
        reconcile()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# --- THIS MODULE NEVER DROPS ANYTHING ----------------------------------------
#
# There is no grant-only mode for Unity Catalog objects, and a bootstrap that
# can destroy is not a bootstrap. A module able to issue a destructive statement
# is one command away from deleting a catalog it did not create -- and on a
# shared metastore, that catalog might belong to another project.
#
# Teardown, if it is ever wanted, belongs in a separate deliberate operation
# with its own review, not in the path that runs on every bootstrap.
