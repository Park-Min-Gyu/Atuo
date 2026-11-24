미리 만든 메시 (요소크기 2 mm, Tetra C3D4, 파이프라인 build 단계가 C3D10H 로 바꿈)
  Part2.inp : gmsh '작은 면 직접 채우기' (Tetra 62,604개)
  Part5.inp : TetWild (Tetra 171,772개, CAD 부피 대비 -0.03 %, 최소 품질 0.058)
  Part6.inp : TetWild (Tetra 169,199개, CAD 부피 대비 -0.04 %, 최소 품질 0.059)

v21 부터는 run_all.bat 이 위 방법으로 자동 메시하므로 보통은 필요 없다.
다만 TetWild 는 모델당 20 분 안팎 걸리고 pip install wildmeshing cadquery-ocp 가 필요하다.
시간을 아끼거나 자동 메시가 PC 에서 실패할 때만:
  1) models 폴더의 같은 이름 .stp 를 다른 곳으로 옮기고 (같은 이름이 두 개 있으면 오류)
  2) 이 .inp 를 models 폴더에 넣고
  3) run_all.bat --only Part5   (모델 이름)
