"""스냅샷 수집 (카카오 공식 API + 수동 확인 정보).

  .venv/bin/python scripts/collect.py places     # 장소 좌표·주소 (카카오 장소 검색)
  .venv/bin/python scripts/collect.py segments   # 구간 (카카오 대중교통 경로, 이미 받은 구간은 건너뜀)
  .venv/bin/python scripts/collect.py build      # data/snapshot/*.json 만들기

영업 상태·영업시간·평점·최근 리뷰 날짜는 API로 얻을 수 없어서(구글 Places 미사용)
카카오맵 장소 페이지를 사람이 보듯 확인해 data/manual/place_details.json에 적는다.
받은 응답 원본은 data/raw/에 저장해 다시 실행할 때 API를 또 부르지 않는다.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402
from trip import live_kakao  # noqa: E402
from trip.models import Place, Segment, TransitOption, haversine_m  # noqa: E402

MANUAL = config.DATA_DIR / "manual"
RAW = config.DATA_DIR / "raw"
NEIGHBORS = 8            # 장소마다 가까운 몇 곳까지 구간을 모을지
WALK_ONLY_M = 700        # 이보다 가까우면 API를 부르지 않고 도보만
WALK_ALSO_M = 1500       # 이보다 가까우면 대중교통 경로에 도보도 대안으로 넣는다
WALK_M_PER_MIN = 67
WALK_DETOUR = 1.3


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# ── 1. 장소 ────────────────────────────────────────────────────────

def collect_places() -> None:
    out = {}
    for s in load(MANUAL / "seed.json")["places"]:
        docs = live_kakao.search_keyword(s["query"], size=10)
        doc = next((d for d in docs if d["id"] == s.get("kakao_id")), None) if s.get("kakao_id") else (docs or [None])[0]
        if doc is None:
            print("못 찾음:", s["id"], s["query"])
            continue
        out[s["id"]] = {"kakao_id": doc["id"], "kakao_name": doc["place_name"], "category": doc["category_name"],
                        "address": doc.get("road_address_name") or doc.get("address_name", ""),
                        "lat": float(doc["y"]), "lng": float(doc["x"]), "place_url": doc["place_url"]}
        print("ok", s["id"], doc["place_name"])
    save(RAW / "places_kakao.json", {"collected": date.today().isoformat(), "places": out})


# ── 2. 구간 ────────────────────────────────────────────────────────

def segment_pairs(coords: dict[str, tuple[float, float]], kinds: dict[str, str]) -> list[tuple[str, str]]:
    spots = [p for p in coords if kinds[p] != "숙소"]
    pairs: set[tuple[str, str]] = set()
    for a in spots:
        near = sorted((b for b in spots if b != a), key=lambda b: haversine_m(*coords[a], *coords[b]))[:NEIGHBORS]
        pairs |= {tuple(sorted((a, b))) for b in near}
    for h in (p for p in coords if kinds[p] == "숙소"):
        pairs |= {(h, b) for b in spots}
    return sorted(pairs)


def walk_option(dist: int) -> TransitOption:
    return TransitOption(mode="WALK", minutes=max(1, math.ceil(dist * WALK_DETOUR / WALK_M_PER_MIN)))


def collect_segments() -> None:
    places = load(RAW / "places_kakao.json")["places"]
    kinds = {s["id"]: s["kind"] for s in load(MANUAL / "seed.json")["places"]}
    coords = {pid: (p["lat"], p["lng"]) for pid, p in places.items()}
    pairs = segment_pairs(coords, kinds)
    todo = [(a, b) for a, b in pairs
            if haversine_m(*coords[a], *coords[b]) > WALK_ONLY_M and not (RAW / "transit" / f"{a}__{b}.json").exists()]
    print(f"구간 {len(pairs)}개 중 API 호출 필요 {len(todo)}개")
    for i, (a, b) in enumerate(todo, 1):
        resp = live_kakao.transit_route(*coords[a], *coords[b])
        save(RAW / "transit" / f"{a}__{b}.json", {"collected": date.today().isoformat(), "response": resp})
        if i % 20 == 0:
            print(f"  {i}/{len(todo)}")
    save(RAW / "pairs.json", pairs)


# ── 3. 스냅샷 만들기 ────────────────────────────────────────────────

def build() -> None:
    seed = {s["id"]: s for s in load(MANUAL / "seed.json")["places"]}
    kakao = load(RAW / "places_kakao.json")
    details = load(MANUAL / "place_details.json")
    places = []
    missing = []
    for pid, s in seed.items():
        k = kakao["places"][pid]
        d = details.get(pid, {})
        if s["kind"] != "숙소" and not d:
            missing.append(pid)
        place = Place(
            id=pid, name=s.get("name") or k["kakao_name"], kind=s["kind"], lat=k["lat"], lng=k["lng"],
            area=s["area"], address=k["address"],
            status=d.get("status", "OPERATIONAL" if s["kind"] == "숙소" else "UNKNOWN"),
            weekly_hours=d.get("weekly_hours"), special_days=d.get("special_days", []), notice=d.get("notice"),
            rating=d.get("rating"), review_count=d.get("review_count"), last_review_date=d.get("last_review_date"),
            stay_min=d.get("stay_min"),
            last_checked=d.get("last_checked", kakao["collected"]),
            source="카카오맵 장소 페이지 수동 확인" if d else "카카오 장소 검색",
        )
        places.append(place.model_dump(mode="json"))
    if missing:
        print("수동 확인 정보가 없는 장소:", ", ".join(missing))

    coords = {p["id"]: (p["lat"], p["lng"]) for p in places}
    segments = []
    for a, b in load(RAW / "pairs.json"):
        dist = haversine_m(*coords[a], *coords[b])
        raw_path = RAW / "transit" / f"{a}__{b}.json"
        if dist <= WALK_ONLY_M or not raw_path.exists():
            if dist > WALK_ONLY_M:
                continue                         # 받지 못한 구간은 실행할 때 추정한다
            options, path, checked = [walk_option(dist)], [], kakao["collected"]
            source = "도보(직선거리로 계산)"
        else:
            source = "카카오 대중교통 경로"
            raw = load(raw_path)
            options, path = live_kakao.parse_transit(raw["response"])
            checked = raw["collected"]
            if dist <= WALK_ALSO_M:
                options.append(walk_option(dist))
                options.sort(key=lambda o: o.minutes)
            if not options:
                continue
        path = [(round(lat, 5), round(lng, 5)) for lat, lng in [coords[a]] + path + [coords[b]]]
        segments.append(Segment(from_id=a, to_id=b, distance_m=dist, options=options, path=path,
                                last_checked=checked, source=source).model_dump(mode="json"))

    save(config.SNAPSHOT_DIR / "places.json", places)
    # 구간은 경로 좌표가 많아서 한 줄에 하나씩 쓴다 (파일 크기와 diff를 줄이려고)
    (config.SNAPSHOT_DIR / "segments.json").write_text(
        "[\n" + ",\n".join(json.dumps(s, ensure_ascii=False, separators=(",", ":")) for s in segments) + "\n]\n",
        encoding="utf-8")
    print(f"스냅샷: 장소 {len(places)}곳, 구간 {len(segments)}개 → {config.SNAPSHOT_DIR}")


if __name__ == "__main__":
    {"places": collect_places, "segments": collect_segments, "build": build}[sys.argv[1]]()
