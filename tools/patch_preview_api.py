# -*- coding: utf-8 -*-
"""app.py 에 회원용 **예상문 조회 API** 를 붙인다 (--apply 없으면 아무것도 안 한다).

🟢 완전 읽기 전용이다. 추천·판정·수집 경로에 한 줄도 닿지 않는다.
🔴 로직을 두 곳에 두지 않는다 — `tools/build_preview.py` 를 **그대로 import** 한다.
   (app.py 에 문장 생성 규칙을 복사하면 반드시 갈린다 — 이 프로젝트가 여러 번 겪은 유형이다)
⚠ import 실패해도 서버는 살아야 한다 → try/except 로 감싸고 `_PREVIEW=None` 으로 둔다.
⚠ 실패 시 API 는 503 을 돌려주고 **다른 경로에 영향을 주지 않는다.**
🔧 되돌리기: PREVIEW_API_ENABLED = False (또는 추가 블록 삭제)
"""
import io, os, sys, ast

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app.py")

BLOCK = '''
# 🟢 [2026-08-28 대표 지시] 회원용 **예상문** 조회 API — 완전 읽기 전용
#   대표: 「1번말은 선행형으로 직전 경주 아쉽게도 3착… 이런 디테일한 예상이 필요하다」
#   확인 결과 일본 경마·경륜에는 회원이 읽을 예상문이 **아예 없었다**
#   (race_summary·analysis 는 한국 PDF 전용이고 summary 는 기술 요약이다).
#   🔴 문장 규칙은 `tools/build_preview.py` 한 곳에만 둔다 — 여기 복사하지 않는다.
#   🔴 저장된 값에서만 문장을 만든다(환각 금지) · LLM 미사용 · 근거를 함께 돌려준다.
#   ⚠ 추천·판정·수집에 개입하지 않는다. 읽어서 글만 만든다.
PREVIEW_API_ENABLED = True
try:
    _pv_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
    if _pv_dir not in sys.path:
        sys.path.insert(0, _pv_dir)
    import build_preview as _PREVIEW
except Exception as _pv_e:                      # 모듈이 없어도 서버는 살아야 한다
    _PREVIEW = None
    print("[예상문] 모듈 로드 실패(무시):", str(_pv_e)[:100])


@app.route("/api/race/preview", methods=["GET"])
def race_preview_api():
    """?raceKey=... → 회원용 예상문. 완전 읽기 전용."""
    rk = (request.args.get("raceKey") or "").strip()
    if not rk:
        return jsonify({"ok": False, "error": "raceKey 필요"}), 400
    if not PREVIEW_API_ENABLED or _PREVIEW is None:
        return jsonify({"ok": False, "error": "예상문 기능이 꺼져 있습니다"}), 503
    try:
        _p = _analysis_log_path(rk)
        _path = _p[0] if isinstance(_p, (tuple, list)) else _p
        if not _path or not os.path.exists(_path):
            return jsonify({"ok": False, "error": "분석 로그 없음", "raceKey": rk}), 404
        _r = _PREVIEW.build(_path)
        if not _r or not _r.get("lines"):
            return jsonify({"ok": False, "error": "예상문 생성 불가(재료 부족)",
                            "raceKey": rk}), 200
        _member = (request.args.get("view") or "") != "admin"
        return jsonify({
            "ok": True, "raceKey": _r.get("raceKey"), "sport": _r.get("sport"),
            "head": _r["lines"][0][0],
            "lines": [t for t, _ in _r["lines"][1:]],
            # 🔴 근거는 대표 확인용이다 — 회원 화면에는 보내지 않는다(view=admin 일 때만)
            "basis": None if _member else [w for _, w in _r["lines"][1:]],
            "note": "저장된 값에서만 생성 · 추측 없음",
        })
    except Exception as _e:
        print("[예상문] 생성 실패(무시):", str(_e)[:120])
        return jsonify({"ok": False, "error": "생성 실패"}), 200

'''

ANCHOR = '@app.route("/api/race/card-timeline", methods=["GET"])'


def apply(dry=True):
    src = io.open(APP, encoding="utf-8").read()
    if "PREVIEW_API_ENABLED" in src:
        print("  ⚠ 이미 배선돼 있다 — 아무것도 하지 않는다"); return 0
    if src.count(ANCHOR) != 1:
        print("  🔴 앵커를 못 찾았다(또는 여럿) — 중단"); return 1
    out = src.replace(ANCHOR, BLOCK.lstrip("\n") + "\n" + ANCHOR, 1)
    try:
        ast.parse(out)
    except SyntaxError as e:
        print("  🔴 문법 오류 — 적용하지 않는다:", e); return 1
    print("  🟢 문법 OK · 추가 %d줄 · 삭제 0줄" % (out.count("\n") - src.count("\n")))
    if dry:
        print("  ⚠ --dry (기본) — 파일을 쓰지 않았다. 적용하려면 --apply"); return 0
    io.open(APP + ".bak_preview", "w", encoding="utf-8", newline="").write(src)
    io.open(APP, "w", encoding="utf-8", newline="").write(out)
    print("  🟢 적용 완료 · 원본 백업 app.py.bak_preview"); return 0


if __name__ == "__main__":
    sys.exit(apply(dry="--apply" not in sys.argv))
