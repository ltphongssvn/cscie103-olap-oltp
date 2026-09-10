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

import json
from pathlib import Path

import pytest

from cscie103_olap_oltp.ledger import (
    GENESIS_HASH,
    LedgerEntry,
    append,
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
