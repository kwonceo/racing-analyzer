# -*- coding: utf-8 -*-
"""CLAUDE.md 자동 아카이브 — 「최근 1일」만 남기고 나머지 일지를 월별 보관소로 옮긴다.

🔴 왜 도구인가: 이 저장소는 **수동 규칙이 안 지켜진다는 것이 세 번 실증**됐다.
   ① 「모든 보고는 REPORT.md 에」 규칙을 만든 직후 두 번 어김(2026-08-12)
   ② docs/규칙목록.md 별도 파일로 뺀 뒤 17일 stale
   ③ 2026-08-09 에 「절반으로 줄였다」고 하고 4주 만에 520k 로 복귀
   ⇒ 사람이 기억해서 하는 정리는 반드시 밀린다.

사용:
    python tools/archive_claudemd.py            # --dry (기본) · 무엇을 옮길지만 본다
    python tools/archive_claudemd.py --apply    # 실제 이동
    python tools/archive_claudemd.py --check    # 글자수만 (훅용 · 종료코드 1 이면 초과)

⚠ 삭제하지 않는다. 옮기기만 한다. docs/archive/ 는 읽기 전용 보관소다.
⚠ 글자수는 **CRLF 포함**(큰 쪽)으로 센다 — 작은 쪽으로 세면 사본 PC 에서 한도를 넘고도 통과한다.
"""
import io, json, os, re, sys, time

ROOT   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLAUDE = os.path.join(ROOT, "CLAUDE.md")
ARCH   = os.path.join(ROOT, "docs", "archive")
LIMIT  = 100000
CR     = "\r\n"

SEC_LOG   = "# ═══ 최근 일지"
SEC_OPEN  = "# ═══ ⏳ 열린 항목"
DATE      = re.compile(r"\[?(2026-\d\d-\d\d)")


def read(p):
    return io.open(p, encoding="utf-8", newline="").read()


def size_of(t):
    """CRLF 포함 글자수. LF 파일이면 줄 수만큼 더해 큰 쪽으로 센다."""
    lf = t.count("\n") - t.count("\r\n")
    return len(t) + lf


def split_sections(lines):
    """'# ═══ …' 구분자로 섹션을 나눈다 → [(제목, 시작줄, 끝줄)]"""
    marks = [i for i, l in enumerate(lines) if l.startswith("# ═══")]
    out = []
    for k, i in enumerate(marks):
        e = marks[k + 1] if k + 1 < len(marks) else len(lines)
        out.append((lines[i], i, e))
    return out


def log_blocks(lines, a, b):
    """일지 섹션 안의 h1 블록들 → [(날짜, 시작, 끝, 제목)]"""
    heads = [i for i in range(a + 1, b) if re.match(r"^# ", lines[i])]
    out = []
    for k, i in enumerate(heads):
        e = heads[k + 1] if k + 1 < len(heads) else b
        m = DATE.search(lines[i])
        out.append((m.group(1) if m else None, i, e, lines[i]))
    return out


def toc(lines_of_file):
    """월별 파일 목차 10줄 — h1 제목을 크기순으로."""
    heads = [i for i, l in enumerate(lines_of_file) if re.match(r"^# ", l) and "═══" not in l]
    rows = []
    for k, i in enumerate(heads):
        e = heads[k + 1] if k + 1 < len(heads) else len(lines_of_file)
        rows.append((sum(len(x) + 2 for x in lines_of_file[i:e]), re.sub(r"^#+\s*", "", lines_of_file[i])))
    rows.sort(reverse=True)
    return ["- {:,}자 — {}".format(n, t[:88]) for n, t in rows[:10]]


def refresh_status(lines, newsize):
    """📌 오늘 상태 블록 갱신 — 🔴 도구가 채울 수 있는 것만 넣는다(못 채우는 줄은 먼저 stale 된다)."""
    n_open = n_appr = 0
    secs = split_sections(lines)
    for t, a, b in secs:
        if t.startswith(SEC_OPEN):
            body = CR.join(lines[a:b])
            n_open = len([1 for l in lines[a:b] if re.match(r"^#{2,3} ", l)])
            n_appr = len(re.findall(r"승인\s*대기|승인\s*사항", body))
    now = time.strftime("%Y-%m-%d %H:%M")
    for i, l in enumerate(lines[:20]):
        if l.startswith("- **CLAUDE.md**"):
            lines[i] = "- **CLAUDE.md** {:,}자 / 한도 {:,}자 ({:.0f}%) · 측정 {}".format(
                newsize, LIMIT, newsize / LIMIT * 100, now)
        elif l.startswith("- **날짜**"):
            lines[i] = "- **날짜** " + time.strftime("%Y-%m-%d")
        elif l.startswith("- **⏳ 열린 항목**"):
            lines[i] = "- **⏳ 열린 항목** {}건  (150줄 넘으면 그날 정리한다)".format(n_open)
        elif l.startswith("- **⏳ 승인 대기**"):
            lines[i] = "- **⏳ 승인 대기** {}건".format(n_appr)
    return lines


def main():
    apply_ = "--apply" in sys.argv
    check  = "--check" in sys.argv
    hook   = "--hook" in sys.argv
    txt    = read(CLAUDE)
    lines  = txt.split(CR) if CR in txt else txt.split("\n")
    cur    = size_of(txt)

    if hook:
        # 🔴 SessionStart 훅용. 한도를 넘을 때만 출력한다(조용한 성공이 기본).
        if cur > LIMIT:
            print(json.dumps({"systemMessage":
                "🔴 CLAUDE.md {:,}자 / 한도 {:,}자 ({:.0f}%) — 초과. "
                "python tools/archive_claudemd.py --apply 로 오래된 일지를 아카이브하십시오.".format(
                    cur, LIMIT, cur / LIMIT * 100)}, ensure_ascii=False))
        return

    if check:
        over = cur > LIMIT
        print("CLAUDE.md {:,}자 / 한도 {:,}자 ({:.0f}%){}".format(
            cur, LIMIT, cur / LIMIT * 100, "  🔴 초과 — python tools/archive_claudemd.py --apply" if over else "  🟢"))
        sys.exit(1 if over else 0)

    secs = split_sections(lines)
    tgt  = [(t, a, b) for t, a, b in secs if t.startswith(SEC_LOG)]
    if not tgt:
        print("🔴 「{} …」 섹션이 없다. 구조가 바뀌었는지 확인할 것.".format(SEC_LOG)); sys.exit(2)
    _, a, b = tgt[0]
    blocks = log_blocks(lines, a, b)
    today  = time.strftime("%Y-%m-%d")
    move   = [x for x in blocks if x[0] and x[0] != today]
    keep   = [x for x in blocks if not (x[0] and x[0] != today)]

    print("CLAUDE.md {:,}자 / 한도 {:,}자 ({:.0f}%)".format(cur, LIMIT, cur / LIMIT * 100))
    print("일지 블록 {}개 · 오늘({}) {}개 유지 · 이동 대상 {}개".format(len(blocks), today, len(keep), len(move)))
    if not move:
        print("🟢 옮길 것이 없다."); return
    for d, i, e, t in move:
        print("  → {} : {}".format(d, re.sub(r"^#+\s*", "", t)[:76]))
    if not apply_:
        print("\n⚠ --dry (기본). 실제로 옮기려면 --apply"); return

    os.makedirs(ARCH, exist_ok=True)
    drop = set()
    for d, i, e, t in move:
        m  = d[:7]
        fp = os.path.join(ARCH, "CLAUDE_일지_{}.md".format(m))
        body = CR.join(lines[i:e])
        if os.path.exists(fp):
            old = read(fp)
            ol  = old.split(CR)
            hdr_end = next((k for k, l in enumerate(ol) if l.strip() == "---"), 0)
            new = CR.join(ol[hdr_end + 1:]) + CR + body
        else:
            new = body
        head = ["# CLAUDE.md 일지 아카이브 · {}".format(m), "",
                "⚠ **읽기 전용 보관소.** 살아 있는 규칙은 여기 두지 않는다.",
                "⚠ 원문 그대로다 — 요약하지 않았다(요약은 재구현이고, 결론이 뒤집힌 이력이 있다).", "",
                "갱신 {}".format(time.strftime("%Y-%m-%d %H:%M")), "", "## 목차 (글자수 상위 10)", ""]
        head += toc(new.split(CR)) + ["", "---", ""]
        io.open(fp, "w", encoding="utf-8", newline="").write(CR.join(head) + new)
        drop |= set(range(i, e))
        print("  ✅ {} 로 이동".format(os.path.basename(fp)))

    out = [l for k, l in enumerate(lines) if k not in drop]
    out = refresh_status(out, size_of(CR.join(out)))
    io.open(CLAUDE, "w", encoding="utf-8", newline="").write(CR.join(out))
    fin = size_of(read(CLAUDE))
    print("\nCLAUDE.md {:,} → {:,}자 ({:.0f}% of {:,})".format(cur, fin, fin / LIMIT * 100, LIMIT))
    if fin > LIMIT:
        print("🔴 아직 한도 초과다. 상시 구획이 커진 것이니 사람이 봐야 한다.")


if __name__ == "__main__":
    main()
