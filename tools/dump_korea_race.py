# -*- coding: utf-8 -*-
"""한국 PDF → 경주별 fitz 텍스트 파일. 🔴 숫자를 하나도 바꾸지 않는다(추출만).

왜: PDF 가 17.9MB 라 채팅에 올릴 수 없다. 경주 단위로 자르면 22~32KB 라 다룰 수 있다.
구조(실측): 경주마다 **요약 페이지 + 전적표 페이지** 두 장이 붙어 있다.
  p5 부산1R(요약) → p6 전적표 · p7 제주1R(요약) → p8 전적표 …
⚠ 파싱이 아니다 — `get_text()` 결과를 **그대로** 쓴다. 정규식으로 값을 고치지 않는다.

사용
  python tools/dump_korea_race.py --list
  python tools/dump_korea_race.py --race 부산:1 --out 원문_부산1R.txt
  python tools/dump_korea_race.py --all --out 원문_전경주.txt
"""
import os
import re
import io
import sys
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF = os.path.join(ROOT, "data", "korea_last.pdf")
_HD = re.compile(r"(부산|제주|서울)경마\s*(\d+)경주")


def index_pages():
    """[(경기장, 경주번호, 요약페이지, 전적표페이지들)] — 헤더가 있는 페이지가 요약,
    다음 헤더 전까지가 그 경주 것이다."""
    import fitz
    d = fitz.open(PDF)
    marks = []
    for i in range(d.page_count):
        m = _HD.search(d[i].get_text())
        if m:
            marks.append((i, m.group(1), int(m.group(2))))
    out = []
    for k, (pg, ven, no) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else d.page_count
        out.append((ven, no, pg, list(range(pg + 1, end))))
    d.close()
    return out


def dump(sel, out_path):
    import fitz
    d = fitz.open(PDF)
    buf = []
    for ven, no, pg, rest in sel:
        buf.append("=" * 72)
        buf.append("%s %s경주   (PDF p%d 요약 + p%s 전적표)" % (ven, no, pg + 1,
                                                          ",".join(str(x + 1) for x in rest) or "-"))
        buf.append("=" * 72)
        for p in [pg] + rest:
            buf.append("---- PDF p%d ----" % (p + 1))
            buf.append(d[p].get_text())          # 🔴 원문 그대로
    d.close()
    txt = "\n".join(buf)
    io.open(out_path, "w", encoding="utf-8").write(txt)
    print("[덤프] %d경주 → %s (%.1f KB)" % (len(sel), out_path, len(txt.encode("utf-8")) / 1024))
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--race", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default="원문_한국경주.txt")
    a = ap.parse_args()

    idx = index_pages()
    if a.list or not (a.race or a.all):
        import fitz
        d = fitz.open(PDF)
        print("경주 %d개" % len(idx))
        for ven, no, pg, rest in idx:
            sz = sum(len(d[p].get_text().encode("utf-8")) for p in [pg] + rest) / 1024
            print("  %-3s%2d경주  p%-2d + %-8s  %5.1f KB" % (ven, no, pg + 1,
                                                          ",".join(str(x + 1) for x in rest), sz))
        d.close()
        return 0

    if a.all:
        return 0 if dump(idx, a.out) else 1
    ven, _, no = a.race.partition(":")
    sel = [x for x in idx if x[0] == ven.strip() and x[1] == int(no)]
    if not sel:
        print("그 경주를 못 찾았다:", a.race)
        return 1
    dump(sel, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
