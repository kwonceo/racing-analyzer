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
    var matrixOpen = false;   // [배당 매트릭스] 오버레이 간략 매트릭스 열림 상태(30초 재렌더에도 유지)
    var boardActive = false;  // [배당판 위 정렬 오버레이] 실제 배당판 위 강조 렌더 성공 여부(패널 격자 생략 판단)
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
      try { removeBoardMatrix(); } catch (_) { /* */ }   // [배당판 위 정렬 오버레이] 정리
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
          chrome.storage.local.get({ analyzeStatus: null, timerDeadline: 0, collectAlert: null,
            ovShowMatrix: false, ovShowPicks: true, ovShowTimeline: false, keirinAutoStatus: null }, function (v) {
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

    // ═══ [배당 매트릭스] 오버레이 간략 매트릭스 (div/span 만·table 금지) ═══
    var MX_COL = { fav: '#38d39f', cut: '#ef4444', weakcut: '#ff9f43', dark: '#c084fc', inv: '#fbbf24' };

    // 말별 역할: 유력(fav)/제거(cut·weakcut)/복병(dark)
    function matrixRoles(d) {
      var role = {};
      (d.keyHorses || []).forEach(function (h) { role[+h] = 'fav'; });
      var e = d.elimination || {};
      (e.horses || []).forEach(function (h) {
        var keep = h.keep || h.override;
        if (keep) { if (role[+h.no] == null) role[+h.no] = 'fav'; }
        else { role[+h.no] = (h.verdict === '🔴' ? 'cut' : 'weakcut'); }
      });
      var keys = {}; (d.keyHorses || []).forEach(function (h) { keys[+h] = 1; });
      (d.darkHorses || []).forEach(function (h) {
        var n = +h.no; if (!keys[n] && role[n] !== 'cut' && role[n] !== 'weakcut') role[n] = 'dark';
      });
      return role;
    }

    function mxHeat(v, lo, hi) {
      if (!(v > 0)) return 'transparent';
      var l = Math.log(v), a0 = Math.log(lo), a1 = Math.log(hi);
      var f = a1 > a0 ? (l - a0) / (a1 - a0) : 0;
      return 'rgba(37,99,235,' + (0.82 - 0.66 * f).toFixed(2) + ')';
    }

    // 간략 복승 삼각 매트릭스 DOM(div 격자) 반환. 없으면 null.
    function buildMatrix(d) {
      var q = (d && Array.isArray(d.quinella)) ? d.quinella : [];
      var om = {}, nosSet = {};
      q.forEach(function (x) {
        var c = (x.combo || x.pair || []).map(Number);
        if (c.length === 2 && x.odds > 0) {
          var k = Math.min(c[0], c[1]) + '|' + Math.max(c[0], c[1]);
          om[k] = x.odds; nosSet[c[0]] = 1; nosSet[c[1]] = 1;
        }
      });
      var nos = Object.keys(nosSet).map(Number).filter(function (n) { return n > 0; }).sort(function (a, b) { return a - b; });
      if (!nos.length) return null;
      var vals = Object.keys(om).map(function (k) { return om[k]; });
      var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
      var role = matrixRoles(d);
      var invSet = {}; (((d.inverse || {}).invHorses) || []).forEach(function (n) { invSet[+n] = 1; });
      var dropMap = {};
      (d.drops || []).forEach(function (dd) {
        var c = (dd.combo || []).map(Number);
        if (c.length === 2 && (dd.pct || 0) <= -20) dropMap[Math.min(c[0], c[1]) + '|' + Math.max(c[0], c[1])] = Math.round(dd.pct);
      });
      var recSet = {};
      (d.betRecommend || []).forEach(function (b) {
        var c = (b.combo || []).map(Number);
        if (c.length === 2 && /복/.test(b.kind || b.label || '')) recSet[Math.min(c[0], c[1]) + '|' + Math.max(c[0], c[1])] = 1;
      });
      var CW = '26px';
      function cellBase(extra) { return 'min-width:' + CW + ';height:20px;line-height:20px;text-align:center;font-size:10px;box-sizing:border-box;' + (extra || ''); }
      function hdrCell(n) {
        var r = role[n], col = MX_COL[r] || '#cbd5e1';
        var mark = r === 'fav' ? '⭐' : r === 'cut' ? '❌' : r === 'dark' ? '🟣' : r === 'weakcut' ? '△' : '';
        var css = cellBase('font-weight:800;color:' + col + ';' + (invSet[n] ? 'box-shadow:inset 0 0 0 2px ' + MX_COL.inv + ';' : ''));
        var s = mk('span', css, mark + n);
        s.title = n + '번' + (r ? ' · ' + ({ fav: '유력', cut: '확실제거', weakcut: '제거권장', dark: '복병' }[r]) : '') + (invSet[n] ? ' · 역배열' : '');
        return s;
      }
      var wrap = mk('div', 'overflow:auto;max-height:200px;max-width:100%;margin-top:5px;border-top:1px solid #334155;padding-top:5px');
      var grid = mk('div', 'display:inline-block;font-family:monospace');
      // 헤더 행
      var hRow = mk('div', 'display:flex');
      hRow.appendChild(mk('span', cellBase('color:#64748b'), '복승'));
      for (var ci0 = 0; ci0 < nos.length - 1; ci0++) hRow.appendChild(hdrCell(nos[ci0]));
      grid.appendChild(hRow);
      // 본문 삼각
      for (var ri = 1; ri < nos.length; ri++) {
        var row = mk('div', 'display:flex');
        row.appendChild(hdrCell(nos[ri]));
        for (var ci = 0; ci < ri; ci++) {
          var key = Math.min(nos[ri], nos[ci]) + '|' + Math.max(nos[ri], nos[ci]);
          var v = om[key];
          if (v > 0) {
            var bd = '';
            if (dropMap[key] != null) bd = 'box-shadow:inset 0 0 0 2px #ef4444;';
            else if (recSet[key]) bd = 'box-shadow:inset 0 0 0 2px #ffd24f;';
            else if (role[nos[ri]] === 'fav' && role[nos[ci]] === 'fav') bd = 'box-shadow:inset 0 0 0 2px #38d39f;';
            var inv = (invSet[nos[ri]] || invSet[nos[ci]]) ? 'outline:2px solid ' + MX_COL.inv + ';outline-offset:-3px;' : '';
            var cell = mk('span', cellBase('color:#e2e8f0;background:' + mxHeat(v, lo, hi) + ';' + bd + inv), v + (dropMap[key] != null ? '▼' : ''));
            cell.title = nos[ri] + '-' + nos[ci] + ' = ' + v + '배' + (dropMap[key] != null ? ' · 급락 ' + dropMap[key] + '%' : '') + (recSet[key] ? ' · 추천' : '');
            row.appendChild(cell);
          } else row.appendChild(mk('span', cellBase('color:#475569'), '·'));
        }
        row.appendChild(mk('span', cellBase('color:#475569'), '—'));
        grid.appendChild(row);
      }
      wrap.appendChild(grid);
      // 범례
      wrap.appendChild(mk('div', 'font-size:9px;color:#94a3b8;margin-top:3px',
        '⭐유력 · 🟣복병 · ❌제거 · 역배열(노랑테) · ▼급락(빨강테) · 추천(금색테)'));
      return wrap;
    }

    // ═══════════════════════════════════════════════════════════════════
    // [배당판 위 정렬 오버레이] 실제 배당판 셀을 getBoundingClientRect 로 측정 →
    //   같은 크기·위치의 강조 셀을 그 위에 1:1로 겹쳐 표시(노안 대비 큰 글씨·3중 강조).
    //   ⚠ 실제 표는 읽기만(위치 측정) — DOM 수정 없음. 자체 레이어(kbOvBoard)만 주입.
    //   [색상 근본 재정의] 초록=복승 추천(1~2) · 파랑=유력(저배당+긍정신호:급락/역배열/스마트머니, 5~6) ·
    //     빨강=경고/회피(죽은인기+고배당·3연속상승·페이크, 3~4) · 나머지=흰색/중립(단순저배당·무변동).
    // ═══════════════════════════════════════════════════════════════════
    var BOARD_ID = 'kbOvBoard';
    var boardItems = [];      // [{el, span}] 배당 셀 ↔ 오버레이 셀 매핑(재배치용)
    var boardHdrItems = [];   // [{el, span}] 헤더(마번) ↔ 오버레이 매핑
    var boardBound = false, boardRaf = 0;
    var BCOL = { fav: '#3b82f6', drop: '#ef4444', rec: '#22c55e', warn: '#eab308' };

    function pureIntT(s) { s = (s == null ? '' : String(s)).trim(); return /^\d{1,2}$/.test(s) ? parseInt(s, 10) : null; }
    function numT(s) { var m = (s == null ? '' : String(s)).replace(/[, ]/g, '').match(/\d+(\.\d+)?/); return m ? parseFloat(m[0]) : null; }

    // 실제 배당판(복승 매트릭스) 표 + 셀 요소 위치 매핑. content.js parseMatrixTable 과 동일 휴리스틱(읽기전용).
    function mapBoardTable(table, oddsClass) {
      var rows; try { rows = [].slice.call(table.rows); } catch (_) { return null; }
      if (rows.length < 2) return null;
      var headerRow = null, best = 1;
      rows.slice(0, 3).forEach(function (r) {
        var c = [].slice.call(r.cells).filter(function (td) { return pureIntT(td.textContent) != null; }).length;
        if (c > best) { best = c; headerRow = r; }
      });
      if (!headerRow) return null;
      var headerNos = [], hdrEls = [], colByIdx = {};
      [].slice.call(headerRow.cells).forEach(function (cell) {
        var n = pureIntT(cell.textContent);
        if (n != null) { headerNos.push(n); hdrEls.push({ no: n, el: cell }); colByIdx[cell.cellIndex] = n; }
      });
      if (headerNos.length < 2) return null;
      var isOdds = oddsClass
        ? function (td) { return td.classList && td.classList.contains(oddsClass); }
        : function (td) { return /^\d+\.\d+$/.test((td.textContent || '').trim()); };
      var cells = [];
      rows.forEach(function (r) {
        if (r === headerRow) return;
        var rc = [].slice.call(r.cells), rowNo = null;
        for (var i = 0; i < rc.length; i++) { var n = pureIntT(rc[i].textContent); if (n != null) { rowNo = n; break; } }
        if (rowNo == null) return;
        var oddsCells = rc.filter(isOdds);
        if (!oddsCells.length) return;
        var all = headerNos.filter(function (n) { return n !== rowNo; });
        var upper = headerNos.filter(function (n) { return n > rowNo; });
        var lower = headerNos.filter(function (n) { return n < rowNo; });
        var cols = null;
        if (oddsCells.length === all.length) cols = all;
        else if (oddsCells.length === upper.length) cols = upper;
        else if (oddsCells.length === lower.length) cols = lower;
        if (cols) {
          oddsCells.forEach(function (oc, i) {
            var colNo = cols[i], val = numT(oc.textContent);
            if (colNo != null && colNo !== rowNo && val != null && val >= 1.0) cells.push({ a: rowNo, b: colNo, odds: val, el: oc });
          });
        } else {
          oddsCells.forEach(function (oc) {
            var colNo = colByIdx[oc.cellIndex], val = numT(oc.textContent);
            if (colNo != null && colNo !== rowNo && val != null && val >= 1.0) cells.push({ a: rowNo, b: colNo, odds: val, el: oc });
          });
        }
      });
      if (cells.length < 3) return null;
      return { table: table, headerRow: headerRow, headerNos: headerNos, hdrEls: hdrEls, cells: cells };
    }

    function locateBoardMatrix() {
      var oddsClass = /asyukk|qwqwd/i.test(location.host) ? 'odds_content' : null;
      var best = null, tables;
      try { tables = document.querySelectorAll('table'); } catch (_) { return null; }
      for (var ti = 0; ti < tables.length; ti++) {
        var info = mapBoardTable(tables[ti], oddsClass);
        if (info && (!best || info.cells.length > best.cells.length)) best = info;
      }
      // 사설(asyukk) class 로 못 찾으면 소수점 숫자 폴백으로 재시도
      if (!best && oddsClass) {
        for (var tj = 0; tj < tables.length; tj++) {
          var info2 = mapBoardTable(tables[tj], null);
          if (info2 && (!best || info2.cells.length > best.cells.length)) best = info2;
        }
      }
      return best;
    }

    // [6번] 조합(a-b) 강조 판정 — 한 셀에 색 하나만. 우선순위: 빨강(급락)>초록(확정)>파랑(유력)>노랑(주의).
    //   해당 없으면 null → [7번] 완전 투명(실제 배당 그대로).
    function boardCellStyle(a, b, ctx) {
      // [색상 근본 재정의] 초록=추천(1~2) > 파랑=유력(저배당+긍정신호,5~6) > 빨강=경고(회피,3~4).
      //   나머지(단순 저배당·무변동 등)는 오버레이 없음 = 흰색/중립. 우선순위: 초록 > 파랑 > 빨강.
      var key = Math.min(a, b) + '|' + Math.max(a, b);
      if (ctx.greenSet[key]) return { col: BCOL.rec, tag: '오늘의 추천(복승)', emph: true, lock: true };
      if (ctx.blueSet[key]) return { col: BCOL.fav, tag: (ctx.blueTag[key] || '유력(저배당+신호)'), emph: true };
      if (ctx.redSet[key]) return { col: BCOL.drop, tag: '경고 · ' + (ctx.redTag[key] || '회피'), emph: true, warn: true };
      return null;   // 흰색/중립(무변동·단순저배당 포함) — 테두리·배경 없음(원본 숫자만)
    }

    function removeBoardMatrix() {
      try { var b = byId(BOARD_ID); if (b) b.remove(); } catch (_) { /* */ }
      boardItems = []; boardHdrItems = [];
    }

    // 각 오버레이 셀을 실제 배당판 셀의 현재 화면 좌표(fixed)로 재배치.
    function positionBoard() {
      var i, it, r;
      for (i = 0; i < boardItems.length; i++) {
        it = boardItems[i];
        try { r = it.el.getBoundingClientRect(); } catch (_) { continue; }
        if (!r || (r.width === 0 && r.height === 0)) { it.span.style.display = 'none'; continue; }
        it.span.style.display = 'flex';
        it.span.style.left = r.left + 'px'; it.span.style.top = r.top + 'px';
        it.span.style.width = r.width + 'px'; it.span.style.height = r.height + 'px';
      }
      for (i = 0; i < boardHdrItems.length; i++) {
        it = boardHdrItems[i];
        try { r = it.el.getBoundingClientRect(); } catch (_) { continue; }
        if (!r || (r.width === 0 && r.height === 0)) { it.span.style.display = 'none'; continue; }
        it.span.style.display = 'flex';
        it.span.style.left = r.left + 'px'; it.span.style.top = r.top + 'px';
        it.span.style.width = r.width + 'px'; it.span.style.height = r.height + 'px';
      }
    }
    function schedulePosition() {
      if (boardRaf) return;
      boardRaf = requestAnimationFrame(function () { boardRaf = 0; try { positionBoard(); } catch (_) { /* */ } });
    }
    function bindBoardReposition() {
      if (boardBound) return; boardBound = true;
      try { window.addEventListener('scroll', schedulePosition, true); } catch (_) { /* */ }
      try { window.addEventListener('resize', schedulePosition); } catch (_) { /* */ }
    }

    // 실제 배당판 위에 정렬된 강조 오버레이를 그린다. 성공 시 true(→패널 격자 생략).
    function renderBoardMatrix(d, st) {
      if (!st || !st.ovShowMatrix || !d || !enabled || killed) { removeBoardMatrix(); return false; }
      var info = locateBoardMatrix();
      if (!info) { removeBoardMatrix(); return false; }
      removeBoardMatrix();
      var role = matrixRoles(d);
      var invSet = {}; (((d.inverse || {}).invHorses) || []).forEach(function (n) { invSet[+n] = 1; });
      var smartSet = {}; (d.darkHorses || []).forEach(function (h) { if (h.smartMoney && h.no != null) smartSet[+h.no] = 1; });
      // [흐름 신호] 말별 배당흐름 플래그(app.py _flow_scores → analyze.flowScores):
      //   긍정=급락흐름/역배열/스마트머니 · 경고=죽은인기+고배당/3연속상승/페이크. 키는 숫자/문자 혼용 방어.
      var flow = d.flowScores || {};
      function flowOf(n) { return flow[n] || flow[String(n)] || {}; }
      var WARN_HIGH_ODDS = 10;   // 죽은인기 '고배당' 기준(대표배당 10배+ = 무변동인데 인기 없음 → 회피)

      function ckey(a, b) { return Math.min(a, b) + '|' + Math.max(a, b); }

      // [긍정 신호 말] 역배열 · 스마트머니 · 급락 흐름 → 저배당과 동시일 때만 파랑
      function posHorse(n) {
        var f = flowOf(n);
        return !!(invSet[n] || smartSet[n] || f.smartMoney || f.trend === '급락');
      }
      // [경고 신호 말] 죽은인기+고배당 · 3회+연속상승(자금이탈) · 페이크(급락후반등). 단순 무변동은 제외(회색).
      function warnHorse(n) {
        var f = flowOf(n);
        return !!((f.dead && (Number(f.rep) || 0) >= WARN_HIGH_ODDS) || f.rising3 || f.fake);
      }
      function warnReason(n) {
        var f = flowOf(n);
        if (f.fake) return '페이크(급락후 반등)';
        if (f.rising3) return '3회+ 연속상승(자금이탈)';
        if (f.dead && (Number(f.rep) || 0) >= WARN_HIGH_ODDS) return '죽은인기+고배당';
        return '회피';
      }

      // [급락 조합] 콤보 단위 급락(-20%↓) — 파랑 판정의 긍정신호 중 하나로 사용(색은 파랑, 별도 빨강 아님)
      var dropMap = {};
      (d.drops || []).forEach(function (dd) {
        var c = (dd.combo || []).map(Number);
        if (c.length === 2 && (dd.pct || 0) <= -20) dropMap[ckey(c[0], c[1])] = Math.round(dd.pct);
      });

      // [3번 초록·추천] 복승 추천 조합(finalQuinellas)만 · 최대 2개
      var greenSet = {}, gN = 0;
      ((d.corePicks && d.corePicks.finalQuinellas) || []).forEach(function (q) {
        var c = (q.combo || []).map(Number);
        if (c.length === 2 && gN < 2) { greenSet[ckey(c[0], c[1])] = 1; gN++; }
      });

      // [1번 파랑·유력] 저배당(하위 band) + 긍정신호(급락조합 or 긍정말) 동시만 · 최대 6개. 배당만 낮으면 흰색.
      //   저배당 게이트=하위 40% 지점(신호 있는 저배당 5~6개 확보). 최종 6개컷 → 결과는 전체의 ~20%.
      var oddsAsc = info.cells.map(function (c) { return c.odds; }).sort(function (x, y) { return x - y; });
      var gi = Math.max(0, Math.floor(oddsAsc.length * 0.4) - 1);
      var blueGate = oddsAsc.length ? oddsAsc[Math.min(gi, oddsAsc.length - 1)] : 0;
      var blueSet = {}, blueTag = {};
      info.cells.filter(function (c) {
        var k = ckey(c.a, c.b);
        if (greenSet[k]) return false;
        if (!(c.odds <= blueGate)) return false;                 // 저배당 게이트(배당만 낮고 신호 없으면 제외=흰색)
        return (dropMap[k] != null) || posHorse(c.a) || posHorse(c.b);
      }).sort(function (x, y) { return x.odds - y.odds; }).slice(0, 6)
        .forEach(function (c) {
          var k = ckey(c.a, c.b);
          blueSet[k] = 1;
          blueTag[k] = dropMap[k] != null ? ('유력 · 급락' + dropMap[k] + '%+저배당')
            : (invSet[c.a] || invSet[c.b]) ? '유력 · 역배열+저배당'
              : '유력 · 스마트머니+저배당';
        });

      // [2번 빨강·경고] 경고 말이 낀 조합 · 저배당순(오인베팅 위험 큰 것)부터 최대 4개. 초록/파랑 제외.
      var redSet = {}, redTag = {};
      info.cells.filter(function (c) {
        var k = ckey(c.a, c.b);
        if (greenSet[k] || blueSet[k]) return false;
        return warnHorse(c.a) || warnHorse(c.b);
      }).sort(function (x, y) { return x.odds - y.odds; }).slice(0, 4)
        .forEach(function (c) {
          var k = ckey(c.a, c.b);
          redSet[k] = 1;
          redTag[k] = warnHorse(c.a) ? warnReason(c.a) : warnReason(c.b);
        });

      var ctx = { dropMap: dropMap, greenSet: greenSet, blueSet: blueSet, blueTag: blueTag, redSet: redSet, redTag: redTag };

      // 오버레이 레이어(뷰포트 고정·클릭 통과)
      var layer = mk('div', 'position:fixed;left:0;top:0;width:0;height:0;z-index:2147482800;pointer-events:none');
      layer.id = BOARD_ID;
      root().appendChild(layer);

      // [테두리+반투명 방식] 강조 셀은 테두리 + 얇은 반투명 배경만 → 원본 배당 숫자가 항상 보인다.
      //   미강조 셀은 오버레이 없음(완전 투명). 아이콘은 우측 상단 작은 뱃지로만 표시.
      info.cells.forEach(function (cell) {
        var stl = boardCellStyle(cell.a, cell.b, ctx);
        if (!stl) return;   // [2번 미강조] 테두리 없음·완전 투명
        var lock = stl.lock;
        // 테두리 두께: 초록(추천)4 · 파랑(유력)/빨강(경고)3
        var bw = lock ? 4 : 3;
        // 반투명 배경만: 추천(초록)=15% · 나머지=25% → 원본 숫자 그대로 보임(텍스트 미주입)
        var op = lock ? 0.15 : 0.25;
        var css = 'position:fixed;box-sizing:border-box;overflow:visible;border-radius:4px;'
          + 'background:rgba(' + hexRgb(stl.col) + ',' + op + ');'
          + 'border:' + bw + 'px solid ' + stl.col + ';';
        var span = mk('span', css);   // 텍스트 미주입 → 원본 배당 숫자 유지(색·값 그대로)
        // 우측 상단 모서리 작은 뱃지 — 추천🔒 · 경고⚠️(색은 테두리로 구분·파랑=뱃지없음)
        var badgeTxt = lock ? '🔒' : (stl.warn ? '⚠️' : '');
        if (badgeTxt) {
          span.appendChild(mk('span',
            'position:absolute;top:-8px;right:-6px;font-size:11px;line-height:1;'
            + 'background:#0f172a;border-radius:6px;padding:1px 2px;box-shadow:0 1px 2px rgba(0,0,0,.5)', badgeTxt));
        }
        span.title = cell.a + '-' + cell.b + ' = ' + cell.odds + '배 · ' + stl.tag;
        layer.appendChild(span);
        boardItems.push({ el: cell.el, span: span });
      });

      // [1·3번] 헤더 마번 강조 — 유력마(⭐)·스마트머니(💰)·역배열(🔄)·확실제거(❌)만. 나머지: 숫자만.
      //   유력마 목록(keyHorses) 전체를 별표 대상으로 → 1번 등 어떤 마번이든 유력마면 헤더에 ⭐ 반드시 표시.
      var favSet = {}; (d.keyHorses || []).map(Number).forEach(function (n) { favSet[n] = 1; });
      info.hdrEls.forEach(function (h) {
        var n = h.no, r0 = role[n];
        var isFav = favSet[n], isSmart = smartSet[n], isInv = invSet[n], isCut = (r0 === 'cut');
        if (!isFav && !isSmart && !isInv && !isCut) return;   // [1번] 나머지: 숫자만
        var mark, bc, lbl;
        if (isFav) { mark = '⭐'; bc = BCOL.fav; lbl = '유력'; }
        else if (isSmart) { mark = '💰'; bc = '#c084fc'; lbl = '스마트머니'; }
        else if (isInv) { mark = '🔄'; bc = BCOL.warn; lbl = '역배열'; }
        else { mark = '❌'; bc = BCOL.drop; lbl = '확실제거'; }
        // [5번] 헤더는 아이콘 기준 유지 · 배경색만 반투명(0.30)으로 조정(테두리 3px·강한 그림자로 가독성 유지)
        var css = 'position:fixed;box-sizing:border-box;display:flex;align-items:center;justify-content:center;'
          + 'font:900 17px/1 -apple-system,BlinkMacSystemFont,sans-serif;color:#fff;'
          + 'background:rgba(' + hexRgb(bc) + ',0.30);border:3px solid ' + bc + ';border-radius:5px;'
          + 'text-shadow:0 1px 3px rgba(0,0,0,.95),0 0 2px rgba(0,0,0,.9);overflow:hidden';
        var span = mk('span', css, mark + n);
        span.title = n + '번 · ' + lbl;
        layer.appendChild(span);
        boardHdrItems.push({ el: h.el, span: span });
      });

      positionBoard();
      bindBoardReposition();
      try {
        if (window.ResizeObserver) {
          if (!renderBoardMatrix._ro) renderBoardMatrix._ro = new ResizeObserver(schedulePosition);
          renderBoardMatrix._ro.disconnect();
          renderBoardMatrix._ro.observe(info.table);
        }
      } catch (_) { /* */ }
      return (boardItems.length + boardHdrItems.length) > 0;
    }

    // #rrggbb → "r,g,b" (rgba 배경용)
    function hexRgb(hex) {
      var m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || '');
      return m ? (parseInt(m[1], 16) + ',' + parseInt(m[2], 16) + ',' + parseInt(m[3], 16)) : '59,130,246';
    }

    // 매트릭스 토글 버튼 + 박스를 패널에 추가. open = 팝업/오버레이 플래그(ovShowMatrix).
    function renderMatrix(panel, d, open) {
      if (!d || !Array.isArray(d.quinella) || !d.quinella.length) return;
      var btn = mk('button', 'all:unset;cursor:pointer;display:block;width:100%;box-sizing:border-box;margin:4px 0;padding:5px 8px;border:1px solid #475569;border-radius:7px;color:#7dd3fc;font-weight:700;font-size:12px;text-align:center;background:rgba(56,189,248,.08)',
        (open ? '📊 배당 매트릭스 접기 ▲' : '📊 배당 매트릭스 펼치기 ▼'));
      btn.addEventListener('click', function () {
        matrixOpen = !open;
        try { chrome.storage.local.set({ ovShowMatrix: !open }); } catch (_) { /* */ }   // 팝업 버튼과 동기화
        render();   // 재렌더 → 상태 반영
      });
      panel.appendChild(btn);
      if (open) {
        // 실제 배당판 위에 정렬 오버레이가 떠 있으면(boardActive) 패널 안 격자는 생략하고 안내만 표시.
        if (boardActive) {
          panel.appendChild(mk('div', 'font-size:11px;color:#7dd3fc;margin:3px 0 2px;padding:5px 8px;border:1px dashed #38bdf8;border-radius:6px;background:rgba(56,189,248,.08)',
            '📊 실제 배당판 위 테두리 강조(원본 배당 그대로) · 🔒초록=최종 답 · 빨강=급락 · 파랑=유력 · 노랑=주의'));
        } else {
          var mx = buildMatrix(d);
          if (mx) panel.appendChild(mx);
        }
      }
    }

    // [⏱ 타임라인] 간략 신호 타임라인(시간순 신호 이력) — signalTimeline.changes 우선, 없으면 signals.
    function renderOvTimeline(panel, d) {
      if (!d) return;
      var tl = d.signalTimeline || {};
      var items = [];
      (tl.changes || []).slice(-6).forEach(function (c) {
        items.push((c.reason || '신호 변경') + (c.new_signal != null ? ' → ' + c.new_signal + '번' : ''));
      });
      if (!items.length) {
        (d.signals || []).slice(0, 8).forEach(function (s) {
          items.push((s.type ? '[' + s.type + '] ' : '') + (s.text || ''));
        });
      }
      if (!items.length) return;
      var box = mk('div', 'margin:5px 0;padding:6px 9px;border:1px solid #334155;border-radius:7px;background:rgba(56,189,248,.06)');
      box.appendChild(mk('div', 'font-weight:800;color:#7dd3fc;font-size:12px', '⏱ 신호 타임라인'));
      if (tl.finalSignal != null) {
        box.appendChild(mk('div', 'font-size:11px;color:#38d39f;font-weight:700;margin-top:2px', '현재 신호: ' + tl.finalSignal + '번' + (tl.finalConfirmed ? ' ✅확정' : '')));
      }
      items.forEach(function (t) { box.appendChild(mk('div', 'font-size:11px;color:#cbd5e1;margin-top:2px', '· ' + t)); });
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

        // [4번·경륜 자동수집 상태] 🚴 경륜 자동수집 중 · 오다와라 5경주 · 30초 간격 (content.js 가 storage 에 기록)
        var kas = st.keirinAutoStatus;
        if (kas && kas.active && (Date.now() - (kas.t || 0) < 120000)) {   // 2분내 갱신된 경우만(진행중)
          var ka = mk('div', 'margin:0 0 6px;padding:6px 9px;border-radius:7px;border:1px solid #22d3ee;background:rgba(34,211,238,.14)');
          ka.appendChild(mk('div', 'font-weight:900;font-size:14px;color:#67e8f9', (kas.label || '🚴 경륜') + ' 자동수집 중'));
          if (kas.raceKey) ka.appendChild(mk('div', 'font-weight:800;font-size:15px;color:#e2e8f0;margin-top:2px', kas.raceKey));
          ka.appendChild(mk('div', 'font-size:12px;color:#94a3b8;margin-top:2px', '수집: ' + (kas.intervalSec || 30) + '초 간격 · 경주 바뀌어도 자동 유지'));
          panel.appendChild(ka);
        }

        // ═══ [정보 순서 정리] 1.결론박스 → 2.카운트다운 → 3.유력마 → 4.이상감지 → 5.나머지 ═══
        // [핵심 추천·추천 과다 근본정리] 딱 이것만 — 최종 복승 ≤2 · 삼복승 ≤2 (총 4개)만 크게 표시.
        //   서버 _final_picks가 모든 파생추천(확신도·복병·급락보존·스마트머니·밀집박스)을 4개로 압축(나머지는 숨김).
        var cp = d.corePicks;
        var _fq = (cp && cp.finalQuinellas) || [];
        var _ft = (cp && cp.finalTrifectas) || [];
        // [폴백·구데이터] finalQuinellas 미보유(구 캐시)면 기존 confQuinellas/quinella·삼복승으로 대체
        if (!_fq.length && cp) {
          var _cq0 = cp.confQuinellas || [];
          if (_cq0.length) _fq = _cq0.slice(0, 2);
          else if (cp.quinella && cp.quinella.length === 2) _fq = [{ combo: cp.quinella, odds: cp.quinellaOdds }];
        }
        if (!_ft.length && cp) {
          var _t0 = cp.confTrifecta || cp.trifecta;
          if (_t0) _ft = [{ combo: _t0, odds: cp.confTrifecta ? cp.confTrifectaOdds : cp.trifectaOdds }];
        }
        if (_fq.length && !d.recommendClosed && st.ovShowPicks !== false) {   // [🎯 추천] 팝업 토글(기본 표시)
          var cpBox = mk('div', 'margin:0 0 6px;padding:9px 12px;border:3px solid #38d39f;border-radius:9px;background:rgba(56,211,159,.18)');
          cpBox.appendChild(mk('div', 'font-weight:900;color:#38d39f;font-size:16px', '🎯 지금 사세요!'));
          _fq.slice(0, 2).forEach(function (q) {
            cpBox.appendChild(mk('div', 'font-weight:800;font-size:18px;margin-top:5px;color:#e2e8f0',
              '복승: ' + q.combo.join('+') + (q.odds != null ? '  (' + q.odds + '배)' : '')));
          });
          _ft.slice(0, 2).forEach(function (t) {
            cpBox.appendChild(mk('div', 'font-weight:800;font-size:18px;margin-top:5px;color:#c4b5fd',
              '삼복승: ' + t.combo.join('+') + (t.odds != null ? '  (' + t.odds + '배)' : '')));
          });
          if (cp.confTop1 != null) {
            // [2번] 확신도1위 글씨 키움(16px)
            cpBox.appendChild(mk('div', 'font-weight:800;color:#cbd5e1;font-size:16px;margin-top:7px',
              '확신도1위 ' + cp.confTop1 + '번' + (cp.confTop1High ? '🔺고배당' : '')));
          }
          panel.appendChild(cpBox);
        }
        // [💎 중고배당 유력마·2번] 감지 시 최상단 강조(복승10배+ & 강한신호 → 삼복승 보험 필수)
        var mhf = d.midHighFavorites || [];
        if (mhf.length) {
          var mhBox = mk('div', 'margin:0 0 6px;padding:7px 10px;border:2px solid #f0abfc;border-radius:8px;background:rgba(240,171,252,.16)');
          mhBox.appendChild(mk('div', 'font-weight:800;color:#f0abfc;font-size:14px', '💎 고배당 유력마 감지!'));
          mhf.slice(0, 3).forEach(function (m) {
            var line = mk('div', 'font-weight:700;color:#f5d0fe;font-size:13px;margin-top:2px');
            line.textContent = m.no + '번 (' + m.odds + '배) ' + ((m.sigTypes || []).join('·')) + ' → 삼복승 보험 필수';
            mhBox.appendChild(line);
          });
          panel.appendChild(mhBox);
        }
        // [복병 등급·2번] ★★★ 스마트머니 복병 → 고배당 복승 강조 배너
        var dhH = d.darkHighlight;
        if (dhH && dhH.quinella && !d.recommendClosed) {
          var dhb = mk('div', 'margin:0 0 6px;padding:8px 11px;border:2px solid #f472b6;border-radius:9px;background:rgba(244,114,182,.18)');
          dhb.appendChild(mk('div', 'font-weight:900;color:#f9a8d4;font-size:14px', dhH.message || '💰 스마트머니 복병 → 고배당 가능!'));
          var dq = mk('div', 'font-weight:800;font-size:16px;margin-top:3px;color:#e2e8f0');
          dq.textContent = '복승: ' + dhH.quinella.join('+') + (dhH.quinellaOdds != null ? ' (' + dhH.quinellaOdds + '배)' : '') + (dhH.cases > 0 ? '  · 유사 ' + dhH.cases + '회 적중' : '');
          dhb.appendChild(dq);
          panel.appendChild(dhb);
        }
        // [📊 배당 매트릭스] ① 실제 배당판 위 정렬 오버레이(1:1 겹침·큰글씨·3중강조) 우선 시도
        boardActive = renderBoardMatrix(d, st);
        //  ② 패널 토글 버튼(+ 배당판 못 찾으면 패널 안 간략 격자 폴백)
        renderMatrix(panel, d, !!st.ovShowMatrix);
        // [⏱ 타임라인] 팝업 [⏱ 타임라인] 버튼 켜짐 시 신호 타임라인 표시
        if (st.ovShowTimeline) renderOvTimeline(panel, d);

        // [1번] 최종 결론 박스 — 최상단·가장 크게(모든 종목 공통: 경마/경륜/경정)
        renderConclusion(panel, d, deadline);

        // [1번] 마감 카운트다운 — 마감 3분 이내 빨강배경+24px↑ · 5분 이내 노랑배경+20px · 그 외 기본. T-30초 깜빡임.
        var cd = countdown(deadline);
        if (cd) {
          var leftMs = deadline ? (deadline - Date.now()) : 0;
          var within3 = leftMs > 0 && leftMs <= 180000;   // 마감 3분 이내
          var within5 = leftMs > 0 && leftMs <= 300000;   // 마감 5분 이내
          var phase = (leftMs > 0 && leftMs <= 30000) ? 'blink'
            : (leftMs > 0 && leftMs <= 60000) ? 'red'
            : (leftMs > 0 && leftMs <= 120000) ? 'orange' : '';
          // 크기·색상 — 3분↓ 26px 빨강 / 5분↓ 20px 노랑 / 그외 14px 기본
          var cdFs = within3 ? 26 : (within5 ? 20 : 14);
          var numCol = (cd === '마감') ? '#fecaca' : within3 ? '#fee2e2' : within5 ? '#fde68a' : '#fbbf24';
          var cdBg = within3 ? 'rgba(220,38,38,.38)' : (within5 ? 'rgba(245,158,11,.30)' : '');
          var cdBd = within3 ? '#ef4444' : (within5 ? '#fbbf24' : '');
          var boxed = within5 || phase;
          var cdRow = mk('div', 'margin:2px 0 6px;display:flex;align-items:center;flex-wrap:wrap;' +
            (boxed ? 'padding:7px 10px;border-radius:8px;' : '') +
            (cdBg ? ('background:' + cdBg + ';') : '') + (cdBd ? ('border:2px solid ' + cdBd + ';') : ''));
          cdRow.id = 'kbOvCd';
          cdRow.appendChild(mk('span', 'color:#cbd5e1;font-size:' + (within3 ? 15 : 13) + 'px', '⏰ 마감까지 '));
          cdRow.appendChild(mk('span', 'font-weight:900;font-size:' + cdFs + 'px;line-height:1.1;color:' + numCol, cd));
          if (phase === 'orange') cdRow.appendChild(mk('span', 'margin-left:8px;font-size:13px;font-weight:800;color:#fbbf24', 'T-2분'));
          if (phase === 'red') cdRow.appendChild(mk('span', 'margin-left:8px;font-size:13px;font-weight:800;color:#fecaca', 'T-1분 임박!'));
          if (phase === 'blink') cdRow.appendChild(mk('span', 'margin-left:8px;font-size:13px;font-weight:900;color:#fecaca', '⚡ 30초!'));
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
          // [2번] 유력마 글씨 키움(18px)
          var kr = mk('div', 'margin:4px 0;display:flex;align-items:baseline;gap:4px');
          kr.appendChild(mk('span', 'color:#94a3b8;font-size:14px', '⭐ 유력마 '));
          kr.appendChild(mk('span', 'font-weight:800;font-size:18px;color:#4ea1ff', baseKeys.slice(0, 3).join(' · ')));
          panel.appendChild(kr);
        }
        // [1번·복병 정리] 복병 최대 3두 + 우선순위: ①스마트머니+집중급락 동시 ②집중급락 횟수 많은 순 ③역배열 감지 말.
        //   기존 6두 나열 → 상위 3두만(가장 강한 신호). 후보를 점수화해 정렬 후 상위 3두 렌더.
        var darkCands = [], darkSeen = {};
        (d.darkHorses || []).forEach(function (h) {
          if (h.no == null || !inV(h.no) || baseKeys.indexOf(Number(h.no)) >= 0 || darkSeen[h.no]) return;
          darkSeen[h.no] = 1;
          var anom = Number(h.anomCount || 0), smart = !!h.smartMoney, forced = !!h.forced;
          var pr = (smart && (forced || anom >= 10)) ? 3 : (forced || anom >= 10) ? 2 : smart ? 2 : 1;  // ①동시=3 ②집중급락=2
          var st = h.stars || pr;   // [복병 등급] 서버 ★ 등급 우선
          darkCands.push({ no: Number(h.no), pr: st, stars: st, tierLabel: h.tierLabel, anom: anom, smart: smart,
            tag: (h.tierReason || h.note || '집중급락'), conf: h.confidence,
            col: (st >= 3) ? '#f472b6' : (st === 2 ? '#c084fc' : '#a78bfa') });
        });
        // ③ 역배열 감지 말(우선순위 최하)
        if (d.inverse && d.inverse.detected && d.inverse.invLead && d.inverse.invLead.no != null) {
          var ino = Number(d.inverse.invLead.no);
          if (inV(ino) && baseKeys.indexOf(ino) < 0 && !darkSeen[ino]) {
            darkSeen[ino] = 1;
            darkCands.push({ no: ino, pr: 1, anom: 0, smart: false, tag: '역배열', col: '#f0abfc' });
          }
        }
        // 급락 복병(후보 3두 미만일 때만 보충) — 이상감지말/최대급락 조합 중 유력마 아닌 말
        if (darkCands.length < 3) {
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
          if (dropDark != null && !darkSeen[dropDark]) {
            darkSeen[dropDark] = 1;
            darkCands.push({ no: dropDark, pr: 1, anom: 0, smart: false, tag: '급락', col: '#f87171' });
          }
        }
        // 정렬: 우선순위 desc → 집중급락 횟수 desc → 마번 asc. 상위 3두만 표시.
        darkCands.sort(function (a, b) { return (b.pr - a.pr) || (b.anom - a.anom) || (a.no - b.no); });
        var darkTop = darkCands.slice(0, 3);
        darkTop.forEach(function (h) {
          // [2번] 복병 글씨 키움(마번·급락 18px)
          var db = mk('div', 'margin:3px 0;display:flex;align-items:baseline;gap:3px;flex-wrap:wrap');
          db.appendChild(mk('span', 'color:#94a3b8;font-size:14px', '🐎 복병 '));
          if (h.tierLabel) db.appendChild(mk('span', 'font-weight:800;font-size:13px;color:' + h.col, h.tierLabel + ' '));   // [복병 등급] ★★★/★★/★
          db.appendChild(mk('span', 'font-weight:900;font-size:18px;color:' + h.col, h.no + '번'));
          var note = h.tag + (h.conf === '높음' ? ' · 신뢰↑' : '');
          db.appendChild(mk('span', 'font-size:14px;font-weight:800;color:' + h.col, note));
          panel.appendChild(db);
        });

        // [복승·쌍승 크로스 역배열] 강한(복승/쌍승 0.5+) 크로스 역배열 말 강조: "🔴 크로스 역배열 13번 복0.72·쌍0.61 🔁양쪽".
        (d.crossReversal || []).filter(function (c) {
          return c.score >= 0.5 || (c.qScore || 0) >= 0.5 || (c.xScore || 0) >= 0.5;
        }).slice(0, 2).forEach(function (c) {
          var col = (c.level === '🔴' || c.both) ? '#f87171' : '#fbbf24';
          var cx = mk('div', 'margin:3px 0;padding:4px 8px;border-left:3px solid ' + col + ';background:rgba(250,204,21,.12);border-radius:6px');
          var qx = (c.qScore != null || c.xScore != null)
            ? ' (복' + (c.qScore != null ? c.qScore : '-') + '·쌍' + (c.xScore != null ? c.xScore : '-') + ')' : (' ' + c.score);
          cx.appendChild(mk('span', 'font-weight:800;color:' + col, c.level + ' 크로스 역배열 ' + c.no + '번' + qx + (c.both ? ' 🔁양쪽' : '')));
          if ((c.refs || []).length) cx.appendChild(mk('span', 'margin-left:6px;font-size:11px;color:#fde68a', '→ ' + c.refs.join('·') + '번 1착 시 2착 강력'));
          panel.appendChild(cx);
        });
        // [2번·스마트머니 복승 보조] 서버가 편성한 스마트머니 복승 보조를 강조 표시("복승 추가: 2+10 (스마트머니)").
        (d.smartQuinella || []).forEach(function (sq) {
          if (!sq || !sq.combo || sq.combo.length !== 2) return;
          var sr = mk('div', 'margin:3px 0;padding:4px 8px;border-left:3px solid #fbbf24;background:rgba(251,191,36,.15);border-radius:6px');
          sr.appendChild(mk('span', 'font-weight:800;color:#fcd34d', '💰 복승 추가: ' + sq.combo.join('+')));
          sr.appendChild(mk('span', 'margin-left:6px;font-size:11px;color:#fde68a', '(스마트머니' + (sq.odds != null ? ' · ' + sq.odds + '배' : '') + ')'));
          panel.appendChild(sr);
        });

        // [3번·중복 제거] 📊 시장 유력(전적 미수집) — 저배당(5배↓)이라 유력마 편입된 말.
        //   이미 유력마(baseKeys)/복병(darkSeen)에 있으면 제외(중복 표시 방지).
        (d.marketFavorites || []).filter(function (m) {
          return m.formMissing && inV(m.no) && baseKeys.indexOf(Number(m.no)) < 0 && !darkSeen[m.no];
        }).forEach(function (m) {
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
          try { removeBoardMatrix(); } catch (_) { /* */ }   // [배당판 위 정렬 오버레이] 정리
          boardActive = false;
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
          // [오버레이 표시 제어] 팝업 📊/🎯/⏱ 버튼 변경 시 즉시 재렌더
          if ((ch.ovShowMatrix || ch.ovShowPicks || ch.ovShowTimeline) && enabled && !killed) render();
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
