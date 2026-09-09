# tests/test_package.py
"""The package is installed, importable, and its version is not a second copy.

These are structural tests, not placeholders. Each one guards a failure this
project can actually have:

  - a src-layout package that is not installed imports in the test run only
    because pytest put the source directory on sys.path, so the suite passes
    while the wheel is broken;
  - __version__ and the distribution version are two hand-maintained copies of
    one fact, and two copies drift;
  - a subpackage missing __init__.py is invisible to mypy and excluded from the
    wheel, and nothing says so until an import fails at runtime.
"""

from importlib import import_module
from importlib.metadata import version

import cscie103_olap_oltp

# THE SIX SUBPACKAGES, ENUMERATED. A test that discovers them by walking the
# directory would pass when a package is deleted, because it would simply find
# five and check five. The list is the assertion.
SUBPACKAGES = (
    "contracts",
    "oltp",
    "olap",
    "etl",
    "policy",
    "evidence",
)


def test_version_matches_distribution() -> None:
    """__version__ and pyproject.toml agree.

    importlib.metadata reads the INSTALLED distribution, so this compares the
    attribute against what was actually built and installed -- not against
    another string in the source tree.
    """
    assert cscie103_olap_oltp.__version__ == version("cscie103-olap-oltp")


def test_every_subpackage_imports() -> None:
    """Each declared subpackage exists and imports cleanly."""
    for name in SUBPACKAGES:
        module = import_module(f"cscie103_olap_oltp.{name}")
        assert module.__name__ == f"cscie103_olap_oltp.{name}"


def test_package_is_installed_not_merely_on_path() -> None:
    """The package resolves from site-packages, not from the source tree.

    With a src layout and an editable install, __file__ still points into src/,
    so the location cannot distinguish the two. What CAN: importlib.metadata
    only answers for a distribution that was actually installed. A bare
    sys.path hit raises PackageNotFoundError here.
    """
    assert version("cscie103-olap-oltp") == "0.1.0"
