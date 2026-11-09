from tests.fakes import FakePlanner
from tests.helpers import REF, SAT
from trip.maps import folium_map, kakao_html, map_data
from trip.service import TripService
from trip.state import TripRequest


def waiting_day(provider, tmp_path):
    # 토요일: 북촌 → 창덕궁(대안 없는 버스, 특별 운영일)
    svc = TripService(provider, FakePlanner({SAT: [("a_bukchon", 60), ("a_changdeok", 90)]},
                                            allocation={SAT: ["a_bukchon", "a_changdeok"]}), tmp_path / "m.sqlite", ref=REF)
    req = TripRequest(start_date=SAT, end_date=SAT, wanted_ids=["a_bukchon", "a_changdeok"], lodging_id="h_myeongdong")
    svc.start_trip(req, trip_id="m")
    view = svc.day("trip-m-day-1")
    svc.close()
    return view


def test_map_data_points_lines_and_flags(provider, tmp_path):
    view = waiting_day(provider, tmp_path)
    assert view.status == "waiting"
    data = map_data(view.values, provider)

    assert [(p["label"], p["name"]) for p in data["points"]] == [("숙", "숙소(명동)"), ("1", "북촌한옥마을"), ("2", "창덕궁")]
    assert len(data["lines"]) == 3                       # 숙소→북촌, 북촌→창덕궁, 창덕궁→숙소

    changdeok = data["points"][2]
    assert any("특별 운영일" in f for f in changdeok["flags"])
    assert ("이 날 영업시간", "09:00~13:00 (특별 운영: 궁중문화 행사로 오후 관람 제한)") in changdeok["info"]
    assert not data["points"][1]["flags"]

    flagged_lines = [l for l in data["lines"] if l["flags"]]
    assert len(flagged_lines) == 1 and "하나뿐" in flagged_lines[0]["flags"][0]


def test_map_renderers(provider, tmp_path):
    data = map_data(waiting_day(provider, tmp_path).values, provider)
    html = kakao_html(data, "TESTKEY")
    assert "appkey=TESTKEY" in html and "북촌한옥마을" in html
    assert "</script>" not in html.split("const DATA = ")[1].split(";\n")[0]   # 데이터 안에 스크립트 종료 태그 없음
    folium_map(data)   # 예외 없이 만들어진다
