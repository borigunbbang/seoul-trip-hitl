"""LLM 플래너: 날짜별 배분, 하루 동선, 반려 후 다시 짜기.

- 출력은 구조화 출력(pydantic)으로 받고, 코드가 한 번 더 검증·보정한다
  (없는 id 제거, 빠진 관광지 추가, 체류 시간 범위 맞추기).
- 응답은 프롬프트 해시를 키로 cache/llm/에 저장한다. 같은 입력이면 다시 부르지 않으므로
  실행할 때마다 결과가 같고(재현성), 비용도 들지 않는다.
- ChatOpenAI는 캐시에 없을 때만 만든다. 그래서 캐시만 있으면 키 없이도 돈다.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field

import config
from trip import prompts
from trip.plan import DayPlan, Stop
from trip.provider import DataProvider

STAY_RANGE = {"관광지": (30, 180), "음식점": (40, 90)}


# ── LLM 출력 형식 ──────────────────────────────────────────────────

class DayAssignment(BaseModel):
    date: str = Field(description="YYYY-MM-DD")
    place_ids: list[str]


class AllocationOut(BaseModel):
    days: list[DayAssignment]
    reason: str


class StopOut(BaseModel):
    place_id: str
    stay_min: int
    meal: Literal["점심", "저녁"] | None = None
    reason: str


class DayPlanOut(BaseModel):
    stops: list[StopOut]
    summary: str


# ── 플래너 인터페이스 ───────────────────────────────────────────────

class Planner(Protocol):
    def allocate(self, dates: list[date], wanted_ids: list[str], note: str) -> tuple[dict[date, list[str]], str]: ...
    def plan_day(self, d: date, lodging_id: str, attraction_ids: list[str], restaurant_ids: list[str],
                 note: str) -> tuple[DayPlan, str]: ...
    def replan_day(self, d: date, lodging_id: str, attraction_ids: list[str], restaurant_ids: list[str],
                   note: str, previous: DayPlan, stop_reasons: list[str], feedback: str) -> tuple[DayPlan, str]: ...


class LLMCache:
    def __init__(self, directory: Path):
        self.dir = directory

    @staticmethod
    def key(model: str, schema: str, messages: list[tuple[str, str]]) -> str:
        raw = json.dumps({"model": model, "schema": schema, "messages": messages}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def get(self, key: str) -> dict | None:
        path = self.dir / f"{key}.json"
        return json.loads(path.read_text(encoding="utf-8"))["response"] if path.exists() else None

    def put(self, key: str, model: str, schema: str, messages: list[tuple[str, str]], response: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        record = {"model": model, "schema": schema, "messages": messages, "response": response}
        (self.dir / f"{key}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


class LLMPlanner:
    def __init__(self, provider: DataProvider, model: str | None = None, cache_dir: Path | None = None, llm=None):
        self.provider = provider
        self.model = model or config.MODEL
        self.cache = LLMCache(cache_dir or config.LLM_CACHE_DIR)
        self._llm = llm               # 테스트에서 가짜 채팅 모델을 넣을 수 있다
        self.calls = 0                # 실제로 LLM을 부른 횟수 (캐시 적중은 세지 않음)

    # ── 공통 호출 ──
    def _structured(self, schema: type[BaseModel], system: str, user: dict) -> BaseModel:
        messages = [("system", system), ("human", json.dumps(user, ensure_ascii=False, indent=1))]
        key = self.cache.key(self.model, schema.__name__, messages)
        cached = self.cache.get(key)
        if cached is not None:
            return schema.model_validate(cached)
        if self._llm is None:
            from langchain_openai import ChatOpenAI

            if not config.OPENAI_API_KEY:
                raise RuntimeError("LLM 캐시에 없는 요청인데 OPENAI_API_KEY가 없습니다. .env를 확인하세요.")
            self._llm = ChatOpenAI(model=self.model, temperature=config.TEMPERATURE, timeout=90, max_retries=2)
        out = self._llm.with_structured_output(schema).invoke(messages)
        self.calls += 1
        self.cache.put(key, self.model, schema.__name__, messages, out.model_dump(mode="json"))
        return out

    def _place_card(self, pid: str) -> dict:
        p = self.provider.place(pid)
        card = {"id": p.id, "name": p.name, "kind": p.kind, "area": p.area, "lat": round(p.lat, 5), "lng": round(p.lng, 5)}
        if p.kind == "음식점" and p.rating is not None:
            card["rating"] = p.rating
        return card

    # ── 날짜별 배분 ──
    def allocate(self, dates, wanted_ids, note):
        user = {"dates": [f"{d.isoformat()}({'월화수목금토일'[d.weekday()]})" for d in dates],
                "attractions": [self._place_card(pid) for pid in wanted_ids],
                "traveler_note": note}
        out: AllocationOut = self._structured(AllocationOut, prompts.ALLOCATE_SYSTEM, user)
        return normalize_allocation(out, dates, wanted_ids), out.reason

    # ── 하루 동선 ──
    def _day_user(self, d, lodging_id, attraction_ids, restaurant_ids, note) -> dict:
        return {"date": f"{d.isoformat()}({'월화수목금토일'[d.weekday()]})",
                "start_time": "09:30",
                "lodging": self._place_card(lodging_id),
                "attractions": [self._place_card(pid) for pid in attraction_ids],
                "restaurant_candidates": [self._place_card(pid) for pid in restaurant_ids],
                "traveler_note": note}

    def plan_day(self, d, lodging_id, attraction_ids, restaurant_ids, note):
        user = self._day_user(d, lodging_id, attraction_ids, restaurant_ids, note)
        out: DayPlanOut = self._structured(DayPlanOut, prompts.DAY_SYSTEM, user)
        return self._to_plan(out, d, lodging_id, attraction_ids, restaurant_ids), out.summary

    def replan_day(self, d, lodging_id, attraction_ids, restaurant_ids, note, previous, stop_reasons, feedback):
        user = self._day_user(d, lodging_id, attraction_ids, restaurant_ids, note)
        user["previous_plan"] = [{"place": self.provider.place(s.place_id).name, "id": s.place_id,
                                  "stay_min": s.stay_min, "meal": s.meal} for s in previous.stops]
        user["stop_reasons"] = stop_reasons
        user["human_feedback"] = feedback
        out: DayPlanOut = self._structured(DayPlanOut, prompts.REPLAN_SYSTEM, user)
        # 반려 후에는 사람이 뺀 관광지를 다시 넣지 않도록, 빠진 관광지를 자동으로 채우지 않는다
        return self._to_plan(out, d, lodging_id, attraction_ids, restaurant_ids, fill_missing=False), out.summary

    def _to_plan(self, out: DayPlanOut, d, lodging_id, attraction_ids, restaurant_ids, fill_missing=True) -> DayPlan:
        return normalize_day(out, d, lodging_id, attraction_ids, restaurant_ids, self.provider, fill_missing)


# ── LLM 출력 보정 (코드가 마지막에 한 번 더 확인) ─────────────────────

def normalize_allocation(out: AllocationOut, dates: list[date], wanted_ids: list[str]) -> dict[date, list[str]]:
    result: dict[date, list[str]] = {d: [] for d in dates}
    by_iso = {d.isoformat(): d for d in dates}
    seen: set[str] = set()
    for day in out.days:
        d = by_iso.get(day.date[:10])
        if d is None:
            continue
        for pid in day.place_ids:
            if pid in wanted_ids and pid not in seen:
                result[d].append(pid)
                seen.add(pid)
    for pid in wanted_ids:                       # LLM이 빠뜨린 관광지는 가장 한가한 날로
        if pid not in seen:
            target = min(dates, key=lambda x: (len(result[x]), x))
            result[target].append(pid)
    return result


def normalize_day(out: DayPlanOut, d: date, lodging_id: str, attraction_ids: list[str], restaurant_ids: list[str],
                  provider: DataProvider, fill_missing: bool = True) -> DayPlan:
    allowed = set(attraction_ids) | set(restaurant_ids)
    stops: list[Stop] = []
    seen: set[str] = set()
    for s in out.stops:
        if s.place_id not in allowed or s.place_id in seen:
            continue
        kind = provider.place(s.place_id).kind
        lo, hi = STAY_RANGE[kind]
        meal = s.meal if kind == "음식점" else None
        stops.append(Stop(place_id=s.place_id, stay_min=min(max(s.stay_min, lo), hi), meal=meal, reason=s.reason))
        seen.add(s.place_id)
    if fill_missing:
        for pid in attraction_ids:
            if pid not in seen:
                p = provider.place(pid)
                stops.append(Stop(place_id=pid, stay_min=p.default_stay_min, reason="배정된 관광지라 끝에 추가함(코드 보정)"))
    return DayPlan(date=d, lodging_id=lodging_id, stops=stops)
