"""규칙 검사. 시각표를 받아 문제 목록을 돌려주는 순수 함수다(LLM 없음).

두 종류로 나눈다 (설계 5.2, 계획 0.4):
- certain   : 정보가 있고 최근 확인됐는데 일정이 틀림 → fixer가 고친다
- uncertain : 승인 기준 1~4에 걸림 → 사람에게 묻는다

막차 시각을 모르는 구간(None)은 기준 1의 '막차 이후'로 보지 않는다.
카카오 경로 API가 막차를 주지 않아서, 모르면 멈추게 하면 거의 모든 날이 멈추기 때문이다.
"""
from __future__ import annotations

from datetime import date

import config
from trip.models import Place, Segment, to_min
from trip.plan import Issue
from trip.schedule import Leg, Schedule, Visit, hhmm

TRANSIT_MODES = {"SUBWAY", "BUS", "BUS_SUBWAY", "ESTIMATE"}
LATE_NIGHT_CUTOFF = 4 * 60   # 막차가 이보다 이르면(예: 00:30) 다음날 새벽으로 본다


def days_since(d: date, ref: date) -> int:
    return (ref - d).days


def is_fresh(checked: date, ref: date) -> bool:
    return days_since(checked, ref) <= config.INFO_STALE_DAYS


def check(schedule: Schedule, ref: date | None = None) -> list[Issue]:
    ref = ref or config.REFERENCE_DATE
    issues: list[Issue] = []
    for visit in schedule.visits:
        issues += check_visit(visit, schedule, ref)
    names = [schedule.lodging.name] + [v.place.name for v in schedule.visits] + [schedule.lodging.name]
    for i, leg in enumerate(schedule.legs):
        issues += check_leg(leg, f"{names[i]} → {names[i + 1]}", ref)
    return issues


# ── 장소 ───────────────────────────────────────────────────────────

def check_visit(v: Visit, schedule: Schedule, ref: date) -> list[Issue]:
    p, d = v.place, schedule.plan.date
    is_food = p.kind == "음식점"
    fresh = is_fresh(p.last_checked, ref)
    out: list[Issue] = []

    def add(kind, code, value, threshold="", criterion=None):
        out.append(Issue(kind=kind, code=code, criterion=criterion, target=p.name, target_id=p.id,
                         stop_index=v.index, value=value + _override_mark(p), threshold=threshold))

    # 영업 상태
    if p.status == "CLOSED_PERMANENTLY" and fresh:
        add("certain", "CLOSED_PERMANENTLY", f"폐업 확인됨 ({p.last_checked} 확인)")
    elif p.status != "OPERATIONAL":
        label = {"CLOSED_PERMANENTLY": "폐업 표시(정보 오래됨)", "CLOSED_TEMPORARILY": "임시 휴업 표시",
                 "UNKNOWN": "영업 상태 정보 없음"}[p.status]
        if is_food:
            add("uncertain", "C2_NOT_OPERATIONAL", label, "영업 중", 2)
        else:
            add("uncertain", "C3_NOTICE", label, "영업 중", 3)

    # 공지 (임시 휴관·공사 등)
    if p.notice:
        add("uncertain", "C2_NOT_OPERATIONAL" if is_food else "C3_NOTICE", f"운영 공지: {p.notice}", "공지 없음",
            2 if is_food else 3)

    # 영업시간
    special = p.special_day(d)
    hours = p.hours_on(d)
    window = f"{hhmm(v.start_min)}~{hhmm(v.end_min)} 방문"
    if special is not None and not is_food:
        ranges = ", ".join(f"{r.open:%H:%M}~{r.close:%H:%M}" for r in special.hours) or "휴관"
        add("uncertain", "C3_SPECIAL_DAY", f"{d:%m/%d} 특별 운영일({ranges}) — {special.note or '사유 미상'}",
            "특별 운영일 아님", 3)
    elif hours is None:
        if is_food:
            add("uncertain", "C2_NO_HOURS", f"{d:%m/%d}({_kor_weekday(d)}) 영업시간 정보 없음", "영업시간 정보 있음", 2)
        else:
            add("uncertain", "C3_NO_HOURS", f"{d:%m/%d}({_kor_weekday(d)}) 운영시간 정보 없음", "운영시간 정보 있음", 3)
    elif fresh:
        if hours == []:
            add("certain", "CLOSED_DAY", f"{d:%m/%d}({_kor_weekday(d)}) 휴무일 방문")
        elif not p.is_open_during(d, v.start_min, v.end_min):
            ranges = ", ".join(f"{r.open:%H:%M}~{r.close:%H:%M}" for r in hours)
            add("certain", "OUTSIDE_HOURS", f"{window}, 영업시간 {ranges}")

    # 음식점 최근 리뷰 (폐업 의심)
    if is_food:
        if p.last_review_date is None:
            add("uncertain", "C2_STALE_REVIEW", "리뷰 날짜 정보 없음", f"{config.REVIEW_STALE_DAYS}일 이내", 2)
        elif (n := days_since(p.last_review_date, ref)) > config.REVIEW_STALE_DAYS:
            add("uncertain", "C2_STALE_REVIEW", f"최근 리뷰 {n}일 전({p.last_review_date})",
                f"{config.REVIEW_STALE_DAYS}일 이내", 2)

    # 낡은 정보
    if not fresh:
        add("uncertain", "C4_STALE_PLACE", f"마지막 확인 {days_since(p.last_checked, ref)}일 전({p.last_checked})",
            f"{config.INFO_STALE_DAYS}일 이내", 4)
    return out


# ── 이동 구간 ───────────────────────────────────────────────────────

def check_leg(leg: Leg, label: str, ref: date) -> list[Issue]:
    seg: Segment = leg.segment
    tid = f"{seg.from_id}>{seg.to_id}"
    out: list[Issue] = []

    def add(kind, code, value, threshold="", criterion=None):
        out.append(Issue(kind=kind, code=code, criterion=criterion, target=label, target_id=tid,
                         value=value + _override_mark(seg), threshold=threshold))

    if seg.best.mode not in TRANSIT_MODES:      # 걸어서 가는 구간은 노선이 사라질 위험이 없다
        return out

    if seg.is_estimate:
        add("certain", "ESTIMATED_SEGMENT", f"수집하지 않은 구간(직선 {seg.distance_m}m, 약 {seg.minutes}분 추정)")
        return out

    if seg.alternatives == 1:
        add("uncertain", "C1_NO_ALTERNATIVE", f"이동 수단이 {_option_label(seg)} 하나뿐", "대안 2개 이상", 1)

    last = seg.best.last_departure
    if last is not None:
        last_min = to_min(last)
        if last_min < LATE_NIGHT_CUTOFF:
            last_min += 24 * 60
        if leg.depart_min > last_min:
            add("uncertain", "C1_AFTER_LAST",
                f"{hhmm(leg.depart_min)} 출발 예정, 추천 경로({_option_label(seg)}) 막차 {last:%H:%M}",
                "막차 이전 출발", 1)

    if not is_fresh(seg.last_checked, ref):
        add("uncertain", "C4_STALE_SEGMENT", f"마지막 확인 {days_since(seg.last_checked, ref)}일 전({seg.last_checked})",
            f"{config.INFO_STALE_DAYS}일 이내", 4)
    return out


def _override_mark(item) -> str:
    """기준 시연을 위해 값을 바꾼 항목이면 사람이 보는 이유에도 그 사실을 붙인다."""
    return " ※시연용으로 바꾼 값" if item.is_overridden else ""


def _option_label(seg: Segment) -> str:
    o = seg.best
    mode = {"SUBWAY": "지하철", "BUS": "버스", "BUS_SUBWAY": "버스+지하철", "ESTIMATE": "추정", "WALK": "도보"}[o.mode]
    return f"{mode} {'·'.join(o.lines)}".strip()


def _kor_weekday(d: date) -> str:
    return "월화수목금토일"[d.weekday()]
