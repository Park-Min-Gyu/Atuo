# -*- coding: utf-8 -*-
"""
yl_mesh_sets.py : CAD -> YL_V10 자동 메시(Tetra) -> 원점 (0,0,0) / 축 +Y -> INNER·OUTER 노드 세트

  python yl_mesh_sets.py config_mesh_sets.json            # models 폴더의 모든 모델
  python yl_mesh_sets.py config_mesh_sets.json Part2      # 이름으로 골라서

입력 : models/ 의 .stp/.step/.igs (HyperMesh 로 메시) 또는 이미 메시한 .inp (메시 단계 건너뜀)
출력 : out/<모델>/
  <모델>_mesh.inp         노드 + LOAD_RP(0,0,0) + 요소(RUBBER) + *Nset INNER / OUTER  (Abaqus 에 *Include 해서 사용)
  <모델>_INNER_nodes.csv  INNER 노드 번호/좌표
  <모델>_OUTER_nodes.csv  OUTER 노드 번호/좌표 + 스웨징 방향(바깥쪽 단위벡터 dx, dz)
  <모델>_SETS.png         INNER(파랑)/OUTER(빨강)/원점 확인 그림
  <모델>_info.json        축, 원점 이동량, 세트 정보, 메시 품질
  mesh/                   YL_V10 결과 (.hm, HyperMesh 가 쓴 .inp, 로그)
"""
from __future__ import annotations

import json
import shutil
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import findexe, inp_mesh, sets_out, shape_general, yl_hex, yl_v10  # noqa: E402

CAD_EXT = {".stp", ".step", ".igs", ".iges", ".hm"}


def log(msg):
    line = time.strftime("[%H:%M:%S] ") + msg
    print(line, flush=True)
    with open(OUT / "mesh_sets_log.txt", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def mesh_model(cfg, src: Path, name: str, mdir: Path) -> Path:
    """CAD -> YL_V10 -> Tetra INP. .inp 는 그대로 사용"""
    if src.suffix.lower() == ".inp":
        return src
    hm = dict(cfg["hypermesh"])
    exe, how = findexe.find_hmbatch(hm)
    if not exe:
        raise RuntimeError("hmbatch.exe 를 못 찾음 -> config 의 hypermesh.exe 에 전체 경로를 적어 주세요")
    hm["exe"] = exe
    if str(cfg.get("engine", "v10")).lower() == "hex":
        # 연구실 YL_HEX_V6 (SolidMap 순수 헥사, 2.0 mm 고정, C3D8H). 안 되면 YL_V10 Tetra 로
        hs = yl_hex.run_hex(hm, HERE / hm.get("hex_tcl", "hypermesh/YL_HEX/YL_HEX_V6_PURE_2mm.tcl"), src, mdir / "mesh",
                            name, float(cfg["elem_size"]), hm.get("hex_elem_type", "C3D8H"))
        if hs.get("STATUS") == "OK":
            log(f"[{name}] YL_HEX 헥사 메시 성공 : Hex {hs.get('N_HEX')}개 ({hs.get('elapsed_s')}s)")
            return Path(hs["INP_FILE"])
        log(f"[{name}] 헥사 메시 실패 -> {hs.get('MESSAGE', '')}")
        if not hm.get("hex_fallback_tet", True):
            raise RuntimeError(f"헥사 메시 실패: {hs.get('MESSAGE', '')[:300]}")
        log(f"[{name}] YL_V10 Tetra 로 넘어갑니다")
    st = yl_v10.run_v10(hm, HERE / hm.get("v10_ps1", "hypermesh/YL_V10/YL_V10.ps1"), src, mdir / "mesh", name,
                        float(cfg["elem_size"]), cfg.get("elem_type", "C3D10H"), bool(cfg.get("fixed_size", True)))
    log(f"[{name}] YL_V10 엔진={st.get('ENGINE')} 2D 합격={st.get('PASS2D')} Tria={st.get('N_TRIA')} "
        f"Tetra={st.get('N_TET')} ({st.get('elapsed_s')}s)")
    if st.get("STATUS") != "OK":
        for l in st.get("LOG_TAIL", [])[-8:]:
            log(f"[{name}]   | {l}")
        raise RuntimeError(f"메시 실패: {st.get('MESSAGE', '')[:400]}")
    return Path(st["INP_FILE"])


def sets_model(cfg, inp: Path, name: str, mdir: Path) -> dict:
    g = cfg.get("geometry", {})
    m = inp_mesh.read_inp_mesh(inp)
    inp_mesh.check_mesh_sane(m, 50)
    comp = inp_mesh.keep_largest_component(m)
    if comp.get("warning"):
        log(f"[{name}] WARNING {comp['warning']}")
    if m.family == "tet4" and cfg.get("upgrade_tet4", True):
        inp_mesh.upgrade_tet4_to_tet10(m)
    inp_mesh.fix_tet10_midside_order(m)
    inp_mesh.fix_orientation(m)
    rq = {}
    if cfg.get("repair_quality", True) and m.family in ("tet4", "tet10"):
        rq = inp_mesh.improve_tet_quality(m)
        rq["surface_slivers_deleted"] = inp_mesh.remove_surface_slivers(m)
    if str(g.get("shape_mode", "general")).lower() == "general":
        ax, sets = shape_general.setup_general(m, g.get("axis", "auto"), g.get("center", "auto"),
                                               g.get("normal_cos_min", 0.6), g.get("inner_tol_mm", 0.5),
                                               g.get("outer_tol_mm", 0.5), g.get("inner_mode", "connected"),
                                               g.get("swage_mode", "auto"), g.get("outer_mode", "hull"))
    else:   # 예전 원통 방식
        ax = inp_mesh.align_axis_to_y(m, g.get("axis", "auto"), "none" if g.get("center") == "auto" else g.get("center", "none"))
        sets = inp_mesh.classify_inner_outer(m, g.get("normal_cos_min", 0.6), g.get("inner_tol_mm", 0.5),
                                             g.get("outer_tol_mm", 0.5), g.get("inner_r_max"), g.get("outer_r_min"))
    w = sets_out.write_mesh_inp(mdir / f"{name}_mesh.inp", m, sets, cfg.get("abaqus_elem_type"))
    sets_out.write_node_csv(mdir / f"{name}_INNER_nodes.csv", m, sets.inner)
    dirs = getattr(sets, "outer_dir", None)
    if dirs is None:
        xo = m.xyz[sets.outer]
        import numpy as np
        r = np.hypot(xo[:, 0], xo[:, 2])
        dirs = np.c_[xo[:, 0] / r, xo[:, 2] / r]
    sets_out.write_node_csv(mdir / f"{name}_OUTER_nodes.csv", m, sets.outer, dirs)
    sets_out.plot_sets(m, sets, mdir / f"{name}_SETS.png", name)
    info = {"model": name, "source_inp": str(inp), "element_type": w["element_type"], "rp_id": w["rp_id"],
            "axis": ax, "sets": sets.info, "quality": inp_mesh.tet_quality(m), "quality_repair": rq,
            "components": comp, "mesh_log": m.log}
    (mdir / f"{name}_info.json").write_text(json.dumps(info, indent=2, ensure_ascii=False, default=str),
                                             encoding="utf-8")
    return info


def main():
    global OUT
    cfg_path = Path(sys.argv[1] if len(sys.argv) > 1 else HERE / "config_mesh_sets.json").resolve()
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    base = cfg_path.parent
    OUT = (base / cfg.get("out_dir", "out")).resolve()
    OUT.mkdir(parents=True, exist_ok=True)
    only = set(sys.argv[2:])
    models = sorted(p for p in (base / cfg.get("models_dir", "models")).glob("*")
                    if p.suffix.lower() in CAD_EXT | {".inp"} and (not only or p.stem in only))
    log(f"===== YL mesh + INNER/OUTER : 모델 {len(models)}개 ({', '.join(p.name for p in models)}) =====")
    n_ok = 0
    for src in models:
        name = "".join(c if c.isalnum() or c == "_" else "_" for c in src.stem)
        mdir = OUT / name
        mdir.mkdir(parents=True, exist_ok=True)
        try:
            inp = mesh_model(cfg, src, name, mdir)
            info = sets_model(cfg, inp, name, mdir)
            s, a = info["sets"], info["axis"]
            log(f"[{name}] OK : {info['element_type']} 요소 {info['quality'].get('n_elem')}개, 축 {a['source_axis']} -> +Y, "
                f"원점 이동 {[round(v, 3) for v in a.get('translated_by', [0, 0, 0])]} mm, "
                f"INNER {s['inner_nodes']} 노드, OUTER {s['outer_nodes']} 노드, 스웨징 방향 {s.get('swage_direction')}")
            for wmsg in s.get("warnings", []):
                log(f"[{name}] WARNING {wmsg}")
            n_ok += 1
        except Exception as e:
            log(f"[{name}] 실패: {e}")
            (mdir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
    log(f"===== 끝 : {n_ok}/{len(models)} 성공 -> {OUT} =====")
    return 0 if n_ok == len(models) else 1


if __name__ == "__main__":
    sys.exit(main())
