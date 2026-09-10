# src/cscie103_olap_oltp/pii.py
"""Scan tracked files for personal information, by content.

WHY THIS EXISTS ALONGSIDE THE STRUCTURAL CHECKS
.gitignore filters by FORMAT; the hygiene gate filters by PATH and extension.
Both are proxies for the real rule, which is about CONTENT. The gap is not
hypothetical: an HTML export of an executed notebook carries names, birth dates
and salaries in its cell outputs, and matches no ignore rule at all.

WHY PERSON IS NOT DETECTED, WHICH LOOKS LIKE A GAP AND IS NOT
PERSON is NER-based, and a model trained on prose reads every capitalised
identifier in source code as a name. The sibling project measured it: enabling
PERSON produced 75 findings, of which roughly 70 were `ruff`, `Darwin`,
`ci.yml`, `dict[str` and `.replace`. That is a category error, not a threshold
to tune, and Presidio's own guidance prescribes removing recognizers the data
does not need.

The pattern-based recognizers below combine regex with dictionary and checksum
validation: explainable detection with few false positives on source code. They
found every real issue in the sibling repository; PERSON found none they missed.

THERE IS NO ALLOWLIST, DELIBERATELY
Suppressing a finding instead of removing its cause is what makes a scanner
decoration -- and `git rm` does not remove data from history, so a suppressed
finding is a leak that has stopped being reported. Every finding is eliminated
at source. The sibling's first real one, a hardcoded email in databricks.yml,
became a substitution, which was better configuration anyway.

WHAT IT STILL CANNOT DO
Free-text names in a committed data file would pass. The hygiene gate is what
prevents such a file from being tracked at all; this is the layer that catches a
contact-shaped or credential-shaped value hiding in something that looks like
code.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.nlp_engine import NlpEngineProvider

from cscie103_olap_oltp.git.env import scrubbed_env
from cscie103_olap_oltp.policy.snapshot import REPO_ROOT

# PATTERN-BASED ONLY. Every entity here is detected by regex plus dictionary and
# checksum validation, not by a language model. PERSON, LOCATION and
# ORGANIZATION are deliberately absent -- see the module docstring.
ENTITIES = [
    "EMAIL_ADDRESS",
    "US_SSN",
    "CREDIT_CARD",
    "IBAN_CODE",
    "US_PASSPORT",
    "US_DRIVER_LICENSE",
    "PHONE_NUMBER",
    "MEDICAL_LICENSE",
    "CRYPTO",
]

# 0.8, matching the threshold production PII platforms use for pattern-based
# detection. Lower admits partial matches; higher would drop a valid SSN that
# lacks surrounding context words.
THRESHOLD = 0.8

SKIP_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2"})

# LOCKFILES ARE HASHES AND URLS BY CONSTRUCTION: nothing a human wrote, so
# nothing personal can be in them -- and they are long enough to dominate the
# scan time.
SKIP_NAMES = frozenset({"uv.lock", "flake.lock"})


def scannable_files(root: Path) -> list[Path]:
    """Every tracked file worth reading as text.

    THE INDEX, NOT THE WORKING TREE. An untracked file with an address in it is
    a local mistake; a tracked one ships and enters history permanently.
    """
    result = subprocess.run(
        ["git", "ls-files", "-z"],  # noqa: S607
        capture_output=True,
        check=False,
        cwd=root,
        env=scrubbed_env(),
    )
    if result.returncode != 0:
        print(result.stderr.decode(errors="replace").strip(), file=sys.stderr)
        raise SystemExit("git ls-files failed")

    paths: list[Path] = []
    for entry in result.stdout.split(b"\0"):
        if not entry:
            continue
        relative = entry.decode("utf-8")
        path = root / relative
        if path.name in SKIP_NAMES or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if not path.is_file():
            continue
        paths.append(path)
    return paths


def build_analyzer() -> AnalyzerEngine:
    """Analyzer pinned to the small spaCy model.

    AnalyzerEngine() WITH NO ARGUMENTS LOADS en_core_web_lg AND DOWNLOADS IT --
    560MB, on first run, ignoring whatever the lockfile pinned. NlpEngineProvider
    makes the choice explicit and keeps the install reproducible.

    An NLP engine is required even with every NER entity disabled, because
    Presidio uses spaCy for tokenization regardless.
    """
    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        }
    )
    return AnalyzerEngine(
        nlp_engine=provider.create_engine(),
        supported_languages=["en"],
    )


def findings_in(analyzer: AnalyzerEngine, text: str) -> list[Any]:
    """Every detection at or above the threshold.

    Returned as objects rather than strings so a caller can group by entity type
    without parsing a message back apart.
    """
    return [
        result
        for result in analyzer.analyze(text=text, entities=ENTITIES, language="en")
        if result.score >= THRESHOLD
    ]


def main() -> int:
    analyzer = build_analyzer()
    files = scannable_files(REPO_ROOT)

    # FAIL CLOSED ON AN EMPTY LIST. Zero files scanned is not zero findings: it
    # means the listing broke, and reporting success there is the vacuous-pass
    # shape this project keeps removing.
    if not files:
        print("no tracked files to scan; refusing to report success", file=sys.stderr)
        return 1

    findings: list[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        for result in findings_in(analyzer, text):
            line = text[: result.start].count("\n") + 1
            findings.append(
                f"{path.relative_to(REPO_ROOT)}:{line}  "
                f"{result.entity_type} (score {result.score:.2f})"
            )

    if findings:
        # THE MATCHED TEXT IS DELIBERATELY NOT PRINTED. Reporting a leak must not
        # become a second copy of it -- the same reason the secret scan runs with
        # --redact. The path and line are enough to find it.
        print(f"{len(findings)} PII finding(s):", file=sys.stderr)
        for finding in sorted(findings):
            print(f"  {finding}", file=sys.stderr)
        print(
            "\nEliminate at SOURCE -- substitute a variable, generate at runtime, "
            "or remove the file. Do not add an allowlist: `git rm` does not remove "
            "data from history, and a suppressed finding makes the scanner "
            "decoration.",
            file=sys.stderr,
        )
        return 1

    print(f"no PII detected across {len(files)} tracked files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
