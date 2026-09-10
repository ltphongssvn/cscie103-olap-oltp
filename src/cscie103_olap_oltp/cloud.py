# src/cscie103_olap_oltp/cloud.py
"""Keep the Lightning Studio's clone in step with GitHub.

    laptop  --push-->  GitHub  <--pull--  Lightning Studio

The repository is the source of truth; the Studio is only where execution
happens. Code travels via git and never by copying files, so the three copies
cannot drift -- and "in sync" becomes a fact anyone can verify with one command
rather than a belief about what was last uploaded.

`lightning cp` is the rejected alternative. It works, and produces exactly the
drift this design prevents: an uploaded file has no commit, no history, and no
way to say which version the Studio is running.

HOW THE REMOTE URL REACHED ITS THIRD DESIGN, BECAUSE BOTH FAILURES WERE REAL.

  1. HARDCODED. The PII gate refused the push: an SSH remote of the form
     user@host is structurally an email address, and the scanner cannot know it
     is a git remote. The rule is to eliminate at source rather than allowlist,
     since a suppressed finding makes the scanner decoration.

  2. PASSED THROUGH from `git remote get-url`. The literal went away and CI
     failed: actions/checkout configures origin over HTTPS, so the runner
     derived HTTPS while the laptop derived SSH. Worse than a broken test, it
     meant the transport the Studio received depended on which machine ran the
     command.

  3. PARSED AND REBUILT, which is what this file does. The remote is decomposed
     into host, owner and repo -- accepting either form -- and the Studio's URL
     is CONSTRUCTED as SSH. Git remains the single source of truth for WHICH
     repository; this module decides the TRANSPORT, identically everywhere.

That is the documented pattern for repository identity: parse a remote into its
components and rebuild it in the form the caller needs, rather than depending on
the form that happens to be configured.

WHY SSH AT ALL, GIVEN THIS REPOSITORY IS PUBLIC

HTTPS would need no credential for the pull direction. It is rejected for fleet
consistency: the sibling project authenticates over SSH, and a fleet where each
repository uses a different transport is one where nobody can predict what a
given machine can reach.

GitHub documents four options -- agent forwarding, HTTPS with OAuth tokens,
deploy keys, machine users -- and agent forwarding is the one that needs no new
keys, no key management, and stores NOTHING on the server, so a compromised
Studio leaves no credential to hunt down and revoke. Its stated limitation, that
a human must SSH in, is not one we have: sync is human-initiated by design.

THE 2026 HARDENINGS ARE APPLIED, NOT ASSUMED
  scoped     ForwardAgent is set per-invocation for one host, never globally --
             a host you forward to can request signatures for any key the agent
             holds.
  short      every operation is one-shot and non-interactive, so the exposure
             window is the length of a pull rather than a session left open.
"""

from __future__ import annotations

import re
import subprocess
import sys

from cscie103_olap_oltp.git.env import scrubbed_env

# THE SSH HOST ALIAS Lightning writes into ~/.ssh/config. Using the alias rather
# than a hostname keeps the identity file, keepalives and host-key policy in one
# place that the CLI maintains.
STUDIO_HOST = "serene-volhard-183"

TEAMSPACE = "ltphongssvn/deploy-model-project"

# ~/ltphongssvn RESOLVES INTO /teamspace/studios/this_studio, WHICH PERSISTS.
# A Studio's ordinary home directory does not survive a restart, so a clone
# placed outside /teamspace silently vanishes -- and the failure looks like the
# clone never happened rather than like storage that was never persistent.
STUDIO_REPO_PATH = "~/ltphongssvn/cscie103-olap-oltp"

INTEGRATION_BRANCH = "develop"

# SPLIT FROM THE HOST DELIBERATELY. Written adjacent to a hostname this is an
# email address to any scanner, and the PII gate is right to say so. Kept apart,
# the SSH form is assembled at the one place that needs it.
SSH_USER = "git"

# BOTH FORMS GIT ACCEPTS, and both appear in this project's own environments:
# scp-style on a laptop, https on a GitHub Actions runner.
_SCP_STYLE = re.compile(r"^[^@/]+@(?P<host>[^:]+):(?P<owner>.+)/(?P<repo>[^/]+?)(?:\.git)?$")
_URL_STYLE = re.compile(
    r"^(?:https?|ssh|git)://(?:[^@/]+@)?(?P<host>[^/:]+)(?::\d+)?/"
    r"(?P<owner>.+)/(?P<repo>[^/]+?)(?:\.git)?$"
)


def parse_remote(url: str) -> tuple[str, str, str]:
    """Decompose a git remote into (host, owner, repo).

    ACCEPTS EITHER FORM, because this project genuinely sees both: a laptop
    configures scp-style SSH and actions/checkout configures HTTPS. A parser
    that handled only one would work on one machine and fail on the other --
    which is precisely the bug this replaced.

    RAISES ON ANYTHING ELSE. A silent fallback would hand the Studio a URL
    pointing at something that is not this repository.
    """
    for pattern in (_SCP_STYLE, _URL_STYLE):
        match = pattern.match(url.strip())
        if match:
            return match["host"], match["owner"], match["repo"]
    raise ValueError(f"could not parse git remote: {url!r}")


def studio_remote_url(origin: str) -> str:
    """The URL the Studio clones from: always SSH, whatever `origin` is.

    THIS IS WHERE THE TRANSPORT DECISION LIVES, and putting it in one function
    is what makes it identical on every machine. The alternative -- letting the
    ambient clone decide -- gave the Studio HTTPS from CI and SSH from a laptop.
    """
    host, owner, repo = parse_remote(origin)
    return f"{SSH_USER}@{host}:{owner}/{repo}.git"


def origin_url() -> str:
    """What git says `origin` is, on whichever machine is asking.

    ASKED OF GIT RATHER THAN DECLARED. The remote is a fact git already stores,
    so a literal here would be a second copy able to disagree with
    `git remote -v`.
    """
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        env=scrubbed_env(),
    )
    if result.returncode != 0:
        raise RuntimeError(
            "no `origin` remote is configured, so there is nothing to clone "
            f"from:\n{result.stderr.strip() or '(no stderr)'}"
        )
    return result.stdout.strip()


def ssh_command() -> list[str]:
    """The ssh invocation, with forwarding scoped to this host only.

    -T because nothing here needs a TTY: every script is piped in on stdin and
    runs to completion. It also keeps the session non-interactive, which is what
    bounds the exposure window.
    """
    return ["ssh", "-T", "-o", "ForwardAgent=yes", STUDIO_HOST, "bash", "-s"]


def clone_script() -> str:
    """Create the Studio's clone if it is absent. Idempotent.

    A BOOTSTRAP THAT ONLY WORKS ONCE IS ONE NOBODY RE-RUNS, and a bootstrap
    nobody re-runs cannot reconcile anything. Running this against a Studio that
    already has the clone reports the fact and changes nothing.
    """
    return f"""
set -euo pipefail

mkdir -p ~/ltphongssvn

if [ -d {STUDIO_REPO_PATH}/.git ]; then
  echo "ok      clone already present"
else
  git clone {studio_remote_url(origin_url())} {STUDIO_REPO_PATH}
  echo "created clone"
fi

cd {STUDIO_REPO_PATH}

# A FRESH CLONE LANDS ON THE DEFAULT BRANCH, which is develop here, so the
# switch is usually a no-op. The fallback covers the case where a local branch
# of that name does not exist yet and has to be created from the remote.
if ! git switch {INTEGRATION_BRANCH} 2>/dev/null; then
  git switch -c {INTEGRATION_BRANCH} origin/{INTEGRATION_BRANCH}
fi

git log --oneline -1
"""


def pull_script() -> str:
    """Fast-forward the Studio to origin/develop, and report where it landed.

    --ff-only IS THE IMPORTANT FLAG. A merge on the Studio would create a commit
    that exists nowhere else -- a third head, which is precisely the drift this
    design prevents. Fast-forward-only turns divergence into a loud failure.

    --prune because branches deleted on merge otherwise linger forever, and
    `git branch -a` stops meaning anything.

    THE RESULT IS A COMMIT HASH, NOT A SUCCESS MESSAGE. The question is whether
    three copies are the same, so the answer has to be comparable.
    """
    return f"""
set -euo pipefail

cd {STUDIO_REPO_PATH}
git fetch origin --prune
git switch {INTEGRATION_BRANCH}
git pull --ff-only

echo
echo "studio now at:"
git log --oneline -1
"""


def run_on_studio(script: str) -> int:
    """Execute a script on the Studio, streaming its output.

    NOT capture_output. This is human-initiated and the output is the point -- a
    clone takes a while, and a silent command that prints everything at the end
    reads as a hang.

    S603 IS SUPPRESSED NARROWLY: the argument list comes from ssh_command(),
    built from module constants with nothing interpolated from user input.
    """
    result = subprocess.run(  # noqa: S603
        ssh_command(),
        input=script,
        text=True,
        check=False,
    )
    return result.returncode


def main() -> int:
    match sys.argv[1:]:
        case ["clone"]:
            return run_on_studio(clone_script())
        case ["pull"]:
            return run_on_studio(pull_script())
        case other:
            raise SystemExit(f"usage: python -m cscie103_olap_oltp.cloud clone|pull  ({other})")


if __name__ == "__main__":
    raise SystemExit(main())


# --- THE STUDIO NEVER PUSHES -------------------------------------------------
#
# There is no push verb, and its absence is a decision rather than an omission.
# Work is authored on the laptop, reviewed through a pull request, and gated by
# CI. A Studio that could push would bypass all three -- and with a forwarded
# agent it would push as YOU, with no way to tell afterwards which machine the
# commit came from.
#
# If the Studio ever needs to author work, the honest answer is the GitFlow
# everything else uses: a feature branch, a pull request, and the quality gate.
#
# TEAMSPACE is recorded for the day a task needs the Lightning CLI -- studio
# start/stop, or cp for artifacts git should not carry. Sync itself needs only
# ssh and git.
