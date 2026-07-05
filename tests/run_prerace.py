#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PDF 전경주 사전분석 저장소 [2번] 단위 검증 (서버 불필요, app.py 직접 로드 + 임시 폴더 격리).

검증: _prerace_key · _prerace_save_race · _prerace_index_load · _prerace_load ·
      _prerace_clear · 경로조작 방어. 프로덕션 data/prerace/ 는 건드리지 않는다(monkeypatch).

실행: python tests/run_prerace.py
"""
import sys, os, tempfile, importlib.util

sys.stdout.reconfigure(encoding="utf-8")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("appmod", os.path.join(_ROOT, "app.py"))
app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(app)

P = {"pass": 0, "fail": 0}


def chk(label, ok, extra=""):
    print(f"  {'✅' if ok else '❌'} {label}{(' — ' + extra) if extra else ''}")
    P["pass" if ok else "fail"] += 1
    return ok


print("=" * 60)
print("PDF 전경주 사전분석 저장소 검증")
print("=" * 60)

# 프로덕션 오염 방지: 임시 폴더로 KOREA_PRERACE_DIR monkeypatch
_tmp = tempfile.mkdtemp(prefix="prerace_test_")
app.KOREA_PRERACE_DIR = _tmp
print(f"  (임시 저장소: {_tmp})")

# ── 키 생성 ─────────────────────────────────────────────
chk("키: 부산 3R → '2026-07-05_부산_3'", app._prerace_key("2026-07-05", "부산", 3) == "2026-07-05_부산_3",
    app._prerace_key("2026-07-05", "부산", 3))
chk("키: 경마장 미상 → '기타'", app._prerace_key("2026-07-05", "", 5).endswith("_기타_5"),
    app._prerace_key("2026-07-05", "", 5))

# ── 저장 → 목록 → 로드 ──────────────────────────────────
race1 = {"venue": "부산", "raceNo": 3, "distance": "1200M", "title": "부산 3경주 1200M",
         "horses": [{"horseNum": 1, "name": "말가"}, {"horseNum": 2, "name": "말나"}],
         "report": {"grades": {"A": 1}, "bet": "복승 1-2"}, "status": "done"}
race2 = {"venue": "서울", "raceNo": 5, "distance": "1000M", "title": "서울 5경주 1000M",
         "horses": [{"horseNum": 1, "name": "말다"}], "report": {"bet": "복승 1-3"}, "status": "done"}
k1 = app._prerace_save_race("2026-07-05", race1)
k2 = app._prerace_save_race("2026-07-05", race2)
chk("경주1 저장 → 파일 생성", os.path.exists(os.path.join(_tmp, k1 + ".json")))
chk("경주2 저장 → 파일 생성", os.path.exists(os.path.join(_tmp, k2 + ".json")))
chk("index.json 생성", os.path.exists(os.path.join(_tmp, "index.json")))

idx = app._prerace_index_load()
chk("목록 2건", len(idx) == 2, str(len(idx)))
chk("목록에 리포트 본문 없음(경량)", all("report" not in e for e in idx))
chk("목록에 horseCount 포함", any(e.get("horseCount") == 2 for e in idx))

full = app._prerace_load(k1)
chk("개별 로드 → 리포트 본문 포함", full is not None and full.get("report", {}).get("bet") == "복승 1-2",
    str(full and full.get("report")))
chk("개별 로드 → 출전마 2두", full and len(full.get("horses", [])) == 2)

# ── 재저장(동일 키) → index 중복 없이 교체 ──────────────
race1b = dict(race1); race1b["report"] = {"bet": "복승 2-4(수정)"}
app._prerace_save_race("2026-07-05", race1b)
idx2 = app._prerace_index_load()
chk("동일 키 재저장 → 목록 여전히 2건(중복X)", len(idx2) == 2, str(len(idx2)))
chk("재저장 반영 → 리포트 갱신됨", app._prerace_load(k1).get("report", {}).get("bet") == "복승 2-4(수정)")

# ── 경로조작 방어 ───────────────────────────────────────
chk("경로조작 '../etc' → None", app._prerace_load("../etc") is None)
chk("경로조작 'a/b' → None", app._prerace_load("a/b") is None)
chk("없는 키 → None", app._prerace_load("2099-01-01_없음_9") is None)

# ── index 유실 시 디렉터리 스캔 복구 ────────────────────
os.remove(os.path.join(_tmp, "index.json"))
rebuilt = app._prerace_index_load()
chk("index 유실 → 디렉터리 스캔으로 2건 복구", len(rebuilt) == 2, str(len(rebuilt)))

# ── 초기화 ──────────────────────────────────────────────
app._prerace_clear()
chk("초기화 → 파일 0개", len([f for f in os.listdir(_tmp)]) == 0)
chk("초기화 후 목록 빈 배열", app._prerace_index_load() == [])

# 정리
try:
    os.rmdir(_tmp)
except OSError:
    pass

print("=" * 60)
print(f"결과: 통과 {P['pass']} / 실패 {P['fail']}")
print("=" * 60)
sys.exit(1 if P["fail"] else 0)
