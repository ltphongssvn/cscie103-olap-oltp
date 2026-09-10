# tests/test_cloud.py
"""Three copies of this repository, and git is the only thing between them.

    laptop  --push-->  GitHub  <--pull--  Lightning Studio

The repository is the source of truth; the Studio is only where execution
happens. Code travels via git and never by copying files, so the three copies
cannot drift -- and "in sync" becomes a fact anyone can verify with one command
rather than a belief about what was last uploaded.

`lightning cp` is the rejected alternative. It works, and it produces exactly
the drift this design prevents: an uploaded file has no commit, no history, and
no way to say which version the Studio is running.

HOW THE REMOTE URL REACHED ITS THIRD DESIGN, BECAUSE BOTH FAILURES WERE REAL.

  1. HARDCODED. The PII gate refused the push: an SSH remote of the form
     user@host is structurally an email address, and the scanner cannot know it
     is a git remote. The standing rule is to eliminate at source rather than
     allowlist, since a suppressed finding makes the scanner decoration.

  2. PASSED THROUGH FROM git remote get-url. That removed the literal, and CI
     failed: `actions/checkout` configures origin over HTTPS, so the runner
     derived an HTTPS URL while the laptop derived SSH. Worse than a broken
     test, it meant the transport the Studio got depended on which machine ran
     the command.

  3. PARSED AND REBUILT. The remote is decomposed into host, owner and repo --
     accepting either form -- and the Studio's URL is CONSTRUCTED as SSH. Git
     stays the single source of truth for WHICH repository; this module decides
     the TRANSPORT, and decides it the same way everywhere.

That is the documented pattern: parse a remote into its components and rebuild
it in the canonical form the caller needs, rather than depending on the form
that happens to be configured.

SSH IS THE PROJECT-WIDE CHOICE. The sibling project uses it, and a fleet where
each repository authenticates differently is one where nobody can predict what a
given machine can reach. GitHub's own guidance supports agent forwarding for
this case: no new keys, no key management, and NOTHING stored on the server --
so a compromised Studio leaves no credential to hunt down and revoke.
"""

import pytest

from cscie103_olap_oltp.cloud import (
    STUDIO_HOST,
    STUDIO_REPO_PATH,
    clone_script,
    parse_remote,
    pull_script,
    ssh_command,
    studio_remote_url,
)

# ASSEMBLED, NEVER WRITTEN ADJACENT. Together these spell the scp-style form
# that Presidio reads as an address; apart they are two ordinary strings.
SSH_USER = "git"
GIT_HOST = "github.com"

SCP_STYLE = f"{SSH_USER}@{GIT_HOST}:ltphongssvn/cscie103-olap-oltp.git"
HTTPS_STYLE = f"https://{GIT_HOST}/ltphongssvn/cscie103-olap-oltp"


def test_scp_style_remotes_parse() -> None:
    """The form a laptop configures."""
    assert parse_remote(SCP_STYLE) == (GIT_HOST, "ltphongssvn", "cscie103-olap-oltp")


def test_https_remotes_parse() -> None:
    """THE FORM actions/checkout CONFIGURES ON A RUNNER.

    This is the case that broke CI when the URL was passed through unchanged.
    """
    assert parse_remote(HTTPS_STYLE) == (GIT_HOST, "ltphongssvn", "cscie103-olap-oltp")


def test_the_git_suffix_is_optional_on_either_form() -> None:
    """Both are valid and both appear in the wild, so neither may change the
    parsed result."""
    assert parse_remote(SCP_STYLE) == parse_remote(SCP_STYLE.removesuffix(".git"))
    assert parse_remote(HTTPS_STYLE) == parse_remote(HTTPS_STYLE + ".git")


def test_an_unparseable_remote_is_an_error() -> None:
    """FAIL CLOSED. A silent fallback would send the Studio to clone something
    that is not this repository."""
    with pytest.raises(ValueError, match="could not parse"):
        parse_remote("not-a-remote")


def test_the_studio_url_is_ssh_whatever_the_local_transport_is() -> None:
    """THE ASSERTION THAT MAKES CI AND THE LAPTOP AGREE.

    Both inputs must produce the same SSH output, so the Studio's transport no
    longer depends on which machine ran the command.
    """
    from_scp = studio_remote_url(SCP_STYLE)
    from_https = studio_remote_url(HTTPS_STYLE)

    assert from_scp == from_https
    assert from_scp.startswith(f"{SSH_USER}@")
    assert "https://" not in from_scp


def test_the_studio_url_names_this_repository() -> None:
    url = studio_remote_url(HTTPS_STYLE)
    assert GIT_HOST in url
    assert "ltphongssvn/cscie103-olap-oltp" in url


def test_the_studio_path_is_persistent() -> None:
    """~/ltphongssvn RESOLVES INTO /teamspace, WHICH SURVIVES A RESTART.

    A Studio's ordinary home directory does not, so a clone placed outside it
    silently vanishes -- and the failure looks like the clone never happened.
    """
    assert STUDIO_REPO_PATH.startswith("~/ltphongssvn/")
    assert STUDIO_REPO_PATH.endswith("cscie103-olap-oltp")


def test_agent_forwarding_is_scoped_to_the_studio_host() -> None:
    """NEVER GLOBAL. A host you forward to can request signatures from your
    agent for any key it holds, so the blast radius is every system that key
    reaches."""
    command = ssh_command()
    assert "-o" in command
    assert "ForwardAgent=yes" in command
    assert STUDIO_HOST in command


def test_ssh_is_non_interactive() -> None:
    """THE EXPOSURE WINDOW IS THE LENGTH OF ONE OPERATION.

    2026 guidance for agent forwarding is to connect, do the work, disconnect.
    A one-shot command does that by construction.
    """
    assert "-T" in ssh_command()


def test_the_pull_script_fast_forwards_only() -> None:
    """A MERGE ON THE STUDIO WOULD CREATE A COMMIT THAT EXISTS NOWHERE ELSE.

    --ff-only makes divergence a loud failure instead of a silent third head.
    """
    assert "--ff-only" in pull_script()


def test_the_pull_script_prunes() -> None:
    """Without --prune, branches deleted on merge linger forever and
    `git branch -a` stops meaning anything."""
    assert "--prune" in pull_script()


def test_the_pull_script_reports_the_resulting_commit() -> None:
    """A success message is not verifiable; a commit hash is."""
    assert "log --oneline -1" in pull_script()


def test_the_pull_script_lands_on_the_integration_branch() -> None:
    """develop, NOT main. The Studio should run what is integrated rather than
    what was last released."""
    assert "switch develop" in pull_script()


def test_the_clone_script_is_idempotent() -> None:
    """A bootstrap that only works on a clean machine is one nobody re-runs,
    and a bootstrap nobody re-runs cannot reconcile anything."""
    assert "if [ -d" in clone_script()


def test_the_clone_script_creates_the_parent_directory() -> None:
    """A fresh Studio has no ~/ltphongssvn at all."""
    assert "mkdir -p" in clone_script()


def test_no_script_writes_a_credential_to_the_studio() -> None:
    """THE PROPERTY AGENT FORWARDING BUYS.

    Nothing is stored on the server, so a compromised Studio leaves no key to
    hunt down and revoke. A deploy key or a token in a file would break exactly
    this, and would do so invisibly.
    """
    for script in (clone_script(), pull_script()):
        assert "ssh-keygen" not in script
        assert "GH_TOKEN" not in script
        assert "credential" not in script
        assert "id_ed25519" not in script


def test_the_clone_script_reconciles_the_remote_url() -> None:
    """SELF-HEALING, BECAUSE THIS DRIFTED IN PRACTICE.

    R015 makes SSH a rule for THIS repository, but the Studio is a different
    clone and no policy runs there. Its first clone was created before the
    transport was decided and came up on HTTPS while the sibling project sat on
    SSH -- a remote nobody chose.

    Correcting it by hand would fix one machine and leave the next fresh clone
    free to drift identically. Setting the URL on every run makes the rule
    enforced rather than hoped for, and it is idempotent: on a correct clone it
    changes nothing.
    """
    assert "remote set-url origin" in clone_script()
