#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
경마 BMED 분석 서버 v3.0 (Vision)

KRA 출주표는 한글이 벡터 외곽선으로 그려져 pdftotext 로는 추출 불가(한글 0자).
→ 브라우저(PDF.js)가 각 페이지를 PNG 로 렌더해 보내고, 서버가 Claude Vision 으로 판독한다.
베팅 로직(A/B/C/D 45:28:17:10, 2착패턴, 배당급락)은 v2.0 에서 그대로 보존.
단승은 절대 추천하지 않고 복승(2착내) + 삼복승(3마리 3착내)만 추천.

API 키는 .env 의 ANTHROPIC_API_KEY 에만 두고 브라우저로 노출하지 않는다.

엔드포인트:
  GET  /                    → static/index.html
  GET  /api/health          → {ok, model, has_key}
  POST /api/extract/jockey  {image:{media_type,data}}     → {jockeys:[...]}
  POST /api/extract/race    {image:{media_type,data}}     → {raceNo,raceTitle,horses:[...]}
  POST /api/analyze         {raceData, jockeyStats}        → 분석+베팅
  POST /api/analyze/japan   {oddsImage?, formImage?}       → 분석+베팅
"""
import os
import re
import json
import time
from itertools import permutations
from flask import Flask, request, jsonify, send_from_directory
from werkzeug.exceptions import HTTPException
import anthropic

MODEL = "claude-sonnet-4-6"


# ---------- .env 로더 (dotenv 의존성 없이) ----------
def load_env():
    path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()

app = Flask(__name__, static_folder="static", static_url_path="")
# [1] 대용량 요청 허용 (큰 배당판 이미지/3종 데이터 업로드 시 413 방지)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB


def client(api_key=None):
    key = (api_key or os.environ.get("ANTHROPIC_API_KEY", "")).strip()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY 가 없습니다 (.env 확인 또는 요청에 api_key 포함).")
    return anthropic.Anthropic(api_key=key)


# ─────────────────────────────────────────
# 스키마 (structured outputs)
# ─────────────────────────────────────────
JOCKEY_SCHEMA = {
    "type": "object",
    "properties": {
        "jockeys": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "total": {"type": "integer"},
                    "w1": {"type": "integer"}, "w2": {"type": "integer"}, "w3": {"type": "integer"},
                    "month": {"type": "integer"},
                    "mW1": {"type": "integer"}, "mW2": {"type": "integer"}, "mW3": {"type": "integer"},
                },
                "required": ["name", "total", "w1", "w2", "w3", "month", "mW1", "mW2", "mW3"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["jockeys"],
    "additionalProperties": False,
}

RACE_SHEET_SCHEMA = {
    "type": "object",
    "properties": {
        "raceNo": {"type": "integer"},
        "raceTitle": {"type": "string"},
        "horses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "horseNum": {"type": "integer"},
                    "horseName": {"type": "string"},
                    "jockey": {"type": "string"},
                    "weight": {"type": "string"},
                    "rating": {"type": "string"},
                    "recentRecord": {"type": "string"},
                    "recentPlacings": {"type": "array", "items": {"type": "integer"},
                                       "description": "최근 경주부터 착순(정수) 배열, 최대 5. 못 읽으면 []"},
                    "health": {"type": "string"},
                    "training": {"type": "string"},
                },
                "required": ["horseNum", "horseName", "jockey", "weight", "rating",
                             "recentRecord", "recentPlacings", "health", "training"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["raceNo", "raceTitle", "horses"],
    "additionalProperties": False,
}

RESULTS_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "venue": {"type": "string"},
                    "raceNo": {"type": "integer"},
                    "placing": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["venue", "raceNo", "placing"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}

DETECT_SCHEMA = {
    "type": "object",
    "properties": {
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "type": {"type": "string", "enum": ["race", "jockey", "other"]},
                    "venue": {"type": "string", "enum": ["서울", "부산", "기타", ""]},
                    "raceNo": {"type": "integer"},
                    "distance": {"type": "string"},
                    "layout": {"type": "string", "enum": ["summary", "detail", ""]},
                },
                "required": ["index", "type", "venue", "raceNo", "distance", "layout"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["pages"],
    "additionalProperties": False,
}

TRAINING_SCHEMA = {
    "type": "object",
    "properties": {
        "horses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "horseNum": {"type": "integer"},
                    "rating": {"type": "string"},
                    "trainer": {"type": "string"},
                    "mark": {"type": "string"},
                },
                "required": ["horseNum", "rating", "trainer", "mark"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["horses"],
    "additionalProperties": False,
}

_PICK = {
    "type": "object",
    "properties": {
        "no": {"type": "integer"},
        "name": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["no", "name", "reason"],
    "additionalProperties": False,
}

_BETLINE = {
    "type": "object",
    "properties": {
        "combo": {"type": "array", "items": {"type": "integer"}},
        "confidence": {"type": "integer"},
        "note": {"type": "string"},
    },
    "required": ["combo", "confidence", "note"],
    "additionalProperties": False,
}

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "race_summary": {"type": "string"},
        "horses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "no": {"type": "integer"},
                    "name": {"type": "string"},
                    "jockey": {"type": "string"},
                    "grade": {"type": "string", "enum": ["A", "B", "C", "D", ""]},
                    "score": {"type": "integer"},
                    "reason": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["no", "name", "jockey", "grade", "score", "reason", "evidence"],
                "additionalProperties": False,
            },
        },
        "grade_picks": {
            "type": "object",
            "properties": {"A": _PICK, "B": _PICK, "C": _PICK, "D": _PICK},
            "required": ["A", "B", "C", "D"],
            "additionalProperties": False,
        },
        "pattern2_horses": {"type": "array", "items": {"type": "string"}},
        "special_notes": {"type": "string"},
        "betting_recommend": {
            "type": "object",
            "properties": {
                # quinella = 복승(2마리), trifecta = 삼복승(3마리). combo는 실제 마번(정수).
                "quinella": {"type": "array", "items": _BETLINE},
                "trifecta": {"type": "array", "items": _BETLINE},
            },
            "required": ["quinella", "trifecta"],
            "additionalProperties": False,
        },
        "analysis": {"type": "string"},
    },
    "required": ["race_summary", "horses", "grade_picks", "pattern2_horses",
                 "special_notes", "betting_recommend", "analysis"],
    "additionalProperties": False,
}

ODDS_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "betTypes": {"type": "array", "items": {"type": "string"}},
        "alerts": {"type": "array", "items": {"type": "string"}},
        "horses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "no": {"type": "integer"},
                    "odds": {"type": "string"},
                    "trend": {"type": "string", "enum": ["급락", "급등", "안정", ""]},
                    "abnormal": {"type": "boolean"},
                    "note": {"type": "string"},
                },
                "required": ["no", "odds", "trend", "abnormal", "note"],
                "additionalProperties": False,
            },
        },
        "combos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "combo": {"type": "array", "items": {"type": "integer"}},
                    "odds": {"type": "string"},
                    "abnormal": {"type": "boolean"},
                    "note": {"type": "string"},
                },
                "required": ["type", "combo", "odds", "abnormal", "note"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "betTypes", "alerts", "horses", "combos"],
    "additionalProperties": False,
}

# 일본경마 병합 분석(전적표+배당판) 스키마
JAPAN_SCHEMA = {
    "type": "object",
    "properties": {
        "mismatch": {"type": "boolean"},
        "mismatchNote": {"type": "string"},
        "raceSummary": {"type": "string"},
        "oddsAlerts": {"type": "array", "items": {"type": "string"}},
        "horses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "no": {"type": "integer"},
                    "name": {"type": "string"},
                    "jockey": {"type": "string"},
                    "grade": {"type": "string", "enum": ["A", "B", "C", "D", ""]},
                    "score": {"type": "integer"},
                    "odds": {"type": "string"},
                    "abnormal": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["no", "name", "jockey", "grade", "score", "odds", "abnormal", "reason"],
                "additionalProperties": False,
            },
        },
        "grade_picks": {
            "type": "object",
            "properties": {"A": _PICK, "B": _PICK, "C": _PICK, "D": _PICK},
            "required": ["A", "B", "C", "D"],
            "additionalProperties": False,
        },
        "betting_recommend": {
            "type": "object",
            "properties": {
                "quinella": {"type": "array", "items": _BETLINE},
                "trifecta": {"type": "array", "items": _BETLINE},
            },
            "required": ["quinella", "trifecta"],
            "additionalProperties": False,
        },
        "analysis": {"type": "string"},
    },
    "required": ["mismatch", "mismatchNote", "raceSummary", "oddsAlerts",
                 "horses", "grade_picks", "betting_recommend", "analysis"],
    "additionalProperties": False,
}

# ─────────────────────────────────────────
# 분석 지침 (BMED + v2.0 베팅 규칙)
# ─────────────────────────────────────────
ANALYSIS_GUIDE = """당신은 한국마사회(KRA) 전문 경마 분석가입니다.
BMED = Blood(혈통/전적)·Mount(기수)·Energy(컨디션/체중)·Dividend(배당 흐름).

## 베팅 규칙 (반드시 준수)
1. 2착 패턴: 현재 거리·코스에서 2착 2회 이상인 말 → 삼복승에 필수 포함. 해당 마명을 pattern2_horses에 기록.
2. 배당 급락: 마감 전 배당이 급락한 말 → 삼복승 3착 보험픽으로 추가(기존 복승 조합은 유지).
3. 4등급 분류: A(유력)·B(차순위)·C(복병)·D(보험), 투자 비중 45:28:17:10. grade_picks에 각 등급의 마번/마명/이유 기재. horses[].grade에도 등급 표기.
4. 베팅 종류는 복승(2착 이내 2마리)과 삼복승(3마리 모두 3착 이내)만. 단승은 절대 추천 금지.
5. betting_recommend.quinella(복승)·trifecta(삼복승)에 실제 마번(정수) 조합으로 제시: 복승 combo 길이 2, 삼복승 길이 3. 각 조합에 confidence(0-100)와 note(근거). 2착패턴마는 삼복승에 필수 포함, 배당 급락마는 삼복승 보험픽으로 추가하되 기존 복승 조합은 유지.

## 평가
각 말을 0-100점(score)으로 평가하고, reason과 evidence(전적/레이팅/기수통계 발췌)를 채우세요.
점수·등급·2착패턴·배당을 종합해 기대값 높은 복승/삼복승 조합을 bets로 제시하세요.
모든 출력은 JSON 스키마를 따르세요."""


# ─────────────────────────────────────────
# Claude 호출
# ─────────────────────────────────────────
def call_claude(content, schema, max_tokens=4096, api_key=None):
    msg = client(api_key).messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": content}],
        extra_body={"output_config": {"format": {"type": "json_schema", "schema": schema}}},
    )
    if getattr(msg, "stop_reason", None) == "refusal":
        raise RuntimeError("AI가 분석을 거부했습니다.")
    text = next((b.text for b in msg.content if getattr(b, "type", None) == "text"), None)
    if not text:
        raise RuntimeError("빈 응답")
    # [4] JSON 파싱 실패를 명확한 메시지로 (특히 max_tokens 초과로 잘린 경우)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        if getattr(msg, "stop_reason", None) == "max_tokens":
            raise RuntimeError(
                f"AI 응답이 max_tokens({max_tokens})를 초과해 JSON이 중간에 잘렸습니다. "
                f"조합이 많은 배당판입니다 — 상위 인기 조합만 분석하도록 조정했습니다. (파싱 위치 {e.pos})")
        raise RuntimeError(f"AI 응답 JSON 파싱 실패: {e} (길이 {len(text)}자)")


def image_block(img):
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": (img or {}).get("media_type", "image/png"),
            "data": (img or {})["data"],
        },
    }


# ─────────────────────────────────────────
# 라우트
# ─────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "model": MODEL,
                    "has_key": bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())})


@app.route("/api/extract/jockey", methods=["POST"])
def extract_jockey():
    body = request.json or {}
    prompt = (
        '이 이미지는 KRA 출주표의 "기수 기승현황표"입니다.\n'
        "표의 모든 기수 행을 읽어 JSON으로 추출하세요.\n"
        "각 기수: name(기수명), total(누적 총 기승수), w1/w2/w3(누적 1/2/3착), "
        "month(당월 기승수), mW1/mW2/mW3(당월 1/2/3착).\n"
        "숫자를 못 읽으면 0. 기수현황표가 아니면 jockeys를 빈 배열로."
    )
    out = call_claude([{"type": "text", "text": prompt}, image_block(body.get("image"))],
                      JOCKEY_SCHEMA, 8192, body.get("api_key"))
    return jsonify(out)


@app.route("/api/extract/race", methods=["POST"])
def extract_race():
    body = request.json or {}
    prompt = (
        "이 이미지는 한국마사회(KRA) 출주표의 한 경주 출전표입니다. 표의 각 행이 한 마리입니다.\n"
        "KRA 출주표의 칸 위치를 정확히 구분해 추출하세요(필드를 섞지 마세요):\n"
        "- horseNum: 맨 왼쪽 마번(1~N).\n"
        "- horseName: 마명. 마명 칸이 축약돼 있으면 같은 페이지의 다른 위치(상단 요약/하단 상세)에 나온 전체 마명을 우선 사용.\n"
        "- jockey: '금일기수' 칸의 사람 이름(예: 르칸, 승완, 인철, 동하). "
        "주의: 아래쪽 '조교훈련/종합분석' 표의 조교사(調教師) 이름을 기수로 쓰지 마세요 — 둘은 다른 칸입니다.\n"
        "- weight: 부담중량(kg 단위 숫자, 보통 50~57). 레이팅 값(예 38)이나 조(組) 번호와 혼동하지 마세요.\n"
        "- rating: 별도 '레이팅' 칸의 숫자만. 주의: 마명 옆 괄호 안 숫자(예 '3(40)', '6(76)')는 산지/성별/"
        "나이(개월)이므로 절대 레이팅으로 쓰지 마세요. 이 영역에 레이팅 칸이 안 보이면 rating=''.\n"
        "- recentRecord: 최근/최종 출전기록(날짜+구간기록). 기록이 없으면 '해당거리 첫 출주'.\n"
        "- recentPlacings: 최근 경주부터(가장 최근이 맨 앞) 착순만 정수로 모은 배열, 최대 5개. "
        "예 최근 3·1·5착 → [3,1,5]. 못 읽으면 [].\n"
        "- health: 마체중/건강 메모가 있으면, 없으면 ''.\n"
        "- training: 조교 메모(예 '주행미합','연습주행')가 있으면, 없으면 ''.\n"
        "raceNo(경주 번호, 모르면 0), raceTitle(예 '제1경주', 모르면 '').\n"
        "마번 1번부터 마지막까지 한 마리도 빠짐없이. 출전표가 아니면 horses를 빈 배열로."
    )
    out = call_claude([{"type": "text", "text": prompt}, image_block(body.get("image"))],
                      RACE_SHEET_SCHEMA, 8192, body.get("api_key"))
    return jsonify(out)


@app.route("/api/extract/training", methods=["POST"])
def extract_training():
    """출주표 하단 '조교훈련 및 종합분석' 표 → 마번별 레이팅/조교사/평가기호"""
    body = request.json or {}
    prompt = (
        "이 이미지는 KRA 출주표 하단의 '조교훈련 및 종합분석' 표입니다. 각 행이 한 마리입니다.\n"
        "- horseNum: 마번.\n"
        "- rating: '레이팅' 칸의 숫자(예 84, 91, 107). 마명 옆 나이(개월)와 혼동 금지.\n"
        "- trainer: 조교사명.\n"
        "- mark: 행 우측 종합 평가 기호(★◎○△※ 중 하나), 없으면 ''.\n"
        "칸이 비면 ''. 모든 출전마를 마번과 함께 반환."
    )
    out = call_claude([{"type": "text", "text": prompt}, image_block(body.get("image"))],
                      TRAINING_SCHEMA, 4096, body.get("api_key"))
    return jsonify(out)


@app.route("/api/extract/results", methods=["POST"])
def extract_results():
    """경주 결과(착순)표 이미지 → 경주별 착순"""
    body = request.json or {}
    prompt = (
        "이 이미지는 경마 경주 결과(착순)표입니다. 경주별 착순을 추출하세요.\n"
        "results 각 항목: venue(경마장 '서울'/'부산'/'일본'/''), raceNo(경주 번호), "
        "placing(1착부터 순서대로 마번 정수 배열).\n"
        "결과표가 아니면 results=[]."
    )
    out = call_claude([{"type": "text", "text": prompt}, image_block(body.get("image"))],
                      RESULTS_SCHEMA, 4096, body.get("api_key"))
    return jsonify(out)


@app.route("/api/detect", methods=["POST"])
def detect():
    """여러 페이지 썸네일을 순서대로 받아 race/jockey/other 분류"""
    body = request.json or {}
    imgs = body.get("images", [])
    content = [{"type": "text", "text": (
        "여러 장의 KRA 출주표 페이지 썸네일이 '페이지 인덱스' 라벨과 함께 순서대로 주어집니다.\n"
        "각 페이지를 분류하세요:\n"
        "- type='race': 상단에 'OO경마 N경주 NM ... 일반경주(시각)' 형태의 경주 헤더가 있는 경주 페이지.\n"
        "- type='jockey': 기수 기승현황(기수별 성적) 표 페이지.\n"
        "- type='other': 그 외.\n"
        "type가 'race'이면:\n"
        "  venue: 헤더의 경마장(서울경마→'서울', 부산경마→'부산', 그 외 '기타').\n"
        "  raceNo: 경주 번호 정수. distance: 예 '1000M'.\n"
        "  layout: 'summary'=한 장에 출전마 전체가 한 표로 정리되고 상단에 예상전개도/전문위원 예상이 있는 요약 페이지, "
        "'detail'=말별 상세 카드가 격자로 배열된 페이지.\n"
        "type가 'race'가 아니면 venue='', raceNo=0, distance='', layout=''.\n"
        "입력된 모든 페이지를 빠짐없이 해당 index와 함께 pages 배열로 반환하세요."
    )}]
    for i, im in enumerate(imgs):
        content.append({"type": "text", "text": f"[페이지 인덱스 {i}]"})
        content.append(image_block(im))
    out = call_claude(content, DETECT_SCHEMA, 2048, body.get("api_key"))
    return jsonify(out)


@app.route("/api/analyze", methods=["POST"])
def analyze():
    body = request.json or {}
    race = body.get("raceData", {})
    jstats = body.get("jockeyStats", {})
    lines = []
    any_weight = False
    any_kra = False
    kra_hist = _kra_load_history()          # [KRA] 마필 과거기록 자동매칭
    rdist = _to_int(race.get("distance"))
    rtrack = (race.get("condition") or {}).get("track")
    for h in race.get("horses", []):
        j = jstats.get(h.get("jockey", ""))
        jstat = "(기수통계 없음)"
        if j:
            # [6번] 기수 거리/주로/마필 적성 자동 반영
            extra = ""
            r30 = j.get("recent30")
            if r30 and r30.get("rides"):
                extra += f", 최근30 {round(r30['places'] / r30['rides'] * 100)}%"
            bd = (j.get("byDistance") or {}).get(str(rdist)) if rdist else None
            if bd and bd.get("rides"):
                extra += f", {rdist}m {round(bd['places'] / bd['rides'] * 100)}%"
            bt = (j.get("byTrack") or {}).get(rtrack) if rtrack else None
            if bt and bt.get("rides"):
                extra += f", 주로{rtrack} {round(bt['places'] / bt['rides'] * 100)}%"
            bh = (j.get("byHorse") or {}).get(h.get("horseName"))
            if bh and bh.get("rides"):
                extra += f", 이 말과 복승권 {bh['places']}/{bh['rides']}"
            jstat = f"(승률 {j['winRate']}%, 복승권 {j['placeRate']}%, 기승 {j['rides']}{extra})"
        # [2번] 마체중 변동
        weight_note = ""
        bw = h.get("bodyWeight")
        if bw:
            any_weight = True
            d = h.get("weightDelta")
            weight_note = f" / 마체중 {bw}kg"
            if d is not None:
                flag = " 🔴위험" if abs(d) >= 20 else " 🟡경고" if abs(d) >= 10 else ""
                weight_note += f"(전회대비 {'+' if d >= 0 else ''}{d}kg{flag})"
        kra_note = kra_horse_summary(h.get("horseName"), kra_hist)   # [KRA] 실제 과거 성적
        if kra_note:
            any_kra = True
        lines.append(
            f"{h.get('horseNum')}번 {h.get('horseName')} / 기수 {h.get('jockey') or '미상'} {jstat} / "
            f"부담 {h.get('weight') or '-'} / 레이팅 {h.get('rating') or '-'} / 전적 {h.get('recentRecord') or '-'} / "
            f"상태 {h.get('health') or '-'} / 조교 {h.get('training') or '-'}{weight_note}{kra_note}"
        )
    title = race.get("raceTitle") or (f"제{race.get('raceNo')}경주" if race.get("raceNo") else "경주")
    cond = race.get("condition") or {}
    cond_line = ""
    if cond.get("track") or cond.get("weather"):
        cond_line = (f"\n\n경주 환경: 주로 {cond.get('track') or '미상'}, 날씨 {cond.get('weather') or '미상'}. "
                     "주로 상태(불량/다습 등)와 날씨가 각 말의 적성에 미치는 영향을 평가에 반영하세요.")
    weight_line = ("\n마체중: ±10kg 이상(🟡)은 컨디션 변화 신호, ±20kg 이상(🔴)은 위험 신호로 평가에 반영하세요."
                   if any_weight else "")
    kra_line = ("\nKRA전적: 한국마사회 실제 과거 성적(출전수·1-2-3착·복승권율·최근 착순)입니다. "
                "복승권율이 높고 최근 착순이 안정/상승세인 마필을 신뢰도 있게 평가에 반영하세요."
                if any_kra else "")
    prompt = (f"{ANALYSIS_GUIDE}\n\n[{title}] 출전마 정보:\n" + "\n".join(lines) + cond_line + weight_line + kra_line +
              "\n\n위 정보로 분석과 베팅 추천을 JSON으로 응답하세요.")
    out = call_claude([{"type": "text", "text": prompt}], ANALYSIS_SCHEMA, 4096, body.get("api_key"))
    return jsonify(out)


@app.route("/api/analyze/japan", methods=["POST"])
def analyze_japan():
    """일본경마: [전적표]+[배당판] 두 이미지를 한 번에 병합 분석"""
    body = request.json or {}
    guide = (
        "당신은 경마 BMED 분석 전문가입니다. 아래에 [전적표]와 [배당판] 두 이미지가 주어집니다.\n"
        "1) 전적표에서 경주 정보(경주명·거리)와 출전마(마번·마명·기수·전적)를 읽으세요.\n"
        "2) 배당판에서 마번별 배당을 읽으세요.\n"
        "3) 두 이미지가 같은 경주인지 검증: 출전 마번 구성·마릿수가 서로 다르면 mismatch=true 로 두고 "
        "mismatchNote에 '배당판과 전적표 경주가 다릅니다' 등 이유를 적으세요. 이 경우 horses/grade_picks/"
        "betting_recommend는 비워도 됩니다.\n"
        "4) 일치하면 mismatch=false. 전적과 배당을 모두 반영해 각 말을 0-100 score와 A/B/C/D 등급(투자비중 "
        "45:28:17:10)으로 평가하고, horses[].odds에 배당, abnormal에 이상배당(급락 등) 여부를 표시하세요. "
        "oddsAlerts에 이상배당 경고를 적으세요.\n"
        "5) 베팅은 복승(2착내 2마리)·삼복승(3마리 3착내)만 추천(단승 금지). betting_recommend.quinella/trifecta에 "
        "실제 마번 조합으로.\n"
        "6) 분석 결과는 반드시 한국어로 작성할 것. 마명·기수명은 원문 유지, 나머지는 전부 한국어."
    )
    content = [{"type": "text", "text": guide}]
    if body.get("formImage"):
        content += [{"type": "text", "text": "[전적표]"}, image_block(body["formImage"])]
    if body.get("oddsImage"):
        content += [{"type": "text", "text": "[배당판]"}, image_block(body["oddsImage"])]
    out = call_claude(content, JAPAN_SCHEMA, 4096, body.get("api_key"))
    return jsonify(out)


@app.route("/api/analyze/odds", methods=["POST"])
def analyze_odds():
    """배당판 이미지 → 마번별 배당 + 이상배당(급락/급등) 감지"""
    body = request.json or {}
    prompt = (
        "이 이미지는 경마 배당판입니다. 다음 순서로 정확히 분석하세요.\n\n"
        "[1단계] 배당 종류 판별 — 화면 상단/탭의 텍스트를 먼저 읽어 어떤 베팅 종류인지 확정하세요.\n"
        "한국 베팅 종류: 단승(1마리 1착), 연승(1마리 3착내), 복승(2마리 1·2착 순서무관), "
        "쌍승(2마리 1·2착 순서 일치), 복연승, 삼복승(3마리 3착내), 삼쌍승(3마리 순서).\n"
        "→ betTypes 배열에 식별된 종류를 넣으세요(예 ['복승']). 탭 텍스트가 보이면 그 텍스트를 근거로.\n\n"
        "[2단계] 배당값 파싱\n"
        "- 단승/연승(마번별 단일 배당): horses에 no(마번), odds, trend(급락/급등/안정/''), abnormal, note.\n"
        "- 복승/쌍승(2마리 조합, 행×열 매트릭스): 표의 맨 윗 행(열 헤더 마번)과 맨 왼쪽 열(행 헤더 마번)을 "
        "먼저 정확히 읽고, 각 셀을 그 교차 마번 조합으로 해석하세요. combos에 type, combo=[행마번, 열마번], "
        "odds(셀 값), abnormal, note. note에는 '행마번+열마번 복승 배당' 형태로 명시.\n"
        "- 삼복승(3마리): combos에 type='삼복승', combo=[마번3개], odds.\n"
        "★ 번호 오인식 방지: 반드시 헤더 행/열의 마번을 기준으로 인덱싱하고, 셀 위치(몇 번째 행/열)로 "
        "헤더 마번을 역추적해 조합을 구성하세요. 헤더를 못 읽으면 그 행/열은 건너뛰세요.\n\n"
        "[3단계] 이상배당 — 비정상적으로 낮은(급락) 배당은 abnormal=true. "
        "alerts에 경고(예 '3-7 복승 급락: 인기 집중'). summary는 한 줄 요약.\n\n"
        "해당 없는 배열은 비워두세요. 배당판이 아니면 betTypes=[], horses=[], combos=[], "
        "alerts=['배당판을 인식하지 못함'].\n\n"
        "[중요] 조합(복승·쌍승·삼복승)은 전체 매트릭스를 다 넣지 말고 "
        "배당이 낮은(인기) 상위 40개 조합만 combos에 넣으세요(응답 크기 제한)."
    )
    out = call_claude([{"type": "text", "text": prompt}, image_block(body.get("image"))],
                      ODDS_SCHEMA, 8192, body.get("api_key"))
    return jsonify(out)


# [3번] 복승/쌍승/삼복승 3종 동시 분석 + 불일치(이상) 감지
_TRIPLE_COMBO = {
    "type": "object",
    "properties": {
        "combo": {"type": "array", "items": {"type": "integer"}},
        "odds": {"type": "string"},
        "abnormal": {"type": "boolean"},
    },
    "required": ["combo", "odds", "abnormal"],
    "additionalProperties": False,
}
TRIPLE_ODDS_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "quinella": {"type": "array", "items": _TRIPLE_COMBO},   # 복승
        "exacta": {"type": "array", "items": _TRIPLE_COMBO},     # 쌍승
        "trio": {"type": "array", "items": _TRIPLE_COMBO},       # 삼복승
        "inconsistencies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "combo": {"type": "array", "items": {"type": "integer"}},
                    "level": {"type": "string", "enum": ["🔴", "🟡", ""]},
                    "note": {"type": "string"},
                },
                "required": ["combo", "level", "note"],
                "additionalProperties": False,
            },
        },
        "alerts": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "quinella", "exacta", "trio", "inconsistencies", "alerts"],
    "additionalProperties": False,
}


@app.route("/api/analyze/odds/triple", methods=["POST"])
def analyze_odds_triple():
    """복승/쌍복/삼복승 배당판 3종을 한 번에 판독하고 베팅종류 간 불일치(이상)를 감지."""
    body = request.json or {}
    prompt = (
        "아래에 같은 경주의 [복승 배당판], [쌍승 배당판], [삼복승 배당판]이 주어집니다(일부만 올 수 있음).\n"
        "[1단계] 각 배당판을 매트릭스/리스트 헤더 마번 기준으로 정확히 파싱:\n"
        "- 복승(quinella): combo=[2마리], odds. / 쌍승(exacta): combo=[선착,후착 2마리], odds. / 삼복승(trio): combo=[3마리], odds.\n"
        "- 비정상적으로 낮은(급락) 배당은 abnormal=true.\n"
        "[2단계] 베팅종류 간 불일치(이상) 감지 — inconsistencies 에 기록:\n"
        "- 예: 복승 A-B가 매우 싼데(인기), 대응하는 쌍승(A→B, B→A) 또는 A·B를 포함한 삼복승이 그만큼 싸지 않으면 불일치 → 한쪽 배당 이상 의심.\n"
        "- 예: 삼복승 A-B-C는 싼데 복승 A-B/ B-C/ A-C 중 일부가 비싸면 특정 두 마리 신뢰도 불일치.\n"
        "- 불일치 강도: 큰 괴리 🔴, 중간 🟡. combo(관련 마번), level, note(어느 종류끼리 어떻게 어긋났는지) 기재.\n"
        "[3단계] alerts 에 핵심 경고 한두 줄, summary 한 줄 요약. 배당판이 아니면 모두 빈 배열 + alerts=['배당판 인식 실패'].\n"
        "[중요] 각 종류(quinella·exacta·trio)는 전체 매트릭스를 다 넣지 말고 "
        "배당이 낮은(인기) 상위 40개 조합만 반환하세요(응답 크기 제한). 소수 배당은 소수점 1자리."
    )
    content = [{"type": "text", "text": prompt}]
    for key, label in [("quinella", "[복승 배당판]"), ("exacta", "[쌍승 배당판]"), ("trio", "[삼복승 배당판]")]:
        if body.get(key):
            content += [{"type": "text", "text": label}, image_block(body[key])]
    if len(content) == 1:
        return jsonify({"error": "복승/쌍승/삼복승 중 최소 1장의 배당판 이미지가 필요합니다."}), 400
    out = call_claude(content, TRIPLE_ODDS_SCHEMA, 8192, body.get("api_key"))
    return jsonify(out)


# ─────────────────────────────────────────
# 배당 이상감지 엔진 (Phase 2) — 수동 시계열 → 급변+괴리 통합 신호
# ─────────────────────────────────────────
# 마감 전 배당을 주기적으로 스냅샷 기록해 odds_store.json 에 누적하고,
# 두 축을 합친 "이상 신호 점수"(0-100, 50=중립)를 산출한다.
#   1) 급변(Drop)   — 마감 직전 배당 급락 = 큰돈 유입(스마트머니) 신호
#   2) 괴리(Edge)   — BMED 기대확률 대비 시장 내재확률이 낮으면 저평가마, 높으면 과대평가
# Vision 단일 스냅샷인 /api/analyze/odds 와 달리, 시간축 변화를 정량화한다.
ODDS_STORE = os.path.join(os.path.dirname(__file__), "odds_store.json")


def _odds_load():
    try:
        with open(ODDS_STORE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _odds_save(db):
    with open(ODDS_STORE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False)


def _odds_get_race(db, race_key):
    return db.get(race_key) or {"snaps": [], "series": {}}


# ───────── 결과(착순) 저장소 — 확장 프로그램 결과페이지 자동수집용 ─────────
RESULTS_STORE = os.path.join(os.path.dirname(__file__), "results_store.json")


def _results_load():
    try:
        with open(RESULTS_STORE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _results_save(db):
    with open(RESULTS_STORE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False)


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _valid_series(arr):
    """null 제거한 유효 배당만 [(index, odds), ...]"""
    return [(i, v) for i, v in enumerate(arr or []) if isinstance(v, (int, float)) and v > 0]


def odds_add_snapshot(race_key, odds_by_horse, t=None):
    """한 라운드 스냅샷 기록: {마번: 배당} 을 하나의 타임스탬프로 누적.
    값 없는 말은 null 로 패딩해 인덱스 정합성 유지."""
    clean = {}
    for k, v in (odds_by_horse or {}).items():
        try:
            no, val = int(k), float(v)
            if val > 0:
                clean[no] = val
        except (TypeError, ValueError):
            continue

    db = _odds_load()
    race = _odds_get_race(db, race_key)
    idx = len(race["snaps"])
    race["snaps"].append(t if t is not None else time.time())

    nums = set(int(n) for n in race["series"].keys()) | set(clean.keys())
    for no in nums:
        key = str(no)
        arr = race["series"].get(key) or [None] * idx
        while len(arr) < idx:
            arr.append(None)
        arr.append(clean.get(no))
        race["series"][key] = arr

    db[race_key] = race
    _odds_save(db)
    return race


def odds_undo(race_key):
    """마지막 스냅샷 1라운드 되돌리기"""
    db = _odds_load()
    race = db.get(race_key)
    if not race or not race["snaps"]:
        return
    race["snaps"].pop()
    for key in list(race["series"].keys()):
        race["series"][key].pop()
        if all(v is None for v in race["series"][key]):
            del race["series"][key]
    db[race_key] = race
    _odds_save(db)


def odds_clear(race_key):
    db = _odds_load()
    db.pop(race_key, None)
    _odds_save(db)


def odds_compute(race_key, bmed_horses):
    """bmed_horses: [{no, name, score}] → 말별 드롭/괴리/통합 신호 + 태그"""
    race = _odds_get_race(_odds_load(), race_key)
    series = race["series"]
    horses = [{"no": h.get("no"), "name": h.get("name", ""), "bmedScore": h.get("score") or 0}
              for h in (bmed_horses or [])]

    # 1) 배당 메트릭(드롭/속도/현재배당)
    for h in horses:
        valid = _valid_series(series.get(str(h["no"])))
        if valid:
            h["firstOdds"], h["lastOdds"] = valid[0][1], valid[-1][1]
            h["drop"] = (h["firstOdds"] - h["lastOdds"]) / h["firstOdds"] if h["firstOdds"] else 0
            vel = 0
            for k in range(1, len(valid)):
                prev, cur = valid[k - 1][1], valid[k][1]
                step = (prev - cur) / prev if prev else 0
                if abs(step) > abs(vel):
                    vel = step
            h["velocity"] = vel
            h["_move"] = len(valid) >= 2
        else:
            h["firstOdds"] = h["lastOdds"] = None
            h["drop"] = h["velocity"] = 0
            h["_move"] = False

    # 2) 확률 정규화 (시장: 부킹마진 제거 / BMED: 점수 비중)
    inv_sum = sum(1 / h["lastOdds"] for h in horses if h["lastOdds"])
    score_sum = sum(max(h["bmedScore"], 0) for h in horses)
    for h in horses:
        h["marketProb"] = (1 / h["lastOdds"]) / inv_sum if h["lastOdds"] and inv_sum else None
        h["bmedProb"] = max(h["bmedScore"], 0) / score_sum if score_sum else None
        h["edge"] = (h["bmedProb"] - h["marketProb"]
                     if h["marketProb"] is not None and h["bmedProb"] is not None else None)

    # 3) 통합 신호 점수 + 태그
    for h in horses:
        edge = h["edge"] or 0
        drop_part = _clamp(h["drop"] / 0.30, -1, 1)            # 30% 급락 → +1
        value_part = _clamp(edge / 0.12, -1, 1) if h["edge"] is not None else 0  # +12%p → +1
        w_drop = 0.5 if h["_move"] else 0                      # 시계열 1회뿐이면 괴리 100% 가중
        w_value = 0.5 if h["_move"] else 1
        combined = w_drop * drop_part + w_value * value_part
        h["signalScore"] = round(_clamp(50 + 50 * combined, 0, 100))

        tags = []
        if h["drop"] >= 0.15 and edge >= 0.05:
            tags.append("🔥 스마트머니")
        elif edge >= 0.08:
            tags.append("💎 저평가마")
        elif h["drop"] >= 0.15:
            tags.append("📈 급락(자금유입)")
        if edge <= -0.08:
            tags.append("⚠️ 과대평가")
        if h["drop"] <= -0.15:
            tags.append("📉 자금이탈")
        h["tags"] = tags
        del h["_move"]

    return {
        "horses": horses,
        "hasOdds": any(h["lastOdds"] for h in horses),
        "hasSeries": any(len(_valid_series(series.get(str(h["no"])))) >= 2 for h in horses),
        "snapCount": len(race["snaps"]),
    }


def odds_suggest_bets(computed):
    """signalScore 상위 말로 복승(2)·삼복승(3) 보정 추천(국소 계산)"""
    ranked = sorted([h for h in computed["horses"] if (h["lastOdds"] or 0) > 0 or h["bmedScore"] > 0],
                    key=lambda h: h["signalScore"], reverse=True)
    bets = []
    if len(ranked) >= 2:
        a, b = ranked[0], ranked[1]
        bets.append({"type": "복승", "combo": [a["no"], b["no"]],
                     "confidence": round((a["signalScore"] + b["signalScore"]) / 2),
                     "note": f"이상신호 상위 2두 ({a['signalScore']}·{b['signalScore']})"})
    if len(ranked) >= 3:
        a, b, c = ranked[0], ranked[1], ranked[2]
        bets.append({"type": "삼복승", "combo": [a["no"], b["no"], c["no"]],
                     "confidence": round((a["signalScore"] + b["signalScore"] + c["signalScore"]) / 3),
                     "note": f"이상신호 상위 3두 ({a['signalScore']}·{b['signalScore']}·{c['signalScore']})"})
    return bets


@app.route("/api/odds/snapshot", methods=["POST"])
def odds_snapshot():
    """배당 한 라운드 기록: {raceKey, odds:{마번:배당}} → {snaps, series}"""
    body = request.json or {}
    race_key = (body.get("raceKey") or "").strip()
    if not race_key:
        return jsonify({"error": "raceKey가 필요합니다."}), 400
    if not (body.get("odds") or {}):
        return jsonify({"error": "odds(마번:배당)가 비어 있습니다."}), 400
    race = odds_add_snapshot(race_key, body["odds"])
    return jsonify({"snaps": len(race["snaps"]), "series": race["series"]})


@app.route("/api/results/auto", methods=["POST"])
def results_auto():
    """확장 프로그램: keiba 결과페이지에서 자동 추출한 착순 저장.
    body: {raceKey, results:[{rank,no,name}], source?} → {ok, saved, top3}"""
    body = request.json or {}
    race_key = (body.get("raceKey") or "").strip()
    results = body.get("results") or []
    if not race_key:
        return jsonify({"error": "raceKey가 필요합니다."}), 400
    # 정규화: rank/no는 정수, 유효한 착순만
    norm = []
    for r in results:
        try:
            rank = int(r.get("rank"))
            no = int(r.get("no"))
        except (TypeError, ValueError):
            continue
        if rank >= 1 and no >= 1:
            norm.append({"rank": rank, "no": no, "name": (r.get("name") or "").strip()})
    if not norm:
        return jsonify({"error": "results(착순)가 비어 있습니다."}), 400
    norm.sort(key=lambda x: x["rank"])
    top3 = [x["no"] for x in norm if x["rank"] <= 3]

    db = _results_load()
    db[race_key] = {"results": norm, "top3": top3,
                    "source": body.get("source") or "extension", "t": time.time()}
    _results_save(db)
    print(f"[결과 자동수집] {race_key}: 1~3착 {top3}")
    return jsonify({"ok": True, "saved": len(norm), "top3": top3})


@app.route("/api/results/get", methods=["POST"])
def results_get():
    """저장된 착순 조회: {raceKey} → {results, top3} (없으면 빈 값)"""
    body = request.json or {}
    rec = _results_load().get((body.get("raceKey") or "").strip())
    return jsonify(rec or {"results": [], "top3": []})


# ───────── 3종(복승·쌍승·삼복승) 확장 원버튼 수집 저장소 ─────────
TRIPLE_STORE = os.path.join(os.path.dirname(__file__), "triple_store.json")


def _triple_load():
    try:
        with open(TRIPLE_STORE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _triple_save(db):
    with open(TRIPLE_STORE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False)


# 출마표2 전적 저장소 (raceKey → {horses:[{no,name,jockey,recent,weight}], t})
STARTERS_STORE = os.path.join(os.path.dirname(__file__), "starters_store.json")


def _starters_load():
    try:
        with open(STARTERS_STORE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _starters_save(db):
    with open(STARTERS_STORE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False)


def _form_from_starters(rk, drops):
    """저장된 출마표2 전적으로 마필 점수·등급 계산. 배당 급락마는 이상감지 상향 반영."""
    rec = _starters_load().get(rk)
    if not rec or not rec.get("horses"):
        return None
    anomaly_by_no = {}
    for d in drops or []:
        if d.get("pct", 0) < 0:  # 배당 하락(자금유입)
            for h in d["combo"]:
                anomaly_by_no.setdefault(int(h), {
                    "signalScore": min(100, 50 + int(abs(d["pct"]))),
                    "drop": abs(d["pct"]) / 100.0})
    horses = [{"no": h.get("no"), "name": h.get("name", ""), "jockey": h.get("jockey", ""),
               "recentPlacings": h.get("recent") or [], "currentWeight": h.get("weight")}
              for h in rec["horses"]]
    scored = compute_horse_scores({}, horses, None, anomaly_by_no)
    scored.sort(key=lambda x: -x["totalScore"])
    return scored


@app.route("/api/odds/triple/ingest", methods=["POST"])
def triple_ingest():
    """확장 [전체 자동 수집]: {raceKey, quinella[], exacta[], trio[]} 저장 → {ok, counts}"""
    body = request.json or {}
    rk = (body.get("raceKey") or "").strip()
    if not rk:
        return jsonify({"error": "raceKey가 필요합니다."}), 400
    q, x, tr = body.get("quinella") or [], body.get("exacta") or [], body.get("trio") or []
    db = _triple_load()
    prev = db.get(rk) or {}
    now = time.time()
    # 변동 추적용 히스토리(최근 12회) — 직전 대비 급락/순위/역전 계산에 사용
    hist = (prev.get("history") or [])
    hist.append({"t": now, "quinella": q, "exacta": x, "trio": tr})
    hist = hist[-12:]
    db[rk] = {"quinella": q, "exacta": x, "trio": tr, "history": hist,
              "source": body.get("source"), "t": now}
    _triple_save(db)
    # 배당 변동 히스토리 파일에 스냅샷 누적 (타임스탬프+발주전분+이상감지)
    try:
        _history_append(rk, q, x, body.get("deadline"))
    except Exception as e:
        print("[히스토리] 기록 실패:", e)
    counts = {"quinella": len(q), "exacta": len(x), "trio": len(tr)}
    print(f"[3종 수집] {rk}: {counts} (history {len(hist)})")
    return jsonify({"ok": True, "counts": counts})


@app.route("/api/odds/triple/reset", methods=["POST"])
def triple_reset():
    """[🔄 새 경주 시작] 활성 3종 배당(triple_store)을 비워 새 경주로 전환.
    경주별 히스토리 파일(data/odds_history)은 그대로 보존(=[히스토리 보기]).
    body: {raceKey?} — 주면 그 경주만, 없으면 전체 활성 초기화."""
    rk = ((request.json or {}).get("raceKey") or "").strip()
    db = _triple_load()
    if rk:
        removed = 1 if db.pop(rk, None) is not None else 0
    else:
        removed = len(db)
        db = {}
    _triple_save(db)
    print(f"[새 경주] 활성 3종 초기화: {rk or '전체'} ({removed}건). 히스토리 파일은 보존.")
    return jsonify({"ok": True, "cleared": rk or "all", "removed": removed})


@app.route("/api/odds/triple/latest", methods=["GET", "POST"])
def triple_latest():
    """최근(또는 지정 raceKey) 3종 배당 조회 → {raceKey, quinella, exacta, trio}.
    raceKey 명시 & 데이터 없으면 빈 결과(waiting) — 이전 경주로 폴백하지 않음."""
    rk, explicit = None, False
    if request.method == "POST":
        rk = ((request.json or {}).get("raceKey") or "").strip() or None
        explicit = rk is not None
    db = _triple_load()
    if not db:
        return jsonify({})
    if rk not in db:
        if explicit:
            return jsonify({"raceKey": rk, "quinella": [], "exacta": [], "trio": [], "waiting": True})
        rk = max(db.keys(), key=lambda k: db[k].get("t", 0))
    rec = db.get(rk) or {}
    return jsonify({"raceKey": rk, "quinella": rec.get("quinella", []),
                    "exacta": rec.get("exacta", []), "trio": rec.get("trio", [])})


# ───────── 3종 규칙기반 즉시 분석: 급락·순위변동·역전·유력마·삼복승추천 ─────────
#   Claude 미사용(빠르고 무료). 확장 [즉시 분석] + 프론트 자동갱신이 함께 사용.
def _un(combo):
    """순서무관 정렬 튜플 키."""
    return tuple(sorted(int(x) for x in combo))


def _odds_map_un(arr):
    """[{combo,odds}] → {정렬튜플: 최저배당}."""
    m = {}
    for it in arr or []:
        try:
            k, o = _un(it["combo"]), float(it["odds"])
        except (KeyError, TypeError, ValueError):
            continue
        if o > 0 and (k not in m or o < m[k]):
            m[k] = o
    return m


def _odds_map_dir(arr):
    """[{combo,odds}] → {(선,후): 배당} (순서있음, 2마리만)."""
    m = {}
    for it in arr or []:
        try:
            k = tuple(int(x) for x in it["combo"])
            o = float(it["odds"])
        except (KeyError, TypeError, ValueError):
            continue
        if len(k) == 2 and o > 0 and (k not in m or o < m[k]):
            m[k] = o
    return m


# ── 전적+배당 복합 제거(elimination) 엔진 ─────────────────────────────
#  각 출전마의 대표 복승배당(가장 싼 조합)으로 배당점수(0~100)를, 출마표2/KRA 전적
#  총점으로 전적보정(-30~+20)을 매겨 합산한다. 합산이 낮을수록 제거 대상.
#  이변 신호(급락 30%+·쌍승 상위 10위)가 있으면 제거를 취소한다.
def _odds_score(o):
    """대표 배당 → 배당점수(시장이 얼마나 지지하는가). 낮은 배당=강한 지지=고점."""
    if o is None or o >= 150:
        return 0
    if o >= 80:
        return 20
    if o >= 50:
        return 40
    if o >= 30:
        return 60
    return 100


def _form_adj(total):
    """전적 총점 → 제거 가중치. 전적 없으면 0(중립)."""
    if total is None:
        return 0
    if total <= 20:
        return -30
    if total <= 40:
        return -10
    if total <= 60:
        return 0
    if total <= 80:
        return 10
    return 20


def _elimination(curQ, curD, exa, drops, form, trio_map=None):
    """배당+전적 복합 제거 판정. 반환: {horses:[...], counts, autoBets}."""
    trio_map = trio_map or {}
    # 1) 출전마 집합 + 대표 복승배당(각 말이 낀 조합 중 최저)
    repr_odds = {}
    for (a, b), o in curQ.items():
        for h in (a, b):
            if o > 0 and (repr_odds.get(h) is None or o < repr_odds[h]):
                repr_odds[h] = o
    for (a, b), o in curD.items():                 # 복승 없으면 쌍승으로 폴백
        for h in (a, b):
            if o > 0 and repr_odds.get(h) is None:
                repr_odds[h] = o
    form_by_no = {h["no"]: h for h in (form or []) if h.get("no") is not None}
    nos = set(repr_odds) | set(form_by_no)
    if not nos:
        return None

    # 2) 이변 신호 집합: 급락 30%+ / 쌍승 상위 10위 내
    drop30 = {}
    for d in drops or []:
        if d.get("pct", 0) <= -30:
            for h in d["combo"]:
                drop30[int(h)] = min(drop30.get(int(h), 0), d["pct"])
    top_exa = set()
    for it in sorted([e for e in (exa or []) if (e.get("odds") or 0) > 0],
                     key=lambda e: e["odds"])[:10]:
        for h in it.get("combo", []):
            top_exa.add(int(h))

    horses = []
    for no in nos:
        o = repr_odds.get(no)
        fh = form_by_no.get(no)
        ftotal = fh.get("totalScore") if fh else None
        os_ = _odds_score(o)
        fadj = _form_adj(ftotal)
        total = os_ + fadj
        # 기본 판정
        if total <= 30:
            verdict, label, keep = "🔴", "확실 제거", False
        elif total <= 50:
            verdict, label, keep = "🟠", "제거 권장", False
        elif total <= 70:
            verdict, label, keep = "🟡", "관찰", True
        else:
            verdict, label, keep = "🟢", "유력 후보", True
        reason = f"배당 {('%g배' % o) if o is not None else '미수집'}({os_}) + 전적 {ftotal if ftotal is not None else '-'}({'+' if fadj > 0 else ''}{fadj}) = {total}"
        # 이변 신호 → 제거 취소
        override, ov_reasons = False, []
        if not keep:
            if no in drop30:
                override = True
                ov_reasons.append(f"배당 급락 {drop30[no]}%")
            if no in top_exa:
                override = True
                ov_reasons.append("쌍승 상위10")
        # 후보 세부 등급: ⭐강력유력(배당낮음+전적우수+이상감지) / ★유력(배당낮음+전적우수) / △관찰
        tier = None
        anomaly_sig = (no in drop30) or (no in top_exa) or bool(fh and fh.get("anomaly"))
        low_odds = os_ >= 100          # 30배 미만 = 시장 강한 지지
        good_form = fadj >= 10         # 전적 61점 이상
        if keep:
            if low_odds and good_form and anomaly_sig:
                tier = "⭐"
            elif low_odds and good_form:
                tier = "★"
            else:
                tier = "△"
        horses.append({
            "no": no, "name": (fh or {}).get("name", ""),
            "oddsRepr": o, "oddsScore": os_,
            "formScore": ftotal, "formAdj": fadj, "total": total,
            "verdict": verdict, "verdictLabel": label, "keep": keep,
            "tier": tier, "override": override,
            "overrideReason": " · ".join(ov_reasons), "anomalySig": anomaly_sig,
            "reason": reason,
        })

    horses.sort(key=lambda h: -h["total"])
    kept = [h for h in horses if h["keep"] or h["override"]]
    elim = [h for h in horses if not (h["keep"] or h["override"])]

    # 후보 기준 자동 조합(복승 top2 / 삼복승 top3)
    cand_nos = [h["no"] for h in sorted(kept, key=lambda h: -h["total"])]
    auto_bets = []
    if len(cand_nos) >= 2:
        a, b = cand_nos[0], cand_nos[1]
        auto_bets.append({"kind": "복승", "combo": sorted([a, b]),
                          "odds": curQ.get(tuple(sorted((a, b))))})
    if len(cand_nos) >= 3:
        c3 = sorted(cand_nos[:3])
        auto_bets.append({"kind": "삼복승", "combo": c3,
                          "odds": trio_map.get(tuple(c3))})  # 실배당 없으면 None
    return {
        "horses": horses,
        "counts": {"entrants": len(horses), "candidates": len(kept), "eliminated": len(elim)},
        "autoBets": auto_bets,
    }


def _triple_analyze(rk, rec):
    quin = rec.get("quinella") or []
    exa = rec.get("exacta") or []
    trio = rec.get("trio") or []
    hist = rec.get("history") or []
    prev = hist[-2] if len(hist) >= 2 else None  # 직전 수집

    curQ = _odds_map_un(quin)
    prevQ = _odds_map_un(prev.get("quinella")) if prev else {}

    # 1) 변동/급락 (복승 조합, 음수 pct = 배당 하락=자금유입)
    drops = []
    for k, o in curQ.items():
        po = prevQ.get(k)
        if po and po > 0:
            pct = round((o - po) / po * 100, 1)
            if abs(pct) >= 8:
                drops.append({"combo": list(k), "prev": po, "cur": o, "pct": pct})
    drops.sort(key=lambda d: d["pct"])

    # 2) 순위 변동 (배당 낮은=인기 순위)
    def _ranks(m):
        return {k: i + 1 for i, (k, _) in enumerate(sorted(m.items(), key=lambda kv: kv[1]))}
    curR, prevR = _ranks(curQ), _ranks(prevQ)
    rank_changes = []
    for k in curQ:
        if k in prevR:
            delta = prevR[k] - curR[k]  # 양수 = 인기 상승
            if abs(delta) >= 3:
                rank_changes.append({"combo": list(k), "prevRank": prevR[k],
                                     "curRank": curR[k], "delta": delta})
    rank_changes.sort(key=lambda d: -abs(d["delta"]))
    rank_changes = rank_changes[:10]

    # 3) 쌍승 역전 (A→B vs B→A)
    curD = _odds_map_dir(exa)
    prevD = _odds_map_dir(prev.get("exacta")) if prev else {}
    reversals, seen = [], set()
    for (a, b), o in curD.items():
        pair = tuple(sorted((a, b)))
        if pair in seen:
            continue
        rev = curD.get((b, a))
        if rev is None:
            continue
        seen.add(pair)
        favored = [a, b] if o <= rev else [b, a]
        info = {"pair": list(pair), "favored": favored,
                "favoredOdds": min(o, rev), "otherOdds": max(o, rev), "flipped": False}
        if prev:
            pa, pb = prevD.get((a, b)), prevD.get((b, a))
            if pa is not None and pb is not None:
                prev_fav = [a, b] if pa <= pb else [b, a]
                info["flipped"] = (prev_fav != favored)
        reversals.append(info)
    reversals.sort(key=lambda r: (not r["flipped"], -(r["otherOdds"] / max(r["favoredOdds"], 0.1))))
    reversals = reversals[:10]

    # 4) 유력마 3마리 (상위 10개 복승 조합 등장 빈도 + 인기가중). 복승 없으면 쌍승 무순.
    base = curQ if curQ else {k: min(curD[k2] for k2 in (k, (k[1], k[0])) if k2 in curD)
                              for k in {tuple(sorted(p)) for p in curD}}
    top = sorted(base.items(), key=lambda kv: kv[1])[:10]
    freq = {}
    for k, o in top:
        for h in k:
            freq[h] = freq.get(h, 0.0) + 1.0 + 1.0 / max(o, 0.1)
    ranked = [h for h, _ in sorted(freq.items(), key=lambda kv: -kv[1])]
    key_horses = ranked[:3]

    # 이상감지말: 최대 급락 조합 중 유력마 아닌 말, 없으면 4순위 유력마
    anomaly_horse = None
    for d in drops:
        for h in d["combo"]:
            if h not in key_horses:
                anomaly_horse = h
                break
        if anomaly_horse is not None:
            break
    if anomaly_horse is None:
        anomaly_horse = ranked[3] if len(ranked) > 3 else (key_horses[-1] if key_horses else None)

    # 5) 베팅 추천: 복승 메인/보조 + 삼복승 메인/보험1/보험2 + 예산 배분율(alloc %)
    trio_map = _odds_map_un(trio)

    def _q(a, b):
        return curQ.get(tuple(sorted((a, b))))

    bet_rec, seen_rec = [], set()

    def _addbet(kind, label, combo, alloc, odds):
        need = 2 if kind == "복승" else 3
        cc = sorted(set(int(x) for x in combo))
        if len(cc) != need:
            return
        key = (kind, tuple(cc))
        if key in seen_rec:
            return
        seen_rec.add(key)
        bet_rec.append({"kind": kind, "label": label, "combo": cc, "alloc": alloc, "expOdds": odds})

    if len(key_horses) >= 2:
        h1, h2 = key_horses[0], key_horses[1]
        _addbet("복승", "복승 메인", [h1, h2], 43, _q(h1, h2))
    if len(key_horses) >= 3:
        h1, h2, h3 = key_horses[0], key_horses[1], key_horses[2]
        _addbet("복승", "복승 보조", [h1, h3], 20, _q(h1, h3))
        _addbet("삼복승", "삼복승 메인", [h1, h2, h3], 29, trio_map.get(tuple(sorted([h1, h2, h3]))))
        if anomaly_horse is not None:
            _addbet("삼복승", "삼복승 보험1", [h1, h2, anomaly_horse], 4,
                    trio_map.get(tuple(sorted([h1, h2, anomaly_horse]))))
            _addbet("삼복승", "삼복승 보험2", [h1, h3, anomaly_horse], 4,
                    trio_map.get(tuple(sorted([h1, h3, anomaly_horse]))))
    # [3번] 삼복승 실배당 미수집 시: 구성 복승 3쌍의 기하평균×2 로 추정(라벨=추정)
    def _trio_est(cc):
        ps = [_q(cc[0], cc[1]), _q(cc[0], cc[2]), _q(cc[1], cc[2])]
        if any(p is None or p <= 0 for p in ps):
            return None
        gm = (ps[0] * ps[1] * ps[2]) ** (1.0 / 3.0)
        return round(gm * 2, 1)
    for r in bet_rec:
        if r["kind"] == "삼복승" and r["expOdds"] is None:
            r["expOddsEst"] = _trio_est(r["combo"])

    # 삼복승만 뽑은 하위 호환 필드
    trio_rec = [{"label": r["label"], "combo": r["combo"],
                 "expOdds": r["expOdds"], "expOddsEst": r.get("expOddsEst")}
                for r in bet_rec if r["kind"] == "삼복승"]

    # 요약(팝업/화면 상단용)
    parts = []
    if drops:
        d0 = drops[0]
        arrow = "▼" if d0["pct"] < 0 else "▲"
        parts.append(f"급락 {d0['combo'][0]}-{d0['combo'][1]} {arrow}{abs(d0['pct'])}%")
    if any(r["flipped"] for r in reversals):
        r0 = next(r for r in reversals if r["flipped"])
        parts.append(f"🔴쌍승역전 {r0['favored'][0]}→{r0['favored'][1]}")
    if key_horses:
        parts.append("유력마 " + "·".join(map(str, key_horses)))
    main_bet = next((r for r in bet_rec if r["label"] == "복승 메인"), None)
    if main_bet:
        parts.append("복승 " + "+".join(map(str, main_bet["combo"])))
    summary = " / ".join(parts) if parts else "데이터 부족 — 복승 수집 필요"

    # 시계열 차트: 복승·쌍승·삼복승 각 "최저(최인기) 배당"의 라운드별 변화 (3줄)
    def _min_odds(arr):
        m = _odds_map_un(arr)
        return min(m.values()) if m else None
    chart_series = []
    for label, field in (("복승", "quinella"), ("쌍승", "exacta"), ("삼복승", "trio")):
        odds = [_min_odds(h.get(field)) for h in hist]
        if any(o is not None for o in odds):
            chart_series.append({"label": label, "odds": odds})
    chart = {"times": [h.get("t") for h in hist], "series": chart_series}

    # [4번] 학습결과 반영: 과거 실적 기반 급락 감지 적중률 안내
    learned = None
    try:
        _da = (_learning_load().get("stats", {}) or {}).get("drop_anomaly") or {}
        if drops and _da.get("n", 0) >= 5 and _da.get("rate") is not None:
            learned = f"과거 데이터: 급락 감지 시 해당말 입상률 {_da['rate']}% ({_da['hit']}/{_da['n']}경주)"
    except Exception:
        learned = None

    # ── [실시간 변동 신호 + 이유 자동 설명] ──
    signals = []

    def _drop_reason(pct):
        if pct <= -50:
            return "🔴", "단기간 대량 자금 유입 → 내부 정보성 베팅 가능성"
        if pct <= -30:
            return "🟠", "자금 유입 감지 → 주목 필요"
        if pct <= -15:
            return "🟡", "자금 유입 초기 → 관찰 필요"
        return None, None

    for d in drops:
        lvl, reason = _drop_reason(d["pct"])
        if lvl:
            signals.append({"level": lvl, "type": "급락",
                            "text": f"{d['combo'][0]}+{d['combo'][1]} 복승 {d['prev']}→{d['cur']} ({d['pct']}%)",
                            "detail": reason})
    for r in reversals:
        if r.get("flipped"):
            signals.append({"level": "🔄", "type": "역전",
                            "text": f"쌍승 {r['favored'][0]}→{r['favored'][1]} ({r['favoredOdds']}) < {r['favored'][1]}→{r['favored'][0]} ({r['otherOdds']})",
                            "detail": f"시장이 {r['favored'][0]}번을 실질 1착으로 판단"})
    # 배당 압축: 상위 4개 복승 근접
    tops = sorted(curQ.values())[:4]
    if len(tops) >= 3 and tops[0] > 0 and tops[-1] / tops[0] < 1.3:
        signals.append({"level": "🟡", "type": "압축",
                        "text": f"상위 복승 배당 근접 ({tops[0]}~{tops[-1]})",
                        "detail": "자금 분산 / 결과 예측 어려움"})
    # 급락 후 반등: 히스토리에서 상위 조합이 15%↓ 후 15%↑
    for k in list(curQ)[:8]:
        seq = [hm[k] for hm in (_odds_map_un(h.get("quinella")) for h in hist[-4:]) if k in hm]
        if len(seq) >= 3:
            lo = min(seq)
            if seq[0] > 0 and lo > 0 and (seq[0] - lo) / seq[0] >= 0.15 and (seq[-1] - lo) / lo >= 0.15:
                signals.append({"level": "🟡", "type": "반등",
                                "text": f"{k[0]}+{k[1]} 급락 후 반등 ({seq[0]}→{lo}→{seq[-1]})",
                                "detail": "페이크 베팅 의심 / 해당말 신뢰도 하락"})

    # 최근 스냅샷 시각·발주전분(히스토리 파일에서)
    last_snap = None
    try:
        _hp, _, _ = _hist_path(rk)
        _hd = json.load(open(_hp, encoding="utf-8"))
        if _hd.get("snapshots"):
            _s = _hd["snapshots"][-1]
            last_snap = {"time": _s.get("time"), "minutes_before": _s.get("minutes_before")}
    except Exception:
        last_snap = None

    form = _form_from_starters(rk, drops)  # 출마표2/KRA 전적 등급(있으면)
    elimination = _elimination(curQ, curD, exa, drops, form, trio_map)  # 배당+전적 복합 제거

    return {
        "raceKey": rk, "hasPrev": bool(prev),
        "counts": {"quinella": len(quin), "exacta": len(exa), "trio": len(trio), "history": len(hist)},
        "drops": drops[:15], "rankChanges": rank_changes, "reversals": reversals,
        "keyHorses": key_horses, "anomalyHorse": anomaly_horse,
        "trioRecommend": trio_rec, "betRecommend": bet_rec,
        "summary": summary, "chart": chart,
        "form": form,
        "elimination": elimination,  # 배당+전적 복합 제거/후보 판정
        "learned": learned,  # 학습 통계 안내(있으면)
        "signals": signals, "lastSnapshot": last_snap,  # 실시간 변동 알림용
    }


@app.route("/api/extract/japan", methods=["POST"])
def extract_japan():
    """[출마표2] 전적 + (선택)배당을 함께 받아 저장하고 통합 분석 반환.
    body: {raceKey, horses:[{no,name,jockey,recent[],weight}], quinella?, exacta?, trio?}
    → _triple_analyze 결과(전적 등급 form 포함)."""
    body = request.json or {}
    rk = (body.get("raceKey") or "").strip()
    if not rk:
        return jsonify({"error": "raceKey가 필요합니다."}), 400
    horses = body.get("horses") or []
    sdb = _starters_load()
    sdb[rk] = {"horses": horses, "t": time.time()}
    _starters_save(sdb)
    # 배당이 함께 오면 triple_store 도 갱신(히스토리 유지)
    if body.get("quinella") or body.get("exacta") or body.get("trio"):
        tdb = _triple_load()
        prev = tdb.get(rk) or {}
        q, x, tr = body.get("quinella") or [], body.get("exacta") or [], body.get("trio") or []
        hist = (prev.get("history") or [])
        hist.append({"t": time.time(), "quinella": q, "exacta": x, "trio": tr})
        hist = hist[-12:]
        tdb[rk] = {"quinella": q, "exacta": x, "trio": tr, "history": hist,
                   "source": body.get("source"), "t": time.time()}
        _triple_save(tdb)
        try:
            _history_append(rk, q, x, body.get("deadline"))
        except Exception as e:
            print("[히스토리] 기록 실패:", e)
    trec = _triple_load().get(rk) or {}
    print(f"[출마표2 전적] {rk}: {len(horses)}두 저장")
    return jsonify(_triple_analyze(rk, trec))


@app.route("/api/odds/triple/analyze", methods=["GET", "POST"])
def triple_analyze():
    """규칙기반 즉시 분석(Claude 미사용): 급락·순위변동·쌍승역전·유력마·삼복승추천.
    raceKey를 명시하면 그 경주만(없으면 대기), 없으면 최근 경주."""
    rk, explicit = None, False
    if request.method == "POST":
        rk = ((request.json or {}).get("raceKey") or "").strip() or None
        explicit = rk is not None
    db = _triple_load()
    if not db:
        return jsonify({"error": "수집된 3종 배당이 없습니다. 먼저 [전체 자동 수집]을 실행하세요."}), 404
    if rk not in db:
        if explicit:  # 지정 경주 데이터가 아직 없음 → 폴백 없이 대기 안내(이전 경주 표시 방지)
            return jsonify({"error": f"'{rk}' 경주의 수집 데이터가 없습니다. 확장에서 해당 raceKey로 [전체 자동 수집]을 실행하세요.",
                            "raceKey": rk, "waiting": True}), 404
        rk = max(db.keys(), key=lambda k: db[k].get("t", 0))
    return jsonify(_triple_analyze(rk, db.get(rk) or {}))


# ══════════════ 배당 변동 히스토리 + 결과기반 자동학습 (Phase 5) ══════════════
ODDS_HISTORY_DIR = os.path.join(os.path.dirname(__file__), "data", "odds_history")
LEARNING_FILE = os.path.join(os.path.dirname(__file__), "data", "learning.json")


def _hist_path(rk):
    """raceKey → (파일경로, date, race). 예: '2026-07-02 나고야 4경주' / '나고야 4경주'."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", rk or "")
    date = m.group(1) if m else time.strftime("%Y-%m-%d", time.localtime())
    race = re.sub(r"\d{4}-\d{2}-\d{2}", "", rk or "").strip() or (rk or "race")
    safe = re.sub(r"[^\w가-힣]+", "_", f"{date}_{race}").strip("_")
    os.makedirs(ODDS_HISTORY_DIR, exist_ok=True)
    return os.path.join(ODDS_HISTORY_DIR, safe + ".json"), date, race


def _combo_dict(arr):
    """[{combo,odds}] → {'1+2': 45.2, ...}."""
    d = {}
    for it in arr or []:
        try:
            c, o = it["combo"], float(it["odds"])
        except (KeyError, TypeError, ValueError):
            continue
        if o > 0:
            d["+".join(str(int(x)) for x in c)] = round(o, 1)
    return d


def _history_append(rk, quinella, exacta, deadline=None):
    """경주별 히스토리 파일에 스냅샷 1건 추가. 직전 대비 급락(≤-20%) 이상감지 기록."""
    path, date, race = _hist_path(rk)
    try:
        doc = json.load(open(path, encoding="utf-8"))
    except Exception:
        doc = {"race": race, "date": date, "raceKey": rk, "snapshots": [], "result": None}
    now = time.time()
    minutes_before = None
    try:
        if deadline:
            dl = float(deadline)                     # epoch ms 또는 s
            dl_ms = dl if dl > 1e12 else dl * 1000
            mb = round((dl_ms - now * 1000) / 60000)
            minutes_before = mb if mb >= 0 else None
    except (TypeError, ValueError):
        minutes_before = None
    curQ = _odds_map_un(quinella)
    anomalies = []
    if doc["snapshots"]:
        prevQ = {}
        for k, v in (doc["snapshots"][-1].get("quinella") or {}).items():
            try:
                prevQ[tuple(sorted(int(x) for x in k.split("+")))] = float(v)
            except ValueError:
                pass
        for k, o in curQ.items():
            po = prevQ.get(k)
            if po and po > 0:
                pct = round((o - po) / po * 100)
                if pct <= -20:
                    anomalies.append(f"급락감지: {'+'.join(map(str, k))} {pct}%")
    doc["snapshots"].append({
        "time": time.strftime("%H:%M:%S", time.localtime(now)),
        "minutes_before": minutes_before,
        "quinella": _combo_dict(quinella), "exacta": _combo_dict(exacta),
        "anomalies": anomalies, "t": now,
    })
    doc["snapshots"] = doc["snapshots"][-300:]
    json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    return path


def _learning_load():
    try:
        return json.load(open(LEARNING_FILE, encoding="utf-8"))
    except Exception:
        return {"records": [], "stats": {}}


def _learning_save(d):
    os.makedirs(os.path.dirname(LEARNING_FILE), exist_ok=True)
    json.dump(d, open(LEARNING_FILE, "w", encoding="utf-8"), ensure_ascii=False)


def _rate(records, sel, cond):
    s = [r for r in records if sel(r)]
    hit = sum(1 for r in s if cond(r))
    return {"n": len(s), "hit": hit, "rate": (round(hit / len(s) * 100, 1) if s else None)}


def _recompute_learning_stats(records):
    """이상감지 유형별 실제 적중률 + 추천 적중률."""
    def rev_hit(r):
        favs = [rv["favored"][0] for rv in (r.get("reversals") or []) if rv.get("favored")]
        return r.get("result", {}).get("1st") in favs
    return {
        "total": len(records),
        "recommend_hit": _rate(records, lambda r: True, lambda r: r.get("was_hit")),
        "drop_anomaly": _rate(records, lambda r: r.get("anomalies_detected"),
                              lambda r: r.get("anomaly_was_correct")),
        "reversal": _rate(records, lambda r: r.get("reversals"), rev_hit),
    }


@app.route("/api/history/list", methods=["GET", "POST"])
def history_list():
    """저장된 경주 히스토리 목록 → [{file,date,race,raceKey,snaps,hasResult}]."""
    out = []
    if os.path.isdir(ODDS_HISTORY_DIR):
        for fn in sorted(os.listdir(ODDS_HISTORY_DIR), reverse=True):
            if not fn.endswith(".json"):
                continue
            try:
                d = json.load(open(os.path.join(ODDS_HISTORY_DIR, fn), encoding="utf-8"))
            except Exception:
                continue
            out.append({"file": fn, "date": d.get("date"), "race": d.get("race"),
                        "raceKey": d.get("raceKey"), "snaps": len(d.get("snapshots") or []),
                        "hasResult": bool(d.get("result"))})
    return jsonify({"races": out})


@app.route("/api/history/get", methods=["POST"])
def history_get():
    """{raceKey|file} → 히스토리 전체(타임라인·스냅샷별 이상감지)."""
    body = request.json or {}
    fn = body.get("file")
    if fn:
        path = os.path.join(ODDS_HISTORY_DIR, os.path.basename(fn))
    else:
        path, _, _ = _hist_path((body.get("raceKey") or "").strip())
    try:
        return jsonify(json.load(open(path, encoding="utf-8")))
    except Exception:
        return jsonify({"error": "히스토리가 없습니다."}), 404


@app.route("/api/history/record-result", methods=["POST"])
def history_record_result():
    """경주 결과 입력 → 히스토리에 결과 기록 + 자동학습 레코드/통계 갱신.
    body: {raceKey, result:{'1st':7,'2nd':1,'3rd':9}}"""
    body = request.json or {}
    rk = (body.get("raceKey") or "").strip()
    result = body.get("result") or {}
    top3 = [result.get("1st"), result.get("2nd"), result.get("3rd")]
    top3 = [int(x) for x in top3 if x not in (None, "")]
    if not rk or len(top3) < 1:
        return jsonify({"error": "raceKey와 결과(1~3착)가 필요합니다."}), 400

    path, _, _ = _hist_path(rk)
    try:
        doc = json.load(open(path, encoding="utf-8"))
    except Exception:
        doc = {"raceKey": rk, "snapshots": [], "result": None}
    doc["result"] = result
    json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False)

    an = _triple_analyze(rk, _triple_load().get(rk) or {})

    def in3(combo):
        return all(x in top3 for x in combo)
    was_hit = any(in3(r["combo"]) for r in an.get("betRecommend", []))

    anomalies_detected, anomaly_correct = [], False
    for d in an.get("drops", [])[:5]:
        if d["pct"] < 0:
            anomalies_detected.append(f"급락: {d['combo'][0]}-{d['combo'][1]} {d['pct']}%")
            if any(h in top3 for h in d["combo"]):
                anomaly_correct = True

    def snap_near(minb):
        best = None
        for s in doc.get("snapshots", []):
            mb = s.get("minutes_before")
            if mb is None:
                continue
            if best is None or abs(mb - minb) < abs(best[0] - minb):
                best = (mb, s)
        return best[1] if best else None

    record = {
        "race": rk, "result": result, "top3": top3, "was_hit": was_hit,
        "anomalies_detected": anomalies_detected, "anomaly_was_correct": anomaly_correct,
        "reversals": [r for r in an.get("reversals", []) if r.get("flipped")],
        "keyHorses": an.get("keyHorses"),
        "odds_at_10min": (snap_near(10) or {}).get("quinella"),
        "odds_at_1min30sec": (snap_near(2) or {}).get("quinella"),
        "t": time.time(),
    }
    L = _learning_load()
    L["records"].append(record)
    L["stats"] = _recompute_learning_stats(L["records"])
    _learning_save(L)
    print(f"[자동학습] {rk} 결과 {top3} → 추천적중 {was_hit}, 급락적중 {anomaly_correct}")
    return jsonify({"ok": True, "record": record, "stats": L["stats"]})


@app.route("/api/learning/stats", methods=["GET", "POST"])
def learning_stats():
    """누적 학습 통계 대시보드."""
    L = _learning_load()
    return jsonify({"stats": L.get("stats", {}), "count": len(L.get("records", []))})


# ───────── KRA 공공데이터: API 키 저장 + 마필 과거기록 조회 ─────────
KRA_KEY_FILE = os.path.join(os.path.dirname(__file__), "data", "kra_key.txt")
KRA_HISTORY_FILE = os.path.join(os.path.dirname(__file__), "data", "kra_history.json")


@app.route("/api/kra/key", methods=["GET", "POST"])
def kra_key():
    """data.go.kr 서비스키 저장/조회. 저장 위치는 tools/fetch_kra.py 와 공유(data/kra_key.txt)."""
    if request.method == "POST":
        key = ((request.json or {}).get("key") or "").strip()
        os.makedirs(os.path.dirname(KRA_KEY_FILE), exist_ok=True)
        with open(KRA_KEY_FILE, "w", encoding="utf-8") as f:
            f.write(key)
        return jsonify({"ok": True, "hasKey": bool(key)})
    has = os.path.exists(KRA_KEY_FILE) and os.path.getsize(KRA_KEY_FILE) > 0
    return jsonify({"hasKey": has})


@app.route("/api/kra/horse", methods=["GET", "POST"])
def kra_horse():
    """마명으로 KRA 과거기록 자동매칭.
    POST {name} 또는 GET ?name=마명 → {records, starts, wins, places, placeRate, recentPlacings}"""
    if request.method == "GET":
        name = (request.args.get("name") or "").strip()
    else:
        name = ((request.json or {}).get("name") or "").strip()
    try:
        with open(KRA_HISTORY_FILE, encoding="utf-8") as f:
            hist = json.load(f)
    except Exception:
        return jsonify({"name": name, "records": [], "starts": 0, "placeRate": 0})
    recs = (hist.get("byHorse") or {}).get(name) or []
    starts = len(recs)
    placed = sum(1 for r in recs if isinstance(r.get("stOrd"), (int, float)) and 0 < r["stOrd"] <= 3)
    wins = sum(1 for r in recs if r.get("stOrd") == 1)
    ordered = sorted(recs, key=lambda r: r.get("date", ""), reverse=True)
    recent_placings = [int(r["stOrd"]) for r in ordered[:5]
                       if isinstance(r.get("stOrd"), (int, float)) and r["stOrd"] > 0]
    return jsonify({
        "name": name, "records": recs, "starts": starts, "wins": wins, "places": placed,
        "placeRate": round(placed / starts * 100, 1) if starts else 0,
        "recentPlacings": recent_placings,  # 최근 5착순(전적 자동 대입용)
    })


def _kra_load_history():
    try:
        with open(KRA_HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def kra_horse_summary(name, hist=None):
    """마명 → KRA 실제 과거기록 요약 문자열(분석 프롬프트 주입용). 기록 없으면 ''"""
    if not name:
        return ""
    if hist is None:
        hist = _kra_load_history()
    recs = (hist.get("byHorse") or {}).get(name) or []
    if not recs:
        return ""
    ordered = sorted(recs, key=lambda r: r.get("date", ""), reverse=True)
    starts = len(recs)
    w = sum(1 for r in recs if r.get("stOrd") == 1)
    s = sum(1 for r in recs if r.get("stOrd") == 2)
    t = sum(1 for r in recs if r.get("stOrd") == 3)
    rate = round((w + s + t) / starts * 100) if starts else 0
    recent = "·".join(str(int(r["stOrd"])) for r in ordered[:5]
                      if isinstance(r.get("stOrd"), (int, float)) and r["stOrd"] > 0)
    return f", KRA전적 {starts}전 {w}-{s}-{t} 복승권{rate}%" + (f" 최근{recent}착" if recent else "")


@app.route("/api/odds/undo", methods=["POST"])
def odds_undo_route():
    body = request.json or {}
    odds_undo((body.get("raceKey") or "").strip())
    return jsonify({"ok": True})


@app.route("/api/odds/clear", methods=["POST"])
def odds_clear_route():
    body = request.json or {}
    odds_clear((body.get("raceKey") or "").strip())
    return jsonify({"ok": True})


@app.route("/api/odds/race", methods=["POST"])
def odds_race_route():
    """저장된 시계열 조회: {raceKey} → {snaps, series}"""
    body = request.json or {}
    return jsonify(_odds_get_race(_odds_load(), (body.get("raceKey") or "").strip()))


@app.route("/api/odds/compute", methods=["POST"])
def odds_compute_route():
    """{raceKey, horses:[{no,name,score}]} → 신호 점수 + 보정 추천(bets)"""
    body = request.json or {}
    race_key = (body.get("raceKey") or "").strip()
    computed = odds_compute(race_key, body.get("horses") or [])
    computed["bets"] = odds_suggest_bets(computed)
    return jsonify(computed)


# ─────────────────────────────────────────
# 전적 분석 엔진 (Phase 3) — 마필 점수 자동 계산
# ─────────────────────────────────────────
# 3-1 기본 점수: 최근 5경주 착순 가중 평균
#   착순→점수: 1착=100, 2착=75, 3착=50, 4착=25, 5착 이하=0
#   가중치(직전 경주부터): 40% / 30% / 20% / 5% / 5%
FORM_WEIGHTS = [0.40, 0.30, 0.20, 0.05, 0.05]


def place_points(p):
    """착순(int) → 점수. 1=100, 2=75, 3=50, 4=25, 5착 이하=0. 무효(기권/미출주 등)는 None."""
    try:
        p = int(p)
    except (TypeError, ValueError):
        return None
    if p <= 0:
        return None
    return {1: 100, 2: 75, 3: 50, 4: 25}.get(p, 0)


def base_form_score(placings):
    """placings: 최근 경주부터 [직전, -2, -3, -4, -5] 착순 리스트.
    유효 착순만 가중평균하되, 실제 사용된 가중치 합으로 재정규화(전적 <5경주 대응). 0~100."""
    used = []
    for w, p in zip(FORM_WEIGHTS, (placings or [])[:5]):
        pts = place_points(p)
        if pts is not None:
            used.append((w, pts))
    if not used:
        return 0.0
    wsum = sum(w for w, _ in used)
    return round(sum(w * pts for w, pts in used) / wsum, 1)


def _to_int(v):
    """'1200', '1200M', '1,200m' → 1200. 숫자 없으면 None."""
    if v is None:
        return None
    m = re.search(r"\d+", str(v).replace(",", ""))
    return int(m.group()) if m else None


def _placings_of(horse):
    """horse 에서 최근 착순 배열을 얻는다. recentPlacings 우선, 없으면 pastRaces[].placing."""
    if horse.get("recentPlacings"):
        return horse["recentPlacings"]
    return [pr.get("placing") for pr in (horse.get("pastRaces") or [])]


# ── 3-2 코스 적성 보너스 ──────────────────
def course_aptitude_bonus(past_races, race):
    """현재 거리 ±100m 경험 +10 / 현재 코스(내·외) 경험 +10 / 현재 조건(급) 경험 +5."""
    bonus, detail = 0, []
    rdist = _to_int(race.get("distance"))
    rcourse = (race.get("course") or "").strip()
    rgrade = (race.get("grade") or "").strip()
    dists, courses, grades = [], [], []
    for pr in past_races or []:
        d = _to_int(pr.get("distance"))
        if d is not None:
            dists.append(d)
        if pr.get("course"):
            courses.append(str(pr["course"]).strip())
        if pr.get("grade"):
            grades.append(str(pr["grade"]).strip())
    if rdist is not None and any(abs(d - rdist) <= 100 for d in dists):
        bonus += 10
        detail.append("거리적성(±100m)+10")
    if rcourse and rcourse in courses:
        bonus += 10
        detail.append(f"코스일치({rcourse})+10")
    if rgrade and rgrade in grades:
        bonus += 5
        detail.append(f"조건일치({rgrade})+5")
    return bonus, detail


# ── 3-3 기수 보너스 ──────────────────────
def top20_threshold(rates):
    """복승률 리스트의 상위 20% 경계값(80번째 백분위). 유효값 없으면 None."""
    vals = sorted(v for v in rates if isinstance(v, (int, float)) and v > 0)
    if not vals:
        return None
    # 80번째 백분위(선형 보간)
    idx = 0.8 * (len(vals) - 1)
    lo = int(idx)
    frac = idx - lo
    return vals[lo] + (vals[min(lo + 1, len(vals) - 1)] - vals[lo]) * frac


def jockey_bonus(horse, threshold):
    """기수 3개월 복승률 상위 20% +15 / 기수-마필 직전 경주 적중(같은 기수·3착 이내) +10."""
    bonus, detail = 0, []
    rate = horse.get("jockey3mPlaceRate")
    if threshold is not None and isinstance(rate, (int, float)) and rate >= threshold and rate > 0:
        bonus += 15
        detail.append(f"기수복승률상위20%({rate}%)+15")
    placings = _placings_of(horse)
    last = placings[0] if placings else None
    last_jockey = horse.get("lastJockey")
    same = (last_jockey is None) or (str(last_jockey).strip() == str(horse.get("jockey", "")).strip())
    lp = _to_int(last)
    if same and lp is not None and 1 <= lp <= 3:
        bonus += 10
        detail.append(f"기수-마필 직전적중({lp}착)+10")
    return bonus, detail


# ── 3-4 특수 플래그 ──────────────────────
def special_flags(horse, race):
    """동일거리 2착 2회↑ → 삼복승필수 / 마체중 ±10kg↑ 경고 / 출전간격 3주↑ 주의 / 연속출전 피로도."""
    flags = []
    rdist = _to_int(race.get("distance"))
    if rdist is not None:
        cnt = sum(1 for pr in (horse.get("pastRaces") or [])
                  if _to_int(pr.get("distance")) == rdist and _to_int(pr.get("placing")) == 2)
        if cnt >= 2:
            flags.append({"type": "삼복승필수", "level": "must",
                          "msg": f"동일거리({rdist}m) 2착 {cnt}회 → 삼복승 필수 포함"})
    cw, lw = _to_int(horse.get("currentWeight")), _to_int(horse.get("lastWeight"))
    if cw is not None and lw is not None and abs(cw - lw) >= 10:
        flags.append({"type": "마체중급변", "level": "warn",
                      "msg": f"마체중 전경주 대비 {cw - lw:+d}kg (±10kg↑) 경고"})
    days = _to_int(horse.get("daysSinceLast"))
    if days is not None and days >= 21:
        flags.append({"type": "장기간격", "level": "caution",
                      "msg": f"출전 간격 {days}일(3주↑) 주의"})
    consec = _to_int(horse.get("consecutiveRuns"))
    if consec is not None and consec >= 2:
        flags.append({"type": "피로도", "level": "warn",
                      "msg": f"{consec}연전 연속 출전 → 피로도 경고"})
    return flags


# ── 3-5 등급 분류 + Phase 2 이상감지 보정 ──
GRADES = ["A", "B", "C", "D"]


def _upgrade(grade):
    i = GRADES.index(grade)
    return GRADES[max(0, i - 1)]


def classify_grades(scored):
    """totalScore 사분위로 A/B/C/D(상위25/50/75%) 부여 후, 이상감지로 보정.
    🔴(signalScore≥75) → 1단계 상향 / 급락 50%↑ → 삼복승 보험 강제 플래그."""
    order = sorted(scored, key=lambda h: h["totalScore"], reverse=True)
    n = len(order)
    for rank, h in enumerate(order):
        frac = rank / n if n else 0
        h["grade"] = "A" if frac < 0.25 else "B" if frac < 0.50 else "C" if frac < 0.75 else "D"
        h["gradeBase"] = h["grade"]
        an = h.get("anomaly") or {}
        sig = an.get("signalScore")
        drop = an.get("drop")
        if isinstance(sig, (int, float)) and sig >= 75:
            h["grade"] = _upgrade(h["grade"])
            if h["grade"] != h["gradeBase"]:
                h.setdefault("flags", []).append(
                    {"type": "이상감지상향", "level": "info",
                     "msg": f"🔴 이상감지(신호 {sig}) → {h['gradeBase']}→{h['grade']} 상향"})
        if isinstance(drop, (int, float)) and drop >= 0.50:
            h.setdefault("flags", []).append(
                {"type": "삼복승보험강제", "level": "must",
                 "msg": f"🔴 배당 급락 {round(drop * 100)}% → 삼복승 보험 강제 추가"})
    return scored


def score_horses_raw(race, horses, jockey_threshold=None):
    """3-1~3-4: 마필별 baseScore/courseBonus/jockeyBonus/totalScore + flags (등급 전 단계)."""
    if jockey_threshold is None:
        jockey_threshold = top20_threshold([h.get("jockey3mPlaceRate") for h in horses])
    scored = []
    for h in horses:
        placings = _placings_of(h)
        base = base_form_score(placings)
        cb, cd = course_aptitude_bonus(h.get("pastRaces"), race)
        jb, jd = jockey_bonus(h, jockey_threshold)
        flags = special_flags(h, race)
        scored.append({
            "no": h.get("no"), "name": h.get("name", ""), "jockey": h.get("jockey", ""),
            "recentPlacings": placings[:5],
            "baseScore": base, "courseBonus": cb, "jockeyBonus": jb,
            "totalScore": round(base + cb + jb, 1),
            "detail": cd + jd, "flags": flags,
            "anomaly": h.get("anomaly"),
        })
    return scored, jockey_threshold


def compute_horse_scores(race, horses, jockey_threshold=None, anomaly_by_no=None):
    """3-1~3-5 통합. anomaly_by_no(마번→이상감지) 주면 등급 보정에 반영."""
    scored, _ = score_horses_raw(race, horses, jockey_threshold)
    if anomaly_by_no:
        for h in scored:
            if anomaly_by_no.get(h["no"]) is not None:
                h["anomaly"] = anomaly_by_no[h["no"]]
    classify_grades(scored)
    return scored


@app.route("/api/score/form", methods=["POST"])
def score_form():
    """{horses:[{no,name,recentPlacings:[..]}]} → 마필별 기본 점수만(3-1)."""
    body = request.json or {}
    out = [{
        "no": h.get("no"), "name": h.get("name", ""),
        "recentPlacings": _placings_of(h)[:5],
        "baseScore": base_form_score(_placings_of(h)),
    } for h in body.get("horses", [])]
    return jsonify({"horses": out})


@app.route("/api/score", methods=["POST"])
def score():
    """{race:{distance,course,grade}, horses:[{...전적/기수/마체중/이상감지...}]}
    → 3-1~3-5 통합 점수 + 등급 + 플래그."""
    body = request.json or {}
    scored = compute_horse_scores(body.get("race") or {}, body.get("horses") or [])
    return jsonify({"horses": scored})


# ─────────────────────────────────────────
# 통합 분석 엔진 (Phase 4) — 전적 점수 + 배당 이상감지 결합
# ─────────────────────────────────────────
def _anomaly_by_no(race_key, horses_for_edge):
    """odds_compute(전적 총점을 실력 확률로) 결과를 마번→이상감지 dict 로 정리."""
    comp = odds_compute(race_key, horses_for_edge)
    by = {}
    for h in comp["horses"]:
        by[h["no"]] = {
            "signalScore": h["signalScore"], "drop": h["drop"], "edge": h["edge"],
            "lastOdds": h["lastOdds"], "tags": h["tags"],
        }
    return by, comp


def resolve_picks(scored):
    """베팅용 A/B/C/D 픽을 등급→총점 순 상위 4두로 위치 기반 선정(빈 등급에도 성립).
    INS(보험마)는 보험 플래그가 있는 말 중 A·B와 겹치지 않는 것 우선."""
    ranked = sorted(scored, key=lambda h: (GRADES.index(h["grade"]), -h["totalScore"]))
    picks = {g: (ranked[i] if i < len(ranked) else None) for i, g in enumerate(["A", "B", "C", "D"])}
    ab = {picks[g]["no"] for g in ("A", "B") if picks[g]}
    ins_cands = sorted(
        [h for h in scored if any(f["type"] in ("삼복승보험강제", "삼복승보험추가") for f in h.get("flags", []))],
        key=lambda h: ((h.get("anomaly") or {}).get("drop") or 0), reverse=True)
    picks["INS"] = next((h for h in ins_cands if h["no"] not in ab), None)
    return picks


# ── 4-2 등급 최종 보정(배당 신호 반영) ──────
def apply_odds_grade_corrections(scored):
    """Phase3 보정(신호≥75 상향 / 급락50%+ 보험강제) 이후 추가 반영:
      🟡 급락 30~50%: 삼복승 보험 추가 / 🔴 쌍승 역전: A·B 순서 교체 검토.
    반환: 경주 단위 alert 리스트."""
    alerts = []
    for h in scored:
        drop = (h.get("anomaly") or {}).get("drop")
        if isinstance(drop, (int, float)) and 0.30 <= drop < 0.50:
            if not any(f["type"] in ("삼복승보험강제", "삼복승보험추가") for f in h.get("flags", [])):
                h.setdefault("flags", []).append(
                    {"type": "삼복승보험추가", "level": "caution",
                     "msg": f"🟡 배당 급락 {round(drop * 100)}% → 삼복승 보험 추가"})
    ranked = sorted(scored, key=lambda h: (GRADES.index(h["grade"]), -h["totalScore"]))
    if len(ranked) >= 2:
        a, b = ranked[0], ranked[1]
        sa = (a.get("anomaly") or {}).get("signalScore")
        sb = (b.get("anomaly") or {}).get("signalScore")
        if isinstance(sa, (int, float)) and isinstance(sb, (int, float)) and sb >= sa + 10:
            a["reverseReview"] = b["reverseReview"] = True
            alerts.append({"type": "쌍승역전", "level": "warn",
                           "msg": f"🔴 쌍승 역전 감지: {b['no']}번({b['name']}) 배당신호 {sb} "
                                  f"> {a['no']}번({a['name']}) {sa} → A/B 순서 교체 검토"})
    return alerts


# ── 4-3 최종 베팅 조합 생성(비중·금액·손익분기·EV) ──
BET_PLAN = [  # 단승 제거(Phase 5 수정) — 복승/삼복승만, 합 100%
    {"key": "q_ab", "type": "복승", "slots": ["A", "B"], "pct": 43},
    {"key": "q_ac", "type": "복승", "slots": ["A", "C"], "pct": 20},
    {"key": "t_abc", "type": "삼복승", "slots": ["A", "B", "C"], "pct": 29},
    {"key": "t_abins", "type": "삼복승", "slots": ["A", "B", "INS"], "pct": 8},
]


def _pl_topk_prob(strengths, combo):
    """Plackett-Luce: combo의 k마리가 정확히 상위 k착을 차지할 확률(순서 무관)."""
    S = sum(strengths.values())
    if S <= 0:
        return 0.0
    total = 0.0
    for perm in permutations(combo):
        p, denom = 1.0, S
        for no in perm:
            if denom <= 0:
                p = 0.0
                break
            p *= strengths.get(no, 0) / denom
            denom -= strengths.get(no, 0)
        total += p
    return total


def build_bets(picks, scored, budget=0):
    """위치 기반 A/B/C/INS 픽으로 5개 베팅 슬롯 구성 → 비중·금액·손익분기·기대값.
    손익분기 = 예산 ÷ 베팅금액(이 베팅 적중 시 예산 전액 회수에 필요한 배당)."""
    budget = _to_int(budget) or 0
    strengths = {h["no"]: max(h["totalScore"], 1.0) for h in scored}

    bets = []
    for plan in BET_PLAN:
        hs = [picks.get(s) for s in plan["slots"]]
        available = all(hs) and len({h["no"] for h in hs}) == len(hs)
        bet = {"key": plan["key"], "type": plan["type"], "slots": plan["slots"],
               "weightPct": plan["pct"], "available": available,
               "combo": [h["no"] for h in hs if h], "labels": [h["name"] for h in hs if h],
               "amount": 0, "modelProb": None, "fairOdds": None,
               "breakevenOdds": None, "evPct": None, "note": ""}
        if not available:
            bet["note"] = "보험마 없음" if "INS" in plan["slots"] else "출전마 부족"
            bets.append(bet)
            continue
        p = _pl_topk_prob(strengths, bet["combo"])
        bet["modelProb"] = round(p, 4)
        bet["fairOdds"] = round(1 / p, 1) if p > 0 else None
        if budget:
            bet["amount"] = int(round(budget * plan["pct"] / 100))
            bet["breakevenOdds"] = round(budget / bet["amount"], 1) if bet["amount"] else None
        bets.append(bet)

    allocated = sum(b["amount"] for b in bets if b["available"])
    summary = {"budget": budget, "allocated": allocated, "unallocated": budget - allocated}
    return bets, summary


def combined_analyze(race_key, race, horses, snaps=None, budget=0):
    """4-1 데이터 통합 + 4-2 등급 보정 + 4-3 베팅 생성.
    전적 총점을 배당 괴리(edge) 계산의 실력 확률로 사용한 뒤, 이상감지로 등급을 재보정한다."""
    # 1) 배당 스냅샷이 함께 오면 해당 raceKey 시계열을 새로 기록
    if snaps is not None:
        odds_clear(race_key)
        for s in snaps:
            odds_add_snapshot(race_key, (s or {}).get("odds") or s or {})

    # 2) 전적 점수(등급 전) — edge 계산용 totalScore 확보
    scored, jt = score_horses_raw(race, horses)
    # 3) 배당 이상감지 — 전적 총점을 실력 확률로
    edge_horses = [{"no": h["no"], "name": h["name"], "score": h["totalScore"]} for h in scored]
    anomaly_by, comp = _anomaly_by_no(race_key, edge_horses)
    # 4) 이상감지 반영해 등급 재산정(Phase3 보정 포함)
    for h in scored:
        if anomaly_by.get(h["no"]) is not None:
            h["anomaly"] = anomaly_by[h["no"]]
    classify_grades(scored)
    # 4-2) 추가 보정(🟡 30%+ 보험 / 🔴 쌍승 역전)
    alerts = apply_odds_grade_corrections(scored)
    # 4-3) 베팅 조합(위치 기반 픽)
    picks = resolve_picks(scored)
    bets, bet_summary = build_bets(picks, scored, budget)
    picks_out = {g: (picks[g]["no"] if picks[g] else None) for g in ("A", "B", "C", "D", "INS")}
    return scored, comp, alerts, bets, bet_summary, picks_out


@app.route("/api/analyze/combined", methods=["POST"])
def analyze_combined():
    """{raceKey, race, horses(전적), oddsSnapshots(배당), budget} → 통합 점수+등급+이상감지+베팅."""
    body = request.json or {}
    race_key = (body.get("raceKey") or "").strip() or "통합분석"
    scored, comp, alerts, bets, bet_summary, picks = combined_analyze(
        race_key, body.get("race") or {}, body.get("horses") or [],
        body.get("oddsSnapshots"), body.get("budget") or 0)
    return jsonify({
        "raceKey": race_key,
        "horses": scored,
        "picks": picks,
        "alerts": alerts,
        "bets": bets,
        "betSummary": bet_summary,
        "odds": {"snapCount": comp["snapCount"], "hasSeries": comp["hasSeries"],
                 "hasOdds": comp["hasOdds"], "horses": comp["horses"]},
    })


@app.errorhandler(Exception)
def on_error(e):
    # HTTP 예외(404/405 등)는 원래 상태코드를 보존 — 405가 500으로 둔갑하던 버그 수정
    if isinstance(e, HTTPException):
        return jsonify({"error": f"{e.code} {e.name}: {e.description}"}), e.code
    return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    print("서버 시작: http://127.0.0.1:8011 (자동 리로드 ON, 코드 수정이 바로 반영됩니다)")
    # debug=True: 코드 저장 시 자동 재기동(stale 서버로 인한 405 재발 방지). 로컬 전용(127.0.0.1).
    # threaded=True: 브라우저의 다중 keep-alive 연결을 동시 처리(단일 스레드 멈춤 방지).
    app.run(host="127.0.0.1", port=8011, debug=True, threaded=True)
