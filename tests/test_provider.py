from datetime import date

import pytest

from trip.provider import SnapshotProvider, apply_overrides
from trip.models import Place


def test_loads_all_kinds(provider):
    assert len(provider.places()) == 11
    assert len(provider.places("관광지")) == 5
    assert len(provider.places("음식점")) == 5
    assert [p.id for p in provider.lodgings()] == ["h_myeongdong"]


def test_override_applied_and_original_kept(provider, raw_provider):
    sujebi = provider.place("r_sujebi")
    assert sujebi.last_review_date == date(2026, 6, 10)
    assert sujebi.overridden == {"last_review_date": "2026-10-28"}
    assert "기준 2" in sujebi.override_reason
    assert raw_provider.place("r_sujebi").last_review_date == date(2026, 10, 28)
    assert not raw_provider.place("r_sujebi").is_overridden


def test_segment_override(provider):
    seg = provider.segment("a_changdeok", "a_insadong")
    assert seg.last_checked == date(2026, 8, 20)
    assert seg.overridden == {"last_checked": "2026-11-05"}


def test_untouched_items_are_not_marked(provider):
    assert not provider.place("a_gyeongbok").is_overridden
    assert not provider.segment("a_gyeongbok", "a_folk").is_overridden


def test_reverse_segment(provider):
    forward = provider.segment("a_bukchon", "a_changdeok")
    backward = provider.segment("a_changdeok", "a_bukchon")
    assert (backward.from_id, backward.to_id) == ("a_changdeok", "a_bukchon")
    assert backward.path == list(reversed(forward.path))
    assert backward.source == "fixture"
    assert backward.alternatives == 1


def test_missing_segment_is_estimated(provider):
    near = provider.segment("a_folk", "r_tosokchon")       # 약 800m
    far = provider.segment("a_gyeongbok", "r_bindaetteok")  # 약 2km
    assert near.is_estimate and near.best.mode == "WALK"
    assert far.is_estimate and far.best.mode == "ESTIMATE"
    assert far.minutes > near.minutes
    assert far.last_checked == date(2026, 11, 9)


def test_segment_properties(provider):
    seg = provider.segment("r_bindaetteok", "h_myeongdong")
    assert seg.alternatives == 2
    assert seg.best.mode == "BUS"
    assert seg.best.last_departure.hour == 21


def test_unknown_override_target_fails():
    place = Place(id="x", name="x", kind="관광지", lat=0, lng=0, last_checked=date(2026, 1, 1))
    with pytest.raises(KeyError):
        apply_overrides({"x": place}, [{"target": "place", "id": "nope", "set": {}, "reason": ""}], "place")


def test_missing_overrides_file_is_ok(tmp_path):
    from tests.conftest import FIXTURES

    p = SnapshotProvider(FIXTURES / "snapshot", tmp_path / "none.json", reference_date=date(2026, 11, 9))
    assert not p.place("r_sujebi").is_overridden
