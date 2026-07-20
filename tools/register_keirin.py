# -*- coding: utf-8 -*-
"""
[경륜(케이린) 결과 일괄 등록·학습 반영]  — 홈서버(서버 실행 중)에서 실행.
register_results.py 의 경륜 버전. 경정/오토레이스 결과도 같은 형식으로 등록 가능.

용도: 경륜 결과 표(구분/경주지역/라운드/1착/2착/3착/…/복승/…/삼복승)를 로컬 서버의
      /api/history/record-result 로 경주별 POST → 착순 저장 + 적중판정 + 복병/패턴 학습 자동 반영.

사용법:
    cd C:\\Users\\USER\\Desktop\\경마분석서버
    py tools\\register_keirin.py                    # 아래 KEIRIN_TABLE(직접 붙여넣은 표) 등록
    py tools\\register_keirin.py keirin_results.txt  # 파일에서 표 읽어 등록(권장·재사용)

표 형식(탭 또는 다중 공백 구분, 헤더행 자동 스킵) — 경정/경마와 동일:
    구분  경주지역  라운드  1착  2착  3착  단승  복승  쌍승  연승  복연승  삼복승  삼쌍승
  · 경주지역 = 벨로드롬(경륜장)명: 세이부엔·야히코(弥彦)·마에바시·우쓰노미야·이와키평(いわき平)·
    기후·오다와라·마쓰도·다치카와·마쓰자카(松阪) 등. 마번은 1~9(경륜 최대 9명).
안전장치: 경주별 try/except(한 경주 실패해도 나머지 계속) · 타임아웃 · 요약 · 서버 미기동 감지.
"""
import json
import os
import sys
import urllib.request
import urllib.error

SERVER = os.environ.get("RACING_SERVER", "http://127.0.0.1:8011")
ENDPOINT = SERVER + "/api/history/record-result"

# 오늘 경륜 결과를 여기에 붙여넣으세요(헤더행 포함, 탭/공백 구분). 비워두면 파일 인자를 사용.
#   예:  일본경륜	세이부엔	1	3	5	7	2.1	6.1	...	30.9	100
KEIRIN_TABLE = """구분	경주지역	라운드	1착	2착	3착	단승	복승	쌍승	연승	복연승	삼복승	삼쌍승
"""


def _num(s):
    """숫자 파싱. '100'(표의 상한 sentinel)·빈칸·비숫자는 None(가짜 payout 방지)."""
    try:
        v = float(str(s).strip())
    except (TypeError, ValueError):
        return None
    if v <= 0 or v >= 100:
        return None
    return v


def parse_table(text):
    """표 텍스트 → [{region, rno, top3[list], quinella, trifecta, exacta, win}]."""
    rows = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        cols = [c.strip() for c in (ln.split("\t") if "\t" in ln else ln.split())]
        if len(cols) < 6:
            continue
        try:
            r1, r2, r3 = int(cols[3]), int(cols[4]), int(cols[5])
        except (ValueError, IndexError):
            continue                          # 헤더행/비정상행 스킵
        rows.append({
            "region": cols[1], "rno": cols[2], "top3": [r1, r2, r3],
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
        text = KEIRIN_TABLE
    rows = parse_table(text)
    if not rows:
        print("=" * 60)
        print("⚠ 등록할 경륜 결과가 없습니다.")
        print("  방법1) 이 스크립트의 KEIRIN_TABLE 에 오늘 경륜 결과표를 붙여넣고 다시 실행")
        print("  방법2) keirin_results.txt 파일에 표를 저장하고: py tools\\register_keirin.py keirin_results.txt")
        print("  표 형식: 구분  경주지역  라운드  1착  2착  3착  단승  복승  쌍승  연승  복연승  삼복승  삼쌍승")
        print("=" * 60)
        return
    print("=" * 60)
    print("경륜 결과 등록 → %s  (경주 %d개)" % (ENDPOINT, len(rows)))
    print("=" * 60)
    ok = hit = 0
    fails = []
    for r in rows:
        rk = "%s %s경주" % (r["region"], r["rno"])
        try:
            res = post(rk, r)
            rec = res.get("record") or {}
            was = rec.get("was_hit")
            q, t = rec.get("quinella_hit"), rec.get("trifecta_hit")
            matched = res.get("matchedFrom")
            tag = ("🎯적중" if was else "  ") + (" 복승O" if q else "") + (" 삼복O" if t else "")
            print("  ✅ %-14s %s  %s%s" % (rk, "-".join(map(str, r["top3"])), tag,
                                          (" (매칭:%s)" % matched) if matched else ""))
            ok += 1
            if was:
                hit += 1
        except urllib.error.URLError as e:
            print("  ❌ %-14s 서버 연결 실패: %s" % (rk, e))
            fails.append(rk)
            if "Connection refused" in str(e):
                print("\n⚠ 서버가 안 떠 있습니다. 먼저 'py app.py'(port 8011) 실행 후 재시도하세요.")
                break
        except Exception as e:
            print("  ❌ %-14s 등록 실패: %s" % (rk, e))
            fails.append(rk)
    print("=" * 60)
    print("완료: 등록 %d/%d · 적중 %d경주%s" %
          (ok, len(rows), hit, (" · 실패 %d(%s)" % (len(fails), ", ".join(fails)) if fails else "")))
    print("→ 결과기록 탭 새로고침 시 목록 제거·통계·복병 학습에 즉시 반영됩니다.")
    print("=" * 60)


if __name__ == "__main__":
    main()
