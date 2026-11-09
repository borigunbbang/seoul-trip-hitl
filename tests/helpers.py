from datetime import date, time

from trip.plan import DayPlan, Stop

FRI, SAT, SUN, MON, TUE, WED = (date(2026, 11, d) for d in (20, 21, 22, 23, 24, 25))
REF = date(2026, 11, 9)


def day(d: date, *stops: tuple, start: str = "09:30") -> DayPlan:
    """stops: (place_id, stay_min) 또는 (place_id, stay_min, meal)."""
    h, m = map(int, start.split(":"))
    return DayPlan(
        date=d, lodging_id="h_myeongdong", start_time=time(h, m),
        stops=[Stop(place_id=s[0], stay_min=s[1], meal=s[2] if len(s) > 2 else None) for s in stops],
    )


def mins(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def codes(issues) -> list[str]:
    return sorted(i.code for i in issues)
