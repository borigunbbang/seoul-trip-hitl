from datetime import date, time

import pytest
from pydantic import ValidationError

from trip.models import Segment, TimeRange, to_min

FRI, SAT, SUN, MON, TUE = (date(2026, 11, d) for d in (20, 21, 22, 23, 24))


def m(hhmm: str) -> int:
    h, mm = map(int, hhmm.split(":"))
    return h * 60 + mm


def test_timerange_overnight():
    r = TimeRange(open=time(18, 0), close=time(2, 0))
    assert r.span_min() == (m("18:00"), m("02:00") + 24 * 60)
    assert r.covers(m("23:00"), m("23:59"))


def test_regular_closed_day_is_empty_not_none(provider):
    gyeongbok = provider.place("a_gyeongbok")
    assert gyeongbok.hours_on(TUE) == []          # 휴무 = 확실
    assert gyeongbok.is_open_during(TUE, m("10:00"), m("11:00")) is False
    assert gyeongbok.is_open_during(FRI, m("10:00"), m("11:00")) is True


def test_no_hours_info_is_none(provider):
    insadong = provider.place("a_insadong")
    assert insadong.hours_on(FRI) is None         # 정보 없음 = 불확실
    assert insadong.is_open_during(FRI, m("10:00"), m("11:00")) is None


def test_missing_weekday_key_is_none(provider):
    hanjeongsik = provider.place("r_hanjeongsik")
    assert hanjeongsik.hours_on(SAT) is not None
    assert hanjeongsik.hours_on(SUN) is None


def test_special_day_takes_priority(provider):
    changdeok = provider.place("a_changdeok")
    assert changdeok.special_day(SAT).note
    assert changdeok.is_open_during(SAT, m("10:00"), m("11:30")) is True
    assert changdeok.is_open_during(SAT, m("13:30"), m("15:00")) is False   # 평소엔 열려 있는 시각
    assert changdeok.is_open_during(FRI, m("13:30"), m("15:00")) is True


def test_visit_must_fit_inside_hours(provider):
    bukchon = provider.place("a_bukchon")       # 10:00~17:00
    assert bukchon.is_open_during(FRI, m("16:30"), m("17:00")) is True
    assert bukchon.is_open_during(FRI, m("16:30"), m("17:30")) is False


def test_segment_requires_option():
    with pytest.raises(ValidationError):
        Segment(from_id="a", to_id="b", distance_m=1, options=[], last_checked=date(2026, 11, 1))


def test_to_min():
    assert to_min(time(9, 30)) == 570
