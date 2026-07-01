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
        sendResponse({ ok: false, error: status.lastError, status });
      });
    return true; // async
  }

  if (msg?.type === 'CHECK_SERVER') {
    checkServer().then(sendResponse);
    return true;
  }
});
