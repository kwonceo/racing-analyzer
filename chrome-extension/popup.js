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
  autoMode: $('autoMode'),
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
    { autoSend: false, intervalSec: 60, raceKey: '', autoMode: 'triple', status: null, resultStatus: null, tripleStatus: null, tripleProgress: null },
    (v) => {
      els.autoSend.checked = !!v.autoSend;
      els.interval.value = String(v.intervalSec || 60);
      els.autoMode.value = v.autoMode || 'triple';
      els.raceKey.value = v.raceKey || '';
      renderStatus(v.status);
      renderResultStatus(v.resultStatus);
      renderTripleStatus(v.tripleStatus);
      if (v.tripleProgress && !v.tripleProgress.done) renderTripleProgress(v.tripleProgress);
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

// ── 활성 탭의 content script 로 메시지 (지원 배당판 사이트인지 확인) ────
//    keiba.go.jp + 사설 배당판(asyukk/qwqwd) 모두 허용
const SUPPORTED_SITE = /keiba\.go\.jp|asyukk|qwqwd/i;
async function activeKeibaTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !SUPPORTED_SITE.test(tab.url || '')) return null;
  return tab;
}

// ── 이벤트 바인딩 ───────────────────────────────────────────────────
els.autoSend.addEventListener('change', () => {
  chrome.storage.local.set({ autoSend: els.autoSend.checked });
});
els.interval.addEventListener('change', () => {
  chrome.storage.local.set({ intervalSec: parseInt(els.interval.value, 10) });
});
els.autoMode.addEventListener('change', () => {
  chrome.storage.local.set({ autoMode: els.autoMode.value });
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

// ── [4번] 미리보기: 전송 전 추출 결과·검증·상위 10조합 확인 ────────────
const previewBtn = document.getElementById('previewBtn');
const previewCard = document.getElementById('previewCard');
const previewHead = document.getElementById('previewHead');
const previewTop = document.getElementById('previewTop');
const previewWarn = document.getElementById('previewWarn');
const previewSend = document.getElementById('previewSend');

previewBtn.addEventListener('click', async () => {
  previewBtn.disabled = true; previewBtn.textContent = '추출 중…';
  const tab = await activeKeibaTab();
  if (!tab) {
    previewCard.style.display = 'block';
    previewHead.innerHTML = '<span class="err">지원 배당판 페이지(keiba.go.jp / 사설 배당판)에서 눌러주세요.</span>';
    previewTop.textContent = ''; previewWarn.textContent = ''; previewSend.style.display = 'none';
  } else {
    chrome.tabs.sendMessage(tab.id, { type: 'PREVIEW' }, (r) => {
      previewCard.style.display = 'block';
      if (chrome.runtime.lastError || !r) {
        previewHead.innerHTML = '<span class="err">페이지 응답 없음. 새로고침 후 재시도.</span>';
        previewTop.textContent = ''; previewWarn.textContent = ''; previewSend.style.display = 'none';
      } else {
        const rk = r.raceKey || '(raceKey 미입력 — 위 칸에 입력하세요)';
        previewHead.innerHTML = `<b>[${r.site}]</b> ${rk}<br>단승 ${r.singles}두 · 복승 ${r.combos}조합`;
        previewTop.textContent = (r.top || []).length
          ? '상위 10 (배당 낮은순):\n' + r.top.map((t) => `  ${t.combo}  →  ${t.odds}`).join('\n')
          : '추출된 조합이 없습니다.';
        previewWarn.className = 'err';
        previewWarn.textContent = (r.validation && !r.validation.ok)
          ? '⚠ ' + r.validation.warnings.join(' / ') : '';
        // 검증 실패해도 전송은 허용하되 경고 표시
        previewSend.style.display = (r.combos || r.singles) ? 'block' : 'none';
      }
    });
  }
  previewBtn.disabled = false; previewBtn.textContent = '🔍 미리보기 (전송 전 확인)';
});

previewSend.addEventListener('click', async () => {
  previewSend.disabled = true; previewSend.textContent = '전송 중…';
  const tab = await activeKeibaTab();
  if (!tab) {
    previewHead.innerHTML = '<span class="err">지원 배당판 페이지에서 눌러주세요.</span>';
  } else {
    await new Promise((resolve) => {
      chrome.tabs.sendMessage(tab.id, { type: 'MANUAL_SEND' }, (res) => {
        if (chrome.runtime.lastError || !res) {
          previewWarn.textContent = '페이지 응답 없음. 새로고침 후 재시도.';
        } else if (res.ok) {
          previewWarn.className = 'ok';
          previewWarn.textContent = `✅ 전송 완료 · ${res.detail || res.raceKey || ''}`;
        } else {
          previewWarn.className = 'err';
          previewWarn.textContent = `❌ ${res.error || '전송 실패'}${res.detail ? ' · ' + res.detail : ''}`;
        }
        resolve();
      });
    });
  }
  previewSend.disabled = false; previewSend.textContent = '✅ 확인 후 전송';
});

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

// content.js 가 storage.tripleProgress 로 흘려보내는 실시간 진행상황 표시
function renderTripleProgress(p) {
  if (!p || !p.msg) return;
  tripleRow.innerHTML = p.done
    ? (/✅|완료/.test(p.msg) ? `<span class="ok">${p.msg}</span>` : `<span class="err">${p.msg}</span>`)
    : `<span class="muted">${p.msg}</span>`;
}

btnTriple.addEventListener('click', async () => {
  btnTriple.disabled = true; btnTriple.textContent = '수집 중… (복승→쌍승→삼복승)';
  tripleDetail.textContent = '';
  const tab = await activeKeibaTab();
  if (!tab) {
    tripleRow.innerHTML = '<span class="err">keiba.go.jp 경주(오즈) 페이지에서 눌러주세요.</span>';
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

// ── [1번] 즉시 분석: 수집 → 서버 이상감지 → 요약 표시 ────────────────
const btnInstant = document.getElementById('instantAnalyze');
const analyzeCard = document.getElementById('analyzeCard');
const analyzeSummary = document.getElementById('analyzeSummary');
const analyzeDetail = document.getElementById('analyzeDetail');

function renderAnalyze(a) {
  analyzeCard.style.display = 'block';
  analyzeSummary.className = 'status-line ok';
  analyzeSummary.textContent = a.summary || '분석 결과';
  const lines = [];
  if ((a.drops || []).length) {
    const d = a.drops.slice(0, 3).map((x) => `${x.combo[0]}-${x.combo[1]} ${x.prev}→${x.cur} (${x.pct > 0 ? '▲' : '▼'}${Math.abs(x.pct)}%)`);
    lines.push('📉 급락: ' + d.join(', '));
  }
  const flips = (a.reversals || []).filter((r) => r.flipped);
  if (flips.length) lines.push('🔴 쌍승역전: ' + flips.slice(0, 2).map((r) => `${r.favored[0]}→${r.favored[1]}`).join(', '));
  if ((a.keyHorses || []).length) lines.push('⭐ 유력마: ' + a.keyHorses.join('·') + (a.anomalyHorse != null ? ` (이상감지말 ${a.anomalyHorse})` : ''));
  (a.betRecommend || a.trioRecommend || []).forEach((r) => {
    const od = r.expOdds != null ? `약 ${r.expOdds}배`
      : (r.expOddsEst != null ? `추정 ${r.expOddsEst}배` : '배당 미수집');
    lines.push(`🎯 ${r.label}: ${r.combo.join('+')} (${od})`);
  });
  if (!a.hasPrev) lines.push('※ 직전 데이터 없음 — 변동은 다음 수집부터');
  analyzeDetail.textContent = lines.join('\n');
}

btnInstant.addEventListener('click', async () => {
  btnInstant.disabled = true; btnInstant.textContent = '수집 중…';
  analyzeCard.style.display = 'block';
  analyzeSummary.className = 'status-line'; analyzeSummary.textContent = '① 배당 수집 중…';
  analyzeDetail.textContent = '';
  const tab = await activeKeibaTab();
  if (!tab) {
    analyzeSummary.className = 'status-line err';
    analyzeSummary.textContent = '지원 배당판 페이지(keiba / 사설 배당판)에서 눌러주세요.';
    btnInstant.disabled = false; btnInstant.textContent = '🚨 즉시 분석 (수집→이상감지)';
    return;
  }
  // ① 수집(복승+쌍승+삼복승 → 서버 ingest)
  await new Promise((resolve) => {
    chrome.tabs.sendMessage(tab.id, { type: 'MANUAL_COLLECT_TRIPLE' }, () => resolve());
  });
  // ② 서버 이상감지 실행
  btnInstant.textContent = '분석 중…';
  analyzeSummary.textContent = '② 이상감지 분석 중…';
  chrome.runtime.sendMessage({ type: 'ANALYZE_TRIPLE', raceKey: '' }, (res) => {
    if (chrome.runtime.lastError || !res) {
      analyzeSummary.className = 'status-line err';
      analyzeSummary.textContent = '서버 응답 없음 (8011 실행 확인).';
    } else if (!res.ok) {
      analyzeSummary.className = 'status-line err';
      analyzeSummary.textContent = res.error || '분석 실패';
    } else {
      renderAnalyze(res.data);
    }
    btnInstant.disabled = false; btnInstant.textContent = '🚨 즉시 분석 (수집→이상감지)';
  });
});

// 상태가 background 에서 갱신되면 실시간 반영
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  if (changes.status) renderStatus(changes.status.newValue);
  if (changes.resultStatus) renderResultStatus(changes.resultStatus.newValue);
  if (changes.tripleStatus) renderTripleStatus(changes.tripleStatus.newValue);
  if (changes.tripleProgress) renderTripleProgress(changes.tripleProgress.newValue);
});

// ── 초기화 ──────────────────────────────────────────────────────────
loadState();
checkServer();
