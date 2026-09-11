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
     is a git remote. The rule is to eliminate at source rather than allowlist.

  2. PASSED THROUGH from `git remote get-url`. The literal went away and CI
     failed: actions/checkout configures origin over HTTPS, so the runner
     derived HTTPS while the laptop derived SSH -- the transport the Studio
     received depended on which machine ran the command.

  3. PARSED AND REBUILT, which is what this file does. The remote is decomposed
     into host, owner and repo -- accepting either form -- and the Studio's URL
     is CONSTRUCTED as SSH. Git remains the single source of truth for WHICH
     repository; this module decides the TRANSPORT, identically everywhere.

SSH IS ENFORCED BY POLICY, NOT BY THIS FILE. R015 in policies/repo/repo.rego
denies a non-SSH origin, so the rule is refused rather than merely intended.
"""

from __future__ import annotations

import re
import subprocess
import sys

from cscie103_olap_oltp.environment import current
from cscie103_olap_oltp.git.env import scrubbed_env
from cscie103_olap_oltp.remediation import ActionableError

# THE STUDIO AND TEAMSPACE COME FROM THE ENVIRONMENT, NOT FROM HERE.
#
# Both were literals in this file AND in mise.toml -- the same deployment fact
# written twice, and a module that worked for exactly one operator. A Studio
# alias is per-person: hardcoding it means anyone who copies this file silently
# targets someone else's machine.
#
# READ AT CALL TIME, NOT AT IMPORT. A module-level read freezes whatever the
# environment was when the first import happened, which makes tests
# order-dependent and monkeypatching useless.

# ~/ltphongssvn RESOLVES INTO /teamspace/studios/this_studio, WHICH PERSISTS.
# A Studio's ordinary home directory does not survive a restart, so a clone
# placed outside /teamspace silently vanishes.
STUDIO_REPO_PATH = "~/ltphongssvn/cscie103-olap-oltp"

INTEGRATION_BRANCH = "develop"

# SPLIT FROM THE HOST DELIBERATELY. Written adjacent to a hostname this is an
# email address to any scanner, and the PII gate is right to say so.
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
    handling only one would work on one machine and fail on the other.

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
    is what makes it identical on every machine.
    """
    host, owner, repo = parse_remote(origin)
    return f"{SSH_USER}@{host}:{owner}/{repo}.git"


def origin_url() -> str:
    """What git says `origin` is, on whichever machine is asking.

    ASKED OF GIT RATHER THAN DECLARED, so a literal cannot disagree with
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
    runs to completion, which also bounds the exposure window of the forwarded
    agent to the length of one operation.
    """
    return ["ssh", "-T", "-o", "ForwardAgent=yes", current().lightning_studio, "bash", "-s"]


def clone_script() -> str:
    r"""Create the Studio's clone if absent, and reconcile its remote. Idempotent.

    THE STUDIO REWRITES SSH URLS TO HTTPS, AND THAT IS NOT OUR CONFIG.
    Lightning's image ships a global `url.<https-prefix>.insteadOf` rule mapping
    the GitHub SSH prefix onto HTTPS, then hands the result to a `gh` credential
    helper. Inspect it with:

        git config --show-origin --get-regexp 'url\..*'

    That is why the first clone came up on HTTPS, and why `git remote set-url`
    alone appeared to do nothing -- the value was stored and rewritten on read.

    THE RULE IS NOT QUOTED VERBATIM HERE. It contains a user@host string that the
    PII scanner correctly reads as an address, and this repository eliminates
    findings at source rather than allowlisting them. The command above prints
    the live rule, which is better than a copy that can go stale.

    THE FIX IS SCOPED, NOT GLOBAL. Unsetting Lightning's rule with
    `git config --global` would mutate a persistent machine that hosts other
    projects, and the change would outlive this session with nothing to connect
    it back to us.

    GIT RESOLVES insteadOf BY LONGEST MATCH, so mapping the WHOLE URL to itself
    outranks the shorter prefix rule above and the rewrite loses. The same
    override is then written to the repository's LOCAL config, so fetch and push
    never depend on ambient state again.
    """
    url = studio_remote_url(origin_url())
    return f"""
set -euo pipefail

mkdir -p ~/ltphongssvn

if [ -d {STUDIO_REPO_PATH}/.git ]; then
  echo "ok      clone already present"
else
  git -c url."{url}".insteadOf="{url}" clone {url} {STUDIO_REPO_PATH}
  echo "created clone"
fi

cd {STUDIO_REPO_PATH}

# RECONCILE, DO NOT MERELY CREATE. R015 requires SSH, and this drifted in
# practice. Setting both on every run makes the rule enforced rather than hoped
# for; on a correct clone it changes nothing.
git config --local url."{url}".insteadOf "{url}"
git remote set-url origin {url}
echo "remote:  $(git remote get-url origin)"

# A FRESH CLONE LANDS ON THE DEFAULT BRANCH, which is develop here, so the
# switch is usually a no-op. The fallback covers a local branch that does not
# exist yet and has to be created from the remote.
if ! git switch {INTEGRATION_BRANCH} 2>/dev/null; then
  git switch -c {INTEGRATION_BRANCH} origin/{INTEGRATION_BRANCH}
fi

git log --oneline -1
"""


def pull_script() -> str:
    """Fast-forward the Studio to origin/develop, and report where it landed.

    --ff-only IS THE IMPORTANT FLAG. A merge on the Studio would create a commit
    that exists nowhere else -- a third head, which is precisely the drift this
    design prevents.

    --prune because branches deleted on merge otherwise linger forever.

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


# ssh-add's DOCUMENTED EXIT CODES, NAMED ONCE. 1 and 2 describe different
# problems with different remedies, and collapsing them sends half the readers
# to the wrong fix.
AGENT_EMPTY = 1
AGENT_UNREACHABLE = 2


class AgentHasNoIdentitiesError(ActionableError):
    """The agent is running but holds no key, so forwarding forwards nothing.

    OBSERVED, NOT ANTICIPATED. Every Studio operation failed with a
    "Permission denied (publickey)" naming GitHub, while local git kept working
    -- because agent FORWARDING does not consult the macOS keychain, so a key
    git resolves happily is absent from what the Studio receives.

    THE ERROR IS NOT QUOTED VERBATIM: it contains a user@host string the PII
    scanner correctly reads as an address, and this repository eliminates
    findings at source rather than allowlisting them.

    macOS CAUSES THIS BY DESIGN: Apple re-aligned ssh-agent with mainstream
    OpenSSH, so a key added to the keychain is NOT re-added to the agent after a
    reboot. The failure appears spontaneously, on a machine that worked
    yesterday, reporting an error that names GitHub rather than the agent.
    """

    code = "ERR_SSH_AGENT_EMPTY"


class AgentUnreachableError(ActionableError):
    """There is no agent to talk to, or its socket is stale.

    A DIFFERENT PROBLEM FROM AN EMPTY AGENT, and the usual causes are
    structural rather than forgetful: a fresh login shell, `sudo`, a
    reattached multiplexer, or a CI context where SSH_AUTH_SOCK never carried
    over.
    """

    code = "ERR_SSH_AGENT_UNREACHABLE"


def require_forwardable_agent() -> None:
    """Fail before connecting, naming the agent rather than the remote.

    A PREFLIGHT, WHICH IS THE POINT. Without it ssh completes, GitHub refuses
    the key, and the reader is handed a permission error they will attribute to
    their account or the repository -- neither of which is wrong.
    """
    listed = subprocess.run(
        ["ssh-add", "-l"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )

    if listed.returncode == AGENT_UNREACHABLE:
        raise AgentUnreachableError(
            "no ssh-agent is reachable, so nothing can be forwarded",
            remediation=(
                "Start one and load your key:\n"
                '  eval "$(ssh-agent -s)"\n'
                "  ssh-add --apple-use-keychain ~/.ssh/id_ed25519"
            ),
            ssh_auth_sock=current().ssh_auth_sock or "(unset)",
        )

    if listed.returncode == AGENT_EMPTY:
        raise AgentHasNoIdentitiesError(
            "the ssh-agent holds no identities, so an empty agent is forwarded",
            remediation=(
                "Load your key:\n"
                "  ssh-add --apple-use-keychain ~/.ssh/id_ed25519\n"
                "Make it survive a reboot by adding to ~/.ssh/config under "
                "`Host *`:\n"
                "  AddKeysToAgent yes\n"
                "  UseKeychain yes\n"
                "  IdentityFile ~/.ssh/id_ed25519"
            ),
        )


def run_on_studio(script: str) -> int:
    """Execute a script on the Studio, streaming its output.

    NOT capture_output. This is human-initiated and the output is the point -- a
    clone takes a while, and a silent command reads as a hang.

    S603 IS SUPPRESSED NARROWLY: the argument list comes from ssh_command(),
    built from module constants with nothing interpolated from user input.
    """
    require_forwardable_agent()

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
# TEAMSPACE is recorded for the day a task needs the Lightning CLI. Sync itself
# needs only ssh and git.
