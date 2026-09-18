# -*- coding: utf-8 -*-
"""확장용 경기장 별칭표 생성 — app.py 의 `_TRACK_GROUPS`·`_TRACK_ALIAS` 를 **ast 로 읽어**(import 안 함)
`chrome-extension/track_alias.js` 를 만든다.

🔴 왜 (2026-09-18 대표: 「경륜 고질병 — 경기장 글자가 틀리면 오버레이가 안 잡힌다 · 지금 토야마」)
  서버는 「토야마」를 「도야마」로 정규화해 저장·응답하는데, 오버레이는 표시 직전에
  「분석 데이터 경주명 ↔ 배당판 경주명」을 **글자 그대로** 대조한다(overlay.js readData · pollOverlayAnalyze).
  ⇒ 배당판 「토야마 11경주」 ↔ 응답 「도야마 11경주」 = 불일치 → 분석을 버려 패널이 안 뜬다.
  서버 별칭표를 확장이 모르는 것이 뿌리다. 별칭을 서버에만 추가해 온 그동안의 수리가 화면에는 안 닿았다.

사용
  python tools/gen_ext_track_alias.py            # 생성
  python tools/gen_ext_track_alias.py --check    # 낡았으면 rc=1 (app.py 별칭을 고친 뒤 재생성 누락 방지)
⚠ `_TRACK_GROUPS` 를 고치면 이 도구를 다시 돌리고 확장 버전을 올린다.
"""
import ast
import io
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "chrome-extension", "track_alias.js")


def load_groups():
    src = io.open(os.path.join(BASE, "app.py"), encoding="utf-8").read()
    got = {}
    for n in ast.parse(src).body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if getattr(t, "id", "") in ("_TRACK_GROUPS", "_TRACK_ALIAS"):
                    got[t.id] = ast.literal_eval(n.value)
    return got.get("_TRACK_GROUPS") or {}, got.get("_TRACK_ALIAS") or {}


def build():
    groups, alias = load_groups()
    amap = {}
    for canon, als in groups.items():
        amap[str(canon).lower()] = canon
        for a in als or []:
            amap.setdefault(str(a).lower(), canon)
    for a, canon in alias.items():
        amap.setdefault(str(a).lower(), canon)
    body = json.dumps(amap, ensure_ascii=False, sort_keys=True, indent=0).replace("\n", "")
    js = u"""// ⚠ 자동 생성 파일 — tools/gen_ext_track_alias.py 가 app.py 의 _TRACK_GROUPS·_TRACK_ALIAS 에서 만든다. 손으로 고치지 않는다.
// [2026-09-18] 경기장 표기 변형(토야마↔도야마 · 타치카와↔다치카와 · 富山 …)을 확장도 서버와 **같은 표**로 본다.
//   별칭표에 없는 변형은 발음 정규화(격음·경음→평음 · 쓰/츠 · 장음 겹침)로 한 번 더 맞춘다.
(function (g) {
  if (g.KB_TRACK_ALIAS) return;
  g.KB_TRACK_ALIAS = %s;
  var CHO = { 15: 0, 16: 3, 17: 7, 14: 12, 1: 0, 4: 3, 8: 7, 13: 12, 10: 9 };   // ㅋ→ㄱ ㅌ→ㄷ ㅍ→ㅂ ㅊ→ㅈ · ㄲ→ㄱ ㄸ→ㄷ ㅃ→ㅂ ㅉ→ㅈ ㅆ→ㅅ
  function phon(s) {
    s = String(s || '').replace(/쓰/g, '츠');
    var out = '', prev = '';
    for (var i = 0; i < s.length; i++) {
      var c = s.charCodeAt(i), ch = s[i];
      if (c >= 0xAC00 && c <= 0xD7A3) {
        var k = c - 0xAC00, cho = Math.floor(k / 588), rest = k %% 588;
        if (CHO[cho] != null) cho = CHO[cho];
        ch = String.fromCharCode(0xAC00 + cho * 588 + rest);
        // 장음 겹침: 받침 없는 「ㅇ」 초성 음절이 바로 앞 음절의 모음과 같으면 접는다(오오이→오이 · 토오→토)
        if (cho === 11 && rest %% 28 === 0 && prev) {
          var pk = prev.charCodeAt(0) - 0xAC00;
          if (pk >= 0 && pk %% 28 === 0 && Math.floor((pk %% 588) / 28) === Math.floor(rest / 28)) continue;
        }
      }
      out += ch; prev = ch;
    }
    return out;
  }
  var PHON_ALIAS = {};
  Object.keys(g.KB_TRACK_ALIAS).forEach(function (a) { var p = phon(a); if (!(p in PHON_ALIAS)) PHON_ALIAS[p] = g.KB_TRACK_ALIAS[a]; });
  function clean(v) {
    return String(v || '').replace(/\\[[^\\]]*\\]/g, '').replace(/[\\s　]/g, '').replace(/(경륜|競輪|경마|競馬|경정|競艇)$/, '').toLowerCase();
  }
  // 경기장명 → 서버 정식 키의 발음형. 별칭표(정확) → 별칭표(발음형) → 자기 발음형.
  g.kbVenueCanon = function (v) {
    var s = clean(v);
    if (!s) return '';
    var c = g.KB_TRACK_ALIAS[s] || PHON_ALIAS[phon(s)] || s;
    return phon(String(c).toLowerCase());
  };
  // 두 경기장명이 같은 곳인가. 둘 중 하나라도 비면 false(호출부가 「못 읽음」을 따로 다룬다).
  g.kbVenueSame = function (a, b) {
    var ca = g.kbVenueCanon(a), cb = g.kbVenueCanon(b);
    if (!ca || !cb) return false;
    return ca === cb;   // ⚠ 포함 대조 금지 — 발음형에서는 이즈카↔이즈 · 마쓰사카↔사가 가 묶인다(tests/run_ext_track_alias.js ④ 가 잡음)
  };
  g.kbVenuePhon = phon;
})(typeof self !== 'undefined' ? self : this);
""" % body
    return js


def main():
    js = build()
    if "--check" in sys.argv:
        cur = io.open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
        if cur.replace("\r\n", "\n") != js:
            print("🔴 track_alias.js 가 app.py 별칭표와 다르다 — python tools/gen_ext_track_alias.py 재실행 + 확장 버전 올림")
            return 1
        print("🟢 track_alias.js 최신")
        return 0
    io.open(OUT, "w", encoding="utf-8", newline="\n").write(js)
    print("생성:", OUT, "· 별칭", js.count('":"') if '":"' in js else js.count('": "'))
    return 0


if __name__ == "__main__":
    sys.exit(main())
