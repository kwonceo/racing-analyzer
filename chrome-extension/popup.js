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
  overlayEnabled: $('overlayEnabled'),   // [보완#1] 배당판 오버레이 ON/OFF
  interval: $('interval'),
  autoMode: $('autoMode'),
  market: $('market'),
  japanTypeRow: $('japanTypeRow'),
  jtLocal: $('jtLocal'),
  jtCentral: $('jtCentral'),
  sendNow: $('sendNow'),
  lastResult: $('lastResult'),
  lastDetail: $('lastDetail'),
  postTime: $('postTime'),
};

// [3번] 발주시각 표시: "발주 16:00 (4분 30초 후)" — 1초마다 남은시간 갱신.
function fmtLeft(ms) {
  if (ms <= 0) return '마감';
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h > 0) return `${h}시간 ${m}분 후`;
  if (m > 0) return `${m}분 ${sec}초 후`;
  return `${sec}초 후`;
}
function renderPostTime(timerTime, deadline, source) {
  const el = els.postTime; if (!el) return;
  if (!deadline) { el.textContent = '자동 감지 대기…'; el.style.color = '#94a3b8'; return; }
  const left = deadline - Date.now();
  const tag = source === 'manual' ? ' · 수동' : ' · 자동';
  el.textContent = `발주 ${timerTime || '--:--'} (${fmtLeft(left)})${tag}`;
  el.style.color = left <= 0 ? '#f87171' : left <= 60000 ? '#f87171' : left <= 300000 ? '#fbbf24' : '#38d39f';
}
let _postTimeTimer = null;
function startPostTimeTicker() {
  const paint = () => chrome.storage.local.get(
    { timerTime: '', timerDeadline: 0, deadlineSource: '' },
    (v) => renderPostTime(v.timerTime, v.timerDeadline, v.deadlineSource));
  paint();
  if (_postTimeTimer) clearInterval(_postTimeTimer);
  _postTimeTimer = setInterval(paint, 1000);
}

function fmtTime(ts) {
  if (!ts) return '—';
  const d = new Date(ts);
  return d.toLocaleTimeString('ko-KR', { hour12: false });
}

// ── 저장된 설정/상태 로드 → UI 반영 ─────────────────────────────────
function loadState() {
  chrome.storage.local.get(
    { autoSend: false, intervalSec: 30, raceKey: '', autoMode: 'triple', market: 'auto', japanType: 'local', status: null, resultStatus: null, tripleStatus: null, tripleProgress: null, resultAutoStatus: null, analyzeStatus: null, autoCollectStatus: null, overlayEnabled: false },
    (v) => {
      els.autoSend.checked = !!v.autoSend;
      if (els.overlayEnabled) els.overlayEnabled.checked = !!v.overlayEnabled;   // [보완#1] 오버레이 상태 복원
      els.interval.value = String(v.intervalSec || 30);
      els.autoMode.value = v.autoMode || 'triple';
      if (els.market) els.market.value = v.market || 'auto';
      renderJapanType(v.japanType || 'local');   // [1번] 중앙/지방 버튼 상태
      syncJapanTypeUI();
      els.raceKey.value = v.raceKey || '';
      renderStatus(v.status);
      renderResultStatus(v.resultStatus);
      renderTripleStatus(v.tripleStatus);
      if (v.tripleProgress && !v.tripleProgress.done) renderTripleProgress(v.tripleProgress);
      renderAutoClosed(v.autoCollectStatus);   // [수정2] 경기 마감 시 중단 안내(팝업 재오픈에도 유지)
      renderResultTimer(v.resultAutoStatus);
      // [3번] 창을 닫았다 다시 열어도 마지막 즉시분석 결과를 복원(수동 결과면 고정 유지)
      if (v.analyzeStatus && v.analyzeStatus.data) applyAnalyzeStatus(v.analyzeStatus, !!v.analyzeStatus.manual);
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
// [보완#1] 오버레이 토글 — chrome.storage 에 저장하면 overlay.js 가 onChanged 로 즉시 반영.
//   배당판을 열지 않아도 미리 ON/OFF 설정 가능(수집 로직과 무관).
if (els.overlayEnabled) els.overlayEnabled.addEventListener('change', () => {
  chrome.storage.local.set({ overlayEnabled: els.overlayEnabled.checked });
});
els.autoSend.addEventListener('change', () => {
  chrome.storage.local.set({ autoSend: els.autoSend.checked });
});
els.interval.addEventListener('change', () => {
  chrome.storage.local.set({ intervalSec: parseInt(els.interval.value, 10) });
});
els.autoMode.addEventListener('change', () => {
  chrome.storage.local.set({ autoMode: els.autoMode.value });
});
if (els.market) els.market.addEventListener('change', () => {
  chrome.storage.local.set({ market: els.market.value });
  syncJapanTypeUI();
});

// [1번] 일본 중앙(JRA)/지방(NAR) 선택 — 중앙=배당만·마감1분30초전, 지방=전적+배당
function renderJapanType(jt) {
  const on = 'background:#2563eb;border-color:#2563eb;color:#fff';
  const off = 'background:#1e293b;border-color:#334155;color:#e2e8f0';
  const base = 'cursor:pointer;border:1px solid;border-radius:6px;padding:4px 8px;font:inherit;font-size:12px;';
  if (els.jtLocal) els.jtLocal.style.cssText = base + (jt === 'central' ? off : on);
  if (els.jtCentral) els.jtCentral.style.cssText = base + (jt === 'central' ? on : off);
}
function syncJapanTypeUI() {
  // 한국경마 모드에서는 일본 종류 선택을 숨긴다.
  if (els.japanTypeRow) els.japanTypeRow.style.display = (els.market && els.market.value === 'korea') ? 'none' : '';
}
[els.jtLocal, els.jtCentral].forEach((b) => {
  if (b) b.addEventListener('click', () => {
    const jt = b.dataset.jt;
    chrome.storage.local.set({ japanType: jt });
    renderJapanType(jt);
  });
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
        previewHead.innerHTML = `<b>[${r.site}]</b> ${rk}<br>복승 ${r.combos}조합`;
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

// ── [1·5번] 결과 자동수집 타이머 예약 + 상태 표시 ────────────────────
const btnArmTimer = document.getElementById('armResultTimer');
const timerCard = document.getElementById('resultTimerCard');
const timerRow = document.getElementById('resultTimerRow');
const timerDetail = document.getElementById('resultTimerDetail');

function renderResultTimer(st) {
  if (!st || !st.state) { timerCard.style.display = 'none'; return; }
  timerCard.style.display = 'block';
  const at = st.nextAt ? new Date(st.nextAt).toLocaleTimeString('ko-KR', { hour12: false }) : '';
  if (st.state === 'scheduled') { timerRow.innerHTML = `<span class="ok">🔄 결과 수집 예약됨</span> ${esc(st.raceKey || '')}`; timerDetail.textContent = at ? `첫 체크 ${at} (발주 후 7분)` : '발주 후 7분'; }
  else if (st.state === 'retry') { timerRow.innerHTML = `<span>🔄 결과 수집 재시도 ${st.attempt || ''}/3</span>`; timerDetail.textContent = at ? `다음 체크 ${at}` : ''; }
  else if (st.state === 'done') {
    const h = st.hit || {};
    const win = h.quinella || h.trifecta || h.was_hit;
    timerRow.innerHTML = win ? '<span class="ok">✅ 결과 수집 완료 · 적중!</span>' : '<span class="err">✅ 결과 수집 완료 · 미적중</span>';
    const parts = [];
    if (st.top3) parts.push(`실제 ${(st.top3 || []).join('-')}`);
    if (h.quinella) parts.push(`복승 적중${h.payouts && h.payouts.quinella ? ' ' + h.payouts.quinella + '배' : ''}`);
    if (h.trifecta) parts.push(`삼복승 적중${h.payouts && h.payouts.trifecta ? ' ' + h.payouts.trifecta + '배' : ''}`);
    if (!h.quinella && !h.trifecta) parts.push('추천 조합 미적중');
    timerDetail.textContent = parts.join(' · ');
  } else if (st.state === 'manual') { timerRow.innerHTML = '<span class="err">❌ 결과 수집 실패 - 수동 확인 필요</span>'; timerDetail.textContent = '(발주 후 7/9/11분 재시도 실패)'; }
  else if (st.state === 'cancelled') { timerRow.textContent = '예약 취소됨'; timerDetail.textContent = ''; }
}

function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

btnArmTimer.addEventListener('click', () => {
  btnArmTimer.disabled = true; btnArmTimer.textContent = '예약 중…';
  chrome.storage.local.get({ raceKey: '', timerDeadline: 0 }, ({ raceKey, timerDeadline }) => {
    if (!timerDeadline) {
      timerCard.style.display = 'block';
      timerRow.innerHTML = '<span class="err">먼저 상단 타이머에 발주시각을 설정하세요.</span>';
      timerDetail.textContent = '';
      btnArmTimer.disabled = false; btnArmTimer.textContent = '⏱ 결과 자동수집 예약 (발주시각 기준)';
      return;
    }
    chrome.runtime.sendMessage({ type: 'SCHEDULE_RESULT_TIMER', raceKey, deadline: timerDeadline }, (res) => {
      if (!res || !res.ok) { timerCard.style.display = 'block'; timerRow.innerHTML = `<span class="err">${(res && res.error) || '예약 실패'}</span>`; }
      btnArmTimer.disabled = false; btnArmTimer.textContent = '⏱ 결과 자동수집 예약 (발주시각 기준)';
    });
  });
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
    tripleDetail.textContent = `${ts.lastTripleRaceKey || ''} · 복승 ${c.quinella || 0} · 쌍승 ${c.exacta || 0}`;
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

// [수정2] 경기 마감으로 자동수집이 중단된 상태를 팝업에 표시
function renderAutoClosed(st) {
  if (!st || !st.closed) return;
  tripleRow.innerHTML = `<span class="err">⏹ 경기 마감 - 자동수집 중단됨${st.closeReason ? ' (' + st.closeReason + ')' : ''}</span>`;
}

btnTriple.addEventListener('click', async () => {
  btnTriple.disabled = true; btnTriple.textContent = '수집 중… (복승→쌍승)';
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
  btnTriple.disabled = false; btnTriple.textContent = '⚡ 전체 자동 수집 (복승·쌍승)';
});

// ── [1번] 즉시 분석: 수집 → 서버 이상감지 → 요약 표시 ────────────────
const btnInstant = document.getElementById('instantAnalyze');
const analyzeCard = document.getElementById('analyzeCard');
const analyzeSummary = document.getElementById('analyzeSummary');
const analyzeDetail = document.getElementById('analyzeDetail');

function renderAnalyze(a, at) {
  if (!a) return;
  analyzeCard.style.display = 'block';
  analyzeSummary.className = 'status-line ok';
  analyzeSummary.textContent = (a.summary || '분석 결과') + (at ? ` · ${fmtTime(at)}` : '');
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
  if ((a.form || []).length) {
    lines.push('🏇 전적등급: ' + a.form.slice(0, 4).map((h) => `${h.grade} ${h.no}번(${h.totalScore})`).join(', '));
  }
  if (!a.hasPrev) lines.push('※ 직전 데이터 없음 — 변동은 다음 수집부터');
  analyzeDetail.textContent = lines.join('\n');
}

// [실시간 분석 유지 버그수정] 즉시분석(수동) 결과를 현재 경주에 '고정' →
//   백그라운드 재분석이 '초반(기준값 설정/재설정·직전없음)' 결과로 덮어써 되돌리던 문제 방지.
//   경주 전환(raceKey 변경) 시에만 고정 해제. 같은 경주의 '더 풍부한' 갱신은 그대로 반영.
let _pinnedAnalyzeRk = null;
function applyAnalyzeStatus(av, fromManual) {
  if (!av || !av.data) return;
  const d = av.data, rk = d.raceKey || '';
  const isBaseline = !!(d.baselineSet || d.baselineReset || d.hasPrev === false);
  // 백그라운드 갱신이 '같은 경주의 초반 결과'면 무시(수동 고정 유지 → 되돌이 방지)
  if (!fromManual && _pinnedAnalyzeRk && rk === _pinnedAnalyzeRk && isBaseline) return;
  if (fromManual) _pinnedAnalyzeRk = rk;                            // 수동 분석 → 이 경주 고정
  else if (rk && rk !== _pinnedAnalyzeRk) _pinnedAnalyzeRk = null;  // 경주 전환 → 고정 해제
  renderAnalyze(d, av.at);
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
      const at = Date.now();
      // [실시간 분석 유지] 수동 즉시분석 = manual 플래그로 저장 → 이 경주에 고정(백그라운드 되돌이 방지)
      applyAnalyzeStatus({ data: res.data, at, manual: true }, true);
      // [3번] 즉시분석 결과를 저장 → 팝업을 닫았다 열어도 자동 복원
      chrome.storage.local.set({ analyzeStatus: { data: res.data, at, manual: true } });
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
  if (changes.autoCollectStatus) renderAutoClosed(changes.autoCollectStatus.newValue);
  if (changes.autoSend) els.autoSend.checked = !!changes.autoSend.newValue;   // [수정2] 마감 시 자동 OFF 반영
  if (changes.resultAutoStatus) renderResultTimer(changes.resultAutoStatus.newValue);
  if (changes.analyzeStatus && changes.analyzeStatus.newValue) {
    const av = changes.analyzeStatus.newValue; applyAnalyzeStatus(av, !!av.manual);
  }
});

// ── 초기화 ──────────────────────────────────────────────────────────
loadState();
checkServer();
startPostTimeTicker();   // [3번] 발주시각 실시간 표시
