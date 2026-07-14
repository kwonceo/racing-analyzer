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
        if (btn.dataset.tab === 'result') { renderRecentResults(); renderResultForm(); loadHighlights(); loadReportList(); loadPendingResults(); }
        if (btn.dataset.tab === 'jockeydb') renderJockeyDb();
        if (btn.dataset.tab === 'multi') startMultiRaceWatch();   // [다중 경주] 전체 경주 대시보드 시작
        else stopMultiRaceWatch();                                // 다른 탭 이동 시 폴링 중단(자원 절약)
        if (btn.dataset.tab === 'jp') { startJapanOddsWatch(); loadJapanReviewList(); }   // [5번] 일본경마: 실시간 배당 연동 + 분석 내역 복기 목록
        // [탭분리] 경정/경륜/바이크/중앙경마 탭 → 라이브 배당 폴링 시작 + 마지막 분석 즉시 반영
        if (['boat', 'cycle', 'bike', 'central'].includes(btn.dataset.tab)) {
          startJapanOddsWatch();
          startSportOddsWatch();   // [화면 복구] 각 종목 배당 독립 폴링 → sportReport-* 실시간 렌더(경마 경주 없어도 표시)
          if (_lastSportAnalyze) mirrorSportAnalysis(_lastSportAnalyze);
          loadSportRecords(btn.dataset.tab);   // [분석기록] 이 종목 과거 분석 기록
        } else {
          stopSportOddsWatch();   // 종목 탭 이탈 시 폴링 중단(자원 절약)
        }
      });
    });
    // [탭분리] 스포츠 탭 예산 입력 → 해당 탭 분석 즉시 재계산(마지막 분석 기준)
    ['boat', 'cycle', 'bike', 'central'].forEach((tab) => {
      const b = document.getElementById('sportBudget-' + tab);
      if (b) b.addEventListener('input', () => { if (_lastSportAnalyze) mirrorSportAnalysis(_lastSportAnalyze); });
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
    // [보완] PDF에서 추출된 발주시각을 수동입력 없이 자동 채움 → 마감 전 3단계 알림 자동 활성
    //        (배당판 없이 PDF 사전분석만 볼 때. 이미 지난 경주면 값만 채우고 알림은 생략)
    if (d.postTime) {
      const inp = document.getElementById('koreaDeadline');
      if (inp) inp.value = d.postTime;
      const [ph, pm] = String(d.postTime).split(':').map(Number);
      const td = new Date(); td.setHours(ph, pm || 0, 0, 0);
      if (!isNaN(ph) && td.getTime() > Date.now() + 30000) {
        _closing.panelRk = title;        // 알림을 이 경주로 귀속
        setKoreaManualDeadline();        // 기존 검증 흐름 재사용(입력값 + panelRk)
        toast(`⏰ 발주 ${d.postTime} 자동 설정 · 마감 전 알림 활성`);
      } else if (!isNaN(ph)) {
        toast(`⏰ 발주시각 ${d.postTime} (이미 지남 · 알림 미설정)`);
      }
    }
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
    initResultPhoto();     // [사진 첨부] 결과 사진 → OCR → 자동 반영
    initQuickEntry();      // [2번-방법3] 순서대로 빠른 입력
    initFailureReview();   // [복기 학습] 실패 대시보드 + 명예의 전당
    // [결과기록 UI 개선] 최근 결과·복기 뷰 — 새로고침 버튼 + 초기 렌더
    { const b = document.getElementById('recentResultsRefresh'); if (b) b.addEventListener('click', renderRecentResults); }
    try { renderRecentResults(); } catch (_) { /* */ }
    initResultSubtabs();   // [결과기록 전면정리] 서브탭 전환 + 직접입력 접기
  }

  // [결과기록 전면정리] 결과기록 탭 서브탭 전환 + 경주별 직접입력 접기/펼치기
  function initResultSubtabs() {
    const bar = document.getElementById('resultSubtabs');
    if (bar && !bar._wired) {
      bar._wired = true;
      bar.querySelectorAll('.subtab-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
          const sub = btn.dataset.sub;
          bar.querySelectorAll('.subtab-btn').forEach((b) => b.classList.toggle('active', b === btn));
          document.querySelectorAll('#tab-result .subtab-panel').forEach((p) => {
            p.style.display = (p.dataset.sub === sub) ? '' : 'none';
          });
          if (sub === 'hall') { try { loadHighlights(); } catch (_) { /* */ } }
          if (sub === 'report') { try { loadReportList(); } catch (_) { /* */ } }
          if (sub === 'records') { try { loadAnalysisRecordsAll(); } catch (_) { /* */ } }   // [분석기록] 검색 기록
        });
      });
    }
    const tf = document.getElementById('toggleResultForm');
    if (tf && !tf._wired) {
      tf._wired = true;
      tf.addEventListener('click', () => {
        const wrap = document.getElementById('resultFormWrap');
        if (!wrap) return;
        const open = (wrap.style.display === 'none' || !wrap.style.display);
        wrap.style.display = open ? 'block' : 'none';
        tf.textContent = open ? '✏️ 경주별 직접 입력 접기' : '✏️ 경주별 직접 입력 펼치기';
        if (open) { try { renderResultForm(); } catch (_) { /* */ } }   // 펼칠 때 최신 렌더
      });
    }
    // [수동 추천 저장] 라이브 배당 미수집 경주에도 전문가 추천 저장 → 결과 입력 시 자동 판정(_wired 가드로 멱등)
    const mr = document.getElementById('mrSave');
    if (mr && !mr._wired) {
      mr._wired = true;
      mr.addEventListener('click', async () => {
        const msg = document.getElementById('mrMsg');
        const rk = (document.getElementById('mrRaceKey').value || '').trim();
        if (!rk) { if (msg) { msg.style.color = '#f87171'; msg.textContent = '경주(raceKey)를 입력하세요.'; } return; }
        // "3+4, 3+7\n4+7" → ['3+4','3+7','4+7']
        const parse = (sel) => (document.getElementById(sel).value || '').split(/[,\n]+/).map((s) => s.trim()).filter(Boolean);
        const quinella = parse('mrQuinella');
        const trifecta = parse('mrTrifecta');
        if (!quinella.length && !trifecta.length) { if (msg) { msg.style.color = '#f87171'; msg.textContent = '복승 또는 삼복승 조합을 최소 1개 입력하세요.'; } return; }
        if (msg) { msg.style.color = ''; msg.textContent = '저장 중…'; }
        try {
          const d = await (await fetch('/api/recommend/manual', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ raceKey: rk, quinella, trifecta, note: document.getElementById('mrNote').value || '' }),
          })).json();
          if (d.error) { if (msg) { msg.style.color = '#f87171'; msg.textContent = d.error; } return; }
          const sv = d.saved || {};
          if (msg) {
            msg.style.color = '#38d39f';
            msg.textContent = `✅ 저장 완료 — 복승 ${(sv.quinella || []).length}개 · 삼복승 ${(sv.trifecta || []).length}개. 결과 입력 시 자동 판정됩니다.`;
          }
        } catch (e) { if (msg) { msg.style.color = '#f87171'; msg.textContent = String(e.message || e); } }
      });
    }
  }

  // ══════════ [2번-방법3] 순서대로 빠른 입력 (경주 시간순 나열 → 1~3착만 입력) ══════════
  function _today() { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; }

  function initQuickEntry() {
    const load = $('#quickLoad'); if (!load) return;
    { const dt = $('#quickDate'); if (dt && !dt.value) dt.value = _today(); }
    load.addEventListener('click', loadQuickEntry);
    { const b = $('#quickSaveAll'); if (b) b.addEventListener('click', () => saveQuickAll()); }
  }

  async function loadQuickEntry() {
    const el = $('#quickEntryList'); if (!el) return;
    const date = ($('#quickDate') && $('#quickDate').value) || _today();
    const pending = $('#quickPendingOnly') && $('#quickPendingOnly').checked ? '&pending=1' : '';
    el.innerHTML = '<p class="hint">⏳ 경주 불러오는 중…</p>';
    let d; try { d = await (await fetch(`/api/races/list?date=${encodeURIComponent(date)}${pending}`)).json(); }
    catch (e) { el.innerHTML = `<p class="err">${esc(e.message)}</p>`; return; }
    const races = d.races || [];
    if (!races.length) { el.innerHTML = `<p class="hint">${esc(date)} 분석 경주가 없습니다. ${pending ? '(미입력만 표시 중 — 체크 해제 시 전체)' : ''}</p>`; return; }
    const dl = races.map((r) => `<option value="${esc(r.raceKey)}">`).join('');
    const rows = races.map((r, i) => {
      const t = r.top3 || [];
      const done = r.hasResult ? '<span style="color:#38d39f">✅ 입력됨</span>' : '<span class="hint">미입력</span>';
      return `<tr data-rk="${esc(r.raceKey)}">
        <td class="hint" style="white-space:nowrap">${i + 1}. ${esc(r.raceKey)}</td>
        <td><input class="cfg-input q-1" type="number" min="1" style="width:52px" value="${t[0] != null ? t[0] : ''}"></td>
        <td><input class="cfg-input q-2" type="number" min="1" style="width:52px" value="${t[1] != null ? t[1] : ''}"></td>
        <td><input class="cfg-input q-3" type="number" min="1" style="width:52px" value="${t[2] != null ? t[2] : ''}"></td>
        <td><input class="cfg-input q-4" type="number" min="1" style="width:52px" placeholder="4착"></td>
        <td><input class="cfg-input q-q" type="number" min="1" step="0.1" style="width:64px" placeholder="복승배"></td>
        <td><input class="cfg-input q-t" type="number" min="1" step="0.1" style="width:64px" placeholder="삼복승배"></td>
        <td><button class="btn q-save" style="padding:2px 8px">저장</button> <span class="q-stat">${done}</span></td>
      </tr>`;
    }).join('');
    el.innerHTML = `<datalist id="raceNameList">${dl}</datalist>
      <table class="data-table"><thead><tr><th>경주(시간순)</th><th>1착</th><th>2착</th><th>3착</th><th>4착</th><th>복승</th><th>삼복승</th><th>저장</th></tr></thead>
      <tbody>${rows}</tbody></table>
      <p class="hint" style="margin-top:4px">저장 시 즉시 적중판정·복기가 실행됩니다. 미적중이면 아래에 복기 리포트가 표시됩니다.</p>
      <div id="quickReviewOut" style="margin-top:8px"></div>`;
    el.querySelectorAll('.q-save').forEach((btn) => btn.addEventListener('click', (e) => {
      const tr = e.target.closest('tr'); if (tr) saveQuickRow(tr);
    }));
  }

  async function saveQuickRow(tr) {
    const rk = tr.getAttribute('data-rk');
    const g = (cls) => { const e = tr.querySelector(cls); return e ? e.value.trim() : ''; };
    const n1 = g('.q-1'), n2 = g('.q-2'), n3 = g('.q-3'), n4 = g('.q-4');
    const stat = tr.querySelector('.q-stat');
    if (!n1) { if (stat) stat.innerHTML = '<span class="err">1착 필수</span>'; return; }
    const result = { '1st': parseInt(n1, 10) };
    if (n2) result['2nd'] = parseInt(n2, 10);
    if (n3) result['3rd'] = parseInt(n3, 10);
    if (n4) result['4th'] = parseInt(n4, 10);
    const body = { raceKey: rk, result, stake: _defaultStake() };
    const q = g('.q-q'), t = g('.q-t');
    if (q) body.quinellaOdds = parseFloat(q);
    if (t) body.trifectaOdds = parseFloat(t);
    if (stat) stat.innerHTML = '⏳';
    let d; try {
      d = await (await fetch('/api/history/record-result', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      })).json();
    } catch (e) { if (stat) stat.innerHTML = `<span class="err">${esc(e.message)}</span>`; return; }
    if (d.error) { if (stat) stat.innerHTML = `<span class="err">${esc(d.error)}</span>`; return; }
    const rec = d.record || {};
    const hit = rec.was_hit;
    // [유력마 적중 병행 노출] 정확 복승/삼복승(quinella_hit)과 별개로, 유력마 2/3 입상 시 참고 배지 병행 표시(손익·기존통계 불변).
    const khHit = rec.keyhorse_quinella_hit || rec.keyhorse_trifecta_hit;
    const khTxt = khHit ? ` <span style="color:#fbbf24;font-weight:600" title="유력마 상위3 중 2마리+ 입상 — 참고 지표(정확 복승/삼복승 판정·손익과 별개)">🔶 유력마 적중(참고)</span>` : '';
    if (stat) stat.innerHTML = (hit ? '<span style="color:#38d39f;font-weight:700">✅ 적중</span>' : '<span style="color:#f87171;font-weight:700">❌ 미적중</span>') + khTxt;
    // [복기 통합] 적중/미적중 모두 복기 리포트 자동 표시(적중=왜 맞았는지 / 미적중=왜 놓쳤는지)
    showFailureReport(rk);
    try { loadLearningStats(); } catch (_) { /* */ }
  }

  async function saveQuickAll() {
    const rows = document.querySelectorAll('#quickEntryList tr[data-rk]');
    const msg = $('#quickMsg');
    let n = 0;
    for (const tr of rows) {
      if (tr.querySelector('.q-1') && tr.querySelector('.q-1').value.trim()) { await saveQuickRow(tr); n++; }
    }
    if (msg) { msg.style.color = '#38d39f'; msg.textContent = `${n}개 경주 저장 완료`; }
  }

  /** [복기 UI 통합] 적중/미적중 경주 복기 리포트를 서버에서 받아 표시(순서대로 빠른입력 하단 공용).
   *   ✅ 적중 → "왜 맞았는지" 녹색 카드 · ❌ 미적중 → "왜 놓쳤는지" 빨강 카드 (한 곳에서 학습). */
  async function showFailureReport(rk, targetSel) {
    const out = $(targetSel || '#quickReviewOut'); if (!out) return;
    out.insertAdjacentHTML('afterbegin', `<div id="fr-loading" class="hint">⏳ ${esc(rk)} 복기 생성 중…</div>`);
    let d; try { d = await (await fetch(`/api/failure/report?raceKey=${encodeURIComponent(rk)}`)).json(); }
    catch (e) { const l = $('#fr-loading'); if (l) l.remove(); return; }
    const l = $('#fr-loading'); if (l) l.remove();
    if (!d.ok) {   // [복기 통합] 데이터 없으면 조용히 넘기지 않고 사유 안내
      out.insertAdjacentHTML('afterbegin', `<div class="hint" style="font-size:12px;padding:4px 0">복기 데이터 없음 — ${esc(d.error || '당시 배당 히스토리가 없는 경주')}</div>`);
      return;
    }
    out.insertAdjacentHTML('afterbegin', renderFailureReport(d));
  }

  /** [복기 UI 통합] 복기 리포트 카드 — 적중=왜 맞았는지(녹색) / 미적중=왜 놓쳤는지(빨강). */
  function renderFailureReport(d) {
    const f = d.failure || {};
    const tl = (d.timelines || {});
    const sigColor = (s) => /강한/.test(s) ? '#f87171' : (/약한/.test(s) ? '#ffd24f' : (/반등/.test(s) ? '#8a94a6' : '#8a94a6'));
    const tlRows = (no) => (tl[String(no)] || []).map((p) => {
      const mb = p.mb;
      const tstr = (mb == null) ? (p.src || '') : (mb >= 0 ? `T-${mb}분` : `마감후${Math.abs(mb)}분`);
      const pct = p.pct != null ? ` <b style="color:${p.pct <= -8 ? '#f87171' : (p.pct >= 8 ? '#8a94a6' : '#cdd6e3')}">${p.pct > 0 ? '+' : ''}${p.pct}%</b>` : '';
      return `<div style="margin:1px 0;font-size:12px">${tstr}: ${p.odds}배${pct} <span style="color:${sigColor(p.signal)}">${esc(p.signal || '')}</span></div>`;
    }).join('') || '<div class="hint" style="font-size:12px">타임라인 없음</div>';
    // [복기 상세 강화] 정답말 전적 점수 + 이상감지말(적중/미적중 공통 근거)
    const scores = d.scores || {};
    const scoreTag = (no) => (scores[String(no)] != null ? ` <span class="hint">전적 ${scores[String(no)]}점</span>` : '');
    const rankOf = (h) => (h === (d.top3 || [])[0] ? '1착' : h === (d.top3 || [])[1] ? '2착' : '3착');
    const anomalyLine = (d.anomaly_horse != null)
      ? `<div style="margin:3px 0;font-size:12px"><span class="hint">🚨 당시 이상감지말:</span> <b style="color:#ff5c5c">${d.anomaly_horse}번</b>${(d.key_horses || []).length ? ` · <span class="hint">유력마</span> <b style="color:#4ea1ff">${(d.key_horses || []).join('·')}</b>` : ''}</div>`
      : ((d.key_horses || []).length ? `<div style="margin:3px 0;font-size:12px"><span class="hint">⭐ 당시 유력마:</span> <b style="color:#4ea1ff">${(d.key_horses || []).join('·')}</b></div>` : '');
    // [복기 통합] 적중 경주 → "왜 맞았는지" 녹색 카드(정답말 신호 근거)
    if (d.was_hit) {
      const winners = (d.top3 || []).map((h, i) =>
        `<div style="margin-top:4px"><span class="chip" style="border-color:#38d39f;color:#38d39f">${h}번(${i === 0 ? '1착' : i === 1 ? '2착' : '3착'})</span>${scoreTag(h)}</div>${tlRows(h)}`).join('');
      return `<div style="border:1px solid #38d39f;border-radius:8px;padding:10px;margin-bottom:8px;background:rgba(56,211,159,.06)">
        <div class="matrix-title" style="color:#38d39f">✅ 적중 복기 — ${esc(d.raceKey)}</div>
        <div>실제 정답: <b>${(d.top3 || []).join('-')}</b> · 적중 추천: <b style="color:#38d39f">${(d.hit_combos || []).join(' / ') || '(추천 조합 적중)'}</b></div>
        ${anomalyLine}
        <div class="matrix-title" style="font-size:12px;margin-top:8px">💡 왜 맞았나 — 정답말 신호·전적·배당 타임라인(1·2·3착)</div>${winners}
        <div style="margin-top:8px;padding:6px 8px;background:rgba(56,211,159,.08);border-radius:6px">
          <b style="color:#38d39f">🔁 재현 포인트:</b> 이 신호 패턴을 다음 경주에서도 우선 반영</div>
      </div>`;
    }
    const focusBlock = (f.focus != null)
      ? `<div class="matrix-title" style="font-size:12px;margin-top:6px">❓ 왜 ${f.focus}번을 놓쳤나${scoreTag(f.focus)}</div>${tlRows(f.focus)}
         <div style="margin-top:3px;font-size:12px">→ ${esc(f.reason || '')}</div>` : '';
    const others = (d.top3 || []).filter((h) => h !== f.focus).map((h) =>
      `<div style="margin-top:4px"><span class="chip">${h}번(${rankOf(h)})</span>${scoreTag(h)}</div>${tlRows(h)}`).join('');
    return `<div style="border:1px solid #f87171;border-radius:8px;padding:10px;margin-bottom:8px;background:rgba(248,113,113,.06)">
      <div class="matrix-title" style="color:#ff8a8a">❌ 복기 리포트 — ${esc(d.raceKey)}</div>
      ${f.label ? `<div style="margin:2px 0"><b>실패 유형:</b> <span style="color:#ffd24f">${esc(f.label)}</span></div>` : ''}
      <div>실제 정답: <b>${(d.top3 || []).join('-')}</b> · 우리 추천: <b>${(d.recommended || []).join(' / ') || '없음'}</b></div>
      ${anomalyLine}
      ${focusBlock}
      <div class="matrix-title" style="font-size:12px;margin-top:8px">📈 정답말 역추적 · 전적(1·2·3착)</div>${others}
      <div style="margin-top:8px;padding:6px 8px;background:rgba(56,211,159,.08);border-radius:6px">
        <b style="color:#38d39f">🔍 개선점 · 다음 대응:</b> ${esc(f.improvement || '상위 3신호 말 전부 추천 포함')}</div>
    </div>`;
  }

  // ══════════ [복기 학습] 실패 유형 대시보드 + 명예의 전당 ══════════
  function initFailureReview() {
    { const b = $('#failReviewRefresh'); if (b) b.addEventListener('click', loadFailureReview); }
    { const b = $('#hallRefresh'); if (b) b.addEventListener('click', loadHallOfFame); }
    { const b = $('#reviewStatsRefresh'); if (b) b.addEventListener('click', loadReviewStats); }   // [6번] 코멘트 모아보기
    try { loadReviewStats(); } catch (_) { /* */ }   // 통계 탭 진입 시 자동 로드(있으면)
    initDataProtect();   // [데이터 보호] 자동/수동 GitHub 백업
  }

  // ══════════ [데이터 보호] 학습 코퍼스 자동/수동 GitHub 백업 ══════════
  function initDataProtect() {
    { const b = $('#dataBackupBtn'); if (b) b.addEventListener('click', runDataBackup); }
    { const b = $('#dataStatusBtn'); if (b) b.addEventListener('click', loadDataStatus); }
  }

  async function runDataBackup() {
    const msg = $('#dataBackupMsg'); if (msg) { msg.style.color = ''; msg.textContent = '⏳ GitHub 백업 요청 중…'; }
    try {
      const d = await (await fetch('/api/data/backup', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label: '수동 백업(통계 탭)' }),
      })).json();
      if (msg) { msg.style.color = '#38d39f'; msg.textContent = `✅ 백업 예약됨(${(d.paths || []).length}개 경로) — 잠시 후 GitHub 반영`; }
    } catch (e) { if (msg) { msg.style.color = 'var(--red)'; msg.textContent = '실패: ' + esc(e.message); } }
    setTimeout(loadDataStatus, 1500);
  }

  async function loadDataStatus() {
    const el = $('#dataStatusView'); if (!el) return;
    el.innerHTML = '<p class="hint">⏳ 보호 현황 확인 중…</p>';
    let d; try { d = await (await fetch('/api/data/status')).json(); }
    catch (e) { el.innerHTML = `<p class="err">${esc(e.message)}</p>`; return; }
    const rows = (d.paths || []).map((p) =>
      `<tr><td>${esc(p.path)}</td><td style="text-align:right">${p.exists ? p.files + '개' : '<span class="hint">없음</span>'}</td>
        <td>${p.exists ? '<span style="color:#38d39f">✅ 보호</span>' : '<span class="hint">-</span>'}</td></tr>`).join('');
    el.innerHTML = `<div style="margin-bottom:4px">GitHub 추적 데이터 파일: <b>${d.trackedFiles != null ? d.trackedFiles : '?'}</b>개</div>
      <table class="data-table" style="max-width:520px"><thead><tr><th>코퍼스 경로</th><th>파일</th><th>백업</th></tr></thead><tbody>${rows}</tbody></table>
      <p class="hint" style="margin-top:4px">🛡️ 결과 입력마다 자동 백업 · 위험한 <code>git reset --hard</code>는 <code>scripts\\safe_reset.bat</code>로 실행하세요(자동 물리 백업).</p>`;
  }

  async function loadFailureReview() {
    const el = $('#failReviewDashboard'); if (!el) return;
    el.innerHTML = '<p class="hint">⏳ 복기 통계 불러오는 중…</p>';
    let d; try { d = await (await fetch('/api/failure/stats')).json(); }
    catch (e) { el.innerHTML = `<p class="err">${esc(e.message)}</p>`; return; }
    el.innerHTML = renderFailureReview(d);
  }

  function renderFailureReview(fs) {
    if (!fs || !fs.total) return '<p class="hint">아직 미적중 분석 데이터가 없습니다. 결과를 입력하면 실패 유형이 자동 분류됩니다.</p>';
    const bar = (pct, color) => `<span style="display:inline-block;height:8px;width:${Math.max(3, pct)}%;max-width:160px;background:${color};border-radius:4px;vertical-align:middle"></span>`;
    const tColor = { '신호미반영': '#ff9f43', '페이크베팅': '#a78bfa', '노이즈': '#4ea1ff', '전적오판': '#f87171', '타이밍': '#38bdf8' };
    const typeRows = (fs.types || []).map((t) =>
      `<tr><td>${esc(t.label)}</td><td style="text-align:right"><b>${t.count}</b>건</td><td style="text-align:right">${t.pct}%</td><td>${bar(t.pct, tColor[t.type] || '#8a94a6')}</td></tr>`).join('');
    const top = fs.top_type;
    const topTxt = top && top.count ? `<div style="margin:6px 0;padding:6px 8px;background:rgba(255,159,67,.1);border-radius:6px">가장 많은 실패 원인: <b style="color:#ffb26b">${esc(top.label)}</b> (${top.count}건, ${top.pct}%) → 개선 방향: ${esc((fs.types.find((x) => x.type === top.type) && FAIL_IMPROVE[top.type]) || '상위 3신호 전부 추천')}</div>` : '';
    // 놓친 신호 패턴 TOP
    const missed = (fs.missed_top || []).map((mp, i) => `<div style="margin:2px 0">${i + 1}. ${esc(mp.pattern)}: <b>${mp.count}회</b></div>`).join('') || '<div class="hint">없음</div>';
    // [4번] 실제 1착말 신호 보유율
    const ws = fs.winner_signal || {};
    const wsBlock = (ws.rate != null)
      ? `<div style="margin:8px 0;padding:8px;border:1px solid #ffd24f;border-radius:6px;background:rgba(255,210,79,.06)">
          <b>🎯 실제 1착 말이 신호 있었던 비율:</b> <span style="font-size:20px;color:#ffd24f;font-weight:800">${ws.rate}%</span> <span class="hint">(${ws.had}/${ws.total})</span>
          <div class="hint" style="margin-top:2px">→ 신호는 감지됐으나 추천에 미반영된 비율 · 높을수록 '추천 로직' 개선 여지</div></div>` : '';
    // [4번] 개선 전/후 적중률
    const imp = fs.improve;
    const impBlock = (imp && (imp.before != null || imp.after != null))
      ? `<div style="margin:8px 0;padding:8px;border:1px solid #38d39f;border-radius:6px;background:rgba(56,211,159,.06)">
          <b>📈 개선 후 적중률 변화</b> <span class="hint">(규칙: ${esc(imp.rule || '')})</span><br>
          수정 전 <b style="color:#8a94a6">${imp.before != null ? imp.before + '%' : '-'}</b> → 수정 후 <b style="color:#38d39f;font-size:18px">${imp.after != null ? imp.after + '%' : '집계중'}</b>
          <span class="hint">(${esc(imp.since || '')} 규칙 적용 이후)</span></div>` : '';
    // [7번] 자동 학습 규칙
    const rules = (fs.rules || []).length
      ? (fs.rules).map((r) => `<div style="margin:4px 0;padding:6px 8px;border-left:3px solid #38d39f;background:rgba(56,211,159,.06)">
          🔔 <b>새 규칙 학습:</b> ${esc(r.text)}<br><span class="hint">근거: ${esc(r.basis || '')} · ${esc(r.created || '')}${r.after_rate != null ? ` · 적용 후 적중률 ${r.after_rate}%` : ''}</span></div>`).join('')
      : '<div class="hint">아직 자동 생성된 규칙이 없습니다. 같은 패턴 3회+ 실패 시 규칙이 자동 추가됩니다.</div>';
    // 최근 실패 사례
    const recent = (fs.recent || []).slice(0, 6).map((c) =>
      `<div style="margin:2px 0;font-size:12px"><span class="chip">${esc(c.label || c.type || '')}</span> ${esc(c.race || '')} · 정답 ${(c.top3 || []).join('-')} <span class="hint">${esc(c.reason || '')}</span></div>`).join('');
    return `<div style="margin-bottom:6px">실패 분석 <b>${fs.total}</b>경주</div>
      <table class="data-table" style="max-width:520px"><thead><tr><th>실패 유형</th><th>건수</th><th>비율</th><th></th></tr></thead><tbody>${typeRows}</tbody></table>
      ${topTxt}${wsBlock}${impBlock}
      <div class="matrix-title" style="font-size:13px;margin-top:10px">🔍 놓친 신호 패턴 TOP</div>${missed}
      <div class="matrix-title" style="font-size:13px;margin-top:10px">🔔 실패에서 배운 규칙 (자동 생성)</div>${rules}
      <div class="matrix-title" style="font-size:13px;margin-top:10px">🗂️ 최근 실패 사례</div>${recent || '<div class="hint">없음</div>'}`;
  }

  const FAIL_IMPROVE = {
    '신호미반영': '상위 3개 신호 말 전부 추천 포함',
    '페이크베팅': '반등폭<급락폭이면 신호 유지',
    '노이즈': '대규모 급락 시 집중도 상위 말 우선',
    '전적오판': '이변 조건(컨디션·거리/기수) 학습 강화',
    '타이밍': 'T-3분/T-1분 수집 간격 단축',
  };

  async function loadHallOfFame() {
    const el = $('#hallOfFame'); if (!el) return;
    el.innerHTML = '<p class="hint">⏳ 불러오는 중…</p>';
    let d; try { d = await (await fetch('/api/hall-of-fame')).json(); }
    catch (e) { el.innerHTML = `<p class="err">${esc(e.message)}</p>`; return; }
    el.innerHTML = renderHallOfFame(d);
  }

  function renderHallOfFame(d) {
    const rawWins = (d && d.wins) || [];
    // [3번] 같은 경주 중복 제거 — 경주별 1개(가장 높은 배당) 유지
    const _seen = new Map();
    rawWins.forEach((w) => {
      const key = w.raceKey || w.race || JSON.stringify(w.top3 || []);
      const od = Math.max((w.trifecta_hit && w.trifecta_odds) || 0, (w.quinella_hit && w.quinella_odds) || 0);
      const prev = _seen.get(key);
      if (!prev || od > (prev._od || 0)) { w._od = od; _seen.set(key, w); }
    });
    const wins = [..._seen.values()];
    if (!wins.length) return '<p class="hint">아직 고배당 적중 기록이 없습니다. 복승 30배+ / 삼복승 100배+ 적중 시 자동 등록됩니다.</p>';
    return wins.slice(0, 30).map((w) => {
      const bigQ = w.quinella_hit && w.quinella_odds;
      const bigT = w.trifecta_hit && w.trifecta_odds;
      const odds = bigT ? `삼복승 <b style="color:#ffd24f">${w.trifecta_odds}배</b>` : (bigQ ? `복승 <b style="color:#ffd24f">${w.quinella_odds}배</b>` : '');
      const tlNo = (w.top3 || [])[0];
      const tl = (w.timelines || {})[String(tlNo)] || [];
      const tlTxt = tl.length ? tl.map((p) => {
        const mb = p.mb; const tstr = (mb == null) ? '' : (mb >= 0 ? `T-${mb}` : `마감후${Math.abs(mb)}`);
        return `${tstr}:${p.odds}`;
      }).join(' → ') : '';
      return `<div style="border:1px solid #ffd24f;border-radius:8px;padding:8px 10px;margin-bottom:6px;background:rgba(255,210,79,.06)">
        <div><b style="color:#ffb26b">🏆 ${esc(w.raceKey || '')}</b> <span class="hint">${esc(w.date || '')}</span></div>
        <div style="margin:2px 0">${odds} 적중 · 정답 <b>${(w.top3 || []).join('-')}</b></div>
        ${w.story ? `<div style="font-size:12px;margin:2px 0">📖 ${esc(w.story)}</div>` : ''}
        ${tlTxt ? `<div class="hint" style="font-size:11px">${esc(String(tlNo))}번 배당: ${esc(tlTxt)}</div>` : ''}
      </div>`;
    }).join('');
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
  // [사진 첨부] 파일 → dataURL
  function _fileToDataUrl(file) {
    return new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(r.result);
      r.onerror = () => reject(new Error('파일 읽기 오류'));
      r.readAsDataURL(file);
    });
  }

  // [사진 첨부] 결과 사진 → /api/result/ocr(Vision 판독) → 다중경주 자동 매칭·적중판정·학습.
  function initResultPhoto() {
    const btn = $('#resultPhotoSubmit'); if (!btn) return;
    const fi = $('#resultPhotoFile');
    { const st = $('#resultPhotoStake'); if (st) st.value = _defaultStake(); }
    // 파일 선택 시 미리보기
    if (fi) fi.addEventListener('change', async () => {
      const f = fi.files && fi.files[0]; const pv = $('#resultPhotoPreview');
      if (!f || !pv) return;
      try { pv.innerHTML = `<img src="${await _fileToDataUrl(f)}" style="max-width:100%;max-height:220px;border:1px solid var(--border);border-radius:6px" />`; } catch (_) { /* */ }
    });
    btn.addEventListener('click', async () => {
      const box = $('#resultPhotoSummary');
      const f = fi && fi.files && fi.files[0];
      if (!f) { box.innerHTML = '<p class="err">결과 사진 파일을 먼저 선택하세요.</p>'; return; }
      const stake = parseInt(($('#resultPhotoStake') && $('#resultPhotoStake').value) || '1000', 10) || 1000;
      if (stake > 0) localStorage.setItem('bmed_default_stake', String(stake));
      box.innerHTML = '<p class="hint">⏳ 사진 판독·매칭·학습 중… (Vision OCR — 표의 모든 경주 자동 인식)</p>';
      let dataUrl;
      try { dataUrl = await _fileToDataUrl(f); }
      catch (e) { box.innerHTML = `<p class="err">파일 읽기 실패: ${esc(e.message)}</p>`; return; }
      let d;
      try {
        d = await (await fetch('/api/result/ocr', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ image: dataUrl, stake }),
        })).json();
      } catch (e) { box.innerHTML = `<p class="err">요청 실패: ${esc(e.message)}</p>`; return; }
      if (d.error) { box.innerHTML = `<p class="err">${esc(d.error)}</p>`; return; }
      if (!d.registered) {
        box.innerHTML = `<p class="hint">⚠️ ${esc(d.note || '등록된 경주가 없습니다.')} ${d.parsed && d.parsed.length ? `(판독 ${d.parsed.length}행이나 분석한 경주와 매칭 실패 — 경주명/날짜 확인)` : '(결과표가 선명한지 확인)'}</p>`;
      } else {
        renderBulkSummary(d, '#resultPhotoSummary');
      }
      // 통계·히스토리·대기목록 자동 갱신
      try { loadLearningStats(); } catch (_) { /* */ }
      try { loadHistoryList(); } catch (_) { /* */ }
      try { if (typeof loadPendingResults === 'function') loadPendingResults(); } catch (_) { /* */ }
    });
  }

  function renderBulkSummary(d, boxSel) {
    const box = $(boxSel || '#bulkResultSummary'); if (!box) return;
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
  // [경주 고정] 자동 전환 중단 상태(사용자가 보고 있는 경주 유지). localStorage로 새로고침에도 유지.
  let _racePinned = false;
  try { _racePinned = (localStorage.getItem('racePinned') === '1'); } catch (_) { /* */ }

  /** [2번] raceKey에서 경마장명(경주번호 앞 토큰) 추출 — 자동 전환 시 같은 경마장인지 확인용. */
  function _raceVenue(rk) {
    if (!rk) return '';
    // "사가 5경주" / "오비히로 8R" / "帯広 3レース" → 앞 토큰(공백 전 또는 숫자 전)
    const m = String(rk).trim().match(/^([^\d]+?)\s*\d/);
    return (m ? m[1] : String(rk)).replace(/[\s·]+$/, '').trim();
  }

  /** [자동 전환 게이트] 최신 수집 경주(latestRk)를 보고, 지금 화면에 표시해야 할 경주를 결정.
   *  고정 중이거나 '다른 경마장'이면 현재 보고 있는 경주(_rrLastRk)를 유지 → 강제 전환 차단.
   *  refreshCurrentRace·pollJapanOdds 두 자동 경로가 이 규칙을 공유(한 곳만 고치면 딴 경로가 전환시키던 문제 해결). */
  function _targetRaceKey(latestRk) {
    if (!latestRk) return _rrLastRk || null;
    if (!_rrLastRk) return latestRk;                 // 최초 로드 = 최신 따름
    if (latestRk === _rrLastRk) return latestRk;
    if (_racePinned) return _rrLastRk;               // [2번] 고정 중 → 무조건 현재 경주 유지
    if (_raceVenue(latestRk) !== _raceVenue(_rrLastRk)) return _rrLastRk;  // [3번] 다른 경마장 → 전환 안 함
    return latestRk;                                 // 같은 경마장 다음 경주 → 전환 허용
  }

  /** [3번] 📌 현재 경주 고정 토글 — 자동 전환 on/off. 서버측 잠금(POST /api/race/pin)까지 반영. */
  async function toggleRacePin() {
    _racePinned = !_racePinned;
    try { localStorage.setItem('racePinned', _racePinned ? '1' : '0'); } catch (_) { /* */ }
    _updatePinButton();
    const status = $('#rrStatus');
    // [서버측 잠금] 프론트뿐 아니라 서버에도 고정 → oddspark 자동수집이 다른 경주를 최신 저장해도 current_race가 고정 경주 유지.
    try {
      if (_racePinned && _rrLastRk) {
        await fetch('/api/race/pin', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ raceKey: _rrLastRk }) });
      } else {
        await fetch('/api/race/pin', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ clear: true }) });
      }
    } catch (_) { /* */ }
    if (_racePinned) {
      if (status) status.textContent = `📌 고정됨(서버 잠금): ${_rrLastRk || '현재 경주'} — 자동 전환 중단`;
      notify(`📌 현재 경주 고정: ${_rrLastRk || ''} · 서버측 잠금(재시작에도 유지)`, true);
    } else {
      if (status) status.textContent = '📌 고정 해제 — 자동 전환 재개';
      notify('📌 고정 해제: 자동 전환 재개', true);
      refreshCurrentRace(false);   // 해제 즉시 최신 경주 반영
    }
  }
  function _updatePinButton() {
    const pb = $('#rrPinBtn');
    if (!pb) return;
    if (_racePinned) {
      pb.textContent = '📌 고정 해제';
      pb.style.background = '#f59e0b';
      pb.style.color = '#1a1a1a';
    } else {
      pb.textContent = '📌 현재 경주 고정';
      pb.style.background = '';
      pb.style.color = '';
    }
  }

  function initRaceRefresh() {
    const btn = $('#rrRefreshBtn');
    if (btn) btn.addEventListener('click', () => refreshCurrentRace(true));
    const pb = $('#rrPinBtn');               // [3번] 현재 경주 고정 버튼
    if (pb) pb.addEventListener('click', toggleRacePin);
    const nb = $('#rrNewRaceBtn');           // [3번] 강제 초기화 버튼
    if (nb) nb.addEventListener('click', newRaceStart);
    // [서버측 잠금 동기화] 서버에 고정된 경주가 있으면(재시작 후에도) 프론트 상태 복원 → 자동 전환 차단 유지.
    (async () => {
      try {
        const d = await (await fetch('/api/race/pin')).json();
        if (d && d.pinned) { _racePinned = true; _rrLastRk = d.pinned; try { localStorage.setItem('racePinned', '1'); } catch (_) { /* */ } }
      } catch (_) { /* */ }
      _updatePinButton();
      refreshCurrentRace(false);
    })();
    if (_rrTimer) clearInterval(_rrTimer);
    _rrTimer = setInterval(() => refreshCurrentRace(false), 12000);   // [4번] 12초마다 경주 변경 확인·자동 전환(늦게 넘어감 방지)
  }

  /** [1·3번] 활성 배당·이상감지·타임라인·경고 상태를 모두 초기화(새 경주 준비). */
  function hardResetRaceState() {
    resetOddsTimeline();                     // 배당 타임라인(일본·한국) 초기화
    state.jpOddsPrev = new Set();             // 이상감지 경고 중복추적 초기화
    state.jpTimeline = [];
    state.jpCurrentRk = null;                 // 현재 경주 기준 초기화
    _rrLastRk = null;
    try { resetAnomalyPanel(); } catch (_) { /* */ }   // [1번] 이상감지 누적 패널 완전 초기화(경주 전환)
    try { setJpOddsStatus('waiting'); } catch (_) { /* */ }
  }

  /** [3번] 🆕 새 경주 시작: 서버 활성 3종 배당 초기화 + 프론트 상태 초기화 + 새 수집 요청. */
  async function newRaceStart() {
    if (!confirm('현재 활성 배당 데이터를 모두 초기화하고 새 경주를 시작할까요?\n(경주별 히스토리·학습 기록은 그대로 보존됩니다)')) return;
    const status = $('#rrStatus');
    if (status) status.textContent = '초기화 중…';
    try {
      await fetch('/api/odds/triple/reset', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}),
      });
      // [서버측 잠금] 새 경주 시작 = 고정 해제(이전 고정 경주에 묶이지 않게)
      await fetch('/api/race/pin', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ clear: true }) });
      _racePinned = false; try { localStorage.setItem('racePinned', '0'); } catch (_) { /* */ }
      _updatePinButton();
    } catch (_) { /* */ }
    hardResetRaceState();
    { const el = $('#rrCurrentRace'); if (el) el.textContent = '— (새 경주 수집 대기)'; }
    if (status) status.textContent = '✅ 초기화 완료 — 확장에서 새 경주 [전체 자동 수집]을 실행하세요';
    notify('🆕 새 경주 시작: 활성 배당 초기화 완료. 확장에서 새 경주를 수집하세요.', true);
    nudgeExtensionCollect();                  // 확장에 즉시 수집 요청(새 경주)
  }

  /** 확장이 마지막으로 수집한 경주(server latest) 조회 → 상단 표시 + (변경/수동 시) 화면 갱신 */
  async function refreshCurrentRace(manual) {
    const label = $('#rrCurrentRace'), status = $('#rrStatus');
    if (manual) {
      if (status) status.textContent = '확장에 즉시 수집 요청…';
      nudgeExtensionCollect();                       // [1번] 확장에서 현재 경주 즉시 수집
      await new Promise((r) => setTimeout(r, 1200));  // 확장 수집·서버 저장 잠깐 대기
    }
    let rk = null, stale = false;
    try { const d = await (await fetch('/api/current_race')).json(); rk = d && d.raceKey; stale = !!(d && d.stale); } catch (_) { /* */ }
    if (!rk) {  // 폴백: 구 엔드포인트
      try { const d = await (await fetch('/api/odds/triple/latest')).json(); rk = d && d.raceKey; stale = !!(d && d.stale); } catch (_) { /* */ }
    }
    // [경주전환 잔존 방어] 최신 경주도 30분+ 미갱신(=끝난 경주)이면 표시 억제(직전 경주 잔존 방지)
    if (!rk || stale) {
      if (label) label.textContent = stale ? '— (직전 경주 종료 · 새 수집 대기)' : '— (수집된 경주 없음)';
      if (status) status.textContent = manual ? '확장 [전체 자동 수집]을 먼저 실행하세요.' : '';
      if (stale) _rrLastRk = null;   // 다음에 새 경주가 오면 '변경'으로 감지
      return;
    }
    const changed = rk !== _rrLastRk;

    if (manual) {
      // [수동 새로고침] 완전 갱신: 화면 업데이트 + 배당 타임라인 초기화 + 성공 메시지
      //   수동 전환은 명시적 사용자 행동이므로 고정 상태여도 전환(고정 대상을 새 경주로 갱신).
      if (label) label.textContent = rk;
      if (changed) hardResetRaceState();              // 경주 바뀌었으면 이전 상태 완전 초기화
      _rrLastRk = rk;
      resetOddsTimeline();                            // [1번] 배당 타임라인 초기화(새 경주 시작)
      refreshActiveView(rk);                          // 분석기 화면 자동 업데이트
      if (status) status.textContent = _racePinned ? `✅ ${rk} 업데이트(고정 유지)` : '✅ 업데이트 완료';
      notify(`✅ ${rk} 업데이트 완료`, true);          // 예: "✅ 제주 3경주 업데이트 완료"
      return;
    }
    // [경주 자동 전환 버그 수정] 자동 감지 경로 — 고정/다른 경마장 시 자동 전환 차단.
    //   [3번 고정] 📌 고정 중이면 다른 경주가 수집돼도 화면 전환 안 함(라벨엔 감지된 경주 힌트만).
    if (_racePinned && changed && _rrLastRk != null) {
      if (label) label.textContent = `${_rrLastRk}  📌`;   // 보고 있는(고정) 경주 유지 표시
      if (status) status.textContent = `📌 고정 중 · 다른 경주 감지(${rk}) — 전환하려면 고정 해제/새로고침`;
      return;                                          // _rrLastRk 갱신 안 함(고정 유지)
    }
    // [2번 종목/경마장 혼재 방지] 고정 안 했어도 '다른 경마장'으로의 자동 전환은 차단(사가↔오비히로 혼재).
    //   같은 경마장의 다음 경주(사가 5R→6R)는 기존대로 자동 전환 허용(정상 진행).
    if (changed && _rrLastRk != null) {
      const prevVenue = _raceVenue(_rrLastRk), newVenue = _raceVenue(rk);
      if (prevVenue && newVenue && prevVenue !== newVenue) {
        if (label) label.textContent = `${_rrLastRk}`;   // 현재 경마장 경주 유지
        if (status) status.textContent = `🔀 다른 경마장 경주 감지(${rk}) — 전환하려면 새로고침(자동 혼재 방지)`;
        return;                                        // 다른 경마장으로 자동 전환하지 않음
      }
      if (label) label.textContent = rk;
      // 같은 경마장 다음 경주 → 자동 초기화 + 새 경주 화면 전환(직전 경주 잔존 방지)
      _rrLastRk = rk;
      hardResetRaceState();                           // [1번] 직전 경주 스냅샷·이상감지·타임라인·경고 초기화
      _rrLastRk = rk;                                 // hardReset이 null로 만든 값 복원(현재 경주로 고정)
      refreshActiveView(rk);                          // 새 경주 첫 수집을 기준값으로 자동 표시
      if (status) status.textContent = '🔄 새 경주 자동 전환됨';
      notify(`🔄 새 경주 자동 전환: ${rk}`, true);
      return;
    }
    if (label) label.textContent = rk;
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
    if (['boat', 'cycle', 'bike', 'central'].includes(tab)) { try { pollSportOdds(); } catch (_) { /* */ } return; }   // [복구] 종목 탭 경주 변경·수동 새로고침 즉시 반영
    try { autoSelectKoreaRace(rk); } catch (_) { /* */ }
  }

  // ═══ [다중 경주 동시 배당판] 별도 추가 기능(기존 단일 경주 분석 무영향) ═══
  let _multiTimer = null, _multiAlerted = {};
  function initMultiRace() {
    const rb = $('#multiRefreshBtn'); if (rb) rb.addEventListener('click', renderMultiDashboard);
    const sb = $('#multiScheduleBtn'); if (sb) sb.addEventListener('click', async () => {
      const st = $('#multiStatus'); if (st) st.textContent = '📅 오늘 스케줄 수집 중…';
      try { await fetch('/api/multi/schedule', { method: 'POST' }); } catch (_) { /* */ }
      renderMultiDashboard();
    });
    const cb = $('#multiDetailClose'); if (cb) cb.addEventListener('click', () => { const c = $('#multiDetailCard'); if (c) c.style.display = 'none'; });
  }
  function startMultiRaceWatch() {
    renderMultiDashboard();
    if (_multiTimer) clearInterval(_multiTimer);
    _multiTimer = setInterval(renderMultiDashboard, 30000);   // 30초 폴링
  }
  function stopMultiRaceWatch() { if (_multiTimer) { clearInterval(_multiTimer); _multiTimer = null; } }

  async function renderMultiDashboard() {
    const box = $('#multiCards'), status = $('#multiStatus'); if (!box) return;
    let d;
    try { d = await (await fetch('/api/multi/dashboard')).json(); }
    catch (_) { if (status) status.textContent = '대시보드 로드 실패'; return; }
    const cards = (d && d.cards) || [];
    const nColl = (d && d.collected) || 0, nSched = cards.length - nColl;
    if (status) status.textContent = `오늘 ${cards.length}경주 (수집·분석 ${nColl} · 예정 ${nSched}) · 30초 자동 갱신${cards.length ? '' : ' (스케줄 갱신을 눌러 오늘 개최를 불러오세요)'}`;
    // [5번] T-3분 마감 임박 알림(소리+배너, 경주당 1회)
    const urgent = (d && d.urgent) || [];
    const banner = $('#multiUrgentBanner');
    const fresh = urgent.filter((k) => !_multiAlerted[k]);
    if (fresh.length && banner) {
      fresh.forEach((k) => { _multiAlerted[k] = 1; });
      banner.style.display = 'block';
      banner.innerHTML = fresh.map((k) => `<div style="padding:8px 12px;background:#7f1d1d;border:2px solid #ef4444;border-radius:8px;color:#fecaca;font-weight:800;font-size:15px;margin:2px 0">⚡ ${esc(k)} 마감 3분전!</div>`).join('');
      try { playAlert('🔴'); } catch (_) { /* */ }
      setTimeout(() => { if (banner) banner.style.display = 'none'; }, 30000);
    } else if (!urgent.length && banner) { banner.style.display = 'none'; }
    // [5번] 종목별 토글 + [4번] 종목별 그룹핑
    const bySport = (d && d.bySport) || {};
    _renderMultiToggles(bySport);
    const shown = cards.filter((c) => _multiSportOn(c.sport || 'horse'));
    // 종목 그룹 순서(경정 제외): 일본경마 → 경륜 → 한국경마
    const order = ['horse', 'central', 'cycle', 'korea'];
    let html = '';
    order.forEach((sp) => {
      const g = shown.filter((c) => (c.sport || 'horse') === sp);
      if (!g.length) return;
      html += `<div style="grid-column:1/-1;margin:6px 0 2px;font-weight:800;color:#94a3b8">${_multiSportLabel(sp)} <span class="hint" style="font-weight:400">${g.length}경주</span></div>`;
      html += g.map(_multiCardHtml).join('');
    });
    box.innerHTML = html || '<p class="hint">표시할 경주가 없습니다. (종목 토글 확인 · 발주 10분전부터 자동 수집)</p>';
    box.querySelectorAll('[data-mkey]').forEach((el) => el.addEventListener('click', () => openMultiDetail(el.dataset.mkey)));
  }
  // [5번] 종목 토글 상태(localStorage) — 경정 기본 제외
  const _multiSportKey = 'multiSportsOn';
  function _multiSportOn(sp) {
    let on; try { on = JSON.parse(localStorage.getItem(_multiSportKey) || 'null'); } catch (_) { on = null; }
    if (!on) return sp !== 'boat';   // 기본: 경정 외 전부 켜짐
    return !!on[sp];
  }
  function _multiSportLabel(sp) {
    return ({ horse: '🇯🇵 일본경마', central: '🏇 중앙경마', cycle: '🚴 경륜', boat: '🚤 경정', korea: '🇰🇷 한국경마' })[sp] || '🏇 경마';
  }
  function _renderMultiToggles(bySport) {
    const bar = $('#multiSportToggles'); if (!bar) return;
    const sports = ['horse', 'central', 'cycle', 'korea'];   // 경정 제외
    bar.innerHTML = sports.map((sp) => {
      const on = _multiSportOn(sp), n = bySport[sp] || 0;
      return `<label style="cursor:pointer;font-size:13px;padding:3px 8px;border-radius:6px;border:1px solid ${on ? '#38d39f' : '#334155'};color:${on ? '#38d39f' : '#94a3b8'}"><input type="checkbox" data-sp="${sp}" ${on ? 'checked' : ''} style="vertical-align:middle"> ${_multiSportLabel(sp)} (${n})</label>`;
    }).join(' ');
    bar.querySelectorAll('input[data-sp]').forEach((cb) => cb.addEventListener('change', () => {
      let on; try { on = JSON.parse(localStorage.getItem(_multiSportKey) || 'null'); } catch (_) { on = null; }
      if (!on) on = { horse: true, central: true, cycle: true, korea: true, boat: false };
      on[cb.dataset.sp] = cb.checked;
      try { localStorage.setItem(_multiSportKey, JSON.stringify(on)); } catch (_) { /* */ }
      renderMultiDashboard();
    }));
  }
  function _multiFmtLeft(s) {
    if (s == null) return '—';
    if (s < 0) return '마감';
    const m = Math.floor(s / 60), ss = s % 60;
    return m > 0 ? `${m}분 ${ss}초` : `${ss}초`;
  }
  function _multiCardHtml(c) {
    const col = c.urgency === 'urgent' ? '#ef4444' : (c.urgency === 'warn' ? '#f59e0b' : '#334155');
    const bg = c.urgency === 'urgent' ? 'rgba(239,68,68,.10)' : (c.urgency === 'warn' ? 'rgba(245,158,11,.08)' : 'rgba(255,255,255,.03)');
    const leftTxt = c.afterClose ? '마감' : _multiFmtLeft(c.secondsLeft);
    // [예정 경주] 아직 배당 수집 전이면 카운트다운만 표시(발주 10분전부터 자동 수집→분석)
    if (c.scheduled) {
      return `<div data-mkey="${esc(c.raceKey)}" title="발주 10분전부터 자동 수집됩니다" style="cursor:pointer;border:2px dashed ${col};border-radius:10px;padding:10px;background:${bg};opacity:.85">
        <div style="display:flex;align-items:center;gap:6px">
          <b style="font-size:15px;color:#e2e8f0">${esc(c.venue || '')} ${c.raceNo}R</b>
          <span style="flex:1"></span>
          <b style="color:${col};font-size:13px">${c.urgency === 'urgent' ? '⚡ ' : ''}${leftTxt}</b>
        </div>
        <div class="hint" style="font-size:11px;margin:2px 0">발주 ${esc(c.postTime || '?')}</div>
        <div class="hint" style="font-size:11px">⏳ 수집 대기 (발주 10분전 자동 시작)</div>
      </div>`;
    }
    const sigs = (c.signals || []).map((s) => `<div style="font-size:12px;font-weight:700;margin:1px 0">${esc(s.text)}</div>`).join('') || '<div class="hint" style="font-size:11px">신호 없음</div>';
    const keyH = (c.keyHorses || []).join(' · ') || '-';
    // [3번·💎 중고배당 유력마] 있으면 카드에 💎 배지 + 요약(별도 강조)
    const mh = (c.midHigh || []);
    const mhBadge = mh.length ? '<span style="background:#f0abfc;color:#1a1a1a;font-weight:800;font-size:11px;padding:1px 6px;border-radius:5px">💎 고배당</span>' : '';
    const anBadge = (c.anomaly && !mh.length) ? '<span style="background:#ef4444;color:#fff;font-weight:800;font-size:11px;padding:1px 6px;border-radius:5px">⚡ 이상감지</span>' : '';
    const mhLine = mh.length ? `<div style="margin:3px 0;font-size:12px;color:#f0abfc;font-weight:700">💎 ${mh.map((m) => `${m.no}번(${m.odds}배)`).join(' · ')}</div>` : '';
    const borderW = mh.length ? '3px' : '2px';
    const spLabel = _multiSportLabel(c.sport);
    return `<div data-mkey="${esc(c.raceKey)}" title="클릭 → 상세 분석" style="cursor:pointer;border:${borderW} solid ${mh.length ? '#f0abfc' : col};border-radius:10px;padding:10px;background:${mh.length ? 'rgba(240,171,252,.08)' : bg}">
      <div style="display:flex;align-items:center;gap:6px">
        <span style="font-size:11px">${spLabel}</span>
        <b style="font-size:15px;color:#e2e8f0">${esc(c.venue || '')} ${c.raceNo}R</b>
        ${mhBadge}${anBadge}
        <span style="flex:1"></span>
        <b style="color:${col};font-size:13px">${c.urgency === 'urgent' ? '⚡ ' : ''}${leftTxt}</b>
      </div>
      ${mhLine}
      <div class="hint" style="font-size:11px;margin:2px 0">발주 ${esc(c.postTime || '?')}${c.confidence != null ? ' · 확신도 ' + esc(String(c.confidence)) : ''}</div>
      <div style="margin:4px 0"><span class="hint" style="font-size:11px">⭐ 유력마 </span><b style="color:#4ea1ff">${esc(keyH)}</b></div>
      ${sigs}
    </div>`;
  }
  async function openMultiDetail(key) {
    const card = $('#multiDetailCard'), title = $('#multiDetailTitle'), bodyEl = $('#multiDetailBody');
    if (!card) return;
    card.style.display = 'block';
    if (title) title.textContent = `${key} 상세 분석`;
    if (bodyEl) bodyEl.innerHTML = '<p class="hint">⏳ 분석 로드 중…</p>';
    try { card.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (_) { /* */ }
    let a;
    try { a = await (await fetch('/api/multi/race/' + encodeURIComponent(key))).json(); }
    catch (_) { if (bodyEl) bodyEl.innerHTML = '<p class="err">분석 로드 실패</p>'; return; }
    if (!a || a.error || a.waiting) { if (bodyEl) bodyEl.innerHTML = `<p class="hint">${esc((a && (a.error || (a.waiting && '배당 수집 대기 중'))) || '데이터 없음')}</p>`; return; }
    // [4번] 기존 분석 렌더 재사용(복승 매트릭스·핵심 신호·추천 조합) + 결과 입력 버튼
    let html = '';
    try { html = sportAnalysisHTML(a); } catch (_) { html = '<p class="hint">렌더 오류</p>'; }
    html += `<div style="margin-top:10px"><button class="btn btn-primary" onclick="document.querySelector('.tab-btn[data-tab=&quot;result&quot;]').click()">📝 결과 입력하러 가기</button></div>`;
    if (bodyEl) bodyEl.innerHTML = html;
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
      let head = '<tr><th class="corner">1착↓ / 2착→</th>' + nos.map((n) => `<th>${n}</th>`).join('') + '</tr>';
      let body = '';
      for (const rowNo of nos) {
        let tds = '';
        for (const colNo of nos) {
          if (rowNo === colNo) { tds += '<td class="diag">—</td>'; continue; }
          const v = lastNum(ex[`${rowNo}>${colNo}`]);
          if (v > 0) {
            const rec = recX.has(`${rowNo}>${colNo}`) ? ' rec-x' : '';
            tds += `<td class="cell${rec}" style="background:${heatColor(v, lo, hi)}" title="${rowNo}번 1착·${colNo}번 2착 = ${v}배">${v}</td>`;
          } else tds += '<td class="empty">·</td>';
        }
        body += `<tr><th>${rowNo}</th>${tds}</tr>`;
      }
      html += `<div class="matrix-title">🔀 쌍승 매트릭스 <span class="hint" style="font-weight:400">행=1착·열=2착 · ${vals.length}쌍</span></div>
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
  function renderBMED(b, budgetSel) {
    if (!b) return '';
    const bEl = document.querySelector(budgetSel || '#tripleBudget');
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
        // [2번] 배당별 손익률(%) = 총원금 대비 (적중 시 회수/원금 − 1)
        const pct = c.payoutRatio != null ? Math.round((c.payoutRatio - 1) * 100) : null;
        const pctTxt = pct != null ? ` <span style="color:${pct >= 0 ? '#38d39f' : '#f87171'}">(${pct >= 0 ? '+' : ''}${pct}%)</span>` : '';
        // [3번] 원금보전 가능 여부 — 이 조합만 적중해도 총원금 이상 회수되면 ✅, 아니면 ❌(손실 N원)
        const preserved = c.preserved != null ? c.preserved : (c.payoutRatio != null && c.payoutRatio >= 1);
        const lossTxt = (!preserved && budget > 0 && c.payoutRatio != null) ? ` (손실 ${won((1 - c.payoutRatio) * budget)}원)` : '';
        const presTxt = c.payoutRatio == null ? '' : (preserved
          ? ' · <span style="color:#38d39f">원금보전 ✅</span>'
          : ` · <span style="color:#f87171">원금보전 ❌${lossTxt}</span>`);
        return `<div style="margin:3px 0"><b style="color:#c4b5fd">${c.label}</b> ${c.combo[0]}+${c.combo[1]}(${c.odds}배): <b>${stake}${budget > 0 ? '원' : ''}</b> → 적중 시 ${pay}${budget > 0 ? '원' : ''}${pctTxt}${presTxt}</div>`;
      }).join('');
      // [2번] 시나리오 자동 표시: 최선(가장 좋은 조합 적중) · 평균(커버 조합 균등 적중 가정) + 전부 미적중.
      const pnl = (ratio) => (ratio == null ? '<span class="hint">-</span>' : sign((ratio - 1) * budget));
      const sc = budget > 0 ? `<div class="hint" style="margin-top:5px">
        최선 <b style="color:#38d39f">${pnl(ins.bestRatio)}</b> · 평균 <b style="color:${(ins.avgRatio || 0) >= 1 ? '#38d39f' : '#f87171'}">${pnl(ins.avgRatio)}</b><br>
        <b style="color:#f87171">전부 미적중 시 ${sign(-budget)}원(전액 손실)</b> · 기대환수 ${ins.expectedReturn != null ? ins.expectedReturn + '%' : '-'}</div>` : '';
      insBlock = `<div style="margin-top:8px;padding:8px;border:1px dashed #a78bfa;border-radius:7px;background:rgba(167,139,250,.08)">
        <div style="font-weight:800;color:#c4b5fd">🛡️ 보험용 추천 (BMED 보험형 · ${esc(ins.band || '')})</div>
        <div class="hint" style="margin:2px 0 5px">1착축 ${ins.anchor}번 · ${ins.combos.map((c) => c.label).join(' / ')} 순 배분 ${ins.preserved ? '<span style="color:#38d39f">· 원금 보전</span>' : '<span style="color:#ffb020">· 저배당(원금보전 제한)</span>'}</div>
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
  // [신규 경고시스템 3번] 경고 신호 감지 배너 — 급변 말 + 추천에 편입된 급변 조합
  function renderAlertSignal(al, roleMap) {
    if (!al || !(al.horses || []).length) return '';
    roleMap = roleMap || {};   // [보완#1] 유력/제거 색상 맵(없으면 기존 표기 유지)
    // 경고 배너의 급변 말·조합도 베팅표와 동일하게 유력(녹색)/제거(빨강·주황)로 채색 통일.
    const horses = al.horses.map((h) => {
      const role = roleMap[+h];
      const col = role === 'fav' ? _ROLE_COLOR.fav : role === 'cut' ? _ROLE_COLOR.cut : role === 'weakcut' ? _ROLE_COLOR.weakcut : '#ffd24f';
      const deco = role === 'cut' ? ';text-decoration:line-through' : '';
      return `<b style="color:${col}${deco}">${h}번</b>`;
    }).join('+');
    const drops = (al.drops || []).map((d) =>
      `<div style="margin:2px 0"><span class="chip chip-red">${_colorCombo(d.combo, roleMap)}</span> <span class="hint">${d.before}→${d.after}배 (▼${Math.abs(d.pct)}%)</span></div>`).join('');
    const picks = (al.picks || []).map((p) => {
      const od = p.expOdds != null ? `${p.expOdds}배` : (p.expOddsEst != null ? `추정 ${p.expOddsEst}배` : '');
      return `<span class="chip chip-red">${p.kind} ${_colorCombo(p.combo, roleMap)}${od ? ` <span class="hint">${od}</span>` : ''}</span>`;
    }).join(' ');
    return `<div style="margin:8px 0;padding:9px 11px;border:2px solid #ffb020;border-radius:8px;background:rgba(255,176,32,.1)">
      <div style="font-size:15px;font-weight:800;color:#ffd24f">⚠️ 경고 신호 감지!</div>
      <div class="hint" style="margin:3px 0 5px">${horses} 배당 급변 (${esc(al.topDrop || '')}) → <b style="color:#ffd24f">해당 말 조합을 추천에 포함</b>(고배당 대비)</div>
      ${drops ? `<div style="margin:4px 0">${drops}</div>` : ''}
      ${picks ? `<div style="margin:5px 0 0"><span class="hint">추천 업데이트</span><br>${picks}</div>` : ''}</div>`;
  }

  // [신규 3·4·5번] 이상감지 말 변경 이력 + 신호 안정화(2연속 확정) + 최종 유효 신호 시점
  function renderSignalTimeline(st) {
    if (!st) return '';
    const changes = st.changes || [];
    const confirmed = st.confirmed || [];
    const candidates = st.candidates || [];
    const excl = st.excluded || {};
    const events = st.events || {};
    // 변경 이력 없고 확정/후보도 없으면(신호 자체가 없음) 생략
    if (!changes.length && !confirmed.length && !candidates.length && !(excl.after_close || excl.next_race)) return '';
    const mb = (o) => (o && o.minutes_before != null ? `T-${o.minutes_before}분` : (o && o.time ? o.time : '—'));
    // [3번] 변경 이력
    const chLines = changes.map((c) =>
      `<div style="margin:2px 0"><span class="hint">${mb(c)}</span> <b style="color:#ffd24f">${c.previous_signal}→${c.new_signal}</b>`
      + `${c.prev_was_candidate ? ` <span class="chip" style="border-color:#8a94a6;color:#b8c0cc">${c.previous_signal} 1회 감지 후 소멸</span>` : ''}`
      + `<br><span class="hint" style="font-size:11px">↳ ${esc(c.reason || '')}</span></div>`).join('');
    // [4번] 확정/후보 신호
    const confChips = confirmed.map((h) => {
      const cf = (events[String(h)] || {}).confirmed;
      return `<span class="chip chip-red">✅ ${h}번 확정${cf ? ` <span class="hint">(${mb(cf)})</span>` : ''}</span>`;
    }).join(' ');
    const candChips = candidates.map((h) =>
      `<span class="chip" style="border-color:#8a94a6;color:#b8c0cc">${h}번 후보(1회)</span>`).join(' ');
    // [5번] 최종 유효 신호 시점(말별 최초/소멸/확정)
    const timeLines = Object.keys(events).map((h) => {
      const e = events[h] || {};
      const parts = [];
      if (e.first) parts.push(`최초 ${mb(e.first)}`);
      if (e.confirmed) parts.push(`<b style="color:#ff8a3d">확정 ${mb(e.confirmed)}</b>`);
      else if ((e.count || 0) <= 1) parts.push('<span style="color:#8a94a6">1회 감지(미확정)</span>');
      if (e.vanished) parts.push(`소멸 ${mb(e.vanished)}`);
      return `<div style="margin:1px 0"><b>${h}번</b>: ${parts.join(' · ')}</div>`;
    }).join('');
    const finalTxt = st.finalSignal != null
      ? `<b style="color:${st.finalConfirmed ? '#ff5c5c' : '#ffd24f'}">${st.finalSignal}번</b> ${st.finalConfirmed ? '(2연속 확정)' : '(후보)'}`
      : '<span class="hint">감지 없음</span>';
    const exclTxt = (excl.after_close || excl.next_race)
      ? `<div class="hint" style="margin-top:4px;font-size:11px">🗑️ 제외된 데이터: ${excl.after_close ? `마감 후 ${excl.after_close}건` : ''}${(excl.after_close && excl.next_race) ? ' · ' : ''}${excl.next_race ? `다음 경주 혼입 ${excl.next_race}건` : ''} (분석·타임라인에서 제외됨)</div>`
      : '';
    return `<div style="margin:8px 0;padding:9px 11px;border:2px solid #7dd3fc;border-radius:8px;background:rgba(125,211,252,.08)">
      <div style="font-size:14px;font-weight:800;color:#7dd3fc">🎯 이상감지 신호 안정화 · 변경 이력</div>
      <div style="margin:4px 0">최종 유효 신호: ${finalTxt}</div>
      ${(confChips || candChips) ? `<div style="margin:4px 0">${confChips}${confChips && candChips ? ' ' : ''}${candChips}</div>` : ''}
      ${chLines ? `<div style="margin:5px 0"><span class="hint">🔀 신호 변경 이력</span>${chLines}</div>` : ''}
      ${timeLines ? `<div style="margin:5px 0"><span class="hint">⏱️ 유효 신호 시점</span><div style="font-size:12px;margin-top:2px">${timeLines}</div></div>` : ''}
      ${exclTxt}</div>`;
  }

  // [신규] 전적 우수하나 시장 비인기 배너(역배열 아님) — 전적은 좋은데 배당이 비인기인 말
  function _strongUnpopularBlock(inv) {
    const su = (inv && inv.strongUnpopular) || [];
    if (!su.length) return '';
    const items = su.map((h) => `<b style="color:#93c5fd">${h.no}번</b> <span class="hint">(전적 ${h.formScore} · 시장 ${h.reprOdds}배${h.popRank ? ` · 인기 ${h.popRank}위` : ''})</span>`).join(' · ');
    return `<div style="margin:8px 0;padding:9px 11px;border:2px solid #3b82f6;border-radius:8px;background:rgba(59,130,246,.1)">
      <div style="font-size:14px;font-weight:800;color:#93c5fd">📈 전적 우수하나 시장 비인기</div>
      <div class="hint" style="margin:3px 0 0;line-height:1.6">${items}<br>→ <b style="color:#dbeafe">역배열 아님</b> — 전적은 우수하나 배당은 비인기(시장이 아직 안 밀어줌). 참고만.</div></div>`;
  }

  // [새 규칙·카와사키11R] 막판 급락+역배열 동시 감지 말 → 삼복승 강제보험 배너(유력마 순위 무관).
  function renderForcedTrifecta(a) {
    const ft = (a && a.forcedTrifecta) || {};
    if (!ft.active || !(ft.horses || []).length) return '';
    const horses = ft.horses.map((h) =>
      `<div style="margin:2px 0"><b style="color:#fca5a5">${h.no}번</b> <span class="hint">${esc(h.note || '')}</span></div>`).join('');
    const combos = (ft.combos || []).map((c) =>
      `<span class="chip chip-red">${c.join('+')} <span class="hint">강제</span></span>`).join(' ');
    return `<div style="margin:8px 0;padding:9px 11px;border:2px solid #ef4444;border-radius:8px;background:rgba(239,68,68,.12)">
      <div style="font-size:15px;font-weight:800;color:#fca5a5">🚨 삼복승 강제보험 (막판 급락+역배열)</div>
      <div class="hint" style="margin:3px 0 4px">${esc(ft.note || '')} — <b style="color:#fecaca">TOP3 밖이어도 강제 편성</b> (카와사키 11R 학습 규칙)</div>
      ${horses}
      ${combos ? `<div style="margin-top:4px">${combos}</div>` : ''}</div>`;
  }

  // [히로시마 2R 학습] 역배열 실질유력마 축 받치기 복승 — 실질유력마+다른유력마 조합 놓침 방지.
  function renderReversalBacking(a) {
    const rb = (a && a.reversalBacking) || [];
    if (!rb.length) return '';
    const lead = rb[0] && rb[0].lead;
    const lines = rb.map((b) => {
      const o = (b.odds != null) ? `<span class="hint">복승 ${b.odds}배</span>` : '<span class="hint">배당 미수집</span>';
      return `<div style="margin:2px 0"><span class="chip" style="background:rgba(168,85,247,.18);border:1px solid #a855f7;color:#e9d5ff">${(b.combo || []).join('+')}</span> ${o} <span class="hint">${esc(b.note || '')}</span></div>`;
    }).join('');
    return `<div style="margin:8px 0;padding:9px 11px;border:1.5px solid #a855f7;border-radius:8px;background:rgba(168,85,247,.1)">
      <div style="font-size:14px;font-weight:800;color:#d8b4fe">🎯 복승 받치기 (역배열 실질유력 ${lead != null ? lead + '번' : ''} 축)</div>
      <div class="hint" style="margin:3px 0 4px">고배당인데 시장이 실질 승자로 미는 말을 축으로, <b style="color:#e9d5ff">다른 유력마와 받치기</b> — 실질유력마+다른유력마(예: 7+4) 조합 놓침 방지 <span class="hint">(히로시마 2R 학습)</span></div>
      ${lines}</div>`;
  }

  // [추천 말 수 유연화] 신호 강도별 추천 말 수 가이드(강제 아님·안내 배지).
  function renderRecommendFlex(a) {
    const rf = a && a.recommendFlex;
    if (!rf) return '';
    if (!rf.recommend && rf.maxHorses === 0 && rf.signalCount === 0) {
      return `<div style="margin:6px 0;padding:6px 9px;border-left:3px solid #8a94a6;background:rgba(138,148,166,.12);border-radius:6px;color:#b8c0cc">
        🎯 <b>추천 말 수</b>: <b>신호 없음 → 추천 보류</b> <span class="hint">(신호 강도가 오르면 자동으로 추천 말 수 안내)</span></div>`;
    }
    const range = rf.minHorses === rf.maxHorses ? `${rf.maxHorses}두` : `${rf.minHorses}~${rf.maxHorses}두`;
    return `<div style="margin:6px 0;padding:6px 9px;border-left:3px solid #38bdf8;background:rgba(56,189,248,.1);border-radius:6px;color:#7dd3fc">
      🎯 <b>추천 말 수 가이드</b>: 신호 <b>${rf.signalCount}개</b> → <b style="color:#38d39f">${range}</b> <span class="hint">${esc(rf.note || '')}</span></div>`;
  }

  // [유력마 1마리] 축+배당 낮은 2마리 최소 복승 + 패스·소액 경고 배너.
  function renderSingleFavorite(a) {
    const sf = a && a.singleFavorite;
    if (!sf || sf.axis == null) return '';
    const parts = (sf.partners || []).map((p) => {
      const od = (sf.partnerOdds && sf.partnerOdds[p] != null) ? `(${sf.partnerOdds[p]}배)` : '';
      return `<b style="color:#fbbf24">${p}번</b><span class="hint">${od}</span>`;
    }).join(' · ');
    const combos = (sf.partners || []).map((p) => `<span class="chip" style="border-color:#f59e0b;color:#fbbf24">${[sf.axis, p].sort((x, y) => x - y).join('+')}</span>`).join(' ');
    return `<div style="margin:8px 0;padding:9px 11px;border:2px solid #f59e0b;border-radius:8px;background:rgba(245,158,11,.1)">
      <div style="font-size:15px;font-weight:800;color:#fbbf24">⚠️ 유력마 1마리 — 패스 또는 소액만 권장</div>
      <div class="hint" style="margin:3px 0 4px">유력마가 <b style="color:#38d39f">${sf.axis}번</b> 1마리뿐입니다. 축 + 배당 낮은 2마리로 최소 복승만 구성했습니다(소액).</div>
      <div style="margin:2px 0">동반 후보: ${parts || '<span class="hint">없음</span>'}</div>
      ${combos ? `<div style="margin-top:3px"><span class="hint">최소 복승:</span> ${combos}</div>` : ''}</div>`;
  }

  // [고배당 동반 패턴·참고] 메인과 별도의 참고 추천 — 고배당 신호말과 함께 들어올 다른 고배당 말.
  function renderHighOddsCompanion(a) {
    const hc = a && a.highOddsCompanion;
    if (!hc || !hc.active || !(hc.items || []).length) return '';
    const lr = hc.learned || {};
    const learnedTxt = (lr.rate != null)
      ? `데이터 기반 패턴: <b>고배당 1착 시 3착 내 고배당 포함 ${lr.rate}%</b> <span class="hint">(${lr.hits}/${lr.races}건${lr.reliable ? ' · 신뢰 가능' : ' · 표본 부족'})</span>`
      : `데이터 기반 패턴: <span class="hint">표본 수집 중(결과 입력 쌓이면 자동 신뢰도 표시)</span>`;
    const items = hc.items.map((it) => {
      const partners = (it.partners || []).map((p) =>
        `<b style="color:#fbbf24">${p.no}번</b><span class="hint">(${p.odds}배)</span>`).join(' · ');
      const trios = (it.trioBets || it.trios || []).map((t) => {
        const combo = t.combo || t;
        const od = (t.expOddsEst != null) ? ` <span class="hint">추정 ${t.expOddsEst}배</span>` : '';
        return `<span class="chip" style="border-color:#a78bfa;color:#c4b5fd">${combo.join('+')}${od}</span>`;
      }).join(' ');
      return `<div style="margin:5px 0;padding:6px 9px;background:rgba(255,255,255,.03);border-radius:6px">
        <div><b style="color:#fbbf24">${it.no}번</b><span class="hint">(${it.odds}배)</span> <span class="hint">${esc(it.signal || '')}</span> 감지 시 함께 들어올 가능성:</div>
        <div style="margin:2px 0">${partners || '<span class="hint">동반 후보 없음</span>'}</div>
        ${trios ? `<div style="margin-top:3px"><span class="hint">참고 삼복승:</span> ${trios}</div>` : ''}</div>`;
    }).join('');
    return `<div style="margin:8px 0;padding:9px 11px;border:2px dashed #a78bfa;border-radius:8px;background:rgba(168,85,247,.07)">
      <div style="font-size:14px;font-weight:800;color:#c4b5fd">💡 참고 추천 (고배당 동반 패턴)</div>
      <div class="hint" style="margin:2px 0 4px">${learnedTxt}</div>
      ${items}
      <div class="hint" style="font-size:11px;margin-top:3px">⚠ 메인 추천과 <b>별도 참고용</b>입니다(강제 편성 아님).</div></div>`;
  }

  // [근본해결3] raw 쌍승역전 조기 반영 — 마감 전 예비 유력마 배너(정식 공식 확정 전 조기 포착).
  function renderPreReversal(a) {
    const pr = (a && a.preReversal) || [];
    if (!pr.length || a.afterClose) return '';
    const horses = pr.map((n) => `<b style="color:#c084fc">${n}번</b>`).join(' · ');
    return `<div style="margin:6px 0;padding:7px 9px;border-left:3px solid #a855f7;background:rgba(168,85,247,.12);border-radius:6px;color:#e9d5ff">
      ⚡ <b>쌍승역전 조기 감지 → 예비 유력마 반영</b>: ${horses} <span class="hint">(정식 공식 확정 전에도 실질 1착 후보를 마감 전 유력마로 조기 반영 — 추천에 즉시 편성)</span></div>`;
  }

  // [1·4번] 마감 후 대급락(50%+) 배너 — 추천 미반영·참고만 + 학습된 입상률(신뢰 시 강조).
  function renderAfterCloseSurge(s) {
    if (!s || !s.detected) return '';
    const horses = (s.horses || []).map((n) => `<b style="color:#ffd24f">${n}번</b>`).join(' · ');
    const drops = (s.drops || []).slice(0, 4).map((d) =>
      `<div style="margin:2px 0"><span class="chip chip-red">${(d.combo || []).join('+')}</span> <span class="hint">${d.before}→${d.after}배 (▼${Math.abs(d.pct)}%)</span></div>`).join('');
    const learned = (s.learnedSample >= 1 && s.learnedHitRate != null)
      ? `<div style="margin-top:5px;padding:5px 7px;border-radius:6px;background:${s.reliable ? 'rgba(56,211,159,.12)' : 'rgba(138,148,166,.12)'}">
          🧠 학습: <b style="color:${s.reliable ? '#38d39f' : '#b8c0cc'}">마감 후 대급락 → 실제 입상률 ${s.learnedHitRate}%</b> <span class="hint">(표본 ${s.learnedSample}건${s.reliable ? ' · 신뢰 가능' : ' · 표본 부족'})</span>${s.reliable ? '<br><span style="color:#38d39f">→ 다음 경주에서 같은 패턴 발생 시 참고하세요</span>' : ''}</div>` : '';
    return `<div style="margin:6px 0;padding:8px 10px;border:2px solid #f59e0b;border-radius:8px;background:rgba(245,158,11,.1)">
      <div style="font-size:15px;font-weight:800;color:#fbbf24">⚡ 마감 후 대급락 감지!</div>
      <div style="margin:3px 0"><span class="hint">대급락 말:</span> ${horses || '-'}</div>
      ${drops}
      <div class="hint" style="margin-top:3px">${esc(s.note || '')}</div>
      ${learned}</div>`;
  }

  function renderInverse(inv) {
    if (!inv) return '';
    // 역배열(쌍승역전) 아니면 → 전적 우수·시장 비인기만(있으면) 표시
    if (!inv.detected) return _strongUnpopularBlock(inv);
    const b = inv.banner || {};
    const kindColor = { '쌍승역전': '#ff8a8a', '복승불일치': '#ffd24f', '배당압축': '#7dd3fc', '초과급락': '#ff5c5c' };
    // 배너 상단부: 단승 1위 · 복승 최저 · 쌍승 역전
    const lines = [];
    if (b.refNo != null) lines.push(`${b.refLabel}: <b>${b.refNo}번</b>${b.refOdds != null ? ` (${b.refOdds}배)` : ''}`);
    if (b.favPair) lines.push(`복승 최저: <b>${b.favPair.join('+')}</b> (${b.favOdds}배) → ${b.favNormal ? '정상(단승1위 포함)' : '<span style="color:#ff8a8a">불일치(단승1위 빠짐)</span>'}`);
    if (b.reversal) { const r = b.reversal; lines.push(`쌍승 역전: <span style="color:#ff8a8a"><b>${r.challenger}번 1착·${r.favorite}번 2착</b> ${r.reverseExacta}배 &lt; ${r.favorite}번 1착·${r.challenger}번 2착 ${r.favoredExacta}배</span> <span class="hint">→ 인기마 ${r.favorite}번보다 <b style="color:#ff8a8a">${r.challenger}번을 1착으로 더 밀어줌</b>(비정상)</span>`); }
    // 유형별 목록
    const typeHtml = (inv.types || []).map((t) =>
      `<div style="margin:3px 0"><span class="chip" style="border-color:${kindColor[t.kind] || '#8a94a6'};color:${kindColor[t.kind] || '#ccc'}">${t.level} ${t.kind}</span> <span class="hint">${esc(t.text)}</span></div>`).join('');
    // [3번] 역배열 감지말 + 복승 역배열 조합(일반 추천과 구분)
    const invH = (inv.invHorses || []).map((h) => `<b style="color:#ff5c5c">${h}번</b>`).join(' · ');
    const invC = (inv.invCombos || []).map((c) => `<span class="chip chip-red">${c.combo.join('+')} <span class="hint">${c.odds}배</span></span>`).join(' ');
    const invBlock = (inv.invHorses && inv.invHorses.length) ? `<div style="margin:6px 0 2px;padding:6px 8px;background:rgba(255,92,92,.08);border-radius:6px">
      <div>⭐ <b style="color:#ff8a8a">역배열 감지말</b>: ${invH} <span class="hint">(배당 높아도 우선 노출)</span></div>
      ${invC ? `<div style="margin-top:4px"><span class="hint">복승 역배열</span> ${invC}</div>` : ''}</div>` : '';
    // [역배열 정확화] 인기순위 vs 쌍승 배당순위 역전(2단계+) 말 상세 + 대조마 + 실질 유력 요약
    const det = (inv.invDetail || []);
    const lead = inv.invLead;
    // [라벨 정확화] 배당 산출원(쌍승 1착방향 / 없으면 복승)을 그대로 표기 — 복승값을 '쌍승'이라 오표기하던 버그 수정.
    const oddsSrc = (inv.invSource && inv.invSource.oddsSrc) || '쌍승';
    const detBlock = det.length ? `<div style="margin:6px 0 2px;padding:7px 9px;background:rgba(168,85,247,.12);border-left:3px solid #a855f7;border-radius:6px">
      ${det.map((x) => `<div style="font-size:13px${x.lowest ? ';font-weight:700' : ''}">인기${x.popRank}위 <b style="color:#c084fc">${x.no}번</b> · ${oddsSrc} <b>${x.odds}배</b>${oddsSrc === '쌍승' ? '<span class="hint">(1착)</span>' : ''}${x.tag ? ` <span class="chip" style="border-color:#c084fc;color:#e9d5ff">${x.level} ${x.tag}${x.diffPct != null ? ' ' + x.diffPct + '%' : ''}</span>` : ''}${x.lowest ? ' <span style="color:#f0abfc">← 낮음</span>' : ''}</div>`).join('')}
      ${lead && lead.vs ? `<div style="font-size:13px">인기${lead.vs.popRank}위 <b>${lead.vs.no}번</b> · ${oddsSrc} <b>${lead.vs.odds}배</b></div>
      <div style="margin-top:3px;color:#e9d5ff">→ 인기 ${lead.popRank}위가 ${lead.vs.popRank}위보다 배당 낮음${lead.diffPct != null ? ` (배당 차이 ${lead.diffPct}%)` : ''}</div>` : ''}
      ${lead ? `<div style="margin-top:2px;color:#f0abfc;font-weight:700">→ ${lead.no}번 실질 유력!${lead.tag ? ` <span class="hint" style="font-weight:400">(${lead.level} ${lead.tag})</span>` : (lead.gap ? ` <span class="hint" style="font-weight:400">(순위 ${lead.gap}단계 역전)</span>` : '')}</div>` : ''}</div>` : '';
    return `<div style="margin:8px 0;padding:9px 11px;border:2px solid #ff5c5c;border-radius:8px;background:rgba(255,92,92,.1)">
      <div style="font-size:15px;font-weight:800;color:#ff8a8a">🔄 역배열 감지!</div>
      <div class="hint" style="margin:3px 0 5px;line-height:1.7">${lines.join('<br>')}<br>→ <b style="color:#ffd24f">실질 유력마가 바뀌었을 가능성</b></div>
      ${detBlock}${typeHtml}${invBlock}</div>${_strongUnpopularBlock(inv)}`;
  }

  // ── ⭐ 유력마 TOP5 + 복병/이상감지 상단 고정 카드 ─────────────────────
  //  [요청] 실시간 배당 이상감지 화면 최상단에 유력마 5두 + 복병/이상감지 2두를
  //  한 줄 요약(마번·마명·전적·배당·주요신호)으로 고정 표시. 30초 폴링마다
  //  renderTripleAnalyze가 재호출되어 자동 갱신. 순위 변동은 직전 순위와 비교해 강조.
  //  클릭 시 해당 말의 전체 분석 + 배당 타임라인(스냅샷 최저 복승) 펼침.
  //  ※ 기존 통합등급/제거분석 패널은 그대로 두고 상단 요약 카드만 추가.
  let _topRankPrev = {};          // { 마번: 순위 } 직전 순위(같은 경주 내)
  let _topRaceKey = null;
  let _lastTimelineSnaps = null;  // loadOddsTimeline이 저장 → 말별 배당 타임라인 재사용
  let _lastTopData = null;        // 클릭 상세용 { top:[], dark:[], byNo:{} }
  let _topAnalysisForClick = null;// 위임 클릭 핸들러가 참조할 최신 분석 dict
  let _topDelegationInstalled = false;
  const _topExpanded = new Set();

  function _horseSignalInfo(a, no) {
    // 해당 마번의 주요 신호(급락/집중급락/이상감지) 요약
    const drops = (a.drops || []).filter((d) => d.combo && (d.combo[0] === no || d.combo[1] === no) && d.pct < 0);
    let bigDrop = null;
    drops.forEach((d) => { if (!bigDrop || d.pct < bigDrop.pct) bigDrop = d; });
    const ex = a.signalQuality && a.signalQuality.excess && a.signalQuality.excess.horses
      ? a.signalQuality.excess.horses[no] : null;
    const isAnom = a.anomalyHorse != null && +a.anomalyHorse === no;
    return { bigDrop, ex, isAnom };
  }
  function _signalChips(info) {
    const c = [];
    if (info.isAnom) c.push('<span class="chip chip-red">🚨이상감지</span>');
    if (info.bigDrop) {
      const p = Math.abs(info.bigDrop.pct);
      c.push(`<span class="chip ${p >= 50 ? 'chip-red' : 'chip-yellow'}">${p >= 50 ? '🔴' : '🟠'} 급락 ${p}%</span>`);
    }
    if (info.ex && info.ex.grade) c.push(`<span class="chip ${info.ex.grade === '🔴' ? 'chip-red' : ''}">${info.ex.grade} 집중 ${info.ex.excess}%p</span>`);
    return c.join(' ');
  }
  // ── [1·2·4번] 경주 판정 크게 표시 + 배팅 금액 자동배분 + 쌍승 강신호 ─────────────
  //  실전 경주유형(확실/신중/애매/패스/대기)을 화면 최상단에 크게, 근거·확신도·예산배분과 함께.
  //  bsel=예산 입력 선택자(#jpBudget·#sportBudget-*) → 예산 × 배분비율로 금액 제안([2번]).
  function renderRaceJudgment(a, bsel) {
    const j = a.raceJudgment; if (!j) return '';
    const sg = a.stageGuide || {};
    const COL = { '확실형': '#38d39f', '신중형': '#ffd24f', '애매형': '#c084fc', '패스형': '#ef4444', 'wait': '#8a94a6' };
    const col = COL[j.type] || '#4ea1ff';
    let budget = 0;
    if (bsel) { const el = document.querySelector(bsel); budget = el ? (parseInt((el.value || '').replace(/[^0-9]/g, ''), 10) || 0) : 0; }
    const al = j.alloc || {};
    const won = (v) => v.toLocaleString('ko-KR') + '원';
    // [배팅 배분 실반영] 유형별 배분비율(main/sub/trio)을 실제 추천 조합(betRecommend)에 매핑.
    //   ⚠ betRecommend 로직은 읽기만(무수정). 조합·배당은 그대로 쓰고 금액만 유형별 비율로 제안.
    const br = a.betRecommend || [];
    const quins = br.filter((b) => b.kind === '복승');
    const trios = br.filter((b) => b.kind === '삼복승');
    const mainQ = quins.find((b) => (b.label || '').includes('메인')) || quins[0] || null;
    const subQs = quins.filter((b) => b !== mainQ).slice(0, 2);
    const trioPick = trios.slice(0, 3);
    const comboTxt = (b) => {
      if (!b) return '-';
      const o = b.expOdds != null ? `${b.expOdds}배` : (b.expOddsEst != null ? `${b.expOddsEst}배 추정` : '');
      return `${(b.combo || []).join('+')}${o ? ` (${o})` : ''}`;
    };
    const amt = (pct, n) => won(Math.round(budget * pct / 100 / Math.max(1, n)));
    let allocHtml;
    if (budget > 0 && (j.type === 'wait' || j.type === '패스형')) {
      allocHtml = '<div class="hint" style="margin-top:4px;color:#ef4444;font-weight:600">배팅 금액 0원 — 이번 경주 건너뛰세요</div>';
    } else if (budget > 0 && (al.main || al.sub || al.trio)) {
      const rows = [];
      if (al.main) rows.push(`<div>💰 복승 메인 ${mainQ ? `<b>${esc(comboTxt(mainQ))}</b>` : '<span class="hint">조합 대기</span>'} — ${al.main}% → <b style="color:${col}">${amt(al.main, 1)}</b></div>`);
      if (al.sub && subQs.length) subQs.forEach((b) => rows.push(`<div>복승 보조 <b>${esc(comboTxt(b))}</b> — ${al.sub}%${subQs.length > 1 ? ' 분할' : ''} → <b>${amt(al.sub, subQs.length)}</b></div>`));
      else if (al.sub) rows.push(`<div>복승 보조 ${al.sub}% → <b>${amt(al.sub, 1)}</b> <span class="hint">(조합 대기)</span></div>`);
      if (al.trio && trioPick.length) trioPick.forEach((b) => rows.push(`<div>삼복승 보험 <b>${esc(comboTxt(b))}</b> — ${al.trio}%${trioPick.length > 1 ? ' 분할' : ''} → <b>${amt(al.trio, trioPick.length)}</b></div>`));
      else if (al.trio) rows.push(`<div>삼복승 보험 ${al.trio}% → <b>${amt(al.trio, 1)}</b> <span class="hint">(조합 대기)</span></div>`);
      const exRow = j.exactaSignal ? `<div>⚡ 쌍승(강신호) <b>${esc((j.exactaSignal.combo || []).join('→'))}</b> — 소액 도전</div>` : '';
      allocHtml = `<div style="margin-top:6px;font-size:12px;line-height:1.75">
        <div style="font-weight:700;color:${col};margin-bottom:2px">🎯 핵심 추천 (3~4개)</div>
        ${rows.join('')}${exRow}<div class="hint" style="margin-top:2px">※ 총 예산 ${won(budget)} 기준 · 삼복승은 보험(소액) · 상세는 아래 추천표</div></div>`;
    } else {
      allocHtml = '<div class="hint" style="margin-top:4px">예산 입력 시 유형별 배분 금액이 표시됩니다</div>';
    }
    const exSig = j.exactaSignal
      ? `<div style="margin-top:6px;padding:6px 9px;background:rgba(168,85,247,.16);border-left:3px solid #a855f7;border-radius:6px;color:#d8b4fe;font-weight:700">${esc(j.exactaSignal.text)} <span class="hint" style="font-weight:400">(역전비 ${j.exactaSignal.ratio})</span></div>`
      : '';
    const stageBadge = sg.phase ? `<span class="chip" style="border-color:${col};color:${col}">${esc(sg.phase)}${sg.grade ? ' · ' + esc(sg.grade) : ''}</span>` : '';
    return `<div style="margin:0 0 10px;border:2px solid ${col};border-radius:12px;padding:11px 13px;background:linear-gradient(180deg,${col}22,rgba(20,28,43,.97));box-shadow:0 4px 16px rgba(0,0,0,.35)">
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        <span style="font-size:20px;font-weight:800;color:${col}">${j.emoji} ${esc(j.label)}</span>
        ${stageBadge}
        <span style="margin-left:auto;font-size:13px">확신도 <b style="color:${col};font-size:19px">${j.confidence}</b>점</span>
      </div>
      <div style="margin-top:5px;font-size:14px;font-weight:600">${esc(j.message)}</div>
      ${(j.reasons && j.reasons.length) ? `<div class="hint" style="margin-top:3px">근거: ${j.reasons.map(esc).join(' · ')}</div>` : ''}
      ${exSig}
      ${allocHtml}
      ${(sg.lines && sg.lines.length) ? `<div class="hint" style="margin-top:7px;border-top:1px solid rgba(255,255,255,.12);padding-top:5px">🕒 ${esc(sg.title || '')} — ${sg.lines.map(esc).join(' / ')}</div>` : ''}
    </div>`;
  }

  /** [혼전 경주] 상위 배당 근접 → 이변 가능성 → 고배당 포함 삼복승 전략(별도 배너, 기존 판정카드와 병행). */
  function renderChaotic(a, bsel) {
    const c = a.chaotic; if (!c || !c.detected) return '';
    // [추천 신중화] 신호 대기면 혼전 고배당 조합 추천도 보류(추천 조합 표시 안 함)
    if (a.raceJudgment && a.raceJudgment.type === 'wait') return '';
    const col = '#ff9f43';   // 혼전 = 주황 경고
    let budget = 0;
    if (bsel) { const el = document.querySelector(bsel); budget = el ? (parseInt((el.value || '').replace(/[^0-9]/g, ''), 10) || 0) : 0; }
    const won = (v) => v.toLocaleString('ko-KR') + '원';
    const comboTxt = (p) => {
      const o = p.expOdds != null ? `${p.expOdds}배` : (p.expOddsEst != null ? `${p.expOddsEst}배 추정` : '');
      return `${(p.combo || []).join('+')}${o ? ` (${o})` : ''}`;
    };
    const picks = c.picks || [];
    const rows = picks.map((p) => {
      const amtTxt = budget > 0 ? ` → <b style="color:${col}">${won(Math.round(budget * (p.alloc || 0) / 100))}</b>` : '';
      const hi = p.highReturn ? ' <span class="chip" style="border-color:#a855f7;color:#d8b4fe">고배당</span>' : '';
      const icon = p.kind === '복승' ? '💰' : '🎲';
      return `<div style="margin-top:2px">${icon} <b>${esc(p.label)}</b> ${esc(comboTxt(p))} — ${p.alloc}%${amtTxt}${hi}</div>`;
    }).join('');
    const totalAlloc = picks.reduce((s, p) => s + (p.alloc || 0), 0);
    return `<div style="margin:0 0 10px;border:2px solid ${col};border-radius:12px;padding:11px 13px;background:linear-gradient(180deg,${col}22,rgba(20,28,43,.97));box-shadow:0 4px 16px rgba(0,0,0,.35)">
      <div style="font-size:18px;font-weight:800;color:${col}">⚠️ 혼전 경주 감지</div>
      <div style="margin-top:3px;font-size:13px;font-weight:600">이변 가능성 있음 · 고배당 포함 삼복승 권장</div>
      ${c.reason ? `<div class="hint" style="margin-top:3px">감지 근거: ${esc(c.reason)}</div>` : ''}
      <div style="margin-top:6px;font-size:12px;line-height:1.7">
        <div style="font-weight:700;color:${col};margin-bottom:2px">🎯 혼전 전략 (고배당 비중↑)</div>
        ${rows || '<div class="hint">조합 대기</div>'}
        <div class="hint" style="margin-top:3px">${budget > 0 ? `※ 총 예산 ${won(budget)} 기준 (합 ${totalAlloc}%)` : '예산 입력 시 금액 자동계산'}${c.note ? ' · ' + esc(c.note) : ''}</div>
      </div>
    </div>`;
  }

  // [전적 과가중 해결] 저배당(5배↓) 시장 유력마 — 전적 미수집이어도 유력마 편입.
  function renderMarketFavorites(a) {
    const mf = ((a && a.marketFavorites) || []).filter((m) => m.formMissing);
    if (!mf.length) return '';
    return `<div style="margin:6px 0;padding:7px 10px;border-left:3px solid #38bdf8;background:rgba(56,189,248,.12);border-radius:6px">
      <div style="font-weight:800;color:#38bdf8">📊 시장 유력 (전적 미수집)</div>
      ${mf.map((m) => `<div class="hint" style="margin-top:2px;color:#7dd3fc">· <b>${m.no}번</b> 배당 ${m.odds}배 → 유력마 편입 <span style="color:#94a3b8">(배당이 낮아 시장이 유력하다고 판단)</span></div>`).join('')}
    </div>`;
  }

  // [3번] 실시간 유력마 추가 배너 — 초반 유력마 고정 후 급락/역배열 감지로 편입된 말.
  function renderRealtimeAdded(a) {
    const ra = (a && a.realtimeAdded) || [];
    if (!ra.length) return '';
    return `<div style="margin:6px 0;padding:7px 10px;border-left:3px solid #22c55e;background:rgba(34,197,94,.13);border-radius:6px">
      <div style="font-weight:800;color:#22c55e">⚡ 실시간 유력마 추가</div>
      ${ra.map((r) => `<div class="hint" style="margin-top:2px;color:#4ade80">· <b>${r.no}번</b> — ${esc(r.reason || '')} 감지 → 유력마·추천 조합에 추가</div>`).join('')}
    </div>`;
  }

  // [복병_집중급락 패턴] 집중급락 10회+/스마트머니 말 → 배당순위 무관 복병 자동 편입(마에바시 8R 학습).
  function renderDarkHorses(a) {
    const keys = new Set((a.keyHorses || []).map(Number));       // 유력마는 복병 아님(중복 제거)
    const dh = ((a && a.darkHorses) || []).filter((h) => !keys.has(Number(h.no))).slice(0, 4);
    if (!dh.length) return '';
    return dh.map((h) => {
      const stars = h.stars || (h.smartMoney ? 3 : h.forced ? 1 : 2);
      // ★★★ 최강=핑크 / ★★ 강함=보라 / ★ 참고=연보라
      const col = stars >= 3 ? '#f472b6' : stars === 2 ? '#c084fc' : '#a78bfa';
      const tierBadge = h.tierLabel ? `<span class="chip" style="border-color:${col};color:${col};font-weight:800">${esc(h.tierLabel)}</span>` : '';
      const sm = h.smartMoney ? '<span class="chip" style="border-color:#fbbf24;color:#fcd34d;font-weight:700">💰 스마트머니</span>' : '';
      const forced = h.forced ? `<span class="chip" style="border-color:#f472b6;color:#f472b6">🔥 집중급락 ${h.anomCount}회</span>` : '';
      const reason = h.tierReason ? `<span class="hint" style="margin-left:auto;color:${col}">${esc(h.tierReason)}</span>` : `<span class="hint" style="margin-left:auto;color:${col}">${esc(h.note || '')}</span>`;
      return `<div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding:5px 8px;border-radius:6px;margin:2px 0;background:rgba(244,114,182,.08);border-left:3px solid ${col}">
        <b style="min-width:30px;color:${col}">복병</b>
        <b style="min-width:32px;color:#4ea1ff">${h.no}번</b>
        <span class="hint"><b style="color:#e2e8f0">${h.oddsRepr != null ? h.oddsRepr + '배' : '배당-'}</b></span>
        ${tierBadge}${sm}${forced}
        ${reason}
      </div>`;
    }).join('');
  }

  // [복병 등급·2번] ★★★ 최강 복병(스마트머니) 감지 → 고배당 복승 강조 배너(유력1+복병 + 유사케이스)
  function renderDarkHighlight(a) {
    const dh = a && a.darkHighlight;
    if (!dh || !dh.quinella) return '';
    const cases = dh.cases > 0 ? `<span class="hint" style="color:#fcd34d;margin-left:8px">유사 복병 고배당 <b>${dh.cases}회</b> 적중</span>` : '';
    const odds = dh.quinellaOdds != null ? ` <b style="color:#ffd24f">(${dh.quinellaOdds}배)</b>` : (dh.oddsRepr != null ? ` <span class="hint">복병 ${dh.oddsRepr}배</span>` : '');
    return `<div style="margin:6px 0;padding:9px 12px;border:2px solid #f472b6;border-radius:9px;background:linear-gradient(90deg,rgba(244,114,182,.18),rgba(251,191,36,.12))">
      <div style="font-weight:900;color:#f9a8d4;font-size:15px">${esc(dh.message || '💰 스마트머니 복병 포함 → 고배당 가능!')}</div>
      <div style="font-weight:800;font-size:16px;margin-top:4px;color:#e2e8f0">복승: ${dh.quinella.join('+')}${odds}${cases}</div>
    </div>`;
  }

  // [유력마 통일] 복승 대표배당 낮은 순 + 이상감지 상위 노출 — TOP5·⭐유력마 라인 공통 정렬 기준.
  //   TOP5(확신도 기반)와 분석기 유력마(복승배당 기반)가 서로 다르던 혼란 해소.
  function _marketReprOdds(a) {
    const m = {};
    const eh = (a.elimination && a.elimination.horses) || [];
    eh.forEach((h) => { if (h.no != null && h.oddsRepr != null) m[Number(h.no)] = h.oddsRepr; });
    if (Array.isArray(a.quinella)) {   // 폴백/보강: 복승 배당에서 각 말 최저
      a.quinella.forEach((q) => {
        const c = q.combo || q.pair, o = q.odds;
        if (c && c.length === 2 && o > 0) c.forEach((n) => { const k = Number(n); if (m[k] == null || o < m[k]) m[k] = o; });
      });
    }
    return m;
  }
  // 이상감지 말은 배당 할인(강한 신호 ×0.5·약한 신호 ×0.7)으로 상위로 끌어올림 — 저배당 시장유력은 유지.
  function _marketEffOdds(a, no, reprMap) {
    const o = reprMap[Number(no)];
    if (o == null) return Infinity;    // 배당 미수집 → 최하위
    const info = _horseSignalInfo(a, no);
    let disc = 1.0;
    if (info.isAnom || (info.ex && info.ex.grade === '🔴') || (info.bigDrop && Math.abs(info.bigDrop.pct) >= 40)) disc = 0.5;
    else if (info.bigDrop || (info.ex && info.ex.grade)) disc = 0.7;
    // [배당 흐름 점수·4번] 저배당 순 → 흐름 점수 순: 흐름 좋은 말 상위·흐름 없는(죽은인기/상승) 말 하위.
    //   기존 시장배당·이상감지 할인은 유지하고 흐름 계수만 곱해 반영(무삭제).
    const fl = a.flowScores && (a.flowScores[Number(no)] || a.flowScores[String(no)]);
    if (fl && fl.score != null) {
      if (fl.score >= 30) disc *= 0.6;         // 스마트머니 → 최상위
      else if (fl.score >= 20) disc *= 0.75;   // 급락
      else if (fl.score >= 10) disc *= 0.9;    // 하락
      else if (fl.score <= -10) disc *= 1.4;   // 상승(자금 이탈) → 하위
      else if (fl.score < 0) disc *= 1.2;      // 무변동(죽은 인기) → 하위
    }
    return o * disc;
  }
  function _marketOrderNos(a, nos) {
    const reprMap = _marketReprOdds(a);
    return nos.slice().sort((x, y) => _marketEffOdds(a, x, reprMap) - _marketEffOdds(a, y, reprMap)
      || ((reprMap[Number(x)] == null ? 1e9 : reprMap[Number(x)]) - (reprMap[Number(y)] == null ? 1e9 : reprMap[Number(y)]))
      || (Number(x) - Number(y)));
  }

  // [배당 흐름 기반 제거·2/4번] 흐름 좋은 고배당 추천(💎) + 흐름 없는 말 제거(🔴).
  //   "들어올 말 찾기 → 안 들어올 말 제거". 서버 flowScores/flowRemoval/highOddsCandidates 소비.
  function renderFlowSection(a) {
    const hoc = (a && a.highOddsCandidates) || [];
    const rem = (a && a.flowRemoval) || [];
    const flow = (a && a.flowScores) || {};
    if (!hoc.length && !rem.length) return '';
    let html = '';
    if (hoc.length) {
      html += `<div class="matrix-title" style="font-size:13px;color:#f0abfc;margin-top:6px">💎 고배당 추천(흐름 기반) <span class="hint" style="font-weight:400">배당 높아도 하락/급락 흐름 → 삼복승 보험 편입</span></div>`;
      html += hoc.slice(0, 3).map((c) => {
        const f = flow[c.no] || flow[String(c.no)] || {};
        const sc = f.score != null ? f.score : c.score;
        return `<div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding:5px 8px;border-radius:6px;margin:2px 0;background:rgba(240,171,252,.08);border-left:3px solid #f0abfc">
          <b style="min-width:30px;color:#f0abfc">💎추천</b>
          <b style="min-width:32px;color:#4ea1ff">${c.no}번</b>
          <span class="hint"><b style="color:#e2e8f0">${c.odds}배</b></span>
          <span class="chip" style="border-color:#f0abfc;color:#f0abfc;font-weight:700">흐름점수 ${sc != null ? sc : '-'}점</span>
          <span class="chip" style="border-color:#f472b6;color:#f472b6">${esc(c.trend || '')}</span>
          ${f.smartMoney ? '<span class="chip" style="border-color:#fbbf24;color:#fcd34d;font-weight:700">💰 스마트머니</span>' : ''}
          <span class="hint" style="margin-left:auto;color:#f0abfc">→ 삼복승 보험 포함</span>
        </div>`;
      }).join('');
    }
    if (rem.length) {
      html += `<div class="matrix-title" style="font-size:13px;color:#f87171;margin-top:6px">🔴 제거(흐름 없음) <span class="hint" style="font-weight:400">저배당이어도 흐름 없으면 제거 · 죽은인기/연속상승/페이크/역배열반대</span></div>`;
      html += rem.slice(0, 5).map((r) => `<div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding:4px 8px;font-size:12px;border-radius:6px;margin:2px 0;background:rgba(248,113,113,.06);border-left:3px solid #f87171">
          <b style="min-width:30px;color:#f87171">🔴제거</b>
          <b style="min-width:32px">${r.no}번</b>
          <span class="hint">${r.rep != null ? r.rep + '배' : '배당-'}</span>
          <span class="chip" style="border-color:#f87171;color:#f87171">흐름점수 ${r.score != null ? r.score : '-'}점</span>
          <span class="hint" style="margin-left:auto;color:#f87171">${(r.reasons || []).map(esc).join(', ')}</span>
        </div>`).join('');
    }
    return html;
  }

  function renderTopHorses(a) {
    _topAnalysisForClick = a;       // 클릭 상세/타임라인용 최신 분석 보관
    _ensureTopHorseDelegation();    // 클릭 위임 1회 설치(다중 패널 안전)
    // 순위 데이터 소스: 제거분석(가장 풍부) → 없으면 전적+배당유력마 폴백
    let horses = [];
    const eh = a.elimination && a.elimination.horses ? a.elimination.horses : null;
    if (eh && eh.length) {
      horses = eh.map((h) => ({
        no: h.no, name: h.name || '', formScore: h.formScore, odds: h.oddsRepr,
        prob: h.combinedProb, total: h.total, tier: h.tier, ev: h.ev, favScore: h.favScore,
        insuranceDemote: h.insuranceDemote, dualConverge: h.dualConverge,
        formScoreAdj: h.formScoreAdj, marketFavorite: h.marketFavorite,
      }));
    } else {
      const fmap = {}; (a.form || []).forEach((h) => { fmap[h.no] = h; });
      const keys = (a.keyHorses || []).map(Number);
      const nos = Array.from(new Set([...(a.form || []).map((h) => h.no), ...keys]));
      horses = nos.map((no) => {
        const f = fmap[no];
        return {
          no, name: f ? (f.name || '') : '', formScore: f ? f.totalScore : null, odds: null,
          prob: null, total: keys.includes(no) ? 1 : 0, tier: keys.includes(no) ? '★' : null,
        };
      });
    }
    // [잔존마 필터·2번] 서버가 준 현재 배당 등장 마번(validHorses)에 없는 잔존마(이전 경주 말)는 유력마 TOP5에서 제외.
    //   validHorses가 비어있으면(배당 미수집) 필터 안 함(오검출 방지). 매칭이 전무하면 원본 유지(안전가드).
    if (Array.isArray(a.validHorses) && a.validHorses.length && horses.length) {
      const vset = new Set(a.validHorses.map(Number));
      const filtered = horses.filter((h) => vset.has(Number(h.no)));
      if (filtered.length && filtered.length !== horses.length) {
        console.log('[잔존마 필터] TOP5: 배당 없는 마번 ' + (horses.length - filtered.length) + '두 제외');
      }
      if (filtered.length) horses = filtered;
    }
    if (!horses.length) return '';
    // 확신도(있으면)는 칩 표시용으로만 보관 — 정렬 기준으로는 쓰지 않는다(유력마 라인과 통일).
    const confMap = (a.confidence && a.confidence.horses) ? a.confidence.horses : null;
    if (confMap) horses.forEach((h) => { const c = confMap[h.no] || confMap[String(h.no)]; if (c) { h.conf = c.confidence; h.band = c.band; } });
    // [유력마 통일] 정렬 기준 = 복승 대표배당 낮은 순(시장 기준) + 이상감지 상위 노출.
    //   분석기 ⭐유력마 라인과 동일 기준 → 두 화면이 어긋나던 혼란 제거.
    const _repr = _marketReprOdds(a);
    horses.sort((x, y) => _marketEffOdds(a, x.no, _repr) - _marketEffOdds(a, y.no, _repr)
      || ((x.odds == null ? 1e9 : x.odds) - (y.odds == null ? 1e9 : y.odds)) || (x.no - y.no));
    const top = horses.slice(0, 5);

    // 복병/이상감지 2두: TOP5 밖 + (이상감지·급락·집중신호) 보유, 신호강도순
    const topNos = new Set(top.map((h) => h.no));
    const rest = horses.filter((h) => !topNos.has(h.no));
    const scored = rest.map((h) => {
      const info = _horseSignalInfo(a, h.no);
      let s = 0;
      if (info.isAnom) s += 100;
      if (info.bigDrop) s += Math.abs(info.bigDrop.pct);
      if (info.ex && info.ex.grade) s += (info.ex.grade === '🔴' ? 50 : 20) + Math.abs(info.ex.excess || 0);  // 초과급락은 음수일수록 강함 → 크기로 반영
      return { h, info, s };
    }).filter((x) => x.s > 0).sort((x, y) => y.s - x.s);
    let dark = scored.slice(0, 2);
    // 신호 복병이 2두 미만이면 TOP5 밖 최고배당(시장 저평가=잠재복병)으로 채워 항상 2두 표시
    if (dark.length < 2) {
      const used = new Set(dark.map((d) => d.h.no));
      const fillers = rest.filter((h) => !used.has(h.no) && h.odds != null)
        .sort((x, y) => (y.odds || 0) - (x.odds || 0))
        .slice(0, 2 - dark.length)
        .map((h) => ({ h, info: _horseSignalInfo(a, h.no), s: 0, filler: true }));
      dark = dark.concat(fillers);
    }

    // 순위 변동(같은 경주 내에서만 비교)
    if (a.raceKey !== _topRaceKey) { _topRankPrev = {}; _topRaceKey = a.raceKey; _topExpanded.clear(); }
    const curRank = {}; top.forEach((h, i) => { curRank[h.no] = i + 1; });

    _lastTopData = { top, dark, byNo: {} };
    top.forEach((h) => { _lastTopData.byNo[h.no] = { h, info: _horseSignalInfo(a, h.no), rank: curRank[h.no] }; });
    dark.forEach((d) => { _lastTopData.byNo[d.h.no] = { h: d.h, info: d.info, dark: true }; });

    const rowHtml = (h, rank, info, isDark, isFiller) => {
      const prev = _topRankPrev[h.no];
      let move = '';
      if (!isDark) {
        if (prev == null) move = '<span class="chip" style="border-color:#38d39f;color:#38d39f">🆕 신규</span>';
        else if (prev > rank) move = `<span class="chip" style="border-color:#38d39f;color:#38d39f;font-weight:700">↑ ${prev}위→${rank}위 상승</span>`;
        else if (prev < rank) move = `<span class="chip" style="border-color:#ff9f43;color:#ff9f43">↓ ${prev}위→${rank}위</span>`;
      }
      const rankBadge = isDark ? '<b style="color:#c084fc;min-width:30px;display:inline-block">복병</b>'
        : `<b style="color:#ffd24f;min-width:30px;display:inline-block">${rank}위</b>`;
      const oddsTxt = h.odds != null ? `${h.odds}배` : '미수집';
      const formTxt = h.formScore != null ? `전적 ${h.formScore}` : '전적-';
      const BAND_COL = { '강력': '#38d39f', '주목': '#ffd24f', '관찰': '#c084fc', '약함': '#8a94a6' };
      const confChip = (!isDark && h.conf != null)
        ? `<span class="chip" style="border-color:${BAND_COL[h.band] || '#4ea1ff'};color:${BAND_COL[h.band] || '#4ea1ff'}">확신 ${h.conf}</span>` : '';
      const sig = _signalChips(info) || (isFiller
        ? '<span class="chip" style="border-color:#c084fc;color:#c084fc">🔎 고배당 복병</span>'
        : '<span class="hint">신호없음</span>');
      // [유력마 통일] 🚨 이상감지 말 구분 — TOP5에 포함하되 별도 표시(급락/집중신호 보유마).
      const _isAnom = !!(info && (info.isAnom || info.bigDrop || (info.ex && info.ex.grade)));
      const anomChip = (!isDark && _isAnom)
        ? '<span class="chip" style="border-color:#ff5c5c;color:#ff5c5c;font-weight:700">🚨 이상감지</span>' : '';
      // [배당 흐름 점수·3번] 말별 흐름점수 칩(상승-10/무변동-5/하락+10/급락+20/스마트머니+30).
      let flowChip = '';
      const _fl = (a.flowScores && (a.flowScores[h.no] || a.flowScores[String(h.no)]));
      if (_fl && _fl.score != null) {
        const _fc = _fl.score >= 20 ? '#38d39f' : (_fl.score > 0 ? '#4ea1ff' : (_fl.score <= -10 ? '#f87171' : '#94a3b8'));
        flowChip = `<span class="chip" style="border-color:${_fc};color:${_fc}" title="배당 흐름: ${esc(_fl.trend || '')}">흐름 ${_fl.score >= 0 ? '+' : ''}${_fl.score}점 ${esc(_fl.trend || '')}</span>`;
      }
      // [배당 우선 전환·3번] 배당 기반 상태 배지
      let mktChip = '';
      let warnLine = '';
      if (!isDark) {
        if (h.dualConverge) {
          mktChip = '<span class="chip" style="border-color:#38d39f;color:#38d39f;font-weight:700">💥 이중수렴 강력추천</span>';
        } else if (h.marketFavorite) {
          mktChip = '<span class="chip" style="border-color:#4ea1ff;color:#4ea1ff">📊 시장 유력(저배당)</span>';
        }
        // 배당 20배+ 말이 TOP5 상위 = 전적 우수하나 시장 비인기 → 고배당 보험으로만 활용 경고
        if (h.insuranceDemote || (h.odds != null && h.odds >= 20)) {
          mktChip += '<span class="chip" style="border-color:#ff9f43;color:#ff9f43;font-weight:700">⚠️ 고배당 보험용</span>';
          warnLine = `<div class="hint" style="width:100%;margin:2px 0 0;color:#ff9f43">⚠️ 전적 우수하나 시장 비인기 · 고배당 보험으로만 활용 권장</div>`;
        }
      }
      const open = _topExpanded.has(h.no);
      return `<div class="top-horse-row" data-no="${h.no}" title="클릭 → 상세+배당 타임라인" style="cursor:pointer;display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding:5px 8px;border-radius:6px;margin:2px 0;background:rgba(255,255,255,${isDark ? '.02' : '.05'});border-left:3px solid ${isDark ? '#c084fc' : (h.dualConverge ? '#38d39f' : (warnLine ? '#ff9f43' : '#ffd24f'))}">
        ${rankBadge}
        <b style="min-width:32px;color:#4ea1ff">${h.no}번</b>
        <span style="font-weight:600">${esc(h.name) || '-'}</span>
        <span class="hint">${formTxt} · <b style="color:#e2e8f0">${oddsTxt}</b></span>
        ${anomChip}
        ${flowChip}
        ${mktChip}
        ${confChip}
        ${move}
        <span style="margin-left:auto">${sig}</span>
        ${warnLine}
      </div>
      <div id="top-detail-${h.no}" style="display:${open ? 'block' : 'none'}"></div>`;
    };

    const topRows = top.map((h) => rowHtml(h, curRank[h.no], _horseSignalInfo(a, h.no), false)).join('');
    const darkRows = dark.length
      ? dark.map((d) => rowHtml(d.h, null, d.info, true, d.filler)).join('')
      : '<div class="hint" style="padding:4px 8px">복병·이상감지 신호 없음</div>';

    _topRankPrev = curRank;   // 다음 갱신 비교용(행 생성 후 저장)

    return `<div id="topHorsesCard" style="position:relative;margin:0 0 8px;border:2px solid #ffd24f;border-radius:10px;padding:8px 10px;background:linear-gradient(180deg,rgba(255,210,79,.12),rgba(20,28,43,.97));box-shadow:0 4px 16px rgba(0,0,0,.4)">
      <div class="matrix-title" style="font-size:14px;color:#ffd24f">⭐ 시장 유력마 TOP 5 <span class="hint" style="font-weight:400">복승배당 기준 · 이상감지 상위 · 30초 자동갱신 · 클릭 시 상세</span></div>
      ${renderMarketFavorites(a)}
      ${renderRealtimeAdded(a)}
      ${topRows}
      <div class="matrix-title" style="font-size:13px;color:#c084fc;margin-top:6px">🐎 복병 · 이상감지 <span class="hint" style="font-weight:400">유력마 밖 강한 신호 · 집중급락/스마트머니 자동 편입</span></div>
      ${renderDarkHorses(a)}
      ${darkRows}
      ${(a.eliminationStrong && a.eliminationStrong.length) ? `
      <div class="matrix-title" style="font-size:13px;color:#ef4444;margin-top:6px">🚫 제거마 <span class="hint" style="font-weight:400">과감히 제외 ${a.eliminationStrong.length}두</span></div>
      ${a.eliminationStrong.map((e) => `<div style="display:flex;gap:6px;align-items:center;padding:3px 8px;font-size:12px;opacity:.85">
        <b style="min-width:26px;color:#ef4444">${e.verdict || '🔴'}</b>
        <b style="min-width:32px">${e.no}번</b>
        <span>${esc(e.name) || '-'}</span>
        <span class="hint" style="margin-left:auto;text-align:right">${e.odds != null ? e.odds + '배' : ''}${(e.reasons && e.reasons.length) ? ' · ' + e.reasons.map(esc).join(', ') : ''}</span>
      </div>`).join('')}` : ''}
      ${renderFlowSection(a)}
    </div>`;
  }
  function _topHorseDetailHtml(no, a) {
    const d = _lastTopData && _lastTopData.byNo[no];
    const h = d ? d.h : { no };
    const info = d ? d.info : _horseSignalInfo(a, no);
    const fmap = {}; (a.form || []).forEach((f) => { fmap[f.no] = f; });
    const f = fmap[no];
    const rows = [];
    if (h.formScore != null) rows.push(`전적점수 <b>${h.formScore}</b>`);
    if (f && f.grade) rows.push(`전적등급 <b>${f.grade}</b>`);
    if (f && (f.recentPlacings || []).length) rows.push(`최근착순 ${f.recentPlacings.join('-')}`);
    if (f && f.jockey) rows.push(`기수 ${esc(f.jockey)}`);
    if (h.odds != null) rows.push(`대표배당 <b>${h.odds}배</b>`);
    if (h.prob != null) rows.push(`통합확률 <b>${h.prob}%</b>`);
    if (h.favScore != null) rows.push(`유력점수 ${h.favScore}`);
    if (h.ev != null) rows.push(`기대값 <b style="color:${h.ev >= 0 ? '#38d39f' : '#ff6b6b'}">${h.ev >= 0 ? '+' : ''}${h.ev}%</b>`);
    const flags = f && (f.flags || []).length ? f.flags.map((x) => `<span class="chip ${x.level === 'must' ? 'chip-red' : ''}">${esc(x.msg)}</span>`).join(' ') : '';
    const sig = _signalChips(info) || '<span class="hint">신호없음</span>';
    return `<div style="margin:2px 0 8px 6px;padding:8px 10px;border-left:2px solid #4ea1ff;background:rgba(78,161,255,.06);border-radius:6px">
      <div style="font-size:12px;margin-bottom:4px">${rows.join(' · ') || '<span class="hint">상세 정보 없음</span>'}</div>
      <div style="margin:3px 0">신호: ${sig}</div>
      ${flags ? `<div style="margin:3px 0">${flags}</div>` : ''}
      <div class="hint" style="margin-top:4px">📉 배당 타임라인(이 말 포함 최저 복승)</div>
      <div class="top-detail-tl hint" style="font-size:11px;line-height:1.9">불러오는 중…</div>
    </div>`;
  }
  async function _horseOddsTimeline(raceKey, no) {
    let snaps = _lastTimelineSnaps;
    if (!snaps) {
      try {
        const d = await (await fetch('/api/history/get', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ raceKey }) })).json();
        snaps = (d && d.snapshots) || [];
      } catch (_) { snaps = []; }
    }
    if (!snaps.length) return '타임라인 데이터 없음';
    const pts = snaps.map((s) => {
      const q = s.quinella || {};
      let mn = null;
      Object.entries(q).forEach(([k, v]) => {
        const pr = String(k).split(/[-+]/).map(Number);
        if (pr.includes(no) && (mn == null || v < mn)) mn = v;
      });
      return { t: s.time || '', v: mn };
    }).filter((p) => p.v != null);
    if (!pts.length) return '이 말이 포함된 복승 배당 없음';
    return pts.map((p, i) => {
      let arrow = '';
      if (i > 0 && pts[i - 1].v != null) {
        const dv = p.v - pts[i - 1].v;
        arrow = dv < 0 ? `<span style="color:#ef4444">▼${Math.abs(dv).toFixed(1)}</span>` : dv > 0 ? `<span style="color:#38d39f">▲${dv.toFixed(1)}</span>` : '';
      }
      return `<span style="margin-right:8px;white-space:nowrap">${esc(p.t)} <b>${p.v}</b>${arrow ? ' ' + arrow : ''}</span>`;
    }).join('');
  }
  // 클릭 위임: 어느 패널(#jpIntegrated·#sportReport-*)의 TOP5 카드든 1개 핸들러로 처리.
  //  행의 다음 형제(#top-detail-N)를 상세 컨테이너로 사용해 패널이 여러 개여도 정확히 토글.
  function _ensureTopHorseDelegation() {
    if (_topDelegationInstalled) return;
    _topDelegationInstalled = true;
    document.addEventListener('click', async (ev) => {
      const row = ev.target.closest ? ev.target.closest('.top-horse-row') : null;
      if (!row || !row.closest('#topHorsesCard')) return;
      const box = row.nextElementSibling;
      if (!box || !box.id || box.id.indexOf('top-detail-') !== 0) return;
      const no = +row.dataset.no;
      const a = _topAnalysisForClick || {};
      if (box.style.display === 'block') { box.style.display = 'none'; _topExpanded.delete(no); return; }
      _topExpanded.add(no); box.style.display = 'block';
      box.innerHTML = _topHorseDetailHtml(no, a);
      const tl = await _horseOddsTimeline(a.raceKey, no);
      const tlEl = box.querySelector('.top-detail-tl'); if (tlEl) tlEl.innerHTML = tl;
    });
  }

  // [추천 근거 상세 카드] 상위 추천마 3두별 전적·배당·기수·종합확신도 근거
  function renderRecommendBasis(rb) {
    if (!rb || !(rb.cards || []).length) return '';
    const bw = rb.basisWeights || {};
    const topLabel = { form: '전적', anomaly: '배당', jockey: '기수' };
    const cards = rb.cards.map((c) => {
      const f = c.form || {}, o = c.odds || {}, jk = c.jockey || {}, cf = c.confidence || {};
      const recent = (f.recent || []).length ? f.recent.join('-') : '미수집';
      const op = [];
      if (o.drop != null) op.push(`급락 ▼${Math.abs(o.drop)}%`);
      if (o.reversal) op.push(`쌍승역전(vs ${o.reversal.favorite}번)`);
      if (o.streak) op.push(`연속하락 ${o.streak}회${o.streakLabel ? `(${o.streakLabel})` : ''}`);
      const oddsTxt = op.length ? op.join(' · ') : '뚜렷한 배당 신호 없음';
      const seriesTxt = (o.series && o.series.length > 1) ? ` <span class="hint">[${o.series.join('→')}]</span>` : '';
      const reasons = (cf.reasons || []).join(' · ');
      const ccol = cf.score >= 70 ? '#38d39f' : (cf.score >= 40 ? '#ffd24f' : '#8a94a6');
      return `<div class="bet-box" style="display:block;margin:5px 0">
        <div style="font-weight:800;font-size:14px">${c.rank}위 <b style="color:#4ea1ff">${c.no}번</b>${c.name ? ` ${esc(c.name)}` : ''}${cf.score != null ? `<span style="float:right;color:${ccol};font-weight:800">확신도 ${cf.score}${cf.grade ? ` ${cf.grade}` : ''}</span>` : ''}</div>
        <div style="font-size:12px;margin-top:3px">🏇 <b>전적</b>: 점수 ${f.score != null ? f.score : '-'} · 최근5착 ${recent}${f.avgPlacing != null ? ` · 평균 ${f.avgPlacing}착` : ''} <span class="hint">(당거리·주로·날씨별: 미수집)</span></div>
        <div style="font-size:12px;margin-top:2px">📉 <b>배당</b>: ${oddsTxt}${seriesTxt}</div>
        <div style="font-size:12px;margin-top:2px">🧑‍💼 <b>기수</b>: ${jk.name ? esc(jk.name) : '-'}${jk.placeRate != null ? ` · 복승률 ${jk.placeRate}%` : ' · 복승률 미상'} <span class="hint">(조합성적: 미수집)</span></div>
        ${reasons ? `<div class="hint" style="font-size:11px;margin-top:2px">→ 확신도 근거: ${esc(reasons)}</div>` : ''}
      </div>`;
    }).join('');
    const bwTxt = bw.top ? ` <span class="hint" style="font-weight:400">· 가장 신뢰 근거: ${topLabel[bw.top] || bw.top}</span>` : '';
    return `<div style="margin:8px 0;padding:9px 11px;border:2px solid #6366f1;border-radius:8px;background:rgba(99,102,241,.08)">
      <div style="font-size:15px;font-weight:800;color:#a5b4fc">📋 추천 근거 (상위 ${rb.cards.length}두)${bwTxt}</div>
      ${cards}
      <div class="hint" style="font-size:11px;margin-top:4px">${esc(rb.dataNote || '')}</div></div>`;
  }

  function renderTripleAnalyze(a) {
    const el = $('#tripleAnalyzeReport'); if (!el) return;
    _lastTripleAnalyze = a;
    // [수집 조기 중단 방어] 서버가 수집 중단(발주 전 2분+ 미수집) 감지 시 → 확장에 즉시 재수집 릴레이(30초 1회 throttle).
    if (a.collectionStalled) {
      const _now = Date.now();
      if (!window._lastStallNudge || _now - window._lastStallNudge > 30000) {
        window._lastStallNudge = _now;
        try { nudgeExtensionCollect(); } catch (_) { /* */ }
        console.log('[수집중단] 발주 전 미수집 감지 → 확장 재수집 트리거');
      }
    }
    if (a.raceKey !== _elimRaceKey) { _elimToggle.clear(); _elimRaceKey = a.raceKey; } // 경주 바뀌면 수동 토글 초기화
    // [5번] 추천 조합 변경 감지
    const betKey = JSON.stringify((a.betRecommend || []).map((r) => r.combo));
    _betUpdatedFlag = (_prevBetKey !== null && betKey !== _prevBetKey);
    _prevBetKey = betKey;
    const drops = (a.drops || []).slice(0, 8).map((d) =>
      `<span class="chip ${d.pct < 0 ? 'chip-red' : 'chip-yellow'}">${d.combo[0]}-${d.combo[1]} ${d.prev}→${d.cur} ${d.pct < 0 ? '▼' : '▲'}${Math.abs(d.pct)}%</span>`).join(' ');
    // 단발 flip(🔴) + 누적 recentFlip(🟠 마감임박 서서히 뒤집힘) 모두 표시 → 마감 직전 역전 놓치지 않음
    const flips = (a.reversals || []).filter((r) => r.flipped || r.recentFlip).slice(0, 6).map((r) =>
      `<span class="chip ${r.flipped ? 'chip-red' : 'chip-yellow'}" title="${r.flipped ? '직전 대비 방향 전환' : '누적(서서히) 역전 — 마감 임박'}">${r.flipped ? '🔴' : '🟠누적'} ${r.favored[0]}→${r.favored[1]} (${r.favoredOdds}&lt;${r.otherOdds})</span>`).join(' ');
    const ranks = (a.rankChanges || []).slice(0, 6).map((r) =>
      `<span class="chip">${r.combo[0]}-${r.combo[1]} ${r.prevRank}위→${r.curRank}위 (${r.delta > 0 ? '▲' : '▼'}${Math.abs(r.delta)})</span>`).join(' ');
    // [유력마 통일] ⭐유력마 라인도 TOP5와 동일 기준(복승 대표배당 낮은 순 + 이상감지 상위)으로 정렬 표시.
    const keyH = _marketOrderNos(a, (a.keyHorses || []).map(Number)).map((h) => `<b style="color:#4ea1ff">${h}</b>`).join(' · ');
    el.innerHTML = `
      ${renderCorePicks(a)}
      ${renderDarkHighlight(a)}
      ${renderTopHorses(a)}
      ${renderBmedMatrixPanel(a)}
      <div class="matrix-title">🚨 이상감지 ${a.sport && a.sport !== 'horse' ? `<span class="chip" style="border-color:#a855f7;color:#c4b5fd">${a.sport === 'cycle' ? '🚴 경륜' : '🚤 경정'}</span> ` : ''}<span class="hint" style="font-weight:400">${esc(a.raceKey)} · ${a.baselineReset ? '⚠️ 기준값 재설정됨' : a.baselineSet ? '🎯 기준값 설정됨' : a.hasPrev ? '직전 대비' : '첫 수집(변동 없음)'}${a.minutesBefore != null && !a.afterClose ? ` · 마감 ${a.minutesBefore}분전` : ''}</span></div>
      ${a.baselineReset ? `<div style="margin:6px 0;padding:7px 9px;border-left:3px solid #ffd24f;background:rgba(255,210,79,.12);border-radius:6px;color:#ffd24f">⚠️ <b>비정상 변동폭 감지 → 기준값 재설정</b> — 이전 경주 배당 잔존 의심(95%+ 급락 다수). 이번 수집을 새 기준값으로 설정했습니다. <b>다음 수집부터 변동을 계산</b>합니다.</div>`
        : a.baselineSet ? `<div style="margin:6px 0;padding:7px 9px;border-left:3px solid #38bdf8;background:rgba(56,189,248,.1);border-radius:6px;color:#7dd3fc">🎯 <b>기준값 설정됨</b> — 새 경주 첫 수집입니다. 변동폭은 <b>다음 수집부터</b> 계산됩니다.</div>` : ''}
      ${a.afterClose ? `<div style="margin:6px 0;padding:7px 9px;border-left:3px solid #8a94a6;background:rgba(138,148,166,.14);border-radius:6px;color:#b8c0cc">⚠️ <b>마감 후 수집</b> — 발주(T-0) 이후 신호는 <b>참고만</b> 하세요. 급락이 있어도 <b>추천 조합·보험에는 반영되지 않습니다</b>(마감 전 기준 유지).</div>` : ''}
      ${a.deadlineCorrected ? `<div style="margin:6px 0;padding:7px 9px;border-left:3px solid #38bdf8;background:rgba(56,189,248,.12);border-radius:6px;color:#7dd3fc">🛠️ <b>발주시각 오검출 정정</b> — 발주시각이 뒤로 이동(예: T-1분→T-7분)해 이전의 잘못된 <b>마감 판정을 무효화</b>하고 올바른 발주시각으로 재설정했습니다. 실시간 급락/역배열 편입이 정상 재개됩니다.</div>` : ''}
      ${a.centralClosing ? `<div style="margin:6px 0;padding:8px 10px;border-left:4px solid #f87171;background:rgba(220,38,38,.16);border-radius:6px;color:#fecaca;font-weight:800">⚠️ 중앙경마 배당판 T-2분에 닫힘 — <span style="color:#fca5a5">지금이 마지막 신호!</span><div class="hint" style="font-weight:400;margin-top:2px;color:#fca5a5">JRA는 실제 발주 2분 전에 배당판이 닫힙니다. T-2분을 실질 마감으로 보고 지금 데이터로 추천을 확정하세요.</div></div>` : ''}
      ${a.collectionStalled ? `<div style="margin:6px 0;padding:8px 10px;border-left:4px solid #fbbf24;background:rgba(245,158,11,.16);border-radius:6px;color:#fcd34d;font-weight:800">⚠️ 수집 중단 감지 — 자동 재수집 시도 중...<div class="hint" style="font-weight:400;margin-top:2px;color:#fcd34d">발주 전인데 ${a.secsSinceCollect != null ? Math.floor(a.secsSinceCollect / 60) + '분 ' + (a.secsSinceCollect % 60) + '초' : '2분+'} 동안 배당이 갱신되지 않았습니다. 배당판 탭을 확인하세요(닫혔으면 다시 열기).</div></div>` : ''}
      ${renderForcedTrifecta(a)}
      ${renderReversalBacking(a)}
      ${renderPreReversal(a)}
      ${renderAfterCloseSurge(a.afterCloseSurge)}
      ${a.marketCheck && a.marketCheck.diverged ? `<div style="margin:6px 0;padding:7px 9px;border-left:3px solid #ff5c5c;background:rgba(255,92,92,.12);border-radius:6px;color:#ff8a8a">⚠️ <b>배당판 불일치</b> — 추천 복승(${(a.marketCheck.mainPair || []).join('+')}=${a.marketCheck.mainOdds}배)이 <b>배당판 최저 인기 조합(${a.marketCheck.favPair.join('+')}=${a.marketCheck.favOdds}배)</b>과 다릅니다. 배당판을 초반에 못 끌어왔거나 전적 편중일 수 있어요 → <b>배당판 인기 조합을 추천에 추가</b>했습니다. 배당 재확인 권장.</div>` : ''}
      ${a.marketCheck && a.marketCheck.stale ? `<div style="margin:6px 0;padding:7px 9px;border-left:3px solid #ffb020;background:rgba(255,176,32,.12);border-radius:6px;color:#ffc862">⚠️ <b>배당 불안정</b> — 최저 복승도 ${a.marketCheck.favOdds}배(실자금 미형성/초반 미수집 의심). <b>배당판 새로고침 후 재수집</b> 권장. 현재 추천은 참고만.</div>` : ''}
      ${renderAlertSignal(a.alertSignal, _horseRoleMap(a))}
      ${renderInverse(a.inverse)}
      ${renderCrossReversal(a)}
      ${renderSignalTimeline(a.signalTimeline)}
      <div style="font-size:15px;font-weight:700;margin:6px 0;color:#ffd24f">${esc(a.summary || '')}</div>
      ${drops ? `<div style="margin:6px 0"><span class="hint">📉 급락/변동</span><br>${drops}</div>` : ''}
      ${flips ? `<div style="margin:6px 0"><span class="hint">🔀 쌍승 역전</span><br>${flips}</div>` : ''}
      ${ranks ? `<div style="margin:6px 0"><span class="hint">📊 순위 변동</span><br>${ranks}</div>` : ''}
      <div style="margin:6px 0"><span class="hint">⭐ 유력마</span> ${keyH || '—'}${a.anomalyHorse != null ? ` <span class="hint">/ 이상감지말</span> <b style="color:#ff5c5c">${a.anomalyHorse}</b>` : ''}</div>
      ${renderIntegratedGrades(a)}
      ${renderSignalQuality(a.signalQuality)}
      ${renderEliminationHTML(a.elimination)}
      ${renderRecommendBasis(a.recommendBasis)}
      ${renderSingleFavorite(a)}
      ${renderRecommendFlex(a)}
      ${renderBetRecommend(a)}
      ${renderHighOddsCompanion(a)}
      ${(a.raceJudgment && a.raceJudgment.type === 'wait') ? '' : renderBMED(a.bmed)}
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
    catch (e) { el.innerHTML = ''; _lastTimelineSnaps = null; return; }   // 조회 실패 시 이전 경주 타임라인 잔상 제거
    if (!d || d.error || !(d.snapshots || []).length) { el.innerHTML = ''; _lastTimelineSnaps = null; return; }
    if (raceKey !== _tlRaceKey) { _tlExpanded.clear(); _tlRaceKey = raceKey; }
    const snaps = d.snapshots;
    _lastTimelineSnaps = snaps;   // ⭐ TOP5 카드 말별 배당 타임라인 재사용
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
    // [보완1·경륜] 競走得点 절대등급(95+A/85+B/75+C/<75D)이 있으면 별도 컬럼 표시(통합 사분위 등급과 함께).
    const hasAbs = form.some((h) => h.absGrade);
    const isKeirin = form.some((h) => h.styleType);
    const rows = form.map((h) => `<tr>
      <td><b style="color:${gc[h.grade] || '#fff'}">${h.grade}</b></td>
      ${hasAbs ? `<td><b style="color:${gc[h.absGrade] || '#8a94a6'}">${esc(h.absGrade || '-')}</b>${h.competScore != null ? ` <span class="hint" style="font-size:10px">${h.competScore}</span>` : ''}</td>` : ''}
      <td>${h.no}</td><td>${esc(h.name || '')}</td>${isKeirin ? `<td>${esc(h.styleType || '-')}</td>` : `<td>${esc(h.jockey || '')}</td>`}
      <td>${(h.recentPlacings || []).join('-') || '-'}</td>
      <td>${h.totalScore}${(h.styleBonus != null && h.styleBonus !== 0 && h.competScore != null) ? ` <span class="hint" style="font-size:10px">(${h.competScore}+각질${h.styleBonus > 0 ? '+' : ''}${h.styleBonus})</span>` : ''}</td>
      <td>${(h.flags || []).map((f) => `<span class="chip ${f.level === 'must' ? 'chip-red' : ''}">${esc(f.msg)}</span>`).join(' ')}</td>
    </tr>`).join('');
    return `<div class="matrix-title" style="font-size:13px">🏇 전적 등급 ${isKeirin ? '(경륜 출마표·競走得点)' : '(출마표2)'}</div>
      <table class="data-table" style="margin-top:4px">
        <thead><tr><th>등급${hasAbs ? '<br><span class="hint" style="font-size:9px">상대</span>' : ''}</th>${hasAbs ? '<th>絶対<br><span class="hint" style="font-size:9px">競走得点</span></th>' : ''}<th>${isKeirin ? '차번' : '마번'}</th><th>${isKeirin ? '선수' : '마명'}</th><th>${isKeirin ? '각질' : '기수'}</th><th>최근착순</th><th>점수</th><th>플래그</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  // [수정#4] 전적등급 + 배당유력마 통합 표시 — 따로 나오던 두 신호를 말별 한 줄로 합쳐 보여준다.
  //   예) "1번 A등급 (전적65·배당유력)" / "4번 (배당유력)" / "7번 A등급 (전적80·배당신호없음)".
  //   기존 '⭐ 유력마' 줄·'🏇 전적 등급' 표는 그대로 두고, 이 통합 카드를 추가만 한다.
  function renderIntegratedGrades(a) {
    const form = a.form || [];
    const keys = (a.keyHorses || []).map(Number);
    const anomaly = a.anomalyHorse != null ? +a.anomalyHorse : null;
    if (!form.length && !keys.length) return '';
    const fmap = {}; form.forEach((h) => { fmap[h.no] = h; });
    const gc = { A: '#38d39f', B: '#4ea1ff', C: '#ffd24f', D: '#8a94a6' };
    const nos = Array.from(new Set([...form.map((h) => h.no), ...keys]));
    const rows = nos.map((no) => {
      const f = fmap[no] || null;
      return {
        no,
        grade: f ? f.grade : null,
        score: f ? f.totalScore : null,
        name: f ? (f.name || '') : '',
        isKey: keys.includes(no),
        isAnom: anomaly === no,
      };
    });
    // 정렬: 배당유력 우선 → 전적점수 내림차순 → 마번
    rows.sort((x, y) => (y.isKey - x.isKey) || ((y.score || 0) - (x.score || 0)) || (x.no - y.no));
    const line = rows.map((r) => {
      const tags = [];
      if (r.score != null) tags.push(`전적${r.score}`);
      if (r.isKey) tags.push('<b style="color:#4ea1ff">배당유력</b>');
      else if (r.grade) tags.push('<span style="color:#8a94a6">배당신호없음</span>');
      if (r.isAnom) tags.push('<b style="color:#ff5c5c">🚨이상감지</b>');
      const gcol = gc[r.grade] || '#c7cfdb';
      const gradeTxt = r.grade ? `<b style="color:${gcol}">${r.grade}등급</b>` : '<span class="hint">전적없음</span>';
      return `<div style="margin:2px 0;font-size:13px">
        <b style="color:#4ea1ff;min-width:34px;display:inline-block">${r.no}번</b> ${gradeTxt}
        ${r.name ? `<span class="hint">${esc(r.name)}</span>` : ''}
        <span class="hint">(${tags.join('·') || '-'})</span></div>`;
    }).join('');
    return `<div style="margin:8px 0;padding:8px 10px;border:1px solid #2b6cb0;border-radius:8px;background:rgba(43,108,176,.08)">
      <div class="matrix-title" style="font-size:13px;color:#7db6ff">🎖️ 통합 등급 <span class="hint" style="font-weight:400">전적등급 + 배당유력마 한눈에</span></div>
      ${line}</div>`;
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
    const mmAxis = (mm && mm.axisCorrected) ? `<div style="margin:2px 0;font-weight:700;color:#38d39f">🔧 복승 축 교정: <b>${(mm.axisCorrected.axis || []).join('+')}</b> <span class="hint" style="font-weight:400">(자금이탈 ${(mm.axisCorrected.demoted || []).join('·')}번 축 강등 → 시장 최저복승으로 축 변경)</span></div>` : '';
    const mmHtml = mm ? `<div class="hint" style="margin:8px 0 2px">⚠️ <b>복승 불일치 감지</b>(단승 예상 vs 실제 최저) — 1.2+ 🟡 · 1.5+ 🔴 · 2.0+ 🔴🔴</div>`
      + `<div style="margin:2px 0"><span class="chip ${/🔴/.test(mm.level) ? 'chip-red' : ''}">${mm.level} 불일치 ${mm.ratio}</span> <span class="hint">${esc(mm.text)}</span></div>${mmAxis}` : '';
    // [2번 고도화] 급락속도·연속하락/반등·페이크·환급률
    const adv = sq.advanced || {};
    const advParts = [];
    (adv.velocity || []).slice(0, 3).forEach((v) => advParts.push(`<div style="margin:2px 0"><span class="chip ${v.level === '🔴' ? 'chip-red' : ''}">${v.level} 급락속도</span> <span class="hint">${v.combo[0]}+${v.combo[1]} 분당 <b>${v.speed}%</b> (${Math.abs(v.pct)}%/${v.minutes}분)</span></div>`));
    Object.values(adv.streaks || {}).forEach((s) => advParts.push(`<div style="margin:2px 0"><span class="chip ${s.type === '연속하락' ? 'chip-red' : ''}">${s.type === '연속하락' ? '🔴 연속하락 +20' : '🟡 단발반등 −15'}</span> <span class="hint">${s.combo[0]}+${s.combo[1]}</span></div>`));
    (adv.fakes || []).forEach((f) => advParts.push(`<div style="margin:2px 0"><span class="chip chip-yellow">⚠️ 페이크 의심</span> <span class="hint">${f.combo[0]}+${f.combo[1]} 급락후반등 (${f.seq.join('→')})</span></div>`));
    // [배당 반등 패턴] 회복비율 분류(유효/페이크) + 재급락(더 강한 신호)
    const RB_META = {
      recrash: { chip: 'chip-red', icon: '🔴🔴 재급락(강신호)', desc: '급락→반등→재급락 = 자금 재유입' },
      valid: { chip: '', icon: '✅ 반등 미미(신호 유효)', desc: '원배당 20% 이내 반등 = 자금 유지', style: 'style="border-color:#38d39f;color:#38d39f"' },
      fake: { chip: 'chip-yellow', icon: '⚠️ 페이크(자금 이탈)', desc: '원배당 80%+ 회복' },
    };
    (adv.rebounds || []).filter((r) => RB_META[r.pattern]).slice(0, 4).forEach((r) => {
      const m = RB_META[r.pattern];
      advParts.push(`<div style="margin:2px 0"><span class="chip ${m.chip}" ${m.style || ''}>${m.icon}</span> <span class="hint"><b>${r.combo[0]}+${r.combo[1]}</b> ${m.desc} · 회복 ${Math.round((r.recovery || 0) * 100)}% (${r.orig}→${r.low}→${r.cur}배)</span></div>`);
    });
    // [4번] 말별 연속 하락 등급 — 확정신호(3회+)·약한신호(2회) 우선, 반등=페이크 · 후보(1회)는 생략
    Object.values(adv.horseStreaks || {})
      .filter((h) => h.rebounded || h.count >= 2)
      .sort((a, b) => (b.count - a.count))
      .forEach((h) => {
        const red = h.count >= 3 && !h.rebounded;
        advParts.push(`<div style="margin:2px 0"><span class="chip ${red ? 'chip-red' : (h.rebounded ? '' : 'chip-yellow')}" ${h.rebounded ? 'style="border-color:#fb923c;color:#fb923c"' : ''}>${h.level} ${h.rebounded ? '페이크의심' : h.count + '회연속하락'}</span> <span class="hint"><b>${h.no}번</b> ${esc(h.label)} (${(h.series || []).join('→')})</span></div>`);
      });
    if (adv.overround && adv.overround.concentrated) advParts.push(`<div style="margin:2px 0"><span class="chip" style="border-color:#ff9f43;color:#ff9f43">🟠 자금집중</span> <span class="hint">상위 3조합이 전체의 <b>${Math.round(adv.overround.top3Share * 100)}%</b> 점유 (환급률 ${adv.overround.refundRate != null ? adv.overround.refundRate : adv.overround.invSum})</span></div>`);
    const advHtml = advParts.length ? `<div class="hint" style="margin:8px 0 2px">⚡ <b>실시간 이상감지 고도화</b>(급락속도·연속하락·페이크·자금집중)</div>${advParts.join('')}` : '';
    return `<div style="margin:8px 0;border:1px solid var(--border);border-radius:8px;padding:8px">
      <div class="matrix-title" style="font-size:14px">🎯 신호 품질 분석 <span class="hint" style="font-weight:400">노이즈 제거 · 자금 집중 감지</span></div>
      <div style="margin:3px 0"><span class="chip" style="border-color:${stColor};color:${stColor}">${esc(st.name || '일반')}</span> <span class="hint">가중치 전적 <b>${Math.round((st.formW || 0.5) * 100)}%</b> · 신호 <b>${Math.round((st.signalW || 0.5) * 100)}%</b> · ${esc(st.note || '')}</span></div>
      <div class="hint" style="margin:4px 0 2px">시장 전체 평균 급락 <b>${ex.overall != null ? ex.overall + '%' : '-'}</b> 대비 <b>초과 급락(집중도)</b> — 초과 5%p+ 🔴 진짜신호 · 0~5%p 🟡 약한신호 · 그 외 노이즈 제거</div>
      ${exRows}${confHtml}${wxHtml}${mmHtml}${advHtml}${iaHtml}</div>`;
  }

  // [버그2·3] 복승/삼복승 추천 + 예산 배분 금액 표
  // [보완#1] 복승 배당판 유력/제거 색상 — 말 번호를 유력마(녹색)/제거마(빨강·회색)로 채색.
  //   유력 = keyHorses ∪ 제거패널 후보(keep/override) · 제거 = 제거패널 eliminated(verdict 🔴/🟠).
  function _horseRoleMap(a) {
    const map = {};
    (a.keyHorses || []).forEach((h) => { map[+h] = 'fav'; });
    const e = a.elimination || {};
    (e.horses || []).forEach((h) => {
      const keep = h.keep || h.override;
      if (keep) { if (map[+h.no] == null) map[+h.no] = 'fav'; }
      else { map[+h.no] = (h.verdict === '🔴' ? 'cut' : 'weakcut'); }
    });
    return map;
  }
  const _ROLE_COLOR = { fav: '#38d39f', cut: '#ef4444', weakcut: '#ff9f43' };
  // 조합(예 [8,6])을 말별 색상으로 렌더. 유력=녹색굵게 · 확실제거=빨강취소선 · 제거권장=주황
  function _colorCombo(combo, roleMap) {
    return (combo || []).map((n) => {
      const role = roleMap[+n];
      if (role === 'fav') return `<b style="color:${_ROLE_COLOR.fav}">${n}</b>`;
      if (role === 'cut') return `<span style="color:${_ROLE_COLOR.cut};text-decoration:line-through">${n}</span>`;
      if (role === 'weakcut') return `<span style="color:${_ROLE_COLOR.weakcut}">${n}</span>`;
      return `${n}`;
    }).join('<span style="color:#8a94a6">+</span>');
  }

  // ═══════════ [배당 매트릭스·전종목] _triple_analyze 데이터로 복승 매트릭스 + 색상/호버/예산/타임라인 ═══════════
  const _MATRIX_COLOR = { fav: '#38d39f', cut: '#ef4444', weakcut: '#ff9f43', dark: '#c084fc', inv: '#fbbf24' };
  const _MATRIX_LABEL = { fav: '유력마', cut: '확실제거', weakcut: '제거권장', dark: '복병', inv: '역배열' };

  // 말별 역할(유력/제거/복병/역배열) — 기존 _horseRoleMap(유력·제거) + 복병(darkHorses) 확장
  function _matrixRoleMap(a) {
    const map = _horseRoleMap(a);                 // fav / cut / weakcut
    const keys = new Set((a.keyHorses || []).map(Number));
    ((a && a.darkHorses) || []).forEach((h) => {
      const n = Number(h.no);
      if (!keys.has(n) && map[n] !== 'cut' && map[n] !== 'weakcut') map[n] = 'dark';
    });
    return map;
  }

  // 복승 배당 heat (낮을수록 진한 파랑) — renderOddsMatrix와 동일 스케일감
  function _mHeat(v, lo, hi) {
    if (!(v > 0)) return 'transparent';
    const l = Math.log(v), a0 = Math.log(lo), a1 = Math.log(hi);
    const f = a1 > a0 ? (l - a0) / (a1 - a0) : 0;
    return `rgba(37,99,235,${(0.85 - 0.7 * f).toFixed(2)})`;
  }

  /** [배당 매트릭스] a.quinella(복승)로 삼각 매트릭스 + 유력/제거/복병/역배열 색상 + 급락 강조 + 호버 근거. */
  function renderBmedMatrix(a) {
    const q = Array.isArray(a.quinella) ? a.quinella : [];
    const om = {};                                 // "min|max" → odds
    const nosSet = new Set();
    q.forEach((x) => {
      const c = (x.combo || x.pair || []).map(Number);
      if (c.length === 2 && x.odds > 0) {
        const k = Math.min(c[0], c[1]) + '|' + Math.max(c[0], c[1]);
        om[k] = x.odds; nosSet.add(c[0]); nosSet.add(c[1]);
      }
    });
    const nos = [...nosSet].filter((n) => n > 0).sort((x, y) => x - y);
    if (!nos.length) return '<p class="hint">복승 배당이 아직 수집되지 않았습니다. 수집되면 매트릭스가 표시됩니다.</p>';
    const vals = Object.values(om); const lo = Math.min(...vals), hi = Math.max(...vals);
    const role = _matrixRoleMap(a);
    const invSet = new Set(((a.inverse || {}).invHorses || []).map(Number));
    const isCycle = a.sport === 'cycle';
    const formMap = {}; (a.form || []).forEach((h) => { if (h.no != null) formMap[Number(h.no)] = h; });
    // 급락 셀 (직전/1차 대비 20%+ 하락) · 추천 복승 조합
    const dropMap = {};
    (a.drops || []).forEach((d) => {
      const c = (d.combo || []).map(Number);
      if (c.length === 2 && (d.pct || 0) <= -20) dropMap[Math.min(c[0], c[1]) + '|' + Math.max(c[0], c[1])] = Math.round(d.pct);
    });
    const recSet = new Set();
    (a.betRecommend || []).forEach((b) => {
      const c = (b.combo || []).map(Number);
      if (c.length === 2 && /복/.test(b.kind || b.label || '')) recSet.add(Math.min(c[0], c[1]) + '|' + Math.max(c[0], c[1]));
    });
    const hdr = (n) => {
      const r = role[n]; const inv = invSet.has(n); const col = _MATRIX_COLOR[r] || '#cbd5e1';
      const mark = r === 'fav' ? '⭐' : r === 'cut' ? '❌' : r === 'weakcut' ? '△' : r === 'dark' ? '🟣' : '';
      let sub = '';
      if (isCycle && formMap[n]) {                 // [2번] 경륜: 각질 + 경쟁점수 등급
        const f = formMap[n];
        sub = `<div style="font-size:9px;color:#94a3b8">${esc(f.styleType || '')}${f.absGrade ? ' ' + f.absGrade : (f.competScore ? ' ' + f.competScore : '')}</div>`;
      }
      const tip = `${n}번${r ? ' · ' + _MATRIX_LABEL[r] : ''}${inv ? ' · 역배열' : ''}`;
      return `<th style="color:${col};${inv ? 'box-shadow:inset 0 0 0 2px ' + _MATRIX_COLOR.inv + ';' : ''}" title="${tip}">${mark}${n}${sub}</th>`;
    };
    // 삼각 매트릭스(행 r > 열 c)
    let head = '<tr><th class="corner" style="font-size:10px">복승</th>' + nos.slice(0, -1).map(hdr).join('') + '</tr>';
    let body = '';
    for (let ri = 1; ri < nos.length; ri++) {
      const rowNo = nos[ri]; let tds = '';
      for (let ci = 0; ci < ri; ci++) {
        const colNo = nos[ci];
        const key = Math.min(rowNo, colNo) + '|' + Math.max(rowNo, colNo);
        const v = om[key];
        if (v > 0) {
          const rec = recSet.has(key); const dp = dropMap[key];
          const rr = role[rowNo], rc = role[colNo];
          // 셀 강조: 급락(빨강테두리 애니메이션)·추천(금색테두리)·유력×유력(녹색테두리)
          let bd = '';
          if (dp != null) bd = 'box-shadow:inset 0 0 0 2px #ef4444;';
          else if (rec) bd = 'box-shadow:inset 0 0 0 2px #ffd24f;';
          else if (rr === 'fav' && rc === 'fav') bd = 'box-shadow:inset 0 0 0 2px #38d39f;';
          const inv = (invSet.has(rowNo) || invSet.has(colNo)) ? 'outline:2px solid ' + _MATRIX_COLOR.inv + ';outline-offset:-3px;' : '';
          const tags = [rr ? _MATRIX_LABEL[rr] + rowNo : '', rc ? _MATRIX_LABEL[rc] + colNo : '', dp != null ? '급락 ' + dp + '%' : '', rec ? '추천' : ''].filter(Boolean).join(' · ');
          const tip = `${rowNo}-${colNo} = ${v}배${tags ? ' | ' + tags : ''}`;
          tds += `<td class="cell" style="background:${_mHeat(v, lo, hi)};${bd}${inv}" title="${tip}">${v}${dp != null ? `<sup style="color:#fecaca;font-size:8px">▼</sup>` : ''}</td>`;
        } else tds += '<td class="empty">·</td>';
      }
      tds += '<td class="diag">—</td>';
      body += `<tr>${hdr(rowNo)}${tds}</tr>`;
    }
    // [7번] 유사케이스 적중률
    const pm = a.patternMatch || {}; const conf = pm.confidence || {};
    const hit = (conf.rate != null) ? `<div class="hint" style="margin-top:4px">🎯 유사케이스 적중률 <b style="color:${conf.rate >= 50 ? '#38d39f' : '#ffd24f'}">${conf.rate}%</b>${conf.n ? ` (표본 ${conf.n})` : ''}${conf.level ? ` · ${esc(conf.level)}` : ''}</div>` : '';
    // [5번] 예산(네덜란드식 배분) 요약 — 기존 betRecommend alloc 재사용
    const bEl = document.querySelector('#tripleBudget');
    const budget = Math.max(0, parseInt((bEl && bEl.value) || '0', 10) || 0);
    const won = (n) => Math.round(n / 100) * 100;
    const budRows = (a.betRecommend || []).filter((r) => (r.alloc || 0) > 0).slice(0, 6).map((r) => {
      const amt = budget > 0 ? won(budget * (r.alloc || 0) / 100).toLocaleString('ko-KR') + '원' : '-';
      return `<span class="chip" title="${esc(r.label || '')}">${_colorCombo(r.combo, role)} <b>${r.alloc}%</b>${budget > 0 ? ' · ' + amt : ''}</span>`;
    }).join(' ');
    const legend = `<div class="hint" style="font-size:10px;margin-top:4px">
      <b style="color:${_MATRIX_COLOR.fav}">⭐유력마</b> · <span style="color:${_MATRIX_COLOR.dark}">🟣복병</span> · <span style="color:${_MATRIX_COLOR.weakcut}">△제거권장</span> · <span style="color:${_MATRIX_COLOR.cut}">❌확실제거</span> · <span style="box-shadow:inset 0 0 0 2px ${_MATRIX_COLOR.inv};padding:0 3px">역배열</span> · <span style="box-shadow:inset 0 0 0 2px #ef4444;padding:0 3px">▼급락</span> · <span style="box-shadow:inset 0 0 0 2px #ffd24f;padding:0 3px">추천</span></div>`;
    return `<div class="matrix-legend"><span>낮은 배당</span><span class="legend-grad"></span><span>높은 배당</span></div>
      <div class="matrix-wrap"><table class="odds-matrix"><thead>${head}</thead><tbody>${body}</tbody></table></div>
      ${legend}${hit}
      ${budRows ? `<div style="margin-top:6px"><span class="hint">💰 예산 배분(네덜란드식)</span><br>${budRows}</div>` : ''}`;
  }

  /** [배당 매트릭스] 토글 패널(30초 재렌더에도 열림상태 유지). */
  function renderBmedMatrixPanel(a) {
    if (!Array.isArray(a.quinella) || !a.quinella.length) return '';
    const open = window._bmedMatrixOpen ? ' open' : '';
    return `<details class="bmed-matrix-panel" style="margin:6px 0;border:1px solid var(--border);border-radius:8px;padding:4px 8px"${open} ontoggle="window._bmedMatrixOpen=this.open">
      <summary style="cursor:pointer;font-weight:700;color:#7dd3fc;padding:5px 0">📊 배당 매트릭스 <span class="hint" style="font-weight:400">유력/제거/복병/역배열 색상 · 급락 강조 · 예산</span></summary>
      <div style="padding-top:6px">${renderBmedMatrix(a)}</div>
    </details>`;
  }

  function renderBetRecommend(a, budgetSel) {
    const recs = a.betRecommend || [];
    // [타이밍 추천 정책·마감 후 추천 금지] 발주(T-0) 이후엔 추천 조합을 표시하지 않는다(참고만).
    if (a.recommendClosed) {
      return `<div class="bet-box" style="display:block;margin:6px 0;border-left:3px solid #8a94a6;background:rgba(138,148,166,.1)">
        <b style="color:#cbd5e1">🔒 마감 — 추천 종료</b>
        <div class="hint" style="margin-top:3px">발주(T-0) 이후입니다. 마감 후에는 추천을 하지 않습니다(급락이 있어도 참고만).</div></div>`;
    }
    // [추천 신중화·근본해결] 신호 대기(wait)·추천 차단(recommendGated: 90초 미달·시장신호 2개 미만·패스형)이면
    //   추천 조합을 표시하지 않고 "⏳ 신호 대기 중"만 표시(저배당 무조건 추천 방지).
    //   단 [T-2분 강제 추천] recommendForced면 신호 약해도 저배당 기준으로 강제 추천(게이트 무시).
    //   복기/리포트 등 raceJudgment 없는 뷰는 영향 없음.
    if (!a.recommendForced && (a.recommendGated || (a.raceJudgment && a.raceJudgment.type === 'wait'))) {
      const m = (a.raceJudgment && a.raceJudgment.message) || '뚜렷한 신호 2개+ 확인 후 추천 조합이 표시됩니다';
      return `<div class="bet-box" style="display:block;margin:6px 0;border-left:3px solid #8a94a6;background:rgba(138,148,166,.08)">
        <b style="color:#cbd5e1">⏳ 신호 대기 — 추천 보류</b>
        <div class="hint" style="margin-top:3px">${esc(m)}</div>
        <div class="hint" style="font-size:11px;margin-top:2px">저배당 무조건 추천 방지 — 급락10%+·쌍승역전·연속하락2회+·환수율이상 중 <b>2개+ 확인 시</b> 추천 조합 표시</div></div>`;
    }
    if (!recs.length) return '';
    const roleMap = _horseRoleMap(a);   // [보완#1] 유력/제거 색상 맵
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
        <td style="font-weight:700">${_colorCombo(r.combo, roleMap)}</td>
        <td>${qTxt}</td>
        <td>${odTxt}</td>
        <td>${r.alloc || 0}%</td>
        <td>${amt != null ? amt.toLocaleString('ko-KR') + '원' : '<span class="hint">예산입력</span>'}</td>
      </tr>`;
    }).join('');
    const totalAlloc = recs.reduce((s, r) => s + (r.alloc || 0), 0);
    const totalAmt = budget > 0 ? won(budget * totalAlloc / 100) : null;
    const upd = _betUpdatedFlag ? ' <span style="color:#38d39f">⚡ 업데이트됨</span>' : '';
    // [보완#1] 색상 범례 — 조합 속 말 번호가 유력/제거 어느 쪽인지 한눈에.
    const legend = `<div class="hint" style="font-size:10px;margin-top:2px">조합 색상: <b style="color:${_ROLE_COLOR.fav}">유력마</b> · <span style="color:${_ROLE_COLOR.weakcut}">제거권장</span> · <span style="color:${_ROLE_COLOR.cut};text-decoration:line-through">확실제거</span></div>`;
    // [타이밍 추천 정책] T-1분 최종 확정(🔒) · T-2분 강제 추천(⚡) 배너
    const phaseBanner = a.recommendLocked
      ? `<div style="margin:4px 0;padding:5px 9px;border-left:3px solid #ef4444;background:rgba(239,68,68,.14);border-radius:6px;color:#fca5a5;font-weight:800">🔒 T-1분 · 최종 확정 — 이 조합으로 마감까지 확정</div>`
      : a.recommendForced
        ? `<div style="margin:4px 0;padding:5px 9px;border-left:3px solid #fbbf24;background:rgba(245,158,11,.14);border-radius:6px;color:#fcd34d;font-weight:800">⚡ T-2분 · 강제 추천 — 신호 약해도 저배당(시장 유력) 기준 편성</div>`
        : '';
    // [핵심만 임팩트] 복승 최대 3 + 삼복승 최대 2 = 총 5개를 큰 카드로. 나머지는 접기(무삭제).
    const impactCard = _renderImpactCard(recs, roleMap);
    const shownKeys = _impactShownKeys(recs);
    const extraN = recs.length - shownKeys.size;
    const fullTable = `<table class="data-table" style="margin-top:4px">
        <thead><tr><th>종류</th><th>조합</th><th>신호품질</th><th>예상배당</th><th>배분</th><th>금액</th></tr></thead>
        <tbody>${rows}</tbody>
        ${totalAmt != null ? `<tfoot><tr><td colspan="4"></td><td><b>${totalAlloc}%</b></td><td><b>${totalAmt.toLocaleString('ko-KR')}원</b></td></tr></tfoot>` : ''}
      </table>`;
    // 접기: 전체 조합·배분·금액 상세(추가 참고 조합 N개). 5개 이하여도 배분/금액 상세는 접어서 보존.
    const detailsSummary = extraN > 0 ? `＋ 추가 참고 조합 ${extraN}개 · 배분/금액 상세 보기` : '＋ 배분·금액 상세 보기';
    const collapse = `<details style="margin-top:6px"><summary style="cursor:pointer;color:#94a3b8;font-size:12px">${detailsSummary}</summary>${legend}${fullTable}</details>`;
    return `<div class="matrix-title" style="font-size:13px">🎯 메인 추천 <span class="hint" style="font-weight:400">(신호 기반)</span>${a.recommendLocked ? ' <span style="color:#ef4444">🔒 확정</span>' : ''}${upd} ${budget > 0 ? `<span class="hint" style="font-weight:400">예산 ${budget.toLocaleString('ko-KR')}원 배분</span>` : '<span class="hint" style="font-weight:400">(예산 입력 시 금액 자동계산)</span>'}</div>
      ${phaseBanner}
      ${impactCard}
      ${collapse}`;
  }

  // [핵심만 임팩트] 별점: ★★★ 신호 강함(상) · ★★ 중간 · ★ 참고용. signalQuality 우선, 없으면 라벨/배분 폴백.
  function _betStars(r) {
    const q = r.signalQuality;
    if (q === '상') return '★★★';
    if (q === '중') return '★★';
    if (q === '하') return '★';
    if (/메인/.test(r.label || '') || (r.alloc || 0) >= 30) return '★★★';
    if (/보조|받치기|승격/.test(r.label || '') || (r.alloc || 0) >= 12) return '★★';
    return '★';
  }
  function _betOddsTxt(r) {
    if (r.expOdds != null) return r.expOdds + '배';
    if (r.expOddsEst != null) return '추정 ' + r.expOddsEst + '배';
    return '미수집';
  }
  // 임팩트 카드에 표시할 조합 선택: 복승 최대 3(배분 높은 순) + 삼복승 최대 2.
  function _impactPick(recs) {
    const boks = recs.filter((r) => r.kind === '복승').slice().sort((a, b) => (b.alloc || 0) - (a.alloc || 0)).slice(0, 3);
    const sams = recs.filter((r) => r.kind === '삼복승').slice().sort((a, b) => (b.alloc || 0) - (a.alloc || 0)).slice(0, 2);
    return { boks, sams };
  }
  function _impactShownKeys(recs) {
    const { boks, sams } = _impactPick(recs);
    const keys = new Set();
    [...boks, ...sams].forEach((r) => keys.add(r.kind + ':' + (r.combo || []).join('+')));
    return keys;
  }
  function _renderImpactCard(recs, roleMap) {
    const { boks, sams } = _impactPick(recs);
    if (!boks.length && !sams.length) return '';
    const row = (r, isSam) => {
      const stars = _betStars(r);
      const starHtml = `<span style="color:#ffd24f;letter-spacing:1px;font-size:14px">${stars}</span><span style="color:#3a3f4b;font-size:14px">${'★'.repeat(3 - stars.length)}</span>`;
      const tag = isSam ? `<span class="hint" style="color:#a855f7;font-weight:700;font-size:11px">${/보험/.test(r.label || '') ? '보험' : '메인'}</span>` : starHtml;
      return `<div style="display:flex;align-items:center;gap:10px;padding:6px 4px;border-bottom:1px solid rgba(255,255,255,.05)">
        <b style="min-width:52px;color:${isSam ? '#38d39f' : '#4ea1ff'}">${isSam ? '삼복승' : '복승'}</b>
        <b style="min-width:78px;font-size:15px">${_colorCombo(r.combo, roleMap)}</b>
        <b style="min-width:64px;color:#e2e8f0">${esc(_betOddsTxt(r))}</b>
        <span style="margin-left:auto">${tag}</span>
      </div>`;
    };
    return `<div style="margin:6px 0;padding:10px 12px;border:2px solid #38d39f;border-radius:10px;background:linear-gradient(180deg,rgba(56,211,159,.10),rgba(20,28,43,.85))">
      <div style="font-size:17px;font-weight:800;color:#38d39f;margin-bottom:4px">🎯 지금 사세요!</div>
      ${boks.map((r) => row(r, false)).join('')}
      ${sams.length ? `<div style="height:4px"></div>` : ''}
      ${sams.map((r) => row(r, true)).join('')}
      <div class="hint" style="font-size:10px;margin-top:5px">★★★ 신호 강함 · ★★ 중간 · ★ 참고용 · 삼복승은 보험/메인</div>
    </div>`;
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
        // [유력마 적중 병행 노출] 정확 복승/삼복승 판정과 별개로 유력마 2/3 입상 참고 지표를 함께 표시(손익·기존통계 불변).
        const khLine = (rv.keyhorse_quinella_hit || rv.keyhorse_trifecta_hit)
          ? `<div style="margin-top:4px;color:#fbbf24">🔶 유력마 기반 적중(참고): 복승 ${rv.keyhorse_quinella_hit ? '✅' : '—'} · 삼복승 ${rv.keyhorse_trifecta_hit ? '✅' : '—'}${(rv.keyhorse_placed || []).length ? ` <span class="hint">입상 유력마 ${(rv.keyhorse_placed || []).join('·')}</span>` : ''}</div>`
          : '';
        judge = `<div style="margin-top:6px">
          <div>복승 추천: ${yn(rv.quinella_hit)}${rv.payouts && rv.payouts.quinella ? ` <span class="hint">${rv.payouts.quinella}배</span>` : ''}</div>
          <div>삼복승 추천: ${yn(rv.trifecta_hit)}${rv.payouts && rv.payouts.trifecta ? ` <span class="hint">${rv.payouts.trifecta}배</span>` : ''}</div>
          ${khLine}
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
    // [마감 후 대급락] 실제 입상률 통계(패턴 신뢰도)
    let acs = null; try { acs = await (await fetch('/api/after-close/stats')).json(); } catch (_) { /* */ }
    // [학습일지] 오늘 배운 것 대시보드(성공/실패/새패턴/개선 + 내일 집중 + 누적 패턴 신뢰도)
    let dl = null; try { dl = await (await fetch('/api/daily-learning')).json(); } catch (_) { /* */ }
    // [고배당 심층분석] 복승30+/삼복승100+ 미적중 분석 통계(A/B/C 유형·개선 후 예상)
    let ho = null; try { ho = await (await fetch('/api/high-odds-review?stats=1')).json(); } catch (_) { /* */ }
    // [수동 케이스 학습] 카와사키 11R 등 사용자 지정 놓친 케이스 상세 복기
    let hocases = null; try { hocases = await (await fetch('/api/high-odds-review?cases=1')).json(); } catch (_) { /* */ }
    // [복기 학습 재설계] 패턴별 신뢰도(표본 50회 게이팅) — 공식 수정은 수동
    let pconf = null; try { pconf = await (await fetch('/api/learning/pattern-confidence')).json(); } catch (_) { /* */ }
    // [기준치 도출] 누적 데이터 기반 최적 기준치 추천(자동 적용 안 함·수동 승인)
    let thopt = null; try { thopt = await (await fetch('/api/thresholds/optimize')).json(); } catch (_) { /* */ }
    // [신호별 적중률·4번] 유력마 기반 신호별 복승/삼복승 적중률
    let sigstat = null; try { sigstat = await (await fetch('/api/learning/signal-stats')).json(); } catch (_) { /* */ }
    // [오늘 결과 통계 대시보드] 오늘 등록 경주 집계(요약·경마장별·신호별·타임라인)
    let today = null; try { today = await (await fetch('/api/stats/today')).json(); } catch (_) { /* */ }
    // [💎 중고배당 유력마·5번] 누적 적중률
    let midhigh = null; try { midhigh = await (await fetch('/api/learning/mid-high-odds')).json(); } catch (_) { /* */ }
    const s = d.stats || {};
    // [AI Phase1] AI 학습 데이터 현황 대시보드
    let ai = null; try { ai = await (await fetch('/api/ai-training/status')).json(); } catch (_) { /* */ }
    const card = (title, st) => `<div class="bet-box" style="display:inline-block;min-width:170px;margin:4px;vertical-align:top"><b>${title}</b><br>${(st && st.rate != null) ? `<span style="font-size:20px;color:#38d39f">${st.rate}%</span> <span class="hint">(${st.hit}/${st.n})</span>` : '<span class="hint">데이터 없음</span>'}</div>`;
    el.innerHTML = `<div style="margin-bottom:6px">학습 경주 수: <b>${d.count || 0}</b></div>
      ${renderTodayStats(today)}
      ${renderMidHighStats(midhigh)}
      ${renderDailyLearning(dl)}
      ${renderSignalStats(sigstat)}
      ${renderPatternConfidence(pconf)}
      ${renderThresholdOptimize(thopt)}
      ${renderHighOddsCases(hocases)}
      ${renderHighOddsReview(ho)}
      ${renderAiDataStatus(ai)}
      ${renderProfitSummary(s.profit_summary)}
      ${renderCompareStats(s.compare_stats, s.integrated_weights, s.basis_weights)}
      ${card('추천 적중률', s.recommend_hit)}
      ${card('급락 감지 적중률', s.drop_anomaly)}
      ${card('쌍승 역전 적중률', s.reversal)}
      ${card('전적 유력마 적중률', s.form_pick)}
      ${card('제거 판정 적중률', s.elimination)}
      ${renderNearMissStats(s.near_miss, nm)}
      ${renderAlertStats(s.alert_stats)}
      ${renderAfterCloseStats(acs)}
      ${renderTrackMonthStats(s.by_track, s.by_month, s.by_strategy)}
      ${renderDiscoveredPatterns(disc)}
      ${renderPatternStats(s.pattern_stats)}
      ${renderDropTiming(s.drop_timing)}
      ${renderUpsetStats(up)}`;
    // [복기 학습] 실패 대시보드 + 명예의 전당도 함께 갱신
    try { loadFailureReview(); loadHallOfFame(); } catch (_) { /* */ }
  }

  // [학습일지] 오늘 배운 것 대시보드 — 성공/실패/새패턴/개선 카운트 + 내일 집중 + 누적 패턴 신뢰도
  // [기준치 도출 3·4·5번] 📊 기준치 최적화 분석 — 데이터 추천 기준치 + 수동 승인 버튼.
  function renderThresholdOptimize(to) {
    if (!to || to.error) return '';
    const block = (t, unit, dir) => {
      if (!t) return '';
      const statusColor = t.status === '검토가능' ? '#38d39f' : '#fbbf24';
      const rec = (t.recommended != null)
        ? `데이터 추천: <b style="color:#38d39f">${t.recommended}${unit}</b>` : '데이터 추천: <span class="hint">표본 부족</span>';
      const basis = (t.recommendedRate != null && t.currentRate != null)
        ? `근거: ${t.recommended}${unit} 시 적중률 <b>${t.recommendedRate}%</b> / 현재 ${t.current}${unit} 시 <b>${t.currentRate}%</b>${t.improve != null ? ` (개선 <b style="color:${t.improve >= 0 ? '#38d39f' : '#f87171'}">${t.improve >= 0 ? '+' : ''}${t.improve}%p</b>)` : ''}`
        : '근거: <span class="hint">표본 축적 중</span>';
      const canApply = t.status === '검토가능' && t.recommended != null && t.recommended !== t.current;
      const btn = canApply
        ? `<button class="btn btn-primary" style="font-size:11px;padding:2px 8px" onclick="window._applyThreshold&&window._applyThreshold('${t.key}',${t.recommended})">✅ 적용</button> <button class="btn" style="font-size:11px;padding:2px 8px" onclick="this.closest('div').style.opacity=.5">❌ 유지</button>`
        : '';
      return `<div style="margin:6px 0;padding:7px 10px;border-left:3px solid ${statusColor};background:rgba(255,255,255,.03);border-radius:6px">
        <div style="font-weight:700">${t.key === 'excess_drop_min' ? '초과급락' : '역배열'} 최적 기준치 <span class="hint">현재: ${t.current}${unit}</span></div>
        <div style="margin:2px 0">${rec}</div>
        <div class="hint" style="font-size:12px">${basis}</div>
        <div style="margin-top:3px">상태: <b style="color:${statusColor}">${t.status === '검토가능' ? `✅ 적용 검토 가능 (${(t.candidates || []).reduce((m, c) => Math.max(m, c.fired || 0), 0)}회)` : `⚠️ 표본 부족 (${to.excess_drop.sampleMin || 50}회 필요)`}</b> ${btn}</div></div>`;
    };
    return `<div class="bet-box" style="margin:6px 0;padding:12px 14px;border:1px solid #2a4a4a;border-radius:10px">
      <div style="font-weight:700;font-size:15px;margin-bottom:4px">📊 기준치 최적화 분석 <span class="hint" style="font-weight:400">(현재 ${to.raceCount || 0}경주 기준)</span></div>
      ${block(to.excess_drop, '%+', 'ge')}
      ${block(to.reversal, ' 미만', 'lt')}
      <div class="hint" style="font-size:11px;margin-top:5px">⚠️ ${esc(to.note || '')} · 적용 시 이전 기준치는 자동 백업됩니다.</div></div>`;
  }

  // [기준치 도출 4번] 수동 승인 → 서버 config 반영(이전값 백업). 전역 노출(onclick).
  window._applyThreshold = async function (key, value) {
    if (!confirm(`기준치를 적용할까요?\n${key} = ${value}\n(이전 값은 백업되며, 이후 경주부터 반영)`)) return;
    try {
      const r = await (await fetch('/api/thresholds/apply', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key, value }) })).json();
      if (r.ok) { toast(`✅ 기준치 적용됨: ${key}=${value}`); loadLearningStats(); }
      else toast('적용 실패: ' + (r.error || ''));
    } catch (e) { toast('적용 실패: ' + e.message); }
  };

  // [💎 중고배당 유력마·5번] 누적 적중률 카드(신호별 입상률 포함).
  function renderMidHighStats(mh) {
    if (!mh || !mh.count) return '';
    const rate = mh.rate || 0;
    const rc = rate >= 40 ? '#38d39f' : (rate >= 20 ? '#fbbf24' : '#f87171');
    const bysig = (mh.bySignal || []).map((r) => `<span style="display:inline-block;margin:2px 8px 2px 0;font-size:12px"><b style="color:#f0abfc">${esc(r.signal)}</b> <span class="hint">${r.placed}/${r.count}</span> <b style="color:${r.rate >= 40 ? '#38d39f' : '#b8c0cc'}">(${r.rate}%)</b></span>`).join('');
    return `<div class="panel-card" style="margin:8px 0;border:1px solid #f0abfc">
      <div class="matrix-title" style="color:#f0abfc">💎 중고배당 유력마 적중률 <span class="hint" style="font-weight:400">복승 10배+ & 강한 신호 → 실제 입상률</span></div>
      <div style="margin:4px 0">누적 <b style="font-size:20px;color:${rc}">${rate}%</b> <span class="hint">(입상 ${mh.placed}/${mh.count}건)</span></div>
      ${bysig ? `<div style="margin-top:4px"><b style="font-size:12px">신호별 입상률: </b>${bysig}</div>` : ''}
      <div class="hint" style="font-size:11px;margin-top:4px">※ 50경주+ 쌓이면 신뢰도 판정. 저배당 축 + 💎 고배당 = 삼복승 대박 보험 자동 편성.</div>
    </div>`;
  }

  // [오늘 결과 통계 대시보드] 오늘 요약 카드 + 경마장별 + 신호별 + 타임라인.
  function renderTodayStats(t) {
    if (!t || t.error) return '';
    if (!t.total) return `<div class="panel-card" style="margin:8px 0"><div class="matrix-title" style="color:#ffd24f">📅 오늘 ${esc(t.date || '')} 요약</div><p class="hint">아직 등록된 결과가 없습니다.</p></div>`;
    const pcol = (t.profit || 0) >= 0 ? '#38d39f' : '#ff6b6b';
    const psign = (t.profit || 0) >= 0 ? '+' : '';
    // [2번] 상단 큰 요약 박스
    const summary = `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;margin:6px 0">
      <div style="padding:10px;border-radius:8px;background:rgba(78,161,255,.10);border:1px solid #4ea1ff;text-align:center"><div class="hint" style="font-size:11px">등록</div><b style="font-size:22px;color:#e2e8f0">${t.total}<span style="font-size:12px">경주</span></b></div>
      <div style="padding:10px;border-radius:8px;background:rgba(56,211,159,.10);border:1px solid #38d39f;text-align:center"><div class="hint" style="font-size:11px">복승 적중</div><b style="font-size:22px;color:#38d39f">${t.hit_quinella}<span style="font-size:12px">건 (${t.rate_quinella}%)</span></b></div>
      <div style="padding:10px;border-radius:8px;background:rgba(192,132,252,.10);border:1px solid #c084fc;text-align:center"><div class="hint" style="font-size:11px">삼복승 적중</div><b style="font-size:22px;color:#c084fc">${t.hit_trifecta}<span style="font-size:12px">건 (${t.rate_trifecta}%)</span></b></div>
      <div style="padding:10px;border-radius:8px;background:rgba(255,255,255,.04);border:1px solid ${pcol};text-align:center"><div class="hint" style="font-size:11px">손익</div><b style="font-size:22px;color:${pcol}">${psign}${(t.profit || 0).toLocaleString()}<span style="font-size:12px">원</span></b></div>
    </div>`;
    // [3번] 경마장별 적중률
    const tracks = Object.entries(t.by_track || {}).sort((a, b) => b[1].total - a[1].total);
    const trackHtml = tracks.length ? `<div style="margin-top:6px"><b style="font-size:13px">🏇 경마장별</b>${tracks.map(([v, e]) => `<div style="display:inline-block;margin:3px 8px 3px 0;font-size:12px"><b>${esc(v)}</b> <span class="hint">${e.hit}/${e.total}</span> <b style="color:${e.rate >= 50 ? '#38d39f' : '#b8c0cc'}">(${e.rate}%)</b></div>`).join('')}</div>` : '';
    // [4번] 신호별 적중률
    const sigs = Object.entries(t.by_signal || {}).sort((a, b) => b[1].total - a[1].total);
    const sigHtml = sigs.length ? `<div style="margin-top:6px"><b style="font-size:13px">📊 신호별</b>${sigs.map(([n, e]) => `<div style="display:inline-block;margin:3px 8px 3px 0;font-size:12px"><b>${esc(n)}</b> <span class="hint">${e.hit}/${e.total}</span> <b style="color:${e.rate >= 50 ? '#38d39f' : '#b8c0cc'}">(${e.rate}%)</b></div>`).join('')}</div>` : '';
    // [5번] 오늘 경주 타임라인
    const tl = (t.timeline || []).map((r) => {
      const mark = r.hit ? '✅' : '❌';
      const odds = r.hit && r.quinella_odds ? ` 복승 <b style="color:#38d39f">${r.quinella_odds}배</b>` : (r.hit ? '' : ' 미적중');
      return `<div style="font-size:12px;padding:2px 0"><span class="hint">${esc(r.time)}</span> ${esc(r.race)} ${mark}${odds}</div>`;
    }).join('');
    const tlHtml = tl ? `<div style="margin-top:8px"><b style="font-size:13px">🕐 오늘 경주 타임라인</b><div style="margin-top:2px">${tl}</div></div>` : '';
    return `<div class="panel-card" style="margin:8px 0;border:2px solid #ffd24f">
      <div class="matrix-title" style="color:#ffd24f;font-size:15px">📅 오늘 ${esc(t.date || '')} 요약</div>
      ${summary}${trackHtml}${sigHtml}${tlHtml}
    </div>`;
  }

  // [신호별 적중률·4번] 📊 신호별 적중률 — 유력마 기반(복승 2/3·삼복승 유력마2+복병1) 신호별 복승/삼복승 적중률.
  function renderSignalStats(ss) {
    const rows = (ss && ss.signals) || [];
    if (!rows.length) return '';
    const minN = (ss && ss.minSample) || 50;
    const barsHtml = rows.map((r) => {
      const relColor = r.reliable ? (r.rate_quinella >= 50 ? '#38d39f' : '#f87171') : '#fbbf24';
      const relTxt = r.reliable ? (r.rate_quinella >= 50 ? '✅ 신뢰(50+)' : '🔴 낮음') : `⚠️ 표본부족(${r.count}/${minN})`;
      return `<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:5px 8px;border-radius:6px;margin:2px 0;background:rgba(255,255,255,.03);border-left:3px solid ${relColor}">
        <b style="min-width:96px;color:#e2e8f0">${esc(r.signal)}</b>
        <span class="hint">복승 <b style="color:#4ea1ff">${r.rate_quinella}%</b> · 삼복승 <b style="color:#c084fc">${r.rate_trifecta}%</b></span>
        <span class="hint">(${r.hit_quinella}/${r.count}복 · ${r.hit_trifecta}/${r.count}삼)</span>
        <span style="margin-left:auto;font-size:11px;font-weight:700;color:${relColor}">${relTxt}</span>
      </div>`;
    }).join('');
    return `<div class="panel-card" style="margin:8px 0">
      <div class="matrix-title" style="color:#38d39f">📊 신호별 적중률 <span class="hint" style="font-weight:400">유력마 기반(복승=유력마 2/3 입상·삼복승=유력마2+복병1) · 50경주+ 신뢰</span></div>
      ${barsHtml}
      <div class="hint" style="margin-top:4px;font-size:11px">※ 기존 정확 적중(복승 1+2·삼복승 1+2+3)과 별개 지표입니다. 50경주+ 표본이면 추천 시 '신뢰도 높음' 강조에 반영됩니다.</div>
    </div>`;
  }

  // [복기 학습 재설계 4번] 🧠 학습 현황 — 패턴별 신뢰도(표본 50회 게이팅). 공식 수정은 수동.
  function renderPatternConfidence(pc) {
    const pats = (pc && pc.patterns) || [];
    if (!pats.length) return '';
    const stColor = { '유의': '#38d39f', '신뢰': '#38d39f', '경고': '#f87171', '표본부족': '#fbbf24' };
    const rows = pats.map((p) => {
      const col = stColor[p.status] || '#8a94a6';
      const rateTxt = (p.rate != null) ? `${p.rate}%` : '-';
      return `<div style="margin:3px 0;padding:5px 8px;border-left:3px solid ${col};background:rgba(255,255,255,.03);border-radius:5px">
        <b>${esc(p.label)}</b>: <b style="color:${col}">${rateTxt}</b> <span class="hint">(${p.hit || 0}/${p.fired || 0}회)</span> ${p.icon || ''}
        <div class="hint" style="font-size:11px">${esc(p.note || '')}</div></div>`;
    }).join('');
    return `<div class="bet-box" style="margin:6px 0;padding:12px 14px;border:1px solid #3a3a5a;border-radius:10px">
      <div style="font-weight:700;font-size:15px;margin-bottom:6px">🧠 학습 현황 <span class="hint" style="font-weight:400">(패턴별 신뢰도 · 표본 ${pc.sampleMin || 50}회 게이팅)</span></div>
      ${rows}
      <div style="margin-top:6px;padding:6px 9px;background:rgba(251,191,36,.1);border-radius:6px;color:#fbbf24;font-size:12px">
        ⚠️ 주의: 표본 <b>${pc.sampleMin || 50}회 미만</b>은 참고만 · <b>공식 수정은 수동</b>으로 (1회 실패로 기준 변경 금지)</div></div>`;
  }

  function renderDailyLearning(dl) {
    if (!dl || dl.error) return '';
    const rs = dl.results_summary || {};
    const prof = rs.profit || {};
    const kl = (dl.key_learnings || []).length;
    const mo = (dl.missed_opportunities || []).length;
    const pd = (dl.pattern_discoveries || []).length;
    const si = (dl.system_improvements || []).length;
    const focus = dl.tomorrow_focus || [];
    const rel = dl.cumulative_pattern_reliability || {};
    const relRow = (label, r) => {
      r = r || {};
      const has = r.n != null && r.hit != null;
      const color = (r.rate || 0) >= 60 ? '#38d39f' : ((r.rate || 0) >= 40 ? '#f5c451' : '#ff6b6b');
      return `<div style="display:flex;justify-content:space-between;gap:12px;padding:3px 0">
        <span>${esc(label)}</span>
        <span>${has ? `적중 <b>${r.hit}/${r.n}</b> <span style="color:${color};font-weight:700">(${r.rate}%)</span>` : '<span class="hint">데이터 없음</span>'}</span></div>`;
    };
    const net = prof.net;
    const netColor = (net || 0) > 0 ? '#38d39f' : ((net || 0) < 0 ? '#ff6b6b' : '#9aa4b2');
    const cnt = (icon, label, n) => `<div class="bet-box" style="display:inline-block;min-width:120px;margin:4px;text-align:center;vertical-align:top">
      <div style="font-size:22px;font-weight:700">${n}</div><div class="hint">${icon} ${label}</div></div>`;
    return `<div class="bet-box" style="margin:6px 0;padding:12px 14px;border:1px solid #334155;border-radius:10px">
      <div style="font-weight:700;font-size:15px;margin-bottom:8px">📔 오늘 배운 것 <span class="hint" style="font-weight:400">(${esc(dl.date || '')})</span></div>
      <div style="margin-bottom:8px">
        오늘 경주 <b>${rs.total_races || 0}</b> · 적중 <b>${rs.hits || 0}</b>
        <span style="color:#38d39f">(${rs.hit_rate || 0}%)</span> · 손익
        <b style="color:${netColor}">${net != null ? (net > 0 ? '+' : '') + net.toLocaleString() + '원' : '-'}</b>
        ${prof.settled != null ? `<span class="hint">(${prof.settled}경주 정산)</span>` : ''}
      </div>
      <div style="margin-bottom:6px">
        ${cnt('✅', '성공 패턴', kl)}${cnt('❌', '실패 원인', mo)}${cnt('💡', '새 패턴', pd)}${cnt('🔧', '시스템 개선', si)}
      </div>
      ${focus.length ? `<div style="margin:8px 0 4px"><b>🎯 내일 집중할 것</b><ul style="margin:4px 0 0 18px;padding:0">${focus.map((f) => `<li>${esc(f)}</li>`).join('')}</ul></div>` : ''}
      <div style="margin-top:8px"><b>📊 누적 패턴 신뢰도</b>
        ${relRow('마감급락', rel['마감급락'])}
        ${relRow('쌍승역전', rel['쌍승역전'])}
        ${relRow('전적이중수렴', rel['전적이중수렴'])}
        ${relRow('추천종합', rel['추천종합'])}
      </div>
    </div>`;
  }

  // [고배당 심층분석] 복승30+/삼복승100+ 미적중 A/B/C 분류 + 개선 후 예상 적중
  // [수동 케이스 학습 복기] 카와사키 11R 등 사용자 지정 놓친 케이스를 추천근거·왜 놓쳤나·새 규칙으로 상세 표시.
  function renderHighOddsCases(hc) {
    const cases = (hc && hc.cases) || [];
    if (!cases.length) return '';
    const nl2br = (t) => esc(t || '').replace(/\n/g, '<br>');
    const cards = cases.map((c) => {
      const rd = c.review_detail || {};
      const block = (title, body, color) => body ? `<div style="margin:5px 0;padding:6px 9px;background:rgba(255,255,255,.03);border-left:3px solid ${color};border-radius:6px">
        <div style="font-weight:700;color:${color};font-size:12px;margin-bottom:2px">${title}</div>
        <div class="hint" style="line-height:1.6">${nl2br(body)}</div></div>` : '';
      return `<div style="margin:8px 0;padding:10px 12px;border:1px solid #4a3a2a;border-radius:9px;background:rgba(239,68,68,.05)">
        <div style="font-weight:800;font-size:14px;color:#fca5a5">🚨 ${esc(c.race || '')} <span class="hint" style="font-weight:400">${esc(c.date || '')} · 결과 ${esc(c.result || '')}</span></div>
        <div style="margin:3px 0;font-size:12px">놓친 말 <b style="color:#fca5a5">${c.missed_horse != null ? c.missed_horse + '번' : '-'}</b> · 신호 <b>${esc(c.signal_type || '')}</b> (${esc(c.signal_time || '')} · ${esc(c.signal_detail || '')})</div>
        ${block('📋 추천 근거 상세', rd.recommend_basis, '#38d39f')}
        ${block('❓ 왜 5번을 삼복승에 넣었나', rd.why_trifecta_5, '#7dd3fc')}
        ${block('⚠️ 왜 놓쳤나', rd.why_missed_detail || c.why_missed, '#fbbf24')}
        <div style="margin:6px 0 0;padding:6px 9px;background:rgba(56,211,159,.1);border-radius:6px">
          <b style="color:#38d39f">✅ 새 규칙 적용됨</b>: ${esc(c.new_rule || '')}${c.rule_applied ? ' <span class="chip" style="border-color:#38d39f;color:#38d39f">코드 반영</span>' : ''}
          <div class="hint" style="font-size:11px;margin-top:2px">💡 교훈: ${esc(c.lesson || '')}</div></div>
      </div>`;
    }).join('');
    return `<div class="bet-box" style="margin:6px 0;padding:12px 14px;border:1px solid #5a2a2a;border-radius:10px">
      <div style="font-weight:700;font-size:15px;margin-bottom:6px">🎓 놓친 케이스 학습 복기 <span class="hint" style="font-weight:400">(사용자 지정 · 새 규칙 도출)</span></div>
      ${cards}</div>`;
  }

  function renderHighOddsReview(ho) {
    if (!ho || ho.error || !ho.total) return '';
    const abc = ho.abc || {};
    const abcColor = { A: '#9aa4b2', B: '#f5c451', C: '#ff8a5c' };
    const bar = (k) => {
      const g = abc[k] || {};
      return `<div style="display:flex;align-items:center;gap:8px;padding:2px 0">
        <span style="min-width:74px">유형${k}</span>
        <span style="flex:1;height:14px;background:#1e293b;border-radius:7px;overflow:hidden">
          <span style="display:block;height:100%;width:${g.pct || 0}%;background:${abcColor[k]}"></span></span>
        <span style="min-width:120px;text-align:right"><b>${g.count || 0}건</b> (${g.pct || 0}%) <span class="hint">${esc(g.label || '')}</span></span>
      </div>`;
    };
    const misses = (ho.recent_misses || []).slice(0, 8).map((m) => {
      const tag = m.catchable ? '<span style="color:#38d39f">개선가능</span>' : '<span class="hint">구조적</span>';
      const od = (m.odds && m.odds.quinella) ? `복승 ${m.odds.quinella}배` : '';
      return `<div style="padding:3px 0;border-top:1px solid #222c3c">
        <b>${esc(m.race || '')}</b> <span class="hint">${esc(m.date || '')}</span> = ${esc(m.result || '')}
        · <span style="color:${abcColor[m.abc] || '#9aa4b2'}">유형${esc(m.abc || '?')}</span> ${esc(m.fail || '')} · ${od} · ${tag}</div>`;
    }).join('');
    return `<div class="bet-box" style="margin:6px 0;padding:12px 14px;border:1px solid #3a4a2a;border-radius:10px">
      <div style="font-weight:700;font-size:15px;margin-bottom:8px">💎 고배당 미적중 심층 분석 <span class="hint" style="font-weight:400">(복승30배+/삼복승100배+)</span></div>
      <div style="margin-bottom:8px">
        총 <b>${ho.total}</b>경주 · 적중 <b style="color:#38d39f">${ho.hits}</b> · 미적중 <b style="color:#ff6b6b">${ho.misses}</b>
        <span class="hint">(적중률 ${ho.hit_rate}%)</span>
      </div>
      <div style="margin-bottom:6px"><b>못 잡은 이유 분류</b>
        ${bar('A')}${bar('B')}${bar('C')}
      </div>
      <div style="margin:8px 0;padding:8px 10px;background:rgba(56,211,159,.1);border-radius:7px">
        🔧 <b>개선 후 예상 추가 적중: +${ho.expected_additional_hits || 0}경주</b>
        <span class="hint">(유형B+C 해결 시)</span> → 예상 적중률 <b style="color:#38d39f">${ho.projected_hit_rate}%</b>
      </div>
      ${misses ? `<div style="margin-top:6px"><b>최근 미적중 고배당</b>${misses}</div>` : ''}
    </div>`;
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
  function renderCompareStats(cs, iw, bw) {
    if (!cs) return '';
    const any = ['anomaly', 'form', 'jockey', 'final'].some((k) => cs[k] && cs[k].n);
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
    // [3번] 근거별 신뢰 가중치(전적/배당/기수 적중률 → 정규화). 가장 신뢰할 근거 강조.
    let bwNote = '';
    if (bw && bw.top) {
      const label = { form: '🏇 전적', anomaly: '🚨 배당(이상감지)', jockey: '🧑‍💼 기수' };
      const parts = ['form', 'anomaly', 'jockey'].filter((k) => bw[k] != null)
        .map((k) => `${label[k]} <b>${Math.round(bw[k] * 100)}%</b>${(bw.rates && bw.rates[k] != null) ? ` <span class="hint">(적중 ${bw.rates[k]}%)</span>` : ''}`);
      bwNote = `<div class="hint" style="margin:4px 0 8px;color:#38d39f">🧠 <b>근거별 신뢰 가중치</b> — ${parts.join(' · ')} → 가장 신뢰: <b>${label[bw.top] || bw.top}</b> (근거별 적중률로 자동 조정)</div>`;
    }
    return `<div class="bet-box" style="display:block;margin:4px 0 10px">
      <b>🆚 근거별 추천 적중률 비교 (전적 · 배당 · 기수)</b> <span class="hint" style="font-weight:400">(복승 top2 정확 또는 삼복승 top3 정확 기준)</span>
      ${wNote}${bwNote}
      <div style="margin-top:2px">
        ${cell('🚨 이상감지(배당) 기반', cs.anomaly, '#ff9f43')}
        ${cell('🏇 전적 기반', cs.form, '#4ea1ff')}
        ${cell('🧑‍💼 기수 기반', cs.jockey, '#c084fc')}
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

  /** [경고 시스템 5번] 고배당 경고 신호 적중률 통계 카드. */
  function renderAlertStats(as) {
    if (!as || !as.n) return '';
    const rate = as.hit_rate != null ? as.hit_rate : (as.n ? Math.round((as.hit / as.n) * 1000) / 10 : 0);
    const color = rate >= 60 ? '#ff8a3d' : rate >= 40 ? '#ffd24f' : '#8a94a6';
    return `<div class="bet-box" style="display:block;margin:4px 0 10px;border-color:#ffb020">
      <b>⚠️ 경고 신호 분석 <span class="hint" style="font-weight:400">(배당 30%↑ 급락 = 고배당 이상감지)</span></b><br>
      <div style="margin-top:4px">경고 발생: <b style="font-size:18px">${as.n}회</b> · 경고 말 입상: <b style="font-size:18px;color:${color}">${as.hit}회 (${rate}%)</b></div>
      <div class="hint" style="margin-top:3px">경고 무시 후 미적중: <b style="color:#ff6b6b">${as.ignored_miss || 0}회</b> <span style="font-weight:400">(경고 말을 넣었으면 적중했을 케이스)</span></div>
      <div style="margin-top:5px;color:#ffd24f">결론: ${esc(as.advice || '데이터 축적 중')}</div>
    </div>`;
  }

  /** [3번] 마감 후 대급락 → 실제 입상률 통계 카드(패턴 신뢰도 측정). */
  function renderAfterCloseStats(acs) {
    if (!acs || !acs.total_judged) {
      if (acs && acs.pending) return `<div class="bet-box" style="display:block;margin:4px 0 10px;border-color:#f59e0b"><b>⚡ 마감 후 대급락 패턴</b><br><span class="hint">판정 대기 ${acs.pending}건 — 결과가 입력되면 입상률이 집계됩니다.</span></div>`;
      return '';
    }
    const rate = acs.hit_rate;
    const color = acs.reliable ? '#38d39f' : rate >= 40 ? '#ffd24f' : '#8a94a6';
    const recent = (acs.recent || []).slice(0, 5).map((c) => {
      const hit = c.hit ? `<span style="color:#38d39f">✅ ${(c.hitHorses || []).join('·')}번 입상</span>` : '<span class="hint">미입상</span>';
      return `<div style="margin:2px 0;font-size:13px">${esc(c.raceKey || '')} · 대급락 ${(c.horses || []).join('·')}번 → ${hit}</div>`;
    }).join('');
    return `<div class="bet-box" style="display:block;margin:4px 0 10px;border-color:#f59e0b">
      <b>⚡ 마감 후 대급락 → 실제 입상률 <span class="hint" style="font-weight:400">(발주 후 50%+ 급락말의 신뢰도)</span></b><br>
      <div style="margin-top:4px">판정 <b style="font-size:18px">${acs.total_judged}건</b> · 입상률 <b style="font-size:20px;color:${color}">${rate}%</b> <span class="hint">(${acs.hits}건 적중)</span>${acs.pending ? ` · <span class="hint">대기 ${acs.pending}건</span>` : ''}</div>
      ${acs.reliable ? '<div style="margin-top:4px;color:#38d39f;font-weight:700">→ 신뢰 가능 패턴(표본 5+·50%+) — 다음 경주 같은 패턴 발생 시 참고</div>' : '<div class="hint" style="margin-top:4px">표본 5건 이상 쌓이면 신뢰도 판정</div>'}
      ${recent ? `<div style="margin-top:5px;border-top:1px dashed var(--border);padding-top:4px">${recent}</div>` : ''}
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
      let head = '<tr><th class="corner">1착↓ / 2착→</th>' + nos.map((n) => `<th>${n}</th>`).join('') + '</tr>';
      let body = '';
      for (const rn of nos) {
        let tds = '';
        for (const cn of nos) { if (rn === cn) { tds += '<td class="diag">—</td>'; continue; } const v = x[`${rn}>${cn}`]; tds += v > 0 ? `<td class="cell" style="background:${heatColor(v, lo, hi)}" title="${rn}번 1착·${cn}번 2착 = ${v}배">${v}</td>` : '<td class="empty">·</td>'; }
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

    // [결정론] pastRaces 기반 강제 편성(2착패턴) — 있을 때만
    const det = report.deterministic;
    if (det && (det.mustTrifecta || []).length) {
      add(el, 'bet-box', `<h3>🔒 결정론 강제 편성 (전적 기반)</h3>
        ${det.mustTrifecta.map((m) => `<div class="bet-line"><span class="bet-type">${m.no}번 ${esc(m.name)}</span><span>${esc(m.msg)}</span></div>`).join('')}
        <p class="hint">동일 거리/코스 2착 2회↑ 마필을 삼복승에 결정론적으로 강제 포함했습니다(AI 추천과 별개 보험).</p>`);
    }

    // 종합 분석
    if (report.analysis) add(el, 'panel-card', `<h3>종합 분석</h3><p class="hint">${esc(report.analysis)}</p>`);
  }

  function add(parent, cls, html) { const d = document.createElement('div'); d.className = cls; d.innerHTML = html; parent.appendChild(d); }

  // ---------- 전적 자동 점수 (Phase 3) ----------
  /** 추출된 출전마 → /api/score 호출 → 점수·등급 패널 렌더 */
  async function renderFormScores(race, sheet) {
    const raceCtx = { distance: (race && race.distance) || '', course: '', grade: '' };
    const horses = (sheet.horses || []).map((h) => {
      const pr = h.pastRaces || [];
      return {
        no: h.horseNum, name: h.horseName, jockey: h.jockey, weight: h.weight,
        recentPlacings: h.recentPlacings || [],
        pastRaces: pr,                                    // [2~6] 거리/기수/부담중량/2착패턴 활성화
        lastJockey: pr[0] && pr[0].jockey,
        lastWeight: pr[0] && pr[0].weight,
        jockey3mPlaceRate: (state.jockeyStats[h.jockey] || {}).placeRate,
      };
    });
    if (!horses.length) return null;
    const res = await Analysis.scoreHorses(raceCtx, horses, state.jockeyStats || {});   // [보완] 기수교체 보너스 활성
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
        <td>${h.distanceBonus ? (h.distanceBonus > 0 ? '+' : '') + h.distanceBonus : '-'}</td>
        <td>${h.jockeyChangeBonus ? (h.jockeyChangeBonus > 0 ? '+' : '') + h.jockeyChangeBonus : '-'}</td>
        <td>${h.weightBonus ? (h.weightBonus > 0 ? '+' : '') + h.weightBonus : '-'}</td>
        <td>${esc(h.runningStyle || '-')}</td>
        <td><b>${h.totalScore}</b></td>
        <td>${badge(h.grade)}${up}</td>
        <td>${flags || ''}</td>
      </tr>`;
    }).join('');
    const el = document.createElement('div');
    el.className = 'panel-card';
    el.innerHTML = `<h3>📊 전적 자동 점수 · 등급 (Phase 3)</h3>
      <p class="hint">전적 가중평균(3-1) + 코스적성(3-2) + 기수보너스(3-3) + 거리변화·기수교체·부담중량(pastRaces) → 총점 · 사분위 등급(3-5, 이상감지 보정 포함) · 특수플래그(3-4)</p>
      <table class="data-table">
        <thead><tr><th>마번</th><th>마명</th><th>기수</th><th>최근착순</th><th>전적</th><th>코스</th><th>기수</th><th>거리</th><th>기수교체</th><th>부담</th><th>각질</th><th>총점</th><th>등급</th><th>플래그</th></tr></thead>
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
    renderKoreaFormDraft(title);   // [복구] 배당 연동 전에도 전적 기준 통합 초안(유력마 TOP5·조합)을 즉시 표시
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
      // [복구·오매칭 방어] 매칭 실패 시 통합영역을 빈칸으로 지우는 대신 '전적 기준 초안'으로 되돌린다.
      //   → 한국 탭에 외국 경주 분석이 잔존하지 않으면서(초안은 이 경주 PDF 전적만 사용) 통합 화면은 항상 유지.
      renderKoreaFormDraft(title);
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
    const signals = (a.signals || []).filter((s) => s.type === '급락' || s.type === '단승급락' || s.type === '역전' || s.type === '대규모급락');
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
    // [저/고배당 분리] 고배당(직전 50배+) 급락은 절대값이 아닌 %로만 판단 → 하단 '참고' 블록.
    //   저배당 급락·비급락 신호는 상단(기존). 30배 이상 제외는 적용 안 함(모든 급락 유지).
    const low = rel.filter((s) => !s.highOdds);
    const high = rel.filter((s) => s.highOdds);
    let mainHtml = '';
    if (low.length) {
      const cnt = { 상: 0, 중: 0, 하: 0 };
      low.forEach((s) => { cnt[_signalTier(s.level).key]++; });
      const chip = (k, icon, color) => cnt[k] ? `<b style="color:${color}">${icon} ${k} ${cnt[k]}</b>` : '';
      const summary = ['상', '중', '하'].map((k) => {
        const t = _signalTier(k === '상' ? '🔴' : k === '중' ? '🟠' : '🟡');
        return chip(k, t.icon, t.color);
      }).filter(Boolean).join(' · ') || '<span class="hint">없음</span>';
      const sorted = low.slice().sort((a, b) => _signalTier(b.level).rank - _signalTier(a.level).rank);
      const shown = sorted.slice(0, 3).map((s) => {
        const t = _signalTier(s.level);
        return `<div style="margin:2px 0" title="${esc(s.detail || '')}"><b style="color:${t.color}">${t.icon}</b> ${esc(s.text)}</div>`;
      }).join('');
      const more = sorted.length > 3 ? `<div class="hint">외 ${sorted.length - 3}건 (마우스 올리면 사유 표시)</div>` : '';
      mainHtml = `<div class="matrix-title" style="font-size:13px;margin-top:8px">${title} · 종합 ${summary}</div>${shown}${more}`;
    }
    let highHtml = '';
    if (high.length) {
      const hs = high.slice().sort((a, b) => (a.dropPct || 0) - (b.dropPct || 0));   // 급락폭 큰(음수 작은) 순
      const rows = hs.slice(0, 4).map((s) => {
        const strong = (s.dropPct != null && s.dropPct <= -40);
        const tag = strong ? '<span style="color:#ffd24f;font-weight:700">주목</span>' : '<span class="hint">약한</span>';
        const combo = (s.combo || []).join('+');
        return `<div style="margin:2px 0;font-size:12px;opacity:${strong ? 1 : 0.7}">${combo} ${s.oddsBefore}배→${s.oddsAfter}배 (${s.dropPct}%) ${tag}</div>`;
      }).join('');
      highHtml = `<div class="matrix-title" style="font-size:12px;margin-top:8px;color:#8a94a6">📊 고배당 급락 신호 (참고) <span class="hint" style="font-weight:400">50배+ · %로 판단</span></div>${rows}`;
    }
    return mainHtml + highHtml;
  }

  function renderKoreaSignals(signals) {
    return renderSignalsSimple(signals, '⏱ 마감 임박 급락', (s) => s.type === '마감급락');
  }

  /** [배당 연동 전·복구] PDF 전적 기준 통합 초안 — 유력마 TOP5 + 조합 + 2착패턴을 즉시 표시.
   *  배당이 매칭되기 전에도 '한 화면 통합 분석'이 항상 보이게 한다(배당 연동 시 renderKoreaIntegrated 로 고도화). */
  function renderKoreaFormDraft(title) {
    const host = $('#koreaIntegrated'); if (!host) return;
    const sc = state.koreaScored[title];
    const report = state.lastReports[title];
    if (!sc || !sc.form || !sc.form.length) { host.innerHTML = ''; return; }
    const gc = { A: '#38d39f', B: '#4ea1ff', C: '#ffd24f', D: '#8a94a6' };
    const gradeBy = {};
    ((report && report.horses) || []).forEach((h) => { gradeBy[h.no] = h.grade; });
    const top = sc.form.slice().sort((a, b) => (b.formScore || 0) - (a.formScore || 0)).slice(0, 5);
    const rows = top.map((h, i) => {
      const g = gradeBy[h.no] || (i === 0 ? 'A' : i < 2 ? 'B' : i < 4 ? 'C' : 'D');
      return `<tr><td><b style="color:${gc[g] || '#fff'}">${g}</b></td><td>${h.no}</td><td>${esc(h.name || '')}</td>
        <td>${esc(h.jockey || '')}</td><td><b>${h.formScore != null ? h.formScore : '-'}</b></td>
        <td>${(h.recentPlacings || []).join('·') || '-'}</td></tr>`;
    }).join('');
    const bd = (report && report.betting_recommend) || {};
    const betLine = (arr, label) => (arr || []).map((b) =>
      `<div class="bet-line"><span><span class="bet-type">${label}</span> ${(b.combo || []).join('-')}</span><span class="hint">신뢰도 ${b.confidence != null ? b.confidence + '%' : '-'} · ${esc(b.note || '')}</span></div>`).join('');
    const bets = betLine(bd.quinella, '복승') + betLine(bd.trifecta, '삼복승');
    const p2 = (report && report.pattern2_horses) || [];
    host.innerHTML = `<div class="panel-card">
      <h3>🔗 통합 분석 (전적 기준 초안) <span class="hint" style="font-weight:400">배당 연동 대기 중</span></h3>
      <div class="hint" style="padding:6px 9px;background:rgba(245,158,11,.1);border-left:3px solid #f59e0b;border-radius:6px;color:#f59e0b;margin-bottom:8px">⏳ 배당판을 수집·매칭하면 <b>이상감지·역배열·통합등급(전적40%+배당60%)</b>으로 이 화면이 자동 고도화됩니다.</div>
      <div class="matrix-title" style="font-size:13px">⭐ 유력마 TOP5 (전적 기준)</div>
      <table class="data-table" style="margin-top:4px">
        <thead><tr><th>등급</th><th>마번</th><th>마명</th><th>기수</th><th>전적점수</th><th>최근착순</th></tr></thead>
        <tbody>${rows}</tbody></table>
      ${p2.length ? `<div style="margin:6px 0"><span class="hint">🎯 2착패턴</span> ${p2.map(esc).join(', ')}</div>` : ''}
      ${bets ? `<div class="matrix-title" style="font-size:13px;margin-top:8px">💰 추천 조합 (전적 기준)</div>${bets}` : '<div class="hint" style="margin-top:6px">추천 조합: PDF 분석 리포트 참고</div>'}
    </div>`;
  }

  /** 통합분석 결과(제거분석·유력마·통합등급·베팅·마감급락) 렌더 — 한글 데이터 그대로 */
  function renderKoreaIntegrated(a) {
    const host = $('#koreaIntegrated'); if (!host) return;
    if (!a || a.error || a.waiting) { host.innerHTML = ''; return; }
    state.koreaLastInteg = a;   // [1번] 예산 변경 시 베팅 금액 재계산용
    // [유력마 통일] ⭐유력마 라인도 TOP5와 동일 기준(복승 대표배당 낮은 순 + 이상감지 상위)으로 정렬 표시.
    const keyH = _marketOrderNos(a, (a.keyHorses || []).map(Number)).map((h) => `<b style="color:#4ea1ff">${h}</b>`).join(' · ');
    // [1번] 제거분석 패널 재사용: id 충돌 방지 위해 패널 id 치환. 아래에서 클릭 토글 핸들러 연결.
    const elimHtml = renderEliminationHTML(a.elimination).replace('id="elimPanel"', 'id="koreaElimPanel"');
    // [실시간 배당 분석·편의] 일본 탭에만 있던 실시간 표시(마감 N분전 배지·급락 경고 배너·추천 근거)를
    //   한국 탭 통합 뷰에도 표시 → 한국경마 실시간 배당 분석을 한 화면에서 편하게 확인.
    const closeTag = a.afterClose ? ' · <b style="color:#8a94a6">마감 후(참고만)</b>'
      : (a.minutesBefore != null ? ` · <b style="color:#ffd24f">마감 ${a.minutesBefore}분전</b>` : '');
    host.innerHTML = `<div class="panel-card">
      <h3>🔗 통합 분석 결과 <span class="hint" style="font-weight:400">${esc(a.raceKey || '')}${closeTag}</span></h3>
      ${renderAlertSignal(a.alertSignal, _horseRoleMap(a))}
      ${renderMarketFavorites(a)}
      ${renderRealtimeAdded(a)}
      ${renderDarkHighlight(a)}
      ${renderKoreaIntegratedTable(a.integrated)}
      ${renderBmedMatrixPanel(a)}
      <div style="margin:8px 0"><span class="hint">⭐ 유력마</span> ${keyH || '—'}${a.anomalyHorse != null ? ` <span class="hint">/ 이상감지말</span> <b style="color:#ff5c5c">${a.anomalyHorse}</b>` : ''}</div>
      ${elimHtml}
      ${renderKoreaSignals(a.signals)}
      ${renderPatternMatch(a.patternMatch)}
      ${renderBetRecommend(a, '#koreaBudget')}
      ${renderDutchCalc(a)}
      ${renderRecommendBasis(a.recommendBasis)}
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
    // [종목 혼재 긴급수정] 일본경마 탭은 sport=horse 만 요청 → 경륜(cycle)·경정·바이크 배당 절대 혼입 안 됨.
    try { latest = await (await fetch('/api/odds/triple/latest?sport=horse')).json(); }
    catch (_) { return; }
    const latestRk = latest && latest.raceKey;
    // [3번·오늘 개최 없음] 일본경마 경주가 없으면(noRace) 타종목 표시 없이 '개최 없음' 대기(이전 경마 데이터는 서버 보존).
    if (latest && latest.noRace) { setJpOddsStatus('waiting'); return; }
    // [경주전환 잔존 방어] 수집 경주 없음 또는 30분+ 미갱신(끝난 경주) → 직전 배당 표시 안 함
    if (!latestRk || latest.stale) { setJpOddsStatus('waiting'); return; }
    // [경주 자동 전환 긴급 수정] 고정/다른 경마장이면 현재 보고 있는 경주(_rrLastRk) 유지 → 오비히로로 강제 전환 차단.
    //   30초 폴링(pollJapanOdds)이 전역 최신 경주를 무조건 렌더하던 게 '계속 전환'의 진짜 원인.
    const rk = _targetRaceKey(latestRk);
    if (!rk) { setJpOddsStatus('waiting'); return; }
    if (rk !== latestRk) {
      // 최신은 다른 경주지만 고정/다른장이라 전환 안 함 — 상단 바에 감지만 표시(화면은 현재 경주 유지).
      const st = $('#rrStatus');
      if (st) st.textContent = _racePinned
        ? `📌 고정 중 · 다른 경주 감지(${latestRk}) — 전환하려면 고정 해제`
        : `🔀 다른 경마장 경주 감지(${latestRk}) — 전환하려면 새로고침`;
    }
    // [탭분리·제주케이스] 한국경마(서울/부산/부경/제주/과천)는 한국경마 탭에서 실시간 배당 분석을 표시한다.
    //   → 일본경마 탭에서는 한국 경주를 렌더하지 않는다(같은 경주가 양 탭에 중복 표시되던 문제 방지).
    if (jpIsKoreaName(rk)) { setJpOddsStatus('waiting'); return; }
    let a;
    try {
      a = await (await fetch('/api/odds/triple/analyze', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ raceKey: rk }),
      })).json();
    } catch (_) { return; }
    if (!a || a.error || a.waiting) { setJpOddsStatus('waiting'); return; }
    // [2번] raceKey 검증: 분석 응답이 요청한 경주와 다르면(직전 경주 잔존) 무시
    if (a.raceKey && a.raceKey !== rk) { setJpOddsStatus('waiting'); return; }
    // [종목 혼재 이중가드] 분석 종목이 경마(horse)가 아니면(경륜/경정/바이크) 일본경마 탭에 렌더 안 함.
    //   → 서버 sport 필터를 통과 못한 예외 데이터도 화면 혼입 원천 차단. 타종목은 mirrorSportAnalysis 로 각 탭 표시.
    if (a.sport && a.sport !== 'horse') { setJpOddsStatus('waiting'); mirrorSportAnalysis(a); return; }
    setJpOddsStatus('linked', rk);
    renderJapanIntegrated(a);
    onJapanOddsUpdate(rk, a);
    mirrorSportAnalysis(a);   // [탭분리] 경정/경륜/바이크/중앙경마 탭에 종목별 라이브 분석 미러링
  }

  // [탭분리] category → 분석기 스포츠 탭(1:1). 현재 경주 종목과 일치하는 탭만 채운다.
  const SPORT_TAB_CAT = { boat: 'boat', cycle: 'cycle', bike: 'bike', central: 'japan_central' };
  let _lastSportAnalyze = null;
  function mirrorSportAnalysis(a) {
    if (!a || a.error || a.waiting) return;
    _lastSportAnalyze = a;
    for (const [tab, cat] of Object.entries(SPORT_TAB_CAT)) {
      if (cat !== a.category) continue;
      const el = document.getElementById('sportReport-' + tab); if (!el) continue;
      el.innerHTML = sportAnalysisHTML(a, '#sportBudget-' + tab);
    }
  }

  // [탭분리·화면 복구] 경륜/경정/바이크 탭은 일본경마(sport=horse) 폴링과 독립적으로 각 종목 배당을 직접 조회해
  //   실시간 이상감지·유력마·복병·추천·타임라인을 sportReport-<탭>에 렌더한다.
  //   ⚠ sport=horse 격리(종목 혼재 방지) 이후, mirrorSportAnalysis 를 부르던 유일 경로(pollJapanOdds)가
  //     경마 경주 없으면 조기 return 하여 경륜/경정/바이크 탭이 플레이스홀더로 비던 문제 복구.
  //   각 탭이 자기 sport 만 조회 + category 일치 확인하므로 종목 혼재는 재발하지 않는다(기존 격리 원칙 유지).
  const _SPORT_POLL_LIST = [
    { sport: 'cycle', cat: 'cycle' },
    { sport: 'boat', cat: 'boat' },
    { sport: 'bike', cat: 'bike' },
  ];
  let _sportOddsTimer = null;
  async function pollSportOdds() {
    for (const s of _SPORT_POLL_LIST) {
      try {
        const latest = await (await fetch('/api/odds/triple/latest?sport=' + s.sport)).json();
        if (!latest || latest.noRace || latest.stale || !latest.raceKey) continue;   // 개최 없음·끝난 경주 스킵
        const a = await (await fetch('/api/odds/triple/analyze', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ raceKey: latest.raceKey, sport: s.sport }),
        })).json();
        if (!a || a.error || a.waiting) continue;
        if (a.raceKey && a.raceKey !== latest.raceKey) continue;   // 직전 경주 잔존 방어
        if (a.category && a.category !== s.cat) continue;          // 종목 일치만 렌더(혼재 방지)
        mirrorSportAnalysis(a);
      } catch (_) { /* 개별 종목 실패는 조용히 건너뜀(다른 종목 영향 없음) */ }
    }
  }
  function stopSportOddsWatch() { if (_sportOddsTimer) { clearInterval(_sportOddsTimer); _sportOddsTimer = null; } }
  function startSportOddsWatch() {
    stopSportOddsWatch();
    pollSportOdds();                                    // 진입 즉시 1회
    _sportOddsTimer = setInterval(pollSportOdds, 30000);   // 30초 폴링(확장 주기와 정렬)
  }
  // 순수 렌더 헬퍼만 조합(사이드이펙트 없는 문자열 반환) → 스포츠 탭 컨테이너에 안전하게 주입.
  // [💎 중고배당 유력마·2번] 감지 시 상단 강조 배너 + 소리/깜빡임(경고음과 다른 소리·경주당 1회).
  let _midHighAlerted = {};
  function renderMidHighFavorites(a) {
    const mh = (a && a.midHighFavorites) || [];
    if (!mh.length) return '';
    // 새 감지 알림(경주+마번 조합 1회) — 소리(고배당 전용)+화면 깜빡임
    const rk = a.raceKey || '';
    const fresh = mh.filter((m) => !_midHighAlerted[rk + '#' + m.no]);
    if (fresh.length && !a.afterClose) {
      fresh.forEach((m) => { _midHighAlerted[rk + '#' + m.no] = 1; });
      try { _playMidHighAlert(); } catch (_) { /* */ }
      try { _flashScreen('#f0abfc'); } catch (_) { /* */ }
    }
    const rows = mh.map((m) => `<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:6px 10px;border-radius:8px;margin:3px 0;background:rgba(240,171,252,.12);border-left:4px solid #f0abfc">
      <b style="font-size:15px;color:#f0abfc">💎 ${m.no}번</b>
      <b style="color:#e2e8f0">${m.odds}배</b>
      ${(m.signals || []).map((s) => `<span class="chip" style="border-color:#f0abfc;color:#f0abfc;font-weight:700">${esc(s.text)}</span>`).join('')}
      <span class="hint" style="margin-left:auto;color:#f0abfc">→ 삼복승 보험 필수</span>
    </div>`).join('');
    return `<div style="margin:6px 0;padding:8px 10px;border:2px solid #f0abfc;border-radius:10px;background:linear-gradient(180deg,rgba(240,171,252,.10),rgba(20,28,43,.6))">
      <div style="font-size:15px;font-weight:800;color:#f0abfc">💎 고배당 유력마 감지! <span class="hint" style="font-weight:400;font-size:11px">복승 10배+ & 강한 신호</span></div>
      ${rows}
    </div>`;
  }
  // [2번] 중고배당 전용 알림음(기존 경고음과 다른 톤) — 상승 아르페지오
  function _playMidHighAlert() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      [880, 1174, 1568].forEach((f, i) => {
        const o = ctx.createOscillator(), g = ctx.createGain();
        o.type = 'triangle'; o.frequency.value = f;
        o.connect(g); g.connect(ctx.destination);
        const t = ctx.currentTime + i * 0.12;
        g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.25, t + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t + 0.15);
        o.start(t); o.stop(t + 0.16);
      });
    } catch (_) { /* */ }
  }
  function _flashScreen(color) {
    const ov = document.createElement('div');
    ov.style.cssText = `position:fixed;inset:0;background:${color};opacity:.35;z-index:99999;pointer-events:none;transition:opacity .5s`;
    document.body.appendChild(ov);
    setTimeout(() => { ov.style.opacity = '0'; }, 120);
    setTimeout(() => { try { ov.remove(); } catch (_) { /* */ } }, 700);
  }

  // [5번] 활성 신호별 과거 적중률 강조 — "이 신호(스마트머니) 과거 적중률 72% → 신뢰도 높음"
  function renderSignalReliability(a) {
    const rel = a && a.signalReliability;
    if (!rel || !Object.keys(rel).length) return '';
    const rows = Object.entries(rel).map(([name, e]) => {
      const col = (e.reliable && e.rate_quinella >= 50) ? '#38d39f' : (e.reliable ? '#f87171' : '#fbbf24');
      return `<span class="chip" style="border-color:${col};color:${col};font-weight:700" title="복승 ${e.rate_quinella}% · 삼복승 ${e.rate_trifecta}% (${e.count}건)">${esc(name)} 적중률 ${e.rate_quinella}% · ${esc(e.note || '')}</span>`;
    }).join(' ');
    return `<div style="margin:4px 0"><span class="hint" style="font-size:11px">📊 이 경주 활성 신호 과거 성적: </span>${rows}</div>`;
  }

  // ═══ [네덜란드식 계산기·매트릭스판] 축/주력/복병/제거 상태 + 삼복승 매트릭스 + 자금분배 엔진(참고용·무삭제) ═══
  //  샘플(네덜란드.txt) 로직 통합: 삼복승 배당(실시간)·기존 추천 자동 반영(복승메인→AXIS+MAIN·복병/스마트머니→DARK·제거마→DROP).
  let _dutchState = {};   // rk -> {states:{no:'AXIS'|'MAIN'|'DARK'|'DROP'|'NONE'}, budget, inited}
  let _dutchLastA = null;
  const _DUTCH_CYCLE = ['NONE', 'AXIS', 'MAIN', 'DARK', 'DROP'];
  const _DUTCH_COL = { AXIS: '#e74c3c', MAIN: '#2ecc71', DARK: '#f1c40f', DROP: '#95a5a6', NONE: '#4a4a5a' };
  function _dutchDefaultBudget() {
    for (const sel of ['#jpBudget', '#tripleBudget', '#koreaBudget']) {
      const el = document.querySelector(sel); const v = el && parseInt(el.value || '0', 10);
      if (v > 0) return v;
    }
    return 100000;
  }
  function _dutchHorseNos(a) {
    let nos = (a.validHorses || []).map(Number);
    if (!nos.length) nos = [...new Set((a.betRecommend || []).flatMap((b) => (b.combo || []).map(Number)))];
    return nos.filter((n) => !isNaN(n)).sort((x, y) => x - y);
  }
  function _dutchTripleOdds(a) {
    const m = {};
    (a.betRecommend || []).forEach((b) => {
      if (b.kind === '삼복승') { const c = (b.combo || []).map(Number); if (c.length === 3 && b.expOdds) m[c.slice().sort((x, y) => x - y).join('-')] = b.expOdds; }
    });
    (a.trio || []).forEach((t) => { const c = (t.combo || t.pair || []).map(Number); const o = t.odds || t.expOdds; if (c.length === 3 && o) m[c.slice().sort((x, y) => x - y).join('-')] = o; });
    return m;
  }
  function _dutchExactaOdds(a) {
    const m = {};
    (a.exacta || []).forEach((x) => { const c = (x.combo || []).map(Number); const o = x.odds; if (c.length === 2 && o) m[c[0] + '➔' + c[1]] = o; });
    return m;
  }
  function _dutchInitStates(a, nos) {
    const st = {}; nos.forEach((n) => { st[n] = 'NONE'; });
    const kh = (a.keyHorses || []).map(Number);
    if (kh[0] != null && st[kh[0]] !== undefined) st[kh[0]] = 'AXIS';               // 복승 메인 첫째 → 축
    kh.slice(1).forEach((n) => { if (st[n] !== undefined && st[n] === 'NONE') st[n] = 'MAIN'; });   // 나머지 유력마 → 주력
    (a.darkHorses || []).forEach((h) => { const n = Number(h.no); if (st[n] !== undefined && st[n] === 'NONE') st[n] = 'DARK'; });   // 복병/스마트머니 → 복병
    (a.midHighFavorites || []).forEach((h) => { const n = Number(h.no); if (st[n] !== undefined && st[n] === 'NONE') st[n] = 'DARK'; });
    (a.eliminationStrong || []).forEach((e) => { const n = Number(e.no); if (st[n] !== undefined && st[n] === 'NONE') st[n] = 'DROP'; });   // 제거마 → 제거
    return st;
  }
  function renderDutchCalc(a) {
    _dutchLastA = a;
    const rk = a.raceKey || '';
    const nos = _dutchHorseNos(a);
    if (nos.length < 3) return '';
    _ensureDutchDelegation();
    let st = _dutchState[rk];
    if (!st) st = _dutchState[rk] = { states: {}, budget: _dutchDefaultBudget(), inited: false };
    if (!st.inited) { st.states = _dutchInitStates(a, nos); st.inited = true; }   // [연동] 기존 추천 자동 반영(1회)
    nos.forEach((n) => { if (st.states[n] === undefined) st.states[n] = 'NONE'; });
    return `<div id="dutchCalcPanel">${_dutchHtml(rk, a, nos, st)}</div>`;
  }
  function _dutchEngine(budget, nos, states, tripleOdds, twinOdds) {
    const axis = nos.find((n) => states[n] === 'AXIS');
    const mains = nos.filter((n) => states[n] === 'MAIN');
    const darks = nos.filter((n) => states[n] === 'DARK');
    const won = (n) => Math.round(n / 1000) * 1000;
    const portfolio = []; let insCost = 0;
    if (!axis || !mains.length) return { portfolio: [], warn: '축마(AXIS)+주력마(MAIN)를 최소 1마리씩 지정하세요.', ok: false, insCost: 0 };
    // [1단계] 복병(DARK) 삼복승 보험 — 축+주력+복병, 예산÷배당(본전 보전)
    darks.forEach((d) => mains.forEach((mn) => {
      const key = [axis, mn, d].sort((x, y) => x - y).join('-');
      if (tripleOdds[key]) { const o = tripleOdds[key]; const bet = won(budget / o); portfolio.push({ type: '복병보험(삼복)', code: '삼복 ' + key, odds: o, bet }); insCost += bet; }
    }));
    // [1단계] 쌍승 원금보험 — 축 포함 저배당 방향 최대 2건
    const twinCands = Object.entries(twinOdds).filter(([k]) => k.split('➔').map(Number).indexOf(axis) >= 0).sort((a2, b2) => a2[1] - b2[1]).slice(0, 2);
    twinCands.forEach(([code, o]) => { const bet = won(budget / o); portfolio.push({ type: '원금보험(쌍승)', code, odds: o, bet }); insCost += bet; });
    // [2단계] 오버플로우 방지
    if (insCost >= budget && insCost > 0) return { portfolio, warn: `보험 베팅금 합계(${insCost.toLocaleString()}원)가 예산 이상 — 배당 낮아 주력/본전 불가.`, ok: false, insCost };
    const remain = Math.max(0, budget - insCost);
    // [3단계] 잔액 → 핵심 메인 삼복승(축+주력2), 없으면 잔액 표기
    if (mains.length >= 2) {
      const key = [axis, mains[0], mains[1]].sort((x, y) => x - y).join('-');
      if (tripleOdds[key]) portfolio.push({ type: '🔥 핵심메인(삼복)', code: '삼복 ' + key, odds: tripleOdds[key], bet: remain });
      else portfolio.push({ type: '🔥 핵심메인(잔액)', code: `축${axis}+주력 ${mains[0]}·${mains[1]}`, odds: null, bet: remain });
    } else {
      portfolio.push({ type: '🔥 핵심메인(잔액)', code: `축${axis}+주력 ${mains[0]}`, odds: null, bet: remain });
    }
    return { portfolio, warn: '', ok: true, insCost, remain };
  }
  function _dutchHtml(rk, a, nos, st) {
    const budget = Math.max(0, st.budget || 0);
    const tripleOdds = _dutchTripleOdds(a);
    const twinOdds = _dutchExactaOdds(a);
    const axis = nos.find((n) => st.states[n] === 'AXIS');
    const btns = nos.map((n) => `<button class="dutch-horse" data-dh="${n}" style="padding:6px 10px;margin:3px;border:none;border-radius:4px;cursor:pointer;font-weight:700;color:${st.states[n] === 'DARK' ? '#000' : '#fff'};background:${_DUTCH_COL[st.states[n]]};${st.states[n] === 'DROP' ? 'opacity:.45' : ''}">${n}번 (${st.states[n]})</button>`).join('');
    let matrix;
    if (!axis) matrix = `<p style="color:#ff6b6b">축마(AXIS)를 1마리 지정하면 삼복승 매트릭스가 활성화됩니다.</p>`;
    else {
      let h = `<div style="overflow-x:auto"><table style="border-collapse:collapse;font-size:12px;margin-top:6px"><tr><th style="border:1px solid #444;padding:5px;background:#333">축${axis}</th>`;
      nos.forEach((c) => { h += `<th style="border:1px solid #444;padding:5px;background:#333">${c}</th>`; });
      h += '</tr>';
      nos.forEach((r) => {
        h += `<tr><th style="border:1px solid #444;padding:5px;background:#333">${r}</th>`;
        nos.forEach((c) => {
          if (r === c) { h += `<td style="border:1px solid #444;padding:5px;background:#3a2222">${r}</td>`; return; }
          if (r > c || r === axis || c === axis) { h += `<td style="border:1px solid #444;padding:5px;background:#111;color:#333">-</td>`; return; }
          if (st.states[r] === 'DROP' || st.states[c] === 'DROP') { h += `<td style="border:1px solid #444;padding:5px;background:#111;color:#666">DROP</td>`; return; }
          const key = [axis, r, c].sort((x, y) => x - y).join('-');
          const o = tripleOdds[key];
          let col = ''; if (st.states[r] === 'MAIN' && st.states[c] === 'MAIN') col = 'color:#2ecc71;font-weight:800'; if (st.states[r] === 'DARK' || st.states[c] === 'DARK') col = 'color:#f1c40f;font-weight:800';
          h += `<td style="border:1px solid #444;padding:5px;${col}">${o ? o + '배' : '-'}</td>`;
        });
        h += '</tr>';
      });
      h += '</table></div>';
      matrix = `<p style="color:#2ecc71;font-weight:700;margin:6px 0 0">[${axis}번] 축 고정 · 삼복승 우상단 매트릭스</p>${h}`;
    }
    const eng = _dutchEngine(budget, nos, st.states, tripleOdds, twinOdds);
    const results = (eng.portfolio || []).map((it) => {
      const payout = it.odds ? Math.floor(it.bet * it.odds) : null;
      const net = payout != null ? payout - budget : null;
      const pc = (net != null && net >= 0) ? '#2ecc71' : '#ff6b6b';
      return `<div style="display:flex;flex-wrap:wrap;gap:8px;justify-content:space-between;padding:6px 10px;margin:3px 0;border-radius:6px;background:rgba(52,73,94,.5)">
        <span><b>[${esc(it.type)}]</b> ${esc(it.code)} ${it.odds ? '(' + it.odds + '배)' : ''}</span>
        <span>추천 <b>${it.bet.toLocaleString()}원</b>${payout != null ? ` → 적중 ${payout.toLocaleString()}원 <span style="color:${pc}">(${net >= 0 ? '+' : ''}${net.toLocaleString()})</span>` : ''}</span>
      </div>`;
    }).join('');
    const warnHtml = eng.warn ? `<div style="margin:6px 0;padding:6px 10px;border:2px solid #ef4444;border-radius:6px;background:rgba(239,68,68,.12);color:#fca5a5;font-weight:700">⚠️ ${esc(eng.warn)}</div>` : '';
    return `<div style="margin:8px 0;padding:10px;border:2px solid #a855f7;border-radius:10px;background:linear-gradient(180deg,rgba(168,85,247,.08),rgba(20,28,43,.5))">
      <div style="font-size:15px;font-weight:800;color:#c084fc">💰 네덜란드식 계산기 <span class="hint" style="font-weight:400;font-size:11px">(참고용 · 기존 추천과 별개 · 마번 클릭: 일반→축→주력→복병→제거)</span></div>
      <div style="margin:6px 0">${btns}</div>
      <div style="display:flex;gap:8px;align-items:center;margin:6px 0">
        <span class="hint">예산</span>
        <input class="dutch-budget cfg-input" type="number" min="0" step="10000" value="${budget}" style="width:130px;padding:3px 6px"> <span class="hint">원</span>
      </div>
      ${matrix}
      <div style="margin-top:8px;font-weight:700;color:#c084fc">📊 자금 분배 포트폴리오</div>
      ${results || '<p class="hint">축마+주력마 지정 시 계산됩니다.</p>'}
      ${warnHtml}
      <div class="hint" style="font-size:11px;margin-top:4px">보험(복병 삼복·쌍승)=예산÷배당(본전 보전) · 잔액=핵심 메인(축+주력2). ⚠ 삼복승 실배당 미수집 시 '-'(추정배당만 표시).</div>
    </div>`;
  }
  function _dutchRefresh() {
    const panel = document.getElementById('dutchCalcPanel');
    if (!panel || !_dutchLastA) return;
    const rk = _dutchLastA.raceKey || '';
    const st = _dutchState[rk]; if (!st) return;
    panel.innerHTML = _dutchHtml(rk, _dutchLastA, _dutchHorseNos(_dutchLastA), st);
  }
  let _dutchDelegated = false;
  function _ensureDutchDelegation() {
    if (_dutchDelegated) return;
    _dutchDelegated = true;
    document.addEventListener('click', (e) => {
      const btn = e.target.closest && e.target.closest('.dutch-horse');
      if (!btn || !_dutchLastA) return;
      const rk = _dutchLastA.raceKey || '', no = parseInt(btn.dataset.dh, 10);
      const st = _dutchState[rk]; if (!st) return;
      let idx = _DUTCH_CYCLE.indexOf(st.states[no] || 'NONE');
      let nx = (idx + 1) % _DUTCH_CYCLE.length;
      // [축 유일] 축(AXIS)은 1마리만 — 이미 다른 축이 있으면 건너뜀
      if (_DUTCH_CYCLE[nx] === 'AXIS' && Object.keys(st.states).some((k) => st.states[k] === 'AXIS' && Number(k) !== no)) nx = (nx + 1) % _DUTCH_CYCLE.length;
      st.states[no] = _DUTCH_CYCLE[nx];
      _dutchRefresh();
    });
    document.addEventListener('input', (e) => {
      if (!e.target.classList || !e.target.classList.contains('dutch-budget') || !_dutchLastA) return;
      const rk = _dutchLastA.raceKey || '', st = _dutchState[rk]; if (!st) return;
      st.budget = Math.max(0, parseInt(e.target.value || '0', 10) || 0);
      _dutchRefresh();
    });
  }

  // [핵심 추천·추천 과다 근본정리] 최종 복승 ≤2 · 삼복승 ≤2 (총 4개)만 크게 표시 — 딱 이것만.
  //   서버 _final_picks가 모든 파생추천(확신도·복병·급락보존·스마트머니·밀집박스)을 4개로 압축(나머지 숨김·데이터는 보존).
  function renderCorePicks(a) {
    const cp = a && a.corePicks;
    if (!cp || a.recommendClosed) return '';
    let fq = cp.finalQuinellas || [];
    let ft = cp.finalTrifectas || [];
    // [폴백·구데이터] finalQuinellas 미보유 시 기존 confQuinellas/quinella·삼복승으로 대체
    if (!fq.length) {
      if ((cp.confQuinellas || []).length) fq = cp.confQuinellas.slice(0, 2);
      else if (cp.quinella && cp.quinella.length === 2) fq = [{ combo: cp.quinella, odds: cp.quinellaOdds }];
    }
    if (!ft.length) {
      const _t0 = cp.confTrifecta || cp.trifecta;
      if (_t0) ft = [{ combo: _t0, odds: cp.confTrifecta ? cp.confTrifectaOdds : cp.trifectaOdds }];
    }
    if (!fq.length) return '';
    const confHead = cp.confTop1 != null ? `<span class="hint" style="font-weight:400;font-size:11px">· 확신도 1위 ${cp.confTop1}번${cp.confTop1High ? ' 🔺고배당' : ''}</span>` : '';
    // [근거 기반 추천·두수별 개수] ★등급(★★★ 이중수렴/★★ 단일강신호/★ 참고) + 근거 + N두·복승개수 헤더
    const starStr = (n) => '★'.repeat(Math.max(0, Math.min(3, n || 0)));
    const nH = cp.raceHorseCount || 0;
    const maxQ = cp.quinellaMax || fq.length;
    const chaoticTag = cp.chaoticRace ? ' <span style="color:#fbbf24">· ⚠️ 혼전(+2)</span>' : '';
    const cntHead = nH ? `<div class="hint" style="font-size:12px;margin-bottom:4px">${nH}두 경주 · 복승 ${fq.length}개 추천 <span style="opacity:.7">(상한 ${maxQ})</span>${chaoticTag}</div>` : '';
    // [두수별 개수] 서버가 이미 상한(3~8)으로 캡 → 전부 표시(구데이터 폴백은 2개)
    const qLines = fq.map((q) => {
      const oo = q.odds != null ? `<span class="hint" style="font-size:13px">(${q.odds}배)</span>` : '';
      const st = q.stars ? ` <span style="color:#fbbf24;font-size:14px">${starStr(q.stars)}</span>` : '';
      const rs = q.reason ? ` <span class="hint" style="font-size:12px">· ${esc(q.reason)}</span>` : '';
      return `<div style="font-size:19px;font-weight:800;margin:5px 0">복승: <span style="color:#4ea1ff">${q.combo.join('+')}</span> ${oo}${st}${rs}</div>`;
    }).join('');
    const tLines = ft.slice(0, 2).map((t) => {
      const oo = t.odds != null ? `<span class="hint" style="font-size:13px">(${t.odds}배)</span>` : '';
      const rs = t.reason ? ` <span class="hint" style="font-size:12px">· ${esc(t.reason)}</span>` : '';
      return `<div style="font-size:18px;font-weight:800;margin:5px 0">🛡 삼복승 보험: <span style="color:#c084fc">${t.combo.join('+')}</span> ${oo}${rs}</div>`;
    }).join('');
    return `<div style="margin:6px 0;padding:14px;border:3px solid #38d39f;border-radius:12px;background:linear-gradient(180deg,rgba(56,211,159,.14),rgba(20,28,43,.92))">
      <div style="font-size:18px;font-weight:900;color:#38d39f;margin-bottom:4px">🎯 지금 사세요! <span class="hint" style="font-weight:400;font-size:11px">(근거 기반)</span> ${confHead}</div>
      ${cntHead}
      ${qLines}
      ${tLines}
    </div>`;
  }

  // [복승 크로스 역배열] 각 말이 인기 상위쌍과의 조합 편차로 산출한 실질 강세(2착 유력) 점수 표시.
  function renderCrossReversal(a) {
    const cr = (a && a.crossReversal) || [];
    if (!cr.length) return '';
    const rows = cr.slice(0, 4).map((c) => {
      const col = c.level === '🔴' ? '#f87171' : (c.level === '🟠' ? '#fbbf24' : '#facc15');
      const refs = (c.refs || []).map((r) => r + '번').join('·');
      const qx = (c.qScore != null || c.xScore != null)
        ? `<span class="hint">복승 <b style="color:${col}">${c.qScore != null ? c.qScore : '-'}</b>·쌍승 <b style="color:${col}">${c.xScore != null ? c.xScore : '-'}</b></span>` : '';
      const both = c.both ? `<span style="font-weight:800;color:#f87171">🔁 양쪽감지</span>` : '';
      return `<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:4px 8px;border-radius:6px;margin:2px 0;background:rgba(250,204,21,.06);border-left:3px solid ${col}">
        <b style="color:${col}">${c.level} ${c.no}번</b>
        <span class="hint">크로스 점수 <b style="color:${col}">${c.score}</b></span>
        ${qx}${both}
        ${refs ? `<span class="hint" style="margin-left:auto">→ ${esc(refs)} 1착 시 2착 강력</span>` : ''}
      </div>`;
    }).join('');
    return `<div class="matrix-title" style="font-size:13px;color:#facc15">🔀 복승·쌍승 크로스 역배열 <span class="hint" style="font-weight:400">인기1위+X vs 인기2위+X · 0.3🟡/0.5🟠/0.7🔴 · 🔁양쪽=복승+쌍승 동시</span></div>${rows}`;
  }

  function sportAnalysisHTML(a, bsel) {
    const six = a.bmed && a.bmed.sixRacer;
    const parts = [];
    parts.push(renderCorePicks(a));   // [핵심 추천] 딱 이것만(복승 X+Y·삼복승 X+Y+Z) 최상단
    parts.push(renderRaceJudgment(a, bsel));   // [1·2·4번] 경주 판정 크게 + 배팅 배분
    parts.push(renderChaotic(a, bsel));   // [혼전] 상위 배당 근접 시 고배당 포함 삼복승 전략 배너
    parts.push(renderMidHighFavorites(a));   // [💎 2번] 중고배당 유력마 감지 상단 강조 배너(소리·깜빡임)
    parts.push(renderTopHorses(a));   // ⭐ 유력마 TOP5 + 복병/이상감지 + 제거마 카드
    parts.push(renderSignalReliability(a));   // [5번] 활성 신호별 과거 적중률(50경주+ 신뢰도 강조)
    parts.push(`<div class="matrix-title">🚨 실시간 이상감지 <span class="hint" style="font-weight:400">${esc(a.raceKey || '')}${six ? ' · 6명 출전' : ''}${a.minutesBefore != null && !a.afterClose ? ` · 마감 ${a.minutesBefore}분전` : ''}</span></div>`);
    if (a.summary) parts.push(`<div style="font-size:15px;font-weight:700;margin:6px 0;color:#ffd24f">${esc(a.summary)}</div>`);
    parts.push(renderForcedTrifecta(a));
    parts.push(renderReversalBacking(a));
    parts.push(renderAlertSignal(a.alertSignal, _horseRoleMap(a)));
    parts.push(renderPreReversal(a));
    parts.push(renderAfterCloseSurge(a.afterCloseSurge));
    parts.push(renderInverse(a.inverse));
    parts.push(renderCrossReversal(a));
    parts.push(renderIntegratedGrades(a));
    parts.push(renderJapanSignals(a.signals));
    parts.push(renderSignalTimeline(a.signalTimeline));   // [복구·요청] 신호 타임라인(집중급락 말 변경·안정화 이력) 종목 뷰에도 표시
    // [경륜/경정 근거 표시] 일본경마 뷰에만 있던 '왜 추천했는지' 근거 카드·패턴매칭을 6명 종목 뷰에도 추가.
    //   경륜은 전적이 없어 배당(급락·쌍승역전·연속하락)·이상감지 기반 근거가 표시된다.
    parts.push(renderPatternMatch(a.patternMatch));
    parts.push(renderRecommendBasis(a.recommendBasis));
    parts.push(renderSingleFavorite(a));
    parts.push(renderRecommendFlex(a));
    parts.push(renderBetRecommend(a, bsel));
    parts.push(renderDutchCalc(a));   // [네덜란드식 계산기] 별도 패널·참고용(기존 추천과 별개)
    parts.push(renderHighOddsCompanion(a));
    parts.push((a.raceJudgment && a.raceJudgment.type === 'wait') ? '' : renderBMED(a.bmed, bsel));
    return parts.join('');
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
    const signals = (a.signals || []).filter((s) => s.type === '급락' || s.type === '단승급락' || s.type === '역전' || s.type === '대규모급락');
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
    return renderSignalsSimple(signals, '⚠️ 이상감지 (복승·쌍승·삼복승)', (s) => s.type === '급락' || s.type === '단승급락' || s.type === '역전');
  }

  /** 실시간 배당 통합분석 결과 렌더(유력마·이상감지·베팅) — 단승 제거 */
  function renderJapanIntegrated(a) {
    const host = $('#jpIntegrated'); if (!host) return;
    if (!a || a.error || a.waiting) { host.innerHTML = ''; return; }
    state.jpLastInteg = a;   // [1번] 예산 변경 시 베팅 금액 재계산용
    // [유력마 통일] ⭐유력마 라인도 TOP5와 동일 기준(복승 대표배당 낮은 순 + 이상감지 상위)으로 정렬 표시.
    const keyH = _marketOrderNos(a, (a.keyHorses || []).map(Number)).map((h) => `<b style="color:#4ea1ff">${h}</b>`).join(' · ');
    // [1번] 전적 점수별 말 목록(출마표2 등급표) + 제거 분석(읽기전용) 복원
    const formHtml = renderFormGrades(a.form);
    const elimHtml = renderEliminationHTML(a.elimination, new Set()).replace('id="elimPanel"', 'id="jpElimPanel"');
    host.innerHTML = `<div class="panel-card">
      ${renderCorePicks(a)}
      ${renderRaceJudgment(a, '#jpBudget')}
      ${renderChaotic(a, '#jpBudget')}
      ${renderForcedTrifecta(a)}
      ${renderReversalBacking(a)}
      ${renderPreReversal(a)}
      ${renderAfterCloseSurge(a.afterCloseSurge)}
      ${renderInverse(a.inverse)}
      ${renderCrossReversal(a)}
      ${renderTopHorses(a)}
      <h3>🔗 실시간 배당 이상감지 <span class="hint" style="font-weight:400">${esc(a.raceKey || '')}</span></h3>
      <div style="margin:8px 0"><span class="hint">⭐ 유력마</span> ${keyH || '—'}${a.anomalyHorse != null ? ` <span class="hint">/ 이상감지말</span> <b style="color:#ff5c5c">${a.anomalyHorse}</b>` : ''}</div>
      ${formHtml}
      ${elimHtml}
      ${renderJapanSignals(a.signals)}
      ${renderPatternMatch(a.patternMatch)}
      ${renderRecommendBasis(a.recommendBasis)}
      ${renderSingleFavorite(a)}
      ${renderRecommendFlex(a)}
      ${renderBetRecommend(a, '#jpBudget')}
      ${renderDutchCalc(a)}
      ${renderHighOddsCompanion(a)}
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

  // ══════════ [결과기록 UI 개선] 최근 결과·복기 뷰 (기간 선택 + 경마장 필터) ══════════
  //   History(로컬 결과기록)에서 선택 기간만 날짜별 그룹으로 표시 + 요약 카드 + 클릭 복기.
  //   기존 섹션(대기목록·빠른입력·재현리포트)은 그대로 두고 최상단에 요약 뷰만 추가.
  let _recentPeriod = 7;        // [보완#1] 기간(일). 0=전체. localStorage 유지
  let _recentTrack = '';        // [보완#3] 경마장 필터('' = 전체)
  try { const p = parseInt(localStorage.getItem('bmed_recent_period'), 10); if (!isNaN(p)) _recentPeriod = p; } catch (_) { /* */ }

  function _dateGroupLabel(dateStr) {
    try {
      const t = new Date(todayStr() + 'T00:00:00');
      const d = new Date(dateStr + 'T00:00:00');
      const diff = Math.round((t - d) / 86400000);
      if (diff <= 0) return '오늘';
      if (diff === 1) return '어제';
      return `${diff}일 전`;
    } catch (_) { return dateStr; }
  }
  // 경주명에서 경마장명 추출(첫 토큰) — "모리오카 3경주"→"모리오카", "일본경마"→"일본경마"
  function _trackOf(title) {
    const t = String(title || '').trim();
    if (!t) return '기타';
    const m = t.split(/\s+/)[0];
    return m.replace(/\d.*$/, '') || m || '기타';
  }

  function renderRecentResults() {
    const host = document.getElementById('recentResultsList');
    const sum = document.getElementById('recentSummary');
    if (!host || !sum) return;
    let all = [];
    try { all = History.all(); } catch (_) { all = []; }
    // [보완#1] 기간 필터(0=전체)
    let inPeriod = all.filter((r) => !!r.date);
    if (_recentPeriod > 0) {
      const today = new Date(todayStr() + 'T00:00:00');
      const cutoff = new Date(today); cutoff.setDate(cutoff.getDate() - (_recentPeriod - 1));
      inPeriod = inPeriod.filter((r) => {
        const d = new Date(r.date + 'T00:00:00');
        return !isNaN(d.getTime()) && d >= cutoff && d <= today;
      });
    }
    // [보완#3] 경마장 목록(기간 내) → 필터 칩
    const trackCounts = {};
    inPeriod.forEach((r) => { const tk = _trackOf(r.raceTitle); trackCounts[tk] = (trackCounts[tk] || 0) + 1; });
    const tracks = Object.keys(trackCounts).sort();
    if (_recentTrack && !trackCounts[_recentTrack]) _recentTrack = '';   // 사라진 필터 초기화
    const recent = _recentTrack ? inPeriod.filter((r) => _trackOf(r.raceTitle) === _recentTrack) : inPeriod;

    // 컨트롤 바(기간 + 경마장 필터) + 요약 카드
    const periodBtn = (val, label) => `<button class="btn btn-small rr-period" data-p="${val}" style="${_recentPeriod === val ? 'background:#4ea1ff;color:#0b1220' : ''}">${label}</button>`;
    const trackChip = (name, label) => `<span class="chip rr-track" data-t="${esc(name)}" style="cursor:pointer;${(_recentTrack === name) ? 'border-color:#4ea1ff;color:#4ea1ff;font-weight:700' : ''}">${esc(label)}</span>`;
    const trackBar = tracks.length > 1
      ? `<div style="margin:8px 0 2px;display:flex;gap:5px;flex-wrap:wrap;align-items:center">
          <span class="hint">경마장:</span>${trackChip('', '전체')}${tracks.map((tk) => trackChip(tk, `${tk}(${trackCounts[tk]})`)).join('')}</div>`
      : '';
    const n = recent.length;
    const hits = recent.filter((r) => r.hit).length;
    const rate = n ? Math.round(hits / n * 1000) / 10 : 0;
    const net = recent.reduce((s, r) => s + ((r.payout || 0) - (r.stake || 0)), 0);
    const netColor = net >= 0 ? '#38d39f' : '#f87171';
    const periodLabel = _recentPeriod > 0 ? `최근 ${_recentPeriod}일` : '전체';
    sum.innerHTML = `<div style="display:flex;gap:5px;flex-wrap:wrap;align-items:center">
        <span class="hint">기간:</span>${periodBtn(7, '7일')}${periodBtn(30, '30일')}${periodBtn(0, '전체')}
        <span class="hint" style="margin-left:auto">${periodLabel}${_recentTrack ? ' · ' + esc(_recentTrack) : ''} 기준</span>
      </div>${trackBar}
      <div class="stat-grid" style="grid-template-columns:repeat(4,1fr);margin-top:8px">
      <div class="stat-card"><div class="num">${n}</div><div class="label">기록</div></div>
      <div class="stat-card"><div class="num">${hits}</div><div class="label">적중</div></div>
      <div class="stat-card"><div class="num">${rate}%</div><div class="label">적중률</div></div>
      <div class="stat-card"><div class="num" style="color:${netColor};font-size:19px">${net >= 0 ? '+' : ''}${net.toLocaleString()}원</div><div class="label">손익</div></div>
    </div>`;
    // 컨트롤 리스너
    sum.querySelectorAll('.rr-period').forEach((b) => b.addEventListener('click', () => {
      _recentPeriod = parseInt(b.dataset.p, 10) || 0;
      try { localStorage.setItem('bmed_recent_period', String(_recentPeriod)); } catch (_) { /* */ }
      renderRecentResults();
    }));
    sum.querySelectorAll('.rr-track').forEach((c) => c.addEventListener('click', () => { _recentTrack = c.dataset.t || ''; renderRecentResults(); }));

    // [3] 빈 화면
    if (!n) {
      host.innerHTML = `<div style="text-align:center;padding:22px 10px;color:#8a94a6">
        <div style="font-size:15px;margin-bottom:4px">${_recentTrack ? esc(_recentTrack) + ' 경주가 없습니다' : '최근 분석한 경주가 없습니다'}</div>
        <div class="hint">배당판에서 분석을 시작하세요. 경주가 끝나면 아래 [결과 입력 대기]에서 착순을 입력하면 여기에 쌓입니다.</div>
      </div>`;
      return;
    }
    // [1] 날짜별 그룹(최신순) · 오늘/어제/N일 전
    const byDate = {};
    recent.forEach((r) => { (byDate[r.date] = byDate[r.date] || []).push(r); });
    const dates = Object.keys(byDate).sort().reverse();
    host.innerHTML = dates.map((date) => {
      const rows = byDate[date].slice().reverse().map((r) => {
        const rk = r.raceKey || r.raceTitle || '';   // [보완#2] 복기 매칭은 raceKey 우선
        const net2 = (r.payout || 0) - (r.stake || 0);
        const netStr = (r.stake || r.payout) ? ` <span style="color:${net2 >= 0 ? '#38d39f' : '#f87171'}">${net2 >= 0 ? '+' : ''}${net2.toLocaleString()}원</span>` : '';
        const badge = r.hit ? '<span style="color:#38d39f;font-weight:700">✅ 적중</span>' : '<span style="color:#f87171;font-weight:700">❌ 미적중</span>';
        const resStr = (r.result || []).length ? ` <span class="hint">${(r.result || []).slice(0, 3).join('-')}</span>` : '';
        const detailId = 'rr-det-' + r.id;
        return `<div class="rr-race" data-rk="${esc(rk)}" data-detail="${detailId}" style="cursor:pointer;padding:6px 9px;margin:3px 0;border:1px solid #2b3648;border-radius:7px;display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <span style="font-weight:600">${esc(r.raceTitle || rk)}</span>${resStr} ${badge}${netStr}
          <span class="hint" style="margin-left:auto">▼ 복기</span>
        </div><div id="${detailId}" style="margin:0 0 4px"></div>`;
      }).join('');
      return `<div style="margin-bottom:12px">
        <div class="matrix-title" style="font-size:13px">${_dateGroupLabel(date)} <span class="hint" style="font-weight:400">${date} · ${byDate[date].length}경주</span></div>
        ${rows}</div>`;
    }).join('');
    // 클릭 → 복기 토글(적중=왜 맞았는지 / 미적중=왜 놓쳤는지)
    host.querySelectorAll('.rr-race').forEach((el) => {
      el.addEventListener('click', () => {
        const det = document.getElementById(el.dataset.detail);
        if (!det) return;
        if (det.innerHTML.trim()) { det.innerHTML = ''; return; }   // 다시 클릭 → 접기
        showFailureReport(el.dataset.rk, '#' + el.dataset.detail);
      });
    });
  }

  function renderResultForm() {
    const wrap = $('#resultForm');
    const titles = Object.keys(state.lastReports);
    const perRace = titles.length
      ? titles.map((t) => {
        const c = state.lastCombined[t] || {};
        const bets = betsForRace(t);
        const betTxt = bets.map((b) => `${b.type} ${b.combo.join('-')}`).join(' / ') || '추천 없음';
        // [보완#3] 추천에 포함된 말 번호 집합 — 착순 입력 시 '추천 적중' 여부를 색으로 즉시 표시
        const recNos = new Set();
        bets.forEach((b) => (b.combo || []).forEach((n) => recNos.add(+n)));
        return `
        <div class="bet-box res-block" data-title="${esc(t)}" style="margin-bottom:12px" data-recnos="${[...recNos].join(',')}">
          <h3>${esc(t)} ${c.hadAnomaly ? '<span class="flag flag-must">🔴 이상감지</span>' : ''}</h3>
          <div class="hint">추천: ${esc(betTxt)}</div>
          <div class="cfg-row" style="margin-top:8px">
            <label class="hint">날짜<br><input class="cfg-input res-date" type="date" value="${todayStr()}" /></label>
            <label class="hint">투자금액(원)<br><input class="cfg-input res-stake" type="number" min="0" step="100" value="${c.budget || 0}" style="width:120px" /></label>
            <label class="hint">1·2·3·4착(콤마)<br><input class="cfg-input res-place" placeholder="3,7,1,5" style="width:130px" inputmode="numeric" /></label>
            <label class="hint">확정배당(배)<br><input class="cfg-input res-odds" type="number" min="0" step="0.1" placeholder="배당" style="width:90px" /></label>
            <label class="hint">수익금액(원)<br><input class="cfg-input res-payout" type="number" min="0" step="100" value="0" style="width:120px" /></label>
            <button class="btn res-autocalc" type="button" title="투자금액 × 확정배당 = 수익금액 자동 입력" style="align-self:flex-end">🧮 자동계산</button>
            <button class="btn btn-primary save-result-btn" disabled title="착순을 먼저 입력하세요">결과 저장</button>
          </div>
          <!-- [보완#3] 착순 입력 즉시 미리보기: 착순 칩 + 추천 적중 판정 -->
          <div class="res-preview hint" style="margin-top:6px;min-height:18px"></div>
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

    // [보완#3] 착순 입력 즉시 미리보기 — 각 경주 블록에 라이브 검증/적중판정 연결.
    //   · 착순을 파싱해 칩으로 표시(추천 포함 말=녹색)
    //   · 추천 베팅별 ✅적중/❌미적중을 저장 전에 미리 보여줌 → 입력 실수 방지
    //   · 중복 마번 경고 · 착순 비어있으면 저장 버튼 비활성
    $$('.res-block').forEach((block) => {
      const title = block.dataset.title;
      const placeInp = block.querySelector('.res-place');
      const preview = block.querySelector('.res-preview');
      const saveBtn = block.querySelector('.save-result-btn');
      const recNos = new Set((block.dataset.recnos || '').split(',').filter(Boolean).map(Number));
      const update = () => {
        const raw = String(placeInp.value || '').split(/[,\s]+/).map((x) => parseInt(x, 10)).filter((n) => n > 0);
        if (!raw.length) {
          preview.innerHTML = '<span class="hint">착순을 입력하면 추천 적중 여부가 여기 표시됩니다.</span>';
          saveBtn.disabled = true; saveBtn.title = '착순을 먼저 입력하세요';
          return;
        }
        saveBtn.disabled = false; saveBtn.removeAttribute('title');
        const dup = raw.length !== new Set(raw).size;
        // 착순 칩: 추천에 포함된 말은 녹색 강조
        const chips = raw.map((n, i) => {
          const inRec = recNos.has(n);
          const col = inRec ? '#38d39f' : '#8a94a6';
          const wt = inRec ? '700' : '400';
          return `<span class="chip" style="border-color:${col};color:${col};font-weight:${wt}">${i + 1}착 ${n}${inRec ? ' ⭐' : ''}</span>`;
        }).join(' ');
        // 추천 베팅별 적중 판정
        const bets = betsForRace(title);
        const judged = bets.map((b) => {
          const hit = History.judgeHit(b, raw);
          return `<span class="chip" style="border-color:${hit ? '#38d39f' : '#f87171'};color:${hit ? '#38d39f' : '#f87171'}">${hit ? '✅' : '❌'} ${b.type} ${(b.combo || []).join('-')}</span>`;
        }).join(' ');
        const anyHit = bets.some((b) => History.judgeHit(b, raw));
        const verdict = !bets.length ? '<span class="hint">추천 없음(참고 저장)</span>'
          : (anyHit ? '<b style="color:#38d39f">🎯 적중! 수익금액을 입력하세요</b>' : '<b style="color:#f87171">미적중</b>');
        preview.innerHTML = `<div style="margin-bottom:3px">${chips}${dup ? ' <span style="color:#ffb020">⚠️ 중복 마번</span>' : ''}</div>`
          + (bets.length ? `<div style="margin-bottom:3px">${judged}</div>` : '')
          + `<div>${verdict}</div>`;
      };
      placeInp.addEventListener('input', update);
      update();

      // [보완#3] 수익금액 자동 계산 — 확정배당 × 투자금액 = 수익금액. 수동 입력 부담 제거.
      const oddsInp = block.querySelector('.res-odds');
      const stakeInp = block.querySelector('.res-stake');
      const payoutInp = block.querySelector('.res-payout');
      const autoBtn = block.querySelector('.res-autocalc');
      const autoCalc = () => {
        const stake = parseInt(stakeInp.value, 10) || 0;
        const odds = parseFloat(oddsInp.value) || 0;
        if (stake <= 0 || odds <= 0) { toast('투자금액과 확정배당을 먼저 입력하세요.'); return; }
        payoutInp.value = Math.round(stake * odds / 100) * 100;   // 100원 단위 반올림
        payoutInp.style.background = 'rgba(56,211,159,.15)';       // 자동 입력 시각 피드백
        setTimeout(() => { payoutInp.style.background = ''; }, 700);
      };
      if (autoBtn) autoBtn.addEventListener('click', autoCalc);
      // 확정배당 입력 후 Enter 또는 값 확정(change) 시에도 자동 계산
      if (oddsInp) oddsInp.addEventListener('change', () => { if ((parseFloat(oddsInp.value) || 0) > 0 && (parseInt(stakeInp.value, 10) || 0) > 0) autoCalc(); });
    });

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
      // [보완#2] 일괄표 적중 미리보기 — 추출된 착순으로 추천 베팅 적중 여부를 저장 전에 표시.
      const bets = key ? betsForRace(key) : [];
      const placing = r.placing || [];
      const anyHit = placing.length && bets.some((b) => History.judgeHit(b, placing));
      const hitTitle = bets.map((b) => `${History.judgeHit(b, placing) ? '✅' : '❌'} ${b.type} ${(b.combo || []).join('-')}`).join(' / ');
      const hitCell = !placing.length ? '<span class="hint">착순없음</span>'
        : !key ? '<span class="hint">매칭없음</span>'
          : !bets.length ? '<span class="hint">추천없음</span>'
            : `<b style="color:${anyHit ? '#38d39f' : '#f87171'}" title="${esc(hitTitle)}">${anyHit ? '✅ 적중' : '❌ 미적중'}</b>`;
      return `<tr data-i="${i}">
        <td>${esc(r.venue || '')} ${r.raceNo}R</td>
        <td>${(r.placing || []).join('-')}</td>
        <td>${key ? '✅ ' + esc(key) : '⚠️ 없음'}</td>
        <td>${hitCell}</td>
        <td><input class="cfg-input batch-stake" type="number" min="0" step="100" value="${c.budget || 0}" style="width:100px" /></td>
        <td><input class="cfg-input batch-odds" type="number" min="0" step="0.1" placeholder="배당" style="width:78px" title="확정배당 입력 시 투자×배당=수익 자동계산" /></td>
        <td><input class="cfg-input batch-payout" type="number" min="0" step="100" value="0" style="width:100px" /></td>
      </tr>`;
    }).join('');
    host.innerHTML = `
      <table class="data-table"><thead><tr><th>경주</th><th>착순</th><th>매칭</th><th>적중</th><th>투자(원)</th><th>확정배당</th><th>수익(원)</th></tr></thead><tbody>${rows}</tbody></table>
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
    // [보완#2] 일괄표 자동계산 — 확정배당 입력 시 그 행의 투자×배당=수익 자동 입력(수동계산 부담 제거).
    host.querySelectorAll('.batch-odds').forEach((inp) => inp.addEventListener('input', () => {
      const tr = inp.closest('tr'); if (!tr) return;
      const stake = parseInt(tr.querySelector('.batch-stake').value, 10) || 0;
      const odds = parseFloat(inp.value) || 0;
      if (stake > 0 && odds > 0) {
        const pay = tr.querySelector('.batch-payout');
        pay.value = Math.round(stake * odds / 100) * 100;   // 100원 단위 반올림
        pay.style.background = 'rgba(56,211,159,.15)';
        setTimeout(() => { pay.style.background = ''; }, 600);
        recalc();
      }
    }));
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
        raceKey: key || '',   // [보완#2] 복기 매칭용 raceKey(분석 히스토리 키) 저장
        bets, result: placing, hit, stake, payout,
        hadAnomaly: !!c.hadAnomaly, recOdds: c.recOdds == null ? null : c.recOdds,
        signals: signalsFor(c),
      });
      saved++;
    });
    toast(`저장 ${saved}건 · 분석매칭 ${matched} · 적중 ${hits} · 당일손익 ${(po - st).toLocaleString()}원`);
    renderStats();
    try { renderRecentResults(); } catch (_) { /* */ }   // [결과기록 UI] 최근 7일 뷰 갱신
  }

  function saveResult(btn) {
    const block = btn.closest('.res-block');
    const title = block.dataset.title;
    // [보완#3] 콤마·공백 모두 허용(미리보기와 동일 파싱) + 0/음수 제외
    const result = (block.querySelector('.res-place').value || '').split(/[,\s]+/).map((s) => parseInt(s, 10)).filter((n) => n > 0);
    if (!result.length) { toast('착순(1·2·3착)을 입력하세요.'); return; }
    const stake = parseInt(block.querySelector('.res-stake').value, 10) || 0;
    const payout = parseInt(block.querySelector('.res-payout').value, 10) || 0;
    const date = block.querySelector('.res-date').value || todayStr();
    const c = state.lastCombined[title] || {};
    const region = title === '일본경마' || title.indexOf('일본') === 0 ? '일본' : '한국';
    const bets = betsForRace(title);
    const hit = bets.some((b) => History.judgeHit(b, result));
    History.addResult({
      date, region, raceTitle: title, raceKey: title,   // [보완#2] 복기 매칭용 raceKey 저장
      bets, result, hit, stake, payout,
      hadAnomaly: !!c.hadAnomaly, recOdds: c.recOdds == null ? null : c.recOdds,
      signals: signalsFor(c),
    });
    updateJockeysFromResult(title, result);   // [6번] 기수 성적 자동 갱신
    refreshRaceChipStatus();                   // [기능3] 진행상황 칩 갱신 (이 경주 → 🏁)
    toast(`저장됨 — ${hit ? '✅ 적중!' : '❌ 미적중'} (투자 ${stake.toLocaleString()} / 수익 ${payout.toLocaleString()})`);
    renderStats();
    try { renderRecentResults(); } catch (_) { /* */ }   // [결과기록 UI] 최근 7일 뷰 갱신
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
    const bV = $('#reviewLogRefresh'); if (bV) bV.addEventListener('click', loadReviewLog);
    loadReviewLog();   // [복기 저장] 리포트 서브탭 진입 시 자동 로드
  }

  // [복기 저장] 확장 팝업 🧠 복기 저장 목록 — /api/review/list
  async function loadReviewLog() {
    const view = $('#reviewLogView'); if (!view) return;
    let d = null;
    try { d = await (await fetch('/api/review/list')).json(); } catch (_) { /* */ }
    const arr = (d && d.reviews) || [];
    if (!arr.length) { view.innerHTML = '<p class="hint">저장된 복기가 없습니다. 확장 팝업에서 🧠 복기 저장을 누르면 여기에 쌓입니다.</p>'; return; }
    view.innerHTML = arr.slice(0, 30).map((r) => {
      const when = r.savedAt ? new Date(r.savedAt).toLocaleString('ko-KR', { hour12: false }) : '';
      const top3 = (r.result && r.result.top3 || []).join('-');
      const hit = r.hit || {};
      const badge = (hit.quinella || hit.trifecta)
        ? '<span style="color:#22c55e;font-weight:700">✅ 적중</span>'
        : (top3 ? '<span style="color:#ef4444">미적중</span>' : '<span class="hint">결과 미입력</span>');
      const keys = (r.keyHorses || []).length ? ' · 유력마 ' + r.keyHorses.join('·') : '';
      return `<div style="background:#1e293b;border:1px solid #334155;border-radius:8px;padding:10px;margin-bottom:8px">
        <div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap">
          <b>${esc(r.raceKey || r.race_id || '')}</b>
          <span class="hint">${esc(when)}</span>
        </div>
        <div style="margin-top:4px;font-size:13px">🔔 중요신호 ${r.signalCount || 0}건${esc(keys)}${top3 ? ' · 결과 ' + esc(top3) : ''} · ${badge}</div>
      </div>`;
    }).join('');
  }

  // [6번] 내 코멘트 모아보기 — /api/review/stats (최고중요·키워드·near_miss)
  const _IMP_LABEL = { 1: '일반', 2: '⭐중요', 3: '⭐⭐최고' };
  function _reviewCommentCard(r) {
    const when = r.savedAt ? new Date(r.savedAt).toLocaleString('ko-KR', { hour12: false }) : '';
    const top3 = (r.result && r.result.top3 || []).join('-');
    const tags = (r.tagLabels || []).map((t) => `<span style="display:inline-block;background:#334155;color:#93c5fd;border-radius:10px;padding:1px 8px;font-size:11px;margin:2px 3px 0 0">🏷 ${esc(t)}</span>`).join('');
    return `<div style="background:#1e293b;border:1px solid #334155;border-radius:8px;padding:10px;margin-bottom:8px">
      <div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap">
        <b>${esc(r.raceKey || r.race_id || '')}</b>
        <span class="hint">${esc(_IMP_LABEL[r.importance] || '일반')}${when ? ' · ' + esc(when) : ''}${top3 ? ' · 결과 ' + esc(top3) : ''}</span>
      </div>
      ${r.comment ? `<div style="margin-top:5px;font-size:13px;white-space:pre-line;color:#e2e8f0;border-left:3px solid #64748b;padding-left:8px">${esc(r.comment)}</div>` : ''}
      ${tags ? `<div style="margin-top:5px">${tags}</div>` : ''}
    </div>`;
  }
  async function loadReviewStats() {
    const view = $('#reviewStatsView'); if (!view) return;
    view.innerHTML = '<p class="hint">불러오는 중…</p>';
    let d = null;
    try { d = await (await fetch('/api/review/stats')).json(); } catch (_) { /* */ }
    if (!d || !d.total) { view.innerHTML = '<p class="hint">저장된 코멘트가 없습니다. 확장 팝업 결과입력에서 자유 코멘트를 남기면 여기에 모입니다.</p>'; return; }
    const imp = d.importance || {};
    let html = '';
    // 중요도 분포 + 키워드 통계
    html += `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
      <span style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:6px 10px;font-size:13px">전체 <b>${d.total}</b></span>
      <span style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:6px 10px;font-size:13px">일반 ${imp[1] || 0}</span>
      <span style="background:#0f172a;border:1px solid #7c3aed;border-radius:8px;padding:6px 10px;font-size:13px;color:#c4b5fd">⭐중요 ${imp[2] || 0}</span>
      <span style="background:#0f172a;border:1px solid #f59e0b;border-radius:8px;padding:6px 10px;font-size:13px;color:#fbbf24">⭐⭐최고 ${imp[3] || 0}</span>
    </div>`;
    if ((d.tags || []).length) {
      html += `<div style="margin-bottom:12px"><b style="font-size:13px">🏷 자주 언급 키워드</b><div style="margin-top:5px">` +
        d.tags.map((t) => `<span style="display:inline-block;background:#334155;color:#93c5fd;border-radius:12px;padding:3px 10px;font-size:12px;margin:3px 4px 0 0">${esc(t.label)} <b>${t.count}</b></span>`).join('') +
        `</div></div>`;
    }
    // ⭐⭐최고 중요 경주
    if ((d.topRaces || []).length) {
      html += `<h3 style="font-size:14px;margin:12px 0 6px">⭐⭐ 최고 중요 경주 (${d.topRaces.length})</h3>` +
        d.topRaces.map(_reviewCommentCard).join('');
    }
    // near_miss 패턴
    if ((d.nearMiss || []).length) {
      html += `<h3 style="font-size:14px;margin:14px 0 6px">😢 아쉬운(near_miss) 경주 (${d.nearMiss.length})</h3>` +
        d.nearMiss.map(_reviewCommentCard).join('');
    }
    view.innerHTML = html;
  }

  // [2번] 고배당 명예의 전당 — /api/highlights 카드형 표시
  async function loadHighlights() {
    const box = $('#highlightWins'); if (!box) return;
    let d = null;
    try { d = await (await fetch('/api/highlights')).json(); } catch (_) { /* */ }
    const rawArr = (d && d.highlights) || [];
    // [3번] 같은 경주 중복 제거 — 경주별 1개(가장 높은 배당) 유지
    const _seen = new Map();
    rawArr.forEach((h) => {
      const key = h.raceKey || h.race || h.report_slug || JSON.stringify(h.top3 || []);
      const od = (h.trifecta_hit ? h.trifecta_odds : h.quinella_odds) || 0;
      const prev = _seen.get(key);
      if (!prev || od > (prev._od || 0)) { h._od = od; _seen.set(key, h); }
    });
    const arr = [..._seen.values()];
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

  // [복기 시각화] 고배당 재현 리포트용 예측vs실제 요약 스트립(why_recommended 기반·elimination 없음).
  function _reportCompareStrip(rep) {
    const res = rep.result || {};
    if (res['1st'] == null) return '';
    const why = rep.why_recommended || {};
    // 경기 전 추천마 = 우리가 실제 추천한 유력마만(입상마 혼입 방지).
    // 우선순위: recommended_horses(신규 리포트) → why 항목 recommended 플래그 → (구 리포트) why 전체 폴백.
    let preNos = Array.isArray(rep.recommended_horses)
      ? rep.recommended_horses.map(Number).filter((n) => !isNaN(n))
      : [];
    if (!preNos.length) {
      const recFlagged = Object.keys(why).map((k) => why[k])
        .filter((s) => s && s.horse != null && s.recommended === true).map((s) => Number(s.horse));
      preNos = recFlagged.length
        ? recFlagged
        : Object.keys(why).map((k) => why[k]).filter((s) => s && s.horse != null).map((s) => Number(s.horse));
    }
    const placed = ['1st', '2nd', '3rd'].map((k) => res[k]).filter((v) => v != null).map(Number);
    const placedSet = new Set(placed);
    const preCmp = preNos.length ? preNos.map((n) => {
      const inHit = placedSet.has(n);
      return `<span class="chip" style="border-color:${inHit ? '#38d39f' : '#5a6172'};color:${inHit ? '#38d39f' : '#8a94a6'}">${n}번 ${inHit ? '✅입상' : '✗'}</span>`;
    }).join(' ') : '<span class="hint">추천마 기록 없음</span>';
    const placeCmp = placed.map((n, i) => {
      const label = ['1착', '2착', '3착'][i];
      const pred = preNos.includes(n);
      return `<div style="margin:2px 0"><b>${label} ${n}번</b> <span class="chip" style="border-color:${pred ? '#38d39f' : '#ffb020'};color:${pred ? '#38d39f' : '#ffb020'}">${pred ? '추천마 예측 ✅' : '미분류(놓침)'}</span></div>`;
    }).join('');
    const hit = preNos.filter((n) => placedSet.has(n)).length;
    const rate = preNos.length ? Math.round((hit / preNos.length) * 100) : 0;
    const missed = placed.filter((n) => !preNos.includes(n));
    const verdict = missed.length ? `<span style="color:#ffb020">${missed.join('·')}번을 사전에 못 짚음</span>` : `<span style="color:#38d39f">입상마 전부 추천 범위 안</span>`;
    return `<div style="margin:8px 0;padding:9px 11px;border:1px solid var(--border);border-radius:8px;background:linear-gradient(90deg,rgba(78,161,255,.1),rgba(56,189,248,.1))">
      <div class="matrix-title" style="font-size:13px">🔍 예측 vs 실제 <span class="hint" style="font-weight:400">복기 핵심 대조</span></div>
      <div style="display:flex;gap:14px;flex-wrap:wrap">
        <div style="flex:1;min-width:200px"><div class="hint" style="margin-bottom:2px">🔮 경기 전 추천마 적중</div>${preCmp}
          <div class="hint" style="margin-top:4px">추천마 <b style="color:${rate >= 50 ? '#38d39f' : '#ffb020'}">${hit}/${preNos.length}두 입상 (${rate}%)</b></div></div>
        <div style="flex:1;min-width:200px"><div class="hint" style="margin-bottom:2px">🏁 실제 입상마 → 예측 분류</div>${placeCmp}</div>
      </div>
      <div style="margin-top:6px;font-size:13px;font-weight:700">📌 총평: ${verdict}</div></div>`;
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
        if (p.excluded) {   // [1·2번] 마감 후 / 다음 경주 혼입 = 제외됨(회색 표시, 변동 계산 제외)
          return `<tr style="opacity:.5"><td>${esc(p.time || '')}${p.minutes_before != null ? ' (T-' + p.minutes_before + '분)' : ''}</td><td>${p.odds}배</td><td class="hint">🗑️ ${esc(p.exclReason || '제외됨')}</td></tr>`;
        }
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

    // 탭5: [신규 5번] 이상감지 신호 변경 이력·안정화·유효 시점
    const st = rep.signal_change_history || {};
    const mb = (o) => (o && o.minutes_before != null ? 'T-' + o.minutes_before + '분' : (o && o.time ? o.time : '—'));
    const stEvents = st.events || {};
    const chgRows = (st.changes || []).map((c) =>
      `<div class="rpt-signal"><b style="color:#ffd24f">${esc(c.previous_signal)}→${esc(c.new_signal)}</b> <span class="hint">${mb(c)}</span>${c.prev_was_candidate ? ' <span class="hint">(직전은 1회 감지 후 소멸)</span>' : ''}<br><span class="hint">↳ ${esc(c.reason || '')}</span></div>`).join('');
    const timeRows = Object.keys(stEvents).map((h) => {
      const e = stEvents[h] || {};
      const bits = [];
      if (e.first) bits.push('최초 감지 ' + mb(e.first));
      if (e.confirmed) bits.push('확정 ' + mb(e.confirmed) + ' (2연속)');
      else if ((e.count || 0) <= 1) bits.push('1회 감지 (미확정)');
      if (e.vanished) bits.push('소멸 ' + mb(e.vanished));
      return `<tr><td>${h}번</td><td>${esc(bits.join(' · '))}</td></tr>`;
    }).join('') || '<tr><td colspan="2" class="hint">신호 시점 데이터 없음</td></tr>';
    const finalTxt = st.finalSignal != null ? `${st.finalSignal}번 ${st.finalConfirmed ? '(2연속 확정)' : '(후보)'}` : '감지 없음';
    const exc = st.excluded || {};
    const excTxt = (exc.after_close || exc.next_race) ? `<p class="hint">🗑️ 제외 데이터: ${exc.after_close ? '마감 후 ' + exc.after_close + '건 ' : ''}${exc.next_race ? '다음경주 혼입 ' + exc.next_race + '건' : ''}</p>` : '';
    const pane5 = `<div class="rpt-pane" data-pane="sigtime"><h3>🎯 신호 변경 이력 · 안정화</h3>
      <p>최종 유효 신호: <b style="color:#ffd24f">${finalTxt}</b> · 확정 ${(st.confirmed || []).length}두 · 후보 ${(st.candidates || []).length}두</p>
      ${chgRows ? `<div style="margin:6px 0"><b class="hint">🔀 변경 이력</b>${chgRows}</div>` : '<p class="hint">신호 변경 없음(일관 신호)</p>'}
      <table class="rpt-tl"><thead><tr><th>말</th><th>유효 신호 시점</th></tr></thead><tbody>${timeRows}</tbody></table>
      ${excTxt}</div>`;

    view.innerHTML = `
      <div class="rpt-head">${esc(rep.race || '')} · ${esc(resultStr)}</div>
      <div class="rpt-sub">${hitBadge}${oddsBadge} ${tagsHtml}</div>
      ${_reportCompareStrip(rep)}
      <div class="rpt-tabs">
        <button class="rpt-tab active" data-pane="story">추천 근거</button>
        <button class="rpt-tab" data-pane="timeline">배당 타임라인</button>
        <button class="rpt-tab" data-pane="form">전적 분석</button>
        <button class="rpt-tab" data-pane="anomaly">이상감지 내역</button>
        <button class="rpt-tab" data-pane="sigtime">신호 이력</button>
      </div>
      ${pane1}${pane2}${pane3}${pane4}${pane5}`;
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

  // [자동 팝업 제거] 경주 종료 후 '결과를 입력하세요' 자동 alert/Notification 팝업은 제거함(사용자 요청 — 불편).
  //   미입력 경주 안내는 결과기록 탭의 [📋 결과 입력 대기] 목록(loadPendingResults)에서만 표시하고,
  //   결과 입력 기능(대기 목록·클릭 시 openQuickResult 팝업·결과기록 탭 입력폼)은 그대로 유지한다.
  //   함수는 호출부(loadPendingResults) 호환을 위해 남기되, 자동 팝업/알림은 띄우지 않는다(no-op).
  function checkResultNotify(_list) { /* 자동 팝업 제거됨 — 결과기록 탭 대기 목록으로 대체 */ }

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
    // [4번] 다음 경주 자동전환 상태 배너(상단 중앙) — 발주 마감 후 새 경주로 넘어가는 과정을 명확히 표시.
    const nb = document.createElement('div');
    nb.id = 'nextRaceBanner';
    nb.style.cssText = 'position:fixed;left:50%;transform:translateX(-50%);top:8px;z-index:99999;'
      + 'padding:8px 18px;border-radius:10px;font:700 13px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;'
      + 'text-align:center;display:none;box-shadow:0 4px 14px rgba(0,0,0,.35);white-space:pre-line';
    document.body.appendChild(nb);
    const two = (n) => String(n).padStart(2, '0');
    const hms = (ms) => { const d = new Date(ms); return two(d.getHours()) + ':' + two(d.getMinutes()) + ':' + two(d.getSeconds()); };
    const hm = (ms) => { const d = new Date(ms); return two(d.getHours()) + ':' + two(d.getMinutes()); };
    const cd = (ms) => { const s = Math.max(0, Math.round(ms / 1000)); return Math.floor(s / 60) + '분 ' + two(s % 60) + '초'; };
    let _nrDoneUntil = 0;   // '✅ 전환됨' 배너를 잠깐만 노출
    function renderNextRace(s, now) {
      const nr = s && s.nextRace;
      // 전환 진행(waiting/refreshing/collecting) → 🔄 대기 배너
      if (nr === 'waiting' || nr === 'refreshing' || nr === 'collecting') {
        const eta = (s.deadline && s.deadline > now) ? `\n예상 시작: ${hm(s.deadline)}` : '';
        const rk = s.raceKey || s.newRaceKey || '';
        nb.style.background = '#1e3a5f'; nb.style.border = '1px solid #3b82f6'; nb.style.color = '#dbeafe';
        nb.textContent = `🔄 다음 경주 대기 중${rk ? ' — ' + rk : ''}${eta}`;
        nb.style.display = 'block';
        return true;
      }
      // 전환 완료(done) → ✅ 배너 12초 노출 후 숨김
      if (nr === 'done') {
        if (!_nrDoneUntil || (s.t && s.t > now - 3000)) _nrDoneUntil = now + 12000;
        if (now < _nrDoneUntil) {
          const rk = s.newRaceKey || s.raceKey || '';
          nb.style.background = '#14432a'; nb.style.border = '1px solid #22c55e'; nb.style.color = '#dcfce7';
          nb.textContent = `✅ ${rk || '새 경주'} 자동 전환됨`;
          nb.style.display = 'block';
          return true;
        }
      }
      // 탭 없음 경고
      if (nr === 'no-tab') {
        nb.style.background = '#4a2b12'; nb.style.border = '1px solid #f59e0b'; nb.style.color = '#fef3c7';
        nb.textContent = '⚠ 배당판 탭이 없어 자동 전환 불가 — 배당판을 열어주세요';
        nb.style.display = 'block';
        return true;
      }
      nb.style.display = 'none';
      return false;
    }
    async function tick() {
      let s = null;
      try { s = await (await fetch('/api/auto/status')).json(); } catch (_) { /* */ }
      const now = Date.now();
      renderNextRace(s, now);   // [4번] 전환 배너(상태와 독립적으로 항상 평가)
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

  // [스펙2·3] 결과 자동수집 이벤트 감시 — 서버 브리지(/api/results/auto-status) 폴링.
  //   ① 실패(manual) → 상단 배너 "⚠️ N경주 자동수집 실패 → 수동입력"(클릭 시 결과기록 탭)
  //   ② 성공(lastDone.seq 증가) → 결과기록 탭 자동 갱신(새로고침 없이 반영) + 토스트
  let _lastResultDoneSeq = -1, _resultWatchInit = false;
  function _refreshResultTab() {
    // 사용자가 결과 입력칸에 타이핑 중이면 폼 재렌더는 건너뜀(입력 유실 방지)
    const focused = document.activeElement;
    const typing = focused && focused.closest && focused.closest('.res-block');
    try { loadPendingResults(); } catch (_) { /* */ }
    try { renderStats(); } catch (_) { /* */ }
    try { renderRecentResults(); } catch (_) { /* */ }   // [결과기록 UI] 최근 7일 뷰 갱신
    try { loadReportList(); } catch (_) { /* */ }
    try { loadHighlights(); } catch (_) { /* */ }
    if (!typing) { try { renderResultForm(); } catch (_) { /* */ } }
  }
  function initResultAutoWatch() {
    if (_resultWatchInit) return; _resultWatchInit = true;
    const bar = document.createElement('div');
    bar.id = 'resultAutoFailBar';
    bar.style.cssText = 'position:fixed;left:0;right:0;top:0;z-index:99999;padding:7px 12px;'
      + 'font:700 13px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;'
      + 'color:#1a1a1a;background:#ffb020;border-bottom:1px solid #c67f00;display:none;'
      + 'text-align:center;cursor:pointer;letter-spacing:.2px';
    bar.title = '클릭하면 결과기록 탭으로 이동해 수동 입력할 수 있습니다.';
    bar.addEventListener('click', () => {
      const rb = [...document.querySelectorAll('.tab-btn, [data-tab]')].find((b) => b.dataset && b.dataset.tab === 'result');
      if (rb) rb.click();
    });
    document.body.appendChild(bar);
    async function tick() {
      let s = null;
      try { s = await (await fetch('/api/results/auto-status')).json(); } catch (_) { return; }
      if (!s) return;
      // ① [자동 표시 제거] 전역 상단 '자동수집 실패 → 수동입력 필요' 배너는 표시하지 않음(사용자 요청).
      //    미입력·실패 경주 안내는 결과기록 탭의 [📋 결과 입력 대기] 목록에서만 확인(전역 배너/팝업 없음).
      bar.style.display = 'none';
      // ② 새 성공(lastDone.seq 증가) → 결과기록 탭만 조용히 자동 갱신(팝업 없이).
      const doneSeq = (s.lastDone && s.lastDone.seq) || 0;
      if (_lastResultDoneSeq < 0) { _lastResultDoneSeq = doneSeq; return; }  // 첫 폴링은 기준만 설정
      if (doneSeq > _lastResultDoneSeq) {
        _lastResultDoneSeq = doneSeq;
        _refreshResultTab();   // 결과기록 탭 자동 갱신(성공 alert 팝업 제거)
      }
    }
    tick();
    setInterval(tick, 5000);
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

  // [경마 oddspark 서버 수집] Chrome 확장 없이 서버가 지방경마 복승·쌍승을 직접 조회.
  //   마감 3분전 3초·평상 30초 적응형 폴링(closingTick에서 구동). 페이지 열려 있을 때만 동작.
  const _keibaOdds = { enabled: false, lastPoll: 0, lastRk: null, busy: false,
    lastCounts: null, lastTime: '', lastMsg: '', lastRkShown: '', pinnedRk: '',
    lastDetect: 0, detecting: false, lastWaiting: false };   // [경주 자동추종] 현재 경주번호 감지 상태
  // 경주 지정(pin)이 있으면 그 경주, 없으면 현재 경주 자동추종
  function _keibaTargetRk() {
    return _keibaOdds.pinnedRk || _closing.panelRk || getActiveRaceKey() || '';
  }
  function _setKeibaStatusHtml(html) { const el = document.getElementById('keibaOddsStatus'); if (el) el.innerHTML = html; }

  /** 요청 형식 3줄 상태 렌더: "✅ oddspark 수집 중 / 복승 N조합·쌍승 N조합 / 마지막 수집: HH:MM:SS" */
  function _renderKeibaStatus() {
    // [중앙경마 자동 비활성화] 현재 경주가 JRA면 토글 비활성화 + 안내(oddspark 미지원).
    const _curRk = _keibaTargetRk();
    const _chk = document.getElementById('keibaOddsAutoChk');
    if (jpIsCentralName(_curRk)) {
      if (_chk) { _chk.checked = false; _chk.disabled = true; }
      _setKeibaStatusHtml('<div style="color:#c084fc;font-weight:800">🏇 중앙경마(JRA)</div><div style="color:#94a3b8;font-size:12px">oddspark 서버 수집 미지원 — <b>Chrome 확장으로만 수집</b>됩니다.</div>');
      return;
    }
    if (_chk && _chk.disabled) { _chk.disabled = false; try { _chk.checked = localStorage.getItem('keibaOddsAuto') === '1'; } catch (_) { /* */ } _keibaOdds.enabled = _chk.checked; }
    if (!_keibaOdds.enabled) { _setKeibaStatusHtml('⬜ 꺼짐 — 토글을 켜면 현재 경주 배당을 자동 수집합니다.'); return; }
    const head = '<div style="color:#38d39f;font-weight:800">✅ oddspark 수집 중</div>';
    const rkLine = _keibaOdds.lastRkShown ? `<div style="color:#94a3b8;font-size:11px">현재 경주: ${esc(_keibaOdds.lastRkShown)}</div>` : '';
    let body;
    if (_keibaOdds.lastCounts) {
      const c = _keibaOdds.lastCounts;
      body = `<div>복승 <b style="color:#e2e8f0">${c.quinella || 0}</b>조합 · 쌍승 <b style="color:#e2e8f0">${c.exacta || 0}</b>조합</div>`;
    } else {
      body = `<div style="color:#94a3b8">${esc(_keibaOdds.lastMsg || '현재 경주 배당 대기 중…')}</div>`;
    }
    const foot = _keibaOdds.lastTime ? `<div style="color:#64748b;font-size:11px">마지막 수집: ${esc(_keibaOdds.lastTime)}</div>` : '';
    _setKeibaStatusHtml(head + rkLine + body + foot);
  }

  async function fetchKeibaOdds(rk, silent) {
    if (!rk) { _keibaOdds.lastCounts = null; _keibaOdds.lastMsg = '현재 경주(raceKey)가 없습니다.'; _renderKeibaStatus(); return null; }
    // [중앙경마 자동 비활성화] JRA는 oddspark 미지원 → 서버 수집 시도 안 함(Failed to fetch 방지). Chrome 확장으로만.
    if (jpIsCentralName(rk)) {
      _keibaOdds.lastRkShown = rk; _keibaOdds.lastCounts = null;
      _keibaOdds.lastMsg = '🏇 중앙경마(JRA)는 oddspark 미지원 — Chrome 확장으로만 수집됩니다.';
      _renderKeibaStatus(); return { central: true };
    }
    _keibaOdds.lastRkShown = rk;
    if (!silent && !_keibaOdds.enabled) _setKeibaStatusHtml('🏇 oddspark 배당 조회 중…');
    let d; try { d = await (await fetch('/api/keiba/odds', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ raceKey: rk }) })).json(); }
    catch (e) { _keibaOdds.lastMsg = '조회 실패: ' + e.message; _keibaOdds.lastCounts = null; _renderKeibaStatus(); return null; }
    _keibaOdds.lastTime = new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
    if (d.error) { _keibaOdds.lastCounts = null; _keibaOdds.lastMsg = '⚠️ ' + (d.error || ''); _renderKeibaStatus(); return d; }
    if (d.waiting) { _keibaOdds.lastWaiting = true; _keibaOdds.lastCounts = null; _keibaOdds.lastMsg = '⏳ ' + (d.reason || '실배당 대기(발매 전·마감 후)'); _renderKeibaStatus(); return d; }
    _keibaOdds.lastWaiting = false;
    _keibaOdds.lastCounts = d.counts || { quinella: 0, exacta: 0 };
    _renderKeibaStatus();
    try { refreshCurrentRace(); } catch (_) { /* */ }
    return d;
  }

  // 적응형 폴링(마감 3분전 3초·평상 15초) — closingTick에서 매초 호출, 자체 스로틀.
  function keibaOddsAutoPoll(rk, left) {
    if (!_keibaOdds.enabled || _keibaOdds.busy) return;
    rk = _keibaOdds.pinnedRk || rk;                      // 경주 지정(pin) 우선
    if (!rk) return;
    if (jpIsKoreaName(rk)) return;                       // 한국 경주는 oddspark 대상 아님(확장/PDF 유지)
    if (jpIsCentralName(rk)) return;                     // [중앙경마] JRA는 oddspark 미지원 → 자동 폴링 안 함
    // [stale 루프 차단] 마감 90초+ 지난(=끝난) 경주는 폴링 중단 — oddspark가 확정배당을 계속 재수집해
    //   그 경주가 계속 '최신'으로 남고 다음 경주로 못 넘어가던 자기강화 루프 제거(pin 지정 시엔 유지).
    if (!_keibaOdds.pinnedRk && left !== Infinity && left < -90000) {
      _keibaOdds.lastMsg = '⏹️ 이 경주 마감됨 — 다음 경주 자동 대기(끝난 경주 재수집 중단)';
      _keibaOdds.lastCounts = null; _renderKeibaStatus();
      return;
    }
    // [수집 간격 단축] 마감 1분전 2초 · 3분전 3초 · 평상 15초
    const interval = (left <= 60000) ? 2000 : (left <= 180000) ? 3000 : 15000;
    const now = Date.now();
    if (_keibaOdds.lastRk === rk && (now - _keibaOdds.lastPoll) < interval - 250) return;
    _keibaOdds.lastPoll = now; _keibaOdds.lastRk = rk; _keibaOdds.busy = true;
    Promise.resolve(fetchKeibaOdds(rk, true)).finally(() => { _keibaOdds.busy = false; });
  }

  /** [경주 자동추종] oddspark '현재 발매중 경주번호'를 감지해 경주 전환 시 raceKey 즉시 갱신 + 데이터 초기화.
   *  원인 교정: ①경주 전환 감지 없음 ②raceKey 자동 갱신 지연 ③이전 경주 캐시 잔존.
   *  - 경주 지정(pin)·한국경주는 자동추종 제외(명시 선택 존중). 전진(번호↑) 전환만 반영(역주행·블립 방지).
   *  - 감지 주기: 마감 3분내/실배당 대기중 8초 · 평상 25초(oddspark 과호출 방지, 서버 15초 캐시와 병행). */
  async function keibaDetectCurrentRace(rk, left) {
    if (!_keibaOdds.enabled || _keibaOdds.detecting) return;
    if (_keibaOdds.pinnedRk) return;                     // 경주 지정 시 자동추종 안 함
    if (!rk || jpIsKoreaName(rk) || jpIsCentralName(rk)) return;   // 한국·중앙경마는 oddspark 대상 아님
    if (!/\d+\s*경주/.test(rk)) return;                  // 경주번호 없는 raceKey 제외
    const detIv = (left <= 180000 || _keibaOdds.lastWaiting) ? 8000 : 25000;
    const now = Date.now();
    if ((now - _keibaOdds.lastDetect) < detIv) return;
    _keibaOdds.lastDetect = now; _keibaOdds.detecting = true;
    try {
      const d = await (await fetch('/api/keiba/current', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ raceKey: rk }),
      })).json();
      // 전진 전환만 반영(현재번호 > 이전번호). 감지 실패·동일·역주행은 무시.
      if (d && d.ok && d.changed && d.raceKey && d.currentRace > (d.prevRace || 0) && d.raceKey !== rk) {
        console.log('[경주 자동추종] 전환 감지: ' + rk + ' → ' + d.raceKey + ' (발매중 R' + d.currentRace + ')');
        // ① 이전 경주 캐시·타임라인 즉시 초기화(잔존 방지)
        hardResetRaceState();
        _keibaOdds.lastRk = null; _keibaOdds.lastPoll = 0; _keibaOdds.lastCounts = null; _keibaOdds.lastWaiting = false;
        // [잔존마 방어·3번] 이전 경주 전적(starters) 삭제 — 새 경주(+한국PDF)만 유지(7번/10번 잔존마 원천 차단)
        try { fetch('/api/starters/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ keepRaceKey: d.raceKey }) }); } catch (_) { /* */ }
        // ② 새 경주로 raceKey 갱신(패널·활성·이상감지 피드 일괄 전환)
        setActiveRaceKey(d.raceKey);
        setAnomalyPanelRace(d.raceKey);
        _keibaOdds.lastMsg = '🔄 경주 전환: ' + d.raceKey + ' (자동추종)';
        try { notify('🔄 경주 전환 감지 → ' + d.raceKey + ' 자동 추종', true); } catch (_) { /* */ }
        // ③ 새 경주 배당 즉시 1회 수집(전환 지연 제거)
        Promise.resolve(fetchKeibaOdds(d.raceKey, false)).catch(() => { /* */ });
      }
    } catch (_) { /* 감지 실패는 조용히(다음 주기 재시도) */ }
    finally { _keibaOdds.detecting = false; }
  }

  /** raceKey → 짧은 라벨(예: '2026-07-05_서울_5' → '2026-07-05 서울 5R') */
  function _rkLabel(rk) {
    if (!rk) return '';
    return String(rk).replace(/_/g, ' ').replace(/(\d+)\s*경주/, '$1R').trim();
  }

  /** [1번] 패널이 표시할 '현재 경주' 설정 — 한국·일본 흐름 모두 여기로 현재 raceKey를 알려준다.
   *  새 경주로 바뀌면 이전 경주 이상감지를 즉시 비우고(잔존 방지) 새 경주만 누적한다. */
  function setAnomalyPanelRace(rk) {
    if (!rk || _closing.panelRk === rk) return;
    _closing.panelRk = rk;
    _closing.historyMode = false;   // 새 경주로 전환 시 자동으로 '현재 보기'로 복귀
    _closing.lastEvents = [];
    // [1번] 새 경주 이벤트 로드 전까지 패널을 비워 이전 경주 데이터가 섞여 보이지 않게 함
    const panel = document.getElementById('anomalyFeedPanel');
    if (panel) { panel.innerHTML = ''; panel.style.display = 'none'; }
    refreshAnomalyFeed(rk);
  }

  /** [1번] 이상감지 누적 패널 완전 초기화 — 새 경주 시작·하드리셋 시 호출(경주 전환 시 완전 리셋). */
  function resetAnomalyPanel() {
    _closing.panelRk = null;
    _closing.historyMode = false;
    _closing.lastEvents = [];
    const panel = document.getElementById('anomalyFeedPanel');
    if (panel) { panel.innerHTML = ''; panel.style.display = 'none'; }
  }

  /** 이상감지 이벤트 배열 → 행 HTML(시각·발주전·심각도색) */
  function _anomalyRows(ev) {
    // [순서 변경] 최신 감지 내역이 맨 위 — 서버는 시간순(오래된→최신)으로 주므로 복사본을 reverse.
    return (ev || []).slice().reverse().map((e) => {
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
    // [1번] 헤더에 현재 경주명 직접 표기 → "🚨 이상감지 (소노다 11경주)" (경주별 분리 명확화)
    panel.innerHTML = _panelHeader(`🚨 이상감지 <span style="color:#e2e8f0">(${esc(_rkLabel(rk))})</span> <span style="color:#94a3b8;font-weight:400">${ev.length}건</span>`,
        `<span id="anomalyHistBtn" title="이전 경주 이상감지 보기(결과기록 탭에서도 확인)" style="cursor:pointer;color:#8ab4f8;padding:0 6px;font-weight:600">📜 히스토리</span>`)
      + _anomalyRows(ev);
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
    // [경마 oddspark 서버 수집] 마감시각 유무와 무관하게 적응형 폴링(없으면 평상 30초). 확장 없이 동작.
    const _keibaLeft = dlMs ? (dlMs - Date.now()) : Infinity;
    // [경주 자동추종] 현재 발매중 경주번호 감지 → 전환 시 raceKey 즉시 갱신(폴링보다 먼저 실행해 새 경주로 수집)
    try { keibaDetectCurrentRace(rk, _keibaLeft); } catch (_) { /* */ }
    try { keibaOddsAutoPoll(_closing.panelRk || rk, _keibaLeft); } catch (_) { /* */ }
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
    // [경마 oddspark 서버 수집] 토글 + 즉시 1회 조회 배선(설정 localStorage 유지)
    { const chk = document.getElementById('keibaOddsAutoChk');
      if (chk) {
        try { chk.checked = localStorage.getItem('keibaOddsAuto') === '1'; } catch (_) { /* */ }
        _keibaOdds.enabled = chk.checked;
        _renderKeibaStatus();
        chk.addEventListener('change', () => {
          _keibaOdds.enabled = chk.checked;
          try { localStorage.setItem('keibaOddsAuto', chk.checked ? '1' : '0'); } catch (_) { /* */ }
          if (chk.checked) {   // 켜면 즉시 1회 수집 + 이후 적응형 폴링
            _keibaOdds.lastPoll = 0; _keibaOdds.lastCounts = null; _keibaOdds.lastMsg = '현재 경주 조회 중…'; _renderKeibaStatus();
            const rk = _closing.panelRk || getActiveRaceKey();
            if (rk && !jpIsKoreaName(rk) && !jpIsCentralName(rk)) fetchKeibaOdds(rk, true);
          } else _renderKeibaStatus();
        });
      }
    }
    { const b = document.getElementById('keibaOddsOnceBtn');
      if (b) b.addEventListener('click', () => fetchKeibaOdds(_keibaTargetRk(), false)); }
    // [경주 지정] pin/자동 토글 — 경주가 안 넘어갈 때 현재 경주를 직접 지정
    { const setb = document.getElementById('keibaOddsRkSet'), clrb = document.getElementById('keibaOddsRkClear'),
          inp = document.getElementById('keibaOddsRk');
      if (setb && inp) setb.addEventListener('click', () => {
        _keibaOdds.pinnedRk = (inp.value || '').trim();
        _keibaOdds.lastPoll = 0; _keibaOdds.lastCounts = null;
        if (clrb) clrb.style.display = _keibaOdds.pinnedRk ? '' : 'none';
        _keibaOdds.lastMsg = _keibaOdds.pinnedRk ? ('경주 지정: ' + _keibaOdds.pinnedRk) : '자동추종';
        _renderKeibaStatus();
        if (_keibaOdds.pinnedRk) fetchKeibaOdds(_keibaOdds.pinnedRk, false);
      });
      if (clrb && inp) clrb.addEventListener('click', () => {
        _keibaOdds.pinnedRk = ''; inp.value = ''; clrb.style.display = 'none';
        _keibaOdds.lastMsg = '자동추종'; _renderKeibaStatus();
      });
    }
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
  // [중앙경마 oddspark 미지원] JRA 경마장 = oddspark 서버 수집 대상 아님(로그인/POST 필요) → Chrome 확장으로만 수집.
  const JP_CENTRAL_TRACKS = ['도쿄', '나카야마', '한신', '쿄토', '교토', '삿포로', '하코다테', '후쿠시마', '니가타', '주쿄', '고쿠라', '코쿠라',
    '東京', '中山', '阪神', '京都', '札幌', '函館', '福島', '新潟', '中京', '小倉'];
  function jpIsCentralName(s) { return JP_CENTRAL_TRACKS.some((t) => (s || '').indexOf(t) >= 0); }
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

  // ══════════ [분석기록] 검색 가능한 통합 분석/배팅 기록 (전 종목) ══════════
  //   모든 종목(경마 지방/중앙·경륜·경정·바이크·한국)의 "어떻게 분석했는지"를 검색·복기.
  const REC_CAT_LABEL = { horse: '🏇 경마', japan_local: '🇯🇵 지방', japan_central: '🏇 중앙',
    boat: '🚤 경정', cycle: '🚴 경륜', bike: '🏍 바이크', korea: '🇰🇷 한국' };
  let _recCache = null;
  const _recAllCfg = { listSel: '#recAnalysisList', detailSel: '#recAnalysisDetail', category: 'all', query: '', sort: 'latest', period: 'all' };

  // [보완3] 기간 필터: date 문자열(YYYY-MM-DD)이 최근 N일 이내인지. period='all'이면 전체.
  function _recWithinPeriod(dateStr, period) {
    if (!period || period === 'all') return true;
    const days = period === '7d' ? 7 : period === '30d' ? 30 : 0;
    if (!days || !dateStr) return true;
    const d = new Date(dateStr + 'T00:00:00');
    if (isNaN(d.getTime())) return true;   // 파싱 실패 시 제외하지 않음
    const cutoff = new Date(); cutoff.setHours(0, 0, 0, 0); cutoff.setDate(cutoff.getDate() - (days - 1));
    return d.getTime() >= cutoff.getTime();
  }

  async function loadRecCache(force) {
    if (_recCache && !force) return _recCache;
    try { _recCache = (await (await fetch('/api/analysis-log/list')).json()).logs || []; }
    catch (_) { _recCache = []; }
    return _recCache;
  }

  // 기록 1건 → 카드 HTML(정렬/그룹 공통). sort='pnl'일 때 손익 배지 표시.
  function _recItemHtml(l, detailSel, showPnl) {
    const badge = l.hasResult ? (l.won ? '✅ 적중' : '❌ 미적중') : '⬜ 결과대기';
    const bc = l.hasResult ? (l.won ? '#38d39f' : '#f87171') : '#94a3b8';
    const catB = REC_CAT_LABEL[l.category] || '';
    const kh = (l.keyHorses || []).length ? ' · 유력마 ' + l.keyHorses.join('·') : '';
    const t3 = (l.top3 || []).filter((x) => x != null && x !== '').join('-');
    const pnlB = (showPnl && l.pnl != null && l.pnl !== '')
      ? `<span style="font-size:11px;font-weight:700;color:${l.pnl >= 0 ? '#38d39f' : '#f87171'}">${l.pnl >= 0 ? '+' : ''}${Number(l.pnl).toLocaleString('ko-KR')}원</span>` : '';
    const revB = l.reviewed ? '<span title="복기완료" style="font-size:11px;color:#c4b5fd;font-weight:700">🧠</span>' : '';
    return `<div class="rec-item" data-file="${esc(l.file)}" data-rk="${esc(l.raceKey || l.race || '')}" data-detail="${esc(detailSel)}" style="cursor:pointer;margin:3px 0;padding:6px 8px;border:1px solid var(--border);border-radius:6px">
      <div style="display:flex;justify-content:space-between;gap:8px;align-items:center">
        <b>${esc(l.race || l.race_id || '')}</b><span style="display:flex;gap:6px;align-items:center">${revB}${pnlB}<span style="font-size:11px;color:${bc};font-weight:700">${badge}</span></span></div>
      <div class="hint" style="font-size:11px;margin-top:2px">${catB ? catB + ' · ' : ''}${esc(l.date || '')} ${esc(l.analyzed_at || '')} · 신호 ${l.signals || 0}${kh}${t3 ? ' · 착순 ' + esc(t3) : ''}</div>
      ${l.summary ? `<div class="hint" style="font-size:11px;margin-top:1px">${esc(l.summary)}</div>` : ''}</div>`;
  }

  // cfg = {listSel, detailSel, category('all'|종목), query, sort('latest'|'won'|'pnl'), period('all'|'7d'|'30d')}
  function renderRecList(cfg) {
    const listEl = document.querySelector(cfg.listSel); if (!listEl) return;
    let logs = _recCache || [];
    if (cfg.category && cfg.category !== 'all') logs = logs.filter((l) => (l.category || '') === cfg.category);
    logs = logs.filter((l) => _recWithinPeriod(l.date, cfg.period));   // [보완3] 기간 필터
    const q = (cfg.query || '').trim().toLowerCase();
    if (q) logs = logs.filter((l) => ((l.race || '') + ' ' + (l.raceKey || '') + ' ' + (l.date || '')
      + ' ' + (l.summary || '') + ' ' + (l.keyHorses || []).join(' ') + ' ' + (REC_CAT_LABEL[l.category] || '')).toLowerCase().includes(q));
    if (!logs.length) {
      listEl.innerHTML = `<p class="hint">${q || (cfg.category && cfg.category !== 'all') || (cfg.period && cfg.period !== 'all') ? '조건에 맞는 기록이 없습니다.' : '분석 기록이 없습니다. 배당을 수집·분석하면 자동으로 쌓입니다.'}</p>`;
      return;
    }
    const sort = cfg.sort || 'latest';
    const head = `<div class="hint" style="margin:2px 0 6px">총 ${logs.length}건</div>`;
    if (sort === 'won' || sort === 'pnl') {
      // [보완3] 적중순/손익순 — 날짜 그룹 없이 평면 정렬 리스트(순위가 의미 있도록).
      const arr = logs.slice();
      if (sort === 'won') {
        // 적중 → 미적중 → 결과대기, 동순위는 최신순
        const rank = (l) => l.hasResult ? (l.won ? 0 : 1) : 2;
        arr.sort((a, b) => (rank(a) - rank(b)) || String(b.date || '').localeCompare(String(a.date || '')) || String(b.analyzed_at || '').localeCompare(String(a.analyzed_at || '')));
      } else {
        // 손익 큰 순, 결과 없는 건(null)은 뒤로
        const pv = (l) => (l.pnl == null || l.pnl === '') ? null : Number(l.pnl);
        arr.sort((a, b) => { const x = pv(a), y = pv(b); if (x == null && y == null) return String(b.date || '').localeCompare(String(a.date || '')); if (x == null) return 1; if (y == null) return -1; return y - x; });
      }
      listEl.innerHTML = head + arr.map((l) => _recItemHtml(l, cfg.detailSel, sort === 'pnl')).join('');
    } else {
      // 최신순 — 날짜별 그룹
      const byDate = {};
      logs.forEach((l) => { (byDate[l.date || '?'] = byDate[l.date || '?'] || []).push(l); });
      listEl.innerHTML = head + Object.keys(byDate).sort().reverse().map((date) =>
        `<div style="margin-bottom:8px"><div class="hint" style="font-weight:700;margin:4px 0">${esc(date)} · ${byDate[date].length}건</div>`
        + byDate[date].map((l) => _recItemHtml(l, cfg.detailSel, false)).join('') + '</div>').join('');
    }
    listEl.querySelectorAll('.rec-item').forEach((c) => c.addEventListener('click',
      () => openAnalysisRecord(c.dataset.file, c.dataset.rk, c.dataset.detail)));
  }

  async function openAnalysisRecord(file, rk, detailSel) {
    const el = document.querySelector(detailSel); if (!el) return;
    el.innerHTML = '<p class="hint">불러오는 중…</p>';
    let d; try { d = await (await fetch('/api/analysis-log/get', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file }) })).json(); }
    catch (e) { el.innerHTML = `<p class="hint" style="color:var(--red)">${esc(e.message)}</p>`; return; }
    if (d.error) { el.innerHTML = `<p class="hint">${esc(d.error)}</p>`; return; }
    const erk = rk || d.raceKey || d.race || '';
    el.innerHTML = renderAnalysisDetail(d, erk, file);
    // [보완2] 결과 입력 폼 토글 + 저장 배선
    const form = el.querySelector('.recResultForm');
    if (form) {
      const tog = form.querySelector('.recResToggle'), body = form.querySelector('.recResBody');
      if (tog && body) tog.addEventListener('click', () => { body.style.display = body.style.display === 'none' ? 'block' : 'none'; });
      const sv = form.querySelector('.recResSave');
      if (sv) sv.addEventListener('click', () => saveRecResult(form, file, erk, detailSel));
    }
    // [복기 표식] 복기 메모 저장 배선
    const rbox = el.querySelector('.reviewNoteBox');
    if (rbox) {
      const rsv = rbox.querySelector('.reviewNoteSave');
      if (rsv) rsv.addEventListener('click', () => saveReviewNote(rbox, file, erk, detailSel));
    }
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  // [복기 표식·학습] 복기 메모 저장 → 서버가 reviewed 마킹 + 학습 코퍼스 축적. 저장 후 상세 재조회로 배지 표시.
  async function saveReviewNote(box, file, rk, detailSel) {
    const msg = box.querySelector('.reviewNoteMsg');
    const ta = box.querySelector('.reviewNoteInput');
    const review = ta ? ta.value.trim() : '';
    if (!review) { if (msg) { msg.style.color = 'var(--red)'; msg.textContent = '메모를 입력하세요'; } return; }
    if (msg) { msg.style.color = ''; msg.textContent = '저장 중…'; }
    let d; try { d = await (await fetch('/api/analysis-log/memo', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file, raceKey: rk, review }) })).json(); }
    catch (e) { if (msg) { msg.style.color = 'var(--red)'; msg.textContent = '실패: ' + e.message; } return; }
    if (d.error) { if (msg) { msg.style.color = 'var(--red)'; msg.textContent = d.error; } return; }
    if (msg) { msg.style.color = '#c4b5fd'; msg.textContent = '🧠 복기 저장 완료 · 기억됨'; }
    try { await loadRecCache(true); renderRecList(_recAllCfg); } catch (_) { /* */ }
    setTimeout(() => { try { openAnalysisRecord(file, rk, detailSel); } catch (_) { /* */ } }, 400);
  }

  // 복기 상세(어떻게 분석했는지) + [보완2] 결과 입력/수정 폼.
  function renderAnalysisDetail(d, rk, file) {
    const sig = d.signals_detected || [], fr = d.final_recommendation || {}, elim = d.elimination || {};
    const horses = d.horses || [];
    const gradeOf = {}; horses.forEach((h) => { gradeOf[h.no] = h.grade; });
    const cand = elim.candidates || [], elimNo = elim.eliminated || [];
    const catB = REC_CAT_LABEL[d.category] || '';
    const candHtml = cand.length ? cand.map((n) => `<span class="chip">${n}번${gradeOf[n] ? '(' + esc(String(gradeOf[n])) + ')' : ''}</span>`).join(' ') : '<span class="hint">없음</span>';
    const elimHtml = elimNo.length ? elimNo.map((n) => `<span class="chip chip-red">${n}번</span>`).join(' ') : '<span class="hint">없음</span>';
    const sev = (s) => /🔴|🚨|🌊/.test(s || '') ? 'chip-red' : '';
    const sigHtml = sig.length ? sig.slice(0, 14).map((s) => `<div style="margin:2px 0"><span class="chip ${sev(s.severity)}">${esc(s.severity || '')} ${esc(s.type || '')}</span> ${esc(s.detail || '')}${s.reason ? ` <span class="hint">— ${esc(s.reason)}</span>` : ''}</div>`).join('') : '<div class="hint">감지된 이상신호 없음</div>';
    const recRow = (k, label) => { const r = fr[k]; return r ? `<tr><td>${label}</td><td style="font-weight:700">${esc(r.combo)}</td><td>${r.odds != null ? r.odds + '배' : '-'}</td><td class="hint">${esc(r.reason || '')}</td></tr>` : ''; };
    const frBody = recRow('quinella_main', '복승 메인') + recRow('quinella_sub', '복승 보조') + recRow('trifecta_main', '삼복승 메인') + recRow('trifecta_insurance1', '삼복승 보험1') + recRow('trifecta_insurance2', '삼복승 보험2');
    const frHtml = frBody ? `<table class="data-table"><thead><tr><th>종류</th><th>조합</th><th>배당</th><th>근거</th></tr></thead><tbody>${frBody}</tbody></table>` : '<div class="hint">추천 조합 기록 없음</div>';
    let resHtml;
    const res = d.result || null, hit = d.hit || null;
    if (res) {
      const top3 = [res['1st'], res['2nd'], res['3rd']].filter((x) => x != null && x !== '').join('-');
      const yn = (b) => b ? '<span style="color:#38d39f;font-weight:700">✅적중</span>' : '<span style="color:#f87171;font-weight:700">❌미적중</span>';
      resHtml = `<div style="margin:2px 0">실제 착순: <b>${esc(top3) || '-'}</b></div>
        ${hit ? `<div style="margin:2px 0">복승 ${yn(hit.quinella_hit)} · 삼복승 ${yn(hit.trifecta_hit)}${hit.pnl != null ? ` · 손익 <b style="color:${hit.pnl >= 0 ? '#38d39f' : '#f87171'}">${(hit.pnl >= 0 ? '+' : '') + Number(hit.pnl).toLocaleString('ko-KR')}원</b>` : ''}</div>` : ''}`;
    } else {
      resHtml = `<div class="hint" style="padding:20px 4px;text-align:center;line-height:1.7">⬜ 결과 미입력<br>아래 <b>결과 입력</b> 또는 <b>결과기록 탭</b>에서 착순을 넣으면<br>적중·손익·복기 대조가 표시됩니다.</div>`;
    }
    // [복기 표식·학습] 복기 메모 입력/저장 + "복기완료" 배지. 저장 시 서버가 reviewed 마킹 + 학습 코퍼스 축적.
    const revBadge = d.reviewed ? `<span class="chip" style="border-color:#a78bfa;color:#c4b5fd">🧠 복기완료${d.reviewed_at ? ' · ' + esc(d.reviewed_at) : ''}</span>` : '';
    const memoHtml = `<div class="reviewNoteBox" data-file="${esc(file || d.file || '')}" data-rk="${esc(rk)}" style="margin-top:10px;border-top:1px dashed var(--border);padding-top:8px">
      <div class="matrix-title" style="font-size:13px">📝 복기 메모 ${revBadge}</div>
      <textarea class="cfg-input reviewNoteInput" rows="2" style="width:100%;max-width:420px;resize:vertical" placeholder="이 경주에서 배운 점·놓친 신호·판단 근거를 남기면 종목·적중과 함께 기억됩니다.">${esc(d.review || '')}</textarea>
      <div class="cfg-row" style="margin-top:4px">
        <button class="btn btn-primary reviewNoteSave" style="font-size:12px">🧠 복기 저장 · 기억</button>
        <span class="hint reviewNoteMsg" style="margin-left:6px"></span>
      </div></div>`;
    // [보완2] 결과 입력/수정 폼 — /api/history/record-result 재사용. 패널별 클래스로 스코프(ID 충돌 방지).
    const rv = (x) => (x != null && x !== '') ? x : '';
    const resObj = res || {};
    const formHtml = `<div class="recResultForm" data-file="${esc(file || d.file || '')}" data-rk="${esc(rk)}" style="margin-top:10px;border-top:1px dashed var(--border);padding-top:8px">
      <button class="btn recResToggle" style="font-size:12px">${res ? '✏️ 결과 수정' : '✍️ 결과 입력'}</button>
      <div class="recResBody" style="display:none;margin-top:8px">
        <div class="cfg-row" style="gap:6px;align-items:center;flex-wrap:wrap">
          <label class="hint">1착 <input class="cfg-input recRes1" type="number" min="1" style="width:58px" value="${rv(resObj['1st'])}"></label>
          <label class="hint">2착 <input class="cfg-input recRes2" type="number" min="1" style="width:58px" value="${rv(resObj['2nd'])}"></label>
          <label class="hint">3착 <input class="cfg-input recRes3" type="number" min="1" style="width:58px" value="${rv(resObj['3rd'])}"></label>
          <label class="hint">4착 <input class="cfg-input recRes4" type="number" min="1" style="width:58px" value="${rv(resObj['4th'])}" title="추천 말이 4착이면 '아깝게 미적중' 학습"></label>
        </div>
        <div class="cfg-row" style="gap:6px;align-items:center;flex-wrap:wrap;margin-top:4px">
          <label class="hint">투자금액 <input class="cfg-input recStake" type="number" min="0" step="1000" style="width:96px" value="${rv((hit || {}).stake || 1000)}">원</label>
          <label class="hint">실수령 배당금(선택) <input class="cfg-input recPayout" type="number" min="0" style="width:116px" placeholder="적중 시 총 수령액">원</label>
        </div>
        <div class="cfg-row" style="gap:6px;align-items:center;flex-wrap:wrap;margin-top:4px">
          <label class="hint">확정 복승배당 <input class="cfg-input recQOdds" type="number" min="1" step="0.1" style="width:78px" placeholder="배">배</label>
          <label class="hint">확정 삼복승배당 <input class="cfg-input recTOdds" type="number" min="1" step="0.1" style="width:78px" placeholder="배">배</label>
        </div>
        <div class="cfg-row" style="margin-top:4px">
          <label class="hint" style="flex:1">메모 <input class="cfg-input recMemo" type="text" style="width:100%;max-width:320px" placeholder="예: 선행마 도주 성공 / 인기마 출발 지연"></label>
        </div>
        <div class="cfg-row" style="margin-top:6px">
          <button class="btn btn-primary recResSave">💾 결과 저장 · 자동 판정</button>
          <span class="hint recResMsg" style="margin-left:6px"></span>
        </div>
        <p class="hint" style="margin:4px 0 0">실수령 배당금 입력 시 정확한 손익 계산. 저장하면 적중·손익·학습 통계가 즉시 갱신됩니다.</p>
      </div></div>`;
    // [복기 시각화] 경기 전(예측) / 경기 후(실제) 2단 분리 — 전 종목·한국 공통
    const compareBlock = _reviewCompareBlock(d);
    const preBlock = `<div style="flex:1;min-width:300px;border:1px solid #4ea1ff55;border-radius:8px;padding:10px;background:rgba(78,161,255,.05)">
      <div class="matrix-title" style="color:#4ea1ff">🔮 경기 전 분석 <span class="hint" style="font-weight:400">(배당·전적 기반 예측)</span></div>
      <div class="matrix-title" style="font-size:13px;margin-top:6px">🏇 유력마 / 제거마</div>
      <div style="margin:2px 0"><b>유력마:</b> ${candHtml}</div>
      <div style="margin:2px 0"><b>제거마:</b> ${elimHtml}</div>
      <div class="matrix-title" style="font-size:13px;margin-top:8px">🚨 이상감지 내역</div>${sigHtml}
      <div class="matrix-title" style="font-size:13px;margin-top:8px">🎯 추천 조합 (당시)</div>${frHtml}${_recHistoryBlock(d)}</div>`;
    const postBlock = `<div style="flex:1;min-width:300px;border:1px solid #38d39f55;border-radius:8px;padding:10px;background:rgba(56,189,248,.05)">
      <div class="matrix-title" style="color:#38d39f">🏁 경기 후 분석 <span class="hint" style="font-weight:400">(실제 결과·복기)</span></div>
      ${resHtml}</div>`;
    return `<div style="border:1px solid var(--border);border-radius:8px;padding:12px">
      <div class="matrix-title">${esc(d.race || rk)} <span class="hint" style="font-weight:400">${catB ? catB + ' · ' : ''}${esc(d.date || '')} · 분석 ${esc(d.analyzed_at || '')}</span></div>
      ${compareBlock}
      <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:8px;align-items:stretch">${preBlock}${postBlock}</div>
      ${memoHtml}${formHtml}</div>`;
  }

  // [보완2] 분석기록 상세의 결과 입력 폼 저장 — 패널 스코프(container)로 입력값 읽기.
  async function saveRecResult(container, file, rk, detailSel) {
    const msg = container.querySelector('.recResMsg');
    const g = (cls) => { const e = container.querySelector('.' + cls); return e ? e.value.trim() : ''; };
    const n1 = g('recRes1');
    if (!n1) { if (msg) { msg.style.color = 'var(--red)'; msg.textContent = '최소 1착은 입력하세요'; } return; }
    const result = { '1st': parseInt(n1, 10) };
    const n2 = g('recRes2'), n3 = g('recRes3'), n4 = g('recRes4');
    if (n2) result['2nd'] = parseInt(n2, 10);
    if (n3) result['3rd'] = parseInt(n3, 10);
    if (n4) result['4th'] = parseInt(n4, 10);
    const stake = g('recStake'), payout = g('recPayout'), qo = g('recQOdds'), to = g('recTOdds'), memo = g('recMemo');
    const payload = { raceKey: rk, result };
    if (stake) { payload.stake = parseInt(stake, 10); payload.budget = parseInt(stake, 10); }
    if (payout) payload.payout = parseInt(payout, 10);
    if (qo) payload.quinellaOdds = parseFloat(qo);
    if (to) payload.trifectaOdds = parseFloat(to);
    if (memo) payload.memo = memo;
    if (msg) { msg.style.color = ''; msg.textContent = '저장·판정 중…'; }
    let d; try { d = await (await fetch('/api/history/record-result', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })).json(); }
    catch (e) { if (msg) { msg.style.color = 'var(--red)'; msg.textContent = '실패: ' + e.message; } return; }
    if (d.error) { if (msg) { msg.style.color = 'var(--red)'; msg.textContent = d.error; } return; }
    const derr = d.dataErrors || [];
    if (msg) { msg.style.color = derr.length ? '#ffb020' : '#38d39f'; msg.textContent = derr.length ? '✅ 저장(⚠️ ' + derr.join(', ') + ')' : '✅ 저장·판정 완료'; }
    // 통계/캐시 갱신 후 상세 재조회(결과·적중·손익 반영)
    try { loadLearningStats(); } catch (_) { /* */ }
    try { renderStats(); } catch (_) { /* */ }
    try { await loadRecCache(true); } catch (_) { /* */ }
    setTimeout(() => { try { openAnalysisRecord(file, rk, detailSel); } catch (_) { /* */ } }, 400);
  }

  // 결과기록 탭 '분석기록' 서브탭 진입 → 캐시 로드 + 검색/필터 UI 배선(1회)
  let _recAllWired = false;
  async function loadAnalysisRecordsAll() {
    await loadRecCache(true);
    renderRecList(_recAllCfg);
    if (_recAllWired) return;
    _recAllWired = true;
    const s = document.querySelector('#recSearchInput');
    if (s) s.addEventListener('input', () => { _recAllCfg.query = s.value; renderRecList(_recAllCfg); });
    document.querySelectorAll('#recCatChips .rec-cat-chip').forEach((chip) => chip.addEventListener('click', () => {
      _recAllCfg.category = chip.dataset.cat || 'all';
      document.querySelectorAll('#recCatChips .rec-cat-chip').forEach((c) => c.classList.toggle('active', c === chip));
      renderRecList(_recAllCfg);
    }));
    const rf = document.querySelector('#recRefreshBtn');
    if (rf) rf.addEventListener('click', async () => { await loadRecCache(true); renderRecList(_recAllCfg); });
    const ss = document.querySelector('#recSortSel');
    if (ss) ss.addEventListener('change', () => { _recAllCfg.sort = ss.value || 'latest'; renderRecList(_recAllCfg); });
    const ps = document.querySelector('#recPeriodSel');
    if (ps) ps.addEventListener('change', () => { _recAllCfg.period = ps.value || 'all'; renderRecList(_recAllCfg); });
  }

  // 스포츠 탭(경정/경륜/바이크/중앙)의 '분석 기록' 섹션 로드(해당 종목만)
  //   탭 키(boat/cycle/bike/central) → category(central은 japan_central) 매핑.
  const SPORT_TAB_TO_CAT = { boat: 'boat', cycle: 'cycle', bike: 'bike', central: 'japan_central' };
  async function loadSportRecords(tabKey) {
    await loadRecCache();
    renderRecList({ listSel: '#sportRec-' + tabKey, detailSel: '#sportRecDetail-' + tabKey,
      category: SPORT_TAB_TO_CAT[tabKey] || tabKey, query: '' });
  }

  // ══════════ [경륜 출마표 분석] oddspark 선수 전적 자동 수집 ══════════
  let _keirinOddsTimer = null;
  function initKeirinCard() {
    const btn = document.querySelector('#keirinFetchBtn');
    if (btn) btn.addEventListener('click', fetchKeirinCard);
    const ob = document.querySelector('#keirinOddsBtn');
    if (ob) ob.addEventListener('click', () => fetchKeirinOdds(false));
    const poll = document.querySelector('#keirinOddsPoll');
    if (poll) poll.addEventListener('change', () => {
      if (_keirinOddsTimer) { clearInterval(_keirinOddsTimer); _keirinOddsTimer = null; }
      // [자동수집 버그수정] 30초마다 '현재 경주 자동감지' 후 수집 → 경주가 바뀌어도 새 경주를 따라간다.
      //   기존엔 고정 입력값(joCode/raceNo)만 반복 fetch해 같은 경주만 수집되던 문제 해결.
      if (poll.checked) { keirinAutoTick(); _keirinOddsTimer = setInterval(keirinAutoTick, 30000); }
    });
    // [지방경마 출주표 전적] oddspark 出走表 분석 버튼
    const nb = document.querySelector('#narFetchBtn');
    if (nb) nb.addEventListener('click', fetchKeibaStarters);
    // [중앙경마(JRA) 출주표 전적] netkeiba 馬柱 분석 버튼
    const jb = document.querySelector('#jraFetchBtn');
    if (jb) jb.addEventListener('click', fetchJraStarters);
  }

  // [경륜 배당 직접조회] oddspark 복승·쌍승을 서버 경유로 가져와 파이프라인(역배열·배당변화·이상감지) 반영.
  async function fetchKeirinOdds(silent) {
    const out = document.querySelector('#keirinOddsResult'); if (!out) return;
    const g = (id) => { const e = document.querySelector(id); return e ? e.value.trim() : ''; };
    const url = g('#keirinUrl'), jo = g('#keirinJo'), ymd = g('#keirinYmd'), race = g('#keirinRace');
    const rk = g('#keirinRaceKey') || (_closing && _closing.panelRk) || getActiveRaceKey() || '';
    if (!rk) { out.innerHTML = '<span style="color:var(--red)">배당을 연결할 raceKey를 입력하세요(예: 히로시마 2경주).</span>'; return; }
    const payload = { raceKey: rk };
    if (url) payload.url = url;
    else if (jo && ymd && race) { payload.joCode = jo; payload.kaisaiBi = ymd; payload.raceNo = race; }
    else { out.innerHTML = '<span style="color:var(--red)">경륜장코드+개최일+경주 또는 URL을 입력하세요.</span>'; return; }
    if (!silent) out.innerHTML = '💰 oddspark 배당을 가져오는 중…';
    let d; try { d = await (await fetch('/api/keirin/odds', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })).json(); }
    catch (e) { out.innerHTML = `<span style="color:var(--red)">실패: ${esc(e.message)}</span>`; return; }
    if (d.error) { out.innerHTML = `<span style="color:var(--red)">${esc(d.error)}</span>`; return; }
    const c = d.counts || {};
    const t = new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    out.innerHTML = `<span style="color:#38d39f">✅ 배당 반영</span> — 복승 <b>${c.quinella || 0}</b>·쌍승 <b>${c.exacta || 0}</b> 조합 (<b>${esc(rk)}</b>, ${t})${_keirinOddsTimer ? ' · 🔄자동갱신중' : ''}`;
    // 배당이 들어가면 현재 라이브 분석 재조회(역배열·배당변화 반영)
    try { refreshCurrentRace(); } catch (_) { /* */ }
  }

  // [자동수집 버그수정] 30초 자동갱신 틱 — '현재 경주 자동감지' 후 입력·raceKey 갱신 → 새 경주 수집.
  //   joCode 지정 시 /api/keirin/current 로 현재 발매중 경주를 감지해 raceNo/raceKey 를 자동 업데이트한다.
  //   → 경주가 바뀌어도 자동으로 새 경주를 따라감(기존 고정입력 반복 fetch 문제 해결). joCode 없으면 기존 방식 폴백.
  let _keirinFollowRace = '';
  async function keirinAutoTick() {
    const g = (id) => { const e = document.querySelector(id); return e ? e.value.trim() : ''; };
    const out = document.querySelector('#keirinOddsResult');
    const jo = g('#keirinJo');
    let ymd = g('#keirinYmd');
    if (!ymd) { const n = new Date(); ymd = `${n.getFullYear()}${String(n.getMonth() + 1).padStart(2, '0')}${String(n.getDate()).padStart(2, '0')}`; }
    if (!jo) { return fetchKeirinOdds(true); }   // 경륜장코드 없으면 자동추적 불가 → 기존(고정입력) 방식
    let cur = null;
    try {
      const d = await (await fetch(`/api/keirin/current?joCode=${encodeURIComponent(jo)}&kaisaiBi=${encodeURIComponent(ymd)}`)).json();
      cur = d && d.current;
    } catch (_) { /* 감지 실패 시 기존 입력값으로 수집 */ }
    if (cur && cur.raceNo != null) {
      const rEl = document.querySelector('#keirinRace'); if (rEl) rEl.value = cur.raceNo;
      const yEl = document.querySelector('#keirinYmd'); if (yEl && cur.kaisaiBi) yEl.value = cur.kaisaiBi;
      const rkEl = document.querySelector('#keirinRaceKey'); if (rkEl && cur.raceKey) rkEl.value = cur.raceKey;
      if (cur.raceKey && cur.raceKey !== _keirinFollowRace) {   // 경주 변경 감지 → 타이머 자연 리셋(다음 틱부터 새 경주)
        _keirinFollowRace = cur.raceKey;
        if (out) out.innerHTML = `🔄 현재 경주 자동추적: <b>${esc(cur.raceKey)}</b>${cur.postTime ? ` (발주 ${esc(cur.postTime)})` : ''} — 배당 수집 중…`;
      }
    }
    return fetchKeirinOdds(true);
  }

  async function fetchKeirinCard() {
    const out = document.querySelector('#keirinCardResult'); if (!out) return;
    const g = (id) => { const e = document.querySelector(id); return e ? e.value.trim() : ''; };
    const url = g('#keirinUrl'), jo = g('#keirinJo'), ymd = g('#keirinYmd'), race = g('#keirinRace');
    const payload = {};
    if (url) payload.url = url;
    else if (jo && ymd && race) { payload.joCode = jo; payload.kaisaiBi = ymd; payload.raceNo = race; }
    else { out.innerHTML = '<p class="hint" style="color:var(--red)">경륜장코드+개최일+경주 또는 URL을 입력하세요.</p>'; return; }
    // [live 통합] raceKey 입력값 우선 → 없으면 현재 경주(패널/활성) 자동 → 전적을 live 분석에 연동
    payload.raceKey = g('#keirinRaceKey') || (_closing && _closing.panelRk) || getActiveRaceKey() || '';
    out.innerHTML = '<p class="hint">🚴 oddspark에서 출마표를 가져오는 중…</p>';
    let d; try { d = await (await fetch('/api/keirin/card', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })).json(); }
    catch (e) { out.innerHTML = `<p class="hint" style="color:var(--red)">실패: ${esc(e.message)}</p>`; return; }
    if (d.error) { out.innerHTML = `<p class="hint" style="color:var(--red)">${esc(d.error)}</p>`; return; }
    out.innerHTML = renderKeirinCard(d);
    // 연동 성공 시 현재 라이브 분석 즉시 재조회(전적 반영된 유력마·근거 표시)
    if (d.linkedRaceKey) { try { refreshCurrentRace(); } catch (_) { /* */ } }
  }

  function renderKeirinCard(d) {
    const a = d.analysis || {}, c = d.card || {};
    const gbadge = (g) => { const col = { A: '#38d39f', B: '#4ea1ff', C: '#ffd24f', D: '#94a3b8' }[g] || '#94a3b8'; return `<span class="chip" style="border-color:${col};color:${col}">${g}등급</span>`; };
    const rows = (a.ranked || []).map((r) => {
      const kr = r.kimariteRatio || {};
      const krTxt = Object.keys(kr).length ? `逃${kr['도주']}/捲${kr['젖히기']}/差${kr['차입']}/マ${kr['마크']}` : '-';
      const bonus = r.styleBonus ? `<span style="color:#ffb26b">+${r.styleBonus} ${esc(r.styleType)}</span>` : '';
      const ch = r.chaku ? r.chaku.join('-') : '-';
      return `<tr>
        <td style="text-align:center;font-weight:700">${r.rank}</td>
        <td style="text-align:center;font-weight:700">${r.car}</td>
        <td>${esc(r.name || '')} <span class="hint" style="font-size:10px">${esc(r.area || '')} ${esc(r.classGrade || '')}</span></td>
        <td style="text-align:center">${gbadge(r.grade)}</td>
        <td style="text-align:right">${r.score != null ? r.score : '-'} ${bonus}${r.styleBonus ? ` <b>→${r.adjScore}</b>` : ''}</td>
        <td style="text-align:center" title="각질">${esc(r.styleLabel || '')}</td>
        <td style="text-align:center" title="1-2-3-외 착순">${ch}${r.rentai != null ? `<br><span class="hint" style="font-size:10px">2연대 ${r.rentai}%</span>` : ''}</td>
        <td class="hint" style="font-size:10px" title="결정수 비율">${krTxt}</td></tr>`;
    }).join('');
    const tend = c.tendency || {};
    const tendTxt = Object.keys(tend).length ? Object.entries(tend).map(([k, v]) => `${k} ${v}%`).join(' · ') : '';
    const lineTxt = (a.line || []).length ? a.line.join(' → ') : '';
    const linked = d.linkedRaceKey
      ? `<div class="hint" style="margin:2px 0 8px;padding:6px 9px;background:rgba(56,211,159,.14);border-left:3px solid #38d39f;border-radius:6px;color:#38d39f">✅ <b>live 분석 연동됨</b> — <b>${esc(d.linkedRaceKey)}</b> 의 유력마·📋추천 근거·통합등급에 이 전적(競走得点)이 배당(역배열·급락)과 통합 반영됩니다.</div>`
      : `<div class="hint" style="margin:2px 0 8px;padding:6px 9px;background:rgba(245,158,11,.12);border-left:3px solid #f59e0b;border-radius:6px;color:#f59e0b">⚠️ live 연동 안 됨(raceKey 미지정) — 전적만 표시. 상단 <b>raceKey</b> 칸에 현재 경주(예: 히로시마 2경주)를 넣으면 배당 분석에 통합됩니다.</div>`;
    return `<div style="border:1px solid var(--border);border-radius:8px;padding:12px;margin-top:8px">
      ${linked}
      <div class="matrix-title">🚴 ${esc(c.venue || '')} ${c.race_no != null ? c.race_no + 'R' : ''} <span class="hint" style="font-weight:400">${esc(c.dist || '')} ${esc(c.post || '')} 발주 · 선수 ${(c.riders || []).length}명</span></div>
      ${a.summary ? `<div style="margin:4px 0"><b>득점 상위:</b> ${esc(a.summary)}</div>` : ''}
      ${tendTxt ? `<div class="hint" style="margin:2px 0">🏁 경륜장 결정타 경향(최근1년): ${esc(tendTxt)}${a.favStyle ? ` · 유리 각질 <b>${esc(a.favStyle)}</b>` : ''}</div>` : ''}
      ${lineTxt ? `<div class="hint" style="margin:2px 0">🔗 라인(隊列) 예상 앞→뒤: <b>${esc(lineTxt)}</b></div>` : ''}
      <table class="data-table" style="margin-top:6px">
        <thead><tr><th>순</th><th>차</th><th>선수</th><th>등급</th><th>득점(보정)</th><th>각질</th><th>착순</th><th>결정수</th></tr></thead>
        <tbody>${rows}</tbody></table>
      ${(a.tips || []).length ? `<div class="matrix-title" style="font-size:12px;margin-top:8px">💡 분석 팁</div>` + a.tips.map((t) => `<div class="hint" style="margin:1px 0">· ${esc(t)}</div>`).join('') : ''}
      ${a.comment ? `<div class="hint" style="margin-top:6px">📝 oddspark 예상: ${esc(a.comment)}</div>` : ''}
      <p class="hint" style="font-size:11px;margin-top:6px">등급 기준: 競走得点 95+ A · 85~94 B · 75~84 C · &lt;75 D. 걸즈(L급)는 득점 스케일이 낮아 대부분 D로 표시될 수 있습니다(상대 랭킹·보정 점수로 비교하세요).</p>
    </div>`;
  }

  // [지방경마 출주표 전적] oddspark 出走表 + 전5경주(각질·거리변화·상3F)를 서버 경유 수집·표시.
  async function fetchKeibaStarters() {
    const out = document.querySelector('#narCardResult'); if (!out) return;
    const g = (id) => { const e = document.querySelector(id); return e ? e.value.trim() : ''; };
    const venue = g('#narVenue'), ymd = g('#narYmd'), race = g('#narRace');
    const rk = g('#narRaceKey') || (_closing && _closing.panelRk) || getActiveRaceKey() || '';
    const payload = { withDetail: true };
    if (rk) payload.raceKey = rk;
    if (venue) payload.venue = venue;
    if (ymd) payload.raceDy = ymd;
    if (race) payload.raceNb = race;
    if (!rk && !(venue && race)) { out.innerHTML = '<p class="hint" style="color:var(--red)">경마장+경주(또는 raceKey)를 입력하세요.</p>'; return; }
    out.innerHTML = '<p class="hint">🏇 oddspark에서 출주표·전적을 가져오는 중… (전5경주 병렬 수집)</p>';
    let d; try { d = await (await fetch('/api/keiba/starters', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })).json(); }
    catch (e) { out.innerHTML = `<p class="hint" style="color:var(--red)">실패: ${esc(e.message)}</p>`; return; }
    if (d.error) { out.innerHTML = `<p class="hint" style="color:var(--red)">${esc(d.error)}${d.scheduled ? ' · 오늘 개최: ' + d.scheduled.map(esc).join(', ') : ''}</p>`; return; }
    out.innerHTML = renderKeibaStarters(d);
    if (d.linkedRaceKey) { try { refreshCurrentRace(); } catch (_) { /* */ } }
  }

  function renderKeibaStarters(d) {
    const r = d.race || {}, hs = d.horses || [];
    const gbadge = (g) => { const col = { A: '#38d39f', B: '#4ea1ff', C: '#ffd24f', D: '#94a3b8' }[g] || '#94a3b8'; return `<span class="chip" style="border-color:${col};color:${col}">${g}</span>`; };
    const rows = hs.map((h) => {
      const bp = h.backPower === '강' ? ' <span title="뒷힘 강점(상3F 상위30%)">💪</span>' : '';
      const ins = h.backPowerInsurance ? ' <span class="hint" style="color:#ffd24f" title="거리 미경험이나 뒷힘 강점 → 삼복승 보험">⚡</span>' : '';
      return `<tr>
      <td style="text-align:center;font-weight:700">${h.rank}</td>
      <td style="text-align:center;font-weight:700">${h.no}</td>
      <td>${esc(h.name || '')}${bp}${ins} <span class="hint" style="font-size:10px">${esc(h.jockey || '')} ${h.weight != null ? h.weight + 'kg' : ''}</span></td>
      <td style="text-align:center">${gbadge(h.grade)}</td>
      <td style="text-align:right"><b>${h.totalScore}</b>${h.gradeBonus ? ` <span class="hint" style="font-size:9px;color:#4ea1ff">등급${h.gradeBonus > 0 ? '+' : ''}${h.gradeBonus}</span>` : ''}</td>
      <td style="text-align:center" title="각질(통과순위)">${esc(h.styleType || '-')}</td>
      <td style="text-align:center" title="최근5착순">${(h.recentPlacings || []).join('·') || '-'}</td>
      <td style="text-align:center" title="상3F 평균(막판스피드)">${h.last3f != null ? h.last3f : '-'}${h.backPower === '강' ? ' 💪' : ''}</td>
      <td class="hint" style="font-size:10px">${(h.detail || []).map(esc).join(' · ')}</td></tr>`;
    }).join('');
    // [3번] 💪 뒷힘 강점 말 요약(상3F 상위30%) — 거리 미경험이나 막판 뒤집기 후보 강조
    const strongBack = hs.filter((h) => h.backPower === '강');
    const backBox = strongBack.length ? `<div style="margin:8px 0;padding:8px 10px;background:rgba(56,211,159,.1);border-left:3px solid #38d39f;border-radius:6px">
      <div style="font-weight:700;color:#38d39f">💪 뒷힘 강점 말 <span class="hint" style="font-weight:400">(상3F 상위30% · 막판 뒤집기 가능)</span></div>
      ${strongBack.map((h) => `<div class="hint" style="margin-top:2px">· <b>${h.no}번 ${esc(h.name || '')}</b> — 상승3F ${h.last3f != null ? h.last3f + '초' : '-'}${h.distExperienced === false ? ' · <span style="color:#ffd24f">⚡ 거리 미경험이나 막판 뒤집기 가능(삼복승 보험)</span>' : ''}</div>`).join('')}
    </div>` : '';
    const linked = d.linkedRaceKey
      ? `<div class="hint" style="margin:2px 0 8px;padding:6px 9px;background:rgba(56,211,159,.14);border-left:3px solid #38d39f;border-radius:6px;color:#38d39f">✅ <b>live 통합 연동됨</b> — <b>${esc(d.linkedRaceKey)}</b> 유력마·통합등급(전적+배당역배열)에 이 전적이 반영됩니다.</div>`
      : `<div class="hint" style="margin:2px 0 8px;padding:6px 9px;background:rgba(245,158,11,.12);border-left:3px solid #f59e0b;border-radius:6px;color:#f59e0b">⚠️ live 연동 안 됨(raceKey 미지정) — 전적만 표시.</div>`;
    return `<div style="border:1px solid var(--border);border-radius:8px;padding:12px;margin-top:8px">
      ${linked}
      <div class="matrix-title">🏇 ${esc(r.venue || '')} ${r.raceNo != null ? r.raceNo + 'R' : ''} <span class="hint" style="font-weight:400">${esc(r.surface || '')}${r.distance != null ? r.distance + 'm' : ''} 마장 ${esc(r.trackCond || '?')} · ${hs.length}두</span></div>
      ${backBox}
      <table class="data-table" style="margin-top:6px">
        <thead><tr><th>순</th><th>마번</th><th>마명(기수·부담)</th><th>등급</th><th>총점</th><th>각질</th><th>최근착순</th><th>상3F</th><th>근거</th></tr></thead>
        <tbody>${rows}</tbody></table>
      <p class="hint" style="font-size:11px;margin-top:6px">전적점수 = 최근5착순 가중평균 + 각질(선행+3/추격+5) + 거리변화(단축+5) + 부담(감소+5) + <b>등급경험(G1~G3/A1 +20·오픈 +10)</b> + <b>당거리경험(+10)</b> + <b>뒷힘(상3F 상위30% +15)</b>. 등급=경주 내 사분위 상대. 💪=뒷힘 강점 · ⚡=거리 미경험이나 뒷힘 강점(삼복승 보험).</p>
    </div>`;
  }

  // [중앙경마(JRA) 출주표 전적] netkeiba 馬柱 전5주(각질·거리변화·상3F)를 서버 경유 수집·표시.
  //   말 데이터 구조가 지방경마와 동일 → 표시는 renderKeibaStarters 재사용(각질 소스만 netkeiba 표기 우선).
  async function fetchJraStarters() {
    const out = document.querySelector('#jraCardResult'); if (!out) return;
    const g = (id) => { const e = document.querySelector(id); return e ? e.value.trim() : ''; };
    const venue = g('#jraVenue'), ymd = g('#jraYmd'), race = g('#jraRace');
    const rk = g('#jraRaceKey') || (_closing && _closing.panelRk) || getActiveRaceKey() || '';
    const payload = {};
    if (rk) payload.raceKey = rk;
    if (venue) payload.venue = venue;
    if (ymd) payload.raceDy = ymd;
    if (race) payload.raceNb = race;
    if (!rk && !(venue && race)) { out.innerHTML = '<p class="hint" style="color:var(--red)">경마장+경주(또는 raceKey)를 입력하세요.</p>'; return; }
    out.innerHTML = '<p class="hint">🏇 netkeiba 馬柱에서 출주표·전5주를 가져오는 중…</p>';
    let d; try { d = await (await fetch('/api/jra/starters', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })).json(); }
    catch (e) { out.innerHTML = `<p class="hint" style="color:var(--red)">실패: ${esc(e.message)}</p>`; return; }
    if (d.error) { out.innerHTML = `<p class="hint" style="color:var(--red)">${esc(d.error)}${d.scheduled ? ' · 오늘 개최 race_id: ' + d.scheduled.slice(0, 12).map(esc).join(', ') : ''}</p>`; return; }
    out.innerHTML = renderKeibaStarters(d);   // 동일 구조 → 재사용
    if (d.linkedRaceKey) { try { refreshCurrentRace(); } catch (_) { /* */ } }
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
    // [복기 통합] 결과가 있으면 적중/미적중 모두 복기 리포트 자동 표시(재조회 시)
    try { if (d.result) showFailureReport(rk, '#jpFailReport'); } catch (_) { /* */ }
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  // [추천 이력] 추천이 시간에 따라 어떻게 바뀌었는지(6+9→3+7 등) 누적 표시. 덮어쓰기 방지 이력.
  function _recHistoryBlock(d) {
    const hist = (d && d.recommendation_history) || [];
    if (hist.length < 2) return '';   // 변경이 있었을 때만(1건이면 변화 없음)
    const rows = hist.map((h, i) => {
      const mb = h.minutes_before != null ? ` <span class="hint">(T-${h.minutes_before}분)</span>` : '';
      const combos = [h.quinella_main && ('복승 ' + h.quinella_main), h.trifecta_main && ('삼복승 ' + h.trifecta_main)].filter(Boolean).join(' · ');
      const kh = (h.keyHorses || []).join('·');
      const isLast = i === hist.length - 1;
      const sig = (h.top_signals || [])[0];
      return `<div style="margin:3px 0;padding:4px 6px;border-left:2px solid ${isLast ? '#ffd24f' : '#5a6172'};background:${isLast ? 'rgba(255,210,79,.08)' : 'transparent'}">
        <b>${esc(h.time || '')}</b>${mb} ${isLast ? '<span class="chip" style="border-color:#ffd24f;color:#ffd24f">최종</span>' : ''}
        <div style="margin-top:1px"><b style="color:#ffd24f">${esc(combos || '-')}</b>${kh ? ` <span class="hint">유력 ${esc(kh)}</span>` : ''}</div>
        ${sig ? `<div class="hint" style="font-size:11px">↳ ${esc(sig)}</div>` : ''}</div>`;
    }).join('');
    return `<div style="margin:8px 0;padding:8px 10px;border:1px solid #ffd24f55;border-radius:8px;background:rgba(255,210,79,.05)">
      <div class="matrix-title" style="font-size:13px;color:#ffd24f">🕓 추천 변경 이력 <span class="hint" style="font-weight:400">(${hist.length}회 · 덮어쓰지 않고 누적)</span></div>
      ${rows}</div>`;
  }

  // [복기 시각화] 경기 전 예측 vs 경기 후 실제 대조 블록(결과 입력된 경주만).
  //  삭제 없이 순수 파생 — d.elimination(예측)·d.result(실제)만 소비.
  function _reviewCompareBlock(d) {
    const res = d.result || {};
    if (res['1st'] == null) return '';   // 결과 없으면 대조 생략
    const elim = d.elimination || {};
    const cand = (elim.candidates || []).map(Number);
    const elimNo = (elim.eliminated || []).map(Number);
    const placed = ['1st', '2nd', '3rd'].map((k) => res[k]).filter((v) => v != null).map(Number);
    const placedSet = new Set(placed);
    const candCmp = cand.length ? cand.map((n) => {
      const inHit = placedSet.has(n);
      return `<span class="chip" style="border-color:${inHit ? '#38d39f' : '#5a6172'};color:${inHit ? '#38d39f' : '#8a94a6'}">${n}번 ${inHit ? '✅입상' : '✗'}</span>`;
    }).join(' ') : '<span class="hint">예측 유력마 없음</span>';
    const placeCmp = placed.map((n, i) => {
      const label = ['1착', '2착', '3착'][i];
      let tag, col;
      if (cand.includes(n)) { tag = '유력마 예측 ✅'; col = '#38d39f'; }
      else if (elimNo.includes(n)) { tag = '제거마였음 ⚠️'; col = '#f87171'; }
      else { tag = '미분류(놓침)'; col = '#ffb020'; }
      return `<div style="margin:2px 0"><b>${label} ${n}번</b> <span class="chip" style="border-color:${col};color:${col}">${tag}</span></div>`;
    }).join('');
    const hitCand = cand.filter((n) => placedSet.has(n)).length;
    const hitRate = cand.length ? Math.round((hitCand / cand.length) * 100) : 0;
    const missed = placed.filter((n) => !cand.includes(n) && !elimNo.includes(n));
    const elimFail = placed.filter((n) => elimNo.includes(n));
    const verdict = elimFail.length ? `<span style="color:#f87171">제거마 ${elimFail.join('·')}번 입상 — 제거 판정 실패</span>`
      : missed.length ? `<span style="color:#ffb020">${missed.join('·')}번을 사전에 못 짚음</span>`
        : `<span style="color:#38d39f">입상마 전부 예측 범위 안</span>`;
    return `<div style="margin:8px 0;padding:9px 11px;border:1px solid var(--border);border-radius:8px;background:linear-gradient(90deg,rgba(78,161,255,.1),rgba(56,189,248,.1))">
      <div class="matrix-title" style="font-size:13px">🔍 예측 vs 실제 <span class="hint" style="font-weight:400">복기 핵심 대조</span></div>
      <div style="display:flex;gap:14px;flex-wrap:wrap">
        <div style="flex:1;min-width:200px"><div class="hint" style="margin-bottom:2px">경기 전 유력마 적중</div>${candCmp}
          <div class="hint" style="margin-top:4px">유력마 <b style="color:${hitRate >= 50 ? '#38d39f' : '#ffb020'}">${hitCand}/${cand.length}두 입상 (${hitRate}%)</b></div></div>
        <div style="flex:1;min-width:200px"><div class="hint" style="margin-bottom:2px">실제 입상마 → 예측 분류</div>${placeCmp}</div>
      </div>
      <div style="margin-top:6px;font-size:13px;font-weight:700">📌 총평: ${verdict}</div></div>`;
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
    // [3번] 경주 전체 이상감지 시계열(anomaly_history) — 결과기록 탭에서 이전 경주 이상감지 확인
    const ah = d.anomaly_history || [];
    const ahHtml = `<div class="matrix-title" style="font-size:13px;margin-top:8px">🚨 이상감지 내역 <span class="hint" style="font-weight:400">(경주 전체 · ${ah.length}건)</span></div>`
      + (ah.length ? `<div style="max-height:220px;overflow:auto">` + ah.slice(0, 60).map((e) => {
          const col = e.severity === '🔴' ? '#f87171' : '#fbbf24';
          const label = e.combo ? `${esc(String(e.combo))}${e.drop != null ? ' ' + e.drop + '%' : ''}` : esc(e.text || '');
          return `<div style="margin:1px 0;font-size:12px"><span class="hint">${esc(e.time || '')}</span>${e.minutes_before != null ? ` <span style="color:#64748b">${e.minutes_before}분전</span>` : ''} <span style="color:${col};font-weight:700">${esc(e.severity || '')} ${label}</span></div>`;
        }).join('') + `</div>`
        : '<div class="hint">누적 이상감지 없음</div>');
    // (기존) 추천 시점 신호(signals_detected) — 근거·이유 포함, 함께 표시(보존)
    const sigHtml = ahHtml + `<div class="matrix-title" style="font-size:12px;margin-top:8px;color:#94a3b8">🔎 추천 시점 신호</div>`
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
    const resultExists = !!(d.result && d.hit);
    const compareBlock = _reviewCompareBlock(d);
    // 경기 전 분석 패널(예측): 유력마·이상감지·추천조합
    const preBlock = `<div style="flex:1;min-width:300px;border:1px solid #4ea1ff55;border-radius:8px;padding:10px;background:rgba(78,161,255,.05)">
      <div class="matrix-title" style="color:#4ea1ff">🔮 경기 전 분석 <span class="hint" style="font-weight:400">(배당·전적 기반 예측)</span></div>
      ${keyBlock}${sigHtml}${frHtml}${_recHistoryBlock(d)}</div>`;
    // 경기 후 분석 패널(실제·복기): #jpReport는 항상 존재해야 함(saveJapanResult가 참조)
    const postInner = resultExists ? reportHtml : '<div class="hint" style="padding:24px 8px;text-align:center;line-height:1.7">아래 <b>실제 결과</b>를 입력하면<br>경기 후 복기가 이 칸에 표시됩니다.</div>';
    const postBlock = `<div style="flex:1;min-width:300px;border:1px solid #38d39f55;border-radius:8px;padding:10px;background:rgba(56,189,248,.05)">
      <div class="matrix-title" style="color:#38d39f">🏁 경기 후 분석 <span class="hint" style="font-weight:400">(실제 결과·복기)</span></div>
      <div id="jpReport">${postInner}</div></div>`;
    return `<div style="border:1px solid var(--border);border-radius:8px;padding:12px">
      <div class="matrix-title">${esc(d.race || rk)} <span class="hint" style="font-weight:400">${esc(d.date || '')} · 분석 ${esc(d.analyzed_at || '')}</span></div>
      ${compareBlock}
      <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:8px;align-items:stretch">${preBlock}${postBlock}</div>
      ${formHtml}</div>`;
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
      ${rep.pairing_miss ? `<div style="margin-top:4px;padding:6px 8px;border-left:3px solid #a855f7;background:rgba(168,85,247,.1);border-radius:6px;color:#d8b4fe">
        🎯 <b>아쉬운 복승조합 엇갈림</b> — 유력마·삼복승 판단은 정확했으나 <b>복승 상대 페어링만 어긋남</b> (실제 복승 ${(rep.pairing_miss.top2 || []).join('+')})<br>
        <span class="hint">유력마 ${(rep.pairing_miss.keyHorses || []).join('·')}번 중 실제 1·2착은 ${(rep.pairing_miss.top2 || []).join('·')}번인데 복승 메인은 다른 조합이었습니다. ${rep.pairing_miss.wouldBackingCover ? '→ ✅ <b>역배열 실질유력마 받치기 복승</b>이 있었다면 커버됐을 케이스(학습 반영).' : '→ 유력마 간 받치기 복승 강화 대상.'}</span></div>` : ''}
      ${renderHitBasis(rep.hit_basis)}
      <div class="matrix-title" style="font-size:12px;margin-top:8px">이상감지 분석</div>${anomalyLines}
      ${formLine}${pnlHtml}
      <div id="jpFailReport"></div></div>`;
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
    // [복기 통합] 적중/미적중 모두 복기 리포트(정답말 역추적) 자동 표시
    try { showFailureReport(rk, '#jpFailReport'); } catch (_) { /* */ }
    // [4번] 통계 자동 업데이트(적중률·이상감지 패턴·손익)
    try { loadLearningStats(); } catch (_) { /* */ }
    try { if (typeof loadHistoryList === 'function') loadHistoryList(); } catch (_) { /* */ }
    try { renderStats(); } catch (_) { /* */ }
    try { loadJapanReviewList(); } catch (_) { /* */ }
    // [복기 시각화] 결과 저장 후 전체 재조회 → 경기전/경기후 2단 + 예측vs실제 대조 블록 갱신
    try { if (file) openJapanReview(file, rk); } catch (_) { /* */ }
  }

  function initJapanReview() {
    const rb = document.querySelector('#jpReviewRefresh'); if (rb) rb.addEventListener('click', loadJapanReviewList);
    const ad = document.querySelector('#jpReviewAllDates'); if (ad) ad.addEventListener('change', loadJapanReviewList);
    const ar = document.querySelector('#oddsArchiveRefresh'); if (ar) ar.addEventListener('click', loadOddsArchiveList);
    const ac = document.querySelector('#oddsArchiveCompress'); if (ac) ac.addEventListener('click', compressOddsArchive);
  }

  // [영구보존·3번] 배당 히스토리 보관함 — 저장된 모든 경주 배당 아카이브 목록·복기.
  async function loadOddsArchiveList() {
    const box = document.querySelector('#oddsArchiveList');
    const msg = document.querySelector('#oddsArchiveMsg');
    if (box) box.innerHTML = '<p class="hint">불러오는 중…</p>';
    let d; try { d = await (await fetch('/api/odds/archive/list')).json(); }
    catch (_) { if (box) box.innerHTML = '<p class="hint">불러오기 실패</p>'; return; }
    const races = (d && d.races) || [];
    if (msg) msg.textContent = `총 ${races.length}개 경주 보존됨`;
    if (!races.length) { if (box) box.innerHTML = '<p class="hint">보관된 경주가 없습니다.</p>'; return; }
    const rows = races.map((r) => {
      const res = r.result ? ` · 결과 ${esc([r.result['1st'], r.result['2nd'], r.result['3rd']].filter((x) => x != null).join('-'))}` : '';
      const comp = r.compressed ? ' 🗜' : '';
      return `<div class="cfg-row" style="justify-content:space-between;padding:5px 8px;border-bottom:1px solid rgba(255,255,255,.06)">
        <span><b>${esc(r.raceKey || r.race || '')}</b>${comp} <span class="hint">${esc(r.date || '')} · ${r.count}스냅샷${res}</span></span>
        <button class="btn btn-small" onclick="window._openOddsArchive('${encodeURIComponent(r.raceKey || '')}')">복기 보기</button>
      </div>`;
    }).join('');
    if (box) box.innerHTML = rows;
  }

  async function openOddsArchive(rk) {
    try { rk = decodeURIComponent(rk); } catch (_) { /* 이미 디코드됨 */ }
    const box = document.querySelector('#oddsArchiveDetail');
    if (box) box.innerHTML = '<p class="hint">불러오는 중…</p>';
    let d; try { d = await (await fetch('/api/odds/archive/get?raceKey=' + encodeURIComponent(rk))).json(); }
    catch (_) { if (box) box.innerHTML = '<p class="hint">불러오기 실패</p>'; return; }
    if (!d || !d.ok) { if (box) box.innerHTML = '<p class="hint">' + esc((d && d.error) || '없음') + '</p>'; return; }
    const snaps = d.snapshots || [];
    // 배당이 존재하는 상위 조합을 열로(마지막 스냅샷 최저배당 6개 기준)
    const last = [...snaps].reverse().find((s) => s.quinella && Object.keys(s.quinella).length) || {};
    const cols = Object.entries(last.quinella || {}).sort((a, b) => a[1] - b[1]).slice(0, 6).map((e) => e[0]);
    const head = ['시각', '마감', ...cols, '이상감지'];
    const trows = snaps.map((s) => {
      if (s.boundary) return `<tr><td colspan="${head.length}" style="color:#fbbf24;font-size:11px">— 세션 경계(${esc(s.reason || '')}) —</td></tr>`;
      const mb = s.after_close ? '마감후' : (s.minutes_before != null ? s.minutes_before + '분' : '-');
      const cells = cols.map((c) => `<td style="text-align:right">${(s.quinella && s.quinella[c] != null) ? s.quinella[c] : '-'}</td>`).join('');
      const an = (s.anomalies || []).length ? `<td class="hint" style="font-size:11px">${esc((s.anomalies || []).slice(0, 2).join(' · '))}</td>` : '<td></td>';
      return `<tr><td>${esc(s.time || '')}</td><td style="text-align:center">${esc(mb)}</td>${cells}${an}</tr>`;
    }).join('');
    const resTxt = d.result ? ` · 결과 ${esc([d.result['1st'], d.result['2nd'], d.result['3rd']].filter((x) => x != null).join('-'))}` : '';
    if (box) box.innerHTML = `<div class="matrix-title" style="font-size:13px">📚 ${esc(d.raceKey || '')} <span class="hint" style="font-weight:400">${d.count}스냅샷${resTxt}</span></div>
      <div style="overflow-x:auto"><table class="odds-table" style="font-size:12px"><thead><tr>${head.map((h) => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${trows}</tbody></table></div>`;
  }
  window._openOddsArchive = openOddsArchive;

  async function compressOddsArchive() {
    const msg = document.querySelector('#oddsArchiveMsg');
    if (msg) msg.textContent = '압축 중…';
    try {
      const d = await (await fetch('/api/odds/archive/compress', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ days: 7 }) })).json();
      if (msg) msg.textContent = `7일+ ${(d && d.compressed) || 0}건 압축 보관 완료`;
    } catch (_) { if (msg) msg.textContent = '압축 실패'; }
    loadOddsArchiveList();
  }

  // ---------- 부트 ----------
  async function boot() {
    initTabs(); initCondBar(); initKorea(); initJapanRace(); initOdds(); initKoreaHistory();
    initAutoStatusBar();   // [v2.0.0] 자동수집 상태바
    initResultAutoWatch(); // [스펙2·3] 결과 자동수집 실패 배너 + 성공 시 결과탭 자동갱신
    initClosingWatch();    // [보완] 이상감지 누적 피드 + 마감 전 단계 알림
    initRaceRefresh();     // [경주 자동 업데이트] 상단 새로고침 바 + 30초 자동 감지
    initMultiRace();       // [다중 경주 동시 배당판] 전체 경주 탭 버튼 바인딩
    initPopout();          // [별도 창] 분석기 팝업 창 열기 + 위치 기억
    initAnalysisLog();     // [분석 로그] 완전 기록 섹션
    initJapanReview();     // [일본경마 복기] 분석 내역 목록 + 결과 입력 + 자동 판정 리포트
    initKeirinCard();      // [경륜 출마표] oddspark 선수 전적 자동 수집·분석
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
