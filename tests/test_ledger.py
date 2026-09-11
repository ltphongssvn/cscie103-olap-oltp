# tests/test_ledger.py
"""An append-only, hash-chained record of what this platform decided.

THE PILLAR THIS COMPLETES. The repository already emits Evidence, Verdicts and
Decisions as data -- but each run overwrote its own file, so history was a
directory of unrelated snapshots. "The platform preserves all of them as data"
requires an AUDIT TRAIL: an ordered record where alteration is detectable.

Each run appends one JSON line carrying the hash of the previous line. Editing,
reordering or deleting a record mid-file breaks the chain at that point and at
every point after it, so alteration is detectable by recomputation.

TAMPER-EVIDENT, NOT TAMPER-PROOF, AND THE DIFFERENCE IS TESTED BELOW. A local
file is inherently mutable. The chain detects in-place edits, reorders and
mid-chain deletions. It does NOT detect tail truncation -- deleting the last N
records leaves a shorter valid chain -- nor a genesis rewrite, where the file is
discarded and started again. Both are closed only by anchoring the head
somewhere the writer cannot reach, and neither is done here.

Claiming this is compliance evidence would be false, and saying so plainly is
the point: an auditor asks how you know the log was not modified, and "the chain
verifies" answers that only for records still present, written by a process the
same user controls.
"""

import fcntl
import hashlib
import json
from pathlib import Path

import pytest

from cscie103_olap_oltp.ledger import (
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    GENESIS_HASH,
    LOCK_POLL_SECONDS,
    LedgerEntry,
    append,
    deadline_reached,
    machine_id,
    verify_chain,
)


def test_the_first_record_chains_to_genesis(tmp_path: Path) -> None:
    """A FIXED, RECOGNISABLE VALUE rather than an empty string.

    An empty previous_hash cannot be told apart from one that failed to write.
    """
    entry = append({"rule": "first"}, path=tmp_path / "ledger.jsonl")
    assert entry.previous_hash == GENESIS_HASH
    assert entry.sequence == 0


def test_each_record_chains_to_the_last(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    first = append({"rule": "a"}, path=ledger)
    second = append({"rule": "b"}, path=ledger)

    assert second.sequence == 1
    assert second.previous_hash == first.entry_hash


def test_an_intact_chain_verifies(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    for index in range(5):
        append({"rule": index}, path=ledger)

    result = verify_chain(ledger)
    assert result.intact
    assert result.entries == 5


def test_an_edited_payload_breaks_the_chain(tmp_path: Path) -> None:
    """THE PROPERTY THE WHOLE FILE EXISTS FOR.

    Changing a recorded verdict after the fact must be detectable, or the
    ledger is decoration.
    """
    ledger = tmp_path / "ledger.jsonl"
    for index in range(3):
        append({"verdict": "fail", "rule": index}, path=ledger)

    lines = ledger.read_text().splitlines()
    tampered = json.loads(lines[1])
    tampered["payload"]["verdict"] = "pass"
    lines[1] = json.dumps(tampered)
    ledger.write_text("\n".join(lines) + "\n")

    result = verify_chain(ledger)
    assert not result.intact
    assert result.broken_at == 1


def test_a_reordered_chain_is_detected(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    for index in range(3):
        append({"rule": index}, path=ledger)

    lines = ledger.read_text().splitlines()
    lines[0], lines[1] = lines[1], lines[0]
    ledger.write_text("\n".join(lines) + "\n")

    assert not verify_chain(ledger).intact


def test_a_deleted_middle_record_is_detected(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    for index in range(4):
        append({"rule": index}, path=ledger)

    lines = ledger.read_text().splitlines()
    del lines[1]
    ledger.write_text("\n".join(lines) + "\n")

    assert not verify_chain(ledger).intact


def test_the_first_break_is_reported_not_a_count(tmp_path: Path) -> None:
    """EVERY RECORD AFTER AN ALTERATION ALSO FAILS.

    A count would describe the length of the tail rather than the location of
    the problem, which is the thing an operator needs.
    """
    ledger = tmp_path / "ledger.jsonl"
    for index in range(6):
        append({"rule": index}, path=ledger)

    lines = ledger.read_text().splitlines()
    tampered = json.loads(lines[2])
    tampered["payload"]["rule"] = "changed"
    lines[2] = json.dumps(tampered)
    ledger.write_text("\n".join(lines) + "\n")

    assert verify_chain(ledger).broken_at == 2


def test_tail_truncation_is_not_detected_and_says_so(tmp_path: Path) -> None:
    """THE HONEST LIMIT, ASSERTED SO IT CANNOT BE QUIETLY OVERCLAIMED.

    Deleting the last records leaves a shorter valid chain. Only anchoring the
    head off-machine closes this, and that is not done here -- so the detail
    string must keep saying so.
    """
    ledger = tmp_path / "ledger.jsonl"
    for index in range(5):
        append({"rule": index}, path=ledger)

    lines = ledger.read_text().splitlines()[:3]
    ledger.write_text("\n".join(lines) + "\n")

    result = verify_chain(ledger)
    assert result.intact
    assert "truncation" in result.detail


def test_an_empty_ledger_is_intact(tmp_path: Path) -> None:
    """Nothing recorded is not a broken chain. A fresh clone has no history and
    must not report tampering."""
    result = verify_chain(tmp_path / "absent.jsonl")
    assert result.intact
    assert result.entries == 0


def test_the_hash_covers_the_previous_hash(tmp_path: Path) -> None:
    """WITHOUT THIS THERE IS NO CHAIN, only a list of independently hashed rows
    that can be reordered freely."""
    payload = {"rule": "x"}
    one = LedgerEntry.compute_hash(0, GENESIS_HASH, "m", payload)
    two = LedgerEntry.compute_hash(0, "f" * 64, "m", payload)
    assert one != two


def test_the_hash_is_stable_across_key_order(tmp_path: Path) -> None:
    """CANONICAL SERIALISATION IS LOAD-BEARING.

    Without sorted keys the same payload hashes differently between runs, and
    the chain fails to verify for a reason that looks exactly like tampering.
    """
    left = LedgerEntry.compute_hash(0, GENESIS_HASH, "m", {"a": 1, "b": 2})
    right = LedgerEntry.compute_hash(0, GENESIS_HASH, "m", {"b": 2, "a": 1})
    assert left == right


def test_the_machine_id_is_opaque() -> None:
    """OWASP ASKS FOR THE ACTOR; A HOSTNAME IS PERSONAL DATA.

    This machine's hostname contains a person's name, and the repository runs a
    PII scanner to keep exactly that out of artifacts. A random identifier
    distinguishes machines without naming anyone.
    """
    identifier = machine_id()
    assert identifier
    assert "MacBook" not in identifier
    assert "@" not in identifier


def test_concurrent_appends_do_not_fork_the_chain(tmp_path: Path) -> None:
    """THE RACE THE SIBLING PROJECT SHIPPED AND THEN FIXED.

    Read-tail, compute-sequence, append is three steps. Two runs -- a local gate
    and a hook firing together -- can both read the same tail and both write
    sequence N, forking the chain. verify_chain would then report tampering that
    never happened, which teaches people to ignore the one signal it exists to
    give.
    """
    from concurrent.futures import ThreadPoolExecutor

    ledger = tmp_path / "ledger.jsonl"
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda index: append({"rule": index}, path=ledger), range(24)))

    result = verify_chain(ledger)
    assert result.intact, result.detail
    assert result.entries == 24


def test_a_record_that_cannot_be_locked_is_not_written(tmp_path: Path) -> None:
    """FAIL CLOSED ON CONTENTION.

    A forked chain is a permanent, silent lie about history, so a record that
    cannot be written safely is not written at all.
    """
    from cscie103_olap_oltp.ledger import LedgerLockError

    ledger = tmp_path / "ledger.jsonl"
    append({"rule": "first"}, path=ledger)

    lock = ledger.with_suffix(ledger.suffix + ".lock")
    import fcntl

    with lock.open("w", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        with pytest.raises(LedgerLockError):
            append({"rule": "blocked"}, path=ledger, timeout_seconds=0.2)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    assert verify_chain(ledger).entries == 1


def test_the_hash_is_over_a_canonical_serialisation() -> None:
    """MUTATION TESTING FOUND THIS, AND NOTHING ELSE WOULD HAVE.

    Removing `separators=(",", ":")` changes the bytes hashed -- json.dumps then
    emits ", " and ": " -- so every hash in the chain differs. The mutant
    SURVIVED: thirteen ledger tests exercised the code and not one noticed the
    chain's identity had changed.

    THAT IS THE WHOLE INTEGRITY PROPERTY. A chain is tamper-evident only if the
    same entry always hashes the same way. A serialisation that varies by
    separator, key order or Python version makes verification a coin flip, and
    the ledger would report a break for a file nobody touched.

    A KNOWN-ANSWER TEST, DELIBERATELY. Recomputing with compute_hash itself
    would pass under the mutant, because both sides would change together. Only
    a pinned digest can fail.
    """
    expected = hashlib.sha256(
        b'{"machine":"m","payload":{"a":1,"b":2},'
        b'"previous_hash":"' + GENESIS_HASH.encode() + b'","sequence":0}'
    ).hexdigest()

    assert (
        LedgerEntry.compute_hash(
            sequence=0,
            previous_hash=GENESIS_HASH,
            machine="m",
            payload={"b": 2, "a": 1},
        )
        == expected
    )


def test_the_hash_ignores_the_order_keys_were_added() -> None:
    """THE PROPERTY sort_keys EXISTS FOR. Two payloads differing only in
    construction order must hash identically, or a record's identity depends on
    how a dict happened to be built."""
    first = LedgerEntry.compute_hash(
        sequence=1, previous_hash=GENESIS_HASH, machine="m", payload={"alpha": 1, "beta": 2}
    )
    second = LedgerEntry.compute_hash(
        sequence=1, previous_hash=GENESIS_HASH, machine="m", payload={"beta": 2, "alpha": 1}
    )

    assert first == second


def test_an_empty_ledger_says_so() -> None:
    """THE DETAIL IS THE DIAGNOSIS, AND IT WAS UNASSERTED.

    Mutants that blanked or corrupted this string all survived: fourteen tests
    checked `intact` and none checked what the ledger SAID. "intact" with no
    explanation is indistinguishable from "verified nothing", which is exactly
    the state an empty file is in.
    """
    verification = verify_chain(Path("/nonexistent/ledger.jsonl"))

    assert verification.intact
    assert verification.entries == 0
    assert verification.detail == "empty ledger"


def test_a_broken_chain_names_which_check_failed(tmp_path: Path) -> None:
    """THREE DIFFERENT BREAKS, THREE DIFFERENT MESSAGES.

    A tamper-evident log that reports "broken" without saying HOW sends the
    reader to diff the file by hand. Each branch's detail survived mutation
    because nothing distinguished them.
    """
    path = tmp_path / "ledger.jsonl"
    append({"a": 1}, path)
    append({"b": 2}, path)

    entries = [json.loads(line) for line in path.read_text().splitlines()]

    # A RECORD WHOSE CONTENTS NO LONGER MATCH ITS HASH.
    tampered = list(entries)
    tampered[1]["payload"] = {"b": 99}
    path.write_text("\n".join(json.dumps(e) for e in tampered) + "\n")

    verification = verify_chain(path)
    assert not verification.intact
    assert verification.broken_at == 1
    assert verification.detail == "the record's contents do not match its hash"


def test_a_reordered_chain_reports_the_sequence(tmp_path: Path) -> None:
    """REORDERING IS THE BREAK A HASH ALONE DOES NOT CATCH: each record still
    matches its own hash, and only the position is wrong."""
    path = tmp_path / "ledger.jsonl"
    append({"a": 1}, path)
    append({"b": 2}, path)

    entries = [json.loads(line) for line in path.read_text().splitlines()]
    path.write_text("\n".join(json.dumps(e) for e in reversed(entries)) + "\n")

    verification = verify_chain(path)
    assert not verification.intact
    assert verification.broken_at == 0
    assert verification.detail == "sequence 1 found at position 0"


def test_an_intact_chain_states_what_it_cannot_prove(tmp_path: Path) -> None:
    """THE LIMITATION TRAVELS WITH THE VERDICT.

    The module docstring says this cannot detect tail truncation or a genesis
    rewrite. That caveat is worthless if it lives only in a docstring, so it is
    in the success detail -- and therefore must be asserted.
    """
    path = tmp_path / "ledger.jsonl"
    append({"a": 1}, path)

    verification = verify_chain(path)

    assert verification.intact
    assert "tail truncation" in verification.detail
    assert "genesis rewrite" in verification.detail


def test_a_broken_verification_still_counts_the_records(tmp_path: Path) -> None:
    """THE COUNT MATTERS MOST WHEN THE CHAIN IS BROKEN, and it was unasserted.

    Mutants blanking `entries` on every failure branch survived: the tests
    checked `intact` and `broken_at` and never the total. Without it the reader
    cannot tell whether record 1 of 2 failed or record 1 of 10,000 -- which is
    the difference between "one bad write" and "the file is destroyed".
    """
    path = tmp_path / "ledger.jsonl"
    for index in range(3):
        append({"n": index}, path)

    entries = [json.loads(line) for line in path.read_text().splitlines()]
    entries[1]["previous_hash"] = GENESIS_HASH
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

    verification = verify_chain(path)

    assert not verification.intact
    assert verification.entries == 3
    assert verification.broken_at == 1
    assert verification.detail == "previous_hash does not match the preceding record"


def test_each_machine_gets_a_distinct_identifier(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """THE IDENTIFIER MUST ACTUALLY IDENTIFY.

    A mutant replacing uuid4() with a constant survived: nothing asserted that
    two machines differ. An id shared by every machine is not an id -- the
    ledger would attribute every record to the same actor while looking
    correct, which is worse than recording nothing.
    """
    from cscie103_olap_oltp import ledger

    first_home = tmp_path / "one" / "machine-id"
    monkeypatch.setattr(ledger, "MACHINE_ID_PATH", first_home)
    first = ledger.machine_id()

    second_home = tmp_path / "two" / "machine-id"
    monkeypatch.setattr(ledger, "MACHINE_ID_PATH", second_home)
    second = ledger.machine_id()

    assert first != second, "every machine would share one identifier"
    assert len(first) == 36, "not a UUID"


def test_the_identifier_is_stable_for_one_machine(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """AND IT MUST NOT CHANGE BETWEEN RUNS, or two records from one laptop look
    like two different machines and the audit trail becomes unreadable."""
    from cscie103_olap_oltp import ledger

    monkeypatch.setattr(ledger, "MACHINE_ID_PATH", tmp_path / "machine-id")

    assert ledger.machine_id() == ledger.machine_id()


def test_the_intact_detail_is_the_exact_caveat(tmp_path: Path) -> None:
    """SUBSTRING CHECKS LET MUTANTS THROUGH.

    An earlier test asserted "tail truncation" appeared somewhere in the detail.
    A mutant that corrupted the surrounding words survived it -- the caveat was
    still findable but no longer readable. This is the sentence a reader is
    given when the ledger says "intact", so the whole sentence is the contract.
    """
    path = tmp_path / "ledger.jsonl"
    append({"a": 1}, path)

    assert verify_chain(path).detail == (
        "chain verifies. NOTE: this cannot detect tail truncation or a "
        "genesis rewrite -- see the module docstring."
    )


def test_the_machine_id_directory_is_created_recursively(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """parents=True IS LOAD-BEARING ON A FRESH MACHINE.

    ~/.config may not exist. Without it, mkdir raises FileNotFoundError on the
    first run of a new laptop -- the exact moment the identifier is needed --
    and the mutant that removed it survived because every test happened to run
    where the parent already existed.
    """
    from cscie103_olap_oltp import ledger

    nested = tmp_path / "config" / "cscie103-olap-oltp" / "machine-id"
    monkeypatch.setattr(ledger, "MACHINE_ID_PATH", nested)

    assert ledger.machine_id()
    assert nested.is_file()


def test_the_lock_is_a_sidecar_with_a_predictable_name(tmp_path: Path) -> None:
    """THE LOCK PATH IS AN OPERATIONAL CONTRACT.

    The remediation tells an operator a crashed process may have left the lock
    behind and that removing it is safe. That instruction is unfollowable if
    the file is not where the message says -- and a mutant renaming it to
    `.LOCK` survived.
    """
    path = tmp_path / "ledger.jsonl"
    append({"a": 1}, path)

    assert (tmp_path / "ledger.jsonl.lock").is_file()


def test_the_lock_error_names_the_file_and_the_timeout(tmp_path: Path) -> None:
    """THE CONTEXT IS THE REMEDIATION'S EVIDENCE.

    "Remove the lock file" is useless without saying which file; "retry" is
    useless without saying how long it already waited. Mutants dropping either
    field survived, because nothing read the structured context.
    """
    import pytest

    from cscie103_olap_oltp.ledger import LedgerLockError

    path = tmp_path / "ledger.jsonl"
    lock_path = tmp_path / "ledger.jsonl.lock"
    path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("w", encoding="utf-8") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        with pytest.raises(LedgerLockError) as caught:
            append({"a": 1}, path, timeout_seconds=0.1)

    assert caught.value.context["lock_path"] == str(lock_path)
    assert caught.value.context["timeout_seconds"] == 0.1


def test_the_lock_error_states_what_happened_and_what_to_do(tmp_path: Path) -> None:
    """THE MESSAGE AND THE REMEDIATION ARE BOTH CONTRACTS.

    Mutants blanking the message, or corrupting the remediation's wording,
    survived: the existing test read the structured context and never the prose.
    An ActionableError whose message is None and whose fix is unreadable has
    lost the only thing distinguishing it from RuntimeError.
    """
    import pytest

    from cscie103_olap_oltp.ledger import LedgerLockError

    path = tmp_path / "ledger.jsonl"
    lock_path = tmp_path / "ledger.jsonl.lock"

    with lock_path.open("w") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        with pytest.raises(LedgerLockError) as caught:
            append({"a": 1}, path, timeout_seconds=0.1)

    error = caught.value
    assert error.args[0] == "could not acquire the ledger lock"
    assert error.remediation == (
        "Another run holds the lock. Retry; if it persists, a "
        "crashed process may have left the lock file behind, and "
        "removing it is safe once no run is in flight."
    )
    assert error.code == "ERR_LEDGER_LOCKED"


def test_the_ledger_directory_is_created_recursively(tmp_path: Path) -> None:
    """parents=True IS LOAD-BEARING ON A FRESH CHECKOUT.

    The default path is .artifacts/ledger.jsonl, and .artifacts does not exist
    in a new clone. Three mutants weakening this survived because every other
    test wrote into a tmp_path whose parent already existed -- so the one
    condition that matters, a directory that is not there yet, was never tried.
    """
    nested = tmp_path / ".artifacts" / "nested" / "ledger.jsonl"

    entry = append({"a": 1}, nested)

    assert nested.is_file()
    assert entry.sequence == 0


def test_the_deadline_is_reached_at_the_deadline() -> None:
    """THE BOUNDARY ITSELF, ASSERTED WITHOUT A CLOCK.

    `>=` means the wait ends AT the deadline; `>` would poll once more. Inline
    that difference was unobservable, so the mutant survived. As a pure
    predicate the boundary case is a direct assertion -- and `now == deadline`
    is precisely the input that separates the two.
    """
    assert deadline_reached(now=10.0, deadline=10.0), "must stop AT the deadline"
    assert deadline_reached(now=10.1, deadline=10.0)
    assert not deadline_reached(now=9.9, deadline=10.0)


def test_the_default_lock_timeout_is_five_seconds() -> None:
    """THE DEFAULT IS AN OPERATOR CONTRACT, and the remediation depends on it.

    "Retry; if it persists..." is meaningless without saying how long a run
    already waited. A mutant changing it survived while the value was an inline
    literal with no caller; as a named constant it is assertable.
    """
    assert DEFAULT_LOCK_TIMEOUT_SECONDS == 5.0
    assert LOCK_POLL_SECONDS < DEFAULT_LOCK_TIMEOUT_SECONDS


def test_the_cli_reports_an_intact_chain(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    """main() HAD NO TEST AT ALL -- ten mutants marked "no coverage".

    An uncovered function cannot be killed regardless of assertion quality, so
    the gate that runs this entry point was itself unverified: `mise run check`
    calls it, and nothing proved it returns 0 on a good chain or prints
    anything a reader could act on.
    """
    from cscie103_olap_oltp import ledger

    path = tmp_path / "ledger.jsonl"
    append({"a": 1}, path)

    # PATCHING LEDGER_PATH DOES NOTHING, WHICH COST A CYCLE TO LEARN.
    # verify_chain binds it as a DEFAULT ARGUMENT, evaluated once at import, so
    # rebinding the module attribute leaves the already-captured default in
    # place -- main() read the real ledger and reported 40 records.
    #
    # The seam is the call main() actually makes.
    monkeypatch.setattr(ledger, "verify_chain", lambda *_: verify_chain(path))

    assert ledger.main() == 0

    out = capsys.readouterr().out
    assert "ledger intact: 1 record(s)" in out
    assert "tail truncation" in out, "the caveat must travel with the success"


def test_the_cli_fails_closed_on_a_broken_chain(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    """A DETECTED ALTERATION MUST STOP THE GATE, not print a note.

    This is the one event the ledger exists to surface. Returning 0 here would
    make the gate decoration -- the chain would be reported broken in a log
    nobody reads while the build went green.
    """
    from cscie103_olap_oltp import ledger

    path = tmp_path / "ledger.jsonl"
    append({"a": 1}, path)
    append({"b": 2}, path)

    entries = [json.loads(line) for line in path.read_text().splitlines()]
    entries[1]["payload"] = {"b": 99}
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

    monkeypatch.setattr(ledger, "verify_chain", lambda *_: verify_chain(path))

    assert ledger.main() == 1

    err = capsys.readouterr().err
    assert "LEDGER BROKEN at record 1" in err
    assert "the record's contents do not match its hash" in err
