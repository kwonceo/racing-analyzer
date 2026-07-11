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
    var stallNudgeAt = 0;   // [수집 조기 중단 방어] 재수집 트리거 throttle(30초)
    var savedPos = null;   // [보완#2] 사용자가 드래그해 옮긴 패널 위치({left,top}) — chrome.storage 에 저장/복원
    var soundOn = false, lastSoundKey = '';   // [보완#3] 강조 팝업 알림음 옵션(기본 off) + 중복 방지
    // [강한 신호 8유형·막판 보존] T-2분 이후 감지된 강신호를 경주 종료 후에도 유지(새 경주 rk 변경 시에만 초기화)
    var preservedSig = null, preservedRk = '', preservedLabel = '';

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
          // [역배열 강화] 배당 차이 기반 4단계 강도(diffPct·level·tag). 미세차(10%↓)는 서버가 이미 제외.
          var tier = (lead && lead.tag) ? (lead.level + ' ' + lead.tag) : ((det[0] && det[0].tag) ? det[0].level + ' ' + det[0].tag : '🟠 역배열');
          var dpv = (lead && lead.diffPct != null) ? lead.diffPct : (det[0] && det[0].diffPct);
          lines.push('강도: ' + tier + (dpv != null ? ' (배당차 ' + dpv + '%)' : ''));
          var critLevel = (/🔴/.test(tier)) ? 'red' : 'orange';   // 🔴/🔴🔴=red · 🟡/🟠=orange
          var ihKey = det.map(function (x) { return x.no; }).join('·');
          return { key: 'inv:' + ihKey + ':' + (lead ? lead.odds : ''), level: critLevel, icon: '🔄', title: '역배열 감지',
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

    // ── [강한 신호 8유형] 강조 박스 + 막판 보존(경주 종료 후 유지) ──────────
    var STRONG_LEVEL_COLOR = { '강력': '#dc2626', '복승': '#f59e0b', '보조': '#3b82f6' };
    function appendStrongBox(panel, ss, preserved, label) {
      try {
        if (!ss || !ss.signals || !ss.signals.length) return;
        var lvl = ss.recommendLevel || '';
        var accent = preserved ? '#38d39f' : (STRONG_LEVEL_COLOR[lvl] || '#dc2626');
        var box = mk('div', 'margin:4px 0 7px;padding:7px 9px;border:2px solid ' + accent +
          ';border-radius:8px;background:rgba(220,38,38,' + (preserved ? '.10' : '.16') + ')');
        box.appendChild(mk('div', 'font-weight:900;font-size:13px;color:' + accent,
          preserved ? '📌 마감 전 주요 신호 (보존)' : '🔴 강한 신호 감지!'));
        if (label) box.appendChild(mk('div', 'color:#94a3b8;font-size:10px;margin-bottom:2px', label));
        ss.signals.slice(0, 6).forEach(function (s) {
          var dot = s.level === 'red' ? '🔴' : '🟠';
          var row = mk('div', 'margin:2px 0');
          row.appendChild(mk('span', 'font-weight:700;font-size:11px;color:' + (s.level === 'red' ? '#fca5a5' : '#fbbf24'),
            dot + ' 유형' + s.type + ' ' + s.label));
          if (s.detail) row.appendChild(mk('div', 'color:#cbd5e1;font-size:11px;margin-left:14px', s.detail));
          box.appendChild(row);
        });
        if (lvl) {
          var recTxt = ss.dualConverge ? '→ 이중수렴 · 강력 추천'
            : (lvl === '강력' ? '→ 강력 추천' : (lvl === '복승' ? '→ 복승 추천' : '→ 보조 추천'));
          box.appendChild(mk('div', 'margin-top:3px;font-weight:800;color:' + accent, recTxt + ' (' + ss.count + '개 신호)'));
        }
        panel.appendChild(box);
      } catch (_) { /* */ }
    }
    // 라이브 강신호 표시 + T-2분 이후 감지분 보존(경주 종료·데이터 소멸 후에도 유지)
    function renderStrongSignals(panel, d, deadline) {
      try {
        var ss = d && d.strongSignals;
        var rk = (d && d.raceKey) || '';
        if (rk && rk !== preservedRk) { preservedRk = rk; preservedSig = null; preservedLabel = ''; }   // 새 경주만 초기화
        var leftMs = deadline ? (deadline - Date.now()) : null;
        var hasLive = !!(ss && ss.signals && ss.signals.length);
        // [4번] T-2분 이내 강신호 → 보존(막판 신호 사라짐 방지 · 유형3 마감직전 대급락 포함)
        if (hasLive && leftMs != null && leftMs <= 120000) {
          preservedSig = ss;
          preservedLabel = leftMs > 0 ? ('T-' + Math.max(1, Math.round(leftMs / 60000)) + '분경 감지 · 보존') : '마감 직전 감지 · 보존';
        }
        if (hasLive) appendStrongBox(panel, ss, false, '');       // 라이브 강신호
        else if (preservedSig) appendStrongBox(panel, preservedSig, true, preservedLabel);  // 경주 종료 후 보존
        // [새 규칙·카와사키11R] 막판 급락+역배열 동시말 → 삼복승 강제보험 강조(유력마 순위 무관)
        var ft = d && d.forcedTrifecta;
        if (ft && ft.active && (ft.horses || []).length) {
          var fb = mk('div', 'margin:5px 0;padding:6px 9px;border:2px solid #ef4444;border-radius:8px;background:rgba(239,68,68,.16)');
          fb.appendChild(mk('div', 'font-weight:800;color:#fca5a5', '🚨 삼복승 강제보험 (막판 급락+역배열)'));
          (ft.horses || []).forEach(function (h) {
            var row = mk('div', 'margin:1px 0;font-size:11px;color:#fecaca');
            row.appendChild(mk('span', 'font-weight:800;color:#fca5a5', h.no + '번'));
            row.appendChild(mk('span', 'margin-left:5px;color:#fecaca', h.note || ''));
            fb.appendChild(row);
          });
          (ft.combos || []).forEach(function (c) {
            fb.appendChild(mk('div', 'margin:1px 0;font-weight:800;color:#fca5a5;font-size:11px',
              '삼복승: ' + c.join('+') + ' (강제 편성)'));
          });
          fb.appendChild(mk('div', 'margin-top:2px;font-size:10px;color:#94a3b8', 'TOP3 밖이어도 강제 편성 · 카와사키 11R 학습 규칙'));
          panel.appendChild(fb);
        }
      } catch (_) { /* */ }
    }
    // [저배당 압축 패턴] 유력마 TOP3 중 저배당 밀집(축 패턴) 강조 박스
    function renderCompression(panel, d) {
      try {
        var cp = d && d.compressionPattern;
        if (!cp || !cp.detected) return;
        var strong = cp.level === '강력';
        var accent = strong ? '#f59e0b' : '#4ea1ff';
        var box = mk('div', 'margin:4px 0 6px;padding:6px 9px;border:1px solid ' + accent +
          ';border-radius:8px;background:rgba(245,158,11,' + (strong ? '.16' : '.10') + ')');
        box.appendChild(mk('div', 'font-weight:800;color:' + accent,
          '🎯 저배당 압축 패턴 (' + (cp.level || '') + ')'));
        if (cp.band) box.appendChild(mk('div', 'color:#94a3b8;font-size:10px', cp.band));
        (cp.reprs || []).forEach(function (r) {
          var lo = r.odds <= 4.0;
          var row = mk('div', 'margin:1px 0;font-size:11px');
          row.appendChild(mk('span', 'font-weight:700;color:' + (lo ? '#fbbf24' : '#e5e7eb'), r.no + '번'));
          row.appendChild(mk('span', 'margin-left:5px;color:#cbd5e1', r.odds + '배' + (lo ? ' ◀ 저배당' : '')));
          box.appendChild(row);
        });
        if (cp.combo && cp.combo.length === 2) {
          box.appendChild(mk('div', 'margin-top:2px;font-weight:800;color:#38d39f',
            '복승: ' + cp.combo[0] + '+' + cp.combo[1] + ' 자신있게'));
        }
        if (cp.note) box.appendChild(mk('div', 'margin-top:2px;font-size:11px;font-weight:700;color:' + accent, '✅ ' + cp.note));
        // [배당 3착 자동 발굴] 축 2두 + 고배당 3착 후보 → 삼복승 보험
        var tp = d && d.thirdPlaceHunt;
        if (tp && tp.active && (tp.candidates || []).length) {
          box.appendChild(mk('div', 'margin-top:5px;color:#c4b5fd;font-weight:700;font-size:11px', '🎯 삼복승 3착 후보'));
          tp.candidates.forEach(function (c, i) {
            var row = mk('div', 'margin:1px 0;font-size:11px');
            row.appendChild(mk('span', 'color:#94a3b8', (i + 1) + '순위 '));
            row.appendChild(mk('span', 'font-weight:800;color:#a78bfa', c.no + '번'));
            row.appendChild(mk('span', 'margin-left:4px;color:#cbd5e1', '(' + c.odds + '배)'));
            row.appendChild(mk('span', 'margin-left:5px;color:#fbbf24', (c.icon || '') + ' ' + c.reason + ' (신뢰 ' + c.conf + ')'));
            box.appendChild(row);
          });
          (tp.trios || []).forEach(function (t) {
            var od = (t.expOddsEst != null) ? ('  추정 ' + t.expOddsEst + '배') : '';
            box.appendChild(mk('div', 'margin:1px 0;font-weight:800;color:#a78bfa;font-size:11px',
              '삼복승: ' + t.combo.join('+') + od));
          });
        }
        panel.appendChild(box);
      } catch (_) { /* */ }
    }

    // 패널 내용 갱신 (div/span 만 사용 · textContent 기반)
    // [최종 결론 박스] 모든 종목(경마/경륜/경정) 공통 — 패널 최상단에 가장 크게 표시.
    //   신호 2개+ → 🎯 지금 사세요!(복승·삼복승·역배열추가) / 신호 1개 → 💡 참고 추천(복승 소액) /
    //   신호 0 → ⏳ 신호 대기 중 / 패스형 → ⛔ 이번 경주 패스.
    function signalCount(d) {
      if (!d) return 0;
      if (d.strongSignals && typeof d.strongSignals.count === 'number') return d.strongSignals.count;
      var n = 0;
      if ((d.drops || []).some(function (x) { return x && x.pct <= -30; })) n++;
      if (d.inverse && d.inverse.detected) n++;
      return n;
    }
    function renderConclusion(panel, d, deadline) {
      var rj = (d && d.raceJudgment) || {};
      var afterClose = !!(d && d.afterClose);
      // 복승/삼복승 추천 조합 추출(betRecommend '복승 메인' 우선 · trioRecommend 최상위)
      var quinella = null, trio = null, revAdd = null;
      if (d) {
        var recs = d.betRecommend || [], main = null;
        for (var i = 0; i < recs.length; i++) {
          if (recs[i].label && recs[i].label.indexOf('복승 메인') === 0) { main = recs[i]; break; }
        }
        if (!main && recs.length) main = recs[0];
        if (main && main.combo && main.combo.length) quinella = main.combo.join('+');
        var trios = (d.trioRecommend || []).filter(function (t) { return t && t.combo && t.combo.length === 3; });
        if (trios.length) trio = trios[0].combo.join('+');
        // 역배열 추가: 역배열 실질유력마(invLead) + 유력마 상위 2두로 삼복승 구성
        if (d.inverse && d.inverse.detected && d.inverse.invLead && d.inverse.invLead.no != null) {
          var lead = Number(d.inverse.invLead.no);
          var others = (d.keyHorses || []).map(Number).filter(function (n) { return n !== lead; }).slice(0, 2);
          if (others.length === 2) revAdd = [lead].concat(others).sort(function (a, b) { return a - b; }).join('+');
        }
      }
      var sc = signalCount(d);
      // [타이밍 추천 정책] 마감 후 추천 금지(closed) · T-1분 최종 확정(locked) · T-2분 강제 추천(forced)
      var closed = !!(d && d.recommendClosed) || afterClose;
      var locked = !!(d && d.recommendLocked);
      var forced = !!(d && d.recommendForced);
      // 상태 판정
      var state;
      if (!d) state = 'wait';
      else if (closed) state = 'closed';                       // 마감 후 → 추천 금지(조합 숨김)
      else if (rj.type === '패스형' && !forced) state = 'pass';
      else if (sc >= 2 && (quinella || trio)) state = 'go2';
      else if ((sc >= 1 || forced) && quinella && !(d.recommendGated && !forced)) state = 'go1';
      else state = 'wait';

      var box = mk('div', 'margin:2px 0 9px;padding:11px 12px;border-radius:10px;text-align:center;border:2px solid #334155');
      var line = function (txt, css) { var e = mk('div', css); e.textContent = txt; box.appendChild(e); };
      if (state === 'closed') {
        box.style.borderColor = '#94a3b8';
        box.style.background = 'rgba(148,163,184,.14)';
        line('🔒 마감 — 추천 종료', 'font-weight:900;font-size:18px;color:#94a3b8');
        line('발주 후에는 추천하지 않습니다 (참고만)', 'margin-top:4px;font-size:12px;color:#cbd5e1');
      } else if (state === 'go2') {
        var goCol = locked ? '#ef4444' : '#22c55e';
        box.style.borderColor = goCol;
        box.style.background = locked ? 'rgba(239,68,68,.15)' : 'rgba(34,197,94,.18)';
        line(locked ? '🔒 최종 확정 · 지금 사세요!' : (forced ? '⚡ 지금 사세요! (T-2분 강제)' : '🎯 지금 사세요!'), 'font-weight:900;font-size:18px;color:' + goCol);
        if (quinella) line('복승: ' + quinella, 'margin-top:5px;font-weight:800;font-size:15px;color:#e5e7eb');
        if (trio) line('삼복승: ' + trio + ' (보험)', 'margin-top:2px;font-weight:800;font-size:15px;color:#e5e7eb');
        if (revAdd) line('역배열 추가: ' + revAdd, 'margin-top:2px;font-weight:700;font-size:13px;color:#f0abfc');
      } else if (state === 'go1') {
        box.style.borderColor = locked ? '#ef4444' : '#4ea1ff';
        box.style.background = locked ? 'rgba(239,68,68,.13)' : 'rgba(78,161,255,.15)';
        line(locked ? '🔒 최종 확정' : (forced ? '⚡ T-2분 강제 추천' : '💡 참고 추천'), 'font-weight:900;font-size:18px;color:' + (locked ? '#f87171' : '#4ea1ff'));
        if (quinella) line('복승: ' + quinella + (forced && !locked ? ' (저배당 기준)' : ' (소액)'), 'margin-top:5px;font-weight:800;font-size:15px;color:#e5e7eb');
        if (trio && (forced || locked)) line('삼복승: ' + trio + ' (보험)', 'margin-top:2px;font-weight:800;font-size:14px;color:#e5e7eb');
      } else if (state === 'pass') {
        box.style.borderColor = '#f87171';
        box.style.background = 'rgba(220,38,38,.16)';
        line('⛔ 이번 경주 패스', 'font-weight:900;font-size:18px;color:#f87171');
        line('신호 없음', 'margin-top:3px;font-size:13px;color:#fecaca');
      } else {
        box.style.borderColor = '#fbbf24';
        box.style.background = 'rgba(245,158,11,.14)';
        line('⏳ 신호 대기 중', 'font-weight:900;font-size:18px;color:#fbbf24');
      }
      panel.appendChild(box);
    }

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

        // ═══ [정보 순서 정리] 1.결론박스 → 2.카운트다운 → 3.유력마 → 4.이상감지 → 5.나머지 ═══
        // [1번] 최종 결론 박스 — 최상단·가장 크게(모든 종목 공통: 경마/경륜/경정)
        renderConclusion(panel, d, deadline);

        // [2번] 마감 카운트다운 — T-2분 주황 · T-1분 빨강 · T-30초 깜빡임
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

        // [중앙경마 마감 특성] JRA 배당판은 T-2분에 닫힘 → 실질 마감 경고
        if (d && d.centralClosing) {
          var ccRow = mk('div', 'margin:2px 0 6px;padding:6px 9px;border-radius:6px;border:1px solid #f87171;background:rgba(220,38,38,.18)');
          ccRow.appendChild(mk('div', 'font-weight:800;font-size:12px;color:#fca5a5', '⚠️ 중앙경마 배당판 T-2분에 닫힘'));
          ccRow.appendChild(mk('div', 'font-weight:800;font-size:12px;color:#fecaca', '지금이 마지막 신호!'));
          panel.appendChild(ccRow);
        }

        // [수집 조기 중단 방어] 발주 전인데 2분+ 미수집 → 경고 + 확장 재수집 트리거(30초 1회 throttle)
        if (d && d.collectionStalled) {
          var stRow = mk('div', 'margin:2px 0 6px;padding:6px 9px;border-radius:6px;border:1px solid #fbbf24;background:rgba(245,158,11,.18)');
          stRow.appendChild(mk('div', 'font-weight:800;font-size:12px;color:#fcd34d', '⚠️ 수집 중단 감지'));
          stRow.appendChild(mk('div', 'font-weight:700;font-size:11px;color:#fde68a', '자동 재수집 시도 중...'));
          panel.appendChild(stRow);
          var _sn = Date.now();
          if (!stallNudgeAt || _sn - stallNudgeAt > 30000) {
            stallNudgeAt = _sn;
            try { chrome.runtime.sendMessage({ type: 'FORCE_COLLECT', reason: 'stall' }); } catch (_) { /* */ }
          }
        }

        // 중요 신호 팝업(상단 중앙)은 항상 갱신 · 배너는 아래 [4번 이상감지] 그룹에 표시
        var crit = computeCritical(d, deadline);
        renderAlertPopup(crit);

        if (!d) {
          renderStrongSignals(panel, d, deadline);   // 경주 종료 후 보존 신호는 유지
          panel.appendChild(mk('div', 'color:#94a3b8', '분석 대기 중 — 배당 수집·이상감지가 실행되면 표시됩니다.'));
          return;
        }

        // [5번] T-30초 마감 임박: 결론 박스(최종 조합)+카운트다운만 표시하고 상세는 생략(집중).
        var _leftMs = deadline ? (deadline - Date.now()) : 0;
        if (_leftMs > 0 && _leftMs <= 30000) {
          panel.appendChild(mk('div', 'margin-top:8px;color:#fca5a5;font-size:11px;font-weight:700', '⚡ 마감 임박 — 최종 조합만 표시'));
          return;
        }

        // [4번] 유력마 3마리 + 복병 — 배당 없는 잔존마 제외
        var vset = validSet(d);
        var inV = function (n) { return !vset || vset.has(Number(n)); };
        var rtNos = (d.realtimeAdded || []).map(function (r) { return Number(r.no); });
        var baseKeys = (d.keyHorses || []).filter(inV).filter(function (n) { return rtNos.indexOf(Number(n)) < 0; });
        if (baseKeys.length) {
          var kr = mk('div', 'margin:3px 0');
          kr.appendChild(mk('span', 'color:#94a3b8', '⭐ 유력마 '));
          kr.appendChild(mk('span', 'font-weight:700;color:#4ea1ff', baseKeys.slice(0, 3).join(' · ')));
          panel.appendChild(kr);
        }
        // [3번] 복병 — 유력마 밖 강한 신호말(역배열 실질유력마 + 급락 이상감지말). 최대 2두.
        var darkShown = {};
        var addDark = function (no, tag, col) {
          if (no == null || !inV(no)) return;
          if (baseKeys.indexOf(Number(no)) >= 0) return;         // 이미 유력마면 복병 아님
          if (darkShown[no]) return;
          darkShown[no] = 1;
          var db = mk('div', 'margin:2px 0');
          db.appendChild(mk('span', 'color:#94a3b8', '🐎 복병 '));
          db.appendChild(mk('span', 'font-weight:700;color:' + col, no + '번 (' + tag + ')'));
          panel.appendChild(db);
        };
        // [복병_집중급락 패턴] 집중급락 10회+/스마트머니 → 배당순위 무관 복병 자동 편입(신뢰 높음 강조)
        (d.darkHorses || []).forEach(function (h) {
          if (h.no == null || !inV(h.no) || baseKeys.indexOf(Number(h.no)) >= 0 || darkShown[h.no]) return;
          darkShown[h.no] = 1;
          var col = (h.confidence === '높음') ? '#f472b6' : '#c084fc';
          var db = mk('div', 'margin:2px 0');
          db.appendChild(mk('span', 'color:#94a3b8', '🐎 복병 '));
          db.appendChild(mk('span', 'font-weight:800;color:' + col, h.no + '번 '));
          db.appendChild(mk('span', 'font-size:11px;font-weight:700;color:' + col, (h.note || '') + (h.confidence === '높음' ? ' · 신뢰↑' : '')));
          panel.appendChild(db);
        });
        // 역배열 복병
        if (d.inverse && d.inverse.detected && d.inverse.invLead && d.inverse.invLead.no != null) {
          addDark(Number(d.inverse.invLead.no), '역배열', '#f0abfc');
        }
        // 급락 복병 — 이상감지말(anomalyHorse) 우선, 없으면 최대 급락 조합 중 유력마 아닌 말
        if (Object.keys(darkShown).length < 2) {
          var dropDark = (d.anomalyHorse != null) ? Number(d.anomalyHorse) : null;
          if (dropDark == null) {
            var td = (d.drops || []).filter(function (x) { return x && x.pct <= -30 && x.combo; })
              .sort(function (a, b) { return a.pct - b.pct; })[0];
            if (td) {
              for (var di = 0; di < td.combo.length; di++) {
                var cno = Number(td.combo[di]);
                if (baseKeys.indexOf(cno) < 0 && inV(cno)) { dropDark = cno; break; }
              }
            }
          }
          if (dropDark != null) addDark(dropDark, '급락', '#f87171');
        }

        // [전적 과가중 해결] 📊 시장 유력(전적 미수집) — 저배당(5배↓)이라 유력마 편입된 말
        (d.marketFavorites || []).filter(function (m) { return m.formMissing && inV(m.no); }).forEach(function (m) {
          var mf = mk('div', 'margin:2px 0;padding:3px 8px;border-left:3px solid #38bdf8;background:rgba(56,189,248,.12);border-radius:6px;font-size:11px');
          mf.appendChild(mk('span', 'font-weight:800;color:#38bdf8', '📊 ' + m.no + '번 시장 유력'));
          mf.appendChild(mk('span', 'margin-left:5px;color:#7dd3fc', '배당 ' + m.odds + '배 (전적 미수집)'));
          panel.appendChild(mf);
        });

        // [3번-실시간] ⚡ 실시간 추가 — 초반 유력마 고정 후 급락/역배열 감지로 편입된 말
        (d.realtimeAdded || []).forEach(function (r) {
          if (!inV(r.no)) return;
          var ra = mk('div', 'margin:3px 0;padding:4px 8px;border-left:3px solid #22c55e;background:rgba(34,197,94,.15);border-radius:6px');
          ra.appendChild(mk('span', 'font-weight:800;color:#4ade80', '⚡ ' + r.no + '번 실시간 추가!'));
          ra.appendChild(mk('span', 'margin-left:6px;font-size:11px;color:#bbf7d0', (r.reason || '') + ' 감지'));
          panel.appendChild(ra);
        });

        // [3번] 핵심 신호 3~4줄 — 🔄 역배열 · 🔴 급락 · 💡 저배당 압축
        var sig = [];
        if (d.inverse && d.inverse.detected && d.inverse.invLead && d.inverse.invLead.no != null) {
          var L = d.inverse.invLead;
          sig.push({ t: '🔄 ' + L.no + '번 역배열' + (L.diffPct != null ? ' ' + L.diffPct + '%' : ''), c: '#f0abfc' });
        }
        var topDrop = (d.drops || []).filter(function (x) { return x && x.pct <= -30 && x.combo; })
          .sort(function (a, b) { return a.pct - b.pct; })[0];
        if (topDrop) {
          var dh = (d.anomalyHorse != null) ? (d.anomalyHorse + '번') : topDrop.combo.join('+');
          sig.push({ t: '🔴 ' + dh + ' 급락 -' + Math.abs(Math.round(topDrop.pct)) + '%', c: '#f87171' });
        }
        var cp = d.compressionPattern;
        if (cp && cp.detected && cp.combo && cp.combo.length === 2) {
          sig.push({ t: '💡 저배당 압축: ' + cp.combo.join('+') + (cp.axis != null ? ' (축 ' + cp.axis + '번)' : ''), c: '#38d39f' });
        }
        if (sig.length) {
          panel.appendChild(mk('div', 'margin:6px 0 2px;color:#94a3b8;font-size:11px', '핵심 신호'));
          sig.slice(0, 4).forEach(function (s) {
            var row = mk('div', 'margin:1px 0;font-weight:800;font-size:13px;color:' + s.c);
            row.textContent = s.t;
            panel.appendChild(row);
          });
        }

        panel.appendChild(mk('div', 'margin-top:8px;color:#64748b;font-size:10px',
          '※ 읽기 전용 · 상세는 분석기 웹 참고'));
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
