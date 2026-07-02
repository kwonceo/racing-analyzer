/* =========================================================================
 * popup.js — 확장 아이콘 팝업 UI 로직
 *   - 서버 연결 상태 (● 초록/빨강)
 *   - raceKey 표시/직접입력
 *   - 자동전송 on/off + 간격 선택
 *   - [지금 전송] 버튼
 *   - 마지막 전송 시간/결과
 * =======================================================================*/

const $ = (id) => document.getElementById(id);

const els = {
  srvDot: $('srvDot'),
  srvText: $('srvText'),
  raceKey: $('raceKey'),
  autoSend: $('autoSend'),
  interval: $('interval'),
  sendNow: $('sendNow'),
  lastResult: $('lastResult'),
  lastDetail: $('lastDetail'),
};

function fmtTime(ts) {
  if (!ts) return '—';
  const d = new Date(ts);
  return d.toLocaleTimeString('ko-KR', { hour12: false });
}

// ── 저장된 설정/상태 로드 → UI 반영 ─────────────────────────────────
function loadState() {
  chrome.storage.local.get(
    { autoSend: false, intervalSec: 60, raceKey: '', status: null, resultStatus: null, tripleStatus: null },
    (v) => {
      els.autoSend.checked = !!v.autoSend;
      els.interval.value = String(v.intervalSec || 60);
      els.raceKey.value = v.raceKey || '';
      renderStatus(v.status);
      renderResultStatus(v.resultStatus);
      renderTripleStatus(v.tripleStatus);
    }
  );
}

function renderStatus(status) {
  if (!status || !status.lastSend) {
    els.lastResult.textContent = '마지막 전송: —';
    els.lastDetail.textContent = '';
    return;
  }
  const when = fmtTime(status.lastSend);
  if (status.lastOk) {
    els.lastResult.innerHTML =
      `마지막 전송: <span class="ok">성공 ${when}</span>`;
    els.lastDetail.textContent =
      `${status.lastRaceKey || ''} · 누적 ${status.lastSnaps ?? '?'}회`;
  } else {
    els.lastResult.innerHTML =
      `마지막 전송: <span class="err">실패 ${when}</span>`;
    els.lastDetail.textContent = status.lastError || '';
  }
}

// ── 서버 연결 확인 ──────────────────────────────────────────────────
function checkServer() {
  els.srvDot.className = 'dot';
  els.srvText.textContent = '확인 중…';
  chrome.runtime.sendMessage({ type: 'CHECK_SERVER' }, (res) => {
    const online = res && res.online;
    els.srvDot.className = 'dot ' + (online ? 'on' : 'off');
    els.srvText.textContent = online ? '서버 연결됨' : '서버 없음 (8011)';
    els.srvText.className = 'muted ' + (online ? 'ok' : 'err');
  });
}

// ── 활성 탭의 content script 로 메시지 (keiba.go.jp 인지 확인) ─────────
async function activeKeibaTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !/keiba\.go\.jp/.test(tab.url || '')) return null;
  return tab;
}

// ── 이벤트 바인딩 ───────────────────────────────────────────────────
els.autoSend.addEventListener('change', () => {
  chrome.storage.local.set({ autoSend: els.autoSend.checked });
});
els.interval.addEventListener('change', () => {
  chrome.storage.local.set({ intervalSec: parseInt(els.interval.value, 10) });
});
els.raceKey.addEventListener('change', () => {
  chrome.storage.local.set({ raceKey: els.raceKey.value.trim() });
});

els.sendNow.addEventListener('click', async () => {
  els.sendNow.disabled = true;
  els.sendNow.textContent = '전송 중…';
  const tab = await activeKeibaTab();
  if (!tab) {
    els.lastResult.innerHTML =
      '<span class="err">keiba.go.jp 배당 페이지에서 눌러주세요.</span>';
    els.lastDetail.textContent = '';
    resetBtn();
    return;
  }
  chrome.tabs.sendMessage(tab.id, { type: 'MANUAL_SEND' }, (res) => {
    if (chrome.runtime.lastError || !res) {
      els.lastResult.innerHTML =
        '<span class="err">페이지 응답 없음. 새로고침 후 재시도.</span>';
      els.lastDetail.textContent = chrome.runtime.lastError?.message || '';
    } else {
      renderStatus(res.status || null);
      if (!res.ok && res.error) {
        els.lastResult.innerHTML = `<span class="err">${res.error}</span>`;
      }
    }
    resetBtn();
  });
});

function resetBtn() {
  els.sendNow.disabled = false;
  els.sendNow.textContent = '지금 전송';
}

// ── [2번] 결과(1~3착) 전송 ──────────────────────────────────────────
const btnResults = document.getElementById('sendResults');
const resultRow = document.getElementById('lastResultRow');
const resultDetail = document.getElementById('lastResultDetail');

function renderResultStatus(rs) {
  if (!rs || !rs.lastResult) return;
  const when = fmtTime(rs.lastResult);
  if (rs.lastResultOk) {
    resultRow.innerHTML = `결과 전송: <span class="ok">성공 ${when}</span>`;
    resultDetail.textContent = `${rs.lastResultRaceKey || ''} · 1~3착 ${(rs.lastTop3 || []).join('-')}`;
  } else {
    resultRow.innerHTML = `결과 전송: <span class="err">실패 ${when}</span>`;
    resultDetail.textContent = rs.lastResultError || '';
  }
}

btnResults.addEventListener('click', async () => {
  btnResults.disabled = true; btnResults.textContent = '전송 중…';
  const tab = await activeKeibaTab();
  if (!tab) {
    resultRow.innerHTML = '<span class="err">keiba.go.jp 결과(성적) 페이지에서 눌러주세요.</span>';
  } else {
    chrome.tabs.sendMessage(tab.id, { type: 'MANUAL_SEND_RESULTS' }, (res) => {
      if (chrome.runtime.lastError || !res) {
        resultRow.innerHTML = '<span class="err">페이지 응답 없음. 새로고침 후 재시도.</span>';
      } else {
        renderResultStatus(res.status || null);
        if (!res.ok && res.error) resultRow.innerHTML = `<span class="err">${res.error}</span>`;
      }
    });
  }
  btnResults.disabled = false; btnResults.textContent = '🏁 결과(1~3착) 전송';
});

// ── [전체 자동 수집] 복승·쌍승·삼복승 3종 ────────────────────────────
const btnTriple = document.getElementById('collectTriple');
const tripleRow = document.getElementById('tripleRow');
const tripleDetail = document.getElementById('tripleDetail');

function renderTripleStatus(ts) {
  if (!ts || !ts.lastTriple) return;
  const when = fmtTime(ts.lastTriple);
  if (ts.lastTripleOk) {
    const c = ts.lastCounts || {};
    tripleRow.innerHTML = `3종 수집: <span class="ok">성공 ${when}</span>`;
    tripleDetail.textContent = `${ts.lastTripleRaceKey || ''} · 복승 ${c.quinella || 0} · 쌍승 ${c.exacta || 0} · 삼복승 ${c.trio || 0}`;
  } else {
    tripleRow.innerHTML = `3종 수집: <span class="err">실패 ${when}</span>`;
    tripleDetail.textContent = ts.lastTripleError || '';
  }
}

btnTriple.addEventListener('click', async () => {
  btnTriple.disabled = true; btnTriple.textContent = '수집 중… (3종 페이지)';
  const tab = await activeKeibaTab();
  if (!tab) {
    tripleRow.innerHTML = '<span class="err">keiba.go.jp 경주 페이지에서 눌러주세요.</span>';
  } else {
    await new Promise((resolve) => {
      chrome.tabs.sendMessage(tab.id, { type: 'MANUAL_COLLECT_TRIPLE' }, (res) => {
        if (chrome.runtime.lastError || !res) {
          tripleRow.innerHTML = '<span class="err">페이지 응답 없음. 새로고침 후 재시도.</span>';
        } else {
          renderTripleStatus(res.status || null);
          if (!res.ok && res.error) tripleRow.innerHTML = `<span class="err">${res.error}</span>`;
        }
        resolve();
      });
    });
  }
  btnTriple.disabled = false; btnTriple.textContent = '⚡ 전체 자동 수집 (복승·쌍승·삼복승)';
});

// 상태가 background 에서 갱신되면 실시간 반영
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  if (changes.status) renderStatus(changes.status.newValue);
  if (changes.resultStatus) renderResultStatus(changes.resultStatus.newValue);
  if (changes.tripleStatus) renderTripleStatus(changes.tripleStatus.newValue);
});

// ── 초기화 ──────────────────────────────────────────────────────────
loadState();
checkServer();
