/* =========================================================================
 * content.js — keiba.go.jp 배당판 추출 엔진
 * -------------------------------------------------------------------------
 * 역할
 *   1) 마번 / 말이름 추출
 *   2) 단승(単勝) 배당 추출  → 서버 snapshot 의 odds 맵
 *   3) 복승(馬連) 배당 매트릭스 추출
 *   4) raceKey(경주 식별자) 생성
 *   → 하나의 JSON payload 로 정리하여 background.js 로 전달(POST 는 background 담당)
 *
 * 설계 메모
 *   keiba.go.jp 의 배당 페이지 DOM 은 종류(単勝複勝 / 馬連 …)마다 다르고
 *   개편될 수 있으므로, 특정 클래스에 의존하지 않고 "표 안의 숫자 패턴"으로
 *   방어적으로 파싱한다. 필요 시 아래 SELECTOR 힌트만 조정하면 된다.
 * =======================================================================*/

(() => {
  'use strict';

  // ── 숫자/텍스트 유틸 ────────────────────────────────────────────────
  const txt = (el) => (el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : '');
  const toNum = (s) => {
    if (s == null) return null;
    const m = String(s).replace(/,/g, '').match(/\d+(?:\.\d+)?/);
    return m ? parseFloat(m[0]) : null;
  };
  const isHorseNo = (n) => Number.isInteger(n) && n >= 1 && n <= 20;
  // "1.2 - 1.5" 같은 복승(place) 범위 → {min,max}
  const parseRange = (s) => {
    const m = String(s).match(/(\d+(?:\.\d+)?)\s*[-–—ー~]\s*(\d+(?:\.\d+)?)/);
    if (m) return { min: parseFloat(m[1]), max: parseFloat(m[2]) };
    const one = toNum(s);
    return one == null ? null : { min: one, max: one };
  };

  // ── raceKey 생성: 날짜 + 회장(会場) + 경주번호 ───────────────────────
  //   1순위: URL 파라미터(k_raceDate/k_babaCode/k_raceNo) — 가장 안정적
  //   보강: 본문 헤더 "2026年7月1日（水） 大 井 第11競走" 파싱
  const BABA_CODE = { 19: '船橋', 20: '大井', 24: '名古屋', 27: '園田', 31: '高知' };
  const TRACKS = /(帯広|門別|盛岡|水沢|浦和|船橋|大井|川崎|金沢|笠松|名古屋|園田|姫路|高知|佐賀)/;

  function extractRaceKey() {
    const q = new URLSearchParams(location.search);
    let date = '', track = '', raceNo = '';

    const rd = (q.get('k_raceDate') || '').match(/(\d{4})\D(\d{1,2})\D(\d{1,2})/);
    if (rd) date = `${rd[1]}-${rd[2].padStart(2, '0')}-${rd[3].padStart(2, '0')}`;
    const rn = q.get('k_raceNo');
    if (rn) raceNo = `${parseInt(rn, 10)}R`;
    const bc = parseInt(q.get('k_babaCode'), 10);
    if (BABA_CODE[bc]) track = BABA_CODE[bc];

    // 본문 텍스트로 보강 (회장명은 "大 井"처럼 공백이 섞일 수 있음)
    const body = (document.body.innerText || '').replace(/\s+/g, ' ');
    if (!date) {
      const m = body.match(/(\d{4})年(\d{1,2})月(\d{1,2})日/);
      if (m) date = `${m[1]}-${m[2].padStart(2, '0')}-${m[3].padStart(2, '0')}`;
    }
    if (!raceNo) {
      const m = body.match(/第\s*(\d{1,2})\s*競走/) || body.match(/\b(\d{1,2})\s*R\b/);
      if (m) raceNo = `${parseInt(m[1], 10)}R`;
    }
    if (!track) {
      // 날짜 ~ "第N競走" 사이 한자에서 회장명 추출 (공백 제거 후 매칭)
      const seg = body.match(/日\s*[（(][^）)]*[）)]\s*([^第]{1,14}?)\s*第\s*\d{1,2}\s*競走/);
      const cand = seg ? seg[1].replace(/[\s　]/g, '') : '';
      const tm = (cand.match(TRACKS) || body.replace(/[\s　]/g, '').match(TRACKS));
      if (tm) track = tm[1];
    }
    return [date, track, raceNo].filter(Boolean).join(' ').trim();
  }

  // ── 1+2+ : 마번/말이름 + 단승 + 복승(place) 추출 (単勝・複勝 표) ──────
  //   실측 구조(class="odd_popular_table_02"):
  //     헤더: 枠 | 馬番 | 馬名 | 単勝オッズ | 複勝オッズ(3着払い) | [複勝上限] | 性齢 | …
  //     주의: 枠 와 馬番 이 별도 컬럼이고, 複勝 는 "4.3-" + "13.6" 두 셀로 나뉜다.
  //   → 위치 추측이 아니라 헤더 라벨로 컬럼 인덱스를 찾는다(방어적).
  function extractHorses() {
    const horses = {};
    const tables = [
      ...document.querySelectorAll('table.odd_popular_table_02'),
      ...document.querySelectorAll('table'),
    ];
    for (const table of tables) {
      const trs = [...table.querySelectorAll('tr')];
      if (trs.length < 2) continue;
      const head = [...trs[0].querySelectorAll('th,td')].map((c) => txt(c));
      const idxNo = head.findIndex((h) => /^馬番$/.test(h));
      const idxName = head.findIndex((h) => /馬名/.test(h));
      const idxWin = head.findIndex((h) => /単勝/.test(h));
      const idxPlace = head.findIndex((h) => /複勝/.test(h));
      if (idxNo === -1 || idxWin === -1) continue; // 단복 표가 아님

      for (const tr of trs.slice(1)) {
        const cells = [...tr.querySelectorAll('th,td')].map((c) => txt(c));
        const no = toNum(cells[idxNo]);
        if (!isHorseNo(no) || !/^\d{1,2}$/.test(cells[idxNo] || '')) continue;
        const name = idxName >= 0 ? cells[idxName] : '';
        // 단승: 순수 소수만 (取消/--- 등은 제외)
        const win = /^\d+(?:\.\d+)?$/.test(cells[idxWin] || '') ? toNum(cells[idxWin]) : null;
        // 복승(place): "4.3-" 셀 + 다음 셀 "13.6" (또는 한 셀 "4.3-5.1")
        let place = null;
        if (idxPlace >= 0) {
          const a = cells[idxPlace] || '';
          const b = cells[idxPlace + 1] || '';
          const min = toNum(a);
          const max = /^\d+(?:\.\d+)?$/.test(b) ? toNum(b) : (parseRange(a)?.max ?? min);
          if (min != null) place = { min, max: max ?? min };
        }
        horses[no] = { no, name, win, place };
      }
      if (Object.keys(horses).length) break; // 첫 유효 표에서 종료
    }
    return Object.values(horses).sort((a, b) => a.no - b.no);
  }

  // ── 2+ : 복승(馬連) 배당 매트릭스 추출 ───────────────────────────────
  //   실측 구조: 오즈는 class="odd_ranking_table" 의 행에 담긴다.
  //     헤더: 組合せ | オッズ | 人気   행: "3-8" | "3.8" | "1"
  //   (a) 이 랭킹 표를 우선 파싱하고, (b) 조합목록/삼각매트릭스는 폴백으로 스캔.
  function extractQuinella() {
    const pairs = {}; // "a-b"(a<b) -> odds

    const addPair = (a, b, odds) => {
      if (!isHorseNo(a) || !isHorseNo(b) || a === b || odds == null) return;
      const key = a < b ? `${a}-${b}` : `${b}-${a}`;
      if (pairs[key] == null) pairs[key] = odds;
    };

    // (a) 실제 형식: odd_ranking_table (組合せ/オッズ/人気)
    for (const t of document.querySelectorAll('table.odd_ranking_table')) {
      const trs = [...t.querySelectorAll('tr')];
      const head = [...(trs[0]?.querySelectorAll('th,td') || [])].map((c) => txt(c));
      const iC = head.findIndex((h) => /組合せ|組番|くみ/.test(h));
      const iO = head.findIndex((h) => /オッズ/.test(h));
      const comboIdx = iC >= 0 ? iC : 0;
      const oddsIdx = iO >= 0 ? iO : 1;
      for (const tr of trs.slice(1)) {
        const cells = [...tr.querySelectorAll('th,td')].map((c) => txt(c));
        const cm = (cells[comboIdx] || '').match(/^(\d{1,2})\s*[-–—ー]\s*(\d{1,2})$/); // 쌍(복승)만
        if (!cm) continue;
        addPair(parseInt(cm[1], 10), parseInt(cm[2], 10), toNum(cells[oddsIdx]));
      }
    }

    // (b) 폴백: 그 외 표에서 "n-m" 조합 + 인접 오즈 스캔
    if (!Object.keys(pairs).length) {
      for (const table of document.querySelectorAll('table')) {
        for (const tr of table.querySelectorAll('tr')) {
          const cells = [...tr.querySelectorAll('th,td')].map((c) => txt(c));
          for (let i = 0; i < cells.length; i++) {
            const cm = cells[i].match(/^(\d{1,2})\s*[-–—ー]\s*(\d{1,2})$/);
            if (!cm) continue;
            let odds = cells[i + 1] ? toNum(cells[i + 1]) : null;
            addPair(parseInt(cm[1], 10), parseInt(cm[2], 10), odds);
          }
        }
      }
    }

    // 평면 배열 + 중첩 매트릭스 두 형태로 반환
    const list = Object.entries(pairs).map(([k, odds]) => {
      const [a, b] = k.split('-').map(Number);
      return { a, b, odds };
    });
    const matrix = {};
    for (const { a, b, odds } of list) {
      (matrix[a] = matrix[a] || {})[b] = odds;
      (matrix[b] = matrix[b] || {})[a] = odds;
    }
    return { pairs: list, matrix };
  }

  // ═══════════════════════════════════════════════════════════════════
  //  사이트 자동 감지 + 범용 배당 매트릭스 파서
  //  ---------------------------------------------------------------------
  //  keiba.go.jp 외에 asyukk34.qwqwd25.net(사설 배당판, class=odds_table/
  //  odds_content) 등 사이트마다 DOM 이 달라, URL 로 사이트를 감지해 파서를
  //  분기한다. 미지의 사이트는 "표 안의 마번 축 + 숫자 셀" 패턴으로 범용 파싱.
  // ═══════════════════════════════════════════════════════════════════

  // [2번] 사이트 자동 감지
  function detectSite() {
    const h = location.host;
    if (/(^|\.)keiba\.go\.jp$/.test(h)) return 'keiba';
    if (/asyukk|qwqwd/i.test(h)) return 'asyukk';
    return 'generic';
  }

  const pureInt = (s) => (/^\d{1,2}$/.test((s || '').trim()) ? parseInt(s, 10) : null);

  // [1번][3번] 범용 매트릭스 표 파서
  //   - 헤더 행(정수 마번이 가장 많은 행)에서 "열 마번" 축을 구성 (cellIndex→마번)
  //   - 각 데이터 행의 첫 정수 셀 = "행 마번"
  //   - 배당 셀(opts.oddsClass 가 있으면 그 class, 없으면 소수점 숫자 셀)만 추출
  //   - matrix(열 마번 축 2개↑)면 조합쌍, 아니면 단일(단승) 리스트로 해석
  //   - "-"·빈칸·1.0 미만은 자동 무시
  function parseMatrixTable(table, opts = {}) {
    const rows = [...table.rows];
    if (rows.length < 2) return { pairs: [], singles: [] };

    // 헤더 행: 앞 3행 중 정수 마번이 가장 많은 행(2개 이상일 때만 헤더로 인정)
    let headerRow = null, best = 1;
    for (const r of rows.slice(0, 3)) {
      const c = [...r.cells].filter((td) => pureInt(td.textContent) != null).length;
      if (c > best) { best = c; headerRow = r; }
    }
    const colNoByIndex = {};
    if (headerRow) {
      for (const cell of headerRow.cells) {
        const n = pureInt(cell.textContent);
        if (n != null) colNoByIndex[cell.cellIndex] = n;
      }
    }
    const colCount = Object.keys(colNoByIndex).length;
    const isOdds = opts.oddsClass
      ? (td) => td.classList.contains(opts.oddsClass)
      : (td) => /^\d+\.\d+$/.test((td.textContent || '').trim()); // 소수점 있는 숫자 = 배당

    const pairs = [], singles = [];
    for (const r of rows) {
      if (r === headerRow) continue;
      // 행 마번 = 첫 정수 셀
      let rowNo = null;
      for (const cell of r.cells) { const n = pureInt(cell.textContent); if (n != null) { rowNo = n; break; } }
      const oddsCells = [...r.cells].filter(isOdds);
      if (!oddsCells.length) continue;

      if (colCount >= 2) {
        // ── 매트릭스: 행 마번 × 열 마번 → 조합 ──
        for (const oc of oddsCells) {
          const colNo = colNoByIndex[oc.cellIndex];
          const val = toNum(oc.textContent);
          if (rowNo != null && colNo != null && rowNo !== colNo && val != null && val >= 1.0) {
            pairs.push({ a: rowNo, b: colNo, odds: val });
          }
        }
      } else if (rowNo != null) {
        // ── 리스트: 행 마번 + 단승(첫 배당)·복승(다음 배당) ──
        const vals = oddsCells.map((o) => (o.textContent || '').trim());
        const win = toNum(vals[0]);
        if (win != null && win >= 1.0) {
          singles.push({ no: rowNo, win, place: vals[1] ? parseRange(vals.slice(1).join(' ')) : null });
        }
      }
    }
    return { pairs, singles };
  }

  // asyukk34/범용 사이트에서 배당 표들을 모아 {horses, quinella} 로 정리
  function extractByMatrix(oddsClass) {
    const tables = new Set();
    if (oddsClass) {
      for (const c of document.querySelectorAll('.' + oddsClass)) {
        const t = c.closest('table'); if (t) tables.add(t);
      }
    }
    // .odds_table 또는 (범용) 모든 표
    for (const t of document.querySelectorAll('table.odds_table, table')) tables.add(t);

    const pairsMap = {}; // "a-b"(a<b) -> odds (최소값 유지)
    const singleMap = {}; // no -> {no,win,place}
    for (const t of tables) {
      const { pairs, singles } = parseMatrixTable(t, oddsClass ? { oddsClass } : {});
      for (const p of pairs) {
        if (!isHorseNo(p.a) || !isHorseNo(p.b) || p.a === p.b) continue;
        const key = p.a < p.b ? `${p.a}-${p.b}` : `${p.b}-${p.a}`;
        if (pairsMap[key] == null || p.odds < pairsMap[key]) pairsMap[key] = p.odds;
      }
      for (const s of singles) if (isHorseNo(s.no) && singleMap[s.no] == null) singleMap[s.no] = s;
    }

    const list = Object.entries(pairsMap).map(([k, odds]) => {
      const [a, b] = k.split('-').map(Number); return { a, b, odds };
    });
    const matrix = {};
    for (const { a, b, odds } of list) {
      (matrix[a] = matrix[a] || {})[b] = odds;
      (matrix[b] = matrix[b] || {})[a] = odds;
    }
    const horses = Object.values(singleMap).sort((a, b) => a.no - b.no)
      .map((s) => ({ no: s.no, name: '', win: s.win, place: s.place || null }));
    return { horses, quinella: { pairs: list, matrix } };
  }

  // [3번] 추출 결과 검증: 마번 1~16 · 배당 ≥1.0 · 최소 3조합
  function validateExtraction(payload) {
    const warnings = [];
    const combos = (payload.quinella && payload.quinella.pairs) || [];
    const badNo = combos.filter((c) => !(c.a >= 1 && c.a <= 16 && c.b >= 1 && c.b <= 16));
    const badOdds = combos.filter((c) => !(c.odds >= 1.0));
    if (combos.length < 3) warnings.push(`복승 조합이 ${combos.length}개뿐 (최소 3개 필요)`);
    if (badNo.length) warnings.push(`마번 범위(1~16) 벗어난 조합 ${badNo.length}개`);
    if (badOdds.length) warnings.push(`배당 1.0 미만 조합 ${badOdds.length}개`);
    return { ok: warnings.length === 0, warnings, combos: combos.length, singles: (payload.horses || []).length };
  }

  // ── 전체 payload 조립 (사이트별 파서 분기) ──────────────────────────
  //   keiba: 기존 단복/랭킹표 파서 / asyukk·generic: 범용 매트릭스 파서
  function buildPayload(overrideRaceKey) {
    const site = detectSite();
    let horses, quinella;
    if (site === 'asyukk') ({ horses, quinella } = extractByMatrix('odds_content'));
    else if (site === 'generic') ({ horses, quinella } = extractByMatrix(null));
    else { horses = extractHorses(); quinella = extractQuinella(); }

    // asyukk/generic 에서 아무것도 못 찾으면 keiba식 폴백도 시도
    if (site !== 'keiba' && !quinella.pairs.length && !horses.length) {
      horses = extractHorses(); quinella = extractQuinella();
    }

    // 서버 필수 필드: odds = {마번: 단승배당}
    const odds = {};
    for (const h of horses) if (h.win != null) odds[String(h.no)] = h.win;

    const raceKey = (overrideRaceKey && overrideRaceKey.trim()) || extractRaceKey();

    return {
      raceKey,
      site, // 'keiba' | 'asyukk' | 'generic'
      odds, // ← 서버가 저장하는 단승 시계열
      // 확장 필드 (참고용)
      horses, // [{no,name,win,place}]
      quinella, // {pairs:[{a,b,odds}], matrix:{a:{b:odds}}}
      capturedAt: new Date().toISOString(),
      source: location.href,
    };
  }

  // ── [2번] 결과(성적) 페이지: 1~3착 자동 추출 ────────────────────────
  //   keiba 결과 페이지 = RaceMarkTable(레이스 성적표). URL에 result/RaceMarkTable
  //   포함 또는 "着順" 헤더가 있으면 결과 페이지로 판단.
  //   표 헤더: 着順 | 枠 | 馬番 | 馬名 | … → 착순의 마번/말이름 추출.
  function isResultPage() {
    if (/result|racemarktable/i.test(location.href)) return true;
    return [...document.querySelectorAll('table')].some((t) => {
      const h = [...(t.querySelector('tr')?.querySelectorAll('th,td') || [])].map((c) => txt(c));
      return h.includes('着順') && h.some((x) => /馬番/.test(x));
    });
  }

  function extractResults() {
    for (const table of document.querySelectorAll('table')) {
      const trs = [...table.querySelectorAll('tr')];
      if (trs.length < 2) continue;
      const head = [...trs[0].querySelectorAll('th,td')].map((c) => txt(c));
      const iRank = head.findIndex((h) => /着順/.test(h));
      const iNo = head.findIndex((h) => /^馬番$/.test(h));
      const iName = head.findIndex((h) => /馬名/.test(h));
      if (iRank === -1 || iNo === -1) continue;
      const results = [];
      for (const tr of trs.slice(1)) {
        const cells = [...tr.querySelectorAll('th,td')].map((c) => txt(c));
        const rank = toNum(cells[iRank]);
        const no = toNum(cells[iNo]);
        // 착순이 정수이고 마번이 유효할 때만 (失格/中止/除外 등은 착순 숫자 없음 → 제외)
        if (!Number.isInteger(rank) || rank < 1 || !isHorseNo(no) || !/^\d+$/.test(cells[iRank] || '')) continue;
        results.push({ rank, no, name: iName >= 0 ? cells[iName] : '' });
      }
      if (results.length) { results.sort((a, b) => a.rank - b.rank); return results; }
    }
    return [];
  }

  async function sendResults(reason) {
    const results = extractResults();
    if (!results.length) return { ok: false, error: '착순을 찾지 못했습니다. 결과(성적) 페이지인지 확인하세요.' };
    const { raceKey: override } = await getSettings();
    const raceKey = (override && override.trim()) || extractRaceKey();
    if (!raceKey) return { ok: false, error: 'raceKey를 만들 수 없습니다. 팝업에서 직접 입력하세요.' };
    const res = await chrome.runtime.sendMessage({
      type: 'POST_RESULTS', reason,
      payload: { raceKey, results, source: location.href },
    });
    return res || { ok: false, error: 'background 응답 없음' };
  }

  // ── [전체 자동 수집] 복승·쌍승·삼복승 3종 한 번에 ──────────────────
  //   현재 경주(URL 파라미터)의 3개 오즈 페이지를 동일출처 fetch → 파싱 → 서버 전송.
  //   복승=馬連(OddsUmLenFuku) · 쌍승=馬単(OddsUmLenTan) · 삼복승=3連複(Odds3LenFuku)
  function parseRankingCombos(doc) {
    const out = [];
    for (const t of doc.querySelectorAll('table.odd_ranking_table')) {
      const trs = [...t.querySelectorAll('tr')];
      const head = [...(trs[0]?.querySelectorAll('th,td') || [])].map((c) => txt(c));
      const ci = Math.max(0, head.findIndex((h) => /組合せ|組番/.test(h)));
      const oi = Math.max(1, head.findIndex((h) => /オッズ/.test(h)));
      for (const tr of trs.slice(1)) {
        const cells = [...tr.querySelectorAll('th,td')].map((c) => txt(c));
        if (!/^\d{1,2}(\s*[-–—ー]\s*\d{1,2})+$/.test(cells[ci] || '')) continue;
        const combo = cells[ci].split(/[-–—ー]/).map((x) => parseInt(x.trim(), 10)).filter((n) => n > 0);
        const odds = toNum(cells[oi]);
        if (combo.length >= 2 && odds != null) out.push({ combo, odds });
      }
    }
    return out;
  }

  async function fetchOddsDoc(oper, q) {
    const html = await fetch(`/KeibaWeb/TodayRaceInfo/${oper}?${q}`, { credentials: 'same-origin' }).then((r) => r.text());
    return new DOMParser().parseFromString(html, 'text/html');
  }

  // 3종은 keiba에서 별도 페이지(탭 클릭=페이지 이동)라, 페이지 이동 없이
  // 순서대로(복승→쌍승→삼복승) 동일출처 fetch 하여 각 배당을 추출한다.
  const TRIPLE_STEPS = [
    { key: 'quinella', oper: 'OddsUmLenFuku', label: '복승', len: 2, cap: 200 },
    { key: 'exacta', oper: 'OddsUmLenTan', label: '쌍승', len: 2, cap: 400 },
    { key: 'trio', oper: 'Odds3LenFuku', label: '삼복승', len: 3, cap: 300 },
  ];

  // [2] 진행상황 → storage 로 브로드캐스트 (팝업이 실시간 표시)
  function setTripleProgress(msg, done) {
    try { chrome.storage.local.set({ tripleProgress: { msg, done: !!done, t: Date.now() } }); } catch (_) { /* noop */ }
  }

  // [4] keiba 배당판(오즈) 페이지인지 확인 (경주 날짜·번호 URL 필수)
  function isKeibaOddsPage() {
    const sp = new URLSearchParams(location.search);
    return /(^|\.)keiba\.go\.jp$/.test(location.host) && !!sp.get('k_raceDate') && !!sp.get('k_raceNo');
  }

  async function collectTripleKeiba(reason) {
    if (!isKeibaOddsPage()) {
      setTripleProgress('❌ 배당판 페이지 아님', true);
      return { ok: false, error: 'keiba.go.jp 배당판(오즈) 페이지가 맞는지 확인하세요 — 경주 날짜·번호가 URL에 있어야 합니다.' };
    }
    const sp = new URLSearchParams(location.search);
    const q = ['k_raceDate', 'k_babaCode', 'k_raceNo']
      .filter((k) => sp.get(k)).map((k) => `${k}=${encodeURIComponent(sp.get(k))}`).join('&');
    const { raceKey: override } = await getSettings();
    const raceKey = (override && override.trim()) || extractRaceKey();
    const clean = (arr, cap) => arr
      .filter((c) => c.odds > 0)
      .map((c) => ({ combo: c.combo, odds: Math.round(c.odds * 10) / 10 }))
      .sort((a, b) => a.odds - b.odds)
      .slice(0, cap);
    const payload = { raceKey, quinella: [], exacta: [], trio: [], capturedAt: new Date().toISOString(), source: location.href };
    try {
      for (const st of TRIPLE_STEPS) {
        setTripleProgress(`${st.label} 수집중…`);        // "복승 수집중…" → "쌍승 수집중…" → …
        const doc = await fetchOddsDoc(st.oper, q);
        payload[st.key] = clean(parseRankingCombos(doc).filter((c) => c.combo.length === st.len), st.cap);
      }
      if (!payload.quinella.length && !payload.exacta.length && !payload.trio.length) {
        setTripleProgress('❌ 3종 배당 없음(발매 시간 확인)', true);
        return { ok: false, error: '3종 배당을 찾지 못했습니다(발매 시간/경주 확인).' };
      }
      setTripleProgress('서버 전송중…');
      const res = await chrome.runtime.sendMessage({ type: 'POST_TRIPLE', payload, reason });
      setTripleProgress(res && res.ok
        ? `3종 수집 완료 ✅ 복승 ${payload.quinella.length}·쌍승 ${payload.exacta.length}·삼복승 ${payload.trio.length}`
        : `❌ 전송 실패: ${(res && res.error) || ''}`, true);
      return res || { ok: false, error: 'background 응답 없음' };
    } catch (e) {
      setTripleProgress('❌ 수집 실패', true);
      return { ok: false, error: String(e.message || e) };
    }
  }

  // ══════════════════════════════════════════════════════════════════
  //  asyukk/사설 배당판: 인페이지 탭([복승][쌍승][삼복승]) 자동 클릭 수집
  //  ------------------------------------------------------------------
  //  keiba 와 달리 사설 사이트는 같은 페이지의 탭 버튼으로 배당표를 바꾼다.
  //  → 탭을 텍스트로 찾아 클릭 → 표가 바뀌었는지 확인 → 추출(변화 없으면 재시도)
  // ══════════════════════════════════════════════════════════════════
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  // [1번] 텍스트로 탭 버튼 찾기 (button/a/td/th/li/span/div, 보이는 것만)
  function findTabButton(labels) {
    const cands = [...document.querySelectorAll('button, a, td, th, li, span, div[role="tab"], .tab, .btn')];
    const norm = (e) => (e.textContent || '').replace(/\s+/g, '').trim();
    const visible = (e) => e.offsetParent !== null || (e.getClientRects && e.getClientRects().length > 0);
    // 1) 텍스트가 정확히 라벨과 같은 요소 (짧은 것 우선)
    for (const lb of labels) {
      const exact = cands.filter((e) => norm(e) === lb && visible(e))
        .sort((a, b) => (a.textContent || '').length - (b.textContent || '').length)[0];
      if (exact) return exact;
    }
    // 2) 짧은 텍스트에 라벨 포함
    for (const lb of labels) {
      const c = cands.find((e) => { const t = norm(e); return t.length <= 6 && t.includes(lb) && visible(e); });
      if (c) return c;
    }
    return null;
  }

  // 현재 표 상태의 시그니처(배당 셀 값) — 탭 전환 여부 감지용
  function oddsSignature() {
    const cells = [...document.querySelectorAll('.odds_content')].slice(0, 40)
      .map((c) => (c.textContent || '').trim());
    if (cells.length) return cells.join('|');
    return [...document.querySelectorAll('table.odds_table, table')]
      .map((t) => t.innerText).join(' ').replace(/\s+/g, ' ').slice(0, 400);
  }

  // [2번] 탭 클릭 → 대기 → 표 변경 확인(변화 없으면 재시도)
  async function clickTabAndWait(labels, prevSig, betLabel, requireChange) {
    console.log(`[배당수집] ${betLabel} 탭 클릭 시도... (labels=${labels.join('/')})`);
    let el = findTabButton(labels);
    if (!el) {
      console.warn(`[배당수집] ⚠ ${betLabel} 탭 버튼을 찾지 못했습니다.`);
      return { clicked: false, changed: false, sig: oddsSignature() };
    }
    for (let attempt = 1; attempt <= 3; attempt++) {
      try { el.click(); } catch (e) { console.warn(`[배당수집] ${betLabel} 클릭 오류`, e); }
      await wait(2000); // 데이터 로딩 대기
      const sig = oddsSignature();
      const changed = sig !== prevSig && sig.length > 0;
      if (!requireChange || changed) {
        console.log(`[배당수집] ${betLabel} 탭 활성화 ${changed ? '(데이터 변경 확인)' : '(현재 탭 유지)'}`);
        return { clicked: true, changed, sig };
      }
      console.warn(`[배당수집] ${betLabel} 데이터 변화 없음 → 재시도 ${attempt}/3`);
      el = findTabButton(labels) || el;
    }
    console.warn(`[배당수집] ⚠ ${betLabel} 클릭했으나 표가 바뀌지 않음.`);
    return { clicked: true, changed: false, sig: oddsSignature() };
  }

  // 현재 화면 매트릭스에서 (행 마번 × 열 마번) 쌍 원본 추출 (dedupe 없음)
  function currentMatrixPairs(oddsClass) {
    const tables = new Set();
    for (const c of document.querySelectorAll('.' + (oddsClass || 'odds_content'))) {
      const t = c.closest('table'); if (t) tables.add(t);
    }
    const scan = tables.size ? [...tables] : [...document.querySelectorAll('table.odds_table, table')];
    const out = [];
    for (const t of scan) {
      const { pairs } = parseMatrixTable(t, oddsClass ? { oddsClass } : {});
      out.push(...pairs); // {a:행, b:열, odds}
    }
    return out;
  }

  // 현재 화면에서 삼복승(3마리) 조합 추출: "a-b-c" 텍스트 + 인접/동행 배당
  function currentTrios() {
    const out = [], seen = new Set();
    for (const el of document.querySelectorAll('td, th, span, div, li')) {
      const t = (el.textContent || '').trim();
      const m = t.match(/^(\d{1,2})\s*[-–—ー]\s*(\d{1,2})\s*[-–—ー]\s*(\d{1,2})$/);
      if (!m) continue;
      const combo = [parseInt(m[1], 10), parseInt(m[2], 10), parseInt(m[3], 10)];
      if (combo.some((n) => !isHorseNo(n)) || new Set(combo).size !== 3) continue;
      // 배당: 같은 행의 .odds_content, 없으면 다음 형제 셀 숫자
      let od = null;
      const cell = el.closest('td, th') || el;
      const row = cell.closest('tr');
      if (row) { const oc = row.querySelector('.odds_content'); if (oc) od = toNum(oc.textContent); }
      if (od == null && cell.nextElementSibling) od = toNum(cell.nextElementSibling.textContent);
      if (od != null && od >= 1.0) {
        const k = combo.join('-');
        if (!seen.has(k)) { seen.add(k); out.push({ combo, odds: od }); }
      }
    }
    return out;
  }

  // [1번] 로컬 유력마 3마리 (서버 로직과 동일: 상위 10 복승조합 등장빈도+인기가중)
  function localKeyHorses(quin) {
    const m = {};
    for (const it of quin) {
      const c = it.combo; if (!c || c.length < 2) continue;
      const k = [c[0], c[1]].sort((a, b) => a - b).join('-'); const o = it.odds;
      if (o > 0 && (m[k] == null || o < m[k])) m[k] = o;
    }
    const top = Object.entries(m).sort((a, b) => a[1] - b[1]).slice(0, 10);
    const freq = {};
    for (const [k, o] of top) for (const h of k.split('-').map(Number)) freq[h] = (freq[h] || 0) + 1 + 1 / Math.max(o, 0.1);
    return Object.entries(freq).sort((a, b) => b[1] - a[1]).map(([h]) => +h).slice(0, 3);
  }

  // [2번] 상단 마번(축마) 버튼 찾기: 텍스트가 정확히 그 숫자인 클릭요소.
  //   오즈 테이블 밖 · button/a 우선(테이블 헤더 숫자와 구분).
  function findHorseButton(no) {
    const want = String(no);
    const pool = [...document.querySelectorAll('button, a, [onclick], .btn, .num, li, span, td')]
      .filter((e) => (e.textContent || '').trim() === want && (e.offsetParent !== null));
    pool.sort((a, b) => {
      const inTbl = (e) => (e.closest('table.odds_table, .odds_table, table') ? 1 : 0);
      if (inTbl(a) !== inTbl(b)) return inTbl(a) - inTbl(b);            // 테이블 밖 우선
      const rank = (e) => (/^(BUTTON|A)$/.test(e.tagName) || e.hasAttribute('onclick') ? 0 : 1);
      return rank(a) - rank(b);
    });
    return pool[0] || null;
  }

  // [1번][3번] 삼복승: 유력마 3마리를 "축"으로 각각 클릭 → 남은 두 말 매트릭스 → 3마리 조합
  async function collectTrioByAxis(keyHorses, oddsClass) {
    const trioMap = {};
    for (const axis of keyHorses) {
      console.log(`[배당수집] 삼복승 축 ${axis}번 버튼 클릭 시도...`);
      const btn = findHorseButton(axis);
      if (!btn) { console.warn(`[배당수집] ⚠ ${axis}번 축 버튼을 찾지 못함`); continue; }
      const before = oddsSignature();
      try { btn.click(); } catch (e) { console.warn(`[배당수집] ${axis}번 클릭 오류`, e); }
      await wait(1000); // 해당 말 기준 배당 로딩 대기
      let sig = oddsSignature(), tries = 0;
      while (sig === before && tries < 2) { await wait(700); sig = oddsSignature(); tries++; }
      let cnt = 0;
      for (const p of currentMatrixPairs(oddsClass)) {            // p={a:행, b:열} = 남은 두 말
        const combo = [axis, p.a, p.b];
        if (new Set(combo).size !== 3 || combo.some((n) => !isHorseNo(n))) continue;
        const key = [...combo].sort((x, y) => x - y).join('-');
        if (trioMap[key] == null || p.odds < trioMap[key]) { trioMap[key] = p.odds; cnt++; }
      }
      console.log(`[배당수집] 삼복승 ${axis}번 기준 추출: ${cnt}개 조합`);
    }
    return Object.entries(trioMap).map(([k, o]) => ({ combo: k.split('-').map(Number), odds: o }));
  }

  // [2번] 출마표2 전적 추출 (헤더 라벨 기반, 한국어·일본어 대응) + 상세 로그
  const STARTER_HDR = {
    no: /^(馬番|마번|번호|No\.?|출전)$/i, name: /馬名|마명|말이름|말명|Horse/i,
    jockey: /騎手|기수|jockey/i, weight: /斤量|부담중량|부담|負担|중량|weight/i,
    recent: /최근|전적|着順|성적|근성적|recent/i,
  };
  function collectStarters() {
    let best = null, bestScore = -1;
    for (const t of document.querySelectorAll('table')) {
      const trs = [...t.querySelectorAll('tr')]; if (trs.length < 2) continue;
      const head = [...trs[0].querySelectorAll('th,td')].map((c) => txt(c));
      const hasNo = head.some((h) => STARTER_HDR.no.test(h));
      const hasName = head.some((h) => STARTER_HDR.name.test(h));
      if (!hasNo || !hasName) continue;
      let score = 0; for (const k in STARTER_HDR) if (head.some((h) => STARTER_HDR[k].test(h))) score++;
      if (score > bestScore) { best = t; bestScore = score; }
    }
    if (!best) { console.warn('[전적] 출마표 테이블(마번+마명 헤더)을 찾지 못함'); return []; }
    const trs = [...best.querySelectorAll('tr')];
    const head = [...trs[0].querySelectorAll('th,td')].map((c) => txt(c));
    const idx = (k) => head.findIndex((h) => STARTER_HDR[k].test(h));
    const iNo = idx('no'), iName = idx('name'), iJock = idx('jockey'), iW = idx('weight'), iRec = idx('recent');
    console.log('[전적] 헤더:', JSON.stringify(head), '| no/name/jockey/weight/recent =', iNo, iName, iJock, iW, iRec);
    const out = [];
    for (const tr of trs.slice(1)) {
      const cells = [...tr.querySelectorAll('th,td')].map((c) => txt(c));
      const no = toNum(cells[iNo]);
      if (!isHorseNo(no) || !/^\d{1,2}$/.test((cells[iNo] || '').trim())) continue;
      // 최근 착순: recent 열의 "1-3-2" 패턴 → 없으면 행 전체에서 착순 시퀀스 탐색
      const recCell = iRec >= 0 ? (cells[iRec] || '') : '';
      const seq = (recCell.match(/\d+(?:\s*[-·・]\s*\d+){1,5}/) || [])[0]
        || (cells.join(' ').match(/\d+(?:\s*[-·・]\s*\d+){2,5}/) || [])[0] || '';
      const recent = seq ? seq.split(/[-·・]/).map((x) => parseInt(x.trim(), 10)).filter((n) => n > 0).slice(0, 5) : [];
      out.push({
        no, name: iName >= 0 ? cells[iName] : '', jockey: iJock >= 0 ? cells[iJock] : '',
        recent, weight: iW >= 0 ? toNum(cells[iW]) : null,
      });
    }
    console.log(`[전적] 추출: ${out.length}두`, out.slice(0, 3));
    return out;
  }

  // [1번] 출마표2 탭 클릭 → 1.5초 대기 → 전적 추출 (탭 없으면 현재화면 시도)
  async function collectStartersByTab() {
    const r = await clickTabAndWait(['출마표2', '출마표', '출주표', '出馬表'], oddsSignature(), '출마표2', false);
    if (!r.clicked) console.warn('[전적] 출마표2 탭 버튼을 찾지 못함 — 현재 화면에서 시도');
    await wait(1500);
    return collectStarters();
  }

  // asyukk/generic 전체 3종 수집 (탭 클릭 방식)
  async function collectTripleByTabs(reason) {
    const site = detectSite();
    const oddsClass = site === 'asyukk' ? 'odds_content' : null;
    const { raceKey: override } = await getSettings();
    const raceKey = (override && override.trim()) || extractRaceKey();
    if (!raceKey) {
      setTripleProgress('❌ raceKey 필요', true);
      return { ok: false, error: '사설 사이트는 raceKey 자동 감지가 안 됩니다. 팝업 raceKey 칸에 입력 후 다시 시도하세요.' };
    }
    const clean = (arr, cap) => arr
      .filter((c) => c.odds > 0)
      .map((c) => ({ combo: c.combo, odds: Math.round(c.odds * 10) / 10 }))
      .sort((a, b) => a.odds - b.odds).slice(0, cap);
    console.log('[배당수집] ===== 전체 3종 수집 시작 (탭 클릭 방식) =====');

    try {
      // 1) 복승 (이미 복승 탭일 수 있음 → 변화 강제 안 함)
      setTripleProgress('복승 수집중…');
      await clickTabAndWait(['복승', '복연', '馬連'], '', '복승', false);
      let sig = oddsSignature();
      const quinMap = {};
      for (const p of currentMatrixPairs(oddsClass)) {
        if (!isHorseNo(p.a) || !isHorseNo(p.b) || p.a === p.b) continue;
        const k = p.a < p.b ? `${p.a}-${p.b}` : `${p.b}-${p.a}`;
        if (quinMap[k] == null || p.odds < quinMap[k]) quinMap[k] = p.odds;
      }
      const quinella = Object.entries(quinMap).map(([k, o]) => {
        const [a, b] = k.split('-').map(Number); return { combo: [a, b], odds: o };
      });
      console.log(`[배당수집] 복승 데이터 추출: ${quinella.length}개 조합`);

      // 2) 쌍승 (순서 있음 → 방향 유지, dedupe는 a>b 키)
      setTripleProgress('쌍승 수집중…');
      const r2 = await clickTabAndWait(['쌍승', '마단', '쌍승식', '馬単'], sig, '쌍승', true);
      sig = r2.sig || oddsSignature();
      const exMap = {};
      for (const p of currentMatrixPairs(oddsClass)) {
        if (!isHorseNo(p.a) || !isHorseNo(p.b) || p.a === p.b) continue;
        const k = `${p.a}>${p.b}`;
        if (exMap[k] == null || p.odds < exMap[k]) exMap[k] = p.odds;
      }
      const exacta = Object.entries(exMap).map(([k, o]) => {
        const [a, b] = k.split('>').map(Number); return { combo: [a, b], odds: o };
      });
      console.log(`[배당수집] 쌍승 데이터 추출: ${exacta.length}개 조합`);

      // 3) 삼복승: 유력마 3마리를 축으로 클릭 → 각 축 매트릭스 추출 (텍스트형이면 폴백)
      const keyHorses = localKeyHorses(quinella);
      console.log(`[배당수집] 유력마(로컬) 1~3순위: ${keyHorses.join('·') || '-'}`);
      setTripleProgress(`삼복승 수집중… (축 ${keyHorses.join('·') || '?'})`);
      await clickTabAndWait(['삼복승', '삼복', '삼쌍승', '3連複'], sig, '삼복승', false);
      let trio = [];
      if (keyHorses.length) trio = await collectTrioByAxis(keyHorses, oddsClass);
      if (!trio.length) {                                   // 폴백: "a-b-c" 텍스트형
        trio = currentTrios().map((t) => ({ combo: t.combo, odds: t.odds }));
        console.log(`[배당수집] 삼복승 텍스트형 폴백 추출: ${trio.length}개 조합`);
      }
      console.log(`[배당수집] 삼복승 총 추출: ${trio.length}개 조합`);

      // 4) 출마표2 전적 (탭 클릭 → 1.5초 → 추출 → 복승 복귀)
      setTripleProgress('출마표2 전적 수집중…');
      let starters = [];
      try { starters = await collectStartersByTab(); } catch (e) { console.warn('[전적] 수집 오류', e); }
      await clickTabAndWait(['복승', '복연', '馬連'], '', '복승(복귀)', false); // 복승으로 복귀

      const payload = {
        raceKey, quinella: clean(quinella, 200), exacta: clean(exacta, 400), trio: clean(trio, 300),
        capturedAt: new Date().toISOString(), source: location.href,
      };
      console.log(`[배당수집] ===== 완료: 복승 ${payload.quinella.length}·쌍승 ${payload.exacta.length}·삼복승 ${payload.trio.length}·전적 ${starters.length}두 =====`);
      if (!payload.quinella.length && !payload.exacta.length && !payload.trio.length && !starters.length) {
        setTripleProgress('❌ 배당·전적 모두 없음(콘솔 로그 확인)', true);
        return { ok: false, error: '배당·전적을 찾지 못했습니다. F12 콘솔의 로그를 확인하세요.' };
      }
      setTripleProgress('서버 전송중…');
      const res = await chrome.runtime.sendMessage({ type: 'POST_TRIPLE', payload, reason });
      // [3번] 전적이 있으면 배당+전적 통합 분석 엔드포인트로 전송
      let japan = null;
      if (starters.length) {
        japan = await chrome.runtime.sendMessage({
          type: 'POST_JAPAN', reason,
          payload: { raceKey, horses: starters, source: location.href },
        });
        console.log('[전적] /api/extract/japan 응답:', japan && japan.ok ? 'ok' : (japan && japan.error));
      }
      setTripleProgress(res && res.ok
        ? `수집 완료 ✅ 복승 ${payload.quinella.length}·쌍승 ${payload.exacta.length}·삼복승 ${payload.trio.length}·전적 ${starters.length}두`
        : `❌ 전송 실패: ${(res && res.error) || ''}`, true);
      return res || { ok: false, error: 'background 응답 없음' };
    } catch (e) {
      console.error('[배당수집] 수집 실패', e);
      setTripleProgress('❌ 수집 실패', true);
      return { ok: false, error: String(e.message || e) };
    }
  }

  // 사이트별 3종 수집 분기: keiba=별도URL fetch / 그 외=탭 클릭
  async function collectTriple(reason) {
    return detectSite() === 'keiba' ? collectTripleKeiba(reason) : collectTripleByTabs(reason);
  }

  // ── 설정 로드 & 자동전송 루프 ───────────────────────────────────────
  let timer = null;

  async function getSettings() {
    return new Promise((resolve) => {
      chrome.storage.local.get(
        // autoMode: 'triple'(전체 3종) | 'snapshot'(단승만)
        { autoSend: false, intervalSec: 60, raceKey: '', autoMode: 'triple' },
        (v) => resolve(v)
      );
    });
  }

  async function doSend(reason) {
    const { raceKey } = await getSettings();
    const payload = buildPayload(raceKey);
    if (!payload.raceKey) {
      return { ok: false, error: 'raceKey 를 만들 수 없습니다. 팝업에서 직접 입력하세요.', payload };
    }
    if (!Object.keys(payload.odds).length) {
      return { ok: false, error: '단승 배당을 찾지 못했습니다. 배당 페이지가 맞는지 확인하세요.', payload };
    }
    // 실제 POST 는 background 가 담당 (mixed-content/CORS 회피)
    const res = await chrome.runtime.sendMessage({ type: 'POST_SNAPSHOT', payload, reason });
    return res || { ok: false, error: 'background 응답 없음' };
  }

  // ── 사이트 무관 전송: 복승 매트릭스→triple ingest, 단승→snapshot ──────
  //   snapshot 은 단승(odds) 이 비면 거부하고 복승 매트릭스를 저장하지 않으므로,
  //   asyukk/generic 의 복승 매트릭스는 /api/odds/triple/ingest(quinella) 로 보내
  //   서버 앱의 매트릭스 UI 에서 바로 보이게 한다. 단승이 있으면 snapshot 도 함께.
  async function sendCurrent(reason) {
    const { raceKey: override } = await getSettings();
    const payload = buildPayload(override);
    if (!payload.raceKey) {
      return { ok: false, error: 'raceKey 를 만들 수 없습니다. 팝업 raceKey 칸에 직접 입력하세요.', payload };
    }
    const pairs = (payload.quinella && payload.quinella.pairs) || [];
    const oddsMap = payload.odds || {};
    if (!pairs.length && !Object.keys(oddsMap).length) {
      return { ok: false, error: '전송할 배당이 없습니다(복승 매트릭스·단승 모두 비어있음). 배당판 페이지인지 확인하세요.', payload };
    }
    const parts = [];
    // 복승 매트릭스 → triple ingest (매트릭스 UI 용)
    if (pairs.length) {
      const quinella = pairs
        .map((p) => ({ combo: [p.a, p.b], odds: Math.round(p.odds * 10) / 10 }))
        .sort((a, b) => a.odds - b.odds).slice(0, 300);
      const r = await chrome.runtime.sendMessage({
        type: 'POST_TRIPLE', reason,
        payload: { raceKey: payload.raceKey, quinella, exacta: [], trio: [], capturedAt: payload.capturedAt, source: payload.source },
      });
      parts.push({ kind: '복승매트릭스', n: quinella.length, ...(r || { ok: false, error: 'background 응답 없음' }) });
    }
    // 단승 → snapshot
    if (Object.keys(oddsMap).length) {
      const r = await chrome.runtime.sendMessage({ type: 'POST_SNAPSHOT', reason, payload });
      parts.push({ kind: '단승', n: Object.keys(oddsMap).length, ...(r || { ok: false, error: 'background 응답 없음' }) });
    }
    const ok = parts.length > 0 && parts.every((p) => p.ok);
    const detail = parts.map((p) => `${p.kind} ${p.n}${p.ok ? '✅' : '❌'}`).join(' · ');
    return { ok, parts, detail, raceKey: payload.raceKey, error: ok ? '' : (parts.find((p) => !p.ok)?.error || '전송 실패') };
  }

  async function restartLoop() {
    if (timer) { clearInterval(timer); timer = null; }
    const { autoSend, intervalSec, autoMode } = await getSettings();
    if (autoSend) {
      const ms = Math.max(10, Number(intervalSec) || 60) * 1000;
      // [3] 자동간격 설정 시 전체 3종 수집(기본) 또는 단승 스냅샷
      const runAuto = () => (autoMode === 'snapshot' ? doSend('auto') : collectTriple('auto'));
      timer = setInterval(runAuto, ms);
      runAuto(); // 켜는 즉시 1회
    }
  }

  // 설정이 바뀌면 루프 재시작
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === 'local' && (changes.autoSend || changes.intervalSec || changes.autoMode)) restartLoop();
  });

  // 팝업 ↔ content 메시지 처리
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg?.type === 'MANUAL_SEND') {
      // keiba 는 단승 snapshot, 그 외(asyukk/generic)는 복승 매트릭스+단승 통합 전송
      (detectSite() === 'keiba' ? doSend('manual') : sendCurrent('manual')).then(sendResponse);
      return true; // async
    }
    if (msg?.type === 'MANUAL_SEND_RESULTS') {
      sendResults('manual').then(sendResponse);
      return true;
    }
    if (msg?.type === 'MANUAL_COLLECT_TRIPLE') {
      collectTriple('manual').then(sendResponse);
      return true;
    }
    // [4번] 미리보기: 전송 전 추출 결과 + 검증 + 상위 10조합 반환
    if (msg?.type === 'PREVIEW') {
      getSettings().then(({ raceKey }) => {
        const payload = buildPayload(raceKey);
        const validation = validateExtraction(payload);
        const top = ((payload.quinella && payload.quinella.pairs) || [])
          .slice().sort((a, b) => a.odds - b.odds).slice(0, 10)
          .map((p) => ({ combo: `${p.a}-${p.b}`, odds: p.odds }));
        sendResponse({
          site: payload.site, raceKey: payload.raceKey,
          singles: (payload.horses || []).length,
          combos: ((payload.quinella && payload.quinella.pairs) || []).length,
          top, validation,
        });
      });
      return true;
    }
  });

  restartLoop();

  // [2번] 결과 페이지면 로드 직후 1회 자동 전송 (URL result/성적표 감지)
  if (isResultPage()) {
    setTimeout(() => { sendResults('auto-result').catch(() => {}); }, 800);
  }
})();
