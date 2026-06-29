/* ===== history.js — 기록 / 통계 / 누적 학습 DB =====
 * 경주 결과를 localStorage("bmed_history")에 누적 저장하고,
 * 적중률·수익 통계를 계산한다.
 *
 * 레코드 형태:
 * {
 *   id, date, region: '한국'|'일본', raceTitle,
 *   bets: [{type, combo, confidence}],   // 추천했던 베팅
 *   result: [1,3,5],                     // 실제 착순 (마번 순서)
 *   hit: boolean, payout: number, stake: number
 * }
 */
(function (global) {
  const LS_KEY = 'bmed_history';

  function _load() {
    try { return JSON.parse(localStorage.getItem(LS_KEY) || '[]'); }
    catch (_) { return []; }
  }
  function _save(list) {
    localStorage.setItem(LS_KEY, JSON.stringify(list));
  }

  /** id 생성 (Math.random 대체: 카운터 기반) */
  function _genId(list) {
    const max = list.reduce((m, r) => Math.max(m, r.id || 0), 0);
    return max + 1;
  }

  function all() { return _load(); }

  /** 결과 레코드 추가 */
  function addResult(record) {
    const list = _load();
    record.id = _genId(list);
    record.savedAt = new Date().toISOString();
    list.push(record);
    _save(list);
    return record;
  }

  function remove(id) {
    _save(_load().filter((r) => r.id !== id));
  }

  function clear() { _save([]); }

  /** 베팅 적중 여부 판정
   * 복승: combo 2마리가 모두 result 상위 2착 안 (정확히는 1·2착 중 두 자리)
   * 삼복승: combo 3마리가 모두 result 상위 3착 안
   */
  function judgeHit(bet, result) {
    if (!result || result.length === 0) return false;
    const top2 = result.slice(0, 2);
    const top3 = result.slice(0, 3);
    if (bet.type === '복승') {
      return bet.combo.length === 2 && bet.combo.every((n) => top2.includes(n));
    }
    if (bet.type === '삼복승') {
      return bet.combo.length === 3 && bet.combo.every((n) => top3.includes(n));
    }
    return false;
  }

  /** 배당대 분류 (recOdds 기준) */
  function oddsBand(o) {
    if (o == null) return '미분류';
    if (o < 3) return '3배 미만';
    if (o < 10) return '3~10배';
    if (o < 30) return '10~30배';
    return '30배 이상';
  }
  const ODDS_BANDS = ['3배 미만', '3~10배', '10~30배', '30배 이상', '미분류'];

  /** 통계 집계 (기본 + 이상감지 효과 + 배당대별 + 월별) */
  function stats() {
    const list = _load();
    const total = list.length;
    let hits = 0, stakeSum = 0, payoutSum = 0;
    const byRegion = {};
    const byType = { 복승: { n: 0, hit: 0 }, 삼복승: { n: 0, hit: 0 } };
    const byAnomaly = { with: { n: 0, hit: 0 }, without: { n: 0, hit: 0 } };
    const byOddsBand = {};
    ODDS_BANDS.forEach((b) => { byOddsBand[b] = { n: 0, hit: 0 }; });
    const byMonth = {};
    // [5번] 이상감지 패턴별: 급락50%+ / 복승불일치 / 배당압축 / 감지없음
    const mk = () => ({ n: 0, hit: 0, stake: 0, payout: 0 });
    const bySignal = { drop50: mk(), mismatch: mk(), squeeze: mk(), none: mk() };

    for (const r of list) {
      const hit = r.hit ?? (r.bets || []).some((b) => judgeHit(b, r.result));
      if (hit) hits++;
      const st = r.stake || 0, po = r.payout || 0;
      stakeSum += st; payoutSum += po;
      byRegion[r.region] = byRegion[r.region] || { n: 0, hit: 0 };
      byRegion[r.region].n++;
      if (hit) byRegion[r.region].hit++;
      for (const b of r.bets || []) {
        if (byType[b.type]) {
          byType[b.type].n++;
          if (judgeHit(b, r.result)) byType[b.type].hit++;
        }
      }
      const ak = r.hadAnomaly ? 'with' : 'without';
      byAnomaly[ak].n++; if (hit) byAnomaly[ak].hit++;
      const bb = oddsBand(r.recOdds); byOddsBand[bb].n++; if (hit) byOddsBand[bb].hit++;
      const m = (r.date || '').slice(0, 7) || '기타';
      byMonth[m] = byMonth[m] || { stake: 0, payout: 0 };
      byMonth[m].stake += st; byMonth[m].payout += po;
      // 이상감지 패턴 집계 (한 경주가 여러 패턴에 해당 가능, 없으면 none)
      const sg = r.signals || {};
      let any = false;
      ['drop50', 'mismatch', 'squeeze'].forEach((k) => {
        if (sg[k]) { any = true; const s = bySignal[k]; s.n++; if (hit) s.hit++; s.stake += st; s.payout += po; }
      });
      if (!any) { const s = bySignal.none; s.n++; if (hit) s.hit++; s.stake += st; s.payout += po; }
    }

    const rate = (o) => (o.n ? Math.round((o.hit / o.n) * 1000) / 10 : 0);
    const roiOf = (o) => (o.stake ? Math.round(((o.payout - o.stake) / o.stake) * 1000) / 10 : 0);
    const withRate = (obj) => Object.fromEntries(
      Object.entries(obj).map(([k, v]) => [k, Object.assign({}, v, { rate: rate(v) })]));
    const withRateRoi = (obj) => Object.fromEntries(
      Object.entries(obj).map(([k, v]) => [k, Object.assign({}, v, { rate: rate(v), roi: roiOf(v) })]));

    return {
      total,
      hits,
      hitRate: total ? Math.round((hits / total) * 1000) / 10 : 0,
      stakeSum,
      payoutSum,
      net: payoutSum - stakeSum,
      roi: stakeSum ? Math.round(((payoutSum - stakeSum) / stakeSum) * 1000) / 10 : 0,
      byRegion,
      byType,
      byAnomaly: withRate(byAnomaly),
      byOddsBand: withRate(byOddsBand),
      byMonth,
      bySignal: withRateRoi(bySignal),
    };
  }

  global.History = { all, addResult, remove, clear, judgeHit, stats };
})(window);
