"""LangGraph 그래프 두 개.

여행 그래프 (스레드 1개 = 여행 1건)
    find_candidates → allocate → END
하루 그래프 (스레드 1개 = 하루, thread_id = trip-{id}-day-{n})
    plan_day → check_fix ─(자동 확정)─────────────→ confirm → END
                   │
                   └─(불확실)→ review ⏸ interrupt ─(승인)→ confirm
                                     └─(반려+사유)→ replan → check_fix

check_fix 안에서 '검사 → 자동 수정'을 상한(3회)까지 반복한다(fixer.check_and_fix).
멈춘 하루는 체크포인터(SQLite)에 저장되어, 프로세스를 다시 켜도 같은 thread_id로 이어서 처리한다.
"""
from __future__ import annotations

from datetime import date

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

import config
from trip.candidates import find_restaurants
from trip.fixer import check_and_fix, fix_allocation
from trip.plan import DayPlan, Issue, Verdict
from trip.planner import Planner
from trip.provider import DataProvider
from trip.schedule import Schedule, hhmm
from trip.state import DayState, TripRequest, TripState


# ── 여행 그래프 ─────────────────────────────────────────────────────

def build_trip_graph(provider: DataProvider, planner: Planner, checkpointer=None, ref: date | None = None):
    ref = ref or config.REFERENCE_DATE

    def find_candidates(state: TripState) -> TripState:
        req = TripRequest.model_validate(state["request"])
        return {"restaurant_ids": find_restaurants(req.wanted_ids, provider)}

    def allocate(state: TripState) -> TripState:
        req = TripRequest.model_validate(state["request"])
        allocation, reason = planner.allocate(req.dates, req.wanted_ids, req.note)
        allocation, actions = fix_allocation(allocation, provider, ref)
        return {"allocation": {d.isoformat(): ids for d, ids in allocation.items()},
                "allocation_reason": reason,
                "allocation_actions": [a.model_dump(mode="json") for a in actions]}

    g = StateGraph(TripState)
    g.add_node("find_candidates", find_candidates)
    g.add_node("allocate", allocate)
    g.add_edge(START, "find_candidates")
    g.add_edge("find_candidates", "allocate")
    g.add_edge("allocate", END)
    return g.compile(checkpointer=checkpointer)


# ── 하루 그래프 ─────────────────────────────────────────────────────

def schedule_view(schedule: Schedule) -> list[dict]:
    """상세 화면·지도에 쓰는 시각표. 숙소 출발 → 장소들 → 숙소 도착."""
    rows = [{"kind": "숙소", "place_id": schedule.lodging.id, "name": schedule.lodging.name,
             "time": f"{hhmm(schedule.legs[0].depart_min)} 출발"}]
    for v, leg in zip(schedule.visits, schedule.legs):
        rows.append({
            "kind": v.place.kind, "place_id": v.place.id, "name": v.place.name, "meal": v.stop.meal,
            "arrive": hhmm(v.arrive_min), "start": hhmm(v.start_min), "end": hhmm(v.end_min),
            "time": f"{hhmm(v.start_min)}~{hhmm(v.end_min)}", "reason": v.stop.reason,
            "leg": {"from_id": leg.segment.from_id, "to_id": leg.segment.to_id, "minutes": leg.segment.minutes,
                    "mode": leg.segment.best.mode, "lines": leg.segment.best.lines,
                    "alternatives": leg.segment.alternatives, "source": leg.segment.source},
        })
    back = schedule.legs[-1]
    rows.append({"kind": "숙소", "place_id": schedule.lodging.id, "name": schedule.lodging.name,
                 "time": f"{hhmm(back.arrive_min)} 도착",
                 "leg": {"from_id": back.segment.from_id, "to_id": back.segment.to_id, "minutes": back.segment.minutes,
                         "mode": back.segment.best.mode, "lines": back.segment.best.lines,
                         "alternatives": back.segment.alternatives, "source": back.segment.source}})
    return rows


def build_day_graph(provider: DataProvider, planner: Planner, checkpointer=None, ref: date | None = None):
    ref = ref or config.REFERENCE_DATE

    def plan_day(state: DayState) -> DayState:
        plan, summary = planner.plan_day(date.fromisoformat(state["date"]), state["lodging_id"],
                                         state["attraction_ids"], state["restaurant_ids"],
                                         state["request"].get("note", ""))
        return {"plan": plan.model_dump(mode="json"), "summary": summary, "status": "planning",
                "reject_count": 0, "human_decision": None,
                "history": [{"event": "plan", "detail": f"LLM이 동선 작성 ({len(plan.stops)}곳)"}]}

    def check_fix(state: DayState) -> DayState:
        r = check_and_fix(DayPlan.model_validate(state["plan"]), provider, ref)
        waiting = r.verdict.decision == "needs_review"
        history = [{"event": "auto_fix", "detail": a.detail} for a in r.actions]
        history.append({"event": "check", "detail": "기준 통과 → 자동 확정" if not waiting
                        else f"불확실 {len(r.verdict.reasons)}건 → 사람 확인 대기"})
        return {"plan": r.plan.model_dump(mode="json"), "schedule": schedule_view(r.schedule),
                "issues": [i.model_dump(mode="json") for i in r.issues],
                "verdict": r.verdict.model_dump(mode="json"),
                "actions": [a.model_dump(mode="json") for a in r.actions],
                "status": "waiting" if waiting else "planning", "history": history}

    def route_after_check(state: DayState) -> str:
        return "review" if state["verdict"]["decision"] == "needs_review" else "confirm"

    def review(state: DayState) -> DayState:
        verdict = Verdict.model_validate(state["verdict"])
        decision = interrupt({
            "date": state["date"],
            "reasons": [r.message for r in verdict.reasons],
            "on_approve": "이 경로가 지도에 확정되어 여행자에게 전달됩니다.",
        })
        action = decision.get("action")
        if action not in ("approve", "reject"):
            raise ValueError(f"알 수 없는 결정: {decision}")
        note = decision.get("reason", "").strip()
        detail = (f"승인 — {note}" if note else "승인") if action == "approve" else f"반려 — {note or '사유 없음'}"
        return {"human_decision": decision, "history": [{"event": action, "detail": detail}]}

    def route_after_review(state: DayState) -> str:
        return "confirm" if state["human_decision"]["action"] == "approve" else "replan"

    def replan(state: DayState) -> DayState:
        previous = DayPlan.model_validate(state["plan"])
        reasons = [Issue.model_validate(i).message for i in state["verdict"]["reasons"]]
        plan, summary = planner.replan_day(date.fromisoformat(state["date"]), state["lodging_id"],
                                           state["attraction_ids"], state["restaurant_ids"],
                                           state["request"].get("note", ""), previous, reasons,
                                           state["human_decision"].get("reason", ""))
        return {"plan": plan.model_dump(mode="json"), "summary": summary, "human_decision": None,
                "reject_count": state.get("reject_count", 0) + 1, "status": "planning",
                "history": [{"event": "replan", "detail": f"반려 사유를 반영해 다시 작성 ({len(plan.stops)}곳)"}]}

    def confirm(state: DayState) -> DayState:
        by_human = bool(state.get("human_decision")) and state["human_decision"]["action"] == "approve"
        return {"status": "confirmed", "confirmed_by": "human" if by_human else "auto",
                "history": [{"event": "confirm", "detail": "사람이 승인해 확정" if by_human else "자동 확정"}]}

    g = StateGraph(DayState)
    for name, fn in [("plan_day", plan_day), ("check_fix", check_fix), ("review", review),
                     ("replan", replan), ("confirm", confirm)]:
        g.add_node(name, fn)
    g.add_edge(START, "plan_day")
    g.add_edge("plan_day", "check_fix")
    g.add_conditional_edges("check_fix", route_after_check, {"review": "review", "confirm": "confirm"})
    g.add_conditional_edges("review", route_after_review, {"confirm": "confirm", "replan": "replan"})
    g.add_edge("replan", "check_fix")
    g.add_edge("confirm", END)
    return g.compile(checkpointer=checkpointer)
