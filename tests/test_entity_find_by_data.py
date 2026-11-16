"""Tests for EntityOperations.find_by_data — provider natural-key lookups (RFC-014 §3.2)."""

import pytest

from system.core import get_core


@pytest.fixture
def core():
    with get_core() as core:
        yield core


def _mk(core, etype, data):
    return core.entity.create(entity_type=etype, data=data)


class TestFindByData:
    def test_finds_entity_by_top_level_data_field(self, core):
        _mk(core, "Event", {"source_id": "abc", "account": "me@gmail.com"})
        other = _mk(core, "Event", {"source_id": "abc", "account": "other@gmail.com"})
        found = core.entity.find_by_data(
            "Event", {"source_id": "abc", "account": "me@gmail.com"}
        )
        assert found is not None
        assert found["uuid"] != other
        assert found["data"]["account"] == "me@gmail.com"

    def test_no_match_returns_none(self, core):
        _mk(core, "Event", {"source_id": "abc"})
        found = core.entity.find_by_data("Event", {"source_id": "zzz"})
        assert found is None

    def test_filters_combined_with_and(self, core):
        _mk(core, "Event", {"calendar_id": "c1", "source_id": "x"})
        _mk(core, "Event", {"calendar_id": "c2", "source_id": "x"})
        found = core.entity.find_by_data(
            "Event", {"calendar_id": "c1", "source_id": "x"}
        )
        assert found["data"]["calendar_id"] == "c1"

    def test_ambiguous_natural_key_raises(self, core):
        _mk(core, "Event", {"calendar_id": "c1", "source_id": "dup"})
        _mk(core, "Event", {"calendar_id": "c2", "source_id": "dup"})
        with pytest.raises(ValueError, match="ambiguous natural key"):
            core.entity.find_by_data("Event", {"source_id": "dup"})

    def test_entity_type_scoped(self, core):
        _mk(core, "Event", {"source_id": "dup"})
        _mk(core, "Scope", {"source_id": "dup"})
        found = core.entity.find_by_data("Event", {"source_id": "dup"})
        assert found["type"] == "Event"

    def test_superseded_excluded_by_default(self, core):
        old = _mk(core, "Event", {"source_id": "s1"})
        new = _mk(core, "Scope", {"source_id": "s1"})
        core.entity.supersede(old, new)
        found = core.entity.find_by_data("Event", {"source_id": "s1"})
        assert found is None

    def test_invalid_field_path_rejected(self, core):
        with pytest.raises(ValueError):
            core.entity.find_by_data("Event", {"bad); drop": "x"})

    def test_nested_path_via_dotted_field(self, core):
        _mk(core, "Event", {"provider": {"etag": "e9"}})
        found = core.entity.find_by_data("Event", {"provider.etag": "e9"})
        assert found is not None
        assert found["data"]["provider"]["etag"] == "e9"