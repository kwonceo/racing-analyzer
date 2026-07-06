/* ===== app.js — 메인 로직 / UI 와이어링 =====
 * 키는 서버(.env)가 보유. 브라우저는 /api/* 만 호출.
 * 한국: PDF → 페이지 렌더(PNG) → 서버 Vision 추출 → 기수/경주 병합 → 분석
 * 분석 출력(v3): race_summary, horses[grade/score], grade_picks(A/B/C/D 45:28:17:10),
 *              pattern2_horses, special_notes, betting_recommend{quinella,trifecta}, analysis
 * 의존: JockeyDB, PdfParser, Analysis, History
 */
(function () {
  'use strict';

  const GRADE_WEIGHT = { A: 45, B: 28, C: 17, D: 10 };

  const state = {
    jockeyStats: {},
    koreaFile: null,      // [v1.12.0] 업로드 대기중 PDF File
    koreaPolling: null,   // [v1.12.0] 진행상황 폴링 setInterval 핸들
    koreaRaces: [],
    japanOdds: null,
    japanForm: null,
    lastReports: {},
    koreaScored: {},  // title -> {race, form:[{no,name,jockey,formScore,recentPlacings}]} — 배당 통합용
    koreaOddsPrev: {},  // [2번] title -> 직전 signal text Set (신규 변동 감지용)
    koreaTimeline: {},  // [3번] title -> [{time,changed,signals,integrated,raceKey}]
    jpForm: null,       // [5번] 일본경마 전적표 이미지 블록
    jpOddsPrev: null,   // [5번] 일본 직전 signal text Set
    jpTimeline: [],     // [5번] 일본 배당 변동 타임라인
    lastSheets: {},   // title -> {horses(추출 출전마), distance} — Phase 4 통합분석용
    lastCombined: {}, // title -> {bets, recOdds, hadAnomaly, budget} — Phase 5 결과기록용
    oddsTrack: { betType: '복승', raceKey: null, snaps: 0, nos: new Set(), firstOdds: {},
      series: {}, times: [], alerted: {}, auto: null, deadlineMs: 0,
      exSeries: {}, exTimes: [], dual: false, _pendingType: null }, // 다중 캡처(자동 교대 중단: 쌍승은 확장 수집)

    raceCondition: { track: '', weather: '' }, // [1번] 주로 상태 / 날씨
    horseWeights: {},        // [2번] title -> { 마번: {cur, prev} }
    activeKoreaCtx: null,    // 재분석용 컨텍스트 {idx,title,race,sheetHorses}
    tripleCaps: { quinella: null, exacta: null, trio: null }, // [3번] 3종 배당판 이미지
    pendingResultTitle: null, // [기능2] 결과입력 탭 이동 시 포커스할 경주명
  };

  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));

  function showLoading(t) { $('#loadingText').textContent = t || 'AI 분석 중...'; $('#loadingOverlay').classList.remove('hidden'); }
  function hideLoading() { $('#loadingOverlay').classList.add('hidden'); }
  function toast(m) { alert(m); }

  // ---------- 서버 상태 ----------
  async function checkServerHealth() {
    const el = $('#serverStatus');
    const h = await Analysis.health();
    if (h.ok && h.has_key) { el.textContent = `● 서버 정상 (${h.model})`; el.classList.add('ok'); }
    else if (h.ok) { el.textContent = '● 서버 ON · 키 없음 (.env 설정 필요)'; el.classList.remove('ok'); }
    else { el.textContent = '● 서버 연결 안 됨'; el.classList.remove('ok'); }
  }

  // ---------- 탭 ----------
  function initTabs() {
    $$('.tab-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        $$('.tab-btn').forEach((b) => b.classList.remove('active'));
        $$('.tab-panel').forEach((p) => p.classList.remove('active'));
        btn.classList.add('active');
        $('#tab-' + btn.dataset.tab).classList.add('active');
        if (btn.dataset.tab === 'stats') renderStats();
        if (btn.dataset.tab === 'result') { renderResultForm(); loadHighlights(); loadReportList(); loadPendingResults(); }
        if (btn.dataset.tab === 'jockeydb') renderJockeyDb();
        if (btn.dataset.tab === 'jp') { startJapanOddsWatch(); loadJapanReviewList(); }   // [5번] 일본경마: 실시간 배당 연동 + 분석 내역 복기 목록
      });
    });
  }

  // ---------- 드롭존 ----------
  function wireDropZone(zone, input, onFile, accept) {
    zone.addEventListener('click', () => input.click());
    input.addEventListener('change', () => { if (input.files[0]) onFile(input.files[0]); });
    ['dragover', 'dragenter'].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add('dragover'); }));
    ['dragleave', 'drop'].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove('dragover'); }));
    zone.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f && (!accept || f.type.match(accept))) onFile(f); });
  }

  function parsePages(str, max) {
    const out = new Set();
    String(str || '').split(',').forEach((part) => {
      const m = part.trim().match(/^(\d+)\s*-\s*(\d+)$/);
      if (m) { for (let i = +m[1]; i <= +m[2]; i++) out.add(i); }
      else { const n = parseInt(part.trim(), 10); if (!isNaN(n)) out.add(n); }
    });
    return [...out].filter((n) => n >= 1 && n <= max).sort((a, b) => a - b);
  }

  // ---------- [1번] 경주 환경(주로/날씨) ----------
  function initCondBar() {
    const bar = $('#condBar');
    if (!bar) return;
    bar.querySelectorAll('.cond-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const isTrack = btn.dataset.track != null;
        const attr = isTrack ? 'track' : 'weather';
        const val = btn.dataset[attr];
        const group = btn.parentElement;
        const already = btn.classList.contains('active');
        group.querySelectorAll('.cond-btn').forEach((b) => b.classList.remove('active'));
        if (already) { state.raceCondition[attr] = ''; }
        else { btn.classList.add('active'); state.raceCondition[attr] = val; }
        // 비 선택 시 주로 불량 자동 연동
        if (!isTrack && !already && val === '비') {
          state.raceCondition.track = '불량';
          bar.querySelectorAll('.cond-btn[data-track]').forEach((b) =>
            b.classList.toggle('active', b.dataset.track === '불량'));
        }
        updateCondHint();
      });
    });
    updateCondHint();
  }
  function updateCondHint() {
    const c = state.raceCondition;
    $('#condHint').textContent = (c.track || c.weather)
      ? `선택됨 — 주로: ${c.track || '-'} / 날씨: ${c.weather || '-'} (분석에 반영)`
      : '선택 사항 — 비 선택 시 주로가 불량으로 자동 설정됩니다';
  }

  // ---------- 한국경마 (서버 백그라운드 분석 v1.12.0) ----------
  // PDF를 서버로 업로드 → 서버가 감지·추출·분석을 백그라운드로 수행 → 클라이언트는 진행상황만 폴링.
  // 탭 전환/새로고침/서버 재시작에도 계속 진행되고, 결과는 data/korea_session.json 에 영구 저장.
  function initKorea() {
    const zone = $('#koreaUploadZone'), input = $('#koreaPdfInput');
    $('#koreaBrowseBtn').addEventListener('click', (e) => { e.stopPropagation(); input.click(); });
    wireDropZone(zone, input, handleKoreaPdf, /application\/pdf/);
    $('#koreaScanBtn').addEventListener('click', startKoreaServerAnalysis);
    $('#koreaResetBtn').addEventListener('click', resetKoreaSession);
    initPrerace();   // [보완#2] 저장된 사전분석 목록(세션 무관 과거 접근)
  }

  // ---------- [보완#2] 저장된 사전분석 (data/prerace) ----------
  // 아침에 분석해 둔 과거 경주를 세션과 무관하게 날짜별로 즉시 불러온다.
  function initPrerace() {
    const toggle = $('#koreaPreraceToggle'); if (!toggle) return;
    const list = $('#koreaPreraceList');
    const refresh = $('#koreaPreraceRefresh');
    toggle.addEventListener('click', async () => {
      const open = list.style.display !== 'none';
      if (open) {
        list.style.display = 'none'; toggle.textContent = '📅 저장된 사전분석 열기';
        if (refresh) refresh.style.display = 'none';
        return;
      }
      list.style.display = 'block'; toggle.textContent = '📅 저장된 사전분석 닫기';
      if (refresh) refresh.style.display = '';
      await loadPreraceList();
    });
    if (refresh) refresh.addEventListener('click', () => loadPreraceList());
  }

  /** 사전분석 패널이 열려 있으면 목록 갱신(분석 진행 중 폴링에서 호출) */
  function refreshPreraceIfOpen() {
    const list = $('#koreaPreraceList');
    if (list && list.style.display !== 'none') loadPreraceList();
  }

  /** /api/korea/prerace 목록을 날짜별로 그룹핑해 렌더 */
  async function loadPreraceList() {
    const box = $('#koreaPreraceList'); if (!box) return;
    box.innerHTML = '<p class="hint">⏳ 불러오는 중…</p>';
    let races;
    try { races = ((await (await fetch('/api/korea/prerace')).json()) || {}).races || []; }
    catch (e) { box.innerHTML = `<p class="err">목록 로드 실패: ${esc(e.message)}</p>`; return; }
    if (!races.length) {
      box.innerHTML = '<p class="hint">저장된 사전분석이 없습니다. PDF를 업로드해 전경주 분석을 실행하면 경주별로 여기에 쌓입니다.</p>';
      return;
    }
    // 날짜별 그룹핑(index는 savedAt 내림차순 → 날짜 최신순 유지)
    const groups = {};
    races.forEach((r) => { (groups[r.date || '날짜미상'] = groups[r.date || '날짜미상'] || []).push(r); });
    const html = Object.keys(groups).map((date) => {
      const chips = groups[date].sort((a, b) => (a.raceNo || 0) - (b.raceNo || 0)).map((r) => {
        const label = `${esc(r.venue || '')} ${r.raceNo}R${r.distance ? ' ' + esc(r.distance) : ''}`;
        const cnt = r.horseCount ? ` <span class="chip-page">(${r.horseCount}두)</span>` : '';
        return `<button class="race-chip prerace-chip" data-key="${esc(r.key)}">📄 ${label}${cnt}</button>`;
      }).join('');
      return `<div class="prerace-group" style="margin-bottom:10px">
        <div class="matrix-title" style="font-size:13px">🗓 ${esc(date)} <span class="hint" style="font-weight:400">· ${groups[date].length}경주</span></div>
        <div class="race-list">${chips}</div></div>`;
    }).join('');
    box.innerHTML = html;
    box.querySelectorAll('.prerace-chip').forEach((c) =>
      c.addEventListener('click', () => openPreraceRace(c.dataset.key)));
  }

  /** 사전분석 1건을 즉시 로드 → 세션 state에 병합 후 기존 렌더 흐름 재사용 */
  async function openPreraceRace(key) {
    if (!key) return;
    showLoading('저장된 사전분석 불러오는 중…');
    let d;
    try { d = await (await fetch('/api/korea/prerace/' + encodeURIComponent(key))).json(); }
    catch (e) { hideLoading(); toast('불러오기 실패: ' + e.message); return; }
    hideLoading();
    if (!d || d.error) { toast(d && d.error ? d.error : '사전분석을 찾을 수 없습니다.'); return; }
    const race = { venue: d.venue, raceNo: d.raceNo, distance: d.distance, title: d.title, summaryPage: d.summaryPage };
    const title = raceLabel(race);
    // state에 병합(중복이면 갱신) — 이후 analyzeKoreaRace가 푸터·결과입력까지 완전 렌더
    if (d.horses && d.horses.length) state.lastSheets[title] = { horses: d.horses, distance: d.distance };
    if (d.report) state.lastReports[title] = d.report;
    let idx = state.koreaRaces.findIndex((r) => raceLabel(r) === title);
    if (idx < 0) { state.koreaRaces.push(race); idx = state.koreaRaces.length - 1; }
    else { state.koreaRaces[idx] = race; }
    renderRaceChips();
    const chip = $$('#koreaRaceList .race-chip')[idx];
    await analyzeKoreaRace(idx, chip);
    if (chip && chip.scrollIntoView) chip.scrollIntoView({ behavior: 'smooth', block: 'center' });
    toast(`📄 ${title} 사전분석 로드 완료`);
  }

  /** 파일 선택 = '새 PDF 업로드' → 기존 서버 세션 초기화 후 대기(자동 감지 버튼으로 시작) */
  async function handleKoreaPdf(file) {
    try {
      stopKoreaPolling();
      await fetch('/api/korea/reset', { method: 'POST' }).catch(() => {});
      state.koreaFile = file;
      state.koreaRaces = []; state.lastReports = {}; state.lastSheets = {};
      state.koreaTimeline = {}; state.koreaOddsPrev = {};
      stopKoreaOddsWatch();
      $('#koreaReport').innerHTML = ''; $('#koreaIntegrated').innerHTML = '';
      { const t = $('#koreaTimeline'); if (t) t.remove(); }
      $('#koreaRaceList').innerHTML = '';
      $('#koreaRestoreBanner').classList.add('hidden');
      $('#koreaUploadZone').classList.add('has-file');
      $('#koreaConfig').classList.remove('hidden');
      $('#koreaResetBtn').style.display = 'none';
      $('#koreaPdfInfo').textContent = `${file.name} 준비됨. [자동 감지]를 누르면 서버가 백그라운드로 전 경주를 분석합니다 (탭 전환·새로고침해도 계속 진행).`;
      $('#koreaProgress').textContent = '';
    } catch (err) { toast('PDF 준비 실패: ' + err.message); }
  }

  /** [1번] PDF를 서버로 업로드하고 백그라운드 분석 시작 → 폴링 */
  async function startKoreaServerAnalysis() {
    if (!state.koreaFile) { toast('먼저 PDF를 업로드하세요.'); return; }
    const btn = $('#koreaScanBtn');
    try {
      btn.disabled = true;
      $('#koreaProgress').textContent = 'PDF 업로드 중...';
      const fd = new FormData();
      fd.append('pdf', state.koreaFile);
      const r = await fetch('/api/korea/start', { method: 'POST', body: fd });
      const d = await r.json();
      if (!r.ok || d.error) throw new Error(d.error || `업로드 실패(${r.status})`);
      $('#koreaProgress').textContent = '분석 시작됨 — 백그라운드로 진행됩니다.';
      startKoreaPolling();
    } catch (err) { toast('분석 시작 실패: ' + err.message); }
    finally { btn.disabled = false; }
  }

  /** [1번] 진행상황 폴링(탭 전환과 무관하게 서버가 진행) */
  function startKoreaPolling() {
    stopKoreaPolling();
    state.koreaPolling = setInterval(pollKoreaStatus, 2000);
    pollKoreaStatus();
  }
  function stopKoreaPolling() {
    if (state.koreaPolling) { clearInterval(state.koreaPolling); state.koreaPolling = null; }
  }
  async function pollKoreaStatus() {
    let s;
    try { s = await (await fetch('/api/korea/status')).json(); }
    catch (e) { return; } // 네트워크 순간 오류는 다음 폴링에서 회복
    const prog = $('#koreaProgress');
    if (s.status === 'running') {
      prog.textContent = s.message || `분석 중... ${s.done || 0}/${s.total || 0} 경주 완료`;
      refreshPreraceIfOpen();   // [보완#2] 진행 중이면 저장된 사전분석 목록도 실시간 갱신
    } else if (s.status === 'done') {
      stopKoreaPolling();
      prog.textContent = s.message || '완료';
      await loadKoreaSession(true);
      refreshPreraceIfOpen();   // [보완#2] 완료 시 최종 목록 갱신
      toast('✅ 분석 완료 — 결과가 서버에 저장되었습니다.');
    } else if (s.status === 'error') {
      stopKoreaPolling();
      prog.textContent = '오류: ' + (s.error || '알 수 없음');
      toast('분석 오류: ' + (s.error || ''));
    }
  }

  /** 서버 세션(전체)을 불러와 칩·리포트 상태 복원 */
  async function loadKoreaSession(silent) {
    let sess;
    try { sess = await (await fetch('/api/korea/session')).json(); }
    catch (e) { if (!silent) toast('세션 로드 실패'); return null; }
    applyKoreaSession(sess);
    return sess;
  }

  /** 세션 → state 반영 후 칩 렌더 */
  function applyKoreaSession(sess) {
    if (!sess || !(sess.races || []).length) return;
    state.koreaSessionDate = sess.date || state.koreaSessionDate;
    state.jockeyStats = Object.assign({}, state.jockeyStats, sess.jockeyStats || {});
    state.koreaRaces = sess.races.map((r) => ({
      venue: r.venue, raceNo: r.raceNo, distance: r.distance, summaryPage: r.summaryPage, title: r.title,
    }));
    sess.races.forEach((r) => {
      if (r.horses && r.horses.length) state.lastSheets[r.title] = { horses: r.horses, distance: r.distance };
      if (r.report) state.lastReports[r.title] = r.report;
    });
    renderRaceChips();
  }

  /** [2번·3번] 페이지 로드 시 저장된 분석 결과 자동 복원 */
  async function restoreKoreaSession() {
    let sess;
    try { sess = await (await fetch('/api/korea/session')).json(); }
    catch (e) { return; }
    if (!sess) return;
    const hasData = (sess.races || []).length > 0;
    if (!hasData && sess.status !== 'running') return;
    if (hasData) {
      applyKoreaSession(sess);
      const banner = $('#koreaRestoreBanner');
      banner.innerHTML = `📂 이전 분석 결과를 불러왔습니다 — <b>${esc(sess.label || '')}</b>. 칩을 눌러 확인하거나, 위에서 새 PDF를 올려 다시 분석하세요.`;
      banner.classList.remove('hidden');
      $('#koreaConfig').classList.remove('hidden');
      $('#koreaResetBtn').style.display = '';
      $('#koreaPdfInfo').textContent = '';
    }
    if (sess.status === 'running') {
      $('#koreaConfig').classList.remove('hidden');
      $('#koreaProgress').textContent = sess.message || '분석 중...';
      startKoreaPolling();  // 서버 재시작 후에도 계속 진행중이면 이어서 폴링
    }
  }

  /** [초기화] '새 PDF 업로드' — 서버 세션/PDF 삭제 후 UI 리셋 */
  async function resetKoreaSession() {
    stopKoreaPolling();
    await fetch('/api/korea/reset', { method: 'POST' }).catch(() => {});
    state.koreaFile = null; state.koreaRaces = []; state.lastReports = {}; state.lastSheets = {};
    $('#koreaReport').innerHTML = ''; $('#koreaIntegrated').innerHTML = '';
    $('#koreaRaceList').innerHTML = '';
    $('#koreaRestoreBanner').classList.add('hidden');
    $('#koreaConfig').classList.add('hidden');
    $('#koreaUploadZone').classList.remove('has-file');
    $('#koreaPdfInput').value = '';
    $('#koreaPdfInfo').textContent = ''; $('#koreaProgress').textContent = '';
    toast('초기화됨 — 새 PDF를 업로드하세요.');
  }

  function mergeJockey(j) {
    const total = j.total || 0;
    const winRate = total ? Math.round((j.w1 / total) * 1000) / 10 : 0;
    const placeRate = total ? Math.round(((j.w1 + j.w2 + j.w3) / total) * 1000) / 10 : 0;
    JockeyDB.upsert(j.name, { winRate, placeRate, rides: total, w1: j.w1, w2: j.w2, w3: j.w3, month: j.month, mW1: j.mW1, mW2: j.mW2, mW3: j.mW3 });
  }
  function rebuildJockeyStats() { state.jockeyStats = {}; JockeyDB.all().forEach((j) => { state.jockeyStats[j.name] = j; }); }

  function raceLabel(r) {
    const v = r.venue ? r.venue + ' ' : '';
    return `${v}${r.raceNo}경주${r.distance ? ' ' + r.distance : ''}`;
  }

  function renderRaceChips() {
    const list = $('#koreaRaceList'); list.innerHTML = '';
    state.koreaRaces.forEach((race, idx) => {
      const chip = document.createElement('button');
      chip.className = 'race-chip';
      chip.dataset.idx = idx;
      chip.innerHTML = `<span class="chip-status">○</span> ${esc(raceLabel(race))} <span class="chip-page">(p${race.summaryPage})</span>`;
      chip.addEventListener('click', () => analyzeKoreaRace(idx, chip));
      list.appendChild(chip);
    });
    const legend = $('#koreaChipLegend');
    if (legend) legend.classList.toggle('hidden', !state.koreaRaces.length);
    refreshRaceChipStatus();
  }

  /** [기능3] 경주 칩 진행상황 갱신: 미분석 ○(회색) / 분석완료 ✅(초록) / 결과입력 🏁(파랑) */
  function refreshRaceChipStatus() {
    const savedTitles = new Set(History.all().map((r) => r.raceTitle));
    $$('#koreaRaceList .race-chip').forEach((chip) => {
      const race = state.koreaRaces[+chip.dataset.idx];
      if (!race) return;
      const title = raceLabel(race);
      const st = chip.querySelector('.chip-status');
      chip.classList.remove('chip-todo', 'chip-analyzed', 'chip-done');
      if (savedTitles.has(title)) { chip.classList.add('chip-done'); if (st) st.textContent = '🏁'; }
      else if (state.lastReports[title]) { chip.classList.add('chip-analyzed'); if (st) st.textContent = '✅'; }
      else { chip.classList.add('chip-todo'); if (st) st.textContent = '○'; }
    });
  }


  /** 칩 클릭: 서버가 이미 분석한 결과를 렌더(재분석 없음). 결과가 없으면 서버 재추출. */
  async function analyzeKoreaRace(idx, chipEl) {
    const race = state.koreaRaces[idx]; if (!race) return;
    $$('#koreaRaceList .race-chip').forEach((c) => c.classList.remove('active'));
    if (chipEl) chipEl.classList.add('active');
    state.activeKorea = idx;
    const title = raceLabel(race);
    const sheet = state.lastSheets[title];
    if (!sheet || !(sheet.horses || []).length) {
      // 서버가 아직 못 읽었거나 비어있음 → 재추출 시도
      await reextractKoreaRace(idx, null);
      return;
    }
    state.activeKoreaCtx = { idx, title, race, sheetHorses: sheet.horses };
    const report = state.lastReports[title];
    if (report) {
      await renderKoreaRaceUI(report);   // 저장된 리포트 즉시 렌더(추가 과금 없음)
    } else {
      await runKoreaAnalysis();           // 리포트가 없으면 클라이언트 분석
    }
  }

  /** 서버 재추출: ±1 페이지 보정 후 해당 경주만 다시 추출·분석 */
  async function reextractKoreaRace(idx, page) {
    const race = state.koreaRaces[idx]; if (!race) return;
    const title = raceLabel(race);
    try {
      showLoading(`${title} 서버 재추출 중...`);
      const r = await fetch('/api/korea/reextract', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ idx, page }),
      });
      const d = await r.json();
      hideLoading();
      if (d.error) {
        if (d.summaryPage) race.summaryPage = d.summaryPage;
        renderPageControl(idx);
        toast(d.error); return;
      }
      const rc = d.race;
      race.summaryPage = rc.summaryPage;
      state.lastSheets[title] = { horses: rc.horses, distance: race.distance };
      state.lastReports[title] = rc.report;
      state.activeKoreaCtx = { idx, title, race, sheetHorses: rc.horses };
      await renderKoreaRaceUI(rc.report);
    } catch (err) { hideLoading(); toast('재추출 실패: ' + err.message); }
  }

  /** [2번] 마체중 입력값을 출전마에 병합 */
  function applyWeights(title, horses) {
    const wmap = state.horseWeights[title] || {};
    return horses.map((h) => {
      const w = wmap[h.horseNum];
      if (w && typeof w.cur === 'number') {
        return { ...h, bodyWeight: w.cur, prevWeight: (typeof w.prev === 'number' ? w.prev : null),
          weightDelta: (typeof w.prev === 'number' ? w.cur - w.prev : null) };
      }
      return h;
    });
  }

  /** 마체중·환경 반영 재분석(클라이언트 /api/analyze 호출) → 렌더 */
  async function runKoreaAnalysis() {
    const ctx = state.activeKoreaCtx; if (!ctx) return;
    const { title, race, sheetHorses } = ctx;
    try {
      showLoading(`${title} BMED 분석 중...`);
      const horses = applyWeights(title, sheetHorses);
      const report = await Analysis.analyzeRace(
        { raceNo: race.raceNo, raceTitle: title, horses, condition: state.raceCondition, distance: race.distance }, state.jockeyStats);
      state.lastReports[title] = report;
      await renderKoreaRaceUI(report);
      hideLoading();
    } catch (err) { hideLoading(); toast('분석 실패: ' + err.message); }
  }

  /** 리포트 + 부가 패널(전적점수·배당·마체중·푸터) 렌더 — 서버/클라이언트 공용 */
  async function renderKoreaRaceUI(report) {
    const ctx = state.activeKoreaCtx; if (!ctx) return;
    const { idx, title, race, sheetHorses } = ctx;
    renderReport('#koreaReport', report, '한국', title);
    renderPageControl(idx);
    let scored = null;
    try { scored = await renderFormScores(race, { horses: sheetHorses }); } catch (e) { console.warn('전적 점수 패널 실패:', e); }
    // [통합분석] 전적 초안 후 배당판 자동 연결 감시 시작(전적 40% + 배당 60%)
    try { await wireKoreaOdds(title, race, scored || []); } catch (e) { console.warn('배당 연결 실패:', e); }
    renderWeightPanel(title, sheetHorses);
    renderKoreaFooter(idx, title, race);   // [기능1·2] 경주 이동 + 결과입력 버튼
    refreshRaceChipStatus();               // [기능3] 진행상황 칩 갱신 (이 경주 → ✅)
  }

  /** [기능1·2] 리포트 하단 푸터: [결과 입력] + [← 이전][서울 3R / 전체 13R][다음 →] */
  function renderKoreaFooter(idx, title, race) {
    const host = $('#koreaReport');
    const total = state.koreaRaces.length;
    const hasPrev = idx > 0;
    const hasNext = idx < total - 1;
    const posText = `${esc(race.venue || '')} ${race.raceNo}R / 전체 ${total}R`;

    const foot = document.createElement('div');
    foot.className = 'panel-card korea-footer';
    foot.innerHTML = `
      <div class="foot-actions">
        <button class="btn btn-primary" id="footResultBtn">📝 결과 입력</button>
      </div>
      <div class="foot-nav">
        ${hasPrev ? '<button class="btn" id="footPrev">← 이전 경주</button>' : '<span class="foot-spacer"></span>'}
        <span class="foot-pos">${posText}</span>
        ${hasNext ? '<button class="btn btn-primary" id="footNext">다음 경주 →</button>' : '<span class="foot-spacer"></span>'}
      </div>`;
    host.appendChild(foot);

    foot.querySelector('#footResultBtn').addEventListener('click', () => openResultFor(title));
    const chips = $$('#koreaRaceList .race-chip');
    if (hasPrev) foot.querySelector('#footPrev').addEventListener('click', () => analyzeKoreaRace(idx - 1, chips[idx - 1]));
    if (hasNext) foot.querySelector('#footNext').addEventListener('click', () => analyzeKoreaRace(idx + 1, chips[idx + 1]));
  }

  /** [기능2] 결과기록 탭으로 이동하며 해당 경주 블록에 포커스 (경주명·추천·날짜는 자동 채워짐) */
  function openResultFor(title) {
    state.pendingResultTitle = title;
    activateTab('result'); // 탭 버튼 클릭 → renderResultForm() 실행 → pendingResultTitle 소비
  }

  /** [2번] 마번별 마체중 변동 입력 패널 */
  function renderWeightPanel(title, horses) {
    state.horseWeights[title] = state.horseWeights[title] || {};
    const wmap = state.horseWeights[title];
    const rows = horses.slice().sort((a, b) => a.horseNum - b.horseNum).map((h) => {
      const w = wmap[h.horseNum] || {};
      return `<tr data-no="${h.horseNum}">
        <td>${h.horseNum}</td><td>${esc(h.horseName || '')}</td>
        <td><input type="number" step="1" class="cfg-input w-cur" style="width:84px" value="${w.cur != null ? w.cur : ''}" placeholder="현재" /></td>
        <td><input type="number" step="1" class="cfg-input w-prev" style="width:84px" value="${w.prev != null ? w.prev : ''}" placeholder="전회" /></td>
        <td class="w-delta">-</td>
      </tr>`;
    }).join('');
    const el = document.createElement('div');
    el.className = 'panel-card';
    el.innerHTML = `<h3>⚖️ 마체중 변동 입력 (kg)</h3>
      <p class="hint">현재 + 전회 마체중 입력 → 변동 자동 계산. ±10kg 🟡 경고 / ±20kg 🔴 위험. [반영 재분석] 시 분석 프롬프트에 포함됩니다.</p>
      <table class="data-table"><thead><tr><th>마번</th><th>마명</th><th>현재 마체중</th><th>전회 마체중</th><th>변동</th></tr></thead>
        <tbody>${rows}</tbody></table>
      <button class="btn btn-primary" id="weightReanalyzeBtn" style="margin-top:8px">⚖️ 마체중 반영 재분석</button>`;
    $('#koreaReport').appendChild(el);

    const recompute = () => {
      el.querySelectorAll('tr[data-no]').forEach((tr) => {
        const no = +tr.dataset.no;
        const cur = parseFloat(tr.querySelector('.w-cur').value);
        const prev = parseFloat(tr.querySelector('.w-prev').value);
        wmap[no] = { cur: isNaN(cur) ? null : cur, prev: isNaN(prev) ? null : prev };
        const cell = tr.querySelector('.w-delta');
        if (!isNaN(cur) && !isNaN(prev)) {
          const d = cur - prev, ad = Math.abs(d);
          const badge = ad >= 20 ? '🔴 위험' : ad >= 10 ? '🟡 경고' : '';
          const color = ad >= 20 ? 'var(--red)' : ad >= 10 ? 'var(--accent-2)' : 'var(--text)';
          cell.innerHTML = `<b style="color:${color}">${d >= 0 ? '+' : ''}${d}kg ${badge}</b>`;
        } else { cell.textContent = '-'; }
      });
    };
    el.querySelectorAll('.w-cur,.w-prev').forEach((inp) => inp.addEventListener('input', recompute));
    recompute();
    el.querySelector('#weightReanalyzeBtn').addEventListener('click', () => runKoreaAnalysis());
  }

  /** 페이지 ±1 보정 컨트롤을 리포트 상단에 삽입 */
  function renderPageControl(idx) {
    const race = state.koreaRaces[idx];
    const bar = document.createElement('div');
    bar.className = 'panel-card';
    bar.style.cssText = 'display:flex;align-items:center;gap:10px';
    bar.innerHTML = `<span class="hint">분석 페이지: <b>p${race.summaryPage}</b> (자동감지)</span>
      <button class="btn btn-small" id="pgPrev">← p${race.summaryPage - 1}</button>
      <button class="btn btn-small" id="pgNext">p${race.summaryPage + 1} →</button>
      <span class="hint">감지가 1페이지 어긋나면 보정하세요</span>`;
    const host = $('#koreaReport');
    host.insertBefore(bar, host.firstChild);
    bar.querySelector('#pgPrev').addEventListener('click', () => shiftRacePage(idx, -1));
    bar.querySelector('#pgNext').addEventListener('click', () => shiftRacePage(idx, +1));
  }

  function shiftRacePage(idx, delta) {
    const race = state.koreaRaces[idx];
    const np = race.summaryPage + delta;
    if (np < 1) { toast('페이지 범위를 벗어났습니다.'); return; }
    reextractKoreaRace(idx, np);   // 서버가 해당 페이지를 재렌더·재추출
  }

  // ---------- 일본경마 (배당판 캡처 + 전적표 업로드 통합) ----------
  function initJapanRace() {
    // [개편] 배당판 캡처 폐지 → 전적표 이미지 업로드만. 배당은 Chrome 확장 실시간 연동.
    $('#jpFormFileBtn').addEventListener('click', () => $('#jpFormInput').click());
    $('#jpFormInput').addEventListener('change', () => { if ($('#jpFormInput').files[0]) handleJpForm($('#jpFormInput').files[0]); });
    jpDropOnly($('#jpFormSlot'), handleJpForm);
    $('#jpAnalyzeBtn').addEventListener('click', analyzeJp);
  }

  /** 슬롯 위 드롭만 처리(클릭은 버튼이 담당) */
  function jpDropOnly(slot, onFile) {
    ['dragover', 'dragenter'].forEach((ev) => slot.addEventListener(ev, (e) => { e.preventDefault(); slot.classList.add('dragover'); }));
    ['dragleave', 'drop'].forEach((ev) => slot.addEventListener(ev, (e) => { e.preventDefault(); slot.classList.remove('dragover'); }));
    slot.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f && f.type.match(/image\//)) onFile(f); });
  }

  /** 캔버스 → 다운스케일 JPEG 이미지 블록 */
  function canvasToBlock(canvas) {
    const maxEdge = 1600;
    const scale = Math.min(1, maxEdge / Math.max(canvas.width, canvas.height));
    const out = document.createElement('canvas');
    out.width = Math.max(1, Math.round(canvas.width * scale));
    out.height = Math.max(1, Math.round(canvas.height * scale));
    out.getContext('2d').drawImage(canvas, 0, 0, out.width, out.height);
    const data = out.toDataURL('image/jpeg', 0.9).split(',')[1];
    return { type: 'image', source: { type: 'base64', media_type: 'image/jpeg', data } };
  }

  function setJpPreview(imgSel, block) {
    const im = $(imgSel);
    im.src = `data:${block.source.media_type};base64,${block.source.data}`;
    im.classList.remove('hidden');
  }

  function setJpStatus(kind, ok) {
    const el = $('#jpFormStatus'); if (!el) return;
    el.textContent = ok ? '✅ 준비됨' : '⬜ 미설정';
    el.classList.toggle('ready', ok);
    $('#jpFormFileBtn').textContent = ok ? '📁 전적표 교체' : '📁 전적표 업로드';
  }

  function updateJpReady() {
    const ok = !!state.jpForm;
    $('#jpAnalyzeBtn').disabled = !ok;
    $('#jpHint').textContent = ok ? '준비 완료 — [전적 분석]을 누르세요' : '전적표를 업로드하세요';
  }

  async function handleJpForm(file) {
    state.jpForm = await Analysis.fileToImageBlock(file);
    setJpPreview('#jpFormPreview', state.jpForm);
    setJpStatus('form', true); updateJpReady();
  }

  async function analyzeJp() {
    if (!state.jpForm) { toast('전적표를 업로드하세요'); return; }
    try {
      showLoading('일본경마 전적 분석 중...');
      const rep = await Analysis.analyzeJapanRace(null, state.jpForm);   // [개편] 전적만 분석(배당은 실시간 연동)
      state.lastReports['일본경마'] = rep;
      renderJapanReport('#jpReport', rep);
      hideLoading();
      startJapanOddsWatch();   // 분석 후 실시간 배당 연동 시작
    } catch (err) { hideLoading(); toast('분석 실패: ' + err.message); }
  }

  /** 일본경마 병합 리포트 (불일치 경고 + 배당 + 등급 + 베팅) */
  function renderJapanReport(sel, rep) {
    const el = $(sel); el.innerHTML = '';
    if (rep.mismatch) {
      add(el, 'bet-box', `<h3>⚠️ 경주 불일치</h3>
        <div class="bet-line"><span class="bet-type" style="color:var(--red)">●</span>
        <span>${esc(rep.mismatchNote || '배당판과 전적표 경주가 다릅니다')}</span></div>
        <p class="hint">같은 경주의 배당판/전적표를 올렸는지 확인하세요.</p>`);
      return;
    }
    add(el, 'panel-card', `<h2>일본경마 병합 분석</h2><p class="hint">${esc(rep.raceSummary || '')}</p>`);

    const al = rep.oddsAlerts || [];
    add(el, 'bet-box', `<h3>🚨 이상배당</h3>` +
      (al.length ? al.map((a) => `<div class="bet-line"><span class="bet-type" style="color:var(--red)">●</span><span>${esc(a)}</span></div>`).join('')
        : `<p class="hint">감지된 이상 배당 없음</p>`));

    const gp = rep.grade_picks || {};
    const gc = ['A', 'B', 'C', 'D'].map((g) => {
      const p = gp[g] || {};
      return `<div class="stat-card" style="text-align:left">
        <div><span class="grade-badge grade-${g}">${g}</span> <strong>${esc(p.name || '-')}</strong>
        <span class="meta">${p.no ? p.no + '번' : ''}</span></div>
        <div class="label" style="margin-top:6px">비중 ${GRADE_WEIGHT[g]}%</div>
        <div class="reason" style="font-size:12px;margin-top:4px">${esc(p.reason || '')}</div></div>`;
    }).join('');
    add(el, 'panel-card', `<h3>등급 분류 (A/B/C/D · 45:28:17:10)</h3><div class="stat-grid">${gc}</div>`);

    const horses = (rep.horses || []).slice().sort((a, b) => b.score - a.score);
    const top = horses[0] ? horses[0].no : null;
    const wrap = document.createElement('div'); wrap.className = 'report-area';
    horses.forEach((h) => {
      const card = document.createElement('div');
      card.className = 'horse-card' + (h.no === top ? ' top-pick' : '');
      const badge = h.grade ? `<span class="grade-badge grade-${h.grade}">${h.grade}</span> ` : '';
      card.innerHTML = `
        <div class="horse-head"><div>${badge}<span class="horse-no">${h.no}</span><strong>${esc(h.name)}</strong></div><div class="score">${h.score}</div></div>
        <div class="meta">기수: ${esc(h.jockey || '')} · 배당: ${esc(h.odds || '-')} ${h.abnormal ? '⚠️급락/이상' : ''}</div>
        <div class="reason">${esc(h.reason || '')}</div>`;
      wrap.appendChild(card);
    });
    el.appendChild(wrap);

    const bd = rep.betting_recommend || {};
    const lines = (arr, label) => (arr || []).map((b) =>
      `<div class="bet-line"><span><span class="bet-type">${label}</span> ${(b.combo || []).join('-')}</span>
       <span>신뢰도 ${b.confidence}% · ${esc(b.note || '')}</span></div>`).join('') ||
      `<div class="bet-line"><span class="bet-type">${label}</span><span>추천 없음</span></div>`;
    add(el, 'bet-box', `<h3>💰 베팅 추천 (복승 / 삼복승 · 단승 없음)</h3>${lines(bd.quinella, '복승')}${lines(bd.trifecta, '삼복승')}`);
    if (rep.analysis) add(el, 'panel-card', `<h3>종합 분석</h3><p class="hint">${esc(rep.analysis)}</p>`);
  }

  // ---------- 배당판 캡처 & 이상배당 ----------
  const cap = { canvas: null, dragging: false, sx: 0, sy: 0, rect: null, stream: null, video: null, autoAnalyze: false }; // rect: 캔버스(표시) 좌표

  // [개편] 배당판 캡처(화면캡처·Alt+C·이미지 업로드) 전면 폐지 → 배당은 Chrome 확장 실시간 수집으로 통일.
  //   여기서는 통계 탭에 남은 '배당 변동 히스토리 & 자동학습' 대시보드 버튼만 연결한다.
  //   (기존 캡처 관련 함수 captureScreen/dualCapture/captureTriple 등은 미사용 상태로 남겨둠 = 주석처리 효과)
  function initOdds() {
    { const b = $('#tripleBudget'); if (b) b.addEventListener('input', () => { if (_lastTripleAnalyze) renderTripleAnalyze(_lastTripleAnalyze); }); }
    { const b = $('#histRefreshBtn'); if (b) b.addEventListener('click', () => { loadHistoryList(); loadLearningStats(); }); } // [5번] 히스토리
    { const b = $('#learnStatsBtn'); if (b) b.addEventListener('click', loadLearningStats); }
    initBulkResult();   // [일괄 결과 등록]
  }

  /** 확장(timer.js 릴레이)에 결과 URL fetch를 요청하고 HTML을 받는다. 6초 내 응답 없으면 실패. */
  function fetchResultViaExtension(url) {
    return new Promise((resolve) => {
      const reqId = 'br_' + Date.now() + '_' + Math.random().toString(36).slice(2, 7);
      let done = false;
      const onMsg = (e) => {
        if (e.source !== window) return;
        const d = e.data;
        if (!d || d.source !== 'bmed-timer' || d.type !== 'FETCH_RESULT_HTML_ACK' || d.reqId !== reqId) return;
        done = true; window.removeEventListener('message', onMsg);
        resolve({ ok: !!d.ok, html: d.html || '', error: d.error || '' });
      };
      window.addEventListener('message', onMsg);
      try { window.postMessage({ source: 'bmed-analyzer', type: 'FETCH_RESULT_HTML', url, reqId }, '*'); }
      catch (ex) { window.removeEventListener('message', onMsg); resolve({ ok: false, error: String(ex) }); return; }
      setTimeout(() => { if (!done) { window.removeEventListener('message', onMsg); resolve({ ok: false, error: '확장 응답 시간초과(확장 미설치/미로그인?)' }); } }, 6000);
    });
  }

  /** [일괄 결과 등록] 결과 페이지 URL/HTML → 서버 일괄 파싱·매칭·학습 → 요약 표시 */
  function initBulkResult() {
    const toggle = $('#bulkResultToggle'); if (!toggle) return;
    const panel = $('#bulkResultPanel');
    { const st = $('#bulkStake'); if (st) st.value = _defaultStake(); }   // [#5] 기본 투자금액 반영
    toggle.addEventListener('click', () => {
      const open = panel.style.display !== 'none';
      panel.style.display = open ? 'none' : 'block';
      toggle.textContent = open ? '📋 일괄 등록 열기' : '📋 일괄 등록 닫기';
    });
    const run = async (payload) => {
      const box = $('#bulkResultSummary');
      const stake = parseInt(($('#bulkStake') && $('#bulkStake').value) || '1000', 10) || 1000;
      if (stake > 0) localStorage.setItem('bmed_default_stake', String(stake));   // [#5] 기본값 기억
      box.innerHTML = '<p class="hint">⏳ 파싱·매칭·학습 중…</p>';
      let d;
      try {
        d = await (await fetch('/api/results/bulk', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(Object.assign({ stake }, payload)),
        })).json();
      } catch (e) { box.innerHTML = `<p class="err">요청 실패: ${esc(e.message)}</p>`; return; }
      if (d.error) {
        box.innerHTML = `<p class="err">${esc(d.error)}</p>`;
        if (d.needPaste) $('#bulkResultHtml').focus();
        return;
      }
      renderBulkSummary(d);
      // 통계·히스토리 자동 갱신
      try { loadLearningStats(); loadHistoryList(); } catch (_) { /* */ }
    };
    { const b = $('#bulkResultLoad'); if (b) b.addEventListener('click', async () => {
      const url = ($('#bulkResultUrl').value || '').trim();
      const box = $('#bulkResultSummary');
      if (!url) { box.innerHTML = '<p class="err">URL을 입력하세요.</p>'; return; }
      // [확장 경유] 로그인 세션을 가진 확장에 fetch 요청 → HTML 받으면 서버로 파싱 전송.
      box.innerHTML = '<p class="hint">⏳ 확장(로그인 세션)으로 결과 페이지 가져오는 중…</p>';
      const ext = await fetchResultViaExtension(url);
      if (ext && ext.ok && ext.html) {
        run({ html: ext.html });
        return;
      }
      // [폴백] 확장 미설치/실패 → 서버 직접 fetch 시도(공개 페이지만 성공)
      box.innerHTML = `<p class="hint">확장 경유 실패(${esc((ext && ext.error) || '응답 없음')}) → 서버 직접 로드 시도…</p>`;
      run({ url });
    }); }
    { const b = $('#bulkResultParse'); if (b) b.addEventListener('click', () => {
      const html = $('#bulkResultHtml').value || '';
      if (!html.trim()) { $('#bulkResultSummary').innerHTML = '<p class="err">결과표 HTML을 붙여넣으세요.</p>'; return; }
      run({ html });
    }); }
  }

  /** [보완#1] 경주별 손익 미리보기 — 서버 _recompute_pnl 과 동일 규칙(클라 미리보기). */
  function _bulkRowPnl(m, stake, payout) {
    stake = (stake > 0) ? stake : 1000;
    if (payout !== '' && payout != null && !isNaN(payout)) return Math.round(payout - stake);
    const p = m.payouts || {};
    if (m.quinella_hit && p.quinella) return Math.round((p.quinella - 1) * stake);
    if (m.trifecta_hit && p.trifecta) return Math.round((p.trifecta - 1) * stake);
    if (m.had_bet) return -stake;
    return 0;
  }

  /** 일괄 등록 결과 요약: 경주별 투자금/실수령 편집 → 정확 손익 조정 + 매칭실패 목록 */
  function renderBulkSummary(d) {
    const box = $('#bulkResultSummary'); if (!box) return;
    const profit = d.profit || 0;
    const pnlTxt = profit >= 0
      ? `<span style="color:#38d39f">수익: +${profit.toLocaleString()}원</span>`
      : `<span style="color:#ff6b6b">손실: ${profit.toLocaleString()}원</span>`;
    const matched = d.matched || [];
    const oddsHint = (m) => {
      const p = m.payouts || {};
      if (m.quinella_hit && p.quinella) return `복승 ${p.quinella}배`;
      if (m.trifecta_hit && p.trifecta) return `삼복승 ${p.trifecta}배`;
      return '';
    };
    const matchedRows = matched.map((m, i) => {
      const tag = m.quinella_hit ? '복승 적중' : m.trifecta_hit ? '삼복승 적중' : m.won ? '적중' : '미적중';
      const color = m.won ? '#38d39f' : '#8a94a6';
      const stake = m.stake || 1000;
      const payVal = (m.payout_actual != null) ? m.payout_actual : '';
      return `<tr data-i="${i}">
        <td>${esc(m.raceKey)}</td>
        <td style="text-align:center">${(m.top3 || []).join('-')}</td>
        <td style="text-align:center;color:${color};font-weight:700">${tag}<br><span class="hint" style="font-weight:400">${oddsHint(m)}</span></td>
        <td style="text-align:right"><input class="cfg-input bulk-stake" type="number" min="0" step="100" value="${stake}" style="width:90px"></td>
        <td style="text-align:right"><input class="cfg-input bulk-payout" type="number" min="0" step="100" value="${payVal}" placeholder="${m.won ? '실수령' : '0'}" style="width:100px"></td>
        <td class="bulk-pnl" style="text-align:right;font-weight:700"></td></tr>`;
    }).join('');
    const unmatched = (d.unmatched || []).map((u) =>
      `<li>${esc(u.area || '')} ${esc(String(u.round || ''))} · 착순 ${(u.top3 || []).join('-')} <span class="hint">(분석 경주 없음)</span></li>`).join('');
    box.innerHTML = `
      <div class="bet-box">
        <h3 style="margin-top:0">✅ 결과 등록 완료</h3>
        <div style="font-size:15px;line-height:1.9">
          등록 <b>${d.registered || 0}</b>건 (파싱 ${d.parsedRows || 0}행) · 적중 <b style="color:#38d39f">${d.hits || 0}</b>건 · ${pnlTxt}
          <span class="hint">(배당배수=결과페이지 실제 확정배당 · 투자금 기본 ${(d.stake || 1000).toLocaleString()}원)</span>
        </div>
      </div>
      ${matchedRows ? `<table class="data-table" style="width:100%;margin-top:8px"><thead><tr>
        <th>경주</th><th style="text-align:center">1~3착</th><th style="text-align:center">판정</th>
        <th style="text-align:right">투자(원)</th><th style="text-align:right">실수령(원)</th><th style="text-align:right">손익</th>
      </tr></thead><tbody>${matchedRows}</tbody></table>
      <div class="cfg-row" style="margin-top:8px;justify-content:space-between;align-items:center">
        <span id="bulkAdjSum" class="hint"></span>
        <button id="bulkAdjSave" class="btn btn-primary">💾 조정 저장 → 학습 반영</button>
      </div>
      <p class="hint" style="margin:4px 0 0">경주별 실제 투자금·실수령 배당금을 입력하면 정확한 손익으로 학습 통계에 반영됩니다(공란이면 확정배당 추정).</p>` : ''}
      ${unmatched ? `<div class="panel-card" style="margin-top:8px"><h3 style="margin-top:0">⚠️ 수동 확인 필요 (매칭 실패 ${d.unmatched.length}건)</h3>
        <ul style="margin:4px 0 0 18px">${unmatched}</ul>
        <p class="hint">해당 경주는 분석(배당 수집) 기록이 없어 자동 매칭되지 않았습니다. 위 [경주 결과 입력]에서 직접 등록하세요.</p></div>` : ''}`;
    if (!matched.length) return;

    const recalc = () => {
      let sum = 0;
      box.querySelectorAll('tr[data-i]').forEach((tr) => {
        const m = matched[+tr.dataset.i];
        const st = parseInt(tr.querySelector('.bulk-stake').value, 10) || 0;
        const pvRaw = tr.querySelector('.bulk-payout').value;
        const pv = (pvRaw === '' ? '' : parseInt(pvRaw, 10));
        const pnl = _bulkRowPnl(m, st, pv);
        sum += pnl;
        const cell = tr.querySelector('.bulk-pnl');
        cell.textContent = (pnl >= 0 ? '+' : '') + pnl.toLocaleString() + '원';
        cell.style.color = pnl >= 0 ? '#38d39f' : '#ff6b6b';
      });
      const el = $('#bulkAdjSum');
      if (el) el.innerHTML = `조정 손익 합계: <b style="color:${sum >= 0 ? '#38d39f' : '#ff6b6b'}">${(sum >= 0 ? '+' : '') + sum.toLocaleString()}원</b>`;
    };
    box.querySelectorAll('.bulk-stake,.bulk-payout').forEach((inp) => inp.addEventListener('input', recalc));
    recalc();

    $('#bulkAdjSave').addEventListener('click', async () => {
      const items = [];
      box.querySelectorAll('tr[data-i]').forEach((tr) => {
        const m = matched[+tr.dataset.i];
        const st = parseInt(tr.querySelector('.bulk-stake').value, 10) || 0;
        const pvRaw = tr.querySelector('.bulk-payout').value;
        const it = { raceKey: m.raceKey, stake: st };
        if (pvRaw !== '') it.payout = parseInt(pvRaw, 10);
        items.push(it);
      });
      const btn = $('#bulkAdjSave'); btn.disabled = true; btn.textContent = '저장 중…';
      try {
        const r = await (await fetch('/api/results/adjust', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ items }),
        })).json();
        if (r.error) { toast('조정 실패: ' + r.error); return; }
        toast(`✅ ${r.updated ? r.updated.length : 0}건 조정 반영 · 조정분 손익 ${(r.net >= 0 ? '+' : '') + (r.net || 0).toLocaleString()}원`);
        loadLearningStats(); loadHistoryList();
      } catch (e) { toast('조정 실패: ' + e.message); }
      finally { btn.disabled = false; btn.textContent = '💾 조정 저장 → 학습 반영'; }
    });
  }

  function capPos(e) {
    const cv = $('#capCanvas'); const r = cv.getBoundingClientRect();
    return { x: (e.clientX - r.left) * (cv.width / r.width), y: (e.clientY - r.top) * (cv.height / r.height) };
  }

  /** 화면 공유 스트림을 한 번만 허가받아 재사용 (Alt+C 재캡처 시 팝업 없이) */
  async function ensureCaptureStream() {
    if (cap.stream && cap.stream.active) return cap.stream;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
      throw new Error('이 브라우저는 화면 캡처(getDisplayMedia)를 지원하지 않습니다.');
    }
    const stream = await navigator.mediaDevices.getDisplayMedia({ video: { displaySurface: 'monitor' } });
    cap.stream = stream;
    stream.getVideoTracks()[0].addEventListener('ended', () => { cap.stream = null; });
    return stream;
  }

  /** 현재 스트림에서 한 프레임을 풀해상도 캔버스로 추출 */
  async function grabFrame() {
    const stream = await ensureCaptureStream();
    if (!cap.video) { cap.video = document.createElement('video'); cap.video.muted = true; }
    if (cap.video.srcObject !== stream) { cap.video.srcObject = stream; await cap.video.play(); }
    await new Promise((r) => requestAnimationFrame(r));
    const full = document.createElement('canvas');
    full.width = cap.video.videoWidth; full.height = cap.video.videoHeight;
    full.getContext('2d').drawImage(cap.video, 0, 0, full.width, full.height);
    return full;
  }

  async function captureScreen() {
    cap.autoAnalyze = false; // 버튼 캡처는 확인 바를 거침
    try { setCaptured(await grabFrame()); notify('✅ 캡처 완료'); }
    catch (e) { notify('화면 캡처 취소/실패: ' + e.message, false); }
  }

  /** [4단계] Alt+C 원클릭: 배당판 탭 전환 → 캡처 → 자동 크롭 → 자동 분석(확인 생략) */
  async function hotkeyCapture() {
    activateTab('japan');
    try {
      cap.autoAnalyze = true;          // 자동 크롭 성공 시 확인 없이 바로 분석
      setCaptured(await grabFrame());  // setCaptured → autoCrop → (감지되면 runCropAnalysis)
      notify('✅ 캡처 완료 — 자동 분석');
    } catch (e) { cap.autoAnalyze = false; notify('캡처 실패: ' + e.message, false); }
  }

  /** 탭 프로그램적 전환 */
  function activateTab(name) {
    const btn = document.querySelector(`.tab-btn[data-tab="${name}"]`);
    if (btn && !btn.classList.contains('active')) btn.click();
  }

  // ---------- [경주 자동 업데이트] 확장 수집 경주 → 상단 표시 + 새로고침 ----------
  //  확장(background)이 30초마다 배당판에서 수집 → 서버 latest 에 최신 raceKey 저장.
  //  분석기는 이 바에서 (1) 새로고침 버튼으로 즉시, (2) 30초마다 자동으로 최신 경주를 조회해
  //  상단에 "제주 3경주" 처럼 표시하고, 경주가 바뀌면 화면을 자동 갱신한다. (기존 기능은 유지)
  let _rrTimer = null, _rrLastRk = null;

  function initRaceRefresh() {
    const btn = $('#rrRefreshBtn');
    if (btn) btn.addEventListener('click', () => refreshCurrentRace(true));
    refreshCurrentRace(false);
    if (_rrTimer) clearInterval(_rrTimer);
    _rrTimer = setInterval(() => refreshCurrentRace(false), 30000);   // [3번] 30초마다 경주 변경 확인
  }

  /** 확장이 마지막으로 수집한 경주(server latest) 조회 → 상단 표시 + (변경/수동 시) 화면 갱신 */
  async function refreshCurrentRace(manual) {
    const label = $('#rrCurrentRace'), status = $('#rrStatus');
    if (manual) {
      if (status) status.textContent = '확장에 즉시 수집 요청…';
      nudgeExtensionCollect();                       // [1번] 확장에서 현재 경주 즉시 수집
      await new Promise((r) => setTimeout(r, 1200));  // 확장 수집·서버 저장 잠깐 대기
    }
    let rk = null;
    try { const d = await (await fetch('/api/current_race')).json(); rk = d && d.raceKey; } catch (_) { /* */ }
    if (!rk) {  // 폴백: 구 엔드포인트
      try { const d = await (await fetch('/api/odds/triple/latest')).json(); rk = d && d.raceKey; } catch (_) { /* */ }
    }
    if (!rk) {
      if (label) label.textContent = '— (수집된 경주 없음)';
      if (status) status.textContent = manual ? '확장 [전체 자동 수집]을 먼저 실행하세요.' : '';
      return;
    }
    if (label) label.textContent = rk;
    const changed = rk !== _rrLastRk;

    if (manual) {
      // [수동 새로고침] 완전 갱신: 화면 업데이트 + 배당 타임라인 초기화 + 성공 메시지
      _rrLastRk = rk;
      resetOddsTimeline();                            // [4번] 배당 타임라인 초기화(새 경주 시작)
      refreshActiveView(rk);                          // [3번] 분석기 화면 자동 업데이트
      if (status) status.textContent = '✅ 업데이트 완료';
      notify(`✅ ${rk} 업데이트 완료`, true);          // 예: "✅ 제주 3경주 업데이트 완료"
      return;
    }
    // [자동 감지·30초] 경주가 바뀌면 자동 전환하지 않고 새로고침을 유도하는 알림만 표시
    if (changed && _rrLastRk != null) {
      if (status) status.textContent = '🔔 새 경주 감지 — 새로고침 클릭';
      notify(`🔔 새 경주 감지: ${rk} · [🔄 경주 새로고침] 버튼을 클릭하세요`, true);
    }
    _rrLastRk = rk;
  }

  /** [4번] 배당 변동 타임라인 초기화 — 새 경주 시작 시 이전 경주의 누적 변동을 비운다. */
  function resetOddsTimeline() {
    // 일본 타임라인
    state.jpTimeline = [];
    state.jpOddsPrev = new Set();
    { const el = document.getElementById('jpTimeline'); if (el) el.remove(); }
    // 한국 타임라인(현재 활성 경주)
    try {
      const t = _koreaOddsTitle;
      if (t) {
        if (state.koreaTimeline) state.koreaTimeline[t] = [];
        if (state.koreaOddsPrev) state.koreaOddsPrev[t] = new Set();
      }
    } catch (_) { /* */ }
    { const el = document.getElementById('koreaTimeline'); if (el) el.remove(); }
  }

  /** 활성 탭에 맞춰 화면 갱신: 일본=실시간 폴 / 한국=칩 자동 선택 */
  function refreshActiveView(rk) {
    const activeBtn = document.querySelector('.tab-btn.active');
    const tab = activeBtn ? activeBtn.dataset.tab : '';
    if (tab === 'jp') { try { pollJapanOdds(); } catch (_) { /* */ } return; }
    try { autoSelectKoreaRace(rk); } catch (_) { /* */ }
  }

  /** raceKey(예 "2026-07-04 제주 3R" / "제주 3경주")에서 회장·경주번호를 뽑아 한국 칩 자동 선택 */
  function autoSelectKoreaRace(rk) {
    if (!state.koreaRaces || !state.koreaRaces.length) return;
    const s = String(rk);
    const m = s.match(/(\d{1,2})\s*(?:R|경주)/i);
    const no = m ? parseInt(m[1], 10) : null;
    if (no == null) return;
    let idx = state.koreaRaces.findIndex((r) => parseInt(r.raceNo, 10) === no && r.venue && s.includes(r.venue));
    if (idx < 0) idx = state.koreaRaces.findIndex((r) => parseInt(r.raceNo, 10) === no);
    if (idx < 0 || state.activeKorea === idx) return;   // 없거나 이미 선택됨 → 재선택 안 함(감시 리셋 방지)
    const chip = document.querySelector(`#koreaRaceList .race-chip[data-idx="${idx}"]`);
    if (chip) chip.click();
  }

  /** 분석기 페이지 → timer.js(확장) 릴레이: 즉시 수집 트리거(postMessage) */
  function nudgeExtensionCollect() {
    try { window.postMessage({ source: 'bmed-analyzer', type: 'FORCE_COLLECT' }, '*'); } catch (_) { /* */ }
  }

  // ---------- [별도 창] 분석기 팝업 창 열기 + 위치/크기 기억 ----------
  //  배당판을 보면서 분석기도 함께 볼 수 있도록 별도(popup) 창으로 연다.
  //  ※ 브라우저 보안 정책상 웹페이지는 창을 '항상 최상단'으로 강제할 수 없어,
  //    '항상 위' 옵션은 설정만 기억한다(팝업 창 형태 유지). OS/브라우저 자체 기능 필요.
  const POPUP_NAME = 'keibaAnalyzerPopup';
  const POPUP_GEOM_KEY = 'bmed_popupGeom';
  const POPUP_ONTOP_KEY = 'bmed_popupOnTop';

  function _isAnalyzerPopup() {
    try { return window.name === POPUP_NAME || new URLSearchParams(location.search).has('popup'); } catch (_) { return false; }
  }

  function openAnalyzerPopup() {
    // [2번] 확장이 있으면 background 가 '일반 창(type:normal)'으로 연다(포커스 잃어도 안 사라짐).
    //   확장이 ACK 하면 window.open 폴백을 건너뛴다. 확장이 없으면(ACK 없음) window.open 으로 연다.
    let acked = false;
    const onAck = (e) => {
      if (e.source === window && e.data && e.data.source === 'bmed-timer' && e.data.type === 'OPEN_ANALYZER_ACK') acked = true;
    };
    window.addEventListener('message', onAck);
    try { window.postMessage({ source: 'bmed-analyzer', type: 'OPEN_ANALYZER_WINDOW' }, '*'); } catch (_) { /* */ }
    setTimeout(() => {
      window.removeEventListener('message', onAck);
      if (acked) { notify('🪟 분석기를 별도(일반) 창으로 열었습니다', true); return; }
      // 폴백: 확장 미설치 → window.open (일반 창 시도)
      let g = null;
      try { g = JSON.parse(localStorage.getItem(POPUP_GEOM_KEY) || 'null'); } catch (_) { /* */ }
      const w = (g && g.w) || 1200, h = (g && g.h) || 900;
      const x = (g && g.x != null) ? g.x : Math.max(0, Math.round((screen.availWidth - w) / 2));
      const y = (g && g.y != null) ? g.y : Math.max(0, Math.round((screen.availHeight - h) / 2));
      const feats = `resizable=yes,scrollbars=yes,width=${w},height=${h},left=${x},top=${y}`;
      const win = window.open(location.origin + '/?popup=1', POPUP_NAME, feats);
      if (win) { try { win.focus(); } catch (_) { /* */ } notify('🪟 분석기를 별도 창으로 열었습니다', true); }
      else notify('팝업이 차단되었습니다 — 브라우저 팝업 허용을 확인하세요', false);
    }, 350);
  }

  function _savePopupGeom() {
    try {
      const geom = { x: window.screenX, y: window.screenY, w: window.outerWidth, h: window.outerHeight };
      if (geom.w > 200 && geom.h > 200) localStorage.setItem(POPUP_GEOM_KEY, JSON.stringify(geom));
    } catch (_) { /* */ }
  }

  function initPopout() {
    const btn = $('#popoutBtn'), chk = $('#onTopChk');
    if (btn) btn.addEventListener('click', openAnalyzerPopup);
    if (chk) {
      try { chk.checked = localStorage.getItem(POPUP_ONTOP_KEY) === '1'; } catch (_) { /* */ }
      chk.addEventListener('change', () => {
        try { localStorage.setItem(POPUP_ONTOP_KEY, chk.checked ? '1' : '0'); } catch (_) { /* */ }
        if (chk.checked) {
          notify('📌 항상 위: 브라우저는 창 고정을 지원하지 않습니다. Windows에서 창을 클릭 후 [Win+Ctrl+T]로 고정하세요(PowerToys 필요).', true);
        } else {
          notify('📌 항상 위 꺼짐', true);
        }
      });
    }
    // 이 페이지가 '별도 창'이면: 위치/크기를 주기적으로 기억 + 닫힐 때 저장. 별도창 버튼은 숨김.
    if (_isAnalyzerPopup()) {
      if (btn) btn.style.display = 'none';
      _savePopupGeom();
      setInterval(_savePopupGeom, 3000);
      window.addEventListener('beforeunload', _savePopupGeom);
      try { document.title = '📊 경마배당분석기 (별도 창)'; } catch (_) { /* */ }
    }
  }

  /** 비차단 토스트 알림 (alert 대체 — 캡처 흐름을 막지 않음) */
  function notify(msg, ok) {
    let n = $('#capNotice');
    if (!n) { n = document.createElement('div'); n.id = 'capNotice'; document.body.appendChild(n); }
    n.textContent = msg;
    n.className = 'cap-notice ' + (ok === false ? 'err' : 'ok') + ' show';
    clearTimeout(n._t); n._t = setTimeout(() => n.classList.remove('show'), 2600);
  }

  function loadOddsFile(file) {
    cap.autoAnalyze = false; // 파일 업로드는 확인 바를 거침
    const img = new Image();
    img.onload = () => {
      const full = document.createElement('canvas');
      full.width = img.naturalWidth; full.height = img.naturalHeight;
      full.getContext('2d').drawImage(img, 0, 0);
      setCaptured(full);
    };
    img.src = URL.createObjectURL(file);
  }

  function setCaptured(full) {
    cap.canvas = full; cap.rect = null;
    const cv = $('#capCanvas');
    const maxW = 1000;
    const scale = Math.min(1, maxW / full.width);
    cv.width = Math.round(full.width * scale);
    cv.height = Math.round(full.height * scale);
    drawCap();
    $('#capWrap').classList.remove('hidden');
    $('#oddsAnalyzeBtn').disabled = false;
    $('#capHint').textContent = `캡처됨 ${full.width}×${full.height}. 자동 크롭 확인 또는 직접 드래그.`;
    autoCrop(); // [2단계] 자동 영역 감지 + 확인 바
  }

  function drawCap() {
    const cv = $('#capCanvas'); const ctx = cv.getContext('2d');
    ctx.clearRect(0, 0, cv.width, cv.height);
    ctx.drawImage(cap.canvas, 0, 0, cv.width, cv.height);
    if (cap.rect && cap.rect.w > 2) {
      ctx.strokeStyle = '#4f8cff'; ctx.lineWidth = 2;
      ctx.strokeRect(cap.rect.x, cap.rect.y, cap.rect.w, cap.rect.h);
      ctx.fillStyle = 'rgba(79,140,255,.12)';
      ctx.fillRect(cap.rect.x, cap.rect.y, cap.rect.w, cap.rect.h);
    }
  }

  function capturedBlock() {
    const full = cap.canvas; const cv = $('#capCanvas');
    // 원본(또는 선택영역) 소스 사각형
    let sx = 0, sy = 0, sw = full.width, sh = full.height;
    if (cap.rect && cap.rect.w > 5 && cap.rect.h > 5) {
      const s = full.width / cv.width; // 표시→원본 배율
      sx = cap.rect.x * s; sy = cap.rect.y * s; sw = cap.rect.w * s; sh = cap.rect.h * s;
    }
    // 장변 1600px로 다운스케일(API 이미지 한도 회피) + JPEG로 용량 절감
    const maxEdge = 1600;
    const scale = Math.min(1, maxEdge / Math.max(sw, sh));
    const out = document.createElement('canvas');
    out.width = Math.max(1, Math.round(sw * scale));
    out.height = Math.max(1, Math.round(sh * scale));
    out.getContext('2d').drawImage(full, sx, sy, sw, sh, 0, 0, out.width, out.height);
    const data = out.toDataURL('image/jpeg', 0.9).split(',')[1];
    return { type: 'image', source: { type: 'base64', media_type: 'image/jpeg', data } };
  }

  // ---------- [2단계] 배당판 영역 자동 크롭 ----------
  /** 1차원 점수 배열에서 가장 밀도 높은 구간(작은 공백 허용) [s,e) 반환 — 순수 */
  function denseBand(arr, frac, maxGap) {
    const n = arr.length;
    let max = 0;
    for (let i = 0; i < n; i++) if (arr[i] > max) max = arr[i];
    if (max <= 0) return { s: 0, e: n };
    const th = max * frac;
    let best = null, curS = -1, curSum = 0, gap = 0;
    for (let i = 0; i < n; i++) {
      if (arr[i] >= th) {
        if (curS < 0) { curS = i; curSum = 0; }
        curSum += arr[i]; gap = 0;
      } else if (curS >= 0) {
        gap++;
        if (gap > maxGap) {
          const e = i - gap + 1;
          if (!best || curSum > best.sum) best = { s: curS, e, sum: curSum };
          curS = -1; curSum = 0; gap = 0;
        } else { curSum += arr[i]; }
      }
    }
    if (curS >= 0 && (!best || curSum > best.sum)) best = { s: curS, e: n, sum: curSum };
    return best ? { s: best.s, e: best.e } : { s: 0, e: n };
  }

  /** 그레이스케일(Uint8 w*h)에서 격자(배당 매트릭스) 영역 bbox 감지 — 순수 */
  function detectMatrixRegion(gray, w, h) {
    const colScore = new Float64Array(w);
    const rowScore = new Float64Array(h);
    const TH = 24;
    for (let y = 0; y < h; y++) {
      for (let x = 1; x < w; x++) {
        if (Math.abs(gray[y * w + x] - gray[y * w + x - 1]) > TH) { colScore[x]++; rowScore[y]++; }
      }
    }
    for (let x = 0; x < w; x++) {
      for (let y = 1; y < h; y++) {
        if (Math.abs(gray[y * w + x] - gray[(y - 1) * w + x]) > TH) { colScore[x]++; rowScore[y]++; }
      }
    }
    const bx = denseBand(colScore, 0.15, Math.max(4, Math.round(w * 0.03)));
    const by = denseBand(rowScore, 0.15, Math.max(6, Math.round(h * 0.05))); // 격자 사이 공백 더 관대하게
    return { x: bx.s, y: by.s, w: Math.max(0, bx.e - bx.s), h: Math.max(0, by.e - by.s) };
  }

  /** 캡처 캔버스에서 자동 크롭 영역을 감지해 cap.rect(표시좌표) 설정 + 확인 바 표시 */
  function autoCrop() {
    const full = cap.canvas;
    if (!full) return;
    const dw = Math.min(240, full.width);
    const dh = Math.max(1, Math.round(full.height * dw / full.width));
    const tmp = document.createElement('canvas'); tmp.width = dw; tmp.height = dh;
    tmp.getContext('2d').drawImage(full, 0, 0, dw, dh);
    const px = tmp.getContext('2d').getImageData(0, 0, dw, dh).data;
    const gray = new Uint8Array(dw * dh);
    for (let i = 0; i < dw * dh; i++) gray[i] = (px[i * 4] * 0.299 + px[i * 4 + 1] * 0.587 + px[i * 4 + 2] * 0.114) | 0;

    const r = detectMatrixRegion(gray, dw, dh);
    const area = (r.w * r.h) / (dw * dh);
    const cv = $('#capCanvas');
    if (!r.w || !r.h || area < 0.04 || area > 0.96) {
      cap.rect = null; drawCap();
      if (cap.autoAnalyze) { cap.autoAnalyze = false; runCropAnalysis(); } // 자동 캡처: 감지 실패해도 전체 프레임으로 진행
      else showCropConfirm(false);
      return;
    }
    cap.rect = { x: r.x * cv.width / dw, y: r.y * cv.height / dh, w: r.w * cv.width / dw, h: r.h * cv.height / dh };
    drawCap();
    if (cap.autoAnalyze) {            // [4단계] 원클릭: 확인 생략하고 바로 분석
      cap.autoAnalyze = false;
      $('#capConfirm').classList.add('hidden');
      runCropAnalysis();
    } else {
      showCropConfirm(true);
    }
  }

  function showCropConfirm(detected) {
    const bar = $('#capConfirm');
    $('#cropConfirmText').textContent = detected
      ? '🔍 배당판 영역을 자동 감지했습니다. 이 영역이 맞나요?'
      : '자동 감지 실패 — 영역을 직접 드래그하거나 전체로 분석하세요.';
    bar.classList.remove('hidden');
  }

  /** 크롭 확인/자동 크롭 후 → 현재 프레임을 시계열에 누적 */
  async function runCropAnalysis() { await captureAccumulate(); }

  async function runOddsAnalysis() {
    if (!cap.canvas) { toast('먼저 화면을 캡처하거나 이미지를 올리세요.'); return; }
    try {
      showLoading('배당판 분석 중...');
      const rep = await Analysis.analyzeOdds(capturedBlock());
      renderOddsReport(rep);
      hideLoading();
    } catch (err) { hideLoading(); toast('분석 실패: ' + err.message); }
  }

  function renderOddsReport(rep) {
    const el = $('#oddsReport'); el.innerHTML = '';
    const types = (rep.betTypes || []).join(', ') || '미식별';
    add(el, 'panel-card', `<h2>배당 분석</h2>
      <div class="bet-line"><span class="bet-type">인식된 베팅 종류</span><span><b>${esc(types)}</b></span></div>
      <p class="hint">${esc(rep.summary || '')}</p>`);

    const alerts = rep.alerts || [];
    add(el, 'bet-box', `<h3>🚨 이상배당 경고</h3>` +
      (alerts.length ? alerts.map((a) => `<div class="bet-line"><span class="bet-type" style="color:var(--red)">●</span><span>${esc(a)}</span></div>`).join('')
        : `<p class="hint">감지된 이상 배당 없음</p>`));

    // 단승/연승 — 마번별
    const hrows = (rep.horses || []).map((h) =>
      `<tr${h.abnormal ? ' style="background:rgba(255,92,92,.12)"' : ''}>
        <td>${h.no}</td><td>${esc(h.odds || '')}</td><td>${esc(h.trend || '')}</td>
        <td>${h.abnormal ? '⚠️' : ''}</td><td>${esc(h.note || '')}</td></tr>`).join('');
    if ((rep.horses || []).length) {
      add(el, 'panel-card', `<h3>마번별 배당 (단승/연승)</h3>
        <table class="data-table"><thead><tr><th>마번</th><th>배당</th><th>흐름</th><th>이상</th><th>비고</th></tr></thead>
        <tbody>${hrows}</tbody></table>`);
    }

    // 복승/쌍승/삼복승 — 조합별
    const crows = (rep.combos || []).map((c) =>
      `<tr${c.abnormal ? ' style="background:rgba(255,92,92,.12)"' : ''}>
        <td>${esc(c.type || '')}</td><td>${(c.combo || []).join('-')}</td><td>${esc(c.odds || '')}</td>
        <td>${c.abnormal ? '⚠️' : ''}</td><td>${esc(c.note || '')}</td></tr>`).join('');
    if ((rep.combos || []).length) {
      add(el, 'panel-card', `<h3>조합 배당 (복승/쌍승/삼복승)</h3>
        <table class="data-table"><thead><tr><th>종류</th><th>조합</th><th>배당</th><th>이상</th><th>비고</th></tr></thead>
        <tbody>${crows}</tbody></table>`);
    }

    if (!(rep.horses || []).length && !(rep.combos || []).length) {
      add(el, 'panel-card', `<p class="hint">배당 데이터를 인식하지 못했습니다.</p>`);
    }
  }

  // ---------- [3번] 3종 동시 분석 ----------
  const TRIPLE_LABEL = { quinella: '복승', exacta: '쌍승', trio: '삼복승' };
  async function captureTriple(kind) {
    if (kind === 'exacta') { notify('🧩 쌍승은 Chrome 확장 [전체 자동 수집]으로 수집하세요 (캡처 중단).', true); return; }
    try {
      const full = await grabFrame();
      state.tripleCaps[kind] = canvasToBlock(full);
      $('#triSt-' + kind).textContent = '✅';
      const n = Object.values(state.tripleCaps).filter(Boolean).length;
      $('#tripleRunBtn').disabled = n < 1;
      $('#tripleHint').textContent = `${TRIPLE_LABEL[kind]} 캡처됨 (${n}/3) — [3종 동시 분석] 가능`;
      notify(`✅ ${TRIPLE_LABEL[kind]} 배당판 캡처`);
    } catch (e) { notify('캡처 실패: ' + e.message, false); }
  }

  async function analyzeTriple() {
    const caps = state.tripleCaps;
    if (!(caps.quinella || caps.exacta || caps.trio)) { toast('최소 1종 배당판을 캡처하세요.'); return; }
    try {
      showLoading('3종 배당판 동시 분석 중...');
      const rep = await Analysis.analyzeOddsTriple(caps);
      hideLoading();
      // [5번] 복승 불일치 감지 여부 저장(결과기록 시 통계 반영)
      state.lastTriple = { mismatch: (rep.inconsistencies || []).length > 0 };
      renderTriple(rep);
    } catch (e) {
      hideLoading();
      // [4] alert 대신 화면에 에러 표시 + 원인/대응 안내
      const el = $('#tripleReport');
      if (el) el.innerHTML =
        `<div class="panel-card" style="border-color:var(--red)">
           <h3 style="color:var(--red)">❌ 3종 배당판 분석 실패</h3>
           <p class="hint">${esc(e.message)}</p>
           <p class="hint">배당 조합이 많으면 응답이 잘릴 수 있어, 서버가 <b>배당 낮은(인기) 상위 40개</b>만 반환하도록 조정되었습니다. 다시 [⚡ 3종 동시 분석]을 눌러보세요. 계속 실패하면 캡처 영역을 한 종류씩 좁혀 시도하세요.</p>
         </div>`;
    }
  }

  function renderTriple(rep) {
    const el = $('#tripleReport'); el.innerHTML = '';
    add(el, 'panel-card', `<h2>🎯 3종 배당판 분석</h2><p class="hint">${esc(rep.summary || '')}</p>`);

    // 불일치(이상) 감지 — 핵심
    const inc = rep.inconsistencies || [];
    add(el, 'bet-box', `<h3>🚨 베팅종류 간 불일치(이상)</h3>` +
      (inc.length ? inc.map((i) =>
        `<div class="bet-line"><span>${i.level || '•'} <span class="bet-type">${(i.combo || []).join('-')}</span></span>
         <span>${esc(i.note || '')}</span></div>`).join('')
        : '<p class="hint">감지된 불일치 없음</p>') +
      ((rep.alerts || []).length ? rep.alerts.map((a) =>
        `<div class="bet-line"><span class="bet-type" style="color:var(--red)">●</span><span>${esc(a)}</span></div>`).join('') : ''));

    // 종류별 조합 배당
    const section = (arr, label) => {
      if (!(arr || []).length) return '';
      const rows = arr.map((c) =>
        `<tr${c.abnormal ? ' style="background:rgba(255,92,92,.12)"' : ''}>
          <td>${(c.combo || []).join('-')}</td><td>${esc(c.odds || '')}</td><td>${c.abnormal ? '⚠️ 급락' : ''}</td></tr>`).join('');
      return `<h3>${label}</h3><table class="data-table"><thead><tr><th>조합</th><th>배당</th><th>이상</th></tr></thead><tbody>${rows}</tbody></table>`;
    };
    const body = section(rep.quinella, '복승') + section(rep.exacta, '쌍승') + section(rep.trio, '삼복승');
    if (body) add(el, 'panel-card', body);
  }

  // ---------- 배당 시계열 이상감지 (Phase 2) ----------
  /** 연결 분석(BMED) 드롭다운 채우기 — 값=경주 라벨(raceKey) */
  function refreshOddsRaceSelect() {
    const sel = $('#oddsRaceSelect');
    if (!sel) return;
    const prev = sel.value;
    const titles = Object.keys(state.lastReports);
    sel.innerHTML = `<option value="배당판캡처">(연결 안 함 · 드롭만)</option>` +
      titles.map((t) => `<option value="${esc(t)}">${esc(t)}</option>`).join('');
    if ([...sel.options].some((o) => o.value === prev)) sel.value = prev;
  }

  function selectedRaceKey() {
    const sel = $('#oddsRaceSelect');
    return (sel && sel.value) ? sel.value : '배당판캡처';
  }

  /** 배당판 Vision 결과 → {마번: 배당(숫자)}.
   *  단승/연승(horses) 우선, 없으면 복승/쌍승(combos)에서 각 말이 낀 최소 배당으로 대표값. */
  function perHorseOdds(rep) {
    const num = (s) => { const m = String(s == null ? '' : s).replace(/,/g, '').match(/[\d.]+/); return m ? parseFloat(m[0]) : null; };
    const out = {};
    (rep.horses || []).forEach((h) => { const v = num(h.odds); if (h.no > 0 && v > 0) out[h.no] = v; });
    if (!Object.keys(out).length) {
      (rep.combos || []).forEach((c) => {
        const v = num(c.odds); if (!(v > 0)) return;
        (c.combo || []).forEach((no) => { if (no > 0 && (out[no] == null || v < out[no])) out[no] = v; });
      });
    }
    return out;
  }

  /** 쌍승(exacta) 배당판 → {"A>B": 배당} 순서쌍. 2마리 조합 셀을 방향(행→열) 그대로 사용. */
  function perPairOdds(rep) {
    const num = (s) => { const m = String(s == null ? '' : s).replace(/,/g, '').match(/[\d.]+/); return m ? parseFloat(m[0]) : null; };
    const combos = (rep.combos || []).filter((c) => (c.combo || []).length === 2);
    const hasExacta = combos.some((c) => /쌍/.test(c.type || ''));
    const out = {};
    combos.forEach((c) => {
      if (hasExacta && !/쌍/.test(c.type || '')) return;   // 쌍승 항목이 있으면 그것만
      const a = +c.combo[0], b = +c.combo[1], v = num(c.odds);
      if (a > 0 && b > 0 && a !== b && v > 0) out[`${a}>${b}`] = v;
    });
    return out;
  }

  /** 복승(quinella) 배당판 → {"A|B": 배당} 순서무관 쌍. 2마리 조합 셀을 정렬 키로 누적. */
  function perQuinellaPairs(rep) {
    const num = (s) => { const m = String(s == null ? '' : s).replace(/,/g, '').match(/[\d.]+/); return m ? parseFloat(m[0]) : null; };
    const out = {};
    (rep.combos || []).filter((c) => (c.combo || []).length === 2).forEach((c) => {
      if (/쌍/.test(c.type || '')) return;                 // 쌍승(순서) 항목 제외
      const a = +c.combo[0], b = +c.combo[1], v = num(c.odds);
      if (a > 0 && b > 0 && a !== b && v > 0) {
        const k = a < b ? `${a}|${b}` : `${b}|${a}`;
        if (out[k] == null || v < out[k]) out[k] = v;
      }
    });
    return out;
  }

  // ========== [다중 캡처 시계열 + 실시간 분석] ==========
  const ODDS_MAX = 20;          // [1번] 최대 누적 포인트
  const SIGNAL_ALERT = 80;      // [6번] 알림 임계 신호점수
  const CHART_COLORS = ['#4f8cff', '#3ecf8e', '#ffb74f', '#ff5c5c', '#b98cff'];

  /** [1번] 스냅샷 누적 — 첫 캡처는 새 추적 시작, 이후 계속 append(최대 20). Vision 재호출 없이 즉시 저장. */
  async function accumulateOdds(odds, quiet) {
    let t = state.oddsTrack;
    const fresh = !t.raceKey || !t.snaps;
    if (fresh) {
      const raceKey = selectedRaceKey();
      try { await Analysis.oddsClear(raceKey); } catch (_) { /* 무시 */ }
      t = state.oddsTrack = {
        betType: t.betType, raceKey, snaps: 0, nos: new Set(),
        firstOdds: { ...odds }, series: {}, times: [], alerted: {},
        auto: t.auto || null, deadlineMs: t.deadlineMs || 0,
        _autoId: t._autoId || null, _cdId: t._cdId || null,
        exSeries: {}, exTimes: [], qSeries: {}, qTimes: [], dual: t.dual !== false, _pendingType: t._pendingType || null,
        _autoRound: 0,
      };
    }
    if (t.snaps >= ODDS_MAX) { notify('⚠️ 최대 20포인트 — 더 저장하지 않습니다', false); stopAutoCapture(); return; }
    const r = await Analysis.oddsSnapshot(t.raceKey, odds);
    t.snaps = r.snaps || (t.snaps + 1);
    if (r.series) t.series = r.series;
    t.times.push(Date.now());
    Object.keys(odds).forEach((n) => t.nos.add(+n));
    await refreshRealtime(quiet);
    if (t.snaps >= ODDS_MAX) { notify('✅ 20포인트 도달 — 자동 캡처 종료', true); stopAutoCapture(); }
  }

  /** 현재 캡처된 프레임(크롭 반영)을 판독해 누적. _pendingType='쌍승'이면 쌍승 시계열로. */
  async function captureAccumulate() {
    if (!cap.canvas) { toast('먼저 화면을 캡처하거나 이미지를 올리세요.'); return; }
    const type = state.oddsTrack._pendingType || '복승';
    if (type === '복승' && state.oddsTrack.snaps >= ODDS_MAX) { notify('최대 20포인트 도달', false); stopAutoCapture(); return; }
    const block = capturedBlock();
    const quiet = !!state.oddsTrack.auto || type === '쌍승';   // 자동/쌍승 캡처는 오버레이 생략
    if (!quiet) showLoading('배당 판독 중... (Vision)');
    try {
      const rep = await Analysis.analyzeOdds(block);
      if (type === '쌍승') {
        const pairs = perPairOdds(rep);
        if (!Object.keys(pairs).length) { toast('쌍승 배당을 못 읽었습니다. 쌍승 매트릭스 화면인지 확인하세요.'); return; }
        accumulateExacta(pairs);
        notify(`🔀 쌍승 ${state.oddsTrack.exTimes.length}번째 캡처 누적 (${Object.keys(pairs).length}쌍)`, true);
      } else {
        const odds = perHorseOdds(rep);
        if (!Object.keys(odds).length) { toast('복승 배당을 못 읽었습니다. 영역 재선택 또는 [빠른입력].'); return; }
        const qpairs = perQuinellaPairs(rep);
        await accumulateOdds(odds, true);                       // state 생성/스냅샷/렌더
        if (Object.keys(qpairs).length) {                       // 복승 쌍 누적 → 매트릭스 갱신
          accumulateQuinellaPairs(qpairs);
          renderOddsMatrix(state.oddsTrack._lastComputed);
        }
        notify(`📸 복승 ${state.oddsTrack.snaps}번째 캡처 누적`, true);
      }
    } catch (e) { toast('캡처 실패: ' + e.message); }
    finally { if (!quiet) hideLoading(); }
  }

  /** 새 프레임을 즉시 잡아 자동 크롭 → 누적 (수동 [캡처 누적] · 자동 인터벌 공용) */
  async function captureNow() {
    try {
      cap.autoAnalyze = true;               // 자동 크롭 성공/실패 모두 runCropAnalysis로 이어짐
      setCaptured(await grabFrame());
    } catch (e) { notify('캡처 실패: ' + e.message, false); }
  }

  /** 지정 타입으로 1회 캡처를 끝까지 await (복승+쌍승 시퀀스용) */
  async function captureAs(type) {
    state.oddsTrack._pendingType = type;
    try {
      cap.autoAnalyze = false;
      setCaptured(await grabFrame());       // cap.canvas + 자동 크롭 rect 설정
      $('#capConfirm').classList.add('hidden');
      await captureAccumulate();            // _pendingType 으로 라우팅
    } catch (e) { notify('캡처 실패: ' + e.message, false); }
    finally { state.oddsTrack._pendingType = null; }
  }

  // ---------- [1·2번] 복승+쌍승 2단 캡처 + 카운트다운 + 스텝 가이드 ----------
  /** [캡처] 1번 → 복승 캡처 → (쌍승 탭 전환 안내 카운트다운) → 쌍승 캡처 */
  // [변경] 복승 전용 캡처. (쌍승은 화면 캡처 대신 Chrome 확장 [전체 자동 수집]으로 통일 —
  //  캡처 방식은 쌍승 탭 전환이 불안정해 복승만 잡히는 문제가 있었다.)
  async function dualCapture() {
    if (state.oddsTrack.auto) { toast('자동 캡처 중에는 [중지] 후 사용하세요.'); return; }
    setStep(2);
    showLoading('복승 배당 판독 중... (Vision)');
    try { await captureAs('복승'); } finally { hideLoading(); }
    setStep(1);
    notify('✅ 복승 캡처 완료 · 쌍승은 확장 [전체 자동 수집] 사용', true);
  }

  /** 안내 문구 + N초 카운트다운(3..2..1) 후 지정 타입 자동 캡처 */
  async function countdownCapture(msg, secs, type) {
    const ov = $('#capCountdown'); if (!ov) { await captureAs(type); return; }
    ov.classList.remove('hidden');
    for (let s = secs; s >= 1; s--) {
      ov.innerHTML = `<div class="cd-msg">${esc(msg)}</div><div class="cd-num">${s}</div>`;
      beepShort();
      await new Promise((r) => setTimeout(r, 1000));
    }
    ov.innerHTML = '<div class="cd-msg">📸 쌍승 캡처 중...</div>';
    await captureAs(type);
    ov.classList.add('hidden');
  }

  /** [2번] 캡처 순서 스텝 가이드 강조 (1~3, 0=없음) */
  function setStep(n) {
    $$('#captureSteps .cap-step').forEach((el) => el.classList.toggle('active', +el.dataset.step === n));
  }

  function beepShort() {
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      const ctx = new AC();
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.connect(g); g.connect(ctx.destination);
      o.type = 'sine'; o.frequency.value = 660;
      g.gain.setValueAtTime(0.0001, ctx.currentTime);
      g.gain.exponentialRampToValueAtTime(0.25, ctx.currentTime + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.18);
      o.start(); o.stop(ctx.currentTime + 0.2);
    } catch (_) { /* 무시 */ }
  }

  // ---------- 쌍승(exacta) 시계열 (클라이언트 저장) ----------
  /** {"A>B": 배당} 한 라운드를 exSeries에 누적 (null 패딩으로 인덱스 정합) */
  function accumulateExacta(pairs) {
    const t = state.oddsTrack;
    if (!t.exSeries) { t.exSeries = {}; t.exTimes = []; }
    const idx = t.exTimes.length;
    const keys = new Set([...Object.keys(t.exSeries), ...Object.keys(pairs)]);
    keys.forEach((k) => {
      const arr = t.exSeries[k] || new Array(idx).fill(null);
      while (arr.length < idx) arr.push(null);
      arr.push(k in pairs ? pairs[k] : null);
      t.exSeries[k] = arr;
    });
    t.exTimes.push(Date.now());
    renderExactaReversal();
    if ($('#oddsMatrixHost')) renderOddsMatrix(t._lastComputed);   // 쌍승 추가 시 매트릭스 즉시 갱신
  }

  // ---------- 복승(quinella) 쌍 시계열 (매트릭스용, 순서무관) ----------
  /** {"A|B": 배당} 한 라운드를 qSeries에 누적 (null 패딩으로 인덱스 정합) */
  function accumulateQuinellaPairs(pairs) {
    const t = state.oddsTrack;
    if (!t.qSeries) { t.qSeries = {}; t.qTimes = []; }
    const idx = t.qTimes.length;
    const keys = new Set([...Object.keys(t.qSeries), ...Object.keys(pairs)]);
    keys.forEach((k) => {
      const arr = t.qSeries[k] || new Array(idx).fill(null);
      while (arr.length < idx) arr.push(null);
      arr.push(k in pairs ? pairs[k] : null);
      t.qSeries[k] = arr;
    });
    t.qTimes.push(Date.now());
  }

  /** [4번 소비] 쌍승 A→B / B→A 표 + 방향 대소 뒤바뀜(역전) 감지 */
  function renderExactaReversal() {
    const el = $('#exactaReport'); if (!el) return; el.innerHTML = '';
    const t = state.oddsTrack; const ex = t.exSeries || {};
    if (!Object.keys(ex).length) return;
    const lastVal = (arr) => { for (let i = (arr || []).length - 1; i >= 0; i--) if (typeof arr[i] === 'number') return arr[i]; return null; };
    const seen = new Set(); const rows = [];
    Object.keys(ex).forEach((k) => {
      const [a, b] = k.split('>').map(Number);
      const uk = a < b ? `${a}|${b}` : `${b}|${a}`;
      if (seen.has(uk)) return; seen.add(uk);
      const ab = ex[`${a}>${b}`] || [], ba = ex[`${b}>${a}`] || [];
      let rev = false, prevSign = 0;
      const len = Math.max(ab.length, ba.length);
      for (let i = 0; i < len; i++) {
        const x = ab[i], y = ba[i];
        if (typeof x === 'number' && typeof y === 'number' && x !== y) {
          const s = x < y ? -1 : 1;
          if (prevSign && s !== prevSign) rev = true;
          prevSign = s;
        }
      }
      rows.push({ a, b, abL: lastVal(ab), baL: lastVal(ba), rev });
    });
    rows.sort((p, q) => (p.rev === q.rev ? 0 : p.rev ? -1 : 1));
    const trs = rows.map((r) => `<tr${r.rev ? ' style="background:rgba(255,92,92,.12)"' : ''}>
      <td>${r.a}→${r.b}</td><td>${r.abL != null ? r.abL : '-'}</td>
      <td>${r.b}→${r.a}</td><td>${r.baL != null ? r.baL : '-'}</td>
      <td>${r.rev ? '🔴 역전' : ''}</td></tr>`).join('');
    add(el, 'panel-card', `<h3>🔀 쌍승 A→B / B→A 시계열 (${t.exTimes.length}포인트)</h3>
      <table class="data-table"><thead><tr><th>정방향</th><th>배당</th><th>역방향</th><th>배당</th><th>역전</th></tr></thead><tbody>${trs}</tbody></table>
      <p class="hint" style="margin-top:6px">두 방향 배당의 대소가 시계열 중 뒤바뀌면 🔴 역전 — 인기 순서 반전 신호</p>`);
  }

  // ---------- [2번] 자동 캡처 인터벌 ----------
  function startAutoCapture(min) {
    stopAutoCapture();
    applyDeadline();
    state.oddsTrack.auto = { min };
    state.oddsTrack._autoId = setInterval(autoTick, min * 60 * 1000);
    const sb = $('#autoStopBtn'); if (sb) sb.disabled = false;
    $$('.auto-int-btn').forEach((b) => b.classList.toggle('active', +b.dataset.min === min));
    notify(`⏱ ${min}분 간격 자동 캡처 시작 (Alt+C 자동 실행·누적)`, true);
    autoTick();                              // 즉시 1회 캡처
    startAutoCountdown(min);
  }
  async function autoTick() {
    const t = state.oddsTrack;
    if (reachedDeadline()) { stopAutoCapture(); beep(); notify('⏰ 마감 시각 도달 — 자동 캡처 종료', true); return; }
    if (t.snaps >= ODDS_MAX) { stopAutoCapture(); return; }
    t._autoRound = (t._autoRound || 0) + 1;
    if (t.dual && t._autoRound % 2 === 0) {
      // [3번] 짝수 회차 → 쌍승: "쌍승 탭으로 전환" 안내 후 5초 대기 → 자동 캡처
      notify('🔀 쌍승 탭으로 전환해주세요 — 5초 후 자동 캡처', true);
      await countdownCapture('🔀 [쌍승] 탭으로 전환하세요! 5초 후 자동 캡처', 5, '쌍승');
    } else {
      state.oddsTrack._pendingType = '복승';
      captureNow();                          // 홀수 회차 → 복승 (사용자가 복승 탭 열어둠)
    }
    if (t.auto) startAutoCountdown(t.auto.min);
  }
  function stopAutoCapture() {
    const t = state.oddsTrack;
    if (t._autoId) clearInterval(t._autoId);
    t._autoId = null; t.auto = null;
    stopAutoCountdown();
    const sb = $('#autoStopBtn'); if (sb) sb.disabled = true;
    $$('.auto-int-btn').forEach((b) => b.classList.remove('active'));
    updateTrackHint();
  }

  // ---------- 마감 시각 (자동 중지 + 차트 X축 기준) ----------
  function parseDeadline() {
    const inp = $('#deadlineInput');
    if (!inp || !inp.value) return 0;
    const [h, m] = inp.value.split(':').map(Number);
    if (isNaN(h)) return 0;
    const d = new Date(); d.setHours(h, m, 0, 0);
    let ms = d.getTime();
    if (ms < Date.now() - 60000) ms += 24 * 3600 * 1000; // 이미 지난 시각이면 다음날로
    return ms;
  }
  function applyDeadline() { state.oddsTrack.deadlineMs = parseDeadline(); updateTrackHint(); }
  function reachedDeadline() { const dl = state.oddsTrack.deadlineMs || parseDeadline(); return dl > 0 && Date.now() >= dl; }

  // ---------- 다음 캡처 카운트다운 (snapTimer 재사용) ----------
  function startAutoCountdown(min) {
    stopAutoCountdown();
    state.oddsTrack._nextAt = Date.now() + min * 60 * 1000;
    state.oddsTrack._cdId = setInterval(updateAutoTimer, 1000);
    updateAutoTimer();
  }
  function updateAutoTimer() {
    const el = $('#snapTimer'); if (!el) return;
    const t = state.oddsTrack;
    if (!t.auto) { el.textContent = ''; return; }
    const left = Math.max(0, Math.round((t._nextAt - Date.now()) / 1000));
    const m = Math.floor(left / 60), s = left % 60;
    const dl = t.deadlineMs || 0;
    const dlTxt = dl ? ` · 마감까지 ${Math.max(0, Math.round((dl - Date.now()) / 60000))}분` : '';
    el.textContent = `⏱ 다음 자동 캡처까지 ${m}:${String(s).padStart(2, '0')} (${t.auto.min}분 간격)${dlTxt}`;
    el.style.color = left <= 15 ? 'var(--accent-2)' : '';
  }
  function stopAutoCountdown() {
    const t = state.oddsTrack;
    if (t._cdId) clearInterval(t._cdId); t._cdId = null;
    const el = $('#snapTimer'); if (el) el.textContent = '';
  }

  /** 시계열 초기화 */
  async function oddsReset() {
    stopAutoCapture();
    const t = state.oddsTrack;
    if (t.raceKey) { try { await Analysis.oddsClear(t.raceKey); } catch (_) { /* 무시 */ } }
    state.oddsTrack = {
      betType: t.betType, raceKey: null, snaps: 0, nos: new Set(), firstOdds: {},
      series: {}, times: [], alerted: {}, auto: null, deadlineMs: t.deadlineMs || 0,
      exSeries: {}, exTimes: [], qSeries: {}, qTimes: [], dual: t.dual !== false, _pendingType: null, _autoRound: 0,
    };
    $('#oddsTrackReport').innerHTML = '';
    const ex = $('#exactaReport'); if (ex) ex.innerHTML = '';
    const w = $('#oddsChartWrap'); if (w) w.classList.add('hidden');
    setStep(1);
    updateTrackHint();
    notify('🗑 시계열 초기화됨', true);
  }

  function updateTrackHint() {
    const el = $('#oddsTrackHint'); if (!el) return;
    const t = state.oddsTrack;
    if (!t.snaps) { el.textContent = '화면 캡처 후 [📸 캡처 누적] 또는 자동 캡처(2/3/5분)를 시작하세요.'; return; }
    el.textContent = `누적 ${t.snaps}/${ODDS_MAX}포인트 · 마번 ${t.nos.size}두${t.auto ? ` · 자동(${t.auto.min}분) 진행중` : ''}`;
  }

  // ---------- [5번] 실시간 신호 재계산 + [3·4번] 차트 + [6번] 알림 ----------
  /** [5번] 추세 지속성: 직전까지 연속 하락 중이면 신호 +10 */
  function augmentTrend(computed, series) {
    (computed.horses || []).forEach((h) => {
      const arr = (series[h.no] || series[String(h.no)] || []).filter((v) => typeof v === 'number' && v > 0);
      let streak = 0;
      for (let i = arr.length - 1; i > 0; i--) { if (arr[i] < arr[i - 1]) streak++; else break; }
      h.trendStreak = streak;
      h.persist = streak >= 2 ? 10 : 0;
      if (h.persist) {
        h.signalScore = Math.min(100, (h.signalScore || 0) + h.persist);
        (h.tags = h.tags || []).push('⏬ 지속하락');
      }
    });
  }

  /** 캡처마다: 서버 신호 계산 → 추세 보정 → 차트/표/알림 갱신 */
  async function refreshRealtime(quiet) {
    const t = state.oddsTrack;
    if (!t.raceKey) return;
    let computed = null;
    try { computed = await Analysis.oddsCompute(t.raceKey, bmedHorsesFor(t.raceKey)); }
    catch (e) { if (!quiet) toast('신호 계산 실패: ' + e.message); }
    if (computed) augmentTrend(computed, t.series || {});
    t._lastComputed = computed;   // 매트릭스 추천 표시용
    drawOddsChart(t, computed);   // t._reversals 설정
    renderOddsRealtime(computed);
    maybeAlert(computed);
    updateTrackHint();
  }

  /** [6번] 신호 임계 초과 시 소리 + 요약 알림 (마번별 1회, 히스테리시스로 재알림) */
  function maybeAlert(computed) {
    if (!computed) return;
    const t = state.oddsTrack;
    (computed.horses || []).forEach((h) => {
      if (h.signalScore >= SIGNAL_ALERT && !t.alerted[h.no]) {
        t.alerted[h.no] = true;
        beep();
        notify(`🔔 ${h.no}번 신호 ${h.signalScore}점 · ${t.betType} ${h.no}번 가능성`, true);
      } else if (h.signalScore < SIGNAL_ALERT - 10) {
        t.alerted[h.no] = false;
      }
    });
  }

  /** 차트에 그릴 상위 5마리 (신호 높은 순, 시계열 존재하는 말만) */
  function pickTopHorses(computed, series, n) {
    const has = (no) => (series[no] || series[String(no)] || []).some((v) => typeof v === 'number' && v > 0);
    return (computed && computed.horses || []).filter((h) => has(h.no))
      .sort((a, b) => (b.signalScore - a.signalScore) || ((a.lastOdds || 99) - (b.lastOdds || 99)))
      .slice(0, n);
  }

  /** [3번] 시계열 라인 차트 (상위5·급락 빨강/반등 노랑) + [4번] 라인 교차=역전 표시 */
  function drawOddsChart(t, computed) {
    const wrap = $('#oddsChartWrap'); const cv = $('#oddsChart');
    if (!wrap || !cv) return;
    const series = t.series || {};
    const top = pickTopHorses(computed, series, 5);
    if (!top.length || t.snaps < 1) { wrap.classList.add('hidden'); t._reversals = 0; return; }
    wrap.classList.remove('hidden');

    const dl = t.deadlineMs || parseDeadline();
    const times = t.times || [];
    const invert = !!dl;                   // 마감 기준이면 오른쪽이 0분(마감)
    const xVal = (i) => {
      if (dl && times[i]) return (dl - times[i]) / 60000;          // 남은 분(클수록 과거)
      if (times[i] && times[0]) return (times[i] - times[0]) / 60000; // 경과 분
      return i;
    };
    const n = t.snaps;
    const xs = []; for (let i = 0; i < n; i++) xs.push(xVal(i));

    const lines = top.map((h, idx) => {
      const arr = series[h.no] || series[String(h.no)] || [];
      const pts = [];
      for (let i = 0; i < n; i++) { const v = arr[i]; if (typeof v === 'number' && v > 0) pts.push({ i, x: xs[i], y: v }); }
      return { no: h.no, color: CHART_COLORS[idx % CHART_COLORS.length], pts, sig: h.signalScore };
    }).filter((l) => l.pts.length);
    if (!lines.length) { wrap.classList.add('hidden'); t._reversals = 0; return; }

    const allY = []; lines.forEach((l) => l.pts.forEach((p) => allY.push(p.y)));
    let minX = Math.min(...xs), maxX = Math.max(...xs);
    let minY = Math.min(...allY), maxY = Math.max(...allY);
    if (minX === maxX) { minX -= 1; maxX += 1; }
    if (minY === maxY) { minY *= 0.9; maxY *= 1.1 || 1; }
    const padY = (maxY - minY) * 0.1 || 0.5; minY -= padY; maxY += padY;

    const W = Math.max(320, wrap.clientWidth || 640), H = 260;
    const dpr = window.devicePixelRatio || 1;
    cv.width = W * dpr; cv.height = H * dpr; cv.style.width = W + 'px'; cv.style.height = H + 'px';
    const ctx = cv.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);
    const L = 46, R = 14, TT = 16, B = 28; const pw = W - L - R, ph = H - TT - B;

    const sx = (x) => { let f = (x - minX) / (maxX - minX || 1); if (invert) f = 1 - f; return L + f * pw; };
    const sy = (y) => TT + (1 - (y - minY) / (maxY - minY || 1)) * ph;

    ctx.strokeStyle = 'rgba(140,148,166,.2)'; ctx.fillStyle = '#8a94a6'; ctx.font = '11px sans-serif'; ctx.lineWidth = 1;
    for (let g = 0; g <= 4; g++) {
      const yy = TT + ph * g / 4; const yv = maxY - (maxY - minY) * g / 4;
      ctx.beginPath(); ctx.moveTo(L, yy); ctx.lineTo(L + pw, yy); ctx.stroke();
      ctx.fillText(yv.toFixed(1), 6, yy + 3);
    }
    const xticks = 4;
    for (let g = 0; g <= xticks; g++) {
      const xx = L + pw * g / xticks;
      const xv = invert ? (maxX - (maxX - minX) * g / xticks) : (minX + (maxX - minX) * g / xticks);
      ctx.fillText((dl || times.length) ? xv.toFixed(0) + '분' : '#' + g, xx - 8, H - 8);
    }
    ctx.fillText(dl ? '← 마감까지 남은 분 (오른쪽=마감)' : '경과(분)', L, 11);

    const RED = '#ff5c5c', YEL = '#ffd24f';
    lines.forEach((l) => {
      for (let k = 1; k < l.pts.length; k++) {
        const a = l.pts[k - 1], b = l.pts[k];
        const rel = a.y ? (a.y - b.y) / a.y : 0;   // >0 하락(배당 짧아짐)
        let col = l.color, lw = 2;
        if (rel >= 0.06) { col = RED; lw = 3; }       // [3번] 급락
        else if (rel <= -0.06) { col = YEL; lw = 3; } // [3번] 반등
        ctx.strokeStyle = col; ctx.lineWidth = lw;
        ctx.beginPath(); ctx.moveTo(sx(a.x), sy(a.y)); ctx.lineTo(sx(b.x), sy(b.y)); ctx.stroke();
      }
      ctx.fillStyle = l.color;
      l.pts.forEach((p) => { ctx.beginPath(); ctx.arc(sx(p.x), sy(p.y), 2.5, 0, Math.PI * 2); ctx.fill(); });
      const lp = l.pts[l.pts.length - 1];
      ctx.font = 'bold 11px sans-serif';
      ctx.fillText(`${l.no}번`, sx(lp.x) + 4, sy(lp.y) - 4);
    });

    // [4번] 라인 교차 → 역전 발생 표시
    let reversals = 0;
    for (let a = 0; a < lines.length; a++) {
      for (let b = a + 1; b < lines.length; b++) {
        const A = lines[a], B = lines[b];
        const mA = {}; A.pts.forEach((p) => { mA[p.i] = p.y; });
        const mB = {}; B.pts.forEach((p) => { mB[p.i] = p.y; });
        const idxs = Object.keys(mA).map(Number).filter((i) => i in mB).sort((x, y) => x - y);
        for (let k = 1; k < idxs.length; k++) {
          const i0 = idxs[k - 1], i1 = idxs[k];
          const d0 = mA[i0] - mB[i0], d1 = mA[i1] - mB[i1];
          if (d0 === 0 || d1 === 0) continue;
          if ((d0 < 0) !== (d1 < 0)) {
            const frac = Math.abs(d0) / (Math.abs(d0) + Math.abs(d1));
            const xc = xs[i0] + (xs[i1] - xs[i0]) * frac;
            const mx = sx(xc);
            ctx.strokeStyle = 'rgba(255,92,92,.55)'; ctx.lineWidth = 1; ctx.setLineDash([4, 3]);
            ctx.beginPath(); ctx.moveTo(mx, TT); ctx.lineTo(mx, TT + ph); ctx.stroke(); ctx.setLineDash([]);
            ctx.fillStyle = RED; ctx.font = '12px sans-serif'; ctx.fillText('🔴역전', mx - 12, TT + 12);
            reversals++;
          }
        }
      }
    }
    t._reversals = reversals;
  }

  /** [5번] 실시간 신호 표 (직전 대비 변화율 · 누적드롭 · 괴리 · 신호+지속) */
  function renderOddsRealtime(computed) {
    const el = $('#oddsTrackReport'); el.innerHTML = '';
    const t = state.oddsTrack;
    if (!computed || !computed.snapCount) { add(el, 'panel-card', '<p class="hint">캡처를 시작하면 실시간 분석이 표시됩니다.</p>'); return; }
    const series = t.series || {};
    const dl = t.deadlineMs || parseDeadline();
    const leftMin = dl ? Math.max(0, Math.round((dl - Date.now()) / 60000)) : null;

    const prevDelta = (no) => {
      const arr = (series[no] || series[String(no)] || []).filter((v) => typeof v === 'number' && v > 0);
      if (arr.length < 2) return null;
      const p = arr[arr.length - 2], c = arr[arr.length - 1];
      return p ? (p - c) / p : null;   // >0 하락
    };

    const rows = computed.horses.slice().sort((a, b) => b.signalScore - a.signalScore).map((h) => {
      const dpct = h.firstOdds != null ? (h.drop * 100).toFixed(0) + '%' : '-';
      const pd = prevDelta(h.no);
      const pdTxt = pd == null ? '-' : (pd >= 0 ? '▼' : '▲') + (Math.abs(pd) * 100).toFixed(0) + '%';
      const epct = h.edge != null ? (h.edge >= 0 ? '+' : '') + (h.edge * 100).toFixed(1) + '%p' : '-';
      const persist = h.persist ? ` <span class="odds-tag">+${h.persist}</span>` : '';
      const streak = h.trendStreak ? ` <span class="hint">(${h.trendStreak}연속↓)</span>` : '';
      return `<tr${h.signalScore >= SIGNAL_ALERT ? ' style="background:rgba(255,92,92,.10)"' : ''}>
        <td>${h.no}</td><td>${esc(h.name || '')}</td>
        <td>${h.lastOdds != null ? h.lastOdds : '-'}</td>
        <td class="${pd > 0.02 ? 'pos' : pd < -0.02 ? 'neg' : ''}">${pdTxt}</td>
        <td class="${h.drop > 0.02 ? 'pos' : h.drop < -0.02 ? 'neg' : ''}">${dpct}</td>
        <td>${epct}</td>
        <td><b>${sigColor(h.signalScore)} ${h.signalScore}</b>${persist}${streak}</td>
        <td>${(h.tags || []).map((x) => `<span class="odds-tag">${esc(x)}</span>`).join(' ')}</td>
      </tr>`;
    }).join('');

    add(el, 'panel-card', `<h3>⚡ 실시간 배당 분석 <span class="hint" style="font-weight:400">스냅샷 ${computed.snapCount}/${ODDS_MAX}${leftMin != null ? ` · 마감 ${leftMin}분 전` : ''}${t._reversals ? ` · 🔴 역전 ${t._reversals}건` : ''}</span></h3>
      <table class="data-table">
        <thead><tr><th>마번</th><th>마명</th><th>배당</th><th>직전Δ</th><th>누적드롭</th><th>괴리</th><th>신호</th><th>판정</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <p class="hint" style="margin-top:8px">직전Δ=직전 캡처 대비 · 누적드롭=1차 대비 · 연속 하락 시 신호 +10 · 🔔 신호 ≥${SIGNAL_ALERT} 소리알림 · 🔴≥75 🟠60 🟡45</p>`);

    const bets = (computed.bets || []).map((b) =>
      `<div class="bet-line"><span><span class="bet-type">${esc(b.type)}</span> ${(b.combo || []).join('-')}</span>
       <span>신호 ${b.confidence} · ${esc(b.note || '')}</span></div>`).join('') || '<p class="hint">신호가 충분하지 않습니다.</p>';
    add(el, 'bet-box', `<h3>💰 이상감지 보정 추천 <span class="hint" style="font-weight:400">(${esc(t.betType)})</span></h3>${bets}`);
    el.insertAdjacentHTML('beforeend', '<div class="panel-card" id="oddsMatrixHost"></div>');
    renderOddsMatrix(computed);
  }

  // ---------- [1번] 배당판 매트릭스 (복승 삼각 · 쌍승 정방형 + 추천 카드) ----------
  const lastNum = (arr) => { for (let i = (arr || []).length - 1; i >= 0; i--) if (typeof arr[i] === 'number') return arr[i]; return null; };
  const firstNum = (arr) => { for (let i = 0; i < (arr || []).length; i++) if (typeof arr[i] === 'number') return arr[i]; return null; };
  const prevNum = (arr) => { const ns = (arr || []).filter((v) => typeof v === 'number'); return ns.length >= 2 ? ns[ns.length - 2] : null; };

  /** 낮은 배당 = 진한 파랑 (로그 스케일). v 없으면 투명. */
  function heatColor(v, lo, hi) {
    if (!(v > 0)) return 'transparent';
    const l = Math.log(v), a0 = Math.log(lo), a1 = Math.log(hi);
    const f = a1 > a0 ? (l - a0) / (a1 - a0) : 0;      // 0=최저배당, 1=최고배당
    return `rgba(37,99,235,${(0.88 - 0.72 * f).toFixed(2)})`;
  }

  function renderOddsMatrix(computed) {
    const host = $('#oddsMatrixHost'); if (!host) return;
    const t = state.oddsTrack;
    const q = t.qSeries || {}, ex = t.exSeries || {};
    const hasQ = Object.keys(q).length, hasX = Object.keys(ex).length;

    // 마번 집합 (쌍 데이터 우선, 없으면 신호 계산의 출전마)
    const nosSet = new Set();
    Object.keys(q).forEach((k) => k.split('|').forEach((n) => nosSet.add(+n)));
    Object.keys(ex).forEach((k) => k.split('>').forEach((n) => nosSet.add(+n)));
    if (!nosSet.size && computed) (computed.horses || []).forEach((h) => nosSet.add(+h.no));
    const nos = [...nosSet].filter((n) => n > 0).sort((a, b) => a - b);
    if (!nos.length || (!hasQ && !hasX)) {
      host.innerHTML = '<h3>🔢 배당판 매트릭스</h3><p class="hint">복승/쌍승을 캡처하면 실제 배당판과 동일한 매트릭스로 표시됩니다. (복승 매트릭스 화면 또는 쌍승 캡처 필요)</p>';
      return;
    }

    // 추천 조합 (신호 계산 bets → 복승/쌍승 키)
    const recQ = new Set(), recX = new Set();
    const score = {}; (computed?.horses || []).forEach((h) => { score[h.no] = h.signalScore; });
    (computed?.bets || []).forEach((b) => {
      const c = (b.combo || []).map(Number); if (c.length !== 2) return;
      if (/복/.test(b.type)) recQ.add(c[0] < c[1] ? `${c[0]}|${c[1]}` : `${c[1]}|${c[0]}`);
      if (/쌍/.test(b.type)) recX.add(`${c[0]}>${c[1]}`);
    });

    let html = '';

    // ── [1-1] 복승 삼각 매트릭스 ──
    if (hasQ) {
      const vals = Object.values(q).map(lastNum).filter((v) => v > 0);
      const lo = Math.min(...vals), hi = Math.max(...vals);
      let head = '<tr><th class="corner">복승</th>' + nos.slice(0, -1).map((n) => `<th>${n}</th>`).join('') + '</tr>';
      let body = '';
      for (let r = 1; r < nos.length; r++) {
        const rowNo = nos[r];
        let tds = '';
        for (let cIdx = 0; cIdx < r; cIdx++) {
          const colNo = nos[cIdx];
          const key = rowNo < colNo ? `${rowNo}|${colNo}` : `${colNo}|${rowNo}`;
          const v = lastNum(q[key]);
          if (v > 0) {
            const rec = recQ.has(key) ? ' rec-q' : '';
            tds += `<td class="cell${rec}" style="background:${heatColor(v, lo, hi)}" title="${rowNo}-${colNo}">${v}</td>`;
          } else tds += '<td class="empty">·</td>';
        }
        tds += '<td class="diag">—</td>';
        body += `<tr><th>${rowNo}</th>${tds}</tr>`;
      }
      html += `<div class="matrix-title">🎰 복승 매트릭스 <span class="hint" style="font-weight:400">${vals.length}조합 · ${t.qTimes.length}회 캡처</span></div>
        <div class="matrix-legend"><span>낮은 배당</span><span class="legend-grad"></span><span>높은 배당</span><span style="margin-left:10px">🔴 추천 조합</span></div>
        <div class="matrix-wrap"><table class="odds-matrix"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
    }

    // ── [1-2] 쌍승 정방형 매트릭스 (순서 있음: 행→열) ──
    if (hasX) {
      const vals = Object.values(ex).map(lastNum).filter((v) => v > 0);
      const lo = Math.min(...vals), hi = Math.max(...vals);
      let head = '<tr><th class="corner">쌍승 ↓→</th>' + nos.map((n) => `<th>${n}</th>`).join('') + '</tr>';
      let body = '';
      for (const rowNo of nos) {
        let tds = '';
        for (const colNo of nos) {
          if (rowNo === colNo) { tds += '<td class="diag">—</td>'; continue; }
          const v = lastNum(ex[`${rowNo}>${colNo}`]);
          if (v > 0) {
            const rec = recX.has(`${rowNo}>${colNo}`) ? ' rec-x' : '';
            tds += `<td class="cell${rec}" style="background:${heatColor(v, lo, hi)}" title="${rowNo}→${colNo}">${v}</td>`;
          } else tds += '<td class="empty">·</td>';
        }
        body += `<tr><th>${rowNo}</th>${tds}</tr>`;
      }
      html += `<div class="matrix-title">🔀 쌍승 매트릭스 <span class="hint" style="font-weight:400">행→열 순서 · ${vals.length}쌍</span></div>
        <div class="matrix-legend"><span>낮은 배당</span><span class="legend-grad"></span><span>높은 배당</span><span style="margin-left:10px">🟠 추천 조합</span></div>
        <div class="matrix-wrap"><table class="odds-matrix"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
    }

    // ── 추천 조합 카드 (복승 우선, 신호 bets 있으면 표시) ──
    const cards = buildComboCards(q, computed, score, recQ);
    if (cards) html += `<div class="matrix-title">⭐ 추천 조합</div><div class="combo-cards">${cards}</div>`;

    host.innerHTML = html;
  }

  /** 추천 조합 카드: 신호 bets(복승) 우선, 없으면 배당 낮은 복승 쌍 상위. 급락(1차 대비)·막판(직전 대비) 변화 표기. */
  function buildComboCards(q, computed, score, recQ) {
    const move = (arr) => {
      const f = firstNum(arr), p = prevNum(arr), l = lastNum(arr);
      const drop = f && l ? (l - f) / f : null;       // <0 = 배당 하락(급락)
      const late = p && l ? (l - p) / p : null;
      const fmt = (x) => x == null ? '-' : `<span class="${x < 0 ? 'dn' : x > 0 ? 'up' : ''}">${x >= 0 ? '+' : ''}${(x * 100).toFixed(0)}%</span>`;
      return `급락 ${fmt(drop)} / 막판 ${fmt(late)}`;
    };
    let picks = [];
    (computed?.bets || []).forEach((b) => {
      const c = (b.combo || []).map(Number); if (c.length !== 2 || !/복/.test(b.type)) return;
      const key = c[0] < c[1] ? `${c[0]}|${c[1]}` : `${c[1]}|${c[0]}`;
      picks.push({ type: '복승', a: c[0], b: c[1], key, rec: true, note: b.note });
    });
    if (!picks.length && Object.keys(q).length) {          // bets 없으면 배당 낮은 복승 쌍 상위 3
      picks = Object.keys(q).map((k) => ({ key: k, odds: lastNum(q[k]) })).filter((x) => x.odds > 0)
        .sort((x, y) => x.odds - y.odds).slice(0, 3)
        .map((x) => { const [a, b] = x.key.split('|').map(Number); return { type: '복승', a, b, key: x.key, rec: recQ.has(x.key) }; });
    }
    return picks.slice(0, 4).map((p) => {
      const odds = lastNum(q[p.key]);
      const sa = score[p.a] != null ? `(${score[p.a]})` : '', sb = score[p.b] != null ? `(${score[p.b]})` : '';
      return `<div class="combo-card${p.rec ? ' rec' : ''}">
        <div class="cc-head"><span class="cc-type">${p.type} ${p.a}+${p.b}</span><span class="cc-odds">${odds != null ? odds + '배' : '-'}</span></div>
        <div class="cc-horses">${p.a}번${sa} + ${p.b}번${sb}</div>
        <div class="cc-move">${move(q[p.key])}</div>
      </div>`;
    }).join('');
  }

  // ── [연동] 확장 [전체 자동 수집] 3종을 서버에서 불러와 매트릭스로 표시 ──
  // ── [경주별 분리] 활성 raceKey 모델 — 현재 경주만 표시, 나머지는 히스토리 ──
  const ACTIVE_RK_KEY = 'bmed_activeRaceKey';
  function getActiveRaceKey() { try { return localStorage.getItem(ACTIVE_RK_KEY) || null; } catch (_) { return null; } }
  function setActiveRaceKey(rk) {
    try { if (rk) localStorage.setItem(ACTIVE_RK_KEY, rk); else localStorage.removeItem(ACTIVE_RK_KEY); } catch (_) { /* */ }
    const lb = $('#activeRaceLabel'); if (lb) lb.textContent = '현재 경주: ' + (rk || '— (다음 수집 시 자동 설정)');
  }
  /** 서버 최신 raceKey 조회(폴백=최근). 활성과 다르면 새 경주로 판단. */
  async function fetchLatestRaceKey() {
    try {
      const d = await (await fetch('/api/odds/triple/latest', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })).json();
      return d && d.raceKey ? d.raceKey : null;
    } catch (_) { return null; }
  }
  /** [2번] 확장이 새 raceKey로 수집하면 자동 전환(이전 경주는 히스토리 파일로 이미 보존). */
  async function reconcileActiveRace() {
    const latest = await fetchLatestRaceKey();
    if (!latest) return getActiveRaceKey();
    const active = getActiveRaceKey();
    if (active && latest !== active) {
      notify(`🆕 새 경주 감지 → 전환: ${latest}\n(이전 경주 ${active}는 [📜 히스토리 보기]에 보존)`, true);
      _resetTripleView(false);   // 화면/토글만 초기화(서버 데이터는 유지)
    }
    if (latest !== active) setActiveRaceKey(latest);
    return latest;
  }

  async function loadTripleFromServer() {
    const el = $('#tripleMatrixReport');
    const active = getActiveRaceKey();
    if (el) el.innerHTML = '<p class="hint">불러오는 중…</p>';
    let data;
    try {
      const body = active ? JSON.stringify({ raceKey: active }) : '{}';
      data = await (await fetch('/api/odds/triple/latest', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body })).json();
    } catch (e) { if (el) el.innerHTML = `<p class="hint" style="color:var(--red)">불러오기 실패: ${esc(e.message)}</p>`; return null; }
    if (!data || !data.raceKey) {
      if (el) el.innerHTML = '<p class="hint">확장에서 수집된 3종 배당이 없습니다. Chrome 확장 [⚡ 전체 자동 수집]을 먼저 실행하세요.</p>';
      return null;
    }
    if (data.waiting || !(data.quinella || []).length && !(data.exacta || []).length && !(data.trio || []).length) {
      if (el) el.innerHTML = `<p class="hint">🔄 새 경주 <b>${esc(data.raceKey)}</b> 데이터 대기중 — 확장에서 이 raceKey로 [전체 자동 수집]을 실행하세요.</p>`;
      return data;
    }
    renderTripleMatrices(data);
    return data;
  }

  /** 화면(분석/타임라인/매트릭스)·토글 초기화. clearActive=true면 활성 raceKey도 해제. */
  function _resetTripleView(clearActive) {
    ['#tripleAnalyzeReport', '#oddsTimeline', '#tripleMatrixReport'].forEach((s) => { const e = $(s); if (e) e.innerHTML = ''; });
    const cw = $('#tripleChartWrap'); if (cw) cw.classList.add('hidden');
    _elimToggle.clear(); _prevBetKey = null; _prevSignalKeys = null; _elimRaceKey = null; _tlRaceKey = null;
    if (clearActive) setActiveRaceKey(null);
  }

  // [4번] 🔄 새 경주 시작 — 현재 데이터 히스토리 보존 + 활성 초기화 + 새 raceKey 요청
  async function startNewRace() {
    if (!confirm('현재 경주 데이터를 히스토리에 보존하고 새 경주를 시작할까요?\n(이전 데이터는 [📜 히스토리 보기]에서 계속 확인할 수 있습니다.)')) return;
    const old = getActiveRaceKey();
    try {
      await fetch('/api/odds/triple/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) });
    } catch (_) { /* */ }
    _resetTripleView(true);
    const rk = prompt('새 경주의 raceKey를 입력하세요 (예: 2026-07-02 나고야 5경주).\n※ Chrome 확장 팝업의 raceKey 칸에도 동일하게 입력해야 합니다.', old || '');
    if (rk && rk.trim()) {
      setActiveRaceKey(rk.trim());
      notify(`🔄 새 경주 시작: ${rk.trim()} — 확장에서 동일 raceKey로 [전체 자동 수집] 하세요`, true);
    } else {
      setActiveRaceKey(null);
      notify('🔄 초기화 완료 — 다음 수집부터 새 경주로 자동 설정됩니다', true);
    }
  }

  // [3번] 📜 히스토리 보기 — 통계 탭의 경주별 히스토리 대시보드로 이동
  function showHistoryView() {
    activateTab('stats');
    try { loadHistoryList(); } catch (_) { /* */ }
    const sec = $('#histRaceList'); if (sec) sec.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  // ── [1번] 규칙기반 이상감지 분석 (현재 경주만) ──────
  async function analyzeTripleRules() {
    const el = $('#tripleAnalyzeReport'); if (!el) return;
    if (!el.innerHTML) el.innerHTML = '<p class="hint">이상감지 분석 중…</p>';
    const active = await reconcileActiveRace();   // [2] 새 경주 자동 전환 + 활성 확정
    let a;
    try {
      const body = active ? JSON.stringify({ raceKey: active }) : '{}';   // [1·3] 현재 경주만 분석
      a = await (await fetch('/api/odds/triple/analyze', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body })).json();
    } catch (e) { el.innerHTML = `<p class="hint" style="color:var(--red)">분석 실패: ${esc(e.message)}</p>`; return; }
    if (a && a.waiting) { el.innerHTML = `<p class="hint">🔄 새 경주 <b>${esc(a.raceKey || active || '')}</b> 데이터 대기중 — 확장에서 이 raceKey로 [전체 자동 수집]을 실행하세요.</p>`; return; }
    if (!a || a.error) { el.innerHTML = `<p class="hint">${esc((a && a.error) || '수집된 3종 배당이 없습니다.')}</p>`; return; }
    if (!getActiveRaceKey() && a.raceKey) setActiveRaceKey(a.raceKey);   // 최초 진입 시 활성 설정
    if (a.raceKey) setAnomalyPanelRace(a.raceKey);   // [보완#1·2] 좌하단 누적 패널을 이 경주로(경주별 분리)
    renderTripleAnalyze(a);
  }

  let _lastTripleAnalyze = null, _prevBetKey = null, _betUpdatedFlag = false;
  let _elimRaceKey = null;
  // [BMED 전략] 5전략 자동선택 + 기대환수율 + 보험용 매트릭스(정상 추천과 함께 선택)
  function renderBMED(b) {
    if (!b) return '';
    const bEl = document.querySelector('#tripleBudget');
    const budget = Math.max(0, parseInt((bEl && bEl.value) || '0', 10) || 0);
    const won = (n) => (Math.round(n / 100) * 100).toLocaleString();
    const sign = (n) => (n >= 0 ? '+' : '') + won(n);
    // 전략명 + 근거 [4번]
    const head = `<div style="font-size:15px;font-weight:800;color:#c4b5fd">📊 현재 적용 전략: ${esc(b.label || '-')}</div>
      <div class="hint" style="margin:2px 0 6px">근거: ${esc(b.reason || '')}${b.afterClose ? ' <span style="color:#8a94a6">· ⚠️ 마감 후(참고만)</span>' : ''}</div>`;
    // 기대 환수율 [3번] — 자동선택 전략 plan 기준
    let expBlock = '';
    if (b.plan && b.plan.length) {
      const best = budget > 0 && b.bestCaseRatio != null ? `<br>최선 시나리오: <b style="color:#38d39f">${sign(b.bestCaseRatio * budget - budget)}원</b>` : '';
      const worst = budget > 0 ? `<br>최악 시나리오: <b style="color:#f87171">${sign(-budget)}원</b> (커버 조합 모두 미적중)` : '';
      expBlock = `<div class="hint" style="margin:4px 0">기대 환수율: <b style="color:${(b.expectedReturn || 0) >= 100 ? '#38d39f' : '#ffd24f'}">${b.expectedReturn != null ? b.expectedReturn + '%' : '-'}</b>${b.returnRate != null ? ` · 보장 환수율 ${b.returnRate}%` : ''}${b.preserved ? ' <span style="color:#38d39f">(원금 보전)</span>' : ''}${best}${worst}</div>`;
    }
    // [2번] 보험용 추천 매트릭스 — 정상 추천과 구분해서 나란히 제시(조건 충족 시만)
    const ins = b.insurance || {};
    let insBlock = '';
    if (ins.active && ins.combos && ins.combos.length) {
      const rows = ins.combos.map((c) => {
        const stake = budget > 0 ? won(c.ratio * budget) : Math.round(c.ratio * 100) + '%';
        const pay = budget > 0 && c.payoutRatio != null ? won(c.payoutRatio * budget) : (c.payoutRatio != null ? c.payoutRatio + 'x' : '-');
        const diff = budget > 0 && c.payoutRatio != null ? ` <span style="color:${c.payoutRatio >= 1 ? '#38d39f' : '#f87171'}">(${sign(c.payoutRatio * budget - budget)})</span>` : '';
        return `<div style="margin:2px 0"><b style="color:#c4b5fd">${c.label}</b> ${c.combo[0]}+${c.combo[1]}(${c.odds}배): <b>${stake}${budget > 0 ? '원' : ''}</b> → 적중 시 ${pay}${budget > 0 ? '원' : ''}${diff}</div>`;
      }).join('');
      const sc = budget > 0 ? `<div class="hint" style="margin-top:4px">최선 <b style="color:#38d39f">${sign((ins.bestRatio - 1) * budget)}</b> · 중간 ${sign((ins.midRatio - 1) * budget)} · 최악 <b style="color:#f87171">${sign((ins.worstRatio - 1) * budget)}</b> · 기대환수 ${ins.expectedReturn != null ? ins.expectedReturn + '%' : '-'}</div>` : '';
      insBlock = `<div style="margin-top:8px;padding:8px;border:1px dashed #a78bfa;border-radius:7px;background:rgba(167,139,250,.08)">
        <div style="font-weight:800;color:#c4b5fd">🛡️ 보험용 추천 (BMED 보험형 · ${esc(ins.band || '')})</div>
        <div class="hint" style="margin:2px 0 5px">1착축 ${ins.anchor}번 · 1+2 최다 / 1+3 중간 / 1+4 최소 ${ins.preserved ? '<span style="color:#38d39f">· 원금 보전</span>' : '<span style="color:#ffb020">· 저배당(원금보전 제한)</span>'}</div>
        ${rows}${sc}
        <div class="hint" style="margin-top:4px;font-size:11px">✅ 정상 추천과 <b>둘 다 확인 후 선택</b>하세요. 보험용은 손실 최소화형입니다.</div></div>`;
    } else if (ins.conditions) {
      const conds = ins.conditions.map((c) => `<span class="chip" style="border-color:${c.ok ? '#38d39f' : '#8a94a6'};color:${c.ok ? '#38d39f' : '#8a94a6'}">${c.ok ? '✅' : '❌'} ${esc(c.label)}${c.value ? ' ' + c.value : ''}</span>`).join(' ');
      insBlock = `<div style="margin-top:8px;padding:8px;border:1px dashed #6b7280;border-radius:7px;background:rgba(107,114,128,.06)">
        <div class="hint">🛡️ 보험용 추천 조건 미충족 → <b>정상 추천${ins.alternate && ins.alternate !== '정상 추천' ? ' / BMED ' + esc(ins.alternate) : ''}</b> 사용${ins.altReason ? ` <span style="font-size:11px">(${esc(ins.altReason)})</span>` : ''}</div>
        <div style="margin-top:3px">${conds}</div></div>`;
    }
    return `<div style="margin:8px 0;padding:9px 11px;border:2px solid #8b5cf6;border-radius:8px;background:rgba(139,92,246,.08)">
      ${head}${expBlock}${insBlock}
      <div class="hint" style="margin-top:5px;font-size:11px">${esc(b.note || '')}${budget <= 0 ? ' · 예산 입력 시 금액 자동 계산' : ''}</div></div>`;
  }

  // [역배열 감지] 단승≠복승/쌍승 순서 → 추천 상단 특별 표시(4유형 + 역배열 감지말·조합)
  function renderInverse(inv) {
    if (!inv || !inv.detected) return '';
    const b = inv.banner || {};
    const kindColor = { '쌍승역전': '#ff8a8a', '복승불일치': '#ffd24f', '배당압축': '#7dd3fc', '초과급락': '#ff5c5c' };
    // 배너 상단부: 단승 1위 · 복승 최저 · 쌍승 역전
    const lines = [];
    if (b.refNo != null) lines.push(`${b.refLabel}: <b>${b.refNo}번</b>${b.refOdds != null ? ` (${b.refOdds}배)` : ''}`);
    if (b.favPair) lines.push(`복승 최저: <b>${b.favPair.join('+')}</b> (${b.favOdds}배) → ${b.favNormal ? '정상(단승1위 포함)' : '<span style="color:#ff8a8a">불일치(단승1위 빠짐)</span>'}`);
    if (b.reversal) { const r = b.reversal; lines.push(`쌍승 역전: <span style="color:#ff8a8a">${r.challenger}→${r.favorite} (${r.reverseExacta}) &lt; ${r.favorite}→${r.challenger} (${r.favoredExacta}) → 비정상</span>`); }
    // 유형별 목록
    const typeHtml = (inv.types || []).map((t) =>
      `<div style="margin:3px 0"><span class="chip" style="border-color:${kindColor[t.kind] || '#8a94a6'};color:${kindColor[t.kind] || '#ccc'}">${t.level} ${t.kind}</span> <span class="hint">${esc(t.text)}</span></div>`).join('');
    // [3번] 역배열 감지말 + 복승 역배열 조합(일반 추천과 구분)
    const invH = (inv.invHorses || []).map((h) => `<b style="color:#ff5c5c">${h}번</b>`).join(' · ');
    const invC = (inv.invCombos || []).map((c) => `<span class="chip chip-red">${c.combo.join('+')} <span class="hint">${c.odds}배</span></span>`).join(' ');
    const invBlock = (inv.invHorses && inv.invHorses.length) ? `<div style="margin:6px 0 2px;padding:6px 8px;background:rgba(255,92,92,.08);border-radius:6px">
      <div>⭐ <b style="color:#ff8a8a">역배열 감지말</b>: ${invH} <span class="hint">(배당 높아도 우선 노출)</span></div>
      ${invC ? `<div style="margin-top:4px"><span class="hint">복승 역배열</span> ${invC}</div>` : ''}</div>` : '';
    return `<div style="margin:8px 0;padding:9px 11px;border:2px solid #ff5c5c;border-radius:8px;background:rgba(255,92,92,.1)">
      <div style="font-size:15px;font-weight:800;color:#ff8a8a">🔄 역배열 감지!</div>
      <div class="hint" style="margin:3px 0 5px;line-height:1.7">${lines.join('<br>')}<br>→ <b style="color:#ffd24f">실질 유력마가 바뀌었을 가능성</b></div>
      ${typeHtml}${invBlock}</div>`;
  }

  function renderTripleAnalyze(a) {
    const el = $('#tripleAnalyzeReport'); if (!el) return;
    _lastTripleAnalyze = a;
    if (a.raceKey !== _elimRaceKey) { _elimToggle.clear(); _elimRaceKey = a.raceKey; } // 경주 바뀌면 수동 토글 초기화
    // [5번] 추천 조합 변경 감지
    const betKey = JSON.stringify((a.betRecommend || []).map((r) => r.combo));
    _betUpdatedFlag = (_prevBetKey !== null && betKey !== _prevBetKey);
    _prevBetKey = betKey;
    const drops = (a.drops || []).slice(0, 8).map((d) =>
      `<span class="chip ${d.pct < 0 ? 'chip-red' : 'chip-yellow'}">${d.combo[0]}-${d.combo[1]} ${d.prev}→${d.cur} ${d.pct < 0 ? '▼' : '▲'}${Math.abs(d.pct)}%</span>`).join(' ');
    const flips = (a.reversals || []).filter((r) => r.flipped).slice(0, 6).map((r) =>
      `<span class="chip chip-red">🔴 ${r.favored[0]}→${r.favored[1]} (${r.favoredOdds}&lt;${r.otherOdds})</span>`).join(' ');
    const ranks = (a.rankChanges || []).slice(0, 6).map((r) =>
      `<span class="chip">${r.combo[0]}-${r.combo[1]} ${r.prevRank}위→${r.curRank}위 (${r.delta > 0 ? '▲' : '▼'}${Math.abs(r.delta)})</span>`).join(' ');
    const keyH = (a.keyHorses || []).map((h) => `<b style="color:#4ea1ff">${h}</b>`).join(' · ');
    el.innerHTML = `
      <div class="matrix-title">🚨 이상감지 <span class="hint" style="font-weight:400">${esc(a.raceKey)} · ${a.baselineReset ? '⚠️ 기준값 재설정됨' : a.baselineSet ? '🎯 기준값 설정됨' : a.hasPrev ? '직전 대비' : '첫 수집(변동 없음)'}${a.minutesBefore != null && !a.afterClose ? ` · 마감 ${a.minutesBefore}분전` : ''}</span></div>
      ${a.baselineReset ? `<div style="margin:6px 0;padding:7px 9px;border-left:3px solid #ffd24f;background:rgba(255,210,79,.12);border-radius:6px;color:#ffd24f">⚠️ <b>비정상 변동폭 감지 → 기준값 재설정</b> — 이전 경주 배당 잔존 의심(95%+ 급락 다수). 이번 수집을 새 기준값으로 설정했습니다. <b>다음 수집부터 변동을 계산</b>합니다.</div>`
        : a.baselineSet ? `<div style="margin:6px 0;padding:7px 9px;border-left:3px solid #38bdf8;background:rgba(56,189,248,.1);border-radius:6px;color:#7dd3fc">🎯 <b>기준값 설정됨</b> — 새 경주 첫 수집입니다. 변동폭은 <b>다음 수집부터</b> 계산됩니다.</div>` : ''}
      ${a.afterClose ? `<div style="margin:6px 0;padding:7px 9px;border-left:3px solid #8a94a6;background:rgba(138,148,166,.14);border-radius:6px;color:#b8c0cc">⚠️ <b>마감 후 수집</b> — 발주(T-0) 이후 신호는 <b>참고만</b> 하세요. 급락이 있어도 <b>추천 조합·보험에는 반영되지 않습니다</b>(마감 전 기준 유지).</div>` : ''}
      ${a.marketCheck && a.marketCheck.diverged ? `<div style="margin:6px 0;padding:7px 9px;border-left:3px solid #ff5c5c;background:rgba(255,92,92,.12);border-radius:6px;color:#ff8a8a">⚠️ <b>배당판 불일치</b> — 추천 복승(${(a.marketCheck.mainPair || []).join('+')}=${a.marketCheck.mainOdds}배)이 <b>배당판 최저 인기 조합(${a.marketCheck.favPair.join('+')}=${a.marketCheck.favOdds}배)</b>과 다릅니다. 배당판을 초반에 못 끌어왔거나 전적 편중일 수 있어요 → <b>배당판 인기 조합을 추천에 추가</b>했습니다. 배당 재확인 권장.</div>` : ''}
      ${a.marketCheck && a.marketCheck.stale ? `<div style="margin:6px 0;padding:7px 9px;border-left:3px solid #ffb020;background:rgba(255,176,32,.12);border-radius:6px;color:#ffc862">⚠️ <b>배당 불안정</b> — 최저 복승도 ${a.marketCheck.favOdds}배(실자금 미형성/초반 미수집 의심). <b>배당판 새로고침 후 재수집</b> 권장. 현재 추천은 참고만.</div>` : ''}
      ${renderInverse(a.inverse)}
      <div style="font-size:15px;font-weight:700;margin:6px 0;color:#ffd24f">${esc(a.summary || '')}</div>
      ${drops ? `<div style="margin:6px 0"><span class="hint">📉 급락/변동</span><br>${drops}</div>` : ''}
      ${flips ? `<div style="margin:6px 0"><span class="hint">🔀 쌍승 역전</span><br>${flips}</div>` : ''}
      ${ranks ? `<div style="margin:6px 0"><span class="hint">📊 순위 변동</span><br>${ranks}</div>` : ''}
      <div style="margin:6px 0"><span class="hint">⭐ 유력마</span> ${keyH || '—'}${a.anomalyHorse != null ? ` <span class="hint">/ 이상감지말</span> <b style="color:#ff5c5c">${a.anomalyHorse}</b>` : ''}</div>
      ${renderSignalQuality(a.signalQuality)}
      ${renderEliminationHTML(a.elimination)}
      ${renderBetRecommend(a)}
      ${renderBMED(a.bmed)}
      ${renderFormGrades(a.form)}`;
    _attachElimHandlers();       // 제거↔후보 클릭 토글
    drawTripleChart(a.chart);
    updateOddsAlert(a);          // [1·4·5] 우상단 실시간 알림 + 소리 + 조합업데이트
    loadOddsTimeline(a.raceKey); // [3] 변동 타임라인
  }

  // ── [제거/후보] 배당+전적 복합 제거 판정 패널 ─────────────────────────
  //  각 말 클릭 → 제거↔후보 수동 전환(_elimToggle). 후보 기준 자동 조합 재생성.
  let _lastElim = null;
  const _elimToggle = new Set();
  function _elimKeep(h, tog) { const base = h.keep || h.override; return (tog || _elimToggle).has(h.no) ? !base : base; }
  //  [1번] tog: 수동 토글 집합. 생략 시 한국 통합패널이 쓰는 공용 _elimToggle(대화형).
  //  일본 통합패널은 읽기전용이라 빈 Set을 넘겨 한국 토글에 오염되지 않게 한다.
  function renderEliminationHTML(e, tog) {
    const T = tog || _elimToggle;
    _lastElim = e || null;
    if (!e || !(e.horses || []).length) return '<div id="elimPanel"></div>';
    const cand = e.horses.filter((h) => _elimKeep(h, T)).sort((a, b) => b.total - a.total);
    const elim = e.horses.filter((h) => !_elimKeep(h, T)).sort((a, b) => b.total - a.total);
    const oddsTxt = (h) => (h.oddsRepr != null ? h.oddsRepr + '배' : '미수집');
    const tierIcon = (h) => (h.override ? '⚠️' : (h.tier || h.verdict));
    const tierColor = (h) => (h.override ? '#f59e0b' : h.verdict === '🟢' ? '#38d39f' : '#ffd24f');
    const TIER_LABEL = { '⭐': '강력유력', '★': '유력', '△': '관찰' };
    const pct = (v) => (v != null ? v + '%' : '-');
    // [3번] 각 말별 확률/기대값 라인
    const probLine = (h) => `<span class="hint" style="font-size:11px;display:block;margin-top:2px">시장 ${pct(h.marketProb)} · 전적 ${pct(h.formProb)} · 통합 <b>${pct(h.combinedProb)}</b>${h.ev != null ? ` · 기대값 <b style="color:${h.ev >= 0 ? '#38d39f' : '#ff6b6b'}">${h.ev >= 0 ? '+' : ''}${h.ev}%</b>` : ''}</span>`;
    const candRows = cand.map((h) => `
      <div class="elim-row" data-no="${h.no}" title="클릭 → 제거로 전환" style="cursor:pointer;padding:5px 8px;border-left:3px solid ${tierColor(h)};margin:3px 0;background:rgba(255,255,255,.03);border-radius:4px">
        <div style="display:flex;gap:8px;align-items:center">
          <b style="font-size:15px;min-width:22px">${tierIcon(h)}</b>
          <b style="min-width:34px;color:#4ea1ff">${h.no}번</b>
          <span>${esc(h.name || '')}</span>
          ${h.tier ? `<span class="hint" style="color:${tierColor(h)}">${TIER_LABEL[h.tier] || ''}</span>` : ''}
          <span class="hint" style="margin-left:auto;text-align:right">배당 ${oddsTxt(h)} · 전적 ${h.formScore != null ? h.formScore : '<span style="color:#f59e0b">미수집</span>'} · 유력 ${h.favScore != null ? h.favScore : '-'} · 제거점수 ${h.total}${T.has(h.no) ? ' <span style="color:#4ea1ff">(수동)</span>' : ''}</span>
        </div>
        ${probLine(h)}
        ${h.override ? `<div style="color:#f59e0b;font-size:11px">⚠️ 제거대상이나 이변(${esc(h.overrideReason)})</div>` : ''}
      </div>`).join('');
    const elimRows = elim.map((h) => `
      <div class="elim-row" data-no="${h.no}" title="클릭 → 후보로 전환" style="cursor:pointer;padding:5px 8px;border-left:3px solid ${h.verdict === '🔴' ? '#ef4444' : '#ff9f43'};margin:3px 0;border-radius:4px;opacity:.85">
        <div><b>${h.verdict} ${h.no}번</b> ${esc(h.name || '')} <span class="hint">· ${esc(h.reason)}${T.has(h.no) ? ' <span style="color:#4ea1ff">(수동)</span>' : ''}</span></div>
        ${probLine(h)}
      </div>`).join('');
    const ab = [];
    if (cand.length >= 2) ab.push('복승 ' + [cand[0].no, cand[1].no].sort((x, y) => x - y).join('+'));
    if (cand.length >= 3) ab.push('삼복승 ' + [cand[0].no, cand[1].no, cand[2].no].sort((x, y) => x - y).join('+'));
    const abTxt = ab.map((s) => `<span class="chip" style="background:rgba(56,211,159,.15);border-color:#38d39f">${s}</span>`).join(' ');
    const formWarn = !e.formAvailable
      ? `<div class="hint" style="margin:2px 0 6px;padding:6px 8px;background:rgba(245,158,11,.12);border-left:3px solid #f59e0b;border-radius:6px;color:#f59e0b">⚠️ <b>전적 데이터 미수집</b> — 배당만으로 판단 중입니다. 확장 [전체 자동 수집] 시 출마표2 전적이 함께 수집되면 전적 보정이 반영됩니다. (F12 콘솔의 <code>[전적수집]</code> 로그 확인)</div>`
      : (e.formCount < e.horses.length
        ? `<div class="hint" style="margin:2px 0 6px;color:#f59e0b">⚠️ 일부 말만 전적 있음(${e.formCount}/${e.horses.length}두) — 나머지는 배당 기준</div>` : '');
    return `<div id="elimPanel" style="margin:8px 0;border:1px solid var(--border);border-radius:8px;padding:8px">
      <div class="matrix-title" style="font-size:14px">🧮 제거 분석 <span class="hint" style="font-weight:400">출전 ${e.horses.length}두 → 후보 ${cand.length}두 압축</span></div>
      <div class="hint" style="margin:2px 0 6px">말 클릭 시 제거↔후보 전환 · 제거점수(100기준 감점, 급락/쌍승 시 +가점) (70+🟢후보 / 50~69🟡관찰 / 30~49🟠제거권장 / ~29🔴확실제거) · ⭐강력유력(전적+배당+기수 모두우수) ★유력(2개+) △관찰(1개)</div>
      ${formWarn}
      <div style="font-weight:700;color:#38d39f;margin-top:4px">🟢 후보 ${cand.length}두</div>
      ${candRows || '<div class="hint">후보 없음</div>'}
      ${abTxt ? `<div style="margin:6px 0 2px"><span class="hint">🎯 후보 기준 자동 조합</span> ${abTxt}</div>` : ''}
      <div style="font-weight:700;color:#ef4444;margin-top:8px">🔴 제거 ${elim.length}두</div>
      ${elimRows || '<div class="hint">제거 대상 없음</div>'}
    </div>`;
  }
  //  [1번] 컨테이너별(제거분석은 한국·일본 통합패널에서도 재사용) 클릭 토글.
  //  id/데이터를 인자로 받아 한국(#koreaElimPanel)·일본(#jpElimPanel)에서도 동작하게 일반화.
  //  인자 없이 호출하면 기존 동작(#elimPanel, _lastElim) 그대로 유지(하위호환).
  function _attachElimHandlers(id, e) {
    id = id || 'elimPanel';
    const data = e || _lastElim;
    const p = document.getElementById(id); if (!p) return;
    p.querySelectorAll('.elim-row').forEach((r) => r.addEventListener('click', () => {
      const no = +r.dataset.no;
      if (_elimToggle.has(no)) _elimToggle.delete(no); else _elimToggle.add(no);
      // 패널만 재렌더(알림/차트 재실행 없이). 컨테이너별 고유 id를 유지한다.
      p.outerHTML = renderEliminationHTML(data).replace('id="elimPanel"', `id="${id}"`);
      _attachElimHandlers(id, data);
    }));
  }

  // ── [1·2·4번] 실시간 변동 알림 오버레이 + 소리 + 플래시 ──────────────
  const LEVEL_RANK = { '🌊': 4, '🔴': 3, '🟠': 2, '🔄': 2, '🟡': 1 };   // 🌊 대규모급락 = 최상위
  const lvColor = (l) => (l === '🌊' ? '#38bdf8' : l === '🔴' ? '#ef4444' : l === '🟠' ? '#ff9f43' : l === '🔄' ? '#a855f7' : '#ffd24f');
  let _prevSignalKeys = null;

  function ensureAlertOverlay() {
    let el = document.getElementById('oddsAlertOverlay');
    if (!el) {
      el = document.createElement('div');
      el.id = 'oddsAlertOverlay';
      el.style.cssText = 'position:fixed;top:70px;right:16px;width:330px;max-height:72vh;overflow:auto;z-index:9999;display:none';
      el.addEventListener('click', (e) => { if (e.target.id === 'oddsAlertClose') el.style.display = 'none'; });
      document.body.appendChild(el);
    }
    return el;
  }
  function _ac() { try { window._audioCtx = window._audioCtx || new (window.AudioContext || window.webkitAudioContext)(); return window._audioCtx; } catch (_) { return null; } }
  function _beep(freq, dur, when, type) {
    const ac = _ac(); if (!ac) return;
    const o = ac.createOscillator(), g = ac.createGain();
    o.type = type || 'sine'; o.frequency.value = freq; o.connect(g); g.connect(ac.destination);
    const t = ac.currentTime + when;
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.3, t + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur); o.start(t); o.stop(t + dur);
  }
  function playAlert(level) { // [4번] 심각도별 소리
    if (level === '🔴') [0, 0.15, 0.3].forEach((w) => _beep(1050, 0.12, w, 'square'));
    else if (level === '🟠') [0, 0.2].forEach((w) => _beep(900, 0.13, w, 'square'));
    else if (level === '🔄') { _beep(1400, 0.1, 0, 'sine'); _beep(700, 0.2, 0.12, 'sine'); }
    else if (level === '🟡') _beep(760, 0.15, 0, 'sine');
  }
  function flashScreen() {
    let f = document.getElementById('oddsFlash');
    if (!f) { f = document.createElement('div'); f.id = 'oddsFlash'; f.style.cssText = 'position:fixed;inset:0;background:rgba(239,68,68,.32);z-index:9998;pointer-events:none;opacity:0;transition:opacity .15s'; document.body.appendChild(f); }
    f.style.opacity = '1'; setTimeout(() => { f.style.opacity = '0'; }, 220);
  }
  function alertShell(snap, body, red, updated) {
    const t = snap && snap.time ? `⏱ ${esc(snap.time)} 수집` : '⏱ 실시간';
    const mb = snap && snap.minutes_before != null ? ` (발주 ${snap.minutes_before}분전)` : '';
    const badge = updated ? '<div style="color:#38d39f;font-weight:700;margin-top:4px">⚡ 새 이상감지 반영 → 조합 업데이트됨</div>' : '';
    return `<div style="background:#141c2b;border:2px solid ${red ? '#ef4444' : '#334155'};border-radius:10px;padding:10px;box-shadow:0 4px 20px rgba(0,0,0,.5)">
      <div style="display:flex;justify-content:space-between;align-items:center"><b>${t}${mb}</b><span id="oddsAlertClose" style="cursor:pointer;color:#8a94a6">✕</span></div>
      ${badge}${body}</div>`;
  }
  function updateOddsAlert(a) {
    const el = ensureAlertOverlay();
    const sigs = a.signals || [];
    if (!sigs.length) {
      el.style.display = 'block';
      el.innerHTML = alertShell(a.lastSnapshot, '<div class="hint" style="padding:6px 2px">변동 신호 없음 · 정상 범위</div>', false, _betUpdatedFlag);
      _prevSignalKeys = new Set();
      return;
    }
    const maxLvl = sigs.map((s) => s.level).sort((x, y) => (LEVEL_RANK[y] || 0) - (LEVEL_RANK[x] || 0))[0];
    const keys = new Set(sigs.map((s) => s.level + '|' + s.text));
    let hasNew = _prevSignalKeys === null;
    if (!hasNew) for (const k of keys) if (!_prevSignalKeys.has(k)) { hasNew = true; break; }
    _prevSignalKeys = keys;
    // [3번] 각 신호에 유효 시점 라벨(마감 N분전) + 마감 후 신호는 회색·참고만
    const body = sigs.map((s) => {
      const gray = !!s.afterClose;
      const ph = s.phase ? `<span class="hint" style="font-size:10px;margin-left:6px">🕒 ${esc(s.phase)}${gray ? '' : ' 신호'}</span>` : '';
      return `<div style="margin:6px 0;padding:6px 8px;border-left:3px solid ${gray ? '#8a94a6' : lvColor(s.level)};background:rgba(255,255,255,.04);${gray ? 'opacity:.6' : ''}">
      <div style="font-weight:700">${s.level} ${esc(s.text)}${ph}</div>
      <div class="hint" style="margin-top:2px">→ ${esc(s.note || s.detail || '')}</div></div>`;
    }).join('');
    el.style.display = 'block';
    el.innerHTML = alertShell(a.lastSnapshot, body, maxLvl === '🔴' && !a.afterClose, _betUpdatedFlag);
    // [1번] 마감 후 신호뿐이면 소리·플래시 생략(참고만) — 마감 전 신호에서만 경보
    const allAfter = sigs.every((s) => s.afterClose);
    if (hasNew && !allAfter) { playAlert(maxLvl); if (maxLvl === '🔴') flashScreen(); }
  }

  // [3번] 변동 히스토리 타임라인 (배당판 캡처 탭, 이상감지 진행 + 클릭 시 스냅샷 배당)
  //  [버그수정] 자동갱신(10초) 때마다 innerHTML을 재생성하면 사용자가 펼쳐 둔
  //  스냅샷 배당이 매번 닫혀 버렸다. 펼침 상태를 스냅샷 시각(고유키)으로 보존해
  //  갱신 후에도 그대로 유지한다. 경주가 바뀌면 초기화.
  let _tlRaceKey = null;
  const _tlExpanded = new Set();
  function _tlSnapDetail(s) {
    const q = Object.entries((s && s.quinella) || {}).sort((x, y) => x[1] - y[1])
      .slice(0, 12).map(([k, v]) => `${k}:${v}`).join(' · ');
    return '복승 ' + (q || '없음');
  }
  async function loadOddsTimeline(raceKey) {
    const el = $('#oddsTimeline'); if (!el || !raceKey) return;
    let d; try { d = await (await fetch('/api/history/get', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ raceKey }) })).json(); }
    catch (e) { el.innerHTML = ''; return; }   // 조회 실패 시 이전 경주 타임라인 잔상 제거
    if (!d || d.error || !(d.snapshots || []).length) { el.innerHTML = ''; return; }
    if (raceKey !== _tlRaceKey) { _tlExpanded.clear(); _tlRaceKey = raceKey; }
    const snaps = d.snapshots;
    const rows = snaps.map((s, i) => {
      const anom = (s.anomalies || []).map((x) => `<span class="chip chip-red">${esc(x)}</span>`).join(' ') || '<span class="hint">정상 범위</span>';
      const mb = s.minutes_before != null ? `(${s.minutes_before}분전)` : '';
      const key = s.time || String(i);
      const open = _tlExpanded.has(key);
      return `<div class="tl-row" data-i="${i}" data-key="${esc(key)}" style="cursor:pointer;padding:4px 6px;border-left:2px solid var(--border);margin-left:4px">
        <b>${esc(s.time || '')}</b> <span class="hint">${mb}</span> ${anom}</div>
        <div id="tl-snap-${i}" style="display:${open ? 'block' : 'none'};margin:2px 0 6px 14px" class="hint">${open ? esc(_tlSnapDetail(s)) : ''}</div>`;
    }).join('');
    el.innerHTML = `<div class="matrix-title" style="font-size:13px">🕒 배당 변동 타임라인 <span class="hint" style="font-weight:400">${snaps.length}회 수집</span></div>${rows}`;
    el.querySelectorAll('.tl-row').forEach((r) => r.addEventListener('click', () => {
      const i = r.dataset.i, key = r.dataset.key; const box = $(`#tl-snap-${i}`); if (!box) return;
      if (box.style.display === 'none') {
        box.textContent = _tlSnapDetail(snaps[i]); box.style.display = 'block'; _tlExpanded.add(key);
      } else { box.style.display = 'none'; _tlExpanded.delete(key); }
    }));
  }

  // [4번] 출마표2 전적 등급 표 (A/B/C/D)
  function renderFormGrades(form) {
    if (!form || !form.length) return '';
    const gc = { A: '#38d39f', B: '#4ea1ff', C: '#ffd24f', D: '#8a94a6' };
    const rows = form.map((h) => `<tr>
      <td><b style="color:${gc[h.grade] || '#fff'}">${h.grade}</b></td>
      <td>${h.no}</td><td>${esc(h.name || '')}</td><td>${esc(h.jockey || '')}</td>
      <td>${(h.recentPlacings || []).join('-') || '-'}</td>
      <td>${h.totalScore}</td>
      <td>${(h.flags || []).map((f) => `<span class="chip ${f.level === 'must' ? 'chip-red' : ''}">${esc(f.msg)}</span>`).join(' ')}</td>
    </tr>`).join('');
    return `<div class="matrix-title" style="font-size:13px">🏇 전적 등급 (출마표2)</div>
      <table class="data-table" style="margin-top:4px">
        <thead><tr><th>등급</th><th>마번</th><th>마명</th><th>기수</th><th>최근착순</th><th>점수</th><th>플래그</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  // [신호 품질 필터링] 초과 급락(집중도) + 상황별 가중치 + 적응형 통합 등급
  function renderSignalQuality(sq) {
    if (!sq) return '';
    const ex = sq.excess || {}, st = sq.situation || {}, ia = sq.integratedAdaptive || [];
    const horses = ex.horses || {};
    const rows = Object.keys(horses).map((no) => ({ no: +no, ...horses[no] }))
      .filter((h) => h.grade)   // 노이즈(초과≥0) 제외 → 진짜/약한 신호만
      .sort((a, b) => a.excess - b.excess);
    const stColor = /집중|대규모/.test(st.name || '') ? '#38bdf8' : st.name === '이상감지 다수' ? '#ff9f43' : '#8a94a6';
    const exRows = rows.length ? rows.map((h) => `<div style="margin:2px 0">
      <span class="chip ${h.grade === '🔴' ? 'chip-red' : ''}">${h.grade} ${h.no}번</span>
      <span class="hint">평균급락 ${h.avg}% · 초과 <b style="color:${h.grade === '🔴' ? '#ef4444' : '#ffd24f'}">${h.excess}%p</b> · ${h.combos}개 조합</span></div>`).join('')
      : `<div class="hint">시장 평균 대비 집중 급락 말 없음 — 전체 노이즈(시장 전반 급락)</div>`;
    const gc = { A: '#38d39f', B: '#4ea1ff', C: '#ffd24f', D: '#8a94a6' };
    const iaHtml = (st.signalSource === 'concentration' && ia.length)
      ? `<div class="hint" style="margin:6px 0 2px">🔀 <b>집중신호 가중 통합 등급</b>(대규모 급락 시 배당 대신 집중도 반영)</div>`
        + `<table class="data-table" style="margin-top:2px"><thead><tr><th>등급</th><th>마번</th><th>마명</th><th>전적</th><th>집중신호</th><th>통합</th></tr></thead><tbody>`
        + ia.slice(0, 8).map((h) => `<tr><td><b style="color:${gc[h.grade] || '#fff'}">${h.grade}</b></td><td>${h.no}</td><td>${esc(h.name || '')}</td><td>${h.formScore}</td><td>${h.signalScore}</td><td><b>${h.integrated}</b></td></tr>`).join('')
        + `</tbody></table>` : '';
    // [핵심 공식] 종합 신뢰도(초과40+역전35+불일치25) + 쌍승역전 + 복승불일치
    const conf = sq.signalConfidence || {}, ch = conf.horses || {};
    const confRows = Object.keys(ch).map((no) => ({ no: +no, ...ch[no] }))
      .filter((h) => h.grade).sort((a, b) => b.confidence - a.confidence);
    const confHtml = confRows.length ? `<div class="hint" style="margin:8px 0 2px">🎯 <b>종합 신뢰도</b>(초과급락40% + 쌍승역전35% + 복승불일치25%) — 70+ 🔴 강력 · 40~69 🟡 참고</div>`
      + `<table class="data-table" style="margin-top:2px"><thead><tr><th>신뢰도</th><th>마번</th><th>초과급락</th><th>쌍승역전</th><th>복승불일치</th></tr></thead><tbody>`
      + confRows.slice(0, 8).map((h) => `<tr><td><b style="color:${h.grade === '🔴' ? '#ef4444' : '#ffd24f'}">${h.grade} ${h.confidence}</b></td><td>${h.no}번</td><td>${h.excessScore}</td><td>${h.reversalScore}</td><td>${h.mismatchScore}</td></tr>`).join('')
      + `</tbody></table>` : '';
    const wx = sq.winExactaReversals || [];
    const wxMulti = wx.some((r) => r.multiRank);
    const wxHtml = wx.length ? `<div class="hint" style="margin:8px 0 2px">🔄 <b>쌍승 역전 감지</b>(단승 유력마 vs 쌍승 방향) — 역전비율 &lt;0.95 🟡 · &lt;0.80 🔴 · &lt;0.60 🔴🔴${wxMulti ? ' · <b>상위권 순위쌍 역전 포함</b>(강한 역전만)' : ''}</div>`
      + wx.slice(0, 5).map((r) => `<div style="margin:2px 0"><span class="chip ${/🔴/.test(r.level) ? 'chip-red' : ''}">${r.level} ${r.challenger}번</span>${r.multiRank ? ` <span class="chip" style="border-color:#a78bfa;color:#a78bfa">${r.favRank}·${r.chalRank}위간</span>` : ''} <span class="hint">${esc(r.text)}</span></div>`).join('') : '';
    const mm = sq.quinellaMismatch;
    const mmHtml = mm ? `<div class="hint" style="margin:8px 0 2px">⚠️ <b>복승 불일치 감지</b>(단승 예상 vs 실제 최저) — 1.2+ 🟡 · 1.5+ 🔴 · 2.0+ 🔴🔴</div>`
      + `<div style="margin:2px 0"><span class="chip ${/🔴/.test(mm.level) ? 'chip-red' : ''}">${mm.level} 불일치 ${mm.ratio}</span> <span class="hint">${esc(mm.text)}</span></div>` : '';
    // [2번 고도화] 급락속도·연속하락/반등·페이크·환급률
    const adv = sq.advanced || {};
    const advParts = [];
    (adv.velocity || []).slice(0, 3).forEach((v) => advParts.push(`<div style="margin:2px 0"><span class="chip ${v.level === '🔴' ? 'chip-red' : ''}">${v.level} 급락속도</span> <span class="hint">${v.combo[0]}+${v.combo[1]} 분당 <b>${v.speed}%</b> (${Math.abs(v.pct)}%/${v.minutes}분)</span></div>`));
    Object.values(adv.streaks || {}).forEach((s) => advParts.push(`<div style="margin:2px 0"><span class="chip ${s.type === '연속하락' ? 'chip-red' : ''}">${s.type === '연속하락' ? '🔴 연속하락 +20' : '🟡 단발반등 −15'}</span> <span class="hint">${s.combo[0]}+${s.combo[1]}</span></div>`));
    (adv.fakes || []).forEach((f) => advParts.push(`<div style="margin:2px 0"><span class="chip chip-yellow">⚠️ 페이크 의심</span> <span class="hint">${f.combo[0]}+${f.combo[1]} 급락후반등 (${f.seq.join('→')})</span></div>`));
    if (adv.overround && adv.overround.concentrated) advParts.push(`<div style="margin:2px 0"><span class="chip" style="border-color:#ff9f43;color:#ff9f43">🟠 자금집중</span> <span class="hint">상위 3조합이 전체의 <b>${Math.round(adv.overround.top3Share * 100)}%</b> 점유 (역수합 ${adv.overround.invSum})</span></div>`);
    const advHtml = advParts.length ? `<div class="hint" style="margin:8px 0 2px">⚡ <b>실시간 이상감지 고도화</b>(급락속도·연속성·페이크·자금집중)</div>${advParts.join('')}` : '';
    return `<div style="margin:8px 0;border:1px solid var(--border);border-radius:8px;padding:8px">
      <div class="matrix-title" style="font-size:14px">🎯 신호 품질 분석 <span class="hint" style="font-weight:400">노이즈 제거 · 자금 집중 감지</span></div>
      <div style="margin:3px 0"><span class="chip" style="border-color:${stColor};color:${stColor}">${esc(st.name || '일반')}</span> <span class="hint">가중치 전적 <b>${Math.round((st.formW || 0.5) * 100)}%</b> · 신호 <b>${Math.round((st.signalW || 0.5) * 100)}%</b> · ${esc(st.note || '')}</span></div>
      <div class="hint" style="margin:4px 0 2px">시장 전체 평균 급락 <b>${ex.overall != null ? ex.overall + '%' : '-'}</b> 대비 <b>초과 급락(집중도)</b> — 초과 5%p+ 🔴 진짜신호 · 0~5%p 🟡 약한신호 · 그 외 노이즈 제거</div>
      ${exRows}${confHtml}${wxHtml}${mmHtml}${advHtml}${iaHtml}</div>`;
  }

  // [버그2·3] 복승/삼복승 추천 + 예산 배분 금액 표
  function renderBetRecommend(a, budgetSel) {
    const recs = a.betRecommend || [];
    if (!recs.length) return '';
    const bEl = document.querySelector(budgetSel || '#tripleBudget');
    const budget = Math.max(0, parseInt((bEl && bEl.value) || '0', 10) || 0);
    const won = (n) => Math.round(n / 100) * 100; // 100원 단위 반올림
    const qc = { '상': '#38d39f', '중': '#ffd24f', '하': '#8a94a6' };
    const rows = recs.map((r) => {
      const amt = budget > 0 ? won(budget * (r.alloc || 0) / 100) : null;
      const kindColor = r.kind === '복승' ? '#4ea1ff' : '#38d39f';
      const tierTxt = r.signalTier
        ? ` <span class="hint" style="color:${/고배당/.test(r.signalTier) ? '#ffd24f' : /낮은/.test(r.signalTier) ? '#4ea1ff' : '#8a94a6'};font-weight:700">${esc(r.signalTier)}</span>`
        : '';
      const odTxt = (r.expOdds != null ? r.expOdds + '배'
        : (r.expOddsEst != null ? r.expOddsEst + `배<span class="hint">(${r.estRough ? '거친추정' : '추정'})</span>` : '<span class="hint">미수집</span>')) + tierTxt;
      const qTxt = r.signalQuality
        ? `<b style="color:${qc[r.signalQuality] || '#8a94a6'}">${esc(r.signalQuality)}</b>${r.signalReason ? `<br><span class="hint" style="font-size:10px">${esc(r.signalReason)}</span>` : ''}`
        : '<span class="hint">-</span>';
      const estBadge = r.estimated ? ' <span class="hint" style="font-size:10px;color:#a855f7">추정보험</span>' : '';
      const revBadge = r.reversalPick ? ' <span class="hint" style="font-size:10px;color:#f59e0b;font-weight:700">🔄역배열</span>' : '';
      return `<tr>
        <td><b style="color:${kindColor}">${esc(r.label)}</b>${estBadge}${revBadge}</td>
        <td style="font-weight:700">${r.combo.join('+')}</td>
        <td>${qTxt}</td>
        <td>${odTxt}</td>
        <td>${r.alloc || 0}%</td>
        <td>${amt != null ? amt.toLocaleString('ko-KR') + '원' : '<span class="hint">예산입력</span>'}</td>
      </tr>`;
    }).join('');
    const totalAlloc = recs.reduce((s, r) => s + (r.alloc || 0), 0);
    const totalAmt = budget > 0 ? won(budget * totalAlloc / 100) : null;
    const upd = _betUpdatedFlag ? ' <span style="color:#38d39f">⚡ 업데이트됨</span>' : '';
    return `<div class="matrix-title" style="font-size:13px">🎯 베팅 추천${upd} ${budget > 0 ? `<span class="hint" style="font-weight:400">예산 ${budget.toLocaleString('ko-KR')}원 배분</span>` : '<span class="hint" style="font-weight:400">(예산 입력 시 금액 자동계산)</span>'}</div>
      <table class="data-table" style="margin-top:4px">
        <thead><tr><th>종류</th><th>조합</th><th>신호품질</th><th>예상배당</th><th>배분</th><th>금액</th></tr></thead>
        <tbody>${rows}</tbody>
        ${totalAmt != null ? `<tfoot><tr><td colspan="4"></td><td><b>${totalAlloc}%</b></td><td><b>${totalAmt.toLocaleString('ko-KR')}원</b></td></tr></tfoot>` : ''}
      </table>`;
  }

  // [1번] 예산 입력칸에 재계산 리스너를 1회만 연결(중복 방지). 값 입력 시 통합패널 재렌더 → 금액 갱신.
  function _bindBudgetInput(sel, onChange) {
    const el = document.querySelector(sel);
    if (!el || el._budgetBound) return;
    el._budgetBound = true;
    el.addEventListener('input', onChange);
  }

  // ── [복기] 당시 분석 전체(전적점수·이상감지·제거/후보·베팅추천) + 결과·적중판정 ──
  //   서버가 히스토리 파일에 저장해 둔 analysis/review 블록을 그대로 재현한다.
  function renderReview(d) {
    const an = d.analysis;
    if (!an) return '<p class="hint" style="margin:6px 0">※ 당시 분석 데이터가 없습니다(구버전 수집분). 다음 경주부터 복기 정보가 표시됩니다.</p>';
    const parts = [];
    if ((an.keyHorses || []).length) {
      parts.push(`<div style="margin:6px 0"><b>⭐ 유력마</b> ${an.keyHorses.join(' · ')}${an.anomalyHorse != null ? ` <span class="hint">(이상감지말 ${an.anomalyHorse})</span>` : ''}</div>`);
    }
    const formHtml = renderFormGrades(an.form);
    if (formHtml) parts.push(formHtml);
    // 이상감지 신호 목록
    const sd = an.signalsDetail || [];
    if (sd.length) {
      const rows = sd.map((s) => `<div style="margin:2px 0"><span class="chip ${/🔴|🚨/.test(s.level || '') ? 'chip-red' : ''}">${esc(s.level || '')} ${esc(s.type || '')}</span> ${esc(s.text || '')}${s.detail ? ` <span class="hint">— ${esc(s.detail)}</span>` : ''}</div>`).join('');
      parts.push(`<div class="matrix-title" style="font-size:13px;margin-top:8px">🚨 이상감지 신호 (당시)</div>${rows}`);
    } else {
      parts.push('<div class="hint" style="margin-top:8px">🚨 이상감지 신호 없음 (당시)</div>');
    }
    // 제거/후보
    const elim = an.elimination || {};
    if ((elim.horses || []).length) {
      const cand = elim.horses.filter((h) => h.keep || h.override);
      const gone = elim.horses.filter((h) => !(h.keep || h.override));
      const chip = (h) => `<span class="chip">${esc(h.verdict || '')}${esc(h.tier || '')} ${h.no}번${h.total != null ? ` <span class="hint">${h.total}</span>` : ''}</span>`;
      parts.push(`<div class="matrix-title" style="font-size:13px;margin-top:8px">🧮 제거법 (당시) <span class="hint" style="font-weight:400">후보 ${cand.length} · 제거 ${gone.length}</span></div>
        <div style="margin:3px 0"><b>후보</b> ${cand.map(chip).join(' ') || '-'}</div>
        <div style="margin:3px 0"><b>제거</b> ${gone.map(chip).join(' ') || '-'}</div>`);
    }
    // 베팅 추천 + 최종추천
    const betHtml = renderBetRecommend({ betRecommend: an.betRecommend || [] });
    if (betHtml) parts.push(betHtml);
    const fr = an.final_recommend || {};
    const frList = [['복승 메인', fr.quinella_main], ['복승 보조', fr.quinella_sub], ['삼복승 메인', fr.trifecta_main]].filter((x) => x[1]);
    if (frList.length) parts.push(`<div style="margin:6px 0"><b>🎯 최종 추천</b> ${frList.map((x) => `${x[0]} <b>${esc(x[1])}</b>`).join(' · ')}</div>`);
    // 실제 결과 + 적중 여부 + 이상감지 자동 판정
    const rv = d.review;
    if (d.result) {
      const r = d.result;
      const resTxt = `1착 ${r['1st'] != null ? r['1st'] : '?'} · 2착 ${r['2nd'] != null ? r['2nd'] : '?'} · 3착 ${r['3rd'] != null ? r['3rd'] : '?'}`;
      let judge = '';
      if (rv) {
        const yn = (b) => (b ? '<span style="color:#38d39f">✅ 적중</span>' : '<span style="color:#f87171">❌ 미적중</span>');
        const sc = rv.signal_correct || [];
        judge = `<div style="margin-top:6px">
          <div>복승 추천: ${yn(rv.quinella_hit)}${rv.payouts && rv.payouts.quinella ? ` <span class="hint">${rv.payouts.quinella}배</span>` : ''}</div>
          <div>삼복승 추천: ${yn(rv.trifecta_hit)}${rv.payouts && rv.payouts.trifecta ? ` <span class="hint">${rv.payouts.trifecta}배</span>` : ''}</div>
          <div>제거법: ${yn(rv.elimination_correct)}</div>
          <div>전적 유력마: ${yn(rv.form_pick_hit)}${rv.form_pick != null ? ` <span class="hint">(${rv.form_pick}번)</span>` : ''}</div>
          <div style="margin-top:4px"><b>이상감지가 맞았나?</b> ${rv.anomaly_was_correct ? '<span style="color:#38d39f">✅ 예 — 급락 신호가 입상마를 예측</span>' : '<span style="color:#f87171">❌ 아니오</span>'}</div>
          ${sc.length ? `<div class="hint" style="margin-top:2px">${sc.map(esc).join('<br>')}</div>` : ''}
        </div>`;
      }
      parts.push(`<div class="matrix-title" style="font-size:13px;margin-top:8px">🏁 실제 결과 & 적중 판정</div>
        <div><b>${esc(resTxt)}</b></div>${judge}`);
    } else {
      parts.push('<div class="hint" style="margin-top:8px">🏁 결과 미입력 — 아래에서 입력하면 적중 판정이 자동 표시됩니다.</div>');
    }
    return `<div style="border:1px solid var(--border);border-radius:8px;padding:8px;margin-bottom:10px">
      <div class="matrix-title">📋 복기 — 당시 분석 전체 ${an.summary ? `<span class="hint" style="font-weight:400">${esc(an.summary)}</span>` : ''}</div>
      ${parts.join('')}</div>`;
  }

  // ── [5번] 배당 변동 히스토리 + 자동학습 UI ──────────────────────────
  async function loadHistoryList() {
    const el = $('#histRaceList'); if (!el) return;
    el.innerHTML = '<p class="hint">불러오는 중…</p>';
    let d; try { d = await (await fetch('/api/history/list')).json(); }
    catch (e) { el.innerHTML = `<p class="hint" style="color:var(--red)">${esc(e.message)}</p>`; return; }
    const races = d.races || [];
    if (!races.length) { el.innerHTML = '<p class="hint">저장된 히스토리가 없습니다. 확장에서 자동 수집하면 경주별로 쌓입니다.</p>'; return; }
    el.innerHTML = races.map((r) => `<div class="race-chip ${r.hasResult ? 'chip-done' : 'chip-todo'}" data-file="${esc(r.file)}" data-rk="${esc(r.raceKey || '')}" style="cursor:pointer;margin:3px 0;display:block">
      <b>${esc(r.race || r.raceKey || '')}</b> <span class="chip-page">${esc(r.date || '')} · ${r.snaps}스냅${r.hasResult ? ' · ✅결과' : ''}</span></div>`).join('');
    el.querySelectorAll('.race-chip').forEach((c) => c.addEventListener('click', () => openHistory(c.dataset.file, c.dataset.rk)));
  }

  async function openHistory(file, rk) {
    const el = $('#histTimeline'); if (!el) return;
    el.innerHTML = '<p class="hint">불러오는 중…</p>';
    let d; try { d = await (await fetch('/api/history/get', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file }) })).json(); }
    catch (e) { el.innerHTML = `<p class="hint" style="color:var(--red)">${esc(e.message)}</p>`; return; }
    if (d.error) { el.innerHTML = `<p class="hint">${esc(d.error)}</p>`; return; }
    const snaps = d.snapshots || [];
    const rows = snaps.map((s) => {
      const q = Object.entries(s.quinella || {}).sort((a, b) => a[1] - b[1]).slice(0, 5).map(([k, v]) => `${k}:${v}`).join(' · ');
      const anom = (s.anomalies || []).map((a) => `<span class="chip chip-red">${esc(a)}</span>`).join(' ');
      return `<tr><td>${esc(s.time || '')}</td><td>${s.minutes_before != null ? s.minutes_before + '분전' : '-'}</td><td>${esc(q)}</td><td>${anom}</td></tr>`;
    }).join('');
    const res = d.result ? `1착 ${d.result['1st'] || '?'} · 2착 ${d.result['2nd'] || '?'} · 3착 ${d.result['3rd'] || '?'}` : '미입력';
    const rkUse = rk || d.raceKey || '';
    el.innerHTML = `<div class="matrix-title">${esc(d.race || d.raceKey || '')} <span class="hint" style="font-weight:400">${esc(d.date || '')} · 결과: ${esc(res)}</span></div>
      ${renderReview(d)}
      <div class="matrix-title" style="font-size:13px;margin-top:8px">🕒 배당 변동 타임라인</div>
      <table class="data-table"><thead><tr><th>시각</th><th>발주전</th><th>복승 상위(낮은순)</th><th>이상감지</th></tr></thead><tbody>${rows}</tbody></table>
      <div class="cfg-row" style="margin-top:8px">
        <span class="hint">결과 입력:</span>
        <input id="resIn1" class="cfg-input" type="number" placeholder="1착" style="width:70px">
        <input id="resIn2" class="cfg-input" type="number" placeholder="2착" style="width:70px">
        <input id="resIn3" class="cfg-input" type="number" placeholder="3착" style="width:70px">
        <input id="resIn4" class="cfg-input" type="number" placeholder="4착" style="width:70px" title="삼복승 아깝게 미적중(추천 말 4착) 학습용">
        <span class="hint">4착=거의적중 학습</span>
        <label class="hint">투자금액(원)<br><input id="resStake" class="cfg-input" type="number" min="0" step="100" value="${_defaultStake()}" style="width:110px"></label>
        <label class="hint">실수령 배당금(원)<br><input id="resPayout" class="cfg-input" type="number" min="0" step="100" placeholder="적중 시 실수령액" style="width:130px"></label>
        <button id="resSaveBtn" class="btn btn-primary">결과 저장 + 학습</button>
      </div>
      <div id="resMsg" class="hint" style="margin-top:6px"></div>`;
    $('#resSaveBtn').addEventListener('click', () => recordResult(rkUse, file));
  }

  /** [#5] 기본 투자금액(정액) — 마지막 입력값을 localStorage에 기억(기본 1000원). */
  function _defaultStake() {
    const v = parseInt(localStorage.getItem('bmed_default_stake') || '1000', 10);
    return (v > 0) ? v : 1000;
  }

  async function recordResult(rk, file) {
    const g = (id) => parseInt(($(id) || {}).value, 10);
    const r1 = g('#resIn1'), r2 = g('#resIn2'), r3 = g('#resIn3'), r4 = g('#resIn4');
    if (!r1) { $('#resMsg').textContent = '최소 1착은 입력하세요.'; return; }
    const stake = parseInt(($('#resStake') || {}).value, 10) || 1000;
    if (stake > 0) localStorage.setItem('bmed_default_stake', String(stake));   // 기본값 기억
    const result = {}; if (r1) result['1st'] = r1; if (r2) result['2nd'] = r2; if (r3) result['3rd'] = r3;
    if (r4) result['4th'] = r4;   // [4착] 삼복승 아깝게 미적중 학습
    // [보완#3] 실수령 배당금(선택) — 입력 시 서버가 추정 대신 실제 손익 계산. 공란이면 확정배당 추정.
    const payoutRaw = ($('#resPayout') || {}).value;
    const payload = { raceKey: rk, result, stake };
    if (payoutRaw !== '' && payoutRaw != null && !isNaN(parseInt(payoutRaw, 10))) payload.payout = parseInt(payoutRaw, 10);
    let d; try { d = await (await fetch('/api/history/record-result', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })).json(); }
    catch (e) { $('#resMsg').textContent = '실패: ' + e.message; return; }
    if (d.error) { $('#resMsg').textContent = d.error; return; }
    const pnl = (d.record && d.record.pnl) || 0;
    const pnlTxt = pnl > 0 ? `<span style="color:#38d39f">+${pnl.toLocaleString()}원</span>`
      : pnl < 0 ? `<span style="color:#ff6b6b">${pnl.toLocaleString()}원</span>` : '±0원';
    const srcTxt = (d.record && d.record.payout_actual != null) ? ' <span class="hint">(실수령 반영)</span>' : ' <span class="hint">(확정배당 추정)</span>';
    $('#resMsg').innerHTML = `✅ 저장 완료 — 추천적중 ${d.record.was_hit ? 'O' : 'X'} · 급락적중 ${d.record.anomaly_was_correct ? 'O' : 'X'} · 손익 ${pnlTxt}${srcTxt}`;
    loadLearningStats(); loadHistoryList();
  }

  async function loadLearningStats() {
    const el = $('#learnDashboard'); if (!el) return;
    let d; try { d = await (await fetch('/api/learning/stats')).json(); }
    catch (e) { el.innerHTML = `<p class="hint">${esc(e.message)}</p>`; return; }
    // [2번] 부진마 역전 학습(전적 기반)도 함께 조회
    let up = null; try { up = await (await fetch('/api/learning/upset')).json(); } catch (_) { /* */ }
    // [4착] near-miss(추천 말 4착) 케이스 + 4착 빈번 말
    let nm = null; try { nm = await (await fetch('/api/learning/near-miss')).json(); } catch (_) { /* */ }
    // [전체데이터·패턴발견] 적중 경주 공통점 자동 발견 + 데이터 충분도
    let disc = null; try { disc = await (await fetch('/api/patterns/discovered')).json(); } catch (_) { /* */ }
    const s = d.stats || {};
    // [AI Phase1] AI 학습 데이터 현황 대시보드
    let ai = null; try { ai = await (await fetch('/api/ai-training/status')).json(); } catch (_) { /* */ }
    const card = (title, st) => `<div class="bet-box" style="display:inline-block;min-width:170px;margin:4px;vertical-align:top"><b>${title}</b><br>${(st && st.rate != null) ? `<span style="font-size:20px;color:#38d39f">${st.rate}%</span> <span class="hint">(${st.hit}/${st.n})</span>` : '<span class="hint">데이터 없음</span>'}</div>`;
    el.innerHTML = `<div style="margin-bottom:6px">학습 경주 수: <b>${d.count || 0}</b></div>
      ${renderAiDataStatus(ai)}
      ${renderProfitSummary(s.profit_summary)}
      ${renderCompareStats(s.compare_stats, s.integrated_weights)}
      ${card('추천 적중률', s.recommend_hit)}
      ${card('급락 감지 적중률', s.drop_anomaly)}
      ${card('쌍승 역전 적중률', s.reversal)}
      ${card('전적 유력마 적중률', s.form_pick)}
      ${card('제거 판정 적중률', s.elimination)}
      ${renderNearMissStats(s.near_miss, nm)}
      ${renderTrackMonthStats(s.by_track, s.by_month, s.by_strategy)}
      ${renderDiscoveredPatterns(disc)}
      ${renderPatternStats(s.pattern_stats)}
      ${renderDropTiming(s.drop_timing)}
      ${renderUpsetStats(up)}`;
  }

  // [AI Phase1·3·7번] AI 학습 준비 현황 대시보드(수집/고품질/목표 진행률/마일스톤/예상 일정)
  function renderAiDataStatus(ai) {
    if (!ai) return '';
    const pct = Math.max(0, Math.min(100, ai.progress || 0));
    const filled = Math.round(pct / 10);
    const bar = '█'.repeat(filled) + '░'.repeat(10 - filled);
    const ms = ai.milestones || {};
    const etaTxt = (m) => !m ? '-' : (m.reached ? '✅ 도달' : (m.eta_days != null ? `${m.eta_days}일 후` : '수집 시작 후 산출'));
    const pd = ms.pattern_discovery, mt = ms.model_training;
    return `<div style="margin:8px 0;padding:10px;border:2px solid #8b5cf6;border-radius:8px;background:rgba(139,92,246,.08)">
      <div class="matrix-title" style="font-size:14px;color:#c4b5fd">🤖 AI 학습 준비 현황</div>
      <div style="margin:4px 0">수집 완료: <b>${ai.collected || 0}</b>경주 · 고품질(AI학습용): <b style="color:#38d39f">${ai.high_quality || 0}</b>경주 (${ai.complete_pct || 0}%) · 평균 품질 <b>${ai.avg_quality || 0}</b>점</div>
      <div style="margin:4px 0">Phase 1: <span style="font-family:monospace;font-size:15px;letter-spacing:1px;color:#a78bfa">${bar}</span> <b style="color:${pct >= 50 ? '#38d39f' : '#ffd24f'}">${pct}%</b> <span class="hint">(목표 ${ai.target || 500}경주)</span></div>
      <div class="hint" style="margin-top:4px">🔎 패턴 발견 가능: <b>100경주</b> 후 (${etaTxt(pd)}) · 🧠 모델 학습 가능: <b>500경주</b> 후 (${etaTxt(mt)})</div>
      <div class="hint" style="margin-top:2px">일평균 ${ai.per_day || 0}경주(${ai.days_collected || 0}일) · 목표까지 ${ai.remaining || 0}경주 · 예상 완료 ${ai.eta_months != null ? '약 ' + ai.eta_months + (typeof ai.eta_months === 'number' && ai.eta_months < 3 ? '개월' : '개월') + ' 후' : '-'}</div>
      <div class="hint" style="font-size:11px;margin-top:2px">결과 입력 시 <code>data/ai_training/</code> 완전 저장(품질 80+ AI학습용) → <code>tools/export_ai_data.py</code> CSV/JSON 내보내기</div></div>`;
  }

  // [5번]·[전략성과] 경마장별·월별·전략별 적중률/수익 집계 표시
  function renderTrackMonthStats(byTrack, byMonth, byStrategy) {
    const has = (o) => o && Object.keys(o).length;
    if (!has(byTrack) && !has(byMonth) && !has(byStrategy)) return '';
    const won = (n) => (n || 0).toLocaleString() + '원';
    const col = (v) => (v >= 0 ? '#38d39f' : '#f87171');
    const rows = (o) => Object.entries(o).sort((a, b) => (b[1].n || 0) - (a[1].n || 0)).map(([k, v]) =>
      `<tr><td>${esc(k)}</td><td>${v.n}</td><td>${v.hit}</td><td><b>${v.rate != null ? v.rate + '%' : '-'}</b></td><td style="color:${col(v.profit)}">${won(v.profit)}</td></tr>`).join('');
    const tbl = (title, o) => has(o) ? `<div style="display:inline-block;vertical-align:top;margin:4px 10px 4px 0">
      <div class="hint" style="font-weight:700;margin-bottom:2px">${title}</div>
      <table class="data-table"><thead><tr><th>구분</th><th>경주</th><th>적중</th><th>적중률</th><th>손익</th></tr></thead><tbody>${rows(o)}</tbody></table></div>` : '';
    return `<div style="margin:8px 0"><div class="matrix-title" style="font-size:14px">📊 경마장별 · 월별 · 전략별 성과</div>${tbl('🏟️ 경마장별', byTrack)}${tbl('📅 월별', byMonth)}${tbl('🎯 BMED 전략별', byStrategy)}</div>`;
  }

  /** [비교학습] 이상감지 vs 전적 vs 최종 추천 적중률 + 통합 가중치 자동 조정 상태. */
  function renderCompareStats(cs, iw) {
    if (!cs) return '';
    const any = ['anomaly', 'form', 'final'].some((k) => cs[k] && cs[k].n);
    if (!any) return '';
    const cell = (title, st, color) => `<div class="bet-box" style="display:inline-block;min-width:180px;margin:4px;vertical-align:top">
      <b>${title}</b><br>${(st && st.rate != null)
        ? `<span style="font-size:22px;color:${color};font-weight:800">${st.rate}%</span> <span class="hint">(${st.hit}/${st.n}경주)</span>`
        : '<span class="hint">데이터 없음</span>'}</div>`;
    // [3번] 가중치 조정 상태
    let wNote = '';
    if (iw) {
      const fp = Math.round((iw.form || 0.4) * 100), ap = Math.round((iw.anomaly || 0.6) * 100);
      if (iw.adjusted) {
        wNote = `<div class="hint" style="margin:4px 0 8px;color:#38d39f">⚙️ <b>가중치 자동 조정됨</b> — 이상감지 <b>${ap}%</b> + 전적 <b>${fp}%</b> (표본 ${iw.sample}경주 · 적중률 비교 반영)</div>`;
      } else {
        wNote = `<div class="hint" style="margin:4px 0 8px">⚙️ 통합 가중치 이상감지 <b>${ap}%</b> + 전적 <b>${fp}%</b> <span style="color:#8a94a6">(기본값 · ${iw.sample || 0}/${iw.need || 50}경주 쌓이면 적중률 우세 쪽으로 자동 조정)</span></div>`;
      }
    }
    return `<div class="bet-box" style="display:block;margin:4px 0 10px">
      <b>🆚 이상감지 vs 전적 추천 적중률 비교</b> <span class="hint" style="font-weight:400">(복승 top2 정확 또는 삼복승 top3 정확 기준)</span>
      ${wNote}
      <div style="margin-top:2px">
        ${cell('🚨 이상감지 기반 추천', cs.anomaly, '#ff9f43')}
        ${cell('🏇 전적 기반 추천', cs.form, '#4ea1ff')}
        ${cell('🎯 최종 추천(블렌드)', cs.final, '#38d39f')}
      </div></div>`;
  }

  /** [4착] 아깝게 4착(추천 말 4착=거의 적중) 건수 + 4착 빈번 말(삼복승 보험픽 우선). */
  function renderNearMissStats(nmStat, nm) {
    const cnt = (nmStat && nmStat.n) || 0;
    const freq = (nm && nm.frequent) || [];
    if (!cnt && !freq.length) return '';
    const freqTxt = freq.length
      ? freq.slice(0, 8).map((f) => `<span class="chip">${esc(f.name)} <b>${f.count}회</b></span>`).join(' ')
      : '<span class="hint">아직 없음(2회+ 4착 시 표시)</span>';
    return `<div class="bet-box" style="display:block;margin:4px 0 10px">
      <b>🟡 삼복승 아깝게 4착 (거의 적중)</b> <span class="hint" style="font-weight:400">추천 말이 4착으로 아깝게 미적중한 케이스</span><br>
      <span style="font-size:18px;color:#ffd24f;font-weight:700">${cnt}건</span>${(nmStat && nmStat.trio_near) ? ` <span class="hint">(삼복승 근접 ${nmStat.trio_near}건)</span>` : ''}
      <div class="hint" style="margin-top:6px">🎯 <b>4착 빈번 말</b>(다음 경주 삼복승 보험픽 우선 고려): ${freqTxt}</div>
    </div>`;
  }

  /** [#5] 누적 손익 요약 카드 — 실제 투자금액 기반 순손익·ROI·적중. */
  function renderProfitSummary(ps) {
    if (!ps || !ps.bets) return '';
    const net = ps.net || 0;
    const color = net > 0 ? '#38d39f' : net < 0 ? '#ff6b6b' : '#8a94a6';
    const sign = net > 0 ? '+' : '';
    const roi = (ps.roi != null) ? `${ps.roi >= 0 ? '+' : ''}${ps.roi}%` : '-';
    return `<div class="bet-box" style="display:block;margin:4px 0 10px">
      <b>💰 누적 손익 <span class="hint" style="font-weight:400">(실제 투자금액 기준)</span></b><br>
      <span style="font-size:24px;color:${color};font-weight:800">${sign}${net.toLocaleString()}원</span>
      <span class="hint" style="margin-left:10px">ROI ${roi} · 투자합 ${(ps.staked || 0).toLocaleString()}원 · 적중 ${ps.wins || 0}/${ps.bets}경주</span>
    </div>`;
  }

  /** [전체데이터·패턴발견] 학습된 패턴 수 + 데이터 충분도(50경주 진행바) + 발견된 공통점 */
  function renderDiscoveredPatterns(disc) {
    if (!disc) return '';
    const n = disc.races_with_result || 0;
    const target = disc.target || 50;
    const suf = disc.sufficiency != null ? disc.sufficiency : Math.round(Math.min(1, n / target) * 100);
    const pats = disc.patterns || [];
    const bar = `<div style="background:#1e2330;border-radius:6px;height:14px;overflow:hidden;margin:4px 0">
      <div style="height:100%;width:${suf}%;background:linear-gradient(90deg,#4ea1ff,#38d39f);transition:width .3s"></div></div>`;
    const rows = pats.map((p) => {
      if (p.type === '전적점수') {
        return `<tr><td>🎯 전적점수</td><td colspan="2"><b>${esc(p.desc)}</b></td>
          <td style="text-align:center" class="hint">표본 ${p.support}</td></tr>`;
      }
      const color = (p.rate >= 65) ? '#38d39f' : (p.rate >= 50) ? '#ffd24f' : '#4ea1ff';
      const icon = p.type === '시점' ? '⏱' : '📈';
      return `<tr><td>${icon} ${esc(p.type)}</td><td><b>${esc(p.desc)}</b></td>
        <td style="text-align:center;color:${color};font-weight:700">${p.rate}%<br><span class="hint" style="font-weight:400">기준 ${p.baseline}% (+${p.lift}p)</span></td>
        <td style="text-align:center" class="hint">${p.hit}/${p.support}</td></tr>`;
    }).join('');
    const body = pats.length
      ? `<table class="data-table" style="width:100%"><thead><tr>
          <th>유형</th><th>발견된 공통점</th><th style="text-align:center">적중률</th><th style="text-align:center">표본</th>
        </tr></thead><tbody>${rows}</tbody></table>`
      : `<p class="hint">${esc(disc.note || `결과 입력 경주가 ${disc.min_races || 10}건 이상 쌓이면 적중 경주의 공통점을 자동 분석합니다.`)}</p>`;
    return `<div class="panel-card" style="margin-top:10px">
      <h3>🔎 자동 발견 패턴 <span class="hint" style="font-weight:400">(전체 데이터 기반)</span></h3>
      <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:6px">
        <div><span class="hint">학습된 패턴</span><br><span style="font-size:20px;color:#38d39f">${pats.length}</span> 개</div>
        <div style="flex:1;min-width:200px"><span class="hint">데이터 충분도 (${n}/${target}경주)</span>${bar}
          <span class="hint">${suf}%${suf >= 100 ? ' · 충분' : ` · ${target - n}경주 더 쌓이면 100%`}</span></div>
      </div>
      ${body}
      <p class="hint" style="margin-top:4px">매 분석마다 배당 타임라인(30초)·전적점수·이상감지·결과가 <code>data/analysis_log/</code>에 전부 저장되며, 결과 입력 시 적중 경주 공통점이 자동 갱신됩니다.</p></div>`;
  }

  /** [2번] 부진마 이변(역전) 조건별 적중률 표 + 최근 이변 사례.
   *  부진마 = 최근 5경주 평균 착순 ≥ threshold. 조건 동반 시 실제 입상(이변) 비율을 보여준다. */
  function renderUpsetStats(up) {
    if (!up) return '';
    const rows0 = (up.conditionRows || []).filter((r) => r.count > 0);
    const th = up.threshold != null ? up.threshold : 4.0;
    // 조건 이름 보기 좋게(기준선/조건없음은 뒤로)
    const label = { '급락동반': '급락 30%+ 동반', '이상감지동반': '복승 이상감지 동반', '조건없음': '조건 없음', '전체부진마': '전체 부진마(기준선)' };
    const order = ['급락동반', '이상감지동반', '조건없음', '전체부진마'];
    rows0.sort((a, b) => (order.indexOf(a.condition) - order.indexOf(b.condition)));
    const baseline = (up.condition_stats && up.condition_stats['전체부진마']) || null;
    const baseRate = baseline && baseline.count ? (baseline.hit / baseline.count * 100) : null;
    const rows = rows0.map((r) => {
      const rate = r.rate != null ? r.rate : 0;
      const color = rate >= 65 ? '#38d39f' : rate >= 40 ? '#ffd24f' : '#ff6b6b';
      // 기준선 대비 상승폭(전체 부진마 입상률보다 얼마나 높은가)
      const lift = (baseRate != null && r.condition !== '전체부진마') ? (rate - baseRate) : null;
      const liftTxt = lift != null ? ` <span class="hint">(기준 ${lift >= 0 ? '+' : ''}${lift.toFixed(1)}p)</span>` : '';
      return `<tr><td><b>${esc(label[r.condition] || r.condition)}</b></td>
        <td style="text-align:center">${r.count}</td><td style="text-align:center">${r.hit}</td>
        <td style="text-align:center;color:${color};font-weight:700">${r.rate}%${liftTxt}</td></tr>`;
    }).join('');
    const table = rows
      ? `<table class="data-table" style="width:100%"><thead><tr>
          <th>동반 조건</th><th style="text-align:center">부진마 수</th><th style="text-align:center">입상(이변)</th><th style="text-align:center">이변 적중률</th>
        </tr></thead><tbody>${rows}</tbody></table>`
      : `<p class="hint">아직 부진마 이변 표본이 없습니다. 전적(착순)이 있는 경주에 결과가 입력되면 자동 누적됩니다.</p>`;
    const cases = (up.patterns || []).slice(0, 8).map((p) =>
      `<div style="margin:2px 0">🐎 <b>${esc(p.race || '')}</b> ${p.horse_no}번 · 평균착순 ${p.recent_avg} → ${p.win_place}착 입상
        <span class="hint">[${(p.conditions || []).map(esc).join(', ') || '조건없음'}]${p.date ? ' · ' + esc(p.date) : ''}</span></div>`).join('');
    return `<div class="panel-card" style="margin-top:10px">
      <h3>🔥 부진마 이변 조건별 적중률</h3>
      <p class="hint" style="margin-top:0">부진마 = 최근 5경주 평균 착순 ${th} 이상. 각 조건을 동반한 부진마가 실제로 1~3착에 든(이변 성공) 비율입니다. 총 사례 ${up.total || 0}건.</p>
      ${table}
      ${cases ? `<div style="margin-top:8px"><b>최근 이변 사례</b>${cases}</div>` : ''}
      <p class="hint" style="margin-top:4px">‘기준 +Np’는 전체 부진마 평균 입상률 대비 상승폭 — 값이 클수록 그 조건이 이변을 예고하는 힘이 큽니다.</p></div>`;
  }

  /** [2번] 패턴별 적중률 표: 패턴 | 발생횟수 | 적중 | 적중률 (발생 많은 순) */
  function renderPatternStats(ps) {
    const entries = Object.entries(ps || {}).filter(([, v]) => (v && v.n));
    if (!entries.length) return '';
    entries.sort((a, b) => b[1].n - a[1].n);
    const rows = entries.map(([name, v]) => {
      const rate = v.rate != null ? v.rate : 0;
      const color = rate >= 65 ? '#38d39f' : rate >= 40 ? '#ffd24f' : '#ff6b6b';
      return `<tr><td><b>${esc(name)}</b></td><td style="text-align:center">${v.n}</td><td style="text-align:center">${v.hit}</td>
        <td style="text-align:center;color:${color};font-weight:700">${v.rate != null ? v.rate + '%' : '-'}</td></tr>`;
    }).join('');
    return `<div class="panel-card" style="margin-top:10px">
      <h3>🧠 이상감지 패턴별 적중률</h3>
      <table class="data-table" style="width:100%"><thead><tr>
        <th>패턴</th><th style="text-align:center">발생횟수</th><th style="text-align:center">적중</th><th style="text-align:center">적중률</th>
      </tr></thead><tbody>${rows}</tbody></table>
      <p class="hint" style="margin-top:4px">패턴이 많이 쌓일수록(발생 5회+) 분석 시 신뢰도·베팅 비중에 자동 반영됩니다.</p></div>`;
  }

  /** [3번] 시점별 급락 효과: 급락 발생 시점(T-N분)별 이상감지 적중률 */
  function renderDropTiming(dt) {
    const order = ['T-1분', 'T-2분', 'T-3분', 'T-5분', 'T-10분', 'T-10분+', '미상'];
    const entries = Object.entries(dt || {}).filter(([, v]) => (v && v.n));
    if (!entries.length) return '';
    entries.sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]));
    const rows = entries.map(([t, v]) => {
      const rate = v.rate != null ? v.rate : 0;
      const color = rate >= 65 ? '#38d39f' : rate >= 40 ? '#ffd24f' : '#ff6b6b';
      return `<tr><td><b>${esc(t)}</b></td><td style="text-align:center">${v.n}</td><td style="text-align:center">${v.hit}</td>
        <td style="text-align:center;color:${color};font-weight:700">${v.rate != null ? v.rate + '%' : '-'}</td></tr>`;
    }).join('');
    return `<div class="panel-card" style="margin-top:10px">
      <h3>⏱ 급락 발생 시점별 효과</h3>
      <table class="data-table" style="width:100%"><thead><tr>
        <th>발생 시점</th><th style="text-align:center">발생횟수</th><th style="text-align:center">적중</th><th style="text-align:center">적중률</th>
      </tr></thead><tbody>${rows}</tbody></table>
      <p class="hint" style="margin-top:4px">적중률 높은 시점의 급락일수록 신뢰도가 높습니다.</p></div>`;
  }

  /** [5번] 현재 경주 패턴 매칭 카드 (분석기 통합 화면용) */
  function renderPatternMatch(pm) {
    if (!pm || !(pm.patterns || []).length) return '';
    const conf = pm.confidence || {};
    const lvlColor = { '높음': '#38d39f', '보통': '#4ea1ff', '주의': '#ffd24f', '낮음': '#ff6b6b', '데이터부족': '#8a94a6' }[conf.level] || '#8a94a6';
    const tags = pm.patterns.map((p) => `<span class="tag" style="background:${lvlColor}22;color:${lvlColor};border:1px solid ${lvlColor}55;border-radius:5px;padding:1px 6px;margin-right:4px">${esc(p)}</span>`).join('');
    const matchRows = (pm.matched || []).filter((m) => m.n).map((m) =>
      `<div style="margin:2px 0"><b>${esc(m.pattern)}</b> — 과거 ${m.rate != null ? m.rate + '%' : '-'} <span class="hint">(${m.hit || 0}/${m.n})</span></div>`).join('');
    const confLine = conf.rate != null
      ? `종합 신뢰도 <b style="color:${lvlColor}">${conf.level} (${conf.rate}%, 표본 ${conf.n})</b>${pm.recommend ? ' · ✅ 베팅 권장' : ' · ⚠️ 보수적 접근'}`
      : `종합 신뢰도 <b style="color:${lvlColor}">데이터 부족</b> <span class="hint">(패턴별 5회+ 쌓이면 산출)</span>`;
    const adviceLine = (pm.betAdvice && pm.betAdvice.note)
      ? `<div style="margin-top:4px" class="hint">💡 ${esc(pm.betAdvice.note)}</div>` : '';
    return `<div class="matrix-title" style="font-size:13px;margin-top:8px">🧠 현재 경주 패턴 매칭</div>
      <div style="margin:4px 0">${tags}</div>
      <div style="margin:4px 0">${confLine}</div>
      ${matchRows}${adviceLine}`;
  }

  // [2번-A] 3종 시계열 차트: 복승·쌍승·삼복승 최인기 배당을 첫 수집=100% 로 정규화해 3줄 표시
  const TRIPLE_CHART = { 복승: '#4ea1ff', 쌍승: '#ffd24f', 삼복승: '#38d39f' };
  function drawTripleChart(chart) {
    const wrap = $('#tripleChartWrap'); const cv = $('#tripleChart');
    if (!wrap || !cv) return;
    const series = (chart && chart.series) || [];
    const times = (chart && chart.times) || [];
    const n = times.length;
    // 각 라인: 첫 유효값 대비 % (정규화). 2회 이상 데이터가 있어야 변동 의미.
    const lines = series.map((s) => {
      const base = (s.odds || []).find((v) => typeof v === 'number' && v > 0);
      const pts = [];
      (s.odds || []).forEach((v, i) => { if (typeof v === 'number' && v > 0 && base) pts.push({ i, rel: v / base * 100, raw: v }); });
      return { label: s.label, color: TRIPLE_CHART[s.label] || '#8a94a6', pts };
    }).filter((l) => l.pts.length);
    if (n < 2 || !lines.some((l) => l.pts.length >= 2)) { wrap.classList.add('hidden'); return; }
    wrap.classList.remove('hidden');

    const relAll = []; lines.forEach((l) => l.pts.forEach((p) => relAll.push(p.rel)));
    let minY = Math.min(...relAll), maxY = Math.max(...relAll);
    if (minY === maxY) { minY -= 5; maxY += 5; }
    const padY = (maxY - minY) * 0.12 || 2; minY -= padY; maxY += padY;
    const minX = 0, maxX = n - 1;

    const W = Math.max(320, wrap.clientWidth || 640), H = 220;
    const dpr = window.devicePixelRatio || 1;
    cv.width = W * dpr; cv.height = H * dpr; cv.style.width = W + 'px'; cv.style.height = H + 'px';
    const ctx = cv.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);
    const L = 48, R = 56, TT = 14, B = 26; const pw = W - L - R, ph = H - TT - B;
    const sx = (x) => L + (x - minX) / (maxX - minX || 1) * pw;
    const sy = (y) => TT + (1 - (y - minY) / (maxY - minY || 1)) * ph;

    // 격자 + Y축(%) + 100% 기준선
    ctx.strokeStyle = 'rgba(140,148,166,.2)'; ctx.fillStyle = '#8a94a6'; ctx.font = '11px sans-serif'; ctx.lineWidth = 1;
    for (let g = 0; g <= 4; g++) {
      const yy = TT + ph * g / 4; const yv = maxY - (maxY - minY) * g / 4;
      ctx.beginPath(); ctx.moveTo(L, yy); ctx.lineTo(L + pw, yy); ctx.stroke();
      ctx.fillText(yv.toFixed(0) + '%', 6, yy + 3);
    }
    if (minY < 100 && maxY > 100) {
      ctx.strokeStyle = 'rgba(140,148,166,.5)'; ctx.setLineDash([4, 3]);
      ctx.beginPath(); ctx.moveTo(L, sy(100)); ctx.lineTo(L + pw, sy(100)); ctx.stroke(); ctx.setLineDash([]);
    }
    for (let g = 0; g <= Math.min(maxX, 6); g++) {
      const i = Math.round(maxX * g / Math.min(maxX, 6));
      ctx.fillStyle = '#8a94a6'; ctx.fillText('#' + (i + 1), sx(i) - 6, H - 8);
    }

    const RED = '#ff5c5c';
    lines.forEach((l) => {
      for (let k = 1; k < l.pts.length; k++) {
        const a = l.pts[k - 1], b = l.pts[k];
        const rel = a.rel ? (a.rel - b.rel) / a.rel : 0; // >0 하락(배당 짧아짐=급락)
        ctx.strokeStyle = rel >= 0.06 ? RED : l.color; ctx.lineWidth = rel >= 0.06 ? 3.5 : 2.5;
        ctx.beginPath(); ctx.moveTo(sx(a.i), sy(a.rel)); ctx.lineTo(sx(b.i), sy(b.rel)); ctx.stroke();
      }
      ctx.fillStyle = l.color;
      l.pts.forEach((p) => { ctx.beginPath(); ctx.arc(sx(p.i), sy(p.rel), 2.5, 0, Math.PI * 2); ctx.fill(); });
      const lp = l.pts[l.pts.length - 1]; const fp = l.pts[0];
      const pct = Math.round(lp.rel - 100);
      ctx.font = 'bold 11px sans-serif';
      ctx.fillText(`${l.label} ${lp.raw}배(${pct >= 0 ? '+' : ''}${pct}%)`, Math.min(sx(lp.i) + 5, W - R - 2), sy(lp.rel) - 4);
    });
  }

  // [2번] 자동 갱신 (10초마다 3종 불러오기 + 이상감지)
  let _tripleAutoTimer = null;
  function toggleTripleAutoRefresh(on) {
    if (_tripleAutoTimer) { clearInterval(_tripleAutoTimer); _tripleAutoTimer = null; }
    if (on) {
      // [3번] 결과가 확장에서 자동 수신되면 서버 학습통계가 갱신됨 → 통계 탭도 함께 자동 갱신
      const tick = () => { loadTripleFromServer(); analyzeTripleRules(); loadLearningStats(); };
      tick();
      _tripleAutoTimer = setInterval(tick, 10000);
    }
  }

  function renderTripleMatrices(data) {
    const el = $('#tripleMatrixReport');
    const q = {}, x = {}; const nosSet = new Set();
    (data.quinella || []).forEach((c) => { const [a, b] = c.combo; if (a && b) { q[a < b ? `${a}|${b}` : `${b}|${a}`] = c.odds; nosSet.add(a); nosSet.add(b); } });
    (data.exacta || []).forEach((c) => { const [a, b] = c.combo; if (a && b) { x[`${a}>${b}`] = c.odds; nosSet.add(a); nosSet.add(b); } });
    (data.trio || []).forEach((c) => (c.combo || []).forEach((n) => nosSet.add(n)));
    // [버그1] 실제 등장 마번의 최소~최대를 "연속"으로 생성(최대 16).
    //  · 조합 등장 번호만 쓰면 중간 번호가 빠져 잘림 → 연속 채움
    //  · 1~최대로 강제하면 존재하지 않는 앞번호(예:1번)가 빈 열로 붙음 → 최소부터 시작
    const present = [...nosSet].filter((n) => n > 0 && n <= 16);
    const nos = [];
    if (present.length) { const lo = Math.min(...present), hi = Math.max(...present); for (let i = lo; i <= hi; i++) nos.push(i); }
    let html = `<div class="matrix-title">📥 확장 수집 3종 <span class="hint" style="font-weight:400">${esc(data.raceKey)} · 복승 ${(data.quinella || []).length}·쌍승 ${(data.exacta || []).length}·삼복승 ${(data.trio || []).length}</span></div>`;

    if (Object.keys(q).length) {
      const vals = Object.values(q).filter((v) => v > 0); const lo = Math.min(...vals), hi = Math.max(...vals);
      let head = '<tr><th class="corner">복승</th>' + nos.slice(0, -1).map((n) => `<th>${n}</th>`).join('') + '</tr>';
      let body = '';
      for (let r = 1; r < nos.length; r++) {
        const rn = nos[r]; let tds = '';
        for (let c = 0; c < r; c++) { const cn = nos[c]; const v = q[rn < cn ? `${rn}|${cn}` : `${cn}|${rn}`]; tds += v > 0 ? `<td class="cell" style="background:${heatColor(v, lo, hi)}" title="${rn}-${cn}">${v}</td>` : '<td class="empty">·</td>'; }
        body += `<tr><th>${rn}</th>${tds}<td class="diag">—</td></tr>`;
      }
      html += `<div class="matrix-title" style="font-size:13px">🎰 복승 매트릭스</div><div class="matrix-wrap"><table class="odds-matrix"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
    }
    if (Object.keys(x).length) {
      const vals = Object.values(x).filter((v) => v > 0); const lo = Math.min(...vals), hi = Math.max(...vals);
      let head = '<tr><th class="corner">쌍승 ↓→</th>' + nos.map((n) => `<th>${n}</th>`).join('') + '</tr>';
      let body = '';
      for (const rn of nos) {
        let tds = '';
        for (const cn of nos) { if (rn === cn) { tds += '<td class="diag">—</td>'; continue; } const v = x[`${rn}>${cn}`]; tds += v > 0 ? `<td class="cell" style="background:${heatColor(v, lo, hi)}" title="${rn}→${cn}">${v}</td>` : '<td class="empty">·</td>'; }
        body += `<tr><th>${rn}</th>${tds}</tr>`;
      }
      html += `<div class="matrix-title" style="font-size:13px">🔀 쌍승 매트릭스</div><div class="matrix-wrap"><table class="odds-matrix"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
    }
    const trio = (data.trio || []).filter((c) => c.odds > 0).sort((a, b) => a.odds - b.odds).slice(0, 12);
    if (trio.length) {
      const cards = trio.map((c) => `<div class="combo-card"><div class="cc-head"><span class="cc-type">삼복승 ${c.combo.join('-')}</span><span class="cc-odds">${c.odds}배</span></div></div>`).join('');
      html += `<div class="matrix-title" style="font-size:13px">🎲 삼복승 인기 상위 ${trio.length}</div><div class="combo-cards">${cards}</div>`;
    }
    el.innerHTML = html;
  }

  /** [2번] "마번 배당" 줄들을 파싱 → {마번: 배당}. 숫자 외 문자(번/배/콜론 등)는 구분자로 처리. */
  function parseQuickOdds(text) {
    const out = {};
    String(text || '').split(/[\n,;]+/).forEach((line) => {
      const t = line.replace(/[^\d. ]+/g, ' ').trim().split(/\s+/).filter(Boolean);
      if (t.length >= 2) {
        const no = parseInt(t[0], 10), v = parseFloat(t[1]);
        if (no > 0 && v > 0) out[no] = v;
      }
    });
    return out;
  }

  /** [2번] 빠른입력 제출 — Vision 없이 즉시 스냅샷 누적 + 실시간 갱신 */
  async function submitQuickOdds() {
    const odds = parseQuickOdds($('#quickOddsInput').value);
    const n = Object.keys(odds).length;
    if (!n) { toast('마번·배당을 한 줄에 하나씩 입력하세요. 예: 4 2.4'); return; }
    try {
      await accumulateOdds(odds, true);
      notify(`⌨️ 복승 빠른입력 ${state.oddsTrack.snaps}번째 누적 (${n}두)`, true);
      $('#quickOddsInput').value = '';
    } catch (e) { toast('빠른입력 실패: ' + e.message); }
  }

  /** [4번] 쌍승 빠른입력 파싱 — 한 줄 "A B ab ba" → {"A>B":ab, "B>A":ba}. ba 생략 가능. */
  function parseQuickPairs(text) {
    const out = {};
    String(text || '').split(/[\n;]+/).forEach((line) => {
      const t = line.replace(/[^\d. ]+/g, ' ').trim().split(/\s+/).filter(Boolean).map(Number);
      if (t.length >= 3) {
        const [a, b, ab, ba] = t;
        if (a > 0 && b > 0 && a !== b) {
          if (ab > 0) out[`${a}>${b}`] = ab;
          if (ba > 0) out[`${b}>${a}`] = ba;
        }
      }
    });
    return out;
  }

  /** [4번] 쌍승 빠른입력 제출 — 즉시 쌍승 시계열 누적 */
  function submitQuickPairs() {
    const pairs = parseQuickPairs($('#quickPairsInput').value);
    const n = Object.keys(pairs).length;
    if (!n) { toast('쌍승은 한 줄에 "A B A→B배당 B→A배당" 형식으로. 예: 3 7 4.2 6.1'); return; }
    accumulateExacta(pairs);
    notify(`🔀 쌍승 빠른입력 ${state.oddsTrack.exTimes.length}번째 누적 (${n}쌍)`, true);
    $('#quickPairsInput').value = '';
  }

  /** [1-1] 복승 매트릭스 빠른입력 파싱 — 한 줄 "A B 배당" → {"A|B": 배당} (순서무관) */
  function parseQuickQuin(text) {
    const out = {};
    String(text || '').split(/[\n;]+/).forEach((line) => {
      const p = line.replace(/[^\d. ]+/g, ' ').trim().split(/\s+/).filter(Boolean);
      if (p.length >= 3) {
        const a = parseInt(p[0], 10), b = parseInt(p[1], 10), v = parseFloat(p[2]);
        if (a > 0 && b > 0 && a !== b && v > 0) out[a < b ? `${a}|${b}` : `${b}|${a}`] = v;
      }
    });
    return out;
  }

  /** [1-1] 복승 매트릭스 빠른입력 제출 — qSeries에 한 라운드 누적 + 매트릭스 갱신 */
  function submitQuickQuin() {
    const pairs = parseQuickQuin($('#quickQuinInput').value);
    const n = Object.keys(pairs).length;
    if (!n) { toast('복승은 한 줄에 "A B 배당" 형식으로. 예: 3 7 8.4'); return; }
    accumulateQuinellaPairs(pairs);
    if ($('#oddsMatrixHost')) renderOddsMatrix(state.oddsTrack._lastComputed);
    notify(`🎰 복승 매트릭스 빠른입력 ${state.oddsTrack.qTimes.length}번째 누적 (${n}쌍)`, true);
    $('#quickQuinInput').value = '';
  }

  /** 알림음 (WebAudio, 외부 파일 없이 비프 3회) */
  function beep() {
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      const ctx = new AC();
      [0, 0.7, 1.4].forEach((t) => {
        const o = ctx.createOscillator(), g = ctx.createGain();
        o.connect(g); g.connect(ctx.destination);
        o.type = 'sine'; o.frequency.value = 880;
        g.gain.setValueAtTime(0.0001, ctx.currentTime + t);
        g.gain.exponentialRampToValueAtTime(0.3, ctx.currentTime + t + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + t + 0.5);
        o.start(ctx.currentTime + t); o.stop(ctx.currentTime + t + 0.5);
      });
    } catch (_) { /* 오디오 미지원 무시 */ }
  }

  /** raceKey에 연결된 BMED 분석이 있으면 점수 포함, 없으면 마번만(드롭 기반) */
  function bmedHorsesFor(raceKey) {
    const rep = state.lastReports[raceKey];
    if (rep && (rep.horses || []).length) {
      return rep.horses.map((h) => ({ no: h.no, name: h.name, score: h.score }));
    }
    return [...state.oddsTrack.nos].sort((a, b) => a - b).map((no) => ({ no, name: '', score: 0 }));
  }

  /** 신호 점수 → 신호등 색. 높을수록 강한 이상신호(매수). */
  function sigColor(s) { return s >= 75 ? '🔴' : s >= 60 ? '🟠' : s >= 45 ? '🟡' : '🟢'; }

  // ---------- 베팅 평탄화 (History 판정용) ----------
  function flattenBets(report) {
    const bd = report.betting_recommend || {};
    const out = [];
    (bd.quinella || []).forEach((b) => out.push({ type: '복승', combo: b.combo, confidence: b.confidence }));
    (bd.trifecta || []).forEach((b) => out.push({ type: '삼복승', combo: b.combo, confidence: b.confidence }));
    return out;
  }

  // ---------- 리포트 렌더 ----------
  function renderReport(targetSel, report, region, title) {
    const el = $(targetSel); el.innerHTML = '';

    // 요약
    add(el, 'panel-card', `<h2>${esc(title)}</h2><p class="hint">${esc(report.race_summary || '')}</p>`);

    // 등급 픽 (A/B/C/D, 비중 45:28:17:10)
    const gp = report.grade_picks || {};
    const gradeCards = ['A', 'B', 'C', 'D'].map((g) => {
      const p = gp[g] || {};
      return `<div class="stat-card" style="text-align:left">
        <div><span class="grade-badge grade-${g}">${g}</span> <strong>${esc(p.name || '-')}</strong>
        <span class="meta">${p.no ? p.no + '번' : ''}</span></div>
        <div class="label" style="margin-top:6px">비중 ${GRADE_WEIGHT[g]}%</div>
        <div class="reason" style="font-size:12px;margin-top:4px">${esc(p.reason || '')}</div>
      </div>`;
    }).join('');
    add(el, 'panel-card', `<h3>등급 분류 (A/B/C/D · 비중 45:28:17:10)</h3><div class="stat-grid">${gradeCards}</div>`);

    // 말 카드 (점수순)
    const horses = (report.horses || []).slice().sort((a, b) => b.score - a.score);
    const topNo = horses[0]?.no;
    const wrap = document.createElement('div'); wrap.className = 'report-area';
    horses.forEach((h) => {
      const card = document.createElement('div');
      card.className = 'horse-card' + (h.no === topNo ? ' top-pick' : '');
      const badge = h.grade ? `<span class="grade-badge grade-${h.grade}">${h.grade}</span> ` : '';
      card.innerHTML = `
        <div class="horse-head">
          <div>${badge}<span class="horse-no">${h.no}</span><strong>${esc(h.name)}</strong></div>
          <div class="score">${h.score}</div>
        </div>
        <div class="meta">기수: ${esc(h.jockey || '미상')}</div>
        <div class="reason">${esc(h.reason || '')}</div>
        ${h.evidence ? `<div class="evidence">📄 ${esc(h.evidence)}</div>` : ''}`;
      wrap.appendChild(card);
    });
    el.appendChild(wrap);

    // 2착 패턴 + 특이사항
    const p2 = (report.pattern2_horses || []);
    add(el, 'bet-box', `
      <h3>🎯 2착 패턴 / 특이사항</h3>
      <div class="bet-line"><span class="bet-type">2착패턴마</span><span>${p2.length ? p2.map(esc).join(', ') : '없음'}</span></div>
      ${report.special_notes ? `<div class="bet-line"><span class="bet-type">특이사항</span><span>${esc(report.special_notes)}</span></div>` : ''}`);

    // 베팅 추천 (복승 / 삼복승)
    const bd = report.betting_recommend || {};
    const betLines = (arr, label) => (arr || []).map((b) =>
      `<div class="bet-line"><span><span class="bet-type">${label}</span> ${(b.combo || []).join('-')}</span>
       <span>신뢰도 ${b.confidence}% · ${esc(b.note || '')}</span></div>`).join('') ||
      `<div class="bet-line"><span class="bet-type">${label}</span><span>추천 없음</span></div>`;
    add(el, 'bet-box', `<h3>💰 베팅 추천 (복승 / 삼복승 · 단승 없음)</h3>
      ${betLines(bd.quinella, '복승')}${betLines(bd.trifecta, '삼복승')}`);

    // 종합 분석
    if (report.analysis) add(el, 'panel-card', `<h3>종합 분석</h3><p class="hint">${esc(report.analysis)}</p>`);
  }

  function add(parent, cls, html) { const d = document.createElement('div'); d.className = cls; d.innerHTML = html; parent.appendChild(d); }

  // ---------- 전적 자동 점수 (Phase 3) ----------
  /** 추출된 출전마 → /api/score 호출 → 점수·등급 패널 렌더 */
  async function renderFormScores(race, sheet) {
    const raceCtx = { distance: (race && race.distance) || '', course: '', grade: '' };
    const horses = (sheet.horses || []).map((h) => ({
      no: h.horseNum, name: h.horseName, jockey: h.jockey,
      recentPlacings: h.recentPlacings || [],
      jockey3mPlaceRate: (state.jockeyStats[h.jockey] || {}).placeRate,
    }));
    if (!horses.length) return null;
    const res = await Analysis.scoreHorses(raceCtx, horses);
    renderFormScorePanel(res.horses || []);
    return res.horses || [];
  }

  function renderFormScorePanel(scored) {
    const host = $('#koreaReport');
    const badge = (g) => `<span class="grade-badge grade-${g}">${g}</span>`;
    const rows = scored.slice().sort((a, b) => b.totalScore - a.totalScore).map((h) => {
      const flags = (h.flags || []).map((f) => `<span class="flag flag-${f.level}">${esc(f.msg)}</span>`).join(' ');
      const up = h.grade !== h.gradeBase ? ` <span class="hint">(${h.gradeBase}→${h.grade})</span>` : '';
      return `<tr>
        <td>${h.no}</td><td>${esc(h.name)}</td><td>${esc(h.jockey || '')}</td>
        <td>${(h.recentPlacings || []).join('·') || '-'}</td>
        <td>${h.baseScore}</td><td>${h.courseBonus ? '+' + h.courseBonus : '-'}</td>
        <td>${h.jockeyBonus ? '+' + h.jockeyBonus : '-'}</td>
        <td><b>${h.totalScore}</b></td>
        <td>${badge(h.grade)}${up}</td>
        <td>${flags || ''}</td>
      </tr>`;
    }).join('');
    const el = document.createElement('div');
    el.className = 'panel-card';
    el.innerHTML = `<h3>📊 전적 자동 점수 · 등급 (Phase 3)</h3>
      <p class="hint">전적 가중평균(3-1) + 코스적성(3-2) + 기수보너스(3-3) → 총점 · 사분위 등급(3-5, 이상감지 보정 포함) · 특수플래그(3-4)</p>
      <table class="data-table">
        <thead><tr><th>마번</th><th>마명</th><th>기수</th><th>최근착순</th><th>전적</th><th>코스</th><th>기수</th><th>총점</th><th>등급</th><th>플래그</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
    host.appendChild(el);
  }

  // ---------- [통합분석] 한국경마 PDF 전적 + 배당판 자동 연결 ----------
  //  전적 초안(A/B/C/D) → 확장이 같은 경주번호로 배당 수집 → 자동 통합(전적40%+배당60%) 재조정.
  let _koreaOddsTimer = null, _koreaOddsTitle = null;

  function stopKoreaOddsWatch() {
    if (_koreaOddsTimer) { clearInterval(_koreaOddsTimer); _koreaOddsTimer = null; }
  }

  function koreaRaceParts(title) {
    const m = String(title || '').match(/(\d+)\s*(?:R|경주|레이스)/i);
    const num = m ? parseInt(m[1], 10) : null;
    let venue = '';
    ['서울', '부산경남', '부경', '부산', '제주'].forEach((v) => { if (!venue && String(title).includes(v)) venue = v; });
    return { venue, num };
  }

  /** 전적 초안 저장 후 배당판 자동 연결 감시 시작(경주 전환 시 교체) */
  async function wireKoreaOdds(title, race, scored) {
    stopKoreaOddsWatch();
    _elimToggle.clear();   // [1번] 경주가 바뀌면 제거분석 수동 토글 초기화(경주별 독립)
    _koreaOddsTitle = title;
    const form = (scored || []).map((h) => ({
      no: h.no, name: h.name, jockey: h.jockey || '',
      formScore: h.totalScore, recentPlacings: h.recentPlacings || [],
    }));
    state.koreaScored[title] = { race, form };
    state.koreaOddsPrev[title] = state.koreaOddsPrev[title] || new Set();
    state.koreaTimeline[title] = state.koreaTimeline[title] || [];
    setKoreaOddsStatus('waiting', title);
    $('#koreaIntegrated').innerHTML = '';
    renderKoreaTimeline(title);
    const tick = () => pollKoreaOdds(title, race, form);
    await tick();
    // [2번] 30초 간격 수집·변동 감시 (확장 [전체 자동 수집] 주기와 정렬)
    _koreaOddsTimer = setInterval(tick, 30000);
  }

  /** 배당 raceKey 자동 매칭 → 전적 저장 → 통합분석 렌더 + 변동 알림/타임라인/히스토리 */
  async function pollKoreaOdds(title, race, form) {
    if (_koreaOddsTitle !== title) return;   // 다른 경주로 이동됨 → 중단
    let m;
    try {
      m = await (await fetch('/api/odds/triple/match', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title }),
      })).json();
    } catch (_) { return; }
    if (_koreaOddsTitle !== title) return;
    if (!m || !m.matched) {
      const noMatch = m && m.reason === 'no_match' && (m.candidates || []).length;
      setKoreaOddsStatus(noMatch ? 'nomatch' : 'waiting', title, m);
      return;
    }
    try {
      // 매칭된 raceKey로 한글 전적 저장 → 그 raceKey의 통합분석(전적+배당) 재요청
      await fetch('/api/korea/form', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ raceKey: m.raceKey, horses: form }),
      });
      const a = await (await fetch('/api/odds/triple/analyze', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ raceKey: m.raceKey }),
      })).json();
      if (_koreaOddsTitle !== title) return;
      if (a && !a.error && !a.waiting) {
        const firstLink = !state.koreaTimeline[title] || !state.koreaTimeline[title].length;
        setKoreaOddsStatus('linked', title, { raceKey: m.raceKey });
        setAnomalyPanelRace(m.raceKey);   // [보완#1] 한국경마도 좌하단 누적 패널에 영구 표시(경주별 분리)
        renderKoreaIntegrated(a);
        onKoreaOddsUpdate(title, race, m.raceKey, a, firstLink);   // [2·3·4번]
      }
    } catch (e) { console.warn('[통합분석] 실패', e); }
  }

  /** [2·3·4번] 통합분석 갱신 시: 신규 변동 감지 → 토스트+소리 / 타임라인 누적 / 히스토리 저장 */
  function onKoreaOddsUpdate(title, race, raceKey, a, firstLink) {
    const now = new Date();
    const hhmmss = now.toTimeString().slice(0, 8);
    const signals = (a.signals || []).filter((s) => s.type === '급락' || s.type === '역전' || s.type === '대규모급락');
    const prev = state.koreaOddsPrev[title] || new Set();
    const fresh = signals.filter((s) => !prev.has(s.text));   // 직전에 없던 신규 변동만

    // [2번] 신규 변동 → 우상단 토스트 + 심각도별 소리
    if (fresh.length && !firstLink) {
      const { venue, num } = koreaRaceParts(title);
      const head = `⏱ ${venue || ''} ${num != null ? num + 'R' : ''} 배당 변동`.trim();
      const lines = fresh.map((s) => `${s.level} ${esc(s.text)}${/급락/.test(s.type) && s.level === '🔴' ? ' 급락!' : ''}`);
      oddsToast(head, lines);
      // 가장 심각한 레벨로 경고음 (🔴 3회 / 🟠 2회 / 🟡 1회)
      const worst = fresh.map((s) => s.level).sort((x, y) => sevRank(y) - sevRank(x))[0];
      try { playAlert(worst); } catch (_) {}
    }
    state.koreaOddsPrev[title] = new Set(signals.map((s) => s.text));

    // [3번] 타임라인 누적 (수집 1건 = 항목 1개)
    const tl = state.koreaTimeline[title] || (state.koreaTimeline[title] = []);
    tl.push({
      time: hhmmss, raceKey, changed: fresh.length > 0,
      signals: signals.map((s) => ({ level: s.level, text: s.text, detail: s.detail })),
      integrated: (a.integrated || []).map((h) => ({ no: h.no, name: h.name, grade: h.grade, odds: h.odds, integrated: h.integrated })),
      anomalyHorse: a.anomalyHorse,
    });
    if (tl.length > 200) tl.splice(0, tl.length - 200);
    renderKoreaTimeline(title);

    // [4번] 경주별 히스토리 저장(전적+배당타임라인+추천+이상감지)
    saveKoreaHistory(title, race, raceKey, a);
  }

  function sevRank(level) { return { '🌊': 4, '🔴': 3, '🟠': 2, '🟡': 1, '🔄': 1 }[level] || 0; }

  /** [4번] 통합 스냅샷을 data/korea_history 에 저장(디바운스) */
  let _koreaSaveTimer = null;
  function saveKoreaHistory(title, race, raceKey, a) {
    if (_koreaSaveTimer) clearTimeout(_koreaSaveTimer);
    _koreaSaveTimer = setTimeout(async () => {
      const { venue, num } = koreaRaceParts(title);
      const anomalies = (a.signals || []).filter((s) => s.type === '급락').map((s) => `${s.level} ${s.text}`);
      const payload = {
        date: (state.koreaSessionDate || todayStr()), venue: venue || race.venue || '',
        raceNo: num != null ? num : race.raceNo, raceKey, title,
        report: state.lastReports[title] || null,
        formScores: (state.koreaScored[title] || {}).form || null,
        integrated: a.integrated || [], recommend: a.betRecommend || [],
        anomalies, signals: a.signals || [], timeline: state.koreaTimeline[title] || [],
      };
      try {
        await fetch('/api/korea/history/save', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
      } catch (e) { console.warn('[히스토리 저장] 실패', e); }
    }, 1200);
  }

  /** [3번] 배당 변동 타임라인 렌더(클릭 시 해당 시점 통합배당판 표시) */
  function renderKoreaTimeline(title) {
    let host = $('#koreaTimeline');
    if (!host) {
      host = document.createElement('div'); host.id = 'koreaTimeline'; host.className = 'panel-card';
      $('#koreaIntegrated').insertAdjacentElement('afterend', host);
    }
    const tl = state.koreaTimeline[title] || [];
    if (!tl.length) {
      host.innerHTML = `<h3>⏱ 배당 변동 타임라인</h3><p class="hint">배당판이 수집되면 30초 간격으로 변동 내역이 누적됩니다.</p>`;
      return;
    }
    const rows = tl.map((e, i) => {
      const badge = e.changed
        ? e.signals.map((s) => s.level).join('')
        : '<span class="hint">변동 없음</span>';
      const summary = e.changed ? e.signals.map((s) => esc(s.text)).join(' · ') : '';
      return `<div class="tl-row" data-i="${i}" style="cursor:pointer;padding:4px 6px;border-left:3px solid ${e.changed ? '#f59e0b' : 'transparent'};border-radius:4px">
        <b>${e.time}</b> ${badge} <span class="hint">${summary}</span></div>`;
    }).reverse().join('');
    host.innerHTML = `<h3>⏱ 배당 변동 타임라인 <span class="hint" style="font-weight:400">(${tl.length}회 수집 · 클릭 시 해당 시점 배당판)</span></h3>
      <div style="max-height:220px;overflow:auto">${rows}</div>
      <div id="koreaTimelineSnap" style="margin-top:8px"></div>`;
    host.querySelectorAll('.tl-row').forEach((r) => r.addEventListener('click', () => showKoreaSnapshot(title, +r.dataset.i)));
  }

  function showKoreaSnapshot(title, i) {
    const e = (state.koreaTimeline[title] || [])[i]; if (!e) return;
    const snap = $('#koreaTimelineSnap'); if (!snap) return;
    const gc = { A: '#38d39f', B: '#4ea1ff', C: '#ffd24f', D: '#8a94a6' };
    const rows = (e.integrated || []).map((h) => `<tr>
      <td><b style="color:${gc[h.grade] || '#fff'}">${h.grade || '-'}</b></td>
      <td>${h.no}</td><td>${esc(h.name || '')}</td>
      <td>${h.odds != null ? h.odds + '배' : '-'}</td><td><b>${h.integrated != null ? h.integrated : '-'}</b></td>
    </tr>`).join('');
    const sig = e.signals.length ? e.signals.map((s) => `<div>${s.level} ${esc(s.text)} <span class="hint">${esc(s.detail || '')}</span></div>`).join('') : '<span class="hint">변동 없음</span>';
    snap.innerHTML = `<div class="matrix-title" style="font-size:13px">🕐 ${e.time} 시점 통합배당판 ${e.anomalyHorse != null ? `· 이상감지말 <b style="color:#ff5c5c">${e.anomalyHorse}</b>` : ''}</div>
      <table class="data-table" style="margin-top:4px"><thead><tr><th>등급</th><th>마번</th><th>마명</th><th>대표배당</th><th>통합</th></tr></thead><tbody>${rows}</tbody></table>
      <div style="margin-top:6px">${sig}</div>`;
  }

  // ---------- [5번] 경주 분석 히스토리 조회 (통계 탭) ----------
  function initKoreaHistory() {
    const rb = $('#koreaHistRefreshBtn'), bb = $('#koreaHistBackupBtn');
    if (rb) rb.addEventListener('click', loadKoreaHistoryList);
    if (bb) bb.addEventListener('click', async () => {
      bb.disabled = true; bb.textContent = '백업 중...';
      try { await fetch('/api/korea/backup', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ label: '경주 히스토리 백업' }) }); notify('☁️ GitHub 백업 요청됨(원격 설정 시 push)'); }
      catch (e) { notify('백업 실패: ' + e.message, false); }
      finally { bb.disabled = false; bb.textContent = '☁️ GitHub 백업'; }
    });
  }

  async function loadKoreaHistoryList() {
    const host = $('#koreaHistList'); if (!host) return;
    host.innerHTML = '<p class="hint">불러오는 중...</p>';
    let d;
    try { d = await (await fetch('/api/korea/history/list')).json(); }
    catch (e) { host.innerHTML = '<p class="hint">목록 로드 실패</p>'; return; }
    const items = d.items || [];
    if (!items.length) { host.innerHTML = '<p class="hint">저장된 경주 히스토리가 없습니다. 한국경마 분석 + 배당 연동 시 자동 저장됩니다.</p>'; return; }
    // 날짜 → 경마장 그룹
    const byDate = {};
    items.forEach((it) => { (byDate[it.date] = byDate[it.date] || []).push(it); });
    const html = Object.keys(byDate).sort().reverse().map((date) => {
      const rows = byDate[date].sort((a, b) => (a.venue || '').localeCompare(b.venue || '') || (a.raceNo || 0) - (b.raceNo || 0))
        .map((it) => `<div class="tl-row korea-hist-item" data-file="${esc(it.file)}" style="cursor:pointer;padding:5px 8px;border-radius:4px">
          <b>${esc(it.venue || '')} ${it.raceNo}R</b>
          ${it.hasResult ? '<span style="color:#38d39f">🏁</span>' : ''}
          ${it.anomalyCount ? `<span style="color:#f59e0b">⚡${it.anomalyCount}</span>` : ''}
          <span class="hint">${esc(it.savedAt || '')}</span>
        </div>`).join('');
      return `<div style="margin-bottom:10px"><div class="matrix-title" style="font-size:13px">📅 ${esc(date)}</div>${rows}</div>`;
    }).join('');
    host.innerHTML = html;
    host.querySelectorAll('.korea-hist-item').forEach((el) => el.addEventListener('click', () => {
      host.querySelectorAll('.korea-hist-item').forEach((x) => x.style.background = '');
      el.style.background = 'rgba(78,161,255,.15)';
      openKoreaHistory(el.dataset.file);
    }));
  }

  async function openKoreaHistory(file) {
    const host = $('#koreaHistDetail'); if (!host) return;
    host.innerHTML = '<p class="hint">불러오는 중...</p>';
    let d;
    try { d = await (await fetch('/api/korea/history/get', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file }) })).json(); }
    catch (e) { host.innerHTML = '<p class="hint">로드 실패</p>'; return; }
    if (d.error) { host.innerHTML = `<p class="hint">${esc(d.error)}</p>`; return; }
    const gc = { A: '#38d39f', B: '#4ea1ff', C: '#ffd24f', D: '#8a94a6' };

    // 전적 점수
    const fs = (d.formScores || []).slice().sort((a, b) => (b.totalScore || 0) - (a.totalScore || 0));
    const formHtml = fs.length ? `<div class="matrix-title" style="font-size:13px">📄 전적 점수</div>
      <table class="data-table"><thead><tr><th>마번</th><th>마명</th><th>기수</th><th>전적점수</th></tr></thead>
      <tbody>${fs.map((h) => `<tr><td>${h.no}</td><td>${esc(h.name || '')}</td><td>${esc(h.jockey || '')}</td><td><b>${h.formScore != null ? h.formScore : (h.totalScore != null ? h.totalScore : '-')}</b></td></tr>`).join('')}</tbody></table>` : '';

    // 통합 등급
    const integHtml = (d.integrated || []).length ? `<div class="matrix-title" style="font-size:13px;margin-top:8px">🎯 통합 등급 (전적40+배당60)</div>
      <table class="data-table"><thead><tr><th>등급</th><th>마번</th><th>마명</th><th>대표배당</th><th>통합</th></tr></thead>
      <tbody>${d.integrated.map((h) => `<tr><td><b style="color:${gc[h.grade] || '#fff'}">${h.grade || '-'}</b></td><td>${h.no}</td><td>${esc(h.name || '')}</td><td>${h.odds != null ? h.odds + '배' : '-'}</td><td><b>${h.integrated != null ? h.integrated : '-'}</b></td></tr>`).join('')}</tbody></table>` : '';

    // 배당 타임라인 그래프(최저 대표배당 추이) + 리스트
    const chart = koreaHistChartSVG(d.timeline || []);
    const tlRows = (d.timeline || []).map((e) => `<div style="padding:2px 4px;border-left:3px solid ${e.changed ? '#f59e0b' : 'transparent'}">
      <b>${esc(e.time || '')}</b> ${e.changed ? (e.signals || []).map((s) => `${s.level} ${esc(s.text)}`).join(' · ') : '<span class="hint">변동 없음</span>'}</div>`).reverse().join('');
    const tlHtml = (d.timeline || []).length ? `<div class="matrix-title" style="font-size:13px;margin-top:8px">⏱ 배당 변동 타임라인 (${d.timeline.length}회)</div>${chart}<div style="max-height:160px;overflow:auto;margin-top:6px">${tlRows}</div>` : '';

    // 이상감지
    const anoHtml = (d.anomalies || []).length ? `<div class="matrix-title" style="font-size:13px;margin-top:8px">⚠️ 이상감지 내역</div>${d.anomalies.map((a) => `<div>${esc(a)}</div>`).join('')}` : '';

    // 추천
    const rec = d.recommend || [];
    const recHtml = rec.length ? `<div class="matrix-title" style="font-size:13px;margin-top:8px">💰 최종 추천</div>
      <table class="data-table"><thead><tr><th>종류</th><th>조합</th><th>배분</th></tr></thead>
      <tbody>${rec.map((r) => `<tr><td>${esc(r.label || r.kind || '')}</td><td><b>${(r.combo || []).join('+')}</b></td><td>${r.alloc != null ? r.alloc + '%' : '-'}</td></tr>`).join('')}</tbody></table>` : '';

    // 실제 결과
    const resHtml = (d.result && d.result.length)
      ? `<div class="matrix-title" style="font-size:13px;margin-top:8px">🏁 실제 결과</div><div>착순: <b>${d.result.join(' → ')}</b></div>`
      : `<div style="margin-top:8px"><span class="hint">실제 결과 미입력</span> <button class="btn btn-small" id="koreaHistResultBtn">결과 입력</button></div>`;

    host.innerHTML = `<div class="panel-card">
      <h3>${esc(d.title || '')} <span class="hint" style="font-weight:400">${esc(d.raceKey || '')} · ${esc(d.savedAt || '')}</span></h3>
      ${formHtml}${integHtml}${tlHtml}${anoHtml}${recHtml}${resHtml}
    </div>`;
    const rbtn = $('#koreaHistResultBtn');
    if (rbtn) rbtn.addEventListener('click', async () => {
      const s = prompt('실제 착순을 마번 순서로 입력 (예: 3 1 5)');
      if (!s) return;
      const result = s.trim().split(/[\s,]+/).map((x) => parseInt(x, 10)).filter((n) => !isNaN(n));
      if (!result.length) return;
      await fetch('/api/korea/history/result', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file, result }) });
      openKoreaHistory(file); loadKoreaHistoryList();
    });
  }

  /** 타임라인 스냅샷들의 최저 통합배당 추이를 간단 SVG 라인으로 */
  function koreaHistChartSVG(timeline) {
    const pts = (timeline || []).map((e) => {
      const odds = (e.integrated || []).map((h) => h.odds).filter((o) => typeof o === 'number' && o > 0);
      return odds.length ? Math.min(...odds) : null;
    });
    const valid = pts.filter((p) => p != null);
    if (valid.length < 2) return '';
    const W = 460, H = 90, pad = 22;
    const lo = Math.min(...valid), hi = Math.max(...valid);
    const span = hi - lo || 1;
    const n = pts.length;
    const xf = (i) => pad + (n === 1 ? 0 : i * (W - 2 * pad) / (n - 1));
    const yf = (v) => H - pad - ((v - lo) / span) * (H - 2 * pad);
    let dpath = '', last = null;
    pts.forEach((p, i) => { if (p == null) return; const c = `${xf(i)},${yf(p)}`; dpath += (last == null ? 'M' : 'L') + c + ' '; last = p; });
    const dots = pts.map((p, i) => p == null ? '' : `<circle cx="${xf(i)}" cy="${yf(p)}" r="2.5" fill="${timeline[i].changed ? '#f59e0b' : '#4ea1ff'}"/>`).join('');
    return `<svg width="100%" viewBox="0 0 ${W} ${H}" style="background:rgba(255,255,255,.03);border-radius:6px">
      <text x="4" y="14" fill="#8a94a6" font-size="10">최저 통합배당 ${hi}→${lo}</text>
      <path d="${dpath}" fill="none" stroke="#4ea1ff" stroke-width="1.6"/>${dots}</svg>`;
  }

  /** [2번] 우상단 스택형 토스트 (급락 알림 전용, 비차단) */
  function oddsToast(head, lines) {
    let host = $('#oddsToastHost');
    if (!host) { host = document.createElement('div'); host.id = 'oddsToastHost'; document.body.appendChild(host); }
    const el = document.createElement('div');
    el.className = 'odds-toast';
    el.innerHTML = `<div class="ot-head">${esc(head)}</div>${(lines || []).map((l) => `<div class="ot-line">${l}</div>`).join('')}`;
    host.appendChild(el);
    requestAnimationFrame(() => el.classList.add('show'));
    setTimeout(() => { el.classList.remove('show'); setTimeout(() => el.remove(), 400); }, 7000);
    el.addEventListener('click', () => { el.classList.remove('show'); setTimeout(() => el.remove(), 400); });
  }

  function setKoreaOddsStatus(kind, title, info) {
    const el = $('#koreaOddsStatus'); if (!el) return;
    const { venue, num } = koreaRaceParts(title);
    const rkHint = `${venue || ''} ${num != null ? num + 'R' : ''}`.trim();
    if (kind === 'waiting') {
      el.innerHTML = `<div class="hint" style="padding:8px 10px;background:rgba(245,158,11,.12);border-left:3px solid #f59e0b;border-radius:6px;color:#f59e0b">
        🟡 <b>배당판 미연결 — 전적 기준 초안</b><br>배당판을 수집하면 더 정확한 분석이 됩니다. Chrome 확장(종목=한국경마)에서 <b>${esc(rkHint)}</b> raceKey로 [⚡ 전체 자동 수집]을 실행하세요.</div>`;
    } else if (kind === 'nomatch') {
      const cands = ((info && info.candidates) || []).map((c) => `<span class="chip">${esc(c)}</span>`).join(' ');
      el.innerHTML = `<div class="hint" style="padding:8px 10px;background:rgba(239,68,68,.12);border-left:3px solid #ef4444;border-radius:6px;color:#ff9f9f">
        ⚠️ <b>배당판 경주명을 확인하세요</b> — 배당은 수집됐지만 <b>${esc(rkHint)}</b>와 경주번호가 매칭되지 않습니다.<br>수집된 경주: ${cands || '—'}</div>`;
    } else if (kind === 'linked') {
      el.innerHTML = `<div class="hint" style="padding:8px 10px;background:rgba(56,211,159,.14);border-left:3px solid #38d39f;border-radius:6px;color:#38d39f">
        ✅ <b>배당판 연결 완료 — 통합 분석</b> (전적 40% + 배당 60%) · <b>${esc((info && info.raceKey) || '')}</b></div>`;
    }
  }

  function renderKoreaIntegratedTable(integ) {
    if (!integ || !integ.length) return '';
    const gc = { A: '#38d39f', B: '#4ea1ff', C: '#ffd24f', D: '#8a94a6' };
    const rows = integ.map((h) => `<tr>
      <td><b style="color:${gc[h.grade] || '#fff'}">${h.grade}</b></td>
      <td>${h.no}</td><td>${esc(h.name || '')}</td><td>${esc(h.jockey || '')}</td>
      <td>${h.formScore}</td><td>${h.oddsScore}</td>
      <td>${h.odds != null ? h.odds + '배' : '-'}</td>
      <td><b>${h.integrated}</b></td>
    </tr>`).join('');
    return `<div class="matrix-title" style="font-size:13px">🎯 통합 등급 (전적 40% + 배당 60%)</div>
      <table class="data-table" style="margin-top:4px">
        <thead><tr><th>등급</th><th>마번</th><th>마명</th><th>기수</th><th>전적</th><th>배당점수</th><th>대표배당</th><th>통합</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  // [3번] 이상감지 UI 단순화: 신호를 🔴상/🟡중/🟢하 3단계로 묶어 '종합 한눈에' + 상위 3건만 표시.
  //   (정보 삭제 아님 — 상세 사유는 접기/툴팁으로 보존)
  function _signalTier(level) {
    if (level === '🔴') return { key: '상', icon: '🔴', color: '#ff5c5c', rank: 3 };
    if (level === '🟠' || level === '🔄') return { key: '중', icon: '🟡', color: '#ffd24f', rank: 2 };
    return { key: '하', icon: '🟢', color: '#38d39f', rank: 1 };
  }
  function renderSignalsSimple(signals, title, filterFn) {
    const rel = (signals || []).filter(filterFn);
    if (!rel.length) return '';
    const cnt = { 상: 0, 중: 0, 하: 0 };
    rel.forEach((s) => { cnt[_signalTier(s.level).key]++; });
    const chip = (k, icon, color) => cnt[k] ? `<b style="color:${color}">${icon} ${k} ${cnt[k]}</b>` : '';
    const summary = ['상', '중', '하'].map((k) => {
      const t = _signalTier(k === '상' ? '🔴' : k === '중' ? '🟠' : '🟡');
      return chip(k, t.icon, t.color);
    }).filter(Boolean).join(' · ') || '<span class="hint">없음</span>';
    const sorted = rel.slice().sort((a, b) => _signalTier(b.level).rank - _signalTier(a.level).rank);
    const shown = sorted.slice(0, 3).map((s) => {
      const t = _signalTier(s.level);
      return `<div style="margin:2px 0" title="${esc(s.detail || '')}"><b style="color:${t.color}">${t.icon}</b> ${esc(s.text)}</div>`;
    }).join('');
    const more = sorted.length > 3 ? `<div class="hint">외 ${sorted.length - 3}건 (마우스 올리면 사유 표시)</div>` : '';
    return `<div class="matrix-title" style="font-size:13px;margin-top:8px">${title} · 종합 ${summary}</div>${shown}${more}`;
  }

  function renderKoreaSignals(signals) {
    return renderSignalsSimple(signals, '⏱ 마감 임박 급락', (s) => s.type === '마감급락');
  }

  /** 통합분석 결과(제거분석·유력마·통합등급·베팅·마감급락) 렌더 — 한글 데이터 그대로 */
  function renderKoreaIntegrated(a) {
    const host = $('#koreaIntegrated'); if (!host) return;
    if (!a || a.error || a.waiting) { host.innerHTML = ''; return; }
    state.koreaLastInteg = a;   // [1번] 예산 변경 시 베팅 금액 재계산용
    const keyH = (a.keyHorses || []).map((h) => `<b style="color:#4ea1ff">${h}</b>`).join(' · ');
    // [1번] 제거분석 패널 재사용: id 충돌 방지 위해 패널 id 치환. 아래에서 클릭 토글 핸들러 연결.
    const elimHtml = renderEliminationHTML(a.elimination).replace('id="elimPanel"', 'id="koreaElimPanel"');
    host.innerHTML = `<div class="panel-card">
      <h3>🔗 통합 분석 결과 <span class="hint" style="font-weight:400">${esc(a.raceKey || '')}</span></h3>
      ${renderKoreaIntegratedTable(a.integrated)}
      <div style="margin:8px 0"><span class="hint">⭐ 유력마</span> ${keyH || '—'}${a.anomalyHorse != null ? ` <span class="hint">/ 이상감지말</span> <b style="color:#ff5c5c">${a.anomalyHorse}</b>` : ''}</div>
      ${elimHtml}
      ${renderKoreaSignals(a.signals)}
      ${renderPatternMatch(a.patternMatch)}
      ${renderBetRecommend(a, '#koreaBudget')}
    </div>`;
    _attachElimHandlers('koreaElimPanel', a.elimination);   // [1번] 제거↔후보 클릭 전환 복원
    _bindBudgetInput('#koreaBudget', () => { if (state.koreaLastInteg) renderKoreaIntegrated(state.koreaLastInteg); });
  }

  // ---------- [5·6번] 일본경마 실시간 배당 연동 (복승·쌍승·삼복승 이상감지) ----------
  //  전적표 이미지 분석 후 Chrome 확장(종목=일본)이 같은 raceKey로 복승·쌍승·삼복승을 수집.
  //  최신 수집 raceKey를 30초 간격으로 폴링 → 통합분석·이상감지를 자동 표시. (단승 제거)
  let _jpOddsTimer = null;

  function stopJapanOddsWatch() {
    if (_jpOddsTimer) { clearInterval(_jpOddsTimer); _jpOddsTimer = null; }
  }

  /** 일본 배당 실시간 감시 시작(탭 진입/전적 분석 후). 30초 간격(확장 주기와 정렬). */
  function startJapanOddsWatch() {
    stopJapanOddsWatch();
    state.jpOddsPrev = state.jpOddsPrev || new Set();
    state.jpTimeline = state.jpTimeline || [];
    setJpOddsStatus('waiting');
    const tick = () => pollJapanOdds();
    tick();
    _jpOddsTimer = setInterval(tick, 30000);
  }

  /** 최신 수집 raceKey → 통합분석 → 단승 급락 우선 이상감지 렌더 + 변동 알림/타임라인 */
  async function pollJapanOdds() {
    let latest;
    try { latest = await (await fetch('/api/odds/triple/latest')).json(); }
    catch (_) { return; }
    const rk = latest && latest.raceKey;
    if (!rk) { setJpOddsStatus('waiting'); return; }
    let a;
    try {
      a = await (await fetch('/api/odds/triple/analyze', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ raceKey: rk }),
      })).json();
    } catch (_) { return; }
    if (!a || a.error || a.waiting) { setJpOddsStatus('waiting'); return; }
    setJpOddsStatus('linked', rk);
    renderJapanIntegrated(a);
    onJapanOddsUpdate(rk, a);
  }

  /** [6번] 신규 변동 감지 → 토스트+소리(복승/쌍승 급락·역전) / 타임라인 누적
   *  [실시간 분석 유지 버그수정] ①경주 전환(raceKey 변경) 시에만 경고 상태 초기화.
   *  ②기준값 설정/재설정 상태(변동 계산 안 함)에서는 경고·prev 갱신 생략 → 초반 되돌이·중복 경고 방지. */
  function onJapanOddsUpdate(rk, a) {
    // [3번] 경주 전환 시에만 초기화 — raceKey 가 바뀌면 이전 경주 경고/타임라인 리셋
    if (state.jpCurrentRk && state.jpCurrentRk !== rk) {
      state.jpOddsPrev = new Set();
      state.jpTimeline = [];
    }
    state.jpCurrentRk = rk;
    const hhmmss = new Date().toTimeString().slice(0, 8);
    // [1·2번] 기준값 설정/재설정 = 변동 계산 안 함 → 경고·prev 갱신 생략(신호 유지, 되돌이 방지)
    if (a.baselineSet || a.baselineReset) {
      const tl0 = state.jpTimeline || (state.jpTimeline = []);
      tl0.push({ time: hhmmss, raceKey: rk, changed: false, baseline: true,
        signals: [{ level: '🎯', type: '기준', text: a.baselineReset ? '기준값 재설정' : '기준값 설정', detail: '' }] });
      if (tl0.length > 200) tl0.splice(0, tl0.length - 200);
      renderJapanTimeline();
      return;   // prev(jpOddsPrev) 유지 → 신호 복귀 시 재경고 안 함
    }
    const firstLink = !state.jpTimeline || !state.jpTimeline.length;
    // 일본: 복승/쌍승 급락·역전 이상감지 (단승 제거)
    const signals = (a.signals || []).filter((s) => s.type === '급락' || s.type === '역전' || s.type === '대규모급락');
    const prev = state.jpOddsPrev || new Set();
    const fresh = signals.filter((s) => !prev.has(s.text));

    if (fresh.length && !firstLink) {
      const lines = fresh.map((s) => `${s.level} ${esc(s.text)}`);
      oddsToast(`⏱ 일본 ${esc(rk)} 배당 변동`, lines);
      const worst = fresh.map((s) => s.level).sort((p, q) => sevRank(q) - sevRank(p))[0];
      try { playAlert(worst); } catch (_) {}
    }
    state.jpOddsPrev = new Set(signals.map((s) => s.text));

    const tl = state.jpTimeline || (state.jpTimeline = []);
    tl.push({
      time: hhmmss, raceKey: rk, changed: fresh.length > 0,
      signals: signals.map((s) => ({ level: s.level, type: s.type, text: s.text, detail: s.detail })),
    });
    if (tl.length > 200) tl.splice(0, tl.length - 200);
    renderJapanTimeline();
  }

  function setJpOddsStatus(kind, rk) {
    const el = $('#jpOddsStatus'); if (!el) return;
    if (kind === 'linked') {
      el.innerHTML = `<div class="hint" style="padding:8px 10px;background:rgba(56,211,159,.14);border-left:3px solid #38d39f;border-radius:6px;color:#38d39f">
        ✅ <b>실시간 배당 연결 — 복승·쌍승·삼복승 이상감지</b> · <b>${esc(rk || '')}</b></div>`;
    } else {
      el.innerHTML = `<div class="hint" style="padding:8px 10px;background:rgba(245,158,11,.12);border-left:3px solid #f59e0b;border-radius:6px;color:#f59e0b">
        🟡 <b>배당 수집 대기중</b> — Chrome 확장(종목=일본경마)에서 raceKey를 설정하고 <b>[⚡ 전체 자동 수집]</b>을 실행하세요. 복승·쌍승·삼복승이 30초 간격으로 연동됩니다.</div>`;
    }
  }

  /** 복승/쌍승 급락·역전 이상감지 신호 렌더 (단승 제거) — [3번] 🔴상/🟡중/🟢하 단순화 */
  function renderJapanSignals(signals) {
    return renderSignalsSimple(signals, '⚠️ 이상감지 (복승·쌍승·삼복승)', (s) => s.type === '급락' || s.type === '역전');
  }

  /** 실시간 배당 통합분석 결과 렌더(유력마·이상감지·베팅) — 단승 제거 */
  function renderJapanIntegrated(a) {
    const host = $('#jpIntegrated'); if (!host) return;
    if (!a || a.error || a.waiting) { host.innerHTML = ''; return; }
    state.jpLastInteg = a;   // [1번] 예산 변경 시 베팅 금액 재계산용
    const keyH = (a.keyHorses || []).map((h) => `<b style="color:#4ea1ff">${h}</b>`).join(' · ');
    // [1번] 전적 점수별 말 목록(출마표2 등급표) + 제거 분석(읽기전용) 복원
    const formHtml = renderFormGrades(a.form);
    const elimHtml = renderEliminationHTML(a.elimination, new Set()).replace('id="elimPanel"', 'id="jpElimPanel"');
    host.innerHTML = `<div class="panel-card">
      <h3>🔗 실시간 배당 이상감지 <span class="hint" style="font-weight:400">${esc(a.raceKey || '')}</span></h3>
      <div style="margin:8px 0"><span class="hint">⭐ 유력마</span> ${keyH || '—'}${a.anomalyHorse != null ? ` <span class="hint">/ 이상감지말</span> <b style="color:#ff5c5c">${a.anomalyHorse}</b>` : ''}</div>
      ${formHtml}
      ${elimHtml}
      ${renderJapanSignals(a.signals)}
      ${renderPatternMatch(a.patternMatch)}
      ${renderBetRecommend(a, '#jpBudget')}
    </div>`;
    _bindBudgetInput('#jpBudget', () => { if (state.jpLastInteg) renderJapanIntegrated(state.jpLastInteg); });
  }

  /** 일본 배당 변동 타임라인 렌더(수집 1건 = 항목 1개) */
  function renderJapanTimeline() {
    let host = $('#jpTimeline');
    if (!host) {
      const anchor = $('#jpIntegrated'); if (!anchor) return;
      host = document.createElement('div'); host.id = 'jpTimeline'; host.className = 'panel-card';
      anchor.insertAdjacentElement('afterend', host);
    }
    const tl = state.jpTimeline || [];
    if (!tl.length) {
      host.innerHTML = `<h3>⏱ 배당 변동 타임라인</h3><p class="hint">배당이 수집되면 30초 간격으로 변동 내역이 누적됩니다.</p>`;
      return;
    }
    const rows = tl.map((e) => {
      const badge = e.changed ? e.signals.map((s) => s.level).join('') : '<span class="hint">변동 없음</span>';
      const summary = e.changed ? e.signals.map((s) => esc(s.text)).join(' · ') : '';
      return `<div style="padding:4px 6px;border-left:3px solid ${e.changed ? '#f59e0b' : 'transparent'};border-radius:4px">
        <b>${e.time}</b> ${badge} <span class="hint">${summary}</span></div>`;
    }).reverse().join('');
    host.innerHTML = `<h3>⏱ 배당 변동 타임라인 <span class="hint" style="font-weight:400">(${tl.length}회 수집)</span></h3>
      <div style="max-height:220px;overflow:auto">${rows}</div>`;
  }

  // ---------- 통합 분석 (Phase 4) ----------
  function refreshCombinedRaceSelect() {
    const sel = $('#combinedRaceSelect');
    if (!sel) return;
    const keys = Object.keys(state.lastSheets);
    sel.innerHTML = keys.length
      ? keys.map((k) => `<option value="${esc(k)}">${esc(k)}</option>`).join('')
      : '<option value="">분석된 경주 없음</option>';
    $('#combinedHint').textContent = keys.length
      ? '예산 입력 후 [통합 분석]을 누르세요.'
      : '먼저 한국경마 탭에서 경주를 분석하세요.';
  }

  function initCombined() {
    $('#combinedRunBtn').addEventListener('click', runCombined);
  }

  async function runCombined() {
    const title = $('#combinedRaceSelect').value;
    const meta = state.lastSheets[title];
    if (!meta) { toast('먼저 한국경마 탭에서 경주를 분석하세요.'); return; }
    const horses = (meta.horses || []).map((h) => ({
      no: h.horseNum, name: h.horseName, jockey: h.jockey,
      recentPlacings: h.recentPlacings || [],
      jockey3mPlaceRate: (state.jockeyStats[h.jockey] || {}).placeRate,
    }));
    const budget = parseInt($('#combinedBudget').value, 10) || 0;
    try {
      showLoading('통합 분석 중...');
      const res = await Analysis.analyzeCombined({
        raceKey: title,
        race: { distance: meta.distance || '', course: '', grade: '' },
        horses, budget,
      });
      hideLoading();
      renderCombined(res);
      stashCombined(title, res, budget);
    } catch (e) { hideLoading(); toast('통합 분석 실패: ' + e.message); }
  }

  /** 통합 결과를 결과기록/통계용으로 저장 (적중 판정·이상감지 효과 검증에 사용) */
  function stashCombined(title, res, budget) {
    const horses = res.horses || [];
    // [5번] 패턴 신호: 급락50%+ / 배당압축
    const drop50 = horses.some((h) => ((h.anomaly || {}).drop || 0) >= 0.50);
    const oh = (res.odds && res.odds.horses) || [];
    const od = oh.filter((h) => h.lastOdds > 0).sort((a, b) => a.lastOdds - b.lastOdds);
    const squeeze = od.length >= 2 && od[1].lastOdds <= od[0].lastOdds * 1.2;
    const had = drop50 || horses.some((h) => ((h.anomaly || {}).signalScore || 0) >= 75)
      || (res.alerts || []).some((a) => a.type === '쌍승역전');
    const qab = (res.bets || []).find((b) => b.key === 'q_ab');
    state.lastCombined[title] = {
      bets: (res.bets || []).filter((b) => b.available).map((b) => ({ type: b.type, combo: b.combo })),
      recOdds: qab ? qab.fairOdds : null,
      hadAnomaly: had,
      signals: { drop50, squeeze },   // mismatch(복승 불일치)는 3종 분석에서 별도
      budget: budget || 0,
    };
  }

  function renderCombined(res) {
    const el = $('#combinedReport'); el.innerHTML = '';
    const horses = res.horses || [];
    const byNo = (no) => horses.find((h) => h.no === no);
    const picks = res.picks || {};

    // [등급 카드]
    const gradeCard = (g) => {
      const h = picks[g] ? byNo(picks[g]) : null;
      if (!h) return `<div class="stat-card" style="text-align:left"><span class="grade-badge grade-${g}">${g}</span> <span class="hint">해당 없음</span></div>`;
      const an = h.anomaly || {};
      const sig = (typeof an.signalScore === 'number') ? ` + 배당신호 ${sigColor(an.signalScore)}` : '';
      const ins = (h.flags || []).some((f) => f.type.indexOf('삼복승보험') === 0) ? ' <span class="flag flag-caution">보험</span>' : '';
      const up = h.grade !== h.gradeBase ? ` <span class="hint">(${h.gradeBase}→${h.grade})</span>` : '';
      return `<div class="stat-card" style="text-align:left">
        <div><span class="grade-badge grade-${g}">${g}</span> <strong>${h.no}번 ${esc(h.name)}</strong>${ins}</div>
        <div class="label" style="margin-top:6px">전적 ${h.totalScore}점${sig}${up}</div></div>`;
    };
    add(el, 'panel-card', `<h3>🏅 등급 카드</h3><div class="stat-grid">${['A', 'B', 'C', 'D'].map(gradeCard).join('')}</div>`);

    // [베팅 추천]
    const slotLabel = (b) => `${b.type} ${b.slots.join('+')}`;
    const won = (n) => (n || 0).toLocaleString() + '원';
    const betRows = (res.bets || []).map((b) => {
      if (!b.available) return `<div class="bet-line"><span><span class="bet-type">${slotLabel(b)}</span></span><span class="hint">${esc(b.note)}</span></div>`;
      const be = b.breakevenOdds ? ` · 손익분기 ${b.breakevenOdds}배` : '';
      const ev = (b.evPct != null) ? ` · EV ${b.evPct >= 0 ? '+' : ''}${b.evPct}%` : '';
      return `<div class="bet-line">
        <span><span class="bet-type">${slotLabel(b)}</span> ${b.combo.join('-')} <span class="hint">(${esc((b.labels || []).join('·'))})</span></span>
        <span>${won(b.amount)} (${b.weightPct}%)${be}${ev}</span></div>`;
    }).join('');
    const bs = res.betSummary || {};
    add(el, 'bet-box', `<h3>💰 베팅 추천 ${bs.budget ? '· 예산 ' + won(bs.budget) : '(예산 입력 시 금액 계산)'}</h3>${betRows}
      ${bs.unallocated ? `<p class="hint">미배분 ${won(bs.unallocated)} (성립 불가 베팅분)</p>` : ''}`);

    // [이상감지 요약]
    const oh = (res.odds && res.odds.horses) || [];
    const drops = oh.filter((h) => (h.drop || 0) >= 0.30).sort((a, b) => b.drop - a.drop)
      .map((h) => `<div class="bet-line"><span>${h.drop >= 0.50 ? '🔴' : '🟡'} 급락 ${h.no}번</span><span>${(h.drop * 100).toFixed(0)}% ↓ (배당 ${h.lastOdds == null ? '-' : h.lastOdds})</span></div>`).join('');
    const rev = (res.alerts || []).filter((a) => a.type === '쌍승역전')
      .map((a) => `<div class="bet-line"><span>🔴 역전</span><span>${esc(a.msg.replace('🔴 쌍승 역전 감지: ', ''))}</span></div>`).join('');
    let squeeze = '';
    const od = oh.filter((h) => h.lastOdds > 0).sort((a, b) => a.lastOdds - b.lastOdds);
    if (od.length >= 2 && od[1].lastOdds <= od[0].lastOdds * 1.2) {
      squeeze = `<div class="bet-line"><span>🟡 압축</span><span>${od[0].no}·${od[1].no}번 상위 배당 근접 (${od[0].lastOdds}·${od[1].lastOdds})</span></div>`;
    }
    add(el, 'bet-box', `<h3>🚨 이상감지 요약</h3>${(drops + rev + squeeze) || '<p class="hint">감지된 이상 신호 없음. (배당 미연동이면 배당판 캡처 탭에서 같은 경주명으로 1·2차 저장 후 다시 분석하세요)</p>'}`);
  }

  // ---------- 결과 입력 (Phase 5-1 / 5-3) ----------
  function todayStr() { return new Date().toISOString().slice(0, 10); }

  /** [5번] 결과 레코드에 저장할 이상감지 패턴 신호 */
  function signalsFor(c) {
    const s = (c && c.signals) || {};
    return { drop50: !!s.drop50, squeeze: !!s.squeeze, mismatch: !!(state.lastTriple && state.lastTriple.mismatch) };
  }

  /** [6번] 착순 결과로 기수-마필/거리/주로 성적 자동 갱신 */
  function updateJockeysFromResult(title, result) {
    const sheet = state.lastSheets[title];
    if (!sheet || !sheet.horses) return;
    const byNo = {}; sheet.horses.forEach((h) => { byNo[h.horseNum] = h; });
    (result || []).forEach((no, i) => {
      const h = byNo[no];
      if (h && h.jockey) {
        JockeyDB.recordRace(h.jockey, {
          horse: h.horseName, distance: sheet.distance, track: state.raceCondition.track, placing: i + 1,
        });
      }
    });
  }

  /** 적중 판정용 베팅: 통합분석(Phase4) 결과 우선, 없으면 BMED 추천 */
  function betsForRace(title) {
    const c = state.lastCombined[title];
    if (c && c.bets && c.bets.length) return c.bets;
    const rep = state.lastReports[title];
    return rep ? flattenBets(rep) : [];
  }

  function renderResultForm() {
    const wrap = $('#resultForm');
    const titles = Object.keys(state.lastReports);
    const perRace = titles.length
      ? titles.map((t) => {
        const c = state.lastCombined[t] || {};
        const betTxt = betsForRace(t).map((b) => `${b.type} ${b.combo.join('-')}`).join(' / ') || '추천 없음';
        return `
        <div class="bet-box res-block" data-title="${esc(t)}" style="margin-bottom:12px">
          <h3>${esc(t)} ${c.hadAnomaly ? '<span class="flag flag-must">🔴 이상감지</span>' : ''}</h3>
          <div class="hint">추천: ${esc(betTxt)}</div>
          <div class="cfg-row" style="margin-top:8px">
            <label class="hint">날짜<br><input class="cfg-input res-date" type="date" value="${todayStr()}" /></label>
            <label class="hint">투자금액(원)<br><input class="cfg-input res-stake" type="number" min="0" step="100" value="${c.budget || 0}" style="width:120px" /></label>
            <label class="hint">1·2·3·4착(콤마)<br><input class="cfg-input res-place" placeholder="3,7,1,5" style="width:130px" /></label>
            <label class="hint">수익금액(원)<br><input class="cfg-input res-payout" type="number" min="0" step="100" value="0" style="width:120px" /></label>
            <button class="btn btn-primary save-result-btn">결과 저장</button>
          </div>
        </div>`;
      }).join('')
      : '<p class="hint">분석한 경주가 여기 표시됩니다.</p>';
    wrap.innerHTML = `
      <div class="panel-card">
        <h3>📷 당일 전체 결과 일괄 입력 (Phase 5-3)</h3>
        <p class="hint">당일 결과(착순) 이미지를 올리면 경주별로 추출해 분석 경주와 매칭합니다. 투자/수익을 입력하면 당일 전체 손익을 자동 계산해 한 번에 저장합니다.</p>
        <input type="file" id="resultImgInput" accept="image/*" hidden/>
        <button class="btn btn-primary" id="resultImgBtn">결과 이미지 업로드</button>
        <div id="batchResult" style="margin-top:10px"></div>
      </div>
      <div class="panel-card"><h3>경주별 결과 입력 (Phase 5-1)</h3>${perRace}</div>`;
    $('#resultImgBtn').addEventListener('click', () => $('#resultImgInput').click());
    $('#resultImgInput').addEventListener('change', () => { if ($('#resultImgInput').files[0]) handleResultImage($('#resultImgInput').files[0]); });
    $$('.save-result-btn').forEach((btn) => btn.addEventListener('click', () => saveResult(btn)));

    // [기능2] 분석 리포트의 [결과 입력]으로 넘어온 경우 해당 경주 블록에 포커스
    if (state.pendingResultTitle) {
      const focus = state.pendingResultTitle;
      state.pendingResultTitle = null;
      const block = $$('.res-block').find((b) => b.dataset.title === focus);
      if (block) {
        block.classList.add('res-focus');
        block.scrollIntoView({ behavior: 'smooth', block: 'center' });
        const inp = block.querySelector('.res-place');
        if (inp) inp.focus();
      }
    }
  }

  async function handleResultImage(file) {
    try {
      showLoading('결과 추출 중...');
      const block = await Analysis.fileToImageBlock(file);
      const out = await Analysis.extractResults(block);
      hideLoading();
      renderBatch(out.results || []);
    } catch (e) { hideLoading(); toast('결과 추출 실패: ' + e.message); }
  }

  /** 추출된 결과를 분석한 경주(lastReports)와 매칭 */
  function findReportFor(venue, raceNo) {
    const keys = Object.keys(state.lastReports);
    if (venue === '일본') return keys.find((k) => k.includes('일본')) || null;
    const pref = `${venue} ${raceNo}경주`;
    return keys.find((k) => k.startsWith(pref)) || keys.find((k) => k.includes(`${raceNo}경주`)) || null;
  }

  function renderBatch(results) {
    const host = $('#batchResult');
    if (!results.length) { host.innerHTML = '<p class="hint">결과를 추출하지 못했습니다.</p>'; return; }
    const rows = results.map((r, i) => {
      const key = findReportFor(r.venue, r.raceNo);
      const c = key ? (state.lastCombined[key] || {}) : {};
      return `<tr data-i="${i}">
        <td>${esc(r.venue || '')} ${r.raceNo}R</td>
        <td>${(r.placing || []).join('-')}</td>
        <td>${key ? '✅ ' + esc(key) : '⚠️ 없음'}</td>
        <td><input class="cfg-input batch-stake" type="number" min="0" step="100" value="${c.budget || 0}" style="width:100px" /></td>
        <td><input class="cfg-input batch-payout" type="number" min="0" step="100" value="0" style="width:100px" /></td>
      </tr>`;
    }).join('');
    host.innerHTML = `
      <table class="data-table"><thead><tr><th>경주</th><th>착순</th><th>매칭</th><th>투자(원)</th><th>수익(원)</th></tr></thead><tbody>${rows}</tbody></table>
      <div class="bet-line" style="margin-top:8px"><span class="bet-type">당일 합계</span><span id="batchSum">-</span></div>
      <button class="btn btn-primary" id="batchSaveBtn" style="margin-top:8px">전체 저장 → 학습 DB</button>`;
    const recalc = () => {
      let st = 0, po = 0;
      host.querySelectorAll('tr[data-i]').forEach((tr) => {
        st += parseInt(tr.querySelector('.batch-stake').value, 10) || 0;
        po += parseInt(tr.querySelector('.batch-payout').value, 10) || 0;
      });
      $('#batchSum').textContent = `투자 ${st.toLocaleString()} · 수익 ${po.toLocaleString()} · 손익 ${(po - st).toLocaleString()}원`;
    };
    host.querySelectorAll('.batch-stake,.batch-payout').forEach((inp) => inp.addEventListener('input', recalc));
    recalc();
    $('#batchSaveBtn').addEventListener('click', () => saveBatch(results, host));
  }

  function saveBatch(results, host) {
    let saved = 0, hits = 0, matched = 0, st = 0, po = 0;
    results.forEach((r, i) => {
      const placing = r.placing || [];
      if (!placing.length) return;
      const key = findReportFor(r.venue, r.raceNo);
      const c = key ? (state.lastCombined[key] || {}) : {};
      const bets = key ? betsForRace(key) : [];
      if (key) matched++;
      const tr = host.querySelector(`tr[data-i="${i}"]`);
      const stake = tr ? (parseInt(tr.querySelector('.batch-stake').value, 10) || 0) : 0;
      const payout = tr ? (parseInt(tr.querySelector('.batch-payout').value, 10) || 0) : 0;
      st += stake; po += payout;
      const hit = bets.some((b) => History.judgeHit(b, placing));
      if (hit) hits++;
      History.addResult({
        date: todayStr(), region: r.venue === '일본' ? '일본' : '한국',
        raceTitle: key || `${r.venue || ''} ${r.raceNo}경주`,
        bets, result: placing, hit, stake, payout,
        hadAnomaly: !!c.hadAnomaly, recOdds: c.recOdds == null ? null : c.recOdds,
        signals: signalsFor(c),
      });
      saved++;
    });
    toast(`저장 ${saved}건 · 분석매칭 ${matched} · 적중 ${hits} · 당일손익 ${(po - st).toLocaleString()}원`);
    renderStats();
  }

  function saveResult(btn) {
    const block = btn.closest('.res-block');
    const title = block.dataset.title;
    const result = (block.querySelector('.res-place').value || '').split(',').map((s) => parseInt(s.trim(), 10)).filter((n) => !isNaN(n));
    if (!result.length) { toast('착순(1·2·3착)을 입력하세요.'); return; }
    const stake = parseInt(block.querySelector('.res-stake').value, 10) || 0;
    const payout = parseInt(block.querySelector('.res-payout').value, 10) || 0;
    const date = block.querySelector('.res-date').value || todayStr();
    const c = state.lastCombined[title] || {};
    const region = title === '일본경마' || title.indexOf('일본') === 0 ? '일본' : '한국';
    const bets = betsForRace(title);
    const hit = bets.some((b) => History.judgeHit(b, result));
    History.addResult({
      date, region, raceTitle: title, bets, result, hit, stake, payout,
      hadAnomaly: !!c.hadAnomaly, recOdds: c.recOdds == null ? null : c.recOdds,
      signals: signalsFor(c),
    });
    updateJockeysFromResult(title, result);   // [6번] 기수 성적 자동 갱신
    refreshRaceChipStatus();                   // [기능3] 진행상황 칩 갱신 (이 경주 → 🏁)
    toast(`저장됨 — ${hit ? '✅ 적중!' : '❌ 미적중'} (투자 ${stake.toLocaleString()} / 수익 ${payout.toLocaleString()})`);
    renderStats();
  }

  // ---------- 통계 대시보드 (Phase 5-2) ----------
  function renderStats() {
    const s = History.stats();
    const won = (n) => (n || 0).toLocaleString() + '원';
    const col = (v) => (v >= 0 ? 'var(--green)' : 'var(--red)');

    const basic = `
      <h3>기본 통계</h3>
      <div class="stat-grid">
        <div class="stat-card"><div class="num">${s.total}</div><div class="label">총 경주</div></div>
        <div class="stat-card"><div class="num">${s.hits}</div><div class="label">적중</div></div>
        <div class="stat-card"><div class="num">${s.hitRate}%</div><div class="label">적중률</div></div>
        <div class="stat-card"><div class="num" style="font-size:20px">${won(s.stakeSum)}</div><div class="label">총 투자금</div></div>
        <div class="stat-card"><div class="num" style="font-size:20px">${won(s.payoutSum)}</div><div class="label">총 수익</div></div>
        <div class="stat-card"><div class="num" style="font-size:20px;color:${col(s.net)}">${won(s.net)}</div><div class="label">순손익</div></div>
        <div class="stat-card"><div class="num" style="color:${col(s.roi)}">${s.roi}%</div><div class="label">ROI</div></div>
      </div>`;

    const a = s.byAnomaly;
    const diff = (a.with.rate - a.without.rate).toFixed(1);
    const anomaly = `
      <h3 style="margin-top:18px">이상감지 효과 검증</h3>
      <div class="stat-grid">
        <div class="stat-card"><div class="num">${a.with.rate}%</div><div class="label">🔴 이상감지 (${a.with.hit}/${a.with.n})</div></div>
        <div class="stat-card"><div class="num">${a.without.rate}%</div><div class="label">🟢 미감지 (${a.without.hit}/${a.without.n})</div></div>
        <div class="stat-card"><div class="num" style="color:${col(diff)}">${diff >= 0 ? '+' : ''}${diff}%p</div><div class="label">차이 (이상감지 효과)</div></div>
      </div>`;

    const bandRows = Object.entries(s.byOddsBand).filter(([, v]) => v.n > 0)
      .map(([k, v]) => `<tr><td>${k}</td><td>${v.n}</td><td>${v.hit}</td><td>${v.rate}%</td></tr>`).join('')
      || '<tr><td colspan="4" class="hint">데이터 없음 (통합분석 후 결과를 입력하면 배당대가 기록됩니다)</td></tr>';
    const bands = `
      <h3 style="margin-top:18px">배당대별 적중률 <span class="hint" style="font-weight:400">(추천 복승 공정배당 기준)</span></h3>
      <table class="data-table"><thead><tr><th>배당대</th><th>경주</th><th>적중</th><th>적중률</th></tr></thead><tbody>${bandRows}</tbody></table>`;

    const months = Object.keys(s.byMonth).sort();
    const maxV = Math.max(1, ...months.map((m) => Math.max(s.byMonth[m].stake, s.byMonth[m].payout)));
    const bars = months.map((m) => {
      const v = s.byMonth[m];
      const pnl = v.payout - v.stake;
      return `<div class="mbar">
        <div class="mbar-cols">
          <div class="mbar-col stake" style="height:${Math.round(v.stake / maxV * 100)}%" title="투자 ${won(v.stake)}"></div>
          <div class="mbar-col payout" style="height:${Math.round(v.payout / maxV * 100)}%" title="수익 ${won(v.payout)}"></div>
        </div>
        <div class="mbar-label">${m}<br><span style="color:${col(pnl)}">${pnl >= 0 ? '+' : ''}${(pnl / 10000).toFixed(1)}만</span></div>
      </div>`;
    }).join('');
    const monthly = months.length ? `
      <h3 style="margin-top:18px">월별 손익 <span class="hint" style="font-weight:400">(파랑=투자 / 초록=수익)</span></h3>
      <div class="mbars">${bars}</div>` : '';

    // [5번] 이상감지 패턴별 효과
    const sg = s.bySignal || {};
    const sigRows = [
      ['🔴 급락 50%+', sg.drop50],
      ['🟡 복승 불일치', sg.mismatch],
      ['🟡 배당 압축', sg.squeeze],
      ['⚪ 감지 없음', sg.none],
    ];
    const anySig = sigRows.some(([, v]) => v && v.n > 0);
    const patternHtml = anySig
      ? `<table class="data-table">
           <thead><tr><th>감지 패턴</th><th>경주수</th><th>적중</th><th>적중률</th><th>ROI</th></tr></thead>
           <tbody>${sigRows.map(([label, v]) => {
             v = v || { n: 0, hit: 0, rate: 0, roi: 0 };
             const roiColor = v.roi >= 0 ? 'var(--green)' : 'var(--red)';
             return `<tr><td>${label}</td><td>${v.n}</td><td>${v.hit}</td><td>${v.rate}%</td>
               <td style="color:${v.n ? roiColor : 'var(--text-dim)'}">${v.n ? (v.roi >= 0 ? '+' : '') + v.roi + '%' : '-'}</td></tr>`;
           }).join('')}</tbody></table>
         <p class="hint">감지 패턴별 적중률·수익률 비교 — 어떤 신호가 실제로 적중에 도움이 되는지 누적 검증.</p>`
      : `<p class="hint">📊 아직 데이터가 없습니다. 통합분석/3종분석 후 결과를 입력하면 <b>기록이 쌓이는 대로 자동으로 채워집니다.</b></p>`;
    const patterns = `<h3 style="margin-top:18px">🔍 이상감지 패턴별 효과</h3>${patternHtml}`;

    const types = `
      <h3 style="margin-top:18px">베팅 종류별</h3>
      <table class="data-table"><thead><tr><th>종류</th><th>건수</th><th>적중</th><th>적중률</th></tr></thead>
        <tbody>${Object.entries(s.byType).map(([t, v]) =>
          `<tr><td>${t}</td><td>${v.n}</td><td>${v.hit}</td><td>${v.n ? Math.round(v.hit / v.n * 100) : 0}%</td></tr>`).join('')}</tbody></table>`;

    $('#statsDashboard').innerHTML = basic + anomaly + bands + patterns + monthly + types +
      (s.total ? '' : '<p class="hint" style="margin-top:12px">아직 기록이 없습니다. 결과기록 탭에서 입력하세요.</p>');
  }

  // ---------- [6번] 기수 DB 조회 ----------
  function renderJockeyDb() {
    const leaders = JockeyDB.leaders();
    // 리딩 순위
    const rankRows = leaders.map((j) =>
      `<tr><td>${j.rank}</td><td><b>${esc(j.name)}</b></td><td>${esc(j.track || '')}</td>
        <td>${j.winRate != null ? j.winRate + '%' : '-'}</td><td>${j.placeRate != null ? j.placeRate + '%' : '-'}</td>
        <td>${j.recent30 ? JockeyDB.rate(j.recent30) + '%' : '-'}</td><td>${j.rides || 0}</td></tr>`).join('');
    $('#jockeyDbView').innerHTML = `
      <h3>리딩 기수 순위 (복승권율)</h3>
      <table class="data-table"><thead><tr><th>#</th><th>기수</th><th>소속</th><th>승률</th><th>복승권율</th><th>최근30</th><th>기승</th></tr></thead>
        <tbody>${rankRows}</tbody></table>
      <div id="jockeyDetail" style="margin-top:16px"></div>`;
    // 셀렉트 채우기
    const sel = $('#jockeySelect');
    sel.innerHTML = leaders.map((j) => `<option value="${esc(j.name)}">${esc(j.name)}</option>`).join('');
    sel.onchange = () => renderJockeyDetail(sel.value);
    if (leaders.length) renderJockeyDetail(sel.value || leaders[0].name);
  }

  function renderJockeyDetail(name) {
    const j = JockeyDB.get(name); if (!j) return;
    const statRow = (label, o) => `<tr><td>${esc(label)}</td><td>${o ? o.places + '/' + o.rides : '-'}</td><td>${o ? JockeyDB.rate(o) + '%' : '-'}</td></tr>`;
    // [거리 동적] 기록 있는 거리만(rides>0) 거리 오름차순 표시, 없는 거리는 숨김
    const distEntries = Object.entries(j.byDistance || {}).filter(([, o]) => o && o.rides > 0)
      .sort((a, b) => parseInt(a[0], 10) - parseInt(b[0], 10));
    const dist = distEntries.length
      ? distEntries.map(([d, o]) => statRow(d + 'm', o)).join('')
      : '<tr><td colspan="3" class="hint">기록 있는 거리 없음 — 결과 입력 시 자동 추가</td></tr>';
    const trkEntries = Object.entries(j.byTrack || {}).filter(([, o]) => o && o.rides > 0);
    const trk = trkEntries.length
      ? trkEntries.map(([t, o]) => statRow(t, o)).join('')
      : '<tr><td colspan="3" class="hint">기록 있는 주로 없음</td></tr>';
    const horse = Object.entries(j.byHorse || {}).map(([h, o]) => statRow(h, o)).join('') || '<tr><td colspan="3" class="hint">조합 기록 없음 — 결과 입력 시 쌓입니다</td></tr>';
    $('#jockeyDetail').innerHTML = `
      <h3>${esc(name)} 상세</h3>
      <div class="stat-grid">
        <div class="stat-card"><div class="num">${j.placeRate || 0}%</div><div class="label">통산 복승권율</div></div>
        <div class="stat-card"><div class="num">${j.recent30 ? JockeyDB.rate(j.recent30) : 0}%</div><div class="label">최근 30경주</div></div>
        <div class="stat-card"><div class="num">${j.winRate || 0}%</div><div class="label">통산 승률</div></div>
      </div>
      <div class="upload-grid" style="margin-top:12px">
        <div><h3 style="font-size:14px">거리별 적성</h3><table class="data-table"><thead><tr><th>거리</th><th>복/기</th><th>율</th></tr></thead><tbody>${dist}</tbody></table></div>
        <div><h3 style="font-size:14px">주로상태별</h3><table class="data-table"><thead><tr><th>주로</th><th>복/기</th><th>율</th></tr></thead><tbody>${trk}</tbody></table></div>
      </div>
      <h3 style="font-size:14px;margin-top:12px">기수-마필 조합 성적</h3>
      <table class="data-table"><thead><tr><th>마명</th><th>복/기</th><th>율</th></tr></thead><tbody>${horse}</tbody></table>`;
  }

  // ---------- escape ----------
  function esc(str) { return String(str == null ? '' : str).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  // ══════════ [신규] 고배당 적중 상세 분석 리포트 시스템 (프론트) ══════════
  let _reportWired = false;
  function wireReportButtons() {
    if (_reportWired) return;
    _reportWired = true;
    const bL = $('#reportLoad'); if (bL) bL.addEventListener('click', () => {
      const sel = $('#reportSelect'); if (sel && sel.value) openRaceReport(sel.value);
    });
    const bR = $('#reportRefresh'); if (bR) bR.addEventListener('click', loadReportList);
  }

  // [2번] 고배당 명예의 전당 — /api/highlights 카드형 표시
  async function loadHighlights() {
    const box = $('#highlightWins'); if (!box) return;
    let d = null;
    try { d = await (await fetch('/api/highlights')).json(); } catch (_) { /* */ }
    const arr = (d && d.highlights) || [];
    if (!arr.length) { box.innerHTML = '<div class="hl-empty">아직 고배당 적중 기록이 없습니다. 결과를 입력하면 복승 30배+ / 삼복승 100배+ 적중이 자동 등록됩니다.</div>'; return; }
    box.innerHTML = arr.map((h) => {
      const odds = h.trifecta_hit ? h.trifecta_odds : h.quinella_odds;
      const kind = h.trifecta_hit ? '삼복승' : '복승';
      const combo = (h.top3 || []).join('+');
      const tags = (h.win_tags || []).map((t) => `<span class="hl-tag">${esc(t.replace('_적중', ''))}</span>`).join('');
      const slug = h.report_slug || '';
      return `<div class="hl-card" data-slug="${esc(slug)}">
        <div class="hl-odds">${odds ? odds + '배' : '고배당'}</div>
        <div class="hl-race">🏆 ${esc(h.race || h.raceKey || '')}</div>
        <div class="hint">${kind} ${esc(combo)}${h.date ? ' · ' + esc(h.date) : ''}</div>
        <div class="hl-tags">${tags}</div>
      </div>`;
    }).join('');
    box.querySelectorAll('.hl-card').forEach((c) => c.addEventListener('click', () => {
      const s = c.dataset.slug; if (s) openRaceReport(s);
    }));
  }

  // [1번] 리포트 목록 → 셀렉트 채우기
  async function loadReportList() {
    wireReportButtons();
    const sel = $('#reportSelect'); if (!sel) return;
    let d = null;
    try { d = await (await fetch('/api/race-report/list')).json(); } catch (_) { /* */ }
    const arr = (d && d.reports) || [];
    if (!arr.length) { sel.innerHTML = '<option value="">(리포트 없음 — 결과를 입력하면 생성됩니다)</option>'; return; }
    sel.innerHTML = arr.map((r) => {
      const mark = r.hit ? '✅' : '·';
      const od = r.win_odds ? ` ${r.win_odds}배` : '';
      return `<option value="${esc(r.slug)}">${mark} ${esc(r.race || r.slug)}${od} (${esc(r.date || '')})</option>`;
    }).join('');
  }

  // [3번] 단일 리포트 열기 → 4탭 재현 화면
  async function openRaceReport(slug) {
    const view = $('#raceReportView'); if (!view) return;
    view.innerHTML = '<p class="hint">리포트 불러오는 중…</p>';
    let rep = null;
    try { rep = await (await fetch('/api/race-report/get?slug=' + encodeURIComponent(slug))).json(); } catch (_) { /* */ }
    if (!rep || rep.error) { view.innerHTML = `<p class="hint">리포트를 불러오지 못했습니다${rep && rep.error ? ' (' + esc(rep.error) + ')' : ''}.</p>`; return; }
    renderRaceReport(rep, view);
    const sel = $('#reportSelect'); if (sel && rep._slug) sel.value = rep._slug;
  }

  function renderRaceReport(rep, view) {
    const res = rep.result || {};
    const cb = rep.confidence_breakdown || {};
    const resultStr = [res['1st'], res['2nd'], res['3rd']].filter((x) => x != null).join('-') + (res['4th'] != null ? ' (4착 ' + res['4th'] + ')' : '');
    const hitBadge = rep.hit ? `<span style="color:#3fae5a">✅ 적중 · ${esc(rep.hit_type || '')}</span>` : '<span class="hint">미적중</span>';
    const oddsBadge = rep.win_odds ? ` · <b style="color:#ffd24f">${rep.win_odds}배 (${esc(rep.odds || '')})</b>` : '';
    const tagsHtml = (rep.win_tags || []).map((t) => `<span class="hl-tag">${esc(t.replace('_적중', ''))} 적중</span>`).join(' ');

    // 탭1: 추천 근거(스토리)
    const story = (rep.recommendation_process || []).map((s) => `<li>${esc(s)}</li>`).join('') || '<li class="hint">스토리 정보 없음</li>';
    const pane1 = `<div class="rpt-pane active" data-pane="story">
      <h3>📖 추천 스토리</h3>
      <ul class="rpt-story">${story}</ul>
      <div class="rpt-conf">
        <div class="cf">초과급락<b>${cb.excess_drop_score != null ? cb.excess_drop_score : '-'}</b></div>
        <div class="cf">쌍승역전<b>${cb.exacta_reversal_score != null ? cb.exacta_reversal_score : '-'}</b></div>
        <div class="cf">복승불일치<b>${cb.quinella_mismatch_score != null ? cb.quinella_mismatch_score : '-'}</b></div>
        <div class="cf">종합신뢰도<b>${cb.total != null ? cb.total : '-'}</b><span class="hint">${esc(cb.grade || '')}</span></div>
        <div class="cf">전적점수<b>${cb.record_score != null ? cb.record_score : '-'}</b></div>
      </div>
      <div class="rpt-bar"><span style="width:${Math.max(0, Math.min(100, cb.total || 0))}%"></span></div>
    </div>`;

    // 탭2: 배당 타임라인(입상마 신호 각각의 대표 조합 변화)
    const why = rep.why_recommended || {};
    const tlBlocks = Object.keys(why).map((k) => {
      const s = why[k];
      if (!s.drop_timeline || !s.drop_timeline.length) return '';
      const rows = s.drop_timeline.map((p) => {
        const cls = p.change == null ? 'flat' : (p.change < 0 ? 'down' : '');
        const chg = p.change == null ? '—' : (p.change > 0 ? '+' : '') + p.change + '%';
        return `<tr><td>${esc(p.time || '')}${p.minutes_before != null ? ' (T-' + p.minutes_before + '분)' : ''}</td><td>${p.odds}배</td><td class="${cls}">${chg}</td></tr>`;
      }).join('');
      return `<div class="rpt-signal ${s.placed ? 'placed' : ''}">
        <b>${s.horse}번 ${s.placed ? '(' + s.place_rank + '착 입상)' : ''}</b> · 대표조합 ${(s.rep_combo || []).join('+')}
        <table class="rpt-tl"><thead><tr><th>시각</th><th>복승배당</th><th>변화</th></tr></thead><tbody>${rows}</tbody></table>
      </div>`;
    }).filter(Boolean).join('') || '<p class="hint">타임라인 데이터가 없습니다.</p>';
    const pane2 = `<div class="rpt-pane" data-pane="timeline"><h3>📉 배당 타임라인</h3>${tlBlocks}</div>`;

    // 탭3: 전적 분석
    const formRows = Object.keys(why).map((k) => {
      const s = why[k];
      return `<tr><td>${s.horse}번</td><td>${s.record_score != null ? s.record_score + '점' : '-'}</td><td>${s.placed ? '✅ ' + s.place_rank + '착' : '—'}</td></tr>`;
    }).join('') || '<tr><td colspan="3" class="hint">전적 정보 없음</td></tr>';
    const pane3 = `<div class="rpt-pane" data-pane="form"><h3>📊 전적 분석</h3>
      <table class="rpt-tl"><thead><tr><th>말</th><th>전적점수</th><th>결과</th></tr></thead><tbody>${formRows}</tbody></table></div>`;

    // 탭4: 이상감지 내역(신호별 근거)
    const anomRows = Object.keys(why).map((k) => {
      const s = why[k];
      const bits = [];
      if (s.excess_drop != null) bits.push(`초과급락 ${s.excess_drop}%p`);
      if (s.exacta_reversal) bits.push(`쌍승역전(비율 ${s.reversal_ratio})`);
      if (!bits.length) return '';
      return `<div class="rpt-signal ${s.placed ? 'placed' : ''}"><b>${s.grade || ''} ${s.horse}번</b> ${s.placed ? '(' + s.place_rank + '착)' : ''} — ${esc(bits.join(' + '))} <span class="hint">${esc(s.reason || '')}</span></div>`;
    }).filter(Boolean).join('') || '<p class="hint">감지된 이상신호가 없습니다.</p>';
    const pane4 = `<div class="rpt-pane" data-pane="anomaly"><h3>🔴 이상감지 내역</h3>${anomRows}</div>`;

    view.innerHTML = `
      <div class="rpt-head">${esc(rep.race || '')} · ${esc(resultStr)}</div>
      <div class="rpt-sub">${hitBadge}${oddsBadge} ${tagsHtml}</div>
      <div class="rpt-tabs">
        <button class="rpt-tab active" data-pane="story">추천 근거</button>
        <button class="rpt-tab" data-pane="timeline">배당 타임라인</button>
        <button class="rpt-tab" data-pane="form">전적 분석</button>
        <button class="rpt-tab" data-pane="anomaly">이상감지 내역</button>
      </div>
      ${pane1}${pane2}${pane3}${pane4}`;
    view.querySelectorAll('.rpt-tab').forEach((tb) => tb.addEventListener('click', () => {
      view.querySelectorAll('.rpt-tab').forEach((x) => x.classList.remove('active'));
      view.querySelectorAll('.rpt-pane').forEach((x) => x.classList.remove('active'));
      tb.classList.add('active');
      const p = view.querySelector('.rpt-pane[data-pane="' + tb.dataset.pane + '"]');
      if (p) p.classList.add('active');
    }));
  }

  // ══════════ [신규] 경주별 결과 입력 시스템 (대기목록·간단팝업·알림·미입력추적) ══════════
  let _pendingTimer = null;
  const _notifiedRaces = () => { try { return JSON.parse(localStorage.getItem('bmed_result_notified') || '{}'); } catch (_) { return {}; } };
  const _markNotified = (rk) => { try { const m = _notifiedRaces(); m[rk] = Date.now(); localStorage.setItem('bmed_result_notified', JSON.stringify(m)); } catch (_) { /* */ } };

  // [1·4번] 결과 입력 대기 목록 로드 + 미입력 추적 배너 + [3번] 알림 체크
  async function loadPendingResults() {
    const box = $('#pendingResultsList'); if (!box) return;
    let d = null;
    try { d = await (await fetch('/api/race-results/missing')).json(); } catch (_) { /* */ }
    const list = (d && d.missing) || [];
    const titleEl = $('#pendingResultsTitle');
    if (titleEl) titleEl.textContent = `📋 결과 입력 대기 (${list.length}경주)`;
    if (!list.length) { box.innerHTML = '<div class="pend-empty">✅ 모든 분석 경주의 결과가 입력되었습니다.</div>'; return; }
    // [4번] 미입력 추적 안내
    const warn = `<div class="pend-warn">⚠️ 결과 미입력 ${list.length}경주 — 지금 입력하면 AI 학습에 즉시 반영됩니다.</div>`;
    box.innerHTML = warn + list.map((m) => {
      const anom = m.hadAnomaly ? ' anom' : '';
      const mark = m.hadAnomaly ? '🔴' : '·';
      return `<div class="pend-item${anom}" data-rk="${esc(m.raceKey)}" data-rec="${esc(m.recommend || '')}">
        <div class="pi-main">
          <div class="pi-race">${mark} ${esc(m.race || m.raceKey)}</div>
          <div class="pi-rec">추천: ${esc(m.recommend || '추천 없음')}${m.updated_at ? ' · 갱신 ' + esc(m.updated_at) : ''}</div>
        </div>
        <button class="btn btn-primary pi-btn">결과 입력</button>
      </div>`;
    }).join('');
    box.querySelectorAll('.pend-item').forEach((it) => {
      it.querySelector('.pi-btn').addEventListener('click', () => openQuickResult(it.dataset.rk, it.dataset.rec));
    });
    checkResultNotify(list);
  }

  // [3번] 발주 후 ~30분 경과(마지막 갱신 기준) 미입력 경주 → 1회 알림(중복 방지)
  function checkResultNotify(list) {
    const now = Date.now();
    const done = _notifiedRaces();
    list.forEach((m) => {
      if (done[m.raceKey]) return;
      const hhmmss = m.updated_at || m.analyzed_at;
      if (!hhmmss) return;
      const parts = String(hhmmss).split(':').map((x) => parseInt(x, 10));
      if (parts.length < 2 || parts.some(isNaN)) return;
      const upd = new Date(); upd.setHours(parts[0], parts[1], parts[2] || 0, 0);
      if (now - upd.getTime() >= 30 * 60 * 1000) {   // 마지막 수집 30분 경과 = 경주 종료 추정
        _markNotified(m.raceKey);
        const msg = `🔔 ${m.race || m.raceKey} 결과를 입력하세요`;
        try { toast(msg); } catch (_) { /* */ }
        try {
          if (window.Notification && Notification.permission === 'granted') new Notification('경마 BMED', { body: msg });
          else if (window.Notification && Notification.permission !== 'denied') Notification.requestPermission();
        } catch (_) { /* */ }
      }
    });
  }

  // [2번] 경주별 간단 결과 입력 팝업 (1~4착 + 복승/삼복승 배당 → 저장)
  function openQuickResult(rk, recommend) {
    const old = document.querySelector('.qr-mask'); if (old) old.remove();
    const mask = document.createElement('div'); mask.className = 'qr-mask';
    mask.innerHTML = `<div class="qr-modal">
      <h3>결과 입력</h3>
      <div class="qr-sub">${esc(rk)}${recommend ? ' · 추천 ' + esc(recommend) : ''}</div>
      <div class="qr-grid">
        <div><label>1착</label><input id="qr1" type="number" min="1" max="18" inputmode="numeric"></div>
        <div><label>2착</label><input id="qr2" type="number" min="1" max="18" inputmode="numeric"></div>
        <div><label>3착</label><input id="qr3" type="number" min="1" max="18" inputmode="numeric"></div>
        <div><label>4착</label><input id="qr4" type="number" min="1" max="18" inputmode="numeric"></div>
      </div>
      <div class="qr-odds">
        <div><label>복승 확정배당</label><input id="qrQO" type="number" min="0" step="0.1" placeholder="예: 5.8"></div>
        <div><label>삼복승 확정배당</label><input id="qrTO" type="number" min="0" step="0.1" placeholder="예: 22.1"></div>
      </div>
      <div class="qr-odds">
        <div><label>투자금액(원)</label><input id="qrStake" type="number" min="0" step="100" placeholder="예: 10000"></div>
        <div><label>실수령 배당금(원)</label><input id="qrPayout" type="number" min="0" step="100" placeholder="공란=확정배당 추정"></div>
      </div>
      <div class="qr-msg" id="qrMsg"></div>
      <div class="qr-actions">
        <button class="btn" id="qrCancel">취소</button>
        <button class="btn btn-primary" id="qrSave">저장</button>
      </div>
    </div>`;
    document.body.appendChild(mask);
    const close = () => mask.remove();
    mask.addEventListener('click', (e) => { if (e.target === mask) close(); });
    mask.querySelector('#qrCancel').addEventListener('click', close);
    mask.querySelector('#qrSave').addEventListener('click', () => saveQuickResult(rk, mask));
    const f1 = mask.querySelector('#qr1'); if (f1) f1.focus();
    mask.querySelectorAll('input').forEach((inp) => inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') saveQuickResult(rk, mask); }));
  }

  async function saveQuickResult(rk, mask) {
    const g = (id) => { const e = mask.querySelector(id); return e ? e.value.trim() : ''; };
    const msg = mask.querySelector('#qrMsg');
    const n1 = g('#qr1');
    if (!n1) { if (msg) { msg.style.color = 'var(--red)'; msg.textContent = '최소 1착은 입력하세요.'; } return; }
    const result = { '1st': parseInt(n1, 10) };
    ['2', '3', '4'].forEach((k) => { const v = g('#qr' + k); if (v) result[k + (k === '2' ? 'nd' : k === '3' ? 'rd' : 'th')] = parseInt(v, 10); });
    const payload = { raceKey: rk, result };
    const qo = g('#qrQO'), to = g('#qrTO'), stake = g('#qrStake'), payout = g('#qrPayout');
    if (qo) payload.quinellaOdds = parseFloat(qo);
    if (to) payload.trifectaOdds = parseFloat(to);
    if (qo || to) payload.finalOdds = { quinella: qo ? parseFloat(qo) : undefined, trifecta: to ? parseFloat(to) : undefined };
    if (stake) { payload.stake = parseInt(stake, 10); payload.budget = parseInt(stake, 10); }
    if (payout) payload.payout = parseInt(payout, 10);
    if (msg) { msg.style.color = ''; msg.textContent = '저장·판정 중…'; }
    let d;
    try { d = await (await fetch('/api/history/record-result', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })).json(); }
    catch (e) { if (msg) { msg.style.color = 'var(--red)'; msg.textContent = '실패: ' + e.message; } return; }
    if (d.error) { if (msg) { msg.style.color = 'var(--red)'; msg.textContent = d.error; } return; }
    const rec = d.record || {};
    const bits = [];
    if (rec.quinella_hit) bits.push('복승 적중');
    if (rec.trifecta_hit) bits.push('삼복승 적중');
    if (!bits.length) bits.push('미적중');
    if (rec.pnl != null) bits.push((rec.pnl >= 0 ? '+' : '') + Number(rec.pnl).toLocaleString() + '원');
    _markNotified(rk);   // 입력 완료 → 알림 대상 제거
    if (msg) { msg.style.color = '#38d39f'; msg.textContent = '✅ ' + bits.join(' · ') + ' — 학습 반영됨'; }
    // 목록·통계·리포트·명예의전당 즉시 갱신 후 팝업 닫기
    setTimeout(() => { mask.remove(); }, 900);
    try { loadPendingResults(); } catch (_) { /* */ }
    try { loadHighlights(); loadReportList(); } catch (_) { /* */ }
    try { loadLearningStats(); } catch (_) { /* */ }
    try { renderStats(); } catch (_) { /* */ }
    try { if (typeof loadHistoryList === 'function') loadHistoryList(); } catch (_) { /* */ }
  }

  // [v2.0.0] 자동수집 상태바 — 확장 백그라운드 엔진 상태를 서버 브리지(/api/auto/status)로
  //   폴링해 "🟢 자동수집 중 | 마지막 | 다음 | 발주까지" 를 화면 하단에 항상 표시.
  function initAutoStatusBar() {
    if (document.getElementById('autoStatusBar')) return;
    const bar = document.createElement('div');
    bar.id = 'autoStatusBar';
    bar.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:99998;padding:5px 12px;'
      + 'font:600 12px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;'
      + 'color:#e2e8f0;border-top:1px solid #334155;display:none;text-align:center;letter-spacing:.2px';
    document.body.appendChild(bar);
    const two = (n) => String(n).padStart(2, '0');
    const hms = (ms) => { const d = new Date(ms); return two(d.getHours()) + ':' + two(d.getMinutes()) + ':' + two(d.getSeconds()); };
    const cd = (ms) => { const s = Math.max(0, Math.round(ms / 1000)); return Math.floor(s / 60) + '분 ' + two(s % 60) + '초'; };
    async function tick() {
      let s = null;
      try { s = await (await fetch('/api/auto/status')).json(); } catch (_) { /* */ }
      const now = Date.now();
      if (!s || (!s.running && !s.stopped)) { bar.style.display = 'none'; return; }
      bar.style.display = 'block';
      if (s.stopped) { bar.style.background = '#1e293b'; bar.textContent = '⏹ 자동수집 중지됨 (발주 마감)'; return; }
      const active = !!(s.running && s.last && (now - s.last < 90000));
      const parts = [active ? '🟢 자동수집 중' : '🟡 자동수집 대기'];
      if (s.last) parts.push('마지막 ' + hms(s.last));
      if (s.next) parts.push('다음 ' + hms(s.next));
      if (s.deadline && s.deadline > now) parts.push('발주까지 ' + cd(s.deadline - now));
      if (s.warn) parts.push('⚠ ' + s.warn);
      bar.style.background = active ? '#0f291b' : '#0f172a';
      bar.textContent = parts.join('   |   ');
    }
    tick();
    setInterval(tick, 2000);
  }

  // ══════════ [보완] 이상감지 누적 피드 + 마감 전 단계 알림 (T-1:30 / T-1:00 / T-30초) ══════════
  //  · 이상감지: 서버 스냅샷(영구)에서 누적·중복제거 → 새 수집/마감 후에도 유지(기존 감지 삭제 안 함)
  //  · 단계 알림: /api/auto/status 의 deadline 으로 남은시간 계산 → 소리 + 화면 강조 + 누적이상 + 베팅요약
  function beepTimes(n, freq) {
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      const ctx = new AC();
      for (let i = 0; i < n; i++) {
        const t = i * 0.5;
        const o = ctx.createOscillator(), g = ctx.createGain();
        o.connect(g); g.connect(ctx.destination);
        o.type = 'sine'; o.frequency.value = freq || 880;
        g.gain.setValueAtTime(0.0001, ctx.currentTime + t);
        g.gain.exponentialRampToValueAtTime(0.35, ctx.currentTime + t + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + t + 0.42);
        o.start(ctx.currentTime + t); o.stop(ctx.currentTime + t + 0.45);
      }
    } catch (_) { /* 오디오 미지원 무시 */ }
  }

  const _closing = { firedRk: null, fired: new Set(), tick: 0, lastEvents: [], panelRk: null, historyMode: false,
    manualDeadlineMs: 0, manualRk: null };   // [보완] 수동 발주시각(한국 PDF 등 서버 deadline 없을 때 폴백)

  /** raceKey → 짧은 라벨(예: '2026-07-05_서울_5' → '2026-07-05 서울 5R') */
  function _rkLabel(rk) {
    if (!rk) return '';
    return String(rk).replace(/_/g, ' ').replace(/(\d+)\s*경주/, '$1R').trim();
  }

  /** [1번] 패널이 표시할 '현재 경주' 설정 — 한국·일본 흐름 모두 여기로 현재 raceKey를 알려준다 */
  function setAnomalyPanelRace(rk) {
    if (!rk || _closing.panelRk === rk) return;
    _closing.panelRk = rk;
    _closing.historyMode = false;   // 새 경주로 전환 시 자동으로 '현재 보기'로 복귀
    refreshAnomalyFeed(rk);
  }

  /** 이상감지 이벤트 배열 → 행 HTML(시각·발주전·심각도색) */
  function _anomalyRows(ev) {
    return (ev || []).map((e) => {
      const mb = e.minutes_before != null ? ` <span style="color:#64748b">${e.minutes_before}분전</span>` : '';
      const col = e.severity === '🔴' ? '#f87171' : '#fbbf24';
      return `<div style="padding:1px 0 1px 8px"><span style="color:#94a3b8">${esc(e.time || '')}</span>${mb} <span style="color:${col};font-weight:700">${e.severity} ${esc(e.text)}</span></div>`;
    }).join('');
  }

  function _panelHeader(titleHtml, rightHtml) {
    return `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px;gap:6px">
        <b style="color:#fca5a5">${titleHtml}</b>
        <span style="white-space:nowrap">${rightHtml || ''}<span id="anomalyFeedClose" style="cursor:pointer;color:#64748b;padding:0 4px">✕</span></span></div>`;
  }
  function _wireFeedButtons(panel) {
    const cl = panel.querySelector('#anomalyFeedClose');
    if (cl) cl.addEventListener('click', () => { panel.style.display = 'none'; });
    const hb = panel.querySelector('#anomalyHistBtn');
    if (hb) hb.addEventListener('click', () => renderAnomalyHistory());
    const bk = panel.querySelector('#anomalyBackBtn');
    if (bk) bk.addEventListener('click', () => { _closing.historyMode = false; refreshAnomalyFeed(_closing.panelRk || getActiveRaceKey()); });
  }

  /** [1·2번] 현재 경주 이상감지 누적 — [raceKey] 헤더 블록 1개(다른 경주와 섞이지 않음). 한국·일본 공통. */
  async function refreshAnomalyFeed(rk) {
    const panel = document.getElementById('anomalyFeedPanel'); if (!panel) return;
    if (_closing.historyMode) return;                 // 히스토리 보기 중이면 현재 갱신 보류
    if (!rk) { panel.style.display = 'none'; return; }
    let d;
    try { d = await (await fetch('/api/odds/anomaly-feed', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ raceKey: rk }) })).json(); }
    catch (_) { return; }
    const ev = (d && d.events) || [];
    _closing.lastEvents = ev;
    if (!ev.length) { panel.style.display = 'none'; return; }
    panel.style.display = 'block';
    panel.innerHTML = _panelHeader(`🚨 이상감지 누적 (${ev.length})`,
        `<span id="anomalyHistBtn" title="이전 경주 이상감지 보기" style="cursor:pointer;color:#8ab4f8;padding:0 6px;font-weight:600">📜 히스토리</span>`)
      + `<div style="color:#e2e8f0;font-weight:700;margin:2px 0">[${esc(_rkLabel(rk))}]</div>${_anomalyRows(ev)}`;
    _wireFeedButtons(panel);
  }

  /** [3번] 히스토리 보기 — 이상감지가 있는 모든 과거 경주를 [raceKey] 블록으로 분리 표시 */
  async function renderAnomalyHistory() {
    const panel = document.getElementById('anomalyFeedPanel'); if (!panel) return;
    _closing.historyMode = true;
    panel.style.display = 'block';
    const backBtn = `<span id="anomalyBackBtn" title="현재 경주로" style="cursor:pointer;color:#8ab4f8;padding:0 6px;font-weight:600">◀ 현재</span>`;
    panel.innerHTML = _panelHeader('🚨 이상감지 히스토리', backBtn) + '<div class="hint" style="padding:4px 8px">불러오는 중…</div>';
    _wireFeedButtons(panel);
    let list;
    try { list = ((await (await fetch('/api/history/list')).json()) || {}).races || []; }
    catch (_) { panel.innerHTML = _panelHeader('🚨 이상감지 히스토리', backBtn) + '<div class="hint" style="padding:4px 8px">목록 로드 실패</div>'; _wireFeedButtons(panel); return; }
    const cur = _closing.panelRk || getActiveRaceKey();
    const withAnom = list.filter((r) => (r.anomalyCount || 0) > 0).sort((a, b) => (b.lastT || 0) - (a.lastT || 0));
    if (!withAnom.length) {
      panel.innerHTML = _panelHeader('🚨 이상감지 히스토리', backBtn) + '<div class="hint" style="padding:4px 8px">이상감지 기록이 있는 경주가 없습니다.</div>';
      _wireFeedButtons(panel); return;
    }
    const blocks = await Promise.all(withAnom.slice(0, 30).map(async (r) => {
      let ev = [];
      try { ev = ((await (await fetch('/api/odds/anomaly-feed', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ raceKey: r.raceKey }) })).json()) || {}).events || []; }
      catch (_) { /* */ }
      if (!ev.length) return '';
      const badge = (r.raceKey === cur) ? ' <span style="color:#38d39f;font-size:10px">● 현재</span>' : '';
      const shown = ev.slice(-40);           // 경주당 최근 40건(히스토리 과밀 방지)
      const more = ev.length > shown.length ? `<div class="hint" style="padding:1px 8px">…외 ${ev.length - shown.length}건 더</div>` : '';
      return `<div style="margin:4px 0 6px;border-top:1px solid #1e293b;padding-top:4px">
        <div style="color:#e2e8f0;font-weight:700">[${esc(_rkLabel(r.raceKey || r.race))}]${badge} <span class="hint" style="font-weight:400">${ev.length}건</span></div>${_anomalyRows(shown)}${more}</div>`;
    }));
    panel.innerHTML = _panelHeader(`🚨 이상감지 히스토리 (${withAnom.length}경주)`, backBtn) + (blocks.filter(Boolean).join('') || '<div class="hint" style="padding:4px 8px">기록 없음</div>');
    _wireFeedButtons(panel);
  }

  /** [3번] 알림에 넣을 누적 이상감지 요약(최근 max건) */
  function _anomalySummaryHtml(max) {
    const ev = _closing.lastEvents || [];
    if (!ev.length) return '<div style="opacity:.85">감지된 이상 없음</div>';
    return ev.slice(-(max || 6)).map((e) =>
      `<div>${e.severity} ${esc(e.text)}${e.time ? ` <span style="opacity:.7">(${esc(e.time)})</span>` : ''}</div>`).join('');
  }

  /** 알림에 넣을 메인 베팅(복승/삼복승) — /api/odds/triple/analyze 의 betRecommend 사용 */
  async function _mainBetsHtml(rk) {
    let a;
    try { a = await (await fetch('/api/odds/triple/analyze', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ raceKey: rk }) })).json(); }
    catch (_) { return ''; }
    const recs = (a && a.betRecommend) || [];
    const q = recs.filter((r) => r.kind === '복승').sort((x, y) => (y.alloc || 0) - (x.alloc || 0))[0];
    const t = recs.filter((r) => r.kind === '삼복승').sort((x, y) => (y.alloc || 0) - (x.alloc || 0))[0];
    const line = (r, tag) => r ? `<div style="font-size:16px;margin:2px 0"><b>${tag}</b> ${(r.combo || []).join('+')}${r.expOdds != null ? ` <span style="opacity:.8">${r.expOdds}배</span>` : ''}</div>` : '';
    return line(q, '복승') + line(t, '삼복승') || '<div style="opacity:.85">추천 조합 없음</div>';
  }

  const CLOSING_STAGES = [
    { id: 't90', at: 90, beeps: 2, freq: 880, bg: '#b45309', title: '⚠️ 마감 1분 30초 전 — 현재 이상감지',
      showAnom: true, betLabel: '추천:', foot: '', plain: '⚠️ 마감 1분 30초 전', autoHide: 25000 },
    { id: 't60', at: 60, beeps: 3, freq: 950, bg: '#b91c1c', title: '🚨 마감 1분 전 — 최종 베팅',
      showAnom: true, betLabel: '최종 베팅(메인):', foot: '지금 베팅하세요!', plain: '🚨 마감 1분 전 · 최종 베팅', autoHide: 30000 },
    { id: 't30', at: 30, beeps: 4, freq: 1046, bg: '#7f1d1d', border: '2px solid #fca5a5', title: '⏰ 30초! 마지막 기회!',
      showAnom: false, betLabel: '', foot: '지금 베팅하세요!', plain: '⏰ 30초! 마지막 기회!', autoHide: 20000 },
  ];

  async function fireClosingAlert(st, rk) {
    beepTimes(st.beeps, st.freq);
    const overlay = document.getElementById('closingAlert'); if (!overlay) return;
    const anom = _anomalySummaryHtml(6);
    const bets = rk ? await _mainBetsHtml(rk) : '';
    overlay.style.background = st.bg;
    overlay.style.border = st.border || 'none';
    overlay.dataset.stage = st.id;
    overlay.innerHTML = `<div style="font-size:18px;margin-bottom:6px">${st.title}</div>
      ${st.showAnom ? `<div style="margin:4px 0"><div style="opacity:.85;font-size:13px">오늘 감지된 이상:</div>${anom}</div>` : ''}
      ${bets ? `<div style="margin:6px 0 2px;border-top:1px solid rgba(255,255,255,.25);padding-top:6px">${st.betLabel || '추천 베팅'}<br>${bets}</div>` : ''}
      ${st.foot ? `<div style="margin-top:8px;font-size:17px;font-weight:800">${st.foot}</div>` : ''}
      <div style="opacity:.6;font-size:11px;margin-top:6px">클릭하면 닫힘</div>`;
    overlay.style.display = 'block';
    if (st.autoHide) setTimeout(() => { if (overlay.dataset.stage === st.id) overlay.style.display = 'none'; }, st.autoHide);
    try { notify(st.plain, false); } catch (_) { /* */ }
  }

  async function closingTick() {
    _closing.tick++;
    let rk = _closing.panelRk || getActiveRaceKey();   // 패널 현재경주(한국·일본 흐름이 지정) 우선
    if (!rk) { try { const d = await (await fetch('/api/odds/triple/latest')).json(); rk = d && d.raceKey; if (rk) _closing.panelRk = rk; } catch (_) { /* */ } }
    if (rk && !_closing.historyMode && _closing.tick % 3 === 0) refreshAnomalyFeed(rk);   // 현재 보기일 때만 3초 갱신
    let s = null;
    try { s = await (await fetch('/api/auto/status')).json(); } catch (_) { /* 서버 상태 없어도 수동 폴백은 진행 */ }
    const dl = s && s.deadline;
    let dlMs = dl ? (dl > 1e12 ? dl : dl * 1000) : 0;
    // [보완] 서버 발주시각 없으면(한국 PDF 사전분석 등) 현재 경주에 설정된 수동 발주시각으로 폴백
    if (!dlMs && _closing.manualDeadlineMs > Date.now()
        && (!_closing.manualRk || _closing.manualRk === (rk || _closing.panelRk))) {
      dlMs = _closing.manualDeadlineMs;
    }
    if (!dlMs) return;
    const left = dlMs - Date.now();
    const key = rk || String(dlMs);
    if (_closing.firedRk !== key) {   // 경주 바뀌면 리셋 + 이미 지난 단계는 조용히 소진(늦게 열어도 스팸 방지)
      _closing.firedRk = key; _closing.fired = new Set();
      CLOSING_STAGES.forEach((st) => { if (left <= st.at * 1000) _closing.fired.add(st.id); });
    }
    for (const st of CLOSING_STAGES) {
      if (left <= st.at * 1000 && left > 0 && !_closing.fired.has(st.id)) {
        _closing.fired.add(st.id);
        fireClosingAlert(st, rk);
      }
    }
  }

  /** 'HH:MM' → 오늘(지났으면 다음날) 발주시각 epoch ms. 0=미설정/오류. */
  function _timeToDeadlineMs(hhmm) {
    if (!hhmm) return 0;
    const [h, m] = String(hhmm).split(':').map(Number);
    if (isNaN(h)) return 0;
    const d = new Date(); d.setHours(h, m || 0, 0, 0);
    let ms = d.getTime();
    if (ms < Date.now() - 60000) ms += 24 * 3600 * 1000;   // 이미 지난 시각이면 다음날
    return ms;
  }

  const KOREA_DL_KEY = 'bmed_korea_deadline';   // [보완] 수동 발주시각 새로고침 유지

  /** epoch ms → 'HH:MM' (input[type=time] 복원용) */
  function _msToHHMM(ms) {
    const d = new Date(ms);
    return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
  }

  /** 상태 텍스트 + 해제버튼 갱신(설정/복원 공용) */
  function _renderKoreaDeadlineStatus(hhmm, ms) {
    const st = document.getElementById('koreaDeadlineStatus');
    const clr = document.getElementById('koreaDeadlineClear');
    const left = Math.max(0, Math.round((ms - Date.now()) / 60000));
    if (st) st.textContent = `✅ 발주 ${hhmm} 설정 · 약 ${left}분 후 · 마감 전 알림 활성${_closing.manualRk ? ' (' + _rkLabel(_closing.manualRk) + ')' : ''}`;
    if (clr) clr.style.display = '';
  }

  /** [보완] 한국 수동 발주시각 설정 → 현재 경주에 마감 전 3단계 알림 발동(서버 deadline 없을 때 폴백) */
  function setKoreaManualDeadline() {
    const inp = document.getElementById('koreaDeadline');
    const st = document.getElementById('koreaDeadlineStatus');
    const ms = _timeToDeadlineMs(inp && inp.value);
    if (!ms) { if (st) st.textContent = '⚠️ 발주시각(HH:MM)을 입력하세요.'; return; }
    _closing.manualDeadlineMs = ms;
    _closing.manualRk = _closing.panelRk || getActiveRaceKey() || null;
    _closing.firedRk = null;   // 새 발주시각 → 단계 알림 재무장
    _renderKoreaDeadlineStatus(inp.value, ms);
    try { localStorage.setItem(KOREA_DL_KEY, JSON.stringify({ ms, rk: _closing.manualRk })); } catch (_) { /* */ }
    try { closingTick(); } catch (_) { /* */ }
  }

  function clearKoreaManualDeadline() {
    _closing.manualDeadlineMs = 0; _closing.manualRk = null; _closing.firedRk = null;
    const st = document.getElementById('koreaDeadlineStatus');
    const clr = document.getElementById('koreaDeadlineClear');
    if (st) st.textContent = '해제됨. 배당판 없이 PDF만 볼 때 발주시각을 입력하면 마감 전 알림이 뜹니다.';
    if (clr) clr.style.display = 'none';
    try { localStorage.removeItem(KOREA_DL_KEY); } catch (_) { /* */ }
  }

  /** [보완] 새로고침 시 저장된 수동 발주시각 복원(미래 시각만; 지났으면 정리) */
  function restoreKoreaManualDeadline() {
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(KOREA_DL_KEY) || 'null'); } catch (_) { saved = null; }
    if (!saved || !saved.ms) return;
    if (saved.ms <= Date.now()) { try { localStorage.removeItem(KOREA_DL_KEY); } catch (_) { /* */ } return; }
    _closing.manualDeadlineMs = saved.ms;
    _closing.manualRk = saved.rk || null;
    _closing.firedRk = null;
    const inp = document.getElementById('koreaDeadline');
    if (inp) inp.value = _msToHHMM(saved.ms);
    _renderKoreaDeadlineStatus(_msToHHMM(saved.ms), saved.ms);
  }

  function initClosingWatch() {
    if (document.getElementById('anomalyFeedPanel')) return;
    const feed = document.createElement('div');
    feed.id = 'anomalyFeedPanel';
    feed.style.cssText = 'position:fixed;left:0;bottom:30px;z-index:99997;width:320px;max-width:86vw;max-height:44vh;overflow:auto;'
      + 'background:#0f172a;border:1px solid #334155;border-radius:0 8px 0 0;padding:8px 10px;display:none;'
      + 'font:600 12px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#e2e8f0;box-shadow:0 -2px 12px rgba(0,0,0,.4)';
    document.body.appendChild(feed);
    const overlay = document.createElement('div');
    overlay.id = 'closingAlert';
    overlay.style.cssText = 'position:fixed;top:44px;left:50%;transform:translateX(-50%);z-index:2147483646;'
      + 'width:min(92vw,560px);display:none;padding:14px 18px;border-radius:12px;box-shadow:0 10px 34px rgba(0,0,0,.55);'
      + 'font:700 15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#fff;cursor:pointer';
    overlay.addEventListener('click', () => { overlay.style.display = 'none'; });
    document.body.appendChild(overlay);
    // [보완] 한국 수동 발주시각 컨트롤 배선
    { const b = document.getElementById('koreaDeadlineSet'); if (b) b.addEventListener('click', setKoreaManualDeadline); }
    { const b = document.getElementById('koreaDeadlineClear'); if (b) b.addEventListener('click', clearKoreaManualDeadline); }
    { const i = document.getElementById('koreaDeadline'); if (i) i.addEventListener('keydown', (e) => { if (e.key === 'Enter') setKoreaManualDeadline(); }); }
    restoreKoreaManualDeadline();   // [보완] 새로고침 시 저장된 발주시각 복원
    setInterval(closingTick, 1000);
    closingTick();
  }

  // ══════════ [분석 로그] 완전 기록 UI (통계 탭) ══════════
  async function loadAnalysisLogList() {
    const el = document.querySelector('#logRaceList'); if (!el) return;
    el.innerHTML = '<p class="hint">불러오는 중…</p>';
    let d; try { d = await (await fetch('/api/analysis-log/list')).json(); }
    catch (e) { el.innerHTML = `<p class="hint" style="color:var(--red)">${esc(e.message)}</p>`; return; }
    const logs = d.logs || [];
    if (!logs.length) { el.innerHTML = '<p class="hint">로그가 없습니다. [오늘 로그 즉시 생성]을 눌러 생성하세요.</p>'; return; }
    const byDate = {};
    logs.forEach((l) => { (byDate[l.date || '?'] = byDate[l.date || '?'] || []).push(l); });
    el.innerHTML = Object.keys(byDate).sort().reverse().map((date) =>
      `<div style="margin-bottom:8px"><div class="hint" style="font-weight:700;margin:4px 0">${esc(date)}</div>`
      + byDate[date].map((l) => `<div class="race-chip ${l.hasResult ? 'chip-done' : 'chip-todo'}" data-file="${esc(l.file)}" style="cursor:pointer;margin:3px 0;display:block">
        <b>${esc(l.race || l.race_id || '')}</b> <span class="chip-page">${esc(l.analyzed_at || '')} · ${l.snaps}스냅 · 신호${l.signals}${l.hasResult ? ' · ✅결과' : ''}</span></div>`).join('')
      + '</div>').join('');
    el.querySelectorAll('.race-chip').forEach((c) => c.addEventListener('click', () => openAnalysisLog(c.dataset.file)));
  }

  async function openAnalysisLog(file) {
    const el = document.querySelector('#logDetail'); if (!el) return;
    el.innerHTML = '<p class="hint">불러오는 중…</p>';
    let d; try { d = await (await fetch('/api/analysis-log/get', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file }) })).json(); }
    catch (e) { el.innerHTML = `<p class="hint" style="color:var(--red)">${esc(e.message)}</p>`; return; }
    if (d.error) { el.innerHTML = `<p class="hint">${esc(d.error)}</p>`; return; }
    el.innerHTML = renderAnalysisLog(d);
    const btn = document.querySelector('#logMemoSave');
    if (btn) btn.addEventListener('click', () => saveLogMemo(file));
  }

  function renderAnalysisLog(d) {
    const inp = d.input_data || {}, tl = d.odds_timeline || [], sig = d.signals_detected || [];
    const horses = d.horses || [], fr = d.final_recommendation || {}, elim = d.elimination || {};
    const res = d.result, hit = d.hit || {};
    const sev = (s) => /🔴|🚨/.test(s || '') ? 'chip-red' : '';
    const inputHtml = `<div class="matrix-title" style="font-size:13px">📥 입력 데이터</div>
      <div class="hint">소스: ${esc(inp.source || '-')}${inp.odds_source ? ' · 배당: ' + esc(inp.odds_source) : ''}${inp.image_file ? ' · 이미지: ' + esc(inp.image_file) : ''}${inp.pdf_file ? ' · PDF: ' + esc(inp.pdf_file) : ''}</div>`;
    const tlRows = tl.slice(-12).map((s) => {
      const q = Object.entries(s.quinella || {}).sort((a, b) => a[1] - b[1]).slice(0, 4).map(([k, v]) => `${k}:${v}`).join(' · ');
      return `<div class="hint"><b>${esc(s.time || '')}</b> ${s.minutes_before != null ? '(' + s.minutes_before + '분전)' : ''} ${esc(q)}</div>`;
    }).join('');
    const tlHtml = `<div class="matrix-title" style="font-size:13px;margin-top:8px">📈 배당 타임라인 <span class="hint" style="font-weight:400">${tl.length}회</span></div>${tlRows || '<div class="hint">수집 없음</div>'}`;
    const sigHtml = `<div class="matrix-title" style="font-size:13px;margin-top:8px">🚨 이상감지 신호</div>`
      + (sig.length ? sig.slice(0, 12).map((s) => `<div style="margin:2px 0"><span class="chip ${sev(s.severity)}">${esc(s.severity || '')} ${esc(s.type || '')}</span> ${esc(s.detail || '')}${s.reason ? ` <span class="hint">— ${esc(s.reason)}</span>` : ''}</div>`).join('') : '<div class="hint">없음</div>');
    const hRows = horses.slice(0, 16).map((h) => `<tr><td><b>${esc(String(h.grade || '-'))}</b></td><td>${h.no}</td><td>${esc(h.name || '')}</td><td>${h.record_score != null ? h.record_score : '-'}</td><td>${h.odds != null ? h.odds : '-'}</td><td class="hint">${esc(h.grade_reason || '')}</td></tr>`).join('');
    const hHtml = `<div class="matrix-title" style="font-size:13px;margin-top:8px">🏇 추천 이유 (말별)</div>
      <table class="data-table"><thead><tr><th>등급</th><th>마번</th><th>마명</th><th>전적</th><th>배당</th><th>이유</th></tr></thead><tbody>${hRows}</tbody></table>`;
    const elimHtml = `<div class="hint" style="margin-top:6px">후보: <b>${(elim.candidates || []).join(', ') || '-'}</b> · 제거: ${(elim.eliminated || []).join(', ') || '-'}</div>`;
    const recRow = (k, label) => { const r = fr[k]; return r ? `<tr><td>${label}</td><td style="font-weight:700">${esc(r.combo)}</td><td>${r.odds != null ? r.odds + '배' : '-'}</td><td class="hint">${esc(r.reason || '')}</td></tr>` : ''; };
    const frHtml = `<div class="matrix-title" style="font-size:13px;margin-top:8px">🎯 추천 조합</div>
      <table class="data-table"><thead><tr><th>종류</th><th>조합</th><th>배당</th><th>이유</th></tr></thead><tbody>${recRow('quinella_main', '복승 메인')}${recRow('quinella_sub', '복승 보조')}${recRow('trifecta_main', '삼복승 메인')}${recRow('trifecta_insurance1', '삼복승 보험1')}${recRow('trifecta_insurance2', '삼복승 보험2')}</tbody></table>`;
    let resHtml = '<div class="hint" style="margin-top:8px">🏁 결과 미입력</div>';
    if (res) {
      const yn = (b) => b ? '<span style="color:#38d39f">✅ 적중</span>' : '<span style="color:#f87171">❌ 미적중</span>';
      resHtml = `<div class="matrix-title" style="font-size:13px;margin-top:8px">🏁 실제 결과</div>
        <div><b>1착 ${res['1st'] != null ? res['1st'] : '?'} / 2착 ${res['2nd'] != null ? res['2nd'] : '?'} / 3착 ${res['3rd'] != null ? res['3rd'] : '?'}</b></div>
        <div style="margin-top:2px">복승 ${yn(hit.quinella_hit)} · 삼복승 ${yn(hit.trifecta_hit)}${hit.anomaly_was_correct != null ? ' · 이상감지 ' + yn(hit.anomaly_was_correct) : ''}</div>`;
    }
    const memoHtml = `<div class="matrix-title" style="font-size:13px;margin-top:8px">📝 복기 메모</div>
      <textarea id="logMemoInput" class="cfg-input" style="width:100%;min-height:70px" placeholder="이 경주 복기 메모…">${esc(d.review || '')}</textarea>
      <div class="cfg-row" style="margin-top:4px"><button id="logMemoSave" class="btn btn-primary">메모 저장</button> <span id="logMemoMsg" class="hint"></span></div>`;
    return `<div style="border:1px solid var(--border);border-radius:8px;padding:10px">
      <div class="matrix-title">${esc(d.race || d.race_id || '')} <span class="hint" style="font-weight:400">${esc(d.date || '')} · 분석 ${esc(d.analyzed_at || '')}</span></div>
      ${inputHtml}${sigHtml}${hHtml}${elimHtml}${frHtml}${tlHtml}${resHtml}${memoHtml}</div>`;
  }

  async function saveLogMemo(file) {
    const ta = document.querySelector('#logMemoInput'); if (!ta) return;
    const msg = document.querySelector('#logMemoMsg');
    try {
      const r = await (await fetch('/api/analysis-log/memo', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file, review: ta.value }) })).json();
      if (msg) msg.textContent = r.ok ? '✅ 저장됨' : (r.error || '실패');
    } catch (e) { if (msg) msg.textContent = '실패: ' + e.message; }
  }

  function initAnalysisLog() {
    const q = (s) => document.querySelector(s);
    const msg = q('#logMsg');
    const rb = q('#logRefreshBtn'); if (rb) rb.addEventListener('click', loadAnalysisLogList);
    const bf = q('#logBackfillBtn'); if (bf) bf.addEventListener('click', async () => {
      if (msg) msg.textContent = '오늘까지의 경주 로그 생성 중…';
      try { const r = await (await fetch('/api/analysis-log/backfill', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })).json();
        if (msg) msg.textContent = `✅ ${r.count}개 로그 생성/갱신`; loadAnalysisLogList();
      } catch (e) { if (msg) msg.textContent = '실패: ' + e.message; }
    });
    const bk = q('#logBackupBtn'); if (bk) bk.addEventListener('click', async () => {
      if (msg) msg.textContent = 'GitHub 백업 중…';
      try { const r = await (await fetch('/api/analysis-log/backup', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })).json();
        if (msg) msg.textContent = r.committed ? (r.pushed ? '✅ GitHub 백업 완료' : '✅ 커밋됨(푸시 건너뜀)') : ('커밋 없음: ' + esc(r.msg || ''));
      } catch (e) { if (msg) msg.textContent = '실패: ' + e.message; }
    });
  }

  // ══════════ [일본경마 복기] 분석 내역 목록 + 결과 입력 + 자동 판정 리포트 ══════════
  const JP_KR_TRACKS = ['서울', '부산', '부경', '제주', '과천'];
  function jpIsKoreaName(s) { return JP_KR_TRACKS.some((t) => (s || '').indexOf(t) >= 0); }
  function jpIsJapanName(s) { s = (s || '').trim(); return !!s && !jpIsKoreaName(s) && !/TEST/i.test(s); }
  function jpTodayStr() { const n = new Date(); return `${n.getFullYear()}-${String(n.getMonth() + 1).padStart(2, '0')}-${String(n.getDate()).padStart(2, '0')}`; }
  function jpRecSummary(fr) {
    fr = fr || {}; const parts = [];
    if (fr.quinella_main && fr.quinella_main.combo) parts.push('복승 ' + fr.quinella_main.combo);
    if (fr.trifecta_main && fr.trifecta_main.combo) parts.push('삼복승 ' + fr.trifecta_main.combo);
    return parts.join(' / ') || '추천 없음';
  }

  // [3번] 오늘 분석했으나 결과 미입력 경주 추적 배너(누락 방지)
  async function renderMissingBanner() {
    try {
      const m = await (await fetch('/api/race-results/missing')).json();
      if (!m || !m.count) return '';
      const names = (m.missing || []).map((x) => esc(x.raceKey || x.race)).join(' / ');
      return `<div style="margin:0 0 8px;padding:8px 10px;border-left:3px solid #ff5c5c;background:rgba(255,92,92,.1);border-radius:6px;color:#ff8a8a">
        ⚠️ <b>오늘 결과 미입력: ${m.count}경주</b><br><span class="hint" style="color:#ffb0b0">${names}</span>
        <div class="hint" style="margin-top:3px;font-size:11px">아래 목록에서 경주를 눌러 결과를 입력하세요 (완전 저장 → AI 학습 반영).</div></div>`;
    } catch (_) { return ''; }
  }

  async function loadJapanReviewList() {
    const el = document.querySelector('#jpReviewList'); if (!el) return;
    el.innerHTML = '<p class="hint">불러오는 중…</p>';
    const missBanner = await renderMissingBanner();
    let d; try { d = await (await fetch('/api/analysis-log/list')).json(); }
    catch (e) { el.innerHTML = `<p class="hint" style="color:var(--red)">${esc(e.message)}</p>`; return; }
    let logs = (d.logs || []).filter((l) => jpIsJapanName(l.race || l.raceKey || ''));
    const allDates = document.querySelector('#jpReviewAllDates');
    if (!(allDates && allDates.checked)) {
      const todays = logs.filter((l) => (l.date || '') === jpTodayStr());
      if (todays.length) logs = todays;   // 오늘 경주가 있으면 오늘만, 없으면 전체(최근순)
    }
    if (!logs.length) { el.innerHTML = missBanner + '<p class="hint">분석된 일본경마 경주가 없습니다. 일본경마 배당을 수집·분석하면 자동으로 목록에 쌓입니다.</p>'; return; }
    const byDate = {};
    logs.forEach((l) => { (byDate[l.date || '?'] = byDate[l.date || '?'] || []).push(l); });
    el.innerHTML = missBanner + Object.keys(byDate).sort().reverse().map((date) =>
      `<div style="margin-bottom:8px"><div class="hint" style="font-weight:700;margin:4px 0">${esc(date)}</div>`
      + byDate[date].map((l) => `<div class="race-chip ${l.hasResult ? 'chip-done' : 'chip-todo'}" data-file="${esc(l.file)}" data-rk="${esc(l.raceKey || l.race || '')}" style="cursor:pointer;margin:3px 0;display:block">
        <b>${esc(l.race || l.race_id || '')}</b> <span class="chip-page">${esc(l.analyzed_at || '')} · 신호${l.signals || 0}${l.hasResult ? ' · ✅결과입력됨' : ' · ⬜결과대기'}</span></div>`).join('')
      + '</div>').join('');
    el.querySelectorAll('.race-chip').forEach((c) => c.addEventListener('click', () => openJapanReview(c.dataset.file, c.dataset.rk)));
  }

  async function openJapanReview(file, raceKey) {
    const el = document.querySelector('#jpReviewDetail'); if (!el) return;
    el.innerHTML = '<p class="hint">불러오는 중…</p>';
    let d; try { d = await (await fetch('/api/analysis-log/get', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file }) })).json(); }
    catch (e) { el.innerHTML = `<p class="hint" style="color:var(--red)">${esc(e.message)}</p>`; return; }
    if (d.error) { el.innerHTML = `<p class="hint">${esc(d.error)}</p>`; return; }
    const rk = raceKey || d.raceKey || d.race || '';
    el.innerHTML = renderJapanReview(d, rk, file);
    const btn = document.querySelector('#jpResSave');
    if (btn) btn.addEventListener('click', () => saveJapanResult(rk, file, jpRecSummary(d.final_recommendation)));
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function renderJapanReview(d, rk, file) {
    const sig = d.signals_detected || [], fr = d.final_recommendation || {}, elim = d.elimination || {};
    const horses = d.horses || [];
    const sev = (s) => /🔴|🚨|🌊/.test(s || '') ? 'chip-red' : '';
    const gradeOf = {}; horses.forEach((h) => { gradeOf[h.no] = h.grade; });
    const cand = elim.candidates || [], elimNo = elim.eliminated || [];
    const candHtml = cand.length ? cand.map((n) => `<span class="chip">${n}번${gradeOf[n] ? '(' + esc(String(gradeOf[n])) + ')' : ''}</span>`).join(' ') : '<span class="hint">없음</span>';
    const elimHtml = elimNo.length ? elimNo.map((n) => `<span class="chip chip-red">${n}번</span>`).join(' ') : '<span class="hint">없음</span>';
    const keyBlock = `<div class="matrix-title" style="font-size:13px">🏇 유력마 / 제거마</div>
      <div style="margin:2px 0"><b>유력마:</b> ${candHtml}</div>
      <div style="margin:2px 0"><b>제거마:</b> ${elimHtml}</div>`;
    const sigHtml = `<div class="matrix-title" style="font-size:13px;margin-top:8px">🚨 이상감지 내역</div>`
      + (sig.length ? sig.slice(0, 12).map((s) => `<div style="margin:2px 0"><span class="chip ${sev(s.severity)}">${esc(s.severity || '')} ${esc(s.type || '')}</span> ${esc(s.detail || '')}${s.reason ? ` <span class="hint">— ${esc(s.reason)}</span>` : ''}</div>`).join('') : '<div class="hint">감지된 이상신호 없음</div>');
    const recRow = (k, label) => { const r = fr[k]; return r ? `<tr><td>${label}</td><td style="font-weight:700">${esc(r.combo)}</td><td>${r.odds != null ? r.odds + '배' : '-'}</td></tr>` : ''; };
    const frHtml = `<div class="matrix-title" style="font-size:13px;margin-top:8px">🎯 추천 조합</div>
      <table class="data-table"><thead><tr><th>종류</th><th>조합</th><th>배당</th></tr></thead><tbody>${recRow('quinella_main', '복승 메인')}${recRow('quinella_sub', '복승 보조')}${recRow('trifecta_main', '삼복승 메인')}${recRow('trifecta_insurance1', '삼복승 보험1')}${recRow('trifecta_insurance2', '삼복승 보험2')}</tbody></table>`;
    const res = d.result || {}, hit = d.hit || {};
    const vv = (x) => (x != null && x !== '') ? x : '';
    const formHtml = `<div class="matrix-title" style="font-size:13px;margin-top:10px">✍️ 실제 결과 입력</div>
      <div class="cfg-row" style="gap:6px;align-items:center;flex-wrap:wrap">
        <label class="hint">1착 <input id="jpRes1" class="cfg-input" type="number" min="1" style="width:60px" value="${vv(res['1st'])}"></label>
        <label class="hint">2착 <input id="jpRes2" class="cfg-input" type="number" min="1" style="width:60px" value="${vv(res['2nd'])}"></label>
        <label class="hint">3착 <input id="jpRes3" class="cfg-input" type="number" min="1" style="width:60px" value="${vv(res['3rd'])}"></label>
        <label class="hint">4착 <input id="jpRes4" class="cfg-input" type="number" min="1" style="width:60px" value="${vv(res['4th'])}" title="추천 말이 4착이면 '아깝게 미적중' 학습"></label>
      </div>
      <div class="cfg-row" style="gap:6px;align-items:center;flex-wrap:wrap;margin-top:4px">
        <label class="hint">투자금액 <input id="jpStake" class="cfg-input" type="number" min="0" step="1000" style="width:100px" value="${vv(hit.stake || 1000)}">원</label>
        <label class="hint">실수령 배당금(선택) <input id="jpPayout" class="cfg-input" type="number" min="0" style="width:120px" placeholder="적중 시 총 수령액">원</label>
      </div>
      <div class="cfg-row" style="gap:6px;align-items:center;flex-wrap:wrap;margin-top:4px">
        <label class="hint">확정 복승배당 <input id="jpQOdds" class="cfg-input" type="number" min="1" step="0.1" style="width:80px" placeholder="배">배</label>
        <label class="hint">확정 삼복승배당 <input id="jpTOdds" class="cfg-input" type="number" min="1" step="0.1" style="width:80px" placeholder="배">배</label>
      </div>
      <div class="cfg-row" style="margin-top:4px">
        <label class="hint" style="flex:1">메모(특이사항) <input id="jpMemo" class="cfg-input" type="text" style="width:100%;max-width:340px" placeholder="예: 선행마 도주 성공 / 인기마 출발 지연"></label>
      </div>
      <div class="cfg-row" style="margin-top:6px">
        <button id="jpResSave" class="btn btn-primary">💾 결과 저장 · 자동 복기</button>
        <span id="jpResMsg" class="hint" style="margin-left:6px"></span>
      </div>
      <p class="hint" style="margin:4px 0 0">실수령 배당금을 입력하면 정확한 손익으로 계산됩니다. 확정배당·메모는 완전 저장(AI 학습용)에 함께 기록됩니다.</p>`;
    let reportHtml = '';
    if (d.result && d.hit) reportHtml = renderJapanReviewReport({
      recommend: jpRecSummary(fr), result: d.result,
      quinella_hit: hit.quinella_hit, trifecta_hit: hit.trifecta_hit,
      signal_correct: hit.signal_correct || [], anomaly_was_correct: hit.anomaly_was_correct,
      form_pick: hit.form_pick, form_pick_hit: hit.form_pick_hit,
      elimination_correct: hit.elimination_correct, pnl: hit.pnl, stake: hit.stake,
      near_miss: hit.near_miss, near_miss_horse: hit.near_miss_horse, result4: (d.result || {})['4th'],
      hit_basis: hit.hit_basis,   // [1번] 적중 근거 요약(재조회)
    }, rk);
    return `<div style="border:1px solid var(--border);border-radius:8px;padding:12px">
      <div class="matrix-title">${esc(d.race || rk)} <span class="hint" style="font-weight:400">${esc(d.date || '')} · 분석 ${esc(d.analyzed_at || '')}</span></div>
      ${keyBlock}${sigHtml}${frHtml}${formHtml}<div id="jpReport">${reportHtml}</div></div>`;
  }

  function renderJapanReviewReport(rep, rk) {
    const yn = (b) => b ? '<span style="color:#38d39f;font-weight:700">✅ 적중</span>' : '<span style="color:#f87171;font-weight:700">❌ 미적중</span>';
    const r = rep.result || {};
    const anomalyLines = (rep.signal_correct && rep.signal_correct.length)
      ? rep.signal_correct.map((s) => `<div style="margin:1px 0">🔴 ${esc(s)}</div>`).join('')
      : `<div class="hint">이상감지 적중 없음${rep.anomaly_was_correct ? '' : ' (급락말이 입상권에 없었음)'}</div>`;
    const pnl = rep.pnl;
    const pnlHtml = (pnl != null && pnl !== '')
      ? `<div style="font-weight:800;font-size:15px;margin-top:6px">수익: <span style="color:${pnl >= 0 ? '#38d39f' : '#f87171'}">${pnl >= 0 ? '+' : ''}${Number(pnl).toLocaleString()}원</span>${rep.stake ? ` <span class="hint" style="font-weight:400">(투자 ${Number(rep.stake).toLocaleString()}원)</span>` : ''}</div>`
      : '';
    const formLine = (rep.form_pick != null)
      ? `<div style="margin-top:4px">전적 유력마 ${rep.form_pick}번 → ${yn(rep.form_pick_hit)}${rep.elimination_correct != null ? ' · 제거 판정 ' + yn(rep.elimination_correct) : ''}</div>`
      : (rep.elimination_correct != null ? `<div style="margin-top:4px">제거 판정 ${yn(rep.elimination_correct)}</div>` : '');
    return `<div style="border:1px solid var(--border);border-radius:8px;padding:10px;margin-top:10px;background:rgba(56,189,248,.06)">
      <div class="matrix-title">🧾 ${esc(rk)} 복기</div>
      <div>추천: <b>${esc(rep.recommend || '-')}</b></div>
      <div>결과: <b>1착 ${r['1st'] != null ? r['1st'] : '?'}번 / 2착 ${r['2nd'] != null ? r['2nd'] : '?'}번 / 3착 ${r['3rd'] != null ? r['3rd'] : '?'}번${(r['4th'] != null || rep.result4 != null) ? ` / 4착 ${r['4th'] != null ? r['4th'] : rep.result4}번` : ''}</b></div>
      <div style="margin-top:4px">판정: 복승 ${yn(rep.quinella_hit)} · 삼복승 ${yn(rep.trifecta_hit)}</div>
      ${rep.near_miss ? `<div style="margin-top:4px;color:#ffd24f">🟡 <b>아깝게 4착 - 거의 적중</b>${rep.near_miss_horse != null ? ` (추천 ${rep.near_miss_horse}번이 4착)` : ''} → 삼복승 보험픽 학습 반영</div>` : ''}
      ${renderHitBasis(rep.hit_basis)}
      <div class="matrix-title" style="font-size:12px;margin-top:8px">이상감지 분석</div>${anomalyLines}
      ${formLine}${pnlHtml}</div>`;
  }

  // [1번] 적중 근거 한눈 요약(전적점수·급락점수+폭·역배열·최종신뢰도·한줄근거)
  function renderHitBasis(hb) {
    if (!hb) return '';
    const chip = (label, val, color) => val == null || val === '' ? '' :
      `<span class="chip" style="border-color:${color};color:${color}">${label} ${val}</span>`;
    const chips = [
      chip('전적점수', hb.formScore != null ? Math.round(hb.formScore) : null, '#4ea1ff'),
      chip('급락점수', hb.dropScore ? Math.round(hb.dropScore) : null, '#ff9f43'),
      chip('급락폭', hb.dropAmt != null ? hb.dropAmt + '%' : null, '#ff5c5c'),
      chip('역배열', hb.inverse ? (hb.inverseHit && hb.inverseHit.length ? '감지·입상 ' + hb.inverseHit.join('·') + '번' : '감지') : null, '#ff8a8a'),
      chip('최종신뢰도', hb.confidence ? Math.round(hb.confidence) : null, hb.confidence >= 70 ? '#ef4444' : '#ffd24f'),
    ].filter(Boolean).join(' ');
    return `<div style="margin-top:6px;padding:6px 8px;background:rgba(255,159,67,.08);border-radius:6px">
      <div style="font-size:12px;font-weight:700;color:#ffb26b">🎯 적중 근거</div>
      <div style="margin:3px 0">${chips || '<span class="hint">근거 데이터 없음</span>'}</div>
      <div class="hint" style="font-size:12px">📌 ${esc(hb.reason || '')}</div></div>`;
  }

  async function saveJapanResult(rk, file, recSummary) {
    const msg = document.querySelector('#jpResMsg');
    const g = (id) => { const e = document.querySelector(id); return e ? e.value.trim() : ''; };
    const n1 = g('#jpRes1'), n2 = g('#jpRes2'), n3 = g('#jpRes3');
    if (!n1) { if (msg) { msg.style.color = 'var(--red)'; msg.textContent = '최소 1착은 입력하세요'; } return; }
    const n4 = g('#jpRes4');
    const result = {}; result['1st'] = parseInt(n1, 10);
    if (n2) result['2nd'] = parseInt(n2, 10);
    if (n3) result['3rd'] = parseInt(n3, 10);
    if (n4) result['4th'] = parseInt(n4, 10);   // [4착] 아깝게 미적중 학습
    const stake = g('#jpStake'), payout = g('#jpPayout');
    const payload = { raceKey: rk, result };
    if (stake) payload.stake = parseInt(stake, 10);
    if (payout) payload.payout = parseInt(payout, 10);
    // [2번] 확정배당·메모·예산 → 완전 저장(AI 학습용)
    const qo = g('#jpQOdds'), to = g('#jpTOdds'), memo = g('#jpMemo');
    if (qo) payload.quinellaOdds = parseFloat(qo);
    if (to) payload.trifectaOdds = parseFloat(to);
    if (memo) payload.memo = memo;
    if (stake) payload.budget = parseInt(stake, 10);
    if (msg) { msg.style.color = ''; msg.textContent = '저장·판정 중…'; }
    let d; try { d = await (await fetch('/api/history/record-result', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })).json(); }
    catch (e) { if (msg) { msg.style.color = 'var(--red)'; msg.textContent = '실패: ' + e.message; } return; }
    if (d.error) { if (msg) { msg.style.color = 'var(--red)'; msg.textContent = d.error; } return; }
    // [4번] 데이터 품질 경고
    const derr = d.dataErrors || [];
    if (msg) { msg.style.color = derr.length ? '#ffb020' : '#38d39f'; msg.textContent = derr.length ? '✅ 저장(⚠️ ' + derr.join(', ') + ')' : '✅ 저장·학습·완전저장 완료'; }
    const rec = d.record || {};
    const rep = {
      recommend: recSummary, result: rec.result || result,
      quinella_hit: rec.quinella_hit, trifecta_hit: rec.trifecta_hit,
      signal_correct: rec.signal_correct || [], anomaly_was_correct: rec.anomaly_was_correct,
      form_pick: rec.form_pick, form_pick_hit: rec.form_pick_hit,
      elimination_correct: rec.elimination_correct, pnl: rec.pnl, stake: rec.stake,
      near_miss: rec.near_miss, near_miss_horse: rec.near_miss_horse, result4: (rec.result || result)['4th'],
      hit_basis: rec.hit_basis,   // [1번] 적중 근거 요약
    };
    const rc = document.querySelector('#jpReport'); if (rc) rc.innerHTML = renderJapanReviewReport(rep, rk);
    // [4번] 통계 자동 업데이트(적중률·이상감지 패턴·손익)
    try { loadLearningStats(); } catch (_) { /* */ }
    try { if (typeof loadHistoryList === 'function') loadHistoryList(); } catch (_) { /* */ }
    try { renderStats(); } catch (_) { /* */ }
    try { loadJapanReviewList(); } catch (_) { /* */ }
  }

  function initJapanReview() {
    const rb = document.querySelector('#jpReviewRefresh'); if (rb) rb.addEventListener('click', loadJapanReviewList);
    const ad = document.querySelector('#jpReviewAllDates'); if (ad) ad.addEventListener('change', loadJapanReviewList);
  }

  // ---------- 부트 ----------
  async function boot() {
    initTabs(); initCondBar(); initKorea(); initJapanRace(); initOdds(); initKoreaHistory();
    initAutoStatusBar();   // [v2.0.0] 자동수집 상태바
    initClosingWatch();    // [보완] 이상감지 누적 피드 + 마감 전 단계 알림
    initRaceRefresh();     // [경주 자동 업데이트] 상단 새로고침 바 + 30초 자동 감지
    initPopout();          // [별도 창] 분석기 팝업 창 열기 + 위치 기억
    initAnalysisLog();     // [분석 로그] 완전 기록 섹션
    initJapanReview();     // [일본경마 복기] 분석 내역 목록 + 결과 입력 + 자동 판정 리포트
    // [신규] 결과 입력 대기 목록 초기 로드 + 60초 폴링(탭 무관 알림 위해 전역)
    try { loadPendingResults(); if (_pendingTimer) clearInterval(_pendingTimer); _pendingTimer = setInterval(loadPendingResults, 60000); } catch (_) { /* */ }
    // [개편] initCombined() 제거 — 통합분석 탭 폐지(한국/일본 탭에 자동 표시).
    checkServerHealth();
    try { await JockeyDB.load(); rebuildJockeyStats(); } catch (e) { console.warn('기수 DB 로드 실패:', e); }
    // [2번·3번] 저장된 한국경마 분석 결과 자동 복원(JockeyDB 로드 후 → 세션 기수통계로 덮어씀)
    try { await restoreKoreaSession(); } catch (e) { console.warn('한국 세션 복원 실패:', e); }
  }
  document.addEventListener('DOMContentLoaded', boot);
})();
