"""「돈은 알고 있었다」 복기 쇼츠 카드 — 경주 하나의 배당 흐름을 세로(1080×1920) 그림으로.

읽기 전용 도구다. 서버·추천 경로를 건드리지 않는다. 외부 패키지 없이 SVG 를 만들고
PC 에 있는 Edge(없으면 Chrome) 헤드리스로 PNG 를 찍는다.

사용
  python tools/youtube/money_flow.py 2026_11_22 오비히로 2          # 한 경주
  python tools/youtube/money_flow.py --pick-day 2026_11_22         # 그날 쇼츠 후보 목록(복승 20배+ 우선)
  python tools/youtube/money_flow.py --pick-day 2026_11_22 --make 3 # 후보 상위 3개를 바로 그림으로
옵션  --root <운영 폴더>(기본 Desktop\\경마분석서버) · --out <출력 폴더>(기본 <root>\\youtube_out)

🔴 콘텐츠 원칙(2026-11-22 대표 결정)
  · 사설 배당판 화면·사이트 이름·링크·「사세요」·수익 보장 문구를 넣지 않는다 — 분석·기록 콘텐츠다.
  · 발주 30초 전 이후 틱은 쓰지 않는다(그때는 살 수 없다 · 원칙 27).
  · 한 경주 안에서는 **틱이 가장 많은 한 소스**만 쓴다(소스를 섞으면 없는 급락이 생긴다 · 원칙 8).
  · 신호가 없던 이변은 「시장도 몰랐다」로 정직하게 쓴다 — 꾸며 내지 않는다.
"""
import argparse, glob, gzip, html, io, json, os, subprocess, sys, tempfile, time
from datetime import datetime

DEF_ROOT = r"C:\Users\Administrator\Desktop\경마분석서버"
LATE_SEC = 30.0
W, H = 1080, 1920


# ───────────────────────── 데이터 ─────────────────────────
def _load(p):
    if os.path.exists(p):
        return json.load(io.open(p, encoding="utf-8"))
    if os.path.exists(p + ".gz"):
        return json.load(gzip.open(p + ".gz", "rt", encoding="utf-8"))
    return None


def race_base(date, venue, rno):
    return "%s_%s_%s경주" % (date, venue, int(rno))


def _qmap(q):
    out = {}
    if isinstance(q, dict):
        for k, o in q.items():
            try:
                a, b = sorted(int(x) for x in str(k).replace("-", "+").split("+"))
                o = float(o)
            except Exception:
                continue
            if o > 0:
                out[(a, b)] = o
    return out


def load_race(root, base):
    al = _load(os.path.join(root, "data", "analysis_log", base + ".json")) or {}
    oh = _load(os.path.join(root, "data", "odds_history", base + ".json")) or {}
    dl = oh.get("deadline_epoch")
    snaps = list(oh.get("archive_snapshots") or []) + list(oh.get("snapshots") or [])
    rows = []
    for s in snaps:
        t = s.get("t")
        q = _qmap(s.get("quinella"))
        if not t or not q or s.get("after_close"):
            continue
        if dl and float(t) > float(dl) - LATE_SEC:
            continue
        rows.append((float(t), str(s.get("src") or "?"), q, s.get("win") or {}))
    # 가장 틱이 많은 소스 하나만
    cnt = {}
    for r in rows:
        cnt[r[1]] = cnt.get(r[1], 0) + 1
    src = max(cnt, key=cnt.get) if cnt else None
    ticks, seen = [], set()
    for r in sorted(rows):
        if r[1] != src or r[0] in seen:
            continue
        seen.add(r[0])
        ticks.append(r)
    res = al.get("result") or oh.get("result") or {}
    try:
        order = [int(res["1st"]), int(res["2nd"])] + ([int(res["3rd"])] if res.get("3rd") else [])
    except Exception:
        order = []
    pay = (res.get("payouts") or {}).get("quinella")
    return dict(base=base, al=al, deadline=dl, src=src, ticks=ticks, order=order,
                pay=float(pay) if isinstance(pay, (int, float)) and pay > 0 else None,
                sport=al.get("sport"), korea=(al.get("category") == "korea"),
                rk=al.get("raceKey") or oh.get("raceKey") or base.split("_", 3)[-1].replace("_", " "))


def late_alert(root, base, deadline):
    """logs/late_drop 에서 이 경주 첫 알림 → (돈 몰린 말 or None, 알림 시각 epoch, picks)"""
    date = base[:10].replace("_", "")
    rk = base[11:].replace("_", " ")
    p = os.path.join(root, "logs", "late_drop", date + ".jsonl")
    if not os.path.exists(p):
        return None
    for ln in io.open(p, encoding="utf-8"):
        try:
            e = json.loads(ln)
        except Exception:
            continue
        if e.get("rk") != rk or not e.get("ok") or not e.get("picks"):
            continue
        cnt = {}
        for pk in e["picks"]:
            for h in pk.get("combo") or []:
                cnt[int(h)] = cnt.get(int(h), 0) + 1
        money = None
        if len(e["picks"]) >= 2:
            h, n = max(cnt.items(), key=lambda kv: kv[1])
            if n >= 2:
                money = h
        return dict(money=money, t=float(e.get("t") or 0), picks=e["picks"])
    return None


def _win_odds(w, h):
    try:
        v = float((w or {}).get(str(h)))
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def horse_mode(ticks, h):
    """단승이 틱의 60%+ 에 있으면 단승, 아니면 '가장 싼 복승'.
    ⚠ 2026-11-23 사가 2R: 5번 '가장 싼 복승'이 조합 하나(5+11)만 8.6→2.9 로 튀어 74% 로 보였다.
      같은 틱 5번의 다른 조합은 그대로였고, 실제 돈은 단승 9.1→3.5(발주 83초 전)였다 — 단승이 말 단위 돈을 바로 보여준다."""
    n = sum(1 for *_x, w in ticks if _win_odds(w, h))
    return "win" if ticks and n >= 0.6 * len(ticks) else "quinella"


def horse_line(ticks, h, mode=None):
    """말 h 에 돈이 들어오면 내려가는 선 — 단승(있으면) 또는 그 말이 든 가장 싼 복승."""
    mode = mode or horse_mode(ticks, h)
    out = []
    for t, _s, q, w in ticks:
        if mode == "win":
            v = _win_odds(w, h)
            if v:
                out.append((t, v))
            continue
        vals = [o for (a, b), o in q.items() if h in (a, b)]
        if vals:
            out.append((t, min(vals)))
    return out


def drop_info(series):
    """첫 값 대비 그 뒤 최저값까지의 하락 → (첫값, 최저값, 최저 시각, 하락%)"""
    if len(series) < 3:
        return None
    first = series[0][1]
    t_low, low = min(series[1:], key=lambda x: x[1])
    return first, low, t_low, (first - low) / first * 100 if first else 0.0


def story(R, alert):
    """화면에 올릴 한 줄 이야기 — 신호가 없으면 정직하게 「시장도 몰랐다」."""
    dl = R["deadline"]
    if len(R["order"]) < 2 or not R["ticks"]:
        return None
    win = tuple(sorted(R["order"][:2]))
    lines = {h: horse_line(R["ticks"], h) for h in win}
    info = {h: drop_info(lines[h]) for h in win}
    if alert and alert.get("money") in win:
        h = alert["money"]
        sec = (dl - alert["t"]) if dl else None
        return dict(kind="alert", horse=h, sec=sec,
                    head="발주 %s 전, %d번에 돈이 몰렸다" % (_fmt_sec(sec), h),
                    sub="마감 직전 급락 신호 — 그 말이 %d착" % (R["order"].index(h) + 1))
    best = None
    for h in win:
        i = info[h]
        if i and (best is None or i[3] > best[1][3]):
            best = (h, i)
    if best and best[1][3] >= 15:
        h, (first, low, t_low, pct) = best
        sec = (dl - t_low) if dl else None
        lab = "단승" if horse_mode(R["ticks"], h) == "win" else "가장 싼 복승"
        return dict(kind="drop", horse=h, sec=sec,
                    head="%d번 배당이 %.0f%% 빠졌다" % (h, pct),
                    sub="%s %.1f → %.1f배 · 발주 %s 전" % (lab, first, low, _fmt_sec(sec)))
    # 정답 조합이 마지막 표시값보다 확정에서 크게 낮다 = 발주 30초 안(살 수 없던 구간)에 돈이 들어왔다
    last_win = [q.get(win) for _t, _s, q, _w in R["ticks"] if q.get(win)]
    if last_win and R["pay"] and R["pay"] <= 0.75 * last_win[-1]:
        # ⚠ 「30초 안에 돈이 들어왔다」고 단정하지 않는다 — 공개 배당 갱신 지연일 수도 있다(도쿄 10R 436 → 174.2)
        return dict(kind="last", horse=None, sec=None, head="마지막에 배당이 확 줄었다",
                    sub="마감 직전 표시 %.0f배 → 확정 %.1f배" % (last_win[-1], R["pay"]))
    return dict(kind="none", horse=None, sec=None, head="시장도 몰랐다",
                sub="정답 말들의 배당은 마감까지 크게 움직이지 않았다")


def _fmt_sec(sec):
    if sec is None:
        return "?"
    sec = max(0, int(round(sec)))
    return "%d분 %02d초" % (sec // 60, sec % 60) if sec >= 60 else "%d초" % sec


# ───────────────────────── 그림(SVG) ─────────────────────────
C_BG, C_FG, C_DIM, C_GRID = "#0f1115", "#f2f2f2", "#8b93a1", "#262b35"
C_GOLD, C_RED, C_LINE2 = "#f5c542", "#ff5a5a", "#5b6474"
FONT = "'Malgun Gothic','맑은 고딕',sans-serif"


def _t(x, y, s, size, color=C_FG, weight="400", anchor="start"):
    return ('<text x="%d" y="%d" font-family="%s" font-size="%d" font-weight="%s" fill="%s" text-anchor="%s">%s</text>'
            % (x, y, FONT, size, weight, color, anchor, html.escape(str(s))))


def build_svg(R, alert, st):
    dl = R["deadline"]
    ticks = R["ticks"]
    win = tuple(sorted(R["order"][:2]))
    # 차트 대상: 정답 조합 · 정답 두 말의 최저복승 · (참고) 시작 시점 시장 최저 조합
    s_win = [(t, q.get(win)) for t, _s, q, _w in ticks if q.get(win)]
    first_q = ticks[0][2]
    fav = min(first_q, key=first_q.get)
    s_fav = [(t, q.get(fav)) for t, _s, q, _w in ticks if q.get(fav)] if fav != win else []
    hl = {h: horse_line(ticks, h) for h in win}

    x0, x1, y0, y1 = 110, 1000, 760, 1430
    t_end = dl if dl else ticks[-1][0]
    t_beg = min(t for t, *_ in ticks)
    span = max(60.0, t_end - t_beg)

    vals = [v for _, v in s_win] + [v for _, v in s_fav] + [v for h in win for _, v in hl[h]] + ([R["pay"]] if R["pay"] else [])
    vmin, vmax = min(vals), max(vals)
    import math
    lo, hi = math.log(max(1.0, vmin * 0.85)), math.log(vmax * 1.15)

    def X(t):
        return x0 + (t - t_beg) / span * (x1 - x0)

    def Y(v):
        return y1 - (math.log(v) - lo) / (hi - lo) * (y1 - y0) if hi > lo else (y0 + y1) / 2

    def path(series, color, width, dash=""):
        if len(series) < 2:
            return ""
        d = " ".join(("M" if i == 0 else "L") + "%.1f,%.1f" % (X(t), Y(v)) for i, (t, v) in enumerate(series))
        return ('<path d="%s" fill="none" stroke="%s" stroke-width="%d" stroke-linejoin="round" %s/>'
                % (d, color, width, ('stroke-dasharray="%s"' % dash) if dash else ""))

    p = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' % (W, H, W, H),
         '<rect width="100%%" height="100%%" fill="%s"/>' % C_BG]
    # 머리
    p.append(_t(W // 2, 170, "돈은 알고 있었다", 92, C_GOLD, "800", "middle"))
    date = R["base"][:10].replace("_", ".")
    kind = "한국경마" if R["korea"] else ("경륜" if R["sport"] == "cycle" else "경마")
    p.append(_t(W // 2, 250, "%s · %s · %s" % (date, R["rk"], kind), 40, C_DIM, "400", "middle"))
    # 결과 칩
    order = " - ".join(map(str, R["order"][:3]))
    p.append('<rect x="190" y="300" width="700" height="150" rx="28" fill="#1b1f27" stroke="%s" stroke-width="3"/>' % C_GOLD)
    p.append(_t(W // 2, 370, "결과  " + order, 60, C_FG, "700", "middle"))
    if R["pay"]:
        p.append(_t(W // 2, 428, "복승 %s  %.1f배" % ("+".join(map(str, win)), R["pay"]), 40, C_GOLD, "700", "middle"))
    # 이야기
    p.append(_t(W // 2, 560, st["head"], 64 if len(st["head"]) <= 14 else 56,
                C_RED if st["kind"] != "none" else C_FG, "800", "middle"))
    p.append(_t(W // 2, 630, st["sub"], 36, C_DIM, "400", "middle"))
    # 차트 틀
    for v in _nice_ticks(math.exp(lo), math.exp(hi)):
        yy = Y(v)
        p.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" stroke="%s" stroke-width="2"/>' % (x0, x1, yy, yy, C_GRID))
        p.append(_t(x0 - 14, int(yy) + 10, ("%.0f" % v) if v >= 10 else ("%.1f" % v), 28, C_DIM, "400", "end"))
    for m in range(0, int(span // 60) + 1):
        tt = t_end - m * 60
        if tt < t_beg:
            break
        xx = X(tt)
        p.append('<line x1="%.1f" x2="%.1f" y1="%d" y2="%d" stroke="%s" stroke-width="1"/>' % (xx, xx, y0, y1, C_GRID))
        if m % 2 == 0:
            p.append(_t(int(xx), y1 + 46, "발주" if m == 0 else "%d분 전" % m, 28, C_DIM, "400", "middle"))
    p.append(_t(x0, y0 - 24, "배당(배) — 내려갈수록 돈이 몰린 것", 30, C_DIM))
    # 선
    if s_fav:
        p.append(path(s_fav, C_LINE2, 5, "14,10"))
    for h in win:
        p.append(path(hl[h], "#7aa2ff" if h == win[0] else "#5fd3a0", 5))
    p.append(path(s_win, C_GOLD, 9))
    # 확정 배당 — 발주 지점의 금색 점(마지막 30초 변화를 감추지 않는다)
    if R["pay"]:
        xx, yy = X(t_end), Y(R["pay"])
        if s_win:
            p.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="4" stroke-dasharray="6,8"/>'
                     % (X(s_win[-1][0]), Y(s_win[-1][1]), xx, yy, C_GOLD))
        p.append('<circle cx="%.1f" cy="%.1f" r="16" fill="%s"/>' % (xx, yy, C_GOLD))
        p.append(_t(int(xx) - 24, int(yy) - 26, "확정 %.1f" % R["pay"], 30, C_GOLD, "700", "end"))
    # 표식: 이야기 시각
    if st.get("sec") is not None and dl:
        tx = dl - st["sec"]
        if t_beg <= tx <= t_end:
            xx = X(tx)
            p.append('<line x1="%.1f" x2="%.1f" y1="%d" y2="%d" stroke="%s" stroke-width="4" stroke-dasharray="10,8"/>'
                     % (xx, xx, y0, y1, C_RED))
            p.append('<circle cx="%.1f" cy="%d" r="14" fill="%s"/>' % (xx, y0 + 10, C_RED))
    # 범례
    ly = 1560
    leg = [(C_GOLD, 9, "", "정답 복승 %s" % "+".join(map(str, win)))]
    leg += [("#7aa2ff" if h == win[0] else "#5fd3a0", 5, "",
             ("%d번 단승" if horse_mode(ticks, h) == "win" else "%d번이 든 가장 싼 복승") % h) for h in win]
    if s_fav:
        leg.append((C_LINE2, 5, "14,10", "처음 시장 1번 인기 %s" % "+".join(map(str, fav))))
    for i, (c, w, dash, label) in enumerate(leg):
        yy = ly + i * 58
        p.append('<line x1="150" x2="250" y1="%d" y2="%d" stroke="%s" stroke-width="%d" %s/>'
                 % (yy, yy, c, w, ('stroke-dasharray="%s"' % dash) if dash else ""))
        p.append(_t(275, yy + 12, label, 36))
    # 꼬리
    p.append(_t(W // 2, 1840, "분석·기록 콘텐츠입니다 · 베팅 권유가 아닙니다", 30, C_DIM, "400", "middle"))
    p.append(_t(W // 2, 1885, "배당 출처: 발주 30초 전까지 수집한 공개 배당 흐름", 26, "#5b6474", "400", "middle"))
    p.append("</svg>")
    return "\n".join(p)


def _nice_ticks(a, b):
    cands = [1.2, 1.5, 2, 3, 5, 7, 10, 15, 20, 30, 50, 70, 100, 150, 200, 300, 500, 1000]
    out = [c for c in cands if a <= c <= b]
    if len(out) > 6:
        out = out[::2]
    return out


# ───────────────────────── PNG 렌더 ─────────────────────────
def _browser():
    # ⚠ 2026-11-23 실측: 이 PC 의 Edge 헤드리스는 rc=0 인데 파일을 안 쓴다 → Chrome 먼저
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if os.path.exists(p):
            return p
    return None


def render_png(svg, out_png):
    exe = _browser()
    page = ('<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;padding:0;background:%s;}'
            'svg{display:block}</style></head><body>%s</body></html>' % (C_BG, svg))
    tmpdir = tempfile.mkdtemp(prefix="yt_")
    hp = os.path.join(tmpdir, "card.html")
    io.open(hp, "w", encoding="utf-8").write(page)
    if not exe:
        return None
    prof = os.path.join(tmpdir, "prof")
    cmd = [exe, "--headless", "--disable-gpu", "--hide-scrollbars", "--no-first-run", "--no-default-browser-check",
           "--user-data-dir=" + prof, "--force-device-scale-factor=1",
           "--window-size=%d,%d" % (W, H), "--screenshot=" + os.path.abspath(out_png), "file:///" + hp.replace("\\", "/")]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
    return out_png if os.path.exists(out_png) else None


# ───────────────────────── 후보 고르기 ─────────────────────────
def pick_day(root, date, min_pay=20.0):
    out = []
    for f in sorted(glob.glob(os.path.join(root, "data", "analysis_log", date + "_*.json"))):
        base = os.path.basename(f)[:-5]
        try:
            R = load_race(root, base)
        except Exception:
            continue
        if not R["pay"] or len(R["order"]) < 2 or len(R["ticks"]) < 5:
            continue
        al = late_alert(root, base, R["deadline"])
        st = story(R, al)
        if not st:
            continue
        score = R["pay"] * {"alert": 2.0, "drop": 1.4, "last": 1.0}.get(st["kind"], 0.6)
        out.append((score, base, R["pay"], st["kind"], st["head"]))
    out.sort(reverse=True)
    return [o for o in out if o[2] >= min_pay] or out[:10]


def make(root, base, out_dir):
    R = load_race(root, base)
    if len(R["order"]) < 2 or len(R["ticks"]) < 3:
        return None, "결과나 배당 틱이 부족하다(틱 %d · 결과 %s)" % (len(R["ticks"]), R["order"])
    al = late_alert(root, base, R["deadline"])
    st = story(R, al)
    svg = build_svg(R, al, st)
    os.makedirs(out_dir, exist_ok=True)
    io.open(os.path.join(out_dir, base + ".svg"), "w", encoding="utf-8").write(svg)
    png = render_png(svg, os.path.join(out_dir, base + ".png"))
    return png or os.path.join(out_dir, base + ".svg"), "%s · 소스 %s · 틱 %d · %s" % (base, R["src"], len(R["ticks"]), st["head"])


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("args", nargs="*")
    ap.add_argument("--pick-day")
    ap.add_argument("--make", type=int, default=0)
    ap.add_argument("--root", default=DEF_ROOT)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    out_dir = a.out or os.path.join(a.root, "youtube_out")
    if a.pick_day:
        c = pick_day(a.root, a.pick_day)
        for i, (sc, base, pay, kind, head) in enumerate(c[:15]):
            print("%2d. %-28s 복승 %6.1f배  [%s] %s" % (i + 1, base, pay, kind, head))
        for sc, base, *_ in c[:a.make]:
            path, msg = make(a.root, base, out_dir)
            print("→", path, "|", msg)
        return
    if len(a.args) == 3:
        base = race_base(a.args[0], a.args[1], a.args[2])
    elif len(a.args) == 1:
        base = os.path.basename(a.args[0]).replace(".json", "")
    else:
        ap.error("날짜 경기장 경주번호  또는 --pick-day 날짜")
    path, msg = make(a.root, base, out_dir)
    print(path, "|", msg)


if __name__ == "__main__":
    main()
