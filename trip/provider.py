"""장소·교통 데이터를 가져오는 한 겹.

- SnapshotProvider (기본): 저장해 둔 JSON을 읽고, 기준 시연용 덮어쓰기 목록을 적용한다.
- LiveProvider (선택, 5단계): 같은 인터페이스로 구글 Places·카카오 경로를 바로 조회한다.

나머지 코드는 DataProvider만 알면 되고, 데이터가 어디서 왔는지는 몰라도 된다.
"""
from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from trip.models import Place, PlaceKind, Segment, TransitOption, haversine_m

WALK_M_PER_MIN = 67          # 시속 4km
WALK_DETOUR = 1.3            # 직선거리 대비 실제 걷는 거리
WALK_MAX_M = 1000            # 이보다 가까우면 추정 구간도 걷는 것으로 본다
TRANSIT_M_PER_MIN = 333      # 대기·환승 포함 시속 20km 정도
TRANSIT_OVERHEAD_MIN = 15    # 정류장까지 걷기·대기


class DataProvider(Protocol):
    def places(self, kind: PlaceKind | None = None) -> list[Place]: ...
    def place(self, place_id: str) -> Place: ...
    def segment(self, from_id: str, to_id: str) -> Segment: ...
    def lodgings(self) -> list[Place]: ...


def estimate_segment(a: Place, b: Place, checked: date) -> Segment:
    """수집하지 않은 구간. 직선거리로 시간을 짐작하고 '추정'으로 표시한다."""
    dist = haversine_m(a.lat, a.lng, b.lat, b.lng)
    if dist <= WALK_MAX_M:
        option = TransitOption(mode="WALK", minutes=max(1, math.ceil(dist * WALK_DETOUR / WALK_M_PER_MIN)))
    else:
        option = TransitOption(mode="ESTIMATE", minutes=TRANSIT_OVERHEAD_MIN + math.ceil(dist / TRANSIT_M_PER_MIN))
    return Segment(
        from_id=a.id, to_id=b.id, distance_m=dist, options=[option],
        path=[(a.lat, a.lng), (b.lat, b.lng)], last_checked=checked, source="추정",
    )


def apply_overrides(items: dict[Any, Any], overrides: list[dict], target: str) -> None:
    """덮어쓰기 목록을 적용한다. 바꾼 필드의 원래 값은 overridden에 남긴다."""
    for ov in overrides:
        if ov["target"] != target:
            continue
        key = ov["id"] if target == "place" else (ov["from"], ov["to"])
        if key not in items:
            raise KeyError(f"덮어쓰기 대상이 스냅샷에 없음: {target} {key}")
        obj = items[key]
        original = {f: obj.model_dump(mode="json").get(f) for f in ov["set"]}
        merged = obj.model_dump() | ov["set"] | {
            "overridden": obj.overridden | original,
            "override_reason": ov["reason"],
        }
        items[key] = type(obj).model_validate(merged)


class SnapshotProvider:
    """스냅샷 폴더(places.json, segments.json)와 덮어쓰기 파일을 읽는다."""

    def __init__(self, snapshot_dir: Path, overrides_path: Path | None = None, reference_date: date | None = None):
        from config import REFERENCE_DATE

        self.reference_date = reference_date or REFERENCE_DATE
        places = [Place.model_validate(p) for p in _load(snapshot_dir / "places.json")]
        segments = [Segment.model_validate(s) for s in _load(snapshot_dir / "segments.json")]
        self._places = {p.id: p for p in places}
        self._segments = {(s.from_id, s.to_id): s for s in segments}

        if overrides_path is not None and overrides_path.exists():
            overrides = _load(overrides_path)
            apply_overrides(self._places, overrides, "place")
            apply_overrides(self._segments, overrides, "segment")

    def places(self, kind: PlaceKind | None = None) -> list[Place]:
        return [p for p in self._places.values() if kind is None or p.kind == kind]

    def place(self, place_id: str) -> Place:
        return self._places[place_id]

    def lodgings(self) -> list[Place]:
        return self.places("숙소")

    def segment(self, from_id: str, to_id: str) -> Segment:
        if (from_id, to_id) in self._segments:
            return self._segments[(from_id, to_id)]
        if (to_id, from_id) in self._segments:
            return self._segments[(to_id, from_id)].reversed()
        return estimate_segment(self.place(from_id), self.place(to_id), self.reference_date)


def _load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))
