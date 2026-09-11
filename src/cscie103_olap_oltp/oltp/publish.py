# src/cscie103_olap_oltp/oltp/publish.py
"""The OLTP contracts, published as JSON Schema for consumers.

    Schema as Code      the models are the source
    Contracts as Data   a consumer binds to a version it can read

WHY PUBLISH AT ALL, GIVEN THE MODELS ARE RIGHT THERE. A producer in another
language cannot import a python class, and a reviewer cannot see a shape change
in a diff of behaviour. The published contract is what makes "the shape of
`customer` changed" show up in a pull request rather than be discovered by
reading code.

DERIVED, NEVER MAINTAINED. pandera's own to_json_schema() serialises the schema;
nothing here walks fields. A hand-rolled renderer would drift from the validator
the first time a Field gained an option, and the drift would be silent because
the renderer would still produce plausible JSON.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from cscie103_olap_oltp.oltp.contracts import TABLES
from cscie103_olap_oltp.policy.snapshot import REPO_ROOT

CONTRACTS_PATH = REPO_ROOT / "contracts" / "oltp.schema.json"

# NO $schema HEADER, DELIBERATELY. This is pandera's serialisation format, not
# a JSON-Schema document, and claiming a dialect it does not follow would invite
# a validator to reject a perfectly good contract.


def generate() -> dict[str, Any]:
    """Render every table's contract from its model."""
    return {
        "title": "OLTP contracts",
        "description": (
            "Third-normal-form entities. Generated from "
            "src/cscie103_olap_oltp/oltp/contracts.py -- edit those models, "
            "then run `mise run oltp:contracts`."
        ),
        # to_json(), NOT to_json_schema(), AND THE DIFFERENCE IS THE CONTRACT.
        #
        # to_json_schema() is a JSON-Schema PROJECTION: it renders the columnar
        # shape -- names, types, a synthetic index -- and DROPS every check.
        # Published that way, `unique=True` and `gt=0` simply vanish, so a
        # consumer reading the contract would see nothing of the rules the
        # producer actually enforces.
        #
        # to_json() is pandera's own round-trippable serialisation, loadable
        # again with from_json, and preserving checks is treated as a
        # correctness property of it -- dropped checks have been filed and fixed
        # as bugs. That makes it the honest artifact for "what must be true of
        # this table".
        "tables": {
            name: json.loads(model.to_schema().to_json()) for name, model in sorted(TABLES.items())
        },
    }


def _serialise(document: dict[str, Any]) -> str:
    """One canonical rendering, so a byte comparison means something."""
    return json.dumps(document, indent=2, sort_keys=True, default=str) + "\n"


def matches_models() -> bool:
    if not CONTRACTS_PATH.is_file():
        return False
    return CONTRACTS_PATH.read_text(encoding="utf-8") == _serialise(generate())


def main() -> int:
    match sys.argv[1:]:
        case ["--generate"]:
            CONTRACTS_PATH.parent.mkdir(parents=True, exist_ok=True)
            CONTRACTS_PATH.write_text(_serialise(generate()), encoding="utf-8")
            print(f"wrote {CONTRACTS_PATH.relative_to(REPO_ROOT)}")
            return 0

        case ["--check"]:
            if not CONTRACTS_PATH.is_file():
                print(
                    f"{CONTRACTS_PATH.name} is missing; run `mise run oltp:contracts`",
                    file=sys.stderr,
                )
                return 1

            if not matches_models():
                print(
                    f"{CONTRACTS_PATH.name} no longer describes the models.\n"
                    "A column was added, renamed or retyped without regenerating.\n"
                    "Fix: mise run oltp:contracts",
                    file=sys.stderr,
                )
                return 1

            print(f"{CONTRACTS_PATH.name} matches the models")
            return 0

        case other:
            raise SystemExit(f"usage: --generate | --check  (got {other})")


if __name__ == "__main__":
    raise SystemExit(main())
