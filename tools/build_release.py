#!/usr/bin/env python3
"""Build the HACS release archive for this integration.

HACS installs a ``zip_release`` integration by extracting the archive
*directly* into ``<config>/custom_components/<domain>/`` - it never strips a
leading directory. The archive must therefore contain the integration files at
its **root**::

    manifest.json
    __init__.py
    coordinator.py
    ...

Wrapping them in ``custom_components/surplife/`` instead produces the tell-tale
double nesting::

    custom_components/surplife/custom_components/surplife/manifest.json

Home Assistant then finds no ``__init__.py`` where it looks and never loads the
integration, while HACS still records the version as installed.

Usage:
    python3 tools/build_release.py                 # -> dist/surplife.zip
    python3 tools/build_release.py -o /tmp/x.zip   # explicit output
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = ROOT / "custom_components" / "surplife"
DEFAULT_OUTPUT = ROOT / "dist" / "surplife.zip"

# Junk that must never ship: bytecode, editor backups, and macOS AppleDouble
# files (``._name``) which ``tar``/``ditto``/Finder scatter over extracted trees.
SKIP_DIRS = {"__pycache__"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
SKIP_PREFIXES = ("._", ".DS_Store")


def _keep(path: pathlib.Path, source: pathlib.Path) -> bool:
    rel = path.relative_to(source)
    if any(part in SKIP_DIRS for part in rel.parts):
        return False
    if path.suffix in SKIP_SUFFIXES:
        return False
    if path.name.startswith(SKIP_PREFIXES):
        return False
    return path.is_file()


def integration_files(source: pathlib.Path = SOURCE) -> list[pathlib.Path]:
    """Return the files to ship, relative to the integration directory."""
    if not source.is_dir():
        raise SystemExit(f"missing integration directory: {source}")
    files = [p for p in sorted(source.rglob("*")) if _keep(p, source)]

    # A release that cannot be imported is worse than no release.
    required = {"__init__.py", "manifest.json"}
    present = {p.relative_to(source).as_posix() for p in files}
    missing = sorted(required - present)
    if missing:
        raise SystemExit(f"refusing to build: missing {', '.join(missing)}")
    return files


def _arcname(path: pathlib.Path, source: pathlib.Path) -> str:
    """Archive name: the path relative to the integration root, never prefixed."""
    return path.relative_to(source).as_posix()


def build(output: pathlib.Path, source: pathlib.Path = SOURCE) -> pathlib.Path:
    files = integration_files(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, _arcname(path, source))
    return output


def manifest_version(source: pathlib.Path = SOURCE) -> str:
    return json.loads((source / "manifest.json").read_text())["version"]


def layout_problems(archive: pathlib.Path) -> list[str]:
    """Return the reasons an archive is unusable, empty when it is well formed."""
    with zipfile.ZipFile(archive) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
    problems = []
    if "__init__.py" not in names:
        problems.append("no __init__.py at the archive root")
    if "manifest.json" not in names:
        problems.append("no manifest.json at the archive root")
    nested = [n for n in names if n.startswith("custom_components/")]
    if nested:
        problems.append(
            f"{len(nested)} entries are nested under custom_components/ "
            "(HACS extracts the archive into the domain directory already)"
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-o", "--output", type=pathlib.Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify an existing archive instead of building one",
    )
    args = parser.parse_args(argv)

    output = args.output
    if not args.check:
        build(output)
        print(f"built {output} (v{manifest_version()})")

    problems = layout_problems(output)
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1
    with zipfile.ZipFile(output) as zf:
        count = len([n for n in zf.namelist() if not n.endswith("/")])
    print(f"ok: {count} files at the archive root")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
