# src/cscie103_olap_oltp/contracts/publish.py
"""Publish both models as Open Data Contract Standard documents.

    Data Contract as Code   producer and consumer bind to one reviewable file
    Contracts as Data       a catalog reads it without importing our code

WHY ODCS RATHER THAN A SHAPE OF OUR OWN. The first version of this module
published a bespoke JSON envelope around pandera's serialisation. It was derived
and gated, so it satisfied this repository's rules -- and was still wrong. ODCS
v3.1.0 shipped under the Linux Foundation's Bitol project, the competing
specification was deprecated in the same window, and catalogs added native
support. A private format is one no tool in the ecosystem can read.

WHY pyodcs RATHER THAN A VENDORED SCHEMA. Copying the official JSON Schema into
contracts/ would be a second copy of something upstream owns, going stale in
silence. pyodcs is the reference implementation and validates without a data
platform, so the specification stays where it is maintained.

THE TYPE MAPPING IS WRITTEN HERE BECAUSE NOTHING OFFERS IT. datacontract-cli
imports from SQL, dbt, BigQuery and Glue but not from pandera; pandera-forge
runs the other way, generating models FROM dataframes. Rather than pretend a
converter exists, the translation is a DECLARED TABLE that refuses an unmapped
dtype -- data with a fail-closed lookup, not logic with a default.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandera.pandas as pa
import yaml

from cscie103_olap_oltp.olap.contracts import TABLES as OLAP_TABLES
from cscie103_olap_oltp.oltp.contracts import TABLES as OLTP_TABLES
from cscie103_olap_oltp.policy.snapshot import REPO_ROOT

__all__ = ["API_VERSION", "MODELS", "generate", "matches_models"]

# .odcs.yaml IS AUTO-DETECTED BY SchemaStore, so an editor validates these
# against the official schema with no local configuration at all.
MODELS: dict[str, tuple[Path, dict[str, type[pa.DataFrameModel]]]] = {
    "oltp": (REPO_ROOT / "contracts" / "oltp.odcs.yaml", OLTP_TABLES),
    "olap": (REPO_ROOT / "contracts" / "olap.odcs.yaml", OLAP_TABLES),
}

# PINNED, NOT FLOATING. A contract whose apiVersion moved on its own would
# change meaning for every consumer without a single reviewable edit.
API_VERSION = "v3.1.0"

# THE TRANSLATION TABLE BETWEEN TWO STANDARDS' VOCABULARIES.
LOGICAL_TYPES = {
    "int64": "integer",
    "float64": "number",
    "bool": "boolean",
    "string[python]": "string",
    "datetime64[ns]": "date",
}


def _properties(schema: pa.DataFrameSchema) -> list[dict[str, Any]]:
    """One ODCS property per column.

    `required` IS THE INVERSE OF `nullable` -- the two standards name the same
    idea from opposite ends, and that inversion is the only translation here.
    """
    rendered: list[dict[str, Any]] = []

    for name, column in schema.columns.items():
        dtype = str(column.dtype)
        if dtype not in LOGICAL_TYPES:
            raise SystemExit(
                f"no ODCS logical type mapped for pandera dtype {dtype!r} "
                f"(column {name!r}); add it to LOGICAL_TYPES"
            )

        prop: dict[str, Any] = {
            "name": name,
            "logicalType": LOGICAL_TYPES[dtype],
            "required": not column.nullable,
        }

        # UNIQUENESS IS A CONTRACT TERM, not an implementation detail: it is the
        # difference between a key and a column, and consumers rely on it.
        if column.unique:
            prop["unique"] = True

        rendered.append(prop)

    return rendered


def generate(model: str) -> dict[str, Any]:
    """Render one ODCS document from the pandera models."""
    _, tables = MODELS[model]

    return {
        "apiVersion": API_VERSION,
        "kind": "DataContract",
        "id": f"cscie103-olap-oltp-{model}",
        "version": "1.0.0",
        "status": "active",
        "name": f"cscie103_olap_oltp_{model}",
        "description": {
            "purpose": (
                f"The {model.upper()} model. Generated from "
                f"src/cscie103_olap_oltp/{model}/contracts.py -- edit those "
                "models, then run `mise run contracts:generate`."
            )
        },
        "schema": [
            {
                "name": name,
                "logicalType": "object",
                "description": (table.to_schema().description or "").strip(),
                "properties": _properties(table.to_schema()),
            }
            for name, table in sorted(tables.items())
        ],
    }


def _serialise(document: dict[str, Any]) -> str:
    """One canonical rendering, so a byte comparison means something.

    sort_keys=False KEEPS THE SPEC'S READING ORDER -- apiVersion, kind, id
    before schema -- which is what someone opening the file expects to see.
    """
    rendered: str = yaml.safe_dump(document, sort_keys=False, width=100, allow_unicode=True)
    return rendered


def matches_models(model: str) -> bool:
    path, _ = MODELS[model]
    return path.is_file() and path.read_text(encoding="utf-8") == _serialise(generate(model))


def main() -> int:
    match sys.argv[1:]:
        case ["--generate"]:
            for name, (path, _) in MODELS.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(_serialise(generate(name)), encoding="utf-8")
                print(f"wrote {path.relative_to(REPO_ROOT)}")
            return 0

        case ["--check"]:
            import pyodcs

            failed = False

            for name, (path, _) in sorted(MODELS.items()):
                if not path.is_file():
                    print(
                        f"{path.name} is missing; run `mise run contracts:generate`",
                        file=sys.stderr,
                    )
                    failed = True
                    continue

                # VALIDATED BY THE REFERENCE IMPLEMENTATION, so "valid ODCS"
                # means what the standard says rather than what we assumed.
                # parse_and_validate PRODUCES THE REPORT; is_valid JUDGES IT.
                # Passing the document straight to is_valid crashes inside the
                # library -- the two verbs are a pipeline, not alternatives.
                report = pyodcs.parse_and_validate(path.read_text(encoding="utf-8"))

                if not pyodcs.is_valid(report):
                    print(f"{path.name} is not valid ODCS {API_VERSION}:", file=sys.stderr)
                    # THE DIAGNOSTICS, NOT A BARE VERDICT. The reference
                    # implementation says which field is wrong and why; dropping
                    # that would leave the reader to re-run the tool by hand.
                    for diagnostic in report.get("diagnostics", []):
                        print(f"  {diagnostic}", file=sys.stderr)
                    failed = True
                    continue

                if not matches_models(name):
                    print(
                        f"{path.name} no longer describes the models.\n"
                        "A column was added, renamed or retyped without regenerating.\n"
                        "Fix: mise run contracts:generate",
                        file=sys.stderr,
                    )
                    failed = True

            if failed:
                return 1

            print(f"ODCS contracts valid and current: {', '.join(sorted(MODELS))}")
            return 0

        case other:
            raise SystemExit(f"usage: --generate | --check  (got {other})")


if __name__ == "__main__":
    raise SystemExit(main())
