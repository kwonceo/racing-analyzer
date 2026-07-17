# -*- coding: utf-8 -*-
"""
[경쟁 AI 추천 입력·비교 분석]  — 홈서버(서버 실행 중)에서 실행.

용도: 경쟁 AI의 추천 복승 조합을 경주별로 입력 → 서버가 축·연결마·패턴을 자동분석하고
      우리 추천/실제결과와 비교해 data/competitor_analysis/ 에 저장 + 누적통계 갱신.

────────────────────────────────────────────────────────────
사용법 1) 텍스트로 직접 입력(기존 방식)
    cd C:\\Users\\USER\\Desktop\\경마분석서버
    py tools\\register_competitor.py                 # 아래 COMPETITOR_TABLE 입력
    py tools\\register_competitor.py comp.txt          # 파일에서 읽기(재사용)

  입력 형식(한 줄에 한 경주, 왼쪽=경주명 | 오른쪽=저쪽 조합들):
    소노다 6경주 | 4+10 7+10 1+4
    소노다 11경주 | 3+6, 7+9, 3+7
  · 조합 구분: 공백 또는 쉼표.  조합 안 번호 구분: + 또는 - (예: 4+10, 4-10 둘 다 됨)
  · 경주명은 등록결과 때 쓰신 것과 같게(예: '소노다 6경주').

────────────────────────────────────────────────────────────
사용법 2) 사진(캡쳐)으로 자동 인식  ★ 신규
    py tools\\register_competitor.py --img 사진.jpg --race "소노다 6경주"

  · --img  : 저쪽 AI 추천표(구매표) 사진/캡쳐 파일 경로.
  · --race : 그 사진이 어느 경주인지(경주명). 사진에는 조합만 있고 경주명은 없으므로 꼭 적어주세요.
  · 사진 속 표 예시(번호 배당 구매장수 금액):
        번호     배당    구매장수   금액
        4-10    11.1    100      1,010
        7-10    11.5    100      1,050
    → '번호' 칸(4-10, 7-10 ...)만 읽어서 조합 [[4,10],[7,10]] 으로 자동 추출·등록합니다.
  · 인식은 Claude Vision(.env 의 ANTHROPIC_API_KEY 사용, 서버와 같은 키)으로 처리합니다.
  · 여러 장이면 한 장씩:  --img a.jpg --race "소노다 6경주"  실행 후  --img b.jpg --race "소노다 11경주"
────────────────────────────────────────────────────────────
"""
import base64
import json
import os
import re
import sys
import urllib.request
import urllib.error


# ---------- .env 로더 (서버 app.py 와 동일 방식, dotenv 불필요) ----------
def _load_env():
    """스크립트 위치 기준 상위 폴더(서버 루트)의 .env 를 읽어 환경변수로 올린다."""
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, "..", ".env"), os.path.join(here, ".env"),
                 os.path.join(os.getcwd(), ".env")):
        path = os.path.abspath(path)
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except Exception:
            pass
        return


_load_env()

SERVER = os.environ.get("RACING_SERVER", "http://127.0.0.1:8011")
ENDPOINT = SERVER + "/api/competitor/record"
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

# 오늘 경쟁 AI 추천을 여기에 입력(경주명 | 조합들). 비워두면 파일 인자 사용.
COMPETITOR_TABLE = """
"""


# ─────────────────────────────────────────
# 텍스트 파싱 (기존 기능 그대로 유지)
# ─────────────────────────────────────────
def parse(text):
    rows = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or "|" not in ln:
            continue
        left, right = ln.split("|", 1)
        rk = left.strip()
        combos = _combos_from_text(right)
        if rk and combos:
            rows.append((rk, combos))
    return rows


def _combos_from_text(right):
    """'4+10 7+10 1+4' 또는 '4-10, 7-10' → [[4,10],[7,10],[1,4]]"""
    combos = []
    for tok in re.split(r"[,\s]+", right.strip()):
        nums = [int(x) for x in re.split(r"[+\-]", tok) if x.strip().isdigit()]
        if len(nums) >= 2:
            combos.append(nums)
    return combos


# ─────────────────────────────────────────
# 사진 인식 (신규) — Claude Vision 으로 '번호' 칸 조합 추출
# ─────────────────────────────────────────
def _media_type(path):
    ext = os.path.splitext(path)[1].lower()
    return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".gif": "image/gif", ".webp": "image/webp"}.get(ext, "image/jpeg")


def combos_from_image(path):
    """사진 파일 → [[번호,번호], ...] 조합 리스트. Claude Vision 사용."""
    try:
        import anthropic
    except Exception:
        raise RuntimeError(
            "anthropic 라이브러리가 없습니다. 서버 폴더에서 'pip install anthropic' 후 다시 실행하세요.")
    key = (os.environ.get("ANTHROPIC_API_KEY", "") or "").strip()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY 가 없습니다(.env 확인). 서버가 쓰는 그 키가 필요합니다.")
    if not os.path.isfile(path):
        raise RuntimeError("사진 파일을 찾을 수 없습니다: %s" % path)

    with open(path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("ascii")

    prompt = (
        "이 사진은 경마(또는 경륜/경정) 복승 '구매표/추천표' 캡쳐입니다.\n"
        "표의 컬럼은 보통 [번호, 배당, 구매장수, 금액] 입니다.\n"
        "각 행의 '번호' 칸에는 복승 조합이 '4-10' 또는 '4+10' 처럼 두 개의 말번호로 적혀 있습니다.\n"
        "'번호' 칸만 위에서 아래 순서대로 모두 읽어서, 각 조합을 [작은번호, 큰번호] 형태의 배열로 만드세요.\n"
        "배당·구매장수·금액 숫자는 절대 조합으로 넣지 마세요(번호 칸만).\n"
        "결과는 반드시 아래 JSON 형식으로만 답하세요(설명 문장 금지):\n"
        '{"combos": [[4,10],[7,10]]}\n'
        "번호 칸을 못 읽으면 {\"combos\": []} 로 답하세요."
    )
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image", "source": {"type": "base64",
                                         "media_type": _media_type(path), "data": data}},
        ]}],
    )
    text = ""
    for b in getattr(msg, "content", []) or []:
        if getattr(b, "type", None) == "text":
            text += b.text
    text = text.strip()
    # 코드블록/여분 텍스트 방어: 본문에서 첫 JSON 객체만 추출
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError("AI 응답에서 조합을 읽지 못했습니다. 응답: %s" % (text[:200] or "(빈 응답)"))
    obj = json.loads(m.group(0))
    combos = []
    for c in (obj.get("combos") or []):
        nums = [int(x) for x in c if str(x).strip().lstrip("-").isdigit()]
        nums = [n for n in nums if n >= 1]
        if len(nums) >= 2:
            combos.append(sorted(nums[:2]) if len(nums) == 2 else nums)
    return combos


# ─────────────────────────────────────────
# 서버 전송 (기존 기능 그대로 유지)
# ─────────────────────────────────────────
def post(rk, combos):
    data = json.dumps({"raceKey": rk, "picks": combos}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(ENDPOINT, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _mark(v):
    return "🎯적중" if v is True else ("미적중" if v is False else "결과대기")


def _send_one(rk, combos):
    """한 경주 등록 + 결과 출력. 성공 True."""
    try:
        r = post(rk, combos)
        rec = r.get("record") or {}
        an = rec.get("analysis") or {}
        comp = rec.get("competitor") or {}
        ch, oh = an.get("competitor_hit"), an.get("our_hit")
        print("  ✅ %-14s" % rk)
        print("      저쪽: %s (%s)" % (comp.get("picks"), comp.get("pattern")))
        print("      우리: %s (축 %s)" % (rec.get("ours", {}).get("picks"), rec.get("ours", {}).get("axis")))
        print("      저쪽 %s / 우리 %s · %s" % (_mark(ch), _mark(oh), an.get("diff")))
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("  ❌ %-14s 서버에 경쟁사 등록 기능(/api/competitor/record)이 없습니다(404)." % rk)
            print("      → app.py 에 경쟁 AI 벤치마킹 백엔드가 빠진 상태입니다. 복원이 필요합니다.")
        else:
            print("  ❌ %-14s 서버 오류 %s: %s" % (rk, e.code, e))
        return False
    except urllib.error.URLError as e:
        print("  ❌ %-14s 서버 연결 실패: %s" % (rk, e))
        if "Connection refused" in str(e):
            print("\n⚠ 서버가 안 떠 있습니다. 먼저 'py app.py' 실행 후 재시도하세요.")
        return False
    except Exception as e:
        print("  ❌ %-14s 실패: %s" % (rk, e))
        return False


# ─────────────────────────────────────────
# 인자 파싱
# ─────────────────────────────────────────
def _get_opt(args, name):
    """--name value  또는  --name=value 를 찾아 값 반환(없으면 None)."""
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


def _run_image(img_path, race):
    print("=" * 60)
    print("📷 사진 인식 → 경쟁 AI 추천 등록")
    print("   사진: %s" % img_path)
    print("   경주: %s" % race)
    print("=" * 60)
    if not race:
        print("⚠ --race 가 필요합니다. 예) --img 사진.jpg --race \"소노다 6경주\"")
        print("  (사진에는 조합만 있고 경주명이 없어서, 어느 경주인지 알려주셔야 해요.)")
        return
    try:
        combos = combos_from_image(img_path)
    except Exception as e:
        print("❌ 사진 인식 실패: %s" % e)
        return
    if not combos:
        print("⚠ 사진에서 조합을 읽지 못했습니다.")
        print("  · 표의 '번호' 칸(예 4-10)이 선명하게 나오도록 다시 캡쳐해 주세요.")
        return
    pretty = "  ".join("%d+%d" % (c[0], c[1]) if len(c) == 2 else "+".join(map(str, c)) for c in combos)
    print("🔎 읽은 조합(%d개): %s" % (len(combos), pretty))
    print("-" * 60)
    ok = _send_one(race, combos)
    print("=" * 60)
    if ok:
        print("완료: 1경주 저장 · 누적통계는 GET /api/competitor/stats 또는 분석기 '📊 경쟁 분석' 확인")
    print("=" * 60)


def _run_text(text):
    rows = parse(text)
    if not rows:
        print("=" * 60)
        print("⚠ 입력된 경쟁 추천이 없습니다.")
        print("  ① 텍스트: 이 스크립트의 COMPETITOR_TABLE 에 아래처럼 넣고 다시 실행")
        print("       소노다 6경주 | 4+10 7+10 1+4")
        print("     또는 파일:  py tools\\register_competitor.py comp.txt")
        print("  ② 사진:   py tools\\register_competitor.py --img 사진.jpg --race \"소노다 6경주\"")
        print("=" * 60)
        return
    print("=" * 60)
    print("경쟁 AI 추천 입력·비교 → %s  (경주 %d개)" % (ENDPOINT, len(rows)))
    print("=" * 60)
    ok = 0
    for rk, combos in rows:
        if _send_one(rk, combos):
            ok += 1
        else:
            # 서버 미기동이면 더 진행 무의미
            pass
    print("=" * 60)
    print("완료: %d경주 저장 · 누적통계는 GET /api/competitor/stats 또는 분석기 '📊 경쟁 분석' 확인" % ok)
    print("=" * 60)


def main():
    args = sys.argv[1:]
    img = _get_opt(args, "--img")
    race = _get_opt(args, "--race")
    if img:                       # ── 사진 모드(신규)
        _run_image(img, race)
        return
    # ── 텍스트 모드(기존): 파일 인자 or COMPETITOR_TABLE
    positional = [a for a in args if not a.startswith("--")]
    if positional and os.path.isfile(positional[0]):
        text = open(positional[0], encoding="utf-8").read()
        print("파일 로드:", positional[0])
    else:
        text = COMPETITOR_TABLE
    _run_text(text)


if __name__ == "__main__":
    main()
