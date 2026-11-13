# -*- coding: utf-8 -*-
"""검증용 가상 부싱 메시 (HyperMesh 출력 INP 대용) + 가상 물성시험 데이터 생성 - 실제 모델/물성 아님"""
import math, sys
from pathlib import Path
import numpy as np
import gmsh

OUT = Path(__file__).resolve().parent / "models"
OUT.mkdir(exist_ok=True)

def finish(name, size):
    occ = gmsh.model.occ
    occ.synchronize()
    vols = [t for d, t in gmsh.model.getEntities(3)]
    gmsh.model.addPhysicalGroup(3, vols, name="RUBBER")
    gmsh.option.setNumber("Mesh.MeshSizeMax", size)
    gmsh.option.setNumber("Mesh.MeshSizeMin", size * 0.5)
    gmsh.option.setNumber("Mesh.ElementOrder", 2)
    gmsh.option.setNumber("Mesh.SecondOrderLinear", 0)
    gmsh.option.setNumber("Mesh.SaveAll", 0)
    gmsh.option.setNumber("Mesh.Optimize", 1)
    gmsh.option.setNumber("Mesh.HighOrderOptimize", 1)
    gmsh.model.mesh.generate(3)
    gmsh.write(str(OUT / f"{name}.inp"))

def plain_bushing(size):
    # 축 = Z (파이프라인이 +Y 로 돌려야 함), Ri=10, Ro=25, L=40
    gmsh.model.add("plain")
    occ = gmsh.model.occ
    o = occ.addCylinder(0, 0, -20, 0, 0, 40, 25)
    i = occ.addCylinder(0, 0, -20, 0, 0, 40, 10)
    occ.cut([(3, o)], [(3, i)])
    finish("BUSH_PLAIN_axisZ", size)

def void_bushing(size):
    # 축 = X, Ri=10, Ro=28, L=40, +-Z 방향으로 콩팥형 공동 2개 (반경 방향 비대칭)
    gmsh.model.add("void")
    occ = gmsh.model.occ
    o = occ.addCylinder(-20, 0, 0, 40, 0, 0, 28)
    i = occ.addCylinder(-20, 0, 0, 40, 0, 0, 10)
    body = occ.cut([(3, o)], [(3, i)])[0]
    tools = []
    for ang0 in (90, 270):
        # 반경 15~22, 각도 +-35 deg 섹터를 X축으로 돌출
        r1, r2, half = 15.0, 22.0, math.radians(35)
        a0 = math.radians(ang0)
        pts = []
        n = 12
        for k in range(n + 1):
            t = a0 - half + 2 * half * k / n
            pts.append((r2 * math.cos(t), r2 * math.sin(t)))
        for k in range(n + 1):
            t = a0 + half - 2 * half * k / n
            pts.append((r1 * math.cos(t), r1 * math.sin(t)))
        # 평면 = YZ (x=-20), 각도 0 = +Y, 90 = +Z
        ptags = [occ.addPoint(-20, y, z) for (y, z) in pts]
        lines = [occ.addLine(ptags[k], ptags[(k + 1) % len(ptags)]) for k in range(len(ptags))]
        cl = occ.addCurveLoop(lines)
        s = occ.addPlaneSurface([cl])
        ext = occ.extrude([(2, s)], 40, 0, 0)
        tools += [e for e in ext if e[0] == 3]
    occ.cut(body, tools)
    finish("BUSH_VOID_axisX", size)

def test_data():
    # 가상 고무 (Ogden 3항, Abaqus 정의) 로 만든 시험 곡선 + 1% 잡음
    mu = [0.45, 0.002, -0.03]; al = [1.6, 5.5, -2.2]
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from core.ogden import nominal_stress
    rng = np.random.default_rng(1)
    for mode, emax in (("uniaxial", 2.0), ("biaxial", 1.0), ("planar", 1.5)):
        e = np.linspace(0.02, emax, 30)
        s = nominal_stress(mode, 1 + e, mu, al) * (1 + 0.01 * rng.standard_normal(len(e)))
        with open(OUT / f"TEST_RUBBER_{mode}.csv", "w") as f:
            f.write("nominal_strain,nominal_stress_MPa\n")
            for a, b in zip(e, s):
                f.write(f"{a:.5f},{b:.6f}\n")
    print("true mu0 =", sum(mu))

gmsh.initialize(); gmsh.option.setNumber("General.Terminal", 0)
size = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
plain_bushing(size); void_bushing(size)
gmsh.finalize()
test_data()
