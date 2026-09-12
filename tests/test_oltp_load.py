# tests/test_oltp_load.py
"""The loader renders SQL correctly, and validates before it writes.

    Data Pipeline as Code   the load is a task, not a notebook someone ran once

WHY THIS FILE EXISTS. oltp/load.py scored 0/53: nothing ran it. It is the module
that writes the seed into real tables, and it builds SQL by hand -- so the
escaping, the sequencing column and the validate-before-write order were all
unasserted.

updated_at IS SUPPLIED BY THE LOADER, NOT DEFAULTED IN THE TABLE. AUTO CDC
sequences by it and needs a monotonically increasing value per key; a database
default would make the ordering depend on when the loader ran rather than on
when the change happened.
"""

import pandas as pd
import pytest

from cscie103_olap_oltp.oltp.load import (
    INITIAL_AT,
    LOAD_TIMES,
    _literal,
    batch_for,
    insert_statement,
    load_batch,
    parser,
    run,
)

TARGET = "cat.sch"
AT = pd.Timestamp("2026-01-01")


def test_a_quote_in_a_string_is_doubled_not_dropped() -> None:
    """DOUBLING IS THE SQL STANDARD, and the only escaping this needs.

    An unescaped apostrophe ends the literal early and the rest of the value
    becomes syntax -- so "O'Hara" is a broken statement, not a name.
    """
    assert _literal("O'Hara") == "'O''Hara'"


def test_a_timestamp_is_rendered_as_a_typed_literal() -> None:
    """A BARE STRING WOULD BE COMPARED AS TEXT, and the as-of join orders by
    this column."""
    rendered = _literal(pd.Timestamp("2026-03-01 12:30:00"))

    assert rendered == "TIMESTAMP '2026-03-01 12:30:00'"


def test_numbers_are_not_quoted() -> None:
    """A QUOTED NUMBER IS A STRING, and a string key joins to nothing."""
    assert _literal(100) == "100"
    assert _literal(9.99) == "9.99"


def test_the_statement_names_every_column_including_the_sequence() -> None:
    """NAMED COLUMNS, NOT POSITIONAL. An INSERT that relies on column order
    breaks silently the first time a column is added in the middle."""
    frame = pd.DataFrame({"product_id": [1], "name": ["x"]})

    sql = insert_statement("product", frame, TARGET, AT)

    assert sql.startswith(f"INSERT INTO {TARGET}.product (product_id, name, updated_at) VALUES")


def test_every_row_carries_the_same_load_timestamp() -> None:
    """ONE TIMESTAMP PER LOAD IS WHAT MAKES THE SEQUENCE MEANINGFUL. Rows within
    a load did not change at different times."""
    frame = pd.DataFrame({"id": [1, 2, 3]})

    sql = insert_statement("t", frame, TARGET, AT)

    assert sql.count(f"TIMESTAMP '{AT.isoformat(sep=' ')}'") == 3


def test_multiple_rows_become_one_statement() -> None:
    """ONE STATEMENT, NOT ONE PER ROW: each execute() is a round trip to the
    warehouse, and a per-row loop turns a seed into minutes."""
    frame = pd.DataFrame({"id": [1, 2]})

    sql = insert_statement("t", frame, TARGET, AT)

    assert sql.count("INSERT INTO") == 1
    assert sql.rstrip().endswith(")")


def test_the_initial_load_precedes_the_price_change() -> None:
    """THE CHANGE MUST SUPERSEDE THE FIRST LOAD IN THE SEQUENCE. Equal or later
    timestamps make the ordering undefined, and AUTO CDC versions by sequence
    rather than by value."""
    from cscie103_olap_oltp.oltp.seed import PRICE_CHANGE_AT

    assert INITIAL_AT < PRICE_CHANGE_AT


def test_a_frame_the_contract_rejects_is_never_written() -> None:
    """THE REAL CONTRACT, NOT A SECOND INJECTED SEAM.

    An earlier version injected the validator alongside the executor so a test
    could record the order of two callbacks. That is the over-injection the
    guidance warns about -- a second optional parameter added to observe
    something, which then has to be kept honest itself.

    THE BEHAVIOUR IS WHAT MATTERS: a row the model refuses must never reach a
    table, where it becomes a quarantined metric instead of an error. Asserting
    that nothing was written proves the order without naming it.
    """
    written: list[str] = []
    refused = pd.DataFrame({"product_id": ["not-a-number"]})

    with pytest.raises(Exception, match="product"):
        load_batch({"product": refused}, TARGET, AT, written.append)

    assert written == [], "the contract must refuse the row before anything is written"


def test_the_row_count_reports_what_was_written() -> None:
    """A LOADER THAT PRINTS A COUNT IT DID NOT MEASURE IS A LOADER NOBODY CAN
    CHECK."""
    # THE REAL SEED, NOT AN INVENTED FRAME. A hand-built one-column frame fails
    # the contract -- which is the contract working, and a reminder that
    # load_batch validates before it writes.
    batch = batch_for("initial")
    written = load_batch(batch, TARGET, AT, lambda _: None)

    assert written == sum(len(frame) for frame in batch.values())


def test_an_unknown_mode_is_refused() -> None:
    """A LOADER THAT GUESSES ITS MODE WRITES THE WRONG BATCH, and the wrong
    batch at the wrong timestamp versions the wrong rows.

    argparse REFUSES IT, so the valid modes are listed from `choices` rather
    than restated in a usage string beside them.
    """
    with pytest.raises(SystemExit):
        parser().parse_args(["wat", TARGET])


def test_each_load_writes_at_its_own_timestamp() -> None:
    """THE TIMESTAMP IS THE SEQUENCE, and a mutant setting it to None survived.

    AUTO CDC orders versions by this column. A null sequence makes the order
    undefined, so the "before" and "after" of the price change can arrive in
    either order -- and the dimension records a history that never happened.
    """
    for mode, at in LOAD_TIMES.items():
        issued: list[str] = []
        load_batch(batch_for(mode), TARGET, at, issued.append)

        assert issued, mode
        for statement in issued:
            assert f"TIMESTAMP '{at.isoformat(sep=' ')}'" in statement, mode
            assert "None" not in statement, "a null sequence leaves version order undefined"


def test_each_mode_selects_a_different_batch() -> None:
    """`--change` WAS NEVER EXERCISED -- deleting the whole branch survived.

    It is the load that makes SCD2 mean anything: without a second batch, Type
    2 is indistinguishable from Type 1 and every version test passes against
    data that never changed.
    """
    initial = batch_for("initial")
    change = batch_for("change")

    assert set(change) == {"product"}, "a change feed carries only what changed"
    assert len(change["product"]) < len(initial["product"])


def test_each_mode_writes_at_its_own_time() -> None:
    """THE TWO LOADS MUST NOT SHARE A TIMESTAMP. Equal sequence values make the
    version order undefined for the one product that changes -- which is the
    only thing the whole SCD2 demonstration rests on."""
    from cscie103_olap_oltp.oltp.load import LOAD_TIMES

    assert LOAD_TIMES["initial"] < LOAD_TIMES["change"]
    assert len(set(LOAD_TIMES.values())) == len(LOAD_TIMES)


def test_the_command_core_writes_each_batch_at_its_own_time() -> None:
    """THE CORE, NOT THE COMPOSITION ROOT.

    `main` is a humble adapter -- parse, bind, return -- and the documented
    practice is not to test it. Everything that can be wrong lives in `run`:
    which batch a mode selects, and which timestamp it writes at. Wiring
    `change` to the initial batch versions the wrong rows; wiring it to the
    initial timestamp leaves the version order undefined.
    """
    for mode, at in LOAD_TIMES.items():
        issued: list[str] = []
        written = run(mode, TARGET, issued.append)

        assert issued, mode
        assert written == sum(len(f) for f in batch_for(mode).values())
        for statement in issued:
            assert statement.startswith(f"INSERT INTO {TARGET}.")
            assert f"TIMESTAMP '{at.isoformat(sep=' ')}'" in statement


def test_the_change_run_carries_only_what_changed() -> None:
    """A CHANGE FEED THAT RESENDS EVERYTHING VERSIONS EVERYTHING. AUTO CDC
    versions on the sequence rather than by comparing values, so an unchanged
    row resent with a later timestamp gains a second, identical version."""
    issued: list[str] = []
    run("change", TARGET, issued.append)

    assert len(issued) == 1
    assert issued[0].startswith(f"INSERT INTO {TARGET}.product ")


def test_the_entry_point_wires_the_real_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE COMPOSITION ROOT IS STILL CODE.

    The Humble Object argument says an adapter this thin need not be tested --
    but `main` still parses, binds and returns, and nine mutants survived
    uncovered on that claim. One end-to-end call is cheaper than the argument.
    """
    from cscie103_olap_oltp.oltp import load

    issued: list[str] = []
    monkeypatch.setattr(load, "execute", issued.append)

    assert load.main(["change", TARGET]) == 0
    assert len(issued) == 1
    assert issued[0].startswith(f"INSERT INTO {TARGET}.product ")
