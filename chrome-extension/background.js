/* =========================================================================
 * background.js — 서비스 워커
 * -------------------------------------------------------------------------
 * 모든 네트워크 I/O 를 여기서 처리한다.
 *   - keiba.go.jp(HTTPS) 페이지에서 http://127.0.0.1 로 직접 fetch 하면
 *     mixed-content/CORS 로 막히므로, 확장 프로그램 컨텍스트(host_permissions
 *     보유)에서 대신 POST 하여 우회한다.
 *   - 마지막 전송 결과/시간을 storage 에 저장 → 팝업이 상태 표시에 사용.
 * =======================================================================*/

const SERVER = 'http://127.0.0.1:8011';
const SNAPSHOT_URL = `${SERVER}/api/odds/snapshot`;
const RESULTS_URL = `${SERVER}/api/results/auto`;
const TRIPLE_URL = `${SERVER}/api/odds/triple/ingest`;
const ANALYZE_URL = `${SERVER}/api/odds/triple/analyze`;
const JAPAN_URL = `${SERVER}/api/extract/japan`;

/** 아이콘 배지: 성공=초록(카운트/✓), 실패=빨강(!). 잠시 후 자동 소거. */
function setBadge(ok, count) {
  try {
    chrome.action.setBadgeText({ text: ok ? (count != null ? String(count) : '✓') : '!' });
    chrome.action.setBadgeBackgroundColor({ color: ok ? '#22c55e' : '#ef4444' });
    setTimeout(() => chrome.action.setBadgeText({ text: '' }), 4000);
  } catch (_) { /* noop */ }
}

// 기본 설정 초기화 + [v2] 자동수집 엔진 복원
chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.get(
    { autoSend: false, intervalSec: 30, raceKey: '' },
    (v) => { chrome.storage.local.set(v); syncAutoEngine(); }
  );
});

async function postSnapshot(payload) {
  const res = await fetch(SNAPSHOT_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  let data = null;
  try { data = await res.json(); } catch (_) { /* noop */ }
  if (!res.ok) {
    throw new Error((data && data.error) || `HTTP ${res.status}`);
  }
  return data; // { snaps, series }
}

async function checkServer() {
  try {
    // 가벼운 요청으로 살아있는지 확인 (라우트가 없어도 응답 자체가 오면 online)
    const res = await fetch(`${SERVER}/`, { method: 'GET' });
    return { online: res.ok || res.status < 500 };
  } catch (_) {
    return { online: false };
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === 'POST_SNAPSHOT') {
    postSnapshot(msg.payload)
      .then((data) => {
        const status = {
          lastSend: Date.now(),
          lastOk: true,
          lastError: '',
          lastRaceKey: msg.payload.raceKey,
          lastSnaps: data?.snaps ?? null,
          lastReason: msg.reason || '',
        };
        chrome.storage.local.set({ status });
        setBadge(true, data?.snaps);
        sendResponse({ ok: true, data, status });
      })
      .catch((err) => {
        const status = {
          lastSend: Date.now(),
          lastOk: false,
          lastError: String(err.message || err),
          lastRaceKey: msg.payload?.raceKey || '',
          lastReason: msg.reason || '',
        };
        chrome.storage.local.set({ status });
        setBadge(false);
        sendResponse({ ok: false, error: status.lastError, status });
      });
    return true; // async
  }

  if (msg?.type === 'POST_RESULTS') {
    fetch(RESULTS_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(msg.payload),
    })
      .then(async (res) => {
        let d = null; try { d = await res.json(); } catch (_) { /* noop */ }
        if (!res.ok) throw new Error((d && d.error) || `HTTP ${res.status}`);
        return d;
      })
      .then((data) => {
        const resultStatus = {
          lastResult: Date.now(), lastResultOk: true, lastResultError: '',
          lastResultRaceKey: msg.payload.raceKey, lastTop3: data?.top3 || [], lastReason: msg.reason || '',
        };
        chrome.storage.local.set({ resultStatus });
        setBadge(true, (data?.top3 || []).length || '✓');
        sendResponse({ ok: true, data, status: resultStatus });
      })
      .catch((err) => {
        const resultStatus = {
          lastResult: Date.now(), lastResultOk: false, lastResultError: String(err.message || err),
          lastResultRaceKey: msg.payload?.raceKey || '',
        };
        chrome.storage.local.set({ resultStatus });
        setBadge(false);
        sendResponse({ ok: false, error: resultStatus.lastResultError, status: resultStatus });
      });
    return true; // async
  }

  if (msg?.type === 'POST_TRIPLE') {
    fetch(TRIPLE_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(msg.payload),
    })
      .then(async (res) => {
        let d = null; try { d = await res.json(); } catch (_) { /* noop */ }
        if (!res.ok) throw new Error((d && d.error) || `HTTP ${res.status}`);
        return d;
      })
      .then((data) => {
        const tripleStatus = {
          lastTriple: Date.now(), lastTripleOk: true, lastTripleError: '',
          lastTripleRaceKey: msg.payload.raceKey, lastCounts: data?.counts || {}, lastReason: msg.reason || '',
        };
        chrome.storage.local.set({ tripleStatus });
        const total = data && data.counts ? Object.values(data.counts).reduce((a, b) => a + b, 0) : '✓';
        setBadge(true, total);
        sendResponse({ ok: true, data, status: tripleStatus });
      })
      .catch((err) => {
        const tripleStatus = {
          lastTriple: Date.now(), lastTripleOk: false, lastTripleError: String(err.message || err),
          lastTripleRaceKey: msg.payload?.raceKey || '',
        };
        chrome.storage.local.set({ tripleStatus });
        setBadge(false);
        sendResponse({ ok: false, error: tripleStatus.lastTripleError, status: tripleStatus });
      });
    return true; // async
  }

  // [출마표2] 전적 + 배당 통합 분석 (/api/extract/japan)
  if (msg?.type === 'POST_JAPAN') {
    fetch(JAPAN_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(msg.payload),
    })
      .then(async (res) => {
        let d = null; try { d = await res.json(); } catch (_) { /* noop */ }
        if (!res.ok) throw new Error((d && d.error) || `HTTP ${res.status}`);
        return d;
      })
      .then((data) => {
        chrome.storage.local.set({ japanStatus: { t: Date.now(), ok: true, raceKey: msg.payload.raceKey, horses: (msg.payload.horses || []).length } });
        sendResponse({ ok: true, data });
      })
      .catch((err) => sendResponse({ ok: false, error: String(err.message || err) }));
    return true; // async
  }

  // [1번] 즉시 분석: 규칙기반 이상감지+유력마+삼복승추천 (서버가 최신 3종으로 계산)
  if (msg?.type === 'ANALYZE_TRIPLE') {
    fetch(ANALYZE_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ raceKey: msg.raceKey || '' }),
    })
      .then(async (res) => {
        let d = null; try { d = await res.json(); } catch (_) { /* noop */ }
        if (!res.ok) throw new Error((d && d.error) || `HTTP ${res.status}`);
        return d;
      })
      .then((data) => sendResponse({ ok: true, data }))
      .catch((err) => sendResponse({ ok: false, error: String(err.message || err) }));
    return true; // async
  }

  // [출마표2] keiba.go.jp DebaTable 등 교차출처 페이지 HTML 가져오기.
  //  content script(asyukk)에서 keiba.go.jp 로 직접 fetch 하면 CORS 로 막히므로,
  //  host_permissions 를 가진 서비스워커가 대신 가져와 HTML 문자열을 돌려준다.
  if (msg?.type === 'FETCH_URL') {
    fetch(msg.url, { credentials: 'omit' })
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.text();
      })
      .then((html) => sendResponse({ ok: true, html }))
      .catch((err) => sendResponse({ ok: false, error: String(err.message || err) }));
    return true; // async
  }

  // [일괄 결과 등록] 결과 페이지를 로그인 세션(쿠키)으로 가져와 HTML 반환.
  //   서버(127.0.0.1)는 asyukk 로그인 세션이 없어 직접 못 여는 URL을, host_permissions +
  //   credentials:'include' 를 가진 확장이 대신 fetch 한다. → 분석기 페이지가 서버로 전달해 파싱.
  if (msg?.type === 'FETCH_RESULT_HTML') {
    fetch(msg.url, { credentials: 'include' })
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.text();
      })
      .then((html) => sendResponse({ ok: true, html, finalUrl: msg.url }))
      .catch((err) => sendResponse({ ok: false, error: String(err.message || err) }));
    return true; // async
  }

  // [1번] 결과 자동수집 타이머 예약/취소 (chrome.alarms)
  if (msg?.type === 'SCHEDULE_RESULT_TIMER') {
    scheduleResultTimer(msg.raceKey, msg.deadline).then(sendResponse);
    return true;
  }
  if (msg?.type === 'CANCEL_RESULT_TIMER') {
    chrome.alarms.clear('resultCheck');
    chrome.storage.local.remove('resultTimer');
    chrome.storage.local.set({ resultAutoStatus: { state: 'cancelled', t: Date.now() } });
    sendResponse({ ok: true });
    return true;
  }

  if (msg?.type === 'CHECK_SERVER') {
    checkServer().then(sendResponse);
    return true;
  }

  // [경주 새로고침] 분석기의 '🔄 경주 새로고침' → 배당판에서 현재 경주 즉시 수집 + 분석.
  //   자동수집 ON/OFF 와 무관하게 1회 강제 수집한다(엔진 상태는 건드리지 않음).
  if (msg?.type === 'FORCE_COLLECT') {
    (async () => {
      try { await _collectOnce(); await _forceAnalyze(); sendResponse({ ok: true }); }
      catch (e) { sendResponse({ ok: false, error: String(e.message || e) }); }
    })();
    return true; // async
  }

  // [4번] 배당판의 '📊 분석기 열기' → 분석기를 별도 '일반 창'으로 열기(이미 있으면 포커스).
  //   msg.force=true 면 재사용하지 않고 항상 새 창을 만든다(분석기 안의 '별도 창으로 열기'용).
  if (msg?.type === 'OPEN_ANALYZER') {
    openAnalyzerWindow(!!msg.force).then(sendResponse);
    return true; // async
  }
});

// ── [별도 창] 분석기 창 열기 + 위치/크기 기억 ──────────────────────────
//   [2번 수정] popup 타입은 포커스를 잃으면 일부 환경에서 뒤로 숨는다 → 'normal'(일반 창)으로 연다.
//   일반 창은 작업표시줄에 남고 포커스를 잃어도 사라지지 않는다.
const ANALYZER_URL = `${SERVER}/`;
let _analyzerWinId = null;

async function openAnalyzerWindow(force) {
  // force 가 아니면: 이미 열린 분석기 창이 있으면 새로 만들지 않고 포커스
  if (!force) {
    try {
      const wins = await chrome.windows.getAll({ populate: true });
      for (const w of wins) {
        if ((w.tabs || []).some((t) => (t.url || '').startsWith(ANALYZER_URL))) {
          await chrome.windows.update(w.id, { focused: true, drawAttention: true });
          _analyzerWinId = w.id;
          return { ok: true, reused: true };
        }
      }
    } catch (_) { /* */ }
  }
  // 저장된 위치/크기(analyzerGeom)로 '일반 창' 생성
  const { analyzerGeom } = await chrome.storage.local.get({ analyzerGeom: null });
  const opts = {
    url: ANALYZER_URL + '?popup=1', type: 'normal', focused: true,
    width: (analyzerGeom && analyzerGeom.width) || 1200,
    height: (analyzerGeom && analyzerGeom.height) || 900,
  };
  if (analyzerGeom && analyzerGeom.left != null) { opts.left = analyzerGeom.left; opts.top = analyzerGeom.top; }
  try { const w = await chrome.windows.create(opts); _analyzerWinId = w.id; return { ok: true }; }
  catch (e) { return { ok: false, error: String(e.message || e) }; }
}

// [2번] 분석기 창의 위치/크기가 바뀌면 저장 → 다음에 같은 위치로 연다.
if (chrome.windows.onBoundsChanged) {
  chrome.windows.onBoundsChanged.addListener((w) => {
    if (w.id === _analyzerWinId && w.width > 200 && w.height > 200) {
      chrome.storage.local.set({ analyzerGeom: { left: w.left, top: w.top, width: w.width, height: w.height } });
    }
  });
}
chrome.windows.onRemoved.addListener((id) => { if (id === _analyzerWinId) _analyzerWinId = null; });

// ── [1번] 결과 자동수집 타이머 (발주 후 10/12/14분 = 종료 7분후 + 재시도 2회) ──
const RESULT_CHECK_OFFSETS = [10, 12, 14]; // 발주시각 기준 분

async function scheduleResultTimer(raceKey, deadline) {
  if (!deadline) return { ok: false, error: '발주시각(타이머)을 먼저 설정하세요.' };
  const times = RESULT_CHECK_OFFSETS.map((m) => deadline + m * 60000);
  await chrome.storage.local.set({
    resultTimer: { raceKey: raceKey || '', deadline, times, idx: 0 },
    resultAutoStatus: { state: 'scheduled', raceKey: raceKey || '', nextAt: times[0], t: Date.now() },
  });
  chrome.alarms.create('resultCheck', { when: Math.max(Date.now() + 3000, times[0]) });
  return { ok: true, firstCheck: times[0] };
}

chrome.alarms.onAlarm.addListener(async (alarm) => {
  // [v2] 자동수집 하트비트/단계 알람 → 엔진 tick (서비스워커가 죽어도 알람이 부활시킴)
  if (alarm.name === AUTO_ALARM || /^stageT/.test(alarm.name)) { autoTick('alarm'); return; }
  // [무변동 소프트 일시중지] 60초 뒤 재점검 → 엔진 재동기화(autoSend 유지면 자동 재개)
  if (alarm.name === 'resumeCheck') { syncAutoEngine(); return; }
  // [v2.0.1] 발주 후 결과 자동수집 알람
  if (/^resFetch\d/.test(alarm.name)) { doResultFetch(parseInt(alarm.name.replace('resFetch', ''), 10)); return; }
  if (alarm.name !== 'resultCheck') return;
  const { resultTimer } = await chrome.storage.local.get({ resultTimer: null });
  if (!resultTimer) return;
  const { raceKey, times, idx } = resultTimer;

  // asyukk 탭을 찾아 결과 수집 지시 (content script 가 경주결과 탭 클릭 + 추출)
  let done = false, data = null;
  try {
    const tabs = await chrome.tabs.query({ url: ['*://*.qwqwd25.net/*'] });
    if (tabs.length) {
      const res = await chrome.tabs.sendMessage(tabs[0].id, { type: 'COLLECT_RESULTS', reason: 'timer' })
        .catch(() => null);
      if (res && res.ok) { done = true; data = res.data || null; }
    } else {
      console.warn('[결과타이머] asyukk 탭이 열려있지 않아 결과를 수집할 수 없습니다.');
    }
  } catch (e) { console.warn('[결과타이머] 수집 오류', e); }

  if (done) {
    await chrome.storage.local.remove('resultTimer');
    await chrome.storage.local.set({
      resultAutoStatus: { state: 'done', raceKey, top3: data && data.top3, hit: data && data.hit, t: Date.now() },
    });
    setBadge(true, '✓');
  } else {
    const nextIdx = idx + 1;
    if (nextIdx < times.length) {
      resultTimer.idx = nextIdx;
      await chrome.storage.local.set({
        resultTimer,
        resultAutoStatus: { state: 'retry', raceKey, attempt: nextIdx + 1, nextAt: times[nextIdx], t: Date.now() },
      });
      chrome.alarms.create('resultCheck', { when: Math.max(Date.now() + 3000, times[nextIdx]) });
    } else {
      await chrome.storage.local.remove('resultTimer');
      await chrome.storage.local.set({ resultAutoStatus: { state: 'manual', raceKey, t: Date.now() } });
      setBadge(false);
    }
  }
});


/* ═══════════════════ [v2.0.0] 백그라운드 자동수집 엔진 ═══════════════════
 * 팝업이 닫혀도, 다른 탭으로 이동해도 자동수집이 계속되도록 타이머를
 * content.js(탭) → background.js(서비스워커) 로 이관한다.
 *   - chrome.alarms(30초 하트비트): 서비스워커가 잠들어도 다시 깨워 수집 지속
 *   - 살아있는 동안은 fine setInterval(5초 점검)로 실제 간격 준수
 *       · 평상시 기본 간격(30초) · 마감 3분전(T-3) → 15초로 단축
 *   - 발주 임박 단계는 chrome.notifications 로 팝업 없이도 알림:
 *       · T-1분: 이상감지 강제 + "🚨 마감 1분전! 복승 A+B 급락감지"
 *       · T-30초: "⏰ 30초! 지금 베팅: 복승 A+B"
 *       · T-0: 수집 중지 + "🏁 경주 시작"
 * ===================================================================== */
const AUTO_ALARM = 'autoCollectTick';           // 30초 하트비트(서비스워커 부활)
const AUTO_STATUS_URL = `${SERVER}/api/auto/status`;
let _fineTimer = null, _keepaliveTimer = null, _collecting = false, _nextDueAt = 0;

function _autoCfg() {
  return new Promise((r) => chrome.storage.local.get(
    { autoSend: false, intervalSec: 30, autoMode: 'triple', timerDeadline: 0, market: 'auto', japanType: 'local' }, r));
}
function _setAutoStatus(s) {
  const st = Object.assign({ t: Date.now() }, s);
  chrome.storage.local.set({ autoCollectStatus: st });
  // [4번] 분석기(웹)는 chrome.storage 를 못 읽으므로 서버로도 상태를 흘려보낸다.
  try { fetch(AUTO_STATUS_URL, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(st) }); } catch (_) { /* */ }
}

// 단계 발화 1회성 보장(서비스워커가 죽었다 살아나도 재발화 안 되게 storage 로 영속화)
async function _stageFiredOnce(deadline, key) {
  const { autoStageFired } = await chrome.storage.local.get({ autoStageFired: null });
  const f = (autoStageFired && autoStageFired.deadline === deadline) ? autoStageFired : { deadline, stages: [] };
  if (f.stages.includes(String(key))) return true;
  f.stages.push(String(key));
  await chrome.storage.local.set({ autoStageFired: f });
  return false;
}

async function syncAutoEngine() {
  const cfg = await _autoCfg();
  if (!cfg.autoSend) { stopAutoEngine(); return; }
  chrome.alarms.create(AUTO_ALARM, { periodInMinutes: 0.5 });   // 30초 하트비트
  ['stageT1', 'stageT30', 'stageT0', 'stageTjra2', 'stageTjraClose'].forEach((n) => chrome.alarms.clear(n));
  const now = Date.now();
  const isCentral = cfg.market !== 'korea' && cfg.japanType === 'central';   // [3번] 중앙(JRA)
  if (cfg.timerDeadline) {   // 발주 임박 단계 정확 발화용 1회성 백업 알람
    if (isCentral) {
      // [3번] 중앙: 마감 2분전(이상감지 확정), 1분30초전(배당 마감·수집 중지)
      if (cfg.timerDeadline - 120000 > now) chrome.alarms.create('stageTjra2', { when: cfg.timerDeadline - 120000 });
      if (cfg.timerDeadline - 90000 > now) chrome.alarms.create('stageTjraClose', { when: cfg.timerDeadline - 90000 });
    } else {
      if (cfg.timerDeadline - 60000 > now) chrome.alarms.create('stageT1', { when: cfg.timerDeadline - 60000 });
      if (cfg.timerDeadline - 30000 > now) chrome.alarms.create('stageT30', { when: cfg.timerDeadline - 30000 });
      if (cfg.timerDeadline > now) chrome.alarms.create('stageT0', { when: cfg.timerDeadline });
    }
    // [v2.0.1] 발주 후 결과 자동수집(7/9/11분) 예약 — 이미 수집 성공했으면 재예약 안 함
    ['resFetch0', 'resFetch1', 'resFetch2'].forEach((n) => chrome.alarms.clear(n));
    const { resultCollected } = await chrome.storage.local.get({ resultCollected: null });
    if (!(resultCollected && resultCollected.deadline === cfg.timerDeadline)) {
      [7, 9, 11].forEach((min, i) => { const at = cfg.timerDeadline + min * 60000; if (at > now) chrome.alarms.create('resFetch' + i, { when: at }); });
      const firstAt = cfg.timerDeadline + 7 * 60000;
      if (firstAt > now) chrome.storage.local.set({ resultAutoStatus: { state: 'scheduled', raceKey: '', nextAt: firstAt, t: Date.now() } });
    }
  }
  _ensureFineLoop();
  autoTick('start');
}
function stopAutoEngine() {
  chrome.alarms.clear(AUTO_ALARM);
  ['stageT1', 'stageT30', 'stageT0', 'stageTjra2', 'stageTjraClose'].forEach((n) => chrome.alarms.clear(n));
  if (_fineTimer) { clearInterval(_fineTimer); _fineTimer = null; }
  if (_keepaliveTimer) { clearInterval(_keepaliveTimer); _keepaliveTimer = null; }
}
function _ensureFineLoop() {
  if (!_fineTimer) _fineTimer = setInterval(() => autoTick('fine'), 5000);
  // keepalive: 20초마다 가벼운 API 호출로 서비스워커 idle 타이머 리셋(마감 임박 정밀도 유지)
  if (!_keepaliveTimer) _keepaliveTimer = setInterval(() => { try { chrome.runtime.getPlatformInfo(() => {}); } catch (_) { /* */ } }, 20000);
}

async function _findOddsTab() {
  const tabs = await chrome.tabs.query({ url: ['*://*.keiba.go.jp/*', '*://*.qwqwd25.net/*'] });
  if (!tabs.length) return null;
  return tabs.find((t) => /Odds|배당|TodayRaceInfo|DebaTable/i.test(t.url || '')) || tabs[0];
}
async function _collectOnce() {
  const tab = await _findOddsTab();
  if (!tab) { _setAutoStatus({ running: true, warn: '배당판 탭이 열려있지 않음' }); return null; }
  try {
    const r = await chrome.tabs.sendMessage(tab.id, { type: 'AUTO_COLLECT', reason: 'bg' });
    // [수정2] 경기 마감 감지 시 자동수집 엔진 정지
    if (r && r.closed) { await _onRaceClosed(r.closeReason || ''); }
    return r;
  } catch (e) {
    _setAutoStatus({ running: true, warn: '수집 탭 응답 없음(페이지 새로고침 필요)' });
    return null;
  }
}

// [마감 처리] 확정 마감(DOM 발매마감)만 완전 정지, 무변동(추정)은 자동 재개되는 소프트 일시중지.
//   [버그수정] 예전엔 무변동 추정에도 autoSend 를 꺼버려, 한 번 오검출되면 수집이 꺼진 채 배너가 안 사라졌다.
//   결과 자동수집(발주 후 7/9/11분 resFetch 알람)은 어느 경우든 유지된다.
async function _onRaceClosed(reason) {
  const definitive = /DOM|발매\s*마감|발매\s*종료|투표|접수|締|販売|受付|終了/.test(reason || '');
  stopAutoEngine();
  if (definitive) {
    await chrome.storage.local.set({ autoSend: false });   // 확정 마감만 완전 정지(설정 OFF)
    _setAutoStatus({ running: false, stopped: true, closed: true, closeReason: reason });
    _notify('closed', '⏹ 경기 마감 - 자동수집 중단됨',
      `배당이 마감되어 자동수집을 중단했습니다.${reason ? ' (' + reason + ')' : ''}`, false);
  } else {
    // 무변동(추정) — autoSend 유지, 소프트 일시중지. 60초 뒤 재점검(배당 변동/새 경주면 자동 재개).
    _setAutoStatus({ running: false, paused: true, closeReason: reason });
    chrome.alarms.create('resumeCheck', { when: Date.now() + 60000 });
  }
}
async function _forceAnalyze() {
  try {
    const res = await fetch(ANALYZE_URL, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ raceKey: '' }) });
    const d = await res.json();
    if (res.ok) chrome.storage.local.set({ analyzeStatus: { data: d, at: Date.now() } });
    return res.ok ? d : null;
  } catch (_) { return null; }
}
function _mainBet(d) { const r = (d && d.betRecommend) || []; const m = r.find((x) => x.label === '복승 메인') || r[0]; return m ? ('복승 ' + m.combo.join('+')) : ''; }
function _trioBet(d) { const r = (d && d.betRecommend) || []; const m = r.find((x) => x.label === '삼복승 메인'); return m ? m.combo.join('+') : ''; }
function _topDrop(d) { const dr = (d && d.drops) || []; const x = dr.find((y) => y.pct < 0); return x ? `복승 ${x.combo[0]}+${x.combo[1]} (${x.pct}%) 급락감지` : ''; }

// [4번] 실시간 이상감지 연속 업데이트 — 매 수집마다 '새로' 발생한 급락만 알림(기존은 유지).
let _seenDropKeys = new Set(), _seenDropDeadline = 0;
function _notifyNewDrops(a, deadline) {
  if (deadline !== _seenDropDeadline) { _seenDropKeys = new Set(); _seenDropDeadline = deadline; }  // 새 경주 → 초기화
  const drops = ((a && a.drops) || []).filter((d) => d.pct < 0 && Array.isArray(d.combo));
  const fresh = drops.filter((d) => !_seenDropKeys.has(`${d.combo[0]}+${d.combo[1]}`));
  drops.forEach((d) => _seenDropKeys.add(`${d.combo[0]}+${d.combo[1]}`));
  if (!fresh.length) return;
  const top = fresh.slice().sort((x, y) => x.pct - y.pct)[0];   // 가장 큰 급락 먼저
  _notify('newdrop', '🔴 새 이상감지', `${top.combo[0]}+${top.combo[1]} 복승 ${top.pct}%`
    + (fresh.length > 1 ? ` 외 ${fresh.length - 1}건` : ''), false);
}

function _notify(id, title, message, strong) {
  try {
    chrome.notifications.create('kb_' + id + '_' + Date.now(), {
      type: 'basic', iconUrl: 'icons/icon128.png', title, message,
      priority: 2, requireInteraction: !!strong, buttons: [{ title: '📊 분석기 열기' }],
    });
  } catch (_) { /* */ }
  // 온페이지 배너+소리(timer.js) 병행 — 배당 페이지/분석기 탭에서도 보이고 들린다.
  chrome.storage.local.set({ collectAlert: { level: strong ? '🚨' : '🟠', text: title + ' · ' + (message || '').split('\n')[0], at: Date.now() } });
}

async function autoTick(reason) {
  const cfg = await _autoCfg();
  if (!cfg.autoSend) { stopAutoEngine(); _setAutoStatus({ running: false }); return; }
  _ensureFineLoop();
  const now = Date.now();
  const left = cfg.timerDeadline ? (cfg.timerDeadline - now) : null;
  const isCentral = cfg.market !== 'korea' && cfg.japanType === 'central';   // [1·3번] 중앙(JRA)

  // T-0: 수집 중지 + 경주 시작 알림
  if (left != null && left <= 0) {
    if (!(await _stageFiredOnce(cfg.timerDeadline, 'stop'))) _notify('start', '🏁 경주 시작', '배당 수집을 자동 중지했습니다.', false);
    stopAutoEngine();
    _setAutoStatus({ running: false, stopped: true, deadline: cfg.timerDeadline });
    return;
  }

  // [3번] 중앙(JRA): 배당이 1분30초전에 마감 → T-1분30초에 수집 자동 중지 + 마감 알림
  if (isCentral && left != null && left <= 90000) {
    if (!(await _stageFiredOnce(cfg.timerDeadline, 'jra-close'))) {
      const a = await _forceAnalyze();
      _notify('jraClose', '⏰ 배당 마감! 지금 베팅하세요', `${_mainBet(a) || '데이터 부족'}`, true);
    }
    stopAutoEngine();
    _setAutoStatus({ running: false, stopped: true, deadline: cfg.timerDeadline });
    return;
  }

  // [4번] T-3분 이상감지 자동 시작 — 수집 간격 단축 + 1회 안내
  if (left != null && left <= 180000 && left > 120000 && !(await _stageFiredOnce(cfg.timerDeadline, 't3'))) {
    _notify('t3', '🔔 마감 3분 · 이상감지 집중 감시 시작', '수집 간격 단축: T-3분 10초 · T-1분 5초', false);
  }
  // 수집(due 시각에만 실제 수집 — fine 5초 점검이지만 간격은 지킴)
  const baseMs = Math.max(5, Number(cfg.intervalSec) || 30) * 1000;
  // [수집속도 개선] 마감 임박 수집 간격 단계 단축 — 마감 전 급락 신호를 놓치지 않게
  //   T-3분(≤180s) 10초 · T-1분(≤60s) 5초 · 평상시 기본(30초). (이전: T-3분 15초·T-1분 10초)
  let intervalMs = baseMs;
  if (left != null) {
    if (left <= 60000) intervalMs = 5000;         // 마감 1분전부터 5초 간격
    else if (left <= 180000) intervalMs = 10000;  // 마감 3분전부터 10초 간격
  }
  if ((reason === 'start' || now >= _nextDueAt) && !_collecting) {
    _collecting = true;
    let r = null;
    try { r = await _collectOnce(); } catch (_) { /* */ }
    _collecting = false;
    // [수정2] 경기 마감 감지 → 엔진이 이미 정지됨. 상태를 running:true 로 덮어쓰지 않고 종료.
    if (r && r.closed) return;
    _nextDueAt = Date.now() + intervalMs;
    _setAutoStatus({ running: true, last: Date.now(), next: _nextDueAt, deadline: cfg.timerDeadline || 0, intervalMs });
    // [4번] 수집 직후 이상감지 재실행 → 화면 갱신 + '새로' 발생한 급락만 즉시 알림
    _forceAnalyze().then((a) => _notifyNewDrops(a, cfg.timerDeadline || 0));
  }

  if (isCentral) {
    // [3번] 중앙(JRA): 마감 2분전 → 이상감지 강제 실행 + 최종 베팅 확정 알림
    if (left != null && left <= 120000 && !(await _stageFiredOnce(cfg.timerDeadline, 'jra-t2'))) {
      const a = await _forceAnalyze();
      const drop = _topDrop(a), bet = _mainBet(a);
      _notify('jraT2', '🚨 마감 2분전 이상감지 완료',
        `${drop || '급락 신호 확인'}\n최종 베팅 확정: ${bet || '데이터 부족'}`, true);
    }
  } else {
    // [4번] T-2분: 강제 이상감지 실행(지방/한국)
    if (left != null && left <= 120000 && left > 60000 && !(await _stageFiredOnce(cfg.timerDeadline, 't2'))) {
      const a = await _forceAnalyze();
      _notify('t2', '🚨 마감 2분전 이상감지 완료',
        `${_topDrop(a) || '급락 신호 확인'}\n베팅 후보: ${_mainBet(a) || '데이터 부족'}`, true);
    }
    // [지방/기존] T-1분: 이상감지 강제 + 최종 베팅 확정
    if (left != null && left <= 60000 && !(await _stageFiredOnce(cfg.timerDeadline, 't1'))) {
      const a = await _forceAnalyze();
      const drop = _topDrop(a), bet = _mainBet(a), trio = _trioBet(a);
      _notify('t1', '🚨 마감 1분전 이상감지',
        `${drop || '급락 신호 확인'}\n최종 베팅: ${bet || '데이터 부족'}${trio ? (' / 삼복승 ' + trio) : ''}`, true);
    }
    // T-30초: 최종 알림
    if (left != null && left <= 30000 && !(await _stageFiredOnce(cfg.timerDeadline, 't30'))) {
      const a = await _forceAnalyze();
      _notify('t30', '⏰ 30초! 지금 베팅', `${_mainBet(a) || '데이터 부족'}`, true);
    }
  }
}

// [v2.0.1] 발주 후 결과 자동수집(fetch 방식). attempt 0/1/2 = 발주+7/9/11분.
async function doResultFetch(attempt) {
  const cfg = await _autoCfg();
  const { resultCollected } = await chrome.storage.local.get({ resultCollected: null });
  if (resultCollected && resultCollected.deadline === cfg.timerDeadline) return;   // 이미 성공
  const tab = await _findOddsTab();
  let res = null;
  if (tab) { try { res = await chrome.tabs.sendMessage(tab.id, { type: 'COLLECT_RESULTS_FETCH', reason: 'auto-result' }); } catch (_) { res = null; } }
  if (res && res.ok) {
    await chrome.storage.local.set({ resultCollected: { deadline: cfg.timerDeadline, t: Date.now() } });
    ['resFetch0', 'resFetch1', 'resFetch2'].forEach((n) => chrome.alarms.clear(n));
    const hit = res.hit || {};
    const win = hit.quinella || hit.trifecta || hit.was_hit;
    const top3 = res.top3 || [];
    chrome.storage.local.set({ resultAutoStatus: { state: 'done', raceKey: res.raceKey, top3, hit, t: Date.now() } });
    const t3 = top3.slice(0, 3).map((n, i) => `${i + 1}착 ${n}번`).join(' / ');
    // 스펙: "✅ 서울 5R 결과 수집 · 복승 3+7 적중!" — 복승 적중 시 조합 표시
    const q = top3.slice(0, 2);
    const hitMsg = hit.quinella ? ` · 복승 ${q.join('+')} 적중!`
      : hit.trifecta ? ` · 삼복승 ${top3.slice(0, 3).join('+')} 적중!`
      : win ? ' · 적중!' : ' · 미적중';
    _notify('result', `✅ ${res.raceKey || '경주'} 결과 수집`, `${t3}${hitMsg}`, false);
    setBadge(true, '✓');
  } else {
    const last = attempt >= 2;
    const nextMin = attempt === 0 ? 9 : 11;
    const nextAt = (!last && cfg.timerDeadline) ? cfg.timerDeadline + nextMin * 60000 : null;
    chrome.storage.local.set({ resultAutoStatus: { state: last ? 'manual' : 'retry', attempt: attempt + 2, nextAt, raceKey: '', t: Date.now() } });
    if (last) _notify('resultFail', '❌ 결과 수집 실패', '수동 확인이 필요합니다.', false);
  }
}

chrome.notifications.onButtonClicked.addListener(() => { chrome.tabs.create({ url: `${SERVER}/` }); });
chrome.notifications.onClicked.addListener(() => { chrome.tabs.create({ url: `${SERVER}/` }); });

// 설정(autoSend/간격/발주시각) 변경 → 엔진 즉시 동기화
chrome.storage.onChanged.addListener((ch, area) => {
  if (area !== 'local') return;
  if (ch.autoSend || ch.intervalSec || ch.autoMode || ch.timerDeadline || ch.market || ch.japanType) syncAutoEngine();
});
// 브라우저 시작/서비스워커 부활 시 엔진 복원
chrome.runtime.onStartup.addListener(syncAutoEngine);
// 로드 즉시 1회 동기화(이미 autoSend 켜져 있으면 바로 재개)
syncAutoEngine();
