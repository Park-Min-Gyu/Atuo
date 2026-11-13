# -*- coding: utf-8 -*-
"""
runners.py : 외부 프로그램 실행 (HyperMesh 배치 / Abaqus / CalculiX) 와 결과 상태 판정
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

IS_WIN = os.name == "nt"


def _p(path) -> str:
    """Tcl/Abaqus 에 넘길 경로는 슬래시로 통일"""
    return os.path.abspath(str(path)).replace("\\", "/")


def _run(cmd, cwd, log_file, timeout_s, shell=False):
    t0 = time.time()
    with open(log_file, "w", encoding="utf-8", errors="ignore") as lf:
        lf.write(f"# cmd: {cmd}\n# cwd: {cwd}\n")
        lf.flush()
        try:
            p = subprocess.run(cmd, cwd=str(cwd), stdout=lf, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, timeout=timeout_s, shell=shell)
            rc = p.returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
        except FileNotFoundError as e:
            rc = f"not found: {e}"
    return rc, time.time() - t0


# ---------------------------------------------------------------------------
# HyperMesh
# ---------------------------------------------------------------------------
def run_hypermesh(hm_cfg: dict, tcl_script: Path, geom: Path, out_dir: Path, base: str, elem_size: float,
                  elem_type: str = "C3D10H") -> dict:
    """[B6] 1차: YL_V4 알고리즘. HyperMesh 가 죽거나 실패하면
       2차: SKIP_V4=1 (YL_V4 를 건너뛰고 단순 메시) 로 hmbatch 를 새로 띄워 자동 재시도."""
    st = _run_hypermesh_once(hm_cfg, tcl_script, geom, out_dir, base, elem_size, elem_type, skip_v4=False)
    if st.get("STATUS") == "OK" or not hm_cfg.get("fallback_simple", True) or not hm_cfg.get("retry_simple", True):
        return st
    if str(st.get("MESSAGE", "")).startswith("hm_status.txt 없음"):
        return st  # 스크립트가 아예 시작 못 함 (경로/라이선스 문제) -> 재시도해도 같음
    if st.get("ALGO") == "FALLBACK_SIMPLE":
        return st  # 1차에서 이미 단순 메시(fallback)까지 해 봤음 -> 같은 것을 또 돌릴 필요 없음
    # 1차 로그 보존
    for name in ("hm_log.txt", "hm_console.log", "hm_status.txt", "command.tcl"):
        f = out_dir / name
        if f.exists():
            dst = out_dir / (f.stem + "_try1" + f.suffix)
            try:
                if dst.exists():
                    dst.unlink()
                f.rename(dst)
            except OSError:
                pass
    first_msg = st.get("MESSAGE", "")
    first_tail = st.get("LOG_TAIL", [])
    st2 = _run_hypermesh_once(hm_cfg, tcl_script, geom, out_dir, base, elem_size, elem_type, skip_v4=True)
    st2["FIRST_TRY"] = first_msg
    st2["LOG_TAIL_TRY1"] = first_tail
    if st2.get("STATUS") == "OK":
        st2["ALGO"] = "FALLBACK_SIMPLE"
        st2["V4_ERROR"] = first_msg
    else:
        st2["MESSAGE"] = f"1차(YL_V4): {first_msg} / 2차(단순 메시): {st2.get('MESSAGE', '')}"
        st2["LOG_TAIL"] = first_tail + st2.get("LOG_TAIL", [])
    return st2


def gmsh_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("gmsh") is not None


def run_gmsh(g_cfg: dict, geom: Path, out_dir: Path, base: str, elem_size: float) -> dict:
    """[v9] 예비 메셔: HyperMesh 가 끝까지 실패했을 때 gmsh 로 Tet4 INP 를 만든다.
    별도 프로세스로 돌려서 gmsh 가 죽거나 멈춰도 파이프라인은 계속된다."""
    from core.gmsh_mesher import GMSH_EXT
    if Path(geom).suffix.lower() not in GMSH_EXT:
        return {"STATUS": "FAIL", "MESSAGE": f"gmsh 는 {Path(geom).suffix} 형식을 못 읽음 (STEP/IGES 만 가능)"}
    if not gmsh_available():
        return {"STATUS": "FAIL", "MESSAGE": "gmsh 미설치 -> 'pip install gmsh' 하면 HyperMesh 실패 시 자동으로 대신 메시합니다"}
    out_dir.mkdir(parents=True, exist_ok=True)
    out_inp = out_dir / f"{base}_hm.inp"
    tmp_inp = out_dir / f"{base}_gmsh.inp"
    if tmp_inp.exists():
        tmp_inp.unlink()
    script = Path(__file__).resolve().parent / "gmsh_mesher.py"
    cmd = [sys.executable, str(script), str(geom), str(tmp_inp), str(elem_size),
           str(float(g_cfg.get("size_min_ratio", 0.15)))]
    log_file = out_dir / "gmsh_console.log"
    rc, dt = _run(cmd, out_dir, log_file, float(g_cfg.get("timeout_min", 30)) * 60)
    res = {}
    for line in _tail(log_file, 5):
        if "GMSH_RESULT " in line:
            try:
                res = json.loads(line.split("GMSH_RESULT ", 1)[1])
            except ValueError:
                pass
    n_tet = int(res.get("n_tet", 0) or 0)
    ok = bool(res.get("ok")) and tmp_inp.exists() and n_tet >= int(g_cfg.get("min_elems", 50))
    st = {"STATUS": "OK" if ok else "FAIL", "ALGO": "GMSH_FALLBACK", "ELEM_SIZE": elem_size, "N_TET": n_tet,
          "N_TRIA": 0, "BAD_TRIA": 0, "INP_FILE": _p(out_inp) if ok else "", "returncode": rc,
          "elapsed_s": round(dt, 1),
          "MESSAGE": "" if ok else f"gmsh 실패 (rc={rc}): {res.get('error') or 'gmsh_console.log 확인'}"}
    if ok:
        if out_inp.exists():
            out_inp.unlink()
        tmp_inp.rename(out_inp)
        # 다음 실행 때 이 메시를 재사용할 수 있게 상태 파일 기록 (HyperMesh 실패 기록은 _hmfail 로 보존)
        status_file = out_dir / "hm_status.txt"
        if status_file.exists():
            keep = out_dir / "hm_status_hmfail.txt"
            if keep.exists():
                keep.unlink()
            status_file.rename(keep)
        status_file.write_text("".join(f"{k}={v}\n" for k, v in st.items()), encoding="utf-8")
    else:
        st["LOG_TAIL"] = _tail(log_file, 8)
    return st


def tet_count_ok(st: dict, min_ratio: float = 0.25, min_abs: int = 50) -> tuple[bool, str]:
    """[v8] hm_status 의 N_TET 이 표면 Tria 수에 비해 충분한지 검사.
    (예전에는 N_TET=3 이어도 STATUS=OK 로 통과해서 build 단계에서 엉뚱한 오류가 났음)"""
    try:
        n_tet = int(float(st.get("N_TET", 0) or 0))
        n_tria = int(float(st.get("N_TRIA", 0) or 0))
    except ValueError:
        return False, f"N_TET/N_TRIA 값을 읽을 수 없음: {st.get('N_TET')}/{st.get('N_TRIA')}"
    need = max(int(min_abs), int(float(min_ratio) * n_tria))
    if n_tet < need:
        return False, (f"Tetra 가 {n_tet}개뿐 (표면 Tria {n_tria}개 기준 최소 {need}개 필요) -> "
                       f"tetmesh 실패. hm_log.txt 의 'tetmesh <...>' 줄 확인")
    return True, ""


def _run_hypermesh_once(hm_cfg, tcl_script, geom, out_dir, base, elem_size, elem_type, skip_v4=False) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    status_file = out_dir / "hm_status.txt"
    if status_file.exists():
        status_file.unlink()
    wrapper = out_dir / "hm_wrapper.tcl"
    wrapper.write_text(
        "# auto-generated by run_pipeline.py\n"
        "set ::YL(BATCH) 1\n"
        f'set ::YL(INPUT) "{_p(geom)}"\n'
        f'set ::YL(OUT_DIR) "{_p(out_dir)}"\n'
        f'set ::YL(BASE_NAME) "{base}"\n'
        f"set ::YL(ELEM_SIZE) {elem_size}\n"
        f'set ::YL(ELEM_TYPE) "{elem_type}"\n'
        f'set ::YL(LOG) "{_p(out_dir / "hm_log.txt")}"\n'
        f'set ::YL(STATUS_FILE) "{_p(status_file)}"\n'
        f"set ::YL(FALLBACK) {1 if hm_cfg.get('fallback_simple', True) else 0}\n"
        f"set ::YL(SKIP_V4) {1 if skip_v4 else 0}\n"
        f"set ::YL(DEFEATURE) {1 if hm_cfg.get('defeature', True) else 0}\n"
        f"set ::YL(SMALL_W_RATIO) {float(hm_cfg.get('small_w_ratio', 0.25))}\n"
        f"set ::YL(SMALL_MAXLEN_RATIO) {float(hm_cfg.get('small_maxlen_ratio', 5.0))}\n"
        f"set ::YL(TET_MIN_RATIO) {float(hm_cfg.get('tet_min_ratio', 0.25))}\n"
        f'if {{[catch {{source "{_p(tcl_script)}"}} yl_top_err]}} {{\n'
        '    catch { yl_log "UNCAUGHT Tcl error: $yl_top_err" ; yl_log "errorInfo: $::errorInfo" }\n'
        f'    catch {{ set fh [open "{_p(status_file)}" w]; puts $fh "STATUS=FAIL"; '
        'puts $fh "MESSAGE=Tcl error: [string map {\\n { }} $yl_top_err]"; close $fh }\n'
        '    catch { *quit 1 }\n'
        '}\n', encoding="utf-8")
    exe = hm_cfg["exe"]
    args = hm_cfg.get("args", ["-tcl"])
    cmd = [exe] + args + [_p(wrapper)]
    rc, dt = _run(cmd, out_dir, out_dir / "hm_console.log", hm_cfg.get("timeout_min", 60) * 60)
    st = read_kv(status_file)
    st["returncode"] = rc
    st["elapsed_s"] = round(dt, 1)
    if not st.get("STATUS"):
        st["STATUS"] = "FAIL"
        st["MESSAGE"] = f"hm_status.txt 없음 (hmbatch 가 스크립트 시작 전에 종료, rc={rc}). hm_console.log 확인"
    elif st.get("STATUS") == "RUNNING":
        st["STATUS"] = "FAIL"
        st["MESSAGE"] = (f"HyperMesh 가 '{st.get('STAGE')}' 단계 도중 비정상 종료 (rc={rc}, 프로그램 자체 크래시). "
                         "hm_log.txt / hm_console.log 확인")
    elif st.get("STATUS") == "OK":
        ok, why = tet_count_ok(st, hm_cfg.get("tet_min_ratio", 0.25))
        if not ok:
            st["STATUS"] = "FAIL"
            st["MESSAGE"] = why
    st["LOG_TAIL"] = _tail(out_dir / "hm_log.txt", 15) + _tail(out_dir / "hm_console.log", 15)
    st["TRY"] = "2 (SKIP_V4)" if skip_v4 else "1 (YL_V4)"
    return st


def _tail(path: Path, n: int) -> list:
    try:
        return [f"{path.name}: {l}" for l in path.read_text(encoding="utf-8", errors="ignore").splitlines()[-n:]]
    except OSError:
        return []


def read_kv(path: Path) -> dict:
    out = {}
    if Path(path).exists():
        for line in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


# ---------------------------------------------------------------------------
# Abaqus
# ---------------------------------------------------------------------------
def abaqus_status(run_dir: Path, job: str) -> dict:
    sta = run_dir / f"{job}.sta"
    msg = run_dir / f"{job}.msg"
    dat = run_dir / f"{job}.dat"
    odb = run_dir / f"{job}.odb"
    s = {"odb": odb.exists(), "completed": False, "errors": [], "n_warnings": 0, "n_increments": 0,
         "cutbacks": 0}
    if sta.exists():
        txt = sta.read_text(errors="ignore")
        s["completed"] = "COMPLETED SUCCESSFULLY" in txt
        if "HAS NOT BEEN COMPLETED" in txt:
            s["errors"].append("THE ANALYSIS HAS NOT BEEN COMPLETED")
        rows = [l for l in txt.splitlines() if re.match(r"^\s+\d+\s+\d+", l)]
        s["n_increments"] = len(rows)
        s["cutbacks"] = sum(1 for l in rows if l.split()[2].endswith("U")) if rows else 0
    for f in (dat, msg):
        if f.exists():
            t = f.read_text(errors="ignore")
            for m in re.finditer(r"\*\*\*ERROR:(.{0,200})", t):
                s["errors"].append(m.group(1).strip().replace("\n", " "))
            s["n_warnings"] += t.count("***WARNING")
    s["errors"] = s["errors"][:5]
    return s


def _abaqus_launch_errors(run_dir: Path, job: str) -> list:
    """[v10] <job>.runlog (abaqus 명령이 화면에 찍은 내용) 에서 오류 줄을 찾는다.
    명령 옵션 오류/라이선스 오류/INP 문법 오류는 .dat/.msg 가 생기기 전에 여기에만 나온다."""
    f = run_dir / f"{job}.runlog"
    try:
        lines = [l.strip() for l in f.read_text(encoding="utf-8", errors="ignore").splitlines()
                 if l.strip() and not l.startswith("# ")]
    except OSError:
        return []
    bad = [l for l in lines if re.search(r"error|not recognized|not valid|unknown|cannot|unable|license|"
                                         r"찾을 수 없|인식|아닙니다", l, re.I)]
    return (bad or lines[-6:])[:8]


def _abaqus_started(run_dir: Path, job: str) -> bool:
    """Abaqus 가 실제로 Job 을 시작했는지 (.dat/.sta/.msg/.odb/.com 중 하나라도 새로 생겼는지)"""
    return any((run_dir / f"{job}{ext}").exists() for ext in (".dat", ".sta", ".msg", ".odb", ".com"))


def run_abaqus(abq_cfg: dict, run_dir: Path, job: str) -> dict:
    cmd_name = abq_cfg.get("cmd", "abaqus")
    exe = f'"{cmd_name}"' if " " in cmd_name else cmd_name
    base = [exe, f"job={job}", f"input={job}.inp"]
    extra = [f"cpus={abq_cfg.get('cpus', 4)}"]
    if abq_cfg.get("memory"):
        extra.append(f"memory={abq_cfg['memory']}")
    if abq_cfg.get("scratch"):
        extra.append(f"scratch={abq_cfg['scratch']}")
    # [v10] 예전 코드는 'ask_continue=off' 를 붙였는데 Abaqus 에 그런 옵션은 없다
    #       (이전 결과 파일을 묻지 않고 덮어쓰는 옵션은 ask_delete=OFF).
    #       옵션 오류면 Abaqus 는 Job 을 시작도 안 하고 2초 만에 끝난다.
    tries = [base + extra + ["interactive", "ask_delete=OFF"],
             base + ["interactive", "ask_delete=OFF"],      # cpus/memory 옵션이 문제일 때
             base + ["interactive"]]
    for ext in (".lck",):   # 이전 실행이 죽으면서 남긴 잠금 파일 (있으면 Abaqus 가 바로 종료)
        try:
            (run_dir / f"{job}{ext}").unlink()
        except OSError:
            pass
    timeout_s = abq_cfg.get("timeout_min", 120) * 60
    rc, dt, used = None, 0.0, ""
    launch_notes = []
    for k, parts in enumerate(tries, 1):
        used = " ".join(parts)
        rc, dt = _run(used, run_dir, run_dir / f"{job}.runlog", timeout_s, shell=True)
        if rc == "timeout":
            subprocess.run(f"{exe} terminate job={job}", cwd=str(run_dir), shell=True)
            break
        if _abaqus_started(run_dir, job):
            break
        # Job 파일이 하나도 안 생김 = abaqus 명령 자체가 거부됨 -> 옵션을 줄여 다시
        launch_notes.append(f"시도{k} [{used}] -> " + " / ".join(_abaqus_launch_errors(run_dir, job)[:3]))
        try:
            keep = run_dir / f"{job}.runlog_try{k}"
            if keep.exists():
                keep.unlink()
            (run_dir / f"{job}.runlog").rename(keep)
        except OSError:
            pass
    s = abaqus_status(run_dir, job)
    s["returncode"] = rc
    s["elapsed_s"] = round(dt, 1)
    s["command"] = used
    s["started"] = _abaqus_started(run_dir, job)
    if launch_notes:
        s["launch_notes"] = launch_notes
    if not s["completed"]:
        s["runlog"] = _abaqus_launch_errors(run_dir, job)
        if not s["errors"]:
            s["errors"] = (["Abaqus 가 Job 을 시작하지 못함 (명령/라이선스 문제)"] if not s["started"] else []) + \
                          s["runlog"][:4]
    return s


def extract_odb(abq_cfg: dict, run_dir: Path, job: str, rp_id: int, script: Path) -> tuple[bool, str]:
    cmd_name = abq_cfg.get("cmd", "abaqus")
    exe = f'"{cmd_name}"' if " " in cmd_name else cmd_name
    out_csv = run_dir / f"{job}_RP.csv"
    cmd = f'{exe} python "{_p(script)}" "{job}.odb" "{out_csv.name}" {rp_id}'
    rc, _ = _run(cmd, run_dir, run_dir / f"{job}.extractlog", 1800, shell=True)
    ok = out_csv.exists() and out_csv.stat().st_size > 50
    return ok, str(out_csv)


# ---------------------------------------------------------------------------
# CalculiX (Abaqus 가 없는 환경에서 INP/흐름/데이터 검증용)
# ---------------------------------------------------------------------------
def run_ccx(ccx_cfg: dict, run_dir: Path, job: str) -> dict:
    env = dict(os.environ, OMP_NUM_THREADS=str(ccx_cfg.get("cpus", 2)))
    t0 = time.time()
    with open(run_dir / f"{job}.runlog", "w") as lf:
        try:
            p = subprocess.run([ccx_cfg.get("cmd", "ccx"), "-i", job], cwd=str(run_dir), stdout=lf,
                               stderr=subprocess.STDOUT, timeout=ccx_cfg.get("timeout_min", 60) * 60, env=env)
            rc = p.returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
    log = (run_dir / f"{job}.runlog").read_text(errors="ignore")
    s = {"returncode": rc, "elapsed_s": round(time.time() - t0, 1),
         "completed": ("Job finished" in log) and rc == 0,
         "errors": re.findall(r"\*ERROR.{0,160}", log)[:5],
         "n_increments": len(re.findall(r"increment \d+ attempt", log)),
         "cutbacks": log.count("attempt 2")}
    return s


def parse_ccx_dat(dat: Path, out_csv: Path, step_periods=(1.0, 1.0)) -> bool:
    """ccx .dat 의 LOAD_RP 변위/반력을 Abaqus 추출 CSV 와 같은 형식으로 변환."""
    if not dat.exists():
        return False
    U, F = {}, {}
    cur = None
    t = None
    for line in dat.read_text(errors="ignore").splitlines():
        m = re.match(r"\s*(displacements|forces) .* for set LOAD_RP and time\s+(\S+)", line)
        if m:
            cur = U if m.group(1) == "displacements" else F
            t = float(m.group(2))
            continue
        if cur is not None:
            v = line.split()
            if len(v) == 4:
                try:
                    cur[t] = [float(x) for x in v[1:]]
                except ValueError:
                    cur = None
                cur = None
    times = sorted(set(U) & set(F))
    if not times:
        return False
    with open(out_csv, "w") as f:
        f.write("# source=ccx_dat\nstep,step_time,total_time,U1,U2,U3,RF1,RF2,RF3\n")
        f.write("Step-1_SWAGE,0,0,0,0,0,0,0,0\n")
        t1 = step_periods[0]
        for tt in times:
            step = "Step-1_SWAGE" if tt <= t1 + 1e-9 else "Step-2_LOAD"
            st = tt if tt <= t1 + 1e-9 else tt - t1
            f.write(f"{step},{st:.8g},{tt:.8g}," + ",".join(f"{v:.10g}" for v in U[tt] + F[tt]) + "\n")
    return True
