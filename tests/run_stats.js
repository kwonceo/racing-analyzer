/* 3단계: 결과기록 → 적중 자동 판정 → 통계 업데이트 검증 (History 모듈)
 * 실행: node tests/run_stats.js
 */
const fs = require('fs'), path = require('path');
const store = {};
global.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } };
global.window = global;
eval(fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'history.js'), 'utf8'));

let pass = 0, fail = 0;
const chk = (label, ok, extra='') => { console.log(`  ${ok?'✅':'❌'} ${label}${extra?' — '+extra:''}`); ok?pass++:fail++; };

const fix = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'ooi_r2.json'), 'utf8'));
const vr = fix.virtualResult;                  // {placing:[4,6,1], stake:50000, winOdds:9.6}
const result = vr.placing;

// 통합분석이 추천했던 베팅(결정적): 복승 4-6, 4-1 / 삼복승 4-6-1, 4-6-8(보험)
const bets = [
  { type: '복승', combo: [4, 6] },
  { type: '복승', combo: [4, 1] },
  { type: '삼복승', combo: [4, 6, 1] },
  { type: '삼복승', combo: [4, 6, 8] },
];

console.log('='.repeat(60));
console.log('[3단계] 결과기록 → 적중 판정 → 통계');
console.log('='.repeat(60));
console.log(`가상 결과: 1착 ${result[0]} / 2착 ${result[1]} / 3착 ${result[2]} · 투자 ${vr.stake.toLocaleString()}원`);

// 적중 자동 판정
chk('복승 4-6 적중 판정', History.judgeHit({type:'복승',combo:[4,6]}, result) === true);
chk('복승 4-1 미적중 판정(1번 3착)', History.judgeHit({type:'복승',combo:[4,1]}, result) === false);
chk('삼복승 4-6-1 적중 판정', History.judgeHit({type:'삼복승',combo:[4,6,1]}, result) === true);
chk('삼복승 보험 4-6-8 미적중', History.judgeHit({type:'삼복승',combo:[4,6,8]}, result) === false);

// 복승 4-6 적중 수익: 베팅액 21,500(43%) × 9.6배
const stake = vr.stake, payout = Math.round(stake * 0.43 * vr.winOdds);
const rec = History.addResult({
  date: '2026-06-29', region: '일본', raceTitle: fix.raceKey,
  bets, result, hit: bets.some((b) => History.judgeHit(b, result)),
  stake, payout, hadAnomaly: true, recOdds: 2.3,
});
chk('레코드 저장 + 적중 자동 기록', rec.hit === true);

const s = History.stats();
console.log('\n[통계 대시보드]');
console.log(`  총경주 ${s.total} · 적중 ${s.hits} · 적중률 ${s.hitRate}%`);
console.log(`  총투자 ${s.stakeSum.toLocaleString()} · 총수익 ${s.payoutSum.toLocaleString()} · 순손익 ${s.net.toLocaleString()} · ROI ${s.roi}%`);
console.log(`  베팅종류별: 복승 ${s.byType['복승'].hit}/${s.byType['복승'].n} · 삼복승 ${s.byType['삼복승'].hit}/${s.byType['삼복승'].n}`);
console.log(`  이상감지효과: 🔴 ${s.byAnomaly.with.rate}% (${s.byAnomaly.with.hit}/${s.byAnomaly.with.n})`);
console.log(`  배당대: ${Object.entries(s.byOddsBand).filter(([,v])=>v.n>0).map(([k,v])=>k+' '+v.hit+'/'+v.n).join(', ')}`);

chk('통계 총경주 1', s.total === 1);
chk('통계 적중률 100%', s.hitRate === 100);
chk('총투자 50,000', s.stakeSum === 50000);
chk(`총수익 ${payout.toLocaleString()}`, s.payoutSum === payout);
chk(`순손익 +${(payout-50000).toLocaleString()}`, s.net === payout - 50000);
chk('복승 1/2 적중', s.byType['복승'].hit === 1 && s.byType['복승'].n === 2);
chk('삼복승 1/2 적중', s.byType['삼복승'].hit === 1 && s.byType['삼복승'].n === 2);
chk('이상감지 효과 집계(🔴 100%)', s.byAnomaly.with.rate === 100 && s.byAnomaly.with.n === 1);
chk('배당대 3배미만 집계', s.byOddsBand['3배 미만'].n === 1);
chk('월별 2026-06 집계', !!s.byMonth['2026-06'] && s.byMonth['2026-06'].payout === payout);

console.log('\n' + '='.repeat(60));
console.log(`결과: 통과 ${pass} / 실패 ${fail}`);
console.log('='.repeat(60));
process.exit(fail ? 1 : 0);
