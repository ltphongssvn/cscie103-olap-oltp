# src/cscie103_olap_oltp/olap/ddl.py
"""Render the pipeline's dimension schemas from the contracts.

    Schema as Code      the pandera models are the one source
    Migration as Code   the DDL is generated, never typed

WHY THIS EXISTS. An IDENTITY surrogate key requires a SPECIFIED target schema,
and specifying one replaces the inferred schema entirely -- so the pipeline had
to spell out every column, duplicating the contract. Two descriptions of one
table, with nothing checking agreement, is the drift this repository gates
everywhere else.

A SINGLE SOURCE OF TRUTH STORES EVERY DATA ELEMENT EXACTLY ONCE and generates
the rest. The contract is that source; this renders the pipeline's view of it.

WHY A FILE AND NOT AN IMPORT. `dlt` exists only inside a pipeline runtime, and
the error-code registry's package walk already proved that boundary must be
physical rather than conventional. A committed, gated JSON file crosses it
without either side importing the other.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandera.pandas as pa

from cscie103_olap_oltp.olap.contracts import TABLES, Versioned
from cscie103_olap_oltp.policy.snapshot import REPO_ROOT
from cscie103_olap_oltp.remediation import ActionableError

__all__ = [
    "FRESH_SCHEMAS",
    "SCD2_COLUMNS",
    "SCHEMAS_PATH",
    "STALE_SCHEMAS",
    "UNMAPPED_DTYPE",
    "UNMAPPED_DTYPE_FIX",
    "USAGE",
    "UnmappedDtypeError",
    "generate",
    "is_versioned",
    "matches_models",
    "streaming_table_schema",
]

SCHEMAS_PATH = REPO_ROOT / "contracts" / "olap.streaming-tables.json"

# LAKEFLOW'S VOCABULARY FOR THE VALIDITY INTERVAL.
#
# Databricks requires these in a specified SCD Type 2 target schema, typed like
# the sequencing column. They are the framework's names for what the pandera
# contract calls valid_from and valid_to.
SCD2_COLUMNS = ("__START_AT", "__END_AT")

# THE CONTRACT'S NAMES FOR WHAT THE FRAMEWORK SUPPLIES.
#
# A dimension contract describes the table a READER sees -- valid_from,
# valid_to, is_current. AUTO CDC produces that history itself, so declaring
# those columns in the target would collide with the ones it maintains.
FRAMEWORK_OWNED = frozenset({"valid_from", "valid_to", "is_current"})

# THE SURROGATE KEY SUFFIX. A dimension's key column is <entity>_key by
# convention here, and that convention is what lets the key be identified
# rather than configured per table.
KEY_SUFFIX = "_key"

# EVERY MESSAGE THIS MODULE EMITS, AS A TEMPLATE.
#
# Inline they were adjacent string literals, so each fragment was separately
# mutable: ten mutants corrupted mid-sentence text that no test could kill
# without pinning the wording word for word. As named templates there is one
# literal per message, and a test asserts the fact each carries.
UNMAPPED_DTYPE = "no SQL type mapped for pandera dtype {dtype!r}"

UNMAPPED_DTYPE_FIX = (
    "Add the dtype to SQL_TYPES in olap/ddl.py."
    " Guessing a default would silently render the column as STRING."
)

STALE_SCHEMAS = (
    "{name} no longer matches the dimension contracts.\nFix: mise run contracts:generate"
)

FRESH_SCHEMAS = "{name} matches the dimension contracts"

USAGE = "usage: --generate | --check  (got {given})"


class UnmappedDtypeError(ActionableError):
    """A pandera dtype with no Unity Catalog equivalent.

    A CLASSIFIED ERROR, NOT SystemExit, AND MUTATION TESTING FORCED THE ISSUE.
    The raise was `SystemExit(<message>)`, and a mutant replacing the message
    with None survived -- because SystemExit(None) exits with status ZERO. The
    fail-loud path would have succeeded silently, and the pipeline would read a
    schema missing a column.

    A HARD EXIT FROM A LIBRARY IS THE LAST RESORT ANYWAY. The documented
    practice is to raise, and convert to an exit status in one place at the
    top. This repository already has the mechanism: a code, a remediation and
    structured facts, which SystemExit cannot carry.
    """

    code = "ERR_UNMAPPED_DTYPE"


SQL_TYPES = {
    "int64": "BIGINT",
    "float64": "DOUBLE",
    "bool": "BOOLEAN",
    "string[python]": "STRING",
    "datetime64[ns]": "TIMESTAMP",
}


def _columns(schema: pa.DataFrameSchema) -> list[str]:
    """The declared columns, in contract order, with the key made generated.

    NO TABLE NAME, BECAUSE NOTHING NEEDS ONE ANY MORE. It survived only to
    feed the substring key test that the suffix convention replaced, and then
    to decorate an error message -- so a mutant passing None went unnoticed on
    every successful call. An argument used only on the failure path is a
    parameter the happy path cannot defend.
    """
    rendered: list[str] = []

    for column, spec in schema.columns.items():
        if column in FRAMEWORK_OWNED:
            continue

        dtype = str(spec.dtype)
        if dtype not in SQL_TYPES:
            raise UnmappedDtypeError(
                UNMAPPED_DTYPE.format(dtype=dtype),
                remediation=UNMAPPED_DTYPE_FIX,
                dtype=dtype,
                column=column,
            )

        # THE SUFFIX ALONE IDENTIFIES THE SURROGATE KEY, which is the documented
        # convention: `_id` is the natural key from the source system, `_key`
        # (or `_sk`) is the warehouse-generated one. Distinguishing them by
        # suffix is what prevents the most common join defect.
        #
        # THE EXTRA "STEM APPEARS IN THE TABLE NAME" CHECK IS GONE. It was a
        # substring test dressed as a rule, and it joined two conditions with
        # an `and` that mutation testing flipped to `or` -- under which any
        # column whose name appeared in the table name became an IDENTITY
        # column the loader can never write. One condition cannot be flipped.
        #
        # SAFE BECAUSE ONLY VERSIONED DIMENSIONS REACH HERE: each carries
        # exactly one `_key`, its own. A fact's foreign keys are never
        # generated by this module.
        if column.endswith(KEY_SUFFIX):
            # THE SURROGATE KEY, GENERATED BY THE PIPELINE. Declaring it as an
            # ordinary BIGINT would make the contract's key a column the loader
            # never fills, and every fact join would resolve to the Unknown
            # member without anything reporting an error.
            rendered.append(f"{column} BIGINT GENERATED ALWAYS AS IDENTITY")
            continue

        rendered.append(f"{column} {SQL_TYPES[dtype]}")

    # THE SEQUENCING COLUMN, WHICH THE CONTRACT DELIBERATELY OMITS. It is
    # warehouse plumbing rather than a business fact -- see oltp/tables.py --
    # but AUTO CDC orders changes by it, so the target must hold it.
    rendered.append("updated_at TIMESTAMP")
    rendered.extend(f"{column} TIMESTAMP" for column in SCD2_COLUMNS)

    return rendered


def is_versioned(model: type[pa.DataFrameModel]) -> bool:
    """Whether AUTO CDC maintains this table's history.

    THE MODEL DECLARES IT by inheriting Versioned. Asking the type is a question
    with one answer; sniffing for validity columns infers a capability from its
    symptoms, and a `dim_` prefix infers it from spelling -- which swept in
    dim_date, a conformed calendar with no history because a date never changes.
    """
    return issubclass(model, Versioned)


def generate() -> dict[str, str]:
    """One schema string per VERSIONED dimension.

    FACTS AND CALENDARS ARE ABSENT BY CONSTRUCTION. A fact is the result of a
    query and a calendar has no versions, so neither is a table AUTO CDC
    maintains -- declaring a schema for either would freeze a shape nothing
    will fill.
    """
    return {
        name: ",\n".join(_columns(model.to_schema()))
        for name, model in sorted(TABLES.items())
        if is_versioned(model)
    }


def streaming_table_schema(name: str) -> str:
    """The schema for one dimension. RAISES for anything that is not one."""
    return generate()[name]


def _display(path: Path) -> str:
    """The path as a reader wants to see it, never raising.

    FORMATTING MUST NOT BE ABLE TO FAIL A SUCCEEDED OPERATION. `relative_to`
    raises ValueError for anything outside the repository, so the tidy short
    form was a crash waiting for the first caller with a different path.
    """
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _serialise(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def matches_models() -> bool:
    """Whether the committed file still describes the contracts.

    THE MISSING CASE IS EXPLICIT, BECAUSE `and` WAS FLIPPABLE. A mutant turning
    it into `or` made an ABSENT file report a match -- the drift gate would
    pass on a repository with no generated schemas at all, which is the
    vacuous-gate shape this project keeps removing.
    """
    if not SCHEMAS_PATH.is_file():
        return False

    return SCHEMAS_PATH.read_text() == _serialise(generate())


def main() -> int:
    match sys.argv[1:]:
        case ["--generate"]:
            SCHEMAS_PATH.parent.mkdir(parents=True, exist_ok=True)
            SCHEMAS_PATH.write_text(_serialise(generate()))
            # relative_to RAISES FOR A PATH OUTSIDE THE REPO, and this is the
            # success path -- so a cosmetic shortening could abort a write that
            # had already happened. walk_up keeps the display relative where it
            # can and falls back to the full path rather than failing.
            print(f"wrote {_display(SCHEMAS_PATH)}")
            return 0

        case ["--check"]:
            if not matches_models():
                print(STALE_SCHEMAS.format(name=SCHEMAS_PATH.name), file=sys.stderr)
                return 1
            print(FRESH_SCHEMAS.format(name=SCHEMAS_PATH.name))
            return 0

        case other:
            # A MESSAGE, NOT None. SystemExit(None) exits with status ZERO, so a
            # mutant blanking this turned an unrecognised flag into success --
            # the task would report done having generated nothing.
            raise SystemExit(USAGE.format(given=other))


if __name__ == "__main__":
    raise SystemExit(main())
