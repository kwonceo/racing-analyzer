# 🔧 복구 가이드 (RECOVERY.md)

경마 BMED 분석기가 문제가 생겼을 때 빠르게 복구하는 방법입니다.
> 안정 체크포인트 태그: **`v2.3.0-stable`** (AI 준비 인프라 완성)

---

## 1. 서버가 안 켜질 때

```bat
:: 서버 실행 (경로: C:\Users\USER\Desktop\경마분석서버)
py app.py
```

**포트 8011 충돌 시** (이미 다른 프로세스가 사용 중):
```bat
netstat -ano | findstr :8011
:: 마지막 열의 PID 확인 후
taskkill /PID <PID> /F
:: 다시 실행
py app.py
```

**`import fitz`(PyMuPDF) 오류** — PDF 분석만 막히고 서버는 503으로 뜹니다(405 아님). 필요 시:
```bat
pip install -r requirements.txt
```

**문법 검증**(서버가 죽으면 최근 수정 파일부터 확인):
```bat
py -c "import ast; ast.parse(open('app.py',encoding='utf-8').read())"
node -e "new Function(require('fs').readFileSync('static/js/app.js','utf8'))"
```

---

## 2. Chrome 확장이 안 될 때

1. 주소창에 `chrome://extensions` 입력 → 이 확장의 **↻ 새로고침**
2. 배당판 탭(**keiba.go.jp** 또는 사설 보드) **F5**
3. 여전히 안 되면 **재설치**: `chrome://extensions` → 확장 제거 → **압축해제된 확장 로드** → `chrome-extension` 폴더 선택
4. 팝업에서 **전체 자동 수집 다시 시작**

> ⚠️ 확장 코드를 바꾼 뒤에는 반드시 새로고침해야 새 코드가 돕니다(팝업에 옛 문구가 보이면 구코드 실행 중).

---

## 3. 데이터가 날아갔을 때

학습·AI 코퍼스(`data/analysis_log/`, `data/race_results/`, `data/ai_training/`, `data/daily_summary/`, `data/prerace/`, `data/korea_history/`)는 **GitHub에 백업**되어 있습니다.

```bat
:: 안정 체크포인트로 되돌리기(데이터 포함)
git checkout v2.3.0-stable

:: 특정 파일만 복구
git checkout v2.3.0-stable -- data/ai_training/

:: 원격의 최신 데이터 다시 받기
git pull origin master
```

> 고빈도 임시 파일(`triple_store.json`·`odds_history/`·`learning.json` 등)은 gitignore라 백업되지 않습니다(정상). 라이브 수집으로 다시 채워집니다.

---

## 4. 코드를 되돌리고 싶을 때

```bat
:: 커밋 이력 보기
git log --oneline

:: 특정 커밋 상태로 파일 확인(되돌리기 전 점검)
git checkout <커밋ID> -- app.py

:: 마지막 커밋으로 워킹트리 복구(주의: 미커밋 변경 사라짐)
git checkout -- app.py

:: 커밋 자체를 되돌리는 새 커밋(안전)
git revert <커밋ID>
```

버전/태그 목록:
```bat
git tag
git show v2.3.0-stable
```

---

## 5. 전체 백업 실행

```bat
scripts\backup_checkpoint.bat
```
→ `data/` 전체 커밋 + GitHub 푸시 + 백업 날짜 기록(`data/backup_log.txt`).

---

## 6. 자주 쓰는 검증/테스트

```bat
py tests\run_flow.py
py tests\run_formula.py
py tests\run_prerace.py
node tests\run_stats.js
```
모두 통과해야 정상(현재 79건).

---

## 긴급 연락 체크리스트
- [ ] 서버 떠 있나? → `http://127.0.0.1:8011` 접속
- [ ] 확장 최신인가? → 팝업 버전 v2.1.x 확인 + 새로고침
- [ ] 데이터 백업됐나? → `git status` 후 `backup_checkpoint.bat`
- [ ] 테스트 통과하나? → 위 4개 실행
