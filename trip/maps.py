"""지도: 하루 상태 → 지도 데이터 → 카카오맵(키 있음) 또는 folium/OpenStreetMap(키 없음).

두 지도 모두 같은 지도 데이터(map_data)를 그린다.
- 지점: 순번, 이름, 방문 시각, 평점, 그 날 영업시간, 마지막 확인일, 걸린 기준
- 선: 실제 경로 좌표(없으면 두 지점 직선). 기준에 걸린 구간은 빨간 점선
"""
from __future__ import annotations

import json
from datetime import date

from trip.plan import Issue
from trip.provider import DataProvider

# 가을 테마(trip/theme.py)에 맞춘 색. 경로는 따뜻한 화면과 구분되게 차분한 청회색으로 둔다.
COLORS = {"route": "#5C6F78", "pin": "#9A5B34", "flag": "#B23A26", "lodging": "#3A2E28", "walk": "#9A8C7E"}


def _hours_text(place, d: date) -> str:
    hours = place.hours_on(d)
    if hours is None:
        return "정보 없음"
    if hours == []:
        return "휴무"
    text = ", ".join(f"{r.open:%H:%M}~{r.close:%H:%M}" for r in hours)
    special = place.special_day(d)
    return f"{text} (특별 운영: {special.note})" if special else text


def map_data(values: dict, provider: DataProvider) -> dict:
    """DayState 값으로 지도에 그릴 지점과 선을 만든다."""
    d = date.fromisoformat(values["date"])
    reasons = [Issue.model_validate(i) for i in (values.get("verdict") or {}).get("reasons", [])]
    flags: dict[str, list[str]] = {}
    for r in reasons:
        flags.setdefault(r.target_id, []).append(r.message)

    points, lines = [], []
    rows = values.get("schedule", [])
    order = 0
    for row in rows:
        p = provider.place(row["place_id"])
        is_lodging = row["kind"] == "숙소"
        if not is_lodging:
            order += 1
        if is_lodging and points and points[0]["id"] == p.id:
            pass                                   # 숙소는 지점 하나로 (출발·도착 같은 곳)
        else:
            info = [("시각", row.get("time", ""))]
            if not is_lodging:
                info += [("평점", f"{p.rating}" if p.rating is not None else "정보 없음"),
                         ("이 날 영업시간", _hours_text(p, d)),
                         ("마지막 확인", f"{p.last_checked}")]
                if p.is_overridden:
                    info.append(("데이터", "기준 시연용으로 값을 바꾼 항목"))
            points.append({"id": p.id, "name": p.name, "kind": row["kind"], "lat": p.lat, "lng": p.lng,
                           "label": "숙" if is_lodging else str(order), "info": info,
                           "flags": flags.get(p.id, [])})
        leg = row.get("leg")
        if leg:
            seg = provider.segment(leg["from_id"], leg["to_id"])
            seg_flags = flags.get(f"{leg['from_id']}>{leg['to_id']}", [])
            lines.append({"path": [list(pt) for pt in seg.path], "mode": seg.best.mode,
                          "label": f"{seg.minutes}분 · {' '.join(seg.best.lines) or seg.best.mode}",
                          "flags": seg_flags})
    return {"points": points, "lines": lines}


def kakao_html(data: dict, js_key: str, height: int = 460) -> str:
    """카카오맵 HTML. 데이터는 JSON으로 넘기고 화면 글자는 textContent로 넣는다(HTML 주입 방지)."""
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f"""
<div id="map" style="width:100%;height:{height}px;border-radius:16px;border:1px solid #E2D5C1"></div>
<div id="status" style="font:12px 'Noto Sans KR',sans-serif;color:#8A7F75;margin-top:6px"></div>
<style>
.pin{{width:28px;height:28px;border-radius:50%;color:#FFF8EE;font:bold 13px 'Noto Sans KR',sans-serif;display:flex;
     align-items:center;justify-content:center;border:2px solid #FFF8EE;box-shadow:0 2px 6px rgba(58,46,40,.35);cursor:pointer}}
.card{{background:#FFFDF8;border:1px solid #E2D5C1;border-radius:12px;padding:10px 12px;font:12px/1.55 'Noto Sans KR',sans-serif;
      color:#3A2E28;min-width:190px;max-width:260px;box-shadow:0 2px 8px rgba(0,0,0,.2);margin-bottom:20px}}
.card b{{font-size:13px;color:#7E3220}} .card .flag{{color:#B23A26;margin-top:4px}}
</style>
<script>
const DATA = {payload};
const C = {json.dumps(COLORS)};
const statusEl = document.getElementById('status');
const s = document.createElement('script');
s.src = 'https://dapi.kakao.com/v2/maps/sdk.js?appkey={js_key}&autoload=false';
s.onerror = () => statusEl.textContent = '카카오맵을 불러오지 못했습니다 (키·도메인 확인)';
s.onload = () => kakao.maps.load(draw);
document.head.appendChild(s);

function card(p) {{
  const el = document.createElement('div'); el.className = 'card';
  const t = document.createElement('b'); t.textContent = p.label + '. ' + p.name; el.appendChild(t);
  p.info.forEach(([k, v]) => {{ const r = document.createElement('div'); r.textContent = k + ': ' + v; el.appendChild(r); }});
  p.flags.forEach(f => {{ const r = document.createElement('div'); r.className = 'flag'; r.textContent = '⚠ ' + f; el.appendChild(r); }});
  return el;
}}

function draw() {{
  const map = new kakao.maps.Map(document.getElementById('map'), {{ center: new kakao.maps.LatLng(37.57, 126.98), level: 6 }});
  const bounds = new kakao.maps.LatLngBounds();
  let open = null;
  DATA.lines.forEach(l => {{
    const path = l.path.map(([a, b]) => new kakao.maps.LatLng(a, b));
    path.forEach(p => bounds.extend(p));
    const flagged = l.flags.length > 0, walk = l.mode === 'WALK';
    new kakao.maps.Polyline({{ map, path, strokeWeight: flagged ? 6 : 5,
      strokeColor: flagged ? C.flag : (walk ? C.walk : C.route), strokeOpacity: .85,
      strokeStyle: flagged || walk ? 'shortdash' : 'solid' }});
  }});
  DATA.points.forEach(p => {{
    const pos = new kakao.maps.LatLng(p.lat, p.lng); bounds.extend(pos);
    const pin = document.createElement('div'); pin.className = 'pin';
    pin.style.background = p.flags.length ? C.flag : (p.kind === '숙소' ? C.lodging : C.pin);
    pin.textContent = p.flags.length ? '⚠' : p.label; pin.title = p.name;
    new kakao.maps.CustomOverlay({{ map, position: pos, content: pin, yAnchor: .5, zIndex: 3 }});
    const info = new kakao.maps.CustomOverlay({{ position: pos, content: card(p), yAnchor: 1, zIndex: 5 }});
    pin.onclick = () => {{
      if (open) open.setMap(null);
      if (open === info) {{ open = null; return; }}
      info.setMap(map); open = info;
      // 정보 카드가 지도 위쪽으로 잘리지 않게, 누른 지점을 지도 아래쪽 1/3 높이로 옮긴다
      const proj = map.getProjection(), pt = proj.containerPointFromCoords(pos);
      const h = document.getElementById('map').offsetHeight;
      map.panTo(proj.coordsFromContainerPoint(new kakao.maps.Point(pt.x, pt.y - h * 0.18)));
    }};
  }});
  if (!bounds.isEmpty()) map.setBounds(bounds, 40, 40, 40, 40);
  statusEl.textContent = '지점을 누르면 평점·영업시간·마지막 확인일이 나옵니다. 빨간 표시는 기준에 걸린 곳입니다.';
  window.addEventListener('resize', () => map.relayout());
}}
</script>
"""


def folium_map(data: dict):
    """카카오 키가 없을 때 쓰는 OpenStreetMap 지도."""
    import html

    import folium

    m = folium.Map(tiles="OpenStreetMap", control_scale=True)
    coords = []
    for l in data["lines"]:
        flagged, walk = bool(l["flags"]), l["mode"] == "WALK"
        coords += l["path"]
        folium.PolyLine(l["path"], weight=6 if flagged else 5, opacity=.85,
                        color=COLORS["flag"] if flagged else (COLORS["walk"] if walk else COLORS["route"]),
                        dash_array="8" if flagged or walk else None, tooltip=l["label"]).add_to(m)
    for p in data["points"]:
        coords.append([p["lat"], p["lng"]])
        color = COLORS["flag"] if p["flags"] else (COLORS["lodging"] if p["kind"] == "숙소" else COLORS["pin"])
        body = "".join(f"<div>{html.escape(k)}: {html.escape(str(v))}</div>" for k, v in p["info"])
        body += "".join(f"<div style='color:{COLORS['flag']}'>⚠ {html.escape(f)}</div>" for f in p["flags"])
        popup = f"<b>{html.escape(p['label'])}. {html.escape(p['name'])}</b>{body}"
        icon = folium.DivIcon(html=(f"<div style='background:{color};color:#FFF8EE;width:26px;height:26px;border-radius:50%;"
                                    f"display:flex;align-items:center;justify-content:center;font-weight:bold;"
                                    f"border:2px solid #FFF8EE;box-shadow:0 2px 6px rgba(58,46,40,.35)'>{'⚠' if p['flags'] else html.escape(p['label'])}</div>"),
                              icon_size=(26, 26), icon_anchor=(13, 13))
        folium.Marker([p["lat"], p["lng"]], icon=icon, tooltip=p["name"],
                      popup=folium.Popup(popup, max_width=280)).add_to(m)
    if coords:
        m.fit_bounds(coords, padding=(30, 30))
    return m
