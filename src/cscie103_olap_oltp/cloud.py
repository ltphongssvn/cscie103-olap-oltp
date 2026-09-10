# src/cscie103_olap_oltp/cloud.py
"""Keep the Lightning Studio's clone in step with GitHub.

THE ARCHITECTURE, WHICH IS THE WHOLE POINT.

    laptop  --push-->  GitHub  <--pull--  Lightning Studio

The repository is the source of truth; the Studio is only where execution
happens. Code travels via git and never by copying files, so the three copies
cannot drift -- and "in sync" becomes a fact anyone can verify with one command
rather than a belief about what was last uploaded.

THE REJECTED ALTERNATIVE IS `lightning cp`. It works, and it produces exactly
the drift this design prevents: an uploaded file has no commit, no history, and
no way to answer which version the Studio is running.

WHY SSH AND AGENT FORWARDING RATHER THAN HTTPS

This repository is public, so HTTPS would need no credential at all for the pull
direction -- genuinely simpler, and rejected anyway. The sibling project
authenticates over SSH, and a fleet where each repository uses a different
transport is one where nobody can predict what a given machine can reach.
Consistency is the property being bought.

GitHub's own guidance supports the choice among its four documented options
(agent forwarding, HTTPS with OAuth tokens, deploy keys, machine users): agent
forwarding needs no new keys and no key management, and stores NOTHING on the
server -- so a compromised Studio leaves no credential to hunt down and revoke.
Its documented limitation, that a human must SSH in and automated processes
cannot, is not one we have: sync is human-initiated by design.

A deploy key would mean one key per repository per environment, living on the
server, usually without a passphrase -- a persistent credential someone has to
remember to rotate. A machine user is a whole second account. Both reintroduce
the key management that forwarding avoids.

THE 2026 HARDENINGS ARE APPLIED, NOT ASSUMED
  scoped     ForwardAgent is set per-invocation for one host, never globally --
             a host you forward to can request signatures for any key the agent
             holds, so the blast radius is every system that key reaches.
  short      every operation is a one-shot non-interactive command, so the
             exposure window is the length of a pull rather than a session
             someone left open.
"""

from __future__ import annotations

import subprocess
import sys

from cscie103_olap_oltp.git.env import scrubbed_env

# THE SSH HOST ALIAS Lightning writes into ~/.ssh/config. Using the alias rather
# than a hostname means the identity file, keepalives and host-key policy stay
# in one place that the CLI maintains.
STUDIO_HOST = "serene-volhard-183"

TEAMSPACE = "ltphongssvn/deploy-model-project"

# ~/ltphongssvn RESOLVES INTO /teamspace/studios/this_studio, WHICH PERSISTS.
# A Studio's ordinary home directory does not survive a restart, so a clone
# placed outside /teamspace silently vanishes -- and the failure looks like the
# clone never happened rather than like storage that was never persistent.
STUDIO_REPO_PATH = "~/ltphongssvn/cscie103-olap-oltp"

INTEGRATION_BRANCH = "develop"


def remote_url() -> str:
    """Where this repository lives, asked of git rather than declared here.

    NOT A CONSTANT, AND THE REASON IS TWOFOLD.

    The remote URL is a fact git already stores. A literal here would be a
    second copy that can disagree with `git remote -v` -- the same duplication
    this repository removes everywhere else -- and deriving repository identity
    from git is the documented pattern for exactly this.

    IT ALSO REMOVED A PII FINDING, WHICH IS HOW THE PROBLEM SURFACED. An SSH
    remote of the form user@host is structurally an email address, and the
    scanner cannot know it is a git remote. The rule is to eliminate at source
    rather than allowlist, because a suppressed finding makes the scanner
    decoration. Here the elimination and the better design are the same change.

    RAISES RATHER THAN RETURNING A DEFAULT: with no origin there is nothing to
    clone from, and a placeholder would send the Studio somewhere real.
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
    return [
        "ssh",
        "-T",
        "-o",
        "ForwardAgent=yes",
        STUDIO_HOST,
        "bash",
        "-s",
    ]


def clone_script() -> str:
    """Create the Studio's clone if it is absent. Idempotent.

    IDEMPOTENT BECAUSE A BOOTSTRAP THAT ONLY WORKS ONCE IS ONE NOBODY RE-RUNS,
    and a bootstrap nobody re-runs cannot reconcile anything. Running this on a
    Studio that already has the clone reports the fact and changes nothing.
    """
    return f"""
set -euo pipefail

mkdir -p ~/ltphongssvn

if [ -d {STUDIO_REPO_PATH}/.git ]; then
  echo "ok      clone already present"
else
  git clone {remote_url()} {STUDIO_REPO_PATH}
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
    whole design prevents. Fast-forward-only turns divergence into a loud
    failure instead.

    --prune because branches deleted on merge otherwise linger on the Studio
    forever, and `git branch -a` stops meaning anything.

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

    NOT capture_output. This is a human-initiated operation and the output is
    the point -- a clone can take a while, and a silent command that prints
    everything at the end reads as a hang.

    S603 IS SUPPRESSED NARROWLY: the argument list is built by ssh_command()
    from module constants, with nothing interpolated from user input.
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
            raise SystemExit(f"usage: python -m cscie103_olap_oltp.cloud clone|pull  (got {other})")


if __name__ == "__main__":
    raise SystemExit(main())


# --- THE STUDIO NEVER PUSHES -------------------------------------------------
#
# There is no push verb here, and its absence is a design decision rather than
# an omission. Work is authored on the laptop, reviewed through a pull request,
# and gated by CI. A Studio that could push would bypass all three -- and with a
# forwarded agent it would push as YOU, with no way to tell afterwards which
# machine the commit came from.
#
# If the Studio ever needs to author work, the honest answer is the same GitFlow
# everything else uses: a feature branch, a pull request, and the quality gate.
# That is a deliberate change with its own review, not a verb quietly added
# here.
#
# TEAMSPACE is recorded for the day a task needs the Lightning CLI -- studio
# start/stop, or cp for artifacts git should not carry. Sync itself needs only
# ssh and git, so no dependency on the SDK is taken for it.
