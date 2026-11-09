"""REPORT용 시나리오 일괄 실행.

  .venv/bin/python scripts/run_scenarios.py

- 실제 스냅샷 + 시연용 덮어쓰기 + LLM 응답 캐시(cache/llm)로 돈다. 캐시에 있으면 LLM을 부르지 않으므로
  두 번 실행해도 결과가 같다(results/results.json을 비교해 확인).
- 매번 새 체크포인터(results/scenarios.sqlite)로 시작한다.
- 승인 대기에 들어간 날에는 scenarios.json의 decisions(사람의 결정)를 넣어 재개한다.
- 결과: results/results.json(기계용), results/results.md(REPORT에 옮길 표)
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402
from trip.plan import Issue  # noqa: E402
from trip.planner import LLMPlanner  # noqa: E402
from trip.provider import SnapshotProvider  # noqa: E402
from trip.service import DayView, TripService  # noqa: E402
from trip.state import TripRequest  # noqa: E402

OUT = config.ROOT / "results"
WEEKDAY = "월화수목금토일"


def day_record(provider, d: DayView) -> dict:
    v = d.values
    reasons = [Issue.model_validate(i) for i in v["verdict"]["reasons"]]
    return {
        "day": d.day_index, "date": d.date, "status": d.status, "confirmed_by": v.get("confirmed_by"),
        "stops": [provider.place(s["place_id"]).name for s in v["plan"]["stops"]],
        "auto_fixes": [f"{a['action']}: {a['detail']}" for a in v.get("actions", [])],
        "reasons": [r.message for r in reasons],
        "criteria": sorted({r.criterion for r in reasons if r.criterion} | ({0} if any(r.code == "FIX_FAILED" for r in reasons) else set())),
        "reject_count": v.get("reject_count", 0),
        "history": [h["event"] for h in v.get("history", [])],
    }


def main() -> None:
    OUT.mkdir(exist_ok=True)
    db = OUT / "scenarios.sqlite"
    for f in OUT.glob("scenarios.sqlite*"):
        f.unlink()
    provider = SnapshotProvider(config.SNAPSHOT_DIR, config.OVERRIDES_PATH)
    planner = LLMPlanner(provider)
    svc = TripService(provider, planner, db)
    spec = json.loads((config.ROOT / "scenarios" / "scenarios.json").read_text(encoding="utf-8"))

    results = []
    for sc in spec["scenarios"]:
        req = TripRequest.model_validate(sc["request"])
        svc.start_trip(req, trip_id=sc["id"])
        trip = svc.trip(sc["id"])
        before = [day_record(provider, d) for d in svc.days(sc["id"])]
        decisions = []
        for d in svc.days(sc["id"]):
            dec = sc["decisions"].get(str(d.day_index))
            if d.status == "waiting" and dec:
                after = svc.resume(d.thread_id, dec["action"], dec.get("reason", ""))
                decisions.append({"day": d.day_index, **dec, "result": day_record(provider, after)})
        results.append({
            "id": sc["id"], "title": sc["title"], "request": sc["request"],
            "allocation": {k: [provider.place(p).name for p in v] for k, v in trip["allocation"].items()},
            "allocation_fixes": [a["detail"] for a in trip.get("allocation_actions", [])],
            "days_before_human": before, "decisions": decisions,
            "days_final": [day_record(provider, d) for d in svc.days(sc["id"])],
        })
    svc.close()

    (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (OUT / "results.md").write_text(to_markdown(results), encoding="utf-8")
    print(f"LLM 실제 호출 {planner.calls}회 (나머지는 캐시)")
    print((OUT / "results.md").read_text(encoding="utf-8"))


def kdate(iso: str) -> str:
    from datetime import date
    d = date.fromisoformat(iso)
    return f"{d:%m/%d}({WEEKDAY[d.weekday()]})"


def to_markdown(results: list[dict]) -> str:
    days = [d for r in results for d in r["days_before_human"]]
    auto = [d for d in days if d["status"] == "confirmed"]
    waiting = [d for d in days if d["status"] == "waiting"]
    fixed_days = [d for d in days if d["auto_fixes"]]
    alloc_fixes = sum(len(r["allocation_fixes"]) for r in results)
    crit = Counter(c for d in waiting for c in d["criteria"])
    final = [d for r in results for d in r["days_final"]]
    decisions = [x for r in results for x in r["decisions"]]

    lines = [
        "# 시나리오 실행 결과", "",
        f"- 여행 {len(results)}건, 날짜 {len(days)}일",
        f"- **자동 확정 {len(auto)}일, 승인 대기 {len(waiting)}일** (멈춘 비율 {len(waiting) / len(days):.0%})",
        f"- 에이전트가 스스로 고친 날 {len(fixed_days)}일 (하루 안 수정 {sum(len(d['auto_fixes']) for d in days)}건), "
        f"날짜 이동 {alloc_fixes}건",
        "- 멈춘 날의 기준별 건수(하루에 여러 기준 가능): "
        + ", ".join(f"{'자동 수정 실패' if c == 0 else f'기준 {c}'} {n}일" for c, n in sorted(crit.items())),
        f"- 사람의 결정 {len(decisions)}건: "
        + ", ".join(f"{x['action']} {n}" for x, n in [({'action': a}, sum(1 for y in decisions if y['action'] == a))
                                                      for a in ("approve", "reject")]),
        f"- 최종: 확정 {sum(1 for d in final if d['status'] == 'confirmed')}일, "
        f"대기 {sum(1 for d in final if d['status'] == 'waiting')}일", "",
    ]
    for r in results:
        req = r["request"]
        lines += [f"## {r['id']} — {r['title']}", "",
                  f"요청: {kdate(req['start_date'])}~{kdate(req['end_date'])}, 메모 \"{req['note']}\"", ""]
        for f in r["allocation_fixes"]:
            lines.append(f"- 날짜 이동(자동 수정): {f}")
        lines += ["", "| 일차 | 동선 | 자동 수정 | 판정 | 멈춘 이유 |", "|---|---|---|---|---|"]
        for d in r["days_before_human"]:
            verdict = "✅ 자동 확정" if d["status"] == "confirmed" else "⏸ 승인 대기"
            lines.append(f"| {d['day']} {kdate(d['date'])} | {' → '.join(d['stops'])} | "
                         f"{'<br>'.join(d['auto_fixes']) or '-'} | {verdict} | {'<br>'.join(d['reasons']) or '-'} |")
        for x in r["decisions"]:
            res = x["result"]
            outcome = {"confirmed": "확정", "waiting": "다시 대기"}[res["status"]]
            lines.append("")
            lines.append(f"- 사람의 결정 — {x['day']}일차: **{'승인' if x['action'] == 'approve' else '반려'}**"
                         + (f" (사유: {x['reason']})" if x.get("reason") else "")
                         + f" → {outcome} ({res['confirmed_by'] or '-'}), 동선: {' → '.join(res['stops'])}"
                         + (f", 남은 이유: {'; '.join(res['reasons'])}" if res["status"] == "waiting" else ""))
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
