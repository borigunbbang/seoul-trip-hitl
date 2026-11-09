"""여행 서비스: 그래프 실행, 여행 목록, 대기 목록, 재개.

Streamlit 화면과 시나리오 실행 스크립트는 이 클래스만 쓴다.
- 체크포인트(각 스레드의 State)는 LangGraph SqliteSaver가 저장한다.
- 여행 목록(어떤 여행에 어떤 하루 스레드가 있는지)은 같은 DB 파일의 trips 표에 둔다.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

import config
from trip.graphs import build_day_graph, build_trip_graph
from trip.planner import Planner
from trip.provider import DataProvider
from trip.state import TripRequest


def open_checkpointer(db_path: Path) -> tuple[SqliteSaver, sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    conn.execute("""CREATE TABLE IF NOT EXISTS trips (
        trip_id TEXT PRIMARY KEY, created_at TEXT, request TEXT, day_threads TEXT)""")
    conn.commit()
    return saver, conn


def day_thread_id(trip_id: str, n: int) -> str:
    return f"trip-{trip_id}-day-{n}"


def cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


@dataclass
class DayView:
    thread_id: str
    trip_id: str
    day_index: int
    date: str
    status: str          # planning | waiting | confirmed
    values: dict
    interrupt: dict | None


class TripService:
    def __init__(self, provider: DataProvider, planner: Planner, db_path: Path | None = None,
                 ref: date | None = None):
        self.provider = provider
        self.planner = planner
        self.saver, self.conn = open_checkpointer(db_path or config.CHECKPOINT_DB)
        self.trip_graph = build_trip_graph(provider, planner, self.saver, ref)
        self.day_graph = build_day_graph(provider, planner, self.saver, ref)

    def close(self) -> None:
        self.conn.close()

    # ── 실행 ──
    def start_trip(self, request: TripRequest, trip_id: str | None = None) -> str:
        trip_id = trip_id or datetime.now().strftime("%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        trip = self.trip_graph.invoke({"trip_id": trip_id, "request": request.model_dump(mode="json")},
                                      cfg(f"trip-{trip_id}"))
        threads = []
        used_restaurants: set[str] = set()    # 앞선 날에 넣은 음식점은 다음 날 후보에서 뺀다
        for n, d in enumerate(request.dates, start=1):
            tid = day_thread_id(trip_id, n)
            threads.append(tid)
            candidates = [r for r in trip["restaurant_ids"] if r not in used_restaurants] or trip["restaurant_ids"]
            result = self.day_graph.invoke({
                "trip_id": trip_id, "day_index": n, "date": d.isoformat(),
                "request": request.model_dump(mode="json"), "lodging_id": request.lodging_id,
                "attraction_ids": trip["allocation"][d.isoformat()],
                "restaurant_ids": candidates,
                "actions": [], "history": [],
            }, cfg(tid))
            used_restaurants |= {s["place_id"] for s in result["plan"]["stops"]}
        self.conn.execute("INSERT OR REPLACE INTO trips VALUES (?, ?, ?, ?)",
                          (trip_id, datetime.now().isoformat(timespec="seconds"),
                           request.model_dump_json(), json.dumps(threads)))
        self.conn.commit()
        return trip_id

    def resume(self, thread_id: str, action: str, reason: str = "") -> DayView:
        """대기 중인 하루에 사람의 결정을 넣고, 멈춘 지점(review)부터 이어서 실행한다."""
        view = self.day(thread_id)
        if view.status != "waiting" or view.interrupt is None:
            raise ValueError(f"{thread_id}는 승인 대기 상태가 아니다 (현재: {view.status})")
        if action == "reject" and not reason.strip():
            raise ValueError("반려할 때는 사유를 적어야 한다")
        self.day_graph.invoke(Command(resume={"action": action, "reason": reason}), cfg(thread_id))
        return self.day(thread_id)

    # ── 조회 ──
    def trips(self) -> list[dict]:
        rows = self.conn.execute("SELECT trip_id, created_at, request, day_threads FROM trips ORDER BY created_at")
        return [{"trip_id": t, "created_at": c, "request": json.loads(r), "day_threads": json.loads(d)}
                for t, c, r, d in rows]

    def trip(self, trip_id: str) -> dict:
        state = self.trip_graph.get_state(cfg(f"trip-{trip_id}")).values
        record = next(t for t in self.trips() if t["trip_id"] == trip_id)
        return {**record, **state}

    def day(self, thread_id: str) -> DayView:
        snap = self.day_graph.get_state(cfg(thread_id))
        v = snap.values
        waiting = "review" in snap.next and bool(snap.interrupts)
        return DayView(thread_id, v["trip_id"], v["day_index"], v["date"],
                       "waiting" if waiting else v.get("status", "planning"), v,
                       snap.interrupts[0].value if waiting else None)

    def days(self, trip_id: str) -> list[DayView]:
        record = next(t for t in self.trips() if t["trip_id"] == trip_id)
        return [self.day(tid) for tid in record["day_threads"]]

    def pending(self) -> list[DayView]:
        return [d for t in self.trips() for d in self.days(t["trip_id"]) if d.status == "waiting"]
