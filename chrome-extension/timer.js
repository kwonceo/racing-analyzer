/* =========================================================================
 * timer.js — [3번] 경주 타이머 (배당판·분석기 탭 상단 고정)
 *   ⚠ v2.1.16: manifest content_scripts.matches 를 배당판(keiba/사설)·분석기(127.0.0.1:8011)로
 *      한정 → claude.ai 등 경마와 무관한 페이지에는 더 이상 바가 뜨지 않는다(무관 페이지 노출 제거).
 * -------------------------------------------------------------------------
 *   - 발주시각(HH:MM) 입력 → 카운트다운 (탭 간 chrome.storage 공유)
 *   - 남은 시간 크게 표시 + 상태(🟢 베팅가능 / 🔴 마감)
 *   - 단계 알림(🔔 소리 + 문구):
 *       10분전 → 배당판 전송 시작하세요
 *        5분전 → 배당 추세 확인
 *        3분전 → 빠른 비교 실행
 *        1분전 → 베팅 마감 임박!
 *   - 소리는 "포커스된 탭"에서만 (여러 탭 동시 비프 방지), 문구는 모든 탭 표시
 * =======================================================================*/

(() => {
  'use strict';
  if (window.top !== window) return;                 // 최상위 프레임만
  if (document.getElementById('kbTimerBar')) return; // 중복 주입 방지

  const STAGES = [
    { min: 10, msg: '📤 배당판 전송 시작하세요' },
    { min: 5, msg: '📈 배당 추세 확인' },
    { min: 3, msg: '⚡ 빠른 비교 실행' },
    { min: 1, msg: '⏰ 베팅 마감 임박!' },
  ];

  let deadline = 0;      // ms epoch (발주 시각)
  let fired = new Set(); // 이미 알린 단계(min)
  let msgUntil = 0;      // 문구 표시 만료 시각

  // ── 바 DOM (arbitrary 사이트 CSS 간섭 피하려 인라인 스타일) ──
  const bar = document.createElement('div');
  bar.id = 'kbTimerBar';
  bar.style.cssText = [
    'all:initial', 'position:fixed', 'top:0', 'left:0', 'right:0', 'z-index:2147483647',
    'height:36px', 'display:flex', 'align-items:center', 'gap:10px', 'padding:0 12px',
    'background:#0f172a', 'color:#e2e8f0',
    "font:600 13px/1 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif",
    'box-shadow:0 2px 10px rgba(0,0,0,.35)', 'border-bottom:1px solid #334155',
  ].join(';');
  const inputCss = 'background:#1e293b;border:1px solid #334155;color:#e2e8f0;border-radius:5px;padding:3px 6px;font:inherit;height:24px';
  bar.innerHTML =
    `<span style="font-size:15px">⏱</span>` +
    `<input id="kbTimerLabel" placeholder="경주" title="경주 이름(선택)" style="${inputCss};width:66px" />` +
    `<span style="color:#94a3b8;font-weight:400">발주</span>` +
    `<input id="kbTimerTime" type="time" title="발주 시각" style="${inputCss}" />` +
    `<span id="kbTimerCount" style="font-size:19px;font-weight:800;min-width:104px;letter-spacing:.5px;font-variant-numeric:tabular-nums">--:--</span>` +
    `<span id="kbTimerStatus" style="padding:2px 9px;border-radius:6px;font-size:12px;background:#334155">대기</span>` +
    `<span id="kbTimerMsg" style="flex:1;color:#fbbf24;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis"></span>` +
    `<button id="kbTimerClose" title="이 탭에서 숨기기" style="all:initial;color:#94a3b8;cursor:pointer;font:16px sans-serif;padding:0 4px">✕</button>`;
  (document.body || document.documentElement).appendChild(bar);

  const $ = (id) => bar.querySelector('#' + id);
  const elLabel = $('kbTimerLabel'), elTime = $('kbTimerTime'),
    elCount = $('kbTimerCount'), elStatus = $('kbTimerStatus'), elMsg = $('kbTimerMsg');

  // ── 발주 시각 계산: HH:MM → 오늘(지났으면 내일) ms ──
  function timeToDeadline(hhmm) {
    if (!hhmm) return 0;
    const [h, m] = hhmm.split(':').map(Number);
    if (isNaN(h)) return 0;
    const d = new Date(); d.setHours(h, m, 0, 0);
    let ms = d.getTime();
    if (ms < Date.now() - 60000) ms += 24 * 3600 * 1000;
    return ms;
  }

  // ── 소리 (포커스된 탭에서만) ──
  function beep() {
    if (!document.hasFocus()) return;
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      const ctx = new AC();
      [0, 0.6, 1.2].forEach((t) => {
        const o = ctx.createOscillator(), g = ctx.createGain();
        o.connect(g); g.connect(ctx.destination);
        o.type = 'sine'; o.frequency.value = 880;
        g.gain.setValueAtTime(0.0001, ctx.currentTime + t);
        g.gain.exponentialRampToValueAtTime(0.3, ctx.currentTime + t + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + t + 0.45);
        o.start(ctx.currentTime + t); o.stop(ctx.currentTime + t + 0.5);
      });
    } catch (_) { /* 무시 */ }
  }

  // ── 발주 시각 변경 시: 이미 지난 단계는 조용히 fired 처리 ──
  function resetFired() {
    fired = new Set();
    if (!deadline) return;
    const left = deadline - Date.now();
    STAGES.forEach((s) => { if (left <= s.min * 60000) fired.add(s.min); });
  }

  // ── 1초 틱: 카운트다운 + 상태 + 단계 알림 ──
  function tick() {
    if (!deadline) {
      elCount.textContent = '--:--';
      elStatus.textContent = '대기'; elStatus.style.background = '#334155';
      return;
    }
    const left = deadline - Date.now();
    if (left <= 0) {
      elCount.textContent = '00:00';
      elStatus.textContent = '🔴 마감'; elStatus.style.background = '#7f1d1d';
      elCount.style.color = '#f87171';
      return;
    }
    const sec = Math.floor(left / 1000);
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    elCount.textContent = (h > 0 ? `${h}:${String(m).padStart(2, '0')}` : String(m)) + ':' + String(s).padStart(2, '0');
    elCount.style.color = left <= 60000 ? '#f87171' : left <= 300000 ? '#fbbf24' : '#e2e8f0';
    elStatus.textContent = '🟢 베팅가능'; elStatus.style.background = '#14532d';

    // 단계 알림 (임계 진입 시 1회)
    for (const st of STAGES) {
      if (left <= st.min * 60000 && !fired.has(st.min)) {
        fired.add(st.min);
        elMsg.textContent = `🔔 ${st.min}분 전 · ${st.msg}`;
        msgUntil = Date.now() + 20000;
        beep();
      }
    }
    if (msgUntil && Date.now() > msgUntil) { elMsg.textContent = ''; msgUntil = 0; }
  }

  // ── 입력 → storage 저장 (모든 탭 공유) ──
  elTime.addEventListener('change', () => {
    // [발주감지] 사용자가 직접 입력하면 'manual'로 표시 → content.js 자동 감지가 이 경주에선 덮어쓰지 않음
    chrome.storage.local.set({ timerTime: elTime.value, timerDeadline: timeToDeadline(elTime.value), deadlineSource: 'manual' });
  });
  elLabel.addEventListener('change', () => chrome.storage.local.set({ timerLabel: elLabel.value }));
  $('kbTimerClose').addEventListener('click', () => bar.remove());

  // [4번] 배당판 탭에 '📊 분석기 열기' 버튼 — 분석기를 별도 창으로 연다.
  //   분석기 자신 페이지(127.0.0.1:8011)에는 표시하지 않는다.
  if (!/(?:127\.0\.0\.1|localhost):8011/.test(location.host)) {
    const ab = document.createElement('button');
    ab.id = 'kbOpenAnalyzer';
    ab.textContent = '📊 분석기 열기';
    ab.title = '경마배당분석기를 별도 창으로 엽니다 (배당판과 나란히 보기)';
    ab.style.cssText = 'all:initial;cursor:pointer;background:#2563eb;color:#fff;border-radius:5px;'
      + "padding:3px 9px;font:700 12px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin-left:2px";
    ab.addEventListener('click', () => { try { chrome.runtime.sendMessage({ type: 'OPEN_ANALYZER' }); } catch (_) { /* */ } });
    bar.insertBefore(ab, $('kbTimerClose'));
  }

  // ── storage 변경 → 모든 탭 동기화 ──
  function applyState(v) {
    if (v.timerLabel != null && document.activeElement !== elLabel) elLabel.value = v.timerLabel || '';
    if (v.timerTime != null && document.activeElement !== elTime) elTime.value = v.timerTime || '';
    const nd = v.timerDeadline || 0;
    if (nd !== deadline) { deadline = nd; resetFired(); }
    if (elLabel.value) elLabel.title = elLabel.value;
  }
  // [1번] 수집기(content.js)가 보내는 발주 임박/최종베팅 알림을 배너+소리로 표시
  function showCollectAlert(a) {
    if (!a || !a.text) return;
    elMsg.textContent = a.text;
    elMsg.style.color = a.level === '🚨' ? '#f87171' : a.level === '🟠' ? '#fbbf24' : '#fca5a5';
    msgUntil = Date.now() + 30000;
    beep();
    if (a.level === '🚨') { setTimeout(beep, 700); setTimeout(beep, 1400); } // 강한 알림(연속 비프)
  }

  // [경주 새로고침] 분석기 페이지(127.0.0.1) → 확장: 즉시 수집 트리거 릴레이.
  //   분석기 웹페이지는 chrome.runtime 에 직접 접근 못 하므로 timer.js 가 중계한다.
  window.addEventListener('message', (e) => {
    if (e.source !== window) return;
    const d = e.data;
    if (!d || d.source !== 'bmed-analyzer') return;
    if (d.type === 'FORCE_COLLECT') {
      try { chrome.runtime.sendMessage({ type: 'FORCE_COLLECT' }); } catch (_) { /* */ }
    }
    // [2번] 분석기 '별도 창으로 열기' → 확장이 '일반 창'으로 연다(포커스 잃어도 안 사라짐).
    //   처리했음을 ACK 로 알려 페이지가 window.open 폴백을 하지 않게 한다.
    if (d.type === 'OPEN_ANALYZER_WINDOW') {
      try { chrome.runtime.sendMessage({ type: 'OPEN_ANALYZER', force: true }); } catch (_) { /* */ }
      try { window.postMessage({ source: 'bmed-timer', type: 'OPEN_ANALYZER_ACK' }, '*'); } catch (_) { /* */ }
    }
    // [일괄 결과 등록] 분석기 페이지가 요청한 URL을 확장(로그인 세션)이 fetch → HTML을 되돌려준다.
    if (d.type === 'FETCH_RESULT_HTML' && d.url) {
      const reqId = d.reqId;
      try {
        chrome.runtime.sendMessage({ type: 'FETCH_RESULT_HTML', url: d.url }, (res) => {
          const err = chrome.runtime.lastError;
          window.postMessage({
            source: 'bmed-timer', type: 'FETCH_RESULT_HTML_ACK', reqId,
            ok: !!(res && res.ok), html: (res && res.html) || '',
            error: err ? err.message : (res && res.error) || '',
          }, '*');
        });
      } catch (ex) {
        window.postMessage({ source: 'bmed-timer', type: 'FETCH_RESULT_HTML_ACK', reqId, ok: false, error: String(ex) }, '*');
      }
    }
  });

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local') return;
    if (changes.collectAlert && changes.collectAlert.newValue) showCollectAlert(changes.collectAlert.newValue);
    if (changes.timerDeadline || changes.timerLabel || changes.timerTime) {
      chrome.storage.local.get({ timerDeadline: 0, timerLabel: '', timerTime: '' }, applyState);
    }
  });

  // ── 초기화 ──
  chrome.storage.local.get({ timerDeadline: 0, timerLabel: '', timerTime: '' }, (v) => {
    applyState(v); tick();
  });
  setInterval(tick, 1000);
})();
