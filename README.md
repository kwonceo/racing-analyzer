# 경마 BMED 분석 서버 v3.0 (Vision)

KRA 출주표는 한글이 **벡터 외곽선**으로 그려져 `pdftotext`로 텍스트 추출이 불가(한글 0자).
→ 브라우저(PDF.js)가 각 페이지를 **PNG로 렌더**해 보내고, 서버가 **Claude Vision**으로 판독한다.
API 키는 서버 `.env`에만 두고 브라우저로 노출하지 않는다.

## 보존된 베팅 로직 (v2.0 → v3.0)
- 말 4마리 **A/B/C/D 등급**, 투자 비중 **45:28:17:10** (`grade_picks`)
- **2착 패턴**(현 거리·코스 2착 2회↑) → 삼복승 필수 포함 (`pattern2_horses`)
- **배당 급락마** → 삼복승 3착 보험픽 추가, 기존 복승 유지
- 출력: `grade_picks` / `betting_recommend.quinella(복승)` / `trifecta(삼복승)` / `pattern2_horses` / `analysis`
- **복승 + 삼복승만**, 단승 없음

## 구조
```
경마분석서버/
├── app.py              Flask: 정적 서빙 + /api/* (Vision 추출·분석)
├── .env                ANTHROPIC_API_KEY=...   (git 제외)
├── requirements.txt
└── static/             프론트엔드
    ├── index.html      탭(한국/일본/결과/통계)
    ├── css/style.css
    ├── data/jockeys.json
    └── js/  jockey · pdf-parser(페이지 렌더) · analysis(백엔드 클라이언트) · history · app
```

## API
| 메서드 | 경로 | 입력 | 출력 |
|---|---|---|---|
| GET | `/` | — | index.html |
| GET | `/api/health` | — | `{ok, model, has_key}` |
| POST | `/api/extract/jockey` | `{image:{media_type,data}}` | `{jockeys:[...]}` |
| POST | `/api/extract/race` | `{image:{media_type,data}}` | `{raceNo,raceTitle,horses:[...]}` |
| POST | `/api/extract/training` | `{image}` | `{horses:[{horseNum,rating,trainer,mark}]}` |
| POST | `/api/extract/results` | `{image}` | `{results:[{venue,raceNo,placing}]}` |
| POST | `/api/detect` | `{images:[...]}` | `{pages:[{index,type,venue,raceNo,...}]}` |
| POST | `/api/analyze` | `{raceData, jockeyStats}` | 분석+베팅 |
| POST | `/api/analyze/japan` | `{oddsImage?, formImage?}` | 분석+베팅 |
| POST | `/api/analyze/odds` | `{image}` | 배당판 Vision 판독(단일 스냅샷) |

## 배당 이상감지 엔진 (Phase 2)
마감 전 배당을 **수동 시계열**로 누적(`odds_store.json`)해, 두 축을 합친 **이상 신호 점수**(0–100, 50=중립)를 산출한다. 사진 한 장만 읽는 `/api/analyze/odds`(Vision)와 달리 **시간축 변화**를 정량화한다.
- **급변(Drop)** — 마감 직전 배당 급락 = 큰돈 유입(스마트머니) 신호 (가중 0.5)
- **괴리(Edge)** — `BMED 기대확률 − 시장 내재확률`(부킹마진 제거 정규화). 양수=저평가마, 음수=과대평가 (가중 0.5)
- 시계열이 1회뿐이면 드롭을 못 구하므로 자동으로 괴리 100% 가중 폴백
- 태그: 🔥스마트머니 / 💎저평가마 / 📈급락 / ⚠️과대평가 / 📉자금이탈

| 메서드 | 경로 | 입력 | 출력 |
|---|---|---|---|
| POST | `/api/odds/snapshot` | `{raceKey, odds:{마번:배당}}` | `{snaps, series}` |
| POST | `/api/odds/compute` | `{raceKey, horses:[{no,name,score}]}` | `{horses:[{signalScore,tags,drop,edge,...}], bets}` |
| POST | `/api/odds/race` | `{raceKey}` | `{snaps, series}` (저장된 시계열) |
| POST | `/api/odds/undo` | `{raceKey}` | `{ok}` (직전 스냅샷 취소) |
| POST | `/api/odds/clear` | `{raceKey}` | `{ok}` (경주 시계열 초기화) |

**프론트엔드 흐름 (배당판 캡처 탭)**: 화면 캡처/크롭 → 복승/쌍승 선택 → `[1차 캡처·10분전]`/`[2차 캡처·1분30초전]`로 두 시점 저장(Vision으로 마번별 배당 추출 후 `/api/odds/snapshot`) → `[이상감지 분석]`(`/api/odds/compute`) → 🔴🟠🟡🟢 신호·태그·보정 추천 표시. 복승/쌍승 매트릭스는 각 말이 낀 최소 조합배당을 대표 배당으로 사용.

## 전적 분석 엔진 (Phase 3)
출전마 전적·기수·마체중·이상감지를 종합해 **마필 점수와 A/B/C/D 등급을 자동 계산**한다. 한국경마 분석 시 출전표에서 `recentPlacings`(최근 착순)를 함께 추출해 `/api/score`로 점수화하고, 리포트 하단 **전적 자동 점수·등급** 패널에 표시한다.
- **3-1 기본 점수** — 최근 5경주 착순 가중평균(1=100·2=75·3=50·4=25·5↓=0 / 가중치 40·30·20·5·5%, 5경주 미만은 재정규화)
- **3-2 코스 적성** — 현재 거리 ±100m 경험 +10 · 코스(내/외) 일치 +10 · 조건(급) 일치 +5
- **3-3 기수 보너스** — 기수 3개월 복승률 상위 20% +15 · 기수-마필 직전 적중(같은 기수·3착 이내) +10
- **3-4 특수 플래그** — 동일거리 2착 2회↑(삼복승 필수) · 마체중 ±10kg↑(경고) · 출전간격 3주↑(주의) · 연속출전(피로도)
- **3-5 등급** — 총점 사분위로 A(상위25%)/B/C/D. 🔴 이상감지(신호≥75)는 1단계 상향, 급락 50%↑는 D등급도 삼복승 보험 강제

| 메서드 | 경로 | 입력 | 출력 |
|---|---|---|---|
| POST | `/api/score/form` | `{horses:[{recentPlacings}]}` | 기본 점수만(3-1) |
| POST | `/api/score` | `{race:{distance,course,grade}, horses:[{recentPlacings,pastRaces,jockey3mPlaceRate,currentWeight,lastWeight,daysSinceLast,consecutiveRuns,anomaly}]}` | 마필별 base/course/jockey/총점 + grade + flags |

## 통합 분석 엔진 (Phase 4)
전적 점수(Phase 3)와 배당 이상감지(Phase 2)를 **한 번의 호출로 결합**해 최종 등급·베팅을 만든다. **전적 총점을 배당 괴리(edge) 계산의 실력 확률로 사용**한 뒤, 이상감지로 등급을 재보정한다. 프론트 **🎯 통합분석 탭**에서 PDF 분석 경주 + 배당판 캡처(같은 경주명) + 예산 → 등급 카드·베팅 추천·이상감지 요약을 한 화면에 표시.
- **4-1 데이터 통합** — `POST /api/analyze/combined`: `{raceKey, race, horses(전적), oddsSnapshots?, budget}`
- **4-2 등급 보정** — 🔴 신호≥75 1단계 상향 · 🔴 급락50%+ 삼복승 보험강제 · 🟡 급락30~50% 보험추가 · 🔴 쌍승 역전(A·B 순서 교체 검토)
- **4-3 베팅 조합** — 복승 A+B 43% · A+C 20% · 삼복승 A+B+C 29% · A+B+보험 8% (합 100%, **단승 없음**). 예산 입력 시 금액·손익분기(예산÷베팅액)·기대값(EV) 자동 계산. A/B/C는 등급→총점 순 상위 3두로 위치 선정(빈 등급에도 성립)

## 결과기록 + 누적 통계 (Phase 5)
결과 입력 → 통계 자동 업데이트의 순환을 완성한다.
- **5-1 경주 결과 기록** — 결과기록 탭에서 경주별 날짜·투자금액·1·2·3착·수익금액 입력 → 통합분석(Phase 4) 추천 조합과 비교해 적중 자동 판정 → 학습 DB 저장. 이상감지 여부·추천 공정배당(recOdds)도 함께 기록
- **5-2 누적 통계 대시보드** — 통계 탭: ① 기본(총경주/적중/적중률/총투자/총수익/순손익/ROI) ② **이상감지 효과 검증**(🔴 있을 때 vs 🟢 없을 때 적중률 비교) ③ **배당대별 적중률**(3배 미만/3~10/10~30/30+) ④ **월별 손익 막대 그래프**
- **5-3 당일 전체 일괄 입력** — 결과 이미지 업로드 → Vision(`/api/extract/results`)으로 경주별 착순 자동 파싱 → 분석 경주와 매칭, 경주별 투자/수익 입력 시 **당일 전체 손익 자동 계산** 후 일괄 저장

## 전적+배당 복합 제거 엔진 (Phase 6)
출전마를 **배당 점수 + 전적 보정**으로 합산해 제거/후보로 자동 분류한다(`_triple_analyze` 응답의 `elimination`). 배당판 캡처 탭 [🚨 이상감지 분석] 결과에 **🧮 제거 분석** 패널로 표시된다.
- **배당 점수**(대표 복승배당 = 각 말이 낀 최저 조합 배당) — 150배+ 0 / 80~149 20 / 50~79 40 / 30~49 60 / 30배 미만 100
- **전적 보정**(출마표2·KRA 전적 총점, Phase 3) — 0~20점 −30 / 21~40 −10 / 41~60 0 / 61~80 +10 / 81+ +20 (전적 없으면 0)
- **합산 판정** — 71+ 🟢 유력 / 51~70 🟡 관찰 / 31~50 🟠 제거 권장 / ~30 🔴 확실 제거
- **후보 세부** — ⭐강력유력(배당낮음+전적우수+이상감지) / ★유력(배당낮음+전적우수) / △관찰
- **이변 신호 제거 취소** — 제거 대상이라도 **배당 급락 30%+ 또는 쌍승 상위 10위**면 "⚠️ 제거 대상이나 이변 신호"로 후보 유지
- **UI** — `출전 N두 → 후보 M두 압축`, 🟢후보/🔴제거 목록, **말 클릭으로 제거↔후보 수동 전환**(경주 바뀌면 초기화), 후보 기준 복승/삼복승 자동 조합 생성

전적 소스: **일본** = Chrome 확장 출마표2(`collectStartersByTab` → `/api/extract/japan`) · **한국** = KRA 과거기록(`GET|POST /api/kra/horse?name=마명` → `records/placeRate/recentPlacings`, PDF 분석 시 마명 자동 매칭).

## 실행
```bash
# 1) 키 설정: .env 의 ANTHROPIC_API_KEY= 뒤에 키 입력
# 2) 의존성
pip install -r requirements.txt
# 3) 실행
python app.py            # http://127.0.0.1:8011
```

## 한국경마 사용 흐름
1. PDF 업로드 → "N페이지 감지"
2. 기수현황표 페이지(기본 `4-5`), 경주표 시작(`8`)/끝 설정 → **분석 시작**
3. 각 페이지 렌더 → 서버 Vision 추출 → 기수DB/경주 병합 → 경주 칩
4. 칩 클릭 → BMED 분석 + 등급/복승/삼복승 리포트
5. 결과입력 탭에서 실제 착순 → 학습 DB → 통계

## 주의
- Vision 호출은 페이지당 1회. 비용 줄이려면 경주표 끝 페이지를 좁혀 1~2경주만 먼저 테스트.
- 조밀한 표가 흐려 추출이 부정확하면, 페이지를 상/하 분할해 보내는 보강이 다음 단계.

## 🏁 실전 운영 체크리스트 (Race Day)
경주 1건당 순서. 배당판은 **마감 전 2회**(드롭 감지용), 전적표는 1회.

| 시점 | 할 일 | 방법 |
|---|---|---|
| **경주 시작 ~10분 전** | **1차 배당판 캡처** | 🇯🇵 일본경마 탭에서 배당판 화면을 띄우고 **`Alt`+`C`** (첫 1회만 화면공유 허용) |
| **경주 시작 ~1분 30초 전** | **2차 배당판 캡처** | 같은 화면에서 다시 **`Alt`+`C`** → 1차 대비 급락 자동 비교 |
| 캡처 후 | **전적표 업로드** | 같은 탭 [📁 전적표 업로드] (배당판은 유지됨) |
| 둘 다 준비되면 | **통합 분석** | [분석 시작] → 등급 카드 + 베팅 추천(복승/삼복승) + 이상감지 요약 |
| **경주 종료 후** | **결과 입력** | 📝 결과기록 탭 → 날짜·투자금액·1·2·3착·수익금액 → 적중 자동 판정 |
| 누적 확인 | **통계** | 📊 통계 탭 → 적중률·ROI·이상감지효과·배당대·월별 자동 갱신 |

> 팁: 한국경마(PDF)는 미리 분석해 두면, 통합분석 탭에서 그 경주를 골라 배당 캡처와 합쳐 등급·베팅을 산출합니다.

## 🧪 오프라인 테스트 (실제 경주 없이 전체 흐름 검증)
실제 이미지/Vision 없이 픽스처 데이터로 전 흐름을 점검합니다.
```bash
python app.py                 # (다른 터미널에서) 서버 실행
python tests/run_flow.py      # 1·2단계: 엔드포인트 응답 + 이상감지→등급→베팅 (오오이 2R 픽스처)
node   tests/run_stats.js     # 3단계: 결과기록 → 적중 판정 → 통계
```
- 픽스처: `tests/fixtures/ooi_r2.json` (오오이 2R — 단승 1번 4.9·4번 2.4·6번 4.8, 복승 4-6 9.6)
- 기대 결과: 4번 A등급 · 4·6 급락 이상감지 · 복승 4-6 추천 · 가상결과 4-6-1 적중 → ROI +312.8%

## 📡 KRA 공공데이터 연동 (data.go.kr)

한국마사회 공식 데이터로 **과거 경주기록·현직기수 실적**을 수집해 분석에 반영합니다.

**1) API 키 준비** — [공공데이터포털](https://www.data.go.kr)에서 활용신청 후 인증키 발급
- `한국마사회_경주별상세성적표`, `한국마사회_현직기수정보` (+ 선택: AI학습용 경주결과)
- 키 저장: 웹 상단 **KRA API 키** 입력란 → `키 저장` (→ `data/kra_key.txt`, gitignore됨)

**2) 데이터 수집** (`tools/fetch_kra.py`)
```bash
python tools/fetch_kra.py --from 20260101 --to 20260131          # 기간 경주성적 → data/kra_history.json 누적
python tools/fetch_kra.py --jockeys                               # 현직기수 → static/data/jockeys.json 실제 복승률 갱신
python tools/fetch_kra.py --from 20260601 --to 20260630 --jockeys --meet 1   # 서울, 둘 다
```
- 엔드포인트: `getracedetailresult`(성적) · `getcurrentjockeyinfo`(기수). AI API는 신청 페이지 요청주소로 `EP_AI` 교체 후 사용.

**3) 활용**
- **기수 실제 복승률**: `jockeys.json`이 갱신되면 기존 분석 파이프라인이 자동 반영
- **마필 과거기록 자동매칭**: `POST /api/kra/horse {name}` → `{records, starts, wins, places, placeRate}`
