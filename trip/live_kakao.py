"""카카오 공식 REST API 호출 (장소 검색, 대중교통 경로).

- 장소 검색: GET /v2/local/search/keyword.json (하루 100,000건 무료)
- 대중교통 경로: GET /v2/routing/publictraffic (하루 1,000건 무료)
  응답에 첫차·막차 시각은 없다. 그래서 last_departure는 항상 None이다.
"""
from __future__ import annotations

import time

import httpx

import config
from trip.models import TransitOption

BASE = "https://dapi.kakao.com"
MAX_OPTIONS = 5
MAX_PATH_POINTS = 80


def _headers() -> dict:
    if not config.KAKAO_REST_KEY:
        raise RuntimeError(".env에 KAKAO_REST_KEY가 없습니다.")
    return {"Authorization": f"KakaoAK {config.KAKAO_REST_KEY}"}


def search_keyword(query: str, size: int = 5) -> list[dict]:
    r = httpx.get(f"{BASE}/v2/local/search/keyword.json", headers=_headers(),
                  params={"query": query, "size": size}, timeout=15)
    r.raise_for_status()
    time.sleep(0.15)
    return r.json().get("documents", [])


def transit_route(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> dict:
    r = httpx.get(f"{BASE}/v2/routing/publictraffic", headers=_headers(), timeout=20,
                  params={"start_x": f"{a_lng}", "start_y": f"{a_lat}", "end_x": f"{b_lng}", "end_y": f"{b_lat}"})
    time.sleep(0.25)
    if r.status_code == 429:
        raise RuntimeError("카카오 대중교통 경로 하루 무료 한도(1,000건)를 넘었습니다. 내일 이어서 실행하세요.")
    r.raise_for_status()
    return r.json()


def parse_transit(resp: dict) -> tuple[list[TransitOption], list[tuple[float, float]]]:
    """경로 응답 → (이동 방법 목록, 추천 경로 좌표). 경로가 없으면 ([], [])."""
    routes = resp.get("routes") or []
    options: list[TransitOption] = []
    seen = set()
    best_path: list[tuple[float, float]] = []
    for route in sorted(routes, key=lambda r: r["properties"]["totalTime"]):
        props = route["properties"]
        lines = [v["name"] for s in route.get("steps", []) for v in (s["properties"].get("vehicles") or [])[:1]]
        mode = {"SUBWAY": "SUBWAY", "BUS": "BUS"}.get(props.get("type"), "BUS_SUBWAY")
        key = (mode, tuple(lines))
        if key in seen:
            continue
        seen.add(key)
        if not options:
            best_path = [(lat, lng) for s in route.get("steps", []) for lng, lat in (s.get("path") or {}).get("points", [])]
        options.append(TransitOption(mode=mode, minutes=max(1, round(props["totalTime"] / 60)),
                                     transfers=props.get("transfers", 0), lines=lines,
                                     fare=(props.get("fare") or {}).get("value")))
        if len(options) == MAX_OPTIONS:
            break
    return options, downsample(best_path)


def downsample(path: list[tuple[float, float]], limit: int = MAX_PATH_POINTS) -> list[tuple[float, float]]:
    if len(path) <= limit:
        return path
    step = (len(path) - 1) / (limit - 1)
    return [path[round(i * step)] for i in range(limit)]
