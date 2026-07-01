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

  // ── 전체 payload 조립 → 서버 snapshot 형식 ──────────────────────────
  //   서버 계약: POST /api/odds/snapshot  { raceKey, odds:{마번:단승배당} }
  //   (복승 매트릭스/말이름은 확장 필드로 함께 실어 보냄 — 서버는 무시해도 안전)
  function buildPayload(overrideRaceKey) {
    const horses = extractHorses();
    const quinella = extractQuinella();

    // 서버 필수 필드: odds = {마번: 단승배당}
    const odds = {};
    for (const h of horses) if (h.win != null) odds[String(h.no)] = h.win;

    const raceKey = (overrideRaceKey && overrideRaceKey.trim()) || extractRaceKey();

    return {
      raceKey,
      odds, // ← 서버가 저장하는 단승 시계열
      // 확장 필드 (참고용)
      horses, // [{no,name,win,place}]
      quinella, // {pairs:[{a,b,odds}], matrix:{a:{b:odds}}}
      capturedAt: new Date().toISOString(),
      source: location.href,
    };
  }

  // ── 설정 로드 & 자동전송 루프 ───────────────────────────────────────
  let timer = null;

  async function getSettings() {
    return new Promise((resolve) => {
      chrome.storage.local.get(
        { autoSend: false, intervalSec: 60, raceKey: '' },
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

  async function restartLoop() {
    if (timer) { clearInterval(timer); timer = null; }
    const { autoSend, intervalSec } = await getSettings();
    if (autoSend) {
      const ms = Math.max(10, Number(intervalSec) || 60) * 1000;
      timer = setInterval(() => doSend('auto'), ms);
      doSend('auto-immediate'); // 켜는 즉시 1회
    }
  }

  // 설정이 바뀌면 루프 재시작
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === 'local' && (changes.autoSend || changes.intervalSec)) restartLoop();
  });

  // 팝업 ↔ content 메시지 처리
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg?.type === 'MANUAL_SEND') {
      doSend('manual').then(sendResponse);
      return true; // async
    }
    if (msg?.type === 'PREVIEW') {
      chrome.storage.local.get({ raceKey: '' }, ({ raceKey }) => {
        sendResponse(buildPayload(raceKey));
      });
      return true;
    }
  });

  restartLoop();
})();
