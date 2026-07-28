# ══════════════════════════════════════════════════════════════════════════════
#  BMED 적중왕 — 모바일 관리 패널  /admin
#  app.py 끝부분에 아래 두 줄 추가:
#    from admin_page import admin_bp
#    app.register_blueprint(admin_bp)
# ══════════════════════════════════════════════════════════════════════════════
from flask import Blueprint, jsonify, request
import os, json, time, glob
from datetime import datetime, timedelta

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

_BASE   = os.path.dirname(__file__)
_DATA   = os.path.join(_BASE, "data")
_ALOG   = os.path.join(_DATA, "analysis_log")
_ODDS_H = os.path.join(_DATA, "odds_history")
_KAKAO  = os.path.join(_DATA, "kakao_sent_state.json")
_DARK   = os.path.join(_DATA, "dark_horse_stats.json")
_TRIPLE = os.path.join(_BASE, "triple_store.json")
_SCHED  = os.path.join(_DATA, "today_schedule.json")


def _jload(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def _today():
    return datetime.now().strftime("%Y_%m_%d")


# ─────────────────────────────────────────────────────────────────────────────
#  HTML 페이지
# ─────────────────────────────────────────────────────────────────────────────
ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no"/>
<title>적중왕 관리</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent;}
:root{
  --bg:#09090f;--bg2:#13131f;--bg3:#1c1c2e;
  --accent:#00e676;--accent2:#00b0ff;--warn:#ff6d00;--danger:#ff1744;
  --text:#e8e8f0;--muted:#7070a0;--border:#2a2a42;
  --card-r:12px;
}
html,body{height:100%;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;overflow-x:hidden;}

/* ── 상단 헤더 ── */
header{position:fixed;top:0;left:0;right:0;z-index:100;background:var(--bg2);border-bottom:1px solid var(--border);padding:12px 16px;display:flex;align-items:center;justify-content:space-between;}
header h1{font-size:16px;font-weight:700;letter-spacing:.5px;color:var(--accent);}
#srv-dot{width:8px;height:8px;border-radius:50%;background:var(--muted);display:inline-block;margin-right:6px;}
#srv-dot.ok{background:var(--accent);}
#srv-dot.err{background:var(--danger);}
#srv-time{font-size:11px;color:var(--muted);}

/* ── 탭 콘텐츠 ── */
main{padding:60px 0 72px;min-height:100%;}
.tab-content{display:none;padding:12px;}
.tab-content.active{display:block;}

/* ── 하단 내비 ── */
nav{position:fixed;bottom:0;left:0;right:0;z-index:100;background:var(--bg2);border-top:1px solid var(--border);display:flex;}
nav button{flex:1;background:none;border:none;color:var(--muted);padding:10px 0 8px;font-size:10px;cursor:pointer;display:flex;flex-direction:column;align-items:center;gap:3px;transition:color .15s;}
nav button .ico{font-size:20px;line-height:1;}
nav button.active{color:var(--accent);}

/* ── 카드 ── */
.card{background:var(--bg2);border:1px solid var(--border);border-radius:var(--card-r);padding:14px;margin-bottom:10px;}
.card-title{font-size:11px;font-weight:700;letter-spacing:.8px;text-transform:uppercase;color:var(--muted);margin-bottom:10px;}

/* ── 상태 그리드 ── */
.stat-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;}
.stat-box{background:var(--bg3);border-radius:8px;padding:12px;text-align:center;}
.stat-val{font-size:24px;font-weight:800;color:var(--accent);line-height:1;}
.stat-val.warn{color:var(--warn);}
.stat-val.danger{color:var(--danger);}
.stat-lab{font-size:10px;color:var(--muted);margin-top:4px;}

/* ── 버튼 ── */
.btn{display:block;width:100%;padding:13px;border:none;border-radius:8px;font-size:14px;font-weight:700;cursor:pointer;letter-spacing:.3px;transition:opacity .15s;}
.btn:active{opacity:.75;}
.btn-primary{background:var(--accent);color:#000;}
.btn-info{background:var(--accent2);color:#000;}
.btn-warn{background:var(--warn);color:#fff;}
.btn-danger{background:var(--danger);color:#fff;}
.btn-ghost{background:var(--bg3);color:var(--text);border:1px solid var(--border);}
.btn+.btn{margin-top:8px;}

/* ── 경주 목록 ── */
.race-item{display:flex;align-items:center;gap:10px;padding:11px 14px;background:var(--bg2);border:1px solid var(--border);border-radius:8px;margin-bottom:7px;cursor:pointer;}
.race-item:active{background:var(--bg3);}
.race-sport{font-size:9px;font-weight:700;padding:3px 6px;border-radius:4px;background:var(--bg3);color:var(--muted);white-space:nowrap;}
.race-sport.kra{background:#1a2a1a;color:var(--accent);}
.race-name{flex:1;font-size:13px;font-weight:600;}
.race-combo{font-size:11px;color:var(--muted);}
.race-badges{display:flex;gap:4px;flex-wrap:wrap;}
.badge{font-size:9px;padding:2px 5px;border-radius:4px;font-weight:700;}
.badge-hit{background:#1a2e1a;color:var(--accent);}
.badge-miss{background:#2e1a1a;color:var(--danger);}
.badge-old{background:#2a2a1a;color:#aaa;}
.badge-no-result{background:var(--bg3);color:var(--muted);}

/* ── 상세 패널 ── */
.detail-panel{background:var(--bg2);border:1px solid var(--border);border-radius:var(--card-r);padding:14px;margin-top:10px;display:none;}
.detail-panel.open{display:block;}
.detail-row{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--border);font-size:12px;}
.detail-row:last-child{border-bottom:none;}
.detail-key{color:var(--muted);}
.detail-val{font-weight:600;text-align:right;}

/* ── 결과 입력 폼 ── */
.input-row{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:10px;}
input[type=number]{background:var(--bg3);border:1px solid var(--border);border-radius:6px;color:var(--text);padding:10px;font-size:16px;text-align:center;width:100%;}
input[type=number]:focus{outline:none;border-color:var(--accent);}
input[type=text]{background:var(--bg3);border:1px solid var(--border);border-radius:6px;color:var(--text);padding:10px;font-size:14px;width:100%;margin-bottom:8px;}
input[type=text]:focus{outline:none;border-color:var(--accent);}
label{font-size:11px;color:var(--muted);margin-bottom:4px;display:block;}

/* ── 로그 ── */
.log-box{background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:10px;font-family:monospace;font-size:10px;color:#8888bb;height:200px;overflow-y:auto;white-space:pre-wrap;word-break:break-all;}

/* ── 통계 바 ── */
.stat-bar{margin-bottom:8px;}
.stat-bar-label{display:flex;justify-content:space-between;font-size:11px;margin-bottom:3px;}
.stat-bar-label .name{color:var(--text);}
.stat-bar-label .val{color:var(--accent);font-weight:700;}
.bar-track{background:var(--bg3);border-radius:4px;height:6px;overflow:hidden;}
.bar-fill{height:100%;background:var(--accent);border-radius:4px;transition:width .4s;}

/* ── 토스트 ── */
#toast{position:fixed;bottom:84px;left:50%;transform:translateX(-50%);background:#fff;color:#000;padding:10px 18px;border-radius:20px;font-size:13px;font-weight:700;z-index:999;opacity:0;transition:opacity .3s;pointer-events:none;white-space:nowrap;}
#toast.show{opacity:1;}

/* ── 타임라인 ── */
.timeline{list-style:none;position:relative;padding-left:20px;}
.timeline::before{content:'';position:absolute;left:6px;top:0;bottom:0;width:1px;background:var(--border);}
.timeline li{position:relative;margin-bottom:8px;font-size:11px;}
.timeline li::before{content:'';position:absolute;left:-14px;top:4px;width:6px;height:6px;border-radius:50%;background:var(--accent2);}
.timeline .t-time{color:var(--muted);margin-right:6px;}
.timeline .t-combo{font-weight:700;color:var(--text);}

/* 로딩 스피너 */
.spin{display:inline-block;width:16px;height:16px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .7s linear infinite;vertical-align:middle;}
@keyframes spin{to{transform:rotate(360deg);}}

.section-sep{height:1px;background:var(--border);margin:12px 0;}
.empty{text-align:center;color:var(--muted);padding:24px;font-size:12px;}
</style>
</head>
<body>
<header>
  <h1><span id="srv-dot"></span>적중왕 관리</h1>
  <span id="srv-time">--:--:--</span>
</header>

<main>
  <!-- ① 현황 -->
  <div id="tab-status" class="tab-content active">
    <div class="card">
      <div class="card-title">서버 현황</div>
      <div class="stat-grid" id="status-grid">
        <div class="stat-box"><div class="stat-val" id="s-total">--</div><div class="stat-lab">오늘 경주수</div></div>
        <div class="stat-box"><div class="stat-val" id="s-kra">--</div><div class="stat-lab">한국경마 수집</div></div>
        <div class="stat-box"><div class="stat-val" id="s-dark">--</div><div class="stat-lab">복병 케이스</div></div>
        <div class="stat-box"><div class="stat-val" id="s-dark-rate">--%</div><div class="stat-lab">복병 적중률</div></div>
      </div>
    </div>
    <div class="card">
      <div class="card-title">KRA 결과 수집 상태</div>
      <div id="kra-status-detail" class="empty"><span class="spin"></span></div>
    </div>
    <div class="card">
      <div class="card-title">오늘 경주 요약</div>
      <div id="today-summary" class="empty"><span class="spin"></span></div>
    </div>
  </div>

  <!-- ② 경주 -->
  <div id="tab-races" class="tab-content">
    <div class="card">
      <div class="card-title">경주 목록 · 상세 조회</div>
      <div id="race-list" class="empty"><span class="spin"></span></div>
    </div>
    <div id="race-detail-panel" class="detail-panel"></div>
  </div>

  <!-- ③ 긴급 조치 -->
  <div id="tab-action" class="tab-content">
    <div class="card">
      <div class="card-title">결과 수동 입력</div>
      <label>경주 키 (예: 서울 3경주)</label>
      <input type="text" id="result-key" placeholder="서울 3경주"/>
      <label>착순 (1착 / 2착 / 3착)</label>
      <div class="input-row">
        <input type="number" id="r1" placeholder="1착" min="1" max="20"/>
        <input type="number" id="r2" placeholder="2착" min="1" max="20"/>
        <input type="number" id="r3" placeholder="3착" min="1" max="20"/>
      </div>
      <button class="btn btn-primary" onclick="submitResult()">결과 등록</button>
    </div>

    <div class="card">
      <div class="card-title">카카오 수동 발송</div>
      <label>경주 키</label>
      <input type="text" id="kakao-key" placeholder="서울 3경주"/>
      <button class="btn btn-info" onclick="kakaoSend()">카카오 발송</button>
    </div>

    <div class="card">
      <div class="card-title">시스템 긴급 조치</div>
      <button class="btn btn-warn" onclick="tripleReset()">⚠ triple_store 초기화</button>
      <button class="btn btn-ghost" onclick="loadLogs()" style="margin-top:8px">📋 서버 로그 조회</button>
      <div id="log-output" style="display:none;margin-top:10px;">
        <div class="log-box" id="log-box"></div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">배당 수치 직접 확인</div>
      <label>경주 키</label>
      <input type="text" id="odds-key" placeholder="히로시마 1경주"/>
      <label>조합 (예: 3+4)</label>
      <input type="text" id="odds-combo" placeholder="3+4"/>
      <button class="btn btn-ghost" onclick="checkOdds()">배당 확인</button>
      <div id="odds-result" style="margin-top:8px;font-size:13px;"></div>
    </div>
  </div>

  <!-- ④ 통계 -->
  <div id="tab-stats" class="tab-content">
    <div class="card">
      <div class="card-title">최근 7일 적중 현황</div>
      <div id="weekly-stats" class="empty"><span class="spin"></span></div>
    </div>
    <div class="card">
      <div class="card-title">복병 경마장별 적중률</div>
      <div id="dark-venue-stats" class="empty"><span class="spin"></span></div>
    </div>
    <div class="card">
      <div class="card-title">복병 등급별 적중률</div>
      <div id="dark-star-stats" class="empty"><span class="spin"></span></div>
    </div>
  </div>
</main>

<nav>
  <button class="active" onclick="switchTab('status',this)"><span class="ico">📡</span>현황</button>
  <button onclick="switchTab('races',this)"><span class="ico">🏇</span>경주</button>
  <button onclick="switchTab('action',this)"><span class="ico">⚡</span>조치</button>
  <button onclick="switchTab('stats',this)"><span class="ico">📊</span>통계</button>
</nav>

<div id="toast"></div>

<script>
// ── 유틸 ─────────────────────────────────────────────────────────────────────
function toast(msg, dur=2200){
  const t=document.getElementById('toast');
  t.textContent=msg; t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'), dur);
}

async function api(path, method='GET', body=null){
  try{
    const opt={method, headers:{'Content-Type':'application/json'}};
    if(body) opt.body=JSON.stringify(body);
    const r=await fetch('/admin'+path, opt);
    return await r.json();
  }catch(e){return {ok:false, error:String(e)};}
}

function switchTab(name, btn){
  document.querySelectorAll('.tab-content').forEach(el=>el.classList.remove('active'));
  document.querySelectorAll('nav button').forEach(el=>el.classList.remove('active'));
  document.getElementById('tab-'+name).classList.add('active');
  btn.classList.add('active');
  if(name==='races' && !_racesLoaded) loadRaces();
  if(name==='stats') loadStats();
}

// ── 상태 ─────────────────────────────────────────────────────────────────────
let _statusTimer;
async function loadStatus(){
  const d = await api('/api/status');
  if(d.error) return;

  const dot = document.getElementById('srv-dot');
  dot.className = 'ok';
  document.getElementById('srv-time').textContent = d.server_time||'';

  document.getElementById('s-total').textContent = d.total_races??'--';
  document.getElementById('s-kra').textContent =
    (d.kra_result_done??'--') + '/' + (d.kra_races??'--');
  document.getElementById('s-dark').textContent = d.dark_cases_total??'--';
  const rate = d.dark_hit_rate??0;
  const rateEl = document.getElementById('s-dark-rate');
  rateEl.textContent = rate+'%';
  rateEl.className = 'stat-val' + (rate>=30?' ':rate<15?' danger':' warn');

  // KRA 상세
  const kd = document.getElementById('kra-status-detail');
  if(d.kra_races===0){
    kd.innerHTML='<span style="color:var(--muted)">오늘 한국경마 없음</span>';
  } else {
    const pct = d.kra_races>0 ? Math.round(d.kra_result_done/d.kra_races*100) : 0;
    kd.innerHTML=`
      <div class="stat-bar">
        <div class="stat-bar-label"><span class="name">결과 수집</span><span class="val">${d.kra_result_done}/${d.kra_races} (${pct}%)</span></div>
        <div class="bar-track"><div class="bar-fill" style="width:${pct}%"></div></div>
      </div>
      <div style="font-size:11px;color:var(--muted);margin-top:6px;">uptime ${Math.floor((d.uptime_sec||0)/3600)}h ${Math.floor(((d.uptime_sec||0)%3600)/60)}m</div>`;
  }
  loadTodaySummary();
}

async function loadTodaySummary(){
  const d = await api('/api/races');
  const el = document.getElementById('today-summary');
  if(!d.races||d.races.length===0){el.textContent='오늘 분석 경주 없음';return;}
  let qHit=0, tHit=0, noResult=0;
  d.races.forEach(r=>{
    if(!r.has_result) noResult++;
    if(r.hit_q) qHit++;
    if(r.hit_t) tHit++;
  });
  el.innerHTML=`
    <div class="stat-grid">
      <div class="stat-box"><div class="stat-val">${qHit}</div><div class="stat-lab">복승 적중</div></div>
      <div class="stat-box"><div class="stat-val">${tHit}</div><div class="stat-lab">삼복승 적중</div></div>
      <div class="stat-box"><div class="stat-val">${d.races.length}</div><div class="stat-lab">총 경주</div></div>
      <div class="stat-box"><div class="stat-val ${noResult>0?'warn':''}">${noResult}</div><div class="stat-lab">결과 미수집</div></div>
    </div>`;
}

// ── 경주 목록 ──────────────────────────────────────────────────────────────
let _racesLoaded=false;
let _openKey=null;
async function loadRaces(){
  _racesLoaded=true;
  const d=await api('/api/races');
  const el=document.getElementById('race-list');
  if(!d.races||d.races.length===0){el.textContent='경주 없음';return;}
  el.innerHTML='';
  d.races.forEach(r=>{
    const isKra = ['서울','부산','제주','코치'].some(v=>r.key&&r.key.includes(v));
    const schema = r.schema==='old'?'<span class="badge badge-old">구</span>':'';
    let hitBadge='<span class="badge badge-no-result">결과없음</span>';
    if(r.has_result){
      hitBadge=(r.hit_q?'<span class="badge badge-hit">복승✓</span>':'<span class="badge badge-miss">복승✗</span>')
              +(r.hit_t?'<span class="badge badge-hit">삼복✓</span>':'<span class="badge badge-miss">삼복✗</span>');
    }
    const combo=r.q_main?`${r.q_main.join('+')} ${r.q_odds?r.q_odds+'배':''}` : '추천없음';
    const div=document.createElement('div');
    div.className='race-item';
    div.innerHTML=`
      <span class="race-sport ${isKra?'kra':''}">${r.sport||'?'}</span>
      <div style="flex:1;min-width:0;">
        <div class="race-name">${r.race||r.key}</div>
        <div class="race-combo">${combo}</div>
      </div>
      <div class="race-badges">${schema}${hitBadge}</div>`;
    div.onclick=()=>loadRaceDetail(r.key);
    el.appendChild(div);
  });
}

async function loadRaceDetail(key){
  if(_openKey===key){
    _openKey=null;
    document.getElementById('race-detail-panel').classList.remove('open');
    return;
  }
  _openKey=key;
  const panel=document.getElementById('race-detail-panel');
  panel.classList.add('open');
  panel.innerHTML='<div class="empty"><span class="spin"></span> 로딩중...</div>';
  // Encode the key for the URL
  const d=await api('/api/race/'+encodeURIComponent(key.replace(/ /g,'_')));
  if(d.error){panel.innerHTML=`<div class="empty">${d.error}</div>`;return;}

  const fq=(d.finalQ||[]).map(q=>`<li>${(q.combo||[]).join('+')} <b>${q.odds??'?'}배</b> <span style="color:var(--muted);font-size:10px">${(q.reason||'').slice(0,20)}</span></li>`).join('');
  const ft=(d.finalT||[]).map(t=>`<li>${(t.combo||[]).join('+')} ${t.odds??'?'}배</li>`).join('');
  const dh=(d.darkHorses||[]).map(h=>`<li>${h.no}번 ${'★'.repeat(h.stars||1)} ${h.odds??'?'}배</li>`).join('');
  const rh=(d.recommendation_history||[]).map(r=>`<li><span class="t-time">${(r.time||'').slice(11,19)}</span><span class="t-combo">Q:${r.quinella_main||'-'} T:${r.trifecta_main||'-'}</span></li>`).join('');
  const res=d.result||{};

  panel.innerHTML=`
    <div class="card-title" style="margin-bottom:8px">${d.race||key} 상세</div>
    <div class="detail-row"><span class="detail-key">왕축</span><span class="detail-val">${(d.favAxis||[]).join(', ')||'없음'}</span></div>
    <div class="detail-row"><span class="detail-key">keyHorses</span><span class="detail-val">${(d.keyHorses||[]).join(', ')||'없음'}</span></div>
    <div class="detail-row"><span class="detail-key">강신호</span><span class="detail-val">${(d.strongSignals||[]).length}건</span></div>
    <div class="detail-row"><span class="detail-key">결과</span><span class="detail-val">${res['1st']||'?'}−${res['2nd']||'?'}−${res['3rd']||'?'}</span></div>
    <div class="section-sep"></div>
    <div style="font-size:11px;color:var(--muted);margin-bottom:4px;">finalQ (${(d.finalQ||[]).length})</div>
    <ul style="list-style:none;font-size:12px;padding:0;margin-bottom:10px;">${fq||'<li style="color:var(--muted)">없음</li>'}</ul>
    <div style="font-size:11px;color:var(--muted);margin-bottom:4px;">finalT (${(d.finalT||[]).length})</div>
    <ul style="list-style:none;font-size:12px;padding:0;margin-bottom:10px;">${ft||'<li style="color:var(--muted)">없음</li>'}</ul>
    <div style="font-size:11px;color:var(--muted);margin-bottom:4px;">복병</div>
    <ul style="list-style:none;font-size:12px;padding:0;margin-bottom:10px;">${dh||'<li style="color:var(--muted)">없음</li>'}</ul>
    <div style="font-size:11px;color:var(--muted);margin-bottom:6px;">추천 변경 이력</div>
    <ul class="timeline">${rh||'<li style="color:var(--muted)">없음</li>'}</ul>`;
  panel.scrollIntoView({behavior:'smooth',block:'start'});
}

// ── 긴급 조치 ──────────────────────────────────────────────────────────────
async function submitResult(){
  const key=document.getElementById('result-key').value.trim();
  const r1=document.getElementById('r1').value;
  const r2=document.getElementById('r2').value;
  const r3=document.getElementById('r3').value;
  if(!key||!r1){toast('경주키와 1착은 필수');return;}
  const d=await api('/api/result/input','POST',{raceKey:key,'1st':r1,'2nd':r2,'3rd':r3});
  toast(d.ok ? `✅ ${d.msg} | 복승:${d.quinella_hit?'✓':'✗'} 삼복:${d.trifecta_hit?'✓':'✗'}` : `❌ ${d.msg}`);
}

async function kakaoSend(){
  const key=document.getElementById('kakao-key').value.trim();
  if(!key){toast('경주키 입력 필요');return;}
  const d=await api('/api/action/kakao_send','POST',{raceKey:key});
  toast(d.ok ? `✅ ${d.msg}` : `❌ ${d.msg}`);
}

async function tripleReset(){
  if(!confirm('triple_store를 초기화합니까?')) return;
  const d=await api('/api/action/triple_reset','POST');
  toast(d.ok ? '✅ '+d.msg : '❌ '+d.msg);
}

async function loadLogs(){
  const box=document.getElementById('log-output');
  const logBox=document.getElementById('log-box');
  box.style.display='block';
  logBox.textContent='로딩중...';
  const d=await api('/api/logs');
  if(d.lines&&d.lines.length>0){
    logBox.textContent=(d.lines||[]).join('\n');
    logBox.scrollTop=logBox.scrollHeight;
  } else {
    logBox.textContent='로그 파일 없음 (app.log, server.log)';
  }
}

async function checkOdds(){
  const key=document.getElementById('odds-key').value.trim();
  const combo=document.getElementById('odds-combo').value.trim();
  if(!key||!combo){toast('경주키와 조합 모두 입력');return;}
  const d=await api('/api/race/'+encodeURIComponent(key.replace(/ /g,'_')));
  if(d.error){document.getElementById('odds-result').textContent='❌ '+d.error;return;}
  // Search in finalQ
  const found=(d.finalQ||[]).find(q=>{
    const c=(q.combo||[]).map(String).sort().join('+');
    const target=combo.split('+').map(s=>s.trim()).sort().join('+');
    return c===target;
  });
  const el=document.getElementById('odds-result');
  if(found){
    el.innerHTML=`<b>${combo}</b> → 시스템: <b style="color:var(--accent)">${found.odds??'?'}배</b><br><span style="color:var(--muted);font-size:11px">${found.reason||''}</span>`;
  } else {
    el.innerHTML=`<span style="color:var(--warn)">${combo} — finalQ에 없음 (보조추천이거나 미편입)</span>`;
  }
}

// ── 통계 ─────────────────────────────────────────────────────────────────────
async function loadStats(){
  // Weekly
  const w=await api('/api/stats/weekly');
  const wel=document.getElementById('weekly-stats');
  if(w.daily&&w.daily.length>0){
    wel.innerHTML=w.daily.map(d=>`
      <div class="stat-bar">
        <div class="stat-bar-label">
          <span class="name">${d.date}</span>
          <span class="val">복승 ${d.q_hits}/${d.total} (${d.q_rate}%)</span>
        </div>
        <div class="bar-track"><div class="bar-fill" style="width:${Math.min(d.q_rate,100)}%"></div></div>
      </div>`).join('');
  } else {
    wel.textContent='데이터 없음';
  }

  // Dark horse
  const dk=await api('/api/dark_horse_stats');
  if(!dk.total){
    document.getElementById('dark-venue-stats').textContent='복병 케이스 없음';
    document.getElementById('dark-star-stats').textContent='복병 케이스 없음';
    return;
  }

  // Venue
  const venues=Object.entries(dk.by_venue||{}).sort((a,b)=>b[1].total-a[1].total);
  document.getElementById('dark-venue-stats').innerHTML=venues.map(([v,s])=>{
    const r=s.total>0?Math.round(s.hit/s.total*100):0;
    return `<div class="stat-bar">
      <div class="stat-bar-label"><span class="name">${v}</span><span class="val">${s.hit}/${s.total} (${r}%)</span></div>
      <div class="bar-track"><div class="bar-fill" style="width:${r}%"></div></div>
    </div>`;
  }).join('')||'없음';

  // Stars
  const stars=Object.entries(dk.by_stars||{}).sort((a,b)=>b[0]-a[0]);
  document.getElementById('dark-star-stats').innerHTML=stars.map(([s,v])=>{
    const r=v.total>0?Math.round(v.hit/v.total*100):0;
    const star='★'.repeat(Math.min(parseInt(s)||1,5));
    return `<div class="stat-bar">
      <div class="stat-bar-label"><span class="name">${star}</span><span class="val">${v.hit}/${v.total} (${r}%)</span></div>
      <div class="bar-track"><div class="bar-fill" style="width:${r}%"></div></div>
    </div>`;
  }).join('')||'없음';
}

// ── 초기화 ────────────────────────────────────────────────────────────────────
loadStatus();
setInterval(loadStatus, 30000);
</script>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
#  API 라우트
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route("/")
def admin_home():
    return ADMIN_HTML


@admin_bp.route("/api/status")
def admin_status():
    today = _today()
    all_files = glob.glob(os.path.join(_ALOG, f"{today}_*.json"))
    kra_files = [f for f in all_files if any(v in f for v in ["서울", "부산", "제주", "코치"])]
    kakao_state = _jload(_KAKAO, {})
    kra_keys = [k for k in kakao_state if any(v in k for v in ["서울", "부산", "제주", "코치"])]
    kra_result_done = sum(1 for k in kra_keys if kakao_state[k].get("resultDone"))
    dark = _jload(_DARK, {})
    cases = dark.get("cases", [])
    dark_hit = sum(1 for c in cases if c.get("hit"))
    try:
        import psutil
        uptime = int(time.time() - psutil.Process(os.getpid()).create_time())
    except Exception:
        uptime = 0
    return jsonify({
        "today": today.replace("_", "-"),
        "total_races": len(all_files),
        "kra_races": len(kra_files),
        "kra_result_done": kra_result_done,
        "dark_cases_total": len(cases),
        "dark_hit_rate": round(dark_hit / len(cases) * 100, 1) if cases else 0,
        "uptime_sec": uptime,
        "triple_store_ok": os.path.exists(_TRIPLE),
        "server_time": datetime.now().strftime("%H:%M:%S"),
    })


@admin_bp.route("/api/races")
def admin_races():
    today = _today()
    date_filter = request.args.get("date", today)
    all_files = sorted(glob.glob(os.path.join(_ALOG, f"{date_filter}_*.json")))
    races = []
    for f in all_files:
        fname = os.path.basename(f)
        try:
            d = _jload(f)
            cp = d.get("corePicks") or d.get("final_recommendation") or {}
            result = d.get("result") or {}
            fq = cp.get("finalQuinellas") or cp.get("quinellas") or []
            ft = cp.get("finalTrifectas") or cp.get("trifectas") or []
            hit = d.get("hit") or {}
            races.append({
                "key": d.get("raceKey") or fname.replace(".json", ""),
                "race": d.get("race", fname),
                "sport": d.get("sport", "?"),
                "schema": "new" if d.get("corePicks") else "old",
                "has_result": bool(result.get("1st")),
                "finalQ_count": len(fq),
                "finalT_count": len(ft),
                "q_main": fq[0].get("combo") if fq else None,
                "q_odds": fq[0].get("odds") if fq else None,
                "hit_q": hit.get("quinella"),
                "hit_t": hit.get("trifecta"),
            })
        except Exception as e:
            races.append({"key": fname, "race": fname, "sport": "?", "error": str(e),
                          "schema": "?", "has_result": False})
    return jsonify({"races": races, "date": date_filter})


@admin_bp.route("/api/race/<path:key>")
def admin_race_detail(key):
    key_norm = key.replace("_", " ")
    candidates = (
        glob.glob(os.path.join(_ALOG, f"*{key_norm}*.json")) +
        glob.glob(os.path.join(_ALOG, f"*{key}*.json"))
    )
    if not candidates:
        return jsonify({"error": "경주 파일 없음"}), 404
    d = _jload(candidates[0])
    cp = d.get("corePicks") or d.get("final_recommendation") or {}
    return jsonify({
        "raceKey": d.get("raceKey"),
        "race": d.get("race"),
        "sport": d.get("sport"),
        "favAxis": cp.get("favAxis") or [],
        "keyHorses": d.get("keyHorses") or [],
        "strongSignals": d.get("strongSignals") or [],
        "finalQ": cp.get("finalQuinellas") or cp.get("quinellas") or [],
        "finalT": cp.get("finalTrifectas") or cp.get("trifectas") or [],
        "darkHorses": d.get("darkHorses") or [],
        "result": d.get("result") or {},
        "hit": d.get("hit") or {},
        "recommendation_history": (d.get("recommendation_history") or [])[-10:],
    })


@admin_bp.route("/api/logs")
def admin_logs():
    for lf in ["app.log", "server.log", "racing.log"]:
        lp = os.path.join(_BASE, lf)
        if os.path.exists(lp):
            with open(lp, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[-100:]
            return jsonify({"lines": [l.rstrip() for l in lines]})
    return jsonify({"lines": []})


@admin_bp.route("/api/dark_horse_stats")
def admin_dark_stats():
    d = _jload(_DARK, {})
    cases = d.get("cases", [])
    total = len(cases)
    hit = sum(1 for c in cases if c.get("hit"))
    by_venue, by_stars = {}, {}
    for c in cases:
        v = c.get("venue", "?")
        by_venue.setdefault(v, {"total": 0, "hit": 0})
        by_venue[v]["total"] += 1
        if c.get("hit"): by_venue[v]["hit"] += 1
        s = str(c.get("stars", "?"))
        by_stars.setdefault(s, {"total": 0, "hit": 0})
        by_stars[s]["total"] += 1
        if c.get("hit"): by_stars[s]["hit"] += 1
    return jsonify({
        "total": total, "hit": hit,
        "hit_rate": round(hit / total * 100, 1) if total else 0,
        "by_venue": by_venue, "by_stars": by_stars,
    })


@admin_bp.route("/api/action/triple_reset", methods=["POST"])
def admin_triple_reset():
    try:
        with open(_TRIPLE, "w", encoding="utf-8") as f:
            json.dump({}, f)
        return jsonify({"ok": True, "msg": "triple_store.json 초기화 완료"})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@admin_bp.route("/api/action/kakao_send", methods=["POST"])
def admin_kakao_send():
    data = request.json or {}
    race_key = data.get("raceKey")
    if not race_key:
        return jsonify({"ok": False, "msg": "raceKey 필요"}), 400
    try:
        import importlib
        app_mod = importlib.import_module("app")
        for fn_name in ["_kakao_send_race", "_send_kakao_race", "kakao_send_race"]:
            fn = getattr(app_mod, fn_name, None)
            if fn:
                fn(race_key)
                return jsonify({"ok": True, "msg": f"카카오 발송 완료: {race_key}"})
        return jsonify({"ok": False, "msg": "카카오 발송 함수 미발견"})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@admin_bp.route("/api/result/input", methods=["POST"])
def admin_result_input():
    data = request.json or {}
    rk   = data.get("raceKey", "").strip()
    r1, r2, r3 = data.get("1st"), data.get("2nd"), data.get("3rd")
    if not rk or not r1:
        return jsonify({"ok": False, "msg": "raceKey와 1착 필수"}), 400
    candidates = (
        glob.glob(os.path.join(_ALOG, f"*{rk}*.json")) +
        glob.glob(os.path.join(_ALOG, f"*{rk.replace(' ','_')}*.json"))
    )
    if not candidates:
        return jsonify({"ok": False, "msg": f"{rk} 파일 없음"}), 404
    try:
        fpath = candidates[0]
        d = _jload(fpath)
        d["result"] = {"1st": r1, "2nd": r2, "3rd": r3,
                        "input_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "source": "admin_manual"}
        cp = d.get("corePicks") or d.get("final_recommendation") or {}
        fq = cp.get("finalQuinellas") or cp.get("quinellas") or []
        ft = cp.get("finalTrifectas") or cp.get("trifectas") or []
        top2 = {int(r1), int(r2)} if r2 else {int(r1)}
        top3 = {int(x) for x in [r1, r2, r3] if x}
        q_hit = any(set(int(x) for x in (q.get("combo") or [])) == top2 for q in fq)
        t_hit = any(set(int(x) for x in (t.get("combo") or [])) == top3 for t in ft)
        d["hit"] = {"quinella": q_hit, "trifecta": t_hit,
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        return jsonify({"ok": True, "msg": "결과 등록 완료",
                        "quinella_hit": q_hit, "trifecta_hit": t_hit})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@admin_bp.route("/api/stats/weekly")
def admin_weekly():
    today_dt = datetime.now()
    daily = []
    for i in range(7):
        dt = today_dt - timedelta(days=i)
        ds = dt.strftime("%Y_%m_%d")
        files = glob.glob(os.path.join(_ALOG, f"{ds}_*.json"))
        q_h = t_h = 0
        for f in files:
            try:
                d = _jload(f)
                hit = d.get("hit") or {}
                if hit.get("quinella"): q_h += 1
                if hit.get("trifecta"): t_h += 1
            except Exception:
                pass
        daily.append({
            "date": dt.strftime("%m/%d"),
            "total": len(files), "q_hits": q_h, "t_hits": t_h,
            "q_rate": round(q_h / len(files) * 100, 1) if files else 0,
        })
    return jsonify({"daily": daily})
