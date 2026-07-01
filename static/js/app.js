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
    oddsTrack: { betType: '복승', raceKey: null, snaps: 0, nos: new Set(), firstOdds: {} },
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
    $('#snap1Btn').addEventListener('click', () => captureSnapshot(1));
    $('#snap2Btn').addEventListener('click', () => captureSnapshot(2));
    $('#oddsDetectBtn').addEventListener('click', () => runOddsDetect());
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
      cap.rect = null; drawCap(); cap.autoAnalyze = false; showCropConfirm(false); return;
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

  /** 자동 라운드: 누적 스냅샷 0이면 1차, 아니면 2차 */
  function autoRound() { return state.oddsTrack.snaps >= 1 ? 2 : 1; }
  async function runCropAnalysis() { await captureSnapshot(autoRound()); }

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
    } catch (e) { hideLoading(); toast('3종 분석 실패: ' + e.message); }
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

  /** 배당 스냅샷 저장 코어 — Vision/빠른입력 공통. round1=새 추적+1차 캐시, round2=정지+즉시 비교.
   *  Vision API를 호출하지 않으므로(이미 받은 odds 사용) ~0.1초. quiet=true면 결과 렌더 시 오버레이 생략. */
  async function applyOddsSnapshot(round, odds, quiet) {
    const raceKey = round === 1 ? selectedRaceKey() : (state.oddsTrack.raceKey || selectedRaceKey());
    if (round === 1) {
      await Analysis.oddsClear(raceKey);       // 1차 = 새 추적 시작
      state.oddsTrack = { betType: state.oddsTrack.betType, raceKey, snaps: 0, nos: new Set(), firstOdds: {} };
    }
    const r = await Analysis.oddsSnapshot(raceKey, odds);
    state.oddsTrack.snaps = r.snaps;
    Object.keys(odds).forEach((n) => state.oddsTrack.nos.add(+n));
    if (round === 1) state.oddsTrack.firstOdds = { ...odds };  // [1번] 1차 결과 캐시

    $('#oddsDetectBtn').disabled = state.oddsTrack.snaps < 2;
    const got = Object.keys(odds).length;
    $('#oddsTrackHint').textContent =
      `${round}차 저장됨 (마번 ${got}두 · 누적 스냅샷 ${state.oddsTrack.snaps}회) — ` +
      (state.oddsTrack.snaps < 2 ? '2차 캡처/빠른입력을 진행하세요.' : '자동 비교 완료(또는 [이상감지 분석]).');

    if (round === 1) { startSnapTimer(); notify('📸 1차 저장 — 8분30초 후 2차 알림', true); }
    else { stopSnapTimer(); await runOddsDetect(true); }  // 2차 = 즉시 비교(변동폭만, API 재호출 없음)
  }

  async function captureSnapshot(round) {
    if (!cap.canvas) { toast('먼저 화면을 캡처하거나 이미지를 올리세요.'); return; }
    const block = capturedBlock();   // 프레임을 먼저 확보(이후 화면이 바뀌어도 안전)
    const hasFirst = state.oddsTrack.firstOdds && Object.keys(state.oddsTrack.firstOdds).length;
    // [3번] 2차는 즉시 1차 결과 + "분석 중"을 표시하고 Vision은 백그라운드(비차단)로 처리
    const bg = round === 2 && hasFirst;
    if (bg) { renderSecondPending(); notify('📸 2차 캡처 — 즉시 1차 표시 · 백그라운드 판독 중', true); }
    else { showLoading(`${round}차 배당 판독 중... (Vision)`); }
    try {
      const rep = await Analysis.analyzeOdds(block);
      const odds = perHorseOdds(rep);
      if (!Object.keys(odds).length) {
        if (!bg) hideLoading();
        toast('배당 수치를 못 읽었습니다. 영역을 다시 선택하거나 [빠른입력]을 쓰세요.'); return;
      }
      if (!bg) $('#loadingText').textContent = `${round}차 배당 저장 중...`;
      await applyOddsSnapshot(round, odds, bg);   // 완료 시 화면 자동 업데이트
      if (bg) notify('✅ 2차 판독 완료 — 변동폭 자동 갱신', true);
    } catch (e) {
      toast(`${round}차 캡처 실패: ` + e.message);
    } finally {
      if (!bg) hideLoading();
    }
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

  /** [2번] 빠른입력 제출 — Vision 없이 즉시 스냅샷 저장 + (2차면) 자동 비교 */
  async function submitQuickOdds() {
    const odds = parseQuickOdds($('#quickOddsInput').value);
    const n = Object.keys(odds).length;
    if (!n) { toast('마번·배당을 한 줄에 하나씩 입력하세요. 예: 4 2.4'); return; }
    const round = autoRound();
    try {
      await applyOddsSnapshot(round, odds, true);   // quiet — 오버레이 없이 즉시
      notify(`⌨️ 빠른입력 ${round}차 저장 (${n}두)`, true);
      if (round === 2) $('#quickOddsInput').value = '';
    } catch (e) { toast('빠른입력 실패: ' + e.message); }
  }

  /** [3번] 2차 백그라운드 판독 중 즉시 표시할 1차 기준 화면 */
  function renderSecondPending() {
    const el = $('#oddsTrackReport'); el.innerHTML = '';
    const fo = state.oddsTrack.firstOdds || {};
    const rows = Object.keys(fo).map(Number).sort((a, b) => a - b)
      .map((no) => `<tr><td>${no}</td><td>${fo[no]}</td></tr>`).join('');
    add(el, 'panel-card', `<h3>📡 2차 Vision 판독 중… <span class="hint" style="font-weight:400">완료 시 드롭·괴리·신호 자동 표시</span></h3>
      <table class="data-table"><thead><tr><th>마번</th><th>1차 배당(기준)</th></tr></thead><tbody>${rows}</tbody></table>
      <p class="hint" style="margin-top:8px">⏳ 화면은 즉시 1차 기준을 표시합니다. 백그라운드 판독이 끝나면 변동폭이 채워집니다.</p>`);
  }

  // ---------- [3단계] 1차/2차 자동 타이머 ----------
  const SNAP_GAP_SEC = 510; // 8분30초 (10분전 1차 → 1분30초전 2차)

  function startSnapTimer() {
    stopSnapTimer();
    const t = state.oddsTrack;
    t.deadline = Date.now() + SNAP_GAP_SEC * 1000;
    t.timerInt = setInterval(updateSnapTimer, 1000);
    t.timerTO = setTimeout(onSnapDue, SNAP_GAP_SEC * 1000);
    updateSnapTimer();
  }
  function updateSnapTimer() {
    const el = $('#snapTimer'); if (!el) return;
    const left = Math.max(0, Math.round((state.oddsTrack.deadline - Date.now()) / 1000));
    const m = Math.floor(left / 60), s = left % 60;
    el.textContent = left > 0 ? `⏱ 2차 캡처까지 ${m}:${String(s).padStart(2, '0')}` : '⏰ 지금 2차 캡처하세요! (Alt+C)';
    el.style.color = left <= 60 ? 'var(--accent-2)' : '';
  }
  function onSnapDue() { beep(); notify('⏰ 2차 캡처 하세요! (Alt+C)', true); updateSnapTimer(); }
  function stopSnapTimer() {
    const t = state.oddsTrack;
    clearInterval(t.timerInt); clearTimeout(t.timerTO);
    t.timerInt = t.timerTO = null; t.deadline = 0;
    const el = $('#snapTimer'); if (el) el.textContent = '';
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

  async function runOddsDetect(quiet) {
    const raceKey = state.oddsTrack.raceKey || selectedRaceKey();
    try {
      if (!quiet) showLoading('이상감지 계산 중...');   // compute는 ms 단위(서버 순수 연산) — quiet면 오버레이 생략
      const c = await Analysis.oddsCompute(raceKey, bmedHorsesFor(raceKey));
      if (!quiet) hideLoading();
      renderOddsDetect(c);
    } catch (e) { if (!quiet) hideLoading(); toast('이상감지 실패: ' + e.message); }
  }

  /** 신호 점수 → 신호등 색. 높을수록 강한 이상신호(매수). */
  function sigColor(s) { return s >= 75 ? '🔴' : s >= 60 ? '🟠' : s >= 45 ? '🟡' : '🟢'; }

  function renderOddsDetect(c) {
    const el = $('#oddsTrackReport'); el.innerHTML = '';
    if (!c.snapCount) { add(el, 'panel-card', '<p class="hint">저장된 배당 스냅샷이 없습니다. 1차/2차 캡처를 먼저 하세요.</p>'); return; }

    const rows = c.horses.slice().sort((a, b) => b.signalScore - a.signalScore).map((h) => {
      const dpct = h.firstOdds != null ? (h.drop * 100).toFixed(0) + '%' : '-';
      const epct = h.edge != null ? (h.edge >= 0 ? '+' : '') + (h.edge * 100).toFixed(1) + '%p' : '-';
      const e = h.edge || 0;
      return `<tr>
        <td>${h.no}</td><td>${esc(h.name || '')}</td>
        <td>${h.lastOdds != null ? h.lastOdds : '-'}</td>
        <td class="${h.drop > 0.02 ? 'pos' : h.drop < -0.02 ? 'neg' : ''}">${dpct}</td>
        <td class="${e > 0.02 ? 'pos' : e < -0.02 ? 'neg' : ''}">${epct}</td>
        <td><b>${sigColor(h.signalScore)} ${h.signalScore}</b></td>
        <td>${(h.tags || []).map((t) => `<span class="odds-tag">${esc(t)}</span>`).join(' ')}</td>
      </tr>`;
    }).join('');

    add(el, 'panel-card', `<h3>⚡ 배당 이상감지 결과 (스냅샷 ${c.snapCount}회)</h3>
      <table class="data-table">
        <thead><tr><th>마번</th><th>마명</th><th>배당</th><th>드롭</th><th>괴리</th><th>신호</th><th>판정</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <p class="hint" style="margin-top:8px">🔴≥75 · 🟠60+ · 🟡45+ · 🟢&lt;45 │ 드롭=배당 짧아진 비율(자금유입) · 괴리=BMED 기대확률−시장 내재확률 · 신호 50=중립</p>`);

    const bt = state.oddsTrack.betType;
    const bets = (c.bets || []).map((b) =>
      `<div class="bet-line"><span><span class="bet-type">${esc(b.type)}</span> ${(b.combo || []).join('-')}</span>
       <span>신호 ${b.confidence} · ${esc(b.note || '')}</span></div>`).join('') || '<p class="hint">신호가 충분하지 않습니다.</p>';
    add(el, 'bet-box', `<h3>💰 이상감지 보정 추천 <span class="hint" style="font-weight:400">(선택: ${esc(bt)})</span></h3>${bets}`);
  }

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
