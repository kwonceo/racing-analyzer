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
import base64
import threading
import subprocess
from itertools import permutations
from html.parser import HTMLParser
from urllib.request import urlopen, Request
from flask import Flask, request, jsonify, send_from_directory
from werkzeug.exceptions import HTTPException
import anthropic
try:
    import fitz  # PyMuPDF — 서버측 PDF 렌더(한국경마 백그라운드 분석)
except Exception as _fitz_err:  # noqa: N816
    # PyMuPDF 미설치여도 서버는 반드시 기동한다.
    # (import 실패로 서버가 안 뜨면, 포트에 남은 구버전 서버가 요청을 받아
    #  '/api/korea/start' 에 405 를 반환하던 것이 405 재발의 실제 원인이었다.)
    fitz = None
    _FITZ_IMPORT_ERROR = str(_fitz_err)
    print("[경고] PyMuPDF(fitz) import 실패 — 한국경마 PDF 분석만 비활성화됩니다:", _FITZ_IMPORT_ERROR)
    print("       설치:  pip install PyMuPDF")
else:
    _FITZ_IMPORT_ERROR = None

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
                    "has_key": bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
                    "pdf_ready": fitz is not None,
                    "pdf_error": _FITZ_IMPORT_ERROR})


def _do_extract_jockey(img, api_key=None):
    prompt = (
        '이 이미지는 KRA 출주표의 "기수 기승현황표"입니다.\n'
        "표의 모든 기수 행을 읽어 JSON으로 추출하세요.\n"
        "각 기수: name(기수명), total(누적 총 기승수), w1/w2/w3(누적 1/2/3착), "
        "month(당월 기승수), mW1/mW2/mW3(당월 1/2/3착).\n"
        "숫자를 못 읽으면 0. 기수현황표가 아니면 jockeys를 빈 배열로."
    )
    return call_claude([{"type": "text", "text": prompt}, image_block(img)],
                       JOCKEY_SCHEMA, 8192, api_key)


@app.route("/api/extract/jockey", methods=["POST"])
def extract_jockey():
    body = request.json or {}
    return jsonify(_do_extract_jockey(body.get("image"), body.get("api_key")))


def _do_extract_race(img, api_key=None):
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
    return call_claude([{"type": "text", "text": prompt}, image_block(img)],
                       RACE_SHEET_SCHEMA, 8192, api_key)


@app.route("/api/extract/race", methods=["POST"])
def extract_race():
    body = request.json or {}
    return jsonify(_do_extract_race(body.get("image"), body.get("api_key")))


def _do_extract_training(img, api_key=None):
    prompt = (
        "이 이미지는 KRA 출주표 하단의 '조교훈련 및 종합분석' 표입니다. 각 행이 한 마리입니다.\n"
        "- horseNum: 마번.\n"
        "- rating: '레이팅' 칸의 숫자(예 84, 91, 107). 마명 옆 나이(개월)와 혼동 금지.\n"
        "- trainer: 조교사명.\n"
        "- mark: 행 우측 종합 평가 기호(★◎○△※ 중 하나), 없으면 ''.\n"
        "칸이 비면 ''. 모든 출전마를 마번과 함께 반환."
    )
    return call_claude([{"type": "text", "text": prompt}, image_block(img)],
                       TRAINING_SCHEMA, 4096, api_key)


@app.route("/api/extract/training", methods=["POST"])
def extract_training():
    """출주표 하단 '조교훈련 및 종합분석' 표 → 마번별 레이팅/조교사/평가기호"""
    body = request.json or {}
    return jsonify(_do_extract_training(body.get("image"), body.get("api_key")))


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


def _do_detect(imgs, api_key=None):
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
    return call_claude(content, DETECT_SCHEMA, 2048, api_key)


@app.route("/api/detect", methods=["POST"])
def detect():
    """여러 페이지 썸네일을 순서대로 받아 race/jockey/other 분류"""
    body = request.json or {}
    return jsonify(_do_detect(body.get("images", []), body.get("api_key")))


def _do_analyze(race, jstats, api_key=None):
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
    return call_claude([{"type": "text", "text": prompt}], ANALYSIS_SCHEMA, 4096, api_key)


@app.route("/api/analyze", methods=["POST"])
def analyze():
    body = request.json or {}
    return jsonify(_do_analyze(body.get("raceData", {}), body.get("jockeyStats", {}), body.get("api_key")))


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
    final_odds = body.get("finalOdds") or None

    db = _results_load()
    db[race_key] = {"results": norm, "top3": top3, "finalOdds": final_odds,
                    "source": body.get("source") or "extension", "t": time.time()}
    _results_save(db)
    print(f"[결과 자동수집] {race_key}: 1~3착 {top3}"
          + (f" · 확정배당 {final_odds}" if final_odds else ""))

    # [3번] 결과 수신 즉시 자동학습 반영(이상감지·추천·전적유력마·제거 적중 판정 → learning.json)
    learned, hit = None, None
    try:
        result = {}
        for x in norm:
            if x["rank"] == 1:
                result["1st"] = x["no"]
            elif x["rank"] == 2:
                result["2nd"] = x["no"]
            elif x["rank"] == 3:
                result["3rd"] = x["no"]
        _rec, learned = _apply_result_learning(race_key, result, top3, final_odds)
        hit = {"quinella": _rec.get("quinella_hit"), "trifecta": _rec.get("trifecta_hit"),
               "was_hit": _rec.get("was_hit"), "payouts": _rec.get("payouts")}
    except Exception as e:
        print(f"[결과 자동수집] 자동학습 반영 실패: {e}")

    return jsonify({"ok": True, "saved": len(norm), "top3": top3, "learned": learned, "hit": hit})


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


def _horse_has_form(h):
    """이 마필 항목이 실제 전적(착순/전적점수)을 담고 있는가."""
    if h.get("recent") or h.get("recentPlacings"):
        return True
    return (h.get("formScore") is not None) or (h.get("totalScore") is not None)


def _rec_form_count(rec):
    """레코드 안에서 전적이 채워진 마필 수."""
    return sum(1 for h in ((rec or {}).get("horses") or []) if _horse_has_form(h))


def _sanitize_starters(horses):
    """출마표2/DebaTable 파서 오탐(오즈표·전체목록 스크레이핑으로 수백 행이 딸려오는 경우) 방어.
    - 마번(no) 1~18 범위만 유효 · 마번 기준 중복 제거(전적 있는 항목 우선 보존)
    - 한 경주 출전마는 최대 18두이므로 334행 같은 쓰레기 입력을 정상 규모로 축소한다.
    반환: 정제된 리스트(마번 오름차순)."""
    by_no = {}
    for h in horses or []:
        try:
            no = int(h.get("no"))
        except (TypeError, ValueError):
            continue
        if no < 1 or no > 18:
            continue
        prev = by_no.get(no)
        if prev is None or (_horse_has_form(h) and not _horse_has_form(prev)):
            by_no[no] = h
    return [by_no[k] for k in sorted(by_no)]


def _form_from_starters(rk, drops):
    """저장된 전적으로 마필 점수·등급 계산. 배당 급락마는 이상감지 상향 반영.
    - 일본(출마표2): recent 착순으로 점수 재계산
    - 한국(PDF): 프론트에서 이미 계산한 formScore/totalScore를 그대로 사용(마명·기수 한글 유지)."""
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
    raw = rec["horses"]
    # [한국경마] 사전 계산된 전적점수가 있으면 그대로 통과(PDF Vision 한글 데이터)
    prescored = any(h.get("formScore") is not None or h.get("totalScore") is not None for h in raw)
    if rec.get("source") == "korea" or prescored:
        scored = []
        for h in raw:
            ts = h.get("totalScore")
            if ts is None:
                ts = h.get("formScore")
            no = h.get("no")
            an = anomaly_by_no.get(int(no)) if no is not None else None
            scored.append({
                "no": no, "name": h.get("name", ""), "jockey": h.get("jockey", ""),
                "recentPlacings": (h.get("recent") or h.get("recentPlacings") or [])[:5],
                "baseScore": round(ts or 0, 1), "courseBonus": 0, "jockeyBonus": 0,
                "totalScore": round(ts or 0, 1), "detail": [], "flags": [], "anomaly": an,
            })
        classify_grades(scored)
        scored.sort(key=lambda x: -x["totalScore"])
        return scored
    # [일본경마] 출마표2 착순으로 재계산
    horses = [{"no": h.get("no"), "name": h.get("name", ""), "jockey": h.get("jockey", ""),
               "recentPlacings": h.get("recent") or [], "currentWeight": h.get("weight")}
              for h in raw]
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
    win = _win_map_clean(body.get("win"))   # [단승] {마번(str): 배당}
    db = _triple_load()
    prev = db.get(rk) or {}
    now = time.time()
    # 변동 추적용 히스토리(최근 12회) — 직전 대비 급락/순위/역전 계산에 사용
    hist = (prev.get("history") or [])
    hist.append({"t": now, "quinella": q, "exacta": x, "trio": tr, "win": win})
    hist = hist[-12:]
    db[rk] = {"quinella": q, "exacta": x, "trio": tr, "win": win, "history": hist,
              "source": body.get("source"), "t": now}
    _triple_save(db)
    # 배당 변동 히스토리 파일에 스냅샷 누적 (타임스탬프+발주전분+이상감지)
    try:
        _history_append(rk, q, x, body.get("deadline"), win)
    except Exception as e:
        print("[히스토리] 기록 실패:", e)
    counts = {"quinella": len(q), "exacta": len(x), "trio": len(tr), "win": len(win)}
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


@app.route("/api/current_race", methods=["GET"])
def current_race():
    """확장이 마지막으로 수집한 '현재 경주' 반환 → {raceKey, updatedAt, counts}.
    분석기 상단 '경주 새로고침' 바가 폴링해 현재 경주명을 표시·자동 전환한다.
    (배당 본문 없이 경주명만 필요하므로 triple/latest 보다 가볍다.)"""
    db = _triple_load()
    if not db:
        return jsonify({"raceKey": None})
    rk = max(db.keys(), key=lambda k: db[k].get("t", 0))
    rec = db.get(rk) or {}
    return jsonify({
        "raceKey": rk,
        "updatedAt": rec.get("t"),
        "counts": {"quinella": len(rec.get("quinella") or []),
                   "exacta": len(rec.get("exacta") or []),
                   "trio": len(rec.get("trio") or [])},
    })


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


def _win_map_clean(w):
    """단승 배당 입력({마번:배당} dict 또는 [{no,win}] list) → {마번(str): float} 정규화."""
    out = {}
    if isinstance(w, dict):
        items = w.items()
    elif isinstance(w, list):
        items = [(it.get("no"), it.get("win", it.get("odds"))) for it in w if isinstance(it, dict)]
    else:
        return out
    for k, v in items:
        try:
            no = int(k)
            od = float(v)
        except (TypeError, ValueError):
            continue
        if 1 <= no <= 30 and od > 0:
            out[str(no)] = round(od, 1)
    return out


def _win_map_int(w):
    """{마번(str):배당} → {마번(int):배당}."""
    m = {}
    for k, v in (w or {}).items():
        try:
            m[int(k)] = float(v)
        except (TypeError, ValueError):
            continue
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
        o_txt = ('%g배' % o) if o is not None else '미수집'
        if ftotal is None:  # 전적 미수집 → 100점 기본값이 아니라 '배당 기준' 판단임을 명시
            reason = f"배당 {o_txt}({os_}) · 전적 미수집 → 배당만으로 {total}"
        else:
            reason = f"배당 {o_txt}({os_}) + 전적 {ftotal}({'+' if fadj > 0 else ''}{fadj}) = {total}"
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
        "formAvailable": bool(form_by_no),                       # 전적 데이터 존재 여부
        "formCount": len(form_by_no),                            # 전적 있는 말 수
    }


def _parse_combo_map(qd):
    """히스토리 스냅샷의 quinella dict('1+2':45.2) → {(1,2):45.2}."""
    out = {}
    for k, v in (qd or {}).items():
        try:
            out[tuple(sorted(int(x) for x in k.split("+")))] = float(v)
        except (ValueError, TypeError):
            pass
    return out


# [한국경마 급락 기준] 마감 임박일수록 작은 급락도 민감하게:
#   10분전 대비 20%↑ → 🟡 주의 / 5분전 대비 15%↑ → 🟠 주의 / 2분전 대비 10%↑ → 🔴 급락
KR_DROP_BANDS = [(10, 0.20, "🟡"), (5, 0.15, "🟠"), (2, 0.10, "🔴")]
_TIER_RANK = {"🟡": 1, "🟠": 2, "🔴": 3}


def _time_based_drop_signals(rk):
    """경주 히스토리(발주전분 기록)로 시간대별 마감 임박 급락 신호 생성.
    최신 스냅샷을 각 시간대(10/5/2분전) 기준 스냅샷과 비교. 발주시각 미설정이면 빈 리스트."""
    try:
        path, _, _ = _hist_path(rk)
        doc = json.load(open(path, encoding="utf-8"))
    except Exception:
        return []
    snaps = [s for s in (doc.get("snapshots") or []) if s.get("minutes_before") is not None]
    if len(snaps) < 2:
        return []
    latest = snaps[-1]
    curQ = _parse_combo_map(latest.get("quinella"))
    if not curQ:
        return []

    def _ref(minutes):
        # minutes분 이상 전 스냅샷 중 minutes에 가장 가까운 것(없으면 None)
        cands = [s for s in snaps[:-1] if (s.get("minutes_before") or 0) >= minutes]
        return min(cands, key=lambda s: s["minutes_before"] - minutes) if cands else None

    best = {}  # combo → {level, band, prev, cur, pct} (가장 강한 티어만 유지)
    for minutes, thr, icon in KR_DROP_BANDS:
        ref = _ref(minutes)
        if not ref:
            continue
        refQ = _parse_combo_map(ref.get("quinella"))
        for k, o in curQ.items():
            po = refQ.get(k)
            if po and po > 0 and o > 0:
                drop = (po - o) / po  # 양수 = 배당 하락(자금유입)
                if drop >= thr:
                    cand = {"level": icon, "band": minutes, "prev": round(po, 1),
                            "cur": round(o, 1), "pct": round(-drop * 100, 1)}
                    cur = best.get(k)
                    if cur is None or _TIER_RANK[icon] > _TIER_RANK[cur["level"]]:
                        best[k] = cand
    reasons = {"🟡": "마감 10분전 대비 급락 → 자금 유입 초기(관찰)",
               "🟠": "마감 5분전 대비 급락 → 자금 유입 가속(주목)",
               "🔴": "마감 2분전 대비 급락 → 마감 임박 확정성 높음"}
    out = [{"level": d["level"], "type": "마감급락", "combo": list(k),
            "text": f"{k[0]}+{k[1]} 복승 {d['prev']}→{d['cur']} ({d['band']}분전 대비 {d['pct']}%)",
            "detail": reasons.get(d["level"], "")}
           for k, d in best.items()]
    out.sort(key=lambda s: _TIER_RANK.get(s["level"], 0), reverse=True)
    return out


def _integrated_grades(form, curQ, curD):
    """[통합분석] 전적 40% + 배당 60% 결합 점수 → A/B/C/D 재부여.
    form=[{no,name,jockey,totalScore}]. 배당 대표값=해당 말이 낀 최저 복승(없으면 쌍승)배당."""
    if not form:
        return None
    repr_odds = {}
    for (a, b), o in (curQ or {}).items():
        for h in (a, b):
            if o > 0 and (repr_odds.get(h) is None or o < repr_odds[h]):
                repr_odds[h] = o
    if not repr_odds:
        for (a, b), o in (curD or {}).items():
            for h in (a, b):
                if o > 0 and (repr_odds.get(h) is None or o < repr_odds[h]):
                    repr_odds[h] = o
    out = []
    for h in form:
        no = h.get("no")
        fscore = max(0.0, min(100.0, float(h.get("totalScore") or 0)))
        o = repr_odds.get(int(no)) if no is not None else None
        oscore = _odds_score(o)
        integ = round(0.4 * fscore + 0.6 * oscore, 1)
        out.append({"no": no, "name": h.get("name", ""), "jockey": h.get("jockey", ""),
                    "formScore": round(fscore, 1), "oddsScore": oscore,
                    "odds": o, "integrated": integ})
    out.sort(key=lambda x: -x["integrated"])
    n = len(out)
    for i, h in enumerate(out):
        frac = i / n if n else 0
        h["grade"] = "A" if frac < 0.25 else "B" if frac < 0.50 else "C" if frac < 0.75 else "D"
    return out


# ══════════ [패턴 학습 강화 시스템] 이상감지 패턴 태깅·매칭·비중조정 ══════════
#   결과 입력 시 패턴 태그 저장 → 패턴별/시점별 적중률 집계 → 현재 경주 패턴 매칭·베팅 비중.
def _pattern_timing_bucket(mb):
    """급락 발생 시점을 T-N분 버킷으로. (발주전 분)"""
    if mb is None:
        return "미상"
    for b in (1, 2, 3, 5, 10):
        if mb <= b + 0.5:
            return f"T-{b}분"
    return "T-10분+"


def _extract_patterns(drops, reversals, signals, curQ, bet_rec):
    """[1·5번] 분석 결과에서 이상감지 패턴 태그 추출."""
    pats = []
    min_pct = min((d.get("pct") for d in (drops or []) if d.get("pct") is not None), default=0)
    if min_pct <= -50:
        pats.append("급락50+")
    elif min_pct <= -30:
        pats.append("급락30+")
    if any(r.get("flipped") for r in (reversals or [])):
        pats.append("쌍승역전")
    if any(s.get("type") == "압축" for s in (signals or [])):
        pats.append("배당압축")
    # 복승불일치: 추천 복승메인 조합 vs 시장 최저배당 복승 조합이 다를 때(전적유력마 ↔ 배당인기 불일치)
    if curQ and bet_rec:
        market_top = min(curQ.items(), key=lambda kv: kv[1])[0]
        main = next((b for b in bet_rec if b.get("label") == "복승 메인"), None)
        if main and tuple(sorted(int(x) for x in main["combo"])) != tuple(sorted(market_top)):
            pats.append("복승불일치")
    return pats


def _pattern_confidence(cur_patterns, pstats):
    """[5번] 매칭된 패턴들의 과거 적중률(표본가중 평균)로 신뢰도 산출. (n>=5 패턴만)"""
    rated = [(p, pstats[p]) for p in cur_patterns
             if p in pstats and (pstats[p].get("n") or 0) >= 5 and pstats[p].get("rate") is not None]
    if not rated:
        return {"level": "데이터부족", "rate": None, "n": 0}
    tot_n = sum(d["n"] for _, d in rated)
    avg = round(sum(d["rate"] * d["n"] for _, d in rated) / tot_n, 1)
    lvl = "높음" if avg >= 65 else "보통" if avg >= 50 else "주의" if avg >= 40 else "낮음"
    return {"level": lvl, "rate": avg, "n": tot_n}


def _scale_alloc(bet_rec, label, target):
    """[4번] 특정 베팅(label)의 alloc 을 target% 로 두고 나머지를 비례 재분배(합 100 유지)."""
    main = next((b for b in bet_rec if b.get("label") == label), None)
    if not main:
        return
    others = [b for b in bet_rec if b is not main]
    rest = sum(b.get("alloc", 0) for b in others)
    main["alloc"] = target
    if rest > 0:
        f = (100 - target) / rest
        for b in others:
            b["alloc"] = round(b.get("alloc", 0) * f)


def _adjust_bet_weights(bet_rec, conf):
    """[4번] 패턴 신뢰도로 복승/삼복승 비중 자동 조정."""
    lvl = conf.get("level")
    if not bet_rec or lvl in (None, "데이터부족"):
        return None
    if lvl == "높음":
        _scale_alloc(bet_rec, "복승 메인", 50)   # 복승 메인 비중 상향
        return {"note": f"신뢰도 높음({conf['rate']}%) → 복승 메인 비중 상향(50%)", "adjusted": True}
    if lvl == "낮음":
        _scale_alloc(bet_rec, "복승 메인", 33)   # 복승 하향 → 삼복승(보험 포함) 비중 상향
        return {"note": f"신뢰도 낮음({conf['rate']}%) → 복승 비중 하향·삼복승 보험 비중 상향", "adjusted": True}
    return {"note": f"신뢰도 {lvl}({conf.get('rate')}%) → 기본 비중 유지", "adjusted": False}


def _triple_analyze(rk, rec):
    quin = rec.get("quinella") or []
    exa = rec.get("exacta") or []
    trio = rec.get("trio") or []
    hist = rec.get("history") or []
    prev = hist[-2] if len(hist) >= 2 else None  # 직전 수집

    curQ = _odds_map_un(quin)
    prevQ = _odds_map_un(prev.get("quinella")) if prev else {}

    # [단승] 현재/직전 단승 배당 + 급락 (가장 강한 신호)
    curWin = _win_map_int(rec.get("win"))
    prevWin = _win_map_int(prev.get("win")) if prev else {}
    single_drops = []
    for no, o in curWin.items():
        po = prevWin.get(no)
        if po and po > 0:
            pct = round((o - po) / po * 100, 1)
            if abs(pct) >= 8:
                single_drops.append({"no": no, "prev": po, "cur": o, "pct": pct})
    single_drops.sort(key=lambda d: d["pct"])   # 가장 큰 급락 먼저
    # [3번] 단승 배당 순위(낮을수록 인기) — 유력마 자동 산출용
    single_rank = [no for no, _ in sorted(curWin.items(), key=lambda kv: kv[1])]

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
    # [3번] 단승 배당이 있으면 그 순위를 유력마 1순위 기준으로 사용(가장 직접적인 시장 신호).
    #  단승 순위를 앞세우고, 복승 빈도 순위를 뒤에 이어붙여 3마리를 채운다.
    if single_rank:
        merged = single_rank + [h for h in ranked if h not in single_rank]
        ranked = merged
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

    # [2번] 단승 급락 = 가장 강한 신호 → 복승 조합 알림보다 먼저 표시
    for d in single_drops:
        lvl, reason = _drop_reason(d["pct"])
        if lvl:
            signals.append({"level": lvl, "type": "단승급락", "horse": d["no"],
                            "text": f"{d['no']}번 단승 {d['prev']}→{d['cur']} ({d['pct']}%)",
                            "detail": reason})
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

    form = _form_from_starters(rk, drops)  # 출마표2/KRA/PDF 전적 등급(있으면)
    elimination = _elimination(curQ, curD, exa, drops, form, trio_map)  # 배당+전적 복합 제거

    # [한국경마] 시간대별(발주 10/5/2분전 대비) 마감 임박 급락 신호를 앞쪽에 병합
    time_drops = _time_based_drop_signals(rk)
    if time_drops:
        signals = time_drops + signals
    # [통합분석] 전적 40% + 배당 60% 통합 등급(전적이 있을 때만)
    integrated = _integrated_grades(form, curQ, curD)
    # [단승] 통합 등급 행에 단승 배당 부착(타임라인/화면 표시용)
    for h in integrated or []:
        h["win"] = curWin.get(h.get("no"))

    # [5번] 현재 경주 패턴 매칭 + [4번] 신뢰도 기반 베팅 비중 자동 조정
    pattern_match = None
    try:
        cur_patterns = _extract_patterns(drops, reversals, signals, curQ, bet_rec)
        if cur_patterns:
            _pstats = (_learning_load().get("stats", {}) or {}).get("pattern_stats") or {}
            matched = [dict({"pattern": p}, **(_pstats.get(p) or {})) for p in cur_patterns]
            conf = _pattern_confidence(cur_patterns, _pstats)
            bet_advice = _adjust_bet_weights(bet_rec, conf)   # bet_rec 의 alloc 조정(제자리)
            pattern_match = {"patterns": cur_patterns, "matched": matched, "confidence": conf,
                             "betAdvice": bet_advice,
                             "recommend": conf.get("level") in ("높음", "보통")}
    except Exception as _e:
        print("[패턴매칭] 실패:", _e)

    return {
        "raceKey": rk, "hasPrev": bool(prev),
        "counts": {"quinella": len(quin), "exacta": len(exa), "trio": len(trio),
                   "win": len(curWin), "history": len(hist)},
        "drops": drops[:15], "singleDrops": single_drops[:15], "rankChanges": rank_changes, "reversals": reversals,
        "keyHorses": key_horses, "anomalyHorse": anomaly_horse,
        "single": {str(k): v for k, v in curWin.items()}, "singleRanking": single_rank,
        "trioRecommend": trio_rec, "betRecommend": bet_rec,
        "summary": summary, "chart": chart,
        "form": form,
        "elimination": elimination,  # 배당+전적 복합 제거/후보 판정
        "integrated": integrated,    # 전적40%+배당60% 통합 등급(A/B/C/D)
        "learned": learned,  # 학습 통계 안내(있으면)
        "signals": signals, "lastSnapshot": last_snap,  # 실시간 변동 알림용
        "patternMatch": pattern_match,  # [4·5번] 현재 패턴 매칭 + 신뢰도 + 베팅 비중 조정
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
    raw_horses = body.get("horses") or []
    horses = _sanitize_starters(raw_horses)   # [전적복구] 오즈표/전체목록 오탐 방어(중복 마번 제거·1~18만)
    sdb = _starters_load()
    prev = sdb.get(rk)
    new_form = sum(1 for h in horses if _horse_has_form(h))
    prev_form = _rec_form_count(prev)
    # [전적복구] 전적 0두인 새 수집이 기존 전적(한국 PDF/이전 출마표2)을 덮어쓰지 못하게 보호.
    #   배당 갱신은 아래에서 계속 진행하되, starters(전적)만 기존 것을 유지한다.
    if prev and prev_form > 0 and new_form == 0:
        print(f"[출마표2 전적] {rk}: 새 수집 전적 0두 · 원본 {len(raw_horses)}행 → 기존 전적 {prev_form}두 보존(덮어쓰기 방지)")
    else:
        if len(raw_horses) != len(horses):
            print(f"[출마표2 전적] {rk}: 입력 {len(raw_horses)}행 → 정제 {len(horses)}두(중복·범위밖 제거)")
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
    print(f"[출마표2 전적] {rk}: 전적 {new_form}두 반영(수신 {len(horses)}두)")
    return jsonify(_triple_analyze(rk, trec))


@app.route("/api/korea/form", methods=["POST"])
def korea_form():
    """[한국경마] PDF Vision 전적을 STARTERS_STORE에 저장(마명·기수 한글 그대로, 전적점수 포함).
    body:{raceKey, horses:[{no,name,jockey,formScore(또는 totalScore),recentPlacings}]} → {ok,count}.
    저장 후 같은 raceKey로 배당이 수집되면 /api/odds/triple/analyze 가 통합(전적+배당) 결과를 낸다."""
    body = request.json or {}
    rk = (body.get("raceKey") or "").strip()
    if not rk:
        return jsonify({"error": "raceKey가 필요합니다."}), 400
    horses = _sanitize_starters(body.get("horses") or [])   # [전적복구] 중복 마번 제거
    sdb = _starters_load()
    sdb[rk] = {"horses": horses, "t": time.time(), "source": "korea"}
    _starters_save(sdb)
    print(f"[한국 전적] {rk}: {len(horses)}두 저장(PDF, 전적 {sum(1 for h in horses if _horse_has_form(h))}두)")
    return jsonify({"ok": True, "count": len(horses), "raceKey": rk})


def _rk_venue_num(k):
    """raceKey/경주명에서 (경마장, 경주번호) 추출. 예:'2026-07-03 서울 5R' → ('서울',5)."""
    m = re.search(r"(\d+)\s*(?:R\b|경주|레이스)", k or "", re.IGNORECASE)
    num = int(m.group(1)) if m else None
    venue = None
    for v in ("부산경남", "부경", "부산", "서울", "제주"):
        if v in (k or ""):
            venue = "부경" if v in ("부산경남", "부산", "부경") else v
            break
    return venue, num


# [한국 배당연결] 일본 지방경마장(한자/한글 표기) — 한국 경주가 같은 번호의
#   일본 잔여 키(예:'후나바시 3경주')에 잘못 매칭되는 것을 막기 위한 목록.
_JP_TRACKS = (
    "船橋", "大井", "名古屋", "園田", "高知", "帯広", "門別", "盛岡", "水沢",
    "浦和", "川崎", "金沢", "笠松", "姫路", "佐賀",
    "후나바시", "오이", "나고야", "소노다", "고치", "가와사키", "우라와",
    "가나자와", "카사마츠", "히메지", "사가", "모리오카", "미즈사와", "오비히로", "몬베츠",
)


def _is_japan_key(k):
    """raceKey에 일본 경마장명이 들어있으면 True(한국 경주 매칭에서 제외용)."""
    return any(t in (k or "") for t in _JP_TRACKS)


@app.route("/api/odds/triple/match", methods=["POST"])
def triple_match():
    """[한국경마 자동연결] 경마장·경주번호로 수집된 배당 raceKey를 찾아 통합분석 반환.
    body:{title?, venue?, num?} → {matched, raceKey?, analysis?, candidates}.
    확장이 '서울 5R'로 수집하면 PDF '서울 5경주'와 번호로 자동 매칭된다."""
    body = request.json or {}
    num = body.get("num")
    venue = (body.get("venue") or "").strip()
    if num is None and body.get("title"):
        v2, n2 = _rk_venue_num(body["title"])
        venue, num = (venue or v2 or ""), n2
    if venue in ("부산경남", "부산", "부경"):
        venue = "부경"
    db = _triple_load()
    keys = sorted(db.keys(), key=lambda k: db[k].get("t", 0), reverse=True)
    if not db:
        return jsonify({"matched": False, "reason": "no_data", "candidates": []})
    if num is None:
        return jsonify({"matched": False, "reason": "no_num", "candidates": keys[:8]})
    # [한국 배당연결 강화] 번호가 같은 후보 중에서
    #   ① 경마장까지 정확히 일치하는 키를 최우선,
    #   ② 없으면 경마장 미상(사용자가 번호만 입력)인 한국계 키,
    #   순으로 매칭한다. 한국 경주(venue 있음)는 같은 번호의 일본 잔여 키에는
    #   절대 매칭되지 않도록 _is_japan_key 로 제외 → 엉뚱한 외국 배당 표시 방지.
    exact, loose = None, None
    for k in keys:  # 최신 우선
        kv, kn = _rk_venue_num(k)
        if kn != int(num):
            continue
        if venue and _is_japan_key(k):
            continue  # 한국 탭인데 일본 경마장 키 → 스킵
        if venue and kv == venue:
            exact = k
            break
        if loose is None and (not venue or not kv):
            loose = k
    match = exact or loose
    if not match:
        return jsonify({"matched": False, "reason": "no_match", "candidates": keys[:8]})
    return jsonify({"matched": True, "raceKey": match,
                    "analysis": _triple_analyze(match, db.get(match) or {})})


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
    an = _triple_analyze(rk, db.get(rk) or {})
    # [복기] 분석 시점의 전적/제거/신호/추천을 히스토리 파일에 보존(통계 탭 복기용)
    try:
        _history_save_analysis(rk, an)
    except Exception as e:
        print("[복기저장] 실패:", e)
    # [분석 로그] 배당 수집·이상감지·추천이 갱신될 때마다 완전 로그 갱신(추적 가능 기록)
    _analysis_log_save(rk, an)
    return jsonify(an)


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


def _history_append(rk, quinella, exacta, deadline=None, win=None):
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
    curWin = _win_map_int(_win_map_clean(win))
    anomalies = []
    if doc["snapshots"]:
        last = doc["snapshots"][-1]
        # [단승] 급락 감지 — 가장 강한 신호이므로 먼저 기록
        prevWin = {}
        for k, v in (last.get("win") or {}).items():
            try:
                prevWin[int(k)] = float(v)
            except (TypeError, ValueError):
                pass
        for no, o in sorted(curWin.items()):
            po = prevWin.get(no)
            if po and po > 0:
                pct = round((o - po) / po * 100)
                if pct <= -15:
                    anomalies.append(f"단승급락: {no}번 {po}→{o} ({pct}%)")
        prevQ = {}
        for k, v in (last.get("quinella") or {}).items():
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
        "win": {str(k): v for k, v in curWin.items()},
        "anomalies": anomalies, "t": now,
    })
    doc["snapshots"] = doc["snapshots"][-300:]
    json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    return path


def _history_save_analysis(rk, an):
    """[경주별 복기] 분석 시점의 전적점수·제거/후보·이상감지 신호·최종 베팅추천을
    경주별 히스토리 파일(data/odds_history)에 함께 보존한다. 통계 탭 '복기' 섹션이
    '당시 분석 전체'를 그대로 재현할 수 있게 하기 위함(스냅샷 누적과는 별도)."""
    if not rk or not an:
        return
    path, date, race = _hist_path(rk)
    try:
        doc = json.load(open(path, encoding="utf-8"))
    except Exception:
        doc = {"race": race, "date": date, "raceKey": rk, "snapshots": [], "result": None}
    elim = an.get("elimination") or {}
    ehorses = elim.get("horses") or []
    candidates = [h["no"] for h in ehorses if (h.get("keep") or h.get("override"))]
    eliminated = [h["no"] for h in ehorses if not (h.get("keep") or h.get("override"))]
    bets = an.get("betRecommend") or []

    def _pick(label):
        r = next((b for b in bets if b.get("label") == label), None)
        return "+".join(map(str, r["combo"])) if r else None

    doc["analysis"] = {
        "keyHorses": an.get("keyHorses") or [],
        "anomalyHorse": an.get("anomalyHorse"),
        "form": an.get("form") or [],                       # 전적 점수(등급/점수/플래그 포함)
        "elimination": {"candidates": candidates, "eliminated": eliminated,
                        "horses": ehorses, "counts": elim.get("counts")},
        "signals": [s.get("text") for s in (an.get("signals") or []) if s.get("text")],
        "signalsDetail": an.get("signals") or [],
        "betRecommend": bets,
        "final_recommend": {
            "quinella_main": _pick("복승 메인"),
            "quinella_sub": _pick("복승 보조"),
            "trifecta_main": _pick("삼복승 메인"),
        },
        "summary": an.get("summary"),
        "at": time.time(),
    }
    try:
        json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception as e:
        print("[복기저장] 실패:", e)


# ══════════════ [분석 로그 완전 저장] data/analysis_log/ (추적 가능한 전체 기록) ══════════════
#   왜 이 말을 추천했는지·어떤 배당을 보고 판단했는지까지 리치 스키마로 경주별 저장.
#   기존 odds_history/learning 파이프라인은 그대로 두고, 그 데이터를 종합해 추가로 남긴다.
ANALYSIS_LOG_DIR = os.path.join(os.path.dirname(__file__), "data", "analysis_log")


def _analysis_log_path(rk):
    m = re.search(r"(\d{4}-\d{2}-\d{2})", rk or "")
    date = m.group(1) if m else time.strftime("%Y-%m-%d", time.localtime())
    race = re.sub(r"\d{4}-\d{2}-\d{2}", "", rk or "").strip() or (rk or "race")
    safe = re.sub(r"[^\w가-힣]+", "_", f"{date}_{race}").strip("_")
    os.makedirs(ANALYSIS_LOG_DIR, exist_ok=True)
    return os.path.join(ANALYSIS_LOG_DIR, safe + ".json"), date, race


def _build_analysis_log(rk, an=None):
    """_triple_analyze 결과 + odds_history(타임라인/결과) + 전적을 종합해 리치 로그를 만들고 저장.
    기존 로그가 있으면 사용자 입력(analyzed_at·복기 메모·profit)은 보존한다."""
    rec = _triple_load().get(rk) or {}
    if an is None:
        an = _triple_analyze(rk, rec)
    path, date, race = _analysis_log_path(rk)
    try:
        doc = json.load(open(path, encoding="utf-8"))
    except Exception:
        doc = {}

    # 배당 타임라인 + 실제 결과/적중(odds_history 에서)
    timeline, result_doc, review_doc = [], None, None
    try:
        hp, _, _ = _hist_path(rk)
        hist = json.load(open(hp, encoding="utf-8"))
        for s in hist.get("snapshots", []):
            timeline.append({"time": s.get("time"), "minutes_before": s.get("minutes_before"),
                             "quinella": s.get("quinella", {})})
        result_doc, review_doc = hist.get("result"), hist.get("review")
    except Exception:
        pass

    # 이상감지 신호(이유 포함)
    last_t = (an.get("lastSnapshot") or {}).get("time")
    signals = [{"time": last_t, "type": s.get("type"), "detail": s.get("text"),
                "severity": s.get("level"), "reason": s.get("detail")} for s in (an.get("signals") or [])]

    # 말별 상세(전적 점수 + 등급 + 등급 이유 + 배당)
    grade_by = {h.get("no"): h.get("grade") for h in (an.get("integrated") or [])}
    drop_set = set()
    for d in (an.get("drops") or []):
        if d.get("pct", 0) < 0:
            for x in d.get("combo", []):
                drop_set.add(x)
    form_by = {h.get("no"): h for h in (an.get("form") or [])}
    elim = an.get("elimination") or {}
    ehorses = elim.get("horses") or []
    win = an.get("single") or {}
    horses = []
    for h in sorted(ehorses, key=lambda x: -(x.get("total") or 0)):
        no = h.get("no")
        f = form_by.get(no, {})
        rp = f.get("recentPlacings") or []
        horses.append({
            "no": no, "name": f.get("name") or h.get("name") or "",
            "jockey": f.get("jockey") or "",
            "record_score": f.get("totalScore"),
            "record_detail": ("최근 " + "-".join(str(x) for x in rp)) if rp else "",
            "odds": win.get(str(no)) if isinstance(win, dict) else None,
            "grade": grade_by.get(no) or f.get("grade") or h.get("tier") or h.get("verdict"),
            "grade_reason": h.get("reason"),
        })

    cand = [h["no"] for h in ehorses if h.get("keep") or h.get("override")]
    elim_no = [h["no"] for h in ehorses if not (h.get("keep") or h.get("override"))]
    elim_reasons = {str(h["no"]): h.get("reason") for h in ehorses
                    if not (h.get("keep") or h.get("override"))}

    def _combo_reason(nos):
        gs = "·".join(f"{n}번({grade_by.get(n, '?')})" for n in nos)
        return gs + (" · 급락감지" if any(n in drop_set for n in nos) else "")

    label_map = [("복승 메인", "quinella_main"), ("복승 보조", "quinella_sub"),
                 ("삼복승 메인", "trifecta_main"), ("삼복승 보험1", "trifecta_insurance1"),
                 ("삼복승 보험2", "trifecta_insurance2")]
    final, alloc = {}, {}
    for lbl, key in label_map:
        r = next((b for b in (an.get("betRecommend") or []) if b.get("label") == lbl), None)
        if not r:
            continue
        od = r.get("expOdds") if r.get("expOdds") is not None else r.get("expOddsEst")
        final[key] = {"combo": "+".join(str(x) for x in r["combo"]), "odds": od, "reason": _combo_reason(r["combo"])}
        alloc["trifecta_insurance" if "보험" in lbl else key] = f"{r.get('alloc', 0)}%"
    final["budget_allocation"] = alloc

    input_data = (doc.get("input_data") if doc else None) or {
        "source": "Chrome확장 자동수집(배당) + 전적표",
        "pdf_file": None, "image_file": None,
        "odds_source": rec.get("source") or "asyukk34 Chrome확장 자동수집",
    }

    log = {
        "race_id": os.path.splitext(os.path.basename(path))[0],
        "date": date, "race": race,
        "analyzed_at": (doc.get("analyzed_at") if doc else None) or time.strftime("%H:%M:%S", time.localtime()),
        "updated_at": time.strftime("%H:%M:%S", time.localtime()),
        "input_data": input_data,
        "odds_timeline": timeline,
        "signals_detected": signals,
        "horses": horses,
        "elimination": {"candidates": cand, "eliminated": elim_no, "elimination_reasons": elim_reasons},
        "final_recommendation": final,
        "summary": an.get("summary"),
        "keyHorses": an.get("keyHorses"),
        "result": result_doc,
        "hit": review_doc,
        "profit": (doc.get("profit") if doc else None),
        "review": (doc.get("review") if doc else None),   # 사용자 복기 메모(텍스트)
    }
    json.dump(log, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return log


def _analysis_log_save(rk, an=None):
    try:
        return _build_analysis_log(rk, an)
    except Exception as e:
        print("[분석로그] 저장 실패:", e)
        return None


def _analysis_log_git_backup(label):
    """data/analysis_log/ 를 커밋(+가능하면 push). 원격/인증 미설정이면 조용히 건너뜀."""
    root = os.path.dirname(os.path.abspath(__file__))
    try:
        subprocess.run(["git", "add", "data/analysis_log"], cwd=root, timeout=30, capture_output=True)
        r = subprocess.run(["git", "commit", "-m", (label or "분석 로그 백업").strip()],
                           cwd=root, timeout=30, capture_output=True, text=True)
        if r.returncode != 0:
            return {"committed": False, "msg": ((r.stdout or "") + (r.stderr or "")).strip()[:200]}
        pr = subprocess.run(["git", "push"], cwd=root, timeout=90, capture_output=True, text=True)
        return {"committed": True, "pushed": pr.returncode == 0,
                "msg": (pr.stderr or "").strip()[:200] if pr.returncode != 0 else "pushed"}
    except Exception as e:
        return {"committed": False, "msg": str(e)}


def _learning_load():
    try:
        return json.load(open(LEARNING_FILE, encoding="utf-8"))
    except Exception:
        return {"records": [], "stats": {}}


def _learning_save(d):
    os.makedirs(os.path.dirname(LEARNING_FILE), exist_ok=True)
    json.dump(d, open(LEARNING_FILE, "w", encoding="utf-8"), ensure_ascii=False)


# ── [2번] 부진마 역전 학습 (전적 기반) ─────────────────────────────────
#   부진마 판정: 최근 5경주 평균 착순 ≥ 4.0
#   입상(1~3착) 시 동반 조건(급락30%+·복승이상감지)을 태깅해 pattern_learning.json 누적.
#   condition_stats: 부진마가 그 조건을 동반한 횟수(count) + 그중 입상(이변 성공)한 횟수(hit)
#   → 적중률 = hit/count = "부진마가 이 조건을 동반했을 때 실제로 이변을 낸 비율".
UPSET_FILE = os.path.join(os.path.dirname(__file__), "data", "pattern_learning.json")
UPSET_AVG_THRESHOLD = 4.0   # 최근5경주 평균 착순 이 값 이상이면 부진마


def _upset_load():
    try:
        d = json.load(open(UPSET_FILE, encoding="utf-8"))
    except Exception:
        d = {}
    d.setdefault("patterns", [])
    d.setdefault("condition_stats", {})
    return d


def _upset_save(d):
    os.makedirs(os.path.dirname(UPSET_FILE), exist_ok=True)
    json.dump(d, open(UPSET_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def _upset_bump(stats, key, hit):
    c = stats.setdefault(key, {"count": 0, "hit": 0})
    c["count"] += 1
    if hit:
        c["hit"] += 1


def _learn_upset(rk, an, top3, date_str=None):
    """부진마(최근5경주 평균착순≥4.0)의 입상 여부 + 동반 조건을 학습.
    반환: 갱신된 pattern_learning dict(없으면 None)."""
    form = an.get("form") or []
    if not form:
        return None
    drops = an.get("drops") or []
    single_nos = {d.get("no") for d in (an.get("singleDrops") or [])}
    key_nos = {h.get("no") for h in (an.get("keyHorses") or [])}
    anom_no = (an.get("anomalyHorse") or {}).get("no")
    top3 = [int(x) for x in (top3 or []) if str(x).lstrip("-").isdigit()]
    d = _upset_load()
    stats = d["condition_stats"]
    learned = []
    for f in form:
        no = f.get("no")
        rp = [int(x) for x in (f.get("recentPlacings") or [])[:5] if isinstance(x, (int, float))]
        if no is None or not rp:
            continue
        avg = round(sum(rp) / len(rp), 1)
        if avg < UPSET_AVG_THRESHOLD:
            continue   # 부진마 아님
        placed = no in top3
        # 동반 조건 판정 — 이 부진마가 낀 복승 조합의 급락/이상감지
        my_drops = [dr for dr in drops if no in (dr.get("combo") or [])]
        strong = [dr for dr in my_drops if (dr.get("pct") or 0) <= -30]
        conditions = []
        if strong:
            worst = min(dr.get("pct") or 0 for dr in strong)
            conditions.append(f"급락{abs(int(worst))}%")
            _upset_bump(stats, "급락동반", placed)
        anomaly = bool(my_drops) or (no in single_nos) or (no in key_nos) or (no == anom_no)
        if anomaly:
            conditions.append("복승이상감지")
            _upset_bump(stats, "이상감지동반", placed)
        if not conditions:
            _upset_bump(stats, "조건없음", placed)   # 조건 없는 부진마도 분모로 집계(비교용)
        _upset_bump(stats, "전체부진마", placed)      # 부진마 전체 입상률(기준선)
        # 입상한 이변 사례만 patterns[]에 근거 사례로 보존
        if placed:
            d["patterns"].append({
                "race": rk, "horse_no": no, "recent_avg": avg,
                "win_place": top3.index(no) + 1,
                "conditions": conditions, "date": date_str or "",
            })
            learned.append((no, avg, conditions))
    d["patterns"] = d["patterns"][-500:]   # 무한 성장 방지(최근 500건 유지)
    _upset_save(d)
    if learned:
        print(f"[부진마학습] {rk}: 부진마 입상 {len(learned)}건 → " +
              ", ".join(f"{no}번(평균{avg},{'+'.join(c) if c else '조건없음'})" for no, avg, c in learned))
    return d


# ── [전체 데이터 저장·패턴 자동 발견] ──────────────────────────────────
#   원시 데이터는 data/analysis_log/ 에 매 분석마다 이미 완전 저장(배당 타임라인 30초·전적점수·
#   이상감지·결과)됨. 여기서는 그 로그들을 스캔해 "적중한 경주들의 공통점"을 자동 발견한다.
DISCOVERED_FILE = os.path.join(os.path.dirname(__file__), "data", "discovered_patterns.json")
DISCOVERY_MIN_RACES = 10    # 결과 있는 경주가 이 이상 쌓이면 패턴 발견 시작
DISCOVERY_TARGET = 50       # 데이터 충분도 100% 기준(경주 수)


def _drop_timing_from_timeline(timeline):
    """배당 타임라인(30초 스냅샷)에서 복승 배당의 최대 하락과 그 시점(T-N분 버킷)."""
    snaps = [s for s in (timeline or []) if isinstance(s.get("quinella"), dict)]
    best_pct, best_mb = 0.0, None
    for i in range(1, len(snaps)):
        prev, cur = snaps[i - 1]["quinella"], snaps[i]["quinella"]
        for k, o in cur.items():
            try:
                po, oo = float(prev.get(k)), float(o)
            except (TypeError, ValueError):
                continue
            if po > 0 and oo > 0:
                pct = (oo - po) / po * 100.0
                if pct < best_pct:
                    best_pct, best_mb = pct, snaps[i].get("minutes_before")
    return (_pattern_timing_bucket(best_mb) if best_mb is not None else None), round(best_pct, 1)


def _race_features(log):
    """분석 로그 1건 → 패턴 발견용 특징. 결과 미입력이면 None(학습 대상 제외)."""
    hit = log.get("hit") or {}
    if not log.get("result") and not hit:
        return None
    good = bool(hit.get("quinella_hit") or hit.get("trifecta_hit") or hit.get("was_hit"))
    sigs = log.get("signals_detected") or []
    types = [s.get("type") for s in sigs]
    drop_pcts = []
    for s in sigs:
        if s.get("type") in ("급락", "단승급락"):
            m = re.search(r"\(-?(\d+)%\)", s.get("detail") or "")
            if m:
                drop_pcts.append(int(m.group(1)))
    tl_bucket, tl_pct = _drop_timing_from_timeline(log.get("odds_timeline") or [])
    max_drop = max(drop_pcts + ([abs(tl_pct)] if tl_pct <= -15 else []) or [0])
    # 적중 경주의 상위등급마(A/B/상) 전적점수 분포(전적 있는 경주만)
    form_scores = [h.get("record_score") for h in (log.get("horses") or [])
                   if isinstance(h.get("record_score"), (int, float))
                   and h.get("grade") in ("A", "B", "상")]
    return {
        "good": good,
        "had_drop": ("급락" in types or "단승급락" in types or max_drop >= 15),
        "strong_drop": max_drop >= 30,
        "very_strong_drop": max_drop >= 50,
        "had_reversal": "역전" in types,
        "had_compression": "압축" in types,
        "drop_timing": tl_bucket,
        "form_scores": form_scores,
    }


def _discover_patterns():
    """data/analysis_log/ 전체를 스캔해 적중 경주의 공통점을 자동 발견 → discovered_patterns.json."""
    feats = []
    if os.path.isdir(ANALYSIS_LOG_DIR):
        for fn in os.listdir(ANALYSIS_LOG_DIR):
            if not fn.endswith(".json"):
                continue
            try:
                log = json.load(open(os.path.join(ANALYSIS_LOG_DIR, fn), encoding="utf-8"))
            except Exception:
                continue
            f = _race_features(log)
            if f:
                feats.append(f)
    n = len(feats)
    out = {
        "generated_at": time.time(),
        "races_with_result": n, "target": DISCOVERY_TARGET,
        "sufficiency": round(min(1.0, n / DISCOVERY_TARGET) * 100),
        "min_races": DISCOVERY_MIN_RACES, "patterns": [],
    }
    if n < DISCOVERY_MIN_RACES:
        out["note"] = f"패턴 발견까지 결과 입력 경주 {DISCOVERY_MIN_RACES - n}건 더 필요 (현재 {n}/{DISCOVERY_MIN_RACES})"
        _discovered_save(out)
        return out

    goods = [f for f in feats if f["good"]]
    base = len(goods) / n * 100.0   # 전체 적중률(기준선)
    patterns = []

    def add_bool(key, label):
        with_ = [f for f in feats if f.get(key)]
        if len(with_) < 5:
            return
        g = sum(1 for f in with_ if f["good"])
        rate = g / len(with_) * 100.0
        lift = rate - base
        if lift >= 12:   # 기준선 대비 +12%p 이상일 때만 '발견된 패턴'으로 채택
            patterns.append({
                "type": "배당변화", "key": key, "desc": label,
                "rate": round(rate, 1), "baseline": round(base, 1),
                "lift": round(lift, 1), "support": len(with_), "hit": g,
            })

    add_bool("had_drop", "급락(자금유입) 동반 경주")
    add_bool("strong_drop", "30%+ 강한 급락 동반 경주")
    add_bool("very_strong_drop", "50%+ 대량 급락 동반 경주")
    add_bool("had_reversal", "쌍승 역전 동반 경주")
    add_bool("had_compression", "배당 압축 동반 경주")

    # 급락 시점 버킷별 적중률(적중 경주에서 급락이 주로 언제?)
    from collections import Counter
    tc, tg = Counter(), Counter()
    for f in feats:
        b = f.get("drop_timing")
        if b:
            tc[b] += 1
            if f["good"]:
                tg[b] += 1
    for b, c in tc.items():
        if c >= 5:
            rate = tg[b] / c * 100.0
            if rate - base >= 12:
                patterns.append({
                    "type": "시점", "key": f"timing:{b}",
                    "desc": f"급락이 {b}에 발생한 경주",
                    "rate": round(rate, 1), "baseline": round(base, 1),
                    "lift": round(rate - base, 1), "support": c, "hit": tg[b],
                })

    # 적중 경주 상위등급마 전적점수 범위(사분위)
    fs = sorted(s for f in goods for s in (f.get("form_scores") or []))
    if len(fs) >= 8:
        q1 = fs[len(fs) // 4]
        q3 = fs[len(fs) * 3 // 4]
        patterns.append({
            "type": "전적점수", "key": "form_range",
            "desc": f"적중 경주 상위등급마 전적점수 주로 {q1}~{q3} 구간",
            "range": [q1, q3], "support": len(fs),
        })

    patterns.sort(key=lambda p: (-(p.get("lift") or 0), -(p.get("support") or 0)))
    out["patterns"] = patterns
    out["baseline_hit_rate"] = round(base, 1)
    _discovered_save(out)
    print(f"[패턴발견] 결과 {n}경주 스캔 → 발견 패턴 {len(patterns)}개(기준 적중률 {round(base,1)}%)")
    return out


def _discovered_load():
    try:
        return json.load(open(DISCOVERED_FILE, encoding="utf-8"))
    except Exception:
        return {"races_with_result": 0, "target": DISCOVERY_TARGET, "sufficiency": 0,
                "min_races": DISCOVERY_MIN_RACES, "patterns": []}


def _discovered_save(out):
    os.makedirs(os.path.dirname(DISCOVERED_FILE), exist_ok=True)
    json.dump(out, open(DISCOVERED_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


# [4번] 고배당 적중 하이라이트 저장소
HIGHLIGHT_FILE = os.path.join(os.path.dirname(__file__), "data", "highlight_wins.json")


def _safe_num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _highlight_save(entry):
    os.makedirs(os.path.dirname(HIGHLIGHT_FILE), exist_ok=True)
    try:
        arr = json.load(open(HIGHLIGHT_FILE, encoding="utf-8"))
    except Exception:
        arr = []
    arr.append(entry)
    json.dump(arr[-500:], open(HIGHLIGHT_FILE, "w", encoding="utf-8"), ensure_ascii=False)


def _rate(records, sel, cond):
    s = [r for r in records if sel(r)]
    hit = sum(1 for r in s if cond(r))
    return {"n": len(s), "hit": hit, "rate": (round(hit / len(s) * 100, 1) if s else None)}


def _recompute_learning_stats(records):
    """이상감지 유형별 실제 적중률 + 추천 적중률 + 전적/제거 예측 적중률."""
    def rev_hit(r):
        favs = [rv["favored"][0] for rv in (r.get("reversals") or []) if rv.get("favored")]
        return r.get("result", {}).get("1st") in favs

    # [2번] 패턴별 적중률: 패턴 태그별 발생/적중 + '동시(2개+)' 버킷
    pattern_stats = {}
    for r in records:
        pats = r.get("patterns") or []
        hit = bool(r.get("was_hit"))
        for p in pats:
            d = pattern_stats.setdefault(p, {"n": 0, "hit": 0})
            d["n"] += 1
            d["hit"] += 1 if hit else 0
        if len(pats) >= 2:
            d = pattern_stats.setdefault("동시(2개+)", {"n": 0, "hit": 0})
            d["n"] += 1
            d["hit"] += 1 if hit else 0
    for d in pattern_stats.values():
        d["rate"] = round(d["hit"] / d["n"] * 100, 1) if d["n"] else None

    # [3번] 시점별 급락 효과: 급락 발생 시점(T-N분) 버킷별 이상감지 적중률
    drop_timing = {}
    for r in records:
        seen_buckets = set()
        for _p, t in (r.get("pattern_timing") or {}).items():
            if t in seen_buckets:
                continue
            seen_buckets.add(t)
            d = drop_timing.setdefault(t, {"n": 0, "hit": 0})
            d["n"] += 1
            d["hit"] += 1 if r.get("anomaly_was_correct") else 0
    for d in drop_timing.values():
        d["rate"] = round(d["hit"] / d["n"] * 100, 1) if d["n"] else None

    return {
        "total": len(records),
        "recommend_hit": _rate(records, lambda r: True, lambda r: r.get("was_hit")),
        "drop_anomaly": _rate(records, lambda r: r.get("anomalies_detected"),
                              lambda r: r.get("anomaly_was_correct")),
        "reversal": _rate(records, lambda r: r.get("reversals"), rev_hit),
        # 전적 기반 유력마(제거법 1순위 후보)가 3착 이내에 든 비율
        "form_pick": _rate(records, lambda r: r.get("form_available"),
                           lambda r: r.get("form_pick_hit")),
        # 제거(🔴/🟠) 판정이 옳았던 비율(제거마가 3착 밖으로 밀려남)
        "elimination": _rate(records, lambda r: r.get("eliminated"),
                             lambda r: r.get("elimination_correct")),
        # [2·3번] 패턴 학습
        "pattern_stats": pattern_stats,
        "drop_timing": drop_timing,
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


def _apply_result_learning(rk, result, top3, final_odds=None):
    """경주 결과 → 히스토리 기록 + 자동학습 레코드/통계 갱신(공용).
    keiba/asyukk 결과 자동수집(results_auto)과 수동 입력(record-result)이 함께 사용.
    이상감지·추천·전적유력마·제거 판정의 실제 적중 여부를 판정해 learning.json 누적."""
    path, _, _ = _hist_path(rk)
    try:
        doc = json.load(open(path, encoding="utf-8"))
    except Exception:
        doc = {"raceKey": rk, "snapshots": [], "result": None}
    doc["result"] = result
    if final_odds:
        doc["finalOdds"] = final_odds
    json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False)

    an = _triple_analyze(rk, _triple_load().get(rk) or {})

    def in3(combo):
        return all(x in top3 for x in combo)
    was_hit = any(in3(r["combo"]) for r in an.get("betRecommend", []))

    # ── [4번] 복승/삼복승 정확 적중 + 수익 + 고배당 하이라이트 ──
    rec_bets = an.get("betRecommend", [])
    top2 = sorted(top3[:2]) if len(top3) >= 2 else []
    top3s = sorted(top3[:3]) if len(top3) >= 3 else []
    quinella_hit = bool(top2 and any(r.get("kind") == "복승" and sorted(r["combo"]) == top2 for r in rec_bets))
    trifecta_hit = bool(top3s and any(r.get("kind") == "삼복승" and sorted(r["combo"]) == top3s for r in rec_bets))
    fo = final_odds if isinstance(final_odds, dict) else {}

    def _odds_val(x):  # 확장은 {combo,odds} 중첩, 수동입력은 숫자 → 둘 다 허용
        return _safe_num(x.get("odds")) if isinstance(x, dict) else _safe_num(x)
    q_odds = _odds_val(fo.get("quinella"))
    t_odds = _odds_val(fo.get("trifecta") or fo.get("trio"))
    payouts = {"quinella": (q_odds if quinella_hit and q_odds else 0),
               "trifecta": (t_odds if trifecta_hit and t_odds else 0)}
    try:
        if (quinella_hit and q_odds and q_odds >= 30) or (trifecta_hit and t_odds and t_odds >= 100):
            _highlight_save({"raceKey": rk, "top3": top3,
                             "quinella_hit": quinella_hit, "quinella_odds": q_odds,
                             "trifecta_hit": trifecta_hit, "trifecta_odds": t_odds, "t": time.time()})
    except Exception as e:
        print("[하이라이트] 저장 실패:", e)

    anomalies_detected, anomaly_correct = [], False
    signal_correct = []   # [복기] 사람이 읽는 형태: "7+12 급락 → 12번 입상 적중"
    for d in an.get("drops", [])[:5]:
        if d["pct"] < 0:
            anomalies_detected.append(f"급락: {d['combo'][0]}-{d['combo'][1]} {d['pct']}%")
            hit_horses = [h for h in d["combo"] if h in top3]
            if hit_horses:
                anomaly_correct = True
                signal_correct.append(
                    f"{d['combo'][0]}+{d['combo'][1]} 복승 급락({d['pct']}%) → "
                    f"{'·'.join(str(h) for h in hit_horses)}번 입상 적중")

    # ── [3번] 전적 예측 + 제거법 적중 판정 ──
    elim = an.get("elimination") or {}
    elim_horses = elim.get("horses") or []
    form_available = bool(elim.get("formAvailable"))
    # 전적 유력마 = 제거법 1순위 후보(keep/override 중 total 최고). 3착 이내면 적중.
    kept = [h for h in elim_horses if h.get("keep") or h.get("override")]
    form_pick = kept[0]["no"] if kept else None
    form_pick_hit = bool(form_pick is not None and form_pick in top3)
    # 제거(🔴 확실제거/🟠 제거권장) 판정: 해당 말들이 모두 3착 밖이면 제거가 옳았음.
    eliminated_nos = [h["no"] for h in elim_horses if not (h.get("keep") or h.get("override"))]
    elimination_correct = bool(eliminated_nos and all(n not in top3 for n in eliminated_nos))

    def snap_near(minb):
        best = None
        for s in doc.get("snapshots", []):
            mb = s.get("minutes_before")
            if mb is None:
                continue
            if best is None or abs(mb - minb) < abs(best[0] - minb):
                best = (mb, s)
        return best[1] if best else None

    # [1번] 이상감지 패턴 태깅 + 시점 저장 (결과 입력 시 자동)
    _pm = an.get("patternMatch") or {}
    _patterns = _pm.get("patterns") or []
    _last_mb = (an.get("lastSnapshot") or {}).get("minutes_before")
    _timing = _pattern_timing_bucket(_last_mb)
    _pattern_timing = {p: _timing for p in _patterns if p.startswith("급락")}
    _bet_type = (rec_bets[0].get("kind") if rec_bets else None)

    record = {
        "race": rk, "result": result, "top3": top3, "was_hit": was_hit,
        "quinella_hit": quinella_hit, "trifecta_hit": trifecta_hit, "payouts": payouts,
        "anomalies_detected": anomalies_detected, "anomaly_was_correct": anomaly_correct,
        "signal_correct": signal_correct,
        "reversals": [r for r in an.get("reversals", []) if r.get("flipped")],
        "keyHorses": an.get("keyHorses"),
        "form_available": form_available, "form_pick": form_pick, "form_pick_hit": form_pick_hit,
        "eliminated": eliminated_nos, "elimination_correct": elimination_correct,
        "finalOdds": final_odds,
        "odds_at_10min": (snap_near(10) or {}).get("quinella"),
        "odds_at_1min30sec": (snap_near(2) or {}).get("quinella"),
        # [1번] 패턴 학습 필드
        "patterns": _patterns, "pattern_timing": _pattern_timing, "bet_type": _bet_type,
        "t": time.time(),
    }
    L = _learning_load()
    L["records"].append(record)
    L["stats"] = _recompute_learning_stats(L["records"])
    _learning_save(L)
    # [2번] 부진마 역전 학습(전적 있는 경주만 작동) — 급락30%+·복승이상감지 동반 조건별 적중률 누적
    try:
        _learn_upset(rk, an, top3, time.strftime("%Y-%m-%d"))
    except Exception as e:
        print("[부진마학습] 실패:", e)
    # [복기] 결과 적중/판정 요약을 히스토리 파일에도 저장 → 통계 탭에서 재계산 없이 표시
    try:
        doc["review"] = {
            "was_hit": was_hit, "quinella_hit": quinella_hit, "trifecta_hit": trifecta_hit,
            "payouts": payouts, "anomaly_was_correct": anomaly_correct,
            "signal_correct": signal_correct, "elimination_correct": elimination_correct,
            "eliminated": eliminated_nos, "form_pick": form_pick, "form_pick_hit": form_pick_hit,
        }
        json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception as e:
        print("[복기저장] 결과 요약 실패:", e)
    # [분석 로그] 결과 입력 시 로그에 실제 결과·적중 반영
    _analysis_log_save(rk)
    # [전체데이터·패턴발견] 결과 반영된 로그까지 포함해 적중 경주 공통점 자동 발견
    try:
        _discover_patterns()
    except Exception as e:
        print("[패턴발견] 실패:", e)
    print(f"[자동학습] {rk} 결과 {top3} → 추천적중 {was_hit}, 급락적중 {anomaly_correct}, "
          f"전적유력마 {form_pick}({'적중' if form_pick_hit else '실패'}), 제거적중 {elimination_correct}")
    return record, L["stats"]


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
    record, stats = _apply_result_learning(rk, result, top3, body.get("finalOdds"))
    return jsonify({"ok": True, "record": record, "stats": stats})


# ── [일괄 결과 등록] 결과 페이지(HTML/URL) 전체 파싱 → 분석경주 자동매칭·학습 ──
class _TableRowCollector(HTMLParser):
    """모든 table 의 tr/td|th 텍스트를 [[셀,...], ...] 로 수집(테이블 경계 무시, 헤더는 내용으로 판별)."""
    def __init__(self):
        super().__init__()
        self.rows, self._cur, self._cell = [], None, None
    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._cur = []
        elif tag in ("td", "th") and self._cur is not None:
            self._cell = []
    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._cur.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._cur is not None:
            self.rows.append(self._cur)
            self._cur = None


def _parse_result_rows(html):
    """결과 페이지 HTML → [{area,round,no1,no2,no3,qOdds,tOdds}]. 헤더 라벨로 컬럼 판별."""
    p = _TableRowCollector()
    try:
        p.feed(html or "")
    except Exception:
        return []
    rows = p.rows

    def ns(s):
        return re.sub(r"\s+", "", s or "")
    hi = None
    for i, r in enumerate(rows):
        joined = "".join(ns(c) for c in r)
        if re.search(r"경주지역|경마장|지역", joined) and re.search(r"라운드|회차|경주", joined):
            hi = i
            break
    if hi is None:
        return []
    heads = [ns(c) for c in rows[hi]]

    def idx(pat):
        for k, h in enumerate(heads):
            if re.search(pat, h):
                return k
        return -1
    iArea = idx(r"경주지역|경마장|지역")
    iRound = idx(r"라운드|회차|경주번호|^경주$|^R$")
    i1, i2, i3 = idx(r"1착|1위"), idx(r"2착|2위"), idx(r"3착|3위")
    iQ, iT = idx(r"복승"), idx(r"삼복승|삼복")

    def cell(r, k):
        return r[k] if (0 <= k < len(r)) else ""

    def firstnum(s):
        m = re.search(r"\d+", s or "")
        return int(m.group()) if m else None
    out = []
    for r in rows[hi + 1:]:
        if len(r) < 3:
            continue
        area, rnd = cell(r, iArea), cell(r, iRound)
        if not area and not rnd:
            continue
        out.append({
            "area": area, "round": rnd,
            "no1": firstnum(cell(r, i1)), "no2": firstnum(cell(r, i2)), "no3": firstnum(cell(r, i3)),
            "qOdds": _safe_num(cell(r, iQ)) if iQ >= 0 else None,
            "tOdds": _safe_num(cell(r, iT)) if iT >= 0 else None,
        })
    return out


def _area_num(s):
    """문자열에서 (한글 회장명 토큰, 경주번호) 추출. 결과행·raceKey 공용."""
    txt = s or ""
    m = re.search(r"[가-힣]{2,}", txt.replace(" ", ""))
    area = m.group() if m else None
    n = re.search(r"(\d{1,2})\s*(?:R\b|경주|라운드|레이스)", txt, re.IGNORECASE)
    num = int(n.group(1)) if n else None
    return area, num


def _match_row_to_key(row, analyzed_keys):
    """결과행(area,round) → 분석된 raceKey 중 지역+번호가 맞는 것 찾기."""
    r_area = re.sub(r"\s", "", row.get("area") or "")
    m = re.search(r"[가-힣]{2,}", r_area)
    r_area = m.group() if m else r_area
    r_num = None
    mn = re.search(r"\d{1,2}", str(row.get("round") or ""))
    if mn:
        r_num = int(mn.group())
    if not r_area or r_num is None:
        return None
    for rk in analyzed_keys:
        k_area, k_num = _area_num(rk)
        if k_num != r_num or not k_area:
            continue
        if r_area in k_area or k_area in r_area:
            return rk
    return None


@app.route("/api/results/bulk", methods=["POST"])
def results_bulk():
    """[일괄 결과 등록] 결과 페이지 전체를 한 번에 파싱→분석경주 자동매칭→적중판정·학습→요약.
    body: {html?} 또는 {url?} 또는 {rows?}, stake?(정액 베팅 가정, 기본 1000).
    → {ok, registered, hits, profit, matched:[...], unmatched:[...], errors:[...]}"""
    body = request.json or {}
    stake = _safe_num(body.get("stake")) or 1000
    rows = body.get("rows")
    if not rows:
        html = body.get("html") or ""
        if not html and body.get("url"):
            try:
                req = Request(body["url"], headers={"User-Agent": "Mozilla/5.0"})
                html = urlopen(req, timeout=10).read().decode("utf-8", "replace")
            except Exception as e:
                return jsonify({"error": f"URL 로드 실패({e}). 결과 페이지 HTML을 붙여넣어 주세요.",
                                "needPaste": True}), 200
        rows = _parse_result_rows(html)
    if not rows:
        return jsonify({"error": "결과표를 파싱하지 못했습니다. (경주지역·라운드·1~3착 컬럼이 있는 결과 페이지 HTML인지 확인)",
                        "needPaste": True}), 200

    analyzed_keys = list(_triple_load().keys())
    matched, unmatched, errors = [], [], []
    hits = 0
    profit = 0
    for row in rows:
        top3 = [row.get("no1"), row.get("no2"), row.get("no3")]
        top3 = [int(x) for x in top3 if isinstance(x, int) and x >= 1]
        if not top3:
            continue
        rk = _match_row_to_key(row, analyzed_keys)
        if not rk:
            unmatched.append({"area": row.get("area"), "round": row.get("round"), "top3": top3})
            continue
        final_odds = {}
        if row.get("qOdds"):
            final_odds["quinella"] = {"combo": top3[:2], "odds": row["qOdds"]}
        if row.get("tOdds"):
            final_odds["trio"] = {"combo": top3[:3], "odds": row["tOdds"]}
        result = {}
        for i, no in enumerate(top3[:3]):
            result[["1st", "2nd", "3rd"][i]] = no
        try:
            rec, _ = _apply_result_learning(rk, result, top3, final_odds or None)
        except Exception as e:
            errors.append({"raceKey": rk, "error": str(e)})
            continue
        q_hit, t_hit = bool(rec.get("quinella_hit")), bool(rec.get("trifecta_hit"))
        won = q_hit or t_hit or bool(rec.get("was_hit"))
        # 수익: 정액 stake 가정 — 적중 시 +(배당-1)*stake, 추천했으나 미적중 시 -stake
        pnl = 0
        pays = rec.get("payouts") or {}
        if q_hit and pays.get("quinella"):
            pnl = round((pays["quinella"] - 1) * stake)
        elif t_hit and pays.get("trifecta"):
            pnl = round((pays["trifecta"] - 1) * stake)
        elif rec.get("bet_type"):   # 추천이 있었는데 미적중
            pnl = -stake
        if won:
            hits += 1
        profit += pnl
        matched.append({"raceKey": rk, "top3": top3, "quinella_hit": q_hit,
                        "trifecta_hit": t_hit, "won": won, "pnl": pnl})

    print(f"[일괄결과] 등록 {len(matched)}건 · 적중 {hits} · 손익 {profit}원 · 매칭실패 {len(unmatched)}건")
    return jsonify({
        "ok": True, "registered": len(matched), "hits": hits, "profit": profit,
        "stake": stake, "matched": matched, "unmatched": unmatched, "errors": errors,
        "parsedRows": len(rows),
    })


@app.route("/api/learning/stats", methods=["GET", "POST"])
def learning_stats():
    """누적 학습 통계 대시보드."""
    L = _learning_load()
    return jsonify({"stats": L.get("stats", {}), "count": len(L.get("records", []))})


@app.route("/api/learning/upset", methods=["GET"])
def learning_upset():
    """[2번] 부진마 역전 학습 조회 → {patterns, condition_stats, summary}.
    조건별 적중률(rate=hit/count)을 계산해 통계 탭에서 바로 표시한다."""
    d = _upset_load()
    cs = d.get("condition_stats") or {}
    rows = []
    for key, v in cs.items():
        cnt, hit = int(v.get("count", 0)), int(v.get("hit", 0))
        rows.append({"condition": key, "count": cnt, "hit": hit,
                     "rate": round(hit / cnt * 100, 1) if cnt else 0.0})
    rows.sort(key=lambda r: (-r["count"], -r["rate"]))
    return jsonify({
        "threshold": UPSET_AVG_THRESHOLD,
        "condition_stats": cs,
        "conditionRows": rows,
        "patterns": (d.get("patterns") or [])[-30:][::-1],   # 최근 30건(최신 우선)
        "total": len(d.get("patterns") or []),
    })


@app.route("/api/patterns/discovered", methods=["GET", "POST"])
def patterns_discovered():
    """[전체데이터·패턴발견] 적중 경주 공통점 자동 발견 결과 + 데이터 충분도.
    POST(또는 ?recompute=1)면 즉시 재계산."""
    if request.method == "POST" or request.args.get("recompute"):
        try:
            return jsonify(_discover_patterns())
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return jsonify(_discovered_load())


# [v2.0.0] 확장(백그라운드 자동수집 엔진) → 분석기(웹) 상태 브리지.
#   확장 서비스워커가 수집 상태를 POST 하고, 분석기 페이지가 GET 으로 폴링해
#   "🟢 자동수집 중 | 마지막 | 다음 | 발주까지" 상태바를 그린다.
_AUTO_STATUS = {}


@app.route("/api/auto/status", methods=["GET", "POST"])
def auto_status():
    global _AUTO_STATUS
    if request.method == "POST":
        s = request.json or {}
        s["serverAt"] = time.time()
        _AUTO_STATUS = s
        return jsonify({"ok": True})
    return jsonify(_AUTO_STATUS)


# ══════════════ [분석 로그] 목록/조회/메모/백필/백업 API ══════════════
@app.route("/api/analysis-log/list", methods=["GET", "POST"])
def analysis_log_list():
    """저장된 분석 로그 목록(날짜별 정렬) → [{file,race_id,date,race,analyzed_at,snaps,hasResult}]."""
    out = []
    if os.path.isdir(ANALYSIS_LOG_DIR):
        for fn in sorted(os.listdir(ANALYSIS_LOG_DIR), reverse=True):
            if not fn.endswith(".json"):
                continue
            try:
                d = json.load(open(os.path.join(ANALYSIS_LOG_DIR, fn), encoding="utf-8"))
            except Exception:
                continue
            out.append({"file": fn, "race_id": d.get("race_id"), "date": d.get("date"),
                        "race": d.get("race"), "analyzed_at": d.get("analyzed_at"),
                        "snaps": len(d.get("odds_timeline") or []),
                        "signals": len(d.get("signals_detected") or []),
                        "hasResult": bool(d.get("result"))})
    return jsonify({"logs": out})


@app.route("/api/analysis-log/get", methods=["POST"])
def analysis_log_get():
    """{file|raceKey} → 분석 로그 전체."""
    body = request.json or {}
    fn = body.get("file")
    if fn:
        path = os.path.join(ANALYSIS_LOG_DIR, os.path.basename(fn))
    else:
        path, _, _ = _analysis_log_path((body.get("raceKey") or "").strip())
    try:
        return jsonify(json.load(open(path, encoding="utf-8")))
    except Exception:
        return jsonify({"error": "분석 로그가 없습니다."}), 404


@app.route("/api/analysis-log/memo", methods=["POST"])
def analysis_log_memo():
    """복기 메모 저장: {file|raceKey, review} → 로그 파일의 review 필드 갱신."""
    body = request.json or {}
    fn = body.get("file")
    if fn:
        path = os.path.join(ANALYSIS_LOG_DIR, os.path.basename(fn))
    else:
        path, _, _ = _analysis_log_path((body.get("raceKey") or "").strip())
    try:
        doc = json.load(open(path, encoding="utf-8"))
    except Exception:
        return jsonify({"error": "분석 로그가 없습니다."}), 404
    doc["review"] = body.get("review", "")
    doc["profit"] = body.get("profit", doc.get("profit"))
    json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return jsonify({"ok": True})


@app.route("/api/analysis-log/backfill", methods=["POST"])
def analysis_log_backfill():
    """[4번] 지금까지 수집/분석된 경주들의 로그를 즉시 생성(없으면 생성, 있으면 최신화).
    triple_store(현재 배당) + odds_history(과거 스냅샷)에 존재하는 모든 raceKey 대상."""
    keys = set(_triple_load().keys())
    if os.path.isdir(ODDS_HISTORY_DIR):
        for fn in os.listdir(ODDS_HISTORY_DIR):
            if not fn.endswith(".json"):
                continue
            try:
                d = json.load(open(os.path.join(ODDS_HISTORY_DIR, fn), encoding="utf-8"))
                if d.get("raceKey"):
                    keys.add(d["raceKey"])
            except Exception:
                continue
    made = []
    for rk in keys:
        log = _analysis_log_save(rk)
        if log:
            made.append(log.get("race_id"))
    return jsonify({"ok": True, "count": len(made), "races": sorted(made)})


@app.route("/api/analysis-log/backup", methods=["POST"])
def analysis_log_backup():
    """[5번] data/analysis_log/ 를 GitHub에 커밋(+push). 하루 경주 종료 후 호출."""
    label = ((request.json or {}).get("label") or f"분석 로그 백업 {time.strftime('%Y-%m-%d', time.localtime())}").strip()
    res = _analysis_log_git_backup(label)
    return jsonify({"ok": True, **res})


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


# ─────────────────────────────────────────
# [한국경마] 서버측 PDF 백그라운드 분석 (v1.12.0)
#   PDF 업로드 → 서버가 PyMuPDF로 페이지/밴드를 렌더 → Vision 감지·추출·분석을 백그라운드
#   스레드에서 수행하고, 진행상황·결과를 data/korea_session.json 에 실시간 저장한다.
#   → 탭 전환/새로고침/서버 재시작에도 분석이 계속되고, 결과가 영구 보존된다.
#   렌더 밴드 좌표는 static/js/pdf-parser.js 와 동일하게 맞춘다.
# ─────────────────────────────────────────
KOREA_SESSION = os.path.join(os.path.dirname(__file__), "data", "korea_session.json")
KOREA_PDF = os.path.join(os.path.dirname(__file__), "data", "korea_last.pdf")
_BAND = (0.0, 0.10, 1.0, 0.41)          # 메인 출전마 표 밴드
_TRAIN_BAND = (0.0, 0.355, 1.0, 0.585)  # 조교훈련/레이팅 표 밴드
_korea_lock = threading.Lock()
_korea_job = {"gen": 0}   # 세대 카운터: 새 업로드/리셋 시 증가 → 이전 스레드 자가 취소


def _korea_default_session():
    return {"status": "idle", "phase": "", "message": "", "done": 0, "total": 0,
            "label": "", "date": "", "startedAt": None, "updatedAt": None,
            "numPages": 0, "detected": None, "detectDone": False, "jockeyDone": False,
            "jockeyStats": {}, "races": [], "error": None}


def _korea_load():
    try:
        with open(KOREA_SESSION, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _korea_save(sess):
    sess["updatedAt"] = time.time()
    with _korea_lock:
        os.makedirs(os.path.dirname(KOREA_SESSION), exist_ok=True)
        tmp = KOREA_SESSION + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sess, f, ensure_ascii=False)
        os.replace(tmp, KOREA_SESSION)


def _render_region_b64(page, frac, target_w):
    """PDF 페이지의 frac(x0,y0,x1,y1 비율) 영역을 target_w 픽셀 폭 PNG(base64)로 렌더.
    pdf-parser.js 의 _renderRegion 와 동일한 스케일 공식을 사용한다."""
    r = page.rect
    x0, y0, x1, y1 = frac
    region_w_pt = (x1 - x0) * r.width
    scale = max(0.3, min(6.0, target_w / region_w_pt)) if region_w_pt else 1.0
    clip = fitz.Rect(x0 * r.width, y0 * r.height, x1 * r.width, y1 * r.height)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
    return {"media_type": "image/png", "data": base64.b64encode(pix.tobytes("png")).decode("ascii")}


def _render_thumb(page):  return _render_region_b64(page, (0, 0, 1, 1), 640)
def _render_band(page):   return _render_region_b64(page, _BAND, 1568)
def _render_train(page):  return _render_region_b64(page, _TRAIN_BAND, 1568)
def _render_full(page):   return _render_region_b64(page, (0, 0, 1, 1), 1540)


def _korea_race_label(venue, race_no, distance):
    """static/js/app.js 의 raceLabel 과 동일 — History 제목 매칭을 위해 형식 유지."""
    v = (venue + " ") if venue else ""
    d = (" " + distance) if distance else ""
    return f"{v}{race_no}경주{d}"


def _load_base_jockeys():
    """정적 기수 DB(static/data/jockeys.json)를 이름 키 dict 로 로드. PDF 추출값을 위에 덮어씀."""
    stats = {}
    try:
        p = os.path.join(os.path.dirname(__file__), "static", "data", "jockeys.json")
        for j in (json.load(open(p, encoding="utf-8")).get("jockeys") or []):
            if j.get("name"):
                stats[j["name"]] = dict(j)
    except Exception as e:
        print("[한국] 기수 DB 로드 실패:", e)
    return stats


def _merge_jockey(stats, j):
    """PDF '기수 기승현황표' 한 행을 stats 에 병합 (app.js mergeJockey 와 동일 계산)."""
    name = j.get("name") or ""
    total = j.get("total") or 0
    if not name:
        return
    w1, w2, w3 = j.get("w1", 0), j.get("w2", 0), j.get("w3", 0)
    win = round(w1 / total * 1000) / 10 if total else 0
    place = round((w1 + w2 + w3) / total * 1000) / 10 if total else 0
    base = stats.get(name, {"name": name})
    base.update({"winRate": win, "placeRate": place, "rides": total, "w1": w1, "w2": w2, "w3": w3,
                 "month": j.get("month", 0), "mW1": j.get("mW1", 0), "mW2": j.get("mW2", 0), "mW3": j.get("mW3", 0)})
    stats[name] = base


def _korea_extract_race(doc, race, api_key=None):
    """요약 페이지 1장에서 메인표+조교표를 추출·병합해 출전마 리스트 반환 (app.js extractRaceFull)."""
    pg = doc[race["summaryPage"] - 1]   # summaryPage 는 1-based
    sheet = _do_extract_race(_render_band(pg), api_key)
    horses = sheet.get("horses") or []
    try:
        tr = _do_extract_training(_render_train(pg), api_key)
        tmap = {t.get("horseNum"): t for t in (tr.get("horses") or [])}
        for h in horses:
            t = tmap.get(h.get("horseNum"))
            if t:
                if t.get("rating"):
                    h["rating"] = t["rating"]
                if t.get("trainer"):
                    h["training"] = ((h.get("training") or "") + f" 조교사 {t['trainer']} {t.get('mark', '')}").strip()
    except Exception as e:
        print("[한국] 조교 추출 실패:", e)
    return horses


def _korea_git_backup(label):
    """완료 후 data/korea_session.json + data/korea_history/ 를 커밋(+가능하면 push).
    원격/인증 미설정이면 조용히 건너뜀."""
    root = os.path.dirname(os.path.abspath(__file__))
    try:
        subprocess.run(["git", "add", "data/korea_session.json", "data/korea_history"],
                       cwd=root, timeout=30, capture_output=True)
        msg = (f"한국경마 세션 백업 {label}").strip()
        r = subprocess.run(["git", "commit", "-m", msg], cwd=root, timeout=30, capture_output=True, text=True)
        if r.returncode != 0:
            print("[한국] git commit 없음/실패:", ((r.stdout or "") + (r.stderr or "")).strip()[:200])
            return
        pr = subprocess.run(["git", "push"], cwd=root, timeout=90, capture_output=True, text=True)
        if pr.returncode == 0:
            print("[한국] GitHub 백업 완료:", label)
        else:
            print("[한국] git push 건너뜀(원격/인증 미설정?):", (pr.stderr or "").strip()[:200])
    except Exception as e:
        print("[한국] git 백업 예외:", e)


def _korea_run_job(gen, api_key=None):
    """백그라운드 분석 본체. 세션(disk)을 기준으로 진행하며, 이미 끝난 단계는 건너뛰어 '재개'를 지원."""
    def cancelled():
        return _korea_job.get("gen") != gen

    sess = _korea_load() or _korea_default_session()
    try:
        doc = fitz.open(KOREA_PDF)
    except Exception as e:
        sess["status"] = "error"; sess["error"] = f"PDF 열기 실패: {e}"; _korea_save(sess); return

    try:
        total_pages = doc.page_count
        sess["numPages"] = total_pages
        sess["status"] = "running"; sess["error"] = None
        _korea_save(sess)

        # 1) 페이지 감지 (썸네일 6장 배치)
        if not sess.get("detectDone"):
            sess["phase"] = "detect"
            detected = {}
            CHUNK = 6
            for s in range(0, total_pages, CHUNK):
                if cancelled():
                    return
                pages = list(range(s, min(s + CHUNK, total_pages)))   # 0-based
                sess["message"] = f"페이지 스캔 {pages[0] + 1}–{pages[-1] + 1} / {total_pages}"
                _korea_save(sess)
                try:
                    imgs = [_render_thumb(doc[p]) for p in pages]
                    out = _do_detect(imgs, api_key)
                    for r in (out.get("pages") or []):
                        i = r.get("index")
                        if i is not None and 0 <= i < len(pages):
                            detected[str(pages[i] + 1)] = r   # 1-based 키로 저장
                except Exception as e:
                    print("[한국] detect 실패", pages, e)
            sess["detected"] = detected
            sess["detectDone"] = True
            _korea_save(sess)
        detected = sess["detected"] or {}

        # 2) 기수현황표 추출 → jockeyStats
        if not sess.get("jockeyDone"):
            sess["phase"] = "jockey"; sess["message"] = "기수현황표 판독 중..."
            _korea_save(sess)
            stats = _load_base_jockeys()
            for jp in [int(p) for p, r in detected.items() if r.get("type") == "jockey"]:
                if cancelled():
                    return
                try:
                    o = _do_extract_jockey(_render_full(doc[jp - 1]), api_key)
                    for j in (o.get("jockeys") or []):
                        _merge_jockey(stats, j)
                except Exception as e:
                    print(f"[한국] 기수 {jp}p 실패:", e)
            sess["jockeyStats"] = stats
            sess["jockeyDone"] = True
            _korea_save(sess)

        # 3) 경주 그룹핑 (venue+raceNo) → 요약 페이지 선택
        if not sess.get("races"):
            groups = {}
            for p, r in detected.items():
                if r.get("type") != "race":
                    continue
                key = (r.get("venue") or "") + "#" + str(r.get("raceNo"))
                g = groups.setdefault(key, {"venue": r.get("venue") or "", "raceNo": r.get("raceNo") or 0,
                                            "distance": r.get("distance") or "", "pages": []})
                g["pages"].append({"page": int(p), "layout": r.get("layout")})
            races = []
            for g in groups.values():
                summ = next((x["page"] for x in g["pages"] if x["layout"] == "summary"),
                            min(x["page"] for x in g["pages"]))
                races.append({"venue": g["venue"], "raceNo": g["raceNo"], "distance": g["distance"],
                              "summaryPage": summ, "title": _korea_race_label(g["venue"], g["raceNo"], g["distance"]),
                              "horses": [], "report": None, "status": "todo"})
            races.sort(key=lambda x: ((x["venue"] or ""), x["raceNo"] or 0))
            sess["races"] = races
            sess["total"] = len(races)
            venue = races[0]["venue"] if races else ""
            sess["label"] = (f"{sess.get('date', '')} {venue}경마 {len(races)}경주").strip()
            _korea_save(sess)

        # 4) 경주별 추출 + BMED 분석
        sess["phase"] = "analyze"
        races = sess["races"]
        for i, race in enumerate(races):
            if cancelled():
                return
            if race.get("report") and race.get("horses"):
                continue   # 이미 완료(재개 시 건너뜀)
            done = sum(1 for r in races if r.get("status") == "done")
            sess["done"] = done
            sess["message"] = f"분석 중... {done}/{len(races)} 경주 완료 — {race['title']} 추출"
            _korea_save(sess)
            try:
                horses = _korea_extract_race(doc, race, api_key)
                if not horses:
                    race["status"] = "empty"; _korea_save(sess); continue
                race["horses"] = horses
                if cancelled():
                    return
                race["report"] = _do_analyze(
                    {"raceNo": race["raceNo"], "raceTitle": race["title"], "horses": horses, "distance": race["distance"]},
                    sess.get("jockeyStats") or {}, api_key)
                race["status"] = "done"
            except Exception as e:
                race["status"] = "error"; race["error"] = str(e)
                print(f"[한국] 경주 '{race['title']}' 분석 실패:", e)
            sess["done"] = sum(1 for r in races if r.get("status") == "done")
            _korea_save(sess)

        sess["status"] = "done"; sess["phase"] = "done"
        sess["done"] = sum(1 for r in races if r.get("status") == "done")
        sess["message"] = f"완료 — {sess['done']}/{len(races)} 경주 분석 완료"
        _korea_save(sess)
        _korea_git_backup(sess.get("label") or "")
    except Exception as e:
        if not cancelled():
            sess["status"] = "error"; sess["error"] = str(e); _korea_save(sess)
        print("[한국] 백그라운드 작업 예외:", e)
    finally:
        doc.close()


def _korea_start_job(api_key=None):
    _korea_job["gen"] = _korea_job.get("gen", 0) + 1
    gen = _korea_job["gen"]
    threading.Thread(target=_korea_run_job, args=(gen, api_key), daemon=True).start()


@app.route("/api/korea/start", methods=["POST"])
def korea_start():
    """PDF 업로드 → 새 세션 시작(기존 세션/진행중 작업은 덮어씀 = '새 PDF 업로드' 초기화)."""
    if fitz is None:
        return jsonify({"error": "서버에 PyMuPDF(fitz)가 설치되지 않아 PDF 분석을 할 수 없습니다. "
                                 "터미널에서 'pip install PyMuPDF' 실행 후 서버를 재시작하세요."}), 503
    f = request.files.get("pdf")
    if not f:
        return jsonify({"error": "PDF 파일이 없습니다 (multipart 'pdf' 필드)."}), 400
    os.makedirs(os.path.dirname(KOREA_PDF), exist_ok=True)
    f.save(KOREA_PDF)
    date = time.strftime("%Y-%m-%d")
    sess = _korea_default_session()
    sess.update({"status": "running", "startedAt": time.time(), "date": date,
                 "label": f"{date} 분석 준비 중", "message": "업로드 완료 — 분석 시작"})
    _korea_save(sess)
    _korea_start_job(request.form.get("api_key"))
    return jsonify({"ok": True})


@app.route("/api/korea/status", methods=["GET"])
def korea_status():
    """진행상황만(경량) — 폴링용."""
    s = _korea_load() or _korea_default_session()
    return jsonify({k: s.get(k) for k in
                    ("status", "phase", "message", "done", "total", "label", "error", "numPages", "updatedAt")})


@app.route("/api/korea/session", methods=["GET"])
def korea_session():
    """전체 세션(경주·리포트 포함) — 페이지 로드 시 복원용."""
    return jsonify(_korea_load() or _korea_default_session())


@app.route("/api/korea/reset", methods=["POST"])
def korea_reset():
    """'새 PDF 업로드' 초기화 — 진행중 작업 취소 + 저장 세션/PDF 삭제. (새로고침으로는 초기화 안 됨)"""
    _korea_job["gen"] = _korea_job.get("gen", 0) + 1
    for p in (KOREA_SESSION, KOREA_PDF):
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception as e:
            print("[한국] reset 삭제 실패:", p, e)
    return jsonify({"ok": True})


@app.route("/api/korea/reextract", methods=["POST"])
def korea_reextract():
    """경주 요약 페이지 ±1 보정 후 재추출·재분석 (감지가 1p 어긋난 경우)."""
    body = request.json or {}
    idx = body.get("idx")
    sess = _korea_load()
    if not sess or idx is None or idx >= len(sess.get("races") or []):
        return jsonify({"error": "세션 또는 경주를 찾을 수 없습니다."}), 400
    if not os.path.exists(KOREA_PDF):
        return jsonify({"error": "저장된 PDF가 없습니다. 새 PDF를 업로드하세요."}), 400
    if fitz is None:
        return jsonify({"error": "서버에 PyMuPDF(fitz)가 설치되지 않았습니다. "
                                 "'pip install PyMuPDF' 실행 후 서버를 재시작하세요."}), 503
    race = sess["races"][idx]
    if body.get("page"):
        race["summaryPage"] = int(body["page"])
    doc = fitz.open(KOREA_PDF)
    try:
        if race["summaryPage"] < 1 or race["summaryPage"] > doc.page_count:
            return jsonify({"error": "페이지 범위를 벗어났습니다."}), 400
        horses = _korea_extract_race(doc, race, body.get("api_key"))
        if not horses:
            return jsonify({"error": "출전마를 못 읽었습니다. ←/→ 로 페이지를 보정해 보세요.",
                            "summaryPage": race["summaryPage"]}), 200
        race["horses"] = horses
        race["report"] = _do_analyze(
            {"raceNo": race["raceNo"], "raceTitle": race["title"], "horses": horses, "distance": race["distance"]},
            sess.get("jockeyStats") or {}, body.get("api_key"))
        race["status"] = "done"
        _korea_save(sess)
        return jsonify({"ok": True, "race": race})
    finally:
        doc.close()


# ── [4번·5번] 경주별 분석 히스토리 (data/korea_history/) ──
#   PDF 전적분석 + 배당 타임라인 + 최종 추천 + 이상감지를 경주 1건 = 파일 1개로 영구 저장.
#   파일명: 2026-07-03_서울_5R.json
KOREA_HISTORY_DIR = os.path.join(os.path.dirname(__file__), "data", "korea_history")


def _korea_hist_file(date, venue, race_no):
    # 날짜 하이픈 보존: 2026-07-03_서울_5R.json
    safe = re.sub(r"[^\w가-힣-]+", "_", f"{date}_{venue or '경마'}_{race_no}R").strip("_")
    return os.path.join(KOREA_HISTORY_DIR, safe + ".json")


@app.route("/api/korea/history/save", methods=["POST"])
def korea_history_save():
    """통합분석 스냅샷 저장/갱신. 같은 경주 파일은 덮어쓰되 결과(result)는 보존."""
    b = request.json or {}
    date = b.get("date") or time.strftime("%Y-%m-%d")
    venue = (b.get("venue") or "").strip()
    race_no = b.get("raceNo") or 0
    os.makedirs(KOREA_HISTORY_DIR, exist_ok=True)
    path = _korea_hist_file(date, venue, race_no)
    prev = {}
    try:
        prev = json.load(open(path, encoding="utf-8"))
    except Exception:
        prev = {}
    doc = {
        "date": date, "venue": venue, "raceNo": race_no,
        "raceKey": b.get("raceKey"), "title": b.get("title"),
        "report": b.get("report"),           # PDF 전적 BMED 분석 결과
        "formScores": b.get("formScores"),   # 전적 점수 패널
        "integrated": b.get("integrated"),   # 통합 등급(전적40+배당60)
        "recommend": b.get("recommend"),     # 최종 베팅 추천
        "anomalies": b.get("anomalies") or [],  # 이상감지 내역
        "signals": b.get("signals") or [],
        "timeline": b.get("timeline") or [],    # 배당 변동 타임라인
        "result": prev.get("result"),           # 실제 착순(입력 시 보존)
        "savedAt": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "t": time.time(),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    return jsonify({"ok": True, "file": os.path.basename(path)})


@app.route("/api/korea/history/list", methods=["GET"])
def korea_history_list():
    items = []
    if os.path.isdir(KOREA_HISTORY_DIR):
        for fn in sorted(os.listdir(KOREA_HISTORY_DIR), reverse=True):
            if not fn.endswith(".json"):
                continue
            try:
                d = json.load(open(os.path.join(KOREA_HISTORY_DIR, fn), encoding="utf-8"))
            except Exception:
                continue
            items.append({
                "file": fn, "date": d.get("date"), "venue": d.get("venue"),
                "raceNo": d.get("raceNo"), "title": d.get("title"), "raceKey": d.get("raceKey"),
                "hasResult": bool(d.get("result")), "savedAt": d.get("savedAt"),
                "anomalyCount": len(d.get("anomalies") or []), "snaps": len(d.get("timeline") or []),
            })
    return jsonify({"items": items})


@app.route("/api/korea/history/get", methods=["POST"])
def korea_history_get():
    fn = os.path.basename((request.json or {}).get("file") or "")
    path = os.path.join(KOREA_HISTORY_DIR, fn)
    if not fn or not os.path.exists(path):
        return jsonify({"error": "해당 히스토리를 찾을 수 없습니다."}), 404
    return jsonify(json.load(open(path, encoding="utf-8")))


@app.route("/api/korea/history/result", methods=["POST"])
def korea_history_result():
    """실제 착순 입력 → 해당 히스토리 파일에 result 병합."""
    b = request.json or {}
    fn = os.path.basename(b.get("file") or "")
    path = os.path.join(KOREA_HISTORY_DIR, fn)
    if not fn or not os.path.exists(path):
        return jsonify({"error": "해당 히스토리를 찾을 수 없습니다."}), 404
    doc = json.load(open(path, encoding="utf-8"))
    doc["result"] = b.get("result")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    return jsonify({"ok": True})


@app.route("/api/korea/backup", methods=["POST"])
def korea_backup():
    """완료 후 GitHub 백업 (세션 + 경주 히스토리). best-effort."""
    _korea_git_backup((request.json or {}).get("label") or "korea history")
    return jsonify({"ok": True})


def _korea_maybe_resume():
    """서버 재시작 시 진행중이던 분석 자동 재개 (data/korea_session.json 기준)."""
    if fitz is None:
        return
    s = _korea_load()
    if s and s.get("status") == "running" and os.path.exists(KOREA_PDF):
        print("[한국] 이전 분석 재개:", s.get("label"))
        _korea_start_job(None)


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
    # 재개는 리로더의 실제 작업 프로세스(WERKZEUG_RUN_MAIN)에서만 1회 수행.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        _korea_maybe_resume()
    app.run(host="127.0.0.1", port=8011, debug=True, threaded=True)
