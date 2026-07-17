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
// [스펙2·3] 결과 자동수집 성공/실패 상태를 분석기(웹)로 흘려보내는 브리지.
//   웹페이지는 chrome.storage 를 못 읽으므로, 서버에 상태를 남겨 분석기가 폴링한다.
const RESULT_AUTO_STATUS_URL = `${SERVER}/api/results/auto-status`;
function _postResultAutoStatus(obj) {
  try {
    fetch(RESULT_AUTO_STATUS_URL, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign({ t: Date.now() }, obj)),
    });
  } catch (_) { /* 서버 꺼져 있어도 무시(로컬 알림은 별개로 동작) */ }
}
const TRIPLE_URL = `${SERVER}/api/odds/triple/ingest`;
const ANALYZE_URL = `${SERVER}/api/odds/triple/analyze`;
const JAPAN_URL = `${SERVER}/api/extract/japan`;
const RESULT_OCR_URL = `${SERVER}/api/result/ocr`;             // [캡쳐+OCR] 결과 화면 판독
const RECORD_RESULT_URL = `${SERVER}/api/history/record-result`; // [캡쳐+OCR] 판독 착순 저장
const REVIEW_SAVE_URL = `${SERVER}/api/review/save`;             // [복기 저장] 중요신호+결과 묶음 저장

// [보완] fetch 실패 원인 친절 변환: "Failed to fetch"(연결 거부=서버 꺼짐)를
//   명확한 안내로 바꿔 팝업/오버레이 상태에 그대로 노출 → 원인 즉시 파악.
function svrErr(err) {
  const m = String((err && err.message) || err || '');
  if (/Failed to fetch|NetworkError|ERR_CONNECTION|load failed/i.test(m)) {
    return '분석 서버 꺼짐(127.0.0.1:8011 응답 없음) — 서버(경마분석기)를 실행하세요';
  }
  return m || '전송 실패';
}

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
  scheduleKeirinDaily();   // [경륜 스케줄] 매일 08:00 자동 수집 알람 등록
});

// ═══ [확장 경유 경륜 스케줄 자동 수집] oddspark 경륜은 로그인 필요 → 로그인 세션 보유한 확장이
//   KaisaiRaceList를 fetch·파싱해 서버로 POST(FETCH_RESULT_HTML과 동일 패턴). 하루 1회(오전 8시). ═══
const KEIRIN_JO_MAP = {   // joCode → 경륜장(한글). 없으면 title에서 파싱·폴백
  '36': '오다와라', '62': '히로시마', '01': '마에바시', '04': '기후', '85': '사세보',
  '83': '구루메', '81': '고쿠라', '31': '마쓰도', '45': '히라쓰카', '48': '가와사키', '56': '기시와다',
  '24': '우쓰노미야',   // 宇都宮競輪(oddspark joCode 24)
  // [joCode 업데이트 2026-07-14·라이브 확인] 기시와다 73→56 교정 + 신규 4곳
  '11': '하코다테', '61': '다마노', '27': '케이오카쿠', '87': '구마모토',
};
function _todayYmd() {
  const d = new Date();
  return '' + d.getFullYear() + String(d.getMonth() + 1).padStart(2, '0') + String(d.getDate()).padStart(2, '0');
}
async function _oddsparkFetch(url) {
  const res = await fetch(url, { credentials: 'include' });   // 로그인 세션(쿠키) 포함
  if (!res.ok) throw new Error('HTTP ' + res.status);
  return res.text();
}
async function fetchKeirinSchedule() {
  try {
    const ymd = _todayYmd();
    const listHtml = await _oddsparkFetch('https://www.oddspark.com/keirin/KaisaiRaceList.do?kaisaiBi=' + ymd);
    // 개최 경륜장 joCode 추출(중복 제거)
    const jos = [...new Set([...listHtml.matchAll(/joCode=(\d{1,3})/g)].map((m) => m[1]))];
    const tracks = [];
    for (const jo of jos) {
      try {
        const rlHtml = await _oddsparkFetch('https://www.oddspark.com/keirin/RaceList.do?joCode=' + jo + '&kaisaiBi=' + ymd);
        // 경륜장명: title '…〇〇競輪…' 에서, 없으면 매핑/폴백
        let venue = KEIRIN_JO_MAP[jo] || '';
        if (!venue) { const tm = rlHtml.match(/<title>[^<]*?([가-힣]{2,6}|[一-龯]{2,5})\s*(?:競輪|경륜)/); if (tm) venue = tm[1]; }
        if (!venue) venue = '경륜' + jo;
        // 경주번호 + 発走時刻(HH:MM) 파싱(방어적) — raceNo 링크 + 부근 시각
        const races = {};
        for (const seg of rlHtml.split(/<\/tr>|<\/li>|<tr[ >]|<li[ >]/)) {
          const mn = seg.match(/raceNo=(\d{1,2})/);
          if (!mn) continue;
          const rno = parseInt(mn[1], 10);
          if (rno < 1 || rno > 12 || races[rno]) continue;
          const mt = seg.match(/(?:発走時刻|発走時間|発走|締切)[^\d]{0,6}(\d{1,2}:\d{2})/) || seg.match(/\b(\d{1,2}:\d{2})\b/);
          races[rno] = { raceNo: rno, postTime: mt ? mt[1] : null };
        }
        const raceList = Object.keys(races).map((k) => races[k]).sort((a, b) => a.raceNo - b.raceNo);
        if (raceList.length) tracks.push({ joCode: jo, venue: venue, races: raceList });
      } catch (e) { console.log('[경륜스케줄]', jo, '실패(건너뜀):', e.message || e); }
    }
    if (tracks.length) {
      await fetch(SERVER + '/api/multi/keirin-schedule', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ymd: ymd, tracks: tracks }),
      });
      console.log('[경륜스케줄] 서버 전송 완료:', tracks.length, '개 경륜장');
    } else {
      console.log('[경륜스케줄] 개최 없음 또는 미로그인(로그인 필요)');
    }
  } catch (e) {
    console.log('[경륜스케줄] 실패(로그인/네트워크?):', e.message || e);
  }
}
function scheduleKeirinDaily() {
  // 다음 오전 8시로 알람 예약(이미 지났으면 내일 8시) + 24시간 주기
  const now = new Date();
  const next = new Date(now); next.setHours(8, 0, 0, 0);
  if (next.getTime() <= now.getTime()) next.setDate(next.getDate() + 1);
  chrome.alarms.create('keirinSchedule', { when: next.getTime(), periodInMinutes: 1440 });
}

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
          lastError: svrErr(err),
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
          lastResult: Date.now(), lastResultOk: false, lastResultError: svrErr(err),
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
          lastTriple: Date.now(), lastTripleOk: false, lastTripleError: svrErr(err),
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
      .catch((err) => sendResponse({ ok: false, error: svrErr(err) }));
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
      .catch((err) => sendResponse({ ok: false, error: svrErr(err) }));
    return true; // async
  }

  // [캡쳐+OCR] 현재 보이는 탭(경주결과 화면) 캡쳐 → dataURL 반환.
  //   캡쳐 직전 오버레이를 잠깐 숨겨(결과를 가리지 않게) 찍고 바로 복원한다.
  if (msg?.type === 'CAPTURE_TAB') {
    (async () => {
      let tabId = null;
      try {
        const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
        tabId = tabs && tabs[0] && tabs[0].id;
        // 오버레이(패널·칩·강조팝업) 잠깐 숨김 → 결과 가림 방지
        if (tabId != null) { try { await chrome.tabs.sendMessage(tabId, { type: 'KB_CAPTURE_PREP', hide: true }); } catch (_) { /* 오버레이 없을 수도 */ } }
        await new Promise((r) => setTimeout(r, 180));   // 리페인트 대기
        chrome.tabs.captureVisibleTab(null, { format: 'png' }, (dataUrl) => {
          // 캡쳐 후 즉시 오버레이 복원
          if (tabId != null) { try { chrome.tabs.sendMessage(tabId, { type: 'KB_CAPTURE_PREP', hide: false }); } catch (_) { /* */ } }
          if (chrome.runtime.lastError || !dataUrl) {
            sendResponse({ ok: false, error: (chrome.runtime.lastError && chrome.runtime.lastError.message) || '캡쳐 실패' });
          } else {
            sendResponse({ ok: true, dataUrl });
          }
        });
      } catch (e) {
        if (tabId != null) { try { chrome.tabs.sendMessage(tabId, { type: 'KB_CAPTURE_PREP', hide: false }); } catch (_) { /* */ } }
        sendResponse({ ok: false, error: String(e.message || e) });
      }
    })();
    return true; // async
  }

  // [배당판 스냅샷] 현재 보이는 탭을 캡처하되 오버레이(강조·패널)를 '유지'한 채 찍는다(CAPTURE_TAB 은 오버레이 숨김=OCR용).
  if (msg?.type === 'CAPTURE_BOARD') {
    (async () => {
      try {
        chrome.tabs.captureVisibleTab(null, { format: 'png' }, (dataUrl) => {
          if (chrome.runtime.lastError || !dataUrl) {
            sendResponse({ ok: false, error: (chrome.runtime.lastError && chrome.runtime.lastError.message) || '캡처 실패' });
          } else {
            sendResponse({ ok: true, dataUrl });
          }
        });
      } catch (e) {
        sendResponse({ ok: false, error: String(e.message || e) });
      }
    })();
    return true; // async
  }

  // [배당판 스냅샷] 워터마크 합성된 base64 PNG + 메타를 서버에 저장(POST /api/snapshot/save).
  if (msg?.type === 'SAVE_BOARD_SNAPSHOT') {
    fetch(`${SERVER}/api/snapshot/save`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(msg.payload || {}),
    })
      .then(async (res) => {
        let d = null; try { d = await res.json(); } catch (_) { /* noop */ }
        if (!res.ok) throw new Error((d && d.error) || `HTTP ${res.status}`);
        return d;
      })
      .then((data) => sendResponse({ ok: true, data }))
      .catch((err) => sendResponse({ ok: false, error: svrErr(err) }));
    return true; // async
  }

  // [캡쳐+OCR] 캡쳐 이미지 → 서버 Vision 판독(/api/result/ocr) → 1·2·3착.
  if (msg?.type === 'POST_RESULT_OCR') {
    fetch(RESULT_OCR_URL, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image: msg.dataUrl }),
    })
      .then(async (res) => {
        let d = null; try { d = await res.json(); } catch (_) { /* noop */ }
        if (!res.ok) throw new Error((d && d.error) || `HTTP ${res.status}`);
        return d;
      })
      .then((data) => sendResponse({ ok: true, data }))
      .catch((err) => sendResponse({ ok: false, error: svrErr(err) }));
    return true; // async
  }

  // [캡쳐+OCR] 판독한 착순을 결과로 저장(기존 record-result 재사용, 적중판정·학습 동일).
  if (msg?.type === 'POST_RECORD_RESULT') {
    fetch(RECORD_RESULT_URL, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(msg.payload),
    })
      .then(async (res) => {
        let d = null; try { d = await res.json(); } catch (_) { /* noop */ }
        if (!res.ok) throw new Error((d && d.error) || `HTTP ${res.status}`);
        return d;
      })
      .then((data) => sendResponse({ ok: true, data }))
      .catch((err) => sendResponse({ ok: false, error: svrErr(err) }));
    return true; // async
  }

  // [복기 저장] 중요 신호(analyzeStatus.data) + 결과(1~3착)를 묶어 서버에 저장 → 패턴학습·복기.
  if (msg?.type === 'SAVE_REVIEW') {
    fetch(REVIEW_SAVE_URL, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(msg.payload),
    })
      .then(async (res) => {
        let d = null; try { d = await res.json(); } catch (_) { /* noop */ }
        if (!res.ok) throw new Error((d && d.error) || `HTTP ${res.status}`);
        return d;
      })
      .then((data) => sendResponse({ ok: true, data }))
      .catch((err) => sendResponse({ ok: false, error: svrErr(err) }));
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
      // [근본해결1] timer.js 능동 수집과 background autoTick 이 겹치지 않게 가드(중복 수집 방지)
      if (_collecting) { sendResponse({ ok: true, skipped: 'collecting' }); return; }
      _collecting = true;
      try { await _collectOnce(); await _forceAnalyze(); sendResponse({ ok: true }); }
      catch (e) { sendResponse({ ok: false, error: String(e.message || e) }); }
      finally { _collecting = false; }
    })();
    return true; // async
  }

  // [다음경주 자동전환·발주시각 독립] content.js 가 카운트다운 소멸(=경주 마감)을 감지하면 통지.
  //   발주시각(timerDeadline)이 안 잡힌 사설 모의배당판에서도 전환 체인을 가동하는 핵심 경로.
  if (msg?.type === 'RACE_FINISHED') {
    (async () => {
      try { await _armNextRaceChain(msg.raceKey || '', 'finished'); } catch (_) { /* */ }
      sendResponse({ ok: true });
    })();
    return true; // async
  }

  // [배당판 추종·board hint] content.js 가 배당판 경주(raceKey)를 전달 → 서버 current_race 힌트 저장 →
  //   분석기가 그 경주를 자동 추종(oddspark 최신 나고야로 안 튐). content.js 는 CORS 로 서버 직접 fetch 불가 → background 릴레이.
  if (msg?.type === 'BOARD_HINT') {
    (async () => {
      try {
        const res = await fetch(`${SERVER}/api/current_race`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ raceKey: msg.raceKey || '', sport: msg.sport || '' }),
        });
        sendResponse({ ok: res.ok });
      } catch (e) { sendResponse({ ok: false, error: String(e.message || e) }); }
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

// ── [1번] 결과 자동수집 타이머 (발주 후 5/7/10분 = 자동 결과수집 + 재시도 2회, 탭클릭 방식 보조 경로) ──
const RESULT_CHECK_OFFSETS = [5, 7, 10]; // 발주시각 기준 분(스펙: T+5 시도·T+7·T+10 재시도)

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
  // [확장 경유 경륜 스케줄] 매일 08:00 → oddspark 경륜 스케줄 fetch(로그인 세션)·서버 전송
  if (alarm.name === 'keirinSchedule') { fetchKeirinSchedule(); return; }
  // [다음경주 자동전환] T+3분 새로고침 · T+3분30초 재수집 · T+4분 분석+알림
  if (alarm.name === 'nextRefresh') { _nextRaceRefresh(); return; }
  if (alarm.name === 'nextCollect') { _nextRaceCollect(); return; }
  if (alarm.name === 'nextAnalyze') { _nextRaceAnalyze(); return; }
  if (alarm.name !== 'resultCheck') return;
  const { resultTimer } = await chrome.storage.local.get({ resultTimer: null });
  if (!resultTimer) return;
  const { raceKey, times, idx } = resultTimer;

  // asyukk 탭을 찾아 결과 수집 지시 (content script 가 경주결과 탭 클릭 + 추출)
  let done = false, data = null;
  try {
    const tabs = await chrome.tabs.query({ url: ['*://*.dke-d11diw.site/*'] });
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
let _lastCollectAt = 0;   // [수집 조기 중단 방어] 마지막 수집 성공 시각(발주 전 2분+ 미수집 self-heal용)

function _autoCfg() {
  return new Promise((r) => chrome.storage.local.get(
    { autoSend: false, intervalSec: 30, autoMode: 'triple', timerDeadline: 0, market: 'auto', japanType: 'local', autoNextRace: true }, r));
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
  if (!cfg.autoSend) {
    stopAutoEngine();
    // [다음경주 자동전환] 자동수집을 끄면 다음경주 전환 알람도 함께 취소(임의 새로고침 방지).
    //   자연 종료(T-0)에선 autoSend 가 유지되므로 이 분기를 안 타고 전환 알람이 살아남는다.
    ['nextRefresh', 'nextCollect', 'nextAnalyze'].forEach((n) => chrome.alarms.clear(n));
    return;
  }
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
    // [일본 결과 자동수집] 발주 후 5/7/10분 예약(스펙: T+5 시도·실패 시 T+7·T+10 재시도, 최대 3회)
    //   — 이미 수집 성공했으면 재예약 안 함
    ['resFetch0', 'resFetch1', 'resFetch2'].forEach((n) => chrome.alarms.clear(n));
    const { resultCollected } = await chrome.storage.local.get({ resultCollected: null });
    if (!(resultCollected && resultCollected.deadline === cfg.timerDeadline)) {
      [5, 7, 10].forEach((min, i) => { const at = cfg.timerDeadline + min * 60000; if (at > now) chrome.alarms.create('resFetch' + i, { when: at }); });
      const firstAt = cfg.timerDeadline + 5 * 60000;
      if (firstAt > now) chrome.storage.local.set({ resultAutoStatus: { state: 'scheduled', raceKey: '', nextAt: firstAt, t: Date.now() } });
    }
  }
  // [다음경주 자동전환] 발주 후 T+3분 배당판 새로고침 → T+3분30초 재수집 → T+4분 분석+알림.
  //   경주 종료(T-0)로 엔진이 멈춰도 이 알람들은 살아남아(stopAutoEngine 이 안 지움) 다음 경주로
  //   자동 전환한다. autoSend OFF 시엔 위 조기분기에서 함께 취소된다.
  //   nextRaceDone(발주시각) 표식이 있으면 재스케줄하지 않아, 이미 예약된 알람을 지워버리지 않는다.
  const { nextRaceDone } = await chrome.storage.local.get({ nextRaceDone: null });
  if (cfg.timerDeadline && cfg.autoNextRace !== false
      && !(nextRaceDone && nextRaceDone.deadline === cfg.timerDeadline)) {
    ['nextRefresh', 'nextCollect', 'nextAnalyze'].forEach((n) => chrome.alarms.clear(n));
    const nrT3 = cfg.timerDeadline + 180000;    // T+3분: 배당판 새로고침
    const nrT3h = cfg.timerDeadline + 210000;   // T+3분30초: 재수집 + 새 raceKey 감지
    const nrT4 = cfg.timerDeadline + 240000;    // T+4분: 분석 + 전환 알림
    if (nrT3 > now) chrome.alarms.create('nextRefresh', { when: nrT3 });
    if (nrT3h > now) chrome.alarms.create('nextCollect', { when: nrT3h });
    if (nrT4 > now) chrome.alarms.create('nextAnalyze', { when: nrT4 });
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
  const tabs = await chrome.tabs.query({ url: ['*://*.keiba.go.jp/*', '*://*.dke-d11diw.site/*'] });
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
//   결과 자동수집(발주 후 5/7/10분 resFetch 알람)은 어느 경우든 유지된다.
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
    // [발주시각 독립 전환] 무변동=경주 마감 신호 → 발주시각 없어도 다음경주 전환 체인 가동(경주당 1회).
    //   진짜 진행중 경주면 새로고침해도 같은 경주라 무해(수집 계속). 발주시각 있으면 T+3 체인이 이미 걸려 스킵.
    try { const { raceKey } = await chrome.storage.local.get({ raceKey: '' }); await _armNextRaceChain(raceKey, 'no-change'); } catch (_) { /* */ }
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

  // [4번·조기수집] T-10분 조기 배당 감시 시작 안내 — 흐름 조기 포착(발주 10분전부터 30초 간격)
  if (left != null && left <= 600000 && left > 540000 && !(await _stageFiredOnce(cfg.timerDeadline, 't10'))) {
    _notify('t10', '🔔 마감 10분 · 조기 배당 감시 시작', '흐름 포착을 위해 30초 간격 수집을 시작합니다.', false);
  }
  // [4번] T-3분 이상감지 자동 시작 — 수집 간격 단축 + 1회 안내
  if (left != null && left <= 180000 && left > 120000 && !(await _stageFiredOnce(cfg.timerDeadline, 't3'))) {
    _notify('t3', '🔔 마감 3분 · 이상감지 집중 감시 시작', '수집 간격 단축: T-3분 10초 · T-1분 5초', false);
  }
  // 수집(due 시각에만 실제 수집 — fine 5초 점검이지만 간격은 지킴)
  const baseMs = Math.max(5, Number(cfg.intervalSec) || 30) * 1000;
  // [수집속도 개선] 마감 임박 수집 간격 단계 단축 — 마감 전 급락 신호를 놓치지 않게(사용자 [2번] 스케줄)
  //   T-30초(≤30s) 3초 · T-1·2분(≤120s) 5초 · T-3분(≤180s) 10초 · T-5분(≤300s) 15초 · 평상시 기본(30초).
  //   [5번 흐름 포착] 마감 10분전부터 1분 → (7.5분)30초 → (5분)15초 → (3분)10초로 단계 단축 → 배당 흐름 변화 최대 포착.
  //   ⚠ MV3 서비스워커 절전 시 fine setInterval(5초)이 억제될 수 있어 실측 간격이 늘 수 있음(keepalive 보강).
  let intervalMs = baseMs;
  if (left != null) {
    if (left <= 30000) intervalMs = 3000;         // 마감 30초전부터 3초 간격
    else if (left <= 120000) intervalMs = 5000;   // 마감 2분전부터 5초 간격(T-1분 포함)
    else if (left <= 180000) intervalMs = 10000;  // 마감 3분전부터 10초 간격
    else if (left <= 300000) intervalMs = 15000;  // 마감 5분전부터 15초 간격
    else if (left <= 450000) intervalMs = Math.min(baseMs, 30000);  // [5번] 마감 7.5분전부터 30초(설정이 더 빠르면 유지)
    else if (left <= 600000) intervalMs = Math.min(baseMs, 30000);  // [4번·조기수집] 마감 10분전부터 30초(기존 1분→30초·흐름 조기 포착)
  }
  // [수집 조기 중단 방어] 발주 전(left>0)인데 마지막 수집 성공 후 2분+ 경과 → 중단으로 보고 즉시 재수집(due 무시).
  //   고쿠라 8R: T-8분에 수집이 멈춰 JRA 마감구간을 놓친 케이스 방어. 백그라운드 전용 모드에서도 self-heal.
  const _stalled = (left != null && left > 0 && _lastCollectAt > 0 && (now - _lastCollectAt) >= 120000);
  if (_stalled) _nextDueAt = 0;   // 즉시 재수집 유도
  if ((reason === 'start' || now >= _nextDueAt) && !_collecting) {
    _collecting = true;
    let r = null;
    try { r = await _collectOnce(); } catch (_) { /* */ }
    _collecting = false;
    // [수정2] 경기 마감 감지 → 엔진이 이미 정지됨. 상태를 running:true 로 덮어쓰지 않고 종료.
    if (r && r.closed) return;
    if (r && !r.closed) _lastCollectAt = Date.now();   // 수집 성공 시각 기록
    _nextDueAt = Date.now() + intervalMs;
    _setAutoStatus({ running: true, last: Date.now(), next: _nextDueAt, deadline: cfg.timerDeadline || 0, intervalMs, stalled: _stalled });
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

// [v2.0.1] 발주 후 결과 자동수집(fetch 방식). attempt 0/1/2 = 발주+5/7/10분.
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
    const fo = res.finalOdds || {};   // [스펙5] 확정 복승/삼복승 배당(content.js가 함께 반환)
    chrome.storage.local.set({ resultAutoStatus: { state: 'done', raceKey: res.raceKey, top3, hit, finalOdds: fo, t: Date.now() } });
    // 스펙5 알림 형식:
    //  ✅ 모리오카 3경주 결과 수집
    //   1착 7번 / 2착 4번 / 3착 9번
    //   복승 7+4: 12.3배
    //   추천 7+4 ✅ 적중!
    const t3 = top3.slice(0, 3).map((n, i) => `${i + 1}착 ${n}번`).join(' / ');
    const q = top3.slice(0, 2);
    const t3combo = top3.slice(0, 3);
    const qCombo = (fo.quinella && fo.quinella.combo && fo.quinella.combo.length) ? fo.quinella.combo : q;
    const qOddsLine = (fo.quinella && fo.quinella.odds) ? `\n복승 ${qCombo.join('+')}: ${fo.quinella.odds}배` : '';
    // [스펙1] 삼복승 확정배당도 조건부 표시("삼복승 7+4+9: 88.5배 ✅ 적중!")
    const tCombo = (fo.trio && fo.trio.combo && fo.trio.combo.length) ? fo.trio.combo : t3combo;
    const tOddsLine = (fo.trio && fo.trio.odds)
      ? `\n삼복승 ${tCombo.join('+')}: ${fo.trio.odds}배${hit.trifecta ? ' ✅ 적중!' : ''}` : '';
    const recLine = hit.quinella ? `\n추천 ${q.join('+')} ✅ 적중!`
      : hit.trifecta ? `\n추천 ${t3combo.join('+')} ✅ 적중!`
      : win ? '\n✅ 적중!' : '\n❌ 미적중';
    _notify('result', `✅ ${res.raceKey || '경주'} 결과 수집`, `${t3}${qOddsLine}${tOddsLine}${recLine}`, false);
    setBadge(true, '✓');
    // [스펙3] 성공 이벤트를 서버로도 전송 → 분석기 결과기록 탭 자동 갱신(새로고침 불필요)
    _postResultAutoStatus({ state: 'done', raceKey: res.raceKey || '', top3, hit, finalOdds: fo });
  } else {
    const last = attempt >= 2;
    const nextMin = attempt === 0 ? 7 : 10;   // 다음 재시도 시각(발주+7 / 발주+10분)
    const nextAt = (!last && cfg.timerDeadline) ? cfg.timerDeadline + nextMin * 60000 : null;
    // 실패 시엔 res 에 raceKey 가 없으므로 현재 설정된 raceKey 를 가져와 표시/전송에 사용
    const { raceKey: curRaceKey } = await chrome.storage.local.get({ raceKey: '' });
    chrome.storage.local.set({ resultAutoStatus: { state: last ? 'manual' : 'retry', attempt: attempt + 2, nextAt, raceKey: curRaceKey || '', t: Date.now() } });
    // [스펙4·5] T+10분까지 실패 → 수동 입력 안내(해당 경주는 결과 미입력으로 남아 결과기록 탭 '미입력' 목록에 표시됨)
    if (last) {
      // [자동 팝업 제거] 결과 자동수집 실패 시 '캡쳐로 입력하세요' Chrome 알림은 띄우지 않는다(사용자 요청).
      //   → 해당 경주는 결과 미입력으로 남아 분석기 '결과기록 탭 > 📋 결과 입력 대기' 목록에만 조용히 표시된다.
      //   (캡쳐→판독·수동입력 기능 자체는 확장 팝업/결과기록 탭에 그대로 보존 — 안내 팝업만 제거)
      // [상태 전송 유지] 실패 상태는 서버로만 전송(팝업/배너 없이) → 결과기록 탭 대기 목록 정확성 유지.
      _postResultAutoStatus({ state: 'manual', raceKey: curRaceKey || '', attempt: attempt + 2 });
    }
  }
}

/* ═══════════ [다음경주 자동전환] 발주 후 자동 새로고침 → 재수집 → 분석 ═══════════
 * 경주가 끝나면(T-0) 수집 엔진은 멈춘다. 사람이 배당판을 새로고침하지 않으면 다음 경주
 * 배당이 안 들어온다. → 아래 3단계 알람으로 자동 전환한다.
 *   T+3분    : 배당판 탭 새로고침(chrome.tabs.reload). 그 전에 끝난 경주 결과 1회 확보 시도.
 *   T+3분30초: 새로고침된 페이지에서 강제 1회 수집 + 새 raceKey/발주시각 자동 감지.
 *   T+4분    : 분석 실행 + "🔄 다음 경주로 전환" 알림. autoSend 유지 시 새 발주시각 감지로
 *              syncAutoEngine 이 재가동되어 연속 수집이 이어진다.
 * autoSend OFF 시엔 syncAutoEngine 이 이 알람들을 함께 취소한다(임의 새로고침 방지).
 * ===================================================================== */
/* [발주시각 독립 자동전환] 발주시각(timerDeadline)이 없어도 '경주 마감'이 확인되면
 *   지금 시각 기준 +12초 새로고침 → +42초 재수집 → +72초 분석 체인을 가동한다.
 *   같은 경주(raceKey)에 대해선 1회만(nextChainRk 가드) → 루프/불필요 새로고침 방지.
 *   호출처: ① content.js RACE_FINISHED(카운트다운 소멸) ② _onRaceClosed 소프트 마감(무변동). */
async function _armNextRaceChain(raceKey, why) {
  const cfg = await _autoCfg();
  if (!cfg.autoSend || cfg.autoNextRace === false) return;
  const rk = (raceKey || '').trim() || (await chrome.storage.local.get({ raceKey: '' })).raceKey || '';
  const { nextChainRk } = await chrome.storage.local.get({ nextChainRk: '' });
  if (rk && nextChainRk === rk) return;                 // 이 경주는 이미 전환 체인 가동함
  // 발주시각 기반 T+3 체인이 이미 예약돼 있으면(정상 경로) 중복 가동 안 함.
  const armed = await chrome.alarms.get('nextRefresh');
  if (armed) return;
  await chrome.storage.local.set({ nextChainRk: rk });
  const now = Date.now();
  ['nextRefresh', 'nextCollect', 'nextAnalyze'].forEach((n) => chrome.alarms.clear(n));
  chrome.alarms.create('nextRefresh', { when: now + 12000 });   // +12초: 배당판 새로고침
  chrome.alarms.create('nextCollect', { when: now + 42000 });   // +42초: 재수집 + 새 raceKey 감지
  chrome.alarms.create('nextAnalyze', { when: now + 72000 });   // +72초: 분석 + 전환 알림
  _setAutoStatus({ running: false, nextRace: 'waiting', why: why || '', raceKey: rk, t: now });
  console.log(`[다음경주] 발주시각 독립 전환 체인 가동(${why}) rk="${rk}"`);
}

async function _nextRaceRefresh() {
  const cfg = await _autoCfg();
  if (!cfg.autoSend || cfg.autoNextRace === false) return;
  // 같은 발주시각엔 1회만 실행(syncAutoEngine 재스케줄 중단 표식).
  await chrome.storage.local.set({ nextRaceDone: { deadline: cfg.timerDeadline, t: Date.now() } });
  // [결과보존] 다음 경주로 넘어가기 전에 끝난 경주 결과를 먼저 1회 수집 시도.
  //   실패해도 기존 T+5/7/10분(resFetch) 재시도가 백업으로 남는다.
  try {
    const { resultCollected } = await chrome.storage.local.get({ resultCollected: null });
    if (cfg.timerDeadline && !(resultCollected && resultCollected.deadline === cfg.timerDeadline)) {
      await doResultFetch(0);
    }
  } catch (_) { /* 결과 미준비면 기존 재시도에 맡김 */ }
  const tab = await _findOddsTab();
  if (!tab) { _setAutoStatus({ running: false, nextRace: 'no-tab', warn: '다음경주 새로고침: 배당판 탭 없음' }); return; }
  try { await chrome.tabs.reload(tab.id); } catch (_) { /* */ }
  _setAutoStatus({ running: false, nextRace: 'refreshing', deadline: cfg.timerDeadline, t: Date.now() });
  _notify('nextRefresh', '🔄 다음 경주 준비 중', '배당판을 새로고침했습니다. 곧 자동 수집을 시작합니다.', false);
}

async function _nextRaceCollect() {
  const cfg = await _autoCfg();
  if (!cfg.autoSend || cfg.autoNextRace === false) return;
  // 새로고침된 페이지에서 강제 1회 수집(콘텐츠 스크립트가 새 raceKey/발주시각도 함께 갱신).
  try { await _collectOnce(); } catch (_) { /* */ }
  try { await _forceAnalyze(); } catch (_) { /* */ }
  _setAutoStatus({ running: true, nextRace: 'collecting', t: Date.now() });
}

async function _nextRaceAnalyze() {
  const cfg = await _autoCfg();
  if (!cfg.autoSend || cfg.autoNextRace === false) return;
  await _forceAnalyze();
  const { raceKey } = await chrome.storage.local.get({ raceKey: '' });
  // [4번] 분석기 배너용: 전환 완료 상태 + 새 경주키를 서버 상태로 흘려보냄(deadline 있으면 예상 시작).
  _setAutoStatus({ running: true, nextRace: 'done', newRaceKey: raceKey || '',
    deadline: cfg.timerDeadline || 0, t: Date.now() });
  _notify('nextRace', '🔄 다음 경주로 전환',
    `새 경주: ${raceKey || '감지 중…'}\n자동 수집 시작`, false);
  // 새 발주시각이 감지됐으면 storage 변경으로 이미 재가동됐지만, 미검출 대비 한 번 깨워준다.
  syncAutoEngine();
}

chrome.notifications.onButtonClicked.addListener(() => { chrome.tabs.create({ url: `${SERVER}/` }); });
chrome.notifications.onClicked.addListener(() => { chrome.tabs.create({ url: `${SERVER}/` }); });

// 설정(autoSend/간격/발주시각) 변경 → 엔진 즉시 동기화
chrome.storage.onChanged.addListener((ch, area) => {
  if (area !== 'local') return;
  if (ch.autoSend || ch.intervalSec || ch.autoMode || ch.timerDeadline || ch.market || ch.japanType) syncAutoEngine();
});
// 브라우저 시작/서비스워커 부활 시 엔진 복원
chrome.runtime.onStartup.addListener(() => { syncAutoEngine(); scheduleKeirinDaily(); });
// 로드 즉시 1회 동기화(이미 autoSend 켜져 있으면 바로 재개)
syncAutoEngine();
