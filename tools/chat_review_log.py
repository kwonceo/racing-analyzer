# -*- coding: utf-8 -*-
"""대표가 **채팅으로 말한 픽**을 3층 복기 기록(logs/review_owner/<YYYYMMDD>.jsonl)에 적는다.

대표(2026-09-26): 「1번 2번 다 진행해」 — ① 대화에 남은 픽을 3층으로 옮긴다 ② 앞으로 채팅 픽은 그 자리에서 적는다.
원칙
  · 원칙 27: **결과보다 먼저 말한 것만 phase="pre"**. 결과를 들은 뒤 말한 것은 "post"(성적 계산에서 빠진다 — review_ui 규칙과 같다).
    마권 캡처는 발주 전 구매 증거라 pre 로 본다(source 에 명시).
  · review_ui.py 와 같은 파일·schema 2 · append 전용(같은 경주 재기록은 새 줄 · 지우지 않는다).
  · source="chat" 으로 8013 화면 입력과 구분한다. sec_to_post 는 모른다(None).
사용
  python tools/chat_review_log.py '{"date":"2026_09_26","rk":"오비히로 9경주","phase":"pre","axis":5,"picks":[5,6,8,10],
        "my_pairs":[[5,6],[5,8],[5,10],[6,10]],"my_trios":[],"note":"…","claude":{"axis":5,"pairs":[[5,6],[5,8],[2,5]]},"result":[2,6,1]}'
  python tools/chat_review_log.py --backfill      # 2026-09-25~26 대화분 일괄(한 번만)
"""
import io, os, sys, json, time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(BASE, "logs", "review_owner")
SCHEMA = 2


def _score(e):
    r = e.get("result")
    if not r or len(r) < 2:
        return None
    w = {int(r[0]), int(r[1])}
    w3 = set(int(x) for x in r[:3])
    pairs = [set(p) for p in (e.get("my_pairs") or [])]
    trios = [set(t) for t in (e.get("my_trios") or [])]
    cl = e.get("claude") or {}
    return {
        "winner_in_picks": int(r[0]) in set(e.get("picks") or []),
        "axis_in_top2": (e.get("axis") in w) if e.get("axis") is not None else None,
        "my_pair_hit": any(p == w for p in pairs),
        "my_trio_hit": any(t == w3 for t in trios) if trios else None,
        "claude_pair_hit": any(set(p) == w for p in (cl.get("pairs") or [])) if cl else None,
        "picks_cover_top2": w <= set(e.get("picks") or []),
    }


def write(e):
    e = dict(e)
    e.setdefault("schema", SCHEMA)
    e.setdefault("source", "chat")
    now = time.time()
    e.setdefault("t", now)
    e.setdefault("at", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)))
    e.setdefault("file", "%s_%s.json" % (e["date"], e["rk"].replace(" ", "_")))
    e.setdefault("sec_to_post", None)
    e.setdefault("result_visible", e.get("phase") == "post")
    e["score"] = _score(e)
    os.makedirs(DIR, exist_ok=True)
    p = os.path.join(DIR, e["date"].replace("_", "") + ".jsonl")
    with io.open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return p, e["score"]


BACKFILL = [
    # ── 2026-09-25 ── (우라와 2R·소노다 2R 은 대표가 8013 에 직접 pre 로 넣었다 → 중복 안 넣음)
    {"date": "2026_09_25", "rk": "우라와 5경주", "phase": "pre", "source": "chat·마권캡처(발주 전 구매)",
     "axis": 7, "picks": [1, 3, 6, 7, 8, 12],
     "my_pairs": [[6, 7], [1, 7], [1, 6], [1, 3], [3, 6], [7, 8], [3, 12], [7, 12]],
     "tickets": {"6+7": 3, "1+7": 3, "7+8": 2, "7+12": 1, "1+6": 1, "1+3": 1, "3+6": 1, "3+12": 1},
     "note": "7 머리 · 6 8 12 — 6+8 짝 누락(중복 6장) · 3번(1.6배) 뺌 → 3착",
     "claude": {"axis": 3, "pairs": [[1, 3], [3, 7], [1, 7]]}, "result": [8, 6, 3]},
    {"date": "2026_09_25", "rk": "소노다 5경주", "phase": "post", "axis": None, "picks": [],
     "note": "결과 뒤 발언: 「6번이 죽을 거라 생각했다」(1.3배 인기마 붕괴 예상 → 6번 1착) · 터짐 판단 틀림",
     "burst_level": 3, "claude": {"axis": 6, "pairs": [[1, 6], [2, 6], [6, 7], [1, 2]]}, "result": [6, 1, 7]},
    {"date": "2026_09_25", "rk": "우라와 8경주", "phase": "post", "axis": 2, "picks": [2, 7],
     "my_pairs": [[2, 7]], "note": "결과 뒤 발언: 「2번을 축으로 봐서 적중」 — 냉대 2번 축(거리 경험)",
     "claude": {"axis": 1, "pairs": [[1, 6], [6, 7], [1, 7], [1, 2], [2, 6]]}, "result": [7, 2, 6]},
    # ── 2026-09-26 ──
    {"date": "2026_09_26", "rk": "오비히로 8경주", "phase": "post", "axis": 10, "picks": [10],
     "note": "결과 뒤 발언: 「나도 10번을 봤다」", "claude": {"axis": 10, "pairs": [[2, 10], [5, 10], [1, 10]]},
     "result": [10, 2, 9]},
    {"date": "2026_09_26", "rk": "오비히로 9경주", "phase": "pre", "axis": 5, "picks": [5, 6, 8, 10],
     "my_pairs": [[5, 6], [5, 8], [5, 10], [6, 10]], "note": "5 축 · 6+10 후보끼리 짝 보험",
     "claude": {"axis": 5, "pairs": [[5, 6], [5, 8], [2, 5]]}, "result": [2, 6, 1]},
    {"date": "2026_09_26", "rk": "오비히로 10경주", "phase": "pre", "axis": 7, "picks": [1, 2, 7, 10],
     "my_pairs": [[7, 10], [1, 7]], "my_trios": [[1, 2, 7], [1, 7, 10]], "note": "7 축 · 9번 안 봄",
     "claude": {"axis": 7, "pairs": [[7, 10], [2, 7], [2, 10]]}, "result": [10, 7, 9]},
]


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--backfill":
        for e in BACKFILL:
            p, s = write(e)
            print(e["rk"], e["phase"], s)
    elif len(sys.argv) > 1:
        p, s = write(json.loads(sys.argv[1]))
        print(p, s)
    else:
        print(__doc__)
