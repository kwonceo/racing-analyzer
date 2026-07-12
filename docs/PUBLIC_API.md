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

---

## 경륜·일본경마·다중경주 API (내일 테스트 연동)

> 이 엔드포인트들은 모두 `/api/*` → **CORS 허용 오리진**에서 호출 가능(브라우저 직접) + **bmed-public 프록시**(`/api/public/*`) 경유 가능.
> ⚠ `keirin/keiba/odds`·`*/starters`·`multi/collect` 는 oddspark 를 **실시간 fetch**(부수효과)하므로 남용 주의.

### 경륜 (keirin)
| 엔드포인트 | 메서드 | 요청 | 응답 주요 필드 |
|---|---|---|---|
| `/api/keirin/card` (별칭 `/api/keirin/starters`) | POST | `{joCode,kaisaiBi,raceNo}` \| `{url}` \| `{html}`, `raceKey?` | `{ok, url, card, analysis, linkedRaceKey}` |
| `/api/keirin/odds` | POST | `{joCode,kaisaiBi,raceNo}`\|`{url}`, `raceKey`(필수) | `{ok, raceKey, counts{quinella,exacta}, quinella[], exacta[], ingest}` |

- `card`: `{venue, riders[], ...}` / `analysis.ranked[]`: 차번·선수명·競走得点·조정점수·각질(styleType)·등급.
- `keirin/odds`: 복승(2車複)·쌍승(2車単)을 파이프라인 주입 → 같은 raceKey로 역배열·급락 자동 계산. 삼복승은 미수집(추정보험).

### 일본경마 (keiba, 지방 NAR · oddspark)
| 엔드포인트 | 메서드 | 요청 | 응답 주요 필드 |
|---|---|---|---|
| `/api/keiba/odds` | GET/POST | `{raceKey, raceDy?, raceNo?, opTrackCd?, sponsorCd?}` | `{ok, raceKey, counts, expected, warning, track, quinella[], exacta[], ingest}` 또는 `{ok, waiting:true, reason, track, counts}` |
| `/api/keiba/current` | GET/POST | `{raceKey\|venue, raceDy?, opTrackCd?, sponsorCd?}` | `{ok, venue, currentRace, prevRace, changed, raceKey, track}` |
| `/api/keiba/schedule` | GET/POST | `{raceDy?}` | `{raceDy, tracks:[{venue, opTrackCd, sponsorCd}]}` |
| `/api/keiba/starters` | POST | `{raceKey\|venue, raceDy?, raceNb, opTrackCd?, sponsorCd?, withDetail?}` | `{ok, url, linkedRaceKey, race{venue,raceNo,distance,surface,trackCond}, horses[]}` |

- `keiba/odds` 는 `_keiba_odds_live` 로 발매 중 실배당만 주입(발매 전·마감 후 가짜값은 `waiting:true`).
- `keiba/current` 로 현재 발매중 경주번호 자동추종(경주 전환 감지 → raceKey 갱신).

### 다중경주 (multi)
| 엔드포인트 | 메서드 | 요청 | 응답 주요 필드 |
|---|---|---|---|
| `/api/multi/schedule` | GET/POST | GET=조회 / POST=강제 재수집 | `{ymd, tracks[], updated}` |
| `/api/multi/collect` | POST | (없음) | `{ok, collected[], skipped}` |
| `/api/multi/dashboard` | GET | (없음) | `{cards[], urgent[], count, collected, bySport}` |
| `/api/multi/race/<key>` | GET | 경로에 raceKey | 경주 전체 분석(_triple_analyze) |

- **경륜·일본경마 데이터 포함**: `multi/dashboard` 는 `triple_store` 를 읽기전용 병합 → **경륜·한국·일본경마 카드 통합**(경정=boat 만 제외). `cards[].sport`(horse/cycle) 로 구분, `bySport` 로 종목별 카운트.
- `multi/schedule`·`multi/collect` 는 지방경마(NAR) 스케줄 기반. **경륜 스케줄은 별도** `POST /api/multi/keirin-schedule`.

## bmed-public 프록시 매핑
| 공개(bmed-public) | 업스트림(기존 서버) |
|---|---|
| `/api/public/keirin/<sub>` | `/api/keirin/<sub>` |
| `/api/public/keiba/<sub>` | `/api/keiba/<sub>` |
| `/api/public/multi/<sub>` | `/api/multi/<sub>` |

- GET=쿼리스트링 전달 · POST=JSON 본문 전달(메서드 미러링). 예: `GET /api/public/multi/dashboard`, `POST /api/public/keirin/odds {raceKey,...}`.
