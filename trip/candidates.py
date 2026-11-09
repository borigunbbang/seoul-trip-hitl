"""장소 후보 찾기.

관광지는 여행자가 고른 곳 그대로 쓴다. 음식점은 그 관광지들 근처에서 평점 순으로 고른다.
음식점 후보에서 영업 상태로 거르지 않는다. 데이터가 틀렸거나 낡은 경우를 걸러 내는 것은
규칙 검사(rules.py)의 일이고, 여기서 미리 빼면 실제 앱에서 생길 위험을 데모에서 볼 수 없다.
"""
from __future__ import annotations

from trip.models import haversine_m
from trip.provider import DataProvider

NEAR_M = 2500
MAX_RESTAURANTS = 12


def find_restaurants(wanted_ids: list[str], provider: DataProvider) -> list[str]:
    attractions = [provider.place(pid) for pid in wanted_ids]

    def nearest(p) -> int:
        return min(haversine_m(a.lat, a.lng, p.lat, p.lng) for a in attractions)

    near = [p for p in provider.places("음식점") if nearest(p) <= NEAR_M]
    near.sort(key=lambda p: (-(p.rating or 0), nearest(p), p.id))
    return [p.id for p in near[:MAX_RESTAURANTS]]
