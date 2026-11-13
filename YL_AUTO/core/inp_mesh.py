# -*- coding: utf-8 -*-
"""
inp_mesh.py
  HyperMesh(또는 다른 전처리기)가 출력한 Abaqus INP에서 메시만 읽어
  1) 요소 방향/중간절점 순서 검사 및 자동 보정
  2) 부싱 축을 무조건 +Y 로 회전 (반경 방향 = XZ 평면)
  3) 경계면 추출 후 INNER(안쪽 접착면) / OUTER(바깥쪽 접착면) 노드 자동 선택
  4) LOAD_RP 를 (0,0,0)에 생성
을 수행한다. Abaqus/HyperMesh 없이 순수 numpy 로 동작한다.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# 요소 정의 (Abaqus 절점 순서 기준)
# ---------------------------------------------------------------------------
# 면은 코너 절점 인덱스(0-based)로 정의. tet10 면에는 중간절점도 포함.
TET4_FACES = [(0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0)]
TET10_FACE_MID = {  # 코너 면 -> 해당 면의 중간절점 (Abaqus C3D10: 5=12,6=23,7=31,8=14,9=24,10=34)
    (0, 1, 2): (4, 5, 6),
    (0, 3, 1): (7, 8, 4),
    (1, 3, 2): (8, 9, 5),
    (2, 3, 0): (9, 7, 6),
}
TET10_EDGES = [(0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)]  # 5..10 번 절점이 놓일 모서리
HEX8_FACES = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]

NNODE_OF = {"tet4": 4, "tet10": 10, "hex8": 8}


def elem_family(abaqus_type: str) -> str | None:
    t = abaqus_type.upper()
    if t.startswith("C3D10"):
        return "tet10"
    if t.startswith("C3D4"):
        return "tet4"
    if t.startswith("C3D8"):
        return "hex8"
    return None


@dataclass
class Mesh:
    node_ids: np.ndarray            # (N,)
    xyz: np.ndarray                 # (N,3)
    elem_ids: np.ndarray            # (E,)
    conn: np.ndarray                # (E,k) 노드 인덱스(0-based, node_ids 기준 행 번호)
    family: str                     # tet4 / tet10 / hex8
    src_elsets: dict = field(default_factory=dict)
    log: list = field(default_factory=list)

    def note(self, msg):
        self.log.append(msg)


# ---------------------------------------------------------------------------
# INP 읽기
# ---------------------------------------------------------------------------
def _kw_params(line: str) -> tuple[str, dict]:
    parts = [p.strip() for p in line[1:].split(",")]
    kw = parts[0].upper()
    prm = {}
    for p in parts[1:]:
        if not p:
            continue
        if "=" in p:
            k, v = p.split("=", 1)
            prm[k.strip().upper()] = v.strip()
        else:
            prm[p.strip().upper()] = ""
    return kw, prm


def read_inp_mesh(path: str | Path, keep_elsets: list[str] | None = None) -> Mesh:
    """*NODE, *ELEMENT(3D solid) 만 읽는다. *INCLUDE 는 재귀로 따라간다.
    keep_elsets: 지정하면 해당 ELSET(HyperMesh 컴포넌트 이름) 요소만 사용."""
    path = Path(path)
    nodes: dict[int, tuple] = {}
    elems: list[tuple[int, str, list[int], str]] = []

    def parse_file(p: Path):
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        mode = None
        etype = None
        elset = ""
        nexp = 0
        pending: list[int] = []
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("**"):
                continue
            if line.startswith("*"):
                kw, prm = _kw_params(line)
                mode = None
                if kw == "NODE" and "OUTPUT" not in kw:
                    mode = "node"
                elif kw == "ELEMENT":
                    etype = prm.get("TYPE", "").upper()
                    fam = elem_family(etype)
                    if fam is None:
                        mode = "skip_elem"
                    else:
                        mode = "elem"
                        nexp = NNODE_OF[fam]
                        elset = prm.get("ELSET", "")
                        pending = []
                elif kw == "INCLUDE":
                    inc = prm.get("INPUT", "").strip('"')
                    ip = (p.parent / inc) if not Path(inc).is_absolute() else Path(inc)
                    if ip.exists():
                        parse_file(ip)
                continue
            if mode == "node":
                v = [x for x in line.split(",") if x.strip() != ""]
                nid = int(float(v[0]))
                c = [float(x) for x in v[1:4]] + [0.0] * (3 - len(v[1:4]))
                nodes[nid] = tuple(c)
            elif mode == "elem":
                vals = [int(float(x)) for x in line.split(",") if x.strip() != ""]
                pending.extend(vals)
                # 연속행 처리: (요소번호 + 절점수) 가 다 찰 때까지 누적
                if len(pending) >= nexp + 1:
                    eid, cn = pending[0], pending[1:nexp + 1]
                    elems.append((eid, etype, cn, elset))
                    pending = pending[nexp + 1:]

    parse_file(path)
    if not nodes or not elems:
        raise ValueError(f"{path}: *NODE / 3D *ELEMENT 를 찾지 못했습니다.")

    if keep_elsets:
        keep = {k.upper() for k in keep_elsets}
        elems = [e for e in elems if e[3].upper() in keep]
        if not elems:
            raise ValueError(f"지정한 ELSET {keep_elsets} 에 해당하는 요소가 없습니다.")

    fams = {elem_family(e[1]) for e in elems}
    if len(fams) > 1:
        # tet4 + tet10 이 섞인 경우 tet10 만 남김(HM orderchange 잔여물)
        if fams == {"tet4", "tet10"}:
            elems = [e for e in elems if elem_family(e[1]) == "tet10"]
            fams = {"tet10"}
        else:
            raise ValueError(f"요소 종류가 섞여 있습니다: {fams}")
    fam = fams.pop()

    used = sorted({n for e in elems for n in e[2]})
    missing = [n for n in used if n not in nodes]
    if missing:
        raise ValueError(f"요소가 참조하는 절점 {len(missing)}개가 *NODE 에 없습니다. 예: {missing[:5]}")
    node_ids = np.array(used, dtype=np.int64)
    idx = {n: i for i, n in enumerate(used)}
    xyz = np.array([nodes[n] for n in used], dtype=float)
    conn = np.array([[idx[n] for n in e[2]] for e in elems], dtype=np.int64)
    elem_ids = np.array([e[0] for e in elems], dtype=np.int64)
    src_elsets = {}
    for e in elems:
        src_elsets.setdefault(e[3], 0)
        src_elsets[e[3]] += 1
    m = Mesh(node_ids, xyz, elem_ids, conn, fam, src_elsets)
    n_orphan = len(nodes) - len(used)
    m.note(f"read {path.name}: nodes={len(used)} (unused {n_orphan} dropped), elems={len(elems)} ({fam}), elsets={src_elsets}")
    return m


# ---------------------------------------------------------------------------
# 요소 검사 / 보정
# ---------------------------------------------------------------------------
def _tet_vol(x, c):
    a, b, cc, d = x[c[:, 0]], x[c[:, 1]], x[c[:, 2]], x[c[:, 3]]
    return np.einsum("ij,ij->i", b - a, np.cross(cc - a, d - a)) / 6.0


def fix_tet10_midside_order(m: Mesh) -> None:
    """중간절점(5~10)이 Abaqus 규칙의 모서리 중점에 있는지 확인하고, 다른 규칙이면 재배열."""
    if m.family != "tet10":
        return
    x, c = m.xyz, m.conn
    corners = c[:, :4]
    mids = c[:, 4:]
    # 각 중간절점이 어느 코너쌍의 중점에 가장 가까운지 찾는다
    pairs = [(0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)]
    pm = np.stack([(x[corners[:, i]] + x[corners[:, j]]) / 2 for i, j in pairs], axis=1)  # (E,6,3)
    mx = x[mids]                                                                         # (E,6,3)
    d = np.linalg.norm(mx[:, :, None, :] - pm[:, None, :, :], axis=3)                   # (E,6,6)
    which = d.argmin(axis=2)                                                             # mid k -> pair index
    ok = np.all(which == np.arange(6), axis=1)
    if ok.all():
        m.note("tet10 midside order: Abaqus 규칙 일치")
        return
    nfix = 0
    for e in np.where(~ok)[0]:
        w = which[e]
        if len(set(w.tolist())) != 6:
            raise ValueError(f"요소 {m.elem_ids[e]}: 중간절점이 모서리 중점과 맞지 않습니다(곡면 투영이 매우 큼).")
        newmid = np.empty(6, dtype=np.int64)
        newmid[w] = mids[e]
        c[e, 4:] = newmid
        nfix += 1
    m.note(f"tet10 midside order: {nfix}개 요소 재배열")


def fix_orientation(m: Mesh) -> None:
    """음의 체적 요소 뒤집기 (tet 전용; hex 는 검사만)."""
    x, c = m.xyz, m.conn
    if m.family in ("tet4", "tet10"):
        v = _tet_vol(x, c)
        neg = v < 0
        if neg.any():
            # 코너 2<->3 교환, tet10 이면 중간절점도 대응 교환 (5<->7, 9<->10)
            c[neg, 1], c[neg, 2] = c[neg, 2].copy(), c[neg, 1].copy()
            if m.family == "tet10":
                c[neg, 4], c[neg, 6] = c[neg, 6].copy(), c[neg, 4].copy()
                c[neg, 8], c[neg, 9] = c[neg, 9].copy(), c[neg, 8].copy()
            m.note(f"음의 체적 tet {int(neg.sum())}개 절점 순서 반전")
        v = _tet_vol(x, c)
        if (v <= 0).any():
            raise ValueError(f"보정 후에도 체적<=0 인 요소 {int((v <= 0).sum())}개")
    else:
        a, g = x[c[:, 0]], x[c[:, 6]]
        if (np.einsum("ij,ij->i", x[c[:, 1]] - a, np.cross(x[c[:, 3]] - a, x[c[:, 4]] - a)) <= 0).any():
            m.note("경고: hex8 일부 요소의 방향이 의심됩니다(자동 보정 안 함).")


def upgrade_tet4_to_tet10(m: Mesh) -> None:
    """HM orderchange 가 실패해 tet4 로 나온 경우 직선 중간절점을 추가해 tet10 으로 변환."""
    if m.family != "tet4":
        return
    c = m.conn
    edge_key = {}
    new_xyz = []
    next_idx = len(m.xyz)
    next_id = int(m.node_ids.max()) + 1
    new_ids = []
    mids = np.zeros((len(c), 6), dtype=np.int64)
    for e in range(len(c)):
        for k, (i, j) in enumerate(TET10_EDGES):
            a, b = int(c[e, i]), int(c[e, j])
            key = (a, b) if a < b else (b, a)
            if key not in edge_key:
                edge_key[key] = next_idx
                new_xyz.append((m.xyz[a] + m.xyz[b]) / 2)
                new_ids.append(next_id)
                next_idx += 1
                next_id += 1
            mids[e, k] = edge_key[key]
    m.xyz = np.vstack([m.xyz, np.array(new_xyz)])
    m.node_ids = np.concatenate([m.node_ids, np.array(new_ids, dtype=np.int64)])
    m.conn = np.hstack([c, mids])
    m.family = "tet10"
    m.note(f"tet4 -> tet10 변환 (중간절점 {len(new_ids)}개 추가, 곡면 투영 없음)")


def check_mesh_sane(m: Mesh, min_elems: int = 50) -> None:
    """[v8] 해석에 쓸 수 없는 메시(요소 몇 개짜리 등)를 build 초기에 명확한 메시지로 거른다."""
    n = len(m.conn)
    if n < min_elems:
        x = m.xyz
        span = (x.max(0) - x.min(0)).round(3).tolist()
        raise ValueError(
            f"메시 요소가 {n}개뿐입니다(최소 {min_elems}개). 범위 {span} mm. "
            f"HyperMesh tetmesh 가 실패한 메시입니다 -> mesh/hm_log.txt 의 'tetmesh <...>' 줄을 확인하고 "
            f"--stage mesh --force 로 메시를 다시 만드세요.")


def keep_largest_component(m: Mesh, min_frac_warn: float = 0.001) -> dict:
    """[v8] 절점을 공유하는 요소끼리 묶어 가장 큰 덩어리만 남긴다.
    (글자/작은 돌기가 따로 떨어진 Tetra 덩어리로 생기면 INNER/OUTER 판별과 해석이 망가짐)"""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    c = m.conn[:, :4] if m.family != "hex8" else m.conn
    E, k = c.shape
    N = len(m.xyz)
    rows = np.repeat(np.arange(E), k)
    cols = c.ravel() + E
    g = coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(E + N, E + N))
    ncomp, lab = connected_components(g, directed=False)
    elab = lab[:E]
    uniq, cnt = np.unique(elab, return_counts=True)
    info = {"n_components": int(len(uniq)), "component_sizes": sorted(cnt.tolist(), reverse=True)[:10]}
    if len(uniq) <= 1:
        return info
    keep_lab = uniq[cnt.argmax()]
    keep = elab == keep_lab
    dropped = int((~keep).sum())
    m.conn = m.conn[keep]
    m.elem_ids = m.elem_ids[keep]
    used = np.unique(m.conn)
    remap = -np.ones(N, dtype=np.int64)
    remap[used] = np.arange(len(used))
    m.conn = remap[m.conn]
    m.xyz = m.xyz[used]
    m.node_ids = m.node_ids[used]
    info["dropped_elems"] = dropped
    m.note(f"떨어진 요소 덩어리 {len(uniq) - 1}개 ({dropped}개 요소) 제거, 가장 큰 덩어리 {int(cnt.max())}개만 사용")
    if dropped > min_frac_warn * E and dropped > 10:
        info["warning"] = f"본체와 떨어진 요소 {dropped}개를 제거했습니다 (원래 {E}개). 형상/메시 확인 권장"
    return info


def tet_quality(m: Mesh) -> dict:
    """품질 지표: 정규화 체적비(가장 나쁜 요소), tet10 중간절점 이탈률."""
    x, c = m.xyz, m.conn
    if m.family not in ("tet4", "tet10"):
        return {}
    v = _tet_vol(x, c)
    edges = [(0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)]
    L = np.stack([np.linalg.norm(x[c[:, i]] - x[c[:, j]], axis=1) for i, j in edges], axis=1)
    lrms = np.sqrt((L ** 2).mean(axis=1))
    qual = 6 * math.sqrt(2) * v / lrms ** 3          # 정사면체 = 1
    out = {
        "n_elem": int(len(c)),
        "vol_min": float(v.min()),
        "shape_q_min": float(qual.min()),
        "shape_q_p01": float(np.percentile(qual, 1)),
        "n_q_below_0p1": int((qual < 0.1).sum()),
        "edge_len_mean": float(L.mean()),
    }
    if m.family == "tet10":
        dev = []
        for k, (i, j) in enumerate(edges):
            mid = (x[c[:, i]] + x[c[:, j]]) / 2
            dev.append(np.linalg.norm(x[c[:, 4 + k]] - mid, axis=1) / L[:, k])
        out["midside_dev_max"] = float(np.max(dev))
    return out


# ---------------------------------------------------------------------------
# 축 정렬 (부싱 축 -> +Y)
# ---------------------------------------------------------------------------
ROT_TO_Y = {
    "Y": np.eye(3),
    "X": np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]),   # Z축 +90°: X -> Y
    "Z": np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]]),   # X축 -90°: Z -> Y
}


def detect_axis(m: Mesh) -> tuple[str, dict]:
    """bbox 중심을 지나는 X/Y/Z 직선 중 재료와 가장 멀리 떨어진 것(=보어 축)을 축으로 판단."""
    x = m.xyz
    ctr = (x.min(0) + x.max(0)) / 2
    score = {}
    for ax, k in (("X", 0), ("Y", 1), ("Z", 2)):
        d = np.delete(x - ctr, k, axis=1)
        score[ax] = float(np.sqrt((d ** 2).sum(1)).min())
    best = max(score, key=score.get)
    return best, score


def align_axis_to_y(m: Mesh, axis: str = "auto", center: str = "none") -> dict:
    info = {}
    if axis.lower() == "auto":
        axis, score = detect_axis(m)
        info["axis_score_min_dist_mm"] = score
        if score[axis] < 0.5:
            raise ValueError(f"축 자동 판별 실패(보어가 보이지 않음) score={score}. config의 axis 를 X/Y/Z 로 지정하세요.")
    axis = axis.upper()
    R = ROT_TO_Y[axis]
    m.xyz = m.xyz @ R.T          # 회전행렬 det=+1 -> 요소 방향 유지
    info["source_axis"] = axis
    info["rotation"] = R.tolist()
    if center.lower() == "bbox":
        ctr = (m.xyz.min(0) + m.xyz.max(0)) / 2
        m.xyz -= ctr
        info["translated_by"] = (-ctr).tolist()
    m.note(f"axis {axis} -> +Y 회전, center={center}")
    return info


# ---------------------------------------------------------------------------
# 경계면 / INNER / OUTER
# ---------------------------------------------------------------------------
def boundary_faces(m: Mesh):
    """반환: faces(list of node-index tuples, 코너+중간), normals(F,3, 바깥 방향), centroids, areas"""
    c = m.conn
    x = m.xyz
    if m.family in ("tet4", "tet10"):
        ftab = TET4_FACES
    else:
        ftab = HEX8_FACES
    count = {}
    for e in range(len(c)):
        for f in ftab:
            corners = tuple(int(c[e, i]) for i in f)
            key = tuple(sorted(corners))
            if key in count:
                count[key] = None
            else:
                count[key] = (e, f)
    faces, normals, cents, areas = [], [], [], []
    for key, val in count.items():
        if val is None:
            continue
        e, f = val
        corners = [int(c[e, i]) for i in f]
        allnodes = list(corners)
        if m.family == "tet10":
            allnodes += [int(c[e, i]) for i in TET10_FACE_MID[f]]
        p = x[corners]
        if len(corners) == 3:
            n = np.cross(p[1] - p[0], p[2] - p[0])
            a = 0.5 * np.linalg.norm(n)
        else:
            n = np.cross(p[2] - p[0], p[3] - p[1])
            a = 0.5 * np.linalg.norm(n)
        cen = p.mean(0)
        ec = x[c[e, :4] if m.family != "hex8" else c[e]].mean(0)
        if np.dot(n, cen - ec) < 0:
            n = -n
        nn = np.linalg.norm(n)
        faces.append(allnodes)
        normals.append(n / nn if nn > 0 else n)
        cents.append(cen)
        areas.append(a)
    return faces, np.array(normals), np.array(cents), np.array(areas)


@dataclass
class SetResult:
    inner: np.ndarray       # node index
    outer: np.ndarray
    info: dict
    outer_dir: np.ndarray | None = None   # [v12] OUTER 절점별 스웨징 방향 (XZ 단위벡터). None 이면 반경 방향


def classify_inner_outer(m: Mesh, cos_min: float = 0.6, inner_tol: float = 0.5, outer_tol: float = 0.5,
                         inner_r_max: float | None = None, outer_r_min: float | None = None,
                         inner_mode: str = "connected", connect_rmax_frac: float = 0.6) -> SetResult:
    """축 = Y 가정.
    INNER : 바깥 법선이 축 쪽(-e_r)을 향하는 경계면 중 반경이 가장 작은 띠(r <= r_min + inner_tol)
    OUTER : 바깥 법선이 +e_r 을 향하는 경계면 중 반경이 가장 큰 띠(r >= r_max - outer_tol)
    inner_r_max / outer_r_min 을 주면 띠 대신 그 반경 기준을 사용."""
    faces, nrm, cen, area = boundary_faces(m)
    r = np.hypot(cen[:, 0], cen[:, 2])
    er = np.zeros_like(cen)
    nz = r > 1e-9
    er[nz, 0] = cen[nz, 0] / r[nz]
    er[nz, 2] = cen[nz, 2] / r[nz]
    nr = np.einsum("ij,ij->i", nrm, er)

    inward = nr < -cos_min
    outward = nr > cos_min
    if not inward.any() or not outward.any():
        raise ValueError(
            f"반경 방향 경계면을 찾지 못했습니다(축 방향/중심 확인 필요). 경계면 {len(faces)}개 중 "
            f"축을 향하는 면 {int(inward.sum())}개, 바깥을 향하는 면 {int(outward.sum())}개, "
            f"요소 {len(m.conn)}개, 반경 범위 {r.min():.3f}~{r.max():.3f} mm. "
            f"요소 수가 매우 적으면 메시 실패, 반경 범위가 이상하면 config 의 geometry.axis(X/Y/Z)·center 확인")

    r_in = r[inward].min()
    r_out = r[outward].max()
    if inner_r_max is not None:
        sel_in = inward & (r <= inner_r_max)
    else:
        sel_in = inward & (r <= r_in + inner_tol)
        if inner_mode == "connected":
            # 원형이 아닌 보어(타원·각진 코어 등): 최소 반경 띠에서 시작해, 축을 향하는 경계면을
            # 이웃(공유 절점)으로 따라가며 전부 INNER 로 포함. 공극 벽면은 보어와 이어져 있지 않아 제외된다.
            r_lim = r_in + connect_rmax_frac * (r_out - r_in)
            ok = inward & (r <= r_lim)
            node2f = {}
            for i in np.where(ok)[0]:
                for nd in faces[i][:3]:
                    node2f.setdefault(nd, []).append(i)
            seen = set(np.where(sel_in)[0].tolist())
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
    if outer_r_min is not None:
        sel_out = outward & (r >= outer_r_min)
    else:
        sel_out = outward & (r >= r_out - outer_tol)

    inner = np.unique(np.concatenate([faces[i] for i in np.where(sel_in)[0]]))
    outer = np.unique(np.concatenate([faces[i] for i in np.where(sel_out)[0]]))
    both = np.intersect1d(inner, outer)
    if len(both):
        raise ValueError(f"INNER/OUTER 에 동시에 속한 절점 {len(both)}개 -> 허용오차를 줄이세요.")

    xin, xout = m.xyz[inner], m.xyz[outer]
    rin_n, rout_n = np.hypot(xin[:, 0], xin[:, 2]), np.hypot(xout[:, 0], xout[:, 2])
    # 보어 중심 추정(원 최소자승): 원점에서 벗어나 있으면 경고 (LOAD_RP 는 0,0,0)
    A = np.c_[2 * xin[:, 0], 2 * xin[:, 2], np.ones(len(xin))]
    b = xin[:, 0] ** 2 + xin[:, 2] ** 2
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    bore_center = (float(sol[0]), float(sol[1]))
    info = {
        "n_boundary_faces": int(len(faces)),
        "inner_mode": inner_mode if inner_r_max is None else "r_max",
        "inner_nodes": int(len(inner)),
        "outer_nodes": int(len(outer)),
        "inner_area_mm2": float(area[sel_in].sum()),
        "outer_area_mm2": float(area[sel_out].sum()),
        "inner_r_range": [float(rin_n.min()), float(rin_n.max())],
        "outer_r_range": [float(rout_n.min()), float(rout_n.max())],
        "inner_y_range": [float(xin[:, 1].min()), float(xin[:, 1].max())],
        "outer_y_range": [float(xout[:, 1].min()), float(xout[:, 1].max())],
        "bore_center_xz": bore_center,
        "bore_center_offset_mm": float(math.hypot(*bore_center)),
    }
    warn = []
    if info["bore_center_offset_mm"] > 0.1:
        warn.append(f"보어 중심이 원점에서 {info['bore_center_offset_mm']:.3f} mm 떨어져 있습니다. "
                    f"LOAD_RP(0,0,0)와 스웨징 반경방향이 어긋납니다 -> center='bbox' 사용 권장")
    info["warnings"] = warn
    return SetResult(inner, outer, info)


def geometry_features(m: Mesh, sets: SetResult) -> dict:
    """머신러닝 학습용 형상 특징값."""
    x = m.xyz
    v = np.abs(_tet_vol(x, m.conn)).sum() if m.family != "hex8" else float("nan")
    r = np.hypot(x[:, 0], x[:, 2])
    return {
        "volume_mm3": float(v),
        "length_y_mm": float(x[:, 1].max() - x[:, 1].min()),
        "r_min_mm": float(r.min()),
        "r_max_mm": float(r.max()),
        "inner_r_mean_mm": float(np.hypot(x[sets.inner, 0], x[sets.inner, 2]).mean()),
        "outer_r_mean_mm": float(np.hypot(x[sets.outer, 0], x[sets.outer, 2]).mean()),
        "inner_length_mm": sets.info["inner_y_range"][1] - sets.info["inner_y_range"][0],
        "outer_length_mm": sets.info["outer_y_range"][1] - sets.info["outer_y_range"][0],
        "inner_area_mm2": sets.info["inner_area_mm2"],
        "outer_area_mm2": sets.info["outer_area_mm2"],
        "bbox_x_mm": float(x[:, 0].max() - x[:, 0].min()),
        "bbox_z_mm": float(x[:, 2].max() - x[:, 2].min()),
    }
