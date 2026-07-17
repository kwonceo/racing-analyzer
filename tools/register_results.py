# -*- coding: utf-8 -*-
"""
[일본경마 결과 일괄 등록·학습 반영]  — 홈서버(서버 실행 중)에서 실행.

용도: 컴파일한 결과 표(구분/경주지역/라운드/1착/2착/3착/…/복승/…/삼복승)를 로컬 서버의
      /api/history/record-result 로 경주별 POST → 착순 저장 + 적중판정 + 복병/패턴 학습 자동 반영.
      한국 경주 결과는 KRA 백필(POST /api/kra/results/backfill)이 담당하므로, 이 스크립트는 일본 경주용.

사용법:
    cd C:\\Users\\USER\\Desktop\\경마분석서버
    py tools\\register_results.py                 # 아래 RESULTS_TABLE(오늘 표) 등록
    py tools\\register_results.py jp_results.txt   # 파일에서 표 읽어 등록(다음날부터 재사용)

표 형식(탭 또는 다중 공백 구분, 헤더행은 자동 스킵):
    구분  경주지역  라운드  1착  2착  3착  단승  복승  쌍승  연승  복연승  삼복승  삼쌍승
안전장치: 경주별 try/except(한 경주 실패해도 나머지 계속) · 타임아웃 · 요약 출력 · 서버 미기동 감지.
"""
import json
import os
import sys
import urllib.request
import urllib.error

SERVER = os.environ.get("RACING_SERVER", "http://127.0.0.1:8011")
ENDPOINT = SERVER + "/api/history/record-result"

# 오늘(2026-07-17) 일본지방경마 결과 — 권대표 제공 표. 파일 인자 없으면 이걸 등록.
RESULTS_TABLE = """구분	경주지역	라운드	1착	2착	3착	단승	복승	쌍승	연승	복연승	삼복승	삼쌍승
일본지방경마	우라와	1	4	5	11	1.8	6.1	9.1	1.2/1.4/2.3	2.3/6.9/11.2	30.9	100
일본지방경마	우라와	2	8	2	12	34.5	100	100	5.0/3.1/1.5	27.8/16.8/10.2	100	100
일본지방경마	소노다	1	2	12	6	2.2	11.3	17.8	1.3/1.8/1.3	3.0/2.0/3.6	8.7	52.1
일본지방경마	우라와	3	6	5	1	2.1	12.4	17.4	1.4/2.1/7.8	5.5/18.2/24.2	100	100
일본지방경마	나고야	1	6	2	7	18.8	35.2	73.8	3.8/1.8/3.7	9.8/23.1/4.6	73.8	100
일본지방경마	소노다	2	3	1	5	2.7	5.8	8	1.3/1.6/6.2	3.3/8.7/14.3	23.7	63.9
일본지방경마	우라와	4	11	7	3	9.5	4.8	21.5	1.8/1.1/1.2	2.4/3.3/1.8	7.9	98.7
일본지방경마	나고야	2	9	11	4	1.2	2.6	2.8	1.0/1.3/6.1	1.5/9.4/21.1	24.6	53.8
일본지방경마	소노다	3	1	2	4	2	5.1	10.1	1.3/1.7	1.4/2.3/2.8	5.7	18.5
일본지방경마	우라와	5	12	2	8	8	28.1	72.6	2.9/2.4/15.1	12.6/80.2/82.6	100	100
일본지방경마	나고야	3	10	12	3	25.3	19.5	71.7	2.3/1.0/1.3	4.5/6.4/1.5	12.9	100
일본지방경마	소노다	4	7	6	5	1.6	12.7	18.5	1.2/2.1/9.6	3.8/16.5/68.2	100	100
일본지방경마	우라와	6	6	10	4	4.8	51.7	61.3	1.6/2.8/1.6	14.1/3.0/11.4	53.3	100
일본지방경마	나고야	4	4	6	1	5.4	12.3	24.4	2.2/2.8	3.7/1.6/2.1	6.3	57
일본지방경마	소노다	5	4	3	8	2.2	4	6.9	1.2/1.3/1.4	2.0/3.8/3.3	9.2	23.3
일본지방경마	우라와	7	7	8	5	1.1	2.7	3	1.0/1.0/1.5	1.4/2.2/5.4	6.3	13
일본지방경마	나고야	5	3	4	12	1.4	9.8	15.5	1.1/2.5/3.7	3.8/5.3/16.1	33	100
일본지방경마	소노다	6	9	6	10	1.1	4.2	4.4	1.0/1.2/1.1	1.9/1.3/3.1	4.4	12.2
일본지방경마	우라와	8	8	2	10	4.7	20.1	37.8	1.8/2.8/3.1	6.6/8.6/16.0	76.3	100
일본지방경마	나고야	6	12	5	9	1.6	4.5	4.7	1.0/1.6/5.2	2.7/10.7/34.1	57.1	100
일본지방경마	소노다	7	2	4	1	26.3	42.8	100	3.4/1.5/1.5	6.8/9.1/2.5	42.7	100
일본지방경마	우라와	9	7	4	6	15.7	55.6	100	2.6/2.4/1.1	14.0/5.2/4.6	34.2	100
일본지방경마	나고야	7	4	7	1	12.3	100	100	3.0/6.5/2.0	28.5/9.3/21.4	100	100
일본지방경마	소노다	8	3	2	6	17.1	100	100	2.9/3.0/1.7	21.1/4.7/5.4	58	100
일본지방경마	우라와	10	3	6	10	15.1	68.3	100	3.7/2.6/1.5	15.7/8.3/4.7	65.5	100
일본지방경마	나고야	8	9	7	12	1.9	43.2	67	1.4/5.7/3.0	10.8/3.8/44.8	87.2	100
일본지방경마	소노다	9	8	9	4	14.6	100	100	3.6/11.7/4.0	67.8/26.1/43.1	100	100
일본지방경마	우라와	11	6	10	5	4.6	46.5	66.8	2.1/7.0/6.6	15.0/14.7/45.3	100	100
일본지방경마	나고야	9	9	11	5	7.1	11.2	27.2	2.1/1.7/1.6	5.1/6.5/3.3	17.3	93.9
일본지방경마	소노다	10	1	7	5	2.2	13.6	26.1	1.3/1.9/3.0	3.7/7.1/19.7	58.3	100
일본지방경마	우라와	12	10	8	9	2.3	7.1	10.2	1.3/1.5/1.4	3.3/3.4/4.8	10	40.8
일본지방경마	나고야	10	10	2	9	2.9	6.2	10.9	1.2/1.3/2.3	3.3/4.8/6.9	22.5	76.4
일본지방경마	소노다	11	7	9	2	1.6	7.6	10.2	1.4/2.2/3.3	3.6/7.0/19.0	33.7	100
일본지방경마	나고야	11	4	5	3	1.9	5.2	7.8	1.2/2.3	1.9/1.4/2.4	4.1	18.5
일본지방경마	소노다	12	1	4	9	12	100	100	3.4/3.8/2.5	27.3/9.6/12.4	100	100
일본지방경마	나고야	12	11	2	12	7.2	100	100	1.8/6.0/1.4	34.2/5.0/20.1	100	100
"""


def _num(s):
    """숫자 파싱. '100'(표의 상한 sentinel)·빈칸·비숫자는 None(가짜 payout 방지)."""
    try:
        v = float(str(s).strip())
    except (TypeError, ValueError):
        return None
    if v <= 0 or v >= 100:      # 100 = 표의 '100배+/미발매' 상한 → 확정배당으로 쓰지 않음
        return None
    return v


def parse_table(text):
    """표 텍스트 → [{region, rno, top3[list], quinella, trifecta, exacta, win}] (일본만)."""
    rows = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        cols = [c.strip() for c in (ln.split("\t") if "\t" in ln else ln.split())]
        if len(cols) < 6:
            continue
        # 헤더행 스킵(1착 자리가 숫자가 아니면 헤더)
        try:
            r1, r2, r3 = int(cols[3]), int(cols[4]), int(cols[5])
        except (ValueError, IndexError):
            continue
        region, rno = cols[1], cols[2]
        rows.append({
            "region": region, "rno": rno, "top3": [r1, r2, r3],
            "win": _num(cols[6]) if len(cols) > 6 else None,
            "quinella": _num(cols[7]) if len(cols) > 7 else None,
            "exacta": _num(cols[8]) if len(cols) > 8 else None,
            "trifecta": _num(cols[11]) if len(cols) > 11 else None,
        })
    return rows


def post(rk, r):
    body = {
        "raceKey": rk,
        "result": {"1st": r["top3"][0], "2nd": r["top3"][1], "3rd": r["top3"][2]},
        "finalOdds": {k: r[k] for k in ("quinella", "trifecta", "exacta", "win") if r.get(k) is not None},
        "quinellaOdds": r.get("quinella"), "trifectaOdds": r.get("trifecta"),
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(ENDPOINT, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def main():
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        text = open(sys.argv[1], encoding="utf-8").read()
        print("표 파일 로드:", sys.argv[1])
    else:
        text = RESULTS_TABLE
    rows = parse_table(text)
    print("=" * 60)
    print("일본경마 결과 등록 → %s  (경주 %d개)" % (ENDPOINT, len(rows)))
    print("=" * 60)
    ok = hit = 0
    fails = []
    for r in rows:
        rk = "%s %s경주" % (r["region"], r["rno"])
        try:
            res = post(rk, r)
            rec = res.get("record") or {}
            was = rec.get("was_hit")
            q = rec.get("quinella_hit")
            t = rec.get("trifecta_hit")
            matched = res.get("matchedFrom")
            tag = ("🎯적중" if was else "  ") + (" 복승O" if q else "") + (" 삼복O" if t else "")
            print("  ✅ %-12s %s  %s%s" % (rk, "-".join(map(str, r["top3"])), tag,
                                          (" (매칭:%s)" % matched) if matched else ""))
            ok += 1
            if was:
                hit += 1
        except urllib.error.URLError as e:
            print("  ❌ %-12s 서버 연결 실패: %s" % (rk, e))
            fails.append(rk)
            if "Connection refused" in str(e):
                print("\n⚠ 서버가 안 떠 있습니다. 먼저 'py app.py'(port 8011) 실행 후 재시도하세요.")
                break
        except Exception as e:
            print("  ❌ %-12s 등록 실패: %s" % (rk, e))
            fails.append(rk)
    print("=" * 60)
    print("완료: 등록 %d/%d · 적중 %d경주%s" %
          (ok, len(rows), hit, (" · 실패 %d(%s)" % (len(fails), ", ".join(fails)) if fails else "")))
    print("→ 결과기록 탭 새로고침 시 목록에서 제거되고 통계·복병 학습에 즉시 반영됩니다.")
    print("=" * 60)


if __name__ == "__main__":
    main()
