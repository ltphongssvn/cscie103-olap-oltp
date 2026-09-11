# src/cscie103_olap_oltp/ledger.py
"""An append-only, hash-chained record of what this platform decided.

THE PILLAR THIS COMPLETES. The repository already emits Evidence, Verdicts and
Decisions as data -- but each run wrote its own file, so history was a directory
of unrelated snapshots. "The platform preserves all of them as data" requires an
AUDIT TRAIL: an ordered record in which alteration is detectable.

Each run appends one JSON line carrying the hash of the previous line. Editing,
reordering or deleting a record mid-file breaks the chain there and at every
point after it, so alteration is detectable by recomputation.

WHAT THIS IS NOT, STATED PLAINLY BECAUSE THE DISTINCTION IS USUALLY ELIDED.
Tamper-EVIDENT, not tamper-PROOF. A local file is inherently mutable. The chain
detects in-place edits, reorders and mid-chain deletions. It does NOT detect:

  TAIL TRUNCATION   deleting the last N records leaves a shorter valid chain
  GENESIS REWRITE   discarding the file and starting again verifies cleanly

Both are closed the same way and neither is done here: anchoring the chain head
somewhere the writer cannot reach -- an off-machine log, a transparency log, or
a timestamp authority.

AND IT IS NOT COMPLIANCE EVIDENCE. Saying so would be false. An auditor asks how
you know the log was not modified, and "the chain verifies" answers that only
for records still present, written by a process the same user controls. This is
a local integrity aid; calling it compliance would be the kind of claim this
repository exists to avoid making.

WHO RAN IT: AN OPAQUE ID, NOT A USERNAME. Recording the actor is standard
practice, but a hostname is identifying data -- this machine's contains a
person's name -- and the repository runs a PII scanner to keep exactly that out
of artifacts. A random identifier generated once and stored locally
distinguishes machines without naming anyone.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import IO, Any

from pydantic import BaseModel, ConfigDict, Field

from cscie103_olap_oltp.policy.snapshot import REPO_ROOT
from cscie103_olap_oltp.remediation import ActionableError

__all__ = [
    "DEFAULT_LOCK_TIMEOUT_SECONDS",
    "GENESIS_HASH",
    "LedgerEntry",
    "LedgerLockError",
    "LedgerVerification",
    "append",
    "deadline_reached",
    "machine_id",
    "verify_chain",
]

# The previous-hash of the first record. A fixed, recognisable value rather than
# an empty string, so a genesis record is distinguishable from one whose
# previous hash failed to write.
GENESIS_HASH = "0" * 64

LEDGER_PATH = REPO_ROOT / ".artifacts" / "ledger.jsonl"

MACHINE_ID_PATH = Path.home() / ".config" / "cscie103-olap-oltp" / "machine-id"

# HOW LONG A RUN WAITS FOR THE LOCK, AND HOW OFTEN IT ASKS.
#
# NAMED BECAUSE A LITERAL DEFAULT HAD NO SEAM. Mutation testing changed
# `timeout_seconds: float = 5.0` to 6.0 and nothing noticed: no caller relied on
# the default, and reading it back through inspect.signature returns the
# wrapper's signature rather than the function's. A module constant is a value a
# test can assert directly.
#
# IT IS ALSO THE OPERATOR CONTRACT. The lock error tells someone to "retry; if
# it persists, a crashed process may have left the lock behind" -- how long
# "persists" means is this number.
DEFAULT_LOCK_TIMEOUT_SECONDS = 5.0

# THE POLL INTERVAL. Short enough that the deadline is honoured closely, long
# enough that contention does not spin a core.
LOCK_POLL_SECONDS = 0.05


class LedgerLockError(ActionableError):
    """The ledger could not be locked, so nothing was written.

    FAIL CLOSED. Appending without the lock risks forking the chain, and a
    forked chain reports tampering that never happened -- which trains people to
    ignore the one signal this file exists to give.

    ActionableError, NOT RuntimeError, AND THE DISTINCTION IS LOAD-BEARING. The
    sibling project made this exact class a RuntimeError and its classification
    guard never saw it, because that guard walked ActionableError subclasses --
    so a new unclassified error slipped past the check built to prevent it.
    """

    code = "ERR_LEDGER_LOCKED"


def machine_id() -> str:
    """A stable, opaque identifier for this machine.

    Generated once and stored, so two runs on one laptop share it and two
    laptops differ. It says nothing about who owns the machine, which is the
    whole point -- see the module docstring on why a hostname is unusable.
    """
    if MACHINE_ID_PATH.is_file():
        # NO EXPLICIT ENCODING, for the reason compute_hash states: the
        # argument is redundant and its only effect was to generate an
        # unkillable "UTF-8" mutant.
        existing = MACHINE_ID_PATH.read_text().strip()
        if existing:
            return existing

    generated = str(uuid.uuid4())
    MACHINE_ID_PATH.parent.mkdir(parents=True, exist_ok=True)
    MACHINE_ID_PATH.write_text(generated + "\n")
    return generated


class LedgerEntry(BaseModel):
    """One record. The hash covers the payload AND the previous hash."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    sequence: int = Field(description="Position in the chain, from zero")
    previous_hash: str
    entry_hash: str
    machine: str
    payload: dict[str, Any]

    @staticmethod
    def compute_hash(
        sequence: int,
        previous_hash: str,
        machine: str,
        payload: dict[str, Any],
    ) -> str:
        """SHA-256 over a canonical serialisation.

        sort_keys AND separators ARE LOAD-BEARING: without them the same payload
        serialises differently between runs, and the chain fails to verify for a
        reason that looks exactly like tampering.
        """
        material = json.dumps(
            {
                "sequence": sequence,
                "previous_hash": previous_hash,
                "machine": machine,
                "payload": payload,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        # NO EXPLICIT ENCODING, AND THAT IS A MUTATION-TESTING RESULT.
        #
        # `.encode("utf-8")` left a mutant that changed it to "UTF-8" -- the
        # same codec, so no test could ever kill it. An equivalent mutant is
        # normally written off as undecidable noise, but this one existed only
        # because the argument was redundant: str.encode defaults to UTF-8.
        #
        # DELETING THE ARGUMENT DELETES THE MUTANT. That is the difference
        # between tolerating an unkillable survivor and removing the reason it
        # could be generated.
        return hashlib.sha256(material.encode()).hexdigest()


class LedgerVerification(BaseModel):
    """The result of walking the chain."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    entries: int
    intact: bool
    broken_at: int | None = Field(
        default=None, description="Sequence of the first record that fails"
    )
    detail: str = ""


def _read_entries(path: Path) -> list[LedgerEntry]:
    if not path.is_file():
        return []
    return [
        LedgerEntry.model_validate_json(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def deadline_reached(now: float, deadline: float) -> bool:
    """Whether the wait is over. EXTRACTED SO THE BOUNDARY IS TESTABLE.

    `>=`, NOT `>`, AND THE DIFFERENCE IS ONE POLL. Inline, the comparison had no
    seam: a mutant loosening it survived because the only observable effect was
    an extra 50ms before the same error, which no test could assert without
    becoming timing-sensitive -- and timing-sensitive tests are how mutation
    testing turns flake into false survivors.

    AS A PURE FUNCTION IT IS ASSERTABLE AT THE EXACT BOUNDARY, with no clock.
    """
    return now >= deadline


def _acquire_exclusive(handle: IO[str], lock_path: Path, timeout_seconds: float) -> None:
    """Take the lock, or refuse to write.

    POLLS RATHER THAN BLOCKING INDEFINITELY: an unattended gate that hangs
    forever on a stale lock is its own outage.
    """
    deadline = time.monotonic() + timeout_seconds

    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError:
            if deadline_reached(time.monotonic(), deadline):
                raise LedgerLockError(
                    "could not acquire the ledger lock",
                    remediation=(
                        "Another run holds the lock. Retry; if it persists, a "
                        "crashed process may have left the lock file behind, and "
                        "removing it is safe once no run is in flight."
                    ),
                    lock_path=str(lock_path),
                    timeout_seconds=timeout_seconds,
                ) from None
            time.sleep(LOCK_POLL_SECONDS)


def append(
    payload: dict[str, Any],
    path: Path = LEDGER_PATH,
    *,
    timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> LedgerEntry:
    """Append one record, chained to the last, under an exclusive lock.

    THE UNLOCKED VERSION HAS A REAL RACE, AND THE SIBLING PROJECT SHIPPED IT.
    Read the tail, compute sequence and previous_hash from it, then append --
    three steps with no lock. Two concurrent runs both read the same tail, both
    write sequence N, and THE CHAIN FORKS. verify_chain then reports tampering
    that never happened, and a false alarm from a tamper-evident log is worse
    than none because it teaches people to ignore it.

    THE LOCK SPANS THE WHOLE READ-COMPUTE-WRITE, not just the write. Locking
    only the append would leave both writers holding the same stale tail.

    A SIDECAR LOCK FILE, so lock state never touches the ledger bytes.

    NO EXPLICIT encoding ON EITHER open(). It is the platform default, so the
    argument changed nothing and generated two unkillable mutants -- "UTF-8"
    and None both behave identically. Removing the redundancy removes the
    mutants rather than tolerating them as equivalent.

    LIMITS, STATED: flock is ADVISORY, so a process that ignores it can still
    corrupt the file, and it is silently ignored on NFS. This guards concurrent
    runs of THIS program on a local filesystem, which is the case that exists.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")

    with lock_path.open("w") as lock_handle:
        _acquire_exclusive(lock_handle, lock_path, timeout_seconds)

        try:
            existing = _read_entries(path)
            sequence = len(existing)
            previous_hash = existing[-1].entry_hash if existing else GENESIS_HASH
            machine = machine_id()

            entry = LedgerEntry(
                sequence=sequence,
                previous_hash=previous_hash,
                entry_hash=LedgerEntry.compute_hash(sequence, previous_hash, machine, payload),
                machine=machine,
                payload=payload,
            )

            with path.open("a") as handle:
                handle.write(entry.model_dump_json() + "\n")
                # FLUSHED AND FSYNCED INSIDE THE LOCK. Releasing before the
                # bytes reach disk would let the next writer read a tail that is
                # still sitting in a buffer.
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    return entry


def verify_chain(path: Path = LEDGER_PATH) -> LedgerVerification:
    """Walk the chain and report the FIRST break.

    REPORTING THE FIRST BREAK RATHER THAN A COUNT IS DELIBERATE: every record
    after an alteration also fails, so a count describes the length of the tail
    rather than the location of the problem.
    """
    entries = _read_entries(path)
    if not entries:
        return LedgerVerification(entries=0, intact=True, detail="empty ledger")

    previous_hash = GENESIS_HASH
    for position, entry in enumerate(entries):
        if entry.sequence != position:
            return LedgerVerification(
                entries=len(entries),
                intact=False,
                broken_at=position,
                detail=f"sequence {entry.sequence} found at position {position}",
            )

        if entry.previous_hash != previous_hash:
            return LedgerVerification(
                entries=len(entries),
                intact=False,
                broken_at=position,
                detail="previous_hash does not match the preceding record",
            )

        expected = LedgerEntry.compute_hash(
            entry.sequence, entry.previous_hash, entry.machine, entry.payload
        )
        if expected != entry.entry_hash:
            return LedgerVerification(
                entries=len(entries),
                intact=False,
                broken_at=position,
                detail="the record's contents do not match its hash",
            )

        previous_hash = entry.entry_hash

    return LedgerVerification(
        entries=len(entries),
        intact=True,
        detail=(
            "chain verifies. NOTE: this cannot detect tail truncation or a "
            "genesis rewrite -- see the module docstring."
        ),
    )


def main() -> int:
    """Verify the chain, and report what that does and does not prove.

    FAIL CLOSED ON A BROKEN CHAIN. A detected alteration is the one event this
    file exists to surface, so it must stop the gate rather than print a note.
    """
    result = verify_chain()

    if not result.intact:
        print(
            f"LEDGER BROKEN at record {result.broken_at}: {result.detail}",
            file=sys.stderr,
        )
        return 1

    print(f"ledger intact: {result.entries} record(s)")
    print(f"  {result.detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
