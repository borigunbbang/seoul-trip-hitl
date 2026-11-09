"""서울 여행 계획 HITL 데모 (Streamlit).

실행: .venv/bin/streamlit run app.py
화면: 새 여행 / 승인 대기(목록 → 상세 → 승인·반려) / 여행 보기(날짜별 지도)
"""
from __future__ import annotations

from datetime import date

import streamlit as st
import streamlit.components.v1 as components

import config
from trip.maps import folium_map, kakao_html, map_data
from trip.plan import Issue
from trip.planner import LLMPlanner
from trip.provider import SnapshotProvider
from trip.service import DayView, TripService
from trip.state import MAX_TRIP_DAYS, TripRequest

st.set_page_config(page_title="서울 여행 HITL", page_icon="🗺️", layout="wide")

FIXTURE_DIR = config.ROOT / "tests" / "fixtures"
WEEKDAY = "월화수목금토일"
STATUS_LABEL = {"waiting": "⏸ 승인 대기", "confirmed": "✅ 확정", "planning": "… 처리 중"}
MODE_LABEL = {"WALK": "도보", "BUS": "버스", "SUBWAY": "지하철", "BUS_SUBWAY": "버스+지하철", "ESTIMATE": "대중교통"}


# ── 준비 ──────────────────────────────────────────────────────────

@st.cache_resource
def get_service() -> tuple[TripService, str]:
    if (config.SNAPSHOT_DIR / "places.json").exists():
        provider = SnapshotProvider(config.SNAPSHOT_DIR, config.OVERRIDES_PATH)
        source = "스냅샷 (실제 수집 데이터)"
    else:
        provider = SnapshotProvider(FIXTURE_DIR / "snapshot", FIXTURE_DIR / "overrides.json")
        source = "시험용 데이터 (tests/fixtures)"
    return TripService(provider, LLMPlanner(provider)), source


svc, data_source = get_service()
provider = svc.provider


def kdate(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d:%m/%d}({WEEKDAY[d.weekday()]})"


def name(pid: str) -> str:
    return provider.place(pid).name


def confirmed_label(view: DayView) -> str:
    if view.status != "confirmed":
        return STATUS_LABEL[view.status]
    by = view.values.get("confirmed_by")
    if by == "human":
        return "✅ 사람이 승인해 확정"
    return "✅ 자동 확정" + (" (반려 후 재작성)" if view.values.get("reject_count") else "")


def draw_map(values: dict, height: int = 460) -> None:
    data = map_data(values, provider)
    if config.KAKAO_JS_KEY:
        components.html(kakao_html(data, config.KAKAO_JS_KEY, height), height=height + 30)
    else:
        from streamlit_folium import st_folium

        st.caption("카카오 키가 없어 OpenStreetMap으로 표시합니다.")
        st_folium(folium_map(data), height=height, use_container_width=True, returned_objects=[],
                  key=f"map-{values['trip_id']}-{values['day_index']}")


def schedule_table(values: dict) -> None:
    rows = []
    for r in values.get("schedule", []):
        leg = r.get("leg")
        move = ""
        if leg:
            mode = MODE_LABEL.get(leg["mode"], leg["mode"])
            move = f"{leg['minutes']}분 {mode} {' '.join(leg['lines'])}".strip()
            if leg["source"] == "추정":
                move += " (추정)"
        rows.append({"이동": move, "시각": r["time"], "장소": r["name"],
                     "종류": r["kind"] + (f"·{r['meal']}" if r.get("meal") else ""),
                     "고른 이유": r.get("reason", "")})
    st.dataframe(rows, hide_index=True, use_container_width=True)


def request_box(req: dict) -> None:
    wanted = ", ".join(name(p) for p in req["wanted_ids"])
    st.markdown(
        f"- **날짜**: {kdate(req['start_date'])} ~ {kdate(req['end_date'])}\n"
        f"- **가고 싶은 곳**: {wanted}\n"
        f"- **숙소**: {name(req['lodging_id'])}\n"
        f"- **요청 메모**: {req.get('note') or '(없음)'}")


def day_detail(view: DayView, *, allow_decision: bool) -> None:
    v = view.values
    st.subheader(f"여행 {view.trip_id} · {view.day_index}일차 {kdate(view.date)}  —  {confirmed_label(view)}")

    left, right = st.columns([5, 6], gap="large")
    with left:
        with st.container(border=True):
            st.markdown("**요청 원문**")
            request_box(v["request"])

        st.markdown("**에이전트의 판단과 근거**")
        st.write(v.get("summary", ""))

        actions = v.get("actions", [])
        if actions:
            st.markdown("**에이전트가 스스로 고친 것** (확실히 틀린 부분)")
            for a in actions:
                label = {"replace": "음식점 교체", "reorder": "순서 변경", "move_day": "날짜 이동"}[a["action"]]
                st.markdown(f"- {label}: {a['detail']}")

    with right:
        draw_map(v)

    schedule_table(v)

    reasons = [Issue.model_validate(i) for i in (v.get("verdict") or {}).get("reasons", [])]
    if view.status == "confirmed" and v.get("confirmed_by") == "human" and reasons:
        st.warning("**사람이 확인하고 승인한 항목**\n" + "\n".join(f"- {r.message}" for r in reasons))
    if view.status == "waiting":
        st.markdown("**멈춘 이유** — 아래 값 때문에 에이전트가 판단하지 않고 확인을 요청합니다")
        st.error("\n".join(f"- {r.message}" for r in reasons))
        st.info("**승인하면**: " + (view.interrupt or {}).get("on_approve", "이 경로가 지도에 확정됩니다."))
        if allow_decision:
            c1, c2 = st.columns([1, 3], gap="large")
            with c1:
                if st.button("승인", type="primary", use_container_width=True, key=f"ok-{view.thread_id}"):
                    svc.resume(view.thread_id, "approve")
                    st.session_state.flash = f"{view.day_index}일차를 승인해 확정했습니다."
                    st.rerun()
            with c2:
                with st.form(f"reject-{view.thread_id}", border=False):
                    reason = st.text_input("반려 사유 (그 날만 다시 짭니다)",
                                           placeholder="예: 인사동은 빼 주세요 / 점심은 다른 곳으로")
                    if st.form_submit_button("반려하고 다시 짜기", use_container_width=True):
                        if not reason.strip():
                            st.warning("반려 사유를 적어 주세요.")
                        else:
                            with st.spinner("반려 사유를 반영해 다시 짜는 중…"):
                                after = svc.resume(view.thread_id, "reject", reason)
                            st.session_state.flash = (f"{view.day_index}일차를 다시 짰습니다 → {confirmed_label(after)}")
                            st.rerun()

    with st.expander("진행 기록"):
        for h in v.get("history", []):
            st.markdown(f"- `{h['event']}` {h['detail']}")


# ── 화면 ──────────────────────────────────────────────────────────

pending = svc.pending()
with st.sidebar:
    st.title("서울 여행 HITL")
    # 선택지 값은 고정하고 글자만 바꾼다 (대기 건수가 바뀌어도 선택이 풀리지 않도록)
    page_labels = {"new": "새 여행", "pending": f"승인 대기 ({len(pending)})", "trips": "여행 보기"}
    page = st.radio("화면", list(page_labels), format_func=page_labels.get, key="page",
                    label_visibility="collapsed")
    st.divider()
    st.caption(f"데이터: {data_source}")
    st.caption(f"기준일: {config.REFERENCE_DATE} · 모델: {config.MODEL}")
    st.caption("지도: " + ("카카오맵" if config.KAKAO_JS_KEY else "OpenStreetMap (카카오 키 없음)"))

if msg := st.session_state.pop("flash", None):
    st.success(msg)

if page == "new":
    st.header("새 여행")
    st.caption("날짜·가고 싶은 곳·숙소를 넣으면 날짜별 동선을 짭니다. 기준에 걸리지 않은 날은 바로 확정되고, "
               "정보가 불확실한 날만 '승인 대기'로 갑니다.")
    attractions = sorted(provider.places("관광지"), key=lambda p: p.name)
    lodgings = provider.lodgings()
    with st.form("new-trip"):
        c1, c2 = st.columns(2)
        start = c1.date_input("시작일", value=date(2026, 11, 20))
        end = c2.date_input("종료일", value=date(2026, 11, 21))
        wanted = st.multiselect("가고 싶은 곳", attractions, format_func=lambda p: f"{p.name} ({p.area})",
                                default=attractions[:3])
        lodging = st.selectbox("숙소", lodgings, format_func=lambda p: p.name)
        note = st.text_input("요청 메모 (선택)", placeholder="예: 고궁 위주로, 점심은 한식")
        submitted = st.form_submit_button("동선 만들기", type="primary")
    if submitted:
        try:
            req = TripRequest(start_date=start, end_date=end, wanted_ids=[p.id for p in wanted],
                              lodging_id=lodging.id, note=note)
        except ValueError as e:
            st.error(f"입력을 확인해 주세요: 최대 {MAX_TRIP_DAYS}일, 가고 싶은 곳 1곳 이상, 종료일 ≥ 시작일. ({e.errors()[0]['msg'] if hasattr(e, 'errors') else e})")
        else:
            try:
                with st.spinner("동선을 짜고 검사하는 중… (하루에 10초 정도)"):
                    trip_id = svc.start_trip(req)
            except RuntimeError as e:
                st.error(str(e))
            else:
                days = svc.days(trip_id)
                auto = [d for d in days if d.status == "confirmed"]
                wait = [d for d in days if d.status == "waiting"]
                st.success(f"여행 {trip_id}: {len(days)}일 중 {len(auto)}일 자동 확정, {len(wait)}일 승인 대기")
                for d in days:
                    first = (d.interrupt or {}).get("reasons", [""])[0] if d.status == "waiting" else ""
                    st.markdown(f"- {d.day_index}일차 {kdate(d.date)}: {confirmed_label(d)}"
                                + (f" — {first}" if first else ""))
                st.caption("왼쪽 '승인 대기'에서 확인하세요.")

elif page == "pending":
    st.header("승인 대기")
    if not pending:
        st.info("승인을 기다리는 날이 없습니다.")
    else:
        trips = {t["trip_id"]: t for t in svc.trips()}
        labels = {}
        for d in pending:
            reasons = d.interrupt["reasons"]
            first = reasons[0].split("] ", 1)[-1]
            more = f" 외 {len(reasons) - 1}건" if len(reasons) > 1 else ""
            labels[d.thread_id] = f"여행 {d.trip_id} · {d.day_index}일차 {kdate(d.date)} — {first}{more}"
        if st.session_state.get("selected") not in labels:       # 방금 처리한 건은 목록에서 빠진다
            st.session_state.pop("selected", None)
        selected = st.radio("대기 목록", list(labels), format_func=labels.get, key="selected")
        st.divider()
        day_detail(svc.day(selected), allow_decision=True)

else:
    st.header("여행 보기")
    trips = svc.trips()
    if not trips:
        st.info("아직 만든 여행이 없습니다.")
    else:
        trip_ids = [t["trip_id"] for t in reversed(trips)]
        trip_id = st.selectbox("여행", trip_ids, format_func=lambda t: f"여행 {t}")
        days = svc.days(trip_id)
        trip = svc.trip(trip_id)
        st.caption("날짜별 배분: " + trip.get("allocation_reason", ""))
        for a in trip.get("allocation_actions", []):
            st.caption("날짜 이동(자동 수정): " + a["detail"])
        n = st.radio("날짜", range(len(days)), horizontal=True,
                     format_func=lambda i: f"{days[i].day_index}일차 {kdate(days[i].date)} {confirmed_label(days[i])}")
        day_detail(days[n], allow_decision=False)
        if days[n].status == "waiting":
            st.caption("이 날은 '승인 대기' 화면에서 승인하거나 반려할 수 있습니다.")
