# src/cscie103_olap_oltp/policy/snapshot.py
"""Observe this repository's configuration and emit it as policy input.

CONFIGURATION AS DATA. policies/repo/repo.rego states what must be true; this
module answers what IS true, as a structured object a policy engine can
evaluate. Neither half is a gate on its own -- a policy with no facts is a rule
file, and facts with no policy are rows.

TWO DESIGN CONSTRAINTS, BOTH LEARNED FROM FAIL-OPEN BUGS:

  ABSENCE RAISES. A missing or unreadable file raises rather than returning an
  empty mapping. Rego resolves an unknown path to undefined, and `not
  input.mise.x` is TRUE when the entire section is missing -- so an empty
  section silently flips a rule's meaning instead of failing.

  PATHS DERIVE FROM __file__, NOT cwd. A git hook runs from wherever the user
  happened to be. A snapshot keyed on the working directory reads a different
  repository depending on who invoked it.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path
from typing import Any

from cscie103_olap_oltp.environment import current

# src/cscie103_olap_oltp/policy/snapshot.py -> up four to the repository root.
REPO_ROOT = Path(__file__).resolve().parents[3]


def _read_text(path: Path) -> str:
    """Read a file, or raise. Never substitute a default for absence."""
    if not path.is_file():
        raise FileNotFoundError(f"policy input missing: {path}")
    return path.read_text(encoding="utf-8")


def _read_toml(path: Path) -> dict[str, Any]:
    """Parse TOML, or raise."""
    if not path.is_file():
        raise FileNotFoundError(f"policy input missing: {path}")
    with path.open("rb") as handle:
        return tomllib.load(handle)


def mise_facts(path: Path) -> dict[str, Any]:
    """Facts feeding R001-R005: the toolchain declaration and every task.

    TASKS ARE DISCOVERED, NOT ENUMERATED. A hand-maintained list goes stale the
    moment a task is added, and the failure is silent -- the new task simply
    stops being examined by R003 and R004.

    mise supports two spellings, [tasks.name] and [tasks."name:sub"], and both
    land in the same `tasks` table after parsing, so one traversal covers them.
    """
    document = _read_toml(path)
    raw_tasks = document.get("tasks", {})

    tasks: list[dict[str, Any]] = []
    for name, body in raw_tasks.items():
        run = body.get("run", "") if isinstance(body, dict) else ""
        tasks.append(
            {
                "name": name,
                "run": run,
                # A TASK IS MULTI-COMMAND IF ITS BODY HAS MORE THAN ONE
                # NON-EMPTY LINE. R004 requires `set -euo pipefail` for exactly
                # those, because bash reports only the LAST command's exit code.
                "is_multiline": len([ln for ln in run.splitlines() if ln.strip()]) > 1,
            }
        )

    text = _read_text(path)
    return {
        "has_tools_block": "\n[tools]" in f"\n{text}",
        "task_shell_enters_devshell": "nix develop"
        in document.get("task_config", {}).get("shell", ""),
        # R005 asks whether the policy gate can pass vacuously. `opa test`
        # SUCCEEDS on zero tests, so --fail-on-empty is what makes it a gate.
        "policy_gate_fails_on_empty": any("--fail-on-empty" in task["run"] for task in tasks),
        "tasks": tasks,
    }


def workflow_facts(path: Path) -> dict[str, Any]:
    """Facts feeding R006-R008: action pins, permissions, and gate duplication.

    PARSED AS TEXT, NOT AS YAML, AND THAT IS DELIBERATE. A YAML parse would need
    a dependency for three questions answerable from the raw file, and the
    workflow is authored here rather than generated. The cost of the choice is
    that a `uses:` inside a comment would be counted -- which is why the
    accompanying test asserts the count against the real file.
    """
    text = _read_text(path)
    lines = text.splitlines()

    uses = [
        {"ref": line.split("uses:", 1)[1].split("#", 1)[0].strip()}
        for line in lines
        if line.strip().startswith("- uses:")
    ]

    # R008: CI must run the gate, not restate its members. Naming an individual
    # gate here is a second copy of a list that lives in mise.toml.
    restates = any(f"mise run {gate}" in text for gate in ("lint", "types", "test", "fmt"))

    return {
        "has_permissions": any(line.startswith("permissions:") for line in lines),
        "restates_gate_steps": restates,
        "uses": uses,
    }


def lefthook_facts(path: Path) -> dict[str, Any]:
    """Facts feeding R009-R011: the hooks and the branch guard."""
    text = _read_text(path)
    pre_commit_block = ""
    if "pre-commit:" in text:
        pre_commit_block = text.split("pre-commit:", 1)[1].split("\npre-push:", 1)[0]

    return {
        "has_pre_commit": "\npre-commit:" in f"\n{text}",
        "has_pre_push": "\npre-push:" in f"\n{text}",
        "pre_commit_guards_branch": "mise run start:check" in pre_commit_block,
    }


def python_facts(root: Path) -> dict[str, Any]:
    """Facts feeding R012: the interpreter, as declared in both places.

    BOTH ARE READ. Comparing a value against itself is the vacuous shape that
    makes a drift check useless, so the two declarations come from two files.
    """
    dotfile = _read_text(root / ".python-version").strip()

    document = _read_toml(root / "pyproject.toml")
    requires = document["project"]["requires-python"]
    # `==3.12.3` -> `3.12.3`. Only the pinned form is understood; a range would
    # make R012 meaningless, so it is left unparsed and will mismatch loudly.
    pyproject = requires.removeprefix("==").strip()

    return {"dotfile_version": dotfile, "pyproject_version": pyproject}


def pytest_facts(path: Path) -> dict[str, Any]:
    """Facts feeding R013-R014: whether the test suite can pass vacuously."""
    document = _read_toml(path)
    options = document.get("tool", {}).get("pytest", {}).get("ini_options", {})
    return {
        "addopts": list(options.get("addopts", [])),
        "xfail_strict": bool(options.get("xfail_strict", False)),
    }


def git_facts(root: Path | None = None) -> dict[str, Any]:
    """Facts feeding R015: how this clone reaches its remote.

    SSH IS THE FLEET-WIDE TRANSPORT, and until this fact existed that rule was
    held by a Python assertion -- which makes it a convention rather than a
    rule. A convention is what drifted: the Studio's clone was created over
    HTTPS while the sibling project sat on SSH, and nothing refused.

    THE HOST IS CARRIED, NOT JUST THE VERDICT. A denial that says "not SSH"
    without naming what it found sends the reader back to the terminal.

    ABSENCE IS NOT A PASS. A clone with no origin reports origin_is_ssh false
    with an empty host, so R015 denies rather than evaluating against undefined.
    """
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        cwd=root or REPO_ROOT,
    )
    url = result.stdout.strip() if result.returncode == 0 else ""

    # scp-style `user@host:owner/repo` or an explicit ssh:// scheme. Anything
    # else -- https, git, a bare path -- is not SSH.
    is_ssh = bool(url) and (url.startswith("ssh://") or "@" in url.split("/", 1)[0])

    host = ""
    if "@" in url:
        host = url.split("@", 1)[1].split(":", 1)[0].split("/", 1)[0]
    elif "://" in url:
        host = url.split("://", 1)[1].split("/", 1)[0]

    # EPHEMERAL CHECKOUTS ARE OUT OF SCOPE FOR R015, AND THAT IS A SCOPE
    # DECISION RATHER THAN AN EXEMPTION.
    #
    # A runner's clone is created by actions/checkout, authenticated with a
    # scoped token, and deleted minutes later. Every reason SSH is the fleet
    # rule -- predictable reach, one key to reason about, no stored credential
    # -- concerns durable developer machines. CI cannot choose its transport
    # without storing a key, which is the thing the rule exists to avoid.
    #
    # CI IS SET BY EVERY MAJOR RUNNER, the same signal the live gates use to
    # decide whether missing credentials are a skip or a failure.
    ephemeral = current().ci

    return {
        "origin_is_ssh": is_ssh,
        "origin_host": host,
        "is_ephemeral_checkout": ephemeral,
    }


def build_snapshot(root: Path) -> dict[str, Any]:
    """Assemble the complete policy input.

    EVERY SECTION THE POLICY READS APPEARS HERE. A missing section is not a
    partial evaluation: Rego resolves it to undefined, which flips a negated
    rule from a denial to a pass. The accompanying test asserts the key set
    exactly, so adding a policy section without a fact source fails.
    """
    return {
        "mise": mise_facts(root / "mise.toml"),
        "workflow": workflow_facts(root / ".github" / "workflows" / "ci.yml"),
        "lefthook": lefthook_facts(root / "lefthook.yml"),
        "python": python_facts(root),
        "pytest": pytest_facts(root / "pyproject.toml"),
        "git": git_facts(root),
    }
