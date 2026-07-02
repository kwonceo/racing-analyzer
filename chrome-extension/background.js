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

// 기본 설정 초기화
chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.get(
    { autoSend: false, intervalSec: 60, raceKey: '' },
    (v) => chrome.storage.local.set(v)
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

  if (msg?.type === 'CHECK_SERVER') {
    checkServer().then(sendResponse);
    return true;
  }
});
