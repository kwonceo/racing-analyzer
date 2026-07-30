# 저장 동시성 — 손상 파일 + 경합

| | |
|---|---|
| **발생** | 2026-07-30 |
| **상태** | ✅ **해결** (단 🟡 미적용 17곳 잔존 — 아래 6번) |
| **영향 범위** | 모든 JSON 저장 경로 |

## 1. 증상
- 저장 중 `PermissionError` (WinError 32/5) 가 반복 발생
- 손상된 JSON 을 읽은 쪽이 파싱 실패 → **빈 기본값으로 재저장** → 데이터가 지워짐

## 2. 원인
**두 가지가 겹쳤다.**

### ⓐ tmp 파일 이름 충돌
`path + ".tmp"` 로 고정된 이름을 썼다. 두 스레드가 같은 파일을 저장하면
같은 tmp 를 동시에 쓰고, `os.replace` 가 서로를 밟았다.

### ⓑ Windows 의 `os.replace` 제약
대상 파일을 **누군가 열고만 있어도** `os.replace` 가 실패한다(WinError 32).
리눅스와 달리 Windows 는 열린 파일을 교체할 수 없다.

### ⓒ 파싱 실패 시 빈 값 재저장
`except` 안에서 빈 dict/list 를 돌려주고, 그 값이 그대로 다시 저장됐다.
🔴 **읽기 실패가 쓰기 삭제로 이어졌다.**

## 3. 조치
`_json_atomic` 을 3중으로 강화했다(`app.py`).

```python
_FILE_LOCK_SLOTS = 1024
_FILE_LOCKS = [threading.RLock() for _ in range(_FILE_LOCK_SLOTS)]
_ATOMIC_RETRY = 3

# ① 경로별 락  ② tmp 고유화(pid + thread id)  ③ 재시도
_lk = _FILE_LOCKS[hash(path) % _FILE_LOCK_SLOTS]
with _lk:
    tmp = "%s.tmp%d_%d" % (path, os.getpid(), threading.get_ident())
    ...
```

그리고 `_json_load_guard(path, default, tag)` 를 만들어 **손상 파일을 격리**했다.
- 손상 감지 → `.corrupt.<타임스탬프>` 로 **복사**(이동 아님 — 원본 보존)
- `data/collect_gaps/<날짜>.json` 에 기록
- 🔴 **그 사이클의 저장을 건너뛴다** — 빈 값으로 덮어쓰지 않는다

## 4. 근거 (⚠ 분모 명시)
| 지표 | 수정 전 | 수정 후 |
|---|---|---|
| 동일 조건 재현 테스트 `PermissionError` | 269회 | **0회** |
| 운영 중 수정 경로 저장 실패 | 기준 100% | **-85%** |

## 5. 상태
✅ **해결** — 단 아래 한 가지가 남아 있다.

🟡 **`path + ".tmp"` 를 그대로 쓰는 곳이 17곳 남아 있다**(체크리스트 D2b).
이번 수정은 `_json_atomic` 경유 경로만 덮었다. 17곳은 별건 보류(ⓐ).

## 6. 재발 방지
- 🔴 **원칙 9 신설**: *"`except` 안에서 빈 기본값을 재저장하면 데이터를 지운다."*
  읽기 실패는 **쓰기를 멈추는 신호**이지 빈 값으로 덮으라는 뜻이 아니다.
- 새 저장 경로는 반드시 `_json_atomic` 을 경유한다.
- 체크리스트 **D2a**(수정 경로) / **D2b**(미수정 17곳)로 분리 추적 중.
