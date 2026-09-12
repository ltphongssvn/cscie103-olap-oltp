# tests/test_hermetic.py
"""The default suite cannot reach the network, and that is enforced.

    Tests as Code   the hermetic claim is checked, not asserted in a comment

WHY THIS EXISTS. `-m not integration` STATED that the default run is hermetic;
nothing held it to that. A mutation run proved the gap twice: a mutant changed
the spaCy model name in pii.py, Presidio called
`_download_spacy_model_if_needed`, and the interpreter died mid-run with
`Fatal Python error: Aborted`.

A HIDDEN EXTERNAL DEPENDENCY IS THE DISEASE AND FLAKINESS IS THE SYMPTOM. The
documented practice is to block the socket, which turns any reach into a named
failure rather than a hang, a crash, or a test that passes only when the
network happens to be up.

WHAT THIS DOES NOT COVER, STATED PLAINLY. pytest-socket blocks IN-PROCESS
sockets. A subprocess has its own, so the integration tests that shell out to
the `databricks` CLI are unaffected -- they are excluded by marker instead. The
pii download was in-process, which is why this catches it.
"""

import socket

import pytest
from pytest_socket import SocketBlockedError


def test_opening_a_socket_fails_in_the_default_suite() -> None:
    """THE GUARD ITSELF, PROVED RATHER THAN CONFIGURED.

    `--disable-socket` lives in addopts, where a later edit could drop it and
    nothing would notice: every test would still pass, and the suite would
    quietly be able to reach the internet again.
    """
    with pytest.raises(SocketBlockedError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)


def test_name_resolution_is_blocked_too() -> None:
    """DNS IS THE FIRST NETWORK CALL MOST LIBRARIES MAKE.

    The spaCy download died inside `proxy_bypass_macosx_sysconf`, before any
    connection was attempted -- so a guard that allowed resolution would have
    missed the failure that motivated this file.
    """
    with pytest.raises(SocketBlockedError):
        socket.getaddrinfo("example.org", 443)
