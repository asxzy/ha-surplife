#!/usr/bin/env python3
"""Publish a commit as a ``-dev`` prerelease tag, for deploying it to a box.

**Why a tag and not a bare commit hash.** HACS with ``zip_release: true`` can
only fetch a *release asset*: it builds
``releases/download/<ref>/surplife.zip``, and a bare SHA is not a release, so
there is nothing to download. HACS does understand SHA archives, but only on
its repo-archive path, which ``zip_release`` turns off. Rather than give up
release assets -- they are what makes the archive layout predictable -- a commit
is published as a prerelease tag whose asset is built from that exact commit.

That also keeps "release" meaning what it should. A normal tag is stable; a
``-dev`` prerelease is a commit that has been deployed but not blessed. HACS
hides prereleases from the update entity unless beta versions are enabled, so
deploying one does not advertise it as an upgrade.

Usage::

    python3 tools/publish_dev_tag.py            # tag HEAD as v0.1.7-dev1
    python3 tools/publish_dev_tag.py --dry-run  # print the tag, publish nothing
    python3 tools/publish_dev_tag.py --tag v0.1.7-dev3

The tag is ``v<manifest version>-dev<n>``, where ``n`` is one past the highest
existing ``-dev`` tag for that version. Re-running after a new commit therefore
adds ``-dev2`` rather than trying to move a tag that is already published.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import tempfile

# ``tools/`` is not a package on ``sys.path`` when this is imported from a test,
# so add it before importing the sibling. Running it as a script puts ``tools/``
# on the path already, which makes the insert a no-op there.
_TOOLS = pathlib.Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import build_release  # noqa: E402  (must follow the sys.path setup above)

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "custom_components" / "surplife" / "manifest.json"

_DEV_TAG = re.compile(r"^v(?P<version>[0-9][^-]*)-dev(?P<number>\d+)$")


def _git(*args: str, capture: bool = True) -> str:
    """Run one git command, failing loudly rather than continuing."""
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=capture,
    )
    return result.stdout.strip() if capture else ""


def _version() -> str:
    return json.loads(MANIFEST.read_text())["version"]


def _next_tag(version: str) -> str:
    """The next free ``-dev`` tag for ``version``."""
    existing = _git("tag", "--list", f"v{version}-dev*").splitlines()
    highest = 0
    for tag in existing:
        match = _DEV_TAG.match(tag.strip())
        if match and match.group("version") == version:
            highest = max(highest, int(match.group("number")))
    return f"v{version}-dev{highest + 1}"


def _head() -> str:
    return _git("rev-parse", "--short", "HEAD")


def _is_dirty() -> bool:
    """Whether files that *ship* differ from HEAD.

    Only the integration package is checked. The tag points at HEAD regardless,
    so a modified README or a local note cannot change what the tag contains --
    but a modified module can, and tagging one would publish code that the tag's
    own name does not describe.
    """
    changed = _git("status", "--porcelain", "--untracked-files=no", "--", "custom_components/surplife")
    return bool(changed)


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", metavar="TAG", help="use this tag instead of the next")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the tag and exit"
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="tag HEAD even with uncommitted changes (the tag still points at HEAD)",
    )
    args = parser.parse_args(argv)

    if _is_dirty() and not args.allow_dirty and not args.dry_run:
        print(
            "error: the working tree has uncommitted changes to tracked files. "
            "Commit first, so the tag matches the code it claims to be.",
            file=sys.stderr,
        )
        return 1

    tag = args.tag or _next_tag(_version())
    print(f"tag:  {tag}")
    print(f"head: {_head()}")

    if args.dry_run:
        print("dry run: nothing published")
        return 0

    # Build the asset from this commit's files, so the tag's archive and the
    # source it names cannot disagree.
    with tempfile.TemporaryDirectory() as tmp:
        archive = pathlib.Path(tmp) / "surplife.zip"
        build_release.build(archive)
        notes = (
            f"Development build of {_head()}, deployed for testing.\n\n"
            "This is a prerelease: it has been run against real hardware but has "
            "not been through a full test pass, so it is not a stable release. "
            "HACS only shows it when beta versions are enabled."
        )
        subprocess.run(
            [
                "gh",
                "release",
                "create",
                tag,
                str(archive),
                "--prerelease",
                "--title",
                tag,
                "--notes",
                notes,
            ],
            cwd=ROOT,
            check=True,
        )
    print(f"published {tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
