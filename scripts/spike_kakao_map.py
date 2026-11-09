"""1.5 카카오맵 시험: Streamlit 화면 안(iframe)에서 카카오맵이 뜨는지 확인한다.

실행: .venv/bin/streamlit run scripts/spike_kakao_map.py --server.port 8501
확인할 것: 지도 타일이 보이고, 마커 2개와 선 1개가 그려지고, 아래 상태 줄이 '성공'이다.
"""
import json
import sys
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

st.set_page_config(page_title="카카오맵 시험", page_icon="🗺️")
st.title("카카오맵 시험")

if not config.KAKAO_JS_KEY:
    st.error(".env에 KAKAO_JS_KEY가 없습니다. 키를 넣고 이 페이지를 새로 고치세요.")
    st.stop()

points = [
    {"name": "경복궁", "lat": 37.579617, "lng": 126.977041},
    {"name": "북촌한옥마을", "lat": 37.582604, "lng": 126.983998},
]

html = """
<div id="map" style="width:100%;height:440px;border-radius:8px"></div>
<p id="status" style="font:14px sans-serif;margin:8px 0">상태: 불러오는 중…</p>
<script>
const POINTS = __POINTS__;
const statusEl = document.getElementById('status');
const report = (msg) => { statusEl.innerText = '상태: ' + msg; };

const s = document.createElement('script');
s.src = 'https://dapi.kakao.com/v2/maps/sdk.js?appkey=__KEY__&autoload=false';
s.onerror = () => report('실패 — SDK 스크립트를 불러오지 못함 (키 또는 도메인 확인)');
s.onload = () => {
  if (!window.kakao || !kakao.maps) { report('실패 — kakao.maps 객체 없음'); return; }
  kakao.maps.load(() => {
    const path = POINTS.map(p => new kakao.maps.LatLng(p.lat, p.lng));
    const map = new kakao.maps.Map(document.getElementById('map'), { center: path[0], level: 5 });
    POINTS.forEach((p, i) => {
      const marker = new kakao.maps.Marker({ map, position: path[i], title: p.name });
      const info = new kakao.maps.InfoWindow({ content: '<div style="padding:4px 8px">' + p.name + '</div>' });
      kakao.maps.event.addListener(marker, 'click', () => info.open(map, marker));
    });
    new kakao.maps.Polyline({ map, path, strokeWeight: 5, strokeColor: '#2563eb', strokeOpacity: 0.9 });
    const bounds = new kakao.maps.LatLngBounds(); path.forEach(p => bounds.extend(p)); map.setBounds(bounds);
    // 타일 이미지가 실제로 받아졌는지 확인 (도메인이 막히면 타일이 비어 있다)
    setTimeout(() => {
      const tiles = [...document.querySelectorAll('#map img')].filter(img => img.naturalWidth > 0);
      report(tiles.length > 0 ? '성공 — 타일 ' + tiles.length + '장, 마커 ' + POINTS.length + '개, 선 1개'
                              : '실패 — 지도 객체는 만들어졌지만 타일이 없음 (도메인 등록 확인)');
    }, 3000);
  });
};
document.head.appendChild(s);
</script>
"""
html = html.replace("__POINTS__", json.dumps(points, ensure_ascii=False)).replace("__KEY__", config.KAKAO_JS_KEY)

components.html(html, height=500)
st.caption("마커를 누르면 장소 이름이 나옵니다.")
