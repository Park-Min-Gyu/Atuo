# -*- coding: utf-8 -*-
"""
gmsh_mesher.py : HyperMesh 메시가 끝까지 실패했을 때 쓰는 예비 메셔 (pip install gmsh)

  python core/gmsh_mesher.py <형상.stp> <출력.inp> <요소크기mm> [최소크기비율]

- STEP/IGES/BREP 형상을 gmsh(OpenCASCADE) 로 읽어 Tet4(C3D4) 메시를 만들고 Abaqus INP 로 저장한다.
  (C3D10H 변환, 방향 보정, INNER/OUTER 선택은 파이프라인 build 단계가 그대로 수행)
- 0.2 mm 양각 글자 같은 작은 형상은 최소 요소크기(요소크기 x 비율)로 눌러서 거칠게 지나간다.
- 한 가지 설정으로 실패하면 설정을 바꿔 여러 번 시도한다 (넓이 ~0 인 면 직접 채우기, 형상 수리 등).
- 마지막 줄에 'GMSH_RESULT {json}' 을 출력한다 (runners.run_gmsh 가 읽음).
"""
from __future__ import annotations

import json
import sys
import time

GMSH_EXT = {".stp", ".step", ".igs", ".iges", ".brep"}

TINY_FACE_MM2 = 1e-3      # 이보다 작은 면은 gmsh 가 메시를 못 하는 경우가 많아 직접 채운다
MAX_HOLE_PERIM = 8.0      # 이보다 둘레가 긴 구멍은 메우지 않는다 (진짜 형상 문제는 숨기지 않음)


def _surface_tris(gmsh, tag):
    import numpy as np
    et, _, en = gmsh.model.mesh.getElements(2, tag)
    out = [n.reshape(-1, 3).astype(int) for ty, n in zip(et, en) if ty == 2]
    return np.concatenate(out) if out else np.zeros((0, 3), int)


def _fill_face(gmsh, f):
    """면 f 의 경계 1D 메시 절점 고리를 부채꼴 삼각형으로 채운다. 반환: 삼각형 수"""
    import numpy as np
    _, curves = gmsh.model.getAdjacencies(2, f)
    adj = {}
    for c in curves:
        _, _, en = gmsh.model.mesh.getElements(1, abs(int(c)))
        if not len(en):
            continue
        for a, b in en[0].reshape(-1, 2).astype(int):
            if a != b:
                adj.setdefault(a, []).append(b)
                adj.setdefault(b, []).append(a)
    if not adj:
        return 0
    start = next(iter(adj))
    loop, prev, cur = [start], None, start
    while len(loop) < 500:
        nb = [n for n in adj[cur] if n != prev]
        if not nb or nb[0] == start:
            break
        prev, cur = cur, nb[0]
        loop.append(cur)
    if len(loop) < 3:
        return 0
    tris = [[loop[0], loop[i], loop[i + 1]] for i in range(1, len(loop) - 1)]
    gmsh.model.mesh.addElementsByType(f, 2, [], np.array(tris).ravel().tolist())
    return len(tris)


def _generate_patched(gmsh, log):
    """[v19] CAD 에 넓이 ~0 인 면/길이 0 모서리가 있어서 gmsh 가 'Impossible to mesh periodic surface' 로
    멈추는 경우용 (Part2: 0.00001 mm² 면 4개, 길이 0 모서리 여러 개).
      1) 그런 아주 작은 면은 경계 절점으로 삼각형을 직접 만들어 넣고
      2) 나머지 면만 gmsh 가 메시 (Mesh.MeshOnlyEmpty)
      3) 비어 남은 면도 직접 채우고, 같은 위치 절점 병합, 손으로 채운 면은 이웃과 방향을 맞춤
      4) 그래도 남은 작은 구멍(둘레 MAX_HOLE_PERIM 이하)만 메운 뒤 3D
    원래 형상은 그대로이고, 손으로 채운 부분은 0.1 mm 수준의 면뿐이다."""
    import re
    from collections import Counter
    import numpy as np
    gmsh.model.mesh.generate(1)
    filled = [t for _, t in gmsh.model.getEntities(2) if gmsh.model.occ.getMass(2, t) < TINY_FACE_MM2]
    for f in filled:
        _fill_face(gmsh, f)
    gmsh.option.setNumber("Mesh.MeshOnlyEmpty", 1)
    for _ in range(20):
        try:
            gmsh.model.mesh.generate(2)
            break
        except Exception as e:
            m = re.search(r"surface (\d+)", str(e))
            if not m or int(m.group(1)) in filled:
                raise
            f = int(m.group(1))
            _fill_face(gmsh, f)
            filled.append(f)
    empty = [t for _, t in gmsh.model.getEntities(2) if len(_surface_tris(gmsh, t)) == 0]
    for f in empty:
        _fill_face(gmsh, f)
    filled += empty
    gmsh.model.mesh.removeDuplicateNodes()
    # 손으로 채운 면 방향 맞추기
    other = {}
    for _, t in gmsh.model.getEntities(2):
        if t in filled:
            continue
        for a, b, c in _surface_tris(gmsh, t):
            for e in ((a, b), (b, c), (c, a)):
                other[e] = t
    for f in filled:
        T = _surface_tris(gmsh, f)
        if not len(T):
            continue
        es = [e for a, b, c in T for e in ((a, b), (b, c), (c, a))]
        if sum(e in other for e in es) > sum((e[1], e[0]) in other for e in es):
            try:
                gmsh.model.mesh.removeElements(2, f)
            except AttributeError:          # 오래된 gmsh
                gmsh.model.mesh.clear([(2, f)])
            gmsh.model.mesh.addElementsByType(f, 2, [], T[:, [0, 2, 1]].ravel().tolist())
    # 남은 작은 구멍 메우기
    owner = {}
    for _, t in gmsh.model.getEntities(2):
        for a, b, c in _surface_tris(gmsh, t):
            for e in ((a, b), (b, c), (c, a)):
                owner[e] = t
    cnt = Counter(tuple(sorted(e)) for e in owner)
    nxt = {y: x for (x, y) in owner if cnt[tuple(sorted((x, y)))] == 1}
    seen, n_hole, big = set(), 0, []
    for st in list(nxt):
        if st in seen:
            continue
        loop, cur = [st], nxt[st]
        seen.add(st)
        while cur != st and cur in nxt and cur not in seen and len(loop) < 200:
            loop.append(cur); seen.add(cur); cur = nxt[cur]
        if cur != st or len(loop) < 3:
            continue
        P = [gmsh.model.mesh.getNode(n)[0] for n in loop]
        per = sum(np.linalg.norm(P[i] - P[(i + 1) % len(P)]) for i in range(len(P)))
        if per > MAX_HOLE_PERIM:
            big.append(round(float(per), 2))
            continue
        tris = [[loop[0], loop[i], loop[i + 1]] for i in range(1, len(loop) - 1)]
        gmsh.model.mesh.addElementsByType(owner[(loop[1], loop[0])], 2, [], np.array(tris).ravel().tolist())
        n_hole += 1
    log(f"아주 작은 면 {len(filled)}개 직접 채움, 작은 구멍 {n_hole}개 메움"
        + (f", 큰 구멍 {len(big)}개(둘레 {big} mm)는 형상 문제라 그대로 둠" if big else ""))
    gmsh.model.mesh.generate(3)
    gmsh.option.setNumber("Mesh.MeshOnlyEmpty", 0)
    return f"작은 면 {len(filled)}개 직접 채움, 구멍 {n_hole}개 메움"


def mesh(src: str, out: str, size: float, min_ratio: float = 0.15, threads: int = 4) -> dict:
    import gmsh

    # (2D 알고리즘, 3D 알고리즘, 최소크기 배율, 형상 자동수리)
    #   2D: 6=Frontal-Delaunay, 5=Delaunay, 1=MeshAdapt (복잡한 곡면에 가장 튼튼함)
    #   3D: 1=Delaunay, 10=HXT
    #   [v17] 4, 5번 : 불러올 때 OCC 형상 수리(퇴화 모서리/작은 모서리·면 정리, 면 꿰매기, solid 만들기) +
    #                  MeshAdapt. 'Impossible to mesh periodic surface' (Part2) 같은 면 하나 실패용.
    #   [v18] Part2 : 'full' 수리는 'Could not fix wire in surface 114' 로 불러오기 단계에서 실패했다.
    #         그래서 MeshAdapt 를 수리 없이(3번), 면 꿰매기만 하는 가벼운 수리로(5번) 따로 시도한다.
    #   [v19] 2번 "patch" : 넓이 ~0 인 면을 직접 채우며 메시 (Part2 에서 성공)
    recipes = [(6, 1, 1.0, None), (6, 1, 1.0, "patch"), (5, 10, 1.0, None), (1, 1, 1.0, None),
               (6, 1, 0.5, None), (1, 1, 1.0, "sew"), (1, 10, 0.5, "full")]
    last_err = ""
    for i, (a2, a3, mfac, fix) in enumerate(recipes, 1):
        t0 = time.time()
        gmsh.initialize()
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.option.setNumber("General.NumThreads", threads)
            gmsh.model.add("m")
            if fix in ("sew", "full"):
                opts = ("OCCSewFaces", "OCCMakeSolids") if fix == "sew" else \
                       ("OCCFixDegenerated", "OCCFixSmallEdges", "OCCFixSmallFaces", "OCCSewFaces", "OCCMakeSolids")
                for opt in opts:
                    gmsh.option.setNumber(f"Geometry.{opt}", 1)
            gmsh.model.occ.importShapes(src)
            gmsh.model.occ.synchronize()
            vols = [t for _, t in gmsh.model.getEntities(3)]
            healed = ""
            if not vols:
                # [v13] 면만 있는 STEP (HyperMesh 로그의 'solids after import : 0') :
                #       면을 꿰매서(sew) solid 로 만든다. 허용오차를 키워 가며 시도.
                for tol in (1e-3, 1e-2, 5e-2, 2e-1):
                    gmsh.model.occ.healShapes([], tol, True, True, True, True, True)
                    gmsh.model.occ.synchronize()
                    vols = [t for _, t in gmsh.model.getEntities(3)]
                    if vols:
                        healed = f"면 꿰매기(sew) tol={tol} mm 로 solid {len(vols)}개 생성"
                        print("gmsh:", healed, flush=True)
                        break
            if not vols:
                raise RuntimeError("형상에 solid(부피)가 없고 면을 꿰매도(최대 0.2 mm) 닫히지 않습니다 "
                                   "-> CAD 에서 solid 로 다시 내보내야 합니다")
            gmsh.option.setNumber("Mesh.MeshSizeMax", size)
            # min_ratio >= 1 : 2 mm 완전 고정 (최소 = 최대 = 요소크기)
            gmsh.option.setNumber("Mesh.MeshSizeMin", size if min_ratio >= 1.0 else size * min_ratio * mfac)
            gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
            gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 1)
            gmsh.option.setNumber("Mesh.ElementOrder", 1)
            gmsh.option.setNumber("Mesh.Algorithm", a2)
            gmsh.option.setNumber("Mesh.Algorithm3D", a3)
            gmsh.option.setNumber("Mesh.Optimize", 1)
            gmsh.option.setNumber("Mesh.OptimizeNetgen", 1 if a3 == 1 else 0)
            gmsh.model.addPhysicalGroup(3, vols, name="RUBBER")
            if fix == "patch":
                note = _generate_patched(gmsh, lambda m: print("gmsh:", m, flush=True))
                healed = (healed + " / " if healed else "") + note
            else:
                gmsh.model.mesh.generate(3)
            etypes, etags, _ = gmsh.model.mesh.getElements(3)
            n_tet = int(sum(len(t) for t in etags))
            n_node = len(gmsh.model.mesh.getNodes()[0])
            if n_tet == 0:
                raise RuntimeError("Tetra 0개")
            gmsh.option.setNumber("Mesh.SaveGroupsOfElements", 1)
            gmsh.write(out)
            return {"ok": True, "n_tet": n_tet, "n_node": n_node, "recipe": i, "n_vol": len(vols),
                    "elapsed_s": round(time.time() - t0, 1), "error": "", "healed": healed}
        except Exception as e:  # 다음 설정으로 재시도
            last_err = f"recipe {i} (2D={a2}, 3D={a3}{f', 형상수리={fix}' if fix else ''}): {e}"
            print("gmsh 시도 실패 ->", last_err, flush=True)
        finally:
            gmsh.finalize()
    return {"ok": False, "n_tet": 0, "error": last_err}


if __name__ == "__main__":
    try:
        res = mesh(sys.argv[1], sys.argv[2], float(sys.argv[3]),
                   float(sys.argv[4]) if len(sys.argv) > 4 else 0.15)
    except ImportError:
        res = {"ok": False, "n_tet": 0, "error": "gmsh 미설치 (pip install gmsh)"}
    except Exception as e:
        res = {"ok": False, "n_tet": 0, "error": f"{type(e).__name__}: {e}"}
    print("GMSH_RESULT " + json.dumps(res, ensure_ascii=False), flush=True)
    sys.exit(0 if res.get("ok") else 1)
