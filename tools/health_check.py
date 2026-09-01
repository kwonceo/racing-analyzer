# -*- coding: utf-8 -*-
"""[완료 조건 체크리스트 2026-07-30] 17항목 자동 판정 — **완전 읽기 전용**.

설계 의도(권대표 지시 2026-07-30)
  · "감시"가 아니라 **완료 조건 체크리스트**다. 각 항목에 숫자 완료선이 있고, 충족되면 알림에서 빠진다.
    **조용해지는 것이 곧 진행 상황이다.**
  · 지금까지 발견이 대부분 **육안**이었다. 외부에 있으면 0건이 된다 → 외부에서 확인 가능해야 한다.

🔴 이 파일의 가장 중요한 규약 — **`denominator`(분모 정의)는 필수 필드다.**
  2026-07-30 실제 사고: "스냅샷 3틱+ 비율"을 **스냅샷 보유 파일만** 분모로 잡으면 81.6% 인데,
  **0틱 경주 123개를 포함**하면 69.4% 다. 즉 **가장 실패한 경주가 통계에서 사라진다.**
  같은 함정이 다른 항목에도 있다(스키마 도입 이후 행만 세기 · 판정 가능 경주만 세기 …).
  → 모든 항목은 분모를 **문자열로 명시**하고, 애매하면 **넓은 쪽(실패가 포함되는 쪽)** 을 고른다.

⚠ 추천·수집·학습에 일절 개입하지 않는다. 파일을 읽기만 하고 쓰지 않는다.
⚠ 측정 불가한 항목은 `ok=null` + `reason` — **억지로 통과시키지 않는다.**

사용:
  python tools/health_check.py            # 사람이 읽는 표
  python tools/health_check.py --json     # JSON
  (서버) GET /api/health/checklist
"""
import argparse
import collections
import glob
import json
import os
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HIST_DIR = os.path.join(BASE, "data", "odds_history")
LOG_DIR = os.path.join(BASE, "logs")

# ── 완료선 상수 ─────────────────────────────────────────────────────────
#  🔴 여기 숫자를 낮추려면 **근거와 함께** CLAUDE.md 에 기록하고 바꾼다(사후 하향 방지).
SNAP_MIN_TICKS = 3          # '수집 성공' 판정 최소 distinct 틱
SNAP_TARGET_RATE = 90.0     # 당일분 3틱+ 목표 비율(%)
SNAP_MIN_N = 10             # 이보다 표본이 적으면 판정하지 않는다(rolling 이 스스로를 속이는 것 방지)

# 스키마 드리프트 🔴 필드 — CLAUDE.md 「탈락 필드 우선순위」 표(🔴 1·2·3) 기준.
#   ⚠ 이 목록을 줄이는 것이 곧 완료선 하향이다. 바꾸려면 CLAUDE.md 를 먼저 고칠 것.
SCHEMA_RED_FIELDS = ["winOdds", "pop", "weight", "surface", "trackCond"]

# 🔴 [2026-08-27] D2 계측 신뢰성 — 「실패가 없다」와 「로그가 없다」를 구분한다.
#   실사고: 06:12 에 당일 로그가 덮어써져 8/26 의 616건이 사라졌는데 D2a 는 0 = 초록이었다.
_ACTIVITY_MARKERS = ("[3종 수집]", "[각질편성]", "[경륜전개]", "[일본전개]",
                     "[분석로그]", "[복기저장]", "[수집관측]", "[결과기록]")
ACTIVITY_MIN_RACES = 5       # 발주 지난 경주가 이보다 적으면 판정 보류(이른 시각)
ACTIVITY_MIN_LINES = 20      # 로그에 활동 흔적이 이보다 적으면 로그가 잘린 것으로 본다

# 🔴 [2026-08-01 · 승인③] **당일 데이터에 의존하는 완료조건** — 여기에만 `⏳미확정` 을 붙인다.
#   왜: 2026-07-31 저녁 `4/23` → 08-01 06:21 `1/23`. **같은 코드인데 값이 떨어졌다.**
#      성능이 나빠진 것이 아니라 **아직 안 잰 것**인데 화면에는 후퇴로 보인다(I1·I4 와 같은 구조).
#   ⚠⚠ **분모 23 은 유지한다.** 미확정 항목을 분모에서 빼지 않는다 —
#      빼면 "23개 중 몇 개가 남았는지"라는 진행 정보 자체가 사라지고 완료선을 사후에 낮추는 것이 된다.
#   ⚠ 시각 무관 항목(`D3` 미구현 · `D7` 모듈로드 · `F2` 누적 · `A/B/C` 전체)은 **넣지 않는다.**
#      특히 `D3` 는 "미구현"이지 "미확정"이 아니다 — 시간이 지나도 저절로 측정되지 않는다.
#   각 항목의 당일 필터 근거(코드 위치)를 함께 적는다. 근거 없이 추가하지 말 것.
DAY_DEPENDENT = {
    "D1":  "check_snapshot_coverage — today=%Y_%m_%d 파일명 접두 필터",
    "D2a": "check_save_failures_fixed — _today_logs()",
    "D2b": "check_save_failures_unfixed — _today_logs()",
    "D4":  "check_schema_drift — day0(오늘 0시) 이후 starters_store 행만",
    "D5":  "check_score_decomposition — day=%Y_%m_%d analysis_log 만",
    "D6":  "check_freeze_success — freeze_log/<오늘>.json",
    "F1":  "check_forecast_discard — _today_logs()",
}


def _mark_pending_today(items):
    """🔴 당일 의존 + **아직 측정 못 함(ok is None)** → `⏳미확정` 으로 재분류.

    ⚠ `ok` 가 True/False 인 항목은 **건드리지 않는다** — 이미 잰 것이다.
      (예: `D2a`·`D2b` 는 '실패 0건'이 목표라 표본이 있으면 새벽에도 정상 판정된다.
       당일 의존이지만 미확정이 아니다 — 규칙 하나로 자동 구분된다.)
    ⚠ 항목을 **삭제하거나 분모에서 빼지 않는다.** 표식만 추가한다.
    반환: ⏳ 로 표시된 항목 수.
    """
    n = 0
    for it in items:
        # 🔴 [2026-08-03] **종결 항목이 우선한다.** F1 은 당일 의존 목록에도 있어
        #   그대로 두면 `⏳미확정(진행 중)` 으로 잡혀 **종결 사실이 화면에서 가려진다.**
        #   ⇒ note 에 종결 표시가 있으면 재분류하지 않는다.
        if "🏁 종결" in str(it.get("note") or ""):
            continue
        if it.get("id") not in DAY_DEPENDENT or it.get("ok") is not None:
            continue
        it["pendingToday"] = True                       # 소비자(카카오·화면)가 읽는 플래그
        it["dayBasis"] = DAY_DEPENDENT[it["id"]]        # 왜 당일 의존인지 근거를 같이 싣는다
        _r = it.get("reason") or ""
        if "⏳미확정" not in _r:
            it["reason"] = ("⏳ **미확정**(당일 데이터 · 진행 중) — " + _r) if _r else \
                           "⏳ **미확정**(당일 데이터 · 진행 중)"
        n += 1
    return n


def _mk(id, area, name, denominator, current=None, target=None, ok=None,
        n=None, note="", reason=""):
    """항목 1건. `denominator` 는 필수다 — 분모를 못 적으면 그 항목은 측정된 것이 아니다."""
    return {"id": id, "area": area, "name": name, "denominator": denominator,
            "current": current, "target": target, "ok": ok, "n": n,
            "measuredAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "note": note, "reason": reason}


def _distinct_ticks(doc):
    """같은 (초, src) 를 1틱으로 센다 — 2중 기록(2026-07-30 수정분) 이전 데이터 보정."""
    seen = set()
    for x in (doc.get("snapshots") or []):
        try:
            t = round(float(x.get("t") or 0), 0)
        except Exception:
            t = 0
        seen.add((t, str(x.get("src") or "")))
    return len(seen)


# ══════════════ ④ 데이터 보전 (4항목) ══════════════

def check_snapshot_coverage(today=None):
    """④-1 스냅샷 3틱+ 경주 비율.

    분모 = **odds_history 파일 전체**(0틱 경주 포함).
      근거: 0틱 경주를 빼면 '가장 실패한 경주'가 통계에서 사라진다(2026-07-30 실측 81.6% ↔ 69.4%).
    판정 = **당일분(rolling)**. 누적은 수정 전 과거가 영구히 섞여 있어 완료 상태에 도달할 수 없다.
    ⚠ 누적값도 `note` 에 **병기**한다 — 당일분만 보면 "과거 데이터가 오염돼 있다"는 사실이
      화면에서 사라지고, 시뮬레이션·리플레이는 과거 데이터로 돌아가므로 그게 중요하다.
    """
    today = today or time.strftime("%Y_%m_%d")
    files = [f for f in glob.glob(os.path.join(HIST_DIR, "*.json"))
             if ".corrupt." not in f and ".tmp" not in f]
    cum_n = cum_ok = day_n = day_ok = 0
    for f in files:
        try:
            doc = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue                      # 손상 파일은 별도 항목(④-2 계열)에서 다룬다
        if not isinstance(doc, dict):
            continue
        d = _distinct_ticks(doc)
        cum_n += 1
        cum_ok += (1 if d >= SNAP_MIN_TICKS else 0)
        if os.path.basename(f).startswith(today):
            day_n += 1
            day_ok += (1 if d >= SNAP_MIN_TICKS else 0)
    cum_rate = round(100.0 * cum_ok / cum_n, 1) if cum_n else None
    day_rate = round(100.0 * day_ok / day_n, 1) if day_n else None
    note = "누적 %s%% (%d/%d) 병기 — 수정 전 과거가 섞여 있다. 리플레이는 이 과거를 쓴다." % (
        cum_rate, cum_ok, cum_n)
    if day_n < SNAP_MIN_N:
        return _mk("D1", "④ 데이터 보전", "스냅샷 %d틱+ 경주 비율" % SNAP_MIN_TICKS,
                   "odds_history 파일 전체(0틱 포함) · 당일분 · distinct 틱",
                   current=day_rate, target=SNAP_TARGET_RATE, ok=None, n=day_n,
                   note=note, reason="표본 부족(n=%d < %d) — 하루 경주가 적으면 n=2로 100%%가 나와 "
                                     "rolling 이 스스로를 속인다" % (day_n, SNAP_MIN_N))
    return _mk("D1", "④ 데이터 보전", "스냅샷 %d틱+ 경주 비율" % SNAP_MIN_TICKS,
               "odds_history 파일 전체(0틱 포함) · 당일분 · distinct 틱",
               current=day_rate, target=SNAP_TARGET_RATE,
               ok=(day_rate is not None and day_rate >= SNAP_TARGET_RATE),
               n=day_n, note=note)


def _races_started_today():
    """오늘 스케줄에서 **발주 시각이 지난** 경주 수. D2b 의 판정 보류 기준."""
    try:
        d = json.load(open(os.path.join(BASE, "data", "today_schedule.json"), encoding="utf-8"))
    except Exception:
        return 0
    now = time.time()
    n = 0
    for t in (d.get("tracks") or []):
        for r in (t.get("races") or []):
            e = r.get("postEpoch")
            if e and float(e) <= now:
                n += 1
    return n



def _today_logs():
    """당일 stdout 로그 파일들(회전본 포함). 회전본은 `server_stdout.log.<YYYYMMDD_HHMMSS>`."""
    ymd = time.strftime("%Y%m%d")
    out = []
    cur = os.path.join(LOG_DIR, "server_stdout.log")
    if os.path.exists(cur):
        out.append(cur)
    for f in glob.glob(os.path.join(LOG_DIR, "server_stdout.log.*")):
        b = os.path.basename(f)
        if b.endswith(".err"):
            continue
        if ymd in b:
            out.append(f)
    return out


def _count_save_failures():
    """당일 stdout 로그에서 **저장 실패 전수**를 태그별로 센다.

    🔴 [2026-08-27] 계수 기준을 고쳤다 — 종전 방식은 세 군데가 틀렸다.
      ① **`WinError` 가 든 줄만** 셌다. 실측으로 WinError 없는 실패가 실재한다([다중경주] 6건).
      ② **태그를 '수정/미수정'으로 갈랐다.** 2026-08-27 에 `path + ".tmp"` **17곳을 전부 흡수**해
         `_json_atomic`/`_text_atomic` 으로 바꿨으므로 그 구분이 더는 성립하지 않는다
         (원칙 19 — 고치면 측정 대상에서 빠지는 정의는 잘못된 정의다).
      ③ 🔴 **로그가 잘리면 0 으로 초록이 된다.** 실제로 2026-08-27 06:12 에
         `Start-Process -RedirectStandardOutput` 이 당일 로그를 덮어써 8/26 원자료(616건)가 사라졌고,
         그 상태에서 D2a 는 0 = 초록으로 보였다. 「실패가 없다」와 「로그가 없다」가 구분되지 않는 것은
         이 프로젝트가 가장 경계하는 조용한 실패다.
      ⇒ 전수 계수(D2a) + **신뢰성 판정**(D2b)으로 나눈다.
    """
    tags = collections.Counter()
    activity = 0
    files = _today_logs()
    for _p in files:
        try:
            txt = open(_p, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for line in txt.split(chr(10)):
            for _m in _ACTIVITY_MARKERS:
                if _m in line:
                    activity += 1
                    break
            if "저장 실패" not in line and not ("실패" in line and "WinError" in line):
                continue
            t = line.strip()
            tag = "기타"
            if t.startswith("["):
                k = t.find("]")
                if 0 < k <= 15:
                    tag = t[1:k]
            tags[tag] += 1
    return tags, activity, len(files)


def check_save_failures_fixed():
    """④-2a 저장 실패 — **전수**(태그 무관 · WinError 유무 무관).

    🔴 [2026-08-27] 종전 「수정 경로만」에서 **전수**로 바꿨다.
      17곳을 전부 `_json_atomic`/`_text_atomic` 으로 흡수했으므로 '수정/미수정' 분리가 무의미해졌다.
      기준값: **2026-08-26 하루 616건**(분석로그 440 · 복기저장 131 · 다중경주 35).
      ⚠ 그 원자료 로그는 2026-08-27 06:12 에 내가 덮어써 사라졌다 — 숫자만 여기 남긴다.
    ⚠ 이 값이 0 이어도 **D2b(계측 신뢰성)가 통과해야** 의미가 있다.
    """
    tags, activity, nf = _count_save_failures()
    total = sum(tags.values())
    brk = " · ".join("%s %d" % (k, v) for k, v in tags.most_common(6)) or "없음"
    return _mk("D2a", "④ 데이터 보전", "저장 실패 전수",
               "당일 stdout 로그 전체(회전본 포함)의 저장 실패 줄 전수",
               current=total, target=0, ok=(total == 0), n=nf,
               note="내역: %s / 기준값 8-26 = 616건" % brk)


def check_save_failures_unfixed():
    """④-2b 저장 실패 **계측이 믿을 만한가** — 로그가 당일 활동을 실제로 담고 있나.

    🔴 [2026-08-27] 항목의 뜻을 바꿨다. 종전은 「미수정 17곳의 실패 건수」였는데
      그 17곳이 **전부 없어져** 분모가 0 이 됐다(원칙 19).
      대신 **D2a 를 믿어도 되는가**를 잰다. 실사고가 그 자리에 있다:
        2026-08-27 06:12 에 당일 로그가 덮어써져 8/26 의 616건이 사라졌는데
        그 상태에서 D2a 는 **0 = 초록**이었다.
      ⇒ 발주가 지난 경주가 있는데 로그에 수집·분석 흔적이 없으면 **판정 보류(ok=None)** 한다.
    ⚠ 「실패가 없다」와 「로그가 없다」를 구분하는 것이 이 항목의 전부다.
    """
    tags, activity, nf = _count_save_failures()
    done = _races_started_today()
    if done < ACTIVITY_MIN_RACES:
        return _mk("D2b", "④ 데이터 보전", "저장 실패 계측 신뢰성",
                   "로그가 당일 활동을 담고 있나",
                   current=None, target="담고 있음", ok=None, n=nf,
                   note="발주 지난 경주 %d건 < %d — 판정 보류(아직 이른 시각)" % (done, ACTIVITY_MIN_RACES))
    ok = activity >= ACTIVITY_MIN_LINES
    return _mk("D2b", "④ 데이터 보전", "저장 실패 계측 신뢰성",
               "로그가 당일 활동을 담고 있나",
               current=("활동 %d줄" % activity), target=("%d줄+" % ACTIVITY_MIN_LINES),
               ok=ok, n=nf,
               note=("발주 지난 경주 %d건 · 로그 활동 %d줄. " % (done, activity)) +
                    ("" if ok else "🔴 로그가 잘렸거나 비었다 — D2a 의 0 을 믿지 말 것."))


def check_schema_contract():
    """④-3 schema contract test 통과.

    분모 = 계약 파일(`tools/schema_contract.py`)에 정의된 저장행 함수 전체.
    현재 계약 파일 자체가 없다 → **ok=false**(미구현은 통과가 아니다).
    """
    p = os.path.join(BASE, "tools", "schema_contract.py")
    exists = os.path.exists(p)
    return _mk("D3", "④ 데이터 보전", "schema contract test 통과",
               "tools/schema_contract.py 의 계약 대상 저장행 함수 전체",
               current=("있음" if exists else "없음"), target="통과",
               ok=(True if exists else False), n=None,
               note="계약 파일 미존재 = 미구현. 미구현은 통과가 아니다(CLAUDE.md 설계안 참조).")


def _zero_odds_races():
    """배당(`winOdds`)이 **통째로 없는 경주** 수. D4 분모를 좁힌 대가를 숨기지 않기 위한 것.

    🔴 왜: 2026-08-28 에 「출주하지 않는 말」을 분모에서 뺐다(정당하다 — 배당이 없는 것이 정상이다).
      그러나 **분모를 좁히면 가장 실패한 것이 통계에서 사라진다**(이 체크리스트의 제1 규약).
      실측: 8/27 oddspark 35경주 중 **11경주가 배당 0** 이고 **전부 몬베츠**였다.
      나머지 24경주는 출주마 기준 **100%** 다 — 즉 파서는 정상이고 **경기장별 문제**다.
    ⇒ 비율 옆에 이 숫자를 함께 적어 그 사실이 매일 보이게 한다.
    반환 (배당0 경주 수, 전체 경주 수, 이름 표본)."""
    store = os.path.join(BASE, "starters_store.json")
    try:
        db = json.load(open(store, encoding="utf-8"))
    except Exception:
        return 0, 0, []
    zero = total = 0
    names = []
    for rk, rec in (db.items() if isinstance(db, dict) else []):
        rec = rec or {}
        if str(rec.get("source") or "") != "oddspark":
            continue
        st = [h for h in (rec.get("horses") or [])
              if isinstance(h, dict) and (h.get("jockey") or h.get("weight"))]
        if not st:
            continue
        total += 1
        if not any(h.get("winOdds") not in (None, "", []) for h in st):
            zero += 1
            if len(names) < 6:
                names.append(str(rk))
    return zero, total, names
def _red_field_rates():
    """🔴 필드 보유율을 **당일분 / 누적**으로 각각 산출. 반환 (day, cum, day_rows, cum_rows, note).

    🔴 분모 통제 (권대표 지시 2026-07-30 · 실측 근거 반영)
      `winOdds`·`pop`·`weight`·`surface`·`trackCond` 는 **경마 出走表 파서에서만** 나온다.
      → 분모를 '경마 경주'로 좁혀야 하는데 **`sport` 태그로 판별하면 안 된다**:
        · 오늘 경륜장 `sport=horse` 오분류 **213건**을 소급 정정했고 실시간 재발도 3경주 확인됐다
          (와카야마 7/24 · 코치 6R 7/25 · 코치 10R 7/26 · 원인은 분석 시점 sport 미확정).
        · **실측 확인**: `starters_store` 132경주 전부 `sport` 가 **None** 이다 —
          애초에 태그로는 분모를 잡을 수 없다.
      → **`source == "oddspark"`**(경마 出走表 파서를 탄 경주)를 분모로 쓴다. 파서 유래값이라
        태그 오염과 무관하다. 실측 분포: keirin 87 · oddspark 23 · korea 14 · keiba_nar 1 · 없음 7.
    ⚠ `t`(레코드 타임스탬프)로 당일분을 가른다 — 키에 날짜가 없기 때문(0/132).
    """
    store = os.path.join(BASE, "starters_store.json")
    if not os.path.exists(store):
        return None, None, 0, 0, "starters_store.json 미존재"
    try:
        db = json.load(open(store, encoding="utf-8"))
    except Exception as e:
        return None, None, 0, 0, "starters_store.json 파싱 실패: %s" % str(e)[:80]
    day0 = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
    day_have, cum_have = collections.Counter(), collections.Counter()
    day_rows = cum_rows = 0
    for rk, rec in (db.items() if isinstance(db, dict) else []):
        rec = rec or {}
        if str(rec.get("source") or "") != "oddspark":
            continue                       # 경마 出走表 파서를 탄 경주만
        is_today = (rec.get("t") or 0) >= day0
        for h in (rec.get("horses") or []):
            # 🔴 [2026-08-28] **출주하지 않는 말은 분모에서 뺀다.**
            #   실측(8/27 oddspark 350행): 그런 행이 **35개(10%)** 였고, 전부 배당판에도 없다.
            #   배당·기수가 없는 것이 **정상**인 행을 분모에 넣으면 보유율이 구조적으로 안 100% 가 된다.
            #   판정: 기수도 부담중량도 없으면 출주하지 않는다.
            #   ⚠ 분모를 좁히는 것은 위험하다(가장 실패한 것이 통계에서 사라진다) — 그래서
            #     🔴 **배당이 통째로 없는 경주 수를 note 에 함께 적어** 진짜 결함이 숨지 않게 한다.
            if not (h.get("jockey") or h.get("weight")):
                continue
            cum_rows += 1
            if is_today:
                day_rows += 1
            for f in SCHEMA_RED_FIELDS:
                # 🔴 [2026-08-26] `surface`·`trackCond` 는 **말이 아니라 경주 속성**이라
                #   레코드 **최상위**에 저장된다. 종전에는 `horses` 행에서만 찾아
                #   실제로 17/17(100%) 채워져 있는데도 **영원히 0%** 가 나왔다.
                #   ⚠ CLAUDE.md 의 "1계층 재수집이 선행 조건이라 0%가 정확한 판정" 도
                #     그때 잘못 진단한 것이다 — 원칙 8-E(없다고 하기 전에 원자료를 연다).
                #   실물: 소노다 1경주 distance=820 · surface=더트 · trackCond=重
                _v = h.get(f)
                if _v in (None, "", []):
                    _v = rec.get(f)        # 경주 최상위 폴백
                if _v not in (None, "", []):
                    cum_have[f] += 1
                    if is_today:
                        day_have[f] += 1
    return day_have, cum_have, day_rows, cum_rows, ""


def check_schema_drift():
    """④-4 스키마 드리프트 🔴 필드 보유율 ≥90%.

    ⚠ **판정 방식 변경(2026-07-30)**: 종전 "보유 행 0개 = 미해소"는 `winOdds` **1.7%** 를
      '해소'로 판정해 **완료선이 너무 관대**했다. → D1 과 동일하게
      **당일 rolling · 보유율 ≥90% · 누적 병기** 로 바꾼다.
    ⚠ '코드에 배선됐는가'가 아니라 '데이터에 남았는가'로 본다(원칙 5) —
      `surface`/`trackCond` 는 코드는 배선됐는데 실데이터 0% 다. rolling 으로도 당분간 0% 일 것이고
      **그게 정확한 판정**이다(1계층 재수집이 선행 조건). 낮게 나온다고 기준을 낮추지 않는다.
    """
    day, cum, day_rows, cum_rows, err = _red_field_rates()
    denom = ("당일 `starters_store` 중 `source=\"oddspark\"`(경마 出走表 파서) 경주의 전체 행 "
             "— ⚠ `sport` 태그 미사용(오분류 213건 정정 이력·실측상 전부 None)")
    if err:
        return _mk("D4", "④ 데이터 보전", "스키마 드리프트 🔴 필드 보유율",
                   denom, current=None, target=90.0, ok=None, n=None, reason=err)
    cum_txt = ", ".join("%s %.1f%%" % (f, 100.0 * cum[f] / cum_rows if cum_rows else 0)
                        for f in SCHEMA_RED_FIELDS)
    note = "누적(%d행): %s · surface/trackCond 는 경주 최상위에 저장된다(2026-08-26 정정)" % (
        cum_rows, cum_txt)
    _zr, _tr, _zn = _zero_odds_races()
    if _zr:
        note += (" · 🔴 배당 통째로 없는 경주 %d/%d (%s)"
                 % (_zr, _tr, ", ".join(_zn[:3])))
    if day_rows < SNAP_MIN_N:
        return _mk("D4", "④ 데이터 보전", "스키마 드리프트 🔴 필드 보유율",
                   denom, current=None, target=90.0, ok=None, n=day_rows,
                   note=note, reason="표본 부족(당일 %d행 < %d) — D1 과 동일 규칙"
                                     % (day_rows, SNAP_MIN_N))
    rates = {f: round(100.0 * day[f] / day_rows, 1) for f in SCHEMA_RED_FIELDS}
    worst = min(rates.values())
    day_txt = ", ".join("%s %.1f%%" % (f, rates[f]) for f in SCHEMA_RED_FIELDS)
    return _mk("D4", "④ 데이터 보전", "스키마 드리프트 🔴 필드 보유율",
               denom, current=worst, target=90.0, ok=(worst >= 90.0), n=day_rows,
               note="당일(%d행): %s / %s" % (day_rows, day_txt, note))


def check_score_decomposition():
    """④-5 점수 분해 `rank`·`baseScore` 배선 — **신설**(권대표 지시 2026-07-30).

    배경: 「탈락 필드 우선순위」에서 **보너스 분해 8종은 체크리스트 A1·A2 로 이관**했으나
      (`gait`·`paceBonus`·`paceBonusBase`·`gradeAtBonus`·`paceDetail` 이 오늘 배선됨),
      **`rank`(통합등급 순위)와 `baseScore` 는 여전히 미배선**임이 실측으로 확인됐다
      (오늘 분석로그 45파일·372행 중 **둘 다 0.0%**).
    ⚠ `paceBonusBase`(98.4%)가 `baseScore` 역할을 하는 것처럼 보이지만 **이름이 다른 별개 필드**이므로
      대체한다고 단정하지 않는다 → 별도 추적 항목으로 분리한다.
    분모 = 당일 `analysis_log` 의 `horses` 전체 행(스키마 도입 시점으로 좁히지 않는다).
    """
    day = time.strftime("%Y_%m_%d")
    files = glob.glob(os.path.join(BASE, "data", "analysis_log", day + "_*.json"))
    fields = ["rank", "baseScore"]
    have = collections.Counter()
    rows = 0
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for h in (d.get("horses") or []):
            rows += 1
            for fl in fields:
                if h.get(fl) not in (None, "", []):
                    have[fl] += 1
    denom = "당일 analysis_log 의 horses 전체 행(스키마 도입 시점으로 좁히지 않는다)"
    if rows < SNAP_MIN_N:
        return _mk("D5", "④ 데이터 보전", "점수 분해 rank·baseScore 보유율",
                   denom, current=None, target=90.0, ok=None, n=rows,
                   reason="표본 부족(당일 %d행 < %d)" % (rows, SNAP_MIN_N))
    rates = {f: round(100.0 * have[f] / rows, 1) for f in fields}
    worst = min(rates.values())
    return _mk("D5", "④ 데이터 보전", "점수 분해 rank·baseScore 보유율",
               denom, current=worst, target=90.0, ok=(worst >= 90.0), n=rows,
               note="당일(%d행): %s · 배선된 분해 필드(paceBonusBase 98.4%%·gradeAtBonus 74.5%%)는 A1·A2 에서 추적"
                    % (rows, ", ".join("%s %.1f%%" % (f, rates[f]) for f in fields)))


# ══════════════ ①②③ 구현분 (2026-09-01) ══════════════
#  🔴 **한 번 스캔해 여러 항목이 나눠 쓴다** — A1·A2·A4 가 각각 전수 스캔하면 20초가 더 든다.
#    실측: analysis_log 5,786파일 전수 6.6초 · 체크리스트 전체 13.8초.
#  🔴 **당일로 판정하고 누적을 병기한다**(D1·D4·D5 와 같은 규칙).
#    ⚠ 「도입 이후로 좁히지 않는다」는 _PENDING 의 분모 설계를 지킨 것이다 —
#      당일은 **시간 창**이지 「스키마 도입 이후」가 아니다. 배선 실패가 당일에 그대로 드러난다.
#      그리고 누적을 함께 내어 **과거가 오염돼 있다는 사실이 화면에서 사라지지 않게** 한다.
_ALOG_CACHE = {}


def _scan_alog():
    """analysis_log 를 **한 번만** 훑어 horses 필드 보유를 센다(당일·누적).

    반환 {"day": {"rows": n, "have": Counter}, "all": {...}}
    ⚠ 결과를 캐시한다 — 같은 실행 안에서 A1·A2·A4 가 공유한다.
    """
    if _ALOG_CACHE:
        return _ALOG_CACHE
    day = time.strftime("%Y_%m_%d")
    out = {"day": {"rows": 0, "have": collections.Counter()},
           "all": {"rows": 0, "have": collections.Counter()},
           "dayRaces": 0, "allRaces": 0}
    for f in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", "*.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        is_day = os.path.basename(f).startswith(day + "_")
        hs = d.get("horses") or []
        # drops_raw 는 **경주 단위** 필드다 — 행이 아니라 경주로 센다(원칙 8-C: 분모를 섞지 않는다)
        _has_dr = d.get("drops_raw") not in (None, "", [], {})
        for scope in (("all",) + (("day",) if is_day else ())):
            out["dayRaces" if scope == "day" else "allRaces"] += 1
            if _has_dr:
                out[scope]["have"]["drops_raw_race"] += 1
        for h in hs:
            for scope in (("all",) + (("day",) if is_day else ())):
                out[scope]["rows"] += 1
                for fl in ("gait", "paceBonus", "paceBonusBase", "gradeAtBonus"):
                    if h.get(fl) not in (None, "", []):
                        out[scope]["have"][fl] += 1
    _ALOG_CACHE.update(out)
    return out


def _field_check(cid, name, fields, target, note_extra=""):
    """analysis_log horses 필드 보유율 공용 판정 — 당일 판정 · 누적 병기."""
    s = _scan_alog()
    denom = "analysis_log 의 horses 전체 행(스키마 도입 시점으로 좁히지 않는다) · **당일 판정 · 누적 병기**"
    dr, ar = s["day"]["rows"], s["all"]["rows"]
    if dr < SNAP_MIN_N:
        return _mk(cid, "① 적중왕전개", name, denom, current=None, target=target,
                   ok=None, n=dr, reason="표본 부족(당일 %d행 < %d)" % (dr, SNAP_MIN_N))
    d_rate = {f: round(100.0 * s["day"]["have"][f] / dr, 1) for f in fields}
    a_rate = {f: round(100.0 * s["all"]["have"][f] / ar, 1) for f in fields} if ar else {}
    worst = min(d_rate.values())
    return _mk(cid, "① 적중왕전개", name, denom, current=worst, target=target,
               ok=(worst >= target), n=dr,
               note="당일(%d행): %s / 누적(%d행): %s%s"
                    % (dr, ", ".join("%s %.1f%%" % (f, d_rate[f]) for f in fields),
                       ar, ", ".join("%s %.1f%%" % (f, a_rate.get(f, 0)) for f in fields),
                       note_extra))


def check_a1_pace_fields():
    """A1 gait·paceBonus·paceBonusBase 보유율."""
    return _field_check("A1", "gait·paceBonus·paceBonusBase 보유율",
                        ["gait", "paceBonus", "paceBonusBase"], 90.0)


def check_a2_grade_at_bonus():
    """A2 gradeAtBonus 보유율 — 2026-08-31 배선(그 이전 경주엔 없다)."""
    return _field_check("A2", "gradeAtBonus 보유율", ["gradeAtBonus"], 90.0,
                        note_extra=" · 2026-08-31 배선이라 누적은 낮은 것이 정상")


def check_a3_declared_style():
    """A3 declaredStyle 누적 보유율 — 분모는 **경륜** starters_store 행만."""
    denom = "경륜(source=keirin) starters_store 전체 행 — 경마 행은 대상이 아니라 분모에서 제외"
    try:
        db = json.load(open(os.path.join(BASE, "starters_store.json"), encoding="utf-8"))
    except Exception as e:
        return _mk("A3", "① 적중왕전개", "declaredStyle 누적 보유율", denom,
                   current=None, target=70.0, ok=None, reason="읽기 실패: %s" % str(e)[:60])
    rows = have = 0
    for v in (db or {}).values():
        if not isinstance(v, dict) or v.get("source") != "keirin":
            continue
        for h in (v.get("horses") or []):
            rows += 1
            if h.get("declaredStyle") not in (None, "", []):
                have += 1
    if rows < SNAP_MIN_N:
        return _mk("A3", "① 적중왕전개", "declaredStyle 누적 보유율", denom,
                   current=None, target=70.0, ok=None, n=rows,
                   reason="표본 부족(%d행 < %d)" % (rows, SNAP_MIN_N))
    r = round(100.0 * have / rows, 1)
    return _mk("A3", "① 적중왕전개", "declaredStyle 누적 보유율", denom,
               current=r, target=70.0, ok=(r >= 70.0), n=rows,
               note="경륜 %d행 중 %d · ⚠ starters_store 는 라이브 캐시라 진행 중 경주 위주다" % (rows, have))


def check_a4_drops_raw():
    """A4 drops_raw 보유율 — 🔴 **경주 단위**다(horses 행이 아니다)."""
    s = _scan_alog()
    denom = "analysis_log **경주 전체**(drops_raw 는 경주 단위 필드 — 행과 섞지 않는다) · 당일 판정 · 누적 병기"
    dn, an = s["dayRaces"], s["allRaces"]
    if dn < SNAP_MIN_N:
        return _mk("A4", "① 적중왕전개", "drops_raw 보유율", denom, current=None,
                   target=90.0, ok=None, n=dn,
                   reason="표본 부족(당일 %d경주 < %d)" % (dn, SNAP_MIN_N))
    d = round(100.0 * s["day"]["have"]["drops_raw_race"] / dn, 1)
    a = round(100.0 * s["all"]["have"]["drops_raw_race"] / an, 1) if an else 0
    return _mk("A4", "① 적중왕전개", "drops_raw 보유율", denom, current=d, target=90.0,
               ok=(d >= 90.0), n=dn, note="당일 %.1f%%(%d경주) / 누적 %.1f%%(%d경주)" % (d, dn, a, an))


def check_b3_ghost_no():
    """B3 유령 마번 — 추천 조합에 **배당에 없는 마번**이 섞였나.

    🔴 분모는 「배당 스냅샷 보유 경주 전체」다(출주표 유무 무관) —
      출주표 보유분만 세면 출주표를 못 받은 경주의 유령이 사라진다.
    """
    day = time.strftime("%Y_%m_%d")
    denom = "당일 **배당 스냅샷 보유 경주 전체**(출주표 유무 무관) · 추천 보유분만 대조"
    tot = bad = 0
    ex = []
    for f in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", day + "_*.json"))):
        name = os.path.basename(f)[:-5]
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        dc = ((d.get("corePicks") or {}).get("displayedCombos") or {})
        rec = list(dc.get("quinellas") or []) + list(dc.get("trifectas") or [])
        if not rec:
            continue
        oh = _hist_read(os.path.join(BASE, "data", "odds_history", name + ".json"))
        # 🔴 [원칙 27] **마감 전 정상 틱**만 쓴다 — 판정 명단은 T-5 에 동결되기 때문이다.
        #   실사고(2026-09-01 몬베츠 5경주): 마지막 스냅샷이 **마감 후 private 11두**(55조합)라
        #   마감 전엔 정상 출전이던 5번이 「유령」으로 잡혔다. 취소마이지 유령이 아니다.
        #   ⇒ after_close·오염 틱을 빼지 않으면 **매일 거짓 경보**가 난다.
        _BADT = ("odds_suspect", "baseline_reset", "next_race_blocked")
        sn = [s for s in ((oh or {}).get("snapshots") or [])
              if s.get("quinella") and not s.get("after_close")
              and not any(s.get(b) for b in _BADT)
              and isinstance(s.get("minutes_before"), (int, float))]
        if not sn:
            continue
        nos = set()
        q = sn[-1].get("quinella")
        if isinstance(q, list):
            for it in q:
                for z in ((it or {}).get("combo") or []):
                    try:
                        nos.add(int(z))
                    except (TypeError, ValueError):
                        pass
        else:
            for k in (q or {}):
                for z in str(k).replace("-", "+").split("+"):
                    try:
                        nos.add(int(z))
                    except (TypeError, ValueError):
                        pass
        if not nos:
            continue
        tot += 1
        g = set()
        for c in rec:
            for z in (c or []):
                try:
                    if int(z) not in nos:
                        g.add(int(z))
                except (TypeError, ValueError):
                    pass
        if g:
            bad += 1
            if len(ex) < 3:
                ex.append("%s %s" % (name, sorted(g)))
    if tot < SNAP_MIN_N:
        return _mk("B3", "② 배당판 오류", "유령 마번 비율", denom, current=None,
                   target=0.0, ok=None, n=tot,
                   reason="표본 부족(당일 %d경주 < %d)" % (tot, SNAP_MIN_N))
    r = round(100.0 * bad / tot, 1)
    return _mk("B3", "② 배당판 오류", "유령 마번 비율", denom, current=r, target=0.0,
               ok=(bad == 0), n=tot,
               note="당일 %d경주 중 %d건%s" % (tot, bad, (" · 예: " + " / ".join(ex)) if ex else ""))


def _hist_read(p):
    """odds_history 읽기 — .json 우선, 없으면 .gz."""
    import gzip
    for path, gz in ((p, False), (p + ".gz", True)):
        try:
            if gz:
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    return json.load(f)
            return json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
    return None


def check_c2_payout_coverage():
    """C2 결과 확정배당 보유율 — 분모는 **결과 입력 완료 경주 전체**."""
    day = time.strftime("%Y_%m_%d")
    denom = "당일 **결과 입력 완료 경주 전체**(결과가 없으면 확정배당이 존재할 수 없어 분모에서 제외)"
    tot = have = 0
    for f in sorted(glob.glob(os.path.join(BASE, "data", "race_results", day + "_*.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        r = d.get("result") or {}
        if not (r.get("1st") and r.get("2nd")):
            continue
        tot += 1
        if (d.get("payouts") or {}).get("quinella") is not None:
            have += 1
    if tot < SNAP_MIN_N:
        return _mk("C2", "③ 예상·복기", "결과 확정배당 보유율", denom, current=None,
                   target=100.0, ok=None, n=tot,
                   reason="표본 부족(당일 %d경주 < %d)" % (tot, SNAP_MIN_N))
    r = round(100.0 * have / tot, 1)
    return _mk("C2", "③ 예상·복기", "결과 확정배당 보유율", denom, current=r, target=100.0,
               ok=(have == tot), n=tot, note="당일 %d/%d · 미보유는 백필 대기" % (have, tot))


def check_c4_kakao_log():
    """C4 카카오 발송 이력 저장 — 🔴 분모는 **발송 시도 전체**(성공·실패 모두)."""
    day = time.strftime("%Y%m%d")
    denom = "당일 발송 시도 전체(성공·실패 모두) — 성공분만 세면 발송 실패가 보이지 않는다"
    p = os.path.join(BASE, "data", "kakao_sent", day + ".json")
    if not os.path.exists(p):
        return _mk("C4", "③ 예상·복기", "카카오 발송 이력 저장", denom, current=0,
                   target=1, ok=False, n=0,
                   note="당일 이력 파일 없음 — 발송이 없었는지 **기록이 안 된 것인지 구분되지 않는다**")
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception as e:
        return _mk("C4", "③ 예상·복기", "카카오 발송 이력 저장", denom, current=None,
                   target=1, ok=None, reason="읽기 실패: %s" % str(e)[:60])
    rows = d if isinstance(d, list) else (d.get("rows") or d.get("sent") or list((d or {}).values()))
    n = len(rows or [])
    fail = sum(1 for r in (rows or []) if isinstance(r, dict) and r.get("ok") is False)
    return _mk("C4", "③ 예상·복기", "카카오 발송 이력 저장", denom, current=(1 if n else 0),
               target=1, ok=(n > 0), n=n,
               note="당일 %d건 기록(실패 %d건 포함) · ⚠ 실패도 남아야 정상이다" % (n, fail))


# ══════════════ ①②③ 미구현 자리 ══════════════
#  ⚠ 미구현 항목도 목록에 남긴다 — **17개 중 몇 개가 미구현인지도 진행 상황**이기 때문이다.
#  ⚠ 각 항목의 `denominator` 는 **지금 설계해 둔다.** ④에서 드러났듯 분모를 잘못 잡으면
#    실패가 통계에서 사라지고, 그때는 이미 완료선이 굳어 있어 되돌리기 어렵다.
_PENDING = [
    # ① 적중왕전개 준비 — 🟢 A1·A2·A3·A4 는 2026-09-01 구현됨(위 함수 참조)
    ("A5", "① 적중왕전개", "paceBonus 3안 비교 표본", 30,
     "결과 보유 경주(착순 확정)", "성적 비교라 결과가 없으면 판정 자체가 불가 — 분모에서 제외가 타당"),
    # ② 배당판 오류 (4)
    ("B1", "② 배당판 오류", "리스트 간 배당 불일치율", 0.0,
     "동일 경주에 2개 이상 리스트가 존재하는 스냅샷", "리스트가 1개면 비교 대상이 없어 판정 불가"),
    ("B2", "② 배당판 오류", "quinella ↔ finalQuinellas 불일치 처리", 0.0,
     "finalQuinellas 보유 경주 전체", "추천이 없는 경주는 대조 대상이 아님"),
    # 🟢 B3(유령 마번)은 2026-09-01 구현됨(위 check_b3_ghost_no)
    ("B4", "② 배당판 오류", "화면 불일치 감지 항목 작동", 1,
     "감지 항목 3종", "발동률이 확인된 항목만 대상(CLAUDE.md 「화면 불일치 자동 감지」)"),
    # ③ 예상·복기 (4)
    ("C1", "③ 예상·복기", "발주완료 경주 예상 저장률", 100.0,
     "**당일 스케줄의 발주완료 경주 전체**", "수집 성공분만 세면 수집 실패가 통계에서 사라진다"),
    # 🟢 C2(확정배당 보유율)·C4(카카오 발송 이력)는 2026-09-01 구현됨
    ("C3", "③ 예상·복기", "det_review 커버율 · 실패 0건", 100.0,
     "**발주완료 경주 전체**(분석 로그 보유 경주만이 아니다)",
     "'분석 로그 보유분만'으로 좁히면 분석 자체가 안 돈 경주가 사라진다 — ④와 같은 함정"),
]


# ── 🏁 [2026-08-03] Gemini 독립 예측 **종결** ────────────────────────────────
#   F2·F3 **동시 열위**로 종결했다(판정선 변별 100·동률 제외를 낮추지 않고 도달 후 판정).
#     F2 Gemini 1.73 ↔ 시장 1.94 · 변별 102(우위 31 · 동률 134 · 열위 71)
#     F3 단독 적중 57 ↔ 98 · 가상 회수율 70.4 ↔ 70.7(동률) · 3제외 53.8 ↔ 62.3
#   🔴 **분모(24)에서 빼지 않는다** — 빼면 "몇 개를 접었는지"가 화면에서 사라진다.
#     미구현을 목록에 남기는 원칙과 같은 이유다. `ok=None` + 사유로 **미충족·미측정과 구분**한다.
#   🔧 되살리려면 `gemini_forecast.FORECAST_TERMINATED = False` 로 바꾼다(그때 이 상수도 함께 내린다).
FORECAST_TERMINATED = True
_TERM_NOTE = "🏁 종결(2026-08-03 · F2·F3 동시 열위 — 판정선 미달)"


def _terminated(idn, title, denom, basis):
    """종결 항목을 **분모에 남긴 채** ok=None 으로 돌려준다(미측정과 사유로 구분)."""
    return _mk(idn, "⑤ 예측 검증", title, denom,
               current=None, target=None, ok=None, n=None, note=_TERM_NOTE, reason=basis)


def check_forecast_discard():
    """⑤-1 Gemini 예측 **폐기율** ≤20% (판정선: 30경주 도달 시 형식 점검).

    분모 = `logs/forecast/` 의 **당일 예측 시도 전체**(성공 + 폐기).
      ⚠ 성공분만 세면 폐기가 통계에서 사라진다 — D1 의 0틱 제외와 같은 함정.
    ⚠ 폐기는 형식 검증 실패(키 누락·명단 밖 번호·confidence 범위 이탈)로 **통째 폐기**된 건이다.
      부분 채택은 하지 않는다.
    """
    if FORECAST_TERMINATED:
        return _terminated("F1", "Gemini 예측 폐기율",
                           "종결 전 분모 유지(재개 시 그대로 쓴다)", "판정선을 낮추지 않고 도달 후 판정했다")

    # 🔴 [2026-07-31] 종전에는 `logs/forecast` 의 **전체 파일**을 셌다 — 어제·그제 것이 섞여
    #   당일 폐기율의 분모가 부풀었다(원칙 8-B: 같은 목록에서 분모를 통일할 것).
    #   ⇒ 당일 접두사(`YYYYMMDD_`)로 제한한다.
    d = os.path.join(BASE, "logs", "forecast")
    _pfx = time.strftime("%Y%m%d") + "_"
    saved = (len([f for f in os.listdir(d) if f.endswith(".json") and f.startswith(_pfx)])
             if os.path.isdir(d) else 0)
    disc = 0
    for p in _today_logs():
        try:
            disc += open(p, encoding="utf-8", errors="replace").read().count("[예측 폐기]")
        except Exception:
            continue
    tried = saved + disc
    denom = "logs/forecast 당일 예측 시도 전체(성공+폐기)"
    if tried < 30:
        return _mk("F1", "⑤ 예측 검증", "Gemini 예측 폐기율", denom,
                   current=(round(100.0 * disc / tried, 1) if tried else None), target=20.0,
                   ok=None, n=tried,
                   reason="판정선 미도달(%d/30경주) — 30경주에서 형식 점검한다" % tried)
    rate = round(100.0 * disc / tried, 1)
    return _mk("F1", "⑤ 예측 검증", "Gemini 예측 폐기율", denom,
               current=rate, target=20.0, ok=(rate <= 20.0), n=tried,
               note="폐기 %d / 시도 %d · 20%% 초과 시 프롬프트 수정" % (disc, tried))


def check_forecast_vs_market():
    """⑤-2 Gemini 평균 `hit_count` ≥ 시장 평균 `market_hit_count` (판정선: **100경주**).

    ⚠ **사후에 판정선을 낮추지 않는다.** 100경주 도달 전에는 `ok=null` 이다.
    ⚠ 시장 대조군은 **마감 전(T-8~T-0)** 스냅샷의 단승 최저 3두다.
      마감 후 배당을 쓰면 시장이 유리해져 비교가 성립하지 않는다.
    """
    if FORECAST_TERMINATED:
        return _terminated("F2", "Gemini 적중 ≥ 시장 적중",
                           "종결 전 분모 유지(재개 시 그대로 쓴다)", "판정선을 낮추지 않고 도달 후 판정했다")

    d = os.path.join(BASE, "logs", "forecast")
    g, m, n = 0, 0, 0
    win = tie = lose = 0
    if os.path.isdir(d):
        for f in os.listdir(d):
            if not f.endswith(".json"):
                continue
            try:
                doc = json.load(open(os.path.join(d, f), encoding="utf-8"))
            except Exception:
                continue
            gr = doc.get("grading") or {}
            if gr.get("hit_count") is None or gr.get("market_hit_count") is None:
                continue
            n += 1
            g += gr["hit_count"]
            m += gr["market_hit_count"]
            if gr["hit_count"] > gr["market_hit_count"]:
                win += 1
            elif gr["hit_count"] == gr["market_hit_count"]:
                tie += 1
            else:
                lose += 1
    # 🔴 [2026-08-01 정정] 판정선은 **변별 표본(동률 제외) 100경주**다.
    #   종전 코드는 **전체 채점 n**(212)으로 세어 판정선 미도달인데도 판정을 내고 있었다.
    #   ⚠ 이건 판정선 하향이 아니라 **판정선대로 고치는 것**이다 — 오히려 더 엄격해진다.
    #   동률은 변별력이 없다(둘 다 같은 답). 그것을 분모에 넣으면 표본이 부풀려진다.
    decisive = win + lose
    denom = "채점 완료 중 **변별 표본**(동률 제외) — 동률은 변별력이 없어 분모에서 뺀다"
    cur = (round(g / n, 2) if n else None)
    note = (("Gemini 평균 %.2f ↔ 시장 평균 %.2f (채점 n=%d) · "
             "우위 %d · 동률 %d · 열위 %d · **변별 %d**") % (g / n, m / n, n, win, tie, lose, decisive)) if n else ""
    if decisive < 100:
        return _mk("F2", "⑤ 예측 검증", "Gemini 적중 ≥ 시장 적중", denom,
                   current=cur, target="시장 이상", ok=None, n=decisive, note=note,
                   reason="판정선 미도달(변별 %d/100경주) — 사후에 낮추지 않는다" % decisive)
    return _mk("F2", "⑤ 예측 검증", "Gemini 적중 ≥ 시장 적중", denom,
               current=cur, target=round(m / n, 2), ok=(g >= m), n=decisive,
               note=note + " · 🔴 **F3 와 함께 판정한다**(F2 단독 종결 금지) — 낮으면 종결 / 비슷하면 이변 경주만 재판정 / 높으면 편입 검토")


def check_forecast_highodds():
    """[F3 · 2026-08-01 신설] Gemini 의 **고배당 능력** — 시장과 다른 답을 내고 맞혔는가.

    🔴 왜 F2 로는 부족한가: **F2 는 배당을 보지 않는다.** Gemini 가 82배를 맞추고 시장이 1.2배를
      맞춰도 F2 에서는 **동점**이다. 대표 원칙(고배당·중배당이 기본)을 F2 는 구조적으로 못 잰다.
      ⇒ F2 만으로 판정하면 고배당 능력을 **재보지도 못하고** 닫힌다.
    🔴 판정 규약: **F2 와 F3 를 나란히 본다. F2 열세여도 F3 우위면 종결하지 않는다.**
      ⚠ 단 **F3 를 이유로 F2 판정선을 낮추지 않는다.** 두 지표가 엇갈리면 **대표 판단**을 받는다.
    ⚠ 지표 정의: `Gemini 단독 적중` = Gemini top3 중 **시장 top3 에 없는 말**이 실제 3착 안에 든 경주.
      시장 단독 적중(= 시장만 찍은 말이 3착 안)과 **건수·배당중앙**을 대조한다.
    ⚠ 상세(가상 회수율·구간별 분포)는 `measure_recovery.py --forecast` 가 낸다. 여기서는 요약만이다.
    """
    if FORECAST_TERMINATED:
        return _terminated("F3", "Gemini 고배당 능력(시장과 다른 답 && 적중)",
                           "종결 전 분모 유지(재개 시 그대로 쓴다)", "판정선을 낮추지 않고 도달 후 판정했다")

    d = os.path.join(BASE, "logs", "forecast")
    g_hit, m_hit = [], []
    diff_n = 0
    if os.path.isdir(d):
        for f in os.listdir(d):
            if not f.endswith(".json"):
                continue
            try:
                doc = json.load(open(os.path.join(d, f), encoding="utf-8"))
            except Exception:
                continue
            gr = doc.get("grading") or {}
            gtop = [x for x in (doc.get("predicted_top3") or []) if x is not None]
            mtop = [x for x in (gr.get("market_top3") or []) if x is not None]
            act = [x for x in (gr.get("actual") or []) if x is not None]
            po = gr.get("payout_quinella")
            if len(gtop) < 2 or len(mtop) < 2 or len(act) < 3:
                continue
            uniq = [x for x in gtop if x not in mtop]
            if uniq:
                diff_n += 1
                if [x for x in uniq if x in act]:
                    g_hit.append(po)
            m_uniq = [x for x in mtop if x not in gtop]
            if m_uniq and [x for x in m_uniq if x in act]:
                m_hit.append(po)
    denom = "시장과 다른 답을 낸 예측 경주(양쪽 top3 가 갈린 건) — 동일 답은 변별력이 없어 제외"

    def _med(v):
        vv = sorted(x for x in v if x)
        return round(vv[len(vv) // 2], 1) if vv else None
    gm, mm = _med(g_hit), _med(m_hit)
    note = ("Gemini 단독 적중 %d건(배당중앙 %s) ↔ 시장 단독 적중 %d건(배당중앙 %s) · 갈린 경주 %d"
            % (len(g_hit), gm if gm else "—", len(m_hit), mm if mm else "—", diff_n))
    if diff_n < 100:
        return _mk("F3", "⑤ 예측 검증", "Gemini 고배당 능력(시장과 다른 답 && 적중)", denom,
                   current=len(g_hit), target="시장 단독 이상", ok=None, n=diff_n, note=note,
                   reason="판정선 미도달(갈린 경주 %d/100) — 사후에 낮추지 않는다" % diff_n)
    # 🔴 판정: 건수와 배당중앙을 **둘 다** 본다. 건수가 적어도 배당중앙이 뚜렷이 높으면 우위 후보다.
    ok = (len(g_hit) >= len(m_hit)) or (gm is not None and mm is not None and gm >= mm * 1.5)
    return _mk("F3", "⑤ 예측 검증", "Gemini 고배당 능력(시장과 다른 답 && 적중)", denom,
               current=len(g_hit), target=len(m_hit), ok=ok, n=diff_n,
               note=note + " · 🔴 F2 와 **함께** 판정한다 · 상세는 `measure_recovery.py --forecast`")


def check_freeze_success():
    """[D6 · 2026-07-31 신설] 마감 시점 동결이 **신규 경주에서 실제로 작동하는가.**

    🔴 핵심 지표는 `closed_row` 비율이다. 수정 전 **44.4%**(235/529)였고,
      '🔒 마감 확정' 행이 만들어진 그 저장에서 폐기되던 것이 원인이었다(병합으로 수정).
    ⚠ 분모는 **당일 동결 시도 건수**다 — 과거 소급 복구분은 섞지 않는다.
    """
    d = os.path.join(BASE, "data", "freeze_log", time.strftime("%Y-%m-%d") + ".json")
    rows = []
    if os.path.exists(d):
        try:
            rows = json.load(open(d, encoding="utf-8")) or []
        except Exception:
            rows = []
    n = len(rows)
    denom = "당일 동결 시도 건수(freeze_log 적재분) — 소급 복구분 제외"
    ok_rows = [r for r in rows if isinstance(r, dict) and r.get("ok")]
    cr = sum(1 for r in ok_rows if r.get("src") == "closed_row")
    pr = sum(1 for r in ok_rows if r.get("src") == "pre_close_row")
    fail = n - len(ok_rows)
    cur = (round(100.0 * cr / n, 1) if n else None)
    note = ("closed_row %d · pre_close_row %d · 실패 %d (n=%d)" % (cr, pr, fail, n)) if n else ""
    if n < 10:
        return _mk("D6", "② 저장 무결성", "마감 확정행 동결 성공률", denom,
                   current=cur, target="≥ 95%", ok=None, n=n, note=note,
                   reason="판정선 미도달(%d/10경주) — 사후에 낮추지 않는다" % n)
    return _mk("D6", "② 저장 무결성", "마감 확정행 동결 성공률", denom,
               current=cur, target="≥ 95%", ok=(cur is not None and cur >= 95.0), n=n,
               note=note + " · 낮으면 recommendation_history 를 덮어쓰는 다른 경로를 전수 조사")


def check_module_load():
    """[D7 · 2026-07-31 신설] **선택 모듈이 조용히 죽어 있지 않은가.**

    🔴 실사고: `gemini_forecast.py` 문법 오류로 import 가 실패했고 서버는 `except` 로
      삼켜 `_gforecast = None` 인 채 돌았다. 로그 1줄뿐이라 **예측이 하루 종일 0건**이었다.
    ⚠ 여기서는 **문법만** 본다(서버 기동 없이 판정 가능). 목표는 항상 0건이다.
    """
    import ast as _ast
    targets, bad = [], []
    for d in (".", "tools", "tests"):
        base = BASE if d == "." else os.path.join(BASE, d)
        if not os.path.isdir(base):
            continue
        for nm in sorted(os.listdir(base)):
            if not nm.endswith(".py"):
                continue
            p = os.path.join(base, nm)
            if not os.path.isfile(p):
                continue
            rel = nm if d == "." else "%s/%s" % (d, nm)
            targets.append(rel)
            try:
                _ast.parse(open(p, encoding="utf-8").read())
            except SyntaxError as e:
                bad.append("%s:%s" % (rel, e.lineno))
            except Exception:
                pass
    n = len(targets)
    denom = "프로젝트 .py 자동 탐색(루트·tools·tests) — 하드코딩 목록 아님"
    return _mk("D7", "② 저장 무결성", "모듈 로드(문법) 실패", denom,
               current=len(bad), target=0, ok=(len(bad) == 0), n=n,
               note=("전부 정상 (%d개 검사)" % n) if not bad
                    else "🔴 " + " · ".join(bad[:5]))


# ══════════════ [무결성 감시 (2026-07-31 신설)] ══════════════
#  🔴 오늘 큰 결함 셋이 전부 "우연히 조사하다가" 잡혔다. **매일 측정이 잡은 게 아니다.**
#    · 경마 확정배당 0.9% — 며칠간 몰랐다
#    · 주기 백업 0회 실행 — 몇 주 몰랐다
#    · 같은 경기장 다른 날짜 파일 혼입 — 오늘 최대 오류의 원인
#  ⚠ 성능 측정(주 1회)과 **성격이 다르다**. 무결성 감시는 **매일·자동**이고 절대 줄이지 않는다.
#  ⚠ 각 항목에 "며칠 연속 미달이면 위험"을 명시한다.
_INTEG_DAYS = "⚠ 2일 연속 미달이면 조사, 3일이면 위험"


SETTLE_MIN = 40         # 마지막 발주 + 이 분(分) 이 지나야 확정값으로 본다(결과·환급 게시 여유)


def _is_settled():
    """그날 경주가 **다 끝났는가**. 반환 (확정여부, 사유).

    🔴 진행 중에 재면 결과 없는 경주가 계속 추가돼 값이 시간에 따라 떨어진다.
      2026-07-31 실측: 21:00 에 77.7% → 22:00 에 73.7%. **같은 날인데 값이 다르다.**
    ⚠ 스케줄을 못 읽으면 **미확정으로 둔다**(억지로 통과시키지 않는다).
    """
    try:
        import urllib.request
        d = json.loads(urllib.request.urlopen(
            "http://127.0.0.1:8011/api/multi/schedule", timeout=6).read().decode())
        pe = [r.get("postEpoch") for t in (d.get("tracks") or [])
              for r in (t.get("races") or []) if r.get("postEpoch")]
        if not pe:
            return True, None                     # 개최 없음 → 확정으로 본다
        last = max(pe)
        left = (last + SETTLE_MIN * 60) - time.time()
        if left > 0:
            return False, ("진행 중 — 마지막 발주 %s + %d분까지 %.0f분 남음(이 시각 전 값은 시간에 따라 변한다)"
                           % (time.strftime("%H:%M", time.localtime(last)), SETTLE_MIN, left / 60.0))
        return True, None
    except Exception as e:
        return False, "스케줄 조회 실패 — 확정 판정 불가(%s)" % str(e)[:50]


def check_payout_coverage():
    """[I1] 확정배당 보유율 — **종목별**. 🔴 경마 0.9% 가 이 항목이 있었으면 첫날 잡혔다."""
    day = time.strftime("%Y-%m-%d")
    files = glob.glob(os.path.join(BASE, "data", "analysis_log", day.replace("-", "_") + "_*.json"))
    by = {}
    for p in files:
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if not (d.get("result") or {}).get("1st"):
            continue
        sp = d.get("sport") or "?"
        t, o = by.get(sp, (0, 0))
        by[sp] = (t + 1, o + (1 if ((d.get("result") or {}).get("payouts") or {}).get("quinella") else 0))
    tot = sum(v[0] for v in by.values())
    ok_ = sum(v[1] for v in by.values())
    note = " · ".join("%s %d/%d(%.0f%%)" % (k, v[1], v[0], 100.0 * v[1] / max(v[0], 1))
                      for k, v in sorted(by.items()))
    cur = round(100.0 * ok_ / tot, 1) if tot else None
    worst = min((100.0 * v[1] / max(v[0], 1)) for v in by.values() if v[0] >= 5) if any(v[0] >= 5 for v in by.values()) else None
    # ⚠ I4 와 **같은 문제**가 있다 — 진행 중이면 결과가 늦게 들어오는 경주가 섞여 값이 낮게 나온다.
    _settled, _why = _is_settled()
    if not _settled:
        return _mk("I1", "🔴 무결성", "확정배당 보유율(종목별)",
                   "당일 결과확정 경주 %d건 — 종목별 분리" % tot,
                   current=cur, target=90.0, ok=None, n=tot,
                   note="⏳ **미확정**(진행 중) " + (note or "당일 결과 없음"), reason=_why)
    return _mk("I1", "🔴 무결성", "확정배당 보유율(종목별)",
               "당일 결과확정 경주 %d건 — 종목별 분리" % tot,
               current=cur, target=90.0,
               ok=(None if tot < 5 else (worst is not None and worst >= 90.0)),
               n=tot, note=note or "당일 결과 없음",
               reason=None if tot >= 5 else "표본 부족(%d<5)" % tot)


def check_backup_alive():
    """[I2] 백업 실제 실행 — 마지막 실행 시각과 경과. 🔴 6시간 주기가 0회인 것을 몇 주 몰랐다."""
    p = os.path.join(BASE, "data", "_periodic_backup_last.txt")
    try:
        last = float(open(p, encoding="utf-8").read().strip())
        el = (time.time() - last) / 3600.0
    except Exception:
        return _mk("I2", "🔴 무결성", "백업 마지막 실행 경과(시간)",
                   "data/_periodic_backup_last.txt", current=None, target="≤ 12",
                   ok=False, n=None, note="🔴 스탬프 파일 없음 — 백업이 한 번도 안 돌았다. " + _INTEG_DAYS)
    return _mk("I2", "🔴 무결성", "백업 마지막 실행 경과(시간)",
               "data/_periodic_backup_last.txt", current=round(el, 1), target="≤ 12",
               ok=(el <= 12.0), n=1,
               note="마지막 %s · %s" % (time.strftime("%m-%d %H:%M", time.localtime(last)), _INTEG_DAYS))


STALE_CODE_SLACK_SEC = 5.0   # app.py 수정과 부팅 스탬프의 허용 오차(초)


def check_stale_code():
    """[I8] 🔴 **지금 도는 서버가 최신 app.py 인가** — 리로더를 끈 상태의 유일한 위험을 잡는다.

    왜 필요한가 (2026-08-23)
      오늘 리로더 재기동이 **43회**였고 그중 6회가 사고, 한 번은 서버가 완전히 죽었다(00:10).
      그래서 운영을 `debug=False`(리로더 끔)로 돌렸다. 배경 데몬은 정상 기동을 확인했다.
      🔴 그런데 `debug=False` 의 유일한 단점이 **「고쳐도 반영이 안 되는데 아무도 모른다」**이다.
        CLAUDE.md 의 405 사고가 정확히 그 유형이고, 이 프로젝트가 가장 경계하는 **조용한 실패**다.
      ⇒ `app.py` 수정 시각과 부팅 스탬프를 대조하면 **시끄럽게** 만들 수 있다.
        이미 있는 두 값만 비교한다. 새 저장소를 만들지 않는다.

    ⚠ 판정: app.py mtime > 마지막 부팅 시각 + 5초 → 🔴 옛 코드 실행 중(재기동 필요).
    ⚠ 스탬프가 없으면 판정 보류(ok=None) — 배선 전 서버일 수 있다. 추측하지 않는다.
    """
    ap = os.path.join(BASE, "app.py")
    sp = os.path.join(BASE, "data", "_bg_boot_last.txt")
    try:
        mt = os.path.getmtime(ap)
    except Exception:
        return _mk("I8", "🔴 무결성", "서버가 최신 app.py 인가",
                   "app.py mtime ↔ data/_bg_boot_last.txt", current=None, target="최신",
                   ok=None, n=None, note="app.py 를 읽지 못했다 — 판정 보류")
    last = None
    try:
        for ln in reversed(open(sp, encoding="utf-8").read().strip().split("\n")):
            ln = ln.strip()
            if not ln:
                continue
            last = float(ln.split("\t")[0])
            break
    except Exception:
        last = None
    if last is None:
        return _mk("I8", "🔴 무결성", "서버가 최신 app.py 인가",
                   "app.py mtime ↔ data/_bg_boot_last.txt", current=None, target="최신",
                   ok=None, n=None,
                   note="부팅 스탬프 없음 — 판정 보류(스탬프 배선 전 서버일 수 있다)")
    gap = mt - last
    stale = gap > STALE_CODE_SLACK_SEC
    return _mk("I8", "🔴 무결성", "서버가 최신 app.py 인가",
               "app.py mtime ↔ data/_bg_boot_last.txt",
               current=("🔴 옛 코드(+%.0f초)" % gap) if stale else "최신",
               target="최신", ok=(not stale), n=1,
               note=("🔴 app.py 를 고친 뒤 재기동하지 않았다 — 지금 도는 것은 옛 코드다. "
                     "수정 %s · 기동 %s. 리로더가 꺼져 있어 자동 반영되지 않는다."
                     % (time.strftime("%m-%d %H:%M:%S", time.localtime(mt)),
                        time.strftime("%m-%d %H:%M:%S", time.localtime(last))))
               if stale else
               ("기동 %s · 코드 %s"
                % (time.strftime("%m-%d %H:%M:%S", time.localtime(last)),
                   time.strftime("%m-%d %H:%M:%S", time.localtime(mt)))))


SERVER_LOG_MAX_MIN = 15.0   # 🔴 이 시간 이상 콘솔 로그가 안 늘면 이상


def check_server_log_alive():
    """[I7] 서버 콘솔 로그가 **지금도 쌓이고 있나** — 최신 `logs/server*.log` 의 갱신 경과(분).

    🔴 왜 필요한가 (2026-08-01 실사고)
      가드 A·B·C 를 검증하려고 `logs/server_stdout.log` 를 봤는데 `[전적 오매칭 차단]` 이 **0회**였다.
      **하마터면 "가드가 안 걸렸다"고 보고할 뻔했다.** 실제로는 그 파일이 **07:03 에서 멈춰 있었고**
      현재 서버 로그는 `logs/server_b.log` 로 가고 있었다(가드는 **42회 정상 발동**).
      ⇒ 로그가 어디로 가는지 모르면 **모든 검증이 무의미**하다. 오늘은 원자료를 다시 봐서 피했지만
        다음엔 오판한다.

    ⚠ 원인은 "로그가 안 남는다"가 아니라 **파일이 18개로 흩어져 어느 것이 현재 것인지 모른다**는 것이다
      (`server_stdout`·`server_a`·`server_b`·`server_jra`·`server_jra4`·`server_jra5`…).
      서버가 죽고 다시 뜰 때마다 새 리다이렉트 파일이 생겼다.
    ⚠ ID 는 `I7` 이다 — `I6`(스냅샷 유입)은 **뺀 번호**라 재사용하면 과거 대조가 깨진다(I5 선례).
    ⚠ 그래서 **특정 파일명을 고정하지 않는다** — `logs/server*.log` 중 **가장 최근 것**을 본다.
      그래야 재기동으로 파일이 바뀌어도 감시가 따라간다(고치면 대상에서 빠지는 정의를 피한다 · 원칙 19).
    """
    import glob as _g
    fs = _g.glob(os.path.join(BASE, "logs", "server*.log"))
    if not fs:
        return _mk("I7", "🔴 무결성", "서버 콘솔 로그 갱신 경과(분)", "logs/server*.log",
                   current=None, target="≤ %d" % int(SERVER_LOG_MAX_MIN), ok=False, n=None,
                   note="🔴 서버 로그 파일이 하나도 없다 — 콘솔 출력이 **어디에도 안 남는다**. " + _INTEG_DAYS)
    newest = max(fs, key=os.path.getmtime)
    el = (time.time() - os.path.getmtime(newest)) / 60.0
    return _mk("I7", "🔴 무결성", "서버 콘솔 로그 갱신 경과(분)", "logs/server*.log",
               current=round(el, 1), target="≤ %d" % int(SERVER_LOG_MAX_MIN),
               ok=(el <= SERVER_LOG_MAX_MIN), n=len(fs),
               note="최신 %s (%s) · 로그파일 %d개 — 흩어져 있으면 검증이 엉뚱한 파일을 본다. %s"
                    % (os.path.basename(newest),
                       time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(newest))),
                       len(fs), _INTEG_DAYS))


DAEMON_LONG_SEC = 300.0     # 🔴 이 이상 주기의 sleep-first 데몬만 감시 대상
# 주기 상수 이름 → 초. `time.sleep(_KRA_BACKFILL_INTERVAL)` 처럼 변수로 쓰는 경우를 푼다.
_DAEMON_CONSTS = {"_KRA_BACKFILL_INTERVAL": 1200.0, "_PERIODIC_BACKUP_INTERVAL": 21600.0,
                  "iv": 21600.0, "_AMEDAS_TTL": 600.0}
# 스탬프 파일이 있는 데몬 — 새로 붙이면 여기에 추가한다.
_DAEMON_STAMPS = {"_kra_backfill_loop": "_kra_backfill_last.txt",
                  "_start_periodic_backup": "_periodic_backup_last.txt",
                  # 🔴 [2026-08-01] 중앙경마 착순·확정배당 수집(300초 = I3 대상).
                  #   ⚠ run-first 구조라 `_scan_daemons`(sleep-first 탐색)에는 안 잡힌다 —
                  #     그래서 여기 **명시 등록**한다. 안 하면 감시에서 통째로 빠진다.
                  "_jra_result_loop": "_jra_result_last.txt"}


def _scan_daemons():
    """app.py 에서 `while True` + **sleep-first** 데몬을 찾아 (함수, 주기초, 줄) 로 돌려준다.

    ⚠ 판정 기준을 **코드에서 자동 산출**한다 — 5분 이상 주기 데몬이 새로 생기면
      목록을 고치지 않아도 자동으로 감시 대상에 들어온다.
    """
    import ast as _ast
    import re as _re
    try:
        src = open(os.path.join(BASE, "app.py"), encoding="utf-8").read()
        tree = _ast.parse(src)
    except Exception:
        return [], []
    lines = src.split(chr(10))
    long_, low = [], []
    for n in _ast.walk(tree):
        if not isinstance(n, _ast.While) or getattr(n.test, "value", None) is not True:
            continue
        first = n.body[0] if n.body else None
        fl = getattr(first, "lineno", n.lineno)
        seg = chr(10).join(lines[fl - 1:fl + 1])
        m = _re.search(r"time\.sleep\(([^)]+)\)", seg)
        if not m:
            continue                                  # sleep-first 가 아니다
        raw = m.group(1).strip()
        try:
            per = float(raw)
        except Exception:
            per = _DAEMON_CONSTS.get(raw, 0.0)
        fn = "?"
        for k in range(n.lineno - 1, max(0, n.lineno - 120), -1):
            mm = _re.match(r"\s*def (\w+)", lines[k])
            if mm:
                fn = mm.group(1)
                break
        (long_ if per >= DAEMON_LONG_SEC else low).append((fn, per, n.lineno))
    return long_, low


def check_daemon_alive():
    """[I3] 🔴 **긴 주기(≥5분) sleep-first 데몬**의 실행시각 스탬프 보유.

    🔴 [2026-08-01 정의 변경] 종전 목표는 "sleep-first 5곳 전부"였다.
      그런데 60초 주기 데몬은 **재기동 공백이 60초**라 실제 위험이 아니다.
      위험하지 않은 것을 목표에 넣으면 **I3 가 영원히 미달**이고,
      항상 빨간 항목은 무시하게 된다(I5 를 회귀 테스트로 옮긴 것과 같은 실수).
    ⇒ 대상을 **주기 ≥ %d초** 로 한정한다. 60초 데몬은 "저위험"으로 note 에만 남긴다.
    """ % int(DAEMON_LONG_SEC)
    # 🔴 대상 = **긴 주기 데몬 전부**(이미 고친 것 + 아직 sleep-first 인 것).
    #   ⚠ 수정하면 sleep-first 가 아니게 되므로 `_scan_daemons` 만 보면 **대상이 0 이 된다** —
    #     고칠수록 분모가 사라지는 함정이다. `_DAEMON_STAMPS` 에 등록된 것을 **항상 포함**한다.
    long_, low = _scan_daemons()
    names = {fn for fn, _p, _l in long_} | set(_DAEMON_STAMPS)
    have, miss = 0, []
    for fn in sorted(names):
        f = _DAEMON_STAMPS.get(fn)
        if f and os.path.exists(os.path.join(BASE, "data", f)):
            have += 1
        else:
            _p = next((p for n2, p, _l in long_ if n2 == fn), 0.0)
            miss.append("%s(%.0f초)" % (fn, _p))
    tot = len(names)
    note = "보유 %d/%d" % (have, tot)
    if miss:
        note += " · 🔴 미보유: " + " · ".join(miss)
    if low:
        note += " · 저위험(감시 제외) %d곳: %s" % (
            len(low), " ".join("%s(%.0f초)" % (x[0], x[1]) for x in low))
    return _mk("I3", "🔴 무결성", "긴주기(≥5분) 데몬 스탬프",
               "app.py `while True` + sleep-first 중 **주기 ≥%d초** — 코드에서 자동 산출"
               % int(DAEMON_LONG_SEC),
               current=have, target=tot, ok=(tot > 0 and have >= tot), n=tot,
               note=note + " · " + _INTEG_DAYS)


def check_measurable_today():
    """[I4] 🔴 **오늘분 측정 가능 여부** — 결과·확정배당·마감시각이 **모두** 갖춰진 경주 비율.

    ⚠ 이것이 핵심이다. 축적 중에도 매일 확인되면 **일주일치가 통째로 날아갈 수 없다.**
    """
    day = time.strftime("%Y-%m-%d")
    files = glob.glob(os.path.join(BASE, "data", "analysis_log", day.replace("-", "_") + "_*.json"))
    tot = ok_ = 0
    miss = {"결과없음": 0, "확정배당없음": 0, "마감시각없음": 0}
    for p in files:
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        tot += 1
        res = d.get("result") or {}
        if not res.get("1st"):
            miss["결과없음"] += 1
            continue
        if not (res.get("payouts") or {}).get("quinella"):
            miss["확정배당없음"] += 1
            continue
        hp = p.replace("analysis_log", "odds_history")
        dl = None
        for cand in (hp, hp + ".gz"):
            if os.path.exists(cand):
                try:
                    if cand.endswith(".gz"):
                        import gzip
                        dl = (json.load(gzip.open(cand, "rt", encoding="utf-8")) or {}).get("deadline_epoch")
                    else:
                        dl = (json.load(open(cand, encoding="utf-8")) or {}).get("deadline_epoch")
                except Exception:
                    dl = None
                break
        if not dl:
            miss["마감시각없음"] += 1
            continue
        ok_ += 1
    cur = round(100.0 * ok_ / tot, 1) if tot else None
    # 🔴 [기준 시각 (2026-08-01)] 경주가 **진행 중**이면 결과 없는 경주가 계속 추가돼
    #   값이 시간에 따라 떨어진다(21:00 77.7% → 22:00 73.7%). **언제 재느냐로 값이 달라진다.**
    #   ⇒ 그날 마지막 발주 + `_SETTLE_MIN` 분이 지나야 **확정값**으로 본다.
    #   ⚠ 실측(2026-07-31): 마지막 발주 **23:30** 인데 카카오는 **21:00** 에 나간다 —
    #     발송 시점의 I4 는 구조적으로 미확정이다.
    _settled, _why = _is_settled()
    if not _settled:
        return _mk("I4", "🔴 무결성", "오늘분 측정 가능 비율",
                   "당일 분석로그 %d건 — 결과·확정배당·마감시각 3종 모두 보유" % tot,
                   current=cur, target=80.0, ok=None, n=tot,
                   note="⏳ **미확정**(진행 중) %d/%d · 결손: %s" % (ok_, tot, miss),
                   reason=_why)
    return _mk("I4", "🔴 무결성", "오늘분 측정 가능 비율",
               "당일 분석로그 %d건 — 결과·확정배당·마감시각 3종 모두 보유" % tot,
               current=cur, target=80.0, ok=(None if tot < 10 else (cur is not None and cur >= 80.0)),
               n=tot, note="%d/%d · 결손: %s · %s" % (ok_, tot, miss, _INTEG_DAYS),
               reason=None if tot >= 10 else "표본 부족(%d<10)" % tot)


def check_file_duplicate():
    """[I5] 파일 중복 감지 — 같은 경기장·경주번호의 **날짜가 다른 파일 수**.

    🔴 오늘 최대 오류의 원인이다. 날짜 없이 glob 매칭하면 다른 날 데이터가 섞인다.
    ⚠ 중복 자체는 정상이다(같은 경기장이 여러 날 개최). **매칭 코드가 날짜를 써야 한다**는 경보다.
    """
    import re as _re
    from collections import Counter
    c = Counter()
    for p in glob.glob(os.path.join(BASE, "data", "analysis_log", "*.json")):
        m = _re.match(r"\d{4}_\d{2}_\d{2}_(.+)\.json$", os.path.basename(p))
        if m:
            c[m.group(1)] += 1
    dup = {k: v for k, v in c.items() if v >= 2}
    worst = max(dup.values()) if dup else 0
    return _mk("I5", "🔴 무결성", "동명 경주 최대 중복 일수",
               "analysis_log 전체 %d파일 · 경기장+경주번호 기준" % sum(c.values()),
               current=worst, target="기록만", ok=None, n=len(c),
               note="중복 키 %d종 · 최대 %d일 · 🔴 날짜 없는 glob 매칭 금지(원칙 16) · %s"
                    % (len(dup), worst, _INTEG_DAYS))


# ══════════════ 🔴 스냅샷 유입 감시 (2026-08-01 · 승인 A안) ══════════════
#   배경: 배당판 PNG 스냅샷이 **2026-07-30 18:25 이후 0장**이었는데 **이틀간 아무도 몰랐다.**
#     스냅샷은 서버가 만들지 않는다 — 확장 오버레이가 화면을 캡처해 POST 한다.
#     ⇒ 서버는 "안 온 요청"을 알 방법이 **구조적으로 없었다.** 그게 이 항목의 존재 이유다.
#
#   🔴 판정 단위를 **"연속 N경주 0장"으로 잡으면 안 된다** — 오탐이 쏟아진다.
#     스냅샷은 사람이 그 배당판 탭을 **포그라운드로 열어둔 경주에만** 찍힌다.
#     실측(7/30): 카드 108건 중 스냅샷 보유 26건 = **24%**. 즉 평소에도 **연속 0장이 정상**이다.
#   ⇒ 단위는 **"당일 전체"**: 발주완료 경주가 N건 이상인데 **당일 스냅샷 총 0장**이면 이상.
#     이 기준은 7/31·8/1(0장)을 잡고 7/30(75장)은 안 잡는다.
SNAPSHOT_INGEST_MIN_RACES = 10   # N — 하루 발주완료가 100~130건이라 10이면 오전 중 걸린다.
#   ⚠ 너무 작으면(예: 3) 개최가 거의 없는 날 오탐, 크면(예: 50) 오후까지 못 잡는다.
#     10 = "오전 한나절을 통째로 놓쳤다"는 뜻이라 사람이 개입할 시간이 남는다.


def _posted_races_today(now=None):
    """오늘 **발주가 지난** 경주 수 — data/today_schedule.json 의 postEpoch 기준(읽기 전용)."""
    now = now or time.time()
    p = os.path.join(BASE, "data", "today_schedule.json")
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        return 0
    n = 0
    for t in (d.get("tracks") or []):
        for r in (t.get("races") or []):
            try:
                if float(r.get("postEpoch") or 0) <= now:
                    n += 1
            except Exception:
                continue
    return n


def snapshot_ingest_counts(day=None):
    """(당일 스냅샷 PNG 수, 마지막 저장 시각, 발주완료 경주 수) — D안 API 와 공유한다."""
    day = day or time.strftime("%Y_%m_%d")
    sd = os.path.join(BASE, "data", "snapshots")
    cnt, last = 0, None
    try:
        for f in os.listdir(sd):
            if not f.lower().endswith(".png"):
                continue
            if f.startswith(day):
                cnt += 1
            try:
                mt = os.path.getmtime(os.path.join(sd, f))
                if last is None or mt > last:
                    last = mt
            except Exception:
                pass
    except Exception:
        pass
    return cnt, last, _posted_races_today()


def check_snapshot_ingest():
    """🔴 I6 배당판 스냅샷 유입(당일) — 0장이면 회원 화면 경주기록에 이미지가 안 뜬다.

    ⚠ ID 를 `I5` 로 쓰지 않는다 — `I5`(파일 중복)는 회귀 테스트로 이관되며 제거된 번호다.
      번호를 재사용하면 과거 기록과 대조가 깨진다.
    """
    cnt, last, posted = snapshot_ingest_counts()
    age_h = round((time.time() - last) / 3600.0, 1) if last else None
    den = "당일 발주완료 경주 %d건(today_schedule postEpoch 기준) · 판정선 %d건 이상" % (
        posted, SNAPSHOT_INGEST_MIN_RACES)
    note = "마지막 스냅샷 %s (%s시간 전)" % (
        time.strftime("%m-%d %H:%M", time.localtime(last)) if last else "없음", age_h)
    if posted < SNAPSHOT_INGEST_MIN_RACES:
        return _mk("I6", "🔴 무결성", "배당판 스냅샷 유입(당일)", den, current=cnt, target="≥ 1",
                   ok=None, n=posted, note=note,
                   reason="⏳ 판정 보류 — 발주완료 %d건 < %d건(개최 적은 시간대)" % (
                       posted, SNAPSHOT_INGEST_MIN_RACES))
    if cnt > 0:
        return _mk("I6", "🔴 무결성", "배당판 스냅샷 유입(당일)", den, current=cnt, target="≥ 1",
                   ok=True, n=posted, note=note)
    return _mk("I6", "🔴 무결성", "배당판 스냅샷 유입(당일)", den, current=0, target="≥ 1",
               ok=False, n=posted, note=note,
               reason="🔴 발주완료 %d경주인데 당일 스냅샷 0장 — 확장 오버레이가 캡처를 안 보내고 있다. "
                      "후보: ⓐ배당판 탭이 포그라운드가 아님 ⓑ오버레이 OFF ⓒtimerDeadline 미갱신" % posted)


def build_checklist():
    """반환 dict 의 **최상단에 `summary` 계열을 배치**한다(모바일에서 먼저 보이도록).
    ⚠ 응답은 `ensure_ascii=False` + UTF-8 로 내보낼 것 — `\\uCda9\\uC871` 로 깨지면 외부에서 못 쓴다."""
    items = [check_snapshot_coverage(),
             check_save_failures_fixed(), check_save_failures_unfixed(),
             check_schema_contract(), check_schema_drift(),
             check_score_decomposition(), check_freeze_success(), check_module_load(),
             # 🔴 무결성 감시(매일·자동) — 성능 측정과 성격이 다르다. 절대 줄이지 않는다.
             check_payout_coverage(), check_backup_alive(), check_daemon_alive(),
             check_server_log_alive(),   # I7 — 로그가 지금도 쌓이는가(검증 신뢰성의 전제)
             # 🔴 [2026-08-23] I8 — 리로더를 끈 운영의 **유일한 위험**을 잡는다.
             #   debug=False 로 돌리면 고쳐도 반영이 안 되는데 아무도 모른다(405 사고 유형).
             #   app.py mtime ↔ 부팅 스탬프 대조로 그 조용한 실패를 시끄럽게 만든다.
             check_stale_code(),
             # 🔴 [2026-08-01 · 권대표 결정] `check_snapshot_ingest()`(I6) **체크리스트에서 뺀다.**
             #   같은 날 아침에 넣었다가 같은 날 뺐다 — 스냅샷 **판정 경로를 중단**하기로 결정됐기 때문이다.
             #   **원칙 18: 안 쓰는 것을 감시하면 노이즈다.** 안 쓰기로 한 데이터가 안 들어온다고
             #   매일 빨간불을 켜면, 그 빨간불이 다른 진짜 경보까지 무디게 만든다.
             #   ⚠ 함수는 **지우지 않았다**(무삭제). 스냅샷을 다시 쓰기로 하면 이 줄만 되살리면 된다.
             #   ⚠ `/api/snapshot/health`(D안)도 남아 있으나 **체크리스트·카카오에는 넣지 않는다** —
             #     필요할 때 사람이 직접 호출하는 진단용이다(호출 안 하면 비용 0).
             # check_snapshot_ingest(),
             check_measurable_today(),   # ⚠ I5(파일 중복)는 제거 — 중복 자체는 정상이라
             #    영원히 빨간불이 되고 그러면 무시하게 된다. → tests/run_glob_safety.py 로 이관.
             check_forecast_discard(), check_forecast_vs_market(), check_forecast_highodds(),
             # 🟢 [2026-09-01] ①②③ 구현분 7종 — 미측정 16 → 9
             check_a1_pace_fields(), check_a2_grade_at_bonus(), check_a3_declared_style(),
             check_a4_drops_raw(), check_b3_ghost_no(),
             check_c2_payout_coverage(), check_c4_kakao_log()]
    for (i, area, name, target, denom, why) in _PENDING:
        items.append(_mk(i, area, name, denom, current=None, target=target,
                         ok=None, n=None, note="분모 근거: " + why, reason="미구현"))
    # 🔴 [분리 집계 (2026-07-31)] 무결성(I)과 완료조건(D·F·A·B·C)은 성격이 다르다.
    #   · 무결성 = **항상 초록이어야 정상**(채워가는 것이 아니다)
    #   · 완료조건 = **N/23 을 채워가는 것**
    #   섞어 세면 구조적 미달(I3 스탬프 1/5)이 완료선을 영원히 막는다.
    #   ⚠ 완료 정의는 "완료조건 23이 전부 초록"으로 유지한다.
    integ = [x for x in items if str(x.get("id") or "").startswith("I")]
    comp = [x for x in items if not str(x.get("id") or "").startswith("I")]
    integ_bad = sum(1 for x in integ if x["ok"] is False)
    done = sum(1 for x in comp if x["ok"] is True)
    fail = sum(1 for x in comp if x["ok"] is False)
    unk = sum(1 for x in comp if x["ok"] is None)
    # 🔴 [승인③] 당일 의존 미측정분을 `⏳미확정` 으로 재분류한다.
    #   ⚠ `unk`(미측정 총수)는 그대로 두고 **그 안에서만** 나눈다 → 분모 23 불변.
    pending_today = _mark_pending_today(comp)
    unk_real = unk - pending_today                    # 시각과 무관하게 못 잰 것(미구현 등)
    # 미충족만 뽑는다 — **충족 항목은 넣지 않는다.** 목록이 짧아지는 것이 진행 신호다.
    open_items = ["[%s] %s (현재 %s / 목표 %s)" % (x["id"], x["name"], x["current"], x["target"])
                  for x in comp if x["ok"] is False]
    pending = ["[%s] %s" % (x["id"], x["name"]) for x in comp if x["ok"] is None]
    # ⏳미확정 목록(당일 데이터 대기) — '미구현'과 섞이지 않게 따로 낸다.
    pending_list = ["[%s] %s" % (x["id"], x["name"]) for x in comp if x.get("pendingToday")]
    return {"summary": "완료조건 %d/%d · 미충족 %d · ⏳미확정 %d · 미측정 %d  |  무결성 %s"
                       % (done, len(comp), fail, pending_today, unk_real,
                          "정상" if integ_bad == 0 else "이상 %d건" % integ_bad),
            "integrity": {"total": len(integ), "bad": integ_bad,
                          "items": [{"id": x["id"], "name": x["name"], "ok": x["ok"],
                                     "current": x["current"], "target": x["target"]} for x in integ]},
            # ⚠ total 은 23 고정(분모 불변). pendingToday 는 unmeasured 안에서 갈라낸 값이다.
            "completion": {"total": len(comp), "done": done, "failed": fail, "unmeasured": unk,
                           "pendingToday": pending_today, "unmeasuredReal": unk_real},
            "openItems": open_items,
            "pendingItems": pending,
            "pendingTodayItems": pending_list,
            "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total": len(comp), "done": done, "failed": fail, "unmeasured": unk,
            "items": items}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = build_checklist()
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0
    print("=" * 100)
    print("완료 조건 체크리스트 — %s" % r["summary"])
    print("생성 %s" % r["generatedAt"])
    print("=" * 100)
    area = None
    for it in r["items"]:
        if it["area"] != area:
            area = it["area"]
            print("\n[%s]" % area)
        mark = ("✅" if it["ok"] is True else "❌" if it["ok"] is False
                else "⏳" if it.get("pendingToday") else "⬜")   # ⏳=당일 데이터 대기 / ⬜=미구현·미측정
        print("  %s %-4s %-32s 현재 %-10s / 목표 %-8s" % (
            mark, it["id"], it["name"], it["current"], it["target"]))
        print("       분모: %s" % it["denominator"])
        if it.get("reason"):
            print("       ⚠ %s" % it["reason"])
        if it.get("note"):
            print("       · %s" % it["note"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
