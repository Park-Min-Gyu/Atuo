# -*- coding: utf-8 -*-
"""
robust_mesher.py : CAD 가 깨져 있어도 메시를 만드는 최후의 메셔 (TetWild 방식)

  python core/robust_mesher.py <형상.stp> <출력.inp> <요소크기mm> [envelope_mm] [stop_energy]

  필요 : pip install wildmeshing cadquery-ocp

왜 필요한가
  HyperMesh(YL_V10)·gmsh 는 CAD 면을 하나하나 메시해서 이어 붙인다. 그래서 CAD 에
    - 넓이가 거의 0 인 찌꺼기 면, 길이 0 모서리 (Part2)
    - 겹치거나 3 장이 만나는 면, 메셔를 죽이는 형상 (Part5)
    - 표면끼리 관통 (Part6, 'PLC Error: A segment and a facet intersect')
  같은 결함이 하나만 있어도 실패하고, 결함마다 따로 고쳐야 했다.
  TetWild 는 처음부터 '구멍·겹침·자기관통이 있는 표면'을 입력으로 가정하고 만든 방법이다.
    1) CAD 를 OpenCASCADE 로 촘촘한 삼각형으로 바꾸고 (면 하나가 깨져도 나머지는 그대로 나옴)
    2) 그 삼각형을 정확히 품는 Tetra 메시를 공간 전체에 만든 뒤 품질을 최적화하고
    3) 표면 안쪽(winding number)만 남긴다.
  결과 표면은 원래 CAD 표면에서 envelope_mm 이내로 유지된다.
마지막 줄에 'ROBUST_RESULT {json}' 을 출력한다 (runners.run_robust 가 읽음).
"""
from __future__ import annotations

import json
import sys
import time

ROBUST_EXT = {".stp", ".step", ".igs", ".iges", ".brep"}


def tessellate(src: str, deflection: float = 0.02):
    """CAD -> (V, F) 삼각형. 면마다 따로 삼각형화하므로 일부 면이 깨져도 나머지는 나온다."""
    import numpy as np
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS
    face_of = getattr(TopoDS, "Face_s", None) or TopoDS.Face
    tri_of = getattr(BRep_Tool, "Triangulation_s", None) or BRep_Tool.Triangulation
    ext = src.lower().rsplit(".", 1)[-1]
    if ext in ("igs", "iges"):
        from OCP.IGESControl import IGESControl_Reader as Reader
    elif ext == "brep":
        Reader = None
    else:
        from OCP.STEPControl import STEPControl_Reader as Reader
    if Reader is None:
        from OCP.BRepTools import BRepTools
        from OCP.BRep import BRep_Builder
        from OCP.TopoDS import TopoDS_Shape
        shape = TopoDS_Shape()
        read = getattr(BRepTools, "Read_s", None) or BRepTools.Read
        read(shape, src, BRep_Builder())
    else:
        r = Reader()
        if int(r.ReadFile(src)) != 1:
            raise RuntimeError(f"CAD 파일을 읽지 못함: {src}")
        r.TransferRoots()
        shape = r.OneShape()
    BRepMesh_IncrementalMesh(shape, deflection, False, 0.3, True)
    Vs, Fs, off, n_skip = [], [], 0, 0
    e = TopExp_Explorer(shape, TopAbs_FACE)
    while e.More():
        f = face_of(e.Current())
        loc = TopLoc_Location()
        tri = tri_of(f, loc)
        if tri is None:
            n_skip += 1
            e.Next()
            continue
        trsf = loc.Transformation()
        V = np.array([[p.X(), p.Y(), p.Z()] for p in
                      (tri.Node(i).Transformed(trsf) for i in range(1, tri.NbNodes() + 1))])
        T = np.array([tri.Triangle(i).Get() for i in range(1, tri.NbTriangles() + 1)], dtype=np.int64) - 1
        if f.Orientation() == TopAbs_REVERSED:
            T = T[:, [0, 2, 1]]
        Vs.append(V)
        Fs.append(T + off)
        off += len(V)
        e.Next()
    if not Fs:
        raise RuntimeError("CAD 에서 삼각형을 하나도 만들지 못함")
    return np.vstack(Vs), np.vstack(Fs), n_skip


def tet_quality(X, T):
    import numpy as np
    p0, p1, p2, p3 = (X[T[:, i]] for i in range(4))
    vol = np.einsum("ij,ij->i", np.cross(p1 - p0, p2 - p0), p3 - p0) / 6.0
    pairs = ((p1, p0), (p2, p1), (p0, p2), (p3, p0), (p3, p1), (p3, p2))
    l2 = sum(((a - b) ** 2).sum(1) for a, b in pairs) / 6.0
    q = 6 * np.sqrt(2) * np.abs(vol) / np.maximum(l2, 1e-300) ** 1.5
    return vol, q


def mesh(src: str, out: str, size: float, eps_mm: float = 0.05, stop_energy: float = 20.0,
         threads: int = 0) -> dict:
    import numpy as np
    import wildmeshing as wm
    t0 = time.time()
    V, F, n_skip = tessellate(src)
    diag = float(np.linalg.norm(V.max(0) - V.min(0)))
    print(f"robust: CAD 삼각형 {len(F)}개 (삼각형화 못 한 면 {n_skip}개), 대각선 {diag:.1f} mm, {time.time() - t0:.1f}s",
          flush=True)
    opts = {"application": "tetwild", "input": ["cad"], "filter": "input", "write_vtu": False,
            "num_threads": int(threads), "length_rel": size / diag, "eps_rel": eps_mm / diag,
            "stop_energy": float(stop_energy), "remove_duplicate_eps": 1e-7, "max_iterations": 80}
    res, rep = wm.tetwild(opts, {"cad": {"V": V, "vertex_attributes": {},
                                         "cells": {"triangle": {"connectivity": F, "attributes": {}}}}})
    X = res["mesh"]["V"]
    T = np.array(res["mesh"]["cells"]["tetrahedron"]["connectivity"], dtype=np.int64)
    if len(T) == 0:
        raise RuntimeError("TetWild 결과 Tetra 0개")
    vol, q = tet_quality(X, T)
    neg = vol < 0
    T[neg] = T[neg][:, [0, 2, 1, 3]]
    with open(out, "w") as fh:
        fh.write("** robust_mesher (OpenCASCADE tessellation + TetWild)\n*NODE\n")
        for i, (x, y, z) in enumerate(X, 1):
            fh.write(f"{i}, {x:.10g}, {y:.10g}, {z:.10g}\n")
        fh.write("*ELEMENT, TYPE=C3D4, ELSET=RUBBER\n")
        for k, (a, b, c, d) in enumerate(T, 1):
            fh.write(f"{k}, {a + 1}, {b + 1}, {c + 1}, {d + 1}\n")
    return {"ok": True, "n_tet": int(len(T)), "n_node": int(len(X)), "volume_mm3": round(float(np.abs(vol).sum()), 2),
            "q_min": round(float(q.min()), 4), "n_q_lt_0p02": int((q < 0.02).sum()),
            "max_energy": round(float(rep.get("max_energy", -1)), 2), "eps_mm": eps_mm,
            "elapsed_s": round(time.time() - t0, 1), "error": ""}


if __name__ == "__main__":
    try:
        res = mesh(sys.argv[1], sys.argv[2], float(sys.argv[3]),
                   float(sys.argv[4]) if len(sys.argv) > 4 else 0.05,
                   float(sys.argv[5]) if len(sys.argv) > 5 else 20.0)
    except ImportError as e:
        res = {"ok": False, "n_tet": 0, "error": f"필요한 패키지 없음 ({e}) -> pip install wildmeshing cadquery-ocp"}
    except Exception as e:
        res = {"ok": False, "n_tet": 0, "error": f"{type(e).__name__}: {e}"}
    print("ROBUST_RESULT " + json.dumps(res, ensure_ascii=False), flush=True)
    sys.exit(0 if res.get("ok") else 1)
