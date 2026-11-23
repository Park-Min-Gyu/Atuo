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

    # (2D 알고리즘, 3D 알고리즘, 최소크기 배율, 형상 자동수리)
    #   2D: 6=Frontal-Delaunay, 5=Delaunay, 1=MeshAdapt (복잡한 곡면에 가장 튼튼함)
    #   3D: 1=Delaunay, 10=HXT
    #   [v17] 4, 5번 : 불러올 때 OCC 형상 수리(퇴화 모서리/작은 모서리·면 정리, 면 꿰매기, solid 만들기) +
    #                  MeshAdapt. 'Impossible to mesh periodic surface' (Part2) 같은 면 하나 실패용.
    #   [v18] Part2 : 'full' 수리는 'Could not fix wire in surface 114' 로 불러오기 단계에서 실패했다.
    #         그래서 MeshAdapt 를 수리 없이(3번), 면 꿰매기만 하는 가벼운 수리로(5번) 따로 시도한다.
    recipes = [(6, 1, 1.0, None), (5, 10, 1.0, None), (1, 1, 1.0, None), (6, 1, 0.5, None),
               (1, 1, 1.0, "sew"), (1, 10, 0.5, "full")]
    last_err = ""
    for i, (a2, a3, mfac, fix) in enumerate(recipes, 1):
        t0 = time.time()
        gmsh.initialize()
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.option.setNumber("General.NumThreads", threads)
            gmsh.model.add("m")
            if fix:
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
