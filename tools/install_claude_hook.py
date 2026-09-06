# -*- coding: utf-8 -*-
"""SessionStart 훅 설치 — CLAUDE.md 가 100,000자를 넘으면 세션 시작에 경고한다.

🔴 왜 PC 마다 따로 설치하나: `.claude/` 는 .gitignore 대상이라 git 으로 전파되지 않는다.
   그래서 자기신고(CLAUDE.md 「📌 오늘 상태」 줄)를 함께 둔다 — 훅이 없는 PC 에서도 숫자는 보인다.
⚠ 이미 settings.json 이 있으면 **덮어쓰지 않는다.** 병합 안내만 출력한다.
"""
import io, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR  = os.path.join(ROOT, ".claude")
FP   = os.path.join(DIR, "settings.json")
CMD  = 'cd "${CLAUDE_PROJECT_DIR:-.}" && python tools/archive_claudemd.py --hook || true'
HOOK = {"hooks": {"SessionStart": [{"hooks": [
    {"type": "command", "command": CMD, "timeout": 20,
     "statusMessage": "CLAUDE.md 크기 확인"}]}]}}


def main():
    os.makedirs(DIR, exist_ok=True)
    if os.path.exists(FP):
        try:
            cur = json.load(io.open(FP, encoding="utf-8"))
        except Exception as e:
            print("🔴 기존 settings.json 을 읽지 못했다(%s). 손대지 않는다." % e); return 2
        got = json.dumps(cur.get("hooks", {}).get("SessionStart", ""), ensure_ascii=False)
        if "archive_claudemd" in got:
            print("🟢 이미 설치돼 있다."); return 0
        print("⚠ settings.json 이 이미 있다. **덮어쓰지 않는다.**")
        print("  hooks.SessionStart 에 아래 command 를 손으로 추가할 것:")
        print("  " + CMD); return 1
    io.open(FP, "w", encoding="utf-8").write(json.dumps(HOOK, ensure_ascii=False, indent=2))
    print("🟢 설치: %s" % FP)
    return 0


if __name__ == "__main__":
    rc = main()
    print()
    import subprocess   # os.system 은 Windows cmd 의 이중 따옴표에서 깨진다
    subprocess.run([sys.executable, os.path.join(ROOT, 'tools', 'archive_claudemd.py'), '--check'])
    sys.exit(rc)
