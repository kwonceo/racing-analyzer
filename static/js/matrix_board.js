/*
 * matrix_board.js — 공개용 3단계 카드 UI + 배당판 매트릭스 (두 프로젝트 공통 컴포넌트)
 *
 *   사용처: ① 경마분석서버 분석기 웹 '📊 배당판' 탭  ② 적중왕(bmed-public) 경주 상세
 *   데이터: GET <apiBase>/api/public/matrix/<raceKey>  (app.py public_matrix)
 *
 *   설계 원칙(사용자 요구):
 *     - 나이 많은 사용자 기준 — 본문 최소 16px · 중요 숫자 28px+ · 셀/버튼 높이 최소 52px
 *     - 색상 의미 고정: 초록=좋음(최종추천) · 파랑=유력 · 노랑=주의(복병) · 빨강=급락 · 보라=특별추천
 *     - 전문용어 제거(quinella→복승 / BMED→분석결과 / signalScore→신뢰도 / early_drop→초기주목)
 *     - 무료 회원은 최종추천/복병을 블러 처리 + '프리미엄 전용' 안내
 *
 *   외부 의존성 없음(순수 DOM). 전역 `MatrixBoard` 하나만 노출.
 *     MatrixBoard.mount(el, { raceKey, apiBase, plan, autoRefresh })  → { refresh(), destroy(), setRaceKey() }
 */
(function (global) {
  'use strict';

  var REFRESH_MS = 30000;   // 30초 자동 업데이트(요구사항)

  var COLORS = {
    green: '#16a34a', greenBg: '#dcfce7', greenDeep: '#14532d',
    blue: '#2563eb', blueBg: '#dbeafe',
    red: '#dc2626', redBg: '#fee2e2',
    amber: '#d97706', amberBg: '#fef3c7', amberDeep: '#78350f',
    purple: '#7c3aed', purpleBg: '#ede9fe',
    gray: '#64748b', grayBg: '#f1f5f9',
    ink: '#0f172a', sub: '#475569', line: '#cbd5e1',
  };

  // ── 작은 DOM 헬퍼 ──────────────────────────────────────────────
  function el(tag, css, text) {
    var e = document.createElement(tag);
    if (css) e.style.cssText = css;
    if (text != null) e.textContent = text;
    return e;
  }
  function card(bg, border, pad) {
    return el('div', 'background:' + bg + ';border:' + border + ';border-radius:14px;'
      + 'padding:' + (pad || '16px') + ';margin-bottom:14px;box-sizing:border-box;');
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  // 배당 표기(없으면 '—')
  function oddsTxt(v) { return (v == null || !(v > 0)) ? '—' : (Math.round(v * 10) / 10) + '배'; }

  // ── 카드 1: 경주 헤더 ──────────────────────────────────────────
  function renderHeader(d) {
    var c = card('#ffffff', '2px solid ' + COLORS.line);
    var top = el('div', 'display:flex;flex-wrap:wrap;align-items:center;gap:10px;');
    top.appendChild(el('div', 'font-size:24px;font-weight:900;color:' + COLORS.ink + ';', d.race_name || '경주'));
    top.appendChild(el('span', 'font-size:20px;color:' + COLORS.sub + ';', (d.horses || 0) + '두'));

    var g = d.confidence || {};
    var gCol = g.grade === 'A' ? COLORS.green : (g.grade === 'B' ? COLORS.blue : COLORS.gray);
    var badge = el('span', 'font-size:20px;font-weight:900;color:#fff;background:' + gCol
      + ';border-radius:999px;padding:4px 14px;', (g.grade || '-') + '급');
    badge.title = g.message || '';
    top.appendChild(badge);
    c.appendChild(top);

    var left = d.deadline_left || '—';
    var closed = d.after_close || left === '마감';
    var dl = el('div', 'margin-top:10px;font-size:22px;font-weight:800;color:'
      + (closed ? COLORS.red : COLORS.ink) + ';', closed ? '⏹ 마감됨' : '⏱ 마감까지 ' + left);
    c.appendChild(dl);

    if (g.label) {
      c.appendChild(el('div', 'margin-top:6px;font-size:16px;color:' + COLORS.sub + ';',
        (g.emoji ? g.emoji + ' ' : '') + g.label + (g.score != null ? ' · 신뢰도 ' + g.score + '점' : '')));
    }
    // [날씨] 경주장 실시간 날씨·주로 상태 — 불량이면 경고색.
    var w = d.weather;
    if (w) {
      var wemo = ({ Clear: '🌤', Clouds: '⛅', Rain: '🌧', Drizzle: '🌦', Thunderstorm: '⛈', Snow: '🌨' })[w.weather_main] || '🌡';
      var bad = (w.level || 0) >= 2;
      var wCol = bad ? COLORS.red : ((w.level || 0) === 1 ? COLORS.amber : COLORS.sub);
      var wtxt = wemo + ' ' + (w.venue || '') + ' ' + (w.desc || '')
        + ((w.rain_mm || 0) > 0 ? ' ' + w.rain_mm + 'mm' : '')
        + ' · 주로 ' + (w.condition || '') + (bad ? ' ⚠️' : '')
        + (w.strongWind ? ' · 강풍 ' + Math.round(w.wind) + 'm/s 🌬' : '');
      c.appendChild(el('div', 'margin-top:6px;font-size:16px;font-weight:' + (bad ? '800' : '600') + ';color:' + wCol + ';', wtxt));
    }
    return c;
  }

  // ── 잠금(무료 회원) 오버레이 ───────────────────────────────────
  function lockWrap(inner, note) {
    var wrap = el('div', 'position:relative;');
    inner.style.filter = 'blur(7px)';
    inner.style.pointerEvents = 'none';
    inner.style.userSelect = 'none';
    wrap.appendChild(inner);
    var ov = el('div', 'position:absolute;inset:0;display:flex;flex-direction:column;'
      + 'align-items:center;justify-content:center;gap:8px;text-align:center;padding:12px;');
    ov.appendChild(el('div', 'font-size:22px;font-weight:900;color:' + COLORS.ink + ';', '🔒 프리미엄 전용'));
    ov.appendChild(el('div', 'font-size:16px;color:' + COLORS.sub + ';', note || '프리미엄에서 전체 공개됩니다'));
    wrap.appendChild(ov);
    return wrap;
  }

  // ── 카드 2: 최종 추천 ──────────────────────────────────────────
  function renderPicks(d, locked) {
    var r = d.recommendation || {};
    var main = r.main || [], tri = r.trifecta || [], special = r.special || [];

    var c = card(COLORS.greenBg, '3px solid ' + COLORS.green);
    c.appendChild(el('div', 'font-size:20px;font-weight:900;color:' + COLORS.greenDeep + ';margin-bottom:10px;',
      '🔒 오늘의 최종 추천'));

    // [항상 표시] 추천이 아직 없어도 카드는 유지하고 대기 상태를 안내한다(카드 숨김은 복병마 카드만 해당).
    if (!main.length && !tri.length) {
      var g = d.confidence || {};
      c.appendChild(el('div', 'font-size:22px;font-weight:900;color:' + COLORS.greenDeep + ';', '⏳ 아직 추천이 없습니다'));
      c.appendChild(el('div', 'margin-top:8px;font-size:15px;color:' + COLORS.greenDeep + ';',
        g.message || '뚜렷한 신호가 나타나면 추천 조합이 표시됩니다.'));
      (g.reasons || []).forEach(function (x) {
        c.appendChild(el('div', 'margin-top:4px;font-size:14px;color:' + COLORS.greenDeep + ';', '→ ' + x));
      });
      return c;
    }

    var body = el('div', '');
    if (main[0]) {
      var m = el('div', 'margin-bottom:8px;');
      m.appendChild(el('span', 'font-size:16px;font-weight:800;color:' + COLORS.greenDeep + ';', '복승  '));
      m.appendChild(el('span', 'font-size:30px;font-weight:900;color:' + COLORS.greenDeep + ';', main[0].combo));
      m.appendChild(el('span', 'font-size:22px;font-weight:800;color:' + COLORS.green + ';', '  ' + oddsTxt(main[0].odds)));
      body.appendChild(m);
    }
    if (tri[0]) {
      var t = el('div', 'margin-bottom:8px;');
      t.appendChild(el('span', 'font-size:16px;font-weight:800;color:' + COLORS.greenDeep + ';', '삼복승  '));
      t.appendChild(el('span', 'font-size:24px;font-weight:900;color:' + COLORS.greenDeep + ';', tri[0].combo));
      t.appendChild(el('span', 'font-size:18px;font-weight:800;color:' + COLORS.green + ';',
        '  ' + oddsTxt(tri[0].odds) + (tri[0].estimated ? ' (예상)' : '')));
      body.appendChild(t);
    }
    if (special.length) {
      var s = el('div', 'margin-top:6px;font-size:18px;font-weight:800;color:' + COLORS.purple + ';',
        '💎 특별 추천: ' + special.map(function (x) { return x.combo; }).join(' · '));
      body.appendChild(s);
    }
    // [개선1] 시장유력/전적A 보완 삼복승 — 왜 이 조합인지 근거 문장 함께 표시
    tri.forEach(function (t) {
      if (!t.insurance) return;
      var ib = el('div', 'margin-top:8px;padding:9px 11px;background:' + COLORS.grayBg + ';border-radius:9px;border-left:4px solid ' + COLORS.amber + ';');
      ib.appendChild(el('div', 'font-size:18px;font-weight:800;color:' + COLORS.ink + ';',
        '🛡 삼복승 보험: ' + t.combo + (t.odds != null ? ' (' + oddsTxt(t.odds) + (t.estimated ? ' 예상' : '') + ')' : '')));
      if (t.insuranceWhy) ib.appendChild(el('div', 'font-size:14px;color:' + COLORS.sub + ';margin-top:3px;', '→ ' + t.insuranceWhy));
      body.appendChild(ib);
    });

    // 부연설명 — 왜 이 조합인지
    var reasons = r.reasons || [];
    if (reasons.length) {
      var why = el('div', 'margin-top:12px;padding-top:10px;border-top:1px solid ' + COLORS.green + '55;');
      reasons.forEach(function (line) {
        why.appendChild(el('div', 'font-size:14px;color:' + COLORS.greenDeep + ';margin:3px 0;', '→ ' + line));
      });
      body.appendChild(why);
    }

    if (r.locked) {
      body.appendChild(el('div', 'margin-top:10px;font-size:14px;color:' + COLORS.red + ';',
        '※ 마감된 경주입니다 — 참고용으로만 보세요.'));
    }
    c.appendChild(locked ? lockWrap(body, '최종 추천 조합은 프리미엄 전용입니다') : body);
    return c;
  }

  // ── 🎯 핵심 축 2두 전략 (축1·축2 + 연결마 복승 5조합) ──────────────
  //   1순위 말만 축으로 쓰면 탈락 시 전멸 → 축 2두로 분산. 복승 ①축1+축2 ②③축1+연결 ④⑤축2+연결.
  function renderAxis(d, locked) {
    var ax = (d.recommendation || {}).axis;
    if (!ax || !(ax.quinella || []).length) return null;
    var c = card(COLORS.blueBg, '3px solid ' + COLORS.blue);
    c.appendChild(el('div', 'font-size:20px;font-weight:900;color:#1e3a8a;',
      ax.title || '🎯 핵심 축'));
    c.appendChild(el('div', 'font-size:14px;color:' + COLORS.sub + ';font-weight:700;margin:2px 0 8px;',
      '축1 ' + ax.axis1 + '번 · 축2 ' + ax.axis2 + '번' +
      ((ax.links || []).length ? ' · 연결마 ' + ax.links.join('·') + '번' : '')));
    var body = el('div', '');
    var NUM = ['①', '②', '③', '④', '⑤', '⑥'];
    (ax.quinella || []).forEach(function (q, i) {
      var row = el('div', 'display:flex;align-items:baseline;gap:8px;margin:5px 0;');
      row.appendChild(el('span', 'font-size:' + (i === 0 ? 30 : 24) + 'px;font-weight:900;color:#1e3a8a;',
        (NUM[i] || (i + 1 + '.')) + ' ' + q.text));
      if (q.label) row.appendChild(el('span', 'font-size:13px;color:' + COLORS.sub + ';', '← ' + q.label));
      body.appendChild(row);
    });
    if ((ax.trifecta || []).length) {
      body.appendChild(el('div', 'margin-top:10px;border-top:1px dashed ' + COLORS.line + ';padding-top:6px;font-size:14px;color:' + COLORS.sub + ';',
        '삼복승: ' + ax.trifecta.map(function (t) {
          return t.text + (t.odds != null ? ' (' + oddsTxt(t.odds) + (t.estimated ? ' 예상' : '') + ')' : '');
        }).join(' · ')));
    }
    c.appendChild(locked ? lockWrap(body, '핵심 축 전략은 프리미엄 전용입니다') : body);
    return c;
  }

  // ── 🏇 편성 시나리오 (각질 편성 + 페이스 예측 + 시나리오 A/B) ──────────
  function renderPace(d, locked) {
    var p = d.pace;
    if (!p || !p.counts) return null;
    var cnt = p.counts;
    var c = card('#f5f3ff', '3px solid ' + COLORS.purple);   // 보라(편성 분석)
    c.appendChild(el('div', 'font-size:20px;font-weight:900;color:#6d28d9;', '🏇 편성 시나리오'));
    c.appendChild(el('div', 'font-size:16px;font-weight:800;color:' + COLORS.ink + ';margin:4px 0;',
      '선행 ' + (cnt['선행'] || 0) + ' · 선입 ' + (cnt['선입'] || 0) + ' · 추입 ' + (cnt['추입'] || 0) +
      ((cnt['자유'] || 0) ? ' · 자유 ' + cnt['자유'] : '')));
    c.appendChild(el('div', 'font-size:18px;font-weight:900;color:#6d28d9;margin-bottom:2px;',
      (p.paceLabel || '') + ' 예상'));
    if (p.advice) c.appendChild(el('div', 'font-size:14px;color:' + COLORS.sub + ';margin-bottom:8px;', p.advice));
    var body = el('div', '');
    var sc = p.scenario;
    if (sc) {
      if ((sc.a || []).length) {
        body.appendChild(el('div', 'font-size:15px;font-weight:800;color:#1e3a8a;margin-top:6px;', '시나리오 A (유력마 축)'));
        sc.a.forEach(function (q) {
          var row = el('div', 'font-size:19px;font-weight:900;color:#1e3a8a;margin:3px 0;');
          row.appendChild(el('span', '', q.text));
          if (q.label) row.appendChild(el('span', 'font-size:12px;color:' + COLORS.sub + ';', ' ← ' + q.label));
          body.appendChild(row);
        });
      }
      if ((sc.b || []).length || (sc.focusNos || []).length) {
        body.appendChild(el('div', 'font-size:15px;font-weight:800;color:#6d28d9;margin-top:10px;', '시나리오 B (편성 유리)'));
        if ((sc.focusNos || []).length) {
          body.appendChild(el('div', 'font-size:15px;font-weight:800;color:#b45309;margin:2px 0;',
            '🐎 ' + sc.focusNos.join('번·') + '번 ' + (sc.focusGait || '') + '마 주목'));
        }
        (sc.b || []).forEach(function (q) {
          var row = el('div', 'font-size:19px;font-weight:900;color:#6d28d9;margin:3px 0;');
          row.appendChild(el('span', '', q.text + ' 고배당 가능'));
          if (q.label) row.appendChild(el('span', 'font-size:12px;color:' + COLORS.sub + ';', ' ← ' + q.label));
          body.appendChild(row);
        });
      }
      if (sc.trifecta) {
        body.appendChild(el('div', 'margin-top:10px;border-top:1px dashed ' + COLORS.line + ';padding-top:6px;font-size:14px;color:' + COLORS.sub + ';',
          '삼복승: ' + sc.trifecta + ' (유력마+편성 유리 복병)'));
      }
    }
    if ((p.scenario2 || []).length) {
      body.appendChild(el('div', 'margin-top:8px;font-size:13px;color:' + COLORS.sub + ';',
        (p.scenario2 || []).map(function (s) { return '· ' + s; }).join('  ')));
    }
    c.appendChild(locked ? lockWrap(body, '편성 시나리오는 프리미엄 전용입니다') : body);
    return c;
  }

  // ── [KRA 6단계] 경주 전개 예측 (한국경마 구간기록 기반·없으면 null) ───────────────────
  function renderKraFlow(d, locked) {
    var f = d.kraFlow;
    if (!f || (!f.hasSection && !f.hasPassRank)) return null;
    var c = card('#eff6ff', '3px solid #2563eb');   // 파랑(전개 예측)
    c.appendChild(el('div', 'font-size:20px;font-weight:900;color:#1d4ed8;', '🏇 경주 전개 예측'));
    var body = el('div', '');
    body.appendChild(el('div', 'font-size:17px;font-weight:900;color:#1e3a8a;margin:4px 0;',
      '선행 ' + (f.leadCount || 0) + '두 경합 → ' + (f.pace || '')));
    if (f.paceReason) body.appendChild(el('div', 'font-size:14px;color:' + COLORS.sub + ';margin-bottom:6px;', f.paceReason));
    if ((f.leadContenders || []).length) {
      body.appendChild(el('div', 'font-size:14px;color:#1e3a8a;margin:2px 0;',
        '선행 후보: ' + f.leadContenders.join('·') + '번'));
    }
    // 추입 복병(고배당)
    (f.darkHorses || []).forEach(function (dh) {
      var row = el('div', 'font-size:18px;font-weight:900;color:#b45309;margin:6px 0 2px;');
      row.appendChild(el('span', '', '💎 추입 복병: ' + dh.no + '번' + (dh.hrName ? ' ' + dh.hrName : '')));
      if (dh.winOdds) row.appendChild(el('span', 'font-size:14px;color:' + COLORS.sub + ';', ' (' + dh.winOdds + '배)'));
      body.appendChild(row);
      if (dh.why) body.appendChild(el('div', 'font-size:13px;color:#b45309;margin-bottom:4px;', '→ ' + dh.why));
    });
    // 유리한 말
    if ((f.favoredHorses || []).length) {
      body.appendChild(el('div', 'font-size:14px;font-weight:800;color:#1d4ed8;margin-top:8px;', '전개 유리 말'));
      (f.favoredHorses || []).forEach(function (h) {
        body.appendChild(el('div', 'font-size:15px;font-weight:800;color:#1e3a8a;margin:2px 0;',
          h.no + '번' + (h.hrName ? ' ' + h.hrName : '') + (h.gaitHint ? ' (' + h.gaitHint + ')' : '') +
          ((h.why || []).length ? ' — ' + h.why.join('·') : '')));
      });
    }
    // 기수변경(있으면)
    var jc = d.kraJockeyChanges || [];
    if (jc.length) {
      body.appendChild(el('div', 'font-size:14px;font-weight:800;color:#dc2626;margin-top:8px;', '⚡ 기수 교체 감지'));
      jc.forEach(function (ch) {
        body.appendChild(el('div', 'font-size:14px;color:#dc2626;margin:2px 0;',
          ch.chulNo + '번 ' + (ch.hrName || '') + ': ' + (ch.jkBefName || '?') + '→' + (ch.jkAftName || '?') +
          (ch.reason ? ' (' + ch.reason + ')' : '')));
      });
    }
    body.appendChild(el('div', 'font-size:12px;color:' + COLORS.sub + ';margin-top:8px;', '※ 과거 구간기록(S1F 선행력·G1F 추입력) 기반 예측 — 참고용'));
    c.appendChild(locked ? lockWrap(body, '경주 전개 예측은 프리미엄 전용입니다') : body);
    return c;
  }

  // ── 카드 3: 복병마 (없으면 null → 카드 숨김) ───────────────────
  function renderDark(d, locked) {
    var list = ((d.recommendation || {}).dark_horse) || [];
    if (!list.length) return null;

    var c = card(COLORS.amberBg, '2px solid #f59e0b');
    c.appendChild(el('div', 'font-size:20px;font-weight:900;color:' + COLORS.amberDeep + ';margin-bottom:10px;',
      '🐎 복병마 주목'));

    var body = el('div', '');
    list.forEach(function (h) {
      var row = el('div', 'display:flex;gap:14px;align-items:flex-start;margin-bottom:12px;');
      row.appendChild(el('div', 'font-size:40px;font-weight:900;line-height:1;color:' + COLORS.amberDeep
        + ';min-width:64px;text-align:center;', String(h.no) + '번'));
      var info = el('div', 'flex:1;min-width:0;');
      if (h.name) info.appendChild(el('div', 'font-size:18px;font-weight:800;color:' + COLORS.amberDeep + ';', h.name));
      (h.why || []).forEach(function (w) {
        info.appendChild(el('div', 'font-size:14px;color:' + COLORS.amberDeep + ';margin:2px 0;', '→ ' + w));
      });
      if (h.use) info.appendChild(el('div', 'font-size:14px;font-weight:700;color:' + COLORS.amber + ';margin-top:4px;',
        '→ 활용: ' + h.use));
      row.appendChild(info);
      body.appendChild(row);
    });
    c.appendChild(locked ? lockWrap(body, '복병마 분석은 프리미엄 전용입니다') : body);
    return c;
  }

  // ── ⚡ 단통 경주 (복승 중심·위험신호) ──────────────────────────
  //   회원 배팅 80% 복승 → 복승을 맨 위·크게, 삼복승은 작게 하단. 단통말은 탈락 위험으로 표시.
  function renderDansung(d, locked) {
    var ds = (d.recommendation || {}).dansung;
    if (!ds || !(ds.quinellaMain || []).length) return null;
    var c = card('#fff7ed', '3px solid ' + COLORS.amber);   // 주의색(주황)
    c.appendChild(el('div', 'font-size:20px;font-weight:900;color:' + COLORS.amberDeep + ';',
      ds.title || '⚡ 단통 경주'));
    c.appendChild(el('div', 'font-size:14px;color:' + COLORS.red + ';font-weight:700;margin:2px 0 8px;',
      '단통말 ' + ds.dansungHorse + '번 (' + (ds.dansungOdds != null ? ds.dansungOdds + '배' : '-') + ') → 탈락 위험 · 복병 집중 모드'));
    var body = el('div', '');
    body.appendChild(el('div', 'border-top:2px solid ' + COLORS.amber + '66;padding-top:8px;font-size:15px;font-weight:800;color:' + COLORS.amberDeep + ';', '복승 추천'));
    // ① 실질 유력, ② 복병 포함 — 크게
    (ds.quinellaMain || []).forEach(function (q, i) {
      var row = el('div', 'display:flex;align-items:baseline;gap:8px;margin:5px 0;');
      row.appendChild(el('span', 'font-size:26px;font-weight:900;color:' + COLORS.amberDeep + ';', (i === 0 ? '①' : (i === 1 ? '②' : '③')) + ' ' + q.text));
      if (q.label) row.appendChild(el('span', 'font-size:13px;color:' + COLORS.sub + ';', '← ' + q.label));
      body.appendChild(row);
    });
    // 복병 복승 필수(강조)
    if (ds.darkQuinella) {
      var dq = el('div', 'margin-top:6px;padding:6px 10px;background:' + COLORS.amberBg + ';border-radius:8px;');
      dq.appendChild(el('span', 'font-size:20px;font-weight:900;color:' + COLORS.amberDeep + ';', '🐎 복병 복승: ' + ds.darkQuinella.text));
      body.appendChild(dq);
    }
    // 삼복승 보험 — 작게 하단
    if ((ds.trifectaInsurance || []).length) {
      body.appendChild(el('div', 'margin-top:10px;border-top:1px dashed ' + COLORS.line + ';padding-top:6px;font-size:13px;color:' + COLORS.sub + ';',
        '삼복승 보험(참고): ' + ds.trifectaInsurance.join(' · ')));
    }
    c.appendChild(locked ? lockWrap(body, '단통 경주 복승 추천은 프리미엄 전용입니다') : body);
    return c;
  }

  // ── 🐎 복병 조합 (유력1+복병1+복병2) ──────────────────────────
  function renderDarkCombo(d, locked) {
    var dc = (d.recommendation || {}).dark_combo;
    if (!dc || !(dc.quinella || []).length) return null;
    var c = card(COLORS.amberBg, '2px solid #f59e0b');
    c.appendChild(el('div', 'font-size:18px;font-weight:900;color:' + COLORS.amberDeep + ';margin-bottom:8px;',
      dc.title || '🐎 복병 조합'));
    var body = el('div', '');
    (dc.quinella || []).forEach(function (q) {
      body.appendChild(el('div', 'font-size:20px;font-weight:800;color:' + COLORS.amberDeep + ';margin:3px 0;', '복승  ' + q));
    });
    (dc.trifecta || []).forEach(function (t) {
      body.appendChild(el('div', 'font-size:17px;font-weight:700;color:' + COLORS.amber + ';margin-top:4px;', '삼복승  ' + t));
    });
    c.appendChild(locked ? lockWrap(body, '복병 조합은 프리미엄 전용입니다') : body);
    return c;
  }

  // ── 예상 vs 결과 비교 ──────────────────────────────────────────
  function renderCompare(d) {
    var r = d.recommendation || {};
    var main = (r.main || [])[0], tri = (r.trifecta || [])[0];
    if (!main && !tri) return null;
    var res = d.result;

    var c = card('#ffffff', '2px solid ' + COLORS.line);
    c.appendChild(el('div', 'font-size:18px;font-weight:900;color:' + COLORS.ink + ';margin-bottom:10px;',
      res ? '📊 예상 vs 결과' : '📊 예상 조합'));

    function line(label, combo, hit) {
      var row = el('div', 'display:flex;align-items:center;gap:10px;margin:6px 0;font-size:18px;');
      row.appendChild(el('span', 'font-size:15px;color:' + COLORS.sub + ';min-width:64px;', label));
      row.appendChild(el('span', 'font-weight:900;color:' + COLORS.ink + ';', combo || '—'));
      if (hit != null) {
        row.appendChild(el('span', 'font-size:20px;font-weight:900;color:' + (hit ? COLORS.green : COLORS.red) + ';',
          hit ? '✅ 적중' : '❌ 미적중'));
      }
      return row;
    }

    if (!res) {
      if (main) c.appendChild(line('복승', main.combo, null));
      if (tri) c.appendChild(line('삼복승', tri.combo, null));
      c.appendChild(el('div', 'margin-top:8px;font-size:14px;color:' + COLORS.sub + ';',
        '경주가 끝나면 실제 결과와 비교해 보여드립니다.'));
      return c;
    }

    var top3 = res.top3 || [];
    // 적중 기준(불변): 복승 = 1·2착 / 삼복승 = 1·2·3착
    function hitOf(comboStr, need) {
      if (!comboStr) return null;
      var ns = comboStr.split('+').map(Number);
      var target = top3.slice(0, need);
      return ns.length === need && ns.every(function (n) { return target.indexOf(n) >= 0; });
    }
    c.appendChild(el('div', 'font-size:16px;color:' + COLORS.sub + ';margin-bottom:6px;',
      '실제 결과: ' + top3.join(' → ') + '착'));
    if (main) c.appendChild(line('복승', main.combo, hitOf(main.combo, 2)));
    if (tri) c.appendChild(line('삼복승', tri.combo, hitOf(tri.combo, 3)));
    return c;
  }

  // ── 상세: 배당판 매트릭스 ──────────────────────────────────────
  //   행 = 작은 말번호 · 열 = 큰 말번호 (실제 배당판과 동일 기준. 예: 3+4 → 3행 4열)
  function renderMatrix(d) {
    var nos = d.horse_nos || [], mx = d.matrix || {};
    if (nos.length < 2) return el('div', 'font-size:16px;color:' + COLORS.sub + ';', '표시할 배당이 없습니다.');

    var wrap = el('div', 'overflow-x:auto;-webkit-overflow-scrolling:touch;');
    var tbl = el('table', 'border-collapse:separate;border-spacing:3px;font-size:16px;');
    // 확장 오버레이(overlay.js locateBoardMatrix)가 이 표를 '실제 배당판'으로 오인해 강조 셀을 겹쳐
    //   그리지 않도록 제외 표식을 단다(분석기 페이지에도 overlay.js 가 주입되기 때문).
    tbl.setAttribute('data-mb-board', '1');

    var CELL = 'min-width:60px;height:52px;text-align:center;border-radius:8px;font-size:16px;box-sizing:border-box;padding:2px 6px;';
    var HEAD = CELL + 'font-weight:900;font-size:18px;background:' + COLORS.grayBg + ';color:' + COLORS.ink + ';';

    var thead = el('thead'), hr = el('tr');
    hr.appendChild(el('th', HEAD, '복승'));
    nos.slice(1).forEach(function (n) { hr.appendChild(el('th', HEAD, String(n))); });
    thead.appendChild(hr); tbl.appendChild(thead);

    var tbody = el('tbody');
    nos.slice(0, -1).forEach(function (rowNo, ri) {
      var tr = el('tr');
      tr.appendChild(el('th', HEAD, String(rowNo)));
      nos.slice(1).forEach(function (colNo, cj) {
        var ci = cj + 1;
        if (ci < ri) { tr.appendChild(el('td', CELL, '')); return; }               // 하단 거울면(정렬용 공백)
        if (ci === ri) {                                                            // 대각선(같은 말)
          tr.appendChild(el('td', CELL + 'background:#334155;', ''));
          return;
        }
        var key = Math.min(rowNo, colNo) + '-' + Math.max(rowNo, colNo);
        var cell = mx[key];
        if (!cell) { tr.appendChild(el('td', CELL + 'color:' + COLORS.gray + ';', '·')); return; }
        var bg = '#ffffff', fg = COLORS.ink, bd = '1px solid ' + COLORS.line, w = '700';
        if (cell.signal === 'green') { bg = COLORS.greenBg; fg = COLORS.greenDeep; bd = '3px solid ' + COLORS.green; w = '900'; }
        else if (cell.signal === 'blue') { bg = COLORS.blueBg; fg = '#1e3a8a'; bd = '3px solid ' + COLORS.blue; w = '800'; }
        else if (cell.signal === 'red') { bg = COLORS.redBg; fg = '#7f1d1d'; bd = '3px solid ' + COLORS.red; w = '800'; }
        var td = el('td', CELL + 'background:' + bg + ';color:' + fg + ';border:' + bd + ';font-weight:' + w + ';',
          (Math.round(cell.odds * 10) / 10) + (cell.signal === 'red' ? ' ▼' : ''));
        td.title = key.replace('-', '+') + ' = ' + cell.odds + '배'
          + (cell.drop != null ? ' · 배당 하락 ' + cell.drop + '%' : '')
          + (cell.signal === 'green' ? ' · 최종 추천' : cell.signal === 'blue' ? ' · 유력' : cell.signal === 'red' ? ' · 급락' : '');
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    tbl.appendChild(tbody);
    wrap.appendChild(tbl);

    var lg = el('div', 'margin-top:10px;display:flex;flex-wrap:wrap;gap:12px;font-size:14px;color:' + COLORS.sub + ';');
    [['최종 추천', COLORS.green], ['유력', COLORS.blue], ['급락 ▼', COLORS.red], ['일반', COLORS.line]]
      .forEach(function (p) {
        var i = el('span', 'display:inline-flex;align-items:center;gap:5px;');
        i.appendChild(el('span', 'width:16px;height:16px;border-radius:4px;border:3px solid ' + p[1] + ';display:inline-block;'));
        i.appendChild(el('span', '', p[0]));
        lg.appendChild(i);
      });
    lg.appendChild(el('span', '', '· 행 = 작은 번호, 열 = 큰 번호 (예: 3+4 → 3행 4열)'));
    wrap.appendChild(lg);
    return wrap;
  }

  // ── 상세: 말별 신호 카드 ───────────────────────────────────────
  function renderHorseCards(d) {
    var list = d.horse_cards || [];
    if (!list.length) return el('div', 'font-size:16px;color:' + COLORS.sub + ';', '말별 정보가 없습니다.');
    var grid = el('div', 'display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:10px;');
    list.forEach(function (h) {
      var bg = '#ffffff', bd = COLORS.line, label = '';
      if (h.special) { bg = COLORS.purpleBg; bd = COLORS.purple; label = '💎 특별'; }
      else if (h.role === 'dark') { bg = COLORS.amberBg; bd = '#f59e0b'; label = '🐎 복병'; }
      else if (h.role === 'fav') { bg = COLORS.greenBg; bd = COLORS.green; label = '⭐ 유력'; }
      else if (h.role === 'cut' || h.role === 'weakcut') { bg = COLORS.grayBg; bd = COLORS.gray; label = '제외 권장'; }

      var c = el('div', 'background:' + bg + ';border:2px solid ' + bd + ';border-radius:12px;padding:12px;min-height:52px;');
      var top = el('div', 'display:flex;align-items:center;gap:10px;');
      top.appendChild(el('span', 'font-size:28px;font-weight:900;color:' + COLORS.ink + ';', String(h.no) + '번'));
      if (label) top.appendChild(el('span', 'font-size:15px;font-weight:800;color:' + bd + ';', label));
      if (h.odds) top.appendChild(el('span', 'font-size:15px;color:' + COLORS.sub + ';margin-left:auto;', oddsTxt(h.odds)));
      c.appendChild(top);
      if (h.grade) c.appendChild(el('div', 'margin-top:4px;font-size:15px;font-weight:700;color:' + COLORS.sub + ';', h.grade));
      (h.tags || []).forEach(function (t) {
        c.appendChild(el('span', 'display:inline-block;margin:4px 4px 0 0;padding:2px 8px;border-radius:999px;'
          + 'font-size:13px;background:#ffffffaa;border:1px solid ' + bd + ';color:' + COLORS.sub + ';', t));
      });
      (h.why || []).forEach(function (w) {
        c.appendChild(el('div', 'margin-top:4px;font-size:14px;color:' + COLORS.sub + ';', '→ ' + w));
      });
      grid.appendChild(c);
    });
    return grid;
  }

  // ── 상세: 추천 조합 전체 ───────────────────────────────────────
  function renderAllCombos(d) {
    var r = d.recommendation || {};
    var box = el('div', '');
    function section(title, items, col) {
      if (!items || !items.length) return;
      box.appendChild(el('div', 'margin:12px 0 6px;font-size:17px;font-weight:900;color:' + col + ';', title));
      items.forEach(function (it) {
        var row = el('div', 'display:flex;flex-wrap:wrap;align-items:baseline;gap:10px;padding:8px 0;'
          + 'border-bottom:1px dashed ' + COLORS.line + ';');
        row.appendChild(el('span', 'font-size:20px;font-weight:900;color:' + COLORS.ink + ';min-width:96px;', it.combo));
        row.appendChild(el('span', 'font-size:17px;font-weight:800;color:' + col + ';',
          oddsTxt(it.odds) + (it.estimated ? ' (예상)' : '')));
        if (it.label) row.appendChild(el('span', 'font-size:14px;color:' + COLORS.sub + ';', it.label));
        if (it.why) row.appendChild(el('div', 'flex-basis:100%;font-size:14px;color:' + COLORS.sub + ';', '→ ' + it.why));
        box.appendChild(row);
      });
    }
    section('🔒 최종 복승', r.main, COLORS.green);
    section('삼복승', r.trifecta, COLORS.blue);
    section('💎 특별 추천', r.special, COLORS.purple);
    if (!box.firstChild) box.appendChild(el('div', 'font-size:16px;color:' + COLORS.sub + ';', '아직 추천 조합이 없습니다.'));
    return box;
  }

  // ── 상세 펼치기 컨테이너 ───────────────────────────────────────
  function renderDetails(d, state) {
    var box = el('div', '');
    var btn = el('button', 'width:100%;min-height:52px;font-size:18px;font-weight:800;cursor:pointer;'
      + 'background:#ffffff;color:' + COLORS.ink + ';border:2px solid ' + COLORS.line + ';border-radius:12px;',
      state.open ? '▲ 상세 분석 접기' : '▼ 상세 분석 펼치기');
    box.appendChild(btn);

    var panel = el('div', 'margin-top:12px;' + (state.open ? '' : 'display:none;'));
    function tabSection(title, node) {
      var s = card('#ffffff', '2px solid ' + COLORS.line);
      s.appendChild(el('div', 'font-size:18px;font-weight:900;color:' + COLORS.ink + ';margin-bottom:10px;', title));
      s.appendChild(node);
      return s;
    }
    panel.appendChild(tabSection('📊 배당판', renderMatrix(d)));
    panel.appendChild(tabSection('🐴 말별 분석', renderHorseCards(d)));
    panel.appendChild(tabSection('🎯 추천 조합 전체', renderAllCombos(d)));
    box.appendChild(panel);

    btn.addEventListener('click', function () {
      state.open = !state.open;
      panel.style.display = state.open ? '' : 'none';
      btn.textContent = state.open ? '▲ 상세 분석 접기' : '▼ 상세 분석 펼치기';
    });
    return box;
  }

  // ── [단순화] 용어 순화(나이 많은 사용자·전문용어 제거) ────────────
  function simpleTerm(s) {
    return String(s == null ? '' : s)
      .replace(/역배열/g, '주목말').replace(/초과급락/g, '집중주목')
      .replace(/signalScore/gi, '신뢰도').replace(/smartMoney/gi, '큰돈유입')
      .replace(/스마트\s?머니/g, '큰돈유입').replace(/BMED/g, '분석결과')
      .replace(/quinella/gi, '복승').replace(/trifecta/gi, '삼복승');
  }

  // ── [기본 화면] 카드 1 — 최종 추천 (진한 초록·크게·24px+) ──────────
  function renderSimpleMain(d, locked) {
    var r = d.recommendation || {};
    var main = r.main || [], tri = r.trifecta || [];
    var c = card(COLORS.greenDeep, '4px solid ' + COLORS.green, '20px');   // 진한 초록
    c.appendChild(el('div', 'font-size:28px;font-weight:900;color:#ffffff;margin-bottom:12px;', '🔒 지금 사세요!'));
    // [출전취소] 競走除外 감지 마번 경고(추천에서 이미 제거됨)
    var scr = d.scratched || [];
    if (scr.length) {
      c.appendChild(el('div', 'font-size:20px;font-weight:900;color:#fecaca;background:#7f1d1d;'
        + 'border-radius:8px;padding:8px 12px;margin-bottom:10px;',
        '⚠️ ' + scr.join('·') + '번 출전 취소 — 제거됨'));
    }
    var body = el('div', '');
    if (!main.length && !tri.length) {
      body.appendChild(el('div', 'font-size:24px;font-weight:800;color:#ffffff;', '⏳ 아직 추천이 없습니다'));
      var g0 = d.confidence || {};
      if (g0.message) body.appendChild(el('div', 'margin-top:8px;font-size:18px;color:#bbf7d0;', simpleTerm(g0.message)));
    } else {
      var NUM = ['①', '②', '③', '④', '⑤'];
      main.slice(0, 3).forEach(function (m, i) {
        var row = el('div', 'margin:8px 0;display:flex;align-items:baseline;flex-wrap:wrap;');
        row.appendChild(el('span', 'font-size:24px;font-weight:800;color:#bbf7d0;', '복승 ' + (NUM[i] || (i + 1)) + ' '));
        row.appendChild(el('span', 'font-size:34px;font-weight:900;color:#ffffff;margin:0 6px;', m.combo));
        if (m.odds != null) row.appendChild(el('span', 'font-size:24px;font-weight:800;color:#86efac;', '(' + oddsTxt(m.odds) + ')'));
        body.appendChild(row);
      });
      if (tri[0]) {
        var t = el('div', 'margin-top:12px;padding-top:12px;border-top:2px solid #ffffff44;');
        t.appendChild(el('span', 'font-size:22px;font-weight:800;color:#bbf7d0;', '삼복승: '));
        t.appendChild(el('span', 'font-size:30px;font-weight:900;color:#ffffff;', tri[0].combo));
        body.appendChild(t);
      }
    }
    c.appendChild(locked ? lockWrap(body, '최종 추천은 프리미엄 전용입니다') : body);
    return c;
  }

  // ── [기본 화면] 카드 2 — 복병 주목 (노란색·18px·작게) ────────────
  function renderSimpleDark(d, locked) {
    var list = (d.recommendation || {}).dark_horse || [];
    if (!list.length) return null;                       // 복병 없으면 카드 숨김
    var c = card('#fef9c3', '3px solid ' + COLORS.amber, '16px');   // 노란색
    c.appendChild(el('div', 'font-size:20px;font-weight:900;color:' + COLORS.amberDeep + ';margin-bottom:8px;', '🐎 복병 주목'));
    var body = el('div', '');
    list.forEach(function (h) {
      var why = (h.why && h.why.length) ? simpleTerm(h.why[0]) : (h.smart_money ? '큰돈 유입' : '실질유력');
      var row = el('div', 'font-size:18px;font-weight:800;color:' + COLORS.amberDeep + ';margin:5px 0;');
      row.textContent = h.no + '번' + (h.odds != null ? ' (' + oddsTxt(h.odds) + ')' : '') + ' — ' + why;
      body.appendChild(row);
    });
    c.appendChild(locked ? lockWrap(body, '복병 분석은 프리미엄 전용입니다') : body);
    return c;
  }

  // ── 메인 렌더 ──────────────────────────────────────────────────
  function render(root, d, opts, state) {
    clear(root);
    root.style.cssText = 'font-family:-apple-system,BlinkMacSystemFont,"Malgun Gothic",sans-serif;'
      + 'font-size:16px;color:' + COLORS.ink + ';max-width:100%;box-sizing:border-box;';

    if (!d || d.ok === false) {
      var w = card('#ffffff', '2px solid ' + COLORS.line);
      w.appendChild(el('div', 'font-size:18px;font-weight:800;', '⏳ 배당 수집 대기 중'));
      w.appendChild(el('div', 'margin-top:6px;font-size:15px;color:' + COLORS.sub + ';',
        (d && d.error) || '아직 이 경주의 배당이 들어오지 않았습니다.'));
      root.appendChild(w);
      return;
    }

    var locked = (opts.plan || 'free') === 'free';
    // ── [기본 화면·항상 보임] 경주 헤더 + 카드1(최종추천 크게) + 카드2(복병 작게) ──
    root.appendChild(renderHeader(d));
    root.appendChild(renderSimpleMain(d, locked));       // 카드1 — 최종 추천(진한 초록·크게)
    // [개선2] 페이스 한 줄(빠른/느린만·14px 회색) — 카드1 바로 아래. 보통은 표시 안 함. 상세는 ▼자세히에 유지.
    var pc = d.pace;
    if (pc && (pc.pace === '빠른' || pc.pace === '느린')) {
      root.appendChild(el('div', 'font-size:14px;color:' + COLORS.sub + ';margin:0 0 12px 4px;',
        pc.paceLabel + ' · ' + (pc.pace === '빠른' ? '추입마 유리' : '선행마 유리')));
    }
    var sdark = renderSimpleDark(d, locked);
    if (sdark) root.appendChild(sdark);                  // 카드2 — 복병 주목(노랑·작게·없으면 숨김)

    // ── [▼ 자세히 보기] 기존 모든 카드를 접기 안에 배치(삭제 아님·펼침 상태 유지) ──
    var detail = el('div', 'margin-top:8px;display:' + (state.detailOpen ? 'block' : 'none') + ';');
    var toggle = el('button', 'width:100%;min-height:56px;font-size:18px;font-weight:800;'
      + 'color:' + COLORS.ink + ';background:' + COLORS.grayBg + ';border:2px solid ' + COLORS.line + ';'
      + 'border-radius:12px;margin:6px 0;cursor:pointer;');
    toggle.textContent = (state.detailOpen ? '▲ 접기' : '▼ 자세히 보기');
    toggle.onclick = function () {
      state.detailOpen = !state.detailOpen;
      detail.style.display = state.detailOpen ? 'block' : 'none';
      toggle.textContent = state.detailOpen ? '▲ 접기' : '▼ 자세히 보기';
    };
    root.appendChild(toggle);
    // 기존 카드 전부 접기 컨테이너로(무삭제)
    var picks = renderPicks(d, locked);
    if (picks) detail.appendChild(picks);
    var axis = renderAxis(d, locked);
    if (axis) detail.appendChild(axis);        // 🎯 핵심 축 2두 전략
    var pace = renderPace(d, locked);
    if (pace) detail.appendChild(pace);        // 🏇 편성 시나리오(A/B)
    var kflow = renderKraFlow(d, locked);
    if (kflow) detail.appendChild(kflow);      // 🏇 경주 전개 예측(KRA 구간기록·복병)
    var dsg = renderDansung(d, locked);
    if (dsg) detail.appendChild(dsg);          // ⚡ 단통 경주
    var dark = renderDark(d, locked);
    if (dark) detail.appendChild(dark);        // 복병마 상세
    var dc = renderDarkCombo(d, locked);
    if (dc) detail.appendChild(dc);            // 🐎 복병 조합
    var cmp = renderCompare(d);
    if (cmp) detail.appendChild(cmp);
    detail.appendChild(renderDetails(d, state));
    root.appendChild(detail);

    var meta = el('div', 'font-size:13px;color:' + COLORS.sub + ';text-align:right;margin-top:4px;');
    meta.textContent = '30초마다 자동 업데이트' + (d.updated_at
      ? ' · 갱신 ' + new Date(d.updated_at * 1000).toLocaleTimeString('ko-KR') : '');
    root.appendChild(meta);
  }

  // ── 공개 API ───────────────────────────────────────────────────
  function mount(root, opts) {
    opts = opts || {};
    var state = { open: false };            // 펼침 상태는 30초 재렌더에도 유지
    var timer = null, dead = false, raceKey = opts.raceKey;

    function url() {
      var base = (opts.apiBase || '').replace(/\/$/, '');
      return base + '/api/public/matrix/' + encodeURIComponent(raceKey || '');
    }

    function refresh() {
      if (dead || !raceKey) return Promise.resolve();
      return fetch(url(), { credentials: 'same-origin' })
        .then(function (r) { return r.json().catch(function () { return { ok: false }; }); })
        .then(function (d) { if (!dead) render(root, d, opts, state); })
        .catch(function (e) {
          if (!dead) render(root, { ok: false, error: '서버에 연결할 수 없습니다. (' + e.message + ')' }, opts, state);
        });
    }

    if (opts.autoRefresh !== false) timer = setInterval(refresh, REFRESH_MS);
    refresh();

    return {
      refresh: refresh,
      setRaceKey: function (k) { raceKey = k; return refresh(); },
      setPlan: function (p) { opts.plan = p; return refresh(); },
      destroy: function () { dead = true; if (timer) clearInterval(timer); clear(root); },
    };
  }

  global.MatrixBoard = { mount: mount, REFRESH_MS: REFRESH_MS };
})(window);
