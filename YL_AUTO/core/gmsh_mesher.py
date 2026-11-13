# -*- coding: utf-8 -*-
"""
gmsh_mesher.py : HyperMesh 메시가 끝까지 실패했을 때 쓰는 예비 메셔 (pip install gmsh)

  python core/gmsh_mesher.py <형상.stp> <출력.inp> <요소크기mm> [최소크기비율]

- STEP/IGES/BREP 형상을 gmsh(OpenCASCADE) 로 읽어 Tet4(C3D4) 메시를 만들고 Abaqus INP 로 저장한다.
  (C3D10H 변환, 방향 보정, INNER/OUTER 선택은 파이프라인 build 단계가 그대로 수행)
- 0.2 mm 양각 글자 같은 작은 형상은 최소 요소크기(요소크기 x 비율)로 눌러서 거칠게 지나간다.
- 한 가지 설정으로 실패하면 설정을 바꿔 최대 3번 시도한다.
- 마지막 줄에 'GMSH_RESULT {json}' 을 출력한다 (runners.run_gmsh 가 읽음).
"""
from __future__ import annotations

import json
import sys
import time

GMSH_EXT = {".stp", ".step", ".igs", ".iges", ".brep"}


def mesh(src: str, out: str, size: float, min_ratio: float = 0.15, threads: int = 4) -> dict:
    import gmsh

    # (2D 알고리즘, 3D 알고리즘, 최소크기 배율)  6=Frontal-Delaunay, 5=Delaunay, 1=Delaunay3D, 10=HXT
    recipes = [(6, 1, 1.0), (5, 10, 1.0), (6, 1, 0.5)]
    last_err = ""
    for i, (a2, a3, mfac) in enumerate(recipes, 1):
        t0 = time.time()
        gmsh.initialize()
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.option.setNumber("General.NumThreads", threads)
            gmsh.model.add("m")
            gmsh.model.occ.importShapes(src)
            gmsh.model.occ.synchronize()
            vols = [t for _, t in gmsh.model.getEntities(3)]
            if not vols:
                raise RuntimeError("형상에 solid(부피)가 없습니다 (면만 있는 STEP)")
            gmsh.option.setNumber("Mesh.MeshSizeMax", size)
            gmsh.option.setNumber("Mesh.MeshSizeMin", size * min_ratio * mfac)
            gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
            gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 1)
            gmsh.option.setNumber("Mesh.ElementOrder", 1)
            gmsh.option.setNumber("Mesh.Algorithm", a2)
            gmsh.option.setNumber("Mesh.Algorithm3D", a3)
            gmsh.option.setNumber("Mesh.Optimize", 1)
            gmsh.option.setNumber("Mesh.OptimizeNetgen", 1 if a3 == 1 else 0)
            gmsh.model.addPhysicalGroup(3, vols, name="RUBBER")
            gmsh.model.mesh.generate(3)
            etypes, etags, _ = gmsh.model.mesh.getElements(3)
            n_tet = int(sum(len(t) for t in etags))
            n_node = len(gmsh.model.mesh.getNodes()[0])
            if n_tet == 0:
                raise RuntimeError("Tetra 0개")
            gmsh.option.setNumber("Mesh.SaveGroupsOfElements", 1)
            gmsh.write(out)
            return {"ok": True, "n_tet": n_tet, "n_node": n_node, "recipe": i, "n_vol": len(vols),
                    "elapsed_s": round(time.time() - t0, 1), "error": ""}
        except Exception as e:  # 다음 설정으로 재시도
            last_err = f"recipe {i} (2D={a2}, 3D={a3}): {e}"
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
