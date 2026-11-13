# -*- coding: utf-8 -*-
"""
results.py : LOAD_RP 하중-변위 CSV -> 정특성(강성) 계산, 자동 QC, 학습용 데이터셋
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np

DOF = {"X": 0, "Y": 1, "Z": 2}


def read_rp_csv(path: Path):
    steps, rows = [], []
    for line in Path(path).read_text().splitlines():
        if not line or line.startswith("#") or line.startswith("step,"):
            continue
        v = line.split(",")
        steps.append(v[0])
        rows.append([float(x) for x in v[1:]])
    return steps, np.array(rows)


def split_combined_csv(csv_path: Path, plan: list, run_dir: Path) -> dict:
    """[v11] 통합 Job(ODB 1개)의 LOAD_RP CSV 를 예전처럼 '스웨징 스텝 + 하중 스텝' 2-스텝짜리
    <job>_RP.csv 로 나눈다 -> 이후 강성 계산/QC/데이터셋 코드는 그대로 사용.
    하중 직전 상태(= 직전 스텝의 마지막 점)를 기준점으로 쓴다.
    반환: {job: {"completed": bool, "n_points": int}}"""
    lines = [l for l in Path(csv_path).read_text().splitlines()
             if l and not l.startswith("#") and not l.startswith("step,")]
    by_step: dict = {}
    for l in lines:
        by_step.setdefault(l.split(",", 1)[0], []).append(l.split(",", 1)[1])
    out = {}
    for c in plan:
        load = by_step.get(c["load_step"], [])
        ref = by_step.get(c["prev_step"], []) or by_step.get(c["swage_step"], [])
        done = bool(load) and float(load[-1].split(",")[0]) > 0.9999
        out[c["job"]] = {"completed": done, "n_points": len(load)}
        f = Path(run_dir) / f"{c['job']}_RP.csv"
        if not load or not ref:
            if f.exists():
                f.unlink()
            continue
        with open(f, "w") as fh:
            fh.write(f"# source=combined:{Path(csv_path).name} load_step={c['load_step']}\n")
            fh.write("step,step_time,total_time,U1,U2,U3,RF1,RF2,RF3\n")
            fh.write(f"Step-1_SWAGE,{ref[-1]}\n")
            for l in load:
                fh.write(f"Step-2_LOAD,{l}\n")
    return out


def analyze_job(csv_path: Path, direction: str, sign: int, amp: float) -> dict:
    """Step-2(하중 스텝) 곡선 추출 및 강성 계산. 스텝 이름이 아닌 '등장 순서'로 스텝을 구분."""
    steps, a = read_rp_csv(csv_path)
    order = []
    for s in steps:
        if s not in order:
            order.append(s)
    res = {"ok": False, "checks": {}}
    if len(order) < 2:
        res["checks"]["two_steps"] = f"FAIL (steps={order})"
        return res
    i = DOF[direction]
    m1 = np.array([s == order[0] for s in steps])
    m2 = np.array([s == order[1] for s in steps])
    sw_end = a[m1][-1]
    # 스웨징 후 RP 잔류 반력(예하중)
    # 열: 0 step_time, 1 total_time, 2~4 U1~U3, 5~7 RF1~RF3
    res["swage_RF_end"] = sw_end[5:8].tolist()
    b = a[m2]
    t = np.r_[0.0, b[:, 0]]
    u = np.r_[sw_end[2 + i], b[:, 2 + i]] - sw_end[2 + i]
    f = np.r_[sw_end[5 + i], b[:, 5 + i]] - sw_end[5 + i]
    # 시간 0 중복 제거
    keep = np.r_[True, np.diff(t) > 1e-12]
    t, u, f = t[keep], u[keep], f[keep]
    u_s, f_s = u * sign, f * sign                    # 하중 방향 기준 양수화
    res.update(step_time_end=float(t[-1]), u_end=float(u_s[-1]), F_end=float(f_s[-1]),
               n_points=int(len(t)))
    c = res["checks"]
    c["completed_full_step"] = "OK" if t[-1] > 0.9999 else f"FAIL (step time {t[-1]:.4f})"
    c["reached_amplitude"] = "OK" if abs(u_s[-1] - amp) <= 1e-3 * max(amp, 1) else f"FAIL (u={u_s[-1]:.4f})"
    c["enough_points"] = "OK" if len(t) >= 6 else f"WARN ({len(t)} points)"
    df = np.diff(f_s)
    c["monotonic_force"] = "OK" if np.all(df > -1e-6 * max(abs(f_s).max(), 1e-9)) else "WARN (force decreases)"
    if u_s[-1] > 0:
        Ks = f_s[-1] / u_s[-1]
        sel = u_s <= 0.1 * amp + 1e-12
        if sel.sum() >= 2:
            K0 = float(np.polyfit(u_s[sel], f_s[sel], 1)[0]) if sel.sum() >= 3 else float(f_s[sel][-1] / u_s[sel][-1])
        else:
            K0 = float(f_s[1] / u_s[1])
        res["K_secant"] = float(Ks)
        res["K_initial"] = K0
        for q in (0.25, 0.5, 0.75):
            uq = q * amp
            fq = float(np.interp(uq, u_s, f_s))
            res[f"K_sec_{int(q * 100)}"] = fq / uq
        n = len(u_s)
        j = max(1, n - 3)
        res["K_tangent_end"] = float(np.polyfit(u_s[j - 1:], f_s[j - 1:], 1)[0]) if n - j + 1 >= 2 else float("nan")
        c["positive_stiffness"] = "OK" if Ks > 0 and K0 > 0 else "FAIL"
    res["curve_u"] = u_s.tolist()
    res["curve_F"] = f_s.tolist()
    res["ok"] = all(not str(v).startswith("FAIL") for v in c.values())
    return res


def write_dataset(rows: list[dict], curves: list[dict], out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    if rows:
        keys = []
        for r in rows:
            for k in r:
                if k not in keys:
                    keys.append(k)
        with open(out_dir / "summary.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in rows:
                w.writerow(r)
    with open(out_dir / "curves.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["job", "model", "elem_size", "swaging_mm", "direction", "sign", "point", "u_mm", "F_N"])
        for c in curves:
            for k, (u, F) in enumerate(zip(c["u"], c["F"])):
                w.writerow([c["job"], c["model"], c["elem_size"], c["sw"], c["dir"], c["sign"], k,
                            f"{u:.6g}", f"{F:.6g}"])


def model_level_checks(rows: list[dict], sym_tol: float) -> list[str]:
    """같은 모델/요소크기/스웨징 안에서 X-Z 대칭성, 스웨징 경향 확인"""
    msgs = []
    groups = {}
    for r in rows:
        if r.get("status") != "OK":
            continue
        groups.setdefault((r["model"], r["elem_size"]), []).append(r)
    for (mdl, es), rs in groups.items():
        bysw = {}
        for r in rs:
            bysw.setdefault(r["swaging_mm"], {})[r["direction"] + ("+" if r["sign"] > 0 else "-")] = r["K_secant"]
        for sw, d in sorted(bysw.items()):
            if "X+" in d and "Z+" in d:
                ratio = d["X+"] / d["Z+"]
                tag = "OK" if abs(ratio - 1) <= sym_tol else "INFO(비대칭 형상이면 정상)"
                msgs.append(f"{mdl} E{es} SW{sw:+g}: Kx/Kz = {ratio:.3f} {tag}")
        for dkey in ("X+", "Y+", "Z+"):
            seq = [(sw, d[dkey]) for sw, d in sorted(bysw.items()) if dkey in d]
            if len(seq) >= 3:
                s = ", ".join(f"SW{sw:+g}:{k:.1f}" for sw, k in seq)
                msgs.append(f"{mdl} E{es} {dkey} K_secant vs swaging -> {s}")
    return msgs


def plot_model(curves: list[dict], out_png: Path, title: str):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    dirs = sorted({c["dir"] for c in curves})
    fig, axes = plt.subplots(1, len(dirs), figsize=(5 * len(dirs), 4), squeeze=False)
    cmap = plt.get_cmap("coolwarm")
    sws = sorted({c["sw"] for c in curves})
    for ax, d in zip(axes[0], dirs):
        for c in sorted([c for c in curves if c["dir"] == d], key=lambda c: c["sw"]):
            k = sws.index(c["sw"]) / max(len(sws) - 1, 1)
            ax.plot(c["u"], c["F"], "-o", ms=2, color=cmap(1 - k), label=f"SW {c['sw']:+g} mm")
        ax.set_title(f"{title}  {d}")
        ax.set_xlabel("displacement (mm)")
        ax.set_ylabel("force (N)")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    plt.close(fig)
    return True


def plot_sets(mesh, sets, out_png: Path, title: str) -> bool:
    """INNER(파랑)/OUTER(빨강)/LOAD_RP(검정 별) 확인용 그림. 축 = +Y.
    왼쪽: 축 방향에서 본 XZ 단면 투영, 가운데/오른쪽: 옆에서 본 XY, ZY 투영."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    x = mesh.xyz
    rng = np.random.default_rng(0)
    allidx = rng.choice(len(x), size=min(len(x), 25000), replace=False)
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.4))
    views = [((0, 2), "X", "Z", "end view (axis Y, out of page)"), ((0, 1), "X", "Y", "side view"),
             ((2, 1), "Z", "Y", "side view")]
    for ax, ((a, b), la, lb, sub) in zip(axes, views):
        ax.scatter(x[allidx, a], x[allidx, b], s=0.3, c="0.80", label="rubber nodes", rasterized=True)
        ax.scatter(x[sets.outer, a], x[sets.outer, b], s=0.8, c="tab:red", label=f"OUTER ({len(sets.outer)})",
                   rasterized=True)
        ax.scatter(x[sets.inner, a], x[sets.inner, b], s=0.8, c="tab:blue", label=f"INNER ({len(sets.inner)})",
                   rasterized=True)
        ax.plot([0], [0], marker="*", ms=14, c="k", ls="none", label="LOAD_RP (0,0,0)")
        ax.set_aspect("equal")
        ax.set_xlabel(f"{la} (mm)")
        ax.set_ylabel(f"{lb} (mm)")
        ax.set_title(sub, fontsize=10)
        ax.grid(alpha=0.3)
    from matplotlib.lines import Line2D
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
