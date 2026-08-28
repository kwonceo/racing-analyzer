# -*- coding: utf-8 -*-
"""app.py 에 「시장순위 쌍 교체」를 배선한다 (--apply 없으면 아무것도 안 한다).

🔴 왜 이 안인가 (2026-08-28 측정 · 8월 경륜 · 확정배당 · 정제 0.5~2.0)
  ① 현행                 회수 71.4% · 대박3뺀 68.9% · 배당중앙 2.4배 · 경주당 2.55
  🟢③ 뒤쪽 2개 교체        회수 82.2% · 대박3뺀 79.2% · 배당중앙 4.1배 · 경주당 2.55
  ⑤ 전면 대체            회수 88.6% · 대박3뺀 84.8% · 배당중앙 9.7배 · 경주당 2.00
  ⇒ ③을 켠다. **구좌를 한 개도 늘리지 않는다**(대표 기준 「경주당 3개 이하」 유지).

  판정 4단계 (③ · 경륜)
    ① 적중 565건 ≥ 30                                                    🟢
    ② 대박3뺀 68.9 → 79.2%                                                🟢
    ③ 기간 3분할 회수/대박뺀  92.3/83.9 · 77.8/72.9 · 80.9/72.8
       (현행 76.1/68.2 · 69.9/66.7 · 70.5/63.7) → **세 구간 모두 둘 다 개선**   🟢
    ④ 🔴 경마는 제외한다 — 전면 대체 기준 대박3뺀 70.9% 로 판정선 미달이었다

  ⚠ 원칙 27 — 시장순위는 **그 시점 배당판**(`rec["quinella"]`)으로 낸다. 마감 후에는
    이 블록에 오지 않는다(afterClose 분기가 동결본을 그대로 쓴다).
  ⚠ 원칙 14 와 다르다 — 명단을 **넓히는 것이 아니라** 같은 개수에서 자리만 바꾼다.
  🔧 되돌리기: RANK_PAIR_SWAP_ENABLED = False 한 줄.
"""
import io, os, sys, ast

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app.py")

CONST = '''
# 🔴🔴 [2026-08-28 측정 · 승인] **시장순위 쌍 교체** — 구좌를 안 늘리고 자리만 바꾼다
#   근거·판정은 tools/patch_rank_pair_swap.py 상단과 CLAUDE.md 참조.
#   경륜 8월 실측: 회수 71.4 → 82.2% · 대박3뺀 68.9 → 79.2% · 배당중앙 2.4 → 4.1배
#   🔴 경마 제외(대박3뺀 판정선 미달) · 🔧 되돌리기 RANK_PAIR_SWAP_ENABLED = False
RANK_PAIR_SWAP_ENABLED = True
RANK_PAIR_SWAP_SPORTS = ("cycle",)
RANK_PAIR_SWAP_N = 2          # 뒤쪽 N개를 교체. 0이면 무동작


def _market_rank_from_quin(qmap):
    """말별 시장 내재확률 순위 — 그 말이 낀 조합의 1/배당 합.
    ⚠ 순위만 쓴다(값 자체는 정규화하지 않는다). 4두 미만이면 None."""
    w = {}
    for (a, b), o in (qmap or {}).items():
        try:
            o = float(o)
        except (TypeError, ValueError):
            continue
        if o <= 1.0:
            continue
        w[a] = w.get(a, 0.0) + 1.0 / o
        w[b] = w.get(b, 0.0) + 1.0 / o
    if len(w) < 4:
        return None
    return {no: i + 1 for i, (no, _) in enumerate(sorted(w.items(), key=lambda kv: -kv[1]))}


def _rank_pair_swap(quin, current, n):
    """시장 **1위 + 4~5위** 조합으로 명단 뒤쪽 n개를 교체한다.
    반환 (새 명단, 넣은 것, 뺀 것) · 조건 미달이면 None(종전 동작 유지)."""
    if n <= 0 or not current:
        return None
    qm = _as_qmap(quin)
    mr = _market_rank_from_quin(qm)
    if not mr:
        return None
    r1 = [no for no, r in mr.items() if r == 1]
    r45 = [no for no, r in mr.items() if 4 <= r <= 5]
    if not r1 or not r45:
        return None
    have = set(tuple(sorted(c)) for c in current)
    cand = []
    for a in r1:
        for b in r45:
            c = tuple(sorted((a, b)))
            if c in have or c not in qm:
                continue
            cand.append((qm[c], list(c)))
    if not cand:
        return None
    cand.sort()                                  # 같은 자리면 싼 쪽 먼저
    add = [c for _, c in cand[:n]]
    keep = current[:max(0, len(current) - len(add))]
    drop = current[max(0, len(current) - len(add)):]
    return (keep + add, add, drop)
'''

HOOK = '''            # 🔴🔴 [2026-08-28] **시장순위 쌍 교체** — 명단 뒤쪽 N개를 「시장 1위+4~5위」로 바꾼다.
            #   ⚠ 더하는 것이 아니라 **바꾸는 것**이라 구좌가 늘지 않는다(경주당 2.55 유지).
            #   ⚠ 위 판정 편입(_judge_extra_quinellas) **뒤**에 둔다 — 편입분까지 포함한
            #     최종 명단을 기준으로 뒤쪽을 바꿔야 실제 회원이 받는 것과 같아진다.
            #   ⚠ 실패해도 종전 명단을 그대로 쓴다(try/except · 조건 미달이면 None).
            try:
                if (RANK_PAIR_SWAP_ENABLED
                        and str(an.get("sport") or "") in RANK_PAIR_SWAP_SPORTS):
                    _gate_hit("rank_pair_swap", rk, "도달", reach_only=True)
                    _sw = _rank_pair_swap(rec.get("quinella"), _dc_out["quinellas"],
                                          RANK_PAIR_SWAP_N)
                    if _sw:
                        _dc_out["quinellas"] = _sw[0]
                        _dc_out["rankPairSwap"] = {
                            "added": _sw[1], "dropped": _sw[2],
                        }
                        _gate_hit("rank_pair_swap", rk,
                                  "교체 +%s / -%s"
                                  % (",".join("+".join(str(x) for x in c) for c in _sw[1]),
                                     ",".join("+".join(str(x) for x in c) for c in _sw[2])),
                                  once_key=rk)
            except Exception as _rpe:
                print("[시장순위 쌍 교체] 스킵(무시):", str(_rpe)[:80])
'''

ANCHOR_CONST = "def _judge_extra_quinellas(cp, sport, already):"
ANCHOR_HOOK = '                print("[판정 편입] 스킵(무시):", str(_jxe)[:80])'


def apply(dry=True):
    src = io.open(APP, encoding="utf-8").read()
    if "RANK_PAIR_SWAP_ENABLED" in src:
        print("  ⚠ 이미 배선돼 있다 — 아무것도 하지 않는다"); return 0
    if ANCHOR_CONST not in src or ANCHOR_HOOK not in src:
        print("  🔴 앵커를 못 찾았다 — 중단한다(app.py 가 바뀌었다)"); return 1
    out = src.replace(ANCHOR_CONST, CONST.lstrip("\n") + "\n\n" + ANCHOR_CONST, 1)
    out = out.replace(ANCHOR_HOOK, ANCHOR_HOOK + "\n" + HOOK.rstrip("\n"), 1)
    try:
        ast.parse(out)
    except SyntaxError as e:
        print("  🔴 문법 오류 — 적용하지 않는다:", e); return 1
    print("  🟢 문법 OK · 추가 %d줄 · 삭제 0줄" % (out.count("\n") - src.count("\n")))
    if dry:
        print("  ⚠ --dry (기본) — 파일을 쓰지 않았다. 적용하려면 --apply")
        return 0
    io.open(APP + ".bak_rankswap", "w", encoding="utf-8", newline="").write(src)
    io.open(APP, "w", encoding="utf-8", newline="").write(out)
    print("  🟢 적용 완료 · 원본 백업 app.py.bak_rankswap")
    return 0


if __name__ == "__main__":
    sys.exit(apply(dry="--apply" not in sys.argv))
