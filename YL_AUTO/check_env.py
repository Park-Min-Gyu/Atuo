# -*- coding: utf-8 -*-
"""연구실 PC 환경 점검:  python check_env.py config_abaqus.json

- Python 패키지(numpy, scipy, matplotlib)
- HyperMesh 2019 hmbatch.exe 위치 (config 경로 -> 시작 메뉴 바로가기 -> 기본 위치)
- Abaqus 실행 명령 위치와 버전 (abaqus information=release)
찾은 경로는 화면에 출력한다. 자동으로 못 찾으면 config 에 직접 적으면 된다.
"""
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import findexe  # noqa: E402

cfg_path = Path(sys.argv[1] if len(sys.argv) > 1 else "config_abaqus.json")
cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
ok = True
print("=" * 70)
print(f" 환경 점검  ({cfg_path})")
print("=" * 70)
print(f"[Python] {sys.version.split()[0]}  ({sys.executable})")
if sys.version_info < (3, 8):
    print("  [NG] Python 3.8 이상이 필요합니다.")
    ok = False
for mod in ("numpy", "scipy", "matplotlib"):
    try:
        m = importlib.import_module(mod)
        print(f"  [OK] {mod} {m.__version__}")
    except Exception:
        print(f"  [NG] {mod} 없음 ->  pip install {mod}")
        ok = ok and mod == "matplotlib"   # matplotlib 은 그래프만 안 나옴

try:
    import gmsh  # noqa: F401
    print(f"  [OK] gmsh {gmsh.__version__} (HyperMesh 실패 시 예비 메셔)")
except Exception:
    print("  [선택] gmsh 없음 ->  pip install gmsh  (HyperMesh 메시 실패 시 자동으로 대신 메시해 줌)")

print("\n[HyperMesh]")
hm_cfg = cfg.get("hypermesh", {})
exe, how = findexe.find_hmbatch(hm_cfg)
if exe:
    print(f"  [OK] hmbatch: {exe}  (찾은 방법: {how})")
else:
    print("  [NG] hmbatch.exe 를 못 찾음. config_abaqus.json 의 hypermesh.exe 에 전체 경로를 적어주세요.")
    print("       보통 위치: C:/Program Files/Altair/2019/hm/bin/win64/hmbatch.exe")
    print("       (.inp 형상만 쓸 거면 HyperMesh 없이도 됩니다)")
tcl = cfg_path.parent / hm_cfg.get("tcl", "hypermesh/YL_V5_batch.tcl")
print(f"  [{'OK' if tcl.exists() else 'NG'}] YL_V5 tcl: {tcl}")
ok = ok and tcl.exists()

print("\n[Abaqus]")
abq, how = findexe.find_abaqus(cfg.get("abaqus", {}))
if not abq:
    print("  [NG] Abaqus 명령을 못 찾음. config_abaqus.json 의 abaqus.cmd 에 전체 경로를 적어주세요.")
    print("       보통 위치: C:/SIMULIA/Commands/abaqus.bat  또는  abq2021.bat")
    ok = False
else:
    q = os.path.normpath(abq) if os.name == "nt" else abq
    qe = f'"{q}"' if " " in q else q
    print(f"  명령: {q}  (찾은 방법: {how})")
    try:
        r = subprocess.run(f"{qe} information=release", shell=True, capture_output=True, text=True, timeout=180)
        txt = (r.stdout or "") + (r.stderr or "")
        line = next((l.strip() for l in txt.splitlines() if "Abaqus" in l and ("20" in l or "6." in l)),
                    txt.strip()[:120])
        good = r.returncode == 0 and "Abaqus" in txt
        print(f"  [{'OK' if good else 'NG'}] {line}")
        if not good:
            print("       라이선스 서버 연결 또는 경로를 확인하세요.")
        ok = ok and good
    except Exception as e:
        print(f"  [NG] Abaqus 실행 실패: {e}")
        ok = False

models = sorted(Path(cfg_path.parent).glob(cfg.get("models_glob", "models/*").replace("\\", "/")))
models = [m for m in models if m.is_file()]
print(f"\n[형상] models_glob = {cfg.get('models_glob')}  ->  {len(models)} 개")
for m in models[:20]:
    print(f"   - {m.name}")
if not models and not cfg.get("models"):
    print("  [주의] 해석할 형상이 없습니다. models 폴더에 .stp 등을 넣으세요.")

print("\n환경 점검 결과:", "통과" if ok else "확인 필요 (위의 [NG] 항목)")
sys.exit(0 if ok else 1)
