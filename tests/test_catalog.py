# tests/test_catalog.py
"""The catalog is created as code, through the path that works on this tier.

WHY IT IS NOT A BUNDLE RESOURCE, HAVING BEEN ONE FOR AN HOUR.

Catalogs ARE a bundle resource type on the direct deployment engine, and this
project declared one -- correctly, since ours did not exist and `bundle bind`
cannot adopt an existing catalog. The deploy then failed:

    Metastore storage root URL does not exist. Default Storage is enabled in
    your account. (400 INVALID_STATE)

That is databricks/cli#4513, filed February 2026 against exactly this
combination -- Free Edition with no default location configured -- and CLOSED AS
NOT PLANNED. Waiting for a fix is not a plan.

The catalog itself is creatable: serverless workspaces support
`CREATE CATALOG catalog_name` with NO location specified, resolving storage from
Default Storage. It is the REST POST /catalogs path the bundle uses that demands
a storage_root the metastore does not have.

THE DESTRUCTIVE-STATEMENT TEST TOOK THREE ATTEMPTS, AND THE LESSON IS GENERAL.

  1. grep the module source for "DROP" -- failed on the comment banner
     explaining that the module never drops anything.
  2. parse the AST and check string literals -- comments gone, but DOCSTRINGS
     are string constants too, so "delete this module" tripped it.
  3. assert on what the function RETURNS.

Only the statement handed to the execution API can execute. Prose about the
statement cannot, in a comment or a docstring. Twice I tested the text when the
question was behavioural; the third version asks the behavioural question, and
it cannot be defeated by writing more explanation.
"""

import pytest

from cscie103_olap_oltp.catalog import (
    CATALOG_NAME,
    OWNED,
    SIBLING_CATALOG,
    create_statement,
)
from cscie103_olap_oltp.policy.snapshot import REPO_ROOT

# Verbs that would make this module capable of destroying something.
DESTRUCTIVE = ("DROP", "DELETE", "TRUNCATE", "ALTER", "REPLACE")


def test_the_statement_is_idempotent() -> None:
    """IF NOT EXISTS IS THE WHOLE PROPERTY.

    Bootstrap must converge on repeated runs. Without it, a second run fails on
    a catalog that is already correct -- which trains people to run bootstrap
    once and never again, and a bootstrap nobody re-runs cannot reconcile drift.
    """
    assert "CREATE CATALOG IF NOT EXISTS" in create_statement(CATALOG_NAME)


def test_the_statement_specifies_no_location() -> None:
    """MANAGED LOCATION would be wrong here, not merely unnecessary.

    Serverless workspaces resolve storage from Default Storage when no location
    is given. Supplying one would require an external location and a storage
    credential, neither of which exists on this tier.
    """
    assert "LOCATION" not in create_statement(CATALOG_NAME)


def test_the_statement_names_this_projects_catalog() -> None:
    assert CATALOG_NAME in create_statement(CATALOG_NAME)


def test_the_catalog_is_not_the_siblings() -> None:
    """ISOLATION. Free Edition shares one workspace and one metastore, so the
    catalog is the only boundary available."""
    assert CATALOG_NAME != SIBLING_CATALOG
    assert CATALOG_NAME == "cscie103_olap_oltp"


def test_the_module_refuses_an_unexpected_catalog_name() -> None:
    """A NAME IS AN IDENTIFIER GOING INTO SQL.

    Identifiers cannot be parameterised, so the choice is an exact allow-list or
    a sanitiser that has to be right every time. With one legal value the
    allow-list is trivially correct.
    """
    with pytest.raises(ValueError, match="not a catalog this project owns"):
        create_statement("something_else")


def test_no_statement_this_module_can_produce_is_destructive() -> None:
    """A BOOTSTRAP THAT CAN DESTROY IS NOT A BOOTSTRAP.

    There is no grant-only mode for UC objects, so a module able to issue a
    destructive statement is one command away from deleting a catalog it did not
    create -- and on a shared metastore, one that belongs to another project.

    ASSERTED ON THE OUTPUT, over every input the module accepts. Prose cannot
    defeat this, and neither can a comment: only what the function returns is
    ever executed.
    """
    for name in OWNED:
        statement = create_statement(name).upper()
        for verb in DESTRUCTIVE:
            assert verb not in statement, statement


def test_the_bundle_no_longer_declares_a_catalog() -> None:
    """The two must not both own it.

    If the bundle still declared a catalog resource, every deploy would retry
    the create that cannot succeed on this tier, and the failure would look new
    each time.
    """
    resources = (REPO_ROOT / "resources" / "unity_catalog.yml").read_text(encoding="utf-8")
    code = "\n".join(line for line in resources.splitlines() if not line.lstrip().startswith("#"))
    assert "catalogs:" not in code


def test_the_schemas_still_reference_the_catalog_variable() -> None:
    """The bundle no longer CREATES the catalog but still deploys into it.

    With the catalog resource gone, the resource reference goes with it, so
    schemas bind through the variable instead.
    """
    resources = (REPO_ROOT / "resources" / "unity_catalog.yml").read_text(encoding="utf-8")
    assert "catalog_name: ${var.catalog}" in resources


def test_the_reason_is_recorded_where_someone_will_look() -> None:
    """A missing catalogs block looks like an oversight without this.

    The next person's first instinct is to add one back, which reproduces the
    failure exactly.
    """
    resources = (REPO_ROOT / "resources" / "unity_catalog.yml").read_text(encoding="utf-8")
    assert "4513" in resources
