"""「사람 vs AI vs 분석기」 주간 성적표 — 세로(1080×1920) 그림.

읽기 전용. 3층 기록(logs/review_owner/*.jsonl)의 **발주 전(pre) 픽**만 센다(원칙 27).
분석기는 같은 경주의 판정 명단(analysis_log corePicks.displayedCombos.quinellas).

사용
  python tools/youtube/weekly_board.py                       # 최근 7일
  python tools/youtube/weekly_board.py --from 2026_11_16 --to 2026_11_22
옵션  --root · --out(기본 <root>\\youtube_out)

집계(경주 = 대표나 Claude 가 발주 전에 픽을 낸 경주만 · 셋 다 같은 경주 집합)
  · 복승 적중 = 픽한 조합 중 하나가 1·2착 쌍 · 구좌 = 복승 조합 수 · 회수율 = 맞힌 확정배당 합 ÷ 구좌
  · 삼복승 적중 = 삼복승 조합 중 하나가 1~3착(배당: trio 키 → 없으면 trifecta = 지방 3連複)
  · 결과는 analysis_log 확정값 우선, 없으면 채팅에 적힌 결과(배당 없이 적중만)
🔴 문구 원칙: 베팅 권유·사이트·수익 보장 없음. 적은 표본은 「표본 N경주」를 반드시 함께 쓴다.
"""
import argparse, glob, html, io, json, os, sys, time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import money_flow as MF   # _browser · _load · 색·글꼴 재사용

W, H = 1080, 1920


def _pairs(v):
    out = []
    for c in v or []:
        try:
            out.append(tuple(sorted(int(x) for x in c)))
        except Exception:
            pass
    return out


def _who_lines(e):
    """한 줄 → [(작성자, 복승들, 삼복승들)] · 옛 형식(source=chat · claude 하위 dict)도 읽는다."""
    src = str(e.get("source") or "")
    who = e.get("who")
    if who in ("owner", "claude"):
        return [(who, _pairs(e.get("my_pairs")), _pairs(e.get("my_trios")))]
    if "claude" in src:
        return [("claude", _pairs(e.get("my_pairs")), _pairs(e.get("my_trios")))]
    out = [("owner", _pairs(e.get("my_pairs")), _pairs(e.get("my_trios")))]
    cl = e.get("claude")
    if isinstance(cl, dict) and cl.get("pairs"):
        out.append(("claude", _pairs(cl.get("pairs")), _pairs(cl.get("trios"))))
    return out


def _keiba_result(date, rk):
    """지방경마만 · tools/form_forecast._result_from_keiba 재사용(요청 1회) · 실패하면 None."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import form_forecast as FF
        venue, rno = rk.rsplit(" ", 1)
        rno = int(rno.replace("경주", ""))
        rev = {v: k for k, v in FF.BABA.items()}
        rev.setdefault("오이", "20")
        if venue not in rev:
            return None
        return FF._result_from_keiba(date.replace("_", "/"), rev[venue], rno)
    except Exception:
        return None


def collect(root, d_from, d_to):
    races = {}
    for f in sorted(glob.glob(os.path.join(root, "logs", "review_owner", "*.jsonl"))):
        for ln in io.open(f, encoding="utf-8"):
            try:
                e = json.loads(ln)
            except Exception:
                continue
            date, rk = e.get("date"), e.get("rk")
            if not date or not rk or not (d_from <= date <= d_to):
                continue
            R = races.setdefault((date, rk), {"pre": {}, "chat_result": None})
            if e.get("result"):
                R["chat_result"] = [int(x) for x in e["result"]]
            if e.get("phase") != "pre":
                continue
            for who, ps, ts in _who_lines(e):
                if not ps and not ts:
                    continue
                prev = R["pre"].get(who)
                if prev is None or float(e.get("t") or 0) >= prev[0]:
                    R["pre"][who] = (float(e.get("t") or 0), ps, ts)
    rows = []
    for (date, rk), R in sorted(races.items()):
        if not R["pre"]:
            continue
        al = MF._load(os.path.join(root, "data", "analysis_log", "%s_%s.json" % (date, rk.replace(" ", "_")))) or {}
        res = al.get("result") or {}
        try:
            order = [int(res["1st"]), int(res["2nd"]), int(res["3rd"])]
        except Exception:
            order = R["chat_result"] or []
        pays = res.get("payouts") or {}
        qpay = pays.get("quinella") if isinstance(pays.get("quinella"), (int, float)) and pays.get("quinella") > 0 else None
        tp = pays.get("trio") if "trio" in pays else pays.get("trifecta")
        tpay = tp if isinstance(tp, (int, float)) and tp > 0 else None
        if qpay is None:   # 서버 결과가 비면(오비히로 11/16 9R 실례) keiba.go.jp 성적표에서 확정배당만 보충
            kb = _keiba_result(date, rk)
            if kb and kb.get("order") and tuple(sorted(kb["order"][:2])) == tuple(sorted(order[:2])):
                qpay = kb.get("quinella")
                tpay = tpay or kb.get("trifecta")
                if len(order) < 3 and kb["order"][2]:
                    order = kb["order"]
        ana = _pairs(((al.get("corePicks") or {}).get("displayedCombos") or {}).get("quinellas"))
        rows.append(dict(date=date, rk=rk, order=order, qpay=qpay, tpay=tpay,
                         owner=R["pre"].get("owner"), claude=R["pre"].get("claude"), ana=ana))
    return [r for r in rows if len(r["order"]) >= 2]


def score(rows, who):
    n = hit = tick = thit = tn = 0
    pay = 0.0
    big = None
    for r in rows:
        if who == "ana":
            ps, ts = r["ana"], []
        else:
            v = r.get(who)
            if not v:
                continue
            ps, ts = v[1], v[2]
        if not ps and not ts:
            continue
        n += 1
        win = tuple(sorted(r["order"][:2]))
        if ps:
            tick += len(ps)
            if win in ps:
                hit += 1
                if r["qpay"]:
                    pay += r["qpay"]
                    if big is None or r["qpay"] > big[0]:
                        big = (r["qpay"], r["rk"], r["date"])
        if ts and len(r["order"]) >= 3:
            tn += 1
            if tuple(sorted(r["order"][:3])) in ts:
                thit += 1
    return dict(n=n, hit=hit, tick=tick, pay=pay, rec=(100 * pay / tick) if tick else None,
                thit=thit, tn=tn, big=big)


def _mark(r, who):
    if who == "ana":
        ps, ts = r["ana"], []
    else:
        v = r.get(who)
        if not v:
            return "—", "dim"
        ps, ts = v[1], v[2]
    if not ps:
        return "—", "dim"
    win = tuple(sorted(r["order"][:2]))
    if win in ps:
        return ("◎ %.1f" % r["qpay"]) if r["qpay"] else "◎", "hit"
    if ts and len(r["order"]) >= 3 and tuple(sorted(r["order"][:3])) in ts:
        return "삼복 ◎", "hit"
    return "✕", "miss"


def build_html(rows, d_from, d_to):
    S = {k: score(rows, k) for k in ("owner", "claude", "ana")}
    name = {"owner": "대표", "claude": "AI (Claude)", "ana": "분석기"}
    col = {"owner": "#7aa2ff", "claude": "#f5c542", "ana": "#5fd3a0"}
    best = max(S, key=lambda k: (S[k]["hit"] / S[k]["n"]) if S[k]["n"] else -1)
    cards = []
    for k in ("owner", "claude", "ana"):
        s = S[k]
        rate = (100 * s["hit"] / s["n"]) if s["n"] else 0
        crown = "👑 " if k == best and s["n"] else ""
        big = ("최고 %.1f배" % s["big"][0]) if s["big"] else "최고 —"
        cards.append(
            '<div class="card" style="border-color:%s"><div class="nm" style="color:%s">%s%s</div>'
            '<div class="rate">%d<span>/%d</span></div><div class="sub">복승 적중 %.0f%%</div>'
            '<div class="sub">%s · 구좌 %d</div>%s</div>'
            % (col[k], col[k], crown, name[k], s["hit"], s["n"], rate, big, s["tick"],
               ('<div class="sub">삼복승 %d/%d</div>' % (s["thit"], s["tn"])) if s["tn"] else ""))
    trs = []
    for r in rows[-14:]:
        cells = []
        for k in ("owner", "claude", "ana"):
            t, cls = _mark(r, k)
            cells.append('<td class="%s">%s</td>' % (cls, html.escape(t)))
        trs.append('<tr><td class="rk">%s<br><span>%s</span></td><td class="od">%s</td>%s</tr>'
                   % (html.escape(r["rk"]), r["date"][5:].replace("_", "/"),
                      html.escape("-".join(map(str, r["order"][:3]))), "".join(cells)))
    period = "%s ~ %s" % (d_from.replace("_", "."), d_to[5:].replace("_", "."))
    css = """
    *{box-sizing:border-box} html,body{margin:0;background:#0f1115;color:#f2f2f2;font-family:'Malgun Gothic',sans-serif}
    .wrap{width:1080px;height:1920px;padding:70px 60px 0;position:relative}
    h1{margin:0;text-align:center;font-size:84px;color:#f5c542;font-weight:800;letter-spacing:-2px}
    .per{text-align:center;color:#8b93a1;font-size:38px;margin:14px 0 40px}
    .cards{display:flex;gap:22px}
    .card{flex:1;background:#1b1f27;border:3px solid;border-radius:26px;padding:26px 18px;text-align:center}
    .nm{font-size:38px;font-weight:800} .rate{font-size:96px;font-weight:800;margin:6px 0}
    .rate span{font-size:44px;color:#8b93a1} .sub{font-size:28px;color:#aab2bf;margin-top:6px}
    table{width:100%;border-collapse:collapse;margin-top:44px;font-size:32px}
    th{color:#8b93a1;font-weight:400;padding:10px 6px;border-bottom:2px solid #262b35;font-size:28px}
    td{padding:14px 6px;border-bottom:1px solid #262b35;text-align:center}
    td.rk{text-align:left;font-weight:700} td.rk span{color:#8b93a1;font-weight:400;font-size:24px}
    td.od{color:#f2f2f2;font-weight:700} td.hit{color:#f5c542;font-weight:800} td.miss{color:#5b6474} td.dim{color:#3a404c}
    .foot{position:absolute;bottom:40px;left:0;right:0;text-align:center;color:#8b93a1;font-size:26px;line-height:1.6}
    """
    body = ('<div class="wrap"><h1>사람 vs AI vs 분석기</h1><div class="per">주간 예측 대결 · %s</div>'
            '<div class="cards">%s</div>'
            '<table><tr><th style="text-align:left">경주</th><th>결과</th><th>대표</th><th>AI</th><th>분석기</th></tr>%s</table>'
            '<div class="foot">◎ 복승 적중(숫자 = 확정 배당) · ✕ 미적중 · — 픽 없음<br>'
            '발주 전에 기록된 픽만 셉니다 · 표본 %d경주 · 분석·기록 콘텐츠이며 베팅 권유가 아닙니다</div></div>'
            % (period, "".join(cards), "".join(trs), len(rows)))
    return '<!doctype html><html><head><meta charset="utf-8"><style>%s</style></head><body>%s</body></html>' % (css, body), S


def render_page(page, out_png):
    import subprocess, tempfile
    exe = MF._browser()
    d = tempfile.mkdtemp(prefix="ytb_")
    hp = os.path.join(d, "board.html")
    io.open(hp, "w", encoding="utf-8").write(page)
    if not exe:
        return None
    subprocess.run([exe, "--headless", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                    "--user-data-dir=" + os.path.join(d, "prof"), "--force-device-scale-factor=1",
                    "--window-size=%d,%d" % (W, H), "--screenshot=" + os.path.abspath(out_png),
                    "file:///" + hp.replace("\\", "/")],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
    return out_png if os.path.exists(out_png) else None


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d_from")
    ap.add_argument("--to", dest="d_to")
    ap.add_argument("--root", default=MF.DEF_ROOT)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    d_to = a.d_to or datetime.now().strftime("%Y_%m_%d")
    d_from = a.d_from or (datetime.strptime(d_to, "%Y_%m_%d") - timedelta(days=6)).strftime("%Y_%m_%d")
    rows = collect(a.root, d_from, d_to)
    page, S = build_html(rows, d_from, d_to)
    out_dir = a.out or os.path.join(a.root, "youtube_out")
    os.makedirs(out_dir, exist_ok=True)
    base = "board_%s_%s" % (d_from, d_to)
    io.open(os.path.join(out_dir, base + ".html"), "w", encoding="utf-8").write(page)
    png = render_page(page, os.path.join(out_dir, base + ".png"))
    for k, s in S.items():
        print("%-7s 경주 %2d 복승 적중 %2d 구좌 %2d 맞힌 배당 %.1f 회수 %s 삼복 %d/%d" % (
            k, s["n"], s["hit"], s["tick"], s["pay"], ("%.0f%%" % s["rec"]) if s["rec"] is not None else "-", s["thit"], s["tn"]))
    for r in rows:
        print(" ", r["date"], r["rk"], r["order"], "대표", _mark(r, "owner")[0], "AI", _mark(r, "claude")[0], "분석기", _mark(r, "ana")[0])
    print(png or os.path.join(out_dir, base + ".html"))


if __name__ == "__main__":
    main()
