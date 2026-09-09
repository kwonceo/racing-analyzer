# -*- coding: utf-8 -*-
"""
전적표 예측 복기(復棋) 데이터화 — 2026-09-09 대표 「복기가 중요한데 이걸 데이터화 해야 해」

채점이 끝난 예측(logs/form_forecast · logs/keirin_forecast)마다 복기 레코드를 만든다.
  ① 기계 복기(모델 호출 없음): 축 결과 · 정답마가 어디 있었나(상대/제외/미언급) · 정답 조합의 시장 인기 · 전개 검증 재료
  ② 모델 복기(Claude): 무엇이 틀렸나 · 어느 읽기 규칙이 도왔나/해쳤나(태그) · 출마표에 단서가 있었나 · 교훈 1문장 · 규칙 수정 제안
  ③ 집계(--stats): 규칙 태그별 도움/해침 횟수 · 축 결과 분포 · 놓친 정답마 위치 분포 · 단서 보유율 — 30건마다 프롬프트를 고칠 근거
저장: 각 예측 JSON 의 "review" 필드 + logs/<kind>_forecast/review_<YYYYMMDD>.jsonl
🔴 별도 프로세스 · 추천 경로 무개입 · 결과표(keiba.go.jp RaceMarkTable · 경륜은 analysis_log.result.flow)로 통과순위·상3F 를 채운다

사용: python tools/forecast_review.py --run [--kind horse|keirin] [--model X]   # 미복기분 전부
      python tools/forecast_review.py --stats
"""
import os, sys, re, io, json, glob, time, html, datetime, argparse
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import form_forecast as FF

KINDS = {"horse": os.path.join(BASE, "logs", "form_forecast"), "keirin": os.path.join(BASE, "logs", "keirin_forecast")}

RULE_TAGS = ["①전개", "②등급이동", "③마장", "④거리", "⑤휴양복귀", "⑥인기과열", "⑥냉대", "⑦반복꾸준회복", "⑧대전표", "⑨감량체중",
             "직전선행입상보존", "조건실적우선", "냉대상대필수", "축붕괴보험", "경기장미입상제외", "라인", "자력형수", "番手", "決まり手", "등급기수", "직전개최", "연대율", "기어", "기타"]

SYSTEM = """당신은 경주 예측의 복기 담당이다. 예측(축·상대·조합·전개·근거·제외)과 실제 결과(착순·배당·통과순위·상3F·인기)를 대조해
「무엇이 틀렸고 왜 틀렸나」를 데이터로 남긴다. 변명하지 말고, 맞은 것도 운인지 근거가 맞은 것인지 가른다. 모든 문장은 한국어.

규칙 태그(아래 목록에서만 고른다): %s

반드시 아래 JSON 하나만 출력한다:
{"verdict": "적중|반적중|미적중",
 "axis_result": "1·2착|3착|착외",
 "missed": [{"no": 정답마 번호, "where": "상대|제외|미언급|축", "why_missed": "왜 못 넣었나 1문장", "clue_in_card": true/false, "clue": "출마표에 있던 단서 1문장(없으면 빈 문자열)"}],
 "pace_check": {"predicted": "예측한 전개 한 줄", "actual": "실제 통과순위로 본 전개 한 줄", "correct": true/false},
 "rule_tags": {"helped": ["태그"...], "hurt": ["태그"...]},
 "lesson": "다음에 다르게 볼 것 1문장",
 "rule_change": "프롬프트 규칙 수정 제안 1개(없으면 \\"없음\\")"}""" % " · ".join(RULE_TAGS)


class _Tbl(HTMLParser):
    def __init__(self):
        super().__init__(); self.rows = []; self._row = None; self._cell = None
    def handle_starttag(self, tag, attrs):
        if tag == "tr": self._row = []
        elif tag in ("td", "th") and self._row is not None: self._cell = []
    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip()); self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row: self.rows.append(self._row)
            self._row = None
    def handle_data(self, d):
        if self._cell is not None: self._cell.append(d)


def horse_result_rows(date_s, baba, rno):
    """keiba.go.jp 성적표 → [{place, no, name, time, margin, last3f, corners, pop, win}] (미확정이면 [])."""
    try:
        h = FF._get("%sRaceMarkTable?k_raceDate=%s&k_raceNo=%d&k_babaCode=%s" % (FF.KEIBA, date_s, rno, baba))
    except Exception as e:
        print("[결과표] 실패", e); return []
    p = _Tbl(); p.feed(html.unescape(h))
    out = []
    for r in p.rows:
        if len(r) < 8 or not re.fullmatch(r"\d{1,2}", r[0] or ""):
            continue
        try:
            corners = next((c for c in r if re.fullmatch(r"\d{1,2}(-\d{1,2}){1,3}", c)), "")
            last3f = next((c for c in r if re.fullmatch(r"\d{2}\.\d", c)), "")
            tm = next((c for c in r if re.fullmatch(r"\d:\d{2}\.\d", c)), "")
            pop = None; win = None
            for i in range(len(r) - 1, 0, -1):
                if re.fullmatch(r"\d+\.\d", r[i]) and re.fullmatch(r"\d{1,2}", r[i - 1]):
                    win = float(r[i]); pop = int(r[i - 1]); break
            out.append({"place": int(r[0]), "no": int(r[2]), "name": r[3], "time": tm, "last3f": last3f, "corners": corners, "pop": pop, "win": win})
        except Exception:
            continue
    return out


def _machine_review(rec, kind):
    p = rec.get("prediction") or {}; res = rec.get("result") or {}
    order = res.get("order") or []
    top2 = set(order[:2]); top3 = set(order[:3])
    axis = p.get("axis")
    partners = [int(x) for x in (p.get("partners") or [])]
    excluded = {int(k): v for k, v in (p.get("excluded") or {}).items() if str(k).isdigit()}
    reasons = {int(k): v for k, v in (p.get("reasons") or {}).items() if str(k).isdigit()}
    where = {}
    for n in order[:2]:
        if n == axis: where[n] = "축"
        elif n in partners: where[n] = "상대%d" % (partners.index(n) + 1)
        elif n in excluded: where[n] = "제외"
        elif n in reasons: where[n] = "근거만"
        else: where[n] = "미언급"
    mk = [m[0] for m in (rec.get("marketAtFetch") or [])]
    return {"axis_result": "1·2착" if axis in top2 else ("3착" if axis in top3 else "착외"),
            "answer_where": {str(k): v for k, v in where.items()},
            "answer_market_rank": [mk.index(n) + 1 if n in mk else None for n in order[:2]],
            "q_hit": (rec.get("grade") or {}).get("q_hit"), "trio_hit": (rec.get("grade") or {}).get("trio_hit")}


def _actual_text(rec, kind, rows):
    if kind == "horse":
        if not rows: return "(결과표 미확보)"
        return "\n".join("%d착 %d번 %s 타임 %s 상3F %s 통과 %s 인기 %s 단승 %s" % (
            r["place"], r["no"], r["name"], r["time"], r["last3f"], r["corners"], r["pop"], r["win"]) for r in rows)
    flow = rows or []
    if not flow: return "(결과 흐름 미확보)"
    return "\n".join("%s착 %s번 決まり手 %s 라스트랩 %s 착차 %s S/B %s" % (
        f.get("placing"), f.get("car"), f.get("kimarite") or "-", f.get("lastLap"), f.get("margin") or "-", f.get("sb") or "-") for f in flow)


def review_one(path, kind, model=None):
    rec = FF._load(path) or {}
    if not rec.get("grade") or not isinstance(rec.get("prediction"), dict):
        return None
    p = rec["prediction"]; res = rec.get("result") or {}
    if kind == "horse":
        rows = horse_result_rows(rec["date"], rec["baba"], rec["rno"])
        rec["resultRows"] = rows
    else:
        fn = "%s_%s" % (rec["date"].replace("-", "_"), rec["race"].replace(" ", "_"))
        doc = FF._load(os.path.join(BASE, "data", "analysis_log", fn + ".json")) or {}
        rows = ((doc.get("result") or {}).get("flow")) or []
        rec["resultRows"] = rows
    mr = _machine_review(rec, kind)
    user = "【경주】 %s\n【예측 시점 시장 순】 %s\n\n【예측】\n%s\n\n【결과】 착순 %s · 복승 %s배 · 삼복승 %s배\n%s\n\n【기계 복기】 %s" % (
        rec.get("race"), "→".join(str(m[0]) for m in (rec.get("marketAtFetch") or [])[:6]),
        json.dumps({k: p.get(k) for k in ("axis", "partners", "quinellas", "trios", "pace", "story", "market_view", "risk", "reasons", "excluded")}, ensure_ascii=False),
        "-".join(str(x) for x in res.get("order") or []), res.get("quinella"), res.get("trifecta"),
        _actual_text(rec, kind, rows), json.dumps(mr, ensure_ascii=False))
    model = model or FF._env("REVIEW_MODEL") or FF._env("FORM_FORECAST_MODEL", "claude-opus-5")
    import anthropic
    client = anthropic.Anthropic(api_key=FF._env("ANTHROPIC_API_KEY"))
    t0 = time.time()
    msg = client.messages.create(model=model, max_tokens=8000, system=SYSTEM, messages=[{"role": "user", "content": user}])
    txt = "".join(getattr(b, "text", "") for b in msg.content)
    m = re.search(r"\{.*\}", txt, flags=re.S)
    llm = json.loads(m.group(0)) if m else {"raw": txt}
    rec["review"] = {"machine": mr, "llm": llm, "model": model, "at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     "usage": {"in": msg.usage.input_tokens, "out": msg.usage.output_tokens}, "latencySec": round(time.time() - t0, 1),
                     "prompt_version": rec.get("prompt_version")}
    io.open(path, "w", encoding="utf-8").write(json.dumps(rec, ensure_ascii=False, indent=1))
    ymd = os.path.basename(os.path.dirname(path))
    io.open(os.path.join(KINDS[kind], "review_%s.jsonl" % ymd), "a", encoding="utf-8").write(json.dumps(
        {"race": rec.get("race"), "date": rec.get("date"), "kind": kind, "review": rec["review"], "result": res, "grade": rec.get("grade")}, ensure_ascii=False) + "\n")
    print("[복기] %s %s · 축 %s · 정답 위치 %s · 해침 %s · 교훈: %s" % (
        rec.get("race"), llm.get("verdict"), mr["axis_result"], mr["answer_where"], (llm.get("rule_tags") or {}).get("hurt"), llm.get("lesson")))
    return rec["review"]


def run(kind=None, model=None):
    n = 0
    for k, d in KINDS.items():
        if kind and k != kind: continue
        for f in sorted(glob.glob(os.path.join(d, "*", "*.json"))):
            rec = FF._load(f) or {}
            if rec.get("grade") and not rec.get("review"):
                try:
                    if review_one(f, k, model): n += 1
                except Exception as e:
                    print("[복기 오류] %s: %s" % (f, e))
    print("[복기] 새로 %d건" % n)


def stats(kind=None):
    from collections import Counter
    for k, d in KINDS.items():
        if kind and k != kind: continue
        helped = Counter(); hurt = Counter(); axis = Counter(); where = Counter(); clue = [0, 0]; pace = [0, 0]; n = 0
        lessons = []
        for f in sorted(glob.glob(os.path.join(d, "*", "*.json"))):
            rec = FF._load(f) or {}; rv = rec.get("review")
            if not rv: continue
            n += 1; mr = rv.get("machine") or {}; llm = rv.get("llm") or {}
            axis[mr.get("axis_result")] += 1
            for v in (mr.get("answer_where") or {}).values(): where[re.sub(r"\d", "", v)] += 1
            for t in (llm.get("rule_tags") or {}).get("helped") or []: helped[t] += 1
            for t in (llm.get("rule_tags") or {}).get("hurt") or []: hurt[t] += 1
            for m in llm.get("missed") or []:
                clue[1] += 1; clue[0] += bool(m.get("clue_in_card"))
            pc = llm.get("pace_check") or {}
            if "correct" in pc: pace[1] += 1; pace[0] += bool(pc.get("correct"))
            if llm.get("lesson"): lessons.append((rec.get("race"), llm["lesson"]))
        print("═══ %s 복기 집계 — %d건%s" % ("경마" if k == "horse" else "경륜", n, "  ⚠판정불가(<30)" if n < 30 else ""))
        if not n: continue
        print("  축 결과:", dict(axis), "| 정답마 위치:", dict(where))
        print("  전개 판단 적중 %d/%d · 놓친 정답마 중 출마표에 단서 있었음 %d/%d" % (pace[0], pace[1], clue[0], clue[1]))
        print("  도운 규칙:", helped.most_common(8))
        print("  해친 규칙:", hurt.most_common(8))
        for r, l in lessons[-5:]: print("   ·", r, "—", l)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true"); ap.add_argument("--stats", action="store_true")
    ap.add_argument("--kind", default=None); ap.add_argument("--model", default=None)
    a = ap.parse_args()
    if a.run: run(a.kind, a.model)
    if a.stats or not a.run: stats(a.kind)
