#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""[AI 데이터 정비] legacy AI 학습 파일에 schema_version 백필(1회 실행).

data/ai_training/*.json 중 schema_version 이 없는(구버전) 파일에 현재 버전을 채워
전체 스키마를 통일한다. 이미 버전이 있는 파일은 건너뛴다(멱등·재실행 안전).

사용법:
    python tools/migrate_ai_schema.py            # 실제 백필
    python tools/migrate_ai_schema.py --dry-run  # 변경 없이 대상만 표시
"""
import os
import sys
import json
import glob

# app.py 의 AI_SCHEMA_VERSION 과 동일 값(단일 소스 우선 시도, 실패 시 상수 폴백)
TARGET_VERSION = "1.0"
try:
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, _root)
    import re
    with open(os.path.join(_root, "app.py"), encoding="utf-8") as _f:
        _m = re.search(r'AI_SCHEMA_VERSION\s*=\s*["\']([^"\']+)["\']', _f.read())
        if _m:
            TARGET_VERSION = _m.group(1)
except Exception:
    pass

AI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ai_training")


def main():
    dry = "--dry-run" in sys.argv
    if not os.path.isdir(AI_DIR):
        print(f"[마이그레이션] 디렉토리 없음: {AI_DIR}")
        return
    files = sorted(glob.glob(os.path.join(AI_DIR, "*.json")))
    total = len(files)
    migrated, skipped, errors = 0, 0, 0
    print(f"[마이그레이션] 대상 폴더: {AI_DIR}")
    print(f"[마이그레이션] 총 {total}개 파일 · 목표 schema_version = {TARGET_VERSION}"
          + (" · (dry-run: 변경 안 함)" if dry else ""))
    for path in files:
        name = os.path.basename(path)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            errors += 1
            print(f"  ⚠ 읽기 실패: {name} ({e})")
            continue
        if data.get("schema_version"):
            skipped += 1
            continue
        # schema_version 을 맨 앞에 오도록 새 dict 재구성(가독성)
        new_data = {"schema_version": TARGET_VERSION}
        new_data.update(data)
        if dry:
            migrated += 1
            print(f"  (dry) 백필 예정: {name}")
            continue
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(new_data, f, ensure_ascii=False, indent=2)
            migrated += 1
            print(f"  ✅ 백필: {name}")
        except Exception as e:
            errors += 1
            print(f"  ⚠ 쓰기 실패: {name} ({e})")
    print("─" * 50)
    print(f"[결과] 백필 {migrated} · 이미최신(건너뜀) {skipped} · 오류 {errors} · 전체 {total}")
    if not dry and migrated:
        print("→ 완료. 이제 /api/ai-training/status 의 schema_counts 에서 legacy 가 사라집니다.")


if __name__ == "__main__":
    main()
