"""카카오 대중교통 경로 응답 파싱. 응답 형태는 2026-11-09에 실제로 받은 응답 구조를 줄여서 옮겼다."""
from trip.live_kakao import downsample, parse_transit


def route(kind, seconds, lines, transfers=0, fare=1550, points=None):
    return {"properties": {"type": kind, "totalTime": seconds, "transfers": transfers, "fare": {"value": fare}},
            "steps": [{"properties": {"type": kind, "vehicles": [{"name": n, "type": "일반"}]},
                       "path": {"points": points or [[126.97, 37.57], [126.98, 37.58]]}} for n in lines]}


def test_parse_sorts_dedupes_and_maps_modes():
    resp = {"status": "OK", "routes": [
        route("BUS", 1800, ["7212"]),
        route("SUBWAY", 1500, ["3호선", "5호선"], transfers=1, points=[[126.0, 37.0], [126.1, 37.1]]),
        route("BUS", 1900, ["7212"]),                       # 같은 노선 중복
        route("BUS_AND_SUBWAY", 2000, ["7212", "3호선"], 1),
    ]}
    options, path = parse_transit(resp)
    assert [(o.mode, o.minutes, o.lines) for o in options] == [
        ("SUBWAY", 25, ["3호선", "5호선"]), ("BUS", 30, ["7212"]), ("BUS_SUBWAY", 33, ["7212", "3호선"])]
    assert options[0].transfers == 1 and options[0].fare == 1550
    assert all(o.last_departure is None for o in options)    # 카카오는 막차를 주지 않는다
    assert path[0] == (37.0, 126.0)                           # [경도, 위도] → (위도, 경도)


def test_parse_empty():
    assert parse_transit({"status": "OK", "routes": []}) == ([], [])


def test_downsample_keeps_ends():
    pts = [(float(i), 0.0) for i in range(500)]
    out = downsample(pts, 80)
    assert len(out) == 80 and out[0] == pts[0] and out[-1] == pts[-1]
