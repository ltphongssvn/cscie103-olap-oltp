# src/cscie103_olap_oltp/git/pr.py
"""Open a pull request, verify its checks, and merge it synchronously.

WHY THIS BLOCKS RATHER THAN RETURNING
The obvious design enables auto-merge and returns immediately, reporting state
BLOCKED, on the reasoning that a laptop-side loop reimplements state GitHub
already owns. That is wrong about who pays: the cost moves to the person, who
watches the Actions tab and confirms the merge by hand on every pull request.

WHY AUTO-MERGE IS NOT USED AT ALL
GitHub changed it in March 2026: auto-merge can no longer be ENABLED until every
requirement is already met, returning HTTP 422. That leaves it useful only where
it is unnecessary. The merge endpoint is called directly instead -- synchronous,
returning a real result rather than a promise.

AN INTENT IS NOT AN OUTCOME
That endpoint verifies nothing -- GitHub documents that it does not care about
failing checks -- and a 200 can carry `merged: false`. So this observes,
verifies, acts, and then observes the RESULT:

    OBSERVE   wait for checks to register, watch them to completion, then wait
              for the rollup's conclusions to settle
    VERIFY    every conclusion acceptable, none still running, and not zero
    ACT       merge synchronously
    OBSERVE   parse the response and confirm merged is true

WHY SETTLING IS A SEPARATE STEP FROM WATCHING
`gh pr checks --watch` returns when each CHECK RUN reaches a terminal state. The
pull request's statusCheckRollup is a different view and lags it: querying
immediately after --watch reports "All checks were successful" while the rollup
still returns conclusion "" for the same check, and the merge is refused as
"still running". Two views of the same fact, updated at different times.
"""

from __future__ import annotations

import subprocess
import sys
import time
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from cscie103_olap_oltp.git.env import git
from cscie103_olap_oltp.policy.snapshot import REPO_ROOT

PROTECTED = frozenset({"develop", "main"})
INTEGRATION_BRANCH = "develop"

# HOW LONG EACH PHASE MAY TAKE, AND WHY EACH NUMBER IS WHAT IT IS.
#
# Registration is fast: a workflow appears within seconds of a push.
CHECKS_APPEAR_TIMEOUT = 120

# THE GATE ITSELF. Sized from the sibling project's measurement rather than
# preference: its quality gate ran 1m07s before Nix was on the runner and 9m37s
# after, because a cold runner downloads the installer and then realises a
# devShell it has never built. Twenty minutes leaves room for a cold cache while
# staying far below GitHub's six-hour job limit, so a genuinely hung workflow
# still fails here rather than being waited on forever.
CHECKS_COMPLETE_TIMEOUT = 1200

# ROLLUP LAG ONLY -- seconds, not minutes. In the sibling project this budget
# silently did CHECKS_COMPLETE_TIMEOUT's job because the watch phase's exit code
# was discarded, and the resulting timeout looked like the bug when the
# discarded exit code was the bug.
CHECKS_SETTLE_TIMEOUT = 120

POLL_INTERVAL = 5


def gh(*args: str, check: bool = True) -> str:
    """Run gh in the repository, or fail loudly.

    S603 IS SUPPRESSED NARROWLY: `*args` makes the list computed, which is what
    ruff flags. Every call site passes literal subcommands; there is no shell.
    """
    result = subprocess.run(  # noqa: S603
        ["gh", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    if check and result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
        raise SystemExit(f"gh {' '.join(args)} failed")
    return result.stdout


class CheckConclusion(StrEnum):
    """Every conclusion GitHub reports.

    DEFINED BEFORE Check, WHICH ANNOTATES A FIELD WITH IT. `from __future__
    import annotations` makes that annotation lazy, so a forward reference
    parses and passes both lint and mypy -- then fails when pydantic resolves it
    at class creation. Ordering is load-bearing and no static gate catches it.

    AN UNRECOGNISED VALUE FAILS VALIDATION, WHICH IS THE INTENT. REST API
    version 2026-03-10 removed merge_commit_sha from pull request responses, and
    automation that reads a missing field as null keeps going -- surfacing days
    later as a deploy to the wrong commit.
    """

    SUCCESS = "SUCCESS"
    NEUTRAL = "NEUTRAL"
    SKIPPED = "SKIPPED"
    FAILURE = "FAILURE"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    STARTUP_FAILURE = "STARTUP_FAILURE"
    STALE = "STALE"


# A SKIPPED CHECK HAS NOT FAILED. Treating SKIPPED as failure blocks every merge
# where a path filter excluded a job, and a gate that refuses correct work is a
# gate people route around.
PASSING_CONCLUSIONS = frozenset(
    {CheckConclusion.SUCCESS, CheckConclusion.NEUTRAL, CheckConclusion.SKIPPED}
)


class Check(BaseModel):
    """One status check on the pull request.

    extra="ignore" RATHER THAN "forbid": gh returns a dozen fields in
    statusCheckRollup -- startedAt, detailsUrl, workflowName -- and forbidding
    them would fail on data this deliberately does not read. MergeResult
    forbids, because its three fields ARE the contract.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    name: str = ""
    conclusion: CheckConclusion | None = None

    @field_validator("conclusion", mode="before")
    @classmethod
    def empty_means_pending(cls, value: object) -> object:
        """gh reports a pending check as "", not null.

        Normalised so pending has ONE representation, while an unrecognised
        non-empty value still fails validation.
        """
        return None if value == "" else value


class PullRequest(BaseModel):
    """The pull request as gh reports it, VALIDATED AT THE BOUNDARY.

    `gh --json` is GitHub's API response relayed through a CLI: external service
    output crossing a trust boundary, which gets a model, validated once and
    trusted after.
    """

    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    number: int
    state: str
    merge_state: str = Field(default="", alias="mergeStateStatus")
    url: str = ""
    checks: tuple[Check, ...] = Field(default=(), alias="statusCheckRollup")

    @property
    def is_merged(self) -> bool:
        return self.state == "MERGED"

    @property
    def is_terminal(self) -> bool:
        return self.state in {"MERGED", "CLOSED"}

    @property
    def checks_settled(self) -> bool:
        """Every registered check has reported a conclusion.

        `bool(self.checks)` IS LOAD-BEARING. `all()` over an empty sequence is
        True, so without it a pull request with no checks reports as fully
        concluded -- the vacuous-pass shape this project keeps removing.
        """
        return bool(self.checks) and all(c.conclusion is not None for c in self.checks)


class MergeResult(BaseModel):
    """What the merge endpoint actually returned.

    strict AND extra="forbid" DELIBERATELY. These three fields are GitHub's
    documented PullRequestMergeResult and all are required; a field appearing or
    vanishing means the contract moved, and this is where that should be loud.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    merged: bool
    sha: str
    message: str


def pr_state() -> PullRequest:
    raw = gh("pr", "view", "--json", "number,state,mergeStateStatus,statusCheckRollup,url")
    # model_validate_json, not model_validate(json.loads(...)): pydantic-core
    # decodes and validates in a single Rust pass.
    return PullRequest.model_validate_json(raw)


def wait_for_checks_to_register(deadline: float) -> bool:
    """Checks do not exist the instant a pull request is created.

    `gh pr checks --watch` exits IMMEDIATELY when none have registered -- it
    watches EXISTING checks. Calling it straight away prints "no checks
    reported" and returns a non-answer that reads like a verdict.
    """
    while time.monotonic() < deadline:
        checks = pr_state().checks
        if checks:
            print("checks registered: " + ", ".join(check.name for check in checks))
            return True
        time.sleep(POLL_INTERVAL)
    return False


def watch_until_complete(reference: list[str]) -> None:
    """Block until every check run reaches a terminal state.

    ITS EXIT CODE IS READ. In the sibling project this ran with the result
    discarded, so an early return was invisible and the settle phase silently
    absorbed the whole wait on a budget meant for lag.

    EXIT 8 IS THE ONE SUCCESS-ADJACENT FAILURE: `gh pr checks` documents it for
    "checks pending", which under --watch means the watch ended before they
    finished -- a timeout, not a verdict. It is reported and the caller proceeds
    to the settle phase, which is bounded and refuses a pending check anyway.

    Any OTHER non-zero code is a failing check, which verify_checks reports with
    the specific names. Raising here would lose that detail.
    """
    result = subprocess.run(  # noqa: S603
        ["gh", "pr", "checks", *reference, "--watch", "--interval", "10"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
        timeout=CHECKS_COMPLETE_TIMEOUT,
    )

    print(result.stdout.strip() or "(watch produced no output)")

    if result.returncode == 8:
        print(
            "watch ended with checks still pending; "
            "the settle phase will decide rather than assuming.",
            file=sys.stderr,
        )
    elif result.returncode != 0 and result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)


def wait_for_checks_to_settle(deadline: float) -> PullRequest:
    """Wait for the ROLLUP to report conclusions, after --watch has returned.

    THIS BUDGET IS FOR LAG, NOT FOR THE GATE. Exhausting it means the rollup
    never caught up, which is a real anomaly -- so the message says what was
    still pending rather than suggesting a longer wait.
    """
    observed = pr_state()
    while time.monotonic() < deadline:
        if observed.is_terminal or observed.checks_settled:
            return observed
        unsettled = [c.name for c in observed.checks if c.conclusion is None]
        print(f"waiting for conclusions: {', '.join(unsettled)}")
        time.sleep(POLL_INTERVAL)
        observed = pr_state()

    still_pending = ", ".join(c.name for c in observed.checks if c.conclusion is None)
    raise SystemExit(
        f"the rollup did not report conclusions for {still_pending} within "
        f"{CHECKS_SETTLE_TIMEOUT}s of the watch returning. That is lag, not a "
        "slow gate -- check whether the run itself is still in progress."
    )


def verify_checks(checks: tuple[Check, ...]) -> None:
    """Refuse unless every check concluded acceptably.

    THIS IS THE AUTHORIZATION STEP. The merge endpoint verifies nothing, so
    without this, merging synchronously would be strictly worse than the
    auto-merge it replaces.

    FAIL CLOSED: no checks at all is a refusal, not a pass. An empty rollup
    means either the workflow never ran or the query returned nothing, and
    treating "nothing to object to" as approval is how a gate becomes
    decoration.
    """
    if not checks:
        raise SystemExit("merge refused: no checks reported at all")

    unfinished = [c.name for c in checks if c.conclusion is None]
    if unfinished:
        raise SystemExit(f"merge refused: checks still running: {', '.join(unfinished)}")

    failing = [c.name for c in checks if c.conclusion not in PASSING_CONCLUSIONS]
    if failing:
        raise SystemExit(f"merge refused: unacceptable checks: {', '.join(failing)}")


def execute_merge(pr_number: int) -> MergeResult:
    """Merge synchronously and return the PARSED result.

    405 means not mergeable; 409 means the head branch moved since the checks
    ran. Both are real outcomes, and 409 is the one commonly missed.
    """
    response = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "gh",
            "api",
            f"repos/{{owner}}/{{repo}}/pulls/{pr_number}/merge",
            "-X",
            "PUT",
            "-f",
            "merge_method=merge",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    if response.returncode != 0:
        raise SystemExit(f"merge request failed: {response.stderr.strip()}")

    return MergeResult.model_validate_json(response.stdout)


def merge_when_green(pr_number: int | None = None) -> MergeResult | None:
    """Observe, verify, act, observe the result."""
    reference = [str(pr_number)] if pr_number is not None else []

    if not wait_for_checks_to_register(time.monotonic() + CHECKS_APPEAR_TIMEOUT):
        raise SystemExit(f"no checks registered within {CHECKS_APPEAR_TIMEOUT}s")

    watch_until_complete(reference)

    observed = wait_for_checks_to_settle(time.monotonic() + CHECKS_SETTLE_TIMEOUT)
    if observed.is_terminal:
        print(f"PR #{observed.number} is already {observed.state}")
        return None

    verify_checks(observed.checks)

    result = execute_merge(observed.number)
    if not result.merged:
        raise SystemExit(f"PR #{observed.number} was not merged: {result.message}")

    print(f"merged {result.sha[:12]}: {result.message}")
    return result


def main() -> int:
    branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=REPO_ROOT).stdout.strip()
    if branch in PROTECTED:
        raise SystemExit(f"refusing: on protected branch {branch!r}; work on a feature branch")

    push = git("push", "-u", "origin", branch, cwd=REPO_ROOT)
    if push.returncode != 0:
        print(push.stderr.strip(), file=sys.stderr)
        raise SystemExit("push failed")

    # AN EXISTING PULL REQUEST IS THE NORMAL CASE, NOT A FAILURE. Pushing a fix
    # to a branch that already has one is what happens after every correction;
    # calling `gh pr create` unconditionally kills the task before it watches
    # anything.
    existing = gh(
        "pr",
        "list",
        "--head",
        branch,
        "--state",
        "open",
        "--json",
        "number",
        "--jq",
        ".[0].number // empty",
    ).strip()

    if existing:
        print(f"reusing open PR #{existing}")
    else:
        gh("pr", "create", "--base", INTEGRATION_BRANCH, "--head", branch, "--fill")

    print(f"opened {pr_state().url}\n")
    merge_when_green()

    final = pr_state()
    if not final.is_merged:
        raise SystemExit(f"PR #{final.number} is {final.state}, not MERGED.")

    print(f"\nPR #{final.number} merged. next: mise run sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
