# 내일 테스트 체크리스트 — 경륜 · 일본경마 (bmed-public 연동)

> 대상: 기존 분석 서버(`127.0.0.1:8011`) + bmed-public 공개 서버(`127.0.0.1:8012`, 프록시).
> API 상세 형식은 [`PUBLIC_API.md`](./PUBLIC_API.md) 참고.

## 0. 사전 준비 (테스트 시작 전)
- [ ] 기존 서버 실행: `python app.py` → `http://127.0.0.1:8011/api/health` 가 `{"status":"ok"}` 인지 확인
- [ ] bmed-public 실행: `python app_public.py` → 콘솔에 `[스케줄러] 백그라운드 시작` 로그 확인
- [ ] bmed-public 헬스: `http://127.0.0.1:8012/health` → `upstream_ok: true` 확인(업스트림 연결)
- [ ] Chrome 확장 최신 로드(배당판 자동수집용) — 확장 새로고침
- [ ] 오늘 날짜(YYYYMMDD) 확인 — `raceDy` 미지정 시 서버 로컬 오늘로 자동 설정됨

## 1. 경륜 테스트 순서
1. [ ] **개최 확인**: oddspark 경륜 페이지에서 오늘 개최장 joCode 확인(예: 오다와라 36 · 마에바시 01 · 기후 04)
2. [ ] **출마표(전적) 수집**: `POST /api/keirin/card` `{joCode,kaisaiBi(YYYYMMDD),raceNo,raceKey}`
   - 확인: `analysis.ranked[]` 에 차번·競走得点·각질·등급이 채워짐 / `linkedRaceKey` 반환
3. [ ] **배당 수집**: `POST /api/keirin/odds` `{joCode,kaisaiBi,raceNo,raceKey}`
   - 확인: `counts.quinella`/`counts.exacta` > 0, `ingest` 반영
   - 반복 호출(폴링) 시 배당변화·역배열 감지되는지
4. [ ] **분석 확인**: `POST /api/odds/triple/analyze` `{raceKey}` → `corePicks.finalQuinellas`/`finalTrifectas`(복승2+삼복승2), `signalQuality`
5. [ ] **공개 프록시 경유**: `POST /api/public/keirin/odds` (bmed-public) → 동일 결과인지
6. [ ] **대시보드 노출**: `GET /api/public/multi/dashboard` → 경륜 카드(`sport:"cycle"`)가 `cards[]`·`bySport.cycle` 에 뜨는지

## 2. 일본경마(지방 NAR) 테스트 순서
1. [ ] **개최 확인**: `GET /api/keiba/schedule?raceDy=YYYYMMDD` → `tracks[]` 에 오늘 개최장·`opTrackCd`/`sponsorCd`
2. [ ] **현재 경주 추종**: `GET /api/keiba/current?raceKey=소노다 11경주` → `currentRace`·`changed`
3. [ ] **출주표(전적) 수집**: `POST /api/keiba/starters` `{raceKey,raceNb,withDetail:true}`
   - 확인: `horses[]` 에 각질·거리·상3F 포함 / `linkedRaceKey` 반환
4. [ ] **배당 수집**: `GET /api/keiba/odds?raceKey=...` (또는 POST)
   - 확인: 발매 중이면 `quinella`/`exacta` 채워짐 · `expected` 조합수 일치
   - 발매 전/마감 후면 `waiting:true` (정상 — 실배당 대기)
   - `warning` 필드(조합 수 불일치) 없는지
5. [ ] **분석 확인**: `POST /api/odds/triple/analyze` `{raceKey}` → 유력마·추천·이상감지
6. [ ] **공개 프록시 경유**: `GET /api/public/keiba/odds?raceKey=...` → 동일 결과인지
7. [ ] **대시보드 노출**: `GET /api/public/multi/dashboard` → 일본경마 카드(`sport:"horse"`)가 뜨는지

## 3. 확인해야 할 화면 목록
- [ ] **분석기 웹**(`127.0.0.1:8011`) — 일본경마 탭: 유력마/제거마·이상감지·복승/삼복승 추천 표시
- [ ] **분석기 웹** — 통계 탭: 패턴 신뢰도(방금 고친 `_pattern_confidence`)·적중률 카드 정상
- [ ] **분석기 웹** — 다중경주 대시보드: 경륜+일본경마 카드 카운트다운·종목 토글(bySport)
- [ ] **오버레이/타이머**: 배당판 상단 카운트다운, "🎯 지금 사세요!"(복승2+삼복승2)
- [ ] **bmed-public**(`127.0.0.1:8012`) — 오늘 경주 목록/현황이 프록시로 뜨는지
- [ ] **결과기록 탭** — 경주 종료 후 결과 입력 → 리포트 "경기 전 추천마"가 **유력마만** 표시(입상마 혼입 없음)

## 4. 오류 발생 시 대처법
| 증상 | 원인 후보 | 대처 |
|---|---|---|
| `upstream_ok:false` / `503 분석 서버 연결 불가` | 기존 서버(8011) 미실행·방화벽 | `python app.py` 재확인, `BMED_UPSTREAM` 값 확인 |
| 경륜 `card` 422 "선수 정보 못 찾음" | joCode/개최일/경주번호 오류 | oddspark URL 직접 열어 파라미터 재확인, `{url}` 통째 전달 |
| keiba `odds` `waiting:true` 반복 | 발매 전·마감 후·비개최 | 발매 시간대 확인, `schedule` 로 개최 여부 확인 |
| keiba `422 개최 목록에 없음` | 경마장명 매칭 실패 | `schedule` 의 `tracks[].venue` 명칭으로 raceKey 맞추기, `opTrackCd`/`sponsorCd` 직접 지정 |
| keiba `odds` `warning`(조합수 불일치) | 배당 매트릭스 일부 파싱 누락 | 재수집(폴링) 1~2회 후 재확인, 지속 시 로그 확인 |
| 대시보드에 경륜·경마 카드 안 뜸 | 30분+ 미갱신(stale)·수집 안 됨 | 해당 경주 배당 재수집, 카드는 발주 10분 전부터 표시 |
| CORS 오류(브라우저 콘솔) | 오리진 미허용 | 허용 오리진(`localhost:8012`·ngrok·Railway) 확인 — `PUBLIC_API.md` CORS 표 |
| 카카오 로그인 "설정 오류(Client Secret)" | `.env` `KAKAO_CLIENT_SECRET` 비어있음 | 카카오 콘솔 보안>Client Secret 값을 `.env` 에 입력 |
| `[패턴매칭] 실패` 로그 | (수정됨) 재발 시 함수 시그니처 | 최신 코드(master) 반영 확인 |

### 로그 보는 법 (Windows)
```bash
# 이모지·UTF-8 로그 깨짐 방지
PYTHONIOENCODING=utf-8 python app.py
```
- 서버 콘솔에서 `[경륜 배당]`·`[경마 배당]`·`[다중경주]` 프리픽스로 수집 상황 추적.
- bmed-public 콘솔에서 `[카카오 콜백]`·`[스케줄러]` 로그 확인.

## 5. 빠른 점검 스니펫 (curl)
```bash
# 기존 서버 상태
curl http://127.0.0.1:8011/api/health
# 공개 서버 상태(업스트림 연결 포함)
curl http://127.0.0.1:8012/health
# 오늘 지방경마 개최장
curl "http://127.0.0.1:8012/api/public/keiba/schedule"
# 다중경주 대시보드(경륜+일본경마)
curl "http://127.0.0.1:8012/api/public/multi/dashboard"
```
