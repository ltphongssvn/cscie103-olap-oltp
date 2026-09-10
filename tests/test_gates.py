# tests/test_gates.py
"""Every gate runs to completion, and every result is reported.

THE PROBLEM THIS REPLACES. `check` chained `mise run lint && mise run types &&
...` under `set -euo pipefail`, so the first failure ended the run. That
produces fix-one, re-run, discover-the-next: a treadmill built into the gate
itself, and it hid real findings during this project's own construction -- five
mypy errors were invisible until lint passed, and four ruff errors were
invisible until the write before that.

Worse, a gate whose result is never printed is indistinguishable from one that
passed, so a chain can quietly under-report what it checked.

ONE LIST, TWO MODES. Each gate declares whether it needs the network. Writing
the hook set and the CI set as two lists in two places is exactly the drift this
repository exists to prevent: add a gate to one, forget the other, and the
difference is invisible until something ships broken. A flag over a single list
cannot drift.
"""

import pytest
from pydantic import ValidationError

from cscie103_olap_oltp.gates import GATES, GateResult, offline_gates, summarise

# EXECUTABLES SUPPLIED BY flake.nix.
#
# THE RULE IS "NO BARE INTERPRETER", NOT "ONLY THESE FOUR TOOLS". The first
# version of this list held uv, mise, opa and regal, and it failed the moment a
# security scanner was added -- the list was an accident of what existed when it
# was written, not a principle. What actually matters is that Python is reached
# through `uv run` so it resolves inside the project venv, and that every other
# binary comes from the pinned toolchain rather than from PATH.
FLAKE_PROVIDED = frozenset(
    {"uv", "mise", "opa", "regal", "betterleaks", "osv-scanner", "zizmor", "gh"}
)

# THE ONE EXECUTABLE THAT CANNOT COME FROM THE FLAKE, because it is what BUILDS
# the flake's devShell. Requiring nix to be flake-provided is circular.
#
# It is enumerated rather than waved through: an unpinned binary is exactly what
# this rule exists to catch, so the exception has to be named and argued, not
# quietly appended to the list above. Its version is pinned instead by the
# determinate-nix-action in ci.yml.
BOOTSTRAP = frozenset({"nix"})


def _result(name: str, *, ok: bool) -> GateResult:
    return GateResult(
        name=name,
        command=("true",),
        returncode=0 if ok else 1,
        output="",
    )


def test_every_gate_has_a_unique_name() -> None:
    """The name is what a failure is reported under.

    Two gates sharing one name makes the summary ambiguous about which failed.
    """
    names = [gate.name for gate in GATES]
    assert len(names) == len(set(names))


def test_every_gate_uses_a_pinned_executable() -> None:
    """Nothing is resolved from ambient PATH.

    PATH is state this repository does not control; a binary found there is
    whatever the machine happens to have.
    """
    for gate in GATES:
        assert gate.command[0] in FLAKE_PROVIDED | BOOTSTRAP, gate.name


def test_only_nix_is_exempt_from_the_flake() -> None:
    """The bootstrap exception stays exactly one entry wide.

    Without this, BOOTSTRAP becomes the place unpinned tools get added when the
    real list refuses them -- an escape hatch that empties the rule it excepts.
    """
    assert {"nix"} == BOOTSTRAP


def test_no_gate_invokes_a_bare_interpreter() -> None:
    """The same rule R003 enforces for tasks, enforced for gates.

    A bare `python3` runs outside the project venv, with none of the locked
    dependencies importable -- and it passes for as long as the code happens to
    use only the standard library.
    """
    for gate in GATES:
        assert gate.command[0] not in {"python", "python3"}, gate.name
        if "python" in gate.command:
            assert gate.command[:2] == ("uv", "run"), gate.name


def test_offline_gates_are_a_subset_of_all_gates() -> None:
    """The hook list is FILTERED from the full list, never a second list."""
    assert set(offline_gates()) <= set(GATES)


def test_offline_gates_exclude_every_network_gate() -> None:
    """A hook that fails on a plane is a hook people bypass with --no-verify,
    at which point it catches nothing at all."""
    assert all(not gate.needs_network for gate in offline_gates())


def test_at_least_one_gate_needs_the_network() -> None:
    """Otherwise the offline/CI split is decorative.

    If every gate were offline, `--offline` would be a no-op that looks like a
    meaningful distinction.
    """
    assert any(gate.needs_network for gate in GATES)


def test_summarise_reports_failure_when_any_gate_failed() -> None:
    results = [_result("lint", ok=True), _result("types", ok=False)]
    assert summarise(results) == 1


def test_summarise_reports_success_only_when_all_passed() -> None:
    results = [_result("lint", ok=True), _result("types", ok=True)]
    assert summarise(results) == 0


def test_summarise_of_nothing_is_a_failure() -> None:
    """FAIL CLOSED. Zero gates run is not zero gates failed.

    An empty result set means the selection matched nothing -- a renamed mode or
    a mistyped filter -- and reporting success there is the vacuous-pass shape
    this project keeps removing.
    """
    assert summarise([]) == 1


def test_gate_result_is_frozen() -> None:
    """An observed outcome is not editable after the fact.

    ValidationError, NOT a type: ignore. The first version of this test carried
    a suppression, and mypy reported it as unused -- correctly: pydantic
    enforces frozen at RUNTIME, so there is no static error to silence.
    """
    result = _result("lint", ok=True)
    with pytest.raises(ValidationError, match="frozen"):
        result.returncode = 1


def test_secret_scanning_is_among_the_gates() -> None:
    """The flake ships betterleaks and nothing ran it.

    A security tool that is installed but never invoked is worse than one that
    is absent: it appears in the toolchain and creates confidence it has not
    earned.
    """
    assert any("secret" in gate.name for gate in GATES)


def test_the_secret_scan_redacts_its_findings() -> None:
    """A scanner that prints the credential it found has made things worse.

    CI logs are retained and often world-readable, so an unredacted finding
    turns a detection into a second exposure.
    """
    scans = [gate for gate in GATES if "secret" in gate.name]
    assert scans
    for gate in scans:
        assert "--redact" in gate.command


def test_dependency_scanning_is_among_the_gates() -> None:
    """Same argument for osv-scanner."""
    assert any("dependenc" in gate.name for gate in GATES)


def test_workflow_audit_is_among_the_gates() -> None:
    """Same argument for zizmor, which audits the workflow that runs the gate."""
    assert any("workflow" in gate.name for gate in GATES)


def test_integration_tests_actually_run_somewhere() -> None:
    """They are deselected from the default pytest run BY DESIGN.

    So a test written and left there executes nowhere at all -- worse than no
    test, because it looks like coverage. This is the line that gives them a
    home.
    """
    assert any("integration" in gate.name for gate in GATES)


def test_the_flake_is_evaluated_on_every_platform() -> None:
    """`nix:flake-check` existed as a task nothing called.

    A package present on Darwin but absent on Linux passes locally and breaks on
    the runner, which is the whole reason --all-systems exists.
    """
    flake = [gate for gate in GATES if "flake" in gate.name]
    assert flake
    for gate in flake:
        assert "--all-systems" in gate.command
