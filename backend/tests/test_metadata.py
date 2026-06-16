"""Tests for the data-source metadata sidecar (display names)."""

from __future__ import annotations

import pytest
from app.services import metadata_service


@pytest.fixture(autouse=True)
def isolated_meta(monkeypatch, tmp_path):
    """Point the sidecar at a tmp file so tests can't clobber each other
    (or the real DATA_DIR/datasources.json)."""
    monkeypatch.setattr(metadata_service.settings, "DATA_DIR", str(tmp_path))
    yield


def test_get_display_name_returns_none_when_unset():
    assert metadata_service.get_display_name("missing-id") is None


def test_set_and_get_display_name_round_trips():
    metadata_service.set_display_name("abc", "Q1 销售")
    assert metadata_service.get_display_name("abc") == "Q1 销售"


def test_set_display_name_normalizes_whitespace():
    metadata_service.set_display_name("abc", "  销售 2026  ")
    assert metadata_service.get_display_name("abc") == "销售 2026"


def test_set_display_name_rejects_empty():
    with pytest.raises(ValueError):
        metadata_service.set_display_name("abc", "   ")


def test_delete_entry_is_idempotent():
    metadata_service.set_display_name("abc", "x")
    metadata_service.delete_entry("abc")
    assert metadata_service.get_display_name("abc") is None
    # Calling again must not raise.
    metadata_service.delete_entry("abc")


def test_set_display_name_overwrites_previous_value():
    metadata_service.set_display_name("abc", "first")
    metadata_service.set_display_name("abc", "second")
    assert metadata_service.get_display_name("abc") == "second"


def test_entries_are_isolated_per_id():
    metadata_service.set_display_name("a", "Alpha")
    metadata_service.set_display_name("b", "Beta")
    assert metadata_service.get_display_name("a") == "Alpha"
    assert metadata_service.get_display_name("b") == "Beta"
