# tests/conftest.py
"""Suite-wide isolation from the ambient git repository.

THIS EXISTS BECAUSE A TEST COMMITTED TO THE REAL REPOSITORY.

A fixture built a scratch repo under tmp_path and ran `git commit` with cwd set
to it. The commit landed on the actual feature branch instead -- authored by
"Test <test@example.invalid>", deleting 58 lines of README, ninety seconds after
a genuine commit. It was found only because the branch carried a commit nobody
wrote.

WHY cwd DID NOT PROTECT ANYTHING. GIT_DIR, GIT_WORK_TREE and GIT_INDEX_FILE
OUTRANK the working directory: git honours them wherever it is invoked from, and
exports them while a hook runs. This repository's pre-push hook runs the suite,
so the act of pushing is what triggered it. Lefthook documents the same hazard,
noting that unit tests doing git operations in temp directories can have very
destructive results because GIT_DIR overrides even `git -C <tempdir>`.

WHY THE EXISTING GUARD DID NOT HELP, WHICH IS THE REAL LESSON.
src/cscie103_olap_oltp/git/env.py was written for exactly this -- its own
docstring records the same incident in the sibling project, where a fixture's
`git commit` wrote into the real repository and the stray commit was pushed.
Every production call routes through it; two fixtures did not, and nothing said
they had to. An identical incident elsewhere reached the same conclusion: the
protection existed, the failure mode was understood, and it was passed along by
word of mouth instead of enforced -- so a fixture written later did not get it.

Patching those two fixtures would leave the third to be written next month. The
variables are removed HERE, once, for every test, so a subprocess that inherits
the environment is safe by default and no fixture author has to know any of this.

monkeypatch RATHER THAN os.environ, AND THAT IS NOT STYLE. Mutating os.environ
directly leaks state across tests and produces order-dependent failures; a
manual try/finally still leaks if teardown is skipped. monkeypatch records each
change and reverses it after the test, and that guarantee holds even when an
assertion raises -- which is the difference between a guard and a good
intention.

THE NAMES COME FROM scrubbed_env(), NOT FROM A LIST. That function asks git for
its own enumeration via `rev-parse --local-env-vars`, so this stays correct when
git adds a variable -- and a literal list here would be the second copy this
repository keeps deleting.
"""

import os

import pytest

from cscie103_olap_oltp.git.env import scrubbed_env


def _routing_names() -> set[str]:
    """Whatever scrubbed_env() removes, derived by comparison.

    ASKING FOR THE DIFFERENCE rather than re-deriving the list means this cannot
    disagree with the module production depends on -- including its fail-closed
    fallback, where every GIT_ name is removed because git could not be asked.
    """
    return set(os.environ) - set(scrubbed_env())


@pytest.fixture(autouse=True)
def _isolate_from_the_ambient_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove git's routing variables for the duration of every test.

    AUTOUSE, BECAUSE OPT-IN IS WHAT FAILED. A fixture has to remember to ask for
    protection; this applies it whether or not its author knows it exists.

    RESTORED BY monkeypatch, so a test that deliberately sets one up --
    tests/test_gitenv.py does -- still observes what it configured, and nothing
    survives into the next test.
    """
    for name in _routing_names():
        monkeypatch.delenv(name, raising=False)
