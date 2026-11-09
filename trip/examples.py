"""예시 시나리오 불러오기.

scenarios/scenarios.json의 여행 4건을 사람의 결정 없이 실행해 승인 대기 건을 만든다.
LLM 응답이 cache/llm/에 들어 있어서 OpenAI 키 없이도 돈다(REPORT와 같은 사유로 반려하는 것까지). 채점자가 저장소를 받자마자
'승인 대기 → 상세 → 승인·반려'를 해 볼 수 있게 하려는 것이다.
"""
from __future__ import annotations

import json

import config
from trip.service import TripService
from trip.state import TripRequest

SCENARIOS = config.ROOT / "scenarios" / "scenarios.json"


def load_examples(svc: TripService) -> list[str]:
    """아직 없는 예시 여행만 만든다. 만든 여행 id 목록을 돌려준다."""
    existing = {t["trip_id"] for t in svc.trips()}
    created = []
    for sc in json.loads(SCENARIOS.read_text(encoding="utf-8"))["scenarios"]:
        trip_id = f"예시-{sc['id']}"
        if trip_id in existing:
            continue
        svc.start_trip(TripRequest.model_validate(sc["request"]), trip_id=trip_id)
        created.append(trip_id)
    return created
