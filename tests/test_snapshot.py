"""저장소에 들어 있는 실제 스냅샷(data/snapshot)과 시연용 덮어쓰기가 올바른지 확인한다."""
import json

import pytest

import config
from trip.provider import SnapshotProvider

pytestmark = pytest.mark.skipif(not (config.SNAPSHOT_DIR / "places.json").exists(), reason="스냅샷 없음")


@pytest.fixture(scope="module")
def real():
    return SnapshotProvider(config.SNAPSHOT_DIR, config.OVERRIDES_PATH)


def test_counts(real):
    assert (len(real.places("관광지")), len(real.places("음식점")), len(real.lodgings())) == (20, 18, 3)


def test_every_override_applied_and_marked(real):
    overrides = json.loads(config.OVERRIDES_PATH.read_text(encoding="utf-8"))
    for ov in overrides:
        item = real.place(ov["id"]) if ov["target"] == "place" else real.segment(ov["from"], ov["to"])
        assert item.is_overridden and item.override_reason == ov["reason"]
        assert set(ov["set"]) <= set(item.overridden)          # 원래 값이 남아 있다


def test_spots_have_hours_except_documented(real):
    # 종묘만 평일 시간제 관람이라 일부 요일이 비어 있다 (data/README.md)
    for p in real.places():
        if p.kind == "숙소":
            continue
        assert p.weekly_hours is not None, p.id
        if p.id != "a_jongmyo":
            assert len(p.weekly_hours) == 7, p.id


def test_segments_reference_known_places(real):
    ids = {p.id for p in real.places()}
    for s in json.loads((config.SNAPSHOT_DIR / "segments.json").read_text(encoding="utf-8")):
        assert s["from_id"] in ids and s["to_id"] in ids
        assert s["options"] and len(s["path"]) >= 2
