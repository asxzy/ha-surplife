"""The release archive must unpack to a loadable integration.

HACS installs a ``zip_release`` integration by extracting the archive straight
into ``<config>/custom_components/<domain>/``. It does not strip a leading path
component. An archive that wraps the files in ``custom_components/surplife/``
therefore lands at ``custom_components/surplife/custom_components/surplife/``,
where Home Assistant never finds ``__init__.py``: setup silently never runs and
the integration's entities and services never appear, while HACS still reports
the new version as installed because it only tracks its own metadata.

These tests pin the archive layout that makes HACS and Home Assistant agree.
"""

from __future__ import annotations

import pathlib
import sys
import zipfile

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SOURCE = REPO / "custom_components" / "surplife"
sys.path.insert(0, str(REPO / "tools"))

import build_release  # noqa: E402


@pytest.fixture()
def archive(tmp_path):
    return build_release.build(tmp_path / "surplife.zip")


def test_files_sit_at_the_archive_root(archive):
    """HACS extracts into the domain directory, so nothing may be prefixed."""
    with zipfile.ZipFile(archive) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]

    assert "__init__.py" in names
    assert "manifest.json" in names
    assert not [n for n in names if n.startswith("custom_components/")]


def test_archive_mirrors_the_integration_directory(archive):
    """Every shipped file is the integration file at the same relative path."""
    with zipfile.ZipFile(archive) as zf:
        names = {n for n in zf.namelist() if not n.endswith("/")}

    expected = {
        p.relative_to(SOURCE).as_posix()
        for p in SOURCE.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    }
    assert names == expected


def test_bytecode_and_apple_double_files_are_excluded(archive):
    """Finder/``tar`` junk must not travel into a release."""
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()

    assert not [n for n in names if "__pycache__" in n]
    assert not [n for n in names if n.endswith((".pyc", ".pyo"))]
    assert not [n for n in names if n.split("/")[-1].startswith(("._", ".DS_Store"))]


def test_layout_check_rejects_the_old_nested_shape(tmp_path):
    """The guard must fail the exact archive shape that caused the outage."""
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("custom_components/surplife/__init__.py", "")
        zf.writestr("custom_components/surplife/manifest.json", "{}")

    problems = build_release.layout_problems(bad)
    assert problems
    assert any("__init__.py" in p for p in problems)
    assert any("nested under custom_components/" in p for p in problems)


def test_layout_check_accepts_a_built_archive(archive):
    assert build_release.layout_problems(archive) == []


def test_builder_refuses_a_directory_without_manifest(tmp_path):
    broken = tmp_path / "surplife"
    broken.mkdir()
    (broken / "__init__.py").write_text("")

    with pytest.raises(SystemExit):
        build_release.build(tmp_path / "out.zip", source=broken)


def test_manifest_version_is_readable_for_tagging():
    """The release tag is derived from the manifest, so it must be a string."""
    version = build_release.manifest_version()
    assert isinstance(version, str)
    assert version
