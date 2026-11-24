# -*- coding: utf-8 -*-
"""sets_out.py : 메시 + INNER/OUTER 노드 세트 + LOAD_RP 를 Abaqus INP / CSV / 그림으로 저장"""
from __future__ import annotations

from pathlib import Path

import numpy as np

ABQ_TYPE = {"tet10": "C3D10H", "tet4": "C3D4H", "hex8": "C3D8H"}


def _write_ids(fh, ids, per_line=16):
    ids = list(map(int, ids))
    for i in range(0, len(ids), per_line):
        fh.write(", ".join(str(n) for n in ids[i:i + per_line]) + "\n")


def write_mesh_inp(path: Path, mesh, sets, elem_type: str | None = None) -> dict:
    """*Node / LOAD_RP(0,0,0) / *Element(RUBBER) / *Nset INNER / *Nset OUTER"""
    etype = elem_type or ABQ_TYPE[mesh.family]
    rp_id = int(mesh.node_ids.max()) + 1
    nid = mesh.node_ids
    with open(path, "w", encoding="ascii") as fh:
        fh.write("** YL mesh + sets : axis = +Y (radial plane = XZ), LOAD_RP = (0,0,0)\n")
        fh.write(f"** element family = {mesh.family} -> {etype}\n")
        fh.write("*Node\n")
        for n, (x, y, z) in zip(nid, mesh.xyz):
            fh.write(f"{int(n)}, {x:.8g}, {y:.8g}, {z:.8g}\n")
        fh.write("*Node, nset=LOAD_RP\n")
        fh.write(f"{rp_id}, 0., 0., 0.\n")
        fh.write(f"*Element, type={etype}, elset=RUBBER\n")
        for k in range(len(mesh.elem_ids)):
            fh.write(", ".join(map(str, [int(mesh.elem_ids[k])] + [int(nid[j]) for j in mesh.conn[k]])) + "\n")
        fh.write("*Nset, nset=INNER\n")
        _write_ids(fh, nid[sets.inner])
        fh.write("*Nset, nset=OUTER\n")
        _write_ids(fh, nid[sets.outer])
    return {"rp_id": rp_id, "element_type": etype}


def write_node_csv(path: Path, mesh, idx, dirs=None):
    """노드 번호, 좌표 (OUTER 는 스웨징 방향 단위벡터 dx, dz 도)"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("node_id,x,y,z" + (",dir_x,dir_z" if dirs is not None else "") + "\n")
        for k, i in enumerate(idx):
            x, y, z = mesh.xyz[i]
            row = f"{int(mesh.node_ids[i])},{x:.8g},{y:.8g},{z:.8g}"
            if dirs is not None:
                row += f",{dirs[k, 0]:.6f},{dirs[k, 1]:.6f}"
            fh.write(row + "\n")


def plot_sets(mesh, sets, out_png: Path, title: str) -> bool:
    """INNER(파랑)/OUTER(빨강)/LOAD_RP(검정 별) 확인용 그림. 축 = +Y."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.lines import Line2D
    except Exception:
        return False
    x = mesh.xyz
    rng = np.random.default_rng(0)
    allidx = rng.choice(len(x), size=min(len(x), 25000), replace=False)
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.4))
    views = [((0, 2), "X", "Z", "end view (axis Y, out of page)"), ((0, 1), "X", "Y", "side view"),
             ((2, 1), "Z", "Y", "side view")]
    for ax, ((a, b), la, lb, sub) in zip(axes, views):
        ax.scatter(x[allidx, a], x[allidx, b], s=0.3, c="0.80", rasterized=True)
        ax.scatter(x[sets.outer, a], x[sets.outer, b], s=0.8, c="tab:red", rasterized=True)
        ax.scatter(x[sets.inner, a], x[sets.inner, b], s=0.8, c="tab:blue", rasterized=True)
        ax.plot([0], [0], marker="*", ms=14, c="k", ls="none")
        ax.set_aspect("equal")
        ax.set_xlabel(f"{la} (mm)")
        ax.set_ylabel(f"{lb} (mm)")
        ax.set_title(sub, fontsize=10)
        ax.grid(alpha=0.3)
    hd = [Line2D([], [], marker="o", ls="none", c="0.70", ms=5, label="rubber nodes"),
          Line2D([], [], marker="o", ls="none", c="tab:red", ms=5, label=f"OUTER ({len(sets.outer)})"),
          Line2D([], [], marker="o", ls="none", c="tab:blue", ms=5, label=f"INNER ({len(sets.inner)})"),
          Line2D([], [], marker="*", ls="none", c="k", ms=10, label="LOAD_RP (0,0,0)")]
    fig.legend(handles=hd, loc="lower center", ncol=4, fontsize=9, frameon=False)
    i = sets.info
    fig.suptitle(f"{title}   INNER area {i['inner_area_mm2']:.0f} mm2, r {i['inner_r_range'][0]:.2f}~"
                 f"{i['inner_r_range'][1]:.2f}  |  OUTER area {i['outer_area_mm2']:.0f} mm2, r "
                 f"{i['outer_r_range'][0]:.2f}~{i['outer_r_range'][1]:.2f}", fontsize=11)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(out_png, dpi=130)
    plt.close(fig)
    return True
