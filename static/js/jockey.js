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

  global.JockeyDB = { load, get, upsert, all };
})(window);
