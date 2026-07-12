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
import random
import hashlib
import threading
import subprocess
import html as _htmllib
from concurrent.futures import ThreadPoolExecutor, as_completed
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
                    "grade": {"type": "string",
                              "description": ("이 말의 이번 출전 경주 등급/조건(예 G1/G2/G3/오픈/국1군~국6군). "
                                              "헤더/전적에서 안 보이면 ''")},
                    "recentRecord": {"type": "string"},
                    "recentPlacings": {"type": "array", "items": {"type": "integer"},
                                       "description": "최근 경주부터 착순(정수) 배열, 최대 5. 못 읽으면 []"},
                    "pastRaces": {
                        "type": "array",
                        "description": ("직전 경주부터 과거 최대 5경주의 상세. 각 경주마다 거리·주로조건·기수명·"
                                        "부담중량·착순·초반위치. 전적표에서 안 보이면 []."),
                        "items": {
                            "type": "object",
                            "properties": {
                                "distance": {"type": "string", "description": "그 경주 거리(m 숫자, 예 '1200'). 모르면 ''"},
                                "condition": {"type": "string", "description": "주로 조건: 양호/무거움/불량 중 하나. 모르면 ''"},
                                "jockey": {"type": "string", "description": "그 경주 기수명. 모르면 ''"},
                                "weight": {"type": "string", "description": "그 경주 부담중량(kg 숫자). 모르면 ''"},
                                "placing": {"type": "integer", "description": "그 경주 착순(정수). 모르면 0"},
                                "grade": {"type": "string",
                                          "description": "그 경주 등급/조건(G1/G2/G3/오픈/국1군~국6군 등). 모르면 ''"},
                                "position": {"type": "string",
                                             "description": ("초반 레이스 포지션: 선행/중단/후방 중 하나. "
                                                             "타임·통과순위로 추정. 모르면 ''")},
                            },
                            "required": ["distance", "condition", "jockey", "weight", "placing", "grade", "position"],
                            "additionalProperties": False,
                        },
                    },
                    "health": {"type": "string"},
                    "training": {"type": "string"},
                },
                "required": ["horseNum", "horseName", "jockey", "weight", "rating", "grade",
                             "recentRecord", "recentPlacings", "pastRaces", "health", "training"],
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
                    "venue": {"type": "string", "enum": ["서울", "부산", "제주", "기타", ""]},
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
# API 사용량/비용 추적 (data/api_usage.json 일별 누적)
# ─────────────────────────────────────────
API_USAGE_FILE = os.path.join(os.path.dirname(__file__), "data", "api_usage.json")
_api_usage_lock = threading.Lock()
# 모델별 100만 토큰당 USD 단가 (input, output). 미등록 모델은 sonnet 기준 폴백.
_API_PRICES = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-opus-4-8": (15.0, 75.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
_API_PRICE_DEFAULT = (3.0, 15.0)


def _log_api_usage(usage, model, label=""):
    """매 Claude 호출의 input/output tokens 를 data/api_usage.json 에 일별 누적하고
    모델 단가로 예상 비용(USD)을 자동 계산한다. 실패해도 본 흐름은 방해하지 않는다."""
    try:
        in_tok = int(getattr(usage, "input_tokens", 0) or 0)
        out_tok = int(getattr(usage, "output_tokens", 0) or 0)
        # 프롬프트 캐시 토큰도 있으면 input 으로 합산(비용은 별도지만 근사)
        in_tok += int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        in_tok += int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        if in_tok <= 0 and out_tok <= 0:
            return
        p_in, p_out = _API_PRICES.get(model, _API_PRICE_DEFAULT)
        cost = in_tok / 1_000_000 * p_in + out_tok / 1_000_000 * p_out
        day = time.strftime("%Y-%m-%d")
        with _api_usage_lock:
            data = {}
            try:
                with open(API_USAGE_FILE, encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception:
                data = {}
            d = data.setdefault(day, {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                                      "estimated_cost_usd": 0.0, "by_model": {}})
            d["calls"] += 1
            d["input_tokens"] += in_tok
            d["output_tokens"] += out_tok
            d["estimated_cost_usd"] = round(d["estimated_cost_usd"] + cost, 4)
            bm = d["by_model"].setdefault(model, {"calls": 0, "input_tokens": 0,
                                                  "output_tokens": 0, "estimated_cost_usd": 0.0})
            bm["calls"] += 1
            bm["input_tokens"] += in_tok
            bm["output_tokens"] += out_tok
            bm["estimated_cost_usd"] = round(bm["estimated_cost_usd"] + cost, 4)
            os.makedirs(os.path.dirname(API_USAGE_FILE), exist_ok=True)
            tmp = API_USAGE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, API_USAGE_FILE)
    except Exception as e:
        print("[비용추적] 기록 실패:", e)


# ─────────────────────────────────────────
# Claude 호출
# ─────────────────────────────────────────
# [보완·재시도] 병렬 Vision 호출(한국 PDF 4워커)이 429/과부하/일시장애로 조용히 실패하지 않게
#   지수 백오프 재시도. 재시도 대상 = RateLimitError·과부하(529)·5xx·연결오류. 최대 _API_MAX_RETRY회.
_API_MAX_RETRY = 4
_API_RETRY_STATUS = {429, 500, 502, 503, 529}


def _api_should_retry(e):
    """이 예외가 재시도 가치가 있는 일시적 오류인지."""
    if isinstance(e, getattr(anthropic, "APIConnectionError", ())):
        return True
    if isinstance(e, getattr(anthropic, "RateLimitError", ())):
        return True
    sc = getattr(e, "status_code", None)
    if sc is None:
        sc = getattr(getattr(e, "response", None), "status_code", None)
    return sc in _API_RETRY_STATUS


def _messages_create_retry(cli, **kwargs):
    """messages.create 를 지수 백오프로 재시도(2·4·8·16초 + 지터). 마지막 실패는 그대로 raise."""
    last = None
    for attempt in range(_API_MAX_RETRY + 1):
        try:
            return cli.messages.create(**kwargs)
        except Exception as e:  # noqa: BLE001 — 재시도 여부는 _api_should_retry 로 판별
            last = e
            if attempt >= _API_MAX_RETRY or not _api_should_retry(e):
                raise
            wait = min(30.0, 2.0 * (2 ** attempt)) + random.uniform(0, 1.0)
            print(f"[AI재시도] {type(e).__name__}({getattr(e, 'status_code', '')}) — "
                  f"{attempt + 1}/{_API_MAX_RETRY} {round(wait, 1)}초 후 재시도")
            time.sleep(wait)
    raise last   # 이론상 도달 안 함


def call_claude(content, schema, max_tokens=4096, api_key=None):
    msg = _messages_create_retry(
        client(api_key),
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": content}],
        extra_body={"output_config": {"format": {"type": "json_schema", "schema": schema}}},
    )
    # [비용추적] 토큰 사용량 일별 기록(실패 무시)
    _log_api_usage(getattr(msg, "usage", None), MODEL)
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


@app.route("/api/usage", methods=["GET"])
def api_usage():
    """API 토큰 사용량/예상 비용 일별 집계 조회. ?days=N 으로 최근 N일만(기본 전체)."""
    data = {}
    try:
        with open(API_USAGE_FILE, encoding="utf-8") as f:
            data = json.load(f) or {}
    except Exception:
        data = {}
    days = sorted(data.keys())
    try:
        n = int(request.args.get("days", 0))
    except (TypeError, ValueError):
        n = 0
    if n > 0:
        days = days[-n:]
    rows = [dict(date=d, **data[d]) for d in days]
    total = {
        "calls": sum(data[d].get("calls", 0) for d in days),
        "input_tokens": sum(data[d].get("input_tokens", 0) for d in days),
        "output_tokens": sum(data[d].get("output_tokens", 0) for d in days),
        "estimated_cost_usd": round(sum(data[d].get("estimated_cost_usd", 0) for d in days), 4),
    }
    return jsonify({"days": rows, "total": total, "today": data.get(time.strftime("%Y-%m-%d"))})


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
        "- grade: 이 말의 이번 경주 등급/조건(예 G1/G2/G3/오픈/국1군~국6군). 헤더나 전적에서 안 보이면 ''.\n"
        "- pastRaces: 전적표에 과거 경주 상세가 있으면 직전 경주부터(가장 최근이 맨 앞) 최대 5경주를 각각 "
        "{distance(그 경주 거리 m 숫자), condition(주로 양호/무거움/불량), jockey(그 경주 기수명), "
        "weight(그 경주 부담중량 kg), placing(착순 정수), grade(그 경주 등급/조건 G1~G3/오픈/국1~6군 등), "
        "position(초반위치 선행/중단/후방)} 로 채우세요. "
        "position은 통과순위나 기록으로 추정 — 초반 선두권이면 '선행', 중위권이면 '중단', 후미면 '후방'. "
        "모르는 칸은 '' (placing만 0). 전적 상세가 표에 없으면 pastRaces=[].\n"
        "- health: 마체중/건강 메모가 있으면, 없으면 ''.\n"
        "- training: 조교 메모(예 '주행미합','연습주행')가 있으면, 없으면 ''.\n"
        "raceNo(경주 번호, 모르면 0), raceTitle(예 '제1경주', 모르면 '').\n"
        "마번 1번부터 마지막까지 한 마리도 빠짐없이. 출전표가 아니면 horses를 빈 배열로."
    )
    return call_claude([{"type": "text", "text": prompt}, image_block(img)],
                       RACE_SHEET_SCHEMA, 12288, api_key)


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
        "results 각 항목: venue(경마장 '서울'/'부산'/'제주'/'일본'/''), raceNo(경주 번호), "
        "placing(1착부터 순서대로 마번 정수 배열).\n"
        "결과표가 아니면 results=[]."
    )
    out = call_claude([{"type": "text", "text": prompt}, image_block(body.get("image"))],
                      RESULTS_SCHEMA, 4096, body.get("api_key"))
    return jsonify(out)


# [캡쳐+OCR] 경주결과 화면 캡쳐 → 결과표(여러 경주) 판독.
#   asyukk '경주결과'는 [경주지역·라운드·1착·2착·3착·단승·복승·쌍승·…] 다중 경주 표.
RESULT_OCR_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "venue": {"type": ["string", "null"]},
                    "raceNo": {"type": ["integer", "null"]},
                    "placing": {"type": "array", "items": {"type": "integer"}},
                    "quinellaOdds": {"type": ["number", "null"]},
                    "trioOdds": {"type": ["number", "null"]},
                },
                "required": ["placing"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["results"],
    "additionalProperties": False,
}


def _img_from_dataurl(img):
    """dataURL 문자열('data:image/png;base64,...') 또는 {media_type,data} → image_block 입력형."""
    if isinstance(img, str) and img.startswith("data:"):
        m = re.match(r"data:([^;]+);base64,(.*)$", img, re.S)
        if m:
            return {"media_type": m.group(1), "data": m.group(2)}
    return img if isinstance(img, dict) else None


@app.route("/api/result/ocr", methods=["POST"])
def result_ocr():
    """경주결과 화면 캡쳐 이미지 → 결과표(여러 경주) 판독 → 분석경주 자동매칭·등록·학습.
    body: {image: dataURL | {media_type,data}, stake?, api_key?}.
    → {ok, registered, hits, profit, matched, unmatched, parsed}"""
    body = request.json or {}
    img = _img_from_dataurl(body.get("image"))
    if not img or not img.get("data"):
        return jsonify({"error": "image(base64 dataURL 또는 {media_type,data})가 필요합니다."}), 400
    prompt = (
        "이 이미지는 경주(경륜/경정/바이크/경마) '경주결과' 화면입니다.\n"
        "표의 각 행이 한 경주이며, 보통 컬럼은 [경주지역, 라운드, 1착, 2착, 3착, 단승, 복승, 쌍승, 연승, 복연승, 삼복승, 삼쌍승] 입니다.\n"
        "확정된 각 경주 행을 results 배열에 하나씩 넣으세요:\n"
        "- venue: 경주지역(예 '사세보','호후','광명').\n"
        "- raceNo: 라운드(경주 번호) 정수.\n"
        "- placing: [1착, 2착, 3착] 선수번호(車番)/말번호 정수 순서대로.\n"
        "- quinellaOdds: '복승' 배당(숫자), trioOdds: '삼복승' 배당(숫자). 없으면 null.\n"
        "착순이 비었거나 아직 미확정인 행은 제외. 결과 화면이 아니면 results=[].\n"
        "배당(복승/삼복승 등)과 착순(1·2·3착)을 절대 혼동하지 마세요."
    )
    out = call_claude([{"type": "text", "text": prompt}, image_block(img)],
                      RESULT_OCR_SCHEMA, 2048, body.get("api_key"))
    results = (out or {}).get("results") or []
    rows = []
    for r in results:
        p = [x for x in (r.get("placing") or []) if isinstance(x, int) and x >= 1]
        if not p:
            continue
        rows.append({"area": r.get("venue"), "round": r.get("raceNo"),
                     "no1": p[0], "no2": p[1] if len(p) > 1 else None,
                     "no3": p[2] if len(p) > 2 else None,
                     "qOdds": _safe_num(r.get("quinellaOdds")),
                     "tOdds": _safe_num(r.get("trioOdds"))})
    if not rows:
        return jsonify({"ok": True, "registered": 0, "hits": 0, "profit": 0,
                        "matched": [], "unmatched": [], "parsed": results,
                        "note": "결과 행을 읽지 못했습니다(결과 화면이 선명한지 확인)."})
    summary = _register_result_rows(rows, _safe_num(body.get("stake")) or 1000)
    summary["parsed"] = results
    return jsonify(summary)


def _do_detect(imgs, api_key=None):
    content = [{"type": "text", "text": (
        "여러 장의 KRA 출주표 페이지 썸네일이 '페이지 인덱스' 라벨과 함께 순서대로 주어집니다.\n"
        "각 페이지를 분류하세요:\n"
        "- type='race': 상단에 'OO경마 N경주 NM ... 일반경주(시각)' 형태의 경주 헤더가 있는 경주 페이지.\n"
        "- type='jockey': 기수 기승현황(기수별 성적) 표 페이지.\n"
        "- type='other': 그 외.\n"
        "type가 'race'이면:\n"
        "  venue: 헤더의 경마장(서울경마→'서울', 부산경마→'부산', 제주경마→'제주', 그 외 '기타').\n"
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


# ── [한국 PDF 결정론 오버레이] pastRaces 기반 각질/거리/기수/부담중량/2착패턴 ──────
def _norm_korea_horse(h, jstats):
    """추출된 한국 출전마(horseNum/horseName…) → 스코어링 입력(no/name/pastRaces…) 정규화."""
    j = (jstats or {}).get(h.get("jockey", ""))
    prs = h.get("pastRaces") or []
    return {
        "no": h.get("horseNum"), "name": h.get("horseName", ""),
        "jockey": h.get("jockey", ""), "weight": h.get("weight"), "rating": h.get("rating"),
        "grade": h.get("grade", ""),                    # [2번] 등급 체계 보정용(이번 경주 등급)
        "recentPlacings": h.get("recentPlacings") or [],
        "pastRaces": prs,
        "lastJockey": (prs[0].get("jockey") if prs else None),
        "lastWeight": (prs[0].get("weight") if prs else None),
        "jockey3mPlaceRate": (j.get("placeRate") if isinstance(j, dict) else None),
    }


def _korea_deterministic_overlay(race, jstats):
    """pastRaces 가 추출된 경우에만 결정론 점수·각질·변화감지·2착패턴(삼복승필수)을 산출.
    pastRaces 가 하나도 없으면 None → 기존 AI-only 동작 그대로."""
    horses = race.get("horses") or []
    if not any((h.get("pastRaces") for h in horses)):
        return None
    leaders = _leader_jockeys(jstats)
    norm = [_norm_korea_horse(h, jstats) for h in horses]
    rrace = {"distance": race.get("distance"), "course": race.get("course"),
             "grade": race.get("grade")}
    scored = compute_horse_scores(rrace, norm, leader_names=leaders)
    must = []
    for s in scored:
        for fl in (s.get("flags") or []):
            if fl.get("type") == "삼복승필수":
                must.append({"no": s["no"], "name": s["name"], "msg": fl["msg"]})
    return {"formScores": scored, "mustTrifecta": must, "leaders": sorted(leaders)}


def _det_horse_note(h, race, leaders):
    """한 마리의 pastRaces 로부터 프롬프트에 넣을 결정론 힌트 텍스트(없으면 '')."""
    if not h.get("pastRaces"):
        return ""
    nh = _norm_korea_horse(h, None)
    nh["jockey"] = h.get("jockey", "")
    style = _running_style(nh)
    parts = []
    if style:
        parts.append(f"각질 {style}")
    _, dd = distance_change_bonus(nh, race, style)
    _, kd = jockey_change_bonus({**nh, "jockey": h.get("jockey", "")}, leaders)
    _, wd = weight_change_bonus(nh)
    for seg in (dd + kd + wd):
        parts.append(seg)
    rdist = _to_int(race.get("distance"))
    if rdist is not None:
        c2 = sum(1 for pr in (h.get("pastRaces") or [])
                 if _to_int(pr.get("distance")) == rdist and _to_int(pr.get("placing")) == 2)
        if c2 >= 2:
            parts.append(f"⭐동일거리({rdist}m) 2착 {c2}회→삼복승 필수")
    return (" / 결정론: " + ", ".join(parts)) if parts else ""


def _force_trifecta_from_must(report, overlay):
    """2착패턴(삼복승필수) 말을 삼복승 조합에 결정론적으로 강제 편성.
    AI 추천에 이미 포함돼 있으면 스킵, 없으면 그 말 + 결정론 총점 상위 2두로 조합 신설."""
    if not overlay or not overlay.get("mustTrifecta"):
        return
    br = report.setdefault("betting_recommend", {})
    tri = br.setdefault("trifecta", [])
    scored = sorted(overlay.get("formScores") or [], key=lambda s: s.get("totalScore", 0), reverse=True)
    # pattern2_horses(마명) 병합
    p2 = report.setdefault("pattern2_horses", [])
    for m in overlay["mustTrifecta"]:
        if m["name"] and m["name"] not in p2:
            p2.append(m["name"])
        no = m["no"]
        already = any(no in (line.get("combo") or []) for line in tri)
        if already:
            continue
        others = [s["no"] for s in scored if s["no"] != no][:2]
        combo = [no] + others
        if len(combo) >= 3:
            tri.append({"combo": combo[:3], "confidence": 60,
                        "note": f"[결정론 강제] {m['msg']}"})


def _do_analyze(race, jstats, api_key=None):
    lines = []
    any_weight = False
    any_kra = False
    kra_hist = _kra_load_history()          # [KRA] 마필 과거기록 자동매칭
    rdist = _to_int(race.get("distance"))
    rtrack = (race.get("condition") or {}).get("track")
    leader_names = _leader_jockeys(jstats)   # [4] 리딩기수 판별용
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
        det_note = _det_horse_note(h, race, leader_names)   # [2~6] pastRaces 결정론 힌트
        lines.append(
            f"{h.get('horseNum')}번 {h.get('horseName')} / 기수 {h.get('jockey') or '미상'} {jstat} / "
            f"부담 {h.get('weight') or '-'} / 레이팅 {h.get('rating') or '-'} / 전적 {h.get('recentRecord') or '-'} / "
            f"상태 {h.get('health') or '-'} / 조교 {h.get('training') or '-'}{weight_note}{kra_note}{det_note}"
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
    det_line = ""
    if any(h.get("pastRaces") for h in race.get("horses", [])):
        det_line = ("\n결정론 힌트('결정론:' 표기)는 과거 전적으로 계산한 값입니다: 거리단축=스피드 유리, "
                    "거리연장=스태미너/각질 적성, 리딩기수 교체=상향, 부담중량 감소=유리·증가=불리, "
                    "각질(선행/추격형), ⭐동일거리 2착 2회↑=삼복승 필수 포함. 이 힌트를 등급·조합에 반영하세요.")
    prompt = (f"{ANALYSIS_GUIDE}\n\n[{title}] 출전마 정보:\n" + "\n".join(lines) + cond_line + weight_line + kra_line +
              det_line + "\n\n위 정보로 분석과 베팅 추천을 JSON으로 응답하세요.")
    report = call_claude([{"type": "text", "text": prompt}], ANALYSIS_SCHEMA, 4096, api_key)
    # [6] 2착패턴(삼복승필수) 결정론 강제 편성 + 결정론 오버레이 부착 (pastRaces 있을 때만)
    try:
        overlay = _korea_deterministic_overlay(race, jstats)
        if overlay:
            report["deterministic"] = overlay
            _force_trifecta_from_must(report, overlay)
    except Exception as e:
        print("[한국] 결정론 오버레이 실패:", e)
    return report


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


def _form_from_starters(rk, drops, sport=None, valid_nos=None):
    """저장된 전적으로 마필 점수·등급 계산. 배당 급락마는 이상감지 상향 반영.
    - 일본(출마표2): recent 착순으로 점수 재계산
    - 한국(PDF): 프론트에서 이미 계산한 formScore/totalScore를 그대로 사용(마명·기수 한글 유지).
    - 경륜/경정/바이크(6명 종목): 한국경마(source=korea) 전적은 오매칭이므로 무시(전적없음이 정상).
      경륜 전적은 oddspark 출마표 분석(source=keirin)으로만 채운다.
    - valid_nos(현재 배당에 등장하는 마번 집합) 지정 시: 이전 경주 잔존마(배당 없는 마번)를 자동 제외."""
    rec = _starters_load().get(rk)
    if not rec or not rec.get("horses"):
        return None
    # [오매칭 차단] 6명 종목(경륜·경정·바이크)에 한국경마 전적이 raceKey 충돌로 들어간 경우 사용 금지.
    if sport in ("cycle", "boat", "bike") and rec.get("source") == "korea":
        return None
    anomaly_by_no = {}
    for d in drops or []:
        if d.get("pct", 0) < 0:  # 배당 하락(자금유입)
            for h in d["combo"]:
                anomaly_by_no.setdefault(int(h), {
                    "signalScore": min(100, 50 + int(abs(d["pct"]))),
                    "drop": abs(d["pct"]) / 100.0})
    raw = rec["horses"]
    # [잔존마 필터·1번] 현재 수집된 배당에 실제 등장하는 마번만 사용 → 이전 경주 잔존마(배당 없는 마번) 자동 제외.
    #   배당 매칭 말이 하나라도 있을 때만 적용(전무하면 마명 기반 종목 등 오검출 방지로 필터 보류).
    if valid_nos:
        def _hno(h):
            try:
                return int(h.get("no"))
            except (TypeError, ValueError):
                return None
        filtered = [h for h in raw if _hno(h) in valid_nos]
        if filtered:
            if len(filtered) != len(raw):
                dropped = sorted({_hno(h) for h in raw if _hno(h) is not None and _hno(h) not in valid_nos})
                print(f"[잔존마 필터] {rk}: 전적 {len(raw)}두 → 배당 출전마 {len(filtered)}두 (배당 없는 마번 제외: {dropped})")
            raw = filtered
    # [한국경마] 사전 계산된 전적점수가 있으면 그대로 통과(PDF Vision 한글 데이터)
    prescored = any(h.get("formScore") is not None or h.get("totalScore") is not None for h in raw)
    if rec.get("source") == "korea" or prescored:
        kra_hist = _kra_load_history()   # [전적→부진마 학습 연결] 마명으로 KRA 실전적 백필
        scored = []
        for h in raw:
            ts = h.get("totalScore")
            if ts is None:
                ts = h.get("formScore")
            no = h.get("no")
            an = anomaly_by_no.get(int(no)) if no is not None else None
            rp = (h.get("recent") or h.get("recentPlacings") or [])[:5]
            if not rp and h.get("name"):
                rp = _kra_recent_placings(h["name"], kra_hist)   # 저장 전적이 비면 KRA 실전적으로 채움
            # 전적점수가 없고(0/None) KRA 착순을 채웠으면 실전적 기반 점수로 보강(부진마·통합등급 반영)
            if (ts in (None, 0)) and rp:
                ts = base_form_score(rp)
            scored.append({
                "no": no, "name": h.get("name", ""), "jockey": h.get("jockey", ""),
                "recentPlacings": rp,
                "baseScore": round(ts or 0, 1), "courseBonus": 0, "jockeyBonus": 0,
                "totalScore": round(ts or 0, 1), "detail": [], "flags": [], "anomaly": an,
                # [보완1·경륜] 競走得点 절대등급 함께 전달(통합 사분위 grade와 별개)
                "competScore": h.get("competScore"), "absGrade": h.get("absGrade"),
                "styleType": h.get("styleType"),
            })
        classify_grades(scored)
        scored.sort(key=lambda x: -x["totalScore"])
        return scored
    # [일본경마] 출마표2 착순으로 재계산 (착순이 비면 KRA 실전적으로 백필 — 한국 마명만 매칭)
    kra_hist = _kra_load_history()
    horses = []
    for h in raw:
        rp = h.get("recent") or []
        if not rp and h.get("name"):
            rp = _kra_recent_placings(h["name"], kra_hist)
        horses.append({"no": h.get("no"), "name": h.get("name", ""), "jockey": h.get("jockey", ""),
                       "recentPlacings": rp, "currentWeight": h.get("weight")})
    scored = compute_horse_scores({}, horses, None, anomaly_by_no)
    scored.sort(key=lambda x: -x["totalScore"])
    return scored


OPENING_ODDS = 100.0    # [배당판 미수집 방어] 복승/단승 이 값 이상 = opening/placeholder(실자금 거의 없음)
OPENING_DROP = -80.0    # opening 배당이 실배당으로 정착할 때의 기계적 급락(신호 아님)
STALE_ACTIVE_SEC = 1800  # [경주전환 잔존 방어] 활성 3종 배당이 이 시간(30분) 넘게 미갱신 → 끝난 경주로 간주(활성 캐시서 정리)


def _is_opening_settle(po, pct):
    """opening/placeholder 배당(직전 100배+)이 실배당으로 정착하며 생기는 기계적 급락(≤-80%).
    배당판을 초반에 못 끌어와 캡처된 가짜 고배당 → 실배당 = 자금유입 신호가 아님(제외 대상)."""
    return po is not None and po >= OPENING_ODDS and pct is not None and pct <= OPENING_DROP


def _baseline_reset_needed(prev_q, cur_q, thresh=-90.0, min_ratio=0.6, min_combos=4):
    """[경주전환·초반미수집 방어] 직전 대비 공통 복승 조합의 다수(60%+)가:
    ①90%+ 급락(다른 경주 배당 잔존, 예: 147.4→5.7 시장 전반 붕괴), 또는
    ②opening 배당(100배+)에서 70%+ 정착(새 경주 배당판을 초반에 못 끌어와 생긴 가짜 고배당)
    → 기준값 재설정. 정상 경주는 일부만 급락(자금이 특정 말로 이동)하므로 시장 전반 동시 붕괴만 걸린다."""
    pm, cm = _odds_map_un(prev_q), _odds_map_un(cur_q)
    common = [k for k in cm if k in pm and pm[k] > 0 and cm[k] > 0]
    if len(common) < min_combos:
        return False
    big = sum(1 for k in common if (cm[k] - pm[k]) / pm[k] * 100 <= thresh)
    if (big / len(common)) >= min_ratio:
        return True
    # [초반 미수집] opening 배당(100배+)이 실배당으로 대거 정착(70%+) → 가짜 급락 방지
    opening = sum(1 for k in common
                  if pm[k] >= OPENING_ODDS and (cm[k] - pm[k]) / pm[k] * 100 <= -70.0)
    return (opening / len(common)) >= min_ratio


def _as_qmap(x):
    """복승 배당을 {정렬튜플: 최저배당} 맵으로 정규화 — 리스트([{combo,odds}])와
    저장 딕셔너리({'1+2': 배당}) 두 형식 모두 지원(스냅샷 저장형식 혼용 방어)."""
    if isinstance(x, dict):
        m = {}
        for k, v in x.items():
            try:
                kk = tuple(sorted(int(y) for y in str(k).split("+")))
                o = float(v)
            except (ValueError, TypeError):
                continue
            if o > 0 and (kk not in m or o < m[kk]):
                m[kk] = o
        return m
    return _odds_map_un(x)


def _next_race_surge(prev_q, cur_q, thresh=200.0, min_ratio=0.6, min_combos=4):
    """[2번 다음경주 혼입 방어] 직전 대비 공통 복승 조합의 다수(60%+)가 200%+ 급등하면
    현재 경주가 끝나고 다음 경주(초기 고배당) 배당이 섞인 것으로 판단 → 이후 데이터 차단.
    정상 경주는 배당이 내려가거나(자금유입) 소폭만 오르므로 대규모 동시 급등만 걸린다.
    (급락 방향은 _baseline_reset_needed 가 담당 — 이건 급등 방향 전용.)"""
    pm, cm = _as_qmap(prev_q), _as_qmap(cur_q)
    common = [k for k in cm if k in pm and pm[k] > 0 and cm[k] > 0]
    if len(common) < min_combos:
        return False
    big = sum(1 for k in common if (cm[k] - pm[k]) / pm[k] * 100 >= thresh)
    return (big / len(common)) >= min_ratio


def _triple_prune_stale(db, keep_rk=None, max_age=STALE_ACTIVE_SEC):
    """[경주전환 잔존 방어] 활성 3종 배당(triple_store)에서 max_age(기본 30분) 넘게
    갱신 안 된 경주를 제거한다. 경주별 히스토리 파일(data/odds_history)은 영구 보존되므로
    학습·복기·이상감지 누적에는 전혀 영향이 없다(활성 표시 캐시만 정리).
    keep_rk(방금 수집한 경주)는 나이와 무관하게 항상 유지. 반환: 제거된 raceKey 리스트.
    → max-t 폴백(triple_latest/current_race/analyze)이 '끝난 직전 경주'를 계속 끌어오던 문제 해소.
    한국·일본 동시 진행 등 최근(30분내) 갱신된 경주는 그대로 두어 병행 수집도 안전."""
    now = time.time()
    stale = [k for k, v in db.items()
             if k != keep_rk and (now - (v.get("t") or 0)) > max_age]
    for k in stale:
        db.pop(k, None)
    return stale


# [마번 범위 검증] 종목별 최대 마번(선수 번호). 이 값을 넘는 마번은 이전 경주 잔존·오검출로 간주해 제외.
#   경륜(cycle)=최대 9명 / 경정(boat)=6정 / 오토레이스(bike)=8차 / 경마(horse)=최대 18두(넉넉히 허용).
_SPORT_MAX_NO = {"cycle": 9, "boat": 6, "bike": 8, "horse": 18}


def _sport_max_no(sport):
    """종목별 유효 최대 마번 반환(모르는 종목은 경마 기준 18로 관대 처리)."""
    return _SPORT_MAX_NO.get((sport or "horse"), 18)


@app.route("/api/odds/triple/ingest", methods=["POST"])
def triple_ingest():
    """확장 [전체 자동 수집]: {raceKey, quinella[], exacta[], trio[]} 저장 → {ok, counts}"""
    body = request.json or {}
    rk = (body.get("raceKey") or "").strip()
    if not rk:
        return jsonify({"error": "raceKey가 필요합니다."}), 400
    return jsonify(_do_triple_ingest(
        rk, body.get("quinella") or [], body.get("exacta") or [], body.get("trio") or [],
        _win_map_clean(body.get("win")), body.get("sport"), body.get("category"),
        body.get("source"), body.get("deadline")))


def _do_triple_ingest(rk, q, x, tr, win, sport=None, category=None, source=None, deadline=None):
    """[코어] 3종 배당 스냅샷 저장 + 히스토리 누적 → 역배열·배당변화·이상감지 파이프라인 공용.
    확장(triple_ingest)과 oddspark 직접조회(keirin_odds)가 함께 사용."""
    db = _triple_load()
    prev = db.get(rk) or {}
    now = time.time()
    # 변동 추적용 히스토리(최근 12회) — 직전 대비 급락/순위/역전 계산에 사용
    prev_hist = prev.get("history") or []
    # [1·3번] 경주 전환 방어: 직전 배당 대비 다수 조합 95%+ 급락 = 다른 경주 잔존 → 기준값 재설정
    #   [실시간 분석 유지 버그수정] 확립된 baseline(4+스냅샷)은 배당 휴리스틱으로 초기화하지 않는다.
    #   초반(미확립·다른 경주 잔존 배당)만 즉시 초기화하고, 확립 후 경주 전환은 raceKey 변경으로 처리
    #   (확장이 경주 바뀌면 새 rk → 새 레코드로 자연히 fresh 시작). 변동성 큰 배당의 단발 블립으로
    #   분석이 '초반(기준값 재설정)'으로 되돌아가던 버그 제거.
    # [종목 전환 완전 초기화] 같은 raceKey에서 종목이 바뀌면(경마→경륜 등) 이전 종목 배당/전적은
    #   완전히 다른 경주이므로 확립 여부와 무관하게 강제 초기화(잔존 배당·잔존마 원천 차단).
    _prev_sport = prev.get("sport")
    _new_sport = sport or _prev_sport or "horse"
    sport_changed = bool(_prev_sport and _new_sport and _prev_sport != _new_sport)
    # [경주 전환 잔존 방어·시간 간격] 직전 수집 후 20분+ 경과 = 이전 경주 종료 → 새 경주(같은 rk 재사용·발주 지연)로
    #   간주해 확립 여부와 무관하게 강제 초기화(이전 급락/배당 완전 제거·이 수집 = 새 기준값). 오다와라 1R 발주지연 케이스 방어.
    _last_t = (prev_hist[-1].get("t") if prev_hist else None) or prev.get("t")
    stale_gap = bool(_last_t and (now - _last_t) > 1200)
    _established = len(prev_hist) >= 4
    baseline_reset = (sport_changed or stale_gap
                      or ((not _established) and bool(prev_hist and _baseline_reset_needed(prev_hist[-1].get("quinella"), q))))
    hist = [] if baseline_reset else list(prev_hist)   # 이전(다른 경주·다른 종목·오래된) 배당 완전 제거
    hist.append({"t": now, "quinella": q, "exacta": x, "trio": tr, "win": win})
    hist = hist[-12:]
    # [수정#3 경륜/경정] 종목 태그 저장(horse|cycle|boat|bike). 기본 horse(경마) → 기존 동작 불변.
    #   ⚠ 종목 전환 시엔 새 종목을 우선(이전 sport 상속 금지) → 경마→경륜 전환이 sport 상속으로 묻히지 않게.
    sport = _new_sport
    if sport_changed:
        # 이전 종목 전적(경마 11두 등)을 완전 제거 → 잔존마 추천 차단.
        #   같은 raceKey 아래 남은 '이전 종목' starters도 삭제(한국 PDF 사전분석분은 아침 일괄이라 보존).
        _sdb = _starters_load()
        _srec = _sdb.get(rk)
        if _srec and (_srec or {}).get("source") != "korea":
            _sdb.pop(rk, None)
            _starters_save(_sdb)
            print(f"[종목 전환] {rk}: 이전 종목 전적(source={_srec.get('source')}) 제거")
        _starters_prune(keep_rk=rk)   # 그 외 다른 경주 잔존 전적도 정리
    # [탭분리] 분석기 탭 라우팅용 카테고리(korea|japan_local|japan_central|boat|cycle|bike).
    category = (category or prev.get("category")
                or {"cycle": "cycle", "boat": "boat", "bike": "bike"}.get(sport, "japan_local"))
    db[rk] = {"quinella": q, "exacta": x, "trio": tr, "win": win, "history": hist,
              "source": source, "sport": sport, "category": category, "t": now}
    # [경주전환 잔존 방어] 30분+ 미갱신된 '끝난 직전 경주'를 활성 캐시에서 정리(히스토리는 보존)
    #   → max-t 폴백이 직전 경주 배당을 계속 끌어오던 문제 차단.
    pruned = _triple_prune_stale(db, keep_rk=rk)
    _triple_save(db)
    # 배당 변동 히스토리 파일에 스냅샷 누적 (타임스탬프+발주전분+이상감지)
    try:
        _history_append(rk, q, x, deadline, win, baseline_reset=baseline_reset)
    except Exception as e:
        print("[히스토리] 기록 실패:", e)
    # [경륜 출마표 전적 자동 수집] 경륜 배당 수집(확장 asyukk 포함) 시 전적도 함께 → 통합등급 반영(raceKey에서 joCode 자동 감지·1회).
    if sport == "cycle":
        try:
            _kv, _kn = _area_num(rk)
            _kjo = _keirin_jo_from_venue(_kv)
            if _kjo and _kn:
                _keirin_autocollect_form(rk, _kjo, time.strftime("%Y%m%d"), _kn)
        except Exception as _ke:
            print("[경륜 전적·자동] ingest 훅 오류(무시):", _ke)
    counts = {"quinella": len(q), "exacta": len(x), "trio": len(tr), "win": len(win)}
    if sport_changed:
        print(f"[종목 전환] {rk}: {_prev_sport}→{_new_sport} → 배당·히스토리·전적 완전 초기화(잔존 데이터 제거)")
    elif stale_gap:
        print(f"[경주전환·시간간격] {rk}: 직전 수집 20분+ 경과 → 이전 경주 종료로 간주·기준값 재설정(급락 데이터 초기화)")
    elif baseline_reset:
        print(f"[경주전환 감지] {rk}: 비정상 변동폭(95%+ 다수) → 기준값 재설정(이전 배당 초기화)")
    if pruned:
        print(f"[활성정리] 끝난 경주 {len(pruned)}건 제거(히스토리 보존): {', '.join(pruned)}")
    print(f"[3종 수집] {rk}: {counts} (history {len(hist)}{' · 기준재설정' if baseline_reset else ''})")
    return {"ok": True, "counts": counts, "baselineReset": baseline_reset,
            "sportChanged": sport_changed, "pruned": pruned}


def _starters_prune(keep_rk=None):
    """[잔존마 방어·3번] starters_store(전적)에서 이전 경주 데이터 제거.
    keep_rk 주면 그 경주만 남기고 전부 삭제(새 경주 데이터만 유지), 없으면 전체 비움.
    한국 PDF 사전분석 전적(source=korea)은 아침 일괄 저장분이라 보존(경주별 재사용)."""
    sdb = _starters_load()
    if not sdb:
        return 0
    removed, kept = 0, {}
    for k, v in sdb.items():
        if k == keep_rk or (v or {}).get("source") == "korea":
            kept[k] = v            # 현재 경주 + 한국 PDF 사전분석분은 유지
        else:
            removed += 1
    _starters_save(kept)
    if removed:
        print(f"[잔존마 방어] starters 정리: {removed}건 제거(유지: {keep_rk or '없음'} + 한국PDF)")
    return removed


@app.route("/api/odds/triple/reset", methods=["POST"])
def triple_reset():
    """[🔄 새 경주 시작] 활성 3종 배당(triple_store)을 비워 새 경주로 전환.
    경주별 히스토리 파일(data/odds_history)은 그대로 보존(=[히스토리 보기]).
    body: {raceKey?, pruneStarters?} — raceKey 주면 그 경주만, 없으면 전체 활성 초기화.
    pruneStarters=true(기본): 이전 경주 전적(starters)도 함께 정리(잔존마 방지)."""
    body = request.json or {}
    rk = (body.get("raceKey") or "").strip()
    db = _triple_load()
    if rk:
        removed = 1 if db.pop(rk, None) is not None else 0
    else:
        removed = len(db)
        db = {}
    _triple_save(db)
    # [3번] 이전 경주 starters 정리(새 경주 raceKey만 유지 → 잔존마 원천 차단). 명시적으로 끄지 않는 한 수행.
    starters_removed = 0
    if body.get("pruneStarters", True):
        starters_removed = _starters_prune(keep_rk=rk or None)
    print(f"[새 경주] 활성 3종 초기화: {rk or '전체'} ({removed}건). 히스토리 파일은 보존.")
    return jsonify({"ok": True, "cleared": rk or "all", "removed": removed,
                    "startersRemoved": starters_removed})


@app.route("/api/starters/reset", methods=["POST"])
def starters_reset():
    """[잔존마 방어·3번] 경주 전환 시 이전 경주 전적(starters) 정리.
    body: {keepRaceKey?} — 주면 그 경주(+한국 PDF)만 유지, 없으면 한국 PDF 외 전부 삭제.
    triple_store(배당)는 건드리지 않음(전적만) → 방금 전환한 새 경주 배당은 보존."""
    keep = ((request.json or {}).get("keepRaceKey") or "").strip() or None
    removed = _starters_prune(keep_rk=keep)
    return jsonify({"ok": True, "startersRemoved": removed, "kept": keep or "korea만"})


# ══════════════ [경주 고정·서버측 raceKey 잠금] ══════════════
#   사용자가 경주를 고정하면 서버가 current_race/triple_latest에서 '항상 그 경주'를 반환한다.
#   → oddspark 자동수집이 다른 경주(오비히로)를 최신으로 저장해도 분석기는 고정 경주(사가)를 유지.
#   파일에 저장(서버 재시작에도 고정 유지) → "서버 재시작해도 반복" 문제 근본 차단.
RACE_PIN_FILE = os.path.join(os.path.dirname(__file__), "data", "race_pin.json")


def _race_pin_load():
    """고정된 raceKey 반환(없으면 None). 파일 기반 → 서버 재시작에도 유지."""
    try:
        d = json.load(open(RACE_PIN_FILE, encoding="utf-8"))
        rk = (d.get("raceKey") or "").strip()
        return rk or None
    except Exception:
        return None


def _race_pin_save(rk):
    os.makedirs(os.path.dirname(RACE_PIN_FILE), exist_ok=True)
    json.dump({"raceKey": (rk or "").strip(), "pinnedAt": time.time(),
               "date": time.strftime("%Y-%m-%d")},   # [날짜 초기화] 고정 날짜 기록 → 어제 고정 자동 해제 판정
              open(RACE_PIN_FILE, "w", encoding="utf-8"), ensure_ascii=False)


def _race_pin_clear():
    try:
        os.remove(RACE_PIN_FILE)
    except OSError:
        pass


@app.route("/api/race/pin", methods=["GET", "POST"])
def race_pin():
    """[경주 고정] GET → 현재 고정 경주 조회. POST {raceKey} → 고정 / POST {clear:true} → 해제.
    고정 시 current_race·triple_latest가 명시 요청 없으면 항상 고정 경주를 반환(서버측 잠금)."""
    if request.method == "POST":
        body = request.json or {}
        if body.get("clear"):
            _race_pin_clear()
            print("[경주 고정] 해제 → 자동 전환 재개")
            return jsonify({"ok": True, "pinned": None})
        rk = (body.get("raceKey") or "").strip()
        if not rk:
            return jsonify({"error": "raceKey가 필요합니다."}), 400
        _race_pin_save(rk)
        print(f"[경주 고정] 서버측 잠금: {rk} (자동 전환 차단·재시작에도 유지)")
        return jsonify({"ok": True, "pinned": rk})
    return jsonify({"pinned": _race_pin_load()})


@app.route("/api/odds/triple/latest", methods=["GET", "POST"])
def triple_latest():
    """최근(또는 지정 raceKey) 3종 배당 조회 → {raceKey, quinella, exacta, trio}.
    raceKey 명시 & 데이터 없으면 빈 결과(waiting) — 이전 경주로 폴백하지 않음.
    [경주 고정] 명시 요청이 없고 서버 고정이 걸려 있으면 항상 고정 경주를 반환(자동 전환 차단)."""
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
        # [경주 고정] 명시 요청 없을 때: 서버 고정 경주가 있고 데이터가 있으면 그걸 우선(자동 전환 차단)
        _pin = _race_pin_load()
        if _pin and _pin in db:
            rk = _pin
        else:
            rk = max(db.keys(), key=lambda k: db[k].get("t", 0))
    rec = db.get(rk) or {}
    # [경주전환 잔존 방어] 30분+ 미갱신 최신 경주 = 끝난 경주 → stale 표기(프론트가 표시 억제)
    age = time.time() - (rec.get("t") or 0)
    return jsonify({"raceKey": rk, "quinella": rec.get("quinella", []),
                    "exacta": rec.get("exacta", []), "trio": rec.get("trio", []),
                    "ageSeconds": round(age), "stale": (not explicit) and age > STALE_ACTIVE_SEC})


def _anomaly_pretty(raw):
    """스냅샷 이상감지 원문 → {text, severity} 로 정규화(사람이 읽는 누적 피드용)."""
    m = re.search(r"(-?\d+)\s*%", raw or "")
    pct = int(m.group(1)) if m else None
    sev = "🔴" if ("역전" in raw or (pct is not None and pct <= -30)) else "🟡"
    if raw.startswith("급락감지:"):
        combo = raw.split(":", 1)[1].strip().split()[0]
        text = f"{combo} 복승 급락 {pct}%" if pct is not None else f"{combo} 복승 급락"
    elif raw.startswith("단승급락:"):
        mm = re.search(r"(\d+)번", raw)
        no = mm.group(1) if mm else "?"
        text = f"{no}번 단승 급락 {pct}%" if pct is not None else f"{no}번 단승 급락"
    elif raw.startswith("쌍승역전:"):
        pair = raw.split(":", 1)[1].strip()
        text = f"쌍승 역전 {pair}"
    else:
        text = raw
    return {"text": text, "severity": sev, "pct": pct}


def _anomaly_combo(raw):
    """스냅샷 이상감지 원문 → 조합 문자열(예 '2+3'·'4'·'5→10')."""
    raw = raw or ""
    if raw.startswith("급락감지:"):
        return raw.split(":", 1)[1].strip().split()[0]
    if raw.startswith("단승급락:"):
        m = re.search(r"(\d+)번", raw)
        return m.group(1) if m else ""
    if raw.startswith("쌍승역전:"):
        return raw.split(":", 1)[1].strip().split()[0]
    return ""


def _anomaly_events_from_doc(doc):
    """[이상감지 누적·공용] 스냅샷 doc → 시간순·중복제거 이상감지 이벤트 리스트.
    anomaly-feed 엔드포인트와 분석로그(anomaly_history)가 동일 규칙으로 공유(경주별 분리)."""
    events, seen = [], set()
    for s in (doc.get("snapshots") or []):
        for raw in (s.get("anomalies") or []):
            p = _anomaly_pretty(raw)
            # [초반미수집 방어] opening 배당 정착으로 이미 기록된 가짜 급락(-88%↓)은 숨김
            #   (복승/단승 급락만 · 쌍승역전은 유지). 물리적으로 정상 시장의 복승 -88%↓는 거의 없음.
            if p["pct"] is not None and p["pct"] <= -88 and ("복승 급락" in p["text"] or "단승 급락" in p["text"]):
                continue
            if p["text"] in seen:      # 최초 감지 시각만 유지(중복 누적 방지)
                continue
            seen.add(p["text"])
            events.append({"time": s.get("time"), "minutes_before": s.get("minutes_before"),
                           "text": p["text"], "severity": p["severity"], "pct": p["pct"],
                           "combo": _anomaly_combo(raw), "t": s.get("t")})
    events.sort(key=lambda e: e.get("t") or 0)   # 시간순
    return events


def _concentration_counts(doc):
    """[복병_집중급락] 경주 이상감지 이벤트에서 말별 등장 횟수(급락/역전 조합에 낀 횟수).
    마에바시 8R 복기: 8번이 낀 조합 다수가 동시 급락 → 15회 최다 = 자금 집중 복병 신호.
    반환 {말번호(int): 등장횟수}."""
    counts = {}
    for e in _anomaly_events_from_doc(doc):
        combo = e.get("combo")
        nos = combo if isinstance(combo, (list, tuple)) else re.split(r"[+\-↔→]", str(combo or ""))
        for h in nos:
            try:
                counts[int(str(h).strip())] = counts.get(int(str(h).strip()), 0) + 1
            except (TypeError, ValueError):
                pass
    return counts


@app.route("/api/odds/anomaly-feed", methods=["GET", "POST"])
def anomaly_feed():
    """[이상감지 누적] 경주별 스냅샷에서 감지된 이상을 시간순·중복제거로 누적 반환.
    스냅샷은 삭제되지 않으므로 마감 후에도, 새 수집 후에도 과거 감지가 유지된다.
    body/query: {raceKey}. → {raceKey, events:[{time,minutes_before,text,severity,pct,combo,t}]}"""
    if request.method == "POST":
        rk = ((request.json or {}).get("raceKey") or "").strip()
    else:
        rk = (request.args.get("raceKey") or "").strip()
    if not rk:
        return jsonify({"raceKey": rk, "events": []})
    path, _, _ = _hist_path(rk)
    try:
        doc = json.load(open(path, encoding="utf-8"))
    except Exception:
        return jsonify({"raceKey": rk, "events": []})
    return jsonify({"raceKey": rk, "events": _anomaly_events_from_doc(doc)})


@app.route("/api/learning/near-miss", methods=["GET"])
def learning_near_miss():
    """[2·3번] 4착 near-miss 케이스 + 4착 빈번(2회+) 말 목록(다음 경주 삼복승 보험픽 우선 고려)."""
    d = _near_miss_load()
    cases = sorted(d.get("cases", []), key=lambda c: -(c.get("t") or 0))
    cnt = {}
    for c in cases:
        nm = (c.get("name") or "").strip()
        if nm:
            cnt[nm] = cnt.get(nm, 0) + 1
    frequent = sorted([{"name": k, "count": v} for k, v in cnt.items() if v >= 2],
                      key=lambda x: -x["count"])
    return jsonify({"cases": cases, "count": len(cases), "frequent": frequent,
                    "note": "추천 말이 4착(아깝게 미적중)한 경주 — 4착 빈번 말은 삼복승 보험픽 우선 고려"})


@app.route("/api/signal-types/stats", methods=["GET"])
def signal_types_stats():
    """[강한 신호 8유형 5번] 유형별 발생/적중/적중률(입상 기준) — 적중률 높은 유형 가중치 상향 근거."""
    rates = _signal_type_hitrates()
    total = len(_signal_type_stats_load().get("races", {}))
    rows = sorted(rates.values(), key=lambda r: (-(r.get("rate") or -1), -(r.get("fired") or 0)))
    return jsonify({"types": rows, "raceCount": total,
                    "note": "유형별 신호말이 1~3착 입상한 비율. 표본이 쌓일수록 적중률 높은 유형을 강조합니다."})


@app.route("/api/compression/stats", methods=["GET"])
def compression_stats():
    """[저배당 압축 패턴 4번] 압축 패턴(4배↓2두+/5배↓3두+) 발생 시 실제 복승 적중률."""
    hr = _compression_hitrate()
    total = len(_compression_stats_load().get("races", {}))
    return jsonify({"hitRate": hr, "raceCount": total,
                    "note": "유력마 TOP3 중 저배당 밀집(축 패턴) 발생 경주의 복승(2두 정확) 적중률. 급락 결합 시 최강."})


@app.route("/api/third-place/stats", methods=["GET"])
def third_place_stats():
    """[배당 3착 자동 발굴 5번] 신호 유형별 고배당 3착 후보의 입상·정확3착 적중률."""
    hr = _third_place_hitrate()
    total = len(_third_place_stats_load().get("races", {}))
    rows = sorted(hr.values(), key=lambda r: (-(r.get("exact3rdRate") or -1), -(r.get("fired") or 0)))
    return jsonify({"buckets": rows, "raceCount": total,
                    "note": "저배당 압축 축 2두 확정 후 고배당 3착 후보(급락/역배열/전적/연속하락)의 3착·입상 적중률."})


@app.route("/api/learning/pattern-confidence", methods=["GET"])
def pattern_confidence():
    """[복기 학습 재설계 2·4번] 신호 유형별 발생/적중/적중률 + 통계 신뢰도(표본 50회 게이팅). 공식 수정 없음."""
    return jsonify(_pattern_confidence())


@app.route("/api/thresholds/optimize", methods=["GET"])
def thresholds_optimize():
    """[기준치 도출 2·3·5번] 누적 데이터로 신호 기준치별 적중률·최적 기준치 추천(자동 적용 안 함)."""
    return jsonify(_threshold_candidates())


@app.route("/api/thresholds/config", methods=["GET"])
def thresholds_config():
    """[기준치 도출 4번] 현재 적용된 기준치 + 변경 이력."""
    c = _threshold_config_load()
    return jsonify({"current": {k: c.get(k) for k in _THRESHOLD_DEFAULTS}, "history": c.get("history")})


@app.route("/api/thresholds/apply", methods=["POST"])
def thresholds_apply():
    """[기준치 도출 4번] 사람이 승인한 기준치를 config에 반영(이전값 자동 백업). body{key,value} 또는 body{current:{...}}."""
    body = request.get_json(silent=True) or {}
    cur = _threshold_config_load()
    new = {k: cur.get(k) for k in _THRESHOLD_DEFAULTS}
    prev = dict(new)
    if body.get("current"):
        for k, v in body["current"].items():
            if k in _THRESHOLD_DEFAULTS:
                new[k] = v
    elif body.get("key") in _THRESHOLD_DEFAULTS:
        try:
            new[body["key"]] = float(body.get("value"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "value 숫자 필요"}), 400
    else:
        return jsonify({"ok": False, "error": "key/value 또는 current 필요"}), 400
    if new == prev:
        return jsonify({"ok": True, "changed": False, "current": new})
    _threshold_config_save(new, applied_note=body.get("note") or "수동 승인", prev=prev)
    return jsonify({"ok": True, "changed": True, "current": new, "backup": prev})


@app.route("/api/high-odds-companion/stats", methods=["GET"])
def high_odds_companion_stats():
    """[고배당 동반 패턴 4번] 고배당(7배+) 1착 경주 중 3착 내 다른 고배당 포함 비율."""
    hr = _high_odds_companion_hitrate()
    return jsonify({**hr,
                    "note": "고배당(7배+) 1착 경주만 분모. 3착 내 다른 고배당 포함 비율 → 참고 추천 신뢰도."})


@app.route("/api/after-close/cases", methods=["GET"])
def after_close_cases():
    """[4번] 마감(T-0) 후 감지되어 베팅 반영 불가했던 신호 케이스 목록 → 수집 간격 단축 필요성 근거."""
    d = _after_close_load()
    cases = sorted(d.get("cases", []), key=lambda c: -(c.get("t") or 0))
    return jsonify({"cases": cases, "count": len(cases),
                    "note": "마감 후 감지된 신호(베팅 반영 불가) — 수집 간격 단축의 필요성 근거 데이터"})


@app.route("/api/after-close/stats", methods=["GET"])
def after_close_stats_api():
    """[3번] 마감 후 대급락(50%+) → 실제 입상률 통계(패턴 신뢰도 측정)."""
    return jsonify(_after_close_stats())


@app.route("/api/learning/signal-lessons", methods=["GET", "POST"])
def learning_signal_lessons():
    """초과급락 신호말이 입상했으나 노이즈 판정으로 추천 누락된 사례(집중신호 오판 학습).
    POST로 사례 수동 추가 가능(race/result/signal_horse/lesson 등)."""
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        _record_signal_lesson(body)
    d = _signal_lesson_load()
    cases = sorted(d.get("cases", []), key=lambda c: -(c.get("t") or 0))
    return jsonify({"cases": cases, "count": len(cases),
                    "note": "초과급락 신호 → 실제 입상인데 노이즈 판정으로 추천 미반영된 사례. "
                            "절대 10%+ 급락은 집중신호로 승격(수정 완료)"})


@app.route("/api/current_race", methods=["GET"])
def current_race():
    """확장이 마지막으로 수집한 '현재 경주' 반환 → {raceKey, updatedAt, counts}.
    분석기 상단 '경주 새로고침' 바가 폴링해 현재 경주명을 표시·자동 전환한다.
    (배당 본문 없이 경주명만 필요하므로 triple/latest 보다 가볍다.)"""
    db = _triple_load()
    if not db:
        return jsonify({"raceKey": None})
    # [경주 고정·서버측 잠금] 고정 경주가 있으면 max-t/번호진행 폴백을 건너뛰고 항상 고정 경주 반환.
    #   → oddspark가 오비히로를 최신 저장해도 분석기는 고정한 사가를 유지(자동 전환 원천 차단).
    _pin = _race_pin_load()
    if _pin:
        _prec = db.get(_pin) or {}
        _page = time.time() - (_prec.get("t") or 0)
        return jsonify({
            "raceKey": _pin, "pinned": True,
            "updatedAt": _prec.get("t"), "ageSeconds": round(_page),
            "stale": bool(_prec) and _page > STALE_ACTIVE_SEC,
            "counts": {"quinella": len(_prec.get("quinella") or []),
                       "exacta": len(_prec.get("exacta") or []),
                       "trio": len(_prec.get("trio") or [])},
        })
    rk = max(db.keys(), key=lambda k: db[k].get("t", 0))
    # [stale 루프 backstop] oddspark가 끝난 경주(예: 카사마츠 3R) 확정배당을 계속 재수집해 그 경주가
    #   영원히 '최신(max-t)'으로 남아 다음 경주로 못 넘어가던 문제 방어. 같은 경마장에서 최근(10분내)
    #   갱신된 '더 높은 경주번호'가 있으면 그쪽을 현재 경주로 우선(경주는 번호순 진행 → 뒤 경주가 현재).
    #   다른 경마장(소노다·카사마츠 동시)은 영향 없음(경마장 토큰 일치 시에만 비교).
    try:
        _now = time.time()
        _area, _num = _area_num(rk)
        if _area and _num is not None:
            for _k, _r in db.items():
                if _k == rk:
                    continue
                if (_now - (_r.get("t") or 0)) > 600:      # 10분 넘게 미갱신은 후보 아님
                    continue
                _a2, _n2 = _area_num(_k)
                if _a2 == _area and _n2 is not None and _n2 > _num:
                    rk, _num = _k, _n2                     # 같은 장·더 높은 번호 = 현재 경주
    except Exception:
        pass
    rec = db.get(rk) or {}
    # [경주전환 잔존 방어] 최신 경주도 30분+ 미갱신이면 '끝난 경주' → stale 표기(프론트가 표시 억제)
    age = time.time() - (rec.get("t") or 0)
    return jsonify({
        "raceKey": rk,
        "updatedAt": rec.get("t"),
        "ageSeconds": round(age),
        "stale": age > STALE_ACTIVE_SEC,
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


# ── [유력마/제거마 공식 개선] 확률·기대값·배당신뢰도·기수 복승률 ──────────
JOCKEYS_JSON = os.path.join(os.path.dirname(__file__), "static", "data", "jockeys.json")
_JOCKEYS_CACHE = {"mtime": None, "byName": {}}


def _jockey_place_rate(name):
    """기수 복승권율(%) — static/data/jockeys.json(KRA 실데이터, mtime 캐시). 없으면 None."""
    if not name:
        return None
    try:
        mt = os.path.getmtime(JOCKEYS_JSON)
    except OSError:
        return None
    if _JOCKEYS_CACHE["mtime"] != mt:
        try:
            data = json.load(open(JOCKEYS_JSON, encoding="utf-8"))
            _JOCKEYS_CACHE["byName"] = {j.get("name"): j for j in data.get("jockeys", [])}
            _JOCKEYS_CACHE["mtime"] = mt
        except Exception:
            return None
    j = _JOCKEYS_CACHE["byName"].get(name.strip())
    return (j or {}).get("placeRate")


def _placings_list(placings):
    return [int(p) for p in (placings or [])[:5] if isinstance(p, (int, float)) and p > 0]


def _avg_placing(placings):
    ps = _placings_list(placings)
    return round(sum(ps) / len(ps), 1) if ps else None


def _prob_ev(o, placings):
    """[배당 우선 전환] 시장확률=1/배당×0.75 · 전적확률=최근입상수/경주수 · 통합=시장0.7+전적0.3 · 기대값=통합×배당-1.
    시장(배당)을 우선하되 전적은 배당 기반으로 가감한다:
      배당 10배+ = 시장이 비인기로 봄 → 전적확률 × 0.7(30% 감점)
      배당 5배↓  = 시장이 유력하게 봄 → 전적확률 × 1.1(10% 보정)
    (v2.2.0 이전: 시장0.6+전적0.4 · 가감 없음 — 전적 과가중으로 고배당 전적마가 저배당 시장마를 눌렀음)."""
    market = (1.0 / o * 0.75) if (o and o > 0) else 0.0
    ps = _placings_list(placings)
    form = (sum(1 for p in ps if p <= 3) / len(ps)) if ps else 0.0
    if o is not None and o >= 10:
        form *= 0.7        # 시장 비인기 → 전적 감가
    elif o is not None and o <= 5:
        form = min(1.0, form * 1.1)   # 시장 유력 → 전적 보정(상한 1.0)
    combined = market * 0.7 + form * 0.3
    ev = (combined * o - 1) if (o and o > 0) else None
    return (round(market * 100, 1), round(form * 100, 1), round(combined * 100, 1),
            (round(ev * 100, 1) if ev is not None else None))


def _confidence(ftotal, o):
    """[1번] 배당신뢰도(-10~+30): 전적우수+배당낮음→+30, 전적불량+배당낮음→-10(이변의심), 배당낮음 단독→0."""
    if o is None or o >= 30:      # 배당 낮음(<30배) 일 때만 신뢰도 판정
        return 0
    if ftotal is None:
        return 0
    if ftotal >= 61:
        return 30
    if ftotal <= 40:
        return -10
    return 0


def _fav_score(ftotal, o, jk_rate):
    """[1번] 유력마 점수 = 전적40% + 배당신뢰도30% + 기수20% + 조건적성10% (각 0~100)."""
    form_sub = ftotal if ftotal is not None else 40           # 전적 미수집 → 중립 40
    low = (o is not None and o < 30)
    if low and ftotal is not None and ftotal >= 61:
        conf_sub = 90                                          # 전적우수+배당낮음 = 신뢰 높음
    elif low and ftotal is not None and ftotal <= 40:
        conf_sub = 20                                          # 전적불량+배당낮음 = 이변 의심
    elif low:
        conf_sub = 55
    else:
        conf_sub = 40                                          # 배당 높음 = 시장 지지 약함
    jk_sub = jk_rate if jk_rate is not None else 40            # 기수 복승률(미상 40)
    apt_sub = 50                                               # 조건적성(거리/코스 데이터 부족 → 중립)
    return round(form_sub * 0.4 + conf_sub * 0.3 + jk_sub * 0.2 + apt_sub * 0.1, 1)


def _elim_score(o, avg_place, jk_rate, has_drop30, in_top_exa, no_dist_exp=False):
    """[2번] 제거 점수(기본 100에서 감점 + 이변 보류 가점). 낮을수록 제거 대상.
    no_dist_exp=True → '현재 거리 경험 없음 -15'. 거리 이력 데이터 확보 시
    호출부(_elimination)가 마별 플래그를 넘기면 자동 활성화(현재는 데이터 미수집→기본 False)."""
    score = 100
    reasons = []
    if o is None or o >= 150:
        score -= 40
        reasons.append("배당 150배+ -40")
    elif o >= 80:
        score -= 20
        reasons.append("배당 80~149 -20")
    if avg_place is not None and avg_place >= 5:
        score -= 30
        reasons.append(f"최근평균 {avg_place}착 -30")
    if jk_rate is not None and jk_rate < 10:
        score -= 10
        reasons.append(f"기수 복승률 {jk_rate}% -10")
    if no_dist_exp:
        score -= 15
        reasons.append("현재 거리 경험 없음 -15")
    if has_drop30:
        score += 30
        reasons.append("배당 급락 30%+ +30(제거 보류)")
    if in_top_exa:
        score += 20
        reasons.append("쌍승 상위 +20(제거 보류)")
    return max(0, min(130, score)), reasons


def _elimination(curQ, curD, exa, drops, form, trio_map=None, curWin=None):
    """배당+전적 복합 제거 판정. 반환: {horses:[...], counts, autoBets}.
    curWin(단승 배당 맵) 주면 [1·5번] 고배당 자동 제거(999/100+ 무조건·50~99 권장·30~49 관찰)."""
    trio_map = trio_map or {}
    win_odds = {int(k): v for k, v in (curWin or {}).items()
                if isinstance(v, (int, float)) and v > 0}   # [1·5번] 단승 고배당 제거용
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
        # [전적 과가중 근본 해결] 저배당(단승/복승 대표 5배 이하) = 시장이 유력하다고 봄 →
        #   전적 미수집/저조해도 전적 점수 최소 30점 보장(유력마 제외 방지). 후쿠시마 2R 6번(4.9배·전적0) 케이스.
        _mkt_odds = o
        _wo = win_odds.get(no)
        if _wo is not None and (_mkt_odds is None or _wo < _mkt_odds):
            _mkt_odds = _wo
        market_favorite = (_mkt_odds is not None and _mkt_odds <= 5.0)
        form_floored = False
        if market_favorite and (ftotal is None or ftotal < 30):
            ftotal = 30.0
            form_floored = True
        # [배당 우선 전환] 전적 점수 배당 기반 가감(랭킹용 formScoreAdj, 원본 formScore는 표시·등급용 보존):
        #   대표배당 10배+ = 시장 비인기 → 전적 × 0.7(30% 감점)
        #   대표배당 5배↓  = 시장 유력   → 전적 × 1.1(10% 보정, 상한 100)
        form_odds_mult = 1.0
        ftotal_ranked = ftotal
        if ftotal is not None and _mkt_odds is not None:
            if _mkt_odds >= 10:
                form_odds_mult = 0.7
            elif _mkt_odds <= 5:
                form_odds_mult = 1.1
            ftotal_ranked = round(min(100.0, ftotal * form_odds_mult), 1)
        # 이중수렴 = 저배당(시장 유력) + 전적 우수(≥61) → 확신도 대폭 상승 → 강력 추천
        dual_converge = (market_favorite and ftotal is not None and ftotal >= 61 and not form_floored)
        # 전적 우수하나 배당 높음(15배+) → 자동 보험 등급 하향(유력마 풀엔 두되 '보험용'으로 취급·경고)
        insurance_demote = (ftotal is not None and ftotal >= 61
                            and _mkt_odds is not None and _mkt_odds >= 15)
        placings = (fh or {}).get("recentPlacings") or []
        avg_place = _avg_placing(placings)
        jk_rate = _jockey_place_rate((fh or {}).get("jockey"))
        os_ = _odds_score(o)           # (보존) 기존 배당점수
        fadj = _form_adj(ftotal)       # (보존) 기존 전적보정
        has_drop30 = no in drop30
        in_top_exa = no in top_exa

        # [2번] 제거 점수(신 공식) → verdict 구동
        #   거리경험 플래그: 전적표에 현재 거리 주행 이력이 명시적으로 '없음'일 때만 True.
        #   (현재 거리 이력 미수집 → fh.noDistExp 부재 시 None→False, 감점 미적용)
        no_dist_exp = bool(fh.get("noDistExp")) if fh else False
        total, elim_reasons = _elim_score(o, avg_place, jk_rate, has_drop30, in_top_exa, no_dist_exp)
        # [1·5번] 단승 고배당 자동 제거 판정(신호 있으면 30~99배는 보류·999/100+는 무조건)
        wo = win_odds.get(no)
        ho_cut, ho_reason = _high_odds_verdict(wo, has_drop30 or in_top_exa)
        if ho_reason:
            elim_reasons.append(ho_reason)
        if total < 30:
            verdict, label, keep = "🔴", "확실 제거", False
        elif total < 50:
            verdict, label, keep = "🟠", "제거 권장", False
        elif total < 70:
            verdict, label, keep = "🟡", "관찰", True
        else:
            verdict, label, keep = "🟢", "유력 후보", True

        # [1번] 유력마 점수 + 배당신뢰도 · [3번] 확률/기대값
        fav = _fav_score(ftotal, o, jk_rate)
        conf = _confidence(ftotal, o)
        mkt_p, form_p, comb_p, ev = _prob_ev(o, placings)

        o_txt = ('%g배' % o) if o is not None else '미수집'
        reason = f"배당 {o_txt} · " + (" / ".join(elim_reasons) if elim_reasons else "감점 없음") + f" = {total}점"

        # 이변 신호로 제거 보류(점수엔 이미 가점됨) 표시
        override, ov_reasons = False, []
        if not keep and (has_drop30 or in_top_exa):
            override = True
            if has_drop30:
                ov_reasons.append(f"배당 급락 {drop30[no]}%")
            if in_top_exa:
                ov_reasons.append("쌍승 상위10")

        # [5번] 세부 등급: 전적/배당/기수 중 몇 가지가 우수한가
        good_form = (ftotal is not None and ftotal >= 61)
        low_odds = (o is not None and o < 30)
        good_jockey = (jk_rate is not None and jk_rate >= 30)
        strong_cnt = sum([good_form, low_odds, good_jockey])
        anomaly_sig = has_drop30 or in_top_exa or bool(fh and fh.get("anomaly"))
        tier = None
        if keep:
            if strong_cnt >= 3:
                tier = "⭐"       # 강력유력: 전적+배당+기수 모두 우수
            elif strong_cnt == 2:
                tier = "★"        # 유력: 2가지 이상 우수
            else:
                tier = "△"        # 관찰: 1가지 이하
        horses.append({
            "no": no, "name": (fh or {}).get("name", ""),
            "oddsRepr": o, "oddsScore": os_,
            "formScore": ftotal, "formAdj": fadj, "total": total,
            "avgPlacing": avg_place, "jockeyPlaceRate": jk_rate,
            "favScore": fav, "confidence": conf,
            "marketProb": mkt_p, "formProb": form_p, "combinedProb": comb_p, "ev": ev,
            "verdict": verdict, "verdictLabel": label, "keep": keep,
            "tier": tier, "override": override, "strongCount": strong_cnt,
            "overrideReason": " · ".join(ov_reasons), "anomalySig": anomaly_sig,
            "winOdds": wo, "highOddsCut": ho_cut, "highOddsReason": ho_reason,
            "marketFavorite": market_favorite, "formFloored": form_floored,
            "formScoreAdj": ftotal_ranked, "formOddsMult": form_odds_mult,
            "dualConverge": dual_converge, "insuranceDemote": insurance_demote,
            "reason": reason,
        })

    # [2번·제거 완화] 과다 제거 방지(1마리 빼고 전부 제거는 비정상).
    #   확실 제거 = '전적 D등급(또는 전적정보 없음) + 배당 150배+' 인 말만. 전적 C등급 이상은 신호 없어도 후보 유지.
    #   ⚠ 기존 _elim_score/verdict(30/50/70)는 참고 점수로 보존 — 최종 keep에만 완화 게이트를 덧씌운다.
    graded = [h for h in horses if h.get("formScore") is not None]
    fgrade = {}
    if graded:
        gs = sorted(graded, key=lambda h: -(h["formScore"] or 0))
        ng = len(gs)
        for i, h in enumerate(gs):
            frac = i / ng
            fgrade[h["no"]] = "A" if frac < 0.25 else "B" if frac < 0.50 else "C" if frac < 0.75 else "D"
    for h in horses:
        h["formGrade"] = fgrade.get(h["no"])
        g = h["formGrade"]
        o = h.get("oddsRepr")
        # [1·5번] 단승 고배당 강제 제거 — 완화 게이트보다 우선.
        #   999/100배+ = 전적·이변신호 무관 확실 제거(제주케이스: 착순 좋아도 무시).
        #   50~99·30~49배 = 이변신호 없을 때만 제거(_high_odds_verdict 가 has_signal 반영).
        if h.get("highOddsCut"):
            wo_ = h.get("winOdds")
            force = (isinstance(wo_, (int, float)) and wo_ >= 100)
            if force or not h["override"]:
                h["keep"] = False
                h["softCut"] = True
                if force:
                    h["override"] = False
                h["verdict"], h["verdictLabel"] = "🔴", "확실 제거(" + (h.get("highOddsReason") or "고배당") + ")"
                continue
        high_odds = (o is None or o >= 150)
        # 확실 제거 = 전적 D등급(또는 전적없음) + 배당 150배+ + 이변신호 없음(급락/쌍승상위=override) → 그 외 전부 유지.
        hard_cut = high_odds and (g in (None, "D")) and not h["override"]
        if hard_cut:
            if h["keep"]:                              # 원래 유지였어도 규칙상 확실 제거로 확정
                h["keep"] = False
                h["softCut"] = True
                h["verdict"], h["verdictLabel"] = "🔴", "확실 제거(전적D+150배+)"
        else:
            if not h["keep"] and not h["override"]:    # 원래 제거였으면 완화 유지
                h["keep"] = True
                h["softKept"] = True
                if g in ("A", "B", "C"):
                    h["softKeepReason"] = "전적 " + g + "등급 → 유지(신호 없어도)"
                else:
                    h["softKeepReason"] = "배당 " + (("%g배" % o) if o is not None else "미수집") + " (150배 미만) → 유지"
                if h["verdict"] in ("🔴", "🟠"):
                    h["verdict"], h["verdictLabel"] = "🟡", "관찰(완화 유지)"

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
            "detail": reasons.get(d["level"], ""),
            # [저/고배당 분리] 고배당(직전 50배+)은 절대값 아닌 %로만 판단·하단 참고 노출
            "oddsBefore": d["prev"], "oddsAfter": d["cur"], "dropPct": d["pct"],
            "highOdds": (d["prev"] or 0) >= 50}
           for k, d in best.items()]
    out.sort(key=lambda s: _TIER_RANK.get(s["level"], 0), reverse=True)
    return out


def _integrated_grades(form, curQ, curD, weights=None):
    """[통합분석] 전적 + 배당(이상감지) 결합 점수 → A/B/C/D 재부여. 기본 전적 30% + 배당 70%(배당 우선).
    [3번] weights=(fw,ow) 미지정 시 학습 가중치 자동 적용(50경주+ 누적 시 적중률 비교로 조정).
    form=[{no,name,jockey,totalScore}]. 배당 대표값=해당 말이 낀 최저 복승(없으면 쌍승)배당."""
    if not form:
        return None
    fw, ow = weights if weights else _learned_integrated_weights()
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
        integ = round(fw * fscore + ow * oscore, 1)
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


def _extract_patterns(drops, reversals, signals, curQ, bet_rec, advanced=None):
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
    # [연속하락] 말별 배당이 2회+ 연속 하락(horseStreaks count>=2·level 🟡/🔴) → 자금 지속유입 신호
    streaks = (advanced or {}).get("horseStreaks") or {}
    if any((v or {}).get("count", 0) >= 2 for v in streaks.values()):
        pats.append("연속하락")
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


# ───────── [대규모 급락 패턴] 전체 조합 동시 급락 감지 + 베팅 전략 ─────────
#   특정 유력마 없이 시장 전체에 자금이 퍼진 상태(자금 분산) → 이변 가능성↑.
#   히스토리 분석 결과 수집 경주의 다수에서 반복 관측됨(반복 패턴).
def _mass_drop_detect(drops, curQ, minutes_before=None):
    """전체 복승 조합의 50%+ 또는 30개+ 가 동시에 30%+ 급락하면 '대규모 급락'."""
    total = len(curQ or {})
    if total < 8:
        return None
    dropped = [d for d in (drops or []) if d.get("pct") is not None and d["pct"] <= -30]
    ratio = round(len(dropped) / total, 3) if total else 0.0
    if ratio >= 0.50 or len(dropped) >= 30:
        return {"detected": True, "dropped": len(dropped), "total": total, "ratio": ratio,
                "minutes_before": minutes_before,
                "note": "🌊 대규모 자금 분산 패턴 — 특정 유력마 없음 · 이변 가능성↑ · 중배당 조합 병행 체크 권장"}
    return None


def _apply_mass_drop_strategy(bet_rec, mass_drop, drops, curQ):
    """[대규모급락 전략] 삼복승 보험 8%→15% 확대 + 중배당(7~40배) 급락 복승 보험 추가 +
    최저배당 복승 신뢰도 하락 표기. 기존 조합은 삭제하지 않고 alloc 조정·보험 추가만(합계≈100 유지)."""
    if not mass_drop or not bet_rec:
        return None
    main = next((b for b in bet_rec if b.get("label") == "복승 메인"), None)
    ins = [b for b in bet_rec if b.get("kind") == "삼복승" and "보험" in (b.get("label") or "")]
    changes, deduct = [], 0.0
    cur_ins = sum(b.get("alloc", 0) for b in ins)
    if ins and cur_ins < 15:
        add = round((15 - cur_ins) / len(ins), 1)
        for b in ins:
            b["alloc"] = round(b.get("alloc", 0) + add, 1)
        deduct += add * len(ins)
        changes.append(f"삼복승 보험 {int(cur_ins)}%→15% 확대")
    # 중배당(7~40배) 급락 복승 보험 1개 추가(급락 가장 큰 것)
    mids = [(d, curQ.get(tuple(sorted(d["combo"]))))
            for d in (drops or []) if d.get("pct") is not None and d["pct"] <= -30]
    mids = sorted([(d, o) for d, o in mids if o and 7 <= o <= 40], key=lambda x: x[0]["pct"])
    if mids:
        d, o = mids[0]
        cc = sorted(int(x) for x in d["combo"])
        if not any(b.get("kind") == "복승" and b.get("combo") == cc for b in bet_rec):
            bet_rec.append({"kind": "복승", "label": "복승 중배당보험(대규모급락)", "combo": cc,
                            "alloc": 6, "expOdds": o, "massDrop": True})
            deduct += 6
            changes.append(f"중배당 복승 보험 {cc[0]}+{cc[1]}({o}배) 추가")
    if main and deduct:
        main["alloc"] = max(8, round(main.get("alloc", 43) - deduct, 1))
    if main:
        main["massDropNote"] = "대규모 급락 → 최저배당 조합 신뢰도 하락(중배당·삼복승 분산 권장)"
    return {"applied": True, "changes": changes}


# ───────── [신호 품질 필터링] 초과 급락률·상황별 가중치·조합 품질 ─────────
#   시장 전체가 함께 내려가는 '노이즈'와, 특정 말에 자금이 집중된 '진짜 신호'를 구분한다.
def _excess_drop_analysis(drops, curQ):
    """[1번] 초과 급락률 — 시장 전체 평균 급락 대비 각 말의 집중도.
    drops=[{combo:[a,b], pct}] (pct<0=급락=자금유입). 반환:
      {overall: 전체평균급락%, horses:{no:{avg,excess,grade,combos}}, concentrated:[🔴 말], count}.
    excess = 해당말 평균급락% - 전체평균급락% (음수일수록 평균보다 더 급락=자금집중).
      excess<=-5 → 🔴 진짜신호 / -5<excess<0 → 🟡 약한신호 / excess>=0 → 노이즈(시장 전체 급락).
    [긴급수정] 절대 급락 10%+(avg<=-10)는 시장평균과 무관하게 🔴 집중신호로 승격 —
      대규모 급락 때 시장평균에 묻혀 노이즈로 버려지던 실입상마 방어
      (카나자와 10R 5-3-6: 3번 -17% 2착이 노이즈 판정으로 추천 누락된 3번째 동일 패턴).
    [근본해결2] 절대 단일급락폭(maxDrop<=-50) 병행 — 대규모 급락 시 개별 조합 -70%가 평균에
      희석돼(avg>-10·excess 양수) 누락되던 실입상마 방어(소노다 11R: 3+9 -70%·3+6 -68% → 3번).
      초과급락(상대) 또는 절대단일급락폭(절대) 둘 중 하나라도 걸리면 신호말로 채택."""
    ABS_STRONG = -10.0   # 절대 평균급락 임계(집중신호 승격 · 노이즈 기준 완화)
    ABS_BIG = -50.0      # [근본해결2] 절대 단일급락폭 임계 — 대규모 급락 평균에 안 묻히는 자금집중
    dd = [d for d in (drops or []) if d.get("pct") is not None and d["pct"] < 0]
    if not dd:
        return {"overall": None, "horses": {}, "concentrated": [], "count": 0}
    overall = round(sum(d["pct"] for d in dd) / len(dd), 1)
    by_horse = {}
    for d in dd:
        for h in d.get("combo", []):
            by_horse.setdefault(int(h), []).append(d["pct"])
    horses, concentrated = {}, []
    for h, pcts in by_horse.items():
        avg = round(sum(pcts) / len(pcts), 1)
        excess = round(avg - overall, 1)   # 음수 = 평균보다 더 급락(집중)
        mx = round(min(pcts), 1)           # [근본해결2] 절대 단일급락폭(가장 큰 급락 조합)
        grade = "🔴" if excess <= -5 else ("🟡" if excess < 0 else None)
        abs_strong = avg <= ABS_STRONG
        abs_big = mx <= ABS_BIG            # 단일 조합 절대급락 50%+ = 자금집중(평균 희석 무관)
        if abs_strong or abs_big:
            grade = "🔴"   # 절대(평균 or 단일) 급락 = 자금집중 확정 → 집중신호 승격(시장평균 무관)
        # 신호강도(%p, 음수) = 초과급락(상대)·절대평균급락·절대단일급락 중 가장 강한(음수 큰) 쪽
        cand = [excess]
        if abs_strong:
            cand.append(avg)
        if abs_big:
            cand.append(mx)
        strength = min(cand)
        horses[h] = {"avg": avg, "excess": excess, "grade": grade, "combos": len(pcts),
                     "absStrong": abs_strong, "absBig": abs_big, "maxDrop": mx, "strength": strength}
        if grade == "🔴":
            concentrated.append(h)
    concentrated.sort(key=lambda n: horses[n]["strength"])   # 가장 집중된 말 먼저(절대·상대 통합)
    # [근본해결2] 절대 단일급락폭만으로 잡힌 말(초과급락 노이즈였던 말) 별도 표기 → 배너·복기용
    abs_only = [h for h in concentrated if horses[h]["absBig"] and horses[h]["excess"] >= 0]
    return {"overall": overall, "horses": horses, "concentrated": concentrated,
            "count": len(dd), "absConcentrated": abs_only}


def _concentration_score(excess_val):
    """신호강도(음수 %p, 초과급락 또는 절대급락 중 강한 쪽)를 0~100 집중신호 점수로. -20%p 이상=100점."""
    if excess_val is None or excess_val >= 0:
        return 0.0
    return round(min(100.0, (-excess_val) / 20.0 * 100.0), 1)


def _signal_situation(drops, mass_drop, excess):
    """[3번] 상황별 가중치 자동 조정. 반환 {name, formW, signalW, signalSource, note}.
    - 일반: 전적50 신호50 / 이상감지다수(30%+급락 3개↑): 전적40 신호60
    - 대규모 급락: 전적30 집중신호70 / 대규모+집중(집중신호 말 존재): 전적20 집중신호80."""
    big = [d for d in (drops or []) if d.get("pct") is not None and d["pct"] <= -30]
    concentrated = (excess or {}).get("concentrated") or []
    if mass_drop and concentrated:
        return {"name": "대규모+집중", "formW": 0.2, "signalW": 0.8, "signalSource": "concentration",
                "note": "대규모 급락 + 집중 신호 → 집중신호 80% 가중(개별 배당 무시)"}
    if mass_drop:
        return {"name": "대규모 급락", "formW": 0.3, "signalW": 0.7, "signalSource": "concentration",
                "note": "대규모 급락 → 개별 신호 신뢰도 하향·집중신호 70% 가중"}
    if len(big) >= 3:
        return {"name": "이상감지 다수", "formW": 0.4, "signalW": 0.6, "signalSource": "odds",
                "note": "이상감지 다수(30%+ 급락 3건↑) → 신호 60% 가중"}
    return {"name": "일반", "formW": 0.5, "signalW": 0.5, "signalSource": "odds",
            "note": "일반 → 전적·신호 균형(50:50)"}


def _integrated_adaptive(form, curQ, curD, excess, situation):
    """[3번] 상황별 가중치로 통합 등급 재산출(기존 _integrated_grades 40/60은 그대로 두고 별도 추가).
    신호점수 = 대규모 상황이면 집중신호점수(초과급락), 아니면 배당점수(_odds_score)."""
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
    fw, sw = situation["formW"], situation["signalW"]
    use_conc = situation["signalSource"] == "concentration"
    ehorses = (excess or {}).get("horses") or {}
    out = []
    for h in form:
        no = h.get("no")
        fscore = max(0.0, min(100.0, float(h.get("totalScore") or 0)))
        if use_conc:
            sscore = _concentration_score((ehorses.get(int(no)) or {}).get("strength")) if no is not None else 0.0
        else:
            sscore = _odds_score(repr_odds.get(int(no)) if no is not None else None)
        integ = round(fw * fscore + sw * sscore, 1)
        out.append({"no": no, "name": h.get("name", ""), "jockey": h.get("jockey", ""),
                    "formScore": round(fscore, 1), "signalScore": sscore,
                    "odds": repr_odds.get(int(no)) if no is not None else None, "integrated": integ})
    out.sort(key=lambda x: -x["integrated"])
    n = len(out)
    for i, h in enumerate(out):
        frac = i / n if n else 0
        h["grade"] = "A" if frac < 0.25 else "B" if frac < 0.50 else "C" if frac < 0.75 else "D"
    return out


# ───────── [마감 후 신호] 발주(T-0) 이후 감지 신호 처리 + 케이스 학습 ─────────
AFTER_CLOSE_FILE = os.path.join(os.path.dirname(__file__), "data", "after_close_cases.json")


def _after_close_load():
    try:
        return json.load(open(AFTER_CLOSE_FILE, encoding="utf-8"))
    except Exception:
        return {"cases": []}


def _after_close_save(d):
    os.makedirs(os.path.dirname(AFTER_CLOSE_FILE), exist_ok=True)
    json.dump(d, open(AFTER_CLOSE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _record_after_close_case(rk, date, mb_signed, anomalies, surge=None):
    """[4번] 마감 후 신호로 베팅 반영 불가했던 케이스 저장. 경주별 1건(신호 최다 시점)으로 갱신.
    surge = {"combos":[{"combo":[a,b],"pct":-99}], "horses":[3,7]} = 마감 후 대급락(50%+) 구조."""
    d = _after_close_load()
    cases = d.setdefault("cases", [])
    mins_after = abs(mb_signed) if mb_signed is not None else None
    payload = {"raceKey": rk, "date": date, "minutes_after_close": mins_after,
               "signal_count": len(anomalies), "signals": list(anomalies)[:6], "t": time.time()}
    if surge and surge.get("horses"):
        # [마감후 대급락] 별도 필드로 보존 → 결과 입상 매칭·입상률 학습·다음경주 참고
        payload["surge_horses"] = surge.get("horses")
        payload["surge_combos"] = surge.get("combos")
        payload["surge_hit"] = None   # 결과 입력 시 _after_close_learn_result 가 채움
    existing = next((c for c in cases if c.get("raceKey") == rk and c.get("date") == date), None)
    if existing:
        # 신호 최다 시점으로 갱신하되, 이미 기록된 surge/결과판정은 보존
        if len(anomalies) >= (existing.get("signal_count") or 0):
            keep = {k: existing.get(k) for k in ("surge_horses", "surge_combos", "surge_hit") if existing.get(k) is not None}
            existing.update(payload)
            for k, v in keep.items():
                if payload.get(k) is None:
                    existing[k] = v
    else:
        cases.append(payload)
    d["cases"] = cases[-500:]
    _after_close_save(d)


def _after_close_learn_result(rk, date, result):
    """[3번] 마감 후 대급락말이 실제 입상(1~3착)했는지 판정 → surge_hit 갱신(입상률 학습)."""
    if not result:
        return
    placed = set()
    for k in ("1st", "2nd", "3rd"):
        v = result.get(k)
        if v is not None:
            try:
                placed.add(int(v))
            except (TypeError, ValueError):
                pass
    if not placed:
        return
    d = _after_close_load()
    changed = False
    for c in d.get("cases", []):
        if c.get("raceKey") != rk:
            continue
        sh = c.get("surge_horses")
        if not sh:
            continue
        hit = any(int(h) in placed for h in sh if h is not None)
        hit_horses = [int(h) for h in sh if h is not None and int(h) in placed]
        if c.get("surge_hit") != hit or c.get("surge_hit_horses") != hit_horses:
            c["surge_hit"] = hit
            c["surge_hit_horses"] = hit_horses
            c["result_placed"] = sorted(placed)
            changed = True
    if changed:
        _after_close_save(d)


def _after_close_stats():
    """[3번] 마감 후 대급락 → 실제 입상률 통계(신뢰도 측정)."""
    d = _after_close_load()
    judged = [c for c in d.get("cases", []) if c.get("surge_horses") and c.get("surge_hit") is not None]
    total = len(judged)
    hits = sum(1 for c in judged if c.get("surge_hit"))
    rate = round(hits / total * 100) if total else None
    recent = sorted(judged, key=lambda c: c.get("t") or 0, reverse=True)[:10]
    return {
        "total_judged": total, "hits": hits, "hit_rate": rate,
        "pending": sum(1 for c in d.get("cases", []) if c.get("surge_horses") and c.get("surge_hit") is None),
        "recent": [{"raceKey": c.get("raceKey"), "horses": c.get("surge_horses"),
                    "hit": c.get("surge_hit"), "hitHorses": c.get("surge_hit_horses"),
                    "combos": (c.get("surge_combos") or [])[:3]} for c in recent],
        "reliable": bool(total >= 5 and rate is not None and rate >= 50),
    }


# ───────── [4착 near-miss] 삼복승 아깝게 미적중(추천 말 4착) 학습 ─────────
NEAR_MISS_FILE = os.path.join(os.path.dirname(__file__), "data", "near_miss.json")


def _near_miss_load():
    try:
        return json.load(open(NEAR_MISS_FILE, encoding="utf-8"))
    except Exception:
        return {"cases": []}


def _near_miss_save(d):
    os.makedirs(os.path.dirname(NEAR_MISS_FILE), exist_ok=True)
    json.dump(d, open(NEAR_MISS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _record_near_miss(rk, date, no, name):
    """[2·3번] 추천 말이 4착(아깝게 미적중)한 케이스 저장. 경주별 1건."""
    d = _near_miss_load()
    cases = d.setdefault("cases", [])
    payload = {"raceKey": rk, "date": date, "no": no, "name": (name or "").strip(), "t": time.time()}
    existing = next((c for c in cases if c.get("raceKey") == rk and c.get("date") == date), None)
    if existing:
        existing.update(payload)
    else:
        cases.append(payload)
    d["cases"] = cases[-1000:]
    _near_miss_save(d)


def _near_miss_frequent(min_count=2):
    """4착 빈번(min_count회+) 말 이름 집합 → 다음 경주 삼복승 보험픽 우선 고려."""
    try:
        cnt = {}
        for c in _near_miss_load().get("cases", []):
            nm = (c.get("name") or "").strip()
            if nm:
                cnt[nm] = cnt.get(nm, 0) + 1
        return {nm for nm, n in cnt.items() if n >= min_count}
    except Exception:
        return set()


# ───────── [강한 신호 8유형 · 적중률 누적 학습] 유형별 발생/적중 → 가중치 상향 ─────────
# 경주별 1건(rk+date 키)으로 저장 → 결과 재입력 시 멱등(덮어쓰기). 유형별 입상(1~3착) 적중률 집계.
SIGNAL_TYPE_STATS_FILE = os.path.join(os.path.dirname(__file__), "data", "signal_type_stats.json")


def _signal_type_stats_load():
    try:
        return json.load(open(SIGNAL_TYPE_STATS_FILE, encoding="utf-8"))
    except Exception:
        return {"races": {}}


def _signal_type_stats_save(d):
    os.makedirs(os.path.dirname(SIGNAL_TYPE_STATS_FILE), exist_ok=True)
    json.dump(d, open(SIGNAL_TYPE_STATS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _learn_strong_signals(rk, date, an, top3):
    """[5번] 결과 → 강한 신호 8유형별 적중(신호말이 1~3착 입상) 누적. 경주별 멱등 저장."""
    try:
        ss = (an or {}).get("strongSignals") or {}
        sigs = ss.get("signals") or []
        if not sigs:
            return
        top3set = set(int(x) for x in (top3 or []) if x is not None)
        by_type = {}
        for s in sigs:
            t = s.get("type")
            if t is None:
                continue
            hs = set(int(h) for h in (s.get("horses") or []))
            hit = bool(hs & top3set) if hs else None   # 신호말 없는 유형(환수율 등)은 판정 불가(None → 분모 제외)
            cur = by_type.get(t)
            if cur is None:
                by_type[t] = {"horses": sorted(hs), "hit": hit}
            else:
                cur["horses"] = sorted(set(cur["horses"]) | hs)
                if hit is True or cur["hit"] is True:
                    cur["hit"] = True
                elif cur["hit"] is None:
                    cur["hit"] = hit
        d = _signal_type_stats_load()
        races = d.setdefault("races", {})
        rid = (date or "") + "|" + rk
        races[rid] = {"raceKey": rk, "date": date,
                      "types": {str(t): v for t, v in by_type.items()},
                      "recommendLevel": ss.get("recommendLevel"), "dual": bool(ss.get("dualConverge")),
                      "t": time.time()}
        if len(races) > 5000:   # 오래된 것부터 정리(백스톱)
            for k in sorted(races, key=lambda k: races[k].get("t", 0))[:len(races) - 5000]:
                races.pop(k, None)
        _signal_type_stats_save(d)
    except Exception as e:
        print("[강한신호학습] 실패:", e)


# ───────── [복기 학습 재설계] 통계 유의성 기준 — 1회 실패로 공식 수정 금지, 표본 확보 후에만 조정 ─────────
#   50회 미만 → 판단 보류(참고만) / 50회+ 적중률 40% 미만 → 경고(수정은 수동) / 100회+ → 소폭 자동조정 검토.
PATTERN_SAMPLE_MIN = 50     # 이 표본 미만은 '판단 보류'(신뢰 불가)
PATTERN_SAMPLE_SIG = 100    # 이 표본 이상 + 통계 유의 → 가중치 소폭 자동조정 검토(최대 5%p)
PATTERN_WARN_RATE = 40      # 표본 충족 + 적중률 이 값 미만 → 경고


def _pattern_status(fired, rate):
    """표본수·적중률 → 신뢰도 상태. 반환 (status, icon, note)."""
    if fired is None or fired < PATTERN_SAMPLE_MIN:
        need = PATTERN_SAMPLE_MIN - (fired or 0)
        return "표본부족", "⚠️", f"표본 부족 (판단 보류 · {PATTERN_SAMPLE_MIN}회 필요, {need}회 남음)"
    if rate is not None and rate < PATTERN_WARN_RATE:
        return "경고", "🔴", f"적중률 {rate}% (< {PATTERN_WARN_RATE}%) — 경고 · 수정은 수동으로"
    if fired >= PATTERN_SAMPLE_SIG:
        return "유의", "✅", f"통계 유의 ({fired}회) — 신뢰 · 소폭 자동조정 검토 가능"
    return "신뢰", "✅", f"신뢰 가능 ({fired}회 기준, 계속 관찰 중)"


def _pattern_confidence():
    """[2·4번] 신호 유형별 발생/적중/적중률 + 통계 신뢰도 상태(표본 50회 게이팅) 통합.
      강신호 8유형 + 저배당압축 + 고배당동반을 한 뷰로. ⚠ 공식 수정은 안 함 — 데이터 표시만."""
    out = []
    try:
        for t, v in sorted(_signal_type_hitrates().items()):
            st, icon, note = _pattern_status(v.get("fired"), v.get("rate"))
            out.append({"key": "signal_" + str(t), "label": v.get("label") or ("유형" + str(t)),
                        "fired": v.get("fired"), "hit": v.get("hit"), "rate": v.get("rate"),
                        "status": st, "icon": icon, "note": note})
    except Exception:
        pass
    try:
        cp = _compression_hitrate().get("all") or {}
        st, icon, note = _pattern_status(cp.get("fired"), cp.get("rate"))
        out.append({"key": "compression", "label": "저배당 압축(축 패턴)",
                    "fired": cp.get("fired"), "hit": cp.get("hit"), "rate": cp.get("rate"),
                    "status": st, "icon": icon, "note": note})
    except Exception:
        pass
    try:
        hc = _high_odds_companion_hitrate()
        st, icon, note = _pattern_status(hc.get("races"), hc.get("rate"))
        out.append({"key": "high_odds_companion", "label": "고배당 동반(1착시 3착내 고배당)",
                    "fired": hc.get("races"), "hit": hc.get("hits"), "rate": hc.get("rate"),
                    "status": st, "icon": icon, "note": note})
    except Exception:
        pass
    out.sort(key=lambda x: -((x.get("fired") or 0)))
    return {"patterns": out, "sampleMin": PATTERN_SAMPLE_MIN, "sampleSig": PATTERN_SAMPLE_SIG,
            "warnRate": PATTERN_WARN_RATE,
            "note": "표본 50회 미만은 참고만 · 공식 수정은 수동으로 (1회 실패로 기준 변경 금지)"}


def _signal_type_hitrates():
    """유형별 발생/적중/적중률 집계 → 가중치 상향 근거. 판정불가(None)는 분모 제외."""
    try:
        agg = {}
        for r in _signal_type_stats_load().get("races", {}).values():
            for t, v in (r.get("types") or {}).items():
                h = v.get("hit")
                if h is None:
                    continue
                a = agg.setdefault(int(t), {"fired": 0, "hit": 0})
                a["fired"] += 1
                if h:
                    a["hit"] += 1
        out = {}
        for t, a in agg.items():
            out[t] = {"type": t, "label": _STRONG_SIGNAL_META.get(t, ""),
                      "fired": a["fired"], "hit": a["hit"],
                      "rate": round(a["hit"] / a["fired"] * 100) if a["fired"] else None}
        return out
    except Exception:
        return {}


# ───────── [저배당 압축 패턴 4번] 압축 패턴 발생 시 실제 복승 적중률 누적 ─────────
COMPRESSION_STATS_FILE = os.path.join(os.path.dirname(__file__), "data", "compression_stats.json")


def _compression_stats_load():
    try:
        return json.load(open(COMPRESSION_STATS_FILE, encoding="utf-8"))
    except Exception:
        return {"races": {}}


def _compression_stats_save(d):
    os.makedirs(os.path.dirname(COMPRESSION_STATS_FILE), exist_ok=True)
    json.dump(d, open(COMPRESSION_STATS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _learn_compression(rk, date, an, top3):
    """[4번] 저배당 압축 패턴 발생 경주 → 압축 2두가 실제 복승 적중(둘 다 1~2착)했는지 누적. 경주별 멱등."""
    try:
        cp = (an or {}).get("compressionPattern") or {}
        if not cp.get("detected"):
            return
        top2 = set(int(x) for x in (top3 or [])[:2] if x is not None)
        combo = [int(x) for x in (cp.get("combo") or [])]
        # 복승 적중 = 압축 2두가 정확히 1·2착 / 부분 적중(1두 입상)도 집계
        hit = bool(combo and len(combo) == 2 and set(combo) == top2)
        partial = bool(combo and (set(combo) & set(int(x) for x in (top3 or [])[:3] if x is not None)))
        d = _compression_stats_load()
        races = d.setdefault("races", {})
        rid = (date or "") + "|" + rk
        races[rid] = {"raceKey": rk, "date": date, "level": cp.get("level"),
                      "combo": combo, "hit": hit, "partial": partial,
                      "withDrop": bool(cp.get("withDrop")), "withReversal": bool(cp.get("withReversal")),
                      "t": time.time()}
        if len(races) > 5000:
            for k in sorted(races, key=lambda k: races[k].get("t", 0))[:len(races) - 5000]:
                races.pop(k, None)
        _compression_stats_save(d)
    except Exception as e:
        print("[압축패턴학습] 실패:", e)


def _compression_hitrate():
    """압축 패턴 복승 적중률(강력/중간·급락결합 별) 집계."""
    try:
        races = list(_compression_stats_load().get("races", {}).values())
        def agg(rows):
            n = len(rows)
            h = sum(1 for r in rows if r.get("hit"))
            p = sum(1 for r in rows if r.get("partial"))
            return {"fired": n, "hit": h, "partial": p,
                    "rate": round(h / n * 100) if n else None,
                    "partialRate": round(p / n * 100) if n else None}
        return {
            "all": agg(races),
            "strong": agg([r for r in races if r.get("level") == "강력"]),
            "mid": agg([r for r in races if r.get("level") == "중간"]),
            "withDrop": agg([r for r in races if r.get("withDrop")]),
        }
    except Exception:
        return {}


# ───────── [고배당 동반 패턴 4번] 고배당 1착 경주 중 3착 내 다른 고배당 포함 비율 누적 ─────────
HIGH_ODDS_COMPANION_FILE = os.path.join(os.path.dirname(__file__), "data", "high_odds_companion_stats.json")
HIGH_ODDS_COMPANION_THRESH = 7.0   # 고배당 기준(대표배당 7배+)


def _high_odds_companion_stats_load():
    try:
        return json.load(open(HIGH_ODDS_COMPANION_FILE, encoding="utf-8"))
    except Exception:
        return {"races": {}}


def _high_odds_companion_stats_save(d):
    os.makedirs(os.path.dirname(HIGH_ODDS_COMPANION_FILE), exist_ok=True)
    json.dump(d, open(HIGH_ODDS_COMPANION_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _learn_high_odds_companion(rk, date, top3, rec, high_thresh=HIGH_ODDS_COMPANION_THRESH):
    """[4번] 1착이 고배당(7배+)인 경주만 분모로, 2·3착에 다른 고배당(7배+)이 있었는지 누적. 경주별 멱등.
      → '고배당 1착 시 3착 내 고배당 포함 비율' 산출(참고 추천 신뢰도 근거)."""
    try:
        top3i = [int(x) for x in (top3 or [])[:3] if x is not None]
        if not top3i:
            return
        repr_map, _, _ = _repr_odds_from_rec(rec or {})
        first = top3i[0]
        fo = repr_map.get(first)
        if not fo or fo < high_thresh:
            return                      # 고배당 1착 경주만 분모(저배당 1착은 대상 아님)
        others = top3i[1:3]
        comp = [h for h in others if (repr_map.get(h) or 0) >= high_thresh]
        d = _high_odds_companion_stats_load()
        races = d.setdefault("races", {})
        rid = (date or "") + "|" + rk
        races[rid] = {"raceKey": rk, "date": date, "firstOdds": round(float(fo), 1),
                      "companion": bool(comp), "companions": comp, "t": time.time()}
        if len(races) > 5000:
            for k in sorted(races, key=lambda k: races[k].get("t", 0))[:len(races) - 5000]:
                races.pop(k, None)
        _high_odds_companion_stats_save(d)
    except Exception as e:
        print("[고배당동반학습] 실패:", e)


def _high_odds_companion_hitrate():
    """고배당 1착 경주 중 3착 내 다른 고배당 포함 비율. 반환 {races,hits,rate,reliable}."""
    try:
        races = list(_high_odds_companion_stats_load().get("races", {}).values())
        n = len(races)
        h = sum(1 for r in races if r.get("companion"))
        return {"races": n, "hits": h, "rate": (round(h / n * 100) if n else None),
                "reliable": n >= 10}
    except Exception:
        return {"races": 0, "hits": 0, "rate": None, "reliable": False}


# ───────── [배당 3착 자동 발굴 5번] 고배당 3착 후보 신호 유형별 3착/입상 적중률 누적 ─────────
THIRD_PLACE_STATS_FILE = os.path.join(os.path.dirname(__file__), "data", "third_place_stats.json")


def _third_place_stats_load():
    try:
        return json.load(open(THIRD_PLACE_STATS_FILE, encoding="utf-8"))
    except Exception:
        return {"races": {}}


def _third_place_stats_save(d):
    os.makedirs(os.path.dirname(THIRD_PLACE_STATS_FILE), exist_ok=True)
    json.dump(d, open(THIRD_PLACE_STATS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _tp_reason_bucket(reason):
    """근거 문자열 → 신호 유형 버킷(급락/역배열/전적/연속하락/이상감지)."""
    r = reason or ""
    if "급락" in r:
        return "급락"
    if "역배열" in r:
        return "역배열"
    if "전적" in r:
        return "전적우수"
    if "연속하락" in r:
        return "연속하락"
    return "이상감지"


def _learn_third_place(rk, date, an, top3):
    """[5번] 3착 발굴 후보가 실제 3착/입상했는지 신호 유형별 누적. 경주별 멱등."""
    try:
        tp = (an or {}).get("thirdPlaceHunt") or {}
        cands = tp.get("candidates") or []
        if not cands:
            return
        top3list = [int(x) for x in (top3 or [])[:3] if x is not None]
        top3set = set(top3list)
        third = top3list[2] if len(top3list) >= 3 else None
        recs = []
        for c in cands:
            no = int(c.get("no"))
            recs.append({"no": no, "bucket": _tp_reason_bucket(c.get("reason")),
                         "place": (no in top3set), "exact3rd": (third is not None and no == third)})
        d = _third_place_stats_load()
        races = d.setdefault("races", {})
        rid = (date or "") + "|" + rk
        races[rid] = {"raceKey": rk, "date": date, "cands": recs, "t": time.time()}
        if len(races) > 5000:
            for k in sorted(races, key=lambda k: races[k].get("t", 0))[:len(races) - 5000]:
                races.pop(k, None)
        _third_place_stats_save(d)
    except Exception as e:
        print("[3착발굴학습] 실패:", e)


def _third_place_hitrate():
    """신호 유형별 3착 후보의 입상(1~3착)·정확3착 적중률 집계."""
    try:
        agg = {}
        for r in _third_place_stats_load().get("races", {}).values():
            for c in (r.get("cands") or []):
                b = c.get("bucket") or "기타"
                a = agg.setdefault(b, {"fired": 0, "place": 0, "exact3rd": 0})
                a["fired"] += 1
                if c.get("place"):
                    a["place"] += 1
                if c.get("exact3rd"):
                    a["exact3rd"] += 1
        out = {}
        for b, a in agg.items():
            out[b] = {"bucket": b, "fired": a["fired"], "place": a["place"], "exact3rd": a["exact3rd"],
                      "placeRate": round(a["place"] / a["fired"] * 100) if a["fired"] else None,
                      "exact3rdRate": round(a["exact3rd"] / a["fired"] * 100) if a["fired"] else None}
        return out
    except Exception:
        return {}


# ───────── [집중신호 오판 학습] 초과급락 신호말이 입상했는데 노이즈 판정으로 추천 누락된 사례 ─────────
SIGNAL_LESSON_FILE = os.path.join(os.path.dirname(__file__), "data", "signal_lessons.json")


def _signal_lesson_load():
    try:
        return json.load(open(SIGNAL_LESSON_FILE, encoding="utf-8"))
    except Exception:
        return {"cases": []}


def _signal_lesson_save(d):
    os.makedirs(os.path.dirname(SIGNAL_LESSON_FILE), exist_ok=True)
    json.dump(d, open(SIGNAL_LESSON_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _record_signal_lesson(case):
    """초과급락 신호 → 실제 입상인데 노이즈 판정으로 추천 미반영된 사례 축적(경주별 1건)."""
    d = _signal_lesson_load()
    cases = d.setdefault("cases", [])
    case = dict(case)
    case.setdefault("t", time.time())
    rk, dt = case.get("race"), case.get("date")
    existing = next((c for c in cases if c.get("race") == rk and c.get("date") == dt), None) if rk else None
    if existing:
        existing.update(case)
    else:
        cases.append(case)
    d["cases"] = cases[-1000:]
    _signal_lesson_save(d)
    return d


def _deadline_phase_label(mb, after_close):
    """이상감지 신호의 유효 시점 라벨. 마감 후=회색 표시용."""
    if after_close:
        return "마감 후"
    if mb is None:
        return None
    if mb <= 0:
        return "마감 직전"
    return f"마감 {mb}분전"


def _signal_combo_bets(signal_horses, curQ, bet_rec, cap=5):
    """[신호 조합] 이상감지 신호가 있는 말들의 모든 복승 조합을 추천에 추가(고배당 포함).
    이미 추천된 복승 조합은 스킵. 배당 높은 순 우선 노출(고배당 놓침 방지). 반환: 추가 alloc 총합.
    예: 신호말 2·6·7 → 2+6(147배 고배당신호)·2+7·6+7 전부 추천 목록에 포함."""
    existing = {tuple(sorted(int(x) for x in b["combo"])) for b in bet_rec if b.get("kind") == "복승"}
    sh = []
    for h in signal_horses:
        if h is not None and int(h) not in sh:
            sh.append(int(h))
    combos = []
    for i in range(len(sh)):
        for j in range(i + 1, len(sh)):
            pair = tuple(sorted((sh[i], sh[j])))
            if pair in existing:
                continue
            o = curQ.get(pair)
            if o is None or o <= 0:   # 배당 미수집 조합은 제외(신호 조합은 복승 배당 있는 것만)
                continue
            combos.append((pair, o))
            existing.add(pair)
    combos.sort(key=lambda x: -(x[1] or 0))   # 고배당 우선 노출
    added = 0.0
    for pair, odds in combos[:cap]:
        if odds >= 50:
            tier = "고배당신호"
        elif odds < 7:
            tier = "낮은배당신호"
        else:
            tier = "신호기반"
        bet_rec.append({"kind": "복승", "label": "복승 신호", "combo": list(pair),
                        "alloc": 3, "expOdds": odds, "signalTier": tier, "signalCombo": True})
        added += 3
    return added


def _combo_signal_quality(combo, excess):
    """[4번] 추천 조합의 신호 품질(상/중/하) + 근거. 조합 내 최대 초과급락 말 기준."""
    ehorses = (excess or {}).get("horses") or {}
    best = None
    for h in (combo or []):
        e = ehorses.get(int(h))
        s = (e or {}).get("strength")
        if s is None:
            s = (e or {}).get("excess")
        if e and s is not None and s < 0:
            if best is None or s < best[1]:
                best = (int(h), e.get("excess"), e.get("grade"), e.get("absStrong"))
    if best is None:
        return {"quality": "하", "reason": "집중 급락 없음(시장 전체 급락·노이즈)"}
    no, ex, grade, abs_strong = best
    reason = f"{no}번 초과급락 {ex}%p" + ("·절대 10%+ 집중" if abs_strong else "")
    return {"quality": ("상" if grade == "🔴" else "중"), "reason": reason}


# ───────── [핵심 이상감지 수학 공식] 쌍승역전·복승불일치·종합신뢰도 ─────────
def _reversal_level(ratio):
    """[강화] 역전비율(<1)→등급. 배당 차이(=1-ratio) 기준으로 오탐 제거 + 4단계 강도.
      배당 차이 10% 미만(ratio>0.90) → 역배열 아님(None) · 10~20% 🟡 약한 · 20~35% 🟠 ·
      35~50% 🔴 강한 · 50%+ 🔴🔴 압도적. (0.1배 수준 미세차 오탐 제거)."""
    if ratio is None or ratio > 0.90:      # 배당 차이 10% 미만 → 역배열 아님(오탐 제거)
        return None, None
    if ratio > 0.80:                        # 배당 차이 10~20%
        return "🟡", "약한 역배열"
    if ratio > 0.65:                        # 배당 차이 20~35%
        return "🟠", "역배열"
    if ratio > 0.50:                        # 배당 차이 35~50%
        return "🔴", "강한 역배열"
    return "🔴🔴", "압도적 역배열"           # 배당 차이 50%+


def _inversion_tier(diff_pct):
    """[역배열 강도] 배당 차이 %(양수) → 등급. 10% 미만 신호없음(None).
      10~20% 🟡 약한 / 20~35% 🟠 / 35~50% 🔴 강한 / 50%+ 🔴🔴 압도적."""
    if diff_pct is None or diff_pct < 10:
        return None, None
    if diff_pct < 20:
        return "🟡", "약한 역배열"
    if diff_pct < 35:
        return "🟠", "역배열"
    if diff_pct < 50:
        return "🔴", "강한 역배열"
    return "🔴🔴", "압도적 역배열"


def _reversal_role(diff_pct, confirmed):
    """[역배열 강도별 축 역할·사용자 요청] 배당 차이%(양수) + 지속성(2회+ 확정) → 역할.
      35%+ → 축(복승 메인) / 20~35% → 보조(복승 보조) / 10~20% → 보험(삼복승) / 10% 미만 → 무시(None).
      [지속성] 1회만 감지된 축(35%+)은 '후보'로 보조 강등(마감 전 2회+ 연속만 확정 축)."""
    if diff_pct is None or diff_pct < 10:
        return None
    if diff_pct >= 35:
        return "축" if confirmed else "보조"     # 1회만이면 후보 → 보조 강등
    if diff_pct >= 20:
        return "보조"
    return "보험"                                # 10~20%


def _exacta_map_any(x):
    """exacta(리스트 [{combo,odds}] 또는 딕셔너리 {'1>2':배당·'1-2'·'1→2'}) → {(선,후):배당} 방향 맵."""
    if isinstance(x, dict):
        m = {}
        for k, v in x.items():
            try:
                parts = [int(y) for y in re.split(r"[>\-→]", str(k)) if y.strip().lstrip("-").isdigit()]
                o = float(v)
            except (ValueError, TypeError):
                continue
            if len(parts) == 2 and o > 0 and ((parts[0], parts[1]) not in m or o < m[(parts[0], parts[1])]):
                m[(parts[0], parts[1])] = o
        return m
    return _odds_map_dir(x)


def _reversal_persistence(hist, fav_rank, lookback=5):
    """[역배열 지속성] 최근 스냅샷들에서 각 challenger가 역배열(ratio<0.90)로 몇 회 감지됐는지 집계.
      현재 fav_rank 기준으로 과거 exacta에 역전 재계산(근사). 반환 {challenger_no: 감지횟수}."""
    counts = {}
    for h in (hist or [])[-lookback:]:
        exm = _exacta_map_any(h.get("exacta"))
        if not exm:
            continue
        for r in _win_exacta_reversal(fav_rank, exm):
            ch = r.get("challenger")
            if ch is not None:
                counts[int(ch)] = counts.get(int(ch), 0) + 1
    return counts


def _reversal_axis_roles(wx_reversals, hist, fav_rank, curWin, curQ):
    """[역배열 강도별 처리] challenger별 역할(축/보조/보험) + 지속성(확정/후보) 산출.
      반환 [{no, diffPct, ratio, role, persistence, confirmed, reprOdds}] (강도 높은 순)."""
    if not wx_reversals:
        return []
    persist = _reversal_persistence(hist, fav_rank)

    def _rep(h):
        if curWin.get(h):
            return curWin[h]
        best = None
        for (a, b), o in (curQ or {}).items():
            if h in (a, b) and o > 0 and (best is None or o < best):
                best = o
        return best

    seen, out = set(), []
    for r in wx_reversals:
        ch = r.get("challenger")
        if ch is None or int(ch) in seen:
            continue
        ch = int(ch)
        seen.add(ch)
        ratio = r.get("ratio") or 1.0
        diff_pct = round((1 - ratio) * 100, 1)
        cnt = max(persist.get(ch, 0), 1)          # 현재 감지분 최소 1 보장(hist에 현재 스냅샷 미포함 대비)
        confirmed = cnt >= 2                        # [2번] 마감 전 2회+ 연속 = 확정 축
        role = _reversal_role(diff_pct, confirmed)
        if role is None:
            continue
        out.append({"no": ch, "diffPct": diff_pct, "ratio": ratio, "role": role,
                    "persistence": cnt, "confirmed": confirmed, "reprOdds": _rep(ch)})
    out.sort(key=lambda x: -x["diffPct"])
    return out


def _win_exacta_reversal(fav_rank, curD, max_rank=4):
    """[1번] 쌍승 역전 감지 공식. 단승 유력마 A vs 다른 말 B 방향 비교.
      역전비율 = 쌍승(B→A) / 쌍승(A→B).  A가 유력한데 B→A가 더 싸면(비율<1) 시장은 B를 실질 1착으로 봄.
        <0.95 🟡 역전 / <0.80 🔴 강한역전 / <0.60 🔴🔴 압도적역전.
      (단승 미수집(일본)이면 복승인기 순위를 유력마 순위로 대체.)
      [다중순위 확장] 1위(primary) 기준 역전을 먼저(강한 순) 반환하고,
        상위권(최대 max_rank위) 다른 순위쌍(2·3·4위 간) 역전을 뒤에 덧붙인다(multiRank=True).
        하위 순위쌍은 노이즈가 크므로 강한 역전(<0.80)만 채택. primary가 항상 앞이라
        wx[0]·[:3]·[:5] 을 쓰는 기존 소비처 동작은 그대로 유지된다."""
    out = []
    if not fav_rank or not curD:
        return out

    def _mk(a, b, ai, bi, multi):
        ab, ba = curD.get((a, b)), curD.get((b, a))
        if not ab or not ba or ab <= 0:
            return None
        ratio = round(ba / ab, 3)      # <1 = B→A(=B 1착)가 더 쌈 = 역전
        if multi and ratio >= 0.80:    # 다중순위(하위)는 강한 역전만 — 노이즈 억제
            return None
        lvl, tag = _reversal_level(ratio)
        if lvl is None:
            return None
        base = {"favorite": a, "challenger": b, "ratio": ratio, "level": lvl, "tag": tag,
                "favoredExacta": ab, "reverseExacta": ba,
                "favRank": ai + 1, "chalRank": bi + 1, "multiRank": multi}
        if multi:
            base["text"] = (f"🔄 역전감지[{ai + 1}·{bi + 1}위 간]: 단승 {a}번({ai + 1}위) vs {b}번({bi + 1}위) — "
                            f"쌍승 {b}→{a}({ba})가 {a}→{b}({ab})보다 낮음 → 상위권 실질순위 역전: {b}번 우세 ({tag} {ratio})")
        else:                          # 기존 primary 문구 그대로 보존
            base["text"] = (f"🔄 역전감지: 단승 {a}번 유력이나 쌍승에서 {b}번 1착({ba})이 "
                            f"{a}번 1착({ab})보다 낮음 → 실질 1착: {b}번 가능성 ({tag} {ratio})")
        return base

    # primary: 최유력마(1위) vs 나머지 — 기존 동작(임계 0.95)
    primary = []
    for bi, b in enumerate(fav_rank[1:], start=1):
        it = _mk(fav_rank[0], b, 0, bi, False)
        if it:
            primary.append(it)
    primary.sort(key=lambda r: r["ratio"])

    # multiRank: 상위권 다른 순위쌍(ai>=1, ai<bi) 역전 — 강한 역전(<0.80)만
    multi = []
    top = fav_rank[:max_rank]
    for ai in range(1, len(top)):
        for bi in range(ai + 1, len(top)):
            it = _mk(top[ai], top[bi], ai, bi, True)
            if it:
                multi.append(it)
    multi.sort(key=lambda r: r["ratio"])

    return primary + multi


def _quinella_mismatch(fav_rank, curQ):
    """[2번] 복승 불일치 감지 공식. 단승 1+2위 예상 조합 vs 실제 최저복승.
      불일치점수 = 예상최저복승 / 실제최저복승.  >1 = 예상 밖 조합에 자금집중.
        1.2+ 🟡 주의 / 1.5+ 🔴 강한신호 / 2.0+ 🔴🔴 압도적신호."""
    if not fav_rank or len(fav_rank) < 2 or not curQ:
        return None
    exp_pair = tuple(sorted((fav_rank[0], fav_rank[1])))
    exp_odds = curQ.get(exp_pair)
    if not exp_odds or exp_odds <= 0:
        return None
    act_pair, act_odds = min(curQ.items(), key=lambda kv: kv[1])
    if not act_odds or act_odds <= 0:
        return None
    ratio = round(exp_odds / act_odds, 3)
    if ratio < 1.2:
        return None                     # 예상=실제 근접 → 신호 없음
    lvl = "🔴🔴" if ratio >= 2.0 else ("🔴" if ratio >= 1.5 else "🟡")
    exp_set = set(exp_pair)
    focus = [h for h in act_pair if h not in exp_set]   # 집중 자금 유입 말
    return {"expected": list(exp_pair), "actual": list(act_pair),
            "expectedOdds": exp_odds, "actualOdds": act_odds, "ratio": ratio,
            "level": lvl, "focusHorses": focus,
            "text": f"⚠️ 복승 불일치: 단승 기준 예상 {exp_pair[0]}+{exp_pair[1]}({exp_odds}) / "
                    f"실제 최저 {act_pair[0]}+{act_pair[1]}({act_odds}) → "
                    f"{('·'.join(map(str, focus)) or '동일')}번 집중 자금 유입 ({lvl} {ratio})"}


def _reversal_strength_score(ratio):
    """역전비율(<1)을 0~100 점수(0.50이하=100, 0.90=0 선형). [강화] 10% 미만 차(ratio>0.90)=0점."""
    if ratio is None or ratio >= 0.90:
        return 0.0
    return round(min(100.0, (0.90 - ratio) / (0.90 - 0.50) * 100.0), 1)


def _mismatch_strength_score(ratio):
    """불일치점수(>1)를 0~100 점수(2.0이상=100, 1.2=0 선형)."""
    if ratio is None or ratio <= 1.2:
        return 0.0
    return round(min(100.0, (ratio - 1.2) / (2.0 - 1.2) * 100.0), 1)


def _signal_confidence(excess, wx_reversals, mismatch):
    """[4번] 종합 신뢰도 점수 = 초과급락 40% + 쌍승역전 35% + 복승불일치 25% (말별 0~100).
      70+ → 🔴 강력 신호 / 40~69 → 🟡 참고 신호 / 40 미만 → 노이즈.
      반환 {horses:{no:{excessScore,reversalScore,mismatchScore,confidence,grade}}, strong:[no], refer:[no]}."""
    horses = {}
    ehorses = (excess or {}).get("horses") or {}
    for no, e in ehorses.items():
        horses.setdefault(int(no), {})["excessScore"] = _concentration_score(e.get("strength"))
    for r in (wx_reversals or []):       # 실질 1착으로 지목된 challenger(B)에 점수
        b = int(r["challenger"])
        d = horses.setdefault(b, {})
        d["reversalScore"] = max(d.get("reversalScore", 0.0), _reversal_strength_score(r["ratio"]))
    if mismatch:                          # 집중 자금 유입 말(focus)에 점수
        sc = _mismatch_strength_score(mismatch["ratio"])
        for h in mismatch.get("focusHorses", []):
            d = horses.setdefault(int(h), {})
            d["mismatchScore"] = max(d.get("mismatchScore", 0.0), sc)
    out = {}
    for no, d in horses.items():
        ex, rv, mm = d.get("excessScore", 0.0), d.get("reversalScore", 0.0), d.get("mismatchScore", 0.0)
        conf = round(0.40 * ex + 0.35 * rv + 0.25 * mm, 1)
        out[no] = {"excessScore": ex, "reversalScore": rv, "mismatchScore": mm,
                   "confidence": conf, "grade": ("🔴" if conf >= 70 else ("🟡" if conf >= 40 else None))}
    strong = sorted([no for no, v in out.items() if v["confidence"] >= 70], key=lambda n: -out[n]["confidence"])
    refer = sorted([no for no, v in out.items() if 40 <= v["confidence"] < 70], key=lambda n: -out[n]["confidence"])
    return {"horses": out, "strong": strong, "refer": refer}


def _rank_inversion_detail(curWin, curQ, curD):
    """[역배열 정확화] 인기순위(단승 우선·없으면 복승) vs 쌍승 배당순위(쌍승 우선·없으면 복승)를
      비교해 **인기순위보다 쌍승 배당순위가 2단계 이상 높은 말**만 역배열로 판정(단순 저배당 표시 금지).
      반환 (detail[{no,popRank,oddsRank,gap,odds,lowest}], lead{...,vs}, source{popSrc,oddsSrc}).
      두 순위 산출원이 같으면(단승·쌍승 없이 복승뿐 → 비교 불가) ([], None, None)."""
    def _best_per_horse(m):
        best = {}
        for k, o in (m or {}).items():
            if not o or o <= 0:
                continue
            for h in k:
                h = int(h)
                if h not in best or o < best[h]:
                    best[h] = o
        return best

    def _best_first_place(m):
        """쌍승(방향성) 전용: 각 말이 **1착**일 때의 최저 배당(=시장이 그 말을 승자로 미는 정도).
          curD 키는 (1착, 2착) → k[0]==말 인 조합만 채택. 방향 무관 최저를 쓰면
          '4→3'(3번이 4번 뒤 2착)을 '3번 저배당=유력'으로 오독하는 버그가 생김(1착 방향만 사용)."""
        best = {}
        for k, o in (m or {}).items():
            if not o or o <= 0 or len(k) < 2:
                continue
            h = int(k[0])                     # k=(1착,2착) → 1착만
            if h not in best or o < best[h]:
                best[h] = o
        return best

    # 인기순위 산출원: 단승 우선(없으면 복승)
    if curWin:
        pop_src, pop_name = {int(n): v for n, v in curWin.items() if v and v > 0}, "단승"
    else:
        pop_src, pop_name = _best_per_horse(curQ), "복승"
    # 쌍승 배당순위 산출원: 쌍승 우선(1착 방향만·없으면 복승 — 단, 인기도 복승이면 비교 불가)
    if curD:
        odds_src, odds_name = _best_first_place(curD), "쌍승"
    elif pop_name != "복승":
        odds_src, odds_name = _best_per_horse(curQ), "복승"
    else:
        return [], None, None
    if not pop_src or not odds_src:
        return [], None, None

    pop_rank = {n: i + 1 for i, n in enumerate(sorted(pop_src, key=lambda x: pop_src[x]))}
    odds_rank = {n: i + 1 for i, n in enumerate(sorted(odds_src, key=lambda x: odds_src[x]))}
    detail = []
    for n in odds_src:
        pr, orr = pop_rank.get(n), odds_rank.get(n)
        if pr is None or orr is None:
            continue
        gap = pr - orr                       # 인기순위 - 배당순위 (양수=배당순위가 더 높음=역배열)
        if not (gap >= 2 and odds_src[n] < 30):
            continue
        # [역배열 강화] 순위 역전 + '배당 차이 10%+' 요구(0.1배 미세차 오탐 제거).
        #   차이 = n(인기 낮은데 배당 싼 말)이, 인기는 높은데 배당은 비싼 말보다 얼마나 싼지(%).
        ref = None
        for m in pop_src:
            if pop_rank.get(m, 999) < pr and odds_src.get(m, 0) > odds_src[n]:
                if ref is None or odds_src[m] < ref:
                    ref = odds_src[m]
        diff_pct = round((ref - odds_src[n]) / ref * 100, 1) if (ref and ref > 0) else None
        lvl, tag = _inversion_tier(diff_pct)
        if lvl is None:                      # 배당 차이 10% 미만 → 역배열 아님
            continue
        detail.append({"no": n, "popRank": pr, "oddsRank": orr, "gap": gap,
                       "odds": round(odds_src[n], 1), "diffPct": diff_pct,
                       "level": lvl, "tag": tag})
    # 강도(배당 차이) 큰 순 → 역전폭 → 배당 낮은 순
    detail.sort(key=lambda x: (-(x.get("diffPct") or 0), -x["gap"], x["odds"]))
    for i, d in enumerate(detail):
        d["lowest"] = (i == 0)
    lead = None
    if detail:
        L = detail[0]
        vs = None                            # 대조마: 인기 더 높은데(순위 작음) 배당은 더 비싼 말
        for n in sorted(pop_src, key=lambda x: pop_rank[x]):
            if pop_rank[n] < L["popRank"] and odds_src.get(n, 0) > L["odds"]:
                vs = {"no": n, "popRank": pop_rank[n], "odds": round(odds_src[n], 1)}
                break
        lead = dict(L, vs=vs)
    return detail, lead, {"popSrc": pop_name, "oddsSrc": odds_name}


def _inverse_arrangement(fav_rank, has_win, curWin, curQ, wx_reversals, quin_mismatch, excess, form=None):
    """[역배열 감지] 진짜 역배열 = 시장 인기 순위(배당 낮은 순) ≠ 쌍승 배당 순위가 역전될 때만.
    ⚠️ [기준 수정] 역배열(detected)은 **쌍승역전(wx_reversals)** 이 있을 때만 True.
       (예: 단승 1위 2번인데 쌍승에서 4→2가 2→4보다 낮으면 → 4번이 실질 1착 = 진짜 역배열.)
       전적이 좋아도 배당이 높기만 한 경우는 역배열이 아니라 '전적 우수하나 시장 비인기'로 별도 분류.
    보조 유형(복승불일치·배당압축·초과급락)은 참고용으로 types 에 계속 담되 역배열 판정 트리거로 쓰지 않음(삭제 아님).
    반환 {detected, types, invHorses, invCombos, banner, invDetail, invLead,
          strongUnpopular:[{no,formScore,reprOdds,popRank}]}."""
    types, inv_horses = [], []

    def _add_inv(h):
        if h is not None and int(h) not in inv_horses:
            inv_horses.append(int(h))

    ref_no = fav_rank[0] if fav_rank else None                 # 단승(없으면 복승인기) 1위
    ref_odds = curWin.get(ref_no) if (has_win and ref_no is not None) else None
    fav_pair, fav_odds = (min(curQ.items(), key=lambda kv: kv[1]) if curQ else (None, None))

    # 유형1 - 쌍승 역전
    for r in (wx_reversals or [])[:3]:
        types.append({"kind": "쌍승역전", "level": r["level"],
                      "text": f"🔄 쌍승역전: 단승 {r['favorite']}번 유력이나 쌍승에서 {r['challenger']}번 1착이 더 낮음",
                      "detail": f"{r['challenger']}→{r['favorite']}({r['reverseExacta']}) < {r['favorite']}→{r['challenger']}({r['favoredExacta']}) → 비정상 (비율 {r['ratio']})",
                      "horses": [r["challenger"]]})
        _add_inv(r["challenger"])
    # 유형2 - 복승 불일치
    if quin_mismatch:
        foc = quin_mismatch.get("focusHorses") or []
        ep, ap = quin_mismatch["expected"], quin_mismatch["actual"]
        types.append({"kind": "복승불일치", "level": quin_mismatch["level"],
                      "text": f"⚠️ 복승불일치: 단승 기준 {ep[0]}+{ep[1]} 예상, 실제 최저는 {ap[0]}+{ap[1]} → {('·'.join(map(str, foc)) or '동일')}번 주목",
                      "detail": f"불일치점수 {quin_mismatch['ratio']} (예상 {quin_mismatch['expectedOdds']} / 실제 {quin_mismatch['actualOdds']})",
                      "horses": foc})
        for h in foc:
            _add_inv(h)
    # 유형3 - 배당 압축(상위 복승 근접)
    tops = sorted(curQ.values())[:4] if curQ else []
    if len(tops) >= 3 and tops[0] > 0 and tops[-1] / tops[0] < 1.3:
        comp_horses = []
        for k, _o in sorted(curQ.items(), key=lambda kv: kv[1])[:4]:
            for h in k:
                if h not in comp_horses:
                    comp_horses.append(h)
        types.append({"kind": "배당압축", "level": "🟡",
                      "text": f"📊 배당압축: 상위 말들 배당 비정상적으로 근접 ({tops[0]}~{tops[-1]})",
                      "detail": "자금 분산 → 특정 유력마 불명확(이변 가능성↑)", "horses": comp_horses})
    # 유형4 - 초과급락(집중)
    for h in (excess.get("concentrated") or [])[:3]:
        e = excess["horses"][h]
        amt = "절대 10%+" if e.get("absStrong") else f"{abs(e['excess'])}%p"
        types.append({"kind": "초과급락", "level": "🔴",
                      "text": f"🔴 집중급락: {h}번 전체 평균보다 {amt} 더 급락 → 실질 유력",
                      "detail": f"평균급락 {e['avg']}% (시장평균 {excess.get('overall')}% 대비)", "horses": [h]})
        _add_inv(h)

    # [기준 수정] 역배열 판정 = 쌍승역전(시장 인기순위 ↔ 쌍승 배당순위 역전)이 있을 때만.
    #   기존의 "유형 존재/단승1위가 복승최저에 빠짐"만으로 True 하던 조건 제거
    #   → 전적 좋고 배당만 높은 말이 역배열로 오탐되던 문제 해결.
    fav_normal = (ref_no is not None and fav_pair is not None and ref_no in fav_pair)
    has_reversal = any(t.get("kind") == "쌍승역전" for t in types)
    detected = bool(has_reversal)

    # [신규 분류] 전적 우수하나 시장 비인기: 전적 상위(총점 60+)인데 배당은 비인기(대표 복승배당 높음/인기 하위).
    #   역배열(쌍승역전) 대상 말은 제외 — 그건 별도 역배열 신호이므로 중복 표시 방지.
    strong_unpopular = []
    try:
        form_map = {}
        for f in (form or []):
            _no, _ts = f.get("no"), f.get("totalScore")
            if _no is not None and isinstance(_ts, (int, float)):
                form_map[int(_no)] = float(_ts)
        if form_map and curQ:
            repr_odds = {}
            for k, o in curQ.items():
                if not o or o <= 0:
                    continue
                for h in k:
                    if h not in repr_odds or o < repr_odds[h]:
                        repr_odds[h] = o
            pop_sorted = [h for h, _ in sorted(repr_odds.items(), key=lambda kv: kv[1])]
            pop_rank = {h: i + 1 for i, h in enumerate(pop_sorted)}
            n_pop = len(pop_sorted)
            rev_horses = {int(r["challenger"]) for r in (wx_reversals or [])} | \
                         {int(r["favorite"]) for r in (wx_reversals or [])}
            form_sorted = [no for no, _ in sorted(form_map.items(), key=lambda kv: -kv[1])]
            for no in form_sorted[:3]:                       # 전적 상위 3두만 검사
                ts = form_map[no]
                if ts < 60:                                  # 전적 우수 기준(good_form 근사)
                    continue
                if no in rev_horses:                         # 쌍승역전 대상이면 역배열 신호로 처리(중복 제외)
                    continue
                pr, ro = pop_rank.get(no), repr_odds.get(no)
                if pr is None or ro is None:
                    continue
                # 시장 비인기 = 인기 하위 절반 밖 이거나 대표 복승배당 15배+ (전적은 좋은데 돈은 안 붙음)
                if pr > max(3, n_pop // 2) or ro >= 15:
                    strong_unpopular.append({"no": int(no), "formScore": round(ts, 1),
                                             "reprOdds": ro, "popRank": pr})
    except Exception:
        strong_unpopular = []

    # 복승 역배열 조합: 역배열 감지말이 낀 복승 조합(배당 있는 것, 저배당순)
    inv_combos, seen_c = [], set()
    for h in inv_horses:
        for k, o in curQ.items():
            if h in k and o and o > 0:
                pair = tuple(sorted(k))
                if pair not in seen_c:
                    seen_c.add(pair)
                    inv_combos.append({"combo": list(pair), "odds": o})
    inv_combos.sort(key=lambda c: c["odds"])
    inv_combos = inv_combos[:6]

    # [역배열 팝업 재정의] 인기순위(단승 배당 기준)보다 조합 배당이 낮게 형성된 말 상세.
    #   각 역배열 말: 마번·인기순위·최저 조합배당(<30배만) → 인기 낮은데 배당 낮으면 실질 유력.
    rank_of = {int(n): i + 1 for i, n in enumerate(fav_rank or [])}
    inv_detail = []
    for h in inv_horses:
        best = None
        for k, o in (curQ or {}).items():
            if h in k and o and 0 < o < 30 and (best is None or o < best):
                best = o
        if best is None:      # <30배 조합만 표시(사용자 요청)
            continue
        inv_detail.append({"no": int(h), "popRank": rank_of.get(int(h)), "odds": round(best, 1)})
    inv_detail.sort(key=lambda x: x["odds"])      # 배당 낮은(실질 유력) 순
    for i, d in enumerate(inv_detail):
        d["lowest"] = (i == 0)                     # 최저 배당 = '← 낮음' 표식
    # 팝업 요약: 인기 낮은데(순위 3위+) 배당 최저인 말이 실질 유력.
    #   [보완] 인기1~2위(정상 최유력)를 '실질 유력'으로 오표기하지 않도록 fallback 제거 → 없으면 None.
    inv_lead = next((d for d in inv_detail if d.get("popRank") and d["popRank"] >= 3), None)

    top_rev = (wx_reversals or [None])[0]
    banner = {
        "refLabel": "단승 1위" if has_win else "복승인기 1위",
        "refNo": ref_no, "refOdds": ref_odds,
        "favPair": list(fav_pair) if fav_pair else None, "favOdds": fav_odds,
        "favNormal": bool(fav_normal),
        "reversal": ({"favorite": top_rev["favorite"], "challenger": top_rev["challenger"],
                      "favoredExacta": top_rev["favoredExacta"], "reverseExacta": top_rev["reverseExacta"],
                      "ratio": top_rev["ratio"]} if top_rev else None),
    } if detected else None

    return {"detected": detected, "types": types, "invHorses": inv_horses,
            "invCombos": inv_combos, "banner": banner,
            "invDetail": inv_detail,          # [팝업] 마번·인기순위·최저조합배당(<30배)
            "invLead": inv_lead,              # [팝업] 인기 낮은데 배당 최저 = 실질 유력 후보
            "strongUnpopular": strong_unpopular}   # [신규] 전적 우수하나 시장 비인기(역배열 아님)


def _rebound_analyze(seq):
    """[배당 반등 패턴] 단일 조합의 배당 시퀀스(시간순) → 급락 후 반등/재급락 분류.
    - drop(급락): 급락 전 고점(pre_max) → 최저(lo). drop_frac<0.10 이면 급락 아님(None).
    - recovery(회복비율) = 급락폭 대비 되돌린 비율 = (cur-lo)/(pre_max-lo).
        · recovery ≤ 0.20  → 'valid'  : 원배당 대비 20% 이내 반등 = 자금 유지 → 신호 유효
        · recovery ≥ 0.80  → 'fake'   : 원배당 80%+ 회복 = 자금 이탈 → 페이크
        · 그 사이           → 'partial'
    - 'recrash'(재급락): 급락 → 반등(최저 대비 +10%↑) → 재급락(반등 고점 대비 -10%↓) = 자금 재유입 = 더 강한 신호.
    반환 {pattern, recovery, orig, low, cur, dropFrac} 또는 None."""
    seq = [x for x in (seq or []) if isinstance(x, (int, float)) and x > 0]
    if len(seq) < 3:
        return None
    lo = min(seq)
    lo_i = seq.index(lo)
    pre_max = max(seq[:lo_i + 1]) if lo_i >= 0 else seq[0]
    if pre_max <= 0:
        return None
    drop_frac = (pre_max - lo) / pre_max
    if drop_frac < 0.10:      # 의미있는 급락이 없으면 반등 패턴 분석 대상 아님
        return None
    cur = seq[-1]
    recovery = (cur - lo) / (pre_max - lo) if pre_max > lo else 0.0
    # 재급락: 최저 이후 반등(+10%↑)했다가 다시 그 반등 고점 대비 -10%↓ 재하락
    recrash = False
    if lo_i < len(seq) - 1:
        after = seq[lo_i:]
        peak_after = max(after)
        peak_after_i = after.index(peak_after)
        if peak_after_i > 0 and peak_after >= lo * 1.10:
            tail = after[peak_after_i:]
            if tail and min(tail) <= peak_after * 0.90:
                recrash = True
    if recrash:
        pattern = "recrash"
    elif recovery <= 0.20:
        pattern = "valid"
    elif recovery >= 0.80:
        pattern = "fake"
    else:
        pattern = "partial"
    return {"pattern": pattern, "recovery": round(recovery, 2),
            "orig": round(pre_max, 1), "low": round(lo, 1), "cur": round(cur, 1),
            "dropFrac": round(drop_frac, 2)}


def _advanced_anomaly(hist, curQ, drops):
    """[2번 고도화] 급락 속도·연속하락/단발반등·페이크베팅·복승 환급률(역수합) 감지.
    hist: 스냅샷 리스트(각 {t, quinella}). 반환:
      {velocity:[{combo,pct,minutes,speed,level}], streaks:{key:{type,confAdj}},
       fakes:[{combo,seq}], overround:{invSum,top3Share,concentrated}, horseConfAdj:{no:점수}}."""
    out = {"velocity": [], "streaks": {}, "fakes": [], "overround": None, "horseConfAdj": {}}
    maps = [_odds_map_un(h.get("quinella")) for h in (hist or [])]
    times = [h.get("t") for h in (hist or [])]

    def _adj(combo, pts):
        for h in combo:
            out["horseConfAdj"][int(h)] = out["horseConfAdj"].get(int(h), 0) + pts

    # ① 급락 속도 = 급락폭 / 수집간격(분). 분당 10%+ 강한 신호(🔴), 5%+ 주의(🟡)
    #   [보완] 마감 임박 짧은 간격(5초 등)에서 -5% 블립이 분당 60%로 과대평가되던 문제 →
    #   간격 하한(0.25분) + '절대 급락폭'도 함께 요구(🔴 abs≥15%, 🟡 abs≥8%)해 과대 신호 제거.
    gap_min = None
    if len(times) >= 2 and times[-1] and times[-2]:
        gap_min = max(0.25, round((times[-1] - times[-2]) / 60.0, 2))
    if gap_min:
        for d in (drops or []):
            if d.get("pct") is not None and d["pct"] < 0:
                speed = round(abs(d["pct"]) / gap_min, 1)
                amag = abs(d["pct"])
                lvl = "🔴" if (speed >= 10 and amag >= 15) else ("🟡" if (speed >= 5 and amag >= 8) else None)
                if lvl:
                    out["velocity"].append({"combo": d["combo"], "pct": d["pct"],
                                            "minutes": gap_min, "speed": speed, "level": lvl})
        out["velocity"].sort(key=lambda v: -v["speed"])

    # ② 연속 하락(3회 연속 → +20) vs 단발 후 반등(-15) + ③ 페이크 베팅(급락 후 반등)
    if len(maps) >= 3:
        keys = set()
        for m in maps[-4:]:
            keys.update(m.keys())
        for k in keys:
            seq = [m[k] for m in maps[-4:] if k in m and m[k] > 0]
            if len(seq) < 3:
                continue
            last3 = seq[-3:]
            # 3회 연속 단조 하락 + 총 하락폭 8%+ (미미한 표류는 제외)
            consec = (all(last3[i] > last3[i + 1] for i in range(2))
                      and last3[0] > 0 and (last3[0] - last3[-1]) / last3[0] >= 0.08)
            lo = min(seq)
            lo_i = seq.index(lo)
            pre_max = max(seq[:lo_i + 1]) if lo_i >= 0 else seq[0]
            drop_frac = (pre_max - lo) / pre_max if pre_max > 0 else 0.0
            rebound_frac = (seq[-1] - lo) / lo if lo > 0 else 0.0
            rebounded = lo_i < len(seq) - 1 and drop_frac >= 0.10 and rebound_frac >= 0.10
            key = f"{k[0]}+{k[1]}"
            if rebounded:
                out["streaks"][key] = {"combo": list(k), "type": "단발후반등", "confAdj": -15}
                _adj(k, -15)
                if drop_frac >= 0.15 and rebound_frac >= 0.15:   # 급락 후 반등 = 페이크 의심
                    out["fakes"].append({"combo": list(k), "seq": [round(x, 1) for x in seq]})
            elif consec:
                out["streaks"][key] = {"combo": list(k), "type": "연속하락", "confAdj": 20}
                _adj(k, 20)

    # ②-b [배당 반등 패턴] 급락→반등 회복비율 정밀 분류 + 급락→반등→재급락(더 강한 신호).
    #   전체 히스토리(마지막 4틱 아님)로 판정 → 회복비율(≤20% 유효 / ≥80% 페이크)·재급락 감지.
    out["rebounds"] = []
    if len(maps) >= 3:
        rkeys = set()
        for m in maps:
            rkeys.update(m.keys())
        for k in rkeys:
            full = [m[k] for m in maps if k in m and m[k] > 0]
            rb = _rebound_analyze(full)
            if not rb:
                continue
            rb["combo"] = list(k)
            out["rebounds"].append(rb)
            # 신호 반영(기존 streaks/fakes 와 별개의 추가 보정): 재급락=더 강한 신호, 유효=보강, 페이크=감점
            if rb["pattern"] == "recrash":
                _adj(k, 20)     # 급락→반등→재급락 = 자금 재유입 = 더 강한 신호
            elif rb["pattern"] == "valid":
                _adj(k, 8)      # 20% 이내 반등 = 자금 유지 = 신호 유효(소폭 보강)
            elif rb["pattern"] == "fake":
                _adj(k, -12)    # 80%+ 회복 = 자금 이탈 = 페이크(감점)
        order = {"recrash": 0, "valid": 1, "partial": 2, "fake": 3}
        out["rebounds"].sort(key=lambda r: (order.get(r["pattern"], 9), -r.get("dropFrac", 0)))

    # [4번] 말별 연속 하락 횟수 추적 (단승 우선, 없으면 그 말이 낀 최저 복승 조합)
    #   1회=후보(⚪) / 2회 연속=약한신호(🟡) / 3회+ 연속=확정신호(🔴) / 급락후 반등=페이크의심(🟠)
    out["horseStreaks"] = {}
    recent = (hist or [])[-5:]

    def _horse_series(no):
        s = []
        for h in recent:
            o = None
            wv = (h.get("win") or {}).get(str(no))
            if wv not in (None, ""):
                try:
                    o = float(wv)
                except (TypeError, ValueError):
                    o = None
            if o is None:   # 단승 없으면 그 말이 낀 최저 복승 조합
                mm = _odds_map_un(h.get("quinella"))
                cand = [v for k, v in mm.items() if no in k and v > 0]
                o = min(cand) if cand else None
            if o and o > 0:
                s.append(round(o, 1))
        return s
    horse_nos = set()
    for m in maps[-5:]:
        for k in m:
            horse_nos.update(k)
    for h in recent:
        for k in (h.get("win") or {}):
            try:
                horse_nos.add(int(k))
            except (TypeError, ValueError):
                pass
    for no in horse_nos:
        s = _horse_series(no)
        if len(s) < 2:
            continue
        cons = 0   # 뒤에서부터 '직전보다 낮음' 연속 횟수
        for i in range(len(s) - 1, 0, -1):
            if s[i] < s[i - 1]:
                cons += 1
            else:
                break
        lo = min(s)
        lo_i = s.index(lo)
        pre_max = max(s[:lo_i + 1]) if lo_i >= 0 else s[0]
        rebounded = (lo_i < len(s) - 1 and lo > 0 and s[-1] >= lo * 1.10
                     and pre_max > 0 and (pre_max - lo) / pre_max >= 0.10)
        if rebounded:
            lvl, label = "🟠", "페이크의심"
        elif cons >= 3:
            lvl, label = "🔴", "확정신호"
        elif cons == 2:
            lvl, label = "🟡", "약한신호"
        elif cons == 1:
            lvl, label = "⚪", "후보"
        else:
            continue
        out["horseStreaks"][no] = {"no": no, "count": cons, "level": lvl,
                                   "label": label, "rebounded": rebounded, "series": s}

    # ④ [3번] 복승 환급률(역수 합) + 상위 조합 집중도. top3 조합이 전체 자금의 90%+ → 특정 조합 집중
    if curQ:
        inv = [1.0 / o for o in curQ.values() if o > 0]
        inv_sum = round(sum(inv), 3)
        if inv_sum > 0:
            top = sorted(inv, reverse=True)
            top3_share = round(sum(top[:3]) / inv_sum, 3)
            out["overround"] = {"invSum": inv_sum, "refundRate": inv_sum,   # 환급률=Σ(1/각조합배당)
                                "top3Share": top3_share, "concentrated": top3_share >= 0.90}
    return out


def _smart_money_combos(hist, curQ, min_rise=0.15, min_drop=0.25):
    """[유형8] 상승 후 급락(스마트머니) — 조합 배당이 초반에 상승(인기 이탈)했다가
    마감 직전 급락(자금 재유입). hist=[{quinella:{...} 또는 [...]}]. 반환 [(combo[2], {rise,drop})] 급락순."""
    try:
        series = {}
        for snap in (hist or []):
            q = snap.get("quinella") or {}
            if isinstance(q, list):
                qq = {}
                for it in q:
                    cb = it.get("combo") if isinstance(it, dict) else None
                    od = it.get("odds") if isinstance(it, dict) else None
                    if cb and od:
                        qq["+".join(str(x) for x in cb)] = od
                q = qq
            if not isinstance(q, dict):
                continue
            for k, od in q.items():
                try:
                    od = float(od)
                except Exception:
                    continue
                if od > 0:
                    series.setdefault(k, []).append(od)
        out = []
        for k, seq in series.items():
            if len(seq) < 3:
                continue
            first = seq[0]
            peak = max(seq)
            cur = seq[-1]
            peak_i = max(range(len(seq)), key=lambda i: seq[i])
            rise = (peak - first) / first if first > 0 else 0
            drop = (peak - cur) / peak if peak > 0 else 0
            # peak가 초반 이후 발생(상승 구간)하고 현재가 peak보다 크게 낮음(급락)
            if rise >= min_rise and drop >= min_drop and peak_i > 0 and cur < peak:
                nums = [int(x) for x in k.replace("-", "+").split("+") if x.strip().isdigit()]
                if len(nums) == 2:
                    out.append((nums, {"rise": round(rise * 100), "drop": round(drop * 100)}))
        out.sort(key=lambda t: -t[1]["drop"])
        return out
    except Exception:
        return []


# ───────── [강한 신호 8유형 자동 감지] 기존 계산 신호 재사용 → 오버레이 강조·학습용 파생 ─────────
# 유형1 연속하락3회+ · 유형2 역배열10%+ · 유형3 마감직전대급락(T-2분30%+) · 유형4 재급락
# 유형5 복수조합동시급락2개+ · 유형6 역배열+급락(이중수렴·단독강력) · 유형7 환수율90%+집중 · 유형8 상승후급락
_STRONG_SIGNAL_META = {
    1: "연속하락 3회+", 2: "역배열 10%+", 3: "마감직전 대급락", 4: "재급락",
    5: "복수조합 동시급락", 6: "역배열+급락(이중수렴)", 7: "환수율 90%+ 집중", 8: "상승후급락(스마트머니)",
}


def _strong_signals(advanced, inverse, drops, cur_mb, hist, curQ, after_close):
    """8유형 강신호 산출(무삭제 — 표시/학습용 파생 뷰). 기존 advanced·inverse·drops·history만 소비.
    반환 {signals:[{type,label,horses,detail,level}], count(서로다른유형수), recommendLevel, dualConverge, types[]}."""
    adv = advanced or {}
    inv = inverse or {}
    sigs = []

    def add(t, horses, detail, level):
        hs = sorted(set(int(h) for h in (horses or []) if h is not None))
        sigs.append({"type": t, "label": _STRONG_SIGNAL_META.get(t, ""), "horses": hs,
                     "detail": detail, "level": level})

    # 강한 급락 조합(pct<=-30) + 급락 말별 조합 수
    strong_drops = [d for d in (drops or []) if d.get("combo") and (d.get("pct") or 0) <= -30]
    drop_horse_cnt = {}
    for d in strong_drops:
        for h in (d.get("combo") or []):
            try:
                drop_horse_cnt[int(h)] = drop_horse_cnt.get(int(h), 0) + 1
            except Exception:
                pass

    # 유형1: 연속하락 3회+
    for no, s in (adv.get("horseStreaks") or {}).items():
        if (s.get("count") or 0) >= 3 and not s.get("rebounded"):
            add(1, [no], str(no) + "번 " + str(s.get("count")) + "연속 하락", "red")

    # 유형2: 역배열 10%+
    lead = inv.get("invLead") if inv.get("detected") else None
    lead_no = lead.get("no") if lead else None
    lead_diff = (lead.get("diffPct") or 0) if lead else 0
    if lead_no is not None and lead_diff >= 10:
        lvl = "red" if ("🔴" in (lead.get("level") or "")) else "orange"
        add(2, [lead_no], str(lead_no) + "번 " + (lead.get("tag") or "역배열") + " (배당차 " + str(lead_diff) + "%)", lvl)

    # 유형3: 마감직전 대급락(T-2분 이내 30%+)
    if cur_mb is not None and 0 <= cur_mb <= 2 and not after_close and strong_drops:
        top = sorted(strong_drops, key=lambda x: x.get("pct", 0))[:2]
        hs = []
        for d in top:
            hs += (d.get("combo") or [])
        det = " / ".join(str(d["combo"][0]) + "+" + str(d["combo"][1]) + " " + str(d.get("pct")) + "%" for d in top)
        add(3, hs, "T-" + str(cur_mb) + "분 " + det, "red")

    # 유형4: 재급락(급락→반등→재급락)
    for rb in (adv.get("rebounds") or []):
        if rb.get("pattern") == "recrash" and rb.get("combo"):
            c = rb["combo"]
            add(4, c, str(c[0]) + "+" + str(c[1]) + " 급락→반등→재급락(강)", "red")

    # 유형5: 복수조합 동시급락 2개+
    multi = sorted([h for h, c in drop_horse_cnt.items() if c >= 2], key=lambda h: -drop_horse_cnt[h])
    if multi:
        det = ", ".join(str(h) + "번(" + str(drop_horse_cnt[h]) + "조합)" for h in multi[:3])
        add(5, multi[:3], det + " → 자금 집중", "red")

    # 유형6: 역배열+급락 동시(이중수렴) — 단독으로도 강력
    dual = False
    if lead_no is not None and lead_diff >= 10 and drop_horse_cnt.get(lead_no, 0) >= 1:
        dual = True
        _mx = min([d.get("pct", 0) for d in strong_drops if lead_no in (d.get("combo") or [])] or [0])
        add(6, [lead_no], str(lead_no) + "번 역배열 " + str(lead_diff) + "% + 급락 " + str(_mx) + "%", "red")

    # 유형7: 환수율 90%+ 집중
    ov = adv.get("overround") or {}
    if ov.get("concentrated"):
        inv_sum = ov.get("invSum") or 0
        refund = round((1.0 / inv_sum if inv_sum > 1 else inv_sum) * 100) if inv_sum else None
        share = round((ov.get("top3Share") or 0) * 100)
        add(7, [], "상위3조합 " + str(share) + "% 집중" + ((" · 환수율 " + str(refund) + "%") if refund is not None else ""), "orange")

    # 유형8: 상승 후 급락(스마트머니)
    for c, info in _smart_money_combos(hist, curQ)[:2]:
        add(8, c, str(c[0]) + "+" + str(c[1]) + " 상승 " + str(info["rise"]) + "% 후 급락 " + str(info["drop"]) + "%", "orange")

    by_type = {}
    for s in sigs:
        by_type.setdefault(s["type"], []).append(s)
    count = len(by_type)
    if dual or count >= 3:
        rec_level = "강력"
    elif count == 2:
        rec_level = "복승"
    elif count == 1:
        rec_level = "보조"
    else:
        rec_level = None
    return {"signals": sigs, "count": count, "recommendLevel": rec_level,
            "dualConverge": dual, "types": sorted(by_type.keys())}


# ───────── [저배당 압축 패턴] 유력마 TOP3 중 저배당 말 밀집(축 패턴) 감지 + 신호 결합 ─────────
def _horse_repr_odds(h, curWin, curQ):
    """말 대표 배당 = 단승 우선, 없으면 그 말 포함 최저 복승."""
    try:
        if curWin.get(h):
            return curWin[h]
        best = None
        for k, o in (curQ or {}).items():
            pair = k if isinstance(k, (tuple, list, frozenset)) else None
            if pair and h in pair and o and o > 0 and (best is None or o < best):
                best = o
        return best
    except Exception:
        return None


def _compression_pattern(key_horses, curWin, curQ, strong_signals=None, sport=None):
    """[1·2번] 유력마 TOP3 중 저배당 말이 몰린 축 패턴 감지.
      4배 이하 2두+ = 강력 압축(복승 메인 자신있게) / 5배 이하 3두+ = 중간 압축.
    [3번] 급락 신호 결합 → 최강(복승 메인 강력) / 역배열 결합 → 고배당 보험 추가.
    [개선·경마전용] 경륜/경정/바이크는 저배당 압축 비활성(배당 신호 기반만) + 명확한 축(배당차 30%+)일 때만 추천.
    반환 {detected, level, band, horses[], combo[2], axis, axisGap, reprs[], withDrop, withReversal, note}."""
    try:
        # [2번 개선] 경륜/경정/바이크는 저배당 압축 공식 비활성화 → 배당 신호 기반 추천만 사용.
        if sport in ("cycle", "boat", "bike"):
            return {"detected": False, "disabled": True, "reason": "경륜/경정/바이크는 저배당 압축 미적용(배당 신호 기반만)"}
        top = [int(h) for h in (key_horses or [])[:3] if h is not None]
        reprs = []
        for h in top:
            o = _horse_repr_odds(h, curWin, curQ)
            if o is not None:
                reprs.append((h, round(float(o), 1)))
        le4 = [(h, o) for h, o in reprs if o <= 4.0]
        le5 = [(h, o) for h, o in reprs if o <= 5.0]
        level = band = None
        horses = []
        if len(le4) >= 2:
            level, band, horses = "강력", "4배 이하 2두+", [h for h, _ in le4]
        elif len(le5) >= 3:
            level, band, horses = "중간", "5배 이하 3두+", [h for h, _ in le5]
        if not level:
            return {"detected": False}
        horses = horses[:3]
        # [2번 개선·축 판정] 저배당 밀집이어도 '명확한 1순위 축'(최저배당이 2순위보다 30%+ 낮음)일 때만 추천.
        #   둘 다 저배당인데 배당이 비슷(차이<30%)하면 축 불명확 → 추천 보류(둘 중 누가 이길지 불확실).
        low = sorted([(h, o) for h, o in reprs if h in horses], key=lambda t: t[1])
        if len(low) < 2:
            return {"detected": False}
        o1, o2 = low[0][1], low[1][1]
        axis_gap = round((o2 - o1) / o2 * 100, 1) if o2 else 0.0   # 2순위 대비 1순위가 몇 % 낮은가
        if o1 > o2 * 0.70:                                          # 30% 미만 차이 = 축 불명확
            return {"detected": False, "noAxis": True, "level": level, "band": band,
                    "reprs": [{"no": h, "odds": o} for h, o in reprs], "axisGap": axis_gap,
                    "note": "저배당 2두 배당 비슷(축 불명확·차이 %g%%) → 추천 보류" % axis_gap}
        axis = low[0][0]                                            # 명확한 1순위 축
        combo = sorted([low[0][0], low[1][0]])
        types = set((strong_signals or {}).get("types") or [])
        with_drop = bool(types & {1, 3, 4, 5, 6})   # 연속하락/마감급락/재급락/동시급락/이중수렴
        with_rev = bool(types & {2, 6})              # 역배열/이중수렴
        if with_drop:
            note = "압축 + 급락 동시 → 복승 메인 강력 추천 (축 %d번·배당차 %g%%)" % (axis, axis_gap)
        elif with_rev:
            note = "압축 + 역배열 → 복승 메인 + 삼복승 보험 (축 %d번)" % axis
        else:
            note = "저배당 압축(명확한 축 %d번·배당차 %g%%) → 복승 메인 자신있게" % (axis, axis_gap)
        return {"detected": True, "level": level, "band": band, "horses": horses,
                "axis": axis, "axisGap": axis_gap,
                "reprs": [{"no": h, "odds": o} for h, o in reprs], "combo": combo,
                "withDrop": with_drop, "withReversal": with_rev, "note": note}
    except Exception:
        return {"detected": False}


# ───────── [배당 3착 자동 발굴] 저배당 압축(축 2두) 확정 시 삼복승 3착 자리를 고배당 말에서 발굴 ─────────
# 우선순위: ①급락 감지 고배당(유형3·5) ②역배열 감지 고배당 ③전적 우수+고배당 ④연속하락 고배당.
# 배당 7~50배(고배당) 우선, 상위 2~3두 → 삼복승 보험(축2두+후보) 편성.
_TP_CONF = {1: "높음", 2: "중", 3: "중", 4: "중", 5: "중"}
_TP_ICON = {1: "\U0001f534", 2: "\U0001f504", 3: "\U0001f4ca", 4: "\U0001f4c9", 5: "\U0001f6a8"}


def _third_place_hunt(compression_pattern, strong_signals, anomaly_horse, wx_reversals,
                      form, curWin, curQ, drops=None, valid_nos=None):
    """[배당 3착 자동 발굴] 압축 축 2두 + 고배당 3착 후보 → 삼복승 보험 편성. 추정배당은 호출부에서 채움."""
    try:
        cp = compression_pattern or {}
        if not cp.get("detected"):
            return {"active": False}
        axis = [int(x) for x in (cp.get("combo") or [])]
        if len(axis) != 2:
            return {"active": False}
        axis_set = set(axis)
        sigs = (strong_signals or {}).get("signals") or []

        def horses_of(*types):
            s = set()
            for x in sigs:
                if x.get("type") in types:
                    for h in (x.get("horses") or []):
                        s.add(int(h))
            return s
        drop_horses = horses_of(3, 5)                 # ① 급락 계열
        rev_sig_horses = horses_of(2, 6)              # ② 역배열 신호
        streak_horses = horses_of(1)                  # ④ 연속하락
        rev_challengers = set(int(r["challenger"]) for r in (wx_reversals or []) if r.get("challenger") is not None)
        form_map = {int(h.get("no")): h for h in (form or []) if h.get("no") is not None}
        # 말별 최대 급락폭(표시용)
        drop_pct = {}
        for d in (drops or []):
            for h in (d.get("combo") or []):
                p = d.get("pct")
                if p is not None:
                    h = int(h)
                    if h not in drop_pct or p < drop_pct[h]:
                        drop_pct[h] = p

        cand = {}

        def consider(no, priority, reason):
            no = int(no)
            if no in axis_set:
                return
            if valid_nos and no not in valid_nos:
                return
            od = _horse_repr_odds(no, curWin, curQ)
            if od is None:
                return
            od = round(float(od), 1)
            if od < 5:                                # 저배당(축 후보)은 3착 발굴 대상 아님
                return
            cur = cand.get(no)
            if cur is None or priority < cur["priority"]:
                cand[no] = {"no": no, "odds": od, "priority": priority, "reason": reason,
                            "conf": _TP_CONF.get(priority, "중"), "icon": _TP_ICON.get(priority, ""),
                            "inBand": 7 <= od <= 50, "dropPct": drop_pct.get(no)}
        for no in drop_horses:                        # ① 급락 감지 고배당
            consider(no, 1, "급락 감지" + ((" " + str(drop_pct[no]) + "%") if drop_pct.get(no) is not None else ""))
        for no in (rev_sig_horses | rev_challengers):  # ② 역배열 감지 고배당
            consider(no, 2, "역배열 감지")
        for no, h in form_map.items():                # ③ 전적 우수 + 고배당
            fs = h.get("totalScore")
            if fs is None:
                fs = h.get("formScore")
            if fs is not None and fs >= 60:
                consider(no, 3, "전적 우수(" + str(round(fs)) + ")")
        for no in streak_horses:                      # ④ 연속하락 고배당
            consider(no, 4, "연속하락")
        if anomaly_horse is not None:                 # 폴백: 이상감지말
            consider(anomaly_horse, 5, "이상감지말")

        ranked = sorted(cand.values(), key=lambda c: (0 if c["inBand"] else 1, c["priority"], c["odds"]))
        top = ranked[:3]
        trios = [{"combo": sorted(axis + [c["no"]]), "cand": c["no"], "reason": c["reason"],
                  "conf": c["conf"], "expOddsEst": None} for c in top]
        return {"active": bool(top), "axis": axis, "mainCombo": sorted(axis),
                "candidates": top, "trios": trios}
    except Exception:
        return {"active": False}


# ───────── [새 규칙·카와사키11R 학습] 막판 급락+역배열 동시 감지 말 → 삼복승 강제 편성 ─────────
#   카와사키 11경주(2026-07-09, 결과 6-1-5) 복기 결과 도출된 규칙:
#     6번이 T-1분 4+6 -33% 급락 + 역배열 감지였으나 유력마 TOP3(1·8·5) 밖이라 복승/삼복승 미편성 → 6번 1착 놓침.
#   ⇒ [규칙] T-2분 이내(마감 임박)에 '급락'과 '역배열'이 **동시에** 감지된 말은 유력마 순위와 무관하게
#           삼복승 보험에 **강제 편성**(유력마 상위 2두와 묶어)하고 오버레이에 강조 표시한다.
#   ⚠ 기존 삼복승 편성·third_place_hunt·strong_signals 무삭제 — 별도 '강제보험' 블록만 추가.
def _reversal_backing_bets(inverse, key_horses, curQ, after_close):
    """[히로시마 2R 학습] 역배열이 지목한 '실질 유력마'(invLead: 인기 낮은데 조합배당 최저 = 고배당인데
    시장이 실질 승자로 미는 말)를 복승 축으로, 다른 유력마들과 '받치기 복승'을 편성한다.
    복승 메인이 실질유력마를 특정 1두와만 묶어(예: 1+7), 실제 결과가 실질유력마+다른유력마(7+4)면
    놓치던 문제 방지 → 실질유력마 축으로 모든 유력마와 받치기(7+1·7+4)해 복승 적중 폭을 넓힘.
    반환 [{combo:[lead,other], odds, note}] (마감 후엔 빈 리스트 = 참고만)."""
    if after_close:
        return []
    inv = inverse or {}
    lead = inv.get("invLead") if inv.get("detected") else None
    if not lead or lead.get("no") is None:
        return []
    try:
        lead_no = int(lead["no"])
    except (TypeError, ValueError):
        return []
    out, seen = [], set()
    for h in (key_horses or []):
        try:
            h = int(h)
        except (TypeError, ValueError):
            continue
        if h == lead_no or h in seen:
            continue
        seen.add(h)
        cc = tuple(sorted((lead_no, h)))
        o = (curQ or {}).get(cc)
        out.append({"combo": list(cc), "odds": (round(o, 1) if isinstance(o, (int, float)) else None),
                    "lead": lead_no, "partner": h,
                    "note": f"역배열 실질유력 {lead_no}번(고배당) 축 + 유력마 {h}번 받치기"})
    return out[:3]


def _forced_trifecta_insurance(cur_mb, after_close, drops, wx_reversals, inverse,
                               strong_signals, key_horses, drop_thresh=-30):
    """T-2분 이내 급락(강급락 pct<=drop_thresh)과 역배열을 동시에 보인 말을 찾아
      유력마 상위 2두와 묶은 삼복승 강제보험 조합을 만든다.
    반환 {active, minutesBefore, horses:[{no,dropPct,revTag,note}], combos:[[a,b,c]], note}."""
    try:
        if after_close or cur_mb is None or not (0 <= cur_mb <= 2):
            return {"active": False}
        # ① 강급락 말(마감 임박 현재 스냅샷 drops) — 조합 최저 pct 기록
        drop_pct = {}
        for d in (drops or []):
            p = d.get("pct")
            if p is None or p > drop_thresh:
                continue
            for h in (d.get("combo") or []):
                h = int(h)
                if h not in drop_pct or p < drop_pct[h]:
                    drop_pct[h] = p
        if not drop_pct:
            return {"active": False}
        # ② 역배열 말 — 쌍승역전 challenger + 역배열 감지말(invLead/invHorses)
        rev = {}
        for r in (wx_reversals or []):
            c = r.get("challenger")
            if c is not None:
                rev[int(c)] = "쌍승역전"
        inv = inverse or {}
        if inv.get("detected"):
            lead = inv.get("invLead") or {}
            if lead.get("no") is not None:
                rev.setdefault(int(lead["no"]), "역배열 실질유력")
            for h in (inv.get("invHorses") or []):
                rev.setdefault(int(h), "역배열 감지")
        # ③ 동시 감지(급락 ∩ 역배열) = 강제 편성 대상
        forced = sorted(set(drop_pct) & set(rev), key=lambda h: drop_pct[h])
        if not forced:
            return {"active": False}
        axis = [int(h) for h in (key_horses or [])[:2] if h is not None]
        horses, combos, seen = [], [], set()
        for hf in forced:
            note = "막판 급락 " + str(drop_pct[hf]) + "% + " + rev[hf] + " 동시 → 삼복승 강제 편성"
            horses.append({"no": hf, "dropPct": drop_pct[hf], "revTag": rev[hf], "note": note})
            base = [a for a in axis if a != hf][:2]        # 유력마 상위 2두(자기 제외)
            if len(base) >= 2:
                cc = tuple(sorted([base[0], base[1], hf]))
                if len(set(cc)) == 3 and cc not in seen:
                    seen.add(cc)
                    combos.append(list(cc))
        return {"active": bool(horses), "minutesBefore": cur_mb,
                "horses": horses, "combos": combos,
                "note": "T-" + str(cur_mb) + "분 급락+역배열 동시 감지 말 → 유력마 순위 무관 삼복승 강제 편성"}
    except Exception as _e:
        print("[강제삼복승] 실패:", _e)
        return {"active": False}


def _closing_drop_insurance(cur_mb, drops, key_horses, valid_nos=None, mass_drop=None, drop_thresh=-30):
    """[마감 순간 급락 삼복승 보험] T-0(마감) 전후 급락 말 → 축(유력마 상위2) + 급락말 삼복승 보험 자동 편성.
    역배열 불필요(급락만으로) — _forced_trifecta_insurance(급락∩역배열)가 놓치는 급락 전용 말을 담당.
    기후 9R 복기: 1+7 축 + 5·6번 마감 순간 급락 → 1+7+5·1+7+6 삼복승 보험 자동.
    ⚠ 대규모 급락(시장 전체 재설정·노이즈, 예: 하코다테 10R 163건 동시 급락)은 제외 — 특정 말 자금집중이 아님.
    반환 {active, minutesBefore, axis, horses:[{no,dropPct,dropCombos,note}], combos:[[a,b,c]], note}."""
    try:
        # 마감 순간 = T-1분~마감 직후(cur_mb<=1). 마감 훨씬 전은 미적용(기존 forced_trifecta 담당).
        if cur_mb is None or cur_mb > 1:
            return {"active": False}
        # [대규모 급락 가드] 시장 전체가 동시 급락 = 배당 재설정 노이즈 → 특정 복병 아님, 보험 미편성.
        if mass_drop and mass_drop.get("detected"):
            return {"active": False, "skipped": "massDrop"}
        cnt, worst = {}, {}
        for d in (drops or []):
            p = d.get("pct")
            if p is None or p > drop_thresh:
                continue
            for h in (d.get("combo") or []):
                h = int(h)
                cnt[h] = cnt.get(h, 0) + 1
                worst[h] = min(worst.get(h, 0), p)
        if not cnt:
            return {"active": False}
        # [대규모 급락 가드 2] 급락 말이 전체 출전의 60%+ = 시장 전반 붕괴(노이즈) → 미편성.
        _field = len(valid_nos) if valid_nos else 0
        if _field >= 6 and len(cnt) >= _field * 0.6:
            return {"active": False, "skipped": "marketWide"}
        axis = [int(h) for h in (key_horses or [])[:2] if h is not None]
        if len(axis) < 2:
            return {"active": False}
        # 급락 말 중 축 아닌 말 → 조합 수 많은 순(자금 집중)·급락폭 큰 순, 최대 3두 보험.
        cand = sorted([h for h in cnt if h not in axis], key=lambda h: (-cnt[h], worst[h]))
        horses, combos, seen = [], [], set()
        for hf in cand[:3]:
            cc = tuple(sorted([axis[0], axis[1], hf]))
            if len(set(cc)) == 3 and cc not in seen:
                seen.add(cc)
                combos.append(list(cc))
                horses.append({"no": hf, "dropPct": worst[hf], "dropCombos": cnt[hf],
                               "note": "마감 순간 급락 %d%%(%d개 조합) → 삼복승 보험" % (worst[hf], cnt[hf])})
        return {"active": bool(combos), "minutesBefore": cur_mb, "axis": axis,
                "horses": horses, "combos": combos,
                "note": "마감(T-0) 순간 급락 말 → 축 %d+%d + 급락말 삼복승 보험 자동 편성" % (axis[0], axis[1])}
    except Exception as _e:
        print("[마감급락보험] 실패:", _e)
        return {"active": False}


# ───────── [패스 권고 경주] 추천 없음/전적없음+신호없음 = 손익·적중률 집계 제외 ─────────
def _is_pass_race(an):
    """패스 권고 경주 판정: ①추천 게이트(신호 대기·패스형) 또는 ②전적없음+강신호없음.
      → 손익 계산·추천 적중률 집계에서 제외(추천을 안 했으므로 미적중이 아니라 '패스가 맞음')."""
    try:
        if not an:
            return False
        if an.get("recommendGated"):
            return True
        rj = an.get("raceJudgment") or {}
        if rj.get("type") in ("wait", "패스형"):
            return True
        no_form = not (an.get("form") or [])
        no_signal = not int((an.get("strongSignals") or {}).get("count") or 0)
        return bool(no_form and no_signal)
    except Exception:
        return False


# ───────── [추천 말 수 유연화] 신호 강도(강신호 유형 수)에 따라 추천 말 수 범위 안내 ─────────
def _recommend_flex(strong_signals, signal_confidence, after_close):
    """신호 강도별 추천 말 수 가이드(강제 아님·안내).
      신호 3개+ → 3~4두 / 2개 → 2~3두 / 1개 → 1~2두 / 0개 → 추천 보류.
      strong_signals.count(서로 다른 강신호 유형 수) 기준, 강신호말 존재 시 최소 1로 보정."""
    cnt = int((strong_signals or {}).get("count") or 0)
    strong_horses = len((signal_confidence or {}).get("strong") or [])
    eff = max(cnt, 1 if strong_horses >= 1 else 0)
    if after_close:
        return {"signalCount": cnt, "minHorses": 0, "maxHorses": 0,
                "recommend": True, "note": "마감 후 — 참고만"}
    if eff >= 3:
        return {"signalCount": cnt, "minHorses": 3, "maxHorses": 4, "recommend": True,
                "note": "강신호 " + str(eff) + "개 → 3~4두 추천"}
    if eff == 2:
        return {"signalCount": cnt, "minHorses": 2, "maxHorses": 3, "recommend": True,
                "note": "신호 2개 → 2~3두 추천"}
    if eff == 1:
        return {"signalCount": cnt, "minHorses": 1, "maxHorses": 2, "recommend": True,
                "note": "신호 1개 → 1~2두 추천"}
    return {"signalCount": 0, "minHorses": 0, "maxHorses": 0, "recommend": False,
            "note": "신호 없음 → 추천 보류(신호 대기)"}


# ───────── [고배당 동반 패턴·참고] 고배당 신호말과 배당 낮게 묶인 다른 고배당 말 탐색(메인과 별도) ─────────
def _repr_odds_from_rec(rec):
    """rec(단승/복승)에서 말별 대표배당 맵 + curWin/curQ 재구성(학습·분석 공용)."""
    curWin = {}
    for k, v in (rec.get("single") or {}).items():
        try:
            curWin[int(k)] = float(v)
        except (TypeError, ValueError):
            pass
    curQ = {}
    for c in (rec.get("quinella") or []):
        combo, o = c.get("combo"), c.get("odds")
        if combo and len(combo) == 2 and o:
            try:
                curQ[tuple(sorted(int(x) for x in combo))] = float(o)
            except (TypeError, ValueError):
                pass
    repr_map = {}
    for h in set([x for k in curQ for x in k] + list(curWin)):
        repr_map[h] = _horse_repr_odds(h, curWin, curQ)
    return repr_map, curWin, curQ


def _high_odds_companion(drops, wx_reversals, inverse, curWin, curQ, key_horses,
                         learned=None, after_close=False, high_thresh=7.0):
    """[참고 추천] 고배당(7배+) 신호말 감지 시 그 말과 복승 배당 낮게 묶인 다른 고배당 말 탐색.
      참고 삼복승(유력마1 + 고배당신호말 + 동반고배당) 제시. ⚠ 강제 아님 — 메인 추천과 별도 참고용.
    반환 {active, axis, items:[{no,odds,signal,partners:[{no,odds,linkOdds}],trios:[[..]]}], learned, note}."""
    try:
        if after_close:
            return {"active": False}

        def repr_odds(h):
            return _horse_repr_odds(int(h), curWin, curQ)

        # 신호 고배당말: 급락(≤-30%) / 역배열(쌍승역전 challenger·역배열 감지말)
        sig = {}
        for d in (drops or []):
            if (d.get("pct") or 0) <= -30:
                for h in (d.get("combo") or []):
                    p = d.get("pct")
                    sig.setdefault(int(h), "급락")
                    # 최대 급락폭 표기용
        drop_pct = {}
        for d in (drops or []):
            for h in (d.get("combo") or []):
                p = d.get("pct")
                if p is not None:
                    h = int(h)
                    if h not in drop_pct or p < drop_pct[h]:
                        drop_pct[h] = p
        for r in (wx_reversals or []):
            c = r.get("challenger")
            if c is not None:
                sig.setdefault(int(c), "역배열")
        inv = inverse or {}
        if inv.get("detected"):
            for h in (inv.get("invHorses") or []):
                sig.setdefault(int(h), "역배열")
        # 고배당(7배+) 신호말만
        hi = []
        for h, tag in sig.items():
            o = repr_odds(h)
            if o and o >= high_thresh:
                hi.append((h, round(float(o), 1), tag))
        if not hi:
            return {"active": False}
        hi.sort(key=lambda t: -t[1])
        axis = int(key_horses[0]) if key_horses else None
        items = []
        for h, o, tag in hi[:2]:
            partners = []
            seen = set()
            for k, qo in (curQ or {}).items():
                if h not in k or not qo or qo <= 0:
                    continue
                other = [x for x in k if x != h]
                if not other:
                    continue
                p = int(other[0])
                if p == axis or p in seen:
                    continue
                po = repr_odds(p)
                if po and po >= high_thresh:
                    seen.add(p)
                    partners.append({"no": p, "odds": round(float(po), 1), "linkOdds": round(float(qo), 1)})
            partners.sort(key=lambda x: x["linkOdds"])   # 묶임(복승) 배당 낮은 순 = 함께 들어올 가능성↑
            partners = partners[:2]
            trios = []
            for pt in partners:
                base = [axis, h, pt["no"]] if axis is not None else [h, pt["no"]]
                cc = sorted(set(int(x) for x in base if x is not None))
                if len(cc) == 3:
                    trios.append(cc)
            det = tag + ((" " + str(drop_pct[h]) + "%") if (tag == "급락" and drop_pct.get(h) is not None) else "")
            items.append({"no": h, "odds": o, "signal": det, "partners": partners, "trios": trios})
        items = [it for it in items if it["partners"]]
        if not items:
            return {"active": False}
        return {"active": True, "axis": axis, "items": items, "learned": learned,
                "note": "고배당 신호말과 배당 낮게 묶인 다른 고배당 말 → 함께 들어올 가능성(참고용·메인과 별도)"}
    except Exception as _e:
        print("[고배당동반] 실패:", _e)
        return {"active": False}


# ───────── [BMED 매트릭스 베팅 전략] 상황별 5전략 자동선택 + 원금보전 배분 + 기대환수율 ─────────
def _capital_preservation(combos):
    """[2번] 원금 보전 자동 계산(예산 무관 '비율' 산출 → 프론트가 예산 곱함).
    combos=[{combo,odds}]. 각 조합 적중 시 총원금 이상 회수되도록 배당 역산 배분.
      Σ(1/o)≤1: base_i=1/o(적중 시 정확히 원금) + 잔여 균등분배(고배당 조합 상방 확대) = 원금 보전.
      Σ(1/o)>1: 등환수 더치(1/o 정규화) = 손실 최소·모든 적중 동일 회수율(보전 불가).
    반환 (plan[{combo,odds,ratio,payoutRatio}], preserved, returnRate%)."""
    cc = [(tuple(int(x) for x in c["combo"]), float(c["odds"]))
          for c in (combos or []) if c.get("odds") and c["odds"] > 0]
    if not cc:
        return [], False, None
    S = sum(1.0 / o for _, o in cc)
    n = len(cc)
    plan = []
    if S <= 1.0:
        leftover = 1.0 - S
        for c, o in cc:
            ratio = 1.0 / o + leftover / n           # 원금 보전 + 잔여 균등(고배당일수록 회수↑)
            plan.append({"combo": list(c), "odds": round(o, 1), "ratio": round(ratio, 4),
                         "payoutRatio": round(ratio * o, 3)})
        preserved = True
        return_rate = round(min(p["payoutRatio"] for p in plan) * 100, 1)
    else:
        for c, o in cc:
            ratio = (1.0 / o) / S                    # 등환수 더치(보전 불가 시 손실 최소)
            plan.append({"combo": list(c), "odds": round(o, 1), "ratio": round(ratio, 4),
                         "payoutRatio": round(ratio * o, 3)})
        preserved = False
        return_rate = round((1.0 / S) * 100, 1)
    return plan, preserved, return_rate


def _expected_return(plan, curQ):
    """[3번] 기대 환수율 = Σ(적중확률_i × 회수비율_i). 확률=시장 복승 역수(1/o) 정규화(전체 조합).
    반환 (expected%, bestCasePayoutRatio, coveredProb%)."""
    if not plan:
        return None, None, None
    inv_all = sum(1.0 / o for o in curQ.values() if o > 0) or 1.0
    exp, covered = 0.0, 0.0
    for x in plan:
        p = (1.0 / x["odds"]) / inv_all if x["odds"] > 0 else 0.0
        covered += p
        exp += p * x["payoutRatio"]
    return round(exp * 100, 1), round(max(x["payoutRatio"] for x in plan), 3), round(covered * 100, 1)


def _bmed_insurance(key_horses, curQ, signal_confidence, inverse, sport="horse"):
    """[보험용 추천] BMED 보험형 매트릭스 — 1착 유력마 축 + 2·3·4위 상대 3조합.
       1+2(최다 베팅·수익 극대)·1+3(중간·준수익)·1+4(최소·손실 최소).
       배당별 자동 비율: 저배당(A+B<3) 60/25/15 · 중배당(3~7) 원금보전 역산 · 고배당(≥7) 40/35/25.
    활성조건: 유력마 4두 압축 + 1착 신뢰도 70%+ + A+B 3배+ + 역배열 미감지.
    (이런 경기 유형일 때만 '보험용'으로 정상 추천과 함께 제시 → 사용자가 보고 선택)."""
    horses = list(dict.fromkeys(int(h) for h in (key_horses or [])))
    h4 = horses[:4]
    # [수정#2 조건완화] 유력마 4두→3두 이상이면 매트릭스 표시(데이터 부족 시에도 보험형 제공).
    #   3두면 1+2·1+3(2조합), 4두면 1+2·1+3·1+4(3조합) 자동 생성.
    cond_compress = len(horses) >= 3
    # 1착 신뢰도(배당 기반): 최저 4개 복승 조합 중 1착축(h0) 포함 비율(%) — 참고 지표(게이트 아님)
    fav_conf = 0.0
    if h4 and curQ:
        cheapest4 = [k for k, _ in sorted(curQ.items(), key=lambda kv: kv[1])[:4]]
        if cheapest4:
            fav_conf = round(sum(1 for k in cheapest4 if h4[0] in k) / len(cheapest4) * 100)
    cond_conf = fav_conf >= 70
    ab_odds = curQ.get(tuple(sorted(h4[:2]))) if len(h4) >= 2 else None
    cond_ab = ab_odds is not None and ab_odds >= 3.0   # 3배+ = 원금보전 여력(저배당은 제한)
    inv_det = bool(inverse and inverse.get("detected"))
    conditions = [
        {"label": "유력마 3두+ 압축", "ok": cond_compress, "value": f"{len(horses)}두"},
        {"label": "1착 신뢰도(참고)", "ok": cond_conf, "value": f"{int(fav_conf)}%"},
        {"label": "A+B 배당 3배+(원금보전)", "ok": cond_ab, "value": (f"{ab_odds}배" if ab_odds else "-")},
        {"label": "역배열 미감지", "ok": not inv_det},
    ]
    # [수정#2 조건완화] 활성화 = 유력마 3두+ 압축 + 역배열 아님 + A+B 복승배당 존재.
    #   신뢰도 70% 게이트 제거(데이터 부족 시에도 표시). 신뢰도는 참고 지표로만 노출.
    active = cond_compress and not inv_det and (ab_odds is not None)
    res = {"active": active, "conditions": conditions, "favConf": fav_conf,
           "anchor": h4[0] if h4 else None, "horses": h4, "abOdds": ab_odds,
           "sixRacer": sport in ("cycle", "boat", "bike"),   # [탭분리] 6명 출전 종목 표기
           "usage": "유력마 압축 + 1착 확실 (역배열 아님)"}
    if not active:
        res["alternate"] = "역배열형" if inv_det else ("분산형" if len(horses) < 3 else "정상 추천")
        res["altReason"] = ("역배열 감지됨 → BMED 역배열형 권장" if inv_det else
                            "유력마 3두 미만 또는 A+B 복승배당 미수집 → 정상 추천 사용")
        return res
    # 3조합: 1+2, 1+3, 1+4 (1착축 h0 고정)
    combos = []
    for i, lbl in ((1, "1+2"), (2, "1+3"), (3, "1+4")):
        if i < len(h4):
            pair = tuple(sorted((h4[0], h4[i])))
            o = curQ.get(pair)
            if o and o > 0:
                combos.append({"combo": list(pair), "odds": round(o, 1), "label": lbl})
    # [배당구간별 자동배분] 1+2위 / 1+3위 / 1+4위 고정 비율(합 100%).
    #   저배당(<3배): 70/20/10 · 중배당(3~7배): 50/30/20 · 고배당(≥7배): 40/35/25.
    if ab_odds < 3:
        band, ratios = "저배당", [0.70, 0.20, 0.10]
    elif ab_odds < 7:
        band, ratios = "중배당", [0.50, 0.30, 0.20]
    else:
        band, ratios = "고배당", [0.40, 0.35, 0.25]
    # 3두(2조합)면 앞 2개 비율만 쓰이므로 합이 100% 미만 → 조합 수만큼 재정규화.
    rr = ratios[:len(combos)]
    rsum = sum(rr) or 1.0
    rr = [round(x / rsum, 3) for x in rr]
    for c, r in zip(combos, rr):
        c["ratio"] = r
        c["payoutRatio"] = round(r * c["odds"], 3)
        # [원금보전 가능 여부] 이 조합만 적중해도 총원금 이상 회수되면 True(✅), 아니면 손실(❌).
        c["preserved"] = bool(c["payoutRatio"] >= 1.0)
    payouts = [c["payoutRatio"] for c in combos if c.get("payoutRatio") is not None]
    # [보완#2 BUG A] 중간값 = 정확한 중앙값. 조합 2개(짝수)일 때 sorted[n//2]는 최댓값과 같아져
    #   '최선==중간'으로 중복 표기되던 버그 → 짝수 개수면 가운데 두 값의 평균으로 산출.
    def _median(vals):
        s = sorted(vals); n = len(s)
        if not n:
            return None
        return s[n // 2] if n % 2 else round((s[n // 2 - 1] + s[n // 2]) / 2, 3)
    res.update({
        "band": band, "combos": combos,
        "bestRatio": max(payouts) if payouts else None,
        "midRatio": _median(payouts),
        "worstRatio": min(payouts) if payouts else None,
        # [평균 시나리오] 커버 조합 중 하나가 적중(균등 가정) 시 평균 회수 비율.
        "avgRatio": round(sum(payouts) / len(payouts), 3) if payouts else None,
        # [보완#2 BUG B] 실제 최악 = 커버 조합 전부 미적중 = 전액 손실(-100%).
        #   worstRatio(적중 조합 중 최소 회수)를 '최악'으로 오인 표기하던 것을 프론트에서 구분하도록
        #   allMissRatio(=0, 전액손실) 필드를 명시 제공한다.
        "allMissRatio": 0.0 if combos else None,
        "preserved": bool(payouts and all(p >= 1.0 for p in payouts)),   # 모든 조합 적중 시 원금 이상
        "expectedReturn": _expected_return(combos, curQ)[0] if combos else None,
    })
    return res


def _bmed_strategy(curQ, key_horses, excess, inverse, mass_drop, signal_confidence, after_close, sport="horse"):
    """[1·4번] 현재 상황 자동 분석 → BMED 5전략 중 최적 선택 + 근거 + 원금보전 배분 + 기대환수율.
    5전략: 보험형(이상감지 없음+유력마 명확)·압축형(2두 강한신호)·역배열형(쌍승역전)
          ·분산형(대규모 급락 노이즈)·고배당도전형(강한 신호+고배당).
    + 보험용 매트릭스(_bmed_insurance)를 함께 산출 → 정상 추천과 나란히 제시(조건 충족 시).
    [탭분리] sport in {cycle,boat,bike} = 6명 출전 종목 → BMED 저배당(원금보전) 집중을 기본 권장."""
    if not curQ:
        return None
    six_racer = sport in ("cycle", "boat", "bike")   # [탭분리] 6명 출전 종목
    strong = list(signal_confidence.get("strong") or [])
    concentrated = list((excess or {}).get("concentrated") or [])
    inv_det = bool(inverse and inverse.get("detected"))
    inv_rev = inv_det and any(t.get("kind") == "쌍승역전" for t in (inverse.get("types") or []))

    def _co(pairs):
        out, seen = [], set()
        for p in pairs:
            pp = tuple(sorted(int(x) for x in p))
            if len(pp) == 2 and pp not in seen and curQ.get(pp) and curQ[pp] > 0:
                seen.add(pp)
                out.append({"combo": list(pp), "odds": curQ[pp]})
        return out

    def _pairs(hs):
        hs = list(dict.fromkeys(hs))
        return [(hs[i], hs[j]) for i in range(len(hs)) for j in range(i + 1, len(hs))]
    cheapest = [list(k) for k, _ in sorted(curQ.items(), key=lambda kv: kv[1])]
    # 강한 신호 말 조합(2두+) + 고배당 여부
    strong_pairs = _pairs(strong[:3]) if len(strong) >= 2 else \
        ([(strong[0], h) for h in key_horses if h != strong[0]] if strong else [])
    strong_combos = _co(strong_pairs)
    strong_hi = [c for c in strong_combos if c["odds"] >= 20]

    if mass_drop:
        name, emoji = "분산형", "🌊"
        reason = "대규모 급락(자금 분산·노이즈) → 특정 유력마 불명확, 넓게 원금 보전"
        combos = _co([tuple(c) for c in cheapest[:6]])
    elif inv_rev:
        name, emoji = "역배열형", "🔄"
        ih = inverse.get("invHorses") or []
        reason = f"쌍승 역전 감지 → 실질 유력마({'·'.join(map(str, ih[:2])) or '-'}번) 역배열 조합 우선"
        combos = _co([tuple(c["combo"]) for c in (inverse.get("invCombos") or [])]) or _co([tuple(cheapest[0])])
    elif strong_hi:
        name, emoji = "고배당도전형", "🚀"
        reason = f"강한 신호 + 고배당 조합({strong_hi[0]['combo'][0]}+{strong_hi[0]['combo'][1]}={strong_hi[0]['odds']}배) → 상방 도전"
        combos = strong_hi + _co([tuple(cheapest[0])])   # 고배당 도전 + 최저 1개 안전판
    elif len(strong) >= 2 or len(concentrated) >= 2:
        name, emoji = "압축형", "🎯"
        base = strong[:2] if len(strong) >= 2 else concentrated[:2]
        reason = f"2두 강한 신호({'·'.join(map(str, base))}번) → 압축 집중 베팅"
        combos = _co(_pairs(base + key_horses[:1]))
    else:
        name, emoji = "보험형", "🛡️"
        reason = "유력마 명확 + 이상감지 없음 → 상위 조합 원금 보전 안정 베팅"
        combos = _co([tuple(c) for c in cheapest[:4]])

    plan, preserved, return_rate = _capital_preservation(combos)
    expected, best_ratio, covered = _expected_return(plan, curQ)
    insurance = _bmed_insurance(key_horses, curQ, signal_confidence, inverse, sport=sport)
    # [탭분리] 6명 출전 종목(경정·경륜·바이크)은 저배당 원금보전 집중이 기본 → 근거·안내에 명시.
    if six_racer:
        reason = f"6명 출전 종목 · 저배당(원금보전) 집중 기본 — {reason}"
    return {
        "strategy": name, "emoji": emoji, "label": f"BMED {name}", "reason": reason,
        "afterClose": bool(after_close),
        "sixRacer": six_racer,               # [탭분리] 6명 출전 종목(경정·경륜·바이크)
        "plan": plan, "preserved": preserved,
        "returnRate": return_rate,          # 보장 환수율%(모든 적중 동일/최소)
        "expectedReturn": expected,          # 기대 환수율%(시장확률 가중)
        "bestCaseRatio": best_ratio,         # 최선 수령 비율(×예산) — 최선 시나리오
        "worstCaseRatio": -1.0,              # 최악(커버 조합 모두 미적중) = 예산 전액 손실
        "coveredProb": covered,              # 커버 조합 시장 적중확률 합%
        "note": ("6명 출전 · 저배당 집중 · 예산 입력 시 조합별 자동 계산" if six_racer and plan
                 else "예산 입력 시 각 조합 베팅액·수령액 자동 계산") if plan else "복승 배당 부족 — 배분 계산 불가",
        # [보험용 추천] 정상 추천과 함께 제시(조건 충족 시만 active) → 사용자가 보고 선택
        "insurance": insurance,
    }


# ───────── [이상감지 vs 추천 비교 학습] 3종 추천 조합 산출 + 가중치 자동 조정 ─────────
def _compare_recommend(form, key_horses, excess, drops, bet_rec):
    """[1번] 이상감지 기반 / 전적 기반 / 최종 추천 조합을 각각 산출(복승 2두·삼복승 3두).
    - 이상감지 기반: 집중급락(초과) 상위 → 급락 조합 등장 말 → 배당 인기(key_horses)
    - 전적 기반: 전적 총점 상위 / 최종: betRecommend 복승·삼복승 메인(블렌드)."""
    def pt(order):
        order = [int(x) for x in order]
        return {"quinella": sorted(order[:2]) if len(order) >= 2 else None,
                "trio": sorted(order[:3]) if len(order) >= 3 else None}
    form_order = [h.get("no") for h in sorted(form or [], key=lambda x: -(x.get("totalScore") or 0))
                  if h.get("no") is not None]
    anom_order = [int(x) for x in ((excess or {}).get("concentrated") or [])]
    for d in (drops or []):
        for h in d.get("combo", []):
            if int(h) not in anom_order:
                anom_order.append(int(h))
    for h in (key_horses or []):
        if int(h) not in anom_order:
            anom_order.append(int(h))
    fq = next((sorted(b["combo"]) for b in (bet_rec or []) if b.get("label") == "복승 메인"), None)
    ft = next((sorted(b["combo"]) for b in (bet_rec or []) if b.get("label") == "삼복승 메인"), None)
    # [기수 근거] 기수 복승률 상위 순 조합(전적·배당과 독립된 3번째 근거)
    jk_order = [h.get("no") for h in sorted(
        (form or []),
        key=lambda x: -((_jockey_place_rate(x.get("jockey")) or 0)))
        if h.get("no") is not None and _jockey_place_rate(h.get("jockey")) is not None]
    return {"anomaly": pt(anom_order),
            "form": (pt(form_order) if len(form_order) >= 2 else {"quinella": None, "trio": None}),
            "jockey": (pt(jk_order) if len(jk_order) >= 2 else {"quinella": None, "trio": None}),
            "final": {"quinella": fq, "trio": ft}}


def _recommend_basis(top_horses, form, elimination, drops, wx_reversals, advanced, signal_confidence, basis_weights=None):
    """[추천 근거 상세 카드] 상위 추천마(최대 3두)별 근거: 전적·배당·기수·종합확신도.
    조립 가능한 데이터만 채우고, 미수집(당거리·주로상태·날씨별 성적·기수-말 조합성적)은 명시(missing).
    반환 [{rank,no,name, form{}, odds{}, jockey{}, confidence{}}]. basis_weights=근거별 신뢰 가중치(있으면 첨부)."""
    form_map = {int(h["no"]): h for h in (form or []) if h.get("no") is not None}
    elim_map = {int(h["no"]): h for h in ((elimination or {}).get("horses") or []) if h.get("no") is not None}
    streaks = (advanced or {}).get("horseStreaks") or {}
    sconf = (signal_confidence or {}).get("horses") or {}
    rev_by = {}
    for r in (wx_reversals or []):
        rev_by.setdefault(int(r["challenger"]), r)

    def _g(m, no):
        return m.get(no) or m.get(str(no)) or {}
    cards = []
    for i, no in enumerate([int(x) for x in (top_horses or [])][:3]):
        f, e = form_map.get(no) or {}, elim_map.get(no) or {}
        # ① 전적 근거 (당거리·주로상태·날씨별은 데이터 미수집)
        recent = (f.get("recentPlacings") or f.get("recent") or [])[:5]
        form_basis = {"score": f.get("totalScore") if f.get("totalScore") is not None else e.get("formScore"),
                      "recent": recent, "avgPlacing": e.get("avgPlacing"),
                      "missing": ["당거리 성적", "주로상태별 성적", "날씨별 성적"]}
        # ② 배당 근거 (급락·쌍승역전·연속하락·변화시점 시퀀스)
        my_drops = sorted([d for d in (drops or []) if no in (d.get("combo") or []) and (d.get("pct") or 0) < 0],
                          key=lambda d: d["pct"])
        st = _g(streaks, no)
        rv = rev_by.get(no)
        odds_basis = {"drop": (my_drops[0]["pct"] if my_drops else None), "dropCount": len(my_drops),
                      "reversal": ({"favorite": rv["favorite"], "ratio": rv["ratio"]} if rv else None),
                      "streak": (st.get("count") or 0), "streakLabel": st.get("label"),
                      "series": st.get("series")}
        # ③ 기수 근거 (복승률 O · 이 말과 조합성적은 미수집)
        jockey_basis = {"name": f.get("jockey") or e.get("jockey"), "placeRate": e.get("jockeyPlaceRate"),
                        "comboNote": None, "missing": ["이 말과 조합 성적"]}
        # ④ 종합 확신도 + 왜 이 점수인지
        sc = _g(sconf, no)
        reasons = []
        if sc.get("excessScore"):
            reasons.append(f"초과급락 {sc['excessScore']}점")
        if sc.get("reversalScore"):
            reasons.append(f"쌍승역전 {sc['reversalScore']}점")
        if sc.get("mismatchScore"):
            reasons.append(f"복승불일치 {sc['mismatchScore']}점")
        if e.get("formScore") is not None:
            reasons.append(f"전적 {e['formScore']}점")
        if e.get("jockeyPlaceRate") is not None:
            reasons.append(f"기수복승률 {e['jockeyPlaceRate']}%")
        conf = {"score": (sc.get("confidence") if sc.get("confidence") is not None else e.get("confidence")),
                "grade": sc.get("grade"), "combinedProb": e.get("combinedProb"),
                "reasons": reasons or ["뚜렷한 이상감지 신호 없음(전적·배당 인기 기준)"]}
        cards.append({"rank": i + 1, "no": no, "name": f.get("name") or e.get("name") or "",
                      "form": form_basis, "odds": odds_basis, "jockey": jockey_basis, "confidence": conf})
    return {"cards": cards, "basisWeights": basis_weights or {},
            "dataNote": "당거리·주로상태·날씨별 성적, 기수-말 조합성적은 현재 미수집(수집 연동 시 자동 반영)"}


def _learned_integrated_weights():
    """[배당 우선 전환] 50경주+ 누적 시 이상감지/전적 적중률 비교로 통합 가중치 자동 조정.
    기본 전적0.3 + 이상감지(배당)0.7(배당 우선). 데이터 부족(각 50경주 미만) 시 기본값 유지.
    이상감지 적중률>전적이면 이상감지 비중↑, 반대면 전적 비중↑(±15%p, 이상감지 0.55~0.85)."""
    fw, ow = 0.3, 0.7
    try:
        cs = (_learning_load().get("stats", {}) or {}).get("compare_stats") or {}
        a, f = cs.get("anomaly") or {}, cs.get("form") or {}
        if (a.get("n") or 0) >= 50 and (f.get("n") or 0) >= 50 \
                and a.get("rate") is not None and f.get("rate") is not None:
            shift = max(-0.15, min(0.15, (a["rate"] - f["rate"]) / 100.0))
            ow = round(max(0.55, min(0.85, 0.7 + shift)), 3)
            fw = round(1 - ow, 3)
    except Exception:
        fw, ow = 0.3, 0.7
    return fw, ow


# ════════════ [추천 로직 전면 개편] 확신도 엔진 + 경주유형 + 단계별 추천 + 강화제거 ════════════
#   ⚠ 기존 함수(_signal_confidence·_elimination·_bmed_strategy·_integrated_grades 등)는 그대로 두고,
#   그 결과를 '소비'해서 새 지표만 파생한다(삭제·수정 없음). _triple_analyze 반환 dict에 필드만 추가.

def _drop_persistence(advanced, no):
    """[2번] 시세급락지속성(0~100). 연속하락 확정=높음, 페이크(반등)=낮음.
      기존 _advanced_anomaly의 horseStreaks[no]{count,rebounded}를 재사용."""
    hs = (advanced or {}).get("horseStreaks") or {}
    s = hs.get(no)
    if s is None:
        s = hs.get(int(no)) if str(no).lstrip("-").isdigit() else None
    if s is None:
        s = hs.get(str(no))
    if not s:
        return 0.0
    if s.get("rebounded"):
        return 15.0                       # 반등=페이크 → 지속성 매우 낮음
    cnt = int(s.get("count") or 0)
    return float(min(100.0, {0: 0, 1: 40, 2: 70}.get(cnt, 90 + min(10, (cnt - 3) * 3))))


def _confidence_engine(signal_confidence, form, advanced, key_horses):
    """[2번] BMED 확신도 = 이상감지강도(40%) + 전적점수(30%) + 시세급락지속성(30%).
      기존 '저배당 우선'이 아니라 신호 기반 확신도로 유력마를 재랭킹.
      반환 {horses:{no:{anomaly,form,persistence,confidence,band}}, ranked:[no], top:[no],
            overall:{best,band,bestHorse}}. band=강력(≥65)/주목(≥45)/관찰(≥25)/약함."""
    fmap = {int(h["no"]): h for h in (form or []) if h.get("no") is not None}
    sc_h = (signal_confidence or {}).get("horses") or {}
    hs = (advanced or {}).get("horseStreaks") or {}
    nos = set(fmap.keys())
    nos |= set(int(n) for n in sc_h.keys())
    nos |= set(int(n) for n in (key_horses or []))
    for k in hs.keys():
        try:
            nos.add(int(k))
        except (ValueError, TypeError):
            pass
    # [보완] 전적 미수집(지방 다수) 경주는 전적 가중치(30%)가 죽어 확신도가 최대 70에 갇혀
    #   확실형(80+)·전적60+ 조건이 무력화됨 → 전적 데이터가 아예 없으면 그 30%를
    #   이상감지·급락지속에 비례 재분배(0.40:0.30 → 0.57:0.43)해 신호만으로도 확신도 산출.
    has_form = any(h.get("totalScore") is not None for h in (form or []))
    if has_form:
        w_a, w_f, w_p = 0.40, 0.30, 0.30
    else:
        w_a, w_f, w_p = 0.57, 0.0, 0.43
    out = {}
    for no in nos:
        anom = float((sc_h.get(no) or sc_h.get(str(no)) or {}).get("confidence") or 0.0)   # 이상감지강도 0~100
        fh = fmap.get(no)
        fscore = max(0.0, min(100.0, float((fh or {}).get("totalScore") or 0.0))) if fh else 0.0
        pers = _drop_persistence(advanced, no)
        conf = round(w_a * anom + w_f * fscore + w_p * pers, 1)
        band = "강력" if conf >= 65 else ("주목" if conf >= 45 else ("관찰" if conf >= 25 else "약함"))
        out[no] = {"no": no, "anomaly": round(anom, 1), "form": round(fscore, 1),
                   "persistence": round(pers, 1), "confidence": conf, "band": band}
    ranked = sorted(out.keys(), key=lambda n: -out[n]["confidence"])
    best = out[ranked[0]]["confidence"] if ranked else 0.0
    ov_band = "강력" if best >= 65 else ("주목" if best >= 45 else ("관찰" if best >= 25 else "약함"))
    return {"horses": out, "ranked": ranked, "top": ranked[:5], "formAvailable": has_form,
            "weights": {"anomaly": w_a, "form": w_f, "persistence": w_p},
            "overall": {"best": best, "band": ov_band, "bestHorse": ranked[0] if ranked else None}}


def _bet_judgment(conf_engine, excess, advanced, wx_reversals, form, mass_drop, after_close, signal_ready):
    """[개편·실전] 경주 유형 자동 판정 + 배팅 배분 비율.
      유형: 확실형/신중형/애매형/패스형/wait(수집중). 조건(초과급락·연속하락·쌍승역전·전적·확신도)
      으로 분류하고 예산 배분(복승메인/보조/삼복승보험)을 제공. 저배당 무조건 추천 방지([5번]).
      반환 {type,emoji,label,message,confidence,reasons[],metrics{},alloc{main,sub,trio},exactaSignal}."""
    best = round(((conf_engine or {}).get("overall") or {}).get("best", 0.0), 1)
    ex_h = (excess or {}).get("horses") or {}
    mags = [-v["strength"] for v in ex_h.values()
            if isinstance(v.get("strength"), (int, float)) and v["strength"] < 0]
    max_ex = round(max(mags), 1) if mags else 0.0                      # 최강 초과급락 크기(%p, +값)
    streaks = list(((advanced or {}).get("horseStreaks") or {}).values())
    max_streak = max([int(s.get("count") or 0) for s in streaks], default=0)   # 최다 연속하락
    fake_any = any(s.get("rebounded") for s in streaks)
    ratios = [r["ratio"] for r in (wx_reversals or []) if r.get("ratio") is not None]
    min_ratio = min(ratios) if ratios else None                       # 최강 쌍승역전(낮을수록 강)
    fscores = [h["totalScore"] for h in (form or []) if h.get("totalScore") is not None]
    max_form = round(max(fscores), 1) if fscores else 0.0
    has_form = len(fscores) > 0        # 전적 미수집이면 확실형 전적조건 완화(확신도로 대체)
    mass = bool(mass_drop)

    reasons = []
    if max_ex >= 5:
        reasons.append(f"초과급락 {max_ex}%p")
    if max_streak >= 1:
        reasons.append(f"연속하락 {max_streak}회")
    if min_ratio is not None:
        reasons.append(f"쌍승역전 {min_ratio}")
    if max_form >= 1:
        reasons.append(f"전적 {max_form}")
    if fake_any:
        reasons.append("페이크 반등 의심")
    if mass:
        reasons.append("대규모 급락(노이즈 주의)")

    strong = (max_ex >= 15 and max_streak >= 3 and (min_ratio is not None and min_ratio < 0.80) and (max_form >= 60 or not has_form))
    moderate = ((5 <= max_ex < 15) or max_streak == 2 or (min_ratio is not None and 0.80 <= min_ratio <= 0.95))

    # [1번·추천 신중화] 시장 신호 4종(전적 제외) 중 2개+ 확인 시에만 추천.
    #   ①초과급락 절대10%+ ②쌍승역전 ③연속하락 2회+ ④환수율 이상(top3 90%+).  전적은 시장 신호가 아니므로 카운트 제외.
    sig_excess = any(v.get("absStrong") for v in ex_h.values())
    sig_reversal = min_ratio is not None
    sig_streak = max_streak >= 2
    _ov = (advanced or {}).get("overround") or {}
    sig_overround = bool(_ov.get("concentrated"))
    market_signal_count = int(sig_excess) + int(sig_reversal) + int(sig_streak) + int(sig_overround)

    if not signal_ready and not after_close:
        typ, emoji, label = "wait", "⏳", "신호 대기"
        msg = "배당 수집 중 — 신호 형성 후 추천(저배당 무조건 추천 안 함)"
        alloc = {"main": 0, "sub": 0, "trio": 0}
    elif market_signal_count < 2 and not after_close:
        # [1번] 시장 신호 4종 중 2개 미만 → 추천 보류(전적만 좋아도 추천 안 함)
        typ, emoji, label = "wait", "⏳", "신호 대기"
        msg = f"아직 뚜렷한 신호 없음 — 시장 신호 {market_signal_count}/2 (급락10%+·쌍승역전·연속하락2회+·환수율이상 중 2개+ 확인 후 추천)"
        alloc = {"main": 0, "sub": 0, "trio": 0}
    elif strong or best >= 80:
        typ, emoji, label = "확실형", "✅", "확실형 — 자신있게 배팅"
        msg = "✅ 이번 경주 자신있음"
        alloc = {"main": 60, "sub": 30, "trio": 10}
    elif moderate and best >= 40:
        typ, emoji, label = "신중형", "⚠️", "신중형 — 소액 추천"
        msg = "⚠️ 신중하게 소액만 (삼복승 보험 상향)"
        alloc = {"main": 40, "sub": 20, "trio": 40}
    elif (max_ex < 5 and max_streak < 1 and not ratios and best < 20) and not after_close:
        typ, emoji, label = "패스형", "⛔", "패스 권고 — 이번 경주 보류"
        msg = "⛔ 이번 경주 패스 권고 · 신호 없음 — 돈 아끼세요"
        alloc = {"main": 0, "sub": 0, "trio": 0}
    else:
        typ, emoji, label = "애매형", "🛡", "애매형 — 보험 중심 소액"
        msg = "🛡 애매함 — 보험(삼복승) 중심 소액 · 고배당 조합 위주"
        alloc = {"main": 20, "sub": 0, "trio": 80}

    # [3번] 쌍승 강신호: 역전<0.70 + 초과급락15+ + 연속3+ + 확신도80+ 모두 충족 시에만
    exacta_signal = None
    if min_ratio is not None and min_ratio < 0.70 and max_ex >= 15 and max_streak >= 3 and best >= 80:
        r0 = min((r for r in (wx_reversals or []) if r.get("ratio") == min_ratio),
                 key=lambda r: r["ratio"], default=None)
        if r0:
            a, b = int(r0["challenger"]), int(r0["favorite"])
            exacta_signal = {"combo": [a, b], "ratio": min_ratio,
                             "text": f"⚡ 쌍승 강신호! {a}→{b} — 역전+급락 동시 · 소액 도전"}

    return {"type": typ, "emoji": emoji, "label": label, "message": msg,
            "confidence": best, "reasons": reasons,
            "metrics": {"maxExcess": max_ex, "maxStreak": max_streak, "minRatio": min_ratio,
                        "maxForm": max_form, "fake": fake_any, "mass": mass,
                        "signalCount": market_signal_count},
            "alloc": alloc, "exactaSignal": exacta_signal}


def _stage_guide(cur_mb, after_close, judgment, inverse, advanced):
    """[3번] 발주 전 시점(T-3/T-2/T-1/T-30초)별 단계 추천 + 최종 등급.
      cur_mb=발주까지 분. 등급은 경주유형(_bet_judgment)에서 유도. 반환 {stage,phase,title,grade,lines[],minutesBefore}."""
    typ = (judgment or {}).get("type")
    grade = {"확실형": "강력추천", "신중형": "일반추천", "애매형": "보험",
             "패스형": "패스", "wait": "대기"}.get(typ, "일반추천")
    if after_close or cur_mb is None:
        return {"stage": "closed", "phase": "마감 후", "title": "발주 후 · 참고만", "grade": grade,
                "minutesBefore": cur_mb, "lines": ["발주(T-0) 이후 신호는 참고만 하세요(추천 미반영)."]}
    if cur_mb >= 2.5:
        return {"stage": "t3", "phase": "T-3분", "title": "1차 추천 (전적40+배당60)", "grade": grade,
                "minutesBefore": cur_mb, "lines": ["전적 40% + 배당 60% 가중 1차 조합", "급락 말 강조 · 제거 말 표시"]}
    if cur_mb >= 1.5:
        fakes = [s.get("no") for s in ((advanced or {}).get("horseStreaks") or {}).values() if s.get("rebounded")]
        return {"stage": "t2", "phase": "T-2분", "title": "2차 업데이트 (급락 지속·페이크)", "grade": grade,
                "minutesBefore": cur_mb,
                "lines": ["급락 지속 여부 확인",
                          ("페이크 반등 제거: " + ", ".join(f"{n}번" for n in fakes)) if fakes else "페이크 반등 없음",
                          "역배열 최종 확인" + (" · 감지됨" if inverse and inverse.get("detected") else "")]}
    if cur_mb >= 0.5:
        return {"stage": "t1", "phase": "T-1분", "title": "3차 업데이트 (환수율·자금집중·확정)", "grade": grade,
                "minutesBefore": cur_mb, "lines": ["환수율·자금집중도 확인", "최종 조합 확정", "배팅 금액 배분"]}
    _fin = {"강력추천": "💪 강력추천 — 확신도 최상", "일반추천": "👍 일반추천 — 확신도 양호",
            "보험": "🛡️ 보험 위주 — 소액 분산", "패스": "⚠️ 신호 약함 — 이번 경주 패스 권고"}
    return {"stage": "t30", "phase": "T-30초", "title": "최종 알림", "grade": grade,
            "minutesBefore": cur_mb, "lines": [_fin.get(grade, "")]}


def _elimination_strong(elimination, form, drops):
    """[5번] 과감한 제거마 목록(기존 _elimination 판정 유지, 강화 파생만).
      대상=제거판정(🔴확실/🟠권장) + 근거 표기: ①전적 하위30% ②급락·변동 없음."""
    horses = (elimination or {}).get("horses") or []
    if not horses:
        return []
    fmap = {int(h["no"]): h for h in (form or []) if h.get("no") is not None}
    fscores = sorted([h["totalScore"] for h in (form or []) if h.get("totalScore") is not None])
    p30 = fscores[max(0, int(len(fscores) * 0.3) - 1)] if fscores else None
    dropped = set()
    for d in (drops or []):
        if d.get("pct") is not None and d["pct"] <= -15:
            for p in d.get("combo", []):
                dropped.add(int(p))
    out = []
    for h in horses:
        try:
            no = int(h["no"])
        except (KeyError, ValueError, TypeError):
            continue
        if h.get("verdict") not in ("🔴", "🟠"):      # 이미 제거권장/확실제거만 강화 표기
            continue
        reasons = []
        fh = fmap.get(no)
        fs = (fh or {}).get("totalScore")
        if fs is not None and p30 is not None and fs <= p30:
            reasons.append("전적 하위30%")
        if no not in dropped:
            reasons.append("급락·변동 없음")
        out.append({"no": no, "name": (fh or {}).get("name") or h.get("name") or "",
                    "odds": h.get("oddsRepr"), "formScore": fs, "verdict": h.get("verdict"),
                    "total": h.get("total"), "reasons": reasons or ["배당+전적 복합 제거"]})
    out.sort(key=lambda x: (x["total"] if x.get("total") is not None else 999))
    return out[:6]


def _chaotic_race(curQ, curWin, key_horses, anomaly_horse, drops):
    """[혼전 경주] 상위 배당이 근접해 이변 가능성이 큰 경주를 감지하고 고배당 포함 삼복승 전략을 편성.
      감지: ①상위 3두 배당 차이 20% 미만  또는  ②저배당 3두 이상 비슷한 배당(최저 대비 25% 이내).
      전략: 복승 저배당 메인(30%) + 삼복승 저배당3두(10%) + 저배당2두+이상감지말(30%) + 저배당2두+고배당복병(30%).
      ⚠ 기존 추천(betRecommend)·판정 로직 무영향 — 별도 chaotic 필드로만 파생."""
    # 1) 말별 대표 배당(인기): 단승 우선, 없으면 그 말 포함 최저 복승으로 근사.
    horse_odds = {}
    if curWin:
        for no, o in curWin.items():
            if o and o > 0:
                horse_odds[int(no)] = float(o)
    if not horse_odds and curQ:
        for (a, b), o in curQ.items():
            if not o or o <= 0:
                continue
            for h in (a, b):
                h = int(h)
                if h not in horse_odds or o < horse_odds[h]:
                    horse_odds[h] = float(o)
    if len(horse_odds) < 3:
        return None
    ordered = sorted(horse_odds.items(), key=lambda kv: kv[1])   # [(no, odds)] 오름차순(낮을수록 인기)
    nums = [no for no, _ in ordered]
    od = [o for _, o in ordered]
    o1 = od[0]
    if o1 <= 0:
        return None
    # 2) 혼전 감지 조건
    spread3 = (od[2] - o1) / o1 if len(od) >= 3 else 999.0   # 상위 3두 배당 확산율
    cond_a = spread3 < 0.20
    near = [no for (no, o) in ordered if o / o1 <= 1.25]     # 최저 대비 25% 이내(저배당 근접군)
    cond_b = len(near) >= 3
    if not (cond_a or cond_b):
        return None
    reasons = []
    if cond_a:
        reasons.append(f"상위 3두 배당 근접({od[0]}·{od[1]}·{od[2]}, 차이 {round(spread3 * 100)}%)")
    if cond_b:
        reasons.append(f"저배당 {len(near)}두 비슷({'·'.join(str(n) for n in near[:5])})")

    low3 = nums[:3]
    low2 = nums[:2]
    an = int(anomaly_horse) if anomaly_horse is not None else None
    # 3) 고배당 복병: 급락(자금유입) 있는 말 중 최고배당(저배당3두·이상감지말 제외), 없으면 최고배당 아웃사이더.
    drop_horses = []
    for d in (drops or []):
        for h in d.get("combo", []):
            hh = int(h)
            if hh not in drop_horses:
                drop_horses.append(hh)
    excl = set(low3) | ({an} if an is not None else set())
    dark_cands = [h for h in drop_horses if h in horse_odds and h not in excl]
    if dark_cands:
        dark = max(dark_cands, key=lambda h: horse_odds[h])
    else:
        outs = [no for (no, _) in ordered if no not in excl]
        dark = outs[-1] if outs else None   # 최고배당 아웃사이더

    def _q(a, b):
        return curQ.get(tuple(sorted((int(a), int(b)))))

    def _trio_est(cc):
        """삼복승 실배당 미수집 → 구성 복승 3쌍 기하평균×2 추정. (odds, is_estimate)."""
        ps = [_q(cc[0], cc[1]), _q(cc[0], cc[2]), _q(cc[1], cc[2])]
        present = [p for p in ps if p and p > 0]
        if len(present) == 3:
            gm = (present[0] * present[1] * present[2]) ** (1.0 / 3.0)
            return round(gm * 2, 1), True
        return None, False

    def _mk_trio(label, cc, alloc, high_return):
        cc = sorted(set(int(x) for x in cc))
        if len(cc) != 3:
            return None
        ev, est = _trio_est(cc)
        pick = {"kind": "삼복승", "label": label, "combo": cc, "alloc": alloc,
                "expOdds": None if est else ev, "highReturn": high_return}
        if est:
            pick["expOddsEst"] = ev
        return pick

    picks = []
    # 복승 저배당 메인 (30% — 사용자 20~30% 범위)
    if len(low2) == 2:
        picks.append({"kind": "복승", "label": "저배당 메인", "combo": sorted(low2),
                      "alloc": 30, "expOdds": _q(low2[0], low2[1]), "highReturn": False})
    # 삼복승 저배당 3두 조합 (10%)
    if len(low3) == 3:
        p = _mk_trio("저배당 3두", low3, 10, False)
        if p:
            picks.append(p)
    # 삼복승 저배당 2두 + 이상감지말 (30% — 고배당 포함)
    if len(low2) == 2 and an is not None and an not in low2:
        p = _mk_trio("저배당 2두+이상감지말", low2 + [an], 30, True)
        if p:
            picks.append(p)
    # 삼복승 저배당 2두 + 고배당 복병 (30% — 고배당 포함)
    if len(low2) == 2 and dark is not None and dark not in low2 and dark != an:
        p = _mk_trio("저배당 2두+고배당 복병", low2 + [dark], 30, True)
        if p:
            picks.append(p)

    return {
        "detected": True,
        "conditions": {"top3Close": cond_a, "lowCluster": cond_b},
        "reason": " · ".join(reasons),
        "spread3Pct": round(spread3 * 100, 1) if spread3 < 900 else None,
        "low3": low3, "anomalyHorse": an, "darkHorse": dark,
        "horseOdds": {str(no): round(o, 1) for no, o in ordered[:8]},
        "picks": picks,
        "banner": "⚠️ 혼전 경주 감지 · 이변 가능성 있음 · 고배당 포함 삼복승 권장",
        "note": "고배당 포함 조합 비중을 높였습니다(이변 대비).",
    }


# ══════════ [배당 흐름 기반 제거 시스템] "들어올 말 찾기" → "안 들어올 말 제거" ══════════
#   철학: 배당 흐름(자금 유입/이탈)으로 말별 흐름 점수를 매겨, 흐름 없는 말은 저배당이어도 제거하고
#         흐름 좋은 말은 고배당이어도 추천에 편입한다. 기존 _elimination(배당+전적)과 별개의 파생 레이어(무삭제).
def _horse_rep_series(hist, no, n=6):
    """최근 n스냅샷의 말별 대표배당(단승 우선·없으면 최저 복승) 시퀀스."""
    s = []
    for h in (hist or [])[-n:]:
        o = None
        wv = (h.get("win") or {}).get(str(no))
        if wv not in (None, ""):
            try:
                o = float(wv)
            except (TypeError, ValueError):
                o = None
        if o is None:
            # ⚠ hist의 quinella는 소스별로 형식이 다름:
            #   triple_store(history)=리스트[{combo,odds}] · odds_history 스냅샷=딕셔너리{'1+4':1.4}.
            #   → 두 형식 모두 지원하는 _as_qmap 사용(list 전용 _odds_map_un·dict 전용 _parse_combo_map 혼용 방지).
            mm = _as_qmap(h.get("quinella"))
            cand = [v for k, v in mm.items() if no in k and v > 0]
            o = min(cand) if cand else None
        if o and o > 0:
            s.append(round(float(o), 1))
    return s


def _flow_scores(hist, valid_nos, strong_signals, advanced, curQ, curWin, inverse):
    """[3번] 말별 배당 흐름 점수: 상승-10·변동없음-5·하락+10·급락+20·스마트머니+30.
    [1번] 제거 플래그: 죽은인기(변동없음)·연속상승(3회+)·페이크(급락후 반등)·역배열반대.
    반환 {no:{score,trend,label,rep,dead,rising3,fake,revOpp,removeReasons[]}}."""
    streaks = (advanced or {}).get("horseStreaks") or {}
    smart = set()
    for _sg in ((strong_signals or {}).get("signals") or []):
        if _sg.get("type") == 8:                      # 상승후급락(스마트머니)
            for _h in (_sg.get("horses") or []):
                try:
                    smart.add(int(_h))
                except (TypeError, ValueError):
                    pass
    # [1번 역배열 반대] 역배열(쌍승역전)이 감지되면, 실제로는 밀려나는 '원인기마'(banner.reversal.favorite)를 제거 대상.
    #   ⚠ 오제거(실제 승자 제거) 방지 위해 보수적: (a)쌍승역전이 실제 detected일 때만, (b)challenger(실질 유력마)는 절대 제거 안 함.
    rev_opp = set()
    inv = inverse or {}
    if inv.get("detected"):
        _rev = (inv.get("banner") or {}).get("reversal") or {}
        _fav, _cha = _rev.get("favorite"), _rev.get("challenger")
        try:
            if _fav is not None and int(_fav) != int(_cha):
                rev_opp.add(int(_fav))   # 시장 인기이나 쌍승역전으로 실제 밀려나는 원인기마 → 제거
        except (TypeError, ValueError):
            pass
    def _rep(no):
        if curWin.get(no):
            return curWin[no]
        best = None
        for (a, b), o in curQ.items():
            if no in (a, b) and o > 0 and (best is None or o < best):
                best = o
        return best
    out = {}
    for no in sorted(valid_nos or []):
        s = _horse_rep_series(hist, no)
        rep = _rep(no)
        st = streaks.get(no) or streaks.get(str(no)) or {}
        fake = bool(st.get("rebounded"))
        cons_down = int(st.get("count") or 0)          # 연속 하락 횟수
        # 연속 상승 횟수(시퀀스 뒤에서부터 직전보다 높음)
        rising = 0
        for i in range(len(s) - 1, 0, -1):
            if s[i] > s[i - 1]:
                rising += 1
            else:
                break
        rising3 = rising >= 3
        change = ((s[-1] - s[0]) / s[0]) if len(s) >= 2 and s[0] > 0 else 0.0
        # 카테고리(강한 것 우선): 스마트머니 → 급락 → 하락 → 변동없음 → 상승
        dead = False
        if no in smart:
            score, trend = 30, "스마트머니"
        elif change <= -0.30 or cons_down >= 3:
            score, trend = 20, "급락"
        elif change <= -0.05 or cons_down >= 1:
            score, trend = 10, "하락"
        elif abs(change) <= 0.03:
            score, trend = -5, "변동없음"
            dead = (len(s) >= 3)                        # T-5~T-1 무변동 = 죽은 인기
        else:
            score, trend = -10, "상승"
        if fake:
            score -= 15                                 # 페이크 반등 = 감점
        reasons = []
        if dead:
            reasons.append("죽은 인기(무변동)")
        if rising3:
            reasons.append("3회 연속 상승(자금 이탈)")
        if fake:
            reasons.append("급락 후 반등(페이크)")
        if no in rev_opp:
            reasons.append("역배열 반대 방향")
        out[int(no)] = {"no": int(no), "score": score, "trend": trend, "label": trend,
                        "rep": rep, "dead": dead, "rising3": rising3, "fake": fake,
                        "revOpp": (no in rev_opp), "removeReasons": reasons,
                        "smartMoney": (no in smart), "series": s}
    return out


def _flow_removal(flow, key_horses, keep_low_odds=3.0):
    """[1번] 흐름 기반 제거 대상: 죽은인기/3연속상승/페이크/역배열반대. 유력마 축(상위2)은 제외(안전).
      [3번·저배당 보조 유지] 흐름이 없어도 저배당(대표배당 keep_low_odds=3배 이하) 말은 제거하지 않고
      '보조'로 유지(시장 최상위권은 흐름 무관 실전력 있음 → 완전 제거 금지). 반환에 keptLowOdds 표시."""
    axis = set(int(h) for h in (key_horses or [])[:2] if h is not None)
    rem = []
    for no, f in flow.items():
        if no in axis:
            continue
        if f.get("removeReasons"):
            _rep = f.get("rep")
            if _rep is not None and _rep <= keep_low_odds:
                continue                                # [3번] 3배 이하 저배당 = 제거 대신 보조 유지(제거 목록서 제외)
            rem.append({"no": no, "rep": _rep, "trend": f.get("trend"),
                        "reasons": f["removeReasons"], "score": f.get("score")})
    rem.sort(key=lambda x: (x.get("score") or 0))       # 점수 낮은(제거 확실) 순
    return rem


def _high_odds_candidates(flow, removed_nos, key_horses, min_odds=10.0):
    """[2번] 제거 후 남은 말 중 배당 10배+ 인데 하락/급락(흐름 좋음) → 고배당 후보 자동 편입."""
    key = set(int(h) for h in (key_horses or []) if h is not None)
    out = []
    for no, f in flow.items():
        if no in removed_nos or no in key:
            continue
        rep = f.get("rep")
        if rep is not None and rep >= min_odds and f.get("trend") in ("급락", "하락", "스마트머니"):
            _sm_note = (" · 스마트머니" if (f.get("smartMoney") and f.get("trend") != "스마트머니") else "")
            out.append({"no": no, "odds": round(float(rep), 1), "trend": f.get("trend"),
                        "score": f.get("score"),
                        "note": "%g배 · %s%s → 삼복승 보험 후보" % (rep, f.get("trend"), _sm_note)})
    out.sort(key=lambda x: (-(x.get("score") or 0), x.get("odds") or 999))
    return out


def _mid_high_odds_favorites(valid_nos, curWin, curQ, drops, dark_horses, reversal_roles, form, min_odds=10.0):
    """[중고배당 유력마·1번] 복승 10배+ 인데 강한 신호 1개+ 보유 → 💎 중고배당 유력마.
      신호: 급락 -30%+ · 역배열 10%+ · 스마트머니 · 집중급락 10회+ · 전적 A/B등급(고배당).
      반환 [{no, odds, signals[{type,text}], sigTypes[], note}] (신호 많은 순)."""
    def _rep(no):
        if curWin.get(no):
            return curWin[no]
        best = None
        for (a, b), o in (curQ or {}).items():
            if no in (a, b) and o > 0 and (best is None or o < best):
                best = o
        return best
    # 신호원별 마번 집계
    drop_nos = {}
    for d in (drops or []):
        if (d.get("pct") or 0) <= -30:
            for h in (d.get("combo") or []):
                drop_nos[int(h)] = min(drop_nos.get(int(h), 0), d.get("pct") or 0)
    rev_nos = {int(r["no"]): (r.get("diffPct") or 0) for r in (reversal_roles or [])
               if r.get("no") is not None and (r.get("diffPct") or 0) >= 10}
    smart_nos = {int(h["no"]) for h in (dark_horses or []) if h.get("smartMoney") and h.get("no") is not None}
    conc_nos = {int(h["no"]): (h.get("anomCount") or 0) for h in (dark_horses or [])
                if h.get("forced") and h.get("no") is not None}
    grade_nos = {}
    for f in (form or []):
        g = f.get("grade") or f.get("tier")
        if g in ("A", "B") and f.get("no") is not None:
            grade_nos[int(f["no"])] = g
    out = []
    for no in sorted(int(x) for x in (valid_nos or [])):
        rep = _rep(no)
        if rep is None or rep < min_odds:
            continue                                   # 복승 10배 미만은 대상 아님
        sigs = []
        if no in drop_nos:
            sigs.append({"type": "급락", "text": "급락 %d%%" % round(drop_nos[no])})
        if no in rev_nos:
            sigs.append({"type": "역배열", "text": "역배열 %g%%" % rev_nos[no]})
        if no in smart_nos:
            sigs.append({"type": "스마트머니", "text": "스마트머니"})
        if no in conc_nos:
            sigs.append({"type": "집중급락", "text": "집중급락 %d회" % conc_nos[no]})
        if no in grade_nos:
            sigs.append({"type": "전적A/B", "text": "전적 %s등급(고배당)" % grade_nos[no]})
        if sigs:
            _stypes = [s["type"] for s in sigs]
            out.append({"no": no, "odds": round(float(rep), 1), "signals": sigs, "sigTypes": _stypes,
                        "note": "%g배 · %s → 삼복승 보험 필수" % (rep, "·".join(_stypes))})
    out.sort(key=lambda x: (-len(x["signals"]), x["odds"]))    # 신호 많은 순 → 저배당 순
    return out


def _cross_reversal(curQ, curD, fav_rank, valid_nos, max_rank=5):
    """[복승 크로스 역배열·전체 말 쌍 비교] 각 말 X에 대해 인기 상위쌍(A인기>B, 상위 max_rank위) 조합에서
      복승(A+X) > 복승(B+X) = X가 '덜 인기있는 말(B)'과 더 싸게 붙음 → X 실질 강세(그 말 1착 시 X 2착 유력).
      편차%(min 1.0 캡) × 가중치 합산 후 정규화. 가중치: 1·2위쌍 ×3 / 1·3·2·3위쌍 ×2 / 기타 ×1.
      쌍승(A→X vs B→X, X→A vs X→B)도 보조 가중(×0.5). 점수 0.3🟡/0.5🟠/0.7🔴.
      반환 [{no, score, level, refs:[상위쌍 마번]}] (0.3+ · 점수순)."""
    if not curQ or not fav_rank:
        return []
    top = [int(h) for h in fav_rank[:max_rank] if h is not None]
    rank = {h: i + 1 for i, h in enumerate(top)}

    def _w(ra, rb):
        if (ra, rb) == (1, 2):
            return 3
        if (ra, rb) in ((1, 3), (2, 3)):
            return 2
        return 1

    def _qo(a, b):
        return curQ.get(tuple(sorted((int(a), int(b)))))

    def _xo(a, b):                                     # 쌍승 방향(a→b)
        return (curD or {}).get((int(a), int(b)))
    out = []
    for X in sorted(int(x) for x in (valid_nos or [])):
        acc, wsum, ref_score = 0.0, 0.0, {}
        for i in range(len(top)):
            for j in range(i + 1, len(top)):
                A, B = top[i], top[j]                  # A 인기 높음(rank 작음)
                if X in (A, B):
                    continue
                w = _w(rank[A], rank[B])
                qa, qb = _qo(A, X), _qo(B, X)          # 복승: q(A+X) vs q(B+X)
                if qa and qb:
                    wsum += w
                    if qa > qb:
                        _c = min((qa - qb) / qb, 1.0) * w
                        acc += _c
                        ref_score[B] = ref_score.get(B, 0.0) + _c
                for (pa, pb) in (((A, X), (B, X)), ((X, A), (X, B))):   # 쌍승(보조 ×0.5)
                    xa, xb = _xo(*pa), _xo(*pb)
                    if xa and xb:
                        wsum += w * 0.5
                        if xa > xb:
                            _c = min((xa - xb) / xb, 1.0) * (w * 0.5)
                            acc += _c
                            ref_score[B] = ref_score.get(B, 0.0) + _c
        if wsum <= 0:
            continue
        score = round(acc / wsum, 2)
        if score >= 0.3:
            lvl = "🔴" if score >= 0.7 else ("🟠" if score >= 0.5 else "🟡")
            refs = [b for b, _ in sorted(ref_score.items(), key=lambda kv: -kv[1])][:3]   # 기여 큰 순
            out.append({"no": X, "score": score, "level": lvl, "refs": refs})
    out.sort(key=lambda x: -x["score"])
    return out


def _core_picks(key_horses, dark_horses, reversal_roles, drops, curWin, curQ, valid_nos):
    """[핵심 추천·추천 과다 근본 해결] 엄격 우선순위로 축 2마리만 선정 → 복승 X+Y · 삼복승 X+Y+Z(딱 이것만).
      우선순위: 1역배열+급락 동시 / 2스마트머니 / 3집중급락 최다 / 4유력마 저배당. 상위 2두=축, 3위=삼복승 3착.
      반환 {axis, third, quinella, trifecta, picks[{no,tier,reason,rep}]} 또는 None(축 2두 미확보 시)."""
    def _rep(no):
        if curWin.get(no):
            return curWin[no]
        best = None
        for (a, b), o in (curQ or {}).items():
            if no in (a, b) and o > 0 and (best is None or o < best):
                best = o
        return best
    rev = {int(r["no"]) for r in (reversal_roles or []) if r.get("no") is not None and (r.get("diffPct") or 0) >= 10}
    drop_nos = {}
    for d in (drops or []):
        if (d.get("pct") or 0) <= -30:
            for h in (d.get("combo") or []):
                drop_nos[int(h)] = min(drop_nos.get(int(h), 0), d.get("pct") or 0)
    smart = {int(h["no"]) for h in (dark_horses or []) if h.get("smartMoney") and h.get("no") is not None}
    conc = {int(h["no"]): (h.get("anomCount") or 0) for h in (dark_horses or []) if h.get("forced") and h.get("no") is not None}
    keyset = [int(k) for k in (key_horses or []) if k is not None]
    scored = []
    for no in sorted(int(x) for x in (valid_nos or [])):
        rep = _rep(no) or 999.0
        tier, reason = 0, ""
        if no in rev and no in drop_nos:
            tier, reason = 4, "역배열+급락"
        elif no in smart:
            tier, reason = 3, "스마트머니"
        elif no in conc:
            tier, reason = 2, "집중급락 %d회" % conc[no]
        elif no in keyset:
            tier, reason = 1, "유력마 저배당 %g배" % rep
        if tier:
            sub = conc.get(no, 0) if tier == 2 else -rep      # 3순위=집중급락 최다·그 외=저배당 우선
            scored.append({"no": no, "tier": tier, "sub": sub, "rep": rep, "reason": reason})
    scored.sort(key=lambda x: (-x["tier"], -x["sub"]))
    if len(scored) < 2:
        return None                                            # 축 2두 미확보 → 핵심추천 없음(기존 추천 유지)
    axis = [scored[0]["no"], scored[1]["no"]]
    third = scored[2]["no"] if len(scored) >= 3 else None
    return {"axis": axis, "third": third,
            "quinella": sorted(axis),
            "trifecta": (sorted(axis + [third]) if third is not None else None),
            "picks": scored[:3]}


def _confidence_picks(confidence, curWin, curQ, key_horses, single_rank, anomaly_horse, valid_nos):
    """[복승 축 = 시장 유력마 저배당 기준] 확신도 1위가 고배당(30배+)이면 복승 축으로 쓰지 않고 삼복승 보험으로만.
      복승 메인 축 우선순위: ①시장 유력마 상위 2두(저배당) ②급락/역배열 저배당 신호말 ③전적/확신도 저배당 말.
      ⚠ 고배당(30배+) 말은 복승 축 금지(엉뚱한 873배 복승 방지) → 삼복승 보험 전용.
      삼복승 메인=시장 유력마 3두 / 보험=고배당 확신도 말(또는 이상감지) + 유력마 2두.
      반환 {favAxis,top1,top1Conf,top1High,quinellas:[{combo,odds,reason}],forced,trifecta,trifectaIns} 또는 None.
      ⚠ 기존 _core_picks·betRecommend는 그대로 두고 별도 필드로만 부가(무삭제)."""
    HIGH = 30.0            # 고배당 기준(복승 축 금지)
    hmap = (confidence or {}).get("horses") or {}
    vset = set(int(v) for v in (valid_nos or []))
    ranked = [int(n) for n in ((confidence or {}).get("ranked") or []) if int(n) in vset]

    def _conf(no):
        return float((hmap.get(no) or hmap.get(str(no)) or {}).get("confidence") or 0.0)

    def _rep(no):        # 대표배당: 단승 우선, 없으면 최저 참여 복승(중앙 JRA=단승 미수집 대비)
        if curWin.get(no):
            return curWin[no]
        best = None
        for (a, b), o in (curQ or {}).items():
            if no in (a, b) and o > 0 and (best is None or o < best):
                best = o
        return best

    def _high(no):
        r = _rep(no)
        return (r is not None and r >= HIGH)

    def _q(a, b):
        if a is None or b is None or a == b:
            return None
        return (curQ or {}).get((min(a, b), max(a, b)))

    # 시장 유력마(저배당) 순위 — 계산된 유력마(key_horses) 우선, 나머지는 대표배당 최저순 보충.
    order = []
    for no in (key_horses or []):
        if int(no) in vset and int(no) not in order:
            order.append(int(no))
    order += sorted([no for no in vset if no not in order and _rep(no) is not None], key=lambda n: _rep(n))
    fav = [no for no in order if not _high(no)]          # 복승 축 후보 = 저배당만(고배당 제외)
    if len(fav) < 2:                                      # 저배당 2두 미확보 → 유력마 순서로 보충
        for no in order:
            if no not in fav:
                fav.append(no)
    if len(fav) < 2:
        return None
    fav1, fav2 = fav[0], fav[1]
    fav3 = fav[2] if len(fav) >= 3 else None

    top1 = ranked[0] if ranked else None
    top1c = _conf(top1) if top1 is not None else 0.0
    top1_high = _high(top1) if top1 is not None else False
    top2 = ranked[1] if len(ranked) >= 2 else None

    quinellas, seen = [], set()

    COMBO_HIGH = 50.0     # 복승 조합 자체가 고배당이면 축으로 부적합(단승 미수집 중앙 JRA 대비)
    def _add(a, b, reason):
        if a is None or b is None or a == b:
            return
        if _high(a) or _high(b):        # [핵심] 고배당(30배+) 말은 복승 축 금지
            return
        _o = _q(a, b)
        if _o is not None and _o >= COMBO_HIGH:   # [핵심2] 조합 배당이 고배당(50배+)이면 축 금지(873배 복승 방지)
            return
        key = (min(a, b), max(a, b))
        if key in seen:
            return
        seen.add(key)
        quinellas.append({"combo": [min(a, b), max(a, b)], "odds": _o, "reason": reason})

    # ① 복승 메인 = 시장 유력마 1위+2두(저배당) — 항상
    _add(fav1, fav2, "시장유력")
    # ② 확신도 1위가 저배당이면 확신도 조합도 추가(후쿠시마 6번 저배당 케이스 보존)
    if top1 is not None and not top1_high and _conf(top1) >= 25:
        if top2 is not None and not _high(top2):
            _add(top1, top2, "확신도1위+2위")
        _add(top1, fav1, "확신도1위+시장유력")
    # ③ 급락/역배열 저배당 신호말(이상감지)이 있으면 시장유력과 페어(2순위)
    if anomaly_horse is not None and not _high(int(anomaly_horse)) and int(anomaly_horse) not in (fav1, fav2):
        _add(fav1, int(anomaly_horse), "시장유력+급락")
    # ④ 확신도 70+ '저배당' 말은 복승 필수 포함(고배당은 제외 → 삼복승 보험으로)
    forced = [no for no in ranked if _conf(no) >= 70 and not _high(no)]
    covered = set()
    for qq in quinellas:
        covered.update(qq["combo"])
    for no in forced:
        if no not in covered:
            _add(fav1 if no != fav1 else fav2, no, "확신도70+ 필수포함")
            covered.add(no)
    if not quinellas:
        return None

    # ⑤ 삼복승 메인 = 시장 유력마 3두 / 보험 = 고배당 확신도 말(또는 이상감지) + 유력마 2두
    trifecta = sorted([fav1, fav2, fav3]) if fav3 is not None else None
    trifecta_ins = None
    if top1 is not None and top1_high and top1 not in (fav1, fav2):
        trifecta_ins = sorted([top1, fav1, fav2])                       # 고배당 확신도 말 → 유력마 2두와 보험
    elif top1 is not None and not top1_high and top2 is not None and anomaly_horse is not None:
        _cand = [top1, top2, int(anomaly_horse)]                        # 확신도 저배당: 1·2위+이상감지(후쿠시마 5+6+8)
        if len(set(_cand)) == 3 and not any(_high(x) for x in _cand):
            trifecta_ins = sorted(_cand)
    if trifecta_ins is None and anomaly_horse is not None and int(anomaly_horse) not in (fav1, fav2):
        trifecta_ins = sorted([fav1, fav2, int(anomaly_horse)])         # 폴백: 유력마 2두 + 이상감지

    return {"favAxis": [fav1, fav2], "top1": top1, "top1Conf": round(top1c, 1),
            "top1High": top1_high, "quinellas": quinellas[:3], "forced": forced,
            "trifecta": trifecta, "trifectaIns": trifecta_ins}


def _triple_analyze(rk, rec):
    quin = rec.get("quinella") or []
    exa = rec.get("exacta") or []
    trio = rec.get("trio") or []
    hist = rec.get("history") or []
    prev = hist[-2] if len(hist) >= 2 else None  # 직전 수집

    curQ = _odds_map_un(quin)
    prevQ = _odds_map_un(prev.get("quinella")) if prev else {}

    # [경주전환 방어] 직전 대비 다수 조합 95%+ 급락 = 다른 경주 배당 잔존 → 기준값 재설정(변동 계산 안 함)
    #   [실시간 분석 유지 버그수정] 확립된 baseline(5+스냅샷)은 단발 블립으로 '재설정' 표시하지 않음
    #   (분석이 초반으로 되돌아가는 현상 방지). 개별 95%+ 급락은 아래 필터가 이미 제외.
    baseline_reset = bool(prev and _baseline_reset_needed(prev.get("quinella"), quin) and len(hist) <= 4)
    # [첫수집 방어] 첫 비교(수집 2건뿐)는 첫 수집 배당이 불안정(못 가져옴/시장 형성 초기 고배당)해
    #   가짜 급락(-90%대)이 뜬다 → 1틱 워밍업: 2번째 수집을 기준으로만 두고 급락 계산 보류(3번째부터 계산).
    market_forming = (len(hist) == 2)
    baseline_set = (not prev) or market_forming   # 첫 수집/첫 비교 = 기준값 설정(변동 계산 안 함)
    if baseline_reset or market_forming:
        prev, prevQ = None, {}   # 오염/불안정 직전값 무효화 → 이 수집을 새 기준값으로

    # [단승] 현재/직전 단승 배당 + 급락 (가장 강한 신호)
    curWin = _win_map_int(rec.get("win"))
    prevWin = _win_map_int(prev.get("win")) if prev else {}
    single_drops = []
    for no, o in curWin.items():
        po = prevWin.get(no)
        if po and po > 0:
            pct = round((o - po) / po * 100, 1)
            if pct <= -95:   # [3번] 비정상 변동폭 → 제외(이전 경주 잔존 의심)
                continue
            if _is_opening_settle(po, pct):   # [초반미수집] opening 배당 정착(가짜 급락) 제외
                continue
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
            if pct <= -95:   # [3번] 비정상 변동폭(95%+ 급락) = 이전 경주 잔존 의심 → 제외
                continue
            if _is_opening_settle(po, pct):   # [초반미수집] opening 배당 정착(가짜 급락) 제외
                continue
            if abs(pct) >= 8:
                drops.append({"combo": list(k), "prev": po, "cur": o, "pct": pct})
    drops.sort(key=lambda d: d["pct"])
    # [대규모 급락] 전체 조합의 50%+ 또는 30개+ 동시 30%급락 → 자금 분산 패턴 감지
    mass_drop = _mass_drop_detect(drops, curQ)
    # [신호 품질] 초과 급락률(시장 평균 대비 집중도) + 상황별 가중치
    excess = _excess_drop_analysis(drops, curQ)
    situation = _signal_situation(drops, mass_drop, excess)
    # [1번] 마감 후(T-0 이후) 감지 여부 — 현재 스냅샷의 부호 포함 발주전분(mb_signed<0)
    cur_mb, after_close = None, False
    deadline_corrected = False   # [1번] 발주시각 오검출 정정 발생 여부(최근 스냅샷 기준)
    collection_stalled = False   # [수집 조기 중단 방어] 발주 전인데 마지막 수집이 2분+ 경과
    secs_since_collect = None
    try:
        _hp0, _, _ = _hist_path(rk)
        _hd0 = json.load(open(_hp0, encoding="utf-8"))
        if _hd0.get("snapshots"):
            _s0 = _hd0["snapshots"][-1]
            cur_mb = _s0.get("mb_signed")
            if cur_mb is None:
                cur_mb = _s0.get("minutes_before")
            after_close = bool(_s0.get("after_close")) or (cur_mb is not None and cur_mb < 0)
            # 최근 5스냅샷 내 오검출 정정이 있었으면 배너 표시용으로 노출
            deadline_corrected = any(_s.get("deadline_corrected") for _s in _hd0["snapshots"][-5:])
            # [수집 조기 중단 방어] 발주시각 감지됨(cur_mb>0) + 마지막 수집 2분+ 경과 + 마감 전 → 중단 감지
            _lt = _s0.get("t")
            if _lt:
                secs_since_collect = int(time.time() - _lt)
                if cur_mb is not None and cur_mb > 0 and secs_since_collect >= 120 and not after_close:
                    collection_stalled = True
    except Exception:
        cur_mb, after_close = None, False

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
    # [마감 직전 놓치지 않음] 직전(3초) 스냅샷뿐 아니라 보관된 '가장 이른 스냅샷'과도 방향을 비교 →
    #   여러 틱에 걸쳐 서서히 뒤집힌 역전(단발 flipped로는 안 잡히는)을 recentFlip 으로 포착.
    early = next((h for h in hist[:-1] if h.get("exacta")), None)   # 보관 창의 최이른 쌍승 스냅샷
    earlyD = _odds_map_dir(early.get("exacta")) if early else {}
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
                "favoredOdds": min(o, rev), "otherOdds": max(o, rev),
                "flipped": False, "recentFlip": False}
        if prev:
            pa, pb = prevD.get((a, b)), prevD.get((b, a))
            if pa is not None and pb is not None:
                prev_fav = [a, b] if pa <= pb else [b, a]
                info["flipped"] = (prev_fav != favored)
        if earlyD:
            ea, eb = earlyD.get((a, b)), earlyD.get((b, a))
            if ea is not None and eb is not None:
                early_fav = [a, b] if ea <= eb else [b, a]
                # 초기 대비 방향이 바뀌었고(누적 역전) 직전 단발 flipped 와 별개일 때만 표기
                info["recentFlip"] = (early_fav != favored)
        reversals.append(info)
    # flipped(단발) 또는 recentFlip(누적)인 역전을 우선 노출 → 마감 임박 역전이 [:10] 컷에 밀리지 않음
    reversals.sort(key=lambda r: (not (r["flipped"] or r.get("recentFlip")),
                                  -(r["otherOdds"] / max(r["favoredOdds"], 0.1))))
    reversals = reversals[:10]

    # [잔존마 필터·1번] 현재 수집된 배당(복승·쌍승·단승)에 실제 등장하는 마번 집합 = 실제 출전마.
    #   이 집합 밖 마번(전적 잔존마·이전 경주 말)은 유력마·전적·추천에서 자동 제외.
    # [마번 범위 검증] 종목별 최대 마번(경륜 9·경정 6·오토 8·경마 18) 초과 마번은 이전 경주(경마 11~18두 등)
    #   잔존·오검출로 간주해 배당에 있어도 제외 → "7명 경륜에 11번 추천" 방지.
    _max_no = _sport_max_no(rec.get("sport"))
    valid_nos = set()
    for _k in curQ:
        for _h in _k:
            try:
                _hi = int(_h)
                if 1 <= _hi <= _max_no:
                    valid_nos.add(_hi)
            except (TypeError, ValueError):
                pass
    for _k in curD:
        for _h in _k:
            try:
                _hi = int(_h)
                if 1 <= _hi <= _max_no:
                    valid_nos.add(_hi)
            except (TypeError, ValueError):
                pass
    for _n in curWin:
        try:
            _hi = int(_n)
            if 1 <= _hi <= _max_no:
                valid_nos.add(_hi)
        except (TypeError, ValueError):
            pass

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
    # [잔존마 필터·1번] 유력마도 배당에 등장하는 마번만(배당 없는 잔존마 제외)
    if valid_nos:
        ranked = [h for h in ranked if h in valid_nos]
    key_horses = ranked[:3]

    # [1·2·4번] 핵심 이상감지 공식: 쌍승역전·복승불일치·종합신뢰도
    #   단승 미수집(일본)이면 복승인기 순위(ranked)를 유력마 순위로 대체
    fav_rank = single_rank if single_rank else ranked
    wx_reversals = _win_exacta_reversal(fav_rank, curD)
    quin_mismatch = _quinella_mismatch(fav_rank, curQ)
    signal_confidence = _signal_confidence(excess, wx_reversals, quin_mismatch)
    # [역배열/추천게이트 공유] 전적 등급을 여기서 미리 계산 → 역배열 '전적 우수·시장 비인기' 판정에 재사용
    #   (기존엔 아래에서 계산했으나 앞당겨 재사용. 삭제 아님·계산 위치만 이동)
    form = _form_from_starters(rk, drops, rec.get("sport"), valid_nos)  # 출마표2/KRA/PDF/경륜 전적(종목 오매칭 차단 + 잔존마 필터)
    # [역배열 감지] 진짜 역배열 = 쌍승역전만 · 전적 우수하나 시장 비인기는 별도 분류(form 전달)
    inverse = _inverse_arrangement(fav_rank, bool(single_rank), curWin, curQ,
                                   wx_reversals, quin_mismatch, excess, form)
    # [역배열 정확화] 인기순위(단승/복승) vs 쌍승 배당순위 비교 → 2단계+ 역전 말만 팝업 상세로 교체.
    #   비교 가능(단승 또는 쌍승 데이터 존재)할 때만 rank 기반으로 덮어쓰고, 아니면 기존 복승기반 유지.
    try:
        _rid, _rlead, _rsrc = _rank_inversion_detail(curWin, curQ, curD)
        if _rsrc is not None:
            inverse["invDetail"] = _rid
            inverse["invLead"] = _rlead
            inverse["invSource"] = _rsrc
            inverse["detected"] = bool(inverse.get("detected") or _rid)
    except Exception as _e:
        print("[역배열 정확화] 실패:", _e)
    # [2번 고도화] 급락속도·연속하락/단발반등·페이크베팅·복승 환급률(역수합)
    advanced = _advanced_anomaly(hist, curQ, drops)
    # [강한 신호 8유형] 기존 advanced·inverse·drops·history 재사용 → 오버레이 강조·학습용 파생 뷰
    strong_signals = _strong_signals(advanced, inverse, drops, cur_mb, hist, curQ, after_close)

    # ═══ [복병_집중급락 패턴·즉시적용] 마에바시 8R 복기 학습 ═══
    #   ① 집중급락 10회+ 말 → 배당 순위 무관 복병 자동 편입(시장 중하위권이어도).
    #   ② 스마트머니(상승후급락 strong_signals Type 8) 감지 말 → 복병 신뢰도 '높음'.
    #   ⚠ 기존 복병(역배열/급락) 산출 무삭제 — 별도 darkHorses 리스트로 파생(프론트가 복병 섹션에 합류).
    dark_horses = []
    try:
        _hdoc = None
        try:
            _hpD, _, _ = _hist_path(rk)
            _hdoc = json.load(open(_hpD, encoding="utf-8"))
        except Exception:
            _hdoc = None
        _cc = _concentration_counts(_hdoc) if _hdoc else {}
        _sm = set()          # 스마트머니(상승후급락 Type 8) 말
        for _sg in ((strong_signals or {}).get("signals") or []):
            if _sg.get("type") == 8:
                for _h in (_sg.get("horses") or []):
                    try:
                        _sm.add(int(_h))
                    except (TypeError, ValueError):
                        pass

        def _rep_o_dark(h):
            if curWin.get(h):
                return curWin[h]
            best = None
            for (a, b), o in curQ.items():
                if h in (a, b) and o > 0 and (best is None or o < best):
                    best = o
            return best
        for _h, _cnt in sorted(_cc.items(), key=lambda kv: -kv[1]):
            if valid_nos and _h not in valid_nos:
                continue
            _sm_hit = _h in _sm
            _forced = (_cnt >= 10)                       # 집중급락 10회+ = 복병 무조건 편입
            if _forced or _sm_hit:
                _conf = "높음" if (_forced and _sm_hit) or _sm_hit else "중"
                dark_horses.append({
                    "no": _h, "anomCount": _cnt, "smartMoney": _sm_hit,
                    "oddsRepr": _rep_o_dark(_h), "forced": _forced, "confidence": _conf,
                    "note": ("🔥 집중급락 %d회" % _cnt) + (" · 💰 스마트머니(상승후급락)" if _sm_hit else ""),
                })
    except Exception as _de:
        print("[복병집중급락] 실패:", _de)

    # [5번] 유형별 학습 적중률을 각 신호에 부착(가중치 상향 근거) — 적중률 높은 유형 강조
    try:
        _sig_rates = _signal_type_hitrates()
        if _sig_rates and strong_signals.get("signals"):
            for _s in strong_signals["signals"]:
                _r = _sig_rates.get(_s.get("type"))
                if _r and _r.get("rate") is not None:
                    _s["hitRate"] = _r["rate"]
                    _s["hitSample"] = _r["fired"]
            strong_signals["hitRates"] = _sig_rates
    except Exception:
        pass
    # 연속하락(+20)/단발반등(-15) → 종합 신뢰도 점수 보정(0~100 재클램프)
    if advanced.get("horseConfAdj"):
        _sc_h = signal_confidence.get("horses") or {}
        for _no, _pts in advanced["horseConfAdj"].items():
            _cur = _sc_h.get(_no) or {"excessScore": 0.0, "reversalScore": 0.0,
                                      "mismatchScore": 0.0, "confidence": 0.0, "grade": None}
            _nc = round(max(0.0, min(100.0, _cur["confidence"] + _pts)), 1)
            _cur["confidence"] = _nc
            _cur["velocityAdj"] = _cur.get("velocityAdj", 0) + _pts
            _cur["grade"] = "🔴" if _nc >= 70 else ("🟡" if _nc >= 40 else None)
            _sc_h[_no] = _cur
        signal_confidence["horses"] = _sc_h
        signal_confidence["strong"] = sorted([n for n, v in _sc_h.items() if v["confidence"] >= 70],
                                             key=lambda n: -_sc_h[n]["confidence"])
        signal_confidence["refer"] = sorted([n for n, v in _sc_h.items() if 40 <= v["confidence"] < 70],
                                            key=lambda n: -_sc_h[n]["confidence"])

    # [유력마-베팅추천 정합] 전적이 있으면 베팅 추천도 '유력마 TOP5'(전적+배당 통합)와 같은 순서로 만든다.
    #   기존 문제: key_horses = 복승/단승 배당 인기순 → 전적 좋은 말이 TOP5엔 있는데 추천엔 빠지고,
    #             전적 나쁜 저배당 인기마가 추천에 들어가 TOP5와 딴판이 되던 불일치.
    #   수정: elimination(전적40+배당60 통합)의 통합점수 순 = renderTopHorses(TOP5) 정렬식과 동일하게
    #        상위 3두로 key_horses 재정렬(전적 수집된 경우만; 전적 없으면 기존 배당 인기 유지).
    _elim_pre = None
    try:
        _elim_pre = _elimination(curQ, curD, exa, drops, form, _odds_map_un(trio), curWin=curWin)
        if _elim_pre and _elim_pre.get("formAvailable"):
            # [배당 우선 전환] 랭킹은 combinedProb(시장0.7+전적0.3) 우선 + 배당 가감된 formScoreAdj 미세반영.
            #   이중수렴(저배당+전적우수)은 +50 가점으로 강력 추천 상위 고정.
            def _rank_form(h):
                _fa = h.get("formScoreAdj")
                return (_fa if _fa is not None else (h.get("formScore") or 0)) / 100.0
            _integ = sorted((_elim_pre.get("horses") or []),
                            key=lambda h: -(((h.get("combinedProb") or 0) * 1000)
                                            + (h.get("total") or 0) + _rank_form(h)
                                            + (50 if h.get("dualConverge") else 0)))
            # [배당 우선 전환] '전적 우수하나 배당 높음(보험 하향)' 말은 메인 유력마에서 뒤로 밀어
            #   저배당(시장 유력)·이중수렴 말을 우선. 삭제가 아니라 순서만 후순위(보험용으로 편성 유지).
            _demote = {int(h["no"]) for h in _integ
                       if h.get("insuranceDemote") and h.get("no") is not None}
            _main = [int(h["no"]) for h in _integ
                     if h.get("no") is not None and int(h["no"]) not in _demote]
            _ins = [int(h["no"]) for h in _integ
                    if h.get("no") is not None and int(h["no"]) in _demote]
            _io = _main + _ins
            if len(_io) >= 2:
                key_horses = _io[:3]                          # 베팅 추천 근간을 통합 유력마로 정렬(보험하향 후순위)
                ranked = _io + [h for h in ranked if h not in _io]   # 삼복승 편성 풀도 통합순 우선
    except Exception as _ke:
        print("[유력마정합] 실패:", _ke)

    # [전적 과가중 근본 해결] 저배당(단승/복승 대표 5배 이하) = 시장이 유력하다고 봄 → 전적 미수집/저조라도
    #   유력마에서 절대 제외하지 않는다(유력마 후보 보장). 후쿠시마 2R: 6번(복승 4.9배 시장최강·전적0)이
    #   통합정렬(전적 비중)에서 밀려 유력마 제외됐던 문제 근본 수정. 배당 낮은 순으로 유력마 상위 편입.
    market_favorites = []
    try:
        def _mkt_repr(h):
            best = curWin.get(h)
            for (a, b), o in curQ.items():
                if h in (a, b) and o and o > 0 and (best is None or o < best):
                    best = o
            return best
        _mf = []
        for h in sorted(valid_nos or []):
            ro = _mkt_repr(h)
            if ro is not None and ro <= 5.0:
                _mf.append((int(h), round(float(ro), 1)))
        _mf.sort(key=lambda t: t[1])                          # 배당 낮은 순(가장 유력한 시장)
        # 전적 수집 여부(형태점수 유무)로 '전적 미수집' 표시
        _form_by = {int(f.get("no")): f for f in (form or []) if f.get("no") is not None}
        for h, ro in _mf:
            _fs = (_form_by.get(h) or {}).get("totalScore")
            market_favorites.append({"no": h, "odds": ro,
                                     "formMissing": (_fs is None or _fs <= 0),
                                     "note": "시장 유력(배당 %g배)%s" % (ro, " · 전적 미수집" if (_fs is None or _fs <= 0) else "")})
        # 유력마에 없는 저배당 시장유력마를 상위(배당 낮은 순)로 편입 — 기존 유력마 유지, 총 5두 이내.
        _mf_nos = [h for h, _ in _mf]
        _added = [h for h in _mf_nos if h not in key_horses]
        if _added:
            key_horses = key_horses + _added
            key_horses = key_horses[:5]
            ranked = _mf_nos + [h for h in ranked if h not in _mf_nos]   # 조합 편성 풀도 시장유력 우선
    except Exception as _mfe:
        print("[시장유력마] 실패:", _mfe)

    # [1번·유력마 1마리] 제거 완화 후에도 유력 후보가 1마리뿐이면 → 축 + 배당 낮은 2마리 자동 추가(최소 복승) + 경고.
    single_favorite = None
    try:
        if _elim_pre:
            _cands = [int(h["no"]) for h in (_elim_pre.get("horses") or [])
                      if (h.get("keep") or h.get("override")) and h.get("no") is not None]
            if len(_cands) == 1:
                _ax = _cands[0]
                _rep = {}
                for (a, b), o in curQ.items():
                    for hh in (a, b):
                        if o and o > 0 and (hh not in _rep or o < _rep[hh]):
                            _rep[hh] = o
                _others = sorted([h for h in _rep if h != _ax], key=lambda h: _rep[h])[:2]
                single_favorite = {"axis": _ax, "partners": _others,
                                   "partnerOdds": {h: _rep[h] for h in _others},
                                   "note": "유력마 1마리 — 축 + 배당 낮은 2마리로 최소 복승 구성(또는 패스·소액만 권장)"}
    except Exception as _sfe:
        print("[유력마1마리] 실패:", _sfe)

    # [근본해결3] raw 쌍승역전 → 마감 전 '예비 유력마' 즉시 반영(정식 win-exacta 확정 전에도).
    #   카와사키 7R: raw 쌍승역전(11↔7) T-3분 감지됐으나 정식 공식(단승 대비)은 마감 후에야 7번 확정 →
    #   복승 인기 기반 wx_reversals(단승 불필요)의 강한 역전(ratio<0.80) challenger(실질 1착 후보)를
    #   마감 전에 한해 key_horses 상위로 조기 승격(기존 유력마는 뒤로 보존·삭제 아님). 마감 후엔 미적용.
    # [역배열 강도별 처리·사용자 요청] 강도(배당차%)+지속성으로 역할 분리(기존 pre_reversal 확장·무삭제):
    #   35%+ 확정(2회+) → 축(복승 메인 승격) / 20~35% → 복승 보조 / 10~20% → 삼복승 보험 / 10%미만 무시.
    #   1회만 감지된 35%+는 '후보'로 보조 강등(마감 전 2회+ 연속만 확정 축).
    pre_reversal = []
    reversal_sub = []            # [보조] 20~35% 또는 후보(1회 축) → 복승 보조
    reversal_insurance = []      # [보험] 10~20% → 삼복승 보험
    reversal_roles = _reversal_axis_roles(wx_reversals, hist, fav_rank, curWin, curQ) if (not after_close and wx_reversals) else []
    if reversal_roles:
        for _rr in reversal_roles:
            _ch, _ro, _role = _rr["no"], _rr.get("reprOdds"), _rr["role"]
            if _role == "축":
                # 축은 실경쟁마(≤15배)만 key_horses 승격(롱샷 오탐 방지) — 기존 조건 유지
                if _ro is not None and _ro <= 15 and _ch not in pre_reversal:
                    pre_reversal.append(_ch)
            elif _role == "보조":
                if _ro is not None and _ro <= 20 and _ch not in reversal_sub:
                    reversal_sub.append(_ch)
            elif _role == "보험":
                if _ch not in reversal_insurance:
                    reversal_insurance.append(_ch)
        if pre_reversal:
            key_horses = (pre_reversal + [h for h in key_horses if h not in pre_reversal])[:3]
            ranked = pre_reversal + [h for h in ranked if h not in pre_reversal]

    # [보완·복승 메인 승격] 마감 임박, 같은 말이 2개+ 복승 조합에서 동시 30%+ 급락 = 자금 집중 → 실질 유력마.
    #   → 유력마 TOP3 최상위로 조기 승격(복승 메인에 자동 편성). 마감 후는 미적용(기존 마감후 정책 유지).
    #   예) 카사마츠 1R: 3+6 ▼61%·3+4 ▼41%·2+3 ▼48% → 3번이 3개 조합 동시 급락 → 유력마 1위 승격.
    surge_promote = []
    if not after_close and drops:
        def _rep_o(h):                                 # 대표배당(단승 우선·없으면 최저 복승)
            if curWin.get(h):
                return curWin[h]
            best = None
            for (a, b), o in curQ.items():
                if h in (a, b) and o > 0 and (best is None or o < best):
                    best = o
            return best
        _sc = {}
        for _d in drops:
            if (_d.get("pct") or 0) <= -30:            # 30%+ 급락 조합만
                for _h in (_d.get("combo") or []):
                    _sc.setdefault(int(_h), []).append(_d.get("pct") or 0)
        # 2개+ 조합 동시 급락 + 대표배당 20배 이하(실제 경쟁마·롱샷 노이즈 제외).
        #   급락 조합 수 많은 순 → 급락폭 큰 순. 노이즈 방지 위해 최대 2두만 승격.
        _cand = [(h, len(v), sum(v)) for h, v in _sc.items()
                 if len(v) >= 2 and (_rep_o(h) or 999) <= 20]
        surge_promote = [h for h, _c, _s in sorted(_cand, key=lambda t: (-t[1], t[2]))][:2]
        if surge_promote:
            key_horses = (surge_promote + [h for h in key_horses if h not in surge_promote])[:3]
            ranked = surge_promote + [h for h in ranked if h not in surge_promote]

    # [3번] 유력마 실시간 추가 — 초반 유력마 고정 후, 실시간 급락/역배열 감지 말을 유력마에 '추가'(기존 유지).
    #   후쿠시마 2R 교훈: 6번(복승 4.9배 시장 최강+6+11 급락 -29.9%)이 전적 0 → 유력마 제외됐다가
    #   급락 신호가 와도 재편입 안 됐다. → 급락(마감 임박 -25%+·평상 -30%+)·역배열 감지 말을 유력마에 추가.
    realtime_added = []
    if not after_close:
        def _rt_rep(h):
            if curWin.get(h):
                return curWin[h]
            best = None
            for (a, b), o in curQ.items():
                if h in (a, b) and o > 0 and (best is None or o < best):
                    best = o
            return best
        _rt_thresh = -25 if (cur_mb is not None and cur_mb <= 3) else -30   # 마감 임박(T-3분내)엔 완화
        _rt_drop = {}
        for _d in (drops or []):
            if (_d.get("pct") or 0) <= _rt_thresh:
                for _h in (_d.get("combo") or []):
                    _rt_drop[int(_h)] = min(_rt_drop.get(int(_h), 0), _d.get("pct") or 0)
        for _h, _pct in sorted(_rt_drop.items(), key=lambda kv: kv[1]):
            if _h not in key_horses and (_rt_rep(_h) or 999) <= 30:      # 실경쟁마(30배 이하)만·롱샷 제외
                realtime_added.append({"no": _h, "type": "drop", "pct": round(_pct),
                                       "reason": "급락 %d%%" % round(_pct)})
        _il = (inverse or {}).get("invLead")                            # 역배열 실질유력마
        if _il and _il.get("no") is not None:
            _iln = int(_il["no"])
            if _iln not in key_horses and not any(r["no"] == _iln for r in realtime_added):
                _dp = _il.get("diffPct")
                realtime_added.append({"no": _iln, "type": "reversal",
                                       "reason": "역배열" + (" %g%%" % _dp if _dp is not None else "")})
        for _r in realtime_added[:2]:                                   # 최대 2두 추가(기존 유력마 유지)
            if _r["no"] not in key_horses:
                key_horses = key_horses + [_r["no"]]

    # [마번 범위 검증·최종 게이트] pre_reversal·surge_promote·realtime_added가 역전/급락 조합에서
    #   유력마에 주입한 마번 중 유효범위(valid_nos = 배당 등장 + 종목 최대마번) 밖은 완전 제외.
    #   → "7명 경륜에 11번 추천" 같은 잔존마 추천을 추천 조립 직전에 원천 차단(기존 유력마 로직 무손상).
    if valid_nos:
        _kh_before = list(key_horses)
        key_horses = [h for h in key_horses if h in valid_nos]
        realtime_added = [r for r in realtime_added if r["no"] in valid_nos]
        pre_reversal = [h for h in pre_reversal if h in valid_nos]
        surge_promote = [h for h in surge_promote if h in valid_nos]
        if len(key_horses) != len(_kh_before):
            _dropped_kh = [h for h in _kh_before if h not in valid_nos]
            print(f"[마번 범위 검증] {rk}: 유력마에서 범위밖 마번 제외 {_dropped_kh} (최대 {_max_no}번·출전 {sorted(valid_nos)})")

    # [저배당 압축 패턴] 최종 유력마 TOP3 중 저배당 밀집(4배 이하 2두+ 강력 / 5배 이하 3두+ 중간) → 축 패턴
    #   [2번 개선] 경마만 적용(경륜/경정/바이크 비활성) + 명확한 축(배당차 30%+)일 때만.
    compression_pattern = _compression_pattern(key_horses, curWin, curQ, strong_signals, rec.get("sport"))

    # ═══ [배당 흐름 기반 제거 시스템] 흐름 점수 + 흐름 제거 + 고배당 후보 발굴 ═══
    #   "들어올 말 찾기 → 안 들어올 말 제거". 흐름 없는 말(죽은인기/연속상승/페이크/역배열반대)은 저배당이어도 제거,
    #   흐름 좋은 고배당(10배+ 하락중) 말은 삼복승 보험 후보로 편입. 기존 유력마/제거 로직과 별개 파생(무삭제).
    flow_scores = _flow_scores(hist, valid_nos, strong_signals, advanced, curQ, curWin, inverse)
    flow_removal = _flow_removal(flow_scores, key_horses)
    _removed_nos = set(r["no"] for r in flow_removal)
    high_odds_candidates = _high_odds_candidates(flow_scores, _removed_nos, key_horses)

    # [중고배당 유력마·1번] 복승 10배+ 인데 강한 신호(급락/역배열/스마트머니/집중급락/전적A·B) 1개+ → 💎 중고배당 유력마.
    #   마감 후엔 미표시(추천 반영 없음). 삼복승 보험 자동 편성·오버레이/대시보드 알림·학습에 사용.
    mid_high_favorites = ([] if after_close else
                          _mid_high_odds_favorites(valid_nos, curWin, curQ, drops, dark_horses, reversal_roles, form))

    # [복승 크로스 역배열] 인기 상위쌍과의 복승/쌍승 조합 편차로 각 말의 실질 강세(2착 유력) 점수화(전체 말 쌍 비교).
    cross_reversal = _cross_reversal(curQ, curD, fav_rank, valid_nos)

    # [핵심 추천·추천 과다 근본 해결] 엄격 우선순위로 축 2두만 선정 → 복승 X+Y·삼복승 X+Y+Z(딱 이것만). 마감 후 미표시.
    core_picks = (None if after_close else
                  _core_picks(key_horses, dark_horses, reversal_roles, drops, curWin, curQ, valid_nos))
    if core_picks:
        _cq = core_picks["quinella"]
        core_picks["quinellaOdds"] = curQ.get((min(_cq), max(_cq))) if len(_cq) == 2 else None

    # 이상감지말: 최대 급락 조합 중 유력마 아닌 말, 없으면 4순위 유력마
    # [1번] 마감 후 급락은 추천에 반영하지 않음(보험 픽·전략에서 제외) → 마감 전 기준 유지
    anomaly_horse = None
    for d in (drops if not after_close else []):
        for h in d["combo"]:
            # [마번 범위 검증] 배당에 등장하는 유효 마번(valid_nos)만 이상감지말 후보 → 잔존마(범위밖) 제외
            if h not in key_horses and (not valid_nos or h in valid_nos):
                anomaly_horse = h
                break
        if anomaly_horse is not None:
            break
    if anomaly_horse is None:
        anomaly_horse = ranked[3] if len(ranked) > 3 else (key_horses[-1] if key_horses else None)

    # [배당 3착 자동 발굴] 저배당 압축(축 2두) 확정 시 삼복승 3착 자리를 고배당 말에서 발굴(마감 전만)
    third_place_hunt = _third_place_hunt(compression_pattern, strong_signals,
                                         (None if after_close else anomaly_horse), wx_reversals,
                                         form, curWin, curQ, drops, valid_nos)
    if third_place_hunt.get("active"):
        for _tr in third_place_hunt.get("trios", []):
            try:
                _ev, _ = _trio_est(_tr["combo"])   # 구성 복승 3쌍 기하평균×2 추정
                _tr["expOddsEst"] = round(_ev, 1) if _ev else None
            except Exception:
                _tr["expOddsEst"] = None

    # 5) 베팅 추천: 복승 메인/보조 + 삼복승 메인/보험1/보험2 + 예산 배분율(alloc %)
    trio_map = _odds_map_un(trio)
    # [핵심 추천] 삼복승 배당 채우기(실배당 없으면 _trio_est 추정)
    if core_picks and core_picks.get("trifecta"):
        _ct = tuple(sorted(core_picks["trifecta"]))
        _to = trio_map.get(_ct)
        if _to is None:
            try:
                _ev, _ = _trio_est(list(_ct))
                _to = round(_ev, 1) if _ev else None
            except Exception:
                _to = None
        core_picks["trifectaOdds"] = _to

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

    # [1번·유력마 1마리] 축 + 배당 낮은 2마리로 최소 복승 자동 편성(소액·경고 동반)
    if single_favorite and single_favorite.get("partners"):
        _sax = single_favorite["axis"]
        for _sp in single_favorite["partners"]:
            _addbet("복승", "복승(유력1축·소액)", [_sax, _sp], 10, _q(_sax, _sp))

    if len(key_horses) == 2:
        h1, h2 = key_horses[0], key_horses[1]
        _addbet("복승", "복승 메인", [h1, h2], 43, _q(h1, h2))
    if len(key_horses) >= 3:
        h1, h2, h3 = key_horses[0], key_horses[1], key_horses[2]
        # [삼복승 구성 복승 자동 편성] 삼복승 3두의 복승 3쌍(1+3·1+7·3+7)을 모두 추천에 포함(하코다테 11R 학습).
        #   배당 차이 반영: 가장 낮은 복승을 메인, 나머지는 보조. 기존 '메인=상위2유력마' → '메인=최저배당쌍'.
        _tri_pairs = list(dict.fromkeys(tuple(sorted(p)) for p in [(h1, h2), (h1, h3), (h2, h3)]))
        _tri_pairs.sort(key=lambda p: (_q(p[0], p[1]) if _q(p[0], p[1]) is not None else 9e9))
        _p_alloc = [43, 20, 12]
        for _pi, _pp in enumerate(_tri_pairs):
            _plabel = "복승 메인" if _pi == 0 else "복승 보조(삼복승 구성)"
            _addbet("복승", _plabel, list(_pp), _p_alloc[min(_pi, 2)], _q(_pp[0], _pp[1]))
        _addbet("삼복승", "삼복승 메인", [h1, h2, h3], 29, trio_map.get(tuple(sorted([h1, h2, h3]))))
        if anomaly_horse is not None:
            _addbet("삼복승", "삼복승 보험1", [h1, h2, anomaly_horse], 4,
                    trio_map.get(tuple(sorted([h1, h2, anomaly_horse]))))
            _addbet("삼복승", "삼복승 보험2", [h1, h3, anomaly_horse], 4,
                    trio_map.get(tuple(sorted([h1, h3, anomaly_horse]))))

    # [히로시마 2R 학습·복승 받치기] 역배열 실질유력마(고배당인데 시장이 실질 승자로 미는 말)를 축으로
    #   다른 유력마들과 받치기 복승을 소액 추가 → 실질유력마+다른유력마(7+4) 조합 놓침 방지.
    #   _addbet 이 복승 메인/보조와 중복되면 자동 dedup(같은 조합은 스킵).
    reversal_backing = _reversal_backing_bets(inverse, key_horses, curQ, after_close)
    for _rb in reversal_backing:
        _addbet("복승", "복승 받치기(역배열 실질유력)", _rb["combo"], 8, _rb["odds"])

    # [2번·스마트머니 복승 보조] 스마트머니(상승후급락=세력 진입) 복병 → 유력마 축과 복승 보조 자동 편성.
    #   "복승 추가: 2+10 (스마트머니)" — 세력이 붙은 복병을 복승으로 커버(마감 전만·소액 8%). 최대 2두.
    smart_quinella = []
    if not after_close and key_horses:
        _sm_axis = int(key_horses[0])
        for _dh in (dark_horses or []):
            if not _dh.get("smartMoney") or _dh.get("no") is None:
                continue
            _sm_no = int(_dh["no"])
            if _sm_no in [int(k) for k in key_horses]:
                continue                                   # 이미 유력마면 복승 메인/보조에 이미 있음
            _sm_combo = sorted({_sm_axis, _sm_no})
            if len(_sm_combo) != 2:
                continue
            _sm_odds = _q(_sm_combo[0], _sm_combo[1])
            _addbet("복승", "복승 보조(스마트머니 복병)", _sm_combo, 8, _sm_odds)
            smart_quinella.append({"no": _sm_no, "combo": _sm_combo, "axis": _sm_axis, "odds": _sm_odds})
            if len(smart_quinella) >= 2:
                break

    # [역배열 강도별 보조·보험] 20~35% 역배열 → 복승 보조 / 10~20% → 삼복승 보험(축 승격은 위 pre_reversal에서 이미 처리).
    if not after_close and key_horses:
        _rvx = int(key_horses[0])
        for _rs in reversal_sub[:2]:
            if int(_rs) not in [int(k) for k in key_horses]:
                _rsc = sorted({_rvx, int(_rs)})
                if len(_rsc) == 2:
                    _addbet("복승", "복승 보조(역배열 20~35%)", _rsc, 8, _q(_rsc[0], _rsc[1]))
        if len(key_horses) >= 2:
            _rv2 = [int(key_horses[0]), int(key_horses[1])]
            for _ri in reversal_insurance[:2]:
                if int(_ri) not in _rv2:
                    _ric = sorted(set(_rv2 + [int(_ri)]))
                    if len(_ric) == 3:
                        _addbet("삼복승", "삼복승 보험(역배열 10~20%)", _ric, 4, trio_map.get(tuple(_ric)))

    # [복승 크로스 역배열 삼복승 자동 편성] 인기 상위2두(fav_rank) + 크로스역배열 강세말(0.5+) → 삼복승 보험.
    #   크로스역배열말은 인기상위말 1착 시 2착 유력이므로 상위2두와 묶어 커버(마감 전만·최대 2두).
    if not after_close and cross_reversal:
        _pop2 = [int(h) for h in fav_rank[:2] if h is not None]
        if len(_pop2) == 2:
            for _cr in cross_reversal[:2]:
                if _cr["score"] >= 0.5 and int(_cr["no"]) not in _pop2:
                    _crc = sorted(set(_pop2 + [int(_cr["no"])]))
                    if len(_crc) == 3:
                        _addbet("삼복승", "삼복승 보험(크로스역배열 %s %.2f)" % (_cr["level"], _cr["score"]),
                                _crc, 4, trio_map.get(tuple(_crc)))

    # [삼복승 무조건 편성] 배당판(실배당) 유무·유력마 3두 미만과 무관하게 삼복승을 항상 추천에 포함.
    #   유력마가 3두 미만이면 선호순 풀(ranked→단승순→복승조합 등장마)로 3두를 채워 메인 생성.
    #   [역배열 대비] 쌍승역전 challenger(실질 상위 지목 아웃사이더)를 낀 조합을 추가(이변 대비).
    #   배분은 아래 삼복승 ≤18% 캡(추정 미수집 시)으로 소액 유지 → 복승 중심 그대로.
    _pool = []
    for _src in (ranked, single_rank):
        for _h in _src:
            if int(_h) not in _pool:
                _pool.append(int(_h))
    for _k in curQ:                       # 복승 조합에 등장하는 말도 후보 풀에 포함
        for _h in _k:
            if int(_h) not in _pool:
                _pool.append(int(_h))
    if len(_pool) >= 3:
        p1, p2, p3 = _pool[0], _pool[1], _pool[2]
        _addbet("삼복승", "삼복승 메인", [p1, p2, p3], 29, trio_map.get(tuple(sorted([p1, p2, p3]))))
        if anomaly_horse is not None and anomaly_horse not in (p1, p2, p3):
            _addbet("삼복승", "삼복승 보험1", [p1, p2, anomaly_horse], 4,
                    trio_map.get(tuple(sorted([p1, p2, anomaly_horse]))))
            _addbet("삼복승", "삼복승 보험2", [p1, p3, anomaly_horse], 4,
                    trio_map.get(tuple(sorted([p1, p3, anomaly_horse]))))
        # 역배열 challenger(상위 2명) 중 메인 미포함(아웃사이더)만 낀 조합 추가(마감 전만)
        if not after_close:
            _chs = []
            for _r in (wx_reversals or [])[:3]:
                _c = _r.get("challenger")
                if _c is not None and int(_c) not in _chs:
                    _chs.append(int(_c))
            _rev_added = 0
            for _c in _chs:
                if _c in (p1, p2, p3) or _rev_added >= 3:
                    continue          # 메인에 이미 포함이면 스킵(중복)
                _addbet("삼복승", "삼복승 역배열", sorted([p1, p2, _c]), 4,
                        trio_map.get(tuple(sorted([p1, p2, _c]))))
                _addbet("삼복승", "삼복승 역배열", sorted([p1, p3, _c]), 4,
                        trio_map.get(tuple(sorted([p1, p3, _c]))))
                _rev_added += 1

    # [새 규칙·카와사키11R 학습] 막판(T-2분 이내) 급락+역배열 동시 감지 말 → 삼복승 강제보험(유력마 순위 무관).
    #   TOP3 밖이어도 강제 편성 → 카와사키 11R 6번(4+6 -33%·역배열, TOP3 밖) 같은 케이스 놓침 방지. 마감 후 미적용.
    forced_trifecta = _forced_trifecta_insurance(cur_mb, after_close, drops, wx_reversals,
                                                 inverse, strong_signals, key_horses)
    if forced_trifecta.get("active"):
        for _cc in forced_trifecta.get("combos", []):
            _addbet("삼복승", "삼복승 강제보험(막판급락+역배열)", _cc, 5,
                    trio_map.get(tuple(sorted(_cc))))

    # [마감 순간 급락 삼복승 보험] T-0 마감 순간 급락 말(역배열 없어도) → 축 + 급락말 삼복승 보험 자동.
    #   기후 9R: 1+7 축 + 5·6번 마감급락 → 1+7+5·1+7+6. 대규모 급락(하코다테 10R 163건 노이즈)은 가드로 제외.
    closing_drop_insurance = _closing_drop_insurance(cur_mb, drops, key_horses,
                                                     valid_nos=valid_nos, mass_drop=mass_drop)
    if closing_drop_insurance.get("active"):
        for _cc in closing_drop_insurance.get("combos", []):
            _addbet("삼복승", "삼복승 보험(마감순간 급락)", _cc, 4,
                    trio_map.get(tuple(sorted(_cc))))

    # [2번 고배당 후보 발굴] 흐름 좋은 고배당(10배+ 하락/급락/스마트머니) 말 → 축2두 + 그 말 삼복승 보험.
    #   흐름 점수 상위 최대 2두. 마감 후는 미적용(기존 정책).
    if not after_close and high_odds_candidates and len(key_horses) >= 2:
        _hax = [int(h) for h in key_horses[:2] if h is not None]
        if len(_hax) >= 2:
            for _hc in high_odds_candidates[:2]:
                _hcc = sorted(set(_hax + [_hc["no"]]))
                if len(_hcc) == 3:
                    _addbet("삼복승", "삼복승 보험(💎고배당 흐름 %g배)" % _hc["odds"], _hcc, 4,
                            trio_map.get(tuple(_hcc)))

    # [4번·중고배당 유력마 자동 편성] 복승10배+ & 강한신호 말 → 저배당 축2두 + 그 말 삼복승 '대박 보험'.
    #   축2두 중고배당3착 = 저배당 축 안정성 + 고배당 3착 대박. high_odds_candidates와 dedup(같은 조합 자동 스킵).
    if not after_close and mid_high_favorites and len(key_horses) >= 2:
        _mhax = [int(h) for h in key_horses[:2] if h is not None]
        if len(_mhax) >= 2:
            for _mf in mid_high_favorites[:2]:
                if int(_mf["no"]) in _mhax:
                    continue
                _mcc = sorted(set(_mhax + [int(_mf["no"])]))
                if len(_mcc) == 3:
                    _addbet("삼복승", "삼복승 보험(💎중고배당 %g배·%s)" % (_mf["odds"], "·".join(_mf.get("sigTypes") or [])),
                            _mcc, 4, trio_map.get(tuple(_mcc)))

    # [추천 말 수 유연화] 신호 강도별 추천 말 수 가이드(강제 아님·안내)
    recommend_flex = _recommend_flex(strong_signals, signal_confidence, after_close)

    # [고배당 동반 패턴·참고] 고배당 신호말 + 배당 낮게 묶인 다른 고배당 말 → 참고 삼복승(메인과 별도)
    #   추정배당은 아래 _trio_est 정의 후 채운다(표시용·추천 배분엔 미포함).
    high_odds_companion = _high_odds_companion(
        drops, wx_reversals, inverse, curWin, curQ, key_horses,
        learned=_high_odds_companion_hitrate(), after_close=after_close)

    # [3번] 삼복승 실배당 미수집 시: 구성 복승 3쌍의 기하평균×2 로 추정(라벨=추정)
    #   [보완] 1쌍 미수집(아웃사이더/역배열 조합)이면 그 쌍을 보수적으로 max(known)로 근사해
    #   '거친 추정(estRough)'이라도 배당을 표시(항상 편성한 삼복승의 판단 근거 제공). 2쌍+ 미수집=None.
    def _trio_est(cc):
        ps = [_q(cc[0], cc[1]), _q(cc[0], cc[2]), _q(cc[1], cc[2])]
        present = [p for p in ps if p is not None and p > 0]
        if len(present) == 3:
            gm = (present[0] * present[1] * present[2]) ** (1.0 / 3.0)
            return round(gm * 2, 1), False
        if len(present) == 2:
            est_missing = max(present)   # 미수집 쌍 = 고배당(비인기) 가능성↑ → 보수적 상향 근사
            gm = (present[0] * present[1] * est_missing) ** (1.0 / 3.0)
            return round(gm * 2, 1), True
        return None, False
    for r in bet_rec:
        if r["kind"] == "삼복승" and r["expOdds"] is None:
            _ev, _rough = _trio_est(r["combo"])
            r["expOddsEst"] = _ev
            if _rough:
                r["estRough"] = True

    # [고배당 동반 참고] 참고 삼복승 추정배당 채움(_trio_est 재사용·표시용·추천 배분엔 미포함)
    if high_odds_companion.get("active"):
        for _it in high_odds_companion.get("items", []):
            _tb = []
            for _cc in _it.get("trios", []):
                _ev, _ = _trio_est(_cc)
                _tb.append({"combo": _cc, "expOddsEst": (round(_ev, 1) if _ev else None)})
            _it["trioBets"] = _tb

    # [보완·역배열 표시] 쌍승역전 challenger 를 낀 삼복승 픽에 플래그 → 프론트 🔄 배지(보험 라벨이어도 가시화)
    _rev_ch = set()
    for _r in (wx_reversals or []):
        _c = _r.get("challenger")
        if _c is not None:
            _rev_ch.add(int(_c))
    if _rev_ch:
        for b in bet_rec:
            if b.get("kind") == "삼복승" and any(int(h) in _rev_ch for h in b.get("combo", [])):
                b["reversalPick"] = True
    # [새 규칙·카와사키11R] 강제보험 픽에 flag → 프론트/오버레이 강조(라벨 기반 판별 보강)
    if forced_trifecta.get("active"):
        _forced_set = {tuple(sorted(c)) for c in forced_trifecta.get("combos", [])}
        for b in bet_rec:
            if b.get("kind") == "삼복승" and tuple(sorted(b.get("combo", []))) in _forced_set:
                b["forcedPick"] = True

    # [대규모급락 전략] 삼복승 보험 8→15% 확대·중배당 복승 보험 추가·최저배당 신뢰도 하락(기존 조합 유지)
    # [1번] 마감 후에는 대규모급락 전략도 추천에 반영하지 않음(참고만)
    mass_drop_strategy = _apply_mass_drop_strategy(bet_rec, mass_drop, drops, curQ) if not after_close else None

    # [신호 조합] 이상감지 신호가 있는 말들의 모든 복승 조합을 추천에 추가(고배당 포함) → 147배 놓침 방지
    if not after_close:
        _sh_order = []

        def _push_sig(h):
            if h is not None and int(h) not in _sh_order:
                _sh_order.append(int(h))
        # [2번] 단승 급락 = 가장 강한 신호 → 추천 우선순위 최상단에 먼저 반영(다른 신호보다 우선)
        for _d in single_drops:
            _push_sig(_d.get("no"))
        # [4번] 말별 연속 하락 '확정신호(3회+)'도 최상위 우선 반영(반등=페이크는 제외)
        for _hs in sorted((advanced.get("horseStreaks") or {}).values(), key=lambda x: -x["count"]):
            if _hs["count"] >= 3 and not _hs["rebounded"]:
                _push_sig(_hs["no"])
        # [5번] 종합 신뢰도 기반 우선 반영 — 강력(70+)·참고(40~69) 신호말을 우선 순위로
        for _h in (signal_confidence.get("strong") or []):
            _push_sig(_h)
        for _h in (signal_confidence.get("refer") or []):
            _push_sig(_h)
        for _rv in wx_reversals:          # 쌍승 역전으로 실질 1착 지목된 말
            _push_sig(_rv.get("challenger"))
        for _h in (quin_mismatch or {}).get("focusHorses", []):   # 복승 불일치 집중 자금 유입 말
            _push_sig(_h)
        for _h in (excess.get("concentrated") or []):
            _push_sig(_h)
        for _d in drops:
            for _h in _d.get("combo", []):
                _push_sig(_h)
        for _rv in reversals:
            if _rv.get("flipped"):
                for _h in (_rv.get("favored") or []):
                    _push_sig(_h)
        if anomaly_horse is not None:
            _push_sig(anomaly_horse)
        if len(_sh_order) >= 2:
            _sig_added = _signal_combo_bets(_sh_order, curQ, bet_rec)
            _main = next((b for b in bet_rec if b.get("label") == "복승 메인"), None)
            if _main and _sig_added:
                _main["alloc"] = max(20, round(_main.get("alloc", 43) - _sig_added, 1))

    # [배당판 일치 검증] 추천 복승 메인이 실제 배당판 최저(최인기) 조합과 크게 다르면 경고 + 인기조합 추가
    #   원인: 배당판을 초반에 못 끌어와 opening 배당 캡처 → 유력마/추천이 현재 배당판과 불일치("2+9=7.1" vs 실제 63.2).
    market_check = None
    if curQ and not after_close:
        _mfav_pair, _mfav_odds = min(curQ.items(), key=lambda kv: kv[1])
        _main = next((b for b in bet_rec if b.get("label") == "복승 메인"), None)
        _main_odds = (_main or {}).get("expOdds")
        # (a) 배당판 자체가 opening/불안정(최저 복승도 80배+) → 수집 재시도 권장
        market_stale = _mfav_odds is not None and _mfav_odds >= 80.0
        # (b) 추천 메인이 배당판 인기 조합보다 2.5배+ 비쌈 = 배당판과 불일치(유력마가 배당 최저와 동떨어짐)
        diverged = (_main_odds is not None and _mfav_odds and _main_odds > _mfav_odds * 2.5)
        if market_stale or diverged:
            market_check = {"favPair": list(_mfav_pair), "favOdds": _mfav_odds,
                            "mainPair": (_main or {}).get("combo"), "mainOdds": _main_odds,
                            "stale": market_stale, "diverged": bool(diverged)}
            # 실제 배당판 최저(인기) 복승을 추천에 반드시 포함(놓침 방지) — _addbet 이 중복 자동 제거
            if len(_mfav_pair) == 2:
                _addbet("복승", "복승 인기(배당최저)", list(_mfav_pair), 5, _mfav_odds)

    # [4번] 추천 조합별 신호 품질(상/중/하) + 근거(초과급락 말) 부착
    for r in bet_rec:
        _cq = _combo_signal_quality(r.get("combo"), excess)
        r["signalQuality"] = _cq["quality"]
        r["signalReason"] = _cq["reason"]

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
    # [경주전환 방어] 비정상 변동폭으로 기준값 재설정된 경우 안내(변동 신호는 계산하지 않음)
    if baseline_reset:
        signals.append({"level": "🟡", "type": "기준재설정",
                        "text": "⚠️ 비정상 변동폭 감지 — 기준값 재설정됨",
                        "detail": "이전 경주 배당 잔존 의심(95%+ 급락 다수) → 이번 수집을 새 기준값으로 설정. 다음 수집부터 변동 계산."})
    # [대규모 급락] 최상단 별도 알림 — 특정 유력마 없음·이변 가능성↑
    if mass_drop:
        signals.append({"level": "🌊", "type": "대규모급락",
                        "text": f"대규모 자금 분산 — {mass_drop['dropped']}/{mass_drop['total']}조합"
                                f"({int(mass_drop['ratio']*100)}%) 동시 30%+ 급락",
                        "detail": mass_drop["note"]})
    # [1·2번] 초과 급락(집중) 말 = 시장 평균보다 크게 급락 → 노이즈 제거 후 진짜 신호로 승격
    for _h in (excess.get("concentrated") or [])[:5]:
        _e = excess["horses"][_h]
        if _e.get("absStrong"):
            signals.append({"level": "🔴", "type": "집중급락", "horse": _h,
                            "text": f"{_h}번 급락 {_e['avg']}% (절대 10%+ 자금집중·추천 필수)",
                            "detail": "시장 전체 급락에 묻혀도 절대 10%+ 급락은 특정 말 자금집중 확정 → 노이즈 아님(집중신호 승격)"})
        else:
            signals.append({"level": "🔴", "type": "집중급락", "horse": _h,
                            "text": f"{_h}번 초과급락 {_e['excess']}%p (시장평균 {excess['overall']}% 대비 집중)",
                            "detail": "시장 평균보다 크게 급락 → 특정 말에 자금 집중(진짜 신호)"})

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
                            "detail": reason,
                            "oddsBefore": d["prev"], "oddsAfter": d["cur"], "dropPct": d["pct"],
                            "highOdds": (d["prev"] or 0) >= 50})
    for d in drops:
        lvl, reason = _drop_reason(d["pct"])
        if lvl:
            _sig = {"level": lvl, "type": "급락",
                    "text": f"{d['combo'][0]}+{d['combo'][1]} 복승 {d['prev']}→{d['cur']} ({d['pct']}%)",
                    "detail": reason,
                    # [저/고배당 분리] 고배당(직전 50배+)은 %로만 판단·하단 참고 노출
                    "oddsBefore": d["prev"], "oddsAfter": d["cur"], "dropPct": d["pct"],
                    "highOdds": (d["prev"] or 0) >= 50}
            # [2번] 대규모 급락 중이면 개별 신호는 신뢰도 하향(초과급락 분석으로 판단 전환)
            if mass_drop:
                _sig["lowConfidence"] = True
                _sig["detail"] = reason + " · ⚠️ 대규모 급락 중 — 개별 신뢰도↓(초과급락 분석 참고)"
            signals.append(_sig)
    for r in reversals:
        if r.get("flipped"):
            signals.append({"level": "🔄", "type": "역전",
                            "text": f"쌍승 {r['favored'][0]}→{r['favored'][1]} ({r['favoredOdds']}) < {r['favored'][1]}→{r['favored'][0]} ({r['otherOdds']})",
                            "detail": f"시장이 {r['favored'][0]}번을 실질 1착으로 판단"})
    # [1번] 쌍승 역전 감지 공식 — 단승(복승인기) 유력마 vs 쌍승 방향 역전(비율 기반)
    for r in wx_reversals[:5]:
        signals.append({"level": r["level"], "type": "쌍승역전공식", "horse": r["challenger"],
                        "text": r["text"],
                        "detail": f"역전비율 = 쌍승({r['challenger']}→{r['favorite']}) {r['reverseExacta']} / "
                                  f"쌍승({r['favorite']}→{r['challenger']}) {r['favoredExacta']} = {r['ratio']} ({r['tag']})"})
    # [2번] 복승 불일치 감지 공식 — 단승 예상 조합 vs 실제 최저복승 괴리
    if quin_mismatch:
        signals.append({"level": quin_mismatch["level"], "type": "복승불일치공식",
                        "horse": (quin_mismatch["focusHorses"] or [None])[0],
                        "text": quin_mismatch["text"],
                        "detail": f"불일치점수 = 예상최저복승 {quin_mismatch['expectedOdds']} / "
                                  f"실제최저복승 {quin_mismatch['actualOdds']} = {quin_mismatch['ratio']}"})
    # [4번] 종합 신뢰도 강력 신호(70+) 요약
    for _no in (signal_confidence.get("strong") or [])[:5]:
        _c = signal_confidence["horses"][_no]
        signals.append({"level": "🔴", "type": "종합신뢰도", "horse": _no,
                        "text": f"{_no}번 종합 신뢰도 {_c['confidence']} → 🔴 강력 신호",
                        "detail": f"초과급락 {_c['excessScore']}×0.4 + 쌍승역전 {_c['reversalScore']}×0.35 "
                                  f"+ 복승불일치 {_c['mismatchScore']}×0.25 = {_c['confidence']}"})
    # [배당판 일치 검증] 추천이 실제 배당판과 어긋나거나 배당판이 불안정하면 최상단 경고
    if market_check:
        if market_check["stale"]:
            signals.insert(0, {"level": "⚠️", "type": "배당불안정",
                               "text": f"배당판 불안정 — 최저 복승도 {market_check['favOdds']}배(실자금 미형성/초반 미수집 의심)",
                               "detail": "배당판을 초반에 못 끌어왔을 수 있음 → 배당판 새로고침 후 재수집 권장. 현재 추천은 참고만."})
        if market_check["diverged"]:
            fp = market_check["favPair"]
            signals.insert(0, {"level": "⚠️", "type": "배당불일치",
                               "text": f"추천 복승({'+'.join(map(str, market_check['mainPair'] or []))}={market_check['mainOdds']}배)이 "
                                       f"배당판 인기 조합({fp[0]}+{fp[1]}={market_check['favOdds']}배)과 다름",
                               "detail": "유력마가 배당판 최저(인기)와 동떨어짐 = 초반 배당 미수집/전적 편중 의심. "
                                         "배당판 인기 조합을 추천에 추가함 — 배당 재확인 권장."})
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
    # [2번 고도화] ① 급락 속도 — 분당 급락%(급락폭/수집간격). 분당 10%+ 강한 신호
    for _v in advanced.get("velocity", [])[:4]:
        signals.append({"level": _v["level"], "type": "급락속도",
                        "text": f"{_v['combo'][0]}+{_v['combo'][1]} 급락 속도 분당 {_v['speed']}% "
                                f"({abs(_v['pct'])}% / {_v['minutes']}분)",
                        "detail": "분당 10%+ = 단시간 집중 자금(강한 신호)" if _v["level"] == "🔴" else "분당 5%+ = 자금 유입 가속(주의)"})
    # [2번 고도화] ② 연속 하락(신뢰도 +20) — 3회 연속 하락 = 지속 자금 유입
    for _key, _st in advanced.get("streaks", {}).items():
        if _st["type"] == "연속하락":
            signals.append({"level": "🔴", "type": "연속하락", "horse": _st["combo"][0],
                            "text": f"{_key} 3회 연속 하락 → 신뢰도 +20",
                            "detail": "지속적 자금 유입(단발 아님) → 신호 신뢰도 상향"})
    # [2번 고도화] ③ 페이크 베팅 의심 — 급락 후 반등(신뢰도 -15)
    for _f in advanced.get("fakes", []):
        signals.append({"level": "⚠️", "type": "페이크베팅", "horse": _f["combo"][0],
                        "text": f"⚠️ 페이크 베팅 의심: {_f['combo'][0]}+{_f['combo'][1]} 급락 후 반등 ({'→'.join(map(str, _f['seq']))})",
                        "detail": "급락으로 유인 후 배당 회복 → 신뢰도 -15(허수 베팅 가능성)"})
    # [2번 고도화] ④ 복승 환급률 이상 — 상위 조합 자금 집중(역수합 top3 점유율 90%+)
    _ov = advanced.get("overround")
    if _ov and _ov.get("concentrated"):
        signals.append({"level": "🟠", "type": "자금집중",
                        "text": f"복승 자금 집중 — 상위 3개 조합이 전체의 {int(_ov['top3Share']*100)}% 점유",
                        "detail": f"복승 역수합(환급률) {_ov['invSum']} · 소수 조합 편중 = 특정 결과에 자금 쏠림(이변 시 고배당)"})
    # [4번] 말별 연속 하락 신호 — 확정(3회+)·약한(2회)·페이크(반등)을 신호로 노출(확정신호부터)
    _hstreaks = sorted((advanced.get("horseStreaks") or {}).values(),
                       key=lambda x: (-x["count"], not x["rebounded"]))
    for _hs in _hstreaks:
        if _hs["rebounded"]:
            signals.append({"level": "🟠", "type": "페이크의심", "horse": _hs["no"],
                            "text": f"⚠️ {_hs['no']}번 급락 후 반등 (페이크 의심) — {'→'.join(map(str, _hs['series']))}",
                            "detail": "연속 하락 신호로 유인 후 배당 회복 → 신뢰도 하향(허수 베팅 가능성)"})
        elif _hs["count"] >= 3:
            signals.append({"level": "🔴", "type": "연속하락", "horse": _hs["no"],
                            "text": f"🔴 {_hs['no']}번 {_hs['count']}회 연속 하락 (확정신호) — {'→'.join(map(str, _hs['series']))}",
                            "detail": "지속 자금 유입 = 강한 매수 신호(추천 우선 반영)"})
        elif _hs["count"] == 2:
            signals.append({"level": "🟡", "type": "연속하락", "horse": _hs["no"],
                            "text": f"🟡 {_hs['no']}번 2회 연속 하락 (약한신호) — {'→'.join(map(str, _hs['series']))}",
                            "detail": "자금 유입 초기 → 다음 수집에서 확정 여부 관찰"})

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

    # form 은 위(역배열 호출 앞)에서 이미 계산됨 — 재사용(역배열·추천게이트와 공유)
    elimination = _elimination(curQ, curD, exa, drops, form, trio_map, curWin=curWin)  # 배당+전적 복합 제거(+단승 고배당)

    # [한국경마] 시간대별(발주 10/5/2분전 대비) 마감 임박 급락 신호를 앞쪽에 병합
    time_drops = _time_based_drop_signals(rk)
    if time_drops:
        signals = time_drops + signals
    # [1·3번] 모든 신호에 유효 시점 라벨 부착 + 마감 후 신호는 '참고만' 태깅(추천 미반영)
    _phase = _deadline_phase_label(cur_mb, after_close)
    for _s in signals:
        _s.setdefault("minutesBefore", cur_mb)
        _s["phase"] = "마감 후" if after_close else _phase
        if after_close:
            _s["afterClose"] = True
            _s["note"] = "⚠️ 마감 후 신호 - 참고만(추천 미반영)"
    # [통합분석] 전적 40% + 배당 60% 통합 등급(전적이 있을 때만)
    integrated = _integrated_grades(form, curQ, curD)
    # [단승] 통합 등급 행에 단승 배당 부착(타임라인/화면 표시용)
    for h in integrated or []:
        h["win"] = curWin.get(h.get("no"))
    # [3번] 상황별 가중치 적응형 통합 등급(기존 40/60 integrated 는 유지, 이건 추가 필드)
    integrated_adaptive = _integrated_adaptive(form, curQ, curD, excess, situation)
    for h in integrated_adaptive or []:
        h["win"] = curWin.get(h.get("no"))
    # [4착 학습] 4착 빈번 말이 이번 출전마에 있으면 삼복승 보험픽 우선 추가(마감 전만·기존 조합 유지)
    try:
        _freq = _near_miss_frequent()
        if _freq and not after_close and len(key_horses) >= 2:
            for _h in (form or []):
                _nm, _no = (_h.get("name") or "").strip(), _h.get("no")
                if _nm and _nm in _freq and _no is not None and _no not in key_horses:
                    _trio = sorted([int(key_horses[0]), int(key_horses[1]), int(_no)])
                    if not any(b.get("kind") == "삼복승" and sorted(b["combo"]) == _trio for b in bet_rec):
                        bet_rec.append({"kind": "삼복승", "label": "삼복승 보험(4착빈번)", "combo": _trio,
                                        "alloc": 3, "expOdds": trio_map.get(tuple(_trio)),
                                        "nearMissPick": True, "nearMissHorse": _no})
                    break
    except Exception as _e:
        print("[4착학습] 보험픽 실패:", _e)

    # [삼복승 절충] 삼복승 배당은 수집하지 않지만(안정성), 추천은 추정배당(_trio_est)으로 유지.
    #   복승 중심으로 삼복승은 '보험(추정)' 소액(총 ≤18%)만 배분하고 남는 몫은 복승 메인으로.
    #   삼복승 배당이 실제로 수집되면(trio_map 있음) 기존 로직 그대로(추정 아님).
    if not trio_map:
        _trio_bets = [b for b in bet_rec if b.get("kind") == "삼복승"]
        for b in _trio_bets:
            b["estimated"] = True   # 추정배당 기반 보험(프론트 '추정' 표기)
        _cur_trio = sum(b.get("alloc", 0) for b in _trio_bets)
        _cap = 18.0
        if _cur_trio > _cap:
            _scale, _freed = _cap / _cur_trio, 0.0
            for b in _trio_bets:
                _new = round(b.get("alloc", 0) * _scale, 1)
                _freed += b.get("alloc", 0) - _new
                b["alloc"] = _new
            _main = next((b for b in bet_rec if b.get("label") == "복승 메인"), None)
            if _main:
                _main["alloc"] = round(_main.get("alloc", 43) + _freed, 1)

    # [비교학습] 이상감지/전적/최종 추천 조합 3종 + 현재 통합 가중치(학습 조정 반영)
    compare_recommend = _compare_recommend(form, key_horses, excess, drops, bet_rec)
    _iw_fw, _iw_ow = _learned_integrated_weights()

    # [추천 근거 상세 카드] 상위 추천마 3두별 전적·배당·기수·종합확신도 근거 조립(근거별 신뢰 가중치 첨부)
    try:
        _bw = ((_learning_load().get("stats") or {}).get("basis_weights")) or {}
        recommend_basis = _recommend_basis(key_horses, form, elimination, drops,
                                           wx_reversals, advanced, signal_confidence, _bw)
    except Exception as _rbe:
        print("[추천근거] 실패:", _rbe)
        recommend_basis = None

    # [BMED 전략] 상황 자동판별 5전략 + 원금보전 배분 + 기대환수율 + 보험용 매트릭스 추천
    #   보험용은 유력마 4두가 필요 → ranked(전체 인기순위) 전달(전략 조합은 내부에서 상위만 사용)
    try:
        bmed = _bmed_strategy(curQ, ranked, excess, inverse, mass_drop, signal_confidence, after_close,
                              sport=(rec.get("sport") or "horse"))
    except Exception as _e:
        print("[BMED] 실패:", _e)
        bmed = None

    # [5번] 현재 경주 패턴 매칭 + [4번] 신뢰도 기반 베팅 비중 자동 조정
    pattern_match = None
    try:
        cur_patterns = _extract_patterns(drops, reversals, signals, curQ, bet_rec, advanced)
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

    # [신규 3번] 경고 신호 요약(alertSignal) — 30%+ 급변 말 + 추천에 편입된 급변 조합(신호 배너용).
    #   추천 반영 자체는 위 _signal_combo_bets(신호 조합)로 이미 완료 → 여기선 요약만 노출.
    alert_signal = None
    _sig_drops = [d for d in drops if d.get("pct") is not None and d["pct"] <= ALERT_DROP_THRESH]
    if _sig_drops and not after_close:
        _ah = []
        for _d in _sig_drops[:4]:
            for _h in _d["combo"]:
                if int(_h) not in _ah:
                    _ah.append(int(_h))
        _picks = [{"combo": b["combo"], "kind": b["kind"], "expOdds": b.get("expOdds"), "expOddsEst": b.get("expOddsEst")}
                  for b in bet_rec if b.get("signalCombo")][:6]
        alert_signal = {
            "horses": _ah,
            "topDrop": f"{_sig_drops[0]['combo'][0]}+{_sig_drops[0]['combo'][1]} {_sig_drops[0]['pct']}%",
            "drops": [{"combo": d["combo"], "before": d.get("prev"), "after": d.get("cur"), "pct": d["pct"]} for d in _sig_drops[:6]],
            "picks": _picks,
            "note": "경고 신호 감지 — 급변 말 조합을 추천에 포함(고배당 대비)",
        }

    # [신규 3·4·5번] 이상감지 말 변경 이력 + 신호 안정화(2연속 확정) + 최종 유효 신호 시점
    try:
        signal_timeline = _signal_timeline(rk)
    except Exception as _e:
        print("[신호타임라인] 실패:", _e)
        signal_timeline = None

    # ═══ [추천 로직 전면 개편] 확신도·경주유형·단계추천·강화제거 파생(기존 필드 무영향) ═══
    #   신호 준비 여부: 첫 수집(기준값 설정)/재설정/스냅샷 2건 미만이면 '저배당 무조건 추천' 방지([5번]).
    confidence = race_judgment = stage_guide = None
    elimination_strong = []
    try:
        # [2번·90초 관찰 의무화] 첫 수집 후 90초 미만이면 신호가 있어도 추천 금지(배당 변화 관찰만).
        #   관찰시간 = 현재 − 첫 스냅샷(t). 측정 불가 시 스냅샷 4건으로 폴백(초반 저배당 무조건 추천 방지).
        observed_sec = None
        try:
            if hist and hist[0].get("t"):
                observed_sec = time.time() - hist[0]["t"]
        except Exception:
            observed_sec = None
        obs_ok = (observed_sec >= 90) if (observed_sec is not None) else (len(hist) >= 4)
        # [5번] 첫 수집 직후 추천 금지 — 최소 3회 관찰 + 90초 경과 후에만 신호 판단(기존 2→3 + 90초 하한)
        signal_ready = (not baseline_set) and (not baseline_reset) and (len(hist) >= 3) and obs_ok
        confidence = _confidence_engine(signal_confidence, form, advanced, key_horses)
        race_judgment = _bet_judgment(confidence, excess, advanced, wx_reversals, form,
                                      mass_drop, after_close, signal_ready)
        stage_guide = _stage_guide(cur_mb, after_close, race_judgment, inverse, advanced)
        elimination_strong = _elimination_strong(elimination, form, drops)
    except Exception as _e:
        print("[추천개편] 파생 실패:", _e)

    # [확신도 복승 필수 포함] 확신도 1위 말(예: 후쿠시마 6번 96.2)이 복승 메인에서 빠져 놓치던 문제 해결.
    #   확신도 70+ 필수 포함 · 확신도1위+2위 · 확신도1위+시장유력 복승 자동 편성 + 확신도상위+이상감지 삼복승.
    #   ⚠ core_picks(기존 축2두)·betRecommend 무삭제 — corePicks에 confQuinellas 등 별도 필드로만 부가.
    try:
        conf_q = None if after_close else _confidence_picks(
            confidence, curWin, curQ, key_horses, single_rank, anomaly_horse, valid_nos)
        if conf_q:
            if core_picks is None:                              # 축2두 미확보라도 확신도 복승은 제시
                _cq0 = conf_q["quinellas"][0]["combo"]
                core_picks = {"axis": list(_cq0), "third": None, "quinella": _cq0,
                              "trifecta": conf_q.get("trifecta"), "picks": [],
                              "quinellaOdds": conf_q["quinellas"][0].get("odds")}
            core_picks["confQuinellas"] = conf_q["quinellas"]
            core_picks["forcedHorses"] = conf_q.get("forced") or []
            core_picks["confTop1"] = conf_q["top1"]
            core_picks["confTop1Conf"] = conf_q["top1Conf"]
            core_picks["confTop1High"] = conf_q.get("top1High")   # 확신도 1위 고배당(30배+) → 복승 축 제외·삼복승 보험 표기용
            core_picks["favAxis"] = conf_q.get("favAxis")         # 시장 유력마 복승 축 2두

            # [보완②] 삼복승 예상배당 표기 — 실배당(trio_map) 우선, 없으면 _trio_est 추정.
            def _tri_odds(cc):
                if not cc or len(cc) != 3:
                    return None
                _o = trio_map.get(tuple(sorted(cc)))
                if _o is not None:
                    return _o
                try:
                    _ev, _ = _trio_est(list(cc))
                    return round(_ev, 1) if _ev else None
                except Exception:
                    return None

            if conf_q.get("trifecta"):
                core_picks["confTrifecta"] = conf_q["trifecta"]
                core_picks["confTrifectaOdds"] = _tri_odds(conf_q["trifecta"])
            if conf_q.get("trifectaIns"):
                core_picks["confTrifectaIns"] = conf_q["trifectaIns"]
                core_picks["confTrifectaInsOdds"] = _tri_odds(conf_q["trifectaIns"])
    except Exception as _e:
        print("[확신도복승] 파생 실패:", _e)

    # [혼전 경주] 상위 배당 근접 → 이변 가능성 → 고배당 포함 삼복승 전략(기존 추천 무영향, 별도 필드).
    chaotic = None
    try:
        if signal_ready:
            chaotic = _chaotic_race(curQ, curWin, key_horses, anomaly_horse, drops)
    except Exception as _e:
        print("[혼전] 파생 실패:", _e)

    # [보완·혼전 복승 박스] 혼전(압축) 경주 감지 시 복승 메인 2두 고정 → 상위 3두 박스로 확대.
    #   기존 복승 메인(h1+h2)·보조(h1+h3)에 h2+h3 조합을 추가 → 상위 3두 3조합 전부 커버(이변 대비).
    #   예) 4·6·3 유력 시 4+6·4+3·6+3 모두 추천 → 3+6 같은 조합을 놓치지 않음.
    try:
        if chaotic and chaotic.get("detected") and len(key_horses) >= 3:
            _b1, _b2, _b3 = key_horses[0], key_horses[1], key_horses[2]
            _addbet("복승", "복승 박스(혼전)", [_b2, _b3], 13, _q(_b2, _b3))
    except Exception as _e:
        print("[혼전복승박스] 실패:", _e)

    # [1번] 마감 후 대급락(50%+) 별도 감지 → "⚡ 마감 후 대급락 감지!" 배너용(추천 미반영·참고만).
    #   [4번] 학습된 '마감 후 대급락 → 입상률'이 신뢰 수준(표본5+·50%+)이면 다음경주 참고 신뢰도 첨부.
    after_close_surge = None
    try:
        if after_close:
            _big = [d for d in (drops or []) if (d.get("pct") or 0) <= -50]
            if _big:
                _hc = {}
                for _d in _big:
                    for _h in (_d.get("combo") or []):
                        _hc[int(_h)] = _hc.get(int(_h), 0) + 1
                _sh = sorted(_hc, key=lambda h: -_hc[h])[:4]
                _acs = _after_close_stats()
                after_close_surge = {
                    "detected": True,
                    "horses": _sh,
                    "drops": [{"combo": _d.get("combo"), "before": _d.get("prev"),
                               "after": _d.get("cur"), "pct": _d.get("pct")}
                              for _d in sorted(_big, key=lambda x: (x.get("pct") or 0))[:6]],
                    "note": "마감 후 대급락 — 추천 미반영, 학습·다음경주 참고 신호로 저장",
                    "learnedHitRate": _acs.get("hit_rate"),
                    "learnedSample": _acs.get("total_judged"),
                    "reliable": _acs.get("reliable"),
                }
    except Exception as _e:
        print("[마감후대급락] 파생 실패:", _e)

    # ═══ [1·2·3번 근본해결] 신호 미충족 시 '저배당 무조건 추천' 완전 비움 ═══
    #   조건: race_judgment 유형이 wait(90초 미달 또는 시장신호 4종 중 2개 미만) 또는 패스형(신호 없음).
    #   생성 코드(_addbet 등)는 모두 보존(무삭제) → 결과 리스트만 비워 모든 소비처(웹·오버레이·복기)가 공통으로
    #   '추천 없음'을 본다. 마감 후(after_close)는 참고 신호이므로 제외(기존 동작 유지).
    # ═══ [타이밍 추천 정책] T-2분 강제 추천 · T-1분 최종 확정 · 마감 후 추천 금지 ═══
    #   recommend_phase: normal(3분+) / forced(T-2분 이내) / locked(T-1분 이내) / closed(마감 후)
    #   - forced/locked: 신호 약해도(wait/패스형) 저배당 시장유력마 기준으로 강제 추천(게이트 해제).
    #   - locked: 최종 확정(프론트 🔒 표시 · 조합 안정).
    #   - closed: 마감 후 추천 금지 → 프론트·오버레이에서 추천 조합 숨김(참고만).
    #     ⚠ 서버 betRecommend 데이터는 결과 학습(_apply_result_learning)이 재사용하므로 비우지 않고 유지.
    # [중앙경마(JRA) 마감 특성] JRA는 실제 발주 2분 전에 배당판이 닫힌다(실질 마감=T-2분).
    #   → 중앙 모드에선 강제 추천을 T-3분으로 앞당기고, T-2분을 최종 확정(실질 마감)으로 처리.
    is_central = (rec.get("category") == "japan_central")
    recommend_phase = "normal"
    if is_central:
        if after_close:
            recommend_phase = "closed"
        elif cur_mb is not None and cur_mb <= 2.5:      # T-2분 이내 = 실질 마감 → 최종 확정
            recommend_phase = "locked"
        elif cur_mb is not None and cur_mb <= 3.5:      # T-3분 이내 = 강제 추천(앞당김)
            recommend_phase = "forced"
    else:
        if after_close:
            recommend_phase = "closed"
        elif cur_mb is not None and cur_mb <= 1.5:      # T-1분 이내 = 최종 확정
            recommend_phase = "locked"
        elif cur_mb is not None and cur_mb <= 2.5:      # T-2분 이내 = 강제 추천
            recommend_phase = "forced"
    recommend_forced = recommend_phase in ("forced", "locked")
    recommend_locked = (recommend_phase == "locked")
    recommend_closed = (recommend_phase == "closed")
    # [중앙경마] 배당판 닫힘 임박(T-2.5분~마감 전) → "지금이 마지막 신호" 경고 표시용
    central_closing = bool(is_central and cur_mb is not None and 0 <= cur_mb <= 2.5 and not after_close)

    # 기존 게이팅(wait/패스형 → 추천 비움) — 단 T-2분 이내(forced)면 강제 추천으로 게이트 해제.
    recommend_gated = bool(race_judgment and race_judgment.get("type") in ("wait", "패스형")
                           and not after_close and not recommend_forced)
    if recommend_gated:
        bet_rec = []          # 복승·삼복승 추천 조합 완전 비움
        trio_rec = []         # 삼복승 추천 비움
        main_bet = None
        bmed = None           # BMED 배분 표도 비움(신호 대기 상태에선 배분 없음)
        mass_drop_strategy = None

    return {
        "raceKey": rk, "hasPrev": bool(prev),
        "recommendGated": recommend_gated,   # [3번] 저배당 무조건 추천 차단(신호 대기) 여부 → 프론트 '⏳ 신호 대기 중'
        # [타이밍 추천 정책] T-2분 강제 추천 · T-1분 최종 확정 · 마감 후 추천 금지(표시 차단·학습 데이터는 보존)
        "recommendPhase": recommend_phase,   # normal|forced|locked|closed
        "recommendForced": recommend_forced, # T-2분 이내 강제 추천(신호 약해도 저배당 기준 편성)
        "recommendLocked": recommend_locked, # T-1분 이내 최종 확정(🔒)
        "recommendClosed": recommend_closed, # 마감 후 → 추천 조합 숨김(참고만)
        "isCentral": is_central,             # [중앙경마] JRA 여부(배당판 T-2분 조기 마감)
        "centralClosing": central_closing,   # [중앙경마] 배당판 닫힘 임박(T-2.5분~) → 마지막 신호 경고
        # [추천 로직 전면 개편] BMED 확신도 엔진 + 실전 경주유형 판정 + 단계별 추천 + 강화 제거마
        "confidence": confidence,          # [2번] 이상감지40+전적30+급락지속30 → 말별 확신도·랭킹
        "raceJudgment": race_judgment,     # [1·4번] 확실/신중/애매/패스형 + 근거 + 배분비율 + 쌍승강신호
        "stageGuide": stage_guide,         # [3번] T-3/T-2/T-1/T-30초 단계 추천 + 최종등급
        "eliminationStrong": elimination_strong,   # [5번] 과감한 제거마 목록(근거 포함)
        "chaotic": chaotic,                # [혼전] 상위 배당 근접 감지 + 고배당 포함 삼복승 전략

        "sport": rec.get("sport") or "horse",   # [수정#3] 종목(horse|cycle|boat|bike) → 프론트 배지
        "category": rec.get("category") or "japan_local",   # [탭분리] 분석기 탭 라우팅
        "alertSignal": alert_signal,   # [신규 3번] 경고 신호 감지 요약(배너)
        "signalTimeline": signal_timeline,   # [신규 3·4·5번] 신호말 변경이력·안정화·유효시점
        "nextRaceBlocked": bool(signal_timeline and signal_timeline.get("excluded", {}).get("next_race")),
        "counts": {"quinella": len(quin), "exacta": len(exa), "trio": len(trio),
                   "win": len(curWin), "history": len(hist)},
        "drops": drops[:15], "singleDrops": single_drops[:15], "rankChanges": rank_changes, "reversals": reversals,
        "keyHorses": key_horses, "anomalyHorse": anomaly_horse,
        "validHorses": sorted(valid_nos),   # [잔존마 필터·2번] 현재 배당 등장 마번(프론트 TOP5 필터 기준)
        "realtimeAdded": realtime_added,   # [3번] 실시간 급락/역배열로 유력마에 추가된 말(오버레이 '⚡ 실시간 추가')
        "darkHorses": dark_horses,   # [복병_집중급락] 집중급락 10회+/스마트머니 → 배당순위 무관 복병 자동 편입
        "marketFavorites": market_favorites,   # [전적 과가중 해결] 저배당(5배↓) 시장유력마(전적 미수집도 유력마 편입)
        "preReversal": pre_reversal,   # [근본해결3] raw 쌍승역전 조기 반영 예비 유력마(마감 전)
        "reversalRoles": reversal_roles,   # [역배열 강도별 처리] challenger별 축/보조/보험 역할 + 지속성(확정/후보)
        "signalReliability": _signal_reliability_for(strong_signals, dark_horses, inverse, compression_pattern, cross_reversal),   # [5번] 활성 신호별 과거 적중률(50경주+ 신뢰)
        "surgePromote": surge_promote,   # [보완] 여러 조합 동시 30%+ 급락 → 복승 메인 승격말(마감 전)
        "strongSignals": strong_signals,   # [강한 신호 8유형] 오버레이 강조·막판 보존·유형별 학습용
        "compressionPattern": compression_pattern,   # [저배당 압축 패턴] 축 패턴(4배↓2두+/5배↓3두+)+신호결합
        "flowScores": flow_scores,   # [배당 흐름 점수] 말별 흐름점수(상승-10/무변동-5/하락+10/급락+20/스마트머니+30)
        "flowRemoval": flow_removal,   # [흐름 기반 제거] 죽은인기/3연속상승/페이크/역배열반대 → 제거 대상
        "highOddsCandidates": high_odds_candidates,   # [고배당 후보 발굴] 흐름 좋은 고배당(10배+ 하락) 말 → 삼복승 보험
        "midHighFavorites": mid_high_favorites,   # [💎 중고배당 유력마] 복승10배+ & 강한신호1개+ → 삼복승 보험·알림·학습
        "corePicks": core_picks,   # [핵심 추천·추천 과다 해결] 엄격 우선순위 축2두 → 복승 X+Y·삼복승 X+Y+Z(딱 이것만)
        "crossReversal": cross_reversal,   # [복승 크로스 역배열] 각 말 실질 강세 점수(인기상위쌍 편차)·삼복승 편성
        "smartQuinella": smart_quinella,   # [스마트머니 복승 보조] 스마트머니 복병 → 축과 복승 보조 자동 추가
        "thirdPlaceHunt": third_place_hunt,   # [배당 3착 자동 발굴] 축2두+고배당 3착 후보 삼복승 보험
        "forcedTrifecta": forced_trifecta,    # [새 규칙·카와사키11R] 막판 급락+역배열 동시말 강제 삼복승
        "closingDropInsurance": closing_drop_insurance,   # [마감순간 급락 보험] 축+마감급락말 삼복승(대규모급락 제외)
        "reversalBacking": reversal_backing,  # [히로시마2R] 역배열 실질유력마 축 받치기 복승
        "recommendFlex": recommend_flex,      # [추천 말 수 유연화] 신호 강도별 추천 말 수 가이드
        "highOddsCompanion": high_odds_companion,  # [고배당 동반 패턴·참고] 메인과 별도 참고 추천
        "singleFavorite": single_favorite,    # [유력마 1마리] 축+배당낮은2마리 최소복승 + 패스/소액 경고

        "single": {str(k): v for k, v in curWin.items()}, "singleRanking": single_rank,
        "trioRecommend": trio_rec, "betRecommend": bet_rec,
        "summary": summary, "chart": chart,
        "form": form,
        "elimination": elimination,  # 배당+전적 복합 제거/후보 판정
        "integrated": integrated,    # 전적40%+배당60% 통합 등급(A/B/C/D)
        "learned": learned,  # 학습 통계 안내(있으면)
        "signals": signals, "lastSnapshot": last_snap,  # 실시간 변동 알림용
        "patternMatch": pattern_match,  # [4·5번] 현재 패턴 매칭 + 신뢰도 + 베팅 비중 조정
        "massDrop": mass_drop, "massDropStrategy": mass_drop_strategy,  # [대규모급락] 감지 + 베팅 전략
        # [신호 품질 필터링] 초과급락(집중도)·상황별 가중치·적응형 통합 등급
        # [핵심 공식] 쌍승역전·복승불일치·종합신뢰도(초과40+역전35+불일치25)
        "signalQuality": {"excess": excess, "situation": situation,
                          "integratedAdaptive": integrated_adaptive,
                          "winExactaReversals": wx_reversals,
                          "quinellaMismatch": quin_mismatch,
                          "signalConfidence": signal_confidence,
                          "advanced": advanced},
        # [마감 후 신호] 현재 스냅샷이 발주(T-0) 이후면 추천 미반영·참고만
        "afterClose": after_close, "minutesBefore": cur_mb,
        "deadlineCorrected": deadline_corrected,   # [1번] 발주시각 오검출 정정(과거 마감상태 무효화) 발생
        "collectionStalled": collection_stalled,   # [수집 조기 중단 방어] 발주 전 2분+ 미수집 → 재수집 필요
        "secsSinceCollect": secs_since_collect,     # 마지막 수집 후 경과초
        "afterCloseSurge": after_close_surge,   # [1·4번] 마감 후 대급락(50%+) 배너 + 학습 입상률

        # [경주전환 방어] 첫 수집(기준값 설정)/비정상 변동폭(기준값 재설정) 여부
        "baselineSet": baseline_set, "baselineReset": baseline_reset,
        # [배당판 일치 검증] 추천↔배당판 인기 조합 불일치·배당 불안정(초반 미수집) 경고
        "marketCheck": market_check,
        # [역배열 감지] 단승≠복승/쌍승 순서(4유형 통합) + 역배열 감지말·복승 역배열 조합
        "inverse": inverse,
        # [BMED 전략] 5전략 자동선택 + 원금보전 배분 + 기대환수율 + 보험용 매트릭스(정상과 함께 선택)
        "bmed": bmed,
        # [비교학습] 이상감지/전적/최종 추천 3종 + 통합 가중치(전적/이상감지, 학습 조정 반영)
        "compareRecommend": compare_recommend,
        "recommendBasis": recommend_basis,   # [추천 근거 카드] 말별 전적·배당·기수·확신도 근거

        "integratedWeights": {"form": _iw_fw, "anomaly": _iw_ow},
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
        # [한국경마 전용 연결] 경마장명 미인식(가라 등) 시에도 일본 경마·경륜/경정/오토바이 키에는 절대 매칭 금지.
        #   기존엔 venue 인식됐을 때만 일본 키를 걸렀고, _is_japan_key는 일본 '경마' 트랙만 잡아
        #   히로시마(경륜=cycle) 같은 키가 번호만 같으면 loose 매칭돼 한국 탭에 엉뚱한 분석이 뜨던 문제 수정.
        _sp = (db.get(k) or {}).get("sport")
        if _is_japan_key(k) or _sp in ("cycle", "boat", "bike"):
            continue
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


# ═══════════════════════════════════════════════════════════════════
#  [신규] 고배당 경고 신호 완전 기록 시스템 (삭제 없이 추가)
#   [1]경고 완전저장(data/alerts/) [2]결과 자동매칭 [4]고배당 경고학습 [5]경고 적중률
#   ※ [3]경고 신호 추천 반영은 기존 _signal_combo_bets(급락 조합 말 추천)로 이미 동작 →
#      alertSignal 요약을 _triple_analyze 반환에 추가(프론트 배너).
# ═══════════════════════════════════════════════════════════════════
ALERTS_DIR = os.path.join(os.path.dirname(__file__), "data", "alerts")
ALERT_DROP_THRESH = -30.0   # 경고 저장/신호 기준(복승 30%+ 급락)


def _alert_meta(rk):
    m = re.search(r"(\d{4}-\d{2}-\d{2})", rk or "")
    date = m.group(1) if m else time.strftime("%Y-%m-%d", time.localtime())
    race = re.sub(r"\d{4}-\d{2}-\d{2}", "", rk or "").strip() or (rk or "race")
    slug = re.sub(r"[^\w가-힣]+", "_", f"{date}_{race}").strip("_")
    return slug, date, race


def _record_alert(rk, an):
    """[1번] 유의미한 배당급변 경고를 data/alerts/<race>.json 에 완전 기록.
    odds_snapshot(before/after)·경고말·현재추천 저장. 같은 조합쌍은 1회만(중복 방지).
    마감 후·기준값(설정/재설정) 상태는 제외(가짜 급락)."""
    if not an or an.get("afterClose") or an.get("baselineSet") or an.get("baselineReset"):
        return None
    drops = [d for d in (an.get("drops") or [])
             if d.get("pct") is not None and d["pct"] <= ALERT_DROP_THRESH]
    if not drops:
        return None
    slug, date, race = _alert_meta(rk)
    os.makedirs(ALERTS_DIR, exist_ok=True)
    path = os.path.join(ALERTS_DIR, slug + ".json")
    try:
        doc = json.load(open(path, encoding="utf-8"))
    except Exception:
        doc = {"race": race, "date": date, "raceKey": rk, "alerts": [], "result": None}
    top = drops[0]
    pair_key = "+".join(str(x) for x in sorted(int(y) for y in top["combo"]))
    if pair_key in {a.get("alert_pair") for a in doc.get("alerts", [])}:
        return None   # 이 조합쌍 경고는 이미 기록됨
    snapshot, horses = {}, []
    for d in drops[:6]:
        snapshot["+".join(str(x) for x in d["combo"])] = {"before": d.get("prev"), "after": d.get("cur")}
        for h in d["combo"]:
            if int(h) not in horses:
                horses.append(int(h))
    main = next((b for b in (an.get("betRecommend") or []) if b.get("label") == "복승 메인"), None)
    cur_rec = "+".join(str(x) for x in main["combo"]) if main else None
    entry = {"time": time.strftime("%H:%M:%S", time.localtime()), "race": race,
             "alert_type": "배당급변", "alert_content": f"{top['combo'][0]}+{top['combo'][1]} {top['pct']}%",
             "alert_pair": pair_key, "odds_snapshot": snapshot, "alert_horses": horses,
             "current_recommend": cur_rec, "minutes_before": an.get("minutesBefore"),
             "result": None, "alert_correct": None}
    doc["alerts"].append(entry)
    json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return entry


def _match_alerts_to_result(rk, top3, an):
    """[2번] 결과 입력 시 경고 내역과 자동 매칭 — 경고말 입상 여부·경고 무시(추천 누락) 판정.
    반환 {fired, horses, hit, ignored} (학습 레코드/[5]통계·[4]하이라이트 근거)."""
    slug, _, _ = _alert_meta(rk)
    path = os.path.join(ALERTS_DIR, slug + ".json")
    try:
        doc = json.load(open(path, encoding="utf-8"))
    except Exception:
        return None
    top3 = [int(x) for x in top3 if x is not None]
    all_h, any_correct = set(), False
    for a in doc.get("alerts", []):
        ah = [int(h) for h in (a.get("alert_horses") or [])]
        placed = [h for h in ah if h in top3]
        a["result"] = top3
        a["alert_correct"] = bool(placed)
        a["placed_horses"] = placed
        rec_set = set(int(x) for x in re.findall(r"\d+", a.get("current_recommend") or ""))
        a["ignored_miss"] = bool(placed) and not any(h in rec_set for h in placed)
        all_h.update(ah)
        if placed:
            any_correct = True
    doc["result"] = top3
    try:
        json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        print("[경고매칭] 저장 실패:", e)
    return {"fired": bool(doc.get("alerts")), "horses": sorted(all_h), "hit": any_correct,
            "ignored": any(a.get("ignored_miss") for a in doc.get("alerts", []))}


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
    # [신규 1번] 유의미한 배당급변 경고를 data/alerts/ 에 완전 기록(중복 제외)
    try:
        _record_alert(rk, an)
    except Exception as e:
        print("[경고기록] 실패:", e)
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


def _hist_read_any(json_path):
    """[영구보존·4번] odds_history 파일 읽기 — .json 우선, 없으면 7일+ 압축본(.json.gz) 투명 해제.
    미존재 시 None."""
    import gzip
    try:
        if os.path.exists(json_path):
            return json.load(open(json_path, encoding="utf-8"))
        gz = json_path + ".gz"
        if os.path.exists(gz):
            with gzip.open(gz, "rt", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print("[아카이브 읽기] 실패:", json_path, e)
    return None


def _archive_compress_old(days=7):
    """[영구보존·4번] 7일+ 지난 odds_history .json 을 .json.gz 로 압축(데이터 삭제 없이 메모리·용량 관리).
    파일명 날짜(YYYY_MM_DD) 우선, 없으면 mtime 기준. 반환 압축 파일 수."""
    import gzip
    cutoff = time.time() - days * 86400
    n = 0
    try:
        if not os.path.isdir(ODDS_HISTORY_DIR):
            return 0
        for fn in os.listdir(ODDS_HISTORY_DIR):
            if not fn.endswith(".json"):
                continue
            fp = os.path.join(ODDS_HISTORY_DIR, fn)
            old = False
            m = re.match(r"(\d{4})_(\d{2})_(\d{2})", fn)
            if m:
                try:
                    ft = time.mktime(time.strptime("-".join(m.groups()), "%Y-%m-%d"))
                    old = ft < cutoff
                except (ValueError, OverflowError):
                    old = False
            if not old:
                try:
                    old = os.path.getmtime(fp) < cutoff
                except OSError:
                    old = False
            if old:
                try:
                    with open(fp, "rb") as f_in, gzip.open(fp + ".gz", "wb") as f_out:
                        f_out.writelines(f_in)
                    os.remove(fp)     # 원본 .json 제거(데이터는 .gz에 그대로 보존 — 삭제 아님, 압축 보관)
                    n += 1
                except Exception as e:
                    print("[아카이브 압축] 파일 실패:", fn, e)
        if n:
            print(f"[아카이브 압축] {days}일+ 경주 배당 {n}건 .gz 압축 보관(데이터 유지)")
    except Exception as e:
        print("[아카이브 압축] 실패:", e)
    return n


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


EXA_REVERSAL_TOPN = 3   # [다중조합] 영구 기록할 상위 저배당(유력) 쌍승 조합 수


def _exa_fav_dirs(exa_dict):
    """쌍승(방향성) 배당 dict('a+b'→odds) → {무순쌍: (유력방향튜플, 유력배당)}.
    양방향 중 저배당 방향을 '유력(1→2착)'으로 판단. 한 방향만 있으면 그 방향."""
    raw = {}
    for kk, vv in (exa_dict or {}).items():
        try:
            ov = float(vv)
        except (TypeError, ValueError):
            continue
        if ov <= 0:
            continue
        parts = str(kk).split("+")
        if len(parts) != 2:
            continue
        try:
            a, b = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        raw[(a, b)] = ov
    out = {}
    for (a, b), ov in raw.items():
        key = tuple(sorted((a, b)))
        rev = raw.get((b, a))
        fav_dir, fav_odds = (a, b), ov
        if rev is not None and rev < ov:
            fav_dir, fav_odds = (b, a), rev
        rec = out.get(key)
        if rec is None or fav_odds < rec[1]:
            out[key] = (fav_dir, fav_odds)
    return out


def _history_append(rk, quinella, exacta, deadline=None, win=None, baseline_reset=False):
    """경주별 히스토리 파일에 스냅샷 1건 추가. 직전 대비 급락(≤-20%) 이상감지 기록.
    baseline_reset=True(경주 전환 감지)면 이 스냅샷을 새 기준값으로만 저장(이상감지 계산 생략)."""
    path, date, race = _hist_path(rk)
    try:
        doc = json.load(open(path, encoding="utf-8"))
    except Exception:
        doc = {"race": race, "date": date, "raceKey": rk, "snapshots": [], "result": None}
    now = time.time()
    # [영구보존] raceKey별 append-only 아카이브 — 분석용 snapshots(오염방어로 리셋/300캡)와 별개로
    #   모든 수집 배당을 절대 삭제·초기화 없이 영구 누적(탭 전환·경주 전환·마감 후에도 유지 → 복기 소스).
    doc.setdefault("archive_snapshots", [])
    # [경주 전환 잔존 방어·시간 간격] 히스토리 파일의 마지막 스냅샷이 20분+ 전이면 이전 경주 종료 → 새 경주로 간주.
    #   이전 경주 스냅샷·이상감지(anomaly_history)를 완전 제거하고 이 스냅샷을 새 기준값으로만 저장(급락 계산 생략).
    if doc.get("snapshots") and not doc.get("result"):
        _last_snap_t = doc["snapshots"][-1].get("t")
        if _last_snap_t and (now - _last_snap_t) > 1200:
            print(f"[경주전환·시간간격] {rk}: 직전 스냅샷 {round((now - _last_snap_t) / 60)}분 전 → 이전 경주 잔존 {len(doc['snapshots'])}건 제거·새 기준값")
            doc["snapshots"] = []
            baseline_reset = True   # 이 스냅샷 = 새 기준값(이상감지 계산 생략)
            # [영구보존] 아카이브는 삭제하지 않고 세션 경계만 표기(20분+ 갭=발주지연/재수집 구분용).
            if doc["archive_snapshots"]:
                doc["archive_snapshots"].append({"t": now, "boundary": True, "reason": "gap_20min"})
    minutes_before = None
    mb_signed = None       # [마감후] 부호 포함(음수=마감 후) — after_close 판별용
    after_close = False
    try:
        if deadline:
            dl = float(deadline)                     # epoch ms 또는 s
            dl_ms = dl if dl > 1e12 else dl * 1000
            mb = round((dl_ms - now * 1000) / 60000)
            mb_signed = mb
            minutes_before = mb if mb >= 0 else None  # (하위호환) 기존 소비자는 >=0만 사용
            after_close = mb < 0
    except (TypeError, ValueError):
        minutes_before = mb_signed = None
        after_close = False
    curQ = _odds_map_un(quinella)
    curWin = _win_map_int(_win_map_clean(win))
    # ═══ [1번·발주시각 오검출 방어] deadline 역행 감지 ═══
    #   직전 스냅샷보다 발주까지 분(mb_signed)이 3분+ 뒤로 이동(발주시각이 늦춰짐)했고, 직전이 T-2분 이내
    #   (마감 임박/마감 후)로 판정됐던 경우 → 발주시각 오검출로 보고, 과거의 잘못된 마감 상태를 무효화하고
    #   현재(늦은)의 올바른 발주시각으로 재설정. 하코다테 8R: T-1분/마감으로 오판 후 T-7분으로 정정된 케이스.
    #   ⚠ realtime_added·마감후 처리가 오검출된 after_close로 무력화되던 문제까지 함께 해소.
    deadline_corrected = False
    if mb_signed is not None and doc["snapshots"]:
        _prev_mb = doc["snapshots"][-1].get("mb_signed")
        if _prev_mb is not None and (mb_signed - _prev_mb) >= 3 and _prev_mb <= 2:
            deadline_corrected = True
            for _s in doc["snapshots"]:          # 과거 오검출 마감 상태 전부 무효화(실제 발주는 더 뒤)
                if _s.get("after_close"):
                    _s["after_close"] = False
                    _s["deadline_corrected"] = True
    anomalies = []
    q_drops = []           # [3번] 구조화 복승 급락(신호말 산출용)
    signal_horse = None    # [3번] 이 스냅샷의 대표 이상감지 말(집중급락 1순위)
    signal_reason = None
    # [2번 다음경주 혼입 방어] 직전 대비 다수 조합 200%+ 급등 = 경주 종료/다음 경주 배당 유입 → 차단.
    #   차단 스냅샷은 이상감지·신호말 계산을 생략하고 next_race_blocked 표기(타임라인·분석에서 제외).
    next_race_blocked = False
    if doc["snapshots"] and not baseline_reset:
        _lastq = doc["snapshots"][-1].get("quinella")
        if _lastq and _next_race_surge(_lastq, quinella):
            next_race_blocked = True
    # [경주전환 방어] 기준값 재설정이면 직전 스냅샷과 비교하지 않음(다른 경주 잔존 → 오검출 방지)
    # [첫수집 방어] 첫 비교(스냅샷 1건뿐)는 첫 수집 배당이 불안정(못 가져옴/시장 형성 초기 고배당)해
    #   가짜 급락(-90%대)이 뜬다. 스냅샷 2건 이상(2번째 수집을 기준)일 때부터 이상감지 기록.
    if len(doc["snapshots"]) >= 2 and not baseline_reset and not next_race_blocked:
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
                if _is_opening_settle(po, pct):   # [초반미수집] opening 배당 정착(가짜 급락) 제외
                    continue
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
                if _is_opening_settle(po, pct):   # [초반미수집] opening 배당 정착(가짜 급락) 제외
                    continue
                if pct <= -20:
                    anomalies.append(f"급락감지: {'+'.join(map(str, k))} {pct}%")
                if pct <= -8:      # [3번] 신호말 산출용 구조화 급락(집중도 계산)
                    q_drops.append({"combo": list(k), "pct": pct})
        # [3번] 이 스냅샷의 대표 이상감지 말 = 집중급락(초과급락) 1순위(재계산 없이 _excess_drop_analysis 재사용)
        try:
            _exc = _excess_drop_analysis(q_drops, curQ)
            _conc = _exc.get("concentrated") or []
            if _conc:
                signal_horse = int(_conc[0])
                _hi = (_exc.get("horses") or {}).get(signal_horse) or {}
                signal_reason = f"{signal_horse}번 집중급락(평균 {_hi.get('avg')}%)"
        except Exception:
            signal_horse, signal_reason = None, None
        # [이상감지 누적] 쌍승(exacta) 방향 역전 → 영구 기록(마감 후에도 유지).
        #   [다중조합] 상위 EXA_REVERSAL_TOPN개 저배당(유력) 조합 각각의 유력방향이
        #   (a,b)→(b,a)로 뒤집히면 1·2착 예측 역전으로 기록(기존 '최저 1조합'만 → 다중 확장).
        #   무순쌍당 1회, 조합별로 별도 라인(피드는 text 기준 중복제거·별도 표시).
        prev_fav = _exa_fav_dirs(last.get("exacta"))
        cur_fav = _exa_fav_dirs(_combo_dict(exacta))
        cur_top = sorted(cur_fav.items(), key=lambda kv: kv[1][1])[:EXA_REVERSAL_TOPN]
        for _key, (cur_dir, _od) in cur_top:
            pv = prev_fav.get(_key)
            if pv and pv[0] == cur_dir[::-1] and pv[0] != cur_dir:
                anomalies.append(f"쌍승역전: {cur_dir[0]}↔{cur_dir[1]}")
    doc["snapshots"].append({
        "time": time.strftime("%H:%M:%S", time.localtime(now)),
        "minutes_before": minutes_before,
        "mb_signed": mb_signed, "after_close": after_close,   # [마감후] 부호 포함·마감 후 여부
        "deadline_corrected": deadline_corrected,   # [1번] 발주시각 오검출 정정 시점(과거 마감상태 무효화)
        "baseline_reset": bool(baseline_reset),   # [경주전환] 기준값 재설정 시점(변동 계산 제외)
        "next_race_blocked": next_race_blocked,   # [2번] 다음 경주 배당 유입(200%+ 급등) 차단 시점
        "signal_horse": signal_horse,             # [3번] 이 스냅샷 대표 이상감지 말(신호 변경 이력·안정화용)
        "signal_reason": signal_reason,
        "quinella": _combo_dict(quinella), "exacta": _combo_dict(exacta),
        "win": {str(k): v for k, v in curWin.items()},
        "anomalies": anomalies, "t": now,
    })
    doc["snapshots"] = doc["snapshots"][-300:]
    # [영구보존·1·2번] append-only 아카이브에 이 수집 배당을 영구 누적(리셋/캡/탭전환과 무관·절대 삭제 안 함).
    doc["archive_snapshots"].append({
        "time": time.strftime("%H:%M:%S", time.localtime(now)), "t": now,
        "minutes_before": minutes_before, "mb_signed": mb_signed, "after_close": after_close,
        "baseline_reset": bool(baseline_reset), "next_race_blocked": next_race_blocked,
        "quinella": _combo_dict(quinella), "exacta": _combo_dict(exacta),
        "win": {str(k): v for k, v in curWin.items()}, "anomalies": anomalies,
    })
    doc["archive_snapshots"] = doc["archive_snapshots"][-5000:]   # 안전 상한(단일 경주 실사용<150·8시간 연속수집도 미달)
    # [4번] 마감 후 신호로 베팅 반영 불가했던 케이스 저장(수집 간격 단축 필요성 근거)
    #   [1번] 마감 후 대급락(50%+) = surge → 별도 구조 저장(입상률 학습·다음경주 참고)
    if after_close and anomalies:
        try:
            _surge = None
            _big = [d for d in q_drops if (d.get("pct") or 0) <= -50]
            if _big:
                _hc = {}
                for _d in _big:
                    for _h in (_d.get("combo") or []):
                        _hc[_h] = _hc.get(_h, 0) + 1
                _sh = sorted(_hc, key=lambda h: -_hc[h])[:4]
                _surge = {"horses": _sh,
                          "combos": [{"combo": _d.get("combo"), "pct": _d.get("pct")}
                                     for _d in sorted(_big, key=lambda x: (x.get("pct") or 0))[:6]]}
            _record_after_close_case(rk, date, mb_signed, anomalies, _surge)
        except Exception as e:
            print("[마감후학습] 케이스 저장 실패:", e)
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


def _canonical_log_key(rk):
    """[중복 key 근본 방지] 같은 경주가 다른 표기(한글 '모리오카 1경주' / 한자 '2026-07-07 盛岡 1R')로
    이미 오늘 로그가 있으면 **그 기존 key를 재사용**해 한 파일에 합친다(중복 로그·미입력 중복 제거).
    - 자기 파일이 이미 있으면 그대로(rk).
    - 없을 때만 오늘·(트랙+라운드) 일치하는 기존 로그를 파일명으로 스캔(경량, doc 1개만 읽음)."""
    try:
        exact, _, _ = _analysis_log_path(rk)
        if os.path.exists(exact):
            return rk
        area, num = _area_num(rk)
        if num is None or not area or not os.path.isdir(ANALYSIS_LOG_DIR):
            return rk
        today_us = time.strftime("%Y_%m_%d", time.localtime())
        cands = []
        for fn in os.listdir(ANALYSIS_LOG_DIR):
            if not fn.endswith(".json") or not fn.startswith(today_us):
                continue
            race = fn[len(today_us):-5].strip("_").replace("_", " ")
            fa, fnum = _area_num(race)
            if fnum == num and fa and (area in fa or fa in area):
                cands.append(fn)
        if cands:
            cands.sort()   # 가장 이른(먼저 생성된) 파일을 canonical 로
            doc = json.load(open(os.path.join(ANALYSIS_LOG_DIR, cands[0]), encoding="utf-8"))
            ck = doc.get("raceKey") or doc.get("race")
            if ck and ck != rk:
                print(f"[중복방지] '{rk}' → 기존 로그 '{ck}' 에 병합(같은 경주)")
            return ck or rk
    except Exception:
        pass
    return rk


def _build_analysis_log(rk, an=None):
    """_triple_analyze 결과 + odds_history(타임라인/결과) + 전적을 종합해 리치 로그를 만들고 저장.
    기존 로그가 있으면 사용자 입력(analyzed_at·복기 메모·profit)은 보존한다."""
    rec = _triple_load().get(rk) or {}
    if an is None:
        an = _triple_analyze(rk, rec)
    rk = _canonical_log_key(rk)   # [중복 key 근본 방지] 같은 경주면 기존 로그 key 재사용
    path, date, race = _analysis_log_path(rk)
    try:
        doc = json.load(open(path, encoding="utf-8"))
    except Exception:
        doc = {}

    # 배당 타임라인 + 실제 결과/적중(odds_history 에서)
    timeline, result_doc, review_doc = [], None, None
    anomaly_history = []   # [2번] 경주 전체 이상감지 시계열(결과기록 탭 이상감지 내역용, 경주별 분리)
    try:
        hp, _, _ = _hist_path(rk)
        hist = json.load(open(hp, encoding="utf-8"))
        for s in hist.get("snapshots", []):
            timeline.append({"time": s.get("time"), "minutes_before": s.get("minutes_before"),
                             "quinella": s.get("quinella", {})})
        result_doc, review_doc = hist.get("result"), hist.get("review")
        # [2번] 이 경주의 이상감지 전체를 시간순·중복제거로 저장(anomaly-feed 와 동일 규칙)
        for e in _anomaly_events_from_doc(hist):
            anomaly_history.append({"time": e.get("time"), "combo": e.get("combo"),
                                    "drop": e.get("pct"), "text": e.get("text"),
                                    "severity": e.get("severity"),
                                    "minutes_before": e.get("minutes_before")})
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

    # [추천 히스토리 보존] 추천이 바뀔 때마다 이전 추천을 덮어쓰지 않고 누적 저장.
    #   조합/유력마 시그니처가 바뀔 때만 append(같은 추천 반복은 무시) → 6+9→3+7 같은 변경 경위 추적.
    prev_hist = (doc.get("recommendation_history") if doc else None) or []
    now_hms = time.strftime("%H:%M:%S", time.localtime())

    def _rec_sig(fr, kh):
        parts = ["-".join(str(x) for x in sorted(kh or []))]
        for k in ("quinella_main", "quinella_sub", "trifecta_main"):
            parts.append(str((fr.get(k) or {}).get("combo")))
        return "|".join(parts)

    new_sig = _rec_sig(final, an.get("keyHorses"))
    last_sig = prev_hist[-1].get("sig") if prev_hist else None
    rec_history = list(prev_hist)
    # [2번·급락 후 추천 재산출 지속] 추천 sig가 그대로여도, 마지막 추천 이후 새 급락/역전이 감지되면
    #   이력을 갱신한다(하코다테 8R: 13:38 이후 추천 로그가 멈춘 문제 해결). 이상감지 누적 개수가
    #   증가 = 새 신호 유입 → 추천 재계산 결과를 재저장(신호↔추천 관계 추적 유지).
    _anom_count = len(anomaly_history or [])
    _last_anom_count = prev_hist[-1].get("anomCount") if prev_hist else 0
    _new_anomaly = _anom_count > (_last_anom_count or 0)
    # 실질 추천이 있을 때만(빈 추천/워밍업 제외) + (시그니처 변경 OR 새 이상감지 유입) 시 이력 추가
    if final.get("quinella_main") and (new_sig != last_sig or _new_anomaly):
        rec_history.append({
            "time": now_hms,
            "minutes_before": (an.get("lastSnapshot") or {}).get("minutesBefore"),
            "sig": new_sig,
            "anomCount": _anom_count,   # [2번] 이 시점까지 누적 이상감지 수(신규 신호 유입 판별)
            "sigChanged": bool(new_sig != last_sig),   # 추천 조합이 실제로 바뀌었는지(신호만 추가면 False)
            "keyHorses": an.get("keyHorses"),
            "summary": an.get("summary"),
            "quinella_main": (final.get("quinella_main") or {}).get("combo"),
            "quinella_sub": (final.get("quinella_sub") or {}).get("combo"),
            "trifecta_main": (final.get("trifecta_main") or {}).get("combo"),
            "top_signals": [s.get("detail") for s in signals[:3] if s.get("detail")],
        })
    rec_history = rec_history[-50:]   # 무한 증가 방지(최근 50회)

    log = {
        "race_id": os.path.splitext(os.path.basename(path))[0],
        "raceKey": rk,   # [일본경마 복기] 결과 입력 시 record-result 로 그대로 전달(정확 매칭)
        # [분석기록] 종목 태그 저장 → 기록 페이지에서 종목별 검색·필터. 기존 값 보존(재분석 시).
        "sport": rec.get("sport") or (doc.get("sport") if doc else None) or "horse",
        "category": rec.get("category") or (doc.get("category") if doc else None) or "japan_local",
        "date": date, "race": race,
        "analyzed_at": (doc.get("analyzed_at") if doc else None) or time.strftime("%H:%M:%S", time.localtime()),
        "updated_at": time.strftime("%H:%M:%S", time.localtime()),
        "input_data": input_data,
        "odds_timeline": timeline,
        "signals_detected": signals,
        "anomaly_history": anomaly_history,   # [2번] 경주 전체 이상감지 시계열(시간·조합·급락%, 경주별 분리 저장)
        "strong_signals": an.get("strongSignals"),   # [강한 신호 8유형] 유형별 학습(적중률 누적)용 스냅샷
        "compression_pattern": an.get("compressionPattern"),   # [저배당 압축 패턴] 축 패턴 스냅샷
        "third_place_hunt": an.get("thirdPlaceHunt"),   # [배당 3착 자동 발굴] 3착 후보 스냅샷
        "horses": horses,
        "elimination": {"candidates": cand, "eliminated": elim_no, "elimination_reasons": elim_reasons},
        "final_recommendation": final,
        "recommendation_history": rec_history,   # [추천 이력 보존] 변경마다 누적(덮어쓰지 않음)
        "compare_recommendation": an.get("compareRecommend"),   # [비교학습] 이상감지/전적/최종 3종
        "corePicks": an.get("corePicks"),   # [확신도 복승 학습] confQuinellas·confTrifecta 결과 판정용 보존
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


# ───────── [결과 데이터 완벽 저장] 경주별 완전 저장(data/race_results/) + 검증 + 누락추적 ─────────
RACE_RESULTS_DIR = os.path.join(os.path.dirname(__file__), "data", "race_results")


def _race_result_id(rk):
    """raceKey → race_id(파일명). 예: '2026-07-05 사가 8경주' → '2026-07-05_사가_8경주'."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", rk or "")
    date = m.group(1) if m else time.strftime("%Y-%m-%d", time.localtime())
    race = re.sub(r"\d{4}-\d{2}-\d{2}", "", rk or "").strip() or (rk or "race")
    safe = re.sub(r"[^\w가-힣]+", "_", f"{date}_{race}").strip("_")
    return safe, date


def _race_result_path(rk):
    os.makedirs(RACE_RESULTS_DIR, exist_ok=True)
    rid, date = _race_result_id(rk)
    return os.path.join(RACE_RESULTS_DIR, rid + ".json"), rid, date


def _validate_race_result(data):
    """[4번] 데이터 품질 검증 — 마번 1~16, 배당 1.0~9999, 날짜 형식, 필수값. 오류 리스트 반환(빈=정상)."""
    errs = []
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(data.get("date") or "")):
        errs.append("날짜 형식 오류(YYYY-MM-DD)")
    res = data.get("result") or {}
    nums = [res.get(k) for k in ("1st", "2nd", "3rd", "4th") if res.get(k) not in (None, "")]
    for n in nums:
        try:
            if not (1 <= int(n) <= 16):
                errs.append(f"마번 범위 초과: {n}(1~16)")
        except (TypeError, ValueError):
            errs.append(f"마번 숫자 아님: {n}")
    if len(nums) != len(set(int(n) for n in nums if str(n).lstrip('-').isdigit())):
        errs.append("착순 마번 중복")
    # 배당 범위(확정배당·시작배당)
    for label, odds in (("확정복승", (data.get("investment") or {}).get("quinella_odds")),
                        ("확정삼복승", (data.get("investment") or {}).get("trifecta_odds"))):
        if odds not in (None, "", 0):
            try:
                if not (1.0 <= float(odds) <= 9999):
                    errs.append(f"{label} 배당 범위 초과: {odds}(1.0~9999)")
            except (TypeError, ValueError):
                errs.append(f"{label} 배당 숫자 아님: {odds}")
    return errs


def _extract_track_round(rk):
    """raceKey → (track 경마장명, round 경주번호). _area_num 재사용(부경=부산 정규화 포함)."""
    return _area_num(rk)


def _build_race_result(rk, an, record, result, top4, inputs=None):
    """[1번] 경주별 완전 저장 구조 조립(AI 학습용). an/record/odds_history/입력값 종합."""
    inputs = inputs or {}
    rid, date = _race_result_id(rk)
    track, rnd = _extract_track_round(rk)
    # 시작 배당 + 타임라인(odds_history 스냅샷)
    odds_start, timeline = {}, []
    try:
        hp, _, _ = _hist_path(rk)
        hist = json.load(open(hp, encoding="utf-8"))
        snaps = hist.get("snapshots") or []
        if snaps:
            odds_start = {"quinella": snaps[0].get("quinella", {}), "exacta": snaps[0].get("exacta", {})}
        for s in snaps:
            timeline.append({"time": s.get("time"), "minutes_before": s.get("minutes_before"),
                             "quinella": s.get("quinella", {}), "exacta": s.get("exacta", {})})
    except Exception:
        pass
    # 이상감지(급락·초과급락 중심)
    sq = an.get("signalQuality") or {}
    ex_h = (sq.get("excess") or {}).get("horses") or {}
    anomalies = []
    last_t = (an.get("lastSnapshot") or {}).get("time")
    for d in (an.get("drops") or [])[:10]:
        if d.get("pct", 0) < 0:
            _hx = next((h for h in d["combo"] if h in ex_h), d["combo"][0] if d["combo"] else None)
            anomalies.append({"time": last_t, "type": "급락", "combo": d["combo"], "horse": _hx,
                              "drop_rate": d.get("pct"),
                              "excess_drop": (ex_h.get(_hx) or {}).get("excess") if _hx in ex_h else None})
    # 예측(추천·전략·신뢰도)
    elim = an.get("elimination") or {}
    ehorses = elim.get("horses") or []
    cand = [h["no"] for h in ehorses if h.get("keep") or h.get("override")] or (an.get("keyHorses") or [])
    elim_no = [h["no"] for h in ehorses if not (h.get("keep") or h.get("override"))]
    rec_bets = an.get("betRecommend") or []
    _main = next((b for b in rec_bets if b.get("label") == "복승 메인"), None)
    _sub = next((b for b in rec_bets if b.get("label") == "복승 보조"), None)
    bmed = an.get("bmed") or {}
    conf_h = ((sq.get("signalConfidence") or {}).get("horses")) or {}
    top_conf = max([(conf_h.get(h) or {}).get("confidence", 0) for h in cand] or [0])
    _cq = (record.get("hit_basis") or {})
    prediction = {
        "candidates": cand, "eliminated": elim_no,
        "recommend_main": "+".join(map(str, _main["combo"])) if _main else None,
        "recommend_sub": "+".join(map(str, _sub["combo"])) if _sub else None,
        "strategy": ("BMED_" + bmed.get("strategy")) if bmed.get("strategy") else None,
        "signal_quality": (next((b.get("signalQuality") for b in rec_bets if b.get("signalQuality")), None)),
        "confidence": round(top_conf, 1),
        "inverse": bool((an.get("inverse") or {}).get("detected")),
    }
    # 결과 분석(적중/패턴 태그)
    main_hit = bool(record.get("quinella_hit"))
    sub_hit = False
    if _sub and result:
        _t2 = sorted([result.get("1st"), result.get("2nd")]) if result.get("2nd") else []
        sub_hit = bool(_t2 and sorted(_sub["combo"]) == [x for x in _t2 if x is not None])
    result_analysis = {
        "main_hit": main_hit, "sub_hit": sub_hit,
        "hit_reason": (record.get("hit_basis") or {}).get("reason") if (main_hit or record.get("was_hit")) else None,
        "miss_reason": None if record.get("was_hit") else "추천 조합 미입상",
        "pattern_tags": record.get("patterns") or [],
        "anomaly_correct": record.get("anomaly_was_correct"),
        "form_pick_hit": record.get("form_pick_hit"),
    }
    inv = {
        "budget": _safe_num(inputs.get("budget")) or 0,
        "main_bet": _safe_num(inputs.get("main_bet")) or 0,
        "sub_bet": _safe_num(inputs.get("sub_bet")) or 0,
        "quinella_odds": _safe_num(inputs.get("quinella_odds")),
        "trifecta_odds": _safe_num(inputs.get("trifecta_odds")),
        "actual_return": record.get("payout_actual"),
        "profit": record.get("pnl"),
    }
    return {
        "race_id": rid, "raceKey": rk, "date": date,
        "track": track, "round": rnd,
        "distance": inputs.get("distance") or record.get("distance"),
        "track_condition": inputs.get("track_condition"),
        "weather": inputs.get("weather"),
        "horse_count": inputs.get("horse_count") or len(an.get("form") or []) or None,
        "result": {k: result.get(k) for k in ("1st", "2nd", "3rd", "4th") if result.get(k) not in (None, "")},
        "odds_at_start": odds_start,
        "odds_timeline": timeline,
        "anomalies": anomalies,
        "prediction": prediction,
        "result_analysis": result_analysis,
        "investment": inv,
        "memo": (inputs.get("memo") or "").strip() or None,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
    }


def _save_race_result(rk, an, record, result, top4, inputs=None):
    """[1·4번] 완전 저장 파일 생성 + 검증 + 중복 방지(같은 race_id 덮어쓰기). 반환 {ok,path,errors}."""
    try:
        data = _build_race_result(rk, an, record, result, top4, inputs)
    except Exception as e:
        print("[결과저장] 조립 실패:", e)
        return {"ok": False, "errors": [f"조립 실패: {e}"]}
    errors = _validate_race_result(data)
    data["validation"] = {"ok": not errors, "errors": errors}
    path, rid, _ = _race_result_path(rk)
    try:
        json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        print("[결과저장] 파일 저장 실패:", e)
        return {"ok": False, "errors": [f"저장 실패: {e}"]}
    if errors:
        print(f"[결과저장] ⚠️ 검증 경고 {rid}: {errors}")
    return {"ok": not errors, "path": path, "race_id": rid, "errors": errors}


def _missing_results(date=None):
    """[3번] 당일 분석했으나 결과 미입력 경주 추적. analysis_log 스캔 → 결과 없는 raceKey 목록."""
    date = date or time.strftime("%Y-%m-%d", time.localtime())
    missing, done = [], set()
    # 결과 저장된 race_id 집합
    if os.path.isdir(RACE_RESULTS_DIR):
        for fn in os.listdir(RACE_RESULTS_DIR):
            if fn.endswith(".json"):
                done.add(os.path.splitext(fn)[0])
    if not os.path.isdir(ANALYSIS_LOG_DIR):
        return {"date": date, "missing": [], "count": 0}
    for fn in sorted(os.listdir(ANALYSIS_LOG_DIR)):
        if not fn.endswith(".json"):
            continue
        try:
            d = json.load(open(os.path.join(ANALYSIS_LOG_DIR, fn), encoding="utf-8"))
        except Exception:
            continue
        if d.get("date") != date:
            continue
        rk = d.get("raceKey") or d.get("race")
        rid, _ = _race_result_id(rk) if rk else (os.path.splitext(fn)[0], date)
        has_result = bool(d.get("result")) or (rid in done)
        if not has_result and rk and "TEST" not in (rk or "").upper():
            # [신규] 추천 요약(삼복승 우선→복승) + 이상감지 여부 + 마지막 갱신시각(발주 근접 알림용)
            fr = d.get("final_recommendation") or {}
            _tm = (fr.get("trifecta_main") or {}).get("combo")
            _qm = (fr.get("quinella_main") or {}).get("combo")
            recommend = ("삼복승 " + str(_tm)) if _tm else (("복승 " + str(_qm)) if _qm else "추천 없음")
            had_anomaly = any((s or {}).get("severity") == "🔴" for s in (d.get("signals_detected") or []))
            missing.append({"raceKey": rk, "race": d.get("race"), "race_id": rid,
                            "analyzed_at": d.get("analyzed_at"), "updated_at": d.get("updated_at"),
                            "recommend": recommend, "hadAnomaly": had_anomaly})
    # 최근 분석 순(갱신시각 내림차순)
    missing.sort(key=lambda m: (m.get("updated_at") or m.get("analyzed_at") or ""), reverse=True)
    return {"date": date, "missing": missing, "count": len(missing)}


# ───────── [AI 분석 Phase1] AI 학습용 완전 데이터(data/ai_training/) + 품질검증 + 현황 ─────────
AI_TRAINING_DIR = os.path.join(os.path.dirname(__file__), "data", "ai_training")
AI_TARGET_RACES = 500   # [3번] 목표 경주 수
# [AI 데이터 정비] 학습 데이터 스키마 버전 — 구조 변경 시 올려서 하위호환/마이그레이션 판단에 사용.
#   변경 이력은 chrome-extension 밖 docs/AI_DATA_SCHEMA.md 참조.
AI_SCHEMA_VERSION = "1.0"


def _ai_training_path(rk):
    os.makedirs(AI_TRAINING_DIR, exist_ok=True)
    rid, _ = _race_result_id(rk)
    return os.path.join(AI_TRAINING_DIR, rid + ".json"), rid


def _odds_range_label(o):
    if o is None:
        return None
    return "저배당" if o < 3 else ("중배당" if o < 7 else "고배당")


def _build_ai_training(rk, an, record, result, top4, inputs=None):
    """[1번] AI 학습용 완전 데이터 구조 조립 — 말별 피처 + 배당 피처 + 예측 + 결과 + 라벨."""
    inputs = inputs or {}
    rid, date = _race_result_id(rk)
    track, rnd = _extract_track_round(rk)
    sq = an.get("signalQuality") or {}
    excess = sq.get("excess") or {}
    ex_h = excess.get("horses") or {}
    adv = sq.get("advanced") or {}
    inv = an.get("inverse") or {}
    wx = sq.get("winExactaReversals") or []
    mm = sq.get("quinellaMismatch") or {}

    # 말별 피처(전적·기수·거리 등 — 미수집 항목은 null, 스키마 미래 대비)
    win = an.get("single") or {}
    horses = []
    for f in (an.get("form") or []):
        no = f.get("no")
        horses.append({
            "no": no, "name": f.get("name") or "", "jockey": f.get("jockey") or "",
            "jockey_winrate": _jockey_place_rate(f.get("jockey")),
            "recent_results": _placings_list(f.get("recentPlacings")),
            "record_score": f.get("totalScore"),
            "distance_score": f.get("distanceScore"),        # 미수집 → None
            "interval_days": f.get("intervalDays"),           # 미수집 → None
            "weight_change": f.get("weightChange"),           # 미수집 → None
            "odds": win.get(str(no)) if isinstance(win, dict) else None,
        })

    # 배당 피처(이미 계산된 신호 재사용)
    drop_rates, excess_drops, drop_speed, consecutive = {}, {}, {}, {}
    for d in (an.get("drops") or []):
        if d.get("pct") is not None and d["pct"] < 0:
            for h in d["combo"]:
                if str(h) not in drop_rates or d["pct"] < drop_rates[str(h)]:
                    drop_rates[str(h)] = d["pct"]
    for no, e in ex_h.items():
        if e.get("excess") is not None and e["excess"] < 0:
            excess_drops[str(no)] = e["excess"]
    for v in adv.get("velocity", []):
        for h in v["combo"]:
            key = str(h)
            if key not in drop_speed or v["speed"] > abs(drop_speed[key]):
                drop_speed[key] = -abs(v["speed"])
    for _key, st in (adv.get("streaks") or {}).items():
        if st.get("type") == "연속하락":
            for h in st["combo"]:
                consecutive[str(h)] = max(consecutive.get(str(h), 0), 3)
    ov = adv.get("overround") or {}
    refund_rate = None
    if ov.get("invSum"):
        refund_rate = round(min(1.0, 1.0 / ov["invSum"]), 3) if ov["invSum"] > 1 else round(ov["invSum"], 3)
    odds_features = {
        "timeline": [{"time": s.get("time"), "quinella": s.get("quinella", {})}
                     for s in _ai_timeline(rk)],
        "drop_rates": drop_rates, "excess_drops": excess_drops, "drop_speed": drop_speed,
        "exacta_reversal": bool(wx) or any(r.get("flipped") for r in (an.get("reversals") or [])),
        "reversal_ratio": (wx[0]["ratio"] if wx else None),
        "quinella_mismatch": mm.get("ratio"),
        "refund_rate": refund_rate,
        "consecutive_drops": consecutive,
        "fake_betting": bool(adv.get("fakes")),
        "large_scale_drop": bool(an.get("massDrop")),
    }

    # 예측
    elim = an.get("elimination") or {}
    ehorses = elim.get("horses") or []
    cand = [h["no"] for h in ehorses if h.get("keep") or h.get("override")] or (an.get("keyHorses") or [])
    elim_no = [h["no"] for h in ehorses if not (h.get("keep") or h.get("override"))]
    rec_bets = an.get("betRecommend") or []
    _main = next((b for b in rec_bets if b.get("label") == "복승 메인"), None)
    bmed = an.get("bmed") or {}
    conf_h = ((sq.get("signalConfidence") or {}).get("horses")) or {}
    top_conf = max([(conf_h.get(h) or {}).get("confidence", 0) for h in cand] or [0])
    prediction = {
        "candidates": cand, "eliminated": elim_no,
        "recommend": "+".join(map(str, _main["combo"])) if _main else None,
        "strategy": ("BMED_" + bmed.get("strategy")) if bmed.get("strategy") else None,
        "confidence": round(top_conf, 1),
        "signal_quality": next((b.get("signalQuality") for b in rec_bets if b.get("signalQuality")), None),
    }

    # 결과 + 라벨
    q_odds = _safe_num(inputs.get("quinella_odds"))
    result_block = {
        "1st": result.get("1st"), "2nd": result.get("2nd"),
        "3rd": result.get("3rd"), "4th": result.get("4th"),
        "quinella_odds": q_odds, "exacta_odds": _safe_num(inputs.get("exacta_odds")),
        "hit": bool(record.get("was_hit")),
        "hit_pattern": (record.get("hit_basis") or {}).get("reason") if record.get("was_hit") else None,
    }
    labels = {
        "quinella_hit": bool(record.get("quinella_hit")),
        "winner": result.get("1st"), "second": result.get("2nd"),
        "odds_range": _odds_range_label(q_odds),
    }
    # [기준치 도출용] 신호별 대표말이 실제 입상(1~3착)했는지 — 기준치별 적중률 계산의 재료.
    _top3 = set(int(x) for x in (result.get("1st"), result.get("2nd"), result.get("3rd")) if x not in (None, ""))

    def _placed(h):
        try:
            return int(h) in _top3
        except (TypeError, ValueError):
            return False
    _ex_horse = None
    if excess_drops:
        _ex_horse = min(excess_drops.items(), key=lambda kv: kv[1])[0]   # 가장 큰 초과급락 말
    _rev_horse = (wx[0].get("challenger") if wx else None)
    _con_horse = next((h for h, c in consecutive.items() if c >= 3), None)
    signal_hit = {
        "excess_drop_horse": _ex_horse, "excess_drop_horse_placed": (_placed(_ex_horse) if _ex_horse else None),
        "excess_drop_value": (excess_drops.get(_ex_horse) if _ex_horse else None),
        "reversal_horse": _rev_horse, "reversal_horse_placed": (_placed(_rev_horse) if _rev_horse is not None else None),
        "reversal_ratio": (wx[0]["ratio"] if wx else None),
        "consecutive_horse": _con_horse, "consecutive_horse_placed": (_placed(_con_horse) if _con_horse else None),
    }
    data = {
        "schema_version": AI_SCHEMA_VERSION,   # [AI 데이터 정비] 스키마 버전 명시(마이그레이션 대비)
        "race_id": rid,
        "race_info": {
            "date": date, "track": track, "round": rnd,
            "distance": inputs.get("distance"), "condition": inputs.get("track_condition"),
            "weather": inputs.get("weather"),
            "horse_count": inputs.get("horse_count") or len(horses) or None, "grade": inputs.get("grade"),
        },
        "horses": horses,
        "odds_features": odds_features,
        "thresholds_used": {                    # [기준치 도출] 이 경주 판정에 쓴 현재 기준치 스냅샷
            # ⚠ ABS_STRONG(-10.0)은 _excess_drop_analysis 지역 상수라 이 스코프에서 접근 불가(NameError) → 리터럴 사용.
            "excess_drop_min": 10.0, "reversal_ratio_max": 0.95,
            "consecutive_min": 3, "pre_close_min": 30,
        },
        "prediction": prediction,
        "result": result_block,
        "labels": labels,
        "signal_hit": signal_hit,               # [기준치 도출] 신호말 입상 여부(기준치별 적중률 재료)
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
    }
    data["quality"] = _ai_quality_score(data)
    return data


def _ai_timeline(rk):
    """odds_history 스냅샷 → 학습용 배당 타임라인."""
    try:
        hp, _, _ = _hist_path(rk)
        return (json.load(open(hp, encoding="utf-8")).get("snapshots") or [])
    except Exception:
        return []


def _ai_quality_score(data):
    """[5번] 데이터 품질 점수 — 필수 5항목 각 20점(합 100). 등급:
      80+ AI학습용 / 60~79 참고용 / 60미만 제외. 범위 오류(마번·배당·날짜) 시 '제외'로 강등.
    필수: ①배당 타임라인(2회+) ②이상감지 결과 ③전적 데이터 ④결과(1~4착) ⑤확정 배당."""
    ri = data.get("race_info") or {}
    of = data.get("odds_features") or {}
    res = data.get("result") or {}
    horses = data.get("horses") or []
    checks = [
        ("배당 타임라인(2회+)", len(of.get("timeline") or []) >= 2),
        ("이상감지 결과", bool(of.get("drop_rates") or of.get("excess_drops")
                          or of.get("exacta_reversal") or of.get("large_scale_drop")
                          or of.get("quinella_mismatch") is not None)),
        ("전적 데이터", any(h.get("record_score") is not None for h in horses)),
        ("결과(1~3착)", all(res.get(k) not in (None, "") for k in ("1st", "2nd", "3rd"))),
        ("확정 배당", res.get("quinella_odds") not in (None, "", 0)),
    ]
    score = sum(20 for _label, ok in checks if ok)
    missing = [label for label, ok in checks if not ok]
    # 범위 검증(오류) — 있으면 학습 부적합('제외')
    errors = []
    for k in ("1st", "2nd", "3rd", "4th"):
        n = res.get(k)
        if n not in (None, ""):
            try:
                if not (1 <= int(n) <= 16):
                    errors.append(f"마번 범위 초과: {k}={n}")
            except (TypeError, ValueError):
                errors.append(f"마번 숫자 아님: {k}={n}")
    for k in ("quinella_odds", "exacta_odds"):
        o = res.get(k)
        if o not in (None, "", 0):
            try:
                if not (1.0 <= float(o) <= 9999):
                    errors.append(f"배당 범위 초과: {k}={o}")
            except (TypeError, ValueError):
                errors.append(f"배당 숫자 아님: {k}={o}")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(ri.get("date") or "")):
        errors.append("날짜 형식 오류")
    if errors:
        grade = "제외"
    elif score >= 80:
        grade = "AI학습용"
    elif score >= 60:
        grade = "참고용"
    else:
        grade = "제외"
    return {"score": score, "grade": grade, "complete": grade == "AI학습용",
            "ai_ready": grade == "AI학습용", "missing": missing, "errors": errors}


def _save_ai_training(rk, an, record, result, top4, inputs=None):
    """[1·2번] AI 학습 완전 데이터 저장 + 품질검증(중복=같은 race_id 덮어쓰기)."""
    try:
        data = _build_ai_training(rk, an, record, result, top4, inputs)
    except Exception as e:
        print("[AI학습저장] 조립 실패:", e)
        return {"ok": False, "errors": [str(e)]}
    path, rid = _ai_training_path(rk)
    try:
        json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        print("[AI학습저장] 파일 저장 실패:", e)
        return {"ok": False, "errors": [str(e)]}
    q = data.get("quality") or {}
    return {"ok": True, "race_id": rid, "score": q.get("score"),
            "grade": q.get("grade"), "complete": q.get("complete")}


# ───────── [기준치 도출 시스템] 누적 ai_training 데이터 → 신호 기준치별 적중률 → 최적 기준치 추천 ─────────
#   ⚠ 자동 적용 금지 — 사람이 검토 후 승인(버튼)해야 config에 반영. 기존 기준치는 백업 보관.
THRESHOLD_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "data", "thresholds_config.json")
THRESHOLD_SAMPLE_MIN = 50   # 이 표본 미만은 '표본 부족'(적용 검토 불가)
_THRESHOLD_DEFAULTS = {"excess_drop_min": 10, "reversal_ratio_max": 0.90,
                       "consecutive_min": 2, "pre_close_min": 30}


def _threshold_config_load():
    try:
        cfg = json.load(open(THRESHOLD_CONFIG_FILE, encoding="utf-8"))
    except Exception:
        cfg = {}
    return {**_THRESHOLD_DEFAULTS, **(cfg.get("current") or {}),
            "history": cfg.get("history") or [], "_raw": cfg}


def _threshold_config_save(current, applied_note=None, prev=None):
    """current 기준치 저장 + 이전값 history 백업(감사·롤백용)."""
    os.makedirs(os.path.dirname(THRESHOLD_CONFIG_FILE), exist_ok=True)
    cfg = _threshold_config_load().get("_raw") or {}
    hist = cfg.get("history") or []
    if prev is not None:
        hist.append({"current": prev, "replaced_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                     "note": applied_note})
    cfg["current"] = current
    cfg["history"] = hist[-50:]
    cfg["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    json.dump(cfg, open(THRESHOLD_CONFIG_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return cfg


def _ai_rows_for_thresholds():
    """ai_training 파일에서 기준치 계산에 필요한 필드만 추출(결과 입력된 경주만)."""
    rows = []
    if not os.path.isdir(AI_TRAINING_DIR):
        return rows
    for fn in os.listdir(AI_TRAINING_DIR):
        if not fn.endswith(".json"):
            continue
        try:
            d = json.load(open(os.path.join(AI_TRAINING_DIR, fn), encoding="utf-8"))
        except Exception:
            continue
        of = d.get("odds_features") or {}
        sh = d.get("signal_hit") or {}
        lb = d.get("labels") or {}
        res = d.get("result") or {}
        top3 = set(int(x) for x in (res.get("1st"), res.get("2nd"), res.get("3rd")) if x not in (None, ""))
        # [기존 파일 폴백] signal_hit 없으면 excess_drops 최대말 + result 로 입상 여부 역산.
        ex_val = sh.get("excess_drop_value")
        ex_placed = sh.get("excess_drop_horse_placed")
        if ex_val is None and (of.get("excess_drops")):
            _eh, ex_val = min((of["excess_drops"]).items(), key=lambda kv: kv[1])
            try:
                ex_placed = int(_eh) in top3
            except (TypeError, ValueError):
                ex_placed = None
        rows.append({
            "excess": ex_val, "excess_placed": ex_placed,
            "reversal_ratio": sh.get("reversal_ratio") or of.get("reversal_ratio"),
            "reversal_placed": sh.get("reversal_horse_placed"),
            "quinella_hit": lb.get("quinella_hit"),
        })
    return rows


def _threshold_candidates():
    """[2·3·5번] 누적 데이터로 신호 기준치별 적중률 계산 → 최적 기준치 추천 + 현재 대비 개선폭.
      초과급락(5/10/12/15/20%+)·역배열(0.95/0.90/0.85/0.80/0.70 미만) 각각 테스트."""
    rows = _ai_rows_for_thresholds()
    cur = _threshold_config_load()
    n = len(rows)

    def _rate(fired_placed):
        f = len(fired_placed)
        h = sum(1 for p in fired_placed if p)
        return (f, h, (round(h / f * 100) if f else None))

    # 초과급락: excess 값(음수)이 -X 이하이면 발화 → 그 말 입상률
    ex_out = []
    for x in (5, 10, 12, 15, 20):
        fp = [r["excess_placed"] for r in rows
              if r.get("excess") is not None and r["excess"] <= -x and r.get("excess_placed") is not None]
        f, h, rate = _rate(fp)
        ex_out.append({"threshold": x, "fired": f, "hit": h, "rate": rate})
    # 역배열: reversal_ratio < X 이면 발화 → 그 말 입상률
    rv_out = []
    for x in (0.95, 0.90, 0.85, 0.80, 0.70):
        fp = [r["reversal_placed"] for r in rows
              if r.get("reversal_ratio") is not None and r["reversal_ratio"] < x and r.get("reversal_placed") is not None]
        f, h, rate = _rate(fp)
        rv_out.append({"threshold": x, "fired": f, "hit": h, "rate": rate})

    def _best(cands, cur_val, key_name):
        # 표본 THRESHOLD_SAMPLE_MIN+ 인 후보 중 적중률 최고. 현재값 적중률과 비교.
        elig = [c for c in cands if (c["fired"] or 0) >= THRESHOLD_SAMPLE_MIN and c["rate"] is not None]
        best = max(elig, key=lambda c: c["rate"]) if elig else None
        cur_c = next((c for c in cands if abs(c["threshold"] - cur_val) < 1e-9), None)
        cur_rate = cur_c["rate"] if cur_c else None
        status = "표본부족"
        if best and best["fired"] >= THRESHOLD_SAMPLE_MIN:
            status = "검토가능"
        improve = (best["rate"] - cur_rate) if (best and cur_rate is not None) else None
        return {"key": key_name, "current": cur_val, "currentRate": cur_rate,
                "recommended": (best["threshold"] if best else None),
                "recommendedRate": (best["rate"] if best else None),
                "improve": improve, "status": status, "candidates": cands,
                "sampleMin": THRESHOLD_SAMPLE_MIN}
    return {
        "raceCount": n,
        "excess_drop": _best(ex_out, cur.get("excess_drop_min", 10), "excess_drop_min"),
        "reversal": _best(rv_out, cur.get("reversal_ratio_max", 0.90), "reversal_ratio_max"),
        "current": {k: cur.get(k) for k in _THRESHOLD_DEFAULTS},
        "note": "표본 " + str(THRESHOLD_SAMPLE_MIN) + "회 미만은 참고만 · 적용은 수동 승인(버튼)",
    }


def _ai_data_status():
    """[3·7번] AI 학습 데이터 현황 — 수집/고품질/평균품질·목표 진행률·마일스톤(100/500) 예상 일정."""
    total, high_q, score_sum, days = 0, 0, 0, set()
    schema_counts = {}   # [AI 데이터 정비] 스키마 버전별 개수(구버전=마이그레이션 대상 파악)
    if os.path.isdir(AI_TRAINING_DIR):
        for fn in os.listdir(AI_TRAINING_DIR):
            if not fn.endswith(".json"):
                continue
            try:
                d = json.load(open(os.path.join(AI_TRAINING_DIR, fn), encoding="utf-8"))
            except Exception:
                continue
            total += 1
            sv = d.get("schema_version") or "legacy"   # 버전 없는 구파일 = legacy
            schema_counts[sv] = schema_counts.get(sv, 0) + 1
            q = d.get("quality") or {}
            score_sum += q.get("score") or 0
            if q.get("ai_ready") or q.get("complete"):   # 80점+ AI학습용(고품질)
                high_q += 1
            dt = (d.get("race_info") or {}).get("date")
            if dt:
                days.add(dt)
    progress = round(total / AI_TARGET_RACES * 100, 1) if AI_TARGET_RACES else None
    per_day = (total / len(days)) if days else 0

    def _eta_days(target):
        r = max(0, target - total)
        return (0 if r == 0 else (round(r / per_day) if per_day > 0 else None))
    return {
        "collected": total, "complete": high_q, "high_quality": high_q,
        "complete_pct": round(high_q / total * 100, 1) if total else 0,
        "avg_quality": round(score_sum / total, 1) if total else 0,
        "schema_version": AI_SCHEMA_VERSION, "schema_counts": schema_counts,   # [AI 데이터 정비]
        "target": AI_TARGET_RACES, "progress": progress,
        "remaining": max(0, AI_TARGET_RACES - total),
        "days_collected": len(days), "per_day": round(per_day, 1),
        "eta_months": (round(_eta_days(AI_TARGET_RACES) / 30, 1)
                       if _eta_days(AI_TARGET_RACES) not in (None, 0) else _eta_days(AI_TARGET_RACES)),
        # [7번] 마일스톤: 패턴 발견 100경주 · 모델 학습 500경주
        "milestones": {
            "pattern_discovery": {"target": 100, "reached": total >= 100, "eta_days": _eta_days(100)},
            "model_training": {"target": 500, "reached": total >= 500, "eta_days": _eta_days(500)},
        },
    }


# ───────── [6번] 일별 자동 요약(data/daily_summary/YYYY-MM-DD.json) ─────────
DAILY_SUMMARY_DIR = os.path.join(os.path.dirname(__file__), "data", "daily_summary")


def _build_daily_summary(date=None):
    """[6번] 당일 경주 요약 저장 — 총경주·평균품질·이상감지·적중·적중률·AI준비 경주·누적."""
    date = date or time.strftime("%Y-%m-%d", time.localtime())
    total_races, q_sum, anomalies, hits, ai_ready = 0, 0, 0, 0, 0
    cumulative = 0
    if os.path.isdir(AI_TRAINING_DIR):
        for fn in os.listdir(AI_TRAINING_DIR):
            if not fn.endswith(".json"):
                continue
            try:
                d = json.load(open(os.path.join(AI_TRAINING_DIR, fn), encoding="utf-8"))
            except Exception:
                continue
            cumulative += 1
            if (d.get("race_info") or {}).get("date") != date:
                continue
            total_races += 1
            q = d.get("quality") or {}
            q_sum += q.get("score") or 0
            if q.get("ai_ready"):
                ai_ready += 1
            of = d.get("odds_features") or {}
            anomalies += len(of.get("drop_rates") or {})
            if (d.get("result") or {}).get("hit"):
                hits += 1
    summary = {
        "date": date, "total_races": total_races,
        "data_quality": round(q_sum / total_races) if total_races else 0,
        "anomalies_detected": anomalies, "hits": hits,
        "hit_rate": round(hits / total_races * 100, 1) if total_races else 0.0,
        "ai_ready_races": ai_ready, "cumulative_total": cumulative,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
    }
    try:
        os.makedirs(DAILY_SUMMARY_DIR, exist_ok=True)
        json.dump(summary, open(os.path.join(DAILY_SUMMARY_DIR, date + ".json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    except Exception as e:
        print("[일별요약] 저장 실패:", e)
    return summary


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


# ══════════════ [데이터 보호] 학습 코퍼스 자동 GitHub 백업 (결과 입력마다) ══════════════
#   [1번] race_results·analysis_log·ai_training 등 추적 코퍼스를 결과 입력 시 자동 커밋+푸시.
#   [2번] 5초 디바운스로 연속 입력(일괄 등록)을 1회 커밋으로 묶어 git index.lock 충돌 방지.
#   ⚠ commit은 지정 경로(pathspec)만 대상 → 개발 중 스테이징된 코드 변경을 쓸어담지 않는다.
DATA_BACKUP_PATHS = [
    "data/race_results", "data/analysis_log", "data/ai_training",
    "data/pattern_learning.json", "data/discovered_patterns.json",
    "data/daily_summary", "data/race_report",
    "data/korea_history", "data/prerace",
    "data/daily_learning", "data/high_odds_review",
]
_data_backup_lock = threading.Lock()        # git 작업 직렬화(index.lock 충돌 방지)
_data_backup_timer = None                   # 디바운스 타이머
_data_backup_timer_lock = threading.Lock()


def _run_data_git_backup(label):
    """[데이터 보호] 추적 데이터 경로만 git add + commit(pathspec) + push. 실패는 조용히 로그."""
    if not _data_backup_lock.acquire(blocking=False):
        # 이미 백업 진행 중 → 이번 트리거는 건너뜀(진행 중 백업이 최신 디스크 상태를 담음)
        return
    try:
        root = os.path.dirname(os.path.abspath(__file__))
        paths = [p for p in DATA_BACKUP_PATHS if os.path.exists(os.path.join(root, p))]
        if not paths:
            return
        subprocess.run(["git", "add"] + paths, cwd=root, timeout=60, capture_output=True)
        msg = (label or "데이터 자동 백업").strip()
        # pathspec commit: 지정 경로 변경만 커밋(다른 스테이징 변경 미포함) → 안전
        r = subprocess.run(["git", "commit", "-m", msg, "--"] + paths,
                           cwd=root, timeout=60, capture_output=True, text=True)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if r.returncode != 0:
            if any(k in out for k in ("nothing to commit", "no changes", "변경 사항", "커밋할", "working tree clean")):
                return   # 변경 없음(정상)
            print("[데이터백업] commit 건너뜀:", out[:200])
            return
        pr = subprocess.run(["git", "push"], cwd=root, timeout=120, capture_output=True, text=True)
        if pr.returncode != 0:
            print("[데이터백업] ⚠ push 실패(원격/인증/분기 diverge?):", (pr.stderr or "").strip()[:200])
        else:
            print(f"[데이터백업] ✅ GitHub 반영: {msg}")
    except Exception as e:
        print("[데이터백업] 예외:", e)
    finally:
        _data_backup_lock.release()


def _data_git_backup(label, delay=5.0):
    """[2번] 결과 입력마다 호출 — delay초 디바운스 후 1회 백업(연속 입력 묶음·응답 지연 없음).
    데몬 타이머라 결과 입력 API를 블로킹하지 않는다. 새 호출이 오면 이전 타이머를 취소·재예약."""
    global _data_backup_timer
    try:
        with _data_backup_timer_lock:
            if _data_backup_timer is not None:
                _data_backup_timer.cancel()
            _data_backup_timer = threading.Timer(max(0.5, float(delay)), _run_data_git_backup, args=[label])
            _data_backup_timer.daemon = True
            _data_backup_timer.start()
    except Exception as e:
        print("[데이터백업] 예약 실패:", e)


# [데이터 자동백업 완성] 결과 입력이 없어도 주기적으로 안전 백업.
#   기존 백업은 결과 입력 시에만 트리거 → 장시간 분석만 하고 결과 미입력이면 분석로그가 백업 안 됨.
#   주기 데몬 스레드가 _run_data_git_backup 을 호출(변경 없으면 no-op).
#   [설정화] .env 의 BACKUP_INTERVAL_HOURS 로 조정(기본 6시간·최소 0.5시간). 잘못된 값은 기본으로 폴백.
def _backup_interval_seconds():
    try:
        hours = float(os.environ.get("BACKUP_INTERVAL_HOURS", "") or 6)
    except (TypeError, ValueError):
        hours = 6.0
    return int(max(0.5, hours) * 3600)   # 최소 0.5시간(1800초) 클램프 — 폭주 방지


_PERIODIC_BACKUP_INTERVAL = _backup_interval_seconds()
_periodic_backup_started = False


def _start_periodic_backup(interval=None):
    global _periodic_backup_started
    if _periodic_backup_started:
        return
    _periodic_backup_started = True
    iv = int(interval or _PERIODIC_BACKUP_INTERVAL)

    def _loop():
        while True:
            try:
                time.sleep(iv)
                _run_data_git_backup(f"주기적 안전 백업({iv // 3600}시간)")
            except Exception as e:
                print("[주기백업] 예외:", e)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    print(f"[주기백업] {iv // 3600}시간 주기 데이터 안전 백업 스레드 시작")


# ═════════ [학습일지] 일별 학습 일지 + 빅데이터 집계(data/daily_learning/YYYY-MM-DD.json) ═════════
#   기존 daily_summary(수치 요약)·learning.json(통계)은 그대로 두고, 그 위에 '학습 일지'를 별도 저장:
#     · results_summary: 총경주·적중·적중률·손익  (_build_daily_summary + 결과파일 investment.profit 재사용)
#     · key_learnings / missed_opportunities / pattern_discoveries / system_improvements / tomorrow_focus (정성)
#     · bigdata: 경마장별·출전두수별·마감시간대별·경주등급별·주로/날씨별 집계  ([2번] 빅데이터)
#     · cumulative_pattern_reliability: 마감급락·쌍승역전·전적유력마 누적 적중률  ([3번] 대시보드)
#   ⚠ 재생성해도 정성/수동 내용은 병합 보존(삭제 금지). extra(수동/API 입력)가 있으면 해당 항목만 갱신.
DAILY_LEARNING_DIR = os.path.join(os.path.dirname(__file__), "data", "daily_learning")


def _dl_time_zone(saved_at):
    """'YYYY-MM-DD HH:MM:SS' → 오전(≤11)/오후(12~16)/저녁(17~)/미상. 마감 시간대 근사."""
    try:
        h = int(str(saved_at).split(" ")[1].split(":")[0])
    except Exception:
        return "미상"
    return "오전" if h < 12 else ("오후" if h < 17 else "저녁")


def _dl_hc_bucket(n):
    """출전 두수 → 버킷."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "미상"
    return "소두수(≤8)" if n <= 8 else ("중두수(9~12)" if n <= 12 else "다두수(13+)")


def _dl_bucket_add(agg, key, hit):
    """집계 헬퍼: agg[key] = {n, hits}. hit 가 None(미판정)이면 n만 증가."""
    b = agg.setdefault(str(key if key not in (None, "") else "미수집"), {"n": 0, "hits": 0})
    b["n"] += 1
    if hit:
        b["hits"] += 1


def _dl_finalize(agg):
    """{key:{n,hits}} → {key:{n,hits,hit_rate}} (내림차순 n)."""
    out = {}
    for k, b in sorted(agg.items(), key=lambda kv: kv[1]["n"], reverse=True):
        out[k] = {"n": b["n"], "hits": b["hits"],
                  "hit_rate": round(b["hits"] / b["n"] * 100, 1) if b["n"] else 0.0}
    return out


def _daily_bigdata(date):
    """[2번] 오늘 경주를 ai_training/ 에서 스캔해 빅데이터 집계.
    경마장·두수·시간대·등급·주로·날씨별 {n,hits,hit_rate}. 미수집 필드는 '미수집' 버킷으로 슬롯 유지."""
    by_track, by_field, by_zone, by_grade, by_cond, by_weather = {}, {}, {}, {}, {}, {}
    if os.path.isdir(AI_TRAINING_DIR):
        for fn in os.listdir(AI_TRAINING_DIR):
            if not fn.endswith(".json"):
                continue
            try:
                d = json.load(open(os.path.join(AI_TRAINING_DIR, fn), encoding="utf-8"))
            except Exception:
                continue
            ri = d.get("race_info") or {}
            if ri.get("date") != date:
                continue
            hit = bool((d.get("result") or {}).get("hit"))
            _dl_bucket_add(by_track, ri.get("track"), hit)
            _dl_bucket_add(by_field, _dl_hc_bucket(ri.get("horse_count")), hit)
            _dl_bucket_add(by_zone, _dl_time_zone(d.get("saved_at")), hit)
            _dl_bucket_add(by_grade, ri.get("grade"), hit)
            _dl_bucket_add(by_cond, ri.get("condition"), hit)
            _dl_bucket_add(by_weather, ri.get("weather"), hit)
    return {
        "by_track": _dl_finalize(by_track),       # 경마장별 이변 패턴
        "by_field_size": _dl_finalize(by_field),  # 출전 두수별 배당 패턴
        "by_time_zone": _dl_finalize(by_zone),    # 시간대별 신호 신뢰도(근사)
        "by_grade": _dl_finalize(by_grade),       # 등급별 이변 확률(미수집 시 '미수집')
        "by_condition": _dl_finalize(by_cond),    # 주로 상태별 적중률(미수집)
        "by_weather": _dl_finalize(by_weather),   # 날씨별 적중률(미수집)
        "note": "grade/condition/weather 는 현재 미수집(슬롯 준비됨) — 수집 배선 시 자동 집계.",
    }


def _daily_profit(date):
    """오늘 결과 입력된 경주의 손익 합(race_results/ investment.profit)."""
    total, n = 0, 0
    if os.path.isdir(RACE_RESULTS_DIR):
        for fn in os.listdir(RACE_RESULTS_DIR):
            if not fn.endswith(".json"):
                continue
            try:
                d = json.load(open(os.path.join(RACE_RESULTS_DIR, fn), encoding="utf-8"))
            except Exception:
                continue
            if d.get("date") != date:
                continue
            p = ((d.get("investment") or {}).get("profit"))
            if p is not None:
                total += int(p)
                n += 1
    return {"net": total, "settled": n}


def _daily_tags(date):
    """오늘 analysis_log/ 파일들의 pattern_tags 를 모아 유니크 태그 목록(데이터 기반)."""
    tags = []
    if os.path.isdir(ANALYSIS_LOG_DIR):
        for fn in os.listdir(ANALYSIS_LOG_DIR):
            if not fn.endswith(".json") or date.replace("-", "_") not in fn:
                continue
            try:
                d = json.load(open(os.path.join(ANALYSIS_LOG_DIR, fn), encoding="utf-8"))
            except Exception:
                continue
            for t in (d.get("pattern_tags") or []):
                if t not in tags:
                    tags.append(t)
    return tags


def _dl_reliability():
    """[3번] 누적 패턴 신뢰도 — learning.json 통계에서 파생(마감급락·쌍승역전·전적유력마·추천종합)."""
    try:
        L = _learning_load()
        s = L.get("stats") or _recompute_learning_stats(L.get("records") or [])
    except Exception:
        s = {}

    def _pick(st, label):
        st = st or {}
        return {"label": label, "hit": st.get("hit"), "n": st.get("n"), "rate": st.get("rate")}

    return {
        "마감급락": _pick(s.get("drop_anomaly"), "급락 감지 적중"),
        "쌍승역전": _pick(s.get("reversal"), "쌍승 역전 적중"),
        "전적이중수렴": _pick(s.get("form_pick"), "전적 유력마 적중(이중수렴 근사)"),
        "추천종합": _pick(s.get("recommend_hit"), "추천 종합 적중"),
    }


# 정성 항목(재생성 시 병합 보존 대상) — 삭제 금지 원칙의 핵심.
_DL_QUAL_KEYS = ("key_learnings", "missed_opportunities", "pattern_discoveries",
                 "system_improvements", "tomorrow_focus")


def _daily_learning_generate(date=None, extra=None):
    """[1번] 일별 학습 일지 생성/갱신 → data/daily_learning/<date>.json.
    수치(results_summary·bigdata·reliability·ai_training_data)는 매번 실데이터로 재계산하되,
    정성 항목·태그는 기존 파일 + extra 를 병합 보존한다(재생성이 수동 기록을 지우지 않음)."""
    date = date or time.strftime("%Y-%m-%d", time.localtime())
    base = _build_daily_summary(date)   # 재사용: total_races·hits·hit_rate·data_quality·cumulative_total
    # 기존 파일 로드(정성 내용 보존)
    path = os.path.join(DAILY_LEARNING_DIR, date + ".json")
    existing = {}
    try:
        with open(path, encoding="utf-8") as f:
            existing = json.load(f) or {}
    except Exception:
        existing = {}
    extra = extra or {}

    journal = dict(existing)   # 기존 내용에서 출발 → 삭제 없음
    journal["date"] = date
    journal["results_summary"] = {
        "total_races": base.get("total_races", 0),
        "hits": base.get("hits", 0),
        "hit_rate": base.get("hit_rate", 0.0),
        "profit": _daily_profit(date),
    }
    # 태그: 기존 ∪ extra ∪ 오늘 analysis_log 태그(데이터 기반)
    tagged = list(existing.get("ai_training_data", {}).get("patterns_tagged") or [])
    for t in ((extra.get("ai_training_data") or {}).get("patterns_tagged") or []) + _daily_tags(date):
        if t not in tagged:
            tagged.append(t)
    journal["ai_training_data"] = {
        "races_collected": base.get("cumulative_total", 0),
        "quality_score": base.get("data_quality", 0),
        "ai_ready_races": base.get("ai_ready_races", 0),
        "patterns_tagged": tagged,
    }
    journal["bigdata"] = _daily_bigdata(date)                      # [2번]
    journal["cumulative_pattern_reliability"] = _dl_reliability()  # [3번]
    # 정성 항목: extra 로 오면 해당 항목만 갱신(수동 우선), 아니면 기존 보존, 둘 다 없으면 빈 배열 유지
    for k in _DL_QUAL_KEYS:
        if extra.get(k) is not None:
            journal[k] = extra[k]
        else:
            journal.setdefault(k, existing.get(k, []))
    journal["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    try:
        os.makedirs(DAILY_LEARNING_DIR, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(journal, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception as e:
        print("[학습일지] 저장 실패:", e)
    return journal


# [4번] 매일 22:00 자동 생성 트리거 — 오늘 결과 집계 → 학습일지 생성 → GitHub 백업.
#   기존 _start_periodic_backup 데몬 패턴 재사용. .env DAILY_LEARNING_HOUR 로 시각 조정(기본 22).
_daily_learning_sched_started = False


def _daily_learning_hour():
    try:
        return int(max(0, min(23, int(os.environ.get("DAILY_LEARNING_HOUR", "") or 22))))
    except (TypeError, ValueError):
        return 22


def _start_daily_learning_scheduler():
    global _daily_learning_sched_started
    if _daily_learning_sched_started:
        return
    _daily_learning_sched_started = True
    hour = _daily_learning_hour()

    def _loop():
        last_done = None
        while True:
            try:
                time.sleep(60)
                now = time.localtime()
                today = time.strftime("%Y-%m-%d", now)
                if now.tm_hour >= hour and last_done != today:
                    _daily_learning_generate(today)   # 정성 내용은 기존 파일 병합 보존
                    _run_data_git_backup(f"학습일지 자동 생성 {today}")
                    last_done = today
                    print(f"[학습일지] {today} {hour}:00 자동 생성·백업 완료")
            except Exception as e:
                print("[학습일지] 스케줄러 예외:", e)

    threading.Thread(target=_loop, daemon=True).start()
    print(f"[학습일지] 매일 {hour}:00 학습 일지 자동 생성 스케줄러 시작")


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


def _learn_mass_drop(rk, an, top3, payouts=None):
    """[대규모급락 학습] 결과 입력 시 pattern_learning.json 의 patterns 배열에 사례 1건 추가 + 통계 갱신.
    대규모 급락(전체 조합 50%+ 동시급락)이 감지된 경주만 기록 → 이변/고배당 여부를 축적."""
    md = an.get("massDrop")
    if not md or not md.get("detected"):
        return None
    top2 = sorted(top3[:2]) if len(top3) >= 2 else []
    res_combo = "+".join(str(x) for x in top2) if top2 else ""
    # 결과 복승 조합이 급락 목록에 있었는지 + 그 급락%
    signal = None
    for d in an.get("drops", []):
        if sorted(d.get("combo") or []) == top2 and d.get("pct") is not None:
            signal = f"{d['pct']}%"
            break
    q_odds = ((payouts or {}).get("quinella") or 0) or 0
    result_odds = "고배당" if q_odds >= 20 else ("중배당" if q_odds >= 7 else ("저배당" if q_odds > 0 else "미상"))
    rec_bets = an.get("betRecommend") or []
    recommended_hit = bool(top2 and any(b.get("kind") == "복승" and sorted(b.get("combo") or []) == top2 for b in rec_bets))
    entry = {
        "pattern": "대규모급락", "raceKey": rk,
        "total_combos_dropped": md.get("dropped"), "drop_ratio": md.get("ratio"),
        "result": res_combo, "result_odds": result_odds,
        "result_quinella_odds": q_odds or None,
        "signal_result_combo": signal,          # 결과 조합이 감지됐던 급락% (예: '-58.4%')
        "recommended_hit": recommended_hit,      # 추천 복승이 적중했는지
        "lesson": "전체급락 시 이변 가능성 높음", "t": time.time(),
    }
    P = _upset_load()
    patterns = P.setdefault("patterns", [])
    patterns.append(entry)
    # 대규모급락 조건 통계(적중률·고배당 비율) 갱신
    md_recs = [p for p in patterns if p.get("pattern") == "대규모급락"]
    n = len(md_recs)
    high = sum(1 for p in md_recs if p.get("result_odds") == "고배당")
    hits = sum(1 for p in md_recs if p.get("recommended_hit"))
    P.setdefault("condition_stats", {})["대규모급락"] = {
        "count": n, "high_odds": high, "recommended_hit": hits,
        "high_odds_rate": round(high / n * 100, 1) if n else None,
        "hit_rate": round(hits / n * 100, 1) if n else None,
    }
    _upset_save(P)
    print(f"[대규모급락학습] {rk} 기록 · 결과 {res_combo}({result_odds}) · 누적 {n}건")
    return entry


def _learn_smart_money(rk, an, top3, payouts=None, date_str=None):
    """[스마트머니 자동 학습] 결과 입력 시 darkHorses 중 스마트머니(상승후급락) 감지 말이 실제 입상(1~3착)했는지
    자동 집계 → pattern_learning.json 의 smart_money_stats(count·hit·rate·cases) 갱신.
    수동 저장(오비히로 11R)과 동일 저장소를 자동으로 누적 → 표본이 쌓이면 신뢰도 산출."""
    darks = an.get("darkHorses") or []
    smart_nos = [int(h["no"]) for h in darks if h.get("smartMoney") and h.get("no") is not None]
    if not smart_nos:
        return None
    top3set = set(int(x) for x in (top3 or []) if x is not None)
    hit_nos = [n for n in smart_nos if n in top3set]
    hit = bool(hit_nos)
    q_odds = ((payouts or {}).get("quinella") or 0) or 0
    t_odds = ((payouts or {}).get("trifecta") or 0) or 0
    P = _upset_load()
    sm = P.setdefault("smart_money_stats", {"count": 0, "hit": 0, "cases": []})
    sm["count"] = int(sm.get("count", 0)) + 1
    if hit:
        sm["hit"] = int(sm.get("hit", 0)) + 1
    sm["rate"] = round(sm["hit"] / sm["count"] * 100, 1) if sm["count"] else 0.0
    sm.setdefault("cases", []).append({
        "race": rk, "date": date_str or time.strftime("%Y-%m-%d"),
        "smart_horses": smart_nos, "placed": hit_nos, "hit": hit,
        "trifecta_odds": t_odds or None, "quinella_odds": q_odds or None,
        "high_odds": bool(t_odds >= 20 or q_odds >= 20),
    })
    sm["cases"] = sm["cases"][-100:]        # 최근 100건만 보존(무한 증가 방지)
    sm["note"] = "스마트머니(상승후급락) 감지 말 복병 편입 자동 집계. 표본 적으면 참고만(50+ 유의)."
    _upset_save(P)
    print(f"[스마트머니학습] {rk} · 스마트머니 {smart_nos} → 입상 {hit_nos or '없음'} · 누적 {sm['count']}건(적중률 {sm['rate']}%)")
    return sm


# ══════════ [적중 판정 기준 개선 + 신호별 학습] 유력마 기반 적중(기존 정확 적중과 병행·무삭제) ══════════
SIGNAL_STATS_FILE = os.path.join(os.path.dirname(__file__), "data", "signal_stats.json")
SIGNAL_STATS_MIN = 50   # [5번] 50경주+ 표본이면 신뢰도 판정(가중치 상향·강조)


def _keyhorse_hit(an, top3):
    """[1·2번·유력마 기반 적중] 기존 정확 적중(복승 1+2·삼복승 1+2+3)과 별개 병행 지표.
      복승: 유력마 상위3 중 2마리+ 입상(1~3착).  삼복승: 유력마 2+ 입상 AND 유력마·복병 합쳐 top3 3자리 커버(유력마2+복병1)."""
    key = [int(x) for x in (an.get("keyHorses") or [])[:3] if x is not None]
    darks = [int(h["no"]) for h in (an.get("darkHorses") or []) if h.get("no") is not None]
    t3 = set(int(x) for x in (top3 or []) if x is not None)
    key_in = [k for k in key if k in t3]
    dark_in = [d for d in darks if d in t3 and d not in key_in]
    q_hit = len(key_in) >= 2
    t_hit = (len(key_in) >= 2) and (len(key_in) + len(dark_in) >= 3)
    return {"quinella_hit": q_hit, "trifecta_hit": t_hit, "keyPlaced": key_in, "darkPlaced": dark_in}


def _active_signal_horses(an):
    """[3번] 이 경주에서 각 신호 유형이 선정한 유력마/복병 마번 → {신호명: [마번]}(빈 신호는 제외)."""
    out = {}
    darks = an.get("darkHorses") or []
    sm = [int(h["no"]) for h in darks if h.get("smartMoney") and h.get("no") is not None]
    if sm:
        out["스마트머니"] = sm
    cj = [int(h["no"]) for h in darks if h.get("forced") and h.get("no") is not None]
    if cj:
        out["집중급락"] = cj
    rev = []
    for r in (an.get("reversalRoles") or []):
        if r.get("no") is not None and int(r["no"]) not in rev:
            rev.append(int(r["no"]))
    _inv = (an.get("inverse") or {}).get("invLead") or {}
    if _inv.get("no") is not None and int(_inv["no"]) not in rev:
        rev.append(int(_inv["no"]))
    if rev:
        out["역배열"] = rev
    drp = []
    for r in (an.get("realtimeAdded") or []):
        if r.get("type") == "drop" and r.get("no") is not None and int(r["no"]) not in drp:
            drp.append(int(r["no"]))
    for h in (an.get("surgePromote") or []):
        if int(h) not in drp:
            drp.append(int(h))
    if an.get("anomalyHorse") is not None and int(an["anomalyHorse"]) not in drp:
        drp.append(int(an["anomalyHorse"]))
    if drp:
        out["급락"] = drp
    dc = [int(h["no"]) for h in ((an.get("elimination") or {}).get("horses") or [])
          if h.get("dualConverge") and h.get("no") is not None]
    if dc:
        out["전적이중수렴"] = dc
    cp = an.get("compressionPattern") or {}
    if cp.get("detected") and cp.get("combo"):
        out["저배당압축"] = [int(x) for x in cp["combo"]]
    # [복승 크로스 역배열·적중률 학습] 강한(0.5+) 크로스 역배열 말 → 실질 강세(2착 유력) 입상 추적
    cx = [int(c["no"]) for c in (an.get("crossReversal") or [])
          if c.get("no") is not None and (c.get("score") or 0) >= 0.5]
    if cx:
        out["크로스역배열"] = cx
    return out


def _signal_stats_load():
    try:
        with open(SIGNAL_STATS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        print("[신호통계] 손상 → 자동 초기화:", e)
        try:
            os.remove(SIGNAL_STATS_FILE)
        except OSError:
            pass
        return {}


def _signal_stats_save(d):
    try:
        os.makedirs(os.path.dirname(SIGNAL_STATS_FILE), exist_ok=True)
        tmp = SIGNAL_STATS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SIGNAL_STATS_FILE)
    except Exception as e:
        print("[신호통계] 저장 실패:", e)


def _learn_signal_stats(rk, an, top3, date_str=None):
    """[3번] 결과 입력 시 신호별 적중(유력마 기반) 자동 태깅·저장 → signal_stats.json. 경주별 멱등(재입력 시 교체)."""
    sig_horses = _active_signal_horses(an)
    kh = _keyhorse_hit(an, top3)
    t3 = set(int(x) for x in (top3 or []) if x is not None)
    S = _signal_stats_load()
    date_str = date_str or time.strftime("%Y-%m-%d")
    # 멱등: 모든 신호에서 이 경주(rk) 기존 케이스 제거 후, 활성 신호에만 재기록
    for name in list(S.keys()):
        S[name]["cases"] = [c for c in (S[name].get("cases") or []) if c.get("race") != rk]
    for name, horses in sig_horses.items():
        e = S.setdefault(name, {"cases": []})
        placed = [h for h in horses if h in t3]
        e["cases"].append({"race": rk, "date": date_str, "horses": horses, "placed": placed,
                           "quinella_hit": kh["quinella_hit"], "trifecta_hit": kh["trifecta_hit"]})
    # cases 기반 통계 재계산(멱등·상한 500)
    for name, e in S.items():
        cs = (e.get("cases") or [])[-500:]
        e["cases"] = cs
        e["count"] = len(cs)
        e["hit_quinella"] = sum(1 for c in cs if c.get("quinella_hit"))
        e["hit_trifecta"] = sum(1 for c in cs if c.get("trifecta_hit"))
        e["rate_quinella"] = round(e["hit_quinella"] / e["count"] * 100, 1) if e["count"] else 0.0
        e["rate_trifecta"] = round(e["hit_trifecta"] / e["count"] * 100, 1) if e["count"] else 0.0
        e["reliable"] = e["count"] >= SIGNAL_STATS_MIN
    _signal_stats_save(S)
    print(f"[신호별학습] {rk} · 활성신호 {list(sig_horses.keys())} · 유력마적중 복승={kh['quinella_hit']} 삼복승={kh['trifecta_hit']}")
    return {"signalStats": S, "keyhorseHit": kh}


MID_HIGH_STATS_FILE = os.path.join(os.path.dirname(__file__), "data", "mid_high_odds_stats.json")


def _mid_high_stats_load():
    try:
        with open(MID_HIGH_STATS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {"count": 0, "placed": 0, "cases": [], "by_signal": {}}
    except Exception:
        try:
            os.remove(MID_HIGH_STATS_FILE)
        except OSError:
            pass
        return {"count": 0, "placed": 0, "cases": [], "by_signal": {}}


def _mid_high_stats_save(d):
    try:
        os.makedirs(os.path.dirname(MID_HIGH_STATS_FILE), exist_ok=True)
        tmp = MID_HIGH_STATS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, MID_HIGH_STATS_FILE)
    except Exception as e:
        print("[중고배당학습] 저장 실패:", e)


def _learn_mid_high_odds(rk, an, top3, payouts=None, date_str=None):
    """[5번] 중고배당 유력마(💎) 감지 시 어떤 신호로 감지했나·실제 입상했나·배당·누적 적중률 저장(경주별 멱등)."""
    mhs = an.get("midHighFavorites") or []
    if not mhs:
        return None
    t3 = set(int(x) for x in (top3 or []) if x is not None)
    S = _mid_high_stats_load()
    S.setdefault("cases", [])
    S["cases"] = [c for c in S["cases"] if c.get("race") != rk]      # 멱등(재입력 교체)
    date_str = date_str or time.strftime("%Y-%m-%d")
    for mf in mhs:
        no = int(mf["no"])
        placed = no in t3
        won = bool(top3) and int(top3[0]) == no
        S["cases"].append({"race": rk, "date": date_str, "no": no, "odds": mf.get("odds"),
                           "signals": mf.get("sigTypes") or [], "placed": placed, "won": won})
    S["cases"] = S["cases"][-300:]
    # 재계산(cases 기반·멱등)
    cs = S["cases"]
    S["count"] = len(cs)
    S["placed"] = sum(1 for c in cs if c.get("placed"))
    S["rate"] = round(S["placed"] / S["count"] * 100, 1) if S["count"] else 0.0
    bysig = {}
    for c in cs:
        for sg in (c.get("signals") or []):
            e = bysig.setdefault(sg, {"count": 0, "placed": 0})
            e["count"] += 1
            if c.get("placed"):
                e["placed"] += 1
    for sg, e in bysig.items():
        e["rate"] = round(e["placed"] / e["count"] * 100, 1) if e["count"] else 0.0
    S["by_signal"] = bysig
    S["note"] = "중고배당 유력마(복승10배+&강한신호) 실제 입상 자동 집계. 표본 적으면 참고만(50+ 유의)."
    _mid_high_stats_save(S)
    print(f"[중고배당학습] {rk} · 💎 {[m['no'] for m in mhs]} · 입상 {[int(m['no']) for m in mhs if int(m['no']) in t3] or '없음'} · 누적 {S['count']}건({S['rate']}%)")
    return S


def _signal_reliability():
    """[5번] 신호별 신뢰도 요약(50경주+ 표본이면 신뢰) → {신호명: {rate_quinella, rate_trifecta, count, reliable}}."""
    S = _signal_stats_load()
    out = {}
    for name, e in S.items():
        out[name] = {"rate_quinella": e.get("rate_quinella", 0), "rate_trifecta": e.get("rate_trifecta", 0),
                     "count": e.get("count", 0), "reliable": bool(e.get("reliable"))}
    return out


def _signal_reliability_for(strong_signals, dark_horses, inverse, compression_pattern, cross_reversal=None):
    """[5번] 현재 분석에서 활성인 신호별 과거 적중률(50경주+ 신뢰) → 프론트 강조 표시용.
      반환 {신호명: {rate_quinella, rate_trifecta, count, reliable, note}} (활성 신호만)."""
    try:
        rel = _signal_reliability()
    except Exception:
        return {}
    active = set()
    darks = dark_horses or []
    if any(h.get("smartMoney") for h in darks):
        active.add("스마트머니")
    if any(h.get("forced") for h in darks):
        active.add("집중급락")
    if (inverse or {}).get("detected"):
        active.add("역배열")
    if (compression_pattern or {}).get("detected"):
        active.add("저배당압축")
    if any((c.get("score") or 0) >= 0.5 for c in (cross_reversal or [])):
        active.add("크로스역배열")
    for _s in ((strong_signals or {}).get("signals") or []):
        _t = _s.get("type")
        if _t in (1, 2, 3):           # 급락 계열(단승/복승 급락·연속하락)
            active.add("급락")
    out = {}
    for name in active:
        e = rel.get(name)
        if not e:
            continue
        _r = e.get("rate_quinella", 0)
        e = dict(e)
        e["note"] = ("신뢰도 높음" if (e.get("reliable") and _r >= 50) else
                     ("표본 부족(참고만)" if not e.get("reliable") else "적중률 낮음(주의)"))
        out[name] = e
    return out


def _learn_upset(rk, an, top3, date_str=None):
    """부진마(최근5경주 평균착순≥4.0)의 입상 여부 + 동반 조건을 학습.
    반환: 갱신된 pattern_learning dict(없으면 None)."""
    form = an.get("form") or []
    if not form:
        return None
    drops = an.get("drops") or []

    def _hno(x):
        # [타입 가드] 말번호를 int/str/dict 어느 형태든 안전 추출. keyHorses·anomalyHorse가
        #   정수 리스트/정수(딕셔너리 아님)로 와도 'int object has no attribute get' 방지.
        if isinstance(x, dict):
            return x.get("no")
        if isinstance(x, bool):
            return None
        if isinstance(x, (int, float)):
            return int(x)
        if isinstance(x, str) and x.lstrip("-").isdigit():
            return int(x)
        return None

    single_nos = {_hno(d) for d in (an.get("singleDrops") or [])}
    single_nos.discard(None)
    key_nos = {_hno(h) for h in (an.get("keyHorses") or [])}
    key_nos.discard(None)
    anom_no = _hno(an.get("anomalyHorse"))
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


# ═══════════════════════════════════════════════════════════════════
#  [신규] 고배당 적중 상세 분석 리포트 시스템 (삭제 없이 추가)
#   [1번] 경주별 완전 재현 리포트(data/race_report/) · [2번] 명예의 전당(highlight_wins)
#   [3번] 추천 근거 재현(프론트) · [4번] 신호조합 학습 태깅(win_tags)
# ═══════════════════════════════════════════════════════════════════
RACE_REPORT_DIR = os.path.join(os.path.dirname(__file__), "data", "race_report")


def _combo_timeline(doc, combo):
    """스냅샷들에서 특정 복승 조합(combo=[a,b])의 배당 변화 타임라인 추출.
    반환 [{time, minutes_before, odds, change, excluded, exclReason}] — change=직전 유효 대비 %.
    [1·2번] 마감 후·다음경주 유입(200%+ 급등) 스냅샷은 excluded 표기(데이터 보존, 변동 계산 제외)."""
    key = "+".join(str(int(x)) for x in sorted(combo))
    tl, prev = [], None
    for s in (doc.get("snapshots") or []):
        q = s.get("quinella") or {}
        o = q.get(key)
        if o is None:
            # 역순 키('b+a')도 방어
            alt = "+".join(str(int(x)) for x in list(sorted(combo))[::-1])
            o = q.get(alt)
        if o is None:
            continue
        try:
            o = round(float(o), 1)
        except (TypeError, ValueError):
            continue
        # [1·2번] 마감 후 / 다음 경주 유입 = 제외(변동 계산에서 배제, 데이터는 표시만)
        excl = bool(s.get("after_close") or s.get("next_race_blocked"))
        excl_reason = ("마감 후 데이터 - 제외됨" if s.get("after_close")
                       else ("다음 경주 혼입 - 제외됨" if s.get("next_race_blocked") else None))
        chg = None if excl else (round((o - prev) / prev * 100, 1) if (prev and prev > 0) else None)
        tl.append({"time": s.get("time"), "minutes_before": s.get("minutes_before"),
                   "odds": o, "change": chg, "excluded": excl, "exclReason": excl_reason})
        if not excl:      # 제외 스냅샷은 기준(prev) 갱신 안 함 → 다음 유효값과 정상 비교
            prev = o
    return tl


def _signal_timeline_from_doc(doc):
    """[3·4·5번] 스냅샷의 signal_horse 시퀀스에서 이상감지 말 변경 이력 + 신호 안정화(2연속 확정)
    + 최종 유효 신호 시점을 도출. 재계산 없이 기록된 signal_horse 만 소비.
    마감후·다음경주차단·기준재설정 스냅샷은 제외. 반환:
      {changes[], confirmed[], candidates[], events{no:{first,last,confirmed,count}},
       finalSignal, finalConfirmed, excluded{after_close,next_race}, validCount}."""
    snaps = doc.get("snapshots") or []
    excl_after = sum(1 for s in snaps if s.get("after_close"))
    excl_next = sum(1 for s in snaps if s.get("next_race_blocked"))
    valid = [s for s in snaps
             if not (s.get("after_close") or s.get("next_race_blocked") or s.get("baseline_reset"))]
    changes, events = [], {}
    prev_sig, streak = None, 0
    confirmed, order_conf = set(), []
    for s in valid:
        sig = s.get("signal_horse")
        t, mb, reason = s.get("time"), s.get("minutes_before"), s.get("signal_reason")
        if sig is None:       # 신호 소멸(감지 없음) — 연속 끊김
            if prev_sig is not None:
                events.setdefault(prev_sig, {}).setdefault("vanished", {"time": t, "minutes_before": mb})
            prev_sig, streak = None, 0
            continue
        sig = int(sig)
        ev = events.setdefault(sig, {"first": {"time": t, "minutes_before": mb}, "count": 0})
        ev["count"] = ev.get("count", 0) + 1
        ev["last"] = {"time": t, "minutes_before": mb}
        if sig == prev_sig:
            streak += 1
        else:
            if prev_sig is not None:   # [3번] 신호말 변경 이력
                # [4번] 직전 신호말이 1회만 감지 후 소멸했는지(후보 소멸) 판별
                prev_once = events.get(prev_sig, {}).get("count", 0) <= 1
                changes.append({
                    "time": t, "minutes_before": mb,
                    "previous_signal": "%d번" % prev_sig, "new_signal": "%d번" % sig,
                    "reason": reason or ("%d번 추가 급락" % sig),
                    "prev_was_candidate": bool(prev_once),
                })
            streak = 1
        if streak >= 2 and sig not in confirmed:   # [4번] 2연속 = 확정 신호
            confirmed.add(sig)
            order_conf.append(sig)
            ev["confirmed"] = {"time": t, "minutes_before": mb}
        prev_sig = sig
    candidates = sorted(h for h in events if h not in confirmed and events[h].get("count"))
    final_sig = None
    for s in reversed(valid):
        if s.get("signal_horse") is not None:
            final_sig = int(s["signal_horse"])
            break
    return {
        "changes": changes,
        "confirmed": order_conf,
        "candidates": candidates,
        "events": {str(k): v for k, v in events.items()},
        "finalSignal": final_sig,
        "finalConfirmed": (final_sig is not None and final_sig in confirmed),
        "excluded": {"after_close": excl_after, "next_race": excl_next},
        "validCount": len(valid),
    }


def _signal_timeline(rk):
    """경주 히스토리 파일을 읽어 _signal_timeline_from_doc 도출(없으면 None)."""
    path, _, _ = _hist_path(rk)
    try:
        doc = json.load(open(path, encoding="utf-8"))
    except Exception:
        return None
    return _signal_timeline_from_doc(doc)


@app.route("/api/odds/signal-timeline", methods=["GET", "POST"])
def signal_timeline_api():
    """[3·4·5번] 이상감지 말 변경 이력 + 신호 안정화 + 최종 유효 신호 시점(경주별)."""
    if request.method == "POST":
        rk = ((request.json or {}).get("raceKey") or "").strip()
    else:
        rk = (request.args.get("raceKey") or "").strip()
    if not rk:
        return jsonify({"raceKey": rk, "timeline": None})
    return jsonify({"raceKey": rk, "timeline": _signal_timeline(rk)})


def _signal_win_tags(an, top3):
    """[4번] 어떤 신호가 실제 입상마를 맞혔는지 태깅.
      초과급락_적중 / 쌍승역전_적중 / 복승불일치_적중 / 전적보조_적중.
    반환 {tags:[...], combo:bool(2개+ 동시), detail:{...}}."""
    top3 = [int(x) for x in top3 if x is not None]
    sq = an.get("signalQuality") or {}
    excess = sq.get("excess") or {}
    tags, detail = [], {}
    # 초과급락_적중: 🔴 집중급락(concentrated)말이 입상
    conc = [int(h) for h in (excess.get("concentrated") or [])]
    hit_conc = [h for h in conc if h in top3]
    if hit_conc:
        tags.append("초과급락_적중")
        detail["excess_hit_horses"] = hit_conc
    # 쌍승역전_적중: 역전 challenger(실질 상위지목마)가 입상
    rev_ch = [int(r["challenger"]) for r in (sq.get("winExactaReversals") or []) if r.get("challenger") is not None]
    hit_rev = [h for h in rev_ch if h in top3]
    if hit_rev:
        tags.append("쌍승역전_적중")
        detail["reversal_hit_horses"] = hit_rev
    # 복승불일치_적중: 집중 자금유입(focus)말이 입상
    mm = sq.get("quinellaMismatch") or {}
    foc = [int(h) for h in (mm.get("focusHorses") or [])]
    hit_foc = [h for h in foc if h in top3]
    if hit_foc:
        tags.append("복승불일치_적중")
        detail["mismatch_hit_horses"] = hit_foc
    # 전적보조_적중: 전적 최고점 말이 입상
    form = an.get("form") or []
    if form:
        top_form = max(form, key=lambda h: (h.get("totalScore") or 0))
        if (top_form.get("totalScore") or 0) > 0 and int(top_form.get("no")) in top3:
            tags.append("전적보조_적중")
            detail["form_hit_horse"] = int(top_form.get("no"))
    return {"tags": tags, "combo": len(tags) >= 2, "detail": detail}


def _report_odds_band(odds):
    if not odds or odds <= 0:
        return "미상"
    return "고배당" if odds >= 20 else ("중배당" if odds >= 7 else "저배당")


def _build_race_report(rk, an, record, result, doc):
    """[1번] 경주별 완전 재현 리포트를 조립해 data/race_report/에 저장하고 dict 반환.
    기존 데이터(an·record·doc.snapshots)만 소비 — 새 계산 없음(재현·설명 전용)."""
    path, date, race = _hist_path(rk)          # 슬러그·날짜 규칙 재사용
    safe = re.sub(r"[^\w가-힣]+", "_", f"{date}_{race}").strip("_")
    top3 = [int(x) for x in (record.get("top3") or []) if x is not None]
    sq = an.get("signalQuality") or {}
    excess = sq.get("excess") or {}
    ex_h = excess.get("horses") or {}
    conf_h = (sq.get("signalConfidence") or {}).get("horses") or {}
    reversals = sq.get("winExactaReversals") or []
    rev_by_ch = {}
    for r in reversals:
        try:
            rev_by_ch[int(r["challenger"])] = r
        except (KeyError, TypeError, ValueError):
            continue
    form_map = {int(h.get("no")): h for h in (an.get("form") or []) if h.get("no") is not None}
    drops = an.get("drops") or []
    bet_rec = an.get("betRecommend") or []

    def _rep_combo_for(no):
        """말 no 를 포함하는 대표 복승 조합(급락 가장 큰 것 → 없으면 추천 조합)."""
        best = None
        for d in drops:
            if d.get("pct") is not None and d["pct"] < 0 and no in [int(x) for x in d.get("combo", [])]:
                if best is None or d["pct"] < best["pct"]:
                    best = d
        if best:
            return [int(x) for x in best["combo"]]
        for b in bet_rec:
            cc = [int(x) for x in b.get("combo", [])]
            if no in cc and len(cc) >= 2:
                return sorted(cc[:2])
        return None

    # ── why_recommended: 입상마 + 유력마 각각의 신호 근거 ──
    focus = []
    for h in top3 + [int(x) for x in (an.get("keyHorses") or [])]:
        if h not in focus:
            focus.append(h)
    why = {}
    for no in focus:
        eh = ex_h.get(no) or ex_h.get(str(no)) or {}
        ch = conf_h.get(no) or conf_h.get(str(no)) or {}
        rv = rev_by_ch.get(no)
        combo = _rep_combo_for(no)
        tl = _combo_timeline(doc, combo) if combo else []
        reasons = []
        if eh.get("grade") == "🔴":
            reasons.append("초과급락")
        elif eh.get("grade") == "🟡":
            reasons.append("약한급락")
        if rv:
            reasons.append("쌍승역전")
        fs = (form_map.get(no) or {}).get("totalScore")
        why["signal_%d" % no] = {
            "horse": no,
            "placed": no in top3,
            "place_rank": (top3.index(no) + 1 if no in top3 else None),
            "excess_drop": eh.get("excess"),
            "avg_drop": eh.get("avg"),
            "grade": eh.get("grade"),
            "rep_combo": combo,
            "drop_timeline": tl,
            "exacta_reversal": bool(rv),
            "reversal_ratio": (rv.get("ratio") if rv else None),
            "record_score": fs,
            "confidence": ch.get("confidence"),
            "reason": " + ".join(reasons) if reasons else ("전적상위" if (fs or 0) >= 60 else "일반"),
        }

    # ── confidence_breakdown: 1착마(없으면 최고신뢰 유력마) 기준 신뢰도 분해 ──
    win_no = top3[0] if top3 else (an.get("keyHorses") or [None])[0]
    wc = conf_h.get(win_no) or conf_h.get(str(win_no)) or {}
    ex_s = round(0.40 * (wc.get("excessScore") or 0.0))
    rv_s = round(0.35 * (wc.get("reversalScore") or 0.0))
    mm_s = round(0.25 * (wc.get("mismatchScore") or 0.0))
    total = wc.get("confidence")
    if total is None:
        total = ex_s + rv_s + mm_s
    grade = "상" if total >= 70 else ("중" if total >= 40 else "하")
    conf_break = {
        "horse": win_no,
        "excess_drop_score": ex_s, "exacta_reversal_score": rv_s, "quinella_mismatch_score": mm_s,
        "record_score": (form_map.get(win_no) or {}).get("totalScore"),
        "total": total, "grade": grade,
    }

    # ── recommendation_process: 스토리형 단계 ──
    snaps = doc.get("snapshots") or []
    t_start = snaps[0].get("time") if snaps else None
    t_last = snaps[-1].get("time") if snaps else None
    steps = []
    if win_no is not None:
        _wf = (form_map.get(win_no) or {}).get("totalScore")
        steps.append("1. 전적 분석: %s번 %s" % (win_no, ("%s점" % _wf if _wf is not None else "전적 정보 제한")))
    if t_start:
        steps.append("2. 배당 수집 시작 %s" % t_start)
    conc = [int(h) for h in (excess.get("concentrated") or [])]
    if conc:
        _c0 = conc[0]
        _ce = (ex_h.get(_c0) or ex_h.get(str(_c0)) or {}).get("excess")
        steps.append("3. 초과급락 감지: %s번 초과급락 %s%%p (전체평균 %s%%)"
                     % (_c0, _ce, excess.get("overall")))
    for r in reversals[:2]:
        steps.append("4. 쌍승 역전: %s→%s(%s) < %s→%s(%s) → %s번 실질 상위 신호"
                     % (r.get("challenger"), r.get("favorite"), r.get("reverseExacta"),
                        r.get("favorite"), r.get("challenger"), r.get("favoredExacta"), r.get("challenger")))
    if total is not None:
        steps.append("5. 종합 신뢰도 %s점(%s) 판정" % (total, grade))
    bmed = (an.get("bmed") or {})
    if bmed.get("strategy"):
        steps.append("6. BMED 전략: %s 적용" % bmed.get("strategy"))
    main_bet = next((b for b in bet_rec if b.get("label") in ("복승 메인", "삼복승 메인")), (bet_rec[0] if bet_rec else None))
    if main_bet:
        steps.append("7. 최종 추천: %s %s"
                     % (main_bet.get("label"), "+".join(str(x) for x in main_bet.get("combo", []))))

    # ── hit_type: 어떤 베팅이 적중했나 ──
    top2 = sorted(top3[:2]) if len(top3) >= 2 else []
    top3s = sorted(top3[:3]) if len(top3) >= 3 else []
    hit_type = None
    for b in bet_rec:
        cc = sorted(int(x) for x in b.get("combo", []))
        if b.get("kind") == "복승" and top2 and cc == top2:
            hit_type = b.get("label") or "복승"
            break
        if b.get("kind") == "삼복승" and top3s and cc == top3s:
            hit_type = b.get("label") or "삼복승"
            break

    payouts = record.get("payouts") or {}
    win_odds = payouts.get("trifecta") or payouts.get("quinella") or 0
    win_tags = _signal_win_tags(an, top3)

    report = {
        "race": race, "raceKey": rk, "date": date,
        "result": {"1st": (top3[0] if len(top3) > 0 else None),
                   "2nd": (top3[1] if len(top3) > 1 else None),
                   "3rd": (top3[2] if len(top3) > 2 else None),
                   "4th": record.get("top4")},
        "hit": bool(record.get("was_hit")),
        "hit_type": hit_type,
        "odds": _report_odds_band(win_odds), "win_odds": win_odds,
        "why_recommended": why,
        "recommendation_process": steps,
        "confidence_breakdown": conf_break,
        "win_tags": win_tags.get("tags"),
        "combo_tags": win_tags.get("combo"),
        "hit_basis": record.get("hit_basis"),
        # [신규 5번] 이상감지 말 변경 이력 + 신호 안정화 + 최초/소멸/확정 시점(재현용)
        "signal_change_history": _signal_timeline_from_doc(doc),
        "pnl": record.get("pnl"), "stake": record.get("stake"),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        os.makedirs(RACE_REPORT_DIR, exist_ok=True)
        json.dump(report, open(os.path.join(RACE_REPORT_DIR, safe + ".json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        report["_slug"] = safe
    except Exception as e:
        print("[경주리포트] 저장 실패:", e)
    return report


def _highlight_story(an, top3, doc):
    """[명예의전당] 고배당 적중 '왜 맞았는지' 스토리 + 정답말 배당 타임라인 스냅샷."""
    basis = []
    for d in (an.get("drops") or [])[:6]:
        if d.get("pct", 0) < 0:
            hit = [h for h in (d.get("combo") or []) if h in top3]
            if hit:
                basis.append(f"{d['combo'][0]}+{d['combo'][1]} 복승 급락({d['pct']}%) → "
                             f"{'·'.join(map(str, hit))}번 입상")
    inv = an.get("inverse") or {}
    inv_hit = [h for h in (inv.get("invHorses") or []) if h in top3]
    if inv.get("detected") and inv_hit:
        basis.append(f"역배열 감지말 {'·'.join(map(str, inv_hit))}번 입상")
    if an.get("massDrop"):
        basis.append("대규모 급락 속 집중신호마 적중")
    tl = {}
    try:
        tl = {str(h): _horse_repr_timeline(doc, h) for h in top3[:3]}
    except Exception:
        tl = {}
    return (" · ".join(basis) or "배당 신호 포착 적중"), tl


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

    # [#5] 누적 손익(pnl) 집계 — 베팅 참여(추천 있던) 경주 기준 순손익·투자합·ROI.
    #   [3번·패스 처리] 패스 권고 경주(추천 없음/전적없음+신호없음)는 손익 집계 제외(추천 안 했으므로).
    bet_recs = [r for r in records if r.get("pnl") is not None and (r.get("bet_type") or r.get("stake")) and not r.get("passed")]
    passed_races = [r for r in records if r.get("passed")]
    pass_correct_n = sum(1 for r in passed_races if r.get("pass_correct"))
    net_profit = sum(int(r.get("pnl") or 0) for r in bet_recs)
    total_staked = sum(int(r.get("stake") or 0) for r in bet_recs if r.get("bet_type"))
    profit_summary = {
        "net": net_profit, "bets": len(bet_recs), "staked": total_staked,
        "roi": round(net_profit / total_staked * 100, 1) if total_staked else None,
        "wins": sum(1 for r in bet_recs if (r.get("pnl") or 0) > 0),
        "passed": len(passed_races), "passCorrect": pass_correct_n,   # [3번] 패스 경주(손익 제외)·패스 적중
    }

    # [비교학습] 이상감지/전적/기수/최종 추천별 적중률(비교 기록 있는 경주만) + 현재 통합 가중치
    compare_stats = {
        "anomaly": _rate(records, lambda r: r.get("cmp_anomaly_hit") is not None,
                         lambda r: r.get("cmp_anomaly_hit")),
        "form": _rate(records, lambda r: r.get("cmp_form_hit") is not None,
                      lambda r: r.get("cmp_form_hit")),
        "jockey": _rate(records, lambda r: r.get("cmp_jockey_hit") is not None,
                        lambda r: r.get("cmp_jockey_hit")),
        "final": _rate(records, lambda r: r.get("cmp_final_hit") is not None,
                       lambda r: r.get("cmp_final_hit")),
    }
    # [3번·근거별 가중치] 전적/배당(이상감지)/기수 3근거 적중률 → 신뢰도 비례 정규화 가중치.
    #   표본 있는 근거만 사용(rate None 제외). 가장 신뢰할 근거에 더 높은 가중치.
    _bw_src = {"form": compare_stats["form"], "anomaly": compare_stats["anomaly"], "jockey": compare_stats["jockey"]}
    _bw_rates = {k: v.get("rate") for k, v in _bw_src.items() if v.get("rate") is not None and (v.get("n") or 0) > 0}
    _bw_total = sum(_bw_rates.values())
    basis_weights = ({k: round(val / _bw_total, 3) for k, val in _bw_rates.items()} if _bw_total > 0 else {})
    if basis_weights:
        basis_weights["top"] = max(_bw_rates, key=lambda k: _bw_rates[k])   # 가장 신뢰할 근거
        basis_weights["rates"] = {k: v.get("rate") for k, v in _bw_src.items()}
        basis_weights["samples"] = {k: (v.get("n") or 0) for k, v in _bw_src.items()}
    _a, _f = compare_stats["anomaly"], compare_stats["form"]
    _fw, _ow, _adjusted = 0.4, 0.6, False
    if (_a.get("n") or 0) >= 50 and (_f.get("n") or 0) >= 50 \
            and _a.get("rate") is not None and _f.get("rate") is not None:
        _ow = round(max(0.45, min(0.75, 0.6 + max(-0.15, min(0.15, (_a["rate"] - _f["rate"]) / 100.0)))), 3)
        _fw = round(1 - _ow, 3)
        _adjusted = True
    integrated_weights = {"form": _fw, "anomaly": _ow, "adjusted": _adjusted,
                          "sample": min(_a.get("n") or 0, _f.get("n") or 0), "need": 50}

    # [5번] 경마장별·월별 + [전략 성과 학습] 전략별 적중률/수익 자동 집계
    by_track, by_month, by_strategy = {}, {}, {}
    for r in records:
        rk = r.get("race") or ""
        track, _ = _area_num(rk)
        m = re.search(r"(\d{4}-\d{2})", rk)
        month = m.group(1) if m else None
        strat = r.get("bmed_strategy")
        if r.get("inverse_detected") and not strat:
            strat = "역배열형"
        pnl = int(r.get("pnl") or 0) if r.get("pnl") is not None else 0
        hit = 1 if r.get("was_hit") else 0
        for bucket, key in ((by_track, track), (by_month, month), (by_strategy, strat)):
            if not key:
                continue
            d = bucket.setdefault(key, {"n": 0, "hit": 0, "profit": 0})
            d["n"] += 1
            d["hit"] += hit
            d["profit"] += pnl
    for bucket in (by_track, by_month, by_strategy):
        for d in bucket.values():
            d["rate"] = round(d["hit"] / d["n"] * 100, 1) if d["n"] else None

    # [신규 4번] 신호조합 학습: 어떤 신호(조합)가 실제 입상마를 맞혔고, 그 중 고배당 적중 비율.
    #   win_tags = 그 경주에서 실제 입상마를 정확히 지목한 신호들(초과급락/쌍승역전/복승불일치/전적보조).
    #   n=태그 발생 경주 · hit=베팅 적중 · high=고배당(복승30배+/삼복승100배+) 적중.
    def _is_high_hit(r):
        p = r.get("payouts") or {}
        return bool((r.get("quinella_hit") and (p.get("quinella") or 0) >= 30)
                    or (r.get("trifecta_hit") and (p.get("trifecta") or 0) >= 100))
    win_tag_stats = {}
    for r in records:
        tags = r.get("win_tags") or []
        hit = bool(r.get("was_hit"))
        high = _is_high_hit(r)
        buckets = list(tags)
        if len(tags) >= 2:
            buckets.append("+".join(sorted(tags)))            # 동시 조합 버킷
            buckets.append("동시(2개+)")
        for b in set(buckets):
            d = win_tag_stats.setdefault(b, {"n": 0, "hit": 0, "high": 0})
            d["n"] += 1
            d["hit"] += 1 if hit else 0
            d["high"] += 1 if high else 0
    for d in win_tag_stats.values():
        d["rate"] = round(d["hit"] / d["n"] * 100, 1) if d["n"] else None
        d["high_rate"] = round(d["high"] / d["n"] * 100, 1) if d["n"] else None

    # [신규 경고시스템 5번] 경고 신호 적중률 — 경고 발생 N / 경고말 입상 N(%) / 경고 무시 후 미적중 N
    _af = [r for r in records if r.get("alert_fired")]
    _ah = sum(1 for r in _af if r.get("alert_hit"))
    _aig = sum(1 for r in _af if r.get("alert_ignored"))
    alert_stats = {"n": len(_af), "hit": _ah,
                   "hit_rate": (round(_ah / len(_af) * 100, 1) if _af else None),
                   "ignored_miss": _aig,
                   "advice": ("경고 발생 시 해당 말 추천 포함 권장" if (_af and _ah / len(_af) >= 0.4) else None)}

    # [보완①·확신도 복승 학습] 확신도 1위 필수 포함 복승/삼복승 적중률 + 확신도 1위 입상률 → 임계값(현재 70) 자동 조정 근거.
    _t1 = _rate(records, lambda r: r.get("conf_top1_placed") is not None, lambda r: r.get("conf_top1_placed"))
    _t1_rate, _t1_n = _t1.get("rate"), _t1.get("n")
    conf_pick_stats = {
        "quinella": _rate(records, lambda r: r.get("conf_quinella_hit") is not None, lambda r: r.get("conf_quinella_hit")),
        "trifecta": _rate(records, lambda r: r.get("conf_trifecta_hit") is not None, lambda r: r.get("conf_trifecta_hit")),
        "trifecta_ins": _rate(records, lambda r: r.get("conf_trifecta_ins_hit") is not None, lambda r: r.get("conf_trifecta_ins_hit")),
        "top1_placed": _t1,
        "forced_placed": _rate(records, lambda r: r.get("conf_forced_placed") is not None, lambda r: r.get("conf_forced_placed")),
        # 임계값 권고(표본 10+): 확신도 1위 입상률 <40% → 상향 / ≥70% → 유지·확대 / 그 외 축적 중
        "threshold_advice": (
            "상향(확신도 1위 입상률 낮음)" if (_t1_rate is not None and _t1_n >= 10 and _t1_rate < 40)
            else ("유지·확대(확신도 1위 입상률 우수)" if (_t1_rate is not None and _t1_n >= 10 and _t1_rate >= 70)
                  else "표본 축적 중")),
    }
    return {
        "alert_stats": alert_stats,       # [신규 5번] 경고 신호 발생·적중·무시 통계
        "conf_pick_stats": conf_pick_stats,   # [보완①] 확신도 1위 필수 복승 학습(적중률·입상률·임계값 권고)
        "win_tag_stats": win_tag_stats,   # [신규 4번] 신호조합별 적중률·고배당 적중률
        "total": len(records),
        "by_track": by_track, "by_month": by_month, "by_strategy": by_strategy,   # [5번]·[전략성과]
        "profit_summary": profit_summary,
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
        # [비교학습] 이상감지/전적/최종 추천별 적중률 + 현재 통합 가중치(50경주+ 자동 조정)
        "compare_stats": compare_stats,
        "basis_weights": basis_weights,   # [3번] 전적/배당/기수 근거별 적중률 → 신뢰 비례 가중치
        "integrated_weights": integrated_weights,
        # [4착] 아깝게 미적중(추천 말 4착) 건수 + 삼복승 근접 건수
        "near_miss": {"n": sum(1 for r in records if r.get("near_miss")),
                      "trio_near": sum(1 for r in records if r.get("trio_near_miss"))},
        # [히로시마2R] 복승조합엇갈림(유력마는 맞음·복승 상대만 어긋남) 건수 + 받치기로 커버 가능했던 건수
        "pairing_miss": {
            "n": sum(1 for r in records if r.get("pairing_miss")),
            "would_cover": sum(1 for r in records
                               if (r.get("pairing_miss_detail") or {}).get("wouldBackingCover")),
        },
    }


@app.route("/api/race-report/list", methods=["GET"])
def race_report_list():
    """[신규 1번] 저장된 경주 재현 리포트 목록 → [{slug,race,date,hit,hit_type,odds,win_tags}]."""
    out = []
    if os.path.isdir(RACE_REPORT_DIR):
        for fn in sorted(os.listdir(RACE_REPORT_DIR), reverse=True):
            if not fn.endswith(".json"):
                continue
            try:
                d = json.load(open(os.path.join(RACE_REPORT_DIR, fn), encoding="utf-8"))
            except Exception:
                continue
            out.append({"slug": fn[:-5], "race": d.get("race"), "date": d.get("date"),
                        "raceKey": d.get("raceKey"), "hit": d.get("hit"), "hit_type": d.get("hit_type"),
                        "odds": d.get("odds"), "win_odds": d.get("win_odds"),
                        "win_tags": d.get("win_tags"), "combo_tags": d.get("combo_tags")})
    return jsonify({"reports": out, "count": len(out)})


@app.route("/api/race-report/get", methods=["GET"])
def race_report_get():
    """[신규 1·3번] 단일 리포트 전체(추천근거·타임라인·신뢰도 분해). query: slug 또는 raceKey."""
    slug = (request.args.get("slug") or "").strip()
    rk = (request.args.get("raceKey") or "").strip()
    if not slug and rk:
        _p, _d, _r = _hist_path(rk)
        slug = re.sub(r"[^\w가-힣]+", "_", f"{_d}_{_r}").strip("_")
    # 경로조작 방어: 슬러그는 파일명 basename 으로만
    slug = os.path.basename(slug)
    if not slug:
        return jsonify({"error": "slug 또는 raceKey 필요"}), 400
    path = os.path.join(RACE_REPORT_DIR, slug + ".json")
    if not os.path.isfile(path):
        return jsonify({"error": "리포트 없음", "slug": slug}), 404
    try:
        return jsonify(json.load(open(path, encoding="utf-8")))
    except Exception as e:
        return jsonify({"error": f"읽기 실패: {e}"}), 500


@app.route("/api/highlights", methods=["GET"])
def highlights_list():
    """[신규 2번] 고배당 명예의 전당(highlight_wins.json) — 최신순."""
    try:
        arr = json.load(open(HIGHLIGHT_FILE, encoding="utf-8"))
    except Exception:
        arr = []
    arr = list(reversed(arr))[:100]
    return jsonify({"highlights": arr, "count": len(arr)})


@app.route("/api/alerts/list", methods=["GET"])
def alerts_list():
    """[신규 경고시스템] 경고 발생 경주 목록(최신순) → [{slug,race,date,count,result,anyHit}]."""
    out = []
    if os.path.isdir(ALERTS_DIR):
        for fn in sorted(os.listdir(ALERTS_DIR), reverse=True):
            if not fn.endswith(".json"):
                continue
            try:
                d = json.load(open(os.path.join(ALERTS_DIR, fn), encoding="utf-8"))
            except Exception:
                continue
            alerts = d.get("alerts") or []
            out.append({"slug": fn[:-5], "race": d.get("race"), "date": d.get("date"),
                        "raceKey": d.get("raceKey"), "count": len(alerts), "result": d.get("result"),
                        "anyHit": any(a.get("alert_correct") for a in alerts),
                        "horses": sorted({int(h) for a in alerts for h in (a.get("alert_horses") or [])})})
    return jsonify({"alerts": out, "count": len(out)})


@app.route("/api/alerts/get", methods=["GET"])
def alerts_get():
    """[신규 경고시스템] 단일 경주 경고 전체(odds_snapshot·경고말·판정). query: slug 또는 raceKey."""
    slug = (request.args.get("slug") or "").strip()
    rk = (request.args.get("raceKey") or "").strip()
    if not slug and rk:
        slug, _, _ = _alert_meta(rk)
    slug = os.path.basename(slug)
    if not slug:
        return jsonify({"error": "slug 또는 raceKey 필요"}), 400
    path = os.path.join(ALERTS_DIR, slug + ".json")
    if not os.path.isfile(path):
        return jsonify({"error": "경고 없음", "slug": slug}), 404
    try:
        return jsonify(json.load(open(path, encoding="utf-8")))
    except Exception as e:
        return jsonify({"error": f"읽기 실패: {e}"}), 500


@app.route("/api/odds/archive/list", methods=["GET"])
def odds_archive_list():
    """[영구보존·3번] 저장된 모든 경주 배당 아카이브 목록(복기용) — .json + 압축(.json.gz) 모두 포함.
    반환 {races:[{raceKey,date,race,count,snapshotCount,hasResult,result,lastTime,compressed}]}."""
    out, seen = [], set()
    try:
        if os.path.isdir(ODDS_HISTORY_DIR):
            for fn in sorted(os.listdir(ODDS_HISTORY_DIR), reverse=True):
                if fn.endswith(".json"):
                    base = os.path.join(ODDS_HISTORY_DIR, fn)
                    comp = False
                elif fn.endswith(".json.gz"):
                    base = os.path.join(ODDS_HISTORY_DIR, fn[:-3])
                    comp = True
                else:
                    continue
                if base in seen:      # .json 우선(같은 경주 .json/.gz 중복 방지)
                    continue
                seen.add(base)
                doc = _hist_read_any(base)
                if not doc:
                    continue
                arch = doc.get("archive_snapshots") or []
                snaps = arch or (doc.get("snapshots") or [])
                if not snaps:
                    continue
                out.append({
                    "raceKey": doc.get("raceKey"), "date": doc.get("date"), "race": doc.get("race"),
                    "count": len(snaps),   # 표시용 유효 스냅샷 수(아카이브 우선, 구파일은 snapshots 폴백)
                    "archiveCount": len(arch), "snapshotCount": len(doc.get("snapshots") or []),
                    "hasResult": bool(doc.get("result")), "result": doc.get("result"),
                    "lastTime": (snaps[-1].get("time") if snaps else None), "compressed": comp,
                })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    out.sort(key=lambda x: (x.get("date") or "", x.get("race") or ""), reverse=True)
    return jsonify({"ok": True, "races": out, "total": len(out)})


@app.route("/api/odds/archive/get", methods=["GET"])
def odds_archive_get():
    """[영구보존·3번] 단일 경주 배당 아카이브 전체 스냅샷(복기 타임라인). archive_snapshots 우선."""
    rk = request.args.get("raceKey") or ""
    if not rk:
        return jsonify({"ok": False, "error": "raceKey 필요"})
    path, _, _ = _hist_path(rk)
    doc = _hist_read_any(path)
    if not doc:
        return jsonify({"ok": False, "error": "해당 경주 아카이브 없음"})
    arch = doc.get("archive_snapshots") or doc.get("snapshots") or []
    return jsonify({"ok": True, "raceKey": doc.get("raceKey"), "date": doc.get("date"),
                    "race": doc.get("race"), "result": doc.get("result"),
                    "count": len(arch), "snapshots": arch})


@app.route("/api/odds/archive/compress", methods=["POST"])
def odds_archive_compress():
    """[영구보존·4번] 7일+ 경주 배당을 .gz 압축 보관(수동 트리거·데이터 삭제 없음)."""
    days = 7
    try:
        days = int((request.get_json(silent=True) or {}).get("days") or 7)
    except (TypeError, ValueError):
        days = 7
    n = _archive_compress_old(days)
    return jsonify({"ok": True, "compressed": n, "days": days})


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
            # [이상감지 히스토리] 스냅샷의 이상감지를 중복제거해 개수 집계(경주별 분리 표시용)
            _seen = set()
            for _s in (d.get("snapshots") or []):
                for _raw in (_s.get("anomalies") or []):
                    _seen.add(_anomaly_pretty(_raw)["text"])
            out.append({"file": fn, "date": d.get("date"), "race": d.get("race"),
                        "raceKey": d.get("raceKey"), "snaps": len(d.get("snapshots") or []),
                        "anomalyCount": len(_seen), "hasResult": bool(d.get("result")),
                        "lastT": (d.get("snapshots") or [{}])[-1].get("t") if d.get("snapshots") else None})
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


def _rec_combos_from_analysis_log(rk):
    """[적중 판정 폴백] 결과 입력 시 재분석 betRecommend가 비었을 때(경주 종료·활성 rec 소실)
    저장된 analysis_log의 recommendation_history(마지막 확정 추천)·final_recommendation으로 복승/삼복승
    조합을 복원해 판정한다. 순서 무관(정렬) 조합. 반환 [{kind, combo}] (betRecommend 호환).
    → 라이브에서 추천했던 4+5/1+4+5가 결과입력 재분석 공백으로 '미적중' 오판되던 문제 해결."""
    try:
        path, _, _ = _analysis_log_path(_canonical_log_key(rk))
        doc = json.load(open(path, encoding="utf-8"))
    except Exception:
        return []

    def _parse(s):
        return sorted(int(x) for x in re.split(r"[+\-]", str(s or "")) if str(x).strip().isdigit())
    out, seen = [], set()

    def _add(kind, combo, need):
        if len(combo) == need:
            key = (kind, tuple(combo))
            if key not in seen:
                seen.add(key)
                out.append({"kind": kind, "combo": combo})
    rh = doc.get("recommendation_history") or []
    if rh:
        last = rh[-1]                              # 마지막(가장 확정) 추천
        _add("복승", _parse(last.get("quinella_main")), 2)
        _add("복승", _parse(last.get("quinella_sub")), 2)
        _add("삼복승", _parse(last.get("trifecta_main")), 3)
    fr = doc.get("final_recommendation") or {}
    for _k, _v in fr.items():
        if isinstance(_v, dict) and _v.get("combo"):
            _c = _parse(_v.get("combo"))
            if len(_c) == 2:
                _add("복승", _c, 2)
            elif len(_c) == 3:
                _add("삼복승", _c, 3)
    return out


def _conf_picks_from_log(rk):
    """[확신도 복승 학습] analysis_log에 보존한 corePicks에서 확신도 복승/삼복승 조합 복원.
    반환 {quinellas:[[a,b]], trifecta:[a,b,c]|None, trifectaIns:[...]|None, forced:[no], top1:no} 또는 None."""
    try:
        path, _, _ = _analysis_log_path(_canonical_log_key(rk))
        doc = json.load(open(path, encoding="utf-8"))
    except Exception:
        return None
    cp = doc.get("corePicks") or {}
    cq = cp.get("confQuinellas") or []
    quinellas = [sorted(int(x) for x in (q.get("combo") or [])) for q in cq if len(q.get("combo") or []) == 2]
    if not quinellas and not cp.get("confTrifecta"):
        return None
    def _s(v):
        return sorted(int(x) for x in v) if v else None
    return {"quinellas": quinellas, "trifecta": _s(cp.get("confTrifecta")),
            "trifectaIns": _s(cp.get("confTrifectaIns")), "forced": cp.get("forcedHorses") or [],
            "top1": cp.get("confTop1")}


def _winning_quinella_odds(rk, top2):
    """[수익 자동 계산·추정] 실배당 미입력 시 승자 복승 조합(top2)의 마지막 시장배당을 analysis_log
    odds_timeline에서 조회(순서 무관). 확정배당이 아닌 시장 추정치. 없으면 None."""
    if not top2 or len(top2) != 2:
        return None
    try:
        path, _, _ = _analysis_log_path(_canonical_log_key(rk))
        doc = json.load(open(path, encoding="utf-8"))
        for s in reversed(doc.get("odds_timeline") or []):
            q = s.get("quinella") or {}
            for k, v in q.items():
                nums = sorted(int(x) for x in re.split(r"[+\-]", str(k)) if str(x).strip().isdigit())
                if nums == sorted(top2) and v and float(v) > 0:
                    return round(float(v), 1)
    except Exception:
        pass
    return None


def _apply_result_learning(rk, result, top3, final_odds=None, stake=None, payout=None, inputs=None):
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

    # [매칭 유연화] triple_store(활성)에 없으면 odds_history 스냅샷에서 rec 재구성 → 분석 재현
    rec = _triple_load().get(rk) or {}
    if not (rec.get("quinella") or rec.get("history")):
        rec = _rec_from_history(rk) or rec
    an = _triple_analyze(rk, rec)

    # [적중 판정 버그 수정] 결과 입력 시 재분석 betRecommend가 비면(경주 종료·활성 rec 소실) 저장 추천이력으로 폴백.
    #   was_hit·복승/삼복승 판정 모두 이 폴백 조합을 사용(라이브 추천이 재분석 공백으로 미적중 오판되던 문제 해결).
    _bet_for_judge = an.get("betRecommend") or _rec_combos_from_analysis_log(rk)

    def in3(combo):
        return all(x in top3 for x in combo)
    was_hit = any(in3(r["combo"]) for r in _bet_for_judge)

    # [신규 경고시스템 2번] 결과 → 경고 내역 자동 매칭(경고말 입상·경고 무시 판정)
    try:
        _al = _match_alerts_to_result(rk, top3, an)
    except Exception as e:
        print("[경고매칭] 실패:", e)
        _al = None

    # [1·3번] 마감 후 대급락말이 실제 입상(1~3착)했는지 판정 → 입상률 학습(surge_hit 갱신)
    try:
        _after_close_learn_result(rk, doc.get("date"), result)
    except Exception as e:
        print("[마감후대급락] 결과학습 실패:", e)

    # [강한 신호 8유형 5번] 유형별 신호말 입상 적중률 누적(경주별 멱등) → 가중치 상향 근거
    try:
        _learn_strong_signals(rk, doc.get("date"), an, top3)
    except Exception as e:
        print("[강한신호학습] 실패:", e)

    # [저배당 압축 패턴 4번] 압축 2두 복승 적중률 누적(경주별 멱등)
    try:
        _learn_compression(rk, doc.get("date"), an, top3)
    except Exception as e:
        print("[압축패턴학습] 실패:", e)

    # [배당 3착 자동 발굴 5번] 3착 후보 신호 유형별 입상/정확3착 적중률 누적(경주별 멱등)
    try:
        _learn_third_place(rk, doc.get("date"), an, top3)
    except Exception as e:
        print("[3착발굴학습] 실패:", e)

    # [고배당 동반 패턴 4번] 고배당 1착 경주 중 3착 내 다른 고배당 포함 비율 누적(경주별 멱등)
    try:
        _learn_high_odds_companion(rk, doc.get("date"), top3, rec)
    except Exception as e:
        print("[고배당동반학습] 실패:", e)

    # ── [4번] 복승/삼복승 정확 적중 + 수익 + 고배당 하이라이트 ──
    rec_bets = _bet_for_judge   # [적중 판정 버그 수정] 재분석 공백 시 저장 추천이력 폴백(위에서 계산)
    top2 = sorted(top3[:2]) if len(top3) >= 2 else []
    top3s = sorted(top3[:3]) if len(top3) >= 3 else []
    # ⚠ 순서 무관 집합 비교: sorted(combo) == sorted(top2/top3) → 4+5==5+4, {1,4,5}=={5,4,1} 동일 처리
    quinella_hit = bool(top2 and any(r.get("kind") == "복승" and sorted(r["combo"]) == top2 for r in rec_bets))
    trifecta_hit = bool(top3s and any(r.get("kind") == "삼복승" and sorted(r["combo"]) == top3s for r in rec_bets))

    # [적중 기준 개선·1·2·3번] 유력마 기반 적중(기존 정확 적중과 병행·무삭제) + 신호별 적중 자동 태깅·저장.
    #   ⚠ 기존 quinella_hit/trifecta_hit(정확 조합)은 손익·기존 통계에 그대로 사용 — 새 지표는 별도.
    keyhorse_hit = _keyhorse_hit(an, top3)
    # [보완①·확신도 복승 학습] 확신도 1위 필수 포함 복승/삼복승이 실제 적중했는지 판정 → 임계값 자동 조정 근거.
    #   재분석 an.corePicks 우선, 경주 종료로 비면 저장 로그(_conf_picks_from_log) 폴백.
    conf_quinella_hit = conf_trifecta_hit = conf_trifecta_ins_hit = None
    conf_top1_placed = conf_forced_placed = None
    try:
        _cp = an.get("corePicks") or {}
        _cq = [sorted(int(x) for x in (q.get("combo") or [])) for q in (_cp.get("confQuinellas") or []) if len(q.get("combo") or []) == 2]
        _ctri = sorted(int(x) for x in _cp["confTrifecta"]) if _cp.get("confTrifecta") else None
        _ctri_ins = sorted(int(x) for x in _cp["confTrifectaIns"]) if _cp.get("confTrifectaIns") else None
        _forced = _cp.get("forcedHorses") or []
        _top1 = _cp.get("confTop1")
        if not _cq and not _ctri:                         # 재분석 공백 → 저장 로그 폴백
            _saved = _conf_picks_from_log(rk)
            if _saved:
                _cq = _saved.get("quinellas") or []
                _ctri = _saved.get("trifecta")
                _ctri_ins = _saved.get("trifectaIns")
                _forced = _saved.get("forced") or []
                _top1 = _saved.get("top1")
        if _cq or _ctri:
            conf_quinella_hit = bool(top2 and any(c == top2 for c in _cq))
            if _ctri:
                conf_trifecta_hit = bool(top3s and _ctri == top3s)
            if _ctri_ins:
                conf_trifecta_ins_hit = bool(top3s and _ctri_ins == top3s)
            if _top1 is not None:
                conf_top1_placed = int(_top1) in top3       # 확신도 1위 말 입상(1~3착) 여부
            if _forced:
                conf_forced_placed = any(int(n) in top3 for n in _forced)   # 확신도 70+ 말 중 입상
    except Exception as e:
        print("[확신도복승학습] 판정 실패:", e)
    try:
        _learn_signal_stats(rk, an, top3, doc.get("date"))
    except Exception as e:
        print("[신호별학습] 실패:", e)
    # [5번·중고배당 유력마 학습] 💎 감지 말의 실제 입상·배당·신호별 적중률 자동 집계.
    #   배당은 midHighFavorites 각 말의 odds를 쓰므로 payouts 불필요(이 시점 payouts 미정의) → None 전달.
    try:
        _learn_mid_high_odds(rk, an, top3, None, doc.get("date"))
    except Exception as e:
        print("[중고배당학습] 실패:", e)

    # [비교학습] 이상감지/전적/최종 추천 조합 각각의 적중 판정(복승 top2 정확 또는 삼복승 top3 정확)
    cmp_rec = an.get("compareRecommend") or {}

    def _cmp_hit(block):
        if not block:
            return None
        q, t = block.get("quinella"), block.get("trio")
        return bool((q and top2 and sorted(q) == top2) or (t and top3s and sorted(t) == top3s))
    cmp_anomaly_hit = _cmp_hit(cmp_rec.get("anomaly"))
    cmp_form_hit = _cmp_hit(cmp_rec.get("form"))
    cmp_jockey_hit = _cmp_hit(cmp_rec.get("jockey"))   # [기수 근거] 기수 복승률 상위 조합 적중 여부
    cmp_final_hit = _cmp_hit(cmp_rec.get("final"))

    # [2·3번] 4착 near-miss: 추천 말(삼복승 조합)이 4착 → '아깝게 미적중' 별도 기록
    try:
        _t4 = result.get("4th")
        top4 = int(_t4) if _t4 not in (None, "") else None
    except (TypeError, ValueError):
        top4 = None
    rec_horses = set()
    for b in rec_bets:
        for x in b.get("combo", []):
            rec_horses.add(int(x))
    trio_near = False   # 추천 삼복승 2두 top3 + 나머지 1두가 정확히 4착 → '거의 적중'
    for b in rec_bets:
        if b.get("kind") == "삼복승":
            cc = [int(x) for x in b.get("combo", [])]
            in3 = [x for x in cc if x in top3s]
            out = [x for x in cc if x not in top3s]
            if len(in3) == 2 and len(out) == 1 and top4 is not None and out[0] == top4:
                trio_near = True
                break
    near_miss_horse = top4 if (top4 is not None and (top4 in rec_horses or trio_near)) else None
    near_miss = bool(near_miss_horse)
    _name_by = {h.get("no"): h.get("name") for h in (an.get("form") or [])}
    near_miss_name = _name_by.get(near_miss_horse) if near_miss_horse is not None else None
    if near_miss:
        try:
            _record_near_miss(rk, time.strftime("%Y-%m-%d"), near_miss_horse, near_miss_name)
        except Exception as e:
            print("[4착학습] near-miss 저장 실패:", e)
    # [히로시마 2R 학습·복기] 복승조합엇갈림 near-miss: 유력마는 맞았으나 복승 상대 페어링만 어긋나 복승 놓침.
    pairing_miss = None
    if not quinella_hit:
        try:
            pairing_miss = _pairing_miss_review(an, top3)
        except Exception as e:
            print("[복승조합엇갈림] 판정 실패:", e)
    fo = final_odds if isinstance(final_odds, dict) else {}

    def _odds_val(x):  # 확장은 {combo,odds} 중첩, 수동입력은 숫자 → 둘 다 허용
        return _safe_num(x.get("odds")) if isinstance(x, dict) else _safe_num(x)
    q_odds = _odds_val(fo.get("quinella"))
    t_odds = _odds_val(fo.get("trifecta") or fo.get("trio"))
    # [수익 자동 계산·추정] 복승 적중인데 실배당 미입력이면 승자 조합의 시장배당으로 추정(확정배당 입력 시 정정).
    if quinella_hit and not q_odds:
        _eq = _winning_quinella_odds(rk, sorted(top3[:2]) if len(top3) >= 2 else [])
        if _eq:
            q_odds = _eq
            print(f"[수익 추정] {rk}: 복승 실배당 미입력 → 시장배당 {_eq}배로 추정(확정배당 입력 시 정정)")
    payouts = {"quinella": (q_odds if quinella_hit and q_odds else 0),
               "trifecta": (t_odds if trifecta_hit and t_odds else 0)}
    try:
        if (quinella_hit and q_odds and q_odds >= 30) or (trifecta_hit and t_odds and t_odds >= 100):
            # [2번] 명예의 전당: 적중 근거(초과급락·역전·전적)·태깅·리포트 슬러그 + 스토리·정답말 타임라인(병합)
            _hp, _hd, _hr = _hist_path(rk)
            _slug = re.sub(r"[^\w가-힣]+", "_", f"{_hd}_{_hr}").strip("_")
            _wt2 = _signal_win_tags(an, top3)
            # [신규 경고시스템 4번] 고배당 경고 패턴: 경고 발생 + 경고말 입상 시 교훈 기록
            _alert_fired = bool(_al and _al.get("fired"))
            _alert_hit = bool(_al and _al.get("hit"))
            _lesson = None
            if _alert_fired and _alert_hit:
                _lesson = "경고 말이 실제 입상 — 경고 신호를 추천에 반영했어야" if (_al or {}).get("ignored") \
                    else "경고 말이 실제 입상 — 경고 신호 추천 반영이 적중"
            _hl_story, _hl_tl = _highlight_story(an, top3, doc)   # [복기] 스토리+정답말 타임라인
            _highlight_save({"raceKey": rk, "top3": top3,
                             "quinella_hit": quinella_hit, "quinella_odds": q_odds,
                             "trifecta_hit": trifecta_hit, "trifecta_odds": t_odds,
                             "date": _hd, "race": _hr, "report_slug": _slug,
                             "win_tags": _wt2.get("tags"), "combo_tags": _wt2.get("combo"),
                             "alert_triggered": _alert_fired, "alert_horses": (_al or {}).get("horses") or [],
                             "alert_ignored": bool(_al and _al.get("ignored")), "lesson": _lesson,
                             "story": _hl_story, "timelines": _hl_tl,
                             "t": time.time()})
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

    # [#5] 실제 투자금액 기반 손익(pnl). stake 미지정 시 정액 1000 가정.
    #   적중: +(확정배당-1)*stake · 추천했으나 미적중: -stake · 추천 없음: 0(불참).
    # [보완#3] payout(실수령 배당금) 지정 시 확정배당 추정 대신 실제 손익으로 계산.
    #   pnl = 실수령액 - 투자금. 실수령 0 = 미적중. 정확도 우선(추정 오차 제거).
    stake = int(stake) if (stake and int(stake) > 0) else 1000
    actual_payout = None
    try:
        if payout is not None and str(payout) != "":
            actual_payout = int(round(float(payout)))
    except (TypeError, ValueError):
        actual_payout = None
    if actual_payout is not None:
        pnl = actual_payout - stake
    elif quinella_hit and payouts.get("quinella"):
        pnl = round((payouts["quinella"] - 1) * stake)
    elif trifecta_hit and payouts.get("trifecta"):
        pnl = round((payouts["trifecta"] - 1) * stake)
    elif _bet_type:
        pnl = -stake
    else:
        pnl = 0

    # [1번] 적중 근거 한눈 요약 — 전적점수·급락점수+폭·역배열·최종신뢰도 + 한줄 근거
    _sq = an.get("signalQuality") or {}
    _conf_h = (_sq.get("signalConfidence") or {}).get("horses") or {}
    _inv = an.get("inverse") or {}
    _form_map = {h.get("no"): h.get("totalScore") for h in (an.get("form") or [])}
    _win_no = top3[0] if top3 else None
    _form_score = _form_map.get(_win_no)
    # 결과 입상마가 낀 최대 급락(급락폭) + 그 말 집중신호 점수(급락점수)
    _best_drop = None
    for _d in an.get("drops", []):
        if _d.get("pct", 0) < 0 and any(h in top3 for h in _d["combo"]):
            if _best_drop is None or _d["pct"] < _best_drop["pct"]:
                _best_drop = _d
    _drop_amt = _best_drop["pct"] if _best_drop else None
    _hit_conf = max([(_conf_h.get(h) or {}).get("confidence", 0) for h in top3] or [0])
    _drop_score = max([(_conf_h.get(h) or {}).get("excessScore", 0) for h in top3] or [0])
    _inv_hit = [h for h in (_inv.get("invHorses") or []) if h in top3]
    _basis = []
    if form_pick_hit:
        _basis.append(f"전적유력마 {form_pick}번 적중")
    if signal_correct:
        _basis.append(signal_correct[0])
    if _inv.get("detected") and _inv_hit:
        _basis.append(f"역배열 감지말 {'·'.join(map(str, _inv_hit))}번 입상")
    if elimination_correct:
        _basis.append("제거 적중")
    hit_basis = {
        "formScore": _form_score, "dropAmt": _drop_amt, "dropScore": _drop_score,
        "inverse": bool(_inv.get("detected")), "inverseHorses": _inv.get("invHorses") or [],
        "inverseHit": _inv_hit, "confidence": _hit_conf,
        "reason": " · ".join(_basis) or ("적중" if was_hit else "추천 미적중"),
    }

    # [4번] 신호조합 학습 태깅(초과급락_적중/쌍승역전_적중/복승불일치_적중/전적보조_적중)
    _wt = _signal_win_tags(an, top3)
    win_tags = _wt.get("tags") or []

    record = {
        "race": rk, "result": result, "top3": top3, "was_hit": was_hit,
        "quinella_hit": quinella_hit, "trifecta_hit": trifecta_hit, "payouts": payouts,
        # [보완①·확신도 복승 학습] 확신도 1위 필수 포함 복승/삼복승 적중·확신도 1위 입상 여부(임계값 자동 조정 근거)
        "conf_quinella_hit": conf_quinella_hit, "conf_trifecta_hit": conf_trifecta_hit,
        "conf_trifecta_ins_hit": conf_trifecta_ins_hit, "conf_top1_placed": conf_top1_placed,
        "conf_forced_placed": conf_forced_placed,
        # [오늘 통계 대시보드] 날짜·경마장·유력마 기반 적중(집계·경마장별·타임라인용)
        "date": doc.get("date"), "venue": (_area_num(rk)[0] or None),
        "keyhorse_quinella_hit": keyhorse_hit["quinella_hit"], "keyhorse_trifecta_hit": keyhorse_hit["trifecta_hit"],
        "stake": stake, "pnl": pnl, "payout_actual": actual_payout,
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
        # [비교학습] 이상감지/전적/최종 추천별 적중 여부 + 조합(통계 누적·가중치 조정 근거)
        "cmp_anomaly_hit": cmp_anomaly_hit, "cmp_form_hit": cmp_form_hit,
        "cmp_jockey_hit": cmp_jockey_hit, "cmp_final_hit": cmp_final_hit,
        "cmp_recommend": cmp_rec,
        # [2·3번] 4착 near-miss(추천 말 4착=아깝게 미적중) — 통계·보험픽 학습 근거
        "top4": top4, "near_miss": near_miss, "near_miss_horse": near_miss_horse,
        "near_miss_name": near_miss_name, "trio_near_miss": trio_near,
        # [히로시마 2R] 복승조합엇갈림 near-miss(유력마는 맞음·복승 상대만 어긋남) — 받치기 복승 학습 근거
        "pairing_miss": bool(pairing_miss), "pairing_miss_detail": pairing_miss,
        # [1번] 적중 근거 요약(전적점수·급락점수+폭·역배열·최종신뢰도·한줄근거)
        "hit_basis": hit_basis,
        # [전략 성과 학습] 적용된 BMED 전략·역배열 여부(전략별 적중률 집계 근거)
        "bmed_strategy": ((an.get("bmed") or {}).get("strategy")),
        "inverse_detected": bool((an.get("inverse") or {}).get("detected")),
        # [4번] 신호조합 적중 태깅(초과급락/쌍승역전/복승불일치/전적보조 + 동시)
        "win_tags": win_tags, "win_tags_combo": bool(len(win_tags) >= 2),
        # [신규 경고시스템 2·5번] 경고 발생·경고말 입상·경고 무시(추천 누락) 판정
        "alert_fired": bool(_al and _al.get("fired")), "alert_horses": (_al or {}).get("horses") or [],
        "alert_hit": bool(_al and _al.get("hit")), "alert_ignored": bool(_al and _al.get("ignored")),
        # [3번·패스 처리] 추천 없음(신호 대기/패스형) 또는 전적없음+신호없음 = 패스 권고 경주 → 손익·적중률 집계 제외.
        "passed": _is_pass_race(an), "pass_correct": bool(_is_pass_race(an) and not quinella_hit and not trifecta_hit),
        "t": time.time(),
    }
    L = _learning_load()
    # [결과 수정 지원] 같은 경주 기존 레코드 제거 후 추가 → 결과 재입력(수정) 시 이중집계 방지(멱등).
    #   결과를 잘못 입력했거나 착순을 정정할 때 record-result 를 다시 호출하면 깨끗이 덮어써진다.
    _before = len(L.get("records") or [])
    L["records"] = [r for r in (L.get("records") or []) if r.get("race") != rk]
    if len(L["records"]) != _before:
        print(f"[결과 수정] {rk}: 기존 레코드 교체(재입력)")
    L["records"].append(record)
    L["stats"] = _recompute_learning_stats(L["records"])
    _learning_save(L)
    # [2번] 부진마 역전 학습(전적 있는 경주만 작동) — 급락30%+·복승이상감지 동반 조건별 적중률 누적
    try:
        _learn_upset(rk, an, top3, time.strftime("%Y-%m-%d"))
    except Exception as e:
        print("[부진마학습] 실패:", e)
    # [대규모급락 학습] 이 경주가 대규모 급락 패턴이면 결과와 함께 pattern_learning 에 사례 기록
    try:
        _learn_mass_drop(rk, an, top3, payouts)
    except Exception as e:
        print("[대규모급락학습] 실패:", e)
    # [스마트머니 자동 학습·보완점3] darkHorses 스마트머니 감지 말의 실제 입상 여부 자동 집계
    try:
        _learn_smart_money(rk, an, top3, payouts, time.strftime("%Y-%m-%d"))
    except Exception as e:
        print("[스마트머니학습] 실패:", e)
    # [복기] 결과 적중/판정 요약을 히스토리 파일에도 저장 → 통계 탭에서 재계산 없이 표시
    try:
        doc["review"] = {
            "was_hit": was_hit, "quinella_hit": quinella_hit, "trifecta_hit": trifecta_hit,
            # [적중 기준 개선] 유력마 기반 적중(별도 지표·기존 정확 적중과 병행)
            "keyhorse_quinella_hit": keyhorse_hit["quinella_hit"],
            "keyhorse_trifecta_hit": keyhorse_hit["trifecta_hit"],
            "keyhorse_placed": keyhorse_hit["keyPlaced"], "dark_placed": keyhorse_hit["darkPlaced"],
            "payouts": payouts, "anomaly_was_correct": anomaly_correct,
            "signal_correct": signal_correct, "elimination_correct": elimination_correct,
            "eliminated": eliminated_nos, "form_pick": form_pick, "form_pick_hit": form_pick_hit,
            "pnl": pnl, "stake": stake,   # [일본경마 복기] 재조회 시 손익 그대로 표시
            "near_miss": near_miss, "near_miss_horse": near_miss_horse,  # [4착] 아깝게 미적중
            "pairing_miss": pairing_miss,   # [히로시마2R] 복승조합엇갈림(유력마는 맞음·복승 상대 어긋남)
            "hit_basis": hit_basis,   # [1번] 적중 근거 요약
        }
        json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception as e:
        print("[복기저장] 결과 요약 실패:", e)
    # [신규 1번] 경주별 완전 재현 리포트(data/race_report/) 저장 — 추천 근거·타임라인·신뢰도 분해
    try:
        _rep = _build_race_report(rk, an, record, result, doc)
        record["report_slug"] = _rep.get("_slug")
        record["win_odds"] = _rep.get("win_odds")
    except Exception as e:
        print("[경주리포트] 생성 실패:", e)
    # [분석 로그] 결과 입력 시 로그에 실제 결과·적중 반영
    _analysis_log_save(rk)
    # [결과 완전 저장] 경주별 완전 파일(data/race_results/) 저장 + 검증(AI 학습용)
    try:
        _rr = _save_race_result(rk, an, record, result, top4, inputs)
        record["race_result_saved"] = _rr.get("ok")
        record["race_result_errors"] = _rr.get("errors") or []
    except Exception as e:
        print("[결과완전저장] 실패:", e)
    # [AI 학습 Phase1] AI 학습용 완전 데이터(data/ai_training/) 저장 + 품질점수
    try:
        _ai = _save_ai_training(rk, an, record, result, top4, inputs)
        record["ai_training_saved"] = _ai.get("ok")
        record["ai_quality_score"] = _ai.get("score")
        record["ai_quality_grade"] = _ai.get("grade")
    except Exception as e:
        print("[AI학습저장] 실패:", e)
    # [6번] 해당 경주 날짜의 일별 요약 갱신(결과 입력마다 최신화)
    try:
        _dt = _race_result_id(rk)[1]
        _build_daily_summary(_dt)
    except Exception as e:
        print("[일별요약] 갱신 실패:", e)
    # [전체데이터·패턴발견] 결과 반영된 로그까지 포함해 적중 경주 공통점 자동 발견
    try:
        _discover_patterns()
    except Exception as e:
        print("[패턴발견] 실패:", e)
    # [실패 복기 학습] 미적중 경주 → 실패 유형 자동 분류 + 정답말 역추적 + 놓친 신호 패턴 누적
    #   + 10건+ 반복 시 규칙 자동 생성. 적중 시엔 건너뜀(복기 대상 아님).
    try:
        if not was_hit:
            _fail = _failure_record(rk, an, top3, doc, record, L.get("stats") or {})
            record["failure"] = _fail   # 레코드에 실패 분류 첨부(복기 리포트 재사용)
            try:                        # 히스토리 review 블록에도 실패 분류 저장(재조회 시 재계산 불필요)
                doc.setdefault("review", {})["failure"] = _fail
                json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False)
            except Exception as _e2:
                print("[복기저장] 실패분류 저장 실패:", _e2)
    except Exception as e:
        print("[실패복기] 분류/학습 실패:", e)
    print(f"[자동학습] {rk} 결과 {top3} → 추천적중 {was_hit}, 급락적중 {anomaly_correct}, "
          f"전적유력마 {form_pick}({'적중' if form_pick_hit else '실패'}), 제거적중 {elimination_correct}")
    # [데이터 보호·2번] 결과 입력마다 학습 코퍼스를 GitHub에 자동 백업(5초 디바운스·비동기)
    try:
        _data_git_backup(f"결과 자동백업: {rk} {'-'.join(map(str, top3))}")
    except Exception as e:
        print("[데이터백업] 트리거 실패:", e)
    return record, L["stats"]


# ══════════════ 실패 복기 학습 시스템 (미적중 자동 분류 · 정답말 역추적 · 규칙 자동생성) ══════════════
#   결과 입력 시 미적중이면 실제 입상마의 배당 타임라인을 역추적해 5개 실패 유형으로 자동 분류하고,
#   놓친 신호 패턴을 누적한다. 같은 패턴이 임계치(기본 10건) 이상 반복되면 개선 규칙을 자동 생성.
#   ⚠ 기존 학습(learning.json·pattern_learning 등)과 독립된 별도 저장소 → 기존 기능 영향 없음.
FAILURE_FILE = os.path.join(os.path.dirname(__file__), "data", "failure_review.json")
# [복기 학습 재설계] 1회~소수 실패로 규칙(공식 변경)을 만들지 않는다. 통계 유의성(50회+) 확보 후에만
#   '검토 후보' 규칙을 생성하고, 실제 적용은 수동. (기존 3회 즉시 생성 → 50회 게이팅으로 엄격화)
FAIL_RULE_THRESHOLD = PATTERN_SAMPLE_MIN   # 50 — 이 표본 미만은 판단 보류(규칙 미생성)

# 실패 유형 정의(번호 → 코드 → 표시라벨)
FAIL_TYPE_LABEL = {
    "신호미반영": "유형1 · 신호 미반영",
    "페이크베팅": "유형2 · 페이크 베팅",
    "노이즈":     "유형3 · 노이즈",
    "전적오판":   "유형4 · 전적 오판",
    "타이밍":     "유형5 · 타이밍(마감 후)",
}
FAIL_TYPE_ORDER = ["신호미반영", "페이크베팅", "노이즈", "전적오판", "타이밍"]


def _failure_load():
    try:
        d = json.load(open(FAILURE_FILE, encoding="utf-8"))
    except Exception:
        d = {}
    d.setdefault("cases", [])
    d.setdefault("type_counts", {})
    d.setdefault("missed_patterns", {})
    d.setdefault("rules", [])
    d.setdefault("winner_signal", {"had": 0, "total": 0})   # [4번] 실제 1착말 신호 보유율
    return d


def _failure_save(d):
    os.makedirs(os.path.dirname(FAILURE_FILE), exist_ok=True)
    json.dump(d, open(FAILURE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _horse_repr_timeline(doc, no):
    """[3번] 특정 말의 대표 배당 타임라인을 스냅샷에서 역추적.
    단승(win) 우선, 없으면 그 말이 포함된 최저 복승 조합 배당(자금 유입 대리지표).
    반환 [{mb, odds, pct(직전대비%), time, after(마감후), src}] (시간순)."""
    try:
        no = int(no)
    except (TypeError, ValueError):
        return []
    tl, prev = [], None
    for s in (doc.get("snapshots") or []):
        o = None
        w = (s.get("win") or {}).get(str(no))
        if w not in (None, ""):
            try:
                o = float(w)
            except (TypeError, ValueError):
                o = None
        src = "단승"
        if o is None:   # 단승 미수집(일본 등) → 그 말이 낀 최저 복승 조합
            best = None
            for k, v in (s.get("quinella") or {}).items():
                if str(no) in str(k).split("+"):
                    try:
                        ov = float(v)
                    except (TypeError, ValueError):
                        continue
                    if ov > 0 and (best is None or ov < best):
                        best = ov
            o, src = best, "복승"
        if o is None or o <= 0:
            continue
        mbs = s.get("mb_signed")
        pct = round((o - prev) / prev * 100) if (prev and prev > 0) else None
        tl.append({"mb": mbs, "odds": round(o, 1), "pct": pct,
                   "time": s.get("time"), "after": bool(s.get("after_close")), "src": src})
        prev = o
    return tl


def _timeline_rebound(tl):
    """타임라인이 '급락 후 반등'(페이크 의심)인지 판정. 최저점까지 10%+ 하락 후 최저 대비 10%+ 반등."""
    odds = [p["odds"] for p in (tl or []) if p.get("odds")]
    if len(odds) < 3:
        return False
    lo = min(odds)
    lo_i = odds.index(lo)
    pre_max = max(odds[:lo_i + 1]) if lo_i >= 0 else odds[0]
    drop_frac = (pre_max - lo) / pre_max if pre_max > 0 else 0.0
    rebound_frac = (odds[-1] - lo) / lo if lo > 0 else 0.0
    return lo_i < len(odds) - 1 and drop_frac >= 0.10 and rebound_frac >= 0.10


def _timeline_signal_label(pct):
    """직전대비 급락% → 사람이 읽는 신호 강도 라벨(역추적 리포트용)."""
    if pct is None:
        return "기준"
    if pct <= -25:
        return "강한 신호 감지됨"
    if pct <= -15:
        return "강한 신호"
    if pct <= -8:
        return "약한 신호"
    if pct >= 8:
        return "반등(자금 이탈)"
    return "신호 없음"


def _pairing_miss_review(an, top3):
    """[히로시마 2R 학습·복기] '유력마는 맞았으나 복승 상대 조합이 엇갈려' 복승을 놓친 아쉬운 near-miss.
    조건: ①추천 복승 어느 것도 실제 top2와 불일치 ②실제 1·2착이 둘 다 유력마(keyHorses)에 포함.
    → 삼복승/유력마 판단은 옳았고 복승 상대 페어링만 어긋난 경우 → 받치기 복승으로 커버 가능.
    반환 dict(detected 등) 또는 None."""
    top3 = [int(x) for x in top3 if x not in (None, "")]
    if len(top3) < 2:
        return None
    top2 = sorted(top3[:2])
    key = [int(x) for x in (an.get("keyHorses") or []) if x not in (None, "")]
    if not (top2[0] in key and top2[1] in key):
        return None                      # top2 중 유력마가 아닌 말이 있으면 다른 유형(전적오판 등)
    q_combos = [sorted(int(x) for x in (b.get("combo") or []))
                for b in (an.get("betRecommend") or []) if b.get("kind") == "복승"]
    if top2 in q_combos:
        return None                      # 복승이 이미 top2를 커버(적중) → near-miss 아님
    lead = ((an.get("inverse") or {}).get("invLead") or {})
    lead_no = lead.get("no")
    backing = [sorted(int(x) for x in (b.get("combo") or []))
               for b in (an.get("reversalBacking") or [])]
    return {
        "detected": True, "top2": top2, "keyHorses": key, "recQuinella": q_combos,
        "leadNo": (int(lead_no) if lead_no is not None else None),
        "backingCovered": bool(top2 in backing),           # 받치기가 top2를 이미 커버했나
        "wouldBackingCover": bool(lead_no is not None and int(lead_no) in top2),
        "lesson": ("유력마·삼복승 판단은 정확했으나 복승 메인이 실질유력마와 '다른' 유력마 받치기를 놓쳐 "
                   "복승 top2를 놓침 → 역배열 실질유력마 축 받치기 복승으로 커버."),
    }


def _classify_failure(rk, an, top3, doc):
    """[1번] 미적중 경주의 실패 유형 자동 분류 + [2·3번] 정답말 역추적 근거 생성.
    실제 입상마(추천에서 빠진 최상위 말)의 배당 타임라인을 분석해 5개 유형으로 분류.
    반환 dict {type, label, focus, winner, missed, reason, improvement, missed_signal,
               timeline, maxDrop, buriedBy}."""
    top3 = [int(x) for x in top3 if x not in (None, "")]
    if not top3:
        return None
    rec_horses = set()
    for b in (an.get("betRecommend") or []):
        for x in (b.get("combo") or []):
            try:
                rec_horses.add(int(x))
            except (TypeError, ValueError):
                pass
    winner = top3[0]
    missed = [h for h in top3 if h not in rec_horses]      # 입상했으나 추천에 없던 말
    focus = missed[0] if missed else winner                 # 복기 초점 말
    tl = _horse_repr_timeline(doc, focus)

    pcts = [p["pct"] for p in tl if p.get("pct") is not None]
    max_drop = min(pcts) if pcts else 0                      # 초점 말 최대 급락(음수)
    pre_close = [p for p in tl if not p.get("after") and p.get("pct") is not None]
    pre_close_drop = min([p["pct"] for p in pre_close], default=0)
    after_drop = min([p["pct"] for p in tl if p.get("after") and p.get("pct") is not None], default=0)

    excess = ((an.get("signalQuality") or {}).get("excess")) or {}
    ehorses = excess.get("horses") or {}
    fx = ehorses.get(focus) or ehorses.get(str(focus)) or {}
    focus_grade = fx.get("grade")                           # 🔴/🟡/None
    focus_strength = fx.get("strength")
    adv = an.get("advanced") or {}
    fakes = adv.get("fakes") or []
    focus_fake = any(focus in (f.get("combo") or []) for f in fakes)
    mass = bool(an.get("massDrop"))
    after_close = bool(an.get("afterClose"))
    rebounded = _timeline_rebound(tl)

    pre_close_signal = (pre_close_drop <= -10) or (focus_grade in ("🔴", "🟡")) or focus_fake
    has_signal = (max_drop <= -10) or (focus_grade in ("🔴", "🟡")) or focus_fake or rebounded

    # ── 유형 판정(우선순위: 타이밍 → 전적오판 → 페이크 → 노이즈 → 신호미반영) ──
    if after_close and after_drop <= -10 and not pre_close_signal:
        ftype = "타이밍"
    elif not has_signal:
        ftype = "전적오판"
    elif rebounded or focus_fake:
        ftype = "페이크베팅"
    elif mass:
        ftype = "노이즈"
    else:
        ftype = "신호미반영"

    # ── 묻힘 원인(신호미반영): 추천된 다른 말의 신호가 더 강했나 ──
    buried_by = None
    if ftype == "신호미반영":
        for h, info in ehorses.items():
            try:
                hh = int(h)
            except (TypeError, ValueError):
                continue
            if hh == focus:
                continue
            s = info.get("strength")
            if s is not None and s < 0 and (focus_strength is None or s < focus_strength) and hh in rec_horses:
                if buried_by is None or s < (ehorses.get(buried_by) or {}).get("strength", 0):
                    buried_by = hh

    # ── 놓친 신호 패턴(누적 통계용 라벨) ──
    if ftype == "타이밍":
        missed_signal = "마감 후 신호"
    elif ftype == "전적오판":
        missed_signal = "배당 신호 없음(전적 이변)"
    elif ftype == "페이크베팅":
        missed_signal = "페이크 후 진짜 신호"
    elif ftype == "노이즈":
        missed_signal = "대규모급락 속 집중신호 미분리"
    elif focus_grade == "🟡" or -15 < max_drop <= -8:
        missed_signal = "약한 신호 무시"
    else:
        missed_signal = "연속 하락 미감지"

    # ── 사람이 읽는 원인 + 개선점 ──
    dv = f"{max_drop}%" if max_drop else "변화 미미"
    if ftype == "신호미반영":
        reason = f"{focus}번은 급락 신호({dv})가 있었으나 추천에서 제외됨"
        if buried_by is not None:
            reason += f" — 추천은 {buried_by}번(더 강한 급락)이라 {focus}번 신호가 묻힘"
        improvement = "상위 3개 신호 말 전부 추천에 포함 · 배당 높아도 신호 있으면 표시"
    elif ftype == "페이크베팅":
        reason = f"{focus}번이 급락 후 반등(페이크 의심)해 신호를 신뢰하지 않음 → 실제로는 입상"
        improvement = "반등폭 < 급락폭이면 신호 유지 · 최종 배당이 초기 대비 낮으면 재평가"
    elif ftype == "노이즈":
        reason = f"전체 급락(대규모)으로 {focus}번 개별 급락({dv})이 노이즈로 처리됨"
        improvement = "대규모 급락 시 초과급락(집중도) 상위 말 우선 · 절대 10%+ 급락 승격"
    elif ftype == "전적오판":
        reason = f"{focus}번은 배당 변화 없이 입상 → 배당은 정상, 전적 판단 실패(이변)"
        improvement = "전적 하위라도 최근 컨디션·거리/기수 변경 반영 · 이변 조건 학습 강화"
    else:  # 타이밍
        reason = f"{focus}번 신호({dv})가 마감 후에 나타나 베팅 반영 불가"
        improvement = "T-3분/T-1분 수집 간격 단축 · 마감 임박 급변 조기 알림"

    # [4번] 실제 1착말 자체의 신호 보유 여부(초점 말이 1착이 아닐 수 있어 별도 산출)
    if winner == focus:
        winner_signal = has_signal
    else:
        wtl = _horse_repr_timeline(doc, winner)
        wdrop = min([p["pct"] for p in wtl if p.get("pct") is not None], default=0)
        wgrade = (ehorses.get(winner) or ehorses.get(str(winner)) or {}).get("grade")
        winner_signal = (wdrop <= -10) or (wgrade in ("🔴", "🟡"))

    return {
        "type": ftype, "label": FAIL_TYPE_LABEL.get(ftype, ftype),
        "focus": focus, "winner": winner, "missed": missed,
        "reason": reason, "improvement": improvement, "missed_signal": missed_signal,
        "timeline": tl, "maxDrop": max_drop, "buriedBy": buried_by,
        "afterClose": after_close, "massDrop": mass, "winnerSignal": bool(winner_signal),
    }


def _failure_record(rk, an, top3, doc, record, stats):
    """[1·4·7번] 실패 분류 → failure_review.json 누적(유형 카운트·놓친 패턴) + 규칙 자동 생성.
    반환: 이 경주의 분류 dict(레코드/히스토리에 첨부)."""
    fail = _classify_failure(rk, an, top3, doc)
    if not fail:
        return None
    d = _failure_load()
    ftype, mpat = fail["type"], fail["missed_signal"]
    d["type_counts"][ftype] = d["type_counts"].get(ftype, 0) + 1
    d["missed_patterns"][mpat] = d["missed_patterns"].get(mpat, 0) + 1
    ws = d.setdefault("winner_signal", {"had": 0, "total": 0})   # [4번] 1착말 신호 보유율 누적
    ws["total"] += 1
    if fail.get("winnerSignal"):
        ws["had"] += 1
    rec_bets = an.get("betRecommend") or []
    d["cases"].append({
        "race": rk, "date": time.strftime("%Y-%m-%d"),
        "top3": top3, "winner": fail["winner"], "focus": fail["focus"],
        "recommended": ["+".join(map(str, b.get("combo") or [])) for b in rec_bets[:6]],
        "type": ftype, "label": fail["label"], "missed_signal": mpat,
        "reason": fail["reason"], "improvement": fail["improvement"],
        "maxDrop": fail["maxDrop"], "t": time.time(),
    })
    d["cases"] = d["cases"][-500:]
    # [7번] 같은 놓친 패턴이 임계치+ 반복 → 개선 규칙 자동 생성(중복 방지)
    try:
        _failure_autorule(d, mpat, ftype, rk, stats)
    except Exception as e:
        print("[실패복기] 규칙 자동생성 실패:", e)
    _failure_save(d)
    return fail


def _failure_autorule(d, mpat, ftype, rk, stats):
    """[7번] 놓친 패턴 N건+ 반복 시 규칙 자동 생성. [5번] 생성 시점 추천 적중률을 before로 스냅샷."""
    cnt = d["missed_patterns"].get(mpat, 0)
    if cnt < FAIL_RULE_THRESHOLD:
        return
    if any(r.get("pattern") == mpat for r in d["rules"]):
        return   # 이미 규칙화된 패턴
    rule_text = {
        "연속 하락 미감지": "연속 하락 3회 이상 말은 배당 높아도 추천에 포함",
        "약한 신호 무시": "약한 급락(-8~-15%) 신호도 상위 3말이면 추천 후보 유지",
        "페이크 후 진짜 신호": "급락 후 반등이라도 반등폭<급락폭이면 신호 유지",
        "대규모급락 속 집중신호 미분리": "대규모 급락 시 초과급락(집중도) 상위 말 우선 추천",
        "마감 후 신호": "T-3분/T-1분 수집 간격 단축 · 마감 임박 급변 조기 알림",
        "배당 신호 없음(전적 이변)": "전적 하위라도 최근 컨디션·거리/기수 변경 이변 조건 강화",
    }.get(mpat, f"{mpat} 패턴 반복 → 추천 로직 보완")
    before_rate = None
    try:
        before_rate = ((stats or {}).get("recommend_hit") or {}).get("rate")
    except Exception:
        before_rate = None
    # [재설계] 50회+여도 자동으로 공식을 바꾸지 않는다 — '수동 검토 후보'로만 등록(status·manual_only).
    status = "검토후보" if cnt >= PATTERN_SAMPLE_SIG else "관찰누적"
    d["rules"].append({
        "pattern": mpat, "type": ftype, "text": rule_text,
        "basis": f"{cnt}건 반복 분석", "sample": cnt,
        "status": status, "manual_only": True,
        "before_rate": before_rate, "created_t": time.time(), "created": time.strftime("%Y-%m-%d"),
    })
    print(f"[실패복기] 📋 검토 후보 규칙(수동): {rule_text} (근거: {cnt}건, 표본 {PATTERN_SAMPLE_MIN}+ 충족)")


def _failure_report(rk):
    """[2·3번] 미적중 경주 복기 리포트 생성(온디맨드). 히스토리 결과+스냅샷에서 재구성.
    반환 {ok, raceKey, top3, recommended, was_hit, failure, timelines(1·2·3착), text}."""
    path, _, _ = _hist_path(rk)
    try:
        doc = json.load(open(path, encoding="utf-8"))
    except Exception:
        return {"ok": False, "error": f"'{rk}' 히스토리 없음"}
    result = doc.get("result") or {}
    top3 = [result.get("1st"), result.get("2nd"), result.get("3rd")]
    top3 = [int(x) for x in top3 if x not in (None, "")]
    if not top3:
        return {"ok": False, "error": "결과(착순) 미입력 경주"}
    rec = _triple_load().get(rk) or {}
    if not (rec.get("quinella") or rec.get("history")):
        rec = _rec_from_history(rk) or rec
    an = _triple_analyze(rk, rec)
    rec_bets = an.get("betRecommend") or []
    recommended = ["+".join(map(str, b.get("combo") or [])) for b in rec_bets[:6]]

    def _in3(combo):
        return combo and all(int(x) in top3 for x in combo)
    was_hit = any(_in3(b.get("combo") or []) for b in rec_bets)
    # [복기 UI 통합] 적중한 추천 조합(왜 맞았는지 표시용)
    hit_combos = ["+".join(map(str, b.get("combo") or [])) for b in rec_bets if _in3(b.get("combo") or [])]

    # [저장된 분류 우선, 없으면 즉석 재분류]
    fail = ((doc.get("review") or {}).get("failure")) if not was_hit else None
    if not was_hit and not fail:
        fail = _classify_failure(rk, an, top3, doc)

    # [3번] 1·2·3착 정답말 배당 역추적(각 말 타임라인 + 신호 라벨) — 적중/미적중 공통
    timelines = {}
    for h in top3:
        tl = _horse_repr_timeline(doc, h)
        timelines[str(h)] = [{"mb": p["mb"], "odds": p["odds"], "pct": p["pct"],
                              "after": p["after"], "src": p["src"],
                              "signal": _timeline_signal_label(p["pct"])} for p in tl]

    # [복기 상세 강화] 정답말 전적 점수 + 이상감지 요약(적중/미적중 공통 근거)
    scores = {}
    for f in (an.get("form") or []):
        if f.get("no") in top3 and f.get("totalScore") is not None:
            scores[str(f["no"])] = f.get("totalScore")
    anomaly_horse = an.get("anomalyHorse")
    key_horses = an.get("keyHorses") or []

    # [복기 UI 통합] 적중=왜 맞았는지 / 미적중=왜 놓쳤는지, 한 리포트로.
    if was_hit:
        text = _success_report_text(rk, top3, hit_combos, timelines)
    else:
        text = _failure_report_text(rk, top3, recommended, was_hit, fail, timelines)
    return {"ok": True, "raceKey": rk, "top3": top3, "recommended": recommended,
            "was_hit": was_hit, "hit_combos": hit_combos, "failure": fail,
            "timelines": timelines, "scores": scores,
            "anomaly_horse": anomaly_horse, "key_horses": key_horses, "text": text}


def _success_report_text(rk, top3, hit_combos, timelines):
    """[복기 UI 통합] 적중 경주 '왜 맞았는지' 텍스트 리포트(정답말 신호 근거 포함)."""
    lines = [f"✅ 적중 복기 - {rk}", ""]
    lines.append(f"실제 정답: {'-'.join(map(str, top3))}")
    lines.append(f"적중 추천: {' / '.join(hit_combos) if hit_combos else '(추천 조합 적중)'}")
    lines.append("")
    lines.append("💡 왜 맞았나 — 정답말 신호 근거:")
    rank_label = {0: "1착", 1: "2착", 2: "3착"}
    for i, h in enumerate(top3):
        tl = timelines.get(str(h)) or []
        # 가장 강한 신호(급락 큰 순)를 요약
        sig = ""
        strong = [p for p in tl if p.get("pct") is not None and p["pct"] <= -8]
        if strong:
            best = min(strong, key=lambda p: p["pct"])
            sig = f" — 최대 {best['pct']}% 급락({best.get('signal', '')})"
        elif tl:
            sig = " — 뚜렷한 급락 없음(전적/인기 기반)"
        lines.append(f"  [{rank_label.get(i, str(i + 1) + '착')} {h}번]{sig}")
        for p in tl:
            mb = p.get("mb")
            tstr = (f"T-{mb}분" if isinstance(mb, (int, float)) and mb >= 0
                    else (f"마감후{abs(mb)}분" if isinstance(mb, (int, float)) else (p.get("src") or "")))
            pv = p.get("pct")
            pctstr = f" ({'+' if isinstance(pv, (int, float)) and pv > 0 else ''}{pv}%)" if pv is not None else ""
            lines.append(f"    {tstr}: {p.get('odds')}배{pctstr} {p.get('signal', '')}".rstrip())
    lines.append("")
    lines.append("🔁 재현 포인트: 이 신호 패턴을 다음 경주에서도 우선 반영")
    return "\n".join(lines)


def _failure_report_text(rk, top3, recommended, was_hit, fail, timelines):
    """[2·3번] 복기 리포트를 사람이 읽는 텍스트로."""
    if was_hit:
        return f"✅ {rk} — 추천 적중 경주(복기 대상 아님)"
    lines = [f"❌ 복기 리포트 - {rk}", ""]
    if fail:
        lines.append(f"실패 유형: {fail.get('label', fail.get('type', ''))}")
        lines.append("")
    lines.append(f"실제 정답: {'-'.join(map(str, top3))}")
    lines.append(f"우리 추천: {' / '.join(recommended) if recommended else '없음'}")
    lines.append("")
    focus = (fail or {}).get("focus")
    if focus is not None:
        lines.append(f"❓ 왜 {focus}번을 놓쳤나:")
        tl = timelines.get(str(focus)) or []
        for p in tl:
            mb = p.get("mb")
            tstr = (f"T-{mb}분" if isinstance(mb, (int, float)) and mb >= 0
                    else (f"마감후{abs(mb)}분" if isinstance(mb, (int, float)) else (p.get("src") or "")))
            pctstr = f" ({p['pct']:+d}%)" if p.get("pct") is not None else ""
            lines.append(f"  {tstr}: {p['odds']}배{pctstr} — {p.get('signal', '')}")
        lines.append(f"  → {fail.get('reason', '')}")
        lines.append("")
    # [복기 완성] 1·2·3착 정답말 배당 역추적 전체를 텍스트 리포트에도 포함(프론트 카드와 동일 수준).
    #   기존엔 focus(놓친 1마)만 텍스트에 있어 복사/내보내기 시 정보가 빠졌다.
    if timelines:
        lines.append("📈 정답말 역추적(1·2·3착):")
        rank_label = {0: "1착", 1: "2착", 2: "3착"}
        for i, h in enumerate(top3):
            lines.append(f"  [{rank_label.get(i, str(i + 1) + '착')} {h}번]")
            tl = timelines.get(str(h)) or []
            if not tl:
                lines.append("    (타임라인 없음)")
                continue
            for p in tl:
                mb = p.get("mb")
                tstr = (f"T-{mb}분" if isinstance(mb, (int, float)) and mb >= 0
                        else (f"마감후{abs(mb)}분" if isinstance(mb, (int, float)) else (p.get("src") or "")))
                pv = p.get("pct")
                pctstr = f" ({'+' if isinstance(pv, (int, float)) and pv > 0 else ''}{pv}%)" if pv is not None else ""
                lines.append(f"    {tstr}: {p.get('odds')}배{pctstr} {p.get('signal', '')}".rstrip())
        lines.append("")
    lines.append(f"🔍 개선점: {(fail or {}).get('improvement', '상위 3신호 말 전부 추천 포함')}")
    return "\n".join(lines)


def _failure_stats():
    """[5·6번] 복기 대시보드 데이터. 유형별 분포 + 놓친 패턴 TOP + 규칙(개선 전/후 적중률)."""
    d = _failure_load()
    tc = d.get("type_counts") or {}
    total = sum(tc.values())
    types = []
    for k in FAIL_TYPE_ORDER:
        n = tc.get(k, 0)
        types.append({"type": k, "label": FAIL_TYPE_LABEL.get(k, k), "count": n,
                      "pct": round(n / total * 100) if total else 0})
    top_type = max(types, key=lambda x: x["count"]) if total else None
    missed = sorted((d.get("missed_patterns") or {}).items(), key=lambda kv: -kv[1])
    missed_top = [{"pattern": k, "count": v} for k, v in missed[:5]]

    # [5번] 규칙별 개선 전/후 적중률(after=규칙 생성 이후 결과 레코드의 추천 적중률)
    records = (_learning_load().get("records") or [])
    rules = []
    for r in (d.get("rules") or []):
        ct = r.get("created_t") or 0
        after = [x for x in records if (x.get("t") or 0) >= ct]
        hit = sum(1 for x in after if x.get("was_hit"))
        after_rate = round(hit / len(after) * 100) if after else None
        rules.append({**r, "after_rate": after_rate, "after_n": len(after)})

    # [4번] 실제 1착말 신호 보유율(신호 감지는 됐으나 추천 미반영 규모)
    ws = d.get("winner_signal") or {"had": 0, "total": 0}
    winner_signal_rate = round(ws["had"] / ws["total"] * 100) if ws.get("total") else None

    # [4번] 개선 후 적중률 변화(가장 최근 규칙 기준 전/후 · 규칙 없으면 전체)
    improve = None
    if rules:
        last = max(rules, key=lambda r: r.get("created_t") or 0)
        improve = {"before": last.get("before_rate"), "after": last.get("after_rate"),
                   "rule": last.get("text"), "since": last.get("created")}

    return {"total": total, "types": types, "top_type": top_type,
            "missed_top": missed_top, "rules": rules,
            "winner_signal": {"had": ws.get("had", 0), "total": ws.get("total", 0),
                              "rate": winner_signal_rate},
            "improve": improve,
            "recent": list(reversed((d.get("cases") or [])[-12:]))}


# ═════════ [고배당 미적중 심층 분석] data/high_odds_review/ — 복승30배+/삼복승100배+ 경주 복기 ═════════
#   기존 실패복기(_classify_failure·_horse_repr_timeline·_failure_report)·명예의전당(_highlight_save 30/100
#   기준)을 재사용해 '고배당' 경주만 골라 ①추출·적중대조 ②정답말 심층 ③타임라인 대조 ④A/B/C 유형
#   ⑤개선 시뮬 ⑥내일 규칙 ⑦누적통계를 산출. ⚠ 기존 시스템 무삭제 — 고배당 필터·A/B/C·시뮬 계층만 추가.
HIGH_ODDS_REVIEW_DIR = os.path.join(os.path.dirname(__file__), "data", "high_odds_review")
HIGH_ODDS_Q = 30    # 복승 고배당 기준(명예의전당과 동일 _apply_result_learning)
HIGH_ODDS_T = 100   # 삼복승 고배당 기준

# [4번] 실패 5유형 → A/B/C 3그룹 (구조불가 / 개선가능 / 로직오류)
_ABC_GROUP = {
    "타이밍":     ("A", "구조적 불가", "개선 불가(패스)"),
    "전적오판":   ("A", "구조적 불가", "개선 불가(패스)"),
    "노이즈":     ("B", "시스템 개선 가능", "개선 후 잡을 수 있음"),
    "페이크베팅": ("C", "로직 개선 필요", "로직 수정으로 해결"),
    "신호미반영": ("C", "로직 개선 필요", "로직 수정으로 해결"),
}


# ── race_results(tracked 코퍼스) 기반 소스 — odds_history(gitignore)가 비어도 동작 ──
#   실데이터는 race_results/*.json 에 있다: result·odds_timeline(복승 조합별)·prediction·result_analysis.
def _rr_load_by_key(rk):
    """raceKey(또는 race_id)로 race_results/ 파일 1건 로드(가장 최근)."""
    if not os.path.isdir(RACE_RESULTS_DIR):
        return None
    cands = []
    for fn in os.listdir(RACE_RESULTS_DIR):
        if not fn.endswith(".json"):
            continue
        try:
            d = json.load(open(os.path.join(RACE_RESULTS_DIR, fn), encoding="utf-8"))
        except Exception:
            continue
        if (d.get("raceKey") == rk) or (d.get("race_id") == rk) or (rk in (d.get("raceKey") or "")):
            cands.append((d.get("saved_at") or "", d))
    if not cands:
        return None
    cands.sort(key=lambda x: x[0], reverse=True)
    return cands[0][1]


def _rr_top3(rr):
    res = rr.get("result") or {}
    return [int(x) for x in (res.get("1st"), res.get("2nd"), res.get("3rd")) if x not in (None, "")]


def _rr_horse_timeline(rr, h):
    """정답말 h 의 대표 배당 타임라인 — 각 스냅샷에서 h 가 낀 최저 복승 조합(자금유입 대리).
    반환 [{mb,odds,pct,time,after,src,signal}] (시간순). _timeline_signal_label 재사용."""
    h = str(h)
    tl, prev = [], None
    for s in (rr.get("odds_timeline") or []):
        best = None
        for k, v in (s.get("quinella") or {}).items():
            if h in str(k).split("+"):
                ov = _safe_num(v)
                if ov and ov > 0 and (best is None or ov < best):
                    best = ov
        if not best:
            continue
        mb = s.get("minutes_before")
        pct = round((best - prev) / prev * 100) if (prev and prev > 0) else None
        tl.append({"mb": mb, "odds": round(best, 1), "pct": pct, "time": s.get("time"),
                   "after": bool(isinstance(mb, (int, float)) and mb < 0),
                   "src": "복승", "signal": _timeline_signal_label(pct)})
        prev = best
    return tl


def _rr_winning_odds(rr, top3):
    """정답 복승(top2) 배당 — odds_timeline 최신값 → odds_at_start → investment 폴백. 삼복승은 대개 미수집."""
    pair = set(str(x) for x in sorted(top3[:2]))
    q = None
    for s in reversed(rr.get("odds_timeline") or []):
        for k, v in (s.get("quinella") or {}).items():
            if set(str(k).split("+")) == pair:
                ov = _safe_num(v)
                if ov:
                    q = ov
                    break
        if q is not None:
            break
    if q is None:
        oas = ((rr.get("odds_at_start") or {}).get("quinella")) or {}
        for k, v in oas.items():
            if set(str(k).split("+")) == pair:
                q = _safe_num(v)
    if q is None:
        q = _safe_num((rr.get("investment") or {}).get("quinella_odds"))
    t = _safe_num((rr.get("investment") or {}).get("trifecta_odds"))
    return {"quinella": q, "trifecta": t}


def _rr_recommend(rr):
    """우리 추천 조합(prediction.recommend_main/sub) → (마번 집합, 표시 문자열 리스트)."""
    pred = rr.get("prediction") or {}
    rec, strs = set(), []
    for cs in (pred.get("recommend_main"), pred.get("recommend_sub")):
        if not cs:
            continue
        strs.append(str(cs))
        for x in re.split(r"[+\s]+", str(cs)):
            try:
                rec.add(int(x))
            except (TypeError, ValueError):
                pass
    return rec, strs


def _rr_classify(rr, top3, timelines):
    """[4번] race_results 기반 미적중 유형 분류(_classify_failure 5유형 로직 이식).
    반환 {type,label,focus,winner,missed,reason,improvement,maxDrop,winnerSignal}."""
    rec, _ = _rr_recommend(rr)
    ra = rr.get("result_analysis") or {}
    winner = top3[0]
    missed = [h for h in top3 if h not in rec]
    focus = missed[0] if missed else winner
    tl = timelines.get(str(focus)) or []
    pcts = [p["pct"] for p in tl if p.get("pct") is not None]
    max_drop = min(pcts) if pcts else 0
    pre = [p for p in tl if not p.get("after") and p.get("pct") is not None]
    after = [p for p in tl if p.get("after") and p.get("pct") is not None]
    pre_drop = min([p["pct"] for p in pre], default=0)
    after_drop = min([p["pct"] for p in after], default=0)
    tags = ra.get("pattern_tags") or []
    mass = any("대규모" in str(t) for t in tags)
    has_pre_signal = pre_drop <= -10
    has_signal = max_drop <= -10

    if after and after_drop <= -10 and not has_pre_signal and after_drop < pre_drop:
        ftype = "타이밍"
    elif not has_signal:
        ftype = "전적오판"
    elif mass:
        ftype = "노이즈"
    elif has_pre_signal:
        ftype = "신호미반영"
    else:
        ftype = "전적오판"

    dv = f"{max_drop}%" if max_drop else "변화 미미"
    if ftype == "신호미반영":
        reason = f"{focus}번은 마감 전 급락 신호({dv})가 있었으나 추천 조합에서 제외됨"
        improvement = "상위 3신호 말 전부 추천 포함 · 배당 높아도 신호 있으면 후보 유지"
    elif ftype == "노이즈":
        reason = f"전체 급락(대규모)으로 {focus}번 개별 급락({dv})이 노이즈로 처리됨"
        improvement = "대규모 급락 시 절대급락폭(-50%+)·초과급락 상위 말 집중신호 승격"
    elif ftype == "타이밍":
        reason = f"{focus}번 신호({dv})가 마감 후에 나타나 베팅 반영 불가"
        improvement = "T-3분/T-1분 수집 간격 단축 · 마감 임박 급변 조기 알림"
    else:  # 전적오판
        reason = f"{focus}번은 마감 전 배당 변화 없이 입상 → 배당 정상, 전적 이변(구조적)"
        improvement = "전적 하위라도 최근 컨디션·거리/기수 변경 이변 조건 학습 강화"

    # 1착말 자체 신호 보유 여부
    wtl = timelines.get(str(winner)) or tl
    wdrop = min([p["pct"] for p in wtl if p.get("pct") is not None], default=0)
    return {"type": ftype, "label": FAIL_TYPE_LABEL.get(ftype, ftype),
            "focus": focus, "winner": winner, "missed": missed,
            "reason": reason, "improvement": improvement, "maxDrop": max_drop,
            "winnerSignal": bool(wdrop <= -10)}


def _high_odds_simulate(fail):
    """[5번] '개선된 로직 적용 시' 반사실 시뮬레이션. 실패 유형·초점말 급락폭 기반."""
    if not fail:
        return {"applicable": False, "improved_logic": "-", "expected": "분류 불가", "catchable": False}
    ftype, focus, md = fail.get("type"), fail.get("focus"), (fail.get("maxDrop") or 0)
    if ftype == "노이즈":
        catch = md <= -50
        return {"applicable": True, "improved_logic": "v2.1.37 절대급락폭 병행 감지",
                "expected": (f"{focus}번 절대 {md}% 급락 → 집중신호 감지 가능 → 적중 가능"
                             if catch else f"{focus}번 급락폭 {md}%로 절대기준(-50%) 미달 → 여전히 한계"),
                "catchable": catch}
    if ftype == "신호미반영":
        return {"applicable": True, "improved_logic": "상위 3신호 말 전부 추천 포함(배당 상한 완화)",
                "expected": f"{focus}번 급락신호({md}%) 보유 → 규칙 적용 시 추천 포함 → 적중 가능",
                "catchable": True}
    if ftype == "페이크베팅":
        return {"applicable": True, "improved_logic": "반등폭<급락폭이면 신호 유지",
                "expected": f"{focus}번 페이크 판정 해제 → 신호 유지 → 적중 가능", "catchable": True}
    # 타이밍(마감 후)·전적오판(신호 없음) = 구조적 한계
    return {"applicable": False, "improved_logic": "-",
            "expected": "구조적 한계 — 마감 후 신호/신호 부재라 로직 개선해도 감지 불가", "catchable": False}


def _high_odds_next_rules(fail):
    """[6번] 미적중 유형별 내일 적용 규칙(고배당 특화)."""
    if not fail:
        return []
    ftype = fail.get("type")
    rules = []
    if ftype == "타이밍":
        rules.append("마감 후 대급락(-50%+) 말은 다음 경주 삼복승 보험픽 고려")
    if ftype == "전적오판":
        rules.append("수집 타임라인에 신호 없는 고배당 → 이변 가능성 낮음 → 패스 가중")
    if ftype in ("신호미반영", "페이크베팅"):
        rules.append("고배당이라도 급락신호 상위 말은 추천 후보 유지(배당 상한 완화)")
    if ftype == "노이즈":
        rules.append("대규모 급락 시 절대급락폭(-50%+) 말 집중신호 승격")
    return rules


def _high_odds_prefilter(rr):
    """전체 분석 전 저비용 판정: race_results 의 결과 top3 + 정답 복승 배당으로 고배당 여부만 확인.
    반환 (is_high, top3, odds) — 결과 없으면 (False, [], {})."""
    top3 = _rr_top3(rr)
    if len(top3) < 2:
        return False, [], {}
    odds = _rr_winning_odds(rr, top3)
    q, t = odds.get("quinella"), odds.get("trifecta")
    is_high = bool((q and q >= HIGH_ODDS_Q) or (t and t >= HIGH_ODDS_T))
    return is_high, top3, {"quinella": q, "trifecta": t}


def _high_odds_review_one(rk, save=True, rr=None):
    """[1~6번] 고배당 경주 1건 심층 분석 — race_results 기반(정답말 역추적·5유형·ABC·시뮬).
    고배당이 아니면 {ok, is_high_odds:False}만 반환(저장 안 함)."""
    if rr is None:
        rr = _rr_load_by_key(rk)
    if not rr:
        return {"ok": False, "error": f"'{rk}' 결과 데이터 없음"}
    rk = rr.get("raceKey") or rk
    is_high, top3, odds = _high_odds_prefilter(rr)
    if not top3:
        return {"ok": False, "error": "결과(착순) 미입력 경주"}
    if not is_high:
        return {"ok": True, "is_high_odds": False, "race": rk, "result": "-".join(map(str, top3))}

    # 정답말(1·2·3착) 타임라인 역추적(race_results odds_timeline 기반)
    timelines = {str(h): _rr_horse_timeline(rr, h) for h in top3}
    rec_set, rec_strs = _rr_recommend(rr)
    # 적중 판정: result_analysis(main/sub) 우선, 없으면 추천 조합이 top3 안에 드는지
    ra = rr.get("result_analysis") or {}
    if ra.get("main_hit") is not None or ra.get("sub_hit") is not None:
        was_hit = bool(ra.get("main_hit") or ra.get("sub_hit"))
    else:
        was_hit = any(set(re.split(r"[+\s]+", s)) <= set(map(str, top3)) for s in rec_strs if s)
    fail = None if was_hit else _rr_classify(rr, top3, timelines)
    q, t = odds.get("quinella"), odds.get("trifecta")
    grp = _ABC_GROUP.get((fail or {}).get("type"), ("?", "미분류", ""))

    # [2번] 정답말(1·2·3착) 심층 — 마감 전 신호 유무 + 놓친 이유(초점말)
    winner_analysis = {}
    rank_label = {0: "1착", 1: "2착", 2: "3착"}
    for i, h in enumerate(top3):
        tl = timelines.get(str(h)) or []
        pre = [p for p in tl if not p.get("after") and p.get("pct") is not None]
        after = [p for p in tl if p.get("after") and p.get("pct") is not None]
        strong_pre = [p for p in pre if p["pct"] <= -10]
        sig_label = ("마감 전 급락 신호 있음" if strong_pre
                     else ("마감 후 대급락(참고만)" if after and min(p["pct"] for p in after) <= -50
                           else "신호 없음(정상 인기)"))
        entry = {"rank": rank_label.get(i, f"{i + 1}착"), "signal_before_close": sig_label, "timeline": tl}
        if fail and h == fail.get("focus"):
            sim = _high_odds_simulate(fail)
            entry["why_missed"] = fail.get("reason")
            entry["could_we_catch"] = bool(sim.get("catchable"))
            entry["improvement"] = fail.get("improvement")
        winner_analysis[f"{h}번"] = entry

    # [3번] 타임라인 대조 — 초점(놓친) 말 배당 변화 vs 우리 추천 초점
    focus = (fail or {}).get("focus", top3[0])
    our_focus = (rec_strs or ["없음"])[0]
    tl_cmp = []
    for p in (timelines.get(str(focus)) or []):
        mb = p.get("mb")
        tstr = (f"T-{mb}분" if isinstance(mb, (int, float)) and mb >= 0
                else (f"마감후{abs(mb)}분" if isinstance(mb, (int, float)) else (p.get("src") or "")))
        tl_cmp.append({"time": tstr, "odds": p.get("odds"), "pct": p.get("pct"),
                       "signal": p.get("signal"), "our_focus": our_focus})

    review = {
        "race": rk, "date": rr.get("date") or "",
        "result": "-".join(map(str, top3)),
        "result_odds": {"quinella": q, "trifecta": t,
                        "quinella_high": bool(q and q >= HIGH_ODDS_Q),
                        "trifecta_high": bool(t and t >= HIGH_ODDS_T)},
        "is_high_odds": True,
        "our_recommend": rec_strs,
        "hit": was_hit,
        "winner_analysis": winner_analysis,          # [2번]
        "timeline_comparison": tl_cmp,               # [3번]
        "abc_type": grp[0], "abc_label": grp[1], "abc_action": grp[2],   # [4번]
        "fail_type": (fail or {}).get("type"), "fail_label": (fail or {}).get("label"),
        "root_cause": ((fail or {}).get("reason") if fail else "고배당 적중"),
        "prevention": (fail or {}).get("improvement") or "",
        "simulation": (None if was_hit else _high_odds_simulate(fail)),   # [5번]
        "next_rules": ([] if was_hit else _high_odds_next_rules(fail)),   # [6번]
        "lesson": ("고배당 적중 — 신호 재현 포인트 확보" if was_hit
                   else ((fail or {}).get("improvement") or "구조적 미적중")),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
    }
    if save:
        try:
            os.makedirs(HIGH_ODDS_REVIEW_DIR, exist_ok=True)
            safe = re.sub(r"[^0-9A-Za-z가-힣]", "_", rk)
            p = os.path.join(HIGH_ODDS_REVIEW_DIR, (review["date"] or "nodate") + "_" + safe + ".json")
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(review, f, ensure_ascii=False, indent=1)
            os.replace(tmp, p)
        except Exception as e:
            print("[고배당복기] 저장 실패:", e)
    return {"ok": True, **review}


def _high_odds_scan(date=None, save=True, limit=800):
    """[1번] 결과 입력된 경주(race_results/)를 스캔해 고배당(복승30+/삼복승100+)만 심층 분석·저장.
    저비용 프리필터로 고배당만 골라 정답말 역추적·분류 수행 → 부하 최소화."""
    out, scanned = [], 0
    if os.path.isdir(RACE_RESULTS_DIR):
        for fn in sorted(os.listdir(RACE_RESULTS_DIR), reverse=True):
            if not fn.endswith(".json") or scanned >= limit:
                continue
            try:
                rr = json.load(open(os.path.join(RACE_RESULTS_DIR, fn), encoding="utf-8"))
            except Exception:
                continue
            rk = rr.get("raceKey") or rr.get("race_id")
            if not rk:
                continue
            if date and date != (rr.get("date") or "") and date not in (rr.get("race_id") or ""):
                continue
            is_high, top3, _ = _high_odds_prefilter(rr)
            if not is_high:
                continue
            scanned += 1
            rev = _high_odds_review_one(rk, save=save, rr=rr)
            if rev.get("ok") and rev.get("is_high_odds"):
                out.append(rev)
    return out


def _high_odds_stats():
    """[7번] 고배당 누적 통계 — 총/적중/미적중 + A/B/C 분포 + 개선 후 예상 추가 적중(B+C catchable)."""
    reviews = []
    if os.path.isdir(HIGH_ODDS_REVIEW_DIR):
        for fn in os.listdir(HIGH_ODDS_REVIEW_DIR):
            if not fn.endswith(".json"):
                continue
            try:
                reviews.append(json.load(open(os.path.join(HIGH_ODDS_REVIEW_DIR, fn), encoding="utf-8")))
            except Exception:
                continue
    total = len(reviews)
    hits = sum(1 for r in reviews if r.get("hit"))
    misses = [r for r in reviews if not r.get("hit")]
    abc = {"A": 0, "B": 0, "C": 0}
    for r in misses:
        g = r.get("abc_type")
        if g in abc:
            abc[g] += 1
    m = len(misses)
    abc_pct = {k: {"count": v, "pct": round(v / m * 100) if m else 0,
                   "label": {"A": "구조적 불가", "B": "시스템 개선 가능", "C": "로직 개선 필요"}[k]}
               for k, v in abc.items()}
    catchable = sum(1 for r in misses if (r.get("simulation") or {}).get("catchable"))
    return {
        "total": total, "hits": hits, "misses": m,
        "hit_rate": round(hits / total * 100, 1) if total else 0.0,
        "abc": abc_pct,
        "expected_additional_hits": catchable,   # 유형B+C(개선가능) 해결 시 추가 적중 가능 수
        "projected_hit_rate": round((hits + catchable) / total * 100, 1) if total else 0.0,
        "recent_misses": [{"race": r.get("race"), "date": r.get("date"), "result": r.get("result"),
                           "abc": r.get("abc_type"), "fail": r.get("fail_label"),
                           "odds": r.get("result_odds"), "catchable": (r.get("simulation") or {}).get("catchable")}
                          for r in sorted(misses, key=lambda x: x.get("date") or "", reverse=True)[:15]],
    }


@app.route("/api/failure/report", methods=["GET", "POST"])
def failure_report():
    """[2·3번] 미적중 경주 복기 리포트(정답말 역추적 포함). ?raceKey= 또는 body{raceKey}."""
    rk = request.args.get("raceKey") or ""
    if not rk and request.method == "POST":
        rk = (request.json or {}).get("raceKey") or ""
    rk = (rk or "").strip()
    if not rk:
        return jsonify({"ok": False, "error": "raceKey가 필요합니다."}), 400
    rk = _resolve_race_key(rk) or rk
    return jsonify(_failure_report(rk))


@app.route("/api/failure/stats", methods=["GET"])
def failure_stats():
    """[5·6번] 복기 학습 대시보드(유형 분포·놓친 패턴 TOP·개선 규칙 전/후 적중률)."""
    return jsonify(_failure_stats())


@app.route("/api/failure/rules", methods=["GET"])
def failure_rules():
    """[7번] 실패에서 자동 학습된 규칙 목록."""
    d = _failure_load()
    return jsonify({"rules": d.get("rules") or []})


def _high_odds_case_save(case):
    """[수동 케이스 학습] 사용자가 짚어준 놓친 케이스(카와사키 11R 등)를 data/high_odds_review/에 저장.
      자동 생성 복기(_high_odds_review_one)와 파일명 충돌 안 나게 '_case' 접미사 사용. manual_case=True 태깅.
      반환 저장 경로."""
    os.makedirs(HIGH_ODDS_REVIEW_DIR, exist_ok=True)
    c = dict(case or {})
    c["manual_case"] = True
    c.setdefault("saved_at", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))
    safe = re.sub(r"[^0-9A-Za-z가-힣]", "_", str(c.get("race") or "case"))
    p = os.path.join(HIGH_ODDS_REVIEW_DIR, (c.get("date") or "nodate") + "_" + safe + "_case.json")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    return p


def _high_odds_cases_load():
    """저장된 수동 케이스(manual_case=True) 목록(최신순)."""
    out = []
    if os.path.isdir(HIGH_ODDS_REVIEW_DIR):
        for fn in sorted(os.listdir(HIGH_ODDS_REVIEW_DIR), reverse=True):
            if not fn.endswith("_case.json"):
                continue
            try:
                out.append(json.load(open(os.path.join(HIGH_ODDS_REVIEW_DIR, fn), encoding="utf-8")))
            except Exception:
                continue
    return out


@app.route("/api/high-odds-review", methods=["GET", "POST"])
def high_odds_review():
    """[고배당 심층분석] 복승30배+/삼복승100배+ 경주 복기.
      GET ?raceKey=      → 해당 경주 심층 분석(고배당 아니면 is_high_odds:False)
      GET ?list=1        → 저장된 고배당 복기 목록(최신순 경량)
      GET ?cases=1       → 수동 학습 케이스(카와사키 11R 등) 목록
      GET ?stats=1       → [7번] 누적 통계(총/적중/미적중·A/B/C·개선 후 예상)
      POST body{case}    → 수동 케이스 저장 / body{date?} → [1번] 스캔·저장"""
    if request.args.get("cases"):
        return jsonify({"cases": _high_odds_cases_load()})
    if request.args.get("stats"):
        return jsonify(_high_odds_stats())
    if request.args.get("list"):
        out = []
        if os.path.isdir(HIGH_ODDS_REVIEW_DIR):
            for fn in sorted(os.listdir(HIGH_ODDS_REVIEW_DIR), reverse=True):
                if not fn.endswith(".json"):
                    continue
                try:
                    r = json.load(open(os.path.join(HIGH_ODDS_REVIEW_DIR, fn), encoding="utf-8"))
                except Exception:
                    continue
                out.append({"race": r.get("race"), "date": r.get("date"), "result": r.get("result"),
                            "hit": r.get("hit"), "abc_type": r.get("abc_type"),
                            "fail_label": r.get("fail_label"), "result_odds": r.get("result_odds")})
        return jsonify({"reviews": out, "count": len(out)})
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        if body.get("case"):                       # [수동 케이스 저장]
            try:
                p = _high_odds_case_save(body["case"])
                return jsonify({"ok": True, "saved": os.path.basename(p)})
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)}), 500
        date = body.get("date") or None
        found = _high_odds_scan(date)
        return jsonify({"scanned_high_odds": len(found),
                        "hits": sum(1 for r in found if r.get("hit")),
                        "misses": sum(1 for r in found if not r.get("hit")),
                        "races": [r.get("race") for r in found]})
    rk = (request.args.get("raceKey") or "").strip()
    if not rk:
        return jsonify({"ok": False, "error": "raceKey 또는 ?list/stats/POST scan 이 필요합니다."}), 400
    rk = _resolve_race_key(rk) or rk
    return jsonify(_high_odds_review_one(rk))


@app.route("/api/hall-of-fame", methods=["GET"])
def hall_of_fame():
    """[5번] 고배당 적중 명예의 전당(복승 30배+/삼복승 100배+) — 최신순 + 스토리·타임라인."""
    try:
        arr = json.load(open(HIGHLIGHT_FILE, encoding="utf-8"))
    except Exception:
        arr = []
    arr = sorted(arr, key=lambda x: x.get("t", 0), reverse=True)
    return jsonify({"wins": arr[:100], "count": len(arr)})


@app.route("/api/races/list", methods=["GET"])
def races_list():
    """[2번] 결과 입력용 경주 목록(시간순). ?date=YYYY-MM-DD &pending=1(미입력만).
    → {races:[{raceKey,area,num,hasResult,top3,lastT,snaps}]} — 자동완성·순서대로 빠른입력용."""
    date = (request.args.get("date") or "").strip()
    pending = request.args.get("pending") in ("1", "true", "yes")
    out = []
    if os.path.isdir(ODDS_HISTORY_DIR):
        for fn in os.listdir(ODDS_HISTORY_DIR):
            if not fn.endswith(".json"):
                continue
            try:
                d = json.load(open(os.path.join(ODDS_HISTORY_DIR, fn), encoding="utf-8"))
            except Exception:
                continue
            rk = d.get("raceKey") or d.get("race")
            if not rk:
                continue
            if date and date not in (rk or "") and date != (d.get("date") or ""):
                continue
            has_result = bool(d.get("result"))
            if pending and has_result:
                continue
            area, num = _area_num(rk)
            res = d.get("result") or {}
            top3 = [res.get("1st"), res.get("2nd"), res.get("3rd")]
            snaps = d.get("snapshots") or []
            out.append({"raceKey": rk, "area": area, "num": num, "hasResult": has_result,
                        "top3": [x for x in top3 if x not in (None, "")],
                        "lastT": (snaps[-1].get("t") if snaps else 0), "snaps": len(snaps)})
    out.sort(key=lambda x: (x.get("lastT") or 0))   # 수집 시각순 → 결과 목록 시간순 매칭
    return jsonify({"races": out, "count": len(out)})


@app.route("/api/history/record-result", methods=["POST"])
def history_record_result():
    """경주 결과 입력 → 히스토리에 결과 기록 + 자동학습 + 완전 저장(race_results).
    body: {raceKey, result:{'1st':7,'2nd':1,'3rd':9,'4th':2}, stake?, payout?, finalOdds?,
           memo?, budget?, quinellaOdds?, trifectaOdds?, distance?, trackCondition?, weather?, horseCount?}"""
    body = request.json or {}
    rk_in = (body.get("raceKey") or "").strip()
    result = body.get("result") or {}
    top3 = [result.get("1st"), result.get("2nd"), result.get("3rd")]
    top3 = [int(x) for x in top3 if x not in (None, "")]
    if not rk_in or len(top3) < 1:
        return jsonify({"error": "raceKey와 결과(1~3착)가 필요합니다."}), 400
    # [매칭 유연화] '서울 5' ↔ '2026-07-05 서울 5경주' 형식 불일치 방어 → 실제 분석 key로 해석
    rk = _resolve_race_key(rk_in) or rk_in
    # [2번] 결과 입력 부가정보(메모·확정배당·예산·경주조건) → 완전 저장 파일에 반영
    inputs = {
        "memo": body.get("memo"), "budget": body.get("budget"),
        "main_bet": body.get("mainBet"), "sub_bet": body.get("subBet"),
        "quinella_odds": body.get("quinellaOdds"), "trifecta_odds": body.get("trifectaOdds"),
        "distance": body.get("distance"), "track_condition": body.get("trackCondition"),
        "weather": body.get("weather"), "horse_count": body.get("horseCount"),
    }
    record, stats = _apply_result_learning(rk, result, top3, body.get("finalOdds"),
                                           stake=body.get("stake"), payout=body.get("payout"), inputs=inputs)
    return jsonify({"ok": True, "record": record, "stats": stats, "raceKey": rk,
                    "matchedFrom": rk_in if rk != rk_in else None,
                    "raceResultSaved": record.get("race_result_saved"),
                    "dataErrors": record.get("race_result_errors") or []})


@app.route("/api/race-results/list", methods=["GET"])
def race_results_list():
    """[1번] 완전 저장된 경주 결과 목록(최신순) → 요약 리스트."""
    out = []
    if os.path.isdir(RACE_RESULTS_DIR):
        for fn in sorted(os.listdir(RACE_RESULTS_DIR), reverse=True):
            if not fn.endswith(".json"):
                continue
            try:
                d = json.load(open(os.path.join(RACE_RESULTS_DIR, fn), encoding="utf-8"))
            except Exception:
                continue
            out.append({"race_id": d.get("race_id"), "date": d.get("date"),
                        "track": d.get("track"), "round": d.get("round"),
                        "result": d.get("result"),
                        "main_hit": (d.get("result_analysis") or {}).get("main_hit"),
                        "strategy": (d.get("prediction") or {}).get("strategy"),
                        "profit": (d.get("investment") or {}).get("profit"),
                        "valid": (d.get("validation") or {}).get("ok", True)})
    return jsonify({"results": out, "count": len(out)})


@app.route("/api/race-results/get", methods=["POST", "GET"])
def race_results_get():
    """[1번] 경주 결과 완전 파일 1건 조회. {race_id | raceKey}."""
    if request.method == "POST":
        body = request.json or {}
        rid, rk = body.get("race_id"), body.get("raceKey")
    else:
        rid, rk = request.args.get("race_id"), request.args.get("raceKey")
    if not rid and rk:
        rid, _ = _race_result_id(rk)
    if not rid:
        return jsonify({"error": "race_id 또는 raceKey가 필요합니다."}), 400
    path = os.path.join(RACE_RESULTS_DIR, os.path.basename(rid) + ".json")
    try:
        return jsonify(json.load(open(path, encoding="utf-8")))
    except Exception:
        return jsonify({"error": "저장된 결과가 없습니다.", "race_id": rid}), 404


@app.route("/api/race-results/missing", methods=["GET"])
def race_results_missing():
    """[3번] 당일 분석했으나 결과 미입력 경주 추적(누락 방지)."""
    return jsonify(_missing_results(request.args.get("date")))


@app.route("/api/ai-training/status", methods=["GET"])
def ai_training_status():
    """[3번] AI 학습 데이터 현황 대시보드(수집/완전/품질/목표 진행률/예상 완료)."""
    return jsonify(_ai_data_status())


@app.route("/api/ai-training/list", methods=["GET"])
def ai_training_list():
    """[1번] AI 학습 데이터 목록(최신순·품질점수 포함)."""
    out = []
    if os.path.isdir(AI_TRAINING_DIR):
        for fn in sorted(os.listdir(AI_TRAINING_DIR), reverse=True):
            if not fn.endswith(".json"):
                continue
            try:
                d = json.load(open(os.path.join(AI_TRAINING_DIR, fn), encoding="utf-8"))
            except Exception:
                continue
            q = d.get("quality") or {}
            out.append({"race_id": d.get("race_id"), "date": (d.get("race_info") or {}).get("date"),
                        "track": (d.get("race_info") or {}).get("track"),
                        "round": (d.get("race_info") or {}).get("round"),
                        "quality": q.get("score"), "complete": q.get("complete"),
                        "hit": (d.get("result") or {}).get("hit")})
    return jsonify({"data": out, "count": len(out)})


@app.route("/api/ai-training/get", methods=["POST", "GET"])
def ai_training_get():
    """[1번] AI 학습 데이터 1건 조회. {race_id | raceKey}."""
    if request.method == "POST":
        body = request.json or {}
        rid, rk = body.get("race_id"), body.get("raceKey")
    else:
        rid, rk = request.args.get("race_id"), request.args.get("raceKey")
    if not rid and rk:
        rid, _ = _race_result_id(rk)
    if not rid:
        return jsonify({"error": "race_id 또는 raceKey가 필요합니다."}), 400
    path = os.path.join(AI_TRAINING_DIR, os.path.basename(rid) + ".json")
    try:
        return jsonify(json.load(open(path, encoding="utf-8")))
    except Exception:
        return jsonify({"error": "AI 학습 데이터가 없습니다.", "race_id": rid}), 404


@app.route("/api/daily-summary", methods=["GET", "POST"])
def daily_summary():
    """[6번] 일별 자동 요약 조회/갱신. GET ?date=YYYY-MM-DD(없으면 오늘) · POST=강제 재생성.
    ?list=1 → 저장된 일별 요약 목록(최신순)."""
    if request.args.get("list"):
        out = []
        if os.path.isdir(DAILY_SUMMARY_DIR):
            for fn in sorted(os.listdir(DAILY_SUMMARY_DIR), reverse=True):
                if fn.endswith(".json"):
                    try:
                        out.append(json.load(open(os.path.join(DAILY_SUMMARY_DIR, fn), encoding="utf-8")))
                    except Exception:
                        continue
        return jsonify({"summaries": out, "count": len(out)})
    date = (request.args.get("date") or "").strip() or None
    if request.method == "POST":
        return jsonify(_build_daily_summary(date))   # 강제 재생성
    # GET: 저장본 있으면 반환, 없으면 즉석 생성
    d = date or time.strftime("%Y-%m-%d", time.localtime())
    path = os.path.join(DAILY_SUMMARY_DIR, d + ".json")
    try:
        return jsonify(json.load(open(path, encoding="utf-8")))
    except Exception:
        return jsonify(_build_daily_summary(d))


@app.route("/api/daily-learning", methods=["GET", "POST"])
def daily_learning():
    """[학습일지] 일별 학습 일지 조회/생성.
      GET ?date=YYYY-MM-DD(없으면 오늘) → 저장본 있으면 반환, 없으면 즉석 생성.
      GET ?list=1 → 저장된 학습일지 목록(최신순, 경량 요약).
      POST (body=extra JSON) → 강제 재생성 + 정성 항목 병합(수동 기록 저장). extra 없으면 수치만 갱신."""
    if request.args.get("list"):
        out = []
        if os.path.isdir(DAILY_LEARNING_DIR):
            for fn in sorted(os.listdir(DAILY_LEARNING_DIR), reverse=True):
                if not fn.endswith(".json"):
                    continue
                try:
                    j = json.load(open(os.path.join(DAILY_LEARNING_DIR, fn), encoding="utf-8"))
                except Exception:
                    continue
                out.append({"date": j.get("date"), "results_summary": j.get("results_summary"),
                            "key_learnings": len(j.get("key_learnings") or []),
                            "missed_opportunities": len(j.get("missed_opportunities") or []),
                            "pattern_discoveries": len(j.get("pattern_discoveries") or []),
                            "generated_at": j.get("generated_at")})
        return jsonify({"journals": out, "count": len(out)})
    date = (request.args.get("date") or "").strip() or None
    if request.method == "POST":
        extra = request.get_json(silent=True) or None
        return jsonify(_daily_learning_generate(date, extra))
    d = date or time.strftime("%Y-%m-%d", time.localtime())
    path = os.path.join(DAILY_LEARNING_DIR, d + ".json")
    try:
        return jsonify(json.load(open(path, encoding="utf-8")))
    except Exception:
        return jsonify(_daily_learning_generate(d))


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


def _parse_result_tsv(text):
    """[일괄등록·탭구분 텍스트] 헤더+탭(\\t) 구분 결과 붙여넣기 파싱(스프레드시트/엑셀 복사 대응).
    헤더 예: 구분 / 경주지역 / 라운드 / 1착 / 2착 / 3착 / 단승 / 복승 / 쌍승 / 연승 / 복연승 / 삼복승 / 삼쌍승.
    헤더 라벨로 컬럼 인덱스를 찾아 매핑(순서·컬럼수 달라도 대응). 탭 없으면 2칸+ 공백 분리 폴백.
    반환 [{area,round,no1,no2,no3,sOdds,qOdds,xOdds,tOdds}] (HTML 파서와 호환 + 단승/쌍승 추가)."""
    if not text:
        return []
    lines = [ln for ln in (text or "").replace("\r", "").split("\n") if ln.strip()]
    if not lines:
        return []

    def _split(ln):
        if "\t" in ln:
            return [c.strip() for c in ln.split("\t")]
        return [c for c in re.split(r"\s{2,}", ln.strip()) if c != ""]   # 탭 없으면 2칸+ 공백 분리

    def ns(s):
        return re.sub(r"\s+", "", s or "")
    # 헤더 라인 탐색(구분/경주지역 + 라운드/경주 포함)
    hi = None
    for i, ln in enumerate(lines):
        j = ns(ln)
        if ("경주지역" in j or "경마장" in j or "구분" in j) and ("라운드" in j or "경주" in j or "회차" in j):
            hi = i
            break
    if hi is None:
        return []
    heads = [ns(h) for h in _split(lines[hi])]

    def idx(pat, default=-1):
        for k, h in enumerate(heads):
            if re.search(pat, h):
                return k
        return default   # [사용자 스펙] 헤더 라벨 못 찾으면 고정 인덱스로 폴백
    # [하이브리드 매핑] 헤더 라벨 우선, 실패 시 사용자 지정 고정 인덱스(0구분/1경주지역/2라운드/3~5착/6단승/7복승/8쌍승/11삼복승)
    iArea = idx(r"^경주지역$|경주지역|^경마장$|^지역$", 1)
    iRound = idx(r"^라운드$|^회차$|^경주번호$|^경주$|^R$", 2)
    i1, i2, i3 = idx(r"^1착$|1착|1위", 3), idx(r"^2착$|2착|2위", 4), idx(r"^3착$|3착|3위", 5)
    iS = idx(r"^단승$", 6)
    iQ = idx(r"^복승$", 7)               # 복연승·삼복승 오매칭 방지(정확 매칭)
    iX = idx(r"^쌍승$", 8)               # 삼쌍승 오매칭 방지
    iT = idx(r"^삼복승$|^삼복$", 11)
    if iArea < 0 or (i1 < 0 and i2 < 0 and i3 < 0):
        return []

    def _fw(s):
        return (s or "").translate({0xFF10 + i: 0x30 + i for i in range(10)})

    def num(cells, k):
        if k < 0 or k >= len(cells):
            return None
        m = re.search(r"\d+", _fw(cells[k]))
        return int(m.group()) if m else None

    def fnum(cells, k):
        return _safe_num(_fw(cells[k])) if (0 <= k < len(cells)) else None
    out = []
    for ln in lines[hi + 1:]:
        cells = _split(ln)
        if len(cells) < 3:
            continue
        area = cells[iArea].strip() if 0 <= iArea < len(cells) else ""
        rnd = cells[iRound].strip() if 0 <= iRound < len(cells) else ""
        if not area and not rnd:
            continue
        if ns(area) in ("경주지역", "경마장", "구분"):   # 중복 헤더 라인 스킵
            continue
        out.append({
            "area": area, "round": rnd,
            "no1": num(cells, i1), "no2": num(cells, i2), "no3": num(cells, i3),
            "sOdds": fnum(cells, iS), "qOdds": fnum(cells, iQ),
            "xOdds": fnum(cells, iX), "tOdds": fnum(cells, iT),
        })
    return out


def _parse_result_rows(html):
    """결과 페이지 HTML → [{area,round,no1,no2,no3,qOdds,tOdds}]. 헤더 라벨로 컬럼 판별.
    [일괄등록 유연화] HTML 표가 아니면(탭구분 붙여넣기) _parse_result_tsv 로 자동 폴백."""
    # [탭구분 텍스트 우선 폴백] HTML 표 태그가 없고 탭/헤더가 있으면 TSV 파서로 처리(엑셀 복사 붙여넣기).
    if html and not re.search(r"<t[rd]\b|<table\b", html, re.I):
        tsv = _parse_result_tsv(html)
        if tsv:
            return tsv
    p = _TableRowCollector()
    try:
        p.feed(html or "")
    except Exception:
        return []
    rows = p.rows

    def _fw2ascii(s):
        # 전각숫자(０-９)·전각콜론 → 반각(중앙 JRA 결과표 대응)
        return (s or "").translate({0xFF10 + i: 0x30 + i for i in range(10)}).replace("：", ":")

    def ns(s):
        return re.sub(r"\s+", "", _fw2ascii(s))
    hi = None
    for i, r in enumerate(rows):
        joined = "".join(ns(c) for c in r)
        if re.search(r"경주지역|경마장|지역|開催|競馬場", joined) and re.search(r"라운드|회차|경주|着|着順|レース", joined):
            hi = i
            break
    if hi is None:  # 완화 매칭 폴백
        for i, r in enumerate(rows):
            joined = "".join(ns(c) for c in r)
            if re.search(r"경주지역|라운드|着順|レース", joined):
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
    iArea = idx(r"경주지역|경마장|지역|開催|競馬場|レース場")
    iRound = idx(r"라운드|회차|경주번호|^경주$|^R$|レース番号|^レース$")
    i1, i2, i3 = idx(r"1착|1위|1着"), idx(r"2착|2위|2着"), idx(r"3착|3위|3着")
    iQ, iT = idx(r"복승|複勝"), idx(r"삼복승|삼복|三連複|3連複")
    if i1 < 0 and i2 < 0 and i3 < 0:  # 착순 컬럼이 전혀 없으면 결과표 아님
        return []

    def cell(r, k):
        return _fw2ascii(r[k]) if (0 <= k < len(r)) else ""

    def firstnum(s):
        m = re.search(r"\d+", _fw2ascii(s))
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


_TRACK_ALIAS = {"부경": "부산", "부산경남": "부산", "서울경마": "서울", "제주도": "제주"}

# [일괄등록 유연화] 일본 경마장 표기 변형(한국어 표준 ↔ 한자 ↔ 로마자/약칭) 통일.
#   raceKey는 한국어 표기(예: '나고야 4경주')로 저장되므로 한자·영문 입력을 한국어로 정규화.
_TRACK_GROUPS = {
    "오비히로": ["帯広", "obihiro", "obi"],
    "모리오카": ["盛岡", "morioka", "mori"],
    "미즈사와": ["水沢", "mizusawa", "mizu"],
    "카와사키": ["川崎", "kawasaki", "kawa"],
    "후나바시": ["船橋", "funabashi", "funa"],
    "오이": ["大井", "ooi", "oi"],
    "우라와": ["浦和", "urawa", "ura"],
    "나고야": ["名古屋", "nagoya", "nago"],
    "카사마츠": ["笠松", "kasamatsu", "kasa"],
    "카나자와": ["金沢", "kanazawa", "kana"],
    "소노다": ["園田", "sonoda", "sono"],
    "히메지": ["姫路", "himeji", "hime"],
    "코치": ["高知", "kochi"],
    "사가": ["佐賀", "saga"],
    "몬베츠": ["門別", "monbetsu", "mombetsu", "monb"],
    "도쿄": ["東京", "tokyo"],
    "나카야마": ["中山", "nakayama", "naka"],
    "한신": ["阪神", "hanshin", "hans"],
    "쿄토": ["京都", "kyoto"],
    "추쿄": ["中京", "chukyo", "chuk"],
    "코쿠라": ["小倉", "kokura", "koku"],
    "니가타": ["新潟", "niigata", "niig"],
    "후쿠시마": ["福島", "fukushima", "fuku"],
    "삿포로": ["札幌", "sapporo", "sapp"],
    "하코다테": ["函館", "hakodate", "hako"],
}
# 역방향 조회맵: 별칭(소문자) → 한국어 표준 + 한국어 자기자신도 포함
_TRACK_REVERSE = {}
for _std, _als in _TRACK_GROUPS.items():
    _TRACK_REVERSE[_std.lower()] = _std
    for _a in _als:
        _TRACK_REVERSE[_a.lower()] = _std


def _track_norm(a):
    """경마장명 정규화(부경=부산 · 帯広/obihiro/OBI=오비히로 등 한/일/영 별칭 통일).
    영문 약칭은 접두 일치(OBI→오비히로)까지 허용."""
    a = (a or "").strip()
    if not a:
        return a
    if a in _TRACK_ALIAS:      # 한국 경마장 기존 별칭 우선
        return _TRACK_ALIAS[a]
    low = a.lower()
    if low in _TRACK_REVERSE:  # 한자·로마자·약칭 정확 일치
        return _TRACK_REVERSE[low]
    # 영문 약칭(접두) — 'obi'가 'obihiro'의 접두이거나 그 반대
    if re.fullmatch(r"[a-z]{2,}", low):
        for alias, std in _TRACK_REVERSE.items():
            if re.fullmatch(r"[a-z]{2,}", alias) and (alias.startswith(low) or low.startswith(alias)):
                return std
    return _TRACK_ALIAS.get(a, a)


def _area_num(s):
    """문자열에서 (경마장 토큰[정규화], 경주번호) 추출. 결과행·raceKey 공용.
    한글·일본어(가나·한자) 경마장명 모두 지원. '서울 5'(접미사 없음)도 번호 추출."""
    txt = re.sub(r"\d{4}-\d{2}-\d{2}", " ", s or "")   # 날짜 먼저 제거(번호 오검출 방지)
    m = re.search(r"[가-힣ぁ-んァ-ヶ一-龯]{2,}", txt.replace(" ", ""))
    token = m.group() if m else None
    if not token:   # [일괄등록 유연화] 한/일 경마장명 없으면 영문 토큰(obihiro·OBI 등)
        m2 = re.search(r"[A-Za-z]{2,}", txt)
        token = m2.group() if m2 else None
    area = _track_norm(token) if token else None
    # 경주번호: 'N경주/NR/N라운드/Nレース' 우선, 없으면 (날짜 제거 후) 첫 숫자
    n = re.search(r"(\d{1,2})\s*(?:R\b|경주|라운드|레이스|レース|R)", txt, re.IGNORECASE)
    if not n:
        n = re.search(r"(\d{1,2})", txt)
    num = int(n.group(1)) if n else None
    return area, num


def _analyzed_race_keys():
    """분석/수집된 모든 raceKey 후보 집합(활성 3종 배당 + odds_history 스냅샷 + analysis_log).
    한국·일본 중앙/지방 모든 경기 포함."""
    keys = set(_triple_load().keys())
    for d in (ODDS_HISTORY_DIR, ANALYSIS_LOG_DIR):
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if not fn.endswith(".json"):
                continue
            try:
                doc = json.load(open(os.path.join(d, fn), encoding="utf-8"))
                rk = doc.get("raceKey") or doc.get("race")
                if rk:
                    keys.add(rk)
            except Exception:
                continue
    return keys


def _resolve_race_key(rk, candidates=None):
    """입력 raceKey를 실제 분석된 key로 유연 매칭(형식 불일치 방어).
    예: '서울 5' → '2026-07-05 서울 5경주'. 오늘 날짜 후보 우선.
    우선순위: ①완전 일치 ②경마장+경주번호 일치 ③경주번호만 일치(후보 유일할 때만).
    매칭 실패 시 None."""
    rk = (rk or "").strip()
    if not rk:
        return None
    cands = list(candidates) if candidates is not None else list(_analyzed_race_keys())
    if rk in cands:
        return rk                                  # ① 완전 일치
    r_area, r_num = _area_num(rk)
    if r_num is None:
        return None
    today = time.strftime("%Y-%m-%d")

    def _today_first(ks):
        return sorted(ks, key=lambda k: (today not in k, k))
    # ② 경마장 + 경주번호 일치 (오늘 날짜 우선)
    tier2 = [k for k in cands
             if _area_num(k)[1] == r_num and r_area and _area_num(k)[0]
             and (r_area in _area_num(k)[0] or _area_num(k)[0] in r_area)]
    if tier2:
        return _today_first(tier2)[0]
    # ③ 경주번호만 일치 — 모호하지 않게(오늘 후보가 유일할 때만) 매칭
    tier3 = [k for k in cands if _area_num(k)[1] == r_num]
    pool = [k for k in tier3 if today in k] or tier3
    if len(pool) == 1:
        return pool[0]
    return None


def _rec_from_history(rk):
    """triple_store에 활성 데이터가 없을 때 odds_history 스냅샷에서 rec 재구성(결과학습용).
    (triple_store는 churn/삭제되어 결과 입력 시점엔 비어있을 수 있음 — 스냅샷은 영구 보존)."""
    path, _, _ = _hist_path(rk)
    try:
        doc = json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}
    snaps = doc.get("snapshots") or []
    if not snaps:
        return {}

    def _arr(d):   # {'1+2':45.2} → [{combo:[1,2],odds:45.2}]
        out = []
        for k, v in (d or {}).items():
            try:
                out.append({"combo": [int(x) for x in str(k).split("+")], "odds": float(v)})
            except (ValueError, TypeError):
                continue
        return out
    hist = []
    for s in snaps:
        hist.append({"t": s.get("t"), "quinella": _arr(s.get("quinella")),
                     "exacta": _arr(s.get("exacta")), "trio": [],
                     "win": {k: v for k, v in (s.get("win") or {}).items()}})
    last = snaps[-1]
    return {"quinella": _arr(last.get("quinella")), "exacta": _arr(last.get("exacta")),
            "trio": [], "win": last.get("win") or {}, "history": hist}


def _match_row_to_key(row, analyzed_keys):
    """결과행(area,round) → 분석된 raceKey 유연 매칭(한국·일본 모든 경기). 실패 시 None."""
    pseudo = f"{row.get('area') or ''} {row.get('round') or ''}".strip()
    if _area_num(pseudo)[1] is None:
        return None
    matched = _resolve_race_key(pseudo, analyzed_keys)
    return matched if matched in analyzed_keys else None


def _match_row_to_all_keys(row, analyzed_keys):
    """결과행 → (경마장+경주번호) 일치하는 **모든** 분석 key.
    같은 경주가 한글('모리오카 1경주')·한자('2026-07-07 盛岡 1R') 등 중복 저장돼 있어도
    전부 반환 → 결과를 모든 중복 로그에 반영(하나만 등록되고 나머지가 미입력으로 남는 문제 방지)."""
    pseudo = f"{row.get('area') or ''} {row.get('round') or ''}".strip()
    r_area, r_num = _area_num(pseudo)
    if r_num is None:
        return []
    today = time.strftime("%Y-%m-%d")
    keys = [k for k in analyzed_keys
            if _area_num(k)[1] == r_num and r_area and _area_num(k)[0]
            and (r_area in _area_num(k)[0] or _area_num(k)[0] in r_area)]
    # 다른 날짜(명시된 과거 날짜) key 제외 — 오늘 결과를 과거 경주 로그에 쓰지 않음.
    #   (날짜 없는 key는 _analysis_log_path 가 오늘 파일로 매핑하므로 안전하게 유지)
    keys = [k for k in keys if not (re.search(r"\d{4}-\d{2}-\d{2}", k) and today not in k)]
    keys = list(dict.fromkeys(keys))
    keys.sort(key=lambda k: (today not in k, k))
    return keys


def _log_has_recommendation(rk):
    """해당 raceKey 로그가 추천/유력마 데이터를 가졌는지(적중판정 기준 primary 선택용)."""
    try:
        path, _, _ = _analysis_log_path(rk)
        doc = json.load(open(path, encoding="utf-8"))
        return bool(doc.get("final_recommendation") or doc.get("betRecommend") or doc.get("keyHorses"))
    except Exception:
        return False


def _mark_result_in_log(rk, result):
    """중복 로그(추천 없는 쪽)에 결과만 기록해 미입력 목록에서 제거(전체 학습은 primary에서만)."""
    try:
        path, _, _ = _analysis_log_path(rk)
        doc = json.load(open(path, encoding="utf-8"))
    except Exception:
        return False
    if doc.get("result"):
        return False
    doc["result"] = result
    doc["result_via"] = "duplicate_sync"   # primary 로그에서 동기화됨 표시
    try:
        json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return True
    except Exception:
        return False


def _all_analyzed_keys():
    """매칭 후보 raceKey 전체 — triple_store(현재 캐시) + analysis_log(과거 분석·프룬됨 포함)."""
    keys = set(_triple_load().keys())
    try:
        if os.path.isdir(ANALYSIS_LOG_DIR):
            for fn in os.listdir(ANALYSIS_LOG_DIR):
                if not fn.endswith(".json"):
                    continue
                try:
                    d = json.load(open(os.path.join(ANALYSIS_LOG_DIR, fn), encoding="utf-8"))
                    k = d.get("raceKey") or d.get("race")
                    if k:
                        keys.add(k)
                except Exception:
                    continue
    except Exception:
        pass
    return list(keys)


def _register_result_rows(rows, stake=1000, analyzed_keys=None):
    """결과행 리스트 → 분석경주 자동매칭 → 적중판정·학습 → 요약.
    일괄등록(HTML)·캡쳐OCR(이미지) 공통 등록 경로. row={area,round,no1,no2,no3,qOdds?,tOdds?}."""
    stake = int(stake) if (stake and int(stake) > 0) else 1000
    if analyzed_keys is None:
        analyzed_keys = _all_analyzed_keys()
    matched, unmatched, errors = [], [], []
    hits = 0
    profit = 0
    for row in rows:
        top3 = [row.get("no1"), row.get("no2"), row.get("no3")]
        top3 = [int(x) for x in top3 if isinstance(x, int) and x >= 1]
        if not top3:
            continue
        keys = _match_row_to_all_keys(row, analyzed_keys)
        if not keys:
            unmatched.append({"area": row.get("area"), "round": row.get("round"), "top3": top3})
            continue
        # 중복 로그 중 추천이 있는 로그를 primary(전체 적중판정·학습 기준)로 선택
        primary = next((k for k in keys if _log_has_recommendation(k)), keys[0])
        final_odds = {}
        if row.get("qOdds"):
            final_odds["quinella"] = {"combo": top3[:2], "odds": row["qOdds"]}
        if row.get("tOdds"):
            final_odds["trio"] = {"combo": top3[:3], "odds": row["tOdds"]}
        result = {}
        for i, no in enumerate(top3[:3]):
            result[["1st", "2nd", "3rd"][i]] = no
        try:
            rec, _ = _apply_result_learning(primary, result, top3, final_odds or None, stake=stake)
        except Exception as e:
            errors.append({"raceKey": primary, "error": str(e)})
            continue
        # 나머지 중복 로그에도 결과 기록 → 미입력 목록에서 함께 제거(학습 중복 방지 위해 결과만)
        dup_synced = [k for k in keys if k != primary and _mark_result_in_log(k, result)]
        q_hit, t_hit = bool(rec.get("quinella_hit")), bool(rec.get("trifecta_hit"))
        won = q_hit or t_hit or bool(rec.get("was_hit"))
        pnl = int(rec.get("pnl") or 0)   # [#5] 손익은 학습 레코드에서 단일 계산
        if won:
            hits += 1
        profit += pnl
        matched.append({"raceKey": primary, "top3": top3, "quinella_hit": q_hit,
                        "trifecta_hit": t_hit, "won": won, "pnl": pnl,
                        "stake": stake, "payouts": rec.get("payouts"),
                        "payout_actual": rec.get("payout_actual"),
                        "had_bet": bool(rec.get("bet_type")),
                        "dupSynced": dup_synced})
    print(f"[결과등록] 등록 {len(matched)}건 · 적중 {hits} · 손익 {profit}원 · 매칭실패 {len(unmatched)}건")
    return {"ok": True, "registered": len(matched), "hits": hits, "profit": profit,
            "stake": stake, "matched": matched, "unmatched": unmatched, "errors": errors,
            "parsedRows": len(rows)}


@app.route("/api/results/bulk", methods=["POST"])
def results_bulk():
    """[일괄 결과 등록] 결과 페이지 전체를 한 번에 파싱→분석경주 자동매칭→적중판정·학습→요약.
    body: {html?} 또는 {url?} 또는 {rows?}, stake?(정액 베팅 가정, 기본 1000)."""
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
    return jsonify(_register_result_rows(rows, stake))


def _recompute_pnl(rec, stake, payout):
    """[보완#1] 조정용 손익 재계산 — 단건 record-result 와 동일 규칙.
    payout(실수령) 지정 시 실수령−투자금, 아니면 확정배당(payouts)×stake 추정."""
    stake = int(stake) if (stake and int(stake) > 0) else 1000
    actual = None
    try:
        if payout is not None and str(payout) != "":
            actual = int(round(float(payout)))
    except (TypeError, ValueError):
        actual = None
    payouts = rec.get("payouts") or {}
    if actual is not None:
        pnl = actual - stake
    elif rec.get("quinella_hit") and payouts.get("quinella"):
        pnl = round((payouts["quinella"] - 1) * stake)
    elif rec.get("trifecta_hit") and payouts.get("trifecta"):
        pnl = round((payouts["trifecta"] - 1) * stake)
    elif rec.get("bet_type"):
        pnl = -stake
    else:
        pnl = 0
    return stake, actual, pnl


@app.route("/api/results/adjust", methods=["POST"])
def results_adjust():
    """[보완#1] 일괄 등록 후 경주별 투자금/실수령 배당금 조정 → 저장된 학습 레코드 in-place 갱신.
    body: {items:[{raceKey, stake, payout}]} 또는 단건 {raceKey, stake, payout}.
    같은 raceKey 는 가장 최근 레코드 1건만 갱신(일괄 재등록 중복 방지)."""
    body = request.json or {}
    items = body.get("items")
    if items is None:
        items = [{"raceKey": body.get("raceKey"), "stake": body.get("stake"), "payout": body.get("payout")}]
    L = _learning_load()
    records = L.get("records", [])
    updated, net = [], 0
    for it in items:
        rk = (it.get("raceKey") or "").strip()
        if not rk:
            continue
        # 해당 raceKey 의 가장 최근 레코드(뒤에서부터)
        target = None
        for r in reversed(records):
            if r.get("race") == rk:
                target = r
                break
        if target is None:
            continue
        stake, actual, pnl = _recompute_pnl(target, it.get("stake"), it.get("payout"))
        target["stake"] = stake
        target["payout_actual"] = actual
        target["pnl"] = pnl
        net += pnl
        updated.append({"raceKey": rk, "stake": stake, "payout_actual": actual, "pnl": pnl})
    if updated:
        L["stats"] = _recompute_learning_stats(records)
        _learning_save(L)
    return jsonify({"ok": True, "updated": updated, "net": net,
                    "profit_summary": (L.get("stats") or {}).get("profit_summary")})


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
        "smartMoneyStats": d.get("smart_money_stats"),       # [보완점3] 스마트머니 자동 학습 집계
    })


def _parse_reversal_signal(s):
    """[4번] analysis_log signals_detected 의 역배열 신호 → (challenger, ratio) 추출(두 포맷 지원)."""
    typ, det, rea = str(s.get("type", "")), str(s.get("detail", "")), str(s.get("reason", ""))
    try:
        if typ == "쌍승역전공식":
            mch = re.search(r"실질 1착: (\d+)번", det)
            mr = re.search(r"([01]\.\d{2,3})", det)
            if mch and mr:
                return int(mch.group(1)), float(mr.group(1))
        elif typ == "역전":
            mch = re.search(r"시장이 (\d+)번", rea)
            mo = re.findall(r"\(([\d.]+)\)", det)
            if mch and len(mo) >= 2 and float(mo[1]) > 0:
                return int(mch.group(1)), round(float(mo[0]) / float(mo[1]), 3)
    except (ValueError, TypeError):
        pass
    return None, None


def _reversal_strength_stats():
    """[4번] 역배열 강도별 실제 적중률 집계 — analysis_log 결과 있는 경주에서 경주당 최강 역전 1건을
    강도(배당차%)로 버킷(축35%+/보조20~35%/보험10~20%)해 입상률·1착률 계산. pattern_learning.json 갱신용."""
    def _band(diff):
        if diff is None or diff < 10:
            return None
        if diff >= 35:
            return "축(35%+)"
        if diff >= 20:
            return "보조(20~35%)"
        return "보험(10~20%)"
    stats, cases = {}, []
    if not os.path.isdir(ANALYSIS_LOG_DIR):
        return {"by_band": {}, "sample_cases": [], "total_cases": 0}
    for fn in os.listdir(ANALYSIS_LOG_DIR):
        if not fn.endswith(".json"):
            continue
        try:
            d = json.load(open(os.path.join(ANALYSIS_LOG_DIR, fn), encoding="utf-8"))
        except Exception:
            continue
        res = d.get("result")
        if not isinstance(res, dict):
            continue
        top = [int(x) for x in [res.get("1st"), res.get("2nd"), res.get("3rd")] if x is not None]
        if not top:
            continue
        best = None
        for s in (d.get("signals_detected") or []):
            ch, ratio = _parse_reversal_signal(s)
            if ch is None:
                continue
            if best is None or ratio < best[1]:
                best = (ch, ratio)
        if not best:
            continue
        ch, ratio = best
        diff = round((1 - ratio) * 100, 1)
        band = _band(diff)
        if not band:
            continue
        placed = 1 if ch in top else 0
        won = 1 if ch == top[0] else 0
        x = stats.setdefault(band, {"n": 0, "placed": 0, "won": 0})
        x["n"] += 1
        x["placed"] += placed
        x["won"] += won
        cases.append({"race": d.get("raceKey"), "challenger": ch, "diffPct": diff,
                      "band": band, "placed": bool(placed), "won": bool(won)})
    for band, x in stats.items():
        x["place_rate"] = round(x["placed"] / x["n"] * 100, 1) if x["n"] else 0.0
        x["win_rate"] = round(x["won"] / x["n"] * 100, 1) if x["n"] else 0.0
    return {"by_band": stats, "sample_cases": cases[-60:], "total_cases": len(cases),
            "note": "역배열 challenger 강도별 실제 입상/1착률(경주당 최강 역전 1건). 표본 적으면 참고만(50+ 유의).",
            "updated": time.strftime("%Y-%m-%d")}


@app.route("/api/stats/today", methods=["GET"])
def stats_today():
    """[오늘 결과 통계 대시보드] 오늘(또는 ?date=YYYY-MM-DD) 등록 경주 집계 →
    총·복승/삼복승 적중·패스·손익·경마장별·신호별·타임라인. 기존 정확 적중 우선·없으면 유력마 기반 병행."""
    date = (request.args.get("date") or time.strftime("%Y-%m-%d")).strip()
    L = _learning_load()
    recs = [r for r in (L.get("records") or []) if r.get("date") == date]

    def _qhit(r):
        return bool(r.get("quinella_hit") or r.get("keyhorse_quinella_hit"))

    def _thit(r):
        return bool(r.get("trifecta_hit") or r.get("keyhorse_trifecta_hit"))
    total = len(recs)
    hit_q = sum(1 for r in recs if _qhit(r))
    hit_t = sum(1 for r in recs if _thit(r))
    passed = sum(1 for r in recs if r.get("passed"))
    profit = sum(int(r.get("pnl") or 0) for r in recs)
    by_track = {}
    for r in recs:
        v = r.get("venue") or (_area_num(r.get("race") or "")[0]) or "기타"
        e = by_track.setdefault(v, {"total": 0, "hit": 0})
        e["total"] += 1
        if _qhit(r):
            e["hit"] += 1
    for v, e in by_track.items():
        e["rate"] = round(e["hit"] / e["total"] * 100, 1) if e["total"] else 0.0
    # [신호별] signal_stats.json 케이스에서 오늘 날짜만 집계(신호별 학습 재사용)
    by_signal = {}
    S = _signal_stats_load()
    for name, e in S.items():
        cs = [c for c in (e.get("cases") or []) if c.get("date") == date]
        if cs:
            hq = sum(1 for c in cs if c.get("quinella_hit"))
            by_signal[name] = {"total": len(cs), "hit": hq,
                               "rate": round(hq / len(cs) * 100, 1) if cs else 0.0}
    # [타임라인] 시간순(등록 t 기준)
    timeline = []
    for r in recs:
        t = r.get("t")
        hhmm = time.strftime("%H:%M", time.localtime(t)) if t else "--:--"
        timeline.append({"time": hhmm, "race": r.get("race"), "hit": _qhit(r),
                         "quinella_odds": (r.get("payouts") or {}).get("quinella"),
                         "trifecta_odds": (r.get("payouts") or {}).get("trifecta"),
                         "pnl": r.get("pnl"), "t": t or 0})
    timeline.sort(key=lambda x: x["t"])
    return jsonify({
        "date": date, "total": total, "hit_quinella": hit_q, "hit_trifecta": hit_t, "pass": passed,
        "rate_quinella": round(hit_q / total * 100, 1) if total else 0.0,
        "rate_trifecta": round(hit_t / total * 100, 1) if total else 0.0,
        "profit": profit, "by_track": by_track, "by_signal": by_signal, "timeline": timeline,
    })


@app.route("/api/learning/mid-high-odds", methods=["GET"])
def learning_mid_high_odds():
    """[5번] 중고배당 유력마(💎) 누적 적중률 조회 — 통계 탭 카드용."""
    S = _mid_high_stats_load()
    rows = []
    for sg, e in (S.get("by_signal") or {}).items():
        rows.append({"signal": sg, "count": e.get("count", 0), "placed": e.get("placed", 0), "rate": e.get("rate", 0)})
    rows.sort(key=lambda r: -r["count"])
    return jsonify({"count": S.get("count", 0), "placed": S.get("placed", 0), "rate": S.get("rate", 0),
                    "bySignal": rows, "recent": (S.get("cases") or [])[-8:][::-1]})


@app.route("/api/learning/signal-stats", methods=["GET"])
def learning_signal_stats():
    """[4번] 신호별 적중률(유력마 기반) 조회 → 통계 탭 '신호별 적중률' 카드용.
    각 신호: 복승/삼복승 적중률·표본·신뢰(50경주+) 여부 + 최근 케이스."""
    S = _signal_stats_load()
    order = ["스마트머니", "역배열", "크로스역배열", "급락", "전적이중수렴", "저배당압축", "집중급락"]
    rows = []
    for name in order + [k for k in S.keys() if k not in order]:
        e = S.get(name)
        if not e:
            continue
        rows.append({
            "signal": name, "count": e.get("count", 0),
            "hit_quinella": e.get("hit_quinella", 0), "hit_trifecta": e.get("hit_trifecta", 0),
            "rate_quinella": e.get("rate_quinella", 0), "rate_trifecta": e.get("rate_trifecta", 0),
            "reliable": bool(e.get("reliable")), "recent": (e.get("cases") or [])[-5:][::-1],
        })
    return jsonify({"signals": rows, "minSample": SIGNAL_STATS_MIN})


@app.route("/api/learning/reversal-stats", methods=["GET", "POST"])
def learning_reversal_stats():
    """[4번] 역배열 강도별 적중률 조회 + (POST) pattern_learning.json 재집계 갱신."""
    if request.method == "POST":
        rs = _reversal_strength_stats()
        d = _upset_load()
        d["reversal_stats"] = rs
        _upset_save(d)
        return jsonify({"ok": True, "reversal_stats": rs})
    d = _upset_load()
    return jsonify(d.get("reversal_stats") or _reversal_strength_stats())


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


# [스펙2·3] 결과 자동수집(발주 후 5/7/10분) 성공/실패 이벤트 브리지.
#   확장 background 가 결과 수집 성공(done)/최종 실패(manual)을 POST 하고,
#   분석기(웹)가 GET 으로 폴링해 ①실패 → 상단 배너("자동수집 실패 → 수동입력")
#   ②성공(seq 증가) → 결과기록 탭 자동 갱신(새로고침 불필요)에 사용한다.
_RESULT_AUTO_EVENTS = {}    # raceKey -> {state, top3, hit, finalOdds, serverAt, seq, ...}
_RESULT_AUTO_SEQ = 0        # 이벤트 순번(분석기가 '새 성공' 감지에 사용)
_RESULT_AUTO_LAST_DONE = None   # {raceKey, seq, t, top3, hit}


@app.route("/api/results/auto-status", methods=["GET", "POST"])
def results_auto_status():
    global _RESULT_AUTO_SEQ, _RESULT_AUTO_LAST_DONE
    if request.method == "POST":
        s = request.json or {}
        rk = (s.get("raceKey") or "").strip()
        state = s.get("state") or ""
        _RESULT_AUTO_SEQ += 1
        s["serverAt"] = time.time()
        s["seq"] = _RESULT_AUTO_SEQ
        # raceKey 없으면 순번키로 저장(실패이벤트도 유실 없이 남김)
        _RESULT_AUTO_EVENTS[rk or f"__seq{_RESULT_AUTO_SEQ}"] = s
        if state == "done":
            _RESULT_AUTO_LAST_DONE = {"raceKey": rk, "seq": _RESULT_AUTO_SEQ,
                                      "t": s["serverAt"], "top3": s.get("top3"), "hit": s.get("hit")}
        # 오래된 이벤트 정리(최근 40개만 유지)
        if len(_RESULT_AUTO_EVENTS) > 40:
            for k in sorted(_RESULT_AUTO_EVENTS, key=lambda k: _RESULT_AUTO_EVENTS[k].get("serverAt", 0))[:-40]:
                _RESULT_AUTO_EVENTS.pop(k, None)
        return jsonify({"ok": True, "seq": _RESULT_AUTO_SEQ})
    # GET: 최근 1시간 이내 '수동입력 필요(manual)' 목록 + 마지막 성공 + 현재 seq.
    #   이미 결과가 저장된(수동 입력 완료) 경주는 실패 목록에서 자동 제외.
    now = time.time()
    saved = set()
    try:
        saved = set(_results_load().keys())
    except Exception:
        saved = set()
    failures = []
    for k, v in _RESULT_AUTO_EVENTS.items():
        if v.get("state") != "manual":
            continue
        if (now - v.get("serverAt", 0)) >= 3600:
            continue
        rk = v.get("raceKey") or ""
        if rk and rk in saved:      # 이미 입력됨 → 배너에서 제외
            continue
        failures.append({"raceKey": rk, "t": v.get("serverAt"), "attempt": v.get("attempt")})
    failures.sort(key=lambda x: x.get("t") or 0, reverse=True)
    return jsonify({"seq": _RESULT_AUTO_SEQ, "failures": failures, "lastDone": _RESULT_AUTO_LAST_DONE})


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
            res = d.get("result") or {}
            hit = d.get("hit") or {}
            # [분석기록] 적중 여부(복승/삼복승 중 하나라도 적중) — 목록에서 배지 표시용
            won = bool(hit.get("quinella_hit") or hit.get("trifecta_hit") or hit.get("was_hit"))
            out.append({"file": fn, "race_id": d.get("race_id"), "date": d.get("date"),
                        "race": d.get("race"), "raceKey": d.get("raceKey") or d.get("race"),
                        "sport": d.get("sport") or "horse",
                        "category": d.get("category") or "japan_local",
                        "summary": d.get("summary") or "",
                        "keyHorses": d.get("keyHorses") or [],
                        "analyzed_at": d.get("analyzed_at"),
                        "snaps": len(d.get("odds_timeline") or []),
                        "signals": len(d.get("signals_detected") or []),
                        "top3": [res.get("1st"), res.get("2nd"), res.get("3rd")] if res else [],
                        "pnl": hit.get("pnl"),   # [보완3] 손익(정렬용) — 결과 미입력이면 None
                        "reviewed": bool(d.get("reviewed")),   # [복기 표식] 복기 완료 여부
                        "reviewed_at": d.get("reviewed_at"),
                        "hasResult": bool(d.get("result")), "won": won})
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


REVIEW_NOTES_FILE = os.path.join(os.path.dirname(__file__), "data", "review_notes.json")


def _review_notes_load():
    """복기 메모 학습 코퍼스(리스트) 로드."""
    try:
        return json.load(open(REVIEW_NOTES_FILE, encoding="utf-8"))
    except Exception:
        return []


def _review_note_append(doc, note, file):
    """복기 메모를 학습 코퍼스에 축적(경주/종목/적중 태그 포함) → 나중에 패턴 마이닝·검색.
    같은 경주(raceKey)는 최신 메모로 갱신(중복 방지)."""
    if not (note or "").strip():
        return
    rk = doc.get("raceKey") or doc.get("race") or file
    hit = doc.get("hit") or {}
    won = bool(hit.get("quinella_hit") or hit.get("trifecta_hit") or hit.get("was_hit"))
    entry = {
        "raceKey": rk, "race": doc.get("race"), "file": file,
        "category": doc.get("category") or "japan_local",
        "sport": doc.get("sport") or "horse",
        "date": doc.get("date"), "won": won,
        "pnl": hit.get("pnl"), "note": note.strip(),
        "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
    }
    notes = _review_notes_load()
    notes = [n for n in notes if n.get("raceKey") != rk]   # 같은 경주 이전 메모 제거
    notes.append(entry)
    try:
        json.dump(notes, open(REVIEW_NOTES_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass


@app.route("/api/analysis-log/memo", methods=["POST"])
def analysis_log_memo():
    """복기 메모 저장 + 복기 표식: {file|raceKey, review} → review 저장 + reviewed=True 표식
    + 학습 코퍼스(review_notes.json) 축적."""
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
    review = body.get("review", "")
    doc["review"] = review
    doc["profit"] = body.get("profit", doc.get("profit"))
    # [복기 표식] 복기 완료 마킹 + 시각 기록 → 목록/상세에서 "🧠 복기완료" 배지
    doc["reviewed"] = True
    doc["reviewed_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # [복기 학습] 메모를 종목·적중 태그와 함께 코퍼스에 축적(검색·패턴화용)
    _review_note_append(doc, review, os.path.basename(path))
    return jsonify({"ok": True, "reviewed": True, "reviewed_at": doc["reviewed_at"]})


@app.route("/api/review-notes/list", methods=["GET"])
def review_notes_list():
    """복기 메모 학습 코퍼스 목록(최신순) — 검색·복기 기억용."""
    notes = _review_notes_load()
    notes = sorted(notes, key=lambda n: n.get("ts") or "", reverse=True)
    return jsonify({"notes": notes, "count": len(notes)})


# ══════════════ [경륜 출마표] oddspark 전적 자동 수집·분석 ══════════════
#   oddspark.com/keirin/RaceList.do?joCode=XX&kaisaiBi=YYYYMMDD&raceNo=N 를 서버가 직접
#   fetch(공개 페이지·확장 불필요)해서 선수 전적(競走得点·착순·결정타·각질·전장소)을 분석.
KEIRIN_STYLE_LABEL = {"逃": "도주(선행)", "捲": "젖히기", "追": "추입(마크)",
                      "差": "차입", "両": "자재(양각)", "自": "자재"}
# joCode → 경륜장명(입력 힌트·폴백용). ⚠ 실제 경기장명은 출마표 <title>에서 파싱(권위 소스)하므로
#   여기 없는 joCode도 '기타 경륜장 자동 탐색'으로 정상 표시됨(_keirin_parse_card 가 title 우선).
#   [확정·live fetch 검증] 36=오다와라(小田原) · 62=히로시마(広島). 그 외 라벨은 미검증(참고만).
KEIRIN_JO = {"36": "오다와라", "62": "히로시마", "01": "마에바시", "04": "기후",
             "85": "사세보", "83": "구루메", "81": "고쿠라", "31": "마쓰도",
             "45": "히라쓰카", "48": "가와사키", "73": "기시와다"}
# 경륜장명 → joCode 역매핑(raceKey에서 joCode 자동 감지용)
_KEIRIN_JO_REV = {}
for _kc, _kv in KEIRIN_JO.items():
    _KEIRIN_JO_REV.setdefault(_kv, _kc)


def _keirin_jo_from_venue(venue):
    """raceKey 경륜장명 → joCode(오다와라36·히로시마62·마에바시01·기후04 등). 없으면 None."""
    if not venue:
        return None
    v = str(venue).strip()
    return _KEIRIN_JO_REV.get(v) or _KEIRIN_JO_REV.get(_area_num(v)[0] or "")


def _kstrip(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = s.replace("&nbsp;", " ").replace("　", " ")
    return re.sub(r"\s+", " ", s).strip()


def _keirin_style_label(ch):
    for k, v in KEIRIN_STYLE_LABEL.items():
        if k in (ch or ""):
            return v
    return ch or ""


def _keirin_grade(score):
    """競走得点 → 전적 등급(스펙: 95+ A / 85~94 B / 75~84 C / <75 D). 예: 99.9점=A."""
    if score is None:
        return "?"
    if score >= 95:
        return "A"
    if score >= 85:
        return "B"
    if score >= 75:
        return "C"
    return "D"


def _keirin_style_bonus(r):
    """결정수 보정: 차입(差)·마크(マ) 우세→추입형 +5 / 도주(逃) 우세→선행형 +3 / 젖히기(捲)→+4."""
    km = r.get("kimarite")
    style = r.get("style") or ""
    dominant = None
    if km and sum(km) > 0:
        dominant = ["逃", "捲", "差", "マ"][km.index(max(km))]
    if dominant in ("差", "マ") or (not dominant and ("追" in style or "差" in style)):
        return "추입형", 5
    if dominant == "逃" or (not dominant and "逃" in style):
        return "선행형", 3
    if dominant == "捲":
        return "젖히기형", 4
    if "追" in style or "差" in style:
        return "추입형", 5
    if "逃" in style:
        return "선행형", 3
    return "", 0


def _keirin_parse_card(html):
    """oddspark 경륜 출마표 HTML → {venue,race_no,dist,post,tendency,riders,line,comment}."""
    out = {"venue": "", "race_no": None, "race_name": "", "dist": "", "post": "",
           "tendency": {}, "riders": [], "line": [], "comment": ""}
    mt = re.search(r"<title>(.*?)</title>", html, re.S)
    if mt:
        title = _kstrip(mt.group(1))
        out["race_name"] = title
        mv = re.search(r"(\S+競輪)", title)
        if mv:
            out["venue"] = mv.group(1).replace("競輪", "").strip()
        mr = re.search(r"(\d+)R", title)
        if mr:
            out["race_no"] = int(mr.group(1))
    mpos = html.find("発走時間")
    if mpos > 0:
        seg = _kstrip(html[mpos - 200:mpos + 120])
        md = re.search(r"(\d+)m", seg)
        if md:
            out["dist"] = md.group(1) + "m"
        mp = re.search(r"発走時間\s*([\d:]+)", seg)
        if mp:
            out["post"] = mp.group(1)
    # 경륜장 결정타 경향(직근1년): 逃げ/捲り/差し %
    mk = re.search(r'kimariteTable.*?</table>', html, re.S)
    if mk:
        pcts = re.findall(r'([\d.]+)%', mk.group(0))
        if len(pcts) >= 3:
            out["tendency"] = {"도주": float(pcts[0]), "젖히기": float(pcts[1]), "차입": float(pcts[2])}
    # 선수 테이블
    mtab = re.search(r'<table class="tb60 h100pr".*?</table>', html, re.S)
    if mtab:
        for tr in re.findall(r'<tr[^>]*>.*?</tr>', mtab.group(0), re.S):
            if "競走得点" not in tr:
                continue
            cells = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)
            nidx = next((i for i, c in enumerate(cells) if "PlayerDetail" in c), None)
            if nidx is None:
                continue
            mcar = re.search(r'class="no(\d+)"', tr)
            car = int(mcar.group(1)) if mcar else None
            mn = re.search(r'playerCd=\d+"\s*>([^<]+)</a>', cells[nidx])
            name = _kstrip(mn.group(1)) if mn else ""
            mage = re.search(r'(\d+)歳／(\d+)期', _kstrip(cells[nidx]))
            age = int(mage.group(1)) if mage else None
            ki = int(mage.group(2)) if mage else None
            fuken = _kstrip(cells[nidx + 1]) if nidx + 1 < len(cells) else ""
            kyu = _kstrip(cells[nidx + 2]) if nidx + 2 < len(cells) else ""
            gcell = _kstrip(cells[nidx + 3]) if nidx + 3 < len(cells) else ""
            mg = re.search(r'([\d.]+)', gcell)
            gear = float(mg.group(1)) if mg else None
            ms = re.search(r'([逃捲追差両自]+)\s*$', gcell)
            style = ms.group(1) if ms else ""
            scell = _kstrip(cells[nidx + 4]) if nidx + 4 < len(cells) else ""
            msc = re.search(r'競走得点：\s*([\d.]+)', scell)
            score = float(msc.group(1)) if msc else None
            mo = re.search(r'着\s*順\s*：\s*(\d+)-\s*(\d+)-\s*(\d+)-\s*(\d+)', scell)
            chaku = [int(x) for x in mo.groups()] if mo else None
            mkm = re.search(r'決まり手：\s*(\d+)-\s*(\d+)-\s*(\d+)-\s*(\d+)', scell)
            kimarite = [int(x) for x in mkm.groups()] if mkm else None
            recent = _kstrip(cells[nidx + 5]) if nidx + 5 < len(cells) else ""
            prev1 = _kstrip(cells[nidx + 6]) if nidx + 6 < len(cells) else ""
            prev2 = _kstrip(cells[nidx + 7]) if nidx + 7 < len(cells) else ""
            rentai = None
            if chaku and sum(chaku) > 0:
                rentai = round((chaku[0] + chaku[1]) / sum(chaku) * 100, 1)
            out["riders"].append({
                "car": car, "name": name, "age": age, "ki": ki, "area": fuken,
                "classGrade": kyu, "gear": gear, "style": style,
                "styleLabel": _keirin_style_label(style), "score": score,
                "chaku": chaku, "kimarite": kimarite, "rentai": rentai,
                "recent": recent, "prev1": prev1, "prev2": prev2,
            })
    ml = re.search(r'<ul class="keirinRyosouline">.*?</ul>', html, re.S)
    if ml:
        out["line"] = [int(x) for x in re.findall(r'class="no([1-9])"', ml.group(0))]
    mc = re.search(r'keirinRyosousouhyo"\s*>\s*([^<]+)', html)
    if mc:
        out["comment"] = _kstrip(mc.group(1))
    return out


def _keirin_analyze(card):
    """파싱된 출마표 → 등급·결정수 보정·경향 반영 랭킹."""
    riders = [r for r in card.get("riders") or [] if r.get("score") is not None]
    for r in riders:
        r["grade"] = _keirin_grade(r["score"])
        typ, bonus = _keirin_style_bonus(r)
        r["styleType"] = typ
        r["styleBonus"] = bonus
        r["adjScore"] = round(r["score"] + bonus, 3)
        km = r.get("kimarite")
        if km and sum(km) > 0:
            tot = sum(km)
            r["kimariteRatio"] = {"도주": round(km[0] / tot * 100, 1),
                                  "젖히기": round(km[1] / tot * 100, 1),
                                  "차입": round(km[2] / tot * 100, 1),
                                  "마크": round(km[3] / tot * 100, 1)}
    ranked = sorted(riders, key=lambda r: r["adjScore"], reverse=True)
    for i, r in enumerate(ranked):
        r["rank"] = i + 1
    tendency = card.get("tendency") or {}
    fav_style = max(tendency, key=tendency.get) if tendency else None
    top = ranked[:3]
    summary = " · ".join(
        f"{r['car']}번 {r['name']}({r['grade']}·득점{r['score']}{'+' + str(r['styleBonus']) + ' ' + r['styleType'] if r['styleBonus'] else ''})"
        for r in top)
    tips = []
    if fav_style:
        tips.append(f"이 경륜장은 최근 1년 '{fav_style}' 결정 비율이 가장 높음({tendency[fav_style]}%)")
    for r in ranked:
        if fav_style and r.get("styleType") and (
                (fav_style == "차입" and r["styleType"] == "추입형")
                or (fav_style == "도주" and r["styleType"] == "선행형")
                or (fav_style == "젖히기" and r["styleType"] == "젖히기형")):
            tips.append(f"{r['car']}번 {r['name']}: 경륜장 유리 각질({r['styleType']}) · 득점 {r['score']}")
    return {"ranked": ranked, "top": top, "favStyle": fav_style, "tendency": tendency,
            "summary": summary, "tips": tips, "line": card.get("line") or [],
            "comment": card.get("comment") or "",
            "venue": card.get("venue"), "raceNo": card.get("race_no"),
            "dist": card.get("dist"), "post": card.get("post")}


def _keirin_url(jo, ymd, race):
    return ("https://www.oddspark.com/keirin/RaceList.do"
            "?joCode=%s&kaisaiBi=%s&raceNo=%s" % (jo, ymd, race))


def _keirin_odds_url(jo, ymd, race, bet_type):
    """oddspark 경륜 배당 페이지 URL. betType 5=복승(2車複)·6=쌍승(2車単)."""
    return ("https://www.oddspark.com/keirin/Odds.do"
            "?joCode=%s&kaisaiBi=%s&raceNo=%s&betType=%s" % (jo, ymd, race, bet_type))


def _keirin_table_rows(html):
    """oddspark 배당 HTML → 표 행 리스트(각 행=셀 텍스트 리스트). 태그 제거·공백 정리."""
    rows = []
    for tr in re.findall(r'<tr[^>]*>.*?</tr>', html, re.S):
        cells = []
        for m in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.S):
            cells.append(_kstrip(re.sub(r'<[^>]+>', '', m)))
        if cells:
            rows.append(cells)
    return rows


def _keirin_odds_pairs(cells):
    """['4','4.5','2','12.7','4', ...] → 선두 정수(축 차번호)와 (배당,순위) 쌍 목록.
    첫 셀이 차번호가 아니면(예: '2着' 코너 라벨) 건너뛰고 다음 정수를 축으로 사용."""
    i = 0
    # 선두의 라벨('2着' 등) 제거 → 첫 정수 셀을 축 차번호로
    while i < len(cells) and not re.fullmatch(r'\d{1,2}', cells[i]):
        i += 1
    if i >= len(cells):
        return None, []
    axis = int(cells[i])
    rest = cells[i + 1:]
    pairs = []
    for j in range(0, len(rest), 2):
        od = rest[j].strip()
        val = None
        if re.fullmatch(r'\d+(?:\.\d+)?', od):
            val = float(od)
        pairs.append(val)   # 빈칸(대각선)·비배당은 None 으로 자리 유지
    return axis, pairs


def _keirin_parse_quinella(html):
    """복승(betType=5) 매트릭스 파싱 → [{combo:[a,b], odds}]. 상삼각: 행 축차 k, 쌍 index j → 조합{j+1,k}."""
    out, seen = [], set()
    for cells in _keirin_table_rows(html):
        # 배당(소수)이 하나도 없는 행(헤더 등) 제외
        if not any(re.fullmatch(r'\d+\.\d+', c) for c in cells):
            continue
        axis, pairs = _keirin_odds_pairs(cells)
        if axis is None:
            continue
        for j, od in enumerate(pairs):
            other = j + 1                       # 상대 차번호(1..axis-1)
            if od is None or od <= 0 or other >= axis:
                continue
            a, b = sorted((other, axis))
            key = (a, b)
            if key in seen:
                continue
            seen.add(key)
            out.append({"combo": [a, b], "odds": od})
    return out


def _keirin_parse_exacta(html):
    """쌍승(betType=6) 매트릭스 파싱 → [{combo:[1착,2착], odds}]. 방향성: 행 축차=1착, 쌍 index j → 2착=j+1."""
    out, seen = [], set()
    for cells in _keirin_table_rows(html):
        if not any(re.fullmatch(r'\d+\.\d+', c) for c in cells):
            continue
        axis, pairs = _keirin_odds_pairs(cells)   # axis = 1착 차번호
        if axis is None:
            continue
        for j, od in enumerate(pairs):
            second = j + 1                      # 2착 차번호
            if od is None or od <= 0 or second == axis:
                continue
            key = (axis, second)
            if key in seen:
                continue
            seen.add(key)
            out.append({"combo": [axis, second], "odds": od})
    return out


def _keirin_fetch(url):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    return urlopen(req, timeout=15).read().decode("utf-8", "ignore")


@app.route("/api/keirin/card", methods=["POST"])
@app.route("/api/keirin/starters", methods=["POST"])   # [별칭] 동일 기능(선수 전적 수집·분석)
def keirin_card():
    """경륜 출마표(선수 전적) 수집·분석: {joCode,kaisaiBi,raceNo} 또는 {url} 또는 {html}
    → oddspark를 서버가 직접 fetch·파싱·분석해 반환.
    선수번호·선수명·競走得点·최근착순·결정수(逃/捲/差/マ)·각질(逃/追)·직근2장소·A/B/C/D등급 포함."""
    body = request.json or {}
    html = body.get("html")
    url = body.get("url")
    if not html:
        if not url:
            jo, ymd, race = body.get("joCode"), body.get("kaisaiBi"), body.get("raceNo")
            if jo and ymd and race:
                url = _keirin_url(jo, ymd, race)
        # url 에서 파라미터만 있으면 정규화(사용자가 RaceList.do 전체 URL 붙여넣기 허용)
        if url and "oddspark.com" not in url:
            return jsonify({"error": "oddspark.com 경륜 출마표 URL이 아닙니다."}), 400
        if url:
            try:
                req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                html = urlopen(req, timeout=15).read().decode("utf-8", "ignore")
            except Exception as e:
                return jsonify({"error": "출마표 수집 실패: %s" % e}), 502
    if not html:
        return jsonify({"error": "joCode/kaisaiBi/raceNo 또는 url 또는 html 중 하나가 필요합니다."}), 400
    card = _keirin_parse_card(html)
    if not card.get("riders"):
        return jsonify({"error": "선수 정보를 찾지 못했습니다(경주 번호/개최일 확인)."}), 422
    # [기타 경륜장 자동 탐색] title에서 경기장명을 못 읽었으면 joCode 매핑으로 폴백 표기.
    if not card.get("venue"):
        _jo = str(body.get("joCode") or "")
        if not _jo and url:
            _mj = re.search(r"joCode=(\d+)", url)
            _jo = _mj.group(1) if _mj else ""
        if _jo and KEIRIN_JO.get(_jo):
            card["venue"] = KEIRIN_JO[_jo]
    an = _keirin_analyze(card)
    # [live 통합] raceKey 가 오면 출마표 전적(競走得点)을 STARTERS_STORE 에 저장 →
    #   같은 raceKey로 배당(복승·쌍승·삼복승)이 수집되면 _triple_analyze 가 전적+배당(역배열·급락)을
    #   통합해 유력마 등급·📋추천 근거·통합등급에 반영(경륜 '전적없음' 해소).
    rk = (body.get("raceKey") or "").strip()
    linked = None
    if rk and an.get("ranked"):
        horses = [{
            "no": r.get("car"), "name": r.get("name", ""), "jockey": "",
            "totalScore": round(float(r.get("adjScore") or r.get("score") or 0), 1),
            "recentPlacings": [], "rentai": r.get("rentai"), "styleType": r.get("styleType"),
        } for r in an["ranked"] if r.get("car") is not None and r.get("score") is not None]
        if horses:
            sdb = _starters_load()
            sdb[rk] = {"horses": horses, "t": time.time(), "source": "keirin"}
            _starters_save(sdb)
            linked = rk
            print(f"[경륜 전적] {rk}: {len(horses)}두 live 분석 반영(競走得点)")
    return jsonify({"ok": True, "url": url, "card": card, "analysis": an, "linkedRaceKey": linked})


def _keirin_autocollect_form(rk, jo, ymd, race):
    """[경륜 출마표 전적 자동 수집] 배당 수집 시 동시에 oddspark 출마표에서 전적(競走得点·착순·결정수·각질) 수집·저장.
    이미 저장(source=keirin)됐으면 재fetch 생략(전적은 경주 중 불변). 실패해도 무시(배당 흐름 무영향).
    STARTERS_STORE(source=keirin) 저장 → _form_from_starters→_triple_analyze 통합등급(A/B/C/D) 자동 반영."""
    if not (rk and jo and ymd and race):
        return None
    try:
        sdb = _starters_load()
        _ex = sdb.get(rk)
        if _ex and _ex.get("source") == "keirin" and _ex.get("horses"):
            return _ex.get("horses")               # 이미 수집됨 → 재fetch 생략
        html = _keirin_fetch(_keirin_url(jo, ymd, race))
        card = _keirin_parse_card(html)
        if not card.get("riders"):
            return None
        if not card.get("venue") and KEIRIN_JO.get(str(jo)):
            card["venue"] = KEIRIN_JO[str(jo)]
        an = _keirin_analyze(card)
        horses = [{
            "no": r.get("car"), "name": r.get("name", ""), "jockey": "",
            "totalScore": round(float(r.get("adjScore") or r.get("score") or 0), 1),
            "recentPlacings": [], "rentai": r.get("rentai"), "styleType": r.get("styleType"),
            # [보완1] 競走得点 절대등급(95+ A/85+ B/75+ C/<75 D) — 통합 사분위 등급과 별개로 함께 표시
            "competScore": round(float(r.get("score") or 0), 1), "absGrade": _keirin_grade(r.get("score")),
            # [보완2] 각질 조정 내역: 통합점수 = 競走得点(원) + 각질보너스. 투명하게 분해 표시.
            "styleBonus": round(float(r.get("adjScore") or r.get("score") or 0) - float(r.get("score") or 0), 1),
        } for r in (an.get("ranked") or []) if r.get("car") is not None and r.get("score") is not None]
        if horses:
            sdb[rk] = {"horses": horses, "t": time.time(), "source": "keirin"}
            _starters_save(sdb)
            print(f"[경륜 전적·자동] {rk}: {len(horses)}두 수집(競走得点·절대등급 A/B/C/D) → 통합등급 자동 반영")
        return horses
    except Exception as e:
        print(f"[경륜 전적·자동] {rk} 실패(무시·배당 무영향): {e}")
        return None


@app.route("/api/keirin/odds", methods=["POST"])
def keirin_odds():
    """[경륜 배당 직접조회] oddspark 복승(2車複)·쌍승(2車単) 배당을 서버가 직접 fetch·파싱해
    기존 3종 수집 파이프라인(_do_triple_ingest)에 주입 → 같은 raceKey로 역배열·배당변화·이상감지가
    자동 계산됨(확장 탭수집 불필요). body: {joCode,kaisaiBi,raceNo | url, raceKey}.
    반복 호출(폴링) 시 히스토리 누적 → 배당변화 감지. 삼복승(3連複)은 oddspark 축선택 필요로 미수집
    (기존 _trio_est 추정 보험 유지)."""
    body = request.json or {}
    rk = (body.get("raceKey") or "").strip()
    if not rk:
        return jsonify({"error": "raceKey가 필요합니다(배당을 어느 경주에 연결할지)."}), 400
    jo, ymd, race = body.get("joCode"), body.get("kaisaiBi"), body.get("raceNo")
    # url 붙여넣기 허용 → 파라미터 추출
    src_url = (body.get("url") or "").strip()
    if src_url and not (jo and ymd and race):
        mj = re.search(r"joCode=(\d+)", src_url)
        my = re.search(r"kaisaiBi=(\d+)", src_url)
        mr = re.search(r"raceNo=(\d+)", src_url)
        jo = jo or (mj.group(1) if mj else None)
        ymd = ymd or (my.group(1) if my else None)
        race = race or (mr.group(1) if mr else None)
    if not (jo and ymd and race):
        return jsonify({"error": "joCode/kaisaiBi/raceNo(또는 url)가 필요합니다."}), 400
    try:
        q = _keirin_parse_quinella(_keirin_fetch(_keirin_odds_url(jo, ymd, race, 5)))
        x = _keirin_parse_exacta(_keirin_fetch(_keirin_odds_url(jo, ymd, race, 6)))
    except Exception as e:
        return jsonify({"error": "배당 수집 실패: %s" % e}), 502
    if not q and not x:
        return jsonify({"error": "배당 정보를 찾지 못했습니다(경주 번호/개최일/발매 여부 확인)."}), 422
    # 기존 파이프라인 주입(sport=cycle) → _triple_analyze 가 역배열·급락·이상감지 계산
    res = _do_triple_ingest(rk, q, x, [], {}, sport="cycle", category="cycle", source="oddspark")
    # [경륜 출마표 전적 자동 수집] 배당과 동시에 전적(競走得点·등급) 수집 → 통합등급 반영(1회·이미 있으면 생략)
    try:
        _keirin_autocollect_form(rk, jo, ymd, race)
    except Exception as _fe:
        print("[경륜 전적·자동] 오류(무시):", _fe)
    print(f"[경륜 배당] {rk}: 복승 {len(q)}·쌍승 {len(x)} oddspark 직접수집 → 파이프라인 반영")
    return jsonify({"ok": True, "raceKey": rk, "counts": {"quinella": len(q), "exacta": len(x)},
                    "quinella": q, "exacta": x, "ingest": res})


# ══════════ [경마 oddspark 서버 직접 수집] 지방경마(NAR) 복승·쌍승 배당 ══════════
# oddspark keiba 는 경륜(joCode)과 URL 체계가 다름 → opTrackCd + sponsorCd + raceNb 사용.
# betType(실측 확정): 6=복승(馬連·순서무관) · 5=쌍승(馬単·순서있음). ⚠ 경륜(5/6)과 반대이므로 주의.
_KEIBA_BET = {"quinella": 6, "exacta": 5}   # 복승=馬連=6 · 쌍승=馬単=5
_KEIBA_SCHED_CACHE = {}                     # {ymd: {한자경마장명: (opTrackCd, sponsorCd)}} 당일 캐시


def _keiba_odds_url(op_track, sponsor, ymd, race_nb, bet_type):
    """oddspark 지방경마 배당 URL. betType 6=복승(馬連)·5=쌍승(馬単)."""
    return ("https://www.oddspark.com/keiba/Odds.do"
            "?sponsorCd=%s&opTrackCd=%s&raceDy=%s&raceNb=%s&viewType=0&betType=%s"
            % (sponsor, op_track, ymd, race_nb, bet_type))


def _keiba_odds_live(q, x):
    """파싱된 복승·쌍승이 '발매 중 실배당'인지 판정(마감 후·발매 전 가짜값 차단).
    실배당은 대부분 소수부가 있음(4.5·12.7·2.7). 마감 후 oddspark가 주는 마방리스트·환급표는
    정수(2.0·3.0·0.0)만 → 소수부 비율이 낮으면 라이브 아님. 조합 최소 3개 + 소수부 30%+ 요구."""
    odds = [c.get("odds") for c in (list(q) + list(x)) if isinstance(c.get("odds"), (int, float)) and c.get("odds") > 0]
    if len(odds) < 3:
        return False
    frac = sum(1 for o in odds if abs(o - round(o)) > 1e-9)
    return (frac / len(odds)) >= 0.30


# ── oddspark 경마(keiba) 전용 배당 매트릭스 파서 ─────────────────────────────
#  ⚠ 경마는 경륜과 표 구조가 다르다(경륜 파서 재사용 시 조합 누락·방향 오류).
#   · 복승(馬連): 행=[axis, 배당, 상대차번호, 배당, 상대차번호, …]. 상대차가 명시적으로 라벨되고
#       첫 배당은 '전용 행이 없는 최소 차번(=implicit)'과의 조합. 상삼각 전체 = N(N-1)/2.
#   · 쌍승(馬単): 그리드(행=2着, 열 위치=1着). 한 블록 최대 8열 → 9두+는 뒤에 '꼬리 블록'으로 이어짐.
#       combo=[1着(열), 2着(행 axis)]. 방향 확정: 단승 압도적 인기마의 1着 조합이 최저(라이브 검증).
#  경륜 파서(_keirin_parse_*)는 그대로 보존(경륜 전용).
def _keiba_matrix_rows(html):
    """배당 매트릭스 행만 추출(선두=차번호·소수배당 포함·마명(가나/한자) 없는 행). 마방리스트 제외."""
    out = []
    for r in _keirin_table_rows(html):
        if not r or not re.fullmatch(r"\d{1,2}", r[0]):
            continue
        if any(re.search(r"[ぁ-んァ-ヶ一-龯]", c) for c in r):   # 마명 포함 행(마방리스트) 제외
            continue
        if not any(re.fullmatch(r"\d+\.\d+", c) for c in r):
            continue
        out.append(r)
    return out


def _keiba_horse_count(html):
    """출전 두수 추정 = 배당표에 등장하는 최대 차번호."""
    mx = 0
    for r in _keirin_table_rows(html):
        for c in r:
            if re.fullmatch(r"\d{1,2}", c):
                mx = max(mx, int(c))
    return mx


def _keiba_parse_quinella(html):
    """복승(馬連=betType6) 매트릭스 → [{combo:[a,b], odds}]. 상대차 라벨을 따라가는 walking 파싱.
    행 축(axis) + implicit 최소차번으로 상삼각 전체(N(N-1)/2) 복원."""
    rows = _keiba_matrix_rows(html)
    if not rows:
        return []
    axes = set(int(r[0]) for r in rows)
    maxcar = max((int(c) for r in rows for c in r if re.fullmatch(r"\d{1,2}", c)), default=0)
    implicit = next((c for c in range(1, maxcar + 1) if c not in axes), 1)  # 전용 행 없는 최소 차번
    out, seen = [], set()
    for r in rows:
        axis = int(r[0])
        partner = implicit                       # 첫 배당의 상대차(라벨 없이 선행)
        for c in r[1:]:
            if re.fullmatch(r"\d+\.\d+", c):
                od = float(c)
                if od > 0 and axis != partner:
                    a, b = sorted((axis, partner))
                    if (a, b) not in seen:
                        seen.add((a, b))
                        out.append({"combo": [a, b], "odds": od})
            elif re.fullmatch(r"\d{1,2}", c) and 1 <= int(c) <= maxcar:
                partner = int(c)                 # 다음 배당의 상대차 라벨
    return out


def _keiba_parse_exacta(html):
    """쌍승(馬単=betType5) 그리드 → [{combo:[1着,2着], odds}]. 행=2着(axis)·열 위치=1着.
    한 블록 8열 초과(9두+)는 축 번호가 리셋되는 '꼬리 블록'으로 이어짐 → block 오프셋으로 1着 복원."""
    rows = _keiba_matrix_rows(html)
    out, seen = [], set()
    block, prev = 0, None
    for r in rows:
        axis = int(r[0])                          # 행 = 2着
        vals = r[1:][0::2]                         # 짝수 위치=배당/빈칸(홀수=축 반복 라벨)
        if prev is not None and axis <= prev:      # 축 리셋 → 다음 8열 블록
            block += 1
        prev = axis
        for idx, v in enumerate(vals):
            first = block * 8 + idx + 1            # 열 위치 = 1着(블록 오프셋 반영)
            if not re.fullmatch(r"\d+\.\d+", v):
                continue
            od = float(v)
            if od <= 0 or first == axis:
                continue
            key = (first, axis)                   # combo=[1着, 2着]
            if key in seen:
                continue
            seen.add(key)
            out.append({"combo": [first, axis], "odds": od})
    return out


def _keiba_schedule(ymd, force=False):
    """그날(ymd=YYYYMMDD) oddspark 지방경마 개최 경마장 → {한자경마장명: (opTrackCd, sponsorCd)}.
    경마장 코드를 하드코딩하지 않고 실제 개최 스케줄에서 런타임 매핑(코드 오류 원천 차단).
    당일 메모리 캐시(6시간). 홈에서 RaceList 링크의 (opTrackCd,sponsorCd) 추출 후 각 제목에서 경마장명 파싱."""
    ck = _KEIBA_SCHED_CACHE.get(ymd)
    if ck and not force and (time.time() - ck.get("t", 0) < 21600):
        return ck["map"]
    out = {}
    try:
        home = _keirin_fetch("https://www.oddspark.com/keiba/")
        pairs = set()
        for m in re.finditer(r'RaceList\.do\?[^"\'>]*', home):
            seg = m.group(0).replace("&amp;", "&")
            mo = re.search(r'opTrackCd=(\d+)', seg)
            ms = re.search(r'sponsorCd=(\d+)', seg)
            md = re.search(r'raceDy=(\d+)', seg)
            if mo and ms and (not md or md.group(1) == ymd):
                pairs.add((mo.group(1), ms.group(1)))
        for op, sp in pairs:
            try:
                rl = _keirin_fetch("https://www.oddspark.com/keiba/RaceList.do"
                                   "?raceDy=%s&opTrackCd=%s&sponsorCd=%s" % (ymd, op, sp))
                mt = re.search(r'<title>(.*?)</title>', rl, re.S)
                title = _kstrip(mt.group(1)) if mt else ""
                mv = re.search(r'([一-龯]{2,4})競馬', title)   # '…園田競馬 1R…'
                if mv:
                    out[mv.group(1)] = (op, sp)
            except Exception:
                continue
    except Exception as e:
        print("[경마 스케줄] 조회 실패:", e)
    _KEIBA_SCHED_CACHE[ymd] = {"t": time.time(), "map": out}
    return out


def _keiba_resolve_track(venue, ymd):
    """raceKey 경마장명(한/일/영) → 그날 oddspark 개최 코드 (opTrackCd, sponsorCd) 또는 None.
    _track_norm 으로 표준화 후 한자명(_TRACK_GROUPS 첫 별칭)으로 스케줄 매칭."""
    if not venue:
        return None
    std = _track_norm(venue)                       # 예: 소노다/園田/sonoda → 소노다
    kanji = None
    als = _TRACK_GROUPS.get(std)
    if als:
        kanji = next((a for a in als if re.fullmatch(r'[一-龯]+', a)), None)
    sched = _keiba_schedule(ymd)
    if kanji and kanji in sched:
        return sched[kanji]
    # 폴백: 스케줄 한자명이 venue/표준명에 직접 포함되는지
    for kn, codes in sched.items():
        if kn == kanji or kn in (venue or ""):
            return codes
    return None


@app.route("/api/keiba/odds", methods=["GET", "POST"])
def keiba_odds():
    """[경마 서버 직접 수집] oddspark 지방경마 복승(馬連=6)·쌍승(馬単=5)을 서버가 직접 fetch·파싱해
    기존 파이프라인(_do_triple_ingest)에 주입 → 역배열·배당변화·이상감지 자동 계산(Chrome 확장 불필요).
    파라미터(POST=JSON body · GET=쿼리스트링 둘 다 허용):
      {raceKey, raceDy?(YYYYMMDD·기본 오늘), raceNo?(기본 raceKey에서 추출),
       opTrackCd?·sponsorCd?(직접 지정 시 스케줄 조회 생략)}.
    반복 호출(마감임박 3초 폴링) 시 히스토리 누적 → 배당변화 감지.
    ⚠ 삼복승(3連複)은 별도 축선택 페이지 필요 → 미수집(_trio_est 추정 보험 유지)."""
    # [GET/POST 공용] GET=쿼리스트링(브라우저·간편 테스트), POST=JSON body(프론트 폴링)
    body = request.json if request.method == "POST" else None
    body = body or request.args or {}
    rk = (body.get("raceKey") or "").strip()
    if not rk:
        return jsonify({"error": "raceKey가 필요합니다.",
                        "usage": "GET /api/keiba/odds?raceKey=소노다%2011경주 또는 POST {raceKey}"}), 400
    ymd = (str(body.get("raceDy") or "").strip()
           or time.strftime("%Y%m%d", time.localtime()))
    # 경주번호: 명시 우선 → raceKey에서 추출
    race_nb = body.get("raceNo")
    venue = None
    if not race_nb or not (body.get("opTrackCd") and body.get("sponsorCd")):
        venue, num = _area_num(rk)
        race_nb = race_nb or num
    if not race_nb:
        return jsonify({"error": "경주번호를 확인할 수 없습니다(raceKey에 'N경주' 포함 또는 raceNo 지정)."}), 400
    op_track, sponsor = body.get("opTrackCd"), body.get("sponsorCd")
    if not (op_track and sponsor):
        codes = _keiba_resolve_track(venue, ymd)
        if not codes:
            return jsonify({"error": "경마장 '%s' 이(가) %s 오늘 oddspark 개최 목록에 없습니다(개최일·경마장명 확인)."
                            % (venue or rk, ymd), "scheduled": list(_keiba_schedule(ymd).keys())}), 422
        op_track, sponsor = codes
    try:
        html_q = _keirin_fetch(_keiba_odds_url(op_track, sponsor, ymd, race_nb, _KEIBA_BET["quinella"]))
        html_x = _keirin_fetch(_keiba_odds_url(op_track, sponsor, ymd, race_nb, _KEIBA_BET["exacta"]))
        # [경마 전용 파서] 경륜과 표 구조가 달라 경마 전용 walking/grid 파서 사용(조합 누락·방향 오류 방지)
        q = _keiba_parse_quinella(html_q)
        x = _keiba_parse_exacta(html_x)
        n_horses = _keiba_horse_count(html_q)
    except Exception as e:
        return jsonify({"error": "배당 수집 실패: %s" % e}), 502
    # [발매 전·마감 후 방어] oddspark는 발매 중이 아니면 배당 매트릭스 대신 마방리스트·환급표(정수 0.0/착순)를
    #   제공 → 파서가 가짜 정수값을 뽑아 파이프라인을 오염시킴. 실배당은 소수부(4.5·12.7 등)가 대부분이므로
    #   '소수부 있는 배당 비율'로 라이브 여부 판정(마감 후 가짜값은 전부 X.0 → 차단).
    if not _keiba_odds_live(q, x):
        return jsonify({"ok": True, "waiting": True, "raceKey": rk,
                        "reason": "발매 중 배당 없음(발매 전·마감 후 추정) — 실배당 대기",
                        "track": {"opTrackCd": op_track, "sponsorCd": sponsor, "raceNb": race_nb},
                        "counts": {"quinella": len(q), "exacta": len(x)}})
    # [조합 수 검증] N두 출전 → 복승 N(N-1)/2 · 쌍승 N(N-1). 실제 파싱 수와 비교해 불일치 시 경고(파싱 누락 조기 발견).
    exp_q = n_horses * (n_horses - 1) // 2 if n_horses >= 2 else 0
    exp_x = n_horses * (n_horses - 1) if n_horses >= 2 else 0
    warn = None
    if n_horses >= 2 and (len(q) != exp_q or len(x) != exp_x):
        warn = (f"⚠️ 조합 수 불일치({n_horses}두): 복승 {len(q)}/{exp_q} · 쌍승 {len(x)}/{exp_x}"
                " — 배당 매트릭스 일부 누락 의심(파서 확인 필요)")
        print("[경마 배당 경고]", rk, warn)
    res = _do_triple_ingest(rk, q, x, [], {}, sport="horse", category="japan_local", source="oddspark")
    print(f"[경마 배당] {rk}: 복승 {len(q)}/{exp_q}·쌍승 {len(x)}/{exp_x} ({n_horses}두) oddspark 직접수집"
          f"(op{op_track}/sp{sponsor} R{race_nb}) → 파이프라인 반영")
    return jsonify({"ok": True, "raceKey": rk,
                    "counts": {"quinella": len(q), "exacta": len(x)},
                    "expected": {"horses": n_horses, "quinella": exp_q, "exacta": exp_x},
                    "warning": warn,
                    "track": {"opTrackCd": op_track, "sponsorCd": sponsor, "raceNb": race_nb},
                    "quinella": q, "exacta": x, "ingest": res})


_KEIBA_CURRENT_CACHE = {}   # {(op,sp,ymd): (t, race_no)} — 현재 발매중 경주번호 단기 캐시(15초)


def _keiba_current_raceno(op_track, sponsor, ymd, ttl=15):
    """[경주 자동추종] oddspark RaceList.do(raceNb 미지정) 응답 <title>이 '현재 발매중 경주'로 자동 포커스됨
    (예: '…園田競馬 11R…' → 11). 이 신호로 현재 경주번호를 단일 요청으로 감지(개최장별 독립).
    라이브 검증: 園田=11R·笠松=10R 동시 확인. 15초 메모리 캐시(oddspark 과호출 방지)."""
    key = (str(op_track), str(sponsor), str(ymd))
    ck = _KEIBA_CURRENT_CACHE.get(key)
    if ck and (time.time() - ck[0]) < ttl:
        return ck[1]
    race = None
    try:
        html = _keirin_fetch("https://www.oddspark.com/keiba/RaceList.do"
                             "?raceDy=%s&opTrackCd=%s&sponsorCd=%s" % (ymd, op_track, sponsor))
        mt = re.search(r"<title>(.*?)</title>", html, re.S)
        title = mt.group(1) if mt else ""
        m = re.search(r"競馬[\s　]*(\d+)\s*R", title) or re.search(r"(\d+)\s*R", title)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 12:
                race = n
    except Exception as e:
        print("[경주 자동추종] 현재 경주 감지 실패:", e)
    _KEIBA_CURRENT_CACHE[key] = (time.time(), race)
    return race


@app.route("/api/keiba/current", methods=["GET", "POST"])
def keiba_current():
    """[경주 자동추종] 지정 경마장의 '현재 발매중 경주번호'를 oddspark에서 감지해 반환.
    프론트가 폴링해 경주 전환 시 raceKey를 즉시 갱신(이전 경주 배당 잔존·지연 방지).
    body: {raceKey | venue, raceDy?(YYYYMMDD·기본 오늘), opTrackCd?·sponsorCd?}.
    반환: {ok, venue, currentRace, raceKey(현재번호로 재구성), track}."""
    body = request.json if request.method == "POST" else None
    body = body or request.args or {}
    rk = (body.get("raceKey") or "").strip()
    venue = (body.get("venue") or "").strip()
    num = None
    if rk and not venue:
        venue, num = _area_num(rk)
    ymd = (str(body.get("raceDy") or "").strip() or time.strftime("%Y%m%d", time.localtime()))
    op_track, sponsor = body.get("opTrackCd"), body.get("sponsorCd")
    if not (op_track and sponsor):
        codes = _keiba_resolve_track(venue, ymd)
        if not codes:
            return jsonify({"ok": False, "error": "경마장 '%s' 이(가) %s oddspark 개최 목록에 없습니다."
                            % (venue or rk, ymd), "scheduled": list(_keiba_schedule(ymd).keys())}), 422
        op_track, sponsor = codes
    cur = _keiba_current_raceno(op_track, sponsor, ymd)
    if not cur:
        return jsonify({"ok": False, "error": "현재 발매중 경주를 감지하지 못했습니다.", "venue": venue}), 422
    # raceKey 를 현재 경주번호로 재구성(경마장명·날짜 유지, 'N경주' 숫자만 교체)
    new_rk = rk
    if rk and num:
        new_rk = re.sub(r"\d+(\s*경주)", "%d\\1" % cur, rk, count=1)
    return jsonify({"ok": True, "venue": venue, "currentRace": cur,
                    "prevRace": num, "changed": bool(num and num != cur),
                    "raceKey": new_rk,
                    "track": {"opTrackCd": op_track, "sponsorCd": sponsor}})


@app.route("/api/keiba/schedule", methods=["GET", "POST"])
def keiba_schedule_ep():
    """그날 oddspark 지방경마 개최 경마장·코드 목록(디버그·프론트 확인용)."""
    ymd = ((request.json or {}).get("raceDy") if request.method == "POST"
           else request.args.get("raceDy")) or time.strftime("%Y%m%d", time.localtime())
    sched = _keiba_schedule(str(ymd).strip(), force=bool((request.args.get("force") or "")))
    return jsonify({"raceDy": ymd, "tracks": [{"venue": k, "opTrackCd": v[0], "sponsorCd": v[1]}
                                              for k, v in sched.items()]})


# ══════════ [지방경마 출주표 전적] oddspark RaceList.do(出走表) + HorseDetail(전5경주) ══════════
#   keiba.go.jp 대신 oddspark 를 메인 전적 소스로 사용. RaceList.do=현재 출전마(마번·마명·기수·부담중량·
#   거리·마장), HorseDetail.do=말별 전5경주(착순·경마장·거리·마장·통과순위=각질·상3F=막판스피드·기수·부담중량).
def _keiba_cells(tr):
    # HTML 엔티티(&#40;=( · &nbsp; 등)까지 디코딩해야 기수/소속/부담중량 셀 파싱이 됨.
    return [re.sub(r"\s+", " ", _htmllib.unescape(re.sub(r"<[^>]+>", "", c))).strip()
            for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]


def _keiba_dist_of(s):
    """'ダ1230'·'芝1400'·'1230m' → (surface, dist_int). 못 읽으면 (surface, None)."""
    s = s or ""
    surface = "더트" if "ダ" in s else "잔디" if "芝" in s else ""
    m = re.search(r"(\d{3,4})", s.replace(",", ""))
    return surface, (int(m.group(1)) if m else None)


def _keiba_parse_shutsuba(html):
    """oddspark 지방경마 출주표(RaceList.do) → {venue,raceNo,distance,surface,trackCond,horses:[...]}.
    horses[]: {no(마번=행순서), name, sexAge, jockey, weight(부담중량), winOdds, pop, lineageNb, detailUrl}."""
    out = {"venue": "", "raceNo": None, "distance": None, "surface": "", "trackCond": "", "horses": []}
    mt = re.search(r"<title>(.*?)</title>", html, re.S)
    if mt:
        t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", mt.group(1)))
        mv = re.search(r"(\S+?)競馬", t)
        if mv:
            out["venue"] = mv.group(1).strip()[-6:]
        mr = re.search(r"(\d+)R", t)
        if mr:
            out["raceNo"] = int(mr.group(1))
    # 레이스 헤더(거리·마장): HorseDetail 등장 이전 구간에서 'ダ1230'·마장상태 추출
    head = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html[:html.find("HorseDetail")] if "HorseDetail" in html else html[:4000]))
    md = re.search(r"([ダ芝])\s*(\d{3,4})\s*m?", head)
    if md:
        out["surface"] = "더트" if md.group(1) == "ダ" else "잔디"
        out["distance"] = int(md.group(2))
    mc = re.search(r"(良|稍重|重|不良)", head)
    if mc:
        out["trackCond"] = mc.group(1)
    # 출전마 행(HorseDetail 링크 보유) — 출주표는 馬番 순서
    trs = [t for t in re.findall(r"<tr[^>]*>.*?</tr>", html, re.S) if "HorseDetail" in t]
    for i, tr in enumerate(trs):
        cells = [c for c in _keiba_cells(tr) if c]
        mln = re.search(r"lineageNb=(\d+)", tr)
        lineage = mln.group(1) if mln else None
        # 마명 셀: 가타카나 이름 + 성별(牡/牝/セ) 포함. 없으면 가타카나 가장 긴 셀로 폴백.
        name_cell = next((c for c in cells if re.search(r"[ァ-ヶ]{2,}", c) and re.search(r"[牡牝セ]", c)), "")
        if not name_cell:
            kata = [c for c in cells if re.search(r"[ァ-ヶ]{3,}", c)]
            name_cell = max(kata, key=len) if kata else ""
        name = ""
        sex_age = ""
        if name_cell:
            name = re.split(r"[\s　]", name_cell.strip())[0]
            msa = re.search(r"([牡牝セ]\s*\d+)", name_cell)
            sex_age = re.sub(r"\s+", "", msa.group(1)) if msa else ""
        # 기수 셀: '福原杏 (兵庫) 57.0 …' — (소속) + 부담중량. 부담중량은 소수/정수 모두 허용.
        jockey_cell = next((c for c in cells if re.search(r"[（(][^）)]*[）)]", c) and re.search(r"\d{2}(?:\.\d)?", c)), "")
        jockey = ""
        weight = None
        if jockey_cell:
            jockey = re.split(r"[\s　（(]", jockey_cell.strip())[0]
            mw = re.search(r"(\d{2}(?:\.\d)?)\b", jockey_cell)
            weight = float(mw.group(1)) if mw else None
        # 단승배당·인기: '52.4 6人気'
        pop = None
        win_odds = None
        oc = next((c for c in cells if re.search(r"\d+\.\d+\s*\d*人気", c) or re.search(r"\d+人気", c)), "")
        if oc:
            mo = re.search(r"(\d+\.\d+)", oc)
            win_odds = float(mo.group(1)) if mo else None
            mp = re.search(r"(\d+)人気", oc)
            pop = int(mp.group(1)) if mp else None
        out["horses"].append({
            "no": i + 1, "name": name, "sexAge": sex_age, "jockey": jockey,
            "weight": weight, "winOdds": win_odds, "pop": pop, "lineageNb": lineage,
            "detailUrl": ("https://www.oddspark.com/keiba/HorseDetail.do?lineageNb=%s" % lineage) if lineage else None,
        })
    return out


def _keiba_parse_horse_past(html, max_n=5):
    """HorseDetail.do → 전적 리스트(최근순 최대 max_n). 各: {date,venue,distance,surface,trackCond,
    fieldSize,placing,jockey,weight,bodyWeight,time,last3f,corner(통과순위),winner}."""
    mtab = re.search(r"<table[^>]*>(?:(?!</table>).)*通過.*?</table>", html, re.S)
    if not mtab:
        return []
    past = []
    for tr in re.findall(r"<tr[^>]*>.*?</tr>", mtab.group(0), re.S):
        r = _keiba_cells(tr)
        if len(r) < 17 or not re.match(r"20\d\d", r[0] or ""):
            continue
        surface, dist = _keiba_dist_of(r[3])
        mcond = re.search(r"(良|稍重|重|不良)", r[4] or "")
        mgrade = re.search(r"(A1|A2|B1|B2|B3|C1|C2|C3|オープン|OPEN|OP)", r[2] or "")  # [2번] NAR 등급(레이스명)
        past.append({
            "date": r[0], "venue": r[1], "distance": dist, "surface": surface,
            "trackCond": (mcond.group(1) if mcond else ""),
            "raceName": (r[2] or "").strip(), "grade": (mgrade.group(1) if mgrade else ""),
            "fieldSize": _to_int(r[5]), "placing": _to_int(r[9]),
            "jockey": r[10], "weight": _safe_num(r[11]), "bodyWeight": r[12],
            "time": r[13], "last3f": _safe_num(r[15]), "corner": (r[16] or "").strip(),
            "winner": (r[17] if len(r) > 17 else ""),
        })
        if len(past) >= max_n:
            break
    return past


def _keiba_corner_style(past):
    """전5경주의 통과순위(通過)로 각질 추정. 코너 초반 순위가 두수 대비 앞이면 선행형, 뒤면 추격형.
    반환 (styleType, styleBonus, detail). 각질 판단 근거: 코너통과 앞→선행, 뒤→추격."""
    fronts, backs = 0, 0
    used = 0
    for pr in (past or [])[:3]:
        corner = pr.get("corner") or ""
        nums = [int(x) for x in re.findall(r"\d+", corner)]
        if not nums:
            continue
        field = pr.get("fieldSize") or max(nums + [8])
        first = nums[0]                      # 첫 코너 통과순위
        used += 1
        if first <= max(2, field * 0.30):    # 상위 30%권 = 선두권
            fronts += 1
        elif first >= field * 0.60:           # 하위 40%권 = 후미
            backs += 1
    if not used:
        return "", 0, []
    if fronts > backs and fronts >= 2:
        return "선행형", 3, [f"코너통과 선두권 {fronts}회 → 선행형(+3)"]
    if backs > fronts and backs >= 2:
        return "추격형", 5, [f"코너통과 후미 {backs}회 → 추격형(막판 추입·+5)"]
    if fronts > backs:
        return "선행형", 3, ["코너통과 앞선 편 → 선행형(+3)"]
    if backs > fronts:
        return "추격형", 5, ["코너통과 뒤선 편 → 추격형(+5)"]
    return "평지형", 0, []


def _keiba_last3f_note(past):
    """전5경주 상3F(막판 스피드) 평균. 빠를수록 추격형 강점. 반환 (avg, note)."""
    vals = [pr.get("last3f") for pr in (past or []) if isinstance(pr.get("last3f"), (int, float)) and pr["last3f"] > 0]
    if not vals:
        return None, ""
    avg = round(sum(vals) / len(vals), 1)
    tag = " (막판 스피드 우수)" if avg <= 38.5 else ""
    return avg, f"상3F 평균 {avg}초{tag}"


def _keiba_build_form(shutsuba, details):
    """출주표 + 말별 전적 → 통합 전적 점수. base_form_score(착순 가중평균) + 각질/거리변화/상3F 보정.
    STARTERS_STORE 저장·통합등급용 horses[] 반환."""
    cur_dist = shutsuba.get("distance")
    horses = []
    for h in shutsuba.get("horses", []):
        past = (details or {}).get(h.get("lineageNb")) or []
        placings = [pr.get("placing") for pr in past if pr.get("placing")]
        base = base_form_score(placings)
        style, sbonus, sdetail = _keiba_corner_style(past)
        # 거리 변화: 직전 경주 거리 vs 이번 거리(재사용: distance_change_bonus)
        nh = {"pastRaces": [{"distance": pr.get("distance")} for pr in past], "weight": h.get("weight")}
        dbonus, ddetail = distance_change_bonus(nh, {"distance": cur_dist}, style)
        # 부담중량 변화(직전 대비)
        nh2 = {"weight": h.get("weight"),
               "pastRaces": [{"weight": pr.get("weight")} for pr in past]}
        wbonus, wdetail = weight_change_bonus(nh2)
        last3f, l3note = _keiba_last3f_note(past)
        gbonus, gdetail = grade_bonus(None, [pr.get("grade") for pr in past])          # [2번] 등급 경험/하락강자
        debonus, dedetail, dexp = dist_exp_bonus(past, cur_dist)                       # [3번] 당거리 경험
        total = round(base + sbonus + dbonus + wbonus + gbonus + debonus, 1)
        horses.append({
            "no": h.get("no"), "name": h.get("name", ""), "jockey": h.get("jockey", ""),
            "weight": h.get("weight"), "winOdds": h.get("winOdds"), "pop": h.get("pop"),
            "recentPlacings": placings[:5], "baseScore": base,
            "styleType": style, "styleBonus": sbonus,
            "distanceBonus": dbonus, "weightBonus": wbonus, "last3f": last3f,
            "gradeBonus": gbonus, "distExpBonus": debonus, "distExperienced": dexp,
            "totalScore": total,
            "detail": sdetail + ddetail + wdetail + gdetail + dedetail + ([l3note] if l3note else []),
            "past": past,
        })
    _apply_back_power(horses)                     # [3번] 뒷힘(상3F 상대) 보정 + 거리미경험·뒷힘강점 플래그
    horses.sort(key=lambda x: x["totalScore"], reverse=True)
    n = len(horses)
    for i, h in enumerate(horses):
        h["rank"] = i + 1
        frac = i / n if n else 0                # 사분위 상대등급(전적점수 절대값이 낮게 뭉치는 NAR 대응)
        h["grade"] = "A" if frac < 0.25 else "B" if frac < 0.50 else "C" if frac < 0.75 else "D"
    return horses


def _apply_back_power(horses):
    """[3번] 경주 내 상3F 상대 뒷힘 보정(상위30% +15·하위 -5) + '⚡ 거리 미경험이나 뒷힘 강점' 플래그.
    horses[] 각 항목에 backPower(강/보통/약)·backPowerBonus 부여하고 totalScore·detail 갱신(멱등)."""
    all_l3 = [h.get("last3f") for h in horses]
    for h in horses:
        bp, bpdetail, level = back_power_bonus(h.get("last3f"), all_l3)
        h["backPower"] = level
        h["backPowerBonus"] = bp
        if bp:
            h["totalScore"] = round((h.get("totalScore") or 0) + bp, 1)
            h.setdefault("detail", []).extend(bpdetail)
        # 거리 미경험 + 뒷힘 강함 → 삼복승 보험 후보 플래그
        if h.get("distExperienced") is False and level == "강":
            h["backPowerInsurance"] = True
            h.setdefault("detail", []).append("⚡ 거리 미경험이나 뒷힘 강점 → 막판 뒤집기 가능(삼복승 보험)")


def _keiba_fetch_details(shutsuba, api_key=None):
    """출전마별 HorseDetail 을 ThreadPool(동시 4)로 병렬 fetch·파싱 → {lineageNb: past[]}."""
    targets = [(h["lineageNb"], h["detailUrl"]) for h in shutsuba.get("horses", [])
               if h.get("lineageNb") and h.get("detailUrl")]
    out = {}

    def _one(item):
        ln, url = item
        try:
            return ln, _keiba_parse_horse_past(_keirin_fetch(url))
        except Exception as e:
            print(f"[지방경마 전적] HorseDetail {ln} 실패:", e)
            return ln, []
    if targets:
        with ThreadPoolExecutor(max_workers=4) as ex:
            for ln, past in ex.map(_one, targets):
                out[ln] = past
    return out


@app.route("/api/keiba/starters", methods=["POST"])
def keiba_starters():
    """[지방경마 출주표 전적] oddspark 出走表 + 전5경주(HorseDetail)를 서버가 직접 fetch·파싱해
    각질(통과순위)·거리변화·상3F(막판스피드) 포함 전적 점수를 산출하고, raceKey로 live 통합등급에 연동.
    body: {raceKey | venue, raceDy?(YYYYMMDD·기본 오늘), opTrackCd?·sponsorCd?, raceNb, withDetail?(기본 true)}."""
    body = request.json or {}
    rk = (body.get("raceKey") or "").strip()
    venue = (body.get("venue") or "").strip()
    ymd = (str(body.get("raceDy") or "").strip() or time.strftime("%Y%m%d", time.localtime()))
    race_nb = body.get("raceNb")
    # raceKey에서 경마장명·경주번호 추출(명시값 우선)
    rk_venue, rk_num = _area_num(rk) if rk else (None, None)
    if not venue:
        venue = rk_venue or ""
    if not race_nb:
        race_nb = rk_num
    if not race_nb:
        return jsonify({"error": "raceNb(경주번호)가 필요합니다(raceKey에 'N경주' 포함 또는 raceNb 지정)."}), 400
    op_track, sponsor = body.get("opTrackCd"), body.get("sponsorCd")
    if not (op_track and sponsor):
        code = _keiba_resolve_track(venue, ymd)
        if not code:
            return jsonify({"error": "경마장 '%s' 이(가) %s oddspark 개최 목록에 없습니다(개최일·경마장명 확인)."
                            % (venue or rk, ymd), "scheduled": list(_keiba_schedule(ymd).keys())}), 422
        op_track, sponsor = code
    url = ("https://www.oddspark.com/keiba/RaceList.do?raceDy=%s&opTrackCd=%s&sponsorCd=%s&raceNb=%s"
           % (ymd, op_track, sponsor, race_nb))
    try:
        html = _keirin_fetch(url)
    except Exception as e:
        return jsonify({"error": "출주표 수집 실패: %s" % e}), 502
    shutsuba = _keiba_parse_shutsuba(html)
    if not shutsuba.get("horses"):
        return jsonify({"error": "출전마를 못 읽었습니다(경주번호/개최일 확인)."}), 422
    details = {}
    if body.get("withDetail", True):
        details = _keiba_fetch_details(shutsuba)
    horses = _keiba_build_form(shutsuba, details)
    # [live 통합] raceKey 오면 STARTERS_STORE 저장(source=oddspark) → _triple_analyze 통합등급 반영
    linked = None
    if rk and horses:
        store = [{"no": h["no"], "name": h["name"], "jockey": h.get("jockey", ""),
                  "totalScore": h["totalScore"], "recentPlacings": h.get("recentPlacings") or [],
                  "styleType": h.get("styleType"), "grade": h.get("grade")}
                 for h in horses if h.get("no") is not None]
        sdb = _starters_load()
        sdb[rk] = {"horses": store, "t": time.time(), "source": "oddspark"}
        _starters_save(sdb)
        linked = rk
        print(f"[지방경마 전적] {rk}: {len(store)}두 oddspark 전적 반영(각질·거리변화·상3F)")
    return jsonify({"ok": True, "url": url, "linkedRaceKey": linked,
                    "race": {"venue": shutsuba.get("venue"), "raceNo": shutsuba.get("raceNo"),
                             "distance": shutsuba.get("distance"), "surface": shutsuba.get("surface"),
                             "trackCond": shutsuba.get("trackCond")},
                    "horses": horses})


# ════════════ [중앙경마(JRA) 전적] netkeiba 馬柱(shutuba_past) 서버 fetch·파싱 ════════════
# 중앙경마는 keiba.go.jp(지방 전용)에 없고 JRA 공식은 POST세션이라 스크래핑 난해 → netkeiba 馬柱
# 페이지(서버렌더 HTML)를 소스로 사용. 한 페이지에 마번·기수·부담중량 + 과거5주(착순·거리·통과순위
# =각질·상3F=막판스피드·마체중)가 모두 담겨 지방경마(HorseDetail 개별 fetch)보다 오히려 단순.
# race_id 12자리에 場코드(4:6)·경주번호(10:12) 내장 → race_list_sub 로 그날 race_id 목록만 받으면 매핑.
_JRA_TRACK = {   # netkeiba 場코드 → (한글, 한자)
    "01": ("삿포로", "札幌"), "02": ("하코다테", "函館"), "03": ("후쿠시마", "福島"),
    "04": ("니가타", "新潟"), "05": ("도쿄", "東京"), "06": ("나카야마", "中山"),
    "07": ("주쿄", "中京"), "08": ("교토", "京都"), "09": ("한신", "阪神"), "10": ("고쿠라", "小倉"),
}
_JRA_SCHED_CACHE = {}   # {ymd: {"t":epoch, "ids":set(race_id)}}


def _jra_venue_code(venue):
    """경마장명(한/일/영) → netkeiba 場코드('05' 등) 또는 None."""
    if not venue:
        return None
    v = str(venue).strip()
    for code, (kr, kanji) in _JRA_TRACK.items():
        if v == kr or v == kanji or kanji in v or (len(kr) >= 2 and kr in v):
            return code
    std = _track_norm(v)                       # 표준화 폴백(소노다/園田 류와 동일 경로)
    for code, (kr, kanji) in _JRA_TRACK.items():
        if std == kr or std == kanji:
            return code
    return None


def _jra_race_list(ymd, force=False):
    """그날(ymd=YYYYMMDD) netkeiba 개최 race_id 집합. race_list_sub(서버렌더 fragment)에서 추출.
    당일 메모리 캐시(6시간)."""
    ck = _JRA_SCHED_CACHE.get(ymd)
    if ck and not force and (time.time() - ck.get("t", 0) < 21600):
        return ck["ids"]
    ids = set()
    try:
        h = _keirin_fetch("https://race.netkeiba.com/top/race_list_sub.html?kaisai_date=%s" % ymd)
        ids = set(re.findall(r"race_id=(\d{12})", h))
    except Exception as e:
        print("[JRA 스케줄] 조회 실패:", e)
    _JRA_SCHED_CACHE[ymd] = {"t": time.time(), "ids": ids}
    return ids


def _jra_resolve_raceid(venue, ymd, race_no):
    """경마장+개최일+경주번호 → netkeiba race_id(12자리) 또는 None. race_id에 場코드·경주번호가 내장."""
    code = _jra_venue_code(venue)
    if not code or not race_no:
        return None
    rr = "%02d" % int(race_no)
    for rid in _jra_race_list(ymd):
        if len(rid) == 12 and rid[4:6] == code and rid[10:12] == rr:
            return rid
    return None


def _jra_past_cell(cell):
    """馬柱 Past 셀(과거 1경주) → {date,venue,placing,distance,surface,trackCond,fieldSize,
    jockey,weight,corner(통과순위),last3f(상3F),bodyWeight}. Data01=날짜·착순 / Data05=코스·마장 /
    Data03=두수·인기·기수·부담 / Data06=통과-상3F-마체중."""
    def g(cls):
        m = re.search(r'<div[^>]*class="' + cls + r'"[^>]*>(.*?)</div>', cell, re.S)
        return re.sub(r"\s+", " ", _htmllib.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))).strip() if m else ""
    d01, d05, d03, d06 = g("Data01"), g("Data05"), g("Data03"), g("Data06")
    if not d01:
        return None
    # [2번] 등급: Data02 의 Icon_GradeType(OP/G1/G2/G3/L) 우선, 없으면 레이스명에서 조건(未勝利/1勝~3勝/新馬)
    d02 = g("Data02")
    mg = re.search(r"(G1|G2|G3|GⅠ|GⅡ|GⅢ|OP|L|オープン|リステッド|3勝|2勝|1勝|未勝利|新馬)", d02)
    grade = mg.group(1) if mg else ""
    mnum = re.search(r'<span[^>]*class="Num"[^>]*>\s*(\d+)', cell)
    placing = int(mnum.group(1)) if mnum else None
    md = re.search(r"(\d{4}\.\d{2}\.\d{2})\s*(\S+)?", d01)
    date = md.group(1) if md else ""
    venue = (md.group(2) if md and md.group(2) else "")
    surface, dist = _keiba_dist_of(d05)
    mc = re.search(r"(良|稍重|重|不良)", d05)
    cond = mc.group(1) if mc else ""
    mf = re.search(r"(\d+)頭", d03)
    field = int(mf.group(1)) if mf else None
    mj = re.search(r"\d+人\s*(\S+?)\s+(\d{2}(?:\.\d)?)\b", d03)
    jockey = mj.group(1) if mj else ""
    weight = float(mj.group(2)) if mj else None
    mcorner = re.search(r"(\d+(?:-\d+){1,3})", d06)   # 통과순위(코너별): '15-13' · '8-7-8'
    corner = mcorner.group(1) if mcorner else ""
    ml3 = re.search(r"\((\d{2}\.\d)\)", d06)          # 상3F: '(33.0)'
    last3f = float(ml3.group(1)) if ml3 else None
    mbw = re.search(r"(\d{3})\s*\(", d06)             # 마체중: '480(+2)'
    bodyw = mbw.group(1) if mbw else ""
    return {"date": date, "venue": venue, "placing": placing, "distance": dist, "surface": surface,
            "trackCond": cond, "fieldSize": field, "jockey": jockey, "weight": weight,
            "corner": corner, "last3f": last3f, "bodyWeight": bodyw, "grade": grade}


def _jra_parse_shutuba_past(html):
    """netkeiba 馬柱(shutuba_past) → {venue,raceNo,distance,surface,trackCond,horses:[...]}.
    horses[]: {no(馬番),name,jockey,weight(부담중량),nkStyle(netkeiba 각질 逃/先/差/追/自),past:[전5주]}.
    범례(placeholder '馬名') 행은 제외."""
    out = {"venue": "", "raceNo": None, "distance": None, "surface": "", "trackCond": "", "horses": []}
    mrid = re.search(r"race_id=(\d{12})", html)
    if mrid:
        rid = mrid.group(1)
        out["raceNo"] = int(rid[10:12])
        vt = _JRA_TRACK.get(rid[4:6])
        out["venue"] = vt[0] if vt else ""
    mrd = re.search(r'class="RaceData01"[^>]*>(.*?)</div>', html, re.S)
    if mrd:
        rdt = re.sub(r"<[^>]+>", " ", mrd.group(1))
        s, d = _keiba_dist_of(rdt)
        out["surface"], out["distance"] = s, d
        mc = re.search(r"(良|稍重|重|不良)", rdt)
        if mc:
            out["trackCond"] = mc.group(1)
    # [2번] 현재 경주 등급: RaceName 영역 Icon_GradeType(G1/G2/G3/L/OP) 또는 조건(3勝~1勝/未勝利/新馬)
    mrn = re.search(r'class="RaceName"[^>]*>(.*?)</div>', html, re.S)
    if mrn:
        rnt = re.sub(r"<[^>]+>", " ", mrn.group(1))
        mg = re.search(r"(G1|G2|G3|GⅠ|GⅡ|GⅢ|OP|L|オープン|リステッド|3勝|2勝|1勝|未勝利|新馬)", rnt)
        out["grade"] = mg.group(1) if mg else ""
    for blk in re.findall(r'<tr[^>]*class="[^"]*HorseList[^"]*"[^>]*>.*?</tr>', html, re.S):
        def cellraw(cls):
            m = re.search(r'<td[^>]*class="' + cls + r'"[^>]*>(.*?)</td>', blk, re.S)
            return m.group(1) if m else ""
        mno = re.search(r'<td[^>]*class="Waku"[^>]*>\s*(\d+)', blk)
        info, jk = cellraw("Horse_Info"), cellraw("Jockey")
        mname = re.search(r'class="Horse02"[^>]*>.*?<a[^>]*>(?:<[^>]+>)*\s*([^<]+)</a>', info, re.S)
        name = mname.group(1).strip() if mname else ""
        if not mno or not name or name == "馬名":     # 범례/빈 행 제외
            continue
        mstyle = re.search(r'class="kyakusitu"[^>]*>\s*([逃先差追自マ])', info)
        nk_style = mstyle.group(1) if mstyle else ""
        mjk = re.search(r"/jockey/[^>]*>\s*([^<]+)</a>", jk)
        jockey = mjk.group(1).strip() if mjk else ""
        mw = re.search(r"<span>\s*(\d{2}(?:\.\d)?)\s*</span>", jk)
        weight = float(mw.group(1)) if mw else None
        past = [p for p in (_jra_past_cell(pc)
                            for pc in re.findall(r'<td[^>]*class="Past[^"]*"[^>]*>(.*?)</td>', blk, re.S)) if p]
        out["horses"].append({"no": int(mno.group(1)), "name": name, "jockey": jockey,
                              "weight": weight, "nkStyle": nk_style, "past": past})
    return out


def _jra_style(h):
    """각질 판정: netkeiba 표기(逃/先=선행+3 · 差/追=추격+5 · 自=평지) 우선, 없으면 통과순위 폴백."""
    s = h.get("nkStyle")
    if s in ("逃", "先"):
        return "선행형", 3, ["netkeiba 각질 %s → 선행형(+3)" % s]
    if s in ("差", "追", "マ"):
        return "추격형", 5, ["netkeiba 각질 %s → 추격형(막판 추입·+5)" % s]
    if s == "自":
        return "평지형", 0, ["netkeiba 각질 自 → 평지형(자재)"]
    return _keiba_corner_style(h.get("past"))          # 통과순위 기반 폴백(지방경마와 동일 로직)


def _jra_build_form(shutuba):
    """馬柱 파싱 결과 → 통합 전적 점수. base_form_score(착순 가중평균) + 각질/거리변화/부담/상3F 보정.
    지방경마 _keiba_build_form 과 동일한 채점식(각질 소스만 netkeiba 표기 우선)."""
    cur_dist = shutuba.get("distance")
    cur_grade = shutuba.get("grade")
    horses = []
    for h in shutuba.get("horses", []):
        past = h.get("past") or []
        placings = [pr.get("placing") for pr in past if pr.get("placing")]
        base = base_form_score(placings)
        style, sbonus, sdetail = _jra_style(h)
        nh = {"pastRaces": [{"distance": pr.get("distance")} for pr in past], "weight": h.get("weight")}
        dbonus, ddetail = distance_change_bonus(nh, {"distance": cur_dist}, style)
        nh2 = {"weight": h.get("weight"), "pastRaces": [{"weight": pr.get("weight")} for pr in past]}
        wbonus, wdetail = weight_change_bonus(nh2)
        last3f, l3note = _keiba_last3f_note(past)
        gbonus, gdetail = grade_bonus(cur_grade, [pr.get("grade") for pr in past])   # [2번] 등급(G1~G3/OP·하락강자)
        debonus, dedetail, dexp = dist_exp_bonus(past, cur_dist)                     # [3번] 당거리 경험
        total = round(base + sbonus + dbonus + wbonus + gbonus + debonus, 1)
        horses.append({
            "no": h.get("no"), "name": h.get("name", ""), "jockey": h.get("jockey", ""),
            "weight": h.get("weight"), "recentPlacings": placings[:5], "baseScore": base,
            "styleType": style, "styleBonus": sbonus, "distanceBonus": dbonus, "weightBonus": wbonus,
            "last3f": last3f, "gradeBonus": gbonus, "distExpBonus": debonus, "distExperienced": dexp,
            "totalScore": total,
            "detail": sdetail + ddetail + wdetail + gdetail + dedetail + ([l3note] if l3note else []),
            "past": past,
        })
    _apply_back_power(horses)                     # [3번] 뒷힘(상3F 상대) + 거리미경험·뒷힘강점 플래그
    horses.sort(key=lambda x: x["totalScore"], reverse=True)
    n = len(horses)
    for i, h in enumerate(horses):
        h["rank"] = i + 1
        frac = i / n if n else 0                        # 사분위 상대등급(중앙 JRA도 착순 뭉침 대응)
        h["grade"] = "A" if frac < 0.25 else "B" if frac < 0.50 else "C" if frac < 0.75 else "D"
    return horses


@app.route("/api/jra/starters", methods=["POST"])
def jra_starters():
    """[중앙경마(JRA) 전적] netkeiba 馬柱(shutuba_past)를 서버가 직접 fetch·파싱해 각질(netkeiba 표기)·
    거리변화·부담중량·상3F 포함 전적 점수를 산출하고, raceKey로 live 통합등급(전적+배당역배열)에 연동.
    body: {raceKey | venue, raceDy?(YYYYMMDD·기본 오늘), raceNb, raceId?(직접 지정 시 매핑 생략)}."""
    body = request.json or {}
    rk = (body.get("raceKey") or "").strip()
    venue = (body.get("venue") or "").strip()
    ymd = (str(body.get("raceDy") or body.get("kaisaiDate") or "").strip()
           or time.strftime("%Y%m%d", time.localtime()))
    race_nb = body.get("raceNb")
    rk_venue, rk_num = _area_num(rk) if rk else (None, None)
    if not venue:
        venue = rk_venue or ""
    if not race_nb:
        race_nb = rk_num
    race_id = (body.get("raceId") or "").strip()
    if not race_id:
        if not race_nb:
            return jsonify({"error": "raceNb(경주번호)가 필요합니다(raceKey에 'N경주' 포함 또는 raceNb 지정)."}), 400
        race_id = _jra_resolve_raceid(venue, ymd, race_nb)
        if not race_id:
            return jsonify({"error": "경마장 '%s' %s R%s 이(가) netkeiba 개최 목록에 없습니다(개최일·경마장명 확인)."
                            % (venue or rk, ymd, race_nb),
                            "scheduled": sorted(_jra_race_list(ymd))[:40]}), 422
    try:
        html = _keirin_fetch("https://race.netkeiba.com/race/shutuba_past.html?race_id=%s" % race_id)
    except Exception as e:
        return jsonify({"error": "출주표 수집 실패: %s" % e}), 502
    shutuba = _jra_parse_shutuba_past(html)
    if not shutuba.get("horses"):
        return jsonify({"error": "출전마를 못 읽었습니다(race_id/개최일 확인)."}), 422
    horses = _jra_build_form(shutuba)
    linked = None
    if rk and horses:
        store = [{"no": h["no"], "name": h["name"], "jockey": h.get("jockey", ""),
                  "totalScore": h["totalScore"], "recentPlacings": h.get("recentPlacings") or [],
                  "styleType": h.get("styleType"), "grade": h.get("grade")}
                 for h in horses if h.get("no") is not None]
        sdb = _starters_load()
        sdb[rk] = {"horses": store, "t": time.time(), "source": "jra"}
        _starters_save(sdb)
        linked = rk
        print(f"[중앙경마 전적] {rk}: {len(store)}두 netkeiba 馬柱 전적 반영(각질·거리변화·상3F)")
    return jsonify({"ok": True, "raceId": race_id, "linkedRaceKey": linked,
                    "race": {"venue": shutuba.get("venue"), "raceNo": shutuba.get("raceNo"),
                             "distance": shutuba.get("distance"), "surface": shutuba.get("surface"),
                             "trackCond": shutuba.get("trackCond")},
                    "horses": horses})


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


@app.route("/api/data/backup", methods=["POST"])
def data_backup():
    """[데이터 보호] 학습 코퍼스(결과·분석로그·AI학습·패턴 등) 즉시 GitHub 백업.
    body: {label?, sync?} — sync=true면 완료까지 대기(기본 비동기 디바운스)."""
    body = request.json or {}
    label = (body.get("label") or f"데이터 수동 백업 {time.strftime('%Y-%m-%d %H:%M', time.localtime())}").strip()
    if body.get("sync"):
        _run_data_git_backup(label)
        return jsonify({"ok": True, "mode": "sync", "label": label})
    _data_git_backup(label, delay=0.5)   # 즉시(0.5초 후) 1회 백업 트리거
    return jsonify({"ok": True, "mode": "async", "label": label,
                    "paths": [p for p in DATA_BACKUP_PATHS
                              if os.path.exists(os.path.join(os.path.dirname(__file__), p))]})


@app.route("/api/data/status", methods=["GET"])
def data_status():
    """[데이터 보호] 추적 코퍼스 경로별 파일 수 + git 추적/미추적 요약(보호 현황 표시)."""
    root = os.path.dirname(os.path.abspath(__file__))
    out = []
    for p in DATA_BACKUP_PATHS:
        full = os.path.join(root, p)
        if p.endswith(".json"):
            out.append({"path": p, "exists": os.path.exists(full), "files": 1 if os.path.exists(full) else 0})
        elif os.path.isdir(full):
            out.append({"path": p, "exists": True,
                        "files": len([f for f in os.listdir(full) if f.endswith(".json")])})
        else:
            out.append({"path": p, "exists": False, "files": 0})
    try:
        tracked = subprocess.run(["git", "ls-files", "data/"], cwd=root, timeout=20,
                                 capture_output=True, text=True)
        tracked_n = len([l for l in (tracked.stdout or "").splitlines() if l.strip()])
    except Exception:
        tracked_n = None
    return jsonify({"paths": out, "trackedFiles": tracked_n})


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


_KRA_HIST_CACHE = {"mtime": None, "data": None}


def _kra_load_history():
    """kra_history.json 로드(파일 mtime 기준 캐시 — 30초 폴링마다 재파싱 방지)."""
    try:
        mt = os.path.getmtime(KRA_HISTORY_FILE)
    except OSError:
        return {}
    if _KRA_HIST_CACHE["mtime"] != mt:
        try:
            with open(KRA_HISTORY_FILE, encoding="utf-8") as f:
                _KRA_HIST_CACHE["data"] = json.load(f)
            _KRA_HIST_CACHE["mtime"] = mt
        except Exception:
            return {}
    return _KRA_HIST_CACHE["data"] or {}


def _kra_recent_placings(name, hist=None):
    """마명 → KRA 실제 최근 착순 배열(최대 5, 최신순). 없으면 []. [전적→부진마 학습 연결]"""
    if not name:
        return []
    if hist is None:
        hist = _kra_load_history()
    recs = (hist.get("byHorse") or {}).get(name.strip()) or []
    if not recs:
        return []
    ordered = sorted(recs, key=lambda r: r.get("date", ""), reverse=True)
    out = []
    for r in ordered[:5]:
        v = r.get("stOrd")
        if isinstance(v, (int, float)) and 1 <= int(v) <= 18:
            out.append(int(v))
    return out


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


# ── 3-2b 각질(레이스 스타일) 추정 [2] ──────────
def _running_style(horse):
    """직전 3경주의 초반 포지션(선행/중단/후방)으로 각질 추정.
    선행 우세 → '선행형', 후방 우세 → '추격형', 혼재 → '평지형'.
    포지션 정보가 전혀 없으면 '' (레이팅/착순만으로는 초반위치 추정 불가 → 미상)."""
    prs = (horse.get("pastRaces") or [])[:3]
    lead = mid = back = 0
    for pr in prs:
        pos = (pr.get("position") or "").strip()
        if any(k in pos for k in ("선행", "선두", "도주")):
            lead += 1
        elif any(k in pos for k in ("후방", "후미", "추입", "추격")):
            back += 1
        elif any(k in pos for k in ("중단", "중위", "중")):
            mid += 1
    if not (lead or mid or back):
        return ""
    if lead > back and lead >= mid:
        return "선행형"
    if back > lead and back >= mid:
        return "추격형"
    return "평지형"


# ── 3-2c 거리 변화 보너스 [3] ──────────────────
def distance_change_bonus(horse, race, style=None):
    """이번 거리 vs 직전 경주 거리. 단축=스피드 유리 +5, 연장=추격형 +3·선행형 -3·미상 적성체크."""
    rdist = _to_int(race.get("distance"))
    prs = horse.get("pastRaces") or []
    last_d = _to_int(prs[0].get("distance")) if prs else None
    if rdist is None or last_d is None or rdist == last_d:
        return 0, []
    if rdist < last_d:
        return 5, [f"거리단축({last_d}→{rdist}m·스피드유리)+5"]
    if style == "추격형":
        return 3, [f"거리연장({last_d}→{rdist}m·추격형적성)+3"]
    if style == "선행형":
        return -3, [f"거리연장({last_d}→{rdist}m·선행형스태미너주의)-3"]
    return 0, [f"거리연장({last_d}→{rdist}m·적성체크)"]


# ── 3-3b 기수 교체 보너스 [4] ──────────────────
def _leader_jockeys(jstats, top_frac=0.2):
    """jockeyStats(이름→통계)에서 복승권율(placeRate) 상위 top_frac 기수명 set."""
    rated = [(n, s.get("placeRate")) for n, s in (jstats or {}).items()
             if isinstance(s, dict) and isinstance(s.get("placeRate"), (int, float)) and s.get("rides", 0)]
    if not rated:
        return set()
    rates = sorted((r for _, r in rated), reverse=True)
    k = max(1, int(len(rates) * top_frac))
    thresh = rates[k - 1]
    return {n for n, r in rated if r >= thresh}


def jockey_change_bonus(horse, leader_names):
    """직전 기수 vs 이번 기수. 리딩기수로 교체 +10, 리딩→무명 강등 -5. 동일/미상/일반교체 0."""
    cur = str(horse.get("jockey", "")).strip()
    prs = horse.get("pastRaces") or []
    last_j = str((prs[0].get("jockey") or "")).strip() if prs else str(horse.get("lastJockey") or "").strip()
    if not cur or not last_j or cur == last_j:
        return 0, []
    ln = leader_names or set()
    if cur in ln and last_j not in ln:
        return 10, [f"리딩기수 교체({last_j}→{cur})+10"]
    if last_j in ln and cur not in ln:
        return -5, [f"무명기수 교체({last_j}→{cur})-5"]
    return 0, [f"기수교체({last_j}→{cur})"]


# ── 3-4b 부담중량 변화 보너스 [5] ──────────────
def weight_change_bonus(horse):
    """이번 부담중량 vs 직전. 증가 -5, 감소 +5, 동일/미상 0. (부담중량=핸디캡, 마체중과 별개)"""
    cur = _to_int(horse.get("weight"))
    prs = horse.get("pastRaces") or []
    last_w = _to_int(prs[0].get("weight")) if prs else _to_int(horse.get("lastWeight"))
    if cur is None or last_w is None or cur == last_w:
        return 0, []
    if cur > last_w:
        return -5, [f"부담중량 증가({last_w}→{cur}kg)-5"]
    return 5, [f"부담중량 감소({last_w}→{cur}kg)+5"]


# ── 3-3c 경마 등급 체계 보정 [2번] ──────────────
def _norm_grade(g):
    """등급명 정규화: 로마숫자→아라비아, 공백 제거, 대문자. 'GⅢ'/'G III'→'G3'."""
    s = str(g or "").upper().replace(" ", "").replace("　", "")
    s = s.replace("GⅢ", "G3").replace("GⅡ", "G2").replace("GⅠ", "G1")
    s = s.replace("GIII", "G3").replace("GII", "G2").replace("GI", "G1")
    s = s.replace("ＧⅢ", "G3").replace("ＧⅡ", "G2").replace("ＧⅠ", "G1")
    return s


# 등급 경험 보너스: 문자열에 토큰 포함 시 해당 보너스(최상위 하나만 적용). 상위→하위 순.
_GRADE_EXP_TIERS = [
    (("G1", "G2", "G3"), 20, "그레이드(G1~G3) 경험"),   # JRA 중상금 / KRA 그레이드 대상경주
    (("A1",), 20, "A1 경험"),                             # NAR A1
    (("A2",), 15, "A2 경험"),                             # NAR A2
    (("오픈", "OP", "オープン", "リステッド", "LISTED", "L"), 10, "오픈/리스티드 경험"),
    (("B1", "B2"), 10, "B급 경험"),                       # NAR B
    (("C1",), 5, "C1 경험"),                              # NAR C1
]
# 등급 랭크(작을수록 상위·강함) — 하락/상승 판정용. 상위 토큰 먼저.
_GRADE_RANK_TIERS = [
    ("G1", 1), ("G2", 2), ("G3", 3),
    ("A1", 2), ("A2", 3),
    ("오픈", 4), ("OP", 4), ("オープン", 4), ("リステッド", 4), ("LISTED", 4), ("L", 4),
    ("B1", 5), ("B2", 6),
    ("국1군", 5), ("1군", 5), ("국2군", 6), ("2군", 6), ("국3군", 7), ("3군", 7),
    ("국4군", 8), ("4군", 8), ("국5군", 9), ("5군", 9), ("국6군", 9), ("6군", 9),
    ("3勝", 5), ("2勝", 6), ("1勝", 7), ("未勝", 9), ("新馬", 9),
    ("C1", 7), ("C2", 8), ("C3", 8),
]


def _grade_exp_bonus_of(g):
    s = _norm_grade(g)
    if not s:
        return 0, ""
    for toks, bonus, label in _GRADE_EXP_TIERS:
        if any(_norm_grade(t) in s for t in toks):
            return bonus, label
    return 0, ""


def _grade_rank(g):
    s = _norm_grade(g)
    if not s:
        return None
    for tok, rk in _GRADE_RANK_TIERS:
        if _norm_grade(tok) in s:
            return rk
    return None


def grade_bonus(cur_grade, past_grades):
    """[2번] 등급 경험(최상위 하나) + 하락(상위→하위·강자 +15)/상승(하위→상위·적응중 -5).
    한국경마 G1~G3 +20·오픈 +10 / 지방 A1 +20·A2 +15·B +10·C1 +5. 반환 (bonus, detail)."""
    past = [g for g in (past_grades or []) if g]
    bonus, detail = 0, []
    exps = [_grade_exp_bonus_of(g) for g in past]
    best = max(exps, key=lambda x: x[0]) if exps else (0, "")
    if best[0] > 0:
        bonus += best[0]
        detail.append(f"{best[1]} +{best[0]}")
    cr = _grade_rank(cur_grade)
    lr = _grade_rank(past[0]) if past else None
    if cr is not None and lr is not None and cr != lr:
        if cr > lr:                      # rank 커짐 = 하위 등급으로 하락 = 상위에서 내려온 강자
            bonus += 15
            detail.append("등급 하락(상위→하위·강자) +15")
        else:                            # 상위 등급으로 상승 = 적응 중
            bonus -= 5
            detail.append("등급 상승(하위→상위·적응중) -5")
    return bonus, detail


# ── 3-3d 당거리 경험 [3번] ──────────────────
def dist_exp_bonus(past_races, cur_dist):
    """[3번] 당거리(정확히 같은 거리) 출전 경험. 있음 +10, 없음 0. 반환 (bonus, detail, experienced)."""
    cd = _to_int(cur_dist)
    if cd is None:
        return 0, [], None
    dists = [_to_int(pr.get("distance")) for pr in (past_races or [])]
    cnt = sum(1 for d in dists if d == cd)
    if cnt > 0:
        return 10, [f"당거리({cd}m) 경험 {cnt}회 +10"], True
    if any(d is not None for d in dists):
        return 0, [f"당거리({cd}m) 미경험"], False
    return 0, [], None


# ── 3-3e 뒷힘(상승3F) [3번] ──────────────────
def back_power_bonus(my_last3f, all_last3f):
    """[3번] 상승3F(막판 뒷힘). 경주 내 상대: 상위30%(빠름) +15, 하위30%(느림) -5, 보통 0.
    상3F는 작을수록 빠름(강함). 반환 (bonus, detail, level: 강/보통/약/None)."""
    if not isinstance(my_last3f, (int, float)) or my_last3f <= 0:
        return 0, [], None
    vals = sorted(v for v in (all_last3f or []) if isinstance(v, (int, float)) and v > 0)
    if len(vals) < 3:
        return 0, [], None
    p30 = vals[max(0, int(0.3 * (len(vals) - 1)))]        # 빠른 쪽 30% 경계
    p70 = vals[min(len(vals) - 1, int(0.7 * (len(vals) - 1)))]
    if my_last3f <= p30:
        return 15, [f"뒷힘 강점(상3F {my_last3f}초·상위30%) +15"], "강"
    if my_last3f >= p70:
        return -5, [f"막판 둔화(상3F {my_last3f}초·하위30%) -5"], "약"
    return 0, [f"상3F {my_last3f}초(보통)"], "보통"


# ── 2-1 고배당 말 자동 제거 [1·5번] ──────────────
def _high_odds_verdict(win_odds, has_signal):
    """[1·5번] 단승 배당 기준 고배당 제거. 999=무조건 / 100배+=확실 / 50~99=권장 / 30~49=신호없으면 제거.
    반환 (force_cut, reason). 이변 신호(급락·쌍승상위) 있으면 30~99배 구간은 제거 보류(999·100+는 무조건)."""
    if not isinstance(win_odds, (int, float)) or win_odds <= 0:
        return False, ""
    if win_odds >= 999:
        return True, "단승 999배 → 무조건 제거(전적 무관)"
    if win_odds >= 100:
        return True, "단승 100배+ → 확실 제거"
    if win_odds >= 50:
        return (not has_signal), "단승 50~99배 → 제거 권장"
    if win_odds >= 30:
        return (not has_signal), "단승 30~49배 → 관찰(신호 없으면 제거)"
    return False, ""


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


def score_horses_raw(race, horses, jockey_threshold=None, leader_names=None):
    """3-1~3-4: 마필별 baseScore/courseBonus/jockeyBonus/distanceBonus/jockeyChangeBonus/
    weightBonus/runningStyle/totalScore + flags (등급 전 단계).
    거리·기수교체·부담중량 보너스는 pastRaces 가 있을 때만 값이 생기고, 없으면 0 → 기존 동작 그대로."""
    if jockey_threshold is None:
        jockey_threshold = top20_threshold([h.get("jockey3mPlaceRate") for h in horses])
    scored = []
    for h in horses:
        placings = _placings_of(h)
        base = base_form_score(placings)
        cb, cd = course_aptitude_bonus(h.get("pastRaces"), race)
        jb, jd = jockey_bonus(h, jockey_threshold)
        style = _running_style(h)                              # [2] 각질 추정
        db_, dd = distance_change_bonus(h, race, style)        # [3] 거리 변화
        kb, kd = jockey_change_bonus(h, leader_names)          # [4] 기수 교체
        wb, wd = weight_change_bonus(h)                        # [5] 부담중량 변화
        # [등급/당거리 보정] 등급 경험·하락강자 + 당거리 정확경험(뒷힘은 상3F 있는 지방/중앙 build_form 에서)
        past_grades = [pr.get("grade") for pr in (h.get("pastRaces") or [])]
        gb, gd = grade_bonus(h.get("grade") or race.get("grade"), past_grades)   # [2] 등급 체계
        deb, ded, dexp = dist_exp_bonus(h.get("pastRaces"), race.get("distance"))  # [3] 당거리 경험
        flags = special_flags(h, race)
        scored.append({
            "no": h.get("no"), "name": h.get("name", ""), "jockey": h.get("jockey", ""),
            "recentPlacings": placings[:5],
            "baseScore": base, "courseBonus": cb, "jockeyBonus": jb,
            "distanceBonus": db_, "jockeyChangeBonus": kb, "weightBonus": wb,
            "gradeBonus": gb, "distExpBonus": deb, "distExperienced": dexp,
            "runningStyle": style,
            "totalScore": round(base + cb + jb + db_ + kb + wb + gb + deb, 1),
            "detail": cd + jd + dd + kd + wd + gd + ded, "flags": flags,
            "anomaly": h.get("anomaly"),
        })
    return scored, jockey_threshold


def compute_horse_scores(race, horses, jockey_threshold=None, anomaly_by_no=None, leader_names=None):
    """3-1~3-5 통합. anomaly_by_no(마번→이상감지) 주면 등급 보정에 반영."""
    scored, _ = score_horses_raw(race, horses, jockey_threshold, leader_names)
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
    """{race:{distance,course,grade}, horses:[{...전적/기수/마체중/이상감지...}], jockeyStats?}
    → 3-1~3-5 통합 점수 + 등급 + 플래그.
    jockeyStats(이름→통계) 주면 [4] 기수교체 보너스(리딩기수 판별)까지 활성화."""
    body = request.json or {}
    leaders = _leader_jockeys(body.get("jockeyStats"))   # [보완] 리딩기수 → 기수교체 보너스 활성
    scored = compute_horse_scores(body.get("race") or {}, body.get("horses") or [],
                                  leader_names=leaders)
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
# [사전분석] 경주별 분석결과를 파일로 영구 저장 → 경주 선택 시 개별 즉시 로드.
KOREA_PRERACE_DIR = os.path.join(os.path.dirname(__file__), "data", "prerace")
# [병렬화] 동시 Vision 호출 개수(3~4). [해시캐싱] 완료된 PDF 분석 결과 캐시 폴더.
KOREA_WORKERS = 4
KOREA_PDF_CACHE = os.path.join(os.path.dirname(__file__), "data", "korea_pdf_cache")


def _pdf_md5(path):
    """PDF 파일의 MD5 해시(재업로드 동일성 판정용). 실패 시 None."""
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _pdf_cache_path(md5):
    return os.path.join(KOREA_PDF_CACHE, f"{md5}.json")


def _pdf_cache_load(md5):
    """같은 해시의 '완료된' 세션 캐시를 반환(없으면 None)."""
    if not md5:
        return None
    try:
        with open(_pdf_cache_path(md5), encoding="utf-8") as f:
            c = json.load(f)
        if c and c.get("status") == "done":
            return c
    except Exception:
        pass
    return None


def _pdf_cache_save(sess):
    """완료된 세션을 PDF 해시 키로 캐시 저장(다음 동일 PDF 재업로드 시 즉시 반환)."""
    md5 = sess.get("pdfHash")
    if not md5 or sess.get("status") != "done":
        return
    try:
        os.makedirs(KOREA_PDF_CACHE, exist_ok=True)
        tmp = _pdf_cache_path(md5) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sess, f, ensure_ascii=False)
        os.replace(tmp, _pdf_cache_path(md5))
    except Exception as e:
        print("[한국] PDF 캐시 저장 실패:", e)
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


_KOREA_TIME_RE = re.compile(r'(?:[01]?\d|2[0-3]):[0-5]\d')


def _extract_korea_post_time(text):
    """한국 PDF 요약 페이지 텍스트에서 발주시각 'HH:MM'(24h·2자리 정규화) 추출. 실패 시 None.
    우선순위: ①경주 제목줄의 '...경마 N경주 ... (HH:MM)' 괄호 ②'발주'/'시각' 라벨 뒤 첫 HH:MM.
    Vision 추가 호출 없이 텍스트 레이어만 사용(검증: 17경주 전건 정확). PDF에 표기 없으면 None."""
    if not text:
        return None

    def _norm(t):
        h, m = t.split(":")
        return f"{int(h):02d}:{m}"

    lines = [l.strip() for l in text.splitlines()]
    # ① 제목줄: '경마 N경주 ... (HH:MM)'
    for l in lines:
        if "경주" in l and "경마" in l and re.search(r"경마\s*\d+\s*경주", l):
            paren = re.findall(r"\(((?:[01]?\d|2[0-3]):[0-5]\d)\)", l)
            if paren:
                return _norm(paren[-1])
    # ② '발주'/'시각' 라벨 근처 첫 HH:MM
    for i, l in enumerate(lines):
        if l.startswith("발주") or l in ("시각", "발주시각"):
            for j in range(i, min(i + 5, len(lines))):
                if _KOREA_TIME_RE.fullmatch(lines[j]):
                    return _norm(lines[j])
    return None


def _korea_extract_race(doc, race, api_key=None):
    """요약 페이지 1장에서 메인표+조교표를 추출·병합해 출전마 리스트 반환 (app.js extractRaceFull).
    부수적으로 요약 페이지 텍스트에서 발주시각을 추출해 race['postTime']에 채운다(추후 마감 알림 자동화)."""
    pg = doc[race["summaryPage"] - 1]   # summaryPage 는 1-based
    try:
        pt = _extract_korea_post_time(pg.get_text())
        if pt:
            race["postTime"] = pt
    except Exception as e:
        print("[한국] 발주시각 추출 실패:", e)
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
        subprocess.run(["git", "add", "data/korea_session.json", "data/korea_history", "data/prerace"],
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


# ── [PDF 전경주 사전분석 저장] 경주별 파일 영구 저장 + 개별 즉시 로드 ──────────
#   기존 korea_session.json(전체 세션) 흐름은 그대로 두고, '완료된 경주'만 별도로
#   data/prerace/<날짜>_<경마장>_<라운드>.json 에 저장해 개별 조회를 빠르게 한다.
def _prerace_key(date, venue, race_no):
    """파일 안전 키: '2026-07-05_부산_3'. 경마장 미상은 '기타'."""
    safe_v = re.sub(r"[^0-9A-Za-z가-힣]", "", str(venue or "")) or "기타"
    return f"{date or 'nodate'}_{safe_v}_{race_no}"


def _prerace_save_race(date, race):
    """완료된 경주 1건을 data/prerace/<key>.json 에 원자적 저장 + index.json 갱신.
    실패해도 본 분석 흐름을 막지 않도록 예외를 삼킨다."""
    try:
        os.makedirs(KOREA_PRERACE_DIR, exist_ok=True)
        key = _prerace_key(date, race.get("venue"), race.get("raceNo"))
        payload = {
            "key": key, "date": date, "venue": race.get("venue", ""),
            "raceNo": race.get("raceNo"), "distance": race.get("distance", ""),
            "title": race.get("title", ""), "horses": race.get("horses") or [],
            "report": race.get("report"), "status": race.get("status", "done"),
            "postTime": race.get("postTime"),   # 발주시각 'HH:MM'(PDF 텍스트 추출) — 마감 알림 자동화용
            "savedAt": time.time(),
        }
        path = os.path.join(KOREA_PRERACE_DIR, key + ".json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
        _prerace_index_update({"key": key, "date": date, "venue": payload["venue"],
                               "raceNo": payload["raceNo"], "distance": payload["distance"],
                               "title": payload["title"], "status": payload["status"],
                               "savedAt": payload["savedAt"],
                               "horseCount": len(payload["horses"])})
        return key
    except Exception as e:
        print("[사전분석] 경주 저장 실패:", e)
        return None


def _prerace_index_path():
    return os.path.join(KOREA_PRERACE_DIR, "index.json")


def _prerace_index_update(entry):
    """index.json 의 동일 key 항목을 교체(없으면 추가). savedAt 내림차순 정렬."""
    idx = _prerace_index_load()
    idx = [e for e in idx if e.get("key") != entry.get("key")]
    idx.append(entry)
    idx.sort(key=lambda e: e.get("savedAt") or 0, reverse=True)
    tmp = _prerace_index_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False)
    os.replace(tmp, _prerace_index_path())


def _prerace_index_load():
    """index.json 로드. 없으면 디렉터리 스캔으로 복구(디스크가 진실 소스)."""
    try:
        with open(_prerace_index_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        pass
    rebuilt = []
    try:
        for fn in os.listdir(KOREA_PRERACE_DIR):
            if not fn.endswith(".json") or fn == "index.json":
                continue
            try:
                d = json.load(open(os.path.join(KOREA_PRERACE_DIR, fn), encoding="utf-8"))
                rebuilt.append({"key": d.get("key"), "date": d.get("date"),
                                "venue": d.get("venue", ""), "raceNo": d.get("raceNo"),
                                "distance": d.get("distance", ""), "title": d.get("title", ""),
                                "status": d.get("status", "done"), "savedAt": d.get("savedAt") or 0,
                                "horseCount": len(d.get("horses") or [])})
            except Exception:
                continue
    except FileNotFoundError:
        return []
    rebuilt.sort(key=lambda e: e.get("savedAt") or 0, reverse=True)
    return rebuilt


def _prerace_load(key):
    """key 로 경주 1건 전체(리포트 포함) 로드. 없으면 None."""
    if not key or "/" in key or "\\" in key or ".." in key:   # 경로 조작 방어
        return None
    try:
        with open(os.path.join(KOREA_PRERACE_DIR, key + ".json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _prerace_clear():
    """data/prerace/ 전체 삭제('새 PDF 업로드' 초기화 시). 폴더 없으면 무시."""
    try:
        for fn in os.listdir(KOREA_PRERACE_DIR):
            try:
                os.remove(os.path.join(KOREA_PRERACE_DIR, fn))
            except Exception:
                pass
    except FileNotFoundError:
        pass


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

        # 1) 페이지 감지 (썸네일 6장 배치) — 썸네일 렌더는 메인 스레드(공유 doc 안전),
        #    Vision 호출만 ThreadPool 병렬(속도). [병렬화]
        if not sess.get("detectDone"):
            sess["phase"] = "detect"; sess["message"] = f"페이지 스캔 (병렬) / {total_pages}"
            _korea_save(sess)
            detected = {}
            CHUNK = 6
            batches = []
            for s in range(0, total_pages, CHUNK):
                pages = list(range(s, min(s + CHUNK, total_pages)))   # 0-based
                imgs = [_render_thumb(doc[p]) for p in pages]         # 메인 스레드 렌더
                batches.append((pages, imgs))

            def _detect_batch(item):
                pages, imgs = item
                if cancelled():
                    return None
                try:
                    return (pages, _do_detect(imgs, api_key))
                except Exception as e:
                    print("[한국] detect 실패", pages, e)
                    return (pages, None)

            with ThreadPoolExecutor(max_workers=KOREA_WORKERS) as ex:
                for res in ex.map(_detect_batch, batches):
                    if not res:
                        continue
                    pages, out = res
                    if not out:
                        continue
                    for r in (out.get("pages") or []):
                        i = r.get("index")
                        if i is not None and 0 <= i < len(pages):
                            detected[str(pages[i] + 1)] = r   # 1-based 키로 저장
            if cancelled():
                return
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

        # 4) 경주별 추출 + BMED 분석 — ThreadPool 병렬(3~4 동시). [병렬화]
        #    ⚠ PyMuPDF Document 는 스레드 안전하지 않으므로 각 워커가 KOREA_PDF 를 독립 open.
        #    세션 저장·사전분석 파일 저장(index.json R-M-W)은 메인 스레드에서만 수행(경쟁 방지).
        sess["phase"] = "analyze"
        races = sess["races"]
        jstats = sess.get("jockeyStats") or {}
        sess_date = sess.get("date")
        todo = [i for i, r in enumerate(races) if not (r.get("report") and r.get("horses"))]

        def _analyze_one(idx):
            race = races[idx]
            if cancelled():
                return (idx, None)
            try:
                d_local = fitz.open(KOREA_PDF)   # 워커 전용 문서(스레드 안전)
            except Exception as e:
                return (idx, {"status": "error", "error": f"PDF 재열기 실패: {e}"})
            try:
                horses = _korea_extract_race(d_local, race, api_key)
                if not horses:
                    return (idx, {"status": "empty", "postTime": race.get("postTime")})
                if cancelled():
                    return (idx, None)
                report = _do_analyze(
                    {"raceNo": race["raceNo"], "raceTitle": race["title"],
                     "horses": horses, "distance": race["distance"]},
                    jstats, api_key)
                return (idx, {"status": "done", "horses": horses, "report": report,
                              "postTime": race.get("postTime")})
            except Exception as e:
                print(f"[한국] 경주 '{race['title']}' 분석 실패:", e)
                return (idx, {"status": "error", "error": str(e)})
            finally:
                d_local.close()

        if todo:
            sess["message"] = f"분석 중... 0/{len(races)} 경주 완료 (병렬 {KOREA_WORKERS})"
            _korea_save(sess)
            with ThreadPoolExecutor(max_workers=KOREA_WORKERS) as ex:
                futs = [ex.submit(_analyze_one, i) for i in todo]
                for fut in as_completed(futs):
                    idx, res = fut.result()
                    if res is None:
                        continue   # 취소됨
                    race = races[idx]
                    race["status"] = res["status"]
                    if res.get("postTime"):
                        race["postTime"] = res["postTime"]
                    if res.get("horses"):
                        race["horses"] = res["horses"]
                    if res.get("report") is not None:
                        race["report"] = res["report"]
                    if res.get("error"):
                        race["error"] = res["error"]
                    if res["status"] == "done":
                        _prerace_save_race(sess_date, race)   # 메인 스레드에서만 저장
                    done = sum(1 for r in races if r.get("status") == "done")
                    sess["done"] = done
                    sess["message"] = f"분석 중... {done}/{len(races)} 경주 완료 — {race['title']}"
                    _korea_save(sess)
                    if cancelled():
                        break
            if cancelled():
                return

        sess["status"] = "done"; sess["phase"] = "done"
        sess["done"] = sum(1 for r in races if r.get("status") == "done")
        sess["message"] = f"완료 — {sess['done']}/{len(races)} 경주 분석 완료"
        _korea_save(sess)
        _pdf_cache_save(sess)   # [해시 캐싱] 동일 PDF 재업로드 시 즉시 복원용
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
    md5 = _pdf_md5(KOREA_PDF)
    # [해시 캐싱] 같은 PDF를 이전에 분석 완료했다면 재분석 없이 즉시 복원.
    cached = _pdf_cache_load(md5)
    if cached:
        cached = dict(cached)
        cached.update({"status": "done", "phase": "done", "date": date, "pdfHash": md5,
                       "message": (cached.get("message") or "") + " (캐시 즉시 복원)"})
        _korea_save(cached)
        # 사전분석 개별 파일도 캐시에서 복원(경주 선택 즉시 로드용)
        try:
            for r in (cached.get("races") or []):
                if r.get("status") == "done" and r.get("report") and r.get("horses"):
                    _prerace_save_race(date, r)
        except Exception as e:
            print("[한국] 캐시 사전분석 복원 실패:", e)
        return jsonify({"ok": True, "cached": True})
    sess = _korea_default_session()
    sess.update({"status": "running", "startedAt": time.time(), "date": date, "pdfHash": md5,
                 "label": f"{date} 분석 준비 중", "message": "업로드 완료 — 분석 시작"})
    _korea_save(sess)
    _korea_start_job(request.form.get("api_key"))
    return jsonify({"ok": True, "cached": False})


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
    _prerace_clear()   # [사전분석] 저장된 경주 파일도 초기화
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
        _prerace_save_race(sess.get("date"), race)   # [사전분석] 재추출 결과도 경주별 파일 갱신
        return jsonify({"ok": True, "race": race})
    finally:
        doc.close()


@app.route("/api/korea/prerace", methods=["GET"])
def korea_prerace_list():
    """[사전분석] 저장된 경주 목록(index) — 경주 선택 UI/즉시 로드용. 리포트 본문은 제외(경량)."""
    return jsonify({"races": _prerace_index_load()})


@app.route("/api/korea/prerace/<key>", methods=["GET"])
def korea_prerace_get(key):
    """[사전분석] 저장된 경주 1건 전체(출전마·리포트 포함) 즉시 로드."""
    d = _prerace_load(key)
    if not d:
        return jsonify({"error": "저장된 사전분석을 찾을 수 없습니다.", "key": key}), 404
    return jsonify(d)


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


# ═══════════════════════════════════════════════════════════════════════════
# [다중 경주 동시 배당판] — 별도 추가 기능(기존 단일 경주 분석 완전 유지·무영향)
#   ⚠ 핵심 원칙: triple_store.json 절대 미접근. multi_race_store.json / today_schedule.json 전용 저장소.
#   실패 격리(6번): fetch 실패 경주만 건너뜀 · 스케줄 실패 시 단일 모드 유지 · 저장소 손상 시 자동 초기화.
# ═══════════════════════════════════════════════════════════════════════════
MULTI_STORE_FILE = os.path.join(os.path.dirname(__file__), "data", "multi_race_store.json")
SCHEDULE_FILE = os.path.join(os.path.dirname(__file__), "data", "today_schedule.json")
_multi_lock = threading.Lock()
MULTI_COLLECT_LEAD_SEC = 600      # [2번] 발주 10분전부터 수집 시작
MULTI_URGENT_SEC = 180            # [3·5번] T-3분 이내 = 긴급(빨강)
MULTI_WARN_SEC = 600             # [3번] T-10분 이내 = 주의(주황)


def _multi_store_load():
    """multi_race_store.json 로드. 손상 시 자동 초기화(6번·기존 기능 영향 없음)."""
    try:
        with open(MULTI_STORE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        print("[다중경주] 저장소 손상 → 자동 초기화:", e)
        try:
            os.remove(MULTI_STORE_FILE)
        except OSError:
            pass
        return {}


def _multi_store_save(db):
    """원자적 쓰기(tmp→replace)로 저장소 손상 방지."""
    try:
        os.makedirs(os.path.dirname(MULTI_STORE_FILE), exist_ok=True)
        tmp = MULTI_STORE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False)
        os.replace(tmp, MULTI_STORE_FILE)
    except Exception as e:
        print("[다중경주] 저장 실패:", e)


def _schedule_load():
    try:
        with open(SCHEDULE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _schedule_save(sched):
    try:
        os.makedirs(os.path.dirname(SCHEDULE_FILE), exist_ok=True)
        tmp = SCHEDULE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sched, f, ensure_ascii=False)
        os.replace(tmp, SCHEDULE_FILE)
    except Exception as e:
        print("[스케줄] 저장 실패:", e)


def _post_time_epoch(hhmm, ymd):
    """'HH:MM' + ymd(YYYYMMDD) → 발주 epoch(서버 로컬). 실패 시 None."""
    try:
        m = re.match(r"(\d{1,2}):(\d{2})", (hhmm or "").strip())
        if not m or len(ymd or "") != 8:
            return None
        st = time.struct_time((int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]),
                               int(m.group(1)), int(m.group(2)), 0, 0, 0, -1))
        return time.mktime(st)
    except (ValueError, TypeError):
        return None


_MULTI_POST_CACHE = {}   # {(ymd,op,sp,rno): "HH:MM"} 발주시각 당일 캐시(시각은 하루 불변 → 재fetch 방지)


def _multi_post_time(op, sp, ymd, rno):
    """[1번] 특정 경주 RaceList.do?raceNb=N 페이지에서 発走時間(HH:MM) 추출. 당일 캐시·실패 시 None(격리)."""
    ck = (ymd, op, sp, rno)
    if ck in _MULTI_POST_CACHE:
        return _MULTI_POST_CACHE[ck]
    try:
        url = ("https://www.oddspark.com/keiba/RaceList.do?raceDy=%s&opTrackCd=%s&sponsorCd=%s&raceNb=%s"
               % (ymd, op, sp, rno))
        html = _keirin_fetch(url)
        # oddspark 실제 라벨: '発走時間 14:30'(時間) — 発走時刻/締切 아님. 방어적으로 발走 계열 다 허용.
        m = (re.search(r"発走時間\s*(\d{1,2}:\d{2})", html)
             or re.search(r"(?:発走時刻|発走|締切)\s*(\d{1,2}:\d{2})", html))
        pt = m.group(1) if m else None
        if pt:
            _MULTI_POST_CACHE[ck] = pt        # 성공만 캐시(실패는 다음 갱신 때 재시도)
        return pt
    except Exception:
        return None


def _multi_race_list(op, sp, ymd):
    """[1번] oddspark RaceList.do → [{raceNo, postTime, postEpoch}]. 경주번호는 목록 페이지에서, 発走時間은 경주별 페이지에서 병렬 취득.
      ⚠ KaisaiRaceList.do·목록 페이지엔 発走時間이 없어(각 경주 페이지에만 '発走時間 HH:MM' 존재) 경주별 fetch 필요."""
    url = ("https://www.oddspark.com/keiba/RaceList.do?raceDy=%s&opTrackCd=%s&sponsorCd=%s" % (ymd, op, sp))
    html = _keirin_fetch(url)
    # 경주번호 집합(raceNb 링크)
    nums = sorted({int(x) for x in re.findall(r"raceNb=(\d{1,2})", html) if 1 <= int(x) <= 12})
    if not nums:
        nums = list(range(1, 13))          # 링크 못 찾으면 1~12 시도(발주시각 없으면 자동 제외됨)
    # 発走時間 경주별 병렬 취득(부하·차단 방지 위해 소량 동시성)
    times = {}
    try:
        with ThreadPoolExecutor(max_workers=4) as ex:
            fut = {ex.submit(_multi_post_time, op, sp, ymd, n): n for n in nums}
            for f in as_completed(fut):
                times[fut[f]] = f.result()
    except Exception as e:
        print("[스케줄] 発走時間 병렬취득 오류(무시):", e)
    out = []
    for n in nums:
        pt = times.get(n)
        out.append({"raceNo": n, "postTime": pt, "postEpoch": _post_time_epoch(pt, ymd)})
    return out


def _multi_schedule_fetch():
    """[1번] 오늘 개최 경주 스케줄(경마장+경주번호+발주시각) → today_schedule.json. 실패해도 예외 전파 안 함."""
    ymd = time.strftime("%Y%m%d")
    sched = {"ymd": ymd, "updated": time.time(), "tracks": []}
    try:
        tracks = _keiba_schedule(ymd)         # {한자경마장명: (opTrackCd, sponsorCd)}
    except Exception as e:
        print("[스케줄] 개최 목록 실패(단일 모드 유지):", e)
        tracks = {}
    for kanji, codes in (tracks or {}).items():
        try:
            op, sp = codes
            races = _multi_race_list(op, sp, ymd)
            sched["tracks"].append({"venue": _track_norm(kanji), "kanji": kanji,
                                    "opTrackCd": op, "sponsorCd": sp, "races": races})
        except Exception as e:
            print("[스케줄]", kanji, "경주목록 실패(건너뜀):", e)
            continue
    _schedule_save(sched)
    print("[다중경주 스케줄] %s: 경마장 %d곳 · 경주 %d개"
          % (ymd, len(sched["tracks"]), sum(len(t["races"]) for t in sched["tracks"])))
    return sched


def _multi_key(venue, race_no):
    return "%s %d경주" % (venue, int(race_no))


def _multi_collect_one(track, race, ymd):
    """[2번] 한 경주 배당 수집 → multi_race_store 저장(실패 격리·triple_store 미접근). 반환 raceKey 또는 None.
      종목 라우팅: opTrackCd=경마(keiba) · joCode=경륜(keirin·배당은 공개라 서버 수집 가능·스케줄만 로그인 필요)."""
    try:
        rno = race["raceNo"]
        is_cycle = bool(track.get("joCode")) and (track.get("sport") == "cycle" or not track.get("opTrackCd"))
        if is_cycle:
            jo = track["joCode"]
            # 경륜: betType 5=복승(2車複)·6=쌍승(2車単) (경마와 반대)
            q = _keirin_parse_quinella(_keirin_fetch(_keirin_odds_url(jo, ymd, rno, 5)))
            x = _keirin_parse_exacta(_keirin_fetch(_keirin_odds_url(jo, ymd, rno, 6)))
            sport, category = "cycle", "cycle"
        else:
            op, sp = track["opTrackCd"], track["sponsorCd"]
            q = _keiba_parse_quinella(_keirin_fetch(_keiba_odds_url(op, sp, ymd, rno, _KEIBA_BET["quinella"])))
            x = _keiba_parse_exacta(_keirin_fetch(_keiba_odds_url(op, sp, ymd, rno, _KEIBA_BET["exacta"])))
            sport, category = "horse", "japan_local"
        if not _keiba_odds_live(q, x):
            return None                        # 발매 전·마감 후(가짜값) → 저장 안 함
        key = _multi_key(track["venue"], rno)
        with _multi_lock:
            db = _multi_store_load()
            prev = db.get(key) or {}
            hist = list(prev.get("history") or [])
            hist.append({"t": time.time(), "quinella": q, "exacta": x, "trio": [], "win": {}})
            hist = hist[-12:]
            db[key] = {"quinella": q, "exacta": x, "trio": [], "win": {}, "history": hist,
                       "sport": sport, "category": category,
                       "venue": track["venue"], "raceNo": rno,
                       "postTime": race.get("postTime"), "postEpoch": race.get("postEpoch"),
                       "t": time.time()}
            _multi_store_save(db)
        # [경륜 전적 자동 수집] 다중 경주 경륜 배당 수집 시 전적도 함께(1회·통합등급 반영)
        if is_cycle:
            try:
                _keirin_autocollect_form(key, track.get("joCode"), ymd, rno)
            except Exception:
                pass
        return key
    except Exception as e:
        print("[다중경주] 수집 실패 %s %s: %s" % (track.get("venue"), race.get("raceNo"), e))
        return None


_JRA_TRACKS = ("삿포로", "하코다테", "후쿠시마", "니가타", "도쿄", "나카야마", "주쿄", "쿄토", "교토", "한신", "고쿠라", "코쿠라")


def _multi_sport_of(rec):
    """[통합·4번] rec의 sport/category → 대시보드 종목 그룹(horse|central|cycle|boat|korea).
      중앙경마(JRA)는 category=japan_central 또는 JRA 경마장명으로 'central' 분류(지방경마 horse와 구분)."""
    cat = (rec.get("category") or "").lower()
    sp = (rec.get("sport") or "").lower()
    if sp == "cycle" or cat == "cycle":
        return "cycle"
    if sp == "boat" or cat == "boat":
        return "boat"
    _venue = rec.get("venue") or ""
    _area = _area_num(_venue)[0] or _area_num(rec.get("raceKey") or "")[0] or ""
    if cat == "korea" or _area in ("서울", "부산", "부경", "제주", "과천"):
        return "korea"
    if cat == "japan_central" or _area in _JRA_TRACKS or _venue in _JRA_TRACKS:
        return "central"                        # [중앙경마·JRA] 지방경마와 별도 그룹
    return "horse"


def _multi_card(key, rec):
    """[3번] 카드 요약: analyze(기존 _triple_analyze 재사용·읽기전용) → 유력마TOP3·핵심신호·확신도·마감남은초·색상."""
    try:
        an = _triple_analyze(key, rec)
    except Exception as e:
        print("[다중경주] 분석 실패", key, e)
        return None
    now = time.time()
    pe = rec.get("postEpoch")
    left = int(pe - now) if pe else None
    if left is None and not an.get("afterClose") and an.get("minutesBefore") is not None:
        left = int(an["minutesBefore"] * 60)   # [통합] postEpoch 없는 확장수집(경륜/한국)은 분석의 발주전분으로 카운트다운
    urgency = "normal"
    if left is not None:
        if left <= MULTI_URGENT_SEC:
            urgency = "urgent"                 # T-3분 이내 빨강
        elif left <= MULTI_WARN_SEC:
            urgency = "warn"                   # T-10분 이내 주황
    # 핵심 신호(급락/역배열/스마트머니) 요약
    signals = []
    drops = [d for d in (an.get("drops") or []) if (d.get("pct") or 0) <= -30]
    if drops:
        d0 = min(drops, key=lambda d: d.get("pct") or 0)
        signals.append({"type": "급락", "text": "🔴 %s 급락 %d%%" % ("+".join(str(x) for x in d0.get("combo") or []), round(d0.get("pct") or 0))})
    inv = an.get("inverse") or {}
    if inv.get("detected") and (inv.get("invLead") or {}).get("no") is not None:
        signals.append({"type": "역배열", "text": "🔄 %s번 역배열" % inv["invLead"]["no"]})
    smart = [h for h in (an.get("darkHorses") or []) if h.get("smartMoney")]
    if smart:
        signals.append({"type": "스마트머니", "text": "💰 %s번 스마트머니" % smart[0].get("no")})
    conf = an.get("confidence") or {}
    # [3번·중고배당 유력마] 있으면 카드에 💎 배지 + 요약
    _mhf = an.get("midHighFavorites") or []
    mid_high = [{"no": m["no"], "odds": m["odds"], "sigTypes": m.get("sigTypes") or []} for m in _mhf[:3]]
    # [4번·⚡ 이상감지 배지] 급락/역배열/스마트머니 신호 하나라도 있으면 anomaly=True
    anomaly = bool(signals)
    return {
        "raceKey": key, "venue": rec.get("venue"), "raceNo": rec.get("raceNo"),
        "sport": _multi_sport_of(rec),   # [통합·4번] 종목 그룹(horse|cycle|korea)
        "postTime": rec.get("postTime"), "secondsLeft": left, "urgency": urgency,
        "keyHorses": (an.get("keyHorses") or [])[:3],
        "signals": signals[:3], "anomaly": anomaly,   # [4번] ⚡ 이상감지 배지
        "midHigh": mid_high,   # [3번] 💎 중고배당 유력마(있으면 카드 배지·별도 강조)
        "confidence": conf.get("overall") or conf.get("level"),
        "quinellaMain": next((b.get("combo") for b in (an.get("betRecommend") or []) if b.get("label") == "복승 메인"), None),
        "afterClose": bool(an.get("afterClose")),
        "updatedSecondsAgo": int(now - (rec.get("t") or now)),
    }


@app.route("/api/multi/keirin-schedule", methods=["POST"])
def multi_keirin_schedule():
    """[확장 경유 경륜 스케줄] 확장(로그인 세션)이 oddspark 경륜 KaisaiRaceList를 fetch·파싱해 전송 →
    today_schedule.json에 sport=cycle 트랙으로 병합(기존 경마 트랙 보존·멱등). 로그인 벽 우회.
    body: {ymd, tracks:[{joCode, venue, races:[{raceNo, postTime}]}]}."""
    body = request.json or {}
    ymd = (body.get("ymd") or time.strftime("%Y%m%d")).strip()
    tracks = body.get("tracks") or []
    sched = _schedule_load()
    if not isinstance(sched, dict) or sched.get("ymd") != ymd:
        sched = {"ymd": ymd, "updated": time.time(), "tracks": []}
    # 기존 cycle 트랙 제거 후 재삽입(멱등) — 경마(horse) 트랙은 그대로 보존
    sched["tracks"] = [t for t in sched.get("tracks", []) if (t.get("sport") or "horse") != "cycle"]
    added = 0
    for t in tracks:
        races = []
        for r in (t.get("races") or []):
            pt = r.get("postTime")
            try:
                rno = int(r.get("raceNo"))
            except (TypeError, ValueError):
                continue
            races.append({"raceNo": rno, "postTime": pt, "postEpoch": _post_time_epoch(pt, ymd)})
        if not races:
            continue
        sched["tracks"].append({"venue": t.get("venue") or ("경륜%s" % t.get("joCode")),
                                "joCode": str(t.get("joCode") or ""), "sport": "cycle", "races": races})
        added += 1
    sched["updated"] = time.time()
    _schedule_save(sched)
    print("[경륜 스케줄·확장경유] %s: 경륜장 %d곳 병합" % (ymd, added))
    return jsonify({"ok": True, "tracks": added, "ymd": ymd})


@app.route("/api/multi/schedule", methods=["GET", "POST"])
def multi_schedule():
    """[1번] 오늘 개최 스케줄 조회(GET) / 강제 재수집(POST)."""
    if request.method == "POST":
        return jsonify(_multi_schedule_fetch())
    sched = _schedule_load()
    if not sched:                              # 없으면 1회 시도(비어도 단일 모드 안전)
        sched = _multi_schedule_fetch()
    return jsonify(sched)


@app.route("/api/multi/collect", methods=["POST"])
def multi_collect_now():
    """[2번] 지금 발주 임박(T-10분 이내) 경주 배당 즉시 수집(수동 트리거). 실패 격리."""
    sched = _schedule_load() or _multi_schedule_fetch()
    ymd = sched.get("ymd") or time.strftime("%Y%m%d")
    now = time.time()
    collected, skipped = [], 0
    for tr in sched.get("tracks", []):
        for rc in tr.get("races", []):
            pe = rc.get("postEpoch")
            if pe and (pe - now) <= MULTI_COLLECT_LEAD_SEC and (pe - now) > -300:   # 발주 10분전~5분후
                k = _multi_collect_one(tr, rc, ymd)
                if k:
                    collected.append(k)
                else:
                    skipped += 1
    return jsonify({"ok": True, "collected": collected, "skipped": skipped})


@app.route("/api/multi/dashboard", methods=["GET"])
def multi_dashboard():
    """[3번] 다중 경주 대시보드 카드 — 배당 수집된 경주(분석 카드) + 아직 수집 전인 예정 경주(카운트다운) 병합.
      수집 전 경주도 스케줄에서 카운트다운 카드로 표시 → 발주 10분전 이전에도 오늘 전체 경주가 보임."""
    db = _multi_store_load()
    cards = []
    collected_keys = set()
    for key, rec in db.items():
        c = _multi_card(key, rec)
        if c:
            cards.append(c)
            collected_keys.add(key)
    # [통합·1·3번] 확장이 수집한 경주(triple_store)도 읽기 전용으로 포함 → 경륜·한국경마·일본경마 통합.
    #   ⚠ triple_store는 절대 쓰지 않고 읽기만(로그인 필요한 경륜 스케줄 서버fetch 대체). 30분+ 미갱신·마감 후는 제외.
    try:
        tdb = _triple_load()
        for key, rec in tdb.items():
            if key in collected_keys:
                continue                              # multi_race_store 우선(중복 제거)
            if (time.time() - (rec.get("t") or 0)) > STALE_ACTIVE_SEC:
                continue                              # 끝난 경주(30분+ 미갱신) 제외
            _sp = _multi_sport_of(rec)
            if _sp == "boat":                         # 경정은 우선 제외(사용자 요청)
                continue
            _va, _nu = _area_num(key)
            _rec2 = dict(rec)
            _rec2.setdefault("venue", _va)
            _rec2.setdefault("raceNo", _nu)
            c = _multi_card(key, _rec2)
            if c and (c.get("secondsLeft") is None or c["secondsLeft"] > -300):
                cards.append(c)
                collected_keys.add(key)
    except Exception as e:
        print("[다중경주] triple_store 병합 오류(무시):", e)
    # [예정 경주] today_schedule의 발주 전 경주(아직 미수집·마감 안 지남)를 카운트다운 카드로 추가
    now = time.time()
    sched = _schedule_load()
    for tr in sched.get("tracks", []):
        for rc in tr.get("races", []):
            pe = rc.get("postEpoch")
            key = _multi_key(tr.get("venue"), rc.get("raceNo")) if rc.get("raceNo") else None
            if not key or key in collected_keys or not pe:
                continue
            left = int(pe - now)
            if left < -120:                 # 이미 마감 2분+ 지난 경주는 제외
                continue
            urg = "urgent" if left <= MULTI_URGENT_SEC else ("warn" if left <= MULTI_WARN_SEC else "normal")
            cards.append({"raceKey": key, "venue": tr.get("venue"), "raceNo": rc.get("raceNo"),
                          "sport": "horse", "postTime": rc.get("postTime"), "secondsLeft": left, "urgency": urg,
                          "keyHorses": [], "signals": [], "anomaly": False, "confidence": None,
                          "quinellaMain": None, "afterClose": False, "scheduled": True})
    cards.sort(key=lambda c: (c.get("secondsLeft") if c.get("secondsLeft") is not None else 9e9))
    urgent = [c["raceKey"] for c in cards if c.get("urgency") == "urgent" and not c.get("afterClose")]
    # [5번] 종목별 카운트(토글 UI용)
    by_sport = {}
    for c in cards:
        by_sport[c.get("sport") or "horse"] = by_sport.get(c.get("sport") or "horse", 0) + 1
    return jsonify({"cards": cards, "urgent": urgent, "count": len(cards),
                    "collected": len(collected_keys), "bySport": by_sport})


@app.route("/api/multi/race/<path:key>", methods=["GET"])
def multi_race_detail(key):
    """[4번] 카드 클릭 → 해당 경주 전체 분석(기존 _triple_analyze 재사용). 결과입력은 기존 엔드포인트 사용."""
    db = _multi_store_load()
    rec = db.get(key)
    if not rec:
        rec = _triple_load().get(key)   # [통합] 확장 수집(경륜/한국/일본) 읽기 전용 폴백
    if not rec:
        return jsonify({"error": "해당 경주 배당이 아직 수집되지 않았습니다.", "waiting": True}), 200
    try:
        an = _triple_analyze(key, rec)
    except Exception as e:
        return jsonify({"error": "분석 실패: %s" % e}), 200
    return jsonify(an)


def _multi_bg_loop():
    """[1·2번] 백그라운드: 스케줄 30분 갱신 + 발주 임박 경주 배당 주기 수집(실패 격리·서버 죽지 않음)."""
    last_sched = 0
    last_day = time.strftime("%Y%m%d")
    while True:
        try:
            now = time.time()
            # [날짜 자동 초기화] 자정 넘어 날짜 바뀌면 어제 데이터 정리 + 스케줄 즉시 재수집(새 날짜 경주로 재시작)
            _cur_day = time.strftime("%Y%m%d")
            if _cur_day != last_day:
                print(f"[날짜초기화] 날짜 변경 {last_day}→{_cur_day} → 어제 데이터 자동 정리")
                _startup_date_reset()
                last_day, last_sched = _cur_day, 0
            if now - last_sched >= 1800:       # 30분마다 스케줄 갱신
                _multi_schedule_fetch()
                last_sched = now
            sched = _schedule_load()
            ymd = sched.get("ymd") or time.strftime("%Y%m%d")
            for tr in sched.get("tracks", []):
                for rc in tr.get("races", []):
                    pe = rc.get("postEpoch")
                    if pe and -120 <= (pe - now) <= MULTI_COLLECT_LEAD_SEC:   # 발주 10분전~2분후
                        _multi_collect_one(tr, rc, ymd)
        except Exception as e:
            print("[다중경주] 백그라운드 오류(무시·서버 유지):", e)
        time.sleep(30)


def _start_multi_race_bg():
    """[6번] 다중 경주 백그라운드 시작(실패해도 서버·단일 모드 무영향)."""
    try:
        threading.Thread(target=_multi_bg_loop, daemon=True).start()
        print("[다중경주] 백그라운드 시작(스케줄 30분·수집 30초·실패격리)")
    except Exception as e:
        print("[다중경주] 백그라운드 시작 실패(단일 모드 유지):", e)


def _startup_date_reset():
    """[서버 시작 시 날짜 자동 초기화] 어제(오늘 아닌 날짜) 데이터를 자동 정리 → '어제 경주 잔존' 방지.
      ⚠ 학습 데이터(odds_history·learning·pattern 등)는 절대 건드리지 않음 — 활성 캐시/고정만 정리(기존 기능 무영향)."""
    today = time.strftime("%Y-%m-%d")
    today_ymd = time.strftime("%Y%m%d")

    def _dstr(t):
        return time.strftime("%Y-%m-%d", time.localtime(t or 0))
    # 1) race_pin.json — 어제 고정 경주면 자동 해제(오늘 아닌 고정 초기화)
    try:
        d = json.load(open(RACE_PIN_FILE, encoding="utf-8"))
        pdate = d.get("date") or _dstr(d.get("pinnedAt"))
        if pdate != today:
            _race_pin_clear()
            print(f"[날짜초기화] 고정 경주({pdate}, 어제) → 자동 해제")
    except Exception:
        pass
    # 2) multi_race_store — 오늘 아닌 경주 제거(oddspark 다중 수집 날짜 초기화)
    try:
        mdb = _multi_store_load()
        keep = {k: v for k, v in mdb.items() if _dstr(v.get("t")) == today}
        if len(keep) != len(mdb):
            _multi_store_save(keep)
            print(f"[날짜초기화] 다중경주 저장소: 어제 경주 {len(mdb) - len(keep)}건 제거(새 날짜로 재시작)")
    except Exception:
        pass
    # 3) today_schedule — 어제 날짜면 비움(백그라운드가 오늘 스케줄로 재생성)
    try:
        sched = _schedule_load()
        if sched.get("ymd") and sched["ymd"] != today_ymd:
            _schedule_save({})
            print(f"[날짜초기화] 스케줄({sched['ymd']}, 어제) → 초기화(오늘 재수집 대기)")
    except Exception:
        pass
    # 4) triple_store(단일 경주 활성 캐시) — 어제 경주 제거(오늘 것만 유지). ⚠ odds_history 학습 파일은 보존.
    try:
        tdb = _triple_load()
        keep = {k: v for k, v in tdb.items() if _dstr(v.get("t")) == today}
        if len(keep) != len(tdb):
            _triple_save(keep)
            print(f"[날짜초기화] 단일 배당 활성캐시: 어제 경주 {len(tdb) - len(keep)}건 제거(히스토리 파일 보존)")
    except Exception:
        pass


if __name__ == "__main__":
    print("서버 시작: http://127.0.0.1:8011 (자동 리로드 ON, 코드 수정이 바로 반영됩니다)")
    # debug=True: 코드 저장 시 자동 재기동(stale 서버로 인한 405 재발 방지). 로컬 전용(127.0.0.1).
    # threaded=True: 브라우저의 다중 keep-alive 연결을 동시 처리(단일 스레드 멈춤 방지).
    # 재개는 리로더의 실제 작업 프로세스(WERKZEUG_RUN_MAIN)에서만 1회 수행.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        _korea_maybe_resume()
        _start_periodic_backup()   # [데이터 자동백업 완성] 6시간 주기 안전 백업(결과 미입력이어도 백업)
        _start_daily_learning_scheduler()   # [학습일지] 매일 22:00 학습 일지 자동 생성·백업
        _startup_date_reset()      # [날짜 초기화] 서버 시작 시 어제 고정·수집 데이터 자동 정리(학습 데이터 보존)
        _start_multi_race_bg()     # [다중 경주 동시 배당판] 별도 저장소·실패격리·단일모드 무영향
        try:
            _archive_compress_old(7)   # [영구보존·4번] 7일+ 경주 배당 .gz 압축 보관(데이터 삭제 없음·용량 관리)
        except Exception as _e:
            print("[아카이브 압축] 시작 시 실패:", _e)
    app.run(host="127.0.0.1", port=8011, debug=True, threaded=True)
