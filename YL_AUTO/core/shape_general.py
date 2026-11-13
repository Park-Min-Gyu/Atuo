# -*- coding: utf-8 -*-
"""
shape_general.py  [v12] 원통이 아닌 임의 형상용 : 축 / 원점(0,0,0) / INNER / OUTER 자동 판별

원통 가정(반경 r = sqrt(x^2+z^2))을 쓰지 않고 '투영 그림자'로 판단한다.

  1) 축 찾기   : 어떤 방향으로 형상을 내려다봤을 때 관통 구멍이 보이면 그 방향이 축.
                 X, Y, Z 를 먼저 보고, 없으면 형상의 주방향(기울어진 축)도 본다.
  2) 원점      : 그 구멍(보어)의 중심을 (0,0,0) 으로 옮긴다. 축 방향은 INNER 면의 가운데.
  3) INNER     : 그림자에서 보어 구멍과 맞닿은 옆면 + 거기에 이어진, 보어를 향하는 옆면
  4) OUTER     : 그림자의 바깥 윤곽과 맞닿은 옆면
  5) 스웨징 방향: OUTER 가 원이면 예전처럼 반경 방향, 아니면 면의 법선 방향(균일 오프셋)

원형 부싱에서는 예전 방식(classify_inner_outer)과 같은 결과가 나온다.
"""
from __future__ import annotations

import math

import numpy as np

from core import inp_mesh
from core.inp_mesh import Mesh, SetResult


# ---------------------------------------------------------------------------
# 투영 그림자(footprint) 래스터
# ---------------------------------------------------------------------------
def _basis(axis: np.ndarray):
    """축 a 에 수직인 두 단위벡터 (u, v).  (u, a, v) 가 오른손계 -> a 가 +Y 가 되는 회전에 사용"""
    a = axis / np.linalg.norm(axis)
    t = np.array([1.0, 0.0, 0.0]) if abs(a[0]) < 0.9 else np.array([0.0, 0.0, 1.0])
    u = np.cross(a, t)
    u /= np.linalg.norm(u)
    v = np.cross(u, a)
    return u, a, v


def rotation_to_y(axis: np.ndarray) -> np.ndarray:
    """axis 를 +Y 로 보내는 회전행렬 (det=+1). X/Y/Z 축이면 예전 ROT_TO_Y 와 같은 행렬."""
    a = axis / np.linalg.norm(axis)
    for name, e in (("X", (1, 0, 0)), ("Y", (0, 1, 0)), ("Z", (0, 0, 1))):
        if np.allclose(a, e, atol=1e-9):
            return inp_mesh.ROT_TO_Y[name]
    y = np.array([0.0, 1.0, 0.0])
    v = np.cross(a, y)
    s, c = np.linalg.norm(v), float(a @ y)
    if s < 1e-12:                       # a = -Y : X 축으로 180도
        return np.diag([1.0, -1.0, -1.0])
    k = v / s
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + s * K + (1 - c) * (K @ K)


def footprint(tri2: np.ndarray, cell: float) -> dict:
    """tri2 : (F,3,2) 경계 삼각형을 평면에 투영한 좌표. 닫힌 표면이므로 합집합 = 형상의 그림자.
    반환 : 격자(grid), 빈 영역 라벨(lab), 바깥 라벨(ext), 구멍 목록(holes: label, area, centroid)"""
    from scipy import ndimage
    lo = tri2.reshape(-1, 2).min(0) - 3 * cell
    hi = tri2.reshape(-1, 2).max(0) + 3 * cell
    shape = np.ceil((hi - lo) / cell).astype(int) + 1
    grid = np.zeros(shape, bool)
    e = np.linalg.norm(tri2 - np.roll(tri2, 1, axis=1), axis=2).max(1)
    nsub = np.maximum(1, np.ceil(e / (0.6 * cell)).astype(int))
    for n in np.unique(nsub):
        t = tri2[nsub == n]
        i, j = np.meshgrid(np.arange(n + 1), np.arange(n + 1), indexing="ij")
        ok = (i + j) <= n
        b1, b2 = i[ok] / n, j[ok] / n
        b0 = 1.0 - b1 - b2
        for s in range(0, len(t), 20000):          # 메모리 절약
            tt = t[s:s + 20000]
            pts = (b0[None, :, None] * tt[:, 0:1] + b1[None, :, None] * tt[:, 1:2]
                   + b2[None, :, None] * tt[:, 2:3]).reshape(-1, 2)
            ij = np.floor((pts - lo) / cell).astype(int)
            grid[ij[:, 0], ij[:, 1]] = True
    lab, nlab = ndimage.label(~grid)
    ext = int(lab[0, 0])
    holes = []
    if nlab > 1:
        idx = np.arange(1, nlab + 1)
        cnt = ndimage.sum(np.ones_like(lab), lab, idx)
        com = ndimage.center_of_mass(np.ones_like(lab), lab, idx)
        for k, c, cm in zip(idx, cnt, com):
            if k == ext or c < 9:
                continue
            holes.append({"label": int(k), "area": float(c * cell * cell),
                          "centroid": (lo + (np.array(cm) + 0.5) * cell)})
    fc = lo + (np.array(ndimage.center_of_mass(grid)) + 0.5) * cell
    return {"grid": grid, "lab": lab, "ext": ext, "holes": holes, "lo": lo, "cell": cell,
            "area": float(grid.sum() * cell * cell), "centroid": fc}


def _label_at(fp: dict, pts: np.ndarray) -> np.ndarray:
    """점 위치의 라벨 : 0 = 재료(그림자 안), ext = 바깥, 그 외 = 구멍 번호"""
    ij = np.floor((pts - fp["lo"]) / fp["cell"]).astype(int)
    sh = np.array(fp["lab"].shape)
    out = np.full(len(pts), fp["ext"], dtype=np.int64)
    ok = np.all((ij >= 0) & (ij < sh), axis=1)
    out[ok] = fp["lab"][ij[ok, 0], ij[ok, 1]]
    return out


def pick_bore(fp: dict):
    """관통 구멍 중 보어 고르기 : 충분히 큰 구멍(최대의 20% 이상) 중 형상 중심에 가장 가까운 것.
    (보어 옆의 공극(void)이 보어로 잡히지 않게)"""
    hs = fp["holes"]
    if not hs:
        return None
    amax = max(h["area"] for h in hs)
    cand = [h for h in hs if h["area"] >= 0.2 * amax]
    return min(cand, key=lambda h: float(np.linalg.norm(h["centroid"] - fp["centroid"])))


# ---------------------------------------------------------------------------
# 1) 축 찾기
# ---------------------------------------------------------------------------
def find_axis(m: Mesh, faces, nrm, area, axis: str = "auto") -> tuple[np.ndarray, dict]:
    x = m.xyz
    tri = np.array([f[:3] for f in faces])
    span = float(np.linalg.norm(x.max(0) - x.min(0)))
    cell = max(span / 400.0, 1e-6)
    named = {"X": np.array([1.0, 0, 0]), "Y": np.array([0, 1.0, 0]), "Z": np.array([0, 0, 1.0])}
    if axis.upper() in named:
        return named[axis.upper()], {"source_axis": axis.upper(), "how": "config"}

    def score(a):
        u, _, v = _basis(a)
        p2 = np.stack([x[tri] @ u, x[tri] @ v], axis=2)
        fp = footprint(p2, cell)
        b = pick_bore(fp)
        return (b["area"] if b else 0.0), fp["area"]

    tried = {}
    best, best_s = None, 0.0
    for name, a in named.items():
        s, fa = score(a)
        tried[name] = round(s, 3)
        if s > best_s:
            best, best_s, best_name = a, s, name
    if best is None:
        # X/Y/Z 어느 쪽으로도 관통 구멍이 안 보임 -> 형상이 기울어져 있을 수 있으니 주방향도 확인
        M = (nrm * area[:, None]).T @ nrm
        _, vec = np.linalg.eigh(M)
        xc = x - x.mean(0)
        _, vec2 = np.linalg.eigh(xc.T @ xc)
        for k, a in enumerate(list(vec.T) + list(vec2.T)):
            s, fa = score(a)
            tried[f"PCA{k}"] = round(s, 3)
            if s > best_s:
                best, best_s, best_name = a, s, f"PCA{k}"
    if best is None:
        raise ValueError(
            "관통 구멍(보어)을 찾지 못했습니다. 어느 방향으로 봐도 형상에 뚫린 구멍이 없습니다 "
            f"(확인한 방향별 구멍 면적 {tried}). INNER(안쪽면)는 관통 구멍의 벽으로 정의하므로, "
            "구멍이 없는 형상은 config 의 geometry.shape_mode 를 'cylinder' 로 두고 inner_r_max/outer_r_min 을 "
            "직접 지정하거나, HyperMesh 에서 INNER/OUTER 를 직접 지정한 .inp 를 사용하세요.")
    return best, {"source_axis": best_name, "how": "through-hole", "hole_area_by_axis_mm2": tried,
                  "axis_vector": [float(v) for v in best]}


# ---------------------------------------------------------------------------
# 전체 : 축 -> +Y 회전, 원점 이동, INNER / OUTER
# ---------------------------------------------------------------------------
def setup_general(m: Mesh, axis: str = "auto", center: str = "auto", cos_min: float = 0.6,
                  inner_tol: float = 0.5, outer_tol: float = 0.5, inner_mode: str = "connected",
                  swage_mode: str = "auto") -> tuple[dict, SetResult]:
    faces, nrm, cen, area = inp_mesh.boundary_faces(m)
    a, ax_info = find_axis(m, faces, nrm, area, axis)
    R = rotation_to_y(a)
    m.xyz = m.xyz @ R.T
    nrm = nrm @ R.T
    cen = cen @ R.T
    ax_info["rotation"] = R.tolist()
    x = m.xyz
    tri = np.array([f[:3] for f in faces])

    # 정밀 그림자 (XZ 평면) : 칸 크기 = 허용오차의 절반 (큰 모델은 최대 1500칸)
    span = float(max(np.ptp(x[:, 0]), np.ptp(x[:, 2])))
    tol = min(inner_tol, outer_tol)
    cell = max(tol / 2.0, span / 1500.0)
    fp = footprint(x[tri][:, :, [0, 2]], cell)
    bore = pick_bore(fp)
    if bore is None:
        raise ValueError(f"축 {ax_info['source_axis']} 방향으로 관통 구멍(보어)이 보이지 않습니다. "
                         f"config 의 geometry.axis 를 확인하세요 (auto 권장).")
    c0 = bore["centroid"]

    n2 = nrm[:, [0, 2]]
    npl = np.linalg.norm(n2, axis=1)
    lateral = npl >= math.sqrt(max(0.0, 1.0 - cos_min ** 2)) * 0.75   # 옆면(축과 나란한 면). 테이퍼 ~53도까지
    d2 = np.where(npl[:, None] > 1e-12, n2 / np.maximum(npl, 1e-12)[:, None], 0.0)
    p2 = cen[:, [0, 2]]

    def lab_step(dist):
        return _label_at(fp, p2 + d2 * dist)
    step_in = max(inner_tol, 2.0 * cell) + cell
    step_out = max(outer_tol, 2.0 * cell) + cell
    l_in, l_out = lab_step(step_in), lab_step(step_out)

    # OUTER : 바깥 윤곽과 맞닿은 옆면
    sel_out = lateral & (l_out == fp["ext"])
    # INNER 씨앗 : 보어 구멍과 맞닿은 옆면
    seed = lateral & (l_in == bore["label"])
    if not seed.any() or not sel_out.any():
        raise ValueError(f"INNER/OUTER 면을 찾지 못했습니다 (보어와 맞닿은 면 {int(seed.sum())}개, "
                         f"바깥 윤곽과 맞닿은 면 {int(sel_out.sum())}개). 형상/축 확인 필요")
    sel_in = seed.copy()
    if inner_mode == "connected":
        # 단차/테이퍼가 있는 보어 : 씨앗에서 시작해 '보어 쪽을 향하는 옆면'을 이웃으로 따라가며 포함.
        # 바깥이나 다른 구멍(공극)을 바라보는 면은 제외.
        to_c = c0[None, :] - p2
        to_c /= np.maximum(np.linalg.norm(to_c, axis=1), 1e-12)[:, None]
        ok = lateral & (np.einsum("ij,ij->i", d2, to_c) > 0.0) & ((l_in == 0) | (l_in == bore["label"])) & ~sel_out
        node2f: dict = {}
        for i in np.where(ok)[0]:
            for nd in faces[i][:3]:
                node2f.setdefault(nd, []).append(i)
        seen = set(np.where(seed)[0].tolist())
        stack = list(seen)
        while stack:
            i = stack.pop()
            for nd in faces[i][:3]:
                for j in node2f.get(nd, ()):
                    if j not in seen:
                        seen.add(j)
                        stack.append(j)
        sel_in = np.zeros(len(faces), bool)
        sel_in[list(seen)] = True

    inner = np.unique(np.concatenate([faces[i] for i in np.where(sel_in)[0]]))
    outer = np.unique(np.concatenate([faces[i] for i in np.where(sel_out)[0]]))
    both = np.intersect1d(inner, outer)
    if len(both):
        raise ValueError(f"INNER/OUTER 에 동시에 속한 절점 {len(both)}개 -> 벽이 너무 얇거나 허용오차가 큽니다 "
                         f"(geometry.inner_tol_mm / outer_tol_mm 를 줄이세요).")

    # ---- 2) 원점 : 보어 중심 -> (0,0,0) ----
    xin = x[inner]
    A = np.c_[2 * xin[:, 0], 2 * xin[:, 2], np.ones(len(xin))]
    sol, *_ = np.linalg.lstsq(A, xin[:, 0] ** 2 + xin[:, 2] ** 2, rcond=None)
    rfit = math.sqrt(max(sol[2] + sol[0] ** 2 + sol[1] ** 2, 0.0))
    res = np.hypot(xin[:, 0] - sol[0], xin[:, 2] - sol[1]) - rfit
    inner_round = bool(rfit > 0 and np.abs(res).max() < max(0.02 * rfit, 0.05))
    bc = np.array([sol[0], sol[1]]) if inner_round else c0     # 원이면 원 맞춤 중심, 아니면 구멍 도심
    ymid = 0.5 * (xin[:, 1].min() + xin[:, 1].max())
    center = (center or "auto").lower()
    shift = np.zeros(3)
    if center == "auto":
        shift = np.array([bc[0], ymid, bc[1]])
    elif center == "bbox":
        shift = (x.min(0) + x.max(0)) / 2
    m.xyz = x = x - shift
    cen = cen - shift
    bc_after = bc - shift[[0, 2]]
    ax_info["center_mode"] = center
    ax_info["translated_by"] = (-shift).tolist()
    m.note(f"축 {ax_info['source_axis']} -> +Y 회전, 원점 이동 {(-shift).round(4).tolist()} (center={center})")

    # ---- 5) 스웨징 방향 (OUTER 절점별, XZ 평면 단위벡터, 바깥쪽이 +) ----
    xo = x[outer]
    ro = np.hypot(xo[:, 0], xo[:, 2])
    outer_round = bool(ro.min() > 1e-9 and (ro.max() - ro.min()) <= max(0.01 * ro.mean(), outer_tol))
    mode = swage_mode.lower()
    if mode == "auto":
        mode = "radial" if outer_round else "normal"
    if mode == "radial":
        dirs = np.c_[xo[:, 0], xo[:, 2]] / np.maximum(ro, 1e-12)[:, None]
    else:
        acc = np.zeros((len(x), 2))
        for i in np.where(sel_out)[0]:
            acc[faces[i]] += d2[i] * area[i]
        dirs = acc[outer]
        ln = np.linalg.norm(dirs, axis=1)
        bad = ln < 1e-12
        dirs[~bad] /= ln[~bad, None]
        dirs[bad] = (np.c_[xo[:, 0], xo[:, 2]] / np.maximum(ro, 1e-12)[:, None])[bad]

    xin = x[inner]
    rin_n = np.hypot(xin[:, 0], xin[:, 2])
    info = {
        "method": "general (projection footprint)",
        "n_boundary_faces": int(len(faces)),
        "inner_mode": inner_mode,
        "inner_nodes": int(len(inner)),
        "outer_nodes": int(len(outer)),
        "inner_area_mm2": float(area[sel_in].sum()),
        "outer_area_mm2": float(area[sel_out].sum()),
        "inner_r_range": [float(rin_n.min()), float(rin_n.max())],
        "outer_r_range": [float(ro.min()), float(ro.max())],
        "inner_y_range": [float(xin[:, 1].min()), float(xin[:, 1].max())],
        "outer_y_range": [float(xo[:, 1].min()), float(xo[:, 1].max())],
        "bore_center_xz": (float(bc_after[0]), float(bc_after[1])),
        "bore_center_offset_mm": float(math.hypot(*bc_after)),
        "inner_is_round": inner_round,
        "outer_is_round": outer_round,
        "swage_direction": mode,
        "n_through_holes": len(fp["holes"]),
        "bore_hole_area_mm2": bore["area"],
        "footprint_cell_mm": cell,
    }
    warn = []
    if info["bore_center_offset_mm"] > 0.1:
        warn.append(f"보어 중심이 원점에서 {info['bore_center_offset_mm']:.3f} mm 떨어져 있습니다 "
                    f"(center='{center}'). LOAD_RP 는 (0,0,0) 입니다 -> geometry.center 를 'auto' 로 두면 자동으로 맞춥니다")
    if not outer_round:
        warn.append("OUTER 가 원형이 아닙니다 -> 스웨징은 면의 법선 방향(균일 오프셋)으로 적용합니다 "
                    "(반경 방향을 원하면 geometry.swage_mode='radial')")
    info["warnings"] = warn
    return ax_info, SetResult(inner, outer, info, dirs)
