# src/cscie103_olap_oltp/gates.py
"""Run every gate, report every result, exit nonzero if any failed.

THE PROBLEM THIS REPLACES
`check` chained gates with `mise run lint && mise run types && ...` under
`set -euo pipefail`, so the first failure ended the run. That produces
fix-one, re-run, discover-the-next: a treadmill built into the gate itself. It
hid real findings during this project's own construction repeatedly -- most
recently four separate defects in one bundle change, which arrived together
instead of over four cycles.

Worse, a gate whose result is never printed is indistinguishable from one that
passed, so a chain can quietly under-report what it checked.

WHAT THIS DOES INSTEAD
Runs each gate to completion, captures its output, and prints a summary of ALL
results. One invocation tells the whole truth. Serial and slower, deliberately:
on a gate that runs on every commit, correctness of reporting beats a few
seconds.

WHY EACH GATE IS STILL ITS OWN TASK
So `mise run lint` works standalone during iteration. This aggregates them; it
does not replace them, and there is no second copy of the list to drift.
"""

from __future__ import annotations

import subprocess
import sys

from pydantic import BaseModel, ConfigDict


class Gate(BaseModel):
    """One check, and whether it can run without a network."""

    model_config = ConfigDict(frozen=True)

    name: str
    command: tuple[str, ...]
    # needs_network MEANS "NOT IN THE GIT HOOKS": either it requires the network
    # and valid credentials, or it is slow enough that including it would train
    # people to bypass the hook. Both make a gate that belongs in CI, where a
    # bypass is caught.
    needs_network: bool = False


class GateResult(BaseModel):
    """What running a gate produced. An observed fact, so frozen."""

    model_config = ConfigDict(frozen=True)

    name: str
    command: tuple[str, ...]
    returncode: int
    output: str

    @property
    def passed(self) -> bool:
        return self.returncode == 0


# ONE LIST, TWO MODES.
#
# WHY NOT TWO LISTS: hooks must be fast and work offline, CI must be exhaustive.
# Writing that as two sets of checks in two places is precisely the drift this
# repo exists to prevent -- add a gate to one, forget the other, and the
# difference is invisible until something ships broken. A flag over a single
# list cannot drift.
#
# WHY ONE SERIAL RUNNER RATHER THAN PARALLEL CI JOBS. A matrix would give
# per-gate check names that branch protection could require individually, and
# failure attribution visible from the check list rather than the log. Both are
# real advantages, and both are rejected on one ground: a matrix must ENUMERATE
# the gates in YAML, making ci.yml a second source of truth for this list. That
# copy goes stale silently, and R008 forbids it.
GATES: tuple[Gate, ...] = (
    Gate(name="lint", command=("uv", "run", "ruff", "check", ".")),
    Gate(name="format", command=("uv", "run", "ruff", "format", "--check", ".")),
    # BOTH PLATFORMS, DELIBERATELY. mypy defaults to the OS it runs on and
    # NARROWS sys.platform comparisons, so a Darwin-only branch is invisible to
    # a Linux run and vice versa. This repo is a macOS laptop plus a Linux
    # runner; checking one leaves the other branch unverified everywhere.
    Gate(
        name="types (darwin)",
        command=("uv", "run", "mypy", "--platform", "darwin", "src", "tests"),
    ),
    Gate(
        name="types (linux)",
        command=("uv", "run", "mypy", "--platform", "linux", "src", "tests"),
    ),
    Gate(name="test", command=("uv", "run", "pytest", "-q")),
    # Policy gets the same treatment as the Python: well-formed and tested, then
    # evaluated against the actual repository. Both are offline.
    Gate(name="policy", command=("mise", "run", "check:policy")),
    Gate(
        name="policy decisions",
        command=("uv", "run", "python", "-m", "cscie103_olap_oltp.policy"),
    ),
    # THIS REPOSITORY ALREADY COMMITTED AN ACCIDENT: a zero-byte file named
    # `....` from a misfired heredoc, staged by `git add -A`. It was caught by
    # reading the commit output, which is luck rather than a gate.
    Gate(
        name="repo hygiene",
        command=("uv", "run", "python", "-m", "cscie103_olap_oltp.hygiene"),
    ),
    # THE AUDIT TRAIL IS VERIFIED, NOT MERELY WRITTEN. A hash chain nobody
    # recomputes is decoration: tampering is detectable only if something
    # actually detects it. Offline, and fast -- it walks a local file.
    Gate(
        name="ledger",
        command=("uv", "run", "python", "-m", "cscie103_olap_oltp.ledger"),
    ),
    # THE PUBLISHED ARTIFACT SCHEMA MUST STILL DESCRIBE THE MODELS. A consumer
    # holding contracts/decision.schema.json has no models -- if a field is
    # renamed and the contract is not regenerated, their reader breaks while
    # every producer test here still passes.
    Gate(
        name="artifact schema",
        command=(
            "uv",
            "run",
            "python",
            "-m",
            "cscie103_olap_oltp.contracts.schema",
            "--check",
        ),
    ),
    # THE ENV CONTRACT IS DERIVED, SO IT MUST BE VERIFIED. A generated file
    # that nobody regenerates is a hand-maintained file with a misleading
    # header -- and template drift is the documented number-one "works on my
    # machine" bug.
    Gate(
        name="env template",
        command=(
            "uv",
            "run",
            "python",
            "-m",
            "cscie103_olap_oltp.environment",
            "--check-template",
        ),
    ),
    # PII BY CONTENT, WHERE THE OTHER TWO CHECK FORM AND PATH.
    #
    # .gitignore filters by FORMAT and the hygiene gate by PATH; both are
    # proxies for a rule about CONTENT. An HTML export of an executed notebook
    # carries names and salaries in its cell outputs and matches no rule at all.
    #
    # Offline: the recognizers are regex plus dictionary and checksum, and the
    # spaCy model is pinned in the lockfile rather than downloaded per run.
    Gate(name="pii", command=("uv", "run", "python", "-m", "cscie103_olap_oltp.pii")),
    # THE INTERPRETER PARITY GATE. Compares .python-version against the version
    # the BUNDLE declares, which needs the CLI to resolve -- hence network.
    #
    # The failure it prevents is a UDF dying with "Python versions in the Spark
    # Connect client and server are different", at execution time, far from the
    # change that caused it.
    Gate(
        name="env parity",
        command=("uv", "run", "python", "-m", "cscie103_olap_oltp.databricks", "env-parity"),
        needs_network=True,
    ),
    # PREREQUISITES: what a deploy depends on that the bundle does not manage.
    # Without this a missing dependency surfaces as an API error naming the
    # symptom rather than the cause.
    Gate(
        name="prereqs",
        command=("uv", "run", "python", "-m", "cscie103_olap_oltp.databricks", "prereqs"),
        needs_network=True,
    ),
    # FULL HISTORY, NOT THE WORKING TREE. A pre-commit hook only ever sees the
    # incoming change, so anything committed before hooks existed -- or pushed
    # with --no-verify -- has never been scanned.
    #
    # --redact IS NOT COSMETIC. Without it a finding prints the credential it
    # found, into a CI log that is retained and often world-readable. A scanner
    # that leaks what it detects has made the exposure worse.
    Gate(name="secret scan", command=("betterleaks", "git", "--redact", "--no-banner")),
    # osv-scanner reads uv.lock; the vulnerability database ships with the
    # scanner rather than being fetched per run, so this is offline.
    Gate(
        name="dependencies",
        command=("osv-scanner", "scan", "source", "--lockfile", "uv.lock"),
    ),
    # THE INTEGRATION TESTS RAN NOWHERE UNTIL THIS LINE. They are deselected
    # from the default pytest run by design, so a test written and left there is
    # a test that exists and never executes -- worse than no test, because it
    # looks like coverage.
    Gate(
        name="test (integration)",
        command=("uv", "run", "pytest", "-m", "integration"),
        needs_network=True,
    ),
    # THE FLAKE'S THREE-PLATFORM PROMISE WAS NEVER VERIFIED BY THE GATE.
    # A package present on Darwin but absent on Linux passes locally and breaks
    # on the runner. Network because evaluation fetches the locked nixpkgs.
    Gate(
        name="nix flake",
        command=("nix", "flake", "check", "--no-build", "--all-systems"),
        needs_network=True,
    ),
    # zizmor audits the workflow that runs this gate. Without --offline it
    # queries GitHub for action metadata, so it is marked accordingly.
    Gate(
        name="workflow audit",
        command=("zizmor", ".github/workflows/ci.yml"),
        needs_network=True,
    ),
)


def offline_gates() -> tuple[Gate, ...]:
    """The gates a git hook may run. FILTERED, never a second list."""
    return tuple(gate for gate in GATES if not gate.needs_network)


def run_gate(gate: Gate) -> GateResult:
    """Run one gate to completion, capturing everything it said.

    BOTH STREAMS ARE MERGED. Which one carries the diagnosis is not knowable in
    advance: ruff writes findings to stdout, git to stderr, and a hook to
    stdout. Capturing one of them is how a diagnosable failure becomes four
    useless words.
    """
    completed = subprocess.run(  # noqa: S603
        list(gate.command),
        capture_output=True,
        text=True,
        check=False,
    )
    return GateResult(
        name=gate.name,
        command=gate.command,
        returncode=completed.returncode,
        output=(completed.stdout + completed.stderr).strip(),
    )


def summarise(results: list[GateResult]) -> int:
    """Print every result, then return the exit code.

    FAIL CLOSED ON AN EMPTY RUN. Zero gates run is not zero gates failed: an
    empty result set means the selection matched nothing -- a renamed mode or a
    mistyped filter -- and reporting success there is the vacuous-pass shape
    this project keeps removing.
    """
    if not results:
        print("no gates ran; refusing to report success", file=sys.stderr)
        return 1

    failed = [result for result in results if not result.passed]

    print("\n" + "=" * 60)
    for result in results:
        mark = "PASS" if result.passed else "FAIL"
        print(f"{mark}  {result.name}")
    print("=" * 60)

    if not failed:
        print(f"{len(results)} gates passed")
        return 0

    # THE OUTPUT OF EVERY FAILURE, NOT JUST THE FIRST. This is the entire point:
    # one invocation tells you everything that is wrong.
    for result in failed:
        print(f"\n--- {result.name} ---", file=sys.stderr)
        print(result.output or "(no output)", file=sys.stderr)

    names = ", ".join(result.name for result in failed)
    print(f"\n{len(failed)} of {len(results)} gates failed: {names}", file=sys.stderr)
    return 1


def main() -> int:
    selected = offline_gates() if "--offline" in sys.argv[1:] else GATES

    results: list[GateResult] = []
    for gate in selected:
        print(f"--> {gate.name}")
        results.append(run_gate(gate))

    return summarise(results)


if __name__ == "__main__":
    raise SystemExit(main())
