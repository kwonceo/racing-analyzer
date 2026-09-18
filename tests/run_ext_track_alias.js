// 확장 경기장 별칭 대조(track_alias.js) 자기검증 — 원칙 17(통과·실패 둘 다) · 원칙 20(오탐: 서로 다른 경기장이 같아지면 안 된다)
//   node tests/run_ext_track_alias.js
const fs = require('fs');
const path = require('path');
const g = {};
new Function('self', fs.readFileSync(path.join(__dirname, '..', 'chrome-extension', 'track_alias.js'), 'utf8'))(g);

let ok = 0, n = 0;
const t = (name, cond) => { n++; if (cond) ok++; console.log((cond ? '✅ ' : '🔴 ') + name); };

// ① 같은 경기장이어야 하는 쌍 (배당판 표기 ↔ 서버 저장 키)
[['토야마', '도야마'], ['타치카와', '다치카와'], ['고치', '코치'], ['코치[륜]', '코치'], ['키시와다', '기시와다'],
 ['마츠야마', '마쓰야마'], ['타케오', '다케오'], ['富山', '도야마'], ['토요하시', '도요하시'], ['오오이', '오이'],
 ['가와사키', '카와사키'], ['코쿠라', '고쿠라'], ['타마노', '다마노'], ['토야마 경륜', '도야마']]
  .forEach(([a, b]) => t('같음 ' + a + ' = ' + b, g.kbVenueSame(a, b) === true));

// ② 달라야 하는 쌍 — 하나라도 같다고 나오면 다른 경주 분석이 화면에 뜬다
[['도야마', '다치카와'], ['나고야', '사가'], ['카와사키', '기시와다'], ['소노다', '오이'], ['마쓰야마', '마쓰도'],
 ['야히코', '이토'], ['부산', '제주'], ['다케오', '다마노'], ['도야마', '와카야마']]
  .forEach(([a, b]) => t('다름 ' + a + ' ≠ ' + b, g.kbVenueSame(a, b) === false));

// ③ 못 읽은 값은 같다고 하지 않는다
t('빈 값 → false', g.kbVenueSame('', '도야마') === false);

// ④ 충돌 검사: 서로 다른 정식 키가 같은 발음형·포함 관계로 묶이면 안 된다(원칙 25 — 종목 섞이는 지명을 합치지 않는다)
const canon = Array.from(new Set(Object.values(g.KB_TRACK_ALIAS)));
const bad = [];
for (let i = 0; i < canon.length; i++) for (let j = i + 1; j < canon.length; j++) {
  if (g.kbVenueSame(canon[i], canon[j])) bad.push(canon[i] + '↔' + canon[j]);
}
t('정식 키 ' + canon.length + '개 사이 충돌 0 (발견: ' + (bad.join(', ') || '없음') + ')', bad.length === 0);

console.log('결과 ' + ok + '/' + n);
process.exit(ok === n ? 0 : 1);
