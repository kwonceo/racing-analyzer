# -*- coding: utf-8 -*-
"""_raw 응답 원문에서 대표용 텍스트를 만든다. 🔴 재생성 없음(비용 0)."""
import io, json, glob, os

def blk(b, head, why=None):
    L = ["-" * 74, head, "-" * 74]
    if why:
        L += ["🔴 폐기 사유: %s" % " / ".join(why), ""]
    sm = b.get("summary") or {}
    if sm:
        L.append("【요약】")
        for k, lab in (("race", "경주"), ("axis", "축"), ("rival", "상대"),
                       ("dark", "복병"), ("combo", "복승"), ("caution", "주의")):
            v = str(sm.get(k) or "").strip()
            if v:
                L.append("  %s : %s" % (lab, v))
        L.append("")
    L.append("【상세】")
    for k, lab in (("raceCharacter", "[1] 경주 성격"), ("structure", "[2] 구조"),
                   ("axisRanks", "[3] 축별 순위"), ("horses", "[4] 말별"),
                   ("excluded", "[5] 뺀 말"), ("combos", "[6] 조합"), ("cautions", "[7] 주의점")):
        v = b.get(k)
        if not v:
            continue
        L.append(lab)
        if isinstance(v, str):
            L.append("  " + v)
        else:
            for it in v:
                L.append("  " + (" / ".join("%s: %s" % (a, c) for a, c in it.items() if c)
                                 if isinstance(it, dict) else str(it)))
    L.append("")
    return L

# 폐기 사유 — _discard 에서 읽는다
why = {}
for f in glob.glob(os.path.join("logs", "form_brief", "_discard", "20260809_*.json")):
    try:
        d = json.load(io.open(f, encoding="utf-8"))
        why[os.path.basename(f).replace(".json", "")] = (d.get("meta") or {}).get("problems") or []
    except Exception:
        pass

out = ["전적 분석문 — 2026-08-09  (통과분 + 폐기분)",
       "※ 전적 원문만 읽고 만든 분석입니다. 시스템 추천과 다를 수 있습니다.",
       "※ 폐기분은 회원에게 나가지 않은 것입니다. 검증이 과한지 판단용입니다.",
       "※ 회원 발송은 복승만입니다. 삼복승은 [6] 조합에 참고로만 둡니다.",
       "=" * 74, ""]
for f in sorted(glob.glob(os.path.join("logs", "form_brief", "_raw", "20260809_*.txt"))):
    name = os.path.basename(f).replace(".txt", "")
    try:
        b = json.loads(io.open(f, encoding="utf-8").read())
    except Exception as e:
        out += ["■ %s — 원문 파싱 실패(%s)" % (name, str(e)[:40]), ""]
        continue
    w = why.get(name)
    out += blk(b, "■ %s%s" % (name, "  (폐기)" if w else "  (통과)"), why=w)
t = "\n".join(out)
io.open("분석문_20260809.txt", "w", encoding="utf-8").write(t)
print("→ 분석문_20260809.txt (%d자 · %d경주)" % (len(t), len(glob.glob(os.path.join('logs','form_brief','_raw','20260809_*.txt')))))
