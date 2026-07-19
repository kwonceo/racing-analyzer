# -*- coding: utf-8 -*-
"""[패치 2026-07-19] 마감 후 기록 제외 판정 + 복기 화면 판정-표시 일치
─────────────────────────────────────────────────────────────────
모리오카 3R 복기: 마감 13:22 인데 로그의 유일한 추천(4+9)이 13:26(마감 후 재계산 잔재)에
기록돼 그걸로 ✅ 적중 판정 + 복기 화면은 현재 corePicks(6+9…)를 보여줘 판정과 표시가 어긋남.
① app.py  — 적중 판정에서 '마감 이후 기록된 추천' 제외. 마감 전 추천이 아예 없으면
            '추천 미형성(noRec)' = 적중/미적중 어느 쪽도 아님(성적 집계 제외).
② app.js  — 복기 블록이 판정에 실제 사용된 라이브 추천을 표시(판정-표시 일치) + 미형성 중립 표기.
실행: 경마분석서버 폴더에서  python tools\\patch_close_guard.py
(백업 자동 생성: app.py.bak-판정제외 / app.js.bak-판정제외)
"""
import os, sys, re, shutil, py_compile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PY = os.path.join(BASE, "app.py")
APP_JS = os.path.join(BASE, "static", "js", "app.js")

def read(p):
    return open(p, encoding="utf-8").read()

def write(p, s):
    open(p, "w", encoding="utf-8", newline="").write(s)

def fail(msg):
    print("❌ 패치 실패:", msg)
    print("   (파일은 변경되지 않았거나 .bak-판정제외 백업으로 복원 가능)")
    sys.exit(1)

# ══════════ ① app.py ══════════
s = read(APP_PY)
if "_live_exact_hit" not in s:
    fail("app.py 에 _live_exact_hit 가 없음 — 이전 '적중 정직화' 버전이 아님(패치 대상 아님)")
if '"noRec": True' in s:
    print("↷ app.py 는 이미 패치됨 — 건너뜀")
else:
    old_a = '''    try:
        _lc = _rec_combos_from_analysis_log(key)
        if not _lc:
            return None'''
    new_a = '''    # [마감 후 기록 제외 (2026-07-19·모리오카 3R)] 마감 13:22 인데 로그의 유일한 추천(4+9)이
    #   13:26 기록(마감 후 재계산 잔재) → 그걸로 ✅ 판정되던 문제. 마감 시각(odds_history 첫
    #   after_close 스냅샷) 이후 기록된 추천은 판정 제외. 마감 전 추천이 하나도 없고 분석 자체
    #   (analyzed_at)도 마감 후면 "추천 미형성"(noRec) — 적중/미적중 어느 쪽도 아님(집계 제외).
    try:
        _close_t = None
        try:
            _hp0, _, _ = _hist_path(key)
            _hd0 = json.load(open(_hp0, encoding="utf-8"))
            for _sx in (_hd0.get("snapshots") or []):
                if _sx.get("after_close") and _sx.get("time"):
                    _close_t = _sx["time"]
                    break
        except Exception:
            pass
        _log_doc = None
        try:
            _lp0, _, _ = _analysis_log_path(_canonical_log_key(key))
            _log_doc = json.load(open(_lp0, encoding="utf-8"))
        except Exception:
            _log_doc = None
        if _close_t and _log_doc:
            _rh0 = _log_doc.get("recommendation_history") or []
            _pre = [e for e in _rh0 if not e.get("time") or str(e.get("time")) < _close_t]
            _an_at = str(_log_doc.get("analyzed_at") or "")
            if _rh0 and not _pre and _an_at and _an_at >= _close_t:
                return {"hit": False, "trioHit": False, "matched": None, "matchedTrio": None,
                        "liveQuinellas": [], "noRec": True}
        _lc = _rec_combos_from_analysis_log(key)
        # [마감 후 기록 개별 제외] 마감 후에만 등장한 조합은 판정 대상에서 뺀다(마감 전 기록에도
        #   있던 조합은 유지 — 같은 추천이 마감 후 재기록된 경우는 정당).
        if _close_t and _log_doc:
            def _combset(pred):
                _out = set()
                for e in (_log_doc.get("recommendation_history") or []):
                    if pred(e):
                        for _k2 in ("quinella_main", "quinella_sub", "trifecta_main"):
                            _v2 = e.get(_k2)
                            if _v2:
                                try:
                                    _out.add(tuple(sorted(int(x) for x in re.split(r"[+\\-]", str(_v2)) if str(x).strip().isdigit())))
                                except Exception:
                                    pass
                return _out
            _only_post = (_combset(lambda e: e.get("time") and str(e["time"]) >= _close_t)
                          - _combset(lambda e: not e.get("time") or str(e["time"]) < _close_t))
            if _only_post:
                _lc = [c for c in _lc if tuple(sorted(int(x) for x in (c.get("combo") or []))) not in _only_post]
        if not _lc:
            return None'''
    if s.count(old_a) != 1:
        fail("app.py 패치A 지점 불일치(%d)" % s.count(old_a))
    s = s.replace(old_a, new_a)

    old_b = '''        return {"hit": hit, "trioHit": trio_hit, "matched": matched,
                "matchedTrio": matched_t, "liveQuinellas": live_q[:4]}'''
    new_b = '''        return {"hit": hit, "trioHit": trio_hit, "matched": matched,
                "matchedTrio": matched_t, "liveQuinellas": live_q[:4], "noRec": False}'''
    if s.count(old_b) != 1:
        fail("app.py 패치B 지점 불일치(%d)" % s.count(old_b))
    s = s.replace(old_b, new_b)

    old_c = '''            _judged = "live"
        else:
            _hit, _trio = bool(_ra.get("main_hit")), False
            _matched, _matched_t, _live_q = None, None, []
            _judged = "stored"
        return {"top3": _t3, "hit": _hit, "trioHit": _trio, "subHit": bool(_ra.get("sub_hit")),
                "matched": _matched, "matchedTrio": _matched_t, "liveQuinellas": _live_q,
                "judged": _judged,'''
    new_c = '''            _judged = "live"
            _norec = bool(_lx.get("noRec"))
        else:
            _hit, _trio = bool(_ra.get("main_hit")), False
            _matched, _matched_t, _live_q = None, None, []
            _judged = "stored"
            _norec = False
        return {"top3": _t3, "hit": _hit, "trioHit": _trio, "subHit": bool(_ra.get("sub_hit")),
                "matched": _matched, "matchedTrio": _matched_t, "liveQuinellas": _live_q,
                "judged": _judged, "noRec": _norec,'''
    if s.count(old_c) != 1:
        fail("app.py 패치C 지점 불일치(%d)" % s.count(old_c))
    s = s.replace(old_c, new_c)

    old_d = '''            _lx0 = _live_exact_hit(rk, [_r0.get("1st"), _r0.get("2nd"), _r0.get("3rd")], doc.get("horse_count"))
            hit = (bool(_lx0["hit"] or _lx0["trioHit"]) if _lx0 is not None else bool(ra.get("main_hit")))'''
    new_d = '''            _lx0 = _live_exact_hit(rk, [_r0.get("1st"), _r0.get("2nd"), _r0.get("3rd")], doc.get("horse_count"))
            if _lx0 is not None and _lx0.get("noRec"):
                races -= 1   # [추천 미형성] 마감 전 추천이 없던 경주 — 집계 제외(판정 대상 아님)
                continue
            hit = (bool(_lx0["hit"] or _lx0["trioHit"]) if _lx0 is not None else bool(ra.get("main_hit")))'''
    if s.count(old_d) != 1:
        fail("app.py 패치D 지점 불일치(%d)" % s.count(old_d))
    s = s.replace(old_d, new_d)

    shutil.copy2(APP_PY, APP_PY + ".bak-판정제외")
    write(APP_PY, s)
    try:
        py_compile.compile(APP_PY, doraise=True)
    except Exception as e:
        shutil.copy2(APP_PY + ".bak-판정제외", APP_PY)
        fail("app.py 문법 오류 → 백업 복원됨: %s" % e)
    print("✅ app.py 패치 완료(문법 검증 통과)")

# ══════════ ② static/js/app.js ══════════
j = read(APP_JS)
if "rs.noRec" in j:
    print("↷ app.js 는 이미 패치됨 — 건너뜀")
else:
    # (1) 복기 블록 전체 교체 — 시작/끝 앵커 사이 스팬 치환(내용 무관·안전)
    a1 = "  // [카드 적중 표시·상세 대조 (2026-07-19)] 결과가 있으면 상세 맨 위에"
    a2 = "  // [오늘 성적 요약 (2026-07-19)] 경륜 탭 상단"
    i1, i2 = j.find(a1), j.find(a2)
    if i1 < 0 or i2 < 0 or i2 <= i1:
        fail("app.js 복기 블록 앵커를 찾지 못함")
    NEW_COMPARE = '''  // [카드 적중 표시·상세 대조 (2026-07-19)] 결과가 있으면 상세 맨 위에 '결과 vs 우리 추천' 복기 블록
  //   [판정-표시 일치] 복기 행 = 적중 판정에 실제 사용된 라이브 추천(rs.liveQuinellas) 우선 —
  //   현재 corePicks(마감 후 재계산본)와 판정(로그 기준)이 다른 조합을 보여주던 불일치 해소(모리오카 3R).
  function _renderResultCompare(a) {
    const rs = a && a.raceResult;
    if (!rs || !rs.top3 || rs.top3[0] == null) return '';
    const t3 = rs.top3.filter((x) => x != null);
    const winSet = t3.slice(0, 2).map(Number).sort((x, y) => x - y).join('+');
    const CIRC = ['①', '②', '③', '④'];
    if (rs.noRec) {
      // [추천 미형성] 마감 전 확정 추천이 없던 경주 — 판정 제외(✅/❌ 아님)
      return `<div style="margin:4px 0 8px;padding:12px 14px;border:3px solid #64748b;border-radius:12px;background:rgba(100,116,139,.10)">
        <div style="font-size:16px;font-weight:900;color:#cbd5e1">➖ 판정 제외 — 📊 결과 ${t3.join('→')} · 정답 복승 ${winSet}${rs.quinellaOdds ? ` (${rs.quinellaOdds}배)` : ''}</div>
        <div style="font-size:14px;color:#94a3b8;margin-top:4px">마감 전 확정 추천이 형성되지 않았던 경주입니다(수집 시작 지연·신호 미형성) — 적중/미적중 어느 쪽으로도 집계하지 않습니다.</div>
      </div>`;
    }
    const anyHit = !!(rs.hit || rs.trioHit);
    let qRows = '';
    if (rs.judged === 'live' && rs.liveQuinellas && rs.liveQuinellas.length) {
      qRows = rs.liveQuinellas.map((ck, i) => {
        const ok = ck === winSet;
        return `<div style="font-size:16px;font-weight:800;margin:3px 0;color:${ok ? '#7fd14f' : '#9ca3af'}">복승 ${CIRC[i] || (i + 1)} ${esc(ck)} ${ok ? '✅ 적중!' : '❌'}</div>`;
      }).join('');
    } else {
      const fq = (((a.corePicks || {}).finalQuinellas) || []).slice(0, 3);
      qRows = fq.length ? fq.map((q, i) => {
        const ck = (q.combo || []).map(Number).sort((x, y) => x - y).join('+');
        const ok = ck === winSet;
        return `<div style="font-size:16px;font-weight:800;margin:3px 0;color:${ok ? '#7fd14f' : '#9ca3af'}">복승 ${CIRC[i] || (i + 1)} ${esc(ck)} ${ok ? '✅ 적중!' : '❌'}</div>`;
      }).join('') : (rs.recommend ? `<div style="font-size:16px;font-weight:800;margin:3px 0;color:${rs.hit ? '#7fd14f' : '#9ca3af'}">복승 ${esc(Array.isArray(rs.recommend) ? rs.recommend.join('+') : String(rs.recommend))} ${rs.hit ? '✅ 적중!' : '❌'}</div>` : '');
    }
    const trioRow = rs.trioHit ? `<div style="font-size:16px;font-weight:800;margin:3px 0;color:#7fd14f">삼복승 ${esc(rs.matchedTrio || '')} ✅ 적중!(1·2·3착 일치)</div>` : '';
    const why = anyHit
      ? `<div style="font-size:14px;color:#a7f3d0;margin-top:4px">💡 ${esc(rs.hit ? ('복승 ' + (rs.matched || '') + ' = 1·2착 정확 일치') : ('삼복승 ' + (rs.matchedTrio || '') + ' = 1·2·3착 정확 일치'))}</div>`
      : `<div style="font-size:14px;color:#fca5a5;margin-top:4px">💡 정답 복승 ${esc(winSet)}${rs.missReason ? ` — ${esc(rs.missReason)}` : ' — 추천과 차이'}</div>`;
    return `<div style="margin:4px 0 8px;padding:12px 14px;border:3px solid ${anyHit ? '#3B6D11' : '#6b7280'};border-radius:12px;background:${anyHit ? 'rgba(59,109,17,.14)' : 'rgba(107,114,128,.10)'}">
      <div style="font-size:16px;font-weight:900;color:${anyHit ? '#7fd14f' : '#cbd5e1'}">${anyHit ? '✅ 적중!' : '❌ 미적중'} — 📊 결과 ${t3.join('→')}${rs.quinellaOdds ? ` · 복승 ${winSet} (${rs.quinellaOdds}배)` : ` · 복승 ${winSet}`}</div>
      <div style="margin-top:6px">${qRows}${trioRow}</div>
      ${why}
    </div>`;
  }

'''
    j = j[:i1] + NEW_COMPARE + j[i2:]

    # (2) 카드 배지 블록 — noRec 중립 표기 추가(스팬 치환)
    b1 = "    // [카드 적중 표시 (2026-07-19)] 결과가 있으면 ✅적중(초록 #3B6D11)/❌미적중(회색) 테두리+배지+착순 한 줄"
    b2 = "    return `<div data-mkey="
    i1, i2 = j.find(b1), j.find(b2)
    if i1 < 0 or i2 < 0 or i2 <= i1:
        fail("app.js 카드 배지 앵커를 찾지 못함")
    NEW_CARD = '''    // [카드 적중 표시 (2026-07-19)] 결과가 있으면 ✅적중(초록 #3B6D11)/❌미적중(회색) 테두리+배지+착순 한 줄
    const rs = c.result;
    let cardBorder = `${borderW} solid ${mh.length ? '#f0abfc' : col}`;
    let cardBg = mh.length ? 'rgba(240,171,252,.08)' : bg;
    let resBadge = '', resLine = '';
    if (rs && rs.top3 && rs.top3[0] != null) {
      if (rs.noRec) {
        // [추천 미형성] 마감 전 확정 추천 없음 — 판정 제외(중립 표기·✅/❌ 아님)
        cardBorder = '2px solid #64748b';
        cardBg = 'rgba(100,116,139,.10)';
        resBadge = '<span style="background:#64748b;color:#fff;font-weight:800;font-size:11px;padding:1px 6px;border-radius:5px">➖ 추천 미형성</span>';
        resLine = `<div style="margin:3px 0;font-size:12px;font-weight:800;color:#94a3b8">📊 결과 ${rs.top3.filter((x) => x != null).join('→')} · 마감 전 추천 미형성(판정 제외)</div>`;
      } else {
        // [적중 표시 정직화 (2026-07-19)] ✅ = 라이브 표시 추천의 '정확' 적중(복승=1·2착 / 삼복승=1·2·3착)만.
        const anyHit = !!(rs.hit || rs.trioHit);
        const hitTag = rs.hit && rs.trioHit ? `✅ 복승 ${esc(rs.matched || '')}·삼복승 적중!`
          : rs.hit ? `✅ 복승 ${esc(rs.matched || '')} 적중!`
            : rs.trioHit ? `✅ 삼복승 ${esc(rs.matchedTrio || '')} 적중!` : '❌ 미적중';
        cardBorder = anyHit ? '3px solid #3B6D11' : '2px solid #6b7280';
        cardBg = anyHit ? 'rgba(59,109,17,.14)' : 'rgba(107,114,128,.10)';
        resBadge = anyHit
          ? '<span style="background:#3B6D11;color:#fff;font-weight:800;font-size:11px;padding:1px 6px;border-radius:5px">✅ 적중!</span>'
          : '<span style="background:#6b7280;color:#fff;font-weight:800;font-size:11px;padding:1px 6px;border-radius:5px">❌ 미적중</span>';
        const recTxt = (rs.liveQuinellas && rs.liveQuinellas.length)
          ? rs.liveQuinellas.join('·')
          : (Array.isArray(rs.recommend) ? rs.recommend.join('+') : (rs.recommend || ''));
        resLine = `<div style="margin:3px 0;font-size:12px;font-weight:800;color:${anyHit ? '#7fd14f' : '#9ca3af'}">📊 결과 ${rs.top3.filter((x) => x != null).join('→')} · ${hitTag}${recTxt ? ` · 추천 ${esc(recTxt)}` : ''}${anyHit && rs.quinellaOdds ? ` (${rs.quinellaOdds}배)` : ''}</div>`;
      }
    }
'''
    j = j[:i1] + NEW_CARD + j[i2:]

    shutil.copy2(APP_JS, APP_JS + ".bak-판정제외")
    write(APP_JS, j)
    # node 가 있으면 문법 검증(없으면 생략)
    try:
        import subprocess
        r = subprocess.run(["node", "--check", APP_JS], capture_output=True, text=True)
        if r.returncode != 0:
            shutil.copy2(APP_JS + ".bak-판정제외", APP_JS)
            fail("app.js 문법 오류 → 백업 복원됨: %s" % (r.stderr or "")[:300])
        print("✅ app.js 패치 완료(node --check 통과)")
    except FileNotFoundError:
        print("✅ app.js 패치 완료(node 미설치 — 문법 검증 생략)")

print("")
print("🎉 패치 완료! 다음을 진행하세요:")
print("  1. 서버 재시작")
print("  2. 브라우저 Ctrl+F5")
print("  3. 모리오카 3경주 카드 → '➖ 추천 미형성(판정 제외)' 로 표시되면 정상")
