# -*- coding: utf-8 -*-
"""
inp_writer.py
  (1) 메시 전용 INP  : *Node / LOAD_RP(0,0,0) / *Element(RUBBER) / INNER / OUTER
  (2) 해석 Job INP    : 메시 *Include + 커플링 + Ogden N=3 + Step-1 스웨징 + Step-2 정특성 하중
  dialect = "abaqus" (실사용) / "ccx" (CalculiX 대체 검증용)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

ABQ_TYPE = {"abaqus": {"tet10": "C3D10H", "tet4": "C3D4H", "hex8": "C3D8H"},
            "ccx": {"tet10": "C3D10", "tet4": "C3D4", "hex8": "C3D8"}}
DIR_DOF = {"X": 1, "Y": 2, "Z": 3}


def fmt_num(v: float) -> str:
    """파일명용 숫자: +1.0 -> p1p0, -2.0 -> m2p0, 0 -> 0p0"""
    s = f"{abs(v):.1f}".replace(".", "p")
    if v > 1e-12:
        return "p" + s
    if v < -1e-12:
        return "m" + s
    return s


def job_name(tag: str, sw: float, direction: str, sign: int) -> str:
    return f"{tag}_SW{fmt_num(sw)}_{direction}{'P' if sign > 0 else 'M'}"


def _write_ids(fh, ids, per_line=16):
    ids = list(map(int, ids))
    for i in range(0, len(ids), per_line):
        fh.write(", ".join(str(n) for n in ids[i:i + per_line]) + "\n")


def write_mesh_inp(path: Path, mesh, sets, dialect: str = "abaqus", lab_aliases: bool = True) -> dict:
    etype = ABQ_TYPE[dialect][mesh.family]
    rp_id = int(mesh.node_ids.max()) + 1
    eid = mesh.elem_ids
    with open(path, "w", encoding="ascii") as fh:
        fh.write("** YL_AUTO mesh-only file (no material / no step)\n")
        fh.write("** axis = +Y (radial plane = XZ), LOAD_RP = (0,0,0)\n")
        fh.write(f"** element family = {mesh.family} -> {etype}\n")
        fh.write("*Node\n")
        for nid, (x, y, z) in zip(mesh.node_ids, mesh.xyz):
            fh.write(f"{int(nid)}, {x:.8g}, {y:.8g}, {z:.8g}\n")
        fh.write("*Node, nset=LOAD_RP\n")
        fh.write(f"{rp_id}, 0., 0., 0.\n")
        fh.write(f"*Element, type={etype}, elset=RUBBER\n")
        nid = mesh.node_ids
        for k in range(len(eid)):
            vals = [int(eid[k])] + [int(nid[j]) for j in mesh.conn[k]]
            # 한 줄 최대 16개 규칙 (C3D10 = 11개라 한 줄)
            fh.write(", ".join(map(str, vals)) + "\n")
        fh.write("*Nset, nset=INNER\n")
        _write_ids(fh, nid[sets.inner])
        fh.write("*Nset, nset=OUTER\n")
        _write_ids(fh, nid[sets.outer])
        if lab_aliases and dialect == "abaqus":
            # 기존 연구실 실행기 셋 이름(rp / rp_node / sw)과 호환
            fh.write("** --- lab runner aliases ---\n")
            fh.write("*Nset, nset=rp\nLOAD_RP\n*Nset, nset=rp_node\nINNER\n*Nset, nset=sw\nOUTER\n")
    return {"rp_id": rp_id, "element_type": etype}


def _material_block(mat: dict, dialect: str) -> str:
    mu, a, D = mat["mu"], mat["alpha"], list(mat["D"])
    if dialect == "ccx":
        # CalculiX 는 D2/D3 = 0 을 '기본값 사용'으로 처리 -> Abaqus 와 같게(항 없음) 하려고 큰 값 사용
        D = [D[0]] + [v if v > 0 else 1e6 for v in D[1:]]
    s = f"*Material, name={mat['name']}\n*Hyperelastic, ogden, n=3\n"
    s += ", ".join(f"{v:.10g}" for v in (mu[0], a[0], mu[1], a[1], mu[2], a[2], D[0], D[1])) + "\n"
    s += f"{D[2]:.10g}\n"
    return s


def _outputs(dialect: str, field_freq: int) -> str:
    if dialect == "abaqus":
        return (f"*Output, field, frequency={field_freq}\n*Node Output\nU, RF\n"
                "*Element Output, directions=YES\nS, LE\n"
                "*Output, history, frequency=1\n*Node Output, nset=LOAD_RP\nU1, U2, U3, RF1, RF2, RF3\n"
                "*Node Print, nset=LOAD_RP, summary=NO, totals=NO, frequency=1\nU1, U2, U3, RF1, RF2, RF3\n")
    return "*Node Print, nset=LOAD_RP\nU\n*Node Print, nset=LOAD_RP\nRF\n*Node File\nU\n"


def _ccx_controls(dialect, sc):
    """CalculiX 전용 수렴 판정 완화 (steps.ccx_controls). Abaqus INP 에는 아무것도 안 씀."""
    cc = sc.get("ccx_controls") if dialect == "ccx" else None
    if not cc:
        return ""
    out = ""
    if cc.get("field"):
        out += "*Controls, Parameters=field\n" + ", ".join(str(v) for v in cc["field"]) + "\n"
    if cc.get("time_incrementation"):
        out += "*Controls, Parameters=time incrementation\n" + ", ".join(str(v) for v in cc["time_incrementation"]) + "\n"
    return out


def _swage_dirs(mesh, sets):
    """OUTER 절점별 스웨징 방향(XZ 평면, 바깥쪽 +). 일반 형상은 법선 방향, 원형은 반경 방향."""
    d = getattr(sets, "outer_dir", None)
    if d is not None:
        return d[:, 0], d[:, 1]
    xo = mesh.xyz[sets.outer]
    r = np.hypot(xo[:, 0], xo[:, 2])
    return xo[:, 0] / r, xo[:, 2] / r


def write_job_inp(path: Path, mesh_file: str, mesh, sets, mat: dict, sw: float, direction: str, sign: int,
                  amp: float, rp_id: int, dialect: str = "abaqus", steps_cfg: dict | None = None,
                  sw_outward_positive: bool = True, title: str = "",
                  inner_constraint: str = "rigid_body") -> None:
    sc = {"swage_inc": [0.1, 1.0, 1e-8, 0.25], "load_inc": [0.02, 1.0, 1e-8, 0.05], "max_inc": 2000,
          "field_freq": 5}
    if steps_cfg:
        sc.update(steps_cfg)
    nl = "NLGEOM=YES" if dialect == "abaqus" else "NLGEOM"
    fh = open(path, "w", encoding="ascii")
    w = fh.write
    w("*Heading\n")
    w(f"YL_AUTO {title} SW={sw:+.3f}mm LOAD={direction}{'+' if sign > 0 else '-'} {amp:g}mm\n")
    w("** units: mm, N, MPa\n")
    w(f"*Include, input={mesh_file}\n")
    if dialect == "abaqus":
        if inner_constraint == "coupling":
            w("*Surface, type=NODE, name=INNER_SURF\nINNER, 1.\n")
            w("*Coupling, constraint name=CP_INNER, ref node=LOAD_RP, surface=INNER_SURF\n*Kinematic\n")
        else:
            # 연구실 기존 INP(modeling_AN_M1.inp)와 같은 방식
            w("*Rigid Body, ref node=LOAD_RP, tie nset=INNER\n")
        w(f"*Solid Section, elset=RUBBER, material={mat['name']}\n,\n")
    else:
        w(f"*Node, nset=LOAD_RP_ROT\n{rp_id + 1}, 0., 0., 0.\n")
        w(f"*Rigid Body, nset=INNER, ref node={rp_id}, rot node={rp_id + 1}\n")
        w(f"*Solid Section, elset=RUBBER, material={mat['name']}\n")
    w(_material_block(mat, dialect))

    # ---- Step-1 : 스웨징 (OUTER 반경방향 변위, XZ 평면) ----
    s1 = sc["swage_inc"]
    w(f"** ---------------- Step-1 : SWAGE {sw:+.3f} mm (radial, XZ plane) ----------------\n")
    w(f"*Step, name=Step-1_SWAGE, {nl}, inc={sc['max_inc']}\n" if dialect == "abaqus"
      else f"*Step, {nl}, INC={sc['max_inc']}\n")
    w("*Static\n" + ", ".join(f"{v:g}" for v in s1) + "\n")
    w(_ccx_controls(dialect, sc))
    w("*Boundary\n")
    if dialect == "abaqus":
        w("LOAD_RP, 1, 6, 0.\n")
    else:
        w("LOAD_RP, 1, 3, 0.\nLOAD_RP_ROT, 1, 3, 0.\n")
    if abs(sw) < 1e-12:
        w("OUTER, 1, 3, 0.\n")
    else:
        sgn = 1.0 if sw_outward_positive else -1.0
        dx, dz = _swage_dirs(mesh, sets)
        ux = sgn * sw * dx
        uz = sgn * sw * dz
        for nid, a, b in zip(mesh.node_ids[sets.outer], ux, uz):
            w(f"{int(nid)}, 1, 1, {a:.8g}\n{int(nid)}, 2, 2, 0.\n{int(nid)}, 3, 3, {b:.8g}\n")
    w(_outputs(dialect, sc["field_freq"]))
    w("*End Step\n")

    # ---- Step-2 : 정특성 하중 (LOAD_RP 변위 제어) ----
    s2 = sc["load_inc"]
    dof = DIR_DOF[direction]
    sname = f"Step-2_{direction}_{'PLUS' if sign > 0 else 'MINUS'}"
    w(f"** ---------------- {sname} : LOAD_RP U{dof} = {sign * amp:+g} mm ----------------\n")
    w(f"*Step, name={sname}, {nl}, inc={sc['max_inc']}\n" if dialect == "abaqus"
      else f"*Step, {nl}, INC={sc['max_inc']}\n")
    w("*Static\n" + ", ".join(f"{v:g}" for v in s2) + "\n")
    w(_ccx_controls(dialect, sc))
    w(f"*Boundary\nLOAD_RP, {dof}, {dof}, {sign * amp:.8g}\n")
    w(_outputs(dialect, sc["field_freq"]))
    w("*End Step\n")
    fh.close()


# ---------------------------------------------------------------------------
# [v11] 통합 Job : 모든 스웨징 x 하중방향을 ODB 1개에 (Abaqus 전용)
# ---------------------------------------------------------------------------
def combined_job_name(tag: str) -> str:
    return f"{tag}_ALL"


def write_combined_inp(path: Path, mesh_file: str, mesh, sets, mat: dict, cases: list, rp_id: int,
                       steps_cfg: dict | None = None, sw_outward_positive: bool = True, title: str = "",
                       inner_constraint: str = "rigid_body") -> list:
    """스웨징 값마다 [SWAGE -> (하중 -> 원위치) x 방향] 스텝을 차례로 이어 붙인 INP 1개를 쓴다.

    고무(Ogden 초탄성)는 이력에 의존하지 않으므로 하중을 0 으로 되돌린 뒤 다음 하중을 걸면
    Job 을 따로 돌린 것과 같은 곡선이 나온다. 메시/재료가 ODB 에 한 번만 저장되어 용량이 준다.
    cases : jobs_for() 가 만든 목록 (job, sw, dir, sign, amp). 스웨징 오름차순으로 정렬해서 사용.
    반환  : 스텝 계획 [{job, sw, dir, sign, amp, swage_step, load_step, prev_step}]"""
    sc = {"swage_inc": [0.1, 1.0, 1e-8, 0.25], "load_inc": [0.02, 1.0, 1e-8, 0.05], "max_inc": 2000,
          "field_freq": 5}
    if steps_cfg:
        sc.update(steps_cfg)
    back_inc = sc.get("back_inc", [0.25, 1.0, 1e-8, 1.0])
    sws = sorted({c["sw"] for c in cases})
    plan = []
    fh = open(path, "w", encoding="ascii")
    w = fh.write
    w("*Heading\n")
    w(f"YL_AUTO {title} ALL-IN-ONE : SW={[float(v) for v in sws]} mm x {len(cases) // max(len(sws), 1)} load cases\n")
    w("** units: mm, N, MPa\n")
    w(f"*Include, input={mesh_file}\n")
    if inner_constraint == "coupling":
        w("*Surface, type=NODE, name=INNER_SURF\nINNER, 1.\n")
        w("*Coupling, constraint name=CP_INNER, ref node=LOAD_RP, surface=INNER_SURF\n*Kinematic\n")
    else:
        w("*Rigid Body, ref node=LOAD_RP, tie nset=INNER\n")
    w(f"*Solid Section, elset=RUBBER, material={mat['name']}\n,\n")
    w(_material_block(mat, "abaqus"))

    sgn = 1.0 if sw_outward_positive else -1.0
    dx, dz = _swage_dirs(mesh, sets)
    outer_ids = mesh.node_ids[sets.outer]
    out_full = _outputs("abaqus", sc["field_freq"])
    out_last = _outputs("abaqus", 999999)      # 원위치 스텝은 마지막 프레임만 저장 (용량 절약)

    def step(name, inc, bc, outputs):
        w(f"** ---------------- {name} ----------------\n")
        w(f"*Step, name={name}, NLGEOM=YES, inc={sc['max_inc']}\n")
        w("*Static\n" + ", ".join(f"{v:g}" for v in inc) + "\n")
        w("*Boundary\n" + bc)
        w(outputs)
        w("*End Step\n")

    prev = None
    first = True
    for sw in sws:
        tag = f"SW{fmt_num(sw)}"
        bc = "LOAD_RP, 1, 6, 0.\n" if first else ""
        ux = sgn * sw * dx
        uz = sgn * sw * dz
        bc += "".join(f"{int(n)}, 1, 1, {a:.8g}\n{int(n)}, 2, 2, 0.\n{int(n)}, 3, 3, {b:.8g}\n"
                      for n, a, b in zip(outer_ids, ux, uz))
        sw_step = f"{tag}_SWAGE"
        step(sw_step, sc["swage_inc"], bc, out_full)
        first = False
        prev = sw_step
        for c in [c for c in cases if c["sw"] == sw]:
            dof = DIR_DOF[c["dir"]]
            base = f"{tag}_{c['dir']}{'P' if c['sign'] > 0 else 'M'}"
            step(f"{base}_LOAD", sc["load_inc"], f"LOAD_RP, {dof}, {dof}, {c['sign'] * c['amp']:.8g}\n", out_full)
            plan.append({"job": c["job"], "sw": c["sw"], "dir": c["dir"], "sign": c["sign"], "amp": c["amp"],
                         "swage_step": sw_step, "load_step": f"{base}_LOAD", "prev_step": prev})
            step(f"{base}_BACK", back_inc, f"LOAD_RP, {dof}, {dof}, 0.\n", out_last)
            prev = f"{base}_BACK"
    fh.close()
    return plan
