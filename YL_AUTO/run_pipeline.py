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
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import findexe, inp_mesh, inp_writer, ogden, results, runners, shape_general  # noqa: E402

STAGES = ["mesh", "build", "run", "extract", "report"]
CAD_EXT = {".stp", ".step", ".igs", ".iges", ".x_t", ".x_b", ".catpart", ".prt", ".sat", ".hm"}


LOG_FILE = None


def log(msg):
    line = time.strftime("[%H:%M:%S] ") + msg
    print(line, flush=True)
    if LOG_FILE is not None:
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
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
        if st.get("STATUS") == "OK" and ok:
            return st
        if st.get("STATUS") == "OK":
            log(f"[{m['name']}] 이전 메시 재사용 안 함 ({why}) -> HyperMesh 다시 실행")
    hm = cfg["hypermesh"]
    st = runners.run_hypermesh(hm, resolve(cfg, hm.get("tcl", "hypermesh/YL_V5_batch.tcl")), src, mesh_dir,
                               m["name"], es, cfg["mesh"].get("elem_type", "C3D10H"))
    # [v9] HyperMesh 가 끝까지 실패하면 예비 메셔(gmsh)로 한 번 더 시도
    g_cfg = cfg.get("gmsh_fallback", {})
    if st.get("STATUS") != "OK" and g_cfg.get("enabled", True):
        log(f"[{m['name']}] HyperMesh 메시 실패 ({st.get('MESSAGE', '')}) -> 예비 메셔 gmsh 시도 ...")
        gs = runners.run_gmsh(g_cfg, src, mesh_dir, m["name"], es)
        if gs.get("STATUS") == "OK":
            gs["HM_ERROR"] = st.get("MESSAGE", "")
            log(f"[{m['name']}] 주의: 이 메시는 HyperMesh 가 아니라 gmsh 로 만들었습니다 "
                f"(Tet {gs.get('N_TET')}개, {gs.get('elapsed_s')}s). 글자 등 작은 형상 주변 요소 품질은 "
                f"qc_report.md 의 shape_q_min 으로 확인하세요.")
            return gs
        log(f"[{m['name']}] gmsh 도 실패: {gs.get('MESSAGE', '')}")
        for l in gs.get("LOG_TAIL", []):
            log(f"[{m['name']}]   gmsh| {l}")
    return st


def stage_build(cfg, m, es, tag, tag_dir, mat, dialect):
    gcfg = dict(cfg.get("geometry", {}))
    gcfg.update({k: v for k, v in m.items() if k in ("axis", "center", "inner_tol_mm", "outer_tol_mm",
                                                       "inner_r_max", "outer_r_min", "normal_cos_min", "elsets",
                                                       "inner_mode", "connect_rmax_frac", "shape_mode", "swage_mode")})
    src = tag_dir / "mesh" / f"{m['name']}_hm.inp"
    mesh = inp_mesh.read_inp_mesh(src, gcfg.get("elsets"))
    inp_mesh.check_mesh_sane(mesh, int(cfg["mesh"].get("min_elems", 50)))
    comp = inp_mesh.keep_largest_component(mesh)
    if comp.get("warning"):
        log(f"[{tag}] WARNING {comp['warning']}")
    inp_mesh.check_mesh_sane(mesh, int(cfg["mesh"].get("min_elems", 50)))
    if mesh.family == "tet4" and cfg["mesh"].get("upgrade_tet4", True):
        inp_mesh.upgrade_tet4_to_tet10(mesh)
    inp_mesh.fix_tet10_midside_order(mesh)
    inp_mesh.fix_orientation(mesh)
    shape_mode = str(gcfg.get("shape_mode", "general")).lower()
    if gcfg.get("inner_r_max") is not None or gcfg.get("outer_r_min") is not None:
        shape_mode = "cylinder"      # 반경을 직접 지정한 모델은 예전 방식 그대로
    if shape_mode == "general":
        # [v12] 임의 형상 : 관통 구멍으로 축/원점(0,0,0)/INNER/OUTER 판별
        ax_info, sets = shape_general.setup_general(
            mesh, gcfg.get("axis", "auto"), gcfg.get("center", "auto"), gcfg.get("normal_cos_min", 0.6),
            gcfg.get("inner_tol_mm", 0.5), gcfg.get("outer_tol_mm", 0.5), gcfg.get("inner_mode", "connected"),
            gcfg.get("swage_mode", "auto"))
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
    winfo = inp_writer.write_mesh_inp(run_dir / mesh_file, mesh, sets, dialect, cfg.get("lab_aliases", True))
    jobs = jobs_for(cfg, tag)
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
    info = {"model": m["name"], "elem_size": es, "tag": tag, "source_inp": str(src), "dialect": dialect,
            "rp_id": winfo["rp_id"], "element_type": winfo["element_type"], "axis": ax_info,
            "sets": sets.info, "quality": q, "features": feats, "mesh_log": mesh.log, "components": comp,
            "material": {k: mat[k] for k in ("name", "mu", "alpha", "D", "mu0", "K0")}, "jobs": jobs,
            "combined": combined}
    (tag_dir / "model_info.json").write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        results.plot_sets(mesh, sets, tag_dir / f"{tag}_SETS.png", tag)
    except Exception as e:  # 그림은 부가 기능
        log(f"[{tag}] SETS 그림 생략: {e}")
    return info


def odb_mode(cfg, dialect) -> str:
    """single : 모델당 ODB 1개(모든 스웨징 x 방향) / separate : Job 마다 ODB 1개(예전 방식). ccx 는 separate 만."""
    mode = str(cfg.get("odb_mode", "separate")).lower()
    return "single" if (mode == "single" and dialect == "abaqus") else "separate"


def stage_run_combined(cfg, run_dir, info, stages, force):
    """[v11] 통합 Job 1개 실행 -> ODB 1개 -> CSV 1개 -> 예전 형식의 <job>_RP.csv 여러 개로 분리."""
    cjob, plan = info["combined"]["job"], info["combined"]["plan"]
    st_file = run_dir / f"{cjob}_status.json"
    st = json.loads(st_file.read_text()) if st_file.exists() else {}
    if "run" in stages and (force or not st.get("completed")):
        acfg = dict(cfg["abaqus"])
        acfg["timeout_min"] = float(acfg.get("timeout_min", 120)) * max(1, len(plan))   # Job 1개가 전부를 계산
        st = runners.run_abaqus(acfg, run_dir, cjob)
        st_file.write_text(json.dumps(st, indent=2))
    csv_path = run_dir / f"{cjob}_RP.csv"
    # 중간에 발산해도 그 전 스텝까지는 ODB 에 남아 있으므로 ODB 만 있으면 추출한다
    if "extract" in stages and (run_dir / f"{cjob}.odb").exists() and (force or not csv_path.exists()
                                                                     or "run" in stages):
        ok, _ = runners.extract_odb(cfg["abaqus"], run_dir, cjob, info["rp_id"],
                                    HERE / "abaqus_scripts" / "extract_odb.py")
        st["extracted"] = bool(ok)
        st_file.write_text(json.dumps(st, indent=2))
    parts = results.split_combined_csv(csv_path, plan, run_dir) if csv_path.exists() else {}
    for c in plan:
        pinfo = parts.get(c["job"], {"completed": False, "n_points": 0})
        vst = {"completed": pinfo["completed"], "n_increments": pinfo["n_points"], "cutbacks": None,
               "elapsed_s": st.get("elapsed_s"), "combined_job": cjob,
               "errors": [] if pinfo["completed"] else (st.get("errors") or [f"{c['load_step']} 스텝 미완료"])}
        (run_dir / f"{c['job']}_status.json").write_text(json.dumps(vst, indent=2))
    st["n_cases_done"] = sum(1 for v in parts.values() if v["completed"])
    st["n_cases"] = len(plan)
    return cjob, st


def stage_run_one(cfg, solver, run_dir, j, rp_id, stages, force):
    job = j["job"]
    st_file = run_dir / f"{job}_status.json"
    st = json.loads(st_file.read_text()) if st_file.exists() else {}
    if "run" in stages and (force or not st.get("completed")):
        if solver == "abaqus":
            st = runners.run_abaqus(cfg["abaqus"], run_dir, job)
        else:
            st = runners.run_ccx(cfg.get("ccx", {}), run_dir, job)
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
def _win(p: str) -> str:
    import os
    return os.path.normpath(p) if os.name == "nt" else p


def resolve_programs(cfg, solver, stages):
    """HyperMesh / Abaqus 실행 파일 경로 확정 (config 경로 -> 시작 메뉴 바로가기 -> 기본 위치 -> PATH)."""
    need_hm = "mesh" in stages and any(Path(m["file"]).suffix.lower() != ".inp" for m in model_list(cfg))
    if need_hm:
        exe, how = findexe.find_hmbatch(cfg.get("hypermesh", {}))
        if not exe:
            log("[ERROR] hmbatch.exe 를 못 찾음 (check_env.py 결과 확인)")
            raise SystemExit("[ERROR] hmbatch.exe 를 못 찾았습니다. config 의 hypermesh.exe 에 전체 경로를 적어주세요 "
                             "(예: C:/Program Files/Altair/2019/hm/bin/win64/hmbatch.exe)")
        cfg["hypermesh"]["exe"] = _win(exe)
        log(f"HyperMesh batch: {cfg['hypermesh']['exe']} ({how})")
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
    global LOG_FILE
    LOG_FILE = work / "pipeline_log.txt"
    log(f"===== run_pipeline {a.config}  stage={a.stage}  only={a.only} =====")
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
    for m in model_list(cfg):
        if only and m["name"] not in only:
            continue
        mname = m.get("material", cfg.get("default_material", next(iter(mats))))
        if mname not in mats:
            raise ValueError(f"모델 {m['name']}: 물성 '{mname}' 이 materials 에 없습니다.")
        mat = mats[mname]
        for es in cfg["mesh"].get("elem_sizes", [2.0]):
            tag = f"{m['name']}_E{inp_writer.fmt_num(float(es))}"
            tag_dir = work / tag
            try:
                if "mesh" in stages:
                    log(f"[{tag}] mesh ...")
                    st = stage_mesh(cfg, m, float(es), tag_dir, a.force)
                    log(f"[{tag}] mesh {st.get('STATUS')} {st.get('MESSAGE', '')} "
                        f"tet={st.get('N_TET', '-')} badTria={st.get('BAD_TRIA', '-')}")
                    if st.get("FIRST_TRY"):
                        log(f"[{tag}] 1차(YL_V4) 실패 -> HyperMesh 를 새로 띄워 2차(단순 메시) 자동 재시도함. "
                            f"1차 원인: {st.get('FIRST_TRY')}")
                        for l in st.get("LOG_TAIL_TRY1", [])[-8:]:
                            log(f"[{tag}]   1차| {l}")
                    if st.get("AREA_API", None) == "" and st.get("ALGO") != "GMSH_FALLBACK":
                        log(f"[{tag}] 주의: HyperMesh 에서 면적 함수를 못 써서 작은 면 자동 무시(defeature)가 "
                            f"제대로 안 됐을 수 있습니다 (hm_log.txt 의 'defeature' 줄 확인)")
                    if st.get("ALGO") == "FALLBACK_SIMPLE":
                        log(f"[{tag}] 주의: YL_V4 메시 알고리즘이 실패해 단순 메시(전체 면 Tria -> Tetra)로 대신 만들었습니다. "
                            f"원인: {st.get('V4_ERROR') or 'V4 단계에서 Tetra 0개'}")
                    if st.get("STATUS") != "OK":
                        for l in st.get("LOG_TAIL", []):
                            log(f"[{tag}]   | {l}")
                        log(f"[{tag}] 메시 실패 -> 이 모델은 해석(ODB) 단계로 가지 않습니다. "
                            f"급하면 HyperMesh GUI 에서 직접 메시한 .inp 를 models 폴더에 넣으면 메시 단계를 건너뜁니다.")
                        continue
                info_file = tag_dir / "model_info.json"
                if "build" in stages or not info_file.exists():
                    info = stage_build(cfg, m, float(es), tag, tag_dir, mat, dialect)
                    log(f"[{tag}] build OK: {info['element_type']} n_elem={info['quality']['n_elem']} "
                        f"INNER={info['sets']['inner_nodes']} OUTER={info['sets']['outer_nodes']} "
                        f"jobs={len(info['jobs'])}"
                        + (f" -> 통합 Job 1개 ({info['combined']['job']}.odb)" if info.get("combined") else ""))
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
            except Exception as e:
                log(f"[{tag}] ERROR {e}")
                (tag_dir).mkdir(parents=True, exist_ok=True)
                (tag_dir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")

    if {"run", "extract"} & set(stages):
        npar = int(cfg.get(solver, {}).get("max_parallel", 1))
        tasks = []
        n_fail_shown = 0
        with ThreadPoolExecutor(max_workers=npar) as ex:
            for info in model_infos:
                run_dir = work / info["tag"] / "run"
                if info.get("combined"):
                    tasks.append(ex.submit(stage_run_combined, cfg, run_dir, info, stages, a.force))
                    continue
                for j in info["jobs"]:
                    tasks.append(ex.submit(stage_run_one, cfg, solver, run_dir, j, info["rp_id"], stages, a.force))
            for fut in as_completed(tasks):
                job, st = fut.result()
                log(f"  {job}: completed={st.get('completed')} inc={st.get('n_increments')} "
                    f"t={st.get('elapsed_s')}s extracted={st.get('extracted', '-')} {st.get('errors') or ''}")
                if "n_cases" in st:
                    log(f"    통합 Job(ODB 1개): 하중 케이스 {st['n_cases_done']}/{st['n_cases']} 개 완료")
                if not st.get("completed") and "run" in stages:
                    n_fail_shown += 1
                    if n_fail_shown <= 3:   # 같은 원인이 반복되므로 처음 3개만 자세히
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

    if "report" in stages and model_infos:
        rows = stage_report(cfg, work, model_infos)
        n_ok = sum(r["status"] == "OK" for r in rows)
        log(f"report: {n_ok}/{len(rows)} jobs OK -> {work / 'qc_report.md'}, {work / 'dataset'}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        log("[ERROR] 예외로 중단:\n" + traceback.format_exc())
        sys.exit(1)
