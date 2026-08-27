# -*- coding: utf-8 -*-
"""[회귀] schema contract test — 파서 키가 저장행에서 조용히 사라지는 것을 막는다.

🔴 이 테스트가 실패하면 **파서에 필드를 추가하고 저장행에 안 넣었다**는 뜻이다.
  같은 유형의 소실이 네 번 반복됐다(distance/surface/trackCond · corners · kimarite · declaredStyle).
⚠ 계약은 `tools/schema_contract.py` 에 있다. 폐기로 분류하려면 **사유를 반드시 적는다.**
"""
import importlib.util
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_p = os.path.join(BASE, "tools", "schema_contract.py")
_s = importlib.util.spec_from_file_location("schema_contract", _p)
_m = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_m)

bad = _m.check()
if bad:
    print("[schema contract] 🔴 위반 %d건" % len(bad))
    for b in bad:
        print("   " + b)
    sys.exit(1)
_pend = _m.pending()
print("[schema contract] 🟢 위반 없음 · ⚠ 알고 있는 미배선 %d건: %s"
      % (len(_pend), ", ".join(sorted(_pend))))
sys.exit(0)
