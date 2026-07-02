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
    koreaRaces: [],
    japanOdds: null,
    japanForm: null,
    lastReports: {},
    lastSheets: {},   // title -> {horses(추출 출전마), distance} — Phase 4 통합분석용
    lastCombined: {}, // title -> {bets, recOdds, hadAnomaly, budget} — Phase 5 결과기록용
    oddsTrack: { betType: '복승', raceKey: null, snaps: 0, nos: new Set(), firstOdds: {},
      series: {}, times: [], alerted: {}, auto: null, deadlineMs: 0,
      exSeries: {}, exTimes: [], dual: true, _pendingType: null }, // 다중 캡처 + 복승/쌍승 동시

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
        if (btn.dataset.tab === 'result') renderResultForm();
        if (btn.dataset.tab === 'jockeydb') renderJockeyDb();
        if (btn.dataset.tab === 'japan') refreshOddsRaceSelect();
        if (btn.dataset.tab === 'combined') refreshCombinedRaceSelect();
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

  // ---------- 한국경마 ----------
  function initKorea() {
    const zone = $('#koreaUploadZone'), input = $('#koreaPdfInput');
    $('#koreaBrowseBtn').addEventListener('click', (e) => { e.stopPropagation(); input.click(); });
    wireDropZone(zone, input, handleKoreaPdf, /application\/pdf/);
    $('#koreaScanBtn').addEventListener('click', runKoreaAuto);
  }

  async function handleKoreaPdf(file) {
    try {
      showLoading('PDF 로드 중...');
      const n = await PdfParser.load(file);
      hideLoading();
      $('#koreaUploadZone').classList.add('has-file');
      $('#koreaConfig').classList.remove('hidden');
      $('#koreaPdfInfo').textContent = `${n}페이지 감지됨. [자동 감지]를 누르면 전 페이지를 훑어 경주를 찾아냅니다.`;
    } catch (err) { hideLoading(); toast('PDF 로드 실패: ' + err.message); }
  }

  /** 전 페이지 자동 감지 → 경주 목록 생성 */
  async function runKoreaAuto() {
    const total = PdfParser.numPages();
    if (!total) { toast('먼저 PDF를 업로드하세요.'); return; }
    const CHUNK = 6;
    const detected = {}; // page -> {type,venue,raceNo,distance,layout}
    const progress = $('#koreaProgress');

    showLoading('페이지 스캔 중...');
    try {
      // 1) 감지 (썸네일 배치)
      let scanned = 0;
      for (let s = 1; s <= total; s += CHUNK) {
        const pages = [];
        for (let p = s; p < Math.min(s + CHUNK, total + 1); p++) pages.push(p);
        $('#loadingText').textContent = `페이지 스캔 ${pages[0]}–${pages[pages.length - 1]} / ${total}`;
        try {
          const blocks = [];
          for (const p of pages) blocks.push((await PdfParser.renderThumb(p)).block);
          const out = await Analysis.detectPages(blocks);
          (out.pages || []).forEach((r) => { const pg = pages[r.index]; if (pg) detected[pg] = r; });
        } catch (e) { console.warn('detect', pages, e); }
        scanned += pages.length;
        progress.textContent = `스캔 ${scanned}/${total}`;
      }

      // 2) 기수현황표 추출
      const jockeyPages = Object.keys(detected).map(Number).filter((p) => detected[p].type === 'jockey');
      for (const jp of jockeyPages) {
        $('#loadingText').textContent = `기수현황표 ${jp}p 판독 중...`;
        try {
          const { block } = await PdfParser.renderPage(jp);
          const o = await Analysis.extractJockeySheet(block);
          (o.jockeys || []).forEach(mergeJockey);
        } catch (e) { console.warn(`기수 ${jp}p 실패:`, e); }
      }
      rebuildJockeyStats();

      // 3) 경주를 venue+raceNo로 묶고, 요약 페이지 선택
      const groups = new Map();
      Object.keys(detected).map(Number).forEach((p) => {
        const r = detected[p];
        if (r.type !== 'race') return;
        const key = (r.venue || '') + '#' + r.raceNo;
        if (!groups.has(key)) groups.set(key, { venue: r.venue || '', raceNo: r.raceNo, distance: r.distance || '', pages: [] });
        groups.get(key).pages.push({ page: p, layout: r.layout });
      });
      state.koreaRaces = [...groups.values()].map((g) => {
        const sum = g.pages.find((x) => x.layout === 'summary');
        g.summaryPage = sum ? sum.page : Math.min(...g.pages.map((x) => x.page));
        return g;
      }).sort((a, b) => (a.venue || '').localeCompare(b.venue || '') || a.raceNo - b.raceNo);

      hideLoading();
      renderRaceChips();
      progress.textContent = `완료 — 경주 ${state.koreaRaces.length}개, 기수현황표 ${jockeyPages.length}p. 칩을 눌러 분석하세요.`;
      if (!state.koreaRaces.length) toast('경주를 찾지 못했습니다. PDF를 확인하세요.');
    } catch (err) { hideLoading(); toast('스캔 실패: ' + err.message); }
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

  /** 요약페이지 1장에서 메인표 + 조교표 두 밴드를 추출해 레이팅까지 병합 */
  async function extractRaceFull(page) {
    const { block } = await PdfParser.renderBand(page);
    const sheet = await Analysis.extractRaceSheet(block);
    try {
      const tb = await PdfParser.renderTrainingBand(page);
      const tr = await Analysis.extractTraining(tb.block);
      const map = {}; (tr.horses || []).forEach((t) => { map[t.horseNum] = t; });
      (sheet.horses || []).forEach((h) => {
        const t = map[h.horseNum];
        if (t) {
          if (t.rating) h.rating = t.rating;
          if (t.trainer) h.training = ((h.training || '') + ` 조교사 ${t.trainer} ${t.mark || ''}`).trim();
        }
      });
    } catch (e) { console.warn('조교 추출 실패:', e); }
    return sheet;
  }

  /** 칩 클릭: 요약 페이지 추출 → 컨텍스트 저장 → 분석 실행 */
  async function analyzeKoreaRace(idx, chipEl) {
    const race = state.koreaRaces[idx]; if (!race) return;
    $$('#koreaRaceList .race-chip').forEach((c) => c.classList.remove('active'));
    if (chipEl) chipEl.classList.add('active');
    state.activeKorea = idx;
    const title = raceLabel(race);
    try {
      showLoading(`${title} 추출 중... (p${race.summaryPage})`);
      const sheet = await extractRaceFull(race.summaryPage);
      if (!(sheet.horses || []).length) {
        hideLoading(); renderPageControl(idx);
        toast('출전마를 못 읽었습니다. ← → 로 페이지를 ±1 보정해보세요.'); return;
      }
      state.lastSheets[title] = { horses: sheet.horses, distance: race.distance };
      state.activeKoreaCtx = { idx, title, race, sheetHorses: sheet.horses };
      await runKoreaAnalysis();
    } catch (err) { hideLoading(); toast('분석 실패: ' + err.message); }
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

  /** 분석 실행(재분석 공용): 마체중·환경 반영 → 리포트 + 전적점수 + 마체중 패널 */
  async function runKoreaAnalysis() {
    const ctx = state.activeKoreaCtx; if (!ctx) return;
    const { idx, title, race, sheetHorses } = ctx;
    try {
      showLoading(`${title} BMED 분석 중...`);
      const horses = applyWeights(title, sheetHorses);
      const report = await Analysis.analyzeRace(
        { raceNo: race.raceNo, raceTitle: title, horses, condition: state.raceCondition, distance: race.distance }, state.jockeyStats);
      state.lastReports[title] = report;
      renderReport('#koreaReport', report, '한국', title);
      renderPageControl(idx);
      try { await renderFormScores(race, { horses: sheetHorses }); } catch (e) { console.warn('전적 점수 패널 실패:', e); }
      renderWeightPanel(title, sheetHorses);
      renderKoreaFooter(idx, title, race);   // [기능1·2] 경주 이동 + 결과입력 버튼
      refreshRaceChipStatus();               // [기능3] 진행상황 칩 갱신 (이 경주 → ✅)
      hideLoading();
    } catch (err) { hideLoading(); toast('분석 실패: ' + err.message); }
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
    if (np < 1 || np > PdfParser.numPages()) { toast('페이지 범위를 벗어났습니다.'); return; }
    race.summaryPage = np;
    analyzeKoreaRace(idx, null);
  }

  // ---------- 일본경마 (배당판 캡처 + 전적표 업로드 통합) ----------
  function initJapanRace() {
    // 배당판: 화면 캡처 / 파일 (state.jpOdds 만 갱신 — 전적표 유지)
    $('#jpOddsCapBtn').addEventListener('click', captureJpOdds);
    $('#jpOddsFileBtn').addEventListener('click', () => $('#jpOddsInput').click());
    $('#jpOddsInput').addEventListener('change', () => { if ($('#jpOddsInput').files[0]) handleJpOdds($('#jpOddsInput').files[0]); });
    jpDropOnly($('#jpOddsSlot'), handleJpOdds);
    // 전적표: 파일 업로드 (state.jpForm 만 갱신 — 배당판 유지)
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
    const el = $(kind === 'odds' ? '#jpOddsStatus' : '#jpFormStatus');
    el.textContent = ok ? '✅ 준비됨' : '⬜ 미설정';
    el.classList.toggle('ready', ok);
    if (kind === 'odds') $('#jpOddsCapBtn').textContent = ok ? '🖥️ 배당판 다시 캡처' : '🖥️ 배당판 캡처';
    else $('#jpFormFileBtn').textContent = ok ? '📁 전적표 교체' : '📁 전적표 업로드';
  }

  function updateJpReady() {
    const both = !!(state.jpOdds && state.jpForm);
    $('#jpAnalyzeBtn').disabled = !both;
    $('#jpHint').textContent = both ? '준비 완료 — [분석 시작]을 누르세요'
      : state.jpOdds ? '전적표를 업로드하세요'
        : state.jpForm ? '배당판을 캡처/업로드하세요'
          : '배당판과 전적표를 모두 준비하세요';
  }

  /** 배당판 화면 캡처 (전적표 state는 건드리지 않음) */
  async function captureJpOdds() {
    try {
      const full = await grabFrame();          // 기존 지속 스트림 재사용
      state.jpOdds = canvasToBlock(full);
      setJpPreview('#jpOddsPreview', state.jpOdds);
      setJpStatus('odds', true);
      updateJpReady();
      notify('✅ 배당판 캡처 완료 (전적표 유지)');
    } catch (e) { notify('배당판 캡처 실패: ' + e.message, false); }
  }
  async function handleJpOdds(file) {
    state.jpOdds = await Analysis.fileToImageBlock(file);
    setJpPreview('#jpOddsPreview', state.jpOdds);
    setJpStatus('odds', true); updateJpReady();
  }
  async function handleJpForm(file) {
    state.jpForm = await Analysis.fileToImageBlock(file);
    setJpPreview('#jpFormPreview', state.jpForm);
    setJpStatus('form', true); updateJpReady();
  }

  async function analyzeJp() {
    if (!(state.jpOdds && state.jpForm)) { toast('배당판과 전적표를 모두 업로드하세요'); return; }
    try {
      showLoading('일본경마 병합 분석 중...');
      const rep = await Analysis.analyzeJapanRace(state.jpOdds, state.jpForm);
      state.lastReports['일본경마'] = rep;
      renderJapanReport('#jpReport', rep);
      hideLoading();
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

  function initOdds() {
    $('#capBtn').addEventListener('click', captureScreen);
    $('#oddsFileBtn').addEventListener('click', () => $('#oddsFileInput').click());
    $('#oddsFileInput').addEventListener('change', () => { if ($('#oddsFileInput').files[0]) loadOddsFile($('#oddsFileInput').files[0]); });
    $('#oddsAnalyzeBtn').addEventListener('click', runOddsAnalysis);

    // 시계열 이상감지 (1차/2차 캡처)
    $$('#betTypeSeg .seg-btn').forEach((b) => b.addEventListener('click', () => {
      $$('#betTypeSeg .seg-btn').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      state.oddsTrack.betType = b.dataset.bt;
    }));
    // [다중캡처] 캡처 누적 / 복승+쌍승 동시 / 자동 인터벌 / 중지 / 초기화
    $('#snapAddBtn').addEventListener('click', () => { state.oddsTrack._pendingType = '복승'; captureNow(); });
    $('#dualCapBtn').addEventListener('click', dualCapture);   // [1번] 복승+쌍승 2단 캡처
    $$('.auto-int-btn').forEach((b) => b.addEventListener('click', () => startAutoCapture(+b.dataset.min)));
    $('#autoStopBtn').addEventListener('click', () => { stopAutoCapture(); notify('⏹ 자동 캡처 중지', true); });
    $('#oddsResetBtn').addEventListener('click', oddsReset);
    $('#deadlineInput').addEventListener('change', applyDeadline);
    const alt = $('#altExactaChk');
    if (alt) { alt.checked = state.oddsTrack.dual !== false; alt.addEventListener('change', () => { state.oddsTrack.dual = alt.checked; }); }
    $('#quickPairsApplyBtn').addEventListener('click', submitQuickPairs); // [4번] 쌍승 빠른입력
    { const b = $('#quickQuinApplyBtn'); if (b) b.addEventListener('click', submitQuickQuin); } // [1-1] 복승 매트릭스 빠른입력
    { const b = $('#loadTripleBtn'); if (b) b.addEventListener('click', loadTripleFromServer); } // [연동] 확장 3종 불러오기
    { const b = $('#analyzeTripleBtn'); if (b) b.addEventListener('click', () => { loadTripleFromServer(); analyzeTripleRules(); }); } // [1번] 이상감지
    { const c = $('#autoRefreshTriple'); if (c) c.addEventListener('change', () => toggleTripleAutoRefresh(c.checked)); } // [2번] 자동 갱신
    setStep(1);
    // [2번] 빠른입력 모드
    $('#quickOddsBtn').addEventListener('click', () => {
      const box = $('#quickOddsBox'); box.classList.toggle('hidden');
      if (!box.classList.contains('hidden')) $('#quickOddsInput').focus();
    });
    $('#quickOddsApplyBtn').addEventListener('click', submitQuickOdds);

    // [3번] 3종 동시 캡처/분석
    $$('.triple-cap-btn').forEach((b) => b.addEventListener('click', () => captureTriple(b.dataset.kind)));
    $('#tripleRunBtn').addEventListener('click', analyzeTriple);
    $('#cropYesBtn').addEventListener('click', () => { $('#capConfirm').classList.add('hidden'); runCropAnalysis(); });
    $('#cropNoBtn').addEventListener('click', () => {
      $('#capConfirm').classList.add('hidden'); cap.rect = null; drawCap();
      notify('영역을 마우스로 직접 드래그하세요', true);
    });
    const cv = $('#capCanvas');
    cv.addEventListener('mousedown', (e) => { $('#capConfirm').classList.add('hidden'); const p = capPos(e); cap.dragging = true; cap.sx = p.x; cap.sy = p.y; cap.rect = null; });
    cv.addEventListener('mousemove', (e) => {
      if (!cap.dragging) return;
      const p = capPos(e);
      cap.rect = { x: Math.min(cap.sx, p.x), y: Math.min(cap.sy, p.y), w: Math.abs(p.x - cap.sx), h: Math.abs(p.y - cap.sy) };
      drawCap();
    });
    window.addEventListener('mouseup', () => { cap.dragging = false; });

    // [1단계] Alt+C 단축키 캡처 (활성 탭에 따라 대상 결정)
    document.addEventListener('keydown', (e) => {
      if (e.altKey && !e.ctrlKey && !e.metaKey && (e.code === 'KeyC' || e.key === 'c' || e.key === 'C')) {
        e.preventDefault();
        const active = document.querySelector('.tab-btn.active')?.dataset.tab;
        if (active === 'jp') captureJpOdds(); // 일본경마: 배당판만 캡처(전적표 유지)
        else hotkeyCapture();                 // 그 외: 배당판 캡처 탭(시계열)
      }
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
  async function dualCapture() {
    if (state.oddsTrack.auto) { toast('자동 캡처 중에는 [중지] 후 사용하세요.'); return; }
    setStep(2);
    showLoading('복승 배당 판독 중... (Vision)');
    try { await captureAs('복승'); } finally { hideLoading(); }
    setStep(3);
    await countdownCapture('🔀 지금 [쌍승] 탭을 클릭하세요! 곧 자동으로 쌍승을 캡처합니다', 3, '쌍승');
    setStep(1);
    notify('✅ 복승+쌍승 동시 캡처 완료', true);
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
  async function loadTripleFromServer() {
    const el = $('#tripleMatrixReport'); if (!el) return;
    el.innerHTML = '<p class="hint">불러오는 중…</p>';
    let data;
    try { data = await (await fetch('/api/odds/triple/latest')).json(); }
    catch (e) { el.innerHTML = `<p class="hint" style="color:var(--red)">불러오기 실패: ${esc(e.message)}</p>`; return; }
    if (!data || !data.raceKey) {
      el.innerHTML = '<p class="hint">확장에서 수집된 3종 배당이 없습니다. Chrome 확장 [⚡ 전체 자동 수집]을 먼저 실행하세요.</p>';
      return;
    }
    renderTripleMatrices(data);
  }

  // ── [1번] 규칙기반 이상감지 분석 (급락·순위·역전·유력마·삼복승추천) ──────
  async function analyzeTripleRules() {
    const el = $('#tripleAnalyzeReport'); if (!el) return;
    if (!el.innerHTML) el.innerHTML = '<p class="hint">이상감지 분석 중…</p>';
    let a;
    try { a = await (await fetch('/api/odds/triple/analyze', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })).json(); }
    catch (e) { el.innerHTML = `<p class="hint" style="color:var(--red)">분석 실패: ${esc(e.message)}</p>`; return; }
    if (!a || a.error) { el.innerHTML = `<p class="hint">${esc((a && a.error) || '수집된 3종 배당이 없습니다.')}</p>`; return; }
    renderTripleAnalyze(a);
  }

  function renderTripleAnalyze(a) {
    const el = $('#tripleAnalyzeReport'); if (!el) return;
    const drops = (a.drops || []).slice(0, 8).map((d) =>
      `<span class="chip ${d.pct < 0 ? 'chip-red' : 'chip-yellow'}">${d.combo[0]}-${d.combo[1]} ${d.prev}→${d.cur} ${d.pct < 0 ? '▼' : '▲'}${Math.abs(d.pct)}%</span>`).join(' ');
    const flips = (a.reversals || []).filter((r) => r.flipped).slice(0, 6).map((r) =>
      `<span class="chip chip-red">🔴 ${r.favored[0]}→${r.favored[1]} (${r.favoredOdds}&lt;${r.otherOdds})</span>`).join(' ');
    const ranks = (a.rankChanges || []).slice(0, 6).map((r) =>
      `<span class="chip">${r.combo[0]}-${r.combo[1]} ${r.prevRank}위→${r.curRank}위 (${r.delta > 0 ? '▲' : '▼'}${Math.abs(r.delta)})</span>`).join(' ');
    const keyH = (a.keyHorses || []).map((h) => `<b style="color:#4ea1ff">${h}</b>`).join(' · ');
    const trios = (a.trioRecommend || []).map((r) =>
      `<div class="combo-card"><div class="cc-head"><span class="cc-type">${esc(r.label)} ${r.combo.join('+')}</span><span class="cc-odds">${r.expOdds != null ? r.expOdds + '배' : '미수집'}</span></div></div>`).join('');
    el.innerHTML = `
      <div class="matrix-title">🚨 이상감지 <span class="hint" style="font-weight:400">${esc(a.raceKey)} · ${a.hasPrev ? '직전 대비' : '첫 수집(변동 없음)'}</span></div>
      <div style="font-size:15px;font-weight:700;margin:6px 0;color:#ffd24f">${esc(a.summary || '')}</div>
      ${drops ? `<div style="margin:6px 0"><span class="hint">📉 급락/변동</span><br>${drops}</div>` : ''}
      ${flips ? `<div style="margin:6px 0"><span class="hint">🔀 쌍승 역전</span><br>${flips}</div>` : ''}
      ${ranks ? `<div style="margin:6px 0"><span class="hint">📊 순위 변동</span><br>${ranks}</div>` : ''}
      <div style="margin:6px 0"><span class="hint">⭐ 유력마</span> ${keyH || '—'}${a.anomalyHorse != null ? ` <span class="hint">/ 이상감지말</span> <b style="color:#ff5c5c">${a.anomalyHorse}</b>` : ''}</div>
      ${trios ? `<div class="matrix-title" style="font-size:13px">🎯 삼복승 추천</div><div class="combo-cards">${trios}</div>` : ''}`;
    drawTripleChart(a.chart);
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
      const tick = () => { loadTripleFromServer(); analyzeTripleRules(); };
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
    const nos = [...nosSet].filter((n) => n > 0).sort((a, b) => a - b);
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
    if (!horses.length) return;
    const res = await Analysis.scoreHorses(raceCtx, horses);
    renderFormScorePanel(res.horses || []);
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
            <label class="hint">1·2·3착(콤마)<br><input class="cfg-input res-place" placeholder="3,7,1" style="width:120px" /></label>
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

  // ---------- 부트 ----------
  async function boot() {
    initTabs(); initCondBar(); initKorea(); initJapanRace(); initOdds(); initCombined();
    checkServerHealth();
    try { await JockeyDB.load(); rebuildJockeyStats(); } catch (e) { console.warn('기수 DB 로드 실패:', e); }
  }
  document.addEventListener('DOMContentLoaded', boot);
})();
