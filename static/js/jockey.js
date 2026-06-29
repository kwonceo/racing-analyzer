/* ===== jockey.js — 기수 DB =====
 * data/jockeys.json 을 로드하고, 이름으로 기수 통계를 조회한다.
 * localStorage("bmed_jockeys") 에 사용자 보정값을 누적 저장한다.
 */
(function (global) {
  const LS_KEY = 'bmed_jockeys';
  let _jockeys = [];   // [{name, track, winRate, placeRate, rides, recentForm}]
  let _index = new Map();

  function _rebuildIndex() {
    _index = new Map();
    for (const j of _jockeys) _index.set(j.name, j);
  }

  /** 기본 데이터(json) + localStorage 보정값 병합 로드 */
  async function load() {
    let base = [];
    try {
      const res = await fetch('data/jockeys.json');
      const json = await res.json();
      base = json.jockeys || [];
    } catch (e) {
      console.warn('[jockey] jockeys.json 로드 실패, 빈 DB로 시작:', e);
    }
    let overrides = {};
    try {
      overrides = JSON.parse(localStorage.getItem(LS_KEY) || '{}');
    } catch (_) {}

    _jockeys = base.map((j) => ({ ...j, ...(overrides[j.name] || {}) }));
    // localStorage 에만 있는 신규 기수도 추가
    for (const [name, data] of Object.entries(overrides)) {
      if (!_jockeys.find((j) => j.name === name)) _jockeys.push({ name, ...data });
    }
    _rebuildIndex();
    return _jockeys;
  }

  /** 이름으로 기수 조회 (없으면 null) */
  function get(name) {
    if (!name) return null;
    return _index.get(name.trim()) || null;
  }

  /** 기수 통계 갱신/추가 후 localStorage 저장 */
  function upsert(name, data) {
    name = (name || '').trim();
    if (!name) return;
    let cur = _index.get(name);
    if (!cur) { cur = { name, winRate: 0, placeRate: 0, rides: 0, recentForm: '' }; _jockeys.push(cur); }
    Object.assign(cur, data);
    _rebuildIndex();
    _persist();
    return cur;
  }

  function _persist() {
    const map = {};
    for (const j of _jockeys) map[j.name] = j;
    localStorage.setItem(LS_KEY, JSON.stringify(map));
  }

  function all() { return _jockeys.slice(); }

  // ===== [6번] 확장 조회 =====
  function rate(o) { return o && o.rides ? Math.round((o.places / o.rides) * 1000) / 10 : 0; }
  function recent30Rate(name) { const j = get(name); return j && j.recent30 ? rate(j.recent30) : null; }
  function distRate(name, dist) {
    const j = get(name); const d = String(parseInt(dist, 10) || '');
    return j && j.byDistance && j.byDistance[d] ? rate(j.byDistance[d]) : null;
  }
  function trackRate(name, track) {
    const j = get(name);
    return j && j.byTrack && j.byTrack[track] ? rate(j.byTrack[track]) : null;
  }
  function comboStat(name, horse) {
    const j = get(name);
    return j && j.byHorse && j.byHorse[horse] ? j.byHorse[horse] : null;
  }
  /** 리딩 기수 순위 (복승권율 기준) */
  function leaders() {
    return all().slice().sort((a, b) => (b.placeRate || 0) - (a.placeRate || 0))
      .map((j, i) => Object.assign({ rank: i + 1 }, j));
  }

  /** [6번] 경주 결과로 기수 성적 자동 갱신 */
  function recordRace(name, info) {
    const j = get((name || '').trim()); if (!j || !info) return;
    const placing = parseInt(info.placing, 10);
    if (!placing) return;
    const win = placing === 1, place = placing >= 1 && placing <= 3;
    const bump = (o) => { o.rides = (o.rides || 0) + 1; if (win) o.wins = (o.wins || 0) + 1; if (place) o.places = (o.places || 0) + 1; };
    // 최근 30경주(롤링)
    j.recent = (j.recent || []).concat(placing).slice(-30);
    j.recent30 = {
      rides: j.recent.length,
      wins: j.recent.filter((p) => p === 1).length,
      places: j.recent.filter((p) => p >= 1 && p <= 3).length,
    };
    const d = String(parseInt(info.distance, 10) || '');
    if (d) { j.byDistance = j.byDistance || {}; j.byDistance[d] = j.byDistance[d] || { rides: 0, places: 0 }; bump(j.byDistance[d]); }
    if (info.track) { j.byTrack = j.byTrack || {}; j.byTrack[info.track] = j.byTrack[info.track] || { rides: 0, places: 0 }; bump(j.byTrack[info.track]); }
    if (info.horse) { j.byHorse = j.byHorse || {}; j.byHorse[info.horse] = j.byHorse[info.horse] || { rides: 0, wins: 0, places: 0 }; bump(j.byHorse[info.horse]); }
    j.rides = (j.rides || 0) + 1;
    _rebuildIndex(); _persist();
    return j;
  }

  global.JockeyDB = {
    load, get, upsert, all,
    rate, recent30Rate, distRate, trackRate, comboStat, leaders, recordRace,
  };
})(window);
