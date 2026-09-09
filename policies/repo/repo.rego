# policies/repo/repo.rego
# REPOSITORY BEHAVIOUR, ENFORCED RATHER THAN DOCUMENTED.
#
# Every rule here was a sentence in a README somewhere, and a sentence in a
# README enforces nothing. These are the same claims expressed so that a machine
# refuses when they are false.
#
# THE OUTPUT IS DATA, NOT A BOOLEAN. Each denial carries a stable id and a
# reason code, so a refusal can be aggregated, queried and explained months
# later -- Violations as Data, not a log line someone has to read.
#
# THREE-VALUED BY CONSTRUCTION. `allow` defaults to false, so absence of
# evidence is never silently a pass. A rule that cannot evaluate produces a
# denial, not silence.

# METADATA
# title: Repository behaviour
# description: |
#   Non-negotiable rules about how this repository is built and gated.
#   Evaluated against a snapshot of the repository's own configuration.
# authors:
#   - ltphongssvn
# entrypoint: true
package repo

import rego.v1

# DEFAULT DENY. The single most important line in the file: a malformed input,
# a renamed field, or a rule that fails to evaluate all land on `false` rather
# than falling through to permission.
default allow := false

allow if count(deny) == 0

# --- R001: the toolchain is declared once -------------------------------------
# mise.toml owning a [tools] block AND flake.nix declaring packages is two
# declarations of one fact. The sibling project shipped both and needed a
# parity-checking script to police the duplication it created.
deny contains finding if {
	input.mise.has_tools_block
	finding := {
		"id": "R001",
		"reason_code": "TOOLCHAIN_DECLARED_TWICE",
		"message": "mise.toml has a [tools] block; the toolchain belongs to flake.nix alone",
	}
}

# --- R002: tasks run inside the devShell --------------------------------------
# Without task_config.shell entering `nix develop`, a task body resolves tools
# from PATH -- which works for whoever happens to be inside a shell already and
# fails for everyone else.
deny contains finding if {
	not input.mise.task_shell_enters_devshell
	finding := {
		"id": "R002",
		"reason_code": "TASK_SHELL_NOT_PINNED",
		"message": "mise task_config.shell must enter the Nix devShell",
	}
}

# --- R003: no bare interpreter ------------------------------------------------
# `python3 script.py` is PATH resolution, and PATH is ambient state this repo
# does not control. It runs whatever interpreter the machine happens to have,
# outside the project venv, with none of the locked dependencies importable.
#
# THE ANCHOR IS A COMMAND POSITION, NOT "ANY NON-WORD CHARACTER". The first
# version of this regex used `(^|[^-\w])`, and its own test caught the bug: the
# SPACE in `uv run python` satisfies [^-\w], so the rule denied the very
# invocation it exists to require. Matching only at the start of the string or
# after a shell separator is what "a command called python" actually means.
deny contains finding if {
	some task in input.mise.tasks
	regex.match(`(^|[\n;|&]\s*)python3?\s`, task.run)
	finding := {
		"id": "R003",
		"reason_code": "BARE_INTERPRETER",
		"message": sprintf("task %q invokes a bare interpreter; use `uv run python`", [task.name]),
	}
}

# --- R004: multi-command tasks fail closed ------------------------------------
# bash does not set errexit. A multi-command task without `set -euo pipefail`
# reports the exit code of its LAST command, so an earlier failure passes
# silently -- a gate that checks four things and can only fail on the fourth.
deny contains finding if {
	some task in input.mise.tasks
	task.is_multiline
	not contains(task.run, "set -euo pipefail")
	finding := {
		"id": "R004",
		"reason_code": "TASK_FAILS_OPEN",
		"message": sprintf("multi-command task %q lacks `set -euo pipefail`", [task.name]),
	}
}

# --- R005: policy tests cannot be vacuous -------------------------------------
# `opa test` SUCCEEDS when zero tests run, so a renamed package or mistyped path
# reports green while testing nothing. --fail-on-empty is what makes the policy
# gate mean anything at all.
deny contains finding if {
	not input.mise.policy_gate_fails_on_empty
	finding := {
		"id": "R005",
		"reason_code": "VACUOUS_POLICY_GATE",
		"message": "check:policy must pass --fail-on-empty to `opa test`",
	}
}

# --- R006: actions are pinned to a commit -------------------------------------
# A tag is mutable: the owner can repoint v5 at any commit, so the workflow runs
# whatever code sits behind that tag today. That matters more here than in a
# lockfile, because a compromised action executes on the runner.
deny contains finding if {
	some step in input.workflow.uses
	not regex.match(`@[0-9a-f]{40}$`, step.ref)
	finding := {
		"id": "R006",
		"reason_code": "ACTION_NOT_SHA_PINNED",
		"message": sprintf("action %q is not pinned to a 40-character commit SHA", [step.ref]),
	}
}

# --- R007: the workflow declares least privilege ------------------------------
# The default GITHUB_TOKEN is far broader than a gate needs.
deny contains finding if {
	not input.workflow.has_permissions
	finding := {
		"id": "R007",
		"reason_code": "WORKFLOW_PERMISSIONS_DEFAULT",
		"message": "the workflow must declare an explicit `permissions` block",
	}
}

# --- R008: CI does not restate the gate ---------------------------------------
# Listing lint, types and test as separate CI steps is a second copy of a list
# that lives in mise.toml, and it drifts the moment a gate is added to one and
# forgotten in the other.
deny contains finding if {
	input.workflow.restates_gate_steps
	finding := {
		"id": "R008",
		"reason_code": "GATE_LIST_DUPLICATED",
		"message": "ci.yml restates individual gates; it must run `mise run check` only",
	}
}

# --- R009/R010: hooks exist ---------------------------------------------------
# A pre-commit needing network or credentials fails on a plane, and a hook that
# fails on a plane is a hook people bypass with --no-verify -- at which point it
# catches nothing.
deny contains finding if {
	not input.lefthook.has_pre_commit
	finding := {
		"id": "R009",
		"reason_code": "NO_PRE_COMMIT_HOOK",
		"message": "lefthook.yml must define a pre-commit hook",
	}
}

deny contains finding if {
	not input.lefthook.has_pre_push
	finding := {
		"id": "R010",
		"reason_code": "NO_PRE_PUSH_HOOK",
		"message": "lefthook.yml must define a pre-push hook",
	}
}

# --- R011: the branch guard runs before work ----------------------------------
# The server ruleset rejects a PUSH to a protected branch, but by then the
# commit exists and has to be unwound.
deny contains finding if {
	not input.lefthook.pre_commit_guards_branch
	finding := {
		"id": "R011",
		"reason_code": "NO_BRANCH_GUARD",
		"message": "pre-commit must run start:check before any other job",
	}
}

# --- R012: the interpreter is pinned and agreed everywhere --------------------
# Spark Connect requires the client and server to share a Python minor version.
# The failure surfaces when a UDF dies, not when the version changed.
deny contains finding if {
	input.python.dotfile_version != input.python.pyproject_version
	finding := {
		"id": "R012",
		"reason_code": "INTERPRETER_DRIFT",
		"message": sprintf(
			".python-version is %q but pyproject.toml requires %q",
			[input.python.dotfile_version, input.python.pyproject_version],
		),
	}
}

# --- R013/R014: the test suite cannot pass vacuously --------------------------
# --strict-markers turns a typo'd marker into an error rather than a silently
# created new one; xfail_strict turns a passing xfail into a failure, so a fixed
# bug cannot leave a test asserting nothing behind.
deny contains finding if {
	some required in ["--strict-markers", "--strict-config"]
	not required in input.pytest.addopts
	finding := {
		"id": "R013",
		"reason_code": "TEST_GATE_PERMISSIVE",
		"message": sprintf("pytest addopts must include %q", [required]),
	}
}

deny contains finding if {
	not input.pytest.xfail_strict
	finding := {
		"id": "R014",
		"reason_code": "XFAIL_NOT_STRICT",
		"message": "xfail_strict must be true; a passing xfail is a failure",
	}
}
