# -*- coding: utf-8 -*-
"""
grid_hex.py : [v26] 블록으로 나누지 않은 임의 형상을 순수 헥사(C3D8H)로 자동 메시 (격자 + 경계층 방식)

  python core/grid_hex.py <형상.stp> <출력.inp> [요소크기mm=2.0] [그림.png]

왜 필요한가
  YL_HEX_V6 (HyperMesh SolidMap) 는 '매핑 가능한' 솔리드만 헥사로 만든다. 공극·필렛이 많은 부싱 솔리드는
  통째로는 매핑이 안 되어 헥사 0 개로 끝난다 (사람이 HyperMesh 에서 블록으로 잘라 줘야 함).
  이 모듈은 자르지 않고 헥사를 만든다. 사면체는 전혀 쓰지 않는다.
  (MeshGems-Hexa / Hexotic, Sandia Sculpt 같은 '격자 기반 전(全) 헥사' 계열의 방법)

방법
  1) CAD -> 촘촘한 삼각형 표면 (OpenCASCADE, CAD 면 번호 유지). 매끈하게 이어지는 면끼리는 한 패치로 묶고,
     꺾이는 각이 큰 모서리(기본 30도 이상)만 '날카로운 모서리'로 남긴다.
  2) 2.0 mm 정육면체 격자로 형상을 덮는다. 축(보어) 방향 격자면은 끝면 같은 평면에 정확히 맞추고,
     반경 방향 격자는 보어 중심에 대칭으로 놓는다 (대칭 형상 -> 대칭 메시).
  3) 격자칸마다 형상 안에 든 부피 비율을 계산해서 절반 이상 든 칸을 남긴다.
     모서리/꼭짓점만 맞닿는 칸(비다양체)은 칸을 더해서 정리한다.
  4) 남은 칸 덩어리 겉면에 헥사 1 겹(경계층)을 넣고, 겉면 절점을 CAD 표면으로 옮긴다.
     날카로운 모서리 위의 절점은 모서리선으로, 꼭짓점은 꼭짓점으로 붙인다.
  5) 표면 위 미끄럼 평활화 + 내부 평활화, scaled Jacobian 검사, 나쁜 요소는 절점 위치를 국부 최적화.
  6) 격자 위치/기준을 바꿔 가며 몇 번 시도해서 품질(최소 scaled Jacobian)이 가장 좋은 것을 쓴다.

마지막 줄에 'GRIDHEX_RESULT {json}' 을 출력한다 (runners.run_grid_hex 가 읽음).
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

GRID_EXT = {".stp", ".step", ".igs", ".iges", ".brep"}

DEFAULTS = {
    "deflection": 0.02,      # CAD 삼각형화 허용 오차 (mm)
    "angular": 0.25,         # CAD 삼각형화 각도 허용 (rad)
    "feature_angle": 30.0,   # 이 각도 이상 꺾이는 모서리만 날카로운 모서리로 유지 (도)
    "pillow_frac": 0.4,      # 경계층 두께 목표 (요소크기 배수)
    "smooth_iters": 25,
    "surf_iters": 8,
    "sj_accept": 0.15,       # 이 품질 이상이면 더 시도하지 않음 (v27.3 : 0.20 -> 0.15, 0.198 같은 결과에서 시간 낭비 방지)
    "sj_fail": 0.02,         # 최선의 시도도 이보다 나쁘면 실패 (Abaqus 가 거부할 수준)
    "max_attempts": 7,
}

# 칸의 8 꼭짓점 (Abaqus C3D8 순서: 아래 1-2-3-4, 위 5-6-7-8)
CORNERS = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
# 칸의 6 면 : (축, 방향, 바깥에서 봐서 반시계 순서의 꼭짓점 오프셋 -> 법선이 바깥)
FACE_DEF = [
    (0, -1, [(0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)]),
    (0, 1, [(1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)]),
    (1, -1, [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)]),
    (1, 1, [(0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)]),
    (2, -1, [(0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)]),
    (2, 1, [(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]),
]
_SJ_NB = [(1, 3, 4), (2, 0, 5), (3, 1, 6), (0, 2, 7), (7, 5, 0), (4, 6, 1), (5, 7, 2), (6, 4, 3)]
HEX_EDGES = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)]


def _gauss_dN():
    """C3D8 2x2x2 적분점에서 형상함수 미분 (적분점 8, 절점 8, 3)"""
    xi = np.array([(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
                   (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)], float)
    out = np.zeros((8, 8, 3))
    for g, p in enumerate(xi / np.sqrt(3.0)):
        for a, (sx, sy, sz) in enumerate(xi):
            out[g, a] = (sx * (1 + sy * p[1]) * (1 + sz * p[2]) / 8, sy * (1 + sx * p[0]) * (1 + sz * p[2]) / 8,
                         sz * (1 + sx * p[0]) * (1 + sy * p[1]) / 8)
    return out


_GDN = _gauss_dN()


def hex_sj_coords(P):
    """P (n,8,3) 꼭짓점 좌표 -> 요소별 최소 scaled Jacobian. 정육면체 1, 0 이하 = 뒤집힘.
    [v27.2] 8 꼭짓점에 더해 Abaqus 가 실제로 검사하는 8 적분점(2x2x2)의 Jacobian 도 본다
    (꼭짓점은 멀쩡해도 심하게 비틀린 요소는 안쪽 적분점에서 음수가 될 수 있음)."""
    out = np.full(len(P), np.inf)
    for k, (a, b, d) in enumerate(_SJ_NB):
        p = P[:, k]
        e1, e2, e3 = P[:, a] - p, P[:, b] - p, P[:, d] - p
        det = np.einsum("ij,ij->i", e1, np.cross(e2, e3))
        nrm = np.linalg.norm(e1, axis=1) * np.linalg.norm(e2, axis=1) * np.linalg.norm(e3, axis=1)
        out = np.minimum(out, det / np.maximum(nrm, 1e-300))
    J = np.einsum("gak,naj->ngkj", _GDN, P)               # (n, 8, 3, 3) : 행 = d/dxi_k
    r0, r1, r2 = J[:, :, 0, :], J[:, :, 1, :], J[:, :, 2, :]
    det = np.einsum("ngj,ngj->ng", r0, np.cross(r1, r2))
    nrm = np.linalg.norm(r0, axis=2) * np.linalg.norm(r1, axis=2) * np.linalg.norm(r2, axis=2)
    return np.minimum(out, (det / np.maximum(nrm, 1e-300)).min(axis=1))


def hex_sj(x, c):
    return hex_sj_coords(x[c])


def hex_volume(x, c):
    """hex8 부피 (5 개 사면체 합 - 계산용일 뿐 요소는 헥사)"""
    v = 0.0
    for t in ((0, 1, 3, 4), (1, 2, 3, 6), (1, 4, 5, 6), (3, 4, 6, 7), (1, 3, 4, 6)):
        a, b, cc, d = x[c[:, t[0]]], x[c[:, t[1]]], x[c[:, t[2]]], x[c[:, t[3]]]
        v = v + np.einsum("ij,ij->i", b - a, np.cross(cc - a, d - a)) / 6.0
    return v


# ---------------------------------------------------------------------------
# 1) CAD -> 삼각형 표면, 패치, 날카로운 모서리
# ---------------------------------------------------------------------------
def _read_shape(src: str):
    ext = Path(src).suffix.lower()
    if ext == ".brep":
        from OCP.BRep import BRep_Builder
        from OCP.BRepTools import BRepTools
        from OCP.TopoDS import TopoDS_Shape
        shape = TopoDS_Shape()
        (getattr(BRepTools, "Read_s", None) or BRepTools.Read)(shape, str(src), BRep_Builder())
        return shape
    if ext in (".igs", ".iges"):
        from OCP.IGESControl import IGESControl_Reader as Reader
    else:
        from OCP.STEPControl import STEPControl_Reader as Reader
    r = Reader()
    if int(r.ReadFile(str(src))) != 1:
        raise RuntimeError(f"CAD 파일을 읽지 못함: {src}")
    r.TransferRoots()
    return r.OneShape()


def _weld(V, F, tol=2e-4):
    """면마다 따로 만든 삼각형의 겹치는 꼭짓점을 하나로 합침"""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree
    n = len(V)
    pairs = cKDTree(V).query_pairs(tol, output_type="ndarray")
    if len(pairs):
        g = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
        _, lab = connected_components(g, directed=False)
    else:
        lab = np.arange(n)
    Vw = np.zeros((int(lab.max()) + 1, 3))
    Vw[lab] = V
    Fw = lab[F]
    keep = (Fw[:, 0] != Fw[:, 1]) & (Fw[:, 1] != Fw[:, 2]) & (Fw[:, 2] != Fw[:, 0])
    T = Vw[Fw]
    area = 0.5 * np.linalg.norm(np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]), axis=1)
    return Vw, Fw, keep & (area > 1e-12)


def _solids(shape):
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    solid_of = getattr(TopoDS, "Solid_s", None) or TopoDS.Solid
    out, e = [], TopExp_Explorer(shape, TopAbs_SOLID)
    while e.More():
        out.append(solid_of(e.Current()))
        e.Next()
    return out


def _volume(shape):
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    (getattr(BRepGProp, "VolumeProperties_s", None) or BRepGProp.VolumeProperties)(shape, g)
    return abs(float(g.Mass()))


def cad_surface(src: str, deflection: float = 0.02, angular: float = 0.25, solid: str = "largest") -> dict:
    """solid = 'largest' : 솔리드가 여러 개면(예: 고무 본체 + 스너버) 가장 큰 것만 메시. 'all' : 전부"""
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS
    face_of = getattr(TopoDS, "Face_s", None) or TopoDS.Face
    tri_of = getattr(BRep_Tool, "Triangulation_s", None) or BRep_Tool.Triangulation
    shape = _read_shape(src)
    vol = None
    others = []
    try:
        sols = _solids(shape)
        if len(sols) > 1 and solid == "largest":
            vols = [_volume(x) for x in sols]
            k = int(np.argmax(vols))
            others = [round(v, 1) for i, v in enumerate(vols) if i != k]
            shape = sols[k]
        vol = _volume(shape)
    except Exception:
        pass
    BRepMesh_IncrementalMesh(shape, deflection, False, angular, True)
    Vs, Fs, Is = [], [], []
    off = n_face = n_skip = 0
    e = TopExp_Explorer(shape, TopAbs_FACE)
    while e.More():
        f = face_of(e.Current())
        loc = TopLoc_Location()
        tri = tri_of(f, loc)
        if tri is None or tri.NbTriangles() == 0:
            n_skip += 1
        else:
            trsf = loc.Transformation()
            V = np.array([[p.X(), p.Y(), p.Z()] for p in
                          (tri.Node(i).Transformed(trsf) for i in range(1, tri.NbNodes() + 1))])
            T = np.array([tri.Triangle(i).Get() for i in range(1, tri.NbTriangles() + 1)], dtype=np.int64) - 1
            if f.Orientation() == TopAbs_REVERSED:
                T = T[:, [0, 2, 1]]
            Vs.append(V)
            Fs.append(T + off)
            Is.append(np.full(len(T), n_face, dtype=np.int64))
            off += len(V)
        n_face += 1
        e.Next()
    if not Fs:
        raise RuntimeError("CAD 에서 삼각형을 하나도 만들지 못함")
    V, F, fid = np.vstack(Vs), np.vstack(Fs), np.concatenate(Is)
    V, F, keep = _weld(V, F)
    return {"V": V, "F": F[keep], "fid": fid[keep], "n_face": n_face, "n_skip": n_skip, "volume": vol,
            "other_solids": others}


def make_patches(V, F, fid, angle_deg=30.0, h=None, concave_smooth=False) -> dict:
    """매끈하게 이어지는 CAD 면을 패치로 묶고, 패치 사이의 날카로운 모서리(삼각형 변)를 돌려줌"""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    T = V[F]
    n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    a2 = np.linalg.norm(n, axis=1)
    nrm = n / np.maximum(a2, 1e-300)[:, None]
    m = len(F)
    E = np.sort(np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]), axis=1)
    Et = np.tile(np.arange(m), 3)
    o = np.lexsort((E[:, 1], E[:, 0]))
    E, Et = E[o], Et[o]
    new = np.ones(len(E), bool)
    new[1:] = np.any(E[1:] != E[:-1], axis=1)
    start = np.nonzero(new)[0]
    size = np.diff(np.append(start, len(E)))
    s2 = start[size == 2]
    t1, t2, ev = Et[s2], Et[s2 + 1], E[s2]
    f1, f2 = fid[t1], fid[t2]
    ang = np.degrees(np.arccos(np.clip(np.abs(np.einsum("ij,ij->i", nrm[t1], nrm[t2])), 0.0, 1.0)))
    if concave_smooth:
        # [v27.3] 오목한 모서리(안쪽으로 꺾인 곳)는 날카로운 모서리로 보지 않음 -> 경계층이 그 모서리를 둥글게 지나감
        cen = T.mean(axis=1)
        concave = np.einsum("ij,ij->i", cen[t2] - cen[t1], nrm[t1]) > 0
        ang = np.where(concave, 0.0, ang)
    nf = int(fid.max()) + 1
    d = f1 != f2
    key = np.minimum(f1, f2)[d] * nf + np.maximum(f1, f2)[d]
    aa = ang[d]
    merge = []
    if len(key):
        o = np.argsort(key, kind="stable")
        key, aa = key[o], aa[o]
        st = np.nonzero(np.r_[True, key[1:] != key[:-1]])[0]
        en = np.r_[st[1:], len(key)]
        for s, e in zip(st, en):
            if np.percentile(aa[s:e], 75) <= angle_deg:
                merge.append((key[s] // nf, key[s] % nf))
    if merge:
        mm = np.array(merge, dtype=np.int64)
        g = coo_matrix((np.ones(len(mm)), (mm[:, 0], mm[:, 1])), shape=(nf, nf))
        _, pof = connected_components(g, directed=False)
    else:
        pof = np.arange(nf)
    _, tp = np.unique(pof[fid], return_inverse=True)
    tp = tp.reshape(-1)
    n_smooth = int(tp.max()) + 1
    n_abs = 0
    if h:
        tp, n_abs = absorb_small_patches(V, F, tp, 0.5 * a2, t1, t2, ev, h)
        _, tp = np.unique(tp, return_inverse=True)
        tp = tp.reshape(-1)
    p1, p2 = tp[t1], tp[t2]
    fe = p1 != p2
    return {"tpatch": tp, "nrm": nrm, "area": 0.5 * a2, "feat_v": ev[fe],
            "feat_p": np.sort(np.stack([p1[fe], p2[fe]], 1), axis=1), "n_patch": int(tp.max()) + 1,
            "n_smooth_groups": n_smooth, "n_absorbed": n_abs}


def absorb_small_patches(V, F, tp, area, t1, t2, ev, h, a_min_ratio=1.5, w_min_ratio=0.5):
    """요소(h)보다 작은 패치(양각 글자, 좁은 모따기 띠 등)는 가장 길게 맞닿은 이웃 패치에 합침
    -> 그런 곳의 모서리선은 메시가 따라가지 않는다 (2 mm 요소로는 표현할 수 없는 크기)."""
    npch = int(tp.max()) + 1
    parea = np.bincount(tp, weights=area, minlength=npch)
    pa, pb = tp[t1], tp[t2]
    d = pa != pb
    pa, pb = pa[d], pb[d]
    el = np.linalg.norm(V[ev[d, 0]] - V[ev[d, 1]], axis=1)
    perim = np.bincount(pa, weights=el, minlength=npch) + np.bincount(pb, weights=el, minlength=npch)
    rep = np.arange(npch)
    width = 2.0 * parea / np.maximum(perim, 1e-12)
    small = (parea < a_min_ratio * h * h) | (width < w_min_ratio * h)
    n_abs = 0
    for s in np.argsort(parea):
        if not small[s] or rep[s] != s:
            continue
        la, lb = rep[pa], rep[pb]
        m1, m2 = (la == s) & (lb != s), (lb == s) & (la != s)
        if not (m1.any() or m2.any()):
            continue
        nbr = np.concatenate([lb[m1], la[m2]])
        ln = np.concatenate([el[m1], el[m2]])
        tot = np.bincount(nbr, weights=ln, minlength=npch)
        q = int(np.argmax(tot))
        rep[rep == s] = q
        parea[q] += parea[s]
        n_abs += 1
    return rep[tp], n_abs


def refine(V, F, max_edge):
    """거리 계산용 : 긴 삼각형을 가장 긴 변의 중점에서 2 분할 (max_edge 이하까지, 원래 삼각형 번호 유지)"""
    P = V[F]
    owner = np.arange(len(F))
    outP, outO = [], []
    for _ in range(60):
        L = np.stack([np.linalg.norm(P[:, 1] - P[:, 0], axis=1), np.linalg.norm(P[:, 2] - P[:, 1], axis=1),
                      np.linalg.norm(P[:, 0] - P[:, 2], axis=1)], 1)
        big = L.max(1) > max_edge
        outP.append(P[~big])
        outO.append(owner[~big])
        if not big.any():
            break
        Q, o, k = P[big], owner[big], np.argmax(L[big], axis=1)
        idx = np.stack([k, (k + 1) % 3, (k + 2) % 3], 1)
        R = Q[np.arange(len(Q))[:, None], idx]
        m = 0.5 * (R[:, 0] + R[:, 1])
        P = np.concatenate([np.stack([R[:, 0], m, R[:, 2]], 1), np.stack([m, R[:, 1], R[:, 2]], 1)])
        owner = np.concatenate([o, o])
    else:
        outP.append(P)
        outO.append(owner)
    return np.concatenate(outP), np.concatenate(outO)


# ---------------------------------------------------------------------------
# 2) 표면 / 모서리선 / 꼭짓점 위 가장 가까운 점
# ---------------------------------------------------------------------------
def _dot(a, b):
    return np.einsum("ij,ij->i", a, b)


def closest_on_tri(p, a, b, c):
    """점 p 에서 삼각형 abc 위 가장 가까운 점 (Ericson, 벡터화)"""
    ab, ac = b - a, c - a
    ap, bp, cp = p - a, p - b, p - c
    d1, d2 = _dot(ab, ap), _dot(ac, ap)
    d3, d4 = _dot(ab, bp), _dot(ac, bp)
    d5, d6 = _dot(ab, cp), _dot(ac, cp)
    va, vb, vc = d3 * d6 - d5 * d4, d5 * d2 - d1 * d6, d1 * d4 - d3 * d2
    eps = 1e-300
    den = va + vb + vc
    den = np.where(np.abs(den) < eps, eps, den)
    res = a + ab * (vb / den)[:, None] + ac * (vc / den)[:, None]
    m = (va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0)
    t = (d4 - d3) / np.maximum((d4 - d3) + (d5 - d6), eps)
    res = np.where(m[:, None], b + (c - b) * t[:, None], res)
    m = (vb <= 0) & (d2 >= 0) & (d6 <= 0)
    t = d2 / np.maximum(d2 - d6, eps)
    res = np.where(m[:, None], a + ac * t[:, None], res)
    m = (d6 >= 0) & (d5 <= d6)
    res = np.where(m[:, None], c, res)
    m = (vc <= 0) & (d1 >= 0) & (d3 <= 0)
    t = d1 / np.maximum(d1 - d3, eps)
    res = np.where(m[:, None], a + ab * t[:, None], res)
    m = (d3 >= 0) & (d4 <= d3)
    res = np.where(m[:, None], b, res)
    m = (d1 <= 0) & (d2 <= 0)
    res = np.where(m[:, None], a, res)
    return res


class SurfQuery:
    """잘게 나눈 삼각형 표면에서 가장 가까운 점 (패치 제한, 법선 방향 조건 가능)"""

    def __init__(self, P, patch, nrm):
        from scipy.spatial import cKDTree
        self.P, self.patch, self.nrm = P, patch, nrm
        self.tree = cKDTree(P.mean(axis=1))
        self._pt = {}

    def _patch_tree(self, p):
        if p not in self._pt:
            from scipy.spatial import cKDTree
            idx = np.nonzero(self.patch == p)[0]
            self._pt[p] = (cKDTree(self.P[idx].mean(axis=1)), idx) if len(idx) else (None, idx)
        return self._pt[p]

    def closest(self, X, k=12, patch=None, ndir=None, ncos=0.0):
        X = np.asarray(X, float)
        n = len(X)
        Y, D, TI = X.copy(), np.full(n, np.inf), np.full(n, -1, dtype=np.int64)
        if n == 0:
            return Y, D, TI
        groups = [(-1, np.arange(n))] if patch is None else [(int(p), np.nonzero(patch == p)[0]) for p in np.unique(patch)]
        for p, idx in groups:
            if p < 0:
                tree, sub = self.tree, None
            else:
                tree, sub = self._patch_tree(p)
                if tree is None:
                    tree, sub = self.tree, None
            kk = int(min(k, tree.n))
            _, cand = tree.query(X[idx], k=kk)
            cand = np.asarray(cand).reshape(len(idx), kk)
            if sub is not None:
                cand = sub[cand]
            c = cand.ravel()
            Xi = np.repeat(X[idx], kk, axis=0)
            Yc = closest_on_tri(Xi, self.P[c, 0], self.P[c, 1], self.P[c, 2])
            d = np.linalg.norm(Yc - Xi, axis=1)
            de = d
            if ndir is not None:
                cs = _dot(self.nrm[c], np.repeat(ndir[idx], kk, axis=0))
                de = d + np.where(cs < ncos, 1e6, 0.0)
            j = np.argmin(de.reshape(len(idx), kk), axis=1)
            s = np.arange(len(idx)) * kk + j
            Y[idx], D[idx], TI[idx] = Yc[s], d[s], c[s]
        return Y, D, TI


class CurveQuery:
    """날카로운 모서리선(패치 쌍별) 위 가장 가까운 점"""

    def __init__(self, V, feat_v, feat_p, max_seg):
        from scipy.spatial import cKDTree
        self.trees = {}
        self.nP = int(feat_p.max()) + 1 if len(feat_p) else 1
        if not len(feat_v):
            self.A = self.B = np.zeros((0, 3))
            return
        A, B = V[feat_v[:, 0]], V[feat_v[:, 1]]
        L = np.linalg.norm(B - A, axis=1)
        ns = np.maximum(1, np.ceil(L / max_seg).astype(np.int64))
        rep = np.repeat(np.arange(len(A)), ns)
        k = np.arange(len(rep)) - np.repeat(np.cumsum(ns) - ns, ns)
        t0, t1 = k / ns[rep], (k + 1) / ns[rep]
        self.A = A[rep] + (B[rep] - A[rep]) * t0[:, None]
        self.B = A[rep] + (B[rep] - A[rep]) * t1[:, None]
        key = feat_p[rep, 0] * self.nP + feat_p[rep, 1]
        mid = 0.5 * (self.A + self.B)
        for kk in np.unique(key):
            idx = np.nonzero(key == kk)[0]
            self.trees[int(kk)] = (cKDTree(mid[idx]), idx)

    def key(self, pa, pb):
        return np.minimum(pa, pb) * self.nP + np.maximum(pa, pb)

    def has(self, keys):
        return np.array([int(k) in self.trees for k in np.atleast_1d(keys)], dtype=bool)

    def closest(self, X, keys):
        X = np.asarray(X, float)
        Y, D = X.copy(), np.full(len(X), np.inf)
        for kk in np.unique(keys):
            sel = np.nonzero(keys == kk)[0]
            if int(kk) not in self.trees:
                continue
            tree, idx = self.trees[int(kk)]
            k = int(min(6, len(idx)))
            _, c = tree.query(X[sel], k=k)
            c = idx[np.asarray(c).reshape(len(sel), k)].ravel()
            P = np.repeat(X[sel], k, axis=0)
            a, ab = self.A[c], self.B[c] - self.A[c]
            t = np.clip(_dot(P - a, ab) / np.maximum(_dot(ab, ab), 1e-300), 0.0, 1.0)
            q = a + ab * t[:, None]
            d = np.linalg.norm(q - P, axis=1).reshape(len(sel), k)
            j = np.argmin(d, axis=1)
            Y[sel] = q.reshape(len(sel), k, 3)[np.arange(len(sel)), j]
            D[sel] = d[np.arange(len(sel)), j]
        return Y, D


def find_corners(V, feat_v, feat_p):
    """날카로운 모서리선이 3 패치 이상 만나거나 갈라지는/끝나는 꼭짓점"""
    if not len(feat_v):
        return np.zeros((0, 3)), []
    vv = feat_v.ravel()
    deg = np.bincount(vv, minlength=len(V))
    vp = np.unique(np.stack([np.repeat(vv, 2), np.repeat(feat_p, 2, axis=0).ravel()], 1), axis=0)
    npatch = np.bincount(vp[:, 0], minlength=len(V))
    cand = np.nonzero((npatch >= 3) | (deg >= 3) | (deg == 1))[0]
    sets = []
    for v in cand:
        sets.append(set(vp[vp[:, 0] == v, 1].tolist()))
    return V[cand], sets


# ---------------------------------------------------------------------------
# 3) 격자 : 축 / 중심, 끝면에 맞춘 축 방향 격자, 칸 안 부피 비율 (광선 홀짝)
# ---------------------------------------------------------------------------
def find_axis_center(V, F):
    """관통 구멍(보어)이 보이는 방향 = 축, 보어 중심 = 반경 격자 대칭 중심"""
    from core import shape_general
    T = V[F]
    span = float(np.linalg.norm(V.max(0) - V.min(0)))
    cell = max(span / 400.0, 1e-6)
    best = (0.0, None, None)
    for ax in range(3):
        o = [k for k in range(3) if k != ax]
        fp = shape_general.footprint(T[:, :, o], cell)
        b = shape_general.pick_bore(fp)
        if b is not None and b["area"] > best[0]:
            best = (b["area"], ax, b)
    c = 0.5 * (V.min(0) + V.max(0))
    if best[1] is None:
        return int(np.argmin(V.max(0) - V.min(0))), c, 0.0
    ax = best[1]
    o = [k for k in range(3) if k != ax]
    c[o] = best[2]["centroid"]
    return ax, c, float(best[0])


def axial_planes(V, F, nrm, area, ax, h):
    """축에 수직인 평면(끝면, 단차면)의 위치와 넓이 -> 격자면을 맞출 후보"""
    m = np.abs(nrm[:, ax]) > 0.9998
    if not m.any():
        return []
    c = V[F[m]][:, :, ax].mean(axis=1)
    a = area[m]
    _, inv = np.unique(np.round(c / 0.02).astype(np.int64), return_inverse=True)
    inv = inv.reshape(-1)
    A = np.bincount(inv, weights=a)
    C = np.bincount(inv, weights=a * c) / np.maximum(A, 1e-300)
    good = A >= max(2.0 * h * h, 0.01 * A.sum())
    return sorted(zip(C[good].tolist(), A[good].tolist()), key=lambda t: -t[1])


def axis_nodes(lo, hi, planes, h):
    keep = []
    for c, _ in planes:
        if lo - 1e-6 <= c <= hi + 1e-6 and all(abs(c - k) >= 0.6 * h for k in keep):
            keep.append(c)
    for c in (lo, hi):
        if all(abs(c - k) >= 0.6 * h for k in keep):
            keep.append(c)
    keep.sort()
    nodes = [keep[0]]
    for a, b in zip(keep[:-1], keep[1:]):
        n = max(1, int(round((b - a) / h)))
        nodes += list(a + (b - a) * np.arange(1, n + 1) / n)
    return np.array([nodes[0] - h] + nodes + [nodes[-1] + h])


def radial_nodes(lo, hi, c, h, shift=0.0):
    k0 = int(np.floor((lo - c - shift) / h)) - 1
    k1 = int(np.ceil((hi - c - shift) / h)) + 1
    return c + shift + h * np.arange(k0, k1 + 1)


def sub_coords(nodes, s=3):
    a, b = nodes[:-1], nodes[1:]
    f = (np.arange(s) + 0.5) / s
    return (a[:, None] + (b - a)[:, None] * f[None, :]).ravel()


def _parity_dir(T, axes, d):
    """d 축 방향 광선들이 표면을 몇 번 지나는지(홀짝) -> 격자점이 안쪽인지. 지나는 횟수가 홀수인 광선은 무효"""
    a_, b_ = [k for k in range(3) if k != d]
    A, B, D = axes[a_], axes[b_], axes[d]
    ja = 1.2345e-5 * (A[1] - A[0] if len(A) > 1 else 1.0) + 3.1e-7
    jb = 2.3456e-5 * (B[1] - B[0] if len(B) > 1 else 1.0) + 1.7e-7
    Aj, Bj = A + ja, B + jb
    ta, tb, td = T[:, :, a_], T[:, :, b_], T[:, :, d]
    ia0, ia1 = np.searchsorted(Aj, ta.min(1), "left"), np.searchsorted(Aj, ta.max(1), "right")
    ib0, ib1 = np.searchsorted(Bj, tb.min(1), "left"), np.searchsorted(Bj, tb.max(1), "right")
    na, nb = np.maximum(ia1 - ia0, 0), np.maximum(ib1 - ib0, 0)
    cnt = na * nb
    tris = np.nonzero(cnt)[0]
    cs = np.cumsum(cnt[tris])
    hr, ht = [], []
    s = 0
    while s < len(tris):
        e = max(int(np.searchsorted(cs, (cs[s - 1] if s else 0) + 3_000_000, "right")), s + 1)
        tt = tris[s:e]
        c = cnt[tt]
        tri = np.repeat(tt, c)
        off = np.arange(len(tri)) - np.repeat(np.cumsum(c) - c, c)
        ia = ia0[tri] + off // nb[tri]
        ib = ib0[tri] + off % nb[tri]
        pa, pb = Aj[ia], Bj[ib]
        x0, x1, x2 = ta[tri, 0], ta[tri, 1], ta[tri, 2]
        y0, y1, y2 = tb[tri, 0], tb[tri, 1], tb[tri, 2]
        w0 = (x1 - pa) * (y2 - pb) - (x2 - pa) * (y1 - pb)
        w1 = (x2 - pa) * (y0 - pb) - (x0 - pa) * (y2 - pb)
        w2 = (x0 - pa) * (y1 - pb) - (x1 - pa) * (y0 - pb)
        hit = ((w0 > 0) & (w1 > 0) & (w2 > 0)) | ((w0 < 0) & (w1 < 0) & (w2 < 0))
        ws = w0 + w1 + w2
        t = (w0 * td[tri, 0] + w1 * td[tri, 1] + w2 * td[tri, 2]) / np.where(ws == 0, 1.0, ws)
        hr.append((ia * len(B) + ib)[hit])
        ht.append(t[hit])
        s = e
    nray = len(A) * len(B)
    hr = np.concatenate(hr) if hr else np.zeros(0, np.int64)
    ht = np.concatenate(ht) if ht else np.zeros(0)
    cntr = np.bincount(hr, minlength=nray)
    ok_ray = (cntr % 2) == 0
    tmin = min(float(D.min()), float(ht.min()) if len(ht) else float(D.min())) - 1.0
    tmax = max(float(D.max()), float(ht.max()) if len(ht) else float(D.max())) + 1.0
    sc = 0.999 / (tmax - tmin)
    key = np.sort(hr + (ht - tmin) * sc)
    q = (np.arange(nray)[:, None] + ((D - tmin) * sc)[None, :]).ravel()
    before = np.searchsorted(key, q, "left").reshape(nray, len(D))
    base = np.concatenate([[0], np.cumsum(cntr)])[:-1]
    par = (((before - base[:, None]) % 2) == 1).reshape(len(A), len(B), len(D))
    ok = np.broadcast_to(ok_ray.reshape(len(A), len(B), 1), par.shape)
    perm = np.argsort([a_, b_, d])
    return np.transpose(par, perm), np.transpose(ok, perm), int((~ok_ray).sum())


def inside_lattice(V, F, axes):
    """격자점 안/밖 : x, y, z 세 방향 광선 홀짝의 다수결 (CAD 에 작은 구멍이 있어도 버팀)"""
    T = V[F]
    shape = tuple(len(a) for a in axes)
    votes = np.zeros(shape, np.int8)
    valid = np.zeros(shape, np.int8)
    bad_rays = 0
    for d in range(3):
        par, ok, nbad = _parity_dir(T, axes, d)
        votes += (par & ok).astype(np.int8)
        valid += ok.astype(np.int8)
        bad_rays += nbad
    return votes * 2 > valid, bad_rays


# ---------------------------------------------------------------------------
# 3b) [단면 쓸기 방식] 축 방향 그림자(모든 단면의 합) -> 2D 사각형 메시 -> 축 방향으로 쌓은 기둥 칸
#     부싱은 고무가 금속을 얇게(1 mm 안팎) 감싸는 경우가 많다. 정육면체 격자로는 그 얇은 막을 못 잡지만
#     단면 사각형 메시는 막의 두께 방향으로도 요소를 넣으므로 막이 끊기지 않는다.
# ---------------------------------------------------------------------------
def _rdp(P, eps):
    """Douglas-Peucker (반복형)"""
    keep = np.zeros(len(P), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(P) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        A, B = P[a], P[b]
        ab = B - A
        L = float(np.hypot(ab[0], ab[1]))
        Q = P[a + 1:b] - A
        d = np.hypot(Q[:, 0], Q[:, 1]) if L < 1e-12 else np.abs(ab[0] * Q[:, 1] - ab[1] * Q[:, 0]) / L
        i = int(np.argmax(d))
        if d[i] > eps:
            keep[a + 1 + i] = True
            stack += [(a, a + 1 + i), (a + 1 + i, b)]
    return P[keep]


def _contour_segments(mask, lo, cell):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    m = np.pad(mask.astype(float), 2)
    xs = lo[0] + (np.arange(m.shape[0]) - 2 + 0.5) * cell
    ys = lo[1] + (np.arange(m.shape[1]) - 2 + 0.5) * cell
    fig = plt.figure()
    try:
        cs = plt.contour(xs, ys, m.T, levels=[0.5])
        segs = getattr(cs, "allsegs", None)
        if segs is not None:
            out = [np.asarray(sg) for sg in segs[0]]
        else:
            out = []
            for path in cs.get_paths():
                v, c = path.vertices, path.codes
                if c is None:
                    out.append(v)
                    continue
                starts = list(np.nonzero(c == 1)[0]) + [len(v)]
                out += [v[a:b] for a, b in zip(starts[:-1], starts[1:])]
    finally:
        plt.close(fig)
    return out


def _poly_area(Q):
    return 0.5 * float(np.sum(Q[:, 0] * np.roll(Q[:, 1], -1) - np.roll(Q[:, 0], -1) * Q[:, 1]))


def _point_in_poly(pt, Q):
    x, y = pt
    xi, yi = Q[:, 0], Q[:, 1]
    xj, yj = np.roll(xi, 1), np.roll(yi, 1)
    c = ((yi > y) != (yj > y)) & (x < (xj - xi) * (y - yi) / np.where(yj - yi == 0, 1e-300, yj - yi) + xi)
    return bool(np.count_nonzero(c) % 2)


def section_regions(V, F, ax, h, res=0.1):
    """축 방향으로 내려다본 형상 그림자의 경계 : [(바깥 경계, [구멍들]), ...]  (단위 mm, 2D)"""
    from core import shape_general
    o = [k for k in range(3) if k != ax]
    fp = shape_general.footprint(V[F][:, :, o], res)
    loops = []
    for sg in _contour_segments(fp["grid"], fp["lo"], fp["cell"]):
        if len(sg) < 8:
            continue
        P = sg[:-1] if np.allclose(sg[0], sg[-1]) else sg
        k = len(P) // 2
        Q = np.vstack([_rdp(P[:k + 1], 0.4 * res)[:-1], _rdp(np.vstack([P[k:], P[:1]]), 0.4 * res)[:-1]])
        a = _poly_area(Q)
        if abs(a) < 0.5 * h * h:
            continue                     # 요소보다 작은 구멍/조각은 무시
        loops.append(Q if a > 0 else Q[::-1])
    depth = [sum(_point_in_poly(L[0], M) for j, M in enumerate(loops) if j != i) for i, L in enumerate(loops)]
    outers = [i for i, d in enumerate(depth) if d % 2 == 0]
    regions = []
    for i in outers:
        holes = []
        for j, d in enumerate(depth):
            if d % 2 == 1 and d == depth[i] + 1 and _point_in_poly(loops[j][0], loops[i]):
                holes.append(loops[j][::-1])          # 구멍은 시계 방향
        regions.append((loops[i], holes))
    return regions, fp


def internal_walls(V, F, ax, regions, fp, h, zs=None, dtol=0.5):
    """[v27.3] 단면 안쪽 벽 : 축 방향 가운데 단면의 경계 중 그림자(모든 단면의 합) 경계와 떨어져 있는 부분.
    예) 얇은 바깥 링 바깥면 (끝의 립이 링보다 바깥으로 나와 있어 그림자 경계와 다름).
    단면 사각형 메시가 이 선을 따라가게 하면 링-립이 꺾이는 모서리에서 계단이 생기지 않는다."""
    from scipy.spatial import cKDTree
    o = [k for k in range(3) if k != ax]
    lo3, hi3 = V.min(0), V.max(0)
    if zs is None:
        zs = [0.5 * (lo3[ax] + hi3[ax]) + 0.013 * h]
    g = fp["grid"]
    ii, jj = np.nonzero(g)
    if not len(ii):
        return []
    P2 = fp["lo"] + (np.stack([ii, jj], 1) + 0.5) * fp["cell"]
    ins, valid = _bucket_rays(V, F, o, ax, P2, np.asarray(zs, float), cell=max(1.0, 4 * fp["cell"]))
    # 경계 점 (그림자 경계 전체를 0.1 mm 간격으로)
    bpts = []
    for outer, holes in regions:
        for Q in [outer] + holes:
            C = np.vstack([Q, Q[:1]])
            for a, b in zip(C[:-1], C[1:]):
                n = max(1, int(np.ceil(np.hypot(*(b - a)) / 0.1)))
                bpts.append(a + (b - a) * (np.arange(n) / n)[:, None])
    btree = cKDTree(np.vstack(bpts))
    walls = []
    for k in range(len(zs)):
        mask = np.zeros_like(g)
        mask[ii, jj] = ins[:, k] & valid
        for sg in _contour_segments(mask, fp["lo"], fp["cell"]):
            if len(sg) < 8:
                continue
            closed = np.allclose(sg[0], sg[-1])
            P = sg[:-1] if closed else sg
            kk = len(P) // 2
            Q = np.vstack([_rdp(P[:kk + 1], 0.04)[:-1], _rdp(np.vstack([P[kk:], P[:1]]), 0.04)[:-1]]) if closed else _rdp(P, 0.04)
            d, _ = btree.query(Q)
            inner = d > dtol
            if inner.all() and closed:
                if abs(_poly_area(Q)) > h * h:
                    walls.append(("loop", Q))
                continue
            # 경계와 닿는 부분을 잘라낸 열린 선 (양 끝은 경계에서 1 mm 이상 떨어지게)
            inner = d > max(dtol, 1.0)
            n = len(Q)
            if not inner.any():
                continue
            start = int(np.argmin(inner)) if closed else 0
            idx = np.r_[np.arange(start, n), np.arange(0, start)] if closed else np.arange(n)
            run = []
            for i in list(idx) + [None]:
                if i is not None and inner[i]:
                    run.append(i)
                    continue
                if len(run) >= 3:
                    C = Q[run]
                    if np.hypot(*np.diff(C, axis=0).T).sum() >= 2 * h:
                        walls.append(("chain", C))
                run = []
    return walls


def _resample_chain(C, hb):
    s = np.r_[0.0, np.cumsum(np.hypot(*np.diff(C, axis=0).T))]
    k = max(1, int(round(s[-1] / hb)))
    t = np.linspace(0, s[-1], k + 1)
    return np.stack([np.interp(t, s, C[:, 0]), np.interp(t, s, C[:, 1])], 1)


def _corner_split_resample(Q, hb, win=0.6, deg=40.0):
    """닫힌 경계를 꺾인 점(창 ±win mm 에서 deg 도 이상)에서 나눠 각 구간을 hb 간격으로 다시 나눔"""
    C = np.vstack([Q, Q[:1]])
    seg = np.hypot(*np.diff(C, axis=0).T)
    s = np.r_[0.0, np.cumsum(seg)]
    L = s[-1]
    n = len(Q)
    si = s[:-1]

    def at(t):
        t = np.mod(t, L)
        return np.stack([np.interp(t, s, C[:, 0]), np.interp(t, s, C[:, 1])], 1)

    d1 = Q - at(si - win)
    d2 = at(si + win) - Q
    cosv = np.einsum("ij,ij->i", d1, d2) / np.maximum(np.linalg.norm(d1, axis=1) * np.linalg.norm(d2, axis=1), 1e-12)
    ang = np.degrees(np.arccos(np.clip(cosv, -1, 1)))
    cand = np.nonzero(ang > deg)[0]
    corners = []
    for i in cand[np.argsort(-ang[cand])]:
        if all(min(abs(si[i] - si[j]), L - abs(si[i] - si[j])) > 2 * win for j in corners):
            corners.append(i)
    corners.sort()
    if not corners:
        k = max(3, int(round(L / hb)))
        return at(np.linspace(0, L, k + 1)[:-1])
    out = []
    for a, b in zip(corners, corners[1:] + [corners[0]]):
        sa, sb = si[a], si[b]
        if sb <= sa:
            sb += L
        k = max(1, int(round((sb - sa) / hb)))
        out.append(at(np.linspace(sa, sb, k + 1)[:-1]))
    return np.vstack(out)


def quad_mesh_2d(regions, h, size_factor=1.0, walls=None):
    """단면을 순수 사각형으로 (gmsh : 사각형 위주 생성 -> 모두 사각형으로 세분). 반환 X2 (n,2), Q2 (m,4) 반시계"""
    import gmsh
    size = 2.0 * h * size_factor           # 세분하면 반으로 줄어듦
    if not gmsh.isInitialized():
        gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.add("section")
    try:
        surf = []
        for outer, holes in regions:
            cls = []
            for Q in [outer] + holes:
                R = _corner_split_resample(Q, 0.5 * size)
                pts = [gmsh.model.geo.addPoint(float(x), float(y), 0.0, size) for x, y in R]
                ln = [gmsh.model.geo.addLine(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]
                cls.append(gmsh.model.geo.addCurveLoop(ln))
            surf.append(gmsh.model.geo.addPlaneSurface(cls))
        # [v27.3] 안쪽 벽 선을 단면 메시에 심기 (메시가 이 선을 따라감)
        emb = {}
        for kind, C in (walls or []):
            ri = None
            for r, (outer, holes) in enumerate(regions):
                if _point_in_poly(C[0], outer) and not any(_point_in_poly(C[0], hq) for hq in holes):
                    ri = r
                    break
            if ri is None:
                continue
            if kind == "loop":
                R = _corner_split_resample(C, 0.5 * size)
                pts = [gmsh.model.geo.addPoint(float(x), float(y), 0.0, size) for x, y in R]
                ln = [gmsh.model.geo.addLine(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]
            else:
                R = _resample_chain(C, 0.5 * size)
                pts = [gmsh.model.geo.addPoint(float(x), float(y), 0.0, size) for x, y in R]
                ln = [gmsh.model.geo.addLine(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
            emb.setdefault(ri, []).extend(ln)
        gmsh.model.geo.synchronize()
        for ri, ln in emb.items():
            gmsh.model.mesh.embed(1, ln, 2, surf[ri])
        gmsh.option.setNumber("Mesh.Algorithm", 8)
        gmsh.option.setNumber("Mesh.RecombineAll", 1)
        gmsh.option.setNumber("Mesh.RecombinationAlgorithm", 1)
        gmsh.option.setNumber("Mesh.SubdivisionAlgorithm", 1)
        gmsh.option.setNumber("Mesh.MeshSizeMax", size)
        gmsh.option.setNumber("Mesh.MeshSizeMin", 0.1 * size)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
        gmsh.option.setNumber("Mesh.Smoothing", 5)
        gmsh.model.mesh.generate(2)
        nt, nc, _ = gmsh.model.mesh.getNodes()
        X2 = nc.reshape(-1, 3)[:, :2]
        idx = np.full(int(nt.max()) + 1, -1, np.int64)
        idx[nt.astype(np.int64)] = np.arange(len(nt))
        types, _, nodes = gmsh.model.mesh.getElements(2)
        quads = []
        for t, nn in zip(types, nodes):
            if gmsh.model.mesh.getElementProperties(t)[3] != 4:
                raise RuntimeError("단면 메시에 사각형이 아닌 요소가 남음")
            quads.append(idx[nn.astype(np.int64)].reshape(-1, 4))
        Q2 = np.vstack(quads)
    finally:
        gmsh.model.remove()
    used = np.unique(Q2)
    remap = -np.ones(len(X2), np.int64)
    remap[used] = np.arange(len(used))
    X2, Q2 = X2[used], remap[Q2]
    P = X2[Q2]
    area = 0.5 * np.sum(P[:, :, 0] * np.roll(P[:, :, 1], -1, 1) - np.roll(P[:, :, 0], -1, 1) * P[:, :, 1], axis=1)
    Q2[area < 0] = Q2[area < 0][:, ::-1]
    return X2, Q2


def _bucket_rays(V, F, o, ax, P2, zs, cell=1.0):
    """축 방향 광선(2D 위치 P2)마다 표면 통과 횟수 홀짝 -> zs 위치가 안쪽인지. 통과 횟수 홀수 광선은 무효"""
    T = V[F]
    ta, tb, td = T[:, :, o[0]], T[:, :, o[1]], T[:, :, ax]
    j = np.array([1.234567e-5, 2.345678e-5])
    R = P2 + j
    lo2 = np.minimum(R.min(0), np.stack([ta.min(), tb.min()])) - cell
    nb = (np.floor((np.maximum(R.max(0), np.stack([ta.max(), tb.max()])) - lo2) / cell) + 2).astype(np.int64)
    rb = np.floor((R - lo2) / cell).astype(np.int64)
    rkey = rb[:, 0] * nb[1] + rb[:, 1]
    order = np.argsort(rkey, kind="stable")
    rk_sorted = rkey[order]
    i0 = np.floor((ta.min(1) - lo2[0]) / cell).astype(np.int64)
    i1 = np.floor((ta.max(1) - lo2[0]) / cell).astype(np.int64)
    j0 = np.floor((tb.min(1) - lo2[1]) / cell).astype(np.int64)
    j1 = np.floor((tb.max(1) - lo2[1]) / cell).astype(np.int64)
    ni, nj = i1 - i0 + 1, j1 - j0 + 1
    cnt = ni * nj
    tri = np.repeat(np.arange(len(F)), cnt)
    off = np.arange(len(tri)) - np.repeat(np.cumsum(cnt) - cnt, cnt)
    bi = i0[tri] + off // nj[tri]
    bj = j0[tri] + off % nj[tri]
    bkey = bi * nb[1] + bj
    a = np.searchsorted(rk_sorted, bkey, "left")
    b = np.searchsorted(rk_sorted, bkey, "right")
    c = b - a
    keep = c > 0
    tri, a, c = tri[keep], a[keep], c[keep]
    hr, ht = [], []
    cs = np.cumsum(c)
    s0 = 0
    while s0 < len(tri):
        e0 = max(int(np.searchsorted(cs, (cs[s0 - 1] if s0 else 0) + 3_000_000, "right")), s0 + 1)
        tt, aa, cc = tri[s0:e0], a[s0:e0], c[s0:e0]
        t2 = np.repeat(tt, cc)
        k = np.arange(len(t2)) - np.repeat(np.cumsum(cc) - cc, cc)
        ray = order[np.repeat(aa, cc) + k]
        pa, pb = R[ray, 0], R[ray, 1]
        x0, x1, x2 = ta[t2, 0], ta[t2, 1], ta[t2, 2]
        y0, y1, y2 = tb[t2, 0], tb[t2, 1], tb[t2, 2]
        w0 = (x1 - pa) * (y2 - pb) - (x2 - pa) * (y1 - pb)
        w1 = (x2 - pa) * (y0 - pb) - (x0 - pa) * (y2 - pb)
        w2 = (x0 - pa) * (y1 - pb) - (x1 - pa) * (y0 - pb)
        hit = ((w0 > 0) & (w1 > 0) & (w2 > 0)) | ((w0 < 0) & (w1 < 0) & (w2 < 0))
        ws = w0 + w1 + w2
        t = (w0 * td[t2, 0] + w1 * td[t2, 1] + w2 * td[t2, 2]) / np.where(ws == 0, 1.0, ws)
        hr.append(ray[hit])
        ht.append(t[hit])
        s0 = e0
    nray = len(P2)
    hr = np.concatenate(hr) if hr else np.zeros(0, np.int64)
    ht = np.concatenate(ht) if ht else np.zeros(0)
    cntr = np.bincount(hr, minlength=nray)
    valid = (cntr % 2) == 0
    tmin = min(float(zs.min()), float(ht.min()) if len(ht) else float(zs.min())) - 1.0
    tmax = max(float(zs.max()), float(ht.max()) if len(ht) else float(zs.max())) + 1.0
    sc = 0.999 / (tmax - tmin)
    key = np.sort(hr + (ht - tmin) * sc)
    q = (np.arange(nray)[:, None] + ((zs - tmin) * sc)[None, :]).ravel()
    before = np.searchsorted(key, q, "left").reshape(nray, len(zs))
    base = np.concatenate([[0], np.cumsum(cntr)])[:-1]
    ins = ((before - base[:, None]) % 2) == 1
    return ins, valid


def column_fractions(V, F, ax, X2, Q2, znodes):
    """기둥 칸 (사각형 q, 축 구간 k) 마다 형상 안 부피 비율 (사각형 3x3 x 축 3 표본)"""
    o = [k for k in range(3) if k != ax]
    g = np.array([1.0 / 6, 0.5, 5.0 / 6])
    uu, vv = np.meshgrid(g, g, indexing="ij")
    uu, vv = uu.ravel(), vv.ravel()
    w = np.stack([(1 - uu) * (1 - vv), uu * (1 - vv), uu * vv, (1 - uu) * vv], 1)       # (9,4)
    P = X2[Q2]                                                                          # (m,4,2)
    S2 = np.einsum("sk,mkd->msd", w, P).reshape(-1, 2)                                  # (m*9,2)
    zs = sub_coords(znodes, 3)
    ins, valid = _bucket_rays(V, F, o, ax, S2, zs)
    m, nz = len(Q2), len(znodes) - 1
    ins = ins.reshape(m, 9, nz, 3)
    vq = valid.reshape(m, 9)
    nv = vq.sum(1)
    frac = (ins & vq[:, :, None, None]).sum(axis=(1, 3)) / np.maximum(nv * 3, 1)[:, None]
    return frac, int((~valid).sum())


def _fans(X2, Q2):
    """2D 꼭짓점마다 둘러싼 사각형 (각도 순서) 과 이웃끼리 변을 공유하는지"""
    n = len(X2)
    vq = np.repeat(np.arange(len(Q2)), 4)
    vv = Q2.ravel()
    o = np.argsort(vv, kind="stable")
    vq, vv = vq[o], vv[o]
    ptr = np.concatenate([[0], np.cumsum(np.bincount(vv, minlength=n))])
    cen = X2[Q2].mean(axis=1)
    edges = set()
    for k in range(4):
        a, b = Q2[:, k], Q2[:, (k + 1) % 4]
        for x, y in zip(np.minimum(a, b).tolist(), np.maximum(a, b).tolist()):
            edges.add((x, y))
    fans = []
    for v in range(n):
        qs = vq[ptr[v]:ptr[v + 1]]
        if not len(qs):
            fans.append((qs, np.zeros(0, bool)))
            continue
        ang = np.arctan2(cen[qs, 1] - X2[v, 1], cen[qs, 0] - X2[v, 0])
        qs = qs[np.argsort(ang)]
        link = np.zeros(len(qs), bool)          # link[i] : qs[i] 와 qs[i+1] 이 v 를 지나는 변을 공유
        for i in range(len(qs)):
            qa, qb = Q2[qs[i]], Q2[qs[(i + 1) % len(qs)]]
            common = (set(qa.tolist()) & set(qb.tolist())) - {v}
            link[i] = any((min(v, c), max(v, c)) in edges for c in common) and len(qs) > 1
        fans.append((qs, link))
    return fans


def _crit_local(qs, link, s_lo, s_hi, has_lo, has_hi):
    """꼭짓점 (v, 축높이) 주위 칸 : 아래층/위층 x 부채꼴. 선택/비선택이 각각 면으로 이어져 있는지"""
    n = len(qs)
    nodes = []
    for layer, has, sv in ((0, has_lo, s_lo), (1, has_hi, s_hi)):
        if has:
            for i in range(n):
                nodes.append((layer, i, bool(sv[i])))
    open_fan = not link.all() or n == 1
    out_virtual = open_fan or not (has_lo and has_hi)
    idx = {(l, i): t for t, (l, i, _) in enumerate(nodes)}
    N = len(nodes) + (1 if out_virtual else 0)
    sel = [nd[2] for nd in nodes] + ([False] if out_virtual else [])
    adj = [[] for _ in range(N)]

    def add(a, b):
        adj[a].append(b)
        adj[b].append(a)
    for (l, i), t in idx.items():
        if l == 0 and (1, i) in idx:
            add(t, idx[(1, i)])
        if link[i] and (l, (i + 1) % n) in idx and n > 1:
            add(t, idx[(l, (i + 1) % n)])
    if out_virtual:
        vt = N - 1
        for (l, i), t in idx.items():
            if not (has_lo and has_hi):
                add(t, vt)
            elif (not link[i]) or (not link[(i - 1) % n]):
                add(t, vt)
    for want in (True, False):
        group = [t for t in range(N) if sel[t] == want]
        if len(group) <= 1:
            continue
        seen, stack = {group[0]}, [group[0]]
        while stack:
            u = stack.pop()
            for w in adj[u]:
                if sel[w] == want and w not in seen:
                    seen.add(w)
                    stack.append(w)
        if len(seen) < len(group):
            return True
    return False


def clean_columns(sel, frac, X2, Q2, max_iter=60):
    """기둥 칸 선택 정리 : 가장 큰 덩어리만, 비다양체 꼭짓점은 칸을 더해서"""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    m, nz = sel.shape
    sel = sel.copy()
    nbr = _quad_neighbors_2d(Q2)
    fans = _fans(X2, Q2)
    added = dropped = 0
    # 3D 면 이웃 (같은 층 사각형 이웃 + 위아래)
    qa, qb = np.nonzero(nbr >= 0)
    pa = np.repeat(np.arange(m)[:, None], 4, 1)[nbr >= 0]
    pb = nbr[nbr >= 0]
    lay = np.arange(nz)
    E1 = np.stack([(pa[:, None] * nz + lay).ravel(), (pb[:, None] * nz + lay).ravel()], 1)
    E2 = np.stack([(np.arange(m)[:, None] * nz + lay[:-1]).ravel(), (np.arange(m)[:, None] * nz + lay[1:]).ravel()], 1)
    E = np.vstack([E1, E2])
    for _ in range(6):
        flat = sel.ravel()
        ok = flat[E[:, 0]] & flat[E[:, 1]]
        g = coo_matrix((np.ones(int(ok.sum())), (E[ok, 0], E[ok, 1])), shape=(m * nz, m * nz))
        _, lab = connected_components(g, directed=False)
        lab = np.where(flat, lab, -1)
        if flat.any():
            cnt = np.bincount(lab[flat])
            keep = int(np.argmax(cnt))
            drop = flat & (lab != keep)
            dropped += int(drop.sum())
            flat = flat & ~drop
            sel = flat.reshape(m, nz)
        changed = False
        for _it in range(max_iter):
            crit_cells = []
            for v, (qs, link) in enumerate(fans):
                if not len(qs):
                    continue
                S = sel[qs]                          # (n, nz)
                anyc = S.any(0)
                allc = S.all(0)
                for kk in range(nz + 1):
                    lo_ok, hi_ok = kk - 1 >= 0, kk <= nz - 1
                    s_lo = S[:, kk - 1] if lo_ok else None
                    s_hi = S[:, kk] if hi_ok else None
                    a_any = (lo_ok and anyc[kk - 1]) or (hi_ok and anyc[kk])
                    if not a_any:
                        continue
                    a_all = (lo_ok and allc[kk - 1] or not lo_ok) and (hi_ok and allc[kk] or not hi_ok)
                    if a_all and link.all() and lo_ok and hi_ok:
                        continue
                    if _crit_local(qs, link, s_lo, s_hi, lo_ok, hi_ok):
                        best, bc = -1.0, None
                        for layer, has in ((kk - 1, lo_ok), (kk, hi_ok)):
                            if not has:
                                continue
                            for q in qs:
                                if not sel[q, layer] and frac[q, layer] > best:
                                    best, bc = frac[q, layer], (q, layer)
                        if bc is not None:
                            crit_cells.append(bc)
            if not crit_cells:
                break
            for q, k in set(crit_cells):
                if not sel[q, k]:
                    sel[q, k] = True
                    added += 1
            changed = True
        if not changed:
            break
    return sel, added, dropped


def _quad_neighbors_2d(Q2):
    """2D 사각형의 변 k (k, k+1) 건너편 사각형 (-1 = 바깥 경계)"""
    m = len(Q2)
    e = np.concatenate([Q2[:, [0, 1]], Q2[:, [1, 2]], Q2[:, [2, 3]], Q2[:, [3, 0]]])
    q = np.tile(np.arange(m), 4)
    slot = np.repeat(np.arange(4), m)
    es = np.sort(e, 1)
    o = np.lexsort((es[:, 1], es[:, 0]))
    es, q, slot = es[o], q[o], slot[o]
    same = np.nonzero(np.all(es[1:] == es[:-1], axis=1))[0]
    nbr = -np.ones((m, 4), np.int64)
    nbr[q[same], slot[same]] = q[same + 1]
    nbr[q[same + 1], slot[same + 1]] = q[same]
    return nbr


def build_columns(sel, X2, Q2, znodes, ax):
    """선택된 기둥 칸 -> 헥사 (좌표, 연결), 겉면 사각형 (바깥 방향), 겉면 법선"""
    m, nz = sel.shape
    o = [k for k in range(3) if k != ax]
    qi, ki = np.nonzero(sel)
    L = nz + 1
    ids = np.concatenate([Q2[qi] * L + ki[:, None], Q2[qi] * L + ki[:, None] + 1], axis=1)
    used = np.unique(ids)
    remap = -np.ones(len(X2) * L, np.int64)
    remap[used] = np.arange(len(used))
    conn = remap[ids]
    X = np.zeros((len(used), 3))
    X[:, o[0]] = X2[used // L, 0]
    X[:, o[1]] = X2[used // L, 1]
    X[:, ax] = znodes[used % L]
    if hex_volume(X, conn[:1])[0] < 0:
        conn = conn[:, [0, 3, 2, 1, 4, 7, 6, 5]]
    nbr = _quad_neighbors_2d(Q2)
    faces, owner = [], []
    for k in range(4):
        nb = nbr[qi, k]
        ext = (nb < 0) | ~sel[np.maximum(nb, 0), ki]
        a, b = Q2[qi[ext], k], Q2[qi[ext], (k + 1) % 4]
        kk = ki[ext]
        faces.append(np.stack([a * L + kk, b * L + kk, b * L + kk + 1, a * L + kk + 1], 1))
        owner.append(np.nonzero(ext)[0])
    below = (ki == 0) | ~sel[qi, np.maximum(ki - 1, 0)]
    faces.append(Q2[qi[below]] * L + ki[below][:, None])
    owner.append(np.nonzero(below)[0])
    above = (ki == nz - 1) | ~sel[qi, np.minimum(ki + 1, nz - 1)]
    faces.append(Q2[qi[above]] * L + ki[above][:, None] + 1)
    owner.append(np.nonzero(above)[0])
    quads = remap[np.vstack(faces)]
    own = np.concatenate(owner)
    fc = X[quads].mean(axis=1)
    cc = X[conn[own]].mean(axis=1)
    nrm = np.cross(X[quads[:, 2]] - X[quads[:, 0]], X[quads[:, 3]] - X[quads[:, 1]])
    flip = np.einsum("ij,ij->i", nrm, fc - cc) < 0
    quads[flip] = quads[flip][:, ::-1]
    nrm[flip] *= -1
    qn = nrm / np.maximum(np.linalg.norm(nrm, axis=1), 1e-12)[:, None]
    return X, conn, quads, qn


# ---------------------------------------------------------------------------
# 4) 칸 고르기 + 비다양체 정리
# ---------------------------------------------------------------------------
def _block_lut():
    """2x2x2 칸 묶음(가운데 꼭짓점) 패턴 256 가지 중 비다양체(안쪽 또는 바깥 칸이 면으로 안 이어짐)"""
    idx = [(i & 1, (i >> 1) & 1, (i >> 2) & 1) for i in range(8)]
    adj = [[j for j in range(8) if sum(abs(idx[i][q] - idx[j][q]) for q in range(3)) == 1] for i in range(8)]

    def ncomp(cells):
        cells, n = set(cells), 0
        while cells:
            n += 1
            stack = [cells.pop()]
            while stack:
                c = stack.pop()
                for nb in adj[c]:
                    if nb in cells:
                        cells.remove(nb)
                        stack.append(nb)
        return n

    lut = np.zeros(256, bool)
    for code in range(256):
        ins = [i for i in range(8) if code >> i & 1]
        out = [i for i in range(8) if not code >> i & 1]
        lut[code] = ncomp(ins) > 1 or ncomp(out) > 1
    return lut


_LUT = None


def clean_cells(sel, frac):
    """가장 큰 덩어리만 남기고, 막힌 작은 빈칸은 채우고, 비다양체 꼭짓점/모서리는 칸을 더해 정리"""
    global _LUT
    from scipy import ndimage
    if _LUT is None:
        _LUT = _block_lut()
    sel = sel.copy()
    nx, ny, nz = sel.shape
    added = dropped = 0
    for _ in range(10):
        changed = False
        lab, n = ndimage.label(sel)
        if n > 1:
            sizes = np.bincount(lab.ravel())
            sizes[0] = 0
            keep = int(np.argmax(sizes))
            dropped += int(sel.sum() - sizes[keep])
            sel = lab == keep
        lab0, n0 = ndimage.label(~sel)
        if n0 > 1:
            border = np.unique(np.concatenate([lab0[0].ravel(), lab0[-1].ravel(), lab0[:, 0].ravel(),
                                               lab0[:, -1].ravel(), lab0[:, :, 0].ravel(), lab0[:, :, -1].ravel()]))
            sizes = np.bincount(lab0.ravel())
            for L in range(1, n0 + 1):
                if L not in border and sizes[L] <= 8:
                    sel[lab0 == L] = True
                    added += int(sizes[L])
                    changed = True
        for _it in range(500):
            P = np.pad(sel, 1)
            FP = np.pad(frac, 1, constant_values=-1.0)
            code = np.zeros((nx + 1, ny + 1, nz + 1), np.int32)
            for i in range(8):
                dx, dy, dz = i & 1, (i >> 1) & 1, (i >> 2) & 1
                code |= P[dx:dx + nx + 1, dy:dy + ny + 1, dz:dz + nz + 1].astype(np.int32) << i
            crit = _LUT[code]
            if not crit.any():
                break
            vi, vj, vk = np.nonzero(crit)
            best = np.full(len(vi), -np.inf)
            bi = np.zeros(len(vi), np.int64)
            for i in range(8):
                dx, dy, dz = i & 1, (i >> 1) & 1, (i >> 2) & 1
                ii, jj, kk = vi + dx, vj + dy, vk + dz
                f = np.where(P[ii, jj, kk], -np.inf, FP[ii, jj, kk])
                f = np.where((ii == 0) | (jj == 0) | (kk == 0) | (ii == nx + 1) | (jj == ny + 1) | (kk == nz + 1),
                             -np.inf, f)
                better = f > best
                best = np.where(better, f, best)
                bi = np.where(better, i, bi)
            ok = np.isfinite(best)
            if not ok.any():
                break
            dx, dy, dz = bi & 1, (bi >> 1) & 1, (bi >> 2) & 1
            ci, cj, ck = (vi + dx - 1)[ok], (vj + dy - 1)[ok], (vk + dz - 1)[ok]
            new = ~sel[ci, cj, ck]
            sel[ci, cj, ck] = True
            added += int(new.sum())
            changed = True
        if not changed:
            break
    return sel, added, dropped


# ---------------------------------------------------------------------------
# 5) 헥사 조립 + 경계층
# ---------------------------------------------------------------------------
def build_cells(sel, xs, ys, zs):
    nx, ny, nz = sel.shape
    ci, cj, ck = np.nonzero(sel)
    used = np.zeros((nx + 1, ny + 1, nz + 1), bool)
    for dx, dy, dz in CORNERS:
        used[ci + dx, cj + dy, ck + dz] = True
    vid = -np.ones(used.shape, np.int64)
    vi, vj, vk = np.nonzero(used)
    vid[vi, vj, vk] = np.arange(len(vi))
    X = np.stack([xs[vi], ys[vj], zs[vk]], 1)
    conn = np.stack([vid[ci + dx, cj + dy, ck + dz] for dx, dy, dz in CORNERS], 1)
    P = np.pad(sel, 1)
    quads, qn = [], []
    for ax, side, offs in FACE_DEF:
        sh = [0, 0, 0]
        sh[ax] = side
        nb = P[1 + sh[0]:1 + sh[0] + nx, 1 + sh[1]:1 + sh[1] + ny, 1 + sh[2]:1 + sh[2] + nz]
        fi, fj, fk = np.nonzero(sel & ~nb)
        quads.append(np.stack([vid[fi + a, fj + b, fk + c] for a, b, c in offs], 1))
        v = np.zeros(3)
        v[ax] = side
        qn.append(np.repeat(v[None], len(fi), 0))
    return X, conn, np.concatenate(quads), np.concatenate(qn)


def pillow(X, conn, quads):
    """겉면에 헥사 1 겹 : 겉면 절점은 그대로 겉면(나중에 CAD 로 이동), 안쪽 칸은 복사 절점에 연결"""
    B = np.unique(quads)
    N = len(X)
    copy = -np.ones(N, np.int64)
    copy[B] = N + np.arange(len(B))
    X2 = np.vstack([X, X[B]])
    conn2 = np.where(copy[conn] >= 0, copy[conn], conn)
    pil = np.hstack([copy[quads], quads])
    return X2, np.vstack([conn2, pil]), B, copy


def _quad_neighbors(quads):
    nq = len(quads)
    e = np.concatenate([quads[:, [0, 1]], quads[:, [1, 2]], quads[:, [2, 3]], quads[:, [3, 0]]])
    q = np.tile(np.arange(nq), 4)
    slot = np.repeat(np.arange(4), nq)
    es = np.sort(e, 1)
    o = np.lexsort((es[:, 1], es[:, 0]))
    es, q, slot = es[o], q[o], slot[o]
    same = np.nonzero(np.all(es[1:] == es[:-1], axis=1))[0]
    nbr = np.repeat(np.arange(nq)[:, None], 4, 1)
    nbr[q[same], slot[same]] = q[same + 1]
    nbr[q[same + 1], slot[same + 1]] = q[same]
    return nbr


def _adjacency(edges, n):
    from scipy.sparse import coo_matrix
    e = np.unique(np.sort(edges, 1), axis=0)
    e = e[e[:, 0] != e[:, 1]]
    A = coo_matrix((np.ones(2 * len(e)), (np.r_[e[:, 0], e[:, 1]], np.r_[e[:, 1], e[:, 0]])), shape=(n, n)).tocsr()
    return A, e


# ---------------------------------------------------------------------------
# 6) 겉면을 CAD 로 : 패치 지정 -> 절점 분류(면 / 모서리선 / 꼭짓점) -> 투영 -> 미끄럼 평활화
# ---------------------------------------------------------------------------
def assign_labels(X, quads, qn, SQ, iters=4):
    """계단면 사각형마다 마주보는 CAD 패치 (법선이 같은 쪽을 보는 가장 가까운 삼각형) + 다수결 정리"""
    nq = len(quads)
    qc = X[quads].mean(axis=1)
    _, _, ti = SQ.closest(qc, k=16, ndir=qn, ncos=0.05)
    lab = SQ.patch[ti]
    nbr = _quad_neighbors(quads)
    for _ in range(iters):
        L = lab[nbr]
        own = (L == lab[:, None]).sum(1)
        cnts = np.stack([(L == L[:, k:k + 1]).sum(1) for k in range(4)], 1)
        k = np.argmax(cnts, 1)
        mode, mc = L[np.arange(nq), k], cnts[np.arange(nq), k]
        ch = (own == 0) & (mc >= 2)
        if not ch.any():
            break
        lab = np.where(ch, mode, lab)
    return lab


def pillow_patch(X, conn, quads, qn, lab, p, lin=None):
    """패치 p 의 겉면에만 헥사 1 겹. 패치 경계(모서리선)에서는 그 겹의 옆면이 이웃 패치 겉면이 된다.
    -> 패치마다 따로 한 겹씩 넣으면 날카로운 모서리를 따라 모서리 요소가 생기고,
       계단 모양 경계를 곡선에 붙일 때 생기는 납작한 사각형(한 사각형에 모서리선 절점 3 개)이 없어진다."""
    sel = lab == p
    if not sel.any():
        return X, conn, quads, qn, lab
    Q = quads[sel]
    nbr = _quad_neighbors(quads)
    Np = np.unique(Q)
    N = len(X)
    copy = -np.ones(N, np.int64)
    copy[Np] = N + np.arange(len(Np))
    X = np.vstack([X, X[Np]])
    if lin is not None:
        # 복사 이력 : 새 절점의 뿌리(원래 계단면 절점)와, 어떤 패치의 경계층으로 생겼는지
        root, offs = lin
        lin[0] = np.concatenate([root, root[Np]])
        offs.extend(offs[n] + (p,) for n in Np.tolist())
    conn = np.where(copy[conn] >= 0, copy[conn], conn)
    conn = np.vstack([conn, np.hstack([copy[Q], Q])])
    qi = np.nonzero(sel)[0]
    side, slab, sqn = [], [], []
    for k in range(4):
        nb = nbr[qi, k]
        m = lab[nb] != p
        a, b = quads[qi[m], k], quads[qi[m], (k + 1) % 4]
        side.append(np.stack([copy[a], copy[b], b, a], 1))
        slab.append(lab[nb[m]])
        sqn.append(qn[nb[m]])
    Qo = quads[~sel]
    Qo = np.where(copy[Qo] >= 0, copy[Qo], Qo)
    quads = np.vstack([Q, Qo] + side)
    lab = np.concatenate([lab[sel], lab[~sel]] + slab)
    qn = np.vstack([qn[sel], qn[~sel]] + sqn)
    return X, conn, quads, qn, lab


def qn_node(quads, qn, N):
    nb = np.zeros((N, 3))
    np.add.at(nb, quads.ravel(), np.repeat(qn, 4, axis=0))
    return nb / np.maximum(np.linalg.norm(nb, axis=1), 1e-12)[:, None]


def place_by_lineage(X, root, offs, isB, bnd, t_root, nfall):
    """경계층 절점 위치 = 뿌리 절점(겉면 위) - 각 경계층 패치의 바깥 법선 x 두께 의 합.
    모서리선 위 뿌리에서 생긴 복사 절점은 이웃 패치 면 위로 (모서리를 따라 생기는 요소가 납작하지 않게)."""
    N = len(X)
    cop = np.nonzero(np.array([len(o) > 0 for o in offs], bool))[0]
    if not len(cop):
        return X
    pairs = {}
    for c in cop.tolist():
        r = int(root[c])
        for q in offs[c]:
            pairs[(r, q)] = None
    R = np.array([k[0] for k in pairs], np.int64)
    Pq = np.array([k[1] for k in pairs], np.int64)
    _, _, ti = bnd.SQ.closest(X[R], k=12, patch=Pq)
    nr = bnd.SQ.nrm[ti].copy()
    fb = nfall[R]
    flip = np.einsum("ij,ij->i", nr, fb) < 0
    nr[flip] *= -1
    for i, k in enumerate(pairs):
        pairs[k] = nr[i]
    X = X.copy()
    for c in cop.tolist():
        r = int(root[c])
        d = np.zeros(3)
        for q in offs[c]:
            d += pairs[(r, q)]
        X[c] = X[r] - t_root[r] * d
    bc = cop[isB[cop]]
    if len(bc):
        X[bc] = bnd.project(X, bc)[bc]
    return X


def place_near_surface(X, conn, B, nrmB, t):
    """겉면 바로 안쪽 절점 = 이웃 겉면 절점들에서 법선 반대쪽으로 t 만큼 들어간 위치의 평균"""
    from scipy.sparse import csr_matrix
    A, _ = _adjacency(conn[:, np.array(HEX_EDGES).ravel()].reshape(-1, 2), len(X))
    isB = np.zeros(len(X), bool)
    isB[B] = True
    off = np.zeros((len(X), 3))
    t = np.asarray(t, float)
    off[B] = X[B] - (t[:, None] if t.ndim else t) * nrmB
    mB = csr_matrix((isB.astype(float), (np.arange(len(X)), np.arange(len(X)))), shape=(len(X), len(X)))
    AB = A @ mB
    cnt = np.asarray(AB.sum(1)).ravel()
    S = AB @ off
    near = (~isB) & (cnt > 0)
    X = X.copy()
    X[near] = S[near] / cnt[near][:, None]
    return X, near


class Boundary:
    def __init__(self, X, quads, qn, lab, SQ, CQ, corners, h, use_features=True):
        self.SQ, self.CQ, self.h = SQ, CQ, h
        self.quads = quads
        N = len(X)
        self.qlab = lab
        self.B = np.unique(quads)
        # 절점별 패치 목록 (많은 순)
        pr, pc = np.unique(np.stack([quads.ravel(), np.repeat(lab, 4)], 1), axis=0, return_counts=True)
        o = np.lexsort((-pc, pr[:, 0]))
        node, labs = pr[o, 0], pr[o, 1]
        first = np.r_[True, node[1:] != node[:-1]]
        nlab = np.bincount(node, minlength=N)
        l1 = np.full(N, -1, np.int64)
        l1[node[first]] = labs[first]
        sec = np.r_[False, (node[1:] == node[:-1]) & first[:-1]]
        l2 = np.full(N, -1, np.int64)
        l2[node[sec]] = labs[sec]
        cls = np.full(N, -1, np.int64)         # -1 내부, 0 면, 1 모서리선, 2 꼭짓점
        cls[self.B] = 0
        ckey = np.full(N, -1, np.int64)
        cpos = np.zeros((N, 3))
        if use_features and len(CQ.trees):
            cand2 = self.B[nlab[self.B] >= 2]
            keys = CQ.key(l1[cand2], l2[cand2])
            has = CQ.has(keys)
            cls[cand2[has]] = 1
            ckey[cand2[has]] = keys[has]
            # 꼭짓점
            c3 = self.B[nlab[self.B] >= 3]
            if len(corners[0]) and len(c3):
                from scipy.spatial import cKDTree
                ct = cKDTree(corners[0])
                d, j = ct.query(X[c3], k=1)
                okc = d <= 1.0 * h
                for idx in np.nonzero(okc)[0]:
                    s = corners[1][j[idx]]
                    n0 = c3[idx]
                    if not (l1[n0] in s and l2[n0] in s):
                        okc[idx] = False
                # 같은 꼭짓점에 여러 절점이 붙으면 가장 가까운 것만
                order = np.argsort(d)
                taken = set()
                for idx in order:
                    if not okc[idx]:
                        continue
                    if j[idx] in taken:
                        okc[idx] = False
                        continue
                    taken.add(j[idx])
                cn = c3[okc]
                cls[cn] = 2
                cpos[cn] = corners[0][j[okc]]
        self.cls, self.l1, self.ckey, self.cpos = cls, l1, ckey, cpos
        A, E = _adjacency(np.concatenate([quads[:, [0, 1]], quads[:, [1, 2]], quads[:, [2, 3]], quads[:, [3, 0]]]), N)
        self.A = A
        self.degA = np.asarray(A.sum(1)).ravel()
        same = (cls[E[:, 0]] == 1) & (cls[E[:, 1]] == 1) & (ckey[E[:, 0]] == ckey[E[:, 1]])
        Ac, _ = _adjacency(E[same], N) if same.any() else (None, None)
        self.Ac = Ac
        self.degC = np.asarray(Ac.sum(1)).ravel() if Ac is not None else np.zeros(N)

    def project(self, X, nodes=None):
        """절점을 제자리(면/모서리선/꼭짓점)로 투영"""
        nodes = self.B if nodes is None else nodes
        Y = X.copy()
        c = self.cls[nodes]
        s = nodes[c == 0]
        if len(s):
            y, d, _ = self.SQ.closest(X[s], k=12, patch=self.l1[s])
            far = d > 1.0 * self.h
            if far.any():
                y2, _, _ = self.SQ.closest(X[s[far]], k=12)
                y[far] = y2
            Y[s] = y
        s = nodes[c == 1]
        if len(s):
            y, d = self.CQ.closest(X[s], self.ckey[s])
            far = ~np.isfinite(d) | (d > 0.6 * self.h)
            if far.any():
                y2, _, _ = self.SQ.closest(X[s[far]], k=12, patch=self.l1[s[far]])
                y[far] = y2
            Y[s] = y
        s = nodes[c == 2]
        Y[s] = self.cpos[s]
        return Y

    def relax(self, nodes):
        """이 절점들의 모서리선/꼭짓점 구속을 풀고 면 절점(첫째 패치 위에서 미끄러짐)으로"""
        nodes = np.asarray(nodes, np.int64)
        m = (self.cls[nodes] == 1) | (self.cls[nodes] == 2)
        self.cls[nodes[m]] = 0
        return int(m.sum())

    def project_points(self, P, nodes):
        """면(cls 0) 절점 후보 위치를 그 절점의 패치 위로"""
        y, _, _ = self.SQ.closest(P, k=12, patch=self.l1[nodes])
        return y

    def normals(self, X, fallback):
        """현재 겉면 사각형으로 계산한 절점 바깥 법선 (이상하면 계단면 법선)"""
        q = self.quads
        n = np.cross(X[q[:, 2]] - X[q[:, 0]], X[q[:, 3]] - X[q[:, 1]])
        acc = np.zeros((len(X), 3))
        np.add.at(acc, q.ravel(), np.repeat(n, 4, axis=0))
        nb = acc[self.B]
        L = np.linalg.norm(nb, axis=1)
        out = nb / np.maximum(L, 1e-12)[:, None]
        bad = (L < 1e-9) | (_dot(out, fallback) < 0.0)
        out[bad] = fallback[bad]
        return out

    def smooth(self, X, iters=8, lam=0.5):
        """겉면 위에서만 미끄러지는 평활화 (모서리선 절점은 모서리선을 따라, 꼭짓점은 고정)"""
        B = self.B
        sB = B[self.cls[B] == 0]
        cB = B[(self.cls[B] == 1) & (self.degC[B] > 0)]
        for _ in range(iters):
            avg = (self.A @ X) / np.maximum(self.degA, 1)[:, None]
            Xn = X.copy()
            Xn[sB] = X[sB] + lam * (avg[sB] - X[sB])
            if len(cB) and self.Ac is not None:
                avgc = (self.Ac @ X) / np.maximum(self.degC, 1)[:, None]
                Xn[cB] = X[cB] + lam * (avgc[cB] - X[cB])
            X = self.project(Xn)
        return X


# ---------------------------------------------------------------------------
# 7) 내부 평활화 + 나쁜 요소 국부 최적화
# ---------------------------------------------------------------------------
def _node_elems(conn, N):
    e = np.repeat(np.arange(len(conn)), conn.shape[1])
    n = conn.ravel()
    o = np.argsort(n, kind="stable")
    ptr = np.concatenate([[0], np.cumsum(np.bincount(n, minlength=N))])
    return ptr, e[o]


def smooth_interior(X, conn, free, iters=20, lam=0.5, guard=0.25, pil=None, n_free=8):
    """내부 절점 Laplacian 평활화. 처음 n_free 번은 그냥, 이후에는 나빠지는 이동을 되돌림.
    pil = (안쪽 절점, 바깥 절점, 바깥 법선, 최소 두께) : 경계층 요소가 법선 방향으로 뒤집히지 않게 유지"""
    A, _ = _adjacency(conn[:, np.array(HEX_EDGES).ravel()].reshape(-1, 2), len(X))
    deg = np.maximum(np.asarray(A.sum(1)).ravel(), 1)
    sj = hex_sj(X, conn)
    for it in range(iters):
        avg = (A @ X) / deg[:, None]
        Xn = X.copy()
        Xn[free] = X[free] + lam * (avg[free] - X[free])
        if pil is not None:
            c, o, nr, tmin = pil
            s = _dot(Xn[c] - Xn[o], nr) + tmin
            Xn[c] -= np.maximum(s, 0.0)[:, None] * nr
        sjn = hex_sj(Xn, conn)
        if it >= n_free:
            worse = (sjn < guard) & (sjn < sj - 1e-9)
            if worse.any():
                back = np.unique(conn[worse])
                back = back[free[back]]
                Xn[back] = X[back]
                sjn = hex_sj(Xn, conn)
        X, sj = Xn, sjn
    return X


def untangle(X, conn, free, bnd, iters=400, target=0.12, step0=0.06, h=2.0, log=None):
    """뒤집히거나 납작한 헥사 펴기 (Knupp 방식 경사하강).
    꼭짓점마다 J = e1.(e2 x e3) 가 target*|e1||e2||e3| 보다 작으면 그 꼭짓점의 J 를 키우는 방향으로
    관련 절점을 조금씩 민다. 내부 절점은 자유롭게, 겉면(면) 절점은 겉면을 따라서만 (다시 투영)."""
    surf = np.zeros(len(X), bool)
    if bnd is not None:
        surf[bnd.B[bnd.cls[bnd.B] == 0]] = True
    movable = free | surf
    step = step0 * h
    act = np.arange(len(conn))
    for it in range(iters):
        if it % 25 == 0:
            sj = hex_sj(X, conn)
            badn = np.zeros(len(X), bool)
            badn[conn[sj < target + 0.05].ravel()] = True
            act = np.nonzero(badn[conn].any(axis=1))[0]
            if not len(act):
                break
        P = X[conn[act]]
        G = np.zeros_like(X)
        nviol = 0
        for k, (a, b, d) in enumerate(_SJ_NB):
            e1, e2, e3 = P[:, a] - P[:, k], P[:, b] - P[:, k], P[:, d] - P[:, k]
            c23, c31, c12 = np.cross(e2, e3), np.cross(e3, e1), np.cross(e1, e2)
            J = np.einsum("ij,ij->i", e1, c23)
            lim = target * np.linalg.norm(e1, axis=1) * np.linalg.norm(e2, axis=1) * np.linalg.norm(e3, axis=1)
            v = J < lim
            if not v.any():
                continue
            nviol += int(v.sum())
            el = conn[act[v]]
            np.add.at(G, el[:, a], -c23[v])
            np.add.at(G, el[:, b], -c31[v])
            np.add.at(G, el[:, d], -c12[v])
            np.add.at(G, el[:, k], c23[v] + c31[v] + c12[v])
        if nviol == 0:
            break
        G[~movable] = 0.0
        gn = np.linalg.norm(G, axis=1)
        m = gn > 0
        D = np.zeros_like(X)
        D[m] = -G[m] / gn[m][:, None] * step
        sm = m & surf
        X = X + D
        if sm.any():
            nodes = np.nonzero(sm)[0]
            X[nodes] = bnd.project_points(X[nodes], nodes)
        step = max(step * 0.993, 0.004 * h)
    if log:
        sj = hex_sj(X, conn)
        log(f"   펴기 후: 최소 SJ {sj.min():.3f}, 0 이하 {int((sj <= 0).sum())}개")
    return X


def _corner_terms(P):
    """P (n,8,3) -> 꼭짓점별 scaled Jacobian s (n,8) 와 그 기울기 계산에 필요한 값들"""
    out = []
    for k, (a, b, d) in enumerate(_SJ_NB):
        e1, e2, e3 = P[:, a] - P[:, k], P[:, b] - P[:, k], P[:, d] - P[:, k]
        l1, l2, l3 = (np.maximum(np.linalg.norm(e, axis=1), 1e-12) for e in (e1, e2, e3))
        c23, c31, c12 = np.cross(e2, e3), np.cross(e3, e1), np.cross(e1, e2)
        L = l1 * l2 * l3
        sv = np.einsum("ij,ij->i", e1, c23) / L
        out.append((k, a, b, d, sv, e1, e2, e3, l1, l2, l3, c23, c31, c12, L))
    return out


def untangle_global(X, conn, free, bnd, h, tau=0.15, iters=300, log=None):
    """매끈한 전체 목적함수 f = sum max(0, tau - s_c)^2 (s_c = 꼭짓점 scaled Jacobian) 를 경사하강 + 줄찾기로 줄임.
    f 가 늘어나는 이동은 받지 않으므로 망가지지 않는다 (단조 감소)."""
    surf = np.zeros(len(X), bool)
    if bnd is not None:
        surf[bnd.B[bnd.cls[bnd.B] == 0]] = True
    movable = free | surf
    ptr, ne = _node_elems(conn, len(X))

    def f_and_grad(Xc, els, need_grad=True):
        P = Xc[conn[els]]
        f = 0.0
        G = np.zeros_like(Xc) if need_grad else None
        for (k, a, b, d, sv, e1, e2, e3, l1, l2, l3, c23, c31, c12, L) in _corner_terms(P):
            v = tau - sv
            m = v > 0
            if not m.any():
                continue
            f += float(np.sum(v[m] ** 2))
            if need_grad:
                w = (-2.0 * v[m])[:, None]
                el = conn[els[m]]
                ga = c23[m] / L[m][:, None] - sv[m][:, None] * e1[m] / (l1[m] ** 2)[:, None]
                gb = c31[m] / L[m][:, None] - sv[m][:, None] * e2[m] / (l2[m] ** 2)[:, None]
                gd = c12[m] / L[m][:, None] - sv[m][:, None] * e3[m] / (l3[m] ** 2)[:, None]
                np.add.at(G, el[:, a], w * ga)
                np.add.at(G, el[:, b], w * gb)
                np.add.at(G, el[:, d], w * gd)
                np.add.at(G, el[:, k], -w * (ga + gb + gd))
        return f, G

    els = np.arange(len(conn))
    for it in range(iters):
        if it % 20 == 0:
            sj = hex_sj(X, conn)
            badn = np.zeros(len(X), bool)
            badn[conn[sj < tau + 0.05].ravel()] = True
            badn &= movable
            if not badn.any():
                break
            els = np.nonzero(badn[conn].any(axis=1))[0]
        f0, G = f_and_grad(X, els)
        if f0 <= 1e-12:
            break
        G[~movable] = 0.0
        if surf.any():
            # 겉면 절점은 겉면 법선 성분을 빼고 (접선 방향으로만)
            nb = np.nonzero(surf & (np.abs(G).sum(1) > 0))[0]
            if len(nb):
                y, _, ti = bnd.SQ.closest(X[nb], k=8, patch=bnd.l1[nb])
                nr = bnd.SQ.nrm[ti]
                G[nb] -= np.einsum("ij,ij->i", G[nb], nr)[:, None] * nr
        gmax = float(np.linalg.norm(G, axis=1).max())
        if gmax <= 0:
            break
        moved = False
        for dlt in (0.1, 0.05, 0.02, 0.008, 0.003):
            Xn = X - (dlt * h / gmax) * G
            nb = np.nonzero(surf & (np.abs(G).sum(1) > 0))[0]
            if len(nb):
                Xn[nb] = bnd.project_points(Xn[nb], nb)
            f1, _ = f_and_grad(Xn, els, need_grad=False)
            if f1 < f0:
                X, moved = Xn, True
                break
        if not moved:
            break
    if log:
        sj = hex_sj(X, conn)
        log(f"   전체 펴기 후: 최소 SJ {sj.min():.3f}, 0 이하 {int((sj <= 0).sum())}개, SJ<0.2 {int((sj < 0.2).sum())}개")
    return X


_DIRS = np.array([(i, j, k) for i in (-1, 0, 1) for j in (-1, 0, 1) for k in (-1, 0, 1)
                  if (i, j, k) != (0, 0, 0) and abs(i) + abs(j) + abs(k) != 2], float)
_DIRS /= np.linalg.norm(_DIRS, axis=1)[:, None]


def _batches(nodes, ptr, ne):
    """같은 요소를 공유하지 않는 절점끼리 묶음 (한 묶음 안에서는 동시에 옮겨도 서로 영향 없음)"""
    rest, out = [int(n) for n in nodes], []
    while rest:
        locked, b, r2 = set(), [], []
        for n in rest:
            els = ne[ptr[n]:ptr[n + 1]].tolist()
            if locked.isdisjoint(els):
                b.append(n)
                locked.update(els)
            else:
                r2.append(n)
        out.append(np.array(b, dtype=np.int64))
        rest = r2
    return out


def _improve_batch(X, conn, nodes, ptr, ne, h, bnd, surf, Lap=None, deg=None, offs=None, dmax=0.0):
    """같은 요소를 공유하지 않는 절점 묶음을 한꺼번에 국부 최적화 (단조 개선 : 좋아질 때만 이동)"""
    cnt = ptr[nodes + 1] - ptr[nodes]
    inc_node = np.repeat(np.arange(len(nodes)), cnt)
    inc_elem = np.concatenate([ne[ptr[n]:ptr[n + 1]] for n in nodes])
    cc = conn[inc_elem]
    slot = np.argmax(cc == nodes[inc_node][:, None], axis=1)
    Pe = X[cc]
    rows = np.arange(len(inc_elem))
    is_o = offs[nodes] if offs is not None else np.zeros(len(nodes), bool)
    is_s = surf[nodes] & ~is_o
    # 절점마다 주변 모서리 길이 (이동 폭 기준)
    Ln = np.zeros(len(nodes))
    for k in range(8):
        d = np.linalg.norm(Pe[:, k] - Pe[rows, slot], axis=1)
        np.maximum.at(Ln, inc_node, d * 0.0)
    el_len = np.linalg.norm(Pe[:, [1, 2, 3, 0, 5, 6, 7, 4, 4, 5, 6, 7]] - Pe[:, [0, 1, 2, 3, 4, 5, 6, 7, 0, 1, 2, 3]], axis=2).mean(1)
    Ln = np.bincount(inc_node, weights=el_len, minlength=len(nodes)) / np.maximum(cnt, 1)

    def qual(Pc):
        # 목적 (최대화) : 뒤집힌 요소를 가장 먼저 펴고, 그다음 나쁜 요소를 올리고, 다른 요소는 망치지 않게
        Pe[rows, slot] = Pc[inc_node]
        v = hex_sj_coords(Pe)
        mn = np.full(len(nodes), np.inf)
        np.minimum.at(mn, inc_node, v)
        tot = np.bincount(inc_node, weights=np.minimum(v, 0.3) + 3.0 * np.minimum(v, 0.0), minlength=len(nodes))
        q = tot + mn
        if is_o.any():
            # 겉면에서 dmax 보다 멀어지는 후보는 버림 (형상 오차 제한)
            _, d, _ = bnd.SQ.closest(Pc[is_o], k=12)
            qo = q[is_o]
            qo[d > dmax] = -np.inf
            q[is_o] = qo
        return q

    is_c = np.zeros(len(nodes), bool)
    if bnd is not None:
        is_c = (bnd.cls[nodes] == 1) & ~is_s & ~is_o

    def proj(Pc):
        if is_s.any() or is_c.any():
            Pc = Pc.copy()
            if is_s.any():
                Pc[is_s] = bnd.project_points(Pc[is_s], nodes[is_s])
            if is_c.any():
                y, d = bnd.CQ.closest(Pc[is_c], bnd.ckey[nodes[is_c]])
                ok = np.isfinite(d)
                yy = Pc[is_c]
                yy[ok] = y[ok]
                Pc[is_c] = yy
        return Pc

    best = X[nodes].copy()
    bq = qual(best)
    q0 = bq.copy()
    # 후보 1 : 이웃 평균 위치 (Laplacian) 쪽으로
    if Lap is not None:
        avg = (Lap[nodes] @ X) / np.maximum(deg[nodes], 1)[:, None]
        for f in (1.0, 0.5, 0.25):
            Pc = proj(best + f * (avg - best))
            q = qual(Pc)
            m = q > bq + 1e-9
            best[m], bq[m] = Pc[m], q[m]
    # 후보 2 : 14 방향 패턴 탐색 (이동 폭은 절점 주변 모서리 길이 기준)
    for frac in (0.3, 0.12, 0.05, 0.02):
        step = (frac * Ln)[:, None]
        for _ in range(3):
            Qb, Pb = bq.copy(), best.copy()
            for dv in _DIRS:
                Pc = proj(best + step * dv)
                q = qual(Pc)
                m = q > Qb + 1e-9
                Qb[m] = q[m]
                Pb[m] = Pc[m]
            if not (Qb > bq + 1e-9).any():
                break
            best, bq = Pb, Qb
    X[nodes] = best
    return X, float((bq - q0).sum())


def optimize_bad(X, conn, free, bnd, h, thr=0.3, passes=5, log=None, offs=None, dmax=0.0):
    """품질 나쁜 요소의 절점을 26 방향으로 조금씩 옮겨 보며 주변 요소의 최소 scaled Jacobian 을 올림.
    내부 절점은 자유롭게, 겉면(면) 절점은 겉면 위에서만. 같은 요소를 공유하지 않는 절점끼리 한꺼번에 계산."""
    ptr, ne = _node_elems(conn, len(X))
    surf = np.zeros(len(X), bool)
    curve = np.zeros(len(X), bool)
    if bnd is not None:
        surf[bnd.B[bnd.cls[bnd.B] == 0]] = True
        curve[bnd.B[bnd.cls[bnd.B] == 1]] = True
    Lap, _ = _adjacency(conn[:, np.array(HEX_EDGES).ravel()].reshape(-1, 2), len(X))
    deg = np.asarray(Lap.sum(1)).ravel()
    for ps in range(passes):
        sj = hex_sj(X, conn)
        bad = np.nonzero(sj < thr)[0]
        if not len(bad):
            break
        nodes = np.unique(conn[bad])
        mov = free[nodes] | surf[nodes] | curve[nodes]
        if offs is not None:
            mov |= offs[nodes]
        nodes = nodes[mov]
        if not len(nodes):
            break
        gain = 0.0
        for b in _batches(nodes, ptr, ne):
            X, g = _improve_batch(X, conn, b, ptr, ne, h, bnd, surf, Lap, deg, offs, dmax)
            gain += g
        if log:
            sj = hex_sj(X, conn)
            log(f"   최적화 {ps + 1}: 최소 SJ {sj.min():.3f}, 0 이하 {int((sj <= 0).sum())}개, SJ<0.2 {int((sj < 0.2).sum())}개")
        if gain < 1e-6:
            break
    return X


# ---------------------------------------------------------------------------
# 8) 한 번의 시도 / 전체
# ---------------------------------------------------------------------------
def local_thickness(XB, nB):
    """겉면 절점마다 반대쪽을 보는 가장 가까운 겉면 절점까지 거리 (얇은 막 두께)"""
    from scipy.spatial import cKDTree
    k = int(min(32, len(XB)))
    d, j = cKDTree(XB).query(XB, k=k)
    opp = np.einsum("ijk,ik->ij", nB[j], nB) < -0.3
    return np.where(opp, d, np.inf).min(axis=1)


def _fit(X, conn, quads, qn, SQ, CQ, corners, h, opts, log=None):
    """공통 : 패치별 경계층 -> 겉면을 CAD 로 -> 경계층 절점 배치 -> 평활화 -> 펴기 -> 국부 최적화"""
    lab = assign_labels(X, quads, qn, SQ)
    if opts.get("features", True):
        order = [int(p) for p in np.argsort(-np.bincount(lab)) if (lab == p).any()]
    else:
        lab = np.zeros(len(lab), np.int64)
        order = [0]
    n0 = len(X)
    # 계단면 절점마다 붙은 칸 모서리의 최소 길이 (경계층 두께가 이보다 충분히 작아야 칸이 안 뒤집힘)
    Ec = conn[:, np.array(HEX_EDGES).ravel()].reshape(-1, 2)
    Lc = np.linalg.norm(X[Ec[:, 0]] - X[Ec[:, 1]], axis=1)
    dcell = np.full(n0, np.inf)
    np.minimum.at(dcell, Ec[:, 0], Lc)
    np.minimum.at(dcell, Ec[:, 1], Lc)
    lin = [np.arange(n0), [()] * n0]
    for p in order:
        X, conn, quads, qn, lab = pillow_patch(X, conn, quads, qn, lab, p, lin)
    root, offs = lin
    bnd = Boundary(X, quads, qn, lab, SQ, CQ, corners, h, use_features=opts.get("features", True))
    B = bnd.B
    isB = np.zeros(len(X), bool)
    isB[B] = True
    nfall_all = qn_node(quads, qn, len(X))
    # 1) 뿌리 겉면 절점(복사 아님)을 CAD 로
    is_root = np.array([len(o) == 0 for o in offs], bool)
    rB = B[is_root[B]]
    X = bnd.project(X, rB)
    # 2) 두께 : 얇은 막 / 곡률 / 기본값 중 작은 것
    nrm_r = nfall_all[rB]
    thick = local_thickness(X[rB], nrm_r)
    kap = boundary_curvature(X, rB, nrm_r, bnd)
    t_root = np.zeros(len(X))
    t_root[rB] = np.maximum(np.minimum.reduce([np.full(len(rB), opts["pillow_frac"] * h), 0.3 * thick,
                                               0.45 / np.maximum(kap, 1e-9), 0.35 * dcell[rB]]), 0.03 * h)
    # 3) 복사 절점 배치 -> 겉면 미끄럼 평활화 -> 내부 복사 절점 다시 배치
    X = place_by_lineage(X, root, offs, isB, bnd, t_root, nfall_all)
    X = bnd.smooth(X, iters=opts["surf_iters"])
    inner = np.array([len(o) > 0 for o in offs], bool) & ~isB
    Xp = place_by_lineage(X, root, offs, isB, bnd, t_root, nfall_all)
    X[inner] = Xp[inner]
    free = ~isB
    sj = hex_sj(X, conn)
    if log:
        log(f"   배치 후: 최소 SJ {sj.min():.3f}, 0 이하 {int((sj <= 0).sum())}개, SJ<0.2 {int((sj < 0.2).sum())}개")
    X = untangle(X, conn, free, bnd, h=h, iters=250, log=log)
    X = smooth_interior(X, conn, free, iters=opts["smooth_iters"], n_free=0)
    X = untangle_global(X, conn, free, bnd, h, iters=400, log=log)
    X = optimize_bad(X, conn, free, bnd, h, thr=0.2, passes=int(opts.get("opt_passes", 4)), log=log)
    X = untangle_global(X, conn, free, bnd, h, iters=300, log=log)
    X = _rescue(X, conn, free, bnd, h, log)
    return X, conn, quads, bnd


def _rescue(X, conn, free, bnd, h, log=None, sj_ok=0.05):
    """[v27.2] 그래도 뒤집히거나 납작한 요소가 남으면 (바깥 링-립처럼 모서리선이 좁게 꺾이는 곳)
    1) 그 근처 절점의 모서리선/꼭짓점 구속을 풀어 면 위에서 자유롭게 미끄러지게 하고
    2) 그래도 남으면 그 요소의 겉면 절점이 CAD 면에서 최대 0.15 h 까지 벗어나는 것을 허용해서 편다.
    (모서리가 아주 조금 둥글어지는 대신 요소가 뒤집히지 않음)"""
    ptr, ne = _node_elems(conn, len(X))
    isB = ~free

    def grow(nodes, k):
        for _ in range(k):
            nodes = np.unique(np.concatenate([conn[ne[ptr[n]:ptr[n + 1]]].ravel() for n in nodes]))
        return nodes

    for stage, (rings, dmax_f, passes) in enumerate([(1, 0.0, 4), (1, 0.08, 4), (2, 0.15, 4), (2, 0.25, 6),
                                                       (3, 0.35, 6)], 1):
        sj = hex_sj(X, conn)
        bad = np.nonzero(sj <= sj_ok)[0]
        if not len(bad):
            break
        ring = grow(np.unique(conn[bad]), rings)
        if stage == 1:
            n_rel = bnd.relax(ring)
            X = optimize_bad(X, conn, free, bnd, h, thr=0.2, passes=passes)
            if log:
                log(f"   구제 1 (모서리선 구속 {n_rel}개 풀기): 0 이하 {int((hex_sj(X, conn) <= 0).sum())}개")
        else:
            bnd.relax(ring)
            offs = np.zeros(len(X), bool)
            offs[ring[isB[ring]]] = True
            dmax = dmax_f * h
            X = optimize_bad(X, conn, free, bnd, h, thr=0.2, passes=passes, offs=offs, dmax=dmax)
            if log:
                log(f"   구제 {stage} (겉면 절점 {int(offs.sum())}개, 면에서 최대 {dmax:.2f} mm 허용): "
                    f"0 이하 {int((hex_sj(X, conn) <= 0).sum())}개")
    return X


def boundary_curvature(X, B, nrmB, bnd):
    """겉면 절점의 곡률 근사 : 이웃 겉면 절점과의 법선 차이 / 거리 의 최대"""
    N = len(X)
    nfull = np.zeros((N, 3))
    nfull[B] = nrmB
    A = bnd.A.tocoo()
    i, j = A.row, A.col
    k = np.linalg.norm(nfull[i] - nfull[j], axis=1) / np.maximum(np.linalg.norm(X[i] - X[j], axis=1), 1e-9)
    kap = np.zeros(N)
    np.maximum.at(kap, i, k)
    return kap[B]


def _result(X, conn, quads, bnd, n_cells, added, dropped, bad_rays, att):
    sj = hex_sj(X, conn)
    vol = hex_volume(X, conn)
    return {"X": X, "conn": conn, "quads": quads, "sj": sj, "sj_min": float(sj.min()),
            "n_cells": n_cells, "n_pillow": len(conn) - n_cells, "added": added, "dropped": dropped,
            "bad_rays": bad_rays, "vol": float(vol.sum()), "att": att,
            "n_feat_nodes": int((bnd.cls == 1).sum()), "n_corner_nodes": int((bnd.cls == 2).sum()),
            "n_patch_used": int(len(np.unique(bnd.qlab)))}


def _attempt_cart(S, SQ, CQ, corners, ax, ctr, planes, h, att, opts):
    V, F = S["V"], S["F"]
    lo, hi = V.min(0), V.max(0)
    nodes = []
    for d in range(3):
        if d == ax:
            nodes.append(axis_nodes(lo[d], hi[d], planes, h))
        else:
            nodes.append(radial_nodes(lo[d], hi[d], ctr[d], h, shift=att.get("shift", 0.0) * h))
    subs = [sub_coords(n, 3) for n in nodes]
    inside, bad_rays = inside_lattice(V, F, subs)
    nx, ny, nz = (len(n) - 1 for n in nodes)
    frac = inside.reshape(nx, 3, ny, 3, nz, 3).mean(axis=(1, 3, 5))
    sel, added, dropped = clean_cells(frac >= att["thr"], frac)
    X, conn, quads, qn = build_cells(sel, *nodes)
    n_cells = len(conn)
    X, conn, quads, bnd = _fit(X, conn, quads, qn, SQ, CQ, corners, h, opts, opts.get('_log'))
    return _result(X, conn, quads, bnd, n_cells, added, dropped, bad_rays, att)


def _attempt_sweep(S, SQ, CQ, corners, ax, planes, h, att, opts, regions, walls=None):
    V, F = S["V"], S["F"]
    lo, hi = V.min(0), V.max(0)
    X2, Q2 = quad_mesh_2d(regions, h, att.get("s2", 1.0), walls if att.get("walls", True) else None)
    znodes = axis_nodes(lo[ax], hi[ax], planes, h)
    frac, bad_rays = column_fractions(V, F, ax, X2, Q2, znodes)
    sel, added, dropped = clean_columns(frac >= att["thr"], frac, X2, Q2)
    X, conn, quads, qn = build_columns(sel, X2, Q2, znodes, ax)
    n_cells = len(conn)
    X, conn, quads, bnd = _fit(X, conn, quads, qn, SQ, CQ, corners, h, opts, opts.get('_log'))
    r = _result(X, conn, quads, bnd, n_cells, added, dropped, bad_rays, att)
    r["n_quad2d"] = int(len(Q2))
    return r


def write_inp(path, X, conn, etype="C3D8H", header=""):
    lines = [f"** {header}", "*NODE"]
    # [v27.4] 0 에 아주 가까운 값이 '-0.000000' 으로 나가지 않게 0 으로 (고정소수점이라 지수 표기는 원래 없음)
    Xw = np.where(np.abs(X) < 5e-7, 0.0, X)
    lines += [f"{i}, {x:.6f}, {y:.6f}, {z:.6f}" for i, (x, y, z) in enumerate(Xw, 1)]
    lines.append(f"*ELEMENT, TYPE={etype}, ELSET=RUBBER")
    lines += [f"{e}, " + ", ".join(str(int(v)) for v in c) for e, c in enumerate(conn + 1, 1)]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def slice_polys(X, conn, normal_axis, z0):
    """헥사 메시를 좌표면(normal_axis = z0)으로 자른 단면 다각형 목록 + 요소 번호"""
    d = X[:, normal_axis] - z0
    D = d[conn]
    E = np.nonzero((D.min(1) < 0) & (D.max(1) > 0))[0]
    ea = np.array([a for a, _ in HEX_EDGES])
    eb = np.array([b for _, b in HEX_EDGES])
    da, db = D[E][:, ea], D[E][:, eb]
    cut = (da * db) < 0
    t = da / np.where(cut, da - db, 1.0)
    Pa, Pb = X[conn[E][:, ea]], X[conn[E][:, eb]]
    P = Pa + (Pb - Pa) * t[..., None]
    o = [k for k in range(3) if k != normal_axis]
    polys, ids = [], []
    for i in range(len(E)):
        pts = P[i][cut[i]][:, o]
        if len(pts) < 3:
            continue
        c = pts.mean(0)
        polys.append(pts[np.argsort(np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0]))])
        ids.append(E[i])
    return polys, np.array(ids, dtype=np.int64), o


def save_picture(png, X, conn, quads, sj, ax, title="", h=2.0):
    """메시 확인 그림 : 겉면(품질 색) / 축에 수직인 단면 / 축을 지나는 단면 / 품질 분포"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    cmap = plt.get_cmap("RdYlGn")
    fig = plt.figure(figsize=(15, 11.5))
    a1 = fig.add_subplot(2, 2, 1, projection="3d")
    nmin = np.full(len(X), np.inf)
    np.minimum.at(nmin, conn.ravel(), np.repeat(sj, conn.shape[1]))
    qsj = nmin[quads].min(axis=1)
    a1.add_collection3d(Poly3DCollection(X[quads], facecolors=cmap(np.clip(qsj, 0, 1)),
                                         edgecolors=(0, 0, 0, 0.3), linewidths=0.15))
    lo, hi = X.min(0), X.max(0)
    c, r = 0.5 * (lo + hi), 0.5 * float((hi - lo).max())
    a1.set_xlim(c[0] - r, c[0] + r)
    a1.set_ylim(c[1] - r, c[1] + r)
    a1.set_zlim(c[2] - r, c[2] + r)
    a1.view_init(elev=28, azim=35)
    a1.set_axis_off()
    a1.set_title("surface (color = worst scaled Jacobian at the face)")
    radial = [k for k in range(3) if k != ax][0]
    for pos, nax, name in ((2, ax, f"cross-section normal to axis {'XYZ'[ax]} (mid)"),
                           (3, radial, f"section through the axis (normal {'XYZ'[radial]})")):
        aa = fig.add_subplot(2, 2, pos)
        z0 = 0.5 * (lo[nax] + hi[nax]) + 0.37 * h
        polys, ids, o = slice_polys(X, conn, nax, z0)
        if polys:
            aa.add_collection(PolyCollection(polys, facecolors=cmap(np.clip(sj[ids], 0, 1)), edgecolors="k",
                                             linewidths=0.3))
            aa.autoscale()
        aa.set_aspect("equal")
        aa.set_xlabel("XYZ"[o[0]])
        aa.set_ylabel("XYZ"[o[1]])
        aa.set_title(name)
    a4 = fig.add_subplot(2, 2, 4)
    a4.hist(sj, bins=50, range=(min(0.0, float(sj.min())), 1.0), color="#4477aa")
    a4.set_yscale("log")
    a4.set_xlabel("scaled Jacobian (1 = perfect cube, 0 = flat)")
    a4.set_ylabel("elements")
    a4.set_title(f"min {sj.min():.3f}   mean {sj.mean():.3f}   <0.2: {int((sj < 0.2).sum())}   n = {len(sj)}")
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(png, dpi=100)
    plt.close(fig)


def mesh(src, out_inp, h=2.0, opts=None, log=print, png=None) -> dict:
    t0 = time.time()
    opts = dict(DEFAULTS, **(opts or {}))
    opts['_log'] = log
    S = cad_surface(str(src), opts["deflection"], opts["angular"])
    V, F = S["V"], S["F"]
    log(f"CAD: 면 {S['n_face']}개 (삼각형화 실패 {S['n_skip']}개), 삼각형 {len(F)}개, CAD 부피 {S['volume']}")
    if S.get("other_solids"):
        log(f"솔리드가 {1 + len(S['other_solids'])}개 -> 가장 큰 고무 본체만 메시. 따로 떨어진 솔리드(스너버 등) 부피 "
            f"{S['other_solids']} mm3 는 이 메시에 넣지 않음")
    P = make_patches(V, F, S["fid"], opts["feature_angle"], h, bool(opts.get("concave_smooth", False)))
    ax, ctr, bore = find_axis_center(V, F)
    planes = axial_planes(V, F, P["nrm"], P["area"], ax, h)
    log(f"패치 {P['n_patch']}개 (날카로운 모서리 변 {len(P['feat_v'])}개), 축={'XYZ'[ax]} 보어 그림자 {bore:.0f} mm2, "
        f"중심=({ctr[0]:.3f}, {ctr[1]:.3f}, {ctr[2]:.3f}), 축에 수직인 평면 {len(planes)}개")
    Pr, own = refine(V, F, 0.5 * h)
    ctx = {}

    def geom_ctx(concave):
        """패치/모서리선 묶음. concave=True : 오목한 모서리를 날카로운 모서리로 보지 않음 (경계층이 둥글게 지나감)"""
        if concave not in ctx:
            Pc = P if concave == bool(opts.get("concave_smooth", False)) else \
                make_patches(V, F, S["fid"], opts["feature_angle"], h, concave)
            ctx[concave] = (SurfQuery(Pr, Pc["tpatch"][own], Pc["nrm"][own]),
                            CurveQuery(V, Pc["feat_v"], Pc["feat_p"], 0.25 * h),
                            find_corners(V, Pc["feat_v"], Pc["feat_p"]), Pc)
        return ctx[concave]

    SQ, CQ, corners, _ = geom_ctx(bool(opts.get("concave_smooth", False)))
    regions = None
    walls = []
    method = str(opts.get("method", "auto")).lower()
    import importlib.util
    if bore > 0 and method in ("auto", "sweep") and importlib.util.find_spec("gmsh") is None:
        log("gmsh 가 없어 단면 쓸기 방식은 건너뜀 (pip install gmsh) -> 정육면체 격자 방식만 사용")
        method = "cart"
    if bore > 0 and method in ("auto", "sweep"):
        try:
            regions, fp_sec = section_regions(V, F, ax, h)
            try:
                walls = internal_walls(V, F, ax, regions, fp_sec, h) if opts.get("walls", True) else []
                if walls:
                    log(f"단면 안쪽 벽 {len(walls)}개 (예: 바깥 링 바깥면) -> 단면 메시가 따라가게 함 "
                        f"(닫힌 선 {sum(1 for k, _ in walls if k == 'loop')}개)")
            except Exception as e:
                walls = []
                log(f"단면 안쪽 벽 찾기 실패 ({e}) -> 없이 진행")
        except Exception as e:  # 단면을 못 만들면 정육면체 격자 방식으로
            log(f"단면 경계를 못 만듦 ({e}) -> 정육면체 격자 방식만 사용")
    attempts = []
    if regions:
        # 단면 사각형 크기 배율 : 2 = 요소크기 h (2 mm), 1 = 경계 근처 h/2. 굵은 것부터 시도하고 안 되면 잘게
        f2 = float(opts.get("section_size_factor", 2.0))
        # [v27.3] 3~5 번째 : 오목한 모서리를 둥글게 (바깥 링이 끝 립으로 꺾이는 형상에서 뒤집힘이 크게 줄어듦)
        attempts += [("sweep", {"thr": 0.5, "s2": f2}), ("sweep", {"thr": 0.5, "s2": 0.75 * f2}),
                     ("sweep", {"thr": 0.5, "s2": 0.75 * f2, "concave": True}),
                     ("sweep", {"thr": 0.5, "s2": f2, "concave": True}),
                     ("sweep", {"thr": 0.5, "s2": 0.5 * f2, "concave": True}),
                     ("sweep", {"thr": 0.6, "s2": f2}), ("sweep", {"thr": 0.5, "s2": 0.5 * f2})]
    if method in ("auto", "cart") and not regions:
        attempts += [("cart", {"shift": 0.0, "thr": 0.5}), ("cart", {"shift": 0.5, "thr": 0.5}),
                     ("cart", {"shift": 0.0, "thr": 0.6}), ("cart", {"shift": 0.5, "thr": 0.4})]
    attempts = attempts[:int(opts["max_attempts"])]
    cad_vol = S["volume"]

    def score(r):
        verr = abs(r["vol"] - cad_vol) / cad_vol if cad_vol else 0.0
        good = r["sj_min"] > opts["sj_fail"] and verr <= 0.05
        return (1 if good else 0, r["sj_min"] if good else -verr)

    best, tried = None, []
    for k, (kind, att) in enumerate(attempts):
        ta = time.time()
        try:
            if kind == "sweep":
                cc = bool(att.get("concave", opts.get("concave_smooth", False)))
                SQa, CQa, ca, Pa = geom_ctx(cc)
                if cc != bool(opts.get("concave_smooth", False)):
                    P0 = geom_ctx(not cc)[3]
                    if len(Pa["feat_v"]) == len(P0["feat_v"]) and Pa["n_patch"] == P0["n_patch"]:
                        log(f"시도 {k + 1} 건너뜀 (오목한 날카로운 모서리가 없어 앞의 시도와 같음)")
                        tried.append({"kind": kind, **att, "skipped": "same patches"})
                        continue
                r = _attempt_sweep(S, SQa, CQa, ca, ax, planes, h, att, opts, regions, walls)
            else:
                r = _attempt_cart(S, SQ, CQ, corners, ax, ctr, planes, h, att, opts)
        except Exception as e:
            log(f"시도 {k + 1} ({kind} {att}) 실패: {type(e).__name__}: {e}")
            tried.append({"kind": kind, **att, "error": str(e)[:200]})
            continue
        nbad = int((r["sj"] < 0.2).sum())
        verr = 100.0 * (r["vol"] - cad_vol) / cad_vol if cad_vol else float("nan")
        log(f"시도 {k + 1} ({'단면 쓸기' if kind == 'sweep' else '정육면체 격자'} {att}): 헥사 {len(r['conn'])}개 "
            f"(칸 {r['n_cells']} + 경계층 {r['n_pillow']}), 최소 SJ {r['sj_min']:.3f}, SJ<0.2 {nbad}개, "
            f"부피 오차 {verr:.2f}% ({time.time() - ta:.1f}s)")
        tried.append({"kind": kind, **att, "sj_min": round(r["sj_min"], 4), "n_hex": len(r["conn"]),
                      "vol_err_pct": round(verr, 2)})
        if best is None or score(r) > score(best):
            best = r
        sc = score(r)
        if sc[0] == 1 and r["sj_min"] >= opts["sj_accept"]:
            break
    if best is None:
        raise RuntimeError("모든 헥사 시도가 실패: " + "; ".join(str(t.get("error", "")) for t in tried))
    X, conn, sj = best["X"], best["conn"], best["sj"]
    vol = best["vol"]
    cad_vol = S["volume"]
    edges = np.linalg.norm(X[conn[:, [a for a, _ in HEX_EDGES]]] - X[conn[:, [b for _, b in HEX_EDGES]]], axis=2)
    verr_ok = (abs(vol - cad_vol) / cad_vol <= 0.10) if cad_vol else True
    res = {"ok": bool(best["sj_min"] > opts["sj_fail"] and verr_ok), "n_hex": int(len(conn)), "n_node": int(len(X)),
           "sj_min": round(float(sj.min()), 4), "sj_mean": round(float(sj.mean()), 4),
           "n_sj_lt_0p2": int((sj < 0.2).sum()), "n_sj_lt_0p1": int((sj < 0.1).sum()), "n_neg": int((sj <= 0).sum()),
           "edge_mean": round(float(edges.mean()), 3), "edge_min": round(float(edges.min()), 3),
           "edge_max": round(float(edges.max()), 3),
           "volume_mm3": round(vol, 1), "cad_volume_mm3": round(cad_vol, 1) if cad_vol else None,
           "volume_err_pct": round(100.0 * (vol - cad_vol) / cad_vol, 2) if cad_vol else None,
           "axis": "XYZ"[ax], "method": "sweep" if best.get("n_quad2d") else "cart",
           "n_patch": P["n_patch"], "n_feature_edges": int(len(P["feat_v"])),
           "n_feature_nodes": best["n_feat_nodes"], "n_corner_nodes": best["n_corner_nodes"],
           "cells_added_for_manifold": best["added"], "cells_dropped_islands": best["dropped"],
           "bad_rays": best["bad_rays"], "attempt": tried, "elem_size": h, "n_cad_face_skip": S["n_skip"],
           "other_solids_mm3": S.get("other_solids", []),
           "elapsed_s": round(time.time() - t0, 1)}
    if not res["ok"]:
        why = []
        if best["sj_min"] <= opts["sj_fail"]:
            why.append(f"헥사 품질이 너무 나쁨 (최소 scaled Jacobian {res['sj_min']}, 0 이하 {res['n_neg']}개, "
                       f"Abaqus 가 거부할 수 있음)")
        if not verr_ok:
            why.append(f"CAD 대비 부피 오차 {res['volume_err_pct']} % (형상 일부를 놓침)")
        res["error"] = "; ".join(why)
    write_inp(out_inp, X, conn, "C3D8H",
              f"grid_hex (YL_AUTO v26): pure hexa C3D8H, size {h} mm, SJ min {res['sj_min']}, src {Path(src).name}")
    if png:
        try:
            save_picture(png, X, conn, best["quads"], sj, ax,
                         f"{Path(src).name} : pure hexa C3D8H {res['n_hex']} el, size {h} mm, "
                         f"SJ min {res['sj_min']}, volume error {res['volume_err_pct']} %", h=h)
        except Exception as e:  # 그림 실패는 메시와 무관
            log(f"그림 저장 실패: {e}")
    return res


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    src, out = argv[1], argv[2]
    h = float(argv[3]) if len(argv) > 3 else 2.0
    png = argv[4] if len(argv) > 4 and argv[4] else None
    opts = json.loads(argv[5]) if len(argv) > 5 and argv[5] else {}
    try:
        res = mesh(src, out, h, opts=opts, png=png, log=lambda s: print(s, flush=True))
    except Exception as e:
        traceback.print_exc()
        res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    print("GRIDHEX_RESULT " + json.dumps(res, ensure_ascii=False), flush=True)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
