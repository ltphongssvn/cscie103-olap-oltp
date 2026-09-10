# src/cscie103_olap_oltp/git/worktree.py
"""Manage linked worktrees: add, list, refresh, remove.

WHY A MODULE AND NOT `git worktree add` TYPED BY HAND
Every project operation is a task. A hand-typed worktree leaves no record of
where worktrees go, what they are named, or what setup they need -- and the
setup is what bites, because git copies none of it.

WHAT GIT DOES NOT COPY, AND WHY add RUNS setup
A fresh worktree has no .venv, an untrusted mise.toml, and no installed git
hooks, so `mise run check` cannot run in it and neither can a commit. The
remedy is to script the setup git does not copy; here that is `mise run setup`,
which already owns exactly those three things.

LAYOUT: SIBLING DIRECTORIES, NAMED AFTER THE BRANCH
Siblings avoid nested-.git problems and make active work visible from one `ls`.
Branch slashes do not translate to folder names, so feature/star-schema becomes
<repo>-star-schema.

REMOVAL IS DELIBERATELY UNFORGIVING
`git worktree remove` refuses on modified or untracked files, and --force is a
good way to lose an untracked file someone copied in -- so --force is never
passed. `git branch -d` refuses unmerged work, which makes it double as proof
the merge happened. Neither is a limitation to route around; both are the check.

THE ROOT IS NOT COUNTED IN PARENTS. The sibling project computed it as
`Path(__file__).parents[N]`, then moved the file and reached a directory that
was not a repository at all -- every command failing with "not a git
repository". REPO_ROOT here is derived once, in one module, and imported.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from cscie103_olap_oltp.git.env import git, scrubbed_env
from cscie103_olap_oltp.policy.snapshot import REPO_ROOT

INTEGRATION_BRANCH = "develop"

# THE ATTRIBUTES GIT CURRENTLY EMITS. An unknown one refuses the whole
# inventory: destructive verbs sit on this parser, and acting on a partially
# understood list is how the wrong directory gets removed.
KNOWN_ATTRIBUTES = frozenset(
    {"worktree", "HEAD", "branch", "bare", "detached", "locked", "prunable"}
)


def validate_slug(slug: str) -> None:
    """A slug becomes both a branch name and a directory name.

    Restricting it to kebab-case keeps those two derivable from one string
    instead of needing a translation table.
    """
    if not slug or not all(ch.islower() or ch.isdigit() or ch == "-" for ch in slug):
        raise SystemExit(f"slug must be lowercase kebab-case: {slug!r}")


def sibling_name(main: Path, slug: str) -> Path:
    """<repo>-<slug>, beside the main checkout."""
    return main.parent / f"{main.name}-{slug}"


def parse_records(root: Path, _raw: bytes | None = None) -> list[dict[str, str]]:
    """Parse `git worktree list --porcelain -z`, failing closed.

    -z IS NOT OPTIONAL. `worktree list --porcelain` does NOT quote paths, so a
    path containing a newline silently corrupts a line-based parser. The git
    docs recommend combining --porcelain with -z for exactly this reason, and
    the accompanying test creates such a path rather than trusting the claim.

    _raw EXISTS FOR THE FAIL-CLOSED TESTS ONLY. Producing an unknown attribute
    or a bare repository from real git would mean depending on a future git
    release or building a second repository shape; injecting the bytes tests the
    parser's contract directly.
    """
    if _raw is None:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain", "-z"],  # noqa: S607
            capture_output=True,
            check=False,
            cwd=root,
            env=scrubbed_env(),
        )
        if result.returncode != 0:
            print(result.stderr.decode(errors="replace").strip(), file=sys.stderr)
            raise SystemExit("git worktree list failed")
        _raw = result.stdout

    records: list[dict[str, str]] = []
    current: dict[str, str] = {}

    for token in _raw.split(b"\0"):
        if token == b"":
            # An empty token closes a record.
            if current:
                records.append(current)
                current = {}
            continue

        try:
            text = token.decode("utf-8")
        except UnicodeDecodeError as error:
            raise SystemExit(f"worktree path is not UTF-8: {error}") from error

        key, _, value = text.partition(" ")
        if key not in KNOWN_ATTRIBUTES:
            raise SystemExit(f"unrecognised worktree attribute {key!r}; refusing to continue")
        current[key] = value

    if current:
        records.append(current)

    if not records or "worktree" not in records[0]:
        raise SystemExit("no main worktree reported; is this a git repository?")
    if "bare" in records[0]:
        raise SystemExit(
            "the main worktree is bare. The sibling layout composes paths from a "
            "parent checkout, and a bare repository has none."
        )

    return records


def _main_path() -> Path:
    """THE FIRST RECORD IS THE MAIN WORKTREE, per git's documented ordering."""
    return Path(parse_records(REPO_ROOT)[0]["worktree"])


def cmd_list() -> int:
    for record in parse_records(REPO_ROOT):
        branch = record.get("branch", "").removeprefix("refs/heads/") or "(detached)"
        flags = [flag for flag in ("locked", "prunable") if flag in record]
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        print(f"{record['worktree']}  {branch}{suffix}")
    return 0


def cmd_add(slug: str) -> int:
    validate_slug(slug)
    branch = f"feature/{slug}"
    path = sibling_name(_main_path(), slug)

    if path.exists():
        raise SystemExit(f"{path} already exists. Remove it first: mise run worktree:remove {slug}")

    # OFF origin/develop, FETCHED FIRST. A worktree cut from a stale local
    # develop starts life behind, which is the divergence this project's
    # branching rules exist to prevent.
    fetched = git("fetch", "origin", "--prune", "--quiet", cwd=REPO_ROOT)
    if fetched.returncode != 0:
        print(fetched.stderr.strip(), file=sys.stderr)
        raise SystemExit("fetch failed")

    added = git(
        "worktree",
        "add",
        "-b",
        branch,
        str(path),
        f"origin/{INTEGRATION_BRANCH}",
        cwd=REPO_ROOT,
    )
    if added.returncode != 0:
        print(added.stderr.strip(), file=sys.stderr)
        raise SystemExit("git worktree add failed")
    print(f"created {path} on {branch}\n")

    # SETUP IS NOT OPTIONAL. Without it the worktree has no .venv, an untrusted
    # mise.toml and no hooks, so the gate cannot run and a commit is refused.
    print("running setup in the new worktree...")
    subprocess.run(
        ["mise", "trust"],  # noqa: S607
        cwd=path,
        check=False,
        capture_output=True,
    )
    setup = subprocess.run(
        ["mise", "run", "setup"],  # noqa: S607
        cwd=path,
        check=False,
    )
    if setup.returncode != 0:
        print(
            f"\nsetup failed in {path}. The worktree exists but is not usable. "
            f"Fix setup there, or remove it:\n  mise run worktree:remove {slug}",
            file=sys.stderr,
        )
        return 1

    print(f"\nnext:\n  cd {path}")
    return 0


def cmd_refresh(slug: str) -> int:
    """Rebase the worktree's branch onto current origin/develop.

    Run from INSIDE the worktree, which is the documented pattern; objects are
    shared with the main clone so no network round-trip is needed.

    REFUSES ON A DIRTY TREE rather than using --autostash. git's manual warns
    that the stash application after a successful rebase may produce non-trivial
    conflicts, and rebase.autoStash defaults to false for that reason. Autostash
    hides work in a stash nobody created.
    """
    validate_slug(slug)
    path = sibling_name(_main_path(), slug)
    if not path.exists():
        raise SystemExit(f"{path} does not exist")

    status = subprocess.run(
        ["git", "status", "--porcelain", "-z"],  # noqa: S607
        capture_output=True,
        check=False,
        cwd=path,
        env=scrubbed_env(),
    )
    if status.stdout.strip(b"\0"):
        raise SystemExit(
            f"refusing: {path} has uncommitted changes.\n"
            "Commit or discard them first -- rebasing over a dirty tree either "
            "refuses, or with --autostash hides the work in a stash you did not "
            "create and may not look for."
        )

    fetched = git("fetch", "origin", "--prune", "--quiet", cwd=REPO_ROOT)
    if fetched.returncode != 0:
        print(fetched.stderr.strip(), file=sys.stderr)
        raise SystemExit("fetch failed")

    # REPORT AN OBSERVED STATE CHANGE, NOT PARSED OUTPUT.
    #
    # git rebase writes "Successfully rebased and updated..." to STDERR, so a
    # successful rebase leaves stdout empty. The sibling project printed stdout
    # and fell back to "already matches origin/develop" when it was empty --
    # confidently reporting that a worktree three PRs behind was up to date.
    #
    # Human-readable git output is explicitly not a stable interface. Comparing
    # SHAs before and after is plumbing, and it cannot describe something that
    # did not happen.
    before = git("rev-parse", "HEAD", cwd=path).stdout.strip()

    rebased = git("rebase", f"origin/{INTEGRATION_BRANCH}", cwd=path)
    if rebased.returncode != 0:
        print((rebased.stdout + rebased.stderr).strip(), file=sys.stderr)
        raise SystemExit(
            f"rebase stopped in {path}. Resolve there and `git rebase --continue`, "
            "or abandon with `git rebase --abort`."
        )

    after = git("rev-parse", "HEAD", cwd=path).stdout.strip()
    target = git("rev-parse", f"origin/{INTEGRATION_BRANCH}", cwd=path).stdout.strip()

    if before == after:
        print(f"{path} was already on origin/{INTEGRATION_BRANCH} ({target[:12]})")
    else:
        print(f"{path}: {before[:12]} -> {after[:12]} (onto {target[:12]})")
    return 0


def cmd_remove(slug: str) -> int:
    validate_slug(slug)
    path = sibling_name(_main_path(), slug)
    branch = f"feature/{slug}"

    if not path.exists():
        raise SystemExit(f"{path} does not exist")

    # NO --force, EVER. The refusal on modified or untracked files is the check,
    # not an obstacle: forcing is how an untracked file someone copied in is
    # lost with no record that it existed.
    removed = git("worktree", "remove", str(path), cwd=REPO_ROOT)
    if removed.returncode != 0:
        print(removed.stderr.strip(), file=sys.stderr)
        raise SystemExit(f"refused to remove {path}")
    print(f"removed {path}")

    # `-d`, NOT `-D`. The refusal on unmerged work doubles as proof the merge
    # happened.
    deleted = git("branch", "-d", branch, cwd=REPO_ROOT)
    if deleted.returncode != 0:
        print(
            f"worktree removed, but branch {branch} was NOT deleted:\n"
            f"  {deleted.stderr.strip()}\n"
            "That refusal means the branch is unmerged. Merge it, or delete it "
            "deliberately with `git branch -D`.",
            file=sys.stderr,
        )
        return 1

    print(f"deleted branch {branch}")
    return 0


def main() -> int:
    match sys.argv[1:]:
        case ["list"]:
            return cmd_list()
        case ["add", slug]:
            return cmd_add(slug)
        case ["refresh", slug]:
            return cmd_refresh(slug)
        case ["remove", slug]:
            return cmd_remove(slug)
        case other:
            raise SystemExit(
                f"usage: python -m cscie103_olap_oltp.git.worktree "
                f"add|list|refresh|remove [slug]  (got {other})"
            )


if __name__ == "__main__":
    raise SystemExit(main())
