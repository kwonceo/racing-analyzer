# -*- coding: utf-8 -*-
"""[날짜별 작업 기록 뼈대 생성 (2026-07-31 신설)]

■ 무엇인가
  `docs/daily/<날짜>.md` 의 **자동 수집 가능한 부분만** 채운다.
  · 그날 커밋 목록(해시·시각·메시지)
  · 체크리스트 N/22
  · 그날 실적(경주 수·적중·손익)
  · 주요 지표(freeze_log · 예측 저장 · 아메다스 관측)

■ 🔴 서술 부분은 **비워 둔다**
  「한 줄」·「이런 일이 있었다」·「이래서 이렇게 했다」·「틀렸던 것」·「결정」은
  **판단이라 자동 수집이 안 된다.** 대화에서 정리해 채운다.
  ⚠ 특히 **「틀렸던 것」을 비워두지 말 것** — 추정이 빗나간 기록이 가장 값어치 있다.
    성공만 적으면 왜 그 결론에 도달했는지 알 수 없다.

■ ⚠ 기존 파일이 있으면 **덮어쓰지 않는다** — 서술이 날아간다.
  `--refresh` 를 주면 자동 수집 구간(`<!-- AUTO:… -->` 사이)만 갱신한다.

사용:
  python tools/build_daily_log.py                 # 오늘
  python tools/build_daily_log.py --date 2026-07-30
  python tools/build_daily_log.py --refresh        # 자동 구간만 갱신
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(BASE, "docs", "daily")
A_BEG, A_END = "<!-- AUTO:BEGIN -->", "<!-- AUTO:END -->"

TEMPLATE = """# %(date)s

## 한 줄
> _(그날을 한 문장으로 — 비워두지 말 것)_

## 이런 일이 있었다
<!-- 시각 · 사건, 시간순 -->
- `00:00` _(사건)_

## 이래서 이렇게 했다
<!-- 문제 → 원인 → 조치 → 결과 -->
### _(제목)_
- **문제**:
- **원인**:
- **조치**:
- **결과**:

## 틀렸던 것
<!-- 🔴 반드시 채울 것. 추정이 빗나간 것과 정정 내용.
     성공만 적으면 왜 그 결론에 도달했는지 알 수 없다. -->
| 추정 | 실제 | 정정 |
|---|---|---|
|  |  |  |

## 결정
<!-- 그날 정한 것 -->
-

## 숫자
%(AUTO)s
"""


def _sh(args):
    try:
        return subprocess.run(args, cwd=BASE, capture_output=True,
                              encoding="utf-8", errors="replace").stdout.strip()
    except Exception:
        return ""


def collect_commits(date):
    out = _sh(["git", "log", "--since=%s 00:00" % date, "--until=%s 23:59" % date,
               "--pretty=format:%h|%ad|%s", "--date=format:%H:%M"])
    rows = []
    for ln in (out or "").splitlines():
        parts = ln.split("|", 2)
        if len(parts) == 3:
            rows.append(tuple(parts))
    return rows


def collect_races(date):
    """그날 실적 — ⚠ 분모를 반드시 함께 낸다."""
    pat = os.path.join(BASE, "data", "analysis_log", date.replace("-", "_") + "_*.json")
    files = glob.glob(pat)
    n = len(files)
    res = hit = ro = frozen = 0
    profit = 0.0
    has_profit = 0
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if d.get("result"):
            res += 1
        h = d.get("hit") or {}
        if h.get("quinella_hit") or h.get("main_hit"):
            hit += 1
        if d.get("readonly"):
            ro += 1
        if d.get("frozen"):
            frozen += 1
        p = d.get("profit") or {}
        v = p.get("net") if isinstance(p, dict) else None
        if isinstance(v, (int, float)):
            profit += v
            has_profit += 1
    return {"races": n, "resulted": res, "hits": hit, "readonly": ro,
            "frozen": frozen, "profit": profit, "profit_n": has_profit}


def collect_indicators(date):
    def _len(p):
        try:
            return len(json.load(open(p, encoding="utf-8")) or [])
        except Exception:
            return 0
    fz = os.path.join(BASE, "data", "freeze_log", date + ".json")
    am = os.path.join(BASE, "data", "amedas_obs", date + ".json")
    fc = glob.glob(os.path.join(BASE, "logs", "forecast",
                                date.replace("-", "") + "_*.json"))
    rows = []
    if os.path.exists(fz):
        rows = json.load(open(fz, encoding="utf-8")) or []
    okr = [r for r in rows if isinstance(r, dict) and r.get("ok")]
    cr = sum(1 for r in okr if r.get("src") == "closed_row")
    pr = sum(1 for r in okr if r.get("src") == "pre_close_row")
    graded = reviewed = 0
    for f in fc:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if d.get("graded"):
            graded += 1
        if d.get("reviewed"):
            reviewed += 1
    return {"freeze_total": len(rows), "freeze_closed": cr, "freeze_pre": pr,
            "freeze_fail": len(rows) - len(okr),
            "amedas": _len(am) if os.path.exists(am) else 0,
            "forecast": len(fc), "graded": graded, "reviewed": reviewed}


def collect_checklist():
    try:
        sys.path.insert(0, os.path.join(BASE, "tools"))
        import health_check
        r = health_check.build_checklist()
        return "%d/%d" % (r["done"], r["total"]), r["summary"]
    except Exception as e:
        return "?", "체크리스트 산출 실패: %s" % str(e)[:80]


def build_auto(date):
    c = collect_commits(date)
    r = collect_races(date)
    i = collect_indicators(date)
    nn, summ = collect_checklist()
    L = [A_BEG, "<!-- 이 구간은 tools/build_daily_log.py 가 생성한다. 손으로 고치지 말 것. -->", ""]
    L.append("### 체크리스트")
    L.append("- **%s** — %s" % (nn, summ))
    L.append("")
    L.append("### 그날 실적  ⚠ 분모 = 그날 분석 로그 %d건" % r["races"])
    if r["races"]:
        L.append("| 항목 | 값 |")
        L.append("|---|---|")
        L.append("| 경주 수 | %d |" % r["races"])
        L.append("| 결과 입력 | %d / %d (%.1f%%) |"
                 % (r["resulted"], r["races"], 100.0 * r["resulted"] / r["races"]))
        L.append("| 적중 | %d / %d (%.1f%%) |"
                 % (r["hits"], r["resulted"] or 1, 100.0 * r["hits"] / (r["resulted"] or 1)))
        L.append("| readonly | %d | " % r["readonly"])
        L.append("| frozen 블록 보유 | %d |" % r["frozen"])
        if r["profit_n"]:
            L.append("| 손익 | %s원 (분모 %d건) |" % ("{:,}".format(int(r["profit"])), r["profit_n"]))
        else:
            L.append("| 손익 | 미집계 |")
    else:
        L.append("_경주 없음(또는 개최 전)_")
    L.append("")
    L.append("### 주요 지표")
    L.append("| 지표 | 값 |")
    L.append("|---|---|")
    fzt = i["freeze_total"]
    L.append("| 동결 시도 | %d건 |" % fzt)
    if fzt:
        L.append("| ├ closed_row | %d (%.1f%%) |" % (i["freeze_closed"], 100.0 * i["freeze_closed"] / fzt))
        L.append("| ├ pre_close_row | %d (%.1f%%) |" % (i["freeze_pre"], 100.0 * i["freeze_pre"] / fzt))
        L.append("| └ 실패 | %d (%.1f%%) |" % (i["freeze_fail"], 100.0 * i["freeze_fail"] / fzt))
    L.append("| 아메다스 관측 | %d건 |" % i["amedas"])
    L.append("| 예측 저장 | %d건 |" % i["forecast"])
    L.append("| ├ 채점 완료 | %d |" % i["graded"])
    L.append("| └ 복기 완료 | %d |" % i["reviewed"])
    L.append("")
    L.append("### 그날 커밋 (%d건)" % len(c))
    if c:
        for h, t, m in c:
            L.append("- `%s` %s — %s" % (h, t, m.split("\n")[0][:95]))
    else:
        L.append("_없음_")
    L.append("")
    L.append("_생성 %s_" % time.strftime("%Y-%m-%d %H:%M:%S"))
    L.append(A_END)
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=time.strftime("%Y-%m-%d"))
    ap.add_argument("--refresh", action="store_true",
                    help="기존 파일의 자동 수집 구간만 갱신(서술 보존)")
    a = ap.parse_args()
    os.makedirs(OUTDIR, exist_ok=True)
    path = os.path.join(OUTDIR, a.date + ".md")
    auto = build_auto(a.date)

    if os.path.exists(path):
        cur = open(path, encoding="utf-8").read()
        if not a.refresh:
            print("이미 있음: %s" % path)
            print("⚠ 덮어쓰지 않는다(서술이 날아간다). 자동 구간만 갱신하려면 --refresh")
            return 0
        if A_BEG in cur and A_END in cur:
            head = cur[:cur.index(A_BEG)]
            tail = cur[cur.index(A_END) + len(A_END):]
            new = head + auto + tail
        else:
            new = cur.rstrip() + "\n\n" + auto + "\n"
        open(path, "w", encoding="utf-8").write(new)
        print("자동 구간 갱신: %s (서술 보존)" % path)
        return 0

    open(path, "w", encoding="utf-8").write(TEMPLATE % {"date": a.date, "AUTO": auto})
    print("생성: %s" % path)
    print("🔴 서술 5개 항목은 비어 있다 — 특히 「틀렸던 것」을 반드시 채울 것")
    return 0


if __name__ == "__main__":
    sys.exit(main())
