# tests/test_catalog_cli.py
"""The catalog bootstrap, exercised rather than assumed.

    Tests as Code   the reconciler's decisions are checked, not its effects

WHY THIS EXISTS. 177 mutants in catalog.py were marked "no tests": the CLI
wrapper, the warehouse lookup, the statement executor and the reconciler were
all unexercised. Only `create_statement` had tests, because it is the one pure
function -- everything that decides whether to act, and what to do when the
platform refuses, ran nowhere.

THE SUBPROCESS IS THE BOUNDARY, AND IT IS FAKED. These functions are thin
wrappers around the Databricks CLI, so the behaviour worth testing is the
DECISION: does it create when absent and skip when present, does it raise on a
non-SUCCEEDED state, does it refuse a catalog this project does not own.
Reaching the real workspace would test Databricks, slowly.
"""

import json

import pytest

from cscie103_olap_oltp import catalog


def test_a_catalog_this_project_does_not_own_is_refused() -> None:
    """THE ALLOW-LIST IS THE WHOLE SAFETY PROPERTY.

    A catalog name is an identifier going into SQL, and identifiers cannot be
    parameterised -- so the choice is an exact allow-list or a sanitiser that
    has to be right every time. On a shared metastore the sibling project's
    catalog is one typo away.
    """
    with pytest.raises(ValueError, match="not a catalog this project owns"):
        catalog.create_statement(catalog.SIBLING_CATALOG)


def test_the_statement_is_idempotent_and_omits_a_location() -> None:
    """IF NOT EXISTS IS THE IDEMPOTENCE PROPERTY, and the absent location is
    required rather than forgotten: serverless resolves storage from Default
    Storage, and supplying one would need an external location this tier has
    no way to create."""
    statement = catalog.create_statement(catalog.CATALOG_NAME)

    assert statement.startswith(f"CREATE CATALOG IF NOT EXISTS {catalog.CATALOG_NAME}")
    assert "MANAGED LOCATION" not in statement


def test_the_warehouse_is_discovered_not_hardcoded(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A WAREHOUSE ID IS WORKSPACE-SPECIFIC, so a literal would bind this
    module to one account.

    THE FAKE RECORDS ITS ARGUMENTS, WHICH IS THE WHOLE POINT. A fake that
    ignores them makes every subcommand mutable without consequence -- mutants
    turning "list" into None survived, because nothing looked.
    """
    calls: list[tuple[str, ...]] = []

    def fake_cli(*args: str) -> object:
        calls.append(args)
        return [{"id": "abc123"}]

    monkeypatch.setattr(catalog, "_cli", fake_cli)

    assert catalog.warehouse_id() == "abc123"
    assert calls == [("warehouses", "list", "--output", "json")]


def test_no_warehouse_is_a_named_failure_not_an_api_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """WITHOUT A WAREHOUSE THE STATEMENT CANNOT RUN AT ALL, and a clear message
    beats an API error about an empty id."""
    monkeypatch.setattr(catalog, "_cli", lambda *_: [])

    with pytest.raises(RuntimeError, match="no SQL warehouse exists"):
        catalog.warehouse_id()


def test_executing_sends_the_statement_and_waits(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """THE REQUEST SHAPE IS THE CONTRACT WITH THE API.

    wait_timeout gives a stopped warehouse room to start -- normal on a tier
    whose single warehouse auto-stops -- so its absence would report a cold
    start as a failure.
    """
    sent: dict[str, object] = {}

    calls: list[tuple[str, ...]] = []

    def fake_cli(*args: str) -> object:
        calls.append(args)
        if args[0] == "warehouses":
            return [{"id": "w1"}]
        sent.update(json.loads(args[-1]))
        return {"status": {"state": "SUCCEEDED"}}

    monkeypatch.setattr(catalog, "_cli", fake_cli)
    catalog.execute("SELECT 1")

    # THE ENDPOINT AND VERB ARE THE CONTRACT, and mutants blanked them
    # unnoticed while the fake ignored its arguments.
    assert calls[-1][:4] == ("api", "post", "/api/2.0/sql/statements", "--json")
    assert sent["statement"] == "SELECT 1"
    assert sent["warehouse_id"] == "w1"
    assert sent["wait_timeout"] == "50s"


def test_a_statement_that_does_not_succeed_raises(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """ANY STATE BUT SUCCEEDED IS A FAILURE. Treating PENDING or FAILED as
    success would report a catalog created that is not."""
    monkeypatch.setattr(
        catalog,
        "_cli",
        lambda *args: (
            [{"id": "w1"}]
            if args[0] == "warehouses"
            else {"status": {"state": "FAILED", "error": {"message": "nope"}}}
        ),
    )

    with pytest.raises(RuntimeError, match="did not succeed"):
        catalog.execute("SELECT 1")


def test_an_existing_catalog_is_left_alone(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    """RECONCILE MEANS CONVERGE, NOT RECREATE."""
    monkeypatch.setattr(catalog, "existing_catalogs", lambda: {catalog.CATALOG_NAME})
    monkeypatch.setattr(
        catalog, "_cli", lambda *_: pytest.fail("must not call the API when it already exists")
    )

    assert catalog.reconcile() is True
    assert capsys.readouterr().out.startswith(f"ok      catalog {catalog.CATALOG_NAME}")


def test_an_absent_catalog_is_created(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    """AND THE STATEMENT SENT IS THE ONE create_statement RENDERS, not a second
    copy written at the call site."""
    sent: dict[str, object] = {}

    calls: list[tuple[str, ...]] = []

    def fake_cli(*args: str) -> object:
        calls.append(args)
        if args[0] == "warehouses":
            return [{"id": "w1"}]
        sent.update(json.loads(args[-1]))
        return {"status": {"state": "SUCCEEDED"}}

    monkeypatch.setattr(catalog, "existing_catalogs", lambda: set())
    monkeypatch.setattr(catalog, "_cli", fake_cli)

    assert catalog.reconcile() is True
    assert calls[-1][:4] == ("api", "post", "/api/2.0/sql/statements", "--json")
    assert sent["statement"] == catalog.create_statement(catalog.CATALOG_NAME)
    assert sent["wait_timeout"] == "50s"
    assert set(sent) == {"warehouse_id", "statement", "wait_timeout"}
    assert capsys.readouterr().out.startswith(f"created catalog {catalog.CATALOG_NAME}")


def test_the_cli_reports_a_failure_without_a_traceback(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    """A BOOTSTRAP THAT PRINTS A STACK TRACE TEACHES PEOPLE TO IGNORE IT."""

    def boom() -> bool:
        raise RuntimeError("no SQL warehouse exists in this workspace.")

    monkeypatch.setattr(catalog, "reconcile", boom)

    assert catalog.main() == 1
    assert "no SQL warehouse exists" in capsys.readouterr().err


def test_the_cli_succeeds_when_reconciliation_does(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(catalog, "reconcile", lambda: True)

    assert catalog.main() == 0


def test_listing_catalogs_asks_for_json(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """existing_catalogs WAS ENTIRELY UNEXERCISED -- 19 mutants with no tests.

    It is two lines, and both matter: the wrong subcommand returns nothing and
    the reconciler then creates a catalog that exists, while a missing
    --output json returns text that the parser turns into an empty set.
    """
    calls: list[tuple[str, ...]] = []

    def fake_cli(*args: str) -> object:
        calls.append(args)
        return [{"name": "one"}, {"name": "two"}]

    monkeypatch.setattr(catalog, "_cli", fake_cli)

    assert catalog.existing_catalogs() == {"one", "two"}
    assert calls == [("catalogs", "list", "--output", "json")]


def test_the_cli_wrapper_parses_json_and_tolerates_silence(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """_cli HAD NO TESTS AT ALL -- 23 mutants -- because every other test
    replaces it.

    REPLACING THE BOUNDARY LEAVES THE BOUNDARY UNTESTED, which is the structural
    lesson here: the fake made the callers testable and made this invisible.
    Some Databricks subcommands print nothing on success, so an empty stdout
    must not be a JSON error.
    """
    import subprocess

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_, **__: subprocess.CompletedProcess([], 0, '{"ok": true}', ""),
    )
    assert catalog._cli("catalogs", "list") == {"ok": True}

    monkeypatch.setattr(
        subprocess, "run", lambda *_, **__: subprocess.CompletedProcess([], 0, "  \n", "")
    )
    assert catalog._cli("catalogs", "list") == {}


def test_a_failing_cli_call_reports_the_command_and_the_stderr(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """WHICH COMMAND FAILED AND WHAT IT SAID ARE BOTH THE DIAGNOSIS.

    Without the command the reader cannot reproduce it; without stderr they
    cannot tell an auth failure from a missing resource.
    """
    import subprocess

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_, **__: subprocess.CompletedProcess([], 1, "", "PERMISSION_DENIED"),
    )

    with pytest.raises(RuntimeError) as caught:
        catalog._cli("catalogs", "list")

    assert "databricks catalogs list failed" in str(caught.value)
    assert "PERMISSION_DENIED" in str(caught.value)


def test_a_silent_failure_still_says_something(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A NON-ZERO EXIT WITH NO STDERR IS THE WORST CASE TO DEBUG, so the
    message says so rather than trailing off."""
    import subprocess

    monkeypatch.setattr(
        subprocess, "run", lambda *_, **__: subprocess.CompletedProcess([], 1, "", "")
    )

    with pytest.raises(RuntimeError, match=r"\(no stderr\)"):
        catalog._cli("catalogs", "list")


def test_the_no_warehouse_message_says_why_one_is_needed() -> None:
    """A REFUSAL THAT DOES NOT SAY WHAT TO PROVISION MAKES THE READER GUESS.

    Mutants corrupted this sentence unnoticed because the test matched a
    fragment. Asserting the facts it carries, rather than its wording, kills
    them without freezing the prose.
    """
    assert "Statement Execution API" in catalog.NO_WAREHOUSE
    assert "Serverless Starter Warehouse" in catalog.NO_WAREHOUSE


def test_a_malformed_response_is_a_named_failure_not_a_crash(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`.get("status", {})` MEANS A MISSING STATUS IS A FAILED STATEMENT.

    A mutant defaulting to None makes the next `.get` raise AttributeError, so
    a malformed API response arrives as a stack trace rather than the message
    written for exactly this case.
    """
    monkeypatch.setattr(
        catalog,
        "_cli",
        lambda *args: [{"id": "w1"}] if args[0] == "warehouses" else {},
    )

    with pytest.raises(RuntimeError, match="did not succeed"):
        catalog.execute("SELECT 1")


def test_the_wrapper_invokes_the_pinned_cli_with_its_arguments(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """THE ARGV IS THE ONE THING THIS FUNCTION DOES.

    A mutant replacing the whole list with None survived: the fake accepted any
    call. Recording argv is what makes the wrapper's only behaviour assertable.
    """
    import subprocess

    seen: list[object] = []

    def fake_run(argv: object, **_: object) -> object:
        seen.append(argv)
        return subprocess.CompletedProcess([], 0, "{}", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    catalog._cli("catalogs", "list", "--output", "json")

    assert seen == [["databricks", "catalogs", "list", "--output", "json"]]
