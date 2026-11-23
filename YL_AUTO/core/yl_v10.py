# -*- coding: utf-8 -*-
"""
yl_v10.py : 연구실 YL_V10 자동 메시(HyperMesh BatchMesher 스킨 엔진)를 파이프라인 메시 단계로 실행

  YL_V10.ps1 (hypermesh/YL_V10/) 은 그대로 둔다. 혼자 쓸 때는 YL_V10.bat 더블클릭 (2D 스킨만).
  파이프라인은 이 파일에서
    1) YL_V10.ps1 끝에 들어 있는 HyperMesh 엔진 Tcl 파일들(run_batch.tcl, YL_V9_batch.tcl, YL_V8_batch.tcl,
       YL_V8_qclean.tcl, YL_mesh_stats.tcl)을 꺼내서
    2) hmbatch -tcl run_batch.tcl 로 V9 엔진(BatchMesher)을 돌리고
    3) V9 스킨이 합격 못 하면(fallback_v8=1) 새 hmbatch 프로세스에서 V8 엔진으로 다시 돌린다 (YL_V10.ps1 과 같은 순서)
    4) 2D 스킨이 합격하면 같은 HyperMesh 세션에서 Tetra + Abaqus INP 까지 만든다
       (V10 단독 실행은 2D 만 만들지만, 자동 해석에는 3D 가 필요해서 YL_V9_ALLOW_3D=1 / YL_DO_TETRA=1 로 켬)
  YL_V10.ps1 을 새로 빌드하면 그 파일만 바꿔 넣으면 된다 (엔진은 실행할 때마다 다시 꺼냄).
"""
from __future__ import annotations

import csv
import os
import re
import shutil
from pathlib import Path

from core import runners

ENGINE_FILES = ("run_batch.tcl", "YL_V9_batch.tcl", "YL_V8_batch.tcl", "YL_V8_qclean.tcl", "YL_mesh_stats.tcl")


def extract_engine(ps1: Path, dest: Path, tet_growth: float | None = None) -> dict:
    """YL_V10.ps1 의 $YLEngineFiles['이름'] = @' ... '@ 블록을 dest 폴더에 파일로 쓴다.
    tet_growth 를 주면 V9/V8 엔진의 Tetra 성장률(TET_GROWTH)만 그 값으로 바꾼다 (원본 ps1 은 안 건드림)."""
    text = Path(ps1).read_text(encoding="utf-8-sig")
    pat = re.compile(r"^\$YLEngineFiles\['([^']+)'\]\s*=\s*@'\r?\n(.*?)\r?\n'@\s*$", re.M | re.S)
    files = {m.group(1): m.group(2) for m in pat.finditer(text)}
    missing = [f for f in ENGINE_FILES if f not in files]
    if missing:
        raise RuntimeError(f"{ps1} 에서 엔진 파일을 찾지 못함: {missing} (YL_V10.ps1 이 맞는지 확인)")
    dest.mkdir(parents=True, exist_ok=True)
    changed = []
    for name, body in files.items():
        body = body.replace("\r\n", "\n")
        if tet_growth is not None and name in ("YL_V9_batch.tcl", "YL_V8_batch.tcl"):
            body, n = re.subn(r"^(set TET_GROWTH\s+)[0-9.]+", lambda mm: f"{mm.group(1)}{tet_growth}", body, flags=re.M)
            if n:
                changed.append(name)
        # PowerShell 과 같게 UTF-8 (BOM 없음)
        (dest / name).write_bytes(body.encode("utf-8"))
    return {"files": sorted(files), "growth_changed": changed}


def _read_summary(csv_path: Path) -> list[dict]:
    if not csv_path.exists():
        return []
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def _run_engine(hm_cfg, engine_dir, job_file, out_dir, log_dir, version, elem_size, elem_type, aspect):
    env_add = {
        "YL_VERSION": version, "YL_JOB": str(job_file), "YL_LIST": str(job_file),
        "YL_OUT_DIR": runners._p(out_dir), "YL_LOG_DIR": runners._p(log_dir),
        "YL_ELEM_SIZE": f"{float(elem_size):g}", "YL_ELEM_TYPE": elem_type, "YL_ASPECT": f"{float(aspect):g}",
        "YL_DO_TETRA": "1", "YL_V9_ALLOW_3D": "1", "YL_EXPORT_INP": "1",
        "YL_FILLET_ROWS": str(hm_cfg.get("v10_fillet_rows", "altair")),
        "YL_V8_FALLBACK": "1" if hm_cfg.get("v10_v8_fallback", True) else "0",
    }
    old = {k: os.environ.get(k) for k in env_add}
    os.environ.update(env_add)
    try:
        cmd = [hm_cfg["exe"]] + list(hm_cfg.get("args", ["-tcl"])) + [runners._p(engine_dir / "run_batch.tcl")]
        rc, dt = runners._run(cmd, log_dir, log_dir / f"hm_console_{version}.log",
                              float(hm_cfg.get("timeout_min", 60)) * 60)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    rows = _read_summary(log_dir / "batch_summary.csv")
    # 엔진별 로그 보존
    for name in ("batch_log.txt", "batch_summary.csv"):
        f = log_dir / name
        if f.exists():
            shutil.copy(f, log_dir / f"{Path(name).stem}_{version}{Path(name).suffix}")
    return rows, rc, dt


def run_v10(hm_cfg: dict, ps1: Path, geom: Path, out_dir: Path, base: str, elem_size: float,
            elem_type: str = "C3D10H", fixed_size: bool = False) -> dict:
    """반환 : 파이프라인 hm_status 형식 dict (STATUS, N_TET, N_TRIA, INP_FILE, ALGO, MESSAGE ...)"""
    out_dir.mkdir(parents=True, exist_ok=True)
    log_dir = out_dir / "_logs"
    log_dir.mkdir(exist_ok=True)
    status_file = out_dir / "hm_status.txt"
    for f in (status_file, log_dir / "batch_summary.csv"):
        if f.exists():
            f.unlink()
    growth = 1.0 if fixed_size else None
    ex = extract_engine(ps1, out_dir / "_engine", growth)
    job = log_dir / "_job.txt"
    job.write_text(f"{runners._p(geom)}\t{base}\t{elem_type}\n", encoding="utf-8")
    aspect = float(hm_cfg.get("v10_aspect", 5.0))

    rows, rc, dt = _run_engine(hm_cfg, out_dir / "_engine", job, out_dir, log_dir, "V9", elem_size, elem_type, aspect)
    row = rows[0] if rows else {}
    engine = "V9"
    total = dt
    first_msg = ""
    if row and str(row.get("fallback_v8", "")).strip() == "1":
        first_msg = row.get("note", "")
        rows8, rc8, dt8 = _run_engine(hm_cfg, out_dir / "_engine", job, out_dir, log_dir, "V8",
                                      elem_size, elem_type, aspect)
        total += dt8
        if rows8:
            row, rc, engine = rows8[0], rc8, "V9>V8"

    n_tet = int(float(row.get("n_tet") or 0)) if row else 0
    inp = (row.get("inp") or "").strip() if row else ""
    out_inp = out_dir / f"{base}_hm.inp"
    if out_inp.exists():
        out_inp.unlink()
    if inp and Path(inp).exists() and n_tet > 0:
        shutil.copy(inp, out_inp)
    ok = bool(row) and row.get("status") == "OK" and out_inp.exists()
    if not row:
        msg = (f"YL_V10 엔진 결과(batch_summary.csv)가 없음 (hmbatch rc={rc}). "
               f"{log_dir.name}/hm_console_V9.log, batch_log_V9.txt 확인")
    elif ok:
        msg = ""
    else:
        msg = f"YL_V10({engine}) : {row.get('note') or row.get('status')}"
        if first_msg and engine == "V9>V8":
            msg = f"V9(BatchMesher): {first_msg} / V8: {row.get('note') or row.get('status')}"
    st = {
        "STATUS": "OK" if ok else "FAIL", "MESSAGE": msg, "ALGO": f"YL_V10_{engine}",
        "ENGINE": engine, "MESHER": row.get("mesher", ""), "ELEM_SIZE": elem_size,
        "N_TRIA": row.get("n_2d", ""), "BAD_TRIA": row.get("bad_aspect_2d", ""),
        "FREE_EDGES": row.get("free_edges", ""), "T_EDGES": row.get("t_edges", ""),
        "PASS2D": row.get("pass2d", ""), "N_TET": n_tet, "BAD_TET_COLLAPSE": row.get("bad_tet", ""),
        "Q_MEAN": row.get("q_mean", ""), "TET_ASP_P95": row.get("tet_asp_p95", ""),
        "TET_COL_LT015": row.get("tet_col_lt015", ""), "DEFEATURED": row.get("defeatured", ""),
        "FIXED_SIZE": 1 if fixed_size else 0, "TET_GROWTH": growth if growth is not None else "engine default",
        "HM_FILE": row.get("output", ""), "INP_FILE": runners._p(out_inp) if ok else "",
        "returncode": rc, "elapsed_s": round(total, 1),
    }
    status_file.write_text("".join(f"{k}={v}\n" for k, v in st.items()), encoding="utf-8")
    st["LOG_TAIL"] = runners._tail(log_dir / "batch_log.txt", 20)
    return st
