# 공개 서비스 연동 API 문서

경마 BMED 분석기 서버(Flask, 기본 `127.0.0.1:8011` · 배포 시 `PORT` 주입)의 공개 연동용 API 정리.
bmed-public(공개 프론트) 연동 기준. 응답은 모두 `application/json`.

## CORS 허용 오리진
`/api/*` 요청은 아래 오리진에서만 교차출처 허용(그 외 Origin 은 CORS 헤더 미부여 → 브라우저 차단).

| 종류 | 값 |
|---|---|
| bmed-public Railway | `https://web-production-d4723.up.railway.app` |
| 로컬 공개 프론트 | `http://localhost:8012` · `http://127.0.0.1:8012` |
| 자기 자신 | `http://localhost:8011` · `http://127.0.0.1:8011` |
| ngrok 와일드카드 | `*.ngrok.io` · `*.ngrok-free.app` · `*.ngrok.app` |
| Railway 서브도메인 | `*.up.railway.app` |

- 구현: `flask_cors` 사용(미설치 시 수동 `after_request` 폴백 — 서버 기동 보장).
- 허용 메서드: `GET, POST, OPTIONS` / 허용 헤더: `Content-Type, Authorization`.
- 현재 상태는 `/api/health` 의 `cors` 필드(`flask_cors` | `manual`)로 확인.

## 접근 등급(향후 인증) 구조
현재 **전부 오픈**. `PREMIUM_ENFORCED=True` 로 승격 시에만 premium 엔드포인트가 인증(`Authorization` 헤더) 요구(없으면 402).

- **공개(로그인 불필요)**: 오늘 경주 목록·현황·결과·최신 배당·상태·통계
- **프리미엄(향후 인증)**: BMED 신호 상세(`/api/odds/signal-timeline`)·복승/삼복승 추천 + 분석(`/api/odds/triple/analyze`)
- 등급표 조회: `GET /api/access/policy` → `{enforced, public[], premium[], note}`

---

## 엔드포인트별 응답 형식

### GET `/api/health` — 서버 상태 (공개)
외부 모니터링용. 민감정보 미포함(내부 경로·키 원문·개인설정 제외).
```json
{ "status": "ok", "version": "2.3.0", "ok": true,
  "model": "claude-sonnet-4-6", "has_key": true,
  "cors": "flask_cors", "pdf_ready": true }
```

### GET `/api/access/policy` — 접근 등급표 (공개)
```json
{ "enforced": false,
  "public":  ["/api/health", "/api/multi/schedule", "..."],
  "premium": ["/api/odds/triple/analyze", "/api/odds/signal-timeline"],
  "note": "현재 전부 오픈. premium 은 향후 인증 뒤로 이동 예정." }
```

### GET `/api/multi/schedule` — 오늘 경주 목록 (공개)
```json
{ "tracks": [ { "venue": "...", "races": [ ... ] } ],
  "updated": 1720000000.0, "ymd": "20260712" }
```
- `tracks`: 경마장별 경주 스케줄 배열 / `updated`: 갱신 epoch초 / `ymd`: 날짜.

### GET `/api/multi/dashboard` — 전체 경주 현황 (공개)
```json
{ "cards": [ { "raceKey": "...", "sport": "...", "deadline": ..., "..." } ],
  "bySport": { "horse": ..., "cycle": ..., "boat": ... },
  "count": 12, "collected": 8, "urgent": [ ... ] }
```
- `cards`: 경주 카드 배열(경주별 요약) / `bySport`: 종목별 집계 / `urgent`: 마감임박 경주.

### GET `/api/odds/triple/latest` — 최신 배당 (공개)
```json
{ "raceKey": "...", "quinella": [ ... ], "exacta": [ ... ], "trio": [ ... ],
  "ageSeconds": 30, "stale": false }
```
- `quinella`(복승)·`exacta`(쌍승)·`trio`(삼복승) 배당 배열 / `ageSeconds`: 수집 경과초 / `stale`: 30분+ 미갱신 여부. 데이터 없으면 `{matched:false, reason, candidates}`.

### GET `/api/learning/stats` — 학습 통계 (공개)
```json
{ "count": 123, "stats": { "...": ... } }
```
- `count`: 학습 표본 수 / `stats`: 급락·역전·불일치·적중률 등 통계 dict.

### GET/POST `/api/odds/signal-timeline` — 신호 타임라인 (⚑ 향후 프리미엄)
```json
{ "raceKey": "...", "timeline": [ { "time": "...", "signal_horse": ..., "..." } ] }
```
- `timeline`: 신호말 변경 이력·안정화·유효시점. 데이터 없으면 `timeline: null`.

### GET/POST `/api/odds/triple/analyze` — 분석 결과 (⚑ 향후 프리미엄)
복승/삼복승 추천 + BMED 신호 상세. 주요 필드:
```json
{ "raceKey": "...", "betRecommend": [ ... ], "bmed": { "strategy": "..." },
  "signalQuality": { ... }, "corePicks": { "finalQuinellas": [...], "finalTrifectas": [...] },
  "alertSignal": ..., "compareRecommend": { ... }, "afterClose": false,
  "category": "...", "chart": { ... } }
```
- `betRecommend`: 추천 조합 / `corePicks.finalQuinellas`·`finalTrifectas`: 최종 4개 추천 / `bmed`: 전략 / `signalQuality`: 신호 품질.

---

## 응답 데이터 최적화(민감정보 제거)
- `/api/health` 에서 `pdf_error`(내부 경로/import 메시지) 노출 제거.
- 프로덕션(`FLASK_ENV=production`)에서 500 에러는 `str(e)` 대신 `{"error":"internal_error"}` 일반 메시지(상세는 서버 로그에만). 로컬 debug 에서는 기존대로 상세 유지.

## 로컬 CORS 테스트
```bash
# localhost:8012 오리진에서 8011 호출 시 CORS 헤더 확인
curl -i -H "Origin: http://localhost:8012" http://127.0.0.1:8011/api/health | grep -i access-control
```
