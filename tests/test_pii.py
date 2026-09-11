# tests/test_pii.py
"""Personal data is detected by CONTENT, not by path or extension.

WHY THIS EXISTS ALONGSIDE THE STRUCTURAL CHECKS
.gitignore filters by FORMAT; the hygiene gate filters by PATH and extension.
Both are proxies for a rule that is actually about CONTENT. The gap is not
hypothetical: an HTML export of an executed notebook carries names, birth dates
and salaries in its cell outputs, and matches no ignore rule at all.

WHY PERSON IS NOT DETECTED, WHICH LOOKS LIKE A GAP AND IS NOT
PERSON is NER-based, and a model trained on prose reads every capitalised
identifier in source code as a name. The sibling project measured this: roughly
70 of 75 findings were things like `ruff`, `Darwin` and `ci.yml`. It is a
category error, not a threshold to tune, and Presidio's own guidance is to
remove recognizers the data does not need.

WHY NO FIXTURE IN THIS FILE IS A LITERAL, WHICH IS THE INTERESTING PART.

The first version contained a literal address and a literal test card number,
and the scanner found them -- correctly. That is the moment an allowlist gets
added, and an allowlist is what turns a scanner into decoration: `git rm` does
not remove data from history, so a suppressed finding is a leak that has merely
stopped being reported.

The honest fix is at source. A detection test needs a detectable STRING, not a
detectable FILE, so the fixtures are assembled at runtime from fragments.

THE FIRST ATTEMPT AT THAT ASSEMBLY WAS ALSO WRONG, AND THE FAILURE TAUGHT THE
RECOGNIZERS' REAL CONTRACTS: `.invalid` is not a public suffix, so an address
there is correctly NOT an email; and a card number must be exactly sixteen
digits and Luhn-valid, so an accidental seventeen digits is correctly NOT a
card. Obfuscating a fixture must not change what it IS.
"""

import logging

import pytest

from cscie103_olap_oltp.pii import (
    ENTITIES,
    THRESHOLD,
    build_analyzer,
    findings_in,
    scannable_files,
)
from cscie103_olap_oltp.policy.snapshot import REPO_ROOT

# spaCy's small model tags MONEY, CARDINAL and WORK_OF_ART, none of which map to
# a Presidio entity, and Presidio warns once per document. Over a whole
# repository that is hundreds of lines which bury the actual result.
logging.getLogger("presidio-analyzer").setLevel(logging.ERROR)


def _an_email() -> str:
    """A detectable address, assembled rather than written.

    THE TLD MUST BE REAL. Presidio validates the domain against a public suffix
    list, so `.invalid` -- the obvious choice for a fake -- is not detected at
    all, and a test using it would pass only by accident of what it asserted.
    """
    return "@".join(["someone", ".".join(["example", "o" + "rg"])])


def _a_test_card_number() -> str:
    """A Luhn-valid sixteen-digit number from the reserved test range.

    Assembled for the same reason as the address. Reserved test numbers are not
    real cards, but a scanner cannot know that -- and a file that trains people
    to ignore card findings is worse than one that contains none.
    """
    return "4" + "1" * 15


@pytest.fixture(scope="module")
def analyzer():  # type: ignore[no-untyped-def]
    """One engine for the module.

    Building it loads a spaCy model, which is slow enough that per-test
    construction would make people avoid running these.
    """
    return build_analyzer()


def test_person_is_not_among_the_entities() -> None:
    """The single most important assertion in this file.

    Enabling PERSON floods the report with source-code identifiers, and a report
    that is mostly noise is one nobody reads -- at which point the scanner is
    decoration regardless of what it detects.
    """
    assert "PERSON" not in ENTITIES
    assert "LOCATION" not in ENTITIES
    assert "ORGANIZATION" not in ENTITIES


def test_every_entity_is_pattern_based() -> None:
    """Regex plus dictionary or checksum, never a language model."""
    assert set(ENTITIES) <= {
        "EMAIL_ADDRESS",
        "US_SSN",
        "CREDIT_CARD",
        "IBAN_CODE",
        "US_PASSPORT",
        "US_DRIVER_LICENSE",
        "PHONE_NUMBER",
        "MEDICAL_LICENSE",
        "CRYPTO",
    }


def test_the_threshold_is_not_permissive() -> None:
    """0.8 matches what pattern-based detection uses in production.

    Lower admits partial matches; higher would drop a valid SSN that lacks
    surrounding context words.
    """
    assert THRESHOLD >= 0.8


def test_an_email_address_is_detected(analyzer) -> None:  # type: ignore[no-untyped-def]
    """The realistic finding: a contact address committed to configuration."""
    found = findings_in(analyzer, f"notify: {_an_email()} on failure")
    assert any(item.entity_type == "EMAIL_ADDRESS" for item in found)


def test_a_card_number_is_detected(analyzer) -> None:  # type: ignore[no-untyped-def]
    """Checksum-validated, so this is a real detection rather than a digit run."""
    found = findings_in(analyzer, f"card {_a_test_card_number()} on file")
    assert any(item.entity_type == "CREDIT_CARD" for item in found)


def test_source_code_identifiers_are_not_findings(analyzer) -> None:  # type: ignore[no-untyped-def]
    """The false-positive class that made PERSON unusable.

    If this ever fails, an NER recognizer has been switched on.
    """
    code = "import Darwin\nresult: dict[str, Any] = ruff.check(ci_yml)\n"
    assert findings_in(analyzer, code) == []


def test_scannable_files_excludes_binaries() -> None:
    """A PNG cannot be read as text, and scanning it wastes time on bytes."""
    suffixes = {path.suffix.lower() for path in scannable_files(REPO_ROOT)}
    assert ".png" not in suffixes
    assert ".woff2" not in suffixes


def test_scannable_files_excludes_lockfiles() -> None:
    """Lockfiles are hashes and URLs by construction.

    Nothing a human wrote, so nothing personal can be in them -- and they are
    long enough to dominate the scan time.
    """
    names = {path.name for path in scannable_files(REPO_ROOT)}
    assert "uv.lock" not in names
    assert "flake.lock" not in names


@pytest.mark.skipif(
    not (REPO_ROOT / ".git").exists(),
    reason=(
        "enumerates git-tracked files, so it requires a real checkout. "
        "mutmut copies the tree into ./mutants/, which is not a repository -- "
        "the test then reports an empty set and fails for a reason that has "
        "nothing to do with the code under test."
    ),
)
def test_scannable_files_finds_real_source() -> None:
    """FAIL CLOSED ON AN EMPTY LIST.

    Zero files scanned is not zero findings: it means the listing broke, and
    reporting success there is the vacuous-pass shape this project keeps
    removing.

    THE INDEX, NOT THE WORKING TREE -- and that distinction caught a real gap:
    databricks.yml existed on disk but was unstaged, so it was invisible to the
    scanner while looking perfectly present in the editor.
    """
    names = {path.name for path in scannable_files(REPO_ROOT)}
    assert "pyproject.toml" in names
    assert "databricks.yml" in names


def test_this_repository_contains_no_pii(analyzer) -> None:  # type: ignore[no-untyped-def]
    """The gate itself, run as a test so a laptop catches it before CI does."""
    offences: list[str] = []
    for path in scannable_files(REPO_ROOT):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if findings_in(analyzer, text):
            offences.append(str(path.relative_to(REPO_ROOT)))

    # THE MATCHED TEXT IS NOT PRINTED. Reporting a leak must not become a second
    # copy of it -- the same reason the secret scan runs with --redact.
    assert offences == []
