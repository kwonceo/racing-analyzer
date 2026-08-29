/* [A안 회귀] 열린 배당판 탭 **전부**에서 수집하는가 · 한 탭 마감이 전체를 죽이지 않는가.
 * 🔴 원칙 12 — 실제 background.js 코드를 잘라 eval 한다(픽스처가 아니다).
 * 🔴 원칙 17 — 발동(둘 다 마감)·미발동(하나만 마감) 둘 다 확인한다.
 * 🔴 원칙 19 — 추출에 실패하면 **조용히 통과하지 않고** rc=1 로 죽는다.
 * 실행: node tests/run_multitab_collect.js
 */
// 🔴 원칙 17 — 발동·미발동 둘 다 확인한다
const fs=require('fs');
const src=fs.readFileSync(require("path").join(__dirname,"..","chrome-extension","background.js"),"utf8");
const a=src.indexOf('const _ODDS_TAB_URLS');
const b=src.indexOf('async function _onRaceClosed');
const b2=src.indexOf('async function _forceAnalyze');
if (a < 0 || b2 < 0 || b2 <= a) { console.error('🔴 background.js 에서 수집 블록을 못 찾았다 — 테스트가 무의미하다'); process.exit(1); }
const block=src.slice(a, b2);
if (!/MULTI_TAB_COLLECT/.test(block) || !/_findOddsTabs/.test(block)) { console.error('🔴 A안 코드가 블록에 없다'); process.exit(1); }
let TABS=[], REPLY={}, closedCalls=[], status=[];
global.chrome={ tabs:{ query:async()=>TABS,
  sendMessage:async(id)=>{ const r=REPLY[id]; if(r==='THROW') throw new Error('no receiver'); return r; } } };
function _setAutoStatus(s){ status.push(s); }
async function _onRaceClosed(reason){ closedCalls.push(reason||''); }
eval(block.replace(/async function _onRaceClosed[\s\S]*/, ''));

async function run(name, tabs, reply, expClosed, expTicks){
  TABS=tabs; REPLY=reply; closedCalls=[]; status=[];
  const r=await _collectOnce();
  const got=closedCalls.length;
  const warned=status.some(s=>s.warn);
  const ok = (got===expClosed);
  console.log(`  ${ok?'✅':'🔴'} ${name.padEnd(34)} _onRaceClosed ${got}회 (기대 ${expClosed}) · 반환 ${r?JSON.stringify(r).slice(0,28):'null'}${warned?' · 경고':''}`);
  return ok;
}
(async()=>{
  let ok=0,n=0;
  const T=(id,url)=>({id,url});
  n++; ok+= await run('탭2 · 둘 다 진행 중',[T(1,'https://ks1.dke-d11diw.site/odds'),T(2,'https://www.keiba.go.jp/x')],{1:{rk:'서울 1'},2:{rk:'나고야 1'}},0);
  n++; ok+= await run('🔴 탭2 · 하나만 마감',[T(1,'https://ks1.dke-d11diw.site/odds'),T(2,'https://www.keiba.go.jp/x')],{1:{closed:true,closeReason:'발매 마감'},2:{rk:'나고야 1'}},0);
  n++; ok+= await run('탭2 · 둘 다 마감',[T(1,'https://ks1.dke-d11diw.site/odds'),T(2,'https://www.keiba.go.jp/x')],{1:{closed:true,closeReason:'발매 마감'},2:{closed:true,closeReason:'발매 마감'}},1);
  n++; ok+= await run('탭1 · 마감(종전과 동일)',[T(1,'https://ks1.dke-d11diw.site/odds')],{1:{closed:true,closeReason:'DOM'}},1);
  n++; ok+= await run('탭2 · 하나는 응답 없음',[T(1,'https://ks1.dke-d11diw.site/odds'),T(2,'https://www.keiba.go.jp/x')],{1:'THROW',2:{rk:'나고야 1'}},0);
  n++; ok+= await run('탭2 · 전부 응답 없음',[T(1,'https://ks1.dke-d11diw.site/odds'),T(2,'https://www.keiba.go.jp/x')],{1:'THROW',2:'THROW'},0);
  n++; ok+= await run('탭 0개',[],{},0);
  console.log(`\n  ${ok}/${n}`);
  process.exit(ok===n?0:1);
})();
