Test_2026.stp 샌드박스 점검용 (HyperMesh 대신 gmsh 사용).
remove_letters_gmsh.py : x=17/19 끝면 양각 글자(0.2 mm)를 외곽 루프 돌출체로 잘라냄 -> cut1.brep (면 5207 -> 977, 부피 -0.011 %)
mesh_gmsh_tet4.py      : python mesh_gmsh_tet4.py 3.0 out.inp cut1.brep  (tet4, 파이프라인이 tet10 으로 변환)
연구실 PC 에서는 필요 없음. CATIA 에서 각인을 지우고 STEP 을 다시 내보내는 것이 정석.
