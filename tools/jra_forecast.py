# -*- coding: utf-8 -*-
"""중앙경마(JRA) 전적표 분석 — netkeiba 馬柱(shutuba_past · 5走)를 읽어 form_forecast 와 같은 방식으로 예측한다.

대표(2026-09-26): 「중앙경마도 전적표로 분석할 수 있어? · 가능하다면 내가 필요할 때만 사용하자」
  ⇒ **데몬 없음 · 8012 에서 누를 때만** 돈다(netkeiba 요청 상한을 아낀다 · 경주당 요청 ≤2).
원칙
  · netkeiba 요청은 서버와 **같은 가드**(netkeiba_guard · 분·시·일 상한 공유)를 통과해야 나간다.
  · 모델·검증기·프롬프트는 form_forecast 를 그대로 쓴다(배당·우리 추천 미투입 · 3대 금지 입력 동일).
  · 저장 logs/jra_forecast/<YYYYMMDD>/<경기장>_<N>경주.json · <YYYYMMDD>.jsonl (form_forecast 와 같은 모양)
사용
  python tools/jra_forecast.py 한신 11            # 오늘
  python tools/jra_forecast.py 한신 11 20260926
"""
import io, os, re, sys, json, time, datetime
from urllib.request import Request, urlopen

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))
import form_forecast as FF            # noqa: E402
try:
    import netkeiba_guard as G        # noqa: E402
except Exception:                     # 가드가 없으면 요청하지 않는다(서버와 같은 안전 기준)
    G = None

OUT = os.path.join(BASE, "logs", "jra_forecast")
# netkeiba 場코드 → (우리 저장 토큰, 한자) — app.py _JRA_TRACK 과 같은 표
TRACK = {"01": ("삿포로", "札幌"), "02": ("하코다테", "函館"), "03": ("후쿠시마", "福島"), "04": ("니가타", "新潟"),
         "05": ("도쿄", "東京"), "06": ("나카야마", "中山"), "07": ("추쿄", "中京"), "08": ("교토", "京都"),
         "09": ("한신", "阪神"), "10": ("고쿠라", "小倉")}
ALIAS = {"쿄토": "교토", "코쿠라": "고쿠라", "주쿄": "추쿄", "중경": "추쿄", "나카교": "추쿄", "히코다테": "하코다테",
         "후크시마": "후쿠시마", "동경": "도쿄", "토쿄": "도쿄", "삿뽀로": "삿포로"}
_SCHED = {}

JRA_NOTE = """
[중앙경마(JRA) 출마표 읽는 법 — 지방경마와 다른 점]
· 말마다 前走~5走가 「날짜 경기장 | 착순 | 경주명 | 등급(GI·GII·GIII·L·OP·3勝·2勝·1勝·未勝利·新馬) | 코스거리 타임 馬場 | 두수 마번 인기 기수 부담 | 통과순위 (상3F) 마체중 | 1착마(차이)」 순으로 나온다.
· 등급 이동을 반드시 본다 — 같은 착순이라도 GIII 5착과 OP 5착은 다르다. 승급 첫 출주·강급을 구분한다.
· 코스(ダ=더트·芝=잔디)와 거리가 이번 경주와 같은 기록을 우선한다. 잔디 기록만 있는 말의 더트 첫 출주는 불확실하다.
· 「(0.2)」 같은 괄호 숫자는 1착마와의 타임 차이다(음수면 그 말이 1착). 착순보다 이 차이를 믿는다.
· 휴양 표기(「4ヵ月休養」「中17週」)와 「鉄砲 [1.0.2.1]」(휴양 후 첫 출주 성적)을 본다.
"""


def _get(url):
    if G is None:
        raise RuntimeError("netkeiba_guard 없음 — 요청하지 않는다")
    ok, why = G.allow("live")
    for _ in range(12):
        if ok or "간격" not in why:
            break
        time.sleep(0.5)
        ok, why = G.allow("live")
    if not ok:
        raise RuntimeError("netkeiba 요청 제한: %s" % why)
    try:
        b = urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "*/*"}),
                    timeout=15).read()
        G.record(True)
    except Exception as e:
        G.record(False, getattr(e, "code", None))
        raise
    m = re.search(rb'charset=["\']?([\w-]+)', b[:3000])
    return b.decode(m.group(1).decode() if m else "utf-8", "ignore")


def venue_code(v):
    v = ALIAS.get(str(v).strip(), str(v).strip())
    for c, (kr, jp) in TRACK.items():
        if v in (kr, jp):
            return c
    return None


def race_id(ymd, code, rno):
    if ymd not in _SCHED:
        _SCHED[ymd] = set(re.findall(r"race_id=(\d{12})", _get(
            "https://race.netkeiba.com/top/race_list_sub.html?kaisai_date=%s" % ymd)))
    rr = "%02d" % int(rno)
    for rid in sorted(_SCHED[ymd]):
        if rid[4:6] == code and rid[10:12] == rr:
            return rid
    return None


def past_text(html):
    """馬柱 html → (헤더 한 줄, 압축 본문, 마번 목록)."""
    t = FF._text(html)
    title = re.search(r"<title>(.*?)</title>", html, re.S)
    head = re.sub(r"\s+", " ", title.group(1)).strip() if title else ""
    m = re.search(r"(ダ|芝|障)\s*\d{3,4}m[^\n]{0,80}", t)
    if m:
        head += " · " + re.sub(r"\s+", " ", m.group(0)).strip()
    k = t.find("枠")
    body = t[k:] if k >= 0 else t
    # 말 기록이 끝나면 범례·馬券 안내·**netkeiba 자체 전개 예상(レース展開予想)**이 붙는다 — 남의 예측이라 반드시 잘라 낸다
    for stop in ("選んだ馬のオッズを見る", "出馬表(5走表示)の見方", "出走頭数、馬番、単勝人気", "馬券の買い方",
                 "レース展開予想", "出馬表の見方", "ページトップ"):
        e = body.find(stop)
        if e > 0:
            body = body[:e]
    body = body[:body.rfind("\n")] if "\n" in body[-200:] else body
    body = body.replace("-- ◎ ◯ ▲ △ ☆ &#10003 消", "")
    body = " | ".join(x.strip() for x in body.split("\n") if x.strip())
    # 🔴 3대 금지 입력 ① 배당·인기 — 말마다 「마체중 | 단승오즈 | 인기」 칸이 있다(발주 전엔 숫자가 찬다). 지운다.
    body, _n_odds = re.subn(r"(kg \([+\-]?\d+\)) \| [\d\-\.]+ \| [\d\*]+ \|", r"\1 |", body)
    field = []
    for mm in re.finditer(r"(?:^|\| )([1-8]) \| (\d{1,2}) \| ", body):
        n = int(mm.group(2))
        if 1 <= n <= 18 and n not in field:
            field.append(n)
    return head, body, field


def _paths(ymd, venue, rno):
    d = os.path.join(OUT, ymd)
    os.makedirs(d, exist_ok=True)
    return d, os.path.join(d, "%s_%d경주.json" % (venue, rno)), os.path.join(OUT, "%s.jsonl" % ymd)


def forecast_one(venue, rno, ymd=None, model=None, force=False):
    ymd = ymd or time.strftime("%Y%m%d")
    code = venue_code(venue)
    if not code:
        return None, "중앙 경기장이 아님: %s" % venue
    kr = TRACK[code][0]
    d, path, jl = _paths(ymd, kr, int(rno))
    if os.path.exists(path) and not force:
        return FF._load(path), None
    rid = race_id(ymd, code, rno)
    if not rid:
        return None, "netkeiba 개최 목록에 없음: %s %s %sR" % (ymd, kr, rno)
    html = _get("https://race.netkeiba.com/race/shutuba_past.html?race_id=%s" % rid)
    head, body, field = past_text(html)
    if len(body) < 500 or not field:
        return None, "馬柱 본문을 못 읽음(길이 %d · 마번 %d)" % (len(body), len(field))
    model = model or FF._env("FORM_FORECAST_MODEL", "claude-opus-5")
    t0 = time.time()
    pred, usage, valid = FF.ask_validated(FF.SYSTEM + JRA_NOTE, FF._user_text(head, body), model, field, [], "horse", ())
    rec = {"date": ymd, "venue": kr, "raceId": rid, "rno": int(rno), "race": "%s %d경주" % (kr, int(rno)), "kind": "jra",
           "fetchedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "model": model, "usage": usage,
           "latencySec": round(time.time() - t0, 1), "prompt_version": getattr(FF, "PROMPT_VERSION", None),
           "head": head, "bodyChars": len(body), "field": field, "marketAtFetch": [],
           "prediction": pred, "validation": valid, "source": "netkeiba shutuba_past"}
    io.open(path, "w", encoding="utf-8").write(json.dumps(rec, ensure_ascii=False, indent=1))
    io.open(jl, "a", encoding="utf-8").write(json.dumps({k: v for k, v in rec.items() if k != "head"}, ensure_ascii=False) + "\n")
    return rec, None


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(0)
    rec, err = forecast_one(sys.argv[1], int(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else None, force=True)
    if err:
        print("실패:", err)
    else:
        p = rec.get("prediction") or {}
        print(rec["race"], rec["raceId"], "축", p.get("axis"), "상대", p.get("partners"), "복승", p.get("quinellas"),
              "삼복승", p.get("trios"), "· 검증", (rec.get("validation") or {}).get("passed"), "·", rec["latencySec"], "초")
