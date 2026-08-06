# -*- coding: utf-8 -*-
"""[한국 PDF fitz 메타 파서 · 2026-08-06] 발주시각·경주목록을 **결정적**으로 뽑는다.

■ 왜 fitz 인가 (🔴 실측 근거)
  한국 PDF 본문은 Claude Vision 으로 판독한다 — 그런데 **재현성이 없다**.
  8/7 실측: Vision(prerace) 15경주(부산1-8·서울4·제주2·4·5·6·7·8) ↔ fitz 16경주(부산1-8·제주1-8).
    · Vision 이 **제주 1·3 을 통째로 누락**했다.
    · 마명 「서울드래」를 **유령 경주 「서울 4」로 오판**했다(서울은 8/7 미개최).
  ⇒ Vision 만 믿으면 명단·게이트가 흔들린다. **경주 뼈대(경마장·경주번호·발주시각)는 fitz 로 고정**한다.
  fitz get_text 는 PDF 내장 텍스트라 **몇 번 돌려도 같다**(결정적).

■ 원문 형태 (원칙: 추측 금지 · 원문 실측)
  경주 헤더 : '8월 7일(금요일) 부산경마1경주  1200M  국6(...)  별정A  일반경주(12:50)'
  발주시각   : 일반경주(HH:MM)  — 콜론 있는 괄호
  ⚠ 서울/부산경남 등 표기 흔들림은 _track 로 정규화.

사용: python tools/parse_korea_meta.py data/korea_last.pdf
"""
import re
import sys

_POST_HEAD = re.compile(r"(부산경남|부산|서울|제주|경남)경마\s*(\d{1,2})경주")
_POST_TIME = re.compile(r"일반경주\((\d{1,2}:\d{2})\)")


def _track(s):
    return {"부산경남": "부산", "경남": "부산"}.get(s, s)


def parse_post_times(pdf_path):
    """→ {(경마장, 경주번호): 'HH:MM'}. 경주 페이지 헤더에서 결정적으로 뽑는다."""
    import fitz
    out = {}
    doc = fitz.open(pdf_path)
    try:
        for pi in range(doc.page_count):
            t = doc[pi].get_text()
            tm = _POST_TIME.search(t)
            if not tm:
                continue
            head = t[:tm.start()].replace("\n", " ")   # 시각 앞쪽 헤더에서 경마장·경주번호
            pm = _POST_HEAD.search(head)
            if not pm:
                continue
            key = (_track(pm.group(1)), int(pm.group(2)))
            out.setdefault(key, tm.group(1))            # 경주 페이지 헤더 우선(첫 등장)
    finally:
        doc.close()
    return out


def race_list(pdf_path):
    """fitz 로 고정한 경주 목록(경마장·경주번호·발주시각). Vision 결과 검증의 기준선."""
    pt = parse_post_times(pdf_path)
    return sorted(({"track": k[0], "no": k[1], "post": v} for k, v in pt.items()),
                  key=lambda r: (r["track"], r["no"]))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "data/korea_last.pdf"
    rl = race_list(path)
    print("fitz 경주 %d개 (결정적):" % len(rl))
    for r in rl:
        print("  %s %d경주 → %s" % (r["track"], r["no"], r["post"]))
    import collections
    c = collections.Counter(r["track"] for r in rl)
    print("경마장별:", dict(c))
    return 0


if __name__ == "__main__":
    sys.exit(main())
