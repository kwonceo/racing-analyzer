/* =========================================================================
 * overlay.js — 배당판 실시간 분석 오버레이 (완전 격리 · 읽기 전용)
 * -------------------------------------------------------------------------
 * ⚠️ 안전 원칙 (기존 수집 기능 절대 영향 금지):
 *   1) content.js(수집 엔진)와 별도 파일 → 수집 로직 바이트 단위 무변경.
 *   2) DOM은 오직 <div>/<span>/<button> 만 주입한다.
 *      · 절대 금지: <table>/<tr>/<td>/<th>/<li>/<dl>  (수집이 querySelectorAll('table')/tr 로 스캔)
 *      · 절대 금지 class/id 문자열: odds, odd_, result, payout, haraimodoshi, chaku
 *      → 이 규칙으로 수집 선택자가 오버레이를 절대 잡지 못한다.
 *   3) id/class 네임스페이스는 kbOv* (timer.js 의 kbTimer* 와도 충돌 안 함).
 *   4) 모든 로직을 try-catch 로 감싼다 → 오버레이 예외가 수집/페이지로 전파 불가.
 *   5) 데이터는 chrome.storage(analyzeStatus/collectAlert/timerDeadline) '읽기'만.
 *      → 신규 배당 수집·탭 클릭·서버 호출 전혀 없음(성능/부하 0에 가까움).
 *   6) 롤백: overlayEnabled(패널 ON/OFF) · overlayKill(전체 즉시 비활성) 2단 토글.
 *      · 문제 시 콘솔에서 chrome.storage.local.set({overlayKill:true}) → 오버레이 완전 제거.
 * =======================================================================*/
(function () {
  'use strict';
  try {
    var ID_CHIP = 'kbOvToggle', ID_PANEL = 'kbOvPanel';
    var enabled = false, killed = false, timer = null;
    var savedPos = null;   // [보완#2] 사용자가 드래그해 옮긴 패널 위치({left,top}) — chrome.storage 에 저장/복원
    var soundOn = false, lastSoundKey = '';   // [보완#3] 강조 팝업 알림음 옵션(기본 off) + 중복 방지

    // [보완#3] 중요 신호 강조 알림음 — 짧은 2단 삑(Web Audio). 실패는 무시(무해).
    function beep() {
      try {
        var Ctx = window.AudioContext || window.webkitAudioContext; if (!Ctx) return;
        var ac = beep._ac || (beep._ac = new Ctx());
        if (ac.state === 'suspended') { try { ac.resume(); } catch (_) { /* */ } }
        var t = ac.currentTime;
        function tone(freq, start, dur) {
          var o = ac.createOscillator(), g = ac.createGain();
          o.type = 'sine'; o.frequency.value = freq; g.gain.value = 0.07;
          o.connect(g); g.connect(ac.destination);
          o.start(t + start); o.stop(t + start + dur);
        }
        tone(880, 0, 0.16); tone(1180, 0.2, 0.18);
      } catch (_) { /* */ }
    }

    // 안전한 요소 생성 헬퍼 (div/span/button 만 사용)
    function mk(tag, css, text) {
      var e = document.createElement(tag);
      if (css) e.style.cssText = css;
      if (text != null) e.textContent = text;   // textContent = HTML 주입 없음(XSS/오염 방지)
      return e;
    }
    function byId(id) { try { return document.getElementById(id); } catch (_) { return null; } }
    function root() { return document.body || document.documentElement; }

    function removeAll() {
      try { var a = byId(ID_CHIP); if (a) a.remove(); } catch (_) { /* */ }
      try { var b = byId(ID_PANEL); if (b) b.remove(); } catch (_) { /* */ }
      try { var c = byId('kbOvAlert'); if (c) c.remove(); } catch (_) { /* */ }   // [강조] 팝업 제거
      try { stopBlink(); } catch (_) { /* */ }
      try { stopCdBlink(); } catch (_) { /* */ }   // [3번] 카운트다운 깜빡임 정리
      if (timer) { try { clearInterval(timer); } catch (_) { /* */ } timer = null; }
    }

    // 발주까지 남은 시간 텍스트
    function countdown(deadline) {
      if (!deadline) return '';
      var ms = deadline - Date.now();
      if (ms <= 0) return '마감';
      var s = Math.round(ms / 1000);
      var m = Math.floor(s / 60);
      var ss = String(s % 60).padStart(2, '0');
      return m + '분 ' + ss + '초';
    }

    // [보완#2] 저장된 위치를 패널에 적용(없으면 기본 우측상단 유지)
    function applyPos(panel) {
      try {
        if (savedPos && typeof savedPos.left === 'number' && typeof savedPos.top === 'number') {
          panel.style.left = savedPos.left + 'px';
          panel.style.top = savedPos.top + 'px';
          panel.style.right = 'auto';
        }
      } catch (_) { /* */ }
    }

    // [보완#2] 헤더 드래그로 패널 이동 + 종료 시 위치 저장. (닫기 ✕ 버튼 클릭은 드래그 제외)
    function startDrag(e) {
      try {
        if (e.target && e.target.tagName === 'BUTTON') return;   // ✕ 버튼은 드래그 아님
        e.preventDefault();
        var panel = byId(ID_PANEL);
        if (!panel) return;
        var rect = panel.getBoundingClientRect();
        var offX = e.clientX - rect.left, offY = e.clientY - rect.top;
        function move(ev) {
          try {
            var left = Math.max(0, Math.min((window.innerWidth || 1200) - 40, ev.clientX - offX));
            var top = Math.max(0, Math.min((window.innerHeight || 800) - 20, ev.clientY - offY));
            panel.style.left = left + 'px';
            panel.style.top = top + 'px';
            panel.style.right = 'auto';
            savedPos = { left: left, top: top };
          } catch (_) { /* */ }
        }
        function up() {
          try { document.removeEventListener('mousemove', move); } catch (_) { /* */ }
          try { document.removeEventListener('mouseup', up); } catch (_) { /* */ }
          try { chrome.storage.local.set({ overlayPos: savedPos }); } catch (_) { /* */ }
        }
        document.addEventListener('mousemove', move);
        document.addEventListener('mouseup', up);
      } catch (_) { /* 드래그 실패는 무시 */ }
    }

    // chrome.storage 읽기(오류 시 빈 객체)
    function readData() {
      return new Promise(function (resolve) {
        try {
          chrome.storage.local.get({ analyzeStatus: null, timerDeadline: 0, collectAlert: null }, function (v) {
            resolve(v || {});
          });
        } catch (_) { resolve({}); }
      });
    }

    // 작은 런처 칩(항상 표시 · 클릭 시 패널 토글)
    function ensureChip() {
      if (byId(ID_CHIP)) return;
      var chip = mk('div',
        'position:fixed;right:12px;top:64px;z-index:2147483000;' +
        'background:#8b5cf6;color:#fff;font:700 12px/1 -apple-system,BlinkMacSystemFont,sans-serif;' +
        'padding:6px 10px;border-radius:14px;cursor:pointer;box-shadow:0 2px 6px rgba(0,0,0,.35);' +
        'user-select:none;letter-spacing:.2px', '📊 분석');
      chip.id = ID_CHIP;
      chip.title = '배당판 실시간 분석 오버레이 켜기/끄기';
      chip.addEventListener('click', function () {
        enabled = !enabled;
        try { chrome.storage.local.set({ overlayEnabled: enabled }); } catch (_) { /* */ }
        render();
      });
      root().appendChild(chip);
    }

    var PANEL_CSS =
      'position:fixed;right:12px;top:96px;z-index:2147482900;width:252px;max-height:70vh;overflow:auto;' +
      'background:rgba(17,24,39,.96);color:#e5e7eb;border:1px solid #4c1d95;border-radius:10px;' +
      'padding:10px 11px;font:500 12px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;' +
      'box-shadow:0 6px 20px rgba(0,0,0,.4)';

    // ── [강화] 종목 라벨 + 등급 색 ─────────────────────────────────────
    var CAT_LABEL = { boat: '🚤 경정', cycle: '🚴 경륜', bike: '🏍 바이크',
      japan_central: '🏇 중앙', japan_local: '🇯🇵 지방', korea: '🇰🇷 한국' };
    var GRADE_COLOR = { A: '#38d39f', B: '#4ea1ff', C: '#fbbf24', D: '#94a3b8' };

    // [2번] 배당 수집된 마번 집합(validHorses) — 없으면 null(필터 안 함)
    function validSet(d) {
      try {
        return (d && Array.isArray(d.validHorses) && d.validHorses.length) ? new Set(d.validHorses.map(Number)) : null;
      } catch (_) { return null; }
    }

    // [강화] 통합 등급 상위 3두(전적등급 + 배당유력마 병합) — vanilla 버전
    function topGrades(d) {
      try {
        // [2번] 배당 없는 잔존마(이전 경주 말)는 제외 — 6두 경주면 1~6번만
        var vset = validSet(d);
        var inV = function (n) { return !vset || vset.has(Number(n)); };
        var form = (d.form || []).filter(function (h) { return inV(h.no); });
        var keys = (d.keyHorses || []).map(Number).filter(inV);
        var anom = d.anomalyHorse != null ? +d.anomalyHorse : null;
        var fmap = {}; form.forEach(function (h) { fmap[h.no] = h; });
        var nos = {}; form.forEach(function (h) { nos[h.no] = 1; }); keys.forEach(function (n) { nos[n] = 1; });
        var arr = Object.keys(nos).map(function (n) {
          n = +n; var f = fmap[n] || null;
          return { no: n, grade: f ? f.grade : null, score: f ? f.totalScore : null,
            key: keys.indexOf(n) >= 0, anom: anom === n };
        });
        arr.sort(function (a, b) { return (b.key - a.key) || ((b.score || 0) - (a.score || 0)) || (a.no - b.no); });
        return arr.slice(0, 3);
      } catch (_) { return []; }
    }

    // [강화] BMED 저배당 배분(보험형 combos 우선 → plan 폴백)
    function bmedRows(d) {
      try {
        var b = d.bmed; if (!b) return null;
        var ins = b.insurance || {};
        if (ins.active && ins.combos && ins.combos.length) {
          return { band: ins.band, six: !!b.sixRacer, anchor: ins.anchor,
            rows: ins.combos.map(function (c) { return { combo: c.combo, odds: c.odds, ratio: c.ratio, preserved: c.preserved }; }) };
        }
        var plan = b.plan || [];
        return { band: null, six: !!b.sixRacer, strategy: b.strategy,
          rows: plan.slice(0, 3).map(function (p) { return { combo: p.combo, odds: p.odds, ratio: p.ratio }; }) };
      } catch (_) { return null; }
    }

    // ── [강조 팝업] 중요 신호를 큰 팝업으로 강조(역배열·강한급락·경고·마감임박) ──
    var ID_ALERT = 'kbOvAlert';
    var alertBlink = null, blinkOn = false, alertDismissed = '';

    // 현재 가장 중요한 신호 1건 산출(우선순위: 역배열 > 강한급락 > 경고 > 마감임박)
    function computeCritical(d, deadline) {
      try {
        if (d && d.inverse && d.inverse.detected && (d.inverse.invDetail || []).length) {
          // [역배열 정확화] 인기순위(단승/복승) vs 쌍승 배당순위 비교 → 2단계+ 역전 말만 표시.
          var det = d.inverse.invDetail.slice(0, 3);
          var lead = d.inverse.invLead;
          var lines = det.map(function (x) {
            return '인기' + x.popRank + '위 ' + x.no + '번 · 쌍승 ' + x.odds + '배' + (x.lowest ? ' ← 낮음' : '');
          });
          if (lead && lead.vs) {
            lines.push('인기' + lead.vs.popRank + '위 ' + lead.vs.no + '번 · 쌍승 ' + lead.vs.odds + '배');
            lines.push('→ 인기 ' + lead.popRank + '위가 ' + lead.vs.popRank + '위보다 배당 낮음');
          }
          if (lead) lines.push('→ ' + lead.no + '번 실질 유력!');
          var g = (lead && lead.gap) ? lead.gap : 2;   // 역전폭 → 강도
          lines.push('강도: ' + (g >= 3 ? '🔴 압도적 역배열' : '🟠 강한 역배열'));
          var ihKey = det.map(function (x) { return x.no; }).join('·');
          return { key: 'inv:' + ihKey + ':' + (lead ? lead.odds : ''), level: 'red', icon: '🔄', title: '역배열 감지',
            lines: lines, msg: '실질 유력마 ' + ((lead && lead.no) || ihKey) + '번 — 인기순위와 쌍승순위 역전' };
        }
        var strong = ((d && d.drops) || []).filter(function (x) { return x && x.pct <= -50 && x.combo; })
          .sort(function (a, b) { return a.pct - b.pct; });
        if (strong.length) {
          var s0 = strong[0];
          return { key: 'drop:' + s0.combo.join('+') + ':' + s0.pct, level: 'red', icon: '🔴', title: '강한 급락',
            msg: s0.combo[0] + '+' + s0.combo[1] + ' ▼' + Math.abs(s0.pct) + '% — 자금 집중' };
        }
        if (d && d.alertSignal && (d.alertSignal.horses || []).length) {
          var hs = d.alertSignal.horses.join('+');
          return { key: 'alert:' + hs, level: 'orange', icon: '⚠️', title: '경고 신호',
            msg: hs + '번 배당 급변 — 추천 포함 권장' };
        }
        if (deadline) {
          var ms = deadline - Date.now();
          if (ms > 0 && ms <= 60000) return { key: 'close', level: 'orange', icon: '⏰', title: '마감 임박',
            msg: '발주까지 1분 이내 — 베팅 마감 준비' };
        }
      } catch (_) { /* */ }
      return null;
    }

    // [3번] 마감 T-30초 카운트다운 깜빡임(빨강↔반투명) — 패널의 kbOvCd 행 전용
    var cdBlink = null, cdBlinkOn = false;
    function stopCdBlink() { if (cdBlink) { try { clearInterval(cdBlink); } catch (_) { /* */ } cdBlink = null; } }
    function startCdBlink() {
      if (cdBlink) return;
      cdBlink = setInterval(function () {
        try {
          var el = byId('kbOvCd'); if (!el) { stopCdBlink(); return; }
          cdBlinkOn = !cdBlinkOn;
          el.style.background = cdBlinkOn ? 'rgba(220,38,38,.55)' : 'rgba(220,38,38,.18)';
          el.style.boxShadow = cdBlinkOn ? '0 0 0 2px rgba(248,113,113,.7)' : 'none';
        } catch (_) { /* */ }
      }, 500);
    }

    function stopBlink() { if (alertBlink) { try { clearInterval(alertBlink); } catch (_) { /* */ } alertBlink = null; } }
    function startBlink() {
      if (alertBlink) return;
      alertBlink = setInterval(function () {
        try {
          var el = byId(ID_ALERT); if (!el) { stopBlink(); return; }
          blinkOn = !blinkOn;
          el.style.boxShadow = blinkOn
            ? '0 0 0 4px rgba(255,255,255,.55), 0 8px 24px rgba(0,0,0,.55)'
            : '0 8px 24px rgba(0,0,0,.55)';
        } catch (_) { /* */ }
      }, 550);
    }

    // 강조 팝업 렌더(중요 신호 있을 때만 · 상단 중앙 · 깜빡임 · ✕로 이 신호 닫기)
    function renderAlertPopup(crit) {
      try {
        var el = byId(ID_ALERT);
        if (!crit || !enabled || killed || alertDismissed === crit.key) {
          if (el) el.remove(); stopBlink();
          if (!crit) lastSoundKey = '';   // [보완#3] 신호 해제 → 다음 신호에 다시 알림음
          return;
        }
        // [보완#3] 새 중요 신호(키 변경) + 소리 옵션 ON → 알림음 1회
        if (soundOn && crit.key !== lastSoundKey) { beep(); }
        lastSoundKey = crit.key;
        var bg = crit.level === 'red'
          ? 'linear-gradient(135deg,#dc2626,#991b1b)' : 'linear-gradient(135deg,#f59e0b,#b45309)';
        if (!el) {
          el = mk('div',
            'position:fixed;left:50%;margin-left:-168px;top:58px;z-index:2147483100;width:336px;' +
            'color:#fff;border:2px solid rgba(255,255,255,.85);border-radius:12px;' +
            'padding:11px 14px;font:600 13px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;' +
            'box-shadow:0 8px 24px rgba(0,0,0,.55)');
          el.id = ID_ALERT;
          root().appendChild(el);
        }
        el.style.background = bg;
        while (el.firstChild) el.removeChild(el.firstChild);
        var head = mk('div', 'display:flex;align-items:center;justify-content:space-between;gap:8px');
        head.appendChild(mk('span', 'font-weight:900;font-size:15px', crit.icon + ' ' + crit.title));
        var ctrls = mk('div', 'display:flex;align-items:center;gap:6px');
        // [보완#3] 알림음 ON/OFF 토글(🔔/🔕)
        var snd = mk('button', 'all:unset;cursor:pointer;color:#fff;font-size:15px;padding:0 2px', soundOn ? '🔔' : '🔕');
        snd.title = soundOn ? '알림음 끄기' : '알림음 켜기';
        snd.addEventListener('click', function () {
          soundOn = !soundOn;
          try { chrome.storage.local.set({ overlaySound: soundOn }); } catch (_) { /* */ }
          if (soundOn) beep();   // 켤 때 확인음 + 오디오 컨텍스트 활성화(사용자 제스처)
          var s2 = byId(ID_ALERT); if (s2 && crit) renderAlertPopup(crit);
        });
        ctrls.appendChild(snd);
        var x = mk('button', 'all:unset;cursor:pointer;color:#fff;font:900 16px sans-serif;padding:0 2px', '✕');
        x.title = '이 알림 닫기(다른 신호가 오면 다시 표시)';
        x.addEventListener('click', function () {
          alertDismissed = crit.key;
          var e2 = byId(ID_ALERT); if (e2) e2.remove(); stopBlink();
        });
        ctrls.appendChild(x);
        head.appendChild(ctrls);
        el.appendChild(head);
        // [역배열 상세] crit.lines 있으면 여러 줄로(마번·인기순위·쌍승배당·강도), 없으면 단일 msg
        if (crit.lines && crit.lines.length) {
          var box = mk('div', 'margin-top:5px;font-size:13px;font-weight:700;line-height:1.5');
          crit.lines.forEach(function (ln) {
            var sep = (ln.indexOf('→') === 0 || ln.indexOf('강도:') === 0);
            box.appendChild(mk('div', sep ? 'margin-top:4px;font-weight:800' : '', ln));
          });
          el.appendChild(box);
        } else {
          el.appendChild(mk('div', 'margin-top:4px;font-size:13px;font-weight:700', crit.msg));
        }
        startBlink();
      } catch (_) { /* 강조 팝업 실패는 무시 */ }
    }

    // 패널 내용 갱신 (div/span 만 사용 · textContent 기반)
    function updatePanel(panel, st) {
      try {
        while (panel.firstChild) panel.removeChild(panel.firstChild);
        var d = (st.analyzeStatus && st.analyzeStatus.data) || null;
        var deadline = st.timerDeadline || 0;

        // 헤더 + 종목 배지 + 닫기(✕) — [보완#2] 헤더를 잡고 드래그하면 패널 이동
        var head = mk('div', 'display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;cursor:move');
        head.title = '드래그하여 위치 이동';
        head.addEventListener('mousedown', startDrag);
        var hL = mk('div', 'display:flex;align-items:center;gap:6px');
        hL.appendChild(mk('span', 'font-weight:800;color:#c4b5fd', '📊 실시간 분석'));
        var cat = d && d.category && CAT_LABEL[d.category];
        if (cat) hL.appendChild(mk('span', 'font-size:10px;font-weight:700;color:#c4b5fd;border:1px solid #6d28d9;border-radius:8px;padding:1px 6px', cat));
        head.appendChild(hL);
        var x = mk('button', 'all:unset;cursor:pointer;color:#94a3b8;font:700 14px sans-serif;padding:0 2px', '✕');
        x.title = '오버레이 끄기';
        x.addEventListener('click', function () {
          enabled = false;
          try { chrome.storage.local.set({ overlayEnabled: false }); } catch (_) { /* */ }
          render();
        });
        head.appendChild(x);
        panel.appendChild(head);

        // [강조] 중요 신호 팝업(상단 중앙) 갱신 + 패널 상단 배너
        var crit = computeCritical(d, deadline);
        renderAlertPopup(crit);
        if (crit) {
          var bnBg = crit.level === 'red' ? 'rgba(220,38,38,.22)' : 'rgba(245,158,11,.2)';
          var bnBd = crit.level === 'red' ? '#f87171' : '#fbbf24';
          var bn = mk('div', 'margin:2px 0 7px;padding:6px 8px;border-left:3px solid ' + bnBd + ';background:' + bnBg + ';border-radius:6px');
          bn.appendChild(mk('div', 'font-weight:800;color:' + bnBd, crit.icon + ' ' + crit.title));
          bn.appendChild(mk('div', 'color:#e5e7eb;font-size:11px;margin-top:1px', crit.msg));
          panel.appendChild(bn);
        }

        // 마감 카운트다운 — [3번] T-2분 주황 · T-1분 빨강 · T-30초 깜빡임
        var cd = countdown(deadline);
        if (cd) {
          var leftMs = deadline ? (deadline - Date.now()) : 0;
          var phase = (leftMs > 0 && leftMs <= 30000) ? 'blink'
            : (leftMs > 0 && leftMs <= 60000) ? 'red'
            : (leftMs > 0 && leftMs <= 120000) ? 'orange' : '';
          var isRed = (phase === 'red' || phase === 'blink');
          var cdBg = isRed ? 'rgba(220,38,38,.28)' : (phase === 'orange' ? 'rgba(245,158,11,.24)' : '');
          var cdBd = isRed ? '#f87171' : (phase === 'orange' ? '#fbbf24' : '');
          var cdRow = mk('div', 'margin:2px 0 6px;' + (phase ? 'padding:5px 8px;border-radius:6px;' : '') +
            (cdBg ? ('background:' + cdBg + ';') : '') + (cdBd ? ('border:1px solid ' + cdBd + ';') : ''));
          cdRow.id = 'kbOvCd';
          cdRow.appendChild(mk('span', 'color:#94a3b8', '⏰ 마감까지 '));
          cdRow.appendChild(mk('span', 'font-weight:800;color:' + (cd === '마감' ? '#f87171' : (cdBd || '#fbbf24')), cd));
          if (phase === 'orange') cdRow.appendChild(mk('span', 'margin-left:6px;font-size:11px;font-weight:700;color:#fbbf24', 'T-2분'));
          if (phase === 'red') cdRow.appendChild(mk('span', 'margin-left:6px;font-size:11px;font-weight:800;color:#fecaca', 'T-1분 마감 임박!'));
          if (phase === 'blink') cdRow.appendChild(mk('span', 'margin-left:6px;font-size:11px;font-weight:800;color:#fecaca', '⚡ 30초!'));
          panel.appendChild(cdRow);
          if (phase === 'blink') startCdBlink(); else stopCdBlink();   // T-30초 깜빡임
        } else { stopCdBlink(); }

        if (!d) {
          panel.appendChild(mk('div', 'color:#94a3b8', '분석 대기 중 — 배당 수집·이상감지가 실행되면 표시됩니다.'));
          return;
        }

        // 유력마 — [2번] 배당 없는 잔존마 제외
        var vset = validSet(d);
        var inV = function (n) { return !vset || vset.has(Number(n)); };
        var keys = (d.keyHorses || []).filter(inV);
        if (keys.length) {
          var kr = mk('div', 'margin:3px 0');
          kr.appendChild(mk('span', 'color:#94a3b8', '⭐ 유력마 '));
          kr.appendChild(mk('span', 'font-weight:700;color:#4ea1ff', keys.join(' · ')));
          panel.appendChild(kr);
        }

        // [3번] 쌍승 역배열 요약 — "🔄 역배열: N번 주목" (상세 블록은 아래 별도 유지)
        if (d.inverse && d.inverse.detected && d.inverse.invLead && d.inverse.invLead.no != null) {
          var ivs = mk('div', 'margin:3px 0;font-weight:800;color:#f0abfc');
          ivs.textContent = '🔄 역배열: ' + d.inverse.invLead.no + '번 주목';
          panel.appendChild(ivs);
        }

        // [3번] 단승 인기 순위 top3 — "1위 4번 (3.2배)"
        var sroll = (d.singleRanking || []).filter(inV);
        var smap = d.single || {};
        if (sroll.length) {
          panel.appendChild(mk('div', 'margin:5px 0 2px;color:#94a3b8', '🥇 단승 인기'));
          sroll.slice(0, 3).forEach(function (no, i) {
            var od = smap[no] != null ? smap[no] : smap[String(no)];
            var row = mk('div', 'margin:1px 0');
            row.appendChild(mk('span', 'color:#94a3b8', (i + 1) + '위 '));
            row.appendChild(mk('span', 'font-weight:800;color:#4ea1ff', no + '번'));
            if (od != null) row.appendChild(mk('span', 'margin-left:5px;color:#e5e7eb', '(' + od + '배)'));
            panel.appendChild(row);
          });
        }

        // [3번] 환수율 + 자금 집중도 — advanced.overround(역수합=invSum, 상위3조합 집중)
        var ov = d.signalQuality && d.signalQuality.advanced && d.signalQuality.advanced.overround;
        if (ov && ov.invSum) {
          var refund = ov.invSum > 1 ? (1 / ov.invSum) : ov.invSum;   // 환수율 = 1/역수합(캡 100%)
          var refundPct = Math.round(Math.min(1, refund) * 100);
          var share = ov.top3Share != null ? Math.round(ov.top3Share * 100) : null;
          var conc = !!ov.concentrated;   // 상위3조합 90%+
          var rr = mk('div', 'margin:5px 0 2px;padding:4px 7px;border-radius:6px;background:' +
            (conc ? 'rgba(245,158,11,.18)' : 'rgba(148,163,184,.12)'));
          rr.appendChild(mk('span', 'color:#94a3b8', '💰 환수율 '));
          rr.appendChild(mk('span', 'font-weight:800;color:' + (conc ? '#fbbf24' : '#e5e7eb'), refundPct + '%'));
          rr.appendChild(mk('span', 'margin-left:6px;font-size:11px;color:' + (conc ? '#fbbf24' : '#94a3b8'),
            conc ? ('→ 특정 조합 집중!' + (share != null ? ' (상위3 ' + share + '%)' : ''))
                 : ('(자금 집중도 낮음' + (share != null ? ' · 상위3 ' + share + '%' : '') + ')')));
          panel.appendChild(rr);
        }

        // 급락 신호(상위 2개)
        var drops = (d.drops || []).filter(function (x2) { return x2 && x2.pct < 0 && x2.combo; })
          .sort(function (a, b) { return a.pct - b.pct; }).slice(0, 2);
        if (drops.length) {
          panel.appendChild(mk('div', 'margin:5px 0 2px;color:#94a3b8', '📉 급락'));
          drops.forEach(function (dr) {
            var row = mk('div', 'margin:1px 0');
            row.appendChild(mk('span', 'display:inline-block;background:#7f1d1d;color:#fecaca;border-radius:6px;padding:1px 6px;font-weight:700',
              (dr.combo[0]) + '+' + (dr.combo[1])));
            row.appendChild(mk('span', 'color:#94a3b8;margin-left:6px', '▼' + Math.abs(dr.pct) + '%'));
            panel.appendChild(row);
          });
        }

        // [강화] 역배열 감지 라인 — 인기순위 vs 쌍승 배당순위 역전(2단계+)
        if (d.inverse && d.inverse.detected && (d.inverse.invDetail || []).length) {
          var det2 = d.inverse.invDetail.slice(0, 3);
          var lead2 = d.inverse.invLead;
          var ivr = mk('div', 'margin:5px 0 2px;padding:4px 7px;background:rgba(168,85,247,.16);border-radius:6px');
          ivr.appendChild(mk('div', 'color:#c4b5fd;font-weight:700', '🔄 역배열 감지'));
          det2.forEach(function (x) {
            var r = mk('div', 'color:#e9d5ff;font-size:11px');
            r.textContent = '인기' + x.popRank + '위 ' + x.no + '번 · 쌍승 ' + x.odds + '배' + (x.lowest ? ' ← 낮음' : '');
            ivr.appendChild(r);
          });
          if (lead2 && lead2.vs) {
            var rv = mk('div', 'color:#e9d5ff;font-size:11px');
            rv.textContent = '인기' + lead2.vs.popRank + '위 ' + lead2.vs.no + '번 · 쌍승 ' + lead2.vs.odds + '배';
            ivr.appendChild(rv);
          }
          if (lead2) ivr.appendChild(mk('div', 'color:#f0abfc;font-size:11px;font-weight:700;margin-top:2px', '→ ' + lead2.no + '번 실질 유력!'));
          panel.appendChild(ivr);
        }
        // [신규] 전적 우수하나 시장 비인기 (역배열 아님) — 전적 좋은데 배당 안 붙은 말
        if (d.inverse && (d.inverse.strongUnpopular || []).length) {
          var su = d.inverse.strongUnpopular.slice(0, 2);
          var sur = mk('div', 'margin:5px 0 2px;padding:4px 7px;background:rgba(59,130,246,.14);border-radius:6px');
          sur.appendChild(mk('div', 'color:#93c5fd;font-weight:700', '📈 전적 우수·시장 비인기 (역배열 아님)'));
          su.forEach(function (h) {
            sur.appendChild(mk('div', 'color:#dbeafe;font-size:11px',
              h.no + '번 · 전적 ' + h.formScore + ' · 시장 ' + h.reprOdds + '배'));
          });
          panel.appendChild(sur);
        }

        // [강화] 통합 등급 상위 3두(전적 + 배당유력)
        var tg = topGrades(d);
        if (tg.length) {
          panel.appendChild(mk('div', 'margin:6px 0 2px;color:#94a3b8', '🎖️ 통합 등급'));
          tg.forEach(function (g) {
            var gr = mk('div', 'margin:1px 0');
            gr.appendChild(mk('span', 'font-weight:800;color:#4ea1ff', g.no + '번'));
            if (g.grade) gr.appendChild(mk('span', 'margin-left:5px;font-weight:700;color:' + (GRADE_COLOR[g.grade] || '#e5e7eb'), g.grade));
            var tagTxt = (g.score != null ? ('전적' + g.score) : '') +
              (g.key ? (g.score != null ? '·배당유력' : '배당유력') : '') +
              (g.anom ? ' 🚨' : '');
            if (tagTxt) gr.appendChild(mk('span', 'margin-left:5px;color:#94a3b8;font-size:11px', '(' + tagTxt + ')'));
            panel.appendChild(gr);
          });
        }

        // 추천 조합(복승 메인 우선, 없으면 첫 추천)
        var recs = (d.betRecommend || []);
        var main = null;
        for (var i = 0; i < recs.length; i++) { if (recs[i].label && recs[i].label.indexOf('복승 메인') === 0) { main = recs[i]; break; } }
        if (!main && recs.length) main = recs[0];
        if (main && main.combo) {
          panel.appendChild(mk('div', 'margin:6px 0 2px;color:#94a3b8', '🎯 추천'));
          var rr = mk('div', 'font-weight:800;color:#38d39f');
          rr.textContent = (main.label ? main.label + ' ' : '') + main.combo.join('+');
          panel.appendChild(rr);
        }

        // [강화] 🎲 삼복승 추천(trioRecommend 최상위 1건 · 실배당/추정 표기)
        var trios = (d.trioRecommend || []).filter(function (t) { return t && t.combo && t.combo.length === 3; });
        if (trios.length) {
          var t0 = trios[0];
          var od = (t0.expOdds != null) ? (t0.expOdds + '배')
            : (t0.expOddsEst != null ? ('추정 ' + t0.expOddsEst + '배') : '');
          panel.appendChild(mk('div', 'margin:6px 0 2px;color:#94a3b8', '🎲 삼복승'));
          var tr = mk('div', 'font-weight:800;color:#a78bfa');
          tr.textContent = t0.combo.join('+') + (od ? ('  ' + od) : '');
          panel.appendChild(tr);
        }

        // [강화] BMED 저배당 배분(보험형 combos 우선)
        var bm = bmedRows(d);
        if (bm && bm.rows && bm.rows.length) {
          var bmHead = mk('div', 'margin:7px 0 2px;color:#94a3b8');
          bmHead.textContent = '🛡️ BMED' + (bm.band ? (' ' + bm.band) : (bm.strategy ? (' ' + bm.strategy) : '')) + (bm.six ? ' · 6명' : '');
          panel.appendChild(bmHead);
          bm.rows.forEach(function (r) {
            var row = mk('div', 'margin:1px 0');
            row.appendChild(mk('span', 'display:inline-block;background:#4c1d95;color:#e9d5ff;border-radius:6px;padding:1px 6px;font-weight:700',
              r.combo[0] + '+' + r.combo[1]));
            if (r.odds != null) row.appendChild(mk('span', 'margin-left:5px;color:#94a3b8;font-size:11px', r.odds + '배'));
            if (r.ratio != null) row.appendChild(mk('span', 'margin-left:5px;font-weight:700;color:#c4b5fd', Math.round(r.ratio * 100) + '%'));
            if (r.preserved === true) row.appendChild(mk('span', 'margin-left:4px;color:#38d39f;font-size:11px', '✅'));
            else if (r.preserved === false) row.appendChild(mk('span', 'margin-left:4px;color:#f87171;font-size:11px', '❌'));
            panel.appendChild(row);
          });
        }

        // 최근 이상감지 알림(collectAlert)
        var al = st.collectAlert;
        if (al && al.text) {
          var ar = mk('div', 'margin-top:7px;padding-top:6px;border-top:1px solid #374151;color:#fbbf24;font-size:11px');
          ar.textContent = (al.level || '🟠') + ' ' + al.text;
          panel.appendChild(ar);
        }

        panel.appendChild(mk('div', 'margin-top:7px;color:#64748b;font-size:10px',
          '※ 읽기 전용 · 수집/베팅에 영향 없음'));
      } catch (_) { /* 패널 갱신 실패는 무시(수집/페이지 영향 없음) */ }
    }

    // 렌더(칩 + 패널 상태 반영)
    function render() {
      try {
        if (killed) { removeAll(); return; }
        ensureChip();
        var chip = byId(ID_CHIP);
        if (chip) chip.style.background = enabled ? '#38bdf8' : '#8b5cf6';
        var panel = byId(ID_PANEL);
        if (!enabled) {
          if (panel) panel.remove();
          try { var al = byId('kbOvAlert'); if (al) al.remove(); } catch (_) { /* */ }   // [강조] 팝업도 제거
          stopBlink();
          stopCdBlink();   // [3번] 카운트다운 깜빡임 정리
          if (timer) { clearInterval(timer); timer = null; }
          return;
        }
        if (!panel) { panel = mk('div', PANEL_CSS); panel.id = ID_PANEL; root().appendChild(panel); applyPos(panel); }
        readData().then(function (st) { var p = byId(ID_PANEL); if (p && enabled && !killed) updatePanel(p, st); });
        // 2초 주기 경량 갱신(카운트다운/데이터) — storage 읽기만
        if (!timer) {
          timer = setInterval(function () {
            try {
              if (!enabled || killed) { if (timer) { clearInterval(timer); timer = null; } return; }
              readData().then(function (st) { var p = byId(ID_PANEL); if (p && enabled && !killed) updatePanel(p, st); });
            } catch (_) { /* */ }
          }, 2000);
        }
      } catch (_) { /* 렌더 실패는 무시 */ }
    }

    // ── 초기화 ──────────────────────────────────────────────────────────
    try {
      chrome.storage.local.get({ overlayEnabled: false, overlayKill: false, overlayPos: null, overlaySound: false }, function (v) {
        try {
          killed = !!(v && v.overlayKill);
          enabled = !!(v && v.overlayEnabled);
          savedPos = (v && v.overlayPos) || null;   // [보완#2] 저장된 위치 복원
          soundOn = !!(v && v.overlaySound);         // [보완#3] 알림음 옵션 복원
          if (killed) { removeAll(); return; }
          render();
        } catch (_) { /* */ }
      });
      // 상태/데이터 변경 → 즉시 반영
      chrome.storage.onChanged.addListener(function (ch, area) {
        try {
          if (area !== 'local') return;
          if (ch.overlayKill) { killed = !!ch.overlayKill.newValue; if (killed) removeAll(); else render(); return; }
          if (ch.overlayPos) { savedPos = ch.overlayPos.newValue || null; }   // [보완#2] 위치 동기화(다른 탭 반영)
          if (ch.overlaySound) { soundOn = !!ch.overlaySound.newValue; }      // [보완#3] 알림음 옵션 동기화
          if (ch.overlayEnabled) { enabled = !!ch.overlayEnabled.newValue; render(); }
          if ((ch.analyzeStatus || ch.collectAlert || ch.timerDeadline) && enabled && !killed) render();
        } catch (_) { /* */ }
      });
      // [캡쳐 대비] 경주결과 캡쳐 순간 오버레이가 결과를 가리지 않게 잠깐 숨김(visibility만·상태 보존).
      //   background 가 캡쳐 직전/직후 KB_CAPTURE_PREP{hide} 를 보냄. 읽기전용 원칙 유지(자체 요소 표시만 토글).
      try {
        chrome.runtime.onMessage.addListener(function (msg, _s, sendResp) {
          try {
            if (msg && msg.type === 'KB_CAPTURE_PREP') {
              var vis = msg.hide ? 'hidden' : '';
              ['kbOvPanel', 'kbOvToggle', 'kbOvAlert'].forEach(function (id) {
                var e = byId(id); if (e) e.style.visibility = vis;
              });
              if (typeof sendResp === 'function') sendResp({ ok: true });
            }
          } catch (_) { /* */ }
          return false;
        });
      } catch (_) { /* */ }
    } catch (_) { /* storage 접근 실패해도 페이지/수집 영향 없음 */ }
  } catch (_) {
    /* 최상위 보호막 — 어떤 예외도 페이지/수집 엔진에 전파되지 않는다. */
  }
})();
