from tests.helpers import FRI, day, mins
from trip.schedule import build_schedule, hhmm


def test_times_add_up(provider):
    # 숙소 09:30 출발 → 경복궁 22분 → 120분 체류 → 숙소로 22분
    s = build_schedule(day(FRI, ("a_gyeongbok", 120)), provider)
    v = s.visits[0]
    assert (v.arrive_min, v.start_min, v.end_min) == (mins("09:52"), mins("09:52"), mins("11:52"))
    assert (s.legs[-1].depart_min, s.end_min) == (mins("11:52"), mins("12:14"))
    assert s.travel_min == 44


def test_waits_until_opening(provider):
    # 08:00 출발 → 북촌 08:25 도착, 10:00에 열 때까지 기다린다
    s = build_schedule(day(FRI, ("a_bukchon", 60), start="08:00"), provider)
    v = s.visits[0]
    assert v.arrive_min == mins("08:25")
    assert (v.start_min, v.end_min) == (mins("10:00"), mins("11:00"))


def test_no_wait_when_hours_unknown(provider):
    s = build_schedule(day(FRI, ("a_insadong", 60), start="07:00"), provider)
    assert s.visits[0].start_min == s.visits[0].arrive_min


def test_hhmm():
    assert hhmm(mins("09:05")) == "09:05"
    assert hhmm(24 * 60 + 30) == "다음날 00:30"
