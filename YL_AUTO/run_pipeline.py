# -*- coding: utf-8 -*-
"""
run_pipeline.py : 모델(CAD) + 물성 -> HyperMesh 자동 메시 -> INP -> Abaqus Job -> ODB -> 정특성 데이터셋

  python run_pipeline.py config.json                # 전체 (mesh -> build -> run -> extract -> report)
  python run_pipeline.py config.json --stage build  # INP 까지만 만들기 (Abaqus 안 돌림)
  python run_pipeline.py config.json --stage run,extract,report --only M01
  python run_pipeline.py config.json --force        # 이미 끝난 단계도 다시

단계
  mesh    : CAD -> hmbatch -tcl YL_V5_batch.tcl -> <tag>/mesh/<name>_hm.inp  (입력이 .inp 면 생략)
  build   : 메시 검사/보정, 축 -> +Y, INNER/OUTER 자동 선택, LOAD_RP(0,0,0),
            메시 전용 INP + (스웨징 x 방향) Job INP 생성
  run     : Abaqus(또는 검증용 CalculiX) 실행 -> .odb
  extract : abaqus python extract_odb.py -> <job>_RP.csv
  report  : 강성 계산, 자동 QC, dataset/summary.csv, curves.csv, qc_report.md, 그래프
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import findexe, inp_check, inp_mesh, inp_writer, ogden, results, runners, shape_general, yl_hex, yl_v10  # noqa: E402

STAGES = ["mesh", "build", "run", "extract", "report"]
CAD_EXT = {".stp", ".step", ".igs", ".iges", ".x_t", ".x_b", ".catpart", ".prt", ".sat", ".hm"}


LOG_FILE = None
_LOG_LOCK = threading.Lock()      # [v27.5] 메시(본 스레드)와 Abaqus 실행(작업 스레드)이 동시에 로그를 씀


def log(msg):
    line = time.strftime("[%H:%M:%S] ") + msg
    with _LOG_LOCK:
        print(line, flush=True)
        if LOG_FILE is not None:
            try:
                with open(LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass


# [v27.5] auto_out/progress.txt : 모델마다 지금 어느 단계인지 한눈에 (메시 중 / 해석 대기 / Abaqus 실행 중 / 완료)
PROGRESS: dict = {}
PROGRESS_FILE = None
_PROG_LOCK = threading.Lock()


def progress(tag, **kw):
    with _PROG_LOCK:
        d = PROGRESS.setdefault(tag, {"mesh": "-", "run": "-"})
        d.update({k: str(v) for k, v in kw.items()})
        d["t"] = time.strftime("%H:%M:%S")
        if PROGRESS_FILE is None:
            return
        w = max([len(t) for t in PROGRESS] + [10])
        lines = [f"YL_AUTO 진행 상황 ({time.strftime('%Y-%m-%d %H:%M:%S')} 갱신, 실행이 끝나면 맨 아래에 '끝' 표시)", "",
                 f"{'모델':<{w}}  | 메시                          | 해석"]
        for t, v in PROGRESS.items():
            lines.append(f"{t:<{w}}  | {v['mesh']:<29} | {v['run']}   ({v['t']})")
        try:
            PROGRESS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass


def progress_end(msg: str):
    if PROGRESS_FILE is None:
        return
    with _PROG_LOCK:
        try:
            with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
                f.write(f"\n끝 ({time.strftime('%Y-%m-%d %H:%M:%S')}) : {msg}\n")
        except OSError:
            pass


def check_models(cfg):
    """해석할 모델이 0개면 어디를 찾았는지, 다른 폴더에 CAD 가 있는지 알려주고 멈춘다."""
    ms = model_list(cfg)
    if ms:
        log(f"모델 {len(ms)}개: " + ", ".join(Path(m['file']).name for m in ms))
        return ms
    where = resolve(cfg, cfg.get("models_glob") or "models/*")
    log("=" * 60)
    log("[오류] 해석할 모델이 0개입니다. 아무것도 실행하지 않고 종료합니다.")
    log(f"  찾은 위치: {where}")
    log(f"  지원 확장자: {', '.join(sorted(CAD_EXT | {'.inp'}))}")
    base = cfg["_base"]
    others = [q for q in base.rglob("*") if q.is_file() and q.suffix.lower() in CAD_EXT
              and "auto_out" not in q.parts and "smoke_out" not in q.parts]
    if others:
        log("  다른 폴더에서 CAD 파일을 찾았습니다. 아래 파일을 models 폴더로 옮기세요:")
        for q in others[:10]:
            log(f"    {q}  ->  {base / 'models' / q.name}")
    log("=" * 60)
    sys.exit(2)


def safe_name(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_]", "_", s)
    return s if s[:1].isalpha() else "M" + s


def load_config(path: Path) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg["_base"] = path.resolve().parent
    return cfg


def resolve(cfg, p) -> Path:
    # Path.resolve() 는 Windows 에서 '*' 같은 와일드카드가 있으면 WinError 123 을 내므로 abspath 사용
    p = Path(p)
    return Path(os.path.abspath(str(p if p.is_absolute() else cfg["_base"] / p)))


def mesh_engine(cfg) -> str:
    """[v25] 메시 엔진. config 에 hypermesh.engine 이 없으면 헥사(hex)가 기본 (Tetra 는 'v10' 이라고 적었을 때만)."""
    return str(cfg.get("hypermesh", {}).get("engine", "hex")).lower()


def check_hex_auto(cfg):
    """[v27.1] 자동 헥사가 꺼져 있으면 시작할 때 크게 알림.
    v26 config 의 기본값(hex_auto=false, 실험 기능 설명)이 그대로 남아 있으면 v27 기본값(true)으로 실행한다."""
    hm = cfg.setdefault("hypermesh", {})
    if mesh_engine(cfg) != "hex":
        return
    if hm.get("hex_auto", True) is False and "[v26 실험]" in str(hm.get("_comment_hex_auto", "")):
        hm["hex_auto"] = True
        log("[알림] config 에 v26 의 기본값 hypermesh.hex_auto = false 가 남아 있어 v27 기본값(true)으로 실행합니다. "
            "(SolidMap 이 안 되는 형상은 자동 헥사로 메시. 정말 끄려면 _comment_hex_auto 줄을 지우고 false 로 두세요)")
    if hm.get("hex_auto", True):
        log("메시: 헥사 전용 - YL_HEX(SolidMap) 를 먼저 시도하고, 안 되면 자동 헥사(블록 분할 불필요)로 메시합니다")
    else:
        log("[주의] 자동 헥사가 꺼져 있습니다 (config: hypermesh.hex_auto = false). SolidMap 이 안 되는 형상(블록으로 안 나눈 "
            "부싱)은 메시 실패로 끝나고 해석을 하지 않습니다. 켜려면 hypermesh.hex_auto 를 true 로 바꾸세요")


def model_list(cfg) -> list[dict]:
    models = list(cfg.get("models", []))
    pat = cfg.get("models_glob")
    if pat:
        for f in sorted(glob.glob(str(resolve(cfg, pat)))):
            if Path(f).suffix.lower() in CAD_EXT | {".inp"}:
                models.append({"name": Path(f).stem, "file": f})
    seen = set()
    out = []
    for m in models:
        m = dict(m)
        m["name"] = safe_name(m["name"])
        if m["name"] in seen:
            raise ValueError(f"모델 이름 중복: {m['name']}")
        seen.add(m["name"])
        m["file"] = str(resolve(cfg, m["file"]))
        out.append(m)
    return out


def jobs_for(cfg, tag):
    L = cfg["load"]
    jobs = []
    for sw in cfg["swaging_mm"]:
        for d in L["directions"]:
            for sg in L.get("signs", [1]):
                amp = float(L["amplitude_mm"][d])
                jobs.append({"job": inp_writer.job_name(tag, float(sw), d, int(sg)), "sw": float(sw), "dir": d,
                             "sign": int(sg), "amp": amp})
    return jobs


# ---------------------------------------------------------------------------
def stage_mesh(cfg, m, es, tag_dir, force):
    return _stage_mesh(cfg, m, es, tag_dir, force)


_FILE_SHA: dict = {}


def _file_sha(p: Path) -> str:
    p = Path(p)
    if p not in _FILE_SHA:
        try:
            _FILE_SHA[p] = hashlib.sha1(p.read_bytes()).hexdigest()[:12]
        except OSError:
            _FILE_SHA[p] = "-"
    return _FILE_SHA[p]


def _hex_signature(cfg, src: Path, es: float, hm: dict) -> str:
    """[v27.5] 헥사 메시 결과를 정하는 것들의 지문 : CAD 파일(크기, 수정 시각) + 메셔 코드 + 설정 + 요소 크기.
    같으면 결과도 같으므로(자동 헥사는 매번 같은 결과) 지난번에 실패한 모델을 다시 시도하지 않는다."""
    try:
        fs = src.stat()
        cad = f"{src.name}|{fs.st_size}|{int(fs.st_mtime)}"
    except OSError:
        cad = src.name
    parts = [cad, _file_sha(HERE / "core" / "grid_hex.py"), _file_sha(HERE / "core" / "runners.py"),
             _file_sha(resolve(cfg, hm.get("hex_tcl", "hypermesh/YL_HEX/YL_HEX_V6_PURE_2mm.tcl"))),
             json.dumps(cfg.get("hex_grid", {}), sort_keys=True), str(float(es)), str(bool(hm.get("exe"))),
             str(bool(hm.get("hex_auto", True)))]
    return hashlib.sha1("||".join(parts).encode()).hexdigest()[:16]


def gridhex_lost_cells(mesh_dir: Path) -> int:
    """[v27.7] 저장된 자동 헥사 메시가 형상 조각을 뺀 채 만들어졌는지 (v27.6 이전 : 얇은 막 조각을 지우던 문제).
    mesh/gridhex_console.log 의 마지막 결과에서 빠진 칸 수를 읽는다. 몇 칸짜리 잡음이면 0."""
    f = Path(mesh_dir) / "gridhex_console.log"
    try:
        lines = [l for l in f.read_text(encoding="utf-8", errors="ignore").splitlines() if "GRIDHEX_RESULT " in l]
        res = json.loads(lines[-1].split("GRIDHEX_RESULT ", 1)[1]) if lines else {}
    except (OSError, ValueError):
        return 0
    n = int(res.get("cells_dropped_islands") or 0)
    return n if n > max(8, 0.001 * int(res.get("n_hex") or 0)) else 0


def mesh_ready(cfg, m, tag_dir: Path) -> bool:
    """[v27.5] 메시가 이미 있어서 바로 해석으로 갈 수 있는 모델인지 (처리 순서를 정하는 데만 씀)"""
    if Path(m["file"]).suffix.lower() == ".inp":
        return True
    mesh_dir = tag_dir / "mesh"
    if not (mesh_dir / f"{m['name']}_hm.inp").exists():
        return False
    st = runners.read_kv(mesh_dir / "hm_status.txt")
    if st.get("STATUS") != "OK":
        return False
    if st.get("ALGO") == "GRID_HEX" and gridhex_lost_cells(mesh_dir):
        return False
    return mesh_engine(cfg) != "hex" or str(st.get("ALGO", "")).startswith(("YL_HEX", "GRID_HEX"))


def _stage_mesh(cfg, m, es, tag_dir, force):
    src = Path(m["file"])
    mesh_dir = tag_dir / "mesh"
    out_inp = mesh_dir / f"{m['name']}_hm.inp"
    if src.suffix.lower() == ".inp":
        mesh_dir.mkdir(parents=True, exist_ok=True)
        if force or not out_inp.exists():
            shutil.copy(src, out_inp)
        return {"STATUS": "OK", "MESSAGE": "input already meshed (.inp) - HyperMesh skipped", "INP_FILE": str(out_inp)}
    if out_inp.exists() and not force:
        st = runners.read_kv(mesh_dir / "hm_status.txt")
        ok, why = runners.tet_count_ok(st, cfg["hypermesh"].get("tet_min_ratio", 0.25))
        # [v15] 2 mm 고정 설정이 바뀌었으면 예전 메시를 쓰지 않고 다시 만든다
        want_fixed = 1 if cfg["mesh"].get("fixed_size") else 0
        is_hex_mesh = str(st.get("ALGO", "")).startswith(("YL_HEX", "GRID_HEX"))
        if st.get("STATUS") == "OK" and ok and not is_hex_mesh and int(st.get("FIXED_SIZE", 0) or 0) != want_fixed:
            ok = False
            log(f"[{m['name']}] 메시 크기 설정(fixed_size={want_fixed})이 이전 메시와 달라 다시 메시합니다")
        eng_now = mesh_engine(cfg)
        want_v10 = eng_now == "v10"
        if st.get("STATUS") == "OK" and ok and eng_now == "hex" and not is_hex_mesh:
            ok = False
            log(f"[{m['name']}] 이전 메시는 헥사가 아님({st.get('ALGO', '?')}) -> 헥사로 다시 메시합니다")
        if eng_now == "hex" and st.get("STATUS") == "OK" and ok and st.get("ALGO") == "GRID_HEX":
            lostc = gridhex_lost_cells(mesh_dir)
            if lostc:
                ok = False
                log(f"[{m['name']}] 이전 자동 헥사 메시는 얇은 부분 {lostc}칸이 빠진 채 만들어졌음 (v27.6 이전 문제) "
                    f"-> 다시 메시합니다")
        if eng_now == "hex" and st.get("STATUS") == "OK" and ok:
            return st
        if st.get("STATUS") == "OK" and ok and want_v10 and not str(st.get("ALGO", "")).startswith(("YL_V10", "GMSH", "ROBUST")):
            ok = False
            log(f"[{m['name']}] 이전 메시는 예전 엔진({st.get('ALGO', '?')})으로 만든 것 -> YL_V10 으로 다시 메시합니다")
        want_gmsh = str(cfg["mesh"].get("mesher", "hypermesh")).lower() == "gmsh"
        if st.get("STATUS") == "OK" and ok and (not want_gmsh or st.get("ALGO") == "GMSH_FALLBACK"
                                               or not runners.gmsh_available()):
            return st
        if st.get("STATUS") == "OK" and why:
            log(f"[{m['name']}] 이전 메시 재사용 안 함 ({why}) -> HyperMesh 다시 실행")
    hm = dict(cfg["hypermesh"])
    hm["fixed_size"] = bool(cfg["mesh"].get("fixed_size"))
    g_cfg = dict(cfg.get("gmsh_fallback", {}))
    g_cfg["fixed_size"] = hm["fixed_size"]
    engine = mesh_engine(cfg)
    if engine == "hex":
        # [v26] 헥사 전용 (순수 헥사 C3D8H 만, Tetra 메셔는 쓰지 않음)
        #   1) YL_HEX_V6 (HyperMesh SolidMap) : 매핑 가능한(블록으로 나눈) 솔리드면 이것으로 끝
        #   2) 안 되면 자동 헥사 (core/grid_hex.py, 격자 + 경계층) : 블록으로 나누지 않은 솔리드도 헥사로
        hs = None
        memo_f = mesh_dir / "hex_fail.json"
        sig = _hex_signature(cfg, src, es, hm)
        if memo_f.exists() and not force:
            try:
                memo = json.loads(memo_f.read_text(encoding="utf-8"))
            except ValueError:
                memo = {}
            if memo.get("sig") == sig:
                log(f"[{m['name']}] 지난번({memo.get('time', '?')})에 같은 CAD 파일, 같은 메셔로 헥사 메시가 실패함 -> "
                    f"다시 시도하지 않음 (같은 결과라 시간만 듦). 다시 하려면 --force 또는 mesh/hex_fail.json 삭제")
                return {"STATUS": "FAIL", "ALGO": "HEX", "MESSAGE": "(지난번과 같은 실패) " + memo.get("message", ""),
                        "N_HEX": 0, "LOG_TAIL": memo.get("log_tail", []), "SKIPPED_SAME": True}
        mesh_dir.mkdir(parents=True, exist_ok=True)
        if hm.get("exe"):
            hs = yl_hex.run_hex(hm, resolve(cfg, hm.get("hex_tcl", "hypermesh/YL_HEX/YL_HEX_V6_PURE_2mm.tcl")), src,
                                mesh_dir, m["name"], es, hm.get("hex_elem_type", "C3D8H"))
            if es != 2.0:
                log(f"[{m['name']}] 참고: YL_HEX 스크립트는 요소크기 2.0 mm 고정입니다 (config 의 {es} mm 는 쓰이지 않음)")
            if hs.get("STATUS") == "OK":
                log(f"[{m['name']}] 헥사 메시 성공 (YL_HEX) : Hex {hs.get('N_HEX')}개, 솔리드 {hs.get('N_SOLIDS')}개 "
                    f"({hs.get('elapsed_s')}s)")
                if memo_f.exists():
                    memo_f.unlink()
                return hs
            log(f"[{m['name']}] YL_HEX(SolidMap) 헥사 안 됨 -> {hs.get('MESSAGE', '')}")
            for l in hs.get("LOG_TAIL", [])[-3:]:
                log(f"[{m['name']}]   hex| {l}")
        else:
            log(f"[{m['name']}] HyperMesh 가 없어 YL_HEX 는 건너뜀")
        if not hm.get("hex_auto", True):
            # [v27] 자동 헥사(단면 쓸기 + 경계층)는 기본으로 켬. 끄려면 config 의 hypermesh.hex_auto = false
            return hs or {"STATUS": "FAIL", "ALGO": "YL_HEX_V6", "MESSAGE": "HyperMesh 없음, hex_auto 꺼짐"}
        log(f"[{m['name']}] 자동 헥사(격자 + 경계층, 블록 분할 불필요, 요소 {es} mm)로 메시합니다")
        gs = runners.run_grid_hex(dict(cfg.get("hex_grid", {})), src, mesh_dir, m["name"], es)
        if gs.get("STATUS") == "OK":
            log(f"[{m['name']}] 헥사 메시 성공 (자동 헥사) : Hex {gs.get('N_HEX')}개, 최소 scaled Jacobian {gs.get('SJ_MIN')}"
                f" (평균 {gs.get('SJ_MEAN')}), CAD 대비 부피 오차 {gs.get('VOLUME_ERR_PCT')} %, {gs.get('elapsed_s')}s"
                f" -> 메시 그림 mesh/{m['name']}_hexmesh.png")
            if gs.get("WARNING"):
                log(f"[{m['name']}] WARNING {gs['WARNING']}")
            log(f"[{m['name']}] HyperMesh 로 헥사 메시 보기: File > Import > Solver Deck (File type = Abaqus) 에서 "
                f"mesh/{m['name']}_hm.inp 또는 run 폴더의 *_mesh.inp 를 읽음"
                + (" (mesh/*_SolidMap_geometry_only.hm 은 SolidMap 시도 때 저장한 CAD 형상뿐, 메시 없음)"
                   if (hs or {}).get("GEOM_ONLY_HM") else ""))
            if memo_f.exists():
                memo_f.unlink()
            return gs
        log(f"[{m['name']}] 자동 헥사 실패 -> {gs.get('MESSAGE', '')}")
        for l in gs.get("LOG_TAIL", [])[-4:]:
            log(f"[{m['name']}]   grid| {l}")
        msg = "; ".join(x for x in ((hs or {}).get("MESSAGE", ""), gs.get("MESSAGE", "")) if x)
        tail = list((hs or {}).get("LOG_TAIL", []))[-2:] + list(gs.get("LOG_TAIL", []))[-4:]
        if gs.get("DETERMINISTIC_FAIL"):   # 품질 기준 탈락만 기록 (시간 초과/중간 종료는 다음에 다시 시도)
            try:
                memo_f.write_text(json.dumps({"sig": sig, "time": time.strftime("%Y-%m-%d %H:%M"), "message": msg,
                                              "log_tail": tail}, ensure_ascii=False, indent=1), encoding="utf-8")
            except OSError:
                pass
        return {"STATUS": "FAIL", "ALGO": "HEX", "MESSAGE": msg, "N_HEX": 0, "LOG_TAIL": tail}
    if str(cfg["mesh"].get("mesher", "hypermesh")).lower() == "gmsh":
        # [v14] gmsh 를 주 메셔로 (Netgen 최적화로 납작한 Tetra 가 적음). 실패하면 HyperMesh 로.
        gs = runners.run_gmsh(g_cfg, src, mesh_dir, m["name"], es)
        if gs.get("STATUS") == "OK":
            return gs
        log(f"[{m['name']}] gmsh 메시 실패 ({gs.get('MESSAGE', '')}) -> HyperMesh 로 시도")
    if engine == "v10":
        # [v20] 연구실 YL_V10 (BatchMesher 스킨 -> 실패 시 V8 -> 같은 세션에서 Tetra + INP)
        ps1 = resolve(cfg, hm.get("v10_ps1", "hypermesh/YL_V10/YL_V10.ps1"))
        st = yl_v10.run_v10(hm, ps1, src, mesh_dir, m["name"], es, cfg["mesh"].get("elem_type", "C3D10H"),
                            hm["fixed_size"])
        if st.get("STATUS") != "OK" and hm.get("v10_then_v5", False):
            log(f"[{m['name']}] YL_V10 실패 ({st.get('MESSAGE', '')}) -> 예전 YL_V5 엔진으로 시도")
            st = runners.run_hypermesh(hm, resolve(cfg, hm.get("tcl", "hypermesh/YL_V5_batch.tcl")), src, mesh_dir,
                                       m["name"], es, cfg["mesh"].get("elem_type", "C3D10H"))
    else:
        st = runners.run_hypermesh(hm, resolve(cfg, hm.get("tcl", "hypermesh/YL_V5_batch.tcl")), src, mesh_dir,
                                   m["name"], es, cfg["mesh"].get("elem_type", "C3D10H"))
    # [v9] HyperMesh 가 끝까지 실패하면 예비 메셔(gmsh)로 한 번 더 시도
    if st.get("STATUS") != "OK" and g_cfg.get("enabled", True) \
            and str(cfg["mesh"].get("mesher", "hypermesh")).lower() != "gmsh":
        log(f"[{m['name']}] HyperMesh 메시 실패 ({st.get('MESSAGE', '')}) -> 예비 메셔 gmsh 시도 ...")
        gs = runners.run_gmsh(g_cfg, src, mesh_dir, m["name"], es)
        if gs.get("STATUS") == "OK":
            gs["HM_ERROR"] = st.get("MESSAGE", "")
            log(f"[{m['name']}] 주의: 이 메시는 HyperMesh 가 아니라 gmsh 로 만들었습니다 "
                f"(Tet {gs.get('N_TET')}개, {gs.get('elapsed_s')}s). 글자 등 작은 형상 주변 요소 품질은 "
                f"qc_report.md 의 shape_q_min 으로 확인하세요.")
            return gs
        log(f"[{m['name']}] gmsh 도 실패: {gs.get('MESSAGE', '')}")
        for l in gs.get("LOG_TAIL", [])[-4:]:
            log(f"[{m['name']}]   gmsh| {l}")
    # [v21] 그래도 실패하면 최후의 메셔 (TetWild) : 구멍/겹침/자기관통이 있는 깨진 CAD 용
    r_cfg = dict(cfg.get("robust_fallback", {}))
    r_cfg["fixed_size"] = bool(cfg["mesh"].get("fixed_size"))
    if st.get("STATUS") != "OK" and r_cfg.get("enabled", True):
        log(f"[{m['name']}] 최후의 메셔(TetWild) 시도 ... (깨진 CAD 용, 몇 분~십여 분 걸림)")
        rs = runners.run_robust(r_cfg, src, mesh_dir, m["name"], es)
        if rs.get("STATUS") == "OK":
            rs["HM_ERROR"] = st.get("MESSAGE", "")
            log(f"[{m['name']}] 주의: 이 메시는 TetWild 로 만들었습니다 (Tet {rs.get('N_TET')}개, 부피 {rs.get('VOLUME_MM3')} mm3, "
                f"{rs.get('elapsed_s')}s). CAD 에 결함이 있어 HyperMesh/gmsh 가 실패한 모델입니다.")
            return rs
        log(f"[{m['name']}] TetWild 도 실패: {rs.get('MESSAGE', '')}")
        for l in rs.get("LOG_TAIL", [])[-4:]:
            log(f"[{m['name']}]   robust| {l}")
    return st


def _snapshot_inputs(run_dir: Path, mesh_file: str, job_inps: list) -> dict:
    """[v27.4] 다시 쓰기 전의 메시/Job INP 글과 지문 (숫자 표기만 바뀐 경우 이전 해석 결과를 계속 쓰기 위해)"""
    snap = {"text": {}, "hash": {}}
    for name in [mesh_file] + [f"{j}.inp" for j in job_inps]:
        f = run_dir / name
        if f.exists():
            try:
                snap["text"][name] = f.read_text(encoding="ascii", errors="ignore")
            except OSError:
                pass
    for j in job_inps:
        if (run_dir / f"{j}.inp").exists():
            snap["hash"][j] = input_hash(run_dir / f"{j}.inp")
    return snap


def _keep_results_if_same(run_dir: Path, tag: str, mesh_file: str, job_inps: list, old: dict):
    """[v27.4] 새 INP 가 예전 INP 와 숫자 표기(8e-06 -> 0.000008 등)만 다르면 해석 상태 파일의 지문을 새 INP 것으로 바꾼다.
    -> 이미 끝난 해석을 다시 돌리지 않는다. 메시/세트/조건이 조금이라도 다르면 손대지 않음 (다시 해석)."""
    names = [mesh_file] + [f"{j}.inp" for j in job_inps]
    if not old["hash"] or any(n not in old["text"] for n in names):
        return
    try:
        same = all(inp_check.equivalent(old["text"][n], (run_dir / n).read_text(encoding="ascii", errors="ignore"))
                   for n in names)
    except OSError:
        return
    if not same:
        return
    n_upd = 0
    for j, h0 in old["hash"].items():
        stf = run_dir / f"{j}_status.json"
        if not stf.exists():
            continue
        try:
            st = json.loads(stf.read_text(encoding="utf-8"))
        except ValueError:
            continue
        h1 = input_hash(run_dir / f"{j}.inp")
        if st.get("input_hash") == h0 and h1 != h0:
            st["input_hash"] = h1
            stf.write_text(json.dumps(st, indent=2, ensure_ascii=False), encoding="utf-8")
            n_upd += 1
    if n_upd:
        log(f"[{tag}] INP 숫자 표기만 바뀜 (값은 같음) -> 이전 해석 결과를 그대로 씀")


def _check_written_inputs(run_dir: Path, tag: str, job_inps: list) -> dict:
    """[v27.4] 방금 쓴 Job INP(+ *Include 메시)를 검사. Abaqus 가 못 읽을 오류가 있으면 해석 전에 멈추고,
    HyperMesh 만 못 읽는 표기(지수 표기 숫자)는 경고만 한다. 결과는 run/<tag>_inpcheck.txt"""
    reps = []
    for k, j in enumerate(job_inps):
        reps.append(inp_check.check_inp(run_dir / f"{j}.inp", follow_include=(k == 0), refs=(k == 0)))
    txt = "\n\n".join(inp_check.format_report(r) for r in reps[:3])
    if len(reps) > 3:
        txt += f"\n\n(Job INP {len(reps) - 3}개 더: 오류 {sum(r['n_errors'] for r in reps[3:])}개, "
        txt += f"지수 표기 {sum(r['n_hm'] for r in reps[3:])}줄)"
    (run_dir / f"{tag}_inpcheck.txt").write_text(txt + "\n", encoding="utf-8")
    n_err, n_hm = sum(r["n_errors"] for r in reps), sum(r["n_hm"] for r in reps)
    if n_err:
        first = [f"{Path(f).name} 줄 {ln}: {msg}" for r in reps for f, ln, msg, _t, _k in r["errors"]][:5]
        raise ValueError(f"INP 검사 실패 (오류 {n_err}개) -> 해석하지 않음: " + " / ".join(first)
                         + f"  (자세히: run/{tag}_inpcheck.txt)")
    if n_hm:
        log(f"[{tag}] WARNING INP 에 HyperMesh 가 못 읽는 지수 표기 숫자 {n_hm}줄 (Abaqus 는 읽음) -> run/{tag}_inpcheck.txt")
    return {"ok": True, "hm_ok": n_hm == 0, "n_errors": n_err, "n_hm": n_hm,
            "n_nodes": reps[0]["n_nodes"], "n_elems": reps[0]["n_elems"], "n_steps": reps[0]["n_steps"]}


def stage_build(cfg, m, es, tag, tag_dir, mat, dialect):
    gcfg = dict(cfg.get("geometry", {}))
    gcfg.update({k: v for k, v in m.items() if k in ("axis", "center", "inner_tol_mm", "outer_tol_mm",
                                                       "inner_r_max", "outer_r_min", "normal_cos_min", "elsets",
                                                       "inner_mode", "connect_rmax_frac", "shape_mode", "swage_mode", "outer_mode")})
    src = tag_dir / "mesh" / f"{m['name']}_hm.inp"
    mesh = inp_mesh.read_inp_mesh(src, gcfg.get("elsets"))
    if mesh_engine(cfg) == "hex" and mesh.family != "hex8":
        raise ValueError(f"헥사 전용 모드(hypermesh.engine = hex)인데 메시가 헥사가 아닙니다 ({mesh.family}, {src.name}). "
                         f"헥사 메시만 해석합니다 -> models 폴더의 이 모델이 Tetra .inp 라면 헥사 .inp 로 바꾸거나 빼 주세요")
    inp_mesh.check_mesh_sane(mesh, int(cfg["mesh"].get("min_elems", 50)))
    comp = inp_mesh.keep_largest_component(mesh)
    if comp.get("warning"):
        log(f"[{tag}] WARNING {comp['warning']}")
    inp_mesh.check_mesh_sane(mesh, int(cfg["mesh"].get("min_elems", 50)))
    if mesh.family == "tet4" and cfg["mesh"].get("upgrade_tet4", True):
        inp_mesh.upgrade_tet4_to_tet10(mesh)
    inp_mesh.fix_tet10_midside_order(mesh)
    inp_mesh.fix_orientation(mesh)
    # [v14] 납작한 Tetra 보정 (발산의 주원인). mesh.repair_quality=false 로 끌 수 있음
    repair = {}
    if cfg["mesh"].get("repair_quality", True) and mesh.family in ("tet4", "tet10"):
        repair = inp_mesh.improve_tet_quality(mesh)
        n_del = inp_mesh.remove_surface_slivers(mesh)
        if n_del:
            comp2 = inp_mesh.keep_largest_component(mesh)
            r2 = inp_mesh.improve_tet_quality(mesh)
            repair.update({k: r2[k] for k in r2 if k.endswith("_after") or k == "n_all_surface_slivers"})
            repair["nodes_moved"] = repair.get("nodes_moved", 0) + r2.get("nodes_moved", 0)
        repair["surface_slivers_deleted"] = n_del
    shape_mode = str(gcfg.get("shape_mode", "general")).lower()
    if gcfg.get("inner_r_max") is not None or gcfg.get("outer_r_min") is not None:
        shape_mode = "cylinder"      # 반경을 직접 지정한 모델은 예전 방식 그대로
    if shape_mode == "general":
        # [v12] 임의 형상 : 관통 구멍으로 축/원점(0,0,0)/INNER/OUTER 판별
        ax_info, sets = shape_general.setup_general(
            mesh, gcfg.get("axis", "auto"), gcfg.get("center", "auto"), gcfg.get("normal_cos_min", 0.6),
            gcfg.get("inner_tol_mm", 0.5), gcfg.get("outer_tol_mm", 0.5), gcfg.get("inner_mode", "connected"),
            gcfg.get("swage_mode", "auto"), gcfg.get("outer_mode", "hull"))
    else:
        c_old = gcfg.get("center", "none")
        ax_info = inp_mesh.align_axis_to_y(mesh, gcfg.get("axis", "auto"), "none" if c_old == "auto" else c_old)
        sets = inp_mesh.classify_inner_outer(mesh, gcfg.get("normal_cos_min", 0.6), gcfg.get("inner_tol_mm", 0.5),
                                             gcfg.get("outer_tol_mm", 0.5), gcfg.get("inner_r_max"),
                                             gcfg.get("outer_r_min"), gcfg.get("inner_mode", "connected"),
                                             float(gcfg.get("connect_rmax_frac", 0.6)))
    q = inp_mesh.tet_quality(mesh)
    feats = inp_mesh.geometry_features(mesh, sets)
    run_dir = tag_dir / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    mesh_file = f"{tag}_mesh.inp"
    jobs = jobs_for(cfg, tag)
    job_inps = ([inp_writer.combined_job_name(tag)] if odb_mode(cfg, dialect) == "single" else [j["job"] for j in jobs])
    old = _snapshot_inputs(run_dir, mesh_file, job_inps)
    winfo = inp_writer.write_mesh_inp(run_dir / mesh_file, mesh, sets, dialect, cfg.get("lab_aliases", True))
    combined = None
    if odb_mode(cfg, dialect) == "single":
        # [v11] 모든 스웨징 x 방향을 Job/ODB 1개로
        cjob = inp_writer.combined_job_name(tag)
        plan = inp_writer.write_combined_inp(run_dir / f"{cjob}.inp", mesh_file, mesh, sets, mat, jobs,
                                             winfo["rp_id"], cfg.get("steps"),
                                             cfg.get("swaging_outward_positive", True),
                                             title=f"{m['name']} E{es}",
                                             inner_constraint=cfg.get("inner_constraint", "rigid_body"))
        combined = {"job": cjob, "plan": plan}
    for j in ([] if combined else jobs):
        inp_writer.write_job_inp(run_dir / f"{j['job']}.inp", mesh_file, mesh, sets, mat, j["sw"], j["dir"],
                                 j["sign"], j["amp"], winfo["rp_id"], dialect, cfg.get("steps"),
                                 cfg.get("swaging_outward_positive", True), title=f"{m['name']} E{es}",
                                 inner_constraint=cfg.get("inner_constraint", "rigid_body"))
    inp_rep = _check_written_inputs(run_dir, tag, job_inps)
    _keep_results_if_same(run_dir, tag, mesh_file, job_inps, old)
    info = {"model": m["name"], "elem_size": es, "tag": tag, "source_inp": str(src), "dialect": dialect,
            "rp_id": winfo["rp_id"], "element_type": winfo["element_type"], "axis": ax_info,
            "sets": sets.info, "quality": q, "features": feats, "mesh_log": mesh.log, "components": comp, "quality_repair": repair,
            "material": {k: mat[k] for k in ("name", "mu", "alpha", "D", "mu0", "K0")}, "jobs": jobs,
            "combined": combined, "inp_check": inp_rep}
    (tag_dir / "model_info.json").write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        results.plot_sets(mesh, sets, tag_dir / f"{tag}_SETS.png", tag)
    except Exception as e:  # 그림은 부가 기능
        log(f"[{tag}] SETS 그림 생략: {e}")
    return info


# [v22] 해석 입력(Job INP + *Include 한 메시 INP)의 지문. 같으면 이전 해석 결과를 그대로 쓴다. (v27.4 : core.inp_check 로 옮김)
input_hash = inp_check.input_hash


def odb_mode(cfg, dialect) -> str:
    """single : 모델당 ODB 1개(모든 스웨징 x 방향) / separate : Job 마다 ODB 1개(예전 방식). ccx 는 separate 만."""
    mode = str(cfg.get("odb_mode", "separate")).lower()
    return "single" if (mode == "single" and dialect == "abaqus") else "separate"


def _split_step_blocks(inp_text: str):
    """통합 INP 를 (머리말, [(스텝이름, 스텝본문)]) 으로 나눈다."""
    marks = [(m.start(), m.group(1)) for m in re.finditer(r"^\*\* -{16} (\S+) -{16}\n", inp_text, re.M)]
    if not marks:
        return inp_text, []
    blocks = []
    for k, (pos, name) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(inp_text)
        blocks.append((name, inp_text[pos:end]))
    return inp_text[:marks[0][0]], blocks


def _write_continuation(run_dir, base_inp: Path, new_job: str, cases: list) -> list:
    """[v13] 남은 케이스만으로 통합 INP 를 다시 만든다 (원본 INP 의 스텝 블록을 재조합).
    스웨징 값마다 SWAGE 스텝을 처음부터 다시 걸고 남은 하중 케이스를 이어 붙인다. 반환: 새 계획"""
    head, blocks = _split_step_blocks(base_inp.read_text(encoding="ascii", errors="ignore"))
    bd = dict(blocks)
    out, plan, prev = [head], [], None
    for sw in list(dict.fromkeys(c["sw"] for c in cases)):
        group = [c for c in cases if c["sw"] == sw]
        sw_step = group[0]["swage_step"]
        out.append(bd[sw_step])
        prev = sw_step
        for c in group:
            back = c["load_step"][:-5] + "_BACK"
            out.append(bd[c["load_step"]])
            plan.append(dict(c, prev_step=prev))
            out.append(bd[back])
            prev = back
    txt = "".join(out)
    if "LOAD_RP, 1, 6, 0." not in txt:      # 첫 스텝에 RP 구속이 있어야 함 (원본은 맨 처음 SWAGE 에만 있음)
        txt = txt.replace("*Boundary\n", "*Boundary\nLOAD_RP, 1, 6, 0.\n", 1)
    (run_dir / f"{new_job}.inp").write_text(txt, encoding="ascii")
    return plan


def _step_order(plan: list) -> list:
    """계획 -> 스텝 이름 순서 (.sta 의 스텝 번호와 맞추기 위함)"""
    names, last_sw = [], None
    for c in plan:
        if c["swage_step"] != last_sw:
            names.append(c["swage_step"])
            last_sw = c["swage_step"]
        names += [c["load_step"], c["load_step"][:-5] + "_BACK"]
    return names


def stage_run_combined(cfg, run_dir, info, stages, force):
    """[v11] 통합 Job 1개 실행 -> ODB 1개 -> CSV -> 예전 형식의 <job>_RP.csv 여러 개로 분리.
    [v13] 어떤 스텝이 발산하면 그 케이스(스웨징 스텝이면 그 스웨징 값 전체)만 건너뛰고,
          남은 케이스로 이어서 계산하는 Job(<job>_r2, _r3 ...)을 자동으로 만들어 돌린다."""
    cjob, plan0 = info["combined"]["job"], info["combined"]["plan"]
    tag = info["tag"]
    st_file = run_dir / f"{cjob}_status.json"
    st = json.loads(st_file.read_text()) if st_file.exists() else {}
    script = HERE / "abaqus_scripts" / "extract_odb.py"
    max_jobs = int(cfg.get("abaqus", {}).get("max_continuations", len(plan0))) + 1
    fp = input_hash(run_dir / f"{cjob}.inp")
    if st.get("input_hash"):
        finished = bool(st.get("finished")) and st["input_hash"] == fp
        if st.get("finished") and st["input_hash"] != fp and "run" in stages:
            log(f"  [{tag}] 해석 입력(메시/세트/조건)이 지난번과 달라 다시 해석합니다")
    else:   # v21 이전 결과 : 예전 규칙 (전부 완료면 재사용)
        finished = bool(st.get("all_done"))
    do_run = "run" in stages and (force or not finished)
    if "run" in stages and not do_run:
        log(f"  [{tag}] 이전 해석 결과 재사용 ({st.get('n_cases_done', '?')}/{len(plan0)} 케이스 완료, 입력 동일) "
            f"-> 다시 돌리려면 --force")

    def extract_and_split(job, plan):
        csv_path = run_dir / f"{job}_RP.csv"
        if "extract" in stages and (run_dir / f"{job}.odb").exists() and (force or do_run or not csv_path.exists()):
            if csv_path.exists():
                csv_path.unlink()
            ok, _ = runners.extract_odb(cfg["abaqus"], run_dir, job, info["rp_id"], script)
            if not ok:
                log(f"  [{tag}] {job}: ODB 에서 LOAD_RP 추출 실패 -> {job}.extractlog 내용:")
                for l in runners._tail(run_dir / f"{job}.extractlog", 8):
                    log(f"    {l}")
        return results.split_combined_csv(csv_path, plan, run_dir, keep_existing=True) if csv_path.exists() else {}

    done, skipped = {}, {}
    chain = st.get("chain") if not do_run else None
    if chain:                                     # 실행 없이 추출/리포트만 다시
        for link in chain:
            for k, v in extract_and_split(link["job"], link["plan"]).items():
                if v["completed"]:
                    done[k] = link["job"]
        skipped = st.get("skipped", {})
    elif do_run:
        for c in plan0:                           # 이전 실행의 케이스별 CSV 는 지우고 새로
            f = run_dir / f"{c['job']}_RP.csv"
            if f.exists():
                f.unlink()
        chain, job, plan = [], cjob, plan0
        for k in range(1, max_jobs + 1):
            acfg = dict(cfg["abaqus"])
            acfg["timeout_min"] = float(acfg.get("timeout_min", 120)) * max(1, len(plan))
            jst = runners.run_abaqus(acfg, run_dir, job)
            if k == 1:
                st = dict(jst)
            chain.append({"job": job, "plan": plan, "completed": jst.get("completed"),
                          "started": bool(jst.get("started", True)), "elapsed_s": jst.get("elapsed_s")})
            parts = extract_and_split(job, plan) if jst.get("started", True) else {}
            prog = runners.sta_progress(run_dir, job) if jst.get("started", True) else {}
            names = _step_order(plan)
            step_done = {names[i - 1] for i, v in prog.items() if i <= len(names) and v["step_time"] > 0.9999}
            for c in plan:
                if parts.get(c["job"], {}).get("completed"):
                    done[c["job"]] = job
            if jst.get("completed") or not prog:
                if not jst.get("completed"):
                    log(f"  [{tag}] {job}: Abaqus 가 시작하지 못함 -> 이어서 계산 안 함 {jst.get('errors') or ''}")
                break
            # 실패한 스텝 = 완료되지 않은 첫 스텝
            failed = next((n for n in names if n not in step_done), None)
            if failed is None:
                break
            fi = names.index(failed) + 1
            ft = prog.get(fi, {}).get("step_time", 0.0)
            err = "; ".join(jst.get("errors") or [])[:160]
            if failed.endswith("_SWAGE"):
                bad = [c for c in plan if c["swage_step"] == failed]
                why = f"스웨징 스텝 {failed} 발산 (step time {ft:.3f})"
            elif failed.endswith("_LOAD"):
                bad = [c for c in plan if c["load_step"] == failed]
                why = f"하중 스텝 {failed} 발산 (step time {ft:.3f})"
            else:                                   # _BACK(원위치) 실패 : 그 케이스의 하중 곡선은 이미 얻음
                bad = []
                why = f"원위치 스텝 {failed} 발산 (step time {ft:.3f})"
            for c in bad:
                skipped[c["job"]] = f"{why} | {err}"
            rest = [c for c in plan0 if c["job"] not in done and c["job"] not in skipped]
            log(f"  [{tag}] {job}: {why}. 완료 {len(done)}/{len(plan0)}, 건너뜀 {len(skipped)}, 남은 {len(rest)} 케이스"
                + (f" -> 이어서 계산 ({cjob}_r{k + 1})" if rest and k < max_jobs else ""))
            if not rest or k >= max_jobs:
                break
            job = f"{cjob}_r{k + 1}"
            plan = _write_continuation(run_dir, run_dir / f"{cjob}.inp", job, rest)
        st["chain"] = chain
        st["skipped"] = skipped
        st["all_done"] = len(done) == len(plan0)
        # Abaqus 가 시작조차 못 한 경우(라이선스 등)는 '끝남'으로 보지 않음 -> 다음 실행에서 다시 시도
        st["finished"] = all(l.get("started", True) for l in chain)
        st["input_hash"] = fp
        st["elapsed_s"] = round(sum(float(l.get("elapsed_s") or 0) for l in chain), 1)
    else:
        for k, v in extract_and_split(cjob, plan0).items():
            if v["completed"]:
                done[k] = cjob
    for c in plan0:
        ok = c["job"] in done
        vst = {"completed": ok, "n_increments": None, "cutbacks": None, "elapsed_s": st.get("elapsed_s"),
               "combined_job": done.get(c["job"], cjob),
               "errors": [] if ok else [skipped.get(c["job"]) or "; ".join(st.get("errors") or [])
                                        or f"{c['load_step']} 스텝 미완료"]}
        (run_dir / f"{c['job']}_status.json").write_text(json.dumps(vst, indent=2, ensure_ascii=False))
    st["n_cases_done"] = len(done)
    st["n_cases"] = len(plan0)
    st["extracted"] = bool(done)
    st_file.write_text(json.dumps(st, indent=2, ensure_ascii=False))
    return cjob, st


def stage_run_one(cfg, solver, run_dir, j, rp_id, stages, force):
    job = j["job"]
    st_file = run_dir / f"{job}_status.json"
    st = json.loads(st_file.read_text()) if st_file.exists() else {}
    fp = input_hash(run_dir / f"{job}.inp")
    if st.get("input_hash"):
        finished = st["input_hash"] == fp and (st.get("completed") or st.get("started", False))
    else:
        finished = bool(st.get("completed"))
    if "run" in stages and (force or not finished):
        old_csv = run_dir / f"{job}_RP.csv"      # [v27.5] 다시 해석하면 예전 결과 CSV 를 지우고 새로 추출
        if old_csv.exists():
            old_csv.unlink()
        if solver == "abaqus":
            st = runners.run_abaqus(cfg["abaqus"], run_dir, job)
        else:
            st = runners.run_ccx(cfg.get("ccx", {}), run_dir, job)
            st["started"] = True
        st["input_hash"] = fp
        st_file.write_text(json.dumps(st, indent=2))
    csv_path = run_dir / f"{job}_RP.csv"
    if "extract" in stages and st.get("completed") and (force or not csv_path.exists()):
        if solver == "abaqus":
            ok, _ = runners.extract_odb(cfg["abaqus"], run_dir, job, rp_id,
                                        HERE / "abaqus_scripts" / "extract_odb.py")
        else:
            ok = runners.parse_ccx_dat(run_dir / f"{job}.dat", csv_path)
        st["extracted"] = bool(ok)
        st_file.write_text(json.dumps(st, indent=2))
    return job, st


def stage_report(cfg, work, model_infos):
    rows, curves, notes = [], [], []
    for info in model_infos:
        tag_dir = work / info["tag"]
        run_dir = tag_dir / "run"
        model_curves = []
        for j in info["jobs"]:
            st_file = run_dir / f"{j['job']}_status.json"
            st = json.loads(st_file.read_text()) if st_file.exists() else {}
            row = {"job": j["job"], "model": info["model"], "elem_size": info["elem_size"],
                   "swaging_mm": j["sw"], "direction": j["dir"], "sign": j["sign"], "amplitude_mm": j["amp"],
                   "element_type": info["element_type"], "n_elem": info["quality"].get("n_elem"),
                   "shape_q_min": info["quality"].get("shape_q_min"),
                   "inner_nodes": info["sets"]["inner_nodes"], "outer_nodes": info["sets"]["outer_nodes"]}
            row.update({f"geo_{k}": v for k, v in info["features"].items()})
            mt = info["material"]
            row.update({"mat": mt["name"], "mu1": mt["mu"][0], "a1": mt["alpha"][0], "mu2": mt["mu"][1],
                        "a2": mt["alpha"][1], "mu3": mt["mu"][2], "a3": mt["alpha"][2], "D1": mt["D"][0]})
            row.update({"solver_completed": st.get("completed"), "n_increments": st.get("n_increments"),
                        "cutbacks": st.get("cutbacks"), "elapsed_s": st.get("elapsed_s")})
            csv_path = run_dir / f"{j['job']}_RP.csv"
            if st.get("completed") and csv_path.exists():
                a = results.analyze_job(csv_path, j["dir"], j["sign"], j["amp"])
                for k in ("K_secant", "K_initial", "K_sec_25", "K_sec_50", "K_sec_75", "K_tangent_end",
                          "F_end", "u_end", "n_points"):
                    row[k] = a.get(k)
                row["swage_RF_end"] = json.dumps([round(v, 4) for v in a.get("swage_RF_end", [])])
                row["qc"] = "; ".join(f"{k}:{v}" for k, v in a["checks"].items() if v != "OK") or "all OK"
                row["status"] = "OK" if a["ok"] else "QC_FAIL"
                c = {"job": j["job"], "model": info["model"], "elem_size": info["elem_size"], "sw": j["sw"],
                     "dir": j["dir"], "sign": j["sign"], "u": a["curve_u"], "F": a["curve_F"]}
                curves.append(c)
                model_curves.append(c)
            else:
                row["status"] = "SOLVER_FAIL" if st else "NOT_RUN"
                row["qc"] = "; ".join(st.get("errors", [])) if st else ""
            rows.append(row)
        if model_curves:
            results.plot_model(model_curves, tag_dir / f"{info['tag']}_FD.png", info["tag"])
    ds = work / "dataset"
    results.write_dataset(rows, curves, ds)
    notes = results.model_level_checks(rows, cfg.get("qc", {}).get("symmetry_tol", 0.10))
    n_ok = sum(r["status"] == "OK" for r in rows)
    md = [f"# YL_AUTO QC report ({time.strftime('%Y-%m-%d %H:%M')})", "",
          f"- jobs: {len(rows)}, OK: {n_ok}, 문제: {len(rows) - n_ok}", ""]
    for info in model_infos:
        s = info["sets"]
        md += [f"## {info['tag']}", "",
               f"- element: {info['element_type']}, n_elem={info['quality'].get('n_elem')}, "
               f"shape_q_min={info['quality'].get('shape_q_min'):.3f}",
               f"- axis: {info['axis']['source_axis']} -> +Y, LOAD_RP id={info['rp_id']} at (0,0,0)",
               f"- INNER {s['inner_nodes']} nodes r={s['inner_r_range']}, "
               f"OUTER {s['outer_nodes']} nodes r={s['outer_r_range']}",
               f"- bore center offset {s['bore_center_offset_mm']:.4f} mm"] + \
              [f"- WARNING: {w}" for w in s.get("warnings", [])] + [""]
        md += ["| job | SW(mm) | dir | K_secant (N/mm) | K_initial | F_end (N) | status | QC |",
               "|---|---|---|---|---|---|---|---|"]
        for r in rows:
            if r["model"] == info["model"] and r["elem_size"] == info["elem_size"]:
                fmtv = lambda v: f"{v:.2f}" if isinstance(v, (int, float)) and v is not None else "-"
                md.append(f"| {r['job']} | {r['swaging_mm']:+g} | {r['direction']}{'+' if r['sign'] > 0 else '-'} | "
                          f"{fmtv(r.get('K_secant'))} | {fmtv(r.get('K_initial'))} | {fmtv(r.get('F_end'))} | "
                          f"{r['status']} | {r.get('qc', '')} |")
        md.append("")
    md += ["## 모델 단위 점검", ""] + [f"- {n}" for n in notes]
    (work / "qc_report.md").write_text("\n".join(md), encoding="utf-8")
    return rows


# ---------------------------------------------------------------------------
# [v27.5] 실행 흐름 : 모델 하나의 메시와 INP 가 끝나면 그 모델의 Abaqus 해석을 바로 시작한다.
#   예전에는 '모든 모델 메시 -> 그다음 모든 해석' 이라서, 자동 헥사가 모델당 수 분~수십 분 걸리는 지금은
#   메시가 다 끝날 때까지(몇 시간) Abaqus 가 하나도 시작하지 않았고, 그 전에 창을 닫으면 해석이 전혀 없었다.
# ---------------------------------------------------------------------------
_RUN_LOCK = threading.Lock()
_RUN_STATE = {"fail_shown": 0, "queued": 0, "running": 0, "done": 0}


def check_previous_run(work: Path):
    """지난 실행이 끝까지 갔는지, Abaqus 가 강제로 꺼진 흔적(.lck/.023/.SMABulk)이 있는지 알려 준다."""
    sf = work / "run_state.json"
    try:
        prev = json.loads(sf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev = None
    if prev and not prev.get("finished"):
        log(f"[주의] 지난 실행({prev.get('started', '?')} 시작)이 끝까지 가지 못하고 멈췄습니다 "
            f"(실행 창을 닫았거나 Ctrl+C, PC 절전/재시작). 그때 돌던 Abaqus 해석은 이번에 다시 합니다.")
    for d in sorted(work.glob("*/run")):
        try:
            left = [q for q in d.iterdir() if q.is_file() and (q.suffix.lower() in (".lck", ".023")
                                                               or q.name.lower().endswith(".smabulk"))]
        except OSError:
            continue
        if not left:
            continue
        t = time.strftime("%m-%d %H:%M", time.localtime(max(q.stat().st_mtime for q in left)))
        jobs = sorted({q.name.split(".")[0] for q in left})
        log(f"[{d.parent.name}] 지난번 Abaqus 가 정상 종료하지 못하고 꺼진 흔적 ({', '.join(jobs)}, {t}) "
            f"-> 그 해석 결과는 불완전하므로 이번에 다시 합니다")
        for q in left:          # 잠금/임시 파일은 지움 (남아 있으면 같은 Job 이 시작하지 못하거나 경고가 매번 반복됨)
            try:
                q.unlink()
            except OSError:
                pass
        for j in jobs:
            base = re.sub(r"_r\d+$", "", j)
            stf = d / f"{base}_status.json"
            try:
                st = json.loads(stf.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            # 통합 Job 은 finished, Job 별 실행은 completed/started 로 '끝남' 을 판단 -> 모두 되돌려 다시 해석하게 함
            st.update({"finished": False, "completed": False, "started": False, "interrupted": True})
            try:
                stf.write_text(json.dumps(st, indent=2, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
    try:
        sf.write_text(json.dumps({"started": time.strftime("%Y-%m-%d %H:%M:%S"), "pid": os.getpid(),
                                  "finished": False}), encoding="utf-8")
    except OSError:
        pass


def mark_run_finished(work: Path):
    sf = work / "run_state.json"
    try:
        st = json.loads(sf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    st.update({"finished": True, "ended": time.strftime("%Y-%m-%d %H:%M:%S")})
    try:
        sf.write_text(json.dumps(st), encoding="utf-8")
    except OSError:
        pass


def _log_job_result(job, st, stages):
    log(f"  {job}: completed={st.get('completed')} inc={st.get('n_increments')} "
        f"t={st.get('elapsed_s')}s extracted={st.get('extracted', '-')} {st.get('errors') or ''}")
    if "n_cases" in st:
        njob = len(st.get("chain") or [1])
        log(f"    통합 Job: 하중 케이스 {st['n_cases_done']}/{st['n_cases']} 개 완료"
            + (f" (발산한 케이스를 건너뛰며 Job {njob}개로 나눠 계산)" if njob > 1 else ""))
        for kjob, why in (st.get("skipped") or {}).items():
            log(f"    건너뜀 {kjob}: {why}")
    if not st.get("completed") and "run" in stages:
        with _RUN_LOCK:
            _RUN_STATE["fail_shown"] += 1
            show = _RUN_STATE["fail_shown"] <= 3      # 같은 원인이 반복되므로 처음 3개만 자세히
        if show:
            log(f"    실행 명령: {st.get('command', '-')}")
            for l in st.get("launch_notes", []):
                log(f"    {l}")
            for l in st.get("runlog", []):
                log(f"    runlog| {l}")
            if not st.get("started", True):
                log("    -> Abaqus 가 Job 파일(.dat/.sta)을 하나도 만들지 못했습니다. 위 runlog 줄이 원인입니다 "
                    "(라이선스 서버 연결, abaqus 명령 옵션).")
    elif st.get("launch_notes"):
        log(f"    참고: 첫 명령이 거부되어 옵션을 줄여 실행함 -> {st.get('command')}")


def _run_task(kind, tag, *args):
    """작업 스레드에서 Abaqus/CalculiX 1 건 실행 (+ 추출) 후 바로 로그와 진행 상황을 남긴다."""
    stages = args[-2]
    with _RUN_LOCK:
        _RUN_STATE["running"] += 1
    progress(tag, run="Abaqus 실행 중" if "run" in stages else "ODB 추출 중")
    log(f"[{tag}] 해석 시작 ({'통합 Job' if kind == 'combined' else args[3]['job']})")
    try:
        job, st = stage_run_combined(*args) if kind == "combined" else stage_run_one(*args)
    except Exception as e:
        log(f"[{tag}] 해석 단계 오류: {type(e).__name__}: {e}")
        progress(tag, run=f"오류: {type(e).__name__}")
        return None, {"completed": False, "errors": [str(e)]}
    finally:
        with _RUN_LOCK:
            _RUN_STATE["running"] -= 1
            _RUN_STATE["done"] += 1
    _log_job_result(job, st, stages)
    if "n_cases" in st:
        progress(tag, run=f"끝: 케이스 {st['n_cases_done']}/{st['n_cases']} 완료"
                 + (f", 건너뜀 {len(st.get('skipped') or {})}" if st.get("skipped") else ""))
    else:
        progress(tag, run=f"끝: {job.replace(tag + '_', '')} completed={st.get('completed')}")
    return job, st


def submit_runs(ex, cfg, solver, work, info, stages, force) -> list:
    run_dir = work / info["tag"] / "run"
    tag = info["tag"]
    calls = ([("combined", (cfg, run_dir, info, stages, force))] if info.get("combined") else
             [("one", (cfg, solver, run_dir, j, info["rp_id"], stages, force)) for j in info["jobs"]])
    with _RUN_LOCK:
        waiting = _RUN_STATE["queued"] - _RUN_STATE["done"] - _RUN_STATE["running"]
        busy = _RUN_STATE["running"] >= getattr(ex, "_max_workers", 1)
        _RUN_STATE["queued"] += len(calls)
    # 진행 상황/로그를 먼저 쓰고 넣는다 (작업 스레드가 바로 시작하면 '실행 중' 을 덮어쓰지 않게)
    progress(tag, run="해석 대기 (앞 해석이 끝나면 시작)" if busy or waiting > 0 else "해석 시작 준비")
    log(f"[{tag}] 해석을 대기열에 넣음 -> Abaqus 가 비는 대로 시작 (다음 모델 메시와 동시에 진행)")
    return [ex.submit(_run_task, kind, tag, *args) for kind, args in calls]


def _win(p: str) -> str:
    import os
    return os.path.normpath(p) if os.name == "nt" else p


def resolve_programs(cfg, solver, stages):
    """HyperMesh / Abaqus 실행 파일 경로 확정 (config 경로 -> 시작 메뉴 바로가기 -> 기본 위치 -> PATH)."""
    need_hm = "mesh" in stages and any(Path(m["file"]).suffix.lower() != ".inp" for m in model_list(cfg))
    if need_hm:
        exe, how = findexe.find_hmbatch(cfg.get("hypermesh", {}))
        if exe:
            cfg["hypermesh"]["exe"] = _win(exe)
            log(f"HyperMesh batch: {cfg['hypermesh']['exe']} ({how})")
        elif mesh_engine(cfg) == "hex" and cfg["hypermesh"].get("hex_auto", True) and runners.grid_hex_available():
            log("[경고] hmbatch.exe 를 못 찾음 -> YL_HEX(HyperMesh) 없이 자동 헥사(격자 + 경계층)로만 메시합니다")
            cfg["hypermesh"]["exe"] = ""
        else:
            log("[ERROR] hmbatch.exe 를 못 찾음 (check_env.py 결과 확인)")
            raise SystemExit("[ERROR] hmbatch.exe 를 못 찾았습니다. config 의 hypermesh.exe 에 전체 경로를 적어주세요 "
                             "(예: C:/Program Files/Altair/2019/hm/bin/win64/hmbatch.exe)")
    if solver == "abaqus" and ({"run", "extract"} & set(stages)):
        cmd, how = findexe.find_abaqus(cfg.get("abaqus", {}))
        if not cmd:
            log("[ERROR] Abaqus 명령을 못 찾음 (check_env.py 결과 확인)")
            raise SystemExit("[ERROR] Abaqus 실행 명령을 못 찾았습니다. config 의 abaqus.cmd 에 전체 경로를 적어주세요 "
                             "(예: C:/SIMULIA/Commands/abaqus.bat)")
        cfg["abaqus"]["cmd"] = _win(cmd)
        log(f"Abaqus: {cfg['abaqus']['cmd']} ({how})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--stage", default="all", help="all 또는 mesh,build,run,extract,report 중 콤마 구분")
    ap.add_argument("--only", default="", help="모델 이름(콤마 구분)만 실행")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    cfg = load_config(Path(a.config))
    stages = STAGES if a.stage == "all" else [s.strip() for s in a.stage.split(",")]
    solver = cfg.get("solver", "abaqus").lower()
    dialect = "abaqus" if solver == "abaqus" else "ccx"
    work = resolve(cfg, cfg.get("work_dir", "auto_out"))
    work.mkdir(parents=True, exist_ok=True)
    global LOG_FILE, PROGRESS_FILE
    LOG_FILE = work / "pipeline_log.txt"
    PROGRESS_FILE = work / "progress.txt"
    log(f"===== run_pipeline {a.config}  stage={a.stage}  only={a.only} =====")
    check_previous_run(work)
    check_hex_auto(cfg)
    check_models(cfg)
    resolve_programs(cfg, solver, stages)

    # 물성 (Ogden N=3)
    mats = {}
    cache_dir = work / "material_cache"
    cache_dir.mkdir(exist_ok=True)
    used = {m.get("material", cfg.get("default_material")) for m in model_list(cfg)}
    for name, spec in cfg["materials"].items():
        if name.startswith("_"):
            continue
        if None not in used and name not in used:
            continue  # 이번 실행에서 안 쓰는 물성은 피팅/검사 생략
        # 같은 물성/시험데이터면 피팅 결과 재사용 (데이터가 바뀌면 자동으로 다시 피팅)
        h = hashlib.sha1(json.dumps(spec, sort_keys=True).encode())
        for f in spec.get("test_data", {}).values():
            h.update(resolve(cfg, f).read_bytes())
        cf = cache_dir / f"{safe_name(name)}_{h.hexdigest()[:12]}.json"
        if cf.exists() and not a.force:
            mats[name] = json.loads(cf.read_text(encoding="utf-8"))
        else:
            mats[name] = ogden.resolve_material(safe_name(name), spec, cfg["_base"])
            cf.write_text(json.dumps(mats[name], indent=2, ensure_ascii=False), encoding="utf-8")
        log(f"material {name}: mu={['%.5g' % v for v in mats[name]['mu']]} "
            f"alpha={['%.4g' % v for v in mats[name]['alpha']]} D1={mats[name]['D'][0]:.4g} "
            f"(mu0={mats[name]['mu0']:.4g} MPa)")
    (work / "materials_ogden3.json").write_text(json.dumps(mats, indent=2, ensure_ascii=False), encoding="utf-8")

    only = {safe_name(s) for s in a.only.split(",") if s.strip()}
    model_infos = []
    todo = []
    for m in model_list(cfg):
        if only and m["name"] not in only:
            continue
        mname = m.get("material", cfg.get("default_material", next(iter(mats))))
        if mname not in mats:
            raise ValueError(f"모델 {m['name']}: 물성 '{mname}' 이 materials 에 없습니다.")
        for es in cfg["mesh"].get("elem_sizes", [2.0]):
            tag = f"{m['name']}_E{inp_writer.fmt_num(float(es))}"
            todo.append((m, mats[mname], es, tag, work / tag))
            progress(tag, mesh="대기", run="-")
    # [v27.5] 메시가 이미 있는 모델을 먼저 -> 그 모델들의 해석이 바로 시작됨 (메시가 오래 걸리는 모델은 뒤로)
    ready = [t for t in todo if mesh_ready(cfg, t[0], t[4])] if "mesh" in stages and not a.force else []
    if ready and len(ready) < len(todo):
        todo = ready + [t for t in todo if t not in ready]
        log(f"메시가 이미 있는 모델 {len(ready)}개를 먼저 처리합니다 -> 해석이 바로 시작됨 "
            f"(나머지 {len(todo) - len(ready)}개는 메시를 만들면서 차례로 해석에 넣음)")
    do_runs = bool({"run", "extract"} & set(stages))
    runner = ThreadPoolExecutor(max_workers=max(1, int(cfg.get(solver, {}).get("max_parallel", 1)))) if do_runs else None
    run_futs = []
    for m, mat, es, tag, tag_dir in todo:
        try:
            if "mesh" in stages:
                log(f"[{tag}] mesh ...")
                progress(tag, mesh="메시 중")
                st = stage_mesh(cfg, m, float(es), tag_dir, a.force)
                cnt = (f"hex={st.get('N_HEX')}" if st.get('N_HEX') not in (None, '')
                       else f"tet={st.get('N_TET', '-')} badTria={st.get('BAD_TRIA', '-')}")
                log(f"[{tag}] mesh {st.get('STATUS')} {st.get('MESSAGE', '')} {cnt}")
                if st.get("FIRST_TRY"):
                    log(f"[{tag}] 1차(YL_V4) 실패 -> HyperMesh 를 새로 띄워 2차(단순 메시) 자동 재시도함. "
                        f"1차 원인: {st.get('FIRST_TRY')}")
                    for l in st.get("LOG_TAIL_TRY1", [])[-8:]:
                        log(f"[{tag}]   1차| {l}")
                if st.get("AREA_API", None) == "" and st.get("ALGO") != "GMSH_FALLBACK":
                    log(f"[{tag}] 주의: HyperMesh 에서 면적 함수를 못 써서 작은 면 자동 무시(defeature)가 "
                        f"제대로 안 됐을 수 있습니다 (hm_log.txt 의 'defeature' 줄 확인)")
                if str(st.get("ALGO", "")).startswith("YL_V10"):
                    log(f"[{tag}] YL_V10 엔진={st.get('ENGINE')} ({st.get('MESHER')}) 2D 합격={st.get('PASS2D')} "
                        f"스킨 Tria={st.get('N_TRIA')} q평균={st.get('Q_MEAN')} | Tetra={st.get('N_TET')} "
                        f"aspect p95={st.get('TET_ASP_P95')} collapse<0.15={st.get('TET_COL_LT015')}% "
                        f"성장률={st.get('TET_GROWTH')}")
                    if st.get("ENGINE") == "V9>V8":
                        log(f"[{tag}] 주의: BatchMesher(V9) 스킨이 합격하지 못해 V8 엔진으로 다시 메시했습니다")
                if st.get("ALGO") == "FALLBACK_SIMPLE":
                    log(f"[{tag}] 주의: YL_V4 메시 알고리즘이 실패해 단순 메시(전체 면 Tria -> Tetra)로 대신 만들었습니다. "
                        f"원인: {st.get('V4_ERROR') or 'V4 단계에서 Tetra 0개'}")
                if st.get("STATUS") != "OK":
                    for l in st.get("LOG_TAIL", []):
                        log(f"[{tag}]   | {l}")
                    hint = (("헥사 전용 모드: YL_HEX 와 자동 헥사가 모두 실패했습니다. mesh 폴더의 gridhex_console.log 와 "
                             "*_hexmesh.png 를 보내 주세요."
                             if cfg.get("hypermesh", {}).get("hex_auto", True) else
                             "헥사 전용 모드: SolidMap 이 이 형상을 매핑하지 못했고, 자동 헥사가 꺼져 있어(hypermesh.hex_auto = "
                             "false) 시도하지 않았습니다. config_abaqus.json 의 hypermesh.hex_auto 를 true 로 바꾸고 다시 실행하세요.")
                            if mesh_engine(cfg) == "hex" else
                            "급하면 HyperMesh GUI 에서 직접 메시한 .inp 를 models 폴더에 넣으면 메시 단계를 건너뜁니다.")
                    log(f"[{tag}] 메시 실패 -> 이 모델은 해석(ODB) 단계로 가지 않습니다. {hint}")
                    progress(tag, mesh="실패" + (" (지난번과 같음)" if st.get("SKIPPED_SAME") else ""),
                             run="안 함 (메시 실패)")
                    continue
                lab = ", ".join(x for x in (str(st.get("ALGO") or ""), f"헥사 {st['N_HEX']}" if st.get("N_HEX") else "") if x)
                progress(tag, mesh=f"OK ({lab})" if lab else "OK")
            else:
                progress(tag, mesh="(이전 메시 사용)")
            info_file = tag_dir / "model_info.json"
            if "build" in stages or not info_file.exists():
                info = stage_build(cfg, m, float(es), tag, tag_dir, mat, dialect)
                log(f"[{tag}] build OK: {info['element_type']} n_elem={info['quality']['n_elem']} "
                    f"INNER={info['sets']['inner_nodes']} OUTER={info['sets']['outer_nodes']} "
                    f"jobs={len(info['jobs'])}"
                    + (f" -> 통합 Job 1개 ({info['combined']['job']}.odb)" if info.get("combined") else "")
                    + (" | INP 검사 OK (Abaqus/HyperMesh 형식)" if (info.get("inp_check") or {}).get("hm_ok") else ""))
                rq = info.get("quality_repair") or {}
                if rq:
                    log(f"[{tag}] 메시 품질 보정: 품질<0.02 요소 {rq['n_below_0p02_before']} -> {rq['n_below_0p02_after']}개, "
                        f"<0.1 요소 {rq['n_below_0p1_before']} -> {rq['n_below_0p1_after']}개, 최소 품질 "
                        f"{rq['q_min_before']:.4f} -> {rq['q_min_after']:.4f} "
                        f"(절점 {rq['nodes_moved']}개 이동, 표면 껍질 요소 {rq.get('surface_slivers_deleted', 0)}개 삭제)")
                    if rq["n_below_0p02_after"] > 0:
                        log(f"[{tag}] WARNING 품질<0.02 요소가 {rq['n_below_0p02_after']}개 남았습니다 -> 큰 스웨징에서 발산할 수 있음. "
                            f"(HyperMesh 메시라면 config 의 mesh.mesher 를 'gmsh' 로 바꿔 비교해 볼 것)")
                if info["sets"].get("method"):
                    tb = [round(v, 3) for v in info["axis"].get("translated_by", [0, 0, 0])]
                    log(f"[{tag}] 형상 판별: 축={info['axis']['source_axis']} -> +Y, 원점 이동 {tb} mm, "
                        f"INNER {'원형' if info['sets']['inner_is_round'] else '비원형'} / "
                        f"OUTER {'원형' if info['sets']['outer_is_round'] else '비원형'}, "
                        f"스웨징 방향={info['sets']['swage_direction']}")
                for w in info["sets"].get("warnings", []):
                    log(f"[{tag}] WARNING {w}")
            else:
                info = json.loads(info_file.read_text(encoding="utf-8"))
            model_infos.append(info)
            if runner is not None:      # [v27.5] 이 모델 해석을 바로 시작 (다음 모델 메시와 동시에)
                run_futs += submit_runs(runner, cfg, solver, work, info, stages, a.force)
            else:
                progress(tag, run="이번 실행은 해석 단계 없음 (--stage)")
        except Exception as e:
            log(f"[{tag}] ERROR {e}")
            progress(tag, run=f"안 함 (오류: {str(e)[:40]})")
            (tag_dir).mkdir(parents=True, exist_ok=True)
            (tag_dir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")

    if runner is not None:
        with _RUN_LOCK:
            left = _RUN_STATE["queued"] - _RUN_STATE["done"]
        if left:
            log(f"메시/INP 단계 끝. 남은 해석 {left}건이 끝날 때까지 기다립니다 "
                f"(이 창을 닫으면 돌던 Abaqus 도 같이 꺼집니다. 진행 상황: {PROGRESS_FILE})")
        runner.shutdown(wait=True)

    if "report" in stages and model_infos:
        rows = stage_report(cfg, work, model_infos)
        n_ok = sum(r["status"] == "OK" for r in rows)
        log(f"report: {n_ok}/{len(rows)} jobs OK -> {work / 'qc_report.md'}, {work / 'dataset'}")
    # [v27.5] 모델별 최종 상태 한눈에
    log("===== 모델별 결과 =====")
    for t, v in PROGRESS.items():
        log(f"  {t}: 메시 {v.get('mesh')} | 해석 {v.get('run')}")
    progress_end(f"모델 {len(PROGRESS)}개, 해석한 모델 {len(model_infos)}개")
    mark_run_finished(work)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        log("[ERROR] 예외로 중단:\n" + traceback.format_exc())
        sys.exit(1)
