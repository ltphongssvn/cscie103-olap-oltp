# policies/repo/repo_test.rego
# EVERY RULE IS TESTED BOTH WAYS, AND THAT PAIRING IS THE POINT.
#
# A rule tested only on its denying input passes even when it denies
# EVERYTHING -- the always-true condition that makes a gate look strict while
# refusing valid work. A rule tested only on its allowing input passes even when
# it denies NOTHING. Each rule below gets one of each.
#
# THE COMPLIANT FIXTURE IS THE BASELINE, and every negative test is that fixture
# with exactly one field changed. Constructing a separate bad input per test
# would let a fixture drift into passing for the wrong reason.
#
# TWO REGO CONSTRAINTS SHAPED THIS FILE, both found by running the gate rather
# than by assuming:
#
#   `with` MODIFIES A RULE BODY, NOT AN EXPRESSION. So
#   `every f in repo.deny with input as x { ... }` is a parse error. Absence is
#   asserted by collecting ids into a set and checking membership instead.
#
#   `with` IS ONLY "IN TEST CONTEXT" INSIDE A RULE NAMED test_. Regal's
#   with-outside-test-context refuses it anywhere else, including a helper rule
#   or function -- and it is right to: `with` defeats OPA's rule-result caching,
#   so a shared helper would re-evaluate the whole policy per call. The
#   documented alternative (pass the value as a function argument) does not
#   apply, because repo.deny reads `input` by design. So the comprehension is
#   written inline in each test. The repetition is the price of the rule, and
#   the rule is correct.
package repo_test

import data.repo
import rego.v1

# THE SHAPE THIS REPOSITORY ACTUALLY HAS. If a real gate ever disagrees with
# this fixture, one of the two is wrong and the mismatch is the finding.
compliant := {
	"mise": {
		"has_tools_block": false,
		"task_shell_enters_devshell": true,
		"policy_gate_fails_on_empty": true,
		"tasks": [
			{"name": "lint", "run": "uv run ruff check .", "is_multiline": false},
			{
				"name": "types",
				"run": "set -euo pipefail\nuv run mypy --platform darwin src tests",
				"is_multiline": true,
			},
		],
	},
	"workflow": {
		"has_permissions": true,
		"restates_gate_steps": false,
		"uses": [{"ref": "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09"}],
	},
	"lefthook": {
		"has_pre_commit": true,
		"has_pre_push": true,
		"pre_commit_guards_branch": true,
	},
	"python": {"dotfile_version": "3.12.3", "pyproject_version": "3.12.3"},
	"git": {
		"origin_is_ssh": true,
		"origin_host": "example.test",
		"is_ephemeral_checkout": false,
	},
	"pytest": {
		"addopts": ["--strict-markers", "--strict-config", "-ra"],
		"xfail_strict": true,
	},
}

# THE BASELINE WITH ONE SECTION PATCHED. A pure function with no `with`, so it
# is legal outside test context and a failing test names the field that differs.
mutate(section, patch) := object.union(
	compliant,
	{section: object.union(compliant[section], patch)},
)

# --- The baseline itself ------------------------------------------------------

test_compliant_input_is_allowed if {
	repo.allow with input as compliant
}

test_compliant_input_has_no_denials if {
	ids := {f.id | some f in repo.deny} with input as compliant
	count(ids) == 0
}

# DEFAULT DENY, PROVEN. An empty input must not fall through to permission --
# this is the test that catches `default allow := true` being introduced later.
test_empty_input_is_denied if {
	not repo.allow with input as {}
}

# --- R001 ---------------------------------------------------------------------

test_r001_denies_tools_block if {
	cfg := mutate("mise", {"has_tools_block": true})
	ids := {f.id | some f in repo.deny} with input as cfg
	"R001" in ids
}

test_r001_silent_without_tools_block if {
	ids := {f.id | some f in repo.deny} with input as compliant
	not "R001" in ids
}

# --- R002 ---------------------------------------------------------------------

test_r002_denies_unpinned_task_shell if {
	cfg := mutate("mise", {"task_shell_enters_devshell": false})
	ids := {f.id | some f in repo.deny} with input as cfg
	"R002" in ids
}

test_r002_silent_when_shell_pinned if {
	ids := {f.id | some f in repo.deny} with input as compliant
	not "R002" in ids
}

# --- R003 ---------------------------------------------------------------------

test_r003_denies_bare_python3 if {
	cfg := mutate("mise", {"tasks": [{
		"name": "bad",
		"run": "python3 scripts/thing.py",
		"is_multiline": false,
	}]})
	ids := {f.id | some f in repo.deny} with input as cfg
	"R003" in ids
}

# `uv run python` MUST NOT MATCH. The regex excludes a preceding word character
# or hyphen for exactly this reason, and without this test that exclusion could
# be deleted while every other test still passed.
test_r003_allows_uv_run_python if {
	cfg := mutate("mise", {"tasks": [{
		"name": "ok",
		"run": "uv run python scripts/thing.py",
		"is_multiline": false,
	}]})
	ids := {f.id | some f in repo.deny} with input as cfg
	not "R003" in ids
}

# --- R004 ---------------------------------------------------------------------

test_r004_denies_multiline_without_errexit if {
	cfg := mutate("mise", {"tasks": [{
		"name": "loose",
		"run": "opa fmt policies/\nopa test policies/",
		"is_multiline": true,
	}]})
	ids := {f.id | some f in repo.deny} with input as cfg
	"R004" in ids
}

# A SINGLE-COMMAND TASK IS EXEMPT. Requiring errexit there would flag every
# one-line task in the file, which is the always-denying failure mode.
test_r004_exempts_single_command_task if {
	ids := {f.id | some f in repo.deny} with input as compliant
	not "R004" in ids
}

# --- R005 ---------------------------------------------------------------------

test_r005_denies_vacuous_policy_gate if {
	cfg := mutate("mise", {"policy_gate_fails_on_empty": false})
	ids := {f.id | some f in repo.deny} with input as cfg
	"R005" in ids
}

test_r005_silent_when_fail_on_empty if {
	ids := {f.id | some f in repo.deny} with input as compliant
	not "R005" in ids
}

# --- R006 ---------------------------------------------------------------------

test_r006_denies_tag_pinned_action if {
	cfg := mutate("workflow", {"uses": [{"ref": "actions/checkout@v5"}]})
	ids := {f.id | some f in repo.deny} with input as cfg
	"R006" in ids
}

# A SHORT SHA IS NOT A PIN. Seven hex characters look like a commit and are
# ambiguous; the rule requires all forty.
test_r006_denies_short_sha if {
	cfg := mutate("workflow", {"uses": [{"ref": "actions/checkout@fbc6f39"}]})
	ids := {f.id | some f in repo.deny} with input as cfg
	"R006" in ids
}

test_r006_allows_full_sha if {
	ids := {f.id | some f in repo.deny} with input as compliant
	not "R006" in ids
}

# --- R007 ---------------------------------------------------------------------

test_r007_denies_default_permissions if {
	cfg := mutate("workflow", {"has_permissions": false})
	ids := {f.id | some f in repo.deny} with input as cfg
	"R007" in ids
}

test_r007_silent_when_declared if {
	ids := {f.id | some f in repo.deny} with input as compliant
	not "R007" in ids
}

# --- R008 ---------------------------------------------------------------------

test_r008_denies_duplicated_gate_list if {
	cfg := mutate("workflow", {"restates_gate_steps": true})
	ids := {f.id | some f in repo.deny} with input as cfg
	"R008" in ids
}

test_r008_silent_when_single_command if {
	ids := {f.id | some f in repo.deny} with input as compliant
	not "R008" in ids
}

# --- R009, R010, R011 ---------------------------------------------------------

test_r009_denies_missing_pre_commit if {
	cfg := mutate("lefthook", {"has_pre_commit": false})
	ids := {f.id | some f in repo.deny} with input as cfg
	"R009" in ids
}

test_r010_denies_missing_pre_push if {
	cfg := mutate("lefthook", {"has_pre_push": false})
	ids := {f.id | some f in repo.deny} with input as cfg
	"R010" in ids
}

test_r011_denies_missing_branch_guard if {
	cfg := mutate("lefthook", {"pre_commit_guards_branch": false})
	ids := {f.id | some f in repo.deny} with input as cfg
	"R011" in ids
}

test_hooks_silent_when_all_present if {
	ids := {f.id | some f in repo.deny} with input as compliant
	count(ids & {"R009", "R010", "R011"}) == 0
}

# --- R012 ---------------------------------------------------------------------

test_r012_denies_interpreter_drift if {
	cfg := mutate("python", {"pyproject_version": "3.11.9"})
	ids := {f.id | some f in repo.deny} with input as cfg
	"R012" in ids
}

test_r012_silent_when_versions_agree if {
	ids := {f.id | some f in repo.deny} with input as compliant
	not "R012" in ids
}

# --- R013, R014 ---------------------------------------------------------------

test_r013_denies_missing_strict_markers if {
	cfg := mutate("pytest", {"addopts": ["--strict-config", "-ra"]})
	ids := {f.id | some f in repo.deny} with input as cfg
	"R013" in ids
}

test_r014_denies_permissive_xfail if {
	cfg := mutate("pytest", {"xfail_strict": false})
	ids := {f.id | some f in repo.deny} with input as cfg
	"R014" in ids
}

test_pytest_rules_silent_when_strict if {
	ids := {f.id | some f in repo.deny} with input as compliant
	count(ids & {"R013", "R014"}) == 0
}

# --- Findings are data, not prose ---------------------------------------------
# A denial without a stable id and reason code cannot be aggregated, queried or
# explained later. This is what makes Violations as Data true rather than
# aspirational.
test_every_finding_carries_id_and_reason_code if {
	findings := repo.deny with input as {}
	count(findings) > 0
	every f in findings {
		f.id != ""
		f.reason_code != ""
		f.message != ""
	}
}

# --- R015 ---------------------------------------------------------------------

test_r015_denies_https_remote if {
	# THE FORM actions/checkout CONFIGURES, and the form the Lightning Studio's
	# first clone acquired while the sibling project sat on SSH. A Python
	# assertion held that convention and nothing refused when it broke.
	cfg := mutate("git", {"origin_is_ssh": false})
	ids := {f.id | some f in repo.deny} with input as cfg
	"R015" in ids
}

test_r015_denies_missing_origin if {
	# ABSENCE IS NOT A PASS. A clone with no remote cannot satisfy the rule, so
	# it refuses rather than evaluating against undefined.
	cfg := mutate("git", {"origin_is_ssh": false, "origin_host": ""})
	ids := {f.id | some f in repo.deny} with input as cfg
	"R015" in ids
}

test_r015_silent_when_remote_is_ssh if {
	ids := {f.id | some f in repo.deny} with input as compliant
	not "R015" in ids
}

test_r015_names_the_host_it_found if {
	# A refusal saying only "not SSH" sends the reader back to a terminal to ask
	# git what the remote actually is.
	cfg := mutate("git", {"origin_is_ssh": false})
	messages := {f.message | some f in repo.deny; f.id == "R015"} with input as cfg
	some message in messages
	contains(message, "example.test")
}

test_r015_exempts_an_ephemeral_checkout if {
	# A CI RUNNER CANNOT CHOOSE ITS TRANSPORT. actions/checkout configures HTTPS
	# and authenticates with a scoped, short-lived token; requiring SSH there
	# would mean storing a key on a runner, which is what the rule prevents.
	cfg := mutate("git", {"origin_is_ssh": false, "is_ephemeral_checkout": true})
	ids := {f.id | some f in repo.deny} with input as cfg
	not "R015" in ids
}

test_r015_still_denies_a_durable_https_clone if {
	# THE EXEMPTION MUST NOT DISABLE THE RULE. Without this test, marking every
	# checkout ephemeral would silently retire R015 while the suite stayed green.
	cfg := mutate("git", {"origin_is_ssh": false, "is_ephemeral_checkout": false})
	ids := {f.id | some f in repo.deny} with input as cfg
	"R015" in ids
}
