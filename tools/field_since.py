# -*- coding: utf-8 -*-
"""[필드 배선일 자동 추출 (2026-08-03 승인 신설)] — **분모 오류를 구조적으로 막는다.**

■ 왜 만들었나 (실사고)
  2026-08-03(47차)에 *"전적 결손 1,496두"* 라고 보고했는데, 그건 **`raw_profile` 배선일
  (2026-07-30) 이전 로그를 분모에 넣고 센 값**이었다. 배선 전에는 그 칸이 **없었던 것**이지
  결손이 아니다. 실체는 **317두**였다 — 4.7배 과대평가.
  🔴 같은 함정이 **배선 시점이 있는 모든 필드**에 있다: pastPops(08-03) · 5키(08-02) ·
     prev1/prev2(08-02) · gradeAtBonus(07-30) · raw_profile(07-29~30) · declaredStyle(07-30) …

■ 왜 **git log** 인가 (손으로 적지 않는 이유)
  배선일 표를 사람이 손으로 적으면 **목록이 또 갈린다.** 2026-08-03 하루에만 "같은 목록이
  여러 벌로 갈려 구멍이 난" 사례를 네 번 봤다(`_JP_TRACKS`·`_JRA_TRACKS`·`_TRACK_GROUPS`·`_JP_BABA_CODE`).
  ⇒ **git 이 이미 알고 있는 사실을 다시 적지 않는다.**

■ ⚠ 한계 (반드시 알고 쓴다)
  ① `-S` 는 "그 문자열이 추가/삭제된 커밋"을 찾는다 → 주석·문서에 먼저 나오면 **실제 배선보다
     이른 날짜**가 잡힐 수 있다. 그래서 파일을 좁히고(`-- app.py`) 저장 키 형태(`"pastPops"`)로 준다.
  ② 필드가 **한 번 지워졌다 다시 생기면** 최초 등장이 실제 배선일이 아니다.
  ③ 🔴 **추출 실패면 `None` 을 돌려준다. 추측으로 날짜를 만들지 않는다.**
     부르는 쪽은 "분모 제한 불가"를 **출력에 명시**해야 한다.
  ④ 🔴 **이 모듈은 「도구로 측정할 때」만 도움이 된다.** 세션 중 즉석 코드는 이걸 안 부르므로
     여전히 같은 실수를 할 수 있다 — **실질 방어선은 원칙 15(측정은 도구로)** 이고,
     이 모듈은 그 원칙을 지켰을 때 **자동으로 옳게 만들어 주는 장치**다.

■ 완전 읽기 전용 — 저장소를 수정하지 않는다(캐시 파일만 쓴다).
"""
import json
import os
import re
import subprocess
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(BASE, "data", "_field_since.json")
CACHE_TTL = 7 * 24 * 3600          # 배선일은 거의 안 바뀐다 — 일주일 캐시

# 자주 쓰는 필드의 검색어(저장 키 형태). 없으면 field 를 그대로 쓴다.
#   ⚠ 이건 **목록이 아니라 별칭**이다 — 여기 없어도 동작한다(추측 없이 그대로 검색).
_PROBE = {
    "pastPops": '"pastPops"',
    "recentPlacings": '"recentPlacings":',
    "fieldSizes": '"fieldSizes":',
    "corners": '"corners":',
    "last3fList": '"last3fList":',
    "pastDistances": '"pastDistances":',
    "gradeAtBonus": '"gradeAtBonus"',
    "raw_profile": "_raw_profile_snapshot",
    "declaredStyle": '"declaredStyle"',
    "prev1": '"prev1"',
    "surface": '"surface": shutsuba',
    "paceBonusBase": '"paceBonusBase"',
}


def _load_cache():
    try:
        return json.load(open(CACHE, encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(d):
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        tmp = CACHE + ".tmp%d" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, CACHE)
    except Exception:
        pass                                   # 캐시 실패가 측정을 막으면 안 된다


def _git_first_date(probe, path="app.py"):
    """그 문자열이 **처음 등장한** 커밋 날짜(YYYY-MM-DD) 또는 None."""
    try:
        out = subprocess.run(
            ["git", "log", "-S", probe, "--date=short", "--format=%ad", "--", path],
            cwd=BASE, capture_output=True, text=True, timeout=60)
        lines = [x.strip() for x in (out.stdout or "").splitlines() if x.strip()]
        return lines[-1] if lines else None    # git log 는 최신순 → 마지막이 최초 등장
    except Exception:
        return None


def since(field, path="app.py", refresh=False):
    """필드 배선일 → 'YYYY_MM_DD' (analysis_log 파일명 규약과 같은 형태). 모르면 **None**."""
    key = "%s|%s" % (field, path)
    cache = _load_cache()
    ent = cache.get(key)
    if ent and not refresh and (time.time() - float(ent.get("t") or 0) < CACHE_TTL):
        return ent.get("since")
    d = _git_first_date(_PROBE.get(field, field), path)
    val = d.replace("-", "_") if d and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) else None
    cache[key] = {"since": val, "t": time.time(), "probe": _PROBE.get(field, field)}
    _save_cache(cache)
    return val


def filter_files(files, field, path="app.py", key_fn=None):
    """배선일 이전 파일을 분모에서 뺀다 → (남은 파일, 제외 건수, 배선일).

    🔴 **제외 건수를 반드시 함께 돌려준다** — 조용히 빼면 "왜 분모가 줄었나"를 모른다.
    🔴 배선일을 모르면(`None`) **아무것도 빼지 않는다**(추측 금지). 부르는 쪽이 그렇게 표시한다.
    """
    s = since(field, path)
    if not s:
        return list(files), 0, None
    kf = key_fn or (lambda f: os.path.basename(f)[:10])
    keep = [f for f in files if kf(f) >= s]
    return keep, len(files) - len(keep), s


def note(field, excluded, s):
    """측정 출력에 그대로 찍을 한 줄. 🔴 분모를 줄였으면 **항상 이 줄을 병기**한다."""
    if not s:
        return "⚠ `%s` 배선일 추출 실패 → **분모 제한 불가**(배선 전 파일이 섞여 있을 수 있다)" % field
    return "⚠ 분모: `%s` 배선일 %s 이전 **%d건 제외**(배선 전은 결손이 아니다)" % (
        field, s.replace("_", "-"), excluded)


if __name__ == "__main__":
    import sys
    fields = sys.argv[1:] or list(_PROBE.keys())
    print("=" * 72)
    print("필드 배선일 (git log -S · 캐시 %s)" % CACHE)
    print("=" * 72)
    for f in fields:
        s = since(f)
        print("  %-18s %s" % (f, s or "🔴 추출 실패 → 분모 제한 불가"))
    print("\n⚠ 한계: 주석 선등장·삭제 후 재추가 시 실제 배선일과 다를 수 있다(모듈 docstring 참조).")
