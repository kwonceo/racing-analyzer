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

    // ── [분석 자동화] 자동전송(autoSend) 여부와 무관하게 주기적으로 서버 분석을 요청 → analyzeStatus 갱신 →
    //    수동 버튼 없이 추천이 자동으로 뜬다. 서버 bg 수집(oddspark)·확장 수집 무엇이든 배당만 있으면 분석 자동.
    //    마감 후에는 서버가 afterClose 로 처리(추천 미반영). 오버레이 꺼져 있으면 생략(자원 절약).
    var _ovAnalyzeTimer = null;
    function pollOverlayAnalyze() {
      if (killed) return;
      try {
        chrome.storage.local.get({ raceKey: '', overlayEnabled: false }, function (v) {
          if (!v || !v.overlayEnabled) return;
          chrome.runtime.sendMessage({ type: 'ANALYZE_TRIPLE', raceKey: (v.raceKey || '') }, function (res) {
            try {
              if (chrome.runtime.lastError || !res || !res.ok || !res.data) return;
              chrome.storage.local.set({ analyzeStatus: { data: res.data, at: Date.now() } });   // → storage.onChanged 로 자동 재렌더
            } catch (_) { /* */ }
          });
        });
      } catch (_) { /* */ }
    }
    function startOverlayAnalyzePoll() {
      if (_ovAnalyzeTimer) return;
      pollOverlayAnalyze();                                       // 즉시 1회(첫 수집분 분석)
      _ovAnalyzeTimer = setInterval(pollOverlayAnalyze, 10000);   // [수정4] 이후 10초마다 자동 재분석(배당 업데이트 반영·기존 20초→단축)
    }

    // ── [배당판 스냅샷] 3단계 자동(T-10·T-2·마감후) + 수동 📸: 배당판+오버레이+패널 캡처 → 워터마크 합성 → 서버 저장 ──
    var _snapStage = {};   // raceKey별 단계 캡처 플래그 { t10, t2, close }(단계별 1회)
    var _snapBusy = false;
    var _lastTransitionRk = '';   // [경주 전환 클리어] 마지막으로 즉시 재분석 트리거한 새 경주(중복 트리거 방지)
    // 단계 트리거 → 파일명 접미 · 워터마크 라벨(스냅샷 캡처 3단계)
    var _SNAP_SUFFIX = { 'T-10': 'T-10', 'T-2': 'T-2', 'close': '마감후', 'auto_t1': '마감1분전', 'manual': '수동' };
    var _SNAP_LABEL = { 'T-10': '마감10분전', 'T-2': '마감2분전', 'close': '마감직후', 'auto_t1': '마감1분전', 'manual': '수동캡처' };
    function _snapToast(m, ok) {
      try {
        var t = mk('div', 'position:fixed;left:50%;bottom:64px;transform:translateX(-50%);z-index:2147483600;'
          + 'padding:10px 18px;border-radius:10px;font:800 15px/1 -apple-system,BlinkMacSystemFont,sans-serif;color:#fff;'
          + 'background:' + (ok === false ? '#dc2626' : '#0f766e') + ';box-shadow:0 4px 14px rgba(0,0,0,.55);pointer-events:none', m);
        root().appendChild(t);
        setTimeout(function () { try { t.remove(); } catch (_) { /* */ } }, 2600);
      } catch (_) { /* */ }
    }
    function _snap2(n) { return (n < 10 ? '0' : '') + n; }
    function _snapFilename(rk, trigger) {
      var now = new Date();
      var ymd = now.getFullYear() + '_' + _snap2(now.getMonth() + 1) + '_' + _snap2(now.getDate());
      var name = (rk || '경주').replace(/[\\/:*?"<>|]/g, '').replace(/\s+/g, '_').slice(0, 40);
      return ymd + '_' + name + '_' + (_SNAP_SUFFIX[trigger] || '수동') + '.png';
    }
    // 캡처 실행: background(CAPTURE_BOARD)로 화면 캡처 → 캔버스에 워터마크(경주명·날짜·시간) → base64 → SAVE_BOARD_SNAPSHOT
    function captureBoardSnapshot(d, trigger) {
      if (_snapBusy || killed) return;
      _snapBusy = true;
      var rk = (d && d.raceKey) || '';
      var silentFail = (trigger !== 'manual');   // 자동 단계(T-10/T-2/마감후)는 실패 토스트 생략(탭 비활성 시 조용히)
      try {
        chrome.runtime.sendMessage({ type: 'CAPTURE_BOARD' }, function (resp) {
          if (!resp || !resp.ok || !resp.dataUrl) {
            _snapBusy = false; if (!silentFail) _snapToast('📸 캡처 실패', false); return;
          }
          var img = new Image();
          img.onload = function () {
            try {
              var cv = document.createElement('canvas');
              cv.width = img.naturalWidth; cv.height = img.naturalHeight;
              var ctx = cv.getContext('2d');
              ctx.drawImage(img, 0, 0);
              // [워터마크] 좌하단 반투명 바에 경주명 · 날짜 시간 · 트리거
              var now = new Date();
              var stamp = now.getFullYear() + '-' + _snap2(now.getMonth() + 1) + '-' + _snap2(now.getDate())
                + ' ' + _snap2(now.getHours()) + ':' + _snap2(now.getMinutes());
              var label = (rk ? rk + '  ·  ' : '') + stamp + '  ·  ' + (_SNAP_LABEL[trigger] || '수동캡처');
              var fs = Math.max(16, Math.round(cv.width / 55));
              ctx.font = '700 ' + fs + 'px -apple-system,BlinkMacSystemFont,sans-serif';
              var tw = ctx.measureText(label).width, pad = Math.round(fs * 0.5);
              ctx.fillStyle = 'rgba(15,23,42,0.80)';
              ctx.fillRect(0, cv.height - fs - pad * 2, tw + pad * 2, fs + pad * 2);
              ctx.fillStyle = '#fde68a'; ctx.textBaseline = 'top';
              ctx.fillText(label, pad, cv.height - fs - pad);
              var b64 = cv.toDataURL('image/png');
              var cp = (d && d.corePicks) || {};
              chrome.runtime.sendMessage({
                type: 'SAVE_BOARD_SNAPSHOT',
                payload: {
                  filename: _snapFilename(rk, trigger), image: b64, raceKey: rk, trigger: trigger,
                  quinellas: cp.finalQuinellas || [], trifectas: cp.finalTrifectas || [],
                  special: cp.bmedSpecial || [], minOdds: cp.dansungMinOdds, dansung: !!cp.dansung
                }
              }, function (r2) {
                _snapBusy = false;
                if (r2 && r2.ok) _snapToast('📸 저장됨');
                else if (!silentFail) _snapToast('📸 저장 실패', false);
              });
            } catch (e) { _snapBusy = false; if (!silentFail) _snapToast('📸 처리 실패', false); }
          };
          img.onerror = function () { _snapBusy = false; if (!silentFail) _snapToast('📸 이미지 오류', false); };
          img.src = resp.dataUrl;
        });
      } catch (e) { _snapBusy = false; }
    }

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
          chrome.storage.local.get({ analyzeStatus: null, timerDeadline: 0, collectAlert: null, raceKey: '',
            ovShowMatrix: false, ovShowPicks: true, ovShowTimeline: false, keirinAutoStatus: null, autoFallback: null, koreaAuto: null,
            detectedCategory: '' }, function (v) {
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

    // [팝업 위치 변경] 화면 중앙 자동 팝업 완전 제거 → 오버레이 패널 상단 '배지' + 클릭 시 상세 펼침.
    //   배당판을 가리지 않도록 패널 안에만 배지 표시. 배지 클릭으로 상세(강도·실질유력·배당차이) 토글, ✕로 닫기.
    //   기존 computeCritical(신호 산출)·crit.lines(역배열 상세)는 그대로 재사용(무삭제) — 렌더 위치/방식만 변경.
    function renderAlertPopup(crit) {
      try {
        var el = byId(ID_ALERT);
        if (!crit || !enabled || killed || alertDismissed === crit.key) {
          if (el) el.remove(); stopBlink();
          if (!crit) lastSoundKey = '';   // 신호 해제 → 다음 신호에 다시 알림음
          return;
        }
        var panel = byId(ID_PANEL);
        if (!panel) { if (el) el.remove(); return; }   // 패널 없으면 표시 안 함(자동 화면 팝업 제거)
        if (soundOn && crit.key !== lastSoundKey) { beep(); }
        lastSoundKey = crit.key;
        var col = crit.level === 'red' ? '#dc2626' : '#f59e0b';
        var wasExp = el && el.getAttribute('data-exp') === '1';
        if (!el) {
          el = mk('div', 'margin:0 0 8px;border-radius:10px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.35)');
          el.id = ID_ALERT;
        }
        // 항상 패널 최상단에 위치(재렌더 시에도 맨 위)
        if (el.parentNode !== panel || panel.firstChild !== el) {
          try { panel.insertBefore(el, panel.firstChild); } catch (_) { panel.appendChild(el); }
        }
        while (el.firstChild) el.removeChild(el.firstChild);
        // ── 배지(클릭 토글) — "🔴 역배열 감지 · 클릭" ──
        var badge = mk('div', 'cursor:pointer;color:#fff;font-weight:900;font-size:14px;padding:9px 12px;'
          + 'background:' + col + ';display:flex;align-items:center;justify-content:space-between;gap:8px');
        badge.appendChild(mk('span', '', crit.icon + ' ' + crit.title + ' · 클릭'));
        var arrow = mk('span', 'font-size:12px', wasExp ? '▲' : '▼');
        badge.appendChild(arrow);
        el.appendChild(badge);
        // ── 상세(펼침 시만) — 강도·실질유력·배당차이 ──
        var detail = mk('div', 'display:' + (wasExp ? 'block' : 'none')
          + ';background:#1f2937;color:#fff;padding:10px 12px;font-size:13px;font-weight:700;line-height:1.5');
        if (crit.lines && crit.lines.length) {
          crit.lines.forEach(function (ln) {
            var sep = (ln.indexOf('→') === 0 || ln.indexOf('강도:') === 0);
            detail.appendChild(mk('div', sep ? 'margin-top:4px;font-weight:800' : '', ln));
          });
        } else {
          detail.appendChild(mk('div', '', crit.msg));
        }
        var closeBtn = mk('button', 'all:unset;cursor:pointer;margin-top:8px;color:#fca5a5;font-size:13px;font-weight:800', '✕ 닫기');
        closeBtn.addEventListener('click', function (e2) {
          e2.stopPropagation();
          alertDismissed = crit.key;
          var e3 = byId(ID_ALERT); if (e3) e3.remove();
        });
        detail.appendChild(closeBtn);
        el.appendChild(detail);
        el.setAttribute('data-exp', wasExp ? '1' : '0');
        badge.addEventListener('click', function () {
          var now = el.getAttribute('data-exp') === '1';
          el.setAttribute('data-exp', now ? '0' : '1');
          detail.style.display = now ? 'none' : 'block';
          arrow.textContent = now ? '▼' : '▲';
        });
        // 화면 깜빡임(startBlink) 미사용 — 배당판 방해 없이 조용히 패널 배지로만 표시
      } catch (_) { /* 강조 배지 실패는 무시 */ }
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
        // [오버레이-패널 통일 2단계] '지금 사세요' 박스도 패널·배당판 강조와 동일한 corePicks.finalQuinellas 1순위를
        //   우선 사용(+현재 배당 표기 — 10초 재분석마다 갱신). 없으면 기존 betRecommend 경로 폴백(무삭제).
        var _cp = d.corePicks || {};
        var _cfq = _cp.finalQuinellas || [];
        if (_cfq.length && _cfq[0].combo && _cfq[0].combo.length === 2) {
          quinella = _cfq[0].combo.join('+') + (_cfq[0].odds != null ? ' (' + _cfq[0].odds + '배)' : '');
        }
        var _cft = _cp.finalTrifectas || [];
        if (_cft.length && _cft[0].combo && _cft[0].combo.length === 3) {
          trio = _cft[0].combo.join('+') + (_cft[0].odds != null ? ' (' + _cft[0].odds + '배)' : '');
        }
        var recs = d.betRecommend || [], main = null;
        for (var i = 0; i < recs.length; i++) {
          if (recs[i].label && recs[i].label.indexOf('복승 메인') === 0) { main = recs[i]; break; }
        }
        if (!main && recs.length) main = recs[0];
        // [수익성 3분류 (2026-07-19)] 저배당 경주는 복승 폴백 금지 — 삼복승 집중 표기
        var _ptLow = ((_cp.profitTier || {}).tier === 'low');
        if (!quinella && !_ptLow && main && main.combo && main.combo.length) quinella = main.combo.join('+');
        if (_ptLow && !quinella && trio) trio += ' (저배당 경주 — 삼복승 집중)';
        var trios = (d.trioRecommend || []).filter(function (t) { return t && t.combo && t.combo.length === 3; });
        if (!trio && trios.length) trio = trios[0].combo.join('+');
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
        if (c.length === 2 && (dd.pct || 0) <= -10) dropMap[Math.min(c[0], c[1]) + '|' + Math.max(c[0], c[1])] = Math.round(dd.pct);
      });
      // [빨강 근본 수정·패널격자] 콤보급락(dropMap) AND 급락 말(flow 급락/스마트머니·anomalyHorse) 1+ AND 유력마(role fav) 1+ → 최대 3개.
      //   보드 오버레이와 동일 게이트 — 고배당 노이즈·유력마 무관 조합 빨강 도배 제거.
      var _pflow = d.flowScores || {};
      var _pDropH = {};
      Object.keys(_pflow).forEach(function (n) { var f = _pflow[n] || {}; if (f.trend === '급락' || f.trend === '스마트머니') _pDropH[+n] = 1; });
      if (d.anomalyHorse != null) _pDropH[+d.anomalyHorse] = 1;
      var _pRedC = [];
      Object.keys(dropMap).forEach(function (k) {
        var pp = k.split('|');
        if (!_pDropH[+pp[0]] && !_pDropH[+pp[1]]) return;
        if (role[+pp[0]] !== 'fav' && role[+pp[1]] !== 'fav') return;   // 유력마 무관 조합 = 빨강 금지(급락이어도)
        _pRedC.push({ k: k, pct: dropMap[k] });
      });
      _pRedC.sort(function (a, b) { return a.pct - b.pct; });
      var redSet = {};
      _pRedC.slice(0, 3).forEach(function (c) { redSet[c.k] = 1; });
      var recSet = {};
      // [오버레이-패널 통일 2단계] 간이 매트릭스 초록테도 패널·배당판 강조와 동일한 corePicks.finalQuinellas 기준.
      //   finalQuinellas 비면 기존 betRecommend 폴백(구데이터 호환·무삭제) — app.py _pub_matrix 초록과 동일 규칙.
      var _rfq = (d.corePicks && d.corePicks.finalQuinellas) || [];
      _rfq.forEach(function (q) {
        var c = (q.combo || []).map(Number);
        if (c.length === 2) recSet[Math.min(c[0], c[1]) + '|' + Math.max(c[0], c[1])] = 1;
      });
      if (!Object.keys(recSet).length) (d.betRecommend || []).forEach(function (b) {
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
      // [행열 정렬 수정] 실제 복승 배당판과 동일한 기준으로 통일: **행 = 작은 말번호 · 열 = 큰 말번호**(우상 삼각).
      //   예) 3+4 조합 → 3행 4열. 기존 격자는 행=큰번호·열=작은번호(좌하 삼각)라 실제 배당판과 전치돼 보였다.
      //   ⚠ 배당값 조회 키(om)는 min|max 정규화라 값 자체는 동일 — 표시 좌표만 배당판 기준으로 맞춘다.
      // 헤더 행: 열 = 큰 말번호(nos[1..n-1])
      var hRow = mk('div', 'display:flex');
      hRow.appendChild(mk('span', cellBase('color:#64748b'), '복승'));
      for (var ci0 = 1; ci0 < nos.length; ci0++) hRow.appendChild(hdrCell(nos[ci0]));
      grid.appendChild(hRow);
      // 본문 삼각: 행 = 작은 말번호(nos[0..n-2])
      for (var ri = 0; ri < nos.length - 1; ri++) {
        var row = mk('div', 'display:flex');
        row.appendChild(hdrCell(nos[ri]));
        for (var ci = 1; ci < nos.length; ci++) {
          if (ci < ri) { row.appendChild(mk('span', cellBase(''), '')); continue; }        // 하단 거울면 = 공백(정렬 유지)
          if (ci === ri) { row.appendChild(mk('span', cellBase('color:#475569'), '—')); continue; }  // 대각선(같은 말)
          var key = Math.min(nos[ri], nos[ci]) + '|' + Math.max(nos[ri], nos[ci]);
          var v = om[key];
          if (v > 0) {
            var bd = '';
            // [색상 우선순위 초록(추천)>파랑(유력)>빨강(급락)] 추천조합 항상 우선 — 급락이 추천/유력을 덮지 않음
            if (recSet[key]) bd = 'box-shadow:inset 0 0 0 2px #22c55e;';                                       // 초록=추천(최우선)
            else if (role[nos[ri]] === 'fav' && role[nos[ci]] === 'fav') bd = 'box-shadow:inset 0 0 0 2px #3b82f6;'; // 파랑=유력×유력
            else if (redSet[key]) bd = 'box-shadow:inset 0 0 0 2px #ef4444;';                                  // 빨강=진짜 급락(급락말 포함·최대3)만
            var inv = (invSet[nos[ri]] || invSet[nos[ci]]) ? 'outline:2px solid ' + MX_COL.inv + ';outline-offset:-3px;' : '';
            var cell = mk('span', cellBase('color:#e2e8f0;background:' + mxHeat(v, lo, hi) + ';' + bd + inv), v + (redSet[key] ? '▼' : ''));
            // 툴팁도 배당판 기준(작은번호-큰번호)으로 표기
            cell.title = nos[ri] + '-' + nos[ci] + ' = ' + v + '배' + (dropMap[key] != null ? ' · 급락 ' + dropMap[key] + '%' : '') + (recSet[key] ? ' · 추천' : '');
            row.appendChild(cell);
          } else row.appendChild(mk('span', cellBase('color:#475569'), '·'));
        }
        grid.appendChild(row);
      }
      wrap.appendChild(grid);
      // 범례
      wrap.appendChild(mk('div', 'font-size:9px;color:#94a3b8;margin-top:3px',
        '추천(초록테) · 유력×유력(파랑테) · ▼급락(빨강테) · ⭐유력 · 🟣복병 · ❌제거 · 역배열(노랑테)'));
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
    var boardRO = null;       // [밀림 방지] 배당판 표 크기·레이아웃 변화 감시(ResizeObserver)
    var BCOL = { fav: '#3b82f6', drop: '#ef4444', rec: '#22c55e', warn: '#eab308', special: '#f0abfc' };

    function pureIntT(s) { s = (s == null ? '' : String(s)).trim(); return /^\d{1,2}$/.test(s) ? parseInt(s, 10) : null; }
    // [신규 미러 사이트 대응] 점 붙은 머리글('1.')도 마번 인식(헤더 전용).
    function hdrIntT(s) { var m = /^(\d{1,2})\.?$/.exec((s == null ? '' : String(s)).trim()); return m ? parseInt(m[1], 10) : null; }
    function numT(s) { var m = (s == null ? '' : String(s)).replace(/[, ]/g, '').match(/\d+(\.\d+)?/); return m ? parseFloat(m[0]) : null; }

    // 실제 배당판(복승 매트릭스) 표 + 셀 요소 위치 매핑. content.js parseMatrixTable 과 동일 휴리스틱(읽기전용).
    function mapBoardTable(table, oddsClass) {
      // [자체 배당판 제외] 분석기 페이지(127.0.0.1:8011)에도 overlay.js 가 주입되는데, 거기의 '📊 배당판' 탭
      //   (matrix_board.js)이 그리는 표는 마번 헤더+소수 배당 구조라 실제 배당판으로 오인된다.
      //   그 표 위에 (다른 경주의) 강조 셀이 겹쳐 그려지는 것을 막는다. 실제 배당판(asyukk/keiba)엔 이 표식이 없다.
      try { if (table.getAttribute && table.getAttribute('data-mb-board')) return null; } catch (_) { /* */ }
      var rows; try { rows = [].slice.call(table.rows); } catch (_) { return null; }
      if (rows.length < 2) return null;
      var headerRow = null, best = 1;
      rows.slice(0, 3).forEach(function (r) {
        var c = [].slice.call(r.cells).filter(function (td) { return hdrIntT(td.textContent) != null; }).length;
        if (c > best) { best = c; headerRow = r; }
      });
      if (!headerRow) return null;
      var headerNos = [], hdrEls = [], colByIdx = {};
      [].slice.call(headerRow.cells).forEach(function (cell) {
        var n = hdrIntT(cell.textContent);
        if (n != null) { headerNos.push(n); hdrEls.push({ no: n, el: cell }); colByIdx[cell.cellIndex] = n; }
      });
      if (headerNos.length < 2) return null;
      var _odCell = function (s) { var t = (s || '').trim(); return /^\d+\.\d+$/.test(t) || /^\d{3,}$/.test(t); }; // 소수 또는 100↑(상한)
      var isOdds = oddsClass
        ? function (td) { return (td.classList && td.classList.contains(oddsClass)) || _odCell(td.textContent); }
        : function (td) { return _odCell(td.textContent); };

      // ═══ [셀 밀림 근본 수정] 열 번호를 '개수 추측'이 아니라 **기하 정렬**로 확정 ═══
      //   기존 로직은 배당 셀 개수가 all/upper/lower 중 무엇과 같은지로 열을 단정했다.
      //   그 추측이 한 번 빗나가면 **그 행의 모든 셀이 통째로 밀린다**(빈칸·출주취소·colspan·
      //   삼각 방향이 예상과 다르면 즉시 발생. upper 를 lower 보다 먼저 검사해 두 개수가 같은
      //   행에선 무조건 upper 로 단정하는 문제도 있었다).
      //   → 각 배당 셀의 **가로 중심이 어느 헤더(마번) 셀의 가로 범위에 들어가는지**로 열을 정한다.
      //     실제 화면 좌표(getBoundingClientRect) 기반이라 두수(7·8·10·18두)·삼각 방향·빈칸과
      //     무관하게 항상 정확하다. 기하 측정이 불가할 때만 기존 개수 추측으로 폴백(무삭제).
      var _hdrBox = [];
      hdrEls.forEach(function (h) {
        var hr; try { hr = h.el.getBoundingClientRect(); } catch (_) { return; }
        if (!hr || hr.width <= 0) return;
        _hdrBox.push({ no: h.no, left: hr.left, right: hr.right, cx: hr.left + hr.width / 2 });
      });
      // 헤더가 화면에 안 잡히면(숨김·미렌더) 기하 매핑 불가 → 폴백 사용
      var _geo = _hdrBox.length >= 2 ? function (td) {
        var r; try { r = td.getBoundingClientRect(); } catch (_) { return null; }
        if (!r || r.width <= 0) return null;
        var cx = r.left + r.width / 2, best = null, bd = 1e9;
        for (var i = 0; i < _hdrBox.length; i++) {
          if (cx >= _hdrBox[i].left && cx <= _hdrBox[i].right) return _hdrBox[i].no;   // 헤더 열 범위 안 = 확정
          var dd = Math.abs(cx - _hdrBox[i].cx);
          if (dd < bd) { bd = dd; best = _hdrBox[i]; }
        }
        // 어떤 헤더 범위에도 안 들면 가장 가까운 헤더 — 단, 셀 폭 이내일 때만(그 이상 어긋나면 매핑 포기)
        return (best && bd <= r.width) ? best.no : null;
      } : null;

      var cells = [];
      rows.forEach(function (r) {
        if (r === headerRow) return;
        var rc = [].slice.call(r.cells), rowNo = null;
        for (var i = 0; i < rc.length; i++) { var n = pureIntT(rc[i].textContent); if (n != null) { rowNo = n; break; } }
        if (rowNo == null) return;
        var oddsCells = rc.filter(isOdds);
        if (!oddsCells.length) return;
        if (_geo) {
          // [1순위] 기하 정렬 — 셀 하나하나를 실제 화면 위치로 헤더 열에 대응(밀림 원천 차단)
          oddsCells.forEach(function (oc) {
            var colNo = _geo(oc), val = numT(oc.textContent);
            if (colNo != null && colNo !== rowNo && val != null && val >= 1.0) cells.push({ a: rowNo, b: colNo, odds: val, el: oc });
          });
          return;
        }
        // [폴백·기존 로직 보존] 기하 측정 불가 시에만 개수 추측 → cellIndex 순으로 대응
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

    // [프레임 대응] 동일출처 iframe/frame(예: 사설 배당판 frm_race_run) 내부 <table>까지 수집(중첩 재귀).
    function _allBoardDocs() {
      var docs = [document], seen = [document];
      function dig(root) {
        var fr; try { fr = root.querySelectorAll('iframe, frame'); } catch (_) { return; }
        for (var i = 0; i < fr.length; i++) {
          var d = null;
          try { d = fr[i].contentDocument || (fr[i].contentWindow && fr[i].contentWindow.document) || null; } catch (_) { d = null; }
          if (d && d.querySelectorAll && seen.indexOf(d) < 0) { seen.push(d); docs.push(d); dig(d); }
        }
      }
      dig(document);
      return docs;
    }
    function _allBoardTables() {
      var out = [], docs = _allBoardDocs();
      for (var k = 0; k < docs.length; k++) {
        try { var t = docs[k].querySelectorAll('table'); for (var j = 0; j < t.length; j++) out.push(t[j]); } catch (_) { /* */ }
      }
      return out;
    }
    // 셀이 들어있는 문서(프레임)의 최상위 창 기준 좌상단 오프셋(중첩 프레임 합산). 최상위 문서면 {0,0}.
    function _boardFrameOffset(doc) {
      var x = 0, y = 0;
      try {
        var win = doc && doc.defaultView;
        while (win && win.frameElement) {
          var fr = win.frameElement.getBoundingClientRect();
          x += fr.left; y += fr.top;
          if (win === win.parent) break;
          win = win.parent;
        }
      } catch (_) { /* 교차출처 조상 = 보정 불가, 현상 유지 */ }
      return { x: x, y: y };
    }
    function locateBoardMatrix() {
      var oddsClass = /asyukk|qwqwd|dke-d11diw/i.test(location.host) ? 'odds_content' : null;
      var best = null, tables;
      try { tables = _allBoardTables(); } catch (_) { return null; }
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
      if (ctx.specialSet && ctx.specialSet[key]) return { col: BCOL.special, tag: (ctx.specialTag[key] || 'BMED 특별'), emph: true, special: true };
      if (ctx.redSet[key]) return { col: BCOL.drop, tag: (ctx.redTag[key] || '급락'), emph: true, warn: true };
      return null;   // 흰색/중립(무변동·단순저배당 포함) — 테두리·배경 없음(원본 숫자만)
    }

    function removeBoardMatrix() {
      try { var b = byId(BOARD_ID); if (b) b.remove(); } catch (_) { /* */ }
      try { if (boardRO) { boardRO.disconnect(); boardRO = null; } } catch (_) { /* */ }
      boardItems = []; boardHdrItems = [];
    }

    // 각 오버레이 셀을 실제 배당판 셀의 현재 화면 좌표(fixed)로 재배치.
    function positionBoard() {
      var i, it, r;
      // [프레임 대응] 셀이 iframe 안이면 그 프레임 위치만큼 보정(최상위 문서면 0). 문서별 1회 캐시.
      var _offDoc = null, _off = { x: 0, y: 0 };
      function _ofs(el) {
        var d = el.ownerDocument;
        if (d === document) return { x: 0, y: 0 };
        if (d !== _offDoc) { _offDoc = d; _off = _boardFrameOffset(d); }
        return _off;
      }
      for (i = 0; i < boardItems.length; i++) {
        it = boardItems[i];
        try { r = it.el.getBoundingClientRect(); } catch (_) { continue; }
        if (!r || (r.width === 0 && r.height === 0)) { it.span.style.display = 'none'; continue; }
        var o = _ofs(it.el);
        it.span.style.display = 'flex';
        it.span.style.left = (r.left + o.x) + 'px'; it.span.style.top = (r.top + o.y) + 'px';
        it.span.style.width = r.width + 'px'; it.span.style.height = r.height + 'px';
      }
      for (i = 0; i < boardHdrItems.length; i++) {
        it = boardHdrItems[i];
        try { r = it.el.getBoundingClientRect(); } catch (_) { continue; }
        if (!r || (r.width === 0 && r.height === 0)) { it.span.style.display = 'none'; continue; }
        var o2 = _ofs(it.el);
        it.span.style.display = 'flex';
        // [단통 배지 등 오프셋 지원] it.dy/it.dx(있으면) 만큼 이동·고정폭(it.fixedW) 지원
        it.span.style.left = (r.left + o2.x + (it.dx || 0)) + 'px'; it.span.style.top = (r.top + o2.y + (it.dy || 0)) + 'px';
        it.span.style.width = (it.fixedW || r.width) + 'px'; it.span.style.height = (it.fixedH || r.height) + 'px';
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
      // [깜박임 제거 v2.1.135] 여기서 매번 지우면 렌더 때마다(10초 분석·타이머 갱신 등) 오버레이가
      //   사라졌다 다시 그려져 깜박임 → 강조 구성이 같으면 위치만 재조정, 바뀐 경우에만 새로 그린다
      //   (제거는 아래 시그니처 비교 후로 이동 — 기존 기능 무삭제·순서만 이동).
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

      // [급락 조합] 콤보 단위 급락(-10%↓) — 빨강 후보. 단, 최종 빨강은 '급락 말 포함' 게이트+상한 3개로 엄격 제한(아래).
      var dropMap = {};
      (d.drops || []).forEach(function (dd) {
        var c = (dd.combo || []).map(Number);
        if (c.length === 2 && (dd.pct || 0) <= -10) dropMap[ckey(c[0], c[1])] = Math.round(dd.pct);
      });

      // [급락 말 집합] '진짜 급락 말' = flowScores trend 급락/스마트머니(최근 배당 10%+ 하락) + 집중급락(anomalyHorse).
      //   빨강은 반드시 이 말 1+ 을 포함한 콤보만 → 고배당 노이즈 콤보(280배가 20% 흔들린 것 등) 빨강 제외.
      var dropHorseSet = {};
      Object.keys(flow).forEach(function (n) {
        var f = flow[n] || {};
        if (f.trend === '급락' || f.trend === '스마트머니') dropHorseSet[+n] = 1;
      });
      if (d.anomalyHorse != null) dropHorseSet[+d.anomalyHorse] = 1;

      // [오버레이-패널 통일] 배당판 강조를 패널 추천(corePicks.finalQuinellas)과 동일 소스로 통일.
      //   ★★★(상위 랭킹) → 초록(greenMax), 그다음 랭킹 → 파랑(blueMax). finalQuinellas 순서 = 패널 표시 순서(확신도/근거 랭킹).
      //   ⚠ finalQuinellas 에 없는 조합은 강조하지 않음(완전 투명) → "패널엔 4번 중심인데 배당판은 7번 저배당 파랑" 불일치 제거.
      //   색상우선 초록>파랑>빨강(빨강은 초록/파랑 아닌 셀만). ⚠ 패널 추천 리스트·finalQuinellas 개수는 불변 — 배당판 시각강조 개수만 제한.
      //   [종목·두수별 개수] 경륜/경정/바이크: 초록1·파랑3. 경마: 8~9두 초록1·파랑3 / 10두 초록1·파랑4 / 11~12두 초록2·파랑4 / 13~18두 초록2·파랑6.
      var _boardN = (info.headerNos || []).length;
      var _sportL = (d.sport || (d.corePicks && d.corePicks.sport) || '').toLowerCase();
      var _smallField = (_sportL === 'cycle' || _sportL === 'boat' || _sportL === 'bike');   // 경륜/경정/바이크(6~9명)
      var greenMax, blueMax, starMax;
      if (_smallField) {                       // 경륜류: 초록1·파랑3
        greenMax = 1; blueMax = 3; starMax = 3;
      } else if (_boardN <= 9) {               // 경마 8~9마리: 초록1·파랑3
        greenMax = 1; blueMax = 3; starMax = 3;
      } else if (_boardN === 10) {             // 경마 10마리: 초록1·파랑4
        greenMax = 1; blueMax = 4; starMax = 3;
      } else if (_boardN <= 12) {              // 경마 11~12마리: 초록2·파랑4
        greenMax = 2; blueMax = 4; starMax = 4;
      } else {                                 // 경마 13~18마리: 초록2·파랑6
        greenMax = 2; blueMax = 6; starMax = 5;
      }

      // [초록·파랑 = 패널 finalQuinellas 랭킹順] 상위 greenMax → 초록, 그다음 blueMax → 파랑(패널과 100% 동일 조합).
      //   기존 '저배당+신호 독립 휴리스틱'은 패널과 다른 조합을 뽑아 불일치를 유발 → 사용자 요청대로 패널 소스로 대체.
      var _fq = (d.corePicks && d.corePicks.finalQuinellas) || [];
      // [패널 일치 폴백] finalQuinellas 가 비면(엄격 게이트로 메인 0) 패널과 동일하게 confQuinellas→quinella 로 강조
      //   → 패널 추천 조합(1+5·1+7 등)이 배당판에도 반드시 초록/파랑으로 표시됨(불일치 제거).
      // [수익성 3분류 (2026-07-19)] 저배당 경주(profitTier=low)는 복승 의도적 0개 — 폴백 금지(삼복승 집중)
      if (!_fq.length && d.corePicks && !d.corePicks.dansung && !((d.corePicks.profitTier || {}).tier === 'low')) {   // [패널 일치] 단통은 폴백 금지(패널 updatePanel과 동일 조건)
        var _cq0 = d.corePicks.confQuinellas || [];
        if (_cq0.length) _fq = _cq0.slice(0, 2);
        else if (d.corePicks.quinella && d.corePicks.quinella.length === 2) {
          _fq = [{ combo: d.corePicks.quinella, odds: d.corePicks.quinellaOdds }];
        }
      }
      var greenSet = {}, blueSet = {}, blueTag = {}, _gN = 0, _bN = 0;
      _fq.forEach(function (q) {
        var c = (q.combo || []).map(Number);
        if (c.length !== 2) return;
        var k = ckey(c[0], c[1]);
        if (greenSet[k] || blueSet[k]) return;                 // 중복 조합 제거
        if (_gN < greenMax) { greenSet[k] = 1; _gN++; return; }   // ★★★(상위 랭킹) → 초록
        if (_bN < blueMax) {                                    // 그다음 랭킹 → 파랑
          blueSet[k] = 1;
          blueTag[k] = ((q.stars || 0) >= 3 ? '추천' : '유력') + (q.reason ? ' · ' + q.reason : '');
          _bN++;
        }
      });

      // [BMED 특별 감지 💎] 고배당+강신호 조합(cp.bmedSpecial) → 배당판에 💎 별도 표시(초록/파랑=메인과 구분)
      var specialSet = {}, specialTag = {};
      ((d.corePicks && d.corePicks.bmedSpecial) || []).forEach(function (q) {
        var c = (q.combo || []).map(Number);
        if (c.length !== 2) return;
        var k = ckey(c[0], c[1]);
        if (greenSet[k] || blueSet[k]) return;
        specialSet[k] = 1;
        specialTag[k] = 'BMED 특별' + (q.reason ? ' · ' + q.reason : '') + (q.score != null ? ' · 신호' + q.score + '점' : '');
      });

      // [배당 소스 통일] 강조 셀(초록/파랑/💎)에 서버(corePicks) 배당을 배지로 표시 →
      //   배당판(사설 asyukk) 숫자와 달라도 패널과 '동일 조합·동일 배당(oddspark)'을 셀 위에 직접 보여줌.
      //   색상 결정은 이미 corePicks combo(greenSet/blueSet/specialSet)로만 함(DOM 배당값 미사용) — 여기서 표시 배당만 서버값으로 통일.
      var _srvOdds = {};
      _fq.forEach(function (q) { var c = (q.combo || []).map(Number); if (c.length === 2 && q.odds != null) _srvOdds[ckey(c[0], c[1])] = q.odds; });
      ((d.corePicks && d.corePicks.bmedSpecial) || []).forEach(function (q) {
        var c = (q.combo || []).map(Number);
        if (c.length === 2 && q.odds != null) { var _k = ckey(c[0], c[1]); if (_srvOdds[_k] == null) _srvOdds[_k] = q.odds; }
      });

      // [헤더 ⭐ 말 집합 선계산] 초록+파랑 조합에 등장하는 말(starMax 상한) = 실제 헤더에 ⭐ 붙는 말.
      //   ⚠ role 'fav'(keyHorses)보다 좁음 — 빨강 게이트(유력마=⭐)와 헤더 ⭐ 표시가 100% 일치하도록 공용 집합.
      var _starHorse = {}, _starN = 0;
      function _markStar(nn) { nn = +nn; if (_starHorse[nn] || _starN >= starMax) return; _starHorse[nn] = 1; _starN++; }
      Object.keys(greenSet).forEach(function (k) { var p = k.split('|'); _markStar(p[0]); _markStar(p[1]); });   // 초록(추천) 우선
      Object.keys(blueSet).forEach(function (k) { var p = k.split('|'); _markStar(p[0]); _markStar(p[1]); });    // 그다음 파랑(유력)

      // [빨강 = 진짜 급락만·근본 수정] 콤보 과다 발화 방지 게이트 + 상한 3개.
      //   조건 ①콤보 최근 배당 10%+ 하락(dropMap) AND ②두 말 중 1+ 이 급락 말(dropHorseSet)
      //   AND ⑤두 말 중 1+ 이 유력마(헤더 ⭐ 표시된 말=_starHorse) — ⭐ 무관 조합은 급락해도 빨강 금지(사용자 요청).
      //   ③초록/파랑/특별 겹치면 빨강 금지(우선순위). ④그래도 많으면 가장 큰 급락순 최대 3개만(노이즈 억제).
      var _redCand = [];
      Object.keys(dropMap).forEach(function (k) {
        if (greenSet[k] || blueSet[k] || specialSet[k]) return;   // ③ 초록>파랑>특별>빨강
        var p = k.split('|');
        if (!dropHorseSet[+p[0]] && !dropHorseSet[+p[1]]) return; // ② 급락 말 없는 콤보 = 노이즈 → 완전 투명
        if (!_starHorse[+p[0]] && !_starHorse[+p[1]]) return;     // ⑤ 헤더 ⭐(유력마) 무관 조합 = 빨강 금지(급락이어도)
        _redCand.push({ k: k, pct: dropMap[k] });
      });
      _redCand.sort(function (a, b) { return a.pct - b.pct; });   // 가장 큰 하락(음수 작은 값) 먼저
      var redSet = {}, redTag = {};
      _redCand.slice(0, 3).forEach(function (c) {                 // ④ 최대 3개
        redSet[c.k] = 1;
        redTag[c.k] = '급락 ' + c.pct + '%';
      });

      var ctx = { dropMap: dropMap, greenSet: greenSet, blueSet: blueSet, blueTag: blueTag, redSet: redSet, redTag: redTag, specialSet: specialSet, specialTag: specialTag };

      // [깜박임 제거 v2.1.135] 강조 구성(초록/파랑/특별/빨강·헤더·서버배당)이 직전과 동일하고 레이어가
      //   살아 있으면 → 지우지 않고 위치만 재조정(부드러운 갱신). 구성이 바뀐 경우에만 교체.
      try {
        var _bsig = JSON.stringify([Object.keys(greenSet).sort(), Object.keys(blueSet).sort(),
          Object.keys(specialSet || {}).sort(), Object.keys(redSet).sort(),
          info.headerNos, _srvOdds, Object.keys(role || {}).sort()]);
        if (renderBoardMatrix._sig === _bsig && byId(BOARD_ID) && boardItems.length) {
          schedulePosition();
          return true;
        }
        renderBoardMatrix._sig = _bsig;
      } catch (_) { /* 시그니처 실패 시 기존 방식(재생성) */ }
      removeBoardMatrix();
      // 오버레이 레이어(뷰포트 고정·클릭 통과)
      var layer = mk('div', 'position:fixed;left:0;top:0;width:0;height:0;z-index:2147482800;pointer-events:none');
      layer.id = BOARD_ID;
      root().appendChild(layer);

      // [셀 밀림 방지] 배당판 표의 크기·레이아웃 변화에도 즉시 재정렬.
      //   scroll/resize 이벤트만으론 '표 내부 재렌더(배당 갱신)·배너 노출로 인한 레이아웃 이동'을 못 잡아
      //   다음 렌더(10초 주기)까지 오버레이가 옛 좌표에 남아 밀려 보였다. 표 자체를 관찰해 즉시 따라간다.
      try {
        if (boardRO) { boardRO.disconnect(); boardRO = null; }
        if (window.ResizeObserver && info.table) {
          boardRO = new ResizeObserver(function () { schedulePosition(); });
          boardRO.observe(info.table);
        }
      } catch (_) { /* ResizeObserver 미지원 → scroll/resize 폴백만 사용 */ }

      // [테두리+반투명 방식] 강조 셀은 테두리 + 얇은 반투명 배경만 → 원본 배당 숫자가 항상 보인다.
      //   미강조 셀은 오버레이 없음(완전 투명). 아이콘은 우측 상단 작은 뱃지로만 표시.
      // [출전취소·밀림 방어] 배당판 셀의 마번이 실제 출전 마번(validHorses·서버가 competition除外 제외)에 없으면
      //   강조하지 않는다 → 취소 말(除外) 셀 오강조 차단 + 배당판 셀 밀림으로 잘못된 마번이 나와도 오강조 방지.
      var _vsetB = validSet(d);
      info.cells.forEach(function (cell) {
        if (_vsetB && (!_vsetB.has(Number(cell.a)) || !_vsetB.has(Number(cell.b)))) return;
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
        // 우측 상단 모서리 작은 뱃지 — 추천🔒 · 특별💎 · 경고⚠️(색은 테두리로 구분·파랑=뱃지없음)
        var badgeTxt = lock ? '🔒' : (stl.special ? '💎' : (stl.warn ? '⚠️' : ''));
        if (badgeTxt) {
          span.appendChild(mk('span',
            'position:absolute;top:-8px;right:-6px;font-size:11px;line-height:1;'
            + 'background:#0f172a;border-radius:6px;padding:1px 2px;box-shadow:0 1px 2px rgba(0,0,0,.5)', badgeTxt));
        }
        // [배당 소스 통일] 강조 셀 좌하단에 서버(corePicks) 배당 배지 → 패널과 동일 배당 표시(배당판 사설 숫자와 달라도).
        var _so = _srvOdds[ckey(cell.a, cell.b)];
        if (_so != null && (stl.lock || stl.special || ctx.blueSet[ckey(cell.a, cell.b)])) {
          span.appendChild(mk('span',
            'position:absolute;bottom:-7px;left:-4px;font:800 10px/1 sans-serif;color:#0f172a;'
            + 'background:' + stl.col + ';border-radius:5px;padding:1px 3px;box-shadow:0 1px 2px rgba(0,0,0,.5)',
            _so + '배'));
        }
        span.title = cell.a + '-' + cell.b + (_so != null
          ? ' · 서버 ' + _so + '배 · ' + stl.tag + ' (배당판 사설 ' + cell.odds + '배)'
          : ' = ' + cell.odds + '배 · ' + stl.tag);
        layer.appendChild(span);
        boardItems.push({ el: cell.el, span: span });
      });

      // [헤더 ⭐ = 초록+파랑 셀에 등장하는 말번호만] 선계산한 _starHorse(빨강 게이트와 공용) 재사용 → ⭐ 표시와 빨강 기준 일치.
      //   ❌제거마는 상한 밖(항상 유지). 스마트머니/역배열은 유력 셀에 들면 ⭐로 커버.
      var _emph = {};
      Object.keys(_starHorse).forEach(function (nn) { _emph[+nn] = { mark: '⭐', bc: BCOL.fav, lbl: '유력(추천·유력 조합)' }; });
      // [BMED 특별 감지 💎] 특별 조합에 등장하는 말번호 → 헤더에 💎(상한 밖·⭐/❌ 아니면 표시)
      var _spH = {};
      Object.keys(specialSet).forEach(function (k) { var p = k.split('|'); _spH[+p[0]] = 1; _spH[+p[1]] = 1; });
      info.hdrEls.forEach(function (h) {
        var n = h.no, r0 = role[n];
        var e = _emph[n], isCut = (r0 === 'cut'), isSp = _spH[n];
        if (!e && !isCut && !isSp) return;   // 강조 대상 아님: 숫자만
        var mark, bc, lbl;
        if (e) { mark = e.mark; bc = e.bc; lbl = e.lbl; }         // 강조 상한(2~3마리) 우선
        else if (isCut) { mark = '❌'; bc = BCOL.drop; lbl = '확실제거'; }   // 제거마(상한 밖·유지)
        else { mark = '💎'; bc = BCOL.special; lbl = 'BMED 특별 감지'; }   // BMED 특별 조합 말
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

      // [단통 배지] 복승 최저배당 ≤1.5배 = 시장 과도 쏠림 → 배당판 헤더 좌상단에 "⚡ 단통" 경고 배지(첫 헤더셀 위)
      if (d.corePicks && d.corePicks.dansung && info.hdrEls.length) {
        var _dsMin = d.corePicks.dansungMinOdds;
        var dsBadge = mk('span',
          'position:fixed;box-sizing:border-box;display:flex;align-items:center;justify-content:center;'
          + 'font:900 13px/1 -apple-system,BlinkMacSystemFont,sans-serif;color:#0f172a;white-space:nowrap;'
          + 'background:#f59e0b;border:2px solid #b45309;border-radius:7px;padding:0 6px;'
          + 'box-shadow:0 2px 6px rgba(0,0,0,.6);z-index:2147482801',
          '⚡ 단통' + (_dsMin != null ? ' ' + _dsMin + '배' : ''));
        dsBadge.title = '단통 경주(복승 최저 ' + (_dsMin != null ? _dsMin + '배' : '≤1.5배') + ') · 저배당 신뢰도 낮음 · 복병(💎) 집중';
        layer.appendChild(dsBadge);
        // 첫 헤더셀 바로 위(dy=-26)에 고정폭 배지로 정렬
        boardHdrItems.push({ el: info.hdrEls[0].el, span: dsBadge, dy: -26, fixedW: 76, fixedH: 22 });
      }

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
        var hR = mk('div', 'display:flex;align-items:center;gap:8px');
        // [배당판 스냅샷·수동] 📸 클릭 시 즉시 캡처(배당판+오버레이+패널) → 워터마크 → 서버 저장 → "📸 저장됨" 토스트
        var snapBtn = mk('button', 'all:unset;cursor:pointer;font-size:15px;line-height:1;padding:0 2px', '📸');
        snapBtn.title = '배당판 스냅샷 저장(수동 캡처)';
        snapBtn.addEventListener('mousedown', function (ev) { try { ev.stopPropagation(); } catch (_) { /* */ } });   // 드래그와 분리
        snapBtn.addEventListener('click', function (ev) {
          try { ev.stopPropagation(); } catch (_) { /* */ }
          captureBoardSnapshot(d, 'manual');
        });
        hR.appendChild(snapBtn);
        var x = mk('button', 'all:unset;cursor:pointer;color:#94a3b8;font:700 14px sans-serif;padding:0 2px', '✕');
        x.title = '오버레이 끄기';
        x.addEventListener('click', function () {
          enabled = false;
          try { chrome.storage.local.set({ overlayEnabled: false }); } catch (_) { /* */ }
          render();
        });
        hR.appendChild(x);
        head.appendChild(hR);
        panel.appendChild(head);

        // [자동전송 역할 재정의·수집모드 배지] NAR 지방경마·경륜 = 서버 oddspark 전담(🔄 서버 자동수집 중),
        //   JRA 중앙·한국·경정·바이크 = 확장 수집(📡 확장 수집 중). d.category(분석 결과) 기준.
        try {
          var _mc = (d && (d.category || (d.corePicks && d.corePicks.category))) || '';
          var _serverCol = (_mc === 'japan_local' || _mc === 'cycle');
          if (_mc) {
            var _cm = mk('div', 'margin:0 0 6px;padding:5px 9px;border-radius:7px;font-size:12px;font-weight:800;' +
              (_serverCol ? 'border:1px solid #22d3ee;background:rgba(34,211,238,.14);color:#67e8f9'
                          : 'border:1px solid #a78bfa;background:rgba(167,139,250,.14);color:#c4b5fd'),
              _serverCol ? '🔄 서버 자동수집 중 (oddspark)' : '📡 확장 수집 중');
            panel.appendChild(_cm);
          }
        } catch (_) { /* */ }

        // [전체수집 자동폴백 표시] oddspark 미등록 경마장(소노다 등)에서 서버 배당이 없어 확장이 전체수집을
        //   자동 실행 중이면 배너 표시(content.js 가 storage.autoFallback 에 기록). 40초 내 최근 상태만 표시.
        try {
          var _fb = st.autoFallback;
          if (_fb && _fb.active && (Date.now() - (_fb.at || 0) < 40000)) {
            var _fbRow = mk('div', 'margin:0 0 6px;padding:6px 9px;border-radius:7px;border:1px solid #f59e0b;background:rgba(245,158,11,.16)');
            _fbRow.appendChild(mk('div', 'font-weight:800;font-size:12px;color:#fcd34d', '⚡ 전체수집 자동 실행 중...'));
            _fbRow.appendChild(mk('div', 'font-weight:700;font-size:11px;color:#fde68a',
              (_fb.raceKey || '') + ' · oddspark 미등록 → 배당판 직접 수집(수동 버튼 불필요)'));
            panel.appendChild(_fbRow);
          }
        } catch (_) { /* */ }

        // [수정2·한국경마 자동수집 표시] 한국 배당판이면 확장이 30초마다 복승 자동수집 중임을 표시(버튼 불필요).
        try {
          var _kr = st.koreaAuto;
          if (_kr && _kr.active && (Date.now() - (_kr.at || 0) < 40000)) {
            var _krRow = mk('div', 'margin:0 0 6px;padding:6px 9px;border-radius:7px;border:1px solid #38bdf8;background:rgba(56,189,248,.16)');
            _krRow.appendChild(mk('div', 'font-weight:800;font-size:12px;color:#7dd3fc', '🇰🇷 한국경마 복승 자동수집 중'));
            _krRow.appendChild(mk('div', 'font-weight:700;font-size:11px;color:#bae6fd',
              (_kr.raceKey || '') + ' · 30초마다 복승 수집(쌍승·삼복승 없음·배당판 고정)'));
            panel.appendChild(_krRow);
          }
        } catch (_) { /* */ }

        // [경주 전환 클리어] 배당판이 새 경주로 넘어갔는데(st.raceKey) 분석은 이전 경주(d.raceKey)면
        //   = 경주 전환 직후 → 이전 추천(corePicks·유력마·복병) 표시를 즉시 숨기고 "🔄 새 경주 분석 중..." 표시 +
        //   새 경주로 즉시 재분석 트리거. 분석 완료(analyzeStatus 갱신)되면 다음 렌더에서 새 결과가 표시됨.
        //   두 raceKey 는 날짜 접두 차이를 무시하고 "경마장 + N경주" 꼬리로 비교(오탐 방지).
        try {
          var _rkTail = function (r) { return String(r || '').replace(/\d{4}-\d{2}-\d{2}/g, '').replace(/\s+/g, ' ').trim(); };
          var _liveRk = _rkTail(st.raceKey);
          var _anaRk = _rkTail(d && d.raceKey);
          // [종목 불일치 클리어·한국경마 자동전환] 배당판이 한국경마인데(detectedCategory='korea') 분석이 다른 종목
          //   (경정/경륜/일본)이면 raceKey 가 비어있거나 꼬리가 우연히 같아도 무조건 전환 클리어 → 경정 분석 잔존 제거.
          var _boardCat = st.detectedCategory || '';
          var _anaCat = (d && (d.category || (d.corePicks && d.corePicks.category))) || '';
          var _catMismatch = (_boardCat === 'korea' && _anaCat && _anaCat !== 'korea');
          if (_catMismatch || (_liveRk && _anaRk && _liveRk !== _anaRk)) {
            var trans = mk('div', 'margin:0 0 6px;padding:8px 10px;border-radius:7px;border:1px solid #38bdf8;background:rgba(56,189,248,.14)');
            trans.appendChild(mk('div', 'font-weight:900;font-size:14px;color:#7dd3fc',
              _catMismatch ? '🇰🇷 한국경마 분석 중...' : '🔄 새 경주 분석 중...'));
            trans.appendChild(mk('div', 'font-weight:800;font-size:14px;color:#e2e8f0;margin-top:2px', st.raceKey || _liveRk || '한국경마'));
            trans.appendChild(mk('div', 'font-size:11px;color:#94a3b8;margin-top:2px',
              _catMismatch ? '이전 종목(경정/경륜) 분석을 초기화했습니다 · 복승 수집 중' : '이전 경주 추천을 초기화했습니다 · 잠시만 기다려 주세요'));
            panel.appendChild(trans);
            // 새 경주/종목 감지 → 즉시 1회 재분석(중복 트리거 방지). 종목 전환은 raceKey 키로 중복 방지.
            var _trigKey = _catMismatch ? ('korea:' + (_liveRk || st.raceKey || '')) : _liveRk;
            if (_lastTransitionRk !== _trigKey) {
              _lastTransitionRk = _trigKey;
              try { pollOverlayAnalyze(); } catch (_) { /* */ }
            }
            return;   // ⬅ 이전 종목/경주 corePicks·유력마·복병·추천 렌더 억제(전환 클리어)
          }
        } catch (_) { /* */ }

        // [배당판 스냅샷·자동 3단계] 마감 10분전(T-10·초기배당) → 2분전(T-2·최종추천 확정) → 마감직후(최종배당). 단계별 raceKey당 1회.
        try {
          var _snapRk = (d && d.raceKey) || '';
          var _snapLeft = deadline ? (deadline - Date.now()) : null;
          if (_snapRk && deadline && _snapLeft != null) {
            var _sd = _snapStage[_snapRk] || (_snapStage[_snapRk] = {});
            if (!_sd.t10 && _snapLeft <= 600000 && _snapLeft > 120000 && d.corePicks) {
              _sd.t10 = 1; captureBoardSnapshot(d, 'T-10');                 // 1단계: 마감 10분전(초기 배당 상태)
            } else if (!_sd.t2 && _snapLeft <= 120000 && _snapLeft > 0 && d.corePicks) {
              _sd.t2 = 1; captureBoardSnapshot(d, 'T-2');                   // 2단계: 마감 2분전(최종 추천 확정)
            } else if (!_sd.close && _snapLeft <= 0 && _snapLeft >= -35000) {
              _sd.close = 1; captureBoardSnapshot(d, 'close');             // 3단계: 마감 직후 30초 이내(최종 배당)
            }
          }
        } catch (_) { /* */ }

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
        var _dansung = !!(cp && cp.dansung);   // [단통] 복승 최저배당 ≤1.5배 = 시장 과도 쏠림
        var _spAll = (cp && cp.bmedSpecial) || [];
        // [폴백·구데이터] finalQuinellas 미보유(구 캐시)면 기존 confQuinellas/quinella·삼복승으로 대체(단, 단통은 폴백 금지=1.5배 재노출 방지)
        if (!_fq.length && cp && !_dansung) {
          var _cq0 = cp.confQuinellas || [];
          if (_cq0.length) _fq = _cq0.slice(0, 2);
          else if (cp.quinella && cp.quinella.length === 2) _fq = [{ combo: cp.quinella, odds: cp.quinellaOdds }];
        }
        if (!_ft.length && cp) {
          var _t0 = cp.confTrifecta || cp.trifecta;
          if (_t0) _ft = [{ combo: _t0, odds: cp.confTrifecta ? cp.confTrifectaOdds : cp.trifectaOdds }];
        }
        if ((_fq.length || _dansung || _spAll.length) && !d.recommendClosed && st.ovShowPicks !== false) {   // [🎯 추천] 팝업 토글(기본 표시)
          var cpBox = mk('div', 'margin:0 0 6px;padding:9px 12px;border:3px solid #38d39f;border-radius:9px;background:rgba(56,211,159,.18)');
          cpBox.appendChild(mk('div', 'font-weight:900;color:#38d39f;font-size:16px', '🎯 지금 사세요! (근거 기반)'));
          // [단통 경고 배너] 저배당 추천 신뢰도 낮음 · 복병 집중
          if (_dansung) {
            var dsB = mk('div', 'margin:5px 0 3px;padding:7px 9px;border:2px solid #f59e0b;border-radius:8px;background:rgba(245,158,11,.16)');
            dsB.appendChild(mk('div', 'font-weight:900;color:#f59e0b;font-size:14px',
              '⚡ 단통 경주 감지' + (cp.dansungMinOdds != null ? ' (최저 ' + cp.dansungMinOdds + '배)' : '')));
            dsB.appendChild(mk('div', 'color:#fcd34d;font-size:12px;margin-top:2px', '저배당 추천 신뢰도 낮음 · 복병 감지에 집중하세요 (💎 참고)'));
            cpBox.appendChild(dsB);
          }
          // [전적 수집 상태] ✅ 전적+배당 / ⚠️ 배당 기반만(formMissing)
          if (cp && cp.formMissing) {
            cpBox.appendChild(mk('div', 'font-weight:800;color:#fbbf24;font-size:12.5px;margin-top:2px', '⚠️ 전적 데이터 없음 — 배당 기반 분석 중'));
          } else {
            cpBox.appendChild(mk('div', 'font-weight:700;color:#38d39f;font-size:12px;margin-top:2px', '✅ 전적+배당 분석'));
          }
          // [근거 기반·두수별 개수] N두 경주 · 복승 N개(혼전 +2) 헤더 + 조합별 ★등급·근거
          var _nH = (cp && cp.raceHorseCount) || 0;
          var _starStr = function (n) { return new Array(Math.max(0, Math.min(3, n || 0)) + 1).join('★'); };
          if (_nH) {
            cpBox.appendChild(mk('div', 'color:#9fb3c8;font-size:12px;margin-top:2px',
              _nH + '두 경주 · 복승 ' + _fq.length + '개 추천' + (cp.chaoticRace ? ' · ⚠️혼전(+2)' : '')));
          }
          var _addBasis = function (box, q, col) {   // [근거 문장] 급락%/스마트머니/역배열 + 요약 줄
            (q.basis || []).forEach(function (b) {
              box.appendChild(mk('div', 'font-size:12.5px;margin:1px 0 0 14px;color:' + col, '→ ' + b));
            });
            if (q.summary) box.appendChild(mk('div', 'font-size:12px;margin:1px 0 0 14px;color:#38d39f', '→ ' + q.summary));
          };
          _fq.forEach(function (q) {   // [두수별] 서버가 상한으로 이미 캡 → 전부 표시(구데이터 폴백은 2개)
            var _st = q.stars ? '  ' + _starStr(q.stars) : '';
            var _rs = q.reason ? '  · ' + q.reason : '';   // 이 말이 들어오는 이유(근거)
            cpBox.appendChild(mk('div', 'font-weight:800;font-size:17px;margin-top:5px;color:#e2e8f0',
              '복승: ' + q.combo.join('+') + (q.odds != null ? '  (' + q.odds + '배)' : '') + _st + _rs));
            _addBasis(cpBox, q, '#7dd3fc');
          });
          _ft.slice(0, 2).forEach(function (t) {
            var _rs = t.reason ? '  · ' + t.reason : '';
            cpBox.appendChild(mk('div', 'font-weight:800;font-size:16px;margin-top:5px;color:#c4b5fd',
              '🛡 삼복승 보험: ' + t.combo.join('+') + (t.odds != null ? '  (' + t.odds + '배)' : '') + _rs));
          });
          // [BMED 특별 감지 💎] 고배당+강신호 별도 섹션(하단·최대 2개)
          var _sp = (cp && cp.bmedSpecial) || [];
          if (_sp.length) {
            var spBox = mk('div', 'margin-top:8px;padding:7px 9px;border:2px dashed #f0abfc;border-radius:8px;background:rgba(240,171,252,.10)');
            spBox.appendChild(mk('div', 'font-weight:800;color:#f0abfc;font-size:14px', '💎 BMED 특별 감지'));
            spBox.appendChild(mk('div', 'color:#c4b5fd;font-size:11px;margin-top:1px', '시장은 저평가 · BMED만 감지한 고배당 기회'));
            _sp.forEach(function (q) {
              var _sc = q.score != null ? '  · 신호 ' + q.score + '점' : '';
              spBox.appendChild(mk('div', 'font-weight:800;font-size:15px;margin-top:4px;color:#f5d0fe',
                '복승: ' + q.combo.join('+') + (q.odds != null ? '  (' + q.odds + '배)' : '') + '  ★★' + _sc));
              _addBasis(spBox, q, '#f0abfc');
            });
            cpBox.appendChild(spBox);
          }
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
          startOverlayAnalyzePoll();   // [분석 자동화] 자동전송 없이도 주기 분석 갱신 시작
        } catch (_) { /* */ }
      });
      // 상태/데이터 변경 → 즉시 반영
      chrome.storage.onChanged.addListener(function (ch, area) {
        try {
          if (area !== 'local') return;
          if (ch.overlayKill) { killed = !!ch.overlayKill.newValue; if (killed) removeAll(); else render(); return; }
          if (ch.overlayPos) { savedPos = ch.overlayPos.newValue || null; }   // [보완#2] 위치 동기화(다른 탭 반영)
          if (ch.overlaySound) { soundOn = !!ch.overlaySound.newValue; }      // [보완#3] 알림음 옵션 동기화
          if (ch.overlayEnabled) { enabled = !!ch.overlayEnabled.newValue; render(); if (enabled) startOverlayAnalyzePoll(); }
          // [오버레이 표시 제어] 팝업 📊/🎯/⏱ 버튼 변경 시 즉시 재렌더
          if ((ch.ovShowMatrix || ch.ovShowPicks || ch.ovShowTimeline) && enabled && !killed) render();
          if ((ch.analyzeStatus || ch.collectAlert || ch.timerDeadline || ch.autoFallback || ch.koreaAuto) && enabled && !killed) render();
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
