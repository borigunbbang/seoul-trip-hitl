"""예시 시나리오는 저장소의 LLM 캐시만으로(키 없이) 불러와져야 한다."""
import pytest

import config
from trip.examples import load_examples
from trip.planner import LLMPlanner
from trip.provider import SnapshotProvider
from trip.service import TripService

pytestmark = pytest.mark.skipif(not (config.SNAPSHOT_DIR / "places.json").exists(), reason="스냅샷 없음")


class NoChat:
    def with_structured_output(self, schema):
        raise AssertionError("예시 시나리오는 캐시로만 돌아야 한다 (LLM 호출 금지)")


def test_load_examples_without_llm(tmp_path):
    provider = SnapshotProvider(config.SNAPSHOT_DIR, config.OVERRIDES_PATH)
    planner = LLMPlanner(provider, llm=NoChat())
    svc = TripService(provider, planner, tmp_path / "ex.sqlite")
    try:
        created = load_examples(svc)
        assert created == ["예시-s1-palaces", "예시-s2-namsan", "예시-s3-east", "예시-s4-weekend"]
        assert planner.calls == 0
        assert len(svc.pending()) == 4                       # REPORT의 승인 대기 4일과 같다
        assert load_examples(svc) == []                      # 두 번 눌러도 중복으로 만들지 않는다
        # 키 없이 반려 → 다시 짜기도 캐시로 된다 (REPORT와 같은 사유)
        view = svc.resume("trip-예시-s4-weekend-day-1", "reject", "창덕궁은 다음에 갈게요. 빼 주세요.")
        assert view.status == "confirmed" and planner.calls == 0
    finally:
        svc.close()
