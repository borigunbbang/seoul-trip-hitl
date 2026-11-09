"""화면 디자인: 톤다운된 가을(단풍) 팔레트와 약간의 CSS.

색·글꼴의 기본값은 .streamlit/config.toml의 테마가 맡고, 여기서는 테마로 안 되는 부분만 다룬다.
"""
from __future__ import annotations

PALETTE = {
    "paper": "#F7F2EA",      # 배경
    "oat": "#EFE6D8",        # 보조 배경
    "card": "#FBF8F2",       # 카드
    "ink": "#3A2E28",        # 글자
    "muted": "#8A7F75",      # 보조 글자
    "maple": "#A8462F",      # 단풍 (주 색)
    "maple_deep": "#7E3220",
    "persimmon": "#C27C3A",  # 감
    "ginkgo": "#C9A23B",     # 은행잎
    "olive": "#6F7D4E",
    "slate": "#5C6F78",      # 지도 경로 (따뜻한 화면과 대비되는 차분한 청회색)
    "line": "#E2D5C1",
    "flag": "#B23A26",       # 기준에 걸린 곳
}

CSS = f"""
<style>
/* 상단 여백과 본문 폭 */
.block-container {{ padding-top: 2.2rem; max-width: 1180px; }}

/* 제목: 글꼴은 테마(headingFont)가 맡고, 자간과 색만 */
h1, h2, h3 {{ letter-spacing: -0.01em; color: {PALETTE['ink']}; }}

/* 테두리 있는 컨테이너를 카드처럼 */
div[data-testid="stVerticalBlockBorderWrapper"]:has(> div > div[data-testid="stVerticalBlock"]) {{
  background: {PALETTE['card']};
  box-shadow: 0 1px 2px rgba(58,46,40,.05), 0 6px 18px rgba(58,46,40,.05);
}}

/* 사이드바 제목 아래 작은 설명 */
.side-tag {{ color: {PALETTE['muted']}; font-size: .82rem; margin-top: -.6rem; }}

/* 첫 화면 배너 */
.hero {{
  position: relative; overflow: hidden;
  border-radius: 22px; padding: 2.1rem 2.2rem 1.9rem;
  color: #FFF8EE;
  background:
    radial-gradient(circle at 88% 18%, rgba(201,162,59,.55) 0, rgba(201,162,59,0) 38%),
    radial-gradient(circle at 70% 120%, rgba(194,124,58,.55) 0, rgba(194,124,58,0) 45%),
    linear-gradient(135deg, {PALETTE['maple_deep']} 0%, {PALETTE['maple']} 55%, {PALETTE['persimmon']} 100%);
  box-shadow: 0 10px 30px rgba(126,50,32,.18);
  margin-bottom: 1.4rem;
}}
.hero .eyebrow {{ font-size: .8rem; letter-spacing: .18em; opacity: .85; text-transform: uppercase; }}
.hero h1 {{ color: #FFF8EE; font-size: 2.1rem; margin: .35rem 0 .5rem; padding: 0; }}
.hero p {{ margin: 0; opacity: .92; line-height: 1.6; max-width: 600px; }}
.hero .leaves {{ position: absolute; right: 1.4rem; top: 1rem; font-size: 2.4rem; opacity: .4; letter-spacing: .2rem;
               transform: rotate(-12deg); pointer-events: none; }}

/* 단계 안내 (입력 → 검사 → 확인) */
.steps {{ display: flex; gap: .6rem; flex-wrap: wrap; margin: .2rem 0 1.2rem; }}
.steps span {{
  background: {PALETTE['oat']}; color: {PALETTE['ink']};
  border: 1px solid {PALETTE['line']}; border-radius: 999px;
  padding: .28rem .8rem; font-size: .85rem;
}}

/* 표 위 작은 제목 */
.section-label {{ font-weight: 700; color: {PALETTE['maple_deep']}; margin: .4rem 0 .3rem; }}
</style>
"""


def hero(eyebrow: str, title: str, body: str) -> str:
    return (f'<div class="hero"><div class="eyebrow">{eyebrow}</div><h1>{title}</h1>'
            f'<p>{body}</p><div class="leaves">🍁🍂</div></div>')


def steps(*labels: str) -> str:
    return '<div class="steps">' + "".join(f"<span>{s}</span>" for s in labels) + "</div>"
