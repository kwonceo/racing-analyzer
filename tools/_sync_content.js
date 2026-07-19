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

  // [raceKey 오검출 수정] 현재 '활성화된 경주 탭' 또는 두드러진 경주 헤더에서 경주번호를 읽는다.
  //   증상: URL k_raceNo(또는 본문 첫 第N競走)가 stale/목록 첫 항목이라 실제 표시 경주(9R)를 4R로 오검출.
  //   → 화면에 실제 표시 중인 경주 = 활성 탭(current/active/selected)·selected option·강조 헤더가 진실.
  function _activeRaceNo() {
    const _n = (t) => {
      const m = String(t || '').replace(/[\s　]/g, '').match(/(?:第)?(\d{1,2})(?:競走|R|レース|경주)?/);
      if (!m) return null;
      const n = parseInt(m[1], 10);
      return (n >= 1 && n <= 12) ? n : null;
    };
    // 1) 활성/선택된 경주 탭·링크(사이트별 클래스 후보 — 하나라도 걸리면 그 번호가 현재 경주)
    const tabSels = [
      'li.current a', 'li.active a', 'li.selected a', 'li.on a', 'a.current', 'a.active', 'a.selected',
      '.current a', '.active a', '.selected a', '[aria-current] ', '[aria-current="true"]',
      '[aria-selected="true"]', '.raceNavi .current', '.race-nav .current', '.raceNo.current',
      '.nowRace', '.now-race', '.race-tab.active', '.tab.active', 'li[class*="current"] a',
    ];
    for (const sel of tabSels) {
      try {
        for (const el of document.querySelectorAll(sel)) {
          const n = _n(el.textContent);
          if (n) return n;
        }
      } catch (_) { /* 잘못된 선택자 무시 */ }
    }
    // 2) select(경주 드롭다운)의 선택된 option
    try {
      for (const sel of document.querySelectorAll('select')) {
        const opt = sel.options && sel.options[sel.selectedIndex];
        if (opt && /R|競走|レース|경주|race/i.test(opt.textContent + (sel.name || '') + (sel.id || ''))) {
          const n = _n(opt.textContent);
          if (n) return n;
        }
      }
    } catch (_) { /* */ }
    // 3) 두드러진 경주 헤더(h1~h3·타이틀 계열) — 목록 본문보다 우선
    try {
      for (const h of document.querySelectorAll('h1,h2,h3,.raceTitle,.race-title,.raceHead,.race_head,.raceNumber,.race_no')) {
        const n = _n(h.textContent);
        if (n && /第\s*\d|R|レース|競走/.test(h.textContent)) return n;
      }
    } catch (_) { /* */ }
    return null;
  }

  function extractRaceKey() {
    const q = new URLSearchParams(location.search);
    let date = '', track = '', raceNo = '';

    const rd = (q.get('k_raceDate') || '').match(/(\d{4})\D(\d{1,2})\D(\d{1,2})/);
    if (rd) date = `${rd[1]}-${rd[2].padStart(2, '0')}-${rd[3].padStart(2, '0')}`;
    // [오검출 수정·최우선] 활성 경주 탭/헤더의 번호 = 실제 표시 경주 → URL/본문보다 우선.
    const activeNo = _activeRaceNo();
    const rn = q.get('k_raceNo');
    if (activeNo) {
      raceNo = `${activeNo}R`;
      if (rn && parseInt(rn, 10) !== activeNo) {
        console.log(`[raceKey 오검출 방지] 활성 경주 탭=${activeNo}R ≠ URL k_raceNo=${rn}R → 활성 탭(${activeNo}R) 채택`);
      }
    }
    if (!raceNo && rn) raceNo = `${parseInt(rn, 10)}R`;   // 활성 탭 못 읽었을 때만 URL 파라미터
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

    // [1번][한국·사설 배당판] 하단 네비게이션 "이전 <경마장> <N>경주 <거리> 다음" = 현재 표시 중인 경주.
    //   현재 경주의 '가장 확실한' 신호이므로 URL/일본어/목록 추출값보다 우선한다(경주가 바뀌면 이 텍스트가 바뀐다).
    //   [수정] 미리 정의된 경마장 목록에 없는 이름(예: 고쿠라)도 잡히도록 '이전 … 경주' 사이의 한글 경마장명을 직접 추출.
    //   본문 다른 곳(경주리스트 등)의 타 경마장 언급을 잘못 집지 않도록 '이전' 네비에 앵커링한다.
    let nav = body.match(/이전\s*([가-힣]{2,7})\s*(\d{1,2})\s*경주[\s\S]{0,15}?다음/);   // "이전 … 다음" 사이 우선
    if (!nav) nav = body.match(/이전\s*([가-힣]{2,7})\s*(\d{1,2})\s*경주/);                 // '다음' 없이도 '이전' 앵커
    if (nav) {
      track = nav[1]; raceNo = `${parseInt(nav[2], 10)}경주`;
    } else {
      // 폴백: 예정 경마장명 목록(한국 KRA + 일본 지방 NAR + 일본 중앙 JRA 주요 경마장 — 고쿠라·삿포로 등 추가) 매칭
      const km = body.match(/(제주|서울|부산경남|부경|부산|과천|나고야|오오이|오이|소노다|후나바시|카와사키|가와사키|몬베츠|후크시마|후쿠시마|히코다테|하코다테|모리오카|미즈사와|우라와|카나자와|가나자와|카사마츠|사가|고치|히메지|오비히로|반에이|카고시마|몬베쓰|카시와|고쿠라|삿포로|니가타|도쿄|나카야마|주쿄|교토|한신)\s*(\d{1,2})\s*경주/);
      if (km) { track = km[1]; raceNo = `${parseInt(km[2], 10)}경주`; }
    }

    return [date, track, raceNo].filter(Boolean).join(' ').trim();
  }

  // ── [발주시간 자동 감지] 배당판 본문에서 발주시각(HH:MM) 읽기 ──────────
  //   한국/사설: "발주 16:00" · "발주시각 16:00" 등, 일본: "発走 16:00" · "締切 15:59".
  //   키워드 인접 패턴만 채택(본문의 무관한 시각 오탐 방지).
  //   [오탐 방지] ① 명시 라벨("발주시간/발주시각/発走/締切")을 바레 "발주"보다 우선.
  //              ② 시계(HH:MM:SS)는 거부 — 헤더 현재시각 "발주 17:31:46"을 발주시각으로 잡던 버그.
  //                 각 패턴 끝의 (?![:：]?\d) 가 뒤따르는 ":초"(=시계)를 배제.
  const POST_TIME_RES = [
    // 1) 명시 라벨 우선(발주시간/발주시각/発走/締切/발매마감) — 시계 거부
    /(?:발주\s*(?:시각|시간)|출발\s*시각|発走\s*(?:時刻|予定)?|締\s*切|締め切り|発売\s*締切)\s*[:：]?\s*(\d{1,2})\s*[:：]\s*(\d{2})(?![:：]?\d)/,
    // 1-b) [경륜/경정 조기감지] 投票締切·発走時刻·締切時刻·レース発走 등 일본 공영경기 라벨 — 시계 거부
    /(?:投票\s*締切|締切\s*時刻|発走\s*時刻|レース\s*発走|次\s*レース)\s*[:：]?\s*(\d{1,2})\s*[:：]\s*(\d{2})(?![:：]?\d)/,
    // 2) 폴백: 바레 "발주 HH:MM"(예정/예상 포함) — 시계 거부
    /(?:발주\s*(?:예정|예상)?)\s*[:：]?\s*(\d{1,2})\s*[:：]\s*(\d{2})(?![:：]?\d)/,
  ];
  function detectPostTime() {
    const txt = ((document.body && document.body.innerText) || '').replace(/\s+/g, ' ');
    for (const re of POST_TIME_RES) {
      const m = txt.match(re);
      if (m) {
        const h = parseInt(m[1], 10), mi = parseInt(m[2], 10);
        if (h >= 0 && h <= 23 && mi >= 0 && mi <= 59) {
          return { hh: h, mm: mi, raw: `${String(h).padStart(2, '0')}:${String(mi).padStart(2, '0')}` };
        }
      }
    }
    return null;
  }
  // [발주시각 오탐 방지 - 핵심] 배당판의 "남은시간" 카운트다운(현재 표시 경주 전용)을 ms 로.
  //   사설 배당판은 "남은시간" 라벨이 깨끗한 텍스트로 안 잡히는 경우가 있어(값만 "6분 48초"),
  //   ① 라벨 인접 매칭 우선 → ② 실패 시 라벨 없는 "N분 M초"(카운트다운)도 채택.
  //   임박 카운트다운 범위(0<ms≤99분)만 신뢰 → 무관한 "N분 M초" 오탐 최소화.
  //   ※ 이 사이트는 모의배당판이라 "발주시간 HH:MM"이 실제 시계와 불일치 → 카운트다운이 유일 신뢰신호.
  function detectRemainingMs() {
    try {
      const txt = (document.body && document.body.innerText) || '';
      // 라벨 인접(남은시간/残り/あと/締切まで) N분 M초 우선
      let m = txt.match(/(?:남은\s*시간|残り\s*時間?|あと|締切\s*まで)[\s\S]{0,16}?(\d{1,2})\s*(?:분|分)\s*(\d{1,2})\s*(?:초|秒)/);
      if (!m) m = txt.match(/(\d{1,2})\s*(?:분|分)\s*(\d{1,2})\s*(?:초|秒)/);   // 라벨 소실 폴백(한/일)
      if (m) {
        const ms = (parseInt(m[1], 10) * 60 + parseInt(m[2], 10)) * 1000;
        if (ms > 0 && ms <= 99 * 60 * 1000) return ms;  // 0<~99분(임박 카운트다운)만
      }
      // [경륜/경정 조기감지] 초 없이 "あと N分"·"残り N分"·"締切まで N分"만 표기되는 배당판 대응
      let mm = txt.match(/(?:残り|あと|締切\s*まで|남은\s*시간)[\s\S]{0,10}?(\d{1,2})\s*(?:분|分)(?!\s*\d)/);
      if (mm) {
        const ms2 = parseInt(mm[1], 10) * 60 * 1000;
        if (ms2 > 0 && ms2 <= 99 * 60 * 1000) return ms2;
      }
    } catch (_) { /* noop */ }
    return null;
  }
  // HH:MM → 오늘(이미 지났으면 내일) epoch ms. timer.js timeToDeadline 과 동일 규칙.
  function postTimeToDeadline(hh, mm) {
    const d = new Date(); d.setHours(hh, mm, 0, 0);
    let ms = d.getTime();
    if (ms < Date.now() - 60000) ms += 24 * 3600 * 1000;
    return ms;
  }
  /** 발주시각을 감지해 storage(timerDeadline·timerTime)에 자동 설정 → timer.js 카운트다운 자동 시작.
   *  같은 경주에서 사용자가 수동 입력했으면 존중하고, 경주가 바뀌면 자동 감지가 다시 우선한다. */
  function autoDetectPostTime(raceKey) {
    // [최우선] 배당판 "남은시간" 카운트다운(현재 표시 경주 전용)으로 발주시각 산출.
    //   상단 네비의 다른 경주 "발주 HH:MM"(예: 大井12R 07:23) / 헤더 시계를 잘못 잡던 버그 방지.
    let pt, ms;
    const remain = detectRemainingMs();
    if (remain != null) {
      ms = Date.now() + remain;
      const d = new Date(ms);
      pt = { hh: d.getHours(), mm: d.getMinutes(),
             raw: `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}` };
    } else {
      pt = detectPostTime();
      if (!pt) return Promise.resolve(null);
      ms = postTimeToDeadline(pt.hh, pt.mm);
      // [증상 차단] HH:MM 폴백이 6시간 넘게 미래로 잡히면(예: 지난 시각→내일로 밀림/헤더 잔재)
      //   "18시간 후" 류 오탐이므로 자동설정하지 않음. 임박 발주(≤6h)만 신뢰.
      if (ms - Date.now() > 6 * 3600 * 1000) return Promise.resolve(null);
    }
    return new Promise((resolve) => {
      try {
        chrome.storage.local.get({ timerDeadline: 0, deadlineSource: '', autoDeadlineRaceKey: '' }, (v) => {
          const raceChanged = !!(raceKey && v.autoDeadlineRaceKey && v.autoDeadlineRaceKey !== raceKey);
          if (v.deadlineSource === 'manual' && !raceChanged) { resolve(null); return; }  // 이 경주는 수동값 존중
          const diff = Math.abs((v.timerDeadline || 0) - ms);
          if (diff < 30000 && v.autoDeadlineRaceKey === raceKey) { resolve(pt); return; } // 이미 동일 → 재기록 생략
          chrome.storage.local.set({
            timerDeadline: ms, timerTime: pt.raw, deadlineSource: 'auto', autoDeadlineRaceKey: raceKey || '',
          }, () => {
            console.log(`[발주감지] 발주시각 ${pt.raw} 자동 설정${raceKey ? ' (raceKey=' + raceKey + ')' : ''}`);
            resolve(pt);
          });
        });
      } catch (_) { resolve(null); }
    });
  }

  // ── 1+2+ : 마번/말이름 + 단승 + 복승(place) 추출 (単勝・複勝 표) ──────
  //   실측 구조(class="odd_popular_table_02"):
  //     헤더: 枠 | 馬番 | 馬名 | 単勝オッズ | 複勝オッズ(3着払い) | [複勝上限] | 性齢 | …
  //     주의: 枠 와 馬番 이 별도 컬럼이고, 複勝 는 "4.3-" + "13.6" 두 셀로 나뉜다.
  //   → 위치 추측이 아니라 헤더 라벨로 컬럼 인덱스를 찾는다(방어적).
  function extractHorses(doc) {
    doc = doc || document;   // [2번] 인자로 받은 fetch 문서에서도 단승/복승 추출 가능
    const horses = {};
    const tables = [
      ...doc.querySelectorAll('table.odd_popular_table_02'),
      ...doc.querySelectorAll('table'),
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
        // [출전취소·除外] 競走除外/取消/失格/中止 텍스트 감지 → scratched 표시(추천 제거용)
        const rowText = (cells.join(' ') + ' ' + name);
        const scratched = /除外|取消|出走取消|競走除外|中止|失格|출전\s?취소|제외/.test(rowText);
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
        horses[no] = { no, name, win, place, scratched };
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
  //  keiba.go.jp 외에 ks1.dke-d11diw.site(사설 배당판, class=odds_table/
  //  odds_content) 등 사이트마다 DOM 이 달라, URL 로 사이트를 감지해 파서를
  //  분기한다. 미지의 사이트는 "표 안의 마번 축 + 숫자 셀" 패턴으로 범용 파싱.
  // ═══════════════════════════════════════════════════════════════════

  // [2번] 사이트 자동 감지
  function detectSite() {
    const h = location.host;
    if (/(^|\.)keiba\.go\.jp$/.test(h)) return 'keiba';
    if (/asyukk|qwqwd|dke-d11diw/i.test(h)) return 'asyukk';
    return 'generic';
  }

  const pureInt = (s) => (/^\d{1,2}$/.test((s || '').trim()) ? parseInt(s, 10) : null);
  // [신규 미러 사이트 대응] 열 머리글이 '1.' 처럼 점이 붙은 경우까지 마번으로 인식(헤더 전용).
  const hdrInt = (s) => { const m = /^(\d{1,2})\.?$/.exec((s || '').trim()); return m ? parseInt(m[1], 10) : null; };

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
      const c = [...r.cells].filter((td) => hdrInt(td.textContent) != null).length;
      if (c > best) { best = c; headerRow = r; }
    }
    // 헤더의 '열 마번'을 등장 순서(headerNos)와 cellIndex 두 방식으로 수집
    let headerNos = [];
    const colNoByIndex = {};
    if (headerRow) {
      for (const cell of headerRow.cells) {
        const n = hdrInt(cell.textContent);
        if (n != null) { headerNos.push(n); colNoByIndex[cell.cellIndex] = n; }
      }
    }
    // 배당 셀 판정: 소수 배당(47.1) 또는 3자리+ 정수(100=100배↑ 상한 표시) — '100'은 무효 아님, 고배당 롱샷.
    const _odCell = (s) => { const t = (s || '').trim(); return /^\d+\.\d+$/.test(t) || /^\d{3,}$/.test(t); };
    const isOdds = opts.oddsClass
      ? (td) => td.classList.contains(opts.oddsClass) || _odCell(td.textContent)
      : (td) => _odCell(td.textContent);

    // [출전취소 번호 밀림 근본방어] 취소마(競走除外/取消)의 행은 배당셀이 0개다.
    //   그 마번을 '취소 컬럼'으로 판정해 열 축(headerNos·colNoByIndex)에서 제거한다.
    //   제거하지 않으면 아래 순서기반 매핑(cols[i])이 취소 컬럼 수만큼 밀려 이후 조합 마번이 전부
    //   어긋난다(예: 3번 취소 시 4-5 배당이 3-4 로 밀려 저장 → 취소마·유령조합 추천). 취소 없으면 무영향.
    if (headerNos.length >= 2) {
      const scratchedCols = new Set();
      const hset = new Set(headerNos);
      for (const r of rows) {
        if (r === headerRow) continue;
        let rn = null;
        for (const cell of r.cells) { const n = pureInt(cell.textContent); if (n != null) { rn = n; break; } }
        if (rn == null || !hset.has(rn)) continue;
        if ([...r.cells].filter(isOdds).length === 0) scratchedCols.add(rn); // 배당셀 전무 = 취소마 행
      }
      if (scratchedCols.size) {
        headerNos = headerNos.filter((n) => !scratchedCols.has(n));
        for (const ci of Object.keys(colNoByIndex)) {
          if (scratchedCols.has(colNoByIndex[ci])) delete colNoByIndex[ci];
        }
        try { console.log('[출전취소·밀림방어] 취소 컬럼 제거:', [...scratchedCols].sort((a, b) => a - b)); } catch (_) {}
      }
    }

    const pairs = [], singles = [];
    for (const r of rows) {
      if (r === headerRow) continue;
      // 행 마번 = 첫 정수 셀
      let rowNo = null;
      for (const cell of r.cells) { const n = pureInt(cell.textContent); if (n != null) { rowNo = n; break; } }
      const oddsCells = [...r.cells].filter(isOdds);
      if (!oddsCells.length) continue;

      if (headerNos.length >= 2 && rowNo != null) {
        // ── 매트릭스: [버그수정] 삼각형/전체 구조 모두 대응.
        //   삼각 매트릭스는 행마다 배당셀이 좌우로 밀려 cellIndex 정렬이 깨진다.
        //   → 배당셀 개수로 열 집합(전체/상삼각/하삼각)을 판별해 '순서 기반'으로 매핑.
        const all = headerNos.filter((n) => n !== rowNo);
        const upper = headerNos.filter((n) => n > rowNo);
        const lower = headerNos.filter((n) => n < rowNo);
        let cols = null;
        if (oddsCells.length === all.length) cols = all;
        else if (oddsCells.length === upper.length) cols = upper;
        else if (oddsCells.length === lower.length) cols = lower;
        if (cols) {
          oddsCells.forEach((oc, i) => {
            const colNo = cols[i]; const val = toNum(oc.textContent);
            if (colNo != null && colNo !== rowNo && val != null && val >= 1.0) pairs.push({ a: rowNo, b: colNo, odds: val });
          });
        } else {
          // 폴백: 빈칸 placeholder가 위치를 유지하는 구조 → cellIndex 정렬
          for (const oc of oddsCells) {
            const colNo = colNoByIndex[oc.cellIndex]; const val = toNum(oc.textContent);
            if (colNo != null && colNo !== rowNo && val != null && val >= 1.0) pairs.push({ a: rowNo, b: colNo, odds: val });
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
      for (const c of queryAllDocs('.' + oddsClass)) {
        const t = c.closest('table'); if (t) tables.add(t);
      }
    }
    // .odds_table 또는 (범용) 모든 표 — [프레임 대응] 동일출처 iframe(예: frm_race_run) 내부 표까지 스캔
    for (const t of queryAllDocs('table.odds_table, table')) tables.add(t);

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

    // [경주 혼입 방지 (2026-07-19)] 부산 3경주 복기: 분석기 지정 경주(override='부산 3경주')와
    //   배당판 실제 표시 경주(모리오카 3경주)가 달랐는데 override 를 무조건 우선해 모리오카 배당이
    //   '부산 3경주' 이름으로 저장·분석됨(추천 4+9 3.3배 = 모리오카 값). → 배당판에서 경주를 직접
    //   읽을 수 있고(detected) 지정 경주와 '경주장 or 경주번호'가 다르면 배당판 기준으로 저장.
    //   배당판에서 못 읽는 사이트(generic 등)는 기존대로 override 사용(기존 동작 유지·대조만 추가).
    const _detectedRk = extractRaceKey();
    let raceKey = (overrideRaceKey && overrideRaceKey.trim()) || _detectedRk;
    try {
      const _vn = (x) => {
        const m = /([가-힣一-龥ぁ-んァ-ヶA-Za-z]+)\s*(\d+)\s*(?:경주|R)/.exec(String(x || ''));
        return m ? (m[1] + '|' + m[2]) : null;
      };
      const _dv = _vn(_detectedRk), _ov = _vn(raceKey);
      if (_dv && _ov && _dv !== _ov) {
        console.log('[혼입방지] 지정 경주(' + raceKey + ') ≠ 배당판 경주(' + _detectedRk + ') → 배당판 기준으로 저장');
        raceKey = _detectedRk;
      }
    } catch (_) { /* 대조 실패 시 기존 동작 */ }

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
  //   [1번] asyukk34 · [2번] keiba RaceResult 둘 다 감지.
  const RANK_HDR = /^(着順|착순|순위|등위|순위착순|rank)$/i;
  const RNO_HDR = /^(馬番|마번|번호|말번호|말번|no\.?)$/i;
  const RNAME_HDR = /(馬名|마명|말명|말이름|horse)/i;
  function isResultPage() {
    if (/result|racemarktable|raceresult/i.test(location.href)) return true;
    return [...document.querySelectorAll('table')].some((t) => {
      const h = [...(t.querySelector('tr')?.querySelectorAll('th,td') || [])].map((c) => txt(c).replace(/\s+/g, ''));
      // keiba(着順+馬番) 또는 사설(착순/순위 + 마번/번호)
      return (h.includes('着順') && h.some((x) => /馬番/.test(x)))
        || (h.some((x) => RANK_HDR.test(x)) && h.some((x) => RNO_HDR.test(x)));
    });
  }

  function extractResults() {
    for (const table of document.querySelectorAll('table')) {
      const trs = [...table.querySelectorAll('tr')];
      if (trs.length < 2) continue;
      const head = [...trs[0].querySelectorAll('th,td')].map((c) => txt(c));
      const hn = head.map((h) => h.replace(/\s+/g, ''));   // 공백 제거 후 라벨 매칭(한/일 공통)
      const iRank = hn.findIndex((h) => RANK_HDR.test(h));
      const iNo = hn.findIndex((h) => RNO_HDR.test(h));
      const iName = hn.findIndex((h) => RNAME_HDR.test(h));
      if (iRank === -1 || iNo === -1) continue;
      const results = [];
      for (const tr of trs.slice(1)) {
        const cells = [...tr.querySelectorAll('th,td')].map((c) => txt(c));
        const rank = toNum(cells[iRank]);
        const no = toNum(cells[iNo]);
        // 착순이 정수이고 마번이 유효할 때만 (失格/中止/除外/실격 등은 착순 숫자 없음 → 제외)
        if (!Number.isInteger(rank) || rank < 1 || !isHorseNo(no) || !/^\d+$/.test((cells[iRank] || '').trim())) continue;
        results.push({ rank, no, name: iName >= 0 ? cells[iName] : '' });
      }
      if (results.length) { results.sort((a, b) => a.rank - b.rank); return results; }
    }
    return [];
  }

  //   확정(정산) 배당 추출: 복승(2두)·삼복승(3두) 조합 + 배당.
  //   keiba 払戻金 표(馬連/3連複 + 組番 + 払戻金) 및 사설 확정배당 표를 텍스트로 스캔.
  //   배당 = 100원(엔)당 払戻金 / 100. 못 찾으면 null.
  function extractResultOdds() {
    const KQ = /(馬連|복승|複勝連|우마렌)/, KT = /(3連複|３連複|삼복승|三連複|산렌푸쿠)/;
    const res = { quinella: null, trio: null, raw: [] };
    for (const tr of document.querySelectorAll('tr, li, dl, .result, [class*=payout], [class*=haraimodoshi]')) {
      const cells = [...tr.querySelectorAll('th,td,dt,dd,span')].map((c) => txt(c));
      const line = (cells.length ? cells.join(' ') : txt(tr));
      const combo = (line.match(/\b\d{1,2}(?:\s*[-－ー]\s*\d{1,2}){1,2}\b/) || [])[0];
      const money = (line.match(/([\d,]{2,})\s*(?:円|원|엔)/) || [])[1];
      if (!combo || !money) continue;
      const nums = combo.split(/[-－ー]/).map((s) => parseInt(s.trim(), 10)).filter((n) => n >= 1 && n <= 20);
      const odds = Math.round((parseInt(money.replace(/,/g, ''), 10) / 100) * 10) / 10;
      if (!nums.length || !(odds > 0)) continue;
      const entry = { combo: nums, odds };
      res.raw.push(entry);
      if (KQ.test(line) && nums.length === 2 && !res.quinella) res.quinella = entry;
      if (KT.test(line) && nums.length === 3 && !res.trio) res.trio = entry;
    }
    return res;
  }

  // [2번] 경주결과 탭 자동 클릭 → 1.5초 대기 → 착순·확정배당 추출·전송
  //   결과 미확정이면 {ok:false, notReady:true} 로 반환 → 타이머가 재시도.
  async function collectResultsByTab(reason) {
    if (!isResultPage()) {
      console.log('[결과수집] 경주결과 탭 클릭 시도... (labels=경주결과/결과/성적/払戻/成績)');
      const r = await clickTabAndWait(['경주결과', '결과', '성적', '払戻', '成績', 'レース結果'],
        oddsSignature(), '경주결과', false);
      if (r.clicked) await wait(1500);
    }
    const results = extractResults();
    if (!results.length) {
      console.warn('[결과수집] 아직 착순 없음(경주 미종료/미확정) → 재시도 대기');
      return { ok: false, notReady: true, error: '결과 미확정' };
    }
    return sendResults(reason);
  }

  async function sendResults(reason) {
    const results = extractResults();
    if (!results.length) {
      // [진단] 사설 결과 페이지 구조 확인용 — 사용자가 F12 로 공유 가능
      const hint = document.querySelectorAll('.result, .chakujun, [class*=result], [class*=chaku]').length;
      console.warn(`[결과수집] ❌ 착순 테이블을 못 찾음. (result/chaku 관련 요소 ${hint}개, table ${document.querySelectorAll('table').length}개) — 경주결과 탭이 열렸는지 확인하세요.`);
      return { ok: false, error: '착순을 찾지 못했습니다. 결과(성적) 페이지인지 확인하세요.' };
    }
    const finalOdds = extractResultOdds();
    const { raceKey: override } = await getSettings();
    const raceKey = (override && override.trim()) || extractRaceKey();
    if (!raceKey) return { ok: false, error: 'raceKey를 만들 수 없습니다. 팝업에서 직접 입력하세요.' };
    console.log(`[결과수집] ✅ 1~3착: ${results.filter((r) => r.rank <= 3).map((r) => `${r.rank}착 ${r.no}번`).join(', ')}`
      + ` | 확정배당 복승: ${finalOdds.quinella ? finalOdds.quinella.combo.join('-') + '=' + finalOdds.quinella.odds + '배' : '미검출'}`
      + `, 삼복승: ${finalOdds.trio ? finalOdds.trio.combo.join('-') + '=' + finalOdds.trio.odds + '배' : '미검출'}`);
    const res = await chrome.runtime.sendMessage({
      type: 'POST_RESULTS', reason,
      payload: { raceKey, results, finalOdds, source: location.href },
    });
    return res || { ok: false, error: 'background 응답 없음' };
  }

  // ══════════════════════════════════════════════════════════════════
  //  [v2.0.1] 경주결과 자동수집 — 사설 배당판 /bet/result 표를 fetch → 파싱
  //  ------------------------------------------------------------------
  //   결과가 iframe(/bet/result?id=..)로 들어오는 사이트 대응. 현재 로그인 세션
  //   쿠키로 동일출처 fetch → 경주지역/라운드/1~3착/복승/삼복승 파싱 →
  //   설정 raceKey(예 '나고야 3경주')와 지역+라운드로 매칭 → /api/results/auto 전송.
  // ══════════════════════════════════════════════════════════════════
  //  [1번] 결과 페이지 URL 감지 — 사이트가 결과를 <iframe id="video_iframe" src="/bet/result?id=N">
  //   로 싣는 구조에 대응. 우선순위: 현재URL → video_iframe → 모든 iframe/frame src(교차출처도
  //   getAttribute 로 읽음) → 링크 → 페이지 HTML 정규식 폴백. 진단 로그 포함.
  const RESULT_RE = /\/bet\/result(?:\?[^"'\s<>]*)?/i;
  function _abs(u) { try { return new URL(u, location.href).href; } catch (_) { return u; } }
  function findResultUrl() {
    if (RESULT_RE.test(location.href)) return location.href;
    // (a) 사이트가 지정한 결과 iframe(video_iframe) 최우선
    try {
      const vf = document.getElementById('video_iframe');
      if (vf) {
        const s = vf.src || vf.getAttribute('src') || (vf.dataset && vf.dataset.src) || '';
        console.log('[결과수집] video_iframe src =', JSON.stringify(s));
        if (RESULT_RE.test(s)) return _abs(s.match(RESULT_RE)[0]);
        try { const h = vf.contentWindow && vf.contentWindow.location && vf.contentWindow.location.href;
          if (h && RESULT_RE.test(h)) return _abs(h); } catch (_) { /* 교차출처 */ }
      }
    } catch (_) { /* */ }
    // (b) 모든 iframe/frame 의 src(교차출처 프레임도 속성값은 부모에서 읽힘)
    let allFrames = [];
    try { allFrames = [...document.querySelectorAll('iframe, frame')]; } catch (_) { allFrames = []; }
    for (const d of sameOriginDocs()) {
      try { allFrames.push(...d.querySelectorAll('iframe, frame')); } catch (_) { /* */ }
    }
    for (const f of allFrames) {
      const s = f.src || (f.getAttribute && f.getAttribute('src')) || (f.dataset && f.dataset.src) || '';
      if (RESULT_RE.test(s)) return _abs(s.match(RESULT_RE)[0]);
    }
    // (c) 결과 링크
    for (const a of queryAllDocs('a[href]')) {
      const h = a.getAttribute('href') || '';
      if (RESULT_RE.test(h)) return _abs(h.match(RESULT_RE)[0]);
    }
    // (d) 폴백: 페이지 HTML 어디든 /bet/result?id=N 이 있으면 사용(동적 삽입 대응)
    try {
      const html = document.documentElement ? document.documentElement.innerHTML : '';
      const m = html.match(/\/bet\/result\?id=\d+/i) || html.match(RESULT_RE);
      if (m) { console.log('[결과수집] HTML 폴백으로 결과 URL 감지:', m[0]); return _abs(m[0]); }
    } catch (_) { /* */ }
    console.warn('[결과수집] ❌ /bet/result URL을 찾지 못함 (video_iframe·iframe·링크·HTML 모두 실패)');
    return null;
  }

  // 전각숫자(０-９)·전각콜론 → 반각 정규화(중앙 JRA 결과표 대응)
  const fw2ascii = (s) => String(s == null ? '' : s)
    .replace(/[０-９]/g, (d) => String.fromCharCode(d.charCodeAt(0) - 0xfee0))
    .replace(/：/g, ':');

  function _parseResultDoc(doc) {
    try {
      const rows = [...doc.querySelectorAll('table tr')];
      const norm = (r) => fw2ascii(r.innerText || r.textContent || '').replace(/\s+/g, '');
      // 헤더 후보: 지역/경마장 + 라운드/경주/着 계열이 함께 있는 행 → 실패 시 완화 매칭
      const headerRow =
        rows.find((r) => /경주지역|경마장|開催|レース場/.test(norm(r)) && /라운드|회차|경주|着|着順|レース番号/.test(norm(r)))
        || rows.find((r) => /경주지역|라운드|着順|レース/.test(norm(r)));
      if (!headerRow) return [];
      const heads = [...headerRow.querySelectorAll('th,td')].map((c) => fw2ascii(c.textContent || '').replace(/\s+/g, ''));
      const idx = (re) => heads.findIndex((h) => re.test(h));
      const iArea = idx(/경주지역|경마장|지역|開催|レース場|競馬場/);
      const iRound = idx(/라운드|회차|경주(?!지역)|^R$|^경주번호|レース番号|^レース$/);   // '경주지역'(지역열)과 혼동 방지
      // 착순 컬럼: 한국(1착/1위/1등) + 중앙 일본(1着/１着) 모두 대응
      const i1 = idx(/1착|1위|1등|1着/), i2 = idx(/2착|2위|2着/), i3 = idx(/3착|3위|3着/);
      const iQ = idx(/복승|複勝/), iT = idx(/삼복승|삼복|三連複|3連複/);
      // 착순 컬럼을 하나도 못 찾으면 이 표는 결과표가 아님 → 빈 배열
      if (i1 < 0 && i2 < 0 && i3 < 0) return [];
      const out = [];
      for (const r of rows.slice(rows.indexOf(headerRow) + 1)) {
        try {
          const cells = [...r.querySelectorAll('th,td')].map((c) => fw2ascii(c.textContent || '').trim());
          if (cells.length < 3) continue;
          const area = iArea >= 0 ? (cells[iArea] || '') : '';
          const round = iRound >= 0 ? (cells[iRound] || '') : '';
          if (!area && !round) continue;
          out.push({
            area, round,
            no1: toNum(cells[i1]), no2: toNum(cells[i2]), no3: toNum(cells[i3]),
            qOdds: iQ >= 0 ? toNum(cells[iQ]) : null, tOdds: iT >= 0 ? toNum(cells[iT]) : null,
          });
        } catch (rowErr) { /* 개별 행 파싱 실패는 건너뜀 */ }
      }
      return out;
    } catch (e) {
      console.warn('[결과수집] _parseResultDoc 예외 → [] 반환(폴백):', e && e.message);
      return [];
    }
  }

  // raceKey('2026-07-03 나고야 3경주') ↔ 결과행(지역='나고야', 라운드='3') 매칭
  function _matchResultRow(rows, raceKey) {
    const rk = (raceKey || '').replace(/\d{4}-\d{2}-\d{2}/, '').trim();
    const area = (rk.match(/[가-힣]{2,}/) || [])[0] || '';
    const no = parseInt((rk.match(/\d{1,2}/) || [])[0] || '', 10);
    if (!area || !no) return null;
    return rows.find((r) => {
      const a = (r.area || '').replace(/\s/g, '');
      const rn = parseInt((String(r.round).match(/\d{1,2}/) || [])[0] || '', 10);
      return rn === no && !!a && (a.includes(area) || area.includes(a));
    }) || null;
  }

  async function collectResultsByFetch(reason) {
    const { raceKey: override } = await getSettings();
    const raceKey = (override && override.trim()) || extractRaceKey();
    if (!raceKey) return { ok: false, error: 'raceKey 없음(팝업에서 입력)' };
    const url = findResultUrl();
    if (!url) return { ok: false, notReady: true, error: '/bet/result 페이지(결과 iframe)를 찾지 못함' };
    let html;
    try { html = await (await fetch(url, { credentials: 'include' })).text(); }
    catch (e) { return { ok: false, error: 'result fetch 실패: ' + (e.message || e) }; }
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const rows = _parseResultDoc(doc);
    if (!rows.length) return { ok: false, notReady: true, error: '결과표 파싱 실패(미확정 가능)' };
    const row = _matchResultRow(rows, raceKey);
    if (!row) return { ok: false, notReady: true, error: `'${raceKey}' 매칭 행 없음(${rows.length}행)` };
    const results = [];
    if (isHorseNo(row.no1)) results.push({ rank: 1, no: row.no1 });
    if (isHorseNo(row.no2)) results.push({ rank: 2, no: row.no2 });
    if (isHorseNo(row.no3)) results.push({ rank: 3, no: row.no3 });
    if (!results.length) return { ok: false, notReady: true, error: '착순 마번 없음(미확정)' };
    const finalOdds = {};
    if (row.qOdds != null && row.qOdds > 0) finalOdds.quinella = { combo: [row.no1, row.no2].filter(isHorseNo), odds: row.qOdds };
    if (row.tOdds != null && row.tOdds > 0) finalOdds.trio = { combo: [row.no1, row.no2, row.no3].filter(isHorseNo), odds: row.tOdds };
    console.log(`[결과fetch] ${raceKey} → ${results.map((r) => r.rank + '착 ' + r.no).join(' / ')}`
      + (finalOdds.quinella ? ` · 복승 ${finalOdds.quinella.odds}` : ''));
    const res = await chrome.runtime.sendMessage({
      type: 'POST_RESULTS', reason: reason || 'auto-result',
      payload: { raceKey, results, finalOdds, source: url },
    });
    if (res && res.ok) {
      const d = res.data || {};
      // [스펙5] finalOdds(확정 복승/삼복승 배당)도 함께 반환 → background 알림에 "복승 7+4: 12.3배" 표시
      return { ok: true, raceKey, top3: d.top3 || results.map((r) => r.no), hit: d.hit || null, finalOdds };
    }
    return { ok: false, error: (res && res.error) || '서버 전송 실패' };
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

  // [한국모드 강화] raceKey 에 KRA 경마장명(서울/부산/제주/과천, 부경/부산경남 변형 + 렛츠런/마사회/KRA)이 있으면
  //   market 토글과 무관하게 무조건 한국경마로 판단 → 복승만 수집 · 쌍승/삼복승 탭 클릭 완전 차단.
  const KRA_TRACK_RE = /(서울|부산경남|부경|부산|제주|과천|렛츠런|렛츠런파크|한국마사회|경마공원|KRA)/;
  function isKoreaByRaceKey(raceKey) {
    return !!(raceKey && KRA_TRACK_RE.test(String(raceKey)));
  }
  // [한국모드 강화2] raceKey 추출이 실패해도(사설 보드 표기 다양) 페이지 본문/URL 에
  //   KRA 경마장명 + 경마 맥락이 있으면 한국으로 확정 → 쌍승/삼복승 클릭 완전 차단.
  function pageLooksKorean() {
    try {
      if (KRA_TRACK_RE.test(location.href)) return true;
      const body = (document.body && document.body.innerText) || '';
      if (!KRA_TRACK_RE.test(body)) return false;
      return /(경주|경마|복승|배당|발주|출주|마번)/.test(body);   // 경마장명 + 경마 맥락 동시 존재 시만
    } catch (_) { return false; }
  }
  // [한국모드 강화3·사용자요청 2번] 배당판 마권종류 탭(bet_type)에 '복승'은 있고 '쌍승/삼복승'이 전혀 없으면
  //   → 한국경마로 확정(한국은 복승만 발매·일본은 쌍승/삼복승 탭 존재). raceKey에 경마장명이 없어도 감지.
  function koreanByBetTabs() {
    try {
      const btns = Array.prototype.slice.call(
        document.querySelectorAll('.bet_type_btn, span[bet_mode], [class*="bet_type"], .odds_menu a, .odds_menu span'));
      if (!btns.length) return false;                       // 탭 UI 미로드 → 판단 보류(오탐 방지)
      const txt = btns.map((e) => (e.textContent || '')).join(' ');
      const hasBok = /복승|복연/.test(txt);
      const hasJp = /쌍승|마단|쌍승식|삼복승|삼복|삼연복|馬単|三連複|３連複|3連複|三連/.test(txt);
      return hasBok && !hasJp;                               // 복승 탭만 있고 쌍승/삼복승 탭 없음 → 한국경마
    } catch (_) { return false; }
  }
  // [한국모드 최종 판정·사용자요청 1~4번] 종목=한국(팝업) OR raceKey(감지·수동입력 둘 다) KRA OR 페이지 KRA
  //   OR 배당판 탭이 복승만(쌍승 없음). override=팝업에 직접 입력한 raceKey(자동수집이 배당판 raceKey를
  //   우선해 '서울'이 누락돼도 사용자가 입력한 '서울 N경주'로 한국 확정 — 자동 감지 실패 근본 해결).
  function isKoreaMode(raceKey, market, override) {
    return market === 'korea'
      || isKoreaByRaceKey(raceKey) || isKoreaByRaceKey(override)
      || pageLooksKorean() || koreanByBetTabs();
  }

  // [1번] 일본 경마장(지방 NAR + 중앙 JRA) — 한글·한자 병기. raceKey/페이지에 이 이름이 있으면 '경마' 확정.
  //   사설 배당판(asyukk) 네비 메뉴에 '경정/경륜' 링크가 있어 경마 경주도 경정으로 오탐되던 문제 차단.
  const HORSE_TRACKS = /(帯広|門別|盛岡|水沢|浦和|船橋|大井|川崎|金沢|笠松|名古屋|園田|姫路|高知|佐賀|札幌|函館|福島|新潟|東京|中山|中京|京都|阪神|小倉|오비히로|반에이|몬베츠|몬베쓰|모리오카|미즈사와|우라와|후나바시|오오이|오이|카와사키|가와사키|카나자와|가나자와|카사마츠|나고야|소노다|히메지|고치|사가|삿포로|하코다테|히코다테|후쿠시마|후크시마|니가타|도쿄|나카야마|주쿄|교토|한신|고쿠라)/;

  // [수정#3/탭분리] 종목 자동 감지 — asyukk34 사설 배당판의 탭/본문 텍스트로 종목을 구분.
  //   팝업 종목이 '경마'인데 페이지가 경륜/경정/바이크로 보이면 이 감지값을 사용(수동 선택이 우선).
  //   반환: 'boat'(경정) | 'cycle'(경륜) | 'bike'(바이크) | null(경마/불명).
  //   [1번 수정] ①raceKey/페이지에 경마장명 있으면 무조건 경마(null) ②경정/경륜은 URL·제목에 명시적일 때만
  //   확정(네비 메뉴 단어 오탐 방지) → 본문은 명확한 종목어(競艇/競輪 등)만 보조 판정.
  function detectSport(raceKey) {
    try {
      const rk = raceKey || '';
      // [수정1·한국 강제] 한국 경마장 raceKey/URL = 무조건 경마(null) → 경정/경륜 오탐 차단.
      if (isKoreaByRaceKey(rk) || KRA_TRACK_RE.test(location.href)) return null;
      // [1번·핵심] raceKey에 일본 경마장명이 있으면 무조건 경마 → 경정/경륜 오탐 차단
      if (HORSE_TRACKS.test(rk)) return null;
      const href = location.href, title = (document.title || '');
      const strong = (re) => re.test(href) || re.test(title);   // URL·제목 = 명시적 신호(네비 메뉴 아님)
      // [경정/경륜 강화] URL 또는 탭 제목에 명시적으로 있을 때만 확정
      if (strong(/(경정|競艇)/)) return 'boat';
      if (strong(/(오토레이스|オートレース|autorace|바이크경주)/)) return 'bike';
      if (strong(/(경륜|競輪)/)) return 'cycle';
      // [배당판 탭 텍스트 확인] 본문 보조 판정 — 경마장명이 본문에 있으면 경마(경정/경륜 페이지에 경마장 링크 오탐 방지)
      const body = ((document.body && document.body.innerText) || '');
      // [이중 방어 2026-07-19·ks1 경륜 오탐 수정] 일본어 전용 경륜 마커(けいりん·競輪)를 경마장명 본문
      //   체크보다 먼저 확인 — 새 ks1 배당판은 본문에 타 종목 메뉴·배너의 경마장명이 섞여 경륜 경주
      //   (기후 2경주)가 경마로 오탐되던 문제. ⚠ 한국어 '경륜/경정'은 ks1 메뉴 라벨(일본경륜·일본경정)에
      //   항상 존재해 선행 체크에 쓰면 전 종목 오탐 → 현재 경주 패널에만 나타나는 일본어 표기만 선행.
      //   (기존 체크는 순서·내용 그대로 유지 — 이 한 줄만 추가. 서버 [종목 정정] horse 포함과 이중 방어.)
      if (/(けいりん|競輪)/.test(body)) return 'cycle';
      if (HORSE_TRACKS.test(body)) return null;
      if (/(競艇|경정|경정장|모터보트|미사리|ボートレース|보트레이스)/.test(body)) return 'boat';
      if (/(オートレース|오토레이스|오토레이스장)/.test(body)) return 'bike';
      if (/(競輪|경륜|경륜장|벨로드롬|광명돔|けいりん)/.test(body)) return 'cycle';
    } catch (_) { /* */ }
    return null;
  }
  // [종목 자동감지 강화] 팝업(수동) 선택 + 페이지 강한 신호를 종합해 '항상 정확한' sport를 결정.
  //   우선순위: ①raceKey에 경마장명 → 무조건 경마(팝업이 경륜/경정이어도 정정) ②URL/제목의 명시 종목어(競輪/競艇/
  //   오토) → 그 종목으로 정정(팝업 stale 방지) ③강한 신호 없으면 팝업 수동선택 존중 ④그것도 없으면 본문 보조감지.
  //   → 사이트 전환 시 팝업에 남은 이전 종목 태그가 그대로 전송되던 문제(경마↔경륜) 차단.
  function resolveSport(popupSport, raceKey) {
    try {
      const rk = raceKey || '';
      // [수정1·한국 강제] 한국 경마장(부산/서울/제주 등) raceKey = 무조건 경마(horse) → 경정/경륜 오탐 완전 차단.
      if (isKoreaByRaceKey(rk) || (KRA_TRACK_RE.test(location.href))) return 'horse';
      // ① 경마장명 raceKey = 명백한 경마(가장 강한 신호) → 팝업 경륜/경정 정정
      if (HORSE_TRACKS.test(rk)) return 'horse';
      const href = location.href, title = (document.title || '');
      const strong = (re) => re.test(href) || re.test(title);
      // ② URL/제목의 명시적 종목어 = 강한 신호 → 팝업 stale 정정
      if (strong(/(경정|競艇)/)) return 'boat';
      if (strong(/(오토레이스|オートレース|autorace)/)) return 'bike';
      if (strong(/(경륜|競輪)/)) return 'cycle';
      // ③ 강한 신호 없음 → 팝업 수동선택(경마 아님) 존중
      if (popupSport && popupSport !== 'horse') return popupSport;
      // ④ 팝업이 경마/미설정 → 본문 보조 감지값(없으면 경마)
      return detectSport(rk) || 'horse';
    } catch (_) {
      return (popupSport && popupSport !== 'horse') ? popupSport : (detectSport(raceKey) || 'horse');
    }
  }
  // [탭분리] 일본 중앙경마(JRA) 힌트 — 페이지 본문/URL 로 중앙 여부 추정(팝업 japanType 이 우선).
  function detectCentralHint() {
    try {
      const body = (((document.body && document.body.innerText) || '') + ' ' + location.href);
      return /(중앙경마|中央競馬|JRA|jra\.go\.jp)/.test(body);
    } catch (_) { return false; }
  }
  const SPORT_LABEL = { horse: '경마', cycle: '경륜', boat: '경정', bike: '바이크' };
  // [탭분리] 종목 카테고리(분석기 탭과 1:1): korea|japan_local|japan_central|boat|cycle|bike.
  const CATEGORY_LABEL = {
    korea: '한국경마', japan_local: '일본 지방경마', japan_central: '일본 중앙경마',
    boat: '일본 경정', cycle: '일본 경륜', bike: '일본 바이크',
  };
  function computeCategory(effSport, isKorea, isCentral) {
    // [종목 오분석 근본수정·수정1] 한국경마(부산/서울/제주 등)는 무조건 korea 우선.
    //   기존엔 effSport==='boat'/'cycle' 을 먼저 반환해, asyukk 페이지의 경정/경륜 네비 링크가 종목감지를
    //   boat 로 켜면 한국 부산 경주가 경정으로 샜다(부산 2경주 6+1 오분석 근본원인). isKorea 를 최우선 판정.
    if (isKorea) return 'korea';
    if (effSport === 'boat') return 'boat';
    if (effSport === 'cycle') return 'cycle';
    if (effSport === 'bike') return 'bike';
    return isCentral ? 'japan_central' : 'japan_local';
  }

  // [자동전송 역할 재정의] NAR 지방경마(japan_local)·경륜(cycle)은 서버가 oddspark로 직접 자동수집 중 →
  //   확장 자동전송은 중복·꼬임(다른 경주 덮어씌움·유령마번) 유발이라 auto 경로에서 아예 차단(서버 전담).
  //   JRA 중앙경마·한국경마(KRA 라이브)·경정·바이크·결과 자동수집은 서버 직접수집이 없어 확장 유지.
  async function _currentCategory() {
    try {
      let rk = ''; try { rk = extractRaceKey() || ''; } catch (_) { rk = ''; }
      const { sport: popupSport, market, japanType, raceKey: ov } = await getSettings();
      const eff = resolveSport(popupSport, rk || ov);
      const isKorea = isKoreaMode(rk || ov, market, ov);
      const isCentral = (japanType === 'central') || detectCentralHint();
      return computeCategory(eff, isKorea, isCentral);
    } catch (_) { return ''; }
  }
  function _isServerCollectedCat(cat) { return cat === 'japan_local' || cat === 'cycle'; }
  // 오버레이/팝업이 읽어 수집 모드 배지 표시("🔄 서버 자동수집 중" vs "📡 확장 수집 중")
  function _markCollectMode(cat) {
    try { chrome.storage.local.set({ collectMode: { serverCollected: _isServerCollectedCat(cat), category: cat || '', at: Date.now() } }); } catch (_) { /* */ }
  }

  // [경주 자동추종] 자동 수집(auto·race-change·bg)은 '지금 배당판에 표시된 경주'(extractRaceKey)를
  //   우선한다 — 저장된 raceKey(override)가 이전 경주로 굳어, 배당판이 다음 경주로 넘어가도 자동
  //   엔진이 계속 이전 경주를 수집하던 버그를 막는다(수동 버튼만 되던 증상의 원인).
  //   수동 수집('manual')·그 외는 기존대로 저장값(override)을 우선해 명시 입력을 존중한다.
  //   두 경우 모두 한쪽이 비면 다른 쪽으로 폴백한다(자동 감지 실패 시 수동값 사용).
  function _resolveRaceKey(reason, override) {
    let detected = '';
    try { detected = extractRaceKey() || ''; } catch (_) { detected = ''; }
    const ov = (override && override.trim()) || '';
    const followBoard = (reason === 'auto' || reason === 'race-change' || reason === 'bg' || reason === 'sport-change'
                         || reason === 'korea-auto' || reason === 'auto-fallback');
    return followBoard ? (detected || ov) : (ov || detected);
  }

  async function collectTripleKeiba(reason) {
    if (!isKeibaOddsPage()) {
      setTripleProgress('❌ 배당판 페이지 아님', true);
      return { ok: false, error: 'keiba.go.jp 배당판(오즈) 페이지가 맞는지 확인하세요 — 경주 날짜·번호가 URL에 있어야 합니다.' };
    }
    const sp = new URLSearchParams(location.search);
    const q = ['k_raceDate', 'k_babaCode', 'k_raceNo']
      .filter((k) => sp.get(k)).map((k) => `${k}=${encodeURIComponent(sp.get(k))}`).join('&');
    const { raceKey: override, market, japanType } = await getSettings();
    const raceKey = _resolveRaceKey(reason, override);   // [경주 자동추종] 자동수집은 배당판 표시 경주 우선
    // [2번][한국모드 강화] 종목=한국 이거나 raceKey(감지·수동입력)/페이지/탭에서 KRA 감지 시 → 복승만(쌍승·삼복승 완전 제외).
    const isKorea = isKoreaMode(raceKey, market, override);
    if (isKorea && market !== 'korea') console.log('[한국모드] KRA 경마장 감지(raceKey/페이지) → 복승만 수집:', raceKey || '(raceKey 미상)');
    // [탭분리] keiba 는 경마 전용 → 한국/중앙/지방 카테고리 산출(팝업 japanType 우선).
    const kCategory = isKorea ? 'korea' : ((japanType === 'central' || detectCentralHint()) ? 'japan_central' : 'japan_local');
    try { chrome.storage.local.set({ detectedCategory: kCategory, detectedSport: 'horse', detectedAt: Date.now() }); } catch (_) { /* */ }
    const clean = (arr, cap) => arr
      .filter((c) => c.odds > 0)
      .map((c) => ({ combo: c.combo, odds: Math.round(c.odds * 10) / 10 }))
      .sort((a, b) => a.odds - b.odds)
      .slice(0, cap);
    const payload = { raceKey, quinella: [], exacta: [], trio: [], sport: 'horse', category: kCategory, capturedAt: new Date().toISOString(), source: location.href };
    // [탭분리] 중앙경마(JRA)는 복승+쌍승만(배당 전용, 삼복승·단승·전적 제외). 지방은 3종+단승+전적.
    const isCentralK = kCategory === 'japan_central';
    // [2번] 한국모드=복승만. [수정#1 삼복승 복구] 지방=복승+쌍승+삼복승 3종(keiba는 Odds3LenFuku fetch로 안정 수집).
    const steps = isKorea
      ? TRIPLE_STEPS.filter((s) => s.key === 'quinella')
      : isCentralK
        ? TRIPLE_STEPS.filter((s) => s.key !== 'trio')   // 중앙: 복승+쌍승(삼복승 제외)
        : TRIPLE_STEPS;   // 지방: 복승+쌍승+삼복승 3종
    if (isKorea) console.log('[한국경마] 복승만 수집 (쌍승/삼복승 미지원)');
    else if (isCentralK) console.log('[중앙경마] 복승+쌍승만 수집(삼복승·단승·전적 제외·배당 전용)');
    else console.log('[삼복승 복구] 일본 지방경마 복승+쌍승+삼복승 3종 수집');
    try {
      for (const st of steps) {
        setTripleProgress(`${st.label} 수집중…`);        // "복승 수집중…" → "쌍승 수집중…" → …
        const doc = await fetchOddsDoc(st.oper, q);
        payload[st.key] = clean(parseRankingCombos(doc).filter((c) => c.combo.length === st.len), st.cap);
      }
      // [2번] 단승(単勝) 배당 수집 — 단승 급락 = 가장 강한 신호. 한국·중앙 제외(복승 중심/배당 전용).
      //   ① 単勝複勝 표를 fetch(여러 oper 후보 시도) → ② 실패 시 현재 화면 DOM 폴백. 실패해도 무시(무해).
      if (!isKorea && !isCentralK) {
        try {
          let winHorses = [], allHorses = [];
          for (const oper of ['OddsTanFuku', 'OddsTanpuku', 'OddsTan']) {
            try {
              const wd = await fetchOddsDoc(oper, q);
              allHorses = extractHorses(wd);
              winHorses = allHorses.filter((h) => h.win != null && h.win >= 1.0);
              if (winHorses.length) break;
            } catch (_) { /* 다음 후보 */ }
          }
          if (!winHorses.length) { allHorses = extractHorses(); winHorses = allHorses.filter((h) => h.win != null && h.win >= 1.0); }
          if (winHorses.length) {
            const win = {};
            for (const h of winHorses) win[String(h.no)] = h.win;
            payload.win = win;   // 서버 triple_ingest 가 단승 시계열로 저장 → 단승급락 감지
            console.log('[단승수집] 단승 배당', Object.keys(win).length, '두');
          }
          // [출전취소·除外] 배당판에서 競走除外 감지된 마번 → payload.scratched(서버가 추천 전체에서 제거)
          const scr = allHorses.filter((h) => h.scratched).map((h) => h.no);
          if (scr.length) { payload.scratched = scr; console.log('[출전취소] 除外 마번', scr); }
        } catch (e) { console.warn('[단승수집] 실패(무시)', e); }
      }
      if (!payload.quinella.length && !payload.exacta.length && !payload.trio.length) {
        setTripleProgress('❌ 3종 배당 없음(발매 시간 확인)', true);
        return { ok: false, error: '3종 배당을 찾지 못했습니다(발매 시간/경주 확인).' };
      }
      setTripleProgress('서버 전송중…');
      const res = await chrome.runtime.sendMessage({ type: 'POST_TRIPLE', payload, reason });
      // 4) 출마표2 전적: 동일출처 DebaTable fetch → 추출 → 통합분석(POST_JAPAN). [탭분리] 중앙경마는 전적표 없음 → 생략.
      let starters = [];
      if (!isCentralK && !isKorea) {
        try {
          setTripleProgress('출마표2 전적 수집중…');
          starters = await fetchDebaStarters({ k_raceDate: sp.get('k_raceDate'), k_raceNo: sp.get('k_raceNo'), k_babaCode: sp.get('k_babaCode') });
        } catch (e) { console.warn('[전적수집] DebaTable 오류', e); }
      } else {
        console.log(`[전적수집] ${isKorea ? '한국경마' : '중앙경마'} → 출마표2(전적표) 생략`);
      }
      if (starters.length) {
        const { timerDeadline } = await getSettings();
        await chrome.runtime.sendMessage({ type: 'POST_JAPAN', reason, payload: { raceKey, horses: starters, deadline: timerDeadline || null, source: location.href } });
      }
      setTripleProgress(res && res.ok
        ? (isKorea
          ? `수집 완료 ✅ 복승 ${payload.quinella.length}·전적 ${starters.length}두`
          : `수집 완료 ✅ 복승 ${payload.quinella.length}·쌍승 ${payload.exacta.length}·전적 ${starters.length}두`)
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

  // [1번] 동일출처 iframe/frame 문서까지 포함해 스캔 (asyukk 배당판은 탭·표를
  //   프레임 내부에 그리는 경우가 많아, top document 만 보면 출마표2 탭·표를 못 찾음).
  //   교차출처 프레임은 contentDocument 접근이 막혀 자동 skip 된다.
  function sameOriginDocs() {
    const docs = [document];
    const seen = new Set([document]);
    const dig = (root) => {
      let frames = [];
      try { frames = [...root.querySelectorAll('iframe, frame')]; } catch (_) { return; }
      for (const f of frames) {
        let d = null;
        try { d = f.contentDocument || (f.contentWindow && f.contentWindow.document) || null; } catch (_) { d = null; }
        if (d && d.querySelectorAll && !seen.has(d)) { seen.add(d); docs.push(d); dig(d); }  // 중첩 프레임도 재귀
      }
    };
    dig(document);
    return docs;
  }
  // 모든 동일출처 문서에서 셀렉터 매칭 요소를 평탄하게 수집
  function queryAllDocs(sel) {
    const out = [];
    for (const d of sameOriginDocs()) { try { out.push(...d.querySelectorAll(sel)); } catch (_) { /* */ } }
    return out;
  }

  // [1번] 텍스트로 탭 버튼 찾기 (button/a/td/th/li/span/div, 보이는 것만 · iframe 포함)
  function findTabButton(labels) {
    const cands = queryAllDocs('button, a, td, th, li, span, div[role="tab"], .tab, .btn, [onclick]');
    const norm = (e) => (e.textContent || '').replace(/\s+/g, '').trim();
    const visible = (e) => e.offsetParent !== null || (e.getClientRects && e.getClientRects().length > 0);
    const clickScore = (e) => (/^(BUTTON|A)$/.test(e.tagName) || e.hasAttribute('onclick')
      || e.getAttribute('role') === 'tab' || /\b(tab|btn|nav)\b/i.test(e.className || '') ? 1 : 0);
    // 긴 라벨 먼저(‘출마표2’가 ‘출마표’보다 우선 매칭되도록)
    const ordered = [...labels].sort((a, b) => b.length - a.length);
    // 1) 정확히 일치 — 클릭가능 요소 우선
    for (const lb of ordered) {
      const hit = cands.filter((e) => norm(e) === lb && visible(e))
        .sort((a, b) => clickScore(b) - clickScore(a))[0];
      if (hit) return hit;
    }
    // 2) 포함(배지·공백·개수표시 허용) — 텍스트가 라벨과 가깝고 클릭가능한 것 우선
    for (const lb of ordered) {
      const hits = cands.filter((e) => { const t = norm(e); return t.includes(lb) && t.length <= lb.length + 10 && visible(e); });
      hits.sort((a, b) => (clickScore(b) - clickScore(a)) || (norm(a).length - norm(b).length));
      if (hits[0]) return hits[0];
    }
    // 3) [긴급2] 최후 폴백: 길이 제한 없이 라벨 포함(클릭가능·짧은 텍스트 우선).
    //    사설 배당판이 '쌍승식 배당표' 처럼 라벨을 길게 쓰는 경우까지 잡는다.
    for (const lb of ordered) {
      const hits = cands.filter((e) => norm(e).includes(lb) && visible(e));
      hits.sort((a, b) => (clickScore(b) - clickScore(a)) || (norm(a).length - norm(b).length));
      if (hits[0]) return hits[0];
    }
    return null;
  }

  // [삼복승 강화] asyukk34 사설 배당판 마권종류 탭(span.bet_type_btn)을 '정확 텍스트'로 클릭.
  //   DevTools 확인: <span class="bet_type_btn" bet_mode="11" combine_mode="triple">삼복승</span>
  //   '삼복승조합'(bet_mode=12)이 '삼복승'을 포함하므로, 반드시 정확 일치로 클릭해 혼동을 방지한다.
  function clickAsyukkBetTab(exactText) {
    try {
      const btns = queryAllDocs('.bet_type_btn');
      for (const b of btns) {
        const t = (b.textContent || '').replace(/\s+/g, '').trim();
        const vis = b.offsetParent !== null || (b.getClientRects && b.getClientRects().length > 0);
        if (t === exactText && vis) { try { b.click(); } catch (_) { /* */ } return b; }
      }
    } catch (_) { /* */ }
    return null;
  }

  // ═══ [보완3] asyukk 배당판 마권종류 탭 상태를 DOM 속성으로 '확정' ═══
  //   기존엔 활성 탭을 알 방법이 없어 oddsSignature() 변화(=화면이 바뀌었다)로만 추측했다.
  //   그 시그니처는 "무엇으로 바뀌었는지"는 모르므로, 잘못된 탭으로 바뀌어도 통과한다.
  //   → `.bet_type_btn` 의 텍스트/bet_mode/combine_mode + 활성표시 클래스를 직접 읽어 확정한다.
  //   ⚠ 활성표시 방식은 보드마다 다를 수 있어 '판정 불가(null)'를 명확히 구분한다 —
  //     불가일 때는 기존 동작(시그니처 기반)을 그대로 두고 절대 차단하지 않는다(오탐으로 정상수집 죽이지 않기).
  const BET_LABEL = { quinella: '복승', exacta: '쌍승', trio: '삼복승' };
  const _ACTIVE_RE = /(^|[\s_-])(on|active|selected|sel|current|cur|checked)([\s_-]|$)/i;

  function asyukkBetTabs() {
    const out = [];
    try {
      for (const b of queryAllDocs('.bet_type_btn')) {
        const text = (b.textContent || '').replace(/\s+/g, '').trim();
        if (!text) continue;
        const vis = b.offsetParent !== null || (b.getClientRects && b.getClientRects().length > 0);
        const cls = b.className || '';
        const aria = (b.getAttribute && (b.getAttribute('aria-selected') || b.getAttribute('data-selected'))) || '';
        out.push({
          el: b, text, visible: vis,
          betMode: (b.getAttribute && b.getAttribute('bet_mode')) || null,
          combineMode: (b.getAttribute && b.getAttribute('combine_mode')) || null,
          active: _ACTIVE_RE.test(String(cls)) || aria === 'true',
        });
      }
    } catch (_) { /* */ }
    return out;
  }

  // 현재 활성 마권종류 탭. 활성표시를 못 찾으면 null(=판정 불가).
  function activeAsyukkBetTab() {
    const tabs = asyukkBetTabs().filter((t) => t.visible);
    const act = tabs.filter((t) => t.active);
    return act.length === 1 ? act[0] : null;   // 0개(표시 없음)·2개+(모호) 모두 판정 불가
  }

  // 기대한 탭이 실제로 활성인지 확정 검증. true=일치 / false=불일치(오염 위험) / null=판정 불가.
  function verifyActiveBetTab(expectText) {
    if (detectSite() !== 'asyukk') return null;
    const a = activeAsyukkBetTab();
    if (!a) return null;
    const ok = a.text === expectText;
    if (!ok) {
      console.warn(`[탭확정] ⚠ 활성 탭 불일치 — 기대 "${expectText}" / 실제 "${a.text}" `
        + `(bet_mode=${a.betMode || '?'} · combine_mode=${a.combineMode || '?'})`);
    } else {
      console.log(`[탭확정] ✅ 활성 탭 = "${a.text}" (bet_mode=${a.betMode || '?'} · combine_mode=${a.combineMode || '?'})`);
    }
    return ok;
  }

  // [진단] 배당판 탭 구조를 1회 덤프 — 활성표시 클래스/bet_mode 실제값 파악용(사용자가 F12 로 공유 가능).
  let _betTabDumped = false;
  function dumpBetTabs() {
    if (_betTabDumped || detectSite() !== 'asyukk') return;
    _betTabDumped = true;
    const tabs = asyukkBetTabs();
    if (!tabs.length) { console.log('[탭확정·진단] .bet_type_btn 없음(이 보드는 탭확정 미지원 → 기존 시그니처 방식만 사용)'); return; }
    console.log('[탭확정·진단] 마권종류 탭 목록:', tabs.map((t) =>
      `"${t.text}"|bet_mode=${t.betMode || '-'}|combine_mode=${t.combineMode || '-'}|class="${t.el.className || '-'}"|active=${t.active}`));
    if (!tabs.some((t) => t.active)) {
      console.log('[탭확정·진단] ⚠ 활성표시 클래스를 못 찾았습니다 → 탭 확정 검증은 비활성(기존 방식 유지). '
        + '위 class 문자열을 공유해주시면 활성 판정 규칙을 추가할 수 있습니다.');
    }
  }

  // [보완1] 복승 탭으로 이동 — 사설(asyukk)은 `.bet_type_btn` 정확일치 우선.
  //   findTabButton 의 3단계 `includes()` 폴백은 '삼복승'/'삼복승조합'이 문자열 '복승'을 포함해 오매칭될 수 있다.
  //   기타 보드(keiba·generic)는 기존 clickTabAndWait 폴백을 그대로 유지(무삭제).
  async function gotoQuinellaTab(isKorea) {
    if (detectSite() === 'asyukk') {
      // [수정1·한국 배당판 고정] 이미 복승 탭이 활성이면 클릭하지 않는다 → 배당판이 바뀌지 않음(깜빡임·전환 제거).
      //   한국경마는 복승만 발매라 배당판이 항상 복승에 있어, 매 수집마다 복승 탭을 다시 누르던 것을 생략.
      //   verifyActiveBetTab 은 활성표시 확정 시에만 true → 확정 불가(null)면 기존대로 클릭(안전).
      if (verifyActiveBetTab('복승') === true) {
        console.log('[복승수집] 이미 복승 탭 활성 → 클릭 생략(배당판 고정)');
        return { clicked: true, changed: false, sig: oddsSignature() };
      }
      const before = oddsSignature();
      const el = clickAsyukkBetTab('복승');
      if (el) {
        await wait(2000);
        const sig = oddsSignature();
        console.log('[복승수집] .bet_type_btn "복승" 정확일치 클릭 ✅ (bet_mode=' + (el.getAttribute('bet_mode') || '?') + ')');
        return { clicked: true, changed: (sig !== before && sig.length > 0), sig };
      }
      console.warn('[복승수집] .bet_type_btn "복승" 정확일치 실패 → 일반 탭 탐색 폴백');
    }
    return clickTabAndWait(['복승', '복연', '馬連'], isKorea ? '' : oddsSignature(), '복승', !isKorea);
  }

  // [오염방지 4] 복승 탭 복귀 — 사설(asyukk)은 `.bet_type_btn` **정확 일치**로 클릭.
  //   findTabButton 의 3단계 `includes()` 폴백은 '삼복승'/'삼복승조합'이 문자열 '복승'을 포함해 오매칭될 수 있고,
  //   기존 복귀 호출은 requireChange=false 라 **어떤 탭이 눌렸든 성공 처리**됐다. 그 결과 보드가 삼복승에 머문 채
  //   다음 수집 사이클이 그 화면을 복승으로 긁어가는 경로가 있었다. → 사설 보드는 정확일치 우선, 기타 보드는 기존 폴백 유지.
  async function returnToQuinellaTab() {
    if (detectSite() === 'asyukk' && clickAsyukkBetTab('복승')) {
      await wait(2000);
      console.log('[배당수집] 복승(복귀) — .bet_type_btn "복승" 정확일치 클릭 ✅');
      return true;
    }
    const r = await clickTabAndWait(['복승', '복연', '馬連'], '', '복승(복귀)', false);   // 기타 보드 폴백(기존 동작 유지)
    return !!(r && r.clicked);
  }

  // 현재 표 상태의 시그니처(배당 셀 값) — 탭 전환 여부 감지용
  function oddsSignature() {
    const cells = [...document.querySelectorAll('.odds_content')].slice(0, 40)
      .map((c) => (c.textContent || '').trim());
    if (cells.length) return cells.join('|');
    return [...document.querySelectorAll('table.odds_table, table')]
      .map((t) => t.innerText).join(' ').replace(/\s+/g, ' ').slice(0, 400);
  }

  // [2번] 탭 클릭 → 대기 → 표 변경 확인(변화 없으면 재시도). waitMs=탭당 로딩 대기(기본 2초)
  async function clickTabAndWait(labels, prevSig, betLabel, requireChange, waitMs) {
    console.log(`[배당수집] ${betLabel} 탭 클릭 시도... (labels=${labels.join('/')})`);
    let el = findTabButton(labels);
    if (!el) {
      console.warn(`[배당수집] ⚠ ${betLabel} 탭 버튼을 찾지 못했습니다.`);
      return { clicked: false, changed: false, sig: oddsSignature() };
    }
    for (let attempt = 1; attempt <= 3; attempt++) {
      try { el.click(); } catch (e) { console.warn(`[배당수집] ${betLabel} 클릭 오류`, e); }
      // [긴급2] 탭 로딩 대기: 기본 2초, 재시도마다 1초씩 증가(느린 사설 배당판 대응)
      await wait((waitMs || 2000) + (attempt - 1) * 1000);
      const sig = oddsSignature();
      const changed = sig !== prevSig && sig.length > 0;
      if (!requireChange || changed) {
        console.log(`[배당수집] ${betLabel} 탭 활성화 ${changed ? '(데이터 변경 확인)' : '(현재 탭 유지)'}`);
        return { clicked: true, changed, sig };
      }
      console.warn(`[배당수집] ${betLabel} 데이터 변화 없음 → 재시도 ${attempt}/3 (버튼 재탐색)`);
      el = findTabButton(labels) || el;   // 매번 버튼을 다시 찾아 재클릭
    }
    console.warn(`[배당수집] ⚠ ${betLabel} 클릭했으나 표가 바뀌지 않음.`);
    return { clicked: true, changed: false, sig: oddsSignature() };
  }

  // [일본] 현재(단승 탭) 화면에서 마번별 단승 배당 추출 → {마번: 배당}
  function currentSingles(oddsClass) {
    const winMap = {};
    const tables = new Set();
    for (const c of document.querySelectorAll('.' + (oddsClass || 'odds_content'))) {
      const t = c.closest('table'); if (t) tables.add(t);
    }
    const scan = tables.size ? [...tables] : [...document.querySelectorAll('table.odds_table, table')];
    for (const t of scan) {
      const { singles } = parseMatrixTable(t, oddsClass ? { oddsClass } : {});
      for (const s of singles) {
        if (isHorseNo(s.no) && s.win != null && s.win >= 1.0 && winMap[s.no] == null) winMap[s.no] = s.win;
      }
    }
    // 폴백: keiba 単勝複勝 표(odd_popular_table_02) 구조
    if (!Object.keys(winMap).length) {
      for (const h of extractHorses()) if (h.win != null && h.win >= 1.0) winMap[h.no] = h.win;
    }
    return winMap;
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

  // [2번] 상단 마번(축마) 버튼 찾기.
  //   [긴급수정] ① <input type=button value="4"> 는 textContent 가 비어 못 찾던 버그
  //   → value 도 함께 매칭. ② 오즈 테이블 헤더 td/th 숫자와 혼동 방지를 위해
  //   '마번 버튼이 모여있는 컨테이너'를 먼저 찾고 그 안에서 숫자 버튼을 클릭.
  const AXIS_SEL = 'input[type=button],input[type=submit],input[type=radio],input[type=checkbox],'
    + 'button,a,[onclick],[role=button],.btn,.num,label,span,li,dd,td';
  function _btnLabel(e) {
    const raw = (e.tagName === 'INPUT') ? (e.value != null ? String(e.value) : '') : (e.textContent || '');
    return raw.replace(/\s+/g, '').trim();
  }
  function _btnVisible(e) { return e.offsetParent !== null || (e.getClientRects && e.getClientRects().length > 0); }
  function _btnClickable(e) {
    return /^(BUTTON|A|INPUT|LABEL)$/.test(e.tagName) || e.hasAttribute('onclick')
      || e.getAttribute('role') === 'button' || /\b(btn|button|num|axis|jiku|select)\b/i.test(e.className || '');
  }
  // 마번 버튼 컨테이너: 서로 다른 1~2자리 숫자 요소가 3개 이상 모인 부모(오즈 테이블 밖·클릭요소 우선).
  function findAxisContainer() {
    const nums = [...document.querySelectorAll(AXIS_SEL)]
      .filter((e) => (e.children.length === 0 || e.tagName === 'INPUT') && /^\d{1,2}$/.test(_btnLabel(e)) && _btnVisible(e));
    const byParent = new Map();
    for (const e of nums) { const p = e.parentElement; if (!p) continue; (byParent.get(p) || byParent.set(p, []).get(p)).push(e); }
    let best = null, bestScore = -1;
    for (const [p, els] of byParent) {
      const distinct = new Set(els.map(_btnLabel)).size;
      if (distinct < 3) continue;
      const clickyRatio = els.filter(_btnClickable).length / els.length;
      const tablePenalty = p.closest('table') ? 0.6 : 1;   // 테이블(헤더) 내부면 감점
      const score = distinct * (0.4 + clickyRatio) * tablePenalty;
      if (score > bestScore) { bestScore = score; best = p; }
    }
    return best;
  }
  let _axisContainer = undefined;   // 경주별 1회 탐색 캐시(null=없음)
  function findHorseButton(no) {
    const want = String(no);
    if (_axisContainer === undefined) {
      _axisContainer = findAxisContainer();
      if (_axisContainer) console.log('[삼복승축마] 마번 버튼 컨테이너 발견:', _axisContainer.tagName, _axisContainer.className || '(class없음)');
      else console.warn('[삼복승축마] ⚠ 마번 버튼 컨테이너를 못 찾음 — 전역에서 탐색합니다.');
    }
    const pick = (scope) => {
      const all = [...scope.querySelectorAll(AXIS_SEL)].filter(_btnVisible);
      // [긴급2] 1차: 완전일치. 2차: 토큰 경계 포함("4번","軸4","[4]","4 番" 등은 매칭,
      //   14·40 처럼 다른 숫자에 붙은 경우는 제외)로 확장해 '마번 버튼 못 찾음' 방지.
      const exact = all.filter((e) => _btnLabel(e) === want);
      const re = new RegExp('(^|\\D)' + want + '(\\D|$)');
      const cands = exact.length ? exact : all.filter((e) => re.test(_btnLabel(e)));
      cands.sort((a, b) => {
        const inTbl = (e) => (e.closest('table') ? 1 : 0);
        if (inTbl(a) !== inTbl(b)) return inTbl(a) - inTbl(b);          // 테이블 밖 우선
        return (_btnClickable(a) ? 0 : 1) - (_btnClickable(b) ? 0 : 1); // 클릭요소 우선
      });
      return cands[0] || null;
    };
    // 1) 컨테이너 안에서 먼저 → 2) 전역 폴백
    return (_axisContainer && pick(_axisContainer)) || pick(document);
  }

  // [1번][3번] 삼복승: 유력마 3마리를 "축"으로 각각 클릭 → 남은 두 말 매트릭스 → 3마리 조합
  async function collectTrioByAxis(keyHorses, oddsClass) {
    const trioMap = {};
    _axisContainer = undefined;   // 경주별 컨테이너 캐시 초기화(다음 findHorseButton 에서 1회 재탐색)
    // [진단] 페이지 내 '숫자 하나' 클릭요소 후보를 자동 출력 → 사용자가 F12 로 확인·공유 가능
    try {
      const diag = [...document.querySelectorAll('input[type=button],input[type=submit],input[type=radio],button,a')]
        .filter((b) => /^\d{1,2}$/.test(_btnLabel(b)) && _btnVisible(b))
        .map((b) => `${b.tagName}|${b.className || '-'}|"${_btnLabel(b)}"|${b.closest('table') ? 'in-table' : 'outside'}`);
      console.log(`[삼복승축마] 숫자 클릭요소 후보 ${diag.length}개:`, diag);
    } catch (_) { /* */ }
    for (const axis of keyHorses) {
      console.log(`[배당수집] 삼복승 축 ${axis}번 버튼 클릭 시도...`);
      const btn = findHorseButton(axis);
      if (!btn) { console.warn(`[배당수집] ⚠ ${axis}번 축 버튼을 찾지 못함`); continue; }
      const before = oddsSignature();
      try { btn.click(); } catch (e) { console.warn(`[배당수집] ${axis}번 클릭 오류`, e); }
      await wait(3000); // 해당 말 기준 배당 로딩 대기(2초→3초)
      let sig = oddsSignature(), tries = 0;
      // 재시도 강화: 변화 없으면 버튼을 다시 찾아 재클릭 후 대기(최대 3회, 총 ~12초)
      while (sig === before && tries < 3) {
        console.log(`[배당수집] ${axis}번 축 매트릭스 변화 없음 → 재클릭·대기 ${tries + 1}/3`);
        try { (findHorseButton(axis) || btn).click(); } catch (_) { /* */ }
        await wait(3000); sig = oddsSignature(); tries++;
      }
      // [오염방지 1] 축 클릭이 끝내 먹지 않으면(매트릭스 무변화) 화면에는 직전 복승/쌍승 매트릭스가 그대로 남아 있다.
      //   그대로 진행하면 그 2두 페어에 axis 를 붙여 '존재하지 않는 삼복승 조합'을 복승 배당값으로 날조하게 된다.
      //   → 이 축은 건너뛴다(다른 축은 계속 시도 — 기존 수집 흐름 유지).
      if (sig === before) {
        console.warn(`[배당수집] ⚠ ${axis}번 축 클릭 후에도 배당 매트릭스가 바뀌지 않음(재시도 ${tries}회) — 잘못된 버튼이거나 로딩 지연. 이 축은 건너뜁니다(조합 날조 방지).`);
        continue;
      }
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
  // [전적복구] 마번 기준 중복 제거(전적 있는 항목 우선) + 규모 검증.
  //   오즈 조합표·전체 경주목록을 잘못 긁어 수백 행이 딸려오는 오탐을 정상 규모(≤18두)로 축소한다.
  function dedupeStarters(list, tag) {
    const byNo = new Map();
    for (const h of (list || [])) {
      const no = parseInt(h && h.no, 10);
      if (!(no >= 1 && no <= 18)) continue;
      const prev = byNo.get(no);
      const hasRec = h.recent && h.recent.length;
      const prevRec = prev && prev.recent && prev.recent.length;
      if (!prev || (hasRec && !prevRec)) byNo.set(no, h);
    }
    const out = [...byNo.keys()].sort((a, b) => a - b).map((k) => byNo.get(k));
    if ((list || []).length !== out.length) {
      console.log(`[전적수집] ${tag || ''} 중복/범위밖 정제: ${(list || []).length}행 → ${out.length}두`
        + (((list || []).length > 30) ? ' ⚠ 원본이 비정상적으로 큼(오즈표/전체목록 오탐 가능)' : ''));
    }
    return out;
  }
  function collectStarters(root) {
    const D = root || document;   // root 지정 시 fetch 해온 DebaTable 문서에서 추출
    const tables = [...D.querySelectorAll('table')];
    const nospace = (s) => (s || '').replace(/\s+/g, '');
    // ── A) 헤더 기반 탐색(마번+마명, 공백 제거 후 매칭) ──
    let best = null, bestScore = -1, bestHead = null;
    for (const t of tables) {
      const trs = [...t.querySelectorAll('tr')]; if (trs.length < 2) continue;
      const head = [...trs[0].querySelectorAll('th,td')].map((c) => txt(c));
      const hn = head.map(nospace);
      if (!hn.some((h) => STARTER_HDR.no.test(h)) || !hn.some((h) => STARTER_HDR.name.test(h))) continue;
      let score = 0; for (const k in STARTER_HDR) if (hn.some((h) => STARTER_HDR[k].test(h))) score++;
      if (score > bestScore) { best = t; bestScore = score; bestHead = head; }
    }
    let trs, iNo, iName, iJock = -1, iW = -1, iRec = -1;
    if (best) {
      trs = [...best.querySelectorAll('tr')];
      const hn = bestHead.map(nospace);
      const idx = (k) => hn.findIndex((h) => STARTER_HDR[k].test(h));
      iNo = idx('no'); iName = idx('name'); iJock = idx('jockey'); iW = idx('weight'); iRec = idx('recent');
      console.log('[전적수집] 헤더:', JSON.stringify(bestHead), '| 열위치 no/name/jockey/weight/recent =', iNo, iName, iJock, iW, iRec);
      if (iRec < 0) console.warn('[전적수집] ⚠ 최근착순(recent) 열을 헤더에서 못 찾음 — 행 전체에서 착순 시퀀스를 추정합니다(부정확할 수 있음).');
    } else {
      // ── B) 폴백: 헤더 라벨이 없을 때, 본문 어느 열이 연속 마번(1~20)인지로 테이블 판별 ──
      let fbT = null, fbBest = -1, fbNoCol = 0;
      for (const t of tables) {
        const rows = [...t.querySelectorAll('tr')]; if (rows.length < 3) continue;
        for (let c = 0; c < 4; c++) {
          const seen = new Set();
          for (const r of rows) {
            const cells = [...r.querySelectorAll('td,th')];
            const v = cells[c] ? txt(cells[c]) : '';
            if (/^\d{1,2}$/.test(v)) { const n = parseInt(v, 10); if (n >= 1 && n <= 20) seen.add(n); }
          }
          if (seen.size > fbBest) { fbBest = seen.size; fbT = t; fbNoCol = c; }
        }
      }
      if (!fbT || fbBest < 3) {
        console.warn('[전적수집] ❌ 출마표 테이블을 찾지 못함(마번+마명 헤더·연속 마번열 모두 실패). 출마표2 탭이 열렸는지, iframe 내부인지 확인하세요.');
        console.warn(`[전적수집] 참고: 문서 table 수=${tables.length}, tr 수=${D.querySelectorAll('table tr').length}`);
        return [];
      }
      trs = [...fbT.querySelectorAll('tr')]; iNo = fbNoCol;
      // 마명 열: 마번열 외에 '대부분 한글/영문/한자 텍스트'인 첫 열
      const ncol = Math.max(0, ...trs.map((r) => r.querySelectorAll('td,th').length));
      iName = -1;
      for (let c = 0; c < ncol; c++) {
        if (c === iNo) continue;
        let tot = 0, textish = 0;
        for (const r of trs) { const cells = [...r.querySelectorAll('td,th')]; const v = cells[c] ? txt(cells[c]) : ''; if (!v) continue; tot++; if (/[가-힣A-Za-z一-龯ぁ-ヶ]/.test(v) && !/^[\d.\-\s]+$/.test(v)) textish++; }
        if (tot >= 3 && textish >= tot * 0.6) { iName = c; break; }
      }
      console.warn(`[전적수집] ⚠ 헤더 라벨 없음 → 폴백 사용: 마번열=${iNo}, 마명열=${iName} (연속 마번 ${fbBest}개 감지). 착순 열 미상 → 행 전체에서 추정.`);
    }
    const out = [];
    for (const tr of trs) {   // 헤더/비데이터 행은 마번 정규식으로 자연 스킵
      const cells = [...tr.querySelectorAll('th,td')].map((c) => txt(c));
      const no = toNum(cells[iNo]);
      if (!isHorseNo(no) || !/^\d{1,2}$/.test((cells[iNo] || '').trim())) continue;
      // 최근 착순: recent 열의 "1-3-2" 패턴 → 없으면 행 전체에서 착순 시퀀스 탐색
      const recCell = iRec >= 0 ? (cells[iRec] || '') : '';
      const seq = (recCell.match(/\d+(?:\s*[-·・]\s*\d+){1,5}/) || [])[0]
        || (cells.join(' ').match(/\d+(?:\s*[-·・]\s*\d+){2,5}/) || [])[0] || '';
      // 착순은 1~18위 범위만 유효 — 마체중(480 등)·배당이 폴백 정규식에 잡히는 오인 방지
      const recent = seq ? seq.split(/[-·・]/).map((x) => parseInt(x.trim(), 10)).filter((n) => n >= 1 && n <= 18).slice(0, 5) : [];
      out.push({
        no, name: iName >= 0 ? cells[iName] : '', jockey: iJock >= 0 ? cells[iJock] : '',
        recent, weight: iW >= 0 ? toNum(cells[iW]) : null,
      });
    }
    console.log(`[전적수집] 추출된 말 수: ${out.length}마리`);
    let withRec = 0;
    for (const h of out) {
      const recTxt = (h.recent && h.recent.length) ? h.recent.join('-') : '(전적 없음)';
      if (h.recent && h.recent.length) withRec++;
      console.log(`[전적수집] ${h.no}번말 ${h.name || ''} 전적: ${recTxt}`);
    }
    console.log(`[전적수집] 착순 데이터 있는 말: ${withRec}/${out.length}마리${withRec === 0 ? ' ⚠ 전적이 하나도 안 잡혔습니다 — recent 열 인식/셀 형식 확인 필요' : ''}`);
    return dedupeStarters(out, 'collectStarters');
  }

  // [1번] 출마표2 탭 클릭 → 1.5초 대기 → 전적 추출 (탭 없으면 현재화면 시도)
  async function collectStartersByTab() {
    console.log('[전적수집] 출마표2 탭 클릭 시도... (labels=출마표2/출마표/출주표/出馬表)');
    // 진단: 화면의 ‘출마’/‘出馬’ 포함 요소를 모두 출력(실제 탭 버튼 텍스트/태그/클래스 확인용).
    //   [1번] top document 뿐 아니라 동일출처 iframe 내부까지 스캔한다.
    try {
      const docs = sameOriginDocs();
      let n = 0;
      queryAllDocs('button, a, td, th, li, span, div[role="tab"], .tab, .btn').forEach((el) => {
        const t = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
        if (t && t.length <= 14 && /(출마|出馬|출주)/.test(t)) { console.log('[전적수집] ‘출마’ 후보:', el.tagName, '|', el.className || '(class없음)', '|', JSON.stringify(t)); n++; }
      });
      console.log(`[전적수집] 동일출처 문서 수: ${docs.length}${docs.length > 1 ? ' (iframe 포함)' : ''}`);
      if (!n) console.warn('[전적수집] ⚠ ‘출마’ 텍스트 요소가 하나도 없음 — 출마표 페이지가 아니거나 교차출처 iframe 내부일 수 있습니다.');
    } catch (_) { /* */ }
    const r = await clickTabAndWait(['출마표2', '출마표', '출주표', '出馬表'], oddsSignature(), '출마표2', false);
    console.log(`[전적수집] 출마표2 탭: ${r.clicked ? '✅ 클릭됨' : '❌ 버튼 못 찾음(현재 화면에서 시도)'} · 화면 ${r.changed ? '변경 확인' : '변화 없음'}`);
    console.log(`[전적수집] 페이지 전체 table tr 수: ${queryAllDocs('table tr').length} (F12에서 document.querySelectorAll('table tr').length 로도 확인 가능)`);
    await wait(2000);   // [긴급2] 탭 로딩 대기 1.5초 → 2초
    // [1번] top document 우선 추출 → 실패 시 동일출처 iframe 문서들에서 재시도
    let starters = collectStarters();
    if (!starters.length) {
      for (const d of sameOriginDocs()) {
        if (d === document) continue;
        const s = collectStarters(d);
        if (s.length) { console.log(`[전적수집] iframe 문서에서 전적 ${s.length}두 추출 성공`); starters = s; break; }
      }
    }
    return starters;
  }

  // ── [출마표2 = keiba.go.jp DebaTable 별도 페이지] 전적 fetch·추출 ──────────
  //  DebaTable URL 예: https://www2.keiba.go.jp/KeibaWeb/TodayRaceInfo/DebaTable?k_raceDate=2026/07/02&k_raceNo=9&k_babaCode=20&odds_flg=4
  //  [수정] 실제 라이브 호스트는 www2.keiba.go.jp (사용자 확인). 파라미터만 있을 때의 기본 폴백 호스트.
  const DEBA_PATH = 'https://www2.keiba.go.jp/KeibaWeb/TodayRaceInfo/DebaTable';
  function debaParamsFromUrl(url) {
    try {
      const u = new URL(url, location.href);
      const sp = u.searchParams;
      const d = sp.get('k_raceDate'), n = sp.get('k_raceNo'), b = sp.get('k_babaCode');
      if (d && n && b) {
        // [수정] keiba.go.jp 원본 URL이면 호스트+경로(base)를 보존 → www2 등 실제 호스트로 fetch(재조립 시 www 로 뭉개지던 버그 방지)
        const base = /(^|\.)keiba\.go\.jp$/i.test(u.host) ? (u.origin + u.pathname) : null;
        return { k_raceDate: d, k_raceNo: n, k_babaCode: b, base };
      }
    } catch (_) { /* */ }
    return null;
  }
  function buildDebaUrl(p) {
    const base = (p && p.base) || DEBA_PATH;   // [수정] 원본 호스트(www2) 보존, 없으면 기본 폴백
    return `${base}?k_raceNo=${encodeURIComponent(p.k_raceNo)}`
      + `&k_raceDate=${encodeURIComponent(p.k_raceDate)}`
      + `&k_babaCode=${encodeURIComponent(p.k_babaCode)}&odds_flg=4`;
  }
  function isDebaPage() {
    return /(^|\.)keiba\.go\.jp$/.test(location.host) && /\/DebaTable/i.test(location.pathname);
  }
  // keiba DebaTable 전용 파서: 말당 5행(rowspan) 구조 + 競走成績(前走~5走前) 착순 추출.
  //  실제 페이지 검증 완료: 馬番/競走馬/騎手 + 최근5착순(前走→5走前).
  function parseDebaTable(D) {
    try {   // [안정화] 예상 밖 DOM에서도 throw 없이 [] 반환 → 상위 폴백(collectStarters) 동작
      let main = null;
      for (const t of (D || document).querySelectorAll('table')) {
        const h = [...t.querySelectorAll('tr')][0];
        const head = h ? [...h.querySelectorAll('th,td')].map(txt).join('|') : '';
        if (/馬番|마번/.test(head) && /(競走馬|馬名|마명)/.test(head)) { main = t; break; }
      }
      if (!main) { console.warn('[전적수집] DebaTable 메인 테이블(馬番+競走馬 헤더) 못찾음'); return []; }
      const out = [];
      for (const tr of main.querySelectorAll('tr')) {
        const cells = [...tr.querySelectorAll('th,td')];
        // 競走成績 셀: "착순 YY.MM.DD ..." (前走~5走前). 이게 있는 행이 말의 메인행.
        const recCells = cells.filter((c) => /^\d{1,2}\s+\d{2}\.\d{1,2}\.\d{1,2}/.test(txt(c)));
        if (!recCells.length) continue;
        // 馬番: rowSpan 정수셀 중 2번째(枠番 다음). 프레임 공유 시 1번째가 곧 馬番.
        const spanNums = cells.filter((c) => (c.rowSpan || 1) >= 2 && /^\d{1,2}$/.test(txt(c)));
        const no = spanNums.length >= 2 ? parseInt(txt(spanNums[1]), 10)
          : (spanNums[0] ? parseInt(txt(spanNums[0]), 10) : null);
        if (!isHorseNo(no)) continue;
        const nameCell = cells.find((c) => (c.colSpan || 1) >= 2
          && /[ぁ-んァ-ヶ一-龯A-Za-z가-힣]/.test(txt(c)) && !/^[\d.]/.test(txt(c)));
        const name = nameCell ? txt(nameCell) : '';
        const ni = cells.indexOf(nameCell);
        const jockey = (ni >= 0 && cells[ni + 1]) ? txt(cells[ni + 1]) : '';
        const recent = recCells.slice(0, 5).map((c) => parseInt(txt(c), 10)).filter((n) => n >= 1 && n <= 18);
        out.push({ no, name, jockey, recent, weight: null });
      }
      return dedupeStarters(out, 'parseDebaTable');
    } catch (e) {
      console.warn('[전적수집] parseDebaTable 예외 → [] 반환(폴백):', e && e.message);
      return [];
    }
  }
  /** DebaTable 파라미터 확보: keiba면 현재 URL, 아니면 저장된 lastDebaParams / 페이지의 keiba 링크 */
  async function getDebaParams() {
    if (isDebaPage() || /(^|\.)keiba\.go\.jp$/.test(location.host)) {
      const p = debaParamsFromUrl(location.href); if (p) return p;
    }
    // asyukk 페이지 내 keiba DebaTable/오즈 링크에서 파라미터 추출 시도
    for (const a of document.querySelectorAll('a[href*="keiba.go.jp"], a[href*="k_raceDate"]')) {
      const p = debaParamsFromUrl(a.getAttribute('href') || ''); if (p) return p;
    }
    // [출마표 버튼] asyukk 지방경마의 [출마표]는 <input value="출마표" onclick="window.open('...DebaTable?k_raceDate=..&k_raceNo=..&k_babaCode=..')">
    //   형태라 a[href] 스캔에 안 잡힘 → onclick 속성에서 keiba DebaTable URL 을 직접 추출(사용자 확인 패턴).
    //   (출마표2=rakuten 은 k_raceDate 가 없어 자연히 제외됨)
    // [수정] ①입력버튼(input[value=출마표]) 우선 스캔 ②따옴표 안 URL(상대/절대·www2 호스트) 우선 추출 → 호스트 보존
    const debaUrlFromOnclick = (oc) => {
      if (!/k_raceDate/i.test(oc)) return null;
      // window.open('URL')·location.href='URL' 등 따옴표 안 URL(상대·절대 모두) 우선
      const q = oc.match(/['"]([^'"]*k_raceDate[^'"]*)['"]/i);
      if (q && q[1]) return q[1].replace(/&amp;/g, '&');
      // 폴백: 절대 URL 패턴
      const m = oc.match(/https?:\/\/[^'"\\)\s]*k_raceDate[^'"\\)\s]*/i);
      return m ? m[0].replace(/&amp;/g, '&') : null;
    };
    // ① 명시적 [출마표] 입력버튼 먼저(사용자 지정 selector)
    const btns = [
      ...document.querySelectorAll('input[value*="출마표"], input[value*="出馬表"], button'),
      ...document.querySelectorAll('[onclick]'),
    ];
    for (const el of btns) {
      const oc = el.getAttribute('onclick') || '';
      const u = debaUrlFromOnclick(oc);
      if (u) { const p = debaParamsFromUrl(u); if (p) { console.log('[전적수집] 출마표 버튼 onclick에서 DebaTable URL 추출:', u, p); return p; } }
    }
    // 마지막 수단: keiba DebaTable 방문 시 저장해 둔 파라미터
    return new Promise((resolve) => {
      try { chrome.storage.local.get({ lastDebaParams: null }, (v) => resolve(v.lastDebaParams || null)); }
      catch (_) { resolve(null); }
    });
  }
  /** DebaTable 페이지를 가져와(교차출처는 background 경유) 전적 추출. */
  async function fetchDebaStarters(params) {
    const p = params || (await getDebaParams());
    if (!p) { console.warn('[전적수집] ⚠ DebaTable 파라미터(k_raceDate/k_raceNo/k_babaCode)를 찾지 못함 — keiba 출마표2를 한 번 열면 자동 저장됩니다.'); return []; }
    const url = buildDebaUrl(p);
    console.log('[전적수집] DebaTable fetch:', url);
    // [안정화] 네트워크 전송 실패 시 1회 재시도(일시적 오류 대응)
    // [수정] 동일출처는 host 가 '정확히' 일치할 때만 → www 페이지에서 www2 로 fetch 시 CORS 차단되던 문제 방지(호스트 다르면 background 경유)
    let sameOrigin = false;
    try { sameOrigin = new URL(url, location.href).host === location.host; } catch (_) { /* */ }
    const fetchHtml = async () => {
      if (sameOrigin) {
        return await fetch(url, { credentials: 'same-origin' }).then((r) => r.text());     // 동일출처
      }
      const res = await chrome.runtime.sendMessage({ type: 'FETCH_URL', url });             // 교차출처(www→www2 포함) → background
      if (!res || !res.ok) throw new Error((res && res.error) || 'FETCH_URL 실패');
      return res.html;
    };
    let html = null;
    for (let attempt = 0; attempt < 2 && html == null; attempt++) {
      try { html = await fetchHtml(); }
      catch (e) {
        console.warn(`[전적수집] ⚠ DebaTable fetch 오류(시도 ${attempt + 1}/2):`, e && e.message);
        if (attempt === 0) await wait(800);   // 잠깐 대기 후 1회 재시도
      }
    }
    if (html == null) { console.warn('[전적수집] ⚠ DebaTable fetch 최종 실패 → 빈 전적'); return []; }
    const doc = new DOMParser().parseFromString(html || '', 'text/html');
    let starters = parseDebaTable(doc);                 // 전용 파서 우선
    if (!starters.length) starters = collectStarters(doc);  // 폴백: 제네릭
    console.log(`[전적수집] DebaTable에서 ${starters.length}두 추출`);
    if (starters.length) console.log('[전적수집] 예:', starters.slice(0, 3).map((h) => `${h.no}번 ${h.name} [${(h.recent || []).join('-')}]`).join(' / '));
    return starters;
  }
  /** keiba DebaTable 페이지 로드 시: 파라미터 저장 + 전적 추출 후 서버 전송(POST_JAPAN). */
  async function collectDebaOnPage() {
    const p = debaParamsFromUrl(location.href);
    if (p) { try { chrome.storage.local.set({ lastDebaParams: p }); } catch (_) { /* */ } }
    console.log('[전적수집] DebaTable 페이지 감지 — 전적 추출 시작');
    let starters = parseDebaTable(document);
    if (!starters.length) starters = collectStarters();
    if (!starters.length) { console.warn('[전적수집] DebaTable에서 전적을 추출하지 못함'); return; }
    console.log(`[전적수집] DebaTable 추출 ${starters.length}두:`, starters.slice(0, 3).map((h) => `${h.no}번 ${h.name} [${(h.recent || []).join('-')}]`).join(' / '));
    const { raceKey: override } = await getSettings();
    const raceKey = (override && override.trim()) || extractRaceKey();
    const { timerDeadline } = await getSettings();
    const res = await chrome.runtime.sendMessage({
      type: 'POST_JAPAN', reason: 'deba-page',
      payload: { raceKey, horses: starters, deadline: timerDeadline || null, source: location.href },
    });
    console.log(`[전적수집] DebaTable 전적 서버 전송: ${res && res.ok ? '✅ ' + starters.length + '두 (raceKey=' + raceKey + ')' : '❌ ' + (res && res.error)}`);
  }

  // asyukk/generic 전체 3종 수집 (탭 클릭 방식)
  async function collectTripleByTabs(reason) {
    const site = detectSite();
    const oddsClass = site === 'asyukk' ? 'odds_content' : null;
    const { raceKey: override, timerDeadline, sport, market, japanType } = await getSettings();
    const raceKey = _resolveRaceKey(reason, override);   // [경주 자동추종] 자동수집은 배당판 표시 경주 우선
    // [종목 자동감지 강화] resolveSport: 강한 신호(경마장명 raceKey·URL/제목 종목어)로 팝업 stale 정정 → 항상 정확 전송.
    const effSport = resolveSport(sport, raceKey);
    _lastSentSport = effSport;                                   // URL/종목 변경 감지용 최신값 보관
    const isCycleBoat = (effSport === 'cycle' || effSport === 'boat' || effSport === 'bike');   // 6명 종목: 복승+쌍승만·전적 없음
    if (isCycleBoat) console.log(`[${SPORT_LABEL[effSport]}] 종목=${SPORT_LABEL[effSport]} → 복승+쌍승만 수집(삼복승·전적 생략, 6명)`, (sport === 'horse' ? '(자동 감지)' : '(수동/정정)'));
    // [5번][한국모드 강화] 종목=한국 이거나 raceKey/페이지에서 KRA(서울/부산/제주/과천) 감지 시 → 무조건 복승만.
    //   한국경마: 출마표2(keiba DebaTable) 수집 생략(전적은 PDF에서) + 쌍승·삼복승 탭 클릭 완전 차단.
    //   [수정#3] 경륜/경정/바이크는 한국경마 판정을 하지 않는다(경마장명 오탐 방지).
    const isKorea = !isCycleBoat && isKoreaMode(raceKey, market, override);
    if (isKorea) console.log('[한국경마] 복승만 수집 (쌍승/삼복승 미지원)', market !== 'korea' ? '(감지: raceKey/입력/탭, raceKey=' + (raceKey || '미상') + ' · 입력=' + (override || '없음') + ')' : '(종목=한국)');
    // [탭분리] 중앙경마(JRA): 팝업 japanType='central' 이거나 페이지가 중앙으로 보이면 → 복승+쌍승만(삼복승·전적 제외).
    const isCentral = !isKorea && !isCycleBoat && (japanType === 'central' || detectCentralHint());
    // [탭분리] 종목 카테고리 산출 + 팝업 실시간 표시용 storage 기록.
    const category = computeCategory(effSport, isKorea, isCentral);
    try { chrome.storage.local.set({ detectedCategory: category, detectedSport: effSport, detectedAt: Date.now() }); } catch (_) { /* */ }
    console.log(`[종목감지] category=${category} (${CATEGORY_LABEL[category]})`);
    if (!raceKey) {
      setTripleProgress('❌ raceKey 필요', true);
      return { ok: false, error: '사설 사이트는 raceKey 자동 감지가 안 됩니다. 팝업 raceKey 칸에 입력 후 다시 시도하세요.' };
    }
    // [오염방지 2·조합 검증] keiba(fetch) 경로에만 있던 `combo.length === st.len` 검증을 탭 경로에도 도입 + 조합 수 정합성 검증.
    //   ① 구조 검증(전 종류): 조합 길이가 종류와 다르면 그 항목 제외(복승·쌍승=2두 / 삼복승=3두).
    //   ② 수량 검증(복승·쌍승): 등장 마번 수 N 으로 기대 조합 수 산출 → 50% 미만이면 종류 전체를 빈 배열 처리(오염 차단).
    //        복승 N*(N-1)/2 · 쌍승 N*(N-1) · 삼복승 N*(N-1)*(N-2)/6
    //   ⚠ 삼복승은 수량 검증에서 제외한다: '유력마 3두 축 클릭'(collectTrioByAxis) 방식이라 설계상 전체 조합의 일부만
    //      수집된다(축 1두만 잡히면 전체의 ~3/N ≈ 25% 수준). 수량 검증을 걸면 정상 수집분까지 버려져 기존 기능이 깨진다.
    //      삼복승 오염(축 클릭 실패 시 조합 날조)은 위 [오염방지 1] `continue` 가 원천 차단한다.
    const EXPECT = {
      quinella: (n) => n * (n - 1) / 2,
      exacta: (n) => n * (n - 1),
      trio: (n) => n * (n - 1) * (n - 2) / 6,
    };
    const clean = (arr, cap, kind) => {
      const len = (kind === 'trio') ? 3 : 2;
      const ok = (arr || []).filter((c) => c && Array.isArray(c.combo) && c.combo.length === len
        && new Set(c.combo).size === len && c.combo.every((n) => isHorseNo(n)) && c.odds > 0);
      const dropped = (arr || []).length - ok.length;
      if (dropped > 0) console.warn(`[조합검증] ${kind}: 구조 불일치 ${dropped}개 제외(기대 ${len}두 조합)`);
      if (ok.length && kind !== 'trio') {
        const nos = new Set();
        ok.forEach((c) => c.combo.forEach((n) => nos.add(n)));
        const n = nos.size, exp = EXPECT[kind](n);
        if (n >= 3 && exp > 0 && ok.length < exp * 0.5) {
          console.warn(`[조합검증] ⚠ ${kind}: 조합 ${ok.length}개 < 기대 ${exp}개(${n}두)의 50% → 다른 탭 데이터 오염 의심, 빈 배열 처리`);
          return [];
        }
        console.log(`[조합검증] ${kind}: ${ok.length}/${exp}개 (${n}두) ✅`);
      }
      // [보완2] betType 명시 — 기존엔 '어느 키에 담겼는가'로만 종류가 암묵 표현돼 서버가 대조할 방법이 없었다.
      //   이제 각 조합이 스스로 종류를 밝히므로 서버가 키와 직접 대조해 뒤바뀐 데이터를 잡아낼 수 있다.
      return ok.map((c) => ({ combo: c.combo, odds: Math.round(c.odds * 10) / 10, betType: kind }))
        .sort((a, b) => a.odds - b.odds).slice(0, cap);
    };
    console.log(`[배당수집] ===== 배당 수집 시작 (탭 클릭 방식, ${isKorea ? '한국:복승' : '일본:복승+쌍승+삼복승'}) =====`);

    try {
      // [단승 제거] 한국=복승만 / 일본=복승·쌍승·삼복승. 단승은 더 이상 수집하지 않는다.
      //    [긴급2] 각 종목을 독립 try 로 감싸 한 종목이 실패해도 다음 종목은 계속 수집(전체 중단 방지).
      const win = {};   // 단승 미수집 — 서버 payload 호환용 빈 맵 유지

      // 1) 복승 (이미 복승 탭일 수 있음 → 변화 강제 안 함).
      let sig = '';
      let quinella = [];
      try {
        setTripleProgress('복승 수집중…');
        dumpBetTabs();   // [보완3·진단] 탭 구조 1회 덤프(활성표시 클래스·bet_mode 실제값 파악)
        const r1 = await gotoQuinellaTab(isKorea);   // [보완1] 사설 보드는 정확일치('삼복승' 오매칭 차단)
        sig = oddsSignature();
        // [오염방지 1] 복승 탭 버튼 자체를 못 찾으면(clicked=false) 지금 화면이 어느 탭인지 알 수 없다 → 수집 포기(빈 배열).
        //   ⚠ changed=false 는 게이트하지 않는다 — '이미 복승 탭'이 정상 상태이고 그때는 표가 안 바뀌는 게 당연하다.
        //      복승 화면 오인(다른 탭을 복승으로 오독)은 [보완3] 활성탭 확정 + [오염방지 2] 조합 수 검증이 차단한다.
        const _v1 = verifyActiveBetTab('복승');   // [보완3] true=확정일치 / false=확정불일치 / null=판정불가
        if (!r1.clicked || _v1 === false) {
          console.warn('[복승수집] ⚠ 복승 탭 '
            + (_v1 === false ? '활성 확정 불일치' : '버튼을 찾지 못함')
            + ' → 복승 수집 포기(빈 배열 전송·다른 탭 데이터 오염 방지)');
          setTripleProgress('복승 탭 확보 실패 — 수집 생략(오염 방지)');
        } else {
          const quinMap = {};
          for (const p of currentMatrixPairs(oddsClass)) {
            if (!isHorseNo(p.a) || !isHorseNo(p.b) || p.a === p.b) continue;
            const k = p.a < p.b ? `${p.a}-${p.b}` : `${p.b}-${p.a}`;
            if (quinMap[k] == null || p.odds < quinMap[k]) quinMap[k] = p.odds;
          }
          quinella = Object.entries(quinMap).map(([k, o]) => {
            const [a, b] = k.split('-').map(Number); return { combo: [a, b], odds: o };
          });
          console.log(`[복승수집] 파싱 ${quinella.length}조합. 최저배당순 상위: `
            + quinella.slice().sort((a, b) => a.odds - b.odds).slice(0, 10).map((c) => `${c.combo[0]}-${c.combo[1]}=${c.odds}`).join(' · '));
          console.log('[복승수집] 실제 배당판과 몇 개 대조해 보세요(예: 4-7). 값이 다르면 매트릭스 열 정렬 문제 → 콘솔의 이 로그를 공유해주세요.');
        }
      } catch (e) { console.warn('[복승수집] 실패 — 건너뛰고 계속', e); sig = sig || oddsSignature(); }

      // 2) 쌍승 — [일본 전용]. 한국경마는 쌍승을 수집하지 않는다(복승만).
      let exacta = [];
      if (isKorea) {
        console.log('[한국경마] 복승만 수집 (쌍승/삼복승 미지원) → 쌍승 탭 클릭 차단');
        console.log('[쌍승수집] 한국경마 모드 → 쌍승 수집 생략(복승만).');
      } else {
        //  [디버그 강화] 쌍승 탭이 실제로 전환·로드됐는지, 조합이 뽑혔는지 상세 로그.
        setTripleProgress('쌍승 수집중…(최대 5초)');
        console.log('[쌍승수집] 탭 클릭 시도... (labels=쌍승/마단/쌍승식/馬単, 타임아웃 5초·재시도 3회)');
        // 쌍승은 불안정 → 타임아웃 5초·재시도 3회. 실패해도 오류 없이 복승만으로 진행.
        const r2 = await clickTabAndWait(['쌍승', '마단', '쌍승식', '馬単'], sig, '쌍승', true, 5000);
        console.log(`[쌍승수집] 탭 클릭 결과: ${r2.clicked ? '✅ 클릭됨' : '❌ 버튼 못 찾음'} · 배당 ${r2.changed ? '변경 확인' : '⚠ 변화 없음(복승 화면 그대로일 수 있음)'}`);
        sig = r2.sig || oddsSignature();
        // [오염방지 1·최우선] 탭 전환이 확인되지 않으면(버튼 못 찾음 OR 표 무변화) 화면에는 아직 복승 매트릭스가 있다.
        //   기존 코드는 이 경고를 찍고도 그대로 긁어 **복승 배당을 쌍승으로 전송**했고, 방향까지 뒤집혀(`b>a`)
        //   쌍승역전 공식(_win_exacta_reversal)에 가짜 역전 신호로 주입됐다. → 전환 미확인 시 쌍승은 빈 배열.
        //   복승과 달리 여기서는 changed=false 도 게이트한다(직전이 복승 화면이므로 '무변화 = 전환 실패'가 확실).
        const _v2 = verifyActiveBetTab('쌍승');   // [보완3] DOM 속성으로 활성 탭 확정
        if (!r2.clicked || !r2.changed || _v2 === false) {
          exacta = [];
          console.warn('[쌍승수집] ⚠ 쌍승 탭 전환 미확인('
            + (!r2.clicked ? '버튼 못 찾음' : (_v2 === false ? '활성 확정 불일치' : '표 무변화'))
            + ') → 쌍승 수집 포기(빈 배열 전송·복승 데이터 오염 방지)');
          setTripleProgress('쌍승 탭 전환 실패 — 수집 생략(오염 방지)');
        } else {
          const exMap = {};
          for (const p of currentMatrixPairs(oddsClass)) {
            if (!isHorseNo(p.a) || !isHorseNo(p.b) || p.a === p.b) continue;
            // [쌍승 방향 긴급수정] asyukk 쌍승 매트릭스는 **열(헤더)=1착(선착)·행=2착(후착)**.
            //   예: 행3·열5 셀 = "5번(열) 1착, 3번(행) 2착". parseMatrixTable은 {a:행, b:열}이므로
            //   선착=열=p.b, 후착=행=p.a → combo=[p.b, p.a]. (기존 [p.a,p.b]는 방향이 반대라
            //   역전 오판을 일으켰음: 5→3 저배당을 3→5로 잘못 저장.)
            const k = `${p.b}>${p.a}`;
            if (exMap[k] == null || p.odds < exMap[k]) exMap[k] = p.odds;
          }
          exacta = Object.entries(exMap).map(([k, o]) => {
            const [a, b] = k.split('>').map(Number); return { combo: [a, b], odds: o };
          });
          console.log(`[쌍승수집] 추출된 조합 수: ${exacta.length}개`);
          if (exacta.length) {
            const top5 = [...exacta].sort((a, b) => a.odds - b.odds).slice(0, 5)
              .map((e) => `${e.combo[0]}→${e.combo[1]} ${e.odds}`).join(' · ');
            console.log(`[쌍승수집] 상위 5개(최저배당순): ${top5}`);
          } else {
            console.warn('[쌍승수집] ⚠ 쌍승 미수집 — 복승만으로 분석을 진행합니다.');
            setTripleProgress('쌍승 미수집 — 복승만으로 분석 진행');
          }
        }
      }

      // 3) [일본 전용] 출마표2 전적: keiba.go.jp DebaTable을 fetch해 추출(우선) → 실패 시 인페이지 탭 클릭 폴백
      //    [4번] 단승→복승→쌍승→출마표2 순서. 불안정한 삼복승보다 먼저 수집해 전적 누락을 방지한다.
      //    [2·5번] 한국경마(market=korea)는 출마표2가 없고 전적은 PDF에서 추출하므로 수집 시도 자체를 생략 → 관련 오류 제거
      let starters = [];
      if (isKorea) {
        console.log('[전적수집] 한국경마 모드 → 출마표2(keiba DebaTable) 수집 생략(전적은 PDF에서 추출)');
        setTripleProgress('한국경마 모드 — 전적은 PDF에서 (출마표2 생략)');
      } else if (isCentral) {
        // [1번] 일본 중앙(JRA): 전적표(출마표2)가 없다 → 수집 시도 자체를 생략, 배당만으로 분석.
        console.log('[전적수집] 일본 중앙경마(JRA) 모드 → 전적표 없음, 출마표2 수집 생략(배당만 분석)');
        setTripleProgress('중앙경마(JRA) — 배당만 분석 (전적표 생략)');
      } else if (isCycleBoat) {
        // [수정#3] 경륜/경정: 출마표2(전적) 개념이 없다 → 수집 생략, 배당만으로 분석.
        console.log(`[전적수집] ${SPORT_LABEL[effSport]} 모드 → 전적표 없음, 출마표2 수집 생략(배당만 분석)`);
        setTripleProgress(`${SPORT_LABEL[effSport]} — 배당만 분석 (전적표 생략)`);
      } else {
        // [1번] 일본 지방(NAR): 전적표 있음 → 출마표2(keiba DebaTable) 수집
        setTripleProgress('출마표2 전적 수집중…(keiba DebaTable)');
        console.log('[배당수집] 출마표2 탭 클릭 시도... (지방경마 NAR — 전적+배당 통합)');
        try { starters = await fetchDebaStarters(); } catch (e) { console.warn('[전적수집] DebaTable fetch 오류', e); }
        if (!starters.length) {
          console.log('[전적수집] DebaTable 실패/없음 → 인페이지 출마표2 탭 시도(폴백)');
          setTripleProgress('출마표2 전적 수집중…(인페이지 폴백)');
          try { starters = await collectStartersByTab(); } catch (e) { console.warn('[전적수집] 인페이지 수집 오류', e); }
          await returnToQuinellaTab(); // 복승으로 복귀([오염방지 4] 사설 보드는 정확일치)
        }
        console.log(`[배당수집] 전적 추출: ${starters.length}두`);
      }

      // 4) [수정#1 삼복승 복구 · 강화] 삼복승 탭 클릭 → ①직접 목록(currentTrios, 6명 소규모 보드)
      //    + ②유력마 3두 축마 버튼 클릭(collectTrioByAxis) 을 함께 시도해 병합(같은 조합 최저배당 유지).
      //    한국·중앙경마만 생략. 경륜·경정·바이크(6명)도 삼복승 탭을 눌러 수집한다(기존 미클릭 문제 해결).
      //    삼복승은 불안정할 수 있어 독립 try/catch로 격리(실패해도 복승·쌍승은 유지).
      let trio = [];
      if (isKorea) {
        console.log('[한국경마] 복승만 수집 (쌍승/삼복승 미지원) → 삼복승 탭 클릭 차단');
      } else if (isCentral) {
        console.log('[중앙경마] 삼복승 수집 생략(복승+쌍승만·배당 전용)');
      } else if (effSport === 'boat') {
        // [경정 삼복승 차단] 경정(보트)은 삼복승 배당이 불안정·노이즈 → 복승+쌍승만 수집(사용자 요청).
        console.log('[경정] 삼복승 수집 차단(복승+쌍승만) — 불안정 노이즈 방지');
      } else {
        try {
          const sportTag = isCycleBoat ? SPORT_LABEL[effSport] : '일본경마';
          setTripleProgress(`삼복승 수집중…(${sportTag} · 탭 클릭)`);
          const keyH = localKeyHorses(quinella);   // 상위 복승조합 등장빈도+인기가중 유력마 3두
          console.log(`[삼복승수집] ${sportTag} 유력마(축) 후보:`, keyH.join('·') || '(없음)');
          // [삼복승 강화] ① asyukk34: span.bet_type_btn '삼복승'(정확) 클릭(삼복승조합 혼동 방지)
          const _sigBeforeTrio = sig;
          let trioTabOk = false;
          const betTab = clickAsyukkBetTab('삼복승');
          if (betTab) {
            console.log('[삼복승수집] ✅ .bet_type_btn "삼복승" 정확 클릭 (bet_mode=' + (betTab.getAttribute('bet_mode') || '?') + ')');
            await wait(2000); sig = oddsSignature();
            // [오염방지 1] 정확 클릭이라도 표가 안 바뀌었으면 전환 실패 — 직전(복승/쌍승) 화면이 그대로 남아 있다.
            trioTabOk = (sig !== _sigBeforeTrio && sig.length > 0);
            if (!trioTabOk) console.warn('[삼복승수집] ⚠ 삼복승 정확 클릭했으나 표 무변화 → 전환 실패로 간주');
          } else {
            // ② 폴백: 일반 텍스트 탭 탐색(keiba·기타 보드)
            const rt = await clickTabAndWait(['삼복승', '삼복', '三連複', '3連複', '３連複', '삼연복'], sig, '삼복승', true, 5000);
            console.log(`[삼복승수집] 폴백 탭 클릭: ${rt.clicked ? '✅ 클릭됨' : '❌ 버튼 못 찾음'} · 배당 ${rt.changed ? '변경 확인' : '⚠ 변화 없음'}`);
            sig = rt.sig || oddsSignature();
            trioTabOk = !!(rt.clicked && rt.changed);
          }
          // [보완3] DOM 속성으로 활성 탭 확정 — 확정 불일치면 시그니처가 바뀌었어도 전환 실패로 간주.
          if (verifyActiveBetTab('삼복승') === false) {
            trioTabOk = false;
            console.warn('[삼복승수집] ⚠ 활성 탭 확정 불일치 → 전환 실패로 간주');
          }
          // [오염방지 1] 삼복승 탭 전환이 확인되지 않으면 수집 자체를 생략(빈 배열) — 복승/쌍승 화면을
          //   삼복승으로 오독하거나 축 클릭으로 조합을 날조하는 경로를 원천 차단.
          let direct = [], byAxis = [];
          if (!trioTabOk) {
            console.warn('[삼복승수집] ⚠ 삼복승 탭 전환 미확인 → 삼복승 수집 포기(빈 배열 전송·복승/쌍승 오염 방지)');
            setTripleProgress('삼복승 탭 전환 실패 — 수집 생략(오염 방지)');
          } else {
            // ① 직접 목록(화면에 "a-b-c 배당" 나열 — 6명 종목 등 소규모 보드에서 유효)
            try { direct = currentTrios(); } catch (_) { /* */ }
            if (direct.length) console.log(`[삼복승수집] 직접 목록 ${direct.length}개 추출`);
            // ② 유력마 축 클릭 매트릭스(일본 지방경마 등 매트릭스형 보드)
            if (keyH.length) {
              try { byAxis = await collectTrioByAxis(keyH, oddsClass); } catch (_) { /* */ }
              if (byAxis.length) console.log(`[삼복승수집] 축 클릭 ${byAxis.length}개 추출`);
            }
          }
          // 병합(같은 3두 조합은 최저 배당 유지)
          const tmap = {};
          for (const t of direct.concat(byAxis)) {
            if (!t || !t.combo || t.combo.length !== 3 || t.odds == null || t.odds <= 0) continue;
            const key = [...t.combo].sort((a, b) => a - b).join('-');
            if (tmap[key] == null || t.odds < tmap[key]) tmap[key] = t.odds;
          }
          trio = Object.entries(tmap).map(([k, o]) => ({ combo: k.split('-').map(Number), odds: o }));
          console.log(`[삼복승수집] 병합 결과 ${trio.length}개 (직접 ${direct.length}·축 ${byAxis.length})`);
          if (!trio.length) {
            console.warn('[삼복승수집] ⚠ 삼복승 미수집 — 복승/쌍승만으로 분석 진행');
            // [진단] 실제 삼복승 배당이 화면 어디에 있는지 파악용 raw 덤프(현재 배당 셀/텍스트 일부)
            try {
              const cells = [...document.querySelectorAll('.odds_content')].slice(0, 25).map((c) => (c.textContent || '').trim()).filter(Boolean);
              console.log('[삼복승진단] .odds_content 셀 샘플:', cells.join(' | ') || '(없음)');
              const trioTxt = [...document.querySelectorAll('td,span,div,li')]
                .map((e) => (e.textContent || '').trim())
                .filter((t) => /^\d{1,2}\s*[-–—ー]\s*\d{1,2}\s*[-–—ー]\s*\d{1,2}/.test(t)).slice(0, 8);
              console.log('[삼복승진단] "a-b-c" 형태 텍스트 샘플:', trioTxt.join(' | ') || '(없음)');
            } catch (_) { /* */ }
          }
          // 복승으로 복귀(다음 수집 사이클 안정화) — [오염방지 4] 사설 보드는 정확일치로 '삼복승' 오매칭 차단
          await returnToQuinellaTab();
        } catch (e) { console.warn('[삼복승수집] 실패 — 복승/쌍승만으로 진행', e); }
      }

      // [오염방지 2] 종류를 넘겨 구조(조합 길이)·수량(기대 조합 수 50%+) 검증을 통과한 것만 전송
      const _q = clean(quinella, 200, 'quinella');
      let _x = clean(exacta, 400, 'exacta');
      const _tr = clean(trio, 300, 'trio');
      // [오염방지 2-b·핵심] 수량 검증만으로는 최악의 오염을 못 잡는다: 복승 삼각(nC2=66)이 쌍승으로 들어오면
      //   66/132 = 정확히 50% 라 하한을 통과한다(복승 보드가 정사각이면 100%라 아예 무의미).
      //   → 값으로 판정: 겹치는 말쌍의 90%+ 에서 쌍승 배당이 복승 배당과 같으면 복승 화면을 긁은 것이다.
      if (_q.length && _x.length) {
        const qm = {};
        _q.forEach((c) => { qm[Math.min(c.combo[0], c.combo[1]) + '|' + Math.max(c.combo[0], c.combo[1])] = c.odds; });
        let tot = 0, same = 0;
        _x.forEach((c) => {
          const qo = qm[Math.min(c.combo[0], c.combo[1]) + '|' + Math.max(c.combo[0], c.combo[1])];
          if (qo == null || !(qo > 0)) return;
          tot++;
          if (Math.abs(c.odds - qo) <= Math.max(0.05, qo * 0.01)) same++;
        });
        if (tot >= 5 && same / tot >= 0.9) {
          console.warn(`[조합검증] ⚠ 쌍승 배당이 복승과 동일(${same}/${tot}) → 복승 화면을 쌍승으로 수집한 것으로 판단, 쌍승 빈 배열 처리`);
          setTripleProgress('쌍승=복승 동일 감지 — 쌍승 제외(오염 방지)');
          _x = [];
        }
      }
      const payload = {
        raceKey, win,
        quinella: _q, exacta: _x, trio: _tr,
        sport: effSport,       // [수정#3] 종목(horse|cycle|boat|bike)
        category,              // [탭분리] 분석기 탭 라우팅용(korea|japan_local|japan_central|boat|cycle|bike)
        deadline: timerDeadline || null, capturedAt: new Date().toISOString(), source: location.href,
      };
      console.log(`[배당수집] ===== 완료: [${CATEGORY_LABEL[category]}] 복승 ${payload.quinella.length}·쌍승 ${payload.exacta.length}·삼복승 ${payload.trio.length}·전적 ${starters.length}두 =====`);
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
          payload: { raceKey, horses: starters, deadline: timerDeadline || null, source: location.href },
        });
        console.log('[전적] /api/extract/japan 응답:', japan && japan.ok ? 'ok' : (japan && japan.error));
      }
      const exNote = isKorea ? '' : (payload.exacta.length ? `·쌍승 ${payload.exacta.length}` : '·쌍승 미수집');
      const stNote = isKorea ? '·전적 PDF' : `·전적 ${starters.length}두`;
      setTripleProgress(res && res.ok
        ? `수집 완료 ✅ 복승 ${payload.quinella.length}${exNote}${stNote}`
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
    // [발주감지] 수집 때마다 발주시각을 배당판에서 자동 읽어 타이머에 반영(수동 입력 불필요)
    try { await autoDetectPostTime(extractRaceKey()); } catch (_) { /* */ }
    const _r = await (detectSite() === 'keiba' ? collectTripleKeiba(reason) : collectTripleByTabs(reason));
    // [수정1·수집→분석 자동연결] 수집 성공 시 즉시 서버 분석을 트리거하고 analyzeStatus 를 갱신 →
    //   오버레이가 20초 폴을 기다리지 않고 바로 분석 결과를 반영("대기 중" 잔존 방지). overlay.pollOverlayAnalyze 와 동일 경로.
    try {
      if (_r && _r.ok) {
        let _rk = '';
        try { _rk = extractRaceKey(); } catch (_) { _rk = ''; }
        if (!_rk) { const _s = await getSettings(); _rk = (_s.raceKey || '').trim(); }
        const _ar = await chrome.runtime.sendMessage({ type: 'ANALYZE_TRIPLE', raceKey: _rk });
        if (_ar && _ar.ok && _ar.data) {
          await new Promise((res) => chrome.storage.local.set({ analyzeStatus: { data: _ar.data, at: Date.now() } }, res));
          console.log('[수집→분석] ' + (_rk || '(현재경주)') + ' 분석 자동 갱신 → 오버레이 즉시 반영');
        }
      }
    } catch (_) { /* */ }
    return _r;
  }

  // ── 설정 로드 & 자동전송 루프 ───────────────────────────────────────
  let timer = null;
  let _autoRunning = false;   // [5번] 재진입 방지: 이전 수집이 30초를 넘겨도 겹치지 않게

  async function getSettings() {
    return new Promise((resolve) => {
      chrome.storage.local.get(
        // autoMode: 'triple'(전체 3종 수집) — 단승 스냅샷 모드는 폐지됨 · timerDeadline: 발주시각(epoch ms)
        // japanType: 'local'(지방 NAR·전적+배당) | 'central'(중앙 JRA·배당만)
        { autoSend: false, intervalSec: 30, raceKey: '', autoMode: 'triple', timerDeadline: 0, sport: 'horse', market: 'auto', japanType: 'local' },
        (v) => resolve(v)
      );
    });
  }

  // [단승 제거] doSend(단승 snapshot 전용)·autoMode 'snapshot' 폐지.
  //   keiba/asyukk 모두 collectTriple 로 복승(+쌍승·삼복승)만 수집한다.

  // ── 사이트 무관 전송: 복승 매트릭스 → triple ingest ─────────────────
  //   asyukk/generic 의 복승 매트릭스를 /api/odds/triple/ingest(quinella) 로 보내
  //   서버 앱의 매트릭스 UI 에서 바로 보이게 한다. (단승 snapshot 은 폐지)
  async function sendCurrent(reason) {
    const { raceKey: override } = await getSettings();
    const payload = buildPayload(override);
    if (!payload.raceKey) {
      return { ok: false, error: 'raceKey 를 만들 수 없습니다. 팝업 raceKey 칸에 직접 입력하세요.', payload };
    }
    const pairs = (payload.quinella && payload.quinella.pairs) || [];
    if (!pairs.length) {
      return { ok: false, error: '전송할 복승 매트릭스가 없습니다. 배당판 페이지인지 확인하세요.', payload };
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
    const ok = parts.length > 0 && parts.every((p) => p.ok);
    const detail = parts.map((p) => `${p.kind} ${p.n}${p.ok ? '✅' : '❌'}`).join(' · ');
    return { ok, parts, detail, raceKey: payload.raceKey, error: ok ? '' : (parts.find((p) => !p.ok)?.error || '전송 실패') };
  }

  // [1번] 발주 임박 단계(120/60초·마감) 1회성 트리거 기록 (발주시각 바뀌면 초기화)
  let _stageFired = new Set();

  // ── [경기 마감 감지] 한/일 공통: 마감되면 자동수집을 중단시킨다 ──────────
  //   1) DOM 텍스트에서 "발매마감/締切" 등 마감 문구 감지
  //   2) 배당이 CLOSE_UNCHANGED_TICKS 회 연속 무변동(발주 임박/미설정 시에만 적용)
  let _lastOddsSig = '';
  let _oddsUnchangedCount = 0;
  const CLOSE_UNCHANGED_TICKS = 5;   // 배당 5회 연속 무변동 → 마감 간주(≈수집 5틱)
  // 마감/종료 문구(한국·일본 배당판 공통). "마감" 단독은 오탐 위험이 커 제외.
  const CLOSE_TEXT_RE = /발매\s*마감|발매\s*종료|투표\s*마감|투표\s*종료|접수\s*마감|접수\s*종료|締\s*切|締め切り|発売\s*締切|発売\s*終了|受付\s*終了|販売\s*終了/;

  // [마감오판 방지] 배당판의 실제 "남은시간"을 직접 읽어 마감 임박 여부 판단(확장 발주시각 미검출 대비).
  //   "남은시간 2분 40초" / "남은시간 00:45" / "2분 40초" 등을 ms 로 환산. 없으면 null.
  function pageRemainingMs() {
    try {
      const txt = (document.body && document.body.innerText) || '';
      let m = txt.match(/남은\s*시간[\s\S]{0,12}?(\d{1,2})\s*분\s*(\d{1,2})\s*초/);
      if (!m) m = txt.match(/남은\s*시간[\s\S]{0,12}?(\d{1,2})\s*[:：]\s*(\d{2})/);
      if (!m) m = txt.match(/(\d{1,2})\s*분\s*(\d{1,2})\s*초/);   // 폴백: 화면 어딘가의 "N분 M초"
      if (m) return (parseInt(m[1], 10) * 60 + parseInt(m[2], 10)) * 1000;
    } catch (_) { /* noop */ }
    return null;
  }

  function detectRaceClosed(deadline) {
    // 1) DOM 텍스트 기반 마감 감지 (가장 확실)
    try {
      const txt = (document.body && document.body.innerText) || '';
      if (CLOSE_TEXT_RE.test(txt)) return { closed: true, reason: 'DOM 발매마감 감지' };
    } catch (_) { /* noop */ }
    // 2) 배당 무변동 기반 감지 — '진짜 마감 임박(≤90초)'일 때만 적용(오탐 방지).
    //    [버그수정] 발주시각 미설정 시 무조건 적용하던 것을 제거 → 페이지 남은시간을 우선 신뢰.
    //    · 페이지에 남은시간 있으면 그 값이 90초 이하일 때만
    //    · 없으면 확장 발주시각(deadline)이 90초 이하일 때만
    //    · 둘 다 없으면(시간 정보 전무) 무변동만으로는 마감 처리하지 않음(DOM 문구에만 의존)
    try {
      const now = Date.now();
      const pageLeft = pageRemainingMs();
      let near;
      if (pageLeft != null) near = pageLeft <= 90000;
      else if (deadline) near = (deadline - now) <= 90000;
      else near = false;
      const sig = (typeof oddsSignature === 'function') ? oddsSignature() : '';
      if (sig && sig === _lastOddsSig) {
        _oddsUnchangedCount++;
        if (near && _oddsUnchangedCount >= CLOSE_UNCHANGED_TICKS) {
          return { closed: true, reason: `배당 ${CLOSE_UNCHANGED_TICKS}회 무변동(마감 임박)` };
        }
      } else {
        _oddsUnchangedCount = 0;
        _lastOddsSig = sig;
      }
    } catch (_) { /* noop */ }
    return { closed: false };
  }

  // [1번] 발주 임박/최종베팅 알림을 모든 탭에 전달 → timer.js 가 배너+소리로 표시
  function pushCollectAlert(level, text) {
    try { chrome.storage.local.set({ collectAlert: { level, text, at: Date.now() } }); } catch (_) { /* noop */ }
  }

  // [1번] 서버 이상감지 분석 실행 → 분석결과(betRecommend 등) 반환. 실패 시 null.
  function runAnalyzeForAlert() {
    return new Promise((resolve) => {
      let done = false;
      chrome.runtime.sendMessage({ type: 'ANALYZE_TRIPLE', raceKey: '' }, (res) => {
        done = true;
        if (chrome.runtime.lastError || !res || !res.ok || !res.data) { resolve(null); return; }
        try { chrome.storage.local.set({ analyzeStatus: { data: res.data, at: Date.now() } }); } catch (_) { /* noop */ }
        resolve(res.data);
      });
      setTimeout(() => { if (!done) resolve(null); }, 8000); // 서버 무응답 방어
    });
  }

  // [1번] 분석결과 → "복승 6+9" 형태의 최종 베팅 문자열
  function _mainBet(data) {
    const recs = (data && data.betRecommend) || [];
    const m = recs.find((r) => r.label === '복승 메인') || recs[0];
    return m ? `복승 ${m.combo.join('+')}` : '';
  }

  // [v2.0.0] 자동수집 타이머를 background.js(서비스워커)로 이관 → 팝업이 닫혀도,
  //   다른 탭으로 이동해도 수집이 계속된다. content.js 는 background 의 AUTO_COLLECT
  //   메시지에만 반응해 수집을 실행한다. (아래 자체 setTimeout 루프는 하위호환 보존·기본 비활성)
  const BG_DRIVES = true;
  async function restartLoop() {
    if (timer) { clearTimeout(timer); timer = null; }
    _stageFired = new Set();
    if (BG_DRIVES) return;   // background.js 가 타이머를 담당하므로 자체 루프는 돌리지 않음
    const { autoSend } = await getSettings();
    if (!autoSend) return;

    // [5번] await + 재진입 가드: 한 번의 수집이 간격보다 오래 걸려도 다음 틱과 겹치지 않게
    const runAuto = async () => {
      if (_autoRunning) { console.log(`[자동수집] 이전 수집 진행중 → 이번 틱(${new Date().toLocaleTimeString('ko-KR', { hour12: false })}) 건너뜀`); return; }
      _autoRunning = true;
      try {
        await collectTriple('auto');   // [단승 제거] 항상 3종(복승·쌍승·삼복승) 수집
      } catch (e) { console.warn('[자동수집] 틱 실행 오류', e); }
      finally { _autoRunning = false; }
    };

    // [긴급1] 발주시각 기준 동적 루프:
    //   T-5분: 수집 간격 30초 → 15초 단축
    //   T-2분: 이상감지 즉시 실행 + 알림
    //   T-1분: 최종 베팅 확정 + 🚨 강한 알림(소리 3번)
    //   T-30초: 마지막 경고
    //   T-0: 수집 중지
    //   발주시각 미설정(timerDeadline=0) 시 기존 동작(고정 간격·무한 수집) 그대로 유지.
    const STAGE_MS = [120000, 60000, 30000, 0];   // 알림/중지 경계(정확히 이 시점에 깨어나도록 정렬)
    const loop = async () => {
      const { autoSend: on, intervalSec, timerDeadline } = await getSettings();
      if (!on) { timer = null; return; }                 // 자동수집 꺼짐 → 종료
      const left = timerDeadline ? (timerDeadline - Date.now()) : null;

      // T-0: 발주 마감 도달 → 수집 자동 중지 (루프 재예약 안 함)
      if (left != null && left <= 0) {
        if (!_stageFired.has('stop')) {
          _stageFired.add('stop');
          pushCollectAlert('🔴', '⏹ 발주 마감 · 배당 수집을 자동 중지했습니다');
        }
        timer = null; return;
      }

      await runAuto();                                   // 이번 틱 수집(완료 즉시 아래 분석)

      // T-2분: 이상감지 즉시 실행 + 알림
      if (left != null && left <= 120000 && !_stageFired.has(120)) {
        _stageFired.add(120);
        const data = await runAnalyzeForAlert();
        pushCollectAlert('🟠', `⚠️ 마감 2분전 이상감지 결과${data && _mainBet(data) ? ' · ' + _mainBet(data) : ''}`);
      }
      // T-1분: 최종 베팅 추천 확정 + 강한 알림(소리 3번)
      if (left != null && left <= 60000 && !_stageFired.has(60)) {
        _stageFired.add(60);
        const data = await runAnalyzeForAlert();
        const bet = data ? _mainBet(data) : '';
        pushCollectAlert('🚨', `🚨 마감 1분전 - 최종베팅: ${bet || '데이터 부족'}`);
      }
      // T-30초: 마지막 경고
      if (left != null && left <= 30000 && !_stageFired.has(30)) {
        _stageFired.add(30);
        pushCollectAlert('🚨', '⏰ 30초 남음 - 지금 베팅하세요!');
      }

      // 다음 간격: 마감 5분전부터 15초, 그 외 설정값(기본 30초).
      //   단, 다음 단계 경계(T-2·T-1·T-30·T-0)가 더 가까우면 그 시점에 정확히 깨어난다.
      const baseMs = Math.max(10, Number(intervalSec) || 30) * 1000;
      let ms = (left != null && left <= 300000) ? 15000 : baseMs;
      if (left != null) {
        for (const s of STAGE_MS) { if (left > s) { ms = Math.min(ms, left - s); break; } }
        ms = Math.max(1000, ms);   // 과도한 폭주 방지(최소 1초)
      }
      timer = setTimeout(loop, ms);
    };

    loop(); // 켜는 즉시 1회
  }

  // 설정이 바뀌면 루프 재시작 (발주시각 변경 시에도 단계 알림 재설정)
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === 'local' && (changes.autoSend || changes.intervalSec || changes.autoMode || changes.timerDeadline)) restartLoop();
  });

  // 팝업 ↔ content 메시지 처리
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg?.type === 'MANUAL_SEND') {
      // [단승 제거] keiba 는 3종 수집(복승·쌍승·삼복승), 그 외(asyukk/generic)는 복승 매트릭스 전송
      (detectSite() === 'keiba' ? collectTriple('manual') : sendCurrent('manual')).then(sendResponse);
      return true; // async
    }
    if (msg?.type === 'MANUAL_SEND_RESULTS') {
      sendResults('manual').then(sendResponse);
      return true;
    }
    // [1·2번] 결과 자동수집: 경주결과 탭 클릭 → 대기 → 추출·전송 (타이머/수동 공용)
    if (msg?.type === 'COLLECT_RESULTS') {
      collectResultsByTab(msg.reason || 'auto').then(sendResponse);
      return true;
    }
    // [v2.0.1] 결과 자동수집(fetch 방식): /bet/result 표를 fetch·파싱·전송
    if (msg?.type === 'COLLECT_RESULTS_FETCH') {
      collectResultsByFetch(msg.reason || 'auto-result').then(sendResponse);
      return true;
    }
    if (msg?.type === 'MANUAL_COLLECT_TRIPLE') {
      collectTriple('manual').then(sendResponse);
      return true;
    }
    // [v2.0.0] background 자동수집 엔진의 수집 지시(팝업/탭 무관하게 주기적으로 옴)
    if (msg?.type === 'AUTO_COLLECT') {
      (async () => {
        if (_autoRunning) { sendResponse({ ok: false, error: '이전 수집 진행중(건너뜀)' }); return; }
        // [버그수정] 자동전송 OFF 시 탭 클릭 방지: 사용자가 방금 자동수집을 껐는데
        //   in-flight 알람(stageT/fine)이 도착하면 복승/쌍승 탭을 강제 클릭해 수동 베팅을 방해하던 문제.
        //   background 엔진은 autoSend 게이트가 있지만, 끄는 순간 이미 발사된 틱은 여기서 최종 차단.
        const { autoSend: _on } = await getSettings();
        if (!_on) { sendResponse({ ok: false, skipped: true, error: '자동전송 OFF — 수집/탭클릭 생략' }); return; }
        // [자동전송 역할 재정의] NAR 지방경마·경륜은 서버 oddspark 전담 → 확장 auto 수집 차단(서버로 전송 안 함)
        const _autoCat = await _currentCategory();
        _markCollectMode(_autoCat);
        if (_isServerCollectedCat(_autoCat)) {
          console.log('[역할재정의] ' + _autoCat + ' → 서버 자동수집 종목이라 확장 자동전송 차단(중복·꼬임 방지)');
          sendResponse({ ok: false, skipped: true, serverCollected: true, category: _autoCat, error: '서버 자동수집 종목 — 확장 전송 차단' });
          return;
        }
        _autoRunning = true;
        try {
          const { timerDeadline } = await getSettings();
          const r = await collectTriple('auto');   // [단승 제거] 항상 3종 수집
          // [수정2] 경기 마감 감지 → background 엔진에 중단 신호 전달
          const close = detectRaceClosed(timerDeadline);
          if (close.closed) {
            setTripleProgress('⏹ 경기 마감 - 자동수집 중단됨', true);
            console.log('[자동수집] 경기 마감 감지 → 자동수집 중단:', close.reason);
          }
          sendResponse(Object.assign({ ok: false }, r || {}, { closed: close.closed, closeReason: close.reason || '' }));
        } catch (e) { sendResponse({ ok: false, error: String(e.message || e) }); }
        finally { _autoRunning = false; }
      })();
      return true; // async
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

  // [1번] 경주 자동 읽기: 30초마다 배당판에서 현재 경주를 감지 → raceKey 자동 업데이트 + 서버 전송.
  //   배당판에서 '이전/다음'으로 경주를 바꾸면(예: 제주 3경주 → 제주 4경주) 자동으로 따라간다.
  let _raceWatchTimer = null, _lastWatchedRace = '';
  // [종목 자동감지 강화] URL 변경·종목 변경 감지용. sport 태그가 사이트 전환 시 누락/이월되지 않게 재수집 유도.
  let _lastSportHref = location.href, _lastSentSport = '';
  // [다음경주 자동전환·마감 감지] 카운트다운(남은시간)이 있었다가 사라지면(=경주 마감)
  //   background 에 RACE_FINISHED 를 통지 → 발주시각 없이도 다음경주 전환 체인 가동.
  let _hadCountdown = false, _finishNotifiedRk = '';
  function _finishWatchdog() {
    let rk = '';
    try { rk = extractRaceKey(); } catch (_) { return; }
    let rem = null;
    try { rem = detectRemainingMs(); } catch (_) { rem = null; }
    if (rem != null && rem > 5000) { _hadCountdown = true; return; }  // 아직 진행중(카운트다운 살아있음)
    // 카운트다운이 있었다가 소멸/≤5초 → 이 경주 마감으로 판단(경주당 1회 통지).
    if (_hadCountdown && rk && _finishNotifiedRk !== rk) {
      _finishNotifiedRk = rk;
      _hadCountdown = false;
      try { chrome.runtime.sendMessage({ type: 'RACE_FINISHED', raceKey: rk }, () => void chrome.runtime.lastError); } catch (_) { /* */ }
      console.log(`[경주마감] 카운트다운 소멸 → 다음경주 전환 통지: "${rk}"`);
    }
  }
  async function watchRaceChange() {
    let rk = '';
    try { rk = extractRaceKey(); } catch (_) { return; }
    if (!rk) return;                                   // 감지 실패 → 기존(수동) raceKey 유지
    if (rk === _lastWatchedRace) return;               // 이번 페이지에서 변경 없음
    _lastWatchedRace = rk;
    _hadCountdown = false; _finishNotifiedRk = '';      // 새 경주 → 마감 감지 상태 초기화
    const { raceKey: cur } = await getSettings();
    if (rk === (cur || '').trim()) return;             // 저장된 raceKey 와 이미 동일
    await new Promise((r) => chrome.storage.local.set({ raceKey: rk }, r));
    console.log(`[경주감지] 현재 경주 자동 감지 → raceKey 업데이트: "${rk}" (이전 "${cur || ''}")`);
    try { _postBoardHint(rk); } catch (_) { /* */ }   // [배당판 추종] 경주 변경 즉시 분석기에 알림(자동 동기화)
    try { _inRk = ''; setTimeout(_autoInstantWatch, 300); } catch (_) { /* */ }   // [즉시분석 자동화] 경주 전환 감지 즉시 1회 실행(10초 tick 대기 없이)
    setTripleProgress(`🆕 경주 자동 감지: ${rk} — 수집합니다…`, false);
    // [발주감지] 경주가 바뀌면 발주시각도 새 경주 기준으로 자동 갱신(수동값보다 우선)
    try { await autoDetectPostTime(rk); } catch (_) { /* */ }
    // [4번·경륜 자동수집 상태] 경륜/경정/바이크면 자동수집 상태 표시(경주 바뀌어도 자동 유지 안내)
    try {
      const { sport: _ps } = await getSettings();
      const _es = resolveSport(_ps, rk);
      if (_es === 'cycle' || _es === 'boat' || _es === 'bike') _setKeirinAutoStatus(rk, _es, '경주 변경');
    } catch (_) { /* */ }
    // 서버에 자동 전송. storage.raceKey 는 위에서 이미 갱신됐고(팝업/분석기용) 자동엔진은 board-first
    //   로 배당판을 따라가므로, 진행중이면 다음 tick 에 맡긴다. 직접 수집이 실패하면 _lastWatchedRace 를
    //   비워 다음 tick 에 재시도(수집이 한 번은 반드시 반영되도록).
    // [자동전송 역할 재정의] NAR·경륜은 서버 전담 → race-change 자동수집도 차단(raceKey 표시 갱신은 위에서 이미 완료).
    if (!_autoRunning) {
      const _cat = await _currentCategory();
      _markCollectMode(_cat);
      if (_isServerCollectedCat(_cat)) {
        console.log('[역할재정의] ' + _cat + ' → race-change 자동수집 차단(서버 전담)');
      } else {
        collectTriple('race-change').catch(() => { _lastWatchedRace = ''; });
      }
    }
  }
  // [종목 자동감지 강화] URL 변경 또는 페이지 종목이 바뀌면 sport를 재감지하고, 직전 전송 종목과 다르면
  //   즉시 재수집을 유도 → 새 sport 태그가 서버에 전달되어 서버의 종목 전환 초기화(배당·전적)가 작동.
  //   (경마 배당판 → 경륜 배당판 전환처럼 사이트가 바뀔 때 이전 종목 태그가 이월되던 문제 차단.)
  async function watchSportChange() {
    try {
      const hrefChanged = (location.href !== _lastSportHref);
      let rk = '';
      try { rk = extractRaceKey(); } catch (_) { /* */ }
      const { sport: popupSport } = await getSettings();
      const eff = resolveSport(popupSport, rk);
      // 최초 1회(_lastSentSport 비어있음)는 기준값만 세팅(불필요한 재수집 방지)
      if (!_lastSentSport) { _lastSentSport = eff; _lastSportHref = location.href; return; }
      const sportChanged = (eff !== _lastSentSport);
      if (!hrefChanged && !sportChanged) return;
      _lastSportHref = location.href;
      if (sportChanged) {
        console.log(`[종목 자동감지] 종목 변경 감지: ${SPORT_LABEL[_lastSentSport] || _lastSentSport} → ${SPORT_LABEL[eff] || eff} (URL변경=${hrefChanged}) → 재수집`);
        _lastSentSport = eff;
        _lastWatchedRace = '';                          // 경주감지 상태도 리셋 → 다음 tick 확실히 새 수집
        if (!_autoRunning) {
          // [자동전송 역할 재정의] 바뀐 종목이 NAR·경륜이면 서버 전담 → sport-change 자동수집 차단
          const _cat = await _currentCategory();
          _markCollectMode(_cat);
          if (!_isServerCollectedCat(_cat)) collectTriple('sport-change').catch(() => { /* */ });
          else console.log('[역할재정의] ' + _cat + ' → sport-change 자동수집 차단(서버 전담)');
        }
      }
    } catch (_) { /* */ }
  }
  // ── [전체수집 자동 폴백] oddspark 미등록 경마장(소노다 등) 자동수집 ───────────────────
  //   문제: 서버 자동수집(_multi_bg_loop)은 oddspark 등록 경마장만 커버 → 소노다처럼 미등록 경마장은
  //         서버가 배당을 못 가져와, 매 경주 [전체 자동 수집] 버튼을 수동으로 눌러야 배당이 수집됐다.
  //   해결: 경주 전환 후 30초 내에 서버에 배당이 안 들어오면(analyze 404 waiting) 전체수집(collectTriple)을
  //         자동 실행하고 30초마다 반복 → 배당이 들어오면(analyze OK) 자동 중단. 오버레이에 진행 표시.
  //   안전: 커버 경마장(나고야 등)은 서버가 30초 내 배당 공급 → analyze OK → 폴백 미발동. 설령 발동돼도
  //         서버 triple_ingest 의 oddspark 커버 가드(v2.1.106)가 확장 중복 전송을 스킵(꼬임 없음).
  const _FB_WAIT_MS = 10000, _FB_REPEAT_MS = 10000;  // [수정3] 전환 후 10초 대기 + 이후 10초 반복(빠른 경주 대응·기존 30초→단축)
  let _fbRk = '', _fbSince = 0, _fbLastTry = 0, _fbActive = false, _fbDoneRk = '';
  // [배당판 추종] 현재 배당판 경주(raceKey)를 서버에 전달 → 분석기가 이 경주를 자동 추종(oddspark 최신 경주로 안 튐).
  //   경주 변경 즉시(watchRaceChange) + 주기(10초 tick)로 재전송(서버 힌트 TTL 90초 유지). autoSend 무관(수동모드에서도 동기화).
  async function _postBoardHint(rk) {
    try {
      if (!rk) { try { rk = extractRaceKey(); } catch (_) { rk = ''; } }
      if (!rk) return;
      var _sp = 'horse';
      try { var _s = await getSettings(); _sp = resolveSport(_s.sport, rk) || 'horse'; } catch (_) { /* */ }
      chrome.runtime.sendMessage({ type: 'BOARD_HINT', raceKey: rk, sport: _sp }, function () { void chrome.runtime.lastError; });
    } catch (_) { /* */ }
  }
  function _setFallbackOverlay(active, rk) {
    // 오버레이(overlay.js)가 읽어 "⚡ 전체수집 자동 실행 중..." 배너를 표시하도록 storage 에 상태 기록.
    try { chrome.storage.local.set({ autoFallback: { active: !!active, raceKey: rk || '', at: Date.now() } }); } catch (_) { /* */ }
  }

  // ── [수정2·한국경마 복승 자동수집] 자동전송(autoSend) 버튼 없이 한국 배당판 열면 30초마다 복승 자동수집 ──
  //   한국경마는 서버 직접수집 불가(asyukk DOM)이므로 확장이 담당. 기존 autoSend 게이트와 무관하게
  //   한국 감지 시 자동 시작(수동 버튼 불필요). 오버레이 배너 "🇰🇷 한국경마 복승 자동수집 중" 표시 + 경주 고정(board hint).
  let _krRk = '', _krLast = 0;
  const _KR_MS = 30000;                                  // 30초 주기
  function _setKoreaAutoOverlay(active, rk) {
    try { chrome.storage.local.set({ koreaAuto: { active: !!active, raceKey: rk || '', at: Date.now() } }); } catch (_) { /* */ }
  }
  async function _koreaAutoWatch() {
    try {
      // 한국 배당판인지 확정(현재 페이지/raceKey 기준). 카테고리='korea' 일 때만 동작.
      const _cat = await _currentCategory();
      if (_cat !== 'korea') {                            // 한국 아님 → 배너 끄고 종료(다른 종목 흐름 무영향)
        if (_krRk) { _krRk = ''; _setKoreaAutoOverlay(false, ''); }
        return;
      }
      let rk = '';
      try { rk = extractRaceKey(); } catch (_) { rk = ''; }
      if (!rk) { const s = await getSettings(); rk = (s.raceKey || '').trim(); }
      if (!rk) return;
      // [오버레이 자동전환] 한국 감지 즉시 detectedCategory·raceKey 기록 → 오버레이가 경정/경륜 잔존분석을
      //   바로 초기화하고 한국경마로 전환(수집 완료 30초 기다리지 않고 종목 불일치 클리어가 즉시 작동).
      try {
        chrome.storage.local.set({ detectedCategory: 'korea', detectedSport: 'horse', detectedAt: Date.now(), raceKey: rk });
      } catch (_) { /* */ }
      // [수정3·배당판 고정] 한국 경주를 분석기에 계속 알림(board hint)→ 분석기가 이 경주를 고정 추종(다른 경주로 안 튐).
      try { _postBoardHint(rk); } catch (_) { /* */ }
      if (rk !== _krRk) { _krRk = rk; _krLast = 0; }     // 경주 전환 → 즉시 1회 허용
      const now = Date.now();
      if (now - _krLast < _KR_MS) return;                // 30초 주기
      if (_autoRunning) return;                          // 다른 수집 진행중이면 스킵(중복 방지)
      _krLast = now;
      _setKoreaAutoOverlay(true, rk);
      setTripleProgress('🇰🇷 한국경마 복승 자동수집 중… (' + rk + ')', false);
      _autoRunning = true;
      try { await collectTriple('korea-auto'); }         // 복승만 수집(collectTripleByTabs 의 isKorea 게이트가 쌍승·삼복승 차단)
      catch (e) { console.warn('[한국자동수집] 오류', e); }
      finally { _autoRunning = false; }
    } catch (_) { /* */ }
  }
  function _serverHasOddsFor(rk) {
    // 서버 analyze 로 이 경주 배당 존재 여부 확인. res.ok=true → 배당 있음 / false → 없음(404 waiting) / null → 통신불명.
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage({ type: 'ANALYZE_TRIPLE', raceKey: rk || '' }, function (res) {
          if (chrome.runtime.lastError) { resolve(null); return; }
          resolve(!!(res && res.ok && res.data));
        });
      } catch (_) { resolve(null); }
    });
  }
  async function _autoFallbackWatch() {
    try {
      const { autoSend } = await getSettings();
      if (!autoSend) { if (_fbActive) { _fbActive = false; _setFallbackOverlay(false, ''); } return; }  // 자동전송 OFF=수동 존중
      let rk = '';
      try { rk = extractRaceKey(); } catch (_) { rk = ''; }
      if (!rk) { const s = await getSettings(); rk = (s.raceKey || '').trim(); }
      if (!rk) return;
      if (rk !== _fbRk) {                              // [1] 경주 전환 감지 → 30초 대기 타이머 리셋
        _fbRk = rk; _fbSince = Date.now(); _fbLastTry = 0;
        if (_fbActive) { _fbActive = false; _setFallbackOverlay(false, ''); }
        return;
      }
      if (rk === _fbDoneRk) return;                    // 이미 배당 확인된 경주 → analyze 반복호출 생략(churn 방지)
      const has = await _serverHasOddsFor(rk);
      if (has === true) {                             // [5] 배당 들어옴 → 폴백 중단(분석은 오버레이 20초 폴이 담당)
        _fbDoneRk = rk;
        if (_fbActive) { _fbActive = false; _setFallbackOverlay(false, rk); console.log('[전체수집 자동폴백] ' + rk + ' 배당 수신 → 폴백 종료'); }
        return;
      }
      if (has === null) return;                       // 통신 불명 → 이번 tick 보류
      const now = Date.now();
      if (now - _fbSince < _FB_WAIT_MS) return;       // [2] 아직 30초 대기 중
      if (now - _fbLastTry < _FB_REPEAT_MS) return;   // [4] 30초 반복 간격
      if (_autoRunning) return;                       // 다른 수집 진행중이면 스킵
      _fbLastTry = now; _fbActive = true;
      _setFallbackOverlay(true, rk);
      console.log('[전체수집 자동폴백] ' + rk + ' — 서버 배당 없음 30초+ → 전체수집 자동 실행(30초 반복·수동 버튼 불필요)');
      setTripleProgress('⚡ 전체수집 자동 실행 중… (' + rk + ')', false);
      _autoRunning = true;
      try { await collectTriple('auto-fallback'); }   // [3] 서버 게이트 없는 전체수집 경로(= 전체수집 버튼과 동일)
      catch (e) { console.warn('[전체수집 자동폴백] 오류', e); }
      finally { _autoRunning = false; }
    } catch (_) { /* */ }
  }

  // ── [즉시분석 자동화] 오버레이 ON일 때 asyukk 배당을 주기적 자동 수집 → 서버 전송 → 분석 ──────────
  //   문제: 즉시분석(asyukk 화면 배당 긁기→서버→분석)이 수동(팝업 🚨 버튼)으로만 실행돼, 사용자가 asyukk 배당판을
  //         보고 있어도 패널이 갱신 안 됨(배당판·패널 불일치). 해결: 오버레이 켜져있으면 경주 전환 즉시 1회 + 30초마다
  //         자동으로 collectTriple(asyukk 수집) 실행 → analyzeStatus 자동 갱신(collectTriple 내 수집→분석 연결).
  //   oddspark 우선: 서버가 이 경주를 oddspark 로 커버 중(analyze.oddsparkCovered=true)이면 asyukk 수집 생략
  //     (oddspark 가 권위 소스·보조로만·불필요한 탭클릭/덮어쓰기 방지). 미커버(몬베츠 등)만 asyukk 즉시분석 실행.
  const _INSTANT_MS = 30000;                           // [사용자] 30초 주기
  let _inRk = '', _inLast = 0;
  function _analyzeOnce(rk) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage({ type: 'ANALYZE_TRIPLE', raceKey: rk || '' }, function (res) {
          if (chrome.runtime.lastError) { resolve(null); return; }
          resolve(res || null);
        });
      } catch (_) { resolve(null); }
    });
  }
  async function _autoInstantWatch() {
    try {
      const st = await new Promise((r) => chrome.storage.local.get({ overlayEnabled: false, overlayKill: false }, r));
      if (!st.overlayEnabled || st.overlayKill) return;   // [사용자] 오버레이 켜져있을 때만
      let rk = '';
      try { rk = extractRaceKey(); } catch (_) { rk = ''; }
      if (!rk) { const s = await getSettings(); rk = (s.raceKey || '').trim(); }
      if (!rk) return;
      const now = Date.now();
      const immediate = (rk !== _inRk);                   // [사용자] 경주 전환 시 즉시 1회
      if (immediate) { _inRk = rk; _inLast = 0; }
      if (!immediate && (now - _inLast) < _INSTANT_MS) return;   // [사용자] 이후 30초 주기
      _inLast = now;
      if (_autoRunning) return;
      // [oddspark 우선] 커버리지 확인 겸 패널 최신화 — analyze 1회. 커버 중이면 asyukk 수집 생략(보조로만).
      const ar = await _analyzeOnce(rk);
      if (ar && ar.ok && ar.data) {
        try { chrome.storage.local.set({ analyzeStatus: { data: ar.data, at: now } }); } catch (_) { /* */ }   // 패널 즉시 반영
        if (ar.data.oddsparkCovered === true) {
          console.log('[즉시분석 자동] ' + rk + ' — oddspark 커버 중 → asyukk 수집 생략(oddspark 우선·보조로만)');
          return;
        }
      }
      // oddspark 미커버(또는 아직 데이터 없음) → asyukk 즉시분석(수집→서버→분석) 자동 실행
      _autoRunning = true;
      console.log('[즉시분석 자동] ' + rk + ' — asyukk 배당 자동 수집(오버레이 ON·' + (immediate ? '경주전환 즉시' : '30초 주기') + ')');
      setTripleProgress('🔄 즉시분석 자동 실행 중… (' + rk + ')', false);
      try { await collectTriple('auto-instant'); } catch (e) { console.warn('[즉시분석 자동] 오류', e); }
      finally { _autoRunning = false; }
    } catch (_) { /* */ }
  }

  function startRaceWatch() {
    if (_raceWatchTimer) clearInterval(_raceWatchTimer);
    setTimeout(watchRaceChange, 1500);                 // 로드 직후 1회
    setTimeout(watchSportChange, 1800);                // [종목감지] 로드 직후 1회(기준값 세팅)
    setTimeout(_autoFallbackWatch, 3000);              // [전체수집 자동폴백] 로드 직후 1회(전환 기준 세팅)
    setTimeout(_postBoardHint, 2200);                  // [배당판 추종] 로드 직후 1회(분석기 즉시 동기화)
    setTimeout(_autoInstantWatch, 3500);               // [즉시분석 자동화] 로드 직후 1회(오버레이 ON이면 asyukk 수집)
    setTimeout(_koreaAutoWatch, 4000);                 // [수정2·한국 자동수집] 로드 직후 1회(한국이면 즉시 시작)
    _raceWatchTimer = setInterval(() => {              // 10초마다 경주 변경 감지 + 마감 감지(자동추종 반응성)
      watchRaceChange();
      watchSportChange();                              // [종목 자동감지 강화] URL·종목 전환 감지 → 재수집
      _finishWatchdog();                               // [다음경주 자동전환] 카운트다운 소멸 = 마감 통지
      _autoFallbackWatch();                            // [전체수집 자동폴백] 10초+ 배당 없으면 전체수집 자동 실행
      _postBoardHint();                                // [배당판 추종] 배당판 경주를 분석기에 계속 알림(힌트 TTL 유지)
      _autoInstantWatch();                             // [즉시분석 자동화] 오버레이 ON·30초 주기 asyukk 수집(커버 경주는 생략)
      _koreaAutoWatch();                               // [수정2·한국 자동수집] 한국 배당판이면 30초마다 복승 자동수집(버튼 불필요)
    }, 10000);
  }

  restartLoop();
  startRaceWatch();   // [1번] 경주 자동 감지 시작

  // [경륜 배당판 자동 수집 트리거] 경륜 배당판(asyukk34 등)에서 자동전송(autoSend) ON이면 수동 버튼 없이 즉시 수집.
  //   발동: ①배당판 로드 ②경륜 탭 클릭(SPA 전환·클릭 디바운스) ③탭 포커스 복귀 — 모두 종목=경륜일 때만.
  //   경마·경정 등 다른 종목·autoSend OFF는 기존 동작 유지. 4초 쿨다운·재진입 가드로 과호출 방지(기존 수집 무영향).
  let _lastKeirinKick = 0;
  async function _maybeKeirinKickstart(reason) {
    try {
      const now = Date.now();
      if (now - _lastKeirinKick < 4000) return;       // 쿨다운(과호출 방지)
      const { autoSend, sport } = await getSettings();
      if (!autoSend) return;                          // 자동전송 OFF면 존중(수동)
      let rk = '';
      try { rk = extractRaceKey(); } catch (_) { /* */ }
      // [4번] 경륜·경정·바이크(사설 asyukk 배당판) 전부 즉시 수집 — 배당판 열리는 즉시 조기 수집 시작(T-10분+ 흐름 포착).
      var _sp = resolveSport(sport, rk);
      if (_sp !== 'cycle' && _sp !== 'boat' && _sp !== 'bike') return;
      // [자동전송 역할 재정의] 경륜(cycle)은 서버 oddspark 전담 → 확장 즉시수집 차단. 경정·바이크는 서버 미커버라 유지.
      if (_sp === 'cycle') { _markCollectMode('cycle'); console.log('[역할재정의] 경륜 → 확장 즉시수집 차단(서버 전담)'); return; }
      if (_autoRunning) return;                       // 이미 수집 중이면 스킵
      _lastKeirinKick = now;
      console.log('[' + _sp + ' 자동수집] ' + reason + ' + 자동전송 ON → 배당판 열리는 즉시 수집(수동 버튼 불필요)');
      _setKeirinAutoStatus(rk, _sp, reason);          // [4번] 상태 표시(🚴 경륜 자동수집 중 · 경주 · N초 간격)
      _autoRunning = true;
      try { await collectTriple('auto'); } catch (e) { console.warn('[경륜 자동수집] 오류', e); }
      finally { _autoRunning = false; }
    } catch (_) { /* */ }
  }
  // [4번] 경륜·경정·바이크 자동수집 상태 저장 + 팝업 표시("🚴 경륜 자동수집 중 · 오다와라 5경주 · 30초 간격")
  function _setKeirinAutoStatus(rk, sport, reason) {
    try {
      getSettings().then(function (s) {
        var iv = s.intervalSec || 30;
        var label = { cycle: '🚴 경륜', boat: '🚤 경정', bike: '🏍 바이크' }[sport] || '자동';
        try {
          chrome.storage.local.set({ keirinAutoStatus: {
            active: true, raceKey: rk || '', sport: sport, label: label,
            intervalSec: iv, reason: reason || '', t: Date.now() } });
        } catch (_) { /* */ }
        setTripleProgress(label + ' 자동수집 중 · ' + (rk || '경주 감지 중') + ' · ' + iv + '초 간격', false);
      });
    } catch (_) { /* */ }
  }
  setTimeout(() => _maybeKeirinKickstart('배당판 로드'), 1300);   // 로드 직후(종목 감지 안정화 대기)
  // [탭 클릭 즉시 트리거] asyukk 경륜·경정·바이크 탭 클릭 등 SPA 전환 시 디바운스 후 재확인(리로드 없이 종목 바뀌는 경우 대응)
  let _keirinClickTimer = null;
  document.addEventListener('click', () => {
    if (_keirinClickTimer) clearTimeout(_keirinClickTimer);
    _keirinClickTimer = setTimeout(() => _maybeKeirinKickstart('탭 클릭'), 700);
  }, true);
  // [탭 포커스 복귀] 다른 탭 갔다 사설 배당판(경륜·경정·바이크)으로 돌아오면 재확인
  document.addEventListener('visibilitychange', () => { if (!document.hidden) _maybeKeirinKickstart('탭 포커스'); });

  // ── [경륜 URL 자동감지] 경주(URL) 변경 시 버튼 없이 즉시 재수집 ────────────────────
  //   문제: 경륜 배당판에서 '다음 경주' 탭 클릭 등으로 URL/경주번호가 바뀌어도 자동 감지가 안 돼
  //         매 경기 [전체 자동 수집]을 수동으로 눌러야 했음.
  //   해결: ①popstate/hashchange ②2초 폴링(어떤 방식의 URL 변경도 확실히 포착)로 href 변경을 감지 →
  //         경주감지 상태를 리셋하고 watchRaceChange + 경륜 킥스타트를 즉시 실행(수동 버튼 불필요).
  //   경마·지방경마 등 전 종목에 안전(watchRaceChange 는 실제 경주 변경시에만 수집·킥스타트는 경륜/경정/바이크만).
  let _lastUrlSeen = location.href;
  function _onUrlChanged(reason) {
    if (location.href === _lastUrlSeen) return;
    const prev = _lastUrlSeen;
    _lastUrlSeen = location.href;
    console.log('[URL 자동감지] ' + reason + ': 경주 URL 변경 → 즉시 재수집 ("' + prev + '" → "' + location.href + '")');
    _lastWatchedRace = '';       // 강제 재감지(다음 tick 확실히 새 경주로 인식)
    _lastKeirinKick = 0;         // 킥스타트 쿨다운 해제(URL 변경 = 확실한 새 경주 신호)
    // 새 경주 배당판 로드 여유 후 재수집(watchRaceChange 가 전 종목의 실제 경주변경 시 수집·상태갱신).
    setTimeout(() => { try { watchRaceChange(); } catch (_) { /* */ } }, 500);
  }
  window.addEventListener('popstate', () => _onUrlChanged('popstate'));
  window.addEventListener('hashchange', () => _onUrlChanged('hashchange'));
  setInterval(() => _onUrlChanged('url-poll'), 2000);   // 폴백 폴링(2초)

  // [2번] 결과 페이지면 로드 직후 1회 자동 전송 (URL result/성적표 감지)
  if (isResultPage()) {
    setTimeout(() => { sendResults('auto-result').catch(() => {}); }, 800);
  }

  // [출마표2] keiba.go.jp DebaTable 페이지면 로드 직후 전적 자동 추출·전송 + 파라미터 저장
  if (isDebaPage()) {
    setTimeout(() => { collectDebaOnPage().catch((e) => console.warn('[전적수집] DebaTable 자동수집 오류', e)); }, 900);
  }
})();
