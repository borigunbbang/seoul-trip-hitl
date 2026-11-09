"""HITL 파이프라인: 자동 확정 / 멈춤 / 승인 / 반려 후 재작성 / 프로세스 재시작 후 재개."""
import json
import subprocess
import sys
from datetime import date

import pytest

from tests.conftest import FIXTURES, ROOT
from tests.fakes import FakePlanner
from tests.helpers import FRI, REF, SAT, TUE, WED
from trip.provider import SnapshotProvider
from trip.service import TripService
from trip.state import TripRequest

CLEAN_FRI = [("a_gyeongbok", 120), ("r_tosokchon", 60, "점심"), ("a_bukchon", 60)]
# 토요일: 폐업 식당(자동 수정됨) + 창덕궁 특별 운영일(기준 3 → 멈춤)
RISKY_SAT = [("a_changdeok", 90), ("r_closed", 60, "점심")]
REPLAN_SAT = [("a_gyeongbok", 120), ("r_tosokchon", 60, "점심"), ("a_bukchon", 60)]

REQUEST = TripRequest(start_date=FRI, end_date=SAT, wanted_ids=["a_gyeongbok", "a_bukchon", "a_changdeok"],
                      lodging_id="h_myeongdong", note="고궁 위주로")
ALLOC = {FRI: ["a_gyeongbok", "a_bukchon"], SAT: ["a_changdeok"]}


@pytest.fixture
def make_service(provider, tmp_path):
    services = []

    def make(planner, db="app.sqlite"):
        s = TripService(provider, planner, tmp_path / db, ref=REF)
        services.append(s)
        return s

    yield make
    for s in services:
        s.close()


def planner(**kw):
    return FakePlanner({FRI: CLEAN_FRI, SAT: RISKY_SAT}, replans={SAT: REPLAN_SAT}, allocation=ALLOC, **kw)


def test_only_uncertain_day_stops(make_service):
    svc = make_service(planner())
    trip_id = svc.start_trip(REQUEST, trip_id="t1")
    fri, sat = svc.days(trip_id)

    assert (fri.status, fri.values["confirmed_by"]) == ("confirmed", "auto")
    assert fri.values["verdict"]["reasons"] == []

    assert sat.status == "waiting"
    assert [p.thread_id for p in svc.pending()] == ["trip-t1-day-2"]
    # 멈춘 이유가 구체적인 값으로 나온다
    assert any("특별 운영일(09:00~13:00)" in r for r in sat.interrupt["reasons"])
    assert sat.interrupt["on_approve"].startswith("이 경로가 지도에 확정")
    # 폐업 식당은 사람에게 묻지 않고 이미 고쳐져 있다
    assert "r_closed" not in [s["place_id"] for s in sat.values["plan"]["stops"]]
    assert [a["action"] for a in sat.values["actions"]] == ["replace"]


def test_approve_confirms_by_human(make_service):
    svc = make_service(planner())
    svc.start_trip(REQUEST, trip_id="t1")
    view = svc.resume("trip-t1-day-2", "approve")
    assert (view.status, view.values["confirmed_by"]) == ("confirmed", "human")
    assert svc.pending() == []
    assert [h["event"] for h in view.values["history"]][-2:] == ["approve", "confirm"]


def test_reject_replans_only_that_day(make_service):
    p = planner()
    svc = make_service(p)
    svc.start_trip(REQUEST, trip_id="t1")
    fri_before = svc.day("trip-t1-day-1").values

    view = svc.resume("trip-t1-day-2", "reject", "창덕궁은 다음에 갈게요. 빼 주세요.")
    assert p.feedback[0][1] == "창덕궁은 다음에 갈게요. 빼 주세요."
    assert any("특별 운영일" in r for r in p.feedback[0][2])          # 멈춘 이유도 플래너에 전달
    assert view.status == "confirmed" and view.values["confirmed_by"] == "auto"   # 다시 짠 계획은 기준 통과
    assert view.values["reject_count"] == 1
    assert "a_changdeok" not in [s["place_id"] for s in view.values["plan"]["stops"]]
    assert svc.day("trip-t1-day-1").values == fri_before                         # 다른 날은 그대로


def test_later_days_get_unused_restaurants(make_service, provider):
    seen = []

    class Recording(FakePlanner):
        def plan_day(self, d, lodging_id, attraction_ids, restaurant_ids, note):
            seen.append(list(restaurant_ids))
            return super().plan_day(d, lodging_id, attraction_ids, restaurant_ids, note)

    svc = make_service(Recording({FRI: CLEAN_FRI, SAT: RISKY_SAT}, allocation=ALLOC))
    svc.start_trip(REQUEST, trip_id="t3")
    assert "r_tosokchon" in seen[0] and "r_tosokchon" not in seen[1]   # 금요일에 쓴 토속촌은 토요일 후보에서 빠짐


def test_reject_needs_reason(make_service):
    svc = make_service(planner())
    svc.start_trip(REQUEST, trip_id="t1")
    with pytest.raises(ValueError, match="사유"):
        svc.resume("trip-t1-day-2", "reject", "  ")
    with pytest.raises(ValueError, match="승인 대기"):
        svc.resume("trip-t1-day-1", "approve")


def test_reject_then_still_risky_stops_again(make_service):
    p = FakePlanner({FRI: CLEAN_FRI, SAT: RISKY_SAT}, replans={SAT: RISKY_SAT}, allocation=ALLOC)
    svc = make_service(p)
    svc.start_trip(REQUEST, trip_id="t1")
    view = svc.resume("trip-t1-day-2", "reject", "다른 식당으로")
    assert view.status == "waiting" and view.values["reject_count"] == 1


def test_allocation_avoids_closed_day(make_service):
    # 화요일에 배정된 경복궁(화요일 휴궁)은 수요일로 옮겨진다
    req = TripRequest(start_date=TUE, end_date=WED, wanted_ids=["a_gyeongbok", "a_folk"], lodging_id="h_myeongdong")
    p = FakePlanner({TUE: [("a_folk", 60)], WED: [("a_gyeongbok", 120)]},
                    allocation={TUE: ["a_gyeongbok", "a_folk"], WED: []})
    svc = make_service(p)
    trip_id = svc.start_trip(req, trip_id="t2")
    trip = svc.trip(trip_id)
    assert trip["allocation"] == {"2026-11-24": ["a_folk"], "2026-11-25": ["a_gyeongbok"]}
    assert trip["allocation_actions"][0]["detail"] == "경복궁: 11/24 정기 휴무 → 11/25로 이동"


# ── 프로세스를 끝내고 새 프로세스에서 재개 ─────────────────────────────

CHILD = """
import sys, json
sys.path.insert(0, {root!r})
from datetime import date
from pathlib import Path
from tests.fakes import FakePlanner
from tests.test_graphs import REQUEST, CLEAN_FRI, RISKY_SAT, REPLAN_SAT, ALLOC, FRI, SAT
from trip.provider import SnapshotProvider
from trip.service import TripService
provider = SnapshotProvider(Path({fixtures!r}) / "snapshot", Path({fixtures!r}) / "overrides.json", reference_date=date(2026, 11, 9))
svc = TripService(provider, FakePlanner({{FRI: CLEAN_FRI, SAT: RISKY_SAT}}, {{SAT: REPLAN_SAT}}, ALLOC), Path({db!r}), ref=date(2026, 11, 9))
step = sys.argv[1]
if step == "start":
    svc.start_trip(REQUEST, trip_id="restart")
elif step == "approve":
    svc.resume("trip-restart-day-2", "approve")
print(json.dumps({{"pending": [d.thread_id for d in svc.pending()],
                  "statuses": [d.status for d in svc.days("restart")],
                  "confirmed_by": [d.values.get("confirmed_by") for d in svc.days("restart")]}}))
"""


def run_child(step: str, db) -> dict:
    code = CHILD.format(root=str(ROOT), fixtures=str(FIXTURES), db=str(db))
    out = subprocess.run([sys.executable, "-c", code, step], capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_resume_after_process_restart(tmp_path):
    db = tmp_path / "restart.sqlite"
    first = run_child("start", db)          # 프로세스 1: 실행하고 끝남
    assert first == {"pending": ["trip-restart-day-2"], "statuses": ["confirmed", "waiting"],
                     "confirmed_by": ["auto", None]}
    second = run_child("approve", db)       # 프로세스 2: 새로 켜서 같은 thread_id로 재개
    assert second == {"pending": [], "statuses": ["confirmed", "confirmed"], "confirmed_by": ["auto", "human"]}
