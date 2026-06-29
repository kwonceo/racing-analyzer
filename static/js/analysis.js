/* ===== analysis.js — 백엔드 클라이언트 =====
 * 키는 서버(.env)가 보유. 브라우저는 동일 출처 /api/* 만 호출한다.
 * - extract*: PDF 페이지 PNG(base64) → Vision 추출
 * - analyzeRace / analyzeJapanRace: 분석 + 베팅(복승/삼복승) 추천
 */
(function (global) {
  async function post(path, body) {
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    let data;
    try { data = await res.json(); } catch (_) { data = { error: '응답 파싱 실패' }; }
    if (!res.ok || data.error) throw new Error(data.error || `서버 오류 ${res.status}`);
    return data;
  }

  function imgPayload(block) {
    const s = block.source;
    return { media_type: s.media_type, data: s.data };
  }

  async function health() {
    try { const r = await fetch('/api/health'); return await r.json(); }
    catch (_) { return { ok: false, has_key: false }; }
  }

  function detectPages(blocks) { return post('/api/detect', { images: blocks.map(imgPayload) }); }
  function extractJockeySheet(block) { return post('/api/extract/jockey', { image: imgPayload(block) }); }
  function extractRaceSheet(block) { return post('/api/extract/race', { image: imgPayload(block) }); }
  function extractTraining(block) { return post('/api/extract/training', { image: imgPayload(block) }); }
  function analyzeRace(raceData, jockeyStats) { return post('/api/analyze', { raceData, jockeyStats }); }
  function analyzeOdds(block) { return post('/api/analyze/odds', { image: imgPayload(block) }); }
  /** [3번] 복승/쌍승/삼복승 3종 동시 분석 */
  function analyzeOddsTriple(blocks) {
    const b = {};
    if (blocks.quinella) b.quinella = imgPayload(blocks.quinella);
    if (blocks.exacta) b.exacta = imgPayload(blocks.exacta);
    if (blocks.trio) b.trio = imgPayload(blocks.trio);
    return post('/api/analyze/odds/triple', b);
  }
  function extractResults(block) { return post('/api/extract/results', { image: imgPayload(block) }); }

  // ----- 배당 이상감지(시계열) -----
  function oddsSnapshot(raceKey, odds) { return post('/api/odds/snapshot', { raceKey, odds }); }
  function oddsCompute(raceKey, horses) { return post('/api/odds/compute', { raceKey, horses }); }
  function oddsClear(raceKey) { return post('/api/odds/clear', { raceKey }); }
  function oddsUndo(raceKey) { return post('/api/odds/undo', { raceKey }); }

  // ----- 전적 분석 엔진 (Phase 3) -----
  function scoreHorses(race, horses) { return post('/api/score', { race, horses }); }

  // ----- 통합 분석 엔진 (Phase 4) -----
  function analyzeCombined(payload) { return post('/api/analyze/combined', payload); }
  function analyzeJapanRace(odds, form) {
    return post('/api/analyze/japan', {
      oddsImage: odds ? imgPayload(odds) : null,
      formImage: form ? imgPayload(form) : null,
    });
  }

  /** File → image content block (일본경마 직접 업로드용) */
  function fileToImageBlock(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const [meta, b64] = reader.result.split(',');
        const mime = (meta.match(/data:(.*?);/) || [])[1] || 'image/jpeg';
        resolve({ type: 'image', source: { type: 'base64', media_type: mime, data: b64 } });
      };
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
  }

  global.Analysis = {
    health, detectPages,
    extractJockeySheet, extractRaceSheet, extractTraining, extractResults,
    analyzeRace, analyzeJapanRace, analyzeOdds, analyzeOddsTriple,
    oddsSnapshot, oddsCompute, oddsClear, oddsUndo,
    scoreHorses, analyzeCombined,
    fileToImageBlock,
  };
})(window);
