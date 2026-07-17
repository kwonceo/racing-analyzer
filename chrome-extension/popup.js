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
  ovMatrixBtn: $('ovMatrixBtn'), ovPicksBtn: $('ovPicksBtn'), ovTimelineBtn: $('ovTimelineBtn'),   // [오버레이 표시 제어]
  interval: $('interval'),
  autoMode: $('autoMode'),
  sport: $('sport'),           // [수정#3] 종목: horse|cycle|boat|bike
  detectedCategory: $('detectedCategory'),   // [탭분리] 배당판 자동 감지 종목 표시
  market: $('market'),
  marketRow: $('marketRow'),
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
    { autoSend: false, intervalSec: 30, raceKey: '', autoMode: 'triple', sport: 'horse', market: 'auto', japanType: 'local', status: null, resultStatus: null, tripleStatus: null, tripleProgress: null, resultAutoStatus: null, analyzeStatus: null, autoCollectStatus: null, overlayEnabled: false, detectedCategory: '', detectedAt: 0, ovShowMatrix: false, ovShowPicks: true, ovShowTimeline: false },
    (v) => {
      els.autoSend.checked = !!v.autoSend;
      renderDetectedCategory(v.detectedCategory, v.detectedAt);   // [탭분리] 자동 감지 종목 복원
      if (els.overlayEnabled) els.overlayEnabled.checked = !!v.overlayEnabled;   // [보완#1] 오버레이 상태 복원
      _setOvBtn(els.ovMatrixBtn, !!v.ovShowMatrix);   // [오버레이 표시 제어] 버튼 활성 상태 복원
      _setOvBtn(els.ovPicksBtn, v.ovShowPicks !== false);
      _setOvBtn(els.ovTimelineBtn, !!v.ovShowTimeline);
      els.interval.value = String(v.intervalSec || 30);
      els.autoMode.value = v.autoMode || 'triple';
      if (els.sport) els.sport.value = v.sport || 'horse';   // [수정#3] 종목 복원
      if (els.market) els.market.value = v.market || 'auto';
      renderJapanType(v.japanType || 'local');   // [1번] 중앙/지방 버튼 상태
      syncSportUI();                              // [수정#3] 경륜/경정이면 경마 지역/종류 숨김
      syncJapanTypeUI();
      els.raceKey.value = v.raceKey || '';
      renderStatus(v.status);
      renderTripleStatus(v.tripleStatus);
      if (v.tripleProgress && !v.tripleProgress.done) renderTripleProgress(v.tripleProgress);
      renderAutoClosed(v.autoCollectStatus);   // [수정2] 경기 마감 시 중단 안내(팝업 재오픈에도 유지)
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
const SUPPORTED_SITE = /keiba\.go\.jp|asyukk|qwqwd|dke-d11diw/i;
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

// [오버레이 표시 제어] 📊 매트릭스 · 🎯 추천 · ⏱ 타임라인 버튼 — 켜면 오버레이 자동 ON + 해당 정보 표시.
function _setOvBtn(btn, on) {
  if (!btn) return;
  btn.dataset.on = on ? '1' : '0';
  btn.style.background = on ? '#0e7490' : '#1e293b';
  btn.style.borderColor = on ? '#22d3ee' : '#334155';
  btn.style.color = on ? '#e0f2fe' : (btn.id === 'ovPicksBtn' ? '#38d39f' : btn.id === 'ovTimelineBtn' ? '#c4b5fd' : '#7dd3fc');
}
function _wireOvBtn(btn, key) {
  if (!btn) return;
  btn.addEventListener('click', () => {
    const on = btn.dataset.on !== '1';
    _setOvBtn(btn, on);
    const patch = {}; patch[key] = on;
    // 표시 버튼을 켜면 오버레이도 자동 ON(끄지는 않음 — 다른 정보 볼 수 있으니)
    if (on && els.overlayEnabled && !els.overlayEnabled.checked) { els.overlayEnabled.checked = true; patch.overlayEnabled = true; }
    chrome.storage.local.set(patch);
  });
}
_wireOvBtn(els.ovMatrixBtn, 'ovShowMatrix');
_wireOvBtn(els.ovPicksBtn, 'ovShowPicks');
_wireOvBtn(els.ovTimelineBtn, 'ovShowTimeline');
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
// [수정#3] 종목 변경(경마/경륜/경정) — 경륜·경정은 경마 지역·일본 종류 선택을 숨긴다.
if (els.sport) els.sport.addEventListener('change', () => {
  chrome.storage.local.set({ sport: els.sport.value });
  syncSportUI();
  syncJapanTypeUI();
});
function syncSportUI() {
  const isHorse = !els.sport || els.sport.value === 'horse';
  if (els.marketRow) els.marketRow.style.display = isHorse ? '' : 'none';
  if (els.japanTypeRow && !isHorse) els.japanTypeRow.style.display = 'none';
}
// [탭분리] 배당판에서 자동 감지된 종목 표시: "현재: 일본 경륜 감지됨"
const CATEGORY_LABEL = {
  korea: '한국경마', japan_local: '일본 지방경마', japan_central: '일본 중앙경마',
  boat: '일본 경정', cycle: '일본 경륜', bike: '일본 바이크',
};
function renderDetectedCategory(cat, at) {
  const el = els.detectedCategory; if (!el) return;
  if (!cat || !CATEGORY_LABEL[cat]) { el.textContent = '감지 대기…'; el.style.color = '#94a3b8'; return; }
  // 5분 이상 지난 감지는 흐리게(오래된 값)
  const stale = at && (Date.now() - at > 5 * 60 * 1000);
  el.textContent = `현재: ${CATEGORY_LABEL[cat]} 감지됨`;
  el.style.color = stale ? '#94a3b8' : '#38d39f';
}

// [1번] 일본 중앙(JRA)/지방(NAR) 선택 — 중앙=배당만·마감1분30초전, 지방=전적+배당
function renderJapanType(jt) {
  const on = 'background:#2563eb;border-color:#2563eb;color:#fff';
  const off = 'background:#1e293b;border-color:#334155;color:#e2e8f0';
  const base = 'cursor:pointer;border:1px solid;border-radius:6px;padding:4px 8px;font:inherit;font-size:12px;';
  if (els.jtLocal) els.jtLocal.style.cssText = base + (jt === 'central' ? off : on);
  if (els.jtCentral) els.jtCentral.style.cssText = base + (jt === 'central' ? on : off);
}
function syncJapanTypeUI() {
  // 한국경마 모드 또는 경륜/경정(비-경마)에서는 일본 종류 선택을 숨긴다.
  const nonHorse = els.sport && els.sport.value !== 'horse';
  const hide = nonHorse || (els.market && els.market.value === 'korea');
  if (els.japanTypeRow) els.japanTypeRow.style.display = hide ? 'none' : '';
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

function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

// ── [결과 입력·자유 코멘트] 1~3착 + 코멘트 + 중요도 → /api/review/save(학습+복기) ──
//   결과는 record-result 와 동일 학습, 코멘트는 원문 그대로 저장·키워드는 참고용 태깅(강제분류 없음).
const btnSaveResult = document.getElementById('saveResultInput');
const resMsg = document.getElementById('resultInputMsg');
const resComment = document.getElementById('resComment');
const resTagPreview = document.getElementById('resTagPreview');
let _importance = 1;   // 1일반 / 2중요 / 3최고
function _resNum(id) { const v = (document.getElementById(id).value || '').trim(); return v === '' ? null : parseInt(v, 10); }

// [3번] 코멘트 키워드 미리보기(서버 _comment_tags 와 동일 규칙·참고용)
const COMMENT_TAG_RULES = [
  [['아쉽', '아쉬', '惜'], '아쉬움'], [['놓쳤', '놓침', '못잡', '못 잡', '놓친'], '놓침'],
  [['주목', '기억', '다음에'], '다음 주목'], [['스마트머니', '스마트 머니'], '스마트머니'],
  [['역배열', '역배'], '역배열'], [['복병'], '복병'], [['고배당'], '고배당'],
];
function previewTags(text) {
  const c = text || '', out = [];
  COMMENT_TAG_RULES.forEach(([kws, label]) => { if (out.indexOf(label) < 0 && kws.some((k) => c.indexOf(k) >= 0)) out.push(label); });
  return out;
}
if (resComment) resComment.addEventListener('input', () => {
  const t = previewTags(resComment.value);
  resTagPreview.textContent = t.length ? '🏷 키워드: ' + t.join(' · ') : '';
});

// [4번] 중요도 버튼 — 선택 표시
function _setImp(v) {
  _importance = v;
  [['imp1', 1], ['imp2', 2], ['imp3', 3]].forEach(([id, iv]) => {
    const b = document.getElementById(id); if (!b) return;
    const on = iv === v;
    b.style.background = on ? (iv === 3 ? '#f59e0b' : iv === 2 ? '#7c3aed' : '#334155') : '#1e293b';
    b.style.borderColor = on ? (iv === 3 ? '#fbbf24' : iv === 2 ? '#a78bfa' : '#64748b') : '#334155';
    b.style.color = on ? '#fff' : '#e2e8f0';
  });
}
[['imp1', 1], ['imp2', 2], ['imp3', 3]].forEach(([id, iv]) => {
  const b = document.getElementById(id); if (b) b.addEventListener('click', () => _setImp(iv));
});
_setImp(1);

if (btnSaveResult) btnSaveResult.addEventListener('click', () => {
  const raceKey = (els.raceKey.value || '').trim();
  const r1 = _resNum('res1'), r2 = _resNum('res2'), r3 = _resNum('res3');
  const comment = (resComment && resComment.value || '').trim();
  if (!raceKey) { resMsg.className = 'muted err'; resMsg.textContent = '먼저 상단 raceKey를 입력/감지하세요.'; return; }
  if (r1 == null && !comment) { resMsg.className = 'muted err'; resMsg.textContent = '착순(1착) 또는 코멘트 중 하나는 입력하세요.'; return; }
  const result = {};
  if (r1 != null) result['1st'] = r1;
  if (r2 != null) result['2nd'] = r2;
  if (r3 != null) result['3rd'] = r3;
  btnSaveResult.disabled = true; btnSaveResult.textContent = '저장 중…';
  resMsg.className = 'muted'; resMsg.textContent = '서버 전송 중…';
  // 현재 분석 신호 스냅샷도 함께 저장(복기용)
  chrome.storage.local.get({ analyzeStatus: null }, (v) => {
    const signals = (v.analyzeStatus && v.analyzeStatus.data) || _lastAnalyze || null;
    chrome.runtime.sendMessage({ type: 'SAVE_REVIEW', payload: { raceKey, result, comment, importance: _importance, signals } }, (res) => {
      btnSaveResult.disabled = false; btnSaveResult.textContent = '저장 + 학습';
      if (chrome.runtime.lastError || !res || !res.ok) {
        resMsg.className = 'muted err';
        resMsg.textContent = '저장 실패: ' + ((res && res.error) || (chrome.runtime.lastError && chrome.runtime.lastError.message) || '서버 응답 없음');
        return;
      }
      const d = res.data || {};
      const hit = d.hit || {};
      const parts = [`✅ 저장+학습 · ${d.raceKey || raceKey}`];
      if (r1 != null) parts.push(`착순 ${[r1, r2, r3].filter((x) => x != null).join('-')}`);
      const impLbl = _importance === 3 ? '⭐⭐최고' : _importance === 2 ? '⭐중요' : '일반';
      parts.push('중요도 ' + impLbl);
      if ((d.tagLabels || []).length) parts.push('🏷 ' + d.tagLabels.join('·'));
      if (hit.quinella) parts.push('복승 적중' + (hit.payouts && hit.payouts.quinella ? ` ${hit.payouts.quinella}배` : ''));
      if (hit.trifecta) parts.push('삼복승 적중' + (hit.payouts && hit.payouts.trifecta ? ` ${hit.payouts.trifecta}배` : ''));
      parts.push('→ 통계 탭 코멘트 모아보기에 반영');
      resMsg.className = 'muted ok';
      resMsg.textContent = parts.join(' · ');
    });
  });
});

// ── [로그 보기] 중요 신호(급락·역배열·스마트머니·경고·확신도) 로그 표시 ──
//   즉시분석/백그라운드 분석이 저장한 analyzeStatus.data 를 그대로 읽어 요약(추가 서버호출 없음).
const btnViewLog = document.getElementById('viewLogBtn');
const logCard = document.getElementById('logCard');
const logHead = document.getElementById('logHead');
const logBody = document.getElementById('logBody');
const logAlertDot = document.getElementById('logAlertDot');
let _lastAnalyze = null;

function hasImportantSignal(a) {
  if (!a) return false;
  const flips = (a.reversals || []).filter((r) => r.flipped).length;
  const smart = (a.darkHorses || []).filter((h) => h.smartMoney).length;
  const alertFired = !!(a.alertSignal && (a.alertSignal.fired || (a.alertSignal.alerts || []).length));
  const strong = (((a.signalQuality || {}).signalConfidence || {}).strong || []).length;
  return (a.drops || []).length > 0 || flips > 0 || smart > 0 || alertFired || strong > 0;
}

// 중요 신호 유무 → 버튼 옆 🔴 표시
function updateLogAlert(a) {
  if (a) _lastAnalyze = a;
  if (logAlertDot) logAlertDot.style.display = hasImportantSignal(_lastAnalyze) ? '' : 'none';
}

function renderLog(a) {
  logCard.style.display = 'block';
  if (!a) {
    logHead.innerHTML = '<span class="muted">분석 데이터 없음</span>';
    logBody.textContent = '먼저 🚨 즉시 분석을 실행하면 신호 로그가 표시됩니다.';
    return;
  }
  const phase = a.afterClose ? '마감 후' : (a.minutesBefore != null ? `마감 ${a.minutesBefore}분전` : '');
  const when = a._at ? fmtTime(a._at) : '';
  logHead.innerHTML = hasImportantSignal(a)
    ? `<span class="err">🔴 중요 신호 감지</span> <span class="muted">${phase}${when ? ' · ' + when : ''}</span>`
    : `<span class="ok">신호 없음(안정)</span> <span class="muted">${phase}${when ? ' · ' + when : ''}</span>`;
  const lines = [];
  // 배당 급락
  (a.drops || []).slice(0, 5).forEach((d) => {
    const c = (d.combo || []).join('-');
    lines.push(`📉 급락  ${c}  ${d.prev}→${d.cur} (${d.pct > 0 ? '▲' : '▼'}${Math.abs(d.pct)}%)`);
  });
  // 쌍승 역배열
  (a.reversals || []).filter((r) => r.flipped).slice(0, 3).forEach((r) => {
    lines.push(`🔄 역배열  ${(r.favored || []).join('→')}${r.ratio != null ? ` (비율 ${r.ratio})` : ''}`);
  });
  // 스마트머니 복병
  (a.darkHorses || []).filter((h) => h.smartMoney).slice(0, 3).forEach((h) => {
    const st = h.stars ? '★'.repeat(h.stars) : '';
    lines.push(`💰 스마트머니  ${h.no}번 ${st}${h.oddsRepr ? ' · ' + h.oddsRepr : ''}`);
  });
  // 경고 신호
  const al = a.alertSignal;
  if (al && (al.fired || (al.alerts || []).length)) {
    (al.alerts || []).slice(0, 3).forEach((x) => {
      lines.push(`⚠️ 경고  ${(x.combo || []).join('-')} ${x.before}→${x.after}`);
    });
    if (!(al.alerts || []).length && al.message) lines.push(`⚠️ 경고  ${al.message}`);
  }
  // 확신도 점수(신호 종합 신뢰도)
  const sc = ((a.signalQuality || {}).signalConfidence) || {};
  const horses = sc.horses || {};
  const scored = Object.keys(horses).map((no) => ({ no, c: horses[no] && horses[no].confidence }))
    .filter((x) => x.c != null).sort((a, b) => b.c - a.c).slice(0, 4);
  if (scored.length) {
    lines.push('🎯 확신도  ' + scored.map((x) => `${x.no}번 ${Math.round(x.c)}점`).join(', '));
  }
  logBody.textContent = lines.length ? lines.join('\n') : '감지된 신호가 없습니다(배당 안정).';
}

if (btnViewLog) btnViewLog.addEventListener('click', () => {
  if (logCard.style.display === 'block') { logCard.style.display = 'none'; return; }
  // 저장된 최신 분석 로드
  chrome.storage.local.get({ analyzeStatus: null }, (v) => {
    const a = v.analyzeStatus && v.analyzeStatus.data ? Object.assign({}, v.analyzeStatus.data, { _at: v.analyzeStatus.at }) : _lastAnalyze;
    renderLog(a);
  });
});

// ── [복기 저장] 경주 종료 후 중요 신호 + 결과 묶어 저장 → 패턴학습·복기 탭 반영 ──
const btnReviewSave = document.getElementById('reviewSaveBtn');
const reviewCard = document.getElementById('reviewCard');
const reviewMsg = document.getElementById('reviewMsg');

if (btnReviewSave) btnReviewSave.addEventListener('click', () => {
  const raceKey = (els.raceKey.value || '').trim();
  if (!raceKey) { reviewCard.style.display = 'block'; reviewMsg.className = 'status-line err'; reviewMsg.textContent = '먼저 상단 raceKey를 입력/감지하세요.'; return; }
  const r1 = _resNum('res1'), r2 = _resNum('res2'), r3 = _resNum('res3');
  const result = {};
  if (r1 != null) result['1st'] = r1;
  if (r2 != null) result['2nd'] = r2;
  if (r3 != null) result['3rd'] = r3;
  btnReviewSave.disabled = true; btnReviewSave.textContent = '저장 중…';
  reviewCard.style.display = 'block'; reviewMsg.className = 'status-line'; reviewMsg.textContent = '신호+결과 저장 중…';
  chrome.storage.local.get({ analyzeStatus: null }, (v) => {
    const signals = (v.analyzeStatus && v.analyzeStatus.data) || _lastAnalyze || null;
    chrome.runtime.sendMessage({ type: 'SAVE_REVIEW', payload: { raceKey, result, signals } }, (res) => {
      btnReviewSave.disabled = false; btnReviewSave.textContent = '🧠 복기 저장 (신호+결과)';
      if (chrome.runtime.lastError || !res || !res.ok) {
        reviewMsg.className = 'status-line err';
        reviewMsg.textContent = '복기 저장 실패: ' + ((res && res.error) || (chrome.runtime.lastError && chrome.runtime.lastError.message) || '서버 응답 없음');
        return;
      }
      const d = res.data || {};
      const parts = [`✅ 복기 저장됨 · ${d.raceKey || raceKey}`];
      if (d.signalCount != null) parts.push(`중요신호 ${d.signalCount}건`);
      if (d.hasResult) parts.push('결과 포함·학습 반영');
      parts.push('→ 분석기 결과기록 탭에서 확인');
      reviewMsg.className = 'status-line ok';
      reviewMsg.textContent = parts.join(' · ');
    });
  });
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
  // [로그 보기] 최신 분석 → 중요신호 🔴 표시 갱신 + 열린 로그 카드 실시간 재렌더
  updateLogAlert(Object.assign({}, d, { _at: av.at }));
  if (logCard && logCard.style.display === 'block') renderLog(Object.assign({}, d, { _at: av.at }));
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
  if (changes.tripleStatus) renderTripleStatus(changes.tripleStatus.newValue);
  if (changes.tripleProgress) renderTripleProgress(changes.tripleProgress.newValue);
  if (changes.autoCollectStatus) renderAutoClosed(changes.autoCollectStatus.newValue);
  if (changes.autoSend) els.autoSend.checked = !!changes.autoSend.newValue;   // [수정2] 마감 시 자동 OFF 반영
  if (changes.analyzeStatus && changes.analyzeStatus.newValue) {
    const av = changes.analyzeStatus.newValue; applyAnalyzeStatus(av, !!av.manual);
  }
  if (changes.detectedCategory) {   // [탭분리] 배당판 자동 감지 종목 실시간 반영
    chrome.storage.local.get({ detectedAt: 0 }, (v) => renderDetectedCategory(changes.detectedCategory.newValue, v.detectedAt));
  }
});

// ── 초기화 ──────────────────────────────────────────────────────────
loadState();
checkServer();
startPostTimeTicker();   // [3번] 발주시각 실시간 표시
