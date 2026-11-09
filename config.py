"""한곳에 모아 둔 설정. 여기 값만 바꿔도 동작이 달라진다.

- 키는 .env에서만 읽는다. 없으면 빈 문자열이고, 키가 필요한 기능은 대체 방식으로 동작한다.
- 기준값(90일, 30일)은 시작값이다. 바꾸면 REPORT에 이유를 적는다.
"""
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

# ── 키 ─────────────────────────────────────────────
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
KAKAO_JS_KEY = os.environ.get("KAKAO_JS_KEY", "")
KAKAO_REST_KEY = os.environ.get("KAKAO_REST_KEY", "")
GOOGLE_PLACES_KEY = os.environ.get("GOOGLE_PLACES_KEY", "")

# ── LLM ────────────────────────────────────────────
# 실습과 같은 제공사·모델 등급. 동선 짜기는 지킬 조건이 많아 균형 등급으로 시작한다.
MODEL = os.environ.get("TRIP_MODEL", "gpt-5.6-terra")
TEMPERATURE = 0

# ── 승인 기준 ───────────────────────────────────────
REVIEW_STALE_DAYS = 90   # 기준 2: 최근 리뷰가 이보다 오래되면 폐업 의심
INFO_STALE_DAYS = 30     # 기준 4: 마지막 확인일이 이보다 오래되면 낡은 정보
MAX_FIX_ROUNDS = 3       # 자동 수정 반복 상한. 넘으면 그 날은 멈춤

# 경과일 계산 기준일. 실행 날짜와 상관없이 결과가 같도록 고정한다.
# 스냅샷을 수집하면 수집한 날짜로 바꾼다.
REFERENCE_DATE = date.fromisoformat(os.environ.get("TRIP_REFERENCE_DATE", "2026-11-09"))

# ── 경로 ───────────────────────────────────────────
DATA_DIR = ROOT / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshot"
OVERRIDES_PATH = DATA_DIR / "overrides.json"
CHECKPOINT_DB = DATA_DIR / "app.sqlite"
LLM_CACHE_DIR = ROOT / "cache" / "llm"
