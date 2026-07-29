# -*- coding: utf-8 -*-
"""[소급 정정] 경륜 전용 경마장이 `sport=horse` 로 저장된 분석 로그의 **sport/category 필드만** 정정.

배경(2026-07-29 실측): 기후 32 · 마쓰도 24 · 기시와다 24 · 사세보 30건이 `sport=horse·category=japan_local`
  로 저장돼 종목별 통계(페이스 보유율 등)를 오염시켰다. 전부 **7/22 이전**이며, `_KEIRIN_ONLY_RE` 지명
  보강으로 이후 발생은 없다 → 남은 것은 소급 정정뿐이다.

⚠ 안전 원칙
  · **sport·category 두 필드만** 바꾼다. 추천·결과·배당·학습 등 다른 필드는 일절 손대지 않는다.
  · 정정 이력을 `sport_fixed` 에 남긴다(무엇을 무엇으로 바꿨는지 추적 가능).
  · `--dry` 가 기본. 실제 쓰기는 `--apply` 를 줘야 한다.
  · 원자적 저장(tmp→replace)으로 중간 손상 방지.

사용:
  python tools/fix_keirin_sport_tag.py            # 대상만 조회(변경 없음)
  python tools/fix_keirin_sport_tag.py --apply    # 실제 정정
"""
import json, glob, os, re, argparse, collections, tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(BASE, "data", "analysis_log")
APP = os.path.join(BASE, "app.py")


def keirin_only_re():
    """app.py 의 `_KEIRIN_ONLY_RE` 원본을 그대로 재사용 — 지명 목록을 두 곳에 두지 않는다."""
    src = open(APP, encoding="utf-8").read()
    i = src.find("_KEIRIN_ONLY_RE = re.compile(")
    if i < 0:
        return None
    depth, j = 0, src.find("(", i)
    for k in range(j, min(len(src), j + 4000)):
        if src[k] == "(":
            depth += 1
        elif src[k] == ")":
            depth -= 1
            if depth == 0:
                body = src[j + 1:k]
                break
    else:
        return None
    parts = re.findall(r'r?"((?:[^"\\]|\\.)*)"', body)
    pat = "".join(parts)
    return re.compile(pat) if pat else None


def _atomic_write(path, obj):
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제로 파일을 정정(미지정 시 조회만)")
    a = ap.parse_args()

    rx = keirin_only_re()
    if not rx:
        print("!! _KEIRIN_ONLY_RE 추출 실패 — 정정 중단(오탐 방지)")
        return
    print("경륜 전용 지명 패턴 로드 OK (%d자)" % len(rx.pattern))

    targets, byv, byd = [], collections.Counter(), collections.Counter()
    for p in sorted(glob.glob(os.path.join(LOG, "*.json"))):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        sport = (d.get("sport") or "").lower()
        if sport in ("cycle", "boat", "bike"):
            continue
        fn = os.path.basename(p)
        venue = fn.split("_")[3] if len(fn.split("_")) > 3 else ""
        rk = str(d.get("raceKey") or "")
        if not (rx.search(venue) or rx.search(rk)):
            continue
        targets.append((p, fn, venue, sport, d.get("category")))
        byv[venue] += 1
        byd[fn[:10]] += 1

    print("\n=== 정정 대상 %d건 ===" % len(targets))
    for v, c in byv.most_common():
        print("  %-10s %3d건" % (v, c))
    if byd:
        print("  날짜 범위: %s ~ %s" % (min(byd), max(byd)))

    if not targets:
        print("정정 대상 없음.")
        return
    if not a.apply:
        print("\n[조회 모드] 실제 정정하려면 --apply 를 주세요. (파일 무변경)")
        return

    done = fail = 0
    for p, fn, venue, sport, cat in targets:
        try:
            d = json.load(open(p, encoding="utf-8"))
            d["sport"] = "cycle"
            d["category"] = "cycle"
            d["sport_fixed"] = {"from_sport": sport, "from_category": cat,
                                "reason": "경륜 전용 지명(%s) — _KEIRIN_ONLY_RE 매칭" % venue,
                                "at": "2026-07-29"}
            _atomic_write(p, d)
            done += 1
        except Exception as e:
            print("  !! %s 실패: %s" % (fn, e))
            fail += 1
    print("\n정정 완료 %d건 · 실패 %d건 (sport/category 2개 필드만 변경 · sport_fixed 이력 기록)"
          % (done, fail))


if __name__ == "__main__":
    main()
