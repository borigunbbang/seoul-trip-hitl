"""서울 여행 계획 HITL 데모 (Streamlit).

실행: .venv/bin/streamlit run app.py
화면: 새 여행 / 승인 대기(목록 → 상세 → 승인·반려) / 여행 보기(날짜별 지도)
"""
from __future__ import annotations

from datetime import date

import streamlit as st

import config
from trip.examples import load_examples
from trip.maps import folium_map, kakao_html, map_data
from trip.plan import Issue
from trip.planner import LLMPlanner
from trip.provider import SnapshotProvider
from trip.service import DayView, TripService
from trip.state import MAX_TRIP_DAYS, TripRequest
from trip.theme import CSS, hero, steps

st.set_page_config(page_title="서울 여행 HITL", page_icon="🍁", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)

FIXTURE_DIR = config.ROOT / "tests" / "fixtures"
WEEKDAY = "월화수목금토일"
MODE_LABEL = {"WALK": "도보", "BUS": "버스", "SUBWAY": "지하철", "BUS_SUBWAY": "버스+지하철", "ESTIMATE": "대중교통"}


# ── 준비 ──────────────────────────────────────────────────────────

USING_SNAPSHOT = (config.SNAPSHOT_DIR / "places.json").exists()


@st.cache_resource
def get_service() -> tuple[TripService, str]:
    if USING_SNAPSHOT:
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


def md(text: str) -> str:
    """마크다운에서 ~ 두 개가 취소선이 되지 않게 한다 (예: '11:00~15:00, 17:00~21:00')."""
    return text.replace("~", "\\~")


def status_text(view: DayView) -> str:
    """일반 글자 상태 (알림 문구·선택지용)."""
    if view.status == "waiting":
        return "승인 대기"
    if view.status != "confirmed":
        return "처리 중"
    if view.values.get("confirmed_by") == "human":
        return "사람이 승인해 확정"
    return "자동 확정" + (" (반려 후 재작성)" if view.values.get("reject_count") else "")


def status_badge(view: DayView) -> str:
    """테마 색을 따르는 배지 (제목용)."""
    color = {"승인 대기": "red", "사람이 승인해 확정": "orange"}.get(status_text(view), "green")
    return f":{color}-badge[{status_text(view)}]"


def label(text: str) -> None:
    st.markdown(f'<div class="section-label">{text}</div>', unsafe_allow_html=True)


def reason_line(r: Issue) -> str:
    tag = f"기준 {r.criterion}" if r.criterion else ("자동 수정 실패" if r.code == "FIX_FAILED" else "확인 필요")
    tail = f" · 기준 {md(r.threshold)}" if r.threshold else ""
    return f"- :red-badge[{tag}] **{md(r.target)}** — {md(r.value)}{tail}"


def draw_map(values: dict, height: int = 460) -> None:
    data = map_data(values, provider)
    if config.KAKAO_JS_KEY:
        # 지도 HTML은 스냅샷 데이터로만 만든다(LLM 출력 없음). 화면 글자는 JS에서 textContent로 넣는다.
        st.iframe(kakao_html(data, config.KAKAO_JS_KEY, height), height=height + 30)
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
    st.dataframe(rows, hide_index=True, width="stretch")


def request_box(req: dict) -> None:
    wanted = ", ".join(name(p) for p in req["wanted_ids"])
    st.markdown(
        f"- **날짜**: {kdate(req['start_date'])} ~ {kdate(req['end_date'])}\n"
        f"- **가고 싶은 곳**: {wanted}\n"
        f"- **숙소**: {name(req['lodging_id'])}\n"
        f"- **요청 메모**: {md(req.get('note') or '(없음)')}")


def day_detail(view: DayView, *, allow_decision: bool) -> None:
    v = view.values
    st.subheader(f"{view.day_index}일차 · {kdate(view.date)}")
    st.markdown(f"{status_badge(view)} &nbsp; :gray[여행 {view.trip_id}]")

    left, right = st.columns([5, 6], gap="large")
    with left:
        with st.container(border=True):
            label("요청 원문")
            request_box(v["request"])

        label("에이전트의 판단과 근거")
        st.write(md(v.get("summary", "")))

        actions = v.get("actions", [])
        if actions:
            label("에이전트가 스스로 고친 것")
            for a in actions:
                kind = {"replace": "음식점 교체", "reorder": "순서 변경", "move_day": "날짜 이동"}[a["action"]]
                st.markdown(f"- :green-badge[{kind}] {md(a['detail'])}")
            st.caption("※ 위 요약은 자동 수정 전에 에이전트가 쓴 글입니다. 실제 동선은 아래 표가 기준입니다.")

    with right:
        draw_map(v)

    label("하루 동선")
    schedule_table(v)

    reasons = [Issue.model_validate(i) for i in (v.get("verdict") or {}).get("reasons", [])]
    if view.status == "confirmed" and v.get("confirmed_by") == "human" and reasons:
        st.warning("**사람이 확인하고 승인한 항목**\n\n" + "\n".join(reason_line(r) for r in reasons))
    if view.status == "waiting":
        label("멈춘 이유 — 아래 값 때문에 에이전트가 판단하지 않고 확인을 요청합니다")
        st.error("\n".join(reason_line(r) for r in reasons))
        st.info("**승인하면** " + (view.interrupt or {}).get("on_approve", "이 경로가 지도에 확정됩니다."), icon="🗺️")
        if allow_decision:
            c1, c2 = st.columns([1, 3], gap="large")
            with c1:
                if st.button("승인", type="primary", width="stretch", key=f"ok-{view.thread_id}"):
                    svc.resume(view.thread_id, "approve")
                    st.session_state.flash = f"{view.day_index}일차를 승인해 확정했습니다."
                    st.rerun()
            with c2:
                with st.form(f"reject-{view.thread_id}", border=False):
                    reason = st.text_input("반려 사유 (그 날만 다시 짭니다)",
                                           placeholder="예: 인사동은 빼 주세요 / 점심은 다른 곳으로")
                    if st.form_submit_button("반려하고 다시 짜기", width="stretch"):
                        if not reason.strip():
                            st.warning("반려 사유를 적어 주세요.")
                        else:
                            with st.spinner("반려 사유를 반영해 다시 짜는 중…"):
                                after = svc.resume(view.thread_id, "reject", reason)
                            st.session_state.flash = f"{view.day_index}일차를 다시 짰습니다 → {status_text(after)}"
                            st.rerun()

    with st.expander("진행 기록"):
        for h in v.get("history", []):
            st.markdown(f"- `{h['event']}` {md(h['detail'])}")


# ── 화면 ──────────────────────────────────────────────────────────

pending = svc.pending()
if goto := st.session_state.pop("goto", None):        # 위젯을 만들기 전에만 선택을 바꿀 수 있다
    st.session_state.page = goto
with st.sidebar:
    st.title("🍂 서울 여행 HITL")
    st.markdown('<div class="side-tag">확인된 길만 걷는 가을 서울</div>', unsafe_allow_html=True)
    # 메뉴 글자는 고정한다. 글자가 바뀌면 Streamlit이 다른 위젯으로 보고 선택을 처음으로 되돌린다.
    page_labels = {"new": "새 여행", "pending": "승인 대기", "trips": "여행 보기"}
    page = st.radio("화면", list(page_labels), format_func=page_labels.get, key="page",
                    label_visibility="collapsed")
    st.markdown(f":red-badge[승인 대기 {len(pending)}건]" if pending else ":green-badge[승인 대기 없음]")
    if USING_SNAPSHOT:
        if st.button("예시 시나리오 불러오기", width="stretch",
                     help="REPORT의 여행 4건(승인 대기 4일 포함)을 만듭니다. 저장된 LLM 응답을 써서 OpenAI 키가 없어도 됩니다."):
            with st.spinner("예시 여행 4건을 만드는 중…"):
                created = load_examples(svc)
            st.session_state.flash = (f"예시 여행 {len(created)}건을 불러왔습니다. 승인 대기 건을 확인해 보세요." if created
                                      else "예시 여행은 이미 불러와 있습니다.")
            st.session_state.goto = "pending"
            st.rerun()
    st.divider()
    st.caption(f"데이터: {data_source}")
    st.caption(f"기준일: {config.REFERENCE_DATE} · 모델: {config.MODEL}")
    st.caption("지도: " + ("카카오맵" if config.KAKAO_JS_KEY else "OpenStreetMap (카카오 키 없음)"))

if msg := st.session_state.pop("flash", None):
    st.success(md(msg), icon="🍁")

if page == "new":
    st.markdown(hero("Seoul · Autumn trip planner", "가을 서울, 확인된 길로만",
                     "날짜와 가고 싶은 곳, 숙소를 알려 주세요. 에이전트가 날짜별 동선을 짜고, "
                     "확실히 틀린 부분은 스스로 고칩니다. 정보가 불확실한 날만 여행자에게 확인을 받습니다."),
                unsafe_allow_html=True)
    st.markdown(steps("① 동선 짜기", "② 영업시간·교통 검사", "③ 틀린 곳 자동 수정", "④ 불확실한 날만 확인 요청"),
                unsafe_allow_html=True)
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
                    st.markdown(f"- {d.day_index}일차 {kdate(d.date)} &nbsp; {status_badge(d)}"
                                + (f" &nbsp; {md(first)}" if first else ""))
                st.caption("왼쪽 '승인 대기'에서 확인하세요.")

elif page == "pending":
    st.header("승인 대기")
    st.caption("에이전트가 정보를 확신하지 못해 멈춘 날입니다. 이유를 보고 승인하거나, 사유를 적어 반려하세요.")
    if not pending:
        st.info("승인을 기다리는 날이 없습니다. '새 여행'에서 동선을 만들거나, 왼쪽의 '예시 시나리오 불러오기'를 누르세요"
                "(키 없이 승인 대기 4건이 생깁니다).")
    else:
        trips = {t["trip_id"]: t for t in svc.trips()}
        labels = {}
        for d in pending:
            reasons = d.interrupt["reasons"]
            first = reasons[0].split("] ", 1)[-1]
            more = f" 외 {len(reasons) - 1}건" if len(reasons) > 1 else ""
            labels[d.thread_id] = f"**{d.day_index}일차 {kdate(d.date)}** · {d.trip_id} — {md(first)}{more}"
        if st.session_state.get("selected") not in labels:       # 방금 처리한 건은 목록에서 빠진다
            st.session_state.pop("selected", None)
        selected = st.radio("대기 목록", list(labels), format_func=labels.get, key="selected")
        st.divider()
        day_detail(svc.day(selected), allow_decision=True)

else:
    st.header("여행 보기")
    st.caption("날짜별로 확정된 동선과 지도를 봅니다.")
    trips = svc.trips()
    if not trips:
        st.info("아직 만든 여행이 없습니다.")
    else:
        trip_ids = [t["trip_id"] for t in reversed(trips)]
        trip_id = st.selectbox("여행", trip_ids, format_func=lambda t: f"여행 {t}")
        days = svc.days(trip_id)
        trip = svc.trip(trip_id)
        st.caption("날짜별 배분: " + md(trip.get("allocation_reason", "")))
        for a in trip.get("allocation_actions", []):
            st.caption("날짜 이동(자동 수정): " + md(a["detail"]))
        n = st.radio("날짜", range(len(days)), horizontal=True,
                     format_func=lambda i: f"{days[i].day_index}일차 {kdate(days[i].date)} · {status_text(days[i])}")
        day_detail(days[n], allow_decision=False)
        if days[n].status == "waiting":
            st.caption("이 날은 '승인 대기' 화면에서 승인하거나 반려할 수 있습니다.")
