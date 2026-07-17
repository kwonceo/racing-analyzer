# -*- coding: utf-8 -*-
"""
[경쟁 AI 추천 입력·비교 분석]  — 홈서버(서버 실행 중)에서 실행.

용도: 경쟁 AI의 추천 복승 조합을 경주별로 입력 → 서버가 축·연결마·패턴을 자동분석하고
      우리 추천/실제결과와 비교해 data/competitor_analysis/ 에 저장 + 누적통계 갱신.

사용법:
    cd C:\\Users\\USER\\Desktop\\경마분석서버
    py tools\\register_competitor.py                 # 아래 COMPETITOR_TABLE 입력
    py tools\\register_competitor.py comp.txt          # 파일에서 읽기(재사용)

입력 형식(한 줄에 한 경주, 왼쪽=경주명 | 오른쪽=저쪽 조합들):
    소노다 6경주 | 4+10 7+10 1+4
    소노다 11경주 | 3+6, 7+9, 3+7
  · 조합 구분: 공백 또는 쉼표.  조합 안 번호 구분: + 또는 - (예: 4+10, 4-10 둘 다 됨)
  · 경주명은 등록결과 때 쓰신 것과 같게(예: '소노다 6경주').
"""
import json
import os
import re
import sys
import urllib.request
import urllib.error

SERVER = os.environ.get("RACING_SERVER", "http://127.0.0.1:8011")
ENDPOINT = SERVER + "/api/competitor/record"

# 오늘 경쟁 AI 추천을 여기에 입력(경주명 | 조합들). 비워두면 파일 인자 사용.
COMPETITOR_TABLE = """
"""


def parse(text):
    rows = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or "|" not in ln:
            continue
        left, right = ln.split("|", 1)
        rk = left.strip()
        combos = []
        for tok in re.split(r"[,\s]+", right.strip()):
            nums = [int(x) for x in re.split(r"[+\-]", tok) if x.strip().isdigit()]
            if len(nums) >= 2:
                combos.append(nums)
        if rk and combos:
            rows.append((rk, combos))
    return rows


def post(rk, combos):
    data = json.dumps({"raceKey": rk, "picks": combos}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(ENDPOINT, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def main():
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        text = open(sys.argv[1], encoding="utf-8").read()
        print("파일 로드:", sys.argv[1])
    else:
        text = COMPETITOR_TABLE
    rows = parse(text)
    if not rows:
        print("=" * 60)
        print("⚠ 입력된 경쟁 추천이 없습니다.")
        print("  이 스크립트의 COMPETITOR_TABLE 에 아래처럼 넣고 다시 실행하세요:")
        print("    소노다 6경주 | 4+10 7+10 1+4")
        print("    소노다 11경주 | 3+6 7+9 3+7")
        print("  또는 comp.txt 파일로: py tools\\register_competitor.py comp.txt")
        print("=" * 60)
        return
    print("=" * 60)
    print("경쟁 AI 추천 입력·비교 → %s  (경주 %d개)" % (ENDPOINT, len(rows)))
    print("=" * 60)
    ok = 0
    for rk, combos in rows:
        try:
            r = post(rk, combos)
            rec = r.get("record") or {}
            an = rec.get("analysis") or {}
            comp = rec.get("competitor") or {}
            ch, oh = an.get("competitor_hit"), an.get("our_hit")

            def mark(v):
                return "🎯적중" if v is True else ("미적중" if v is False else "결과대기")
            print("  ✅ %-14s" % rk)
            print("      저쪽: %s (%s)" % (comp.get("picks"), comp.get("pattern")))
            print("      우리: %s (축 %s)" % (rec.get("ours", {}).get("picks"), rec.get("ours", {}).get("axis")))
            print("      저쪽 %s / 우리 %s · %s" % (mark(ch), mark(oh), an.get("diff")))
            ok += 1
        except urllib.error.URLError as e:
            print("  ❌ %-14s 서버 연결 실패: %s" % (rk, e))
            if "Connection refused" in str(e):
                print("\n⚠ 서버가 안 떠 있습니다. 먼저 'py app.py' 실행 후 재시도하세요.")
                break
        except Exception as e:
            print("  ❌ %-14s 실패: %s" % (rk, e))
    print("=" * 60)
    print("완료: %d경주 저장 · 누적통계는 GET /api/competitor/stats 또는 분석기 '📊 경쟁 분석' 확인" % ok)
    print("=" * 60)


if __name__ == "__main__":
    main()
