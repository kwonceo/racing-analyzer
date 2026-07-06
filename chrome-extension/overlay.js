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
      'position:fixed;right:12px;top:96px;z-index:2147482900;width:230px;max-height:64vh;overflow:auto;' +
      'background:rgba(17,24,39,.96);color:#e5e7eb;border:1px solid #4c1d95;border-radius:10px;' +
      'padding:10px 11px;font:500 12px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;' +
      'box-shadow:0 6px 20px rgba(0,0,0,.4)';

    // 패널 내용 갱신 (div/span 만 사용 · textContent 기반)
    function updatePanel(panel, st) {
      try {
        while (panel.firstChild) panel.removeChild(panel.firstChild);
        var d = (st.analyzeStatus && st.analyzeStatus.data) || null;
        var deadline = st.timerDeadline || 0;

        // 헤더 + 닫기(✕)
        var head = mk('div', 'display:flex;align-items:center;justify-content:space-between;margin-bottom:6px');
        head.appendChild(mk('span', 'font-weight:800;color:#c4b5fd', '📊 실시간 분석'));
        var x = mk('button', 'all:unset;cursor:pointer;color:#94a3b8;font:700 14px sans-serif;padding:0 2px', '✕');
        x.title = '오버레이 끄기';
        x.addEventListener('click', function () {
          enabled = false;
          try { chrome.storage.local.set({ overlayEnabled: false }); } catch (_) { /* */ }
          render();
        });
        head.appendChild(x);
        panel.appendChild(head);

        // 마감 카운트다운
        var cd = countdown(deadline);
        if (cd) {
          var cdRow = mk('div', 'margin:2px 0 6px');
          cdRow.appendChild(mk('span', 'color:#94a3b8', '마감까지 '));
          cdRow.appendChild(mk('span', 'font-weight:800;color:' + (cd === '마감' ? '#f87171' : '#fbbf24'), cd));
          panel.appendChild(cdRow);
        }

        if (!d) {
          panel.appendChild(mk('div', 'color:#94a3b8', '분석 대기 중 — 배당 수집·이상감지가 실행되면 표시됩니다.'));
          return;
        }

        // 유력마
        var keys = (d.keyHorses || []);
        if (keys.length) {
          var kr = mk('div', 'margin:3px 0');
          kr.appendChild(mk('span', 'color:#94a3b8', '⭐ 유력마 '));
          kr.appendChild(mk('span', 'font-weight:700;color:#4ea1ff', keys.join(' · ')));
          panel.appendChild(kr);
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
          if (timer) { clearInterval(timer); timer = null; }
          return;
        }
        if (!panel) { panel = mk('div', PANEL_CSS); panel.id = ID_PANEL; root().appendChild(panel); }
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
      chrome.storage.local.get({ overlayEnabled: false, overlayKill: false }, function (v) {
        try {
          killed = !!(v && v.overlayKill);
          enabled = !!(v && v.overlayEnabled);
          if (killed) { removeAll(); return; }
          render();
        } catch (_) { /* */ }
      });
      // 상태/데이터 변경 → 즉시 반영
      chrome.storage.onChanged.addListener(function (ch, area) {
        try {
          if (area !== 'local') return;
          if (ch.overlayKill) { killed = !!ch.overlayKill.newValue; if (killed) removeAll(); else render(); return; }
          if (ch.overlayEnabled) { enabled = !!ch.overlayEnabled.newValue; render(); }
          if ((ch.analyzeStatus || ch.collectAlert || ch.timerDeadline) && enabled && !killed) render();
        } catch (_) { /* */ }
      });
    } catch (_) { /* storage 접근 실패해도 페이지/수집 영향 없음 */ }
  } catch (_) {
    /* 최상위 보호막 — 어떤 예외도 페이지/수집 엔진에 전파되지 않는다. */
  }
})();
