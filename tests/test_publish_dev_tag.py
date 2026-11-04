"""Tests for publishing a commit as a ``-dev`` prerelease tag.

The tag name is the whole contract here: it is what HACS installs by, so
counting up wrongly means either moving a published tag (which GitHub refuses)
or reusing one that points at different code.
"""

from __future__ import annotations

import pytest

from tools import publish_dev_tag


@pytest.fixture
def tagger(monkeypatch):
    """Drive tag naming without touching the real repository."""

    def _set(existing: list[str], version: str = "0.1.6") -> None:
        monkeypatch.setattr(publish_dev_tag, "_version", lambda: version)
        monkeypatch.setattr(
            publish_dev_tag,
            "_git",
            lambda *args, **kw: "\n".join(existing),
        )

    return _set


def test_the_first_dev_tag_is_number_one(tagger) -> None:
    tagger([])
    assert publish_dev_tag._next_tag("0.1.6") == "v0.1.6-dev1"


def test_it_counts_up_from_the_highest_existing_tag(tagger) -> None:
    """A second commit gets ``-dev2`` rather than reusing ``-dev1``."""
    tagger(["v0.1.6-dev1", "v0.1.6-dev2"])
    assert publish_dev_tag._next_tag("0.1.6") == "v0.1.6-dev3"


def test_it_ignores_dev_tags_for_other_versions(tagger) -> None:
    """A stale ``-dev9`` for an older version must not push the number up."""
    tagger(["v0.1.5-dev9", "v0.1.6-dev1"])
    assert publish_dev_tag._next_tag("0.1.6") == "v0.1.6-dev2"


def test_it_ignores_stable_tags(tagger) -> None:
    tagger(["v0.1.5", "v0.1.6"])
    assert publish_dev_tag._next_tag("0.1.6") == "v0.1.6-dev1"


def test_a_dry_run_publishes_nothing(monkeypatch, capsys) -> None:
    """The dry run is how the tag is checked before anything is published."""
    published: list[object] = []
    monkeypatch.setattr(publish_dev_tag, "_is_dirty", lambda: False)
    monkeypatch.setattr(publish_dev_tag, "_head", lambda: "abc1234")
    monkeypatch.setattr(publish_dev_tag, "_next_tag", lambda version: "v0.1.6-dev1")
    monkeypatch.setattr(publish_dev_tag, "_version", lambda: "0.1.6")
    monkeypatch.setattr(
        publish_dev_tag.subprocess, "run", lambda *a, **k: published.append(a)
    )

    assert publish_dev_tag.main(["--dry-run"]) == 0
    assert published == []


def test_it_refuses_to_tag_a_dirty_tree(monkeypatch) -> None:
    """A tag on uncommitted work would name code it does not contain."""
    monkeypatch.setattr(publish_dev_tag, "_is_dirty", lambda: True)
    monkeypatch.setattr(
        publish_dev_tag.subprocess,
        "run",
        lambda *a, **k: pytest.fail("must not publish from a dirty tree"),
    )

    assert publish_dev_tag.main([]) == 1
