# Railway 배포 가이드 — 24시간 자동수집(지방경마·경륜·한국경마)

기존 분석 서버(`app.py`)를 Railway에 배포해 **24시간 백그라운드 자동수집**을 돌리기 위한 절차. bmed-public 공개 서버는 이 서버를 업스트림으로 프록시한다.

## 핵심 수정(코드 반영 완료)
- **gunicorn 스케줄러 기동 문제 해결**: 백그라운드 작업(다중경주 자동수집·6시간 백업·학습일지·날짜정리)이 `if __name__=="__main__"` 안에만 있어 gunicorn에서 전부 스킵되던 것을 → `_boot_background()` 로 묶고 **모듈 로드 시점(`SERVER_SOFTWARE=gunicorn`)에도 기동**하도록 수정. 멱등(중복 방지).
- **Procfile `--workers 1`**: 워커가 여러 개면 자동수집·git 백업이 중복 실행되므로 **반드시 1 워커**(+threads 8로 동시성 확보).
  ```
  web: gunicorn app:app --workers 1 --threads 8 --bind 0.0.0.0:$PORT --timeout 120
  ```

## ⚠️ Railway 임시 파일시스템(가장 중요)
Railway 컨테이너 파일시스템은 **재배포·재시작 시 초기화**된다. 런타임에 `data/` 에 쌓인 배당·결과·학습 데이터가 유실될 수 있다. 두 가지로 방어:

### 방법 A — Railway Volume(권장·영구 디스크)
1. Railway 프로젝트 → **Variables** 옆 **Volumes** → New Volume.
2. Mount path: 프로젝트의 `data` 절대경로(예: `/app/data`). 컨테이너 작업디렉터리가 `/app` 이면 `/app/data`.
3. 마운트 후에는 `data/` 쓰기가 볼륨에 영구 저장 → 재배포에도 유지.
   - 추적 데이터(`analysis_log/`·`race_results/`·`race_report/`·`ai_training/` 등)는 git 클론으로 초기 시드되고, 이후 런타임 갱신분은 볼륨에 축적.

### 방법 B — git 백업(보조·이미 구현됨)
- 서버는 결과 입력마다 `_data_git_backup`(5초 디바운스)로 코퍼스를 자동 add+commit+push 한다.
- Railway에서 **push 하려면 자격증명 필요**: 환경변수에 `GITHUB_TOKEN`(repo 권한 PAT) 설정 + 원격을 토큰 URL로.
  - 볼륨(A)을 쓰면 필수는 아님(로컬 영속). A를 우선 권장.

## 환경변수(Railway → Variables)
| 변수 | 값 | 필수 |
|---|---|---|
| `ANTHROPIC_API_KEY` | 실제 Claude 키 | 한국 PDF 분석 시 |
| `KRA_API_KEY` | 한국마사회 API 키 | KRA 배당 수집 시 |
| `FLASK_ENV` | `production` | 권장(debug OFF·에러 민감정보 숨김) |
| `PORT` | (Railway 자동 주입) | 자동 |
| `BACKUP_INTERVAL_HOURS` | `6`(기본) | 선택 |
| `DAILY_LEARNING_HOUR` | `22`(기본) | 선택 |

⚠️ **`.env` 파일은 절대 커밋/업로드 금지**(gitignore). 위 값은 Railway 대시보드 Variables에만 입력.

## 배포 절차
1. 이 저장소를 Railway에 연결(GitHub repo → New Project → Deploy from repo).
2. 위 **환경변수** 입력, **Volume**(`/app/data`) 마운트.
3. 배포 → 빌드(`requirements.txt`: flask·flask-cors·gunicorn·anthropic·PyMuPDF) → `Procfile` 로 gunicorn 1워커 기동.
4. 로그에서 확인:
   - `[부팅] 백그라운드 작업 기동 완료(자동수집·백업·학습·날짜정리)`
   - `[다중경주 스케줄] … 트랙 N곳(경륜 M곳 포함)`
5. `https://<railway-domain>/api/health` → `{"status":"ok"}` 확인.

## 배포 후 점검 체크리스트
- [ ] `/api/health` 200·`status:ok`
- [ ] 로그에 `[부팅] 백그라운드 … 기동 완료` (스케줄러 정상)
- [ ] `/api/multi/dashboard` 에 지방경마·경륜 카드 표시(자동수집 동작)
- [ ] `/api/multi/schedule` 에 오늘 트랙(경륜 포함)
- [ ] `/api/kra/status` → `keySet:true`(KRA 키 인식)
- [ ] 재배포 후에도 `data/` 유지(볼륨 마운트 확인)
- [ ] bmed-public `BMED_UPSTREAM` 을 이 Railway 도메인으로 설정 → `/health` `upstream_ok:true`

## 알려진 제약 / 주의
- **1 워커 고정**: 자동수집·백업 중복 방지를 위해 워커를 늘리지 말 것(동시성은 threads로).
- **KRA API**: 현재 `API160_1` 이 HTTP 500(키 미활성 추정). 활성화 후 `/api/kra/test` 로 필드 확인.
- **oddspark 수집**: Railway 아웃바운드로 oddspark 접근 필요(차단 시 수집 실패 로그). 대부분 정상.
- **시간대**: 발주시각 계산은 서버 로컬시간 기준. Railway는 UTC이므로 `TZ=Asia/Tokyo`(일본경마·경륜) 또는 필요 시간대 환경변수 설정 권장.
