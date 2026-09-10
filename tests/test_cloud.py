# tests/test_cloud.py
"""Three copies of this repository, and git is the only thing between them.

THE ARCHITECTURE, WHICH IS THE WHOLE POINT.

    laptop  --push-->  GitHub  <--pull--  Lightning Studio

The repository is the source of truth; the Studio is only where execution
happens. Code travels via git and never by copying files, so the three copies
cannot drift -- and "in sync" becomes a fact anyone can verify with one command
rather than a belief about what was last uploaded.

The rejected alternative is `lightning cp`. It works, and it produces exactly
the drift this design exists to prevent: a file uploaded from one machine has no
commit, no history, and no way to answer which version the Studio is running.

WHY SSH AND AGENT FORWARDING RATHER THAN HTTPS.

This repository is public, so HTTPS would need no credential at all for the pull
direction -- genuinely simpler. It is rejected for consistency: the sibling
project uses SSH, and a fleet where each repository authenticates differently is
one where nobody can predict what a given machine can reach.

GitHub's own guidance supports the choice: agent forwarding needs no new keys,
no key management, and stores NOTHING on the server -- so a compromised Studio
means no credential to hunt down and revoke. Its documented limitation, that
users must SSH in and automated processes cannot, is not one we have: sync is
human-initiated by design.

The 2026 hardening is applied rather than assumed: forwarding is scoped to the
single Studio host, never enabled globally, and every operation is a one-shot
non-interactive command so the exposure window is the length of a pull.
"""

from cscie103_olap_oltp.cloud import (
    REMOTE_URL,
    STUDIO_HOST,
    STUDIO_REPO_PATH,
    clone_script,
    pull_script,
    ssh_command,
)


def test_the_remote_is_ssh_not_https() -> None:
    """CROSS-PROJECT CONSISTENCY IS THE REASON, NOT SECURITY.

    HTTPS would be simpler here because the repository is public. A fleet where
    each repo authenticates differently is one where nobody can predict what a
    machine can reach, so every repository uses the same transport.
    """
    assert REMOTE_URL.startswith("git@github.com:")
    assert "https://" not in REMOTE_URL


def test_the_remote_names_this_repository() -> None:
    assert REMOTE_URL == "git@github.com:ltphongssvn/cscie103-olap-oltp.git"


def test_the_studio_path_is_persistent() -> None:
    """~/ltphongssvn RESOLVES INTO /teamspace, WHICH SURVIVES A RESTART.

    A Studio's ordinary home directory does not. Cloning outside /teamspace
    produces a repository that vanishes on the next restart, and the failure
    looks like the clone never happened.
    """
    assert STUDIO_REPO_PATH.startswith("~/ltphongssvn/")
    assert STUDIO_REPO_PATH.endswith("cscie103-olap-oltp")


def test_agent_forwarding_is_scoped_to_the_studio_host() -> None:
    """NEVER GLOBAL. Enabling ForwardAgent for every host means any server you
    reach can request signatures from your agent for any key it holds."""
    command = ssh_command()
    assert "-o" in command
    assert "ForwardAgent=yes" in command
    assert STUDIO_HOST in command


def test_ssh_is_non_interactive() -> None:
    """THE EXPOSURE WINDOW IS THE LENGTH OF ONE OPERATION.

    2026 guidance for agent forwarding is to connect, do the work, disconnect.
    A one-shot command does that by construction; a long-lived shell leaves the
    agent reachable for as long as it stays open.
    """
    command = ssh_command()
    assert "-T" in command or "bash" in " ".join(command)


def test_the_pull_script_fast_forwards_only() -> None:
    """A MERGE ON THE STUDIO WOULD CREATE A COMMIT THAT EXISTS NOWHERE ELSE.

    --ff-only makes divergence a loud failure instead of a silent third head,
    which is exactly the drift the whole design prevents.
    """
    assert "--ff-only" in pull_script()


def test_the_pull_script_prunes() -> None:
    """Without --prune, branches deleted on merge linger on the Studio forever
    and `git branch -a` stops meaning anything."""
    assert "--prune" in pull_script()


def test_the_pull_script_reports_the_resulting_commit() -> None:
    """ "Pulled" is not verifiable; a commit hash is.

    The question being answered is "are the three copies the same", so the
    answer has to be something comparable rather than a success message.
    """
    assert "log --oneline -1" in pull_script()


def test_the_pull_script_lands_on_the_integration_branch() -> None:
    """develop, NOT main. GitFlow makes develop the integration branch, and the
    Studio should run what is integrated rather than what was last released."""
    assert "switch develop" in pull_script()


def test_the_clone_script_is_idempotent() -> None:
    """Running setup twice must not fail on an existing clone.

    A bootstrap that only works on a clean machine is one nobody re-runs, and a
    bootstrap nobody re-runs cannot reconcile anything.
    """
    script = clone_script()
    assert "if [ -d" in script or "-d " in script


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
