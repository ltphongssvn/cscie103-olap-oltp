# src/cscie103_olap_oltp/git/ghcli.py
"""One place that knows what a gh exit code means.

WHY THIS MODULE EXISTS
`fetch_ruleset` called gh with `check=True`, so an authentication failure
surfaced as `subprocess.CalledProcessError: ... returned non-zero exit status 4`
-- a traceback that reads like a missing ruleset and is not. The gate said
"test (integration) FAILED" and the operator's first move was to look at branch
protection, which was fine.

THE ANTI-PATTERN THIS AVOIDS, WHICH IS THE REAL RISK
GitHub's own CLI tooling accumulated four separate sites reimplementing
`strings.Contains(err.Error(), "exit status 4")` to detect gh authentication
errors, each with slightly different supporting substrings, while a centralized
helper already existed. Duplicated, divergent checks make the auth-error
contract fragile and let behaviour drift between commands.

So exit 4 is named HERE and nowhere else. A second caller adds an import, not a
second definition.

WHY A DISTINCT EXCEPTION RATHER THAN A BETTER MESSAGE
A caller can decide. The integration gate FAILS on missing credentials when the
environment promised them and SKIPS when it did not, and neither branch has to
parse a string to tell the difference. Collapsing both into SystemExit forces
every caller to one policy.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

# gh's DOCUMENTED CODE FOR "NOT AUTHENTICATED". Named once, as a constant, so
# the magic number appears in exactly one place in this repository.
NOT_AUTHENTICATED = 4


class GhError(RuntimeError):
    """gh failed for a reason the caller may want to distinguish."""


class NotAuthenticatedError(GhError):
    """gh has no usable credentials.

    ON A LAPTOP gh reads a keyring; ON A RUNNER it reads GH_TOKEN. Neither is
    ambient state this repository controls, so its absence is a first-class
    outcome rather than an unexpected crash.

    THE `Error` SUFFIX IS NOT DECORATION. Python's naming convention is that
    exception names end in Error, and ruff's N818 enforces it -- a class named
    `NotAuthenticated` reads as a predicate or a state at the call site, which
    is exactly wrong for something that unwinds the stack.
    """


def gh(*args: str) -> str:
    """Run gh and return stdout, translating exit codes into meaning.

    NOT check=True. That raises CalledProcessError, whose message names the
    command and the number and explains nothing -- which is precisely how an
    auth failure came to look like a missing ruleset.

    S603 IS SUPPRESSED NARROWLY: `*args` makes the list computed, which is what
    ruff flags. Every call site passes literal subcommands; there is no shell.
    """
    result = subprocess.run(  # noqa: S603
        ["gh", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode == NOT_AUTHENTICATED:
        raise NotAuthenticatedError(
            "gh is not authenticated (exit 4).\n"
            "  locally:  gh auth login\n"
            "  in CI:    set GH_TOKEN on the step that runs the gate\n"
            f"  gh said: {result.stderr.strip() or '(nothing)'}"
        )

    if result.returncode != 0:
        raise GhError(
            f"gh {' '.join(args)} failed with exit {result.returncode}: "
            f"{result.stderr.strip() or '(no stderr)'}"
        )

    return result.stdout


def gh_json(*args: str) -> Any:
    """Run gh and parse its JSON output.

    The parse is here rather than at each call site so a malformed response
    fails once, with the command that produced it named.
    """
    raw = gh(*args)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise GhError(f"gh {' '.join(args)} returned unparseable JSON: {error}") from error
